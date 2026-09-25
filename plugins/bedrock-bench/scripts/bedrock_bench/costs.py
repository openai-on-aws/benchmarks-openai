"""Explicit accounting: unknown costs stay unknown; token subsets are not additive."""

from __future__ import annotations

from datetime import date
import math
from urllib.parse import urlparse


FIELDS = (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens",
)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def count(value):
    value = number(value)
    return int(value) if value is not None and value == int(value) else None


def normalize_usage(source, raw):
    if not isinstance(raw, dict):
        return dict.fromkeys(FIELDS)
    if source == "opencode":
        # OpenCode emits uncached input and visible output; add their subsets
        # once to align with Responses/Codex totals. Preserve raw events too.
        cached = count((raw.get("cache") or {}).get("read"))
        written = count((raw.get("cache") or {}).get("write"))
        reasoning = count(raw.get("reasoning"))
        inp, out = count(raw.get("input")), count(raw.get("output"))
        usage = {
            "input_tokens": complete_sum([inp, cached, written]),
            "cached_input_tokens": cached,
            "cache_write_input_tokens": written,
            "output_tokens": complete_sum([out, reasoning]),
            "reasoning_output_tokens": reasoning,
        }
    elif source == "openrouter":
        details = raw.get("prompt_tokens_details") or {}
        usage = {
            "input_tokens": count(raw.get("prompt_tokens")),
            "cached_input_tokens": count(details.get("cached_tokens")),
            "cache_write_input_tokens": count(details.get("cache_write_tokens", 0)),
            "output_tokens": count(raw.get("completion_tokens")),
            "reasoning_output_tokens": count((raw.get("completion_tokens_details") or {}).get("reasoning_tokens")),
        }
    else:
        usage = {
            "input_tokens": count(raw.get("input_tokens")),
            "cached_input_tokens": count(raw.get("cached_input_tokens")) if source == "codex"
                else count((raw.get("input_tokens_details") or {}).get("cached_tokens")),
            "cache_write_input_tokens": 0,  # These adapters do not request explicit cache writes.
            "output_tokens": count(raw.get("output_tokens")),
            "reasoning_output_tokens": count(raw.get("reasoning_output_tokens")) if source == "codex"
                else count((raw.get("output_tokens_details") or {}).get("reasoning_tokens")),
        }
    known_input = usage["input_tokens"]
    subsets = complete_sum([usage["cached_input_tokens"], usage["cache_write_input_tokens"]])
    if known_input is not None and subsets is not None and subsets > known_input:
        return dict.fromkeys(FIELDS)
    if (usage["reasoning_output_tokens"] is not None and usage["output_tokens"] is not None
            and usage["reasoning_output_tokens"] > usage["output_tokens"]):
        return dict.fromkeys(FIELDS)
    return usage


def complete_sum(values):
    values = list(values)
    return sum(values) if values and all(v is not None for v in values) else None


def sum_usage(records):
    return {key: complete_sum(r.get(key) for r in records) for key in FIELDS}


def validate_rate_card(card):
    required = {
        "provider", "model", "region", "service_tier", "as_of", "source",
        "input_usd_per_million", "output_usd_per_million",
    }
    allowed = required | {
        "cached_input_usd_per_million", "cache_write_input_usd_per_million",
        "max_input_tokens",
    }
    if not isinstance(card, dict) or required - card.keys() or card.keys() - allowed:
        raise ValueError("Rate card has missing or unsupported fields")
    for key in ("provider", "model", "service_tier", "source"):
        if not isinstance(card[key], str) or not card[key]:
            raise ValueError(f"Rate card {key} must be a nonempty string")
    if card["region"] is not None and not isinstance(card["region"], str):
        raise ValueError("Rate card region must be a string or null")
    date.fromisoformat(card["as_of"])
    if urlparse(card["source"]).scheme != "https" or not urlparse(card["source"]).hostname:
        raise ValueError("Rate card source must be an HTTPS reference")
    for key, value in card.items():
        if key.endswith("_usd_per_million") and number(value) is None:
            raise ValueError(f"Invalid rate: {key}")
    if "max_input_tokens" in card and (count(card["max_input_tokens"]) or 0) <= 0:
        raise ValueError("max_input_tokens must be a positive integer")


def find_rate(cards, target):
    return next((card for card in cards if all(
        card.get(key) == getattr(target, key)
        for key in ("provider", "model", "region", "service_tier")
    )), None)


def estimate(usage, card):
    if card is None:
        return None
    inp, out = usage["input_tokens"], usage["output_tokens"]
    cached, written = usage["cached_input_tokens"], usage["cache_write_input_tokens"]
    if inp is None or out is None:
        return None
    if card.get("max_input_tokens") is not None and inp > card["max_input_tokens"]:
        return None
    if cached is None or written is None:
        return None
    if cached and "cached_input_usd_per_million" not in card:
        return None
    if written and "cache_write_input_usd_per_million" not in card:
        return None
    cost = (
        (inp - cached - written) * card["input_usd_per_million"]
        + cached * card.get("cached_input_usd_per_million", 0)
        + written * card.get("cache_write_input_usd_per_million", 0)
        + out * card["output_usd_per_million"]
    ) / 1_000_000
    return round(cost, 12)


def charge(source, raw, usage, card, *, emitted_cost=None, served_tier=None):
    if source == "openrouter" and number((raw or {}).get("cost")) is not None:
        return {"usd": raw["cost"], "basis": "provider_reported"}
    # A zero OpenCode catalog estimate can mean missing model prices. Only an
    # explicit rate card can establish a free run in that case.
    if source == "opencode" and number(emitted_cost) is not None and emitted_cost > 0:
        return {"usd": emitted_cost, "basis": "runner_estimate"}
    if served_tier is not None and card is not None and served_tier != card["service_tier"]:
        return {"usd": None, "basis": "unknown", "reason": "Served service tier differs from rate card"}
    value = estimate(usage, card)
    return {"usd": value, "basis": "rate_card_estimate" if value is not None else "unknown"}
