"""Compare two models using timestamped quick-eval result JSONs."""

import argparse
import glob
import json
import math
import os

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def wilson_interval(successes, total, z=1.96):
    if total == 0:
        return None, None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return center - margin, center + margin


def load_latest(results_dir, backend, models, effort):
    latest = {}
    for path in glob.glob(os.path.join(results_dir, "quickeval_*.json")):
        with open(path) as handle:
            payload = json.load(handle)
        if payload.get("backend") != backend or payload.get("model") not in models:
            continue
        if payload.get("reasoning_effort") != effort:
            continue
        key = (payload["model"], payload["task"])
        candidate = {**payload, "_path": path}
        if key not in latest or candidate["timestamp"] > latest[key]["timestamp"]:
            latest[key] = candidate
    return latest


def successful(rows):
    return [row for row in rows if not row.get("error")]


def aggregate(payloads):
    rows = [row for payload in payloads for row in payload["results"]]
    ok = successful(rows)
    correct = sum(bool(row["correct"]) for row in ok)
    total_cost = sum(
        payload["summary"].get("total_cost_usd") or 0 for payload in payloads
    )
    return {
        "attempts": len(rows),
        "errors": len(rows) - len(ok),
        "correct": correct,
        "accuracy": correct / len(rows) if rows else None,
        "success_only_accuracy": correct / len(ok) if ok else None,
        "mean_latency_ms": (
            sum(row["latency_ms"] for row in ok) / len(ok) if ok else None
        ),
        "mean_output_tokens": (
            sum(row["output_tokens"] for row in ok) / len(ok) if ok else None
        ),
        "mean_reasoning_tokens": (
            sum(row["reasoning_tokens"] for row in ok) / len(ok) if ok else None
        ),
        "total_cost_usd": total_cost,
        "cost_per_success_usd": total_cost / correct if correct else None,
    }


def paired_outcomes(payload_a, payload_b):
    a_by_id = {row["id"]: row for row in payload_a["results"]}
    b_by_id = {row["id"]: row for row in payload_b["results"]}
    if set(a_by_id) != set(b_by_id):
        raise ValueError(
            f"{payload_a['task']}: model samples differ; refusing an unpaired comparison"
        )
    counts = {"both": 0, "a_only": 0, "b_only": 0, "neither": 0}
    for item_id in a_by_id:
        a_correct = bool(a_by_id[item_id]["correct"]) and not a_by_id[item_id].get(
            "error"
        )
        b_correct = bool(b_by_id[item_id]["correct"]) and not b_by_id[item_id].get(
            "error"
        )
        if a_correct and b_correct:
            counts["both"] += 1
        elif a_correct:
            counts["a_only"] += 1
        elif b_correct:
            counts["b_only"] += 1
        else:
            counts["neither"] += 1
    return counts


def pct(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def number(value, digits=1):
    return "n/a" if value is None else f"{value:,.{digits}f}"


def money(value):
    return "n/a" if value is None else f"${value:.6f}"


def build_report(latest, model_a, model_b, label_a, label_b, backend, effort):
    tasks_a = {task for model, task in latest if model == model_a}
    tasks_b = {task for model, task in latest if model == model_b}
    tasks = sorted(tasks_a & tasks_b)
    if not tasks:
        raise ValueError("no matching tasks found for both models")

    lines = [
        f"# {label_a} vs {label_b} — quality smoke",
        "",
        f"- Backend: `{backend}`",
        f"- Reasoning effort: `{effort or 'model default'}`",
        "- Accuracy uses exact task scorers. Cost is estimated from recorded token "
        "usage and repository list prices.",
        "- This is a small smoke sample; Wilson intervals are shown to make the "
        "uncertainty explicit.",
        "",
        "| Task | N | Model | Correct | Accuracy (95% Wilson CI) | Mean latency | "
        "Total cost | Cost/correct | Mean reasoning tokens |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]

    paired = {}
    for task in tasks:
        payload_a = latest[(model_a, task)]
        payload_b = latest[(model_b, task)]
        paired[task] = paired_outcomes(payload_a, payload_b)
        for payload, label in ((payload_a, label_a), (payload_b, label_b)):
            summary = payload["summary"]
            low, high = wilson_interval(summary["n_correct"], summary["n"])
            accuracy = (
                f"{pct(summary['accuracy'])} ({pct(low)}–{pct(high)})"
            )
            lines.append(
                f"| {task} | {summary['n']} | {label} | {summary['n_correct']} "
                f"| {accuracy} | {number(summary['mean_latency_ms'])} ms "
                f"| {money(summary['total_cost_usd'])} "
                f"| {money(summary['cost_per_success_usd'])} "
                f"| {number(summary['mean_reasoning_tokens'])} |"
            )

    aggregate_a = aggregate([latest[(model_a, task)] for task in tasks])
    aggregate_b = aggregate([latest[(model_b, task)] for task in tasks])
    lines += [
        "",
        "## Aggregate",
        "",
        "| Model | Correct/attempted | Accuracy | Errors | Mean latency | Total cost "
        "| Cost/correct | Mean output tokens | Mean reasoning tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for aggregate_row, label in (
        (aggregate_a, label_a),
        (aggregate_b, label_b),
    ):
        lines.append(
            f"| {label} | {aggregate_row['correct']}/{aggregate_row['attempts']} "
            f"| {pct(aggregate_row['accuracy'])} | {aggregate_row['errors']} "
            f"| {number(aggregate_row['mean_latency_ms'])} ms "
            f"| {money(aggregate_row['total_cost_usd'])} "
            f"| {money(aggregate_row['cost_per_success_usd'])} "
            f"| {number(aggregate_row['mean_output_tokens'])} "
            f"| {number(aggregate_row['mean_reasoning_tokens'])} |"
        )

    lines += [
        "",
        "## Paired outcomes",
        "",
        "| Task | Both correct | Only " + label_a + " | Only " + label_b + " | Neither |",
        "|---|---:|---:|---:|---:|",
    ]
    for task in tasks:
        counts = paired[task]
        lines.append(
            f"| {task} | {counts['both']} | {counts['a_only']} "
            f"| {counts['b_only']} | {counts['neither']} |"
        )

    lines += ["", "## Source files", ""]
    for task in tasks:
        lines.append(
            f"- `{os.path.basename(latest[(model_a, task)]['_path'])}` vs "
            f"`{os.path.basename(latest[(model_b, task)]['_path'])}`"
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="mantle")
    parser.add_argument("--model-a", required=True)
    parser.add_argument("--model-b", required=True)
    parser.add_argument("--label-a")
    parser.add_argument("--label-b")
    parser.add_argument("--effort", default=None)
    parser.add_argument("--results-dir", default=RESULTS_DIR)
    parser.add_argument(
        "--out", default=os.path.join(RESULTS_DIR, "QUICK_EVAL_COMPARISON.md")
    )
    args = parser.parse_args()

    latest = load_latest(
        args.results_dir,
        args.backend,
        {args.model_a, args.model_b},
        args.effort,
    )
    report = build_report(
        latest,
        args.model_a,
        args.model_b,
        args.label_a or args.model_a,
        args.label_b or args.model_b,
        args.backend,
        args.effort,
    )
    print(report)
    with open(args.out, "w") as handle:
        handle.write(report)
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
