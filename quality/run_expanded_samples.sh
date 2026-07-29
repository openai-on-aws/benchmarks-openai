#!/usr/bin/env bash
# Expanded-sample sweep: DeepSearchQA stratified-50 and GDPval n=48,
# across the repo's five standard arms, then judging.
#
# DeepSearchQA arms run sequentially so the shared search cache fills once
# (search-cache.json writes are not cross-process-safe). Judge: gpt-5.5 on
# the 1P key (same judge identity as the n=20 runs). GDPval judge: gpt-5.5
# on Bedrock Mantle (same as the n=24 runs). One judge backend per suite.
set -u
cd "$(dirname "$0")/.."

echo "=== GDPval n=48 sweep $(date -u) ==="
gdp() {
  local region=$1 backend=$2 model=$3; shift 3
  echo "--- gdpval $backend $model $* $(date -u +%H:%M) ---"
  AWS_REGION=$region python3 quality/gdpval_eval.py --n 48 --backend "$backend" --model "$model" "$@" \
    || echo "FAILED gdpval $model"
}
gdp us-west-2 mantle openai.gpt-5.6-luna  --effort none
gdp us-west-2 mantle openai.gpt-5.6-terra --effort none
gdp us-east-1 mantle openai.gpt-5.6-sol   --effort none
gdp us-west-2 saas gpt-5.4-mini
gdp us-west-2 saas gpt-5.4-nano

echo "=== GDPval judging (Mantle gpt-5.5) $(date -u) ==="
for f in quality/results/gdpval_*_n48_*.json; do
  case "$f" in *_judged.json) continue;; esac
  AWS_REGION=us-east-1 python3 quality/gdpval_eval.py --n 48 --judge-only --judge-backend mantle --file "$f" \
    || echo "FAILED judge $f"
done
echo "GDPVAL48 DONE $(date -u)"

echo "=== DeepSearchQA n=50 sweep $(date -u) ==="
cd quality/deepsearchqa
source eval.env
dsq() {
  local region=$1 backend=$2 model=$3; shift 3
  echo "--- deepsearchqa $backend $model $* $(date -u +%H:%M) ---"
  AWS_REGION=$region python3 run_deepsearchqa.py --sample 50 --backend "$backend" --model "$model" "$@" \
    || echo "FAILED deepsearchqa $model"
}
dsq us-west-2 mantle openai.gpt-5.6-luna  --effort none
dsq us-west-2 mantle openai.gpt-5.6-terra --effort none
dsq us-east-1 mantle openai.gpt-5.6-sol   --effort none
dsq us-west-2 saas gpt-5.4-mini
dsq us-west-2 saas gpt-5.4-nano

echo "=== DeepSearchQA judging (1P gpt-5.5) $(date -u) ==="
for f in ../results/deepsearchqa_*_n50_*.json; do
  case "$f" in *_judged.json) continue;; esac
  python3 judge_deepsearchqa.py --file "$f" || echo "FAILED judge $f"
done
echo "EXPANDED SWEEP DONE $(date -u)"
