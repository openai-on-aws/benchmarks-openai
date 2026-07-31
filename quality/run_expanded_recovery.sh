#!/usr/bin/env bash
# Recovery for the expanded sweep: re-run only what the 2026-07-30 AWS
# credential expiry killed — GDPval n48 judging (terra resume, mini, nano)
# and the three Mantle DeepSearchQA n=50 arms + their judging.
set -u
cd "$(dirname "$0")/.."

echo "=== GDPval n48 judging recovery $(date -u) ==="
for f in quality/results/gdpval_mantle_openai.gpt-5.6-terra_none_n48_20260729_162946.json \
         quality/results/gdpval_saas_gpt-5.4-mini_n48_20260729_185551.json \
         quality/results/gdpval_saas_gpt-5.4-nano_n48_20260729_192432.json; do
  AWS_REGION=us-east-1 python3 quality/gdpval_eval.py --n 48 --judge-only --judge-backend mantle --file "$f" \
    || echo "FAILED judge $f"
done
echo "GDPVAL48 JUDGE RECOVERY DONE $(date -u)"

echo "=== DeepSearchQA n=50 Mantle arms $(date -u) ==="
cd quality/deepsearchqa
source eval.env
dsq() {
  local region=$1 model=$2; shift 2
  echo "--- deepsearchqa mantle $model $(date -u +%H:%M) ---"
  AWS_REGION=$region python3 run_deepsearchqa.py --sample 50 --backend mantle --model "$model" --effort none \
    || echo "FAILED deepsearchqa $model"
}
dsq us-west-2 openai.gpt-5.6-luna
dsq us-west-2 openai.gpt-5.6-terra
dsq us-east-1 openai.gpt-5.6-sol

echo "=== DeepSearchQA judging (1P gpt-5.5) $(date -u) ==="
for f in ../results/deepsearchqa_mantle_*_n50_*.json; do
  case "$f" in *_judged.json) continue;; esac
  python3 judge_deepsearchqa.py --file "$f" || echo "FAILED judge $f"
done
echo "RECOVERY SWEEP DONE $(date -u)"
