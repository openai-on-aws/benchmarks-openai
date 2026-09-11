#!/usr/bin/env python3
"""Plan or execute Astra across the runnable benchmark suites on main."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from quality.eval_utils import ASTRA_EFFORTS, ASTRA_MODELS

ROOT = Path(__file__).resolve().parent
SUITES = ("performance", "quick", "agentic", "gpqa", "aime", "hle",
          "deepsearchqa", "gdpval", "parity")
PERF_BACKENDS = {"mantle": "bedrock", "runtime": "bedrock-runtime", "saas": "openai"}


@dataclass
class Step:
    suite: str
    backend: str
    model: str
    command: list[str]
    result_group: str
    pattern: str


def build_plan(backends, suites, profile, effort, python, runtime_model):
    """No credentials, imports of API clients, or network calls while planning."""
    steps = []
    for backend in backends:
        model = runtime_model if backend == "runtime" else ASTRA_MODELS[backend]
        common = ["--backend", backend, "--model", model, "--effort", effort]
        commands = {
            "performance": ("performance/benchmark.py",
                ["--backend", PERF_BACKENDS[backend], "--model", model, "--effort", effort,
                 "--tag", f"astra-{profile}"] +
                (["1k", "--runs", "2", "--outputs", "2048"] if profile == "smoke"
                 else ["1k", "5k", "10k", "20k", "--runs", "25"]),
                "performance", "results_*.json"),
            "quick": ("quality/quick_evals.py", common + ["--concurrency", "1"] +
                (["--n", "2"] if profile == "smoke" else []), "quality", "quickeval_*.json"),
            "agentic": ("quality/agentic_evals.py", common +
                ["--suite", "all", "--repeats", "1" if profile == "smoke" else "5"],
                "quality", "agentic_*.json"),
            "gpqa": ("quality/gpqa_diamond.py", common +
                (["--max-questions", "2", "--repeats", "1"] if profile == "smoke" else []),
                "quality", "gpqa_diamond_*.json"),
            "aime": ("quality/aime_2025.py", common +
                (["--max-questions", "2", "--repeats", "1"] if profile == "smoke" else []),
                "quality", "aime_2024_*.json"),
            "hle": ("quality/hle.py", common +
                (["--max-questions", "2"] if profile == "smoke" else []),
                "quality", "hle_*.json"),
            "deepsearchqa": ("quality/deepsearchqa/run_deepsearchqa.py", common +
                (["--indices", "0,1"] if profile == "smoke" else ["--sample", "50"]),
                "quality", "deepsearchqa_*.json"),
            "gdpval": ("quality/gdpval_eval.py", common +
                ["--n", "2" if profile == "smoke" else "24"], "quality", "gdpval_*.json"),
            "parity": ("parity/run_parity.py", common, "parity", "results_*.txt"),
        }
        for suite in suites:
            script, flags, group, pattern = commands[suite]
            steps.append(Step(suite, backend, model, [python, script, *flags], group, pattern))
    return steps


def inspect_results(paths):
    """A process exiting zero is insufficient: runners also save API failures."""
    errors, incomplete, parity_failures = 0, 0, 0
    if not paths:
        raise RuntimeError("runner produced no result files")
    for path in paths:
        if path.suffix == ".txt":
            text = path.read_text()
            if "[PASS]" not in text:
                raise RuntimeError(f"no parity check passed: {path.name}")
            parity_failures += text.count("[FAIL]")
            continue
        data = json.loads(path.read_text())
        rows = data.get("results", [])
        if "raw" in data:
            rows = [row for runs in data["raw"].values() for row in runs]
        if not rows:
            raise RuntimeError(f"empty results: {path.name}")
        errors += sum(bool(row.get("error")) or row.get("status") == "failed" for row in rows)
        incomplete += sum(
            row.get("status", row.get("stop_reason")) == "incomplete"
            or row.get("outcome", row.get("stop_reason")) == "max_turns"
            for row in rows)
    return {"api_errors": errors, "incomplete": incomplete, "parity_failures": parity_failures}


def judge_commands(step, paths, python, profile):
    commands = []
    for path in paths:
        if step.suite == "deepsearchqa":
            commands.append([python, "quality/deepsearchqa/judge_deepsearchqa.py", "--file", str(path)])
        elif step.suite == "gdpval":
            commands.append([python, "quality/gdpval_eval.py", "--judge-only",
                             "--judge-backend", "saas", "--n", "2" if profile == "smoke" else "24",
                             "--file", str(path)])
        elif step.suite == "hle":
            commands.append([python, "quality/rescore_hle.py", "--file", str(path)])
    return commands


def inspect_judgments(step, paths):
    suffix = "_rescored.json" if step.suite == "hle" else "_judged.json"
    artifacts, errors = [], 0
    for path in paths:
        artifact = path.with_name(path.stem + suffix)
        if not artifact.exists():
            raise RuntimeError(f"judge produced no artifact for {path.name}")
        data = json.loads(artifact.read_text())
        artifacts.append(artifact)
        for row in data["results"]:
            judgment = row.get("judgment", {})
            llm = judgment.get("llm") or {}
            errors += bool(judgment.get("error") or llm.get("error") or
                           str(row.get("llm_verdict", "")).startswith("error:"))
    return artifacts, errors


def select_list(value, choices):
    selected = value.split(",")
    if not selected or any(s not in choices for s in selected) or len(set(selected)) != len(selected):
        raise argparse.ArgumentTypeError(f"choose unique comma-separated values from {', '.join(choices)}")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backends", type=lambda v: select_list(v, ASTRA_MODELS),
                        default=list(ASTRA_MODELS))
    parser.add_argument("--suites", type=lambda v: select_list(v, SUITES), default=list(SUITES))
    parser.add_argument("--profile", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--effort", choices=ASTRA_EFFORTS, default="low")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    parser.add_argument("--runtime-model", choices=["us.openai.gpt-6-astra", "global.openai.gpt-6-astra"],
                        default=ASTRA_MODELS["runtime"])
    parser.add_argument("--output-dir", type=Path, help="new directory for this campaign")
    parser.add_argument("--execute", action="store_true", help="make paid API calls; default is plan only")
    parser.add_argument("--judge", action="store_true", help="also grade this run's HLE/DeepSearchQA/GDPval files")
    args = parser.parse_args()
    if "mantle" in args.backends and args.region != "us-west-2":
        parser.error("Astra Mantle is available in us-west-2; use --region us-west-2 or select runtime/saas")

    steps = build_plan(args.backends, args.suites, args.profile, args.effort, sys.executable, args.runtime_model)
    print(f"Astra {args.profile} | effort={args.effort} | region={args.region} | {len(steps)} suite/backend runs")
    for step in steps:
        print(shlex.join(step.command))
    print("Full covers the existing suite defaults; smoke scores are not publishable benchmark results.")
    print("Comparison-only workloads and unfinished branch-only suites: see docs/astra-benchmarks.md.")
    if args.judge:
        print("Judging: fixed GPT-5.5 for DeepSearchQA/GDPval; Claude Haiku for HLE; newly created files only.")
    if not args.execute:
        print("Plan only. Add --execute to run with your configured API credentials.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = (args.output_dir or ROOT / "runs" / f"astra_{args.profile}_{stamp}").resolve()
    if output.exists():
        parser.error("--output-dir must be new; select suites/backends to retry in a new directory")
    output.mkdir(parents=True)
    env = {**os.environ, "AWS_REGION": args.region}
    manifest = {"model": "gpt-6-astra", "profile": args.profile, "reasoning_effort": args.effort,
                "region": args.region, "started_at": stamp,
                "cost_basis": "uncached Standard list-price estimate; cache writes/discounts, tool and judge fees excluded",
                "steps": []}
    manifest_path = output / "manifest.json"

    try:
        # Complete every selected backend's discovery before paid benchmark calls.
        for backend in args.backends:
            model = args.runtime_model if backend == "runtime" else ASTRA_MODELS[backend]
            command = [sys.executable, "performance/benchmark.py", "--backend",
                       PERF_BACKENDS[backend], "--list-models"]
            result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=True)
            if model not in result.stdout.splitlines():
                raise RuntimeError(f"{backend}: {model} was not advertised by model discovery")
            print(f"Preflight: {backend}/{model} advertised (invocation access still requires a successful call).")
        for step in steps:
            result_dir = output / step.result_group
            result_dir.mkdir(exist_ok=True)
            step_env = {**env, "BENCHMARK_RESULTS_DIR": str(result_dir)}
            before = set(result_dir.glob(step.pattern))
            record = {"suite": step.suite, "backend": step.backend, "model": step.model,
                      "command": step.command, "status": "running"}
            manifest["steps"].append(record)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            print(f"\nRunning {step.backend}/{step.suite}", flush=True)
            result = subprocess.run(step.command, cwd=ROOT, env=step_env)
            paths = sorted(set(result_dir.glob(step.pattern)) - before)
            record["files"] = [str(path.relative_to(output)) for path in paths]
            record["exit_code"] = result.returncode
            if result.returncode:
                raise RuntimeError(f"{step.backend}/{step.suite} exited {result.returncode}")
            checks = inspect_results(paths)
            record.update(checks)
            if checks["api_errors"]:
                raise RuntimeError(f"{step.backend}/{step.suite} recorded {checks['api_errors']} API errors")
            record["status"] = "review_required" if checks["incomplete"] or checks["parity_failures"] else "completed"
            if args.judge:
                commands = judge_commands(step, paths, sys.executable, args.profile)
                for command in commands:
                    subprocess.run(command, cwd=ROOT, env=step_env, check=True)
                if commands:
                    artifacts, errors = inspect_judgments(step, paths)
                    record["judge_files"] = [str(path.relative_to(output)) for path in artifacts]
                    record["judge_errors"] = errors
                    if errors:
                        record["status"] = "review_required"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    except (RuntimeError, subprocess.CalledProcessError, KeyboardInterrupt) as e:
        manifest["error"] = str(e) or "interrupted"
        if manifest["steps"]:
            manifest["steps"][-1]["status"] = "failed"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        parser.exit(1, f"Stopped: {manifest['error']}\nSaved campaign state: {manifest_path}\n")
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Saved campaign state: {manifest_path}")


if __name__ == "__main__":
    main()
