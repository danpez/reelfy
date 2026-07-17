#!/bin/bash
# Notariza y estampa el DMG. Requiere credenciales guardadas una vez con:
#   xcrun notarytool store-credentials reelfy \
#     --apple-id tacataca-tacu@hotmail.com --team-id TEAMID
#   (contraseña específica de app de account.apple.com)
# o con API key de App Store Connect:
#   xcrun notarytool store-credentials reelfy --key KEY.p8 --key-id XXX --issuer YYY
set -euo pipefail
cd "$(dirname "$0")"
DMG=dist/Reelfy.dmg
[ -f "$DMG" ] || { echo "No existe $DMG — corre dist.sh primero"; exit 1; }

echo "==> notarizando (espera a Apple)…"
xcrun notarytool submit "$DMG" --keychain-profile reelfy --wait

echo "==> estampando ticket"
xcrun stapler staple "$DMG"

echo "==> verificación Gatekeeper"
spctl -a -t open --context context:primary-signature -v "$DMG" || true
echo "OK — $DMG listo para distribuir"
