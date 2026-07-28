# The per-token price is the wrong number: comparing LLMs on cost per successful task

> **Draft for review.** Co-author candidates: AWS + OpenAI product. Every number in this post traces to a timestamped result JSON in [`openai-on-aws/benchmarks-openai`](https://github.com/openai-on-aws/benchmarks-openai) (July 2026 runs). Nothing here is hand-copied from memory.

## The sticker-price trap

Ask most platform teams how they compare LLMs on cost and you will get the same answer: dollars per million tokens. It is the number on every pricing page, so it becomes the number in every spreadsheet.

But production workloads do not buy tokens. They buy *outcomes*: a resolved support ticket, a passing patch, a correct extraction. Between the pricing page and the outcome sit three multipliers the sticker price ignores:

1. **Accuracy.** A wrong answer costs the same tokens as a right one, then costs you again in retries or human review.
2. **Token efficiency.** Two models at different unit prices can produce the same answer with very different token counts.
3. **Trajectory length.** In agentic, tool-calling workloads, each turn re-sends the whole conversation, so input tokens grow superlinearly with turn count — and every extra turn adds a full round trip of latency.

We built an open-source harness to measure all three across five models — three OpenAI models on Amazon Bedrock (`openai.gpt-5.6-luna`, `-terra`, `-sol`) and two on OpenAI's first-party API (`gpt-5.4-mini`, `gpt-5.4-nano`) — because most teams evaluating Bedrock today are running mini or nano on the 1P API and want to know whether the newer models are worth it. The short version: sometimes yes, sometimes no, and the per-token price predicts almost none of it.

## Three lenses, three different rankings

**Lens 1 — single-call accuracy.** On AIME competition math (2022–24, 60 questions), sol scores 75% where mini scores 37%. That is not a gap retries can close cheaply: at 37% accuracy, you expect nearly three attempts per success.

**Lens 2 — cost per successful answer.** Total spend across all attempts, divided by correct answers. On that same AIME set, luna comes out at **$0.0105 per success versus mini's $0.0139 — 25% cheaper** — despite luna's list price being roughly 1.5x higher ($1.10/$6.60 vs $0.75/$4.50 per 1M input/output tokens).

**Lens 3 — multi-turn trajectory cost.** On our hardest agentic task (chained knowledge-graph lookups), sol finishes in a mean of 3.2 turns while nano takes 6.6 — and nano only completes it in 2 of 5 attempts. Turn count, not token price, dominates the bill for these workloads.

Three lenses, three different winners. Here is how we measured them.

## The experiment

The harness ([`openai-on-aws/benchmarks-openai`](https://github.com/openai-on-aws/benchmarks-openai)) runs one identical code path — the Responses API, streaming — against both backends, flipping only `--backend bedrock` or `--backend openai`. Bring your own keys; nothing is bundled.

- **Quality:** six benchmarks — AIME 2022–24 (60 questions), GPQA Diamond (198, via an ungated CC-BY-4.0 mirror), MMLU-Pro (140), MATH-500 (100), GSM8K (100), and HumanEval (164, scored by actually executing the official test suites, not string-matching). Same seeded question sets for every model.
- **Cost per success:** every attempt's token usage is logged and priced at list; total spend divided by correct answers.
- **Agentic multi-turn (new):** six tool-calling tasks x 5 repeats per model, with deterministic mock tool backends, a real execute-and-feedback loop, and a 12-turn cap (`quality/agentic_evals.py`). The model must actually call tools, read results, and decide what to do next.
- **Latency:** matched single-call configs on both backends, p50/p95/p99.

Bedrock models ran with reasoning effort set to `none` (thinking disabled); 1P models ran at defaults. All results below are reproducible from the repo — that is the point of publishing it.

### Single-call accuracy

| Benchmark | luna | terra | sol | mini | nano |
|---|---|---|---|---|---|
| AIME 2022–24 | 38% | 58% | **75%** | 37% | 38% |
| GPQA Diamond | 49% | 56% | **68%** | 43% | 52% |
| MMLU-Pro | 61% | 66% | **82%** | 59% | 62% |
| MATH-500 | 88% | 92% | **94%** | 84% | 84% |
| GSM8K | **97%** | 96% | 95% | 96% | 95% |
| HumanEval | 95% | 96% | **97%** | 95% | 90% |

Confidence intervals at these sample sizes are ±8–12 points, so treat anything inside that band as a tie. Read that way: sol clearly leads on the four hardest benchmarks; GSM8K is saturated (everyone ties); luna is at or above mini everywhere.

### Cost per successful answer

| Benchmark | luna | terra | sol | mini | nano | luna vs mini |
|---|---|---|---|---|---|---|
| AIME | $0.0105 | $0.0234 | $0.0299 | $0.0139 | **$0.0050** | **25% cheaper** |
| GPQA | $0.0006 | $0.0014 | $0.0025 | **$0.0005** | $0.0008 | +22% |
| MMLU-Pro | $0.0005 | $0.0011 | $0.0018 | $0.0004 | **$0.0003** | +34% |
| MATH-500 | $0.0016 | $0.0041 | $0.0069 | $0.0017 | **$0.0006** | −1% (tie) |
| GSM8K | $0.0009 | $0.0019 | $0.0038 | $0.0007 | **$0.0002** | +20% |
| HumanEval | $0.0012 | $0.0026 | $0.0045 | $0.0009 | **$0.0003** | +29% |

Honest reading: on single-call benchmarks, **nano is the cheapest path to a correct answer on five of six suites**. Luna lands within ±34% of mini everywhere and inverts on AIME. Sol's 2–5x list-price premium narrows to roughly 2x per success on the hard tasks, because it wastes far fewer attempts.

## Findings, honestly framed

### 1. The AIME inversion: token efficiency beats token price

Luna and mini score a statistical tie on AIME (38% vs 37%), yet luna costs 25% less per success while charging ~1.5x more per token. The arithmetic only works one way: luna reached the same answers with substantially fewer billed tokens per attempt. Unit price told you luna was more expensive; the invoice says otherwise. This is the cleanest single demonstration that $/1M tokens is not a decision variable — it is one input to one.

### 2. Agentic turns: convergence on easy tasks, separation on hard ones

The multi-turn results are the most interesting and the easiest to over-claim, so here is the full table:

| Model | Success | Mean turns | Cost / success | Mean wall time |
|---|---|---|---|---|
| luna (BR) | 30/30 | 3.73 | $0.00132 | 2.36s |
| terra (BR) | 30/30 | 3.33 | $0.00293 | 2.45s |
| sol (BR) | 30/30 | 3.20 | $0.00587 | 9.85s |
| mini (1P) | 30/30 | 3.47 | $0.000832 | 4.66s |
| nano (1P) | 27/30 | 3.77 | $0.000301 | 5.66s |

On five of six tasks (order totals, weather lookup, config search, ticket triage, and a flaky-tool retry task), *every* model converges to essentially identical turn counts — 3 to 4 turns. If your agent's tasks look like these, model choice barely moves trajectory shape.

The separator is `multi_hop`, which requires chaining knowledge-graph lookups where each query depends on the previous result. Mean turns: **sol 3.2, terra 4.0, mini 4.8, luna 6.4, nano 6.6** — and nano succeeded on only 2 of 5 repeats (every other model went 5/5).

Two things are true at once, and we want to state both plainly:

- The "smarter models take fewer turns" hypothesis **holds for sol and terra** versus mini and nano. Sol plans the chain and executes it in a third fewer turns than mini (3.2 vs 4.8), about half the turns of nano.
- It **does not hold for luna on this task**. Luna took *more* turns than mini on the mock hard task (6.4 vs 4.8) — it explored less efficiently, though it always got there in the end. (Keep reading: on the live-web benchmark below, luna flips this and beats mini on turns *and* accuracy — turn efficiency is a property of the model-workload pair, not the model alone.)

Why turns matter economically: each turn re-sends the entire conversation, so input tokens compound superlinearly as trajectories lengthen, and each turn adds a full network round trip. Our mock tools return short results, which *understates* the effect — real workloads that stuff RAG chunks or log excerpts into tool results amplify per-turn cost far more. And at n=5 repeats per task, the turn-count numbers are directional, not conclusive.

So we removed the mock-tool control and measured the amplified version directly.

### 2b. The same effect on the live web: DeepSearchQA

We ran a 20-question stratified sample of [DeepSearchQA](https://arxiv.org/abs/2601.20975) — multi-step web-research questions — through a real agent loop: live `web_search` and `fetch_page` tools (disk-cached for reproducibility), budgets of 8 searches, 6 fetches, and 14 turns per question. Grading uses the paper's frozen autorater prompt with a two-layer design (a deterministic string-match pass first, then an LLM judge that is not one of the candidate models; per-arm agreement between layers ran 0.82–1.00).

| Model | Mean F1 | Pass rate (F1≥0.7) | Mean turns | Input tokens/question | Cost/question | Cost/pass |
|---|---|---|---|---|---|---|
| terra (BR) | **0.717** | **60%** | **5.55** | 44k | $0.129 | $0.214 |
| sol (BR) | 0.690 | 65% | 7.95 | 109k | $0.620 | $0.953 |
| luna (BR) | 0.547 | 50% | 6.45 | 62k | $0.071 | $0.143 |
| mini (1P) | 0.459 | 35% | 8.40 | 115k | $0.089 | $0.254 |
| nano (1P) | 0.406 | 35% | 5.60 | 67k | $0.014 | $0.040 |

Here the turn effect stops being theoretical. Mini took the most turns of any arm (8.4) — mostly re-search loops — and every extra turn re-sent a context stuffed with search results, driving its input tokens to 115k per question, 2.6× terra's 44k. That's how a model with a ~3.7× lower per-token price ends up with a *higher* cost-per-pass than terra ($0.254 vs $0.214). Luna, which lost the turn race on the mock micro-task, wins it here: fewer turns than mini, 9 F1 points higher, and 44% cheaper per passing answer. Turn efficiency is workload-dependent — which is exactly why you should measure it on trajectories that look like yours.

(Caveats: n=20 questions per arm, so treat small F1 gaps as directional; absolute scores aren't comparable to the paper's leaderboard because our judge differs from the one it prescribes.)

### 3. Reliability is a cost line, not a footnote

Nano's 3 failures out of 30 agentic runs are not free. A failed trajectory burns its entire token budget *and* triggers whatever comes next — an automated retry (double the cost) or a human review (dwarfing the model bill entirely). Cost-per-success accounting captures the first effect; your incident process pays the second.

Latency tails behave the same way. In our matched single-call runs, the worst-case TTFT p99/p50 ratio was 2.1–2.5x on Bedrock versus 4.6–6.6x on the 1P API — and for the same model, Bedrock luna showed 8–33% lower TTFT and 15–95% higher tokens/sec than 1P luna, with terra tied at small inputs and ahead 8–21% at 10k/20k tokens. In the eval harness itself (6-way concurrency), luna was the fastest model on every benchmark (577–1,195ms mean per question); nano ran up to 3.5s. Sol is a deep reasoner with 3–10s TTFT — though roughly half its 1P equivalent at small inputs on Bedrock. If a timeout counts as a failure in your system, tail latency is accuracy.

## A back-of-envelope model you can apply

Expected cost per successful task:

```
cost/success ≈ [ Σ over turns t of (C₀ + g·(t−1)) × p_in  +  T × o × p_out ] / s
```

where `C₀` = initial context (system prompt + tools + request), `g` = context growth per turn (model output + tool result), `T` = turns, `o` = output tokens per turn, `p_in`/`p_out` = prices, `s` = success rate.

**Worked example** (illustrative parameters, chosen to be modest: `C₀` = 2,000 tokens, `g` = 600, `o` = 150):

- *Model A* — mini-like: 4 turns, 100% success. Input = 2,000 + 2,600 + 3,200 + 3,800 = 11,600 tokens; output = 600. At mini prices: (11,600 × $0.75 + 600 × $4.50)/1M ≈ **$0.0114 per success**.
- *Model B* — nano-like on a hard task: 7 turns, 40% success. Input = 26,600 tokens; output = 1,050. At nano prices: ≈ $0.0066 per attempt, but ÷ 0.4 = **$0.0166 per success** — about 46% *more* than Model A, from a model whose list price is roughly a quarter of A's.

Two structural effects to notice. Going from 4 to 6 turns (+50%) grows input tokens from 11,600 to 21,000 (+81%) — that is the superlinearity. And dividing by success rate is the retry tax: at 40% success, every task effectively costs 2.5 attempts. Plug in your own `C₀` and `g` — if your tool results are RAG chunks measured in thousands of tokens, `g` dominates everything else in the formula.

## What this means if you run mini/nano today

Most teams evaluating Bedrock are running mini or nano on the 1P API, and the data does **not** say "always upgrade." It suggests a decision rule based on two questions: *how chained is the task?* and *what does a failure cost?*

- **Simple tasks, cheap failures** (classification, extraction, short tool loops): stay put, or run the numbers on nano specifically — it wins cost-per-success on five of six single-call benchmarks, and turn counts converge on easy agentic tasks anyway.
- **Chained reasoning, cheap failures:** terra is the balanced answer in our data — 30/30 agentic success, 3.33 mean turns, $0.00293 per success, and near-luna wall time.
- **Chained reasoning, expensive failures** (customer-facing agents, code changes, anything with human review downstream): sol's premium narrows to ~2x per success on hard tasks while sweeping 4 of 6 accuracy benchmarks and taking the fewest turns. Price the failure, then decide.
- **Latency-sensitive, interactive:** luna — fastest per question in every eval, flat TTFT (~1.1–1.7s) across input sizes, tighter tails — with the caveat that it wanders on chained tasks (more turns than mini, but a 30/30 finish rate).

The general principle travels even if our numbers don't map to your workload: **compute cost per successful task, not cost per token, and weight it by what a failure costs you.**

## Reproduce it, and the caveats that matter

```bash
git clone https://github.com/openai-on-aws/benchmarks-openai
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...   # your own 1P key
export AWS_REGION=us-west-2    # plus IAM credentials

quality/quick_evals.py         # accuracy + cost-per-success suites
quality/agentic_evals.py       # the multi-turn tool-calling loop
performance/run_all.sh         # latency matrix, both backends
```

Caveats, stated as bluntly as the findings:

- **Sample sizes are small.** 60–198 questions per accuracy suite (±8–12 point CIs); 5 repeats per agentic task. Accuracy orderings outside the CI band are solid; agentic turn counts are directional.
- **Reasoning effort was `none`** on the Bedrock models. Higher effort would likely raise sol/terra/luna accuracy — and cost. We tested the cheap configuration deliberately.
- **Mock tools are cheap.** Real tool outputs (RAG, logs) grow per-turn cost much faster than our harness shows. The turn-count *effect* generalizes; the dollar figures are a floor.
- **GPQA used an ungated mirror** (`hendrydong/gpqa_diamond_mc`, CC-BY-4.0); results on the canonical gated set are pending access.
- **Sol ran in us-east-1** (not available in us-west-2 at time of testing); everything else in us-west-2.

If your results differ, open an issue with your result JSONs — that is how this dataset gets better.

---

*All figures in this post trace to timestamped result files in [`openai-on-aws/benchmarks-openai`](https://github.com/openai-on-aws/benchmarks-openai). Prices are July 2026 list prices per 1M tokens: Bedrock luna $1.10/$6.60, terra $2.75/$16.50, sol $5.50/$33.00; 1P mini $0.75/$4.50, nano $0.20/$1.25 (input/output).*
