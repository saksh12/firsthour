#!/usr/bin/env bash
# Share the FirstHour concept page (docs/mission-control.html) through a temporary
# public Cloudflare link. No Cloudflare account needed (a "quick tunnel").
#
#   scripts/share_concept.sh            # prints a https://<random>.trycloudflare.com link
#   PORT=9000 scripts/share_concept.sh
#
# Only this one page is served, never the rest of the repo (the repo is private).
# The link works while this script runs; Ctrl+C stops it and the link dies.
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-8799}"
PAGE="docs/mission-control.html"

CF="$(command -v cloudflared || true)"
for p in /opt/homebrew/bin/cloudflared /usr/local/bin/cloudflared; do
  [ -z "$CF" ] && [ -x "$p" ] && CF="$p"
done
if [ -z "$CF" ]; then
  echo "cloudflared is not installed. On macOS: brew install cloudflared" >&2
  exit 1
fi
[ -f "$PAGE" ] || { echo "missing $PAGE" >&2; exit 1; }

# Serve a copy from a scratch dir so nothing else in the repo is reachable.
SITE="$(mktemp -d)"
cp "$PAGE" "$SITE/index.html"
LOG="$(mktemp)"

cleanup() {
  kill "${TUNNEL_PID:-}" "${SERVER_PID:-}" 2>/dev/null || true
  rm -rf "$SITE" "$LOG"
}
trap cleanup EXIT INT TERM

python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$SITE" >/dev/null 2>&1 &
SERVER_PID=$!

"$CF" tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >"$LOG" 2>&1 &
TUNNEL_PID=$!

echo "Local:  http://localhost:$PORT/"
echo "Opening a Cloudflare tunnel..."

# The link only works once cloudflared has registered a connection with Cloudflare's
# edge, which needs outbound port 7844. Wait for that, not just for the URL.
URL=""
for _ in $(seq 1 45); do
  [ -z "$URL" ] && URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -n1 || true)"
  [ -n "$URL" ] && grep -q "Registered tunnel connection" "$LOG" && break
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "cloudflared exited early:" >&2; cat "$LOG" >&2; exit 1
  fi
  sleep 1
done

if ! grep -q "Registered tunnel connection" "$LOG"; then
  echo >&2
  echo "The tunnel could not connect to Cloudflare. This network is blocking outbound" >&2
  echo "port 7844 (venue Wi-Fi often does). Switch to a phone hotspot and run this again." >&2
  echo "The page still works locally at http://localhost:$PORT/ until you press Ctrl+C." >&2
  wait "$SERVER_PID"
  exit 1
fi

echo "Public: $URL"
echo "Anyone with this link can open the page while this runs. Ctrl+C to stop sharing."
wait "$TUNNEL_PID"
