"""Grabacion de pantalla sincronizada con una toma (pista fx.media, estilo "screen").

El montaje reconoce una grabacion de pantalla que SUENA igual que una toma de camara (scripts/
screensync.py, montage.detect_screens) y compose la deja en plan.fx.media encima de esa toma:

  {id, start, end (tiempo ORIGINAL del video compuesto), sid, path, kind: "video", style: "screen",
   src: "screen", enabled, locked, name, cam, cam_name, media_dur, w, h (de la fuente completa),
   crop: [x0, y0, x1, y1] | None (fracciones: lo que hay dentro del marco de un dispositivo emulado),
   sync: {offset, rate, pieces: [[t0, t1, cam_in]]}}

SINCRONIA: el segundo t del video compuesto muestra la pantalla en
    offset + rate * (cam_in + t - t0)        (el pedazo [t0, t1] que contiene a t)
en segundos de cada ARCHIVO (el desfase ya trae el start_time de cada audio: screensync.file_offset).
Cada pedazo es un tramo de la toma en el video compuesto (los cortes que el montaje hizo dentro de la
toma traen su propio cam_in) y el render recorta silencios en este MISMO tiempo original: la pantalla
sigue cada corte sin deriva (el cuadro final T muestra la pantalla de sync_time(inverse(T))). Va muda:
la voz es la de la camara. Fuera de los pedazos no hay pantalla (el elemento se acota a ellos).

PRESENTACION (la edicion aprobada: ventana flotante sobre la toma calmada; 9:16 y simetria):
  - Fondo: la toma de camara desenfocada y oscurecida (media.draw_background, el mismo look que la
    tarjeta flotante: blur 0.55, dim 0.28). La camara solo aporta su audio.
  - Ventana: la grabacion COMPLETA dentro de su marco (`crop`; su proporcion, de ASPECT_MIN a
    ASPECT_MAX) lo mas grande que cabe ARRIBA de los subtitulos (los subtitulos blancos nunca caen
    sobre la interfaz blanca de una app): desde TOP del alto hasta media.card_band, centrada en
    x = 0.5 con margenes identicos al pixel (media.card_px), esquinas redondeadas, sombra suave y
    borde claro fino. A 4K una grabacion de celular (600-900 px de ancho) se AMPLIA al tamano de la
    ventana del export con lanczos (texto nitido; es el limite de la fuente).
  - Movimiento (`motion`; vacio = segun el estilo del director):
      "cut"    (minimal): aparece y se va con el corte de su toma; fondo calmado desde el primer cuadro
      "calm"   (elegante): entra con un acercamiento de 3.5 % y una subida corta (IN_DUR) y se queda quieta
      "lively" (dinamico, energetico): la entrada con resorte de la tarjeta flotante y una flotacion
               leve, SIN inclinacion 3D
    En todos la ventana nunca es translucida y se va con un corte seco (coincide con el cambio de toma).

Este modulo es el dueno de la geometria, la animacion y la sincronia (funciones PURAS, sin skia, con
port identico en app/static/screencard.js: tests/test_screen_card.py compara ambos); el dibujo es el
de la tarjeta flotante (media.draw_card con look(), media.draw_background).
"""
from __future__ import annotations

import math
from typing import Any

from . import ctx as C
from . import ease
from . import media as MF

STYLE = "screen"
MOTIONS = ("cut", "calm", "lively")
MOTION_BY_STYLE = {"minimal": "cut", "elegante": "calm"}     # el resto: "lively"
ASPECT_MIN, ASPECT_MAX = 0.40, 2.40     # proporcion de la ventana (= la de la grabacion recortada, acotada)
ASPECT_DEFAULT = 9 / 19.5               # sin dims: un celular
WIDTH_MIN = 0.30
TOP = 0.0625                            # la ventana puede subir hasta aqui (la edicion aprobada)
RADIUS = MF.RADIUS                      # esquinas (fraccion del ancho de la ventana)
IN_DUR = 0.35                           # entrada de "calm" y "lively"
CALM_FROM = 0.965                       # escala inicial "calm"
CALM_RISE = 0.012                       # sube desde +1.2 % del alto
LIVELY_FLOAT = 0.5                      # flotacion de "lively" (fraccion de la de float_card)
DEFAULTS = {"blur": MF.DEFAULTS["blur"], "dim": MF.DEFAULTS["dim"], "radius": RADIUS,
            "shadow": MF.DEFAULTS["shadow"], "border": True, "motion": None, "width": None}


def _num(v: Any, default: float) -> float:
    if isinstance(v, bool) or v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _r5(v: float) -> float:
    return math.floor(v * 1e5 + 0.5) / 1e5


def param(it: dict, k: str) -> Any:
    v = it.get(k) if isinstance(it, dict) else None
    return DEFAULTS.get(k) if v is None else v


def motion_of(it: dict, style: str | None = None) -> str:
    """"cut" | "calm" | "lively": el del item o, vacio, el del estilo del director (minimal: cut,
    elegante: calm, el resto: lively)."""
    m = it.get("motion") if isinstance(it, dict) else None
    if m in MOTIONS:
        return m
    return MOTION_BY_STYLE.get(style or "minimal", "lively")


def look(it: dict) -> dict:
    """Lo que media.draw_card lee del item para dibujar la ventana (sin brillo ni Ken Burns)."""
    return {"radius": param(it, "radius"), "shadow": param(it, "shadow"), "border": param(it, "border"),
            "sheen": 0.0, "kenburns": 0.0, "kind": "video"}


# ================================================================ sincronia (PURA)
def sync_pieces(it: dict) -> list[list[float]]:
    s = it.get("sync") if isinstance(it, dict) else None
    out = []
    for p in (s or {}).get("pieces") or []:
        try:
            a, b, c = float(p[0]), float(p[1]), float(p[2])
        except (TypeError, ValueError, IndexError):
            continue
        if b > a and math.isfinite(a) and math.isfinite(b) and math.isfinite(c):
            out.append([a, b, c])
    out.sort(key=lambda q: q[0])
    return out


def sync_time(it: dict, t: float) -> float | None:
    """s del video compuesto -> s de la grabacion de pantalla (el pedazo que contiene t; fuera de
    todos, el mas cercano acotado a sus bordes). None sin sincronia."""
    ps = sync_pieces(it)
    if not ps:
        return None
    s = it.get("sync") or {}
    off, rate = _num(s.get("offset"), 0.0), _num(s.get("rate"), 1.0) or 1.0
    t = _num(t, 0.0)
    hit = None
    for p in ps:
        if p[0] - 1e-6 <= t < p[1] - 1e-6:      # en una union gana el pedazo siguiente
            hit = p
            break
    if hit is None:
        hit = min(ps, key=lambda p: 0.0 if p[0] <= t <= p[1] else min(abs(t - p[0]), abs(t - p[1])))
    tt = _clamp(t, hit[0], hit[1])
    return off + rate * (hit[2] + tt - hit[0])


def coverage(it: dict) -> tuple[float, float] | None:
    """Tramo [a, b] (tiempo original) donde el elemento puede verse: [start, end] acotado a sus
    pedazos sincronizados (fuera de ellos no hay pantalla). None si no queda nada."""
    ps = sync_pieces(it)
    a, b = _num(it.get("start"), 0.0), _num(it.get("end"), 0.0)
    if ps:
        a, b = max(a, ps[0][0]), min(b, ps[-1][1])
    return (a, b) if b - a > 1e-3 else None


# ================================================================ geometria (PURA)
def crop_of(it: dict) -> list[float] | None:
    """`crop` del item saneado ([x0, y0, x1, y1] en fracciones, dentro de [0, 1], de al menos 10 %
    de lado) o None (la grabacion completa)."""
    c = it.get("crop") if isinstance(it, dict) else None
    if not isinstance(c, (list, tuple)) or len(c) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in c)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    x0, y0, x1, y1 = _clamp(x0, 0.0, 1.0), _clamp(y0, 0.0, 1.0), _clamp(x1, 0.0, 1.0), _clamp(y1, 0.0, 1.0)
    if x1 - x0 < 0.1 or y1 - y0 < 0.1 or (x0 <= 0 and y0 <= 0 and x1 >= 1 and y1 >= 1):
        return None
    return [x0, y0, x1, y1]


def src_aspect(w: Any, h: Any, crop: list | None = None) -> float:
    """Proporcion de lo que se ve de la grabacion (dentro de su marco), acotada."""
    w, h = _num(w, 0.0), _num(h, 0.0)
    if w > 0 and h > 0 and crop:
        w, h = w * (crop[2] - crop[0]), h * (crop[3] - crop[1])
    a = w / h if w > 0 and h > 0 else ASPECT_DEFAULT
    return _clamp(a, ASPECT_MIN, ASPECT_MAX)


def band(box_aspect: float = 9 / 16, cap_top: float | None = None,
         cap_bottom: float | None = None) -> tuple[float, float]:
    """(arriba, abajo) de la ventana: la banda de la tarjeta flotante (media.card_band: arriba de los
    subtitulos) pero, si es la de ARRIBA, empezando en TOP (la ventana es contenido: lo mas grande
    posible)."""
    top, bottom = MF.card_band(box_aspect, cap_top, cap_bottom)
    if abs(top - (C.SAFE["top"] + MF.MARGIN)) < 1e-9:
        top = min(top, TOP)
    return top, bottom


def layout(src_w: Any, src_h: Any, box_aspect: float = 9 / 16, cap_top: float | None = None,
           cap_bottom: float | None = None, width: Any = None, crop: list | None = None) -> dict:
    """Ventana en fracciones de la caja -> {cx, cy, w, h, aspect, radius}.

      (top, bottom) = band(box_aspect, cap_top, cap_bottom)   (arriba de los subtitulos)
      asp = src_aspect(src_w, src_h, crop); room = bottom - top
      w = min(width o SYM_W, room * asp / box_aspect); h = w * box_aspect / asp
      cy = top + h / 2 + (room - h) * media.UP_BIAS ; cx = 0.5 SIEMPRE"""
    top, bottom = band(box_aspect, cap_top, cap_bottom)
    ba = _clamp(_num(box_aspect, 9 / 16), 0.2, 5.0)
    asp = src_aspect(src_w, src_h, crop)
    room = max(0.08, bottom - top)
    wmax = _clamp(_num(width, C.SYM_W), WIDTH_MIN, C.SYM_W)
    w = min(wmax, room * asp / ba)
    h = w * ba / asp
    cy = top + h / 2 + max(0.0, room - h) * MF.UP_BIAS
    return {"cx": C.XC, "cy": _r5(cy), "w": _r5(w), "h": _r5(h), "aspect": _r5(asp), "radius": RADIUS}


# ================================================================ animacion (PURA)
def anim(lt: float, dur: float, motion: str = "cut", in_dur: Any = None) -> dict:
    """Estado en el tiempo local -> {alpha (siempre 1: nunca translucida), scale, dy, tilt (0), sway,
    fdy, lift, bg}.
      "cut":    todo en su lugar desde el primer cuadro (scale 1, dy 0, bg 1)
      "calm":   e = out_cubic(lt / tin); scale = CALM_FROM + (1 - CALM_FROM) e; dy = CALM_RISE (1 - e);
                bg = smoothstep(lt / (0.6 tin)) (el fondo se calma en ~0.2 s)
      "lively": media.card_anim (resorte, flotacion LIVELY_FLOAT) sin inclinacion; bg como "calm"
    Sin fundido de salida: la ventana se va con un corte seco al terminar (su toma termina ahi)."""
    dur = max(1e-3, _num(dur, 1.0))
    lt = _clamp(_num(lt, 0.0), 0.0, dur)
    tin = max(1e-3, min(_num(in_dur, IN_DUR), 0.4 * dur))
    if motion == "lively":
        # (la duracion larga quita la salida de card_anim: la ventana se queda hasta el corte)
        st = MF.card_anim(lt, dur + 1e3, tin, MF.OUT_DUR, LIVELY_FLOAT)
        st["tilt"], st["sway"] = 0.0, 0.0
    elif motion == "calm":
        e = ease.out_cubic(ease.clamp(lt / tin))
        st = {"scale": CALM_FROM + (1.0 - CALM_FROM) * e, "dy": CALM_RISE * (1.0 - e),
              "tilt": 0.0, "sway": 0.0, "fdy": 0.0, "lift": 0.0}
    else:
        st = {"scale": 1.0, "dy": 0.0, "tilt": 0.0, "sway": 0.0, "fdy": 0.0, "lift": 0.0}
    st["alpha"] = 1.0
    st["bg"] = 1.0 if motion not in ("calm", "lively") else ease.smoothstep(ease.clamp(lt / (0.6 * tin)))
    return st
