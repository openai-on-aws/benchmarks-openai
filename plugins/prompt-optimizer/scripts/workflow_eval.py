#!/usr/bin/env python3
"""Compare models or optimize a text-task prompt from Codex, locally or on AgentCore."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ENGINE = PLUGIN_ROOT / "engine"


def require_study_python():
    """Reject unsupported interpreters before importing pinned engine dependencies."""
    if sys.version_info[:2] != (3, 12):
        version = ".".join(map(str, sys.version_info[:3]))
        raise ValueError(
            f"Study commands require Python 3.12; found Python {version} at {sys.executable}. "
            "Create a workspace environment with python3.12 -m venv /path/to/workspace/.venv, "
            "then use that environment's Python to install "
            f"-r {PLUGIN_ROOT / 'requirements.txt'} and rerun this command. "
            "Keep the pinned dependency versions unchanged. "
            "The recorded demo and saved dashboard do not require engine dependencies."
        )


def writable_workspace(path: str) -> Path:
    """Installed plugin files are inputs; user projects and results live elsewhere."""
    resolved = Path(path).expanduser().resolve()
    if resolved.is_relative_to(PLUGIN_ROOT):
        raise ValueError("Choose a project/output directory outside the installed plugin.")
    return resolved


def decision_paths(patch, decision_file):
    """Persist file references relative to the answer file, independent of cwd."""
    from copy import deepcopy
    value = deepcopy(patch)
    if not isinstance(value, dict):
        raise ValueError('Decisions must be a JSON object')
    base = Path(decision_file).expanduser().resolve().parent
    def resolve(path):
        if not isinstance(path, str):
            raise ValueError('File references must be path strings')
        path = Path(path).expanduser()
        return str((base / path if not path.is_absolute() else path).resolve())
    for key in ('connection', 'label_review_file'):
        if value.get(key) is not None:
            value[key] = resolve(value[key])
    quality = value.get('quality')
    if isinstance(quality, dict) and quality.get('reference_file') is not None:
        quality['reference_file'] = resolve(quality['reference_file'])
    return value


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
    for action in ("start", "inspect", "configure", "review-template", "prepare", "plan", "preflight", "run", "status", "results"):
        command = commands.add_parser(action)
        command.add_argument("--project", required=True, help="User-owned evaluation directory")
        if action == "start":
            command.add_argument("--goal", default="Compare customer workflow quality, model cost and latency.")
            command.add_argument("--mode", choices=("compare", "optimize"), default=None,
                                 help="Compare models with a fixed prompt (default), or generate and evaluate prompt revisions")
        elif action == "inspect":
            command.add_argument("--input", action="append", help="Evaluation log or manual JSON/JSONL file; repeat as needed")
            command.add_argument("--sources", help="JSON array of paths with optional per-file mappings")
        elif action == "configure":
            command.add_argument("--decisions", required=True, help="JSON containing the user's newly resolved choices")
        elif action == "review-template":
            command.add_argument("--output", required=True, help="New review template for a human to complete; does not approve labels")
        elif action == "preflight":
            command.add_argument("--backend", choices=("local", "agentcore"), default="local")
            command.add_argument("--deployment-receipt", help="Runtime upgrade receipt for implementation verification")
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
            require_study_python()
            sys.path.insert(0, str(ENGINE))
            from customer_eval import project

            if args.action == "start":
                existing = (directory / "project.json").exists()
                state = project.start(directory, args.goal)
                if not existing:
                    value = project.configure(directory, {"optimization_enabled": args.mode == "optimize"})
                else:
                    if args.mode is not None and state['decisions'].get('optimization_enabled', True) != (args.mode == 'optimize'):
                        raise ValueError('Existing project has a different mode. Use configure before preparation, or create a new project revision.')
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
                value = project.configure(directory, decision_paths(project.read(args.decisions), args.decisions))
            elif args.action == "review-template":
                from customer_eval.label_review import write_review_template
                destination = writable_workspace(args.output)
                if destination.exists():
                    raise ValueError('Review output already exists; preserve it or choose a new file.')
                cases, _, _ = project.prepare_cases(directory, apply_reviews=False)
                write_review_template(cases, destination)
                value = {'status': 'review_template_ready', 'path': str(destination), 'cases': len(cases),
                         'message': 'A human must review individual references and complete the attestations. This template grants no review status.'}
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
            elif args.action == "preflight":
                from customer_eval.preflight import inspect
                value = inspect(directory, backend=args.backend, deployment_receipt=args.deployment_receipt)
            elif args.backend == "local":
                value = run_local(project, directory) if args.action == "run" else local_results(project, directory)
            elif args.action == "status" and not (directory / "submission").exists():
                value = project.status(directory)
            else:
                from customer_eval import project_cloud

                value = project_cloud.run(directory, live=True) if args.action == "run" else project_cloud.refresh(directory)
        print(json.dumps(value, indent=2, ensure_ascii=False))
        if args.action == "preflight" and value.get("ready_for_qualification") is not True:
            return 1
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "needs_attention", "error_type": type(exc).__name__, "message": str(exc),
            "recovery": "Existing inputs and receipts are retained. Inspect uncertain jobs before retrying paid work.",
        }, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
