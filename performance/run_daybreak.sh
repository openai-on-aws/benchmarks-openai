#!/usr/bin/env bash
# Compare Daybreak Blue and Daybreak Red/Cyber on the in-region Ohio endpoint.
#
# Examples:
#   ./performance/run_daybreak.sh
#   PROFILE=core ./performance/run_daybreak.sh
#   PROFILE=full RUNS=30 CONCURRENCY=4 ./performance/run_daybreak.sh
#   MODELS="openai.gpt-5.6-cyber" PROFILE=smoke ./performance/run_daybreak.sh
#
# These models are available through bedrock-mantle only. They do not have
# bedrock-runtime inference-profile IDs, and they require separate access approval.

set -euo pipefail
cd "$(dirname "$0")/.."

export AWS_REGION="${AWS_REGION:-us-east-2}"

PROFILE="${PROFILE:-smoke}"
MODELS="${MODELS:-openai.gpt-daybreak-blue-5.6-sol openai.gpt-5.6-cyber}"
CONCURRENCY="${CONCURRENCY:-1}"
EFFORT="${EFFORT:-}"
PYTHON="${PYTHON:-python3}"

case "$PROFILE" in
  smoke)
    RUNS="${RUNS:-2}"
    SIZES="${SIZES:-1k}"
    # Reasoning tokens count against max_output_tokens. A 128-token cap can
    # finish before either model emits visible text, so smoke uses 1024.
    OUTPUTS="${OUTPUTS:-1024}"
    WARMUPS="${WARMUPS:-0}"
    ;;
  core)
    RUNS="${RUNS:-10}"
    SIZES="${SIZES:-1k 10k}"
    OUTPUTS="${OUTPUTS:-256,1024}"
    WARMUPS="${WARMUPS:-1}"
    ;;
  full)
    RUNS="${RUNS:-25}"
    SIZES="${SIZES:-1k 5k 10k 20k}"
    OUTPUTS="${OUTPUTS:-256,1024,4096}"
    WARMUPS="${WARMUPS:-2}"
    ;;
  *)
    echo "ERROR: PROFILE must be smoke, core, or full" >&2
    exit 2
    ;;
esac

read -r -a MODEL_ARRAY <<< "$MODELS"
read -r -a SIZE_ARRAY <<< "$SIZES"

echo "Daybreak/Cyber performance matrix"
echo "Region:      $AWS_REGION"
echo "Profile:     $PROFILE"
echo "Models:      $MODELS"
echo "Sizes:       $SIZES"
echo "Outputs:     $OUTPUTS"
echo "Runs:        $RUNS"
echo "Warmups:     $WARMUPS per output configuration"
echo "Concurrency: $CONCURRENCY"

echo
echo "Preflight: checking approved models on bedrock-mantle"
AVAILABLE="$("$PYTHON" performance/benchmark.py --backend bedrock --list-models)"
for model in "${MODEL_ARRAY[@]}"; do
  if ! grep -Fxq "$model" <<< "$AVAILABLE"; then
    echo "ERROR: $model is not listed for the active AWS identity in $AWS_REGION." >&2
    echo "Confirm Trusted Access for Cyber and the model-specific AWS approval." >&2
    exit 1
  fi
done

for model in "${MODEL_ARRAY[@]}"; do
  echo
  echo "Running $model"
  benchmark_command=(
    "$PYTHON" performance/benchmark.py
    --backend bedrock
    --model "$model"
    --runs "$RUNS"
    --outputs "$OUTPUTS"
    --warmups "$WARMUPS"
    --concurrency "$CONCURRENCY"
    --tag "daybreak-$PROFILE"
  )
  [ -n "$EFFORT" ] && benchmark_command+=(--effort "$EFFORT")
  benchmark_command+=("${SIZE_ARRAY[@]}")
  "${benchmark_command[@]}"
done

if [ "${#MODEL_ARRAY[@]}" -eq 2 ]; then
  case "${MODEL_ARRAY[0]}" in
    *daybreak-blue*) LABEL_A="Daybreak Blue" ;;
    *cyber*) LABEL_A="Daybreak Red" ;;
    *) LABEL_A="${MODEL_ARRAY[0]}" ;;
  esac
  case "${MODEL_ARRAY[1]}" in
    *daybreak-blue*) LABEL_B="Daybreak Blue" ;;
    *cyber*) LABEL_B="Daybreak Red" ;;
    *) LABEL_B="${MODEL_ARRAY[1]}" ;;
  esac
  echo
  echo "Building $LABEL_A vs $LABEL_B comparison"
  "$PYTHON" performance/compare.py \
    --backend-a bedrock \
    --backend-b bedrock \
    --model-a "${MODEL_ARRAY[0]}" \
    --model-b "${MODEL_ARRAY[1]}" \
    --label-a "$LABEL_A" \
    --label-b "$LABEL_B" \
    --out performance/results/DAYBREAK_COMPARISON.md
fi
