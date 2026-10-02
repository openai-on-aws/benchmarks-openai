# Bedrock vs OpenAI 1P — latency comparison

- **Bedrock model(s):** openai.gpt-5.6-luna
- **OpenAI 1P model(s):** gpt-5.6-luna
- Values are p50 across runs; delta is Bedrock relative to 1P (positive = Bedrock better). Full distributions (p95/p99/mean) are in the underlying result JSONs.

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

## Source files

- `results_bedrock_openai.gpt-5.6-luna_1kinput_25runs_20260718_110651.json` vs `results_openai_gpt-5.6-luna_1kinput_25runs_20260720_104916.json`
- `results_bedrock_openai.gpt-5.6-luna_5kinput_25runs_20260718_111120.json` vs `results_openai_gpt-5.6-luna_5kinput_25runs_20260720_105557.json`
- `results_bedrock_openai.gpt-5.6-luna_10kinput_25runs_20260718_112356.json` vs `results_openai_gpt-5.6-luna_10kinput_25runs_20260720_111516.json`
- `results_bedrock_openai.gpt-5.6-luna_20kinput_25runs_20260718_121453.json` vs `results_openai_gpt-5.6-luna_20kinput_25runs_20260720_113411.json`
