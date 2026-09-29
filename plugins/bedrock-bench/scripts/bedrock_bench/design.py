"""Offline experiment design using the packaged runner's validation and protocol."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
import hashlib
import json
import math
from pathlib import Path
import random
import re
import sys
import tempfile

from . import __version__
from .config import Experiment, Target, fingerprint
from .costs import FIELDS, estimate, find_rate
from .suites import (
    PLUGIN, SuiteExperiment, SuiteTarget, directory_digest,
    load_experiment, task_catalog,
)


# Both runners materialize and shuffle a finite schedule. Fail before allocating
# an unexpectedly large plan; never silently reduce the user's repetitions.
MAX_ATTEMPTS = 100_000
CONDITION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")


def _object(value, name, *, required=(), allowed=None):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    missing = set(required) - value.keys()
    extra = value.keys() - set(allowed) if allowed is not None else set()
    if missing or extra:
        raise ValueError(f"{name}: missing fields {sorted(missing)}; unsupported fields {sorted(extra)}")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _number(value, name, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a nonnegative {'integer' if integer else 'number'}")
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid or (integer and not isinstance(value, int)):
        raise ValueError(f"{name} must be a finite nonnegative {'integer' if integer else 'number'}")
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON field: {key}")
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def _json_bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _experiment(data, base):
    """Validate shape before delegating semantics and defaults to the runner."""
    _object(data, "base_experiment", required=("name", "targets", "tasks"))
    version = data.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("base_experiment.schema_version must be 1 or 2")
    cls, target_cls = (Experiment, Target) if version == 1 else (SuiteExperiment, SuiteTarget)
    _object(data, "base_experiment", allowed={f.name for f in fields(cls)})
    if not isinstance(data["targets"], list) or not data["targets"]:
        raise ValueError("base_experiment.targets must be a nonempty array")
    for target in data["targets"]:
        _object(target, "target", required=("id", "runner", "provider", "model"),
                allowed={f.name for f in fields(target_cls)})
    if "limits" in data:
        _object(data["limits"], "limits")
    try:
        return (Experiment.from_dict(data) if version == 1
                else SuiteExperiment.from_dict(data, base=base))
    except (TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise ValueError(f"Invalid experiment: {exc}") from exc


def _target_id(condition, target):
    name = f"{condition}--{target}"
    if len(name) > 64:
        name = name[:51] + "-" + fingerprint([condition, target])[:12]
    return name


def _conditions(brief, base, brief_dir):
    requested = brief["conditions"]
    if not isinstance(requested, list) or len(requested) < 2:
        raise ValueError("conditions must contain a baseline and at least one variant")
    ids, identities, generated_ids = set(), set(), set()
    base_targets = {target["id"]: target for target in base.to_dict()["targets"]}
    allowed = set(next(iter(base_targets.values()))) - {"id"}
    results, varied_fields = [], set()
    for index, condition in enumerate(requested):
        _object(condition, "condition", required=("id", "target_changes"),
                allowed=("id", "label", "target_changes"))
        cid = condition["id"]
        if not isinstance(cid, str) or not CONDITION_ID.fullmatch(cid) or cid.casefold() in ids:
            raise ValueError("Condition IDs must be unique and contain 1–32 letters, numbers, _ or -")
        ids.add(cid.casefold())
        label = _text(condition.get("label", cid), "condition.label")
        changes = _object(condition["target_changes"], "target_changes")
        if index == 0 and changes:
            raise ValueError("The first condition must be the unchanged baseline (target_changes: {})")
        if index and set(changes) != set(base_targets):
            raise ValueError("Every variant must explicitly change each base target, using its exact ID")
        data = base.to_dict()
        data["name"] = f"{base.name} / {label}"
        for target in data["targets"]:
            patch = changes.get(target["id"], {})
            _object(patch, f"target_changes.{target['id']}", allowed=allowed)
            target.update(deepcopy(patch))
        # This also resolves every variant's skill paths against the original
        # brief, before configs are moved into the output directory.
        candidate = _experiment(data, brief_dir).to_dict()
        target_map, differences = {}, {}
        for target in candidate["targets"]:
            base_id = target["id"]
            different = sorted(key for key in allowed if target[key] != base_targets[base_id][key])
            if index and not different:
                raise ValueError(f"Condition {cid} does not change target {base_id}")
            varied_fields.update(different)
            differences[base_id] = different
            identity = fingerprint({key: value for key, value in target.items() if key != "id"})
            if identity in identities:
                raise ValueError("Duplicate target settings across conditions; use repetitions for repeats")
            identities.add(identity)
            target["id"] = _target_id(cid, base_id)
            if target["id"] in generated_ids:
                raise ValueError("Condition and target IDs produce a duplicate combined target ID")
            generated_ids.add(target["id"])
            target_map[base_id] = target["id"]
        results.append({
            "id": cid, "label": label, "target_changes": deepcopy(changes),
            "changed_fields": differences, "target_ids": target_map,
            "experiment": f"conditions/{cid}.json", "config": candidate,
        })
    if brief["contrast"] == "single-factor" and len(varied_fields) != 1:
        raise ValueError("single-factor requires exactly one changed target field across all variants")
    return results, sorted(varied_fields)


def _budget(brief, conditions, combined):
    budget = _object(brief.get("budget", {}), "budget",
                     allowed=("max_usd", "reserve_fraction", "usage_per_attempt"))
    ceiling = budget.get("max_usd")
    if ceiling is not None:
        _number(ceiling, "budget.max_usd")
    reserve = _number(budget.get("reserve_fraction", 0), "budget.reserve_fraction")
    if reserve > 1:
        raise ValueError("budget.reserve_fraction must be between 0 and 1")
    usage_rows = budget.get("usage_per_attempt", [])
    if not isinstance(usage_rows, list):
        raise ValueError("budget.usage_per_attempt must be an array")
    valid_cells = {(c["id"], tid) for c in conditions for tid in c["target_ids"]}
    assumptions = {}
    for row in usage_rows:
        _object(row, "usage assumption", required=("condition", "target", "source"),
                allowed={"condition", "target", "source", *FIELDS})
        key = (_text(row["condition"], "usage.condition"), _text(row["target"], "usage.target"))
        if key not in valid_cells or key in assumptions:
            raise ValueError("Usage assumptions must identify a unique condition and base target")
        _text(row["source"], "usage.source")
        usage = {name: row.get(name) for name in FIELDS}
        for name, value in usage.items():
            if value is not None:
                _number(value, f"usage.{name}", integer=True)
        inp, out = usage["input_tokens"], usage["output_tokens"]
        cache = sum(usage[name] or 0 for name in ("cached_input_tokens", "cache_write_input_tokens"))
        if inp is not None and cache > inp:
            raise ValueError("Cache reads and writes are subsets of input_tokens")
        reasoning = usage["reasoning_output_tokens"]
        if out is not None and reasoning is not None and reasoning > out:
            raise ValueError("reasoning_output_tokens is a subset of output_tokens")
        assumptions[key] = {"usage": usage, "source": row["source"]}

    targets = {t.id: t for t in combined.targets}
    attempts_per_cell = len(combined.tasks) * combined.repetitions
    cells = []
    for condition in conditions:
        for base_id, target_id in condition["target_ids"].items():
            assumption = assumptions.get((condition["id"], base_id))
            card = find_rate(combined.rate_cards, targets[target_id])
            cost = estimate(assumption["usage"], card) if assumption else None
            if cost is not None:
                _number(cost, "estimated cost per attempt")
            total = None if cost is None else round(cost * attempts_per_cell, 12)
            if total is not None:
                _number(total, "estimated cell cost")
            cells.append({
                "condition": condition["id"], "base_target": base_id, "target": target_id,
                "attempts": attempts_per_cell, "assumption": assumption, "rate_card": card,
                "estimated_usd_per_attempt": cost, "estimated_inference_usd": total,
                "basis": "assumed_usage_rate_card_estimate" if cost is not None else "unknown",
            })
    known = sum(cell["attempts"] for cell in cells if cell["estimated_inference_usd"] is not None)
    attempts = attempts_per_cell * len(targets)
    subtotal = round(sum(cell["estimated_inference_usd"] or 0 for cell in cells), 12)
    _number(subtotal, "estimated inference subtotal")
    total = subtotal if known == attempts else None
    reserve_usd = None if ceiling is None else ceiling * reserve
    allowance = None if ceiling is None else ceiling - reserve_usd
    return {
        "currency": "USD", "max_usd": ceiling, "reserve_fraction": reserve,
        "reserved_usd": reserve_usd, "inference_allowance_usd": allowance,
        "estimated_inference_usd": total, "known_estimate_subtotal_usd": subtotal,
        "estimated_attempts": known, "total_attempts": attempts, "estimate_coverage": known / attempts,
        "within_inference_allowance": None if total is None or allowance is None else total <= allowance,
        "enforced_dollar_cap": False, "all_in_total_usd": None,
        "excluded_costs": ["runner infrastructure", "subscriptions", "external tools", "verifier/judge inference"],
        "cells": cells,
    }


def _schedule(experiment, conditions):
    lookup = {tid: (c["id"], base_id) for c in conditions for base_id, tid in c["target_ids"].items()}
    # Keep this identical to engine.execute and upstream.execute_suite. The seed
    # schedules trials; it is not sent to a provider as a model sampling seed.
    schedule = [(target.id, task, rep) for rep in range(experiment.repetitions)
                for task in experiment.tasks for target in experiment.targets]
    random.Random(experiment.seed).shuffle(schedule)
    return [
        {
            "index": index, "attempt_id": f"{index:04}-{target}-{task}-r{rep + 1}",
            "condition": lookup[target][0], "base_target": lookup[target][1],
            "target": target, "task": task, "repetition": rep + 1,
            "fixture_seed": experiment.seed + rep if isinstance(experiment, Experiment) else None,
        }
        for index, (target, task, rep) in enumerate(schedule, 1)
    ]


def _limitations(experiment, budget):
    suite = getattr(experiment, "suite", "starter")
    notices = []
    if suite == "starter":
        notices.append({"code": "starter_plumbing_only", "detail":
                        "The three starter fixtures test benchmark plumbing, not general agent quality."})
    if suite == "aws-cdk-smoke":
        notices.append({"code": "cdk_catalog_pilot", "detail":
                        "The packaged CDK catalog has one SQS/Lambda/DynamoDB repair task. Repeats do not add AWS workload coverage."})
    if len(experiment.tasks) == 1:
        notices.append({"code": "single_task_pilot", "detail":
                        "One distinct task supports a pilot on that task, not a population-level ranking."})
    if suite in {"terminal-bench", "swe-bench", "aws-bench"}:
        notices.append({"code": "packaged_catalog_snapshot", "detail":
                        "Selection uses the packaged registry snapshot. Task commits do not pin all downloaded dependencies or image bytes."})
    if suite == "aws-bench":
        notices.append({"code": "aws_environment_and_judge", "detail":
                        "AWS-Bench requires the selected testing environment and a model judge; their costs are outside agent inference."})
    if isinstance(experiment, SuiteExperiment) and not experiment.validation_only:
        if any(target.agent_version is None for target in experiment.targets):
            notices.append({"code": "agent_version_unpinned", "detail":
                            "At least one agent CLI version is unpinned. Record actual versions and recheck compatibility after execution."})
    if budget["estimate_coverage"] < 1:
        notices.append({"code": "unknown_inference_cost", "detail":
                        "Missing usage assumptions or exact applicable rate cards leave some or all inference costs unknown."})
    if budget["within_inference_allowance"] is False:
        notices.append({"code": "estimated_budget_exceeded", "detail":
                        "Assumed inference spend exceeds the stated allowance; the planner has not reduced attempts or changed targets."})
    return notices


def _markdown(design):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    def money(value):
        return "unknown" if value is None else f"${value:.6f}"

    counts, execution, budget = design["counts"], design["execution"], design["budget"]
    plan = design["experiment"]["plan"]
    lines = [
        f"# {cell(plan['name'])}", "", cell(design["question"]), "",
        f"Status: planned only. Evidence mode if executed: **{plan['mode']}**. No trials have run.",
        "", f"{counts['distinct_tasks']} distinct tasks × {counts['repetitions']} repetitions × "
        f"{counts['base_targets']} base targets × {counts['conditions']} conditions = "
        f"**{counts['total_attempts']} attempts**. Repetitions do not add distinct tasks.",
        "", f"Contrast: `{design['contrast']}`. Changed target fields: "
        + ", ".join(f"`{field}`" for field in design["varied_fields"]) + ".",
        ("A single-factor contrast is matched within each base target; observed differences still need "
         "uncertainty and evidence checks." if design["contrast"] == "single-factor" else
         "This compares complete settings bundles. It cannot attribute a difference to one changed component."),
        "", "## Experiment files", "",
        "Use [experiment.json](experiment.json) for one run interleaving all conditions. "
        "The condition files below are alternatives for separate runs; executing both forms duplicates the budget.",
        "", "| Condition | Base target → runner target | Attempts | Alternative config |",
        "|---|---|---:|---|",
    ]
    for condition in design["conditions"]:
        mapping = "; ".join(f"{base} → {target}" for base, target in condition["target_ids"].items())
        lines.append(f"| {cell(condition['label'])} | {cell(mapping)} | "
                     f"{condition['plan']['attempts']} | [{condition['id']}]({condition['experiment']}) |")
    lines += [
        "", "## Bounds and order", "", f"Selected tasks: {', '.join(f'`{t}`' for t in design['tasks'])}.",
        f"Seed: `{design['seed']}`. Both runners use a seeded shuffle of repetition → task → target. "
        "The exact primary schedule and attempt IDs are in [DESIGN.json](DESIGN.json). "
        "Separate condition configs have their own shuffles and do not reproduce the interleaved order.",
        "The seed controls trial order and starter fixture generation; it does not guarantee deterministic model responses.",
        "", "```json", json.dumps(design["limits"], indent=2), "```", "",
        f"Limit support: {cell(json.dumps(plan['limit_support']) if isinstance(plan['limit_support'], dict) else plan['limit_support'])}",
        f"Serial execution; {execution['automatic_trial_retries']} automatic trial retries. "
        "CLI-internal retries and auxiliary calls may be client-managed.",
        f"Sum of configured per-attempt controller timeout allowances: "
        f"{execution['summed_controller_timeout_seconds']:g} seconds. "
        "This excludes controller overhead and is not an enforced whole-run deadline.",
        "", "## Budget assumptions", "",
        f"Planning ceiling: {money(budget['max_usd'])}; reserve: {budget['reserve_fraction']:.0%}; "
        f"inference allowance: {money(budget['inference_allowance_usd'])}.",
        f"Estimated inference total: **{money(budget['estimated_inference_usd'])}**. "
        f"Known estimate subtotal: {money(budget['known_estimate_subtotal_usd'])}; "
        f"coverage: {budget['estimated_attempts']}/{budget['total_attempts']} attempts.",
        "These are user-supplied token assumptions multiplied by exact rate cards, including failed attempts. "
        "No prices or success rates are inferred. Cache and reasoning tokens are subsets, not added twice.",
        "**There is no enforced dollar cap.** Infrastructure, subscriptions, external tools, and verifier/judge "
        "costs are excluded. The reserve is an assumption; the all-in cost remains unknown.",
        "", "## Provenance and interpretation", "",
        f"Protocol hash: `{design['protocol_hash']}`. Plugin version: `{design['provenance']['plugin_version']}`.",
        "[DESIGN.json](DESIGN.json) records task sources, skill content hashes, requested changes, normalized "
        "runner plans, cost inputs, and file hashes. [brief.json](brief.json) preserves the input bytes. "
        "The original brief and skill directories are not modified.",
        "Compare only completed runs that pass the runner's protocol and evidence-type checks. A shared planned "
        "protocol hash alone does not establish that saved runs used the same agent versions or skill contents.",
        "Use paired task/repetition outcomes for a controlled contrast. Treat repetitions of a task as repeated "
        "observations of that task; do not claim extra independent task coverage or an official leaderboard result.",
        "",
    ]
    for notice in design["limitations"]:
        lines.append(f"- {notice['detail']}")
    if plan.get("preparation_needed"):
        lines += ["", "Preparation needed before a separately authorized run:", ""]
        lines += [f"- {cell(issue)}" for issue in plan["preparation_needed"]]
    if design["notes"]:
        lines += ["", "## Brief notes", ""]
        lines += [f"- {cell(note)}" for note in design["notes"]]
    return "\n".join(lines) + "\n"


def write_design(brief_path, directory) -> Path:
    """Write a new offline plan directory and return its absolute DESIGN.md path.

    The brief embeds a normal runner experiment, an unchanged first condition,
    and explicit target patches for subsequent conditions. Relative tools/skills
    resolve against the input brief. Invalid briefs raise ValueError; an existing
    output path raises FileExistsError. No runtime is prepared or executed.
    """
    source = Path(brief_path).expanduser().resolve()
    requested_output = Path(directory).expanduser()
    if requested_output.exists() or requested_output.is_symlink():
        raise FileExistsError(f"Design output must be a new directory: {requested_output}")
    output = requested_output.resolve()
    if output.is_relative_to(PLUGIN):
        raise ValueError("Keep design outputs outside the installed plugin")
    raw = source.read_bytes()
    try:
        brief = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON brief: {exc}") from exc
    _object(brief, "brief", required=("schema_version", "question", "contrast", "base_experiment", "conditions"),
            allowed=("schema_version", "question", "contrast", "base_experiment", "conditions", "budget", "notes"))
    if type(brief["schema_version"]) is not int or brief["schema_version"] != 1:
        raise ValueError("brief.schema_version must be 1")
    _text(brief["question"], "question")
    if brief["contrast"] not in ("single-factor", "system-comparison"):
        raise ValueError("contrast must be single-factor or system-comparison")
    notes = brief.get("notes", [])
    if not isinstance(notes, list):
        raise ValueError("notes must be an array of strings")
    for note in notes:
        _text(note, "note")
    base = _experiment(brief["base_experiment"], source.parent)
    conditions, varied = _conditions(brief, base, source.parent)
    combined_data = base.to_dict()
    combined_data["targets"] = [target for c in conditions for target in c["config"]["targets"]]
    combined = _experiment(combined_data, source.parent)
    total_attempts = len(combined.targets) * len(combined.tasks) * combined.repetitions
    if total_attempts > MAX_ATTEMPTS:
        raise ValueError(f"Design exceeds the materialized schedule limit of {MAX_ATTEMPTS} attempts")
    budget = _budget(brief, conditions, combined)
    skill_hashes = {}
    for target in combined.targets:
        for skill in getattr(target, "skills", []):
            if output.is_relative_to(Path(skill)):
                raise ValueError("Design output must not modify an input skill directory")
            if skill not in skill_hashes:
                skill_hashes[skill] = directory_digest(skill)
    artifacts = {"brief.json": raw, "experiment.json": _json_bytes(combined.to_dict())}
    for condition in conditions:
        artifacts[condition["experiment"]] = _json_bytes(condition.pop("config"))
    # Validate the actual emitted JSON with the public loader and plan API,
    # rather than assuming our intermediate dataclasses imply valid files.
    with tempfile.TemporaryDirectory(prefix="bedrock-bench-design-") as temp:
        root = Path(temp)
        plans = {}
        for name, content in artifacts.items():
            if name == "brief.json":
                continue
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            plans[name] = load_experiment(path).plan()
    primary = plans["experiment.json"]
    if len({plan["protocol_hash"] for plan in plans.values()}) != 1:
        raise ValueError("Generated conditions have differing task protocols")
    if len({plan["mode"] for plan in plans.values()}) != 1:
        raise ValueError("Generated conditions mix evidence types")
    for condition in conditions:
        condition["plan"] = plans[condition["experiment"]]
    suite = getattr(combined, "suite", "starter")
    task_sources = primary.get("sources")
    if task_sources is None:
        from .tasks import SUITE_REVISION
        task_sources = {"suite_revision": SUITE_REVISION, "scoring_revision": "artifact-v1",
                        "fixture_seed_rule": "seed + repetition - 1", "protocol_hash": primary["protocol_hash"]}
    timeout = (combined.limits.timeout_seconds if isinstance(combined, Experiment)
               else combined.limits.process_timeout_seconds)
    design = {
        "schema_version": 1, "kind": "bedrock-bench-design", "status": "planned",
        "question": brief["question"], "contrast": brief["contrast"], "varied_fields": varied,
        "notes": notes, "suite": suite, "seed": combined.seed, "tasks": combined.tasks,
        "protocol_hash": primary["protocol_hash"], "limits": primary["limits"],
        "counts": {
            "distinct_tasks": len(combined.tasks), "repetitions": combined.repetitions,
            "base_targets": len(base.targets), "conditions": len(conditions),
            "target_condition_cells": len(combined.targets),
            "attempts_per_cell": len(combined.tasks) * combined.repetitions,
            "attempts_per_condition": len(base.targets) * len(combined.tasks) * combined.repetitions,
            "total_attempts": total_attempts, "catalog_tasks": len(task_catalog(suite)),
        },
        "experiment": {"path": "experiment.json", "plan": primary},
        "conditions": conditions, "budget": budget,
        "execution": {
            "executed": False, "primary_experiment": "experiment.json",
            "condition_configs_are_alternatives": True, "concurrency": 1,
            "automatic_trial_retries": 0, "provider_sampling_seed_set": False,
            "summed_controller_timeout_seconds": total_attempts * timeout,
            "schedule": _schedule(combined, conditions),
        },
        "provenance": {
            "brief_path": str(source), "brief_snapshot": "brief.json",
            "brief_sha256": hashlib.sha256(raw).hexdigest(), "plugin_version": __version__,
            "planner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python_version": sys.version.split()[0], "task_sources": task_sources,
            "skill_sha256": skill_hashes,
            "file_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in artifacts.items()},
        },
        "limitations": _limitations(combined, budget),
    }
    artifacts["DESIGN.json"] = _json_bytes(design)
    artifacts["DESIGN.md"] = _markdown(design).encode("utf-8")
    # Exclusive directory creation is the final gate. A concurrent creator wins;
    # we never replace an existing plan, empty directory, symlink, or input file.
    output.mkdir(parents=True, exist_ok=False)
    for name, content in artifacts.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(content)
    return output / "DESIGN.md"
