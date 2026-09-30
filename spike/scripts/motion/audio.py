"""Mezclador final del Motion Engine: voz + musica + SFX -> WAV 48 kHz estereo a -14 LUFS.

Contrato (docs/MOTION-ENGINE.md, 4.2b):
    mix(voice_wav, duration, sfx_events, music_cfg, out_wav) -> dict
Todos los tiempos (eventos, stops, swells) en tiempo FINAL (segundos).

Cadena:
  1. voz (ya procesada por la cadena de estudio) -> nivel de referencia REF_LUFS (-14 LUFS)
  2. nivel de la voz en banda 120-4500 Hz (RMS 20 ms a 1 kHz de control) -> dos curvas 0..1:
     - actividad corta (hold 160 ms, look-ahead 40 ms, ataque 40 ms, release 250 ms): agacha los
       SFX y la cola grave de los impactos bajo la voz
     - actividad de musica (hold 600 ms, look-ahead 80 ms, release 500 ms): la cama no "respira"
       entre palabras ni en pausas cortas; solo sube en pausas reales (> ~0.6 s)
  3. musica: loop con fundido de igual potencia o recorte, tape stop real (la propia pista se
     frena antes del stop), nivel relativo a la voz, ducking + EQ dinamico en la banda de la voz,
     stops (hueco dramatico), swells, fade in/out (omitidos si music.py ya ajusto el final)
  4. SFX: cada evento se alinea por su `hit` con `at`; variante determinista; `dur` recorta o
     extiende los sonidos continuos; transposicion a la tonalidad de la musica (SFX tonales);
     paneo por balance; bus de SFX con limitador propio (un SFX muy fuerte no aplasta la mezcla)
  5. bus: suma -> normalizacion a -14 LUFS integrados (BS.1770-4 propio) -> limitador de true
     peak con look-ahead (-1.5 dBTP) -> iteraciones a +-0.08 LU. Si llegar al objetivo exige
     limitar mas de MAX_GR_DB de forma sostenida (p98 de bloques de 400 ms) o mas de
     MAX_GR_ABS_DB en un transitorio aislado, se baja la ganancia y se informa lufs_short_by
  6. WAV 48 kHz estereo PCM 24 bits de EXACTAMENTE round(duration*48000) muestras

Semantica de music_cfg (ganancias relativas a la voz, en LU):
  gain_db   nivel de la musica MIENTRAS hay voz (cama bajo la voz, en la banda de la voz).
            Default del plan: -17.
  duck_db   diferencia total entre la cama y los huecos (negativo). En pausas reales la musica
            sube |duck_db| (tope: OPEN_MAX_LU bajo la voz). Default del plan: -9.
  voice_carve_db (default -3): parte de duck_db que se aplica solo en 250 Hz-5 kHz (EQ
            dinamico): graves y brillo de la musica se agachan |duck|-|carve| y la banda de la voz
            |duck|. Asi el default (-17, -9, -3) da huecos a -8 LU bajo la voz, graves de la cama
            a -11 LU y banda media a -17 LU.
  fade_in, fade_out (s); ending ({mode, hit} de music.get_music, o el modo como texto): si el
            modo es natural, hit o fade, music.py ya hizo el final y el arranque (el primer
            downbeat no se suaviza): aqui solo quedan fundidos de seguridad (6 ms / 30 ms) para
            no apagar el golpe final; la cola la decide fade_out DENTRO de music.py.
  intro_fade (s, default 0): entrada suave EXPLICITA de la musica ("Entrada suave" del
            inspector); se respeta siempre, tambien con el arranque horneado (fade_in no, por
            compatibilidad: los planes traen fade_in 0.2 que nunca debio suavizar el golpe).
  Al final de la mezcla completa (voz + SFX + musica) siempre hay un fundido de seguridad de
  END_FADE_S (12 ms): un keep que termina a media palabra nunca deja un clic al final.
  stops [{at, dur, mode?, fade?, fade_back?}]: hueco dramatico. mode "tape" (default): la pista se
            frena en [at-fade, at] (fade default 0.5 s) y queda en silencio hasta at+dur. mode
            "cut": corte seco con fundido `fade` (default 25 ms). fade_back (default 60 ms).
            Un record_stop de SFX junto a un tape stop se omite: lo reemplaza el frenado real.
  swells [{start, end, db, ramp?}], offset (s, inicio dentro del archivo), enabled (bool),
  source ("none" = sin musica), keyscale ("A minor", "F# major"...: transpone los SFX tonales).
  Archivo: `wav` | `file` | `path` (cualquier formato que lea libsndfile o ffmpeg).

Eventos SFX: {at, name, gain_db?, pan?, variant? (0-based), pitch? (semitonos), dur? (s), id?,
  enabled?}. Con gain_db 0 el pico del SFX llega al nivel de los picos de la voz (prominente);
  el director usa de -2 a -7 dB. Valores invalidos no rompen la mezcla: el evento va a sfx_skipped.

Medicion: `loudness(x)` implementa ITU-R BS.1770-4 / EBU R128 (integrado con compuertas
absoluta y relativa, momentaneo, corto plazo, LRA y true peak con sobremuestreo 4x), por bloques
de 5.5 s (memoria acotada en videos largos).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from scipy import signal
from scipy.ndimage import maximum_filter1d, minimum_filter1d, uniform_filter1d

SR = 48000
REF_LUFS = -14.0          # nivel interno de la voz (= entregado): los SFX se calibran contra el
TARGET_LUFS = -14.0
TRUE_PEAK_DB = -1.5
OPEN_MAX_LU = -4.0        # la musica en huecos nunca pasa de voz-4 LU
VOICE_CARVE_DB = -3.0     # parte del duck que se aplica solo en la banda de la voz
CTRL_HZ = 1000            # tasa de control de envolventes (1 ms)
MAX_GR_DB = 3.0           # limitacion sostenida maxima (p98 de bloques de 400 ms) del bus final
MAX_GR_ABS_DB = 6.0       # tope absoluto (un transitorio aislado: voz + SFX coincidiendo)
SFX_BUS_CEIL_DB = -1.0    # techo del bus de SFX antes de sumar (dBTP)
SFX_DUCK_DB = -1.5        # los SFX se agachan esto bajo la voz
MUSIC_HOLD_S = 0.6        # la cama no sube en pausas mas cortas que esto
MUSIC_RELEASE_S = 0.5
MIN_MUSIC_S = 0.5         # musica mas corta que esto = ausente
TAPE_S = 0.5              # largo default del frenado (tape stop)
IMPACT_TAIL_DUCK_DB = -6.0

_SUB = SR // 10           # sub-bloque de 100 ms (paso de BS.1770)
_CHUNK = 55 * _SUB        # bloques de proceso de 5.5 s

# ---------------------------------------------------------------- utilidades numericas
def _num(v: Any, default: float = 0.0) -> float:
    """float tolerante: None, texto, NaN o inf -> default."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float(default)
    return f if math.isfinite(f) else float(default)


def _as_list(v: Any) -> list:
    return list(v) if isinstance(v, (list, tuple)) else []


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def db(v: float) -> float:
    return 20 * math.log10(v) if v > 0 else -math.inf


def undb(d: float) -> float:
    return 10.0 ** (d / 20.0)


# ---------------------------------------------------------------- BS.1770-4
# Coeficientes K-weighting para 48 kHz (ITU-R BS.1770-4, tabla 1 y 2)
_K_SOS = np.array([
    [1.53512485958697, -2.69169618940638, 1.19839281085285, 1.0, -1.69065929318241, 0.73248077421585],
    [1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621],
])


def as_stereo(x: np.ndarray) -> np.ndarray:
    """(n,) | (n,1) | (n,2) | (n,k) -> float64 (n,2)."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        return np.stack([x, x], axis=1)
    if x.shape[1] == 1:
        return np.repeat(x, 2, axis=1)
    if x.shape[1] > 2:
        return x[:, :2].copy()
    return x


def _ch2(x: np.ndarray) -> np.ndarray:
    """Vista 2D sin copiar ni cambiar el tipo: (n,) -> (n,1); (n,k>2) -> (n,2)."""
    x = np.asarray(x)
    if x.ndim == 1:
        return x[:, None]
    return x[:, :2] if x.shape[1] > 2 else x


def k_weight(x: np.ndarray) -> np.ndarray:
    """Filtro K (48 kHz) por canal. x: (n,) o (n,c)."""
    return signal.sosfilt(_K_SOS, x, axis=0)


def _block_power(z2: np.ndarray, win: int, hop: int) -> np.ndarray:
    """Potencia media por bloque (sin bloques parciales). z2: (n,c) cuadrados -> (nb,c)."""
    n = z2.shape[0]
    if n < win:
        return np.zeros((0, z2.shape[1]))
    cs = np.concatenate([np.zeros((1, z2.shape[1])), np.cumsum(z2, axis=0)], axis=0)
    starts = np.arange(0, n - win + 1, hop)
    return (cs[starts + win] - cs[starts]) / win


def _lk(p: np.ndarray) -> np.ndarray:
    """Potencia (suma de canales) -> LUFS."""
    with np.errstate(divide="ignore"):
        return -0.691 + 10.0 * np.log10(np.maximum(p, 1e-20))


def _kw_sub(x: np.ndarray, gain: np.ndarray | None = None) -> np.ndarray:
    """Energia K-ponderada (suma de canales) por sub-bloque de 100 ms, procesando por bloques de
    5.5 s con el estado del filtro encadenado (identico a filtrar todo junto, memoria acotada).
    Mono cuenta como dos canales iguales (igual que as_stereo). `gain` (n,) opcional se aplica
    antes del filtro (curva de ganancia del limitador)."""
    x2 = _ch2(x)
    mult = 2.0 if x2.shape[1] == 1 else 1.0
    nsub = x2.shape[0] // _SUB
    out = np.zeros(nsub)
    if nsub == 0:
        return out
    zi = [np.zeros((_K_SOS.shape[0], 2)) for _ in range(x2.shape[1])]
    end = nsub * _SUB
    for a in range(0, end, _CHUNK):
        b = min(end, a + _CHUNK)
        g = None if gain is None else np.asarray(gain[a:b], dtype=np.float64)
        acc = np.zeros(b - a)
        for c in range(x2.shape[1]):
            s = np.asarray(x2[a:b, c], dtype=np.float64)
            if g is not None:
                s = s * g
            z, zi[c] = signal.sosfilt(_K_SOS, s, zi=zi[c])
            acc += z * z
        out[a // _SUB:b // _SUB] = acc.reshape(-1, _SUB).sum(axis=1) * mult
    return out


def _win_power(sub: np.ndarray, k: int) -> np.ndarray:
    """Potencia media de ventanas de k sub-bloques con paso de 1 sub-bloque."""
    if sub.size < k:
        return np.zeros(0)
    cs = np.concatenate([[0.0], np.cumsum(sub)])
    return (cs[k:] - cs[:-k]) / (k * _SUB)


def _gated(pb: np.ndarray) -> float:
    """Integrado con compuerta absoluta (-70) y relativa (-10 LU)."""
    if pb.size == 0:
        return -math.inf
    lb = _lk(pb)
    g1 = pb[lb > -70.0]
    if g1.size == 0:
        return -math.inf
    rel = _lk(np.array([g1.mean()]))[0] - 10.0
    g2 = pb[(lb > -70.0) & (lb > rel)]
    if g2.size == 0:
        return -math.inf
    return float(_lk(np.array([g2.mean()]))[0])


def _tp_iter(x: np.ndarray, block: int = 1 << 18, pad: int = 64):
    """(a, b, pico por muestra) por bloques: max |x| de canales y de los 4 puntos
    sobremuestreados (true peak). Memoria acotada aunque el video dure minutos."""
    x2 = _ch2(x)
    n = x2.shape[0]
    for a in range(0, n, block):
        b = min(n, a + block)
        a0, b0 = max(0, a - pad), min(n, b + pad)
        seg = np.asarray(x2[a0:b0], dtype=np.float64)
        up = signal.resample_poly(seg, 4, 1, axis=0)
        pk = np.abs(up).max(axis=1)
        s0 = (a - a0) * 4
        sl = pk[s0:s0 + (b - a) * 4]
        if sl.shape[0] < (b - a) * 4:
            sl = np.concatenate([sl, np.zeros((b - a) * 4 - sl.shape[0])])
        v = np.maximum(sl.reshape(b - a, 4).max(axis=1), np.abs(seg[a - a0:b - a0]).max(axis=1))
        yield a, b, v


def _tp_per_sample(x: np.ndarray, block: int = 1 << 18, pad: int = 64) -> np.ndarray:
    """Pico por muestra (true peak) -> float32 (n,)."""
    out = np.zeros(_ch2(x).shape[0], dtype=np.float32)
    for a, b, v in _tp_iter(x, block, pad):
        out[a:b] = v
    return out


def _tp_max(x: np.ndarray) -> float:
    m = 0.0
    for _a, _b, v in _tp_iter(x):
        if v.size:
            m = max(m, float(v.max()))
    return m


def true_peak_db(x: np.ndarray) -> float:
    """True peak (dBTP) con sobremuestreo 4x (BS.1770-4 anexo 2)."""
    if np.asarray(x).shape[0] == 0:
        return -math.inf
    return db(_tp_max(x))


def integrated_lufs(x: np.ndarray) -> float:
    """Loudness integrada BS.1770-4 (compuerta -70 LUFS y relativa -10 LU)."""
    if np.asarray(x).shape[0] == 0:
        return -math.inf
    return _gated(_win_power(_kw_sub(x), 4))


def loudness(x: np.ndarray) -> dict:
    """Medicion completa: integrado, momentaneo max, corto plazo max, LRA y true peak."""
    sub = _kw_sub(x) if np.asarray(x).shape[0] else np.zeros(0)
    pm = _win_power(sub, 4)
    ps = _win_power(sub, 30)
    out = {"integrated": _gated(pm),
           "momentary_max": float(_lk(pm).max()) if pm.size else -math.inf,
           "short_term_max": float(_lk(ps).max()) if ps.size else -math.inf,
           "lra": 0.0, "true_peak": true_peak_db(x)}
    if ps.size:
        ls = _lk(ps)
        g = ps[ls > -70.0]
        if g.size:
            rel = _lk(np.array([g.mean()]))[0] - 20.0
            vals = ls[(ls > -70.0) & (ls > rel)]
            if vals.size > 1:
                out["lra"] = float(np.percentile(vals, 95) - np.percentile(vals, 10))
    return out


def peak_window_lufs(x: np.ndarray, win_s: float = 0.1) -> float:
    """Loudness K-ponderada maxima en ventana corta (sonidos breves: SFX)."""
    x = as_stereo(x)
    z = k_weight(x)
    win = max(1, int(win_s * SR))
    if z.shape[0] < win:
        z = np.concatenate([z, np.zeros((win - z.shape[0], 2))], axis=0)
    p = _block_power(z * z, win, max(1, win // 8)).sum(axis=1)
    return float(_lk(p).max()) if p.size else -math.inf


# ---------------------------------------------------------------- E/S
def _ffmpeg_bin() -> str:
    try:
        import paths as _paths
        return str(_paths.FFMPEG)
    except Exception:  # pragma: no cover
        return "ffmpeg"


def load_audio(path: str | Path, sr: int = SR) -> np.ndarray:
    """Lee cualquier audio -> float64 (n,2) a `sr`. libsndfile primero, ffmpeg de respaldo
    (m4a/aac/mp4 o formatos que libsndfile no conozca)."""
    path = Path(path)
    data = None
    fsr = sr
    try:
        import soundfile as sf
        data, fsr = sf.read(str(path), dtype="float64", always_2d=True)
    except Exception:
        data = None
    if data is None:
        cmd = [_ffmpeg_bin(), "-v", "error", "-i", str(path), "-vn", "-ac", "2", "-ar", str(sr),
               "-f", "f32le", "-"]
        raw = subprocess.run(cmd, check=True, capture_output=True).stdout
        data = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)
        fsr = sr
    data = as_stereo(data)
    # NaN/inf de un wav ajeno o de una cache vieja (voz de estudio con NaN en la cola): a 0. Un
    # solo NaN en la voz contagia la actividad de voz y con ella musica, SFX y el bus: mezcla muda
    if not np.isfinite(data).all():
        data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    if fsr != sr and data.shape[0]:
        g = math.gcd(int(fsr), int(sr))
        data = signal.resample_poly(data, sr // g, int(fsr) // g, axis=0)
    return data


def write_wav(path: str | Path, x: np.ndarray, sr: int = SR, subtype: str = "PCM_24") -> None:
    """Escribe WAV (recorta a [-1,1]; NaN/inf -> 0 como red de seguridad)."""
    import soundfile as sf
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    y = np.nan_to_num(np.asarray(x, dtype=np.float32), nan=0.0, posinf=1.0, neginf=-1.0)
    sf.write(str(path), np.clip(y, -1.0, 1.0), sr, subtype=subtype)


def fit_length(x: np.ndarray, n: int) -> np.ndarray:
    if x.shape[0] >= n:
        return x[:n]
    return np.concatenate([x, np.zeros((n - x.shape[0], x.shape[1]), dtype=x.dtype)], axis=0)


# ---------------------------------------------------------------- curvas
def _rc(p: np.ndarray) -> np.ndarray:
    """Coseno alzado 0..1 (fundidos suaves)."""
    p = np.clip(p, 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * p)


def _voice_level_ms(voice: np.ndarray) -> np.ndarray | None:
    """Nivel (dB) de la voz en banda 120-4500 Hz, RMS de 20 ms centrado, a CTRL_HZ.
    Por bloques (memoria acotada). None si la voz es silencio digital."""
    v2 = _ch2(voice)
    n = v2.shape[0]
    hop = SR // CTRL_HZ
    n_ctrl = int(math.ceil(n / SR * CTRL_HZ))
    if n_ctrl == 0:
        return None
    sos = _bp_sos(120.0, 4500.0)
    zi = np.zeros((sos.shape[0], 2))
    e = np.zeros(n_ctrl + 1)
    for a in range(0, n, _CHUNK):          # _CHUNK es multiplo de hop
        b = min(n, a + _CHUNK)
        s = np.asarray(v2[a:b], dtype=np.float64).mean(axis=1)
        y, zi = signal.sosfilt(sos, s, zi=zi)
        y2 = y * y
        k = int(math.ceil(y2.shape[0] / hop))
        if y2.shape[0] < k * hop:
            y2 = np.concatenate([y2, np.zeros(k * hop - y2.shape[0])])
        e[a // hop:a // hop + k] = y2.reshape(k, hop).sum(axis=1)
    e = e[:n_ctrl]
    if not np.any(e):
        return None
    w = int(0.020 * CTRL_HZ)
    cs = np.concatenate([[0.0], np.cumsum(np.concatenate([np.zeros(w // 2), e, np.zeros(w)]))])
    ms = (cs[w:w + n_ctrl] - cs[:n_ctrl]) / (w * hop)
    return 10 * np.log10(np.maximum(ms, 1e-14))


def _activity(lv: np.ndarray, *, hold_s: float, lookahead_s: float, attack_s: float,
              release_s: float, range_db: float = 20.0, knee_db: float = 6.0,
              min_event_s: float = 0.03) -> np.ndarray:
    """Nivel de voz (dB a CTRL_HZ) -> curva de actividad 0..1 para sidechain."""
    loud = lv[lv > lv.max() - 60]
    top = np.percentile(loud, 95) if loud.size else lv.max()
    thr = top - range_db
    act = np.clip((lv - (thr - knee_db / 2)) / knee_db, 0.0, 1.0)
    # apertura morfologica: descarta chasquidos/respiraciones mas cortos que min_event_s
    me = int(min_event_s * CTRL_HZ)
    if me > 1:
        act = maximum_filter1d(minimum_filter1d(act, size=me, mode="nearest"), size=me, mode="nearest")
    # hold hacia el futuro + look-ahead hacia el pasado: la salida en n es max(act[n-hold .. n+la]),
    # es decir, la actividad empieza `la` ANTES de la voz y dura `hold` DESPUES de ella.
    h = int(hold_s * CTRL_HZ)
    la = int(lookahead_s * CTRL_HZ)
    size = h + la + 1
    if size > 1:
        # maximum_filter1d cubre [n - size//2 - origin, n + (size-1)//2 - origin]
        origin = h - size // 2
        origin = int(max(-(size // 2), min((size - 1) // 2, origin)))
        act = maximum_filter1d(act, size=size, origin=origin, mode="nearest")
    # suavizado asimetrico (tiempos 10-90 % -> tau = t/2.2)
    ka = math.exp(-1.0 / max(attack_s / 2.2 * CTRL_HZ, 1e-6))
    kr = math.exp(-1.0 / max(release_s / 2.2 * CTRL_HZ, 1e-6))
    out = np.empty_like(act)
    y = float(act[0])
    for i, a in enumerate(act.tolist()):
        k = ka if a > y else kr
        y = a + (y - a) * k
        out[i] = y
    return out


_VOICE_ACT = dict(hold_s=0.16, lookahead_s=0.04, attack_s=0.040, release_s=0.250)
_MUSIC_ACT = dict(hold_s=MUSIC_HOLD_S, lookahead_s=0.08, attack_s=0.060, release_s=MUSIC_RELEASE_S)


def voice_activity(voice: np.ndarray, *, hold_s: float = 0.16, lookahead_s: float = 0.04,
                   attack_s: float = 0.040, release_s: float = 0.250,
                   range_db: float = 20.0, knee_db: float = 6.0, min_event_s: float = 0.03) -> np.ndarray:
    """Curva de actividad de voz a CTRL_HZ (0..1) lista para sidechain.

    RMS de 20 ms en banda 120-4500 Hz; umbral relativo al nivel alto de la voz (p95) menos
    `range_db` con rodilla suave; apertura de `min_event_s` (ignora clicks de boca y ruidos);
    look-ahead (la curva sube `lookahead_s` antes de la silaba) y hold (se mantiene `hold_s`
    despues: une los huecos entre palabras); suavizado asimetrico con tiempos 10-90 %.
    """
    n_ctrl = int(math.ceil(_ch2(voice).shape[0] / SR * CTRL_HZ))
    lv = _voice_level_ms(voice)
    if lv is None:
        return np.zeros(n_ctrl)
    return _activity(lv, hold_s=hold_s, lookahead_s=lookahead_s, attack_s=attack_s,
                     release_s=release_s, range_db=range_db, knee_db=knee_db, min_event_s=min_event_s)


def music_activity(voice: np.ndarray) -> np.ndarray:
    """Actividad para el ducking de la musica: hold largo (la cama no sube en pausas cortas)."""
    return voice_activity(voice, **_MUSIC_ACT)


def ctrl_to_audio(curve: np.ndarray, n: int, a: int = 0) -> np.ndarray:
    """Curva a CTRL_HZ -> tasa de audio (interpolacion lineal) para las muestras [a, a+n)."""
    if curve.size == 0:
        return np.zeros(n)
    tc = np.arange(curve.size) / CTRL_HZ
    ta = (a + np.arange(n)) / SR
    return np.interp(ta, tc, curve)


@lru_cache(maxsize=8)
def _lp_sos(fc: float):
    return signal.butter(2, fc, btype="lowpass", fs=SR, output="sos")


@lru_cache(maxsize=8)
def _hp_sos(fc: float):
    return signal.butter(2, fc, btype="highpass", fs=SR, output="sos")


@lru_cache(maxsize=16)
def _bp_sos(lo: float, hi: float, order: int = 2):
    return signal.butter(order, [lo, hi], btype="bandpass", fs=SR, output="sos")


def voice_band(x: np.ndarray, lo: float = 250.0, hi: float = 5000.0, *, pad_s: float = 0.1) -> np.ndarray:
    """Banda media (lo..hi) de fase cero: x - graves - agudos (reconstruccion exacta).
    Por bloques con solape (la respuesta de los filtros decae en pocos ms). float32."""
    x2 = _ch2(x)
    n = x2.shape[0]
    out = np.empty((n, x2.shape[1]), dtype=np.float32)
    pad = int(pad_s * SR)
    for a in range(0, n, _CHUNK):
        b = min(n, a + _CHUNK)
        a0, b0 = max(0, a - pad), min(n, b + pad)
        s = np.asarray(x2[a0:b0], dtype=np.float64)
        band = s - signal.sosfiltfilt(_lp_sos(lo), s, axis=0) - signal.sosfiltfilt(_hp_sos(hi), s, axis=0)
        out[a:b] = band[a - a0:b - a0]
    return out


# ---------------------------------------------------------------- limitador
def _gr_curve(need: np.ndarray, la: int, release_db_s: float) -> np.ndarray:
    """Reduccion requerida (dB >= 0 por muestra) -> reduccion suavizada (dB, float32).

    - max en ventana +-la (ataque anticipado por el look-ahead)
    - release lineal en dB (retencion de pico con caida constante, vectorizada con cummax)
    - media movil de longitud la (sin esquinas) que nunca queda debajo de lo requerido en el pico
    """
    n = need.shape[0]
    held = maximum_filter1d(np.asarray(need, dtype=np.float32), size=2 * la + 1, mode="constant")
    ramp = np.arange(n, dtype=np.float32)
    ramp *= np.float32(release_db_s / SR)
    rel = held + ramp
    np.maximum.accumulate(rel, out=rel)
    rel -= ramp
    del ramp
    np.maximum(rel, held, out=rel)
    del held
    return uniform_filter1d(rel, size=la, mode="nearest")


def limit_true_peak(x: np.ndarray, ceiling_db: float = TRUE_PEAK_DB, *, lookahead_s: float = 0.003,
                    release_db_s: float = 40.0) -> tuple[np.ndarray, float]:
    """Limitador de true peak con look-ahead. Devuelve (y, reduccion_max_dB).

    Picos por muestra desde sobremuestreo 4x; ganancia de _gr_curve; lo que quede (raro) se
    recorta a nivel de muestra. Conserva float32 si la entrada es float32."""
    x = np.asarray(x)
    if x.ndim != 2 or x.shape[1] != 2:
        x = as_stereo(x)
    n = x.shape[0]
    if n == 0:
        return x, 0.0
    ceil_db = ceiling_db - 0.15  # margen para el error de interpolacion
    pk = _tp_per_sample(x)
    need = 20.0 * np.log10(np.maximum(pk, np.float32(1e-12)))
    need -= np.float32(ceil_db)
    np.maximum(need, 0.0, out=need)
    if need.max() <= 0:
        return x, 0.0
    la = max(1, int(lookahead_s * SR))
    sm = _gr_curve(need, la, release_db_s)
    del need, pk
    g = np.power(np.float32(10.0), -sm / np.float32(20.0)).astype(x.dtype if x.dtype == np.float32 else np.float64)
    y = x * g[:, None]
    hard = undb(ceiling_db - 0.05)
    np.clip(y, -hard, hard, out=y)
    return y, float(sm.max())


# ---------------------------------------------------------------- musica
def _music_file(cfg: dict) -> str | None:
    for k in ("wav", "file", "path"):
        v = cfg.get(k)
        if v and isinstance(v, (str, Path)) and Path(str(v)).exists():
            return str(v)
    return None


def _loop_fit(m: np.ndarray, n: int, xfade_s: float = 0.6) -> np.ndarray:
    """Ajusta la musica a n muestras (float32): recorta, o repite con fundido de igual potencia.
    Buffer de salida reservado de una vez: O(n) aunque la pista sea muy corta."""
    ch = m.shape[1] if m.ndim == 2 else 1
    L = m.shape[0]
    if L >= n:
        return np.ascontiguousarray(m[:n], dtype=np.float32)
    out = np.zeros((n, ch), dtype=np.float32)
    if L == 0:
        return out
    xf = int(min(xfade_s * SR, L // 4))
    if xf < 2:
        reps = int(math.ceil(n / L))
        return np.ascontiguousarray(np.tile(np.asarray(m, np.float32), (reps, 1))[:n])
    p = np.linspace(0.0, 1.0, xf, dtype=np.float32)[:, None]
    fin = np.sin(p * np.float32(np.pi / 2))
    fout = np.cos(p * np.float32(np.pi / 2))
    step = L - xf
    pos = 0
    first = True
    while pos < n:
        seg = np.array(m, dtype=np.float32, copy=True)
        if not first:
            seg[:xf] *= fin
        if pos + step < n:
            seg[-xf:] *= fout
        k = min(L, n - pos)
        out[pos:pos + k] += seg[:k]
        pos += step
        first = False
    return out


def _ending_mode(cfg: dict) -> str | None:
    e = cfg.get("ending")
    mode = e.get("mode") if isinstance(e, dict) else e
    return mode if mode in ("natural", "hit", "fade") else None


def _parse_stops(cfg: dict) -> list[dict]:
    """stops normalizados: {at, dur, mode (tape|cut), tape (s de frenado), fade, fade_back}."""
    out = []
    for s in _as_list(cfg.get("stops")):
        if not isinstance(s, dict):
            continue
        at = _num(s.get("at"), math.nan)
        if not math.isfinite(at):
            continue
        dur = max(0.0, _num(s.get("dur"), 0.6))
        mode = str(s.get("mode") or "").lower()
        fade = s.get("fade")
        if mode not in ("cut", "tape"):
            # compatibilidad: un fade corto explicito era un corte seco
            mode = "cut" if (fade is not None and _num(fade, 1.0) < 0.15) else "tape"
        if mode == "tape":
            T = max(0.15, _num(fade, TAPE_S)) if fade is not None else TAPE_S
            T = min(T, max(0.0, at))
            fdn = 0.02
        else:
            T = 0.0
            fdn = max(_num(fade, 0.025), 1e-3)
        fup = max(_num(s.get("fade_back"), 0.06), 1e-3)
        out.append({"at": at, "dur": dur, "mode": mode, "tape": T, "fade": fdn, "fade_back": fup})
    return out


def music_gain_curve(duration: float, cfg: dict, act: np.ndarray, music_lufs: float,
                     voice_ref: float = REF_LUFS) -> tuple[np.ndarray, dict]:
    """Curva de ganancia lineal (banda ancha) de la musica a CTRL_HZ y datos de nivel.

    act: actividad para la musica (music_activity). El carve (EQ dinamico en la banda de la voz)
    forma parte del presupuesto de duck: info['voice_carve_db'] es lo que mix debe aplicar
    en 250 Hz-5 kHz ademas de esta curva. Devuelve (gain_lin_ctrl, info)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    duration = max(0.0, _num(duration, 0.0))
    nc = int(math.ceil(duration * CTRL_HZ))
    tc = np.arange(nc) / CTRL_HZ
    act = np.asarray(act, dtype=np.float64).ravel()
    act = fit_length(act[:, None], nc)[:, 0] if act.size else np.zeros(nc)
    music_lufs = _num(music_lufs, -20.0)
    voice_ref = _num(voice_ref, REF_LUFS)
    gain_db = _num(cfg.get("gain_db"), -17.0)
    duck = abs(_num(cfg.get("duck_db"), -9.0))
    carve = abs(_num(cfg.get("voice_carve_db", VOICE_CARVE_DB), VOICE_CARVE_DB))
    under = voice_ref + gain_db                    # LUFS objetivo de la cama (banda de la voz)
    open_ = min(under + duck, voice_ref + OPEN_MAX_LU)
    open_ = max(open_, under)
    total = open_ - under                          # duck efectivo total (>= 0)
    carve = min(carve, total)
    broad = total - carve                          # parte de banda ancha
    base = open_ - music_lufs                      # dB a aplicar en huecos
    curve_db = base - broad * act
    # swells
    for s in _as_list(cfg.get("swells")):
        if not isinstance(s, dict):
            continue
        a, b = _num(s.get("start"), math.nan), _num(s.get("end"), math.nan)
        if not (math.isfinite(a) and math.isfinite(b)) or b <= a:
            continue
        d = _num(s.get("db"), 4.0)
        ramp = max(_num(s.get("ramp"), 0.25), 1e-3)
        up = _rc((tc - (a - ramp)) / ramp)
        dn = 1.0 - _rc((tc - b) / ramp)
        curve_db = curve_db + d * np.minimum(up, dn)
    g = 10.0 ** (curve_db / 20.0)
    # fades de entrada/salida (si music.py ya hizo el final, solo fundidos de seguridad)
    fi = max(0.0, _num(cfg.get("fade_in"), 0.2))
    fo = max(0.0, _num(cfg.get("fade_out"), 1.5))
    emode = _ending_mode(cfg)
    if emode:
        fo = min(fo, 0.03)
        fi = min(fi, 0.006)
    # entrada suave pedida (inspector/director): se respeta TAMBIEN con arranque horneado
    intro = max(0.0, _num(cfg.get("intro_fade"), 0.0))
    if intro > fi:
        fi = intro
    if fi > 0:
        g = g * _rc(tc / fi)
    if fo > 0:
        g = g * _rc((duration - tc) / fo)
    # stops: silencio desde `at` hasta at+dur, regreso con fade_back
    stops = _parse_stops(cfg)
    for s in stops:
        at, dur, fdn, fup = s["at"], s["dur"], s["fade"], s["fade_back"]
        down = 1.0 - _rc((tc - (at - fdn)) / fdn)
        back = _rc((tc - (at + dur)) / fup)
        g = g * np.maximum(down, back)
    info = {"under_lufs": under, "open_lufs": open_, "duck_db": -total, "broadband_duck_db": -broad,
            "voice_carve_db": -carve, "base_gain_db": base, "fade_in": fi, "fade_out": fo,
            "intro_fade": intro}
    return g, info


def _tv_lowpass(x: np.ndarray, fc: np.ndarray, nper: int = 512, hop: int = 64) -> np.ndarray:
    """Pasabajos de 2o orden variante en el tiempo por STFT (sin zipper). x (L,c), fc (L,)."""
    L = x.shape[0]
    if L < nper:
        return x
    f, tt, Z = signal.stft(x.T, fs=SR, window="hann", nperseg=nper, noverlap=nper - hop,
                           boundary="zeros", padded=True)
    fct = np.interp(tt, np.arange(L) / SR, fc)
    G = 1.0 / np.sqrt(1.0 + (f[:, None] / np.maximum(fct[None, :], 20.0)) ** 4)
    _, y = signal.istft(Z * G[None], fs=SR, window="hann", nperseg=nper, noverlap=nper - hop, boundary=True)
    y = y.T[:L]
    if y.shape[0] < L:
        y = np.concatenate([y, np.zeros((L - y.shape[0], y.shape[1]))], axis=0)
    return y


def _tape_brake(m: np.ndarray, at: float, T: float) -> bool:
    """Tape stop sobre la propia pista: en [at-T, at] la lectura se frena (velocidad (1-u)^1.8,
    baja el tono como un disco), el filtro se cierra y el nivel cae. In place."""
    a1 = min(m.shape[0], int(round(at * SR)))
    a0 = max(0, int(round((at - T) * SR)))
    L = a1 - a0
    if L < int(0.05 * SR):
        return False
    u = np.arange(L) / L
    speed = (1.0 - u) ** 1.8
    pos = np.cumsum(speed) - speed[0]
    src = np.asarray(m[a0:a1], dtype=np.float64)
    idx = np.arange(L)
    seg = np.stack([np.interp(pos, idx, src[:, c]) for c in range(src.shape[1])], axis=1)
    seg = _tv_lowpass(seg, 120.0 + 11000.0 * speed ** 1.5)
    seg *= (np.clip(speed * 3.0, 0.0, 1.0) ** 0.5)[:, None]
    k = min(L, int(0.005 * SR))                 # entrada sin escalon (el filtro ya actua)
    w = _rc(np.linspace(0.0, 1.0, k))[:, None]
    seg[:k] = src[:k] * (1 - w) + seg[:k] * w
    m[a0:a1] = seg.astype(m.dtype)
    return True


# ---------------------------------------------------------------- SFX
ALIASES = {
    "whoosh": "whoosh_fast", "whoosh_hard": "whoosh_fast", "swoosh": "swoosh_up",
    "impact": "impact_soft", "hit": "impact_soft", "boom": "impact_boom", "thud": "impact_soft",
    "bling": "sparkle", "shine": "sparkle", "glitter": "sparkle", "ka_ching": "cash",
    "register": "cash", "money": "cash", "camera": "shutter", "type": "typing",
    "keyboard": "typing", "typewriter": "typing", "riser": "riser_short", "drop": "sub_drop",
    "tape_stop": "record_stop", "rip": "paper_rip", "write": "pen_scribble", "heart": "heartbeat",
    "alert": "notification", "check": "success", "blip": "pop_soft", "bubble_pop": "bubble",
    "transition": "whoosh_fast", "swish": "whoosh_fast",
    # espanol (nombres que proponen el LLM o el usuario)
    "transicion": "whoosh_fast", "impacto": "impact_soft", "golpe": "impact_soft",
    "golpe_fuerte": "impact_boom", "explosion": "impact_boom", "campana": "bell",
    "campanita": "ding", "brillo": "sparkle", "destello": "sparkle", "camara": "shutter",
    "foto": "shutter", "maquina_de_escribir": "typing", "tecleo": "typing", "teclado": "typing",
    "dinero": "cash", "caja_registradora": "cash", "precio": "cash", "moneda": "coin",
    "latido": "heartbeat", "corazon": "heartbeat", "papel": "paper_rip", "plumon": "pen_scribble",
    "garabato": "pen_scribble", "exito": "success", "notificacion": "notification",
    "burbuja": "bubble", "subida": "riser_short", "caida": "sub_drop", "freno": "record_stop",
    "disco": "record_stop", "deslizar": "swipe", "boton": "click", "reloj": "tick",
    "latigazo": "whip", "barrido": "whoosh_soft",
}
_SFX_CACHE: dict[tuple[str, float], np.ndarray] = {}


def _catalog() -> dict:
    """Catalogo de SFX. Si falta y la carpeta es escribible (desarrollo/CI) lo genera en serie;
    en la app empaquetada (solo lectura) devuelve {} y los SFX se omiten sin romper la mezcla."""
    from . import assets
    try:
        cat = assets.sfx_catalog()
    except Exception:
        cat = {}
    if not cat:
        d = Path(assets.SFX_DIR)
        writable = os.access(d, os.W_OK) if d.exists() else os.access(d.parent, os.W_OK)
        if writable:
            try:
                from . import sfx as _sfx
                _sfx.generate_all(workers=1)
                cat = assets.sfx_catalog()
            except Exception:
                cat = {}
    return cat


def _norm_name(s: Any) -> str:
    k = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[\s\-]+", "_", k.strip().lower())


def parse_sfx_name(name: Any, cat: dict) -> tuple[str | None, int | None]:
    """(nombre del catalogo, variante 0-based sugerida por un sufijo `_N`) para `name`.

    Orden: exacto, sufijo de variante (pop_2 -> pop, variante 1), alias (ingles y espanol),
    prefijo (>= 3 letras: el nombre mas largo que sea prefijo), tags del catalogo."""
    if not name:
        return None, None
    k = _norm_name(name)
    if not k:
        return None, None
    if k in cat:
        return k, None

    def simple(q: str) -> str | None:
        if q in cat:
            return q
        a = ALIASES.get(q)
        return a if a in cat else None

    m = re.match(r"^(.*?)_?(\d+)$", k)
    if m and m.group(1):
        base = simple(m.group(1))
        if base is not None:
            num = int(m.group(2))
            return base, (num - 1 if num >= 1 else None)
    a = simple(k)
    if a is not None:
        return a, None
    if len(k) >= 3:
        longer = [c for c in cat if k.startswith(c)]
        if longer:
            return max(longer, key=len), None
        shorter = sorted(c for c in cat if c.startswith(k))
        if shorter:
            return shorter[0], None
    for c in sorted(cat):
        tags = {_norm_name(t) for t in (cat[c].get("tags") or [])}
        if k in tags:
            return c, None
    return None, None


def resolve_sfx(name: str, cat: dict) -> str | None:
    """Nombre del catalogo para `name` (exacto, alias, prefijo o tag)."""
    return parse_sfx_name(name, cat)[0]


def key_root(keyscale: Any) -> int | None:
    """Clase de altura (0 = Do) de la tonica MAYOR para una tonalidad ("A minor" -> Do, porque
    los SFX tonales son arpegios mayores: sobre la relativa mayor quedan diatonicos)."""
    if not keyscale or not isinstance(keyscale, str):
        return None
    m = re.match(r"^\s*([A-Ga-g])\s*(#|b|sharp|flat)?\s*(.*)$", keyscale.strip())
    if not m:
        return None
    pc = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}[m.group(1).lower()]
    acc = (m.group(2) or "").lower()
    if acc in ("#", "sharp"):
        pc += 1
    elif acc in ("b", "flat"):
        pc -= 1
    rest = m.group(3).strip().lower()
    minor = rest.startswith("min") or rest == "m" or rest.startswith("m ") or rest.startswith("moll")
    return (pc + (3 if minor else 0)) % 12


def _variants(entry: dict) -> list[dict]:
    v = entry.get("variants")
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return v
    return [{"file": entry["file"], "dur": entry.get("dur", 0.0), "hit": entry.get("hit", 0.0)}]


def _load_sfx(path: Path) -> np.ndarray:
    key = (str(path), path.stat().st_mtime if path.exists() else 0.0)
    a = _SFX_CACHE.get(key)
    if a is None:
        a = load_audio(path)
        _SFX_CACHE[key] = a
    return a


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "little")


def _pan_gains(p: float) -> tuple[float, float]:
    """Balance de potencia constante sin realce en el centro (fuentes estereo)."""
    p = _clamp(_num(p, 0.0), -1.0, 1.0)
    th = (p + 1.0) * math.pi / 4.0
    return min(1.0, math.sqrt(2) * math.cos(th)), min(1.0, math.sqrt(2) * math.sin(th))


def _repitch(x: np.ndarray, semis: float) -> np.ndarray:
    if abs(semis) < 1e-3:
        return x
    ratio = 2.0 ** (semis / 12.0)       # >1 = mas agudo = mas corto
    up, down = 1000, int(round(1000 * ratio))
    g = math.gcd(up, down)
    return signal.resample_poly(x, up // g, down // g, axis=0)


def _fit_dur(x: np.ndarray, hs: int, dur: float, loop: list | None, category: str,
             ratio: float) -> tuple[np.ndarray, int, dict]:
    """Ajusta un SFX a `dur` segundos audibles.

    - risers: dur = anticipacion (segundos antes del golpe): se recorta la cabeza y entra con
      fundido; no se alarga.
    - resto: el sonido dura de `at` a at+dur: si es mas corto que el archivo se corta con fundido
      (40 ms en texturas/ui, hasta 120 ms en el resto); si es mas largo y el SFX tiene zona
      sostenida (`loop` del catalogo) se repite esa zona con fundidos de igual potencia de 30 ms.
    """
    d = int(round(dur * SR))
    L = x.shape[0]
    if category == "riser":
        if d < hs:
            y = x[hs - d:].copy()
            fi = max(1, int(min(0.5 * d, 0.4 * SR)))
            y[:fi] *= (_rc(np.linspace(0.0, 1.0, fi)) ** 1.5)[:, None]
            return y, d, {"mode": "recorte_cabeza"}
        return x, hs, {"mode": "natural"}
    E = hs + d
    fo = int(0.04 * SR) if category in ("texture", "ui") else int(min(0.12 * SR, max(0.04 * SR, 0.3 * d)))
    if E < L:
        y = x[:E].copy()
    elif loop and len(loop) == 2:
        a = int(round(float(loop[0]) / ratio * SR))
        b = int(round(float(loop[1]) / ratio * SR))
        xf = int(0.03 * SR)
        if not (0 <= a < b <= L and b - a > 2 * xf):
            return x, hs, {"mode": "natural"}
        body = x[a:b]
        y = np.zeros((max(E, b), x.shape[1]), dtype=x.dtype)
        y[:b] = x[:b]
        pos = b
        p = np.linspace(0.0, 1.0, xf)[:, None]
        cw, sw = np.cos(p * np.pi / 2), np.sin(p * np.pi / 2)
        while pos < E:
            y[pos - xf:pos] = y[pos - xf:pos] * cw + body[:xf] * sw
            k = min(body.shape[0] - xf, E - pos)
            y[pos:pos + k] = body[xf:xf + k]
            pos += k
        y = y[:E]
    else:
        return x, hs, {"mode": "natural"}
    fo = max(1, min(fo, y.shape[0]))
    y[-fo:] *= (1.0 - _rc(np.linspace(0.0, 1.0, fo)))[:, None]
    return y, hs, {"mode": "corte" if E < L else "loop"}


def _duck_low_tail(seg: np.ndarray, start: int, hs: int, act: np.ndarray,
                   depth_db: float = IMPACT_TAIL_DUCK_DB) -> np.ndarray:
    """Agacha los graves (< 150 Hz) de la cola de un impacto mientras hay voz (sin lodo bajo la
    fundamental de la voz). seg empieza en la muestra absoluta `start`; hs = golpe en seg."""
    t0 = hs + int(0.15 * SR)
    if t0 >= seg.shape[0] - 16:
        return seg
    low = signal.sosfiltfilt(_lp_sos(150.0), seg, axis=0)
    w = _rc((np.arange(seg.shape[0]) - t0) / (0.05 * SR))
    a = ctrl_to_audio(act, seg.shape[0], start)
    gl = 1.0 - w * (1.0 - undb(depth_db) ** a)
    return seg - low * (1.0 - gl)[:, None]


# categorias con leve humanizacion (varias repeticiones seguidas suenan naturales)
_HUMANIZE = {"ui", "tonal"}


def build_sfx_bus(n: int, events: list[dict], *, seed: int = 7, sfx_db: float = 0.0,
                  humanize: bool = True, cat: dict | None = None, key_pc: int | None = None,
                  act: np.ndarray | None = None,
                  tape_windows: list[tuple[float, float]] | None = None) -> tuple[np.ndarray, list, list]:
    """Suma los eventos alineados por hit. Devuelve (bus (n,2) float32, colocados, omitidos).

    Cada evento se procesa aislado: un valor invalido lo manda a `omitidos` sin tirar la mezcla.
    key_pc: tonica mayor de la musica (key_root) para transponer los SFX con `root`.
    act: actividad de voz a CTRL_HZ (agacha la cola grave de los impactos).
    tape_windows: [(inicio, fin)] de los tape stops de la musica (reemplazan a record_stop)."""
    from . import assets
    cat = _catalog() if cat is None else cat
    bus = np.zeros((n, 2), dtype=np.float32)
    placed: list[dict] = []
    skipped: list[dict] = []
    last_var: dict[str, int] = {}
    evs = []
    if not isinstance(events, (list, tuple)):
        events = []
    for idx, ev in enumerate(events):
        if not isinstance(ev, dict):
            skipped.append({"name": None, "reason": "evento invalido"})
            continue
        if ev.get("enabled") is False:
            continue
        at = _num(ev.get("at"), math.nan)
        if not math.isfinite(at):
            skipped.append({"name": ev.get("name"), "reason": "valor invalido: at"})
            continue
        evs.append((at, idx, ev))
    evs.sort(key=lambda e: (e[0], e[1]))
    for at, idx, ev in evs:
        try:
            name, sv = parse_sfx_name(ev.get("name", ""), cat)
            if name is None:
                skipped.append({"name": ev.get("name"), "reason": "no existe en el catalogo"})
                continue
            if name == "record_stop" and tape_windows and any(a - 0.35 <= at <= b + 0.35 for a, b in tape_windows):
                skipped.append({"name": name, "reason": "reemplazado por el tape stop de la musica", "at": at})
                continue
            entry = cat[name]
            vs = _variants(entry)
            key = f"{seed}:{name}:{ev.get('id', idx)}"
            if ev.get("variant") is not None:
                vi = int(_num(ev.get("variant"), 0)) % len(vs)
            elif sv is not None:
                vi = sv % len(vs)
            else:
                vi = _h(key) % len(vs)
                if len(vs) > 1 and last_var.get(name) == vi:
                    vi = (vi + 1) % len(vs)
            last_var[name] = vi
            v = vs[vi]
            path = Path(assets.SFX_DIR) / v["file"]
            if not path.exists():
                skipped.append({"name": name, "reason": f"falta {v['file']}"})
                continue
            x = _load_sfx(path)
            category = entry.get("category", "")
            pitch_given = math.isfinite(_num(ev.get("pitch"), math.nan))
            semis = _clamp(_num(ev.get("pitch"), 0.0), -24.0, 24.0)
            gdb = _clamp(_num(ev.get("gain_db"), 0.0), -60.0, 12.0) + sfx_db
            root = v.get("root", entry.get("root"))
            tr = 0
            if key_pc is not None and root is not None and not pitch_given:
                tr = (int(key_pc) - int(root)) % 12
                if tr > 6:
                    tr -= 12
                semis += tr
            if humanize and category in _HUMANIZE:
                if not pitch_given and (key_pc is None or root is None):
                    r = (_h(key + ":hz") % 10_000) / 10_000.0
                    semis += (r - 0.5) * 0.5          # +-0.25 semitonos
                gdb += ((_h(key + ":g") % 1000) / 1000.0 - 0.5) * 1.2
            x = _repitch(x, semis)
            ratio = 2.0 ** (semis / 12.0)
            hit = float(v.get("hit", entry.get("hit", 0.0))) / ratio
            hs = int(round(hit * SR))
            dur = _num(ev.get("dur"), math.nan)
            dmode = None
            if math.isfinite(dur) and dur > 0:
                x, hs, dinfo = _fit_dur(x, hs, dur, v.get("loop"), category, ratio)
                dmode = dinfo["mode"]
            start = int(round(at * SR)) - hs
            if start >= n or at < -1.0 or start + x.shape[0] <= 0:
                skipped.append({"name": name, "reason": "fuera de la duracion", "at": at})
                continue
            gl, gr = _pan_gains(ev.get("pan", 0.0))
            seg = x * (np.array([gl, gr]) * undb(gdb))
            a0 = 0
            if start < 0:
                a0 = -start
                start = 0
                seg = seg[a0:].copy()
                k = min(seg.shape[0], int(0.004 * SR))
                if k > 0:
                    seg[:k] *= _rc(np.linspace(0, 1, k))[:, None]
            if category == "impact" and act is not None and act.size:
                seg = _duck_low_tail(seg, start, hs - a0, act)
            end = min(n, start + seg.shape[0])
            seg = seg[: end - start]
            if end == n and seg.shape[0] > 0 and start + x.shape[0] - a0 > n:
                k = min(seg.shape[0], int(0.012 * SR))
                seg[-k:] *= (1.0 - _rc(np.linspace(0, 1, k)))[:, None]
            bus[start:end] += seg.astype(np.float32)
            item = {"name": name, "variant": vi, "variant_label": vi + 1, "file": v["file"],
                    "at": round(at, 4), "start": round(start / SR, 4), "hit": round(hs / SR, 4),
                    "gain_db": round(gdb, 2), "pitch": round(semis, 3), "trimmed_head": a0 > 0}
            if tr:
                item["transpose"] = tr
            if dmode:
                item["dur"] = round((hs if category == "riser" else x.shape[0] - hs) / SR, 4)
                item["dur_mode"] = dmode
            if ev.get("id") is not None:
                item["id"] = ev.get("id")
            placed.append(item)
        except Exception as e:  # un evento malo nunca tira la mezcla
            skipped.append({"name": ev.get("name"), "reason": f"error: {e}"[:160]})
    return bus, placed, skipped


# ---------------------------------------------------------------- mezcla
def _gain_vec(sm: np.ndarray | None, gain: float, n: int, a: int = 0, b: int | None = None) -> np.ndarray:
    b = n if b is None else b
    if sm is None:
        return np.full(b - a, undb(gain), dtype=np.float32)
    return np.power(np.float32(10.0), (np.float32(gain) - sm[a:b]) / np.float32(20.0))


def mix(voice_wav: str | Path | None, duration: float, sfx_events: list[dict] | None,
        music_cfg: dict | None, out_wav: str | Path, *, seed: int = 7,
        target_lufs: float | None = TARGET_LUFS, true_peak: float = TRUE_PEAK_DB,
        voice_db: float = 0.0, sfx_db: float = 0.0, sfx_duck_db: float = SFX_DUCK_DB,
        humanize: bool = True, stems_dir: str | Path | None = None) -> dict:
    """Mezcla final. Ver docstring del modulo. Devuelve metricas (LUFS medidos, TP, etc.).

    target_lufs=None: sin normalizacion de loudness (solo limitador), util para previews.
    stems_dir: si se da, escribe voice.wav / music.wav / sfx.wav (post ganancia final) ahi.
    Lanza ValueError si duration no es un numero > 0.
    """
    t0 = time.perf_counter()
    dur_in = duration
    duration = _num(duration, math.nan)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"audio.mix: duration invalida ({dur_in!r}); debe ser un numero > 0 en segundos")
    n = int(round(duration * SR))
    if n <= 0:
        raise ValueError(f"audio.mix: duration demasiado corta ({dur_in!r})")
    seed = int(_num(seed, 7))
    voice_db = _num(voice_db, 0.0)
    sfx_db = _num(sfx_db, 0.0)
    sfx_duck_db = _num(sfx_duck_db, SFX_DUCK_DB)
    true_peak = _num(true_peak, TRUE_PEAK_DB)
    if target_lufs is not None:
        target_lufs = _num(target_lufs, TARGET_LUFS)
    nc = int(math.ceil(duration * CTRL_HZ))
    info: dict[str, Any] = {"wav": str(out_wav), "sr": SR, "duration": duration, "samples": n}

    # 1. voz
    voice = np.zeros((n, 2), dtype=np.float32)
    vin = -math.inf
    if voice_wav and Path(str(voice_wav)).exists():
        try:
            voice = np.ascontiguousarray(fit_length(load_audio(voice_wav), n), dtype=np.float32)
            vin = integrated_lufs(voice)
            if math.isfinite(vin):
                voice *= np.float32(undb(REF_LUFS - vin + voice_db))
        except Exception as e:
            info["voice_error"] = str(e)[:200]
            voice = np.zeros((n, 2), dtype=np.float32)
            vin = -math.inf
    has_voice = math.isfinite(vin)
    info["voice_lufs_in"] = round(vin, 2) if has_voice else None
    lv = _voice_level_ms(voice) if has_voice else None
    if lv is not None:
        act_v = fit_length(_activity(lv, **_VOICE_ACT)[:, None], nc)[:, 0]
        act_m = fit_length(_activity(lv, **_MUSIC_ACT)[:, None], nc)[:, 0]
    else:
        act_v = np.zeros(nc)
        act_m = np.zeros(nc)
        has_voice = False

    # 2. musica
    music = None
    mcfg = dict(music_cfg) if isinstance(music_cfg, dict) else {}
    use = bool(mcfg.get("enabled", True)) and mcfg.get("source") != "none"
    mfile = _music_file(mcfg) if use else None
    tape_windows: list[tuple[float, float]] = []
    kpc = None
    if mfile:
        m = None
        try:
            m = load_audio(mfile)
            off = int(max(0.0, _num(mcfg.get("offset"), 0.0)) * SR)
            if off >= m.shape[0]:
                info["music_offset_ignored"] = True
                off = 0
            m = m[off:]
            if m.shape[0] < MIN_MUSIC_S * SR:
                info["music_skipped"] = "muy corta"
                m = None
        except Exception as e:
            info["music_skipped"] = f"no se pudo leer: {e}"[:200]
            m = None
        if m is not None:
            m = _loop_fit(m, n)
            mlufs = integrated_lufs(m)
            if not math.isfinite(mlufs):
                info["music_skipped"] = "silencio"
            else:
                vref = REF_LUFS + voice_db if has_voice else REF_LUFS
                g, minfo = music_gain_curve(duration, mcfg, act_m, mlufs, voice_ref=vref)
                tapes = 0
                for s in _parse_stops(mcfg):
                    if s["mode"] == "tape" and s["tape"] > 0 and _tape_brake(m, s["at"], s["tape"]):
                        tape_windows.append((s["at"] - s["tape"], s["at"]))
                        tapes += 1
                carve = minfo["voice_carve_db"] if has_voice else 0.0
                music = np.empty_like(m)
                for a in range(0, n, _CHUNK):
                    b = min(n, a + _CHUNK)
                    ga = ctrl_to_audio(g, b - a, a).astype(np.float32)[:, None]
                    seg = m[a:b]
                    if carve < 0:
                        # EQ dinamico: hueco extra en la banda de la voz solo con voz
                        pad = int(0.1 * SR)
                        a0, b0 = max(0, a - pad), min(n, b + pad)
                        mid = voice_band(m[a0:b0])[a - a0:b - a0]
                        cf = undb(carve) ** ctrl_to_audio(act_m, b - a, a) - 1.0
                        seg = seg + mid * cf.astype(np.float32)[:, None]
                    music[a:b] = seg * ga
                del m
                kpc = key_root(mcfg.get("keyscale") or mcfg.get("key"))
                info["music"] = {"file": mfile, "lufs_in": round(mlufs, 2),
                                 **{k: round(float(v), 2) for k, v in minfo.items()},
                                 "ending": _ending_mode(mcfg), "tape_stops": tapes, "key_root": kpc}
    # 3. SFX
    sfx_bus, placed, skipped = build_sfx_bus(n, sfx_events or [], seed=seed, sfx_db=sfx_db,
                                             humanize=humanize, key_pc=kpc,
                                             act=act_v if has_voice else None,
                                             tape_windows=tape_windows)
    if sfx_duck_db and has_voice and placed:
        dk = undb(-abs(sfx_duck_db))
        for a in range(0, n, _CHUNK):
            b = min(n, a + _CHUNK)
            sfx_bus[a:b] *= (dk ** ctrl_to_audio(act_v, b - a, a)).astype(np.float32)[:, None]
    sgr = 0.0
    if placed:
        # bus de SFX con su propio limitador: un SFX muy fuerte se aplasta el, no la mezcla
        sfx_bus, sgr = limit_true_peak(sfx_bus, SFX_BUS_CEIL_DB, lookahead_s=0.002, release_db_s=80.0)
    info["sfx"] = placed
    info["sfx_skipped"] = skipped
    info["sfx_bus_gr_db"] = round(sgr, 2)

    # 4. bus + loudness + true peak
    bus = voice + sfx_bus
    if music is not None:
        bus += music
    if not stems_dir:
        del voice, sfx_bus, music
        voice = sfx_bus = music = None
    fin = np.isfinite(bus)
    if not fin.all():
        bus[~fin] = 0.0
        info["nan_fixed"] = True
    del fin
    pk = _tp_per_sample(bus)
    pk_db = 20.0 * np.log10(np.maximum(pk, np.float32(1e-9)))
    del pk
    ceil_db = true_peak - 0.15
    la = max(1, int(0.003 * SR))
    gain = 0.0
    sm = None
    lim_gr = 0.0
    short_by = 0.0

    def gr_for(gain_db: float):
        need = pk_db + np.float32(gain_db - ceil_db)
        np.maximum(need, 0.0, out=need)
        if need.max() <= 0:
            return None, 0.0
        s = _gr_curve(need, la, 40.0)
        return s, float(s.max())

    if target_lufs is not None and pk_db.size:
        lin = _gated(_win_power(_kw_sub(bus), 4))
        if math.isfinite(lin):
            B = int(0.4 * SR)
            nb = pk_db.shape[0] // B
            pmax = float(pk_db.max())
            p98 = float(np.percentile(pk_db[:nb * B].reshape(nb, B).max(axis=1), 98)) if nb >= 5 else pmax
            gain_cap = min(ceil_db + MAX_GR_DB - p98, ceil_db + MAX_GR_ABS_DB - pmax)
            gain = float(np.clip(target_lufs - lin, -30.0, 24.0))
            gain = min(gain, gain_cap)
            for it in range(6):
                sm, lim_gr = gr_for(gain)
                got = _gated(_win_power(_kw_sub(bus, _gain_vec(sm, gain, n)), 4))
                if not math.isfinite(got) or abs(got - target_lufs) < 0.08:
                    break
                new = min(float(np.clip(gain + target_lufs - got, -30.0, 30.0)), gain_cap)
                if abs(new - gain) < 1e-3 or it == 5:
                    short_by = target_lufs - got      # tope de limitacion: se queda corto
                    break
                gain = new
    else:
        sm, lim_gr = gr_for(0.0)
    y = bus * _gain_vec(sm, gain, n)[:, None]
    del bus, sm
    hard = np.float32(undb(true_peak - 0.05))
    np.clip(y, -hard, hard, out=y)
    _edge_fades(y)
    write_wav(out_wav, y)
    meas = loudness(y)
    info.update({
        "lufs": round(meas["integrated"], 2) if math.isfinite(meas["integrated"]) else None,
        "true_peak_dbtp": round(meas["true_peak"], 2) if math.isfinite(meas["true_peak"]) else None,
        "lra": round(meas["lra"], 2),
        "momentary_max": round(meas["momentary_max"], 2) if math.isfinite(meas["momentary_max"]) else None,
        "gain_db": round(gain, 2), "limiter_max_gr_db": round(lim_gr, 2),
    })
    if short_by > 0.08:
        info["lufs_short_by"] = round(short_by, 2)
    del y
    if stems_dir:
        sd = Path(stems_dir)
        gl = np.float32(undb(gain))
        write_wav(sd / "voice.wav", voice * gl)
        write_wav(sd / "music.wav", (music if music is not None else np.zeros_like(voice)) * gl)
        write_wav(sd / "sfx.wav", sfx_bus * gl)
        info["stems"] = {k: str(sd / f"{k}.wav") for k in ("voice", "music", "sfx")}
    info["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return info


END_FADE_S = 0.012      # fundido de seguridad al final de la mezcla completa
START_FADE_S = 0.002    # y al inicio (inaudible; evita un escalon si la voz arranca en alto)


def _edge_fades(y: np.ndarray) -> None:
    """Fundidos de seguridad en sitio (coseno elevado) al inicio y al final de la mezcla: la
    ultima muestra siempre es ~0 aunque la voz o un SFX terminen a media onda (clic/pop al
    final del export). No toca el final horneado de la musica (ya termina en 0)."""
    n = y.shape[0]
    for k, at_end in ((int(round(START_FADE_S * SR)), False), (int(round(END_FADE_S * SR)), True)):
        k = min(k, n // 2)
        if k <= 1:
            continue
        r = (0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, k, dtype=np.float64))).astype(np.float32)
        if at_end:
            y[n - k:] *= r[::-1, None]
        else:
            y[:k] *= r[:, None]


def ffmpeg_ebur128(path: str | Path) -> dict:
    """Mide con el filtro ebur128 de ffmpeg (verificacion independiente)."""
    cmd = [_ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
           "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    tail = err[err.rfind("Summary:"):]
    out = {}
    for line in tail.splitlines():
        s = line.strip()
        if s.startswith("I:"):
            out["integrated"] = float(s.split()[1])
        elif s.startswith("LRA:"):
            out["lra"] = float(s.split()[1])
        elif s.startswith("Peak:"):
            out["true_peak"] = float(s.split()[1])
    return out


if __name__ == "__main__":  # pragma: no cover
    import sys
    a = load_audio(sys.argv[1])
    print(json.dumps(loudness(a), indent=1))
