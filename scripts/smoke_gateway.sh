#!/usr/bin/env bash
# Prove the gateway streams valid OpenAI-shaped SSE before Agora is involved.
#
#   Terminal 1:  .venv/bin/uvicorn src.gateway.app:app --port 7860
#   Terminal 2:  scripts/smoke_gateway.sh
#
# Then repeat step 2 against your PUBLIC tunnel URL from a phone hotspot.
# If it answers from a network you do not control, Agora's servers can reach
# it too — that single check prevents a wasted day.
set -euo pipefail

BASE="${1:-http://localhost:7860}"
SECRET="$(grep -E '^GATEWAY_SHARED_SECRET=' .env | cut -d= -f2-)"

echo "== health =="
curl -fsS "$BASE/health"; echo; echo

echo "== technical — expect streamed content then [DONE] =="
curl -fsS -N -X POST "$BASE/v1/smoke/technical/chat/completions" \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"model":"x","stream":true,"messages":[{"role":"user","content":"hello"}]}'
echo

echo "== product — expect SILENCE: no content deltas, just [DONE] =="
curl -fsS -N -X POST "$BASE/v1/smoke/product/chat/completions" \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"model":"x","stream":true,"messages":[{"role":"user","content":"hello"}]}'
echo

echo "== bad token — expect 401 =="
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE/v1/smoke/technical/chat/completions" \
  -H "Authorization: Bearer wrong" -H "Content-Type: application/json" -d '{"messages":[]}'
