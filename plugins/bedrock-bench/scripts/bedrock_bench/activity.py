"""Replay public prompts and tools from saved Harbor/Codex session evidence."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from .config import fingerprint
from .explorer import _evidence_path, _reject_constant, load_run
from .runners import redact
from .views import ASSETS, embedded_json, write_view

MAX_SESSION_BYTES = 16 * 1024 * 1024
MAX_EVENT_BYTES = 512 * 1024
MAX_SESSIONS = 8
MAX_CALLS = 200
MAX_ATTEMPTS = 32
COMMAND_CHARS = 8_000
OUTPUT_CHARS = 6_000
PROMPT_CHARS = 16_000


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing recorded timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Recorded timestamp needs a timezone")
    return result.timestamp()


def target_label(target):
    if target.get("runner") == "oracle":
        return "Reference solution"
    if target.get("runner") == "nop":
        return "Unchanged baseline"
    model = target.get("model", "")
    match = re.fullmatch(r"openai\.gpt-\d+-([a-z]+)", model)
    return match[1].capitalize() if match else target.get("id") or model or "Unknown model"


def _text(value, limit):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True)
    text = redact(text)
    return text[:limit], len(text) > limit


def _title(tool, command):
    if tool == "apply_patch":
        return "Apply patch"
    if tool == "write_stdin":
        return "Check running command"
    first = next((line.strip() for line in command.splitlines() if line.strip()), tool)
    return first if len(first) <= 65 else first[:62] + "…"


def _session(path, root, origin, agent_end):
    """Read only user task messages and tool exchanges; exclude reasoning/config."""
    relative = path.relative_to(root).as_posix()
    path = _evidence_path(root, relative)
    if path.stat().st_size > MAX_SESSION_BYTES:
        raise ValueError(f"Session exceeds the {MAX_SESSION_BYTES // 1024 // 1024} MiB read limit")
    with path.open("rb") as stream:
        raw = stream.read(MAX_SESSION_BYTES + 1)
    if len(raw) > MAX_SESSION_BYTES:
        raise ValueError("Session grew beyond the read limit")
    digest = hashlib.sha256(raw).hexdigest()
    calls, outputs, prompts, warnings = {}, {}, [], []
    skipped_lines = 0
    calls_clipped = False
    for line_number, line in enumerate(raw.splitlines(), 1):
        if len(line) > MAX_EVENT_BYTES:
            skipped_lines += 1
            continue
        try:
            event = json.loads(line, parse_constant=_reject_constant)
            payload = event.get("payload", {})
            if event.get("type") != "response_item" or not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            source = f"{relative}#L{line_number}"
            if kind == "message" and payload.get("role") == "user":
                parts = payload.get("content", [])
                text = "\n".join(part["text"] for part in parts if isinstance(part, dict)
                                 and part.get("type") in {"input_text", "text"}
                                 and isinstance(part.get("text"), str))
                # Session environment wrappers aren't benchmark task prompts.
                if text.strip() and not text.lstrip().startswith("<environment_context>"):
                    prompt, clipped = _text(text, PROMPT_CHARS)
                    prompts.append({"text": prompt, "source": source, "clipped": clipped})
                continue
            if kind not in {"function_call", "custom_tool_call",
                            "function_call_output", "custom_tool_call_output"}:
                continue
            when = timestamp(event.get("timestamp")) - origin
            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                continue
            if kind in {"function_call_output", "custom_tool_call_output"}:
                outputs[call_id] = (when, payload.get("output", ""), source)
                continue
            if call_id in calls:
                continue
            if len(calls) >= MAX_CALLS:
                calls_clipped = True
                continue
            tool = payload.get("name")
            if not isinstance(tool, str):
                continue
            arguments = payload.get("arguments", payload.get("input", ""))
            if kind == "function_call" and isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    pass
            command = arguments.get("cmd", arguments) if isinstance(arguments, dict) else arguments
            command, clipped = _text(command, COMMAND_CHARS)
            calls[call_id] = {
                "id": call_id, "tool": tool, "start": when, "end": agent_end,
                "command": command, "commandClipped": clipped, "output": "",
                "outputClipped": False, "exitCode": None, "sessionRunning": False,
                "replyRecorded": False, "notice": None,
                "title": _title(tool, command), "source": source,
            }
        except (ValueError, TypeError, AttributeError, KeyError):
            skipped_lines += 1
    for call_id, call in calls.items():
        if call_id not in outputs:
            continue
        when, output, source = outputs[call_id]
        if when < call["start"]:
            warnings.append(f"Reply precedes call {call_id}; reply timing was not used.")
            continue
        output = output if isinstance(output, str) else json.dumps(output, ensure_ascii=True)
        exit_code = re.search(r"(?:Process exited with code|Exit code:)\s*(-?\d+)", output)
        content = output.split("Output:\n", 1)[-1]
        text, clipped = _text(content, OUTPUT_CHARS)
        call.update(end=when, replyRecorded=True, output=text, outputClipped=clipped,
                    outputSource=source, sessionRunning="Process running with session ID" in output,
                    exitCode=int(exit_code[1]) if exit_code else None)
        if re.search(r"(?:command not found|No such file or directory)", content):
            call["notice"] = "The saved output reports a missing command or file; check the output and exit status."
    if skipped_lines:
        warnings.append(f"{skipped_lines} malformed, oversized, or untimed session events were skipped.")
    if calls_clipped:
        warnings.append(f"Tool list limited to the first {MAX_CALLS} calls per session.")
    return {
        "calls": sorted(calls.values(), key=lambda call: call["start"]),
        "prompts": prompts, "warnings": warnings,
        "session": {"source": relative, "sha256": digest},
    }


def _attempt_activity(root, row, origin, budget):
    upstream = row.get("upstream") or {}
    if row.get("target", {}).get("runner") != "codex" or upstream.get("harness") != "harbor":
        raise ValueError("Replay currently supports saved Harbor/Codex sessions")
    result = _evidence_path(root, f"attempts/{row['attempt_id']}/{upstream['result']}")
    with result.open("rb") as stream:
        raw = stream.read(MAX_SESSION_BYTES + 1)
    if len(raw) > MAX_SESSION_BYTES:
        raise ValueError("Upstream result exceeds the evidence read limit")
    if row.get("trace_sha256") and row.get("trace") == result.relative_to(root).as_posix():
        if hashlib.sha256(raw).hexdigest() != row["trace_sha256"]:
            raise ValueError("Saved upstream result no longer matches its recorded checksum")
    trial = json.loads(raw, parse_constant=_reject_constant)
    phase = trial.get("agent_execution") or {}
    start, end = timestamp(phase.get("started_at")), timestamp(phase.get("finished_at"))
    if end < start:
        raise ValueError("Agent finish precedes its start")
    paths = sorted((result.parent / "agent" / "sessions").glob("**/*.jsonl"))
    if not paths:
        raise ValueError("No saved Codex sessions found beside the upstream result")
    sessions, calls, prompts, warnings = [], [], [], []
    for path in paths[:MAX_SESSIONS]:
        parsed = _session(path, root, origin, end - origin)
        sessions.append(parsed["session"])
        calls.extend(parsed["calls"])
        prompts.extend(parsed["prompts"])
        warnings.extend(parsed["warnings"])
    if len(paths) > MAX_SESSIONS:
        warnings.append(f"Replay limited to the first {MAX_SESSIONS} saved sessions.")
    # Multiple sessions can reuse call IDs. Retain the raw ID for inspection.
    for call in calls:
        call["recordedCallId"] = call["id"]
        call["id"] = fingerprint([call["source"], call["id"]])[:20]
    calls.sort(key=lambda call: call["start"])
    if not calls:
        raise ValueError("No timestamped tool calls were recorded in these sessions")
    if len(calls) > MAX_CALLS:
        warnings.append(f"Replay limited to the first {MAX_CALLS} tool calls for this attempt.")
    prompt = "\n\n".join(item["text"] for item in prompts)
    prompt, prompt_clipped = _text(prompt, PROMPT_CHARS)
    return {
        "id": "m-" + fingerprint(row["attempt_id"])[:16],
        "name": target_label(row["target"]), "model": row["target"]["model"],
        "region": row["target"].get("region"), "attempt": row["attempt_id"],
        "task": row["task_id"], "repetition": row.get("repetition"),
        "agent": {"start": start - origin, "end": end - origin},
        "passed": row.get("success") is True, "budget": budget,
        "prompt": prompt, "promptClipped": prompt_clipped or any(p["clipped"] for p in prompts),
        "promptSources": [p["source"] for p in prompts], "sessions": sessions,
        "calls": calls[:MAX_CALLS], "warnings": warnings,
    }


def activity_data(source, *, attempt=None):
    source = Path(source).expanduser().resolve()
    run = load_run(source)
    root = source.parent
    rows = [row for row in run["attempts"] if attempt is None or row.get("attempt_id") == attempt]
    if attempt is not None and len(rows) != 1:
        raise ValueError(f"Expected one attempt named {attempt!r}; found {len(rows)}")
    origin = timestamp(run.get("started_at"))
    budget = run.get("experiment", {}).get("limits", {}).get("timeout_seconds")
    models, unavailable = [], []
    for row in rows[:MAX_ATTEMPTS]:
        try:
            models.append(_attempt_activity(root, row, origin, budget))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            unavailable.append({"attempt": row.get("attempt_id"), "reason": str(exc)})
    if len(rows) > MAX_ATTEMPTS:
        unavailable.append({"attempt": None, "reason": f"Replay limited to {MAX_ATTEMPTS} attempts; "
                            "use --attempt to inspect an omitted attempt."})
    counts = Counter(model["name"] for model in models)
    for model in models:
        if counts[model["name"]] > 1:
            model["name"] += f" · {model['task']} · #{model['repetition']}"
    return {
        "version": 1, "mode": "replay", "runId": run["run_id"], "source": str(source),
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "name": run.get("name") or run["run_id"], "startedAt": run.get("started_at"),
        "synthetic": run.get("synthetic", False), "validationOnly": run.get("validation_only", False),
        "models": models, "unavailable": unavailable,
        "scope": "Recorded public task prompts and tool exchanges only. No live monitoring.",
    }


def replay_fragment(data):
    return (ASSETS / "replay.html").read_text().replace("__REPLAY_DATA__", embedded_json(data))


def write_replay(source, directory, *, inline=False, attempt=None):
    data = activity_data(source, attempt=attempt)
    return write_view(directory, "REPLAY", data, replay_fragment(data), inline=inline)
