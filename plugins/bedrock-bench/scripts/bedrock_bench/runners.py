"""CLI process adapters and documented event-to-usage mappings."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from .costs import charge, complete_sum, normalize_usage, sum_usage
from .tasks import SYSTEM_PROMPT


def redact(text):
    for key, value in os.environ.items():
        if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", key, re.I) and len(value) >= 8:
            text = text.replace(value, "[REDACTED]")
    return text


def command_for(target, limits, workspace, config_path):
    env = os.environ.copy()
    if target.aws_profile:
        env["AWS_PROFILE"] = target.aws_profile
    if target.region:
        env["AWS_REGION"] = target.region
        env["AWS_DEFAULT_REGION"] = target.region
    if target.runner == "native":
        entry = Path(__file__).resolve().parents[1] / "bench.py"
        return [sys.executable, str(entry), "_worker", str(config_path), str(workspace)], env
    if target.runner == "codex":
        command = [
            "codex", "exec", "--json", "--ephemeral", "--ignore-user-config",
            "--skip-git-repo-check", "--sandbox", "workspace-write", "--color", "never",
            "--cd", str(workspace), "--model", target.model,
            "-c", f"model_provider={json.dumps(target.provider)}",
            "-c", 'shell_environment_policy.inherit="core"',
            "-c", 'web_search="disabled"',
        ]
        if target.reasoning_effort:
            command += ["-c", f"model_reasoning_effort={json.dumps(target.reasoning_effort)}"]
        if target.provider == "amazon-bedrock":
            command += ["-c", f"model_providers.amazon-bedrock.aws.region={json.dumps(target.region)}"]
            if target.aws_profile:
                command += ["-c", f"model_providers.amazon-bedrock.aws.profile={json.dumps(target.aws_profile)}"]
        return command + ["-"], env
    if target.runner == "opencode":
        # Use installed provider authentication, with a dedicated task agent.
        # OpenCode's client/managed configuration still participates in merging.
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "share": "disabled", "autoupdate": False,
            "compaction": {"auto": False},
            "agent": {"bedrock-bench": {
                "description": "Complete a local Bedrock Bench task",
                "mode": "primary", "prompt": SYSTEM_PROMPT, "steps": limits.max_turns,
                "permission": {"*": "deny", "read": "allow", "glob": "allow",
                    "grep": "allow", "edit": "allow", "external_directory": "deny"},
            }},
        })
        command = ["opencode", "run", "--format", "json", "--agent", "bedrock-bench",
                   "--title", "Bedrock Bench",
                   "--model", f"{target.provider}/{target.model}", "--dir", str(workspace)]
        if target.reasoning_effort:
            command += ["--variant", target.reasoning_effort]
        return command, env
    raise ValueError(f"No process adapter for {target.runner}")


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        pass


def kill_process(process):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def run_process(command, env, workspace, prompt, timeout):
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            command, cwd=workspace, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        return {"stdout": "", "stderr": redact(str(exc)), "exit_code": None,
                "timed_out": False, "wall_seconds": time.perf_counter() - started}
    timed_out = False
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_process(process)
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            kill_process(process)
            stdout, stderr = process.communicate(timeout=3)
    except BaseException:
        stop_process(process)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            kill_process(process)
            process.wait(timeout=3)
        raise
    return {"stdout": redact(stdout), "stderr": redact(stderr),
            "exit_code": process.returncode, "timed_out": timed_out,
            "wall_seconds": time.perf_counter() - started}


def parse_result(runner, process, rate_card=None):
    steps, errors, final_text = [], [], ""
    completed, tool_events, non_json = False, 0, 0
    native_accounting_closed = False
    observed_models, upstream_providers = set(), set()
    seen_parts = set()
    for line in process["stdout"].splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            if line.strip():
                non_json += 1
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if runner == "native":
            if kind == "step":
                source, raw = event.get("source"), event.get("usage")
                usage = normalize_usage(source, raw)
                steps.append({"usage": usage, "cost": charge(source, raw, usage, rate_card,
                    served_tier=event.get("service_tier")), "response_id": event.get("response_id")})
                if event.get("observed_model"):
                    observed_models.add(event["observed_model"])
                if event.get("upstream_provider"):
                    upstream_providers.add(event["upstream_provider"])
            elif kind == "tool":
                tool_events += 1
            elif kind == "completed":
                completed, final_text = True, event.get("final_text", "")
                native_accounting_closed = True
            elif kind == "error":
                errors.append(event.get("error", "Native runner error"))
                native_accounting_closed = event.get("accounting_complete") is True
        elif runner == "codex":
            if kind == "turn.completed":
                usage = normalize_usage("codex", event.get("usage"))
                steps.append({"usage": usage, "cost": charge("codex", event.get("usage"), usage, rate_card)})
                completed = True
            elif kind in ("turn.failed", "error"):
                errors.append(event.get("error", event.get("message", "Codex error")))
            elif kind == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    final_text = item.get("text", "")
                elif item.get("type") in ("command_execution", "mcp_tool_call", "file_change", "web_search"):
                    tool_events += 1
        elif runner == "opencode":
            part = event.get("part") or {}
            identity = part.get("id")
            if identity and (kind, identity) in seen_parts:
                continue
            if identity:
                seen_parts.add((kind, identity))
            if kind == "step_finish":
                usage = normalize_usage("opencode", part.get("tokens"))
                steps.append({"usage": usage, "cost": charge("opencode", part.get("tokens"), usage,
                    rate_card, emitted_cost=part.get("cost"))})
                if part.get("reason") == "stop":
                    completed = True
                elif part.get("reason") in ("length", "content-filter", "error"):
                    errors.append(f"OpenCode finish reason: {part['reason']}")
            elif kind == "tool_use":
                tool_events += 1
            elif kind == "text":
                final_text = part.get("text", "")
            elif kind == "error":
                errors.append(event.get("error", "OpenCode error"))
    if process["timed_out"]:
        status = "timeout"
    elif process["exit_code"] != 0 or errors:
        status = "runner_error"
    elif not completed:
        status = "incomplete"
    else:
        status = "completed"
    costs = [s["cost"]["usd"] for s in steps]
    # A failed process may have incurred unreported spend after its last event.
    accounting_complete = bool(steps) and (
        (native_accounting_closed and process["exit_code"] == 0 and not process["timed_out"])
        if runner == "native" else status == "completed"
    )
    total = complete_sum(costs) if accounting_complete else None
    return {
        "status": status, "errors": errors, "exit_code": process["exit_code"],
        "wall_seconds": round(process["wall_seconds"], 6),
        "usage": sum_usage([s["usage"] for s in steps]),
        "usage_granularity": "agent_turn_total" if runner == "codex" else "model_step",
        "reported_usage_events": len(steps), "reported_tool_events": tool_events,
        "steps": steps, "cost_usd": total,
        "known_cost_subtotal_usd": sum(c for c in costs if c is not None),
        "cost_basis": sorted({s["cost"]["basis"] for s in steps}) or ["unknown"],
        "accounting_complete": accounting_complete and total is not None,
        "observed_models": sorted(observed_models), "upstream_providers": sorted(upstream_providers),
        "non_json_lines": non_json, "final_text": final_text,
    }


def run_target(target, limits, task, workspace, attempt_dir, rate_card):
    config_path = attempt_dir / "worker.json"
    config_path.write_text(json.dumps({"target": asdict(target), "limits": asdict(limits), "prompt": task.prompt}))
    command, env = command_for(target, limits, workspace, config_path)
    process = run_process(command, env, workspace, SYSTEM_PROMPT + "\n\n" + task.prompt, limits.timeout_seconds)
    (attempt_dir / "events.jsonl").write_text(process["stdout"])
    (attempt_dir / "stderr.txt").write_text(process["stderr"])
    result = parse_result(target.runner, process, rate_card)
    result["command"] = command
    return result
