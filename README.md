# Benchmarks with OpenAI on AWS

Benchmarks and evals for OpenAI models running on AWS — specifically Amazon Bedrock's OpenAI-compatible Responses API endpoint ("Mantle") — compared side-by-side against the same models on OpenAI's SaaS API. This repo is the team's validated, shareable source for benchmark results: every number we circulate should trace back to a script and a results file here.

> This project is under active development.

Three suites:

- **performance/** — latency benchmarks: TTFT, output tokens/sec, end-to-end latency
- **quality/** — accuracy evals: GPQA Diamond, AIME, HLE
- **parity/** — Responses API feature-parity checks against Bedrock

## Repository layout

```
performance/
  benchmark.py                # Unified latency harness — one script, both backends
                              #   (--backend bedrock | openai), Responses API, streaming
  compare.py                  # Side-by-side Bedrock vs 1P report (COMPARISON.md) from result JSONs
  run_all.sh                  # Runs the full matrix on both backends, then compare.py
  benchmark_bedrock.py        # (legacy) Bedrock-only benchmark, superseded by benchmark.py
  benchmark_openai_saas.py    # (legacy) SaaS-only benchmark via Chat Completions, superseded
  generate_report.py          # Builds a .docx report (numbers are a hardcoded snapshot of past runs)
  data/                       # Canonical prompts: ~1k / 5k / 10k / 20k input tokens
  results/                    # Timestamped result JSONs + TTFT/tok-s plot (plot_ttft_otps.png)
quality/
  gpqa_diamond.py             # GPQA Diamond, 5 repeats, mean pass@1
  aime_2025.py                # AIME competition math (loads the public AIME 1983-2024 dataset;
                              #   the 2025 set is not yet on Hugging Face)
  hle.py                      # Humanity's Last Exam, text-only subset (~2,158 questions)
  rescore_hle.py              # LLM-judge rescoring of HLE runs (Claude Haiku on Bedrock via boto3)
  eval_utils.py               # Shared error-capture helper
  RESULTS.md                  # Methodology + results of completed runs
  results/                    # Raw eval output JSONs
parity/
  run_parity.py               # 34 Responses API feature checks against the Bedrock endpoint
  results.txt                 # Latest recorded parity run
docs/
  migration-workload-plan.md  # Migration workload pack plan (draft; see below)
```

## Prerequisites & auth

Python 3.10+ recommended. Install dependencies:

```bash
pip install -r requirements.txt
```

**Bedrock (Mantle) auth** — two options:

1. Standard IAM credentials (env vars, profile, or instance role). The scripts mint a short-lived token automatically via `aws-bedrock-token-generator`. This is the only mode `performance/benchmark_bedrock.py` and `parity/run_parity.py` support.
2. `AWS_BEARER_TOKEN_BEDROCK` — a pre-issued Bedrock bearer token. The quality scripts check this first and fall back to IAM if it is unset.

Region comes from `AWS_REGION` (default `us-west-2`). The endpoint defaults to `https://bedrock-mantle.us-west-2.api.aws/openai/v1` and can be overridden with `MANTLE_BASE_URL` (performance and quality scripts).

**OpenAI SaaS (1P) auth** — bring your own key:

- `OPENAI_API_KEY` — used by `performance/benchmark.py --backend openai` (and the legacy `benchmark_openai_saas.py`).
- Quality scripts prefer `OPENAI_API_KEY_SAAS` and fall back to `OPENAI_API_KEY`, so you can keep a separate key for eval runs.

**Hugging Face datasets:** the quality evals download `Idavidrein/gpqa`, `qq8933/AIME_1983_2024`, and `cais/hle` at runtime. GPQA and HLE are gated — accept the terms on Hugging Face and run `huggingface-cli login` first.

## Quick start

### Performance benchmark — Bedrock vs your own OpenAI 1P key

Everything runs through one harness, `performance/benchmark.py`, which uses the same
Responses-API streaming code path against both backends so the numbers are directly
comparable. Bring your own OpenAI API key for the 1P side.

**The one-command path** — full matrix on both backends, then a side-by-side report:

```bash
export OPENAI_API_KEY=sk-...     # your OpenAI 1P key
export AWS_REGION=us-west-2      # plus IAM credentials (profile/env/role)
./performance/run_all.sh
```

Defaults compare `openai.gpt-5.6-luna` (Bedrock) against `gpt-5.6-luna` (1P) across
1k/5k/10k/20k inputs × 3 output configs × 25 runs each. Override with env vars:

```bash
BEDROCK_MODEL=openai.gpt-5.6-terra OPENAI_MODEL=gpt-5.6-terra ./performance/run_all.sh
RUNS=5 SIZES="1k 10k" ./performance/run_all.sh     # quicker pass
SKIP_BEDROCK=1 ./performance/run_all.sh            # 1P side only
```

Budget roughly 30–60 min per input size per backend at 25 runs; a `RUNS=5` pass
finishes in a fraction of that and is fine for a first look.

**Running a single backend directly:**

```bash
# Bedrock (uses IAM creds, or AWS_BEARER_TOKEN_BEDROCK if set)
python performance/benchmark.py --backend bedrock --model openai.gpt-5.6-luna 1k --runs=5

# OpenAI 1P
python performance/benchmark.py --backend openai --model gpt-5.6-luna 1k --runs=5
```

Useful flags: `--outputs 100,1000` overrides the per-size `max_output_tokens` sweep,
`--effort low|medium|high` sets reasoning effort (accepted by gpt-5.6 on both backends),
`--concurrency N` fires N parallel requests to probe throughput under load,
`--list-models` prints the model ids the backend offers, and `--tag` labels the
results file. Each run writes a timestamped JSON (summary stats + raw per-call data)
into `performance/results/`.

**Comparing results:**

```bash
python performance/compare.py --model-a openai.gpt-5.6-luna --model-b gpt-5.6-luna
```

Matches Bedrock and 1P runs on identical (input size, max output, concurrency, effort)
configs, prints p50 TTFT / ITL / tok-s / E2E side by side with relative deltas, and
writes `performance/results/COMPARISON.md`. Only results produced by `benchmark.py`
(schema v2) are compared; the older single-backend scripts remain for provenance.

### Quality eval

```bash
python quality/gpqa_diamond.py --backend mantle   # Bedrock
python quality/gpqa_diamond.py --backend saas     # OpenAI SaaS
```

`--backend` is how all three evals switch targets (`aime_2025.py` works the same; `hle.py` requires the flag). A full GPQA run is 990 calls (~2 hours). For a quick smoke test, HLE supports capping:

```bash
python quality/hle.py --backend mantle --max-questions 20
```

HLE's exact-string scoring is deliberately strict; re-score a completed Mantle run with an LLM judge (Claude Haiku on Bedrock, needs IAM credentials with `bedrock:InvokeModel`):

```bash
python quality/rescore_hle.py
```

Note: `rescore_hle.py` picks the lexicographically last non-`_rescored` `hle_mantle_*.json` in `quality/results/` — it prints which file it selected. All eval outputs land in `quality/results/`.

### Parity suite

```bash
python parity/run_parity.py
```

Runs 34 live checks against the Bedrock Responses endpoint and writes `parity/results_<model>_<region>.txt`. Configure with env vars: `MANTLE_MODEL` (default `openai.gpt-5.4`), `AWS_REGION` (default `us-west-2`), `MANTLE_BASE_URL` (defaults to the region's `bedrock-mantle` endpoint). `parity/results.txt` is the original gpt-5.4 us-west-2 reference run.

## Choosing models

| Env var / flag | Used by | Default |
|---|---|---|
| `--model` | `performance/benchmark.py` | `openai.gpt-5.6-luna` (bedrock) / `gpt-5.6-luna` (openai) |
| `BEDROCK_MODEL` / `OPENAI_MODEL` | `performance/run_all.sh` | `openai.gpt-5.6-luna` / `gpt-5.6-luna` |
| `MANTLE_MODEL` | quality scripts with `--backend mantle`, legacy `benchmark_bedrock.py` | `openai.gpt-5.4` |
| `SAAS_MODEL` | quality scripts with `--backend saas`, legacy `benchmark_openai_saas.py` | `gpt-5.4` / `gpt-5.5` |
| `MANTLE_BASE_URL` | performance + quality Bedrock calls | `https://bedrock-mantle.<AWS_REGION>.api.aws/openai/v1` |

Run `python performance/benchmark.py --backend bedrock --list-models` (or `--backend openai`)
to see what your credentials can reach before kicking off a long run.

Current OpenAI model IDs on Bedrock, newest first (availability varies by region —
`--list-models` is the source of truth for yours; as of 2026-07-18, us-west-2 serves
luna/terra/5.4/oss but not sol or 5.5):

- `openai.gpt-5.6-luna`
- `openai.gpt-5.6-terra`
- `openai.gpt-5.6-sol`
- `openai.gpt-5.5`
- `openai.gpt-5.4`
- `openai.gpt-oss-120b` / `openai.gpt-oss-20b`

Evaluation work currently targets the latest arrivals (the gpt-5.6 family); the results checked into this repo were produced against gpt-5.4 and gpt-5.5. The quality scripts drop the `temperature` parameter for reasoning-model families that reject it (`5.5` and `5.6` — see `supports_temperature` in `quality/eval_utils.py`); extend that list when newer families arrive.

## What's measured

**Performance** — for every (input size × max output tokens) config: TTFT (ms), inter-token latency (ITL, ms between successive streamed chunks), output tokens/sec, and end-to-end latency (ms), each reported as mean / stddev / p50 / p95 / p99 / min / max across runs. Per-call reasoning-token and cached-token counts are also recorded, since reasoning models can spend a large share of latency before the first visible token. Raw per-call measurements are kept in the result JSONs under [`performance/results/`](performance/results/).

**Quality** — GPQA Diamond (198 questions, 5 repeats, mean pass@1), AIME competition math, and HLE text-only (exact match plus LLM-judge rescoring), run identically against both backends. Methodology and completed-run results live in [`quality/RESULTS.md`](quality/RESULTS.md); raw outputs are in [`quality/results/`](quality/results/). Per team policy, accuracy numbers are not quoted here — consult the results files.

**Parity** — 34 feature checks of the Responses API surface on Bedrock: streaming, instructions, multi-turn, structured output (JSON schema), function calling (single/parallel/forced/round-trip), image inputs (URL / base64 / S3), tool types (web search, file search, MCP, custom, namespace, tool search), background mode, `store`/retrieval, and usage reporting. Recorded runs: [`parity/results.txt`](parity/results.txt) (gpt-5.4 reference) plus per-model/region files, e.g. [`parity/results_openai.gpt-5.6-luna_us-west-2.txt`](parity/results_openai.gpt-5.6-luna_us-west-2.txt) and [`parity/results_openai.gpt-5.6-terra_us-west-2.txt`](parity/results_openai.gpt-5.6-terra_us-west-2.txt).

## Migration workload pack

For teams migrating an existing OpenAI SaaS workload (gpt-5.4-mini/nano class) to Bedrock, [`docs/migration-workload-plan.md`](docs/migration-workload-plan.md) drafts a reusable migration pack: five representative workload archetypes, a model matrix (SaaS mini/nano baseline vs `gpt-5.6-luna`/`-terra` targets), precise metric definitions (task success, first-pass accuracy, retries, p50/p95 latency, token usage, caching, cost per successful task), and starting guidance on which model to test first — with customer-specific evals as the final gate.

## Provenance

Ported from the internal `aws-research-science` repo's benchmarks suite (runs dated May–June 2026), with account-specific values redacted.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Security

See [CONTRIBUTING.md](CONTRIBUTING.md#security-issue-notifications) for information on reporting security issues.

## License

This repository is dual-licensed:

- **Code** is licensed under the [MIT No Attribution (MIT-0)](LICENSE) license.
- **Documentation and text content** is licensed under the [Creative Commons Attribution-ShareAlike 4.0 International (CC-BY-SA 4.0)](LICENSE-DOCS.md) license.
