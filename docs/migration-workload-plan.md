# Migration Workload Plan — OpenAI SaaS mini/nano → OpenAI models on Bedrock

> **DRAFT for team discussion — not customer-shareable yet.** Per team policy, this document quotes **no benchmark numbers**. All quantitative claims live in this repo's results files (`performance/results/`, `quality/RESULTS.md`, `parity/results.txt`), which are the source of record once validated.

---

## 1. Objective

Build **one reusable migration pack** — not per-workload discovery — that the field can hand to any customer moving OpenAI SaaS workloads onto Bedrock's OpenAI-compatible (Mantle) endpoint. Per the team Slack proposal, the pack consists of:

- **3–5 representative workload archetypes** (Section 2) that cover the large majority of real migrations,
- a fixed **model matrix** (Section 3): gpt-5.4-mini/nano SaaS baseline vs gpt-5.6-luna and gpt-5.6-terra on Bedrock,
- a fixed **metric set** (Section 4): task success / first-pass accuracy, retries, p50/p95 latency, token usage, caching, and cost per successful task,
- **starting guidance** on which model to test first per archetype (Section 6), with **customer-specific evals remaining the final gate**.

This repo (`benchmarks-openai`) is the **validated, shareable source of record**: the performance, quality, and parity harnesses already here are extended (Section 5) rather than rebuilt per engagement.

---

## 2. Workload archetypes (5 selected)

Selected for coverage of the mini/nano customer base and of the Bedrock endpoint's real feature constraints. Two strong candidates were deliberately deferred: **Codex CLI coding-agent migration** (already covered feature-by-feature by the `bedrock-feature-parity` Codex panel and the codex-on-bedrock guidance repo; its baseline is not mini/nano-class) and **reasoning-heavy research assistant** (already covered by `quality/` AIME/GPQA/HLE evals; also not a mini/nano workload). Both can be added as archetypes 6–7 later without changing the pack's structure.

Every archetype design below is shaped by confirmed endpoint constraints (`parity/results.txt` for gpt-5.4; `parity/results_openai.gpt-5.6-{luna,terra}_us-west-2.txt` from the 2026-07-14 Phase 0 run; `bedrock-feature-parity/docs/DESIGN.md`): **no server-side web_search/file_search/image_generation/computer_use/shell tools; remote MCP must be a same-region Lambda connector; image input via `data:`/`s3://` only; `max_output_tokens` minimum of 16; caching needs `prompt_cache_key`; temperature/top_p rejected on the 5.6 family (reasoning models).**

> **Phase 0 delta (2026-07-14):** `previous_response_id` stateful conversation **passes on gpt-5.6-luna and -terra** (it fails on gpt-5.4, which is what the original constraint was based on). Archetype C below still specifies client-managed history as the portable default, but server-side state is now a validated option on the 5.6 targets — worth a dedicated soak test before relying on it.

### A. High-volume classification & routing (nano-class)

| | |
|---|---|
| **Description** | Ticket triage, intent detection, moderation, model routing — millions of short calls/day, today on gpt-5.4-mini/nano SaaS. |
| **AWS services** | Bedrock Mantle Responses API; SQS/EventBridge ingestion; Lambda workers; DynamoDB results; CloudWatch (`AWS/BedrockMantle` namespace). |
| **Why representative** | The purest mini/nano→luna migration: the luna price tier is explicitly the mini/nano-class replacement (see `bedrock-feature-parity/agent/bench/pricing.py`). Highest-volume, most cost-sensitive customer segment. |
| **API features exercised** | Short-prompt streaming and non-streaming; shared-system-prompt caching (`prompt_cache_key`); temperature-0 determinism (no `seed`/`stop` on Responses API — determinism tested via output equivalence); optional low-token structured output (note `max_output_tokens` ≥ 16). |
| **Primary eval signals** | Classification accuracy (exact match — cheap to grade); p50/p95 latency at ~1k-token inputs; determinism at temperature 0; throughput under throttling; cache hit rate on the shared system prompt; cost per 1k requests. Highly latency- and cost-sensitive. |

### B. Document processing / structured extraction (IDP)

| | |
|---|---|
| **Description** | High-volume typed-field extraction from invoices, claims, KYC documents using strict `json_schema` structured output — a classic mini/nano batch workload moving to luna for cost. |
| **AWS services** | S3 (documents, and the `s3://` image-input scheme); Step Functions + Lambda fan-out; Bedrock Mantle Responses API with `text.format: json_schema` (strict); DynamoDB results; EventBridge triggers. |
| **Why representative** | Structured output is parity-verified on gpt-5.x (`parity/results.txt`); document/image-input constraints (`data:`/`s3://` only, https URLs rejected) directly shape the pipeline design. Batch IDP is one of the most common enterprise LLM workloads. |
| **API features exercised** | Strict json_schema structured output; image/document input via `data:`/`s3://`; caching on the shared extraction instructions (input-heavy cost shape); `max_output_tokens` = 16 minimum edge cases on short field outputs. |
| **Primary eval signals** | Schema-validity rate (JSON parse + schema conformance); field-level exact-match accuracy; image-input reliability (earlier runs saw 500s and post-downscale legibility issues — see `parity/results.txt`); throughput and cost per 1k documents. Latency-insensitive (batch). |

### C. Enterprise chat assistant with governance controls

| | |
|---|---|
| **Description** | Customer-facing or internal chat on mini-class SaaS models, migrating to luna/terra wrapped in AWS enterprise controls: guardrails as an enforcement gate, KMS-encrypted app logs, policy-scoped grounding material. |
| **AWS services** | Bedrock Mantle Responses API; Bedrock Guardrails via `ApplyGuardrail` **app-side before the Responses call** (no server-side hook); CloudWatch Logs (KMS) — required because there are **no S3 model-invocation logs** for Mantle Responses traffic; `AWS/BedrockMantle` CloudWatch metrics; S3 reference material. |
| **Why representative** | Mirrors the bedrock-oai-demo enterprise pattern ("same SDK shape, AWS controls around it") built for OpenAI Technical Success audiences — the most common ask from regulated enterprises. |
| **API features exercised** | Streaming (SSE lifecycle, TTFT-critical); **client-managed multi-turn state** — full-history replay with `store: false`, since `previous_response_id` chaining is not honored; `instructions` field passthrough (honored on gpt-5.x; **silently dropped on gpt-oss** — disqualifies gpt-oss here); shared-system-prompt caching. |
| **Primary eval signals** | TTFT and output-tokens/sec (the `performance/` harness already measures both, streaming); multi-turn coherence under full-history replay; guardrail intervention correctness and refusal behavior (including false-positive rate — see Section 8); cost per conversation. Highly latency-sensitive. |

### D. RAG knowledge assistant over S3 documents (BYO retrieval)

| | |
|---|---|
| **Description** | Retrieval-augmented Q&A over private corpora. **This is the canonical forced-migration archetype**: `file_search` (OpenAI vector stores) is confirmed unsupported on the Bedrock endpoint, so SaaS RAG stacks *must* re-platform retrieval onto AWS while keeping the OpenAI SDK for generation. |
| **AWS services** | S3 (corpus); Amazon Bedrock Knowledge Bases or OpenSearch Serverless (retrieval — brought by the app, not the endpoint); Lambda orchestration; Bedrock Mantle Responses API (generation); Guardrails for grounded-answer policy. |
| **Why representative** | Every SaaS customer using OpenAI vector stores / file_search hits this gap on day one; the pack must show the re-platformed pattern working end to end, not just note the gap. |
| **API features exercised** | Long-context handling (roughly 1k–20k-token stuffed contexts — the exact matrix the `performance/` harness runs); prompt caching on repeated corpus prefixes (cached input billed at a discount — see pricing source of record); structured output for citations; no server-side retrieval tools. |
| **Primary eval signals** | Groundedness/faithfulness and citation accuracy (LLM-judge or exact match); retrieval-augmented QA accuracy vs the SaaS baseline; long-context latency percentiles; cache leverage on corpus prefixes. Moderate latency sensitivity. |

### E. Tool-using agentic workflow (function calling + MCP)

| | |
|---|---|
| **Description** | Multi-step agents (Strands, LangChain, OpenAI Agents SDK) calling business APIs and MCP servers. Second forced re-platform: remote MCP via `server_url` is rejected — MCP tools must be **AWS Lambda connectors** (`connector_id` = Lambda ARN, same region, resource policy for `bedrock.amazonaws.com`). |
| **AWS services** | Lambda (MCP connectors); Bedrock Mantle Responses API; DynamoDB agent state; Step Functions orchestration; Strands/LangChain runtimes (per `sample-openai-on-aws` notebooks). |
| **Why representative** | Agentic tool loops are the fastest-growing workload class; `bedrock-feature-parity` already probes the full tool surface (function, forced `tool_choice`, parallel calls, custom tools, MCP execution evidence via a Lambda fixture), so the pack inherits a validated harness. |
| **API features exercised** | Function tool round-trips (`function_call_output`); forced `tool_choice`; parallel tool calls (and `parallel_tool_calls: false`); custom tool type; Lambda-connector MCP with **execution evidence** (mcp_call completed, not merely accepted); stateless multi-turn replay across tool loops; no web_search (server-side web search is unreliable across runs — treat as unavailable). |
| **Primary eval signals** | Tool-calling accuracy (BFCL grader available in llm-eval-kit); end-to-end multi-step task success rate; retries per task; MCP execution-evidence pass rate; cost shape dominated by multi-turn loops (token usage per completed task). Moderate latency sensitivity. |

---

## 3. Model matrix

| Role | Model | Where | Notes |
|---|---|---|---|
| **Baseline (customer today)** | gpt-5.4-mini | OpenAI SaaS | Migrating customer's current model. **Not available on Bedrock.** |
| **Baseline (customer today)** | gpt-5.4-nano | OpenAI SaaS | Same — nano-class high-volume tier. **Not available on Bedrock.** |
| **Primary target** | openai.gpt-5.6-luna | Bedrock Mantle | The mini/nano-class replacement price tier. First candidate for archetypes A, B, C. |
| **Primary target** | openai.gpt-5.6-terra | Bedrock Mantle | Mid tier. First candidate for archetypes D, E; escalation for A–C. |
| Secondary comparison | openai.gpt-5.6-sol | Bedrock Mantle | Top tier — escalation only, where quality gates fail on terra. |
| Secondary comparison | openai.gpt-5.5, openai.gpt-5.4 (full-size) | Bedrock Mantle | Existing `performance/` and `quality/` results give continuity baselines. |
| Secondary comparison | openai.gpt-oss-120b / 20b | Bedrock (Chat Completions/Converse too) | Extreme cost floor only. Caveats: `instructions` field silently dropped; structured output can hang server-side on 120b — excluded from archetypes B and C by default. |

**Constraints to state up front in every customer conversation:**

- **Only the latest arrivals are evaluable on the AWS side today**: the gpt-5.6 family (luna/terra/sol) plus full-size gpt-5.5/5.4. **gpt-5.4-mini/nano do not exist on Bedrock** — so this is a *migration* comparison across model generations and cost classes, not a same-model A/B (see Section 8 on fairness).
- **Regional availability is asymmetric** and `/v1/models` is region-scoped — every run must start with model discovery in the target region; do not assume. Snapshot from 2026-07-14 discovery: luna + terra in us-west-2 / us-east-1 / us-east-2; **sol and gpt-5.5 not in us-west-2** (us-east-1 / us-east-2 only); eu-west-1 has only gpt-oss.
- API surface differs by family: gpt-5.x is **Responses API only** (Chat Completions/InvokeModel/Converse rejected), base path `/openai/v1`; gpt-oss supports Chat Completions/Converse, base path `/v1`.

---

## 4. Metrics

Metric definitions are fixed pack-wide; the "measured today?" column refers to this repo plus the reusable `bedrock-feature-parity/agent/bench` module.

| Metric | Precise definition | Measured today? |
|---|---|---|
| **Task success** | Per-archetype binary gate per task, judged by an auditable grader: A = label exact match; B = JSON parses + validates against schema + field-level exact match; C = LLM-judge rubric pass + guardrail behavior correct; D = groundedness/citation grader pass; E = end-state assertion (tool loop reached correct final answer, MCP execution evidence present). Success rate = successes / tasks, within MAX_ATTEMPTS. | Partially — `bench/runner.py` defines `success_rate`/`first_pass_rate` with GSM8K/HumanEval graders; **per-archetype graders must be built** (llm-eval-kit `@grader` SDK exists). |
| **First-pass accuracy** | Success on attempt 1 with no retry of any kind (no client retry, no re-prompt, no repair pass). Distinct headline from task success. | Yes in schema (`first_pass_rate` in `bench/runner.py`); needs per-archetype graders as above. |
| **Retries** | Count of additional attempts per task, **with cause taxonomy**: (a) transport (connection reset/read timeout — the `performance/` harness already retries these), (b) API error (4xx/5xx, throttling), (c) content-filter block (tracked separately — see `quality/RESULTS.md`), (d) grader-failure re-prompt (bad JSON, wrong format), (e) truncation (`incomplete` status / `max_output_tokens`). | Partially — retry counts exist in `bench/runner.py`; **cause taxonomy classifier must be built** (the parity suite's normalized error-phrase matching in `bedrock-feature-parity/agent/runner.py` `_classify` is the starting point). |
| **p50/p95 latency** | Two series per config: **TTFT** (request sent → first streamed token) and **e2e** (request sent → final event), reported as p50/p95 over ≥25 runs per (model, archetype, input-size) cell. | Yes — `performance/benchmark_bedrock.py` / `benchmark_openai_saas.py` already measure p50/p95/p99 TTFT and tokens/sec on the 1k/5k/10k/20k matrix, both Bedrock and SaaS. Needs per-archetype prompt shapes added. |
| **Token usage** | From the `usage` object per response: input, output, **reasoning** (where reported), and cached-input tokens; aggregated per *completed task* (all attempts and tool-loop turns included), not per call. | Partially — usage capture exists in both harnesses; **per-task (multi-call) aggregation must be built** for archetypes C/D/E. |
| **Caching** | Prompt-cache **hit rate** = cached input tokens / total input tokens across a session keyed by `prompt_cache_key` (hits are probabilistic even with the key — report the observed rate, don't assume). **Cost effect** = spend delta vs the same run priced with zero cache reads, using the discounted cached-input rate from the pricing source of record. | Partially — `bench` tracks `cache_read_rate` and `pricing.py` holds the cached-input discount; **cache-key session instrumentation per archetype must be built**. |
| **Cost per successful task** | `total spend / number of successful tasks`, where total spend includes **all attempts, retries, and tool-loop turns** (failed attempts are paid for), priced from `bench/pricing.py` (on-demand table incl. gpt-5.6 luna/terra/sol tiers, with `is_known()` staleness badging). SaaS side priced from the published OpenAI mini/nano rates, recorded with retrieval date. | Partially — `cost_per_success` exists in `bench/runner.py`; **SaaS price table + per-archetype wiring must be built**. |

---

## 5. Harness plan

### What exists today

| Asset | Location | Gives us |
|---|---|---|
| Latency matrix harness | `performance/benchmark_bedrock.py`, `benchmark_openai_saas.py`, `generate_report.py` | p50/p95/p99 TTFT + tokens/sec, 1k–20k inputs, 25 runs, **both Bedrock and SaaS** — reuse as-is for the latency columns. |
| Quality evals | `quality/` (AIME 2025, GPQA Diamond, HLE + LLM-judge rescoring; results in `quality/RESULTS.md`) | Head-to-head Mantle-vs-SaaS quality methodology, answer-extraction and judge patterns. |
| Parity gate | `parity/run_parity.py` (34-test Responses-API contract suite; `parity/results.txt` is from an earlier 30-test run) | Go/no-go feature gate per model/region; currently pinned to gpt-5.4 us-west-2 — must be parameterized. |
| Metric schema + graders | `bedrock-feature-parity/agent/bench/` (sibling repo) | `first_pass_rate`, `success_rate`, retries, p50/p95, cached tokens, `cache_read_rate`, `cost_per_success`; pricing table; GSM8K/HumanEval graders. |
| Probe library | `bedrock-feature-parity/agent/probes/` | Bearer-token minting, base-path auto-detection, execution-evidence probes (tools, MCP, caching, structured output) — the classifier and clients are directly reusable. |
| Grader SDK | `llm-eval-kit` (sibling repo) | `@grader` decorator, exact-match/string-similarity/BFCL graders, dataset loaders, `EvalPipeline.run_with_report()`, Lambda deployment. |

### Gap list to build (per archetype)

| Gap | A Classify | B IDP | C Chat | D RAG | E Agentic |
|---|---|---|---|---|---|
| Task dataset (golden set, ~50–200 tasks) | Build (label set) | Build (docs + gold fields) | Build (multi-turn scripts + guardrail cases) | Build (corpus + Q/A + citations) | Build (tool specs + Lambda MCP fixture + end states) |
| Success grader | exact-match (kit built-in) | schema + field grader | LLM-judge rubric + guardrail assert | groundedness/citation grader | end-state assert + BFCL |
| Retry instrumentation + cause taxonomy | shared build, wired per archetype | ← | ← | ← | ← |
| Caching instrumentation (`prompt_cache_key` sessions, hit-rate capture) | shared build | ← | ← | ← | ← |
| Cost accounting (Bedrock + SaaS price tables, per-task aggregation) | shared build | ← | ← | ← | ← |
| SaaS mini/nano baseline runner (same tasks, OpenAI SaaS) | extend `benchmark_openai_saas.py` pattern | ← | ← | ← | ← |
| Archetype-specific infra | — | S3 image fixtures | ApplyGuardrail wiring | KB/OpenSearch retrieval stack | Lambda MCP connector (same-region, resource policy) |

**Composition** (per the harness survey): parity gating from the probe library → latency/cost baselines from `performance/` → task-quality metrics via llm-eval-kit graders → everything reported through the `bench` metric schema, with results committed to this repo.

---

## 6. Starting guidance — which model to test first

> **Starting guidance only.** These heuristics order the first experiment; **customer-specific evals remain the final gate** in every case.

| Archetype | Test first | Escalate to | Rationale |
|---|---|---|---|
| A. Classification/routing | **luna** | terra if accuracy gate fails; gpt-oss-20b only for extreme cost floors (mind the gpt-oss `instructions` drop) | Latency- and cost-dominated; luna is the mini/nano-class price tier. |
| B. IDP / extraction | **luna** | terra for complex layouts/low-confidence fields | Batch, latency-insensitive, cost per 1k docs decides; structured output is verified on gpt-5.x. Avoid gpt-oss-120b (structured-output hangs). |
| C. Enterprise chat | **luna** | terra if judged answer quality or refusal behavior misses the bar | Interactive TTFT matters and per-conversation cost scales with the user base; start cheap, escalate on quality. |
| D. RAG assistant | **terra** | luna as the cost-down experiment once groundedness passes | Groundedness/citation quality is the make-or-break signal and synthesis over long stuffed contexts is harder; establish quality first, then optimize cost. |
| E. Agentic tool use | **terra** | sol only if multi-step success plateaus; luna for simple single-tool loops | Errors compound across tool loops — retries make a "cheaper" model expensive per successful task. |

General rule of thumb: **latency-sensitive / high-volume / simple-output → start luna; multi-step, grounded, or compounding-error workloads → start terra.** Cost comparisons must always use **cost per successful task**, never list price per token.

---

## 7. Execution phases

**Phase 0 — Parity gate on the 5.6 targets.** *(started 2026-07-14)*
`parity/run_parity.py` is parameterized (model, region, base URL from env) and has been run against luna and terra in us-west-2: **23/34 passed each** (`parity/results_openai.gpt-5.6-luna_us-west-2.txt`, `..._terra_...`). Key deltas vs the gpt-5.4 reference: `previous_response_id` statefulness now works; temperature/top_p rejected (reasoning models); the s3:// image check failed only because the test fixture bucket is a redacted placeholder — needs a real bucket to be conclusive. Remaining: repeat in us-east-1/us-east-2, provision a Lambda MCP connector to close the one SKIP, and re-check sol where available. Deliverable: go/no-go per archetype feature dependency.

**Phase 1 — Harness composition (shared build).**
Port the `bench` metric schema into this repo; wire llm-eval-kit graders; build the shared instrumentation from Section 5 (retry taxonomy, cache-session capture, per-task token/cost aggregation, SaaS mini/nano price table + runner). Deliverable: one runner that emits the full Section 4 metric row for any (archetype, model, endpoint) cell.

**Phase 2 — Pilot on the two cheap-to-grade archetypes.**
Build golden sets and run archetypes **A (classification)** and **B (IDP)** end to end: SaaS mini/nano baseline vs luna and terra. These have deterministic graders, so they validate the harness before the LLM-judged archetypes. Deliverable: first two archetype result files committed here + a harness retro.

**Phase 3 — Full matrix.**
Build C/D/E datasets and infra (guardrail wiring, retrieval stack, Lambda MCP connector); run the full archetype × model matrix, ≥25 runs per latency cell and fixed repeat counts for quality cells. Deliverable: complete results in this repo as the source of record; only then do numbers become quotable per team policy.

**Phase 4 — Package the pack.**
Write the field-facing one-pager per archetype (design constraints, starting-guidance row, pointer to results), plus a "bring your own tasks" template so customer-specific evals slot into the same runner. Deliverable: reusable migration pack v1 + feedback loop with the first field engagements.

---

## 8. Open questions / risks

1. **Cost-class comparison fairness.** mini/nano (SaaS) vs luna/terra (Bedrock) are different model generations and tiers — this is a *migration* decision aid, not a like-for-like model benchmark. We must present it that way, and cost-per-successful-task (not per-token price) is the only defensible comparison unit. Open question: do we also report a same-model sanity anchor (full-size gpt-5.4/5.5 Mantle-vs-SaaS, which `performance/` and `quality/` already cover)?
2. **Content-filter false positives.** `quality/RESULTS.md` documents deterministic Mantle-side blocks on benign competition-math problems in one AIME window (not reproducible later, reported upstream). The retry taxonomy tracks filter blocks separately so a recurrence is visible per archetype rather than silently deflating success rates.
3. **Feature-parity gaps that shape (or block) designs.** No server-side retrieval/web tools; stateless-only multi-turn; Lambda-only MCP; `data:`/`s3://`-only image input with a history of 500s and post-downscale legibility issues (risk to archetype B); structured-output hangs on gpt-oss-120b; `instructions` dropped on gpt-oss. Any Phase 0 regression on luna/terra reshapes the affected archetype.
4. **Regional availability.** gpt-5.x listing was us-east-1-only at discovery time across 15 Mantle regions; MCP connectors must be same-region. Multi-region customers need an availability check as step one — do we commit to publishing a per-region availability snapshot in this repo?
5. **Caching variability.** Cache hits are probabilistic even with `prompt_cache_key`; cost-with-caching numbers need observed hit rates over enough runs, plus a stated sensitivity range.
6. **Quality gap vs SaaS.** Earlier full-size runs showed a consistent Mantle-vs-SaaS gap on GPQA-class evals (see `quality/RESULTS.md` for the numbers). If the gap persists on 5.6, "escalate one tier" may be the honest guidance more often than the price tiers suggest.
7. **SaaS baseline logistics.** Prior SaaS quality runs failed on personal API-key quota; the pack needs a funded org key with adequate rate limits before Phase 2.
8. **Pricing staleness.** Bedrock and SaaS price tables both drift; `pricing.py`'s `is_known()` badging should gate any cost figure published from this repo, and SaaS rates need a retrieval date on every result file.
9. **Observability constraints.** No S3 model-invocation logs for Mantle Responses traffic — the pack's operational-evidence story (app-side CloudWatch Logs + `AWS/BedrockMantle` metrics) needs validating as part of archetype C, since customers will ask.
