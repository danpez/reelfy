#!/bin/bash
# Instalador de macOS (Apple Silicon) construido EN LOCAL, igual que el CI (reelfy-desktop.yml):
#   motor ensamblado -> firma profunda de todo Mach-O (Developer ID) -> Tauri (app firmada) ->
#   notarizar y estampar la app -> DMG de marca (dmgbuild) -> notarizar y estampar el DMG.
# Sin secretos en archivos ni en variables: la identidad vive en el llavero y la notarizacion usa
# un perfil guardado UNA vez con `xcrun notarytool store-credentials <perfil> --key ... `.
#
#   uso:  scripts/build-macos-local.sh            (desde reelfy-desktop/ o cualquier lado)
#   env:  SIGN_ID         identidad (default: la primera "Developer ID Application" del llavero)
#         NOTARY_PROFILE  perfil de notarytool (default: reelfy-notary)
#         SKIP_ASSEMBLE=1 reusar src-tauri/engine ya ensamblado
#         NOTARIZE=0      firmar sin notarizar (prueba rapida; Gatekeeper avisara al descargarlo)
#         UNSIGNED=1      ni firma ni notarizacion (solo para probar el empaquetado)
# Resultado: dist-local/Reelfy.dmg (+ .sha256) en la raiz del repo.
set -euo pipefail
cd "$(dirname "$0")/.."                         # reelfy-desktop
ROOT="$(cd .. && pwd)"
SPIKE="$ROOT/spike"
ENGINE="src-tauri/engine"
ENT="$PWD/src-tauri/entitlements.plist"
OUT="$ROOT/dist-local"
PROFILE="${NOTARY_PROFILE:-reelfy-notary}"
NOTARIZE="${NOTARIZE:-1}"
UNSIGNED="${UNSIGNED:-0}"
VERSION="$(python3 -c "import json;print(json.load(open('src-tauri/tauri.conf.json'))['version'])")"
mkdir -p "$OUT"
say() { printf '\n==> %s\n' "$*"; }

# ---------------------------------------------------------------- identidad y notarizacion
SIGN_ID="${SIGN_ID:-}"
if [ "$UNSIGNED" != "1" ]; then
  if [ -z "$SIGN_ID" ]; then
    SIGN_ID="$(security find-identity -v -p codesigning | grep -o '"Developer ID Application: [^"]*"' | head -1 | tr -d '"')"
  fi
  if [ -z "$SIGN_ID" ]; then
    echo "ERROR: no hay identidad 'Developer ID Application' en el llavero (ver docs: importar el certificado)." >&2
    exit 1
  fi
  echo "firma: $SIGN_ID"
  if [ "$NOTARIZE" = "1" ] && ! xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null 2>&1; then
    echo "ERROR: no existe el perfil de notarizacion '$PROFILE' (xcrun notarytool store-credentials)." >&2
    exit 1
  fi
else
  NOTARIZE=0
  echo "AVISO: build SIN firma (UNSIGNED=1)"
fi

# ---------------------------------------------------------------- binarios nativos (como el CI)
say "[1/8] whisper-cli y acestep.cpp"
if [ ! -x "$SPIKE/whisper.cpp/build/bin/whisper-cli" ]; then
  [ -d "$SPIKE/whisper.cpp" ] || git clone --depth 1 https://github.com/ggerganov/whisper.cpp "$SPIKE/whisper.cpp"
  cmake -S "$SPIKE/whisper.cpp" -B "$SPIKE/whisper.cpp/build" -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_EXAMPLES=ON
  cmake --build "$SPIKE/whisper.cpp/build" --config Release -j 8 --target whisper-cli
fi
ACE_SHA=b7ba6d9ce2f19ca7627fb8f340032c3ebe8ae253
if [ ! -x "$SPIKE/acestep.cpp/build/ace-synth" ] || [ "$(git -C "$SPIKE/acestep.cpp" rev-parse HEAD 2>/dev/null)" != "$ACE_SHA" ]; then
  [ -d "$SPIKE/acestep.cpp" ] || git clone --filter=blob:none https://github.com/ServeurpersoCom/acestep.cpp "$SPIKE/acestep.cpp"
  git -C "$SPIKE/acestep.cpp" checkout -q "$ACE_SHA"
  git -C "$SPIKE/acestep.cpp" submodule update --init --recursive
  rm -rf "$SPIKE/acestep.cpp/build"
  cmake -S "$SPIKE/acestep.cpp" -B "$SPIKE/acestep.cpp/build" -DCMAKE_BUILD_TYPE=Release \
    -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DGGML_BLAS=ON
  cmake --build "$SPIKE/acestep.cpp/build" --config Release -j 8 --target ace-synth ace-server
fi

# ---------------------------------------------------------------- motor
if [ "${SKIP_ASSEMBLE:-0}" != "1" ]; then
  say "[2/8] ensamblar el motor"
  bash scripts/assemble-engine-macos.sh
else
  say "[2/8] motor ya ensamblado (SKIP_ASSEMBLE=1)"
fi
# bytecode que un arranque de prueba haya dejado dentro: fuera (rompe la firma)
find "$ENGINE" -name __pycache__ -type d -prune -exec rm -rf {} +

# ---------------------------------------------------------------- firma profunda
if [ "$UNSIGNED" != "1" ]; then
  say "[3/8] firma profunda de los binarios del motor"
  LIST="$(mktemp)"
  while IFS= read -r f; do
    case "$(file -b "$f" 2>/dev/null)" in Mach-O*) printf '%s\n' "$f" >> "$LIST";; esac
  done < <(find "$ENGINE" -type f)
  echo "binarios Mach-O a firmar: $(wc -l < "$LIST" | tr -d ' ')"
  # hojas primero (rutas mas profundas antes)
  awk '{print gsub(/\//,"/")"\t"$0}' "$LIST" | sort -rn | cut -f2- | while IFS= read -r f; do
    codesign --force --timestamp --options runtime -s "$SIGN_ID" "$f" 2>/dev/null
  done
  for j in "$ENGINE"/python/bin/python3.12 \
           "$ENGINE"/bin/ollama-runtime/ollama \
           "$ENGINE"/bin/ollama-runtime/lib/ollama/llama-server; do
    [ -f "$j" ] && codesign --force --timestamp --options runtime --entitlements "$ENT" -s "$SIGN_ID" "$j"
  done
  rm -f "$LIST"
fi

# ---------------------------------------------------------------- cascara Tauri
say "[4/8] npm ci + pruebas de la cascara"
npm ci --no-audit --no-fund
rm -rf src-tauri/target/release/bundle
cargo test --locked --lib --manifest-path src-tauri/Cargo.toml

say "[5/8] Tauri build (app firmada; la notarizacion va aparte con el perfil del llavero)"
if [ "$UNSIGNED" = "1" ]; then
  NO_STRIP=true npm run tauri build -- --bundles dmg
else
  env -u APPLE_API_ISSUER -u APPLE_API_KEY -u APPLE_API_KEY_PATH -u APPLE_ID -u APPLE_PASSWORD \
    NO_STRIP=true APPLE_SIGNING_IDENTITY="$SIGN_ID" npm run tauri build -- --bundles dmg
fi

BUNDLE="src-tauri/target/release/bundle"
TDMG="$(ls "$BUNDLE"/dmg/*.dmg | head -1)"
APP="$BUNDLE/macos/Reelfy.app"
if [ ! -d "$APP" ]; then                       # tauri deja la .app en bundle/macos; si no, del dmg
  VOL="$(hdiutil attach "$TDMG" -nobrowse | grep -oE '/Volumes/.+' | head -1)"
  mkdir -p "$BUNDLE/macos"
  cp -R "$VOL/Reelfy.app" "$BUNDLE/macos/"
  hdiutil detach "$VOL" >/dev/null
fi

# ---------------------------------------------------------------- notarizar la app
if [ "$NOTARIZE" = "1" ]; then
  say "[6/8] notarizar y estampar la app"
  ZIP="$(mktemp -d)/Reelfy.zip"
  ditto -c -k --keepParent "$APP" "$ZIP"
  xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait | tee "$OUT/notary-app.log" || true
  grep -q "status: Accepted" "$OUT/notary-app.log" || { echo "ERROR: Apple no acepto la app (ver log de notarytool)" >&2; exit 1; }
  xcrun stapler staple "$APP"
  rm -f "$ZIP" "$OUT/notary-app.log"
else
  say "[6/8] sin notarizacion"
fi

# ---------------------------------------------------------------- DMG de marca
say "[7/8] DMG de marca (dmgbuild)"
DMGBUILD="$(command -v dmgbuild || true)"
if [ -z "$DMGBUILD" ]; then
  python3 -m venv "$ROOT/.dmgbuild-venv" >/dev/null
  "$ROOT/.dmgbuild-venv/bin/pip" install -q dmgbuild
  DMGBUILD="$ROOT/.dmgbuild-venv/bin/dmgbuild"
fi
mkdir -p "$OUT"
DMG="$OUT/Reelfy.dmg"
rm -f "$DMG"
# como el CI: desde src-tauri (dmg_settings.py resuelve el fondo relativo a esa carpeta)
(cd src-tauri && "$DMGBUILD" -s ../scripts/dmg_settings.py -D app="$(cd "../$(dirname "$APP")" && pwd)/Reelfy.app" Reelfy "$DMG")

if [ "$UNSIGNED" != "1" ]; then
  # el DMG tambien firmado: Gatekeeper lo acepta como "Notarized Developer ID" (sin firma el
  # ticket basta para abrirlo, pero spctl lo marca "no usable signature")
  codesign --force --timestamp -s "$SIGN_ID" "$DMG"
fi
if [ "$NOTARIZE" = "1" ]; then
  say "[8/8] notarizar y estampar el DMG"
  xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait | tee "$OUT/notary-dmg.log" || true
  grep -q "status: Accepted" "$OUT/notary-dmg.log" || { echo "ERROR: Apple no acepto el DMG" >&2; exit 1; }
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
  rm -f "$OUT/notary-dmg.log"
else
  say "[8/8] DMG sin notarizar"
fi

# ---------------------------------------------------------------- verificacion
say "verificacion"
if [ "$UNSIGNED" != "1" ]; then
  codesign --verify --deep --strict --verbose=1 "$APP"
  spctl -a -vv -t exec "$APP" 2>&1 || true
  spctl -a -vv -t open --context context:primary-signature "$DMG" 2>&1 || true
fi
shasum -a 256 "$DMG" | tee "$DMG.sha256"
ls -la "$DMG"
echo "LISTO: Reelfy $VERSION -> $DMG"
