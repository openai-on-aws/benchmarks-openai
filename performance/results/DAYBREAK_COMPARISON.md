# Daybreak Blue vs Daybreak Red — latency comparison

- **Daybreak Blue model(s):** openai.gpt-daybreak-blue-5.6-sol
- **Daybreak Red model(s):** openai.gpt-5.6-cyber
- Values are p50 across runs; delta is Daybreak Blue relative to Daybreak Red (positive = Daybreak Blue better). Full distributions (p95/p99/mean) are in the underlying result JSONs.

| Input | Max out | Conc | Effort | Metric | Daybreak Blue | Daybreak Red | Delta |
|---|---|---|---|---|---|---|---|
| 1k | 128 | 1 | - | TTFE p50 (ms) | 1690.5 | 660.2 | -156% |
| 1k | 128 | 1 | - | E2E p50 (ms) | 5151.5 | 2718.3 | -90% |
| 1k | 1024 | 1 | - | TTFE p50 (ms) | 651.9 | 653.9 | +0% |
| 1k | 1024 | 1 | - | TTFT p50 (ms) | 9334.7 | 9564.6 | +2% |
| 1k | 1024 | 1 | - | TTFT−TTFE p50 (ms) | 8682.8 | 8910.8 | +3% |
| 1k | 1024 | 1 | - | ITL p50 (ms) | 12.5 | 15.9 | +21% |
| 1k | 1024 | 1 | - | Tok/s p50 | 79.0 | 62.9 | +26% |
| 1k | 1024 | 1 | - | E2E p50 (ms) | 17545.2 | 16886.1 | -4% |

## Source files

- `results_bedrock_openai.gpt-daybreak-blue-5.6-sol_1kinput_2runs_daybreak-smoke_20260831_224817.json` vs `results_bedrock_openai.gpt-5.6-cyber_1kinput_2runs_daybreak-smoke_20260831_224830.json`
- `results_bedrock_openai.gpt-daybreak-blue-5.6-sol_1kinput_2runs_daybreak-smoke_20260831_224940.json` vs `results_bedrock_openai.gpt-5.6-cyber_1kinput_2runs_daybreak-smoke_20260831_225018.json`
