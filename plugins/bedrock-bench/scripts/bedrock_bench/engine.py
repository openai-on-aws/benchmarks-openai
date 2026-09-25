"""Run a finite task schedule, retaining evidence after every attempt."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import random
import subprocess
import sys
import time
import uuid

from . import __version__
from .config import fingerprint
from .costs import find_rate
from .report import write_report
from .runners import redact, run_target
from .tasks import encoded, execute_tool, make_task, protocol


def runner_version(runner):
    if runner in ("native", "demo"):
        return f"bedrock-bench/{__version__}+{implementation_digest()[:12]}"
    try:
        process = subprocess.run([runner, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return redact(process.stdout.strip() or process.stderr.strip())[:300]


def implementation_digest():
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_metadata():
    packages = {}
    for name in ("openai", "boto3", "aws-bedrock-token-generator"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": sys.version.split()[0], "packages": packages,
            "implementation_sha256": implementation_digest()}


def demo_result(target, task, workspace, attempt_dir):
    # This exercises filesystem operations and grading, with invented usage and
    # prices. No model, credential, API, or agent executable is called.
    events = [{"type": "demo", "synthetic": True}]
    names = execute_tool(workspace, "list_files", {})["files"]
    for name in names:
        events.append({"type": "tool", "name": "read_file",
                       "result": execute_tool(workspace, "read_file", {"path": name})})
    answer = task.demonstration_answer()
    imperfect = target.model == "fixture-imperfect"
    if imperfect and task.id == "inference-triage":
        answer = {**answer, "successful_requests": 4}
    events.append({"type": "tool", "name": "write_file", "result": execute_tool(workspace, "write_file",
        {"path": "answer.json", "content": encoded(answer)})})
    multiplier = 2 if imperfect else 1
    usage = {"input_tokens": 1000 * multiplier, "cached_input_tokens": 200,
             "cache_write_input_tokens": 0, "output_tokens": 150 * multiplier,
             "reasoning_output_tokens": 50}
    value = 0.002 * multiplier
    events.append({"type": "synthetic_usage", "usage": usage, "cost_usd": value})
    (attempt_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (attempt_dir / "stderr.txt").write_text("")
    return {
        "status": "completed", "errors": [], "exit_code": 0,
        "wall_seconds": 1.25 * multiplier, "usage": usage,
        "usage_granularity": "synthetic", "reported_usage_events": multiplier,
        "reported_tool_events": len(names) + 1,
        "steps": [{"usage": usage, "cost": {"usd": value, "basis": "synthetic"}}],
        "cost_usd": value, "known_cost_subtotal_usd": value, "cost_basis": ["synthetic"],
        "accounting_complete": True, "observed_models": [], "upstream_providers": [],
        "non_json_lines": 0, "final_text": "Synthetic fixture completed",
    }


def save_json(path, value):
    # Write outside the candidate directory and publish complete JSON atomically.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def execute(experiment, output, *, allow_live=False, progress=None):
    synthetic = experiment.targets[0].runner == "demo"
    if not synthetic and not allow_live:
        raise ValueError("Live execution requires --execute")
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = Path(output).resolve() / run_id
    root.mkdir(parents=True, exist_ok=False)
    versions = {runner: runner_version(runner) for runner in {t.runner for t in experiment.targets}}
    data = {
        "schema_version": 1, "run_id": run_id, "name": experiment.name, "synthetic": synthetic,
        "started_at": now.isoformat(), "status": "running",
        "protocol_hash": fingerprint(protocol(experiment)),
        "experiment": experiment.to_dict(), "attempts": [],
        "runtime": runtime_metadata(),
        "cost_scope": "Inference only; excludes infrastructure, subscriptions, external tools, and judges",
    }
    save_json(root / "run.json", data)
    schedule = [(target, task_id, rep) for rep in range(experiment.repetitions)
                for task_id in experiment.tasks for target in experiment.targets]
    random.Random(experiment.seed).shuffle(schedule)
    started = time.perf_counter()
    try:
        for index, (target, task_id, rep) in enumerate(schedule, 1):
            task = make_task(task_id, experiment.seed + rep)
            attempt_id = f"{index:04}-{target.id}-{task_id}-r{rep + 1}"
            attempt_dir = root / "attempts" / attempt_id
            attempt_dir.mkdir(parents=True)
            workspace = attempt_dir / "workspace"
            task.prepare(workspace)
            result = (demo_result(target, task, workspace, attempt_dir) if synthetic else
                      run_target(target, experiment.limits, task, workspace, attempt_dir,
                                 find_rate(experiment.rate_cards, target)))
            grading = task.grade(workspace)
            row = {
                **result, "attempt_id": attempt_id, "task_id": task_id, "repetition": rep + 1,
                "target": asdict(target), "runner_version": versions[target.runner],
                "success": grading["success"] and result["status"] == "completed", "grading": grading,
                "trace": str((attempt_dir / "events.jsonl").relative_to(root)),
                "trace_sha256": hashlib.sha256((attempt_dir / "events.jsonl").read_bytes()).hexdigest(),
                "workspace": str(workspace.relative_to(root)),
            }
            if not row["success"] and row["status"] == "completed":
                row["status"] = "task_failed"
            data["attempts"].append(row)
            save_json(root / "run.json", data)
            if progress:
                progress(index, len(schedule), row)
    except BaseException:
        data["status"] = "interrupted"
        save_json(root / "run.json", data)
        raise
    data["status"] = "completed"
    data["finished_at"] = datetime.now(timezone.utc).isoformat()
    data["controller_wall_seconds"] = time.perf_counter() - started
    save_json(root / "run.json", data)
    write_report([data], root)
    return root, data
