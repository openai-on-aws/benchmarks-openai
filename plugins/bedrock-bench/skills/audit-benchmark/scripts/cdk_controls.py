#!/usr/bin/env python3
"""Materialize closed CDK controls and run the real packaged verifier offline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys


SKILL = Path(__file__).resolve().parents[1]
PLUGIN = SKILL.parents[1]
TASK_ID = "cdk-sqs-lambda-dynamodb"
SOURCE = PLUGIN / "benchmarks" / "aws-cdk-smoke" / TASK_ID
PROJECT_FILES = ("bin/app.ts", "cdk.json", "lambda/handler.js", "lib/stack.ts",
                 "package.json", "package-lock.json", "tsconfig.json")
DRIVER_FILES = ("cdk_driver.cjs", "offline.cjs", "check_handlers.cjs")
MARKER = "BEDROCK_BENCH_AUDIT_RESULT="


def api():
    # This file is also directly runnable from an installed plugin.
    from bedrock_bench import audit
    return audit


def apply_patches(reference, patches):
    files = dict(reference)
    for patch in patches:
        if set(patch) != {"file", "before", "after"} or patch["file"] not in {"lib/stack.ts", "lambda/handler.js"}:
            raise ValueError("A mutation may change only the two submitted files")
        before, after = patch["before"], patch["after"]
        if not isinstance(before, str) or not before or not isinstance(after, str) or before == after:
            raise ValueError("A mutation must specify a nonempty, changing literal replacement")
        text = files[patch["file"]].decode()
        if text.count(before) != 1:
            raise ValueError(f"Mutation source drift in {patch['file']}: expected one exact match for {before!r}")
        files[patch["file"]] = text.replace(before, after, 1).encode()
    if files == reference:
        raise ValueError("A mutation must change the candidate")
    return files


def prepare(root, data, tools):
    audit = api()
    sources = [SOURCE / "environment/project" / name for name in PROJECT_FILES]
    sources += [SOURCE / name for name in ("instruction.md", "task.toml", "environment/Dockerfile",
                "solution/stack.ts", "solution/handler.js", "solution/solve.sh",
                "tests/verify.cjs", "tests/test.sh", "tests/docker-compose.yaml")]
    resources = [SKILL / "assets/cdk-mutations.json", SKILL / "assets/alternate-handler.js",
                 SKILL / "assets/alternate-stack.ts",
                 Path(__file__)] + [SKILL / "scripts" / name for name in DRIVER_FILES]
    data["source_hashes"] = audit.source_hashes(sources)
    data["grader_hashes"] = audit.source_hashes([SOURCE / "tests/verify.cjs", SOURCE / "tests/test.sh"])
    data["controller_hashes"].update(audit.source_hashes(resources))
    data["scope"].update({
        "tasks": [TASK_ID],
        "verifier": "Byte-for-byte copy of packaged tests/verify.cjs with BENCH_PROJECT",
        "build": "Pinned TypeScript CLI, followed by the packaged dist/bin/app.js App.synth entrypoint",
        "harness": "Direct verifier audit; Harbor orchestration and test.sh reward-file transport are not exercised",
    })
    data["scope"]["limitations"] += [
        "No deployment, live IAM policy evaluation, AWS SDK network I/O, or provider authentication is tested.",
        "Local Node execution is for authored controls only, not a sandbox for untrusted agent submissions.",
        "The independent handler fixture self-check does not contribute to packaged-grader pass/kill counts.",
        "Dependency versions and the available runtime lockfile are recorded; installed transitive package bytes are not attested.",
    ]
    baseline = {name: (SOURCE / "environment/project" / name).read_bytes() for name in PROJECT_FILES}
    reference = {**baseline, "lib/stack.ts": (SOURCE / "solution/stack.ts").read_bytes(),
                 "lambda/handler.js": (SOURCE / "solution/handler.js").read_bytes()}
    cases = [
        audit.make_case(root, TASK_ID, "reference", "reference", "The packaged correct solution is accepted",
                        "accept", reference, {"base": "packaged-solution", "patches": []}),
    ]
    alternate = {**baseline, "lib/stack.ts": (SKILL / "assets/alternate-stack.ts").read_bytes(),
                 "lambda/handler.js": (SKILL / "assets/alternate-handler.js").read_bytes()}
    cases.append(audit.make_case(root, TASK_ID, "alternate-valid", "alternate-valid",
        "Accept equivalent mixed-case scoped IAM, inline basic logging, a different handler, batch size 1, and longer visibility",
        "accept", alternate,
        {"base": "packaged-environment", "patches": [],
         "stack_resource": "skills/audit-benchmark/assets/alternate-stack.ts",
         "handler_resource": "skills/audit-benchmark/assets/alternate-handler.js"}))
    cases.append(audit.make_case(root, TASK_ID, "unchanged-baseline", "baseline",
        "Reject the unmodified broken task", "reject", baseline, {"base": "packaged-environment", "patches": []}))
    seen = {case["name"] for case in cases}
    mutations = json.loads((SKILL / "assets/cdk-mutations.json").read_text())
    if not isinstance(mutations, list) or not mutations:
        raise ValueError("CDK mutation resource must be a nonempty list")
    for mutation in mutations:
        name = mutation["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name) or name in seen:
            raise ValueError("Duplicate or invalid CDK control ID")
        seen.add(name)
        files = apply_patches(reference, mutation["patches"])
        cases.append(audit.make_case(root, TASK_ID, name, mutation.get("kind", "semantic-mutant"),
            mutation["invariant"], "reject", files,
            {"base": "packaged-solution", "patches": mutation["patches"]}))
    data["cases"] = cases
    verifier = root / "controller" / "verifier"
    for name in ("verify.cjs", "test.sh"):
        audit.save_new(verifier / name, (SOURCE / "tests" / name).read_bytes())
    for name in DRIVER_FILES:
        audit.save_new(root / "controller/runtime" / name, (SKILL / "scripts" / name).read_bytes())
    audit.save_new(root / "controller/runtime/package.json", baseline["package.json"])
    data["plan_validation"] = {
        "status": "passed", "kind": "materialization-only", "actual_grader_executed": False,
        "checks": ["Unique case IDs", "Literal patch anchors match once", "Only submitted files mutated",
                   "Each mutant differs from reference", "Verifier copied without modification"],
    }
    data["runtime_contract"] = {
        "local_node_modules": [str(tools / "node_modules"), str(tools / "aws-cdk-smoke/node_modules")],
        "container_selector": [str(tools / "image-id.txt"), str(tools / "aws-cdk-smoke/image-id.txt")],
        "container_image_format": "sha256:<64 lowercase hex characters>; must already exist locally",
        "container_modules": "/app/node_modules",
        "network": "Node network/process guard; containers also use --network=none and --pull=never",
        "phase_timeout_seconds": 30,
    }
    data["preparation_needed"] = [
        "Execution requires Node.js 22+ and the packaged lockfile's already installed node_modules in one of runtime_contract.local_node_modules.",
        "Alternatively select an already built smoke image in image-id.txt; the audit never installs, builds, pulls, or searches registries.",
    ]


def environment(node=None):
    # Inference tokens, AWS credentials, NODE_OPTIONS, npm hooks, shell startup
    # variables, and user-configured commands are deliberately not inherited.
    env = {
        "PATH": (str(Path(node).parent) + os.pathsep if node else "") + os.defpath,
        "LANG": "C.UTF-8", "CI": "true", "CDK_DISABLE_CLI_TELEMETRY": "1",
        "AWS_EC2_METADATA_DISABLED": "true", "AWS_CONFIG_FILE": os.devnull,
        "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1",
        "CDK_DEFAULT_ACCOUNT": "123456789012", "CDK_DEFAULT_REGION": "us-east-1",
        "JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION": "1",
        "NODE_OPTIONS": "", "NODE_PATH": "",
    }
    if os.name == "nt" and "SystemRoot" in os.environ:
        env["SystemRoot"] = os.environ["SystemRoot"]
    return env


def process_error(result):
    if result["error"]:
        return result["error"]
    if result["timed_out"]:
        return "Subprocess timed out; this is not a rejected mutant"
    if result["output_limited"]:
        return "Subprocess exceeded its evidence log limit"
    return None


def verdict(result, phase):
    error = process_error(result)
    if error:
        return "infrastructure_error", error, None
    rows = [line[len(MARKER):] for line in result["stdout"].splitlines() if line.startswith(MARKER)]
    try:
        if len(rows) != 1:
            raise ValueError("Expected exactly one structured driver result")
        row = json.loads(rows[0])
        if not isinstance(row, dict) or row.get("phase") != phase:
            raise ValueError("Wrong driver result phase")
        observed = row.get("observed")
        if observed not in {"accept", "reject", "infrastructure_error"}:
            raise ValueError("Invalid driver outcome")
        if phase == "verify" and row.get("verifier_invoked") is not True:
            raise ValueError("The actual packaged verifier was not invoked")
        if (not isinstance(row.get("errors"), list)
                or any(not isinstance(e, dict) or not isinstance(e.get("message"), str) for e in row["errors"])
                or not isinstance(row.get("offline_blocks"), list)):
            raise ValueError("Invalid structured driver error evidence")
        if observed == "accept" and (row["errors"] or row["offline_blocks"]):
            raise ValueError("Driver reported acceptance with errors")
        if observed == "reject" and (not row["errors"] or row["offline_blocks"]):
            raise ValueError("Rejection requires grader error evidence and no blocked operation")
        if phase == "probe" and observed == "accept" and not isinstance(row.get("runtime"), dict):
            raise ValueError("Runtime preflight omitted version evidence")
        if (observed == "accept" and result["exit_code"] != 0) or (
                observed == "reject" and result["exit_code"] != 1):
            raise ValueError("Driver verdict and subprocess exit code disagree")
    except (ValueError, TypeError) as exc:
        return "infrastructure_error", str(exc), None
    reason = "; ".join(e["message"] for e in row["errors"])
    return observed, reason or f"Packaged {phase} completed", row


def attach_logs(root, case, directory):
    audit = api()
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            case["evidence"].append({"path": path.relative_to(root).as_posix(), "sha256": audit.sha256(path.read_bytes())})


def choose_runtime(root, tools):
    """Only conventional directories or a strict local image ID select runtime."""
    node = shutil.which("node")
    for modules in (tools / "node_modules", tools / "aws-cdk-smoke/node_modules"):
        if modules.is_dir():
            if not node:
                return None, "Node.js is not on PATH; the existing node_modules cannot be used"
            return {"kind": "local-node", "node": node, "modules": str(modules.resolve())}, None
    for selector in (tools / "image-id.txt", tools / "aws-cdk-smoke/image-id.txt"):
        if not selector.exists():
            continue
        if selector.is_symlink() or not selector.is_file() or selector.stat().st_size > 80:
            return None, f"Invalid image selector: {selector}; expected a small regular text file"
        image = selector.read_text().strip()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            return None, f"Invalid image ID in {selector}; commands and image tags are not accepted"
        docker = shutil.which("docker")
        if not docker:
            return None, "Docker is not on PATH for the explicitly selected local image"
        if ":" in str(root):
            return None, "Docker bind paths must not contain ':'; select another output directory"
        # Context inspection reads local CLI metadata, then all daemon operations
        # explicitly use the validated Unix socket. Remote Docker hosts are refused.
        host = os.environ.get("DOCKER_HOST")
        if not host:
            result = api().run_bounded([docker, "context", "inspect", "--format",
                "{{json .Endpoints.docker.Host}}"], root, root / "controller/runtime/docker-context",
                env={**environment(docker), **({"HOME": os.environ["HOME"]} if "HOME" in os.environ else {})}, timeout=10)
            if process_error(result) or result["exit_code"]:
                return None, "Cannot inspect the local Docker context; see controller/runtime/docker-context"
            try:
                host = json.loads(result["stdout"])
            except ValueError:
                return None, "Docker context did not report a valid socket"
        if not isinstance(host, str) or not host.startswith("unix:///") or "\n" in host:
            return None, "Offline audit requires a local Unix Docker socket; remote Docker hosts are refused"
        return {"kind": "local-docker", "docker": docker, "host": host, "image": image,
                "modules": "/app/node_modules"}, None
    return None, f"No local node_modules or explicit image-id.txt found under {tools}; no installation or image pull was attempted"


def command(runtime, root, project, phase, *, cidfile=None):
    controller = root / "controller"
    if runtime["kind"] == "local-node":
        return [runtime["node"], "--require", str(controller / "runtime/offline.cjs"),
                str(controller / "runtime/cdk_driver.cjs"), phase, str(project), runtime["modules"],
                str(controller / "verifier/verify.cjs"), str(controller / "runtime/package.json")]
    # Fixed entrypoint overrides image startup commands. No candidate receives
    # a writable controller mount, Docker socket, host environment, or network.
    if any(":" in str(p) for p in (controller, project)):
        raise ValueError("Docker bind paths must not contain ':'")
    argv = [runtime["docker"], "--host", runtime["host"], "run", "--rm", "--pull=never",
            "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--pids-limit=128", "--memory=2g", "--cpus=2",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
            "--volume", f"{controller}:/audit:ro",
            "--volume", f"{project}:/candidate:rw", "--workdir", "/candidate",
            "--entrypoint", "node"]
    if cidfile is not None:
        argv.extend(["--cidfile", str(cidfile)])
    if os.name == "posix":
        argv.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    for name, value in environment().items():
        if name != "PATH":
            argv.extend(["--env", f"{name}={value}"])
    return argv + [runtime["image"], "--require", "/audit/runtime/offline.cjs",
                   "/audit/runtime/cdk_driver.cjs", phase, "/candidate", "/app/node_modules",
                   "/audit/verifier/verify.cjs", "/audit/runtime/package.json"]


def run_phase(runtime, root, project, phase, directory, *, timeout=30):
    """Stop a timed-out Docker workload, not just its attached client."""
    audit = api()
    cidfile = directory / "container.id"
    result = None
    try:
        result = audit.run_bounded(command(runtime, root, project, phase, cidfile=cidfile),
                                   project, directory, env=environment(runtime.get("node")), timeout=timeout)
        return result
    finally:
        if runtime["kind"] == "local-docker" and (result is None or process_error(result)):
            # --cidfile is written only by the Docker client in a newly created
            # controller phase directory. Never remove a name supplied by data.
            if cidfile.is_file() and not cidfile.is_symlink() and cidfile.stat().st_size <= 65:
                container_id = cidfile.read_text().strip()
                if re.fullmatch(r"[a-f0-9]{64}", container_id):
                    cleanup = audit.run_bounded(
                        [runtime["docker"], "--host", runtime["host"], "rm", "--force", container_id],
                        root, directory / "cleanup", env=environment(), timeout=10)
                    if result is not None:
                        result["container_cleanup"] = {
                            "exit_code": cleanup["exit_code"], "error": process_error(cleanup),
                            "evidence": (directory / "cleanup").relative_to(root).as_posix(),
                        }
                        audit.save_new(directory / "cleanup.json", audit.json_bytes(result["container_cleanup"]))


def fixture_check(root, data):
    audit = api()
    node = shutil.which("node")
    if not node:
        data["fixture_validation"] = {"status": "unavailable", "reason": "Node.js is not on PATH",
                                      "actual_packaged_grader": False}
        return
    manifest = []
    for case in data["cases"]:
        changed_handler = any(p["file"] == "lambda/handler.js" for p in case["recipe"].get("patches", []))
        expected = "reject" if changed_handler or case["kind"] == "baseline" else "accept"
        manifest.append({"id": case["id"], "expected": expected,
                         "file": str(root / case["candidate"] / "lambda/handler.js")})
    manifest_path = audit.save_new(root / "controller/handler-controls.json", audit.json_bytes(manifest))
    result = audit.run_bounded([node, "--require", str(root / "controller/runtime/offline.cjs"),
                               str(root / "controller/runtime/check_handlers.cjs"), str(manifest_path)],
                              root, root / "controller/fixture-check", env=environment(node), timeout=15)
    try:
        if process_error(result) or result["exit_code"] not in {0, 1}:
            raise ValueError(process_error(result) or "Fixture process failed")
        value = json.loads(result["stdout"])
        if value.get("kind") != "fixture-self-check" or value.get("status") not in {"passed", "failed"}:
            raise ValueError("Invalid fixture self-check output")
    except (ValueError, AttributeError) as exc:
        value = {"status": "failed", "reason": str(exc), "actual_packaged_grader": False}
    data["fixture_validation"] = value
    data["fixture_validation"]["evidence"] = "controller/fixture-check"
    if value["status"] == "failed":
        data["controller_error"] = "Handler control self-check failed; candidate recipes need investigation"


def execute(root, data, tools):
    audit = api()
    fixture_check(root, data)
    try:
        runtime, error = choose_runtime(root, tools)
    except (OSError, ValueError, UnicodeError) as exc:
        runtime, error = None, f"Cannot inspect existing runtime: {type(exc).__name__}: {exc}"
    if runtime is None:
        data["preparation_needed"] = [error] + data["preparation_needed"]
        for case in data["cases"]:
            audit.record(case, "unavailable", error)
        return
    data["runtime"] = runtime
    probe_project = root / data["cases"][0]["candidate"]
    probe = run_phase(runtime, root, probe_project, "probe", root / "controller/runtime/probe", timeout=20)
    observed, reason, detail = verdict(probe, "probe")
    if observed != "accept":
        error = f"Existing runtime is not ready: {reason}; see controller/runtime/probe"
        data["preparation_needed"].insert(0, error)
        for case in data["cases"]:
            audit.record(case, "unavailable", error)
        return
    data["runtime"]["verified_versions"] = detail["runtime"]
    lock = Path(runtime["modules"]).parent / "package-lock.json"
    if runtime["kind"] == "local-node" and lock.is_file():
        data["runtime"]["lockfile_sha256"] = audit.sha256(lock.read_bytes())
        data["runtime"]["matches_packaged_lockfile"] = (
            lock.read_bytes() == (SOURCE / "environment/project/package-lock.json").read_bytes())
    data["preparation_needed"] = []
    for case in data["cases"]:
        project = root / case["candidate"]
        # Never execute an edited or symlinked control as if it were the
        # materialized, hashed fixture.
        if any((project / name).is_symlink() or not (project / name).is_file()
               or audit.sha256((project / name).read_bytes()) != item["sha256"]
               for name, item in case["files"].items()):
            audit.record(case, "infrastructure_error", "Candidate files changed after control materialization")
            continue
        for phase in ("build", "synth", "verify"):
            directory = root / "controller/cases" / case["id"] / phase
            result = run_phase(runtime, root, project, phase, directory)
            attach_logs(root, case, directory)
            if phase == "verify":
                observed, reason, detail = verdict(result, phase)
                audit.record(case, observed, reason)
                audit.evidence(root, case, "verdict.json", detail or {"error": reason, "verifier_invoked": False})
            elif process_error(result) or result["exit_code"] != 0:
                # These controls are expected to build/synth. A compiler error,
                # missing dependency, or launch failure is not evidence that
                # the verifier detected the intended semantic mutation.
                audit.record(case, "infrastructure_error",
                             f"{phase} did not complete: {process_error(result) or 'nonzero exit'}; see phase logs")
                break
            elif phase == "synth":
                template = project / "cdk.out/Benchmark.template.json"
                if template.is_symlink() or not template.is_file():
                    audit.record(case, "infrastructure_error", "Synthesis produced no regular Benchmark.template.json")
                    break
                audit.evidence(root, case, "template.json", template.read_bytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="New or empty audit directory")
    parser.add_argument("--tools-dir", default=".bench-tools")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        from bedrock_bench.audit import write_audit
        path = write_audit("aws-cdk-smoke", args.out, seed=args.seed, execute=args.execute, tools_dir=args.tools_dir)
        print(path)
        status = json.loads(path.with_name("AUDIT.json").read_text())["status"]
        return {"planned": 0, "passed": 0, "failed": 1, "unavailable": 2}[status]
    except (ValueError, OSError) as exc:
        parser.exit(2, f"audit: {exc}\n")


if __name__ == "__main__":
    sys.path.insert(0, str(PLUGIN / "scripts"))
    raise SystemExit(main())
