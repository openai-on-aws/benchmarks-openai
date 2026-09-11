"""
HLE (Humanity's Last Exam) — text-only subset
openai.gpt-5.4 on Bedrock Mantle vs OAI SaaS.
- 2,158 text-only questions (image questions excluded)
- 1 repeat (HLE is designed for pass@1)
- temperature=0.6
- Published GPT-5.4 score: 39.8% (no tools)
- Scoring: exact string match on extracted answer, case-insensitive
"""

import os, sys, re, json, random, argparse
from datetime import datetime, timezone

from quick_evals import make_client as shared_client
from eval_utils import (capture_error, supports_temperature, legacy_model,
                        resolve_effort, response_options)
from datasets import load_dataset

RESULTS_DIR = os.environ.get("BENCHMARK_RESULTS_DIR", os.path.join(os.path.dirname(__file__), "results"))
TEMPERATURE = 0.6

SYSTEM_PROMPT = (
    "You are an expert. Answer the following question as concisely and accurately as possible. "
    "On the very last line of your response, write your final answer in the format: Answer: <answer>"
)


def make_client(backend, model=None):
    client, _ = shared_client(backend)
    return client, legacy_model(backend, model)



def extract_answer(text):
    matches = re.findall(r"[Aa]nswer:\s*(.+)", text)
    if matches:
        return matches[-1].strip()
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    return lines[-1] if lines else None


def score(predicted, correct):
    if predicted is None:
        return False
    return predicted.strip().lower() == correct.strip().lower()


def run_single(client, model, question_text, max_retries=5, effort=None):
    for attempt in range(max_retries):
        try:
            r = client.responses.create(
                model=model,
                instructions=SYSTEM_PROMPT,
                input=[{"role": "user", "content": question_text}],
                max_output_tokens=1024,
                **response_options(model, effort, TEMPERATURE),
            )
            return r.output_text, None, r.status
        except Exception as e:
            err = capture_error(e)
            if err["status_code"] == 429:
                import time
                wait = 2 ** attempt * 5
                print(f" [rate limit, wait {wait}s]", end="", flush=True)
                time.sleep(wait)
            else:
                return None, err, "error"
    return None, {"error_message": "max retries exceeded"}, "error"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["mantle", "saas", "runtime"], required=True)
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--start-from", type=int, default=0)
    parser.add_argument("--model", help="model ID (overrides MANTLE_MODEL / SAAS_MODEL / RUNTIME_MODEL)")
    parser.add_argument("--effort", help="reasoning effort; Astra defaults to low")
    args = parser.parse_args()
    args.model = legacy_model(args.backend, args.model)
    try:
        args.effort = resolve_effort(args.model, args.effort)
    except ValueError as e:
        parser.error(str(e))
    if args.max_questions is not None and args.max_questions < 1:
        parser.error("--max-questions must be positive")
    os.makedirs(RESULTS_DIR, exist_ok=True)

    ds = load_dataset("cais/hle", split="test")
    questions = [q for q in ds if not q.get("image")]
    if args.max_questions:
        random.seed(42)
        questions = random.sample(questions, args.max_questions)
    if args.start_from:
        questions = questions[args.start_from:]
        print(f"Resuming from question {args.start_from+1}, {len(questions)} remaining.")

    client, model = make_client(args.backend, args.model)
    started_at = datetime.now(timezone.utc)

    print(f"\nHLE Eval — {model} ({args.backend})")
    print(f"Questions: {len(questions)} (text-only)  |  Repeats: 1")
    print(f"Temperature: {TEMPERATURE if supports_temperature(model) else 'omitted'}  |  Published GPT-5.4 (no tools): 39.8%")
    print(f"Started: {started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 65)

    results = []
    correct_count = 0

    for qi, q in enumerate(questions):
        response_text, error, response_status = run_single(client, model, q["question"], effort=args.effort)
        predicted = extract_answer(response_text) if response_text else None
        is_correct = score(predicted, q["answer"]) if predicted else False
        if is_correct:
            correct_count += 1

        status = "✅" if is_correct else ("⚠️" if predicted is None else "❌")
        req_id = error.get("request_id", "") if isinstance(error, dict) else ""
        print(f"  [{qi+1:>4}/{len(questions)}] {status}  pred={str(predicted)[:40]:40s}  correct={q['answer'][:30]}"
              + (f"  req_id={req_id}" if req_id else ""), flush=True)

        results.append({
            "question_idx": qi,
            "question_id": q.get("id", ""),
            "category": q.get("category", ""),
            "correct_answer": q["answer"],
            "predicted": predicted,
            "is_correct": is_correct,
            "status": response_status,
            "error": error,
        })

        # Refresh mantle token every 100 calls
        if args.backend in ("mantle", "runtime") and (qi + 1) % 100 == 0:
            client, model = make_client(args.backend, args.model)

    ended_at = datetime.now(timezone.utc)
    accuracy = correct_count / len(questions) * 100
    duration = (ended_at - started_at).total_seconds()

    print("\n" + "=" * 65)
    print(f"RESULTS — HLE (text-only) | {model} ({args.backend})")
    print(f"Accuracy: {correct_count}/{len(questions)} = {accuracy:.1f}%")
    print(f"Published GPT-5.4 (no tools): 39.8%")
    print(f"Ended: {ended_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Duration: {duration/60:.1f} min")

    ts = started_at.strftime("%Y%m%d_%H%M%S")
    fname = os.path.join(RESULTS_DIR, f"hle_{args.backend}_{model}_{ts}.json")
    with open(fname, "w") as output:
        json.dump({
            "eval": "hle_text_only", "model": model, "backend": args.backend,
            "n_questions": len(questions), "temperature": TEMPERATURE if supports_temperature(model) else None,
            "reasoning_effort": args.effort,
            "max_output_tokens": 1024,
            "started_at": started_at.isoformat(), "ended_at": ended_at.isoformat(),
            "duration_seconds": round(duration, 1),
            "correct": correct_count, "accuracy_pct": round(accuracy, 2),
            "published_gpt54_score_pct": 39.8,
            "results": results,
        }, output, indent=2)
    print(f"Saved: {os.path.basename(fname)}")


if __name__ == "__main__":
    main()
