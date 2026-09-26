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
from .suites import (
    SUITES, SuiteExperiment, SuiteLimits, SuiteTarget, catalog,
    load_experiment, task_catalog,
)


def demo_experiment():
    return Experiment(
        name="Synthetic agent task economics",
        targets=[Target("fixture-efficient", "demo", "synthetic", "fixture-efficient"),
                 Target("fixture-imperfect", "demo", "synthetic", "fixture-imperfect")],
        tasks=list(TASK_IDS),
    )


def doctor(tools_dir=".bench-tools"):
    binaries = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return {
        "python": sys.version.split()[0],
        "executables": {runner: shutil.which(runner) for runner in
                        ("codex", "opencode", "docker", "uv", "harbor", "aws-bench")},
        "prepared_harnesses": {
            harness: (Path(tools_dir).resolve() / harness / binaries / (harness + suffix)).is_file()
            for harness in ("harbor", "aws-bench")
        },
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
    doctor_parser = sub.add_parser("doctor", help="Check local tools and credential presence without network access")
    doctor_parser.add_argument("--tools-dir", default=".bench-tools")
    sub.add_parser("suites", help="List packaged and upstream benchmark suites")
    tasks = sub.add_parser("tasks", help="List exact task names in a packaged suite catalog")
    tasks.add_argument("--suite", choices=list(SUITES), default="starter")
    init = sub.add_parser("init", help="Write an experiment for an explicitly selected suite and model")
    init.add_argument("--suite", choices=list(SUITES), default="starter")
    init.add_argument("--runner", choices=["native", "codex", "opencode"], required=True)
    init.add_argument("--provider", required=True)
    init.add_argument("--model", required=True)
    init.add_argument("--region")
    init.add_argument("--reasoning-effort")
    init.add_argument("--repetitions", type=int, default=1)
    init.add_argument("--timeout-seconds", type=float)
    init.add_argument("--task", action="append", help="Exact task name; repeat to select several")
    init.add_argument("--all-tasks", action="store_true")
    init.add_argument("--tools-dir", default=".bench-tools")
    init.add_argument("--env-name")
    init.add_argument("--environment-profile")
    init.add_argument("--agent-version")
    init.add_argument("--skill", action="append", default=[], help="Local skill directory for Harbor")
    init.add_argument("--out", required=True)
    prepare = sub.add_parser("prepare", help="Plan installation of a suite's isolated runtime")
    prepare.add_argument("--suite", choices=list(SUITES), required=True)
    prepare.add_argument("--tools-dir", default=".bench-tools")
    prepare.add_argument("--execute", action="store_true")
    plan = sub.add_parser("plan", help="Validate an experiment and display its bounds")
    plan.add_argument("experiment")
    run = sub.add_parser("run", help="Plan by default; --execute enables paid calls")
    run.add_argument("experiment")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--out", default="bench-results")
    demo = sub.add_parser("demo", help="Run synthetic fixtures through scoring and reporting")
    demo.add_argument("--out", default="bench-results")
    smoke = sub.add_parser("smoke", help="Validate a suite's default task using its reference solution")
    smoke.add_argument("--suite", choices=["aws-cdk-smoke", "terminal-bench", "swe-bench"], default="aws-cdk-smoke")
    smoke.add_argument("--tools-dir", default=".bench-tools")
    smoke.add_argument("--execute", action="store_true")
    smoke.add_argument("--out", default="bench-results")
    aws_env = sub.add_parser("aws-env", help="Plan or execute an AWS-Bench environment operation")
    aws_env.add_argument("action", choices=["show", "init", "setup", "verify", "reset", "cleanup"])
    aws_env.add_argument("experiment")
    aws_env.add_argument("--execute", action="store_true")
    aws_env.add_argument("--out", default="bench-results")
    report = sub.add_parser("report", help="Compare run.json files with identical task protocols")
    report.add_argument("runs", nargs="+")
    report.add_argument("--out", required=True)
    report.add_argument("--format", choices=["markdown", "html", "inline"], default="markdown",
                        help="Report to print/open; HTML, Markdown, and JSON are always saved")
    saved_runs = sub.add_parser("runs", help="List saved runs without executing benchmarks")
    saved_runs.add_argument("directory", nargs="?", default="bench-results")
    library = sub.add_parser("library", help="Build an offline, searchable library of saved runs")
    library.add_argument("directory", nargs="?", default="bench-results")
    library.add_argument("--out", required=True)
    library.add_argument("--format", choices=["html", "inline"], default="html")
    library.add_argument("--limit", type=int, default=50, help="Most recent runs to include (1–1000)")
    library.add_argument("--replay-limit", type=int, default=5, help="Replays to embed (0–50)")
    replay = sub.add_parser("replay", help="Show recorded Harbor/Codex prompts and tool activity")
    replay.add_argument("run")
    replay.add_argument("--out", required=True)
    replay.add_argument("--attempt", help="Restrict replay to one saved attempt")
    replay.add_argument("--format", choices=["html", "inline"], default="html")
    inspect = sub.add_parser("inspect", help="Read one saved attempt and bounded evidence previews")
    inspect.add_argument("run")
    inspect.add_argument("--attempt", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            print(json.dumps(doctor(args.tools_dir), indent=2))
        elif args.command == "suites":
            print(json.dumps(catalog(), indent=2))
        elif args.command == "tasks":
            print("\n".join(task_catalog(args.suite)))
        elif args.command == "runs":
            from .explorer import list_runs
            print(json.dumps(list_runs(args.directory), indent=2, allow_nan=False))
        elif args.command == "library":
            from .library import write_library
            print(write_library(args.directory, args.out, inline=args.format == "inline",
                                limit=args.limit, replay_limit=args.replay_limit))
        elif args.command == "replay":
            from .activity import write_replay
            print(write_replay(args.run, args.out, inline=args.format == "inline", attempt=args.attempt))
        elif args.command == "inspect":
            from .explorer import inspect_attempt
            print(json.dumps(inspect_attempt(args.run, args.attempt), indent=2, allow_nan=False))
        elif args.command == "prepare":
            from .tooling import prepare as prepare_tools
            print(json.dumps(prepare_tools(args.suite, args.tools_dir, execute=args.execute), indent=2))
        elif args.command == "aws-env":
            from .upstream import aws_environment_action
            experiment = load_experiment(args.experiment)
            if not isinstance(experiment, SuiteExperiment):
                raise ValueError("aws-env requires an aws-bench suite experiment")
            result = aws_environment_action(experiment, args.action, args.out, execute=args.execute)
            print(json.dumps(result, indent=2))
            if args.execute and (result.get("exit_code") != 0 or result.get("timed_out")):
                return 1
        elif args.command == "init":
            if args.task and args.all_tasks:
                raise ValueError("Choose --task or --all-tasks")
            selected = args.task
            if selected is None:
                selected = (list(task_catalog(args.suite)) if args.all_tasks or args.suite == "starter"
                            else [SUITES[args.suite]["default_task"]])
            if args.suite == "starter":
                if args.env_name or args.environment_profile or args.skill or args.agent_version:
                    raise ValueError("Environment, agent-version and skill options require a repository suite")
                experiment = Experiment(
                    name="Agent task comparison",
                    targets=[Target("candidate", args.runner, args.provider, args.model,
                                    region=args.region, reasoning_effort=args.reasoning_effort)],
                    tasks=selected, repetitions=args.repetitions,
                    limits=Limits(timeout_seconds=120 if args.timeout_seconds is None else args.timeout_seconds),
                )
            else:
                experiment = SuiteExperiment(
                    name=f"{args.suite} agent comparison", suite=args.suite,
                    targets=[SuiteTarget(
                        "candidate", args.runner, args.provider, args.model,
                        region=args.region, reasoning_effort=args.reasoning_effort,
                        agent_version=args.agent_version,
                        skills=[str(Path(p).expanduser().resolve()) for p in args.skill])],
                    tasks=selected, repetitions=args.repetitions,
                    limits=SuiteLimits(timeout_seconds=300 if args.timeout_seconds is None else args.timeout_seconds),
                    tools_dir=str(Path(args.tools_dir).resolve()),
                    aws_environment=args.env_name, environment_profile=args.environment_profile,
                )
            # Never overwrite a user's existing experiment.
            with Path(args.out).open("x") as output:
                output.write(json.dumps(experiment.to_dict(), indent=2) + "\n")
            print(Path(args.out).resolve())
        elif args.command == "report":
            from .explorer import load_run
            runs = [load_run(path) for path in args.runs]
            write_report(runs, args.out, sources=args.runs, inline=args.format == "inline")
            filename = {"markdown": "REPORT.md", "html": "REPORT.html", "inline": "REPORT.inline.html"}[args.format]
            print(Path(args.out).resolve() / filename)
        else:
            if args.command == "demo":
                experiment = demo_experiment()
            elif args.command == "smoke":
                targets = [SuiteTarget("reference", "oracle", "reference", "none")]
                if args.suite == "aws-cdk-smoke":
                    targets.insert(0, SuiteTarget("broken", "nop", "reference", "none"))
                experiment = SuiteExperiment(
                    name=f"{args.suite} reference smoke", suite=args.suite,
                    targets=targets, tasks=[SUITES[args.suite]["default_task"]],
                    limits=SuiteLimits(verifier_timeout_seconds=900 if args.suite != "aws-cdk-smoke" else 120),
                    tools_dir=str(Path(args.tools_dir).resolve()),
                )
            else:
                experiment = load_experiment(args.experiment)
            if args.command == "plan" or (args.command in {"run", "smoke"} and not args.execute):
                print(json.dumps(experiment.plan(), indent=2))
            else:
                def progress(index, total, row):
                    print(f"[{index}/{total}] {row['target']['id']} / {row['task_id']}: {row['status']}", file=sys.stderr)
                if isinstance(experiment, SuiteExperiment):
                    from .upstream import execute_suite
                    root, data = execute_suite(experiment, args.out, allow_live=True, progress=progress)
                else:
                    root, data = execute(experiment, args.out, allow_live=args.command == "run", progress=progress)
                print(root / "REPORT.md")
                if args.command == "smoke":
                    outcomes = {row["target"]["id"]: row for row in data["attempts"]}
                    if (not outcomes["reference"]["success"]
                            or ("broken" in outcomes and outcomes["broken"]["status"] != "task_failed")):
                        return 1
                elif any(row["status"] in {"runner_error", "timeout", "incomplete"} for row in data["attempts"]):
                    return 1
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Bedrock Bench: {exc}", file=sys.stderr)
        return 2
    return 0
