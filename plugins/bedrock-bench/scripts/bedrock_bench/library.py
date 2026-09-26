"""Index recent saved runs with provenance, comparable summaries, and replays."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .activity import activity_data, replay_fragment, target_label, timestamp
from .config import fingerprint
from .explorer import _evidence_path, _target_key, list_runs, load_run
from .report import aggregate, compare
from .views import ASSETS, embedded_json, write_view

ACCOUNTING_FIELDS = {
    "usage", "usage_granularity", "reported_usage_events", "cost_usd",
    "known_cost_subtotal_usd", "cost_basis", "accounting_complete", "accounting_review",
}


def reviewed_source(original):
    """Prefer an explicit reviewed copy only when it still describes this run."""
    original = Path(original).resolve()
    run = load_run(original)
    candidate = original.parent / "run-accounting-reviewed.json"
    if not candidate.exists():
        return original, run, None
    try:
        candidate = _evidence_path(original.parent, candidate.name)
        reviewed = load_run(candidate)
        review = reviewed.get("accounting_review", {})
        if (review.get("original_run") != original.name
                or review.get("original_sha256") != hashlib.sha256(original.read_bytes()).hexdigest()):
            raise ValueError("Original result checksum does not match the accounting review")
        audit = _evidence_path(original.parent, review.get("source"))
        if not audit.is_file():
            raise ValueError("Accounting review source is missing")
        unchanged = lambda value: {k: v for k, v in value.items()
                                   if k not in {"name", "attempts", "accounting_review"}}
        if unchanged(run) != unchanged(reviewed) or len(run["attempts"]) != len(reviewed["attempts"]):
            raise ValueError("Reviewed copy changes the run or task protocol")
        for before, after in zip(run["attempts"], reviewed["attempts"]):
            if ({k: v for k, v in before.items() if k not in ACCOUNTING_FIELDS}
                    != {k: v for k, v in after.items() if k not in ACCOUNTING_FIELDS}):
                raise ValueError("Reviewed copy changes an outcome, target, timing, or evidence path")
        return candidate, reviewed, None
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return original, run, f"Reviewed accounting copy ignored: {exc}"


def _empty_total():
    return {
        "attempts": 0, "successes": 0, "success_rate": None, "total_cost_usd": None,
        "known_cost_subtotal_usd": 0, "cost_coverage": 0, "cost_per_attempt_usd": None,
        "cost_per_success_usd": None, "median_wall_seconds": None,
        "median_agent_seconds": None, "cost_basis": [], "failures": {},
    }


def _summary(run):
    if run.get("status") == "completed" and run["attempts"]:
        summary = compare([run])
    else:
        # Retain partial evidence without making an interrupted run comparable.
        groups = defaultdict(list)
        for row in run["attempts"]:
            groups[_target_key(row["target"], row["runner_version"])].append(row)
        summary = {"targets": [{"target": {**rows[0]["target"], "runner_version": rows[0]["runner_version"]},
                                **aggregate(rows)} for rows in groups.values()], "tasks": []}
    targets = []
    for target in summary["targets"]:
        identity = target["target"]
        targets.append({**target, "key": _target_key(identity, identity["runner_version"]),
                        "label": target_label(identity)})
    return targets, summary["tasks"]


def _entry(source, original, run):
    targets, tasks = _summary(run)
    comparable = run.get("status") == "completed" and bool(run["attempts"])
    group = fingerprint([run["protocol_hash"], run["synthetic"], run.get("validation_only", False)]) if comparable else None
    duration = run.get("controller_wall_seconds")
    if run.get("started_at") and run.get("finished_at"):
        duration = timestamp(run["finished_at"]) - timestamp(run["started_at"])
    name = (load_run(original).get("name") if source != original else run.get("name")) or run["run_id"]
    identities = [row["target"] for row in run["attempts"]]
    return {
        "id": run["run_id"], "name": name,
        "source": str(source), "originalSource": str(original),
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "startedAt": run.get("started_at") or "", "finishedAt": run.get("finished_at"),
        "duration": duration, "status": run.get("status"),
        "kind": "demo" if run.get("synthetic") else "reference" if run.get("validation_only") else "live",
        "suite": run.get("experiment", {}).get("suite", "starter"),
        "protocol": run.get("protocol_hash"), "comparisonGroup": group,
        "tasks": sorted({row["task_id"] for row in run["attempts"]}),
        "models": sorted({t["model"] for t in identities}),
        "runners": sorted({t["runner"] for t in identities}),
        "regions": sorted({t["region"] for t in identities if t.get("region")}),
        "reviewedAccounting": source != original, "hasReplay": False,
        "total": aggregate(run["attempts"]) if run["attempts"] else _empty_total(),
        "targets": targets, "taskResults": tasks,
        "costScope": "Inference only. Infrastructure, subscriptions, external tools, and judge costs are excluded.",
        "attempts": [{"id": row["attempt_id"], "target": row["target"]["id"],
                      "model": row["target"]["model"], "task": row["task_id"],
                      "status": row["status"], "passed": row["success"], "cost": row["cost_usd"]}
                     for row in run["attempts"]],
    }


def library_data(directory, *, limit=50, replay_limit=5):
    if not 1 <= limit <= 1000 or not 0 <= replay_limit <= 50:
        raise ValueError("Use --limit 1–1000 and --replay-limit 0–50")
    directory = Path(directory).expanduser().resolve()
    discovered = list_runs(directory)
    runs, activities, warnings = [], {}, []
    unreadable = list(discovered["unreadable"])
    seen = set()
    for saved in discovered["runs"][:limit]:
        original = Path(saved["path"])
        try:
            source, run, warning = reviewed_source(original)
            if run["run_id"] in seen:
                raise ValueError(f"Duplicate run ID {run['run_id']}; keep one source in this results directory")
            entry = _entry(source, original, run)
            json.dumps(entry, allow_nan=False)
            seen.add(run["run_id"])
            if warning:
                warnings.append({"run": run["run_id"], "message": warning})
            runs.append(entry)
            if len(activities) < replay_limit:
                try:
                    replay = activity_data(source)
                    replay["name"] = entry["name"]
                    if replay["models"]:
                        activities[run["run_id"]] = replay
                        entry["hasReplay"] = True
                    else:
                        entry["replayUnavailable"] = "No supported saved tool trace. Use per-attempt evidence."
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    entry["replayUnavailable"] = f"Replay could not be indexed: {exc}"
        except (OSError, ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError) as exc:
            # One corrupt run must not hide the rest of the library.
            unreadable.append({"path": str(original), "error": str(exc)})
    return {
        "version": 1, "directory": str(directory), "snapshotAt": datetime.now(timezone.utc).isoformat(),
        "runs": runs, "activities": activities, "unreadable": unreadable, "warnings": warnings,
        "discovered": len(discovered["runs"]), "limit": limit, "replayLimit": replay_limit,
    }


def write_library(directory, output, *, inline=False, limit=50, replay_limit=5):
    data = library_data(directory, limit=limit, replay_limit=replay_limit)
    fragment = ((ASSETS / "library.html").read_text()
                .replace("__TRACE_FRAGMENT__", replay_fragment(None))
                .replace("__LIBRARY_DATA__", embedded_json(data)))
    return write_view(output, "LIBRARY", data, fragment, inline=inline)
