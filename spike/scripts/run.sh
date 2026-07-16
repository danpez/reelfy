#!/usr/bin/env bash
# Reelfy spike runner. Usage: ./scripts/run.sh input/mi-video.mp4
set -euo pipefail
SPIKE="$(cd "$(dirname "$0")/.." && pwd)"
source "$SPIKE/.venv/bin/activate"
exec python "$SPIKE/scripts/pipeline.py" "$@"
