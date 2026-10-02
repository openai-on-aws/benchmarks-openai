#!/usr/bin/env bash
# Run a portable, paired CyberSOCEval matrix in Amazon Bedrock us-east-2.
#
# The default matrix compares the two Daybreak products with a general Sol
# control and Claude Opus 4.8. Set INCLUDE_FABLE=1 only after the AWS account
# has been deliberately opted into the provider_data_share retention mode
# required by Fable.
#
# Example:
#   PURPLELLAMA_DIR=../PurpleLlama N=10 ./quality/cyber/run_defensive_matrix.sh

set -euo pipefail
cd "$(dirname "$0")/../.."

export AWS_REGION="${AWS_REGION:-us-east-2}"
PYTHON="${PYTHON:-venv/bin/python}"
PURPLELLAMA_DIR="${PURPLELLAMA_DIR:?Set PURPLELLAMA_DIR to a PurpleLlama checkout}"
N="${N:-10}"
SEED="${SEED:-42}"
CONCURRENCY="${CONCURRENCY:-1}"
MAX_OUTPUT_TOKENS="${MAX_OUTPUT_TOKENS:-2048}"
TAG="${TAG:-portable-matrix}"
INCLUDE_FABLE="${INCLUDE_FABLE:-0}"

arms=(
  "mantle:openai.gpt-daybreak-blue-5.6-sol"
  "mantle:openai.gpt-5.6-cyber"
  "mantle:openai.gpt-5.6-sol"
  "runtime:us.anthropic.claude-opus-4-8"
)

if [ "$INCLUDE_FABLE" = "1" ]; then
  arms+=("runtime:us.anthropic.claude-fable-5")
fi

for arm in "${arms[@]}"; do
  backend="${arm%%:*}"
  model="${arm#*:}"
  echo "Running defensive matrix: backend=$backend model=$model n=$N seed=$SEED"
  "$PYTHON" quality/cyber/cybersoc_eval.py \
    --purplellama-dir "$PURPLELLAMA_DIR" \
    --backend "$backend" \
    --model "$model" \
    --n "$N" \
    --seed "$SEED" \
    --concurrency "$CONCURRENCY" \
    --max-output-tokens "$MAX_OUTPUT_TOKENS" \
    --no-structured-output \
    --tag "$TAG"
done

echo "Matrix complete. Compare the new files with:"
echo "  $PYTHON quality/cyber/compare_cybersoc.py <result.json> ... --out <report.md>"
