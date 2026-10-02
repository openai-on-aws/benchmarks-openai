"""
Build a side-by-side comparison from result JSONs.

Reads schema_version-2/3 files produced by benchmark.py (older files from the
legacy scripts are skipped with a note). For each (input size, max_output_tokens)
config present on both sides, prints timing and throughput metrics side by side
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
    ("ttfe_ms", "TTFE p50 (ms)", "lower"),
    ("ttft_ms", "TTFT p50 (ms)", "lower"),
    ("reasoning_wait_ms", "TTFT−TTFE p50 (ms)", "lower"),
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
        if data.get("schema_version") not in (2, 3):
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
        print(f"(skipped {skipped} legacy result files without schema_version=2/3)")
    return runs


def pick(runs, backend, model):
    out = {}
    for (b, m, size, max_out, conc, effort), row in runs.items():
        if b == backend and (model is None or m == model):
            out[(size, max_out, conc, effort)] = {**row, "model": m}
    return out


def fmt_delta(a, b, direction):
    """Positive = side A better."""
    if a is None or b is None or b == 0:
        return "n/a"
    if direction == "lower":
        pct = (b - a) / b * 100
    else:
        pct = (a - b) / b * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.0f}%"


def main():
    p = argparse.ArgumentParser(description="Compare two benchmark result groups")
    p.add_argument("--backend-a", default="bedrock")
    p.add_argument("--backend-b", default="openai")
    p.add_argument("--model-a", help="side A model id to select (default: any)")
    p.add_argument("--model-b", help="side B model id to select (default: any)")
    p.add_argument("--label-a", help="display label for side A")
    p.add_argument("--label-b", help="display label for side B")
    p.add_argument("--results-dir", default=RESULTS_DIR)
    p.add_argument("--out", default=os.path.join(RESULTS_DIR, "COMPARISON.md"))
    args = p.parse_args()

    runs = load_results(args.results_dir)
    side_a = pick(runs, args.backend_a, args.model_a)
    side_b = pick(runs, args.backend_b, args.model_b)
    label_a = args.label_a or args.backend_a
    label_b = args.label_b or args.backend_b

    common = sorted(set(side_a) & set(side_b),
                    key=lambda k: (SIZE_ORDER.get(k[0], 9), k[1], k[2], str(k[3])))
    only_a = set(side_a) - set(side_b)
    only_b = set(side_b) - set(side_a)

    if not common:
        print("No overlapping (input size, max_out, concurrency, effort) configs between backends yet.")
        if side_a:
            print(f"  {label_a} configs: {sorted(set(side_a))}")
        if side_b:
            print(f"  {label_b} configs: {sorted(set(side_b))}")
        return

    lines = []
    lines.append(f"# {label_a} vs {label_b} — latency comparison")
    lines.append("")
    a_models = sorted({v["model"] for v in side_a.values()})
    b_models = sorted({v["model"] for v in side_b.values()})
    lines.append(f"- **{label_a} model(s):** {', '.join(a_models)}")
    lines.append(f"- **{label_b} model(s):** {', '.join(b_models)}")
    lines.append(f"- Values are p50 across runs; delta is {label_a} relative to {label_b} "
                 f"(positive = {label_a} better). Full distributions (p95/p99/mean) are "
                 "in the underlying result JSONs.")
    lines.append("")
    lines.append(f"| Input | Max out | Conc | Effort | Metric | {label_a} | {label_b} | Delta |")
    lines.append("|---|---|---|---|---|---|---|---|")

    for key in common:
        size, max_out, conc, effort = key
        a_row, b_row = side_a[key], side_b[key]
        for field, label, direction in METRICS:
            a = (a_row.get(field) or {}).get("p50") if a_row.get(field) else None
            b = (b_row.get(field) or {}).get("p50") if b_row.get(field) else None
            if a is None and b is None:
                continue
            lines.append(f"| {size} | {max_out} | {conc} | {effort or '-'} | {label} "
                         f"| {a if a is not None else 'n/a'} | {b if b is not None else 'n/a'} "
                         f"| {fmt_delta(a, b, direction)} |")

    lines.append("")
    lines.append("## Source files")
    lines.append("")
    seen_pairs = []
    for key in common:
        pair = (side_a[key]["file"], side_b[key]["file"])
        if pair not in seen_pairs:
            seen_pairs.append(pair)
            lines.append(f"- `{pair[0]}` vs `{pair[1]}`")
    if only_a:
        lines.append("")
        lines.append(f"Configs with {label_a} results only: {sorted(only_a)}")
    if only_b:
        lines.append("")
        lines.append(f"Configs with {label_b} results only: {sorted(only_b)}")

    report = "\n".join(lines) + "\n"
    print(report)
    with open(args.out, "w") as f:
        f.write(report)
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
