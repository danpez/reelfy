"""Musica del Motion Engine: IA local + generador procedural + biblioteca, AJUSTADA al video.

Contrato (docs/MOTION-ENGINE.md, 3.1 music y 4.2b music):

    get_music(cfg, duration, style, seed, on_pct) -> {wav, bpm, beats, downbeats, sections, drop, ...}

Fuentes para `cfg.source == "ai"` (en orden; si una falla se usa la siguiente):
  1. ACE-Step 1.5 turbo (MIT) via acestep.cpp en modo DiT-only (sin LM): caption + bpm +
     tonalidad -> WAV 48 kHz. CLI `ace-synth`, o `ace-server` si REELFY_ACE_URL apunta a uno vivo
     (mantiene el modelo cargado: ahorra ~9 s por generacion).
  2. Generador procedural propio (numpy/scipy): pop, edm, trap, piano, lofi, ambient, cinematico
     (el genero sale del prompt si lo nombra); hook con energia, drop exacto, final natural/hit/fade.
  3. Biblioteca `spike/assets/music` (pistas propias).
`source` "library" usa la biblioteca (`track`), "procedural" el generador y "file" un archivo del
usuario (`file`). Todas pasan por el mismo ajuste.

Analisis (`analyze`): tempo sin errores de octava (candidatos 1/2..2 + contratiempo), beats con DP
+ correccion de contratiempo + re-fase de tramos sin bateria a la rejilla de la bateria, confianza
de pulso (sin pulso -> sin beats: nada de rejilla inventada en pads/drones), downbeats por bombo,
backbeat, cambio armonico y remate, drop = entrada audible (beat donde la energia salta y se
sostiene).

Ajuste al video (`fit`):
  - recorta silencios de cabeza/cola (ACE suele terminar la cancion ~13% antes y deja silencio),
  - arranque con energia (hook): ACE hace intros de 10-17 s casi mudas; el video no arranca ahi,
  - coloca el drop donde se pide (por defecto en el primer segundo en dinamico/energetico, o al
    volver del primer stop dramatico del director) y reporta donde QUEDO,
  - costuras ocultas: dentro de un stop (audio.py silencia ahi) o en el golpe del drop,
  - final: "natural" (el remate real), "hit" (downbeat + cola de reverb) o "fade" (musical),
  - si la pista es mas corta, loops de 16/8/4/2/1 compases con costuras en downbeat,
  - normaliza a TARGET_LUFS (compresion suave si hace falta) con techo PEAK_DB (true peak).
La salida es un WAV 48 kHz estereo de duracion EXACTA (muestras = round(duration * 48000)).

Cache en DATA/work/music (o `cache_dir`): el crudo por (prompt, bpm, tonalidad, seed) y se reusa
para cualquier duracion con material suficiente (recortar el video no regenera); el ajuste por
hash de crudo+duracion+parametros. `prefetch` adelanta la generacion en un hilo.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import numpy as np
import soundfile as sf
from scipy import ndimage, signal

try:  # dentro del motor (scripts/ en sys.path)
    import paths as _paths
    SPIKE = Path(_paths.SPIKE)
    DATA = Path(_paths.DATA)
    FFMPEG = _paths.FFMPEG
    LIB_MUSIC_DIR = Path(_paths.MUSIC_DIR)
except Exception:  # pragma: no cover - uso aislado
    SPIKE = Path(__file__).resolve().parents[2]
    DATA = Path(os.environ.get("REELFY_DATA") or SPIKE)
    FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
    LIB_MUSIC_DIR = SPIKE / "assets" / "music"

SR = 48000
VERSION = "m6.2"          # sube si cambia el algoritmo de ajuste/sintesis (invalida caches)
RAW_VERSION = "r1"        # sube si cambia la forma de generar el crudo con ACE (regenera audio)
ANALYSIS_VERSION = "a7"   # sube si cambia `analyze` (re-analiza el crudo cacheado, sin regenerar)
TARGET_LUFS = -14.0       # nivel de la pista entregada; audio.mix aplica gain_db sobre esto
PEAK_DB = -1.0            # techo (true peak aproximado)
MAX_LIMIT_DB = 4.0        # como maximo 4 dB de limitacion para llegar al objetivo
PREROLL = 0.012           # s antes del beat de entrada (no cortar el ataque)

# ---------------------------------------------------------------- modelos ACE-Step
HF_REPO = "https://huggingface.co/Serveurperso/ACE-Step-1.5-GGUF/resolve/main/"
MUSIC_MODEL_FILES: list[dict] = [
    {"role": "dit", "file": "acestep-v15-turbo-Q8_0.gguf", "size": 2549528000,
     "sha256": "288f708a61cfc241013a98a62f98ba331f83fe34d0d3559acdd9b0f6a2f7cd6b"},
    {"role": "text_encoder", "file": "Qwen3-Embedding-0.6B-Q8_0.gguf", "size": 784144960,
     "sha256": "972f23255e46adfe744a0eb9a0039f3c63988f65753b0968d776e8b27168c321"},
    {"role": "vae", "file": "vae-BF16.gguf", "size": 337420928,
     "sha256": "0599862ac5d15cd308e1d2e368373aea6c02e25ebd1737ad4a4562a0901b0ef8"},
]
for _m in MUSIC_MODEL_FILES:
    _m["url"] = HF_REPO + _m["file"]
MUSIC_MODELS_BYTES = sum(m["size"] for m in MUSIC_MODEL_FILES)
DIT_MODEL = MUSIC_MODEL_FILES[0]["file"]
LICENSE = "ACE-Step 1.5 (MIT), acestep.cpp (MIT); generador procedural propio"


def _exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def ace_bin(tool: str = "ace-synth") -> str | None:
    """Ruta del binario de acestep.cpp (`ace-synth` o `ace-server`) o None si no esta.

    Orden: env REELFY_ACE_BIN (ruta al ace-synth o a su carpeta; los demas binarios se buscan al
    lado) -> bundle SPIKE/bin[/acestep] -> build de dev SPIKE/acestep.cpp/build -> PATH.
    """
    env = os.environ.get("REELFY_ACE_BIN")
    if env:
        p = Path(env)
        if p.is_dir():
            p = p / _exe(tool)
        elif tool != "ace-synth":
            p = p.with_name(_exe(tool))
        return str(p) if p.exists() else None
    for c in (SPIKE / "bin" / _exe(tool), SPIKE / "bin" / "acestep" / _exe(tool),
              SPIKE / "acestep.cpp" / "build" / _exe(tool),
              SPIKE / "acestep.cpp" / "build" / "Release" / _exe(tool),
              SPIKE / "acestep.cpp" / "build" / "bin" / _exe(tool)):
        if c.exists():
            return str(c)
    return shutil.which(tool)


def _user_models_dirs() -> list[Path]:
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Application Support" / "Reelfy" / "music-models"]
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return [Path(base) / "Reelfy" / "music-models"] if base else []
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return [Path(xdg) / "Reelfy" / "music-models"]


_VERIFIED: dict[str, tuple] = {}


def _model_file_ok(p: Path, m: dict, verify: bool = False) -> bool:
    """Tamano EXACTO (una descarga cortada no cuenta) y, con `verify`, sha256 comprobado una vez:
    se deja una marca `<archivo>.ok` (tamano, mtime, sha256) para no volver a hashear 3.6 GB."""
    try:
        st = p.stat()
    except OSError:
        return False
    if st.st_size != m["size"]:
        return False
    if not verify:
        return True
    key = (st.st_size, st.st_mtime_ns)
    if _VERIFIED.get(str(p)) == key:
        return True
    mark = p.with_name(p.name + ".ok")
    try:
        d = json.loads(mark.read_text())
        if d.get("size") == st.st_size and d.get("mtime_ns") == st.st_mtime_ns and d.get("sha256") == m["sha256"]:
            _VERIFIED[str(p)] = key
            return True
    except Exception:
        pass
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != m["sha256"]:
        return False
    _VERIFIED[str(p)] = key
    try:
        mark.write_text(json.dumps({"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": m["sha256"]}))
    except OSError:  # carpeta de solo lectura: basta con la verificacion en memoria
        pass
    return True


def _models_complete(d: Path, verify: bool = False) -> bool:
    return all(_model_file_ok(d / m["file"], m, verify) for m in MUSIC_MODEL_FILES)


def music_models_dir() -> Path:
    """Carpeta de los GGUF. Env REELFY_MUSIC_MODELS manda. Si no: la primera carpeta completa entre
    DATA/music-models (app) y la de usuario (~/Library/Application Support/Reelfy/music-models en
    Mac). Si ninguna esta completa, devuelve el destino de descarga del setup."""
    env = os.environ.get("REELFY_MUSIC_MODELS")
    if env:
        return Path(env)
    app = DATA / "music-models"
    cands = ([app] + _user_models_dirs()) if os.environ.get("REELFY_DATA") else (_user_models_dirs() + [app])
    for c in cands:
        if _models_complete(c):
            return c
    for c in cands:
        if c.exists() and any(c.glob("*.gguf")):
            return c
    return cands[0]


def music_model_status() -> dict:
    """Estado detallado para el setup/UI: carpeta, binario y cada archivo (presente/completo)."""
    d = music_models_dir()
    files, missing = [], 0
    for m in MUSIC_MODEL_FILES:
        p = d / m["file"]
        size = p.stat().st_size if p.exists() else 0
        ok = size == m["size"]
        if not ok:
            missing += max(0, m["size"] - size) if size <= m["size"] else m["size"]
        mark = p.with_name(p.name + ".ok")
        files.append({**m, "path": str(p), "present": p.exists(), "bytes": size, "ok": ok,
                      "verified": ok and (_VERIFIED.get(str(p)) is not None or mark.exists())})
    b = ace_bin()
    return {"dir": str(d), "bin": b, "files": files, "missing_bytes": max(0, missing),
            "ready": bool(b) and all(f["ok"] for f in files)}


def music_ready() -> bool:
    """True si la musica IA puede correr: binario presente y los 3 GGUF completos (tamano exacto
    y sha256 verificado una vez; la primera llamada tarda ~2-4 s en hashear)."""
    return bool(ace_bin()) and _models_complete(music_models_dir(), verify=True)


# ---------------------------------------------------------------- prompts por estilo y animo
MOODS = ("upbeat", "chill", "inspiring", "corporate", "playful", "dramatic")
STYLE_DEFAULT_MOOD = {"dinamico": "upbeat", "elegante": "inspiring", "minimal": "chill",
                      "energetico": "upbeat"}
STYLE_MUSIC = {
    "dinamico": {"genre": "pop", "bpm": 110, "keys": ["A major", "G major", "C major", "E major"],
                 "caption": "modern upbeat pop, bright synth plucks, warm round bass, punchy clean drums, crisp claps"},
    "elegante": {"genre": "piano", "bpm": 92, "keys": ["D major", "F major", "Bb major", "E major"],
                 "caption": "elegant modern piano, soft warm strings, gentle pad, light tasteful percussion with a soft kick"},
    "minimal": {"genre": "ambient", "bpm": 96, "keys": ["F major", "D minor", "A minor", "C major"],
                "caption": "minimal modern electronic, soft plucked pulses, airy warm pads, gentle kick, clean and spacious"},
    "energetico": {"genre": "edm", "bpm": 124, "keys": ["A minor", "F minor", "G minor", "C minor"],
                   "caption": "energetic dance pop, four on the floor kick, driving bass, big bright synth chords, claps, open hi hats"},
}
MOOD_MUSIC = {
    "upbeat": {"words": "happy, positive, confident, bright", "bpm": 4, "mode": None},
    "chill": {"words": "relaxed, smooth, laid back, soft dynamics", "bpm": -14, "mode": None},
    "inspiring": {"words": "inspiring, hopeful, uplifting, warm", "bpm": -2, "mode": "major"},
    "corporate": {"words": "clean corporate, professional, modern, trustworthy", "bpm": 0, "mode": "major"},
    "playful": {"words": "playful, quirky, bouncy, fun, marimba and pizzicato accents", "bpm": 6, "mode": "major"},
    "dramatic": {"words": "dramatic, cinematic tension, deep percussion, epic", "bpm": -8, "mode": "minor"},
}
BED_WORDS = ("instrumental background music for a short vertical video with voice over, "
             "starts on the beat, steady energy, leaves space for the voice")
NO_VOCALS = "no vocals, no singing, no voice"


BPM_MIN, BPM_MAX = 66.0, 160.0

# Parametros editables de plan.fx.music (los consume schema.py / el inspector del Studio)
CFG_SCHEMA = {
    "enabled": {"type": "bool", "default": True, "label": "Musica"},
    "source": {"type": "enum", "options": ["ai", "procedural", "library", "file", "none"], "default": "ai",
               "label": "Fuente"},
    "mood": {"type": "enum", "options": list(MOODS), "default": None, "label": "Animo"},
    "prompt": {"type": "text", "default": "", "label": "Descripcion (IA)"},
    "bpm": {"type": "number", "min": 0, "max": BPM_MAX, "default": 0,
            "label": "BPM (0 = automatico; valores 1-65 se suben a 66)"},
    "bpm_lock": {"type": "bool", "default": False, "label": "Forzar BPM (incluso 110)"},
    "keyscale": {"type": "text", "default": None, "label": "Tonalidad (p. ej. A major)"},
    "seed": {"type": "int", "default": None, "label": "Variacion (otra musica)"},
    "track": {"type": "enum", "options": "library_tracks", "default": None, "label": "Pista"},
    "file": {"type": "file", "default": None, "label": "Archivo propio (fuente: file)"},
    "ending": {"type": "enum", "options": ["auto", "natural", "hit", "fade"], "default": "auto",
               "label": "Final"},
    "drop_at": {"type": "time", "default": None, "label": "Golpe principal en"},
    "fade_out": {"type": "number", "min": 0.6, "max": 3.0, "default": 1.5, "label": "Cola final (s)"},
}

# palabras que piden voz: se quitan del prompt libre (ACE-Step tararea o canta si las ve)
_VOCAL_RE = re.compile(r"\b(vocal(?:s|es|ist)?|voices?|voz|voces|sing(?:s|ing|er|ers)?|sung|cant(?:o|a|an|ando|ante|antes)"
                       r"|rap(?:s|ping|per|pers)?|rapeo|choir|coros?|lyrics?|letras?|humming|tarareo|acapella"
                       r"|a cappella|spoken|narration|narracion)\b", re.I)
_CLAUSE_RE = re.compile(r"\s*(?:,|;|\.|\bwith\b|\bcon\b|\band\b|\by\b|\bfeaturing\b|\bfeat\b)\s*", re.I)
# genero del procedural segun el texto (gana la primera mencion en el prompt)
_GENRE_WORDS = (
    ("lofi", r"lo-?fi|hip ?hop|boom ?bap|chillhop"),
    ("trap", r"\btrap\b|\b808s?\b|\bdrill\b"),
    ("edm", r"\bedm\b|\bdance\b|\bhouse\b|techno|\bclub\b|four on the floor|future bass|\belectro\b"),
    ("cinematic", r"cinematic|orchestral|\bepic\b|trailer|cinematograf"),
    ("piano", r"piano|\bstrings\b|classical|acoustic|elegant|elegante"),
    ("ambient", r"ambient|airy|atmospher|soft pulses|minimal electronic|ambiental"),
    ("pop", r"\bpop\b"),
)


def genre_from_text(text: str) -> str | None:
    """Genero del generador procedural que corresponde a un prompt libre (o None)."""
    low = (text or "").lower()
    best = None
    for g, pat in _GENRE_WORDS:
        m = re.search(pat, low)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), g)
    return best[1] if best else None


def strip_vocals(text: str) -> str:
    """Quita del prompt libre las clausulas que piden voz ('with female vocals singing')."""
    parts = [c.strip() for c in _CLAUSE_RE.split(text or "") if c and c.strip()]
    keep = [c for c in parts if not _VOCAL_RE.search(c)]
    return ", ".join(keep)


def _flip_mode(key: str, mode: str | None) -> str:
    if not mode:
        return key
    root = key.split()[0]
    return f"{root} {mode}"


def build_prompt(style: str = "dinamico", mood: str | None = None, custom: str = "",
                 bpm: float | None = None, seed: int = 0, keyscale: str | None = None) -> dict:
    """Prompt de ACE-Step (siempre instrumental) + bpm + tonalidad para estilo x animo.

    `custom` (texto libre del usuario o del director) describe el genero y reemplaza la
    descripcion del estilo; se le quitan las clausulas que piden voz y SIEMPRE se le agrega el
    sufijo funcional (instrumental, cama para voz en off, arranca en el beat) y, si el animo se
    pidio explicito, sus palabras. `bpm`/`keyscale` explicitos mandan (bpm se limita a
    [BPM_MIN, BPM_MAX]). `genre` sale del texto libre si lo nombra (para el procedural).
    """
    style = style if style in STYLE_MUSIC else "dinamico"
    st = STYLE_MUSIC[style]
    explicit = mood in MOOD_MUSIC
    mood = mood if explicit else STYLE_DEFAULT_MOOD.get(style, "upbeat")
    md = MOOD_MUSIC[mood]
    b = float(bpm) if bpm and float(bpm) > 0 else float(st["bpm"] + md["bpm"])
    b = float(int(round(min(BPM_MAX, max(BPM_MIN, b)))))
    key = valid_keyscale(keyscale) if keyscale else None
    if not key:
        keys = st["keys"]
        key = _flip_mode(keys[abs(int(seed)) % len(keys)], md["mode"])
    custom = strip_vocals((custom or "").strip().rstrip("."))
    genre = st["genre"]
    if custom:
        genre = genre_from_text(custom) or genre
        cap = custom
        if "instrumental" not in custom.lower():
            cap += ", instrumental"
        if explicit:
            cap += ", " + md["words"]
        cap += f", {BED_WORDS}, {NO_VOCALS}"
    else:
        cap = f"{st['caption']}, {md['words']}, {BED_WORDS}, {NO_VOCALS}"
    return {"caption": cap, "lyrics": "[Instrumental]", "bpm": b, "keyscale": key,
            "genre": genre, "mood": mood, "style": style}


# ---------------------------------------------------------------- utilidades de audio
def load_audio(src: str | Path, sr: int = SR) -> np.ndarray:
    """Lee cualquier audio (wav/flac/mp3/m4a...) a float32 (n, 2) a `sr`."""
    p = Path(src)
    x = None
    if p.suffix.lower() in (".wav", ".flac", ".ogg", ".aiff", ".aif"):
        try:
            x, fs = sf.read(str(p), dtype="float32", always_2d=True)
        except Exception:
            x = None
    if x is None:
        cmd = [FFMPEG, "-v", "error", "-i", str(p), "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"]
        raw = subprocess.run(cmd, capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    elif x.shape[1] > 2:
        x = x[:, :2]
    if fs != sr:
        g = math.gcd(int(fs), int(sr))
        x = signal.resample_poly(x, sr // g, int(fs) // g, axis=0)
    return np.ascontiguousarray(x, dtype=np.float32)


def write_audio(path: str | Path, x: np.ndarray, sr: int = SR, subtype: str = "PCM_24") -> Path:
    """Escritura atomica (tmp + rename) para que otro proceso nunca lea un archivo a medias."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.{os.getpid()}.{threading.get_ident()}{path.suffix}")
    sf.write(str(tmp), np.clip(x, -1.0, 1.0), sr, subtype=subtype)
    os.replace(tmp, path)
    return path


def _biquad(kind: str, fc: float, q: float, gain_db: float, fs: float) -> tuple[np.ndarray, np.ndarray]:
    a_ = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * fc / fs
    al = math.sin(w0) / (2 * q)
    c = math.cos(w0)
    if kind == "shelf":
        b = [a_ * ((a_ + 1) + (a_ - 1) * c + 2 * math.sqrt(a_) * al),
             -2 * a_ * ((a_ - 1) + (a_ + 1) * c),
             a_ * ((a_ + 1) + (a_ - 1) * c - 2 * math.sqrt(a_) * al)]
        a = [(a_ + 1) - (a_ - 1) * c + 2 * math.sqrt(a_) * al,
             2 * ((a_ - 1) - (a_ + 1) * c),
             (a_ + 1) - (a_ - 1) * c - 2 * math.sqrt(a_) * al]
    elif kind == "peak":
        b = [1 + al * a_, -2 * c, 1 - al * a_]
        a = [1 + al / a_, -2 * c, 1 - al / a_]
    else:  # highpass
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
        a = [1 + al, -2 * c, 1 - al]
    return np.array(b) / a[0], np.array(a) / a[0]


def _kweight(x: np.ndarray, sr: int) -> np.ndarray:
    b1, a1 = _biquad("shelf", 1681.974450955533, 0.7071752369554196, 4.0, sr)
    b2, a2 = _biquad("hp", 38.13547087602444, 0.5003270373238773, 0.0, sr)
    return signal.lfilter(b2, a2, signal.lfilter(b1, a1, x, axis=0), axis=0)


def loudness_lufs(x: np.ndarray, sr: int = SR, gated: bool = True) -> float:
    """Loudness integrada ITU-R BS.1770-4 (bloques 400 ms, 75% traslape, compuertas -70/-10)."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    if len(x) < int(0.4 * sr):
        return float("-inf")
    y = _kweight(x, sr)
    blk, hop = int(0.4 * sr), int(0.1 * sr)
    nb = 1 + (len(y) - blk) // hop
    p2 = np.cumsum(np.concatenate([np.zeros((1, y.shape[1])), y ** 2]), axis=0)
    idx = np.arange(nb) * hop
    z = (p2[idx + blk] - p2[idx]) / blk          # (nb, ch)
    zs = z.sum(axis=1)
    lk = -0.691 + 10 * np.log10(zs + 1e-20)
    if not gated:
        return float(-0.691 + 10 * np.log10(zs.mean() + 1e-20))
    m = lk > -70
    if not m.any():
        return float("-inf")
    rel = -0.691 + 10 * np.log10(zs[m].mean()) - 10
    m &= lk > rel
    if not m.any():
        return float("-inf")
    return float(-0.691 + 10 * np.log10(zs[m].mean()))


def true_peak_db(x: np.ndarray) -> float:
    """Pico con sobremuestreo 4x (aproximacion de true peak)."""
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0:
        return float("-inf")
    up = signal.resample_poly(x, 4, 1, axis=0)
    return float(20 * np.log10(max(np.abs(up).max(), np.abs(x).max()) + 1e-12))


def _limiter(y: np.ndarray, sr: int, ceiling_db: float, look: float = 0.004, release: float = 0.12) -> np.ndarray:
    """Limitador de picos con lookahead por bloques (sin bucle por muestra)."""
    c = 10 ** (ceiling_db / 20)
    n = len(y)
    B = 32
    nb = -(-n // B)
    # deteccion de picos sobre la senal sobremuestreada 4x (true peak, no solo muestras)
    yy = y if y.ndim == 2 else y[:, None]
    up = np.abs(signal.resample_poly(yy, 4, 1, axis=0))[: 4 * n]
    pk = up.reshape(n, 4, yy.shape[1]).max(axis=(1, 2)) if len(up) == 4 * n else np.abs(yy).max(axis=1)
    pk = np.maximum(pk, np.abs(yy).max(axis=1))
    pk = np.pad(pk, (0, nb * B - n))
    need = np.minimum(1.0, c / np.maximum(pk.reshape(nb, B).max(axis=1), 1e-9))
    if need.min() >= 1.0:
        return y
    la = max(1, int(look * sr / B))
    need = ndimage.minimum_filter1d(need, size=2 * la + 1, mode="nearest")
    k = 1.0 - math.exp(-B / (release * sr))
    g = np.empty_like(need)
    cur = 1.0
    for i in range(nb):  # ~1500 iteraciones por segundo de audio: barato
        v = need[i]
        cur = v if v < cur else cur + (1.0 - cur) * k
        if cur > v:
            cur = v
        g[i] = cur
    gs = np.interp(np.arange(n), np.arange(nb) * B + B / 2, g).astype(np.float32)
    out = y * (gs[:, None] if y.ndim == 2 else gs)
    return np.clip(out, -c, c)


def _soft_compress(y: np.ndarray, sr: int, ratio: float = 2.0, pct: float = 80.0) -> np.ndarray:
    """Compresion de bus suave (RMS 30 ms, umbral en el percentil `pct`, rodilla por suavizado de
    la ganancia ~25 ms): baja el factor de cresta de material muy dinamico (piano, cinematico) para
    que llegue al nivel objetivo sin aplastar los picos con el limitador."""
    env = np.sqrt(ndimage.uniform_filter1d((y.astype(np.float64) ** 2).mean(axis=1), size=int(0.03 * sr)) + 1e-12)
    thr = max(float(np.percentile(env, pct)), 1e-6)
    g = np.where(env > thr, (env / thr) ** (1.0 / ratio - 1.0), 1.0)
    g = ndimage.uniform_filter1d(ndimage.minimum_filter1d(g, size=int(0.005 * sr)), size=int(0.025 * sr))
    return (y * g[:, None]).astype(np.float32)


def normalize(y: np.ndarray, sr: int = SR, target: float = TARGET_LUFS, peak_db: float = PEAK_DB,
              max_limit: float = MAX_LIMIT_DB) -> tuple[np.ndarray, float]:
    """Lleva `y` a `target` LUFS limitando picos a `peak_db`. Si llegar exige mas de `max_limit`
    dB de limitacion (material muy dinamico), primero se comprime suave (2:1, hasta 2 pasadas) y
    lo que aun falte se deja por debajo del objetivo en vez de aplastarlo. El nivel real se
    devuelve (y se reporta en get_music como `lufs`)."""
    L = loudness_lufs(y, sr)
    if not np.isfinite(L):
        return y.astype(np.float32), L
    for _ in range(2):
        pk = 20 * np.log10(np.abs(y).max() + 1e-12)
        if pk + (target - L) <= peak_db + max_limit:
            break
        y = _soft_compress(y, sr)
        L = loudness_lufs(y, sr)
    pk = 20 * np.log10(np.abs(y).max() + 1e-12)
    g_db = target - L
    if pk + g_db > peak_db + max_limit:
        g_db = peak_db + max_limit - pk
    y = (y * 10 ** (g_db / 20)).astype(np.float32)
    y = _limiter(y, sr, peak_db - 0.2)
    if true_peak_db(y) > peak_db:  # segunda pasada si la interpolacion de ganancia dejo algo
        y = _limiter(y, sr, peak_db - 0.4)
    return y, loudness_lufs(y, sr)


def _frames_db(mono: np.ndarray, sr: int, hop_s: float = 0.01) -> np.ndarray:
    hop = max(1, int(sr * hop_s))
    nf = len(mono) // hop
    if nf == 0:
        return np.full(1, -120.0)
    fr = mono[: nf * hop].reshape(nf, hop).astype(np.float64)
    return 10 * np.log10((fr ** 2).mean(axis=1) + 1e-12)


def silence_runs(y: np.ndarray, sr: int = SR, rel_db: float = -35.0, min_len: float = 0.5,
                 ref_db: float | None = None) -> list[tuple[float, float]]:
    """Tramos (inicio, fin) mas largos que `min_len` por debajo de ref+rel_db (ref = p95)."""
    mono = y.mean(axis=1) if y.ndim == 2 else y
    db = _frames_db(mono, sr, 0.02)
    ref = np.percentile(db, 95) if ref_db is None else ref_db
    low = db < ref + rel_db
    out, i = [], 0
    while i < len(low):
        if low[i]:
            j = i
            while j < len(low) and low[j]:
                j += 1
            if (j - i) * 0.02 >= min_len:
                out.append((i * 0.02, j * 0.02))
            i = j
        else:
            i += 1
    return out


# ---------------------------------------------------------------- analisis (beats, downbeats, secciones)
def _tempo_sweep(env: np.ndarray, fsr: float, start: float, end: float, T0: float,
                 span: float = 0.035, n: int = 141) -> tuple[float, float, float]:
    """Periodo T (+-span alrededor de T0) y fase que maximizan la envolvente media en la rejilla."""
    tt = np.arange(len(env)) / fsr
    best = (-1.0, T0, 0.0)
    for Tc in T0 * (1 + np.linspace(-span, span, n)):
        phs = np.arange(0.0, Tc, 0.5 / fsr)
        ks = np.arange(0, max(1, int((end - start) / Tc)) + 1)
        v = np.interp(start + phs[:, None] + Tc * ks[None, :], tt, env).mean(axis=1)
        i = int(np.argmax(v))
        if v[i] > best[0]:
            best = (float(v[i]), float(Tc), float(phs[i]))
    return best


def _perc_analysis(S: np.ndarray, fs2: int, n_fft: int = 2048) -> dict:
    """Componente percusivo (HPSS) por bandas: derivada positiva de la energia LINEAL en < 150 Hz
    (bombo), 150-3500 Hz (tarola/cuerpo) y 4-10 kHz (claps, crack de la caja: marca 2 y 4),
    normalizadas a 1, y `kurt` = curtosis de la derivada percusiva (20-3500 Hz): los golpes reales
    dan picos aislados (curtosis > 10); un drone o un pad con vaiven de volumen da una derivada
    suave (curtosis < 6) aunque su "pulso" tenga contraste.

    En escala log, el bombeo del sidechain y un bajo sincopado (notas en el contratiempo) parecen
    golpes; separar lo percusivo y medir energia lineal deja al bombo y la tarola como guia."""
    import librosa
    from scipy import stats

    P = librosa.decompose.hpss(S, kernel_size=17)[1]
    f = np.fft.rfftfreq(n_fft, 1.0 / fs2)[: S.shape[0]]
    out = {}
    for name, lo, hi in (("kick", 20, 150), ("snare", 150, 3500), ("hi", 4000, 10000)):
        e = P[(f >= lo) & (f < hi)].sum(axis=0)
        e = np.maximum(0.0, np.diff(e, prepend=e[:1]))
        out[name] = ndimage.gaussian_filter1d(e / (e.max() + 1e-12), 1.0)
    ep = P[(f >= 20) & (f < 3500)].sum(axis=0)
    d = np.maximum(0.0, np.diff(ep))
    out["kurt"] = float(stats.kurtosis(d)) if d.size > 8 and d.std() > 0 else 0.0
    return out


def _perc_envs(S: np.ndarray, fs2: int, n_fft: int = 2048, with_hi: bool = False):
    """(bombo, tarola[, brillo]) normalizadas (ver `_perc_analysis`)."""
    a = _perc_analysis(S, fs2, n_fft)
    return (a["kick"], a["snare"], a["hi"]) if with_hi else (a["kick"], a["snare"])


def _regularize_runs(beats: np.ndarray, T: float, e: np.ndarray, fsr: float, start: float,
                     end: float) -> np.ndarray:
    """Reemplaza tramos donde el DP derivo (2+ intervalos seguidos a >6% del periodo) por una
    rejilla de periodo T cuya fase se ajusta a la envolvente `e` entre los beats buenos vecinos.
    Un intervalo aislado fuera de rango (corrimiento de groove entre secciones) se respeta."""
    if len(beats) < 3:
        return beats
    tt = np.arange(len(e)) / fsr
    d = np.diff(beats)
    bad_int = np.abs(d - T) > 0.06 * T
    n = len(beats)
    bad = np.zeros(n, bool)
    i = 0
    while i < len(bad_int):
        if bad_int[i]:
            j = i
            while j < len(bad_int) and bad_int[j]:
                j += 1
            if j - i >= 2:               # corrida: beats i+1 .. j-1 son dudosos (y los bordes)
                bad[i + 1:j] = True
                if i == 0:
                    bad[0] = True
                if j == len(bad_int):
                    bad[n - 1] = True
            i = j
        else:
            i += 1
    if not bad.any():
        return beats
    out = list(beats[~bad])
    k = 0
    while k < n:
        if not bad[k]:
            k += 1
            continue
        m = k
        while m < n and bad[m]:
            m += 1
        left = beats[k - 1] if k > 0 else None
        right = beats[m] if m < n else None
        ts = left + 0.5 * T if left is not None else start - 0.03
        te = right - 0.5 * T if right is not None else end
        if te > ts:
            if left is not None:         # continuar la rejilla desde el beat bueno de la izquierda
                cands = [left]
            elif right is not None:
                cands = [right]
            else:
                cands = []
            best, bph = -1e9, 0.0
            if cands:
                g0 = cands[0]
                ks = np.arange(math.floor((ts - g0) / T), math.ceil((te - g0) / T) + 1)
                for dph in np.linspace(-0.25 * T, 0.25 * T, 41):
                    g = g0 + dph + T * ks
                    g = g[(g >= ts) & (g <= te)]
                    if len(g):
                        v = float(np.interp(g, tt, e).mean()) - 2.0 * abs(dph) / T
                        if v > best:
                            best, bph = v, dph
                g = g0 + bph + T * ks
            else:
                g = start + np.arange(0, int((end - start) / T) + 1) * T
            g = g[(g >= ts) & (g <= te)]
            out.extend(g.tolist())
        k = m
    return np.sort(np.array(out))


PULSE_MIN = 1.5           # contraste percusivo beat/contratiempo minimo para creer en la rejilla
PULSE_KURT = 8.0          # curtosis minima de la derivada percusiva (golpes reales, no un vaiven)
MAX_DOUBLE_BPM = 170.0    # al corregir octava, no duplicar por encima de esto


def _grid_on_off(e: np.ndarray, fsr: float, start: float, end: float, T: float,
                 ph: float) -> tuple[float, float]:
    """Media de `e` en la rejilla (start + ph + kT) y en su contratiempo (+T/2)."""
    tt = np.arange(len(e)) / fsr
    g = start + ph + T * np.arange(0, max(1, int((end - start - ph) / T)) + 1)
    g = g[g <= end]
    if not len(g):
        return 0.0, 0.0
    return float(np.interp(g, tt, e).mean()), float(np.interp(g + T / 2, tt, e).mean())


def _estimate_period(env: np.ndarray, e_pk: np.ndarray, oenv: np.ndarray, fs2: int, H: int,
                     start: float, end: float, hint: float | None) -> float:
    """Periodo del beat.

    Con `hint` (el bpm que se le pidio a ACE, o un metadato confiable): barrido fino +-3.5%.
    Sin hint, librosa se equivoca de octava (128 -> 64) o de 3/2: se evaluan T0 * {1/2, 2/3, 1,
    3/2, 2} (barrido +-8% cada uno) sobre la envolvente percusiva + flujo espectral:
      1. se elige la rejilla mas saliente (media en la rejilla / media global): descarta 2/3 y 3/2
         (rejillas desalineadas) pero favorece la octava lenta (subconjunto de los golpes fuertes),
      2. se duplica el tempo mientras el contratiempo de la rejilla elegida tenga golpes fuertes
         (media ahi >= 1.5x la media global: la caja en 2 y 4 de un "57 BPM" que en realidad es
         114) y el resultado no pase de MAX_DOUBLE_BPM.
    """
    fsr = fs2 / H
    if hint:
        return _tempo_sweep(env, fsr, start, end, 60.0 / float(hint))[1]
    import librosa

    tg = librosa.feature.tempo(onset_envelope=oenv, sr=fs2, hop_length=H)
    T0 = 60.0 / max(1e-3, float(np.atleast_1d(tg)[0]))
    mix = e_pk / (e_pk.std() + 1e-12) + 0.5 * env / (env.std() + 1e-12)
    i0, i1 = int(start * fsr), max(int(start * fsr) + 1, int(end * fsr))
    mean = float(mix[i0:i1].mean()) + 1e-12
    best = None
    for r in (0.5, 2.0 / 3.0, 1.0, 1.5, 2.0):
        Tc = T0 * r
        if not 50.0 <= 60.0 / Tc <= 200.0:
            continue
        _, T, ph = _tempo_sweep(mix, fsr, start, end, Tc, span=0.08, n=161)
        on, off = _grid_on_off(mix, fsr, start, end, T, ph)
        if best is None or on / mean > best[0]:
            best = (on / mean, T, off / mean)
    if best is None:
        return T0
    _, T, off_rel = best
    for _ in range(3):
        if off_rel < 1.5 or 120.0 / T > MAX_DOUBLE_BPM:
            break
        _, T, ph = _tempo_sweep(mix, fsr, start, end, T / 2, span=0.01, n=41)
        off_rel = _grid_on_off(mix, fsr, start, end, T, ph)[1] / mean
    while 60.0 / T > 200.0:
        T *= 2
    return float(T)


def _rephase_quiet(beats: np.ndarray, T: float, e_pk: np.ndarray, fsr: float, start: float,
                   end: float) -> np.ndarray:
    """Re-fasea tramos SIN bateria cuya rejilla quedo corrida respecto a la de la bateria vecina
    (tipico en intros de ACE: pads/plucks sin golpes y el DP se engancha al contratiempo; al
    entrar la bateria el DP "se acomoda" comprimiendo 3-4 intervalos o deja uno de 1.5T).

    Por beat se mide la percusion (bombo+tarola); las corridas de >= 4 beats sin percusion (< 25%
    del p90) junto a una corrida percusiva de >= 4 beats se comparan con la rejilla de esa bateria
    extrapolada con periodo T (se prefiere la bateria SIGUIENTE: la que entra define donde caen los
    tiempos). Si el desfase mediano es >= 0.3T, la corrida se reemplaza por la rejilla extrapolada.
    Desfases menores (el corrimiento de 16avo de ACE entre secciones) se respetan."""
    n = len(beats)
    if n < 8:
        return beats
    fi = np.clip(np.round(beats * fsr).astype(int), 0, len(e_pk) - 1)
    p = np.array([e_pk[max(0, f - 2): f + 3].max() for f in fi])
    ref = float(np.percentile(p, 90))
    if ref <= 1e-6:
        return beats
    perc = p >= 0.25 * ref

    def runs_of(mask):
        out, i = [], 0
        while i < len(mask):
            j = i
            while j < len(mask) and mask[j] == mask[i]:
                j += 1
            out.append([i, j, bool(mask[i])])
            i = j
        return out

    runs = runs_of(perc)
    for r in runs:                       # golpes sueltos (< 4 beats) no definen rejilla
        if r[2] and r[1] - r[0] < 4:
            perc[r[0]:r[1]] = False
    runs = runs_of(perc)
    segs, changed = [], False
    for k, (a, b, is_p) in enumerate(runs):
        seg = beats[a:b]
        if is_p or b - a < 4:
            segs.append(seg)
            continue
        nxt = runs[k + 1] if k + 1 < len(runs) and runs[k + 1][2] else None
        prv = runs[k - 1] if k > 0 and runs[k - 1][2] else None
        if nxt is not None:
            g = beats[nxt[0]: min(nxt[1], nxt[0] + 8)]
            g0 = float(np.median(g - T * np.arange(len(g))))
        elif prv is not None:
            g = beats[max(prv[0], prv[1] - 8): prv[1]]
            g0 = float(np.median(g - T * np.arange(len(g))))
        else:
            segs.append(seg)
            continue
        z = np.exp(2j * np.pi * (seg - g0) / T).mean()      # fase circular (+-0.5 son lo mismo)
        if abs(z) < 0.3 or abs(float(np.angle(z)) / (2 * np.pi)) < 0.3:
            segs.append(seg)
            continue
        lo = seg[0] - 0.5 * T if a > 0 else start - 0.03
        hi = seg[-1] + 0.5 * T
        kk = np.arange(math.floor((lo - g0) / T), math.ceil((hi - g0) / T) + 1)
        grid = g0 + T * kk
        segs.append(grid[(grid >= max(start - 0.03, lo)) & (grid <= min(end, hi))])
        changed = True
    if not changed:
        return beats
    out = np.sort(np.concatenate(segs))
    keep = np.concatenate([[True], np.diff(out) > 0.5 * T])
    return out[keep]


def _track_beats(y: np.ndarray, fs2: int, S: np.ndarray, oenv: np.ndarray,
                 start: float, end: float, hint: float | None) -> dict:
    """Beats reales de la pista (tiempo local, no rejilla global) + confianza de pulso.

    ACE no es metronomico entre secciones: el groove puede correrse un 16avo entre intro y drop.
    Por eso: (1) tempo global por candidatos de octava + barrido fino, (2) DP de librosa con ese
    tempo a hop 64 (el periodo del DP se cuantiza a cuadros: a hop 256 son 0.7% de error, a 64
    son 0.2%), (3) por segmento, decidir beat vs contratiempo con el componente PERCUSIVO
    grave+medio (bombo y tarola; los hats y el bajo a contratiempo engañan al DP en EDM),
    (4) tramos sin bateria re-faseados a la rejilla de la bateria vecina.
    Devuelve {beats, bpm, grid, kick, snare, hi, confidence}; grid "none" (sin beats) si el
    contraste percusivo beat/contratiempo < PULSE_MIN o la percusion no tiene golpes (curtosis <
    PULSE_KURT): pads y drones no tienen rejilla que seguir.
    """
    import librosa

    H = 256
    fsr = fs2 / H
    env = ndimage.gaussian_filter1d(oenv.astype(np.float64), 1.0)
    pa = _perc_analysis(S, fs2)
    kick, snare, hi = pa["kick"], pa["snare"], pa["hi"]
    e_pk = kick + snare
    T = _estimate_period(env, e_pk, oenv, fs2, H, start, end, hint)
    o64 = librosa.onset.onset_strength(y=y, sr=fs2, hop_length=64, n_fft=512)
    _, bf = librosa.beat.beat_track(onset_envelope=o64, sr=fs2, hop_length=64, bpm=60.0 / T,
                                    tightness=400, trim=False)
    beats = librosa.frames_to_time(bf, sr=fs2, hop_length=64)
    beats = beats[(beats >= start - 0.05) & (beats <= end)]
    tt = np.arange(len(e_pk)) / fsr
    # segmentos: se cortan donde el intervalo se aleja del periodo (corrimientos de groove)
    d = np.diff(beats)
    cuts = [0] + [i + 1 for i in range(len(d)) if abs(d[i] - T) > 0.15 * T] + [len(beats)]
    out = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        seg = beats[a:b]
        if len(seg) >= 6:
            on = float(np.interp(seg, tt, e_pk).mean())
            off = float(np.interp(seg + T / 2, tt, e_pk).mean())
            if off > 1.3 * on:  # el DP se engancho al contratiempo
                seg = seg + T / 2
                seg = seg[seg <= end]
                prev_last = out[-1][-1] if out and len(out[-1]) else -1e9
                if len(seg) and seg[0] - T >= start - 0.05 and seg[0] - T > prev_last + 0.6 * T:
                    seg = np.concatenate([[seg[0] - T], seg])
        out.append(seg)
    beats = np.sort(np.concatenate(out)) if out else beats
    beats = _rephase_quiet(beats, T, e_pk, fsr, start, end)
    beats = _regularize_runs(beats, T, e_pk + 0.5 * _zs(env[: len(e_pk)]) * e_pk.std(), fsr, start, end)
    # eliminar beats pegados tras las correcciones
    if len(beats):
        keep = np.concatenate([[True], np.diff(beats) > 0.5 * T])
        beats = beats[keep]
    # confianza de pulso: contraste de la percusion en el beat contra el contratiempo
    conf = 0.0
    if len(beats) >= 4:
        on = float(np.interp(beats, tt, e_pk).mean())
        off = float(np.interp(beats + T / 2, tt, e_pk).mean())
        conf = on / (off + 1e-9) if on > 1e-6 else 0.0
    beats = beats - 0.006  # el pico de la envolvente llega ~6-8 ms tarde respecto al ataque
    beats = beats[beats >= 0]
    grid = "dp" if len(beats) >= 4 else "tracked"
    if conf < PULSE_MIN or pa["kurt"] < PULSE_KURT:
        beats, grid = np.zeros(0), "none"
        conf = min(conf, conf * pa["kurt"] / PULSE_KURT) if pa["kurt"] > 0 else 0.0
    return {"beats": beats, "bpm": 60.0 / T, "grid": grid, "kick": kick, "snare": snare, "hi": hi,
            "confidence": round(float(conf), 3)}


def _zs(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    s = v.std()
    return (v - v.mean()) / s if s > 1e-9 else v * 0


def _downbeat_phase(beats: np.ndarray, fsr: float, kick: np.ndarray, snare: np.ndarray,
                    hi: np.ndarray, chroma: np.ndarray, hit: float | None) -> int:
    """Fase (0..3) del "1" dentro de los beats.

    Evidencia (en unidades de desviacion estandar, solo en beats con bateria para bombo/brillo):
      - bombo en 1 y 3 mas que en 2 y 4,
      - backbeat: claps/caja (4-10 kHz percusivo) en 2 y 4,
      - cambio armonico (croma) en el 1 (lo que separa 1 de 3),
      - el golpe final de la cancion cae en un 1 (prior fuerte),
      - prior suave: la musica empieza en el 1."""
    n = len(beats)
    fi = np.clip(np.round(beats * fsr).astype(int), 0, len(kick) - 1)

    def at(env):
        return np.array([env[max(0, f - 2): f + 3].max() for f in fi])

    K, Sn, Hh = at(kick), at(snare), at(hi)
    nfc = chroma.shape[1]
    bounds = np.concatenate([[max(0, fi[0] - 8)], fi, [min(nfc, fi[-1] + 8)]])
    cn = np.zeros(n)
    for i in range(n):
        a0, a1, b1 = bounds[i], bounds[i + 1], bounds[i + 2]
        a_ = chroma[:, a0:a1].mean(axis=1) if a1 > a0 else np.zeros(12)
        b_ = chroma[:, a1:b1].mean(axis=1) if b1 > a1 else np.zeros(12)
        cn[i] = 1 - float(a_ @ b_) / (np.linalg.norm(a_) * np.linalg.norm(b_) + 1e-9)
    perc = (K + Sn) >= 0.15 * max(1e-9, float((K + Sn).max()))
    if perc.sum() < 8:
        perc = np.ones(n, bool)
    idx = np.arange(n)
    zK = K / (K[perc].std() + 1e-9)
    zH = Hh / (Hh[perc].std() + 1e-9)
    zC = cn / (cn.std() + 1e-9)

    def pm(v, m, k):
        sel = (idx % 4 == k % 4) & m
        return float(v[sel].mean()) if sel.any() else 0.0

    allm = np.ones(n, bool)
    sc = np.zeros(4)
    for k in range(4):
        kc = pm(zK, perc, k) + pm(zK, perc, k + 2) - pm(zK, perc, k + 1) - pm(zK, perc, k + 3)
        bb = pm(zH, perc, k + 1) + pm(zH, perc, k + 3) - pm(zH, perc, k) - pm(zH, perc, k + 2)
        ch = pm(zC, allm, k) - np.mean([pm(zC, allm, j) for j in range(4) if j != k])
        sc[k] = 0.5 * kc + 0.6 * bb + 1.0 * ch
    if hit is not None and n:
        j = int(np.argmin(np.abs(beats - hit)))
        if abs(beats[j] - hit) < 0.08:
            sc[j % 4] += 0.6
    sc[0] += 0.1
    return int(np.argmax(sc))


def analyze(x: np.ndarray, sr: int = SR, bpm_hint: float | None = None) -> dict:
    """Analiza una pista: extension util, tempo, beats, downbeats, final y secciones.

    Devuelve dict JSON-serializable (tiempos en s del propio archivo):
      start, end, dur, bpm, beats, downbeats, grid ("dp"|"tracked"|"none"), beat_confidence,
      ending {mode: hit|fade|none, hit, end}, bars [[a, b, energia_db]], bar_chroma, drop, sections
    `bpm_hint` solo debe pasarse si es confiable (el bpm pedido a ACE, metadato de biblioteca o el
    usuario lo fijo): sin hint el tempo se estima libre de errores de octava.
    `drop` es el golpe de entrada (beat donde la energia salta), no necesariamente un downbeat.
    """
    import librosa  # import perezoso: solo cuando hay que analizar

    mono = (x.mean(axis=1) if x.ndim == 2 else x).astype(np.float32)
    dur = len(mono) / sr
    db = _frames_db(mono, sr, 0.01)
    ref = float(np.percentile(db, 95))
    act = np.where(db > ref - 34)[0]
    empty = {"start": 0.0, "end": 0.0, "dur": dur, "bpm": float(bpm_hint or 0), "beats": [],
             "downbeats": [], "grid": "none", "beat_confidence": 0.0,
             "ending": {"mode": "none", "hit": None, "end": 0.0},
             "bars": [], "bar_chroma": [], "drop": None, "sections": [], "ref_db": ref}
    if len(act) == 0 or dur < 1.0:
        return empty
    start, end = act[0] * 0.01, min(dur, (act[-1] + 1) * 0.01)

    fs2, H = 22050, 256
    y = librosa.resample(mono, orig_sr=sr, target_sr=fs2, res_type="soxr_hq")
    fsr = fs2 / H
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=H)) ** 2
    mel = librosa.feature.melspectrogram(S=S, sr=fs2, n_mels=96, fmax=fs2 / 2)
    lm = librosa.power_to_db(mel, ref=np.max)
    oenv = librosa.onset.onset_strength(S=lm, sr=fs2, hop_length=H)
    chroma = librosa.feature.chroma_stft(S=S, sr=fs2, hop_length=H, tuning=0.0)

    tr = _track_beats(y, fs2, S, oenv, start, end, bpm_hint)
    beats, bpm = np.asarray(tr["beats"], dtype=np.float64), tr["bpm"]
    empty["beat_confidence"] = tr["confidence"]
    if len(beats) < 4:
        empty.update(start=start, end=end, bpm=float(bpm), beats=[], grid="none")
        empty["ending"] = _detect_ending(db, ref, oenv, fsr, np.zeros(0), start, end, dur)
        return empty

    # final: ultimo golpe fuerte seguido de decaimiento (remate) o desvanecido
    ending = _detect_ending(db, ref, oenv, fsr, beats, start, end, dur)
    # downbeats: bombo/backbeat en tramos con bateria + cambio armonico + remate en el 1
    phase = _downbeat_phase(beats, fsr, tr["kick"], tr["snare"], tr["hi"], chroma,
                            ending.get("hit") if ending.get("mode") == "hit" else None)
    downbeats = beats[phase::4]

    # compases, energia y croma por compas
    bars, bar_chroma = [], []
    for a_, b_ in zip(downbeats[:-1], downbeats[1:]):
        i0, i1 = int(a_ * 100), int(b_ * 100)
        e = float(10 * np.log10(np.mean(10 ** (db[i0:i1] / 10)) + 1e-12)) if i1 > i0 else -120.0
        bars.append([float(a_), float(b_), e])
        f0, f1 = int(a_ * fsr), int(b_ * fsr)
        c = chroma[:, f0:f1].mean(axis=1) if f1 > f0 else np.zeros(12)
        bar_chroma.append([round(float(v), 4) for v in c])
    # cola sostenida casi muda (ACE a veces termina en un drone 25-30 dB abajo durante 5-10 s):
    # no es material util, se recorta el final ahi y el ajuste hace su propio final
    if len(bars) >= 4:
        e = np.array([b[2] for b in bars])
        top = float(np.percentile(e, 90))
        k = len(bars)
        while k > 0 and e[k - 1] < top - 22.0:
            k -= 1
        if k < len(bars) and end - bars[k][0] >= 3.0 and k >= 2:
            end = min(end, float(bars[k][0]) + 0.3)
            bars, bar_chroma = bars[:k], bar_chroma[:k]
            ending = {"mode": "none", "hit": None, "end": float(end)}
            beats = beats[beats <= end]
            downbeats = downbeats[downbeats <= end]
    drop = _find_drop(bars, ending, beats, db)
    ana = {"start": float(start), "end": float(end), "dur": float(dur), "bpm": float(bpm),
           "beats": [float(b) for b in beats], "downbeats": [float(b) for b in downbeats],
           "grid": tr["grid"], "beat_confidence": tr["confidence"], "ending": ending, "bars": bars,
           "bar_chroma": bar_chroma, "drop": drop, "ref_db": ref}
    ana["sections"] = _label_sections([(a, b, e) for a, b, e in bars])
    return ana


def _detect_ending(db: np.ndarray, ref: float, oenv: np.ndarray, fsr: float, beats: np.ndarray,
                   start: float, end: float, dur: float) -> dict:
    import librosa

    none = {"mode": "none", "hit": None, "end": float(end)}
    ons = librosa.onset.onset_detect(onset_envelope=oenv, sr=int(fsr * 256), hop_length=256, units="time")
    f0, f1 = int(start * fsr), int(end * fsr)
    if f1 - f0 < 10:
        return none
    thr = 0.35 * np.percentile(oenv[f0:f1], 99)
    cand = [t for t in ons if end - 4.0 <= t <= end - 0.05
            and oenv[min(len(oenv) - 1, int(round(t * fsr)))] >= thr]

    def mean_db(a, b):
        i0, i1 = max(0, int(a * 100)), min(len(db), int(b * 100))
        return float(10 * np.log10(np.mean(10 ** (db[i0:i1] / 10)) + 1e-12)) if i1 > i0 else -120.0

    tail_silence = dur - end >= 0.25
    if cand and len(beats):
        hit = float(cand[-1])
        j = int(np.argmin(np.abs(beats - hit)))
        if abs(beats[j] - hit) < 0.08:
            hit = float(beats[j])
        tail = end - hit
        pre = mean_db(hit - 2.0, hit - 0.05)
        post = mean_db(hit + 0.6, end) if tail > 0.8 else -120.0
        if tail <= 4.5 and (tail_silence or tail >= 1.0) and (tail < 0.8 or post < pre - 6):
            return {"mode": "hit", "hit": hit, "end": float(end)}
    # desvanecido: pendiente clara en los ultimos 8 s
    if end - start > 10:
        sec = np.array([mean_db(t, t + 1.0) for t in np.arange(end - 8.0, end - 0.5, 1.0)])
        if len(sec) >= 4:
            slope = np.polyfit(np.arange(len(sec)), sec, 1)[0]
            if slope < -2.0 and sec[-1] < sec.max() - 10:
                return {"mode": "fade", "hit": None, "end": float(end)}
    return none


def _find_drop(bars: list, ending: dict, beats: np.ndarray | None = None,
               db: np.ndarray | None = None) -> float | None:
    """Golpe de entrada principal: el compas con el mayor salto de energia (>= 3 dB sobre los 2
    anteriores, sostenido, y que entra a la zona alta de energia), refinado a nivel de beat: se reporta el primer beat (en ese
    compas o el anterior) donde la energia salta >= 4 dB y se sostiene. Asi, una entrada a mitad de
    compas (bateria que entra en el 3, o un golpe de anacrusa) se reporta donde SUENA."""
    if len(bars) < 4:
        return None
    e = np.array([b[2] for b in bars])
    hit = ending.get("hit")
    lo, hi = float(np.percentile(e, 5)), float(np.percentile(e, 95))
    best, bi = 0.0, None
    for gate in (0.7, 0.55):   # entrada a la zona ALTA de energia (no el primer acorde del intro)
        for i in range(1, len(bars)):
            if hit is not None and bars[i][0] >= hit - 0.1:
                break
            jump = e[i] - max(e[max(0, i - 2):i])
            sustained = i + 1 >= len(e) or e[i + 1] >= e[i] - 6.0
            if jump >= 3.0 and jump > best and sustained and e[i] >= lo + gate * (hi - lo):
                best, bi = jump, i
        if bi is not None:
            break
    if bi is None:
        return None
    t = float(bars[bi][0])
    if beats is None or db is None or len(beats) < 4:
        return t
    # refinamiento por beat en [2 beats antes del compas, fin del compas]: el primer beat que
    # salta >= 4 dB sobre los 2 anteriores, ya esta al nivel sostenido de despues (a <= 3 dB de
    # la mediana de los 4 siguientes: un redoble o riser previo no cuenta) y se sostiene en el
    # beat siguiente (un golpe aislado tampoco)
    b = np.asarray(beats, dtype=np.float64)
    beat = (bars[bi][1] - bars[bi][0]) / 4.0
    a0, a1 = bars[bi][0] - 2 * beat - 1e-3, bars[bi][1]
    ev = np.full(len(b), -120.0)
    for j in range(len(b) - 1):
        i0, i1 = int(b[j] * 100), int(b[j + 1] * 100)
        if i1 > i0:
            ev[j] = float(10 * np.log10(np.mean(10 ** (db[i0:i1] / 10)) + 1e-12))
    for j in range(2, len(b) - 2):
        if not (a0 - 1e-3 <= b[j] < a1 - 1e-3):
            continue
        after = ev[j + 1: j + 5]
        after = after[after > -119]
        if not len(after):
            continue
        lvl = float(np.median(after))
        if (ev[j] - max(ev[j - 2], ev[j - 1]) >= 4.0 and ev[j] >= lvl - 3.0 and ev[j + 1] >= min(ev[j], lvl) - 3.0
                and ev[j] >= lo + 0.6 * (hi - lo)):
            return float(b[j])
    return t


MIN_SPAN_DB = 6.0   # rango minimo para normalizar energia por compas (pistas parejas = todo alto)


def _norm_energy(e) -> np.ndarray:
    """dB por compas -> 0..1 relativo a la seccion fuerte (p90). Con rango < MIN_SPAN_DB (pista
    pareja) las diferencias de 1-2 dB no se inflan a 0..1: todo queda alto."""
    e = np.asarray(e, dtype=np.float64)
    if not len(e):
        return e
    lo, hi = float(np.percentile(e, 10)), float(np.percentile(e, 90))
    span = max(hi - lo, MIN_SPAN_DB)
    return np.clip((e - (hi - span)) / span, 0, 1)


def _label_sections(bars: list) -> list[dict]:
    """Etiqueta secciones por energia de compas: intro, verse, build, drop, break, outro."""
    if not bars:
        return []
    e = np.array([b[2] for b in bars], dtype=np.float64)
    en = _norm_energy(e)
    en_s = en.copy()
    if len(en) >= 3:
        en_s = np.convolve(np.pad(en, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    lvl = np.where(en_s >= 0.6, 2, np.where(en_s <= 0.3, 0, 1))
    runs = []
    i = 0
    while i < len(lvl):
        j = i
        while j < len(lvl) and lvl[j] == lvl[i]:
            j += 1
        runs.append([i, j, int(lvl[i])])
        i = j
    # fusionar corridas de 1 compas con la vecina mas parecida
    k = 0
    while len(runs) > 1 and k < len(runs):
        a, b, l = runs[k]
        if b - a < 2:
            if k == 0:
                runs[1][0] = a
            elif k == len(runs) - 1:
                runs[k - 1][1] = b
            else:
                prev, nxt = runs[k - 1], runs[k + 1]
                if abs(prev[2] - l) <= abs(nxt[2] - l):
                    prev[1] = b
                else:
                    nxt[0] = a
            runs.pop(k)
            m = 0  # re-fusionar vecinos con el mismo nivel
            while m < len(runs) - 1:
                if runs[m][2] == runs[m + 1][2]:
                    runs[m][1] = runs[m + 1][1]
                    runs.pop(m + 1)
                else:
                    m += 1
            k = 0
            continue
        k += 1
    out = []
    for n, (a, b, l) in enumerate(runs):
        if l == 2:
            label = "drop" if n > 0 and runs[n - 1][2] < 2 else "main"
        elif l == 0:
            label = "intro" if n == 0 else ("outro" if n == len(runs) - 1 else "break")
        else:
            label = "intro" if n == 0 and len(runs) > 1 and runs[1][2] > l else "verse"
            if n + 1 < len(runs) and runs[n + 1][2] == 2 and n > 0:
                label = "build"
        out.append({"start": float(bars[a][0]), "end": float(bars[b - 1][1]), "label": label,
                    "energy": round(float(en[a:b].mean()), 3)})
    return out


# ---------------------------------------------------------------- ajuste a duracion
def _reverb_ir(sr: int, rt60: float, seed: int = 0, predelay: float = 0.012, damp: float = 0.55) -> np.ndarray:
    """Respuesta al impulso estereo sintetica (ruido con decaimiento y amortiguamiento de agudos)."""
    rng = np.random.default_rng(seed + 9173)
    n = int((min(rt60 * 1.1, 4.0) + predelay) * sr)
    t = np.arange(n) / sr
    ir = rng.standard_normal((n, 2)).astype(np.float32)
    sos_d = _sos("lp", 2200.0, sr)
    dark = signal.sosfilt(sos_d, ir, axis=0)
    env_b = np.exp(-6.9 * t / max(0.2, rt60 * (1 - damp)))
    env_d = np.exp(-6.9 * t / rt60)
    ir = ir * env_b[:, None] * 0.6 + dark * env_d[:, None] * 1.4
    pd = int(predelay * sr)
    ir[:pd] = 0
    # primeras reflexiones discretas
    for d, g, ch in ((0.019, 0.5, 0), (0.023, 0.45, 1), (0.031, 0.35, 0), (0.037, 0.3, 1)):
        k = int(d * sr)
        if k < n:
            ir[k, ch] += g
    ir /= np.sqrt((ir ** 2).sum(axis=0, keepdims=True) + 1e-12)
    return ir.astype(np.float32)


def _conv(x: np.ndarray, ir: np.ndarray, n: int | None = None) -> np.ndarray:
    n = n or len(x)
    out = np.zeros((n, 2), np.float32)
    for c in range(2):
        xc = x[:, c] if x.ndim == 2 else x
        r = signal.fftconvolve(xc, ir[:, c])[:n]
        out[: len(r), c] = r
    return out


def _pick_loop(ana: dict, body_end: float, min_start: float | None = None, energy_w: float = 0.2,
               gates: tuple = (0.35, 0.0)) -> tuple[float, float] | None:
    """Mejor par de downbeats (a, b) para repetir: 16/8/4/2/1 compases, antes del remate, con
    compases de costura parecidos (croma + energia). Prefiere tramos con energia (no la intro);
    si no hay, relaja hasta usar todo el cuerpo entre downbeats. `min_start`/`energy_w`/`gates`
    permiten pedir un loop DENTRO de la seccion fuerte (despues del drop)."""
    bars = ana.get("bars") or []
    if len(bars) < 2:
        return None
    e = np.array([b[2] for b in bars])
    en = _norm_energy(e)
    ch = np.array(ana.get("bar_chroma") or np.zeros((len(bars), 12)))
    starts = [b[0] for b in bars] + [bars[-1][1]]

    def cos(i: int, j: int) -> float:
        return float(ch[i] @ ch[j]) / (np.linalg.norm(ch[i]) * np.linalg.norm(ch[j]) + 1e-9)

    i_min = 0 if min_start is None else next((i for i in range(len(bars)) if starts[i] >= min_start - 0.05), len(bars))
    for gate in gates:
        first_ok = next((i for i in range(i_min, len(bars)) if en[i] >= gate), None)
        if first_ok is None:
            continue
        best, pair = -1e9, None
        for L in (16, 8, 4, 2, 1):
            for i in range(first_ok, len(bars)):
                j = i + L
                if j >= len(starts) or starts[j] > body_end - 0.05:
                    break
                if j >= len(bars):
                    continue
                sim = cos(i, j)
                prev = cos(i - 1, j - 1) if i > 0 else sim
                sc = sim + 0.5 * prev - 0.08 * abs(e[i] - e[j]) - (0.05 * abs(e[i - 1] - e[j - 1]) if i > 0 else 0)
                sc += {16: 0.12, 8: 0.08, 4: 0.0, 2: -0.15, 1: -0.3}[L] + energy_w * min(en[i:j].mean(), 0.8)
                if energy_w > 0.5 and en[i:j].min() < gates[0]:
                    continue
                if sc > best:
                    best, pair = sc, (starts[i], starts[j])
            if pair is not None and best > 1.2:
                break
        if pair is not None:
            return pair
    if min_start is not None:
        return None
    # ultimo recurso: todo el cuerpo entre el primer y el ultimo downbeat antes del remate
    ok = [t for t in starts if t <= body_end - 0.05]
    if len(ok) >= 2 and ok[-1] - ok[0] > 0.5:
        return ok[0], ok[-1]
    return None


class _Ext:
    """Linea de tiempo extendida: lista de tramos del crudo [(a, b)] concatenados."""

    def __init__(self, segs: list[tuple[float, float]]):
        self.segs = segs
        self.offs = np.cumsum([0.0] + [b - a for a, b in segs])
        self.S = float(self.offs[-1])

    def map_times(self, ts) -> np.ndarray:
        """Tiempos del crudo -> todas sus apariciones en la linea extendida (ordenadas)."""
        out = []
        ts = np.asarray(ts, dtype=np.float64)
        for (a, b), o in zip(self.segs, self.offs[:-1]):
            m = (ts >= a - 1e-6) & (ts < b - 1e-6)
            out.append(ts[m] - a + o)
        return np.sort(np.concatenate(out)) if out else np.zeros(0)

    def render(self, x: np.ndarray, sr: int, t0: float, n: int, xf: float = 0.028) -> np.ndarray:
        """Audio de la linea extendida en [t0, t0 + n/sr) con crossfades equal-power de `xf`
        terminando 2 ms ANTES de cada costura (el downbeat entrante suena completo)."""
        out = np.zeros((n, 2), np.float32)
        L = len(x)
        nx = int(xf * sr)
        guard = int(0.002 * sr)
        for k, ((a, b), o) in enumerate(zip(self.segs, self.offs[:-1])):
            ia, ib = int(round(a * sr)), int(round(b * sr))
            io = int(round(o * sr)) - int(round(t0 * sr))   # posicion del tramo en la salida
            # cola extra para el crossfade con el siguiente tramo
            pre = nx + guard if k > 0 else 0                # el tramo arranca antes (fade-in)
            s0, s1 = ia - pre, ib
            d0 = io - pre
            if s0 < 0:
                d0 -= s0
                s0 = 0
            seg = x[s0:min(s1, L)].astype(np.float32).copy()
            if k > 0 and pre > 0:
                w = np.ones(len(seg), np.float32)
                ramp = np.sin(0.5 * np.pi * np.linspace(0, 1, nx, dtype=np.float32))
                w[:nx] = ramp[: min(nx, len(seg))] if len(seg) >= nx else ramp[: len(seg)]
                seg *= w[:, None]
            if k < len(self.segs) - 1:  # fade-out al final de este tramo (antes de la costura)
                m = len(seg)
                e0 = max(0, m - nx - guard)
                ramp = np.cos(0.5 * np.pi * np.linspace(0, 1, nx, dtype=np.float32))
                seg[e0:e0 + nx] *= ramp[: len(seg[e0:e0 + nx])][:, None]
                seg[e0 + nx:] = 0
                # la continuacion hasta la costura la pone el siguiente tramo (su pre-roll)
            lo, hi = max(0, d0), min(n, d0 + len(seg))
            if hi > lo:
                out[lo:hi] += seg[lo - d0:hi - d0]
        return out


def _apply_ending(y: np.ndarray, sr: int, mode: str, c: float | None, seed: int = 0) -> np.ndarray:
    """Final dentro de `y` (ya de largo exacto). mode: natural|hit|fade; c = tiempo del downbeat."""
    n = len(y)
    if mode == "natural" or c is None:
        if c is not None and n - int((c + 0.3) * sr) > int(0.1 * sr):
            # si la cancion se corta en seco tras el remate, llenar el hueco con la reverb del golpe
            ci = int(round(c * sr))
            hr = float(np.sqrt(np.mean(y[ci:ci + int(0.25 * sr)] ** 2)) + 1e-9)
            rest = float(np.sqrt(np.mean(y[int((c + 0.3) * sr):] ** 2)) + 1e-12)
            if rest < hr * 10 ** (-30 / 20):
                L = n - ci
                hl = min(L, int(0.3 * sr))
                hitseg = y[ci:ci + hl].copy()
                k = int(hl * 0.45)
                hitseg[hl - k:] *= np.cos(0.5 * np.pi * np.linspace(0, 1, k, dtype=np.float32))[:, None]
                tail = _conv(hitseg, _reverb_ir(sr, float(np.clip(L / sr * 1.2, 0.9, 2.4)), seed=seed), L)
                tr = float(np.sqrt(np.mean(tail[: int(0.4 * sr)] ** 2)) + 1e-9)
                y[ci:] += tail * (hr / tr) * 10 ** (-9.0 / 20)
        # el remate suena completo y su cola se desvanece en los ultimos <= 0.4 s
        f0 = n - int(0.06 * sr) if c is None else max(int((c + 0.12) * sr), n - int(0.4 * sr))
        f = max(int(0.06 * sr), n - f0)
        f = min(n, f)
        y[n - f:] *= (np.cos(0.5 * np.pi * np.linspace(0, 1, f, dtype=np.float32)) ** 2)[:, None]
        return y
    ci = int(round(c * sr))
    L = n - ci
    if L <= int(0.05 * sr) or ci < 0:
        f = min(n, int(0.06 * sr))
        y[n - f:] *= np.cos(0.5 * np.pi * np.linspace(0, 1, f, dtype=np.float32))[:, None]
        return y
    t = np.arange(L, dtype=np.float32) / sr
    if mode == "fade":
        g = np.cos(0.5 * np.pi * np.clip(t / (L / sr), 0, 1)) ** 1.5
        y[ci:] *= g[:, None]
        return y
    # "hit": deja pasar el golpe del downbeat, cae rapido y queda una cola de reverb del golpe
    hold = 0.11
    env = np.where(t < hold, 1.0, np.exp(-(t - hold) / 0.2)).astype(np.float32)
    hl = min(L, int(0.32 * sr))
    hitseg = y[ci:ci + hl].copy()
    w = np.ones(hl, np.float32)
    k = int(hl * 0.45)
    w[hl - k:] = np.cos(0.5 * np.pi * np.linspace(0, 1, k, dtype=np.float32))
    hitseg *= w[:, None]
    ir = _reverb_ir(sr, rt60=float(np.clip(L / sr * 1.2, 0.9, 2.4)), seed=seed)
    tail = _conv(hitseg, ir, L)
    hr = float(np.sqrt(np.mean(hitseg ** 2)) + 1e-9)
    tr = float(np.sqrt(np.mean(tail[: int(0.4 * sr)] ** 2)) + 1e-9)
    tail *= (hr / tr) * 10 ** (-7.0 / 20)
    y[ci:] = y[ci:] * env[:, None] + tail
    f = min(L, int(0.25 * sr))
    y[n - f:] *= np.cos(0.5 * np.pi * np.linspace(0, 1, f, dtype=np.float32))[:, None]
    return y


def _norm_stops(stops, D: float) -> list[tuple[float, float]]:
    """stops del plan ([{at, dur}] o [(at, dur)], tiempo final) -> [(at, dur)] validos y ordenados."""
    out = []
    for s in stops or []:
        try:
            at, dur = (float(s.get("at")), float(s.get("dur", 0.6))) if isinstance(s, dict) else (float(s[0]), float(s[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(at) and math.isfinite(dur) and dur > 0.05 and 0.0 <= at < D:
            out.append((at, min(dur, D - at)))
    return sorted(out)


def _fit_search(ana: dict, D: float, T: float, loop, ending: str, drop_at: float | None,
                drop_tol: float, tail: float, hook: float, hook_win: float,
                forced: float | None = None) -> tuple | None:
    """Busqueda de (costo, loops, offset en la linea extendida, modo de final, golpe final).

    `forced`: tiempo del crudo donde DEBE empezar (la parte B de una costura oculta en un stop);
    entonces no se evalua el arranque (lo resuelve la cabeza) y el drop ya viene alineado."""
    beats = np.asarray(ana["beats"], dtype=np.float64)
    dbs = np.asarray(ana["downbeats"], dtype=np.float64)
    start, end = float(ana.get("start", 0.0)), float(ana.get("end", 0.0))
    endi = ana.get("ending") or {"mode": "none"}
    emode = endi.get("mode", "none")
    hit = endi.get("hit")
    body_end = (hit - 0.05) if emode == "hit" and hit else (end - 2.0 if emode == "fade" else end)
    anchor = hit if emode == "hit" else (end - 0.02 if emode == "fade" else None)
    want_natural = ending in ("auto", "natural") and anchor is not None
    sil = [tuple(r) for r in ana.get("silences", [])]
    drop_raw = ana.get("drop")
    lo_e = _bar_energy_lookup(ana)
    tail_hit = float(np.clip(tail, 0.5, 2.5))
    bar = 4 * T
    s0 = max(0.0, float(beats[0]) - PREROLL)
    hook_eff = _hook_level(ana, hook)
    bstart = np.array([b[0] for b in ana.get("bars") or []])
    bend = np.array([b[1] for b in ana.get("bars") or []])
    ben = _bar_energy_norm(ana)

    def build(nl: int) -> _Ext:
        if nl == 0 or loop is None:
            return _Ext([(s0, end)])
        a, b = loop
        return _Ext([(s0, b)] + [(a, b)] * (nl - 1) + [(a, end)])

    best, first = None, None
    max_loops = 0 if loop is None else 60
    # loops necesarios para cubrir el video arrancando en el drop (con un loop corto pueden ser 6-8)
    need = 0
    if loop is not None and drop_raw is not None and loop[1] - loop[0] > 0.5:
        need = int(math.ceil(max(0.0, D - (end - float(drop_raw))) / (loop[1] - loop[0]))) + 1
    for nl in range(0, max_loops + 1):
        ext = build(nl)
        if ext.S < 1.0:
            break
        edb_arr = ext.map_times(dbs)
        edb = set(np.round(edb_arr, 4))
        ea = ext.map_times([anchor]) if anchor is not None else np.zeros(0)
        eanch = float(ea[-1]) if len(ea) else None
        eb_end = ext.map_times([max(start, body_end - 1e-3)])
        ebody = float(eb_end[-1]) if len(eb_end) else ext.S
        esil = []
        for a, b in sil:
            for oa in ext.map_times([a]):
                esil.append((float(oa), float(oa) + (b - a)))
        edrop = ext.map_times([drop_raw]) if (drop_at is not None and drop_raw is not None) else None
        if forced is not None:
            offs = [forced - s0] if s0 - 1e-6 <= forced < ext.segs[0][1] else []
        else:
            offs = ext.map_times(beats)
        for o in offs:
            o = float(o)
            if o > ext.S - 1.0:
                continue
            cost = 0.2 * nl + 0.004 * o
            if forced is None:
                is_db = round(o, 4) in edb
                is_half = (not is_db) and len(edb_arr) and float(np.min(np.abs(edb_arr + 2 * T - o))) < 0.03
                cost += 0.0 if is_db else (0.25 if is_half else 0.45)
                # arranque con energia (el hook): energia media ponderada por tiempo en
                # [o, o + hook_win] (2 compases o hasta el primer stop). Una anacrusa tranquila de
                # 1 compas o menos antes de la entrada se acepta con costo chico.
                if hook > 0 and len(bstart):
                    E, quiet = _window_energy(ext, o, hook_win, bstart, bend, ben, hook_eff)
                    pen = 4.0 * max(0.0, hook_eff - E) + 1.5 * max(0.0, quiet - bar) / bar
                    if quiet <= bar * 1.02 and E < hook_eff:
                        pen = min(pen, 0.3 * quiet / bar + 0.5 * max(0.0, hook_eff - E))
                else:
                    r0 = ext_to_raw(ext, o)
                    pen = 1.2 * max(0.0, 0.45 - lo_e(r0, 2 * bar))
                cost += pen
            # silencios internos dentro de la ventana
            cost += 3.0 * sum(1 for a, b in esil if a < o + D - 0.5 and b > o + 0.2)
            # tramos de 2+ compases casi mudos debajo de la voz (un break de 1 compas es musica)
            if len(bstart):
                cost += 0.8 * _quiet_bars(ext, o, D, bar, bstart, bend, ben)
            if edrop is not None and len(edrop) and forced is None:
                rel_d = edrop - o
                inside = rel_d[(rel_d >= -0.05) & (rel_d <= D - 2.0)]
                if len(inside):  # presente: cuanto mas cerca del pedido, mejor
                    dist = max(0.0, float(np.min(np.abs(inside - drop_at))) - drop_tol)
                    cost += min(2.5, 0.6 * dist / T)
                else:            # el golpe quedaria fuera del video
                    cost += 3.0
            # final natural: el remate cae en [D-1.4, D-0.25] (hit) o el fade termina ~D
            if want_natural and eanch is not None:
                rel = eanch - o
                ok = (D - 1.4 <= rel <= D - 0.25) if emode == "hit" else (D - 0.3 <= rel <= D + 1.0)
                if ok:
                    c_nat = cost + (0.0 if emode == "hit" else 0.3) + 0.15 * abs(rel - (D - 0.55))
                    if emode == "hit":  # silencio que quedaria tras la cola real del remate
                        c_nat += 1.5 * max(0.0, (D - rel) - (end - anchor) - 0.25)
                    if best is None or c_nat < best[0]:
                        best = (c_nat, nl, o, "natural", float(rel))
            if ending == "natural" and want_natural:
                continue
            # final cortado: downbeat c en [D - tail - 1 compas, D - 0.45], antes del remate
            if o + D > ext.S + 2.0:
                continue
            rel_db = edb_arr - o
            m = (rel_db >= D - tail_hit - max(2 * T, bar)) & (rel_db <= D - 0.45) & (edb_arr <= ebody)
            if not m.any():
                continue
            cands = rel_db[m]
            c = float(cands[np.argmin(np.abs(cands - (D - tail_hit)))])
            mode = "fade" if ending == "fade" else "hit"
            c_cut = cost + 0.6 + 0.2 * abs(c - (D - tail_hit)) / T
            if best is None or c_cut < best[0]:
                best = (c_cut, nl, o, mode, c)
        # con la primera solucion seguimos probando loops mientras el costo siga alto (arrancar en
        # el drop suele exigir 1-2 loops mas que arrancar en la intro)
        if best is not None and first is None:
            first = nl
        if best is not None and (best[0] < 0.5 or (nl >= first + 1 and best[0] < 1.5)
                                 or nl >= max(first + 4, need)):
            break
    return best


def _bar_energy_norm(ana: dict) -> np.ndarray:
    return _norm_energy([b[2] for b in ana.get("bars") or []])


def _hook_level(ana: dict, hook: float) -> float:
    """Energia exigida al arranque: `hook`, pero nunca mas que ~90% de lo que da el propio drop
    del crudo (una cancion que crece hasta el final no puede arrancar a su nivel maximo)."""
    if hook <= 0:
        return 0.0
    en = _bar_energy_norm(ana)
    bars = ana.get("bars") or []
    drop = ana.get("drop")
    if drop is None or not len(en):
        return float(hook)
    i = int(np.clip(np.searchsorted([b[0] for b in bars], drop + 1e-3) - 1, 0, len(en) - 1))
    ref = float(np.max(en[i:i + 2])) if len(en[i:i + 2]) else 1.0
    return float(min(hook, 0.9 * ref))


def _quiet_bars(ext: "_Ext", o: float, D: float, bar: float, bstart: np.ndarray, bend: np.ndarray,
                ben: np.ndarray, level: float = 0.12) -> int:
    """Compases (en corridas de 2 o mas) con energia normalizada < level dentro de [o, o+D-1 compas]."""
    nb = int((D - bar) / bar)
    if nb < 2:
        return 0
    ts = o + (np.arange(nb) + 0.5) * bar
    raw = np.array([ext_to_raw(ext, float(t)) for t in ts])
    k = np.clip(np.searchsorted(bstart, raw, side="right") - 1, 0, len(ben) - 1)
    inside = (raw >= bstart[k] - 1e-3) & (raw < bend[k] + 1e-3)
    q = inside & (ben[k] < level)
    total, run = 0, 0
    for v in list(q) + [False]:
        if v:
            run += 1
        else:
            total += run if run >= 2 else 0
            run = 0
    return total


def _window_energy(ext: "_Ext", o: float, win: float, bstart: np.ndarray, bend: np.ndarray,
                   ben: np.ndarray, level: float) -> tuple[float, float]:
    """(energia media normalizada ponderada por tiempo en [o, o+win] de la linea extendida,
    segundos iniciales por debajo de `level`)."""
    n = 16
    ts = o + (np.arange(n) + 0.5) * (win / n)
    raw = np.array([ext_to_raw(ext, float(t)) for t in ts])
    k = np.clip(np.searchsorted(bstart, raw, side="right") - 1, 0, len(ben) - 1)
    inside = (raw >= bstart[k] - 1e-3) & (raw < bend[k] + 1e-3)
    e = np.where(inside, ben[k], 0.0)
    below = e < level
    quiet = (int(np.argmin(below)) if not below.all() else n) * (win / n)
    return float(e.mean()), float(quiet)


def _pick_head(ana: dict, T: float, length: float, avoid: tuple[float, float] | None) -> float | None:
    """Downbeat del crudo desde donde tocar `length` s de material con energia (la cabeza antes de
    un stop): prefiere la entrada del drop (golpe desde el cuadro 0) y si no cabe, el compas con
    mas energia sostenida. Evita el remate final y los silencios."""
    bars = ana.get("bars") or []
    if not bars:
        return None
    lo_e = _bar_energy_lookup(ana)
    endi = ana.get("ending") or {}
    limit = (endi.get("hit") if endi.get("mode") == "hit" and endi.get("hit") else ana["end"]) - 0.1
    sil = [tuple(r) for r in ana.get("silences", [])]
    drop = ana.get("drop")
    best, bh = -1e9, None
    for b in bars:
        h = float(b[0])
        if h + length > limit:
            break
        if any(a < h + length and z > h for a, z in sil):
            continue
        e = lo_e(h, max(length, 4 * T))
        sc = e - (0.0 if drop is not None and abs(h - drop) < 2 * T else 0.15)
        if drop is not None and h < drop - 2 * T:
            sc -= 0.3        # material previo al drop: normalmente intro/build
        if sc > best:
            best, bh = sc, h
    if drop is not None and drop + length <= limit and bh is not None and best < lo_e(drop, 4 * T) - 0.15:
        bh = float(drop)
    return bh


def _pseudo_drop(ana: dict, body_end: float, min_t: float = 0.0) -> float | None:
    """Primer downbeat >= min_t con energia alta sostenida (compas >= 0.75 y el siguiente >= 0.6,
    normalizado) que deje al menos 4 compases antes del remate. Para crudos sin un drop marcado
    (energia pareja): el drop pedido cae igual en un 1 con energia."""
    bars = ana.get("bars") or []
    en = _bar_energy_norm(ana)
    for i in range(len(bars) - 1):
        if bars[i][0] + 4 * (bars[i][1] - bars[i][0]) > body_end:
            break
        if bars[i][0] >= min_t - 1e-3 and en[i] >= 0.75 and en[i + 1] >= 0.6:
            return float(bars[i][0])
    return None


def _pick_drop_head(ana: dict, T: float, length: float, body_end: float,
                    level: float) -> tuple[float, float, float] | None:
    """Cabeza para una costura en el golpe del drop: `length` s del crudo que terminan en un
    downbeat hd de la seccion fuerte (no el propio drop) y que suenan con energia; el compas
    previo a hd debe parecerse armonicamente al compas previo al drop (asi desemboca natural).
    Devuelve (inicio en el crudo, energia media, similitud armonica) o None."""
    bars = ana.get("bars") or []
    drop = ana.get("drop")
    if len(bars) < 3 or drop is None:
        return None
    start = float(ana.get("start", 0.0))
    starts = np.array([b[0] for b in bars])
    ends = np.array([b[1] for b in bars])
    en = _bar_energy_norm(ana)
    ch = np.array(ana.get("bar_chroma") or np.zeros((len(bars), 12)))
    i_pre = int(np.clip(np.searchsorted(starts, drop - 1e-3) - 1, 0, len(bars) - 1))

    def cos(i: int, j: int) -> float:
        return float(ch[i] @ ch[j]) / (np.linalg.norm(ch[i]) * np.linalg.norm(ch[j]) + 1e-9)

    best = None
    for j in range(1, len(bars) + 1):
        hd = float(ends[j - 1])
        if hd > body_end - 0.05 or abs(hd - drop) < T:
            continue
        h0 = hd - length
        if h0 < start + 0.02:
            continue
        m = (ends > h0 + 1e-3) & (starts < hd - 1e-3)
        w = np.minimum(ends[m], hd) - np.maximum(starts[m], h0)
        e = float((en[m] * w).sum() / max(1e-6, w.sum()))
        sim = cos(j - 1, i_pre) if j - 1 != i_pre else 0.0
        sc = e + 0.4 * sim + (0.1 if hd > drop else 0.0)
        if e >= 0.6 * level and (best is None or sc > best[0]):
            best = (sc, h0, e, sim)
    return None if best is None else (best[1], best[2], best[3])


def fit(x: np.ndarray, sr: int, ana: dict, duration: float, ending: str = "auto",
        drop_at: float | None = None, tail: float = 1.2, seed: int = 0, stops=None,
        hook: float = 0.0, drop_tol: float = 0.0) -> tuple[np.ndarray, dict]:
    """Ajusta el crudo `x` (con su `ana`) a `duration` exacta. Devuelve (audio, info) con
    beats/downbeats/drop en tiempo de salida. No normaliza (ver `normalize`).

    - `hook` (0..1): energia minima (normalizada por compas) de los primeros 2 compases (o hasta
      el primer stop). Evita que el video arranque sobre un intro casi mudo; una anacrusa de 1
      compas se acepta. 0 = solo evita intros vacias largas.
    - `drop_at` (s, tiempo FINAL) + `drop_tol`: donde debe caer la entrada del drop del crudo.
    - `stops` [(at, dur)] o [{at, dur}]: tramos que audio.py silencia. Si hay un stop que termina
      justo antes de `drop_at`, la costura se esconde dentro del silencio: antes del stop suena
      material con energia (desde el cuadro 0) y al volver entra el drop exactamente en drop_at.
      Sin stop y con drop_at explicito >= 1 s, la costura puede ir en el golpe del drop (el
      impacto la tapa) si el material previo al drop del crudo es demasiado tranquilo.
    Candidatos (arranque normal con loops, costura en stop, costura en el drop) x (loop general,
    loop dentro de la seccion fuerte) compiten por costo: hook, drop, final natural/cortado,
    silencios, loops.
    info: src_offset (s del crudo donde arranca), loops, ending, end_hit, segments, beats,
    downbeats, drop (donde QUEDO la entrada del drop en el video, o None), hook_end (fin de la
    cabeza si hubo costura), seams (tiempos de costura en el video)."""
    D = float(duration)
    N = int(round(D * sr))
    beats = np.asarray(ana.get("beats") or [], dtype=np.float64)
    dbs = np.asarray(ana.get("downbeats") or [], dtype=np.float64)
    start, end = float(ana.get("start", 0.0)), float(ana.get("end", len(x) / sr))
    if len(beats) < 4 or len(dbs) < 2:  # sin rejilla: recorte simple + loop crudo + fade
        return _fit_plain(x, sr, start, end, D, N)
    if D < 2.5:  # clip muy corto: el compas mas energico, entrando en su downbeat, con cola
        bars = ana.get("bars") or []
        o = max(bars, key=lambda b: b[2])[0] if bars else float(dbs[0])
        t0 = max(0.0, o - PREROLL)
        y = np.zeros((N, 2), np.float32)
        seg = x[int(t0 * sr): int(t0 * sr) + N]
        y[: len(seg)] = seg
        y = _apply_ending(y, sr, "natural", min(D * 0.5, 0.35), seed=seed)
        fi = int(0.006 * sr)
        y[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)[:, None]
        ob = beats[(beats >= t0) & (beats < t0 + D)] - t0
        odb = dbs[(dbs >= t0) & (dbs < t0 + D)] - t0
        return y, {"src_offset": round(t0, 4), "loops": 0, "ending": "natural", "end_hit": round(PREROLL, 4),
                   "segments": [[t0, t0 + D]], "beats": [round(float(b), 4) for b in ob],
                   "downbeats": [round(float(b), 4) for b in odb], "drop": None, "hook_end": None,
                   "seams": []}
    T = 60.0 / max(1e-3, float(ana.get("bpm") or 60.0 / np.median(np.diff(beats))))
    bar = 4 * T
    endi = ana.get("ending") or {"mode": "none"}
    emode = endi.get("mode", "none")
    hit = endi.get("hit")
    body_end = (hit - 0.05) if emode == "hit" and hit else (end - 2.0 if emode == "fade" else end)
    drop_raw = ana.get("drop")
    if drop_raw is None and drop_at is not None:
        # sin drop claro (energia pareja): se usa como "golpe" el primer downbeat de la zona fuerte,
        # asi un drop pedido (p. ej. al volver de un stop) igual cae en un 1 con energia
        st0 = _norm_stops(stops, D)
        lead = drop_at
        for at_, du_ in st0:          # con stop antes del drop basta el material despues de la costura
            if -0.35 <= drop_at - (at_ + du_) <= 4 * T:
                lead = drop_at - (at_ + min(0.5 * du_, max(0.06, du_ - 0.06)))
                break
        drop_raw = _pseudo_drop(ana, body_end, float(beats[0]) - PREROLL + max(0.0, lead))
        if drop_raw is not None:
            ana = dict(ana, drop=drop_raw)
    loops = [_pick_loop(ana, body_end)]
    if drop_raw is not None:         # loop dentro de la seccion fuerte (para arrancar en el drop)
        le = _pick_loop(ana, body_end, min_start=drop_raw, energy_w=0.8, gates=(0.5, 0.3))
        if le is not None and le != loops[0]:
            loops.append(le)
    st = _norm_stops(stops, D)
    hook_win = min(2 * bar, st[0][0]) if st else 2 * bar
    if drop_at is not None:
        drop_at = float(np.clip(drop_at, 0.0, max(0.0, D - 2.0)))
    s0 = max(0.0, float(beats[0]) - PREROLL)

    def build(nl: int, lp) -> _Ext:
        if nl == 0 or lp is None:
            return _Ext([(s0, end)])
        a, b = lp
        return _Ext([(s0, b)] + [(a, b)] * (nl - 1) + [(a, end)])

    # candidatos: (costo, tipo, resultado de busqueda, loop, extra)
    cands = []
    for lp in loops:
        r = _fit_search(ana, D, T, lp, ending, drop_at, drop_tol, tail, hook, hook_win)
        if r is not None:
            cands.append((r[0], "plain", r, lp, None))
    hook_eff = _hook_level(ana, hook)
    if drop_at is not None and drop_raw is not None:
        # (a) costura oculta en un stop que termina a <= 1 compas antes del drop pedido
        for at, dur in st:
            s_end = at + dur
            if at < 0.5 or s_end > D - 2.5 or not (-0.35 <= drop_at - s_end <= bar):
                continue
            seam = at + min(0.5 * dur, max(0.06, dur - 0.06))
            rs = float(drop_raw) - (drop_at - seam)          # inicio de la parte B en el crudo
            h = _pick_head(ana, T, seam + 0.05, None)
            if rs < s0 or h is None:
                break
            for lp in loops:
                r = _fit_search(ana, D - seam, T, lp, ending, None, 0.0, tail, 0.0, 0.0, forced=rs)
                if r is not None:
                    cands.append((r[0] + 0.1, "stop", r, lp, (seam, h - PREROLL)))
            break
        # (b) costura en el golpe del drop (el impacto la tapa): antes suena material con energia
        # que "desemboca" en el drop justo en drop_at (sin stop, drop pedido explicito >= 1 s)
        if drop_at >= 1.0 and drop_tol <= 0.05:
            hd = _pick_drop_head(ana, T, drop_at, body_end, hook_eff)
            if hd is not None:
                head_raw, e_head, sim = hd
                seam = drop_at - PREROLL
                for lp in loops:
                    r = _fit_search(ana, D - seam, T, lp, ending, None, 0.0, tail, 0.0, 0.0,
                                    forced=float(drop_raw) - PREROLL)
                    if r is not None:
                        pen = 0.35 + 4.0 * max(0.0, hook_eff - e_head) + 0.4 * (1.0 - sim)
                        cands.append((r[0] + pen, "dropcut", r, lp, (seam, head_raw)))
    if not cands:
        return _fit_plain(x, sr, start, end, D, N)
    cands.sort(key=lambda c: c[0])
    _, kind, (_, nl, o, mode, c), loop, extra = cands[0]

    seams: list[float] = []
    hook_end = None
    if kind in ("stop", "dropcut"):
        seam, head_raw = extra
        body = build(nl, loop)
        k = int(np.searchsorted(body.offs, o, side="right") - 1)
        k = min(max(k, 0), len(body.segs) - 1)
        segs = [(head_raw, head_raw + seam), (body.segs[k][0] + (o - body.offs[k]), body.segs[k][1])]
        segs += body.segs[k + 1:]
        ext = _Ext(segs)
        t0 = 0.0
        c_out = seam + c
        seams, hook_end = [seam], seam
        drop_out = float(drop_at)
    else:
        ext = build(nl, loop)
        t0 = o - PREROLL
        c_out = c + PREROLL
        drop_out = None
        if drop_raw is not None:
            ed = ext.map_times([drop_raw]) - t0
            ed = ed[(ed >= -0.02) & (ed <= D - 1.0)]
            if len(ed):
                drop_out = float(ed[np.argmin(np.abs(ed - drop_at))] if drop_at is not None else ed[0])
                drop_out = max(0.0, drop_out)
        seams = [float(v - t0) for v in ext.offs[1:-1] if 0 < v - t0 < D]
    y = ext.render(x, sr, t0, N)
    y = _apply_ending(y, sr, mode, c_out if emode == "hit" or mode != "natural" else None, seed=seed)
    fade_in = int(0.006 * sr)
    y[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)[:, None]
    # tiempos de salida (hasta el golpe final inclusive); en la costura oculta se quitan los beats
    # que caen dentro del stop (la rejilla de la cabeza y la de la parte B no son continuas)
    stop = min(D, c_out + 1e-3)
    ob = ext.map_times(beats) - t0
    odb = ext.map_times(dbs) - t0

    def clean(v):
        v = v[(v >= -1e-3) & (v <= stop)]
        if hook_end is not None:
            at0 = next((a for a, d in st if a <= hook_end <= a + d), hook_end)
            v = v[(v < at0 - 0.05) | (v >= hook_end)]
        return np.clip(v, 0, None)

    ob, odb = clean(ob), clean(odb)
    if len(ob) > 1:
        ob = ob[np.concatenate([[True], np.diff(ob) > 0.5 * T])]
    src0 = float(ext.segs[0][0]) if hook_end is not None else float(ext_to_raw(ext, t0))
    info = {"src_offset": round(src0, 4), "loops": int(nl if loop else 0),
            "ending": mode, "end_hit": round(float(c_out), 4),
            "segments": [[float(a), float(b)] for a, b in ext.segs],
            "beats": [round(float(b), 4) for b in ob], "downbeats": [round(float(b), 4) for b in odb],
            "drop": None if drop_out is None else round(float(drop_out), 4),
            "hook_end": None if hook_end is None else round(float(hook_end), 4),
            "seams": [round(float(s), 4) for s in seams]}
    return y, info


def ext_to_raw(ext: _Ext, t: float) -> float:
    k = int(np.searchsorted(ext.offs, t, side="right") - 1)
    k = min(max(k, 0), len(ext.segs) - 1)
    return ext.segs[k][0] + (t - ext.offs[k])


def _bar_energy_lookup(ana: dict) -> Callable[[float, float], float]:
    bars = ana.get("bars") or []
    if not bars:
        return lambda t, w: 1.0
    en = _norm_energy([b[2] for b in bars])
    starts = np.array([b[0] for b in bars])

    def f(t: float, w: float) -> float:
        m = (starts >= t - 1e-3) & (starts < t + w)
        if not m.any():
            k = int(np.clip(np.searchsorted(starts, t) - 1, 0, len(en) - 1))
            return float(en[k])
        return float(en[m].mean())
    return f


def _fit_plain(x: np.ndarray, sr: int, start: float, end: float, D: float, N: int) -> tuple[np.ndarray, dict]:
    """Ajuste sin rejilla de beats (material sin pulso): repetir con crossfade largo y fade final."""
    a, b = int(start * sr), max(int(start * sr) + 1, int(end * sr))
    body = x[a:b]
    L = len(body)
    out = np.zeros((N, 2), np.float32)
    xf = max(1, min(int(0.25 * sr), L // 4))
    seg = body[:N]
    out[: len(seg)] = seg
    pos = len(seg)
    while pos < N and L > xf + 1:  # cada vuelta avanza L - xf > 0
        s0 = pos - xf
        seg = body[: N - s0].copy()
        k = min(xf, len(seg))
        ramp = np.sin(0.5 * np.pi * np.linspace(0, 1, k, dtype=np.float32))[:, None]
        seg[:k] *= ramp
        out[s0:s0 + k] *= np.cos(0.5 * np.pi * np.linspace(0, 1, k, dtype=np.float32))[:, None]
        out[s0:s0 + len(seg)] += seg
        pos = s0 + len(seg)
    f = min(N, int(1.2 * sr))
    out[N - f:] *= np.cos(0.5 * np.pi * np.linspace(0, 1, f, dtype=np.float32))[:, None]
    return out, {"src_offset": round(start, 4), "loops": 0, "ending": "fade", "end_hit": round(max(0.0, D - 1.2), 4),
                 "segments": [[start, end]], "beats": [], "downbeats": [], "drop": None,
                 "hook_end": None, "seams": []}


def sections_for(y: np.ndarray, sr: int, downbeats: list[float], D: float, end_hit: float | None,
                 ending: str, drop: float | None = None, hook_end: float | None = None) -> list[dict]:
    """Secciones en tiempo de salida a partir de la energia real por compas del audio final.

    `drop` (el que coloco `fit`) manda: la seccion se parte ahi con label "drop" y ningun otro
    tramo anterior se llama drop. `hook_end`: fin de la cabeza de una costura en un stop (label
    "hook"). Labels: pickup|hook|intro|verse|build|drop|main|break|outro|ending."""
    mono = y.mean(axis=1)
    db = _frames_db(mono, sr, 0.01)
    t_end = float(end_hit) if end_hit is not None else D

    def edb(a, b):
        i0, i1 = int(a * 100), int(b * 100)
        return float(10 * np.log10(np.mean(10 ** (db[i0:i1] / 10)) + 1e-12)) if i1 > i0 else -120.0

    dbs = [d for d in downbeats if d <= t_end + 1e-3]
    bars = [(a, b, edb(a, b)) for a, b in zip(dbs[:-1], dbs[1:]) if int(b * 100) > int(a * 100)]
    secs = _label_sections(bars)
    if secs and secs[0]["start"] > 0.05:  # anacrusa antes del primer downbeat
        if secs[0]["label"] == "intro":
            secs[0]["start"] = 0.0
        else:
            secs.insert(0, {"start": 0.0, "end": secs[0]["start"], "label": "pickup",
                            "energy": secs[0]["energy"]})
    if not secs:
        secs = [{"start": 0.0, "end": t_end, "label": "main", "energy": 1.0}]
    secs[-1]["end"] = t_end
    # cortes forzados: fin de la cabeza (hook) y drop colocado por fit
    for cut in (hook_end, drop):
        if cut is None or not (0.05 < cut < t_end - 0.05):
            continue
        out = []
        for s in secs:
            if s["start"] + 0.05 < cut < s["end"] - 0.05:
                out += [dict(s, end=cut), dict(s, start=cut)]
            else:
                out.append(s)
        secs = out
    if hook_end is not None:
        for s in secs:
            if s["end"] <= hook_end + 1e-3:
                s["label"] = "hook"
            elif drop is not None and s["start"] >= hook_end - 1e-3 and s["end"] <= drop + 1e-3:
                s["label"] = "build"     # lo que suena entre la costura y el drop
    if drop is not None:
        for i, s in enumerate(secs):    # un inicio a < 60 ms del drop es el drop
            if abs(s["start"] - drop) < 0.06 and s["start"] != drop:
                if i > 0:
                    secs[i - 1]["end"] = drop
                s["start"] = drop
        seen_break = False
        for s in secs:
            if s["start"] < drop - 1e-3:
                if s["label"] == "drop":
                    s["label"] = "main"
            elif abs(s["start"] - drop) < 1e-3:
                s["label"] = "drop"
            else:
                if s["label"] in ("break", "verse", "intro", "build", "outro"):
                    seen_break = True
                if s["label"] == "drop" and not seen_break:
                    s["label"] = "main"
        # un "build" que no desemboca en drop es un verso
        for i, s in enumerate(secs):
            if s["label"] == "build" and not (i + 1 < len(secs) and secs[i + 1]["label"] == "drop"):
                s["label"] = "verse"
    # fusionar vecinos con el mismo label y recalcular energia normalizada por compas
    merged = []
    for s in secs:
        if merged and merged[-1]["label"] == s["label"] and s["label"] != "drop":
            merged[-1]["end"] = s["end"]
        else:
            merged.append(dict(s))
    secs = merged
    if D - t_end > 0.05:
        secs.append({"start": t_end, "end": float(D), "label": "ending", "energy": 0.0})
    ev = [b[2] for b in bars] or [edb(0, t_end)]
    lo, hi = float(np.percentile(ev, 10)), float(np.percentile(ev, 90))
    span = max(hi - lo, MIN_SPAN_DB)
    for s in secs:
        if s["label"] != "ending":
            s["energy"] = round(float(np.clip((edb(s["start"], s["end"]) - (hi - span)) / span, 0, 1)), 3)
    secs = [s for s in secs if s["end"] - s["start"] > 0.05]
    if secs:
        secs[0]["start"] = 0.0
    for s in secs:
        s["start"], s["end"] = round(float(s["start"]), 3), round(float(s["end"]), 3)
    return secs


# ---------------------------------------------------------------- ACE-Step (acestep.cpp)
class MusicCancelled(Exception):
    """Envuelve la excepcion que lanzo on_pct (cancelacion del job): no se cae a otra fuente."""


def _pct(on_pct: Callable | None, v: float) -> None:
    if on_pct is None:
        return
    try:
        on_pct(int(max(0, min(100, v))))
    except MusicCancelled:
        raise
    except Exception as e:  # cancelacion
        raise MusicCancelled(e) from e


class _Progress:
    """Progreso monotono (varios intentos/fuentes no hacen retroceder la barra)."""

    def __init__(self, cb: Callable | None):
        self.cb, self.v = cb, -1

    def __call__(self, p: float) -> None:
        p = int(max(0, min(100, p)))
        if p > self.v:
            self.v = p
            if self.cb:
                self.cb(p)


def ace_request(prompt: dict, gen_dur: float, seed: int) -> dict:
    """Request DiT-only (sin LM): toda la metadata dada, lyrics [Instrumental], salida float."""
    return {
        "synth_model": DIT_MODEL, "caption": prompt["caption"], "lyrics": "[Instrumental]",
        "bpm": int(round(prompt["bpm"])), "duration": float(round(gen_dur, 2)),
        "keyscale": prompt["keyscale"], "timesignature": "4", "vocal_language": "unknown",
        "inference_steps": 8, "guidance_scale": 1.0, "shift": 3.0, "seed": int(seed) & 0x7FFFFFFF,
        "output_format": "wav32", "peak_clip": 0,
    }


LAST_ACE_TIMING: dict = {}   # desglose de la ultima generacion (carga, DiT, VAE) para metricas
_ACE_STAGES = (("Load TextEnc", 12), ("Load CondEnc", 16), ("Encode-Text", 18), ("Load DiT", 26),
               ("Load VAE", 58), ("VAE-Decode", 90), ("Wrote", 94))


def _ace_cli(req: dict, work: Path, on_pct: Callable | None, timeout: float) -> Path:
    b = ace_bin("ace-synth")
    if not b:
        raise RuntimeError("ace-synth no encontrado")
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("req*.wav"):
        old.unlink()
    rp = work / "req.json"
    rp.write_text(json.dumps(req, indent=1))
    cmd = [b, "--models", str(music_models_dir()), "--request", str(rp)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            cwd=str(work), bufsize=1, errors="replace")
    state = {"pct": 2.0, "t_vae": None, "tail": [], "cancel": None}
    est_vae = 0.25 * float(req["duration"]) + 1.0
    t_start = time.time()

    timing = {"load_ms": 0.0, "dit_ms": 0.0, "vae_ms": 0.0}

    def reader():
        for line in proc.stdout:
            line = line.rstrip()
            try:
                if line.startswith("[Store] Load") and line.endswith(" ms"):
                    timing["load_ms"] += float(line.split(":")[-1].split()[0])
                elif line.startswith("[DiT-Generate] Total:"):
                    timing["dit_ms"] += float(line.split(":")[1].split()[0])
                elif "] Decode:" in line and line.startswith("[VAE-Decode"):
                    timing["vae_ms"] += float(line.split("Decode:")[1].split()[0])
            except ValueError:
                pass
            if line and not line.startswith("ggml_metal"):
                state["tail"] = (state["tail"] + [line])[-25:]
            for key, p in _ACE_STAGES:
                if key in line:
                    state["pct"] = max(state["pct"], p)
                    if key == "Load VAE":
                        state["t_vae"] = time.time()
            if line.startswith("[DiT] Step"):
                try:
                    k, n = line.split()[2].split("/")
                    state["pct"] = max(state["pct"], 26 + 30 * int(k) / int(n))
                except Exception:
                    pass

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    try:
        while proc.poll() is None:
            if time.time() - t_start > timeout:
                proc.kill()
                raise RuntimeError(f"ace-synth excedio {timeout:.0f} s")
            p = state["pct"]
            if state["t_vae"] and p < 90:  # el VAE no reporta: estimar por tiempo
                p = max(p, 58 + 30 * min(1.0, (time.time() - state["t_vae"]) / est_vae))
            _pct(on_pct, p)
            time.sleep(0.25)
    except MusicCancelled:
        proc.kill()
        proc.wait()
        raise
    th.join(timeout=2)
    if proc.returncode != 0:
        raise RuntimeError("ace-synth rc=%s: %s" % (proc.returncode, " | ".join(state["tail"][-4:])))
    LAST_ACE_TIMING.clear()
    LAST_ACE_TIMING.update({k: round(v / 1000, 2) for k, v in timing.items()},
                           wall_s=round(time.time() - t_start, 2), via="cli")
    outs = sorted(work.glob("req*.wav"), key=lambda p: p.stat().st_mtime)
    if not outs:
        raise RuntimeError("ace-synth no escribio audio: " + " | ".join(state["tail"][-3:]))
    return outs[-1]


def _ace_server(url: str, req: dict, work: Path, on_pct: Callable | None, timeout: float) -> Path:
    """Genera via ace-server (modelo ya cargado). POST /synth -> id; GET /job hasta done; result."""
    import requests

    url = url.rstrip("/")
    r = requests.post(url + "/synth", json=req, timeout=10)
    r.raise_for_status()
    jid = r.json()["id"]
    t0 = time.time()
    est = 3.0 + 0.3 * float(req["duration"])
    try:
        while True:
            st = requests.get(url + "/job", params={"id": jid}, timeout=10).json().get("status")
            if st == "done":
                break
            if st in ("failed", "cancelled"):
                raise RuntimeError(f"ace-server job {st}")
            if time.time() - t0 > timeout:
                requests.post(url + "/job", params={"id": jid, "cancel": 1}, timeout=5)
                raise RuntimeError("ace-server timeout")
            _pct(on_pct, 5 + 85 * min(1.0, (time.time() - t0) / est))
            time.sleep(0.3)
    except MusicCancelled:
        try:  # avisar al servidor; si no responde, la cancelacion igual se propaga
            requests.post(url + "/job", params={"id": jid, "cancel": 1}, timeout=5)
        except Exception:
            pass
        raise
    res = requests.get(url + "/job", params={"id": jid, "result": 1}, timeout=60)
    res.raise_for_status()
    ctype = res.headers.get("Content-Type", "")
    body = res.content
    audio = None
    if "multipart" in ctype and "boundary=" in ctype:
        bnd = ctype.split("boundary=")[1].split(";")[0].strip().strip('"').encode()
        for part in body.split(b"--" + bnd):
            head, _, data = part.partition(b"\r\n\r\n")
            if b"audio/" in head.lower():
                audio = data[:-2] if data.endswith(b"\r\n") else data
                break
    elif ctype.startswith("audio/"):
        audio = body
    if not audio:
        raise RuntimeError("ace-server sin parte de audio")
    work.mkdir(parents=True, exist_ok=True)
    p = work / "req0.wav"
    p.write_bytes(audio)
    return p


def _ace_generate(prompt: dict, gen_dur: float, seed: int, work: Path, on_pct: Callable | None) -> Path:
    req = ace_request(prompt, gen_dur, seed)
    timeout = 180 + 6 * gen_dur
    url = os.environ.get("REELFY_ACE_URL")
    if url:
        try:
            return _ace_server(url, req, work, on_pct, timeout)
        except MusicCancelled:
            raise
        except Exception as e:
            print(f"   music: ace-server no disponible ({e}); uso ace-synth")
    return _ace_cli(req, work, on_pct, timeout)


GEN_BUCKETS = (30.0, 45.0, 60.0, 90.0, 120.0, 150.0, 180.0)


def gen_duration(D: float) -> float:
    """Duracion a pedirle a ACE: ~+20% +4 s (ACE remata ~13% antes), en cubetas amplias (el costo
    lo dominan la carga de modelos y el VAE). Tope 180 s: videos mas largos se extienden con loops
    en compas. Ademas, un crudo existente se reusa para cualquier video con D <= parte util / 0.75
    (ver _pick_raw): recortar el video en el Studio no regenera ni cambia la cancion."""
    need = D * 1.2 + 4.0
    return float(next((b for b in GEN_BUCKETS if b >= need), GEN_BUCKETS[-1]))


def _raw_quality(ana: dict, bpm: float, gen_dur: float) -> tuple[float, list[str]]:
    """Puntaje 0..1 de un crudo de ACE y problemas detectados."""
    probs = []
    s = 1.0
    b = ana.get("bpm") or 0
    if not b or min(abs(b / bpm - r) for r in (0.5, 1.0, 2.0)) > 0.04:
        s -= 0.4
        probs.append(f"tempo {b:.1f} vs {bpm:.0f}")
    length = ana["end"] - ana["start"]
    if length < 0.6 * gen_dur:
        s -= 0.3
        probs.append(f"corta {length:.1f}s")
    long_sil = [r for r in ana.get("silences", []) if r[1] - r[0] > 1.0]
    if long_sil:
        s -= 0.3
        probs.append(f"silencios {long_sil[:2]}")
    if ana.get("grid") != "dp":
        s -= 0.1
        probs.append("sin pulso claro")
    return max(0.0, s), probs


# ---------------------------------------------------------------- generador procedural
def _mf(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12.0)


_NOTE = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5, "F#": 6, "GB": 6,
         "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}
_QUAL = {"maj": (0, 4, 7), "min": (0, 3, 7), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10),
         "dom7": (0, 4, 7, 10), "maj9": (0, 4, 7, 11, 14), "m9": (0, 3, 7, 10, 14),
         "dom9": (0, 4, 7, 10, 14), "sus2": (0, 2, 7), "sus4": (0, 5, 7), "add9": (0, 4, 7, 14)}
_PROGS = {
    "pop": [(0, "maj"), (7, "maj"), (9, "min"), (5, "maj")],          # I V vi IV
    "inspiring": [(9, "min"), (5, "maj"), (0, "maj"), (7, "maj")],    # vi IV I V
    "corporate": [(0, "add9"), (9, "m7"), (5, "maj7"), (7, "sus4")],  # I vi IV V
    "playful": [(0, "maj"), (4, "min"), (5, "maj"), (7, "maj")],      # I iii IV V
    "lofi": [(2, "m9"), (7, "dom9"), (0, "maj9"), (9, "m9")],         # ii V I vi
    "elegant": [(0, "maj7"), (4, "m7"), (9, "m7"), (5, "maj7")],      # I iii vi IV
    "minor": [(0, "min"), (8, "maj"), (3, "maj"), (10, "maj")],       # i VI III VII
    "minor_edm": [(0, "min"), (8, "maj"), (10, "maj"), (7, "min")],   # i VI VII v
}
_MINOR_PROGS = ("minor", "minor_edm")
_GENRES = {
    "pop": {"prog": "pop", "kick": "pop", "snare": "clap", "hats": "8th", "bass": "pop8",
            "chords": "pluck", "pad": "saw", "lead": "pluck8", "side": 0.35, "rt60": 1.4, "swing": 0.5,
            "intro": 0, "mix": {"kick": 0, "snare": -3.5, "hats": -12, "bass": -3, "chords": -7,
                                  "pad": -13, "lead": -8.5, "fx": -9, "perc": -15}},
    "edm": {"prog": "minor_edm", "kick": "edm", "snare": "clap", "hats": "offbeat", "bass": "offbeat",
            "chords": "stab", "pad": "saw", "lead": "pluck16", "side": 0.6, "rt60": 1.6, "swing": 0.5,
            "intro": 0, "mix": {"kick": 0, "snare": -4, "hats": -10, "bass": -3, "chords": -6,
                                  "pad": -12, "lead": -8, "fx": -8, "perc": -14}},
    "piano": {"prog": "elegant", "kick": "soft", "snare": "rim", "hats": "shaker", "bass": "sustain",
              "chords": "piano", "pad": "strings", "lead": "piano_arp", "side": 0.0, "rt60": 2.2,
              "swing": 0.52, "intro": 2, "mix": {"kick": -6, "snare": -12, "hats": -15, "bass": -6,
                                                    "chords": 0, "pad": -5, "lead": -4, "fx": -10, "perc": -16}},
    "lofi": {"prog": "lofi", "kick": "lofi", "snare": "lofi", "hats": "swing", "bass": "sustain",
             "chords": "epiano", "pad": None, "lead": "bell", "side": 0.15, "rt60": 1.2, "swing": 0.6,
             "intro": 2, "mix": {"kick": 0, "snare": -3, "hats": -13, "bass": -4, "chords": -2.5,
                                   "pad": -12, "lead": -9, "fx": -12, "perc": -22}},
    "cinematic": {"prog": "minor", "kick": "tom", "snare": None, "hats": None, "bass": "sustain",
                  "chords": "strings", "pad": "strings", "lead": "piano_arp", "side": 0.0, "rt60": 2.6,
                  "swing": 0.5, "intro": 2, "mix": {"kick": 0, "snare": -8, "hats": -14, "bass": -4,
                                                      "chords": -2, "pad": -6, "lead": -5, "fx": -7, "perc": -7}},
    # trap: medio tiempo (clap en el 3), hats en 16avos con redobles, 808 largo que sigue al bombo
    "trap": {"prog": "minor_edm", "kick": "trap", "snare": "clap_half", "hats": "trap", "bass": "808",
             "chords": "pluck", "pad": "saw", "lead": "bell", "side": 0.25, "rt60": 1.5, "swing": 0.5,
             "intro": 0, "mix": {"kick": 0, "snare": -3, "hats": -10.5, "bass": -1.5, "chords": -8,
                                   "pad": -12, "lead": -9, "fx": -8, "perc": -14}},
    # ambient/minimal: pulsos de pluck en corcheas, pads amplios, bombo suave
    "ambient": {"prog": "corporate", "kick": "soft", "snare": "rim", "hats": "shaker", "bass": "sustain",
                "chords": "pulse", "pad": "saw", "lead": "bell", "side": 0.3, "rt60": 2.0, "swing": 0.5,
                "intro": 1, "mix": {"kick": -1, "snare": -10, "hats": -13, "bass": -4, "chords": -4,
                                      "pad": -7, "lead": -10, "fx": -10, "perc": -16}},
}
_SOS_CACHE: dict = {}


def _sos(kind: str, f: float | tuple, sr: int, order: int = 2) -> np.ndarray:
    key = (kind, f, sr, order)
    s = _SOS_CACHE.get(key)
    if s is None:
        ny = sr / 2
        if kind == "bp":
            wn = (max(10.0, f[0]) / ny, min(0.99 * ny, f[1]) / ny)
            s = signal.butter(order, wn, btype="band", output="sos")
        else:
            s = signal.butter(order, min(0.99 * ny, max(10.0, f)) / ny,
                              btype="low" if kind == "lp" else "high", output="sos")
        _SOS_CACHE[key] = s
    return s


def _saw(f, n: int, sr: int, phase0: float = 0.0) -> np.ndarray:
    """Sierra polyBLEP (sin aliasing audible). `f` escalar o arreglo (vibrato)."""
    if np.isscalar(f):
        dt = np.full(n, f / sr)
        ph = (phase0 + (f / sr) * np.arange(n)) % 1.0
    else:
        dt = np.asarray(f) / sr
        ph = (phase0 + np.cumsum(dt)) % 1.0
    y = 2.0 * ph - 1.0
    m = ph < dt
    tt = ph[m] / dt[m]
    y[m] -= tt + tt - tt * tt - 1.0
    m = ph > 1.0 - dt
    tt = (ph[m] - 1.0) / dt[m]
    y[m] -= tt * tt + tt + tt + 1.0
    return y


def _adsr(n: int, sr: int, gate: float, a: float = 0.005, d: float = 0.2, s: float = 0.7,
          r: float = 0.08) -> np.ndarray:
    t = np.arange(n) / sr
    tc = np.minimum(t, gate)
    e = np.minimum(1.0, tc / max(a, 1e-4)) * (s + (1 - s) * np.exp(-np.maximum(0.0, tc - a) / max(d, 1e-4)))
    rel = np.where(t < gate, 1.0, np.exp(-(t - gate) / max(r, 1e-4)))
    return e * rel


class _Synth:
    """Instrumentos sintetizados con cache de notas identicas (misma altura y duracion)."""

    def __init__(self, sr: int, rng: np.random.Generator, genre: str, bright: float = 1.0):
        self.sr, self.rng, self.genre, self.bright = sr, rng, genre, bright
        self._c: dict = {}
        self.drums = self._make_drums()

    def _noise(self, n: int) -> np.ndarray:
        return self.rng.standard_normal(n)

    # ---- bateria (una muestra por pieza; variacion por velocidad al colocar)
    def _make_drums(self) -> dict:
        sr, g = self.sr, self.genre
        d = {}
        # bombo
        n = int(0.5 * sr)
        t = np.arange(n) / sr
        f_end, f_amt, dec, click = {"pop": (54, 110, 0.26, 0.22), "edm": (52, 135, 0.30, 0.28),
                                    "soft": (58, 70, 0.22, 0.05), "lofi": (55, 90, 0.20, 0.10),
                                    "tom": (62, 45, 0.55, 0.03), "trap": (58, 150, 0.16, 0.3)}.get(
            _GENRES[g]["kick"] if g in _GENRES else "pop", (48, 115, 0.30, 0.2))
        f = f_end + f_amt * np.exp(-t / 0.032)
        body = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t / dec)
        cl = signal.sosfilt(_sos("bp", (1800.0, 7000.0), sr), self._noise(n)) * np.exp(-t / 0.004) * click
        k = np.tanh(1.7 * (body + cl)) / np.tanh(1.7)
        k[: int(0.0008 * sr)] *= np.linspace(0, 1, int(0.0008 * sr))
        if _GENRES.get(g, {}).get("kick") == "tom":
            k += 0.4 * signal.sosfilt(_sos("lp", 900.0, sr), self._noise(n)) * np.exp(-t / 0.07)
        d["kick"] = (k / np.abs(k).max()).astype(np.float32)
        # caja / clap / rim
        n = int(0.45 * sr)
        t = np.arange(n) / sr
        nz = signal.sosfilt(_sos("bp", (1200.0, 9000.0), sr), self._noise(n))
        tone = 0.6 * np.sin(2 * np.pi * 185 * t) * np.exp(-t / 0.05) + 0.3 * np.sin(2 * np.pi * 330 * t) * np.exp(-t / 0.03)
        sn = tone + 1.1 * nz * np.exp(-t / 0.13)
        if g == "lofi":
            sn = signal.sosfilt(_sos("lp", 4500.0, sr), sn)
        d["snare"] = (sn / np.abs(sn).max()).astype(np.float32)
        nzc = signal.sosfilt(_sos("bp", (900.0, 5200.0), sr, 2), self._noise(n))
        env = np.zeros(n)
        for i in range(3):
            t0 = 0.0105 * i
            env += (t >= t0) * np.exp(-np.maximum(0, t - t0) / 0.006) * (0.8 + 0.1 * i)
        env += (t >= 0.031) * np.exp(-np.maximum(0, t - 0.031) / 0.15) * 0.85
        cp = nzc * env
        d["clap"] = (cp / np.abs(cp).max()).astype(np.float32)
        n2 = int(0.12 * sr)
        t2 = np.arange(n2) / sr
        rim = signal.sosfilt(_sos("bp", (1700.0, 5500.0), sr), self._noise(n2)) * np.exp(-t2 / 0.018) \
            + 0.5 * np.sin(2 * np.pi * 1750 * t2) * np.exp(-t2 / 0.012)
        d["rim"] = (rim / np.abs(rim).max()).astype(np.float32)
        # hats (metalico 808 + ruido)
        for name, dur, dec, hp in (("hat", 0.14, 0.032, 7000.0), ("ohat", 0.6, 0.26, 6200.0)):
            n = int(dur * sr)
            t = np.arange(n) / sr
            met = sum(np.sign(np.sin(2 * np.pi * fr * 1.37 * t + i)) for i, fr in
                      enumerate((205.3, 304.4, 369.6, 522.7, 540.0, 800.0)))
            h = signal.sosfilt(_sos("hp", hp, sr, 3), 0.6 * met / 6 + 0.8 * self._noise(n)) * np.exp(-t / dec)
            h[: int(0.001 * sr)] *= np.linspace(0, 1, int(0.001 * sr))
            d[name] = (h / np.abs(h).max()).astype(np.float32)
        n = int(0.16 * sr)
        t = np.arange(n) / sr
        sh = signal.sosfilt(_sos("bp", (4500.0, 11000.0), sr), self._noise(n)) * \
            np.minimum(1, t / 0.014) * np.exp(-np.maximum(0, t - 0.014) / 0.045)
        d["shaker"] = (sh / np.abs(sh).max()).astype(np.float32)
        # crash (estereo decorrelado)
        n = int(2.6 * sr)
        t = np.arange(n) / sr
        cr = np.stack([signal.sosfilt(_sos("hp", 3800.0, sr, 2), self._noise(n)) for _ in range(2)], axis=1)
        cr *= (np.exp(-t / 1.1) * np.minimum(1, t / 0.002))[:, None]
        d["crash"] = (cr / np.abs(cr).max()).astype(np.float32)
        # impacto grave (sub drop) para entradas de drop
        n = int(1.4 * sr)
        t = np.arange(n) / sr
        fsub = 34 + 50 * np.exp(-t / 0.18)
        sub = np.sin(2 * np.pi * np.cumsum(fsub) / sr) * np.exp(-t / 0.55)
        d["sub"] = (sub / np.abs(sub).max()).astype(np.float32)
        return d

    def riser(self, dur: float) -> np.ndarray:
        """Riser de ruido: banda que sube 250 Hz -> 9 kHz con crescendo (STFT, estereo)."""
        sr = self.sr
        n = int(dur * sr)
        out = np.zeros((n, 2), np.float32)
        for c in range(2):
            x = self._noise(n)
            f, tt, Z = signal.stft(x, fs=sr, nperseg=2048)
            prog = np.clip(tt / dur, 0, 1)
            fc = 250 * (9000 / 250) ** (prog ** 1.4)
            lf = np.log2(np.maximum(f, 20))[:, None]
            mask = np.exp(-0.5 * ((lf - np.log2(fc)[None, :]) / 0.9) ** 2)
            _, y = signal.istft(Z * mask, fs=sr, nperseg=2048)
            y = y[:n]
            out[: len(y), c] = y
        t = np.arange(n) / sr
        env = (t / dur) ** 2.2
        env[-int(0.02 * sr):] *= np.linspace(1, 0, int(0.02 * sr))
        out *= env[:, None]
        return out / (np.abs(out).max() + 1e-9)

    # ---- tonales
    def note(self, inst: str, m: float, dur: float, vel: float = 1.0) -> np.ndarray:
        key = (inst, round(m, 2), round(dur, 3))
        s = self._c.get(key)
        if s is None:
            s = getattr(self, "_" + inst)(_mf(m), dur).astype(np.float32)
            self._c[key] = s
        return s * vel

    def _pluck(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int((dur + 0.5) * sr)
        t = np.arange(n) / sr
        s = 0.5 * (_saw(f * 2 ** (-6 / 1200), n, sr) + _saw(f * 2 ** (6 / 1200), n, sr, 0.37))
        s += 0.25 * np.sin(2 * np.pi * f * t)
        br = signal.sosfilt(_sos("lp", min(9000.0, 6500.0 * self.bright), sr), s)
        dk = signal.sosfilt(_sos("lp", max(600.0, f * 2.5), sr), s)
        a = np.exp(-t / 0.07)
        y = a * br + (1 - a) * dk
        return y * _adsr(n, sr, dur, a=0.002, d=0.25, s=0.25, r=0.12)

    def _stab(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int((dur + 0.25) * sr)
        s = sum(_saw(f * 2 ** (c / 1200), n, sr, p) for c, p in ((-11, 0.1), (0, 0.5), (11, 0.8)))
        s = signal.sosfilt(_sos("lp", 3800.0 * self.bright, sr), s / 3)
        return s * _adsr(n, sr, dur, a=0.004, d=0.18, s=0.6, r=0.07)

    def _padsaw(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int((dur + 0.7) * sr)
        s = sum(_saw(f * 2 ** (c / 1200), n, sr, p) for c, p in ((-9, 0.2), (-3, 0.6), (4, 0.1), (10, 0.9)))
        s = signal.sosfilt(_sos("lp", 1600.0 * self.bright, sr, 2), s / 4)
        return s * _adsr(n, sr, dur, a=0.35, d=0.6, s=0.85, r=0.6)

    def _strings(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int((dur + 0.9) * sr)
        t = np.arange(n) / sr
        vib = f * (1 + 0.0022 * np.sin(2 * np.pi * 5.2 * t) * np.minimum(1, t / 0.6))
        s = sum(_saw(vib * 2 ** (c / 1200), n, sr, p) for c, p in ((-7, 0.3), (0, 0.7), (7, 0.1)))
        s = signal.sosfilt(_sos("lp", 2400.0, sr, 2), s / 3)
        s = signal.sosfilt(_sos("hp", 180.0, sr, 1), s)
        return s * _adsr(n, sr, dur, a=0.45, d=0.8, s=0.9, r=0.8)

    def _piano(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int((dur + 0.6) * sr)
        t = np.arange(n) / sr
        B = 0.00032
        T1 = float(np.clip(3.2 * (220.0 / f) ** 0.5, 0.7, 4.0))
        y = np.zeros(n)
        for k in range(1, 11):
            fk = k * f * math.sqrt(1 + B * k * k)
            if fk > 12000:
                break
            amp = 1.0 / k ** 1.15
            tau = T1 / (1 + 0.55 * (k - 1))
            env = 0.55 * np.exp(-t / (tau * 0.18)) + 0.45 * np.exp(-t / tau)
            y += amp * env * (np.sin(2 * np.pi * fk * t) + np.sin(2 * np.pi * fk * 1.0006 * t + 0.4)) * 0.5
        y += 0.05 * signal.sosfilt(_sos("lp", 2500.0, sr), self._noise(n)) * np.exp(-t / 0.006)
        y *= np.minimum(1, t / 0.002)
        y *= np.where(t < dur, 1.0, np.exp(-(t - dur) / 0.14))
        return y

    def _epiano(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int((dur + 0.5) * sr)
        t = np.arange(n) / sr
        idx = 1.6 * np.exp(-t / 0.4) + 0.25
        body = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
        tine = np.sin(2 * np.pi * f * t + 0.9 * np.exp(-t / 0.05) * np.sin(2 * np.pi * 14 * f * t))
        y = body + 0.18 * tine * np.exp(-t / 0.25)
        y *= np.exp(-t / 1.8) * np.minimum(1, t / 0.003)
        y *= np.where(t < dur, 1.0, np.exp(-(t - dur) / 0.12))
        y *= 1 + 0.12 * np.sin(2 * np.pi * 4.6 * t)
        return y

    def _mallet(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int(0.9 * sr)
        t = np.arange(n) / sr
        y = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.42) + 0.3 * np.sin(2 * np.pi * 3.93 * f * t) * np.exp(-t / 0.06) \
            + 0.1 * np.sin(2 * np.pi * 9.4 * f * t) * np.exp(-t / 0.02)
        return y * np.minimum(1, t / 0.001)

    def _bell(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int(1.8 * sr)
        t = np.arange(n) / sr
        y = sum(a * np.sin(2 * np.pi * r * f * t) * np.exp(-t / d) for r, a, d in
                ((1.0, 1.0, 1.1), (2.76, 0.45, 0.5), (5.40, 0.22, 0.25), (8.93, 0.1, 0.12)))
        return y * np.minimum(1, t / 0.0015)

    def _bass(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int((dur + 0.12) * sr)
        t = np.arange(n) / sr
        s = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t + 0.3)
        s += 0.28 * signal.sosfilt(_sos("lp", 750.0, sr), _saw(f, n, sr))
        s = np.tanh(1.4 * s)
        s = signal.sosfilt(_sos("lp", 1400.0, sr), s)
        return s * _adsr(n, sr, dur, a=0.004, d=0.3, s=0.8, r=0.05)

    def _bassub(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int((dur + 0.3) * sr)
        t = np.arange(n) / sr
        s = np.tanh(1.2 * (np.sin(2 * np.pi * f * t) + 0.12 * np.sin(4 * np.pi * f * t)))
        return s * _adsr(n, sr, dur, a=0.015, d=0.6, s=0.85, r=0.2)

    def _bass808(self, f: float, dur: float) -> np.ndarray:
        """808: seno con caida de afinacion en el ataque y saturacion suave (sub largo)."""
        sr = self.sr
        n = int((dur + 0.15) * sr)
        t = np.arange(n) / sr
        fi = f * (1 + 0.6 * np.exp(-t / 0.025))
        ph = 2 * np.pi * np.cumsum(fi) / sr
        s = np.tanh(1.8 * (np.sin(ph) + 0.08 * np.sin(2 * ph)))
        s += 0.12 * signal.sosfilt(_sos("bp", (900.0, 3000.0), sr), self._noise(n)) * np.exp(-t / 0.004)
        return s * _adsr(n, sr, dur, a=0.002, d=0.5, s=0.75, r=0.1)

    def _basspluck(self, f: float, dur: float) -> np.ndarray:
        sr = self.sr
        n = int((dur + 0.1) * sr)
        t = np.arange(n) / sr
        s = _saw(f, n, sr) + 0.6 * np.sin(2 * np.pi * f * t)
        br = signal.sosfilt(_sos("lp", 1800.0, sr), s)
        dk = signal.sosfilt(_sos("lp", 380.0, sr), s)
        a = np.exp(-t / 0.05)
        return np.tanh(1.3 * (a * br + (1 - a) * dk)) * _adsr(n, sr, dur, a=0.002, d=0.15, s=0.6, r=0.04)


def _parse_key(keyscale: str) -> tuple[int, bool]:
    parts = (keyscale or "A major").replace("-", " ").split()
    root = _NOTE.get(parts[0].upper().replace("♯", "#"), 9)
    minor = len(parts) > 1 and parts[1].lower().startswith("min")
    return root, minor


def _voicings(pcs: list[int], lo: int = 55, hi: int = 77) -> list[list[int]]:
    """Todas las inversiones del acorde (clases de altura) dentro de [lo, hi]."""
    out = []
    n = len(pcs)
    for inv in range(n):
        rot = pcs[inv:] + [p + 12 for p in pcs[:inv]]
        for base in range(lo - 12, hi):
            if (base - rot[0]) % 12:
                continue
            v = [base + (p - rot[0]) for p in rot]
            v2, last = [], -99
            for m in v:
                while m <= last:
                    m += 12
                v2.append(m)
                last = m
            if v2[0] >= lo and v2[-1] <= hi:
                out.append(v2)
    return out


def _voice_lead(chords: list[list[int]], lo: int = 55, hi: int = 77) -> list[list[int]]:
    prev, res = None, []
    for pcs in chords:
        cands = _voicings(pcs, lo, hi) or [[lo + (p % 12) for p in pcs]]
        if prev is None:
            center = (lo + hi) / 2 - 2
            best = min(cands, key=lambda v: abs(np.mean(v) - center))
        else:
            best = min(cands, key=lambda v: sum(min(abs(a - b) for b in prev) for a in v) + 0.3 * abs(np.mean(v) - np.mean(prev)))
        res.append(best)
        prev = best
    return res


def _place(bus: np.ndarray, sig: np.ndarray, t: float, sr: int, gain: float = 1.0, pan: float = 0.0) -> None:
    i = int(round(t * sr))
    if i >= len(bus) or gain == 0:
        return
    if i < 0:
        sig = sig[-i:]
        i = 0
    m = min(len(sig), len(bus) - i)
    if m <= 0:
        return
    if sig.ndim == 1:
        a = math.pi / 4 * (pan + 1)
        bus[i:i + m, 0] += sig[:m] * (gain * math.cos(a) * 1.414)
        bus[i:i + m, 1] += sig[:m] * (gain * math.sin(a) * 1.414)
    else:
        bus[i:i + m] += sig[:m] * gain


def _genre_for(style: str, mood: str, genre: str | None = None) -> str:
    g = genre if genre in _GENRES else STYLE_MUSIC.get(style, STYLE_MUSIC["dinamico"])["genre"]
    if mood == "dramatic" and g == "piano":
        return "cinematic"
    return g


def _prog_for(genre: str, mood: str) -> str:
    base = _GENRES[genre]["prog"]
    if mood == "dramatic":
        return "minor" if genre not in ("edm", "trap") else "minor_edm"
    if mood in ("inspiring", "corporate", "playful"):
        return {"inspiring": "inspiring", "corporate": "corporate", "playful": "playful"}[mood]
    if mood == "upbeat" and base in _MINOR_PROGS:
        return "inspiring"
    if mood == "chill" and genre == "pop":
        return "lofi"
    return base


def synth_procedural(duration: float, style: str = "dinamico", mood: str | None = None,
                     bpm: float | None = None, keyscale: str | None = None, seed: int = 7,
                     drop_at: float | None = None, sr: int = SR,
                     on_pct: Callable | None = None, genre: str | None = None, ending: str = "auto",
                     tail: float = 1.2, stops=None, hook: bool | None = None,
                     drop_tol: float = 0.0) -> tuple[np.ndarray, dict]:
    """Genera una pista instrumental de largo EXACTO `duration`.

    Devuelve (audio (n,2) float32 sin normalizar, info con bpm/beats/downbeats/secciones/final).
    Estructura: pickup (anacrusa) -> [hook] -> intro -> verso -> build (riser) -> drop -> verso2
    -> drop ... y la ultima seccion antes del final siempre es drop (energia en el CTA).
      genre:  fuerza el genero (pop|edm|trap|piano|lofi|ambient|cinematic), p. ej. el que nombra
              el prompt libre; por defecto sale del estilo y el animo.
      hook:   (default: dinamico/energetico) los primeros compases suenan a nivel de drop (hasta
              el drop pedido o el primer stop): el video arranca con energia.
      drop_at: s (tiempo final) donde entra el drop: cae exacto en un downbeat (si hace falta, la
              rejilla se desplaza una fraccion de beat; el tempo no cambia). drop_tol: tolerancia.
      ending: auto/natural = remate con acorde que resuena ~0.3-0.9 s antes del final;
              hit = golpe seco en el downbeat mas cercano a D - tail + cola de reverb;
              fade = desvanecido musical desde el downbeat mas cercano a D - tail.
      stops:  [(at, dur)] (los silencia audio.py): el hook dura hasta el primer stop.
    """
    mood = mood if mood in MOOD_MUSIC else STYLE_DEFAULT_MOOD.get(style, "upbeat")
    pr = build_prompt(style, mood, bpm=bpm, seed=seed, keyscale=keyscale)
    bpm = pr["bpm"]
    genre = _genre_for(pr["style"], mood, genre)
    G = _GENRES[genre]
    if hook is None:
        hook = pr["style"] in EARLY_DROP_STYLES
    emode = ending if ending in ("hit", "fade") else "natural"
    tail = float(np.clip(tail if tail is not None else 1.2, 0.45, 3.0))
    rng = np.random.default_rng((int(seed) * 7919 + sum(map(ord, genre + mood))) & 0xFFFFFFFF)
    bright = {"chill": 0.7, "dramatic": 0.85, "upbeat": 1.1, "playful": 1.05}.get(mood, 1.0)
    syn = _Synth(sr, rng, genre, bright)
    prog_name = _prog_for(genre, mood)
    root, minor = _parse_key(pr["keyscale"])
    want_minor = prog_name in _MINOR_PROGS
    if want_minor and not minor:
        root = (root + 9) % 12
    elif minor and not want_minor:
        root = (root + 3) % 12
    prog = _PROGS[prog_name]

    D = float(duration)
    N = int(round(D * sr))
    beat = 60.0 / bpm
    bar = 4 * beat
    step = beat / 4

    def grid(delta: float, pick_fixed: int | None) -> tuple[int, int, float, int, float]:
        """(nbeats, pick, t_db0, nbars, t_hit) con la rejilla de beats en delta + i*beat."""
        if emode == "natural":
            nb = max(0, int(math.floor((D - 0.3 - delta) / beat)))
            if nb and delta + nb * beat > D - 0.25:
                nb -= 1                    # con 0 beats: un solo golpe al inicio (stinger)
        else:                              # el golpe/fade cae en el downbeat mas cercano a D - tail
            nb = max(0, int(round((D - tail - delta) / beat)))
        pk = nb % 4 if pick_fixed is None else pick_fixed
        if pick_fixed is not None:         # el golpe final tambien en downbeat de esa rejilla
            nb = pk + 4 * max(0, (nb - pk) // 4 if emode == "natural" else int(round((nb - pk) / 4)))
        while nb > pk and D - (delta + nb * beat) < (0.25 if emode == "natural" else 0.45):
            nb -= 4 if pick_fixed is not None else 1
            pk = nb % 4 if pick_fixed is None else pk
        nb = max(nb, pk)
        return nb, pk, delta + pk * beat, (nb - pk) // 4, delta + pk * beat + ((nb - pk) // 4) * bar

    delta = 0.0
    nbeats, pick, t_db0, nbars, t_hit = grid(0.0, None)
    if drop_at is not None and 0.0 <= drop_at < D - 3.0:
        # el drop pedido debe caer EXACTO en un downbeat (p. ej. justo al volver de un stop): si la
        # rejilla natural no lo deja dentro de la tolerancia, se desplaza la rejilla (la anacrusa
        # dura una fraccion de beat) sin cambiar el tempo
        dtol = max(float(drop_tol or 0.0), 0.02)
        k_ok = [t_db0 + k * bar for k in range(0, nbars + 1)]
        if not any(abs(t - drop_at) <= dtol for t in k_ok):
            j = int(math.floor(drop_at / beat + 1e-9))
            delta = drop_at - j * beat
            nbeats, pick, t_db0, nbars, t_hit = grid(delta, j % 4)
    n_after = int(math.ceil((D - t_hit) / bar)) if emode != "natural" else 0  # musica tras el golpe

    # arreglo por compas
    st_ = _norm_stops(stops, D)
    intro = 0 if hook else min(G["intro"], max(0, nbars - 3))
    if drop_at is not None and 0.0 <= drop_at < D - 3.0:
        dbar = int(round((drop_at - t_db0) / bar))
        intro = min(intro, max(0, dbar - 1))       # un drop temprano acorta la intro
        drop_bar = int(np.clip(dbar, 0 if hook else intro + 1, max(intro + 1, nbars - 2)))
    else:
        drop_bar = min(intro + 4, max(intro + 1, nbars - 4))
    drop_bar = max(0, min(drop_bar, max(0, nbars - 1)))
    hook_bars = 0
    if hook and drop_bar > 0:
        hook_bars = drop_bar if drop_bar <= 2 else 2
        if st_ and st_[0][0] > t_db0:      # con stop temprano: el hook llega hasta el stop
            hook_bars = max(hook_bars, min(drop_bar, int(math.ceil((st_[0][0] - t_db0) / bar))))
    sec = []
    for b in range(nbars):
        if b < hook_bars:
            s = "hook"
        elif b < intro:
            s = "intro"
        elif b < drop_bar:
            s = "verse"
        else:
            k = (b - drop_bar) % 12
            s = "drop" if k < 8 else "verse2"
        sec.append(s)
    for b in range(nbars - 1):  # compas previo a cada drop = build (si hay algo que construir)
        if sec[b] in ("verse", "verse2") and sec[b + 1] == "drop" and (b == 0 or sec[b - 1] != "drop"):
            sec[b] = "build" if b >= intro else sec[b]
    k = nbars - 1                # la cancion no termina en un verso: la cola sube a drop
    while k >= 0 and sec[k] == "verse2":
        sec[k] = "drop"
        k -= 1
    sec += ["drop"] * n_after
    nb_all = nbars + n_after

    # voicing con voice leading, repitiendo la progresion (1 acorde por compas); la anacrusa
    # usa el ultimo acorde de la progresion (resuelve al primero en el downbeat)
    seq = [prog[-1]] + [prog[(b % len(prog))] for b in range(nb_all + 1)]
    pcs_seq = [[(root + deg + iv) for iv in _QUAL[q]] for deg, q in seq]
    vo_all = _voice_lead(pcs_seq, 55, 76)
    v_pick, vo = vo_all[0], vo_all[1:]
    bass_root = [31 + ((root + deg - 31) % 12) for deg, q in seq[1:]]   # G1..F#2

    def lead_tones(v: list[int]) -> list[int]:
        base = sorted({67 + ((p - 67) % 12) for p in v})                  # G4..F#5
        return base + [base[0] + 12, base[1] + 12] if len(base) > 1 else base + [base[0] + 12]

    stems = {k: np.zeros((N, 2), np.float32) for k in ("kick", "snare", "hats", "bass", "chords", "pad", "lead", "fx", "perc")}
    kicks: list[float] = []
    dr = syn.drums
    swing = G["swing"]

    def st(bar_t: float, s16: float) -> float:
        """Tiempo de un paso de 16avos con swing de corcheas (pasos 2, 6, 10, 14)."""
        q, r = divmod(s16, 4)
        off = 0.0
        if abs(r - 2) < 1e-6:
            off = (swing - 0.5) * 2 * beat
        return bar_t + q * beat + r * step + off

    def hv(v: float) -> float:
        return v * (1 + 0.07 * (rng.random() - 0.5))

    def drums_bar(bt: float, s: str, b: int) -> None:
        if s == "intro" and genre in ("pop", "edm", "trap"):
            for k in range(0, 16, 2):
                _place(stems["hats"], dr["hat"], st(bt, k), sr, hv(0.45 if k % 4 else 0.3), 0.25)
            return
        if s == "intro":
            if genre == "lofi":
                for k in range(0, 16, 2):
                    _place(stems["hats"], dr["hat"], st(bt, k), sr, hv(0.35), 0.2)
            return
        full = s in ("drop", "hook")
        kind = G["kick"]
        if kind in ("pop",):
            ks = [0, 8, 10] + ([14] if full and b % 2 else [])
        elif kind == "edm":
            ks = [0, 4, 8, 12]
        elif kind == "soft":
            ks = [0, 10] if full else [0]
        elif kind == "lofi":
            ks = [0, 7, 10]
        elif kind == "trap":
            ks = [0, 10] + (([7] if b % 2 else [14]) if full else [])
        else:  # tom (cinematico): pulso claro en negras con acento en 1 y 3
            ks = [0, 4, 8, 10, 12] if full else [0, 8]
        if s == "build" and kind in ("pop", "edm", "trap"):
            ks = [k for k in ks if k < 8]
        for k in ks:
            tk = st(bt, k)
            _place(stems["kick"], dr["kick"], tk, sr, hv(1.0 if k in (0, 8) else 0.85))
            kicks.append(tk)
        sn = G["snare"]
        if sn:
            smp = dr["clap" if sn in ("clap", "clap_half") else ("rim" if sn == "rim" else "snare")]
            for k in ((8,) if sn == "clap_half" else (4, 12)):
                _place(stems["snare"], smp, st(bt, k), sr, hv(1.0), 0.05)
            if s == "build" and sn in ("clap", "snare", "clap_half"):
                for i, k in enumerate(range(8, 16)):
                    _place(stems["snare"], dr["snare"], st(bt, k), sr, 0.25 + 0.6 * i / 7, -0.1)
        h = G["hats"]
        if h == "8th":
            for k in range(0, 16, 1 if full else 2):
                v = 0.55 if k % 4 == 2 else (0.35 if k % 2 == 0 else 0.2)
                _place(stems["hats"], dr["hat"], st(bt, k) + 0.003 * (rng.random() - 0.5), sr, hv(v), 0.25)
        elif h == "offbeat":
            for k in (2, 6, 10, 14):
                _place(stems["hats"], dr["ohat"], st(bt, k), sr, hv(0.6 if full else 0.4), 0.2)
            if full:
                for k in range(0, 16):
                    _place(stems["hats"], dr["hat"], st(bt, k), sr, hv(0.25 if k % 2 else 0.18), -0.25)
        elif h == "shaker":
            for k in range(0, 16, 2):
                _place(stems["hats"], dr["shaker"], st(bt, k), sr, hv(0.6 if k % 4 else 0.4), 0.3)
        elif h == "swing":
            for k in range(0, 16, 2):
                _place(stems["hats"], dr["hat"], st(bt, k) + 0.004 * (rng.random() - 0.5), sr,
                       hv(0.5 if k % 4 == 0 else 0.32), 0.2)
        elif h == "trap":  # 16avos con redoble de fusas en el ultimo tiempo (compases impares y build)
            roll = s == "build" or b % 2 == 1
            for k in range(0, 12 if roll else 16):
                _place(stems["hats"], dr["hat"], st(bt, k), sr, hv(0.42 if k % 4 == 0 else (0.3 if k % 2 == 0 else 0.22)), 0.2)
            if roll:
                for i in range(8):
                    _place(stems["hats"], dr["hat"], bt + 3 * beat + i * beat / 8, sr, 0.18 + 0.3 * i / 7, 0.2 - 0.05 * i)

    lead_pat = [0, 1, 2, 1, 2, 3, 2, 1]
    rng_rests = rng.random(64) < 0.18

    for b in range(nb_all):
        bt = t_db0 + b * bar
        s = sec[b]
        sx = "drop" if s == "hook" else s        # el hook suena como drop
        drums_bar(bt, s, b)
        v = vo[b]
        # bajo
        br_ = bass_root[b]
        bk = G["bass"]
        if s != "intro" or genre == "lofi":
            if s == "intro":
                pass
            elif bk == "pop8":
                pat = range(0, 16, 2) if sx == "drop" else (0, 4, 8, 12)
                ln = 1.7 * step if sx == "drop" else 3.4 * step
                for k in pat:
                    m = br_ + (12 if (sx == "drop" and k in (6, 14)) else 0)
                    _place(stems["bass"], syn.note("bass", m, ln), st(bt, k), sr, 0.95)
            elif bk == "offbeat":
                for k in (2, 6, 10, 14):
                    _place(stems["bass"], syn.note("basspluck", br_, 1.6 * step), st(bt, k), sr, 1.0)
                if sx == "drop":
                    for k in (3, 7, 11, 15):
                        _place(stems["bass"], syn.note("basspluck", br_ + 12, 0.8 * step), st(bt, k), sr, 0.5)
            elif bk == "808":  # sub largo que sigue al bombo
                pat = ((0, 7, 0), (7, 3, 12 if b % 4 == 3 else 0), (10, 6, 0)) if sx == "drop" else ((0, 10, 0), (10, 6, 0))
                for k, ln, dm in pat:
                    _place(stems["bass"], syn.note("bass808", br_ + dm, ln * step - 0.01), st(bt, k), sr, 1.0)
            else:  # sostenido (piano/lofi/cine)
                if genre == "lofi":
                    for k, ln, dm in ((0, 6, 0), (7, 3, 7), (10, 6, 0)):
                        _place(stems["bass"], syn.note("bassub", br_ + dm, ln * step), st(bt, k), sr, 0.9 if k == 0 else 0.7)
                else:
                    _place(stems["bass"], syn.note("bassub", br_, 2 * beat - 0.05), bt, sr, 0.9)
                    _place(stems["bass"], syn.note("bassub", br_ + (7 if genre != "cinematic" else 0), 2 * beat - 0.05),
                           bt + 2 * beat, sr, 0.75)
        # acordes
        ck = G["chords"]
        cv = 0.55 if s == "intro" else (1.0 if sx == "drop" else 0.85)
        if ck == "pluck":
            if mood == "playful":
                hits = [(0, 2), (3, 2), (6, 2), (10, 2), (12, 2)]
            else:
                hits = [(0, 5), (6, 5), (12, 3.5)] if s != "intro" else [(0, 14)]
            for k, ln in hits:
                for j, m in enumerate(v):
                    _place(stems["chords"], syn.note("pluck", m, ln * step), st(bt, k) + 0.004 * j, sr, cv * 0.5,
                           -0.35 + 0.7 * j / max(1, len(v) - 1))
        elif ck == "pulse":  # pulsos de acorde en corcheas (acento en el tiempo)
            hits = [(k, 1.7) for k in range(0, 16, 2)]
            for k, ln in hits:
                acc = 1.0 if k % 4 == 0 else 0.72
                for j, m in enumerate(v):
                    _place(stems["chords"], syn.note("pluck", m, ln * step), st(bt, k) + 0.003 * j, sr,
                           cv * 0.42 * acc, -0.35 + 0.7 * j / max(1, len(v) - 1))
        elif ck == "stab":
            hits = [(2, 1.5), (6, 1.5), (10, 1.5), (14, 1.5)] if s != "intro" else [(0, 14)]
            for k, ln in hits:
                for j, m in enumerate(v):
                    _place(stems["chords"], syn.note("stab", m, ln * step), st(bt, k), sr, cv * 0.45,
                           -0.4 + 0.8 * j / max(1, len(v) - 1))
        elif ck == "piano":
            hits = [(0, 7.5), (8, 7.5)] if s in ("drop", "verse2") else [(0, 15.5)]
            for k, ln in hits:
                for j, m in enumerate([v[0] - 12] + v):
                    _place(stems["chords"], syn.note("piano", m, ln * step), st(bt, k) + 0.012 * j, sr,
                           cv * (0.5 if j else 0.4), -0.3 + 0.6 * j / max(1, len(v)))
        elif ck == "epiano":
            for k, ln, vv in ((0, 9.5, 1.0), (10, 5.5, 0.7)):
                for j, m in enumerate(v):
                    _place(stems["chords"], syn.note("epiano", m, ln * step), st(bt, k) + 0.018 * j, sr,
                           cv * vv * 0.5, -0.3 + 0.6 * j / max(1, len(v) - 1))
        elif ck == "strings":
            for j, m in enumerate(v):
                _place(stems["chords"], syn.note("strings", m, bar - 0.02), bt, sr, cv * 0.5,
                       -0.5 + j / max(1, len(v) - 1))
            if s in ("verse", "build", "drop", "verse2"):  # ostinato de cuerdas graves en corcheas
                for i, k in enumerate(range(0, 16, 2)):
                    m = br_ + 12 + (7 if i % 4 == 3 else 0)
                    _place(stems["perc"], syn.note("stab", m, 1.3 * step), st(bt, k), sr,
                           0.55 if k % 8 == 0 else 0.4, 0.25 * (-1) ** i)
        # pad
        if G["pad"] and s != "intro" or (G["pad"] and genre in ("piano", "cinematic", "ambient")):
            inst = "strings" if G["pad"] == "strings" else "padsaw"
            if not (ck == "strings" and inst == "strings"):
                for j, m in enumerate(v):
                    _place(stems["pad"], syn.note(inst, m - (12 if inst == "strings" else 0), bar - 0.02), bt, sr,
                           0.4, -0.6 + 1.2 * j / max(1, len(v) - 1))
        # melodia/arpegio (solo en drops y hook)
        if sx == "drop":
            lk = G["lead"]
            inst = {"pluck8": "pluck", "pluck16": "pluck", "piano_arp": "piano", "bell": "bell"}[lk]
            if mood == "playful":
                inst = "mallet"
            tones = lead_tones(v)
            if lk in ("pluck8", "piano_arp"):
                for i, k in enumerate(range(0, 16, 2)):
                    if rng_rests[(b * 8 + i) % 64] and k:
                        continue
                    m = tones[lead_pat[i] % len(tones)]
                    _place(stems["lead"], syn.note(inst, m, 1.6 * step), st(bt, k), sr, 0.55 if k % 4 == 0 else 0.42,
                           0.3 * math.sin(i))
            elif lk == "pluck16":
                for i, k in enumerate(range(0, 16)):
                    m = tones[[0, 2, 1, 3, 2, 4, 3, 1][i % 8] % len(tones)]
                    _place(stems["lead"], syn.note(inst, m, 0.8 * step), st(bt, k), sr, 0.5 if k % 4 == 0 else 0.33,
                           0.35 * math.sin(i * 1.3))
            else:  # campanitas escasas (lofi)
                for i, k in enumerate((0, 6, 12)):
                    if (b + i) % 2 == 0:
                        _place(stems["lead"], syn.note(inst, tones[(b + 2 * i) % len(tones)], 2 * step), st(bt, k),
                               sr, 0.45, 0.4 * (-1) ** i)
            if mood in ("inspiring", "upbeat") and genre in ("pop", "piano"):
                for i, k in enumerate((0, 8)):
                    _place(stems["lead"], syn.note("bell", tones[(b + i) % len(tones)] + 12, 2 * step), st(bt, k), sr,
                           0.16, 0.5)
        # fx: riser antes de cada drop, crash + sub al entrar
        if s == "build" or (b + 1 < nb_all and sec[b + 1] == "drop" and s != "drop"):
            rl = bar if bar >= 1.6 else 2 * bar
            _place(stems["fx"], syn.riser(rl), bt + bar - rl, sr, 0.55)
        if (s == "drop" and (b == 0 or sec[b - 1] != "drop")) or (s == "hook" and b == 0):
            _place(stems["fx"], dr["crash"], bt, sr, 0.7)
            if genre in ("pop", "edm", "cinematic", "trap"):
                _place(stems["fx"], dr["sub"], bt, sr, 0.8)

    # anacrusa: ultimo acorde de la progresion + hats (nunca silencio al arrancar)
    if t_db0 > 0.03:
        inst = {"pulse": "pluck"}.get(G["chords"], G["chords"])
        if G["chords"] == "pulse":   # los pulsos arrancan desde la anacrusa
            for k in range(0, pick * 4, 2):
                for j, m in enumerate(v_pick):
                    _place(stems["chords"], syn.note("pluck", m, 1.7 * step), delta + k * step + 0.003 * j, sr,
                           0.36, -0.35 + 0.7 * j / 2)
        else:
            for j, m in enumerate(v_pick):
                _place(stems["chords"], syn.note(inst, m, t_db0 + 0.02), 0.0, sr, 0.42, -0.3 + 0.6 * j / 2)
        if G["pad"] and genre in ("ambient", "piano", "cinematic"):
            pinst = "strings" if G["pad"] == "strings" else "padsaw"
            for j, m in enumerate(v_pick):
                _place(stems["pad"], syn.note(pinst, m - (12 if pinst == "strings" else 0), t_db0 + 0.05), 0.0, sr,
                       0.4, -0.6 + 1.2 * j / 2)
        for k in range(pick * 4):
            if k % 2 == 0 and G["hats"]:
                _place(stems["hats"], dr["hat"] if G["hats"] != "shaker" else dr["shaker"], delta + k * step, sr, 0.3, 0.2)
        rl = min(t_db0, bar)
        if rl > 0.4 and genre in ("pop", "edm", "trap"):
            _place(stems["fx"], syn.riser(rl), t_db0 - rl, sr, 0.35 if not hook else 0.5)
        if hook and pick >= 2 and G["snare"] in ("clap", "snare", "clap_half"):  # redoble de entrada
            n_r = pick * 4
            for i in range(n_r):
                _place(stems["snare"], dr["snare"], delta + i * step, sr, 0.2 + 0.55 * i / max(1, n_r - 1), -0.1)

    # golpe final: bombo + crash (+ en "natural": acorde largo + bajo que resuenan hasta el final;
    # en hit/fade la musica sigue y _apply_ending la corta o la desvanece)
    ring = D - t_hit
    if emode != "fade":
        _place(stems["kick"], dr["kick"], t_hit, sr, 1.0)
        kicks.append(t_hit)
        _place(stems["fx"], dr["crash"], t_hit, sr, 0.75)
    if emode == "natural":
        fq = "min" if want_minor else {"lofi": "maj9", "elegant": "maj7", "corporate": "add9"}.get(prog_name, "maj")
        last_v = vo[nbars - 1] if nbars else v_pick
        vf = _voice_lead([[p for p in last_v], [root + iv for iv in _QUAL[fq]]], 55, 76)[1]
        fin_inst = {"pluck": "stab", "pulse": "stab", "stab": "stab", "piano": "piano", "epiano": "epiano",
                    "strings": "strings"}[G["chords"]]
        for j, m in enumerate([vf[0] - 12] + vf):
            _place(stems["chords"], syn.note(fin_inst, m, max(0.3, ring)), t_hit + 0.006 * j, sr, 0.5,
                   -0.3 + 0.6 * j / max(1, len(vf)))
        if genre in ("pop", "edm", "trap"):
            for j, m in enumerate(vf):
                _place(stems["lead"], syn.note("pluck", m + 12, 0.5), t_hit, sr, 0.35, -0.3 + 0.6 * j / max(1, len(vf) - 1))
        _place(stems["bass"], syn.note("bassub", 31 + ((root - 31) % 12), max(0.3, ring - 0.2)), t_hit, sr, 0.9)
    _pct(on_pct, 55)

    # sidechain (bombeo) en bajo/acordes/pad/lead
    side = G["side"]
    if side > 0 and kicks:
        tt = np.arange(N) / sr
        g = np.ones(N, np.float32)
        kt = np.sort(np.array(kicks))
        idx = np.searchsorted(kt, tt, side="right") - 1
        valid = idx >= 0
        dt = np.where(valid, tt - kt[np.maximum(idx, 0)], 9.0)
        env = np.where(dt < 0.004, dt / 0.004, np.exp(-np.maximum(0, dt - 0.004) / (0.33 * beat)))
        g = (1 - side * env).astype(np.float32)
        for k in ("bass", "chords", "pad", "lead"):
            stems[k] *= (g if k != "bass" else 1 - 0.6 * side * env.astype(np.float32))[:, None]

    # balance automatico: cada stem a su nivel relativo (medido donde suena)
    mix_t = G["mix"]
    ref = None
    for k in ("kick", "chords"):
        L = loudness_lufs(stems[k], sr, gated=True)
        if np.isfinite(L):
            ref = L - mix_t[k]
            break
    ref = -20.0 if ref is None else ref
    for k, s in stems.items():
        L = loudness_lufs(s, sr, gated=True)
        if np.isfinite(L):
            s *= 10 ** ((ref + mix_t.get(k, -12) - L) / 20)
    _pct(on_pct, 70)

    # efectos: delay ping-pong en lead, reverb por envio
    lead = stems["lead"]
    if np.abs(lead).max() > 0:
        d = int(0.75 * beat * sr)
        echo = np.zeros_like(lead)
        for i, gg in enumerate((0.33, 0.2, 0.12, 0.07)):
            sh = d * (i + 1)
            if sh < N:
                ch = i % 2
                echo[sh:, ch] += lead[:-sh, :].mean(axis=1) * gg
        echo = signal.sosfilt(_sos("lp", 4500.0, sr), echo, axis=0)
        stems["lead"] = lead + echo.astype(np.float32)
    stems["hats"] = signal.sosfilt(_sos("lp", 11500.0, sr), stems["hats"], axis=0).astype(np.float32)
    sends = {"snare": 0.22, "hats": 0.05, "chords": 0.28, "pad": 0.35, "lead": 0.3, "fx": 0.2, "perc": 0.1}
    if genre in ("piano", "cinematic"):
        sends.update(chords=0.4, pad=0.5, lead=0.4)
    rsend = sum(stems[k] * v for k, v in sends.items())
    ir = _reverb_ir(sr, G["rt60"], seed=int(seed))
    wet = _conv(signal.sosfilt(_sos("hp", 250.0, sr), rsend, axis=0).astype(np.float32), ir, N)
    mix = sum(stems.values()) + wet * 0.55
    # dinamica de arreglo: verso/intro mas bajos, el build sube, el drop pega (contraste ~4 dB)
    sec_db = {"intro": -5.0 if genre in ("pop", "edm", "trap") else -1.0, "verse": -3.5, "verse2": -3.0,
              "build": -3.5, "drop": 0.0, "hook": 0.0}
    g_db = np.zeros(N, np.float32)
    if t_db0 > 0:
        g_db[: int(t_db0 * sr)] = -1.5 if hook else -2.5
    for b in range(nb_all):
        i0, i1 = int((t_db0 + b * bar) * sr), min(N, int((t_db0 + (b + 1) * bar) * sr))
        v0 = sec_db.get(sec[b], 0.0)
        if sec[b] == "build":
            g_db[i0:i1] = np.linspace(v0, -0.5, max(0, i1 - i0))
        else:
            g_db[i0:i1] = v0
    g_db = ndimage.uniform_filter1d(g_db, size=int(0.06 * sr))
    mix = mix * (10 ** (g_db / 20))[:, None]

    if genre == "lofi":  # textura: paso bajos, wow/flutter y vinilo
        mix = signal.sosfilt(_sos("lp", 5200.0, sr), mix, axis=0)
        tt = np.arange(N) / sr
        warp = tt + 0.0011 * np.sin(2 * np.pi * 0.55 * tt) + 0.0003 * np.sin(2 * np.pi * 5.1 * tt)
        mix = np.stack([np.interp(warp, tt, mix[:, c]) for c in range(2)], axis=1)
        hiss = signal.sosfilt(_sos("bp", (1500.0, 9000.0), sr), rng.standard_normal((N, 2)), axis=0) * 0.004
        crack = np.zeros((N, 2))
        pos = rng.integers(0, N, size=int(D * 7))
        crack[pos, rng.integers(0, 2, len(pos))] = rng.uniform(0.05, 0.25, len(pos))
        crack = signal.sosfilt(_sos("bp", (1000.0, 6000.0), sr), crack, axis=0)
        mix = mix + (hiss + crack) * 0.5 * np.abs(mix).max()
    mix = signal.sosfilt(_sos("hp", 36.0, sr, 2), mix, axis=0)
    # hueco para la voz (2-3 kHz) y agudos domados: la pista vive DEBAJO de una voz
    bq, aq = _biquad("peak", 2600.0, 0.8, -3.5, sr)
    mix = signal.lfilter(bq, aq, mix, axis=0)
    # compresion de bus suave (envolvente RMS 30 ms) + saturacion leve
    env = np.sqrt(ndimage.uniform_filter1d((mix ** 2).mean(axis=1), size=int(0.03 * sr)) + 1e-12)
    thr = np.percentile(env, 90)
    gc = np.where(env > thr, (env / thr) ** (1 / 1.8 - 1), 1.0)
    gc = ndimage.uniform_filter1d(gc, size=int(0.01 * sr))
    mix = mix * gc[:, None]
    pk = np.abs(mix).max() + 1e-9
    mix = np.tanh(1.2 * mix / pk) / np.tanh(1.2)
    # entrada limpia; salida: el remate suena completo y su cola cae hasta el final exacto
    fi = int(0.004 * sr)
    mix[:fi] *= np.linspace(0, 1, fi)[:, None]
    mix = _apply_ending(mix.astype(np.float32), sr, emode, t_hit, seed=int(seed))
    _pct(on_pct, 85)

    beats = [round(delta + i * beat, 4) for i in range(nbeats + 1)]
    downbeats = [round(t_db0 + i * bar, 4) for i in range(nbars + 1)]
    secs = []
    if t_db0 > 0.03:
        secs.append({"start": 0.0, "end": round(t_db0, 3), "label": "pickup", "energy": 0.8 if hook else 0.3})
    b = 0
    lab_map = {"verse2": "verse"}
    en_map = {"intro": 0.35, "verse": 0.65, "build": 0.75, "drop": 1.0, "verse2": 0.65, "hook": 1.0}
    while b < nbars:
        e = b
        while e < nbars and sec[e] == sec[b]:
            e += 1
        secs.append({"start": round(t_db0 + b * bar, 3), "end": round(t_db0 + e * bar, 3),
                     "label": lab_map.get(sec[b], sec[b]), "energy": en_map.get(sec[b], 0.6)})
        b = e
    secs.append({"start": round(t_hit, 3), "end": round(D, 3), "label": "ending", "energy": 0.0})
    drop = next((s["start"] for s in secs if s["label"] == "drop"), None)
    hook_end = next((s["end"] for s in secs if s["label"] == "hook"), None)
    info = {"bpm": float(bpm), "beats": beats, "downbeats": downbeats, "sections": secs, "drop": drop,
            "ending": emode, "end_hit": round(t_hit, 4), "genre": genre, "progression": prog_name,
            "keyscale": pr["keyscale"], "src_offset": 0.0, "loops": 0, "hook_end": hook_end, "seams": [],
            "beat_confidence": None}
    return mix.astype(np.float32), info


# ---------------------------------------------------------------- biblioteca
def library_tracks() -> list[dict]:
    """Pistas propias: spike/assets/music (tracks.json) + assets/motion/music."""
    out = []
    for d in (LIB_MUSIC_DIR, SPIKE / "assets" / "motion" / "music"):
        meta = {}
        f = d / "tracks.json"
        if f.exists():
            try:
                meta = {t["id"]: t for t in json.loads(f.read_text()).get("tracks", [])}
            except Exception:
                meta = {}
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() in (".m4a", ".mp3", ".wav", ".flac", ".ogg"):
                m = meta.get(p.stem, {})
                out.append({"id": p.stem, "path": str(p), "bpm": m.get("bpm"), "moods": m.get("moods", []),
                            "title": m.get("title", p.stem)})
    return out


_LIB_PREF = {"elegante": ["ambient", "uplift", "lofi"], "minimal": ["lofi", "ambient", "uplift"],
             "dinamico": ["uplift", "lofi", "ambient"], "energetico": ["uplift", "lofi", "ambient"]}


def _pick_library(style: str, mood: str, track: str | None) -> dict | None:
    tr = library_tracks()
    if not tr:
        return None
    byid = {t["id"]: t for t in tr}
    if track and track in byid:
        return byid[track]
    pref = list(_LIB_PREF.get(style, _LIB_PREF["dinamico"]))
    if mood == "chill":
        pref = ["lofi"] + pref
    elif mood in ("dramatic", "inspiring") and style == "elegante":
        pref = ["ambient"] + pref
    for p in pref:
        if p in byid:
            return byid[p]
    return tr[0]


# ---------------------------------------------------------------- cache y orquestacion
def _default_cache() -> Path:
    try:
        import paths
        return Path(paths.WORK) / "music"
    except Exception:
        return DATA / "work" / "music"


def _hash(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


@contextmanager
def _lock(d: Path, name: str = ".music.lock"):
    """Candado entre procesos e hilos (evita dos generaciones simultaneas en la GPU; un
    `prefetch` en curso y el get_music del render se esperan en vez de generar dos veces)."""
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / name, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except ImportError:  # Windows: sin candado (una generacion a la vez por diseno)
            pass
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        f.close()


def _empty_result(D: float, reason: str = "") -> dict:
    return {"wav": None, "bpm": 0.0, "beats": [], "downbeats": [], "sections": [], "drop": None,
            "duration": float(D) if math.isfinite(D) else 0.0, "source": "none", "cached": False,
            "ending": {"mode": None, "hit": None}, "error": reason or None}


def _analyze_raw(x: np.ndarray, bpm_hint: float | None) -> dict:
    ana = analyze(x, SR, bpm_hint)
    s, e = ana["start"], ana["end"]
    runs = silence_runs(x[int(s * SR):int(e * SR)], SR, rel_db=-35, min_len=0.5, ref_db=ana.get("ref_db"))
    ana["silences"] = [[round(a + s, 3), round(b + s, 3)] for a, b in runs]
    ana["ana_version"] = ANALYSIS_VERSION
    ana["bpm_hint"] = bpm_hint
    return ana


def _load_ana(raw: Path, rmeta: Path, bpm_hint: float | None) -> dict:
    """Analisis cacheado del crudo; si es de una version vieja, re-analiza (sin regenerar)."""
    ana = json.loads(rmeta.read_text())
    if ana.get("ana_version") != ANALYSIS_VERSION or ana.get("bpm_hint") != bpm_hint:
        keep = {k: ana[k] for k in ("quality", "seed_used", "gen_s", "gen_dur", "ace_timing") if k in ana}
        ana = _analyze_raw(load_audio(raw, SR), bpm_hint)
        ana.update(keep)
        _write_json(rmeta, ana)
    return ana


def _write_json(p: Path, obj: Any) -> None:
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, p)


def _finish(y: np.ndarray, info: dict, D: float, out: Path, meta: dict) -> dict:
    y, lufs = normalize(y, SR)
    write_audio(out, y, SR)
    drop = info.get("drop")
    secs = info.get("sections") or sections_for(y, SR, info.get("downbeats", []), D, info.get("end_hit"),
                                                info.get("ending", "hit"), drop, info.get("hook_end"))
    res = {"wav": str(out), "bpm": round(float(info.get("bpm") or meta.get("bpm") or 0), 3),
           "beats": info.get("beats", []), "downbeats": info.get("downbeats", []), "sections": secs,
           "drop": drop, "duration": float(D), "ending": {"mode": info.get("ending"), "hit": info.get("end_hit")},
           "src_offset": info.get("src_offset", 0.0), "loops": info.get("loops", 0),
           "hook_end": info.get("hook_end"), "seams": info.get("seams", []),
           "beat_confidence": info.get("beat_confidence"),
           "lufs": round(float(lufs), 2) if np.isfinite(lufs) else None, "cached": False}
    res.update(meta)
    # en disco las rutas van relativas al cache (si DATA se mueve, el hit sigue sirviendo)
    disk = dict(res, wav=out.name, raw=Path(res["raw"]).name if res.get("raw") else None)
    _write_json(out.with_suffix(".json"), disk)
    prune_cache(out.parent)
    return res


def _cached_result(out: Path) -> dict | None:
    j = out.with_suffix(".json")
    if out.exists() and j.exists():
        try:
            r = json.loads(j.read_text())
        except Exception:
            return None
        r["wav"] = str(out)
        if r.get("raw"):
            r["raw"] = str(out.parent / Path(r["raw"]).name)
        r["cached"] = True
        try:
            os.utime(out)  # LRU: lo usado recientemente sobrevive a la poda
        except OSError:
            pass
        return r
    return None


CACHE_MAX_BYTES = int(os.environ.get("REELFY_MUSIC_CACHE_MB", "1500")) * 1_000_000


def prune_cache(cache: Path, max_bytes: int = CACHE_MAX_BYTES) -> int:
    """Poda LRU del cache: carpetas de trabajo de ACE abandonadas (> 1 h), luego ajustes (baratos
    de rehacer), luego crudos. Devuelve bytes liberados."""
    freed = 0
    try:
        now = time.time()
        for d in Path(cache).glob("ace_*"):
            if d.is_dir() and now - d.stat().st_mtime > 3600:
                freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d, ignore_errors=True)
        files = [p for p in Path(cache).iterdir() if p.suffix in (".wav", ".flac")]
    except OSError:
        return freed
    total = sum(p.stat().st_size for p in files)
    for prefix in ("music_", "raw_"):
        for p in sorted((f for f in files if f.name.startswith(prefix)), key=lambda f: f.stat().st_mtime):
            if total - freed <= max_bytes:
                return freed
            sz = p.stat().st_size
            p.unlink(missing_ok=True)
            p.with_suffix(".json").unlink(missing_ok=True)
            freed += sz
    return freed


def _fit_from_raw(raw: Path, ana: dict, D: float, fopt: dict, cache: Path, meta: dict,
                  on_pct: Callable | None) -> dict:
    fkey = _hash({"v": VERSION, "a": ANALYSIS_VERSION, "raw": raw.name, "D": round(D, 3), "f": fopt,
                  "lufs": TARGET_LUFS})
    out = cache / f"music_{fkey}.wav"
    hit = _cached_result(out)
    if hit:
        return hit
    x = load_audio(raw, SR)
    _pct(on_pct, 93)
    y, info = fit(x, SR, ana, D, ending=fopt["ending"], drop_at=fopt["drop_at"], tail=fopt["tail"],
                  seed=int(meta.get("seed", 0)), stops=fopt["stops"], hook=fopt["hook"],
                  drop_tol=fopt["drop_tol"])
    info["bpm"] = ana.get("bpm") if ana.get("grid") != "none" else 0.0   # sin pulso no hay tempo
    info["beat_confidence"] = ana.get("beat_confidence")
    info["sections"] = None
    return _finish(y, info, D, out, meta)


def _raw_candidates(cache: Path, rbase: str) -> list[tuple[Path, Path, dict]]:
    out = []
    for j in cache.glob(f"raw_{rbase}_*.json"):
        f = j.with_suffix(".flac")
        if not f.exists():
            continue
        try:
            out.append((f, j, json.loads(j.read_text())))
        except Exception:
            continue
    return out


def _pick_raw(cands: list[tuple[Path, Path, dict]], D: float) -> tuple[Path, Path] | None:
    """Crudo reutilizable para un video de D s: el de menor duracion generada cuya parte util
    alcance 0.75*D (fit extiende con loops lo que falte). Estable ante recortes chicos."""
    ok = [(float(a.get("gen_dur") or 0), -f.stat().st_mtime, f, j) for f, j, a in cands
          if float(a.get("end", 0)) - float(a.get("start", 0)) >= 0.75 * D]
    if not ok:
        return None
    ok.sort(key=lambda r: (r[0], r[1]))
    return ok[0][2], ok[0][3]


def _adopt_legacy_raws(cache: Path, prompt: dict, seed: int, rbase: str) -> None:
    """Crudos del esquema anterior (clave con la duracion generada) -> esquema actual, para no
    regenerar musica ya hecha."""
    for gd in range(20, 610, 10):
        old = _hash({"v": RAW_VERSION, "engine": "ace", "model": DIT_MODEL, "caption": prompt["caption"],
                     "bpm": prompt["bpm"], "key": prompt["keyscale"], "dur": float(gd), "seed": int(seed)})
        f, j = cache / f"raw_{old}.flac", cache / f"raw_{old}.json"
        if f.exists() and j.exists():
            try:
                os.replace(f, cache / f"raw_{rbase}_{gd}.flac")
                os.replace(j, cache / f"raw_{rbase}_{gd}.json")
            except OSError:
                pass


def _source_ai(prompt: dict, D: float, seed: int, cache: Path, fopt: dict, on_pct) -> dict:
    if not music_ready():
        raise RuntimeError("musica IA no instalada (ace-synth o modelos faltantes)")
    # el crudo se indexa por (caption, bpm, tonalidad, seed): cambiar la duracion del video reusa
    # cualquier crudo con material suficiente (solo se regenera si falta mucho)
    rbase = _hash({"v": RAW_VERSION, "engine": "ace", "model": DIT_MODEL, "caption": prompt["caption"],
                   "bpm": prompt["bpm"], "key": prompt["keyscale"], "seed": int(seed)})
    gen_s = 0.0
    cands = _raw_candidates(cache, rbase)
    if not cands:
        _adopt_legacy_raws(cache, prompt, seed, rbase)
        cands = _raw_candidates(cache, rbase)
    pick = _pick_raw(cands, D)
    if pick is None:
        with _lock(cache):
            pick = _pick_raw(_raw_candidates(cache, rbase), D)
            if pick is None:
                gd = gen_duration(D)
                raw, rmeta = cache / f"raw_{rbase}_{int(gd)}.flac", cache / f"raw_{rbase}_{int(gd)}.json"
                best = None
                work = cache / f"ace_{rbase}_{int(gd)}"
                try:
                    for sd in (int(seed), int(seed) + 1009):
                        t0 = time.time()
                        wav = _ace_generate(prompt, gd, sd, work,
                                            (lambda p: _pct(on_pct, p * 0.9)) if on_pct else None)
                        x = load_audio(wav, SR)
                        pk = float(np.abs(x).max())
                        if pk > 0.98:  # el VAE puede pasar de 1.0 en float: bajar antes de guardar en PCM
                            x *= 0.98 / pk
                        ana = _analyze_raw(x, prompt["bpm"])
                        ana["ace_timing"] = dict(LAST_ACE_TIMING)
                        q, probs = _raw_quality(ana, prompt["bpm"], gd)
                        gen_s += time.time() - t0
                        if probs:
                            print(f"   music: crudo ACE seed={sd} calidad {q:.2f} ({'; '.join(probs)})")
                        if best is None or q > best[0]:
                            best = (q, x, ana, sd)
                        if q >= 0.7:
                            break
                finally:
                    shutil.rmtree(work, ignore_errors=True)
                q, x, ana, sd = best
                ana.update(quality=q, seed_used=sd, gen_s=round(gen_s, 2), gen_dur=gd)
                write_audio(raw, x, SR, subtype="PCM_24")
                _write_json(rmeta, ana)
                pick = (raw, rmeta)
    raw, rmeta = pick
    try:
        os.utime(raw)
    except OSError:
        pass
    ana = _load_ana(raw, rmeta, prompt["bpm"])
    meta = {"source": "ai", "engine": "ace-step-1.5-turbo", "prompt": prompt["caption"],
            "keyscale": prompt["keyscale"], "seed": int(seed), "raw": str(raw),
            "gen_dur": ana.get("gen_dur"), "quality": ana.get("quality")}
    res = _fit_from_raw(raw, ana, D, fopt, cache, meta, on_pct)
    res["gen_s"] = round(gen_s, 2)
    return res


def _source_procedural(prompt: dict, D: float, seed: int, cache: Path, fopt: dict, on_pct) -> dict:
    key = _hash({"v": VERSION, "engine": "procedural", "style": prompt["style"], "mood": prompt["mood"],
                 "genre": prompt.get("genre"), "bpm": prompt["bpm"], "key": prompt["keyscale"],
                 "D": round(D, 3), "seed": int(seed), "f": fopt, "lufs": TARGET_LUFS})
    out = cache / f"music_{key}.wav"
    hit = _cached_result(out)
    if hit:
        return hit
    t0 = time.time()
    y, info = synth_procedural(D, prompt["style"], prompt["mood"], prompt["bpm"], prompt["keyscale"], seed,
                               fopt["drop_at"], SR, on_pct, genre=prompt.get("genre"), ending=fopt["ending"],
                               tail=fopt["tail"], stops=fopt["stops"], hook=fopt["hook"] >= 0.6,
                               drop_tol=fopt["drop_tol"])
    meta = {"source": "procedural", "engine": f"procedural-{info['genre']}", "prompt": prompt["caption"],
            "keyscale": info["keyscale"], "seed": int(seed), "raw": None,
            "gen_s": round(time.time() - t0, 2), "gen_dur": D}
    return _finish(y, info, D, out, meta)


def _source_file(path: Path, bpm_hint: float | None, D: float, cache: Path, fopt: dict, on_pct,
                 source: str) -> dict:
    st = path.stat()
    rkey = _hash({"v": RAW_VERSION, "file": str(path.resolve()), "size": st.st_size, "mtime": int(st.st_mtime)})
    raw, rmeta = cache / f"raw_{rkey}.flac", cache / f"raw_{rkey}.json"
    if not (raw.exists() and rmeta.exists()):
        x = load_audio(path, SR)
        ana = _analyze_raw(x, bpm_hint)
        write_audio(raw, x, SR, subtype="PCM_24")
        _write_json(rmeta, ana)
    ana = _load_ana(raw, rmeta, bpm_hint)
    meta = {"source": source, "engine": "file", "prompt": "", "keyscale": None, "seed": 0,
            "raw": str(raw), "file": str(path), "gen_s": 0.0, "gen_dur": ana.get("dur")}
    return _fit_from_raw(raw, ana, D, fopt, cache, meta, on_pct)


# ---------------------------------------------------------------- configuracion tolerante
STYLE_HOOK = {"dinamico": 0.75, "energetico": 0.75, "elegante": 0.6, "minimal": 0.6}
EARLY_DROP_STYLES = ("dinamico", "energetico")   # sin drop_at: el golpe entra en el primer segundo


def _num(v: Any, default: float | None = None, lo: float | None = None, hi: float | None = None) -> float | None:
    """float tolerante: None/''/'abc'/NaN/inf -> default; recorta a [lo, hi]."""
    if isinstance(v, bool):
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    if lo is not None:
        f = max(lo, f)
    if hi is not None:
        f = min(hi, f)
    return f


def valid_keyscale(k: Any) -> str | None:
    """'a minor', 'Bb major', 'F#-min' -> forma canonica ('A minor', 'Bb major', 'F# minor');
    invalida (p. ej. 'H# lydian') -> None."""
    if not isinstance(k, str):
        return None
    parts = k.replace("-", " ").replace("♯", "#").replace("♭", "b").split()
    if len(parts) != 2:
        return None
    r, m = parts[0], parts[1].lower()
    if not r or r[0].upper() not in "ABCDEFG" or len(r) > 2 or (len(r) == 2 and r[1] not in "#b"):
        return None
    if m in ("major", "maj", "mayor"):
        m = "major"
    elif m in ("minor", "min", "menor"):
        m = "minor"
    else:
        return None
    root = r[0].upper() + (r[1] if len(r) == 2 else "")
    return f"{root} {m}" if root.upper() in _NOTE else None


def _coerce_cfg(cfg: dict | None, D: float, seed: int) -> tuple[dict, list[str]]:
    """plan.fx.music -> cfg normalizada (nunca lanza). Devuelve (cfg, avisos)."""
    c = dict(cfg) if isinstance(cfg, dict) else {}
    warn = []
    src = str(c.get("source") or "").strip().lower()
    f = c.get("file") if isinstance(c.get("file"), str) and c.get("file").strip() else None
    if src not in ("ai", "procedural", "library", "file", "none"):
        if src:
            warn.append(f"source desconocida {src!r}: se usa ai")
        src = "file" if (f and not src) else "ai"   # compatibilidad: file sin source = archivo propio
    bpm_in = _num(c.get("bpm"))
    lock = bool(c.get("bpm_lock"))
    bpm = None
    if bpm_in and bpm_in > 0 and (lock or bpm_in != 110.0):
        bpm = float(np.clip(bpm_in, BPM_MIN, BPM_MAX))
        if bpm != bpm_in:
            warn.append(f"bpm {bpm_in:g} fuera de rango: se usa {bpm:g}")
    ks = c.get("keyscale")
    key = valid_keyscale(ks) if ks else None
    if ks and key is None:
        warn.append(f"tonalidad invalida {ks!r}: se usa la del estilo")
    sd = _num(c.get("seed"))
    if c.get("seed") not in (None, "") and sd is None:
        warn.append(f"seed invalida {c.get('seed')!r}")
    drop_at = _num(c.get("drop_at"), None, 0.0)
    if c.get("drop_at") not in (None, "") and drop_at is None:
        warn.append(f"drop_at invalido {c.get('drop_at')!r}: se ignora")
    ending = c.get("ending") if c.get("ending") in ("auto", "natural", "hit", "fade") else "auto"
    mood = c.get("mood") if c.get("mood") in MOOD_MUSIC else None
    out = {"enabled": c.get("enabled", True) not in (False, 0, "false", "0"),
           "source": src, "file": f, "track": c.get("track") if isinstance(c.get("track"), str) else None,
           "prompt": c.get("prompt") if isinstance(c.get("prompt"), str) else "",
           "mood": mood, "bpm": bpm, "bpm_lock": lock and bpm is not None, "keyscale": key,
           "seed": int(sd) if sd is not None else int(seed), "drop_at": drop_at, "ending": ending,
           "tail": float(_num(c.get("fade_out"), 1.2, 0.6, 3.0)), "stops": _norm_stops(c.get("stops"), D)}
    return out, warn


def get_music(cfg: dict | None, duration: float, style: str = "dinamico", seed: int = 7,
              on_pct: Callable | None = None, cache_dir: str | Path | None = None) -> dict:
    """Musica lista para mezclar, de largo EXACTO `duration` (s, tiempo final del video).

    cfg (plan.fx.music, ver CFG_SCHEMA; valores invalidos se corrigen, nunca lanza por la cfg):
      enabled, source (ai|procedural|library|file|none), track, file (solo con source "file"),
      prompt (texto libre; se fuerza instrumental y se le agrega el sufijo funcional de cama),
      mood (upbeat|chill|inspiring|corporate|playful|dramatic), bpm (0/None/110 = auto por estilo
      y animo; `bpm_lock: true` fuerza cualquier valor), keyscale, seed (otra variacion),
      drop_at (s en tiempo FINAL: render remapea antes de llamar), ending (auto|natural|hit|fade),
      fade_out (s, cola final objetivo), stops [{at, dur}] (tiempo final: audio.py los silencia;
      aqui sirven para esconder una costura y hacer que el drop entre al volver). swells/gain_db/
      duck_db son del mezclador y aqui se ignoran.
    Sin drop_at: en dinamico/energetico el drop entra en el primer segundo (hook con energia); con
      stops, el drop entra donde termina el primer stop.
    Devuelve {wav, bpm, beats, downbeats, sections, drop, hook_end, seams, duration, source,
      engine, prompt, keyscale, seed, ending{mode,hit}, lufs, cached, gen_s, beat_confidence,
      warnings, ...}. `wav` es None si no hay musica. `drop` es donde QUEDO la entrada del drop en
      el video (o None). `lufs` es el nivel real (TARGET_LUFS salvo material muy dinamico).
    WAV: 48 kHz estereo PCM24, muestras = round(duration*48000), <= PEAK_DB dBTP y con el final ya
    horneado (el mezclador no necesita otro fade_out). Tiempos en segundos de salida.
    Si on_pct lanza (cancelacion), se propaga la misma excepcion (no se cae a otra fuente).
    """
    D = _num(duration, float("nan"))
    if D is None or not math.isfinite(D) or D <= 0.25:
        return _empty_result(D if D is not None else float("nan"), "duracion invalida")
    c, warn = _coerce_cfg(cfg, D, seed)
    if not c["enabled"] or c["source"] == "none":
        return _empty_result(D, "desactivada")
    cache = Path(cache_dir) if cache_dir else _default_cache()
    cache.mkdir(parents=True, exist_ok=True)
    style = style if style in STYLE_MUSIC else "dinamico"
    mood = c["mood"] or STYLE_DEFAULT_MOOD[style]
    seed = c["seed"]
    prompt = build_prompt(style, mood, c["prompt"], c["bpm"], seed, c["keyscale"])
    drop_at, drop_tol = c["drop_at"], 0.0
    st = c["stops"]
    if drop_at is None and st and 0.5 <= st[0][0] and st[0][0] + st[0][1] <= D - 3.0:
        drop_at = st[0][0] + st[0][1]          # el golpe entra al volver del primer stop
    elif drop_at is None and style in EARLY_DROP_STYLES:
        drop_at, drop_tol = 0.5, 0.5            # drop en [0, 1] s: arranque con energia
    fopt = {"ending": c["ending"], "drop_at": drop_at, "drop_tol": drop_tol, "tail": c["tail"],
            "stops": [[round(a, 3), round(d, 3)] for a, d in st], "hook": STYLE_HOOK.get(style, 0.6)}
    src = c["source"]
    if src == "file":
        order = ["file", "procedural", "library"]
    elif src == "library":
        order = ["library", "procedural"]
    elif src == "procedural":
        order = ["procedural", "library"]
    else:
        order = ["ai", "procedural", "library"]
    errors = []
    t0 = time.time()
    on_pct = _Progress(on_pct) if on_pct else None
    for s in order:
        try:
            _pct(on_pct, 1)
            if s == "ai":
                res = _source_ai(prompt, D, seed, cache, fopt, on_pct)
            elif s == "procedural":
                res = _source_procedural(prompt, D, seed, cache, fopt, on_pct)
            elif s == "file":
                if not c["file"]:
                    raise FileNotFoundError("source file sin `file`")
                p = Path(c["file"])
                if not p.exists():
                    raise FileNotFoundError(str(p))
                res = _source_file(p, c["bpm"] if c["bpm_lock"] else None, D, cache, fopt, on_pct, "file")
            else:
                t = _pick_library(style, mood, c["track"])
                if not t:
                    raise RuntimeError("biblioteca vacia")
                res = _source_file(Path(t["path"]), t.get("bpm"), D, cache, fopt, on_pct, "library")
                res["track"] = t["id"]
            res["fallback_from"] = errors or None
            res["warnings"] = warn or None
            res["total_s"] = round(time.time() - t0, 2)
            _pct(on_pct, 100)
            return res
        except MusicCancelled as e:
            raise (e.__cause__ or e) from None
        except Exception as e:
            errors.append(f"{s}: {e}")
            print(f"   music: {s} fallo ({e}); siguiente fuente")
    r = _empty_result(D, "; ".join(errors))
    r["warnings"] = warn or None
    return r


def prefetch(cfg: dict | None, duration: float, style: str = "dinamico", seed: int = 7,
             cache_dir: str | Path | None = None) -> threading.Thread:
    """Arranca get_music en un hilo (daemon) y devuelve el hilo. Para correr la generacion de ACE
    (GPU Metal) en paralelo con el resto del render o desde el director; el get_music posterior
    con la misma cfg es un hit de cache, o espera el candado de la generacion en curso."""
    def run():
        try:
            get_music(cfg, duration, style, seed, None, cache_dir)
        except Exception as e:  # pragma: no cover - solo precalentamiento
            print(f"   music: prefetch fallo ({e})")
    th = threading.Thread(target=run, name="music-prefetch", daemon=True)
    th.start()
    return th


# ---------------------------------------------------------------- validacion (tests/demos)
def validate_music(src: str | Path | np.ndarray, sr: int = SR, bpm: float | None = None) -> dict:
    """Metricas objetivas (no podemos escuchar): duracion, LUFS, true peak, clipping, silencios
    internos, tempo detectado SIN pista (estimador propio, libre de errores de octava) vs pedido,
    y loudness del arranque (0-2 s) respecto al total."""
    x = load_audio(src, sr) if not isinstance(src, np.ndarray) else src
    ana = analyze(x, sr, None)
    tempo = float(ana["bpm"] or 0.0)
    body = x[: max(1, len(x) - int(1.0 * sr))]
    return {"dur": len(x) / sr, "lufs": loudness_lufs(x, sr), "true_peak_db": true_peak_db(x),
            "clipped": int((np.abs(x) >= 0.999).sum()), "tempo": tempo, "grid": ana["grid"],
            "tempo_err": (min(abs(tempo / bpm - r) for r in (0.5, 1.0, 2.0)) if bpm and tempo else None),
            "lu_first2": loudness_lufs(x[: 2 * sr], sr, gated=False),
            "silences": silence_runs(body, sr, rel_db=-35, min_len=0.5)}
