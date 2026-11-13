#!/usr/bin/env python3
"""Compare invoice extraction or ticket triage in Codex, locally, or on AgentCore."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ENGINE = PLUGIN_ROOT / "engine"


def writable_workspace(path: str) -> Path:
    """Installed plugin files are inputs; user projects and results live elsewhere."""
    resolved = Path(path).expanduser().resolve()
    if resolved.is_relative_to(PLUGIN_ROOT):
        raise ValueError("Choose a project/output directory outside the installed plugin.")
    return resolved


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    demo = commands.add_parser("demo", help="Copy the recorded invoice dashboard; no model or AWS calls")
    demo.add_argument("--output", required=True, help="New directory for the recorded report")
    dashboard = commands.add_parser("dashboard", help="Render an interactive dashboard from saved evidence; no model or AWS calls")
    dashboard.add_argument("--project", required=True, help="Directory containing saved local/AgentCore reports, or a single report.json")
    dashboard.add_argument("--output", required=True, help="HTML file outside the installed plugin")
    dashboard.add_argument("--title", help="Display title for this comparison")
    dashboard.add_argument("--force", action="store_true", help="Replace an existing dashboard HTML file")
    for action in ("start", "inspect", "configure", "prepare", "plan", "run", "status", "results"):
        command = commands.add_parser(action)
        command.add_argument("--project", required=True, help="User-owned evaluation directory")
        if action == "start":
            command.add_argument("--goal", default="Compare invoice extraction quality, model cost and latency with one fixed prompt.")
        elif action == "inspect":
            command.add_argument("--input", action="append", help="Evaluation log or manual JSON/JSONL file; repeat as needed")
            command.add_argument("--sources", help="JSON array of paths with optional per-file mappings")
        elif action == "configure":
            command.add_argument("--decisions", required=True, help="JSON containing the user's newly resolved choices")
        elif action in ("run", "status", "results"):
            command.add_argument("--backend", choices=("local", "agentcore"), default="agentcore")
            if action == "run":
                command.add_argument("--live", action="store_true", help="Use the existing authorization for paid model calls")
    return result


def prepared_config(project, directory, *, live=False):
    state = project.read(directory / "project.json")
    if not state.get("prepared"):
        raise ValueError("Complete intake and prepare the study before running.")
    config = project.load_with_endpoints(directory / state["prepared"]["config"], live=live)
    if config["fingerprint"] != state["prepared"]["fingerprint"]:
        raise ValueError("Prepared inputs or engine changed. Create a new reviewed project revision.")
    return config


def run_local(project, directory):
    import fcntl
    from customer_eval.runner import Runner

    config = prepared_config(project, directory, live=True)
    with (directory / ".local-run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("This local project is already running. Inspect its saved results.") from exc
        runner = Runner(config, directory / "local", backend="local")
        try:
            runner.run(optimize=config["document"]["optimization"]["enabled"])
        finally:
            runner.close()
    return local_results(project, directory)


def local_results(project, directory):
    output = directory / "local"
    if not (output / "report.json").exists():
        return {"status": "no_local_report", "message": "No completed local report exists. Existing checkpoints are retained."}
    report = project.read(output / "report.json")
    return {
        "status": report["evaluation_status"],
        "backend": "local",
        "simulated": report["simulated"],
        "report": str(output / "decision.html"),
        "evidence": str(output / "report.json"),
        "fingerprint": report["fingerprint"],
    }


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == "demo":
            output = writable_workspace(args.output)
            # Refuse replacement, including an existing empty directory or symlink.
            if output.exists():
                raise ValueError("Demo output already exists. Open its index.html or choose a new directory.")
            shutil.copytree(PLUGIN_ROOT / "examples/recorded-invoice-comparison", output)
            from dashboard import render_dashboard

            interactive = render_dashboard(output, output / "dashboard.html")
            value = {
                "status": "recorded_report_ready",
                "recorded_on": "2026-11-13",
                "fresh_model_calls": 0,
                "report": str(output / "index.html"),
                "dashboard": interactive["report"],
                "scope": "Historical live measurements on synthetic, unreviewed invoice references.",
            }
        elif args.action == "dashboard":
            from dashboard import render_dashboard

            value = render_dashboard(Path(args.project), writable_workspace(args.output),
                                     title=args.title, force=args.force)
        else:
            directory = writable_workspace(args.project)
            # Reject accidental execution before loading any model/provider dependencies.
            if args.action == "run" and not args.live:
                raise ValueError("Running models requires the user's authorization and --live.")
            sys.path.insert(0, str(ENGINE))
            from customer_eval import project

            if args.action == "start":
                existing = (directory / "project.json").exists()
                state = project.start(directory, args.goal)
                if not existing:
                    value = project.configure(directory, {"optimization_enabled": False})
                else:
                    value = project.status(directory)
            elif args.action == "inspect":
                if args.sources and args.input:
                    raise ValueError("Use either --input or --sources, not both.")
                sources = project.read(args.sources) if args.sources else [{"path": p} for p in args.input or []]
                if not sources:
                    raise ValueError("Provide --input evaluation logs or a --sources mapping file.")
                state = project.read(directory / "project.json")
                value = project.inspect(directory, sources, prompt_location=state["decisions"].get("prompt_location"))
            elif args.action == "configure":
                value = project.configure(directory, project.read(args.decisions))
            elif args.action == "prepare":
                value = project.prepare(directory)
            elif args.action == "plan":
                from customer_eval.contract import cases

                config = prepared_config(project, directory)
                repetitions = config["document"]["execution"]["repetitions"]
                value = {
                    "fingerprint": config["fingerprint"],
                    "prompt_optimization": config["document"]["optimization"]["enabled"],
                    "models": {k: v["model_id"] for k, v in config["routes"].items()},
                    "distinct_cases": {s: len(cases(config, s)) for s in config["splits"]},
                    "repetitions": repetitions,
                    "fixed_prompt_evaluation_trials_per_backend": (
                        2 * repetitions * sum(len(cases(config, s)) for s in ("validation", "holdout"))
                        if not config["document"]["optimization"]["enabled"] else None
                    ),
                    "agentcore_route_checks": 2,
                    "budget_policy": config["document"]["runner"].get("budget_policy", "capped"),
                    "model_budget_usd": config["document"]["execution"]["budget_usd"],
                    "calls_made": 0,
                }
            elif args.backend == "local":
                value = run_local(project, directory) if args.action == "run" else local_results(project, directory)
            elif args.action == "status" and not (directory / "submission").exists():
                value = project.status(directory)
            else:
                from customer_eval import project_cloud

                value = project_cloud.run(directory, live=True) if args.action == "run" else project_cloud.refresh(directory)
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "needs_attention", "error_type": type(exc).__name__, "message": str(exc),
            "recovery": "Existing inputs and receipts are retained. Inspect uncertain jobs before retrying paid work.",
        }, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
