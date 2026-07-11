#!/usr/bin/env bash
# Remove the Synapse autonomous-loop launchd jobs for the current user.
set -euo pipefail
DOMAIN="gui/$(id -u)"
DEST="$HOME/Library/LaunchAgents"
for LABEL in com.synapse.daily-driver com.synapse.weekly-release; do
  launchctl bootout "$DOMAIN/${LABEL}" 2>/dev/null || true
  rm -f "$DEST/${LABEL}.plist"
  echo "removed: ${LABEL}"
done
echo "Done. (Logs under ~/Library/Logs/synapse-loop/ are left in place.)"
