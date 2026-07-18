"""
Build a side-by-side Bedrock vs OpenAI-1P comparison from result JSONs.

Reads schema_version-2 files produced by benchmark.py (older files from the
legacy scripts are skipped with a note). For each (input size, max_output_tokens)
config present on both backends, prints TTFT / ITL / tok-s / E2E side by side
and the relative delta. Writes COMPARISON.md next to the results.

Usage:
  python performance/compare.py                          # all v2 results, latest run per config
  python performance/compare.py --model-a openai.gpt-5.6-luna --model-b gpt-5.6-luna
  python performance/compare.py --out performance/results/COMPARISON.md
"""

import argparse
import glob
import json
import os

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

SIZE_ORDER = {"1k": 0, "5k": 1, "10k": 2, "20k": 3}
METRICS = [
    ("ttft_ms", "TTFT p50 (ms)", "lower"),
    ("itl_ms", "ITL p50 (ms)", "lower"),
    ("otps", "Tok/s p50", "higher"),
    ("e2e_ms", "E2E p50 (ms)", "lower"),
]


def load_results(results_dir):
    """Return {(backend, model, input_label, max_out): summary_row} keeping the latest run."""
    runs = {}
    skipped = 0
    for path in sorted(glob.glob(os.path.join(results_dir, "results_*.json"))):
        with open(path) as f:
            data = json.load(f)
        if data.get("schema_version") != 2:
            skipped += 1
            continue
        for row in data["summary"]:
            key = (data["backend"], data["model"], data["input_label"],
                   row["max_output_tokens"], data.get("concurrency", 1),
                   data.get("reasoning_effort"))
            candidate = {**row, "started_at": data["started_at"], "file": os.path.basename(path)}
            if key not in runs or candidate["started_at"] > runs[key]["started_at"]:
                runs[key] = candidate
    if skipped:
        print(f"(skipped {skipped} legacy result files without schema_version=2)")
    return runs


def pick(runs, backend, model):
    out = {}
    for (b, m, size, max_out, conc, effort), row in runs.items():
        if b == backend and (model is None or m == model):
            out[(size, max_out, conc, effort)] = {**row, "model": m}
    return out


def fmt_delta(a, b, direction):
    """Positive = Bedrock better."""
    if a is None or b is None or b == 0:
        return "n/a"
    if direction == "lower":
        pct = (b - a) / b * 100  # how much lower Bedrock is vs 1P
    else:
        pct = (a - b) / b * 100  # how much higher Bedrock is vs 1P
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.0f}%"


def main():
    p = argparse.ArgumentParser(description="Compare Bedrock vs OpenAI 1P benchmark results")
    p.add_argument("--model-a", help="Bedrock model id to select (default: any)")
    p.add_argument("--model-b", help="OpenAI 1P model id to select (default: any)")
    p.add_argument("--results-dir", default=RESULTS_DIR)
    p.add_argument("--out", default=os.path.join(RESULTS_DIR, "COMPARISON.md"))
    args = p.parse_args()

    runs = load_results(args.results_dir)
    bedrock = pick(runs, "bedrock", args.model_a)
    openai_1p = pick(runs, "openai", args.model_b)

    common = sorted(set(bedrock) & set(openai_1p),
                    key=lambda k: (SIZE_ORDER.get(k[0], 9), k[1], k[2], str(k[3])))
    only_br = set(bedrock) - set(openai_1p)
    only_1p = set(openai_1p) - set(bedrock)

    if not common:
        print("No overlapping (input size, max_out, concurrency, effort) configs between backends yet.")
        if bedrock:
            print(f"  Bedrock configs:  {sorted(set(bedrock))}")
        if openai_1p:
            print(f"  OpenAI configs:   {sorted(set(openai_1p))}")
        return

    lines = []
    lines.append("# Bedrock vs OpenAI 1P — latency comparison")
    lines.append("")
    a_models = sorted({v["model"] for v in bedrock.values()})
    b_models = sorted({v["model"] for v in openai_1p.values()})
    lines.append(f"- **Bedrock model(s):** {', '.join(a_models)}")
    lines.append(f"- **OpenAI 1P model(s):** {', '.join(b_models)}")
    lines.append("- Values are p50 across runs; delta is Bedrock relative to 1P "
                 "(positive = Bedrock better). Full distributions (p95/p99/mean) are "
                 "in the underlying result JSONs.")
    lines.append("")
    lines.append("| Input | Max out | Conc | Effort | Metric | Bedrock | OpenAI 1P | Delta |")
    lines.append("|---|---|---|---|---|---|---|---|")

    for key in common:
        size, max_out, conc, effort = key
        br, op = bedrock[key], openai_1p[key]
        for field, label, direction in METRICS:
            a = (br.get(field) or {}).get("p50") if br.get(field) else None
            b = (op.get(field) or {}).get("p50") if op.get(field) else None
            if a is None and b is None:
                continue
            lines.append(f"| {size} | {max_out} | {conc} | {effort or '-'} | {label} "
                         f"| {a if a is not None else 'n/a'} | {b if b is not None else 'n/a'} "
                         f"| {fmt_delta(a, b, direction)} |")

    lines.append("")
    lines.append("## Source files")
    lines.append("")
    for key in common:
        lines.append(f"- `{bedrock[key]['file']}` vs `{openai_1p[key]['file']}`")
    if only_br:
        lines.append("")
        lines.append(f"Configs with Bedrock results only (no 1P counterpart yet): {sorted(only_br)}")
    if only_1p:
        lines.append("")
        lines.append(f"Configs with 1P results only (no Bedrock counterpart yet): {sorted(only_1p)}")

    report = "\n".join(lines) + "\n"
    print(report)
    with open(args.out, "w") as f:
        f.write(report)
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
