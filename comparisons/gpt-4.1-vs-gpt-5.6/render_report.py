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
    if value is None:
        return "N/A"
    cost = cell["operational_unchanged"]["cost_bounds_usd"]
    upper = cost["conservative_upper_bound_usd"] / success_count(cell, metric)
    return f"${value:.6f}–${upper:.6f}" if upper - value > 1e-12 else f"${value:.6f}"


def format_recorded_spend(lower, upper):
    """Keep an incomplete-usage bound visible wherever spend is displayed."""
    return f"${lower:.2f}–${upper:.2f}" if upper - lower > 1e-12 else f"${lower:.6f}"


def metric_interval(cell, metric="primary"):
    """Read a saved interval for the displayed metric; never manufacture one."""
    interval = cell.get(f"{metric}_wilson_95")
    if interval is not None:
        return interval
    if metric == "primary" and cell["benchmark"] in {"math500", "aime"}:
        return cell["source_v1_0"].get("corrected_wilson_95")
    return None


def attempt_reasons(operational):
    labels = {
        "max-output-tokens-exhausted": "Output-token limit",
        "file-input-over-limit": "File-input limit",
        "context-length-exceeded": "Context-length limit",
        "api-authentication": "Authentication",
        "usage-metadata-missing": "Missing usage metadata",
        "api-error-attempts": "API errors",
    }
    counts = operational.get("error_families_by_attempt", {})
    return "; ".join(f"{labels.get(name, name)}: {count}" for name, count in sorted(counts.items()) if count) or "Cause not classified in aggregate"


def failure_summary(lookup, conditions):
    overall = {}
    for model, effort in conditions:
        cells = [lookup[(benchmark, model, effort)] for benchmark, _, _ in WORKLOADS]
        overall[(model, effort)] = 100 * sum(c["operational_unchanged"]["api_completed"] for c in cells) / sum(c["planned"] for c in cells)
    banking_share = 100 * lookup[("banking77", DIRECT, "omitted")]["planned"] / sum(lookup[(b, DIRECT, "omitted")]["planned"] for b, _, _ in WORKLOADS)
    out = ["", "### Workloads below full completion", "",
           f"Banking77 contributes {banking_share:.2f}% of planned cases per configuration. The overall rate is weighted by case count and can conceal lower completion on smaller workloads. Every affected workload/configuration is shown below; quality continues to use the full planned denominator.", "",
           "| Benchmark | Model | Reasoning | Completed / planned | Workload completion | Overall completion | Uncompleted cases | Recorded attempt events, including retries |",
           "|---|---|---|---:|---:|---:|---:|---|"]
    for benchmark, label, _ in WORKLOADS:
        for model, effort in conditions:
            cell = lookup[(benchmark, model, effort)]
            op = cell["operational_unchanged"]
            gap = cell["planned"] - op["api_completed"]
            if gap:
                out.append(f"| {label} | {LABELS[model]} | {effort} | {op['api_completed']}/{cell['planned']} | {100*op['api_completed']/cell['planned']:.2f}% | {overall[(model, effort)]:.2f}% | {gap} | {attempt_reasons(op)} |")
    out += ["",
            "Uncompleted cases count final outcomes. Recorded attempt events count events across attempts, including errors recovered by a retry; they need not sum to the case gap. Some cases ended in local input rejection without an API response. A zero API-error count does not explain a completion gap, so missing classifications are shown explicitly.", "",
            "An output-token-limit event means the request exhausted its configured allowance before completing the response. A file-input-limit event means the recorded input exceeded a limit enforced by the tested input path. These are outcomes of the tested configurations. [Executed settings and timing definitions](METHODOLOGY.md#executed-run-settings) explain the budgets, concurrency, retries, and latency populations.", ""]
    return out


def statistical_interpretation(delta, adjusted_p):
    if delta == 0:
        return "No observed difference; equality not established"
    if adjusted_p >= 0.05:
        return "Difference not established"
    return ("Higher" if delta > 0 else "Lower") + " measured score; adjusted test threshold met"


def uncertainty_sections(scorecard, lookup, conditions):
    """Present existing statistical outputs with their original comparison scope."""
    records = []
    for item in scorecard["paired_comparisons"]:
        for metric, label in (("strict", "Strict document accuracy"), ("normalized", "Address-normalized (post hoc; exploratory)")):
            records.append((item, label, item[metric], "holm_adjusted_p_within_family"))
    for item in scorecard["unchanged_paired_comparisons_from_v1_0"]:
        records.append((item, "Corrected task accuracy", item["corrected"], "holm_adjusted_p_within_workload"))
    paired_workloads = {item[0]["benchmark"] for item in records}
    labels = {b: label for b, label, _ in WORKLOADS}
    out = ["## Statistical uncertainty and comparison coverage", "",
           "Point estimates describe these saved cases. Available intervals and paired tests are reproduced below without recomputation. Statistical support is not uniform across the 120 result cells: the exported paired comparisons cover synthetic invoices, MATH-500, and AIME only.", "",
           "| Benchmark | Saved per-configuration 95% intervals | Saved paired comparisons |",
           "|---|---|---|"]
    for benchmark, label, _ in WORKLOADS:
        count = sum(metric_interval(lookup[(benchmark, model, effort)]) is not None for model, effort in conditions)
        intervals = f"Wilson intervals for {count} configurations" if count else "Unavailable in saved scorecard"
        pairs = "GPT-4.1 vs none; low vs high" if benchmark in paired_workloads else "Unavailable in saved scorecard"
        out.append(f"| {label} | {intervals} | {pairs} |")
    out += ["",
            "Paired 95% intervals are unadjusted, stratified task-bootstrap intervals from 10,000 resamples. The p-values are exact two-sided McNemar tests adjusted with Holm within each three-model family, separately for each workload, comparison type, and scoring rule. They are not adjusted across all 120 cells. An interval excluding zero can therefore coexist with an adjusted p-value above 0.05.", "",
            "The interpretation column uses the stored adjusted test at 0.05. 'Difference not established' does not demonstrate equivalence. A zero-width bootstrap interval when all observed paired outcomes agree does not establish zero uncertainty in future cases. These tests do not remove cross-platform/run-condition differences or validate a post hoc scoring choice; normalized invoice results remain exploratory.", ""]
    for family, heading in (("gpt41-vs-gpt56-none", "GPT-4.1 versus GPT-5.6 none"), ("gpt56-low-vs-high", "GPT-5.6 low versus high")):
        out += [f"### Paired comparisons: {heading}", "",
                "Delta is candidate minus baseline in percentage points (pp). The first family uses GPT-4.1 omitted as baseline and each GPT-5.6 model at none as candidate; the second compares high against low within the same model.", "",
                "| Benchmark | Model | Scoring rule | Paired cases | Delta (pp) | Paired 95% interval (pp; unadjusted) | Holm-adjusted p | Interpretation |",
                "|---|---|---|---:|---:|---:|---:|---|"]
        for item, metric, stats, p_key in records:
            if item["comparison"] != family:
                continue
            lo, hi = stats["paired_95_interval"]
            p_value = stats[p_key]
            out.append(f"| {labels[item['benchmark']]} | {LABELS[item['candidate_model']]} | {metric} | {stats['paired_tasks']} | {100*stats['delta']:+.2f} | [{100*lo:+.2f}, {100*hi:+.2f}] | {p_value:.6f} | {statistical_interpretation(stats['delta'], p_value)} |")
        out.append("")
    out += ["### Available per-configuration confidence intervals", "",
            "These are the saved Wilson 95% intervals for a single configuration's proportion correct, with the full planned denominator. They are unadjusted and are not intervals for the difference between models. The 40 metric rows below cover 30 result cells; invoices have both strict and normalized intervals. No intervals are inferred for the other 90 cells or for F1 metrics.", "",
            "| Benchmark | Model | Reasoning | Scoring rule | Correct / planned | Accuracy | Wilson 95% interval (unadjusted) |",
            "|---|---|---|---|---:|---:|---:|"]
    for benchmark, label, _ in WORKLOADS:
        for model, effort in conditions:
            cell = lookup[(benchmark, model, effort)]
            metrics = (("secondary", "Strict document accuracy"), ("primary", "Address-normalized (post hoc; exploratory)")) if benchmark == "structured-document-extraction" else (("primary", "Corrected task accuracy"),)
            for metric, metric_label in metrics:
                interval = metric_interval(cell, metric)
                if interval is None:
                    continue
                lo, hi = interval
                out.append(f"| {label} | {LABELS[model]} | {effort} | {metric_label} | {success_count(cell, metric)}/{cell['planned']} | {100*cell[metric]:.2f}% | [{100*lo:.2f}%, {100*hi:.2f}%] |")
    return out + [""]



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
        f"**Measurement version:** v1.1.0. **Reporting package:** {manifest['package_version']}. **Coverage:** all 12 benchmarks, 10 model/reasoning conditions, 120 result cells, and 47,960 planned observations.", "",
        "This project-specific evaluation presents the saved v1.1.0 scorecard under the recorded configurations and scoring rules. Every headline quality table shows both strict and address-normalized invoice accuracy. Address normalization was introduced after inspecting saved responses and remains exploratory pending validation on fresh cases. MATH-500 includes the v1.0.0 grader corrections.", "",
        "All quality values are shown on a 0–100 scale. F1 is explicitly labeled and should not be read as document accuracy. N is the planned observation count per model/condition; the public benchmark names refer to the evaluated subsets shown here.", "",
        "## Interpretation boundary", "",
        "- GPT-4.1 versus GPT-5.6 is a descriptive, cross-platform comparison across the configured API paths; it is not a controlled causal estimate of a model migration.",
        "- GPT-5.6 none versus low/high is descriptive because the saved runs differ in timing, some output ceilings, and overlapping suite execution. [Executed settings and dataset selection](METHODOLOGY.md) document these differences and unrecorded details. Reasoning-effort results should be read as workload-specific sensitivity evidence, not a universal reasoning effect.",
        "- The scorecard does not establish migration readiness or a universal model winner. Quality, completion, latency, and cost must be evaluated together against customer-specific acceptance criteria.", "",
        "[Workload completion gaps](#workloads-below-full-completion), [statistical uncertainty](#statistical-uncertainty-and-comparison-coverage), and [methodology](METHODOLOGY.md) are part of the interpretation of every point estimate.", "",
        "## Executive operating summary", "",
        "Completion means an API-completed response and is distinct from answer quality. Recorded spend is the metered lower bound across all attempts; a range is shown when missing usage required a conservative upper bound. No quality metric is aggregated across unlike workloads.", "",
        "| Model | Reasoning | Planned | API completed | Completion gap | Completion rate | Recorded spend / bounds (USD) | Attempts missing usage |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for m, e in conditions:
        condition_cells = [lookup[(b, m, e)] for b, _, _ in WORKLOADS]
        planned = sum(c["planned"] for c in condition_cells)
        completed = sum(c["operational_unchanged"]["api_completed"] for c in condition_cells)
        lower = sum(c["operational_unchanged"]["cost_bounds_usd"]["metered_lower_bound_usd"] for c in condition_cells)
        upper = sum(c["operational_unchanged"]["cost_bounds_usd"]["conservative_upper_bound_usd"] for c in condition_cells)
        missing = sum(c["operational_unchanged"]["cost_bounds_usd"].get("unpriced_usage_missing_attempts", 0) for c in condition_cells)
        spend = format_recorded_spend(lower, upper)
        out.append(f"| {LABELS[m]} | {e} | {planned} | {completed} | {planned-completed} | {100*completed/planned:.2f}% | {spend} | {missing} |")
    out += failure_summary(lookup, conditions)
    out += ["", "## Workload quality and recorded cost per correct", "",
            "For accuracy, exact-match, and task-success metrics, recorded cost per correct is metered spend divided by the number correct out of the full planned denominator. F1-only workloads report `N/A`: an aggregate F1 score is not a count of correct documents or entities and cannot support a defensible cost-per-correct calculation from this scorecard.", ""]
    for effort in ("none", "low", "high"):
        out += [f"## GPT-5.6 reasoning: {effort}", "",
                "Invoice strict and address-normalized scores describe the same saved responses. The normalized row is post hoc and exploratory; it requires validation on fresh cases. These point estimates do not have uniform statistical support: [available confidence intervals and paired comparisons](#statistical-uncertainty-and-comparison-coverage) cover invoices, MATH-500, and AIME only.", "",
                "| Benchmark | Metric | N | GPT-4.1 | GPT-4.1 $/correct | Luna | Luna $/correct | Terra | Terra $/correct | Sol | Sol $/correct |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for b, label, metric in WORKLOADS:
            values = [lookup[(b, m, "omitted" if m == DIRECT else effort)] for m in MODELS]
            if len({c["planned"] for c in values}) != 1:
                raise ValueError("unequal planned counts")
            metrics = (("secondary", "Strict document accuracy"), ("primary", "Address-normalized accuracy (post hoc; exploratory)")) if b == "structured-document-extraction" else (("primary", metric),)
            for value_key, metric_label in metrics:
                model_values = []
                for c in values:
                    model_values += [f"{100*c[value_key]:.2f}%", format_cost_per_correct(c, value_key)]
                out.append(f"| {label} | {metric_label} | {values[0]['planned']} | " + " | ".join(model_values) + " |")
        out.append("")
    out += uncertainty_sections(scorecard, lookup, conditions)
    out += ["## Invoice extraction: strict and post hoc normalized scoring", "",
            "### What normalization means", "",
            "Address normalization allows the same address information to be returned with a line break or a comma separator. It applies only to the synthetic invoice benchmark's `supplier.address` and `customer.address` fields, using the same rule for GPT-4.1 and every GPT-5.6 model/reasoning setting.", "",
            "The exact comparison is:", "",
            "1. Keep every field match already accepted by the strict grader.",
            "2. For a mismatched supplier or customer address, replace each LF newline (`\\n`) with comma-space (`, `) in both the expected address and the model's address. Trim leading/trailing whitespace and collapse whitespace runs to a single space.",
            "3. Accept that address only if the resulting strings match exactly. All remaining characters, digits, punctuation, capitalization, and component order must match.", "",
            "In this illustrative example, these two addresses are equivalent under address-normalized scoring. This is not a captured model response:", "",
            "```text", "Expected address:", "400 Trial Plaza", "Seattle, WA 98101", "",
            "Model's address:", "400 Trial Plaza, Seattle, WA 98101", "```", "",
            "This example passes address-normalized comparison and fails strict comparison because of the added comma. Changing `400` to `401`, changing postal code `98101` to `98102`, or omitting an apartment present in the reference still fails. Abbreviation expansion, fuzzy matching, case folding, and deletion of other punctuation are not added.", "",
            "The original response must first pass JSON parsing and schema validation. A complete document passes only when the API response is completed and every field passes its applicable comparison. Missing fields, invalid types, wrong dates, and incorrect totals are still checked by the existing rules. All 192 planned documents remain in each condition's denominator.", "",
            "The saved JSON/CSV uses `primary` for address-normalized document accuracy and `secondary` for strict document accuracy using the v1.0.0 corrected references. Those field names preserve the measurement record; they do not mean normalization was specified before the run or validated on fresh cases. Address normalization is a post hoc, exploratory metric revision. Strict scoring already collapses whitespace, normalizes currency case, and compares money at cent precision; it does not require byte-for-byte text equality.", "",
            "This formatting rule is separate from the earlier correction of compact-layout invoice references and the MATH-500 grader fixes. It is an offline rescore of saved responses: the source documents and model outputs are preserved, and no new model calls were made.", "",
            "### Results under both metrics", "",
            "| Model | Reasoning | Strict accuracy | Strict correct / planned | Recorded $ / strict correct | Normalized accuracy (post hoc; exploratory) | Normalized correct / planned | Recorded $ / normalized correct |",
            "|---|---|---:|---:|---:|---:|---:|---:|"]
    for m, e in conditions:
        c = lookup[("structured-document-extraction", m, e)]
        out.append(f"| {LABELS[m]} | {e} | {100*c['secondary']:.2f}% | {c['secondary_successes']}/{c['planned']} | {format_cost_per_correct(c, 'secondary')} | {100*c['primary']:.2f}% | {c['primary_successes']}/{c['planned']} | {format_cost_per_correct(c)} |")
    out += ["", "## Recorded cost and latency for every result", "",
            "Costs sum recorded usage across all attempts, including retries. A range runs from the metered lower bound to the conservative upper estimate for attempts missing usage. It is an accounting bound, not a statistical confidence interval or an exact billed total. Exact values remain in the CSV. Completion is API completion, separate from answer quality.", "",
            "For Sol/high ExtractBench, four saved attempts were marked completed but lacked usage metadata and were retried. The metered total includes the recorded usage of their retries; usage for the original attempts remains unpriced. The stored upper estimate adds an allowance for each missing attempt using 1,000,000 uncached input tokens and 16,384 output tokens at the frozen long-context rates. The saved evidence does not establish why usage metadata was absent.", "",
            "Latency values below are seconds. End-to-end means the final retained attempt's duration, including terminal failures and local input rejections; it excludes earlier retry durations, retry backoff, and application preprocessing outside the attempt timer. TTFT is time to first text output on final attempts with a recorded first-output timestamp; attempts without that timestamp do not enter its percentiles. [Timing and retry definitions](METHODOLOGY.md#latency-retries-and-cost) explain the different populations. All timing values are carried directly from the saved records.", "",
            "The accompanying CSV also includes token counts, retries, harness-classified terminal failure counts (`capability_failure_observations`), cost bounds, and cost-per-correct values for count-based quality metrics. The terminal-failure field includes recorded request/input-limit outcomes and inherited incomplete-response counts where the source aggregate does not classify a cause; it does not assess inherent model capability. F1-only cost-per-correct values remain blank; no values are inferred.", ""]
    flat = []
    for m, e in conditions:
        out += [f"### {LABELS[m]} / {e}", "",
                "| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |",
                "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for b, label, _ in WORKLOADS:
            c = lookup[(b, m, e)]
            op = c["operational_unchanged"]
            cost = op["cost_bounds_usd"]
            values = [op.get(kind, {}).get(percentile) for kind in ("ttft_ms", "end_to_end_ms") for percentile in ("p50", "p95")]
            spend = format_recorded_spend(cost["metered_lower_bound_usd"], cost["conservative_upper_bound_usd"])
            out.append(f"| {label} | {op['api_completed']}/{c['planned']} | {spend} | {cost.get('unpriced_usage_missing_attempts', 0)} | "
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
            "- [Scoring policy](evidence/scoring-policy-v1.1.0.json), [comparison metadata](evidence/comparison-evidence-map.json), and [public file manifest](evidence/publication.json). The statistical tables reproduce the available paired results and confidence intervals in the scorecard JSON; per-case raw evidence is outside this export.",
            "- [Methodology](METHODOLOGY.md), [executed run settings](evidence/run-settings.json), and [dataset selection](evidence/dataset-selection.json).",
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
