#!/usr/bin/env bash
# Expanded agentic evidence run:
#   1) hard suite, all 5 models, 10 repeats
#   2) core suite, all 5 models, 20 repeats (tighter CIs than the original n=5)
#   3) luna effort sweep (low, medium) on both suites, 10 repeats
set -u
cd "$(dirname "$0")"

run() {
  local region=$1 backend=$2 model=$3 suite=$4 repeats=$5; shift 5
  echo "=== $suite $backend $model $* n=$repeats $(date -u +%H:%M) ==="
  AWS_REGION=$region python3 agentic_evals.py --backend "$backend" --model "$model" \
    --suite "$suite" --repeats "$repeats" "$@" || echo "FAILED $suite $model $*"
}

for suite_n in "hard 10" "core 20"; do
  set -- $suite_n
  suite=$1; n=$2
  run us-west-2 mantle openai.gpt-5.6-luna  "$suite" "$n" --effort none
  run us-west-2 mantle openai.gpt-5.6-terra "$suite" "$n" --effort none
  run us-east-1 mantle openai.gpt-5.6-sol   "$suite" "$n" --effort none
  run us-west-2 saas gpt-5.4-mini           "$suite" "$n"
  run us-west-2 saas gpt-5.4-nano           "$suite" "$n"
done

for eff in low medium; do
  run us-west-2 mantle openai.gpt-5.6-luna all 10 --effort "$eff"
done

echo "EXPANDED AGENTIC DONE $(date -u)"
