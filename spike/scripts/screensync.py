#!/usr/bin/env python3
"""
Grabaciones de pantalla sincronizadas por AUDIO con una toma de camara ("Armar con IA").

Un creador graba una demo con la camara y, al mismo tiempo, graba la pantalla del celular (con su
microfono). Las dos pistas oyen lo mismo durante la toma: la de pantalla trae ademas lo de antes y
despues (preparar la app, una cuenta regresiva). Este modulo encuentra ese traslape y el desfase
exacto entre los dos relojes, sin ffmpeg ni red (numpy puro):

  1. Envolvente de energia en dB (ventana de 20 ms, paso de 10 ms) de cada audio, a cualquier
     frecuencia de muestreo (los limites de cada ventana se calculan en segundos). El piso de ruido
     de cada microfono se aplana (percentil 10 + 3 dB, y no mas de 50 dB bajo el pico).
  2. Correlacion NORMALIZADA (Pearson en el traslape, no la cruda que favorece traslapes largos) para
     TODOS los desfases a la vez con FFT y sumas acumuladas: O(n log n).
  3. Aceptacion estricta (dos clips ajenos nunca pasan): traslape >= OVERLAP_MIN s con voz en los dos
     (>= VOICED_MIN s), correlacion >= CORR_MIN (mas exigente en traslapes cortos: CORR_MIN_SHORT a
     OVERLAP_MIN, CORR_MIN desde OVERLAP_FULL) y un pico UNICO: le gana por MARGIN_MIN al mejor
     desfase a mas de 0.5 s.
  4. Afinado: envolvente de 1 ms en ventanas del inicio, el medio y el final del traslape, maximo
     local a +-30 ms del pico grueso con interpolacion parabolica (precision ~1 ms). Si los tres
     coinciden en una deriva de reloj (> 20 ppm, consistente) se reporta `rate`; si no, rate = 1.

Convencion: t_pantalla = offset + rate * t_camara (segundos de cada wav). Cada wav extraido con ffmpeg
empieza en la primera muestra de audio de su archivo, que puede llegar DESPUES del primer cuadro (el
iPhone graba la pantalla con el audio 0.128 s tarde): file_offset() lo pasa a segundos de cada ARCHIVO
(los de "-ss" y del video compuesto) con el audio_lead de cada uno (video_io.audio_lead).

Marco de la grabacion (content_rect): una grabacion de una pantalla emulada (el modo de dispositivo
de un navegador, un espejo del celular en la compu) trae un MARCO alrededor de lo que importa: filas y
columnas de la orilla que no cambian en todo el tramo que se usa y son de un color parejo. Se
recorta para que la tarjeta muestre solo la pantalla.

API
  envelope(y, sr, hop=HOP, win=WIN) -> np.ndarray (dB por ventana; la ventana k empieza en k*hop s)
  match(cam_y, cam_sr, scr_y, scr_sr, env_cam=None, env_scr=None) -> dict | None
      {"offset", "rate", "corr", "margin", "overlap": [a, b] (s de la camara), "other": [a, b] (s de la
       pantalla), "voiced": s, "ok": pasa las reglas, "checks": [{"t" (s de la camara), "offset",
       "corr"}]}
  match_env(a_db, b_db, hop=HOP) -> dict | None      lo mismo con envolventes ya calculadas
  read_wav(path) -> (y float32 mono, sr) | (None, None)
  screen_time(t_cam, offset, rate=1.0) -> s de la pantalla
  pieces(segs, cam_sid, sync, scr_dur) -> [[t0, t1, cam_in]]   tramos del video compuesto (segs de
      compose: {start, end, kind, sid, in}) donde la toma de camara esta Y la pantalla tiene cuadros
  runs(pieces, gap=RUN_GAP) -> [[pieces...]]      tramos contiguos (un elemento por tramo)
  composed_to_screen(pieces, t, offset, rate=1.0) -> s de la pantalla para el segundo t del video
      compuesto (el tramo que lo contiene; fuera: el mas cercano, acotado) o None sin tramos
  file_offset(offset, cam_lead=0, scr_lead=0) -> desfase en segundos de los ARCHIVOS
  content_rect(path, t0=None, t1=None) -> {"x", "y", "w", "h", "W", "H"} (px de display) | None
      lo que hay dentro del marco estatico de la grabacion en [t0, t1] s (None: sin marco)
  crop_frac(rect) -> [x0, y0, x1, y1] (fracciones, el `crop` del render) | None
"""
from __future__ import annotations

import math
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np

HOP = 0.01                # s: envolvente gruesa (100 Hz)
WIN = 0.02                # s: ventana de energia
FINE_HOP = 0.001          # s: envolvente fina (1 kHz)
FINE_WIN = 0.010
FINE_SPAN = 0.03          # s: el afinado busca +- esto alrededor del pico grueso
FINE_WINDOW = 20.0        # s: largo de cada ventana de afinado (inicio, medio, final)
OVERLAP_MIN = 4.0         # s: traslape minimo
OVERLAP_FULL = 12.0       # s: desde aqui basta CORR_MIN
CORR_MIN = 0.80
CORR_MIN_SHORT = 0.90
VOICED_MIN = 2.0          # s con voz en el traslape (en los dos audios)
MARGIN_MIN = 0.12         # el pico le gana por esto al mejor desfase lejano
FAR = 0.5                 # s: "lejano" para el margen
DRIFT_MIN = 2e-5          # deriva de reloj minima que vale la pena (20 ppm)
DRIFT_MAX = 2e-3          # mas que esto no es deriva de reloj (es otra cosa): se ignora
RUN_GAP = 0.5             # s: tramos del video compuesto mas juntos que esto son un solo elemento
FLOOR_P = 10              # percentil del piso de ruido
FLOOR_ABOVE = 3.0         # dB sobre el piso
RANGE_DB = 50.0           # dB bajo el pico que se conservan
# marco de la grabacion (content_rect)
FRAME_N = 24              # cuadros que se miran (repartidos en el tramo que se usa)
FRAME_SIDE = 1440         # se miran a lo mas a este lado largo (una grabacion 4K se reduce)
FRAME_STATIC = 10         # niveles (0-255) que un pixel del marco puede variar entre cuadros (ruido)
FRAME_STATIC_FRAC = 0.98  # fraccion de pixeles quietos de una linea del marco
FRAME_FLAT = 20.0         # desviacion maxima del color a lo largo de una linea del marco
FRAME_DEPTH = 0.25        # lo mas que el marco ocupa de cada lado
FRAME_MIN = 2             # px: menos que esto no es marco
FRAME_INSET = 2           # px de mas (en la fuente) en cada lado con marco: su linea fina de orilla
FRAME_KEEP = 0.40         # lo que queda debe medir al menos esto del ancho y del alto
FRAME_DARK = 40.0         # bandas en solo dos lados: marco si son oscuras (letterbox)
FRAME_TONE = 20.0         # una linea del marco es de su color (a lo mas esto por canal)
FRAME_PROBE = 0.03        # franja de cada orilla donde se mide el color del marco
FRAME_EDGE = 0.02         # lineas de OTRO color pegadas a la orilla que todavia son marco (una barra)


# ----------------------------------------------------------------------------- audio
def read_wav(path):
    """wav PCM 16/24/32 bits o flotante 32 -> (mono float32, sr). (None, None) si no se puede."""
    try:
        w = wave.open(str(path))
    except (wave.Error, EOFError, OSError):
        return _read_other(path)
    with w:
        ch, sr, nf, sw = w.getnchannels(), w.getframerate(), w.getnframes(), w.getsampwidth()
        raw = w.readframes(nf)
    if sw == 2:
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, np.int32).astype(np.float32) / 2147483648.0
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float32) / float(1 << 23)
    else:
        return None, None
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(1)
    return x, int(sr)


def _read_other(path):
    try:
        import edit as E
        return np.asarray(E._mono16k(str(path)), np.float32), 16000
    except Exception:  # noqa: sin audio legible
        return None, None


def envelope(y, sr, hop: float = HOP, win: float = WIN) -> np.ndarray | None:
    """Energia en dB por ventana de `win` s con paso de `hop` s (la ventana k empieza en k*hop s del
    archivo). Los limites salen en SEGUNDOS: igual a 16, 44.1 o 48 kHz."""
    if y is None or sr is None or sr <= 0:
        return None
    y = np.asarray(y, np.float64).ravel()
    dur = len(y) / float(sr)
    n = int(math.floor((dur - win) / hop)) + 1
    if n < 8:
        return None
    c = np.concatenate([[0.0], np.cumsum(y * y)])
    a = np.rint(np.arange(n) * hop * sr).astype(np.int64)
    b = np.minimum(len(y), np.rint((np.arange(n) * hop + win) * sr).astype(np.int64))
    span = np.maximum(1, b - a)
    e = (c[b] - c[a]) / span
    return 10.0 * np.log10(e + 1e-12)


def _flat(db: np.ndarray) -> np.ndarray:
    """Piso de ruido aplanado: lo que este bajo el piso (+3 dB) o 50 dB bajo el pico no cuenta."""
    hi = float(np.percentile(db, 99.5))
    lo = max(float(np.percentile(db, FLOOR_P)) + FLOOR_ABOVE, hi - RANGE_DB)
    return np.maximum(db, lo)


def _voiced(db: np.ndarray) -> np.ndarray:
    """Ventanas con voz (bool): 10 dB sobre el piso y a menos de 25 dB del pico."""
    hi = float(np.percentile(db, 99.5))
    lo = float(np.percentile(db, FLOOR_P))
    return db > max(lo + 10.0, hi - 25.0)


# ----------------------------------------------------------------------------- correlacion
def ncc_all(a: np.ndarray, b: np.ndarray):
    """Correlacion normalizada (Pearson en el traslape) de `a` contra `b` para TODOS los desfases.
    Desfase L (en muestras): b[k + L] se compara con a[k]. -> (lags, ncc, n) con n = muestras del
    traslape."""
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    N, M = len(a), len(b)
    nfft = 1 << int(math.ceil(math.log2(N + M)))
    # centrar ayuda a la precision numerica (la correlacion no cambia)
    a = a - a.mean()
    b = b - b.mean()
    xc = np.fft.irfft(np.fft.rfft(b, nfft) * np.conj(np.fft.rfft(a, nfft)), nfft)
    lags = np.arange(-(N - 1), M)
    sab = xc[np.mod(lags, nfft)]
    ca = np.concatenate([[0.0], np.cumsum(a)])
    ca2 = np.concatenate([[0.0], np.cumsum(a * a)])
    cb = np.concatenate([[0.0], np.cumsum(b)])
    cb2 = np.concatenate([[0.0], np.cumsum(b * b)])
    k0 = np.maximum(0, -lags)
    k1 = np.minimum(N, M - lags)
    n = np.maximum(0, k1 - k0)
    sa = ca[k1] - ca[k0]
    sa2 = ca2[k1] - ca2[k0]
    sb = cb[k1 + lags] - cb[k0 + lags]
    sb2 = cb2[k1 + lags] - cb2[k0 + lags]
    nn = np.maximum(1, n)
    cov = sab - sa * sb / nn
    va = np.maximum(0.0, sa2 - sa * sa / nn)
    vb = np.maximum(0.0, sb2 - sb * sb / nn)
    den = np.sqrt(va * vb)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(den > 1e-9, cov / den, 0.0)
    return lags, np.clip(r, -1.0, 1.0), n


def corr_needed(overlap_s: float) -> float:
    """Correlacion minima para un traslape de `overlap_s` s (mas exigente si es corto)."""
    if overlap_s >= OVERLAP_FULL:
        return CORR_MIN
    p = max(0.0, min(1.0, (overlap_s - OVERLAP_MIN) / (OVERLAP_FULL - OVERLAP_MIN)))
    return CORR_MIN_SHORT + (CORR_MIN - CORR_MIN_SHORT) * p


def match_env(a_db, b_db, hop: float = HOP) -> dict | None:
    """Desfase grueso entre dos envolventes (a = camara, b = otro clip). Ver match()."""
    if a_db is None or b_db is None or len(a_db) < 8 or len(b_db) < 8:
        return None
    a, b = _flat(np.asarray(a_db)), _flat(np.asarray(b_db))
    lags, r, n = ncc_all(a, b)
    ok = n * hop >= OVERLAP_MIN
    if not ok.any():
        return None
    rr = np.where(ok, r, -2.0)
    i = int(np.argmax(rr))
    best = float(rr[i])
    L = int(lags[i])
    ov = float(n[i] * hop)
    far = np.abs(lags - L) * hop > FAR
    second = float(np.max(np.where(ok & far, r, -1.0))) if (ok & far).any() else -1.0
    margin = best - second
    k0, k1 = max(0, -L), min(len(a), len(b) - L)
    va, vb = _voiced(np.asarray(a_db)), _voiced(np.asarray(b_db))
    voiced = float((va[k0:k1] & vb[k0 + L:k1 + L]).sum() * hop)     # voz en los dos a la vez
    res = {"offset": round(L * hop, 4), "rate": 1.0, "corr": round(best, 4), "margin": round(margin, 4),
           "overlap": [round(k0 * hop, 3), round(k1 * hop + WIN, 3)],
           "voiced": round(voiced, 2), "ok": False}
    res["ok"] = bool(best >= corr_needed(ov) and margin >= MARGIN_MIN and voiced >= VOICED_MIN)
    return res


def _fine_offset(ya, sra, yb, srb, t0: float, t1: float, coarse: float) -> tuple[float, float] | None:
    """Desfase fino (s) en la ventana [t0, t1] (s de `a`) alrededor de `coarse`: envolvente de 1 ms,
    correlacion normalizada en +-FINE_SPAN y pico parabolico. -> (offset, corr) o None."""
    pad = FINE_SPAN + FINE_WIN + 0.01
    sa0, sa1 = int(round(t0 * sra)), int(round(t1 * sra))
    sb0 = int(round((t0 + coarse - pad) * srb))
    sb1 = int(round((t1 + coarse + pad) * srb))
    if sa0 < 0 or sb0 < 0 or sa1 > len(ya) or sb1 > len(yb) or sa1 - sa0 < sra * 1.0:
        return None
    ea = envelope(ya[sa0:sa1], sra, FINE_HOP, FINE_WIN)
    eb = envelope(yb[sb0:sb1], srb, FINE_HOP, FINE_WIN)
    if ea is None or eb is None:
        return None
    ea, eb = _flat(ea), _flat(eb)
    # eb empieza en (t0 + coarse - pad): el desfase grueso cae en el indice k0 = pad / FINE_HOP
    base = (sb0 / srb) - (sa0 / sra)          # s de b en su indice 0 menos s de a en su indice 0
    span = int(round(FINE_SPAN / FINE_HOP))
    kc = int(round((coarse - base) / FINE_HOP))
    n = len(ea)
    best = None
    vals = {}
    a0 = ea - ea.mean()
    sa = math.sqrt(float((a0 * a0).sum()))
    if sa <= 1e-9:
        return None
    for L in range(kc - span, kc + span + 1):
        if L < 0 or L + n > len(eb):
            continue
        seg = eb[L:L + n]
        b0 = seg - seg.mean()
        sb = math.sqrt(float((b0 * b0).sum()))
        if sb <= 1e-9:
            continue
        r = float((a0 * b0).sum() / (sa * sb))
        vals[L] = r
        if best is None or r > vals[best]:
            best = L
    if best is None:
        return None
    frac = 0.0
    if best - 1 in vals and best + 1 in vals:
        y0, y1, y2 = vals[best - 1], vals[best], vals[best + 1]
        d = y0 - 2 * y1 + y2
        if d < -1e-12:
            frac = max(-0.5, min(0.5, 0.5 * (y0 - y2) / d))
    return base + (best + frac) * FINE_HOP, vals[best]


def match(cam_y, cam_sr, scr_y, scr_sr, env_cam=None, env_scr=None) -> dict | None:
    """El otro clip (`scr`) suena igual que la toma de camara en algun traslape? -> el resultado de
    match_env afinado (offset a ~1 ms, `checks` al inicio/medio/final, `rate` si hay deriva) con
    "ok" (pasa las reglas de aceptacion) o None si alguno no tiene audio legible. `env_*`: las
    envolventes ya calculadas (envelope) para no repetirlas al comparar uno contra muchos."""
    if cam_y is None or scr_y is None:
        return None
    ea = env_cam if env_cam is not None else envelope(cam_y, cam_sr)
    eb = env_scr if env_scr is not None else envelope(scr_y, scr_sr)
    res = match_env(ea, eb)
    if res is None:
        return None
    if not res["ok"]:
        return res
    a0, a1 = res["overlap"]
    a1 = min(a1, len(cam_y) / float(cam_sr))
    L = res["offset"]
    ov = a1 - a0
    w = min(FINE_WINDOW, max(2.0, ov / 3.0))
    centers = [a0 + w / 2, (a0 + a1) / 2, a1 - w / 2] if ov >= 3 * w * 0.9 else [(a0 + a1) / 2]
    checks = []
    for c in centers:
        t0, t1 = max(a0 + 0.05, c - w / 2), min(a1 - 0.05, c + w / 2)
        got = _fine_offset(np.asarray(cam_y, np.float64), cam_sr, np.asarray(scr_y, np.float64), scr_sr,
                           t0, t1, L) if t1 - t0 >= 1.0 else None
        if got is not None and got[1] >= 0.5:
            checks.append({"t": round((t0 + t1) / 2, 3), "offset": round(got[0], 5), "corr": round(got[1], 4)})
    if checks:
        offs = np.array([c["offset"] for c in checks])
        ts = np.array([c["t"] for c in checks])
        off, rate = float(np.median(offs)), 1.0
        if len(checks) >= 3:
            slope, icpt = np.polyfit(ts, offs, 1)
            resid = float(np.max(np.abs(offs - (icpt + slope * ts))))
            if DRIFT_MIN <= abs(slope) <= DRIFT_MAX and resid < 0.002:
                rate, off = 1.0 + float(slope), float(icpt)
        res["offset"], res["rate"] = round(off, 5), round(rate, 7)
    res["checks"] = checks
    scr_dur = len(scr_y) / float(scr_sr)
    cam_dur = len(cam_y) / float(cam_sr)
    o, r = res["offset"], res["rate"]
    ca = max(0.0, -o / r)
    cb = min(cam_dur, (scr_dur - o) / r)
    res["overlap"] = [round(ca, 3), round(cb, 3)]
    res["other"] = [round(screen_time(ca, o, r), 3), round(screen_time(cb, o, r), 3)]
    return res


def screen_time(t_cam: float, offset: float, rate: float = 1.0) -> float:
    return float(offset) + float(rate or 1.0) * float(t_cam)


def file_offset(offset: float, cam_lead: float = 0.0, scr_lead: float = 0.0) -> float:
    """Desfase de los wavs (match) -> desfase de los ARCHIVOS: t_pantalla = offset' + rate * t_camara
    en segundos de cada archivo. El segundo w del wav es el w + lead de su archivo (lead = lo que
    pasa antes de su primera muestra de audio: video_io.audio_lead), asi que
    offset' = offset + scr_lead - cam_lead (con rate ~ 1 la correccion es la misma en todo el tramo)."""
    return float(offset) + float(scr_lead or 0.0) - float(cam_lead or 0.0)


# ----------------------------------------------------------------------------- marco de la grabacion
def _frames_at(path: str, times: list[float], w: int, h: int) -> np.ndarray | None:
    """Cuadros RGB (k, h, w, 3) uint8 de `path` en `times` (s del archivo), reducidos a w x h."""
    from motion import video_io as V

    def one(t):
        try:
            r = subprocess.run([V.FFMPEG, "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
                                "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale={w}:{h}:flags=area",
                                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        a = np.frombuffer(r.stdout, np.uint8)
        return a.reshape(h, w, 3) if a.size == w * h * 3 else None

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="marco") as ex:
        got = [f for f in ex.map(one, times) if f is not None]
    return np.stack(got) if len(got) >= 3 else None


def _line(static: np.ndarray, mean: np.ndarray, side: str, n: int, lo: int, hi: int):
    """(pixeles quietos, color medio por pixel) de la linea n desde la orilla `side`, en [lo, hi)."""
    H, W = static.shape
    if side == "top":
        return static[n, lo:hi], mean[n, lo:hi]
    if side == "bottom":
        return static[H - 1 - n, lo:hi], mean[H - 1 - n, lo:hi]
    if side == "left":
        return static[lo:hi, n], mean[lo:hi, n]
    return static[lo:hi, W - 1 - n], mean[lo:hi, W - 1 - n]


def _flat_line(st: np.ndarray, mn: np.ndarray) -> bool:
    """Linea de marco: quieta (FRAME_STATIC_FRAC de sus pixeles) y de color parejo (FRAME_FLAT)."""
    return len(st) >= 4 and st.mean() >= FRAME_STATIC_FRAC and float(mn.std(0).max()) <= FRAME_FLAT


def _depth(static: np.ndarray, mean: np.ndarray, side: str, lo: int, hi: int, limit: int,
           color: np.ndarray, edge: int) -> int:
    """Lineas de la orilla `side` que son marco: quietas, parejas y del COLOR del marco (a FRAME_TONE);
    antes de la primera del color del marco se aceptan hasta `edge` lineas parejas de otro color (una
    barra pegada a la orilla de la pantalla, la linea fina del borde de la ventana). Se detiene en la
    primera que no: el contenido quieto de la app (su encabezado, sus margenes) es de otro color y no
    se come."""
    n, seen = 0, False
    while n < limit:
        st, mn = _line(static, mean, side, n, lo, hi)
        if not _flat_line(st, mn):
            break
        near = float(np.abs(mn.mean(0) - color).max()) <= FRAME_TONE
        if near:
            seen = True
        elif seen or n >= edge:
            break
        n += 1
    return n if seen else 0


def _frame_color(static: np.ndarray, mean: np.ndarray) -> np.ndarray | None:
    """Color del marco: la mediana de las lineas quietas y parejas en la franja de la orilla (FRAME_PROBE
    de cada lado). None si no hay ninguna."""
    H, W = static.shape
    cols = []
    for side, size, other in (("top", H, W), ("bottom", H, W), ("left", W, H), ("right", W, H)):
        for n in range(max(1, int(size * FRAME_PROBE))):
            st, mn = _line(static, mean, side, n, 0, other)
            if _flat_line(st, mn):
                cols.append(mn.mean(0))
    return np.median(np.array(cols), axis=0) if cols else None


def frame_box(frames: np.ndarray) -> tuple[int, int, int, int] | None:
    """Cuadros (k, h, w, 3) -> (x0, y0, x1, y1) de lo que hay dentro del marco estatico (px de los
    cuadros; x1/y1 exclusivos) o None si no hay marco. Un marco es de verdad un MARCO: en los 4 lados
    (un dispositivo emulado, una ventana) o bandas oscuras en dos lados opuestos (letterbox), de UN
    color (el de su orilla); una banda quieta suelta (el encabezado fijo de una app, sus margenes
    blancos) no se recorta."""
    if frames is None or len(frames) < 3:
        return None
    F = frames.astype(np.int16)
    static = (F.max(0) - F.min(0)).max(-1) <= FRAME_STATIC
    mean = F.mean(0)
    H, W = static.shape
    color = _frame_color(static, mean)
    if color is None:
        return None
    lw, lh = int(W * FRAME_DEPTH), int(H * FRAME_DEPTH)
    ew, eh = max(2, int(W * FRAME_EDGE)), max(2, int(H * FRAME_EDGE))
    t = b = l = r = 0
    for _ in range(3):                       # cada eje con lo que el otro ya recorto
        nt = _depth(static, mean, "top", l, W - r, lh, color, eh)
        nb = _depth(static, mean, "bottom", l, W - r, lh, color, eh)
        nl = _depth(static, mean, "left", nt, H - nb, lw, color, ew)
        nr = _depth(static, mean, "right", nt, H - nb, lw, color, ew)
        if (nt, nb, nl, nr) == (t, b, l, r):
            break
        t, b, l, r = nt, nb, nl, nr
    if t >= lh or b >= lh or l >= lw or r >= lw:
        return None                          # un "marco" que llega al tope no es marco (todo parejo)
    t, b, l, r = (v if v >= FRAME_MIN else 0 for v in (t, b, l, r))
    # el marco es de OTRO color que el fondo de la pantalla (el color mas comun adentro): el blanco que
    # rodea una app blanca sin marco (sus margenes quietos) no es marco
    inside = F[:, t:H - b, l:W - r].reshape(-1, 3)[::7] // 8
    vals, cnt = np.unique(inside, axis=0, return_counts=True)
    bg = vals[int(np.argmax(cnt))].astype(float) * 8 + 4
    if float(np.abs(bg - color).max()) <= FRAME_TONE:
        return None
    if not (t and b and l and r):
        # sin marco en los 4 lados solo cuentan bandas OSCURAS en dos lados opuestos (letterbox): el
        # margen blanco fijo de una app no es marco
        dark = float(color.mean()) < FRAME_DARK
        lr, tb = bool(l and r and dark), bool(t and b and dark)
        if not (lr or tb):
            return None
        if not lr:
            l = r = 0
        if not tb:
            t = b = 0
    return l, t, W - r, H - b


@lru_cache(maxsize=64)
def _content_rect_cached(path: str, size: int, mtime: int, t0: float | None, t1: float | None):
    from motion import video_io as V
    info = V.probe(path)
    W, H, dur = int(info.get("w") or 0), int(info.get("h") or 0), float(info.get("duration") or 0.0)
    if W < 32 or H < 32 or dur <= 0:
        return None
    a = max(0.0, t0 if t0 is not None else 0.0)
    b = min(dur, t1 if t1 is not None else dur)
    if b - a < 0.2:
        a, b = 0.0, dur
    n = max(3, min(FRAME_N, int((b - a) / 0.5)))
    times = [a + (k + 0.5) * (b - a) / n for k in range(n)]
    k = min(1.0, FRAME_SIDE / max(W, H))
    w, h = max(16, int(round(W * k / 2)) * 2), max(16, int(round(H * k / 2)) * 2)
    box = frame_box(_frames_at(path, times, w, h))
    if box is None:
        return None
    sx, sy = W / w, H / h
    x0, y0, x1, y1 = box
    # a px de la fuente, hacia adentro (un px reducido de marco nunca queda) + la linea fina de orilla
    X0 = int(math.ceil(x0 * sx)) + (FRAME_INSET if x0 else 0)
    Y0 = int(math.ceil(y0 * sy)) + (FRAME_INSET if y0 else 0)
    X1 = int(math.floor(x1 * sx)) - (FRAME_INSET if x1 < w else 0)
    Y1 = int(math.floor(y1 * sy)) - (FRAME_INSET if y1 < h else 0)
    cw, ch = (X1 - X0) // 2 * 2, (Y1 - Y0) // 2 * 2      # pares (croma 4:2:0)
    if cw < W * FRAME_KEEP or ch < H * FRAME_KEEP or (cw >= W - 1 and ch >= H - 1):
        return None
    return {"x": X0, "y": Y0, "w": cw, "h": ch, "W": W, "H": H}


def content_rect(path, t0: float | None = None, t1: float | None = None) -> dict | None:
    """Lo que hay DENTRO del marco estatico de una grabacion de pantalla (el marco de un dispositivo
    emulado, bandas fijas) en el tramo [t0, t1] (s del archivo; el que se usa: antes y despues la
    grabacion puede mostrar otra cosa) -> {"x", "y", "w", "h"} (px de display de la fuente, pares) +
    {"W", "H"} de la fuente; None si no hay marco o no se puede leer. En cache por archivo y tramo."""
    p = Path(str(path or ""))
    try:
        st = p.stat()
    except OSError:
        return None
    r0 = None if t0 is None else round(float(t0), 2)
    r1 = None if t1 is None else round(float(t1), 2)
    try:
        got = _content_rect_cached(str(p), st.st_size, int(st.st_mtime_ns), r0, r1)
    except Exception:  # noqa: sin poder mirarla se usa completa (como siempre)
        return None
    return dict(got) if got else None


def crop_frac(rect: dict | None) -> list[float] | None:
    """{"x", "y", "w", "h", "W", "H"} -> [x0, y0, x1, y1] en fracciones de la fuente (el `crop` del
    render y del Studio) o None."""
    try:
        W, H = float(rect["W"]), float(rect["H"])
        x, y, w, h = (float(rect[k]) for k in ("x", "y", "w", "h"))
    except (TypeError, KeyError, ValueError):
        return None
    if W <= 0 or H <= 0 or w <= 0 or h <= 0:
        return None
    return [round(x / W, 5), round(y / H, 5), round((x + w) / W, 5), round((y + h) / H, 5)]


# ----------------------------------------------------------------------------- video compuesto
MOTION_STEP = 0.25        # s entre muestras de la curva de cambio de una grabacion de pantalla
MOTION_W = 64             # px de ancho de esas muestras (gris)
MOTION_TOP, MOTION_BOTTOM = 0.08, 0.12   # franjas de arriba / abajo que no cuentan (estado, indicadores)


def motion_curve(path, t0: float, t1: float, step: float = MOTION_STEP, w: int = MOTION_W):
    """Cuanto cambia la grabacion de pantalla entre muestras cada `step` s en [t0, t1] (s del archivo):
    (tiempos, cambio medio en niveles 0-255 respecto a la muestra anterior; el primero 0) o None si no se
    puede leer. Una sola lectura con ffmpeg; la reticula se fija en t0 con la busqueda sin descarte (como
    el render: un tramo SIN cuadros de ReplayKit, pantalla quieta, repite el ultimo y cambia 0)."""
    from motion import video_io as V
    try:
        info = V.probe(str(path))
    except Exception:  # noqa
        return None
    W, H = int(info.get("w") or 0), int(info.get("h") or 0)
    if not W or not H or t1 - t0 < step:
        return None
    h = max(2, int(round(w * H / W / 2)) * 2)
    n = int(math.floor((t1 - t0) / step)) + 1
    cmd = [V.FFMPEG, "-v", "error", "-nostdin", "-noaccurate_seek", "-ss", f"{max(0.0, t0):.3f}", "-i", str(path),
           "-map", "0:v:0", "-an", "-vf", f"fps=fps={1.0 / step:.6f}:start_time=0:round=near,scale={w}:{h}:flags=area,"
           "format=gray", "-frames:v", str(n), "-f", "rawvideo", "pipe:1"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    k = len(r.stdout) // (w * h)
    if k < 2:
        return None
    f = np.frombuffer(r.stdout[:k * w * h], np.uint8).reshape(k, h, w).astype(np.float32)
    # solo el CONTENIDO: sin la barra de estado de arriba ni la franja de abajo (el indicador de llamada o de
    # dictado del celular parpadea cada pocos segundos aunque la pantalla no cambie)
    y0, y1 = int(round(MOTION_TOP * h)), max(int(round(MOTION_TOP * h)) + 1, int(round((1.0 - MOTION_BOTTOM) * h)))
    f = f[:, y0:y1]
    d = np.zeros(k, np.float32)
    d[1:] = np.abs(f[1:] - f[:-1]).mean(axis=(1, 2))
    return t0 + step * np.arange(k), d


UI_FLAT_STD = 2.0         # niveles: desviacion local (5x5) de un pixel "liso" de interfaz
# (medido: tomas de iPhone 0.15-0.32 lisas con 1-11 "lineas"; grabaciones de pantalla del celular y de la
# compu 0.53-0.79 con 11-24 lineas)
UI_FLAT = 0.42            # fraccion lisa minima de una interfaz con texto...
UI_TEXT = 4               # ...y lineas de texto minimas
UI_TEXT_ONLY = 16         # o tantas lineas de texto con algo liso (UI_FLAT_MIN; una pagina llena de texto)
UI_FLAT_MIN = 0.25


def ui_stats(rgb: np.ndarray) -> dict:
    """{"flat": fraccion de pixeles en zonas LISAS (fondos de una interfaz: desviacion local 5x5 menor a
    UI_FLAT_STD en una imagen de ~640 px), "text": lineas de texto probables (brollscan.text_boxes)}."""
    import cv2
    import brollscan
    h, w = rgb.shape[:2]
    if max(h, w) > 720:
        s = 640.0 / max(h, w)
        rgb = cv2.resize(rgb, (max(2, int(w * s)), max(2, int(h * s))), interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    k = (5, 5)
    loc = np.sqrt(np.maximum(0.0, cv2.blur(g * g, k) - cv2.blur(g, k) ** 2))
    return {"flat": float((loc < UI_FLAT_STD).mean()), "text": len(brollscan.text_boxes(rgb))}


def ui_like(frames) -> bool:
    """Cuadros (RGB) de un video SIN metadatos de camara que se ven como una INTERFAZ (una grabacion de
    pantalla de la compu: OBS, Loom, Zoom; 1920x1080 sin etiqueta de grabador): en TODOS, fondos lisos
    (>= UI_FLAT) con lineas de texto (>= UI_TEXT), o muchisimo texto (>= UI_TEXT_ONLY). Una toma de camara
    (ruido, textura, una persona) no pasa. Lo decisivo sigue siendo el AUDIO: solo es pantalla si ademas
    suena igual que una toma (montage.detect_screens)."""
    fs = [f for f in frames or [] if f is not None]
    if not fs:
        return False
    for f in fs:
        st = ui_stats(f)
        if not ((st["flat"] >= UI_FLAT and st["text"] >= UI_TEXT)
                or (st["text"] >= UI_TEXT_ONLY and st["flat"] >= UI_FLAT_MIN)):
            return False
    return True


def pieces(segs, cam_sid: str, sync: dict, scr_dur: float | None) -> list[list[float]]:
    """Tramos del video compuesto donde se ve la toma `cam_sid` (segs de compose: {start, end, kind,
    sid, in}) recortados a donde la pantalla tiene cuadros ([0, scr_dur] en su reloj). -> [[t0, t1,
    cam_in]] ordenados (cam_in = s de la camara en t0; el audio de la toma corre a 1x)."""
    off = float(sync.get("offset") or 0.0)
    rate = float(sync.get("rate") or 1.0)
    out = []
    for s in segs or []:
        try:
            if s.get("kind", "clip") != "clip" or s.get("sid") != cam_sid or s.get("in") is None:
                continue
            a, b, i0 = float(s["start"]), float(s["end"]), float(s["in"])
        except (TypeError, ValueError, KeyError):
            continue
        if b - a <= 1e-3:
            continue
        # la camara de [i0, i0 + (b - a)] con pantalla en [0, scr_dur]
        lo, hi = (-off) / rate, ((scr_dur if scr_dur else 1e9) - off) / rate
        c0, c1 = max(i0, lo), min(i0 + (b - a), hi)
        if c1 - c0 <= 1e-3:
            continue
        out.append([round(a + (c0 - i0), 4), round(a + (c1 - i0), 4), round(c0, 4)])
    out.sort(key=lambda p: p[0])
    return out


def runs(ps, gap: float = RUN_GAP) -> list[list[list[float]]]:
    """Tramos contiguos (separados por menos de `gap` s) -> grupos."""
    out: list[list[list[float]]] = []
    for p in sorted(ps or [], key=lambda q: q[0]):
        if out and p[0] - out[-1][-1][1] <= gap:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def composed_to_screen(ps, t: float, offset: float, rate: float = 1.0) -> float | None:
    """s del video compuesto -> s de la grabacion de pantalla (por el tramo que lo contiene; fuera de
    todos, el mas cercano acotado a sus bordes). None sin tramos."""
    if not ps:
        return None
    t = float(t)
    hit = None
    for p in ps:
        if p[0] - 1e-6 <= t < p[1] - 1e-6:      # en una union gana el pedazo siguiente
            hit = p
            break
    if hit is None:
        hit = min(ps, key=lambda p: 0.0 if p[0] <= t <= p[1] else min(abs(t - p[0]), abs(t - p[1])))
    tt = min(max(t, hit[0]), hit[1])
    return screen_time(hit[2] + (tt - hit[0]), offset, rate)
