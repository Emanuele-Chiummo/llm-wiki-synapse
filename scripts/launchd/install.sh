#!/usr/bin/env bash
# Install (or reinstall) the Synapse autonomous-loop launchd jobs for the current user.
#   Daily driver  → 07:03 every day        (com.synapse.daily-driver)
#   Weekly release → Friday 18:03           (com.synapse.weekly-release)
# Idempotent: safe to re-run after editing a plist or moving the repo.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DEST="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
mkdir -p "$DEST" "$HOME/Library/Logs/synapse-loop"

for LABEL in com.synapse.daily-driver com.synapse.weekly-release; do
  SRC="$HERE/${LABEL}.plist"
  OUT="$DEST/${LABEL}.plist"
  # Substitute path placeholders into the installed copy.
  sed -e "s|__REPO__|${REPO}|g" -e "s|__HOME__|${HOME}|g" "$SRC" >"$OUT"

  # Reload cleanly (bootout is harmless if not currently loaded).
  launchctl bootout   "$DOMAIN/${LABEL}"   2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$OUT"
  launchctl enable    "$DOMAIN/${LABEL}"
  echo "installed + loaded: ${LABEL}"
done

chmod +x "$REPO/scripts/loop-run.sh"
echo
echo "Active Synapse jobs:"
launchctl list | grep -E "com\.synapse\." || echo "  (none found — check errors above)"
echo
echo "Smoke-test without spending tokens:  DRY_RUN=1 bash \"$REPO/scripts/loop-run.sh\" daily"
echo "Fire a real daily run now:           launchctl kickstart -k $DOMAIN/com.synapse.daily-driver"
echo "Logs:                                ~/Library/Logs/synapse-loop/"
