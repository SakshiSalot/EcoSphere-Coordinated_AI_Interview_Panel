#!/usr/bin/env bash
# Bring the whole product up, in the right order, with the addresses wired.
#
#   scripts/serve.sh
#
# A quick-tunnel gets a NEW random hostname every time it starts, and the
# gateway reads GATEWAY_PUBLIC_URL once at import. So a restarted tunnel leaves
# .env pointing at a dead address and the gateway holding a stale one — the
# agents then join the channel and sit in silence, because Agora cannot reach
# the endpoint it was told to call. That has cost us three debugging sessions;
# this script exists so it cannot happen a fourth time.
#
# Order matters: tunnel first (to learn the URL), write .env, THEN start the
# gateway so it reads the address that actually works.
set -euo pipefail

cd "$(dirname "$0")/.."
PORT=7860
PY=.venv/bin/python

command -v cloudflared >/dev/null || { echo "cloudflared not installed: brew install cloudflared"; exit 1; }
test -x "$PY" || { echo "no venv — python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

echo "  stopping anything already running"
pkill -f "src.gateway.app" 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 2

if [ ! -f frontend/dist/index.html ]; then
  echo "  building the browser app"
  npm --prefix frontend install --silent
  npm --prefix frontend run build --silent
fi

echo "  starting the tunnel"
LOG=/tmp/echosphere-tunnel.log
: > "$LOG"   # fixed path so a failed tunnel is debuggable
cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate > "$LOG" 2>&1 &
TUNNEL_PID=$!

# The hostname appears in the log a moment before DNS resolves it, so wait for
# the address to actually answer rather than for the line to print.
#
# ANY HTTP response counts, which is why this is not `curl -f`. The gateway has
# not been started yet — it starts below, deliberately, so it reads the address
# this loop discovers — so the tunnel correctly returns 502 here. Requiring a
# healthy /health meant the condition could never be true, and this loop ran
# its full sixty iterations on every single start: roughly two minutes of
# silent waiting before anything happened. A 502 is the proof we actually want,
# because it means DNS resolved and cloudflared is answering.
for _ in $(seq 1 45); do
  URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$LOG" | head -1 || true)
  if [ -n "${URL:-}" ] && curl -sS -o /dev/null --max-time 5 "$URL/" 2>/dev/null; then
    break
  fi
  sleep 2
done
[ -n "${URL:-}" ] || { echo "  tunnel never printed a URL — see $LOG"; kill $TUNNEL_PID; exit 1; }

"$PY" - "$URL" <<'PY'
import sys, re, pathlib
p = pathlib.Path(".env")
s = p.read_text()
url = sys.argv[1]
if re.search(r"(?m)^GATEWAY_PUBLIC_URL=", s):
    s = re.sub(r"(?m)^GATEWAY_PUBLIC_URL=.*$", f"GATEWAY_PUBLIC_URL={url}", s)
else:
    s = s.rstrip() + f"\nGATEWAY_PUBLIC_URL={url}\n"
p.write_text(s)
PY
echo "  public URL: $URL   (written to .env)"

echo "  starting the gateway"
"$PY" -m src.gateway.app > /tmp/echosphere-gateway.log 2>&1 &
GATEWAY_PID=$!

for _ in $(seq 1 40); do
  curl -fsS --max-time 3 "http://localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done

curl -fsS --max-time 3 "http://localhost:$PORT/health" >/dev/null 2>&1 \
  || { echo "  gateway failed to start:"; tail -20 /tmp/echosphere-gateway.log; exit 1; }

echo "  checking Agora can reach it"
curl -fsS --max-time 25 "$URL/health" >/dev/null 2>&1 \
  && echo "  reachable from the internet" \
  || echo "  WARNING: the public URL did not answer — agents will join and stay silent"

cat <<EOF

  ─────────────────────────────────────────────────────────────
   Open  http://localhost:$PORT
   Logs  tail -f /tmp/echosphere-gateway.log
   Stop  make kill
  ─────────────────────────────────────────────────────────────

EOF

trap 'kill $GATEWAY_PID $TUNNEL_PID 2>/dev/null || true' INT TERM
wait $GATEWAY_PID
