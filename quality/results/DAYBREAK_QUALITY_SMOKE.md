# Daybreak Blue vs Daybreak Red — quality smoke

- Backend: `mantle`
- Reasoning effort: `model default`
- Accuracy uses exact task scorers. Cost is estimated from recorded token usage and repository list prices.
- This is a small smoke sample; Wilson intervals are shown to make the uncertainty explicit.

| Task | N | Model | Correct | Accuracy (95% Wilson CI) | Mean latency | Total cost | Cost/correct | Mean reasoning tokens |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| gsm8k | 10 | Daybreak Blue | 9 | 90.0% (59.6%–98.2%) | 3,057.1 ms | $0.045556 | $0.005062 | 38.3 |
| gsm8k | 10 | Daybreak Red | 9 | 90.0% (59.6%–98.2%) | 1,399.0 ms | $0.090956 | $0.010106 | 39.6 |
| math500 | 10 | Daybreak Blue | 9 | 90.0% (59.6%–98.2%) | 3,555.8 ms | $0.090150 | $0.010017 | 125.5 |
| math500 | 10 | Daybreak Red | 9 | 90.0% (59.6%–98.2%) | 2,393.5 ms | $0.172576 | $0.019175 | 123.7 |
| mmlu_pro | 14 | Daybreak Blue | 12 | 85.7% (60.1%–96.0%) | 4,132.2 ms | $0.078507 | $0.006542 | 117.4 |
| mmlu_pro | 14 | Daybreak Red | 11 | 78.6% (52.4%–92.4%) | 3,937.5 ms | $0.328515 | $0.029865 | 232.8 |

## Aggregate

| Model | Correct/attempted | Accuracy | Errors | Mean latency | Total cost | Cost/correct | Mean output tokens | Mean reasoning tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Daybreak Blue | 30/34 | 88.2% | 0 | 3,646.5 ms | $0.214213 | $0.007140 | 165.4 | 96.5 |
| Daybreak Red | 29/34 | 85.3% | 0 | 2,736.8 ms | $0.592047 | $0.020415 | 185.6 | 143.9 |

## Paired outcomes

| Task | Both correct | Only Daybreak Blue | Only Daybreak Red | Neither |
|---|---:|---:|---:|---:|
| gsm8k | 9 | 0 | 0 | 1 |
| math500 | 9 | 0 | 0 | 1 |
| mmlu_pro | 11 | 1 | 0 | 2 |

## Source files

- `quickeval_gsm8k_mantle_openai.gpt-daybreak-blue-5.6-sol_20260901_091126.json` vs `quickeval_gsm8k_mantle_openai.gpt-5.6-cyber_20260901_091246.json`
- `quickeval_math500_mantle_openai.gpt-daybreak-blue-5.6-sol_20260901_091057.json` vs `quickeval_math500_mantle_openai.gpt-5.6-cyber_20260901_091235.json`
- `quickeval_mmlu_pro_mantle_openai.gpt-daybreak-blue-5.6-sol_20260901_091036.json` vs `quickeval_mmlu_pro_mantle_openai.gpt-5.6-cyber_20260901_091216.json`
