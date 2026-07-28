# SA Field Guide: Positioning OpenAI gpt-5.6 on Amazon Bedrock vs. gpt-5.4-mini/nano on OpenAI 1P

**Audience:** SAs and account teams advising customers who run gpt-5.4-mini/nano on OpenAI's first-party (1P) API and are evaluating OpenAI models on Amazon Bedrock.
**Source of truth:** `openai-on-aws/benchmarks-openai` (July 2026). Every number in this guide traces to a result JSON in that repo. Do not quote numbers that don't. See Section 6 to reproduce everything yourself.
**Models covered:** Bedrock (BR): `openai.gpt-5.6-luna`, `-terra`, `-sol` — all run with reasoning **effort=none** (thinking disabled). OpenAI 1P: gpt-5.4-mini and gpt-5.4-nano at API defaults.

---

## 1. TL;DR decision framework

| Customer situation | Position | Why (traceable numbers) |
|---|---|---|
| Runs mini/nano today; cost-sensitive; single-turn or simple tasks (classification, extraction, summarization, straightforward Q&A) | **BR luna** | Equal-or-better accuracy than mini on all 6 benchmarks with thinking disabled; on AIME luna is **25% cheaper per success** than mini ($0.0105 vs $0.0139); MATH-500 per-success cost is a tie (-1%). Fastest per-question latency in the harness on every benchmark (577–1,195 ms means at 6-way concurrency). |
| Agentic workloads with chained/multi-hop reasoning; tool outputs are large (RAG chunks, logs, API payloads) | **BR terra** | Terra matched sol's 30/30 agentic success and needed only 4.0 turns on the hardest task (multi_hop) vs mini's 4.8 and luna's 6.4. Fewer turns = superlinearly fewer input tokens re-sent (Section 3). Mid-tier price: $2.75/$16.50 per 1M tokens. |
| Hard-reasoning workloads (math, science, competition-grade problems) where accuracy is the constraint | **BR sol** | Sweeps 4 of 6 benchmarks: AIME 75%, GPQA 68%, MMLU-Pro 82%, HumanEval 97%. List price is 2–5x, but per-success premium narrows to roughly 2x on hard tasks. Fewest agentic turns (3.20 mean). **us-east-1 only.** |
| Extreme cost floor; tolerant of failures and slower responses; tasks are shallow | **1P nano remains defensible — or benchmark luna against it** | Nano wins cost-per-success on 4 benchmarks (as low as $0.0002 on GSM8K) but failed multi_hop 3/5 times and is the slowest in the harness (up to 3.5 s per question). Frame the reliability trade honestly. |
| Wants same-model comparison: "is Bedrock slower than 1P?" | **BR, with data** | BR luna: TTFT 8–33% lower than 1P luna, throughput 15–95% higher tok/s. Tail behavior is the headline: worst p99/p50 TTFT ratio 2.1–2.5x on BR vs **4.6–6.6x on 1P**. |

The one-sentence version: **luna is the default landing zone for mini/nano-class workloads; terra is the upgrade for multi-turn agentic work; sol is the reasoning ceiling for accuracy-constrained tasks; nano keeps a niche at the absolute cost floor if the customer accepts its failure and latency profile.**

---

## 2. Why single-benchmark comparisons mislead: the three lenses

Customers (and competitors' decks) usually show one leaderboard and stop. That produces wrong decisions in both directions. Use three lenses, in order.

### Lens 1: Accuracy (capability ceiling)

Six benchmarks, effort=none on Bedrock, defaults on 1P. Winner in **bold**.

| Benchmark (n) | BR luna | BR terra | BR sol | 1P mini | 1P nano |
|---|---|---|---|---|---|
| AIME 2022–24 (60) | 38% | 58% | **75%** | 37% | 38% |
| GPQA Diamond (198, ungated mirror) | 49% | 56% | **68%** | 43% | 52% |
| MMLU-Pro (140) | 61% | 66% | **82%** | 59% | 62% |
| MATH-500 (100) | 88% | 92% | **94%** | 84% | 84% |
| GSM8K (100, saturated) | **97%** | 96% | 95% | 96% | 95% |
| HumanEval (164, official tests executed) | 95% | 96% | **97%** | 95% | 90% |

**CI caveat (repeat this to customers):** at these sample sizes, confidence intervals are roughly ±8–12 points. Any gap inside that band is a tie. So "luna 38% vs mini 37% on AIME" is a tie; "sol 75% vs mini 37%" is not.

What this lens tells you: sol is a different capability class (sweeps 4/6). Luna, running with **thinking disabled**, ties or edges mini everywhere. What it doesn't tell you: what any of this costs.

### Lens 2: Cost per success (unit economics of correctness)

Accuracy divided into spend changes the ranking. Definition: **total spend across all attempts ÷ number of correct answers** (Section 4). Winner in **bold**; last column is luna vs mini.

| Benchmark | luna | terra | sol | mini | nano | luna vs mini |
|---|---|---|---|---|---|---|
| AIME | $0.0105 | $0.0234 | $0.0299 | $0.0139 | **$0.0050** | **25% cheaper** |
| GPQA | $0.0006 | $0.0014 | $0.0025 | **$0.0005** | $0.0008 | +22% |
| MMLU-Pro | $0.0005 | $0.0011 | $0.0018 | $0.0004 | **$0.0003** | +34% |
| MATH-500 | $0.0016 | $0.0041 | $0.0069 | $0.0017 | **$0.0006** | −1% (tie) |
| GSM8K | $0.0009 | $0.0019 | $0.0038 | $0.0007 | **$0.0002** | +20% |
| HumanEval | $0.0012 | $0.0026 | $0.0045 | $0.0009 | **$0.0003** | +29% |

Read this carefully before quoting it:

- **Luna's list price is ~47% above mini's** ($1.10/$6.60 vs $0.75/$4.50 per 1M in/out), yet on the hardest benchmark (AIME) luna is 25% *cheaper per correct answer*, and on MATH-500 it's a tie. List price is not unit cost. Token volume — especially default-reasoning output tokens on 1P — dominates.
- **Nano wins this lens 4 of 6 times.** Be honest about that. Nano is legitimately the cost-per-success floor when the task is within its capability and failures are cheap. Its weaknesses show up in the third lens.
- **Sol's premium compresses on hard tasks.** List price is 2–5x luna/terra; per-success on AIME it's $0.0299 vs luna's $0.0105 — roughly 2x mini's $0.0139 — because sol converts far more attempts into successes (75% vs 37–38%).

### Real-work validation: GDPval professional deliverables

Benchmarks measure question-answering; customers ship *work products*. **GDPval** (`quality/gdpval_eval.py`, 24 stratified tasks from openai/gdpval, arXiv:2510.04374) tests exactly that: real occupational deliverables — compliance briefs, financial plans, nursing protocols, purchasing analyses — written by professionals with 14+ years of experience, each with a human-authored rubric. We run the text-only slice (tasks with no reference files), same 24 tasks per model, fixed seed. Grading: a rubric-anchored LLM judge (gpt-5.5 — OpenAI family, **not** a candidate arm) marks every rubric item; a deliverable **passes** at ≥0.7 of weighted rubric points. The official GDPval grading is blind pairwise comparison by human domain experts — these scores are NOT comparable to the paper's win rates; within-run comparisons only.

| Model | Mean rubric fraction | Pass rate (≥0.7) | Cost/task | Cost/passing deliverable |
|---|---|---|---|---|
| BR sol | **0.723** | **17/24 (71%)** | $0.145 | $0.205 |
| BR terra | 0.704 | 13/24 (54%) | $0.069 | $0.127 |
| BR luna | 0.703 | 15/24 (63%) | $0.029 | $0.046 |
| 1P mini | 0.660 | 11/24 (46%) | $0.012 | $0.026 |
| 1P nano | 0.603 | 10/24 (42%) | $0.004 | $0.010 |

How to read it — and what *not* to claim:

- **All three 5.6 arms out-write mini and nano, with thinking disabled.** Luna beats mini head-to-head on 15 of 24 tasks (4 losses, 5 ties) and passes 15 deliverables to mini's 11. The gap concentrates in the regulated professions (lawyers, nurses, financial advisors) where rubrics demand specific caveats and structure.
- **This is a quality argument, not a cost inversion.** GDPval is single-call — no turns, no compounding context — so unlike DeepSearchQA the cost ranking follows list price: mini/nano stay cheaper per passing deliverable. What 5.6 buys here is pass *rate*, not pass *price*. The pivot question is what a failed deliverable costs: if a sub-bar draft means human rework, price that rework into the mini/nano columns before deciding.
- **Luna is the value point.** Statistically tied with terra and within 2 points of sol on mean rubric fraction, at 22% of sol's cost per passing deliverable ($0.046 vs $0.205). Terra's agentic turn advantage (Section 3) simply doesn't apply to single-call writing — one more reminder that model choice is workload-specific.
- **Caveats, always attached:** n=24 tasks; pass-rate gaps under ~20 points are directional at this sample size (mean rubric fraction, averaged over 16–83 rubric items per task, is the steadier metric). The 8,192-token output cap truncated more 5.6 deliverables than 1P ones (luna 3, terra 4, sol 6 vs mini 0, nano 1), so the 5.6 scores are floors. Judge-serving stability was cross-checked: the two arms fully judged on both the 1P and Bedrock serving paths of gpt-5.5 moved ≤0.007 mean rubric fraction. Result files: `quality/results/gdpval_*_judged.json`.

### Lens 3: Trajectory cost (what agents actually spend)

Single-call benchmarks price one request. Real agentic workloads are trajectories: N turns, each re-sending the growing conversation, each adding a round trip, with failed trajectories burning their full budget *and* triggering retries or human review. This is the lens most decks skip, and it's where model rankings reshuffle again — Section 3 covers it in full.

**The pitch discipline:** never let a customer (or your own deck) decide on Lens 1 alone. A model that wins the leaderboard can lose the invoice, and vice versa.

---

## 3. The multi-turn economics argument — done honestly

### 3a. Why turns compound cost

Every turn of a tool-calling agent re-sends the entire conversation so far: system prompt, all prior assistant turns, all prior tool results. If each turn adds a roughly constant amount of new content, input tokens paid across the trajectory grow with the *square* of the turn count (turn 1 pays 1 unit of context, turn 2 pays ~2, turn N pays ~N; the sum is ~N(N+1)/2). Superlinear input-token growth means a model that resolves a task in 3 turns instead of 5 doesn't save 40% — it saves substantially more, before you even count the two extra full round-trip latencies. And a *failed* trajectory is the worst case: it burns its entire token budget and produces a retry or a human-review ticket on top.

So "which model takes fewer turns?" is an economic question, not just a UX one.

### 3b. What our agentic data shows

Harness: `quality/agentic_evals.py` — 6 tool-calling tasks × 5 repeats each, deterministic mock backends, max 12 turns, a real execute-and-feedback loop (the model must call tools, read results, and decide the next action).

| Model | Success | Mean turns | Cost/success | Mean wall time |
|---|---|---|---|---|
| BR luna | 30/30 | 3.73 | $0.00132 | 2.36 s |
| BR terra | 30/30 | 3.33 | $0.00293 | 2.45 s |
| BR sol | 30/30 | 3.20 | $0.00587 | 9.85 s |
| 1P mini | 30/30 | 3.47 | $0.000832 | 4.66 s |
| 1P nano | 27/30 | 3.77 | $0.000301 | 5.66 s |

The per-task breakdown is where the real story is. **5 of the 6 tasks** (order_totals, warm_city, config_hunt, ticket_triage, flaky_tool) converge to identical turn counts — 3–4 turns — across *all five models*. On easy and moderate tool-calling work, model choice barely moves turn count.

The separator is **multi_hop** (chained knowledge-graph lookups, where each answer feeds the next query):

| Model | multi_hop mean turns | multi_hop success |
|---|---|---|
| sol | 3.2 | 5/5 |
| terra | 4.0 | 5/5 |
| mini | 4.8 | 5/5 |
| luna | 6.4 | 5/5 |
| nano | 6.6 | **2/5** |

### 3b-ii. Real-web validation: DeepSearchQA

The mock-tool suite controls for tool cost; **DeepSearchQA** (`quality/deepsearchqa/`, 20 stratified questions from google/deepsearchqa, arXiv:2601.20975) removes that control: a live web_search + fetch_page agent loop (Tavily-backed, disk-cached for reproducibility), budgets of 8 searches / 6 fetches / 14 turns per question. Grading is two-layer to contain same-family judge bias: a deterministic string-match pre-pass settles what it can, and a frozen autorater prompt on gpt-5.5 (an OpenAI model that is *not* a candidate arm) grades the rest; every file records the layer-agreement rate (0.82–1.00 across arms). Absolute scores are not leaderboard-comparable (the paper prescribes a gemini judge); within-run comparisons are the point.

| Model | Mean F1 | Pass (F1≥0.7) | Mean turns | Mean cost/question | Cost/pass |
|---|---|---|---|---|---|
| BR terra | **0.717** | **12/20** | **5.55** | $0.129 | $0.214 |
| BR sol | 0.690 | 13/20 | 7.95 | $0.620 | $0.953 |
| BR luna | 0.547 | 10/20 | 6.45 | $0.071 | $0.143 |
| 1P mini | 0.459 | 7/20 | 8.40 | $0.089 | $0.254 |
| 1P nano | 0.406 | 7/20 | 5.60 | $0.014 | $0.040 |

This is the strongest version of the turns argument in our data, because it's a real workload: **terra needs 34% fewer turns than mini (5.55 vs 8.40) while scoring 26 F1 points higher** — and the turn gap makes terra's *trajectory* economics competitive despite a ~3.7× per-token price gap: cost-per-pass is terra $0.214 vs mini $0.254. Mini's extra turns (8.4, the most of any arm, with the most searches and fetches) are re-search loops that inflate its input tokens to 115k/question vs terra's 44k — the compounding-context effect measured live. Luna beats mini on every axis here: higher F1 (0.547 vs 0.459), fewer turns, lower cost/question, and 44% lower cost-per-pass. Nano stays the floor-price option ($0.040/pass) but passes only 35% of questions — fine for tolerant workloads, disqualifying for research agents whose failures cost human review time. Caveat: n=20 per arm; treat gaps under ~15 F1 points as directional.

### 3c. The honest version of the claim

State this precisely, because the sloppy version is factually wrong:

- **TRUE:** terra and sol take fewer turns than mini and nano on chained-reasoning tasks (terra 4.0 and sol 3.2 vs mini 4.8, nano 6.6 on multi_hop). On the live-web DeepSearchQA run the gap widens: terra 5.55 turns vs mini 8.40 — 34% fewer — with mini's extra turns showing up directly as 2.6× the input tokens per question (115k vs 44k). Given the superlinear math above, that turn advantage compounds into real input-token and latency savings on exactly the workloads where trajectories are long.
- **MIXED — scope it:** "luna takes fewer turns than mini" is true on the live-web workload (6.45 vs 8.40 on DeepSearchQA, where luna also wins F1 0.547 vs 0.459) but **false** on the mock multi_hop task (luna 6.4 vs mini 4.8). The safe formulation: *on real research workloads our data shows luna needing fewer turns and scoring higher than mini; on isolated chained-lookup micro-tasks it does not.* Quote the workload with the number, or a customer re-run will surface the discrepancy.
- **Nano's failure mode matters most here:** 2/5 on multi_hop. In production, those 3 failures each burn a full trajectory budget and generate a retry or escalation. Nano's $0.000301 cost-per-success already absorbs those failures in this harness, but only because mock-tool trajectories are cheap (see Section 5).

### 3d. When this matters: the task-complexity threshold

Turn counts converge on easy tasks; differentiation appears **only when the task requires chained inference** — each step's output determines the next step's input. Discovery question for the customer: "Do your agent traces show tool calls whose arguments depend on earlier tool *results*, or are your tools mostly independent lookups?" Independent lookups → luna's economics win. Chained inference at volume → terra (or sol if accuracy-critical) earns its price tier through shorter trajectories.

**Directionality caveat, always stated:** n=5 repeats per task. These results are directional, not conclusive. Position them as "our harness shows X; run it on your tasks" (Section 6), never as a guarantee.

---

## 4. Cost-per-success methodology (reusable with any customer)

The formula account teams should standardize on:

```
cost_per_success = total_spend_all_attempts / number_of_correct_answers
```

Total spend includes *every* attempt — wrong answers cost real money and belong in the numerator. Dividing by successes (not attempts) is the point: it prices the unit the customer actually wants, a correct output.

**Worked example — AIME 2022–24 (60 questions):**

1. Run all 60 questions through each model; sum the actual token spend (input + output at list price) across all 60 attempts.
2. Count correct answers: luna scored 38%, mini 37% — statistically a tie on accuracy (inside the ±8–12 point CI band).
3. Divide: luna lands at **$0.0105 per correct answer**; mini at **$0.0139**.
4. Result: luna is **25% cheaper per success** — despite a list price ~47% higher ($1.10/$6.60 vs $0.75/$4.50 per 1M tokens).

Why does the model with the higher sticker price win? Because mini runs at 1P defaults, which include reasoning, and reasoning tokens are billed output tokens on every attempt — right and wrong. Luna at effort=none produces its answers with far less output volume. On a hard benchmark where both models fail often, mini pays reasoning-token freight on ~63% of attempts that yield nothing.

**How to run this with a customer:** take 50–100 *of their* representative tasks with known-good answers, run each candidate model, log spend per attempt from the usage fields, grade the outputs, apply the formula. The repo's harness does exactly this and emits result JSONs (Section 6). One afternoon of work produces a defensible per-success number in the customer's own domain — far more persuasive than any leaderboard.

Two rules when presenting it: (a) always show accuracy *and* cost-per-success together — a cheap-per-success model with unacceptable absolute accuracy (nano at 38% on AIME) may still fail the business requirement; (b) state sample sizes and the CI band on the same slide.

---

## 5. Gotchas — what to watch out for before quoting anything

1. **Confidence intervals.** ±8–12 points at these sample sizes (60–198 questions). Gaps inside that band are ties. Luna 38% vs mini 37% on AIME is a tie; do not present it as a win. Sol's sweeps (e.g., 75% vs 37% on AIME) are outside the band and safe to present.
2. **Saturated benchmarks prove nothing.** GSM8K scores span 95–97% across all five models — the benchmark is saturated and cannot differentiate them. If a customer's deck leans on GSM8K, redirect to AIME/GPQA/MMLU-Pro.
3. **Reasoning-effort asymmetry.** All Bedrock 5.6 numbers here are **effort=none** (thinking disabled); 1P mini/nano ran at defaults. This is deliberately conservative for Bedrock — luna's accuracy parity with mini comes *without* spending reasoning tokens — but it means these numbers are a floor, not a ceiling, for 5.6 with effort enabled. Say so explicitly; never let a customer believe they're comparing like-for-like reasoning configs.
4. **Region availability.** Sol runs in **us-east-1 only** — it is not in us-west-2. Check region fit (data residency, latency to the customer's stack) before positioning sol.
5. **Parameter rejection on 5.6.** The 5.6 family rejects `temperature`/`top_p`. Customers lifting code from mini/nano integrations will hit API errors until they strip those parameters. Flag it in migration planning; it's a five-minute fix that looks like an outage if nobody warned them.
6. **Mock-tool vs real-tool cost amplification.** Our agentic harness uses deterministic mock backends with short tool results. Real workloads return big payloads — RAG chunks, log excerpts, API responses — and because every turn re-sends all prior tool results, large outputs amplify per-turn cost far beyond what the harness shows. The *turn-count rankings* transfer; the absolute dollar figures do not.
7. **Latency tails, not just medians.** Median TTFT looks fine on both platforms; the tail is where SLOs die. Worst p99/p50 TTFT ratio: 2.1–2.5x on Bedrock vs **4.6–6.6x on 1P**. For latency-sensitive customers, this is often the most compelling same-model argument for Bedrock. Also note: TTFT is roughly flat vs input size (~1.1–1.7 s for luna/terra), and sol's TTFT is 3–10 s — it is a deep reasoner; do not position sol for interactive-latency use cases (though BR sol runs roughly half the TTFT of 1P sol at small inputs).
8. **GPQA provenance.** Our GPQA Diamond numbers use the ungated CC-BY-4.0 mirror (`hendrydong/gpqa_diamond_mc`); the canonical gated set is pending access approval. Disclose this if a customer compares against published GPQA figures.
9. **Agentic n=5.** Five repeats per task. Directional. Every slide using the agentic data carries that caveat.
10. **The cardinal rule: no unvalidated numbers.** Team policy is that every quoted figure traces to a result JSON in the repo. If a number isn't in a result file, it doesn't go in a deck, an email, or a Highspot page. A customer re-running our public harness and getting a different answer costs more credibility than any slide ever earned.

---

## 6. Run the evidence yourself (and have the customer run it too)

Everything above reproduces from `openai-on-aws/benchmarks-openai` with your own credentials — the harness never stores 1P keys.

**Full performance suite (latency + throughput, BR vs 1P, matched configs):**

```bash
git clone <internal-remote>/openai-on-aws/benchmarks-openai && cd benchmarks-openai
export OPENAI_API_KEY=...        # bring your own; never committed or stored
# AWS credentials via your standard profile/SSO
./run_all.sh                     # one command: runs the matrix, writes result JSONs
```

**Accuracy / cost-per-success (the six quality benchmarks):**

```bash
python quality/quick_evals.py    # AIME, GPQA, MMLU-Pro, MATH-500, GSM8K, HumanEval
```

**Agentic multi-turn (turn counts, trajectory cost, success rates):**

```bash
python quality/agentic_evals.py  # 6 tasks x 5 repeats, deterministic mock backends, max 12 turns
```

**Professional-work deliverables (GDPval text-only slice, rubric-judged):**

```bash
python quality/gdpval_eval.py --backend mantle --model openai.gpt-5.6-luna --effort none
python quality/gdpval_eval.py --judge-only --judge-backend mantle  # gpt-5.5 judge; one backend per comparison
```

All harnesses emit result JSONs; every table in this guide is derived from those files. The highest-leverage SA move: swap in 50–100 of the *customer's* tasks and re-run — the methodology in Section 4 then produces per-success economics in their own domain, on their own account, with numbers they generated themselves.

---

## 7. Customer conversation guide: five discovery questions

**Q1. "What does a failure cost you — and what happens after one?"**
If a wrong answer is silently discarded (batch enrichment, best-effort tagging): nano's cost floor is defensible ($0.0002–$0.0050 per success across our benchmarks). If a failure triggers retries, human review, or a bad customer interaction: weight reliability — luna and terra went 30/30 in the agentic harness while nano dropped 3/30, all on the chained-reasoning task. Failure cost is the single strongest predictor of which lens (Section 2) should dominate the decision.

**Q2. "Are your agent's tool calls chained — do later calls depend on earlier results?"**
Independent lookups: turn counts converge across models (5 of 6 of our tasks landed at identical 3–4 turns for everyone), so buy on cost-per-success → **luna**. Chained inference: turn counts diverge (sol 3.2 / terra 4.0 / mini 4.8 / luna 6.4 / nano 6.6 on multi_hop) and turn count drives superlinear input-token cost → **terra**, or **sol** if accuracy is also binding. Directional data (n=5) — offer to re-run on their traces.

**Q3. "How hard is your hardest recurring task, and is accuracy a gate or a dial?"**
Accuracy as a hard gate on genuinely hard work (competition-grade math/science, deep code correctness): **sol** — it sweeps AIME (75%), GPQA (68%), MMLU-Pro (82%), HumanEval (97%), and its per-success premium narrows to roughly 2x on hard tasks. Accuracy as a dial on moderate work: **luna** matches mini everywhere at equal-or-better per-success cost on the hard end (AIME 25% cheaper, MATH-500 tie).

**Q4. "What are your latency SLOs — and are they on the median or the tail?"**
Interactive p99 SLOs: Bedrock's tail is the story — worst p99/p50 TTFT of 2.1–2.5x vs 4.6–6.6x on 1P, plus BR luna at 8–33% lower TTFT and 15–95% higher tok/s than 1P luna. Recommend **luna or terra** (TTFT ~1.1–1.7 s, roughly flat vs input size). Sol's 3–10 s TTFT rules it out for interactive paths; keep it for async/batch reasoning.

**Q5. "How big are your tool outputs and prompts?"**
Large per-turn payloads (RAG chunks, logs) amplify the cost of every extra turn — our mock-tool harness *understates* this. Big payloads plus chained tasks strengthen the **terra** case (fewer turns on exactly those tasks) beyond what the raw harness dollars show. Also check region and config fit here: **sol is us-east-1 only**, and 5.6 rejects `temperature`/`top_p` — surface both before the pilot, not during it.

**Closing motion:** don't argue from our tables — hand them the repo. One command (`./run_all.sh` plus the two eval scripts) on their own tasks, their own keys, their own account. The methodology is the product; the numbers are just our run of it.

---

*Maintained in `docs/sa-guide-model-comparison.md`, `openai-on-aws/benchmarks-openai`. Data as of July 2026; re-run the harnesses before quoting numbers externally or after any model/pricing update.*