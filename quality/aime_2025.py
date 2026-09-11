"""
AIME 2025 eval on openai.gpt-5.4 (Bedrock Mantle).
Methodology follows Artificial Analysis:
  - 1 repeat (AA uses 10; we use 1 for speed)
  - temperature=0.6 (reasoning model)
  - Zero-shot instruction prompted
  - Numerical answer extraction (integers 0-999)
  - Published GPT-5.4 score: not listed (AIME 2025 is standalone)
"""

import os
import sys
import re
import json
import time
import argparse
from datetime import datetime, timezone

from quick_evals import make_client as shared_client
from eval_utils import (capture_error, supports_temperature, legacy_model,
                        resolve_effort, response_options)
from datasets import load_dataset

N_REPEATS   = 5
TEMPERATURE = 0.6
RESULTS_DIR = os.environ.get("BENCHMARK_RESULTS_DIR", os.path.join(os.path.dirname(__file__), "results"))

SYSTEM_PROMPT = (
    "You are an expert mathematician. Solve the following competition math problem. "
    "Show your work carefully, then on the very last line write your final answer as: "
    "Answer: <integer> (a single integer between 0 and 999)."
)


def make_client(backend, model=None):
    client, _ = shared_client(backend)
    return client, legacy_model(backend, model)



def extract_answer(text):
    """Extract integer answer. Takes last match."""
    # Primary: "Answer: 123"
    matches = re.findall(r"[Aa]nswer:\s*(\d{1,3})", text)
    if matches:
        return int(matches[-1])

    # Fallback: \boxed{123}
    matches = re.findall(r"\\boxed\{(\d{1,3})\}", text)
    if matches:
        return int(matches[-1])

    # Fallback: last standalone integer 0-999 at end of response
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    for line in reversed(lines):
        m = re.match(r"^(\d{1,3})$", line)
        if m:
            return int(m.group(1))

    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["mantle", "saas", "runtime"], default="mantle")
    parser.add_argument("--model", help="model ID (overrides MANTLE_MODEL / SAAS_MODEL / RUNTIME_MODEL)")
    parser.add_argument("--effort", help="reasoning effort; Astra defaults to low")
    parser.add_argument("--max-questions", type=int)
    parser.add_argument("--repeats", type=int, default=N_REPEATS)
    args = parser.parse_args()
    args.model = legacy_model(args.backend, args.model)
    try:
        args.effort = resolve_effort(args.model, args.effort)
    except ValueError as e:
        parser.error(str(e))
    if args.max_questions is not None and args.max_questions < 1:
        parser.error("--max-questions must be positive")
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # AIME 2024 — closest available public dataset (2025 not yet on HuggingFace)
    dataset = load_dataset("qq8933/AIME_1983_2024", split="train")
    questions = [q for q in dataset if int(q.get("Year", 0)) == 2024]
    questions = questions[:args.max_questions]
    print(f"Note: Using AIME 2024 ({len(questions)} problems) — 2025 dataset not yet public on HuggingFace")

    client, model = make_client(args.backend, args.model)
    print(f"\nAIME 2024 Eval — {model} ({args.backend})")
    print(f"Questions: {len(questions)}  |  Repeats: {args.repeats}")
    print(f"Temperature: {TEMPERATURE if supports_temperature(model) else 'omitted'}")

    started_at = datetime.now(timezone.utc)
    print(f"Started: {started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 65)
    all_results = []
    correct = 0
    total = 0

    for qi, q in enumerate(questions):
        problem = q.get("Question", q.get("problem", ""))
        answer = q.get("Answer", q.get("answer", ""))
        # Normalize answer to integer
        try:
            correct_answer = int(str(answer).strip().replace(",", ""))
        except:
            correct_answer = None

        for ri in range(args.repeats):
            total += 1
            try:
                r = client.responses.create(
                    model=model,
                    instructions=SYSTEM_PROMPT,
                    input=[{"role": "user", "content": problem}],
                    max_output_tokens=4096,
                    **response_options(model, args.effort, TEMPERATURE),
                )
                response_text = r.output_text
                predicted = extract_answer(response_text)
                is_correct = (predicted == correct_answer) if (predicted is not None and correct_answer is not None) else False
                correct += is_correct

                status = "✅" if is_correct else ("⚠️" if predicted is None else "❌")
                print(f"  [{total:>3}] Q{qi+1:>2} R{ri+1} {status}  pred={predicted}  correct={correct_answer}")

                all_results.append({
                    "question_idx": qi,
                    "repeat": ri,
                    "problem_preview": problem[:100],
                    "correct_answer": correct_answer,
                    "predicted": predicted,
                    "is_correct": is_correct,
                    "status": r.status,
                    "error": None,
                })
            except Exception as e:
                err = capture_error(e)
                print(f"  [{total:>3}] Q{qi+1:>2} R{ri+1} ❌  ERROR: {err['error_message'][:100]} req_id={err['request_id']}", flush=True)
                all_results.append({
                    "question_idx": qi, "repeat": ri,
                    "problem_preview": problem[:100],
                    "correct_answer": correct_answer,
                    "predicted": None, "is_correct": False,
                    "error": err,
                })

            if total % 10 == 0 and args.backend in ("mantle", "runtime"):
                client, model = make_client(args.backend, args.model)

    ended_at = datetime.now(timezone.utc)
    accuracy = correct / total * 100 if total > 0 else 0

    print("\n" + "=" * 65)
    print(f"RESULTS — AIME 2024 | {model} ({args.backend})")
    print(f"Accuracy: {correct}/{total} = {accuracy:.1f}%")
    print(f"Ended:    {ended_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    duration = (ended_at - started_at).total_seconds()
    print(f"Duration: {duration/60:.1f} min")

    ts = started_at.strftime("%Y%m%d_%H%M%S")
    fname = os.path.join(RESULTS_DIR, f"aime_2024_{args.backend}_{model}_{ts}.json")
    payload = {
        "eval": "aime_2024",
        "model": model,
        "backend": args.backend,
        "n_questions": len(questions),
        "n_repeats": args.repeats,
        "temperature": TEMPERATURE if supports_temperature(model) else None,
        "reasoning_effort": args.effort,
        "max_output_tokens": 4096,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round(duration, 1),
        "correct": correct,
        "total": total,
        "accuracy_pct": round(accuracy, 2),
        "results": all_results,
    }
    with open(fname, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Saved:    {os.path.basename(fname)}")


if __name__ == "__main__":
    main()
