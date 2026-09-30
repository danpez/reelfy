"""Recursos flotantes del guion (pista fx.media, estilo `float_card`).

El guion puede marcar un recurso al final de una linea ("... - Agregar video_marcas"); el montaje
lo coloca mientras se dice esa linea y compose lo deja en plan.fx.media:

  {id, start, end (tiempo ORIGINAL del video compuesto), sid, path, kind: image|video, name,
   fit: hold|speed|trim, speed, src_in, style: "float_card", enabled, locked, src: "script"}

Se dibuja como una TARJETA FLOTANTE profesional (no un corte): la imagen o el clip recortado
(cover) en una tarjeta 4:5 con esquinas redondeadas, sombra suave y borde claro fino, sobre el
video de la persona desenfocado y oscurecido; entra con un resorte (escala 0.9 -> 1, sube, se
aclara y se endereza de una ligera inclinacion 3D), flota suave mientras esta y sale limpia.
Imagenes con Ken Burns lento (centrado); clips mudos, cuadro exacto, con su `fit`:
  hold  imagen fija / clip a 1x que se congela en su ultimo cuadro si es mas corto
  speed clip acelerado `speed` veces (cabe en el hueco de la linea)
  trim  clip a 1x desde `src_in`, cortado al final del hueco

SIMETRIA (regla de la marca): la tarjeta va SIEMPRE centrada en x = 0.5 con ancho <= SYM_W
(0.76, la banda segura); en pixeles el ancho tiene la paridad de la caja, asi los margenes
izquierdo y derecho son identicos al pixel. Nada se mueve de lado (la flotacion es vertical y
la inclinacion es sobre el eje horizontal).

Subtitulos: la tarjeta va en la banda libre ARRIBA de la franja de subtitulos (caption_top) o, si
el usuario subio los subtitulos y arriba no cabe una tarjeta digna (< ROOM_OK del alto), en la banda
de ABAJO (caption_bottom) cuando es mas grande; si ninguna banda cabe ni al ancho minimo, detras de
los subtitulos. Los subtitulos se dibujan despues, encima de todo: siguen legibles.

Este modulo es el dueno de la geometria y la animacion (funciones PURAS, sin skia) y del
dibujo con skia. render.py resuelve el archivo, lo normaliza (clip al tamano de la tarjeta con
la velocidad horneada) y llama a draw_background/draw_card en el compositor.

CONTRATO PARA LA VISTA EN VIVO DEL STUDIO (app/static/floatcard.js, window.FloatCard): el port JS
es identico paso a paso en card_layout, caption_top, card_anim, kenburns_zoom, src_time,
cover_rect y card_px (tests/test_media_float_preview.py compara ambos). Todas las fracciones
son de la CAJA DE LAYOUT 9:16 (x del ancho, y del alto); `box_aspect` = ancho/alto de la caja.
"""
from __future__ import annotations

import math
from typing import Any

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
FLOAT_AMP = 0.004                # flotacion vertical (fraccion del alto)
FLOAT_T = 3.2                    # periodo de la flotacion (s)
BREATH = 0.004                   # "respiracion" de la escala
KENBURNS = 0.08                  # acercamiento de las imagenes en todo el item

# ---------------------------------------------------------------- estilo (defaults del esquema)
DEFAULTS = {"blur": 0.55, "dim": 0.28, "radius": RADIUS, "shadow": 0.6, "border": True,
            "kenburns": KENBURNS, "float": 1.0, "in_dur": IN_DUR, "out_dur": OUT_DUR}
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
    """Parametro de estilo del item (o su default)."""
    v = it.get(k) if isinstance(it, dict) else None
    return DEFAULTS[k] if v is None else v


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


def card_anim(lt: float, dur: float, in_dur: float = IN_DUR, out_dur: float = OUT_DUR,
              float_amt: float = 1.0) -> dict:
    """Estado de la tarjeta en su tiempo local -> {alpha, scale, dy, tilt, bg}.

      tin = min(in_dur, 0.4 dur); tout = min(out_dur, 0.3 dur)
      sp = spring(lt, SPRING_F, SPRING_Z) (0 -> 1 con un rebote)
      alpha = out_cubic(clamp(lt / (0.6 tin)));  scale = POP_FROM + (1 - POP_FROM) sp
      dy = RISE (1 - sp) (fraccion del alto; + = abajo); tilt = TILT (1 - sp) grados
      bg = smoothstep(clamp(lt / (1.2 tin)))
      flotacion: k = smoothstep(clamp(lt / (2 tin))); dy += FLOAT_AMP float_amt k sin(2 pi lt / FLOAT_T)
                 scale *= 1 + BREATH float_amt k sin(2 pi lt / (1.5 FLOAT_T))
      salida (q = clamp((dur - lt) / tout), pe = 1 - q): alpha *= smoothstep(q);
                 scale *= 1 - EXIT_SCALE in_cubic(pe); dy -= EXIT_RISE in_cubic(pe);
                 bg *= smoothstep(q)"""
    dur = max(1e-3, _num(dur, 1.0))
    lt = _clamp(_num(lt, 0.0), 0.0, dur)
    tin = max(1e-3, min(_num(in_dur, IN_DUR), 0.4 * dur))
    tout = max(1e-3, min(_num(out_dur, OUT_DUR), 0.3 * dur))
    sp = ease.spring(lt, SPRING_F, SPRING_Z)
    alpha = ease.out_cubic(ease.clamp(lt / (0.6 * tin)))
    scale = POP_FROM + (1.0 - POP_FROM) * sp
    dy = RISE * (1.0 - sp)
    tilt = TILT * (1.0 - sp)
    bg = ease.smoothstep(ease.clamp(lt / (1.2 * tin)))
    fa = _num(float_amt, 1.0)
    k = ease.smoothstep(ease.clamp(lt / (2.0 * tin)))
    dy += FLOAT_AMP * fa * k * math.sin(2 * math.pi * lt / FLOAT_T)
    scale *= 1.0 + BREATH * fa * k * math.sin(2 * math.pi * lt / (1.5 * FLOAT_T))
    q = ease.clamp((dur - lt) / tout)
    if q < 1.0:
        pe = 1.0 - q
        alpha *= ease.smoothstep(q)
        scale *= 1.0 - EXIT_SCALE * ease.in_cubic(pe)
        dy -= EXIT_RISE * ease.in_cubic(pe)
        bg *= ease.smoothstep(q)
    return {"alpha": alpha, "scale": scale, "dy": dy, "tilt": tilt, "bg": bg}


def kenburns_zoom(lt: float, dur: float, amount: float = KENBURNS) -> float:
    """Acercamiento lento de una imagen: 1 -> 1 + amount en todo el item (in_out_quad)."""
    dur = max(1e-3, _num(dur, 1.0))
    return 1.0 + max(0.0, _num(amount, KENBURNS)) * ease.in_out_quad(ease.clamp(_num(lt, 0.0) / dur))


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
              persp: float = 2.4) -> list[tuple[float, float]]:
    """Esquinas (tl, tr, br, bl) de una tarjeta w x h centrada en el origen, inclinada `tilt_deg`
    sobre el eje HORIZONTAL (el borde de arriba se aleja) con perspectiva (distancia = persp *
    alto) y escalada. Simetrica en x (la regla de la marca se mantiene durante la entrada)."""
    th = math.radians(_num(tilt_deg, 0.0))
    d = max(1e-3, persp * h)
    out = []
    for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        z = -y * math.sin(th)                 # arriba (y < 0) se aleja si tilt > 0
        k = d / (d + z)
        out.append((x * k * scale, y * math.cos(th) * k * scale))
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


def _prescaled(img: skia.Image, w: int, h: int, zmax: float) -> skia.Image:
    """La imagen recortada (cover, centrada) a (w, h) * zmax en alta calidad, UNA vez por imagen y
    tamano: el Ken Burns por cuadro solo toma el centro 1/z de esta (bilineal a 0.93-1x)."""
    pw, ph = max(2, int(math.ceil(w * zmax))), max(2, int(math.ceil(h * zmax)))
    key = (img.uniqueID(), pw, ph)
    out = _PRE.get(key)
    if out is None:
        sx, sy, sw, sh = cover_rect(img.width(), img.height(), pw, ph)
        s = C.offscreen(pw, ph)
        smp = C.SAMPLING if sw > pw * 2 else C.SAMPLING_CUBIC
        s.getCanvas().drawImageRect(img, skia.Rect.MakeXYWH(sx, sy, sw, sh), skia.Rect.MakeWH(pw, ph), smp)
        out = s.makeImageSnapshot()
        if len(_PRE) > 6:
            _PRE.pop(next(iter(_PRE)))
        _PRE[key] = out
    return out


def draw_card(canvas: skia.Canvas, img: skia.Image | None, bw: float, bh: float, card: dict,
              st: dict, it: dict, lt: float, dur: float, baked: bool = False) -> tuple | None:
    """Tarjeta en la caja de layout (canvas ya trasladado a la caja). `img` = cuadro del medio
    (imagen completa normalizada, o el cuadro del clip ya recortado a la tarjeta si `baked`);
    `st` = card_anim. Devuelve el rect en reposo (x0, y0, w, h) en px de la caja, o None."""
    if st.get("alpha", 0.0) <= 0.003:
        return None
    x0, y0, w, h = card_px(card, bw, bh)
    cx = x0 + w / 2.0
    cy = y0 + h / 2.0 + _num(st.get("dy"), 0.0) * bh
    r = _clamp(_num(param(it, "radius"), RADIUS), 0.0, 0.5) * w
    s_ = _num(st.get("scale"), 1.0)
    tilt = _num(st.get("tilt"), 0.0)
    M = skia.Matrix.Scale(s_, s_)
    if abs(tilt) > 0.02:                   # inclinacion 3D (perspectiva) solo en la entrada
        quad = tilt_quad(w, h, tilt, s_)
        src = [skia.Point(-w / 2, -h / 2), skia.Point(w / 2, -h / 2), skia.Point(w / 2, h / 2),
               skia.Point(-w / 2, h / 2)]
        P = skia.Matrix()
        if P.setPolyToPoly(src, [skia.Point(x, y) for x, y in quad]):
            M = P
    rect = skia.Rect.MakeXYWH(-w / 2, -h / 2, w, h)
    rr = skia.RRect.MakeRectXY(rect, r, r)
    shadow = _clamp(_num(param(it, "shadow"), DEFAULTS["shadow"]), 0.0, 1.0)
    sh_sig, sh_dy = 0.045 * w, 0.028 * w
    canvas.save()
    canvas.translate(cx, cy)
    canvas.concat(M)
    pad = sh_sig * 3 + sh_dy
    alpha = _clamp(_num(st.get("alpha"), 1.0), 0.0, 1.0)
    if alpha < 0.999:                      # capa solo mientras entra o sale (cuesta)
        canvas.saveLayer(skia.Rect.MakeLTRB(-w / 2 - pad, -h / 2 - pad, w / 2 + pad, h / 2 + pad),
                         skia.Paint(Alphaf=alpha))
    else:
        canvas.save()
    try:
        if shadow > 0.001:
            # sombra doble: una amplia y suave + una corta de contacto
            for sig, dy_, a in ((sh_sig, sh_dy, 0.55), (0.012 * w, 0.006 * w, 0.35)):
                canvas.drawRRect(rr.makeOffset(0, dy_), skia.Paint(
                    Color=skia.ColorSetARGB(int(round(255 * a * shadow)), 0, 0, 0), AntiAlias=True,
                    MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(0.5, sig))))
        canvas.save()
        canvas.clipRRect(rr, True)
        # fondo opaco de la tarjeta (= floatcard.drawCard): una imagen con transparencia (un logo
        # PNG) se ve sobre la tarjeta oscura, no sobre la sombra
        canvas.drawRect(rect, skia.Paint(Color=C.color(CARD_BG)))
        if img is None:
            pass
        elif baked:
            canvas.drawImageRect(img, rect, _sampling(img.width(), w))
        else:
            kb = max(0.0, _num(param(it, "kenburns"), KENBURNS))
            pre = _prescaled(img, w, h, 1.0 + kb)
            z = kenburns_zoom(lt, dur, kb)
            # `pre` ES el recorte cover (zoom 1): el Ken Burns muestra su centro 1/z
            sx, sy, sw, sh = cover_rect(pre.width(), pre.height(), w, h, z)
            canvas.drawImageRect(pre, skia.Rect.MakeXYWH(sx, sy, sw, sh), rect, _sampling(sw, w), None)
        canvas.restore()
        if param(it, "border"):
            bwid = max(1.0, 0.0028 * w)
            inner = skia.RRect.MakeRectXY(rect.makeInset(bwid / 2, bwid / 2), max(0.0, r - bwid / 2),
                                          max(0.0, r - bwid / 2))
            canvas.drawRRect(inner, C.stroke("#FFFFFF", bwid, 0.5))
    finally:
        canvas.restore()
        canvas.restore()
    return x0, y0, w, h
