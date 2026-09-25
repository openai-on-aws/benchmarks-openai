"""A fixed filesystem-tool agent loop for controlled model/provider comparisons."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

from .config import Limits, Target
from .tasks import execute_tool, SYSTEM_PROMPT, TOOLS


def emit(kind, **data):
    print(json.dumps({"type": kind, **data}, allow_nan=False), flush=True)


def make_client(target, timeout):
    # Optional dependencies are only imported for explicitly executed API runs.
    from openai import OpenAI
    if target.provider.startswith("bedrock-"):
        token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        if not token:
            from aws_bedrock_token_generator import provide_token
            token = provide_token(region=target.region)
        host = (
            f"https://bedrock-mantle.{target.region}.api.aws/openai/v1"
            if target.provider == "bedrock-mantle"
            else f"https://bedrock-runtime.{target.region}.amazonaws.com/openai/v1"
        )
        return OpenAI(api_key=token, base_url=host, max_retries=0, timeout=timeout)
    if target.provider == "openrouter":
        return OpenAI(api_key=os.environ["OPENROUTER_API_KEY"],
                      base_url="https://openrouter.ai/api/v1", max_retries=0, timeout=timeout)
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY_SAAS") or os.environ["OPENAI_API_KEY"],
                  base_url="https://api.openai.com/v1", max_retries=0, timeout=timeout)


def tool_result(workspace, call_name, raw_arguments):
    try:
        arguments = json.loads(raw_arguments)
    except (ValueError, TypeError):
        result = {"error": "Tool arguments are not valid JSON"}
    else:
        result = execute_tool(workspace, call_name, arguments)
    emit("tool", name=call_name, arguments=raw_arguments, result=result)
    return json.dumps(result)


def responses_loop(client, target, limits, prompt, workspace):
    history = [{"role": "user", "content": prompt}]
    options = {"reasoning": {"effort": target.reasoning_effort}} if target.reasoning_effort else {}
    for step in range(1, limits.max_turns + 1):
        started = time.perf_counter()
        response = client.responses.create(
            model=target.model, instructions=SYSTEM_PROMPT, input=history, tools=TOOLS,
            max_output_tokens=limits.max_output_tokens, service_tier=target.service_tier,
            store=False, include=["reasoning.encrypted_content"], **options,
        )
        emit("step", step=step, source="responses",
             usage=response.usage.model_dump() if response.usage else None,
             response_id=response.id, observed_model=response.model,
             service_tier=getattr(response, "service_tier", None),
             response_status=response.status, wall_seconds=time.perf_counter() - started)
        if response.status != "completed":
            emit("error", error=f"Response ended with status={response.status}", accounting_complete=True)
            return
        calls = [item for item in response.output if item.type == "function_call"]
        # Preserve reasoning items and function call IDs throughout the loop.
        history.extend(item.model_dump(exclude_none=True) for item in response.output)
        if not calls:
            emit("completed", final_text=response.output_text)
            return
        for call in calls:
            result = tool_result(workspace, call.name, call.arguments)
            history.append({"type": "function_call_output", "call_id": call.call_id, "output": result})
    emit("error", error="Maximum model turns reached", code="turn_limit", accounting_complete=True)


def openrouter_loop(client, target, limits, prompt, workspace):
    history = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    tools = [{"type": "function", "function": {k: v for k, v in tool.items() if k != "type"}} for tool in TOOLS]
    extra = {}
    if target.reasoning_effort:
        extra["reasoning"] = {"effort": target.reasoning_effort}
    if target.routing:
        extra["provider"] = {"order": target.routing, "allow_fallbacks": False}
    for step in range(1, limits.max_turns + 1):
        started = time.perf_counter()
        response = client.chat.completions.create(
            model=target.model, messages=history, tools=tools,
            max_completion_tokens=limits.max_output_tokens, extra_body=extra,
        )
        emit("step", step=step, source="openrouter",
             usage=response.usage.model_dump() if response.usage else None,
             response_id=response.id, observed_model=response.model,
             upstream_provider=getattr(response, "provider", None),
             wall_seconds=time.perf_counter() - started)
        if not response.choices:
            emit("error", error="Response has no choices", accounting_complete=True)
            return
        choice = response.choices[0]
        message = choice.message
        history.append(message.model_dump(exclude_none=True))
        if choice.finish_reason not in ("stop", "tool_calls"):
            emit("error", error=f"Response ended with finish_reason={choice.finish_reason}", accounting_complete=True)
            return
        if not message.tool_calls:
            emit("completed", final_text=message.content or "")
            return
        for call in message.tool_calls:
            result = tool_result(workspace, call.function.name, call.function.arguments)
            history.append({"role": "tool", "tool_call_id": call.id, "content": result})
    emit("error", error="Maximum model turns reached", code="turn_limit", accounting_complete=True)


def worker(config_path, workspace):
    config = json.loads(Path(config_path).read_text())
    target, limits = Target(**config["target"]), Limits(**config["limits"])
    try:
        with make_client(target, limits.timeout_seconds) as client:
            loop = openrouter_loop if target.provider == "openrouter" else responses_loop
            loop(client, target, limits, config["prompt"], Path(workspace))
    except Exception as exc:
        # The controller records this as a failed attempt with incomplete cost.
        emit("error", error=str(exc), error_type=type(exc).__name__,
             status_code=getattr(exc, "status_code", None),
             request_id=getattr(exc, "request_id", None))
        return 1
    return 0
