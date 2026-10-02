#!/usr/bin/env bash
# Run the maintained Inspect Evals adapter for Cybench against Ohio Bedrock.
#
# K8s is the safe default because it can enforce network policy. Docker is only
# allowed when the caller explicitly acknowledges Cybench's unrestricted-network
# warning:
#   SANDBOX=docker CYBENCH_ACKNOWLEDGE_RISKS=1 LIMIT=1 ./quality/cyber/run_cybench.sh
#
# Examples:
#   LIMIT=2 EVAL_NAMES=avatar ./quality/cyber/run_cybench.sh
#   MODELS="openai/bedrock/gpt-5.6-cyber" ./quality/cyber/run_cybench.sh

set -euo pipefail
cd "$(dirname "$0")/../.."

export AWS_REGION="${AWS_REGION:-us-east-2}"

if [ -z "${INSPECT:-}" ] && [ -x "venv/bin/inspect" ]; then
  INSPECT="venv/bin/inspect"
else
  INSPECT="${INSPECT:-inspect}"
fi
MODELS="${MODELS:-openai/bedrock/gpt-daybreak-blue-5.6-sol openai/bedrock/gpt-5.6-cyber}"
MANTLE_BASE_URL="${MANTLE_BASE_URL:-https://bedrock-mantle.${AWS_REGION}.api.aws/openai/v1}"
SANDBOX="${SANDBOX:-k8s}"
VARIANT="${VARIANT:-hard}"
LIMIT="${LIMIT:-}"
EVAL_NAMES="${EVAL_NAMES:-}"
MAX_CONNECTIONS="${MAX_CONNECTIONS:-1}"
MAX_SAMPLES="${MAX_SAMPLES:-1}"
MAX_TOKENS="${MAX_TOKENS:-4096}"
TURN_LIMIT="${TURN_LIMIT:-12}"
TIME_LIMIT="${TIME_LIMIT:-900}"
LOG_DIR="${LOG_DIR:-quality/results/inspect-cybench}"

if ! command -v "$INSPECT" >/dev/null 2>&1; then
  echo "ERROR: inspect is not installed." >&2
  echo "Install the maintained adapter with: pip install 'inspect-evals[cybench]'" >&2
  exit 1
fi

case "$SANDBOX" in
  k8s)
    ;;
  docker)
    if [ "${CYBENCH_ACKNOWLEDGE_RISKS:-}" != "1" ]; then
      echo "ERROR: Docker gives the model a Kali environment and unrestricted internet." >&2
      echo "Set CYBENCH_ACKNOWLEDGE_RISKS=1 only after accepting that risk." >&2
      exit 2
    fi
    export CYBENCH_ACKNOWLEDGE_RISKS=1
    ;;
  *)
    echo "ERROR: SANDBOX must be k8s or docker." >&2
    exit 2
    ;;
esac

mkdir -p "$LOG_DIR"
read -r -a MODEL_ARRAY <<< "$MODELS"

for model in "${MODEL_ARRAY[@]}"; do
  command=(
    "$INSPECT" eval inspect_evals/cybench
    --model "$model"
    --model-base-url "$MANTLE_BASE_URL"
    --max-connections "$MAX_CONNECTIONS"
    --max-samples "$MAX_SAMPLES"
    --max-tokens "$MAX_TOKENS"
    --turn-limit "$TURN_LIMIT"
    --time-limit "$TIME_LIMIT"
    --no-fail-on-error
    --log-dir "$LOG_DIR"
    -M "aws_region=$AWS_REGION"
    -M responses_api=true
    -M responses_store=false
    -T "sandbox_type=$SANDBOX"
    -T "variant_names=$VARIANT"
  )
  [ -n "$LIMIT" ] && command+=(--limit "$LIMIT")
  [ -n "$EVAL_NAMES" ] && command+=(-T "eval_names=$EVAL_NAMES")

  echo "Running Cybench: model=$model region=$AWS_REGION sandbox=$SANDBOX base_url=$MANTLE_BASE_URL"
  "${command[@]}"
done
