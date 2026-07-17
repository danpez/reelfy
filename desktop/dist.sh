#!/bin/bash
# Build the DISTRIBUTABLE Reelfy.app (self-contained) + DMG.
# Bundles: relocatable Python + deps, engine code, whisper-cli + dylibs,
# static ffmpeg/ffprobe (with libass), small models and music. The big
# whisper model downloads on first launch (server /setup).
set -euo pipefail
cd "$(dirname "$0")"
SPIKE="$(cd ../spike && pwd)"
DIST=dist
ENGINE="$DIST/engine"
APP="$DIST/Reelfy.app"
CACHE="$DIST/cache"
PYURL="https://github.com/astral-sh/python-build-standalone/releases/download/20250409/cpython-3.12.10+20250409-aarch64-apple-darwin-install_only.tar.gz"
FFURL="https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release"

mkdir -p "$CACHE"

echo "==> [1/8] engine: código + assets"
rm -rf "$ENGINE"
mkdir -p "$ENGINE"/{scripts,app,assets,models,bin,whisper.cpp/build/bin}
rsync -a --exclude __pycache__ "$SPIKE/scripts/" "$ENGINE/scripts/"
rsync -a --exclude __pycache__ "$SPIKE/app/" "$ENGINE/app/"
rsync -a "$SPIKE/assets/music" "$ENGINE/assets/"
cp "$SPIKE/models/face_detection_yunet_2023mar.onnx" "$SPIKE/models/rnnoise.rnnn" "$ENGINE/models/"

echo "==> [2/8] whisper-cli + dylibs (rpath portable)"
cp "$SPIKE"/whisper.cpp/build/bin/whisper-cli "$ENGINE/whisper.cpp/build/bin/"
cp "$SPIKE"/whisper.cpp/build/bin/lib*.dylib "$ENGINE/whisper.cpp/build/bin/"
install_name_tool -add_rpath @executable_path "$ENGINE/whisper.cpp/build/bin/whisper-cli" 2>/dev/null || true

echo "==> [3/8] ffmpeg/ffprobe estáticos (libass incluido)"
for b in ffmpeg ffprobe; do
  if [ ! -f "$CACHE/$b" ]; then
    curl -sL -o "$CACHE/$b.zip" "$FFURL/$b.zip"
    unzip -oq "$CACHE/$b.zip" -d "$CACHE"
    chmod +x "$CACHE/$b"
  fi
  cp "$CACHE/$b" "$ENGINE/bin/$b"
done

echo "==> [4/8] python standalone"
if [ ! -f "$CACHE/python.tar.gz" ]; then
  curl -sL -o "$CACHE/python.tar.gz" "$PYURL"
fi
tar -xzf "$CACHE/python.tar.gz" -C "$ENGINE"   # -> engine/python

echo "==> [5/8] dependencias python"
"$SPIKE/.venv/bin/pip" freeze | grep -viE "^(-e|pip=|setuptools=|wheel=)" > "$DIST/requirements.lock"
"$ENGINE/python/bin/python3" -m pip install -q --no-warn-script-location \
  -r "$DIST/requirements.lock"
# poda: caches y tests pesados
find "$ENGINE/python" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$ENGINE"/python/lib/python3.12/test \
       "$ENGINE"/python/lib/python3.12/site-packages/torch/test \
       "$ENGINE"/python/share 2>/dev/null || true

echo "==> [6/8] binario Swift + icono"
"$SPIKE/.venv/bin/python" gen_icon.py >/dev/null
mkdir -p "$DIST/build"
swiftc -O -o "$DIST/build/Reelfy" main.swift

echo "==> [7/8] bundle .app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$DIST/build/Reelfy" "$APP/Contents/MacOS/Reelfy"
cp Info.plist "$APP/Contents/Info.plist"
cp build/Reelfy.icns "$APP/Contents/Resources/Reelfy.icns"
mv "$ENGINE" "$APP/Contents/Resources/engine"

# Firma: Developer ID si REELFY_SIGN_ID está definido (p.ej. "Developer ID
# Application: Kevin Gonzalez (TEAMID)"); si no, ad-hoc (solo pruebas locales).
if [ -n "${REELFY_SIGN_ID:-}" ]; then
  echo "==> firma Developer ID + hardened runtime (tarda: miles de binarios)"
  ENT="$(pwd)/entitlements.plist"
  # 1) todo Mach-O anidado (dylibs, .so, ejecutables del motor)
  find "$APP/Contents/Resources/engine" -type f \( -name "*.dylib" -o -name "*.so" \) -print0 |
    xargs -0 -P 8 -n 20 codesign --force --timestamp --options runtime -s "$REELFY_SIGN_ID" 2>/dev/null
  for exe in "$APP/Contents/Resources/engine/bin/ffmpeg" \
             "$APP/Contents/Resources/engine/bin/ffprobe" \
             "$APP/Contents/Resources/engine/whisper.cpp/build/bin/whisper-cli" \
             "$APP"/Contents/Resources/engine/python/bin/python3.12; do
    codesign --force --timestamp --options runtime --entitlements "$ENT" -s "$REELFY_SIGN_ID" "$exe"
  done
  # 2) binario principal y la .app
  codesign --force --timestamp --options runtime --entitlements "$ENT" -s "$REELFY_SIGN_ID" \
    "$APP/Contents/MacOS/Reelfy"
  codesign --force --timestamp --options runtime --entitlements "$ENT" -s "$REELFY_SIGN_ID" "$APP"
  codesign --verify --deep --strict "$APP" && echo "firma verificada"
else
  codesign --force --deep -s - "$APP" 2>/dev/null
fi

echo "==> [8/8] DMG"
rm -f "$DIST/Reelfy.dmg"
DMGROOT="$DIST/dmgroot"
rm -rf "$DMGROOT"; mkdir -p "$DMGROOT"
cp -R "$APP" "$DMGROOT/"
ln -s /Applications "$DMGROOT/Applications"
hdiutil create -quiet -volname "Reelfy" -srcfolder "$DMGROOT" -ov -format UDZO "$DIST/Reelfy.dmg"
rm -rf "$DMGROOT"

du -sh "$APP" "$DIST/Reelfy.dmg"
echo "OK -> $DIST/Reelfy.dmg"
