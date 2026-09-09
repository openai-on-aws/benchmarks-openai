"""Render or verify the GPT-4.1/GPT-5.6 aggregate reporting package offline."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath

PACKAGE = Path(__file__).resolve().parent
SOURCE = PACKAGE / "results/2026-08-30/scorecard.json"
DESTINATION = PACKAGE
DIRECT = "gpt-4.1-2025-04-14"
MODELS = [DIRECT, "openai.gpt-5.6-luna", "openai.gpt-5.6-terra", "openai.gpt-5.6-sol"]
LABELS = {DIRECT: "GPT-4.1", **{m: m.rsplit("-", 1)[-1].title() for m in MODELS[1:]}}
WORKLOADS = [
    ("classification-routing", "Classification routing", "Task accuracy"),
    ("structured-document-extraction", "Synthetic invoice extraction", "Address-normalized document accuracy"),
    ("banking77", "Banking77", "Exact-match accuracy"),
    ("cord-ocr", "CORD OCR", "Entity F1"),
    ("cord-image", "CORD image", "Entity F1"),
    ("extractbench", "ExtractBench", "Mean unified-value F1"),
    ("gsm8k", "GSM8K", "Task accuracy"),
    ("math500", "MATH-500", "Task accuracy"),
    ("aime", "AIME", "Task accuracy"),
    ("gpqa", "GPQA", "Task accuracy"),
    ("mmlu_pro", "MMLU-Pro", "Task accuracy"),
    ("humaneval", "HumanEval", "Task success"),
]
COUNT_BASED_WORKLOADS = {
    "classification-routing",
    "structured-document-extraction",
    "banking77",
    "gsm8k",
    "math500",
    "aime",
    "gpqa",
    "mmlu_pro",
    "humaneval",
}


def success_count(cell, metric="primary"):
    """Return a discrete correct count only when the metric supports one."""
    if cell["benchmark"] not in COUNT_BASED_WORKLOADS:
        return None
    explicit = cell.get(f"{metric}_successes")
    if explicit is not None:
        return explicit
    value = cell.get(metric)
    if value is None:
        return None
    estimated = value * cell["planned"]
    rounded = round(estimated)
    if abs(estimated - rounded) > 0.01:
        raise ValueError(f"{metric} does not resolve to a discrete success count")
    return rounded


def cost_per_correct(cell, metric="primary"):
    successes = success_count(cell, metric)
    if not successes:
        return None
    cost = cell["operational_unchanged"]["cost_bounds_usd"]["metered_lower_bound_usd"]
    return cost / successes


def format_cost_per_correct(cell, metric="primary"):
    value = cost_per_correct(cell, metric)
    return f"${value:.6f}" if value is not None else "N/A"



GENERATED_FILES = {"RESULTS.md", "results/2026-08-30/scorecard.csv"}
MANIFEST_FILE = "evidence/publication.json"


def verify_public_manifest(include_generated=True):
    """Check package inventory and hashes without consulting private source paths."""
    manifest = json.loads((PACKAGE / MANIFEST_FILE).read_text())
    if manifest.get("manifest_version") != "benchmark-publication-manifest-1.0":
        raise ValueError("unsupported public manifest version")
    records = manifest.get("files")
    if not isinstance(records, dict) or SOURCE.relative_to(PACKAGE).as_posix() not in records:
        raise ValueError("public manifest must include the scorecard")
    for name, record in records.items():
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or ".." in path.parts
                or path.as_posix() != name or "\\" in name or name == MANIFEST_FILE):
            raise ValueError(f"invalid package-relative manifest path: {name}")
        target = PACKAGE / name
        if target.is_symlink() or not target.resolve().is_relative_to(PACKAGE.resolve()):
            raise ValueError(f"manifest path escapes package: {name}")
        if not include_generated and name in GENERATED_FILES:
            continue
        if not target.is_file():
            raise ValueError(f"missing release file: {name}")
        content = target.read_bytes()
        if len(content) != record["bytes"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError(f"release file drift: {name}")
    actual = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob("*") if p.is_file() or p.is_symlink()}
    expected = set(records) | {MANIFEST_FILE}
    if not include_generated:
        actual -= GENERATED_FILES
        expected -= GENERATED_FILES
    if actual != expected:
        raise ValueError(f"release inventory mismatch: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    return manifest


def make_report():
    source_bytes = SOURCE.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    manifest = json.loads((PACKAGE / "evidence/publication.json").read_text())
    source_name = SOURCE.relative_to(PACKAGE).as_posix()
    if manifest["files"][source_name]["sha256"] != source_hash:
        raise ValueError("scorecard does not match its public manifest")
    scorecard = json.loads(source_bytes)
    cells = scorecard["cells"]
    lookup = {(c["benchmark"], c["model_id"], c["effort"]): c for c in cells}
    conditions = [(DIRECT, "omitted"), *[(m, e) for m in MODELS[1:] for e in ("none", "low", "high")]]
    required = {(b, m, e) for b, _, _ in WORKLOADS for m, e in conditions}
    if len(cells) != 120 or set(lookup) != required:
        raise ValueError("full matrix coverage mismatch")
    out = [
        "# Descriptive GPT-4.1 and GPT-5.6 benchmark scorecard", "",
        "**Comparison:** GPT-4.1 through the OpenAI API and GPT-5.6 Luna, Terra, and Sol through Amazon Bedrock across 12 workloads, with a separate GPT-5.6 reasoning-effort sensitivity view.", "",
        "**Scorecard version:** v1.1.0. **Coverage:** all 12 benchmarks, 10 model/reasoning conditions, 120 result cells, and 47,960 planned observations.", "",
        "This is a complete presentation of the saved v1.1.0 scorecard. Synthetic invoice extraction uses address-normalized document accuracy; corrected strict accuracy is retained below. MATH-500 includes the v1.0.0 grader corrections. The other benchmark scores, recorded spend, and latency remain unchanged.", "",
        "All quality values are shown on a 0–100 scale. F1 is explicitly labeled and should not be read as document accuracy. N is the planned observation count per model/condition; the public benchmark names refer to the evaluated subsets shown here.", "",
        "## Interpretation boundary", "",
        "- GPT-4.1 versus GPT-5.6 is a descriptive, cross-platform comparison across the configured API paths; it is not a controlled causal estimate of a model migration.",
        "- GPT-5.6 none versus low/high is descriptive because the saved runs differ in timing and some output ceilings. Reasoning-effort results should be read as workload-specific sensitivity evidence, not a universal reasoning effect.",
        "- The scorecard does not establish migration readiness or a universal model winner. Quality, completion, latency, and cost must be evaluated together against customer-specific acceptance criteria.", "",
        "## Executive operating summary", "",
        "Completion means an API-completed response and is distinct from answer quality. Recorded spend is the metered lower bound across all attempts; a range is shown when missing usage required a conservative upper bound. No quality metric is aggregated across unlike workloads.", "",
        "| Model | Reasoning | Planned | API completed | Completion gap | Completion rate | Recorded spend (USD) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for m, e in conditions:
        condition_cells = [lookup[(b, m, e)] for b, _, _ in WORKLOADS]
        planned = sum(c["planned"] for c in condition_cells)
        completed = sum(c["operational_unchanged"]["api_completed"] for c in condition_cells)
        lower = sum(c["operational_unchanged"]["cost_bounds_usd"]["metered_lower_bound_usd"] for c in condition_cells)
        upper = sum(c["operational_unchanged"]["cost_bounds_usd"]["conservative_upper_bound_usd"] for c in condition_cells)
        spend = f"${lower:.6f}" if abs(upper - lower) < 1e-12 else f"${lower:.6f}–${upper:.6f}"
        out.append(f"| {LABELS[m]} | {e} | {planned} | {completed} | {planned-completed} | {100*completed/planned:.2f}% | {spend} |")
    out += ["", "## Workload quality and recorded cost per correct", "",
            "For accuracy, exact-match, and task-success metrics, recorded cost per correct is metered spend divided by the number correct out of the full planned denominator. F1-only workloads report `N/A`: an aggregate F1 score is not a count of correct documents or entities and cannot support a defensible cost-per-correct calculation from this scorecard.", ""]
    for effort in ("none", "low", "high"):
        out += [f"## GPT-5.6 reasoning: {effort}", "",
                "| Benchmark | Metric | N | GPT-4.1 | GPT-4.1 $/correct | Luna | Luna $/correct | Terra | Terra $/correct | Sol | Sol $/correct |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for b, label, metric in WORKLOADS:
            values = [lookup[(b, m, "omitted" if m == DIRECT else effort)] for m in MODELS]
            if len({c["planned"] for c in values}) != 1:
                raise ValueError("unequal planned counts")
            model_values = []
            for c in values:
                model_values += [f"{100*c['primary']:.2f}%", format_cost_per_correct(c)]
            out.append(f"| {label} | {metric} | {values[0]['planned']} | " + " | ".join(model_values) + " |")
        out.append("")
    out += ["## Invoice extraction: primary and secondary", "",
            "### What normalization means", "",
            "Address normalization allows the same address information to be returned with a line break or a comma separator. It applies only to the synthetic invoice benchmark's `supplier.address` and `customer.address` fields, using the same rule for GPT-4.1 and every GPT-5.6 model/reasoning setting.", "",
            "The exact comparison is:", "",
            "1. Keep every field match already accepted by the strict grader.",
            "2. For a mismatched supplier or customer address, replace each LF newline (`\\n`) with comma-space (`, `) in both the expected address and the model's address. Trim leading/trailing whitespace and collapse whitespace runs to a single space.",
            "3. Accept that address only if the resulting strings match exactly. All remaining characters, digits, punctuation, capitalization, and component order must match.", "",
            "For example, these two addresses are equivalent under the primary metric:", "",
            "```text", "Expected address:", "400 Trial Plaza", "Seattle, WA 98101", "",
            "Model's address:", "400 Trial Plaza, Seattle, WA 98101", "```", "",
            "This example passes address-normalized comparison and fails strict comparison because of the added comma. Changing `400` to `401`, changing postal code `98101` to `98102`, or omitting an apartment present in the reference still fails. Abbreviation expansion, fuzzy matching, case folding, and deletion of other punctuation are not added.", "",
            "The original response must first pass JSON parsing and schema validation. A complete document passes only when the API response is completed and every field passes its applicable comparison. Missing fields, invalid types, wrong dates, and incorrect totals are still checked by the existing rules. All 192 planned documents remain in each condition's denominator.", "",
            "**Primary metric:** address-normalized document accuracy. **Secondary metric:** strict document accuracy using the v1.0.0 corrected references. Strict scoring already collapses whitespace, normalizes currency case, and compares money at cent precision; it does not require byte-for-byte text equality.", "",
            "This formatting rule is separate from the earlier correction of compact-layout invoice references and the MATH-500 grader fixes. It is an offline rescore of saved responses: the source documents and model outputs are preserved, and no new model calls were made.", "",
            "### Results under both metrics", "",
            "| Model | Reasoning | Normalized accuracy | Normalized correct / planned | Recorded $ / normalized correct | Strict accuracy | Strict correct / planned | Recorded $ / strict correct |",
            "|---|---|---:|---:|---:|---:|---:|---:|"]
    for m, e in conditions:
        c = lookup[("structured-document-extraction", m, e)]
        out.append(f"| {LABELS[m]} | {e} | {100*c['primary']:.2f}% | {c['primary_successes']}/{c['planned']} | {format_cost_per_correct(c)} | {100*c['secondary']:.2f}% | {c['secondary_successes']}/{c['planned']} | {format_cost_per_correct(c, 'secondary')} |")
    out += ["", "## Recorded cost and latency for every result", "",
            "Costs are recorded metered totals across all attempts in each cell, not current price quotations or cost per request. Completion is API completion, separate from answer quality. Latencies below are seconds; TTFT is time to first token. All figures are carried directly from the saved operational records.", "",
            "The accompanying CSV also includes token counts, retries, capability failures, cost bounds, and cost-per-correct values for count-based quality metrics. F1-only cost-per-correct values remain blank; no values are inferred.", ""]
    flat = []
    for m, e in conditions:
        out += [f"### {LABELS[m]} / {e}", "",
                "| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |",
                "|---|---:|---:|---:|---:|---:|---:|"]
        for b, label, _ in WORKLOADS:
            c = lookup[(b, m, e)]
            op = c["operational_unchanged"]
            cost = op["cost_bounds_usd"]
            values = [op.get(kind, {}).get(percentile) for kind in ("ttft_ms", "end_to_end_ms") for percentile in ("p50", "p95")]
            out.append(f"| {label} | {op['api_completed']}/{c['planned']} | {cost['metered_lower_bound_usd']:.6f} | "
                       + " | ".join(f"{value/1000:.3f}" if value is not None else "—" for value in values) + " |")
            row = {k: c[k] for k in ("benchmark", "model_id", "effort", "planned", "primary_metric", "primary", "secondary_metric", "secondary")}
            row.update({"primary_percent": c["primary"]*100, "secondary_percent": c["secondary"]*100 if c["secondary"] is not None else None,
                        "api_completed": op["api_completed"], "attempts": op["attempts"], "retry_observations": op["retry_observations"],
                        "capability_failure_observations": op["capability_failure_observations"]})
            for name in ("metered_lower_bound_usd", "conservative_upper_bound_usd", "unpriced_usage_missing_attempts"):
                row[name] = cost.get(name)
            for kind in ("ttft_ms", "end_to_end_ms"):
                for percentile in ("p50", "p95"):
                    row[f"{kind}_{percentile}"] = op.get(kind, {}).get(percentile)
            for name in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_input_tokens", "cache_write_input_tokens"):
                row[name] = op["usage"].get(name)
            row["primary_cost_per_success_usd"] = cost_per_correct(c)
            row["secondary_cost_per_success_usd"] = cost_per_correct(c, "secondary")
            flat.append(row)
        out.append("")
    out += ["## Source and detailed evidence", "",
            "- [All 120 quality and operational rows](results/2026-08-30/scorecard.csv)",
            "- [Versioned scorecard JSON](results/2026-08-30/scorecard.json)",
            "- [Scoring policy](evidence/scoring-policy-v1.1.0.json), [comparison metadata](evidence/comparison-evidence-map.json), and [public file manifest](evidence/publication.json). Paired statistics are in the scorecard JSON; per-case raw evidence is outside this export.",
            "- [Benchmark sources and attribution](BENCHMARK_SOURCES.md).",
            f"- Exported scorecard-file SHA-256: `{source_hash}`.", "",
            "No new inference was performed. Scores describe the saved benchmark cases; the post hoc metric revision should be validated on fresh data before prospective performance claims.", ""]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(flat[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(flat)
    if SOURCE.read_bytes() != source_bytes:
        raise ValueError("source changed during rendering")
    return {"RESULTS.md": "\n".join(out), "results/2026-08-30/scorecard.csv": stream.getvalue()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Regenerate Markdown and CSV from the committed scorecard")
    mode.add_argument("--verify", action="store_true", help="Verify source hashes and reproduce reports without writing (default)")
    args = parser.parse_args()
    verify_public_manifest(include_generated=not args.write)
    for name, content in make_report().items():
        target = DESTINATION / name
        if args.write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode())
            print(f"WROTE {target.relative_to(PACKAGE)}")
        elif not target.is_file() or target.read_bytes() != content.encode():
            raise ValueError(f"Generated report drift: {name}")
    verify_public_manifest()
    if not args.write:
        print("PASS: public file hashes and inventory match; all 120 results reproduce byte for byte in Markdown and CSV")


if __name__ == "__main__":
    main()
