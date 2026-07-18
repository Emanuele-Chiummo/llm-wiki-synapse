#!/usr/bin/env bash
#
# testflight.sh — archive the Synapse iOS app and upload the build to TestFlight.
#
# STATUS: SCAFFOLD — cannot run to completion in the dev/CI sandbox because it
# needs a *paid* Apple Developer Program membership + an App Store Connect API key.
# See the "TestFlight" section of ios/README.md for the full prerequisite list.
# The script refuses to run until those secrets are provided, rather than pretend.
#
# Usage (once credentials exist), from repo root:
#   ASC_KEY_ID=XXXX ASC_ISSUER_ID=xxxx-... ASC_KEY_P8=/path/AuthKey_XXXX.p8 \
#   TEAM_ID=YOURPAIDTEAM ios/scripts/testflight.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."          # -> ios/
ROOT="$(pwd)"
SCHEME="Synapse"
ARCHIVE="$ROOT/build/Synapse.xcarchive"
EXPORT_DIR="$ROOT/build/export"
EXPORT_PLIST="$ROOT/ExportOptions-AppStore.plist"

: "${DEVELOPER_DIR:=/Applications/Xcode.app/Contents/Developer}"
export DEVELOPER_DIR

# ── Preflight: require the credentials TestFlight needs ─────────────────────────
missing=()
[ -n "${ASC_KEY_ID:-}" ]    || missing+=("ASC_KEY_ID (App Store Connect API key id)")
[ -n "${ASC_ISSUER_ID:-}" ] || missing+=("ASC_ISSUER_ID (App Store Connect issuer id)")
[ -n "${ASC_KEY_P8:-}" ]    || missing+=("ASC_KEY_P8 (path to the AuthKey_*.p8 file)")
[ -n "${TEAM_ID:-}" ]       || missing+=("TEAM_ID (paid Apple Developer Program team id)")
if [ "${#missing[@]}" -gt 0 ]; then
  echo "ERROR: TestFlight upload is blocked — missing required credentials:" >&2
  for m in "${missing[@]}"; do echo "  - $m" >&2; done
  echo "" >&2
  echo "These are owner-provided secrets (a paid Apple Developer account is required)." >&2
  echo "See ios/README.md → 'TestFlight'. This script will NOT fabricate them." >&2
  exit 2
fi
[ -f "$ASC_KEY_P8" ] || { echo "ERROR: ASC_KEY_P8 not found at $ASC_KEY_P8" >&2; exit 2; }

echo "==> Regenerating project"
xcodegen generate

echo "==> Archiving $SCHEME (Release)"
xcodebuild -project Synapse.xcodeproj -scheme "$SCHEME" \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath "$ARCHIVE" \
  DEVELOPMENT_TEAM="$TEAM_ID" \
  clean archive

echo "==> Exporting App Store ipa"
# Inject the real team id into a temp copy of the export plist.
tmp_plist="$(mktemp)"
sed "s/REPLACE_WITH_PAID_TEAM_ID/$TEAM_ID/" "$EXPORT_PLIST" > "$tmp_plist"
xcodebuild -exportArchive \
  -archivePath "$ARCHIVE" \
  -exportOptionsPlist "$tmp_plist" \
  -exportPath "$EXPORT_DIR" \
  -allowProvisioningUpdates \
  -authenticationKeyID "$ASC_KEY_ID" \
  -authenticationKeyIssuerID "$ASC_ISSUER_ID" \
  -authenticationKeyPath "$ASC_KEY_P8"
rm -f "$tmp_plist"

IPA="$(ls "$EXPORT_DIR"/*.ipa | head -1)"
echo "==> Uploading $IPA to TestFlight"
xcrun altool --upload-app --type ios --file "$IPA" \
  --apiKey "$ASC_KEY_ID" --apiIssuer "$ASC_ISSUER_ID"

echo "==> Done. The build will appear in App Store Connect → TestFlight after processing."
