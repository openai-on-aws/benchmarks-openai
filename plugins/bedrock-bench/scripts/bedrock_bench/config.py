"""Validated experiment configuration shared by every runner."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
import re


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def positive(value, name, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number")
    if not math.isfinite(value) or value <= 0 or (integer and not isinstance(value, int)):
        raise ValueError(f"{name} must be a positive {'integer' if integer else 'number'}")
    return value


@dataclass(frozen=True)
class Limits:
    timeout_seconds: float = 120
    max_turns: int = 10
    max_output_tokens: int = 2048

    def __post_init__(self):
        positive(self.timeout_seconds, "timeout_seconds")
        positive(self.max_turns, "max_turns", integer=True)
        positive(self.max_output_tokens, "max_output_tokens", integer=True)


@dataclass(frozen=True)
class Target:
    id: str
    runner: str
    provider: str
    model: str
    region: str | None = None
    reasoning_effort: str | None = None
    service_tier: str = "default"
    aws_profile: str | None = None
    routing: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.id):
            raise ValueError("target.id must contain 1-64 letters, numbers, underscores, or hyphens")
        if self.runner not in {"native", "codex", "opencode", "demo"}:
            raise ValueError(f"Unsupported runner: {self.runner}")
        if any(not isinstance(v, str) or not v.strip() for v in (self.provider, self.model, self.service_tier)):
            raise ValueError("provider, model, and service_tier must be nonempty strings")
        if self.runner == "native" and self.provider not in {
            "openai", "bedrock-mantle", "bedrock-runtime", "openrouter"
        }:
            raise ValueError("Native provider must be openai, bedrock-mantle, bedrock-runtime, or openrouter")
        if self.runner == "codex" and self.provider not in {"openai", "amazon-bedrock"}:
            raise ValueError("Codex adapter supports the openai and amazon-bedrock built-in providers")
        if self.runner == "demo" and self.provider != "synthetic":
            raise ValueError("The demo runner must use provider=synthetic")
        if self.service_tier != "default" and (
            self.runner in {"codex", "opencode"} or self.provider == "openrouter"
        ):
            raise ValueError("This adapter currently supports only the default service tier")
        if "bedrock" in self.provider and (
            not isinstance(self.region, str) or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", self.region)
        ):
            raise ValueError("Bedrock targets require an explicit AWS region")
        if self.routing and (self.runner != "native" or self.provider != "openrouter"):
            raise ValueError("routing applies only to native OpenRouter targets")
        if not isinstance(self.routing, list) or any(not isinstance(v, str) or not v for v in self.routing):
            raise ValueError("routing must be a list of provider names")
        for name in ("region", "reasoning_effort", "aws_profile"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a nonempty string or null")


@dataclass(frozen=True)
class Experiment:
    name: str
    targets: list[Target]
    tasks: list[str]
    repetitions: int = 1
    seed: int = 42
    limits: Limits = field(default_factory=Limits)
    rate_cards: list[dict] = field(default_factory=list)
    schema_version: int = 1

    def __post_init__(self):
        if self.schema_version != 1:
            raise ValueError("Unsupported experiment schema_version")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Experiment name is required")
        positive(self.repetitions, "repetitions", integer=True)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        if not self.targets or len({t.id for t in self.targets}) != len(self.targets):
            raise ValueError("Targets must be nonempty and have unique IDs")
        if (not isinstance(self.tasks, list) or not self.tasks
                or any(not isinstance(task, str) for task in self.tasks)
                or len(set(self.tasks)) != len(self.tasks)):
            raise ValueError("Tasks must be nonempty and unique")
        if not isinstance(self.rate_cards, list):
            raise ValueError("rate_cards must be an array")
        if any(t.runner == "demo" for t in self.targets) and any(t.runner != "demo" for t in self.targets):
            raise ValueError("Synthetic and live targets must be separate experiments")
        from .tasks import TASK_IDS
        from .costs import validate_rate_card
        for task in self.tasks:
            if task not in TASK_IDS:
                raise ValueError(f"Unknown task {task!r}; choose from {', '.join(TASK_IDS)}")
        for card in self.rate_cards:
            validate_rate_card(card)
        keys = [tuple(c.get(k) for k in ("provider", "model", "region", "service_tier")) for c in self.rate_cards]
        if len(keys) != len(set(keys)):
            raise ValueError("Rate cards must have unique provider/model/region/service_tier keys")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data["targets"] = [Target(**t) for t in data["targets"]]
        data["limits"] = Limits(**data.get("limits", {}))
        return cls(**data)

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text()))

    def to_dict(self):
        return asdict(self)

    def plan(self):
        from .tasks import make_task, protocol
        return {
            "name": self.name,
            "mode": "synthetic" if self.targets[0].runner == "demo" else "live",
            "attempts": len(self.targets) * len(self.tasks) * self.repetitions,
            "protocol_hash": fingerprint(protocol(self)),
            "targets": [asdict(t) for t in self.targets],
            "tasks": [{"id": t, "description": make_task(t, self.seed).description} for t in self.tasks],
            "repetitions": self.repetitions,
            "limits": asdict(self.limits),
            "limit_support": {
                "native": "Controller wall timeout; max model calls and output tokens per response",
                "codex": "Controller wall timeout; internal model-call/output limits are client-managed",
                "opencode": "Controller wall timeout; agent steps; output limit is client-managed",
            },
            "cost_scope": "Inference only; excludes runner infrastructure, subscriptions, external tools, and judges",
            "execution": "No model calls until run --execute; demo uses synthetic data",
        }
