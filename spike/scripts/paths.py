"""Reelfy — resolución central de rutas y binarios.

Dos raíces:
  SPIKE (solo lectura)  — código, whisper.cpp, assets, modelos pequeños.
                          Env REELFY_HOME; default: el árbol del repo.
  DATA  (escribible)    — input/output/work, modelo whisper descargado, marca
                          del usuario. Env REELFY_DATA; default: SPIKE (dev).

Binarios: primero env, luego bundle (SPIKE/bin), luego Homebrew (dev).
"""
import os
from pathlib import Path

SPIKE = Path(os.environ.get("REELFY_HOME") or Path(__file__).resolve().parent.parent)
DATA = Path(os.environ.get("REELFY_DATA") or SPIKE)

INPUT = DATA / "input"
OUTPUT = DATA / "output"
WORK = DATA / "work"
BRAND = DATA / "assets/brand"
for _d in (INPUT, OUTPUT, WORK, BRAND):
    _d.mkdir(parents=True, exist_ok=True)

MUSIC_DIR = SPIKE / "assets/music"
YUNET = SPIKE / "models/face_detection_yunet_2023mar.onnx"
RNNOISE = SPIKE / "models/rnnoise.rnnn"
WCLI = SPIKE / "whisper.cpp/build/bin/whisper-cli"

# el modelo grande de whisper puede venir del repo (dev) o descargado (app)
_MODEL_REPO = SPIKE / "whisper.cpp/models/ggml-large-v3-turbo.bin"
_MODEL_DATA = DATA / "models/ggml-large-v3-turbo.bin"
MODEL_URL = ("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
             "ggml-large-v3-turbo.bin")
MODEL_SIZE = 1_624_555_275  # bytes, para % de descarga


def model_path():
    """Ruta efectiva del modelo whisper (repo en dev, descargado en app)."""
    return _MODEL_REPO if _MODEL_REPO.exists() else _MODEL_DATA


def engine_ready():
    return model_path().exists()


def _bin(name, brew_default):
    env = os.environ.get(f"REELFY_{name.upper()}")
    if env:
        return env
    bundled = SPIKE / "bin" / name
    return str(bundled) if bundled.exists() else brew_default


FFMPEG = _bin("ffmpeg", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFPROBE = _bin("ffprobe", "/opt/homebrew/bin/ffprobe")
