"""Reelfy — resolución central de rutas y binarios.

Dos raíces:
  SPIKE (solo lectura)  — código, whisper.cpp, assets, modelos pequeños.
                          Env REELFY_HOME; default: el árbol del repo.
  DATA  (escribible)    — input/output/work, modelo whisper descargado, marca
                          del usuario. Env REELFY_DATA; default: SPIKE (dev).

Binarios (multiplataforma): primero env, luego bundle (SPIKE/bin, con .exe en
Windows), luego el PATH del sistema (shutil.which), y por último el default de
dev (Homebrew en Mac). Así el mismo motor corre en Win/Mac/Linux.
"""
import os
import shutil
import sys
from pathlib import Path

# --- plataforma ---
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
EXE = ".exe" if IS_WINDOWS else ""

SPIKE = Path(os.environ.get("REELFY_HOME") or Path(__file__).resolve().parent.parent)
DATA = Path(os.environ.get("REELFY_DATA") or SPIKE)

INPUT = DATA / "input"
OUTPUT = DATA / "output"
WORK = DATA / "work"
BRAND = DATA / "assets/brand"
LOGS = DATA / "logs"
OLLAMA_MODELS = DATA / "ollama"          # modelos del LLM embebido (fuera del ~/.ollama del sistema)
for _d in (INPUT, OUTPUT, WORK, BRAND, LOGS, OLLAMA_MODELS):
    _d.mkdir(parents=True, exist_ok=True)

MUSIC_DIR = SPIKE / "assets/music"
YUNET = SPIKE / "models/face_detection_yunet_2023mar.onnx"
RNNOISE = SPIKE / "models/rnnoise.rnnn"


def _first_existing(*candidates):
    """Primera ruta que exista, o None."""
    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    return None


# whisper-cli: en dev el build de CMake lo deja en build/bin (Unix) o
# build/bin/Release (Windows, generador multi-config). En la app viene en SPIKE/bin.
WCLI = (
    os.environ.get("REELFY_WHISPER")
    or _first_existing(
        SPIKE / "bin" / f"whisper-cli{EXE}",
        SPIKE / f"whisper.cpp/build/bin/whisper-cli{EXE}",
        SPIKE / f"whisper.cpp/build/bin/Release/whisper-cli{EXE}",
    )
    or shutil.which("whisper-cli")
    or str(SPIKE / f"whisper.cpp/build/bin/whisper-cli{EXE}")
)

# el modelo grande de whisper puede venir del repo (dev) o descargado (app)
_MODEL_REPO = SPIKE / "whisper.cpp/models/ggml-large-v3-turbo.bin"
_MODEL_DATA = DATA / "models/ggml-large-v3-turbo.bin"
MODEL_URL = ("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
             "ggml-large-v3-turbo.bin")
MODEL_SIZE = 1_624_555_275  # bytes, para % de descarga


# Alineador de captions (wav2vec2 CTC MMS_FA exportado a ONNX int8). Corre con
# onnxruntime — sin torch. Mismo patrón que whisper: en el repo durante dev,
# descargado a DATA en la app.
_ALIGNER_REPO = SPIKE / "models/mms_fa_int8.onnx"
_ALIGNER_DATA = DATA / "models/mms_fa_int8.onnx"
# Se aloja en el repo PÚBLICO de releases (clipfy es privado y sus assets
# requerirían autenticación para descargarse).
ALIGNER_URL = os.environ.get(
    "REELFY_ALIGNER_URL",
    "https://github.com/danpez/reelfy-releases/releases/download/models-v1/mms_fa_int8.onnx")
ALIGNER_SIZE = 357_386_092  # bytes, para verificar que la descarga quedó completa


def model_path():
    """Ruta efectiva del modelo whisper (repo en dev, descargado en app)."""
    return _MODEL_REPO if _MODEL_REPO.exists() else _MODEL_DATA


def aligner_model_path():
    """Ruta efectiva del modelo del alineador (repo en dev, descargado en app)."""
    return _ALIGNER_REPO if _ALIGNER_REPO.exists() else _ALIGNER_DATA


def aligner_ready():
    """El modelo del alineador existe Y está completo (tamaño coincide)."""
    p = aligner_model_path()
    return p.exists() and p.stat().st_size >= ALIGNER_SIZE - 1_000_000


def engine_ready():
    """El modelo existe Y está COMPLETO (el tamaño coincide). Un modelo truncado
    por una descarga interrumpida se detecta aquí y se vuelve a descargar."""
    p = model_path()
    return p.exists() and p.stat().st_size >= MODEL_SIZE - 1_000_000


def _bin(name, mac_dev_default):
    """Resuelve un binario: env -> bundle (SPIKE/bin, con .exe) -> default de dev
    conocido-bueno en Mac (p.ej. ffmpeg-full, que trae libass) -> PATH del
    sistema -> nombre a secas. El bundle y el default-Mac ganan sobre el PATH
    porque el ffmpeg genérico del sistema suele NO traer libass (subtítulos)."""
    env = os.environ.get(f"REELFY_{name.upper()}")
    if env:
        return env
    bundled = SPIKE / "bin" / f"{name}{EXE}"
    if bundled.exists():
        return str(bundled)
    if IS_MAC and Path(mac_dev_default).exists():
        return mac_dev_default
    found = shutil.which(name)
    if found:
        return found
    return mac_dev_default if IS_MAC else name


FFMPEG = _bin("ffmpeg", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFPROBE = _bin("ffprobe", "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")

# ollama embebido (binario + lib/ollama/runners). En la app viene en
# SPIKE/bin/ollama-runtime/ollama[.exe]; en dev cae al del sistema.
OLLAMA_BIN = (
    os.environ.get("REELFY_OLLAMA_BIN")
    or _first_existing(SPIKE / "bin" / "ollama-runtime" / f"ollama{EXE}")
    or shutil.which("ollama")
    or ("/opt/homebrew/bin/ollama" if IS_MAC else f"ollama{EXE}")
)
LLM_MODEL = os.environ.get("REELFY_LLM_MODEL", "qwen2.5:3b")
