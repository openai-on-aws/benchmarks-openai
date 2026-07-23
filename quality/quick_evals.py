"""
Quick cross-backend evals: small, reproducible samples of community-standard
benchmarks, run identically against Bedrock ("mantle") and OpenAI SaaS.

Tasks (all ungated on Hugging Face):
  mmlu_pro  TIGER-Lab/MMLU-Pro     stratified sample, 10-option MCQ, exact letter match
  math500   HuggingFaceH4/MATH-500 boxed-answer match (normalized string)
  gsm8k     openai/gsm8k           final-number match

Sampling uses a fixed seed so every model sees the same questions.

Usage:
  python quality/quick_evals.py --backend mantle --model openai.gpt-5.6-luna --effort none
  python quality/quick_evals.py --backend saas --model gpt-5.4-mini
"""

import argparse
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from datasets import load_dataset
from openai import OpenAI

from eval_utils import capture_error

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
SEED = 42

DEFAULTS = {"mmlu_pro": 140, "math500": 100, "gsm8k": 100, "aime": 60, "humaneval": 164}
MAX_TOKENS = {"mmlu_pro": 1024, "math500": 2048, "gsm8k": 1024, "aime": 8192, "humaneval": 2048}

LETTERS = "ABCDEFGHIJ"

# List prices, USD per 1M tokens (input, output). Sources, retrieved 2026-07-21:
#   Bedrock: https://aws.amazon.com/bedrock/pricing/ (in-region US East)
#   OpenAI:  https://developers.openai.com/api/docs/pricing (Standard tier)
# Cached-input discounts are NOT applied (benchmark prompts are unique per question).
PRICES = {
    ("mantle", "openai.gpt-5.6-luna"):  (1.10, 6.60),
    ("mantle", "openai.gpt-5.6-terra"): (2.75, 16.50),
    ("mantle", "openai.gpt-5.6-sol"):   (5.50, 33.00),
    ("mantle", "openai.gpt-5.4"):       (2.75, 16.50),
    ("saas", "gpt-5.6-luna"):  (1.00, 6.00),
    ("saas", "gpt-5.6-terra"): (2.50, 15.00),
    ("saas", "gpt-5.6-sol"):   (5.00, 30.00),
    ("saas", "gpt-5.4-mini"):  (0.75, 4.50),
    ("saas", "gpt-5.4-nano"):  (0.20, 1.25),
}


def call_cost_usd(backend, model, input_tokens, output_tokens):
    price = PRICES.get((backend, model))
    if not price:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1e6


def make_client(backend):
    if backend == "mantle":
        token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        if not token:
            from aws_bedrock_token_generator import provide_token
            token = provide_token(region=os.environ.get("AWS_REGION", "us-west-2"))
        base = os.environ.get(
            "MANTLE_BASE_URL",
            f"https://bedrock-mantle.{os.environ.get('AWS_REGION', 'us-west-2')}.api.aws/openai/v1")
        return OpenAI(api_key=token, base_url=base), base
    key = os.environ.get("OPENAI_API_KEY_SAAS") or os.environ["OPENAI_API_KEY"]
    return OpenAI(api_key=key), "https://api.openai.com/v1"


# ------------------------------------------------------------------ task prep

def load_mmlu_pro(n):
    ds = load_dataset("TIGER-Lab/MMLU-Pro", split="test")
    by_cat = {}
    for row in ds:
        by_cat.setdefault(row["category"], []).append(row)
    rng = random.Random(SEED)
    per_cat = max(1, n // len(by_cat))
    items = []
    for cat in sorted(by_cat):
        items.extend(rng.sample(by_cat[cat], min(per_cat, len(by_cat[cat]))))
    for row in items:
        opts = "\n".join(f"{LETTERS[i]}. {o}" for i, o in enumerate(row["options"]))
        yield {
            "id": row["question_id"],
            "prompt": (f"{row['question']}\n\n{opts}\n\n"
                       "Think briefly if needed, then give your final answer as "
                       "'Final answer: <letter>'."),
            "gold": row["answer"],
            "category": row["category"],
        }


def load_math500(n):
    ds = load_dataset("HuggingFaceH4/MATH-500", split="test")
    rng = random.Random(SEED)
    for row in rng.sample(list(ds), min(n, len(ds))):
        yield {
            "id": row["unique_id"],
            "prompt": (f"{row['problem']}\n\n"
                       "Solve the problem. Put your final answer in \\boxed{}."),
            "gold": row["answer"],
            "category": row["subject"],
        }


def load_gsm8k(n):
    ds = load_dataset("openai/gsm8k", "main", split="test")
    rng = random.Random(SEED)
    for i, row in enumerate(rng.sample(list(ds), min(n, len(ds)))):
        yield {
            "id": f"gsm8k_{i}",
            "prompt": (f"{row['question']}\n\n"
                       "Solve step by step, then give your final answer as "
                       "'Final answer: <number>'."),
            "gold": row["answer"].split("####")[-1].strip().replace(",", ""),
            "category": "gsm8k",
        }


def load_aime(n):
    """Most recent AIME problems (hardest discrimination band). Answers are ints 0-999."""
    ds = load_dataset("qq8933/AIME_1983_2024", split="train")
    rows = sorted(ds, key=lambda r: (r["Year"], r["Problem Number"]), reverse=True)[:n]
    for row in rows:
        yield {
            "id": f"aime_{row['Year']}_{row['Part'] or 'I'}_{row['Problem Number']}",
            "prompt": (f"{row['Question']}\n\n"
                       "Solve this competition problem. Show your work, then on the last "
                       "line write 'Final answer: <integer>' (an integer from 0 to 999)."),
            "gold": str(int(row["Answer"])),
            "category": f"aime_{row['Year']}",
        }


def load_humaneval(n):
    ds = load_dataset("openai/openai_humaneval", split="test")
    for row in list(ds)[:n]:
        yield {
            "id": row["task_id"],
            "prompt": ("Complete the following Python function. Return ONLY the complete "
                       "function definition (including the signature) in a ```python code "
                       "block, with no tests or example usage.\n\n```python\n"
                       f"{row['prompt']}```"),
            "gold": "",  # correctness comes from executing the checks, not string match
            "category": "humaneval",
            "_test": row["test"],
            "_entry_point": row["entry_point"],
            # Everything before the function def (imports, helpers) — the official
            # harness always executes this preamble with the completion.
            "_header": row["prompt"][:row["prompt"].index(f"def {row['entry_point']}")],
        }


LOADERS = {"mmlu_pro": load_mmlu_pro, "math500": load_math500, "gsm8k": load_gsm8k,
           "aime": load_aime, "humaneval": load_humaneval}


# ------------------------------------------------------------------- scoring

def extract_letter(text):
    m = re.findall(r"[Ff]inal answer:?\s*\**\(?([A-J])\)?", text)
    if m:
        return m[-1]
    m = re.findall(r"\b([A-J])\b", text)
    return m[-1] if m else None


def norm_math(s):
    if s is None:
        return None
    s = s.strip().strip("$")
    s = re.sub(r"\\left|\\right|\\!|\\,|\\;|~", "", s)
    s = re.sub(r"\\text\s*\{[^{}]*\}", "", s)   # units like \text{ cm} anywhere
    s = re.sub(r"\\mbox\s*\{[^{}]*\}", "", s)
    s = re.sub(r"\\d?frac", r"\\frac", s)
    # \frac14, \frac 59, \frac{1}4 → \frac{1}{4}
    s = re.sub(r"\\frac\s*(\d|\{[^{}]+\})\s*(\d|\{[^{}]+\})",
               lambda m: "\\frac{%s}{%s}" % (m.group(1).strip("{}"), m.group(2).strip("{}")), s)
    s = re.sub(r"\\sqrt\s*(\d)", r"\\sqrt{\1}", s)  # \sqrt3 → \sqrt{3}
    s = s.replace("\\ ", "").replace(" ", "")
    # strip a simple lhs like x=, k=, f(2,3)=  (but keep multi-part answers intact)
    m = re.match(r"^[a-zA-Z]\w*(\([^()]*\))?=([^=]+)$", s)
    if m:
        s = m.group(2)
    s = re.sub(r"(?<![\d.])\.(\d)", r"0.\1", s)  # .35 → 0.35
    s = s.rstrip(".").rstrip("\\")
    return s


def math_equal(pred, gold):
    a, b = norm_math(pred), norm_math(gold)
    if a is None or b is None:
        return False
    if a == b:
        return True
    # multi-part answers ("3,5,7"): compare as sets of parts
    if "," in a or "," in b:
        pa = sorted(p for p in a.split(",") if p)
        pb = sorted(p for p in b.split(",") if p)
        return pa == pb
    # numeric equivalence fallback (\frac{1}{2} vs 0.5 etc.)
    va, vb = _latex_value(a), _latex_value(b)
    return va is not None and vb is not None and abs(va - vb) < 1e-9


def _latex_value(s):
    """Numeric value of simple latex (plain numbers and \\frac{a}{b}); None if not simple."""
    try:
        return float(s)
    except ValueError:
        pass
    m = re.fullmatch(r"\\frac\{(-?[\d.]+)\}\{(-?[\d.]+)\}", s)
    if m:
        try:
            return float(m.group(1)) / float(m.group(2))
        except (ValueError, ZeroDivisionError):
            return None
    return None


def extract_boxed(text):
    starts = [m.end() for m in re.finditer(r"\\boxed\{", text)]
    if not starts:
        return None
    start = starts[-1]
    depth, i = 1, start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[start:i - 1] if depth == 0 else None


def extract_number(text):
    m = re.findall(r"[Ff]inal answer:?\s*\**\$?(-?[\d,]+(?:\.\d+)?)", text)
    if not m:
        m = re.findall(r"(-?[\d,]+(?:\.\d+)?)", text)
    if not m:
        return None
    return m[-1].replace(",", "").rstrip(".")


def extract_code(text):
    m = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    return m[-1] if m else text


def run_humaneval_check(code, test, entry_point, header="", timeout=15):
    """Execute candidate code against the official HumanEval checks in a subprocess."""
    import subprocess, sys, tempfile
    program = f"{header}\n{code}\n\n{test}\n\ncheck({entry_point})\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(program)
        path = f.name
    try:
        r = subprocess.run([sys.executable, path], capture_output=True, timeout=timeout)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    finally:
        os.unlink(path)


def score(task, gold, text, item=None):
    if text is None:
        return None, False
    if task == "mmlu_pro":
        pred = extract_letter(text)
        return pred, pred == gold
    if task == "math500":
        pred = extract_boxed(text)
        return pred, math_equal(pred, gold)
    if task == "aime":
        pred = extract_number(text)
        if pred is None:
            b = extract_boxed(text)
            pred = norm_math(b) if b else None
        try:
            return pred, pred is not None and int(float(pred)) == int(gold)
        except (ValueError, TypeError):
            return pred, False
    if task == "humaneval":
        code = extract_code(text)
        ok = run_humaneval_check(code, item["_test"], item["_entry_point"],
                                 header=item.get("_header", ""))
        return (code[:80] + "..."), ok
    pred = extract_number(text)
    try:
        return pred, pred is not None and float(pred) == float(gold)
    except (ValueError, TypeError):
        return pred, False


# -------------------------------------------------------------------- runner

def call_one(client, model, effort, item, max_tokens, max_retries=4):
    kwargs = {}
    if effort:
        kwargs["reasoning"] = {"effort": effort}
    for attempt in range(max_retries):
        try:
            t0 = time.perf_counter()
            r = client.responses.create(
                model=model,
                input=[{"role": "user", "content": item["prompt"]}],
                max_output_tokens=max_tokens,
                **kwargs,
            )
            latency = round((time.perf_counter() - t0) * 1000, 1)
            u = r.usage
            return {
                "text": r.output_text,
                "latency_ms": latency,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "reasoning_tokens": getattr(u.output_tokens_details, "reasoning_tokens", 0),
                "status": r.status,
                "error": None,
            }
        except Exception as e:
            err = str(e).lower()
            if attempt < max_retries - 1 and any(x in err for x in
                    ["rate_limit", "429", "500", "502", "503", "overloaded",
                     "timeout", "connection"]):
                time.sleep(2 ** attempt * 3)
                continue
            return {"text": None, "latency_ms": None, "input_tokens": 0,
                    "output_tokens": 0, "reasoning_tokens": 0, "status": "error",
                    "error": capture_error(e)}
    return {"text": None, "latency_ms": None, "input_tokens": 0, "output_tokens": 0,
            "reasoning_tokens": 0, "status": "error", "error": {"error_message": "max retries"}}


def run_task(backend, model, effort, task, n, concurrency):
    client, base_url = make_client(backend)
    items = list(LOADERS[task](n))
    max_tokens = MAX_TOKENS[task]
    print(f"\n=== {task}: {len(items)} questions | {backend}/{model}"
          + (f" | effort={effort}" if effort else "") + f" | conc={concurrency} ===")

    results = [None] * len(items)
    done = [0]

    def work(idx):
        item = items[idx]
        r = call_one(client, model, effort, item, max_tokens)
        pred, correct = score(task, item["gold"], r["text"], item)
        done[0] += 1
        if done[0] % 20 == 0 or done[0] == len(items):
            n_ok = sum(1 for x in results if x and x["correct"])
            print(f"  [{done[0]:>3}/{len(items)}] running accuracy ~{n_ok}/{done[0]}")
        results[idx] = {
            "id": item["id"], "category": item["category"], "gold": item["gold"],
            "pred": pred, "correct": bool(correct), **r,
        }

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(work, range(len(items))))

    ok = [r for r in results if not r["error"]]
    n_correct = sum(1 for r in ok if r["correct"])
    costs = [call_cost_usd(backend, model, r["input_tokens"], r["output_tokens"]) for r in ok]
    total_cost = round(sum(c for c in costs if c is not None), 6) if any(c is not None for c in costs) else None
    # Cost per successful answer: total spend across ALL attempts / number of successes.
    # This is the retry-aware number — failures aren't free.
    cost_per_success = round(total_cost / n_correct, 6) if total_cost is not None and n_correct else None
    summary = {
        "n": len(items),
        "n_errors": len(items) - len(ok),
        "n_correct": n_correct,
        "accuracy": round(n_correct / len(ok), 4) if ok else None,
        "mean_latency_ms": round(sum(r["latency_ms"] for r in ok) / len(ok), 1) if ok else None,
        "mean_output_tokens": round(sum(r["output_tokens"] for r in ok) / len(ok), 1) if ok else None,
        "mean_reasoning_tokens": round(sum(r["reasoning_tokens"] for r in ok) / len(ok), 1) if ok else None,
        "total_cost_usd": total_cost,
        "mean_cost_per_call_usd": round(total_cost / len(ok), 6) if total_cost is not None and ok else None,
        "cost_per_success_usd": cost_per_success,
    }
    print(f"  DONE {task}: {n_correct}/{len(ok)} correct ({summary['accuracy']}), "
          f"{summary['n_errors']} errors, mean latency {summary['mean_latency_ms']} ms, "
          f"cost/success ${cost_per_success}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_model = model.replace("/", "-")
    path = os.path.join(RESULTS_DIR,
        f"quickeval_{task}_{backend}_{safe_model}" + (f"_{effort}" if effort else "") + f"_{ts}.json")
    with open(path, "w") as f:
        json.dump({
            "task": task, "backend": backend, "model": model, "base_url": base_url,
            "reasoning_effort": effort, "seed": SEED, "max_output_tokens": max_tokens,
            "timestamp": ts, "summary": summary, "results": results,
        }, f, indent=2)
    print(f"  Saved {os.path.basename(path)}")
    return summary


def rescore_all():
    """Re-grade every quickeval result file in place with the current scorers."""
    import glob
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "quickeval_*.json"))):
        with open(path) as f:
            d = json.load(f)
        if d["task"] == "humaneval":
            continue  # grading requires executing the checks; can't re-grade from text
        changed = 0
        for r in d["results"]:
            if r["text"] is None:
                continue
            pred, correct = score(d["task"], r["gold"], r["text"])
            if bool(correct) != r["correct"] or pred != r["pred"]:
                changed += 1
            r["pred"], r["correct"] = pred, bool(correct)
        ok = [r for r in d["results"] if not r["error"]]
        n_correct = sum(1 for r in ok if r["correct"])
        old = d["summary"]["accuracy"]
        d["summary"]["n_correct"] = n_correct
        d["summary"]["accuracy"] = round(n_correct / len(ok), 4) if ok else None
        costs = [call_cost_usd(d["backend"], d["model"], r["input_tokens"], r["output_tokens"]) for r in ok]
        if any(c is not None for c in costs):
            total = round(sum(c for c in costs if c is not None), 6)
            d["summary"]["total_cost_usd"] = total
            d["summary"]["mean_cost_per_call_usd"] = round(total / len(ok), 6) if ok else None
            d["summary"]["cost_per_success_usd"] = round(total / n_correct, 6) if n_correct else None
        with open(path, "w") as f:
            json.dump(d, f, indent=2)
        flag = f"  ({changed} grades changed, was {old})" if changed else ""
        print(f"{d['model']:>28} {d['task']:>9}: {n_correct}/{len(ok)} = {d['summary']['accuracy']}{flag}")


def main():
    p = argparse.ArgumentParser(description="Quick cross-backend quality evals")
    p.add_argument("--rescore", action="store_true",
                   help="re-grade existing result files with current scorers; no API calls")
    args_pre, _ = p.parse_known_args()
    if args_pre.rescore:
        rescore_all()
        return
    p.add_argument("--backend", choices=["mantle", "saas"], required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", help="reasoning effort (e.g. none, low); omit for model default")
    p.add_argument("--tasks", default="mmlu_pro,math500,gsm8k")
    p.add_argument("--n", type=int, help="override sample size for every task")
    p.add_argument("--concurrency", type=int, default=6)
    args = p.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    for task in args.tasks.split(","):
        task = task.strip()
        if task not in LOADERS:
            raise SystemExit(f"unknown task {task}; choose from {list(LOADERS)}")
        run_task(args.backend, args.model, args.effort, task,
                 args.n or DEFAULTS[task], args.concurrency)


if __name__ == "__main__":
    main()
