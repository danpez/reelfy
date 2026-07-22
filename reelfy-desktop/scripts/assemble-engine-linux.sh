#!/bin/bash
# Ensambla el motor de Reelfy dentro de src-tauri/engine/ para Linux (x86_64).
# Requiere que whisper.cpp ya esté clonado+compilado en spike/whisper.cpp/build
# (lo hace el workflow de CI). El modelo grande de whisper se descarga en runtime.
set -euo pipefail
cd "$(dirname "$0")/.."                        # -> reelfy-desktop/
ROOT="$(cd .. && pwd)"
SPIKE="$ROOT/spike"
ENGINE="src-tauri/engine"
CACHE=".engine-cache"
PYURL="https://github.com/astral-sh/python-build-standalone/releases/download/20250409/cpython-3.12.10+20250409-x86_64-unknown-linux-gnu-install_only.tar.gz"
FFURL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
OLLAMAURL="https://ollama.com/download/ollama-linux-amd64.tgz"

mkdir -p "$CACHE"

echo "==> [1/5] código + assets"
rm -rf "$ENGINE"
mkdir -p "$ENGINE"/{scripts,app,assets,models,bin,whisper.cpp/build/bin}
rsync -a --exclude __pycache__ "$SPIKE/scripts/" "$ENGINE/scripts/"
rsync -a --exclude __pycache__ "$SPIKE/app/" "$ENGINE/app/"
rsync -a "$SPIKE/assets/music" "$ENGINE/assets/"
cp "$SPIKE/models/face_detection_yunet_2023mar.onnx" \
   "$SPIKE/models/rnnoise.rnnn" "$ENGINE/models/"

echo "==> [2/5] whisper-cli + libs (rpath \$ORIGIN)"
cp "$SPIKE"/whisper.cpp/build/bin/whisper-cli "$ENGINE/whisper.cpp/build/bin/"
# libs compartidas del build (pueden estar en build/bin, build/src o build/ggml/src)
find "$SPIKE/whisper.cpp/build" -name "*.so*" -exec cp -a {} "$ENGINE/whisper.cpp/build/bin/" \; 2>/dev/null || true
if command -v patchelf >/dev/null; then
  patchelf --set-rpath '$ORIGIN' "$ENGINE/whisper.cpp/build/bin/whisper-cli" 2>/dev/null || true
fi

echo "==> [3/5] ffmpeg/ffprobe estáticos (con libass)"
if [ ! -f "$CACHE/ff/ffmpeg" ]; then
  curl -sL -o "$CACHE/ff.tar.xz" "$FFURL"
  mkdir -p "$CACHE/ff"
  tar -xJf "$CACHE/ff.tar.xz" -C "$CACHE/ff" --strip-components=2   # */bin/{ffmpeg,ffprobe}
fi
cp "$CACHE/ff/ffmpeg" "$CACHE/ff/ffprobe" "$ENGINE/bin/"
chmod +x "$ENGINE/bin/ffmpeg" "$ENGINE/bin/ffprobe"

echo "==> [4/5] ollama embebido (binario + runners)"
# Resolver la última release de ollama y bajar el tgz de Linux. No-fatal: si
# falla, la app compila igual (solo sin LLM embebido), como en macOS.
if [ ! -f "$CACHE/ollama/bin/ollama" ]; then
  set +e
  # Tomar la URL del asset directamente del JSON de la release (robusto al nombre)
  OLLAMA_URL=$(curl -fsSL https://api.github.com/repos/ollama/ollama/releases/latest \
    | grep -oE '"browser_download_url": *"[^"]*ollama-linux-amd64\.tgz"' | head -1 | cut -d'"' -f4)
  [ -n "$OLLAMA_URL" ] && curl -fSL -o "$CACHE/ollama.tgz" "$OLLAMA_URL"
  mkdir -p "$CACHE/ollama"
  tar -xzf "$CACHE/ollama.tgz" -C "$CACHE/ollama" 2>/dev/null
  set -e
fi
if [ -f "$CACHE/ollama/bin/ollama" ]; then
  mkdir -p "$ENGINE/bin/ollama-runtime"
  cp "$CACHE/ollama/bin/ollama" "$ENGINE/bin/ollama-runtime/ollama"
  chmod +x "$ENGINE/bin/ollama-runtime/ollama"
  [ -d "$CACHE/ollama/lib/ollama" ] && cp -a "$CACHE/ollama/lib" "$ENGINE/bin/ollama-runtime/"
  # Podar runners/libs de GPU (CUDA/ROCm): cientos de MB y usamos CPU (llama-server).
  find "$ENGINE/bin/ollama-runtime" \
       \( -iname "*cuda*" -o -iname "*rocm*" -o -iname "*cublas*" -o -iname "*cudnn*" \
          -o -iname "*rocblas*" -o -iname "*hipblas*" -o -iname "*amdhip*" \) \
       -exec rm -rf {} + 2>/dev/null || true
  echo "    ollama embebido ($(du -sh "$ENGINE/bin/ollama-runtime" | cut -f1), runners GPU podados)"
else
  echo "    ⚠ ollama no se pudo obtener — la IA de lenguaje no vendrá embebida (build continúa)"
fi

echo "==> [5/5] python-standalone + dependencias"
if [ ! -f "$CACHE/python.tar.gz" ]; then
  curl -sL -o "$CACHE/python.tar.gz" "$PYURL"
fi
tar -xzf "$CACHE/python.tar.gz" -C "$ENGINE"    # -> engine/python
"$ENGINE/python/bin/python3" -m pip install -q --no-warn-script-location \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  -r "$SPIKE/requirements.txt"
find "$ENGINE/python" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$ENGINE"/python/lib/python3.12/test \
       "$ENGINE"/python/lib/python3.12/site-packages/torch/test \
       "$ENGINE"/python/share 2>/dev/null || true
# datos de prueba de librerías científicas: peso muerto.
find "$ENGINE/python/lib/python3.12/site-packages" -type d \( -name tests -o -name test \) \
     -prune -exec rm -rf {} + 2>/dev/null || true

echo "OK -> $ENGINE ($(du -sh "$ENGINE" | cut -f1))"
