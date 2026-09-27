#!/bin/bash
# E2E real del motor pro por HTTP (docs/MOTION-ENGINE.md 6.1 y 7).
#   uso: tests/e2e_pro.sh [puerto] [carpeta_datos]
# Levanta un servidor AISLADO (tests/devserver.sh: DATA propio, sin licencia, modelos
# enlazados), corre tests/e2e_pro.py contra el y lo detiene por su PID al terminar.
# Salidas en spike/work/e2e/ (ignorado por git): plan, cuadros, vista previa, video final,
# shorts, hoja de contacto, e2e_result.json (tiempos por etapa y validaciones).
# Medios: REELFY_TEST_MEDIA o spike/work/media (6 IMG_*.MOV). Sin medios: sale con 77 (skip).
set -uo pipefail
SPIKE="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-${REELFY_E2E_PORT:-8013}}"
DATA="${2:-${REELFY_E2E_DATA:-$SPIKE/work/devdata_servidor}}"
OUT="${REELFY_E2E_OUT:-$SPIKE/work/e2e}"
MEDIA="${REELFY_TEST_MEDIA:-$SPIKE/work/media}"
n=$(ls "$MEDIA"/IMG_*.MOV 2>/dev/null | wc -l | tr -d ' ')
if [ "$n" -lt 6 ]; then
  echo "e2e_pro: sin los 6 IMG_*.MOV en $MEDIA (skip)"; exit 77
fi
if curl -s -o /dev/null "http://127.0.0.1:$PORT/tracks"; then
  echo "e2e_pro: el puerto $PORT ya esta en uso; usa otro"; exit 2
fi
mkdir -p "$OUT" "$DATA"
LOGF="$OUT/server.log"
bash "$SPIKE/tests/devserver.sh" "$PORT" "$DATA" > "$LOGF" 2>&1 &
PID=$!
stop() { kill "$PID" 2>/dev/null; sleep 1; kill -9 "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; }
trap stop EXIT
for i in $(seq 1 120); do
  curl -s -o /dev/null "http://127.0.0.1:$PORT/tracks" && break
  if ! kill -0 "$PID" 2>/dev/null; then echo "e2e_pro: el servidor no arranco"; tail -30 "$LOGF"; exit 1; fi
  sleep 0.5
done
echo "e2e_pro: servidor $PID en :$PORT (datos $DATA), carga $(uptime | sed 's/.*load/load/')"
cd "$SPIKE"
.venv/bin/python -u tests/e2e_pro.py --base "http://127.0.0.1:$PORT" --media "$MEDIA" --out "$OUT" "${@:3}"
RC=$?
echo "e2e_pro: fin rc=$RC, carga $(uptime | sed 's/.*load/load/')"
exit $RC
