"""
DeepSearchQA agentic web-research eval — benchmarks-openai port.

Methodology recreated from a colleague's evaluation (2026-07-21) of
google/deepsearchqa (arXiv:2601.20975): zero-shot agentic loop with
web_search + fetch_page function tools (Tavily-backed, disk-cached),
budgets of 8 searches / 6 fetches / 14 turns per question, stratified
question sample. Our arms are this repo's standard five:

  BR  openai.gpt-5.6-luna / -terra / -sol   (reasoning effort=none)
  1P  gpt-5.4-mini / gpt-5.4-nano           (defaults)

To stay comparable with the colleague's run, --indices default to their
exact 20 stratified dataset indices; the ported search cache makes tool
results identical where queries repeat.

Usage:
  source quality/deepsearchqa/eval.env   # TAVILY_API_KEYS (live search fallback)
  python quality/deepsearchqa/run_deepsearchqa.py --backend mantle --model openai.gpt-5.6-luna --effort none
  python quality/deepsearchqa/run_deepsearchqa.py --backend saas --model gpt-5.4-mini
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from quick_evals import make_client, call_cost_usd, capture_error  # noqa: E402

import search_tool  # noqa: E402  (lives next to this script)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "results")

MAX_SEARCHES = 8
MAX_FETCHES = 6
MAX_TURNS = 14
MAX_OUTPUT_TOKENS = 4096

# The colleague's stratified-20 sample (proportional across the 17
# problem_category values, deterministic). Frozen for comparability.
DEFAULT_INDICES = [0, 1, 3, 4, 5, 6, 7, 9, 11, 12, 13, 15, 18, 24, 31, 35, 47, 80, 125, 187]

# Verbatim from the colleague's run (results-deepsearchqa.json system_prompt).
SYSTEM_PROMPT = """You are a meticulous research assistant. You have two tools:
- web_search(query): returns top web results as JSON (title, url, snippet).
- fetch_page(url): returns the full visible text of a page (~8000 chars).

Budget: 8 searches and 6 page fetches per question. Search to locate sources, then fetch the promising pages to verify exact facts — tables, statistics, and precise values are usually NOT in snippets, so fetch before you commit to a number or name.

Questions may require finding ALL items that satisfy the criteria (a set answer) or a single fact. Be comprehensive: for set-style questions, enumerate every qualifying item you can verify; do not include items you cannot verify. When you are done, end with a clear final answer: a concise list of the item(s) that answer the question."""

TOOLS = [
    {"type": "function", "name": search_tool.TOOL_NAME,
     "description": search_tool.TOOL_DESCRIPTION,
     "parameters": search_tool.TOOL_PARAMETERS, "strict": True},
    {"type": "function", "name": search_tool.FETCH_TOOL_NAME,
     "description": search_tool.FETCH_TOOL_DESCRIPTION,
     "parameters": search_tool.FETCH_TOOL_PARAMETERS, "strict": True},
]


def run_case(client, backend, model, effort, row, idx):
    history = [{"role": "developer", "content": SYSTEM_PROMPT},
               {"role": "user", "content": row["problem"]}]
    kwargs = {"reasoning": {"effort": effort}} if effort else {}

    searches = fetches = live_calls = turns = 0
    tot_in = tot_cached = tot_out = tot_reason = 0
    cost = 0.0
    tool_log = []
    answer = None
    stop = "no_answer"
    t0 = time.perf_counter()

    while turns < MAX_TURNS:
        turns += 1
        try:
            r = client.responses.create(model=model, input=history, tools=TOOLS,
                                        max_output_tokens=MAX_OUTPUT_TOKENS, **kwargs)
        except Exception as e:
            return {"dataset_index": idx, "problem": row["problem"],
                    "problem_category": row["problem_category"],
                    "answer_type": row["answer_type"], "gold": row["answer"],
                    "answer_text": None, "turns": turns, "search_count": searches,
                    "fetch_count": fetches, "live_search_calls": live_calls,
                    "tool_log": tool_log, "input_tokens": tot_in,
                    "cached_tokens": tot_cached, "output_tokens": tot_out,
                    "reasoning_tokens": tot_reason, "cost_usd": round(cost, 6),
                    "latency_s": round(time.perf_counter() - t0, 2),
                    "stop_reason": "api_error", "api_success": False,
                    "error": capture_error(e)}

        u = r.usage
        tot_in += u.input_tokens
        tot_cached += getattr(u.input_tokens_details, "cached_tokens", 0) or 0
        tot_out += u.output_tokens
        tot_reason += getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0
        cost += call_cost_usd(backend, model, u.input_tokens, u.output_tokens) or 0.0

        calls = [o for o in r.output if o.type == "function_call"]
        if not calls:
            answer = r.output_text or ""
            stop = getattr(r, "status", None) or "completed"
            break

        for call in calls:
            history.append({"type": "function_call", "name": call.name,
                            "call_id": call.call_id, "arguments": call.arguments})
            args = json.loads(call.arguments or "{}")
            if call.name == search_tool.TOOL_NAME:
                if searches >= MAX_SEARCHES:
                    payload, cached = json.dumps({"error": "search budget exhausted"}), True
                else:
                    payload, cached = search_tool.web_search(args.get("query", ""))
                    searches += 1
                    live_calls += 0 if cached else 1
                tool_log.append({"tool": "web_search", "query": args.get("query"),
                                 "cached": cached})
            elif call.name == search_tool.FETCH_TOOL_NAME:
                if fetches >= MAX_FETCHES:
                    payload, cached = json.dumps({"error": "fetch budget exhausted"}), True
                else:
                    payload, cached = search_tool.fetch_page(args.get("url", ""))
                    fetches += 1
                tool_log.append({"tool": "fetch_page", "url": args.get("url"),
                                 "cached": cached})
            else:
                payload = json.dumps({"error": f"unknown tool {call.name}"})
            history.append({"type": "function_call_output",
                            "call_id": call.call_id, "output": payload})
    else:
        stop = "max_turns"

    return {"dataset_index": idx, "problem": row["problem"],
            "problem_category": row["problem_category"],
            "answer_type": row["answer_type"], "gold": row["answer"],
            "answer_text": answer, "turns": turns, "search_count": searches,
            "fetch_count": fetches, "live_search_calls": live_calls,
            "tool_log": tool_log, "input_tokens": tot_in,
            "cached_tokens": tot_cached, "output_tokens": tot_out,
            "reasoning_tokens": tot_reason, "cost_usd": round(cost, 6),
            "latency_s": round(time.perf_counter() - t0, 2),
            "stop_reason": stop, "api_success": True, "error": None}


def main():
    p = argparse.ArgumentParser(description="DeepSearchQA agentic web-research eval")
    p.add_argument("--backend", choices=["mantle", "saas"], required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", help="reasoning effort (e.g. none); omit for default")
    p.add_argument("--indices", help="comma-separated dataset indices "
                                     "(default: the colleague-run stratified 20)")
    args = p.parse_args()

    from datasets import load_dataset
    ds = load_dataset("google/deepsearchqa", split="eval")
    indices = ([int(x) for x in args.indices.split(",")] if args.indices
               else DEFAULT_INDICES)

    client, base_url = make_client(args.backend)
    print(f"DeepSearchQA: {len(indices)} questions | {args.backend}/{args.model}"
          + (f" | effort={args.effort}" if args.effort else ""))

    results = []
    for n, idx in enumerate(indices):
        row = ds[idx]
        r = run_case(client, args.backend, args.model, args.effort, row, idx)
        results.append(r)
        print(f"  [{n+1:>2}/{len(indices)}] idx={idx:>3} {row['answer_type']:<13} "
              f"turns={r['turns']:>2} searches={r['search_count']} fetches={r['fetch_count']} "
              f"cost=${r['cost_usd']:.4f} ({r['stop_reason']})")

    ok = [r for r in results if r["api_success"]]
    total_cost = sum(r["cost_usd"] for r in ok)
    live = sum(r["live_search_calls"] for r in ok)
    summary = {
        "n": len(results), "n_api_errors": len(results) - len(ok),
        "mean_turns": round(sum(r["turns"] for r in ok) / len(ok), 2) if ok else None,
        "mean_searches": round(sum(r["search_count"] for r in ok) / len(ok), 2) if ok else None,
        "mean_fetches": round(sum(r["fetch_count"] for r in ok) / len(ok), 2) if ok else None,
        "live_search_calls": live,
        "mean_latency_s": round(sum(r["latency_s"] for r in ok) / len(ok), 2) if ok else None,
        "mean_input_tokens": round(sum(r["input_tokens"] for r in ok) / len(ok), 1) if ok else None,
        "total_cost_usd": round(total_cost, 4),
        "mean_cost_per_q_usd": round(total_cost / len(ok), 6) if ok else None,
    }
    print(f"\nSUMMARY: turns={summary['mean_turns']} searches={summary['mean_searches']} "
          f"cost/q=${summary['mean_cost_per_q_usd']} total=${summary['total_cost_usd']} "
          f"(live searches: {live})")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_model = args.model.replace("/", "-")
    path = os.path.join(RESULTS_DIR,
        f"deepsearchqa_{args.backend}_{safe_model}"
        + (f"_{args.effort}" if args.effort else "") + f"_{ts}.json")
    with open(path, "w") as f:
        json.dump({"dataset": "google/deepsearchqa (eval split)",
                   "indices": indices, "backend": args.backend, "model": args.model,
                   "base_url": base_url, "reasoning_effort": args.effort,
                   "budgets": {"searches": MAX_SEARCHES, "fetches": MAX_FETCHES,
                               "turns": MAX_TURNS},
                   "system_prompt": SYSTEM_PROMPT, "timestamp": ts,
                   "summary": summary, "results": results}, f, indent=2)
    print(f"Saved {os.path.basename(path)}")


if __name__ == "__main__":
    main()
