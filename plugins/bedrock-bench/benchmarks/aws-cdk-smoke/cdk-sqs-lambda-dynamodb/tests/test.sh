#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
cd /app
if npm run build && npm run synth && node /tests/verify.cjs; then
    printf '1\n' > /logs/verifier/reward.txt
fi
