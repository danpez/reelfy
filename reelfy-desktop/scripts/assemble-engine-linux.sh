#!/bin/bash
# Ensambla el motor de Reelfy dentro de src-tauri/engine/ para Linux (x86_64).
# Requiere que whisper.cpp ya esté clonado+compilado en spike/whisper.cpp/build y
# acestep.cpp (musica IA, CPU/Vulkan con backends dinamicos) en spike/acestep.cpp/build
# (lo hace el workflow de CI). Los modelos grandes (whisper, LLM, afinador, sujeto y
# musica) se descargan en el primer arranque.
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

echo "==> [1/6] código + assets"
rm -rf "$ENGINE"
mkdir -p "$ENGINE"/{scripts,app,assets,models,bin,whisper.cpp/build/bin}
rsync -a --exclude __pycache__ "$SPIKE/scripts/" "$ENGINE/scripts/"
rsync -a --exclude __pycache__ "$SPIKE/app/" "$ENGINE/app/"
rsync -a "$SPIKE/assets/music" "$ENGINE/assets/"
# Motion Engine: fuentes (OFL), iconos (Tabler, MIT) y SFX propios.
rsync -a --exclude '*.wav' --exclude '_build.json' "$SPIKE/assets/motion" "$ENGINE/assets/"
cp "$SPIKE/models/face_detection_yunet_2023mar.onnx" \
   "$SPIKE/models/rnnoise.rnnn" "$ENGINE/models/"

echo "==> [2/6] whisper-cli + libs (rpath \$ORIGIN)"
cp "$SPIKE"/whisper.cpp/build/bin/whisper-cli "$ENGINE/whisper.cpp/build/bin/"
# libs compartidas del build (pueden estar en build/bin, build/src o build/ggml/src)
find "$SPIKE/whisper.cpp/build" -name "*.so*" -exec cp -a {} "$ENGINE/whisper.cpp/build/bin/" \; 2>/dev/null || true
if command -v patchelf >/dev/null; then
  patchelf --set-rpath '$ORIGIN' "$ENGINE/whisper.cpp/build/bin/whisper-cli" 2>/dev/null || true
fi

echo "==> [3/6] acestep.cpp (musica IA: ace-synth/ace-server + libggml y backends)"
ACE="$SPIKE/acestep.cpp/build"
SYNTH=$(find "$ACE" -type f -name ace-synth -perm -u+x 2>/dev/null | head -1)
if [ -n "$SYNTH" ]; then
  mkdir -p "$ENGINE/bin/acestep"
  cp "$SYNTH" "$(dirname "$SYNTH")/ace-server" "$ENGINE/bin/acestep/"
  # libggml*.so* y los backends cargables (cpu-<variante>, vulkan) junto al ejecutable
  find "$ACE" -name "*.so*" -exec cp -a {} "$ENGINE/bin/acestep/" \; 2>/dev/null || true
  if command -v patchelf >/dev/null; then
    for b in "$ENGINE"/bin/acestep/ace-synth "$ENGINE"/bin/acestep/ace-server "$ENGINE"/bin/acestep/*.so*; do
      [ -L "$b" ] || patchelf --set-rpath '$ORIGIN' "$b" 2>/dev/null || true
    done
  fi
  # (sin tuberia a grep -q: con pipefail el SIGPIPE daria un falso negativo)
  ACE_OUT=$("$ENGINE/bin/acestep/ace-synth" --help 2>&1 || true)
  case "$ACE_OUT" in
    *Usage*) echo "    ace-synth OK ($(ls "$ENGINE/bin/acestep" | wc -l | tr -d ' ') archivos)" ;;
    *) echo "    AVISO: ace-synth no arranca (revisa las .so)"; echo "$ACE_OUT" | head -3 ;;
  esac
else
  echo "    AVISO: sin spike/acestep.cpp/build — la música IA caerá a procedural"
fi

echo "==> [4/6] ffmpeg/ffprobe estáticos (con libass)"
if [ ! -f "$CACHE/ff/ffmpeg" ]; then
  curl -sL -o "$CACHE/ff.tar.xz" "$FFURL"
  mkdir -p "$CACHE/ff"
  tar -xJf "$CACHE/ff.tar.xz" -C "$CACHE/ff" --strip-components=2   # */bin/{ffmpeg,ffprobe}
fi
cp "$CACHE/ff/ffmpeg" "$CACHE/ff/ffprobe" "$ENGINE/bin/"
chmod +x "$ENGINE/bin/ffmpeg" "$ENGINE/bin/ffprobe"

echo "==> [5/6] ollama embebido (binario + runners; qwen3.5 requiere >= 0.30)"
# OJO: ollama publica el build de Linux como `ollama-linux-amd64.tar.zst`
# (antes .tgz). Se resuelve el asset del JSON de la release y se extrae con zstd.
# No-fatal: si falla, la app compila igual (solo sin LLM embebido), como en macOS.
if [ ! -f "$CACHE/ollama/bin/ollama" ]; then
  set +e
  OLLAMA_URL=$(curl -fsSL https://api.github.com/repos/ollama/ollama/releases/latest \
    | grep -oE '"browser_download_url": *"[^"]*ollama-linux-amd64\.tar\.zst"' \
    | head -1 | cut -d'"' -f4)
  if [ -n "$OLLAMA_URL" ]; then
    echo "    bajando $(basename "$OLLAMA_URL")…"
    curl -fSL -o "$CACHE/ollama.tar.zst" "$OLLAMA_URL"
    mkdir -p "$CACHE/ollama"
    tar --use-compress-program=unzstd -xf "$CACHE/ollama.tar.zst" -C "$CACHE/ollama"
  fi
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
  echo "    AVISO: ollama no se pudo obtener — la IA de lenguaje no vendrá embebida (build continúa)"
fi

echo "==> [6/6] python-standalone + dependencias"
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

# humo: las dependencias nativas del motor pro cargan en el python ensamblado
# (skia-python necesita libEGL/libGL/fontconfig: dependencias del .deb)
# FATAL: render.py importa skia al cargar; sin estas libs /render pro, /frame y /preview mueren.
if ! "$ENGINE/python/bin/python3" -c "import skia, onnxruntime, cv2, numpy, soundfile, scipy; print('    deps nativas OK: skia', skia.__version__, '| onnxruntime', onnxruntime.__version__, '| cv2', cv2.__version__)"; then
  echo "ERROR: deps nativas del motor pro no cargan en el python ensamblado (¿faltan libegl1/libgl1/libfontconfig1?)" >&2
  exit 1
fi

echo "OK -> $ENGINE ($(du -sh "$ENGINE" | cut -f1))"
du -sh "$ENGINE"/* 2>/dev/null | sed "s|^|   |"   # desglose por carpeta
