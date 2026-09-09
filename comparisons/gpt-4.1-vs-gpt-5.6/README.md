# GPT-4.1 versus GPT-5.6: benchmark results

This 1.0.0-rc2 publication candidate presents the saved comparison across **12 workloads, 10 model/reasoning conditions, and 47,960 planned observations**. Full-stage runs are dated August 26–30, 2026. The latest scorecard includes the v1.0.0 grading corrections and the v1.1.0 address-normalization metric revision.

Start with **[RESULTS.md](RESULTS.md)** for all quality, completion, recorded cost, and p50/p95 latency results. The **[120-row CSV](results/2026-08-30/scorecard.csv)** includes token usage and cost per correct result for count-based metrics; F1-only workloads leave cost per correct blank.

## Comparison scope

| Model | Serving path | Reasoning settings |
|---|---|---|
| `gpt-4.1-2025-04-14` | OpenAI first-party Responses API | Omitted |
| `openai.gpt-5.6-luna` | Amazon Bedrock Mantle Responses API, `us-east-1` | `none`, `low`, `high` |
| `openai.gpt-5.6-terra` | Amazon Bedrock Mantle Responses API, `us-east-1` | `none`, `low`, `high` |
| `openai.gpt-5.6-sol` | Amazon Bedrock Mantle Responses API, `us-east-1` | `none`, `low`, `high` |

GPT-4.1 versus GPT-5.6 is a **cross-platform comparison**: latency and cost describe the configured serving paths. GPT-5.6 low/high runs form a separate reasoning-effort comparison; none versus low/high is descriptive because run timing and some output ceilings differ. GPT-4.1's same baseline is repeated for reference in the reasoning tables.

The workloads are synthetic classification/routing and invoice extraction, Banking77, CORD image and supplied OCR, ExtractBench, GSM8K, MATH-500, AIME, GPQA Diamond, MMLU-Pro, and HumanEval. The report provides the evaluated sample size for each workload. Results support workload-specific comparisons and require validation against deployment-specific acceptance criteria.

## Normalization and score interpretation

Synthetic invoice extraction uses **address-normalized document accuracy** as primary and retains **strict document accuracy** as secondary. Existing strict field matches remain valid. For mismatched `supplier.address` and `customer.address` strings, the additional rule replaces LF newline with comma-space on both sides, collapses whitespace, and requires exact equality. It preserves other characters, digits, capitalization, punctuation, and component order. Original JSON/schema validation and every other field's grading rule still apply.

The report includes a worked example and failure cases. Strict scoring uses the corrected v1.0.0 references and the original whitespace, currency-case, and cent-precision money rules. This metric revision is separate from the earlier compact-layout reference and math-grader corrections. It rescored the same saved responses; it does not represent a model improvement.

Quality metrics retain their own definitions and denominators. An aggregate F1 score is not a count of successful documents, so no cost-per-correct value is inferred from F1. Recorded prices and timings describe these runs rather than current price quotations or guaranteed service latency.

## Reproduce the report

Python 3.10+ and the standard library are sufficient. From the repository root:

```sh
python3 comparisons/gpt-4.1-vs-gpt-5.6/render_report.py --verify
```

This checks the public manifest against every shipped package file, verifies the complete 120-cell matrix, and regenerates Markdown and CSV in memory for byte-for-byte comparison. To write the same reports again:

```sh
python3 comparisons/gpt-4.1-vs-gpt-5.6/render_report.py --write
```

**Reproduction boundary:** this is a reporting package built from saved aggregate results. It does not rerun inference or regrade individual responses. Raw per-attempt logs, dataset examples, and the original execution/rescoring harness are outside this export. The public manifest lists package-relative files and hashes; the full source-to-release mapping remains in the internal review record. File integrity and report reproduction do not establish independent replication of inference or grading, or publication approval.

## Files

| File | Purpose |
|---|---|
| [RESULTS.md](RESULTS.md) | Complete human-readable scorecard, operating summary, and normalization explanation |
| [results/2026-08-30/scorecard.json](results/2026-08-30/scorecard.json) | v1.1.0 measurements with public-facing metadata; all 120 result cells, operational records, paired statistics, and retained v1.0.0 results are preserved |
| [results/2026-08-30/scorecard.csv](results/2026-08-30/scorecard.csv) | All 120 quality and operational rows |
| [render_report.py](render_report.py) | Deterministic, offline report generation and verification |
| [evidence/comparison-evidence-map.json](evidence/comparison-evidence-map.json) | Public benchmark references, sample sizes, model settings, run dates, and comparison boundaries |
| [evidence/scoring-policy-v1.0.0.json](evidence/scoring-policy-v1.0.0.json) | Earlier reference/math correction policy |
| [evidence/scoring-policy-v1.1.0.json](evidence/scoring-policy-v1.1.0.json) | Address-normalized primary and strict secondary metric policy |
| [evidence/publication.json](evidence/publication.json) | Publication-candidate version, scope, and hashes of shipped files |
| [BENCHMARK_SOURCES.md](BENCHMARK_SOURCES.md) | Benchmark references, attribution context, and rights-review boundary |

No inference calls are made by this package. Dataset licenses continue to govern the original datasets; dataset examples are not redistributed here. This candidate contains unpublished benchmark results and methodology requiring release approval; the manifest and offline verifier do not grant that approval.
