#!/usr/bin/env bash
# Reelfy local app. Opens http://127.0.0.1:8000
set -euo pipefail
SPIKE="$(cd "$(dirname "$0")/.." && pwd)"
source "$SPIKE/.venv/bin/activate"
echo "Reelfy → http://127.0.0.1:8000"
exec python "$SPIKE/app/server.py"
