#!/usr/bin/env python3
"""A small, reproducible ARC-AGI-2 public evaluation pilot on Amazon Bedrock."""

import argparse
import json
from pathlib import Path
import random
import re
import subprocess
import sys

from arc_common import (BedrockSession, RunLimit, add_common_arguments, digest,
                        finish, new_run, positive_int, write_json)

DATASET_REVISION = "f3283f727488ad98fe575ea6a5ac981e4a188e49"
PROMPT_VERSION = "arc2-grids-v1"


def valid_grid(grid):
    return (
        isinstance(grid, list) and 1 <= len(grid) <= 30
        and all(isinstance(row, list) and 1 <= len(row) <= 30 for row in grid)
        and len({len(row) for row in grid}) == 1
        and all(type(cell) is int and 0 <= cell <= 9 for row in grid for cell in row)
    )


def parse_grid(text):
    text = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if match:
        text = match.group(1)
    try:
        grid = json.loads(text)
    except (ValueError, TypeError):
        return None
    return grid if valid_grid(grid) else None


def prompt_for(task, test_index):
    # Explicit allow-list: never pass the test output or the original task dict.
    examples = [{"input": pair["input"], "output": pair["output"]} for pair in task["train"]]
    payload = {"examples": examples, "test_input": task["test"][test_index]["input"]}
    return (
        "Infer the transformation from the example input/output grids. Apply it to "
        "the test input. Return only the output grid as a JSON array of integer rows. "
        "Grid dimensions are part of the answer; colours are integers 0 through 9.\n"
        + json.dumps(payload, separators=(",", ":"))
    )


def load_tasks(root, revision, n, seed):
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if actual != revision:
        raise ValueError(f"dataset revision {actual} does not match requested {revision}")
    dirty = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain", "--", "data/evaluation"], text=True)
    if dirty:
        raise ValueError("evaluation data has local changes")
    paths = sorted((root / "data/evaluation").glob("*.json"))
    if not 1 <= n <= len(paths):
        raise ValueError(f"choose --n between 1 and {len(paths)}")
    selected = sorted(random.Random(seed).sample(paths, n))
    tasks, metadata = [], []
    for path in selected:
        content = path.read_bytes()
        task = json.loads(content)
        if not task.get("train") or not task.get("test"):
            raise ValueError(f"empty task {path.stem}")
        if not all(valid_grid(pair[key]) for group in ("train", "test")
                   for pair in task[group] for key in ("input", "output")):
            raise ValueError(f"invalid task grids {path.stem}")
        tasks.append((path.stem, task))
        metadata.append({"id": path.stem, "sha256": digest(content),
                         "test_inputs": len(task["test"])})
    return tasks, {"repository": "https://github.com/arcprize/ARC-AGI-2",
                   "revision": actual, "split": "evaluation", "seed": seed, "tasks": metadata}


def evaluate_task(session, task_id, task, attempts, checkpoint):
    row = {"id": task_id, "status": "running", "test_inputs": []}
    checkpoint(row)
    for index, pair in enumerate(task["test"]):
        test = {"index": index, "attempts": []}
        row["test_inputs"].append(test)
        for _ in range(attempts):
            # Independent attempts, with no correctness feedback or early exit.
            session.reset_context()
            result = session.call([{"role": "user", "content": prompt_for(task, index)}])
            grid = parse_grid(result["text"]) if result["status"] == "completed" else None
            result.update(prediction=grid, correct=grid == pair["output"])
            test["attempts"].append(result)
            checkpoint(row)
    row["solved_first_attempt"] = all(t["attempts"][0]["correct"] for t in row["test_inputs"])
    row["solved"] = all(any(a["correct"] for a in t["attempts"]) for t in row["test_inputs"])
    row["status"] = "completed"
    checkpoint(row)
    return row


def summarize(rows, planned):
    complete = [r for r in rows if r["status"] == "completed"]
    solved = sum(r["solved"] for r in complete)
    return {"planned_tasks": planned, "completed_tasks": len(complete), "solved_tasks": solved,
            "task_accuracy": solved / planned if len(complete) == planned else None,
            "first_attempt_accuracy": sum(r["solved_first_attempt"] for r in complete) / planned
            if len(complete) == planned else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--dataset-dir", type=Path, required=True, help="official ARC-AGI-2 git checkout")
    parser.add_argument("--revision", default=DATASET_REVISION)
    parser.add_argument("--n", type=positive_int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--attempts", type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    session = None
    try:
        tasks, provenance = load_tasks(args.dataset_dir, args.revision, args.n, args.seed)
        run = new_run(args, "arc-agi-2")
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        parser.error(str(e))
    run.update(dataset=provenance, prompt_version=PROMPT_VERSION, attempts_per_test=args.attempts)
    run["planned_calls"] = sum(len(task["test"]) for _, task in tasks) * args.attempts
    print(f"ARC-AGI-2: {len(tasks)} tasks, {run['planned_calls']} calls, {args.backend}/{args.model}")
    exit_code = 0
    try:
        if args.execute:
            session = BedrockSession(args)
            run["status"] = "running"
            for task_id, task in tasks:
                def checkpoint(row):
                    if not run["results"] or run["results"][-1]["id"] != row["id"]:
                        run["results"].append(row)
                    write_json(args.output_dir / "manifest.json", run)
                evaluate_task(session, task_id, task, args.attempts, checkpoint)
            run["status"] = "completed"
    except RunLimit as e:
        run.update(status="limited", error=str(e))
        exit_code = 2
    except KeyboardInterrupt:
        run.update(status="interrupted")
        exit_code = 130
    except Exception as e:
        # Top-level: preserve partial evidence and surface a nonzero exit.
        run.update(status="failed", error_type=type(e).__name__, error=str(e))
        exit_code = 1
    finally:
        run["summary"] = summarize(run["results"], len(tasks))
        finish(run, args, session)
    print(f"{run['status']}: {args.output_dir / 'manifest.json'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
