"""Shared, bounded Bedrock Responses calls for the ARC pilots."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import re
import time

from eval_utils import resolve_effort, response_options


def positive_int(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def positive_float(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return value


def add_common_arguments(parser):
    parser.add_argument("--backend", choices=["mantle", "runtime"], default="mantle")
    parser.add_argument("--model", default="openai.gpt-6-astra")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    parser.add_argument("--effort", default="low", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--max-output-tokens", type=positive_int, default=4096)
    parser.add_argument("--max-input-bytes", type=positive_int, default=100_000)
    parser.add_argument("--budget-usd", type=positive_float, default=10.0,
                        help="estimated uncached token-cost limit, per invocation")
    parser.add_argument("--input-rate", type=positive_float, help="USD per million input tokens")
    parser.add_argument("--output-rate", type=positive_float, help="USD per million output tokens")
    parser.add_argument("--output-dir", type=Path, required=True, help="new directory")
    parser.add_argument("--execute", action="store_true", help="make paid Bedrock calls")


def write_json(path, data):
    """Checkpoint atomically, including on early termination."""
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def new_run(args, suite):
    resolve_effort(args.model, args.effort)
    if not (args.model.startswith("openai.") or ".openai." in args.model):
        raise ValueError("select an OpenAI model ID advertised by Amazon Bedrock")
    if (args.input_rate is None) != (args.output_rate is None):
        raise ValueError("provide both --input-rate and --output-rate")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    directory = Path(__file__).resolve().parent
    sources = ["arc_common.py", "eval_utils.py", "quick_evals.py",
               "arc_agi2.py" if suite == "arc-agi-2" else "arc_agi3.py"]
    return {
        "schema_version": 1, "suite": suite, "status": "planned",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend": args.backend, "model": args.model, "region": args.region,
        "reasoning_effort": args.effort, "max_output_tokens": args.max_output_tokens,
        "max_input_bytes": args.max_input_bytes, "budget_usd": args.budget_usd,
        "rate_override": {"input": args.input_rate, "output": args.output_rate}
        if args.input_rate is not None else None,
        "harness_sha256": {name: digest((directory / name).read_bytes()) for name in sources},
        "label": "public-set pilot; not an official ARC leaderboard result",
        "results": [],
    }


class RunLimit(RuntimeError):
    pass


def model_cost(backend, model, region, input_tokens, output_tokens):
    """ARC-only Standard rates; historical result pricing remains unchanged."""
    from quick_evals import call_cost_usd
    if region not in ("us-west-2", "us-east-1", "us-east-2"):
        return None
    canonical = re.sub(r"^(?:us|global)\.", "", model)
    aliases = {"openai.gpt-5.4-2026-03-05": "openai.gpt-5.4",
               "openai.gpt-5.5-2026-04-23": "openai.gpt-5.5"}
    canonical = aliases.get(canonical, canonical)
    # Verified 2026-09-11: https://aws.amazon.com/bedrock/pricing/
    # Frontier additions: AWS model cards linked in docs/arc-benchmarks.md.
    additional = {
        "openai.gpt-5.5": (5.50, 33.00),
        "openai.gpt-5.6-cyber": (13.75, 82.50),
        "openai.gpt-daybreak-blue-5.6-sol": (5.50, 33.00),
        "openai.gpt-oss-20b": (0.07, 0.30),
        "openai.gpt-oss-120b": (0.15, 0.60),
        "openai.gpt-oss-safeguard-20b": (0.07, 0.20),
        "openai.gpt-oss-safeguard-120b": (0.15, 0.60),
    }
    if model.startswith("global.") and canonical != "openai.gpt-6-astra":
        return None  # Require verified explicit rates for this optional route.
    if canonical in additional:
        rates = additional[canonical]
        if input_tokens > 272_000:
            if canonical != "openai.gpt-daybreak-blue-5.6-sol":
                return None
            rates = (11.0, 49.5)
        return (input_tokens * rates[0] + output_tokens * rates[1]) / 1e6
    if input_tokens > 272_000 and canonical != "openai.gpt-6-astra":
        return None
    priced_model = model if canonical == "openai.gpt-6-astra" else canonical
    return call_cost_usd(backend, priced_model, input_tokens, output_tokens)


@dataclass
class TokenBudget:
    limit: float
    cost: object
    spent: float = 0.0
    reserved: float = 0.0

    def reserve(self, input_bound, output_bound):
        if self.reserved:
            raise RunLimit("previous call has unsettled usage; refusing another paid call")
        estimate = self.cost(input_bound, output_bound)
        if estimate is None:
            raise RunLimit("unknown model price; supply --input-rate and --output-rate")
        if self.spent + estimate > self.limit:
            raise RunLimit("estimated token-cost budget reached before the next call")
        self.reserved = estimate

    def settle(self, input_tokens, output_tokens):
        cost = self.cost(input_tokens, output_tokens)
        if cost is None:
            raise RunLimit("cannot account for response usage")
        self.spent += cost
        self.reserved = 0
        return cost


class BedrockSession:
    """One model session. Never silently retry, truncate, or switch endpoints."""

    def __init__(self, args):
        from quick_evals import make_client
        os.environ["AWS_REGION"] = args.region
        self.args = args
        self.plain_messages = "gpt-oss-" in args.model
        client, self.endpoint = make_client(args.backend)
        if args.backend == "mantle" and "gpt-oss-" in args.model:
            self.endpoint = self.endpoint.replace("/openai/v1", "/v1")
        self.client = client.with_options(base_url=self.endpoint, max_retries=0, timeout=120)
        if args.input_rate is not None:
            # Explicit overrides must cover the entire configured context range.
            cost = lambda i, o: (i * args.input_rate + o * args.output_rate) / 1e6
        else:
            cost = lambda i, o: model_cost(args.backend, args.model, args.region, i, o)
        self.budget = TokenBudget(args.budget_usd, cost)
        self.previous_context_tokens = 0
        self.unaccounted_response = None

    def reset_context(self):
        self.previous_context_tokens = 0

    def call(self, history, *, compaction_threshold=None):
        body_bytes = len(json.dumps(history, ensure_ascii=False).encode())
        if body_bytes > self.args.max_input_bytes:
            raise RunLimit("input byte limit reached; no history was silently truncated")
        # UTF-8 byte length bounds ordinary text tokenization. Add the previously
        # reported context size to cover opaque retained reasoning, plus framing.
        input_bound = body_bytes + self.previous_context_tokens + 4096
        self.budget.reserve(input_bound, self.args.max_output_tokens)
        options = response_options(self.args.model, self.args.effort)
        options.update(store=False, service_tier="default")
        if "gpt-oss-" not in self.args.model:
            options["include"] = ["reasoning.encrypted_content"]
        if compaction_threshold is not None:
            # extra_body also works with SDK releases predating this parameter.
            options["extra_body"] = {"context_management": [
                {"type": "compaction", "compact_threshold": compaction_threshold}]}
        start = time.perf_counter()
        response = self.client.responses.create(
            model=self.args.model, input=history,
            max_output_tokens=self.args.max_output_tokens, **options)
        elapsed = time.perf_counter() - start
        usage = response.usage
        if usage is None:
            self.unaccounted_response = response.model_dump(mode="json", exclude_none=True)
            raise RuntimeError(
                f"response status={response.status} has no usage; see unaccounted_response "
                "in the manifest; the call reservation is retained")
        cost = self.budget.settle(usage.input_tokens, usage.output_tokens)
        self.previous_context_tokens = usage.input_tokens + usage.output_tokens
        return {
            "response_id": response.id, "text": response.output_text,
            "status": response.status, "elapsed_seconds": elapsed,
            "usage": usage.model_dump(mode="json"),
            "estimated_cost_usd": cost,
            "output": [item.model_dump(mode="json", exclude_none=True) for item in response.output],
        }


def finish(run, args, session=None):
    if session is not None:
        run["endpoint"] = session.endpoint
        run["client_versions"] = {p: version(p) for p in ("openai", "boto3")}
        run["estimated_cost_usd"] = session.budget.spent
        run["unsettled_call_reservation_usd"] = session.budget.reserved
        if session.unaccounted_response is not None:
            run["unaccounted_response"] = session.unaccounted_response
    run["cost_basis"] = (
        "uncached token list-price estimate; excludes cache-write premiums and service fees; "
        "explicit rate overrides apply to the entire context range")
    run["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(args.output_dir / "manifest.json", run)
