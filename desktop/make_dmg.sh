#!/bin/bash
# Construye un DMG de marca (fondo Reelfy, iconos posicionados, "arrastra a
# Aplicaciones") con dmgbuild — escribe el layout sin Finder, funciona desde
# consola. Uso: make_dmg.sh <Reelfy.app> <salida.dmg>
set -euo pipefail
cd "$(dirname "$0")"
APP="${1:-dist/Reelfy.app}"
OUT="${2:-dist/Reelfy.dmg}"
PY="../spike/.venv/bin/python"
[ -d "$APP" ] || { echo "No existe $APP"; exit 1; }

# fondo de marca (1x + @2x) unidos en un TIFF multi-resolución para retina
"$PY" dmg_background.py
tiffutil -cathidpicheck build/dmg-bg.png build/dmg-bg@2x.png -out build/dmg-bg.tiff >/dev/null

rm -f "$OUT"
"$PY" -m dmgbuild -s dmg_settings.py -D app="$APP" "Reelfy" "$OUT"
echo "DMG de marca -> $OUT"
