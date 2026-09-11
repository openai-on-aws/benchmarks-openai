"""Shared utilities for eval scripts."""

import os
import re

# Reasoning-model families that reject the temperature parameter.
_NO_TEMPERATURE_MARKERS = ("5.5", "5.6", "gpt-6-astra")

ASTRA_MODELS = {
    "mantle": "openai.gpt-6-astra",
    "runtime": "us.openai.gpt-6-astra",
    "saas": "gpt-6-astra",
}
ASTRA_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def is_astra(model):
    return re.fullmatch(r"(?:(?:us|global)\.)?(?:openai\.)?gpt-6-astra", model) is not None


def resolve_effort(model, effort):
    """Make Astra's baseline explicit; never silently replace an invalid effort."""
    if is_astra(model):
        if effort is None:
            return "low"
        if effort not in ASTRA_EFFORTS:
            raise ValueError(f"{model} requires reasoning effort in {ASTRA_EFFORTS}; got {effort!r}")
    return effort


def response_options(model, effort=None, temperature=None, *, tools=False):
    effort = resolve_effort(model, effort)
    options = {"reasoning": {"effort": effort}} if effort else {}
    if temperature is not None and supports_temperature(model):
        options["temperature"] = temperature
    if is_astra(model):
        options["service_tier"] = "default"
        if tools:
            options["include"] = ["reasoning.encrypted_content"]
    return options


def legacy_model(backend, model=None):
    """Keep old env defaults while allowing explicit CLI models and Runtime."""
    if model:
        return model
    env, default = {
        "mantle": ("MANTLE_MODEL", "openai.gpt-5.4"),
        "saas": ("SAAS_MODEL", "gpt-5.4"),
        "runtime": ("RUNTIME_MODEL", ASTRA_MODELS["runtime"]),
    }[backend]
    return os.environ.get(env, default)


def sum_costs(costs):
    """An unknown component makes the total unknown, never zero."""
    costs = list(costs)
    return sum(costs) if costs and all(c is not None for c in costs) else None


def rounded_cost(cost, digits=6):
    return round(cost, digits) if cost is not None else None


def supports_temperature(model):
    """Whether the model accepts the temperature parameter."""
    return not any(m in model for m in _NO_TEMPERATURE_MARKERS)


def capture_error(e):
    """Extract all available fields from an API exception."""
    resp = getattr(e, 'response', None)
    return {
        "error_type":    type(e).__name__,
        "error_message": str(e),
        "request_id":    getattr(e, 'request_id', None),
        "status_code":   getattr(e, 'status_code', None),
        "error_code":    getattr(e, 'code', None),
        "error_param":   getattr(e, 'param', None),
        "api_error_type": getattr(e, 'type', None),
        "response_headers": dict(resp.headers) if resp is not None else None,
    }
