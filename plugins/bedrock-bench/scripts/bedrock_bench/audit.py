"""Offline controls for packaged graders, independent of model measurements.

The only public entrypoint is write_audit. Candidate data never selects a
command, executable, grader, or output path. CDK support lives with the skill.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from . import tasks


PLUGIN = Path(__file__).resolve().parents[2]
SKILL = PLUGIN / "skills" / "audit-benchmark"
SUPPORTED_SUITES = ("starter", "aws-cdk-smoke")
MAX_LOG_BYTES = 1_048_576


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def save_new(path, content):
    """Never replace evidence, including a dangling symlink."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content if isinstance(content, bytes) else content.encode())
    return path


def source_hashes(paths):
    result = {}
    for path in paths:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing or symlinked packaged audit source: {path}")
        result[path.relative_to(PLUGIN).as_posix()] = sha256(path.read_bytes())
    return result


def checked_path(value, name):
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError(f"{name} must be a directory path")
    text = os.fspath(value)
    if (not isinstance(text, str) or not text.strip()
            or any(ord(c) < 32 for c in text) or ".." in Path(text).parts):
        raise ValueError(f"{name} must be a nonempty path without traversal or control characters")
    path = Path(text).expanduser()
    if path.is_symlink():
        raise ValueError(f"{name} must not be a symlink")
    return path.resolve()


def new_output(directory, tools_dir):
    root = checked_path(directory, "directory")
    tools = checked_path(tools_dir, "tools_dir")
    if tools.exists() and not tools.is_dir():
        raise ValueError("tools_dir must be a directory")
    if root == PLUGIN or root.is_relative_to(PLUGIN):
        raise ValueError("Audit output must be outside the installed plugin")
    if root == tools or root.is_relative_to(tools) or tools.is_relative_to(root):
        raise ValueError("Audit output and tools_dir must be separate directory trees")
    # An existing empty directory is useful to callers using TemporaryDirectory.
    # Refuse every nonempty directory, even if it contains no AUDIT.* yet.
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise ValueError("Audit output must be a new or empty directory; artifacts are never overwritten")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root, tools


def make_case(root, task_id, name, kind, invariant, expected, files, recipe):
    candidate = root / "candidates" / task_id / name
    candidate.mkdir(parents=True, exist_ok=False)
    inventory = {}
    for name_in_case, content in sorted(files.items()):
        path = Path(name_in_case)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Control files must be candidate-relative")
        data = content if isinstance(content, bytes) else content.encode()
        save_new(candidate / path, data)
        inventory[path.as_posix()] = {"sha256": sha256(data), "bytes": len(data)}
    return {
        "id": f"{task_id}/{name}", "task": task_id, "name": name,
        "kind": kind, "label": "reference" if kind in {"reference", "alternate-valid"} else "synthetic",
        "model": None, "invariant": invariant, "expected": expected,
        "observed": "not_run", "status": "planned",
        "candidate": candidate.relative_to(root).as_posix(),
        "files": inventory, "recipe": recipe, "evidence": [],
    }


def evidence(root, case, name, value):
    path = root / "controller" / "cases" / case["id"] / name
    save_new(path, value if isinstance(value, (bytes, str)) else json_bytes(value))
    item = {"path": path.relative_to(root).as_posix(), "sha256": sha256(path.read_bytes())}
    case["evidence"].append(item)
    return item


def record(case, observed, reason):
    if observed not in {"accept", "reject", "infrastructure_error", "unavailable"}:
        raise ValueError("Invalid observed audit outcome")
    case["observed"] = observed
    case["reason"] = reason
    case["status"] = (
        "unavailable" if observed == "unavailable"
        else "passed" if observed == case["expected"] else "failed"
    )


def finish(data):
    cases = data["cases"]
    data["summary"] = {
        "cases": len(cases),
        "matched": sum(c["status"] == "passed" for c in cases),
        "rejected_valid": [c["id"] for c in cases if c["expected"] == "accept" and c["observed"] == "reject"],
        "surviving_mutants": [c["id"] for c in cases if c["expected"] == "reject" and c["observed"] == "accept"],
        "killed_mutants": [c["id"] for c in cases if c["expected"] == "reject" and c["observed"] == "reject"],
        "infrastructure_errors": [c["id"] for c in cases if c["observed"] == "infrastructure_error"],
        "unavailable": [c["id"] for c in cases if c["observed"] == "unavailable"],
    }
    if not data["execute"]:
        data["status"] = "planned"
    elif any(c["status"] == "failed" for c in cases) or data.get("controller_error"):
        data["status"] = "failed"
    elif not cases or any(c["status"] != "passed" for c in cases):
        data["status"] = "unavailable"
    else:
        data["status"] = "passed"
    data["finished_at"] = datetime.now(timezone.utc).isoformat()


def independent_answer(task, *, alternate=False):
    """Solve only visible inputs; do not use Task.expected or demonstration_answer."""
    if task.id == "invoice-reconciliation":
        invoices = json.loads(task.files["invoices.json"])
        payments = json.loads(task.files["payments.json"])
        paid = {}
        for payment in payments:
            paid[payment["invoice_id"]] = paid.get(payment["invoice_id"], 0) + payment["amount_cents"]
        balances = {row["id"]: max(row["amount_cents"] - paid.get(row["id"], 0), 0)
                    for row in invoices if row["status"] != "void"}
        return {
            "outstanding_cents": balances,
            "total_outstanding_cents": sum(balances.values()),
            "overdue_invoice_ids": sorted(row["id"] for row in invoices
                if balances.get(row["id"], 0) > 0 and row["due_date"] < "2026-09-01"),
        }
    if task.id == "inference-triage":
        rows = json.loads(task.files["attempts.json"])
        latest = {}
        for row in rows:
            if row["request_id"] not in latest or latest[row["request_id"]]["attempt"] < row["attempt"]:
                latest[row["request_id"]] = row
        failures = sorted(key for key, row in latest.items() if row["status"] != 200)
        return {
            "logical_requests": len(latest), "attempts": len(rows),
            "successful_requests": len(latest) - len(failures),
            "throttled_attempts": sum(row["status"] == 429 for row in rows),
            "failed_request_ids": failures,
            "total_attempt_time_ms": sum(row["elapsed_ms"] for row in rows),
        }
    if task.id == "deployment-order":
        dependencies = {row["name"]: set(row["depends_on"]) for row in json.loads(task.files["services.json"])}
        order = []
        while len(order) < len(dependencies):
            ready = sorted((name for name, deps in dependencies.items()
                            if name not in order and deps <= set(order)), reverse=alternate)
            if not ready:
                raise ValueError("Control input contains a dependency cycle")
            order.append(ready[0])
        return {"deployment_order": order}
    raise ValueError(f"No independent audit reference for {task.id}")


def starter_cases(root, seed):
    cases, graders = [], {}
    for task_id in tasks.TASK_IDS:
        task = tasks.make_task(task_id, seed)
        graders[task_id] = task
        answer = independent_answer(task)
        variants = [
            ("reference", "reference", "Solve the visible task inputs independently", "accept", json_bytes(answer)),
            ("alternate-valid", "alternate-valid", "Accept equivalent JSON formatting and any valid topological order",
             "accept", (json.dumps(independent_answer(task, alternate=True), sort_keys=False,
                                  separators=(",", ":")) + " \n").encode()),
            ("missing-answer", "malformed", "Require answer.json", "reject", None),
            ("malformed-json", "malformed", "Reject truncated JSON", "reject", b'{"unfinished":'),
            ("wrong-container", "malformed", "Require a JSON object", "reject", b"[]\n"),
            ("invalid-utf8", "malformed", "Reject undecodable artifacts", "reject", b"\xff\xfe"),
            ("oversize-answer", "malformed", "Enforce the answer byte limit even for otherwise valid JSON",
             "reject", json_bytes(answer) + b" " * (tasks.MAX_FILE_BYTES + 1)),
        ]
        semantic = []
        if task_id == "invoice-reconciliation":
            invoices = json.loads(task.files["invoices.json"])
            include_void = deepcopy(answer)
            void = next(row for row in invoices if row["status"] == "void")
            include_void["outstanding_cents"][void["id"]] = void["amount_cents"]
            include_void["total_outstanding_cents"] += void["amount_cents"]
            semantic.append(("include-void", "Exclude void invoices", include_void))
            omit_zero = deepcopy(answer)
            omit_zero["outstanding_cents"] = {k: v for k, v in omit_zero["outstanding_cents"].items() if v}
            semantic.append(("omit-zero-balance", "Include fully paid invoices with zero balances", omit_zero))
            ignore_payments = deepcopy(answer)
            ignore_payments["outstanding_cents"] = {r["id"]: r["amount_cents"] for r in invoices if r["status"] != "void"}
            ignore_payments["total_outstanding_cents"] = sum(ignore_payments["outstanding_cents"].values())
            semantic.append(("ignore-payments", "Subtract payments before summing balances", ignore_payments))
            not_overdue = deepcopy(answer)
            not_overdue["overdue_invoice_ids"] = sorted(answer["outstanding_cents"])
            semantic.append(("all-invoices-overdue", "Only positive balances due before the as-of date are overdue", not_overdue))
            bool_balance = deepcopy(answer)
            zero = next(k for k, v in answer["outstanding_cents"].items() if v == 0)
            bool_balance["outstanding_cents"][zero] = False
            semantic.append(("boolean-zero", "JSON booleans are not integer cents", bool_balance))
            wrong_total = deepcopy(answer)
            wrong_total["total_outstanding_cents"] += 1
            semantic.append(("incorrect-total", "Total must equal the reconciled balances", wrong_total))
        elif task_id == "deployment-order":
            order = answer["deployment_order"]
            semantic.extend([
                ("dependency-inversion", "Dependencies precede dependents", {"deployment_order": list(reversed(order))}),
                ("duplicate-service", "Every service appears exactly once", {"deployment_order": order[:-1] + [order[0]]}),
                ("missing-service", "No required service may be omitted", {"deployment_order": order[:-1]}),
                ("unknown-service", "Do not substitute unknown service names", {"deployment_order": order[:-1] + ["unknown"]}),
                ("non-string-service", "Service names are strings", {"deployment_order": order[:-1] + [False]}),
                ("non-list-order", "The deployment order is an array", {"deployment_order": ",".join(order)}),
            ])
        else:
            for name, field, value, invariant in [
                ("attempts-as-requests", "logical_requests", answer["attempts"], "Retries are not additional logical requests"),
                ("requests-as-attempts", "attempts", answer["logical_requests"], "Count every retry attempt"),
                ("failed-attempts-as-requests", "failed_request_ids", ["req-2", "req-3", "req-4"], "Use final attempt status for request outcome"),
                ("omit-throttles", "throttled_attempts", 0, "Count all status-429 attempts"),
                ("success-only-time", "total_attempt_time_ms",
                 sum(r["elapsed_ms"] for r in json.loads(task.files["attempts.json"]) if r["status"] == 200),
                 "Elapsed time includes retries and failures"),
                ("boolean-count", "successful_requests", True, "Boolean values are not request counts"),
                ("string-count", "attempts", str(answer["attempts"]), "Counts must use the expected JSON types"),
            ]:
                mutant = deepcopy(answer)
                mutant[field] = value
                semantic.append((name, invariant, mutant))
        # The deployment prompt does not require exactly one key; do not declare
        # its permissive extra-key behavior a false positive.
        if task_id != "deployment-order":
            semantic.append(("extra-field", "The task requests exactly the named output fields", {**answer, "extra": 1}))
        variants.extend((name, "semantic-mutant", invariant, "reject", json_bytes(value))
                        for name, invariant, value in semantic)
        for name, kind, invariant, expected, artifact in variants:
            files = dict(task.files)
            if artifact is not None:
                files["answer.json"] = artifact
            case = make_case(root, task_id, name, kind, invariant, expected, files, {
                "type": "fixed-artifact", "input_seed": seed,
                "answer": "answer.json" if artifact is not None else None,
                "reference": f"{task_id}/reference",
            })
            if kind == "semantic-mutant" and artifact == json_bytes(answer):
                raise ValueError(f"Mutation did not change the reference: {case['id']}")
            cases.append(case)
        # Exercise the real grader's path guard without copying controller
        # answers into the candidate or following a link while hashing.
        case = make_case(root, task_id, "symlink-answer", "malformed",
                         "Do not follow an answer symlink outside the candidate", "reject",
                         task.files, {"type": "symlink", "target_scope": "controller-only reference answer"})
        target = root / "controller" / "references" / task_id / "answer.json"
        save_new(target, json_bytes(answer))
        link = root / case["candidate"] / "answer.json"
        try:
            link.symlink_to(os.path.relpath(target, link.parent))
            case["files"]["answer.json"] = {"type": "symlink", "target": os.readlink(link)}
        except OSError as exc:
            case["preparation_error"] = f"Cannot create the symlink control: {type(exc).__name__}"
        cases.append(case)
    return cases, graders


def execute_starter(root, cases, graders):
    for case in cases:
        if case.get("preparation_error"):
            record(case, "infrastructure_error", case["preparation_error"])
            evidence(root, case, "grade.json", {"error": case["reason"], "grader_invoked": False})
            continue
        try:
            result = graders[case["task"]].grade(root / case["candidate"])
            if not isinstance(result, dict) or type(result.get("success")) is not bool:
                raise ValueError("Grader must return an object with boolean success")
            # Also require evidence to be serializable before trusting a verdict.
            json_bytes(result)
            record(case, "accept" if result["success"] else "reject", str(result.get("reason", "")))
            evidence(root, case, "grade.json", {"grader_invoked": True, "result": result})
        except Exception as exc:
            record(case, "infrastructure_error", f"{type(exc).__name__}: {exc}")
            evidence(root, case, "grade.json", {"grader_invoked": True, "error": case["reason"]})


def run_bounded(command, cwd, directory, *, env, timeout=30):
    """Fixed argv only; bound time and log size, kill the local process group."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    stdout, stderr = directory / "stdout.txt", directory / "stderr.txt"
    result = {"command": list(command), "timeout_seconds": timeout, "exit_code": None,
              "timed_out": False, "output_limited": False, "error": None}
    started = time.monotonic()
    process = None

    def kill():
        if process is None or process.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=5)

    with stdout.open("xb") as out, stderr.open("xb") as err:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=os.name == "posix")
            while process.poll() is None:
                result["timed_out"] = time.monotonic() - started >= timeout
                result["output_limited"] = max(stdout.stat().st_size, stderr.stat().st_size) > MAX_LOG_BYTES
                if result["timed_out"] or result["output_limited"]:
                    kill()
                    break
                time.sleep(0.02)
            result["exit_code"] = process.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            # Also bound children if the controller is interrupted or evidence
            # collection fails after the process starts.
            kill()
    result["wall_seconds"] = round(time.monotonic() - started, 4)
    for name, path in (("stdout", stdout), ("stderr", stderr)):
        size = path.stat().st_size
        result["output_limited"] |= size > MAX_LOG_BYTES
        with path.open("rb") as stream:
            result[name] = stream.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")
    save_new(directory / "process.json", json_bytes({k: v for k, v in result.items() if k not in {"stdout", "stderr"}}))
    return result


def load_cdk_helper():
    path = SKILL / "scripts" / "cdk_controls.py"
    spec = importlib.util.spec_from_file_location("_bedrock_bench_cdk_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def markdown(data):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    lines = [
        f"# {data['suite']} grader audit", "",
        f"Status: **{data['status']}**. {len(data['cases'])} fixed controls; seed {data['seed']}.",
        "This is synthetic/reference grader validation. It contains no model measurements.", "",
        "[Machine-readable evidence and source hashes](AUDIT.json). "
        "Candidate files are in `candidates/`; controller recipes and evidence are in `controller/`.", "",
        "| Case | Label | Expected | Observed | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in data["cases"]:
        lines.append("| " + " | ".join(cell(case[k]) for k in ("id", "label", "expected", "observed", "status")) + " |")
    for title, key in (("Surviving mutants", "surviving_mutants"), ("Rejected valid controls", "rejected_valid"),
                       ("Infrastructure errors", "infrastructure_errors")):
        if data["summary"][key]:
            lines += ["", f"## {title}", ""]
            for case in data["cases"]:
                if case["id"] in data["summary"][key]:
                    lines.append(f"- `{case['id']}`: {cell(case.get('reason', ''))}")
    for title, values in (("Preparation needed", data.get("preparation_needed", [])),
                          ("Scope and limits", data["scope"]["limitations"])):
        if values:
            lines += ["", f"## {title}", ""] + [f"- {cell(value)}" for value in values]
    if data.get("fixture_validation"):
        lines += ["", "Fixture self-check: " + cell(data["fixture_validation"]["status"]) +
                  ". This checks the controls, not the packaged CDK grader."]
    if data.get("controller_error"):
        lines += ["", "Controller error: " + cell(data["controller_error"])]
    return "\n".join(lines) + "\n"


def write_audit(suite, directory, *, seed=42, execute=False, tools_dir=".bench-tools") -> Path:
    """Write AUDIT.md/AUDIT.json and exact controls to a new or empty directory.

    Invalid options raise ValueError. Grader mismatches and infrastructure errors
    are recorded as failed; missing optional runtimes are unavailable.
    """
    if not isinstance(suite, str) or suite not in SUPPORTED_SUITES:
        raise ValueError(f"Audit supports only {', '.join(SUPPORTED_SUITES)}")
    if type(seed) is not int:
        raise ValueError("seed must be an integer, not a boolean")
    if type(execute) is not bool:
        raise ValueError("execute must be a boolean")
    root, tools = new_output(directory, tools_dir)
    data = {
        "schema_version": 1, "audit_id": f"audit-{uuid.uuid4().hex}",
        "kind": "grader-audit", "suite": suite, "seed": seed, "execute": execute,
        "status": "planned", "synthetic": True, "validation_only": True, "model": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "model_calls": False, "aws_calls": False, "network": False, "installs": False,
            "candidate_source": "Packaged deterministic controls only; no user configs or recorded traces",
            "limitations": ["Finite controls at one seed do not prove grader completeness or model quality.",
                            "A rejected control is counted only after a valid grader verdict; infrastructure errors never kill mutants."],
        },
        "cases": [], "preparation_needed": [],
        "controller_hashes": source_hashes([Path(__file__), SKILL / "SKILL.md"]),
    }
    if suite == "starter":
        data["suite_revision"] = tasks.SUITE_REVISION
        data["scope"]["tasks"] = list(tasks.TASK_IDS)
        data["source_hashes"] = source_hashes([Path(tasks.__file__)])
        data["grader_hashes"] = dict(data["source_hashes"])
        data["scope"]["limitations"].append(
            "Starter controls use the generated task inputs. Boundary inputs absent from those fixtures (e.g. overpayments) are not claimed as covered.")
        data["cases"], graders = starter_cases(root, seed)
        if execute:
            execute_starter(root, data["cases"], graders)
    else:
        helper = load_cdk_helper()
        helper.prepare(root, data, tools)
        if execute:
            helper.execute(root, data, tools)
    data["plan"] = {
        "command": [sys.executable, "-B", str(PLUGIN / "scripts/bench.py"), "audit",
                    "--suite", suite, "--seed", str(seed), "--tools-dir", str(tools),
                    "--out", str(root.with_name(root.name + "-execute-" + uuid.uuid4().hex[:8])), "--execute"],
        "execution": "Explicit argv for another, non-overwriting audit; never evaluated as shell text",
    }
    for case in data["cases"]:
        evidence(root, case, "control.json", {k: v for k, v in case.items() if k != "evidence"})
    finish(data)
    save_new(root / "AUDIT.json", json_bytes(data))
    return save_new(root / "AUDIT.md", markdown(data))
