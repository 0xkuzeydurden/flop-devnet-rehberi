#!/usr/bin/env bash
# Query the running backend without printing its access token.
set -euo pipefail
docker compose exec -T inference python3 - <<'PY'
import json
import os
import urllib.request

request = urllib.request.Request(
    'http://127.0.0.1:8000/health',
    headers={'Authorization': 'Bearer ' + os.environ['INFERENCE_API_KEY']},
)
with urllib.request.urlopen(request, timeout=15) as response:
    health = json.load(response)
checks = {
    'model_identity': health.get('model_identity') == 'measured',
    'model_hash': health.get('model_hash') == os.environ['MODEL_HASH'],
    'decode_policy_hash': health.get('decode_policy_hash') == os.environ['DECODE_POLICY_HASH'],
    'precision': str(health.get('precision', '')).lower() == os.environ['TOPLOC_PRECISION'].lower(),
}
for label, passed in checks.items():
    print(('OK' if passed else 'HATA') + ': ' + label)
if not all(checks.values()):
    raise SystemExit('Model kontrolü geçmedi. Miner servisini başlatmayın.')
print('Model kontrolü tamam. Miner servisini başlatabilirsiniz.')
PY
