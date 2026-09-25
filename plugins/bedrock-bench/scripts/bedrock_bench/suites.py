"""Packaged suite catalog and configuration for upstream benchmark execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re

from .config import Experiment, Target, fingerprint, positive
from .costs import validate_rate_card


PLUGIN = Path(__file__).resolve().parents[2]
REGISTRY = PLUGIN / "benchmarks" / "registry.json"
AWS_REGISTRY = PLUGIN / "benchmarks" / "aws-registry.json"
HARBOR_VERSION = "0.23.0"
AWS_BENCH_COMMIT = "ea65432b5ce1d838b932728fcec4e02f493761a5"
TOOL_REQUIREMENTS = {
    "harbor": f"harbor=={HARBOR_VERSION}",
    "aws-bench": f"aws-bench @ git+https://github.com/aws-bench/aws-bench.git@{AWS_BENCH_COMMIT}",
}
SUITES = {
    "starter": {
        "harness": "built-in", "description": "Three deterministic JSON agent tasks",
        "repository": "https://github.com/openai-on-aws/benchmarks-openai",
        "requires_aws_environment": False,
    },
    "aws-cdk-smoke": {
        "harness": "harbor", "description": "Repair an SQS, Lambda, DynamoDB CDK application",
        "repository": "https://github.com/openai-on-aws/benchmarks-openai",
        "requires_aws_environment": False, "default_task": "cdk-sqs-lambda-dynamodb",
    },
    "terminal-bench": {
        "harness": "harbor", "description": "Terminal-Bench 2.0 repository and terminal tasks",
        "repository": "https://github.com/harbor-framework/terminal-bench-2",
        "dataset": "terminal-bench", "version": "2.0", "default_task": "fix-git",
        "requires_aws_environment": False,
    },
    "swe-bench": {
        "harness": "harbor", "description": "SWE-bench Verified through Harbor's task adapter",
        "repository": "https://github.com/SWE-bench/SWE-bench",
        "dataset": "swebench-verified", "version": "1.0",
        "docker_platform": "linux/amd64",
        "default_task": "django__django-15098", "requires_aws_environment": False,
    },
    "aws-bench": {
        "harness": "aws-bench", "description": "AWS-Bench quickstart against a provisioned AWS environment",
        "repository": "https://github.com/aws-bench/aws-bench",
        "dataset": "aws-bench-quickstart", "version": "0.7.2",
        "default_task": "describe-cloudformation-stack-resources",
        "requires_aws_environment": True,
    },
}


def dataset(suite):
    info = SUITES[suite]
    return next(d for d in json.loads(REGISTRY.read_text())
                if d["name"] == info["dataset"] and d["version"] == info["version"])


def task_catalog(suite):
    if suite == "starter":
        from .tasks import TASK_IDS
        return {name: {"name": name} for name in TASK_IDS}
    if suite == "aws-cdk-smoke":
        name = SUITES[suite]["default_task"]
        return {name: {"name": name, "path": str(PLUGIN / "benchmarks" / suite / name)}}
    return {entry["name"]: entry for entry in dataset(suite)["tasks"]}


def catalog():
    return [{"id": name, **info, "task_count": len(task_catalog(name)),
             "tool_requirement": TOOL_REQUIREMENTS.get(info["harness"])}
            for name, info in SUITES.items()]


def directory_digest(path):
    """Fingerprint source bytes, rejecting symlinks and excluding local build outputs."""
    path = Path(path)
    if not path.is_dir():
        raise ValueError(f"Missing source directory: {path}")
    digest = hashlib.sha256()
    ignored = {"node_modules", "dist", "cdk.out", "__pycache__", ".git", ".pytest_cache"}
    for entry in sorted(path.rglob("*")):
        relative = entry.relative_to(path)
        if any(part in ignored for part in relative.parts):
            continue
        if entry.is_symlink():
            raise ValueError(f"Benchmark source must not contain symlinks: {entry}")
        if entry.is_file():
            digest.update(relative.as_posix().encode() + b"\0" + entry.read_bytes() + b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class SuiteTarget:
    id: str
    runner: str
    provider: str
    model: str
    region: str | None = None
    reasoning_effort: str | None = None
    service_tier: str = "default"
    aws_profile: str | None = None
    routing: list[str] = field(default_factory=list)
    agent_version: str | None = None
    skills: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.runner in {"oracle", "nop"}:
            if self.provider != "reference" or self.model != "none":
                raise ValueError("oracle/nop use provider=reference and model=none")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.id):
                raise ValueError("Invalid target ID")
            if self.skills or self.reasoning_effort or self.routing:
                raise ValueError("Reference checks do not accept model settings or skills")
        else:
            if self.runner not in {"codex", "opencode"}:
                raise ValueError("Repository suites support codex and opencode, or oracle/nop validation")
            Target(**{key: value for key, value in asdict(self).items()
                      if key not in {"agent_version", "skills"}})
            if self.runner == "codex" and "/" in self.model:
                raise ValueError("The upstream Codex adapter requires a bare model or inference-profile ID; it strips slash prefixes, so ARNs are unsupported")
        if self.routing:
            raise ValueError("Repository suites do not support fixed OpenRouter routing yet")
        if self.agent_version is not None and (
            not isinstance(self.agent_version, str)
            or not re.fullmatch(r"[A-Za-z0-9._+-]{1,80}", self.agent_version)
        ):
            raise ValueError("agent_version must be an explicit CLI package version")
        if not isinstance(self.skills, list) or any(not isinstance(s, str) or not s for s in self.skills):
            raise ValueError("skills must be local skill-directory paths")
        for skill in self.skills:
            if not (Path(skill) / "SKILL.md").is_file():
                raise ValueError(f"Missing skill entrypoint: {skill}")


@dataclass(frozen=True)
class SuiteLimits:
    timeout_seconds: float = 300
    setup_timeout_seconds: float = 600
    verifier_timeout_seconds: float = 120
    process_timeout_seconds: float = 1500

    def __post_init__(self):
        for name, value in asdict(self).items():
            positive(value, name)
        if self.process_timeout_seconds <= self.timeout_seconds:
            raise ValueError("process_timeout_seconds must exceed the agent timeout")


@dataclass(frozen=True)
class SuiteExperiment:
    name: str
    suite: str
    targets: list[SuiteTarget]
    tasks: list[str]
    repetitions: int = 1
    seed: int = 42
    limits: SuiteLimits = field(default_factory=SuiteLimits)
    rate_cards: list[dict] = field(default_factory=list)
    tools_dir: str = ".bench-tools"
    aws_environment: str | None = None
    environment_profile: str | None = None
    schema_version: int = 2

    def __post_init__(self):
        if self.schema_version != 2 or self.suite not in SUITES or self.suite == "starter":
            raise ValueError("Repository experiments require schema_version=2 and a repository suite")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Experiment name is required")
        positive(self.repetitions, "repetitions", integer=True)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        if not self.targets or len({t.id for t in self.targets}) != len(self.targets):
            raise ValueError("Targets must be nonempty with unique IDs")
        if (not isinstance(self.tasks, list) or not self.tasks
                or any(not isinstance(t, str) for t in self.tasks)
                or len(set(self.tasks)) != len(self.tasks)):
            raise ValueError("Select at least one unique task")
        available = task_catalog(self.suite)
        if any(t not in available for t in self.tasks):
            raise ValueError(f"Unknown task; use tasks --suite {self.suite} to list exact task names")
        reference = [t.runner in {"oracle", "nop"} for t in self.targets]
        if any(reference) and not all(reference):
            raise ValueError("Reference validation and model measurements must be separate runs")
        if self.suite == "aws-bench" and any(reference):
            raise ValueError("The bundled AWS quickstart uses a model judge; use aws-cdk-smoke for reference validation")
        if self.suite == "aws-bench" and any(t.skills for t in self.targets):
            raise ValueError("Skill injection is currently supported for Harbor suites only")
        if self.suite == "aws-bench" and any(t.runner == "opencode" and "bedrock" in t.provider for t in self.targets):
            raise ValueError("Use Codex for AWS-Bench on Bedrock; OpenCode's Bedrock credential forwarding is not validated there")
        if not isinstance(self.tools_dir, str) or not self.tools_dir:
            raise ValueError("tools_dir must be a directory path")
        for name in ("aws_environment", "environment_profile"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or "\n" in value):
                raise ValueError(f"{name} must be a nonempty single-line string")
        if self.suite != "aws-bench" and (self.aws_environment or self.environment_profile):
            raise ValueError("AWS environment settings apply only to aws-bench")
        if not isinstance(self.rate_cards, list):
            raise ValueError("rate_cards must be an array")
        for card in self.rate_cards:
            validate_rate_card(card)
        keys = [tuple(c.get(k) for k in ("provider", "model", "region", "service_tier")) for c in self.rate_cards]
        if len(keys) != len(set(keys)):
            raise ValueError("Rate cards must have unique target identities")

    @property
    def validation_only(self):
        return all(t.runner in {"oracle", "nop"} for t in self.targets)

    @property
    def harness(self):
        return SUITES[self.suite]["harness"]

    @property
    def executable(self):
        import os
        return str(Path(self.tools_dir).resolve() / self.harness /
                   ("Scripts" if os.name == "nt" else "bin") /
                   (self.harness + (".exe" if os.name == "nt" else "")))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data, *, base=None):
        data = dict(data)
        data["targets"] = [dict(t) for t in data["targets"]]
        if base is not None:
            def absolute(path):
                return str((Path(base) / Path(path).expanduser()).resolve())
            data["tools_dir"] = absolute(data.get("tools_dir", ".bench-tools"))
            for target in data["targets"]:
                target["skills"] = [absolute(p) for p in target.get("skills", [])]
        data["targets"] = [SuiteTarget(**t) for t in data["targets"]]
        data["limits"] = SuiteLimits(**data.get("limits", {}))
        return cls(**data)

    def protocol(self):
        entries = task_catalog(self.suite)
        sources = []
        for name in self.tasks:
            entry = entries[name]
            if self.suite == "aws-cdk-smoke":
                source = {"name": name, "sha256": directory_digest(entry["path"])}
            else:
                source = {key: entry[key] for key in ("name", "git_url", "git_commit_id", "path")}
            sources.append(source)
        return {
            "suite": self.suite, "sources": sources, "limits": asdict(self.limits),
            "repetitions": self.repetitions, "seed": self.seed,
            "scoring_revision": "upstream-binary-reward-v1", "reward_key": "reward",
            "dataset_version": SUITES[self.suite].get("version"),
            "docker_platform": SUITES[self.suite].get("docker_platform"),
        }

    def plan(self):
        issues = []
        if not Path(self.executable).is_file():
            issues.append(f"Prepare the {self.harness} runtime with prepare --suite {self.suite} --execute")
        if self.suite == "aws-bench" and not self.aws_environment:
            issues.append("Set aws_environment to an explicitly selected AWS-Bench testing environment")
        return {
            "name": self.name, "suite": self.suite, "harness": self.harness,
            "mode": "reference-validation" if self.validation_only else "live",
            "repository": SUITES[self.suite]["repository"],
            "tool_requirement": TOOL_REQUIREMENTS[self.harness],
            "executable": self.executable,
            "attempts": len(self.targets) * len(self.tasks) * self.repetitions,
            "tasks": self.tasks, "repetitions": self.repetitions,
            "targets": [asdict(t) for t in self.targets], "limits": asdict(self.limits),
            "concurrency": 1, "upstream_retries": 0,
            "limit_support": "Agent timeout and whole-process timeout; model-call and output-token limits are client-managed",
            "protocol_hash": fingerprint(self.protocol()),
            "sources": self.protocol()["sources"],
            "aws_environment": self.aws_environment,
            "requires_aws_environment": self.suite == "aws-bench",
            "docker_platform": SUITES[self.suite].get("docker_platform"),
            "cost_scope": "Agent inference only; infrastructure, subscriptions, tools, and verifier/judge costs are excluded",
            "execution": "No model calls, downloads, containers, or AWS operations until run --execute",
            "preparation_needed": issues,
        }


def load_experiment(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if data.get("schema_version", 1) == 1:
        return Experiment.from_dict(data)
    return SuiteExperiment.from_dict(data, base=path.parent)
