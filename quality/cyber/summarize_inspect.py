"""Summarize Inspect cyber-evaluation logs without copying prompt contents.

The report separates benchmark capability failures from harness/setup failures
and calculates token cost from uncached input, cache reads, cache writes, and
output. It is intentionally benchmark-agnostic so the same report can combine
Cybench, ExploitBench, SEC-bench, and later Inspect-compatible suites.
"""

import argparse
import glob
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


# USD per 1M tokens in Amazon Bedrock us-east-2. Anthropic cache-write values
# use the default five-minute TTL.
MODEL_PRICES = {
    "gpt-daybreak-blue-5.6-sol": {
        "input": 5.50,
        "cache_read": 0.55,
        "cache_write": 6.875,
        "output": 33.00,
    },
    "gpt-5.6-cyber": {
        "input": 13.75,
        "cache_read": 1.375,
        "cache_write": 17.1875,
        "output": 82.50,
    },
    "gpt-5.6-sol": {
        "input": 4.00,
        "cache_read": 0.40,
        "cache_write": 5.00,
        "output": 20.00,
    },
    "claude-fable-5": {
        "input": 11.00,
        "cache_read": 1.10,
        "cache_write": 13.75,
        "output": 55.00,
    },
    "claude-opus-4-8": {
        "input": 5.50,
        "cache_read": 0.55,
        "cache_write": 6.875,
        "output": 27.50,
    },
}

REFUSAL_MARKERS = (
    "i can't assist",
    "i cannot assist",
    "i'm unable to assist",
    "i am unable to assist",
    "cannot comply",
    "can't comply",
    "not able to provide",
)

CORRECT_VALUES = {"c", "correct", "true", "1", "pass", "passed"}


def price_for_model(model):
    """Return the most specific suffix-matched price entry."""
    matches = [
        (key, value)
        for key, value in MODEL_PRICES.items()
        if str(model).endswith(key)
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))[1]


def estimate_usage_cost(model, usage):
    price = price_for_model(model)
    if price is None:
        return None
    return round(
        (
            (usage.get("input_tokens", 0) or 0) * price["input"]
            + (usage.get("input_tokens_cache_read", 0) or 0)
            * price["cache_read"]
            + (usage.get("input_tokens_cache_write", 0) or 0)
            * price["cache_write"]
            + (usage.get("output_tokens", 0) or 0) * price["output"]
        )
        / 1_000_000,
        8,
    )


def score_is_correct(scores):
    for score in (scores or {}).values():
        value = score.get("value") if isinstance(score, dict) else score
        if str(value).strip().casefold() in CORRECT_VALUES:
            return True
    return False


def event_counts(sample):
    return Counter(
        event.get("event")
        for event in (sample.get("events") or [])
        if isinstance(event, dict)
    )


def refusal_text(sample):
    parts = []
    error = sample.get("error")
    if isinstance(error, dict):
        parts.append(str(error.get("message", "")))
    output = sample.get("output")
    if isinstance(output, dict):
        parts.append(str(output.get("completion", "")))
        message = output.get("message")
        if isinstance(message, dict):
            parts.append(str(message.get("content", "")))
    return " ".join(parts).casefold()


def classify_sample(sample):
    counts = event_counts(sample)
    error = sample.get("error")
    turns = sample.get("turn_count") or 0
    if error and turns == 0 and counts["tool"] == 0:
        return "setup_error"
    if score_is_correct(sample.get("scores")):
        return "success"
    if any(marker in refusal_text(sample) for marker in REFUSAL_MARKERS):
        return "refusal"
    if counts["sample_limit"]:
        return "sample_limit"
    if error:
        return "model_or_tool_error"
    if not sample.get("scores"):
        return "unscored"
    return "incorrect"


def usage_for_sample(sample):
    total = Counter()
    costs = []
    for model, usage in (sample.get("model_usage") or {}).items():
        if not isinstance(usage, dict):
            continue
        for field in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "input_tokens_cache_write",
            "input_tokens_cache_read",
            "reasoning_tokens",
        ):
            total[field] += usage.get(field, 0) or 0
        explicit_cost = usage.get("total_cost")
        cost = (
            explicit_cost
            if explicit_cost is not None
            else estimate_usage_cost(model, usage)
        )
        if cost is None:
            costs = None
        elif costs is not None:
            costs.append(cost)
    return dict(total), None if costs is None else round(sum(costs), 8)


def log_to_dict(log):
    if isinstance(log, dict):
        return log
    return log.model_dump(mode="json")


def arm_key(payload):
    spec = payload.get("eval") or {}
    task_args = spec.get("task_args") or {}
    variant = task_args.get("variant_names") or task_args.get("variant") or ""
    return (
        str(spec.get("model", "unknown")),
        str(spec.get("task", "unknown")),
        str(variant),
    )


def summarize_payloads(payloads, sources=None):
    grouped = defaultdict(list)
    for index, raw_payload in enumerate(payloads):
        payload = log_to_dict(raw_payload)
        source = sources[index] if sources and index < len(sources) else None
        grouped[arm_key(payload)].append((payload, source))

    arms = []
    for (model, task, variant), logs in sorted(grouped.items()):
        failures = Counter()
        usage = Counter()
        total_cost = 0.0
        cost_known = True
        tool_calls = 0
        model_calls = 0
        wall_time_seconds = 0.0
        started = []
        completed = []
        samples = []

        for payload, source in logs:
            stats = payload.get("stats") or {}
            if stats.get("started_at"):
                started.append(stats["started_at"])
            if stats.get("completed_at"):
                completed.append(stats["completed_at"])
            for sample in payload.get("samples") or []:
                classification = classify_sample(sample)
                failures[classification] += 1
                counts = event_counts(sample)
                tool_calls += counts["tool"]
                model_calls += counts["model"]
                wall_time_seconds += sample.get("total_time") or 0.0
                sample_usage, sample_cost = usage_for_sample(sample)
                usage.update(sample_usage)
                if sample_cost is None:
                    cost_known = False
                else:
                    total_cost += sample_cost
                samples.append(
                    {
                        "id": sample.get("id"),
                        "epoch": sample.get("epoch"),
                        "source": source,
                        "classification": classification,
                        "correct": classification == "success",
                        "wall_time_seconds": sample.get("total_time"),
                        "working_time_seconds": sample.get("working_time"),
                        "turn_count": sample.get("turn_count"),
                        "tool_calls": counts["tool"],
                        "model_calls": counts["model"],
                        "usage": sample_usage,
                        "estimated_cost_usd": sample_cost,
                    }
                )

        attempted = len(samples)
        evaluable = attempted - failures["setup_error"]
        successes = failures["success"]
        cost_value = round(total_cost, 6) if cost_known else None
        arms.append(
            {
                "model": model,
                "task": task,
                "variant": variant or None,
                "n_logs": len(logs),
                "n_attempted": attempted,
                "n_capability_evaluable": evaluable,
                "n_success": successes,
                "attempted_success_rate": (
                    round(successes / attempted, 4) if attempted else None
                ),
                "capability_success_rate": (
                    round(successes / evaluable, 4) if evaluable else None
                ),
                "failure_taxonomy": dict(sorted(failures.items())),
                "wall_time_seconds": round(wall_time_seconds, 3),
                "seconds_per_success": (
                    round(wall_time_seconds / successes, 3)
                    if successes
                    else None
                ),
                "tool_calls": tool_calls,
                "model_calls": model_calls,
                "usage": dict(usage),
                "estimated_total_cost_usd": cost_value,
                "estimated_cost_per_success_usd": (
                    round(cost_value / successes, 6)
                    if cost_value is not None and successes
                    else None
                ),
                "started_at": min(started) if started else None,
                "completed_at": max(completed) if completed else None,
                "samples": samples,
            }
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cost_basis": (
            "Amazon Bedrock us-east-2 on-demand token rates; Anthropic "
            "cache writes assume the five-minute TTL"
        ),
        "arms": arms,
    }


def percent(value):
    return "—" if value is None else f"{value * 100:.1f}%"


def money(value):
    return "—" if value is None else f"${value:.4f}"


def render_markdown(report):
    lines = [
        "# Cyber agent benchmark summary",
        "",
        (
            "Setup failures are included in attempted counts but excluded from "
            "the capability-evaluable denominator. Costs are estimates."
        ),
        "",
        (
            "| Model | Task/variant | Evaluable | Success | Capability rate | "
            "Cost | Cost/success | Time | Tools | Calls |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in report["arms"]:
        task = arm["task"]
        if arm["variant"]:
            task = f"{task} / {arm['variant']}"
        lines.append(
            "| {model} | {task} | {evaluable}/{attempted} | {success} | "
            "{rate} | {cost} | {cost_success} | {time:.1f}s | {tools} | "
            "{calls} |".format(
                model=arm["model"],
                task=task,
                evaluable=arm["n_capability_evaluable"],
                attempted=arm["n_attempted"],
                success=arm["n_success"],
                rate=percent(arm["capability_success_rate"]),
                cost=money(arm["estimated_total_cost_usd"]),
                cost_success=money(arm["estimated_cost_per_success_usd"]),
                time=arm["wall_time_seconds"],
                tools=arm["tool_calls"],
                calls=arm["model_calls"],
            )
        )

    lines.extend(["", "## Failure taxonomy", ""])
    for arm in report["arms"]:
        labels = ", ".join(
            f"{name}={count}"
            for name, count in arm["failure_taxonomy"].items()
            if name != "success"
        )
        lines.append(f"- `{arm['model']}`: {labels or 'none'}")
    lines.extend(
        [
            "",
            "## Interpretation rules",
            "",
            "- Do not treat setup errors as evidence about model capability.",
            "- A sample-limit failure means the result depends on the frozen turn, token, or time budget.",
            "- Report refusal rate separately from incorrect answers and infrastructure errors.",
            "- Cost per success is undefined when an arm has no successful samples.",
            "",
        ]
    )
    return "\n".join(lines)


def resolve_inputs(patterns):
    paths = []
    for pattern in patterns:
        candidate = Path(pattern)
        if candidate.is_dir():
            paths.extend(sorted(candidate.glob("*.eval")))
        else:
            paths.extend(Path(match) for match in sorted(glob.glob(pattern)))
    return list(dict.fromkeys(path.resolve() for path in paths))


def main():
    parser = argparse.ArgumentParser(
        description="Summarize Inspect cyber logs with cost and failure taxonomy"
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        default=["quality/results/inspect-cybench"],
        help="Inspect .eval files, directories, or glob patterns",
    )
    parser.add_argument(
        "--json-out",
        default="quality/results/CYBER_AGENT_SUMMARY.json",
    )
    parser.add_argument(
        "--markdown-out",
        default="quality/results/CYBER_AGENT_SUMMARY.md",
    )
    args = parser.parse_args()

    paths = resolve_inputs(args.inputs)
    if not paths:
        parser.error("no .eval logs matched")

    from inspect_ai.log import read_eval_log

    payloads = [read_eval_log(path) for path in paths]
    report = summarize_payloads(payloads, [str(path) for path in paths])
    json_path = Path(args.json_out)
    markdown_path = Path(args.markdown_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2) + "\n")
    markdown_path.write_text(render_markdown(report))
    print(f"Summarized {len(paths)} logs across {len(report['arms'])} arms")
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")


if __name__ == "__main__":
    main()
