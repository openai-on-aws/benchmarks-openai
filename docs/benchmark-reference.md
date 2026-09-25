# Benchmark command reference

Commands and configuration for the performance, quality, and API parity suites.
Run commands from the repository root. For other workflows, use the
[Bedrock Bench plugin guide](../plugins/bedrock-bench/README.md),
[ARC pilot guide](arc-benchmarks.md), or [Astra suite guide](astra-benchmarks.md).

## Setup

Use Python 3.10+ for these scripts; Bedrock Bench and ARC-AGI-3 use Python 3.12+.
Install the API benchmark dependencies:

```bash
python -m pip install -r requirements.txt
```

Configure AWS credentials for Bedrock and `OPENAI_API_KEY` for comparisons with
OpenAI's API. See [authentication](#authentication) for the supported options.
Live benchmarks make paid model calls. The examples below retain the existing
GPT-5.6 defaults for reproducing earlier runs; newer agentic configurations are in
the [GPT-6 experiment pack](../examples/bedrock-bench/).

## Performance

Run the latency matrix on Bedrock and OpenAI:

```bash
export AWS_REGION=us-west-2      # plus IAM credentials (profile/env/role)
# Set OPENAI_API_KEY in your environment before running both backends.
./performance/run_all.sh
```

Defaults compare `openai.gpt-5.6-luna` (Bedrock) vs `gpt-5.6-luna` (1P) across 1k/5k/10k/20k-token inputs × 3 output budgets × 25 runs each. For a first look, shrink it:

```bash
RUNS=5 SIZES="1k 10k" ./performance/run_all.sh        # smaller comparison
BEDROCK_MODEL=openai.gpt-5.6-terra OPENAI_MODEL=gpt-5.6-terra ./performance/run_all.sh
SKIP_BEDROCK=1 ./performance/run_all.sh               # 1P side only
```

Then build the full report (percentile tables + charts + findings, as markdown/HTML/DOCX):

```bash
python performance/report.py     # → performance/results/REPORT.{md,html,docx}
```

### Reports

Solid line = median call; shaded band = p5→p95 spread across 25 calls; dashed = p99 tail:

![Sample benchmark chart: TTFT and throughput, Bedrock vs OpenAI 1P](../performance/results/chart_gpt-5.6-luna.png)

### Single-backend runs and comparison

Run one backend directly:

```bash
# Bedrock (uses IAM creds, or AWS_BEARER_TOKEN_BEDROCK if set)
python performance/benchmark.py --backend bedrock --model openai.gpt-5.6-luna 1k --runs=5

# OpenAI 1P
python performance/benchmark.py --backend openai --model gpt-5.6-luna 1k --runs=5
```

| Flag | What it does |
|---|---|
| `--outputs 100,1000` | Override the per-size `max_output_tokens` sweep |
| `--effort none\|low\|medium\|high` | Set reasoning effort (gpt-5.6 accepts it on every backend) |
| `--concurrency N` | Fire N parallel requests to probe throughput under load |
| `--list-models` | Print the backend's model ids (for `bedrock-runtime`: all ACTIVE inference profiles in the region, regardless of access grants) |
| `--tag smoke` | Label the results filename |

Compare any two runs that share a config:

```bash
python performance/compare.py --model-a openai.gpt-5.6-luna --model-b gpt-5.6-luna
```

This matches Bedrock and 1P runs on identical (input size, max output, concurrency, effort) configs and writes `performance/results/COMPARISON.md` with p50 TTFT / ITL / tok-s / E2E deltas. ⏳ Budget 30–60 min per input size per backend at 25 runs.

Legacy single-backend scripts (`benchmark_bedrock.py`, `benchmark_openai_saas.py`) are kept for provenance of the May–June 2026 results.

The streaming performance suite uses the same Responses API streaming code path
across backends, with matching prompts, token budgets, and retry logic. Raw
measurements include reasoning and cache usage alongside percentile summaries.
Bedrock Bench uses separate agent runners; its results also reflect runner behavior.

## Quality

The quick-eval, agentic, DeepSearchQA, and GDPval harnesses switch backends with `--backend mantle|saas|runtime` (the full-eval scripts also support these backends, with explicit `--model` and `--effort`) and emit timestamped result JSONs with per-attempt token usage, so cost-per-success falls out of every run. `mantle` is the `bedrock-mantle` OpenAI-compatible endpoint; `runtime` is the `bedrock-runtime` endpoint — same Responses API and auth, but model ids must be **inference profiles** (e.g. `--backend runtime --model us.openai.gpt-5.6-luna`; bare `openai.*` ids are rejected).

**Quick evals** — fixed-seed samples of six community benchmarks (AIME 2022–24, GPQA Diamond via ungated mirror, MMLU-Pro, MATH-500, GSM8K, HumanEval with official tests executed), exact-match scoring, every model sees the same questions:

```bash
python quality/quick_evals.py --backend mantle --model openai.gpt-5.6-luna --effort none
python quality/quick_evals.py --backend saas --model gpt-5.4-mini
python quality/quick_evals.py --rescore     # re-grade existing results after scorer changes
```

**Agentic multi-turn** — 6 tool-calling tasks × 5 repeats, deterministic mock backends, a real execute-and-feedback loop (max 12 turns). Measures success rate, turn counts, and trajectory cost:

```bash
python quality/agentic_evals.py --backend mantle --model openai.gpt-5.6-terra --effort none
```

**DeepSearchQA (live web research)** — stratified questions from google/deepsearchqa through a real `web_search` + `fetch_page` agent loop (Tavily-backed, disk-cached for reproducibility). Two-layer grading: deterministic pre-pass, then a frozen autorater prompt on gpt-5.5 (never a candidate model):

```bash
cp quality/deepsearchqa/eval.env.example quality/deepsearchqa/eval.env  # add Tavily key(s)
python quality/deepsearchqa/run_deepsearchqa.py --backend mantle --model openai.gpt-5.6-terra --effort none
python quality/deepsearchqa/judge_deepsearchqa.py       # grade unjudged result files
bash quality/deepsearchqa/run_all_arms.sh               # or: all five arms + judging
```

**GDPval (professional deliverables)** — a stratified text-only slice of openai/gdpval: real occupational work products graded item-by-item against human-authored rubrics by a gpt-5.5 judge (rubric-anchored; not comparable to the paper's human pairwise win rates):

```bash
python quality/gdpval_eval.py --backend mantle --model openai.gpt-5.6-luna --effort none
python quality/gdpval_eval.py --judge-only --judge-backend mantle   # judge; one backend per comparison
```

**Full evals** — GPQA Diamond (198 Qs × 5 repeats), AIME competition math, HLE text-only (~2,158 Qs):

```bash
python quality/gpqa_diamond.py --backend mantle
python quality/hle.py --backend mantle --max-questions 20    # quick smoke test
python quality/rescore_hle.py    # LLM-judge rescoring of strict exact-match HLE runs
```

> 📌 The canonical GPQA and HLE datasets are gated on Hugging Face — accept their terms and `huggingface-cli login` first. Everything else (AIME, MMLU-Pro, MATH-500, GSM8K, HumanEval, the GPQA mirror, deepsearchqa, gdpval) needs no gating.

Methodology and completed-run details: [`quality/RESULTS.md`](../quality/RESULTS.md). Accuracy numbers live in the results files.

## API parity

```bash
python parity/run_parity.py
```

34 live checks of the Responses API surface on Bedrock: streaming, multi-turn, stateful conversations (`previous_response_id`), structured output, function calling (single/parallel/forced/round-trip), image inputs, tool types, background mode, and usage reporting. Writes `parity/results_<model>_<region>.txt`; recorded runs for gpt-5.4 and gpt-5.6 luna/terra across three regions are checked in.

## Choosing models

```bash
python performance/benchmark.py --backend bedrock --list-models   # list model IDs in your region
```

| Env var / flag | Used by | Default |
|---|---|---|
| `--model` | `performance/benchmark.py` (required for quality scripts) | `openai.gpt-5.6-luna` (bedrock) / `us.openai.gpt-5.6-luna` (bedrock-runtime) / `gpt-5.6-luna` (openai) |
| `BEDROCK_MODEL` / `OPENAI_MODEL` | `performance/run_all.sh` | `openai.gpt-5.6-luna` / `gpt-5.6-luna` |
| `MANTLE_MODEL` / `SAAS_MODEL` | full quality scripts, legacy benchmarks | `openai.gpt-5.4` / `gpt-5.4` |
| `AWS_REGION` | these Bedrock API scripts | `us-west-2` |
| `MANTLE_BASE_URL` | override the Bedrock endpoint | `https://bedrock-mantle.<AWS_REGION>.api.aws/openai/v1` |
| `BEDROCK_RUNTIME_BASE_URL` | override the bedrock-runtime endpoint | `https://bedrock-runtime.<AWS_REGION>.amazonaws.com/openai/v1` |

Availability and reasoning settings depend on the model, endpoint, and region.
Discovery lists advertised models; it does not prove invocation access. Consult
the [Astra guide](astra-benchmarks.md) and
[GPT-6 agentic experiment pack](../examples/bedrock-bench/) for their model IDs,
regions, reasoning settings, and dated pricing assumptions.

The Runtime backend uses inference-profile IDs, such as
`us.openai.gpt-5.6-luna`, instead of bare `openai.*` IDs. Select it with
`--backend bedrock-runtime` for performance or `--backend runtime` for quality.
`--backend bedrock-runtime --list-models` lists active profiles in the region.

## Authentication

These options apply to the API scripts in this reference. Bedrock Bench container
agents have their own [provider setup](../plugins/bedrock-bench/skills/benchmark-agent-tasks/references/cli.md#providers-and-authentication).

**Bedrock (both the "Mantle" and `bedrock-runtime` endpoints)** — two options:

1. Standard IAM credentials (env vars, profile, or instance role) — scripts mint short-lived tokens automatically via `aws-bedrock-token-generator`.
2. `AWS_BEARER_TOKEN_BEDROCK` — a pre-issued bearer token; quality scripts check this first, then fall back to IAM.

**OpenAI 1P** — bring your own key:

- `OPENAI_API_KEY` — used everywhere.
- Quality scripts prefer `OPENAI_API_KEY_SAAS` if set, so you can keep a separate key for eval runs.

Keys are read from the environment only — never stored in this repo.

## Provenance

The original benchmark suites were ported from the internal
`aws-research-science` suite, with runs dated May–June 2026 and account-specific
values redacted. Published numbers should trace to a script and saved results.
See the [recorded comparisons](../comparisons/gpt-4.1-vs-gpt-5.6/) and
[quality results](../quality/RESULTS.md) for evidence and methodology.
