"""Compare matching task protocols without turning incomplete costs into rankings."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import statistics

from .config import fingerprint
from .costs import complete_sum


def aggregate(rows):
    successes = sum(row["success"] is True for row in rows)
    costs = [row["cost_usd"] for row in rows]
    total = complete_sum(costs)
    agent_times = [row.get("agent_wall_seconds") for row in rows]
    return {
        "attempts": len(rows), "successes": successes,
        "success_rate": successes / len(rows),
        "total_cost_usd": total,
        "known_cost_subtotal_usd": sum(row["known_cost_subtotal_usd"] for row in rows),
        "cost_coverage": sum(cost is not None for cost in costs) / len(rows),
        "cost_per_attempt_usd": total / len(rows) if total is not None else None,
        "cost_per_success_usd": total / successes if total is not None and successes else None,
        "median_wall_seconds": statistics.median(row["wall_seconds"] for row in rows),
        "median_agent_seconds": statistics.median(agent_times) if all(t is not None for t in agent_times) else None,
        "cost_basis": sorted({basis for row in rows for basis in row["cost_basis"]}),
        "failures": dict(sorted((status, sum(row["status"] == status for row in rows if not row["success"]))
                               for status in {row["status"] for row in rows if not row["success"]})),
    }


def compare(runs):
    if not runs:
        raise ValueError("At least one run is required")
    if any(run.get("schema_version") != 1 for run in runs):
        raise ValueError("Unsupported result schema")
    if any(run.get("status") != "completed" or not run.get("attempts") for run in runs):
        raise ValueError("Compare completed, nonempty runs; interrupted runs retain evidence in run.json")
    if len({run["protocol_hash"] for run in runs}) != 1:
        raise ValueError("Task protocols differ: use matching tasks, fixtures, repetitions, seed, and limits")
    if len({run["synthetic"] for run in runs}) != 1:
        raise ValueError("Synthetic and live results cannot be combined")
    if len({run.get("validation_only", False) for run in runs}) != 1:
        raise ValueError("Reference validation cannot be compared with model measurements")
    if len({run["run_id"] for run in runs}) != len(runs):
        raise ValueError("The same run was supplied more than once")
    groups, task_groups = defaultdict(list), defaultdict(list)
    target_info = {}
    for run in runs:
        for row in run["attempts"]:
            # Target IDs are friendly labels; execution identity includes version.
            identity = {k: v for k, v in row["target"].items() if k != "id"}
            identity["runner_version"] = row["runner_version"]
            key = fingerprint(identity)
            target_info[key] = {"id": row["target"]["id"], **identity}
            groups[key].append(row)
            task_groups[(key, row["task_id"])].append(row)
    return {
        "schema_version": 1, "synthetic": runs[0]["synthetic"],
        "validation_only": runs[0].get("validation_only", False),
        "protocol_hash": runs[0]["protocol_hash"],
        "run_ids": [run["run_id"] for run in runs],
        "targets": [{"target": target_info[key], **aggregate(rows)} for key, rows in groups.items()],
        "tasks": [{"target": target_info[key], "target_id": target_info[key]["id"], "task_id": task, **aggregate(rows)}
                  for (key, task), rows in task_groups.items()],
        "scope": "Inference only. Infrastructure, subscriptions, external tools, and judge costs are excluded.",
        "upstream_trials": [
            {**row["upstream"], "run_id": run["run_id"], "attempt_id": row["attempt_id"],
             "task_id": row["task_id"], "source": row["upstream"].get("task_id")}
            for run in runs for row in run["attempts"] if row.get("upstream")
        ],
    }


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def dollars(value):
    return "unknown" if value is None else f"${value:.6f}"


def markdown(summary):
    label = "Synthetic demonstration — no model measurements" if summary["synthetic"] else "Agent task results"
    if summary.get("validation_only"):
        label = "Reference validation — no model measurements"
    lines = [
        f"# Bedrock Bench — {label}", "",
        summary["scope"], "",
        "Cost per success includes spend on failed attempts. An incomplete cost total remains unknown. "
        "Zero successes has no finite cost per success.", "",
        "| Target | Runner / provider / model | Attempts | Passed | Cost coverage | Total cost | Cost / success | Median wall seconds | Median agent seconds | Basis |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary["targets"]:
        target = row["target"]
        identity = f"{target['runner']} / {target['provider']} / {target['model']}"
        cps = "no successes" if not row["successes"] else dollars(row["cost_per_success_usd"])
        agent_time = "unavailable" if row["median_agent_seconds"] is None else f"{row['median_agent_seconds']:.3f}"
        lines.append(f"| {cell(target['id'])} | {cell(identity)} | {row['attempts']} | {row['successes']} | "
                     f"{row['cost_coverage']:.0%} | {dollars(row['total_cost_usd'])} | {cps} | "
                     f"{row['median_wall_seconds']:.3f} | {agent_time} | {cell(', '.join(row['cost_basis']))} |")
    lines += [
        "", "## Per-task results", "",
        "| Target | Task | Passed / attempts | Total cost | Cost / success |",
        "|---|---|---:|---:|---:|",
    ]
    for row in summary["tasks"]:
        cps = "no successes" if not row["successes"] else dollars(row["cost_per_success_usd"])
        target_label = f"{row['target_id']} / {row['target']['runner']} / {row['target']['model']}"
        lines.append(f"| {cell(target_label)} | {cell(row['task_id'])} | "
                     f"{row['successes']} / {row['attempts']} | {dollars(row['total_cost_usd'])} | {cps} |")
    lines += [
        "", "## Target settings", "",
    ]
    for row in summary["targets"]:
        target = row["target"]
        lines.append(f"- **{cell(target['id'])}**: runner `{cell(target['runner_version'])}`; "
                     f"region `{cell(target['region'])}`; reasoning `{cell(target['reasoning_effort'])}`; "
                     f"tier `{cell(target['service_tier'])}`; routing `{cell(target['routing'])}`.")
        if target.get("agent_version"):
            lines.append(f"  Requested agent version: `{cell(target['agent_version'])}`.")
        if target.get("skill_sha256"):
            lines.append(f"  Skill source digests: `{cell(json.dumps(target['skill_sha256'], sort_keys=True))}`.")
    if summary.get("upstream_trials"):
        lines += ["", "## Upstream evidence", ""]
        for row in summary["upstream_trials"]:
            result = row.get("result")
            evidence = f"`attempts/{row['attempt_id']}/{result}`" if result else "missing upstream result"
            lines.append(f"- `{cell(row['task_id'])}` ({cell(row['harness'])}): {evidence}; "
                         f"task checksum `{cell(row.get('task_checksum'))}`; run `{cell(row['run_id'])}`.")
        lines += [
            "", "Reported wall time includes harness setup, the agent, verification, and cleanup. "
            "Agent-only time is retained separately in run.json. Upstream numeric reward=1 is a pass; "
            "missing results, verifier errors, and timeouts remain visible as failed attempts.",
        ]
    if summary.get("validation_only"):
        lines += ["", "This run checks benchmark plumbing using a reference solution and/or an unchanged "
                  "baseline. Zero inference cost reflects no model calls and is not a model cost comparison."]
    lines += [
        "", "## Interpretation", "",
        "- Native runs hold the agent loop and filesystem tools fixed.",
        "- Codex and OpenCode runs compare complete agent systems; their internal prompts, tools, "
        "retries, and caching differ.",
        "- `provider_reported` is an API-reported charge; `rate_card_estimate` and `runner_estimate` "
        "are estimates, not invoices.",
        "- Known-cost subtotals and failure counts are retained in comparison.json even when full cost is unknown.",
        "- Starter tasks and small samples validate the harness; they do not establish a general model ranking.",
        "- Match model access, reasoning settings, pricing, and environment before drawing conclusions.",
        "", f"Protocol: `{summary['protocol_hash']}`",
        "", "Runs: " + ", ".join(f"`{run_id}`" for run_id in summary["run_ids"]), "",
    ]
    return "\n".join(lines)


def write_report(runs, directory, *, sources=None, inline=False):
    from .explorer import render_explorer
    summary = compare(runs)
    directory = Path(directory)
    html = render_explorer(runs, summary, directory, sources)
    fragment = render_explorer(runs, summary, directory, sources, inline=True) if inline else None
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "comparison.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    (directory / "REPORT.md").write_text(markdown(summary))
    (directory / "REPORT.html").write_text(html)
    if fragment is not None:
        (directory / "REPORT.inline.html").write_text(fragment)
    return summary
