"""Offline failure inventories from bounded, explicitly recorded public evidence.

This module never imports a runner, executes a trace, or projects configuration
or session prompts. Categories describe observations, not established root causes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import errno
import hashlib
import heapq
import itertools
import json
import math
import os
from pathlib import Path
import re
import stat

MAX_SOURCES = 1_000
MAX_RECORDS = 100_000
MAX_DETAILS = 1_000
MAX_RUN_BYTES = 32 * 1024 * 1024
MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_EVENT_BYTES = 64 * 1024
MAX_EVENTS = 20_000
MAX_SIGNALS = 24
MAX_ERROR_ITEMS = 16
MAX_TEXT_CHARS = 4_096
MAX_LABEL_CHARS = 512

COST_SCOPE = (
    "Recorded agent inference only; infrastructure, subscriptions, external tools, "
    "and grader/judge charges are excluded. Estimates retain their recorded basis."
)
# Keep these permitted changes aligned with library.ACCOUNTING_FIELDS. Explicit
# review selection uses bounded reads here, never the library's source fallback.
ACCOUNTING_FIELDS = frozenset({
    "usage", "usage_granularity", "reported_usage_events", "cost_usd",
    "known_cost_subtotal_usd", "cost_basis", "accounting_complete", "accounting_review",
})
MIXED_COST_FIELDS = (
    "total_cost_usd", "known_cost_subtotal_usd", "failed_attempt_cost_usd",
    "failed_attempt_known_cost_subtotal_usd", "cost_coverage",
    "recorded_cost_coverage", "failed_attempt_cost_coverage",
)
MIXED_COST_REASON = (
    "Combined cost amounts and coverage are withheld because the selected runs have "
    "different evidence kinds. Synthetic demonstrations, reference validation, and "
    "live measurements describe different accounting scopes. Use the separate "
    "partition costs and coverage below."
)
TAXONOMY = {
    "timeout": "Recorded timeout status or a timeout signature in terminal runner evidence.",
    "grader_failure": "Recorded task rejection or grader/verifier failure; this does not prove a grader defect.",
    "runner_error": "Recorded runner failure without a more specific supported signature.",
    "setup_error": "A setup/environment signature in terminal runner evidence.",
    "authentication": "An authentication/authorization signature in terminal runner evidence.",
    "throttling": "A throttling/rate-limit signature in terminal runner evidence.",
    "incomplete_evidence": "An incomplete result, missing scheduled attempt, or unavailable outcome evidence.",
    "unknown": "The available or conflicting evidence does not support a more specific category.",
    "interrupted": "The attempt itself is explicitly recorded as interrupted or cancelled.",
    "success": "The saved attempt explicitly reports success.",
}
WARNING_TEXT = {
    "duplicate_source": "A repeated source or identical copy of a run was counted once.",
    "duplicate_attempt": "An identical repeated attempt ID was counted once.",
    "missing_identity": "Some attempt identity fields were not recorded; no identifier was invented.",
    "unknown_schedule": "No complete schedule or experiment grid was recorded; missing attempts cannot be counted.",
    "unreconciled_schedule": "Some recorded attempts cannot be matched to schedule slots; missing attempts remain unknown.",
    "unexpected_attempt": "An attempt has a slot outside the saved schedule; it was retained.",
    "repeated_slot": "Distinct attempt IDs share a schedule slot; both recorded attempts were retained.",
    "unknown_evidence_kind": "The run does not explicitly identify its evidence kind.",
    "conflicting_evidence_kind": "Synthetic and reference flags conflict; this is a separate evidence partition.",
    "unknown_protocol": "No protocol hash was recorded; this run has its own partition.",
    "inconsistent_outcome": "Outcome fields conflict; the attempt has an unknown outcome.",
    "missing_outcome": "No supported terminal outcome was recorded.",
    "invalid_grading": "Malformed grading evidence was ignored; the attempt was retained.",
    "invalid_errors": "Malformed runner error evidence was ignored; the attempt was retained.",
    "error_limit": "Runner error evidence exceeded its item or text limit.",
    "accounting_inconsistent": "Cost and accounting-complete fields conflict; full cost remains unknown.",
    "mixed_evidence_costs_withheld": MIXED_COST_REASON,
    "evidence_missing": "Recorded evidence is absent.",
    "evidence_unsafe": "An evidence reference is unsafe or is not a regular file; it was not read.",
    "evidence_unreadable": "Recorded evidence could not be read.",
    "evidence_oversized": "Evidence exceeds its byte limit; it was not parsed.",
    "evidence_budget": "The total evidence read budget was exhausted; remaining evidence was not parsed.",
    "evidence_malformed": "Malformed or oversized evidence events were ignored.",
    "evidence_unsupported": "The artifact contains no supported public result/event schema.",
    "checksum_mismatch": "Evidence differs from its recorded checksum; its contents were not used.",
    "invalid_checksum": "The recorded evidence checksum is malformed; its contents were not used.",
    "signal_limit": "Additional public evidence signals were omitted from the detail projection.",
}

# These patterns produce fixed labels, never copies of a matched error message.
# A match is a signature observation, not a causal explanation.
SIGNATURES = (
    ("timeout", "timeout_signature",
     r"\b(?:[A-Za-z]*Timeout(?:Error|Exception)|timed?\s*out|deadline exceeded)\b"),
    ("authentication", "authentication_signature",
     r"\b(?:AccessDenied(?:Exception)?|Unauthorized(?:Exception)?|AuthenticationError|"
     r"InvalidClientTokenId|ExpiredToken(?:Exception)?|UnrecognizedClientException|"
     r"NoCredentialsError|not authorized|invalid (?:api[- ]?key|credentials|token)|"
     r"missing (?:api[- ]?key|credentials)|credentials? (?:expired|not found)|HTTP\s*40[13])\b"),
    ("throttling", "throttling_signature",
     r"\b(?:Throttl(?:ing|ed)(?:Exception)?|TooManyRequests(?:Exception)?|"
     r"RateLimitError|rate[- ]limit(?:ed| exceeded)?|HTTP\s*429)\b"),
    ("setup_error", "setup_signature",
     r"\b(?:SetupError|EnvironmentSetupError|EnvironmentStartError|"
     r"ModuleNotFoundError|ImportError|FileNotFoundError|command not found|"
     r"executable (?:not found|missing)|cannot connect to the docker daemon|"
     r"failed to (?:pull|build) (?:the )?(?:docker )?image)\b"),
    ("grader_failure", "verifier_error_signature",
     r"\b(?:VerifierError|GraderError|Verifier(?:Output|Reward)(?:Error|ParseError)|"
     r"RewardFileNotFoundError|verifier (?:failed|error)|grader (?:failed|error))\b"),
)
SIGNATURES = tuple((category, label, re.compile(pattern, re.I))
                   for category, label, pattern in SIGNATURES)
FAILED_STATUSES = {
    "timeout", "timed_out", "task_failed", "runner_error", "setup_error",
    "auth_error", "authentication_error", "throttled", "throttling",
    "grader_error", "verifier_error", "failed", "error", "incomplete",
}
INTERRUPTED_STATUSES = {"interrupted", "cancelled", "canceled", "aborted"}
OPEN_STATUSES = {"running", "pending", "started", "in_progress", "queued"}
TARGET_FIELDS = (
    "runner", "provider", "model", "region", "reasoning_effort", "service_tier",
    "routing", "agent_version",
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _strict_json(raw):
    def reject(_):
        raise ValueError("Non-finite JSON number")

    def finite(value):
        number = float(value)
        return number if math.isfinite(number) else reject(value)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    try:
        return json.loads(raw, parse_float=finite, parse_constant=reject, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid or non-finite JSON") from exc


def _label(value, field, *, optional=False):
    if value is None and optional:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_LABEL_CHARS
            or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)):
        raise ValueError(f"{field} must be a nonempty string of at most {MAX_LABEL_CHARS} characters")
    return value


def _integer(value, field, *, minimum=1, maximum=MAX_RECORDS):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def _amount(value, field):
    if value is None:
        return None
    try:
        valid = type(value) in (int, float) and value >= 0 and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{field} must be a finite nonnegative number or null")
    return value


class _Public:
    """Redact labels as well as evidence; never project arbitrary trace text."""

    def __init__(self):
        self.secrets = sorted({
            value for key, value in os.environ.items()
            if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", key, re.I) and len(value) >= 8
        }, key=len, reverse=True)

    def __call__(self, value):
        if not isinstance(value, str):
            return value
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", "[REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)((?:api[_-]?key|token|secret|password|credential|"
                       r"x-amz-signature)\s*[=:]\s*)[^\s,;\"'&]+", r"\1[REDACTED]", value)
        return value


def _open_contained(root, path, flags):
    """Pin each directory while opening evidence, so symlink swaps cannot escape."""
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        # Platforms without openat still get the explicit containment/symlink
        # check; the descriptor is checked for regular-file type before reading.
        return os.open(_contained(root, path.relative_to(root).as_posix()), flags)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root, directory_flags)
    try:
        parts = path.relative_to(root).parts
        for part in parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return os.open(parts[-1], flags, dir_fd=fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("Unsafe evidence path") from exc
        raise
    finally:
        os.close(fd)


def _read_regular(path, maximum, *, root=None):
    """Bound reads and reject FIFOs/devices before reading, including races."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags) if root is None else _open_contained(root, path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Not a regular file")
        if info.st_size > maximum:
            raise OverflowError("File exceeds read limit")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            raw = stream.read(maximum + 1)
    finally:
        if fd is not None:
            os.close(fd)
    if len(raw) > maximum:
        raise OverflowError("File exceeds read limit")
    return raw


def _contained(root, relative):
    if (not isinstance(relative, str) or not relative or len(relative) > 4_096
            or "\\" in relative or ":" in relative
            or any(ord(char) < 32 for char in relative)):
        raise ValueError("Unsafe evidence reference")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Unsafe evidence reference")
    candidate = root
    for part in path.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("Symlink evidence is not read")
    candidate = candidate.resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Evidence escapes source directory")
    return candidate


def _validate_run(run):
    if (not isinstance(run, dict) or type(run.get("schema_version")) is not int
            or run["schema_version"] != 1 or not isinstance(run.get("attempts"), list)
            or any(not isinstance(row, dict) for row in run["attempts"])):
        raise ValueError("Expected schema_version=1 run.json with an attempts array of objects")
    if len(run["attempts"]) > MAX_RECORDS:
        raise ValueError("Source exceeds the full-record limit")
    _label(run.get("run_id"), "run_id")
    _label(run.get("status"), "run status", optional=True)
    _label(run.get("protocol_hash"), "protocol_hash", optional=True)
    for key in ("synthetic", "validation_only"):
        if key in run and type(run[key]) is not bool:
            raise ValueError(f"{key} must be a boolean when recorded")


def _review_provenance(entry, remaining_bytes):
    """Prove a selected review's source/outcome integrity, without auditing prices."""
    path, reviewed = entry["source"], entry["run"]
    if "accounting_review" not in reviewed and path.name != "run-accounting-reviewed.json":
        return None, 0
    review = reviewed.get("accounting_review")
    if not isinstance(review, dict):
        raise ValueError("Selected accounting review requires provenance metadata")
    original_name = review.get("original_run")
    if not isinstance(original_name, str) or Path(original_name).name != original_name:
        raise ValueError("Accounting review original_run must name a file beside the selected source")
    try:
        original = _contained(path.parent, original_name)
        audit = _contained(path.parent, review.get("source"))
        if original == path:
            raise ValueError("Accounting review cannot be its own original")
        raw_original = _read_regular(
            original, min(MAX_RUN_BYTES, remaining_bytes), root=path.parent)
        original_sha256 = hashlib.sha256(raw_original).hexdigest()
        if review.get("original_sha256") != original_sha256:
            raise ValueError("Accounting review original checksum does not match")
        before = _strict_json(raw_original)
        _validate_run(before)
        unchanged_run = lambda run: {key: value for key, value in run.items()
                                     if key not in {"name", "attempts", "accounting_review"}}
        # Preserve the library's allowed fields while distinguishing JSON
        # booleans from numbers (Python equality alone treats False as zero).
        if (_digest(unchanged_run(before)) != _digest(unchanged_run(reviewed))
                or len(before["attempts"]) != len(reviewed["attempts"])):
            raise ValueError("Accounting review changes run metadata, protocol, or attempt count")
        for original_row, reviewed_row in zip(before["attempts"], reviewed["attempts"]):
            if (_digest({key: value for key, value in original_row.items() if key not in ACCOUNTING_FIELDS})
                    != _digest({key: value for key, value in reviewed_row.items() if key not in ACCOUNTING_FIELDS})):
                raise ValueError("Accounting review changes an outcome, target, timing, or evidence path")
        raw_audit = _read_regular(
            audit, min(MAX_EVIDENCE_BYTES, remaining_bytes - len(raw_original)), root=path.parent)
    except (OSError, OverflowError, RuntimeError) as exc:
        raise ValueError("Accounting review proof is missing, unsafe, unreadable, or exceeds its read limit") from exc
    return {
        "status": "provenance_verified",
        "original_path": str(original), "original_sha256": original_sha256,
        "original_bytes": len(raw_original), "audit_path": str(audit),
        "audit_sha256": hashlib.sha256(raw_audit).hexdigest(), "audit_bytes": len(raw_audit),
        "pricing_audited": False,
        "scope": "Original checksum, unchanged non-accounting fields, and presence of the cited audit source only.",
    }, len(raw_original) + len(raw_audit)


def _load_sources(sources):
    if isinstance(sources, (str, os.PathLike)):
        sources = [sources]
    try:
        paths = list(itertools.islice(iter(sources), MAX_SOURCES + 1))
    except TypeError as exc:
        raise ValueError("sources must contain saved run paths") from exc
    if not paths or len(paths) > MAX_SOURCES:
        raise ValueError(f"Provide 1–{MAX_SOURCES} saved run sources")
    loaded, by_id, by_path, duplicates = [], {}, {}, []
    total_bytes = 0
    for value in paths:
        if not isinstance(value, (str, os.PathLike)):
            raise ValueError("Each source must be a saved run file or run directory")
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            path = (path / "run.json").resolve()
        if path in by_path:
            original = by_path[path]
            duplicates.append({"source": str(path), "sha256": original["sha256"],
                               "run_id": original["run"]["run_id"],
                               "accounting_review": original["accounting_review"]})
            continue
        try:
            raw = _read_regular(path, min(MAX_RUN_BYTES, MAX_SOURCE_BYTES - total_bytes))
            run = _strict_json(raw)
        except (OSError, ValueError, OverflowError) as exc:
            raise ValueError("Source is unreadable, oversized, or not valid finite JSON") from exc
        total_bytes += len(raw)
        _validate_run(run)
        entry = {"source": path, "run": run, "sha256": hashlib.sha256(raw).hexdigest()}
        entry["accounting_review"], proof_bytes = _review_provenance(entry, MAX_SOURCE_BYTES - total_bytes)
        total_bytes += proof_bytes
        if run["run_id"] in by_id:
            if by_id[run["run_id"]]["run"] != run:
                raise ValueError("Conflicting copies of the same run ID; choose one source explicitly")
            duplicates.append({"source": str(path), "sha256": entry["sha256"], "run_id": run["run_id"],
                               "accounting_review": entry["accounting_review"]})
        else:
            by_id[run["run_id"]] = entry
            loaded.append(entry)
        by_path[path] = entry
    return loaded, duplicates, total_bytes


def _target(row):
    target = row.get("target")
    if target is None:
        target = {}
    if not isinstance(target, dict):
        raise ValueError("attempt.target must be an object")
    _label(target.get("id"), "target.id", optional=True)
    for key in TARGET_FIELDS:
        if key != "routing":
            _label(target.get(key), f"target.{key}", optional=True)
    routing = target.get("routing")
    if routing is not None:
        if not isinstance(routing, list) or len(routing) > 32:
            raise ValueError("target.routing must be an array of at most 32 provider labels")
        for provider in routing:
            _label(provider, "target.routing provider")
    version = _label(row.get("runner_version"), "runner_version", optional=True)
    identity = {key: value for key, value in target.items() if key != "id"}
    identity["runner_version"] = version
    return {
        "model_key": _digest(identity),
        "target_id": target.get("id"),
        "target": {**{key: target.get(key) for key in TARGET_FIELDS}, "runner_version": version},
    }


def _slot(row):
    target = row.get("target") or {}
    values = (target.get("id"), row.get("task_id"), row.get("repetition"))
    return values if all(value is not None for value in values) else None


def _schedule(run):
    """Return declared slots only. A missing slot has no invented attempt ID."""
    experiment = run.get("experiment")
    if experiment is None:
        experiment = {}
    if not isinstance(experiment, dict):
        raise ValueError("experiment must be an object")
    targets = experiment.get("targets")
    identities = {}
    if targets is not None:
        if not isinstance(targets, list) or any(not isinstance(target, dict) for target in targets):
            raise ValueError("experiment.targets must be an array of objects")
        for target in targets:
            name = _label(target.get("id"), "experiment target ID")
            if name in identities:
                raise ValueError("Duplicate experiment target ID")
            _target({"target": target})
            identities[name] = target
    if "schedule" in run:
        schedule = run["schedule"]
        if not isinstance(schedule, list) or len(schedule) > MAX_RECORDS:
            raise ValueError("schedule must be a bounded array")
        slots = []
        for index, item in enumerate(schedule):
            if not isinstance(item, dict):
                raise ValueError("Each schedule entry must be an object")
            target = _label(item.get("target"), "schedule target")
            task = _label(item.get("task"), "schedule task")
            repetition = _integer(item.get("repetition"), "schedule repetition")
            slots.append({
                "target": identities.get(target, {"id": target}), "task_id": task,
                "repetition": repetition, "schedule_index": index,
                "attempt_id": _label(item.get("attempt_id"), "schedule attempt_id", optional=True),
            })
        basis = "schedule"
    elif all(key in experiment for key in ("targets", "tasks", "repetitions")):
        tasks = experiment["tasks"]
        if not isinstance(tasks, list):
            raise ValueError("experiment.tasks must be an array")
        for task in tasks:
            _label(task, "experiment task")
        if len(set(tasks)) != len(tasks):
            raise ValueError("Duplicate experiment task")
        repetitions = _integer(experiment["repetitions"], "experiment repetitions")
        if len(identities) * len(tasks) * repetitions > MAX_RECORDS:
            raise ValueError("Expected attempt count exceeds the record limit")
        slots = [{"target": target, "task_id": task, "repetition": rep, "attempt_id": None}
                 for rep in range(1, repetitions + 1)
                 for task in tasks for target in identities.values()]
        basis = "experiment_grid"
    else:
        return None, "unknown"
    if len({_slot(row) for row in slots}) != len(slots):
        raise ValueError("Duplicate scheduled slot")
    return slots, basis


def _accounting(row, warnings):
    cost = _amount(row.get("cost_usd"), "cost_usd")
    known = _amount(row.get("known_cost_subtotal_usd"), "known_cost_subtotal_usd")
    complete = row.get("accounting_complete")
    if complete is not None and type(complete) is not bool:
        raise ValueError("accounting_complete must be a boolean or null")
    basis = row.get("cost_basis", [])
    if not isinstance(basis, list) or len(basis) > 32:
        raise ValueError("cost_basis must be an array of at most 32 labels")
    for item in basis:
        _label(item, "cost_basis")
    if cost is not None and known is not None and known > cost and not math.isclose(known, cost):
        raise ValueError("Known cost subtotal exceeds recorded full cost")
    if known is None:
        known = cost if cost is not None else 0
    if (cost is None and complete is True) or (cost is not None and complete is False):
        warnings.add("accounting_inconsistent")
        cost = None
    return {
        "cost_usd": cost, "known_cost_subtotal_usd": known,
        "accounting_complete": cost is not None,
        "cost_basis": sorted(set(basis)) or ["unknown"],
    }


def _outcome(row, warnings):
    status, success = row.get("status"), row.get("success")
    if success is not None and type(success) is not bool:
        raise ValueError("success must be a boolean or null")
    grading = row.get("grading")
    if success is True and isinstance(grading, dict) and grading.get("success") is False:
        warnings.add("inconsistent_outcome")
        return "unknown"
    if status in INTERRUPTED_STATUSES and success is not True:
        return "interrupted"
    if ((success is True and status in FAILED_STATUSES | INTERRUPTED_STATUSES | OPEN_STATUSES)
            or (success is False and status in OPEN_STATUSES)):
        warnings.add("inconsistent_outcome")
        return "unknown"
    if success is True:
        return "success"
    if status in OPEN_STATUSES:
        warnings.add("missing_outcome")
        return "unknown"
    if success is False or status in FAILED_STATUSES:
        return "failure"
    warnings.add("missing_outcome")
    return "unknown"


def _signal(category, signature, source, field, *, confidence="medium", scope="terminal", line=None):
    result = {"category": category, "signature": signature, "source": source, "field": field,
              "confidence": confidence, "scope": scope}
    if line is not None:
        result["line"] = line
    return result


def _error_signals(value, source, field, warnings, *, scope="terminal", line=None):
    # Only allowlisted scalar error fields are inspected. No serialized dicts,
    # stack traces, tool arguments, prompts, or nested response payloads.
    if isinstance(value, dict):
        values = [(f"{field}.{key}", value[key]) for key in (
            "code", "type", "name", "message", "exception_type", "exception_message"
        ) if key in value]
    else:
        values = [(field, value)]
    signals = []
    for pointer, text in values:
        if not isinstance(text, str):
            warnings.add("invalid_errors")
            continue
        if len(text) > MAX_TEXT_CHARS:
            warnings.add("error_limit")
        for category, signature, pattern in SIGNATURES:
            if pattern.search(text[:MAX_TEXT_CHARS]):
                signals.append(_signal(category, signature, source, pointer, scope=scope, line=line))
    return signals


def _row_signals(row, source, pointer, warnings):
    signals = []
    errors = row.get("errors", [])
    if not isinstance(errors, list):
        warnings.add("invalid_errors")
    else:
        if len(errors) > MAX_ERROR_ITEMS:
            warnings.add("error_limit")
        for index, error in enumerate(errors[:MAX_ERROR_ITEMS]):
            signals.extend(_error_signals(error, source, f"{pointer}/errors/{index}", warnings))
    grading = row.get("grading")
    if grading is not None:
        if (not isinstance(grading, dict) or
                (grading.get("success") is not None and type(grading["success"]) is not bool)):
            warnings.add("invalid_grading")
        elif grading.get("success") is False:
            signals.append(_signal("grader_failure", "grader_reported_rejection", source,
                                   f"{pointer}/grading/success", confidence="high", scope="grader"))
    return signals


def _event_signals(event, source, line, warnings):
    kind = event.get("type")
    if not isinstance(kind, str):
        raise ValueError("An event needs a string type")
    if kind in {"error", "turn.failed"}:
        field = "error" if "error" in event else "message"
        return _error_signals(event.get(field), source, f"/{field}", warnings, line=line), True
    if kind == "tool":
        result = event.get("result")
        if isinstance(result, dict) and "error" in result:
            signals = _error_signals(result["error"], source, "/result/error", warnings,
                                     scope="tool", line=line)
            return signals or [_signal("runner_error", "tool_reported_error", source,
                                       "/result/error", scope="tool", line=line)], True
        return [], True
    if kind == "item.completed":
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise ValueError("An item needs a string type")
        if item["type"] in {"command_execution", "mcp_tool_call"}:
            code = item.get("exit_code")
            if item.get("status") == "failed" or (type(code) is int and code != 0):
                return [_signal("runner_error", "tool_reported_error", source, "/item",
                                scope="tool", line=line)], True
        return [], True
    if kind == "response_item":
        payload = event.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            raise ValueError("A response item needs a string type")
        # Deliberately ignore all message/reasoning/configuration payloads.
        if payload.get("type") in {"function_call_output", "custom_tool_call_output"}:
            output = payload.get("output")
            if isinstance(output, str):
                match = re.search(r"(?:Process exited with code|Exit code:)\s*(-?\d+)",
                                  output[:MAX_TEXT_CHARS])
                if match and match[1].lstrip("-").strip("0"):
                    return [_signal("runner_error", "tool_nonzero_exit", source, "/payload/output",
                                    scope="tool", line=line)], True
            return [], True
        return [], payload.get("type") in {"function_call", "custom_tool_call"}
    return [], kind in {
        "step", "completed", "turn.completed", "turn.started", "thread.started",
        "step_finish", "tool_use", "demo", "synthetic_usage",
    }


def _parse_evidence(raw, path, warnings):
    signals, malformed, recognized = [], 0, 0
    if path.suffix.lower() == ".jsonl":
        # splitlines is bounded by MAX_EVIDENCE_BYTES; individual events are
        # bounded separately to avoid parsing a giant embedded prompt.
        lines = raw.splitlines()
        malformed += max(0, len(lines) - MAX_EVENTS)
        for line_number, line in enumerate(lines[:MAX_EVENTS], 1):
            if not line.strip():
                continue
            try:
                if len(line) > MAX_EVENT_BYTES:
                    raise ValueError("Oversized event")
                event = _strict_json(line)
                if not isinstance(event, dict):
                    raise ValueError("Non-object event")
                found, supported = _event_signals(event, str(path), line_number, warnings)
                recognized += int(supported)
                remaining = MAX_SIGNALS - len(signals)
                if len(found) > remaining:
                    warnings.add("signal_limit")
                signals.extend(found[:remaining])
            except (ValueError, TypeError, AttributeError, RecursionError):
                malformed += 1
    elif path.suffix.lower() == ".json":
        try:
            trial = _strict_json(raw)
            if not isinstance(trial, dict):
                raise ValueError("Non-object result")
            if "exception_info" in trial:
                recognized += 1
                exception = trial["exception_info"]
                if exception is not None:
                    if not isinstance(exception, dict):
                        raise ValueError("Malformed exception")
                    signals.extend(_error_signals(exception, str(path), "/exception_info", warnings))
            if "verifier_result" in trial:
                recognized += 1
                verifier = trial["verifier_result"]
                if verifier is not None:
                    if not isinstance(verifier, dict):
                        raise ValueError("Malformed verifier")
                    rewards = verifier.get("rewards")
                    if rewards is not None:
                        if not isinstance(rewards, dict):
                            raise ValueError("Malformed rewards")
                        reward = rewards.get("reward")
                        if reward is not None:
                            if type(reward) not in (int, float):
                                raise ValueError("Non-numeric reward")
                            if reward != 1:
                                signals.append(_signal(
                                    "grader_failure", "verifier_reported_rejection", str(path),
                                    "/verifier_result/rewards/reward", confidence="high", scope="grader"))
            for key in ("agent_execution", "agent_result"):
                if key in trial:
                    recognized += 1
                    if trial[key] is not None and not isinstance(trial[key], dict):
                        raise ValueError("Malformed agent result/phase")
        except (ValueError, TypeError, AttributeError, RecursionError):
            malformed += 1
    if malformed:
        warnings.add("evidence_malformed")
    if not recognized:
        warnings.add("evidence_unsupported")
    return signals, {"recognized_events": recognized, "malformed_events": malformed}


def _evidence(row, root, budget, warnings):
    references = [("trace", row.get("trace"))]
    upstream = row.get("upstream")
    if upstream is not None:
        if not isinstance(upstream, dict):
            warnings.add("evidence_malformed")
        elif upstream.get("result") is not None:
            attempt_id, result = row.get("attempt_id"), upstream["result"]
            try:
                if (not isinstance(attempt_id, str) or "/" in attempt_id
                        or not isinstance(result, str)):
                    raise ValueError("Invalid upstream reference")
                # Validate the recorded relative part before adding its prefix.
                _contained(root, result)
            except (OSError, ValueError, RuntimeError):
                warnings.add("evidence_unsafe")
            else:
                references.append(("upstream_result", f"attempts/{attempt_id}/{result}"))
    evidence, signals, seen = [], [], {}
    for kind, relative in references:
        item = {"kind": kind, "relative_path": relative
                if isinstance(relative, str) and len(relative) <= MAX_TEXT_CHARS else None}
        if relative is None:
            item["status"] = "missing"
            warnings.add("evidence_missing")
            evidence.append(item)
            continue
        try:
            path = _contained(root, relative)
        except (OSError, ValueError, RuntimeError):
            item["status"] = "unsafe"
            warnings.add("evidence_unsafe")
            evidence.append(item)
            continue
        if path in seen:
            seen[path]["kinds"].append(kind)
            continue
        item.update(path=str(path), kinds=[kind])
        seen[path] = item
        evidence.append(item)
        try:
            if budget["remaining"] <= 0:
                item["status"] = "budget_exceeded"
                warnings.add("evidence_budget")
                continue
            maximum = min(MAX_EVIDENCE_BYTES, budget["remaining"])
            try:
                raw = _read_regular(path, maximum, root=root)
            except OverflowError:
                item["status"] = "too_large" if maximum == MAX_EVIDENCE_BYTES else "budget_exceeded"
                warnings.add("evidence_oversized" if maximum == MAX_EVIDENCE_BYTES else "evidence_budget")
                continue
            budget["remaining"] -= len(raw)
            item.update(status="available", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            expected = row.get("trace_sha256") if kind == "trace" else None
            if expected is not None:
                if not isinstance(expected, str) or re.fullmatch(r"[0-9a-fA-F]{64}", expected) is None:
                    item["status"] = "invalid_checksum"
                    warnings.add("invalid_checksum")
                    continue
                item["recorded_sha256"] = expected
                if item["sha256"] != expected.lower():
                    item["status"] = "checksum_mismatch"
                    warnings.add("checksum_mismatch")
                    continue
            found, stats = _parse_evidence(raw, path, warnings)
            item.update(stats)
            if stats["malformed_events"]:
                item["status"] = "malformed"
            elif not stats["recognized_events"]:
                item["status"] = "unsupported"
            signals.extend(found)
        except FileNotFoundError:
            item["status"] = "missing"
            warnings.add("evidence_missing")
        except ValueError:
            item["status"] = "unsafe"
            warnings.add("evidence_unsafe")
        except OSError:
            item["status"] = "unreadable"
            warnings.add("evidence_unreadable")
    return evidence, signals


def _classification(row, outcome, signals, warnings, source, pointer):
    status = row.get("status")
    direct = {
        "timeout": "timeout", "timed_out": "timeout", "task_failed": "grader_failure",
        "setup_error": "setup_error", "auth_error": "authentication",
        "authentication_error": "authentication", "throttled": "throttling",
        "throttling": "throttling", "grader_error": "grader_failure",
        "verifier_error": "grader_failure", "incomplete": "incomplete_evidence",
    }
    if outcome in {"success", "interrupted"}:
        return _signal(outcome, "recorded_outcome", source, pointer, confidence="high", scope="outcome")
    if outcome == "missing":
        return _signal("incomplete_evidence", "scheduled_attempt_not_recorded", source, pointer,
                       confidence="high", scope="schedule")
    if "inconsistent_outcome" in warnings:
        return _signal("unknown", "conflicting_outcomes", source, pointer, confidence="low")
    if outcome == "unknown":
        return _signal("incomplete_evidence", "terminal_outcome_not_recorded", source, pointer,
                       confidence="low")
    if status in direct:
        return _signal(direct[status], "recorded_status", source, f"{pointer}/status", confidence="high")
    if status in {"runner_error", "error", "failed"}:
        # Tool-scope and grader-scope errors cannot explain a runner failure.
        terminal = [signal for signal in signals if signal["scope"] == "terminal"]
        if terminal:
            return terminal[0]
        if status == "runner_error":
            return _signal("runner_error", "recorded_status", source, f"{pointer}/status", confidence="high")
    if status in {"completed", "task_failed"}:
        grader = next((signal for signal in signals if signal["scope"] == "grader"), None)
        if grader is not None:
            return grader
    return _signal("unknown", "no_supported_failure_signature", source, pointer, confidence="low")


def _sum(values):
    try:
        result = math.fsum(values)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Aggregate cost is not finite") from exc
    if not math.isfinite(result):
        raise ValueError("Aggregate cost is not finite")
    return result


def _aggregate(rows, *, include_costs=True):
    counts = Counter(row["outcome"] for row in rows)
    attempts = len(rows)
    recorded = attempts - counts["missing"]
    known_costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
    failures = [row for row in rows if row["outcome"] == "failure"]
    failed_costs = [row["cost_usd"] for row in failures if row["cost_usd"] is not None]
    return {
        "attempts": attempts, "recorded_attempts": recorded, "successes": counts["success"],
        "failures": counts["failure"], "interrupted_attempts": counts["interrupted"],
        "missing_attempts": counts["missing"], "unknown_outcomes": counts["unknown"],
        "known_cost_attempts": len(known_costs), "unknown_cost_attempts": attempts - len(known_costs),
        "cost_coverage": len(known_costs) / attempts if attempts and include_costs else None,
        "recorded_cost_coverage": len(known_costs) / recorded if recorded and include_costs else None,
        "total_cost_usd": _sum(known_costs) if include_costs and len(known_costs) == attempts else None,
        "known_cost_subtotal_usd": _sum(row["known_cost_subtotal_usd"] for row in rows) if include_costs else None,
        "failed_attempt_cost_usd": _sum(failed_costs) if include_costs and len(failed_costs) == len(failures) else None,
        "failed_attempt_known_cost_subtotal_usd": _sum(
            row["known_cost_subtotal_usd"] for row in failures) if include_costs else None,
        "failed_attempt_unknown_costs": len(failures) - len(failed_costs),
        "failed_attempt_cost_coverage": len(failed_costs) / len(failures) if failures and include_costs else None,
        "cost_basis": sorted({basis for row in rows for basis in row["cost_basis"]}),
    }


def _group(rows, fields):
    grouped = defaultdict(list)
    for row in rows:
        grouped[tuple(row[field] for field in fields)].append(row)
    return [{**dict(zip(fields, key)), **_aggregate(values)}
            for key, values in sorted(grouped.items(), key=lambda item: str(item[0]))]


def _build(sources, *, limit):
    _integer(limit, "limit", minimum=0, maximum=MAX_DETAILS)
    loaded, duplicates, source_bytes = _load_sources(sources)
    public = _Public()
    records, candidates, runs, partitions, models = [], [], [], {}, {}
    protected = {entry["source"] for entry in loaded}
    protected.update(Path(row["source"]) for row in duplicates)
    for entry in loaded + duplicates:
        review = entry["accounting_review"]
        if review:
            protected.update((Path(review["original_path"]), Path(review["audit_path"])))
    budget = {"remaining": MAX_TOTAL_EVIDENCE_BYTES}
    for entry in loaded:
        run, source = entry["run"], str(entry["source"])
        run_warnings = set()
        kind = ("conflicting" if run.get("synthetic") and run.get("validation_only") else
                "synthetic" if run.get("synthetic") else
                "reference" if run.get("validation_only") else
                "live" if run.get("synthetic") is False else "unknown")
        if kind in {"unknown", "conflicting"}:
            run_warnings.add(f"{kind}_evidence_kind")
        protocol = run.get("protocol_hash")
        if protocol is None:
            run_warnings.add("unknown_protocol")
        experiment = run.get("experiment")
        if experiment is None:
            experiment = {}
        if not isinstance(experiment, dict):
            raise ValueError("experiment must be an object")
        suite = _label(run.get("suite", experiment.get("suite", "starter")), "suite")
        partition_id = _digest([kind, protocol, suite, run["run_id"] if protocol is None else None])
        partitions.setdefault(partition_id, {
            "partition_id": partition_id, "evidence_kind": kind, "protocol_hash": protocol,
            "suite": suite, "run_ids": [],
        })["run_ids"].append(run["run_id"])
        context = {
            "run_id": run["run_id"], "source": source, "source_sha256": entry["sha256"],
            "partition_id": partition_id, "evidence_kind": kind, "protocol_hash": protocol, "suite": suite,
        }
        unique, seen_ids = [], {}
        for index, row in enumerate(run["attempts"]):
            _label(row.get("attempt_id"), "attempt_id", optional=True)
            _label(row.get("task_id"), "task_id", optional=True)
            _label(row.get("status"), "attempt status", optional=True)
            _target(row)
            if row.get("repetition") is not None:
                _integer(row["repetition"], "attempt repetition")
            attempt_id = row.get("attempt_id")
            if attempt_id is not None and attempt_id in seen_ids:
                if seen_ids[attempt_id] != row:
                    raise ValueError("Conflicting duplicate attempt ID")
                run_warnings.add("duplicate_attempt")
                continue
            if attempt_id is not None:
                seen_ids[attempt_id] = row
            unique.append((index, row))
        slots, schedule_basis = _schedule(run)
        missing_known = slots is not None and all(_slot(row) is not None for _, row in unique)
        if slots is None:
            run_warnings.add("unknown_schedule")
        elif not missing_known:
            run_warnings.add("unreconciled_schedule")
        missing = []
        if missing_known:
            recorded_slots = Counter(_slot(row) for _, row in unique)
            planned_slots = {_slot(slot) for slot in slots}
            if any(count > 1 for count in recorded_slots.values()):
                run_warnings.add("repeated_slot")
            if set(recorded_slots) - planned_slots:
                run_warnings.add("unexpected_attempt")
            missing = [slot for slot in slots if _slot(slot) not in recorded_slots]
        if len(records) + len(unique) + len(missing) > MAX_RECORDS:
            raise ValueError("Inventory exceeds the full-record limit; select fewer sources")
        run_records = []
        work = [(index, row, False) for index, row in unique]
        work.extend((index, row, True) for index, row in enumerate(missing))
        for index, row, absent in work:
            warnings = set()
            pointer = ((f"/schedule/{row['schedule_index']}" if schedule_basis == "schedule"
                        else "/experiment") if absent else f"/attempts/{index}")
            identity = _target(row)
            models.setdefault(identity["model_key"], {"model_key": identity["model_key"], **identity["target"]})
            if not absent and (row.get("attempt_id") is None or _slot(row) is None):
                warnings.add("missing_identity")
            outcome = "missing" if absent else _outcome(row, warnings)
            accounting = (_accounting({}, warnings) if absent else _accounting(row, warnings))
            signals = [] if absent else _row_signals(row, source, pointer, warnings)
            evidence, trace_signals = ([], []) if absent else _evidence(row, entry["source"].parent, budget, warnings)
            protected.update(Path(item["path"]) for item in evidence if "path" in item)
            signals.extend(trace_signals)
            selected = _classification(row, outcome, signals, warnings, source, pointer)
            if len(signals) > MAX_SIGNALS:
                warnings.add("signal_limit")
            key = _digest([run["run_id"], "missing" if absent else "recorded",
                           _slot(row) if absent else row.get("attempt_id") or index])
            record = {
                **context, "key": key, "attempt_id": row.get("attempt_id"),
                "attempt_index": None if absent else index,
                "task_id": row.get("task_id"), "repetition": row.get("repetition"),
                "target_id": identity["target_id"], "model_key": identity["model_key"],
                "model": identity["target"]["model"], "runner": identity["target"]["runner"],
                "provider": identity["target"]["provider"],
                "status": "not_recorded" if absent else row.get("status"),
                "outcome": outcome, "category": selected["category"],
                "confidence": selected["confidence"], **accounting,
                "warning_codes": sorted(warnings),
            }
            records.append(record)
            run_records.append(record)
            # Keep only bounded details in memory, preferring failures, then
            # interruptions/unknowns/missing slots, and finally successes.
            priority = {"failure": 0, "interrupted": 1, "unknown": 2, "missing": 3, "success": 4}[outcome]
            if limit:
                candidate = (-priority, -len(records), {
                    "key": key, **context, "attempt_id": row.get("attempt_id"),
                    "attempt_index": record["attempt_index"], "task_id": row.get("task_id"),
                    "repetition": row.get("repetition"), "target_id": identity["target_id"],
                    "classification": selected, "signals": signals[:MAX_SIGNALS],
                    "evidence": evidence, "warning_codes": sorted(warnings),
                })
                if len(candidates) < limit:
                    heapq.heappush(candidates, candidate)
                elif candidate[:2] > candidates[0][:2]:
                    heapq.heapreplace(candidates, candidate)
        runs.append({
            **context, "status": run.get("status"),
            "accounting_review": entry["accounting_review"],
            "synthetic": run.get("synthetic"), "validation_only": run.get("validation_only", False),
            "expected_attempts": len(slots) if slots is not None else None,
            "schedule_basis": schedule_basis, "missing_attempts_known": missing_known,
            "warning_codes": sorted(run_warnings), "counts": _aggregate(run_records),
        })
    partition_rows = _group(records, ("partition_id",))
    partition_totals = {row["partition_id"]: row for row in partition_rows}
    for partition in partitions.values():
        partition["counts"] = {key: value for key, value in partition_totals.get(
            partition["partition_id"], _aggregate([])).items() if key != "partition_id"}
    warnings = Counter(code for row in records for code in row["warning_codes"])
    warnings.update(code for run in runs for code in run["warning_codes"])
    if duplicates:
        warnings["duplicate_source"] += len(duplicates)
    evidence_kinds = sorted({run["evidence_kind"] for run in runs})
    mixed_costs = len(evidence_kinds) > 1
    if mixed_costs:
        warnings["mixed_evidence_costs_withheld"] = 1
    result = {
        "schema_version": 1, "artifact": "bedrock-bench-diagnosis",
        "scope": "Offline descriptive inventory; categories are observable signatures, not proven causes or model rankings.",
        "cost_scope": COST_SCOPE, "taxonomy": TAXONOMY,
        "counts": _aggregate(records, include_costs=not mixed_costs),
        "cost_aggregation": {
            "status": "withheld" if mixed_costs else "same_evidence_kind",
            "evidence_kinds": evidence_kinds, "reason": MIXED_COST_REASON if mixed_costs else None,
            "withheld_fields": list(MIXED_COST_FIELDS) if mixed_costs else [],
        },
        "coverage": {
            "runs": len(runs), "runs_with_unknown_missing_attempts": sum(not run["missing_attempts_known"] for run in runs),
            "details_included": len(candidates), "details_omitted": len(records) - len(candidates),
            "details_limit": limit, "source_bytes": source_bytes,
            "max_review_audit_bytes": MAX_EVIDENCE_BYTES,
            "evidence_bytes_read": MAX_TOTAL_EVIDENCE_BYTES - budget["remaining"],
            "max_evidence_bytes_per_file": MAX_EVIDENCE_BYTES,
            "max_total_evidence_bytes": MAX_TOTAL_EVIDENCE_BYTES,
        },
        "runs": runs, "partitions": list(partitions.values()), "models": list(models.values()),
        "records": records, "details": [item[2] for item in sorted(candidates, key=lambda item: item[:2], reverse=True)],
        "by_category": _group(records, ("partition_id", "category")),
        "by_model": _group(records, ("partition_id", "model_key")),
        "by_task": _group(records, ("partition_id", "task_id")),
        "cells": _group(records, ("partition_id", "model_key", "task_id", "category")),
        "duplicate_sources": duplicates,
        "warnings": [{"code": code, "count": count, "message": WARNING_TEXT[code]}
                     for code, count in sorted(warnings.items())],
    }

    def sanitize(value):
        if isinstance(value, str):
            return public(value)
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        return value

    return sanitize(result), protected


def diagnosis_data(sources, *, limit=100):
    """Build an inventory. ``limit`` bounds details, never records or counts.

    Sources may be one path or an iterable of run JSON files/run directories.
    Conflicting duplicate IDs, invalid schemas/accounting, and capacity overflow
    raise ValueError. Missing/malformed/unsafe traces instead produce warnings.
    """
    return _build(sources, limit=limit)[0]


def _cell(value):
    text = "unknown" if value is None else str(value)
    # Escape Markdown/HTML so hostile model/task IDs stay inert text.
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")
            .replace("[", "\\[").replace("]", "\\]").replace("\n", " ").replace("\r", " "))


def _money(value):
    return "unknown" if value is None else f"${value:.6f}"


def _percent(value):
    return "unavailable" if value is None else f"{value:.1%}"


def _count_table(rows, label):
    lines = [
        f"| {label} | Recorded | Success | Failure | Interrupted | Missing | Unknown outcome | Cost coverage | Known spend | Failure spend (known) | Unknown costs |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in rows:
        lines.append(
            f"| {_cell(name)} | {row['recorded_attempts']} | {row['successes']} | {row['failures']} | "
            f"{row['interrupted_attempts']} | {row['missing_attempts']} | {row['unknown_outcomes']} | "
            f"{_percent(row['cost_coverage'])} | {_money(row['known_cost_subtotal_usd'])} | "
            f"{_money(row['failed_attempt_known_cost_subtotal_usd'])} | "
            f"{row['unknown_cost_attempts']} |")
    return lines


def markdown(data):
    counts = data["counts"]
    if data["cost_aggregation"]["status"] == "withheld":
        cost_lines = [
            data["cost_aggregation"]["reason"], "",
            f"{counts['known_cost_attempts']} attempts have a recorded complete cost; "
            f"{counts['unknown_cost_attempts']} attempt costs are unknown "
            f"({counts['failed_attempt_unknown_costs']} among failures). "
            "These are population counts, not a combined spend or coverage estimate.",
        ]
    else:
        cost_lines = [
            f"Full inference cost: {_money(counts['total_cost_usd'])}. "
            f"Known subtotal: {_money(counts['known_cost_subtotal_usd'])}. "
            f"Failed-attempt cost: {_money(counts['failed_attempt_cost_usd'])}; "
            f"known failed-attempt subtotal: {_money(counts['failed_attempt_known_cost_subtotal_usd'])}. "
            f"{counts['unknown_cost_attempts']} attempt costs are unknown "
            f"({counts['failed_attempt_unknown_costs']} among failures).",
            "",
            f"Cost coverage: {_percent(counts['cost_coverage'])} of all identified slots; "
            f"{_percent(counts['recorded_cost_coverage'])} of recorded attempts; "
            f"{_percent(counts['failed_attempt_cost_coverage'])} of failed attempts.",
        ]
    lines = [
        "# Bedrock Bench failure diagnosis", "", data["scope"], "", data["cost_scope"], "",
        f"{counts['recorded_attempts']} recorded attempts; {counts['successes']} successes, "
        f"{counts['failures']} failures, {counts['interrupted_attempts']} interrupted attempts, "
        f"{counts['missing_attempts']} missing scheduled attempts, and {counts['unknown_outcomes']} unknown outcomes.",
        "",
        *cost_lines,
        "",
        "Cost coverage counts complete costs over all recorded plus identified missing attempts; "
        "recorded-only coverage is also retained in JSON. Unknown costs are not zero. "
        "Missing attempts are unobserved schedule slots, not demonstrated task failures. "
        "Interrupted run status does not change already recorded attempt outcomes.",
        "",
        f"Details: {data['coverage']['details_included']} included, {data['coverage']['details_omitted']} omitted. "
        "All recorded attempts and identified missing slots remain in the counts and compact records. "
        f"Missing-attempt counts are unavailable for {data['coverage']['runs_with_unknown_missing_attempts']} runs.",
        "", "## Sources", "",
    ]
    for run in data["runs"]:
        lines.extend([
            f"- Run **{_cell(run['run_id'])}**: {_cell(run['source'])}; "
            f"SHA-256 `{run['source_sha256']}`; status {_cell(run['status'])}; "
            f"evidence {_cell(run['evidence_kind'])}; expected attempts {_cell(run['expected_attempts'])}.",
        ])
        review = run.get("accounting_review")
        if review:
            lines.extend([
                f"  Accounting-review provenance verified against original {_cell(review['original_path'])}; "
                f"SHA-256 `{review['original_sha256']}`.",
                f"  Cited audit source: {_cell(review['audit_path'])}; SHA-256 `{review['audit_sha256']}`. "
                "This validates provenance and unchanged non-accounting fields; it does not audit pricing.",
            ])
    model_labels = {
        row["model_key"]: " / ".join(str(row.get(key) or "unknown") for key in (
            "runner", "provider", "model", "runner_version"))
        for row in data["models"]
    }
    for partition in data["partitions"]:
        pid = partition["partition_id"]
        lines += [
            "", f"## {_cell(partition['evidence_kind'])} / {_cell(partition['suite'])} / protocol {_cell(partition['protocol_hash'])}",
            "", "This partition is a descriptive inventory. Matching labels alone do not establish comparable experiments.", "",
        ]
        lines += _count_table([("Partition total", partition["counts"])], "Scope")
        lines += ["", "### Categories", ""]
        lines += _count_table([(row["category"], row) for row in data["by_category"]
                               if row["partition_id"] == pid], "Category")
        lines += ["", "### Models and runners", ""]
        lines += _count_table([(model_labels[row["model_key"]], row) for row in data["by_model"]
                               if row["partition_id"] == pid], "Model / runner version")
        lines += ["", "### Tasks", ""]
        lines += _count_table([(row["task_id"], row) for row in data["by_task"]
                               if row["partition_id"] == pid], "Task")
    lines += ["", "## Bounded evidence details", "",
              "Only fixed signatures and public field/line citations are shown. Tool-scope signals "
              "are observations and never determine a task's failure category. No trace commands, "
              "prompts, hidden reasoning, stack dumps, or replay instructions are included.", ""]
    for detail in data["details"]:
        classification = detail["classification"]
        lines += [
            f"### {_cell(detail['run_id'])} / attempt {_cell(detail['attempt_id'])}", "",
            f"Task {_cell(detail['task_id'])}; repetition {_cell(detail['repetition'])}; "
            f"target {_cell(detail['target_id'])}. Category **{classification['category']}**, "
            f"confidence **{classification['confidence']}** in the observation, not its cause.",
            f"Basis: {classification['signature']}; {_cell(classification['source'])} "
            f"{_cell(classification['field'])}"
            + (f" line {classification['line']}." if "line" in classification else "."), "",
        ]
        for evidence in detail["evidence"]:
            lines.append(f"- Evidence {_cell(evidence['relative_path'])}: {evidence['status']}; "
                         f"SHA-256 {_cell(evidence.get('sha256'))}.")
        if detail["warning_codes"]:
            lines.append("- Notices: " + ", ".join(detail["warning_codes"]) + ".")
        lines.append("")
    lines += ["## Notices", ""]
    lines.extend(f"- {warning['code']} ({warning['count']}): {warning['message']}"
                 for warning in data["warnings"])
    if not data["warnings"]:
        lines.append("No input or evidence notices.")
    lines += [
        "", "## Triage and charts", "",
        "Inspect the exact saved attempt and its cited public fields before proposing a cause. "
        "Timeout signatures do not establish which resource or limit was responsible. "
        "A grader rejection does not establish a defective grader; use benchmark audit controls "
        "when grader validity is in question. Use accounting inspection for unknown costs.",
        "",
        "For a heatmap or Pareto chart, use DIAGNOSIS.json records or cells through "
        "$bedrock-bench:visualize-results. Keep partition_id filters and unknown/missing categories; "
        "count records once, not once per signal. The detailed evidence selection is not the population. "
        "Use $bedrock-bench:inspect-results for saved inspection/replay and prepare any new controlled "
        "experiment separately. An inventory or recorded suggestion does not authorize execution.",
        "",
    ]
    return "\n".join(lines)


def write_diagnosis(sources, directory, *, limit=100) -> Path:
    """Write DIAGNOSIS.json and DIAGNOSIS.md; return the absolute Markdown path.

    Existing destination files (including symlinks) are never overwritten.
    Validation and aggregation finish before either artifact is created.
    """
    data, protected = _build(sources, limit=limit)
    encoded = json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    report = markdown(data)
    directory = Path(directory).expanduser().resolve()
    targets = [directory / "DIAGNOSIS.json", directory / "DIAGNOSIS.md"]
    if any(path in protected for path in targets):
        raise ValueError("Diagnosis output would replace a recorded source artifact")
    if any(path.exists() or path.is_symlink() for path in targets):
        raise ValueError("Diagnosis output already exists; choose a fresh output directory")
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for path, text in zip(targets, (encoded, report)):
            with path.open("x", encoding="utf-8") as stream:
                created.append(path)
                stream.write(text)
    except (OSError, ValueError):
        for path in created:
            path.unlink()
        raise
    return targets[1]
