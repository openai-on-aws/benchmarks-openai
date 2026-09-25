"""Execute pinned upstream tasks and import their results into Bedrock Bench."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import time
import uuid

from .config import fingerprint
from .costs import FIELDS, count, estimate, find_rate, number, sum_usage
from .engine import runtime_metadata, save_json
from .report import write_report
from .runners import redact, run_process
from .suites import AWS_BENCH_COMMIT, AWS_REGISTRY, HARBOR_VERSION, SUITES, directory_digest, task_catalog


def agent_config(experiment, target):
    config = {
        "name": target.runner, "model_name": None if target.runner in {"oracle", "nop"} else target.model,
        "override_timeout_sec": experiment.limits.timeout_seconds,
        "override_setup_timeout_sec": experiment.limits.setup_timeout_seconds,
        "kwargs": {},
    }
    if target.agent_version:
        config["kwargs"]["version"] = target.agent_version
    if target.reasoning_effort:
        key = "reasoning_effort" if target.runner == "codex" else "variant"
        config["kwargs"][key] = target.reasoning_effort
    if target.runner == "opencode":
        config["model_name"] = f"{target.provider}/{target.model}"
    if target.skills:
        config["skills"] = [str(Path(p).resolve()) for p in target.skills]
    if "bedrock" in target.provider:
        if experiment.harness == "harbor":
            adapter = "BedrockCodex" if target.runner == "codex" else "BedrockOpenCode"
            config["name"] = None
            config["import_path"] = f"bedrock_bench.harbor_agents:{adapter}"
        else:
            # aws-bench supplies its own Bedrock-aware Codex adapter.
            config["env"] = {"AWS_REGION": target.region, "AWS_DEFAULT_REGION": target.region}
    return config


def job_config(experiment, target, task_id, attempt_dir):
    """One trial per process makes task selection, retries and failures explicit."""
    config = {
        "job_name": "job", "jobs_dir": str(Path(attempt_dir) / "upstream"),
        "n_attempts": 1, "n_concurrent_trials": 1,
        "retry": {"max_retries": 0},
        "environment": {"type": "docker", "delete": True},
        "verifier": {"override_timeout_sec": experiment.limits.verifier_timeout_seconds},
        "agents": [agent_config(experiment, target)],
    }
    if experiment.harness == "aws-bench":
        config["env_name"] = experiment.aws_environment
        info = SUITES[experiment.suite]
        config["dataset"] = {
            "name": info["dataset"], "version": info["version"],
            "registry_path": str(AWS_REGISTRY), "task_names": [task_id],
        }
    else:
        source = task_catalog(experiment.suite)[task_id]
        if experiment.suite == "aws-cdk-smoke":
            task = {"path": source["path"]}
        else:
            task = {key: source[key] for key in ("path", "git_url", "git_commit_id")}
        config["tasks"] = [task]
    return config


def process_environment(experiment, target):
    env = os.environ.copy()
    # Import the packaged adapter from the isolated harness interpreter.
    scripts = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = scripts + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["DO_NOT_TRACK"] = "1"
    if platform := SUITES[experiment.suite].get("docker_platform"):
        env["DOCKER_DEFAULT_PLATFORM"] = platform
    if target.aws_profile:
        env["AWS_PROFILE"] = target.aws_profile
    if target.region:
        env["AWS_REGION"] = target.region
        env["AWS_DEFAULT_REGION"] = target.region
        env["BEDROCK_BENCH_MODEL_REGION"] = target.region
    if experiment.harness == "aws-bench":
        env["AWS_REGION"] = env["AWS_DEFAULT_REGION"] = "us-east-1"
        if experiment.environment_profile:
            env["AWS_PROFILE"] = experiment.environment_profile
    if target.provider == "openai":
        # aws-bench otherwise auto-selects Bedrock when this token is present.
        env.pop("AWS_BEARER_TOKEN_BEDROCK", None)
    return env


def elapsed(timing):
    try:
        start = datetime.fromisoformat(timing["started_at"].replace("Z", "+00:00"))
        finish = datetime.fromisoformat(timing["finished_at"].replace("Z", "+00:00"))
        value = (finish - start).total_seconds()
        return value if value >= 0 else None
    except (TypeError, KeyError, ValueError, AttributeError):
        return None


def usage_and_cost(raw, target, rate_card, *, complete):
    context = raw.get("agent_result") or {}
    contexts = ([context] if context else
                [step.get("agent_result") or {} for step in raw.get("step_results") or []])
    usages, values, bases = [], [], []
    for entry in contexts:
        usage = {
            "input_tokens": count(entry.get("n_input_tokens")),
            "cached_input_tokens": count(entry.get("n_cache_tokens")),
            # Codex Responses does not request explicit cache-write billing.
            # Other adapters do not expose enough information to assume zero.
            "cache_write_input_tokens": 0 if target.runner == "codex" else None,
            "output_tokens": count(entry.get("n_output_tokens")),
            "reasoning_output_tokens": None,
        }
        if (usage["input_tokens"] is not None and usage["cached_input_tokens"] is not None
                and usage["cached_input_tokens"] > usage["input_tokens"]):
            usage = dict.fromkeys(FIELDS)
        reported = number(entry.get("cost_usd"))
        # Upstream cost is an agent/catalog estimate, not a provider invoice.
        # A default zero may mean unavailable pricing.
        value = reported if reported is not None and reported > 0 else None
        basis = "runner_estimate" if value is not None else "unknown"
        model_usage = entry.get("model_usage")
        models = set(model_usage or {}) if isinstance(model_usage, (dict, type(None))) else None
        if value is None and models is not None and (not models or models <= {target.model, f"{target.provider}/{target.model}"}):
            value = estimate(usage, rate_card)
            if value is not None:
                basis = "rate_card_estimate"
        usages.append(usage)
        values.append(value)
        bases.append(basis)
    total = sum(values) if complete and values and all(v is not None for v in values) else None
    return {
        "usage": sum_usage(usages), "cost_usd": total,
        "known_cost_subtotal_usd": sum(v for v in values if v is not None),
        "cost_basis": sorted(set(bases)) or ["unknown"],
        "accounting_complete": total is not None,
        "usage_granularity": "upstream_agent_total", "reported_usage_events": len(contexts),
    }


def strict_json(text):
    def reject(value):
        raise ValueError(f"Non-finite JSON value: {value}")
    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            reject(value)
        return parsed
    return json.loads(text, parse_constant=reject, parse_float=finite_float)


def import_trial(experiment, target, task_id, attempt_dir, process):
    candidates = []
    for path in (attempt_dir / "upstream").rglob("result.json"):
        if path.is_symlink():
            continue
        try:
            raw = strict_json(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict) and "task_name" in raw and "trial_name" in raw:
            candidates.append((path, raw))
    error = None
    raw, result_path = {}, None
    if len(candidates) != 1:
        error = f"Expected one upstream trial result, found {len(candidates)}"
    else:
        result_path, raw = candidates[0]
        if raw["task_name"] != task_id:
            error = f"Upstream task mismatch: expected {task_id}, received {raw['task_name']}"
        source = task_catalog(experiment.suite)[task_id]
        observed = raw.get("task_id")
        if (source.get("git_commit_id") and
                (not isinstance(observed, dict) or observed.get("git_commit_id") != source["git_commit_id"])):
            error = "Upstream result does not match the pinned task commit"
        for key in ("agent_result", "verifier_result", "agent_info"):
            if raw.get(key) is not None and not isinstance(raw[key], dict):
                error = f"Invalid upstream {key} shape"
                raw[key] = {}
        if raw.get("step_results") is not None and (
            not isinstance(raw["step_results"], list)
            or any(not isinstance(step, dict) or not isinstance(step.get("agent_result", {}), (dict, type(None)))
                   for step in raw["step_results"])
        ):
            error = "Invalid upstream step_results shape"
            raw["step_results"] = []
        contexts = [raw.get("agent_result")] + [
            step.get("agent_result") for step in raw.get("step_results") or []
        ]
        if any(entry is not None and not isinstance(entry.get("model_usage"), (dict, type(None)))
               for entry in contexts):
            error = "Invalid upstream model_usage shape"
    rewards = (raw.get("verifier_result") or {}).get("rewards") or {}
    if not isinstance(rewards, dict):
        error = "Invalid upstream rewards shape"
        rewards = {}
    reward = number(rewards.get("reward"))
    exception = raw.get("exception_info")
    if process["timed_out"]:
        status = "timeout"
    elif error or process["exit_code"] != 0 or exception:
        status = "runner_error"
    elif reward is None:
        status = "incomplete"
        error = "Upstream verifier did not report a numeric 'reward'"
    else:
        status = "completed" if reward == 1 else "task_failed"
    accounting = usage_and_cost(raw, target, find_rate(experiment.rate_cards, target),
                                complete=status in {"completed", "task_failed"})
    if experiment.validation_only:
        accounting.update(cost_usd=0, known_cost_subtotal_usd=0,
                          cost_basis=["reference_no_model"], accounting_complete=True)
    info = raw.get("agent_info") or {}
    duration = elapsed(raw.get("agent_execution"))
    exception_message = (f"{exception.get('exception_type', 'UpstreamError')}: "
                         f"{exception.get('exception_message', '')}") if isinstance(exception, dict) else exception
    row = {
        **accounting, "status": status, "success": status == "completed",
        "errors": [str(item) for item in (error, exception_message) if item],
        "exit_code": process["exit_code"], "wall_seconds": process["wall_seconds"],
        "agent_wall_seconds": duration,
        "grading": {"success": status == "completed", "rewards": rewards,
                    "reason": error or ("Upstream verifier passed" if reward == 1 else "Upstream verifier did not pass")},
        "runner_version": str(info.get("version") or "unavailable"),
        "upstream": {
            "harness": experiment.harness, "trial_name": raw.get("trial_name"),
            "task_checksum": raw.get("task_checksum"), "task_id": raw.get("task_id"),
            "result": str(result_path.relative_to(attempt_dir)) if result_path else None,
            "agent_info": info, "verifier_cost_included": False,
        },
    }
    return row


def harness_metadata(experiment):
    python = Path(experiment.executable).parent / ("python.exe" if os.name == "nt" else "python")
    if not python.is_file():
        raise ValueError(f"Cannot verify the harness runtime; run prepare --suite {experiment.suite} --execute")
    code = """
import importlib.metadata as m,json
packages={p.metadata['Name']:p.version for p in m.distributions()}
direct_urls={}
if 'aws-bench' in packages:
 direct_urls['aws-bench']=json.loads(m.distribution('aws-bench').read_text('direct_url.json') or '{}')
print(json.dumps({'packages':packages,'direct_urls':direct_urls},sort_keys=True))
"""
    result = subprocess.run([str(python), "-c", code], text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise ValueError(f"Cannot inspect harness runtime: {redact(result.stderr)[-1000:]}")
    metadata = json.loads(result.stdout)
    expected_harbor = HARBOR_VERSION if experiment.harness == "harbor" else "0.9.0"
    valid = metadata["packages"].get("harbor") == expected_harbor
    if experiment.harness == "aws-bench":
        source = metadata["direct_urls"].get("aws-bench", {})
        valid = valid and source.get("vcs_info", {}).get("commit_id") == AWS_BENCH_COMMIT
    if not valid:
        raise ValueError(f"Harness runtime differs from the suite's pinned version; rerun prepare --suite {experiment.suite} --execute")
    return {**metadata, "verified": True}


def redact_upstream(directory):
    """Upstream logs can contain environment values; redact before publishing."""
    for path in directory.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix not in {".json", ".jsonl", ".txt", ".log", ".yaml", ".yml", ".toml"}:
            continue
        try:
            original = path.read_text()
        except (OSError, UnicodeError):
            continue
        cleaned = redact(original)
        if cleaned != original:
            path.write_text(cleaned)


def execute_suite(experiment, output, *, allow_live=False, progress=None):
    if not allow_live:
        raise ValueError("Repository execution requires --execute, including reference container checks")
    if not Path(experiment.executable).is_file():
        raise ValueError(f"Missing harness runtime; run prepare --suite {experiment.suite} --execute")
    if experiment.harness == "aws-bench" and not experiment.aws_environment:
        raise ValueError("Set aws_environment and provision it with aws-env before execution")
    for target in experiment.targets:
        if "bedrock" in target.provider and not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
            raise ValueError("Container agents need AWS_BEARER_TOKEN_BEDROCK for Bedrock inference; no token was found")
    identities = {
        target.id: {**asdict(target),
                    "skill_sha256": {str(Path(p).resolve()): directory_digest(p) for p in target.skills}}
        for target in experiment.targets
    }
    protocol = experiment.protocol()
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = Path(output).resolve() / run_id
    root.mkdir(parents=True, exist_ok=False)
    data = {
        "schema_version": 1, "run_id": run_id, "name": experiment.name,
        "synthetic": False, "validation_only": experiment.validation_only,
        "suite": experiment.suite, "started_at": now.isoformat(), "status": "running",
        "protocol_hash": fingerprint(protocol), "protocol": protocol,
        "experiment": experiment.to_dict(), "attempts": [], "runtime": runtime_metadata(),
        "cost_scope": "Agent inference only; infrastructure, subscriptions, external tools, and verifier/judge costs are excluded",
        "harness_runtime": harness_metadata(experiment),
        "docker_client": {"config_directory": os.environ.get("DOCKER_CONFIG"),
                          "host": os.environ.get("DOCKER_HOST"),
                          "platform": SUITES[experiment.suite].get("docker_platform")
                                      or os.environ.get("DOCKER_DEFAULT_PLATFORM")},
    }
    lock = Path(experiment.executable).parent.parent / "bedrock-bench-tool.json"
    if lock.is_file():
        data["harness_runtime"]["installation"] = json.loads(lock.read_text())
    schedule = [(target, task, rep) for rep in range(experiment.repetitions)
                for task in experiment.tasks for target in experiment.targets]
    random.Random(experiment.seed).shuffle(schedule)
    data["schedule"] = [{"target": t.id, "task": task, "repetition": rep + 1} for t, task, rep in schedule]
    save_json(root / "run.json", data)
    started = time.perf_counter()
    try:
        for index, (target, task_id, rep) in enumerate(schedule, 1):
            attempt_id = f"{index:04}-{target.id}-{task_id}-r{rep + 1}"
            attempt_dir = root / "attempts" / attempt_id
            attempt_dir.mkdir(parents=True)
            config_path = attempt_dir / "job.json"
            save_json(config_path, job_config(experiment, target, task_id, attempt_dir))
            command = [experiment.executable, "run", "--config", str(config_path)]
            if experiment.harness == "aws-bench":
                command.append("--yes")
            process = run_process(command, process_environment(experiment, target),
                                  attempt_dir, "", experiment.limits.process_timeout_seconds)
            (attempt_dir / "stdout.txt").write_text(process["stdout"])
            (attempt_dir / "stderr.txt").write_text(process["stderr"])
            redact_upstream(attempt_dir)
            row = import_trial(experiment, target, task_id, attempt_dir, process)
            version = data["harness_runtime"]["packages"].get(experiment.harness, "unavailable")
            row["runner_version"] = f"{experiment.harness}/{version}; {target.runner}/{row['runner_version']}"
            trace = attempt_dir / row["upstream"]["result"] if row["upstream"]["result"] else config_path
            row.update(attempt_id=attempt_id, task_id=task_id, repetition=rep + 1,
                       target=identities[target.id], command=command,
                       trace=str(trace.relative_to(root)),
                       trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
                       workspace=str(attempt_dir.relative_to(root)))
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


def aws_environment_action(experiment, action, output, *, execute=False):
    if experiment.suite != "aws-bench" or not experiment.aws_environment:
        raise ValueError("aws-env requires an aws-bench experiment with an explicit aws_environment")
    if action not in {"show", "init", "setup", "verify", "reset", "cleanup"}:
        raise ValueError("Unsupported AWS environment action")
    info = SUITES["aws-bench"]
    command = [experiment.executable, "env", action, "--env-name", experiment.aws_environment]
    if action != "show":
        command += ["--dataset", f"{info['dataset']}@{info['version']}", "--registry-path", str(AWS_REGISTRY)]
    if action in {"reset", "cleanup"}:
        command.append("--yes")
    result = {
        "action": action, "aws_environment": experiment.aws_environment, "command": command,
        "region": "us-east-1", "aws_profile": experiment.environment_profile,
        "creates_or_changes_aws_resources": action in {"init", "setup", "reset", "cleanup"},
        "execution": "Plan only unless --execute is supplied. Setup creates billable AWS resources.",
    }
    if not execute:
        return result
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    process = run_process(command, process_environment(experiment, experiment.targets[0]),
                          root, "", experiment.limits.process_timeout_seconds)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    log = root / f"aws-env-{action}-{stamp}.json"
    save_json(log, {**result, **process})
    return {**result, "exit_code": process["exit_code"], "timed_out": process["timed_out"],
            "log": str(log)}
