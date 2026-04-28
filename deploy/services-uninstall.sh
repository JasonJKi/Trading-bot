#!/usr/bin/env bash
# Uninstall the launchd LaunchAgents on the SERVER. Run remotely via:
#
#   make mac-services-uninstall
set -euo pipefail

TARGET_DIR="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

for label in com.tradingbot.orchestrator com.tradingbot.api com.tradingbot.tunnel; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  rm -f "$TARGET_DIR/$label.plist"
  echo "removed: $label"
done
