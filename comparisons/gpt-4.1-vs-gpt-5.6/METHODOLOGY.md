# Evaluation methodology

This comparison reports 12 workloads under 10 model/reasoning configurations: pinned GPT-4.1 with the reasoning field omitted, and GPT-5.6 Luna, Terra and Sol at `none`, `low` and `high`. The same full-stage cases are used for every configuration of a workload.

The original GPT-4.1/GPT-5.6 `none` comparison and the later GPT-5.6 `low`/`high` comparison have different run conditions. They support workload-specific observations. They do not isolate model effects across providers or establish production migration readiness.

[Recorded run settings](evidence/run-settings.json) cover all 120 cells, including output ceilings, request fields, retry counts and the sample counts used for latency. [Dataset selection](evidence/dataset-selection.json) records pinned public revisions, splits, selection procedures and case identifiers. These additions describe existing measurements; they do not change the scorecard.

## Executed run settings

Per-attempt records confirm streaming Responses API requests, `service_tier=default` and `store=false`. GPT-4.1 used `gpt-4.1-2025-04-14` through the direct OpenAI API, with no reasoning field. GPT-5.6 used the Mantle Responses-compatible endpoint in `us-east-1`, with the indicated reasoning effort.

The table shows the recorded `max_output_tokens` ceiling, rather than output tokens actually consumed. Each original-comparison ceiling was shared by GPT-4.1 and all three GPT-5.6 `none` configurations. Every `low` and `high` configuration used 16,384.

| Workload | GPT-4.1 omitted / GPT-5.6 none | GPT-5.6 low / high |
|---|---:|---:|
| Synthetic classification/routing | 2,048 | 16,384 |
| Synthetic invoice extraction | 8,192 | 16,384 |
| Banking77 | 32 | 16,384 |
| CORD original images | 8,192 | 16,384 |
| CORD provided OCR | 8,192 | 16,384 |
| ExtractBench | 16,384 | 16,384 |
| GSM8K | 16,384 | 16,384 |
| MATH-500 | 16,384 | 16,384 |
| AIME mirror subset | 16,384 | 16,384 |
| GPQA Diamond | 4,096 | 16,384 |
| MMLU-Pro | 16,384 | 16,384 |
| HumanEval | 2,048 | 16,384 |

Consequently, `none` versus `low`/`high` changes both effort and output budget for several workloads. Treat those comparisons as contextual. The `low` versus `high` comparison uses the same ceiling.

The retained runner implements a 600-second request timeout, disables SDK retries, and allows at most two recorded attempts per observation, with a two-second backoff for eligible retries. It does not supply `temperature`, `top_p` or a model sampling seed. These are implemented request policies; provider sampling defaults are not measured. The separate execution-order seed was 41056026.

## Concurrency, location and cache state

Each suite process used one serial request lane per model: four model workers for the original comparison, and three for each reasoning-effort run. This is a per-suite limit.

Recorded full-stage attempt timestamps show that the four original suite processes overlapped. Among the included full-stage records, up to four positive-duration attempt intervals overlapped per model across those suites. The included `low`/`high` full-stage runs showed at most one per model. This describes the observed overlap; it is not a configured global limit or a measurement of unrelated account traffic. Intervals rounded to zero duration were excluded from this calculation.

This concurrency difference is another reason to avoid attributing `none` versus `low`/`high` latency differences solely to reasoning effort. The workloads were not run as a common production-load or throughput test.

Bedrock's endpoint region is recorded as `us-east-1`. The direct OpenAI request metadata leaves its region unspecified; this does not identify a physical processing location. Client geographic location, host placement and network route were not recorded. Endpoint region must not be interpreted as client location.

The direct API used automatic caching and Bedrock used implicit default caching. No explicit prompt-cache key or options were supplied. Costs use the provider's recorded cache-read/write token attribution.

Parity, smoke and pilot stages preceded full evaluation. No dedicated cache-warming, cache-flushing, cold-cache guarantee or cross-run cache-isolation procedure is recorded. Initial cache state and cache effects across runs are therefore uncontrolled. For public benchmarks with nested operational stages, some full-stage inputs had appeared in those earlier gates.

## Latency, retries, and cost

The published E2E latency is the harness-observed duration of the **final retained attempt** for each observation. Its population includes terminal failures and locally rejected oversized file inputs. It is not elapsed time from the first attempt through all retries, nor total customer-application or document-pipeline latency.

TTFT is measured from attempt start to the first nonempty streamed output-text delta. Only final attempts with that event contribute a TTFT sample. Responses or failures without an observed text delta are excluded from TTFT, so its sample count can differ from E2E. Both counts are explicit in [run-settings.json](evidence/run-settings.json).

Earlier retry durations and the two-second backoff are excluded from latency percentiles. In contrast, token totals and metered spend include all retained attempts with reported usage. Missing-usage cost bounds are separate from metered spend. Four saved high-Sol ExtractBench attempts were marked completed but lacked usage metadata and were retried; the saved evidence does not establish why that metadata was absent. The conservative allowance for each missing-usage attempt uses 1,000,000 uncached input tokens plus the 16,384-token output ceiling at the frozen long-context rates; this is an upper-bound estimate, not observed usage.

Dataset loading, authentication refresh between observations and grading are outside successful API timing. For ExtractBench, PDF loading precedes the successful API timer; some request construction occurs after that timer starts. Failed-attempt timing can also include local failure handling. These boundaries matter when interpreting very short terminal-failure latencies.

Declared terminal output-limit and file-limit failures remain in the planned quality denominator with zero credit. Classified operational failures could receive one retry. The high-effort ExtractBench run also allowed one retry for a classified response with missing usage. The final attempt determines its latency sample; all retained usage-bearing attempts contribute cost.

## Public dataset selection

All counts below are per model/reasoning configuration. Public upstream revision strings are recorded in full in [dataset-selection.json](evidence/dataset-selection.json). Stable source IDs or exact index mappings identify membership without distributing problems, expected answers, prompts or model responses.

| Workload | Evaluated N | Pinned source and split | Selection |
|---|---:|---|---|
| Banking77 | 3,080 | [PolyAI-LDN](https://github.com/PolyAI-LDN/task-specific-datasets/tree/57ec275d8078af65b7731c2a98be812d844a6d6b), test | Complete official test split; upstream duplicate retained |
| CORD image / OCR | 100 each | [CORD v2](https://huggingface.co/datasets/naver-clova-ix/cord-v2/tree/7f0115a4b758a71d6473b8d085751692da2fef98), test | Same 100 receipts in both input arms |
| ExtractBench | 370 | [ExtractBench](https://huggingface.co/datasets/llamaindex/ExtractBench/tree/f6180e917a050a84582e6366cff85b7dc1e84e58), main set | All short, medium and long tasks |
| GSM8K | 100 | [GSM8K](https://huggingface.co/datasets/openai/gsm8k/tree/740312add88f781978c0658806c59bc2815b9866), main/test | Sample without replacement; Python seed 42 |
| MATH-500 | 100 | [MATH-500](https://huggingface.co/datasets/HuggingFaceH4/MATH-500/tree/6e4ed1a2a79af7d8630a6b768ec859cb5af4d3be), test | Sample without replacement; Python seed 42 |
| AIME mirror subset | 60 | [AIME mirror](https://huggingface.co/datasets/qq8933/AIME_1983_2024/tree/3e2cc86390666c5c756622afc0eeb9e6194496bc), train | Most recent parseable records by the procedure below |
| GPQA Diamond | 198 | [GPQA](https://huggingface.co/datasets/Idavidrein/gpqa/tree/633f5ee89ab8ad4522a9f850766b73f62147ffdd), gpqa_diamond/train | Complete split; deterministic row/option permutation with seed 42 |
| MMLU-Pro | 140 | [MMLU-Pro](https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro/tree/b189ec765aa7ed75c8acfea42df31fdae71f97be), test | 10 questions per category; Python seed 42 |
| HumanEval | 164 | [HumanEval](https://huggingface.co/datasets/openai/openai_humaneval/tree/7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544), test | Complete split in upstream order |

MMLU-Pro groups the pinned test rows by category, visits categories in sorted order, and draws 10 rows from each using a single `random.Random(42)` instance. GSM8K samples zero-based source row indices; the selection metadata includes those indices because the evaluation's GSM8K case IDs are sample ordinals. MATH-500 IDs are the upstream `unique_id` values.

AIME sorts the pinned mirror descending by `(Year, Problem Number)`, preserving upstream order for ties. It skips rows whose stripped answer cannot be parsed as an integer and takes the first 60 parseable records. The evaluated set contains 14 records from 2024, 29 from 2023 and 17 from 2022. “1983–2024” describes the mirror's source span, not the evaluated years. This subset is neither a random sample nor one complete contest year.

Banking77 development gates used training records and CORD gates used validation receipts; full evaluation used test records. Both CORD arms share receipt IDs and expected documents. The image arm uses original image bytes with `detail=high`. The OCR arm contains upstream OCR words, row IDs and quadrilaterals, excluding gold-derived category, group, subgroup and key annotations.

For the four public capability subsets and GPQA/HumanEval, smoke/pilot stages are nested subsets of the full set. They are operational gates, not independent holdouts or extra statistical samples. Stage membership uses seed 5602026, distinct from subset selection and execution-order seeds.

ExtractBench contains 252 short, 98 medium and 20 long tasks: 325 public-record documents and 45 synthetic long-list documents rendered from real layouts. It retains intentionally paired clean/degraded captures. Six examples on its upstream smoke branch are byte-identical main-set examples; they are not independent samples. One 59,470,077-byte PDF exceeds the configured input path's limit and remains in the 370-task denominator as a terminal failure.

Public dataset labels are upstream reference labels. Source access conditions still apply, including GPQA access requirements. Individual GPQA IDs are retained outside this export; the pinned complete split identifies the population. See [benchmark sources and attribution](BENCHMARK_SOURCES.md).

## Synthetic workload design

The two project-authored synthetic sets each contain 48 development cases and 192 evaluated holdout cases. They were created by deterministic code without model generation, random input, or customer data. IDs distinguish development from holdout. Routing scenario families and invoice layout families do not cross those splits.

Routing uses six queues, with 32 holdout records per queue. Each scenario is rendered as direct, multi-intent, negation/distractor and noisy-thread variants. The engineered distribution exercises precedence, temporal context and competing intent; it is not an estimate of production traffic.

Invoice extraction uses four development and 12 holdout layout families. Cases vary headers, row formats, item ordering, decimal/date conventions, addresses, one-to-six line items, discounts, shipping, payments and amount due. Distractors include prior totals, superseded emails, remittance sections, wrapping and OCR-like text.

Every synthetic invoice input is text. OCR-like transcripts test noisy-text handling; they do not test image ingestion, OCR accuracy, handwriting or visual table understanding.

Expected answers are author-seeded, and independent adjudication remains pending. The design separates development and evaluation scenario/layout families, but shared authorship of data, schema and labels can still limit external validity. These results measure agreement with the authored reference under the published scoring rules; they do not independently validate business correctness.

Saved invoice responses were subsequently used for reference corrections and the address-normalization metric revision. These 192 evaluated cases therefore do not provide fresh validation of that post hoc scoring rule, even though their layout families were separated from prompt-development cases.

The selection metadata identifies synthetic record IDs and design, but this package does not distribute the exact synthetic inputs or their generator. It reproduces aggregate reporting rather than a fresh inference run. A production decision requires representative customer data, independent label review where appropriate, and testing on the intended operational path.
