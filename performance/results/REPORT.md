# GPT-5.6 on Amazon Bedrock vs OpenAI 1P — Latency Benchmark Report

**Generated:** 2026-07-20 21:42 UTC · **Repo:** [openai-on-aws/benchmarks-openai](https://github.com/openai-on-aws/benchmarks-openai)

Both backends are exercised through an identical code path — the OpenAI Responses API with streaming
(`performance/benchmark.py`). Metrics: **TTFT** (time to first output-text token), **Tok/s** (output tokens
per second of generation time), **E2E** (end-to-end wall time). Every number below is computed from the
timestamped result JSONs in `performance/results/` at report build time.

## 1. Test setup

| Parameter | Value |
|---|---|
| Models | `openai.gpt-5.6-luna`, `openai.gpt-5.6-terra` (Bedrock) vs `gpt-5.6-luna`, `gpt-5.6-terra` (1P) |
| Bedrock endpoint | `https://bedrock-mantle.us-west-2.api.aws/openai/v1` |
| OpenAI 1P endpoint | `https://api.openai.com/v1` |
| API surface | Responses API, streaming — identical code path on both backends (`performance/benchmark.py`) |
| Auth | Bedrock: IAM via `aws-bedrock-token-generator` · 1P: user-supplied `OPENAI_API_KEY` |
| Runs per config | 25 |
| Concurrency | Single thread, sequential calls |
| Reasoning effort | Default (not set) |
| Output configs | 1k input: 100/500/1000 · 5k: 500/1000/5000 · 10k: 500/1000/5000 · 20k: 1000/5000/10000 |
| Prompt source | Manhattan Project (Wikipedia) — real varied text, no repetition |
| 1k input prompt | 1,002 verified tokens (4,219 chars) |
| 5k input prompt | 4,750 verified tokens (23,788 chars) |
| 10k input prompt | 9,984 verified tokens (47,568 chars) |
| 20k input prompt | 20,136 verified tokens (95,123 chars) |
| Run dates | 2026-07-18, 2026-07-20 (Bedrock luna 07-18; all else 07-20) |
| Total calls | 1,200 (1 errored) |

## 2. Key performance findings

- **TTFT is roughly flat across input sizes on Bedrock (gpt-5.6-luna):** at 1,000 max output tokens, p50 TTFT stays within 1,204–1,409 ms from 1K to 20K input tokens — a 20× larger prompt does not meaningfully move time-to-first-token.
- **TTFT is roughly flat across input sizes on Bedrock (gpt-5.6-terra):** at 1,000 max output tokens, p50 TTFT stays within 1,343–1,664 ms from 1K to 20K input tokens — a 20× larger prompt does not meaningfully move time-to-first-token.
- **gpt-5.6-luna TTFT:** averaged across all 12 configs, Bedrock p50 TTFT is 21% lower than 1P (1,345 vs 1,709 ms).
- **gpt-5.6-terra TTFT:** averaged across all 12 configs, Bedrock p50 TTFT is 5% lower than 1P (1,475 vs 1,556 ms).
- **Throughput (≥500-token outputs):** luna averages 209.5 tok/s on Bedrock vs 146.3 on 1P (+43%); terra averages 89.2 vs 85.9 tok/s (+4%).
- **Luna streams ~2.0× faster than terra on both backends** (~178 vs ~88 tok/s at ≥500-token outputs) — a model characteristic, not a platform one.
- **gpt-5.6-luna TTFT tail:** worst p99/p50 ratio is 2.1× on Bedrock (10K/500 out) vs 4.6× on 1P (20K/1,000 out).
- **gpt-5.6-terra TTFT tail:** worst p99/p50 ratio is 2.5× on Bedrock (5K/1,000 out) vs 6.6× on 1P (10K/1,000 out).
- **At 100-token output budgets, gpt-5.6 often spends the whole budget on reasoning and emits no visible text** (15/25 on Bedrock gpt-5.6-luna; 20/25 on OpenAI 1P gpt-5.6-luna; 5/25 on Bedrock gpt-5.6-terra). Null-TTFT calls are excluded from latency stats; budget well above 100 output tokens for latency-sensitive use.
- **Responses often stop well before large `max_output_tokens` limits** (model finishes naturally rather than truncating): Bedrock gpt-5.6-luna 5K/5,000: mean 3,628 tokens; Bedrock gpt-5.6-luna 10K/5,000: mean 3,524 tokens; Bedrock gpt-5.6-luna 20K/5,000: mean 3,179 tokens; Bedrock gpt-5.6-luna 20K/10,000: mean 3,149 tokens; and 12 more configs.

### Caveats

- Bedrock **luna** ran 2026-07-18; all other matrices ran 2026-07-20, so the luna comparison includes
  day-to-day variance. The **terra** comparison is same-day on both backends.
- Sequential, single-region (us-west-2), default reasoning effort. Concurrency and effort sweeps are
  supported by the harness but not yet run.
- Delta convention: **positive = Bedrock better** (lower latency or higher throughput).

## 3. gpt-5.6-luna

### 3.1 Benchmark chart

Top row: TTFT p50 with p5→p95 band. Bottom row: output tok/s p50 with p5→p95 band.
One panel per input size; y-scale shared within each row.

![gpt-5.6-luna benchmark chart](chart_gpt-5.6-luna.png)

### 3.2 Bedrock detail (all timings ms)

| Input | Max out | TTFT p50 | TTFT p95 | TTFT p99 | Tok/s p50 | E2E p50 | E2E p95 |
|---|---|---|---|---|---|---|---|
| 1K | 100 | 1,111 | 1,815 | 2,193 | 578.8 | 1,181 | 1,625 |
| 1K | 500 | 1,560 | 2,135 | 2,170 | 273.7 | 3,327 | 3,715 |
| 1K | 1,000 | 1,381 | 1,968 | 2,149 | 221.9 | 5,902 | 6,572 |
| 5K | 500 | 1,179 | 1,596 | 2,181 | 232.9 | 3,282 | 3,797 |
| 5K | 1,000 | 1,204 | 1,972 | 2,201 | 211.1 | 5,985 | 11,364 |
| 5K | 5,000 | 1,212 | 1,909 | 1,981 | 197.4 | 19,535 | 23,716 |
| 10K | 500 | 1,458 | 2,700 | 3,061 | 288.9 | 3,202 | 3,610 |
| 10K | 1,000 | 1,391 | 2,226 | 2,362 | 226.7 | 5,691 | 6,749 |
| 10K | 5,000 | 1,687 | 2,432 | 2,576 | 136.6 | 27,685 | 34,395 |
| 20K | 1,000 | 1,409 | 2,178 | 2,403 | 161.4 | 7,785 | 12,452 |
| 20K | 5,000 | 1,293 | 1,644 | 2,174 | 198.3 | 17,206 | 20,032 |
| 20K | 10,000 | 1,252 | 2,121 | 2,240 | 155.7 | 21,822 | 25,508 |

### 3.3 OpenAI 1P detail (all timings ms)

| Input | Max out | TTFT p50 | TTFT p95 | TTFT p99 | Tok/s p50 | E2E p50 | E2E p95 |
|---|---|---|---|---|---|---|---|
| 1K | 100 | 1,360 | 2,019 | 2,061 | 298.0 | 1,626 | 2,599 |
| 1K | 500 | 1,705 | 3,860 | 5,677 | 154.9 | 4,923 | 9,032 |
| 1K | 1,000 | 1,681 | 2,790 | 2,874 | 148.7 | 8,598 | 9,085 |
| 5K | 500 | 1,465 | 3,672 | 4,634 | 142.8 | 4,945 | 9,035 |
| 5K | 1,000 | 1,797 | 2,514 | 2,748 | 141.9 | 8,630 | 11,415 |
| 5K | 5,000 | 1,519 | 2,496 | 4,082 | 136.3 | 30,886 | 37,958 |
| 10K | 500 | 1,763 | 3,031 | 4,181 | 173.2 | 4,596 | 5,625 |
| 10K | 1,000 | 1,790 | 4,056 | 5,246 | 155.4 | 8,309 | 16,585 |
| 10K | 5,000 | 2,155 | 3,306 | 6,193 | 135.9 | 26,781 | 35,267 |
| 20K | 1,000 | 1,760 | 3,692 | 8,018 | 145.5 | 8,818 | 17,657 |
| 20K | 5,000 | 1,901 | 2,850 | 3,037 | 137.5 | 23,662 | 28,737 |
| 20K | 10,000 | 1,607 | 2,621 | 4,268 | 136.7 | 23,945 | 29,850 |

### 3.4 Side-by-side comparison

| Input | Max out | Metric | Bedrock | OpenAI 1P | Delta |
|---|---|---|---|---|---|
| 1K | 100 | TTFT p50 (ms) | 1,110.8 | 1,359.9 | +18% |
| 1K | 100 | TTFT p95 (ms) | 1,815.1 | 2,019.4 | +10% |
| 1K | 100 | TTFT p99 (ms) | 2,193.0 | 2,061.1 | -6% |
| 1K | 100 | Tok/s p50 | 578.8 | 298.0 | +94% |
| 1K | 100 | E2E p50 (ms) | 1,180.6 | 1,625.8 | +27% |
| 1K | 100 | E2E p95 (ms) | 1,625.4 | 2,598.8 | +37% |
| 1K | 500 | TTFT p50 (ms) | 1,560.4 | 1,705.2 | +8% |
| 1K | 500 | TTFT p95 (ms) | 2,134.6 | 3,859.8 | +45% |
| 1K | 500 | TTFT p99 (ms) | 2,169.7 | 5,677.4 | +62% |
| 1K | 500 | Tok/s p50 | 273.7 | 154.9 | +77% |
| 1K | 500 | E2E p50 (ms) | 3,327.1 | 4,922.8 | +32% |
| 1K | 500 | E2E p95 (ms) | 3,714.6 | 9,032.5 | +59% |
| 1K | 1,000 | TTFT p50 (ms) | 1,380.8 | 1,681.0 | +18% |
| 1K | 1,000 | TTFT p95 (ms) | 1,968.0 | 2,790.0 | +29% |
| 1K | 1,000 | TTFT p99 (ms) | 2,149.1 | 2,873.6 | +25% |
| 1K | 1,000 | Tok/s p50 | 221.9 | 148.7 | +49% |
| 1K | 1,000 | E2E p50 (ms) | 5,902.0 | 8,597.8 | +31% |
| 1K | 1,000 | E2E p95 (ms) | 6,571.5 | 9,085.1 | +28% |
| 5K | 500 | TTFT p50 (ms) | 1,179.3 | 1,465.1 | +20% |
| 5K | 500 | TTFT p95 (ms) | 1,596.2 | 3,672.4 | +57% |
| 5K | 500 | TTFT p99 (ms) | 2,180.8 | 4,634.2 | +53% |
| 5K | 500 | Tok/s p50 | 232.9 | 142.8 | +63% |
| 5K | 500 | E2E p50 (ms) | 3,281.5 | 4,945.2 | +34% |
| 5K | 500 | E2E p95 (ms) | 3,797.0 | 9,035.2 | +58% |
| 5K | 1,000 | TTFT p50 (ms) | 1,203.9 | 1,796.6 | +33% |
| 5K | 1,000 | TTFT p95 (ms) | 1,972.0 | 2,514.5 | +22% |
| 5K | 1,000 | TTFT p99 (ms) | 2,200.6 | 2,748.2 | +20% |
| 5K | 1,000 | Tok/s p50 | 211.1 | 141.9 | +49% |
| 5K | 1,000 | E2E p50 (ms) | 5,984.6 | 8,629.9 | +31% |
| 5K | 1,000 | E2E p95 (ms) | 11,364.0 | 11,415.3 | +0% |
| 5K | 5,000 | TTFT p50 (ms) | 1,211.9 | 1,519.1 | +20% |
| 5K | 5,000 | TTFT p95 (ms) | 1,909.4 | 2,496.4 | +24% |
| 5K | 5,000 | TTFT p99 (ms) | 1,981.3 | 4,082.5 | +51% |
| 5K | 5,000 | Tok/s p50 | 197.4 | 136.3 | +45% |
| 5K | 5,000 | E2E p50 (ms) | 19,535.4 | 30,886.4 | +37% |
| 5K | 5,000 | E2E p95 (ms) | 23,716.5 | 37,957.9 | +38% |
| 10K | 500 | TTFT p50 (ms) | 1,458.4 | 1,762.6 | +17% |
| 10K | 500 | TTFT p95 (ms) | 2,700.5 | 3,031.3 | +11% |
| 10K | 500 | TTFT p99 (ms) | 3,060.8 | 4,181.2 | +27% |
| 10K | 500 | Tok/s p50 | 288.9 | 173.2 | +67% |
| 10K | 500 | E2E p50 (ms) | 3,202.3 | 4,596.0 | +30% |
| 10K | 500 | E2E p95 (ms) | 3,609.6 | 5,625.1 | +36% |
| 10K | 1,000 | TTFT p50 (ms) | 1,390.6 | 1,790.4 | +22% |
| 10K | 1,000 | TTFT p95 (ms) | 2,225.9 | 4,055.6 | +45% |
| 10K | 1,000 | TTFT p99 (ms) | 2,361.6 | 5,246.4 | +55% |
| 10K | 1,000 | Tok/s p50 | 226.7 | 155.4 | +46% |
| 10K | 1,000 | E2E p50 (ms) | 5,691.2 | 8,309.0 | +32% |
| 10K | 1,000 | E2E p95 (ms) | 6,748.9 | 16,585.4 | +59% |
| 10K | 5,000 | TTFT p50 (ms) | 1,686.6 | 2,155.2 | +22% |
| 10K | 5,000 | TTFT p95 (ms) | 2,431.5 | 3,306.0 | +26% |
| 10K | 5,000 | TTFT p99 (ms) | 2,576.1 | 6,193.1 | +58% |
| 10K | 5,000 | Tok/s p50 | 136.6 | 135.9 | +1% |
| 10K | 5,000 | E2E p50 (ms) | 27,685.4 | 26,781.0 | -3% |
| 10K | 5,000 | E2E p95 (ms) | 34,394.8 | 35,267.2 | +2% |
| 20K | 1,000 | TTFT p50 (ms) | 1,409.4 | 1,759.5 | +20% |
| 20K | 1,000 | TTFT p95 (ms) | 2,177.7 | 3,692.5 | +41% |
| 20K | 1,000 | TTFT p99 (ms) | 2,403.4 | 8,017.5 | +70% |
| 20K | 1,000 | Tok/s p50 | 161.4 | 145.5 | +11% |
| 20K | 1,000 | E2E p50 (ms) | 7,785.3 | 8,817.9 | +12% |
| 20K | 1,000 | E2E p95 (ms) | 12,452.4 | 17,657.1 | +29% |
| 20K | 5,000 | TTFT p50 (ms) | 1,292.7 | 1,901.3 | +32% |
| 20K | 5,000 | TTFT p95 (ms) | 1,643.5 | 2,849.8 | +42% |
| 20K | 5,000 | TTFT p99 (ms) | 2,173.7 | 3,036.6 | +28% |
| 20K | 5,000 | Tok/s p50 | 198.3 | 137.5 | +44% |
| 20K | 5,000 | E2E p50 (ms) | 17,205.9 | 23,662.0 | +27% |
| 20K | 5,000 | E2E p95 (ms) | 20,032.2 | 28,737.1 | +30% |
| 20K | 10,000 | TTFT p50 (ms) | 1,252.2 | 1,607.3 | +22% |
| 20K | 10,000 | TTFT p95 (ms) | 2,120.8 | 2,620.8 | +19% |
| 20K | 10,000 | TTFT p99 (ms) | 2,239.6 | 4,267.6 | +48% |
| 20K | 10,000 | Tok/s p50 | 155.7 | 136.7 | +14% |
| 20K | 10,000 | E2E p50 (ms) | 21,821.7 | 23,945.0 | +9% |
| 20K | 10,000 | E2E p95 (ms) | 25,508.2 | 29,849.6 | +15% |

## 4. gpt-5.6-terra

### 4.1 Benchmark chart

Top row: TTFT p50 with p5→p95 band. Bottom row: output tok/s p50 with p5→p95 band.
One panel per input size; y-scale shared within each row.

![gpt-5.6-terra benchmark chart](chart_gpt-5.6-terra.png)

### 4.2 Bedrock detail (all timings ms)

| Input | Max out | TTFT p50 | TTFT p95 | TTFT p99 | Tok/s p50 | E2E p50 | E2E p95 |
|---|---|---|---|---|---|---|---|
| 1K | 100 | 1,490 | 2,186 | 2,322 | 215.2 | 1,938 | 2,351 |
| 1K | 500 | 1,492 | 2,564 | 2,818 | 90.1 | 7,124 | 8,013 |
| 1K | 1,000 | 1,664 | 2,545 | 2,870 | 87.4 | 13,512 | 14,150 |
| 5K | 500 | 1,562 | 2,379 | 2,454 | 91.8 | 6,951 | 7,638 |
| 5K | 1,000 | 1,559 | 2,622 | 3,858 | 86.5 | 13,036 | 14,258 |
| 5K | 5,000 | 1,538 | 2,181 | 2,369 | 82.7 | 42,681 | 51,922 |
| 10K | 500 | 1,418 | 2,578 | 3,367 | 102.7 | 6,318 | 6,874 |
| 10K | 1,000 | 1,498 | 2,254 | 2,763 | 96.9 | 11,746 | 12,723 |
| 10K | 5,000 | 1,494 | 2,259 | 2,764 | 87.1 | 43,905 | 53,316 |
| 20K | 1,000 | 1,343 | 1,667 | 2,252 | 88.4 | 12,681 | 13,624 |
| 20K | 5,000 | 1,283 | 2,018 | 2,311 | 84.2 | 42,622 | 46,187 |
| 20K | 10,000 | 1,359 | 2,039 | 3,207 | 83.3 | 39,446 | 48,804 |

### 4.3 OpenAI 1P detail (all timings ms)

| Input | Max out | TTFT p50 | TTFT p95 | TTFT p99 | Tok/s p50 | E2E p50 | E2E p95 |
|---|---|---|---|---|---|---|---|
| 1K | 100 | 1,434 | 2,296 | 2,442 | 174.3 | 2,016 | 3,671 |
| 1K | 500 | 1,581 | 2,924 | 3,754 | 92.0 | 7,030 | 9,058 |
| 1K | 1,000 | 1,447 | 2,741 | 4,005 | 86.1 | 13,289 | 15,406 |
| 5K | 500 | 1,491 | 2,587 | 2,614 | 89.1 | 7,086 | 9,936 |
| 5K | 1,000 | 1,422 | 2,723 | 3,805 | 86.7 | 12,979 | 19,224 |
| 5K | 5,000 | 1,535 | 2,414 | 2,636 | 81.3 | 48,742 | 53,087 |
| 10K | 500 | 1,806 | 2,813 | 3,390 | 94.3 | 7,138 | 17,214 |
| 10K | 1,000 | 1,699 | 4,244 | 11,283 | 88.2 | 13,025 | 35,501 |
| 10K | 5,000 | 1,727 | 4,634 | 7,728 | 82.4 | 44,416 | 58,804 |
| 20K | 1,000 | 1,455 | 2,357 | 3,363 | 83.4 | 13,448 | 15,727 |
| 20K | 5,000 | 1,510 | 4,412 | 5,239 | 81.1 | 40,318 | 72,056 |
| 20K | 10,000 | 1,563 | 2,759 | 3,123 | 79.9 | 40,771 | 67,593 |

### 4.4 Side-by-side comparison

| Input | Max out | Metric | Bedrock | OpenAI 1P | Delta |
|---|---|---|---|---|---|
| 1K | 100 | TTFT p50 (ms) | 1,489.9 | 1,434.5 | -4% |
| 1K | 100 | TTFT p95 (ms) | 2,186.0 | 2,296.5 | +5% |
| 1K | 100 | TTFT p99 (ms) | 2,322.0 | 2,441.7 | +5% |
| 1K | 100 | Tok/s p50 | 215.2 | 174.3 | +23% |
| 1K | 100 | E2E p50 (ms) | 1,938.1 | 2,015.8 | +4% |
| 1K | 100 | E2E p95 (ms) | 2,350.8 | 3,671.4 | +36% |
| 1K | 500 | TTFT p50 (ms) | 1,492.1 | 1,580.7 | +6% |
| 1K | 500 | TTFT p95 (ms) | 2,563.7 | 2,924.3 | +12% |
| 1K | 500 | TTFT p99 (ms) | 2,817.7 | 3,753.5 | +25% |
| 1K | 500 | Tok/s p50 | 90.1 | 92.0 | -2% |
| 1K | 500 | E2E p50 (ms) | 7,123.6 | 7,029.5 | -1% |
| 1K | 500 | E2E p95 (ms) | 8,013.3 | 9,057.9 | +12% |
| 1K | 1,000 | TTFT p50 (ms) | 1,663.5 | 1,447.4 | -15% |
| 1K | 1,000 | TTFT p95 (ms) | 2,545.4 | 2,740.8 | +7% |
| 1K | 1,000 | TTFT p99 (ms) | 2,870.4 | 4,004.9 | +28% |
| 1K | 1,000 | Tok/s p50 | 87.4 | 86.1 | +2% |
| 1K | 1,000 | E2E p50 (ms) | 13,512.4 | 13,289.1 | -2% |
| 1K | 1,000 | E2E p95 (ms) | 14,150.0 | 15,405.9 | +8% |
| 5K | 500 | TTFT p50 (ms) | 1,561.8 | 1,490.8 | -5% |
| 5K | 500 | TTFT p95 (ms) | 2,378.8 | 2,587.1 | +8% |
| 5K | 500 | TTFT p99 (ms) | 2,454.0 | 2,614.5 | +6% |
| 5K | 500 | Tok/s p50 | 91.8 | 89.1 | +3% |
| 5K | 500 | E2E p50 (ms) | 6,950.7 | 7,086.2 | +2% |
| 5K | 500 | E2E p95 (ms) | 7,638.2 | 9,935.6 | +23% |
| 5K | 1,000 | TTFT p50 (ms) | 1,559.3 | 1,421.5 | -10% |
| 5K | 1,000 | TTFT p95 (ms) | 2,621.9 | 2,722.7 | +4% |
| 5K | 1,000 | TTFT p99 (ms) | 3,857.8 | 3,804.8 | -1% |
| 5K | 1,000 | Tok/s p50 | 86.5 | 86.7 | -0% |
| 5K | 1,000 | E2E p50 (ms) | 13,035.9 | 12,979.4 | -0% |
| 5K | 1,000 | E2E p95 (ms) | 14,257.7 | 19,224.2 | +26% |
| 5K | 5,000 | TTFT p50 (ms) | 1,537.9 | 1,535.1 | -0% |
| 5K | 5,000 | TTFT p95 (ms) | 2,181.4 | 2,414.3 | +10% |
| 5K | 5,000 | TTFT p99 (ms) | 2,369.2 | 2,636.3 | +10% |
| 5K | 5,000 | Tok/s p50 | 82.7 | 81.3 | +2% |
| 5K | 5,000 | E2E p50 (ms) | 42,680.7 | 48,741.7 | +12% |
| 5K | 5,000 | E2E p95 (ms) | 51,922.4 | 53,086.8 | +2% |
| 10K | 500 | TTFT p50 (ms) | 1,417.5 | 1,805.7 | +21% |
| 10K | 500 | TTFT p95 (ms) | 2,578.0 | 2,812.7 | +8% |
| 10K | 500 | TTFT p99 (ms) | 3,366.6 | 3,390.0 | +1% |
| 10K | 500 | Tok/s p50 | 102.7 | 94.3 | +9% |
| 10K | 500 | E2E p50 (ms) | 6,318.2 | 7,138.1 | +11% |
| 10K | 500 | E2E p95 (ms) | 6,874.3 | 17,213.6 | +60% |
| 10K | 1,000 | TTFT p50 (ms) | 1,498.5 | 1,699.2 | +12% |
| 10K | 1,000 | TTFT p95 (ms) | 2,254.1 | 4,243.5 | +47% |
| 10K | 1,000 | TTFT p99 (ms) | 2,763.1 | 11,282.8 | +76% |
| 10K | 1,000 | Tok/s p50 | 96.9 | 88.2 | +10% |
| 10K | 1,000 | E2E p50 (ms) | 11,745.6 | 13,024.6 | +10% |
| 10K | 1,000 | E2E p95 (ms) | 12,722.7 | 35,500.6 | +64% |
| 10K | 5,000 | TTFT p50 (ms) | 1,494.1 | 1,726.9 | +13% |
| 10K | 5,000 | TTFT p95 (ms) | 2,259.4 | 4,633.7 | +51% |
| 10K | 5,000 | TTFT p99 (ms) | 2,763.6 | 7,727.9 | +64% |
| 10K | 5,000 | Tok/s p50 | 87.1 | 82.4 | +6% |
| 10K | 5,000 | E2E p50 (ms) | 43,905.3 | 44,415.9 | +1% |
| 10K | 5,000 | E2E p95 (ms) | 53,315.6 | 58,803.8 | +9% |
| 20K | 1,000 | TTFT p50 (ms) | 1,342.9 | 1,454.8 | +8% |
| 20K | 1,000 | TTFT p95 (ms) | 1,666.7 | 2,356.8 | +29% |
| 20K | 1,000 | TTFT p99 (ms) | 2,252.3 | 3,363.2 | +33% |
| 20K | 1,000 | Tok/s p50 | 88.4 | 83.4 | +6% |
| 20K | 1,000 | E2E p50 (ms) | 12,681.1 | 13,448.3 | +6% |
| 20K | 1,000 | E2E p95 (ms) | 13,623.5 | 15,727.1 | +13% |
| 20K | 5,000 | TTFT p50 (ms) | 1,283.4 | 1,510.0 | +15% |
| 20K | 5,000 | TTFT p95 (ms) | 2,018.3 | 4,411.5 | +54% |
| 20K | 5,000 | TTFT p99 (ms) | 2,311.1 | 5,238.6 | +56% |
| 20K | 5,000 | Tok/s p50 | 84.2 | 81.1 | +4% |
| 20K | 5,000 | E2E p50 (ms) | 42,621.5 | 40,317.8 | -6% |
| 20K | 5,000 | E2E p95 (ms) | 46,187.1 | 72,055.5 | +36% |
| 20K | 10,000 | TTFT p50 (ms) | 1,359.4 | 1,563.4 | +13% |
| 20K | 10,000 | TTFT p95 (ms) | 2,039.1 | 2,759.1 | +26% |
| 20K | 10,000 | TTFT p99 (ms) | 3,206.7 | 3,123.4 | -3% |
| 20K | 10,000 | Tok/s p50 | 83.3 | 79.9 | +4% |
| 20K | 10,000 | E2E p50 (ms) | 39,446.1 | 40,770.7 | +3% |
| 20K | 10,000 | E2E p95 (ms) | 48,804.3 | 67,593.1 | +28% |

## Source files

Every table row and chart point traces to a result JSON under `performance/results/`
(`results_<backend>_<model>_<size>input_25runs_<timestamp>.json`), which holds full
distributions (mean/stddev/p50/p95/p99/min/max) and raw per-call measurements.
