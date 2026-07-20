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

DEFAULTS = {"mmlu_pro": 140, "math500": 100, "gsm8k": 100}
MAX_TOKENS = {"mmlu_pro": 1024, "math500": 2048, "gsm8k": 1024}

LETTERS = "ABCDEFGHIJ"


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


LOADERS = {"mmlu_pro": load_mmlu_pro, "math500": load_math500, "gsm8k": load_gsm8k}


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


def score(task, gold, text):
    if text is None:
        return None, False
    if task == "mmlu_pro":
        pred = extract_letter(text)
        return pred, pred == gold
    if task == "math500":
        pred = extract_boxed(text)
        return pred, math_equal(pred, gold)
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
        pred, correct = score(task, item["gold"], r["text"])
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
    summary = {
        "n": len(items),
        "n_errors": len(items) - len(ok),
        "n_correct": n_correct,
        "accuracy": round(n_correct / len(ok), 4) if ok else None,
        "mean_latency_ms": round(sum(r["latency_ms"] for r in ok) / len(ok), 1) if ok else None,
        "mean_output_tokens": round(sum(r["output_tokens"] for r in ok) / len(ok), 1) if ok else None,
        "mean_reasoning_tokens": round(sum(r["reasoning_tokens"] for r in ok) / len(ok), 1) if ok else None,
    }
    print(f"  DONE {task}: {n_correct}/{len(ok)} correct ({summary['accuracy']}), "
          f"{summary['n_errors']} errors, mean latency {summary['mean_latency_ms']} ms")

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
