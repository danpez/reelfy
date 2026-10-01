"""Recursos flotantes del guion (pista fx.media, estilo `float_card`).

El guion puede marcar un recurso al final de una linea ("... - Agregar video_marcas"); el montaje
lo coloca mientras se dice esa linea y compose lo deja en plan.fx.media:

  {id, start, end (tiempo ORIGINAL del video compuesto), sid, path, kind: image|video, name,
   fit: hold|speed|trim, speed, src_in, style: "float_card", enabled, locked, src: "script"}

Se dibuja como una TARJETA FLOTANTE profesional (no un corte): la imagen o el clip recortado
(cover) en una tarjeta 4:5 con esquinas redondeadas, sombra suave y borde claro fino, sobre el
video de la persona desenfocado y oscurecido; entra con un resorte (escala 0.9 -> 1, sube, se
aclara y se endereza de una ligera inclinacion 3D) y sale limpia. Clips mudos, cuadro exacto,
con su `fit`:
  hold  imagen fija / clip a 1x que se congela en su ultimo cuadro si es mas corto
  speed clip acelerado `speed` veces (cabe en el hueco de la linea)
  trim  clip a 1x desde `src_in`, cortado al final del hueco

VIDA EN REPOSO (la tarjeta nunca se ve quieta; todo es funcion PURA del tiempo local: el mismo
cuadro siempre sale igual, en el render y en floatcard.js). Parametros compartidos con el port:
  flotacion   dy = -FLOAT_AMP * sin(2 pi lt / FLOAT_T) (sube primero; ~15 px a 1920 de alto)
  vaiven 3D   tilt = SWAY * sin(2 pi lt / SWAY_T) grados sobre el eje HORIZONTAL con perspectiva
              (camara a PERSP * alto): arriba y abajo se alejan por turnos, el centro horizontal
              no se mueve y la tarjeta sigue simetrica en cada cuadro (tilt_quad)
  respiracion escala * (1 + BREATH * sin(2 pi lt / (1.5 FLOAT_T)))
              (las tres entran suave con la entrada; `float` las escala: 0 = quieta; la amplitud
              se topa en float = 1 -nunca entra a los subtitulos- y mas `float` solo la acelera)
  Ken Burns   imagenes: zoom 1 + kenburns (KB_LEAD + (1 - KB_LEAD) in_out_quad(lt / dur)) y
              PARALAJE: la imagen se queda atras PARALLAX de la flotacion dentro de la tarjeta (sin
              pasar de PARALLAX_MAX de la holgura del zoom: no recorta lo que va pegado al borde)
  brillo      imagenes (clips: `sheen` 0 salvo que el item lo pida): vidrio = brillo fijo arriba
              (GLOSS_A, mas fuerte cuando la parte de arriba se acerca) + una FRANJA DE LUZ
              horizontal que cruza de arriba abajo SIN PRISA (SHEEN_DUR s, casi a velocidad
              constante: a 30 fps avanza < 1/2 de su nucleo por cuadro; la primera al terminar la
              entrada + SHEEN_DELAY y luego cada SHEEN_EVERY s) con un reflejo suave detras, el
              borde que se ilumina al pasar (RIM_A) y, con `holo`, un irisado sutil en los flancos de
              la franja (HOLO_A). Lleva DESENFOQUE DE MOVIMIENTO (light_at: la luz promediada en un
              obturador de SHUTTER s): se ve como un barrido continuo, no a saltos. Todo es un
              degradado VERTICAL encima de la imagen, recortado a la tarjeta: horizontal y centrado,
              simetrico como la tarjeta. `sheen` y `holo` son controles del item en el Studio.
  sombra      se aleja y aclara un poco cuando la tarjeta sube (se siente levantada).

SIMETRIA (regla de la marca): la tarjeta va SIEMPRE centrada en x = 0.5 con ancho <= SYM_W
(0.76, la banda segura); en pixeles el ancho tiene la paridad de la caja, asi los margenes
izquierdo y derecho son identicos al pixel. Nada se mueve de lado (la flotacion es vertical, la
inclinacion es sobre el eje horizontal y la luz es una franja horizontal).

Subtitulos: la tarjeta va en la banda libre ARRIBA de la franja de subtitulos (caption_top) o, si
el usuario subio los subtitulos y arriba no cabe una tarjeta digna (< ROOM_OK del alto), en la banda
de ABAJO (caption_bottom) cuando es mas grande; si ninguna banda cabe ni al ancho minimo, detras de
los subtitulos. Los subtitulos se dibujan despues, encima de todo: siguen legibles.

Este modulo es el dueno de la geometria y la animacion (funciones PURAS, sin skia) y del
dibujo con skia. render.py resuelve el archivo, lo normaliza (clip al tamano de la tarjeta con
la velocidad horneada) y llama a draw_background/draw_card en el compositor.

CONTRATO PARA LA VISTA EN VIVO DEL STUDIO (app/static/floatcard.js, window.FloatCard): el port JS
es identico paso a paso en card_layout, caption_top, card_anim, kenburns_zoom, parallax_px,
sheen_state, light_stops, light_at, card_quad, src_time, cover_rect y card_px (tests/test_media_float_preview.py
compara ambos). El dibujo del port aproxima la perspectiva con franjas horizontales (canvas 2D
solo tiene transformaciones afines). Todas las fracciones son de la CAJA DE LAYOUT 9:16 (x del
ancho, y del alto); `box_aspect` = ancho/alto de la caja.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import skia

from . import ctx as C
from . import ease

STYLES = ("float_card",)
FITS = ("hold", "speed", "trim")

# ---------------------------------------------------------------- geometria (fracciones de la caja)
CARD_W = C.SYM_W                 # ancho de la tarjeta = banda segura simetrica (0.76)
CARD_W_MIN = 0.4
ASPECT = 4 / 5                   # proporcion por defecto (ancho/alto)
ASPECT_TALL = 9 / 16             # lo mas vertical (fuente vertical y hay lugar)
ASPECT_WIDE = 1.0                # lo mas ancho cuando no cabe (subtitulos muy arriba)
ASPECT_MAX = 16 / 9              # tope de un `aspect` manual
MARGIN = 0.015                   # aire minimo arriba (bajo SAFE.top) y sobre los subtitulos
ROOM_OK = 0.35                   # banda arriba de los subtitulos que ya basta (si no, la mas grande)
UP_BIAS = 0.35                   # lugar en el hueco libre: 0 = arriba, 0.5 = centrado
RADIUS = 0.055                   # radio de las esquinas (fraccion del ANCHO de la tarjeta)
CAPTION_HALF = 0.05              # media altura de la franja de subtitulos (espejo de render)
CAP_GAP = 0.01
# (escala, lineas maximas) de cada preset de subtitulos (espejo de text.CAPTION_PRESETS)
CAPTION_PRESET_LINES = {"clean_bold": (1.0, 1), "karaoke": (1.0, 1), "boxed": (1.0, 1),
                        "minimal_small": (0.74, 2), "bold_caps": (1.28, 2)}

# ---------------------------------------------------------------- animacion
IN_DUR = 0.35                    # entrada (resorte)
OUT_DUR = 0.25                   # salida
SPRING_F, SPRING_Z = 2.6, 0.55   # resorte de la entrada (Hz, amortiguacion): un rebote corto
POP_FROM = 0.9                   # escala inicial
RISE = 0.035                     # sube desde +3.5 % del alto
TILT = 14.0                      # grados de inclinacion inicial (eje horizontal)
EXIT_SCALE = 0.06                # la salida encoge 6 %
EXIT_RISE = 0.02                 # y sube 2 % del alto
# vida en reposo (ver el docstring; espejo exacto en floatcard.js)
FLOAT_AMP = 0.008                # flotacion vertical (fraccion del alto de la caja: ~15 px a 1920)
FLOAT_T = 3.4                    # periodo de la flotacion (s)
BREATH = 0.006                   # "respiracion" de la escala
SWAY = 4.5                       # vaiven 3D (grados, eje horizontal)
SWAY_T = 5.6                     # periodo del vaiven (s; no multiplo de FLOAT_T: no se repite igual)
PERSP = 2.4                      # distancia de la camara de la perspectiva = PERSP * alto
WARP_STEP = 0.2                  # salto maximo del borde entre franjas vecinas (px): sin escalones
WARP_MIN, WARP_MAX = 8, 320      # franjas de la perspectiva (warp_strips)
KENBURNS = 0.08                  # acercamiento de las imagenes en todo el item
KB_LEAD = 0.3                    # el acercamiento arranca en KB_LEAD * kenburns (holgura del paralaje)
PARALLAX = 0.35                  # la imagen se queda atras esta fraccion de la flotacion
PARALLAX_MAX = 0.35              # ... sin pasar de esta fraccion de la holgura del acercamiento
# brillo de vidrio (imagenes): franja de luz horizontal + brillo fijo arriba + borde + irisado
SHEEN_A = 0.36                   # intensidad pico de la franja (luz blanca encima, source-over)
SHEEN_DELAY = 0.25               # la primera pasada empieza al terminar la entrada + esto (s)
SHEEN_EVERY = 4.6                # una pasada cada tantos s
SHEEN_DUR = 1.8                  # lo que tarda en cruzar (s): lento y continuo, no un destello
SHEEN_MIN = 1.5                  # pasada mas corta (items cortos); si no cabe, no hay pasada
SHEEN_OUT = 0.3                  # la franja empieza y termina fuera de la tarjeta (fraccion del alto)
SHEEN_EASE = 0.35                # fraccion coseno del recorrido (el resto lineal): velocidad casi pareja
SHUTTER = 1 / 30                 # obturador del desenfoque de movimiento de la luz (s, el mismo a 30 o 60 fps)
SHUTTER_N = 4                    # muestras dentro del obturador
GLOSS_A = 0.07                   # brillo fijo de vidrio en la parte de arriba
HOLO_A = 0.16                    # irisado de los flancos de la franja con holo = 1
RIM_A = 1.0                      # el borde claro se ilumina al pasar la luz (factor del degradado)
# franja (u relativo al centro, fraccion de SHEEN_A): nucleo (ancho a media altura ~0.086 del
# alto) con halo suave + un reflejo suave detras (arriba: la franja baja; ~0.06 a media altura)
_BAND = ((-0.3, 0.0), (-0.24, 0.3), (-0.18, 0.0), (-0.14, 0.08), (-0.05, 0.42), (0.0, 1.0),
         (0.05, 0.42), (0.14, 0.08), (0.21, 0.0))
_GLOSS = ((0.0, 1.0), (0.16, 0.55), (0.42, 0.0))
# irisado: (u relativo, (r, g, b), fraccion de HOLO_A) en los flancos (dispersion de la luz)
_HOLO = ((-0.36, (150, 120, 255), 0.0), (-0.29, (150, 120, 255), 1.0), (-0.22, (90, 210, 255), 1.0),
         (-0.14, (90, 210, 255), 0.0), (0.12, (255, 214, 130), 0.0), (0.2, (255, 214, 130), 1.0),
         (0.28, (255, 120, 200), 1.0), (0.36, (255, 120, 200), 0.0))

# ---------------------------------------------------------------- estilo (defaults del esquema)
DEFAULTS = {"blur": 0.55, "dim": 0.28, "radius": RADIUS, "shadow": 0.6, "border": True,
            "kenburns": KENBURNS, "float": 1.0, "in_dur": IN_DUR, "out_dur": OUT_DUR}
# parametros del brillo (por item; controles 'Brillo' e 'Irisado' del esquema): `sheen` 0..1 (vidrio +
# franja de luz; vacio = automatico: 1 en imagenes y 0 en clips), `holo` 0..1 (irisado de la franja)
LIVE_DEFAULTS = {"sheen": 1.0, "holo": 0.6}
BLUR_PX = 22.0                   # sigma del desenfoque del fondo con blur = 1 (px a 1080 de ancho)
CARD_BG = "#1C1C22"              # fondo opaco de la tarjeta (bajo la imagen; = floatcard.js)
BG_DOWN = 4                      # el fondo se desenfoca a <= 1/8 de resolucion (barato)


def _num(v: Any, default: float) -> float:
    if isinstance(v, bool):
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f and abs(f) != float("inf") else default


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _r5(v: float) -> float:
    """5 decimales redondeando como Math.round del port JS (round() de python va al par)."""
    return math.floor(v * 1e5 + 0.5) / 1e5


def param(it: dict, k: str) -> Any:
    """Parametro de estilo del item (o su default: DEFAULTS o LIVE_DEFAULTS)."""
    v = it.get(k) if isinstance(it, dict) else None
    return (DEFAULTS[k] if k in DEFAULTS else LIVE_DEFAULTS[k]) if v is None else v


def is_video(it: dict, baked: bool = False) -> bool:
    """El recurso es un clip (o un cuadro ya horneado a la tarjeta, como el marcador)."""
    return bool(baked) or (isinstance(it, dict) and it.get("kind") == "video")


def sheen_amount(it: dict, baked: bool = False) -> float:
    """`sheen` efectivo 0..1: imagenes 1 por defecto; clips 0 salvo que el item lo pida (la luz
    sobre un video en movimiento distrae)."""
    if is_video(it, baked) and (not isinstance(it, dict) or it.get("sheen") is None):
        return 0.0
    return _clamp(_num(param(it, "sheen"), LIVE_DEFAULTS["sheen"]), 0.0, 1.0)


# ================================================================ geometria (PURA)
def _caption_ys(cap: dict | None, on: bool, t0: float | None, t1: float | None,
                box_aspect: float) -> tuple[list[float], float] | None:
    """([y de los subtitulos y de sus tramos y_ranges que tocan el item], media altura) o None."""
    if not on or not isinstance(cap, dict) or cap.get("enabled") is False \
            or (cap.get("preset") or "clean_bold") == "none":
        return None
    ys = [_num(cap.get("y"), 0.72)]
    for r in cap.get("y_ranges") or []:
        if not isinstance(r, dict) or r.get("hide"):
            continue
        a, b = _num(r.get("start"), float("nan")), _num(r.get("end"), float("nan"))
        if a != a or b != b:
            continue
        if t0 is not None and t1 is not None and not (a < t1 and t0 < b):
            continue
        ys.append(_num(r.get("y"), ys[0]))
    scale, lines = CAPTION_PRESET_LINES.get(cap.get("preset") or "clean_bold", (1.0, 1))
    if cap.get("max_lines") is not None:
        lines = max(1, int(_num(cap.get("max_lines"), lines)))
    size = _num(cap.get("size"), 0.052)
    return ys, max(CAPTION_HALF, 0.625 * lines * size * scale * box_aspect)


def caption_top(cap: dict | None, on: bool = True, t0: float | None = None,
                t1: float | None = None, box_aspect: float = 9 / 16) -> float | None:
    """Borde superior (fraccion del alto de la caja) de la franja de subtitulos mientras dura el
    item [t0, t1] (tiempo ORIGINAL), o None si no hay subtitulos. Se toma la `y` mas ALTA entre
    la de los subtitulos y la de los tramos `y_ranges` (sin `hide`) que tocan el item; media
    altura = max(CAPTION_HALF, 0.625 * lineas * size * escala * box_aspect) (bloque de hasta
    `max_lines` lineas del preset) + CAP_GAP."""
    r = _caption_ys(cap, on, t0, t1, box_aspect)
    return None if r is None else _r5(min(r[0]) - r[1] - CAP_GAP)


def caption_bottom(cap: dict | None, on: bool = True, t0: float | None = None,
                   t1: float | None = None, box_aspect: float = 9 / 16) -> float | None:
    """Borde INFERIOR de la franja de subtitulos durante [t0, t1] (la `y` mas BAJA + media altura +
    CAP_GAP), o None sin subtitulos. Con caption_top delimita la banda libre de abajo."""
    r = _caption_ys(cap, on, t0, t1, box_aspect)
    return None if r is None else _r5(max(r[0]) + r[1] + CAP_GAP)


def card_band(box_aspect: float = 9 / 16, cap_top: float | None = None,
              cap_bottom: float | None = None) -> tuple[float, float]:
    """(arriba, abajo) de la banda libre donde va la tarjeta (fracciones del alto de la caja):
      arriba de los subtitulos [SAFE.top + MARGIN, min(SAFE.bottom, cap_top) - MARGIN] si mide
      >= ROOM_OK; si no, la mas grande entre esa y la de abajo [max(SAFE.top, cap_bottom) + MARGIN,
      SAFE.bottom - MARGIN] (subtitulos subidos); si ni la mayor cabe una tarjeta de CARD_W_MIN al
      formato mas ancho (CARD_W_MIN * box_aspect / ASPECT_WIDE), toda la zona segura (detras de los
      subtitulos, que van encima). Sin subtitulos: toda la zona segura. Sin cap_bottom se supone
      la franja minima (cap_top + 2 (CAPTION_HALF + CAP_GAP))."""
    top0, bot0 = C.SAFE["top"] + MARGIN, C.SAFE["bottom"] - MARGIN
    if cap_top is None:
        return top0, bot0
    ct = _num(cap_top, C.SAFE["bottom"])
    cb = _num(cap_bottom, ct + 2 * (CAPTION_HALF + CAP_GAP)) if cap_bottom is not None \
        else ct + 2 * (CAPTION_HALF + CAP_GAP)
    above = (top0, min(C.SAFE["bottom"], ct) - MARGIN)
    below = (max(C.SAFE["top"], cb) + MARGIN, bot0)
    ra, rb = above[1] - above[0], below[1] - below[0]
    ba = _clamp(_num(box_aspect, 9 / 16), 0.2, 5.0)
    if max(ra, rb) < CARD_W_MIN * ba / ASPECT_WIDE - 1e-9:
        return top0, bot0
    if ra >= ROOM_OK - 1e-9 or ra >= rb:
        return above
    return below


def card_layout(src_w: float | None, src_h: float | None, box_aspect: float = 9 / 16,
                cap_top: float | None = None, width: float | None = None,
                aspect: float | None = None, y: float | None = None,
                cap_bottom: float | None = None) -> dict:
    """Tarjeta en fracciones de la caja -> {cx, cy, w, h, aspect, radius}.

      (top, bottom) = card_band(box_aspect, cap_top, cap_bottom); room = bottom - top
      w = clamp(width or CARD_W, CARD_W_MIN, SYM_W)
      asp = aspect manual (ASPECT_TALL..ASPECT_MAX) o, auto: ASPECT (4:5) si la fuente es igual
            o mas ancha; si es mas vertical, la de la fuente hasta ASPECT_TALL (9:16)
      h = w * box_aspect / asp
      auto y h > room: asp = min(ASPECT_WIDE, max(asp, w * box_aspect / room))  (menos vertical)
      aun h > room: w = max(CARD_W_MIN, room * asp / box_aspect); si sigue sin caber, h = room y
            w = room * asp / box_aspect (nunca tapa los subtitulos)
      cy = y manual acotada al hueco, o top + h/2 + (room - h) * UP_BIAS; cx = 0.5 SIEMPRE
      radius = RADIUS (fraccion del ancho de la tarjeta)"""
    top, bottom = card_band(box_aspect, cap_top, cap_bottom)
    room = max(0.08, bottom - top)
    ba = _clamp(_num(box_aspect, 9 / 16), 0.2, 5.0)
    w = _clamp(_num(width, CARD_W), CARD_W_MIN, C.SYM_W)
    sw, sh = _num(src_w, 0.0), _num(src_h, 0.0)
    sa = sw / sh if sw > 0 and sh > 0 else ASPECT
    manual = aspect is not None and _num(aspect, 0.0) > 0
    if manual:
        asp = _clamp(_num(aspect, ASPECT), ASPECT_TALL, ASPECT_MAX)
    else:
        asp = ASPECT if sa >= ASPECT else max(ASPECT_TALL, sa)
    h = w * ba / asp
    if h > room + 1e-9 and not manual:
        asp = min(ASPECT_WIDE, max(asp, w * ba / room))
        h = w * ba / asp
    if h > room + 1e-9:
        w = max(CARD_W_MIN, room * asp / ba)
        h = w * ba / asp
        if h > room + 1e-9:
            h = room
            w = room * asp / ba
    free = max(0.0, room - h)
    if y is not None and _num(y, -1.0) >= 0:
        cy = _clamp(_num(y, 0.5), top + h / 2, bottom - h / 2) if free > 0 else top + h / 2
    else:
        cy = top + h / 2 + free * UP_BIAS
    return {"cx": C.XC, "cy": _r5(cy), "w": _r5(w), "h": _r5(h), "aspect": _r5(asp), "radius": RADIUS}


def card_px(card: dict, bw: float, bh: float) -> tuple[int, int, int, int]:
    """Rect de la tarjeta en px de la caja (x0, y0, w, h), SIMETRICO al pixel: el ancho tiene la
    paridad del ancho de la caja (x0 = (bw - w) / 2 entero) y el alto sale de la proporcion."""
    rnd = lambda v: int(math.floor(v + 0.5))  # noqa: E731  (= Math.round del port JS)
    bw_i = rnd(bw)
    x0 = rnd((bw_i - _num(card.get("w"), CARD_W) * bw_i) / 2)
    x0 = max(0, min(x0, bw_i // 2 - 1))
    w = bw_i - 2 * x0
    asp = _num(card.get("aspect"), ASPECT) or ASPECT
    h = max(2, rnd(w / asp))
    y0 = rnd(_num(card.get("cy"), 0.4) * bh - h / 2)
    return x0, y0, w, h


def _tin_tout(dur: float, in_dur: float, out_dur: float) -> tuple[float, float]:
    return (max(1e-3, min(_num(in_dur, IN_DUR), 0.4 * dur)),
            max(1e-3, min(_num(out_dur, OUT_DUR), 0.3 * dur)))


def card_anim(lt: float, dur: float, in_dur: float = IN_DUR, out_dur: float = OUT_DUR,
              float_amt: float = 1.0) -> dict:
    """Estado de la tarjeta en su tiempo local -> {alpha, scale, dy, tilt, bg, sway, fdy, lift}.

      tin = min(in_dur, 0.4 dur); tout = min(out_dur, 0.3 dur)
      sp = spring(lt, SPRING_F, SPRING_Z) (0 -> 1 con un rebote)
      alpha = out_cubic(clamp(lt / (0.6 tin)));  scale = POP_FROM + (1 - POP_FROM) sp
      dy = RISE (1 - sp) (fraccion del alto; + = abajo); tilt = TILT (1 - sp) grados
      bg = smoothstep(clamp(lt / (1.2 tin)))
      vida (k = smoothstep(clamp(lt / (2 tin))), fa = max(0, float_amt), fd = min(fa, 1),
            ph = lt (1 + 0.5 (max(fa, 1) - 1)): la AMPLITUD se topa en float 1 (asi nunca entra a
            la franja de subtitulos ni sale de la zona segura); mas `float` solo la hace mas viva):
        fdy = -FLOAT_AMP fd k sin(2 pi ph / FLOAT_T) (sube primero); dy += fdy
        lift = clamp(-fdy / FLOAT_AMP, -1, 1) (+ = arriba: la sombra se aleja)
        sway = SWAY fd k sin(2 pi ph / SWAY_T); tilt += sway (+ = la parte de arriba se aleja)
        scale *= 1 + BREATH fd k sin(2 pi ph / (1.5 FLOAT_T))
      salida (q = clamp((dur - lt) / tout), pe = 1 - q): alpha *= smoothstep(q);
                 scale *= 1 - EXIT_SCALE in_cubic(pe); dy -= EXIT_RISE in_cubic(pe);
                 bg *= smoothstep(q)"""
    dur = max(1e-3, _num(dur, 1.0))
    lt = _clamp(_num(lt, 0.0), 0.0, dur)
    tin, tout = _tin_tout(dur, in_dur, out_dur)
    sp = ease.spring(lt, SPRING_F, SPRING_Z)
    alpha = ease.out_cubic(ease.clamp(lt / (0.6 * tin)))
    scale = POP_FROM + (1.0 - POP_FROM) * sp
    dy = RISE * (1.0 - sp)
    tilt = TILT * (1.0 - sp)
    bg = ease.smoothstep(ease.clamp(lt / (1.2 * tin)))
    fa = max(0.0, _num(float_amt, 1.0))
    fd = min(fa, 1.0)
    ph = lt * (1.0 + 0.5 * (max(fa, 1.0) - 1.0))
    k = ease.smoothstep(ease.clamp(lt / (2.0 * tin)))
    fdy = -FLOAT_AMP * fd * k * math.sin(2 * math.pi * ph / FLOAT_T)
    dy += fdy
    sway = SWAY * fd * k * math.sin(2 * math.pi * ph / SWAY_T)
    tilt += sway
    scale *= 1.0 + BREATH * fd * k * math.sin(2 * math.pi * ph / (1.5 * FLOAT_T))
    q = ease.clamp((dur - lt) / tout)
    if q < 1.0:
        pe = 1.0 - q
        alpha *= ease.smoothstep(q)
        scale *= 1.0 - EXIT_SCALE * ease.in_cubic(pe)
        dy -= EXIT_RISE * ease.in_cubic(pe)
        bg *= ease.smoothstep(q)
    return {"alpha": alpha, "scale": scale, "dy": dy, "tilt": tilt, "bg": bg, "sway": sway,
            "fdy": fdy, "lift": _clamp(-fdy / FLOAT_AMP, -1.0, 1.0)}


def kenburns_zoom(lt: float, dur: float, amount: float = KENBURNS) -> float:
    """Acercamiento lento de una imagen en todo el item: 1 + amount (KB_LEAD + (1 - KB_LEAD)
    in_out_quad(lt / dur)) (arranca un poco acercada: holgura para el paralaje); amount 0 = 1."""
    dur = max(1e-3, _num(dur, 1.0))
    e = ease.in_out_quad(ease.clamp(_num(lt, 0.0) / dur))
    return 1.0 + max(0.0, _num(amount, KENBURNS)) * (KB_LEAD + (1.0 - KB_LEAD) * e)


def parallax_px(fdy: float, bh: float, h: float, zoom: float) -> float:
    """Paralaje dentro de la tarjeta (px de la tarjeta; + = la ventana de la imagen baja, el
    contenido sube): la imagen se queda atras PARALLAX de la flotacion `fdy` (fraccion del alto
    de la caja `bh`), acotada a PARALLAX_MAX de la holgura del acercamiento ((1 - 1/zoom) h / 2)."""
    slack = (1.0 - 1.0 / max(1.0, _num(zoom, 1.0))) * max(0.0, _num(h, 0.0)) / 2.0
    lim = PARALLAX_MAX * slack
    return _clamp(PARALLAX * _num(fdy, 0.0) * _num(bh, 0.0), -lim, lim)


def sheen_state(lt: float, dur: float, in_dur: float = IN_DUR,
                out_dur: float = OUT_DUR) -> tuple[float, float] | None:
    """Franja de luz en el tiempo local -> (centro u, envolvente) o None si no hay pasada.

      a0 = tin + SHEEN_DELAY; pasadas en a = a0 + i SHEEN_EVERY de d = min(SHEEN_DUR, fin - a)
      segundos (fin = dur - tout: nunca durante la salida; si d < SHEEN_MIN, no hay pasada)
      p = (lt - a) / d; e = (1 - SHEEN_EASE) p + SHEEN_EASE (1 - cos(pi p)) / 2 (casi lineal: la
      velocidad pico es (1 + 0.57 SHEEN_EASE) la media, no pi/2 de un coseno puro)
      u = -SHEEN_OUT + (1 + 2 SHEEN_OUT) e (0 = arriba de la tarjeta, 1 = abajo: cruza de arriba
      abajo); env = smoothstep(clamp(5 min(p, 1 - p))) (entra y sale fundida: sin tiron)"""
    dur = max(1e-3, _num(dur, 1.0))
    lt = _num(lt, 0.0)
    tin, tout = _tin_tout(dur, in_dur, out_dur)
    end = dur - tout
    a0 = tin + SHEEN_DELAY
    if lt < a0 or lt >= end:
        return None
    a = a0 + math.floor((lt - a0) / SHEEN_EVERY) * SHEEN_EVERY
    d = min(SHEEN_DUR, end - a)
    if d < SHEEN_MIN - 1e-9:
        return None
    p = (lt - a) / d
    if p >= 1.0:
        return None
    e = (1.0 - SHEEN_EASE) * p + SHEEN_EASE * (1.0 - math.cos(math.pi * p)) / 2.0
    u = -SHEEN_OUT + (1.0 + 2.0 * SHEEN_OUT) * e
    return u, ease.smoothstep(ease.clamp(5.0 * min(p, 1.0 - p)))


def _key_at(keys: list, u: float) -> tuple[float, float, float, float]:
    """(r, g, b, a) de una capa de luz lineal por tramos [(u, r, g, b, a)] en u (0 fuera)."""
    if u < keys[0][0] or u > keys[-1][0]:
        k = keys[0] if u < keys[0][0] else keys[-1]
        return k[1], k[2], k[3], 0.0
    for i in range(1, len(keys)):
        b = keys[i]
        if u <= b[0]:
            a = keys[i - 1]
            span = b[0] - a[0]
            f = (u - a[0]) / span if span > 1e-12 else 1.0
            return tuple(a[j] + (b[j] - a[j]) * f for j in range(1, 5))
    k = keys[-1]
    return k[1], k[2], k[3], k[4]


def light_stops(sheen: tuple[float, float] | None, sway: float = 0.0, amount: float = 1.0,
                holo: float = LIVE_DEFAULTS["holo"]) -> list[tuple[float, float, float, float, float]]:
    """Degradado VERTICAL de la luz de la tarjeta -> [(u, r, g, b, a)] (u 0 = arriba, 1 = abajo;
    rgb 0..255 sin premultiplicar, a 0..1) que va ENCIMA de la imagen (source-over: con luz blanca es
    lo mismo que screen); [] sin luz.

    Capas (blancas salvo el irisado), cada una lineal por tramos:
      vidrio   _GLOSS (fijo arriba) * GLOSS_A * amount * (1 - 0.35 sway / SWAY)
      franja   _BAND alrededor de sheen[0] * SHEEN_A * amount * sheen[1]
      irisado  _HOLO alrededor de sheen[0] * HOLO_A * amount * holo * sheen[1]
    Se evaluan en los puntos de quiebre de todas las capas dentro de (0, 1) mas 0 y 1, y se
    combinan como luces en screen: P = 1 - prod(1 - rgb_i a_i / 255), A = 1 - prod(1 - a_i),
    rgb = 255 P / A."""
    amt = _clamp(_num(amount, 1.0), 0.0, 1.0)
    if amt <= 1e-4:
        return []
    layers = []
    gm = GLOSS_A * amt * (1.0 - 0.35 * _clamp(_num(sway, 0.0) / SWAY, -1.0, 1.0))
    layers.append([(u, 255.0, 255.0, 255.0, f * gm) for u, f in _GLOSS])
    if sheen is not None:
        c, env = _num(sheen[0], 0.0), _clamp(_num(sheen[1], 0.0), 0.0, 1.0)
        a = SHEEN_A * amt * env
        layers.append([(c + u, 255.0, 255.0, 255.0, f * a) for u, f in _BAND])
        ho = HOLO_A * amt * env * _clamp(_num(holo, 0.0), 0.0, 1.0)
        if ho > 1e-4:
            layers.append([(c + u, float(rgb[0]), float(rgb[1]), float(rgb[2]), f * ho)
                           for u, rgb, f in _HOLO])
    us = [0.0, 1.0] + [k[0] for lay in layers for k in lay if 0.0 < k[0] < 1.0]
    us.sort()
    out = []
    peak = 0.0
    for u in us:
        if out and u - out[-1][0] < 1e-6:
            continue
        pr = pg = pb = pa = 1.0
        for lay in layers:
            r, g, b, a = _key_at(lay, u)
            pr *= 1.0 - r / 255.0 * a
            pg *= 1.0 - g / 255.0 * a
            pb *= 1.0 - b / 255.0 * a
            pa *= 1.0 - a
        A = 1.0 - pa
        if A > 1e-9:
            rgb = tuple(_clamp(255.0 * (1.0 - p) / A, 0.0, 255.0) for p in (pr, pg, pb))
        else:
            rgb = (255.0, 255.0, 255.0)
        peak = max(peak, A)
        out.append((u, rgb[0], rgb[1], rgb[2], A))
    return out if peak >= 0.5 / 255.0 else []


def light_at(lt: float, dur: float, sway: float = 0.0, amount: float = 1.0,
             holo: float = LIVE_DEFAULTS["holo"], in_dur: float = IN_DUR,
             out_dur: float = OUT_DUR) -> list[tuple[float, float, float, float, float]]:
    """La luz de la tarjeta en el cuadro de tiempo local `lt` CON DESENFOQUE DE MOVIMIENTO ->
    [(u, r, g, b, a)] como light_stops (lo que dibujan draw_card y floatcard.drawCard).

    La franja se promedia en un obturador de SHUTTER s centrado en lt: SHUTTER_N muestras en
    lt + SHUTTER ((i + 1/2) / SHUTTER_N - 1/2), cada una light_stops(sheen_state(muestra), sway,
    amount, holo); se evaluan todas en la union de sus paradas (ordenada; paradas a < 1e-6 de la
    anterior se omiten) y se promedian PREMULTIPLICADAS (a = media de a_i; rgb = media de rgb_i a_i
    / media de a_i; una lista vacia cuenta como luz 0). Sin franja en ninguna muestra es exactamente
    light_stops(None, sway, amount, holo). [] si el pico queda < 0.5/255."""
    amt = _clamp(_num(amount, 1.0), 0.0, 1.0)
    if amt <= 1e-4:
        return []
    shs = [sheen_state(_num(lt, 0.0) + SHUTTER * ((i + 0.5) / SHUTTER_N - 0.5), dur, in_dur, out_dur)
           for i in range(SHUTTER_N)]
    if all(x is None for x in shs):
        return light_stops(None, sway, amt, holo)
    lists = [light_stops(x, sway, amt, holo) for x in shs]
    us = sorted([0.0, 1.0] + [k[0] for lay in lists for k in lay])
    uu = []
    for u in us:
        if not uu or u - uu[-1] >= 1e-6:
            uu.append(u)
    U = np.asarray(uu, dtype=np.float64)
    pa = np.zeros(len(uu))
    prgb = np.zeros((3, len(uu)))
    for lay in lists:
        if not lay:
            continue
        st = np.asarray(lay, dtype=np.float64)
        a = np.interp(U, st[:, 0], st[:, 4])
        pa += a
        for j in range(3):
            prgb[j] += np.interp(U, st[:, 0], st[:, 1 + j]) * a
    pa /= SHUTTER_N
    prgb /= SHUTTER_N
    out = []
    for i, u in enumerate(uu):
        A = float(pa[i])
        if A > 1e-12:
            rgb = tuple(_clamp(float(prgb[j, i]) / A, 0.0, 255.0) for j in range(3))
        else:
            rgb = (255.0, 255.0, 255.0)
        out.append((u, rgb[0], rgb[1], rgb[2], A))
    return out if float(pa.max()) >= 0.5 / 255.0 else []


def speed_of(it: dict) -> float:
    """Velocidad efectiva del clip: `speed` solo con fit 'speed' (0.25..4); si no, 1."""
    if not isinstance(it, dict) or it.get("fit") != "speed":
        return 1.0
    return _clamp(_num(it.get("speed"), 1.0), 0.25, 4.0)


def src_time(it: dict, lt: float, src_dur: float | None = None, fps: float = 30.0) -> float:
    """Tiempo del archivo (s) que se ve en el tiempo local `lt`: src_in + lt * speed, congelado en
    el ultimo cuadro (src_dur - 1/fps) si el clip se acaba."""
    t = max(0.0, _num(it.get("src_in"), 0.0)) + max(0.0, _num(lt, 0.0)) * speed_of(it)
    d = _num(src_dur, 0.0)
    if d > 0:
        t = min(t, max(0.0, d - 1.0 / max(1.0, _num(fps, 30.0))))
    return t


def cover_rect(iw: float, ih: float, dw: float, dh: float, zoom: float = 1.0) -> tuple:
    """Rect FUENTE (x, y, w, h en px de la imagen) que LLENA un destino dw x dh (cover),
    centrado, acercado `zoom` veces."""
    iw, ih = max(1e-6, float(iw)), max(1e-6, float(ih))
    ar = max(1e-6, float(dw)) / max(1e-6, float(dh))
    sw, sh = (ih * ar, ih) if iw / ih > ar else (iw, iw / ar)
    z = max(1.0, _num(zoom, 1.0))
    sw, sh = sw / z, sh / z
    return (iw - sw) / 2, (ih - sh) / 2, sw, sh


def tilt_quad(w: float, h: float, tilt_deg: float, scale: float = 1.0,
              persp: float = PERSP) -> list[tuple[float, float]]:
    """Esquinas (tl, tr, br, bl) de una tarjeta w x h centrada en el origen, inclinada `tilt_deg`
    sobre el eje HORIZONTAL (el borde de arriba se aleja) con perspectiva (distancia = persp *
    alto) y escalada. Simetrica en x (la regla de la marca se mantiene en cada cuadro)."""
    th = math.radians(_num(tilt_deg, 0.0))
    d = max(1e-3, persp * h)
    out = []
    for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        z = -y * math.sin(th)                 # arriba (y < 0) se aleja si tilt > 0
        k = d / (d + z)
        out.append((x * k * scale, y * math.cos(th) * k * scale))
    return out


def card_quad(card: dict, bw: float, bh: float, st: dict) -> list[tuple[float, float]]:
    """Esquinas (tl, tr, br, bl) de la tarjeta ANIMADA en px de la caja (estado `st` de card_anim):
    lo que se ve en ese cuadro (sin la sombra). Simetrica respecto al centro de la caja."""
    x0, y0, w, h = card_px(card, bw, bh)
    cx = x0 + w / 2.0
    cy = y0 + h / 2.0 + _num(st.get("dy"), 0.0) * bh
    return [(cx + x, cy + y) for x, y in tilt_quad(w, h, _num(st.get("tilt"), 0.0),
                                                   _num(st.get("scale"), 1.0))]


def warp_strips(w: float, h: float, tilt_deg: float, scale: float = 1.0,
                persp: float = PERSP) -> list[tuple[float, float, float, float, float]]:
    """Perspectiva de tilt_quad por FRANJAS horizontales afines -> [(v0, v1, y0, y1, kx)]: las
    filas locales [v0, v1] (v de -h/2 a h/2) van a [y0, y1] (px respecto al centro de la
    tarjeta, lineal entre los dos) con escala horizontal kx (x' = kx x, la del centro de la
    franja). Los bordes de cada franja caen EXACTO en la perspectiva; el borde lateral salta a lo
    mas WARP_STEP px entre franjas vecinas (n = max|dk/dv| h scale w / 2 / WARP_STEP, entre WARP_MIN
    y WARP_MAX: depende del tamano en px, a 4K hay mas franjas). Sin inclinacion: una franja.
    Es la forma barata y sin costuras de dibujar la perspectiva (render y vista en vivo igual):
    cada franja es una transformacion afin (camino rapido de Skia y unico posible en canvas 2D)."""
    s = _num(scale, 1.0)
    t = _num(tilt_deg, 0.0)
    if abs(t) <= 0.02:
        return [(-h / 2, h / 2, -h / 2 * s, h / 2 * s, s)]
    th = math.radians(t)
    sn, cs = math.sin(th), math.cos(th)
    d = max(1e-3, persp * h)
    dk = d * abs(sn) / (d - h / 2 * abs(sn)) ** 2 * h          # max |dk/dv| * h (en el borde que se acerca)
    n = int(_clamp(math.ceil(dk * abs(s) * _num(w, 0.0) / 2 / WARP_STEP), WARP_MIN, WARP_MAX))

    def proj(v):
        k = d / (d - v * sn)                  # = tilt_quad (z = -v sin)
        return v * cs * k * s, k * s

    out = []
    ya = proj(-h / 2)[0]
    for i in range(n):
        v0, v1 = -h / 2 + h * i / n, -h / 2 + h * (i + 1) / n
        yb = proj(v1)[0]
        out.append((v0, v1, ya, yb, proj((v0 + v1) / 2)[1]))
        ya = yb
    return out


# ================================================================ dibujo (skia)
class _Surfaces:
    """Superficies de trabajo del fondo (una por tamano, reusadas cuadro a cuadro)."""

    def __init__(self):
        self.s: dict[tuple, skia.Surface] = {}

    def get(self, w: int, h: int) -> skia.Surface:
        k = (int(w), int(h))
        if k not in self.s:
            if len(self.s) > 6:
                self.s.pop(next(iter(self.s)))
            self.s[k] = C.offscreen(*k)
        return self.s[k]


_SURF = _Surfaces()


def draw_background(canvas: skia.Canvas, snap: skia.Image, W: int, H: int, amt: float,
                    blur: float = DEFAULTS["blur"], dim: float = DEFAULTS["dim"]) -> None:
    """Fondo detras de la tarjeta: el cuadro compuesto hasta aqui, desenfocado y oscurecido.

    Barato (~6 ms a 1080x1920): se reduce en proporcion al desenfoque (hasta 1/8; primero a 1/2
    = promedio 2x2), se desenfoca ahi (sigma a resolucion completa = BLUR_PX * blur * W/1080, que
    crece con `amt`), se oscurece en la version chica y se amplia sobre el cuadro con alfa
    min(1, 1.6 amt) (el oscurecido se compensa para que el total sea dim * amt)."""
    amt = _clamp(_num(amt, 0.0), 0.0, 1.0)
    if amt <= 0.003 or snap is None:
        return
    b = _clamp(_num(blur, DEFAULTS["blur"]), 0.0, 1.0)
    dm = _clamp(_num(dim, DEFAULTS["dim"]), 0.0, 0.9)
    a = min(1.0, amt * 1.6) if b > 0.001 else 1.0
    if b <= 0.001 and dm <= 0.001:
        return
    if b <= 0.001:
        canvas.drawRect(skia.Rect.MakeWH(W, H), skia.Paint(Color=skia.ColorSetARGB(
            int(round(255 * dm * amt)), 0, 0, 0)))
        return
    sig_full = BLUR_PX * b * (W / 1080.0)
    # reduccion proporcional al desenfoque (el mismo aspecto a 1080, 540 o 216 de ancho)
    down = max(1.0, min(float(BG_DOWN * 2), sig_full / 1.5))
    src = snap
    if down >= 2.0:
        hw, hh = max(8, W // 2), max(8, H // 2)
        half = _SURF.get(hw, hh)
        half.getCanvas().drawImageRect(snap, skia.Rect.MakeWH(hw, hh), C.SAMPLING_FAST)
        src = half.makeImageSnapshot()
    sw, sh = max(8, int(round(W / down))), max(8, int(round(H / down)))
    small = _SURF.get(sw, sh)
    sc = small.getCanvas()
    sig = max(0.4, sig_full * (0.35 + 0.65 * amt) * sw / W)
    sc.drawImageRect(src, skia.Rect.MakeWH(sw, sh), C.SAMPLING_FAST,
                     skia.Paint(ImageFilter=skia.ImageFilters.Blur(sig, sig, skia.TileMode.kClamp),
                                BlendMode=skia.BlendMode.kSrc))
    if dm > 0.001:
        sc.drawRect(skia.Rect.MakeWH(sw, sh), skia.Paint(Color=skia.ColorSetARGB(
            int(round(255 * min(0.95, dm * amt / a))), 0, 0, 0)))
    canvas.drawImageRect(small.makeImageSnapshot(), skia.Rect.MakeWH(W, H), C.SAMPLING_FAST,
                         skia.Paint(Alphaf=a))


def _sampling(src_w: float, dst_w: float):
    """Por cuadro: bilineal hasta reducir 0.6x (el medio ya viene a la escala de la tarjeta:
    clip horneado o imagen pre-escalada); mas reduccion, mipmaps."""
    r = dst_w / max(1e-6, src_w)
    return C.SAMPLING_FAST if r >= 0.6 else C.SAMPLING


_PRE: dict = {}


def _prescaled(img: skia.Image, w: int, h: int, zmax: float) -> tuple[skia.Image, bool]:
    """(imagen, opaca): la imagen recortada (cover, centrada) a (w, h) * zmax en alta calidad, UNA
    vez por imagen y tamano: el Ken Burns por cuadro solo toma el centro 1/z de esta (bilineal a
    0.93-1x). `opaca` = sin transparencia (no hace falta el fondo de la tarjeta debajo)."""
    pw, ph = max(2, int(math.ceil(w * zmax))), max(2, int(math.ceil(h * zmax)))
    key = (img.uniqueID(), pw, ph)
    out = _PRE.get(key)
    if out is None:
        sx, sy, sw, sh = cover_rect(img.width(), img.height(), pw, ph)
        s = C.offscreen(pw, ph)
        smp = C.SAMPLING if sw > pw * 2 else C.SAMPLING_CUBIC
        s.getCanvas().drawImageRect(img, skia.Rect.MakeXYWH(sx, sy, sw, sh), skia.Rect.MakeWH(pw, ph), smp)
        pic = s.makeImageSnapshot()
        try:
            opaque = bool(img.isOpaque()) or int(pic.toarray()[..., 3].min()) >= 255
        except Exception:  # noqa
            opaque = False
        out = (pic, opaque)
        if len(_PRE) > 6:
            _PRE.pop(next(iter(_PRE)))
        _PRE[key] = out
    return out


def light_column(stops: list, n: int) -> np.ndarray:
    """La luz de light_stops en `n` filas (centros (i + 0.5) / n): RGBA uint8 PREMULTIPLICADO
    n x 1 x 4, interpolando cada canal sin premultiplicar entre paradas (como un degradado de
    canvas 2D / Skia)."""
    n = max(1, int(n))
    u = (np.arange(n, dtype=np.float64) + 0.5) / n
    st = np.asarray(stops, dtype=np.float64).reshape(-1, 5)
    a = np.clip(np.interp(u, st[:, 0], st[:, 4]), 0.0, 1.0)
    out = np.empty((n, 1, 4), np.uint8)
    for j in range(3):
        out[:, 0, j] = np.clip(np.interp(u, st[:, 0], st[:, 1 + j]) * a + 0.5, 0, 255).astype(np.uint8)
    out[:, 0, 3] = np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return out


_BIG = 1e6


def draw_card(canvas: skia.Canvas, img: skia.Image | None, bw: float, bh: float, card: dict,
              st: dict, it: dict, lt: float, dur: float, baked: bool = False) -> tuple | None:
    """Tarjeta en la caja de layout (canvas ya trasladado a la caja). `img` = cuadro del medio
    (imagen completa normalizada, o el cuadro del clip ya recortado a la tarjeta si `baked`);
    `st` = card_anim. Devuelve el rect en reposo (x0, y0, w, h) en px de la caja, o None.

    La perspectiva va por FRANJAS afines (warp_strips): cada franja recorta sus filas exactas
    (recorte sin AA en filas compartidas: sin costuras ni doble mezcla) y dibuja el rrect de la
    tarjeta con su transformacion: fondo (solo si la imagen tiene transparencia), imagen (Ken
    Burns y paralaje en la matriz local de UN shader), luz (columna de light_column estirada a
    lo ancho) y el anillo del borde. Es el camino afin rapido de Skia: la tarjeta viva cuesta lo
    mismo que la quieta de antes (la perspectiva real de Skia costaba ~2.5x). La sombra va en
    afin con el rectangulo medio del cuadrilatero (borrosa: la perspectiva no se nota)."""
    if st.get("alpha", 0.0) <= 0.003:
        return None
    x0, y0, w, h = card_px(card, bw, bh)
    cx = x0 + w / 2.0
    cy = y0 + h / 2.0 + _num(st.get("dy"), 0.0) * bh
    r = _clamp(_num(param(it, "radius"), RADIUS), 0.0, 0.5) * w
    s_ = _num(st.get("scale"), 1.0)
    tilt = _num(st.get("tilt"), 0.0)
    quad = tilt_quad(w, h, tilt, s_)
    strips = warp_strips(w, h, tilt, s_)
    rect = skia.Rect.MakeXYWH(-w / 2, -h / 2, w, h)
    rr = skia.RRect.MakeRectXY(rect, r, r)
    shadow = _clamp(_num(param(it, "shadow"), DEFAULTS["shadow"]), 0.0, 1.0)
    sh_sig, sh_dy = 0.045 * w, 0.028 * w
    lift = _clamp(_num(st.get("lift"), 0.0), -1.0, 1.0)
    # medio: un shader (Ken Burns + paralaje en su matriz local) para todas las franjas
    paints = []
    opaque = img is not None and baked
    if img is not None:
        if baked:
            pic, sr = img, skia.Rect.MakeWH(img.width(), img.height())
            smp = _sampling(img.width(), w)
        else:
            # clip sin hornear (/frame sin cache): sin Ken Burns ni paralaje, como el export
            kb = 0.0 if is_video(it) else max(0.0, _num(param(it, "kenburns"), KENBURNS))
            pic, opaque = _prescaled(img, w, h, 1.0 + kb)
            z = kenburns_zoom(lt, dur, kb)
            # `pic` ES el recorte cover (zoom 1): el Ken Burns muestra su centro 1/z, corrido por
            # el paralaje (dentro de la holgura: nunca sale de la imagen)
            sx, sy, sw, sh = cover_rect(pic.width(), pic.height(), w, h, z)
            sy += parallax_px(st.get("fdy"), bh, h, z) * sh / h
            sr = skia.Rect.MakeXYWH(sx, sy, sw, sh)
            smp = _sampling(sw, w)
        lm = skia.Matrix()
        lm.setRectToRect(sr, rect, skia.Matrix.kFill_ScaleToFit)
        paints.append(skia.Paint(Shader=pic.makeShader(skia.TileMode.kClamp, skia.TileMode.kClamp,
                                                       smp, lm), AntiAlias=True))
    if not opaque:
        # fondo opaco de la tarjeta (= floatcard.drawCard): una imagen con transparencia (un logo
        # PNG) se ve sobre la tarjeta oscura, no sobre la sombra
        paints.insert(0, skia.Paint(Color=C.color(CARD_BG), AntiAlias=True))
    # luz (vidrio + franja + irisado): columna premultiplicada estirada a lo ancho, encima
    amt = sheen_amount(it, baked)
    stops = light_at(lt, dur, _num(st.get("sway"), 0.0), amt, param(it, "holo"), param(it, "in_dur"),
                     param(it, "out_dur")) if amt > 1e-3 else []
    light = lit = None
    if stops:
        nrow = max(2, int(round(h)))
        col = light_column(stops, nrow)
        rows = np.nonzero(col[:, 0, 3])[0]
        if len(rows):
            lit = (-h / 2 + h * rows[0] / nrow, -h / 2 + h * (rows[-1] + 1) / nrow)
            lm2 = skia.Matrix()
            lm2.setRectToRect(skia.Rect.MakeWH(1, nrow), rect, skia.Matrix.kFill_ScaleToFit)
            light = C.image_from_rgba(col).makeShader(skia.TileMode.kClamp, skia.TileMode.kClamp,
                                                      C.SAMPLING_FAST, lm2)
    qx = max(abs(x) for x, _ in quad)
    qt, qb = min(y for _, y in quad), max(y for _, y in quad)
    canvas.save()
    canvas.translate(cx, cy)
    pad = (sh_sig * 3 + sh_dy * 1.3) * max(1.0, s_)
    alpha = _clamp(_num(st.get("alpha"), 1.0), 0.0, 1.0)
    if alpha < 0.999:                      # capa solo mientras entra o sale (cuesta)
        canvas.saveLayer(skia.Rect.MakeLTRB(-qx - pad, qt - pad, qx + pad, qb + pad),
                         skia.Paint(Alphaf=alpha))
    else:
        canvas.save()
    try:
        if shadow > 0.001:
            # sombra doble (una amplia y suave + una corta de contacto) en el rectangulo medio del
            # cuadrilatero; al subir la tarjeta la amplia se aleja y se aclara un poco
            canvas.save()
            canvas.translate(0, (qt + qb) / 2.0)
            canvas.scale((quad[1][0] + quad[2][0]) / w, (qb - qt) / h)
            for sig, dy_, a in ((sh_sig, sh_dy * (1.0 + 0.3 * lift), 0.55 * (1.0 - 0.12 * lift)),
                                (0.012 * w, 0.006 * w, 0.35)):
                canvas.drawRRect(rr.makeOffset(0, dy_), skia.Paint(
                    Color=skia.ColorSetARGB(int(round(255 * a * shadow)), 0, 0, 0), AntiAlias=True,
                    MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(0.5, sig))))
            canvas.restore()
        # borde claro fino (+ el brillo que lo cruza): anillo relleno (drawDRRect) por franja, sin trazo
        ring = None
        if param(it, "border"):
            bwid = max(1.0, 0.0028 * w)
            ring = skia.RRect.MakeRectXY(rect.makeInset(bwid, bwid), max(0.0, r - bwid), max(0.0, r - bwid))
            ring_p = [skia.Paint(Color=skia.ColorSetARGB(128, 255, 255, 255), AntiAlias=True)]
            if light is not None:          # el borde se ilumina al pasar la luz
                ring_p.append(skia.Paint(Shader=light, AntiAlias=True, Alphaf=RIM_A))
        last = len(strips) - 1
        for i, (v0, v1, ya, yb, kx) in enumerate(strips):
            canvas.save()
            if last:                       # filas exactas de la franja (la 1a y la ultima, abiertas)
                canvas.clipRect(skia.Rect.MakeLTRB(-_BIG, -_BIG if i == 0 else ya, _BIG,
                                                   _BIG if i == last else yb))
            sy_ = (yb - ya) / (v1 - v0)
            canvas.concat(skia.Matrix.MakeAll(kx, 0, 0, 0, sy_, ya - v0 * sy_, 0, 0, 1))
            for p in paints:
                canvas.drawRRect(rr, p)
            if light is not None and v1 > lit[0] and v0 < lit[1]:
                canvas.drawRRect(rr, skia.Paint(Shader=light, AntiAlias=True))
            if ring is not None:
                if v0 < -h / 2 + r + 2 * bwid or v1 > h / 2 - r - 2 * bwid or not last:
                    for p in ring_p:       # franja con esquinas: el anillo completo
                        canvas.drawDRRect(rr, ring, p)
                else:                      # en medio: dos barras (mucho mas barato que el anillo)
                    for p in ring_p:
                        canvas.drawRect(skia.Rect.MakeLTRB(-w / 2, v0 - 2, -w / 2 + bwid, v1 + 2), p)
                        canvas.drawRect(skia.Rect.MakeLTRB(w / 2 - bwid, v0 - 2, w / 2, v1 + 2), p)
            canvas.restore()
    finally:
        canvas.restore()
        canvas.restore()
    return x0, y0, w, h
