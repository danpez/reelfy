#!/bin/bash
# Levanta un servidor de Reelfy AISLADO para pruebas (puerto y carpeta de datos propios).
#   uso: tests/devserver.sh <puerto> <carpeta_datos>
# - DATA aislado (jobs.db, input, output, work propios) para que varios servidores no se roben jobs.
# - REELFY_GATED=0: sin licencia en pruebas.
# - Enlaza los modelos pesados ya descargados (whisper, alineador, matte) en vez de copiarlos.
# - Para probar con una COPIA de otro proyecto: copia jobs.db y enlaza sus archivos UNO POR UNO
#   (ln -s por archivo) o copialos. Nunca enlaces una carpeta entera (input/, input/sources/...): la
#   limpieza del disco al arrancar borraria las fuentes "sin uso" del proyecto original (el servidor
#   ya no limpia dentro de carpetas enlazadas o fuera de su DATA: server._gc_dir_ok).
# Variables utiles: REELFY_OLLAMA_URL (default http://127.0.0.1:11498), REELFY_LLM_MODEL (qwen3.5:4b).
set -euo pipefail
PORT="${1:?puerto}"
DATA="${2:?carpeta de datos}"
SPIKE="$(cd "$(dirname "$0")/.." && pwd)"
APPSUP="$HOME/Library/Application Support"
mkdir -p "$DATA/models/matte"
link() { [ -e "$1" ] && [ ! -e "$2" ] && ln -s "$1" "$2" || true; }
for src in "$APPSUP/online.mixiuh.reelfy/models/ggml-large-v3-turbo.bin" "$APPSUP/Reelfy/models/ggml-large-v3-turbo.bin"; do
  link "$src" "$DATA/models/ggml-large-v3-turbo.bin"
done
link "$SPIKE/models/mms_fa_int8.onnx" "$DATA/models/mms_fa_int8.onnx"
link "$SPIKE/models/modnet.onnx" "$DATA/models/modnet.onnx"
for f in "$SPIKE"/models/matte/*.onnx; do link "$f" "$DATA/models/matte/$(basename "$f")"; done
link "$APPSUP/Reelfy/music-models" "$DATA/music-models"
export REELFY_PORT="$PORT" REELFY_DATA="$DATA" REELFY_GATED=0
export REELFY_OLLAMA_URL="${REELFY_OLLAMA_URL:-http://127.0.0.1:11498}"
export REELFY_LLM_MODEL="${REELFY_LLM_MODEL:-qwen3.5:4b}"
export REELFY_MUSIC_MODELS="${REELFY_MUSIC_MODELS:-$APPSUP/Reelfy/music-models}"
export REELFY_ACE_BIN="${REELFY_ACE_BIN:-$SPIKE/acestep.cpp/build/ace-synth}"
cd "$SPIKE"
exec .venv/bin/python -u app/server.py
