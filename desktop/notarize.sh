#!/bin/bash
# Notariza Reelfy y deja el DMG listo para distribuir. Flujo bulletproof:
# notariza la app (registra su cdhash) -> estampa la app -> reempaca el DMG con
# la app ya estampada -> notariza el DMG -> estampa el DMG. Así tanto el DMG
# como la .app validan OFFLINE.
#
# Credenciales guardadas una vez (perfil de llavero 'mixiuh') con la API key de
# App Store Connect:
#   xcrun notarytool store-credentials mixiuh \
#     --key ~/Work/Mixiuh/keys/AuthKey_XXXX.p8 --key-id XXXX --issuer UUID
set -euo pipefail
cd "$(dirname "$0")"
APP=dist/Reelfy.app
DMG=dist/Reelfy.dmg
PROFILE=mixiuh
[ -d "$APP" ] || { echo "No existe $APP — corre dist.sh (firmado) primero"; exit 1; }

pack_dmg() {
  local root=dist/dmgroot
  rm -rf "$root"; mkdir -p "$root"
  cp -R "$APP" "$root/"; ln -s /Applications "$root/Applications"
  rm -f "$DMG"
  hdiutil create -quiet -volname "Reelfy" -srcfolder "$root" -ov -format UDZO "$DMG"
  rm -rf "$root"
}

echo "==> [1/5] notarizando la app (zip)…"
ZIP=dist/Reelfy.zip
ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait
rm -f "$ZIP"

echo "==> [2/5] estampando la app"
xcrun stapler staple "$APP"

echo "==> [3/5] reempacando el DMG con la app estampada"
pack_dmg

echo "==> [4/5] notarizando el DMG…"
xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait

echo "==> [5/5] estampando el DMG"
xcrun stapler staple "$DMG"

echo "==> verificación"
MP=$(hdiutil attach "$DMG" -nobrowse -readonly | grep Volumes | awk '{print $3}')
spctl -a -vvv "$MP/Reelfy.app" 2>&1 | sed 's/^/    /'
hdiutil detach "$MP" -quiet
echo "OK — $DMG listo para distribuir"
