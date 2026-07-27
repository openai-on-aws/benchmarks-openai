"""
DeepSearchQA judge — two-layer design to contain same-family judge bias.

All candidates in this repo's run are OpenAI models, and the team's
constraint is an OpenAI judge. That creates family-preference risk, so:

Layer 1 — deterministic pre-pass (no LLM): each ground-truth item is
  matched against the answer text (normalized substring). Items that
  match are settled objectively; only unresolved items go to the LLM.
Layer 2 — LLM judge: gpt-5.5 (OpenAI family per team constraint, but
  NOT one of the candidate arms, so no model grades its own output),
  temperature omitted, using the FROZEN DeepSearchQA autorater prompt
  (arXiv:2601.20975 App. A) verbatim.

Every case records both layers, and the summary reports the agreement
rate between the deterministic pass and the judge on the items both
graded — the bias-visibility metric.

Caveats recorded in every output file: the paper prescribes
gemini-2.5-flash as judge, so absolute P/R/F1 here is NOT
leaderboard-comparable; within-run comparisons are cross-checked by
the deterministic layer.

Usage:
  python quality/deepsearchqa/judge_deepsearchqa.py             # judge all unjudged result files
  python quality/deepsearchqa/judge_deepsearchqa.py --file quality/results/deepsearchqa_..._.json
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from quick_evals import make_client  # noqa: E402

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "results")
PROMPT_FILE = os.path.join(os.path.dirname(__file__), "deepsearchqa-grading-prompt.md")

JUDGE_MODEL = "gpt-5.5"   # OpenAI family (team constraint) but NOT a candidate arm
PASS_F1 = 0.70


def load_frozen_prompt():
    text = open(PROMPT_FILE).read()
    m = re.search(r"<!-- TEMPLATE:START -->\n(.*?)<!-- TEMPLATE:END -->", text, re.S)
    if not m:  # colleague's file may end at EOF without an END marker
        m = re.search(r"<!-- TEMPLATE:START -->\n(.*)", text, re.S)
    template = m.group(1).strip()
    sha = hashlib.sha256(template.encode()).hexdigest()
    return template, sha


def split_gold(gold, answer_type):
    if answer_type == "Single Answer":
        return [gold.strip()]
    # Set answers in the dataset are comma-separated strings; parenthetical
    # qualifiers stay attached to their item.
    parts, depth, cur = [], 0, ""
    for ch in gold:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return [p for p in parts if p]


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def final_region(answer_text):
    """The conclusion section of the answer: from the last 'final answer'
    marker if present, else the last 600 chars. Scanning the whole text
    over-credits items merely mentioned mid-reasoning."""
    m = list(re.finditer(r"final answer|answer[:\s]*$|^\*\*", answer_text,
                         re.I | re.M))
    if m:
        return answer_text[m[-1].start():]
    return answer_text[-600:]


def det_match(item, answer_text):
    """True if the ground-truth item plainly appears in the answer's conclusion."""
    ni, na = norm(item), norm(final_region(answer_text))
    if not ni:
        return False
    if ni in na:
        return True
    # tolerate dropped parenthetical qualifiers: match the pre-paren head
    head = norm(re.sub(r"\(.*?\)", "", item))
    return bool(head) and len(head) >= 4 and head in na


def judge_case(client, template, case):
    """Return the case's judgment dict (deterministic + LLM layers)."""
    answer = case["answer_text"] or ""
    items = split_gold(case["gold"], case["answer_type"])
    det = {item: det_match(item, answer) for item in items}

    prompt = (template.replace("{prompt}", case["problem"])
                      .replace("{prompt_type}", case["answer_type"])
                      .replace("{answer}", case["gold"])
                      .replace("{response}", answer))
    llm = {"parsed": False, "details": None, "excessive": None, "raw": None}
    try:
        r = client.responses.create(model=JUDGE_MODEL,
                                    input=[{"role": "user", "content": prompt}],
                                    max_output_tokens=2048)
        raw = r.output_text
        llm["raw"] = raw[:2000]
        m = re.search(r"\{.*\}", raw, re.S)
        parsed = json.loads(m.group(0))
        ac = parsed["Answer Correctness"]
        llm["details"] = {str(k): bool(v) for k, v in ac["Correctness Details"].items()}
        llm["excessive"] = [str(x) for x in ac.get("Excessive Answers", [])]
        llm["parsed"] = True
    except Exception as e:
        llm["error"] = str(e)[:300]

    # score from the LLM layer when parsed; else fall back to deterministic
    if llm["parsed"]:
        found = sum(1 for v in llm["details"].values() if v)
        expected = len(llm["details"])
        excessive = len(llm["excessive"])
        source = "llm"
    else:
        found = sum(1 for v in det.values() if v)
        expected = len(det)
        excessive = 0
        source = "deterministic_fallback"

    precision = found / (found + excessive) if (found + excessive) else 0.0
    recall = found / expected if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    # agreement between layers, over the deterministic item set
    agree = None
    if llm["parsed"] and det:
        # align llm detail keys to gold items by normalized text
        llm_by_norm = {norm(k): v for k, v in llm["details"].items()}
        overlaps = [(det[i], llm_by_norm.get(norm(i))) for i in det
                    if norm(i) in llm_by_norm]
        if overlaps:
            agree = sum(1 for a, b in overlaps if a == b) / len(overlaps)

    return {"deterministic": det, "llm": llm, "score_source": source,
            "found": found, "expected": expected, "excessive_count": excessive,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "pass": f1 >= PASS_F1,
            "layer_agreement": round(agree, 4) if agree is not None else None}


def judge_file(path, client, template, sha):
    d = json.load(open(path))
    out_path = path.replace(".json", "_judged.json")
    if os.path.exists(out_path):
        print(f"skip (already judged): {os.path.basename(path)}")
        return
    print(f"judging {os.path.basename(path)} ({len(d['results'])} cases) ...")
    judged = []
    for case in d["results"]:
        j = judge_case(client, template, case) if case["answer_text"] else \
            {"score_source": "no_answer", "precision": 0.0, "recall": 0.0,
             "f1": 0.0, "pass": False, "layer_agreement": None,
             "deterministic": {}, "llm": None, "found": 0,
             "expected": len(split_gold(case["gold"], case["answer_type"])),
             "excessive_count": 0}
        judged.append({**case, "judgment": j})
        print(f"   idx={case['dataset_index']:>3} F1={j['f1']:.2f} "
              f"({j['score_source']}, agree={j['layer_agreement']})")

    ok = [c for c in judged if c["judgment"]["score_source"] != "no_answer"]
    agrees = [c["judgment"]["layer_agreement"] for c in ok
              if c["judgment"]["layer_agreement"] is not None]
    summary = {
        "judge_model": JUDGE_MODEL,
        "judge_family_caveat": ("Judge is same-family (OpenAI) as all candidates and "
                                "differs from the paper's prescribed gemini-2.5-flash; "
                                "absolute scores are not leaderboard-comparable. "
                                "No candidate arm judges its own output."),
        "grading_prompt_sha256": sha,
        "pass_threshold_f1": PASS_F1,
        "mean_precision": round(sum(c["judgment"]["precision"] for c in judged) / len(judged), 4),
        "mean_recall": round(sum(c["judgment"]["recall"] for c in judged) / len(judged), 4),
        "mean_f1": round(sum(c["judgment"]["f1"] for c in judged) / len(judged), 4),
        "pass_rate": round(sum(1 for c in judged if c["judgment"]["pass"]) / len(judged), 4),
        "mean_layer_agreement": round(sum(agrees) / len(agrees), 4) if agrees else None,
        "n_llm_parse_failures": sum(1 for c in ok if c["judgment"]["score_source"] == "deterministic_fallback"),
    }
    with open(out_path, "w") as f:
        json.dump({**{k: d[k] for k in d if k != "results"},
                   "judge_summary": summary, "results": judged}, f, indent=2)
    print(f"  mean F1 {summary['mean_f1']} | pass {summary['pass_rate']:.0%} "
          f"| layer agreement {summary['mean_layer_agreement']}")
    print(f"  Saved {os.path.basename(out_path)}")


def main():
    p = argparse.ArgumentParser(description="Judge DeepSearchQA result files")
    p.add_argument("--file", help="one result file (default: all unjudged)")
    args = p.parse_args()

    template, sha = load_frozen_prompt()
    client, _ = make_client("saas")   # judge runs on the 1P key
    files = ([args.file] if args.file else
             [f for f in sorted(glob.glob(os.path.join(RESULTS_DIR, "deepsearchqa_*.json")))
              if not f.endswith("_judged.json")])
    for path in files:
        judge_file(path, client, template, sha)


if __name__ == "__main__":
    main()
