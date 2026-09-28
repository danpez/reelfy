"""Transiciones en las uniones de tomas (docs/MOTION-ENGINE.md, secciones 3.1 y 4.2b).

Firma normativa:  apply(img, prev, item, p, ctx) -> skia.Image
  img   cuadro actual ya compuesto (en las que mezclan A y B: B en vivo)
  prev  ultimo cuadro compuesto antes de la union, congelado (A)
  p     progreso [0,1] a lo largo de la ventana de la transicion
  WINDOWS[kind] = fraccion de la ventana ANTES de la union (0.5 centrada, 0.0 empieza en la union)

Kinds: cut, flash, light_leak, whip, zoom_blur, glitch (centradas: solo procesan el cuadro actual,
el cambio A->B ocurre bajo el pico del efecto) y film_burn, torn_paper, circle, slide (mezclan
prev=A congelado con img=B en vivo; su ventana SIEMPRE empieza en la union, sin importar `align`).

Parametros (ademas de id, kind, at, dur, enabled; todos opcionales y tolerantes a basura):
  intensity 0..1   fuerza del efecto; 0 = corte seco (devuelve img)
  align            auto|center|start|end (solo centradas; ver effective_frac)
  dir              left|right|up|down (whip, slide, torn_paper)
  color            flash: white|warm|cool|hex; light_leak/film_burn: warm|gold|rose|cool|hex
                   (un hex se asigna al preset de tono mas cercano o rota la rampa; los neutros
                   caen al default); glitch: acento (neutros se ignoran); torn_paper: papel;
                   circle: color del aro (solo con ring=true)
  x, y             zoom_blur/circle: centro (sin x,y usa el rostro por cuadro, con retencion);
                   film_burn/torn_paper: punto a proteger (rostro de A; alias `avoid` [x,y])
  gate, peak       light_leak: barra del marco de pelicula (bool) y pico whiteout|partial
  mode             torn_paper: split|sweep
  ring             circle: aro fino en el borde (bool)

Estrategia de rendimiento (CPU): todo lo que es por pixel y suave (desenfoques, fugas de luz,
campos de quemado) se calcula a resolucion reducida con numpy/OpenCV y se escala con cv2.resize;
skia solo compone a 1:1 con modos de mezcla (cada pasada ~2 ms en 1080x1920) y dibuja lo
vectorial (bordes de papel, circulo). Los desenfoques direccionales reducen solo el eje del
movimiento, asi el otro eje conserva todo su detalle.

Invariantes que prueban los tests: p=0 y p=1 no alteran el cuadro (centradas) o devuelven A y B
exactos (mezclas); intensity 0 = corte; mismo item + seed = mismo resultado.
"""
from __future__ import annotations

import colorsys
import math
import threading
from collections import OrderedDict

import cv2
import numpy as np
import skia

from . import ctx as C
from . import ease as E

KINDS = ("cut", "flash", "light_leak", "whip", "zoom_blur", "glitch",
         "film_burn", "torn_paper", "circle", "slide")

# fraccion de la ventana antes de la union (contrato 4.2b)
WINDOWS: dict[str, float] = {
    "cut": 0.5, "flash": 0.5, "light_leak": 0.5, "whip": 0.5, "zoom_blur": 0.5, "glitch": 0.5,
    "film_burn": 0.0, "torn_paper": 0.0, "circle": 0.0, "slide": 0.0,
}
MIXERS = ("film_burn", "torn_paper", "circle", "slide")
CENTERED = ("flash", "light_leak", "whip", "zoom_blur", "glitch")

DIRS = ("left", "right", "up", "down")
ALIGNS = ("auto", "center", "start", "end")
_ALIGN_FRAC = {"center": 0.5, "start": 0.0, "end": 1.0}

# punto que protegen film_burn y torn_paper cuando el item no trae x,y: donde suele estar el
# rostro en un video vertical a camara (el director lo llena con el rostro real de la toma A)
PROTECT_DEFAULT = (0.5, 0.36)
_BURN_AVOID_DEFAULT = PROTECT_DEFAULT  # nombre anterior (compatibilidad)

# valores por defecto de cada kind (el director y el inspector parten de aqui)
DEFAULTS: dict[str, dict] = {
    "cut": {"dur": 0.0},
    "flash": {"dur": 0.30, "color": "white"},
    "light_leak": {"dur": 0.70, "color": "warm", "gate": False, "peak": "whiteout"},
    "whip": {"dur": 0.36, "dir": "left"},
    "zoom_blur": {"dur": 0.40, "x": None, "y": None},
    "glitch": {"dur": 0.36, "color": None},
    "film_burn": {"dur": 0.60, "color": "warm", "x": None, "y": None},
    "torn_paper": {"dur": 0.60, "dir": "left", "color": "#F2EEE6", "mode": "split", "x": None, "y": None},
    "circle": {"dur": 0.60, "x": None, "y": None, "ring": False, "color": None},
    "slide": {"dur": 0.42, "dir": "left"},
}

# SFX sugeridos (catalogo de assets) y desfase del golpe visual en las mezclas, en fraccion de
# `dur` desde el inicio de la ventana (hit_time lo calcula; en las centradas es el pico = union).
SFX: dict[str, list[str]] = {
    "cut": [], "flash": ["impact_soft", "shutter"], "light_leak": ["whoosh_soft", "swoosh_up"],
    "whip": ["whip", "whoosh_fast"], "zoom_blur": ["whoosh_fast", "riser_short"],
    "glitch": ["glitch"], "film_burn": ["whoosh_soft", "riser_short"],
    "torn_paper": ["paper_rip"], "circle": ["swoosh_up", "pop_soft"], "slide": ["swipe", "whoosh_fast"],
}
HIT: dict[str, float] = {
    "cut": 0.0, "flash": 0.0, "light_leak": 0.0, "whip": 0.0, "zoom_blur": 0.0, "glitch": 0.0,
    "film_burn": 0.25, "torn_paper": 0.05, "circle": 0.3, "slide": 0.5,
}

LEAK_COLORS = ("warm", "gold", "rose", "cool")


# =========================================================================== esquema (inspector)
def _p(k: str, type_: str, label: str, default=None, **kw) -> dict:
    """Mismo formato que schema.P (k, type, label, default, ...)."""
    d = {"k": k, "type": type_, "label": label, "default": default}
    d.update(kw)
    return d


def _s_dur(d, lo, hi):
    return _p("dur", "num", "Duración", d, min=lo, max=hi, step=0.01, unit="s", req=True)


_S_INT = _p("intensity", "num", "Intensidad", 1.0, min=0.0, max=1.0, step=0.05)
_S_ALIGN = _p("align", "seg", "Alineación con la unión", "auto", opts=list(ALIGNS))


def _s_dir(d="left"):
    return _p("dir", "seg", "Dirección", d, opts=list(DIRS))


def _s_xy(what: str):
    return [_p("x", "num", f"{what} X", None, min=0.0, max=1.0, step=0.005, nullable=True),
            _p("y", "num", f"{what} Y", None, min=0.0, max=1.0, step=0.005, nullable=True)]


# PARAMS[kind] = parametros propios del kind (sin kind/at/enabled/locked) en formato de schema.py:
# schema puede armar el inspector con [kind, ...COMMON_EVENT] + PARAMS[kind] sin duplicar nada.
PARAMS: dict[str, list[dict]] = {
    "cut": [],
    "flash": [_s_dur(0.30, 0.1, 1.0), _S_INT, _S_ALIGN,
              _p("color", "color", "Color", "white", presets=["white", "warm", "cool"])],
    "light_leak": [_s_dur(0.70, 0.3, 1.6), _S_INT, _S_ALIGN,
                   _p("color", "color", "Tono", "warm", presets=list(LEAK_COLORS)),
                   _p("peak", "seg", "Pico", "whiteout", opts=["whiteout", "partial"]),
                   _p("gate", "bool", "Barra del marco de película", False)],
    "whip": [_s_dur(0.36, 0.2, 0.8), _S_INT, _S_ALIGN, _s_dir("left")],
    "zoom_blur": [_s_dur(0.40, 0.2, 0.9), _S_INT, _S_ALIGN, *_s_xy("Centro")],
    "glitch": [_s_dur(0.36, 0.15, 1.0), _S_INT, _S_ALIGN,
               _p("color", "color", "Acento", None, nullable=True)],
    "film_burn": [_s_dur(0.60, 0.35, 1.4), _S_INT,
                  _p("color", "color", "Tono del fuego", "warm", presets=list(LEAK_COLORS)),
                  *_s_xy("Proteger (rostro)")],
    "torn_paper": [_s_dur(0.60, 0.35, 1.4), _S_INT, _s_dir("left"),
                   _p("mode", "seg", "Modo", "split", opts=["split", "sweep"]),
                   _p("color", "color", "Papel", "#F2EEE6"),
                   *_s_xy("Proteger (rostro)")],
    "circle": [_s_dur(0.60, 0.3, 1.2), _S_INT, *_s_xy("Centro"),
               _p("ring", "bool", "Aro en el borde", False),
               _p("color", "color", "Color del aro", "#FFFFFF", nullable=True)],
    "slide": [_s_dur(0.42, 0.2, 0.9), _S_INT, _s_dir("left")],
}

# claves validas por kind (para el validador del director): comunes + las del esquema + alias
_EVENT_KEYS = {"id", "kind", "at", "enabled", "src", "locked", "z"}
FIELDS: dict[str, set[str]] = {
    k: _EVENT_KEYS | {"dur", "intensity", "align"} | {p["k"] for p in prm}
    | ({"avoid"} if k in ("film_burn", "torn_paper") else set())
    for k, prm in PARAMS.items()
}
ALL_FIELDS: set[str] = set().union(*FIELDS.values())

SHUTTER_S = 1.0 / 30.0  # obturador fijo: el motion blur se ve igual a 30 y a 60 fps

_BLACK = skia.ColorSetARGB(255, 0, 0, 0)
_CLEAR = skia.ColorSetARGB(0, 0, 0, 0)


# =========================================================================== lectura robusta de params
def _num(v, default=None):
    """float finito o `default` (acepta numeros en texto; NaN, inf, listas, None -> default)."""
    if isinstance(v, (bool, np.bool_)):
        return float(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _bool(v, default: bool = False) -> bool:
    """Booleano estricto: 'false', '0', 'no', 'off' y '' valen False."""
    if v is None:
        return default
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return bool(v) if math.isfinite(float(v)) else default
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "si", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "", "none", "null"):
            return False
    return default


def _kind(item: dict) -> str:
    k = item.get("kind", "cut") if isinstance(item, dict) else "cut"
    return k if isinstance(k, str) else "cut"


def _param(item: dict, name: str, default=None):
    v = item.get(name)
    if v is None:
        v = DEFAULTS.get(_kind(item), {}).get(name, default)
    return default if v is None else v


def _str_opt(item: dict, name: str, options, default: str) -> str:
    v = item.get(name)
    s = v.strip().lower() if isinstance(v, str) else None
    if s in options:
        return s
    d = DEFAULTS.get(_kind(item), {}).get(name, default)
    return d if d in options else default


def _dirp(item: dict) -> str:
    return _str_opt(item, "dir", DIRS, "left")


def _dur(item: dict) -> float:
    d = _num(item.get("dur"))
    if d is None:
        d = _num(DEFAULTS.get(_kind(item), {}).get("dur"), 0.4)
    return max(0.05, d)


def _raw_dur(item: dict) -> float:
    """dur tal cual (0 o negativo = corte); invalido -> default del kind."""
    d = _num(item.get("dur"))
    if d is None:
        d = _num(DEFAULTS.get(_kind(item), {}).get("dur"), 0.0)
    return d


def _intensity(item: dict) -> float:
    return E.clamp(_num(item.get("intensity"), 1.0), 0.0, 1.0)


def _pt(x, y):
    """(x, y) normalizado, finito y acotado a [-0.5, 1.5], o None."""
    xf, yf = _num(x), _num(y)
    if xf is None or yf is None:
        return None
    return E.clamp(xf, -0.5, 1.5), E.clamp(yf, -0.5, 1.5)


def _protect_point(item: dict) -> tuple[float, float]:
    """Punto a proteger (film_burn, torn_paper): x,y del item, alias `avoid`, o el default."""
    p = _pt(item.get("x"), item.get("y"))
    if p is None:
        av = item.get("avoid")
        if isinstance(av, dict):
            p = _pt(av.get("x"), av.get("y"))
        elif isinstance(av, (list, tuple, np.ndarray)) and len(av) >= 2:
            p = _pt(av[0], av[1])
    if p is None:
        return PROTECT_DEFAULT
    return E.clamp(p[0]), E.clamp(p[1])


def _face(ctx):
    """(cx, cy, w, h) finito de ctx.face, o None (acepta tuplas, listas y ndarray)."""
    f = getattr(ctx, "face", None)
    if f is None:
        return None
    try:
        vals = [float(v) for v in list(f)[:4]]
    except (TypeError, ValueError):
        return None
    if len(vals) < 2 or not all(math.isfinite(v) for v in vals):
        return None
    return vals


_HOLD: dict = {}
_HOLD_LOCK = threading.Lock()
_HOLD_FRAMES = 20  # cuadros que se retiene el ultimo rostro visto si el tracker pierde la cara


def _center(item: dict, ctx, default: tuple[float, float]) -> tuple[float, float]:
    """Centro de zoom_blur/circle: x,y del item; si no, el rostro del cuadro; si el tracker lo
    pierde a media ventana, el ultimo rostro visto en esa misma ventana (evita saltos); si no,
    `default`. El director deberia llenar x,y (mediana del rostro) para no depender del cuadro."""
    p = _pt(item.get("x"), item.get("y"))
    if p is not None:
        return p
    key = (_key(item), getattr(ctx, "seed", 0))
    n = int(getattr(ctx, "n", 0))
    f = _face(ctx)
    with _HOLD_LOCK:
        if f is not None:
            v = (E.clamp(f[0], -0.5, 1.5), E.clamp(f[1], -0.5, 1.5))
            _HOLD[key] = (n, v)
            while len(_HOLD) > 64:
                _HOLD.pop(next(iter(_HOLD)))
            return v
        held = _HOLD.get(key)
    if held is not None and 0 < n - held[0] <= _HOLD_FRAMES:
        return held[1]
    return default


# =========================================================================== ventana y golpe
def effective_frac(item: dict) -> float:
    """Fraccion de la ventana antes de la union que se usa de verdad. Las mezclas siempre 0.0
    (prev = A congelado solo existe despues de la union); las centradas respetan
    `align` (center 0.5, start 0.0, end 1.0) y con auto/None/otro usan WINDOWS."""
    kind = _kind(item)
    if kind in MIXERS:
        return 0.0
    a = item.get("align")
    a = a.strip().lower() if isinstance(a, str) else None
    if a in _ALIGN_FRAC:
        return _ALIGN_FRAC[a]
    return WINDOWS.get(kind, 0.5)


def window(item: dict) -> tuple[float, float]:
    """(t0, t1) de la ventana en el mismo tiempo que `item['at']`."""
    dur = max(0.0, _raw_dur(item))
    at = _num(item.get("at"), 0.0)
    frac = effective_frac(item)
    return at - dur * frac, at + dur * (1.0 - frac)


def progress(item: dict, t) -> float | None:
    """p en [0,1] si t cae dentro de la ventana, si no None."""
    t = _num(t)
    if t is None:
        return None
    t0, t1 = window(item)
    if t1 - t0 <= 1e-6 or t < t0 or t > t1:
        return None
    return (t - t0) / (t1 - t0)


def hit_time(item: dict) -> float:
    """Tiempo (misma base que `at`) del golpe visual, para alinear el SFX. En las centradas el
    pico del efecto esta a mitad de la ventana (con align=center coincide con `at`)."""
    t0, t1 = window(item)
    kind = _kind(item)
    if kind not in MIXERS:
        return 0.5 * (t0 + t1)
    return t0 + (t1 - t0) * HIT.get(kind, 0.0)


# =========================================================================== utilidades
def _key(item: dict) -> str:
    k = item.get("id")
    if k is not None and k != "":
        return str(k)
    return f"{_kind(item)}@{_num(item.get('at'), 0.0):.3f}"


def _axis_sign(d: str) -> tuple[int, int]:
    """dir -> (eje numpy, signo del movimiento del contenido). axis 1 = x, 0 = y."""
    return {"left": (1, -1), "right": (1, 1), "up": (0, -1), "down": (0, 1)}.get(d, (1, -1))


def _in_pow(p: float, n: float) -> float:
    p = E.clamp(p)
    return p ** n


def _in_out_pow(p: float, n: float) -> float:
    p = E.clamp(p)
    return 0.5 * (2 * p) ** n if p < 0.5 else 1.0 - 0.5 * (2 - 2 * p) ** n


def _d_in_out_pow(p: float, n: float) -> float:
    p = E.clamp(p)
    return n * (2 * p) ** (n - 1) if p < 0.5 else n * (2 - 2 * p) ** (n - 1)


def _bell(p: float) -> float:
    """0 en los extremos, 1 en 0.5, derivada nula en los extremos (sin saltos)."""
    s = math.sin(math.pi * E.clamp(p))
    return s * s


def _surface(w: int, h: int) -> skia.Surface:
    return C.offscreen(w, h)


def _arr(img: skia.Image) -> np.ndarray:
    return C.image_to_rgba(img)


def _img(arr: np.ndarray) -> skia.Image:
    return C.image_from_rgba(np.ascontiguousarray(arr))


def _fit(img: skia.Image | None, w: int, h: int) -> skia.Image | None:
    """Asegura tamano w x h (prev podria venir de otra resolucion)."""
    if img is None or (img.width() == w and img.height() == h):
        return img
    s = _surface(w, h)
    s.getCanvas().drawImageRect(img, skia.Rect.MakeWH(w, h), C.SAMPLING)
    return s.makeImageSnapshot()


class _LRU(OrderedDict):
    """Cache pequena para estado estatico por item (campos de ruido, geometria).
    Segura entre hilos: el valor se calcula fuera del candado (es determinista)."""

    def __init__(self, maxsize: int = 48):
        super().__init__()
        self.maxsize = maxsize
        self._lock = threading.Lock()

    def get_or(self, key, make):
        with self._lock:
            if key in self:
                self.move_to_end(key)
                return self[key]
        v = make()
        with self._lock:
            self[key] = v
            while len(self) > self.maxsize:
                self.popitem(last=False)
        return v

    def clear(self):
        with self._lock:
            super().clear()
        with _HOLD_LOCK:
            _HOLD.clear()


_CACHE = _LRU(64)


def _fbm(rng: np.random.Generator, h: int, w: int, cells: tuple[int, ...] = (3, 6, 12, 24),
         gain: float = 0.55, aspect: float | None = None) -> np.ndarray:
    """Ruido fractal suave (suma de rejillas aleatorias escaladas) normalizado a media 0, std 1."""
    aspect = aspect if aspect is not None else h / max(1, w)
    acc = np.zeros((h, w), np.float32)
    amp = 1.0
    for c in cells:
        cy, cx = max(2, int(round(c * aspect))), max(2, c)
        g = rng.random((cy + 1, cx + 1)).astype(np.float32)
        acc += amp * cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)
        amp *= gain
    acc -= acc.mean()
    acc /= max(1e-6, float(acc.std()))
    return acc


def _up(arr: np.ndarray, w: int, h: int) -> np.ndarray:
    """Escala un buffer de baja resolucion a w x h (bilineal, rapido en OpenCV)."""
    if arr.shape[1] == w and arr.shape[0] == h:
        return arr
    return cv2.resize(arr, (w, h), interpolation=cv2.INTER_LINEAR)


def _lowres(w: int, h: int, div: float) -> tuple[int, int]:
    return max(16, int(w / div)), max(16, int(h / div))


# =========================================================================== color
_NAMED_COLORS = {"white": "#FFFFFF", "black": "#000000", "warm": "#FFE4BC", "cool": "#E6F2FF",
                 "gold": "#FFD98A", "rose": "#FFC7C2", "paper": "#F2EEE6", "cream": "#F5EBD6",
                 "red": "#E5484D", "orange": "#FF8A3D", "yellow": "#FFD23F", "green": "#46A758",
                 "blue": "#3E7BFA", "cyan": "#00E5FF", "magenta": "#FF2E88", "purple": "#8E4EC6"}


def _safe_color(c, fallback):
    """Color del item como '#RRGGBB': nombre conocido, hex, int o tupla; cualquier otra cosa
    (o un valor invalido) devuelve `fallback` sin excepcion."""
    if c is None:
        return fallback
    if isinstance(c, str):
        c = _NAMED_COLORS.get(c.strip().lower(), c.strip())
    try:
        r, g, b = C.rgb(c)
    except Exception:
        return fallback
    return "#%02X%02X%02X" % (r, g, b)


def _hsv(hexc: str) -> tuple[float, float, float]:
    r, g, b = (v / 255.0 for v in C.rgb(hexc))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s, v


def _ramp_lut(stops: list[tuple[float, str]]) -> np.ndarray:
    """Rampa de color (gradient map) -> LUT 256x4 RGBA uint8 (alpha 255)."""
    xs = np.array([s[0] for s in stops], np.float32)
    cols = np.array([C.rgb(s[1]) for s in stops], np.float32)
    t = np.linspace(0, 1, 256, dtype=np.float32)
    lut = np.empty((256, 4), np.uint8)
    for ch in range(3):
        lut[:, ch] = np.clip(np.interp(t, xs, cols[:, ch]), 0, 255).astype(np.uint8)
    lut[:, 3] = 255
    return lut


# rampas de fugas de luz: de rojo profundo en los bordes a amarillo-blanco en el nucleo
_LEAK_RAMPS = {
    "warm": [(0.0, "#000000"), (0.10, "#2A0500"), (0.26, "#9E1C00"), (0.42, "#F04A00"),
             (0.58, "#FF8A12"), (0.74, "#FFC444"), (0.88, "#FFE488"), (1.0, "#FFF2B4")],
    "gold": [(0.0, "#000000"), (0.12, "#2E1600"), (0.30, "#9C5A00"), (0.48, "#F29B00"),
             (0.66, "#FFC933"), (0.82, "#FFE48E"), (1.0, "#FFF3C8")],
    "rose": [(0.0, "#000000"), (0.12, "#2E0010"), (0.30, "#9E0F3A"), (0.48, "#F2345F"),
             (0.64, "#FF7F7A"), (0.80, "#FFC2A6"), (1.0, "#FFEBDD")],
    "cool": [(0.0, "#000000"), (0.12, "#001026"), (0.30, "#0A3F7A"), (0.48, "#1F86E8"),
             (0.66, "#6CC3FF"), (0.84, "#CDEEFF"), (1.0, "#EAF7FF")],
}
_FIRE_RAMP = [(0.0, "#000000"), (0.12, "#200300"), (0.26, "#6E1000"), (0.40, "#C43200"),
              (0.54, "#FF6A0A"), (0.68, "#FFA235"), (0.82, "#FFD27A"), (0.93, "#FFF0C8"), (1.0, "#FFFCF2")]
# tono de referencia de cada preset (grados HSV): un hex cercano (< 25 grados) usa el preset
_PRESET_HUES = {"warm": 24.0, "gold": 42.0, "rose": 350.0, "cool": 208.0}


def _rotate_ramp(stops: list[tuple[float, str]], dh: float) -> list[tuple[float, str]]:
    """Rota el tono de una rampa conservando su perfil de luminancia (las paradas claras
    siguen aclarando hacia crema, no hacia rosa ni blanco puro)."""
    out = []
    for x, hexc in stops:
        r, g, b = (v / 255.0 for v in C.rgb(hexc))
        y0 = 0.2126 * r + 0.7152 * g + 0.0722 * b
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        r2, g2, b2 = colorsys.hsv_to_rgb((h + dh / 360.0) % 1.0, s, v)
        y1 = 0.2126 * r2 + 0.7152 * g2 + 0.0722 * b2
        if y1 > 1e-4:
            f = y0 / y1
            r2, g2, b2 = r2 * f, g2 * f, b2 * f
            m = max(r2, g2, b2)
            if m > 1.0:  # sobrepasa: mezclar hacia blanco conservando la luminancia
                t = (m - 1.0) / max(1e-4, m - y0)
                r2, g2, b2 = (c + (y0 - c) * t for c in (r2, g2, b2))
        out.append((x, "#%02X%02X%02X" % tuple(int(E.clamp(c) * 255 + 0.5) for c in (r2, g2, b2))))
    return out


def _ramp_key(color, fire: bool = False):
    """Decide la rampa: preset por nombre; hex saturado -> preset de tono cercano o rampa base
    rotada; neutro (blanco, gris, casi sin saturacion), oscuro o invalido -> rampa base."""
    base = "fire" if fire else "warm"
    if color is None:
        return base
    if isinstance(color, str):
        s = color.strip().lower()
        if s in ("warm", "fire", "default", "auto", ""):
            return base
        if s in _LEAK_RAMPS:
            return s
    hexc = _safe_color(color, None)
    if hexc is None:
        return base
    h, sat, val = _hsv(hexc)
    if sat < 0.25 or val < 0.2:
        return base
    best, bd = None, 999.0
    for name, ph in _PRESET_HUES.items():
        d = abs((h - ph + 180.0) % 360.0 - 180.0)
        if d < bd:
            best, bd = name, d
    if bd <= 25.0:
        return base if best == "warm" else best
    return ("rot", base, int(round(h)))


def _lut_for(color, fire: bool = False) -> np.ndarray:
    rk = _ramp_key(color, fire)

    def make():
        if rk == "fire":
            return _ramp_lut(_FIRE_RAMP)
        if isinstance(rk, str):
            return _ramp_lut(_LEAK_RAMPS[rk])
        _, base, hue = rk
        stops = _FIRE_RAMP if base == "fire" else _LEAK_RAMPS["warm"]
        return _ramp_lut(_rotate_ramp(stops, hue - _PRESET_HUES["warm"]))
    return _CACHE.get_or(("lut", rk), make)


def _apply_lut(field: np.ndarray, lut: np.ndarray) -> np.ndarray:
    idx = np.clip(field * 255.0, 0, 255).astype(np.uint8)
    return lut[idx]


# =========================================================================== cut
def cut(img, prev, item, p, ctx):
    return img


# =========================================================================== flash
_FLASH_NAMED = {"white": "#FFFFFF", "warm": "#FFE4BC", "cool": "#E6F2FF"}
_FLASH_ATTACK, _FLASH_HOLD = 0.38, 0.54


def _flash_level(p: float) -> float:
    """Subida acelerada (38% de la ventana), meseta en el pico que cubre el corte (p=0.5, con
    el cuadro anterior incluido) y caida mas larga con cola."""
    if p < _FLASH_ATTACK:
        return (p / _FLASH_ATTACK) ** 2.2
    if p <= _FLASH_HOLD:
        return 1.0
    return (1.0 - (p - _FLASH_HOLD) / (1.0 - _FLASH_HOLD)) ** 2.4


def flash(img, prev, item, p, ctx):
    """Destello de camara: la imagen se sobreexpone como ganancia (conserva la saturacion,
    las luces se queman primero) y solo en el pico (1-2 cuadros) llega al color del destello."""
    k = _intensity(item)
    lvl = _flash_level(E.clamp(p)) * k
    if lvl < 0.004:
        return img
    col = _param(item, "color", "white")
    col = _FLASH_NAMED.get(col.strip().lower(), col) if isinstance(col, str) else col
    r, g, b = (v / 255.0 for v in C.rgb(_safe_color(col, "#FFFFFF")))
    wash = E.clamp(lvl) ** 4
    keep = 1.0 - wash
    # ganancia tenida por el color (un destello calido quema menos el azul)
    gains = [(1.0 + 3.0 * lvl * (0.55 + 0.45 * cc)) * keep for cc in (r, g, b)]
    m = [gains[0], 0, 0, 0, r * wash,
         0, gains[1], 0, 0, g * wash,
         0, 0, gains[2], 0, b * wash,
         0, 0, 0, 1, 0]
    w, h = img.width(), img.height()
    s = _surface(w, h)
    s.getCanvas().drawImage(img, 0, 0, paint=skia.Paint(ColorFilter=skia.ColorFilters.Matrix(m)))
    return s.makeImageSnapshot()


# =========================================================================== light leak
def _leak_state(ctx, key: str, lw: int, lh: int) -> dict:
    """Parametros estaticos de una fuga: manchas (trayectoria, tamano, ganancia), laminas de
    luz de borde nitido que cruzan en diagonal, texturas y barra del marco de pelicula."""
    def make():
        rng = ctx.rng(key + ":leak")
        # direccion principal del flujo de luz (entra por un borde y cruza en diagonal)
        ang = rng.uniform(0, 2 * math.pi)
        fx, fy = math.cos(ang), math.sin(ang)
        blobs = []
        n = int(rng.integers(4, 6))
        for i in range(n):
            core = i == 0
            # centro en el pico (p=0.5): nucleo cerca del centro, satelites repartidos
            if core:
                cx, cy = 0.5 + rng.normal(0, 0.12), 0.45 + rng.normal(0, 0.12)
            else:
                cx, cy = rng.uniform(-0.1, 1.1), rng.uniform(-0.05, 1.05)
            speed = rng.uniform(0.35, 0.9) * (0.6 if core else 1.0)
            blobs.append({
                "c": (cx, cy), "v": (fx * speed + rng.normal(0, 0.15), fy * speed + rng.normal(0, 0.15)),
                # semiejes en unidades de ancho (x) y alto (y); alargadas como las fugas reales
                "sx": rng.uniform(0.35, 0.7) * (1.5 if core else 1.0),
                "sy": rng.uniform(0.22, 0.55) * (1.4 if core else 1.0),
                "rot": rng.uniform(-0.9, 0.9) + ang,
                "gain": (1.05 if core else rng.uniform(0.45, 0.85)),
                "phase": rng.uniform(0, 1),
                "lag": 0.0 if core else rng.uniform(-0.12, 0.12),
            })
        # laminas: bandas de borde nitido (como la luz que entra por la ventanilla del proyector)
        sheets = []
        for i in range(int(rng.integers(1, 3))):
            th = ang + rng.normal(0, 0.45) + (math.pi / 2 if i else 0.0)
            sheets.append({
                "n": (math.cos(th), math.sin(th)), "c0": rng.uniform(-0.18, 0.18),
                "v": rng.uniform(0.7, 1.3) * (1 if rng.random() < 0.5 else -1),
                "hw": rng.uniform(0.07, 0.2), "curv": rng.normal(0, 0.7),
                "gain": rng.uniform(0.4, 0.7), "lag": rng.uniform(-0.08, 0.08),
            })
        # texturas organicas mas grandes que el cuadro: se recorren con deriva subpixel
        tex1 = _fbm(rng, int(lh * 1.6), int(lw * 1.6), cells=(2, 4, 8), gain=0.5)
        tex2 = _fbm(rng, int(lh * 1.6), int(lw * 1.6), cells=(2, 3, 6), gain=0.5)
        # textura anisotropa (vetas en la direccion del flujo)
        streak = _fbm(rng, int(lh * 1.6), int(lw * 1.6), cells=(3, 6, 12), gain=0.5)
        k = max(3, int(lw * 0.18)) | 1
        streak = cv2.GaussianBlur(streak, (0, 0), sigmaX=k * (abs(fx) + 0.3) / 2, sigmaY=k * (abs(fy) + 0.3) / 2)
        streak /= max(1e-6, float(streak.std()))
        yy, xx = np.mgrid[0:lh, 0:lw].astype(np.float32)
        return {"blobs": blobs, "sheets": sheets, "tex1": tex1, "tex2": tex2, "streak": streak,
                "flow": (fx, fy), "xx": xx / max(1, lw - 1), "yy": yy / max(1, lh - 1),
                "gate_y0": rng.uniform(0.5, 0.74)}
    return _CACHE.get_or(("leak", key, ctx.seed, lw, lh), make)


def _drift(tex: np.ndarray, lw: int, lh: int, ox: float, oy: float) -> np.ndarray:
    th, tw = tex.shape
    cx = tw / 2 + ox * (tw - lw) * 0.5
    cy = th / 2 + oy * (th - lh) * 0.5
    return cv2.getRectSubPix(tex, (lw, lh), (float(cx), float(cy)))


def _leak_field(ctx, item, p: float, lw: int, lh: int, g: float) -> np.ndarray:
    st = _leak_state(ctx, _key(item), lw, lh)
    xx, yy = st["xx"], st["yy"]
    aspect = lh / max(1, lw)  # para que los semiejes sean isotropos en pixeles
    acc = np.zeros((lh, lw), np.float32)
    tt = p - 0.5
    grow = 0.55 + 0.75 * g
    for b in st["blobs"]:
        cx = b["c"][0] + b["v"][0] * (tt + b["lag"])
        cy = b["c"][1] + b["v"][1] * (tt + b["lag"])
        ca, sa = math.cos(b["rot"]), math.sin(b["rot"])
        dx = (xx - cx)
        dy = (yy - cy) * aspect
        u = (dx * ca + dy * sa) / (b["sx"] * grow)
        v = (-dx * sa + dy * ca) / (b["sy"] * grow)
        # envolvente propia: cada mancha respira con un pequeno desfase
        bg = E.clamp(g * (1.0 + 0.25 * math.sin(2 * math.pi * (p * 1.3 + b["phase"]))))
        acc += (b["gain"] * bg) * cv2.exp(-(u * u + v * v))
    fx, fy = st["flow"]
    n1 = _drift(st["tex1"], lw, lh, fx * (p - 0.5) * 1.2, fy * (p - 0.5) * 1.2)
    n2 = _drift(st["tex2"], lw, lh, -fy * (p - 0.5) * 0.8, fx * (p - 0.5) * 0.8)
    sk = _drift(st["streak"], lw, lh, fx * (p - 0.5) * 0.6, fy * (p - 0.5) * 0.6)
    # laminas de borde nitido (feather ~3 px de referencia o 1.2 px de la rejilla)
    feather = max(1.2 / lw, 3.0 / 1080.0)
    dxc = xx - 0.5
    dyc = (yy - 0.5) * aspect
    for sh in st["sheets"]:
        nx, ny = sh["n"]
        along = -dxc * ny + dyc * nx
        sd = dxc * nx + dyc * ny - (sh["c0"] + sh["v"] * (tt + sh["lag"])) + sh["curv"] * along * along
        edge = np.clip((sh["hw"] - np.abs(sd)) / feather, 0.0, 1.0)
        acc += (sh["gain"] * g) * edge * np.clip(0.75 + 0.3 * n2, 0.2, 1.2)
    mod = 0.9 + 0.2 * n1 + 0.12 * n2 + 0.12 * sk
    np.clip(mod, 0.35, 1.6, out=mod)
    acc *= mod
    # tonemap suave: el nucleo tiende a amarillo palido sin quemarse a blanco plano
    return 1.0 - cv2.exp(-1.2 * acc)


def _draw_gate(c: skia.Canvas, w: int, h: int, st: dict, key: str, p: float, dur: float, k: float,
               g: float, ctx):
    """Barra del marco de pelicula (el proyector 'se sale de cuadro'): banda oscura fina de
    nucleo nitido con halo calido y una linea fina paralela. Salta por pasos a 12 fps, como
    un deslizamiento real de la pelicula (por eso no lleva desenfoque de movimiento)."""
    if not (0.26 < p < 0.8):
        return
    nsteps = max(1.0, dur * 12.0)
    step = math.floor(p * nsteps + 1e-6)
    q = step / nsteps
    u = E.clamp((q - 0.26) / 0.54)
    # solo se ve mientras la fuga es fuerte (sobre la imagen limpia se leeria como una raya gris)
    a = 0.9 * math.sin(math.pi * u) ** 0.5 * min(1.0, k) * E.smoothstep((g - 0.3) / 0.4)
    if a < 0.02:
        return
    rng = ctx.rng(f"{key}:gate:{step}")
    yc = (st["gate_y0"] + 0.2 * u + rng.normal(0, 0.004)) * h
    core = 0.0105 * h
    halo = skia.Paint(Color=C.color("#FF7A1E", 0.45 * a), BlendMode=skia.BlendMode.kScreen,
                      MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, core * 0.9))
    c.drawRect(skia.Rect.MakeLTRB(-4, yc - core * 1.4, w + 4, yc + core * 1.4), halo)
    soft = skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(0.5, ctx.px(0.8)))
    c.drawRect(skia.Rect.MakeLTRB(-4, yc - core / 2, w + 4, yc + core / 2),
               skia.Paint(Color=C.color("#120500", a), AntiAlias=True, MaskFilter=soft))
    y2 = yc + core * 1.6
    c.drawRect(skia.Rect.MakeLTRB(-4, y2, w + 4, y2 + max(1.0, 0.0022 * h)),
               skia.Paint(Color=C.color("#1C0A02", 0.7 * a), AntiAlias=True, MaskFilter=soft))


def light_leak(img, prev, item, p, ctx):
    """Fuga de luz de pelicula: manchas calidas que cruzan el cuadro con textura organica y
    laminas de borde nitido en diagonal, rampa de color rojo -> naranja -> amarillo -> crema
    (gradient map), mezcla screen y en el pico suma aditiva que lava el cuadro dejando ver la
    silueta (peak=whiteout, como el objetivo en 9.0 y 30.5 s) o una fuga parcial que deja ver
    al sujeto (peak=partial, como en 26.5 s). gate=true agrega la barra del marco de pelicula."""
    k = _intensity(item)
    p = E.clamp(p)
    # envolvente: sube acelerando hacia el corte, baja con cola un poco mas larga
    if p < 0.5:
        g = E.smoothstep(p / 0.5) ** 1.15
    else:
        g = 1.0 - E.smoothstep((p - 0.5) / 0.5) ** 0.85
    partial = _str_opt(item, "peak", ("whiteout", "partial"), "whiteout") == "partial"
    g *= k * (0.62 if partial else 1.0)
    if g < 0.006:
        return img
    w, h = img.width(), img.height()
    lw, lh = _lowres(w, h, 6 if ctx.draft else 4)
    field = _leak_field(ctx, item, p, lw, lh, E.clamp(g))
    field *= 0.55 + 0.38 * E.clamp(g)
    lut = _lut_for(_param(item, "color", "warm"))
    leak_img = _img(_up(_apply_lut(field, lut), w, h))

    s = _surface(w, h)
    c = s.getCanvas()
    c.drawImage(img, 0, 0)
    expo = (0.12 if partial else 0.24) * E.clamp(g) ** 2
    if expo > 0.01:  # sobreexposicion de la propia imagen
        c.drawImage(img, 0, 0, paint=skia.Paint(Alphaf=expo, BlendMode=skia.BlendMode.kPlus))
    c.drawImage(leak_img, 0, 0, paint=skia.Paint(BlendMode=skia.BlendMode.kScreen))
    if _bool(item.get("gate"), bool(DEFAULTS["light_leak"]["gate"])):
        # antes de la pasada aditiva: en el pico la luz invade un poco la barra
        st = _leak_state(ctx, _key(item), lw, lh)
        _draw_gate(c, w, h, st, _key(item), p, _dur(item), k, E.clamp(g), ctx)
    burn = 0.0 if partial else E.clamp(g) ** 4 * 0.2
    if burn > 0.01:
        c.drawImage(leak_img, 0, 0, paint=skia.Paint(Alphaf=burn, BlendMode=skia.BlendMode.kPlus))
    return s.makeImageSnapshot()


# =========================================================================== desenfoque direccional
def _reduce_factor(blur: float, maxf: int = 8) -> int:
    if blur < 3.0:
        return 1
    return int(max(2, min(maxf, round(blur / 4.0))))


def _box2(arr: np.ndarray, axis: int, k: float) -> np.ndarray:
    """Dos pasadas de caja (~ nucleo triangular) a lo largo de `axis`.
    Nucleo siempre impar y >= 3: centrado (sin medio pixel de corrimiento) y evita un fallo
    de cv2.blur con ancho 2 en 4 canales (anula las ultimas columnas)."""
    kk = int(round(k * 0.62))
    if kk < 2:
        return arr
    kk = max(3, kk | 1)
    ks = (kk, 1) if axis == 1 else (1, kk)
    out = cv2.blur(arr, ks, borderType=cv2.BORDER_REFLECT)
    return cv2.blur(out, ks, borderType=cv2.BORDER_REFLECT)


def _shrink(arr: np.ndarray, axis: int, f: int) -> np.ndarray:
    if f <= 1:
        return arr
    h, w = arr.shape[:2]
    size = (max(2, w // f), h) if axis == 1 else (w, max(2, h // f))
    return cv2.resize(arr, size, interpolation=cv2.INTER_AREA)


def _grow(arr: np.ndarray, w: int, h: int, scale: float = 1.0) -> np.ndarray:
    """Regresa a w x h; con scale > 1 hace un leve zoom centrado en el mismo paso."""
    if scale <= 1.0005:
        return _up(arr, w, h)
    sw, sh = int(round(w * scale)), int(round(h * scale))
    big = cv2.resize(arr, (sw, sh), interpolation=cv2.INTER_LINEAR)
    x0, y0 = (sw - w) // 2, (sh - h) // 2
    return big[y0:y0 + h, x0:x0 + w]


def _mirror_idx(n: int, shift: float) -> np.ndarray:
    idx = np.arange(n) - int(round(shift))
    per = 2 * n
    idx %= per
    return np.where(idx >= n, per - 1 - idx, idx)


# =========================================================================== whip
def _whip_offset(p: float, D: float) -> tuple[float, float]:
    """(desplazamiento, d/dp) del contenido; A sale acelerando, B entra frenando."""
    if p < 0.5:
        q = p / 0.5
        return D * q ** 3, D * 3 * q * q / 0.5
    q = (p - 0.5) / 0.5
    return -D * (1 - q) ** 3, D * 3 * (1 - q) ** 2 / 0.5


def whip(img, prev, item, p, ctx):
    """Whip pan: primera mitad A sale acelerando, segunda mitad B entra frenando, con
    desenfoque de movimiento proporcional a la velocidad y envoltura en espejo (sin bordes).
    intensity escala el recorrido (35%..90% del cuadro) y el desenfoque."""
    k = _intensity(item)
    p = E.clamp(p)
    axis, sign = _axis_sign(_dirp(item))
    w, h = img.width(), img.height()
    n = w if axis == 1 else h
    D = (0.35 + 0.55 * k) * n
    off, vel = _whip_offset(p, D)
    off *= sign
    blur = min(0.8 * n, abs(vel) / _dur(item) * SHUTTER_S * 1.3)
    scale = 1.0 + 0.06 * k * _bell(p)
    if abs(off) < 0.5 and blur < 1.0:
        return img
    arr = _arr(img)
    f = _reduce_factor(blur)
    small = _shrink(arr, axis, f)
    m = small.shape[1] if axis == 1 else small.shape[0]
    idx = _mirror_idx(m, off / f)
    moved = np.take(small, idx, axis=axis)
    moved = _box2(moved, axis, blur / f)
    out = _grow(moved, w, h, scale)
    return _img(out)


# =========================================================================== zoom blur
def zoom_blur(img, prev, item, p, ctx):
    """Zoom con desenfoque radial: A entra en zoom acelerando, B sale del zoom frenando.
    El centro queda nitido (como un radial blur real) y las orillas se estiran."""
    k = _intensity(item)
    p = E.clamp(p)
    zmax = 1.0 + 0.55 * k
    if p < 0.5:
        q = p / 0.5
        z = 1.0 + (zmax - 1.0) * q ** 3
        dz = (zmax - 1.0) * 3 * q * q / 0.5
    else:
        q = (p - 0.5) / 0.5
        z = 1.0 + (zmax - 1.0) * (1 - q) ** 3
        dz = (zmax - 1.0) * 3 * (1 - q) ** 2 / 0.5
    smear = min(0.42, dz / _dur(item) * SHUTTER_S * 1.6 / z)
    if z < 1.0005 and smear < 0.002:
        return img
    w, h = img.width(), img.height()
    cxn, cyn = _center(item, ctx, (0.5, 0.45))
    cx, cy = cxn * w, cyn * h

    s = _surface(w, h)
    c = s.getCanvas()
    c.save()
    c.translate(cx, cy)
    c.scale(z, z)
    c.translate(-cx, -cy)
    c.drawImage(img, 0, 0, C.SAMPLING_FAST)
    c.restore()

    div = 4 if not ctx.draft else 3
    rmax = math.hypot(max(cx, w - cx), max(cy, h - cy))
    if smear * rmax > 1.5 * div:
        lw, lh = _lowres(w, h, div)
        small = cv2.resize(_arr(img), (lw, lh), interpolation=cv2.INTER_AREA)
        scx, scy = cx * lw / w, cy * lh / h
        nsamp = 12 if not ctx.draft else 7
        acc = np.zeros((lh, lw, 4), np.float32)
        wsum = 0.0
        for i in range(nsamp):
            t = i / (nsamp - 1)
            zi = z * (1.0 - smear * t)
            wt = 1.0 - 0.55 * t  # la estela se desvanece
            M = np.float32([[zi, 0, scx * (1 - zi)], [0, zi, scy * (1 - zi)]])
            acc += wt * cv2.warpAffine(small, M, (lw, lh), flags=cv2.INTER_LINEAR,
                                       borderMode=cv2.BORDER_REFLECT).astype(np.float32)
            wsum += wt
        acc /= wsum
        # mascara radial: nitido en el centro, estela completa hacia las orillas
        yy, xx = np.mgrid[0:lh, 0:lw].astype(np.float32)
        r = np.sqrt(((xx - scx) * (w / lw)) ** 2 + ((yy - scy) * (h / lh)) ** 2)
        a = np.clip((smear * r - div) / (3.0 * div), 0.0, 1.0)
        acc *= a[..., None]
        acc[..., 3] = a * 255.0
        over = _up(np.clip(acc, 0, 255).astype(np.uint8), w, h)
        c.drawImage(_img(over), 0, 0)
    # leve destello en el pico: ayuda a esconder el corte
    fl = 0.18 * k * max(0.0, 1.0 - abs(p - 0.5) / 0.12)
    if fl > 0.005:
        c.drawPaint(skia.Paint(Color=C.color("#FFFFFF", fl), BlendMode=skia.BlendMode.kScreen))
    return s.makeImageSnapshot()


# =========================================================================== glitch
_GLITCH_ACCENTS = ("#00E5FF", "#FF2E88")


def _glitch_accents(item: dict) -> list[str]:
    """Acento del item solo si tiene color de verdad (un blanco/gris del director se ignora)."""
    acc = _safe_color(item.get("color"), None)
    if acc is not None:
        _h, sat, val = _hsv(acc)
        if sat >= 0.25 and val >= 0.2:
            return [acc]
    return list(_GLITCH_ACCENTS)


def glitch(img, prev, item, p, ctx):
    """Glitch digital a doces (12 fps): franjas horizontales desplazadas, separacion RGB,
    bloques de compresion, lineas de ruido y leves saltos de cuadro. Pico en el corte."""
    k = _intensity(item)
    p = E.clamp(p)
    dur = _dur(item)
    nsteps = dur * 12.0
    step = int(math.floor(p * nsteps + 1e-6))
    # todo se congela por paso de 1/12 s (incluida la envolvente): antes del corte se usa el
    # inicio del paso y despues su final, asi p=0 y p=1 quedan intactos
    pq = step / nsteps if p < 0.5 else min(1.0, (step + 1) / nsteps)
    env = math.sin(math.pi * pq) ** 0.7
    key = _key(item)
    rng = ctx.rng(f"{key}:glitch:{step}")
    g = env * k * (0.6 + 0.55 * rng.random())
    if abs(pq - 0.5) > 0.14 and rng.random() < 0.3:
        g *= 0.2  # respiros entre golpes
    elif abs(pq - 0.5) <= 0.1:
        g *= 1.3  # golpe fuerte alrededor del corte
    if g < 0.05:
        return img
    w, h = img.width(), img.height()
    px = ctx.px
    s = _surface(w, h)
    c = s.getCanvas()

    # 1) cuadro base con salto (jitter) global
    jx = int(round(rng.normal(0, 7 * g) * ctx.s))
    jy = int(round(rng.normal(0, 18 * g) * ctx.s)) if rng.random() < 0.35 else 0
    c.drawImage(img, jx, jy)
    if jx:
        c.drawImage(img, jx - math.copysign(w, jx), jy)
    if jy:
        c.drawImage(img, jx, jy - math.copysign(h, jy))

    # 2) franjas desplazadas (con envoltura)
    nb = int(3 + 11 * g)
    for _ in range(nb):
        y0 = rng.random() * h
        bh = h * float(rng.choice([0.004, 0.012, 0.03, 0.06, 0.11], p=[0.25, 0.3, 0.25, 0.14, 0.06]))
        dx = rng.normal(0, 0.1 * w * g)
        if rng.random() < 0.15:
            dx *= 2.5
        dx = int(round(dx))
        if dx == 0:
            continue
        c.save()
        c.clipRect(skia.Rect.MakeXYWH(0, y0, w, bh))
        c.drawImage(img, dx + jx, jy)
        c.drawImage(img, dx + jx - math.copysign(w, dx), jy)
        c.restore()
    base = s.makeImageSnapshot()

    # 3) separacion RGB (tres pasadas moduladas y sumadas)
    amt = px(rng.uniform(6, 22) * g)
    if amt >= 1.0:
        dy = px(rng.normal(0, 2.5 * g))
        c.clear(_BLACK)
        add = skia.BlendMode.kPlus
        full = skia.Rect.MakeWH(w, h)
        for col, ox, oy in (("#FF0000", -amt, -dy), ("#00FF00", 0.0, 0.0), ("#0000FF", amt, dy)):
            # shader con clamp: los canales desplazados no dejan orillas de color en los bordes
            sh = base.makeShader(skia.TileMode.kClamp, skia.TileMode.kClamp, C.SAMPLING_FAST,
                                 skia.Matrix.Translate(round(ox), round(oy)))
            c.drawRect(full, skia.Paint(Shader=sh, BlendMode=add, ColorFilter=skia.ColorFilters.Blend(
                C.color(col), skia.BlendMode.kModulate)))
        base = s.makeImageSnapshot()

    # 4) bloques de compresion: copias desplazadas en rejilla de 8 px y algunos de color
    accents = _glitch_accents(item)
    grid = max(4.0, px(8))
    nblk = int(rng.integers(2, 9) * g)
    for _ in range(nblk):
        bw = px(float(rng.choice([24, 40, 64, 96, 160])))
        bh = px(float(rng.choice([16, 24, 40, 64])))
        x = round(rng.random() * (w - bw) / grid) * grid
        y = round(rng.random() * (h - bh) / grid) * grid
        dst = skia.Rect.MakeXYWH(x, y, bw, bh)
        if rng.random() < 0.28:
            col = accents[int(rng.integers(0, len(accents)))]
            c.drawRect(dst, skia.Paint(Color=C.color(col, 0.35 + 0.25 * rng.random()),
                                       BlendMode=skia.BlendMode.kScreen))
        else:
            sx = min(max(0.0, x + rng.normal(0, px(60))), w - bw)
            sy = min(max(0.0, y + rng.normal(0, px(30))), h - bh)
            c.drawImageRect(base, skia.Rect.MakeXYWH(sx, sy, bw, bh), dst, C.SAMPLING_FAST)

    # 5) lineas de ruido y barrido claro
    nl = int(rng.integers(1, 6) * g)
    for _ in range(nl):
        y = rng.random() * h
        lh_ = max(1.0, px(rng.uniform(1, 4)))
        v = int(rng.uniform(150, 255))
        c.drawRect(skia.Rect.MakeXYWH(0, y, w, lh_),
                   skia.Paint(Color=skia.ColorSetARGB(int(110 * min(1.0, g)), v, v, v),
                              BlendMode=skia.BlendMode.kScreen))
    if not ctx.draft and g > 0.35:
        # scanlines muy sutiles
        pat = _scanline_shader(ctx)
        c.drawPaint(skia.Paint(Shader=pat, Alphaf=min(1.0, 0.35 * g), BlendMode=skia.BlendMode.kMultiply))
    return s.makeImageSnapshot()


def _scanline_shader(ctx) -> skia.Shader:
    period = max(2, int(round(ctx.px(4))))

    def make():
        a = np.full((period, 4, 4), 255, np.uint8)
        a[0, :, :3] = 170
        return C.image_from_rgba(a)
    img = _CACHE.get_or(("scan", period), make)
    return img.makeShader(skia.TileMode.kRepeat, skia.TileMode.kRepeat, C.SAMPLING_FAST)


# =========================================================================== slide
_SLIDE_LIN = 0.12  # parte lineal de la curva: A se mueve desde el primer cuadro (no se ve congelado)


def slide(img, prev, item, p, ctx):
    """Empuje: A sale y B entra pegados, curva rapida en el centro (con arranque que ya se
    mueve), motion blur por velocidad."""
    k = _intensity(item)
    p = E.clamp(p)
    w, h = img.width(), img.height()
    A = _fit(prev, w, h) or img
    B = img
    if p <= 0.0:
        return A
    if p >= 1.0:
        return B
    axis, sign = _axis_sign(_dirp(item))
    n = w if axis == 1 else h
    nexp = 3.0
    q = (1 - _SLIDE_LIN) * _in_out_pow(p, nexp) + _SLIDE_LIN * p
    dq = (1 - _SLIDE_LIN) * _d_in_out_pow(p, nexp) + _SLIDE_LIN
    travel = n * q
    blur = min(0.6 * n, n * dq / _dur(item) * SHUTTER_S * 0.8 * k)
    if blur < 1.5:
        s = _surface(w, h)
        c = s.getCanvas()
        oa = sign * round(travel)
        ob = oa - sign * n
        if axis == 1:
            c.drawImage(A, oa, 0)
            c.drawImage(B, ob, 0)
        else:
            c.drawImage(A, 0, oa)
            c.drawImage(B, 0, ob)
        return s.makeImageSnapshot()
    f = _reduce_factor(blur)
    a_s = _shrink(_arr(A), axis, f)
    b_s = _shrink(_arr(B), axis, f)
    m = a_s.shape[1] if axis == 1 else a_s.shape[0]
    strip = np.concatenate([a_s, b_s] if sign < 0 else [b_s, a_s], axis=axis)
    start = travel / f if sign < 0 else m - travel / f
    marg = int(blur / f) + 2
    idx = np.clip(np.arange(-marg, m + marg) + int(round(start)), 0, 2 * m - 1)
    win = np.take(strip, idx, axis=axis)
    win = _box2(win, axis, blur / f)
    win = win[:, marg:marg + m] if axis == 1 else win[marg:marg + m]
    return _img(_up(np.ascontiguousarray(win), w, h))


# =========================================================================== circle
def circle(img, prev, item, p, ctx):
    """Iris: un circulo con borde suave se abre desde un punto (x,y o rostro) revelando B, que
    llega con un ligero zoom de asentamiento; A se oscurece un poco y una sombra suave marca el
    borde. ring=true agrega un aro fino de luz (color) sobre el borde."""
    k = _intensity(item)
    p = E.clamp(p)
    w, h = img.width(), img.height()
    A = _fit(prev, w, h) or img
    B = img
    if p <= 0.0:
        return A
    if p >= 1.0:
        return B
    cxn, cyn = _center(item, ctx, (0.5, 0.5))
    cx, cy = cxn * w, cyn * h
    far = max(math.hypot(cx - x, cy - y) for x in (0, w) for y in (0, h))
    feather = ctx.px(34)
    q = 0.75 * _in_out_pow(p, 2.2) + 0.25 * p  # ya se abre desde el primer cuadro
    r = (far + 2 * feather) * q
    s = _surface(w, h)
    c = s.getCanvas()
    c.drawImage(A, 0, 0)
    dim = 0.28 * k * E.smoothstep(p / 0.8)  # A se oscurece un poco mientras se va
    # sombra suave alrededor del borde (B se abre "debajo" de A) y oscurecimiento de A en una
    # sola pasada: el gradiente radial se extiende con `dim` mas alla de la sombra
    sw = feather * 2.2
    rs = max(1.0, r + sw)
    sh_a = 0.42 * k * (1.0 - E.smoothstep((p - 0.6) / 0.4))
    if sh_a > 0.01 or dim > 0.005:
        t0 = max(0.0, (r - feather) / rs)
        shade = skia.GradientShader.MakeRadial(
            (cx, cy), rs, [C.color("#000000", max(sh_a, dim)), C.color("#000000", max(sh_a * 0.55, dim)),
                           C.color("#000000", dim)], [t0, max(t0, r / rs), 1.0])
        c.drawPaint(skia.Paint(Shader=shade))
    if r > 1.0:
        # capa con limites enteros y margen: la mascara (drawPaint) cubre TODA la capa, sin
        # filas o columnas de borde que se queden sin enmascarar
        L = max(0, math.floor(cx - r) - 2)
        T_ = max(0, math.floor(cy - r) - 2)
        R = min(w, math.ceil(cx + r) + 2)
        Bm = min(h, math.ceil(cy + r) + 2)
        if R > L and Bm > T_:
            rect = skia.Rect.MakeLTRB(L, T_, R, Bm)
            c.saveLayer(rect, None)
            zb = 1.0 + 0.08 * (1.0 - E.out_cubic(p))
            if zb > 1.002:
                c.save()
                c.translate(cx, cy)
                c.scale(zb, zb)
                c.translate(-cx, -cy)
                c.drawImage(B, 0, 0, C.SAMPLING_FAST)
                c.restore()
            else:
                c.drawImage(B, 0, 0)
            inner = max(0.0, (r - feather) / r)
            mask = skia.GradientShader.MakeRadial((cx, cy), r, [_BLACK, _CLEAR], [inner, 1.0])
            c.drawPaint(skia.Paint(Shader=mask, BlendMode=skia.BlendMode.kDstIn))
            c.restore()
        # aro opcional: linea fina y suave sobre el borde
        if _bool(item.get("ring"), False):
            col = _safe_color(item.get("color"), "#FFFFFF")
            ring_a = 0.85 * k * (1.0 - E.smoothstep((p - 0.55) / 0.45))
            if ring_a > 0.01:
                rc = max(1.0, r - feather * 0.55)
                # halo ancho y tenue + nucleo fino (sin MaskFilter: en un circulo de 1500 px
                # de radio el desenfoque cuesta ~15 ms)
                for aw, wd in ((0.22, 10.0), (0.35, 6.0), (1.0, 2.6)):
                    c.drawCircle(cx, cy, rc, skia.Paint(
                        Color=C.color(col, ring_a * aw), AntiAlias=True, Style=skia.Paint.kStroke_Style,
                        StrokeWidth=ctx.px(wd)))
    return s.makeImageSnapshot()


# =========================================================================== film burn
def _burn_origins(rng: np.random.Generator, face, n: int, aspect: float) -> list[tuple[float, float]]:
    """Origenes del quemado: hacia la periferia del cuadro y lejos del punto protegido."""
    out = []
    for _ in range(n):
        best, best_d = None, -1.0
        for _try in range(12):
            if rng.random() < 0.5:
                ox = rng.choice([rng.uniform(0.02, 0.28), rng.uniform(0.72, 0.98)])
                oy = rng.uniform(0.08, 0.92)
            else:
                ox = rng.uniform(0.1, 0.9)
                oy = rng.choice([rng.uniform(0.02, 0.22), rng.uniform(0.7, 0.96)])
            dface = 1.0 if not face else math.hypot(ox - face[0], (oy - face[1]) * aspect)
            if dface > 0.45:
                best = (ox, oy)
                break
            if dface > best_d:
                best, best_d = (ox, oy), dface
        out.append(best)
    return out


def _burn_state(ctx, key: str, lw: int, lh: int, protect) -> dict:
    """Campo F (0..1) por distancia a los origenes + ruido fractal (con octava fina para un
    borde carcomido), sus valores ordenados (para que el area quemada avance pareja, sin
    importar la geometria) y un ruido de burbujas (hoyos secundarios por delante del frente).
    Depende solo del plan (item + seed + punto protegido), nunca del cuadro: todos los workers
    y todos los cuadros de la ventana ven el mismo quemado."""
    def make():
        rng = ctx.rng(key + ":burn")
        yy, xx = np.mgrid[0:lh, 0:lw].astype(np.float32)
        xs, ys = xx / max(1, lw - 1), yy / max(1, lh - 1)
        aspect = lh / max(1, lw)
        d = np.full((lh, lw), 9.0, np.float32)
        norig = 1 if rng.random() < 0.72 else 2
        for i, (ox, oy) in enumerate(_burn_origins(rng, protect, norig, aspect)):
            di = np.sqrt((xs - ox) ** 2 + ((ys - oy) * aspect) ** 2) * (1.0 + 0.35 * i)
            d = np.minimum(d, di)
        n = _fbm(rng, lh, lw, cells=(2, 4, 8, 16, 32), gain=0.5)
        fine = _fbm(rng, lh, lw, cells=(64, 128), gain=0.6)
        F = d + 0.12 * n + 0.03 * fine
        F -= F.min()
        F /= max(1e-6, float(F.max()))
        F = F.astype(np.float32)
        bub = _fbm(rng, lh, lw, cells=(12, 24, 48), gain=0.6)
        return {"F": F, "Fs": np.sort(F.ravel()), "bub": bub}
    return _CACHE.get_or(("burn", key, ctx.seed, lw, lh, protect), make)


def film_burn(img, prev, item, p, ctx):
    """Quemado de pelicula: la pelicula de A se quema desde la periferia con un frente carcomido
    y burbujas que abren hoyos por delante. Perfil del frente (de A hacia B): calor que
    sobreexpone A en naranja, anillo de carbon cafe oscuro, borde incandescente amarillo-blanco
    y del lado de B una fase caliente (B sale de luz blanca y naranja). Bloom y parpadeo."""
    k = _intensity(item)
    p = E.clamp(p)
    w, h = img.width(), img.height()
    A = _fit(prev, w, h) or img
    B = img
    if p <= 0.0:
        return A
    if p >= 1.0:
        return B
    lw, lh = _lowres(w, h, 3)
    key = _key(item)
    st = _burn_state(ctx, key, lw, lh, _protect_point(item))
    F, Fs, bub = st["F"], st["Fs"], st["bub"]
    a0 = E.remap(p, 0.0, 0.9)
    area = 0.82 * E.smoothstep(a0) + 0.18 * a0  # fraccion quemada; ya avanza desde el inicio
    T = float(Fs[int(area * (Fs.size - 1))]) + E.lerp(-0.05, 0.1, area)
    d = F - T  # >0 lado A (sin quemar), <0 agujero (B)
    # burbujas: donde el ruido es alto y el frente esta cerca, la pelicula se abre antes
    dpos = np.maximum(d, 0.0)
    x = d - 0.075 * np.clip(bub - 0.85, 0.0, None) * cv2.exp(-dpos / 0.06)
    env = E.out_quad(p / 0.1) * (1.0 - E.smoothstep((p - 0.72) / 0.28))
    flick = 0.86 + 0.14 * E.wobble(p * _dur(item) * 9.0, ctx.rand(key + ":fl") * 10)
    wr, wc, wh, ws, soft = 0.03, 0.045, 0.18, 0.2, 0.01

    reveal = np.clip(-x / soft + 0.5, 0.0, 1.0)
    xp = np.maximum(x, 0.0)
    xn = np.maximum(-x, 0.0)
    rim = cv2.exp(-(x / wr) ** 2)
    char = cv2.exp(-((x - 1.5 * wr) / wc) ** 2) * (x > 0)
    heat = cv2.exp(-xp / wh) * (x > 0)
    spill = cv2.exp(-xn / ws) * (x <= 0)

    # multiplicador sobre A: carbon (cafe muy oscuro) y calor (le quita azul, tine naranja)
    ce = env * k
    mul = np.empty((lh, lw, 4), np.float32)
    mul[..., 0] = 1.0 - 0.66 * ce * char
    mul[..., 1] = (1.0 - 0.76 * ce * char) * (1.0 - 0.16 * ce * heat)
    mul[..., 2] = (1.0 - 0.84 * ce * char) * (1.0 - 0.45 * ce * heat)
    mul[..., 3] = 1.0
    mul_img = _img(_up((np.clip(mul, 0, 1) * 255).astype(np.uint8), w, h))

    # luz: borde incandescente y fase caliente de B (con bloom corto), mas la sobreexposicion
    # de A junto al frente, que se apaga sobre el anillo de carbon para que este se lea
    hot = 1.2 * rim + 0.62 * spill
    bloom = cv2.GaussianBlur(hot, (0, 0), sigmaX=max(1.0, lw * 0.014))
    glow = 0.72 * hot + 0.5 * bloom + 0.36 * np.sqrt(heat) * (1.0 - 0.85 * char)
    glow *= env * flick * k
    lut = _lut_for(_param(item, "color", "warm"), fire=True)
    glow_img = _img(_up(_apply_lut(np.clip(glow, 0, 1.25) / 1.25, lut), w, h))
    mask_img = C.image_from_gray(_up((reveal * 255).astype(np.uint8), w, h))

    s = _surface(w, h)
    c = s.getCanvas()
    c.drawImage(A, 0, 0)
    c.drawImage(mul_img, 0, 0, paint=skia.Paint(BlendMode=skia.BlendMode.kMultiply))
    c.saveLayer(None, None)
    c.drawImage(B, 0, 0)
    c.drawImage(mask_img, 0, 0, paint=skia.Paint(BlendMode=skia.BlendMode.kDstIn))
    c.restore()
    c.drawImage(glow_img, 0, 0, paint=skia.Paint(BlendMode=skia.BlendMode.kPlus))
    lift = 0.16 * k * _bell(p) * flick
    if lift > 0.005:
        c.drawPaint(skia.Paint(Color=C.color("#FF9A45", lift), BlendMode=skia.BlendMode.kScreen))
    return s.makeImageSnapshot()


# =========================================================================== torn paper
def _noise1d(rng: np.random.Generator, s: np.ndarray, cells: int, smooth: bool) -> np.ndarray:
    knots = rng.normal(0, 1, cells + 3)
    x = (s - s.min()) / max(1e-9, (s.max() - s.min())) * cells
    i = np.floor(x).astype(int)
    t = x - i
    if smooth:
        t = t * t * (3 - 2 * t)
    return knots[i] * (1 - t) + knots[i + 1] * t


def _paper_texture(ctx, color) -> skia.Image:
    def make():
        rng = ctx.rng("transitions:paper")
        n = 256
        wn = rng.normal(0, 1, (n, n))
        fy = np.fft.fftfreq(n)[:, None]
        fx = np.fft.fftfreq(n)[None, :]
        f = np.sqrt(fx * fx + fy * fy) + 1e-3
        blot = np.real(np.fft.ifft2(np.fft.fft2(wn) / f ** 1.1))
        fib = np.real(np.fft.ifft2(np.fft.fft2(rng.normal(0, 1, (n, n))) *
                                   np.exp(-((fx * 40) ** 2 + (fy * 4) ** 2))))
        v = (blot - blot.mean()) / blot.std() * 0.6 + (fib - fib.mean()) / fib.std() * 0.4
        r, g, b = C.rgb(color)
        shade = 1.0 + 0.045 * v
        arr = np.empty((n, n, 4), np.uint8)
        arr[..., 0] = np.clip(r * shade, 0, 255)
        arr[..., 1] = np.clip(g * shade, 0, 255)
        arr[..., 2] = np.clip(b * shade * 0.995, 0, 255)
        arr[..., 3] = 255
        return C.image_from_rgba(arr)
    return _CACHE.get_or(("paper", ctx.seed, str(color)), make)


def _tear_base(rng, s: np.ndarray, vertical: bool, protect, Ar: float) -> np.ndarray:
    """Posicion transversal (px de referencia) de la linea de rasgado a lo largo de s, pasando
    al lado del punto protegido (rostro) en vez de cortarlo.
    Linea vertical (movimiento horizontal): a ~0.28 del ancho desde el rostro, del lado con mas
    espacio, en diagonal que se acerca al centro lejos del rostro.
    Linea horizontal (movimiento vertical): por debajo de la barbilla (o encima si el rostro
    esta abajo), con leve inclinacion."""
    px_, py_ = protect
    if vertical:
        u_face = px_ + 0.28 if px_ <= 0.5 else px_ - 0.28
        u_face = E.clamp(u_face, 0.18, 0.82)
        span = max(py_, 1.0 - py_, 0.3)
        base = u_face + (0.5 - u_face) * 0.6 * np.abs(s - py_) / span * np.sign(s - py_)
        base += rng.uniform(-0.015, 0.015)
    else:
        u_face = py_ + 0.2 if py_ <= 0.55 else py_ - 0.2
        u_face = E.clamp(u_face, 0.18, 0.85)
        base = u_face + rng.uniform(-0.06, 0.06) * (s - px_)
    return base * Ar


def _torn_state(ctx, key: str, mode: str, vertical: bool, protect) -> dict:
    """Geometria estatica del rasgado en px de referencia (1080 de ancho): linea con forma
    grande, mediana y un borde fino irregular de 1-2 px, bandas de fibra y fibras sueltas."""
    def make():
        rng = ctx.rng(f"{key}:torn:{mode}")
        npts = 720
        Lr, Ar = (1920.0, 1080.0) if vertical else (1080.0, 1920.0)
        s = np.linspace(-0.04, 1.04, npts)
        shape = (50.0 * _noise1d(rng, s, 3, True) + 21.0 * _noise1d(rng, s, 8, True)
                 + 9.0 * _noise1d(rng, s, 26, False) + 3.5 * _noise1d(rng, s, 90, False))
        fine = 1.3 * _noise1d(rng, s, 420, False)  # carcomido fino del borde de la fibra
        if mode == "split":
            u = _tear_base(rng, s, vertical, protect, Ar) + shape
        else:
            u = shape + rng.uniform(-0.03, 0.03) * (s - 0.5) * Ar
        band_a = 7.0 + 17.0 * np.abs(_noise1d(rng, s, 14, False)) + 3.5 * np.abs(_noise1d(rng, s, 60, False))
        band_b = 2.5 + 6.0 * np.abs(_noise1d(rng, s, 20, False))
        # fibras sueltas: posicion a lo largo, largo 3-18 px ref (sesgado a cortas), angulo, alfa
        nf = 170
        fib = {"i": rng.integers(0, npts, nf), "len": 3.0 + 15.0 * rng.random(nf) ** 2,
               "ang": rng.normal(0, 0.6, nf), "side": rng.random(nf) < 0.6,
               "alpha": rng.uniform(0.3, 0.95, nf)}
        return {"s": s, "u": u, "fine": fine, "band_a": band_a, "band_b": band_b, "fib": fib,
                "Lr": Lr, "Ar": Ar, "rip_down": bool(rng.random() < 0.65),
                "theta": rng.uniform(3.0, 6.5), "lift_y": rng.uniform(0.01, 0.035)}
    return _CACHE.get_or(("torn", key, ctx.seed, mode, vertical, protect), make)


def _poly(points) -> skia.Path:
    path = skia.Path()
    path.moveTo(*points[0])
    for pt in points[1:]:
        path.lineTo(*pt)
    path.close()
    return path


def _polyline(points) -> skia.Path:
    path = skia.Path()
    path.moveTo(*points[0])
    for pt in points[1:]:
        path.lineTo(*pt)
    return path


def torn_paper(img, prev, item, p, ctx):
    """Papel rasgado. mode='split' (default): el rasgado corre a lo largo del cuadro de A por
    un costado del rostro (x,y o `avoid`; el director pone el rostro de A) y las dos piezas se
    separan con leve giro revelando B; mode='sweep': A se va completo en `dir` con el borde
    trasero rasgado. Banda de fibra de papel con textura, borde exterior carcomido y suave,
    sombra gris hacia la foto, fibras sueltas, sombra proyectada sobre B y sombreado de
    profundidad en la orilla de la foto."""
    k = _intensity(item)
    p = E.clamp(p)
    w, h = img.width(), img.height()
    A = _fit(prev, w, h) or img
    B = img
    if p <= 0.0:
        return A
    if p >= 1.0:
        return B
    mode = _str_opt(item, "mode", ("split", "sweep"), "split")
    axis, sign = _axis_sign(_dirp(item))
    vertical = axis == 1  # linea de rasgado vertical cuando el movimiento es horizontal
    L, Aw = (h, w) if vertical else (w, h)  # largo de la linea y ancho transversal (px reales)
    protect = _protect_point(item) if mode == "split" else None
    st = _torn_state(ctx, _key(item), mode, vertical, protect)
    # en draft basta la mitad de los puntos (armar los caminos en Python domina el costo)
    dec = 2 if ctx.draft else 1
    s_arr, u_ref, fine = st["s"][::dec], st["u"][::dec], st["fine"][::dec]
    band_a, band_b = st["band_a"][::dec], st["band_b"][::dec]
    Ar = st["Ar"]
    ku = Aw / Ar  # px de referencia transversales -> px reales
    pw = ctx.px(1)
    paper_col = _safe_color(_param(item, "color", "#F2EEE6"), "#F2EEE6")

    def to_xy(sv, uv):  # uv en px de referencia
        return (uv * ku, sv * L) if vertical else (sv * L, uv * ku)

    # ---- fases: rasgado (solo split) y separacion
    if mode == "split":
        rip = E.out_cubic(E.remap(p, 0.0, 0.3))
        # se jalan acelerando (arranque suave, salida decidida) y salen justo al final
        # el movimiento del primer cuadro lo da el rasgado que ya avanza (out_cubic); las piezas
        # se separan cuando el rasgado casi termina (abrirlas antes abriria tambien lo no rasgado)
        sep = _in_pow(E.remap(p, 0.16, 1.0), 1.7)
        sf = rip * 1.16 - 0.06
        sprog = s_arr if st["rip_down"] else (1.0 - s_arr)
        grow = np.clip((sf - sprog) / 0.07, 0.0, 1.0)
        pieces = [(-1, u_ref, band_a * grow), (1, u_ref, band_b * grow)]
    else:
        # con velocidad inicial: A se mueve desde el primer cuadro
        sep = 0.85 * _in_out_pow(p, 2.0) + 0.15 * p
        side = 1 if sign < 0 else -1  # lado donde queda el borde rasgado
        edge_off = float(np.abs(u_ref).max()) + 14.0
        base = Ar + edge_off if side > 0 else -edge_off
        pieces = [(-side, base + u_ref, band_a)]

    theta = st["theta"] * (0.6 + 0.4 * k)
    sh_sigma = (8.0 + 26.0 * sep) * pw
    sh_off = (8.0 + 20.0 * sep) * pw
    lift = 1.0 + 0.025 * sep
    rot_extra = math.sin(math.radians(theta)) * L * 0.5

    def piece_matrix(pside: int, edge: np.ndarray) -> skia.Matrix:
        """Transformacion de una pieza: se aleja en su lado, gira un poco y se levanta.
        El recorrido alcanza justo para sacar la pieza (y su sombra) del cuadro."""
        if pside < 0:
            clear = float(edge.max()) * ku
            cu = float(np.clip(edge, 0, Ar).mean()) * 0.5
        else:
            clear = (Ar - float(edge.min())) * ku
            cu = (Ar + float(np.clip(edge, 0, Ar).mean())) * 0.5
        clear += rot_extra + 3 * sh_sigma + sh_off + 0.03 * Aw + 40 * pw * ku
        tr = sep * clear * pside
        rot = theta * sep * pside * (1 if st["rip_down"] else -1)
        m = skia.Matrix()
        cx_, cy_ = to_xy(0.5, cu)
        if vertical:
            m.preTranslate(tr, st["lift_y"] * sep * h)
        else:
            m.preTranslate(st["lift_y"] * sep * w, tr)
        m.preTranslate(cx_, cy_)
        m.preRotate(rot)
        m.preScale(lift, lift)
        m.preTranslate(-cx_, -cy_)
        return m

    def piece_paths(pside: int, edge: np.ndarray, band: np.ndarray):
        # la pieza ocupa el lado pside (-1: u menores, +1: u mayores) del borde
        far = -0.6 * Ar if pside < 0 else 1.6 * Ar
        s0, s1 = s_arr[0], s_arr[-1]
        overlap = 1.2 / max(1e-6, ku)
        outer_u = edge - pside * (overlap + np.abs(fine) * (band > 0.5))
        inner_u = edge + pside * band  # la foto termina antes que el papel
        shade_u = inner_u + pside * (6.0 * pw / max(1e-6, ku))  # sombra metida en la foto
        outer_pts = [to_xy(sv, uv) for sv, uv in zip(s_arr, outer_u)]
        inner_pts = [to_xy(sv, uv) for sv, uv in zip(s_arr, inner_u)]
        outer = _poly([to_xy(s0, far)] + outer_pts + [to_xy(s1, far)])
        inner = _poly([to_xy(s0, far)] + inner_pts + [to_xy(s1, far)])
        band_poly = _poly(outer_pts + inner_pts[::-1])
        # sombras solo donde el rasgado ya llego (es un tramo continuo) y con 1 de cada 3 puntos:
        # son suaves y cada trazo de 720 puntos cuesta ~1 ms
        torn = np.nonzero(band > 0.5)[0]
        if torn.size >= 2:
            sl = slice(int(torn[0]), int(torn[-1]) + 1, 3)
            i_line = _polyline(inner_pts[sl])
            s_line = _polyline([to_xy(sv, uv) for sv, uv in zip(s_arr[sl], shade_u[sl])])
        else:
            i_line = s_line = None
        lines = (_polyline(outer_pts), i_line, s_line)
        return outer, inner, band_poly, lines, outer_u

    s = _surface(w, h)
    c = s.getCanvas()
    separated = sep > 1e-4
    geo = [(pside, piece_matrix(pside, edge), *piece_paths(pside, edge, band), band)
           for pside, edge, band in pieces]

    if separated:
        c.drawImage(B, 0, 0)
        # sombra a baja resolucion (suave, barata) proyectada sobre B
        div = 4
        sw_, sh_ = _lowres(w, h, div)
        ss = skia.Surface.MakeRaster(skia.ImageInfo.MakeA8(sw_, sh_))
        sc = ss.getCanvas()
        sc.clear(_CLEAR)
        sp = skia.Paint(Color=_BLACK, AntiAlias=True,
                        MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(0.5, sh_sigma)))
        for pside, m, outer, inner, band_poly, lines, outer_u, band in geo:
            sc.save()
            sc.scale(sw_ / w, sh_ / h)
            sc.translate(sh_off * 0.6, sh_off)
            sc.concat(m)
            sc.drawPath(outer, sp)
            sc.restore()
        sh_arr = ss.makeImageSnapshot().toarray(colorType=skia.kAlpha_8_ColorType)
        sh_img = C.image_from_gray(_up(np.ascontiguousarray(sh_arr), w, h))
        a_sh = (0.55 + 0.1 * sep) * (0.4 + 0.6 * k)
        c.drawImage(sh_img, 0, 0, paint=skia.Paint(Color=C.color("#1A1410", a_sh)))
    else:
        c.drawImage(A, 0, 0)

    paper_sh = _paper_texture(ctx, paper_col).makeShader(
        skia.TileMode.kRepeat, skia.TileMode.kRepeat, C.SAMPLING_FAST,
        skia.Matrix.Scale(pw * 1.5, pw * 1.5))
    paper_paint = skia.Paint(Shader=paper_sh, AntiAlias=True)
    # orilla exterior de la fibra: un trazo translucido encima la suaviza (el papel roto no
    # tiene orilla vectorial); sin MaskFilter, que en un camino de 1920 px cuesta ~3 ms
    fringe = skia.Paint(Shader=paper_sh, AntiAlias=True, Style=skia.Paint.kStroke_Style,
                        StrokeWidth=max(1.0, 1.6 * pw), Alphaf=0.5)
    fiber_col = C.mix(paper_col, "#FFFFFF", 0.35)
    fr, fg, fb = (fiber_col >> 16) & 0xFF, (fiber_col >> 8) & 0xFF, fiber_col & 0xFF

    def stroke(col, a, wd):
        return skia.Paint(Color=C.color(col, a), AntiAlias=True, Style=skia.Paint.kStroke_Style,
                          StrokeWidth=max(0.8, wd * pw), StrokeJoin=skia.Paint.kRound_Join)
    # sombra gris tenue de la fibra junto a la foto, y profundidad de la orilla de la foto
    # (desenfoque falso con trazos concentricos: mucho mas barato que un MaskFilter)
    band_shade = [stroke("#5E554B", 0.12, 7.0), stroke("#5E554B", 0.16, 3.2)]
    photo_shade = [stroke("#000000", 0.09, 20.0), stroke("#000000", 0.12, 8.0)]
    fib = st["fib"]
    for pside, m, outer, inner, band_poly, lines, outer_u, band in geo:
        o_line, i_line, s_line = lines
        c.save()
        c.concat(m)
        if separated:
            photo = skia.Paint(Shader=A.makeShader(skia.TileMode.kClamp, skia.TileMode.kClamp, C.SAMPLING_FAST),
                               AntiAlias=True)
            c.drawPath(inner, photo)
            if s_line is not None:
                for sp_ in photo_shade:  # la orilla de la foto se levanta
                    c.drawPath(s_line, sp_)
        if float(band.max()) > 0.3:
            # banda de fibra blanca del rasgado
            c.drawPath(band_poly, paper_paint)
            c.drawPath(o_line, fringe)
            if i_line is not None:
                for sp_ in band_shade:
                    c.drawPath(i_line, sp_)
            # fibras sueltas (alfa variable) donde el rasgado ya llego
            groups = {0: skia.Path(), 1: skia.Path(), 2: skia.Path()}
            for i, ln, an, sd, al in zip(fib["i"] // dec, fib["len"], fib["ang"], fib["side"], fib["alpha"]):
                if band[i] < 2.0 or (pside < 0) != bool(sd):
                    continue
                x0, y0 = to_xy(s_arr[i], outer_u[i])
                ln = ln * pw
                dx_n, dy_n = (-pside, 0.0) if vertical else (0.0, -pside)
                ca, sa = math.cos(an), math.sin(an)
                dx, dy = dx_n * ca - dy_n * sa, dx_n * sa + dy_n * ca
                path = groups[min(2, int(al * 3))]
                path.moveTo(x0, y0)
                path.quadTo(x0 + dx * ln * 0.5 + dy * ln * 0.2, y0 + dy * ln * 0.5 - dx * ln * 0.2,
                            x0 + dx * ln, y0 + dy * ln)
            for gi, path in groups.items():
                if not path.isEmpty():
                    fp = skia.Paint(Color=skia.ColorSetARGB(int(255 * (0.35 + 0.28 * gi)), fr, fg, fb),
                                    AntiAlias=True, Style=skia.Paint.kStroke_Style,
                                    StrokeWidth=max(0.7, (0.8 + 0.25 * gi) * pw), StrokeCap=skia.Paint.kRound_Cap)
                    c.drawPath(path, fp)
        c.restore()
    return s.makeImageSnapshot()


# =========================================================================== registro
RENDERERS = {
    "cut": cut, "flash": flash, "light_leak": light_leak, "whip": whip, "zoom_blur": zoom_blur,
    "glitch": glitch, "film_burn": film_burn, "torn_paper": torn_paper, "circle": circle,
    "slide": slide,
}


def apply(img: skia.Image, prev: skia.Image | None, item: dict, p: float, ctx) -> skia.Image:
    """Despachador generico: kind desconocido, dur <= 0 o intensity 0 se comportan como corte
    (devuelven img). p se acota a [0,1] y NaN cuenta como 0."""
    if not isinstance(item, dict):
        return img
    fn = RENDERERS.get(_kind(item), cut)
    if fn is cut or _raw_dur(item) <= 0.0 or _intensity(item) <= 0.0:
        return img
    pp = _num(p, 0.0)
    return fn(img, prev, item, E.clamp(pp), ctx)
