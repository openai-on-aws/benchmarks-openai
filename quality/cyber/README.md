# Cyber evaluation adapters

This directory contains cyber-specific evaluation and reporting paths for
Daybreak Blue, GPT-5.6 Cyber, general GPT-5.6 Sol, Claude Fable 5, and Claude
Opus 4.8 in Amazon Bedrock `us-east-2`.

## CyberSOCEval malware analysis

`cybersoc_eval.py` runs the 609-question malware-analysis subset from
PurpleLlama/CyberSecEval 4 through the Responses API. The source data is not
vendored. Clone it with its data submodule:

```bash
git clone --recurse-submodules https://github.com/meta-llama/PurpleLlama.git ../PurpleLlama
export AWS_REGION=us-east-2

python quality/cyber/cybersoc_eval.py \
  --purplellama-dir ../PurpleLlama \
  --backend mantle \
  --model openai.gpt-5.6-cyber \
  --n 10
```

Use `--n 0` (the default) for all questions. The result JSON contains exact-set
accuracy, Jaccard, precision/recall/F1, subgroup scores, errors, parse failures,
incomplete-response reasons, latency, usage, estimated cost, and source commits.
API, parse, and incomplete responses count against the primary attempted-task
score; a successful-call-only score is also included for diagnosis. The default
output cap is 2,048 tokens because reasoning-only truncations were observed at
512 tokens for both models and at 1,024 tokens for the Cyber model.

For a provider-portable matrix, use instruction-level JSON rather than a
provider-specific structured-output schema:

```bash
PURPLELLAMA_DIR=../PurpleLlama N=10 \
  ./quality/cyber/run_defensive_matrix.sh
```

The default arms are Daybreak Blue, Cyber, general Sol, and Opus 4.8. Fable is
excluded by default because it requires the AWS account to opt into the
`provider_data_share` retention mode. Set `INCLUDE_FABLE=1` only after that
governance decision has been made outside the benchmark.

Compare the generated files with protocol validation:

```bash
python quality/cyber/compare_cybersoc.py \
  quality/results/cybersoc_malware_*_portable-matrix_*.json \
  --out quality/results/CYBERSOC_COMPARISON.md
```

Full and truncated report inputs are different protocols. If
`--truncate-input` is needed for one model, use it for every comparison arm.
The full reports averaged roughly 66K input tokens in a 10-question pilot, so
estimate cost from a smoke run before scaling to 50 or all 609 questions.

## Cybench

`run_cybench.sh` invokes the maintained Inspect Evals adapter:

```bash
pip install 'inspect-evals[cybench]'
LIMIT=2 EVAL_NAMES=avatar ./quality/cyber/run_cybench.sh
```

The wrapper passes the Ohio Mantle `/openai/v1` base URL explicitly. Current
Inspect versions otherwise classify the `gpt-daybreak-*` alias as a non-frontier
model and derive the incompatible `/v1` path. It also defaults to one concurrent
sandbox, 4,096 output tokens per turn, 12 turns, and 15 minutes per sample; all
limits can be overridden with environment variables.

Kubernetes is the default sandbox because it can apply deny-by-default egress.
Docker mode gives the model Kali and unrestricted internet and therefore
requires an explicit `CYBENCH_ACKNOWLEDGE_RISKS=1`.

Summarize Inspect logs with cache-aware cost, cost per solve, tool/model calls,
and a failure taxonomy:

```bash
python quality/cyber/summarize_inspect.py \
  quality/results/inspect-cybench \
  --json-out quality/results/CYBER_AGENT_SUMMARY.json \
  --markdown-out quality/results/CYBER_AGENT_SUMMARY.md
```

Do not place production credentials in either sandbox or permit access to
non-benchmark targets. See
[`../../docs/daybreak-cyber-benchmark-plan.md`](../../docs/daybreak-cyber-benchmark-plan.md)
for the full safety procedure, benchmark selection analysis, licenses, cost
bounds, statistical plan, and upstream contribution strategy. The paper-backed
five-arm publication design is in
[`../../docs/cyber-benchmark-publication-strategy.md`](../../docs/cyber-benchmark-publication-strategy.md).
