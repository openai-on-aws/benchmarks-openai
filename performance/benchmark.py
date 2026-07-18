"""
Unified latency/performance benchmark: OpenAI models on Bedrock ("Mantle") vs OpenAI SaaS (1P).

Both backends are exercised through the same code path — the Responses API with
streaming — so results are directly comparable.

Metrics per call:
  - TTFT (ms)            time to first output-text delta
  - ITL (ms)             inter-chunk latency between successive text deltas (mean + p95 per call)
  - OTPS                 output tokens / second of generation time
  - E2E (ms)             end-to-end wall time
  - token usage          input / output / reasoning / cached tokens

Usage:
  python performance/benchmark.py --backend bedrock 1k 5k --runs=5
  python performance/benchmark.py --backend openai --model gpt-5.6-luna 1k
  python performance/benchmark.py --backend bedrock --list-models
"""

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from openai import OpenAI

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

DEFAULT_MODELS = {
    "bedrock": "openai.gpt-5.6-luna",
    "openai": "gpt-5.6-luna",
}

PROMPTS = {
    "1k":  {"file": "prompt_1k.txt",  "tokens": 1002},
    "5k":  {"file": "prompt_5k.txt",  "tokens": 4750},
    "10k": {"file": "prompt_10k.txt", "tokens": 9984},
    "20k": {"file": "prompt_20k.txt", "tokens": 20136},
}

OUTPUT_CONFIGS = {
    "1k":  [100, 500, 1000],
    "5k":  [500, 1000, 5000],
    "10k": [500, 1000, 5000],
    "20k": [1000, 5000, 10000],
}

INSTRUCTION = "\n\nContinue writing about this topic in detail."

RETRYABLE_MARKERS = (
    "connection reset", "readerror", "remoteprotocolerror", "rate_limit",
    "429", "500", "502", "503", "overloaded", "timeout", "timed out",
)


def bedrock_base_url():
    region = os.environ.get("AWS_REGION", "us-west-2")
    return f"https://bedrock-mantle.{region}.api.aws/openai/v1"


def make_client(backend, base_url):
    if backend == "bedrock":
        token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        if not token:
            from aws_bedrock_token_generator import provide_token
            token = provide_token(region=os.environ.get("AWS_REGION", "us-west-2"))
        return OpenAI(api_key=token, base_url=base_url)
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY is not set — export your OpenAI 1P key first.")
    return OpenAI(api_key=key, base_url=base_url)


def list_models(backend, base_url):
    # The Bedrock endpoint serves models.list from /v1, not /openai/v1.
    if backend == "bedrock":
        base_url = base_url.replace("/openai/v1", "/v1")
    client = make_client(backend, base_url)
    ids = sorted(m.id for m in client.models.list())
    for mid in ids:
        print(mid)
    return ids


def load_prompt(label):
    path = os.path.join(DATA_DIR, PROMPTS[label]["file"])
    return open(path).read() + INSTRUCTION


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    idx = (p / 100) * (len(sorted_vals) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def summarize(values):
    s = sorted(v for v in values if v is not None)
    if not s:
        return None
    return {
        "mean":   round(statistics.mean(s), 1),
        "stddev": round(statistics.stdev(s), 1) if len(s) > 1 else 0.0,
        "p50":    round(percentile(s, 50), 1),
        "p95":    round(percentile(s, 95), 1),
        "p99":    round(percentile(s, 99), 1),
        "min":    round(min(s), 1),
        "max":    round(max(s), 1),
    }


def run_single(client, model, prompt, max_output_tokens, effort):
    first_token_time = None
    last_delta_time = None
    itl_gaps = []
    output_tokens = 0
    input_tokens = 0
    reasoning_tokens = 0
    cached_tokens = 0
    status = None

    kwargs = {}
    if effort:
        kwargs["reasoning"] = {"effort": effort}

    start = time.perf_counter()
    stream = client.responses.create(
        model=model,
        input=[{"role": "user", "content": prompt}],
        max_output_tokens=max_output_tokens,
        stream=True,
        **kwargs,
    )

    for event in stream:
        now = time.perf_counter()
        event_type = getattr(event, "type", None)

        if event_type == "response.output_text.delta":
            if getattr(event, "delta", None):
                if first_token_time is None:
                    first_token_time = now
                else:
                    itl_gaps.append((now - last_delta_time) * 1000)
                last_delta_time = now

        elif event_type in ("response.completed", "response.incomplete", "response.failed"):
            resp = getattr(event, "response", None)
            status = getattr(resp, "status", None) if resp else None
            usage = getattr(resp, "usage", None) if resp else None
            if usage:
                output_tokens = getattr(usage, "output_tokens", 0) or 0
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                otd = getattr(usage, "output_tokens_details", None)
                reasoning_tokens = getattr(otd, "reasoning_tokens", 0) or 0
                itd = getattr(usage, "input_tokens_details", None)
                cached_tokens = getattr(itd, "cached_tokens", 0) or 0

    end = time.perf_counter()

    e2e = end - start
    ttft = (first_token_time - start) if first_token_time else None
    gen_time = (end - first_token_time) if first_token_time else e2e
    # OTPS is only meaningful when output actually streamed over time; if the
    # whole response arrived in a single flush (gen_time ~0), report None.
    if itl_gaps and gen_time > 0 and output_tokens > 0:
        otps = output_tokens / gen_time
    else:
        otps = None
    itl_sorted = sorted(itl_gaps)

    return {
        "max_output_tokens": max_output_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cached_tokens": cached_tokens,
        "ttft_ms": round(ttft * 1000, 1) if ttft else None,
        "itl_mean_ms": round(statistics.mean(itl_sorted), 2) if itl_sorted else None,
        "itl_p95_ms": round(percentile(itl_sorted, 95), 2) if itl_sorted else None,
        "otps": round(otps, 1) if otps is not None else None,
        "e2e_ms": round(e2e * 1000, 1),
        "status": status,
        "error": None,
    }


def run_with_retries(backend, base_url, model, prompt, max_out, effort, client_box, max_retries=5):
    for attempt in range(max_retries):
        try:
            return run_single(client_box[0], model, prompt, max_out, effort)
        except Exception as e:
            err = str(e)
            if attempt < max_retries - 1 and any(m in err.lower() for m in RETRYABLE_MARKERS):
                wait = 2 ** attempt * 2
                print(f" [retryable error, wait {wait}s: {err[:80]}]", end="", flush=True)
                time.sleep(wait)
                # Recreate the client: drops dead connections and re-mints the Bedrock token.
                client_box[0] = make_client(backend, base_url)
                continue
            return {"max_output_tokens": max_out, "input_tokens": 0, "output_tokens": 0,
                    "reasoning_tokens": 0, "cached_tokens": 0, "ttft_ms": None,
                    "itl_mean_ms": None, "itl_p95_ms": None, "otps": None, "e2e_ms": None,
                    "status": "error", "error": err[:300]}
    return {"max_output_tokens": max_out, "input_tokens": 0, "output_tokens": 0,
            "reasoning_tokens": 0, "cached_tokens": 0, "ttft_ms": None,
            "itl_mean_ms": None, "itl_p95_ms": None, "otps": None, "e2e_ms": None,
            "status": "error", "error": "max retries exceeded"}


def run_benchmark(backend, base_url, model, input_label, output_configs, n_runs, effort, concurrency, tag):
    prompt = load_prompt(input_label)
    nominal_input_tokens = PROMPTS[input_label]["tokens"]
    total_calls = len(output_configs) * n_runs

    started_at = datetime.now(timezone.utc)
    print(f"\nBenchmark: {input_label} input (~{nominal_input_tokens} tokens)")
    print(f"Backend:   {backend} | Model: {model}")
    print(f"Endpoint:  {base_url}")
    print(f"Runs:      {n_runs} per config x {output_configs} = {total_calls} calls"
          + (f" | concurrency={concurrency}" if concurrency > 1 else "")
          + (f" | effort={effort}" if effort else ""))
    print(f"Started:   {started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    raw = {max_out: [] for max_out in output_configs}
    call_num = 0

    for max_out in output_configs:
        # Fresh client per config: keeps the Bedrock bearer token well inside its validity window.
        client_box = [make_client(backend, base_url)]

        if concurrency > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(run_with_retries, backend, base_url, model,
                                       prompt, max_out, effort, client_box)
                           for _ in range(n_runs)]
                for i, fut in enumerate(futures):
                    r = fut.result()
                    raw[max_out].append(r)
                    call_num += 1
                    print(f"  [{call_num:>3}/{total_calls}] max_out={max_out:>5} run={i+1:>2}/{n_runs}  "
                          f"ttft={r['ttft_ms']}ms  otps={r['otps']}  e2e={r['e2e_ms']}ms"
                          + (f"  ERROR: {r['error'][:80]}" if r["error"] else ""))
        else:
            for run_idx in range(n_runs):
                call_num += 1
                print(f"  [{call_num:>3}/{total_calls}] max_out={max_out:>5} run={run_idx+1:>2}/{n_runs} ... ",
                      end="", flush=True)
                r = run_with_retries(backend, base_url, model, prompt, max_out, effort, client_box)
                raw[max_out].append(r)
                if r["error"]:
                    print(f"ERROR: {r['error'][:100]}")
                else:
                    print(f"ttft={r['ttft_ms']}ms  otps={r['otps']}  e2e={r['e2e_ms']}ms")

    ended_at = datetime.now(timezone.utc)

    summary = []
    print(f"\n{'='*88}")
    print(f"SUMMARY: {backend} | {model} | {input_label} input | {n_runs} runs per config")
    print(f"{'='*88}")
    print(f"{'Max Out':>8}  {'Metric':>12}  {'Mean':>8}  {'Stddev':>8}  {'p50':>8}  {'p95':>8}  {'p99':>8}  {'Min':>8}  {'Max':>8}")
    print(f"{'-'*88}")

    for max_out in output_configs:
        runs = raw[max_out]
        ok = [r for r in runs if not r["error"]]
        errors = len(runs) - len(ok)

        stats_by_metric = {
            "TTFT(ms)":    summarize([r["ttft_ms"] for r in ok]),
            "ITL(ms)":     summarize([r["itl_mean_ms"] for r in ok]),
            "Tok/s":       summarize([r["otps"] for r in ok if r["otps"]]),
            "E2E(ms)":     summarize([r["e2e_ms"] for r in ok]),
        }
        for metric, stats in stats_by_metric.items():
            if stats is None:
                continue
            print(f"{max_out:>8}  {metric:>12}  {stats['mean']:>8}  {stats['stddev']:>8}  "
                  f"{stats['p50']:>8}  {stats['p95']:>8}  {stats['p99']:>8}  "
                  f"{stats['min']:>8}  {stats['max']:>8}")
        if errors:
            print(f"{max_out:>8}  {'errors':>12}  {errors}/{len(runs)} calls failed")

        summary.append({
            "max_output_tokens": max_out,
            "n_runs": n_runs,
            "n_errors": errors,
            "ttft_ms": stats_by_metric["TTFT(ms)"],
            "itl_ms": stats_by_metric["ITL(ms)"],
            "otps": stats_by_metric["Tok/s"],
            "e2e_ms": stats_by_metric["E2E(ms)"],
            "reasoning_tokens_mean": round(statistics.mean([r["reasoning_tokens"] for r in ok]), 1) if ok else None,
            "cached_tokens_mean": round(statistics.mean([r["cached_tokens"] for r in ok]), 1) if ok else None,
        })

    duration = (ended_at - started_at).total_seconds()
    print(f"{'='*88}")
    print(f"Ended:     {ended_at.strftime('%Y-%m-%d %H:%M:%S UTC')} | Duration: {duration/60:.1f} min")

    safe_model = model.replace("/", "-").replace(":", "-")
    ts = started_at.strftime("%Y%m%d_%H%M%S")
    parts = [f"results_{backend}_{safe_model}_{input_label}input_{n_runs}runs"]
    if concurrency > 1:
        parts.append(f"c{concurrency}")
    if effort:
        parts.append(effort)
    if tag:
        parts.append(tag)
    filename = os.path.join(RESULTS_DIR, "_".join(parts) + f"_{ts}.json")

    payload = {
        "schema_version": 2,
        "backend": backend,
        "model": model,
        "base_url": base_url,
        "input_label": input_label,
        "nominal_input_tokens": nominal_input_tokens,
        "n_runs": n_runs,
        "concurrency": concurrency,
        "reasoning_effort": effort,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round(duration, 1),
        "summary": summary,
        "raw": {str(k): v for k, v in raw.items()},
    }
    with open(filename, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Saved:     {os.path.basename(filename)}")
    return payload


def main():
    p = argparse.ArgumentParser(description="Latency benchmark: Bedrock vs OpenAI 1P (Responses API, streaming)")
    p.add_argument("sizes", nargs="*", metavar="SIZE",
                   help="input sizes to run: 1k 5k 10k 20k (default: all)")
    p.add_argument("--backend", choices=["bedrock", "openai"], required=True)
    p.add_argument("--model", help=f"model id (defaults: {DEFAULT_MODELS})")
    p.add_argument("--runs", type=int, default=25, help="runs per output config (default 25)")
    p.add_argument("--outputs", help="comma-separated max_output_tokens overriding the per-size defaults, e.g. 100,1000")
    p.add_argument("--effort", help="reasoning effort to request (e.g. low, medium, high)")
    p.add_argument("--concurrency", type=int, default=1, help="parallel in-flight requests (default 1 = sequential)")
    p.add_argument("--base-url", help="override the backend endpoint")
    p.add_argument("--tag", help="extra tag appended to the results filename")
    p.add_argument("--list-models", action="store_true", help="list model ids available on the backend and exit")
    args = p.parse_args()

    base_url = args.base_url or (
        os.environ.get("MANTLE_BASE_URL", bedrock_base_url()) if args.backend == "bedrock"
        else "https://api.openai.com/v1"
    )

    if args.list_models:
        list_models(args.backend, base_url)
        return

    model = args.model or DEFAULT_MODELS[args.backend]
    sizes = args.sizes or list(PROMPTS.keys())
    bad = [s for s in sizes if s not in PROMPTS]
    if bad:
        p.error(f"unknown input size(s) {bad}; choose from {list(PROMPTS.keys())}")
    os.makedirs(RESULTS_DIR, exist_ok=True)

    for label in sizes:
        configs = [int(x) for x in args.outputs.split(",")] if args.outputs else OUTPUT_CONFIGS[label]
        run_benchmark(args.backend, base_url, model, label, configs,
                      args.runs, args.effort, args.concurrency, args.tag)


if __name__ == "__main__":
    main()
