#!/bin/bash
# Ensambla el motor de Reelfy (Python + binarios) dentro de src-tauri/engine/
# para macOS (Apple Silicon). Tauri lo empaqueta como resource -> en la app
# queda en Contents/Resources/engine y la cáscara lo arranca (REELFY_HOME).
#
# Layout resultante (compatible con spike/scripts/paths.py y motion/music.py):
#   engine/{scripts,app,assets/{music,motion},models}
#   engine/bin/{ffmpeg,ffprobe,ollama-runtime/…}
#   engine/bin/acestep/{ace-synth,ace-server,libggml*.0.dylib}   (musica IA, Metal)
#   engine/whisper.cpp/build/bin/{whisper-cli,*.dylib}
#   engine/python/…  (python-standalone + deps del requirements.txt, con skia-python)
#
# Los modelos grandes NO se empaquetan: whisper, LLM (qwen3.5:4b), afinador, sujeto
# (MODNet/PP-HumanSeg) y musica (ACE-Step, opcional) se descargan en el primer arranque.
# Requiere spike/acestep.cpp/build compilado (Metal); si falta, la app usa musica procedural.
set -euo pipefail
cd "$(dirname "$0")/.."                      # -> reelfy-desktop/
ROOT="$(cd .. && pwd)"                        # -> repo raíz (clipfy)
SPIKE="$ROOT/spike"
ENGINE="src-tauri/engine"
CACHE=".engine-cache"
PYURL="https://github.com/astral-sh/python-build-standalone/releases/download/20250409/cpython-3.12.10+20250409-aarch64-apple-darwin-install_only.tar.gz"
FFURL="https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release"

mkdir -p "$CACHE"

echo "==> [1/6] código + assets"
rm -rf "$ENGINE"
mkdir -p "$ENGINE"/{scripts,app,assets,models,bin,whisper.cpp/build/bin}
rsync -a --exclude __pycache__ "$SPIKE/scripts/" "$ENGINE/scripts/"
rsync -a --exclude __pycache__ "$SPIKE/app/" "$ENGINE/app/"
rsync -a "$SPIKE/assets/music" "$ENGINE/assets/"
# Motion Engine: fuentes (OFL), iconos (Tabler, MIT) y SFX propios. Sin wav pesados de trabajo.
rsync -a --exclude '*.wav' --exclude '_build.json' "$SPIKE/assets/motion" "$ENGINE/assets/"
cp "$SPIKE/models/face_detection_yunet_2023mar.onnx" \
   "$SPIKE/models/rnnoise.rnnn" "$ENGINE/models/"

echo "==> [2/6] whisper-cli + dylibs (rpath portable)"
cp "$SPIKE"/whisper.cpp/build/bin/whisper-cli "$ENGINE/whisper.cpp/build/bin/"
cp "$SPIKE"/whisper.cpp/build/bin/lib*.dylib "$ENGINE/whisper.cpp/build/bin/"
install_name_tool -add_rpath @executable_path "$ENGINE/whisper.cpp/build/bin/whisper-cli" 2>/dev/null || true
codesign --force -s - "$ENGINE/whisper.cpp/build/bin/whisper-cli" 2>/dev/null || true

echo "==> [3/6] acestep.cpp (musica IA: ace-synth/ace-server + libggml, Metal)"
ACE="$SPIKE/acestep.cpp/build"
if [ -x "$ACE/ace-synth" ]; then
  mkdir -p "$ENGINE/bin/acestep"
  cp "$ACE/ace-synth" "$ACE/ace-server" "$ENGINE/bin/acestep/"
  # los binarios enlazan @rpath/libggml*.0.dylib: se copian con ese nombre (sin symlinks;
  # carpeta propia porque whisper trae OTRA version de libggml con los mismos nombres)
  for l in "$ACE"/libggml*.0.dylib; do cp -L "$l" "$ENGINE/bin/acestep/$(basename "$l")"; done
  for b in "$ENGINE"/bin/acestep/*; do
    for rp in $(otool -l "$b" | awk '/LC_RPATH/{f=1} f&&/ path /{print $2; f=0}'); do
      [ "$rp" != "@executable_path" ] && [ "$rp" != "@loader_path" ] && \
        install_name_tool -delete_rpath "$rp" "$b" 2>/dev/null || true
    done
  done
  for b in ace-synth ace-server; do
    install_name_tool -add_rpath @executable_path "$ENGINE/bin/acestep/$b" 2>/dev/null || true
  done
  for b in "$ENGINE"/bin/acestep/*; do codesign --force -s - "$b" 2>/dev/null || true; done
  # (sin tuberia a grep -q: con pipefail el SIGPIPE daria un falso negativo)
  ACE_OUT=$("$ENGINE/bin/acestep/ace-synth" --help 2>&1 || true)
  case "$ACE_OUT" in
    *Usage*) echo "    ace-synth OK ($(ls "$ENGINE/bin/acestep" | wc -l | tr -d ' ') archivos)" ;;
    *) echo "    AVISO: ace-synth no arranca (revisa dylibs)"; echo "$ACE_OUT" | head -3 ;;
  esac
else
  echo "    AVISO: sin spike/acestep.cpp/build/ace-synth — la música IA caerá a procedural"
fi

echo "==> [4/6] ffmpeg/ffprobe estáticos (con libass)"
for b in ffmpeg ffprobe; do
  if [ ! -f "$CACHE/$b" ]; then
    curl -sL -o "$CACHE/$b.zip" "$FFURL/$b.zip"
    unzip -oq "$CACHE/$b.zip" -d "$CACHE"
    chmod +x "$CACHE/$b"
  fi
  cp "$CACHE/$b" "$ENGINE/bin/$b"
done

echo "==> [5/6] ollama embebido (binario + runners; qwen3.5 requiere >= 0.30)"
OLLAMA_LIBEXEC=$(dirname "$(readlink -f "$(command -v ollama)" 2>/dev/null)" 2>/dev/null || echo "")
if [ -z "$OLLAMA_LIBEXEC" ] || [ ! -f "$OLLAMA_LIBEXEC/ollama" ]; then
  OLLAMA_LIBEXEC=$(ls -d /opt/homebrew/Cellar/ollama/*/libexec 2>/dev/null | tail -1)
fi
if [ -n "$OLLAMA_LIBEXEC" ] && [ -f "$OLLAMA_LIBEXEC/ollama" ]; then
  mkdir -p "$ENGINE/bin/ollama-runtime"
  cp -R "$OLLAMA_LIBEXEC"/ "$ENGINE/bin/ollama-runtime/"
  rm -rf "$ENGINE/bin/ollama-runtime/lib/ollama/mlx_metal_v3"
  find "$ENGINE/bin/ollama-runtime" -type l ! -exec test -e {} \; -exec rm -f {} \;
  OV=$("$ENGINE/bin/ollama-runtime/ollama" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | tail -1 || true)
  echo "    ollama ${OV:-?}"
  case "$OV" in
    0.[0-9].*|0.[12][0-9].*) echo "    AVISO: ollama ${OV} es anterior a 0.30: no sirve qwen3.5:4b (actualiza: brew upgrade ollama)" ;;
  esac
else
  echo "    AVISO: ollama no encontrado — la IA de lenguaje no vendrá embebida"
fi

echo "==> [6/6] python-standalone + dependencias"
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

# humo FATAL: las dependencias nativas del motor pro cargan en el python ensamblado
# (render.py importa skia al cargar: sin ellas /render pro, /frame y /preview mueren)
if ! "$ENGINE/python/bin/python3" -c "import skia, onnxruntime, cv2, numpy, soundfile, scipy; print('    deps nativas OK: skia', skia.__version__, '| onnxruntime', onnxruntime.__version__, '| cv2', cv2.__version__)"; then
  echo "ERROR: deps nativas del motor pro no cargan en el python ensamblado" >&2
  exit 1
fi

echo "OK -> $ENGINE ($(du -sh "$ENGINE" | cut -f1))"
du -sh "$ENGINE"/* 2>/dev/null | sed "s|^|   |"   # desglose por carpeta
