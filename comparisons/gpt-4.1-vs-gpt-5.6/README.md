# GPT-4.1 versus GPT-5.6: benchmark results

This reporting package (1.0.0-rc5) presents the saved comparison across **12 workloads, 10 model/reasoning conditions, and 47,960 planned observations**. Full-stage runs are dated August 26–30, 2026. The measurement scorecard remains v1.1.0, including the earlier grading corrections and the address-normalization metric revision. This is a project-specific evaluation of the recorded configurations and scoring rules.

Start with **[RESULTS.md](RESULTS.md)** for all quality, completion, recorded cost, and p50/p95 latency results. The **[120-row CSV](results/2026-08-30/scorecard.csv)** includes token usage and cost per correct result for count-based metrics; F1-only workloads leave cost per correct blank.

For a visual view, open **[CHARTS.md](CHARTS.md)** for the summary and workload previews, or download **[CHARTS.html](CHARTS.html)** and open it locally in a browser. The offline explorer includes all 12 workloads and 120 result cells, with 36 workload/reasoning views of TTFT, response latency (final attempt), completion, recorded spend, a quality-versus-cost scatter plot, and an accompanying quality table. Invoice scatter plots show strict and post hoc normalized scoring in separate panels. GitHub displays the HTML source rather than running the explorer.

![Completion and recorded spend across the ten tested configurations](charts/operating-summary.png)

The summary is weighted by case count; Banking77 supplies 64.22% of cases. Use the workload views to inspect completion gaps. No overall latency or quality score is pooled across workloads.

The report places workload completion gaps beside overall completion, shows strict and post hoc normalized invoice scores together, displays cost bounds and missing-usage counts at workload level, and presents the saved confidence intervals and paired tests. **[METHODOLOGY.md](METHODOLOGY.md)** documents execution settings, timing populations, subset selection, synthetic-case design, and details that were not recorded.

## Comparison scope

| Model | Serving path | Reasoning settings |
|---|---|---|
| `gpt-4.1-2025-04-14` | OpenAI first-party Responses API | Omitted |
| `openai.gpt-5.6-luna` | Amazon Bedrock Mantle Responses API, `us-east-1` | `none`, `low`, `high` |
| `openai.gpt-5.6-terra` | Amazon Bedrock Mantle Responses API, `us-east-1` | `none`, `low`, `high` |
| `openai.gpt-5.6-sol` | Amazon Bedrock Mantle Responses API, `us-east-1` | `none`, `low`, `high` |

GPT-4.1 versus GPT-5.6 is a **cross-platform comparison**: latency and cost describe the configured serving paths. GPT-5.6 low/high runs form a separate reasoning-effort comparison; none versus low/high is descriptive because run timing, some output ceilings, and overlapping suite execution differ. GPT-4.1's same baseline is repeated for reference in the reasoning tables. Recorded end-to-end latency measures the final retained attempt, including terminal failures and local input rejections, and excludes earlier retries and backoff; it is not full application latency.

The workloads are synthetic classification/routing and invoice extraction, Banking77, CORD image and supplied OCR, ExtractBench, GSM8K, MATH-500, AIME, GPQA Diamond, MMLU-Pro, and HumanEval. The report provides the evaluated sample size for each workload. Results support workload-specific comparisons and require validation against deployment-specific acceptance criteria.

## Normalization and score interpretation

Synthetic invoice extraction shows **strict document accuracy** and **address-normalized document accuracy (post hoc; exploratory)** in every headline table. The saved JSON/CSV retains its original field names: normalized is `primary`, strict is `secondary`. These names do not mean normalization was specified before the run or validated on fresh cases. Existing strict field matches remain valid. For mismatched `supplier.address` and `customer.address` strings, the additional rule replaces LF newline with comma-space on both sides, collapses whitespace, and requires exact equality. It preserves other characters, digits, capitalization, punctuation, and component order. Original JSON/schema validation and every other field's grading rule still apply.

The report includes a worked example and failure cases. Strict scoring uses the corrected v1.0.0 references and the original whitespace, currency-case, and cent-precision money rules. This metric revision is separate from the earlier compact-layout reference and math-grader corrections. It rescored the same saved responses; it does not represent a model improvement. Validate the normalization rule on fresh cases before making prospective performance claims.

Quality metrics retain their own definitions and denominators. An aggregate F1 score is not a count of successful documents, so no cost-per-correct value is inferred from F1. Recorded prices and timings describe these runs rather than current price quotations or guaranteed service latency.

## Statistical and operational interpretation

The report reproduces existing paired comparisons for **synthetic invoices, MATH-500, and AIME**; the other nine workloads have no paired comparison in the saved scorecard. It labels unadjusted confidence intervals separately from Holm-adjusted p-values. Adjustment applies within each stored three-model comparison family, not across the full benchmark matrix. Neither a non-significant result nor a zero-width bootstrap interval demonstrates equivalence in future cases. Post hoc normalized invoice comparisons remain exploratory even when their adjusted test detects a difference.

Overall API completion is weighted by the number of cases, with Banking77 contributing 64.22% per configuration. The completion table therefore lists every workload/configuration below full completion, including local input rejections. Recorded attempt events include recovered retries and are distinct from final uncompleted cases.

When usage metadata is missing, spend is displayed from its recorded lower bound to its conservative upper estimate, with the missing-attempt count. This accounting range is not a statistical confidence interval. All exact values remain available in the preserved CSV and JSON.

## Reproduce the report

Python 3.10+ and the standard library are sufficient. From the repository root:

```sh
python3 comparisons/gpt-4.1-vs-gpt-5.6/render_report.py --verify
```

This checks the public manifest against every shipped package file, verifies the complete 120-cell matrix, and regenerates RESULTS.md and CSV in memory for byte-for-byte comparison. It verifies stored chart-file hashes without rerendering charts. To write the same reports again:

```sh
python3 comparisons/gpt-4.1-vs-gpt-5.6/render_report.py --write
```

Run the reporting regression checks with the same standard-library environment:

```sh
python3 -B comparisons/gpt-4.1-vs-gpt-5.6/test_render_report.py
```

Chart-data regression checks also use the standard library. [Chart reproduction instructions](CHARTS.md#reproduce-the-charts) describe those checks and the optional Matplotlib environment needed to regenerate figures.

**Reproduction boundary:** this is a reporting package built from saved aggregate results. It does not rerun inference or regrade individual responses. Raw per-attempt logs, dataset examples, and the original execution/rescoring harness are outside this export. The public manifest lists package-relative files and hashes; the full source-to-release mapping remains in the internal review record. File integrity and report reproduction do not establish independent replication of inference or grading, or publication approval.

## Files

| File | Purpose |
|---|---|
| [RESULTS.md](RESULTS.md) | Complete human-readable scorecard, operating summary, and normalization explanation |
| [CHARTS.md](CHARTS.md) | Visual guide with an operating summary, operational and quality-versus-cost previews, and chart definitions |
| [CHARTS.html](CHARTS.html) | Self-contained offline explorer with 36 workload/reasoning views, operational and quality-versus-cost figures, and SVG downloads; download and open locally |
| [charts/operating-summary.png](charts/operating-summary.png), [SVG](charts/operating-summary.svg) | Completion and recorded spend across all ten configurations |
| [charts/workload-example.png](charts/workload-example.png) | Classification-routing preview for the original comparison |
| [charts/quality-cost-example.png](charts/quality-cost-example.png), [SVG](charts/quality-cost-example.svg) | Classification-routing quality versus recorded spend, with completion counts and rates |
| [charts/chart-data.json](charts/chart-data.json) | All 120 chart data records, including timing populations and cost bounds |
| [render_charts.py](render_charts.py), [chart-template.html](chart-template.html) | Chart renderer and offline explorer template; regeneration uses Matplotlib |
| [test_render_charts.py](test_render_charts.py) | Standard-library checks for chart-data mapping and coverage |
| [results/2026-08-30/scorecard.json](results/2026-08-30/scorecard.json) | v1.1.0 measurements with public-facing metadata; all 120 result cells, operational records, paired statistics, and retained v1.0.0 results are preserved |
| [results/2026-08-30/scorecard.csv](results/2026-08-30/scorecard.csv) | All 120 quality and operational rows |
| [render_report.py](render_report.py) | Deterministic, offline report generation and verification |
| [test_render_report.py](test_render_report.py) | Regression checks for metric selection, statistical scope, operational uncertainty, reproduction, and integrity rejection |
| [METHODOLOGY.md](METHODOLOGY.md) | Executed settings, timing definitions, dataset selection, and limitations |
| [evidence/run-settings.json](evidence/run-settings.json) | Structured execution settings and recorded/unrecorded distinctions |
| [evidence/dataset-selection.json](evidence/dataset-selection.json) | Pinned public sources, evaluated subsets, selection rules, and permitted case identifiers |
| [evidence/comparison-evidence-map.json](evidence/comparison-evidence-map.json) | Public benchmark references, sample sizes, model settings, run dates, and comparison boundaries |
| [evidence/scoring-policy-v1.0.0.json](evidence/scoring-policy-v1.0.0.json) | Earlier reference/math correction policy |
| [evidence/scoring-policy-v1.1.0.json](evidence/scoring-policy-v1.1.0.json) | Preserved post hoc normalization policy and strict scoring rules |
| [evidence/publication.json](evidence/publication.json) | Publication-candidate version, scope, and hashes of shipped files |
| [BENCHMARK_SOURCES.md](BENCHMARK_SOURCES.md) | Benchmark references, attribution context, and rights-review boundary |

No inference calls are made by this package. Dataset licenses continue to govern the original datasets; dataset examples are not redistributed here. This candidate requires applicable release approval; the manifest and offline verifier do not grant that approval.
