#!/bin/bash
# Ensambla el motor de Reelfy (Python + binarios) dentro de src-tauri/engine/
# para macOS (Apple Silicon). Tauri lo empaqueta como resource -> en la app
# queda en Contents/Resources/engine y la cáscara lo arranca (REELFY_HOME).
#
# Layout resultante (compatible con spike/scripts/paths.py):
#   engine/{scripts,app,assets/music,models}
#   engine/bin/{ffmpeg,ffprobe,ollama-runtime/…}
#   engine/whisper.cpp/build/bin/{whisper-cli,*.dylib}
#   engine/python/…  (python-standalone + deps del requirements.lock)
#
# El modelo grande de whisper NO se empaqueta (se descarga en el primer arranque).
set -euo pipefail
cd "$(dirname "$0")/.."                      # -> reelfy-desktop/
ROOT="$(cd .. && pwd)"                        # -> repo raíz (clipfy)
SPIKE="$ROOT/spike"
ENGINE="src-tauri/engine"
CACHE=".engine-cache"
PYURL="https://github.com/astral-sh/python-build-standalone/releases/download/20250409/cpython-3.12.10+20250409-aarch64-apple-darwin-install_only.tar.gz"
FFURL="https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release"

mkdir -p "$CACHE"

echo "==> [1/5] código + assets"
rm -rf "$ENGINE"
mkdir -p "$ENGINE"/{scripts,app,assets,models,bin,whisper.cpp/build/bin}
rsync -a --exclude __pycache__ "$SPIKE/scripts/" "$ENGINE/scripts/"
rsync -a --exclude __pycache__ "$SPIKE/app/" "$ENGINE/app/"
rsync -a "$SPIKE/assets/music" "$ENGINE/assets/"
cp "$SPIKE/models/face_detection_yunet_2023mar.onnx" \
   "$SPIKE/models/rnnoise.rnnn" "$ENGINE/models/"

echo "==> [2/5] whisper-cli + dylibs (rpath portable)"
cp "$SPIKE"/whisper.cpp/build/bin/whisper-cli "$ENGINE/whisper.cpp/build/bin/"
cp "$SPIKE"/whisper.cpp/build/bin/lib*.dylib "$ENGINE/whisper.cpp/build/bin/"
install_name_tool -add_rpath @executable_path "$ENGINE/whisper.cpp/build/bin/whisper-cli" 2>/dev/null || true

echo "==> [3/5] ffmpeg/ffprobe estáticos (con libass)"
for b in ffmpeg ffprobe; do
  if [ ! -f "$CACHE/$b" ]; then
    curl -sL -o "$CACHE/$b.zip" "$FFURL/$b.zip"
    unzip -oq "$CACHE/$b.zip" -d "$CACHE"
    chmod +x "$CACHE/$b"
  fi
  cp "$CACHE/$b" "$ENGINE/bin/$b"
done

echo "==> [4/5] ollama embebido (binario + runners)"
OLLAMA_LIBEXEC=$(dirname "$(readlink -f "$(command -v ollama)" 2>/dev/null)" 2>/dev/null || echo "")
if [ -z "$OLLAMA_LIBEXEC" ] || [ ! -f "$OLLAMA_LIBEXEC/ollama" ]; then
  OLLAMA_LIBEXEC=$(ls -d /opt/homebrew/Cellar/ollama/*/libexec 2>/dev/null | tail -1)
fi
if [ -n "$OLLAMA_LIBEXEC" ] && [ -f "$OLLAMA_LIBEXEC/ollama" ]; then
  mkdir -p "$ENGINE/bin/ollama-runtime"
  cp -R "$OLLAMA_LIBEXEC"/ "$ENGINE/bin/ollama-runtime/"
  rm -rf "$ENGINE/bin/ollama-runtime/lib/ollama/mlx_metal_v3"
  find "$ENGINE/bin/ollama-runtime" -type l ! -exec test -e {} \; -exec rm -f {} \;
else
  echo "    ⚠ ollama no encontrado — la IA de lenguaje no vendrá embebida"
fi

echo "==> [5/5] python-standalone + dependencias"
if [ ! -f "$CACHE/python.tar.gz" ]; then
  curl -sL -o "$CACHE/python.tar.gz" "$PYURL"
fi
tar -xzf "$CACHE/python.tar.gz" -C "$ENGINE"    # -> engine/python
"$ENGINE/python/bin/python3" -m pip install -q --no-warn-script-location -r "$SPIKE/requirements.txt"
find "$ENGINE/python" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$ENGINE"/python/lib/python3.12/test \
       "$ENGINE"/python/lib/python3.12/site-packages/torch/test \
       "$ENGINE"/python/share 2>/dev/null || true
# datos de prueba de librerías científicas: peso muerto y generan warnings de
# notarización (.gz/.npz que Apple no puede desempacar).
find "$ENGINE/python/lib/python3.12/site-packages" -type d \( -name tests -o -name test \) \
     -prune -exec rm -rf {} + 2>/dev/null || true

echo "OK -> $ENGINE ($(du -sh "$ENGINE" | cut -f1))"
