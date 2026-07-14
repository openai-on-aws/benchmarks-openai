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
  benchmark_bedrock.py        # Perf benchmark vs Bedrock (Responses API, streaming)
  benchmark_openai_saas.py    # Same prompt/output matrix vs OpenAI SaaS (Chat Completions, streaming)
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

**OpenAI SaaS auth:**

- `OPENAI_API_KEY` — used by `performance/benchmark_openai_saas.py`.
- Quality scripts prefer `OPENAI_API_KEY_SAAS` and fall back to `OPENAI_API_KEY`, so you can keep a separate key for eval runs.

**Hugging Face datasets:** the quality evals download `Idavidrein/gpqa`, `qq8933/AIME_1983_2024`, and `cais/hle` at runtime. GPQA and HLE are gated — accept the terms on Hugging Face and run `huggingface-cli login` first.

## Quick start

### Performance benchmark

Against Bedrock (IAM credentials required):

```bash
export AWS_REGION=us-west-2
python performance/benchmark_bedrock.py 1k --runs=5
```

Positional args pick input sizes (`1k`, `5k`, `10k`, `20k`; default `1k 10k 20k`), `--runs=N` sets runs per output config (default 25). Each input size sweeps three `max_output_tokens` configs.

Against OpenAI SaaS (25 runs per config, fixed):

```bash
export OPENAI_API_KEY=sk-...
SAAS_MODEL=gpt-5.5 python performance/benchmark_openai_saas.py 1k
```

Both write timestamped JSONs (summary stats + raw per-call data) into `performance/results/`.

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

| Env var | Used by | Default |
|---|---|---|
| `MANTLE_MODEL` | `performance/benchmark_bedrock.py`, quality scripts with `--backend mantle` | `openai.gpt-5.4` |
| `SAAS_MODEL` | `performance/benchmark_openai_saas.py` | `gpt-5.5` |
| `SAAS_MODEL` | quality scripts with `--backend saas` | `gpt-5.4` |
| `MANTLE_BASE_URL` | performance + quality Bedrock calls | `https://bedrock-mantle.us-west-2.api.aws/openai/v1` |

Current OpenAI model IDs on Bedrock, newest first:

- `openai.gpt-5.6-luna`
- `openai.gpt-5.6-terra`
- `openai.gpt-5.6-sol`
- `openai.gpt-5.5`
- `openai.gpt-5.4`
- `openai.gpt-oss-120b` / `openai.gpt-oss-20b`

Evaluation work currently targets the latest arrivals (the gpt-5.6 family); the results checked into this repo were produced against gpt-5.4 and gpt-5.5. The quality scripts drop the `temperature` parameter for reasoning-model families that reject it (`5.5` and `5.6` — see `supports_temperature` in `quality/eval_utils.py`); extend that list when newer families arrive.

## What's measured

**Performance** — for every (input size × max output tokens) config: TTFT (ms), output tokens/sec, and end-to-end latency (ms), each reported as mean / stddev / p50 / p95 / p99 / min / max across runs. Raw per-call measurements are kept in the result JSONs under [`performance/results/`](performance/results/).

**Quality** — GPQA Diamond (198 questions, 5 repeats, mean pass@1), AIME competition math, and HLE text-only (exact match plus LLM-judge rescoring), run identically against both backends. Methodology and completed-run results live in [`quality/RESULTS.md`](quality/RESULTS.md); raw outputs are in [`quality/results/`](quality/results/). Per team policy, accuracy numbers are not quoted here — consult the results files.

**Parity** — 34 feature checks of the Responses API surface on Bedrock: streaming, instructions, multi-turn, structured output (JSON schema), function calling (single/parallel/forced/round-trip), image inputs (URL / base64 / S3), tool types (web search, file search, MCP, custom, namespace, tool search), background mode, `store`/retrieval, and usage reporting. See the latest run in [`parity/results.txt`](parity/results.txt).

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
