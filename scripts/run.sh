#!/usr/bin/env bash
# Local entrypoint: starts orchestrator + FastAPI backend + Next.js dashboard.
# Three processes share data/trading.db via the FastAPI layer.
#
# Ports are read from .env (HEALTHZ_PORT / API_PORT / WEB_PORT) with sensible
# defaults. Override any of them in .env if a port is taken on your machine.
#
# Usage:
#   scripts/run.sh           # everything (worker + api + web)
#   scripts/run.sh --once    # one orchestrator cycle, no api/web
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  echo "error: .venv not found. run 'make install' first (after creating a 3.11+ venv)." >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "error: .env not found. run 'cp .env.example .env' and fill in ALPACA_API_KEY/SECRET." >&2
  exit 1
fi
if [[ ! -d web/node_modules ]]; then
  echo "web/node_modules missing — installing…"
  (cd web && npm install --silent)
fi

# Source .env so the shell — and child processes — see the port overrides.
# `set -a` exports every var assigned while it's on; we flip it off after sourcing.
set -a
# shellcheck disable=SC1091
source .env
set +a

HEALTHZ_PORT="${HEALTHZ_PORT:-8081}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"

PY=.venv/bin/python
mkdir -p data

if [[ "${1:-}" == "--once" ]]; then
  exec "$PY" -m src.core.orchestrator --once
fi

PIDS=()
cleanup() {
  trap - TERM INT
  echo
  echo "stopping…"
  kill -TERM "${PIDS[@]}" 2>/dev/null || true
  wait
}
trap cleanup TERM INT

# Pre-flight: warn if any of the three ports is already bound. Keeps the
# error message clear instead of buried under uvicorn/next stack traces.
for p in "$HEALTHZ_PORT" "$API_PORT" "$WEB_PORT"; do
  if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "error: port $p already in use. Set HEALTHZ_PORT/API_PORT/WEB_PORT in .env to free ones." >&2
    exit 1
  fi
done

"$PY" -m src.core.orchestrator &
PIDS+=($!)

"$PY" -m uvicorn src.api.main:app --host 127.0.0.1 --port "$API_PORT" --log-level warning &
PIDS+=($!)

# Pass the API URL to Next.js so its /api/* rewrites point at the right port.
(cd web && NEXT_PUBLIC_API_URL="http://127.0.0.1:$API_PORT" \
  npm run dev -- --port "$WEB_PORT" --turbopack) &
PIDS+=($!)

cat <<EOF

  worker      pid ${PIDS[0]}  (healthz: http://localhost:$HEALTHZ_PORT/healthz)
  api         http://localhost:$API_PORT  (pid ${PIDS[1]})
  dashboard   http://localhost:$WEB_PORT  (pid ${PIDS[2]})

  Ctrl-C to stop all three.
EOF

# Wait for any process to exit. macOS ships bash 3.2 which lacks `wait -n`,
# so poll. 1s loop is plenty — we're not latency-sensitive on shutdown.
while kill -0 "${PIDS[0]}" 2>/dev/null \
   && kill -0 "${PIDS[1]}" 2>/dev/null \
   && kill -0 "${PIDS[2]}" 2>/dev/null; do
  sleep 1
done
EXIT=1
cleanup
exit $EXIT
