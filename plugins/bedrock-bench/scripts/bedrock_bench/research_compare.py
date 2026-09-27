"""Deterministic, paired analysis of saved runs; standard library, no execution.

The public entry point is write_comparison. Selectors are a target ID, a full
execution-identity fingerprint, or a mapping with target_id/identity_key and
optional run_ids. See the compare-experiments skill's references/schema.md.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import random
import statistics

from .config import fingerprint
from .explorer import _evidence_path, _target_key, load_run
from .library import reviewed_source
from .report import aggregate, compare


OUTPUT_FILES = ("COMPARISON.json", "TASKS.csv", "ATTEMPTS.csv",
                "SUCCESS_BY_BUDGET.csv", "COMPARISON.md")
METRICS = {
    "success_rate": "fraction",
    "mean_wall_seconds": "seconds",
    "mean_agent_seconds": "seconds",
    "cost_per_attempt_usd": "USD/attempt",
    "cost_per_success_usd": "USD/success",
}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _integer(value, name, *, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        bound = f" >= {minimum}" if minimum is not None else ""
        raise ValueError(f"{name} must be an integer{bound}")
    return value


def _number(value, name, *, nullable=False):
    if value is None and nullable:
        return
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite, nonnegative number"
                         + (" or null" if nullable else ""))


def _finite(value, location):
    """Also catch overflow literals (1e999), which parse_constant misses."""
    if isinstance(value, dict):
        for key, item in value.items():
            _finite(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, f"{location}[{index}]")
    elif type(value) in (int, float):
        try:
            valid = math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f"Non-finite or overflowing numeric value at {location}")


def _unique_keys(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_source(source):
    path = Path(source).expanduser().resolve(strict=True)
    run = load_run(path)  # Reuse saved-run schema and non-finite constant checks.
    raw = path.read_bytes()
    parsed = json.loads(raw, object_pairs_hook=_unique_keys)
    _finite(parsed, str(path))
    if parsed != run:
        raise ValueError(f"Source changed while being read: {path}")
    experiment = run.get("experiment")
    if not isinstance(experiment, dict):
        raise ValueError(f"{path}: experiment metadata is required to verify the complete schedule")
    provenance = {
        "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
        "run_id": run["run_id"], "name": run.get("name"),
        "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
        "protocol_hash": run.get("protocol_hash"),
        "cost_scope": run.get("cost_scope"),
        "rate_cards": experiment.get("rate_cards", []),
    }
    if "accounting_review" in run:
        review = run["accounting_review"]
        if not isinstance(review, dict):
            raise ValueError(f"Invalid accounting review in {path}")
        original = _evidence_path(path.parent, review.get("original_run"))
        selected, checked, warning = reviewed_source(original)
        if warning or selected != path or checked != run:
            raise ValueError(f"Accounting review rejected for {path}: "
                             f"{warning or 'expected the validated run-accounting-reviewed.json'}")
        audit = _evidence_path(path.parent, review.get("source"))
        provenance["accounting_review"] = {
            "original_path": str(original), "original_sha256": _sha(original),
            "audit_path": str(audit), "audit_sha256": _sha(audit),
        }
    return run, provenance


def _kind(run):
    return "synthetic" if run["synthetic"] else "reference" if run.get("validation_only") else "live"


def _validate_run(run):
    """Verify the whole recorded schedule, including targets not selected."""
    run_id = run["run_id"]
    if type(run.get("schema_version")) is not int or run["schema_version"] != 1:
        raise ValueError("Unsupported result schema")
    _text(run.get("protocol_hash"), f"{run_id}.protocol_hash")
    if type(run.get("synthetic")) is not bool or type(run.get("validation_only", False)) is not bool:
        raise ValueError(f"{run_id}: synthetic and validation_only must be booleans")
    if run["synthetic"] and run.get("validation_only"):
        raise ValueError(f"{run_id}: a run cannot be both synthetic and reference validation")
    experiment = run.get("experiment")
    if not isinstance(experiment, dict):
        raise ValueError(f"{run_id}: experiment metadata is required to verify the complete schedule")
    tasks = experiment.get("tasks")
    if (not isinstance(tasks, list) or not tasks
            or any(not isinstance(t, str) or not t for t in tasks)
            or len(set(tasks)) != len(tasks)):
        raise ValueError(f"{run_id}: experiment.tasks must be nonempty and unique")
    repetitions = _integer(experiment.get("repetitions"), f"{run_id}.repetitions", minimum=1)
    _integer(experiment.get("seed"), f"{run_id}.seed")
    limits = experiment.get("limits")
    if not isinstance(limits, dict) or not limits:
        raise ValueError(f"{run_id}: recorded limits are required")
    for name, value in limits.items():
        _number(value, f"{run_id}.limits.{name}")
        if value <= 0:
            raise ValueError(f"{run_id}.limits.{name} must be positive")
    targets = experiment.get("targets")
    if not isinstance(targets, list) or not targets or any(not isinstance(t, dict) for t in targets):
        raise ValueError(f"{run_id}: recorded experiment targets are required")
    declared = {}
    for target in targets:
        target_id = _text(target.get("id"), f"{run_id}.target.id")
        if target_id in declared:
            raise ValueError(f"{run_id}: duplicate declared target ID {target_id!r}")
        declared[target_id] = target

    protocol = run.get("protocol")
    if protocol is not None:
        if not isinstance(protocol, dict) or fingerprint(protocol) != run["protocol_hash"]:
            raise ValueError(f"{run_id}: recorded protocol body does not match protocol_hash")
        for key in ("limits", "repetitions", "seed"):
            if protocol.get(key) != experiment[key]:
                raise ValueError(f"{run_id}: recorded protocol and experiment disagree on {key}")
        if "sources" in protocol:
            sources = protocol["sources"]
            if (not isinstance(sources, list)
                    or any(not isinstance(s, dict) or not isinstance(s.get("name"), str) for s in sources)
                    or Counter(s.get("name") for s in sources) != Counter(tasks)):
                raise ValueError(f"{run_id}: protocol fixture sources do not match declared tasks")
        if protocol.get("suite", experiment.get("suite", "starter")) != experiment.get("suite", "starter"):
            raise ValueError(f"{run_id}: protocol and experiment suites differ")

    expected = {(target_id, task, rep) for target_id in declared
                for task in tasks for rep in range(1, repetitions + 1)}
    seen_ids, seen_slots, seen_identities = set(), set(), set()
    for row in run["attempts"]:
        attempt_id = _text(row.get("attempt_id"), f"{run_id}.attempt_id")
        label = f"{run_id}/{attempt_id}"
        if attempt_id in seen_ids:
            raise ValueError(f"Duplicate attempt ID within run: {label}")
        seen_ids.add(attempt_id)
        target = row.get("target")
        if not isinstance(target, dict):
            raise ValueError(f"{label}: target must be an object")
        target_id = _text(target.get("id"), f"{label}.target.id")
        if target_id not in declared:
            raise ValueError(f"{label}: undeclared target {target_id!r}")
        for key, value in declared[target_id].items():
            if target.get(key) != value:
                raise ValueError(f"{label}: attempt target differs from experiment target on {key}")
        for key in ("runner", "provider", "model"):
            _text(target.get(key), f"{label}.target.{key}")
        version = _text(row.get("runner_version"), f"{label}.runner_version")
        if "runner_version" in target and target["runner_version"] != version:
            raise ValueError(f"{label}: conflicting runner_version values")
        row_kind = ("synthetic" if target["runner"] == "demo" or target["provider"] == "synthetic"
                    else "reference" if target["runner"] in {"oracle", "nop"} or target["provider"] == "reference"
                    else "live")
        if row_kind != _kind(run):
            raise ValueError(f"{label}: target evidence type {row_kind} contradicts run type {_kind(run)}")
        task = _text(row.get("task_id"), f"{label}.task_id")
        rep = _integer(row.get("repetition"), f"{label}.repetition", minimum=1)
        slot = (target_id, task, rep)
        logical_slot = (_target_key(target, version), task, rep)
        if slot in seen_slots or logical_slot in seen_identities:
            raise ValueError(f"Duplicate attempt task/repetition slot: {label}, {slot!r}")
        seen_slots.add(slot)
        seen_identities.add(logical_slot)
        if type(row.get("success")) is not bool:
            raise ValueError(f"{label}.success must be boolean")
        status = _text(row.get("status"), f"{label}.status")
        if status in {"running", "pending", "queued", "scheduled"}:
            raise ValueError(f"{label}: nonterminal attempt in a completed schedule")
        if row["success"] != (status == "completed"):
            raise ValueError(f"{label}: success and status disagree")
        _number(row.get("wall_seconds"), f"{label}.wall_seconds")
        _number(row.get("agent_wall_seconds"), f"{label}.agent_wall_seconds", nullable=True)
        if "cost_usd" not in row:
            raise ValueError(f"{label}: record unknown cost_usd explicitly as null")
        _number(row["cost_usd"], f"{label}.cost_usd", nullable=True)
        _number(row.get("known_cost_subtotal_usd"), f"{label}.known_cost_subtotal_usd")
        if type(row.get("accounting_complete")) is not bool:
            raise ValueError(f"{label}.accounting_complete must be boolean")
        if row["accounting_complete"] != (row["cost_usd"] is not None):
            raise ValueError(f"{label}: accounting_complete contradicts cost_usd")
        if row["cost_usd"] is not None and not math.isclose(
            row["cost_usd"], row["known_cost_subtotal_usd"], rel_tol=1e-9, abs_tol=1e-12
        ):
            raise ValueError(f"{label}: complete cost and known-cost subtotal disagree")
        bases = row.get("cost_basis")
        if not isinstance(bases, list) or not bases or any(not isinstance(b, str) or not b for b in bases):
            raise ValueError(f"{label}.cost_basis must be a nonempty list of strings")
        if _kind(run) == "live" and set(bases) & {"synthetic", "reference_no_model"}:
            raise ValueError(f"{label}: non-model cost basis in a live run")
        upstream = row.get("upstream")
        if upstream is not None:
            if not isinstance(upstream, dict):
                raise ValueError(f"{label}.upstream must be an object")
            if upstream.get("task_checksum") is not None:
                _text(upstream["task_checksum"], f"{label}.upstream.task_checksum")
    if seen_slots != expected:
        missing, extra = sorted(expected - seen_slots), sorted(seen_slots - expected)
        raise ValueError(f"{run_id}: incomplete or unmatched task/repetition schedule; "
                         f"missing={missing[:8]!r} ({len(missing)} total), "
                         f"unexpected={extra[:8]!r} ({len(extra)} total). No attempts were dropped.")
    if "schedule" in run:
        schedule = run["schedule"]
        if not isinstance(schedule, list) or any(not isinstance(s, dict) for s in schedule):
            raise ValueError(f"{run_id}: invalid saved schedule")
        recorded = []
        for slot in schedule:
            recorded.append((_text(slot.get("target"), "schedule.target"),
                             _text(slot.get("task"), "schedule.task"),
                             _integer(slot.get("repetition"), "schedule.repetition", minimum=1)))
        if len(recorded) != len(expected) or set(recorded) != expected:
            raise ValueError(f"{run_id}: saved schedule has missing, duplicate, or unexpected slots")


def _validate_protocols(runs):
    """The report gate is mandatory, even for a proposed controlled change."""
    try:
        summary = compare(runs)
    except ValueError as exc:
        if len({run["protocol_hash"] for run in runs}) > 1:
            fields = ("tasks", "repetitions", "seed", "limits", "suite")
            changed = [key for key in fields
                       if len({fingerprint(run["experiment"].get(key)) for run in runs}) > 1]
            detail = ", ".join(changed) or "fixture sources, task contents, prompts, tools, or scoring"
            raise ValueError(f"{exc}. Differing recorded factors: {detail}. "
                             "A controlled-factor hypothesis does not permit bypassing the existing "
                             "task protocol gate; use compatible saved runs. There is no override.") from exc
        raise
    # Detect stale/incorrect hashes without rebuilding fixtures from today's installation.
    for key in ("tasks", "repetitions", "seed", "limits", "suite", "aws_environment", "environment_profile"):
        values = [run["experiment"].get(key, "starter" if key == "suite" else None) for run in runs]
        if len({fingerprint(value) for value in values}) > 1:
            raise ValueError(f"Recorded experiment {key} differs despite matching protocol hashes; "
                             "these saved runs cannot isolate target effects. No protocol override is available.")
    return summary


def _select(selector, observations, role):
    if isinstance(selector, str):
        _text(selector, role)
        matches = [row for row in observations
                   if row["target_id"] == selector or row["identity_key"] == selector]
    elif isinstance(selector, dict):
        if (set(selector) - {"target_id", "identity_key", "run_ids"}
                or not ({"target_id", "identity_key"} & set(selector))):
            raise ValueError(f"{role}: selector needs target_id or identity_key and optional run_ids")
        for key in ("target_id", "identity_key"):
            if key in selector:
                _text(selector[key], f"{role}.{key}")
        requested_runs = selector.get("run_ids")
        if requested_runs is not None:
            if (not isinstance(requested_runs, list) or not requested_runs
                    or any(not isinstance(r, str) or not r for r in requested_runs)
                    or len(set(requested_runs)) != len(requested_runs)):
                raise ValueError(f"{role}.run_ids must be a nonempty, unique list of run IDs")
            unknown = set(requested_runs) - {row["run_id"] for row in observations}
            if unknown:
                raise ValueError(f"{role}: unknown selected run IDs {sorted(unknown)!r}")
        matches = [row for row in observations
                   if all(row[key] == selector[key] for key in ("target_id", "identity_key") if key in selector)
                   and (requested_runs is None or row["run_id"] in requested_runs)]
        if requested_runs is not None and {row["run_id"] for row in matches} != set(requested_runs):
            raise ValueError(f"{role}: the selected target is absent from one or more requested runs")
    else:
        raise ValueError(f"{role} must be a target ID, full identity key, or selector object")
    if not matches:
        available = sorted({(row["target_id"], row["identity_key"]) for row in observations})
        raise ValueError(f"{role} selector matched no attempts; available target IDs / keys: {available!r}")
    identities = {row["identity_key"] for row in matches}
    if len(identities) != 1:
        details = {row["identity_key"]: row["identity"] for row in matches}
        raise ValueError(f"Ambiguous {role} selector: versions/settings resolve to multiple identities: "
                         f"{json.dumps(details, sort_keys=True)}. Select a full identity_key or explicit run_ids.")
    return {
        "selector": selector, "identity_key": matches[0]["identity_key"],
        "identity": matches[0]["identity"],
        "target_ids": sorted({row["target_id"] for row in matches}),
        "run_ids": sorted({row["run_id"] for row in matches}),
        "attempts": len(matches),
    }, matches


def _mean(values):
    values = list(values)
    return statistics.fmean(values) if values and all(v is not None for v in values) else None


def _delta(baseline, candidate):
    return candidate - baseline if baseline is not None and candidate is not None else None


def _task_summary(observations):
    rows = [row["row"] for row in observations]
    return {
        **aggregate(rows),
        "mean_wall_seconds": _mean(row["wall_seconds"] for row in rows),
        "mean_agent_seconds": _mean(row.get("agent_wall_seconds") for row in rows),
        "agent_time_coverage": sum(row.get("agent_wall_seconds") is not None for row in rows) / len(rows),
    }


def _balanced(tasks, role):
    result = {key: _mean(task[role][key] for task in tasks)
              for key in METRICS if key != "cost_per_success_usd"}
    cost, success = result["cost_per_attempt_usd"], result["success_rate"]
    # Ratio of task-balanced means, never the mean of per-task cost/success.
    result["cost_per_success_usd"] = cost / success if cost is not None and success else None
    return result


def _quantile(values, probability):
    """Linear interpolation at (n - 1) * p (the common type-7 quantile)."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _metrics(tasks, resamples, seed):
    baseline, candidate = _balanced(tasks, "baseline"), _balanced(tasks, "candidate")
    samples = {key: [] for key in METRICS}
    undefined = Counter()
    performed_resamples = 0
    if len(tasks) > 1:
        rng = random.Random(seed)
        for _ in range(resamples):
            drawn = [tasks[rng.randrange(len(tasks))] for _ in tasks]
            before, after = _balanced(drawn, "baseline"), _balanced(drawn, "candidate")
            for key in METRICS:
                value = _delta(before[key], after[key])
                if value is None:
                    undefined[key] += 1
                else:
                    samples[key].append(value)
            performed_resamples += 1
    result = {}
    for key, unit in METRICS.items():
        point = _delta(baseline[key], candidate[key])
        reason = None
        if point is None:
            reason = "undefined_point_estimate"
        elif len(tasks) < 2:
            reason = "one_distinct_task"
        elif undefined[key]:
            # Conditioning on successful bootstrap draws biases ratio intervals.
            reason = "undefined_bootstrap_draws"
        interval = None if reason else {
            "level": 0.95, "low": _quantile(samples[key], 0.025),
            "high": _quantile(samples[key], 0.975),
            "method": "paired_task_cluster_percentile",
        }
        result[key] = {
            "unit": unit, "baseline": baseline[key], "candidate": candidate[key],
            "delta": point, "confidence_interval": interval, "interval_unavailable_reason": reason,
            "valid_resamples": len(samples[key]), "undefined_resamples": undefined[key],
        }
    return result, performed_resamples


def _curves(selected, task_count):
    curves = {}
    for field in ("wall_seconds", "agent_wall_seconds"):
        missing = {role: sum(row["row"].get(field) is None for row in rows)
                   for role, rows in selected.items()}
        result = {
            "descriptive": True, "timing_field": field, "missing_attempts": missing,
            "status": "unavailable" if any(missing.values()) else "available", "points": [],
        }
        curves[field] = result
        if any(missing.values()):
            result["reason"] = "Complete timing coverage on both sides is required; no subset curve was fitted."
            continue
        budgets = sorted({0, *(row["row"][field] for rows in selected.values() for row in rows)})
        for role, rows in selected.items():
            events = sorted(rows, key=lambda row: (row["row"][field], row["task_id"],
                                                   row["repetition"], row["run_id"], row["attempt_id"]))
            task_sizes = Counter(row["task_id"] for row in rows)
            task_successes = Counter()
            ended = passed = 0
            for budget in budgets:
                while ended < len(events) and events[ended]["row"][field] <= budget:
                    row = events[ended]
                    if row["row"]["success"]:
                        passed += 1
                        task_successes[row["task_id"]] += 1
                    ended += 1
                result["points"].append({
                    "role": role, "budget_seconds": budget, "attempts": len(rows),
                    "ended_attempts": ended, "successful_attempts": passed,
                    "failed_ended_attempts": ended - passed,
                    "success_fraction": math.fsum(task_successes[t] / task_sizes[t]
                                                 for t in sorted(task_sizes)) / task_count,
                })
    return curves


def _build(runs, sources, baseline, candidate, resamples, seed):
    for run in runs:
        _validate_run(run)
    existing = _validate_protocols(runs)
    observations = []
    for run, source in zip(runs, sources):
        for row in run["attempts"]:
            identity = {k: v for k, v in row["target"].items() if k != "id"}
            identity["runner_version"] = row["runner_version"]
            observations.append({
                "run_id": run["run_id"], "source": source["path"], "source_sha256": source["sha256"],
                "identity_key": _target_key(row["target"], row["runner_version"]), "identity": identity,
                "target_id": row["target"]["id"], "task_id": row["task_id"],
                "attempt_id": row["attempt_id"], "repetition": row["repetition"], "row": row,
            })
    observations.sort(key=lambda row: (row["task_id"], row["repetition"], row["run_id"], row["attempt_id"]))
    selections, selected = {}, {}
    for role, selector in (("baseline", baseline), ("candidate", candidate)):
        selections[role], selected[role] = _select(selector, observations, role)
    observation_id = lambda row: (row["run_id"], row["attempt_id"])
    overlap = {observation_id(row) for row in selected["baseline"]} & {
        observation_id(row) for row in selected["candidate"]}
    if overlap:
        raise ValueError("Baseline and candidate select overlapping attempts; scope selectors to "
                         "different targets or disjoint run_ids, even when comparing the same identity.")

    cells = {role: Counter((row["task_id"], row["repetition"]) for row in rows)
             for role, rows in selected.items()}
    expected_cells = {(task, rep) for task in runs[0]["experiment"]["tasks"]
                      for rep in range(1, runs[0]["experiment"]["repetitions"] + 1)}
    for role, rows in selected.items():
        panels = defaultdict(set)
        for row in rows:
            panels[row["run_id"], row["target_id"]].add((row["task_id"], row["repetition"]))
        for (run_id, target_id), panel in panels.items():
            if panel != expected_cells:
                raise ValueError(f"{role}: selecting this identity leaves an incomplete task/repetition panel "
                                 f"in {run_id}/{target_id}; missing={sorted(expected_cells - panel)[:8]!r}. "
                                 "Versions/settings changed within a target run; partial identities cannot be compared.")
    if cells["baseline"] != cells["candidate"]:
        mismatches = [{"task_id": task, "repetition": rep,
                       "baseline": cells["baseline"][task, rep], "candidate": cells["candidate"][task, rep]}
                      for task, rep in sorted(cells["baseline"].keys() | cells["candidate"].keys())
                      if cells["baseline"][task, rep] != cells["candidate"][task, rep]]
        raise ValueError(f"Unbalanced pairing by task/repetition: {mismatches[:8]!r} "
                         f"({len(mismatches)} cells). All observations must match; none were dropped.")

    task_ids = sorted(runs[0]["experiment"]["tasks"])
    grouped = {role: defaultdict(list) for role in selected}
    for role, rows in selected.items():
        for row in rows:
            grouped[role][row["task_id"]].append(row)
    fixtures = {}
    pinned_sources = {source["name"]: source for source in (runs[0].get("protocol") or {}).get("sources", [])}
    for task, rep in sorted(cells["baseline"]):
        rows = [row for role in selected for row in grouped[role][task] if row["repetition"] == rep]
        checksums = {(row["row"].get("upstream") or {}).get("task_checksum") for row in rows} - {None}
        if len(checksums) > 1:
            raise ValueError(f"Fixture task checksums differ for {task!r}, repetition {rep}: "
                             f"{sorted(checksums)!r}. Matching task names are insufficient.")
        pin = pinned_sources.get(task)
        for row in rows:
            observed = (row["row"].get("upstream") or {}).get("task_id")
            if isinstance(observed, dict) and pin:
                for field in ("git_commit_id", "git_url"):
                    if pin.get(field) is not None and observed.get(field) is not None and pin[field] != observed[field]:
                        raise ValueError(f"Fixture source {field} differs from the saved protocol for "
                                         f"{row['run_id']}/{row['attempt_id']}")
        fixtures[task, rep] = {
            "repetition": rep,
            "fixture_key": fingerprint([existing["protocol_hash"], task, rep]),
            "protocol_source": pin,
            "task_checksum": next(iter(checksums), None),
            "checksum_observations": sum((row["row"].get("upstream") or {}).get("task_checksum") is not None
                                         for row in rows),
            "observations": len(rows),
        }
    tasks = []
    for task in task_ids:
        item = {"task_id": task, "weight": 1 / len(task_ids),
                "fixtures": [value for (task_id, _), value in fixtures.items() if task_id == task]}
        for role in selected:
            item[role] = _task_summary(grouped[role][task])
        item["deltas"] = {key: _delta(item["baseline"][key], item["candidate"][key]) for key in METRICS}
        tasks.append(item)
    included = {observation_id(row) for rows in selected.values() for row in rows}
    excluded = Counter((row["run_id"], row["target_id"], row["identity_key"])
                       for row in observations if observation_id(row) not in included)
    excluded_rows = [{"run_id": run, "target_id": target, "identity_key": identity,
                      "attempts": count, "reason": "not_selected"}
                     for (run, target, identity), count in sorted(excluded.items())]
    attempts = []
    for role, rows in selected.items():
        for row in rows:
            saved = row["row"]
            attempts.append({
                **{key: row[key] for key in ("run_id", "source", "source_sha256", "identity_key",
                                             "target_id", "task_id", "attempt_id", "repetition")},
                "role": role, "fixture_key": fixtures[row["task_id"], row["repetition"]]["fixture_key"],
                "status": saved["status"], "success": saved["success"],
                "wall_seconds": saved["wall_seconds"], "agent_wall_seconds": saved.get("agent_wall_seconds"),
                "cost_usd": saved["cost_usd"], "known_cost_subtotal_usd": saved["known_cost_subtotal_usd"],
                "cost_basis": saved["cost_basis"], "accounting_complete": saved["accounting_complete"],
                "task_checksum": (saved.get("upstream") or {}).get("task_checksum"),
            })
    aggregates = {role: _task_summary(rows) for role, rows in selected.items()}
    metrics, performed_resamples = _metrics(tasks, resamples, seed)
    limitations = [
        {"code": "task_sampling_assumption",
         "message": "Intervals resample observed task IDs as exchangeable clusters. They describe task-mix "
                    "uncertainty conditional on these saved repetitions, not a general model ranking."},
        {"code": "shared_run_effects",
         "message": "Tasks may share run, service, or environment effects. This task bootstrap does not "
                    "estimate those correlations or future run-to-run variability."},
        {"code": "descriptive_budgets",
         "message": "Success by budget is descriptive: the fraction of all attempts observed to end "
                    "successfully by that budget. Failures/timeouts stay in its denominator. It is not "
                    "a survival estimate or a prediction for a different timeout."},
        {"code": "inference_cost_scope", "message": existing["scope"]},
    ]
    if len(tasks) == 1:
        limitations.append({"code": "one_distinct_task",
                            "message": "Only one distinct task: no confidence interval is reported. "
                                       "More repeats of it do not provide independent task evidence."})
    elif len(tasks) < 10:
        limitations.append({"code": "few_distinct_tasks",
                            "message": f"Only {len(tasks)} distinct tasks: percentile intervals can be unstable "
                                       "and poorly calibrated. More repetitions do not increase task count."})
    if 0 < performed_resamples < 1000:
        limitations.append({"code": "few_resamples",
                            "message": "Fewer than 1,000 bootstrap draws; percentile endpoints have appreciable "
                                       "Monte Carlo variability. The recorded seed makes this run reproducible."})
    if any(run.get("protocol") is None for run in runs):
        limitations.append({"code": "protocol_hash_only",
                            "message": "Some saved runs contain only a protocol hash. Their fixture identity "
                                       "relies on that recorded hash; current installed fixtures were not substituted."})
    if any((row["row"].get("upstream") is not None
            and not row["row"]["upstream"].get("task_checksum")) for rows in selected.values() for row in rows):
        limitations.append({"code": "missing_fixture_checksums",
                            "message": "Some upstream attempts lack task checksums, including possible missing "
                                       "trial results. They are retained; fixture pairing relies on the shared "
                                       "recorded protocol for these observations."})
    if any(row["cost_coverage"] < 1 for row in aggregates.values()):
        limitations.append({"code": "unknown_spend",
                            "message": "Some attempt costs are unknown. Full totals and affected cost deltas "
                                       "remain null; known-cost subtotals are not total spend."})
    if any(row["agent_time_coverage"] < 1 for row in aggregates.values()):
        limitations.append({"code": "missing_agent_time",
                            "message": "Agent time is incomplete. Affected means/deltas and both agent-budget "
                                       "curves are unavailable; trial wall time remains a separate measure."})
    if any(row["successes"] == 0 for row in aggregates.values()):
        limitations.append({"code": "zero_successes",
                            "message": "A selected side has zero successes. Its cost per success and the "
                                       "paired cost-per-success delta are undefined."})
    if metrics["cost_per_success_usd"]["interval_unavailable_reason"] == "undefined_bootstrap_draws":
        limitations.append({"code": "undefined_ratio_draws",
                            "message": "Some task bootstrap samples have zero successes. The cost-per-success "
                                       "interval is withheld; undefined draws were not silently discarded."})
    if excluded_rows:
        limitations.append({"code": "explicit_selection",
                            "message": f"{sum(excluded.values())} nonselected attempts are listed under excluded; "
                                       "all supplied runs still passed the compatibility and schedule checks."})
    identity_before, identity_after = selections["baseline"]["identity"], selections["candidate"]["identity"]
    differences = [{"field": key, "baseline": identity_before.get(key), "candidate": identity_after.get(key)}
                   for key in sorted(identity_before.keys() | identity_after.keys())
                   if (key in identity_before) != (key in identity_after) or identity_before.get(key) != identity_after.get(key)]
    if differences:
        limitations.append({"code": "recorded_factor_differences",
                            "message": "Execution identity differences are listed explicitly. A paired association "
                                       "alone does not attribute the change to a particular model, setting, or skill."})
    if aggregates["baseline"]["cost_basis"] != aggregates["candidate"]["cost_basis"]:
        limitations.append({"code": "different_cost_bases",
                            "message": "The sides use different cost bases. Compare recorded spend with its "
                                       "coverage and pricing provenance; estimates are not invoices."})
    return {
        "schema_version": 1, "analysis": "paired_saved_results", "evidence_type": _kind(runs[0]),
        "synthetic": existing["synthetic"], "validation_only": existing["validation_only"],
        "protocol_hash": existing["protocol_hash"], "scope": existing["scope"],
        "sources": sources, "selections": selections, "identity_differences": differences,
        "protocol": runs[0].get("protocol"),
        "pairing": {
            "unit": "task_id", "strata": ["protocol_hash", "task_id", "repetition"],
            "distinct_tasks": len(tasks), "repetitions": runs[0]["experiment"]["repetitions"],
            "fixture_strata": len(cells["baseline"]), "attempts_per_side": len(selected["baseline"]),
            "observations_per_stratum_per_side": sorted(set(cells["baseline"].values())),
            "run_pairing": "No arbitrary run-to-run pairing. All observations in each matched task/repetition "
                           "cell are retained with equal counts per side, then averaged within task.",
            "unmatched_attempts": 0, "excluded": excluded_rows,
        },
        "method": {
            "delta": "candidate_minus_baseline", "task_weighting": "equal",
            "bootstrap_unit": "task_id", "within_task": "all repetitions and run observations retained together",
            "resamples": resamples, "configured_resamples": resamples,
            "performed_resamples": performed_resamples, "seed": seed, "confidence_level": 0.95,
            "quantile": "linear interpolation at (n - 1) * p",
            "cost_per_success": "ratio of task-balanced cost-per-attempt and success-rate means",
            "undefined_bootstrap_draws": "withhold interval if any draw is undefined; never filter them away",
        },
        "aggregates": aggregates, "metrics": metrics, "tasks": tasks, "attempts": attempts,
        "success_by_budget": _curves(selected, len(tasks)), "limitations": limitations,
        "artifacts": {name: name for name in OUTPUT_FILES},
    }


def _cell(value):
    escaped = html.escape(str(value), quote=False).replace("\r", " ").replace("\n", " ")
    for char in ("\\", "|", "`", "[", "]", "*", "_"):
        escaped = escaped.replace(char, "\\" + char)
    return escaped


def _display(value):
    return "undefined / unknown" if value is None else f"{value:.6g}"


def _markdown(data):
    lines = [
        f"# Saved experiment comparison — {data['evidence_type']}", "",
        "Candidate minus baseline; equal weight per distinct task. "
        "Positive success deltas favor the candidate; lower cost/time deltas favor it.", "",
        f"{data['pairing']['distinct_tasks']} distinct tasks; "
        f"{data['pairing']['repetitions']} scheduled repetitions per task per run; "
        f"{data['pairing']['attempts_per_side']} attempts per side.", "",
        "| Metric | Baseline | Candidate | Paired delta | 95% task-cluster interval |",
        "|---|---:|---:|---:|---|",
    ]
    for key, metric in data["metrics"].items():
        interval = metric["confidence_interval"]
        ci = (f"[{_display(interval['low'])}, {_display(interval['high'])}]" if interval
              else "unavailable: " + metric["interval_unavailable_reason"])
        lines.append(f"| {_cell(key)} ({metric['unit']}) | {_display(metric['baseline'])} | "
                     f"{_display(metric['candidate'])} | {_display(metric['delta'])} | {_cell(ci)} |")
    lines += ["", "## Accounting and timing", "", data["scope"], "",
              "Costs include failures and timeouts. Unknown costs remain unknown; zero successes "
              "has no finite cost per success. Wall time is the recorded trial duration; "
              "agent time is separate and never substituted for it.", "",
              "| Side | Passed / attempts | Total USD | Known subtotal USD | Cost coverage | Agent-time coverage | Basis |",
              "|---|---:|---:|---:|---:|---:|---|"]
    for role, row in data["aggregates"].items():
        lines.append(f"| {role} | {row['successes']} / {row['attempts']} | {_display(row['total_cost_usd'])} | "
                     f"{_display(row['known_cost_subtotal_usd'])} | {row['cost_coverage']:.1%} | "
                     f"{row['agent_time_coverage']:.1%} | {_cell(', '.join(row['cost_basis']))} |")
    lines += ["", "## Exact selections", ""]
    for role, selection in data["selections"].items():
        lines += [f"### {role.capitalize()}", "",
                  f"Target IDs: {_cell(', '.join(selection['target_ids']))}. "
                  f"Runs: {_cell(', '.join(selection['run_ids']))}.", "",
                  f"Identity key: `{selection['identity_key']}`", "",
                  "```json", json.dumps(selection["identity"], sort_keys=True, indent=2, ensure_ascii=True), "```", ""]
    lines += ["## Limits of interpretation", ""]
    lines.extend(f"- {_cell(item['message'])}" for item in data["limitations"])
    method = data["method"]
    bootstrap = (f"Bootstrap: {method['configured_resamples']} configured draws; "
                 f"{method['performed_resamples']} performed (seed {method['seed']}). ")
    if method["performed_resamples"] == 0:
        bootstrap += "Resampling skipped because only one distinct task was observed."
    else:
        bootstrap += (
            "Whole tasks were resampled with all repetitions retained. "
            "Percentile endpoints use linear interpolation. Intervals are marginal, without "
            "multiple-comparison adjustment; they are not a significance or causal verdict."
        )
    lines += ["", bootstrap, "", "## Sources and exports", ""]
    for source in data["sources"]:
        lines += [f"- Run {_cell(source['run_id'])}: {_cell(source['path'])}; SHA-256 `{source['sha256']}`."]
        if source.get("accounting_review"):
            review = source["accounting_review"]
            lines += [f"  Accounting review original: {_cell(review['original_path'])}, "
                      f"SHA-256 `{review['original_sha256']}`; audit: {_cell(review['audit_path'])}, "
                      f"SHA-256 `{review['audit_sha256']}`."]
    lines += ["", f"Protocol: `{_cell(data['protocol_hash'])}`.", "",
              "COMPARISON.json retains all metrics, pairing checks, selections, exclusions, and limitations. "
              "TASKS.csv contains paired task aggregates; ATTEMPTS.csv retains every selected attempt "
              "including failures/timeouts; SUCCESS_BY_BUDGET.csv contains descriptive curves. "
              "CSV empty numeric cells mean unknown/undefined, never zero. JSON is the exact text authority; "
              "formula-leading CSV strings have a protective apostrophe.", ""]
    return "\n".join(lines)


def _csv(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        values = {}
        for key in fields:
            value = row.get(key)
            if isinstance(value, (list, dict)):
                value = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)
            elif isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value  # Avoid spreadsheet formulas in untrusted labels.
            values[key] = value
        writer.writerow(values)
    return stream.getvalue()


def _exports(data):
    task_rows = []
    summary_fields = ("attempts", "successes", "success_rate", "total_cost_usd",
                      "known_cost_subtotal_usd", "cost_coverage", "cost_basis", "cost_per_attempt_usd",
                      "cost_per_success_usd", "mean_wall_seconds", "mean_agent_seconds",
                      "agent_time_coverage", "failures")
    task_fields = ["task_id", "weight"]
    for role in ("baseline", "candidate"):
        task_fields.extend(f"{role}_{key}" for key in summary_fields)
    task_fields.extend(f"delta_{key}" for key in METRICS)
    for task in data["tasks"]:
        row = {"task_id": task["task_id"], "weight": task["weight"]}
        for role in ("baseline", "candidate"):
            row.update({f"{role}_{key}": task[role][key] for key in summary_fields})
        row.update({f"delta_{key}": value for key, value in task["deltas"].items()})
        task_rows.append(row)
    attempt_fields = ("role", "identity_key", "target_id", "run_id", "source", "source_sha256",
                      "task_id", "repetition", "fixture_key", "task_checksum", "attempt_id",
                      "status", "success", "wall_seconds", "agent_wall_seconds", "cost_usd",
                      "known_cost_subtotal_usd", "cost_basis", "accounting_complete")
    curve_fields = ("timing_field", "descriptive", "role", "budget_seconds", "attempts",
                    "ended_attempts", "successful_attempts", "failed_ended_attempts", "success_fraction")
    curve_rows = [{**point, "timing_field": field, "descriptive": True}
                  for field, curve in data["success_by_budget"].items() for point in curve["points"]]
    return {
        "COMPARISON.json": json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n",
        "TASKS.csv": _csv(task_rows, task_fields),
        "ATTEMPTS.csv": _csv(data["attempts"], attempt_fields),
        "SUCCESS_BY_BUDGET.csv": _csv(curve_rows, curve_fields),
        "COMPARISON.md": _markdown(data),
    }


def _check_sources(sources):
    for source in sources:
        checks = [(source["path"], source["sha256"])]
        if source.get("accounting_review"):
            review = source["accounting_review"]
            checks += [(review["original_path"], review["original_sha256"]),
                       (review["audit_path"], review["audit_sha256"])]
        for name, checksum in checks:
            if _sha(Path(name)) != checksum:
                raise ValueError(f"Source changed during comparison: {name}")


def _write_new(directory, artifacts):
    """Exclusive creation protects existing inputs, outputs, symlinks, and links."""
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name, content in artifacts.items():
            path = directory / name
            with path.open("x", encoding="utf-8", newline="") as stream:
                created.append((path, os.fstat(stream.fileno()).st_ino))
                stream.write(content)
    except BaseException:
        for path, inode in created:
            if path.exists() and not path.is_symlink() and path.stat().st_ino == inode:
                path.unlink()
        raise


def write_comparison(sources, directory, *, baseline, candidate, resamples=2000, seed=42) -> Path:
    """Write COMPARISON.md/JSON and task, attempt, and budget CSVs from saved files.

    A string selector is an unambiguous target ID or full identity key (the same
    SHA-256 used by the explorer). A mapping accepts target_id and/or identity_key,
    plus optional run_ids. Each side must select one identity and a complete,
    balanced task/repetition panel. Source files and existing outputs are never
    overwritten. The returned Markdown path is absolute.
    """
    _integer(resamples, "resamples", minimum=2)
    _integer(seed, "seed")
    if isinstance(sources, (str, os.PathLike)):
        sources = [sources]
    sources = list(sources)
    if not sources:
        raise ValueError("At least one saved run source is required")
    directory = Path(directory).expanduser().resolve()
    for name in OUTPUT_FILES:
        path = directory / name
        if path.exists() or path.is_symlink():
            raise ValueError(f"Refusing to overwrite existing input or artifact: {path}. Choose a fresh output directory.")
    loaded = [_load_source(source) for source in sources]
    loaded.sort(key=lambda pair: (pair[0]["run_id"], pair[1]["path"]))
    runs, provenance = [pair[0] for pair in loaded], [pair[1] for pair in loaded]
    try:
        data = _build(runs, provenance, baseline, candidate, resamples, seed)
        _finite(data, "comparison")
        artifacts = _exports(data)
    except OverflowError as exc:
        raise ValueError("Finite inputs overflowed the comparison arithmetic; check recorded numeric magnitudes") from exc
    _check_sources(provenance)
    _write_new(directory, artifacts)
    return directory / "COMPARISON.md"
