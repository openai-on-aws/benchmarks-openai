#!/usr/bin/env python3
"""Discover Bedrock OpenAI models, then plan or run a fixed ARC comparison matrix."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "quality"))
from arc_common import positive_float, positive_int, write_json
from arc_agi2 import DATASET_REVISION


def discover_region(region):
    """Keep discovery failures visible; catalogue entries are not invocation proof."""
    import boto3
    from botocore.config import Config
    from aws_bedrock_token_generator import provide_token
    from openai import OpenAI
    models, errors = [], []
    try:
        token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or provide_token(region=region)
        client = OpenAI(api_key=token, base_url=f"https://bedrock-mantle.{region}.api.aws/v1",
                        timeout=20, max_retries=0)
        models.extend({"backend": "mantle", "region": region, "model": model.id}
                      for model in client.models.list() if model.id.startswith("openai."))
    except Exception as e:
        errors.append({"backend": "mantle", "region": region, "error": type(e).__name__,
                       "message": str(e)})
    try:
        client = boto3.client("bedrock", region_name=region, config=Config(
            connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 1}))
        for page in client.get_paginator("list_inference_profiles").paginate():
            models.extend({"backend": "runtime", "region": region, "model": p["inferenceProfileId"]}
                          for p in page["inferenceProfileSummaries"]
                          if p.get("status") == "ACTIVE" and ".openai." in p["inferenceProfileId"])
    except Exception as e:
        errors.append({"backend": "runtime", "region": region, "error": type(e).__name__,
                       "message": str(e)})
    return models, errors


def canonical_model(model):
    return re.sub(r"^(?:us|global|eu|apac)\.", "", model)


def select_models(catalog, requested="all", all_routes=False):
    candidates = catalog["models"]
    wanted = requested.split(",") if requested != "all" else None
    if wanted:
        missing = [name for name in wanted if not any(
            name in (m["model"], canonical_model(m["model"])) for m in candidates)]
        if missing:
            raise ValueError(f"models absent from this discovery snapshot: {', '.join(missing)}")
        candidates = [m for m in candidates if any(
            name in (m["model"], canonical_model(m["model"])) for name in wanted)]
    # Compare each advertised ID once. Prefer Mantle, then US Runtime over Global,
    # and use the user's region order. Dated snapshots remain distinct conditions.
    ranked = sorted(candidates, key=lambda m: (
        m["backend"] != "mantle", m["model"].startswith("global."),
        catalog["regions"].index(m["region"]), m["model"]))
    chosen, seen = [], set()
    for model in ranked:
        key = (model["backend"], model["region"], model["model"]) if all_routes \
            else canonical_model(model["model"])
        if key not in seen:
            chosen.append(model)
            seen.add(key)
    if not chosen:
        raise ValueError("no OpenAI model conditions discovered")
    return chosen


def build_commands(models, args):
    steps = []
    suites = args.suites.split(",")
    for index, model in enumerate(models):
        for suite in suites:
            name = re.sub(r"[^A-Za-z0-9_.-]", "_", model["model"])
            output = args.output_dir.resolve() / f"{index:02d}-{name}-{suite}"
            command = [sys.executable, str(ROOT / "quality" / f"arc_agi{suite[-1]}.py"),
                       "--backend", model["backend"], "--model", model["model"],
                       "--region", model["region"], "--effort", args.effort,
                       "--output-dir", str(output), "--budget-usd",
                       str(args.budget_usd / (len(models) * len(suites))),
                       "--max-output-tokens", str(args.max_output_tokens)]
            if suite == "arc2":
                command += ["--dataset-dir", str(args.dataset_dir.resolve()), "--n", str(args.n),
                            "--revision", DATASET_REVISION, "--seed", "42", "--attempts", "2"]
            else:
                command += ["--environments-dir", str(args.environments_dir.resolve()),
                            "--max-actions", str(args.max_actions)]
                if args.compact_threshold:
                    command += ["--compact-threshold", str(args.compact_threshold)]
            safeguard = "gpt-oss-safeguard-" in model["model"]
            steps.append({**model, "suite": suite,
                          "api": "chat_completions" if safeguard else "responses",
                          "effective_reasoning_effort": "model_default" if safeguard else args.effort,
                          "command": command,
                          "result": str(output / "manifest.json"), "status": "planned"})
    return steps


def write_report(campaign, directory):
    lines = [
        "# Bedrock ARC pilot", "",
        "Small public-set runs validate the harness. They are not official leaderboard results.",
        "ARC-AGI-2 and ARC-AGI-3 measure different capabilities; compare within each suite.", "",
        "| Model | Region | Suite | API / effort | Run status | Score | Coverage | Estimated token cost |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    total = 0.0
    for step in campaign["steps"]:
        path = Path(step["result"])
        data = json.loads(path.read_text()) if path.exists() else {}
        summary = data.get("summary", {})
        if step["suite"] == "arc2":
            value = summary.get("task_accuracy")
            score = f"{100 * value:.1f}% exact tasks" if value is not None else "—"
            coverage = f"{summary.get('completed_tasks', 0)}/{summary.get('planned_tasks', '?')} tasks"
        else:
            value = data.get("pilot_score")
            score = f"{value:.2f}% RHAE" if value is not None else "—"
            coverage = f"{len(data.get('results', []))}/{len(data.get('games', [])) or '?'} games"
        cost = data.get("estimated_cost_usd")
        cost_text = f"${cost:.4f}" if cost is not None else "—"
        total += cost or 0
        api = data.get("api", step.get("api", "pending"))
        effort = data.get("effective_reasoning_effort",
                          data.get("reasoning_effort", step.get("effective_reasoning_effort", "pending")))
        lines.append(f"| `{step['model']}` | {step['region']} | {step['suite']} | {api} / {effort} | "
                     f"{data.get('status', step['status'])} | {score} | {coverage} | {cost_text} |")
    lines.extend(["", f"Settled estimated token cost: ${total:.4f}.",
                  "Failed calls may have unreported usage; inspect unsettled reservations in each manifest.",
                  "Safeguard and Daybreak entries are specialised-model diagnostics, not a general-purpose ranking.",
                  "See each run's manifest for exact settings, failures, hashes, and raw observations.", ""])
    (directory / "REPORT.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aws-profile", help="AWS profile for discovery and invocation")
    parser.add_argument("--regions", default="us-west-2,us-east-1,us-east-2")
    parser.add_argument("--discover", action="store_true", help="save a catalogue and stop; no model calls")
    parser.add_argument("--catalog", type=Path, help="saved catalogue; avoids new discovery")
    parser.add_argument("--models", default="all", help="all, or comma-separated advertised IDs")
    parser.add_argument("--all-routes", action="store_true", help="also compare regions and endpoint routes")
    parser.add_argument("--suites", choices=["arc2", "arc3", "arc2,arc3"], default="arc2,arc3")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "runs/datasets/ARC-AGI-2")
    parser.add_argument("--environments-dir", type=Path, default=ROOT / "runs/datasets/arc3")
    parser.add_argument("--n", type=positive_int, default=20)
    parser.add_argument("--max-actions", type=positive_int, default=40)
    parser.add_argument("--max-output-tokens", type=positive_int, default=4096)
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="low",
                        help="explicit common reasoning setting; unsupported models remain visible")
    parser.add_argument("--compact-threshold", type=positive_int)
    parser.add_argument("--budget-usd", type=positive_float, default=50.0,
                        help="total estimated token-cost cap, divided across all model/suite conditions")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.discover and (args.catalog or args.execute):
        parser.error("--discover cannot be combined with --catalog or --execute")
    if args.aws_profile:
        os.environ["AWS_PROFILE"] = args.aws_profile
    try:
        args.output_dir.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        parser.error(str(e))
    if args.catalog:
        catalog = json.loads(args.catalog.read_text())
    else:
        regions = list(dict.fromkeys(args.regions.split(",")))
        catalog = {
            "created_at": datetime.now(timezone.utc).isoformat(), "regions": regions,
            "scope": "Mantle model listings and active Runtime inference profiles in selected regions",
            "invocation_verified": False, "models": [], "errors": [],
        }
        with ThreadPoolExecutor(max_workers=3) as pool:
            for models, errors in pool.map(discover_region, regions):
                catalog["models"].extend(models)
                catalog["errors"].extend(errors)
    write_json(args.output_dir / "catalog.json", catalog)
    if args.discover:
        print(f"{len(catalog['models'])} routes; {len(catalog['errors'])} discovery errors")
        print(args.output_dir / "catalog.json")
        return 1 if catalog["errors"] else 0
    try:
        models = select_models(catalog, args.models, args.all_routes)
    except ValueError as e:
        parser.error(str(e))
    steps = build_commands(models, args)
    campaign = {"status": "planned", "budget_usd": args.budget_usd,
                "discovery_errors": catalog["errors"], "steps": steps}
    path = args.output_dir / "campaign.json"
    write_json(path, campaign)
    write_report(campaign, args.output_dir)
    for step in steps:
        print(shlex.join(step["command"]))
    if not args.execute:
        print(f"Plan only: {len(models)} model conditions; {path}")
        return 0
    if catalog["errors"] and args.models == "all":
        parser.error("discovery was incomplete; refresh it or explicitly select models")
    code = 0
    try:
        campaign["status"] = "running"
        for step in steps:
            step["status"] = "running"
            write_json(path, campaign)
            output = Path(step["result"]).parent
            with open(output.parent / f"{output.name}.log", "w") as log:
                result = subprocess.run([*step["command"], "--execute"], cwd=ROOT,
                                        stdout=log, stderr=subprocess.STDOUT)
            step["exit_code"] = result.returncode
            artifact = Path(step["result"])
            data = json.loads(artifact.read_text()) if artifact.exists() else {}
            complete = result.returncode == 0 and data.get("status") == "completed"
            step["status"] = "completed" if complete else "review_required"
            code = max(code, int(not complete))
            write_json(path, campaign)
            write_report(campaign, args.output_dir)
        campaign["status"] = "review_required" if code else "completed"
    except KeyboardInterrupt:
        campaign["status"] = "interrupted"
        code = 130
    finally:
        write_json(path, campaign)
        write_report(campaign, args.output_dir)
    print(f"{campaign['status']}: {path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
