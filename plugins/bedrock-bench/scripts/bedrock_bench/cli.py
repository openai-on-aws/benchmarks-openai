"""CLI entry point; planning and offline demonstrations require no SDKs."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

from .config import Experiment, Limits, Target
from .engine import execute
from .report import write_report
from .tasks import TASK_IDS


def demo_experiment():
    return Experiment(
        name="Synthetic agent task economics",
        targets=[Target("fixture-efficient", "demo", "synthetic", "fixture-efficient"),
                 Target("fixture-imperfect", "demo", "synthetic", "fixture-imperfect")],
        tasks=list(TASK_IDS),
    )


def doctor():
    return {
        "python": sys.version.split()[0],
        "executables": {runner: shutil.which(runner) for runner in ("codex", "opencode")},
        "optional_packages": {name: importlib.util.find_spec(name) is not None
                              for name in ("openai", "aws_bedrock_token_generator", "boto3")},
        "credential_environment_present": {name: bool(os.environ.get(name)) for name in
            ("OPENAI_API_KEY", "OPENAI_API_KEY_SAAS", "OPENROUTER_API_KEY", "AWS_BEARER_TOKEN_BEDROCK")},
        "note": "Presence only; this does not authenticate, validate model access, or make network calls.",
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "_worker":
        from .native import worker
        return worker(argv[1], argv[2])
    parser = argparse.ArgumentParser(description="Bedrock Bench: cost and success of agent tasks")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local tools and credential presence without network access")
    sub.add_parser("tasks", help="List the starter tasks")
    init = sub.add_parser("init", help="Write a starter experiment for an explicitly selected model")
    init.add_argument("--runner", choices=["native", "codex", "opencode"], required=True)
    init.add_argument("--provider", required=True)
    init.add_argument("--model", required=True)
    init.add_argument("--region")
    init.add_argument("--reasoning-effort")
    init.add_argument("--repetitions", type=int, default=1)
    init.add_argument("--timeout-seconds", type=float, default=120)
    init.add_argument("--out", required=True)
    plan = sub.add_parser("plan", help="Validate an experiment and display its bounds")
    plan.add_argument("experiment")
    run = sub.add_parser("run", help="Plan by default; --execute enables paid calls")
    run.add_argument("experiment")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--out", default="bench-results")
    demo = sub.add_parser("demo", help="Run synthetic fixtures through scoring and reporting")
    demo.add_argument("--out", default="bench-results")
    report = sub.add_parser("report", help="Compare run.json files with identical task protocols")
    report.add_argument("runs", nargs="+")
    report.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            print(json.dumps(doctor(), indent=2))
        elif args.command == "tasks":
            print("\n".join(TASK_IDS))
        elif args.command == "init":
            experiment = Experiment(
                name="Agent task comparison",
                targets=[Target("candidate", args.runner, args.provider, args.model,
                                region=args.region, reasoning_effort=args.reasoning_effort)],
                tasks=list(TASK_IDS), repetitions=args.repetitions,
                limits=Limits(timeout_seconds=args.timeout_seconds),
            )
            # Never overwrite a user's existing experiment.
            with Path(args.out).open("x") as output:
                output.write(json.dumps(experiment.to_dict(), indent=2) + "\n")
            print(Path(args.out).resolve())
        elif args.command == "report":
            runs = [json.loads(Path(path).read_text()) for path in args.runs]
            write_report(runs, args.out)
            print(Path(args.out).resolve() / "REPORT.md")
        else:
            experiment = demo_experiment() if args.command == "demo" else Experiment.load(args.experiment)
            if args.command == "plan" or (args.command == "run" and not args.execute):
                print(json.dumps(experiment.plan(), indent=2))
            else:
                def progress(index, total, row):
                    print(f"[{index}/{total}] {row['target']['id']} / {row['task_id']}: {row['status']}", file=sys.stderr)
                root, _ = execute(experiment, args.out, allow_live=args.command == "run", progress=progress)
                print(root / "REPORT.md")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Bedrock Bench: {exc}", file=sys.stderr)
        return 2
    return 0
