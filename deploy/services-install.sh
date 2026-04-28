#!/usr/bin/env bash
# Install/refresh the launchd LaunchAgents on the SERVER. Run remotely via:
#
#   make mac-services-install
#
# What it does:
#   1. Reads APP_DIR (default ~/Trading-bot) and API_PORT (from .env, default 8000).
#   2. Substitutes placeholders in deploy/launchd/*.plist.
#   3. Installs into ~/Library/LaunchAgents/.
#   4. Reloads via `launchctl bootout` + `launchctl bootstrap`.
#
# Idempotent — safe to run on every deploy (in fact `mac-deploy` calls it).
# No sudo: LaunchAgents live in $HOME and run as the current user. Requires
# auto-login on the server so they survive reboots.
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/Trading-bot}"
TARGET_DIR="$HOME/Library/LaunchAgents"
TEMPLATE_DIR="$APP_DIR/deploy/launchd"

# API_PORT comes from .env. Fall back to 8000.
API_PORT=8000
if [[ -f "$APP_DIR/.env" ]]; then
  port_line=$(grep -E '^API_PORT=' "$APP_DIR/.env" || true)
  if [[ -n "$port_line" ]]; then
    API_PORT="${port_line#API_PORT=}"
  fi
fi

mkdir -p "$TARGET_DIR" "$APP_DIR/logs"

# launchctl uses gui/$UID for per-user agents.
DOMAIN="gui/$(id -u)"

# Always install orchestrator + api. Install the tunnel agent only if cloudflared
# credentials are present (~/.cloudflared/config.yml shipped via mac-tunnel-creds-push).
LABELS=(com.tradingbot.orchestrator com.tradingbot.api)
if [[ -f "$HOME/.cloudflared/config.yml" ]]; then
  LABELS+=(com.tradingbot.tunnel)
fi

failed_labels=()
for label in "${LABELS[@]}"; do
  src="$TEMPLATE_DIR/$label.plist"
  dst="$TARGET_DIR/$label.plist"
  if [[ ! -f "$src" ]]; then
    echo "missing template: $src" >&2
    failed_labels+=("$label (no template)")
    continue
  fi
  sed -e "s|__APP_DIR__|$APP_DIR|g" \
      -e "s|__API_PORT__|$API_PORT|g" \
      -e "s|__HOME__|$HOME|g" \
      "$src" > "$dst"

  # Bootout if already loaded (ignore "not loaded" error), then bootstrap.
  # The half-second gap matters: launchd's bootout returns before the job is
  # fully evicted, and a too-fast bootstrap on the same label can fail with
  # "Input/output error" (launchctl exit 5). The sleep is empirical.
  if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    sleep 0.5
  fi

  # Don't let one agent's failure stop the rest. set -e is on for everything
  # else, so we wrap just this call.
  if launchctl bootstrap "$DOMAIN" "$dst" 2>&1; then
    echo "loaded: $label"
  else
    echo "FAILED: $label (will not retry — check 'launchctl print $DOMAIN/$label')" >&2
    failed_labels+=("$label")
  fi
done

echo
echo "agents installed to $TARGET_DIR"
echo "logs in $APP_DIR/logs/"

if [[ ${#failed_labels[@]} -gt 0 ]]; then
  echo
  echo "WARNING: ${#failed_labels[@]} agent(s) failed:" >&2
  printf '  %s\n' "${failed_labels[@]}" >&2
  exit 1
fi
