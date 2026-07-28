"""
GDPval slice: professional-work deliverables, rubric-judged.

Dataset: openai/gdpval (public 220-task gold subset; arXiv:2510.04374).
Tasks are real occupational deliverables (briefs, plans, analyses) written by
professionals with 14+ years of experience, each with a human-authored rubric.

Scope of this harness (and its honest limits):
  - Only tasks with NO reference files (95 of 220) — the text-only slice a
    Responses API call can attempt without a file-handling pipeline.
  - Stratified sample across sectors, fixed seed, same tasks for every model.
  - Grading: rubric-anchored LLM judge (gpt-5.5 — OpenAI family per team
    constraint, but NOT a candidate arm). The official GDPval grading is
    blind pairwise comparison by human domain experts; rubric-LLM scores are
    NOT comparable to the paper's win rates. Within-run comparisons only.
  - Judge output: per-rubric-item verdicts -> fraction of rubric points
    earned (weighted by item score). "Pass" = >= 0.7 of rubric points.

Usage:
  python quality/gdpval_eval.py --backend mantle --model openai.gpt-5.6-luna --effort none
  python quality/gdpval_eval.py --judge-only    # judge any unjudged result files
"""

import argparse
import glob
import json
import os
import random
import time
from datetime import datetime, timezone

from datasets import load_dataset

from quick_evals import make_client, call_cost_usd, capture_error

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
SEED = 42
N_TASKS = 24
MAX_OUTPUT_TOKENS = 8192
# Judge is gpt-5.5 (not a candidate arm; family caveat recorded in output).
# It can be served from either backend; all arms of one comparison must be
# judged by the same backend so scores stay apples-to-apples.
JUDGE_MODELS = {"saas": "gpt-5.5", "mantle": "openai.gpt-5.5"}
JUDGE_RETRIES = 3
PASS_FRACTION = 0.70

# Applied identically to every arm. Without it, models decline tasks that ask
# for file attachments (PDF/Excel) or live web access, which grades harness
# limitations instead of work quality. Disclosed in judge_caveat.
SYSTEM_PROMPT = (
    "Produce the complete deliverable inline as well-structured markdown text. "
    "You cannot attach files or browse the web: if the task asks for a document "
    "(PDF, spreadsheet, email, etc.), write its full content directly — for "
    "spreadsheets use markdown tables. If the task references external sources "
    "you cannot access, work from your knowledge and state your assumptions "
    "explicitly. Never decline the task; deliver the best professional work "
    "product the text medium allows.")

JUDGE_INSTRUCTIONS = """You are grading a professional work deliverable against a rubric.

TASK GIVEN TO THE WORKER:
{prompt}

RUBRIC (JSON list; each item has a "criterion" and a "score" weight):
{rubric}

DELIVERABLE TO GRADE:
{deliverable}

For EVERY rubric item, decide whether the deliverable satisfies the criterion.
Judge only what is present in the deliverable text. Be strict: partial or
implied coverage that a demanding client would reject counts as not satisfied.

Return ONLY a JSON object: {{"verdicts": [{{"id": "<rubric_item_id>",
"ok": true|false}}, ...]}} with one entry per rubric item, in rubric order.
No evidence strings, no commentary — just the JSON."""


def load_tasks(n=N_TASKS):
    ds = load_dataset("openai/gdpval", split="train")
    text_only = [r for r in ds if not r["reference_files"]]
    by_sector = {}
    for r in text_only:
        by_sector.setdefault(r["sector"], []).append(r)
    rng = random.Random(SEED)
    # proportional stratified sample: at least 1 per sector, fill by size
    sectors = sorted(by_sector, key=lambda s: -len(by_sector[s]))
    picks = []
    quota = {s: max(1, round(n * len(by_sector[s]) / len(text_only))) for s in sectors}
    for s in sectors:
        pool = sorted(by_sector[s], key=lambda r: r["task_id"])
        picks.extend(rng.sample(pool, min(quota[s], len(pool))))
    picks = picks[:n]
    return [{"task_id": r["task_id"], "sector": r["sector"], "occupation": r["occupation"],
             "prompt": r["prompt"], "rubric": json.loads(r["rubric_json"])
                 if isinstance(r["rubric_json"], str) else r["rubric_json"]}
            for r in picks]


def generate(backend, model, effort, tasks):
    client, base_url = make_client(backend)
    kwargs = {"reasoning": {"effort": effort}} if effort else {}
    results = []
    for i, t in enumerate(tasks):
        t0 = time.perf_counter()
        try:
            r = client.responses.create(
                model=model,
                instructions=SYSTEM_PROMPT,
                input=[{"role": "user", "content": t["prompt"]}],
                max_output_tokens=MAX_OUTPUT_TOKENS, **kwargs)
            u = r.usage
            cost = call_cost_usd(backend, model, u.input_tokens, u.output_tokens)
            results.append({
                "task_id": t["task_id"], "sector": t["sector"], "occupation": t["occupation"],
                "deliverable": r.output_text, "status": r.status,
                "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                "reasoning_tokens": getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0,
                "latency_s": round(time.perf_counter() - t0, 2),
                "cost_usd": round(cost, 6) if cost else None, "error": None,
            })
            print(f"  [{i+1:>2}/{len(tasks)}] {t['occupation'][:40]:40} "
                  f"out_tok={u.output_tokens:>5} {results[-1]['latency_s']:>6.1f}s")
        except Exception as e:
            results.append({"task_id": t["task_id"], "sector": t["sector"],
                            "occupation": t["occupation"], "deliverable": None,
                            "status": "error", "input_tokens": 0, "output_tokens": 0,
                            "reasoning_tokens": 0, "latency_s": None, "cost_usd": None,
                            "error": capture_error(e)})
            print(f"  [{i+1:>2}/{len(tasks)}] ERROR {str(e)[:80]}")
    return results, base_url


def _judge_call(client, judge_model, prompt):
    for attempt in range(JUDGE_RETRIES):
        try:
            jr = client.responses.create(model=judge_model,
                input=[{"role": "user", "content": prompt}], max_output_tokens=16384)
            text = jr.output_text
            return json.loads(text[text.index("{"):text.rindex("}") + 1])["verdicts"]
        except Exception:
            if attempt == JUDGE_RETRIES - 1:
                raise
            time.sleep(5 * (attempt + 1))


def judge_file(path, tasks_by_id, backend="saas"):
    # Resume from an existing _judged.json: keep good judgments, redo errors.
    out = path.replace(".json", "_judged.json")
    with open(out if os.path.exists(out) else path) as f:
        d = json.load(f)

    def is_graded(r):
        return isinstance(r.get("judgment"), dict) and "rubric_fraction" in r["judgment"]

    todo = [r for r in d["results"]
            if not r["error"] and r["deliverable"] and not is_graded(r)]
    if not todo and d.get("judge_summary"):
        return None
    client, _ = make_client(backend)
    judge_model = JUDGE_MODELS[backend]
    print(f"Judging {os.path.basename(path)} "
          f"({len(todo)} to grade, judge {backend}/{judge_model})")
    for i, r in enumerate(d["results"]):
        if r["error"] or not r["deliverable"]:
            r["judgment"] = None
            continue
        if is_graded(r):
            continue
        t = tasks_by_id[r["task_id"]]
        rubric_slim = [{"rubric_item_id": it["rubric_item_id"], "score": it["score"],
                        "criterion": it["criterion"]} for it in t["rubric"]]
        prompt = JUDGE_INSTRUCTIONS.format(
            prompt=t["prompt"], rubric=json.dumps(rubric_slim, indent=1),
            deliverable=r["deliverable"])
        try:
            verdicts = _judge_call(client, judge_model, prompt)
            by_id = {v["id"]: bool(v.get("ok")) for v in verdicts}
            total_w = sum(it["score"] for it in t["rubric"])
            earned = sum(it["score"] for it in t["rubric"] if by_id.get(it["rubric_item_id"]))
            frac = earned / total_w if total_w else 0.0
            r["judgment"] = {"rubric_fraction": round(frac, 4),
                             "items_satisfied": sum(by_id.values()),
                             "items_total": len(t["rubric"]), "verdicts": verdicts,
                             "judge_backend": backend, "judge_model": judge_model}
            print(f"  [{i+1:>2}] rubric_fraction={frac:.2f} "
                  f"({sum(by_id.values())}/{len(t['rubric'])} items)")
        except Exception as e:
            r["judgment"] = {"error": str(e)[:300]}
            print(f"  [{i+1:>2}] JUDGE ERROR {str(e)[:80]}")
    graded = [r for r in d["results"] if is_graded(r)]
    scores = [r["judgment"]["rubric_fraction"] for r in graded]
    passed = sum(1 for r in graded if r["judgment"]["rubric_fraction"] >= PASS_FRACTION)
    total_cost = sum(r["cost_usd"] or 0 for r in d["results"])
    judge_backends = sorted({r["judgment"].get("judge_backend", "saas") for r in graded})
    d["judge_summary"] = {
        "judge_model": "gpt-5.5",
        "judge_backends": judge_backends,
        "judge_caveat": ("Rubric-anchored LLM judge, same family (OpenAI) as all candidates "
                         "but not a candidate arm. Official GDPval uses blind human expert "
                         "pairwise grading; these scores are NOT comparable to paper win "
                         "rates. Within-run comparison only."),
        "pass_fraction_threshold": PASS_FRACTION,
        "n_graded": len(graded),
        "mean_rubric_fraction": round(sum(scores) / len(scores), 4) if scores else None,
        "pass_rate": round(passed / len(graded), 4) if graded else None,
        "cost_per_pass_usd": round(total_cost / passed, 6) if passed else None,
    }
    with open(out, "w") as f:
        json.dump(d, f, indent=2)
    js = d["judge_summary"]
    print(f"  DONE: mean rubric fraction {js['mean_rubric_fraction']} | "
          f"pass {passed}/{len(graded)} | cost/pass ${js['cost_per_pass_usd']}")
    return out


def main():
    p = argparse.ArgumentParser(description="GDPval text-only slice, rubric-judged")
    p.add_argument("--backend", choices=["mantle", "saas"])
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--n", type=int, default=N_TASKS)
    p.add_argument("--judge-only", action="store_true")
    p.add_argument("--judge-backend", choices=["saas", "mantle"], default="saas",
                   help="where gpt-5.5 judge calls go; use one backend per comparison")
    p.add_argument("--file", help="judge only this result file (with --judge-only)")
    args = p.parse_args()

    tasks = load_tasks(args.n)
    tasks_by_id = {t["task_id"]: t for t in tasks}

    if args.judge_only:
        paths = [args.file] if args.file else sorted(
            glob.glob(os.path.join(RESULTS_DIR, "gdpval_*.json")))
        for path in paths:
            if not path.endswith("_judged.json"):
                judge_file(path, tasks_by_id, backend=args.judge_backend)
        return

    if not args.backend or not args.model:
        p.error("--backend and --model are required unless --judge-only")

    print(f"GDPval slice: {len(tasks)} text-only tasks | {args.backend}/{args.model}"
          + (f" | effort={args.effort}" if args.effort else ""))
    results, base_url = generate(args.backend, args.model, args.effort, tasks)

    ok = [r for r in results if not r["error"]]
    total_cost = sum(r["cost_usd"] or 0 for r in ok)
    summary = {
        "n": len(results), "n_errors": len(results) - len(ok),
        "mean_output_tokens": round(sum(r["output_tokens"] for r in ok) / len(ok), 1) if ok else None,
        "mean_latency_s": round(sum(r["latency_s"] for r in ok) / len(ok), 2) if ok else None,
        "total_cost_usd": round(total_cost, 6),
        "mean_cost_per_task_usd": round(total_cost / len(ok), 6) if ok else None,
    }
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_model = args.model.replace("/", "-")
    path = os.path.join(RESULTS_DIR,
        f"gdpval_{args.backend}_{safe_model}" + (f"_{args.effort}" if args.effort else "") + f"_{ts}.json")
    with open(path, "w") as f:
        json.dump({"dataset": "openai/gdpval (text-only slice)", "seed": SEED,
                   "backend": args.backend, "model": args.model, "base_url": base_url,
                   "reasoning_effort": args.effort, "timestamp": ts,
                   "task_ids": [t["task_id"] for t in tasks],
                   "summary": summary, "results": results}, f, indent=2)
    print(f"Saved {os.path.basename(path)}  (judge with --judge-only)")


if __name__ == "__main__":
    main()
