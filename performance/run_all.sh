#!/usr/bin/env bash
# Run the full latency matrix on both backends and build the comparison report.
#
# Usage:
#   export OPENAI_API_KEY=sk-...          # your OpenAI 1P key
#   export AWS_REGION=us-west-2           # IAM creds via profile/env/role
#   ./performance/run_all.sh                                  # gpt-5.6-luna, full matrix, 25 runs
#   BEDROCK_MODEL=openai.gpt-5.6-terra OPENAI_MODEL=gpt-5.6-terra ./performance/run_all.sh
#   RUNS=5 SIZES="1k 10k" ./performance/run_all.sh            # quicker pass
#
# The two backends run sequentially (Bedrock first). Skip one with
# SKIP_BEDROCK=1 or SKIP_OPENAI=1.

set -euo pipefail
cd "$(dirname "$0")/.."

BEDROCK_MODEL="${BEDROCK_MODEL:-openai.gpt-5.6-luna}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-5.6-luna}"
RUNS="${RUNS:-25}"
SIZES="${SIZES:-1k 5k 10k 20k}"
EFFORT="${EFFORT:-}"

EFFORT_ARG=()
[ -n "$EFFORT" ] && EFFORT_ARG=(--effort "$EFFORT")

echo "=== Latency matrix: sizes=[$SIZES] runs=$RUNS effort=${EFFORT:-default} ==="

if [ -z "${SKIP_BEDROCK:-}" ]; then
  echo ""
  echo "--- Preflight: Bedrock model availability (${AWS_REGION:-us-west-2}) ---"
  python3 performance/benchmark.py --backend bedrock --list-models | grep -x "$BEDROCK_MODEL" \
    || { echo "ERROR: $BEDROCK_MODEL not available in ${AWS_REGION:-us-west-2}"; exit 1; }
  echo ""
  echo "--- Bedrock: $BEDROCK_MODEL ---"
  # shellcheck disable=SC2086
  python3 performance/benchmark.py --backend bedrock --model "$BEDROCK_MODEL" \
    --runs="$RUNS" "${EFFORT_ARG[@]}" $SIZES
fi

if [ -z "${SKIP_OPENAI:-}" ]; then
  : "${OPENAI_API_KEY:?ERROR: OPENAI_API_KEY is not set — export your OpenAI 1P key}"
  echo ""
  echo "--- OpenAI 1P: $OPENAI_MODEL ---"
  # shellcheck disable=SC2086
  python3 performance/benchmark.py --backend openai --model "$OPENAI_MODEL" \
    --runs="$RUNS" "${EFFORT_ARG[@]}" $SIZES
fi

echo ""
echo "--- Comparison report ---"
python3 performance/compare.py --model-a "$BEDROCK_MODEL" --model-b "$OPENAI_MODEL"
