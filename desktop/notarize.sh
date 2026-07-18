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
  ./make_dmg.sh "$APP" "$DMG"   # DMG de marca (fondo + "arrastra a Aplicaciones")
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
# desmontar cualquier volumen Reelfy stale (evita verificar un montaje viejo).
# `|| true`: grep sin match devuelve 1 y con `set -e` abortaría el script.
(mount | grep -i "/Volumes/Reelfy" | awk '{print $1}' || true) | while read -r d; do
  hdiutil detach "$d" -force >/dev/null 2>&1 || true
done
MP=$(hdiutil attach "$DMG" -nobrowse -readonly | grep Volumes | awk '{print $3}')
spctl -a -vvv "$MP/Reelfy.app" 2>&1 | sed 's/^/    /'
hdiutil detach "$MP" -quiet
echo "OK — $DMG listo para distribuir"
