# Bedrock vs OpenAI 1P — latency comparison

- **Bedrock model(s):** openai.gpt-5.6-terra
- **OpenAI 1P model(s):** gpt-5.6-terra
- Values are p50 across runs; delta is Bedrock relative to 1P (positive = Bedrock better). Full distributions (p95/p99/mean) are in the underlying result JSONs.

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

## Source files

- `results_bedrock_openai.gpt-5.6-terra_1kinput_25runs_20260720_104913.json` vs `results_openai_gpt-5.6-terra_1kinput_25runs_20260720_115912.json`
- `results_bedrock_openai.gpt-5.6-terra_5kinput_25runs_20260720_105845.json` vs `results_openai_gpt-5.6-terra_5kinput_25runs_20260720_120857.json`
- `results_bedrock_openai.gpt-5.6-terra_10kinput_25runs_20260720_112534.json` vs `results_openai_gpt-5.6-terra_10kinput_25runs_20260720_130137.json`
- `results_bedrock_openai.gpt-5.6-terra_20kinput_25runs_20260720_115134.json` vs `results_openai_gpt-5.6-terra_20kinput_25runs_20260720_133108.json`
