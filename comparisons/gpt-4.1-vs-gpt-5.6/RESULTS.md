# Descriptive GPT-4.1 and GPT-5.6 benchmark scorecard

**Comparison:** GPT-4.1 through the OpenAI API and GPT-5.6 Luna, Terra, and Sol through Amazon Bedrock across 12 workloads, with a separate GPT-5.6 reasoning-effort sensitivity view.

**Measurement version:** v1.1.0. **Reporting package:** 1.0.0-rc4. **Coverage:** all 12 benchmarks, 10 model/reasoning conditions, 120 result cells, and 47,960 planned observations.

This project-specific evaluation presents the saved v1.1.0 scorecard under the recorded configurations and scoring rules. Every headline quality table shows both strict and address-normalized invoice accuracy. Address normalization was introduced after inspecting saved responses and remains exploratory pending validation on fresh cases. MATH-500 includes the v1.0.0 grader corrections.

All quality values are shown on a 0–100 scale. F1 is explicitly labeled and should not be read as document accuracy. N is the planned observation count per model/condition; the public benchmark names refer to the evaluated subsets shown here.

## Interpretation boundary

- GPT-4.1 versus GPT-5.6 is a descriptive, cross-platform comparison across the configured API paths; it is not a controlled causal estimate of a model migration.
- GPT-5.6 none versus low/high is descriptive because the saved runs differ in timing, some output ceilings, and overlapping suite execution. [Executed settings and dataset selection](METHODOLOGY.md) document these differences and unrecorded details. Reasoning-effort results should be read as workload-specific sensitivity evidence, not a universal reasoning effect.
- The scorecard does not establish migration readiness or a universal model winner. Quality, completion, latency, and cost must be evaluated together against customer-specific acceptance criteria.

[Workload completion gaps](#workloads-below-full-completion), [statistical uncertainty](#statistical-uncertainty-and-comparison-coverage), and [methodology](METHODOLOGY.md) are part of the interpretation of every point estimate.

## Executive operating summary

Completion means an API-completed response and is distinct from answer quality. Recorded spend is the metered lower bound across all attempts; a range is shown when missing usage required a conservative upper bound. No quality metric is aggregated across unlike workloads.

| Model | Reasoning | Planned | API completed | Completion gap | Completion rate | Recorded spend / bounds (USD) | Attempts missing usage |
|---|---|---:|---:|---:|---:|---:|---:|
| GPT-4.1 | omitted | 4796 | 4794 | 2 | 99.96% | $37.874710 | 0 |
| Luna | none | 4796 | 4788 | 8 | 99.83% | $3.208804 | 0 |
| Luna | low | 4796 | 4794 | 2 | 99.96% | $3.530319 | 0 |
| Luna | high | 4796 | 4741 | 55 | 98.85% | $7.913579 | 0 |
| Terra | none | 4796 | 4790 | 6 | 99.87% | $32.757704 | 0 |
| Terra | low | 4796 | 4793 | 3 | 99.94% | $38.506107 | 0 |
| Terra | high | 4796 | 4784 | 12 | 99.75% | $52.972756 | 0 |
| Sol | none | 4796 | 4767 | 29 | 99.40% | $62.763572 | 0 |
| Sol | low | 4796 | 4786 | 10 | 99.79% | $73.462579 | 0 |
| Sol | high | 4796 | 4758 | 38 | 99.21% | $104.53–$141.89 | 4 |

### Workloads below full completion

Banking77 contributes 64.22% of planned cases per configuration. The overall rate is weighted by case count and can conceal lower completion on smaller workloads. Every affected workload/configuration is shown below; quality continues to use the full planned denominator.

| Benchmark | Model | Reasoning | Completed / planned | Workload completion | Overall completion | Uncompleted cases | Recorded attempt events, including retries |
|---|---|---|---:|---:|---:|---:|---|
| ExtractBench | GPT-4.1 | omitted | 369/370 | 99.73% | 99.96% | 1 | File-input limit: 1 |
| ExtractBench | Luna | none | 362/370 | 97.84% | 99.83% | 8 | Context-length limit: 1; File-input limit: 2; Output-token limit: 5 |
| ExtractBench | Luna | low | 368/370 | 99.46% | 99.96% | 2 | File-input limit: 2 |
| ExtractBench | Luna | high | 326/370 | 88.11% | 98.85% | 44 | File-input limit: 2; Output-token limit: 42 |
| ExtractBench | Terra | none | 364/370 | 98.38% | 99.87% | 6 | Context-length limit: 1; File-input limit: 2; Output-token limit: 3 |
| ExtractBench | Terra | low | 367/370 | 99.19% | 99.94% | 3 | File-input limit: 2; Output-token limit: 1 |
| ExtractBench | Terra | high | 358/370 | 96.76% | 99.75% | 12 | File-input limit: 2; Output-token limit: 10 |
| ExtractBench | Sol | none | 341/370 | 92.16% | 99.40% | 29 | Context-length limit: 1; File-input limit: 2; Output-token limit: 26 |
| ExtractBench | Sol | low | 360/370 | 97.30% | 99.79% | 10 | File-input limit: 2; Output-token limit: 8 |
| ExtractBench | Sol | high | 332/370 | 89.73% | 99.21% | 38 | Authentication: 1; File-input limit: 2; Output-token limit: 36; Missing usage metadata: 4 |
| AIME | Luna | high | 59/60 | 98.33% | 98.85% | 1 | Cause not classified in aggregate |
| GPQA | GPT-4.1 | omitted | 197/198 | 99.49% | 99.96% | 1 | Cause not classified in aggregate |
| GPQA | Luna | high | 188/198 | 94.95% | 98.85% | 10 | Cause not classified in aggregate |

Uncompleted cases count final outcomes. Recorded attempt events count events across attempts, including errors recovered by a retry; they need not sum to the case gap. Some cases ended in local input rejection without an API response. A zero API-error count does not explain a completion gap, so missing classifications are shown explicitly.

An output-token-limit event means the request exhausted its configured allowance before completing the response. A file-input-limit event means the recorded input exceeded a limit enforced by the tested input path. These are outcomes of the tested configurations. [Executed settings and timing definitions](METHODOLOGY.md#executed-run-settings) explain the budgets, concurrency, retries, and latency populations.


## Workload quality and recorded cost per correct

For accuracy, exact-match, and task-success metrics, recorded cost per correct is metered spend divided by the number correct out of the full planned denominator. F1-only workloads report `N/A`: an aggregate F1 score is not a count of correct documents or entities and cannot support a defensible cost-per-correct calculation from this scorecard.

## GPT-5.6 reasoning: none

Invoice strict and address-normalized scores describe the same saved responses. The normalized row is post hoc and exploratory; it requires validation on fresh cases. These point estimates do not have uniform statistical support: [available confidence intervals and paired comparisons](#statistical-uncertainty-and-comparison-coverage) cover invoices, MATH-500, and AIME only.

| Benchmark | Metric | N | GPT-4.1 | GPT-4.1 $/correct | Luna | Luna $/correct | Terra | Terra $/correct | Sol | Sol $/correct |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | Task accuracy | 192 | 98.44% | $0.000785 | 100.00% | $0.000090 | 100.00% | $0.000904 | 99.48% | $0.001625 |
| Synthetic invoice extraction | Strict document accuracy | 192 | 92.71% | $0.005490 | 84.38% | $0.000739 | 91.67% | $0.006802 | 95.83% | $0.010542 |
| Synthetic invoice extraction | Address-normalized accuracy (post hoc; exploratory) | 192 | 92.71% | $0.005490 | 91.67% | $0.000680 | 91.67% | $0.006802 | 97.92% | $0.010317 |
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

Invoice strict and address-normalized scores describe the same saved responses. The normalized row is post hoc and exploratory; it requires validation on fresh cases. These point estimates do not have uniform statistical support: [available confidence intervals and paired comparisons](#statistical-uncertainty-and-comparison-coverage) cover invoices, MATH-500, and AIME only.

| Benchmark | Metric | N | GPT-4.1 | GPT-4.1 $/correct | Luna | Luna $/correct | Terra | Terra $/correct | Sol | Sol $/correct |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | Task accuracy | 192 | 98.44% | $0.000785 | 100.00% | $0.000095 | 100.00% | $0.000978 | 100.00% | $0.001822 |
| Synthetic invoice extraction | Strict document accuracy | 192 | 92.71% | $0.005490 | 73.96% | $0.000999 | 89.58% | $0.007278 | 92.19% | $0.012284 |
| Synthetic invoice extraction | Address-normalized accuracy (post hoc; exploratory) | 192 | 92.71% | $0.005490 | 89.06% | $0.000829 | 90.10% | $0.007236 | 99.48% | $0.011384 |
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

Invoice strict and address-normalized scores describe the same saved responses. The normalized row is post hoc and exploratory; it requires validation on fresh cases. These point estimates do not have uniform statistical support: [available confidence intervals and paired comparisons](#statistical-uncertainty-and-comparison-coverage) cover invoices, MATH-500, and AIME only.

| Benchmark | Metric | N | GPT-4.1 | GPT-4.1 $/correct | Luna | Luna $/correct | Terra | Terra $/correct | Sol | Sol $/correct |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | Task accuracy | 192 | 98.44% | $0.000785 | 100.00% | $0.000120 | 99.48% | $0.001058 | 99.48% | $0.001844 |
| Synthetic invoice extraction | Strict document accuracy | 192 | 92.71% | $0.005490 | 76.04% | $0.001305 | 91.15% | $0.008049 | 84.38% | $0.014203 |
| Synthetic invoice extraction | Address-normalized accuracy (post hoc; exploratory) | 192 | 92.71% | $0.005490 | 88.54% | $0.001121 | 95.31% | $0.007697 | 98.96% | $0.012110 |
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

## Statistical uncertainty and comparison coverage

Point estimates describe these saved cases. Available intervals and paired tests are reproduced below without recomputation. Statistical support is not uniform across the 120 result cells: the exported paired comparisons cover synthetic invoices, MATH-500, and AIME only.

| Benchmark | Saved per-configuration 95% intervals | Saved paired comparisons |
|---|---|---|
| Classification routing | Unavailable in saved scorecard | Unavailable in saved scorecard |
| Synthetic invoice extraction | Wilson intervals for 10 configurations | GPT-4.1 vs none; low vs high |
| Banking77 | Unavailable in saved scorecard | Unavailable in saved scorecard |
| CORD OCR | Unavailable in saved scorecard | Unavailable in saved scorecard |
| CORD image | Unavailable in saved scorecard | Unavailable in saved scorecard |
| ExtractBench | Unavailable in saved scorecard | Unavailable in saved scorecard |
| GSM8K | Unavailable in saved scorecard | Unavailable in saved scorecard |
| MATH-500 | Wilson intervals for 10 configurations | GPT-4.1 vs none; low vs high |
| AIME | Wilson intervals for 10 configurations | GPT-4.1 vs none; low vs high |
| GPQA | Unavailable in saved scorecard | Unavailable in saved scorecard |
| MMLU-Pro | Unavailable in saved scorecard | Unavailable in saved scorecard |
| HumanEval | Unavailable in saved scorecard | Unavailable in saved scorecard |

Paired 95% intervals are unadjusted, stratified task-bootstrap intervals from 10,000 resamples. The p-values are exact two-sided McNemar tests adjusted with Holm within each three-model family, separately for each workload, comparison type, and scoring rule. They are not adjusted across all 120 cells. An interval excluding zero can therefore coexist with an adjusted p-value above 0.05.

The interpretation column uses the stored adjusted test at 0.05. 'Difference not established' does not demonstrate equivalence. A zero-width bootstrap interval when all observed paired outcomes agree does not establish zero uncertainty in future cases. These tests do not remove cross-platform/run-condition differences or validate a post hoc scoring choice; normalized invoice results remain exploratory.

### Paired comparisons: GPT-4.1 versus GPT-5.6 none

Delta is candidate minus baseline in percentage points (pp). The first family uses GPT-4.1 omitted as baseline and each GPT-5.6 model at none as candidate; the second compares high against low within the same model.

| Benchmark | Model | Scoring rule | Paired cases | Delta (pp) | Paired 95% interval (pp; unadjusted) | Holm-adjusted p | Interpretation |
|---|---|---|---:|---:|---:|---:|---|
| Synthetic invoice extraction | Luna | Strict document accuracy | 192 | -8.33 | [-14.58, -2.08] | 0.041559 | Lower measured score; adjusted test threshold met |
| Synthetic invoice extraction | Luna | Address-normalized (post hoc; exploratory) | 192 | -1.04 | [-6.25, +4.17] | 1.000000 | Difference not established |
| Synthetic invoice extraction | Terra | Strict document accuracy | 192 | -1.04 | [-4.17, +2.08] | 0.726562 | Difference not established |
| Synthetic invoice extraction | Terra | Address-normalized (post hoc; exploratory) | 192 | -1.04 | [-4.17, +2.08] | 1.000000 | Difference not established |
| Synthetic invoice extraction | Sol | Strict document accuracy | 192 | +3.12 | [-1.56, +7.81] | 0.526352 | Difference not established |
| Synthetic invoice extraction | Sol | Address-normalized (post hoc; exploratory) | 192 | +5.21 | [+1.56, +9.38] | 0.063812 | Difference not established |
| MATH-500 | Luna | Corrected task accuracy | 100 | +0.00 | [-6.00, +6.00] | 1.000000 | No observed difference; equality not established |
| MATH-500 | Terra | Corrected task accuracy | 100 | +1.00 | [-4.00, +6.00] | 1.000000 | Difference not established |
| MATH-500 | Sol | Corrected task accuracy | 100 | +4.00 | [-1.00, +9.00] | 0.867188 | Difference not established |
| AIME | Luna | Corrected task accuracy | 60 | +8.33 | [-6.67, +23.33] | 0.383310 | Difference not established |
| AIME | Terra | Corrected task accuracy | 60 | +18.33 | [+5.00, +31.67] | 0.038422 | Higher measured score; adjusted test threshold met |
| AIME | Sol | Corrected task accuracy | 60 | +30.00 | [+16.67, +43.33] | 0.000831 | Higher measured score; adjusted test threshold met |

### Paired comparisons: GPT-5.6 low versus high

Delta is candidate minus baseline in percentage points (pp). The first family uses GPT-4.1 omitted as baseline and each GPT-5.6 model at none as candidate; the second compares high against low within the same model.

| Benchmark | Model | Scoring rule | Paired cases | Delta (pp) | Paired 95% interval (pp; unadjusted) | Holm-adjusted p | Interpretation |
|---|---|---|---:|---:|---:|---:|---|
| Synthetic invoice extraction | Luna | Strict document accuracy | 192 | +2.08 | [-4.17, +8.33] | 1.000000 | Difference not established |
| Synthetic invoice extraction | Luna | Address-normalized (post hoc; exploratory) | 192 | -0.52 | [-4.69, +3.12] | 1.000000 | Difference not established |
| Synthetic invoice extraction | Terra | Strict document accuracy | 192 | +1.56 | [-2.60, +6.25] | 1.000000 | Difference not established |
| Synthetic invoice extraction | Terra | Address-normalized (post hoc; exploratory) | 192 | +5.21 | [+2.08, +8.85] | 0.019043 | Higher measured score; adjusted test threshold met |
| Synthetic invoice extraction | Sol | Strict document accuracy | 192 | -7.81 | [-13.02, -3.12] | 0.012232 | Lower measured score; adjusted test threshold met |
| Synthetic invoice extraction | Sol | Address-normalized (post hoc; exploratory) | 192 | -0.52 | [-1.56, +0.00] | 1.000000 | Difference not established |
| MATH-500 | Luna | Corrected task accuracy | 100 | +1.00 | [-3.00, +5.00] | 1.000000 | Difference not established |
| MATH-500 | Terra | Corrected task accuracy | 100 | +0.00 | [-3.00, +3.00] | 1.000000 | No observed difference; equality not established |
| MATH-500 | Sol | Corrected task accuracy | 100 | +0.00 | [+0.00, +0.00] | 1.000000 | No observed difference; equality not established |
| AIME | Luna | Corrected task accuracy | 60 | +16.67 | [+5.00, +30.00] | 0.063812 | Difference not established |
| AIME | Terra | Corrected task accuracy | 60 | +6.67 | [+0.00, +15.00] | 0.437500 | Difference not established |
| AIME | Sol | Corrected task accuracy | 60 | +1.67 | [+0.00, +5.00] | 1.000000 | Difference not established |

### Available per-configuration confidence intervals

These are the saved Wilson 95% intervals for a single configuration's proportion correct, with the full planned denominator. They are unadjusted and are not intervals for the difference between models. The 40 metric rows below cover 30 result cells; invoices have both strict and normalized intervals. No intervals are inferred for the other 90 cells or for F1 metrics.

| Benchmark | Model | Reasoning | Scoring rule | Correct / planned | Accuracy | Wilson 95% interval (unadjusted) |
|---|---|---|---|---:|---:|---:|
| Synthetic invoice extraction | GPT-4.1 | omitted | Strict document accuracy | 178/192 | 92.71% | [88.13%, 95.61%] |
| Synthetic invoice extraction | GPT-4.1 | omitted | Address-normalized (post hoc; exploratory) | 178/192 | 92.71% | [88.13%, 95.61%] |
| Synthetic invoice extraction | Luna | none | Strict document accuracy | 162/192 | 84.38% | [78.57%, 88.83%] |
| Synthetic invoice extraction | Luna | none | Address-normalized (post hoc; exploratory) | 176/192 | 91.67% | [86.89%, 94.81%] |
| Synthetic invoice extraction | Luna | low | Strict document accuracy | 142/192 | 73.96% | [67.32%, 79.65%] |
| Synthetic invoice extraction | Luna | low | Address-normalized (post hoc; exploratory) | 171/192 | 89.06% | [83.86%, 92.73%] |
| Synthetic invoice extraction | Luna | high | Strict document accuracy | 146/192 | 76.04% | [69.53%, 81.53%] |
| Synthetic invoice extraction | Luna | high | Address-normalized (post hoc; exploratory) | 170/192 | 88.54% | [83.26%, 92.31%] |
| Synthetic invoice extraction | Terra | none | Strict document accuracy | 176/192 | 91.67% | [86.89%, 94.81%] |
| Synthetic invoice extraction | Terra | none | Address-normalized (post hoc; exploratory) | 176/192 | 91.67% | [86.89%, 94.81%] |
| Synthetic invoice extraction | Terra | low | Strict document accuracy | 172/192 | 89.58% | [84.46%, 93.16%] |
| Synthetic invoice extraction | Terra | low | Address-normalized (post hoc; exploratory) | 173/192 | 90.10% | [85.06%, 93.57%] |
| Synthetic invoice extraction | Terra | high | Strict document accuracy | 175/192 | 91.15% | [86.28%, 94.40%] |
| Synthetic invoice extraction | Terra | high | Address-normalized (post hoc; exploratory) | 183/192 | 95.31% | [91.33%, 97.51%] |
| Synthetic invoice extraction | Sol | none | Strict document accuracy | 184/192 | 95.83% | [91.99%, 97.87%] |
| Synthetic invoice extraction | Sol | none | Address-normalized (post hoc; exploratory) | 188/192 | 97.92% | [94.77%, 99.19%] |
| Synthetic invoice extraction | Sol | low | Strict document accuracy | 177/192 | 92.19% | [87.51%, 95.21%] |
| Synthetic invoice extraction | Sol | low | Address-normalized (post hoc; exploratory) | 191/192 | 99.48% | [97.11%, 99.91%] |
| Synthetic invoice extraction | Sol | high | Strict document accuracy | 162/192 | 84.38% | [78.57%, 88.83%] |
| Synthetic invoice extraction | Sol | high | Address-normalized (post hoc; exploratory) | 190/192 | 98.96% | [96.28%, 99.71%] |
| MATH-500 | GPT-4.1 | omitted | Corrected task accuracy | 91/100 | 91.00% | [83.77%, 95.19%] |
| MATH-500 | Luna | none | Corrected task accuracy | 91/100 | 91.00% | [83.77%, 95.19%] |
| MATH-500 | Luna | low | Corrected task accuracy | 97/100 | 97.00% | [91.55%, 98.97%] |
| MATH-500 | Luna | high | Corrected task accuracy | 98/100 | 98.00% | [93.00%, 99.45%] |
| MATH-500 | Terra | none | Corrected task accuracy | 92/100 | 92.00% | [85.00%, 95.89%] |
| MATH-500 | Terra | low | Corrected task accuracy | 99/100 | 99.00% | [94.55%, 99.82%] |
| MATH-500 | Terra | high | Corrected task accuracy | 99/100 | 99.00% | [94.55%, 99.82%] |
| MATH-500 | Sol | none | Corrected task accuracy | 95/100 | 95.00% | [88.83%, 97.85%] |
| MATH-500 | Sol | low | Corrected task accuracy | 100/100 | 100.00% | [96.30%, 100.00%] |
| MATH-500 | Sol | high | Corrected task accuracy | 100/100 | 100.00% | [96.30%, 100.00%] |
| AIME | GPT-4.1 | omitted | Corrected task accuracy | 23/60 | 38.33% | [27.09%, 50.98%] |
| AIME | Luna | none | Corrected task accuracy | 28/60 | 46.67% | [34.63%, 59.11%] |
| AIME | Luna | low | Corrected task accuracy | 44/60 | 73.33% | [60.99%, 82.87%] |
| AIME | Luna | high | Corrected task accuracy | 54/60 | 90.00% | [79.85%, 95.34%] |
| AIME | Terra | none | Corrected task accuracy | 34/60 | 56.67% | [44.10%, 68.43%] |
| AIME | Terra | low | Corrected task accuracy | 55/60 | 91.67% | [81.93%, 96.39%] |
| AIME | Terra | high | Corrected task accuracy | 59/60 | 98.33% | [91.14%, 99.71%] |
| AIME | Sol | none | Corrected task accuracy | 41/60 | 68.33% | [55.77%, 78.69%] |
| AIME | Sol | low | Corrected task accuracy | 59/60 | 98.33% | [91.14%, 99.71%] |
| AIME | Sol | high | Corrected task accuracy | 60/60 | 100.00% | [93.98%, 100.00%] |

## Invoice extraction: strict and post hoc normalized scoring

### What normalization means

Address normalization allows the same address information to be returned with a line break or a comma separator. It applies only to the synthetic invoice benchmark's `supplier.address` and `customer.address` fields, using the same rule for GPT-4.1 and every GPT-5.6 model/reasoning setting.

The exact comparison is:

1. Keep every field match already accepted by the strict grader.
2. For a mismatched supplier or customer address, replace each LF newline (`\n`) with comma-space (`, `) in both the expected address and the model's address. Trim leading/trailing whitespace and collapse whitespace runs to a single space.
3. Accept that address only if the resulting strings match exactly. All remaining characters, digits, punctuation, capitalization, and component order must match.

In this illustrative example, these two addresses are equivalent under address-normalized scoring. This is not a captured model response:

```text
Expected address:
400 Trial Plaza
Seattle, WA 98101

Model's address:
400 Trial Plaza, Seattle, WA 98101
```

This example passes address-normalized comparison and fails strict comparison because of the added comma. Changing `400` to `401`, changing postal code `98101` to `98102`, or omitting an apartment present in the reference still fails. Abbreviation expansion, fuzzy matching, case folding, and deletion of other punctuation are not added.

The original response must first pass JSON parsing and schema validation. A complete document passes only when the API response is completed and every field passes its applicable comparison. Missing fields, invalid types, wrong dates, and incorrect totals are still checked by the existing rules. All 192 planned documents remain in each condition's denominator.

The saved JSON/CSV uses `primary` for address-normalized document accuracy and `secondary` for strict document accuracy using the v1.0.0 corrected references. Those field names preserve the measurement record; they do not mean normalization was specified before the run or validated on fresh cases. Address normalization is a post hoc, exploratory metric revision. Strict scoring already collapses whitespace, normalizes currency case, and compares money at cent precision; it does not require byte-for-byte text equality.

This formatting rule is separate from the earlier correction of compact-layout invoice references and the MATH-500 grader fixes. It is an offline rescore of saved responses: the source documents and model outputs are preserved, and no new model calls were made.

### Results under both metrics

| Model | Reasoning | Strict accuracy | Strict correct / planned | Recorded $ / strict correct | Normalized accuracy (post hoc; exploratory) | Normalized correct / planned | Recorded $ / normalized correct |
|---|---|---:|---:|---:|---:|---:|---:|
| GPT-4.1 | omitted | 92.71% | 178/192 | $0.005490 | 92.71% | 178/192 | $0.005490 |
| Luna | none | 84.38% | 162/192 | $0.000739 | 91.67% | 176/192 | $0.000680 |
| Luna | low | 73.96% | 142/192 | $0.000999 | 89.06% | 171/192 | $0.000829 |
| Luna | high | 76.04% | 146/192 | $0.001305 | 88.54% | 170/192 | $0.001121 |
| Terra | none | 91.67% | 176/192 | $0.006802 | 91.67% | 176/192 | $0.006802 |
| Terra | low | 89.58% | 172/192 | $0.007278 | 90.10% | 173/192 | $0.007236 |
| Terra | high | 91.15% | 175/192 | $0.008049 | 95.31% | 183/192 | $0.007697 |
| Sol | none | 95.83% | 184/192 | $0.010542 | 97.92% | 188/192 | $0.010317 |
| Sol | low | 92.19% | 177/192 | $0.012284 | 99.48% | 191/192 | $0.011384 |
| Sol | high | 84.38% | 162/192 | $0.014203 | 98.96% | 190/192 | $0.012110 |

## Recorded cost and latency for every result

Costs sum recorded usage across all attempts, including retries. A range runs from the metered lower bound to the conservative upper estimate for attempts missing usage. It is an accounting bound, not a statistical confidence interval or an exact billed total. Exact values remain in the CSV. Completion is API completion, separate from answer quality.

For Sol/high ExtractBench, four saved attempts were marked completed but lacked usage metadata and were retried. The metered total includes the recorded usage of their retries; usage for the original attempts remains unpriced. The stored upper estimate adds an allowance for each missing attempt using 1,000,000 uncached input tokens and 16,384 output tokens at the frozen long-context rates. The saved evidence does not establish why usage metadata was absent.

Latency values below are seconds. End-to-end means the final retained attempt's duration, including terminal failures and local input rejections; it excludes earlier retry durations, retry backoff, and application preprocessing outside the attempt timer. TTFT is time to first text output on final attempts with a recorded first-output timestamp; attempts without that timestamp do not enter its percentiles. [Timing and retry definitions](METHODOLOGY.md#latency-retries-and-cost) explain the different populations. All timing values are carried directly from the saved records.

The accompanying CSV also includes token counts, retries, harness-classified terminal failure counts (`capability_failure_observations`), cost bounds, and cost-per-correct values for count-based quality metrics. The terminal-failure field includes recorded request/input-limit outcomes and inherited incomplete-response counts where the source aggregate does not classify a cause; it does not assess inherent model capability. F1-only cost-per-correct values remain blank; no values are inferred.

### GPT-4.1 / omitted

| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | $0.148310 | 0 | 0.506 | 1.493 | 0.667 | 1.823 |
| Synthetic invoice extraction | 192/192 | $0.977292 | 0 | 0.577 | 1.718 | 3.299 | 6.734 |
| Banking77 | 3080/3080 | $3.372096 | 0 | 0.487 | 1.034 | 0.647 | 1.278 |
| CORD OCR | 100/100 | $0.696184 | 0 | 0.649 | 1.225 | 2.204 | 4.669 |
| CORD image | 100/100 | $0.576156 | 0 | 1.410 | 2.620 | 3.247 | 6.175 |
| ExtractBench | 369/370 | $27.650146 | 0 | 3.227 | 17.665 | 12.233 | 57.251 |
| GSM8K | 100/100 | $0.184344 | 0 | 0.538 | 1.029 | 1.730 | 2.968 |
| MATH-500 | 100/100 | $0.668780 | 0 | 0.509 | 1.084 | 3.395 | 14.231 |
| AIME | 60/60 | $1.742436 | 0 | 0.536 | 0.810 | 19.323 | 43.161 |
| GPQA | 197/198 | $1.217596 | 0 | 0.533 | 1.127 | 4.520 | 11.415 |
| MMLU-Pro | 140/140 | $0.364266 | 0 | 0.524 | 1.024 | 1.924 | 5.962 |
| HumanEval | 164/164 | $0.277104 | 0 | 0.502 | 1.040 | 1.429 | 2.824 |

### Luna / none

| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | $0.017356 | 0 | 0.289 | 0.417 | 0.292 | 0.419 |
| Synthetic invoice extraction | 192/192 | $0.119680 | 0 | 0.458 | 0.601 | 1.303 | 1.650 |
| Banking77 | 3080/3080 | $0.389471 | 0 | 0.296 | 0.606 | 0.299 | 0.613 |
| CORD OCR | 100/100 | $0.090504 | 0 | 0.459 | 0.678 | 0.948 | 1.928 |
| CORD image | 100/100 | $0.093238 | 0 | 1.276 | 3.686 | 1.787 | 4.085 |
| ExtractBench | 362/370 | $2.352190 | 0 | 1.458 | 7.278 | 4.915 | 38.515 |
| GSM8K | 100/100 | $0.016367 | 0 | 0.451 | 0.531 | 0.668 | 1.006 |
| MATH-500 | 100/100 | $0.027911 | 0 | 0.447 | 0.669 | 0.907 | 2.031 |
| AIME | 60/60 | $0.045030 | 0 | 0.453 | 0.508 | 2.553 | 3.729 |
| GPQA | 198/198 | $0.012320 | 0 | 0.295 | 0.614 | 0.298 | 0.615 |
| MMLU-Pro | 140/140 | $0.008112 | 0 | 0.295 | 0.447 | 0.299 | 0.451 |
| HumanEval | 164/164 | $0.036627 | 0 | 0.445 | 0.510 | 0.699 | 1.185 |

### Luna / low

| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | $0.018277 | 0 | 0.333 | 0.660 | 0.338 | 0.687 |
| Synthetic invoice extraction | 192/192 | $0.141794 | 0 | 1.069 | 2.975 | 1.849 | 3.577 |
| Banking77 | 3080/3080 | $0.429267 | 0 | 0.327 | 1.242 | 0.332 | 1.252 |
| CORD OCR | 100/100 | $0.126628 | 0 | 2.253 | 3.959 | 2.946 | 4.709 |
| CORD image | 100/100 | $0.080550 | 0 | 4.109 | 18.781 | 4.754 | 19.171 |
| ExtractBench | 368/370 | $2.294218 | 0 | 4.681 | 9.032 | 8.205 | 21.636 |
| GSM8K | 100/100 | $0.022849 | 0 | 0.713 | 1.286 | 1.042 | 1.690 |
| MATH-500 | 100/100 | $0.061113 | 0 | 1.096 | 8.080 | 1.545 | 9.304 |
| AIME | 60/60 | $0.129701 | 0 | 6.037 | 19.076 | 8.094 | 21.101 |
| GPQA | 198/198 | $0.130707 | 0 | 1.882 | 11.563 | 1.883 | 11.582 |
| MMLU-Pro | 140/140 | $0.035123 | 0 | 0.901 | 3.972 | 0.901 | 3.975 |
| HumanEval | 164/164 | $0.060093 | 0 | 0.876 | 3.890 | 1.200 | 4.913 |

### Luna / high

| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | $0.023095 | 0 | 0.517 | 0.884 | 0.522 | 0.884 |
| Synthetic invoice extraction | 192/192 | $0.190570 | 0 | 1.698 | 6.388 | 2.581 | 7.272 |
| Banking77 | 3080/3080 | $0.577992 | 0 | 0.334 | 2.134 | 0.338 | 2.157 |
| CORD OCR | 100/100 | $0.394426 | 0 | 11.585 | 33.902 | 12.158 | 34.748 |
| CORD image | 100/100 | $0.299481 | 0 | 8.378 | 38.478 | 9.137 | 38.996 |
| ExtractBench | 326/370 | $5.179149 | 0 | 29.949 | 63.684 | 37.400 | 76.296 |
| GSM8K | 100/100 | $0.028756 | 0 | 0.878 | 1.932 | 1.094 | 2.260 |
| MATH-500 | 100/100 | $0.085035 | 0 | 1.615 | 14.030 | 1.948 | 14.475 |
| AIME | 59/60 | $0.317173 | 0 | 12.391 | 69.877 | 14.172 | 72.821 |
| GPQA | 188/198 | $0.643222 | 0 | 3.158 | 52.810 | 3.461 | 87.616 |
| MMLU-Pro | 140/140 | $0.091627 | 0 | 1.061 | 9.378 | 1.063 | 9.382 |
| HumanEval | 164/164 | $0.083053 | 0 | 1.299 | 5.812 | 1.632 | 5.947 |

### Terra / none

| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | $0.173560 | 0 | 0.350 | 0.511 | 0.353 | 0.514 |
| Synthetic invoice extraction | 192/192 | $1.197166 | 0 | 0.565 | 0.859 | 1.793 | 2.347 |
| Banking77 | 3080/3080 | $3.894832 | 0 | 0.360 | 0.633 | 0.367 | 0.639 |
| CORD OCR | 100/100 | $0.905934 | 0 | 0.566 | 0.956 | 1.258 | 2.575 |
| CORD image | 100/100 | $0.935596 | 0 | 1.390 | 3.631 | 2.217 | 4.758 |
| ExtractBench | 364/370 | $24.092598 | 0 | 1.586 | 8.127 | 8.134 | 55.355 |
| GSM8K | 100/100 | $0.142732 | 0 | 0.376 | 0.654 | 0.899 | 1.454 |
| MATH-500 | 100/100 | $0.288086 | 0 | 0.539 | 0.796 | 1.310 | 4.345 |
| AIME | 60/60 | $0.584003 | 0 | 0.547 | 0.799 | 4.957 | 11.347 |
| GPQA | 198/198 | $0.125104 | 0 | 0.371 | 0.694 | 0.381 | 0.708 |
| MMLU-Pro | 140/140 | $0.081261 | 0 | 0.362 | 1.256 | 0.371 | 1.259 |
| HumanEval | 164/164 | $0.336833 | 0 | 0.428 | 0.927 | 0.976 | 1.930 |

### Terra / low

| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | $0.187843 | 0 | 0.393 | 0.757 | 0.396 | 0.760 |
| Synthetic invoice extraction | 192/192 | $1.251840 | 0 | 0.599 | 1.982 | 1.798 | 3.608 |
| Banking77 | 3080/3080 | $4.944100 | 0 | 0.415 | 2.007 | 0.424 | 2.013 |
| CORD OCR | 100/100 | $1.055226 | 0 | 0.609 | 5.123 | 1.484 | 6.308 |
| CORD image | 100/100 | $0.744114 | 0 | 3.955 | 14.902 | 4.816 | 15.319 |
| ExtractBench | 367/370 | $26.440579 | 0 | 7.657 | 16.745 | 13.650 | 46.107 |
| GSM8K | 100/100 | $0.194608 | 0 | 0.784 | 1.268 | 1.129 | 1.983 |
| MATH-500 | 100/100 | $0.527811 | 0 | 1.309 | 13.813 | 1.890 | 14.565 |
| AIME | 60/60 | $1.349141 | 0 | 10.350 | 36.451 | 13.754 | 39.890 |
| GPQA | 198/198 | $1.114153 | 0 | 1.963 | 21.238 | 1.967 | 21.239 |
| MMLU-Pro | 140/140 | $0.307338 | 0 | 0.938 | 4.693 | 0.939 | 4.697 |
| HumanEval | 164/164 | $0.389356 | 0 | 0.585 | 2.384 | 0.989 | 2.968 |

### Terra / high

| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | $0.202165 | 0 | 0.446 | 0.751 | 0.449 | 0.754 |
| Synthetic invoice extraction | 192/192 | $1.408617 | 0 | 1.177 | 2.785 | 2.177 | 4.282 |
| Banking77 | 3080/3080 | $5.683049 | 0 | 0.475 | 2.161 | 0.479 | 2.165 |
| CORD OCR | 100/100 | $1.386163 | 0 | 3.574 | 8.647 | 4.179 | 9.313 |
| CORD image | 100/100 | $0.979157 | 0 | 4.432 | 10.030 | 5.093 | 11.559 |
| ExtractBench | 358/370 | $37.345983 | 0 | 19.100 | 45.685 | 27.345 | 85.413 |
| GSM8K | 100/100 | $0.199294 | 0 | 0.796 | 1.315 | 1.140 | 1.982 |
| MATH-500 | 100/100 | $0.581878 | 0 | 1.223 | 9.990 | 2.007 | 11.644 |
| AIME | 60/60 | $1.831918 | 0 | 10.638 | 70.220 | 13.689 | 74.340 |
| GPQA | 198/198 | $2.541812 | 0 | 2.528 | 59.405 | 2.531 | 59.485 |
| MMLU-Pro | 140/140 | $0.353643 | 0 | 0.866 | 6.640 | 0.867 | 6.642 |
| HumanEval | 164/164 | $0.459078 | 0 | 0.605 | 4.528 | 1.096 | 5.054 |

### Sol / none

| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | $0.310316 | 0 | 0.458 | 1.519 | 0.461 | 1.525 |
| Synthetic invoice extraction | 192/192 | $1.939689 | 0 | 0.598 | 1.523 | 2.178 | 3.345 |
| Banking77 | 3080/3080 | $6.983116 | 0 | 0.477 | 1.566 | 0.484 | 1.580 |
| CORD OCR | 100/100 | $1.554440 | 0 | 0.728 | 1.962 | 1.675 | 3.561 |
| CORD image | 100/100 | $1.607168 | 0 | 1.869 | 4.843 | 2.900 | 7.294 |
| ExtractBench | 341/370 | $48.061522 | 0 | 2.009 | 9.180 | 8.864 | 103.346 |
| GSM8K | 100/100 | $0.226932 | 0 | 0.586 | 1.476 | 1.104 | 1.814 |
| MATH-500 | 100/100 | $0.404844 | 0 | 0.587 | 1.469 | 1.509 | 4.775 |
| AIME | 60/60 | $0.855848 | 0 | 0.583 | 2.607 | 5.789 | 14.022 |
| GPQA | 198/198 | $0.231409 | 0 | 0.497 | 1.872 | 0.505 | 2.057 |
| MMLU-Pro | 140/140 | $0.146068 | 0 | 0.463 | 1.449 | 0.464 | 1.472 |
| HumanEval | 164/164 | $0.442220 | 0 | 0.578 | 1.444 | 1.020 | 1.915 |

### Sol / low

| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | $0.349796 | 0 | 0.555 | 2.373 | 0.557 | 2.379 |
| Synthetic invoice extraction | 192/192 | $2.174336 | 0 | 0.837 | 2.920 | 2.424 | 4.625 |
| Banking77 | 3080/3080 | $8.355486 | 0 | 0.574 | 2.590 | 0.580 | 2.606 |
| CORD OCR | 100/100 | $1.918070 | 0 | 1.839 | 4.722 | 2.732 | 6.500 |
| CORD image | 100/100 | $1.128814 | 0 | 4.184 | 10.755 | 5.105 | 12.019 |
| ExtractBench | 360/370 | $54.193861 | 0 | 8.638 | 17.848 | 15.735 | 83.855 |
| GSM8K | 100/100 | $0.339187 | 0 | 1.240 | 3.280 | 1.792 | 4.024 |
| MATH-500 | 100/100 | $0.737884 | 0 | 1.837 | 11.701 | 2.616 | 12.776 |
| AIME | 60/60 | $1.734467 | 0 | 9.916 | 32.677 | 13.078 | 38.230 |
| GPQA | 198/198 | $1.456168 | 0 | 3.721 | 16.202 | 3.739 | 16.206 |
| MMLU-Pro | 140/140 | $0.381159 | 0 | 1.298 | 6.158 | 1.305 | 6.161 |
| HumanEval | 164/164 | $0.693352 | 0 | 1.739 | 7.413 | 2.177 | 7.821 |

### Sol / high

| Benchmark | Completed / planned | Recorded spend / bounds (USD) | Attempts missing usage | TTFT p50 (s) | TTFT p95 (s) | Final-attempt E2E p50 (s) | Final-attempt E2E p95 (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Classification routing | 192/192 | $0.352128 | 0 | 0.627 | 2.227 | 0.630 | 2.230 |
| Synthetic invoice extraction | 192/192 | $2.300880 | 0 | 0.876 | 3.406 | 2.507 | 4.983 |
| Banking77 | 3080/3080 | $9.581062 | 0 | 0.683 | 3.793 | 0.694 | 3.829 |
| CORD OCR | 100/100 | $2.678698 | 0 | 4.546 | 13.540 | 5.249 | 14.554 |
| CORD image | 100/100 | $2.005276 | 0 | 6.355 | 20.120 | 7.122 | 20.860 |
| ExtractBench | 332/370 | $78.10–$115.46 | 4 | 29.268 | 79.309 | 38.213 | 120.693 |
| GSM8K | 100/100 | $0.372253 | 0 | 1.175 | 4.746 | 1.645 | 4.881 |
| MATH-500 | 100/100 | $0.963758 | 0 | 1.920 | 17.551 | 2.639 | 18.283 |
| AIME | 60/60 | $3.030289 | 0 | 14.021 | 77.255 | 17.165 | 83.647 |
| GPQA | 198/198 | $3.679994 | 0 | 7.523 | 43.249 | 7.536 | 43.254 |
| MMLU-Pro | 140/140 | $0.627427 | 0 | 1.372 | 6.999 | 1.377 | 7.007 |
| HumanEval | 164/164 | $0.836902 | 0 | 6.144 | 17.178 | 6.712 | 17.493 |

## Source and detailed evidence

- [All 120 quality and operational rows](results/2026-08-30/scorecard.csv)
- [Versioned scorecard JSON](results/2026-08-30/scorecard.json)
- [Scoring policy](evidence/scoring-policy-v1.1.0.json), [comparison metadata](evidence/comparison-evidence-map.json), and [public file manifest](evidence/publication.json). The statistical tables reproduce the available paired results and confidence intervals in the scorecard JSON; per-case raw evidence is outside this export.
- [Methodology](METHODOLOGY.md), [executed run settings](evidence/run-settings.json), and [dataset selection](evidence/dataset-selection.json).
- [Benchmark sources and attribution](BENCHMARK_SOURCES.md).
- Exported scorecard-file SHA-256: `1fc8900b08ef308b1f5ed1e1a36df28685d7395a1f61fe07a196a425c811aa37`.

No new inference was performed. Scores describe the saved benchmark cases; the post hoc metric revision should be validated on fresh data before prospective performance claims.
