# CyberSOCEval comparison

Protocol: `full` reports, seed `42`, max output `2048`, structured output `False`, dataset `4be64c3a24442b51c76175e6ec67722cc3f5fe38`.

| Model | Exact | Jaccard | F1 | Latency | Cost | Cost/task | Cost/exact | Errors | Refusals | Incomplete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mantle/openai.gpt-daybreak-blue-5.6-sol | 0/3 (0.0%) | 0.111 | 0.167 | 9798.7 ms | $1.1674 | $0.3891 | — | 0 | 0 | 0 |
| mantle/openai.gpt-5.6-cyber | 0/3 (0.0%) | 0.167 | 0.267 | 6387.0 ms | $2.9609 | $0.9870 | — | 0 | 0 | 0 |
| mantle/openai.gpt-5.6-sol | 0/3 (0.0%) | 0.083 | 0.133 | 7892.6 ms | $0.8451 | $0.2817 | — | 0 | 0 | 0 |
| runtime/us.anthropic.claude-opus-4-8 | 0/3 (0.0%) | 0.250 | 0.356 | 3011.8 ms | $1.7008 | $0.5669 | — | 0 | 0 | 0 |

## Paired Jaccard outcomes

- `mantle/openai.gpt-daybreak-blue-5.6-sol` vs `mantle/openai.gpt-5.6-cyber`: 1–1, 1 ties.
- `mantle/openai.gpt-daybreak-blue-5.6-sol` vs `mantle/openai.gpt-5.6-sol`: 1–0, 2 ties.
- `mantle/openai.gpt-daybreak-blue-5.6-sol` vs `runtime/us.anthropic.claude-opus-4-8`: 1–1, 1 ties.
- `mantle/openai.gpt-5.6-cyber` vs `mantle/openai.gpt-5.6-sol`: 1–0, 2 ties.
- `mantle/openai.gpt-5.6-cyber` vs `runtime/us.anthropic.claude-opus-4-8`: 0–1, 2 ties.
- `mantle/openai.gpt-5.6-sol` vs `runtime/us.anthropic.claude-opus-4-8`: 0–1, 2 ties.

Cost per exact answer is intentionally left undefined when an arm has no exact successes. Small pilots validate the harness and must not be presented as model rankings.
