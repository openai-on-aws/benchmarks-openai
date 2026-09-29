#!/usr/bin/env python3
"""Generate deterministic, synthetic diagnosis fixtures without running agents."""

import argparse
import hashlib
import json
from pathlib import Path


def _write(path, value, *, jsonl=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("\n".join(json.dumps(row, sort_keys=True) for row in value) + "\n" if jsonl
            else json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
    return hashlib.sha256(text.encode()).hexdigest()


def make_fixture(directory):
    """Return two synthetic run paths. Refuse an existing fixture directory."""
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=False)
    targets = [
        {"id": name, "runner": "demo", "provider": "synthetic", "model": name}
        for name in ("fixture-a", "fixture-b")
    ]
    specs = [
        ("completed", True, 0.1, 0.1, [
            {"type": "tool", "result": {"error": "FileNotFoundError: temporary file absent"}},
            {"type": "completed"},
        ]),
        ("task_failed", False, 0.2, 0.2, [{"type": "completed"}]),
        ("timeout", False, None, 0.3, [{"type": "error", "error": "AgentTimeoutError"}]),
        ("runner_error", False, None, 0.1, [{"type": "error", "error": "ThrottlingException"}]),
        ("interrupted", False, None, 0.0, [{"type": "step"}]),
        ("completed", True, 0.1, 0.1, None),
    ]
    paths = []
    for protocol_index in (1, 2):
        run_id = f"diagnosis-synthetic-p{protocol_index}"
        root = directory / run_id
        attempts = []
        schedule = [
            {"target": target["id"], "task": task, "repetition": repetition}
            for repetition in (1, 2)
            for task in ("repair-a", "repair-b")
            for target in targets
        ]
        for index, (status, success, cost, known, trace) in enumerate(specs):
            slot = schedule[index]
            attempt_id = f"attempt-{index + 1}"
            row = {
                "attempt_id": attempt_id, "task_id": slot["task"], "repetition": slot["repetition"],
                "target": targets[index % 2], "runner_version": "synthetic-fixture-v1",
                "status": status, "success": success, "cost_usd": cost,
                "known_cost_subtotal_usd": known, "cost_basis": ["synthetic"],
                "accounting_complete": cost is not None,
                "grading": {"success": success},
                "trace": f"attempts/{attempt_id}/events.jsonl",
            }
            if trace is not None:
                row["trace_sha256"] = _write(root / row["trace"], trace, jsonl=True)
            attempts.append(row)
        run = {
            "schema_version": 1, "run_id": run_id,
            "synthetic": True, "validation_only": False, "status": "interrupted",
            "protocol_hash": f"synthetic-protocol-{protocol_index}",
            "experiment": {"targets": targets, "tasks": ["repair-a", "repair-b"],
                           "repetitions": 2, "suite": "diagnosis-fixture"},
            "schedule": schedule, "attempts": attempts,
        }
        source = root / "run.json"
        _write(source, run)
        paths.append(source)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="A new directory for synthetic fixture runs")
    arguments = parser.parse_args()
    try:
        paths = make_fixture(arguments.out)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Fixture could not be written: {exc}\n")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
