"""Sintesis del catalogo de SFX del Motion Engine (DSP propio: numpy/scipy, sin muestras de
terceros -> sin licencias). Contrato: docs/MOTION-ENGINE.md seccion 3.1 (sfx) y 4.2b.

Salida: `assets/motion/sfx/<name>.flac` (+ variantes `<name>_2.flac`, ...), 48 kHz estereo
16 bits con dither TPDF, y `catalog.json`:
    {name: {file, dur, hit, tags, variants: [{file, dur, hit}], category, desc, lufs_100ms, peak_db}}
`hit` = segundos desde el inicio del archivo hasta el golpe perceptual (el mezclador alinea
`at` del evento con `hit`). En risers el golpe es el final; en whooshes, el paso mas cercano.

Tecnicas:
  - ruido coloreado + filtro variante en el tiempo por STFT (barridos suaves, sin zipper)
  - fly-by fisico para whooshes: trayectoria -> distancia -> retardo (doppler real por
    lectura fraccional), 1/r, absorcion del aire, paneo por angulo + ITD
  - sintesis modal (bancos de resonadores) para clicks, teclas, monedas, obturador
  - FM y aditiva inarmonica para campanas (ding FM limpio, bell con parciales de campana)
  - wavetable limitada en banda (sin aliasing) para risers y fuentes tonales
  - reverb por convolucion con IR sintetica (decaimiento por banda, reflexiones tempranas)
  - normalizacion: loudness K-ponderada en ventana de 100 ms por categoria + techo -1 dBTP
  - traduccion a altavoz de telefono: los golpes graves llevan armonicos psicoacusticos del sub
    (saturacion asimetrica filtrada a 140-1000 Hz) y un "knock" de cuerpo: en un telefono (sin
    graves bajo ~300 Hz) el oido reconstruye la fundamental y el golpe se sigue sintiendo

Nivel: con gain_db 0 en el mezclador el pico de un SFX llega al nivel de los picos de la voz
entregada a -14 LUFS (prominente, estilo short-form); el director usa de -2 a -7 dB.

Metadatos por variante en el catalogo: `root` (clase de altura de la tonica de los SFX tonales,
0 = Do; el mezclador los transpone a la tonalidad de la musica) y `loop` ([inicio, fin] en s de
la zona sostenida de los SFX continuos: el mezclador la repite cuando el evento trae `dur`).

`generate_all(force=False)` es idempotente (firma del codigo en `_build.json`), rapida, atomica
(archivo temporal + os.replace) y con candado entre procesos. Determinista: cada sonido usa su
propia semilla fija.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np
from scipy import signal

from . import assets
from .audio import SR, as_stereo, limit_true_peak, peak_window_lufs, true_peak_db

VERSION = "1.1"
PEAK_CEIL_DB = -1.0
MAX_LIMIT_DB = 5.0   # limitacion maxima de transitorio al masterizar cada SFX
# whooshes: envolvente ancha; mas limitacion aplanaria la cima y correria el "paso" (hit)
MAX_LIMIT_BY_CAT = {"whoosh": 2.0}
MASTER_RELEASE_DB_S = 60.0    # release lento: la limitacion conserva la forma de la envolvente
# niveles de las capas de traduccion a telefono (relativos al sub)
IMPACT_SOFT_PHONE = (1.8, 1.5, 0.3)   # armonicos, knock, transitorio 2-5 kHz
IMPACT_BOOM_PHONE = (1.0, 1.2)        # armonicos, knock
SUB_DROP_PHONE = 0.6
HEART_PHONE = (2.0, 1.3, 1.6)          # golpe 250-700 Hz, armonicos, knock
HEART_BEATS = ((0.004, (55, 42, 0.09, 1.0)), (0.29, (75, 55, 0.06, 0.7)),
               (0.91, (55, 42, 0.09, 0.9)), (1.19, (75, 55, 0.06, 0.6)))
_NFFT = 1024
_HOP = 128

# ---------------------------------------------------------------- basicos
def _n(d: float) -> int:
    return int(round(d * SR))


def _t(n: int) -> np.ndarray:
    return np.arange(n) / SR


def _colored(n: int, rng: np.random.Generator, slope_db_oct: float = -3.0) -> np.ndarray:
    """Ruido con pendiente espectral (0 blanco, -3 rosa, -6 marron), RMS 1."""
    x = rng.standard_normal(n)
    if slope_db_oct == 0:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = f[1] if len(f) > 1 else 1.0
    X *= (f / 1000.0) ** (slope_db_oct / 6.0206)
    y = np.fft.irfft(X, n)
    return y / (np.std(y) + 1e-12)


def _tvf(x: np.ndarray, gain_fn: Callable[[np.ndarray, np.ndarray], np.ndarray]) -> np.ndarray:
    """Filtro variante en el tiempo por STFT. gain_fn(f[:,None], t[None,:]) -> |H|."""
    n = len(x)
    f, tt, Z = signal.stft(x, fs=SR, window="hann", nperseg=_NFFT, noverlap=_NFFT - _HOP,
                           boundary="zeros", padded=True)
    G = gain_fn(f[:, None], tt[None, :])
    _, y = signal.istft(Z * G, fs=SR, window="hann", nperseg=_NFFT, noverlap=_NFFT - _HOP,
                        boundary=True)
    y = y[:n]
    if len(y) < n:
        y = np.concatenate([y, np.zeros(n - len(y))])
    return y


def _bp(f, fc, q):
    """Magnitud de pasabanda resonante de 2o orden (pico 1)."""
    f = np.maximum(f, 1.0)
    return 1.0 / np.sqrt(1.0 + (q * (f / fc - fc / f)) ** 2)


def _lp(f, fc, order=2):
    return 1.0 / np.sqrt(1.0 + (np.maximum(f, 0.0) / fc) ** (2 * order))


def _hp(f, fc, order=2):
    f = np.maximum(f, 1e-3)
    return 1.0 / np.sqrt(1.0 + (fc / f) ** (2 * order))


@lru_cache(maxsize=64)
def _sos(kind: str, a: float, b: float | None = None, order: int = 2):
    if kind == "bp":
        return signal.butter(order, [a, b], btype="bandpass", fs=SR, output="sos")
    return signal.butter(order, a, btype=kind, fs=SR, output="sos")


def _filt(x: np.ndarray, kind: str, a: float, b: float | None = None, order: int = 2) -> np.ndarray:
    return signal.sosfilt(_sos(kind, float(a), None if b is None else float(b), order), x, axis=0)


def _rc(p):
    p = np.clip(p, 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * p)


def _env(t: np.ndarray, attack: float, tau: float, t0: float = 0.0) -> np.ndarray:
    """Ataque coseno + caida exponencial, empezando en t0."""
    u = t - t0
    a = _rc(u / max(attack, 1e-5))
    d = np.exp(-np.maximum(u - attack, 0.0) / max(tau, 1e-5))
    return np.where(u < 0, 0.0, a * d)


def _sat(x: np.ndarray, drive: float) -> np.ndarray:
    if drive <= 0:
        return x
    return np.tanh(drive * x) / math.tanh(drive)


def _transient(x: np.ndarray, t0: float, boost_db: float = 4.0, tau: float = 0.006) -> np.ndarray:
    """Realce de ataque (transient designer): +boost_db en el golpe, decae con tau."""
    t = _t(x.shape[0])
    g = 1.0 + (10 ** (boost_db / 20) - 1.0) * np.where(t >= t0, np.exp(-(t - t0) / tau), 0.0)
    return x * (g[:, None] if x.ndim == 2 else g)


def _pan(p: float) -> tuple[float, float]:
    th = (max(-1.0, min(1.0, p)) + 1.0) * math.pi / 4.0
    return math.cos(th), math.sin(th)


def _place(buf: np.ndarray, sig: np.ndarray, t0: float, gain: float = 1.0, pan: float = 0.0) -> None:
    """Suma sig (mono o estereo) en buf (n,2) a partir de t0 con paneo de potencia constante."""
    s = _n(t0)
    if s >= buf.shape[0]:
        return
    if sig.ndim == 1:
        gl, gr = _pan(pan)
        sig = np.stack([sig * gl, sig * gr], axis=1) * math.sqrt(2)
    e = min(buf.shape[0], s + sig.shape[0])
    buf[s:e] += sig[: e - s] * gain


def _stereo(x: np.ndarray, pan: float = 0.0) -> np.ndarray:
    gl, gr = _pan(pan)
    return np.stack([x * gl, x * gr], axis=1) * math.sqrt(2)


def _modal(n: int, freqs, amps, taus, attack: float = 0.0003, t0: float = 0.0) -> np.ndarray:
    """Banco de resonadores (senos amortiguados)."""
    t = _t(n)
    u = np.maximum(t - t0, 0.0)
    out = np.zeros(n)
    for f, a, tau in zip(freqs, amps, taus):
        if f >= SR * 0.45:
            continue
        out += a * np.exp(-u / tau) * np.sin(2 * np.pi * f * u)
    return out * _rc(u / attack) * (t >= t0)


def _burst(n: int, rng, tau: float, lo: float | None = None, hi: float | None = None,
           t0: float = 0.0) -> np.ndarray:
    """Rafaga de ruido con caida exponencial, filtrada."""
    t = _t(n)
    u = t - t0
    x = rng.standard_normal(n) * np.where(u >= 0, np.exp(-np.maximum(u, 0) / tau), 0.0)
    if lo and hi:
        x = _filt(x, "bp", lo, hi)
    elif hi:
        x = _filt(x, "lowpass", hi)
    elif lo:
        x = _filt(x, "highpass", lo)
    return x


def _chirp_phase(f: np.ndarray) -> np.ndarray:
    return 2 * np.pi * np.cumsum(f) / SR


@lru_cache(maxsize=4)
def _saw_table(harm: int = 48, size: int = 4096) -> np.ndarray:
    ph = np.arange(size) / size
    k = np.arange(1, harm + 1)[:, None]
    # ventana de Lanczos para suavizar Gibbs
    sig = np.sinc(k / (harm + 1))
    tab = (np.sin(2 * np.pi * k * ph) / k * sig).sum(axis=0)
    return tab / np.abs(tab).max()


def _wt_osc(freq: np.ndarray, phase0: float = 0.0, harm: int = 48) -> np.ndarray:
    tab = _saw_table(harm)
    ph = (phase0 + np.cumsum(freq) / SR) % 1.0
    idx = ph * len(tab)
    return np.interp(idx, np.arange(len(tab) + 1), np.concatenate([tab, tab[:1]]))


def _phone(sub: np.ndarray, lo: float = 300.0, hi: float = 1500.0, drive: float = 3.0,
           asym: float = 0.35) -> np.ndarray:
    """Armonicos psicoacusticos de un sub para altavoces pequenos (telefono): saturacion
    asimetrica (armonicos pares e impares) filtrada a lo..hi, con el mismo pico que el sub.
    El oido reconstruye la fundamental ausente a partir de los armonicos 2-8."""
    pk = float(np.abs(sub).max()) + 1e-12
    h = np.tanh(drive * sub / pk + asym) - math.tanh(asym)
    h = _filt(h, "bp", lo, hi, order=2)
    return h / (float(np.abs(h).max()) + 1e-12) * pk


# ---------------------------------------------------------------- reverb
@lru_cache(maxsize=16)
def _ir(rt60: float, predelay: float, damp: float, seed: int, er: float, width: float) -> np.ndarray:
    """IR estereo sintetica: ruido con decaimiento dependiente de frecuencia + tempranas."""
    rng = np.random.default_rng(seed)
    n = _n(rt60 * 1.15 + 0.05)
    out = np.zeros((n, 2))
    lf = np.log2(np.array([60.0, 250.0, 1000.0, 4000.0, 12000.0, 24000.0]))
    mult = np.array([1.25, 1.12, 1.0, 1.0 - 0.45 * damp, 1.0 - 0.75 * damp, 1.0 - 0.85 * damp])
    for c in range(2):
        x = rng.standard_normal(n)

        def g(f, t, _lf=lf, _m=mult):
            rt = rt60 * np.interp(np.log2(np.maximum(f, 30.0)), _lf, _m)
            return 10.0 ** (-3.0 * t / np.maximum(rt, 0.02))

        y = _tvf(x, g)
        # densidad difusa que crece en los primeros ms
        k = _n(0.012)
        y[:k] *= _rc(np.linspace(0, 1, k))
        out[:, c] = y
    if width < 1.0:
        m = out.mean(axis=1, keepdims=True)
        out = m + (out - m) * width
    if er > 0:
        for c in range(2):
            for _ in range(10):
                d = rng.uniform(0.003, 0.045)
                i = _n(d)
                if i < n:
                    out[i, c] += er * rng.choice([-1, 1]) * math.exp(-d / 0.03) * rng.uniform(0.4, 1.0) * 3.0
    out /= np.sqrt((out ** 2).sum(axis=0, keepdims=True)) + 1e-12
    pd = _n(predelay)
    if pd:
        out = np.concatenate([np.zeros((pd, 2)), out], axis=0)
    return out


def _fade_tail(x: np.ndarray, frac: float = 0.12, min_s: float = 0.03) -> np.ndarray:
    """Fundido final (coseno) para que ningun buffer seco termine sonando (evita clicks)."""
    n = x.shape[0]
    k = min(n, max(_n(min_s), int(frac * n)))
    if k > 1:
        w = 1.0 - _rc(np.linspace(0, 1, k))
        x = x.copy()
        x[-k:] *= w[:, None] if x.ndim == 2 else w
    return x


def _verb(x: np.ndarray, rt60: float = 1.0, wet: float = 0.2, predelay: float = 0.012,
          damp: float = 0.5, seed: int = 11, er: float = 0.4, width: float = 1.0,
          tail_fade: float = 0.12, wet_hp: float = 0.0) -> np.ndarray:
    """Seco + reverb (wet en amplitud relativa). Salida mas larga (incluye la cola).

    El seco se funde al final (`tail_fade`, fraccion de su largo) antes de convolucionar.
    `wet_hp` (Hz): pasaaltos del envio a la reverb (sin sub en la cola = sin lodo)."""
    x = as_stereo(x) if x.ndim == 2 else np.stack([x, x], axis=1)
    if tail_fade > 0:
        x = _fade_tail(x, tail_fade)
    ir = _ir(round(rt60, 3), round(predelay, 4), round(damp, 3), seed, round(er, 3), round(width, 3))
    n = x.shape[0] + ir.shape[0] - 1
    out = np.zeros((n, 2))
    out[: x.shape[0]] += x
    send = _filt(x, "highpass", wet_hp) if wet_hp > 0 else x
    for c in range(2):
        out[:, c] += wet * signal.fftconvolve(send[:, c], ir[:, c])[:n]
    return out


# ---------------------------------------------------------------- whooshes
def _flyby(dur: float, t0: float, speed: float, dist: float, rng, *, direction: int = 1,
           f_far: float = 380.0, f_near: float = 2600.0, q: float = 0.9, air: float = 0.35,
           body: float = 0.45, p_in: float = 1.3, p_out: float = 1.9, wake: float = 0.22,
           turb: float = 0.35, itd: float = 0.0005, width: float = 0.9) -> np.ndarray:
    """Objeto que pasa frente al oyente: doppler real + 1/r + aire + paneo por angulo."""
    n = _n(dur)
    t = _t(n)
    c = 343.0
    xp = direction * speed * (t - t0)
    r = np.sqrt(xp * xp + dist * dist)
    prox = dist / r
    amp = np.where(t < t0, prox ** p_in, prox ** p_out)
    # estela: turbulencia que persiste tras el paso
    wk = np.where(t > t0, np.exp(-(t - t0) / 0.16) * (1 - np.exp(-(t - t0) / 0.03)), 0.0)
    amp = amp + wake * wk
    pad = _n(0.08)
    src = _colored(n + 2 * pad, rng, -2.5)
    # turbulencia: modulacion lenta aleatoria
    tb = _filt(rng.standard_normal(n + 2 * pad), "lowpass", 28.0)
    tb /= np.std(tb) + 1e-9
    src *= 1.0 + turb * np.tanh(tb)
    delay = r / c
    read = (t - (delay - delay.min())) * SR + pad
    y = np.interp(read, np.arange(len(src)), src)
    # velocidad radial -> desplazamiento doppler del filtro (la textura tambien se corre)
    vr = np.gradient(r, 1 / SR)
    dop = c / (c + vr)
    tf = np.linspace(0, dur, max(2, int(dur * SR / _HOP) + 2))

    def g(f, tt):
        pf = np.interp(tt, t, prox)
        dp = np.interp(tt, t, dop)
        fc = f_far * (f_near / f_far) ** (pf ** 1.4) * dp
        shape = _bp(f, fc, q) + air * _hp(f, 4500.0, 2) * _lp(f, 14000.0, 2) * pf ** 3
        shape = shape + body * _lp(f, 220.0, 2) * _hp(f, 90.0, 2) * pf ** 2
        return shape * _lp(f, 16000.0, 2)

    del tf
    y = _tvf(y, g)
    y = _filt(y, "highpass", 75.0)
    # ventana: entra y sale desde silencio (sin bordes duros)
    win = _rc(t / (0.3 * t0)) * _rc((dur - t) / (0.3 * (dur - t0)))
    y = y * amp * win
    # paneo por angulo + ITD (oido lejano retrasado)
    pan = np.clip(xp / r, -1, 1) * width
    th = (pan + 1.0) * math.pi / 4.0
    gl, gr = np.cos(th), np.sin(th)
    dl = np.maximum(pan, 0) * itd * SR
    dr = np.maximum(-pan, 0) * itd * SR
    idx = np.arange(n, dtype=np.float64)
    L = np.interp(idx - dl, idx, y) * gl
    R = np.interp(idx - dr, idx, y) * gr
    return np.stack([L, R], axis=1) * math.sqrt(2)


def _hit_near(x: np.ndarray, t_design: float, win: float = 0.025, smooth: float = 0.001) -> float:
    """Golpe perceptual de un sonido puntual: primer instante en que la envolvente llega al
    80 % de su maximo local (ventana `win` desde el inicio disenado)."""
    e = np.abs(as_stereo(x)).max(axis=1)
    w = max(1, _n(smooth))
    if w > 1:
        e = np.convolve(e, np.ones(w) / w, mode="same")
    a = max(0, _n(t_design) - _n(0.001))
    b = min(len(e), _n(t_design + win))
    if b <= a:
        return t_design
    seg = e[a:b]
    k = int(np.argmax(seg >= 0.8 * seg.max()))
    return float((a + k) / SR)


def _peak_time(x: np.ndarray, win_s: float = 0.004) -> float:
    e = np.abs(as_stereo(x)).max(axis=1)
    w = max(1, _n(win_s))
    if w > 1:
        e = np.convolve(e, np.ones(w) / w, mode="same")
    return float(np.argmax(e) / SR)


def whoosh_soft(v: int, rng):
    cfg = [dict(dur=1.05, t0=0.56, speed=24, dist=3.6, direction=1, f_far=300, f_near=1900, q=0.8, air=0.22),
           dict(dur=1.15, t0=0.62, speed=20, dist=3.2, direction=-1, f_far=260, f_near=1500, q=1.1, air=0.15, body=0.6),
           dict(dur=0.95, t0=0.50, speed=28, dist=4.0, direction=1, f_far=420, f_near=2600, q=0.7, air=0.4)][v]
    x = _flyby(rng=rng, **cfg)
    x = _verb(x, rt60=0.9, wet=0.16, damp=0.6, seed=21 + v)
    return x, _peak_time(x, 0.03)


def whoosh_fast(v: int, rng):
    cfg = [dict(dur=0.62, t0=0.30, speed=46, dist=2.2, direction=1, f_far=420, f_near=3400, q=0.85, air=0.5, p_out=2.3),
           dict(dur=0.58, t0=0.28, speed=52, dist=2.0, direction=-1, f_far=500, f_near=3000, q=1.2, air=0.35, p_out=2.4, body=0.6),
           dict(dur=0.66, t0=0.32, speed=40, dist=2.4, direction=1, f_far=360, f_near=4200, q=0.7, air=0.6, p_out=2.1)][v]
    x = _flyby(rng=rng, **cfg)
    x = _verb(x, rt60=0.7, wet=0.12, damp=0.6, seed=31 + v)
    return x, _peak_time(x, 0.012)


def _swoosh(rng, up: bool, dur: float, f0: float, f1: float, tpk: float, q0: float, q1: float,
            pan0: float, pan1: float):
    n = _n(dur)
    t = _t(n)
    src = _colored(n, rng, -2.0)
    if up:
        env = np.where(t < tpk, (t / tpk) ** 2.4, np.exp(-(t - tpk) / 0.055))
        prog = np.clip(t / tpk, 0, 1) ** 1.6
    else:
        env = np.where(t < tpk, _rc(t / tpk), np.exp(-(t - tpk) / 0.16))
        prog = np.clip((t - tpk * 0.3) / (dur - tpk), 0, 1) ** 0.7
    fcs = f0 * (f1 / f0) ** prog

    def g(f, tt):
        fc = np.interp(tt, t, fcs)
        qq = q0 + (q1 - q0) * np.interp(tt, t, prog)
        return (_bp(f, fc, qq) + 0.35 * _bp(f, fc * 2.1, qq * 1.4)) * _lp(f, 15000, 2)

    y = _tvf(src, g) * env
    pan = pan0 + (pan1 - pan0) * _rc(t / dur)
    th = (pan + 1) * math.pi / 4
    x = np.stack([y * np.cos(th), y * np.sin(th)], axis=1) * math.sqrt(2)
    x = _verb(x, rt60=0.8, wet=0.14, damp=0.5, seed=41 + int(up))
    return x


def swoosh_up(v: int, rng):
    p = [dict(dur=0.72, f0=260, f1=6500, tpk=0.60, q0=1.2, q1=3.2, pan0=-0.35, pan1=0.35),
         dict(dur=0.55, f0=380, f1=8000, tpk=0.46, q0=1.6, q1=3.8, pan0=0.3, pan1=-0.3)][v]
    x = _swoosh(rng, True, **p)
    return x, _peak_time(x, 0.01)


def swoosh_down(v: int, rng):
    p = [dict(dur=0.78, f0=6000, f1=240, tpk=0.07, q0=3.0, q1=1.2, pan0=0.35, pan1=-0.35),
         dict(dur=0.62, f0=7500, f1=350, tpk=0.05, q0=3.5, q1=1.5, pan0=-0.3, pan1=0.3)][v]
    x = _swoosh(rng, False, **p)
    return x, _peak_time(x, 0.01)


def whip(v: int, rng):
    t0 = [0.20, 0.17, 0.22][v]
    x = _flyby(0.55, t0, speed=[120, 140, 105][v], dist=1.3, rng=rng, direction=[1, -1, 1][v],
               f_far=600, f_near=5200, q=0.7, air=0.8, p_in=1.6, p_out=3.0, wake=0.12, turb=0.2)
    n = x.shape[0]
    tc = t0  # el chasquido ocurre en el instante de maxima velocidad (paso mas cercano)
    crack = _burst(n, rng, 0.0007, lo=1800, hi=12000, t0=tc) * 1.3
    crack += _modal(n, [2750 + 180 * v, 4100, 6300], [0.5, 0.35, 0.2], [0.006, 0.004, 0.0025], t0=tc)
    crack += _modal(n, [155.0], [0.35], [0.03], attack=0.001, t0=tc)
    x = x * 0.85 + _stereo(crack, [0.15, -0.15, 0.1][v]) * 0.9
    x = _verb(x, rt60=0.6, wet=0.12, damp=0.4, seed=51 + v)
    return x, _hit_near(x, tc - 0.001, 0.006)


# ---------------------------------------------------------------- pops / ui
def _pop_voice(n, rng, f_hi, f_lo, tau_p, tau_a, attack, click, harm=0.15, t0=0.0):
    t = _t(n)
    u = np.maximum(t - t0, 0)
    f = f_lo + (f_hi - f_lo) * np.exp(-u / tau_p)
    ph = _chirp_phase(f)
    e = _env(t, attack, tau_a, t0)
    y = np.sin(ph) * e + harm * np.sin(2 * ph) * e ** 1.6
    if click > 0:
        y += click * _burst(n, rng, 0.0006, lo=2500, hi=9000, t0=t0)
    # aire de la boca: soplo corto grave
    y += 0.08 * _burst(n, rng, 0.004, hi=1200, t0=t0)
    return y


def pop(v: int, rng):
    f_hi, f_lo, tp, ta, ck = [(1500, 430, 0.009, 0.038, 0.22), (1250, 360, 0.011, 0.044, 0.18),
                              (1750, 520, 0.008, 0.034, 0.25)][v]
    n = _n(0.32)
    y = _sat(_pop_voice(n, rng, f_hi, f_lo, tp, ta, 0.0007, ck), 1.3)
    x = _verb(_stereo(y), rt60=0.35, wet=0.07, damp=0.3, seed=61 + v, er=0.6)
    return x, _hit_near(x, 0.0, 0.025)


def pop_soft(v: int, rng):
    f_hi, f_lo = [(760, 290), (900, 340)][v]
    n = _n(0.35)
    y = _pop_voice(n, rng, f_hi, f_lo, 0.014, 0.06, 0.0028, 0.0, harm=0.08)
    y = _filt(y, "lowpass", 3200)
    x = _verb(_stereo(y), rt60=0.45, wet=0.09, damp=0.5, seed=71 + v)
    return x, _hit_near(x, 0.0, 0.025)


def bubble(v: int, rng):
    n = _n(0.5)
    y = np.zeros(n)
    specs = [[(0.0, 380, 2.3, 1.0), (0.075, 520, 2.1, 0.6), (0.128, 700, 1.9, 0.35)],
             [(0.0, 440, 2.6, 1.0), (0.06, 330, 2.4, 0.5)]][v]
    t = _t(n)
    for t0, f0, rise, a in specs:
        u = np.maximum(t - t0, 0)
        f = f0 * np.minimum(1 + (rise - 1) * (1 - np.exp(-u / 0.03)), rise)
        e = _env(t, 0.0012, 0.028, t0)
        y += a * np.sin(_chirp_phase(np.where(t >= t0, f, 0.0))) * e
    y = _sat(y, 1.1)
    x = _verb(_stereo(y), rt60=0.5, wet=0.1, damp=0.4, seed=81 + v)
    return x, _hit_near(x, 0.0, 0.025)


def click(v: int, rng):
    n = _n(0.16)
    spec = [dict(f=[2150, 3900, 6100], a=[1, 0.6, 0.35], rel=0.055),
            dict(f=[1600, 3000, 4700], a=[1, 0.5, 0.25], rel=None),
            dict(f=[3200, 5400, 8000], a=[1, 0.55, 0.3], rel=0.042)][v]
    t0 = 0.002
    y = _burst(n, rng, 0.0005, lo=2000, hi=11000, t0=t0) * 0.9
    y += _modal(n, spec["f"], spec["a"], [0.004, 0.0026, 0.0015], t0=t0)
    y += _modal(n, [185.0], [0.25], [0.006], attack=0.0008, t0=t0)
    if spec["rel"]:
        t1 = t0 + spec["rel"]
        y += 0.42 * (_burst(n, rng, 0.0004, lo=2500, hi=12000, t0=t1) * 0.8
                     + _modal(n, [f * 1.12 for f in spec["f"]], spec["a"], [0.003, 0.002, 0.0012], t0=t1))
    x = _verb(_stereo(y), rt60=0.2, wet=0.05, damp=0.3, seed=91 + v, er=0.7)
    return x, _hit_near(x, 0.002, 0.025)


def tick(v: int, rng):
    n = _n(0.14)
    f, a, taus = [([2600, 4700, 7300], [1, 0.5, 0.25], [0.007, 0.004, 0.002]),
                  ([1250, 2900, 5100], [1, 0.45, 0.2], [0.012, 0.006, 0.003])][v]
    t0 = 0.002
    y = _modal(n, f, a, taus, t0=t0) + 0.5 * _burst(n, rng, 0.0004, lo=2000, hi=10000, t0=t0)
    if v == 1:
        y += _modal(n, [420.0], [0.35], [0.01], t0=t0)
    x = _verb(_stereo(y), rt60=0.25, wet=0.06, damp=0.4, seed=101 + v)
    return x, _hit_near(x, 0.002, 0.025)


def typing(v: int, rng):
    dur = 1.45
    n = _n(dur)
    buf = np.zeros((n, 2))
    tk = 0.004
    k = 0
    onsets = []
    while tk < dur - 0.2 and k < 12:
        space = k in (4, 9)
        g = rng.uniform(0.72, 1.0) * (1.1 if space else 1.0)
        fm = rng.uniform(0.86, 1.14)
        y = _burst(n, rng, 0.0006, lo=1500, hi=9000, t0=tk) * 0.5
        y += _modal(n, [1800 * fm, 3300 * fm, 5200 * fm], [0.8, 0.45, 0.25], [0.005, 0.003, 0.002], t0=tk)
        body = [260 * fm, 540 * fm] if space else [330 * fm, 620 * fm]
        y += _modal(n, body, [0.55, 0.3], [0.022 if space else 0.016, 0.01], attack=0.0006, t0=tk)
        tr = tk + rng.uniform(0.028, 0.045)
        y += 0.3 * _modal(n, [2300 * fm, 4200 * fm], [0.6, 0.3], [0.003, 0.002], t0=tr)
        # saturacion suave por tecla: recorta la aguja del click y deja el cuerpo (mas presencia
        # con el mismo pico, como un teclado grabado con compresion)
        y = _sat(y / 2.0, 2.0) * 2.0
        _place(buf, y, 0.0, g, rng.uniform(-0.25, 0.25))
        onsets.append(tk)
        k += 1
        tk += 0.062 + rng.gamma(3.0, 0.018) + (0.07 if space else 0.0)
    x = _verb(buf, rt60=0.3, wet=0.1, damp=0.5, seed=111, er=0.6)
    # loop de la tecla 2 a la ultima: fundido de 30 ms antes de cada golpe (en el hueco)
    loop = [round(onsets[1] - 0.034, 4), round(onsets[-1] - 0.034, 4)]
    return x, 0.0045, {"loop": loop}


def shutter(v: int, rng):
    n = _n(0.3)
    buf = np.zeros(n)
    for t0, g in ((0.002, 1.0), (0.074, 0.8)):
        for j, dt in enumerate((0.0, 0.0016, 0.0034)):
            buf += g * (0.9 - 0.25 * j) * (_modal(n, [1850, 3300, 5200, 7800], [1, 0.6, 0.4, 0.25],
                                                  [0.006, 0.004, 0.003, 0.002], t0=t0 + dt)
                                           + 0.6 * _burst(n, rng, 0.0009, lo=3000, hi=12000, t0=t0 + dt))
        buf += g * _modal(n, [240.0, 520.0], [0.45, 0.25], [0.015, 0.008], attack=0.0006, t0=t0)
    x = _verb(_stereo(buf), rt60=0.22, wet=0.07, damp=0.3, seed=121, er=0.7)
    return x, _hit_near(x, 0.002, 0.025)


def swipe(v: int, rng):
    dur = [0.34, 0.28][v]
    n = _n(dur)
    t = _t(n)
    tp = [0.075, 0.06][v]
    env = np.where(t < tp, (t / tp) ** 1.5, np.exp(-(t - tp) / 0.065))
    f0, f1 = [(1600, 4600), (2200, 6200)][v]
    prog = _rc(t / dur)
    fcs = f0 * (f1 / f0) ** prog
    y = _tvf(_colored(n, rng, -1.0), lambda f, tt: _bp(f, np.interp(tt, t, fcs), 1.1) * _hp(f, 700, 2))
    y *= env
    pan = [(-0.55, 0.55), (0.55, -0.55)][v]
    pp = pan[0] + (pan[1] - pan[0]) * prog
    th = (pp + 1) * math.pi / 4
    x = np.stack([y * np.cos(th), y * np.sin(th)], axis=1) * math.sqrt(2)
    x = _verb(x, rt60=0.3, wet=0.06, damp=0.4, seed=131 + v)
    return x, _peak_time(x, 0.01)


# ---------------------------------------------------------------- tonales
def _fm_bell(n, fc, t0=0.0, index=2.4, ratio=1.4, tau_i=0.12, tau_a=0.55, shimmer=1.2):
    t = _t(n)
    u = np.maximum(t - t0, 0)
    on = t >= t0
    I = index * np.exp(-u / tau_i)
    e = _env(t, 0.0008, tau_a, t0)
    y = np.sin(2 * np.pi * fc * u + I * np.sin(2 * np.pi * fc * ratio * u)) * e
    y += 0.3 * np.sin(2 * np.pi * (fc + shimmer) * u) * _env(t, 0.001, tau_a * 0.9, t0)
    y += 0.22 * np.sin(2 * np.pi * fc * 2.756 * u) * _env(t, 0.0006, tau_a * 0.35, t0)
    y += 0.07 * np.sin(2 * np.pi * fc * 5.404 * u) * _env(t, 0.0005, tau_a * 0.12, t0)
    return y * on


def ding(v: int, rng):
    n = _n(1.9)
    fc = [1318.51, 1567.98][v]
    y = _fm_bell(n, fc, t0=0.002, index=[2.2, 1.8][v], ratio=[1.4, 3.5][v])
    y += 0.12 * _burst(n, rng, 0.0008, lo=4000, hi=12000, t0=0.002)
    x = _verb(_stereo(y), rt60=1.5, wet=0.2, damp=0.3, seed=141 + v)
    return x, _hit_near(x, 0.002, 0.025)


def bell(v: int, rng):
    n = _n(3.4)
    f0 = 784.0
    parts = [(0.5, 0.35, 2.4), (1.0, 0.6, 1.8), (1.183, 0.45, 1.3), (1.506, 0.25, 1.0), (2.0, 1.0, 1.2),
             (2.514, 0.3, 0.65), (2.662, 0.25, 0.55), (3.011, 0.35, 0.45), (4.166, 0.2, 0.3),
             (5.433, 0.12, 0.2), (6.796, 0.08, 0.14), (8.215, 0.05, 0.1)]
    t = _t(n)
    t0 = 0.002
    y = np.zeros(n)
    for r, a, tau in parts:
        f = f0 * r
        det = rng.uniform(0.3, 0.9)
        e = _env(t, 0.0009, tau, t0)
        y += a * e * (np.sin(2 * np.pi * (f - det / 2) * (t - t0)) + 0.8 * np.sin(2 * np.pi * (f + det / 2) * (t - t0) + 0.7))
    y += 0.25 * _burst(n, rng, 0.004, lo=2000, hi=9000, t0=t0)
    x = _verb(_stereo(y * 0.5), rt60=2.2, wet=0.26, damp=0.35, seed=151)
    return x, _hit_near(x, 0.002, 0.025)


def _mallet(n, f, t0, tau=0.9, parts=((1.0, 1.0, 1.0), (2.756, 0.22, 0.28), (5.404, 0.07, 0.11)),
            rng=None, noise=0.08):
    t = _t(n)
    y = np.zeros(n)
    for r, a, tf in parts:
        if f * r < SR * 0.45:
            y += a * np.sin(2 * np.pi * f * r * np.maximum(t - t0, 0)) * _env(t, 0.0007, tau * tf, t0)
    if rng is not None and noise:
        y += noise * _burst(n, rng, 0.0006, lo=3000, hi=10000, t0=t0)
    return y


def chime(v: int, rng):
    n = _n(2.6)
    buf = np.zeros((n, 2))
    notes = [1318.51, 1661.22, 1975.53, 2637.02]
    pans = [-0.5, -0.15, 0.2, 0.5]
    for i, (f, p) in enumerate(zip(notes, pans)):
        _place(buf, _mallet(n, f, 0.0, tau=0.85, rng=rng), 0.004 + 0.068 * i, [1, 0.9, 0.85, 0.8][i], p)
    x = _verb(buf, rt60=1.9, wet=0.32, damp=0.25, seed=161)
    return x, 0.0055


def sparkle(v: int, rng):
    dur = 1.5
    n = _n(dur)
    buf = np.zeros((n, 2))
    scale = [2637.0, 2960.0, 3322.4, 3951.1, 4434.9, 5274.0, 5919.9, 6644.9]
    k = 18 if v == 0 else 14
    times = np.sort(np.concatenate([[0.004], 0.004 + rng.exponential(0.16, k - 1)]))
    times = times[times < 0.9]
    t = _t(n)
    for i, t0 in enumerate(times):
        if v == 0:
            idx = min(len(scale) - 1, int(i / len(times) * len(scale) + rng.integers(-1, 2)))
        else:
            idx = int(rng.integers(0, len(scale)))
        f = scale[max(0, idx)] * (1 + rng.uniform(-0.003, 0.003))
        a = (1.0 if i == 0 else rng.uniform(0.35, 0.9)) * math.exp(-t0 / 0.6)
        vib = 1 + 0.002 * np.sin(2 * np.pi * rng.uniform(5, 9) * t)
        u = np.maximum(t - t0, 0)
        g = np.sin(2 * np.pi * f * u * vib) * _env(t, 0.0006, rng.uniform(0.03, 0.09), t0)
        g += 0.18 * np.sin(2 * np.pi * f * 2.0 * u) * _env(t, 0.0005, 0.02, t0)
        _place(buf, g, 0.0, a, rng.uniform(-0.7, 0.7))
    # brillo de ruido agudo
    sh = _filt(rng.standard_normal(n), "bp", 7000, 13000)
    am = np.abs(_filt(rng.standard_normal(n), "lowpass", 40.0))
    am /= am.max() + 1e-9
    she = np.where(t < 0.06, _rc(t / 0.06), np.exp(-(t - 0.06) / 0.25))
    buf += np.stack([sh * am, np.roll(sh, 97) * am], axis=1) * she[:, None] * 0.12
    x = _verb(buf, rt60=1.6, wet=0.38, damp=0.2, seed=171 + v)
    return x, 0.0045


def notification(v: int, rng):
    n = _n(1.3)
    buf = np.zeros((n, 2))
    marimba = ((1.0, 1.0, 1.0), (3.93, 0.35, 0.18), (9.2, 0.12, 0.06))
    for f, t0, g in ((880.0, 0.003, 0.85), (1318.51, 0.115, 1.0)):
        y = _mallet(n, f, 0.0, tau=0.33, parts=marimba, rng=rng, noise=0.05)
        _place(buf, y, t0, g, 0.0)
    x = _verb(buf, rt60=0.9, wet=0.2, damp=0.4, seed=181)
    return x, 0.0045


def success(v: int, rng):
    n = _n(2.0)
    buf = np.zeros((n, 2))
    notes = [1046.5, 1318.51, 1567.98, 2093.0]
    parts = ((1.0, 1.0, 1.0), (2.0, 0.18, 0.3), (3.0, 0.07, 0.15))
    for i, f in enumerate(notes):
        _place(buf, _mallet(n, f, 0.0, tau=0.6, parts=parts, rng=rng, noise=0.04), 0.004 + 0.072 * i,
               [0.85, 0.9, 0.95, 1.0][i], [-0.3, -0.1, 0.1, 0.3][i])
    t = _t(n)
    pad = sum(np.sin(2 * np.pi * f * t) for f in notes[:3]) / 3
    pe = np.where(t < 0.3, _rc((t - 0.1) / 0.2), np.exp(-(t - 0.3) / 0.5))
    buf += _stereo(pad * pe * 0.12)
    x = _verb(buf, rt60=1.5, wet=0.28, damp=0.3, seed=191)
    return x, 0.0055


def cash(v: int, rng):
    n = _n(1.6)
    buf = np.zeros((n, 2))
    # "ka": palanca
    ka = _modal(n, [900, 2300, 4100], [0.8, 0.5, 0.3], [0.008, 0.005, 0.003], t0=0.003)
    ka += 0.7 * _burst(n, rng, 0.0018, lo=1000, hi=6000, t0=0.003)
    ka += _modal(n, [140.0], [0.5], [0.025], attack=0.001, t0=0.003)
    _place(buf, ka, 0.0, 0.55, -0.1)
    # "ching": dos campanitas
    t_hit = 0.078
    for f, dt, g, p in ((2093.0, 0.0, 1.0, -0.15), (2637.0, 0.011, 0.8, 0.15)):
        y = _mallet(n, f, 0.0, tau=0.9, parts=((1.0, 1.0, 1.0), (2.32, 0.5, 0.55), (4.25, 0.3, 0.28), (6.63, 0.12, 0.13)),
                    rng=rng, noise=0.15)
        y += 0.5 * np.sin(2 * np.pi * (f + 3.1) * _t(n)) * _env(_t(n), 0.001, 0.7)
        _place(buf, y, t_hit + dt, g, p)
    # monedas
    tc = t_hit + 0.02
    for i in range(11):
        tc += rng.exponential(0.045)
        if tc > 0.62:
            break
        f0 = rng.uniform(3000, 5200)
        cn = _modal(_n(0.25), [f0, f0 * 1.59, f0 * 2.14, f0 * 2.65], [1, 0.6, 0.4, 0.25],
                    [0.06, 0.04, 0.025, 0.015])
        _place(buf, cn, tc, rng.uniform(0.18, 0.4) * math.exp(-(tc - t_hit) / 0.5), rng.uniform(-0.5, 0.5))
    # cajon: rodamiento + golpe
    t = _t(n)
    roll = _filt(rng.standard_normal(n), "lowpass", 900) * (0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 22 * t)))
    re = _rc((t - 0.11) / 0.05) * (1 - _rc((t - 0.30) / 0.05))
    buf += _stereo(roll * re * 0.12, 0.1)
    clunk = _modal(n, [110, 240, 520], [0.8, 0.5, 0.3], [0.06, 0.04, 0.02], attack=0.001, t0=0.34)
    clunk += 0.5 * _burst(n, rng, 0.006, hi=1500, t0=0.34)
    buf += _stereo(clunk * 0.45)
    x = _verb(buf, rt60=0.55, wet=0.15, damp=0.4, seed=201, er=0.6)
    return x, t_hit + 0.0015


def coin(v: int, rng):
    n = _n(1.2)
    buf = np.zeros(n)
    f0 = 3400.0
    ratios = [1, 1.59, 2.14, 2.30, 2.65, 2.92]
    amps = [1, 0.7, 0.5, 0.35, 0.3, 0.2]
    for t0, a, tm in ((0.002, 1.0, 1.0), (0.14, 0.5, 0.45), (0.225, 0.3, 0.3), (0.28, 0.16, 0.2)):
        taus = [tm * x for x in (0.38, 0.26, 0.18, 0.12, 0.1, 0.08)]
        fo = f0 * (1 + rng.uniform(-0.004, 0.004))
        buf += a * (_modal(n, [fo * r for r in ratios], amps, taus, t0=t0)
                    + 0.3 * _burst(n, rng, 0.0005, lo=3000, hi=12000, t0=t0))
    x = _verb(_stereo(buf * 0.6), rt60=0.5, wet=0.12, damp=0.3, seed=211)
    return x, _hit_near(x, 0.002, 0.025)


# ---------------------------------------------------------------- risers / impactos
def _riser(rng, dur: float, f_lo: float, f_hi: float, p0: float, p1: float, seed: int):
    n = _n(dur)
    t = _t(n)
    p = t / dur
    env = p ** 2.2
    # capa de ruido con pulso acelerado
    rate = 4.0 + 20.0 * p ** 1.5
    ph = np.cumsum(rate) / SR
    puls = 1.0 - (0.2 + 0.35 * p) * (0.5 - 0.5 * np.cos(2 * np.pi * ph))
    src = _colored(n, rng, -1.5)
    fcs = f_lo * (f_hi / f_lo) ** (p ** 1.4)

    def gn(f, tt):
        fc = np.interp(tt, t, fcs)
        qq = 0.8 + 2.6 * np.interp(tt, t, p)
        return _bp(f, fc, qq) + 0.25 * _hp(f, fc, 2) * _lp(f, 15000, 2)

    noise = _tvf(src, gn) * puls
    # capa tonal: 5 sierras desafinadas, subiendo 2 octavas, filtro que abre
    pitch = p0 * (p1 / p0) ** (p ** 1.6)
    ton = np.zeros((n, 2))
    for dc, pan in ((-15, -0.8), (-7, -0.4), (0, 0.0), (7, 0.4), (15, 0.8)):
        s = _wt_osc(pitch * 2 ** (dc / 1200.0), phase0=rng.uniform())
        ton += _stereo(s, pan) / 5
    lpc = 400 * (9000 / 400) ** (p ** 1.3)
    for c in range(2):
        ton[:, c] = _tvf(ton[:, c], lambda f, tt: _lp(f, np.interp(tt, t, lpc), 2))
    x = np.stack([noise, np.roll(noise, 173)], axis=1) * 0.7 + ton * 0.55
    x *= env[:, None]
    # final: golpe en el pico y corte limpio
    k = _n(0.018)
    x[-k:] *= (1 - _rc(np.linspace(0, 1, k)))[:, None]
    x = _verb(x, rt60=1.4, wet=0.22, damp=0.4, seed=seed, tail_fade=0.0)
    return x, dur - 0.01


def riser_short(v: int, rng):
    return _riser(rng, 1.6, 220, 7500, 110.0, 440.0, 221)


def riser_long(v: int, rng):
    return _riser(rng, 4.0, 160, 9000, 82.4, 329.6, 231)


def impact_soft(v: int, rng):
    n = _n(1.1)
    t = _t(n)
    t0 = 0.002
    f = [72 + 85 * np.exp(-np.maximum(t - t0, 0) / 0.025), 62 + 100 * np.exp(-np.maximum(t - t0, 0) / 0.02)][v]
    sub = np.sin(_chirp_phase(f)) * _env(t, 0.0012, [0.095, 0.115][v], t0)
    y = sub + 0.35 * _burst(n, rng, 0.025, hi=1200, t0=t0)
    y += 0.22 * _burst(n, rng, 0.0008, lo=1000, hi=6000, t0=t0)
    if v == 1:
        y += 0.3 * _burst(n, rng, 0.06, lo=200, hi=900, t0=t0)
    # traduccion a telefono: armonicos del sub + knock de cuerpo + transitorio de 2-5 kHz
    y += IMPACT_SOFT_PHONE[0] * _phone(sub)
    y += IMPACT_SOFT_PHONE[1] * _modal(n, [[330, 470, 690], [310, 445, 650]][v], [0.55, 0.35, 0.2],
                                       [0.032, 0.02, 0.012], attack=0.0006, t0=t0)
    y += IMPACT_SOFT_PHONE[2] * _burst(n, rng, 0.0018, lo=2000, hi=5000, t0=t0)
    y = _sat(y, 1.5)
    x = _verb(_stereo(y), rt60=0.7, wet=0.16, damp=0.7, seed=241 + v, wet_hp=120.0)
    return x, _hit_near(x, 0.002, 0.04)


def impact_boom(v: int, rng):
    n = _n(4.2)
    t = _t(n)
    t0 = 0.003
    u = np.maximum(t - t0, 0)
    f = 36 + 110 * np.exp(-u / 0.06)
    # dos etapas: golpe inicial y cuerpo (el ataque es el maximo global)
    # cuerpo del sub corto (0.3 s) y parciales metalicos breves: la cola no enloda la voz
    e2 = 0.5 * _env(t, 0.0015, 0.05, t0) + 0.5 * _env(t, 0.0015, 0.3, t0)
    sub = _sat(np.sin(_chirp_phase(f)) * e2, 1.6)
    punch = 0.6 * _burst(n, rng, 0.045, hi=2500, t0=t0)
    body = 0.35 * _burst(n, rng, 0.18, lo=150, hi=900, t0=t0)
    crack = 0.9 * _burst(n, rng, 0.0018, lo=1800, hi=12000, t0=t0)
    crack += _modal(n, [180.0, 345.0, 610.0], [0.45, 0.3, 0.15], [0.016, 0.01, 0.006], attack=0.0004, t0=t0)
    metal = _modal(n, [97, 151, 233, 377], [0.08, 0.06, 0.05, 0.03], [0.35, 0.3, 0.25, 0.2], attack=0.002, t0=t0)
    # traduccion a telefono: armonicos del sub + knock de cuerpo
    phone = IMPACT_BOOM_PHONE[0] * _phone(sub) * _env(t, 0.001, 0.35, t0)
    phone += IMPACT_BOOM_PHONE[1] * _modal(n, [310.0, 420.0, 610.0], [0.6, 0.4, 0.25], [0.07, 0.045, 0.025],
                                           attack=0.0006, t0=t0)
    y = _transient(sub + punch + body + crack + metal + phone, t0, 3.0, 0.006)
    x = _stereo(y)
    # envio a la reverb sin graves (> 240 Hz): la cola no se encima con la F0 de la voz
    x = _verb(x, rt60=2.4, wet=0.34, damp=0.7, predelay=0.012, seed=251, wet_hp=240.0)
    return x, _hit_near(x, 0.003, 0.04)


def sub_drop(v: int, rng):
    n = _n(2.4)
    t = _t(n)
    t0 = 0.004
    u = np.maximum(t - t0, 0)
    f = 28 + 95 * np.exp(-u / 0.35)
    sub = np.sin(_chirp_phase(f)) * _env(t, 0.005, 0.9, t0)
    y = _sat(sub, 2.6)
    y += 0.08 * _burst(n, rng, 0.4, hi=120, t0=t0)
    # traduccion a telefono: los armonicos siguen la caida de tono (se oye el "drop")
    y += SUB_DROP_PHONE * _phone(sub, 300.0, 1200.0, drive=2.5) * _env(t, 0.004, 0.55, t0)
    x = _verb(_stereo(y), rt60=1.2, wet=0.1, damp=0.8, seed=261, wet_hp=90.0)
    return x, _hit_near(x, 0.004, 0.04)


def heartbeat(v: int, rng):
    n = _n(1.7)
    y = np.zeros(n)
    t = _t(n)
    for t0, (fa, fb, tau, a) in HEART_BEATS:
        u = np.maximum(t - t0, 0)
        f = fb + (fa - fb) * np.exp(-u / 0.03)
        s = np.sin(_chirp_phase(np.where(t >= t0, f, 0.0))) * _env(t, 0.004, tau, t0)
        y += a * s
        y += a * 0.3 * _burst(n, rng, 0.05, hi=250, t0=t0)
        # traduccion a telefono: golpe de 150-600 Hz (como un estetoscopio) + armonicos
        y += a * HEART_PHONE[0] * _burst(n, rng, 0.018, lo=250, hi=700, t0=t0)
        y += a * HEART_PHONE[1] * _phone(s, 300.0, 900.0)
        y += a * HEART_PHONE[2] * _modal(n, [310.0, 430.0], [0.6, 0.35], [0.035, 0.02], attack=0.002, t0=t0)
    y = _sat(y, 2.0)
    x = _verb(_stereo(y), rt60=0.5, wet=0.08, damp=0.8, seed=271, wet_hp=90.0)
    # un ciclo exacto (lub-dub) entre dos silencios: el loop no corta ningun latido
    period = HEART_BEATS[2][0] - HEART_BEATS[0][0]
    return x, _hit_near(x, 0.004, 0.04), {"loop": [0.70, round(0.70 + period, 4)]}


# ---------------------------------------------------------------- texturas
def glitch(v: int, rng):
    dur = 0.5
    n = _n(dur)
    buf = np.zeros((n, 2))
    tpos = 0.003
    first = True
    while tpos < dur - 0.06:
        L = rng.uniform(0.012, 0.045)
        m = _n(L)
        tt = _t(m)
        kind = rng.integers(0, 5) if not first else 1
        if kind == 0:
            s = np.sin(2 * np.pi * rng.uniform(300, 3000) * tt)
        elif kind == 1:
            s = np.tanh(8 * np.sin(2 * np.pi * rng.uniform(120, 900) * tt))
        elif kind == 2:
            s = _filt(rng.standard_normal(m), "bp", 1500, 9000)
        elif kind == 3:
            s = rng.standard_normal(m)
        else:
            s = np.zeros(m)
        bits = int(rng.integers(3, 7))
        s = np.round(s * 2 ** bits) / 2 ** bits
        hold = int(rng.integers(3, 20))
        s = np.repeat(s[::hold], hold)[:m]
        reps = int(rng.integers(1, 4))
        s = np.tile(s, reps)
        f = min(len(s), _n(0.0008))
        if f > 1:
            s[:f] *= np.linspace(0, 1, f)
            s[-f:] *= np.linspace(1, 0, f)
        p = rng.choice([-0.9, -0.5, 0.0, 0.5, 0.9])
        _place(buf, s * rng.uniform(0.4, 1.0) * (1.0 if first else 0.8), tpos, 0.6, float(p))
        tpos += len(s) / SR + rng.uniform(0.0, 0.02)
        first = False
    buf = _filt(buf, "highpass", 60)
    x = _verb(buf, rt60=0.3, wet=0.06, damp=0.3, seed=281 + v)
    return x, 0.0035, {"loop": [0.03, 0.40]}


def scratch(v: int, rng):
    dur = 0.62
    n = _n(dur)
    # fuente: tono vocal con formantes + ruido
    L = _n(2.0)
    f0 = 180 * (1 + 0.01 * np.sin(2 * np.pi * 5 * _t(L)))
    src = _wt_osc(f0, harm=60)
    src += 0.25 * rng.standard_normal(L)
    fm = sum(g * _filt(src, "bp", lo, hi) for lo, hi, g in ((550, 850, 1.0), (1000, 1400, 0.7), (2300, 2900, 0.4)))
    t = _t(n)
    # baby scratch: dos empujes adelante-atras
    T = 0.24
    pos = 0.6 + 0.16 * (1 - np.cos(2 * np.pi * np.clip(t, 0, 2 * T) / T)) / 2
    y = np.interp(pos * SR, np.arange(L), fm)
    speed = np.abs(np.gradient(pos * SR))
    y *= np.clip(speed / (speed.max() + 1e-9) * 1.4, 0, 1) ** 0.6
    y = _filt(y, "highpass", 80)
    cr = (rng.random(n) < 0.0015) * rng.standard_normal(n)
    y += 0.3 * _filt(cr, "bp", 1500, 7000)
    x = _verb(_stereo(y), rt60=0.3, wet=0.06, damp=0.4, seed=291)
    x[-_n(0.02):] *= np.linspace(1, 0, _n(0.02))[:, None]
    return x, float(np.argmax(speed[: _n(T / 2)]) / SR)


def paper_rip(v: int, rng):
    dur = 0.8
    n = _n(dur)
    t = _t(n)
    walk = np.cumsum(rng.standard_normal(n // 480 + 2)) * 0.12
    inten = 0.75 + 0.25 * np.tanh(np.interp(t, np.linspace(0, dur, len(walk)), walk))
    inten *= _rc(t / 0.015) * (1 - _rc((t - 0.56) / 0.09))
    buf = np.zeros((n, 2))
    for ch, (lo, hi) in enumerate(((1200, 7000), (1500, 8500))):
        rate = 2600.0 * inten
        imp = (rng.random(n) < rate / SR) * rng.lognormal(0, 0.7, n) * rng.choice([-1, 1], n)
        k1 = _burst(_n(0.003), rng, 0.0004, lo=lo, hi=hi)
        k2 = _burst(_n(0.004), rng, 0.0008, lo=lo * 0.6, hi=hi * 0.7)
        sel = rng.random(n) < 0.5
        c = signal.fftconvolve(imp * sel, k1)[:n] + signal.fftconvolve(imp * ~sel, k2)[:n]
        fib = _filt(rng.standard_normal(n), "bp", 700, 5000)
        fib *= 1 + 0.6 * np.sin(2 * np.pi * rng.uniform(55, 85) * t)
        buf[:, ch] = c * 0.9 + fib * 0.22 * inten
    buf *= inten[:, None] ** 0.5
    x = _verb(buf, rt60=0.35, wet=0.08, damp=0.4, seed=301)
    return x, 0.012, {"loop": [0.06, 0.50]}


def pen_scribble(v: int, rng):
    dur = 1.25
    n = _n(dur)
    t = _t(n)
    env = np.zeros(n)
    fcs = np.zeros(n)
    tp = 0.004
    while tp < dur - 0.12:
        L = rng.uniform(0.09, 0.17)
        m = (t >= tp) & (t < tp + L)
        u = (t[m] - tp) / L
        env[m] = np.maximum(env[m], (0.15 + 0.85 * np.sin(np.pi * u) ** 1.2) * rng.uniform(0.6, 1.0))
        fcs[m] = rng.uniform(2500, 4500)
        tp += L * rng.uniform(0.85, 1.0)
    fcs[fcs == 0] = 3500
    fcs = np.convolve(fcs, np.ones(_n(0.01)) / _n(0.01), mode="same")
    tex = np.abs(_filt(rng.standard_normal(n), "lowpass", 300.0))
    tex /= tex.max() + 1e-9
    src = _colored(n, rng, -1.0) * (0.55 + 0.45 * tex)
    y = _tvf(src, lambda f, tt: _bp(f, np.interp(tt, t, fcs), 0.8) * _hp(f, 1200, 2))
    y += 0.12 * _filt(rng.standard_normal(n), "lowpass", 600)
    y *= env
    y *= 1 - _rc((t - (dur - 0.08)) / 0.08)
    pan = 0.25 * np.sin(2 * np.pi * 0.8 * t)
    th = (pan + 1) * math.pi / 4
    x = np.stack([y * np.cos(th), y * np.sin(th)], axis=1) * math.sqrt(2)
    x = _verb(x, rt60=0.25, wet=0.05, damp=0.4, seed=311)
    return x, 0.02, {"loop": [0.15, 1.05]}


def record_stop(v: int, rng):
    dur = 1.05
    n = _n(dur)
    L = _n(2.2)
    tl = _t(L)
    chord = sum(_wt_osc(np.full(L, f), phase0=rng.uniform()) for f in (130.81, 164.81, 196.0, 261.63)) / 4
    chord = _filt(chord, "lowpass", 3000)
    kick = np.zeros(L)
    for k0 in np.arange(0, 2.2, 0.5):
        u = np.maximum(tl - k0, 0)
        kick += np.sin(_chirp_phase(np.where(tl >= k0, 50 + 90 * np.exp(-u / 0.03), 0))) * _env(tl, 0.001, 0.12, k0)
    hat = np.zeros(L)
    for h0 in np.arange(0.25, 2.2, 0.5):
        hat += _burst(L, rng, 0.02, lo=6000, hi=14000, t0=h0)
    src = 0.5 * chord + 0.6 * kick + 0.12 * hat
    t = _t(n)
    ts = 0.05
    T = 0.85
    speed = np.where(t < ts, 1.0, np.clip(1 - (t - ts) / T, 0, 1) ** 1.8)
    pos = np.cumsum(speed) + _n(0.2)
    y = np.interp(pos, np.arange(L), src)
    y = _tvf(y, lambda f, tt: _lp(f, 100 + 12000 * np.interp(tt, t, speed) ** 1.5, 2))
    y *= np.clip(speed * 3, 0, 1) ** 0.5
    y = _filt(y, "highpass", 35)
    x = _verb(_stereo(y), rt60=0.4, wet=0.06, damp=0.5, seed=321)
    return x, ts


# ---------------------------------------------------------------- registro
# name: (fn, variantes, categoria, loudness objetivo 100 ms, tags, descripcion)
# Objetivos calibrados contra una voz entregada a -14 LUFS: con gain_db 0 el SFX queda al nivel
# de las silabas fuertes de la voz (prominente); con las ganancias del director (-2 a -7 dB) y el
# ducking de SFX (-1.5 dB) queda de 3 a 8 LU bajo la voz, como en el short-form de referencia.
# Los transitorios muy cortos (click, tick, whip) quedan bajo su objetivo por el techo de pico.
SPECS: dict[str, tuple] = {
    "whoosh_soft": (whoosh_soft, 3, "whoosh", -9.5, ["whoosh", "transition", "movement", "soft", "transicion", "movimiento"], "Paso de aire suave (transiciones tranquilas)"),
    "whoosh_fast": (whoosh_fast, 3, "whoosh", -8.0, ["whoosh", "transition", "fast", "movement", "transicion", "rapido"], "Whoosh rapido con doppler (cortes, entradas de texto)"),
    "swoosh_up": (swoosh_up, 2, "whoosh", -8.0, ["swoosh", "rise", "up", "reveal", "sube", "aparece"], "Barrido ascendente que culmina en el golpe"),
    "swoosh_down": (swoosh_down, 2, "whoosh", -9.0, ["swoosh", "fall", "down", "exit", "baja", "sale"], "Barrido descendente (salidas)"),
    "whip": (whip, 3, "whoosh", -7.0, ["whip", "whip pan", "transition", "latigazo", "transicion"], "Latigazo con chasquido (whip pan)"),
    "pop": (pop, 3, "ui", -9.0, ["pop", "appear", "sticker", "icon", "aparece"], "Pop con caida de tono (aparece un elemento)"),
    "pop_soft": (pop_soft, 2, "ui", -10.0, ["pop", "soft", "subtle", "suave"], "Pop suave y redondo"),
    "bubble": (bubble, 2, "ui", -10.0, ["bubble", "blup", "cute", "burbuja"], "Burbujas ascendentes"),
    "click": (click, 3, "ui", -11.0, ["click", "button", "ui", "mouse", "boton"], "Click de interfaz (presion y liberacion)"),
    "tick": (tick, 2, "ui", -11.0, ["tick", "clock", "counter", "reloj", "contador"], "Tick corto (contadores, relojes)"),
    "typing": (typing, 1, "ui", -10.0, ["typing", "keyboard", "type", "teclado", "escribir"], "Tecleo (texto que se escribe)"),
    "shutter": (shutter, 1, "ui", -10.0, ["shutter", "camera", "photo", "camara", "foto"], "Obturador de camara"),
    "swipe": (swipe, 2, "ui", -10.0, ["swipe", "slide", "card", "desliza", "tarjeta"], "Deslizar (tarjetas, paneles)"),
    "notification": (notification, 1, "tonal", -10.0, ["notification", "message", "alert", "notificacion", "mensaje"], "Notificacion de dos notas (marimba)"),
    "ding": (ding, 2, "tonal", -10.0, ["ding", "bell", "correct", "idea", "campana", "acierto"], "Ding FM limpio"),
    "bell": (bell, 1, "tonal", -9.0, ["bell", "ring", "campana", "anuncio"], "Campana con parciales reales"),
    "chime": (chime, 1, "tonal", -10.0, ["chime", "magic", "reveal", "arpegio", "magia"], "Arpegio de celesta (revelaciones)"),
    "sparkle": (sparkle, 2, "tonal", -10.0, ["sparkle", "shine", "clean", "new", "brillo", "limpio"], "Destellos agudos (brillo, limpio, nuevo)"),
    "success": (success, 1, "tonal", -10.0, ["success", "check", "done", "exito", "listo"], "Exito (arpegio mayor)"),
    "cash": (cash, 1, "tonal", -8.0, ["cash", "money", "price", "register", "dinero", "precio"], "Caja registradora: palanca, campana, monedas y cajon"),
    "coin": (coin, 1, "tonal", -10.0, ["coin", "money", "price", "moneda", "costo"], "Moneda que cae y vibra"),
    "riser_short": (riser_short, 1, "riser", -7.0, ["riser", "build", "tension", "tension", "sube"], "Riser corto (1.6 s) que culmina en el golpe"),
    "riser_long": (riser_long, 1, "riser", -7.0, ["riser", "build", "tension", "intro", "sube"], "Riser largo (4 s) que culmina en el golpe"),
    "impact_soft": (impact_soft, 2, "impact", -7.0, ["impact", "hit", "thud", "golpe", "enfasis"], "Golpe suave con cuerpo"),
    "impact_boom": (impact_boom, 1, "impact", -4.0, ["impact", "boom", "cinematic", "drop", "golpe", "dramatico"], "Impacto cinematografico con sub y cola"),
    "sub_drop": (sub_drop, 1, "impact", -8.0, ["sub", "drop", "bass", "grave", "caida"], "Caida de sub grave"),
    "heartbeat": (heartbeat, 1, "impact", -9.0, ["heartbeat", "tension", "suspense", "corazon", "latido"], "Latido doble"),
    "glitch": (glitch, 3, "texture", -10.0, ["glitch", "digital", "error", "tech", "falla"], "Glitch digital (stutter, bitcrush)"),
    "scratch": (scratch, 1, "texture", -9.0, ["scratch", "vinyl", "dj", "stop", "rayon"], "Scratch de vinilo"),
    "paper_rip": (paper_rip, 1, "texture", -10.0, ["paper", "rip", "tear", "papel", "rasgar"], "Papel que se rasga"),
    "pen_scribble": (pen_scribble, 1, "texture", -12.0, ["pen", "scribble", "write", "draw", "plumon", "dibujar"], "Plumon sobre papel (garabatos que se dibujan)"),
    "record_stop": (record_stop, 1, "texture", -8.0, ["record", "stop", "tape", "freeze", "alto", "disco"], "Disco que se frena (corte dramatico de musica)"),
}
NAMES = list(SPECS)

# tonica (clase de altura, 0 = Do) de los SFX tonales, por variante: el mezclador los transpone a
# la tonalidad de la musica (relativa mayor en tonalidades menores)
ROOTS: dict[str, list[int]] = {
    "ding": [4, 7],            # Mi6, Sol6
    "bell": [7],               # Sol5
    "chime": [4],              # arpegio de Mi mayor
    "sparkle": [4, 4],         # pentatonica de Mi mayor
    "success": [0],            # arpegio de Do mayor
    "cash": [0],               # campanas Do7 + Mi7
    "notification": [9],       # La5 -> Mi6
    "record_stop": [0],        # acorde de Do mayor
}


def _seed(name: str, v: int) -> int:
    return int.from_bytes(hashlib.blake2b(f"sfx:{name}:{v}".encode(), digest_size=4).digest(), "little")


def _finalize(x: np.ndarray, target_lufs: float, rng: np.random.Generator,
              max_limit_db: float = MAX_LIMIT_DB) -> tuple[np.ndarray, dict]:
    """DC y ultrasonidos, recorte de cola, fundidos, loudness por ventana corta y techo de pico."""
    x = as_stereo(x)
    x = _filt(x, "highpass", 18.0)
    x = _filt(x, "lowpass", 19000.0, order=4)  # sin ultrasonidos (solo gastan headroom)
    pk = np.abs(x).max()
    if pk <= 0:
        return x, {"lufs_100ms": -99.0, "peak_db": -99.0}
    thr = pk * 10 ** (-66 / 20)
    idx = np.nonzero(np.abs(x).max(axis=1) > thr)[0]
    end = min(x.shape[0], int(idx[-1]) + _n(0.03)) if idx.size else x.shape[0]
    x = x[:end].copy()
    k = min(end, _n(0.03))
    x[-k:] *= (1 - _rc(np.linspace(0, 1, k)))[:, None]
    k0 = min(end, _n(0.0005))
    x[:k0] *= np.linspace(0, 1, k0)[:, None]
    lu = peak_window_lufs(x, 0.1)
    g = 10 ** ((target_lufs - lu) / 20)
    tp = true_peak_db(x * g)
    if tp > PEAK_CEIL_DB:
        # masterizado del SFX: hasta max_limit_db de limitacion de transitorio; el resto,
        # bajando la ganancia
        excess = tp - PEAK_CEIL_DB
        if excess > max_limit_db:
            g *= 10 ** (-(excess - max_limit_db) / 20)
        x, _ = limit_true_peak(x * g, PEAK_CEIL_DB + 0.1, lookahead_s=0.002, release_db_s=MASTER_RELEASE_DB_S)
    else:
        x = x * g
    # dither TPDF a 16 bits (determinista)
    lsb = 1.0 / 32768
    x = x + (rng.random(x.shape) - rng.random(x.shape)) * lsb
    return x, {"lufs_100ms": round(peak_window_lufs(x, 0.1), 2), "peak_db": round(true_peak_db(x), 2)}


def _render(name: str, variant: int = 0) -> tuple[np.ndarray, float, dict, dict]:
    fn, nv, category, target, _tags, _desc = SPECS[name]
    variant = int(variant) % nv
    rng = np.random.default_rng(_seed(name, variant))
    res = fn(variant, rng)
    x, hit = res[0], res[1]
    extra = dict(res[2]) if len(res) > 2 else {}
    y, meas = _finalize(x, target, np.random.default_rng(_seed(name, variant) + 1),
                        MAX_LIMIT_BY_CAT.get(category, MAX_LIMIT_DB))
    dur = y.shape[0] / SR
    hit = float(min(max(hit, 0.0), dur))
    if "loop" in extra:
        a, b = extra["loop"]
        b = min(float(b), dur - 0.05)
        extra["loop"] = [round(float(a), 4), round(b, 4)] if b - float(a) > 0.1 else None
        if extra["loop"] is None:
            del extra["loop"]
    roots = ROOTS.get(name)
    if roots:
        extra["root"] = int(roots[min(variant, len(roots) - 1)])
    return y, hit, meas, extra


def render(name: str, variant: int = 0) -> tuple[np.ndarray, float, dict]:
    """Sintetiza un SFX (variante 0-based). Devuelve (audio (n,2), hit_s, medidas)."""
    y, hit, meas, _extra = _render(name, variant)
    return y, hit, meas


def _file(name: str, v: int) -> str:
    return f"{name}.flac" if v == 0 else f"{name}_{v + 1}.flac"


# funciones del mezclador que intervienen en el masterizado de cada SFX (medicion y limitador)
_MASTER_DEPS = ("as_stereo", "_ch2", "k_weight", "_block_power", "_lk", "_tp_iter", "_tp_per_sample",
                "_tp_max", "true_peak_db", "peak_window_lufs", "_gr_curve", "limit_true_peak", "undb", "db")


def _code_hash() -> str:
    """Firma del generador: este modulo + SOLO el codigo de audio.py que usa el masterizado
    (un cambio en el mezclador que no toca medicion/limitador no regenera los FLAC)."""
    from . import audio as _a
    h = hashlib.sha1(Path(__file__).read_bytes())
    for nm in _MASTER_DEPS:
        h.update(inspect.getsource(getattr(_a, nm)).encode())
    h.update(np.asarray(_a._K_SOS).tobytes())
    h.update(f"{SR}:{VERSION}".encode())
    return h.hexdigest()[:16]


@contextmanager
def _lock(out: Path):
    """Candado exclusivo entre procesos (pytest y el render pueden generar a la vez). El archivo
    del candado vive en el directorio temporal del sistema, no en la carpeta versionada."""
    key = hashlib.sha1(str(Path(out).resolve()).encode()).hexdigest()[:12]
    lp = Path(tempfile.gettempdir()) / f"reelfy_sfx_{key}.lock"
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows: sin candado (solo se genera en desarrollo/CI)
        yield
        return
    with open(lp, "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _job(args):
    name, v, out_dir = args
    import soundfile as sf
    y, hit, meas, extra = _render(name, v)
    f = _file(name, v)
    dst = Path(out_dir) / f
    tmp = dst.with_name(f".{f}.{os.getpid()}.tmp")
    sf.write(str(tmp), y.astype(np.float32), SR, subtype="PCM_16", format="FLAC")
    os.replace(tmp, dst)   # atomico: un lector nunca ve un FLAC a medias
    return name, v, {"file": f, "dur": round(y.shape[0] / SR, 4), "hit": round(hit, 4), **meas, **extra}


def _fresh(out: Path, sig: str, todo: list[str]) -> dict | None:
    cat_f, build_f = out / "catalog.json", out / "_build.json"
    if not (cat_f.exists() and build_f.exists()):
        return None
    try:
        b = json.loads(build_f.read_text())
        cat = json.loads(cat_f.read_text())
        ok = b.get("hash") == sig and all(
            n in cat and all((out / vv["file"]).exists() for vv in cat[n]["variants"]) for n in todo)
        return cat if ok else None
    except Exception:
        return None


def generate_all(force: bool = False, out_dir: str | Path | None = None, names: list[str] | None = None,
                 workers: int | None = None, verbose: bool = False) -> dict:
    """Genera todo el catalogo (idempotente, atomico, con candado). Devuelve el catalogo."""
    out = Path(out_dir) if out_dir else assets.SFX_DIR
    out.mkdir(parents=True, exist_ok=True)
    cat_f = out / "catalog.json"
    sig = _code_hash()
    todo = [n for n in (names or NAMES) if n in SPECS]
    if not force:
        cat = _fresh(out, sig, todo)
        if cat is not None:
            return cat
    with _lock(out):
        if not force:
            cat = _fresh(out, sig, todo)      # otro proceso lo genero mientras esperabamos
            if cat is not None:
                return cat
        for stale in out.glob(".*.tmp"):      # restos de una generacion interrumpida
            stale.unlink(missing_ok=True)
        t0 = time.perf_counter()
        jobs = [(n, v, str(out)) for n in todo for v in range(SPECS[n][1])]
        if workers is None:
            workers = max(1, min(8, (os.cpu_count() or 2) - 2))
        res = None
        if workers > 1 and len(jobs) > 4:
            from concurrent.futures import ProcessPoolExecutor
            import multiprocessing as mp
            try:
                with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
                    res = list(ex.map(_job, jobs))
            except Exception as e:   # entorno sin spawn usable (app congelada, __main__ raro): en serie
                if verbose:
                    print(f"sfx: pool de procesos no disponible ({type(e).__name__}); se genera en serie")
                res = None
        if res is None:
            res = [_job(j) for j in jobs]
        cat = {}
        if cat_f.exists() and names:
            try:
                cat = json.loads(cat_f.read_text())
            except Exception:
                cat = {}
        by: dict[str, dict] = {}
        for name, v, e in res:
            by.setdefault(name, {})[v] = e
        for name in todo:
            fn, nv, category, target, tags, desc = SPECS[name]
            vs = [by[name][v] for v in range(nv)]
            variants = []
            for x in vs:
                vv = {"file": x["file"], "dur": x["dur"], "hit": x["hit"]}
                for k in ("root", "loop"):
                    if k in x:
                        vv[k] = x[k]
                variants.append(vv)
            entry = {"file": vs[0]["file"], "dur": vs[0]["dur"], "hit": vs[0]["hit"], "tags": tags,
                     "variants": variants, "category": category, "desc": desc,
                     "lufs_100ms": vs[0]["lufs_100ms"], "peak_db": max(x["peak_db"] for x in vs)}
            for k in ("root", "loop"):
                if k in vs[0]:
                    entry[k] = vs[0][k]
            cat[name] = entry
        cat = {k: cat[k] for k in NAMES if k in cat}
        _write_atomic(cat_f, json.dumps(cat, indent=1, ensure_ascii=False))
        _write_atomic(out / "_build.json", json.dumps({"hash": sig, "version": VERSION, "sr": SR}))
        if verbose:
            print(f"sfx: {len(jobs)} archivos en {time.perf_counter() - t0:.1f} s -> {out}")
        return cat


if __name__ == "__main__":  # pragma: no cover
    import sys
    generate_all(force="--force" in sys.argv, verbose=True)
