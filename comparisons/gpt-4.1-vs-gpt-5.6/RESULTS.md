# Descriptive GPT-4.1 and GPT-5.6 benchmark scorecard

**Comparison:** GPT-4.1 through the OpenAI API and GPT-5.6 Luna, Terra, and Sol through Amazon Bedrock across 12 workloads, with a separate GPT-5.6 reasoning-effort sensitivity view.

**Scorecard version:** v1.1.0. **Coverage:** all 12 benchmarks, 10 model/reasoning conditions, 120 result cells, and 47,960 planned observations.

This is a complete presentation of the saved v1.1.0 scorecard. Synthetic invoice extraction uses address-normalized document accuracy; corrected strict accuracy is retained below. MATH-500 includes the v1.0.0 grader corrections. The other benchmark scores, recorded spend, and latency remain unchanged.

All quality values are shown on a 0–100 scale. F1 is explicitly labeled and should not be read as document accuracy. N is the planned observation count per model/condition; the public benchmark names refer to the evaluated subsets shown here.

## Interpretation boundary

- GPT-4.1 versus GPT-5.6 is a descriptive, cross-platform comparison across the configured API paths; it is not a controlled causal estimate of a model migration.
- GPT-5.6 none versus low/high is descriptive because the saved runs differ in timing and some output ceilings. Reasoning-effort results should be read as workload-specific sensitivity evidence, not a universal reasoning effect.
- The scorecard does not establish migration readiness or a universal model winner. Quality, completion, latency, and cost must be evaluated together against customer-specific acceptance criteria.

## Executive operating summary

Completion means an API-completed response and is distinct from answer quality. Recorded spend is the metered lower bound across all attempts; a range is shown when missing usage required a conservative upper bound. No quality metric is aggregated across unlike workloads.

| Model | Reasoning | Planned | API completed | Completion gap | Completion rate | Recorded spend (USD) |
|---|---|---:|---:|---:|---:|---:|
| GPT-4.1 | omitted | 4796 | 4794 | 2 | 99.96% | $37.874710 |
| Luna | none | 4796 | 4788 | 8 | 99.83% | $3.208804 |
| Luna | low | 4796 | 4794 | 2 | 99.96% | $3.530319 |
| Luna | high | 4796 | 4741 | 55 | 98.85% | $7.913579 |
| Terra | none | 4796 | 4790 | 6 | 99.87% | $32.757704 |
| Terra | low | 4796 | 4793 | 3 | 99.94% | $38.506107 |
| Terra | high | 4796 | 4784 | 12 | 99.75% | $52.972756 |
| Sol | none | 4796 | 4767 | 29 | 99.40% | $62.763572 |
| Sol | low | 4796 | 4786 | 10 | 99.79% | $73.462579 |
| Sol | high | 4796 | 4758 | 38 | 99.21% | $104.528715–$141.891403 |

## Workload quality and recorded cost per correct

For accuracy, exact-match, and task-success metrics, recorded cost per correct is metered spend divided by the number correct out of the full planned denominator. F1-only workloads report `N/A`: an aggregate F1 score is not a count of correct documents or entities and cannot support a defensible cost-per-correct calculation from this scorecard.

## GPT-5.6 reasoning: none

| Benchmark | Metric | N | GPT-4.1 | GPT-4.1 $/correct | Luna | Luna $/correct | Terra | Terra $/correct | Sol | Sol $/correct |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | Task accuracy | 192 | 98.44% | $0.000785 | 100.00% | $0.000090 | 100.00% | $0.000904 | 99.48% | $0.001625 |
| Synthetic invoice extraction | Address-normalized document accuracy | 192 | 92.71% | $0.005490 | 91.67% | $0.000680 | 91.67% | $0.006802 | 97.92% | $0.010317 |
| Banking77 | Exact-match accuracy | 3080 | 78.51% | $0.001395 | 84.25% | $0.000150 | 81.66% | $0.001549 | 85.97% | $0.002637 |
| CORD OCR | Entity F1 | 100 | 46.89% | N/A | 65.13% | N/A | 80.55% | N/A | 88.61% | N/A |
| CORD image | Entity F1 | 100 | 68.63% | N/A | 56.24% | N/A | 73.33% | N/A | 81.07% | N/A |
| ExtractBench | Mean unified-value F1 | 370 | 78.78% | N/A | 74.87% | N/A | 77.69% | N/A | 81.10% | N/A |
| GSM8K | Task accuracy | 100 | 94.00% | $0.001961 | 98.00% | $0.000167 | 95.00% | $0.001502 | 96.00% | $0.002364 |
| MATH-500 | Task accuracy | 100 | 91.00% | $0.007349 | 91.00% | $0.000307 | 92.00% | $0.003131 | 95.00% | $0.004262 |
| AIME | Task accuracy | 60 | 38.33% | $0.075758 | 46.67% | $0.001608 | 56.67% | $0.017177 | 68.33% | $0.020874 |
| GPQA | Task accuracy | 198 | 69.70% | $0.008823 | 47.47% | $0.000131 | 54.55% | $0.001158 | 63.64% | $0.001837 |
| MMLU-Pro | Task accuracy | 140 | 82.86% | $0.003140 | 57.14% | $0.000101 | 67.14% | $0.000864 | 80.71% | $0.001293 |
| HumanEval | Task success | 164 | 96.34% | $0.001754 | 94.51% | $0.000236 | 96.34% | $0.002132 | 98.17% | $0.002747 |

## GPT-5.6 reasoning: low

| Benchmark | Metric | N | GPT-4.1 | GPT-4.1 $/correct | Luna | Luna $/correct | Terra | Terra $/correct | Sol | Sol $/correct |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | Task accuracy | 192 | 98.44% | $0.000785 | 100.00% | $0.000095 | 100.00% | $0.000978 | 100.00% | $0.001822 |
| Synthetic invoice extraction | Address-normalized document accuracy | 192 | 92.71% | $0.005490 | 89.06% | $0.000829 | 90.10% | $0.007236 | 99.48% | $0.011384 |
| Banking77 | Exact-match accuracy | 3080 | 78.51% | $0.001395 | 83.70% | $0.000167 | 84.16% | $0.001907 | 85.84% | $0.003160 |
| CORD OCR | Entity F1 | 100 | 46.89% | N/A | 79.19% | N/A | 84.20% | N/A | 89.35% | N/A |
| CORD image | Entity F1 | 100 | 68.63% | N/A | 65.16% | N/A | 74.98% | N/A | 81.71% | N/A |
| ExtractBench | Mean unified-value F1 | 370 | 78.78% | N/A | 75.29% | N/A | 78.67% | N/A | 83.39% | N/A |
| GSM8K | Task accuracy | 100 | 94.00% | $0.001961 | 97.00% | $0.000236 | 96.00% | $0.002027 | 95.00% | $0.003570 |
| MATH-500 | Task accuracy | 100 | 91.00% | $0.007349 | 97.00% | $0.000630 | 99.00% | $0.005331 | 100.00% | $0.007379 |
| AIME | Task accuracy | 60 | 38.33% | $0.075758 | 73.33% | $0.002948 | 91.67% | $0.024530 | 98.33% | $0.029398 |
| GPQA | Task accuracy | 198 | 69.70% | $0.008823 | 85.35% | $0.000773 | 83.84% | $0.006712 | 89.90% | $0.008181 |
| MMLU-Pro | Task accuracy | 140 | 82.86% | $0.003140 | 81.43% | $0.000308 | 85.00% | $0.002583 | 87.14% | $0.003124 |
| HumanEval | Task success | 164 | 96.34% | $0.001754 | 96.95% | $0.000378 | 98.17% | $0.002418 | 99.39% | $0.004254 |

## GPT-5.6 reasoning: high

| Benchmark | Metric | N | GPT-4.1 | GPT-4.1 $/correct | Luna | Luna $/correct | Terra | Terra $/correct | Sol | Sol $/correct |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | Task accuracy | 192 | 98.44% | $0.000785 | 100.00% | $0.000120 | 99.48% | $0.001058 | 99.48% | $0.001844 |
| Synthetic invoice extraction | Address-normalized document accuracy | 192 | 92.71% | $0.005490 | 88.54% | $0.001121 | 95.31% | $0.007697 | 98.96% | $0.012110 |
| Banking77 | Exact-match accuracy | 3080 | 78.51% | $0.001395 | 85.32% | $0.000220 | 84.55% | $0.002182 | 86.30% | $0.003605 |
| CORD OCR | Entity F1 | 100 | 46.89% | N/A | 84.10% | N/A | 85.04% | N/A | 89.05% | N/A |
| CORD image | Entity F1 | 100 | 68.63% | N/A | 74.39% | N/A | 78.04% | N/A | 82.63% | N/A |
| ExtractBench | Mean unified-value F1 | 370 | 78.78% | N/A | 74.00% | N/A | 78.64% | N/A | 80.06% | N/A |
| GSM8K | Task accuracy | 100 | 94.00% | $0.001961 | 96.00% | $0.000300 | 97.00% | $0.002055 | 95.00% | $0.003918 |
| MATH-500 | Task accuracy | 100 | 91.00% | $0.007349 | 98.00% | $0.000868 | 99.00% | $0.005878 | 100.00% | $0.009638 |
| AIME | Task accuracy | 60 | 38.33% | $0.075758 | 90.00% | $0.005874 | 98.33% | $0.031049 | 100.00% | $0.050505 |
| GPQA | Task accuracy | 198 | 69.70% | $0.008823 | 87.37% | $0.003718 | 88.38% | $0.014525 | 92.42% | $0.020109 |
| MMLU-Pro | Task accuracy | 140 | 82.86% | $0.003140 | 82.86% | $0.000790 | 85.00% | $0.002972 | 86.43% | $0.005185 |
| HumanEval | Task success | 164 | 96.34% | $0.001754 | 99.39% | $0.000510 | 98.78% | $0.002834 | 99.39% | $0.005134 |

## Invoice extraction: primary and secondary

### What normalization means

Address normalization allows the same address information to be returned with a line break or a comma separator. It applies only to the synthetic invoice benchmark's `supplier.address` and `customer.address` fields, using the same rule for GPT-4.1 and every GPT-5.6 model/reasoning setting.

The exact comparison is:

1. Keep every field match already accepted by the strict grader.
2. For a mismatched supplier or customer address, replace each LF newline (`\n`) with comma-space (`, `) in both the expected address and the model's address. Trim leading/trailing whitespace and collapse whitespace runs to a single space.
3. Accept that address only if the resulting strings match exactly. All remaining characters, digits, punctuation, capitalization, and component order must match.

For example, these two addresses are equivalent under the primary metric:

```text
Expected address:
400 Trial Plaza
Seattle, WA 98101

Model's address:
400 Trial Plaza, Seattle, WA 98101
```

This example passes address-normalized comparison and fails strict comparison because of the added comma. Changing `400` to `401`, changing postal code `98101` to `98102`, or omitting an apartment present in the reference still fails. Abbreviation expansion, fuzzy matching, case folding, and deletion of other punctuation are not added.

The original response must first pass JSON parsing and schema validation. A complete document passes only when the API response is completed and every field passes its applicable comparison. Missing fields, invalid types, wrong dates, and incorrect totals are still checked by the existing rules. All 192 planned documents remain in each condition's denominator.

**Primary metric:** address-normalized document accuracy. **Secondary metric:** strict document accuracy using the v1.0.0 corrected references. Strict scoring already collapses whitespace, normalizes currency case, and compares money at cent precision; it does not require byte-for-byte text equality.

This formatting rule is separate from the earlier correction of compact-layout invoice references and the MATH-500 grader fixes. It is an offline rescore of saved responses: the source documents and model outputs are preserved, and no new model calls were made.

### Results under both metrics

| Model | Reasoning | Normalized accuracy | Normalized correct / planned | Recorded $ / normalized correct | Strict accuracy | Strict correct / planned | Recorded $ / strict correct |
|---|---|---:|---:|---:|---:|---:|---:|
| GPT-4.1 | omitted | 92.71% | 178/192 | $0.005490 | 92.71% | 178/192 | $0.005490 |
| Luna | none | 91.67% | 176/192 | $0.000680 | 84.38% | 162/192 | $0.000739 |
| Luna | low | 89.06% | 171/192 | $0.000829 | 73.96% | 142/192 | $0.000999 |
| Luna | high | 88.54% | 170/192 | $0.001121 | 76.04% | 146/192 | $0.001305 |
| Terra | none | 91.67% | 176/192 | $0.006802 | 91.67% | 176/192 | $0.006802 |
| Terra | low | 90.10% | 173/192 | $0.007236 | 89.58% | 172/192 | $0.007278 |
| Terra | high | 95.31% | 183/192 | $0.007697 | 91.15% | 175/192 | $0.008049 |
| Sol | none | 97.92% | 188/192 | $0.010317 | 95.83% | 184/192 | $0.010542 |
| Sol | low | 99.48% | 191/192 | $0.011384 | 92.19% | 177/192 | $0.012284 |
| Sol | high | 98.96% | 190/192 | $0.012110 | 84.38% | 162/192 | $0.014203 |

## Recorded cost and latency for every result

Costs are recorded metered totals across all attempts in each cell, not current price quotations or cost per request. Completion is API completion, separate from answer quality. Latencies below are seconds; TTFT is time to first token. All figures are carried directly from the saved operational records.

The accompanying CSV also includes token counts, retries, capability failures, cost bounds, and cost-per-correct values for count-based quality metrics. F1-only cost-per-correct values remain blank; no values are inferred.

### GPT-4.1 / omitted

| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |
|---|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | 0.148310 | 0.506 | 1.493 | 0.667 | 1.823 |
| Synthetic invoice extraction | 192/192 | 0.977292 | 0.577 | 1.718 | 3.299 | 6.734 |
| Banking77 | 3080/3080 | 3.372096 | 0.487 | 1.034 | 0.647 | 1.278 |
| CORD OCR | 100/100 | 0.696184 | 0.649 | 1.225 | 2.204 | 4.669 |
| CORD image | 100/100 | 0.576156 | 1.410 | 2.620 | 3.247 | 6.175 |
| ExtractBench | 369/370 | 27.650146 | 3.227 | 17.665 | 12.233 | 57.251 |
| GSM8K | 100/100 | 0.184344 | 0.538 | 1.029 | 1.730 | 2.968 |
| MATH-500 | 100/100 | 0.668780 | 0.509 | 1.084 | 3.395 | 14.231 |
| AIME | 60/60 | 1.742436 | 0.536 | 0.810 | 19.323 | 43.161 |
| GPQA | 197/198 | 1.217596 | 0.533 | 1.127 | 4.520 | 11.415 |
| MMLU-Pro | 140/140 | 0.364266 | 0.524 | 1.024 | 1.924 | 5.962 |
| HumanEval | 164/164 | 0.277104 | 0.502 | 1.040 | 1.429 | 2.824 |

### Luna / none

| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |
|---|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | 0.017356 | 0.289 | 0.417 | 0.292 | 0.419 |
| Synthetic invoice extraction | 192/192 | 0.119680 | 0.458 | 0.601 | 1.303 | 1.650 |
| Banking77 | 3080/3080 | 0.389471 | 0.296 | 0.606 | 0.299 | 0.613 |
| CORD OCR | 100/100 | 0.090504 | 0.459 | 0.678 | 0.948 | 1.928 |
| CORD image | 100/100 | 0.093238 | 1.276 | 3.686 | 1.787 | 4.085 |
| ExtractBench | 362/370 | 2.352190 | 1.458 | 7.278 | 4.915 | 38.515 |
| GSM8K | 100/100 | 0.016367 | 0.451 | 0.531 | 0.668 | 1.006 |
| MATH-500 | 100/100 | 0.027911 | 0.447 | 0.669 | 0.907 | 2.031 |
| AIME | 60/60 | 0.045030 | 0.453 | 0.508 | 2.553 | 3.729 |
| GPQA | 198/198 | 0.012320 | 0.295 | 0.614 | 0.298 | 0.615 |
| MMLU-Pro | 140/140 | 0.008112 | 0.295 | 0.447 | 0.299 | 0.451 |
| HumanEval | 164/164 | 0.036627 | 0.445 | 0.510 | 0.699 | 1.185 |

### Luna / low

| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |
|---|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | 0.018277 | 0.333 | 0.660 | 0.338 | 0.687 |
| Synthetic invoice extraction | 192/192 | 0.141794 | 1.069 | 2.975 | 1.849 | 3.577 |
| Banking77 | 3080/3080 | 0.429267 | 0.327 | 1.242 | 0.332 | 1.252 |
| CORD OCR | 100/100 | 0.126628 | 2.253 | 3.959 | 2.946 | 4.709 |
| CORD image | 100/100 | 0.080550 | 4.109 | 18.781 | 4.754 | 19.171 |
| ExtractBench | 368/370 | 2.294218 | 4.681 | 9.032 | 8.205 | 21.636 |
| GSM8K | 100/100 | 0.022849 | 0.713 | 1.286 | 1.042 | 1.690 |
| MATH-500 | 100/100 | 0.061113 | 1.096 | 8.080 | 1.545 | 9.304 |
| AIME | 60/60 | 0.129701 | 6.037 | 19.076 | 8.094 | 21.101 |
| GPQA | 198/198 | 0.130707 | 1.882 | 11.563 | 1.883 | 11.582 |
| MMLU-Pro | 140/140 | 0.035123 | 0.901 | 3.972 | 0.901 | 3.975 |
| HumanEval | 164/164 | 0.060093 | 0.876 | 3.890 | 1.200 | 4.913 |

### Luna / high

| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |
|---|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | 0.023095 | 0.517 | 0.884 | 0.522 | 0.884 |
| Synthetic invoice extraction | 192/192 | 0.190570 | 1.698 | 6.388 | 2.581 | 7.272 |
| Banking77 | 3080/3080 | 0.577992 | 0.334 | 2.134 | 0.338 | 2.157 |
| CORD OCR | 100/100 | 0.394426 | 11.585 | 33.902 | 12.158 | 34.748 |
| CORD image | 100/100 | 0.299481 | 8.378 | 38.478 | 9.137 | 38.996 |
| ExtractBench | 326/370 | 5.179149 | 29.949 | 63.684 | 37.400 | 76.296 |
| GSM8K | 100/100 | 0.028756 | 0.878 | 1.932 | 1.094 | 2.260 |
| MATH-500 | 100/100 | 0.085035 | 1.615 | 14.030 | 1.948 | 14.475 |
| AIME | 59/60 | 0.317173 | 12.391 | 69.877 | 14.172 | 72.821 |
| GPQA | 188/198 | 0.643222 | 3.158 | 52.810 | 3.461 | 87.616 |
| MMLU-Pro | 140/140 | 0.091627 | 1.061 | 9.378 | 1.063 | 9.382 |
| HumanEval | 164/164 | 0.083053 | 1.299 | 5.812 | 1.632 | 5.947 |

### Terra / none

| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |
|---|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | 0.173560 | 0.350 | 0.511 | 0.353 | 0.514 |
| Synthetic invoice extraction | 192/192 | 1.197166 | 0.565 | 0.859 | 1.793 | 2.347 |
| Banking77 | 3080/3080 | 3.894832 | 0.360 | 0.633 | 0.367 | 0.639 |
| CORD OCR | 100/100 | 0.905934 | 0.566 | 0.956 | 1.258 | 2.575 |
| CORD image | 100/100 | 0.935596 | 1.390 | 3.631 | 2.217 | 4.758 |
| ExtractBench | 364/370 | 24.092598 | 1.586 | 8.127 | 8.134 | 55.355 |
| GSM8K | 100/100 | 0.142732 | 0.376 | 0.654 | 0.899 | 1.454 |
| MATH-500 | 100/100 | 0.288086 | 0.539 | 0.796 | 1.310 | 4.345 |
| AIME | 60/60 | 0.584003 | 0.547 | 0.799 | 4.957 | 11.347 |
| GPQA | 198/198 | 0.125104 | 0.371 | 0.694 | 0.381 | 0.708 |
| MMLU-Pro | 140/140 | 0.081261 | 0.362 | 1.256 | 0.371 | 1.259 |
| HumanEval | 164/164 | 0.336833 | 0.428 | 0.927 | 0.976 | 1.930 |

### Terra / low

| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |
|---|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | 0.187843 | 0.393 | 0.757 | 0.396 | 0.760 |
| Synthetic invoice extraction | 192/192 | 1.251840 | 0.599 | 1.982 | 1.798 | 3.608 |
| Banking77 | 3080/3080 | 4.944100 | 0.415 | 2.007 | 0.424 | 2.013 |
| CORD OCR | 100/100 | 1.055226 | 0.609 | 5.123 | 1.484 | 6.308 |
| CORD image | 100/100 | 0.744114 | 3.955 | 14.902 | 4.816 | 15.319 |
| ExtractBench | 367/370 | 26.440579 | 7.657 | 16.745 | 13.650 | 46.107 |
| GSM8K | 100/100 | 0.194608 | 0.784 | 1.268 | 1.129 | 1.983 |
| MATH-500 | 100/100 | 0.527811 | 1.309 | 13.813 | 1.890 | 14.565 |
| AIME | 60/60 | 1.349141 | 10.350 | 36.451 | 13.754 | 39.890 |
| GPQA | 198/198 | 1.114153 | 1.963 | 21.238 | 1.967 | 21.239 |
| MMLU-Pro | 140/140 | 0.307338 | 0.938 | 4.693 | 0.939 | 4.697 |
| HumanEval | 164/164 | 0.389356 | 0.585 | 2.384 | 0.989 | 2.968 |

### Terra / high

| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |
|---|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | 0.202165 | 0.446 | 0.751 | 0.449 | 0.754 |
| Synthetic invoice extraction | 192/192 | 1.408617 | 1.177 | 2.785 | 2.177 | 4.282 |
| Banking77 | 3080/3080 | 5.683049 | 0.475 | 2.161 | 0.479 | 2.165 |
| CORD OCR | 100/100 | 1.386163 | 3.574 | 8.647 | 4.179 | 9.313 |
| CORD image | 100/100 | 0.979157 | 4.432 | 10.030 | 5.093 | 11.559 |
| ExtractBench | 358/370 | 37.345983 | 19.100 | 45.685 | 27.345 | 85.413 |
| GSM8K | 100/100 | 0.199294 | 0.796 | 1.315 | 1.140 | 1.982 |
| MATH-500 | 100/100 | 0.581878 | 1.223 | 9.990 | 2.007 | 11.644 |
| AIME | 60/60 | 1.831918 | 10.638 | 70.220 | 13.689 | 74.340 |
| GPQA | 198/198 | 2.541812 | 2.528 | 59.405 | 2.531 | 59.485 |
| MMLU-Pro | 140/140 | 0.353643 | 0.866 | 6.640 | 0.867 | 6.642 |
| HumanEval | 164/164 | 0.459078 | 0.605 | 4.528 | 1.096 | 5.054 |

### Sol / none

| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |
|---|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | 0.310316 | 0.458 | 1.519 | 0.461 | 1.525 |
| Synthetic invoice extraction | 192/192 | 1.939689 | 0.598 | 1.523 | 2.178 | 3.345 |
| Banking77 | 3080/3080 | 6.983116 | 0.477 | 1.566 | 0.484 | 1.580 |
| CORD OCR | 100/100 | 1.554440 | 0.728 | 1.962 | 1.675 | 3.561 |
| CORD image | 100/100 | 1.607168 | 1.869 | 4.843 | 2.900 | 7.294 |
| ExtractBench | 341/370 | 48.061522 | 2.009 | 9.180 | 8.864 | 103.346 |
| GSM8K | 100/100 | 0.226932 | 0.586 | 1.476 | 1.104 | 1.814 |
| MATH-500 | 100/100 | 0.404844 | 0.587 | 1.469 | 1.509 | 4.775 |
| AIME | 60/60 | 0.855848 | 0.583 | 2.607 | 5.789 | 14.022 |
| GPQA | 198/198 | 0.231409 | 0.497 | 1.872 | 0.505 | 2.057 |
| MMLU-Pro | 140/140 | 0.146068 | 0.463 | 1.449 | 0.464 | 1.472 |
| HumanEval | 164/164 | 0.442220 | 0.578 | 1.444 | 1.020 | 1.915 |

### Sol / low

| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |
|---|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | 0.349796 | 0.555 | 2.373 | 0.557 | 2.379 |
| Synthetic invoice extraction | 192/192 | 2.174336 | 0.837 | 2.920 | 2.424 | 4.625 |
| Banking77 | 3080/3080 | 8.355486 | 0.574 | 2.590 | 0.580 | 2.606 |
| CORD OCR | 100/100 | 1.918070 | 1.839 | 4.722 | 2.732 | 6.500 |
| CORD image | 100/100 | 1.128814 | 4.184 | 10.755 | 5.105 | 12.019 |
| ExtractBench | 360/370 | 54.193861 | 8.638 | 17.848 | 15.735 | 83.855 |
| GSM8K | 100/100 | 0.339187 | 1.240 | 3.280 | 1.792 | 4.024 |
| MATH-500 | 100/100 | 0.737884 | 1.837 | 11.701 | 2.616 | 12.776 |
| AIME | 60/60 | 1.734467 | 9.916 | 32.677 | 13.078 | 38.230 |
| GPQA | 198/198 | 1.456168 | 3.721 | 16.202 | 3.739 | 16.206 |
| MMLU-Pro | 140/140 | 0.381159 | 1.298 | 6.158 | 1.305 | 6.161 |
| HumanEval | 164/164 | 0.693352 | 1.739 | 7.413 | 2.177 | 7.821 |

### Sol / high

| Benchmark | Completed / planned | Recorded USD | TTFT p50 (s) | TTFT p95 (s) | End-to-end p50 (s) | End-to-end p95 (s) |
|---|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | 0.352128 | 0.627 | 2.227 | 0.630 | 2.230 |
| Synthetic invoice extraction | 192/192 | 2.300880 | 0.876 | 3.406 | 2.507 | 4.983 |
| Banking77 | 3080/3080 | 9.581062 | 0.683 | 3.793 | 0.694 | 3.829 |
| CORD OCR | 100/100 | 2.678698 | 4.546 | 13.540 | 5.249 | 14.554 |
| CORD image | 100/100 | 2.005276 | 6.355 | 20.120 | 7.122 | 20.860 |
| ExtractBench | 332/370 | 78.100049 | 29.268 | 79.309 | 38.213 | 120.693 |
| GSM8K | 100/100 | 0.372253 | 1.175 | 4.746 | 1.645 | 4.881 |
| MATH-500 | 100/100 | 0.963758 | 1.920 | 17.551 | 2.639 | 18.283 |
| AIME | 60/60 | 3.030289 | 14.021 | 77.255 | 17.165 | 83.647 |
| GPQA | 198/198 | 3.679994 | 7.523 | 43.249 | 7.536 | 43.254 |
| MMLU-Pro | 140/140 | 0.627427 | 1.372 | 6.999 | 1.377 | 7.007 |
| HumanEval | 164/164 | 0.836902 | 6.144 | 17.178 | 6.712 | 17.493 |

## Source and detailed evidence

- [All 120 quality and operational rows](results/2026-08-30/scorecard.csv)
- [Versioned scorecard JSON](results/2026-08-30/scorecard.json)
- [Scoring policy](evidence/scoring-policy-v1.1.0.json), [comparison metadata](evidence/comparison-evidence-map.json), and [public file manifest](evidence/publication.json). Paired statistics are in the scorecard JSON; per-case raw evidence is outside this export.
- [Benchmark sources and attribution](BENCHMARK_SOURCES.md).
- Exported scorecard-file SHA-256: `1fc8900b08ef308b1f5ed1e1a36df28685d7395a1f61fe07a196a425c811aa37`.

No new inference was performed. Scores describe the saved benchmark cases; the post hoc metric revision should be validated on fresh data before prospective performance claims.
