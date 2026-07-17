#!/bin/bash
# Build Reelfy.app and install it to /Applications.
set -euo pipefail
cd "$(dirname "$0")"
SPIKE="$(cd ../spike && pwd)"
BUILD=build
APP="$BUILD/Reelfy.app"

echo "==> icon"
"$SPIKE/.venv/bin/python" gen_icon.py

echo "==> swiftc"
mkdir -p "$BUILD"
swiftc -O -o "$BUILD/Reelfy" main.swift

echo "==> bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/Reelfy" "$APP/Contents/MacOS/Reelfy"
cp Info.plist "$APP/Contents/Info.plist"
cp "$BUILD/Reelfy.icns" "$APP/Contents/Resources/Reelfy.icns"

echo "==> codesign (ad-hoc)"
codesign --force --deep -s - "$APP"

echo "==> install /Applications"
rm -rf /Applications/Reelfy.app
cp -R "$APP" /Applications/Reelfy.app

echo "OK -> /Applications/Reelfy.app"
