"""Small Harbor adapters that keep Bedrock credentials out of job configurations.

Loaded only by the optional Harbor runtime, never by offline catalog/plan commands.
"""

from __future__ import annotations

import os

from harbor.agents.installed.codex import Codex
from harbor.agents.installed.opencode import OpenCode


def bedrock_environment():
    token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "").strip()
    region = os.environ.get("BEDROCK_BENCH_MODEL_REGION", "").strip()
    if not token or not region:
        raise ValueError("Container agents on Bedrock require AWS_BEARER_TOKEN_BEDROCK and an explicit model region")
    return {"AWS_BEARER_TOKEN_BEDROCK": token, "AWS_REGION": region, "AWS_DEFAULT_REGION": region}


class BedrockCodex(Codex):
    """Use the client's built-in Bedrock provider inside Harbor's container."""

    def __init__(self, *args, **kwargs):
        # Harbor snapshots extra_env before setup/run; adding it in run is too late.
        kwargs["extra_env"] = {**(kwargs.get("extra_env") or {}), **bedrock_environment()}
        super().__init__(*args, config={"model_provider": "amazon-bedrock"}, **kwargs)

    def to_agent_info(self):
        info = super().to_agent_info()
        if info.model_info is not None:
            info.model_info.provider = "amazon-bedrock"
        return info


class BedrockOpenCode(OpenCode):
    """Forward the inference token without persisting it in AgentConfig.env."""

    def __init__(self, *args, **kwargs):
        kwargs["extra_env"] = {**(kwargs.get("extra_env") or {}), **bedrock_environment()}
        super().__init__(*args, **kwargs)
