#!/usr/bin/env bash
# DeepSearchQA: run the frozen 20-question sample across the repo's five
# standard arms, then judge everything.
set -u
cd "$(dirname "$0")"
source eval.env

run() {
  local region=$1 backend=$2 model=$3; shift 3
  echo "=== $backend $model $* $(date -u +%H:%M) ==="
  AWS_REGION=$region python3 run_deepsearchqa.py --backend "$backend" --model "$model" "$@" \
    || echo "FAILED $model"
}

run us-west-2 mantle openai.gpt-5.6-luna  --effort none
run us-west-2 mantle openai.gpt-5.6-terra --effort none
run us-east-1 mantle openai.gpt-5.6-sol   --effort none
run us-west-2 saas gpt-5.4-mini
run us-west-2 saas gpt-5.4-nano

echo "=== judging all ==="
python3 judge_deepsearchqa.py
echo "DEEPSEARCHQA DONE $(date -u)"
