#!/usr/bin/env bash
# Fly entrypoint: runs the trading orchestrator and the Streamlit dashboard side
# by side. They share the same machine + volume so the dashboard reads the same
# SQLite file the orchestrator writes to.
#
# Exits if either process dies — Fly will restart the machine.
set -euo pipefail

cleanup() {
  trap - TERM INT
  kill -TERM "$WORKER_PID" "$DASHBOARD_PID" 2>/dev/null || true
  wait
}
trap cleanup TERM INT

python -m src.core.orchestrator &
WORKER_PID=$!

streamlit run dashboard/app.py \
  --server.port 8080 \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false &
DASHBOARD_PID=$!

# Wait for whichever exits first; propagate its exit code.
wait -n "$WORKER_PID" "$DASHBOARD_PID"
EXIT=$?
cleanup
exit $EXIT
