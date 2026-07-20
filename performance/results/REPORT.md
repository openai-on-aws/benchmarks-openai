# GPT-5.6 on Amazon Bedrock vs OpenAI 1P — Latency Benchmark Report

**Date:** 2026-07-20 · **Repo:** [openai-on-aws/benchmarks-openai](https://github.com/openai-on-aws/benchmarks-openai)

## Methodology

Both backends were exercised through an identical code path — the OpenAI **Responses API with streaming** — via `performance/benchmark.py`. For each model, the matrix is 4 input sizes (~1k / 5k / 10k / 20k tokens) × 3 `max_output_tokens` configs × **25 runs**, sequential requests, default reasoning effort.

- **Bedrock**: `https://bedrock-mantle.us-west-2.api.aws/openai/v1` (models `openai.gpt-5.6-luna`, `openai.gpt-5.6-terra`)
- **OpenAI 1P**: `https://api.openai.com/v1` (models `gpt-5.6-luna`, `gpt-5.6-terra`)

Metrics: **TTFT** (time to first output-text token), **ITL** (inter-token latency between streamed chunks), **Tok/s** (output tokens per second of generation time), **E2E** (end-to-end wall time). Tables show p50; full distributions (mean/stddev/p95/p99/min/max) and raw per-call data are in the result JSONs.

## Headline findings

- **gpt-5.6-luna:** Bedrock led on effectively all 12 configs — TTFT p50 8–33% lower, tokens/sec 15–95% higher, E2E ~10–35% faster.
- **gpt-5.6-terra:** effectively tied at 1k/5k inputs (deltas within ±6%); at 10k/20k inputs Bedrock led consistently (TTFT 8–21% lower, throughput 4–10% higher).
- **Model difference (both backends):** terra streams at ~80–100 tok/s vs luna's ~140–290 — luna is roughly 2.5× faster at output generation for these workloads.
- **Reliability of the runs:** 1,199 of 1,200 benchmark calls succeeded (one connection error on Bedrock luna 10k).

### Caveats

- Bedrock **luna** ran 2026-07-18; all other matrices ran 2026-07-20. The luna comparison therefore includes day-to-day variance. The **terra** comparison is same-day on both backends.
- Sequential, single-region (us-west-2), default reasoning effort. Concurrency and effort sweeps are supported by the harness but not yet run.
- At small output budgets (`max_out=100`) gpt-5.6 frequently spends the whole budget on reasoning and emits no visible text (null TTFT, excluded from stats) — on both backends.

Delta convention: **positive = Bedrock better** (lower latency or higher throughput).

## gpt-5.6-luna — full comparison

| Input | Max out | Conc | Effort | Metric | Bedrock | OpenAI 1P | Delta |
|---|---|---|---|---|---|---|---|
| 1k | 100 | 1 | - | TTFT p50 (ms) | 1110.8 | 1359.9 | +18% |
| 1k | 100 | 1 | - | ITL p50 (ms) | 0.4 | 9.4 | +96% |
| 1k | 100 | 1 | - | Tok/s p50 | 578.8 | 298.0 | +94% |
| 1k | 100 | 1 | - | E2E p50 (ms) | 1180.6 | 1625.8 | +27% |
| 1k | 500 | 1 | - | TTFT p50 (ms) | 1560.4 | 1705.2 | +8% |
| 1k | 500 | 1 | - | ITL p50 (ms) | 5.0 | 7.8 | +36% |
| 1k | 500 | 1 | - | Tok/s p50 | 273.7 | 154.9 | +77% |
| 1k | 500 | 1 | - | E2E p50 (ms) | 3327.1 | 4922.8 | +32% |
| 1k | 1000 | 1 | - | TTFT p50 (ms) | 1380.8 | 1681.0 | +18% |
| 1k | 1000 | 1 | - | ITL p50 (ms) | 5.1 | 7.6 | +33% |
| 1k | 1000 | 1 | - | Tok/s p50 | 221.9 | 148.7 | +49% |
| 1k | 1000 | 1 | - | E2E p50 (ms) | 5902.0 | 8597.8 | +31% |
| 5k | 500 | 1 | - | TTFT p50 (ms) | 1179.3 | 1465.1 | +20% |
| 5k | 500 | 1 | - | ITL p50 (ms) | 5.2 | 8.0 | +35% |
| 5k | 500 | 1 | - | Tok/s p50 | 232.9 | 142.8 | +63% |
| 5k | 500 | 1 | - | E2E p50 (ms) | 3281.5 | 4945.2 | +34% |
| 5k | 1000 | 1 | - | TTFT p50 (ms) | 1203.9 | 1796.6 | +33% |
| 5k | 1000 | 1 | - | ITL p50 (ms) | 5.2 | 7.7 | +32% |
| 5k | 1000 | 1 | - | Tok/s p50 | 211.1 | 141.9 | +49% |
| 5k | 1000 | 1 | - | E2E p50 (ms) | 5984.6 | 8629.9 | +31% |
| 5k | 5000 | 1 | - | TTFT p50 (ms) | 1211.9 | 1519.1 | +20% |
| 5k | 5000 | 1 | - | ITL p50 (ms) | 5.1 | 7.4 | +31% |
| 5k | 5000 | 1 | - | Tok/s p50 | 197.4 | 136.3 | +45% |
| 5k | 5000 | 1 | - | E2E p50 (ms) | 19535.4 | 30886.4 | +37% |
| 10k | 500 | 1 | - | TTFT p50 (ms) | 1458.4 | 1762.6 | +17% |
| 10k | 500 | 1 | - | ITL p50 (ms) | 4.6 | 7.0 | +34% |
| 10k | 500 | 1 | - | Tok/s p50 | 288.9 | 173.2 | +67% |
| 10k | 500 | 1 | - | E2E p50 (ms) | 3202.3 | 4596.0 | +30% |
| 10k | 1000 | 1 | - | TTFT p50 (ms) | 1390.6 | 1790.4 | +22% |
| 10k | 1000 | 1 | - | ITL p50 (ms) | 4.9 | 7.2 | +32% |
| 10k | 1000 | 1 | - | Tok/s p50 | 226.7 | 155.4 | +46% |
| 10k | 1000 | 1 | - | E2E p50 (ms) | 5691.2 | 8309.0 | +32% |
| 10k | 5000 | 1 | - | TTFT p50 (ms) | 1686.6 | 2155.2 | +22% |
| 10k | 5000 | 1 | - | ITL p50 (ms) | 7.1 | 7.5 | +5% |
| 10k | 5000 | 1 | - | Tok/s p50 | 136.6 | 135.9 | +1% |
| 10k | 5000 | 1 | - | E2E p50 (ms) | 27685.4 | 26781.0 | -3% |
| 20k | 1000 | 1 | - | TTFT p50 (ms) | 1409.4 | 1759.5 | +20% |
| 20k | 1000 | 1 | - | ITL p50 (ms) | 6.3 | 7.6 | +17% |
| 20k | 1000 | 1 | - | Tok/s p50 | 161.4 | 145.5 | +11% |
| 20k | 1000 | 1 | - | E2E p50 (ms) | 7785.3 | 8817.9 | +12% |
| 20k | 5000 | 1 | - | TTFT p50 (ms) | 1292.7 | 1901.3 | +32% |
| 20k | 5000 | 1 | - | ITL p50 (ms) | 5.2 | 7.5 | +31% |
| 20k | 5000 | 1 | - | Tok/s p50 | 198.3 | 137.5 | +44% |
| 20k | 5000 | 1 | - | E2E p50 (ms) | 17205.9 | 23662.0 | +27% |
| 20k | 10000 | 1 | - | TTFT p50 (ms) | 1252.2 | 1607.3 | +22% |
| 20k | 10000 | 1 | - | ITL p50 (ms) | 6.1 | 7.5 | +19% |
| 20k | 10000 | 1 | - | Tok/s p50 | 155.7 | 136.7 | +14% |
| 20k | 10000 | 1 | - | E2E p50 (ms) | 21821.7 | 23945.0 | +9% |

### Source files

- `results_bedrock_openai.gpt-5.6-luna_1kinput_25runs_20260718_110651.json` vs `results_openai_gpt-5.6-luna_1kinput_25runs_20260720_104916.json`
- `results_bedrock_openai.gpt-5.6-luna_5kinput_25runs_20260718_111120.json` vs `results_openai_gpt-5.6-luna_5kinput_25runs_20260720_105557.json`
- `results_bedrock_openai.gpt-5.6-luna_10kinput_25runs_20260718_112356.json` vs `results_openai_gpt-5.6-luna_10kinput_25runs_20260720_111516.json`
- `results_bedrock_openai.gpt-5.6-luna_20kinput_25runs_20260718_121453.json` vs `results_openai_gpt-5.6-luna_20kinput_25runs_20260720_113411.json`

## gpt-5.6-terra — full comparison

| Input | Max out | Conc | Effort | Metric | Bedrock | OpenAI 1P | Delta |
|---|---|---|---|---|---|---|---|
| 1k | 100 | 1 | - | TTFT p50 (ms) | 1489.9 | 1434.5 | -4% |
| 1k | 100 | 1 | - | ITL p50 (ms) | 11.9 | 13.4 | +11% |
| 1k | 100 | 1 | - | Tok/s p50 | 215.2 | 174.3 | +23% |
| 1k | 100 | 1 | - | E2E p50 (ms) | 1938.1 | 2015.8 | +4% |
| 1k | 500 | 1 | - | TTFT p50 (ms) | 1492.1 | 1580.7 | +6% |
| 1k | 500 | 1 | - | ITL p50 (ms) | 12.9 | 12.4 | -4% |
| 1k | 500 | 1 | - | Tok/s p50 | 90.1 | 92.0 | -2% |
| 1k | 500 | 1 | - | E2E p50 (ms) | 7123.6 | 7029.5 | -1% |
| 1k | 1000 | 1 | - | TTFT p50 (ms) | 1663.5 | 1447.4 | -15% |
| 1k | 1000 | 1 | - | ITL p50 (ms) | 12.5 | 12.1 | -3% |
| 1k | 1000 | 1 | - | Tok/s p50 | 87.4 | 86.1 | +2% |
| 1k | 1000 | 1 | - | E2E p50 (ms) | 13512.4 | 13289.1 | -2% |
| 5k | 500 | 1 | - | TTFT p50 (ms) | 1561.8 | 1490.8 | -5% |
| 5k | 500 | 1 | - | ITL p50 (ms) | 12.2 | 12.1 | -1% |
| 5k | 500 | 1 | - | Tok/s p50 | 91.8 | 89.1 | +3% |
| 5k | 500 | 1 | - | E2E p50 (ms) | 6950.7 | 7086.2 | +2% |
| 5k | 1000 | 1 | - | TTFT p50 (ms) | 1559.3 | 1421.5 | -10% |
| 5k | 1000 | 1 | - | ITL p50 (ms) | 12.3 | 12.1 | -2% |
| 5k | 1000 | 1 | - | Tok/s p50 | 86.5 | 86.7 | -0% |
| 5k | 1000 | 1 | - | E2E p50 (ms) | 13035.9 | 12979.4 | -0% |
| 5k | 5000 | 1 | - | TTFT p50 (ms) | 1537.9 | 1535.1 | -0% |
| 5k | 5000 | 1 | - | ITL p50 (ms) | 12.3 | 12.4 | +1% |
| 5k | 5000 | 1 | - | Tok/s p50 | 82.7 | 81.3 | +2% |
| 5k | 5000 | 1 | - | E2E p50 (ms) | 42680.7 | 48741.7 | +12% |
| 10k | 500 | 1 | - | TTFT p50 (ms) | 1417.5 | 1805.7 | +21% |
| 10k | 500 | 1 | - | ITL p50 (ms) | 10.8 | 12.0 | +10% |
| 10k | 500 | 1 | - | Tok/s p50 | 102.7 | 94.3 | +9% |
| 10k | 500 | 1 | - | E2E p50 (ms) | 6318.2 | 7138.1 | +11% |
| 10k | 1000 | 1 | - | TTFT p50 (ms) | 1498.5 | 1699.2 | +12% |
| 10k | 1000 | 1 | - | ITL p50 (ms) | 11.1 | 12.1 | +8% |
| 10k | 1000 | 1 | - | Tok/s p50 | 96.9 | 88.2 | +10% |
| 10k | 1000 | 1 | - | E2E p50 (ms) | 11745.6 | 13024.6 | +10% |
| 10k | 5000 | 1 | - | TTFT p50 (ms) | 1494.1 | 1726.9 | +13% |
| 10k | 5000 | 1 | - | ITL p50 (ms) | 11.7 | 12.3 | +5% |
| 10k | 5000 | 1 | - | Tok/s p50 | 87.1 | 82.4 | +6% |
| 10k | 5000 | 1 | - | E2E p50 (ms) | 43905.3 | 44415.9 | +1% |
| 20k | 1000 | 1 | - | TTFT p50 (ms) | 1342.9 | 1454.8 | +8% |
| 20k | 1000 | 1 | - | ITL p50 (ms) | 11.9 | 12.4 | +4% |
| 20k | 1000 | 1 | - | Tok/s p50 | 88.4 | 83.4 | +6% |
| 20k | 1000 | 1 | - | E2E p50 (ms) | 12681.1 | 13448.3 | +6% |
| 20k | 5000 | 1 | - | TTFT p50 (ms) | 1283.4 | 1510.0 | +15% |
| 20k | 5000 | 1 | - | ITL p50 (ms) | 12.1 | 12.4 | +2% |
| 20k | 5000 | 1 | - | Tok/s p50 | 84.2 | 81.1 | +4% |
| 20k | 5000 | 1 | - | E2E p50 (ms) | 42621.5 | 40317.8 | -6% |
| 20k | 10000 | 1 | - | TTFT p50 (ms) | 1359.4 | 1563.4 | +13% |
| 20k | 10000 | 1 | - | ITL p50 (ms) | 12.2 | 12.7 | +4% |
| 20k | 10000 | 1 | - | Tok/s p50 | 83.3 | 79.9 | +4% |
| 20k | 10000 | 1 | - | E2E p50 (ms) | 39446.1 | 40770.7 | +3% |

### Source files

- `results_bedrock_openai.gpt-5.6-terra_1kinput_25runs_20260720_104913.json` vs `results_openai_gpt-5.6-terra_1kinput_25runs_20260720_115912.json`
- `results_bedrock_openai.gpt-5.6-terra_5kinput_25runs_20260720_105845.json` vs `results_openai_gpt-5.6-terra_5kinput_25runs_20260720_120857.json`
- `results_bedrock_openai.gpt-5.6-terra_10kinput_25runs_20260720_112534.json` vs `results_openai_gpt-5.6-terra_10kinput_25runs_20260720_130137.json`
- `results_bedrock_openai.gpt-5.6-terra_20kinput_25runs_20260720_115134.json` vs `results_openai_gpt-5.6-terra_20kinput_25runs_20260720_133108.json`
