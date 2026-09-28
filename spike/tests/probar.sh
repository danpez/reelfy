#!/bin/bash
# Probar Reelfy como USUARIO (motor de desarrollo) en el navegador, con una carpeta de datos propia.
#   uso: tests/probar.sh [puerto=8040] [carpeta_datos=work/devdata_personal]
# - Arranca la IA local (ollama en :11498 con los modelos de Reelfy) si no esta corriendo.
# - Levanta el servidor con tests/devserver.sh (sin licencia, modelos enlazados) y abre el navegador.
# - Ctrl+C lo cierra. Los proyectos y exports quedan en la carpeta de datos (se conservan).
set -euo pipefail
SPIKE="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-8040}"
DATA="${2:-$SPIKE/work/devdata_personal}"
APPSUP="$HOME/Library/Application Support/Reelfy"
OLL=11498
up() { curl -s -m 1 "$1" >/dev/null 2>&1; }

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "El puerto $PORT ya esta ocupado: abre http://127.0.0.1:$PORT o usa otro puerto (tests/probar.sh 8041)."
  exit 1
fi
mkdir -p "$DATA"
if ! up "http://127.0.0.1:$OLL/api/version"; then
  OLLAMA="$(command -v ollama || true)"
  if [ -z "$OLLAMA" ]; then
    echo "Falta ollama (brew install ollama). Sin el, Reelfy corta y subtitula pero no dirige con IA."
  else
    echo "Arrancando la IA local (ollama :$OLL)..."
    OLLAMA_HOST="127.0.0.1:$OLL" OLLAMA_MODELS="$APPSUP/ollama" nohup "$OLLAMA" serve \
      >"$DATA/ollama.log" 2>&1 &
    for _ in $(seq 1 30); do up "http://127.0.0.1:$OLL/api/version" && break; sleep 1; done
  fi
fi
# abre el navegador en cuanto el servidor responda
( for _ in $(seq 1 180); do
    if up "http://127.0.0.1:$PORT/"; then [ -n "${REELFY_NO_OPEN:-}" ] || open "http://127.0.0.1:$PORT"; exit 0; fi
    sleep 1
  done ) &
echo "Reelfy: http://127.0.0.1:$PORT   datos: $DATA   (Ctrl+C para cerrar)"
exec "$SPIKE/tests/devserver.sh" "$PORT" "$DATA"
