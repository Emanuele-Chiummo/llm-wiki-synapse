#!/usr/bin/env bash
#
# Build an UNSIGNED iOS .ipa for sideloading, attached to each GitHub release
# (Synapse-<version>-unsigned.ipa). The native app is intentionally never
# code-signed by CI — users sign it on sideload with their own Apple ID
# (AltStore / Sideloadly). See ios/README.md §"Sideload dell'.ipa".
#
# Usage:  ./build-unsigned-ipa.sh [version]
#   version defaults to MARKETING_VERSION in project.yml.
#
# Output: ios/build/Synapse-<version>-unsigned.ipa  (build/ is gitignored)
#
set -euo pipefail
cd "$(dirname "$0")"

# Full Xcode is required to build for the device SDK (iphoneos); the Command
# Line Tools alone cannot. xcode-select frequently points at CLT on this
# machine, so override DEVELOPER_DIR for this invocation (see memory:
# ios-tauri-build-environment / synapse-native-ios-app).
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

VERSION="${1:-$(grep -m1 'MARKETING_VERSION:' project.yml | sed -E 's/.*"([^"]+)".*/\1/')}"
SCHEME="Synapse"
BUILD_DIR="build"
DERIVED="${BUILD_DIR}/DerivedData"
OUT_IPA="${BUILD_DIR}/Synapse-${VERSION}-unsigned.ipa"

echo "==> Building Synapse ${VERSION} (unsigned, iphoneos) with DEVELOPER_DIR=${DEVELOPER_DIR}"
xcodegen generate

xcodebuild \
  -project Synapse.xcodeproj \
  -scheme "${SCHEME}" \
  -configuration Release \
  -sdk iphoneos \
  -derivedDataPath "${DERIVED}" \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" \
  build

APP="${DERIVED}/Build/Products/Release-iphoneos/Synapse.app"
[ -d "${APP}" ] || { echo "ERROR: ${APP} not found after build"; exit 1; }

# Package the .app into the classic unsigned Payload/ → zip → .ipa layout.
rm -rf "${BUILD_DIR}/Payload" "${OUT_IPA}"
mkdir -p "${BUILD_DIR}/Payload"
cp -R "${APP}" "${BUILD_DIR}/Payload/"
( cd "${BUILD_DIR}" && zip -qry "Synapse-${VERSION}-unsigned.ipa" Payload )
rm -rf "${BUILD_DIR}/Payload"

echo "==> Done: ios/${OUT_IPA}"
ls -lh "${OUT_IPA}"
