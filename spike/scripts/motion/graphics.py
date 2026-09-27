"""Graficos overlay: garabatos dibujados a mano, iconos, tarjetas de informacion, lower thirds,
llamadas a la accion, barra de progreso y stickers de texto.

Contrato (docs/MOTION-ENGINE.md 3.1 y 4.2):
    RENDERERS[kind](canvas, item, lt, dur, ctx) -> None
  - `lt`: segundos desde el inicio del item (tiempo final); `dur`: duracion final del item.
  - Campos comunes: x, y (centro normalizado, salvo lower_third/progress), size (fraccion de W),
    color, anim_in, anim_out, rotation (grados), intensity (0..1).

Lenguaje visual (referencia: ejemplo_objetivo.mp4):
  - Garabatos con trazo de pincel real (ancho variable con punta afilada), "boil" a 12 fps
    (la geometria tiembla como animacion dibujada a mano), se dibujan con el trazo y rematan con pop.
  - Tarjetas premium: esquinas redondeadas, doble sombra suave, jerarquia tipografica,
    contenido que entra escalonado y micro-animaciones (contador, pin que cae, palomitas).
  - Todo movimiento usa easing organico (resortes / out_cubic / out_expo); nada lineal.

Parametros extra (opcionales, no normativos) que entiende este modulo:
  - comunes: `in_dur`, `out_dur` (s), `shadow` (0..1), `anchor` (face_left|face_right|face_top|face)
    para colocar el grafico junto a la cara cuando no hay x/y.
  - doodle: `draw_time` (s), `aspect` (circle_scribble), `bend` (flechas con path_to),
    `double` (underline doble), `glow` (0..1, halo del color para fondos oscuros),
    `style` (brush|sketch: sketch = contorno hueco aspero con rayado, como las flechas del objetivo).
  - icon: `style` (clean|sketch), `hatch` (bool, sketch).
  - card: `accent` (color de acento), `label` (texto del boton en link), `map_label`, `days`
    (schedule: indices 0..6 disponibles), `cursor` (bool).
  - cta: `button`, `button_done`, `subtitle`, `avatar` (initial|face).
  - sticker_text: `text`, `font`, `border` (bool).
"""
from __future__ import annotations

import math
import re
from collections import OrderedDict
from contextlib import contextmanager
from functools import lru_cache

import numpy as np
import skia

from . import assets, ease
from . import ctx as C

TAU = math.tau

# ============================================================== utilidades comunes


class _LRU(OrderedDict):
    """Cache LRU minima (por proceso) para geometria/texturas."""

    def __init__(self, cap: int = 256):
        super().__init__()
        self.cap = cap

    def get_or(self, key, fn):
        if key in self:
            self.move_to_end(key)
            return self[key]
        v = fn()
        self[key] = v
        if len(self) > self.cap:
            self.popitem(last=False)
        return v


_PIC_CACHE = _LRU(48)


def _g(item: dict, key: str, default=None):
    v = item.get(key)
    return default if v is None else v


def _num(item: dict, key: str, default: float) -> float:
    try:
        v = item.get(key)
        return float(default if v is None else v)
    except (TypeError, ValueError):
        return float(default)


def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def spring(t: float, freq: float = 3.2, damping: float = 0.42) -> float:
    """ease.spring que se asienta EXACTO en 1 cuando la envolvente cae bajo 1e-3: un grafico quieto
    produce cuadros identicos (mejor compresion y determinismo pixel a pixel)."""
    if t > 0 and math.exp(-_clamp(damping, 0.01, 0.999) * TAU * freq * t) < 1e-3:
        return 1.0
    return ease.spring(t, freq=freq, damping=damping)


@lru_cache(maxsize=256)
def _spring_reach(level: float, freq: float, damping: float) -> float:
    """Primer instante (s) en que spring(t, freq, damping) alcanza `level`."""
    t = 0.0
    while t < 3.0:
        if spring(t, freq, damping) >= level:
            return t
        t += 1.0 / 240.0
    return 3.0


def _badge_icon_q(t: float, t0: float, box_px: float, ctx) -> tuple[float, float]:
    """(progreso de trazo, escala) del icono dentro de un badge. Arranca en t0 con q minimo 0.15
    (nunca puntos sueltos); iconos de menos de 60 px ref entran con pop de escala completo."""
    if t < t0:
        return 0.0, 0.0
    if box_px < ctx.px(60):
        return 1.0, spring(t - t0, 3.2, 0.5)
    q = 0.15 + 0.85 * ease.out_cubic(_clamp((t - t0) / 0.45))
    return q, 0.82 + 0.18 * ease.out_back(_clamp((t - t0) / 0.3))


@lru_cache(maxsize=256)
def _font(name: str, size_px: float) -> skia.Font:
    return assets.font(name, size_px)


def font(name: str, size_px: float) -> skia.Font:
    """Fuente cacheada (tamano redondeado a 0.25 px para reutilizar)."""
    return _font(name, round(max(1.0, size_px) * 4) / 4)


def compose(outer, inner):
    """Compone path effects tolerando None (TrimPathEffect.Make(0,1) devuelve None)."""
    if outer is None:
        return inner
    if inner is None:
        return outer
    return skia.PathEffect.MakeCompose(outer, inner)


def trim(s: float, e: float):
    s, e = max(0.0, s), min(1.0, e)
    if s <= 0.0 and e >= 1.0:
        return None
    return skia.TrimPathEffect.Make(s, max(s + 1e-4, e))


def trim_paint(base: skia.Paint, s: float, e: float) -> skia.Paint:
    """Como ctx.trim_path_paint pero tolera el rango completo (Make(0,1) devuelve None)."""
    p = skia.Paint(base)
    if e - s < 1e-4:
        p.setAlphaf(0.0)
        return p
    eff = trim(s, e)
    if eff is not None:
        p.setPathEffect(eff)
    return p


@lru_cache(maxsize=64)
def _blur(sigma: float) -> skia.MaskFilter:
    return skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(0.1, sigma))


def blur_mask(sigma: float) -> skia.MaskFilter:
    return _blur(round(sigma * 2) / 2)


def _fonts(ctx) -> dict:
    """Familias de la tarjeta segun el estilo (display del estilo + cuerpo legible)."""
    pal = ctx.palette
    f = pal.get("fonts", {})
    minimal = ctx.style == "minimal"
    return {
        "display": f.get("title", "Poppins-Black"),
        "title": "Montserrat-Bold" if minimal else "Poppins-Bold",
        "body": "Montserrat-Medium" if minimal else "Poppins-Medium",
        "label": "Montserrat-SemiBold" if minimal else "Poppins-SemiBold",
        "black": "Montserrat-Black" if minimal else "Poppins-Black",
        "script": f.get("script", "Caveat-Bold"),
    }


def text_w(fnt: skia.Font, s: str, tracking: float = 0.0) -> float:
    if not s:
        return 0.0
    return fnt.measureText(s) + tracking * (len(s) - 1)


def draw_text(canvas: skia.Canvas, s: str, x: float, y: float, fnt: skia.Font, col, alpha: float = 1.0,
              align: str = "left", tracking: float = 0.0, paint: skia.Paint | None = None) -> float:
    """Texto en baseline y; align left|center|right; tracking en px. Devuelve el ancho."""
    if not s or alpha <= 0.003:
        return 0.0
    w = text_w(fnt, s, tracking)
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    p = paint if paint is not None else C.fill(col, alpha)
    if tracking == 0.0:
        canvas.drawString(s, x, y, fnt, p)
    else:
        for ch in s:
            canvas.drawString(ch, x, y, fnt, p)
            x += fnt.measureText(ch) + tracking
    return w


ELLIPSIS = "\u2026"


def ellipsize(fnt: skia.Font, s: str, max_w: float, tracking: float = 0.0) -> str:
    """Recorta `s` con elipsis para que quepa en max_w (busqueda binaria del prefijo)."""
    s = str(s)
    if text_w(fnt, s, tracking) <= max_w:
        return s
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if text_w(fnt, s[:mid].rstrip() + ELLIPSIS, tracking) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    if lo == 0:
        return ELLIPSIS if text_w(fnt, ELLIPSIS, tracking) <= max_w else ""
    return s[:lo].rstrip() + ELLIPSIS


def _split_word(fnt: skia.Font, wd: str, max_w: float) -> list[str]:
    """Parte una palabra mas ancha que max_w (URL, @usuario) en trozos que caben, prefiriendo
    cortar despues de '/', '.', '-', '_' o '@'."""
    out, cur = [], ""
    for ch in wd:
        if cur and fnt.measureText(cur + ch) > max_w:
            cut = max(cur.rfind(c) for c in "/.-_@")
            if 0 < cut < len(cur) - 1 and fnt.measureText(cur[:cut + 1]) > max_w * 0.45:
                out.append(cur[:cut + 1])
                cur = cur[cut + 1:]
            else:
                out.append(cur)
                cur = ""
        cur += ch
    if cur:
        out.append(cur)
    return out


def wrap(fnt: skia.Font, s: str, max_w: float, max_lines: int = 2) -> list[str]:
    """Corte de lineas voraz por ancho. Garantiza que TODAS las lineas quepan en max_w: las
    palabras mas anchas que la linea se parten y lo que no cabe en max_lines termina en elipsis."""
    words: list[str] = []
    for wd in str(s).split():
        words.extend([wd] if fnt.measureText(wd) <= max_w else _split_word(fnt, wd, max_w))
    lines: list[str] = []
    cur = ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if fnt.measureText(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    max_lines = max(1, int(max_lines))
    if len(lines) > max_lines:
        rest = " ".join(lines[max_lines - 1:])
        lines = lines[:max_lines - 1] + [rest]
        # hay texto que no cabe: la ultima linea siempre termina en elipsis
        last = ellipsize(fnt, rest, max_w)
        if not last.endswith(ELLIPSIS):
            last = ellipsize(fnt, last + ELLIPSIS, max_w)
        lines[-1] = last
    return [ellipsize(fnt, ln, max_w) for ln in lines]


def fit_font(name: str, s: str, size_px: float, max_w: float, min_px: float = 8.0) -> skia.Font:
    """Reduce el tamano hasta que `s` quepa en max_w."""
    f = font(name, size_px)
    w = f.measureText(s)
    for _ in range(3):  # el ancho no escala exacto con el tamano (hinting): se reintenta
        if w <= max_w or w <= 0 or f.getSize() <= min_px:
            break
        f = font(name, max(min_px, f.getSize() * max_w / w * 0.99))
        w = f.measureText(s)
    return f


XC = (C.SAFE["left"] + C.SAFE["right"]) / 2.0  # centro horizontal de SAFE (0.47)
SAFE_W = C.SAFE["right"] - C.SAFE["left"]       # ancho util (0.82 de W)
ANCHORS = ("face", "face_left", "face_right", "face_top")


def _face_ok(f) -> tuple | None:
    try:
        v = tuple(float(a) for a in f[:4])
    except Exception:
        return None
    if len(v) != 4 or not all(math.isfinite(a) for a in v) or v[2] <= 0 or v[3] <= 0:
        return None
    return v


def center_of(item: dict, ctx, dx: float = XC, dy: float = 0.5) -> tuple[float, float] | None:
    """Centro en px. Si no hay x/y y hay `anchor`, se coloca junto a la cara: la del cuadro
    (ctx.face) o, en huecos de deteccion, `_face0` (cara al inicio del item, la fija render).
    Con anchor y sin ninguna cara devuelve None (el grafico no se dibuja: un circulo 'alrededor
    de la cabeza' en el centro del cuadro seria peor que no dibujarlo)."""
    x, y = item.get("x"), item.get("y")
    anchor = item.get("anchor")
    if (x is None or y is None) and anchor in ANCHORS:
        face = _face_ok(ctx.face) if ctx.face else None
        if face is None and item.get("_face0") is not None:
            face = _face_ok(item["_face0"])
        if face is None:
            return None
        fx, fy, fw, fh = face
        ox, oy = {"face_left": (-0.5 * fw - 0.09, -0.25 * fh),
                  "face_right": (0.5 * fw + 0.09, -0.25 * fh),
                  "face_top": (0.0, -0.5 * fh - 0.05),
                  "face": (0.0, 0.0)}.get(anchor, (0.0, 0.0))
        ax = _clamp(fx + ox, C.SAFE["left"] + 0.04, C.SAFE["right"] - 0.04)
        ay = _clamp(fy + oy, C.SAFE["top"] + 0.03, C.SAFE["bottom"] - 0.03)
        x = ax if x is None else x
        y = ay if y is None else y
    return (dx if x is None else float(x)) * ctx.W, (dy if y is None else float(y)) * ctx.H


def safe_cx(cx: float, half_w: float, ctx) -> float:
    """Recorre el centro horizontal para que [cx-half_w, cx+half_w] quede dentro de SAFE
    (columna de botones de TikTok a la derecha). Si no cabe, lo centra en SAFE."""
    lo, hi = C.SAFE["left"] * ctx.W + half_w, C.SAFE["right"] * ctx.W - half_w
    if lo > hi:
        return XC * ctx.W
    return _clamp(cx, lo, hi)


# -------------------------------------------------------------- entradas / salidas
ANIM_IN = ("pop", "draw", "fade", "slide_up", "slide_down", "slide_left", "slide_right", "scale",
           "drop", "flip", "none", "rise", "scale_down")
ANIM_OUT = ("fade", "pop_out", "slide_up", "slide_down", "undraw", "none")
# nombres que usa schema.py (GRAPHIC_ANIM_IN) -> nombres internos
ANIM_ALIASES = {"rise": "slide_up", "scale_down": "scale", "slide_in": "slide_up", "cut": "none"}

# Valores recomendados por tipo. schema.py (_adopt_modules) los adopta como defaults del
# inspector: el esquema generico usa size=0.18, y=0.3, stroke=8 para todos los kinds, que
# dejan un CTA microscopico o la barra de progreso a media pantalla.
DEFAULTS = {
    "doodle": {"x": XC, "y": 0.4, "stroke": 12.0, "anim_in": "draw", "anim_out": "pop_out",
               "draw": True, "wiggle": 0.5, "shadow": 0.55},
    "icon": {"x": XC, "y": 0.4, "size": 0.16, "anim_in": "pop", "anim_out": "pop_out", "draw": True,
             "badge": "none", "style": "clean"},
    "card": {"x": XC, "y": 0.62, "size": 0.74, "anim_in": "slide_up", "anim_out": "slide_down", "theme": "light"},
    "lower_third": {"x": 0.07, "y": 0.70, "size": 0.056, "theme": "dark", "anim_in": "draw",
                    "anim_out": "undraw"},
    "cta": {"x": XC, "y": 0.70, "size": 0.62, "anim_in": "pop", "anim_out": "pop_out"},
    # la barra usa su propia clave `bar_y` (borde superior, fraccion de H); x/y/size comunes no aplican
    "progress": {"thickness": 8.0, "bar_y": 0.0, "y": 0.0, "anim_in": "fade", "anim_out": "none"},
    "sticker_text": {"x": XC, "y": 0.35, "size": 0.062, "anim_in": "pop", "anim_out": "pop_out"},
}
CTA_SIZE = {"follow": 0.72, "link_below": 0.56, "comment": 0.62, "save": 0.62, "share": 0.62}
# rango legible por kind/variante (fraccion de W): un valor fuera de rango (p. ej. el size=0.18
# generico del esquema) se acota en vez de dibujar algo ilegible o que se sale de SAFE
SIZE_RANGE = {"card": (0.5, SAFE_W), "follow": (0.56, SAFE_W), "link_below": (0.4, 0.74),
              "cta": (0.45, SAFE_W), "sticker_text": (0.035, 0.16), "lower_third": (0.03, 0.09)}


def size_of(item: dict, key: str, default: float) -> float:
    lo, hi = SIZE_RANGE.get(key, (0.01, 2.0))
    return _clamp(_num(item, "size", default), lo, hi)


_COLOR_KEYS = ("color", "accent", "badge_color", "bg", "fg", "arrow_color", "button_color")


def _color_ok(v) -> bool:
    if v is None:
        return False
    try:
        C.color(v)
        return True
    except Exception:
        return False


def sanitize(item: dict) -> dict:
    """Copia tolerante del item: descarta colores invalidos y numeros no numericos (el director
    o la UI pueden mandar basura; un grafico nunca debe tumbar el render)."""
    it = dict(item or {})
    for k in _COLOR_KEYS:
        if k in it and not _color_ok(it[k]):
            it.pop(k)
    for k in ("x", "y", "size", "rotation", "intensity", "stroke", "wiggle", "shadow", "thickness",
              "draw_time", "in_dur", "out_dur", "aspect", "bend", "glow", "press_at", "tilt", "bar_y", "margin",
              "progress"):
        if k in it and it[k] is not None:
            try:
                v = float(it[k])
                it[k] = v if math.isfinite(v) else None
            except (TypeError, ValueError):
                it[k] = None
    if it.get("size") is not None and it["size"] <= 0:
        it["size"] = None
    pt = it.get("path_to")
    if pt is not None:
        try:
            it["path_to"] = [float(pt[0]), float(pt[1])]
        except Exception:
            it["path_to"] = None
    for k in ("name", "variant", "badge", "style", "theme", "icon", "anim_in", "anim_out", "font", "avatar",
              "anchor"):
        if k in it and not isinstance(it[k], str):
            it.pop(k)
    for k in ("title", "subtitle", "value", "label", "text", "handle", "button", "button_done", "map_label"):
        v = it.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (str, int, float)) or (isinstance(v, float) and not math.isfinite(v)):
            it.pop(k)
        elif not isinstance(v, str):
            it[k] = f"{v:g}" if isinstance(v, float) else str(v)
    if "items" in it:
        its = it["items"]
        it["items"] = [str(x) for x in its if isinstance(x, (str, int, float)) and not isinstance(x, bool)] \
            if isinstance(its, (list, tuple)) else []
    if "days" in it and not isinstance(it["days"], (list, tuple)):
        it.pop("days")
    for k in ("anim_in", "anim_out"):
        if it.get(k) is not None:
            it[k] = ANIM_ALIASES.get(it[k], it[k])
    return it


class _St:
    __slots__ = ("s", "dx", "dy", "a", "r", "sy")

    def __init__(self, s=1.0, dx=0.0, dy=0.0, a=1.0, r=0.0, sy=1.0):
        self.s, self.dx, self.dy, self.a, self.r, self.sy = s, dx, dy, a, r, sy

    def __mul__(self, o: "_St") -> "_St":
        return _St(self.s * o.s, self.dx + o.dx, self.dy + o.dy, self.a * o.a, self.r + o.r, self.sy * o.sy)


def enter_state(kind: str, lt: float, tin: float, ctx, intensity: float = 1.0, big: bool = False) -> _St:
    """Estado de entrada (escala, desplazamiento, alpha, rotacion) para t=lt.
    big=True (tarjetas, CTA): pop sutil (desde 62%, ~4% de rebase) y opaco casi de inmediato;
    un panel grande que crece desde 0 con 20% de rebase se ve caricaturesco."""
    kind = ANIM_ALIASES.get(kind, kind)
    tin = max(1e-3, tin)
    p = _clamp(lt / tin)
    k = intensity
    if kind == "none" or p >= 1.0 and kind not in ("pop", "draw", "slide_up", "slide_down",
                                                   "slide_left", "slide_right", "drop"):
        return _St()
    if kind == "pop":
        if big:
            sp = spring(lt, freq=2.7, damping=0.6 + (1.0 - k) * 0.2)
            return _St(s=0.62 + 0.38 * sp, a=_clamp(lt / 0.07))
        damp = 0.42 + (1.0 - k) * 0.3
        sp = spring(lt, freq=2.9, damping=damp)
        return _St(s=max(0.0, sp), a=_clamp(lt / 0.08), r=(1.0 - sp) * -9.0 * k)
    if kind == "draw":
        sp = spring(lt, freq=2.6, damping=0.5)
        return _St(s=0.9 + 0.1 * sp)
    if kind == "fade":
        return _St(a=ease.out_quad(p), s=0.985 + 0.015 * ease.out_cubic(p))
    if kind in ("slide_up", "slide_down", "slide_left", "slide_right"):
        sp = spring(lt, freq=2.2, damping=0.74)
        d = (1.0 - sp) * ctx.px(80)
        dx = {"slide_left": d, "slide_right": -d}.get(kind, 0.0)
        dy = {"slide_up": d, "slide_down": -d}.get(kind, 0.0)
        return _St(dx=dx, dy=dy, a=ease.out_quad(_clamp(lt / 0.1)), s=0.965 + 0.035 * sp)
    if kind == "scale":
        return _St(s=1.0 + 0.4 * (1.0 - ease.out_cubic(p)), a=ease.out_quad(p))
    if kind == "drop":
        return _St(dy=-(1.0 - ease.out_bounce(p)) * ctx.px(240), a=_clamp(p * 5))
    if kind == "flip":  # aproximacion 2D (las tarjetas usan perspectiva real)
        sp = spring(lt, freq=2.2, damping=0.6)
        return _St(sy=max(0.02, sp), a=_clamp(lt / (tin * 0.3)))
    return _St()


def exit_state(kind: str, pout: float, ctx) -> _St:
    kind = ANIM_ALIASES.get(kind, kind)
    if pout <= 0.0 or kind == "none":
        return _St()
    if kind == "pop_out":
        return _St(s=max(0.0, 1.0 - 0.75 * ease.in_back(pout, 1.4)), a=1.0 - ease.in_quad(pout))
    if kind == "slide_up":
        return _St(dy=-ctx.px(70) * ease.in_cubic(pout), a=1.0 - ease.in_quad(pout))
    if kind == "slide_down":
        return _St(dy=ctx.px(50) * ease.in_cubic(pout), a=1.0 - ease.in_quad(pout))
    if kind == "undraw":
        return _St(a=1.0 - _clamp((pout - 0.75) / 0.25))
    return _St(a=1.0 - ease.out_quad(pout), s=1.0 - 0.04 * pout)  # fade


@contextmanager
def group(canvas: skia.Canvas, cx: float, cy: float, st: _St):
    canvas.save()
    canvas.translate(cx + st.dx, cy + st.dy)
    if st.r:
        canvas.rotate(st.r)
    if st.s != 1.0 or st.sy != 1.0:
        canvas.scale(st.s, st.s * st.sy)
    try:
        yield canvas
    finally:
        canvas.restore()


def _visible(st: _St) -> bool:
    return st.a > 0.004 and st.s > 0.004 and st.sy > 0.004


# ============================================================== pincel dibujado a mano


def _catmull(P: np.ndarray, per: int = 8, closed: bool = False) -> np.ndarray:
    """Curva Catmull-Rom uniforme que pasa por los puntos de control (densificada)."""
    P = np.asarray(P, np.float64)
    n = len(P)
    if n < 2:
        return P
    if n == 2 and not closed:
        t = np.linspace(0.0, 1.0, per + 1)[:, None]
        return P[0] + (P[1] - P[0]) * t
    if closed:
        Q = np.vstack([P[-1:], P, P[:2]])
    else:
        Q = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    p0, p1, p2, p3 = Q[:-3], Q[1:-2], Q[2:-1], Q[3:]
    t = np.linspace(0.0, 1.0, per, endpoint=False)[None, :, None]
    a = 2 * p1
    b = p2 - p0
    c = 2 * p0 - 5 * p1 + 4 * p2 - p3
    d = -p0 + 3 * p1 - 3 * p2 + p3
    seg = 0.5 * (a[:, None] + b[:, None] * t + c[:, None] * t * t + d[:, None] * t * t * t)
    end = P[:1] if closed else P[-1:]
    return np.vstack([seg.reshape(-1, 2), end])


def _resample(P: np.ndarray, n: int) -> tuple[np.ndarray, float]:
    d = np.hypot(*np.diff(P, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(d)])
    L = float(s[-1])
    if L < 1e-9:
        return np.repeat(P[:1], max(2, n), 0), 0.0
    u = np.linspace(0.0, L, max(2, n))
    return np.stack([np.interp(u, s, P[:, 0]), np.interp(u, s, P[:, 1])], 1), L


def _plen(P: np.ndarray) -> float:
    return float(np.hypot(*np.diff(P, axis=0).T).sum()) if len(P) > 1 else 0.0


def _vn(x: np.ndarray) -> np.ndarray:
    """Ruido suave determinista [-1,1] (misma forma que ease.wobble, vectorizado)."""
    return np.sin(x * 2.1) * 0.5 + np.sin(x * 3.7 + 1.3) * 0.3 + np.sin(x * 7.3 + 0.4) * 0.2


def jitter(P: np.ndarray, amp: float, k: float, seed: float) -> np.ndarray:
    """Desplaza puntos con ruido que depende de la POSICION (puntos compartidos se mueven igual)."""
    if amp <= 0:
        return P
    x, y = P[:, 0], P[:, 1]
    ph = k * 1.618 + seed * 7.13
    dx = _vn(x * 2.3 + y * 1.1 + ph)
    dy = _vn(x * 1.3 - y * 2.7 + ph * 1.31 + 5.0)
    return P + amp * np.stack([dx, dy], 1)


def _profile(u: np.ndarray, tin: float, tout: float, wmin: float) -> np.ndarray:
    a = np.ones_like(u) if tin <= 0 else np.sin(np.clip(u / tin, 0, 1) * (math.pi / 2))
    b = np.ones_like(u) if tout <= 0 else np.sin(np.clip((1 - u) / tout, 0, 1) * (math.pi / 2))
    return wmin + (1.0 - wmin) * a * b


def _outline(pts: np.ndarray, wv: np.ndarray, cap: int = 6) -> np.ndarray:
    """Contorno cerrado de un trazo de ancho variable con remates redondos."""
    T = np.gradient(pts, axis=0)
    nr = np.hypot(T[:, 0], T[:, 1])
    nr[nr < 1e-9] = 1.0
    T = T / nr[:, None]
    N = np.stack([-T[:, 1], T[:, 0]], 1)
    h = (wv * 0.5)[:, None]
    Lf = pts + N * h
    Rt = pts - N * h
    a1 = math.atan2(N[-1, 1], N[-1, 0])
    ang = np.linspace(a1, a1 - math.pi, cap + 2)[1:-1]
    cap_e = pts[-1] + h[-1] * np.stack([np.cos(ang), np.sin(ang)], 1)
    a0 = math.atan2(N[0, 1], N[0, 0])
    ang0 = np.linspace(a0 + math.pi, a0, cap + 2)[1:-1]
    cap_s = pts[0] + h[0] * np.stack([np.cos(ang0), np.sin(ang0)], 1)
    return np.vstack([Lf, cap_e, Rt[::-1], cap_s])


def _add_poly(path: skia.Path, poly: np.ndarray):
    """Agrega un poligono siempre en sentido horario (area > 0 en y-abajo) para union por winding."""
    x, y = poly[:, 0], poly[:, 1]
    area = float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))
    if area < 0:
        poly = poly[::-1]
    path.addPoly([skia.Point(float(a), float(b)) for a, b in poly.tolist()], True)


def brush(path: skia.Path, segs: list[np.ndarray], width: float, u0: float = 0.0, u1: float = 1.0,
          taper: tuple[float, float] = (0.12, 0.3), wmin: float = 0.22, rough: float = 0.10,
          seed: float = 0.0, step_px: float = 5.0):
    """Agrega a `path` el trazo de pincel de la polilinea `segs` (lista de sub-curvas densas y
    consecutivas; cada union es una esquina con remate redondo) entre u0 y u1 (0..1 de longitud)."""
    lens = [_plen(s) for s in segs]
    L = sum(lens)
    if L <= 1e-6 or u1 <= u0:
        return
    acc = 0.0
    for seg, l in zip(segs, lens):
        a, b = acc / L, (acc + l) / L
        acc += l
        lo, hi = max(u0, a), min(u1, b)
        if hi <= lo or l <= 1e-9:
            continue
        n = int(_clamp(l / step_px, 6, 220))
        P, _ = _resample(seg, n)
        s0, s1 = (lo - a) / (b - a), (hi - a) / (b - a)
        i0, i1 = s0 * (n - 1), s1 * (n - 1)
        k0, k1 = int(math.ceil(i0)), int(math.floor(i1))

        def at(fi):
            j = min(int(fi), n - 2)
            f = fi - j
            return P[j] * (1 - f) + P[j + 1] * f

        mid = P[k0:k1 + 1] if k1 >= k0 else P[:0]
        pts = np.vstack([at(i0)[None], mid, at(i1)[None]])
        # quitar duplicados en los extremos
        keep = np.concatenate([[True], np.hypot(*np.diff(pts, axis=0).T) > 1e-6])
        pts = pts[keep]
        ug = a + (b - a) * np.linspace(s0, s1, len(pts))
        wv = width * _profile(ug, taper[0], taper[1], wmin)
        if rough > 0:
            wv = wv * (1.0 + rough * _vn(ug * 9.0 + seed * 3.1))
        if len(pts) < 2 or _plen(pts) < 0.35 * float(wv.max()):
            c = pts[-1]
            path.addCircle(float(c[0]), float(c[1]), float(max(0.5, wv.max() * 0.5)), skia.PathDirection.kCW)
            continue
        _add_poly(path, _outline(pts, wv))


def quad_star(path: skia.Path, cx: float, cy: float, rx: float, ry: float, pinch: float, rot: float = 0.0,
              jit: np.ndarray | None = None):
    """Destello de 4 puntas con lados concavos (curvas cuadraticas)."""
    ca, sa = math.cos(rot), math.sin(rot)
    tips = [(0, -ry), (rx, 0), (0, ry), (-rx, 0)]
    ctrl = [(pinch * rx, -pinch * ry), (pinch * rx, pinch * ry), (-pinch * rx, pinch * ry), (-pinch * rx, -pinch * ry)]

    def tr(p, i):
        x, y = p
        if jit is not None:
            x += jit[i % len(jit), 0]
            y += jit[i % len(jit), 1]
        return cx + x * ca - y * sa, cy + x * sa + y * ca

    t = [tr(p, i) for i, p in enumerate(tips)]
    c = [tr(p, i + 4) for i, p in enumerate(ctrl)]
    path.moveTo(*t[0])
    for i in range(4):
        path.quadTo(*c[i], *t[(i + 1) % 4])
    path.close()


# ============================================================== garabatos (doodle)

DOODLE_NAMES = ("sparkle", "star4", "star5", "burst", "rays", "arrow_curve", "arrow_straight",
                "arrow_down", "circle_scribble", "underline", "squiggle", "dotted_path", "heart",
                "check", "cross", "exclaim", "question", "swirl", "lightning", "loop")

# extension por defecto (fraccion de W que ocupa el rango unitario [-1,1])
DOODLE_SIZE = {"sparkle": 0.15, "star4": 0.14, "star5": 0.16, "burst": 0.30, "rays": 0.24,
               "arrow_curve": 0.34, "arrow_straight": 0.30, "arrow_down": 0.20, "circle_scribble": 0.50,
               "underline": 0.50, "squiggle": 0.42, "dotted_path": 0.50, "heart": 0.16, "check": 0.16,
               "cross": 0.16, "exclaim": 0.14, "question": 0.16, "swirl": 0.18, "lightning": 0.16,
               "loop": 0.40}
DRAW_TIME = {"sparkle": 0.55, "star4": 0.45, "star5": 0.6, "burst": 0.42, "rays": 0.38,
             "arrow_curve": 0.55, "arrow_straight": 0.45, "arrow_down": 0.45, "circle_scribble": 0.6,
             "underline": 0.38, "squiggle": 0.5, "dotted_path": 0.85, "heart": 0.6, "check": 0.32,
             "cross": 0.4, "exclaim": 0.38, "question": 0.55, "swirl": 0.6, "lightning": 0.3, "loop": 0.6}
STROKE = {"burst": 11.0, "rays": 12.0, "circle_scribble": 11.0, "underline": 15.0, "dotted_path": 12.0,
          "star5": 11.0, "check": 15.0, "cross": 15.0, "exclaim": 17.0, "question": 14.0}


def _E(kind, segs=None, t=(0.0, 1.0), w=1.0, taper=(0.12, 0.3), wmin=0.22, double=False, **kw):
    e = {"kind": kind, "segs": [np.asarray(s, np.float64) for s in (segs or [])], "t": t, "w": w,
         "taper": taper, "wmin": wmin, "double": double}
    e.update(kw)
    return e


def _arrow_head(end: np.ndarray, prev: np.ndarray, hl: float, ang: float = 30.0) -> list:
    d = end - prev
    d = d / max(1e-9, float(np.hypot(*d)))
    a = math.radians(ang)
    back = -d

    def rot(v, th):
        c, s = math.cos(th), math.sin(th)
        return np.array([v[0] * c - v[1] * s, v[0] * s + v[1] * c])

    l_end = end + rot(back, a) * hl
    r_end = end + rot(back, -a) * hl
    # leve curvatura de mano en cada ala
    lm = (end + l_end) / 2 + rot(d, math.pi / 2) * hl * 0.05
    rm = (end + r_end) / 2 - rot(d, math.pi / 2) * hl * 0.05
    return [[l_end, lm, end], [end, rm, r_end]]


def _doodle_spec(name: str, item: dict, rnd: float) -> list[dict]:
    """Geometria base en espacio unitario ([-1,1], y hacia abajo)."""
    E = _E
    if name == "sparkle":
        return [E("qstar", c=(0.0, 0.0), r=(0.72, 1.0), pinch=0.14, t=(0.0, 0.55)),
                E("qstar", c=(0.98, -0.72), r=(0.24, 0.34), pinch=0.16, t=(0.3, 0.8)),
                E("qstar", c=(-0.9, 0.62), r=(0.17, 0.24), pinch=0.16, t=(0.45, 0.95)),
                E("dot", c=(0.78, 0.55), r=0.07, t=(0.55, 1.0))]
    if name == "star4":
        tips = [(0, -1), (0.8, 0), (0, 1), (-0.8, 0)]
        segs = []
        for i in range(4):
            a, b = np.array(tips[i], float), np.array(tips[(i + 1) % 4], float)
            m = (a + b) / 2 * 0.36
            segs.append([a, m, b])
        return [E("brush", segs, t=(0.0, 1.0), taper=(0.05, 0.12), wmin=0.5, double=True)]
    if name == "star5":
        pts = [(math.cos(-math.pi / 2 + i * 4 * math.pi / 5), math.sin(-math.pi / 2 + i * 4 * math.pi / 5))
               for i in range(6)]
        pts[-1] = (pts[-1][0] + 0.1, pts[-1][1] - 0.08)  # no cierra perfecto: gesto de mano
        segs = []
        for i in range(5):
            a, b = np.array(pts[i]), np.array(pts[i + 1])
            n = np.array([-(b - a)[1], (b - a)[0]]) * (0.03 * (1 if i % 2 else -1))
            segs.append([a, (a + b) / 2 + n, b])
        return [E("brush", segs, t=(0.0, 1.0), taper=(0.06, 0.12), wmin=0.4)]
    if name == "burst":
        out = []
        n = 11
        for i in range(n):
            ang = -math.pi / 2 + i * TAU / n
            r1 = 1.0 if i % 2 == 0 else 0.78
            a = np.array([math.cos(ang), math.sin(ang)]) * 0.48
            b = np.array([math.cos(ang), math.sin(ang)]) * r1
            out.append(E("brush", [[a, b]], t=(i * 0.045, i * 0.045 + 0.5), taper=(0.12, 0.45), wmin=0.3))
        return out
    if name == "rays":
        out = []
        angs = [-150, -122, -90, -58, -30]
        for i, dg in enumerate(angs):
            ang = math.radians(dg)
            r1 = 1.0 if dg == -90 else (0.9 if abs(dg + 90) < 40 else 0.76)
            d = np.array([math.cos(ang), math.sin(ang)])
            nrm = np.array([-d[1], d[0]])
            a, b = d * 0.34, d * r1
            mid = (a + b) / 2 + nrm * 0.04
            order = [2, 1, 3, 0, 4].index(i)
            out.append(E("brush", [[a, mid, b]], t=(order * 0.09, order * 0.09 + 0.55), taper=(0.12, 0.35),
                         wmin=0.35, ray=True))
        return out
    if name in ("arrow_curve", "arrow_straight", "arrow_down"):
        if name == "arrow_curve":
            ctrl = np.array([(-1.0, 0.5), (-0.55, -0.12), (0.02, -0.42), (0.55, -0.38), (0.95, -0.08)])
        elif name == "arrow_straight":
            ctrl = np.array([(-1.0, 0.06), (-0.3, -0.03), (0.35, -0.02), (0.98, 0.0)])
        else:
            ctrl = np.array([(0.04, -1.0), (-0.06, -0.35), (0.05, 0.3), (0.0, 0.95)])
        return _arrow_elems(ctrl, 1.0)
    if name == "circle_scribble":
        asp = _num(item, "aspect", 0.62)
        n = 16
        th0 = -2.55 + (rnd - 0.5) * 0.4
        turns = 1.16
        pts = []
        for i in range(n + 1):
            u = i / n
            th = th0 + u * TAU * turns
            r = 1.02 - 0.07 * u + 0.025 * math.sin(u * 9.0 + rnd * 6)
            pts.append((r * math.cos(th), asp * r * math.sin(th)))
        return [E("brush", [pts], taper=(0.08, 0.25), wmin=0.3)]
    if name == "underline":
        segs = [[(-1.0, 0.08), (-0.35, 0.02), (0.35, -0.02), (1.0, -0.12)]]
        out = [E("brush", segs, t=(0.0, 0.7 if item.get("double") else 1.0), taper=(0.06, 0.35), wmin=0.2)]
        if item.get("double"):
            out.append(E("brush", [[(0.85, 0.12), (0.1, 0.2), (-0.72, 0.28)]], t=(0.62, 1.0), w=0.75,
                         taper=(0.1, 0.5), wmin=0.15))
        return out
    if name == "squiggle":
        xs = np.linspace(-1, 1, 17)
        pts = np.stack([xs, 0.2 * np.sin(xs * math.pi * 2.25)], 1)
        return [E("brush", [pts], taper=(0.1, 0.3), wmin=0.25)]
    if name == "dotted_path":
        ctrl = np.array([(-1.0, 0.25), (-0.5, -0.25), (0.05, 0.1), (0.55, 0.25), (0.95, -0.2)])
        return _dotted_elems(ctrl)
    if name == "heart":
        pts = [(0.02, 0.92), (-0.5, 0.42), (-0.93, -0.12), (-0.8, -0.72), (-0.35, -0.86), (-0.04, -0.46),
               (0.0, -0.4), (0.04, -0.46), (0.35, -0.86), (0.8, -0.72), (0.93, -0.12), (0.5, 0.42),
               (0.02, 0.9), (-0.1, 0.82)]
        return [E("brush", [pts], taper=(0.08, 0.3), wmin=0.3)]
    if name == "check":
        return [E("brush", [[(-0.85, -0.02), (-0.55, 0.26), (-0.3, 0.55)],
                            [(-0.3, 0.55), (0.2, -0.1), (0.9, -0.78)]], taper=(0.12, 0.35), wmin=0.25)]
    if name == "cross":
        return [E("brush", [[(-0.8, -0.82), (0.02, 0.0), (0.84, 0.8)]], t=(0.0, 0.5), taper=(0.15, 0.3)),
                E("brush", [[(0.8, -0.84), (0.0, 0.02), (-0.82, 0.78)]], t=(0.48, 1.0), taper=(0.15, 0.3))]
    if name == "exclaim":
        return [E("brush", [[(0.02, -1.0), (0.0, -0.3), (-0.02, 0.42)]], t=(0.0, 0.7), taper=(0.02, 0.7), wmin=0.25),
                E("dot", c=(-0.02, 0.84), r=0.13, t=(0.7, 1.0))]
    if name == "question":
        pts = [(-0.58, -0.45), (-0.42, -0.86), (0.08, -1.0), (0.55, -0.74), (0.5, -0.3), (0.05, 0.0), (0.0, 0.38)]
        return [E("brush", [pts], t=(0.0, 0.75), taper=(0.1, 0.4), wmin=0.25),
                E("dot", c=(0.0, 0.84), r=0.12, t=(0.75, 1.0))]
    if name == "swirl":
        pts = []
        n = 26
        tmax = 2.3 * TAU
        for i in range(n + 1):
            th = i / n * tmax
            r = 0.1 + 0.9 * (i / n)
            pts.append((r * math.cos(th), r * math.sin(th)))
        return [E("brush", [pts], taper=(0.05, 0.35), wmin=0.25)]
    if name == "lightning":
        poly = [(0.3, -1.0), (-0.52, 0.12), (-0.03, 0.1), (-0.32, 1.0), (0.58, -0.2), (0.06, -0.18), (0.38, -1.0)]
        return [E("bolt", poly=np.array(poly), t=(0.0, 1.0))]
    if name == "loop":
        pts = [(-1.0, 0.24), (-0.5, 0.18), (-0.05, 0.02), (0.3, -0.3), (0.32, -0.66), (0.06, -0.86),
               (-0.22, -0.68), (-0.24, -0.32), (0.08, -0.04), (0.55, 0.12), (1.0, 0.06)]
        return [E("brush", [pts], taper=(0.08, 0.35), wmin=0.25)]
    return _doodle_spec("sparkle", item, rnd)


def _arrow_elems(ctrl: np.ndarray, scale: float) -> list[dict]:
    dense = _catmull(ctrl, 10)
    L = _plen(dense)
    hl = min(0.34 * scale, 0.22 * L)
    head = _arrow_head(dense[-1], dense[-4], hl, 32.0)
    return [_E("brush", [ctrl], t=(0.0, 0.78), taper=(0.1, 0.06), wmin=0.3, double=True, shaft=True),
            _E("brush", head, t=(0.74, 1.0), w=0.95, taper=(0.25, 0.25), wmin=0.35)]


def _dotted_elems(ctrl: np.ndarray) -> list[dict]:
    dense = _catmull(ctrl, 10)
    return [_E("dash", [ctrl], t=(0.0, 0.92)),
            _E("head_fill", end=dense[-1].copy(), prev=dense[-4].copy(), t=(0.86, 1.0))]


def _doodle_geometry(name: str, item: dict, ctx, S: float, cx: float, cy: float):
    """(elementos, S, cx, cy). Con `path_to` las flechas/punteados van de (x,y) a path_to."""
    rnd = ctx.rand(f"dd:{item.get('id', name)}")
    pt = item.get("path_to")
    if pt and name in ("arrow_curve", "arrow_straight", "arrow_down", "dotted_path", "squiggle"):
        a = np.array([cx, cy])
        b = np.array([float(pt[0]) * ctx.W, float(pt[1]) * ctx.H])
        d = b - a
        L = float(np.hypot(*d))
        if L > 1.0:
            S = L / 2
            u = d / L
            nrm = np.array([-u[1], u[0]])
            bend = _num(item, "bend", 0.32 * (1 if rnd > 0.5 else -1))
            if name == "arrow_straight":
                bend *= 0.2
            ua = np.zeros(2)
            ub = d / S
            ctrl = np.array([ua,
                             ua + (ub - ua) * 0.3 + nrm * bend * 1.3,
                             ua + (ub - ua) * 0.68 + nrm * bend * 1.1,
                             ub])
            if name == "dotted_path":
                return _dotted_elems(ctrl), S, cx, cy
            return _arrow_elems(ctrl, 0.5), S, cx, cy
    return _doodle_spec(name, item, rnd), S, cx, cy


def _dd_color(name: str, item: dict, ctx) -> int:
    if item.get("color"):
        return C.color(item["color"])
    pal = ctx.palette
    if name in ("sparkle", "star4"):
        return C.mix(pal["accent"], "#FFFFFF", 0.38)
    if name == "lightning":
        return C.color(pal["accent"])
    if name == "heart":
        return C.color("#FF4D6A")
    return C.color(pal["primary"])


def _idle(name: str, t: float, k: float, inten: float) -> _St:
    """Micro-movimiento despues de dibujado (t = segundos desde que termino de dibujarse)."""
    if t <= 0:
        return _St()
    if name in ("sparkle", "star4"):
        tw = math.sin(ease.hold_steps(t, 12) * TAU * 1.3)
        return _St(s=1.0 + 0.06 * tw * inten, r=3.0 * math.sin(t * 1.7) * inten)
    if name == "arrow_down":
        b = abs(math.sin(t * math.pi * 1.6))
        return _St(dy=b * 0.10, s=1.0)  # dy en unidades (se multiplica por S)
    if name == "heart":
        ph = (t % 1.0)
        beat = math.exp(-((ph - 0.1) / 0.06) ** 2) * 0.09 + math.exp(-((ph - 0.3) / 0.06) ** 2) * 0.06
        return _St(s=1.0 + beat * inten)
    if name in ("rays", "burst"):
        return _St(s=1.0 + 0.05 * math.sin(ease.hold_steps(t, 8) * TAU * 1.5) * inten)
    if name == "lightning":
        fl = 0.86 + 0.14 * (0.5 + 0.5 * math.sin(k * 2.7))
        return _St(a=fl)
    if name == "exclaim":
        return _St(r=5.0 * math.exp(-t * 5) * math.sin(t * 40) * inten)
    return _St()


def draw_doodle(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    item = sanitize(item)
    name = item.get("name") or "sparkle"
    if name not in DOODLE_NAMES:
        name = "sparkle"
    inten = _clamp(_num(item, "intensity", 1.0))
    size = _num(item, "size", DOODLE_SIZE[name])
    S0 = size * ctx.W / 2.0
    c = center_of(item, ctx, XC, 0.4)
    if c is None:
        return
    cx, cy = c
    elems, S, cx, cy = _doodle_geometry(name, item, ctx, S0, cx, cy)
    rnd = ctx.rand(f"dd:{item.get('id', name)}")
    col = _dd_color(name, item, ctx)

    draw_on = bool(_g(item, "draw", True))
    anim_in = item.get("anim_in") or ("draw" if draw_on else "pop")
    if draw_on and anim_in in ("pop", "draw"):
        anim_in = "draw"  # dibujarse ya es la entrada; un pop desde 0 encima se ve doble
    elif not draw_on and anim_in == "draw":
        anim_in = "pop"
    anim_out = item.get("anim_out") or "pop_out"
    tdraw = _num(item, "draw_time", DRAW_TIME[name])
    tin = _num(item, "in_dur", tdraw if anim_in == "draw" else 0.4)
    if not draw_on:
        tdraw = min(tin, 0.4)
    tout = _num(item, "out_dur", 0.34 if anim_out == "undraw" else 0.22)
    tdraw = min(tdraw, max(0.08, dur * 0.6))
    _, pout = ease.envelope(lt, dur, tin, tout)

    st = enter_state(anim_in, lt, tin, ctx, inten) * exit_state(anim_out, pout, ctx)
    kboil = round(ease.hold_steps(lt, 12.0) * 12.0)  # boil a 12 fps (animacion a doces)
    idle = _idle(name, lt - tdraw, kboil, inten)
    st = st * _St(s=idle.s, r=idle.r, a=idle.a)
    rot = _num(item, "rotation", 0.0) + (rnd - 0.5) * 8.0 * inten
    st.r += rot
    if not _visible(st):
        return
    idle_dy = idle.dy * S

    # boil: la geometria tiembla a 12 fps (siempre, incluso quieta)
    wig = _clamp(_num(item, "wiggle", 0.5))
    amp = (ctx.px(1.2) + ctx.px(4.2) * wig) * (0.5 + 0.5 * inten) / max(S, 1e-6)
    amp_static = 0.035 * (0.4 + wig)
    seed = rnd * 10.0
    stroke_px = ctx.px(_num(item, "stroke", STROKE.get(name, 12.0)))
    sketch = item.get("style") == "sketch"
    if sketch:
        stroke_px *= 2.4  # cuerpo ancho: se dibuja hueco (contorno) con rayado dentro
    undraw = _clamp(ease.in_out_quad(pout)) if anim_out == "undraw" else 0.0
    per = 5 if ctx.draft else 8

    path = skia.Path()
    dash_paths = []
    for ei, e in enumerate(elems):
        t0, t1 = e["t"]
        if draw_on:
            st_t = t0 * tdraw
            ln = max(1e-3, (t1 - t0) * tdraw)
            q = _clamp((lt - st_t) / ln)
            loc_t = lt - st_t
        else:
            q, loc_t = 1.0, 10.0
        if q <= 0.0:
            continue
        kind = e["kind"]
        if kind in ("brush", "dash"):
            segs = []
            for s_i, sc in enumerate(e["segs"]):
                P = jitter(sc, amp_static, -3.7, seed)
                P = jitter(P, amp, kboil, seed + ei * 0.37)
                segs.append(_catmull(P, per) * S)
            prog = ease.in_out_quad(q) if kind == "brush" else ease.out_cubic(q)
            if e.get("ray") and lt > tdraw:
                # rayos de energia: laten alargandose/encogiendose por boil
                prog = 0.82 + 0.18 * (0.5 + 0.5 * math.sin(kboil * 1.9 + ei * 1.3))
            if kind == "dash":
                dash_paths.append((segs, prog, undraw))
                continue
            w = stroke_px * e["w"]
            brush(path, segs, w, undraw, prog, e["taper"], e["wmin"], 0.12, seed + ei, ctx.px(5))
            if e.get("double") and not ctx.draft:
                segs2 = []
                for sc in e["segs"]:
                    P = jitter(sc, amp_static * 1.2, 11.3, seed + 3.0)
                    P = jitter(P, amp * 1.2, kboil + 0.5, seed + 5.0)
                    segs2.append(_catmull(P, per) * S)
                q2 = _clamp((q - 0.12) / 0.88)
                if q2 > 0:
                    brush(path, segs2, w * 0.45, undraw, ease.in_out_quad(q2), (0.2, 0.3), 0.2, 0.1,
                          seed + 9, ctx.px(5))
        elif kind == "qstar":
            sp = spring(loc_t, freq=3.1, damping=0.42) if draw_on else 1.0
            if undraw > 0:
                sp *= 1.0 - ease.in_back(undraw)
            if sp <= 0.01:
                continue
            ccx, ccy = e["c"]
            base8 = np.array([[ccx + i * 0.37, ccy - i * 0.21] for i in range(8)], float)
            J = jitter(base8, amp * 0.9, kboil, seed + ei) - base8
            tw = 1.0 + 0.08 * math.sin(kboil * 2.3 + ei * 1.7) * inten if lt > tdraw else 1.0
            rx, ry = e["r"]
            quad_star(path, ccx * S, ccy * S, rx * S * sp * tw, ry * S * sp * tw, e["pinch"],
                      rot=(1 - sp) * 0.6 + (rnd - 0.5) * 0.15, jit=J * S)
        elif kind == "dot":
            sp = spring(loc_t, freq=3.3, damping=0.45) if draw_on else 1.0
            if undraw > 0:
                sp *= 1.0 - undraw
            ccx, ccy = e["c"]
            j = jitter(np.array([[ccx, ccy]]), amp, kboil, seed + ei)[0]
            r = e["r"] * S * max(0.0, sp)
            if r > 0.3:
                path.addCircle(float(j[0] * S), float(j[1] * S), float(r), skia.PathDirection.kCW)
        elif kind == "head_fill":
            sp = spring(loc_t, freq=3.2, damping=0.45) if draw_on else 1.0
            if undraw > 0:
                sp *= 1.0 - undraw
            if sp > 0.02:
                end = jitter(e["end"][None], amp_static, -3.7, seed)
                end = jitter(end, amp, kboil, seed)[0] * S
                prev = e["prev"] * S
                d = end - prev
                d = d / max(1e-9, float(np.hypot(*d)))
                nrm = np.array([-d[1], d[0]])
                hl = stroke_px * 2.6 * sp
                tip = end + d * hl * 0.6
                b1 = end - d * hl * 0.5 + nrm * hl * 0.62
                b2 = end - d * hl * 0.5 - nrm * hl * 0.62
                _add_poly(path, np.array([tip, b1, b2]))
        elif kind == "bolt":
            poly = jitter(e["poly"], amp_static, -3.7, seed)
            poly = jitter(poly, amp * 0.8, kboil, seed) * S
            if q < 1.0:
                # rayo que "cae": se revela de arriba hacia abajo
                bolt = skia.Path()
                _add_poly(bolt, poly)
                ymin, ymax = float(poly[:, 1].min()), float(poly[:, 1].max())
                yc = ymin + (ymax - ymin) * ease.out_cubic(q)
                clip = skia.Path()
                clip.addRect(skia.Rect.MakeLTRB(-S * 2, ymin - 5, S * 2, yc))
                res = skia.Op(bolt, clip, skia.PathOp.kIntersect_PathOp)
                if res is not None:
                    path.addPath(res)
            else:
                _add_poly(path, poly)

    shadow = _clamp(_num(item, "shadow", 0.55))
    glow = _clamp(_num(item, "glow", 0.0))
    with group(canvas, cx, cy + idle_dy, st):
        a = st.a
        if sketch and not path.isEmpty():
            _draw_sketch(canvas, path, col, a, shadow, kboil, ctx)
            for segs, prog, u0 in dash_paths:
                _draw_dashes(canvas, segs, prog, u0, stroke_px, col, a, lt, shadow, ctx)
            return
        if shadow > 0 and not path.isEmpty():
            sp = C.fill("#000000", 0.42 * shadow * a)
            sp.setMaskFilter(blur_mask(ctx.px(6)))
            canvas.save()
            canvas.translate(0, ctx.px(3))
            canvas.drawPath(path, sp)
            canvas.restore()
        if glow > 0 and not path.isEmpty():
            gp = skia.Paint(Color=col, AntiAlias=True)
            gp.setAlphaf(0.55 * glow * a)
            gp.setMaskFilter(blur_mask(ctx.px(14)))
            canvas.drawPath(path, gp)
        if not path.isEmpty():
            p = skia.Paint(Color=col, AntiAlias=True)
            p.setAlphaf(a * (skia.ColorGetA(col) / 255.0))
            canvas.drawPath(path, p)
        for segs, prog, u0 in dash_paths:
            _draw_dashes(canvas, segs, prog, u0, stroke_px, col, a, lt, shadow, ctx)


def _draw_sketch(canvas, path: skia.Path, col, a: float, shadow: float, kboil: int, ctx):
    """Estilo 'marcador hueco' del video objetivo: doble contorno aspero + rayado diagonal dentro.
    El pincel se construye como poligonos solapados (union por winding); se fusionan con
    skia.Simplify para que el contorno sea UNO solo (sin aristas internas en cruces ni en la
    union punta-cuerpo de las flechas)."""
    merged = skia.Simplify(path)
    if merged is not None and not merged.isEmpty():
        path = merged
    if shadow > 0:
        sp = C.fill("#000000", 0.25 * shadow * a)
        sp.setMaskFilter(blur_mask(ctx.px(6)))
        canvas.save()
        canvas.translate(0, ctx.px(3))
        canvas.drawPath(path, sp)
        canvas.restore()
    # base translucida del color: el hueco se lee como forma sobre video con mucho detalle
    canvas.drawPath(path, C.fill(col, 0.14 * a))
    b = path.computeTightBounds()
    hatch = skia.Path()
    step = ctx.px(9.0)
    # rayado recto (un DiscretePathEffect por linea cuesta ~4 ms); la fase y la inclinacion
    # cambian con el boil para que el rayado tambien "hierva"
    ph = (int(kboil) * 0.37 % 1.0) * step
    tilt = 1.0 + 0.08 * math.sin(int(kboil) * 1.7)
    x = b.left() - b.height() * tilt + ph
    while x < b.right():
        hatch.moveTo(x, b.bottom())
        hatch.lineTo(x + b.height() * tilt, b.top())
        x += step
    canvas.save()
    canvas.clipPath(path, skia.ClipOp.kIntersect, True)
    canvas.drawPath(hatch, C.stroke(col, ctx.px(2.4), 0.9 * a))
    canvas.restore()
    op = C.stroke(col, ctx.px(3.6), a)
    op.setPathEffect(skia.DiscretePathEffect.Make(ctx.px(9), ctx.px(1.2), int(kboil)))
    canvas.drawPath(path, op)
    op2 = C.stroke(col, ctx.px(2.0), 0.8 * a)
    op2.setPathEffect(skia.DiscretePathEffect.Make(ctx.px(11), ctx.px(1.6), int(kboil) + 7))
    canvas.save()
    canvas.translate(ctx.px(1.6), -ctx.px(1.2))
    canvas.drawPath(path, op2)
    canvas.restore()


def _draw_dashes(canvas, segs, prog, u0, w, col, a, lt, shadow, ctx):
    """Trazo punteado grueso con remates redondos que avanza (marcha) mientras vive."""
    pts = np.vstack(segs)
    sp = skia.Path()
    sp.addPoly([skia.Point(float(x), float(y)) for x, y in pts.tolist()], False)
    dash, gap = w * 1.9, w * 1.55
    phase = -(lt * w * 5.0) % (dash + gap)  # marcha hacia adelante
    eff = compose(skia.DashPathEffect.Make([dash, gap], phase), trim(u0, prog))
    if prog - u0 <= 0.002:
        return
    if shadow > 0:
        ps = C.stroke("#000000", w, 0.4 * shadow * a, cap="round")
        ps.setPathEffect(eff)
        ps.setMaskFilter(blur_mask(ctx.px(5)))
        canvas.save()
        canvas.translate(0, ctx.px(3))
        canvas.drawPath(sp, ps)
        canvas.restore()
    p = C.stroke("#FFFFFF", w, 1.0, cap="round")
    p.setColor(col)
    p.setAlphaf(a)
    p.setPathEffect(eff)
    canvas.drawPath(sp, p)


# ============================================================== iconos (Tabler)


def _squircle(r: skia.Rect, k: float = 0.3) -> skia.RRect:
    rad = min(r.width(), r.height()) * k
    return skia.RRect.MakeRectXY(r, rad, rad)


_SHADOW_CACHE = _LRU(32)


def _shadow_layers(ctx, spread: float, key: float, amb: float):
    return ((ctx.px(5) * spread, ctx.px(8) * spread, key), (ctx.px(18) * spread, ctx.px(30) * spread, amb))


def soft_shadow(canvas: skia.Canvas, rr, ctx, alpha: float = 1.0, key: float = 0.22, amb: float = 0.16,
                spread: float = 1.0, circle: tuple | None = None):
    """Doble sombra suave (key + ambiente) para tarjetas, badges y pastillas.
    Con el canvas rotado skia pierde el camino rapido del blur de rrect (~9 ms en una foto grande):
    en ese caso se usa una imagen de la sombra pre-difuminada a 1/3 (cache) dibujada con la matriz."""
    if alpha <= 0.01:
        return
    layers = _shadow_layers(ctx, spread, key, amb)
    m = canvas.getTotalMatrix()
    rotated = abs(m.getSkewX()) > 1e-4 or abs(m.getSkewY()) > 1e-4
    if rotated and circle is None:
        r = rr.rect()
        rx = rr.getSimpleRadii().x() if rr.isSimple() else 0.0
        margin = max(b for _, b, _ in layers) * 3.0 + max(dy for dy, _, _ in layers)
        sc = 1.0 / 3.0
        ck = (int(r.width()), int(r.height()), int(rx), ctx.W, round(spread, 3), round(key, 3), round(amb, 3))

        def build():
            w = int((r.width() + 2 * margin) * sc) + 2
            h = int((r.height() + 2 * margin) * sc) + 2
            surf = C.offscreen(w, h)
            c = surf.getCanvas()
            c.clear(skia.ColorTRANSPARENT)
            c.scale(sc, sc)
            base = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(margin, margin, r.width(), r.height()), rx, rx)
            for dy, blur, a in layers:
                p = C.fill("#000000", a)
                p.setMaskFilter(blur_mask(blur))
                c.save()
                c.translate(0, dy)
                c.drawRRect(base, p)
                c.restore()
            return surf.makeImageSnapshot()

        img = _SHADOW_CACHE.get_or(ck, build)
        dst = skia.Rect.MakeXYWH(r.left() - margin, r.top() - margin, img.width() / sc, img.height() / sc)
        canvas.drawImageRect(img, dst, C.SAMPLING_FAST, skia.Paint(Alphaf=_clamp(alpha)))
        return
    for dy, blur, a in layers:
        p = C.fill("#000000", a * alpha)
        p.setMaskFilter(blur_mask(blur))
        canvas.save()
        canvas.translate(0, dy)
        if circle is not None:
            canvas.drawCircle(circle[0], circle[1], circle[2], p)
        else:
            canvas.drawRRect(rr, p)
        canvas.restore()


def draw_badge(canvas, shape: str, cx: float, cy: float, d: float, col, ctx, alpha: float = 1.0,
               shadow: bool = True):
    """Badge circle|squircle con degradado sutil de luz superior y borde interior."""
    r = skia.Rect.MakeXYWH(cx - d / 2, cy - d / 2, d, d)
    rr = skia.RRect.MakeOval(r) if shape == "circle" else _squircle(r, 0.3)
    if shadow:
        soft_shadow(canvas, rr, ctx, alpha, key=0.2, amb=0.12, spread=d / ctx.px(150))
    p = C.fill(col, alpha)
    canvas.drawRRect(rr, p)
    hl = skia.Paint(AntiAlias=True)
    hl.setDither(True)  # degradados sin anillos (8 bits)
    hl.setShader(skia.GradientShader.MakeLinear(
        [skia.Point(cx, cy - d / 2), skia.Point(cx, cy + d / 2)],
        [C.color("#FFFFFF", 0.22 * alpha), C.color("#FFFFFF", 0.0), C.color("#000000", 0.08 * alpha)],
        [0.0, 0.55, 1.0]))
    canvas.drawRRect(rr, hl)
    canvas.drawRRect(rr, C.stroke("#FFFFFF", max(1.0, d * 0.012), 0.22 * alpha))


@lru_cache(maxsize=1024)
def resolve_icon(name: str) -> str:
    """Nombre Tabler real: el pedido si existe, si no el mejor resultado de icon_search, si no 'star'."""
    name = str(name or "")
    if name and assets.icon_paths(name, "outline"):
        return name
    found = assets.icon_search(name, 1) if name else []
    if found and assets.icon_paths(found[0], "outline"):
        return found[0]
    return "star"


@lru_cache(maxsize=1024)
def has_filled(name: str) -> bool:
    """True si el icono (ya resuelto) tiene variante filled propia. assets.icon_paths(.., 'filled')
    cae al outline cuando no existe: se compara la geometria serializada."""
    f = assets.icon_paths(name, "filled")
    o = assets.icon_paths(name, "outline")
    if not f:
        return False
    if len(f) != len(o):
        return True
    return any(bytes(a.serialize()) != bytes(b.serialize()) for a, b in zip(f, o))


def icon_paths(name: str, variant: str = "outline") -> tuple:
    return assets.icon_paths(resolve_icon(name), variant)


def paint_icon(canvas, name: str, cx: float, cy: float, box: float, col, alpha: float = 1.0,
               q: float = 1.0, variant: str = "outline", stroke_w: float = 2.0, sketch: int | None = None,
               hatch: bool = False, blur: float = 0.0):
    """Dibuja un icono Tabler centrado en (cx,cy) en una caja de `box` px.
    q: progreso de dibujo 0..1 (cada sub-trazo se dibuja escalonado con TrimPathEffect)."""
    rname = resolve_icon(name)
    ps = assets.icon_paths(rname, variant)
    if not ps or alpha <= 0.003 or q <= 0:
        return
    sc = box / 24.0
    canvas.save()
    canvas.translate(cx - box / 2, cy - box / 2)
    canvas.scale(sc, sc)
    n = len(ps)
    filled = variant == "filled" and has_filled(rname)
    base = C.stroke(col, stroke_w, alpha)
    mf = blur_mask(blur / sc) if blur > 0 else None
    if mf is not None:
        base.setMaskFilter(mf)
    span = 0.45 if n > 1 else 0.0
    for i, pth in enumerate(ps):
        s0 = (i / (n - 1) * span) if n > 1 else 0.0
        qi = ease.in_out_quad(_clamp((q - s0) / (1.0 - span)))
        if qi <= 0:
            continue
        if filled:
            if q >= 1.0:
                fp = C.fill(col, alpha)
                if mf is not None:
                    fp.setMaskFilter(mf)
                canvas.drawPath(pth, fp)
            else:
                pp = trim_paint(C.stroke(col, 1.4, alpha), 0.0, qi)
                canvas.drawPath(pth, pp)
                fa = _clamp((q - 0.7) / 0.3)
                if fa > 0:
                    canvas.drawPath(pth, C.fill(col, alpha * fa))
            continue
        pp = skia.Paint(base)
        eff = trim(0.0, qi)
        if sketch is not None:
            eff = compose(skia.DiscretePathEffect.Make(2.2, 0.32, int(sketch)), eff)
        if eff is not None:
            pp.setPathEffect(eff)
        canvas.drawPath(pth, pp)
        if sketch is not None and mf is None:
            p2 = C.stroke(col, stroke_w * 0.45, alpha * 0.85)
            e2 = compose(skia.DiscretePathEffect.Make(3.0, 0.45, int(sketch) + 17), trim(0.0, qi))
            p2.setPathEffect(e2)
            canvas.save()
            canvas.translate(0.35, -0.25)
            canvas.drawPath(pth, p2)
            canvas.restore()
    if hatch and q >= 0.6 and mf is None:
        # rayado a mano dentro del contorno cerrado mas grande (look de marcador)
        closed = list(ps)
        if closed:
            big = max(closed, key=lambda p: p.computeTightBounds().width() * p.computeTightBounds().height())
            b = big.computeTightBounds()
            ha = alpha * _clamp((q - 0.6) / 0.4) * 0.8
            canvas.save()
            canvas.clipPath(big, skia.ClipOp.kIntersect, True)
            hp = C.stroke(col, 0.55, ha)
            if sketch is not None:
                hp.setPathEffect(skia.DiscretePathEffect.Make(1.5, 0.25, int(sketch) + 5))
            x = b.left() - b.height()
            while x < b.right():
                canvas.drawLine(x, b.bottom(), x + b.height(), b.top(), hp)
                x += 1.35
            canvas.restore()
    canvas.restore()


def draw_icon(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    item = sanitize(item)
    name = item.get("name") or "star"
    variant = item.get("variant") or "outline"
    badge = item.get("badge") or "none"
    inten = _clamp(_num(item, "intensity", 1.0))
    size = _num(item, "size", 0.16) * ctx.W
    c = center_of(item, ctx, XC, 0.4)
    if c is None:
        return
    cx, cy = c
    pal = ctx.palette
    style = item.get("style") or "clean"
    draw_on = bool(_g(item, "draw", True))
    anim_in = item.get("anim_in") or "pop"
    anim_out = item.get("anim_out") or "pop_out"
    tin = _num(item, "in_dur", 0.45)
    tout = _num(item, "out_dur", 0.22)
    _, pout = ease.envelope(lt, dur, tin, tout)
    st = enter_state(anim_in, lt, tin, ctx, inten) * exit_state(anim_out, pout, ctx)
    st.r += _num(item, "rotation", 0.0)
    # flotacion sutil
    st.dy += math.sin(lt * TAU * 0.45) * ctx.px(3) * inten
    st.r += math.sin(lt * TAU * 0.3 + 1.0) * 1.2 * inten
    if not _visible(st):
        return
    ipop = 1.0
    if not draw_on:
        q = 1.0
    elif badge != "none":
        # el icono empieza a trazarse cuando el badge ya casi se asento (no puntos sueltos
        # sobre un badge que sigue rebotando); iconos chicos entran con pop de escala
        t0 = _spring_reach(0.85, 2.9, 0.42 + (1.0 - inten) * 0.3) if anim_in == "pop" else 0.1
        q, ipop = _badge_icon_q(lt, t0, size * 0.56, ctx)
    else:
        q = _clamp(lt / 0.55)
    if anim_out == "undraw" and pout > 0:
        q = min(q, 1.0 - pout)
    kboil = round(ease.hold_steps(lt, 12.0) * 12.0)  # boil a 12 fps (animacion a doces)
    with group(canvas, cx, cy, st):
        a = st.a
        if badge != "none":
            bcol = item.get("badge_color") or pal["accent"]
            draw_badge(canvas, badge, 0.0, 0.0, size, bcol, ctx, a)
            icol = item.get("color") or ("#15151A" if C.luminance(bcol) > 0.55 else "#FFFFFF")
            if C.luminance(icol) > 0.8 and C.luminance(bcol) > 0.7:  # blanco sobre badge claro
                icol = "#15151A"
            box = size * 0.56
            sw = 2.0
            if ipop < 0.999:
                canvas.scale(max(0.0, ipop), max(0.0, ipop))
        else:
            icol = item.get("color") or pal["primary"]
            box = size
            sw = 2.0 if style == "clean" else 1.45
            shd = _num(item, "shadow", 0.6)
            if shd > 0:
                # sombra difusa del trazo para legibilidad sobre fondos claros
                canvas.save()
                canvas.translate(0, ctx.px(3))
                paint_icon(canvas, name, 0.0, 0.0, box, "#000000", 0.45 * a * shd, q, variant, sw + 0.8,
                           blur=ctx.px(5))
                canvas.restore()
        sketch = int(kboil) if style == "sketch" else None
        paint_icon(canvas, name, 0.0, 0.0, box, icol, a, q, variant, sw, sketch,
                   hatch=bool(_g(item, "hatch", style == "sketch")))


# ============================================================== tarjetas (card)

CARD_VARIANTS = ("price", "location", "schedule", "link", "phone", "rating", "checklist", "stat", "generic")
CARD_ICON = {"price": "cash", "location": "map-pin", "schedule": "calendar-event", "link": "link",
             "phone": "phone", "rating": "star", "checklist": "list-check", "stat": "trending-up",
             "generic": "info-circle"}


def card_theme(item: dict, ctx) -> dict:
    pal = ctx.palette
    acc = item.get("accent") or pal["accent"]
    th = item.get("theme") or "light"
    on_acc = "#141418" if C.luminance(acc) > 0.55 else "#FFFFFF"
    if th == "dark":
        return {"bg": "#16161C", "bg_a": 0.94, "fg": "#FFFFFF", "sub": "#A9AAB8", "line": ("#FFFFFF", 0.09),
                "chip": ("#FFFFFF", 0.08), "accent": acc, "on_accent": on_acc, "badge": acc,
                "on_badge": on_acc, "border": ("#FFFFFF", 0.08), "map": "#23252D", "map_st": "#343844",
                "map_park": "#26402F", "ink": "#FFFFFF", "name": "dark"}
    if th == "brand":
        bg = item.get("bg") or pal.get("accent2") or acc  # 'color' no: el esquema lo rellena con blanco
        fg = "#141418" if C.luminance(bg) > 0.55 else "#FFFFFF"
        badge = "#FFFFFF" if fg == "#141418" else "#141418"
        badge = "#FFFFFF"
        return {"bg": bg, "bg_a": 1.0, "fg": fg, "sub": fg, "sub_a": 0.72, "line": (fg, 0.14),
                "chip": (fg, 0.12), "accent": "#FFFFFF" if fg == "#141418" else acc,
                "on_accent": "#141418", "badge": badge, "on_badge": "#141418", "border": ("#FFFFFF", 0.18),
                "map": C.mix(bg, "#FFFFFF", 0.55), "map_st": "#FFFFFF", "map_park": C.mix(bg, "#FFFFFF", 0.3),
                "ink": fg, "name": "brand"}
    return {"bg": "#FFFFFF", "bg_a": 1.0, "fg": "#15151B", "sub": "#6C6D7A", "line": ("#000000", 0.07),
            "chip": ("#000000", 0.05), "accent": acc, "on_accent": on_acc, "badge": acc, "on_badge": on_acc,
            "border": ("#FFFFFF", 0.6), "map": "#EEF0EA", "map_st": "#FFFFFF", "map_park": "#D5EACB",
            "ink": "#15151B", "name": "light"}


def _col_a(spec):
    """('#hex', alpha) | '#hex' -> (color, alpha)."""
    if isinstance(spec, tuple):
        return spec
    return spec, 1.0


def parse_amount(s: str) -> tuple[str, float, str, int, str]:
    """'$1,000 MXN' -> ('$', 1000.0, ' MXN', decimales, separador de miles)."""
    s = str(s or "")
    m = re.search(r"(\d[\d.,]*)", s)
    if not m:
        return s, 0.0, "", 0, ","
    num = m.group(1)
    pre, suf = s[:m.start()], s[m.end():]
    sep = ","
    dec = 0
    if re.search(r"[.,]\d{1,2}$", num) and not re.search(r"[.,]\d{3}$", num):
        dec = len(num) - max(num.rfind("."), num.rfind(",")) - 1
        dsep = num[-dec - 1]
        sep = "." if dsep == "," else ","
        body = num[:-dec - 1].replace(",", "").replace(".", "")
        val = float(body + "." + num[-dec:])
    else:
        if "." in num and "," not in num and re.fullmatch(r"\d{1,3}(\.\d{3})+", num):
            sep = "."
        val = float(num.replace(",", "").replace(".", ""))
    return pre, val, suf, dec, sep


def fmt_amount(v: float, dec: int, sep: str) -> str:
    s = f"{v:,.{dec}f}"
    if sep == ".":
        s = s.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")
    return s


class _Row:
    __slots__ = ("h", "fn", "gap")

    def __init__(self, h: float, fn, gap: float = 0.0):
        self.h, self.fn, self.gap = h, fn, gap


def _draw_digits(canvas, s: str, x: float, y: float, fnt: skia.Font, col, a: float) -> float:
    """Numeros en celdas de ancho fijo (el contador no 'baila')."""
    cw = max(fnt.measureText(d) for d in "0123456789")
    p = C.fill(col, a)
    for ch in s:
        if ch.isdigit():
            w = fnt.measureText(ch)
            canvas.drawString(ch, x + (cw - w) / 2, y, fnt, p)
            x += cw
        else:
            canvas.drawString(ch, x, y, fnt, p)
            x += fnt.measureText(ch)
    return x


def _digits_w(s: str, fnt: skia.Font) -> float:
    cw = max(fnt.measureText(d) for d in "0123456789")
    return sum(cw if ch.isdigit() else fnt.measureText(ch) for ch in s)


def _row_header(item, th, F, u, inner_w, ctx, icon: str, title: str, subtitle: str, badge_fn=None,
                badge_scale: float = 1.0, title_px: float = 5.6):
    bs = 15.0 * u * badge_scale
    gap = 4.2 * u
    tw = inner_w - bs - gap
    ft = font(F["title"], title_px * u)
    fs = font(F["body"], 3.9 * u)
    tl = wrap(ft, title, tw, 2) if title else []
    sl = wrap(fs, subtitle, tw, 2) if subtitle else []
    lh_t, lh_s = title_px * u * 1.18, 3.9 * u * 1.35
    th_txt = len(tl) * lh_t + (len(sl) * lh_s + (1.2 * u if sl else 0))
    h = max(bs, th_txt)
    sub_col, sub_a = th["sub"], th.get("sub_a", 1.0)
    t_icon = _spring_reach(0.85, 2.8, 0.5)

    def fn(cv, x, y, w, t, a):
        sp = spring(t, freq=2.8, damping=0.5)
        bcx, bcy = x + bs / 2, y + h / 2
        if sp > 0.01:
            cv.save()
            cv.translate(bcx, bcy)
            cv.scale(sp, sp)
            draw_badge(cv, "squircle", 0, 0, bs, th["badge"], ctx, a)
            if badge_fn is not None:
                badge_fn(cv, 0.0, 0.0, bs, t, a)
            else:
                q, ip = _badge_icon_q(t, t_icon, bs * 0.56, ctx)
                if q > 0 and ip > 0.01:
                    cv.scale(ip, ip)
                    paint_icon(cv, icon, 0, 0, bs * 0.56, th["on_badge"], a, q)
            cv.restore()
        tx = x + bs + gap
        ty = y + (h - th_txt) / 2
        for i, ln in enumerate(tl):
            e = ease.out_cubic(_clamp((t - 0.05 - i * 0.05) / 0.35))
            draw_text(cv, ln, tx + (1 - e) * 3 * u, ty + lh_t * (i + 0.82), ft, th["fg"], a * e)
        ty += len(tl) * lh_t + (1.2 * u if sl else 0)
        for i, ln in enumerate(sl):
            e = ease.out_cubic(_clamp((t - 0.14 - i * 0.05) / 0.35))
            draw_text(cv, ln, tx + (1 - e) * 3 * u, ty + lh_s * (i + 0.78), fs, sub_col, a * e * sub_a)

    return _Row(h, fn)


def _row_value(item, th, F, u, inner_w, ctx, value: str, counter: bool = True, big: float = 16.0,
               marker: bool = True):
    """Cifra grande. counter=True: cuenta desde 0 con digitos en celdas fijas; el grupo
    prefijo+cifra se alinea a la derecha dentro del ancho FINAL, asi el sufijo ('MXN') queda
    quieto en su lugar desde el primer cuadro (no salta al pasar de 999 a 1,000).
    counter=False: texto tal cual (telefonos, etiquetas)."""
    pre, val, suf, dec, sep = parse_amount(value)
    has_num = counter and bool(re.search(r"\d", str(value)))
    pre_s, suf_s = pre.strip(), suf.strip()
    long_pre = len(pre_s) > 2  # 'Desde $' va en linea base (en superindice se ve raro)
    k = 1.0
    for _ in range(3):
        fbig = font(F["display"], big * u * k)
        fsuf = font(F["label"], big * 0.3 * u * k)
        fpre = fsuf if long_pre else font(F["display"], big * 0.56 * u * k)
        final = fmt_amount(val, dec, sep) if has_num else str(value)
        wnum = _digits_w(final, fbig) if has_num else fbig.measureText(final)
        wpre = fpre.measureText(pre_s) if has_num and pre_s else 0
        gpre = ((1.6 if long_pre else 1.0) * u) if wpre else 0
        wsuf = fsuf.measureText(suf_s) if has_num and suf_s else 0
        gsuf = 2.0 * u if wsuf else 0
        total = wpre + gpre + wnum + gsuf + wsuf
        if total <= inner_w:
            break
        k *= inner_w / total
    if not has_num:
        final = ellipsize(fbig, final, inner_w)
        wnum = fbig.measureText(final)
        total = wnum
    m = fbig.getMetrics()
    cap = -m.fAscent * 0.72
    h = cap + 2.4 * u
    under = th["name"] == "dark"  # en oscuro: barra debajo (texto blanco sobre amarillo no se lee)
    on_marker = marker and not under
    suf_col = th["fg"] if on_marker else th["sub"]
    suf_a = 0.75 if on_marker else th.get("sub_a", 1.0)

    def fn(cv, x, y, w, t, a):
        base = y + cap + 0.6 * u
        pc = ease.out_expo(_clamp(t / 0.95)) if has_num else 1.0
        s = fmt_amount(round(val * pc, dec), dec, sep) if has_num else final
        sw = _digits_w(s, fbig) if has_num else wnum
        if marker:
            mp = ease.out_cubic(_clamp((t - 0.55) / 0.4))
            if mp > 0:
                if under:
                    mr = skia.Rect.MakeLTRB(x, base + 1.4 * u, x + total * mp, base + 2.6 * u)
                    cv.drawRoundRect(mr, 0.6 * u, 0.6 * u, C.fill(th["accent"], a))
                else:
                    mr = skia.Rect.MakeLTRB(x - 1.2 * u, base - cap * 0.5, x - 1.2 * u + (total + 2.4 * u) * mp,
                                            base + 1.3 * u)
                    cv.drawRoundRect(mr, 1.4 * u, 1.4 * u, C.fill(th["accent"], a * 0.92))
        if not has_num:
            draw_text(cv, final, x, base, fbig, th["fg"], a)
            return
        right = x + wpre + gpre + wnum  # borde derecho fijo de la cifra final
        xn = right - sw
        if wpre:
            if long_pre:
                draw_text(cv, pre_s, xn - gpre - wpre, base, fpre, th["fg"], a)
            else:
                draw_text(cv, pre_s, xn - gpre - wpre, base - cap + (-fpre.getMetrics().fAscent * 0.72),
                          fpre, th["fg"], a)
        _draw_digits(cv, s, xn, base, fbig, th["fg"], a)
        if wsuf:
            e = ease.out_cubic(_clamp((t - 0.3) / 0.4))
            draw_text(cv, suf_s, right + gsuf, base, fsuf, suf_col, a * e * suf_a)

    return _Row(h, fn)


def _row_items(item, th, F, u, inner_w, ctx, items: list, check_every: float = 0.22):
    fs = font(F["body"], 4.5 * u)
    lh = 8.8 * u
    rows = [(wrap(fs, str(s), inner_w - 10 * u, 1) or [""])[0] for s in items[:6]]
    h = len(rows) * lh

    def fn(cv, x, y, w, t, a):
        for i, s in enumerate(rows):
            ti = t - i * check_every
            e = ease.out_cubic(_clamp(ti / 0.3))
            if e <= 0:
                continue
            cy = y + lh * (i + 0.5)
            r = 2.9 * u
            cxx = x + r
            sp = spring(ti - 0.1, 3.0, 0.5)
            cv.drawCircle(cxx, cy, r * max(0.0, sp), C.fill(th["accent"], a))
            ck = _clamp((ti - 0.18) / 0.25)
            if ck > 0:
                pth = skia.Path()
                pth.moveTo(cxx - r * 0.45, cy + r * 0.02)
                pth.lineTo(cxx - r * 0.1, cy + r * 0.38)
                pth.lineTo(cxx + r * 0.5, cy - r * 0.34)
                cv.drawPath(pth, trim_paint(C.stroke(th["on_accent"], r * 0.26, a), 0, ease.out_cubic(ck)))
            draw_text(cv, s, x + 2 * r + 3.2 * u + (1 - e) * 2 * u, cy + 1.5 * u, fs, th["fg"], a * e)

    return _Row(h, fn)


def _row_divider(th, u):
    def fn(cv, x, y, w, t, a):
        e = ease.out_expo(_clamp(t / 0.5))
        c, al = _col_a(th["line"])
        cv.drawRect(skia.Rect.MakeXYWH(x, y + 0.6 * u, w * e, max(1.0, 0.28 * u)), C.fill(c, al * a))
    return _Row(1.6 * u, fn)


def _landmark_label(item: dict) -> str:
    if item.get("map_label"):
        return str(item["map_label"])
    sub = str(item.get("subtitle") or "")
    m = re.search(r"(Glorieta\s+[A-Z][\w\u00c0-\u017f]+|Parque\s+[A-Z][\w\u00c0-\u017f]+|Plaza\s+[A-Z][\w\u00c0-\u017f]+)", sub)
    if m:
        return m.group(1)
    m = re.search(r"\bde(?:l| la| los| las)?\s+(.+)$", sub)
    return m.group(1).strip()[:26] if m else ""


def _map_picture(key, w, h, th, rnd, ctx, label: str, u: float):
    # rnd viene de ctx.rand(...) y rng de ctx.rng(...) (determinista por item y seed)
    """Mini mapa procedural (calles en reticula girada, avenida, glorieta y parque)."""

    def build():
        rec = skia.PictureRecorder()
        cv = rec.beginRecording(skia.Rect.MakeWH(w, h))
        cv.drawRect(skia.Rect.MakeWH(w, h), C.fill(th["map"]))
        rng = ctx.rng(f"map:{key}")
        ang = -16.0 + rnd * 8
        cv.save()
        cv.translate(w / 2, h / 2)
        cv.rotate(ang)
        span = max(w, h) * 1.5
        step = w * 0.16
        # manzanas con parque
        pk = skia.Rect.MakeXYWH(-step * 2.0 + 0.1 * step, -step * 1.0 + 0.1 * step, step * 0.8, step * 0.8)
        cv.drawRoundRect(pk, step * 0.12, step * 0.12, C.fill(th["map_park"]))
        pk2 = skia.Rect.MakeXYWH(step * 1.1, step * 0.1, step * 0.8, step * 0.8)
        cv.drawRoundRect(pk2, step * 0.12, step * 0.12, C.fill(th["map_park"], 0.7))
        stc = th["map_st"]
        minor = C.stroke(stc, max(1.0, w * 0.018), 1.0, cap="butt")
        for i in range(-6, 7):
            off = i * step + float(rng.uniform(-0.08, 0.08)) * step
            cv.drawLine(off, -span, off, span, minor)
            cv.drawLine(-span, off, span, off, minor)
        # avenida principal (mas ancha, con borde)
        av = C.stroke(C.mix(stc, "#000000", 0.08) if th["name"] == "light" else stc, w * 0.05, 1.0, cap="butt")
        cv.drawLine(-span, step * 0.0, span, step * 0.0, av)
        cv.drawLine(-span, step * 0.0, span, step * 0.0,
                    C.stroke(th["accent"], max(1.0, w * 0.008), 0.55, cap="butt"))
        cv.restore()
        return rec.finishRecordingAsPicture()

    return _PIC_CACHE.get_or(("map", key, ctx.seed, int(w), int(h), th["name"], th["map"], str(th["map_st"]),
                              str(th["map_park"]), str(th["accent"])), build)


def _row_map(item, th, F, u, inner_w, ctx):
    mh = inner_w * 0.46
    # sin id, la clave del mapa sale del contenido (dos tarjetas sin id no comparten mapa)
    mkey = item.get("id") or repr(sorted((k, str(v)) for k, v in item.items() if not str(k).startswith("_")))
    rnd = ctx.rand(f"map:{mkey}")
    fl = font(F["label"], 2.9 * u)
    label = ellipsize(fl, _landmark_label(item), inner_w * 0.86)

    def fn(cv, x, y, w, t, a):
        rr = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, mh), 3.6 * u, 3.6 * u)
        cv.save()
        cv.clipRRect(rr, skia.ClipOp.kIntersect, True)
        cv.translate(x, y)
        if a < 0.999:
            cv.saveLayerAlpha(skia.Rect.MakeWH(w, mh), int(a * 255))
        # zoom lento del mapa (vida)
        z = 1.0 + 0.04 * ease.out_cubic(_clamp(t / 2.5))
        cv.save()
        cv.translate(w / 2, mh / 2)
        cv.scale(z, z)
        cv.translate(-w / 2, -mh / 2)
        cv.drawPicture(_map_picture(mkey, w, mh, th, rnd, ctx, label, u))
        # glorieta y destino (en coordenadas del mapa sin girar)
        gx, gy = w * 0.26, mh * 0.66
        px_, py_ = w * 0.70, mh * 0.40
        rg = w * 0.07
        cv.drawCircle(gx, gy, rg, C.stroke(th["map_st"], w * 0.03, 1.0))
        cv.drawCircle(gx, gy, rg * 0.62, C.fill(th["map_park"]))
        cv.drawCircle(gx, gy, rg * 1.16, C.stroke(C.mix(th["map_st"], "#000000", 0.1), max(1.0, w * 0.004), 0.5))
        # ruta punteada (2 cuadras) que se dibuja
        rp = ease.in_out_cubic(_clamp((t - 0.55) / 0.8))
        if rp > 0:
            route = skia.Path()
            route.moveTo(gx + rg * 1.1, gy)
            route.cubicTo(gx + (px_ - gx) * 0.5, gy + mh * 0.02, px_ - w * 0.05, py_ + mh * 0.22, px_, py_ + mh * 0.06)
            rpn = C.stroke(th["accent"] if th["name"] != "brand" else "#141418", w * 0.018, 1.0, cap="round")
            rpn.setPathEffect(compose(skia.DashPathEffect.Make([w * 0.004, w * 0.034], -t * w * 0.06),
                                      trim(0.0, rp)))
            cv.drawPath(route, rpn)
        # etiqueta del punto de referencia
        if label:
            le = ease.out_cubic(_clamp((t - 0.35) / 0.4))
            if le > 0:
                lw = fl.measureText(label) + 3.4 * u
                lr = skia.Rect.MakeXYWH(gx - lw / 2, gy + rg * 1.35 + (1 - le) * u, lw, 5.2 * u)
                if lr.bottom() > mh - 1.5 * u:  # no cabe abajo: arriba de la glorieta
                    lr = skia.Rect.MakeXYWH(gx - lw / 2, gy - rg * 1.35 - 5.2 * u - (1 - le) * u, lw, 5.2 * u)
                if lr.right() > w - 1.5 * u:
                    lr.offset(w - 1.5 * u - lr.right(), 0)
                if lr.left() < 1.5 * u:
                    lr.offset(1.5 * u - lr.left(), 0)
                cv.drawRoundRect(lr, 2.6 * u, 2.6 * u, C.fill("#FFFFFF" if th["name"] != "dark" else "#0E0E12", 0.92 * le))
                draw_text(cv, label, lr.centerX(), lr.top() + 3.6 * u, fl,
                          "#3A3B44" if th["name"] != "dark" else "#E8E8EE", le, align="center")
        # pin que cae con rebote + onda en el suelo
        dp = _clamp((t - 0.2) / 0.7)
        if dp > 0:
            drop = ease.out_bounce(dp)
            pin_h = mh * 0.36
            yoff = -(1 - drop) * mh * 0.9
            shade = 0.25 + 0.75 * drop
            cv.drawOval(skia.Rect.MakeXYWH(px_ - pin_h * 0.2 * shade, py_ - pin_h * 0.05, pin_h * 0.4 * shade, pin_h * 0.1),
                        C.fill("#000000", 0.22 * shade))
            if dp >= 1.0:
                for k in range(2):
                    rt = ((t - 0.9 - k * 0.6) % 1.2) / 1.2
                    if t - 0.9 - k * 0.6 > 0:
                        cv.drawOval(skia.Rect.MakeXYWH(px_ - pin_h * 0.6 * rt, py_ - pin_h * 0.16 * rt,
                                                       pin_h * 1.2 * rt, pin_h * 0.32 * rt),
                                    C.stroke(th["accent"], max(1.0, 0.5 * u), (1 - rt) * 0.8))
            _pin(cv, px_, py_ + yoff, pin_h, "#FF4D5E" if th["name"] != "brand" else "#141418")
        cv.restore()
        if a < 0.999:
            cv.restore()
        cv.restore()

    return _Row(mh, fn)


def _pin(cv, x, y, h, col):
    """Pin de mapa: gota con punta en (x,y), circulo interior blanco y sombra."""
    r = h * 0.36
    cyc = y - h + r
    p = skia.Path()
    p.moveTo(x, y)
    p.cubicTo(x - r * 0.35, y - h * 0.28, x - r, cyc + r * 0.55, x - r, cyc)
    p.arcTo(skia.Rect.MakeLTRB(x - r, cyc - r, x + r, cyc + r), 180, 180, False)
    p.cubicTo(x + r, cyc + r * 0.55, x + r * 0.35, y - h * 0.28, x, y)
    p.close()
    sp = C.fill("#000000", 0.28)
    sp.setMaskFilter(blur_mask(h * 0.06))
    cv.save()
    cv.translate(0, h * 0.04)
    cv.drawPath(p, sp)
    cv.restore()
    cv.drawPath(p, C.fill(col))
    hl = skia.Paint(AntiAlias=True)
    hl.setDither(True)  # degradados sin anillos (8 bits)
    hl.setShader(skia.GradientShader.MakeLinear([skia.Point(x - r, cyc - r), skia.Point(x + r, y)],
                                                [C.color("#FFFFFF", 0.28), C.color("#FFFFFF", 0.0)]))
    cv.drawPath(p, hl)
    cv.drawCircle(x, cyc, r * 0.42, C.fill("#FFFFFF"))


def _cal_clock_badge(th, u):
    """Ilustracion de badge para horarios: calendario con reloj cuyas manecillas giran."""

    def fn(cv, cx, cy, bs, t, a):
        s = bs * 0.62
        r = skia.Rect.MakeXYWH(cx - s / 2 - s * 0.06, cy - s / 2, s, s * 0.92)
        on = th["on_badge"]
        cv.drawRoundRect(r, s * 0.14, s * 0.14, C.stroke(on, s * 0.075, a))
        cv.drawRect(skia.Rect.MakeLTRB(r.left(), r.top() + s * 0.24, r.right(), r.top() + s * 0.24 + s * 0.06),
                    C.fill(on, a))
        for k in (0.3, 0.7):
            xx = r.left() + r.width() * k
            cv.drawLine(xx, r.top() - s * 0.1, xx, r.top() + s * 0.1, C.stroke(on, s * 0.075, a))
        # puntos de dias que aparecen
        for i in range(6):
            e = _clamp((t - 0.15 - i * 0.05) / 0.2)
            if e <= 0:
                continue
            xx = r.left() + r.width() * (0.22 + (i % 3) * 0.24)
            yy = r.top() + s * (0.48 + (i // 3) * 0.22)
            cv.drawCircle(xx, yy, s * 0.045 * ease.out_back(e), C.fill(on, a))
        # reloj
        cr = s * 0.3
        ccx, ccy = r.right() - cr * 0.15, r.bottom() - cr * 0.15
        cv.drawCircle(ccx, ccy, cr * 1.18, C.fill(th["badge"], a))
        cv.drawCircle(ccx, ccy, cr, C.fill("#FFFFFF" if th["name"] != "dark" else "#FFFFFF", a))
        cv.drawCircle(ccx, ccy, cr, C.stroke(on, s * 0.06, a))
        spin = ease.out_cubic(_clamp((t - 0.1) / 1.1))
        am = -math.pi / 2 + spin * TAU * 1.0 + math.radians(30)
        ah = -math.pi / 2 + spin * TAU / 12 + math.radians(120)
        cv.drawLine(ccx, ccy, ccx + math.cos(am) * cr * 0.72, ccy + math.sin(am) * cr * 0.72,
                    C.stroke("#15151B", s * 0.05, a))
        cv.drawLine(ccx, ccy, ccx + math.cos(ah) * cr * 0.46, ccy + math.sin(ah) * cr * 0.46,
                    C.stroke("#15151B", s * 0.06, a))

    return fn


def _row_chips(th, F, u, inner_w, labels: list, active: list | None = None, delay: float = 0.07):
    fs = font(F["label"], 3.6 * u)
    padx, ch = 3.2 * u, 7.6 * u
    gap = 1.8 * u
    lines = [[]]
    x = 0.0
    # un chip nunca es mas ancho que la tarjeta: la etiqueta se recorta con elipsis
    labels = [ellipsize(fs, str(s), inner_w - 2 * padx) for s in labels]
    widths = [min(inner_w, fs.measureText(s) + 2 * padx) for s in labels]
    for i, w in enumerate(widths):
        if x + w > inner_w and lines[-1]:
            lines.append([])
            x = 0.0
        lines[-1].append((i, x, w))
        x += w + gap
    h = len(lines) * ch + (len(lines) - 1) * gap

    def fn(cv, x0, y0, W_, t, a):
        for li, ln in enumerate(lines):
            for (i, xx, w) in ln:
                e = spring(t - i * delay, 3.0, 0.55)
                if e <= 0.01:
                    continue
                on = active is None or i in active
                r = skia.Rect.MakeXYWH(x0 + xx, y0 + li * (ch + gap), w, ch)
                cv.save()
                cv.translate(r.centerX(), r.centerY())
                cv.scale(e, e)
                cv.translate(-r.centerX(), -r.centerY())
                if on:
                    cv.drawRoundRect(r, ch / 2, ch / 2, C.fill(th["accent"], a))
                    tc = th["on_accent"]
                else:
                    c, al = _col_a(th["chip"])
                    cv.drawRoundRect(r, ch / 2, ch / 2, C.fill(c, al * a))
                    tc = th["sub"]
                draw_text(cv, str(labels[i]), r.centerX(), r.centerY() + 1.25 * u, fs, tc, a, align="center")
                cv.restore()

    return _Row(h, fn)


def _row_button(item, th, F, u, inner_w, ctx, label: str, icon: str = "arrow-right", press_at: float = 1.05,
                cursor: bool = True):
    bh = 12.5 * u
    fb = fit_font(F["title"], label, 4.6 * u, inner_w - 22 * u, min_px=3.4 * u)
    label = ellipsize(fb, label, inner_w - 22 * u)

    def fn(cv, x, y, w, t, a):
        e = spring(t, 2.6, 0.6)
        pr = t - press_at
        press = 1.0 - 0.06 * math.exp(-((pr - 0.06) / 0.07) ** 2) if cursor else 1.0
        r = skia.Rect.MakeXYWH(x, y, w, bh)
        cv.save()
        cv.translate(r.centerX(), r.centerY())
        cv.scale(press * (0.92 + 0.08 * e), press * (0.92 + 0.08 * e))
        cv.translate(-r.centerX(), -r.centerY())
        cv.drawRoundRect(r, bh / 2, bh / 2, C.fill(th["accent"], a * _clamp(e * 2)))
        # brillo que barre
        sh = ((t - 0.4) % 2.2) / 0.7
        if 0 < sh < 1:
            cv.save()
            cv.clipRRect(skia.RRect.MakeRectXY(r, bh / 2, bh / 2), skia.ClipOp.kIntersect, True)
            sx = r.left() - w * 0.3 + (w * 1.6) * ease.in_out_quad(sh)
            gp = skia.Paint(AntiAlias=True)
            gp.setDither(True)  # degradados sin anillos (8 bits)
            gp.setShader(skia.GradientShader.MakeLinear(
                [skia.Point(sx - w * 0.12, 0), skia.Point(sx + w * 0.12, 0)],
                [C.color("#FFFFFF", 0.0), C.color("#FFFFFF", 0.45 * a), C.color("#FFFFFF", 0.0)]))
            cv.drawRect(r, gp)
            cv.restore()
        tw_ = fb.measureText(label)
        ib = 5.2 * u
        tot = tw_ + 2.0 * u + ib
        tx = r.centerX() - tot / 2
        draw_text(cv, label, tx, r.centerY() + fb.getSize() * 0.36, fb, th["on_accent"], a)
        nudge = math.sin(max(0.0, t - 0.6) * TAU * 1.2) * 0.8 * u
        paint_icon(cv, icon, tx + tw_ + 2.0 * u + ib / 2 + nudge, r.centerY(), ib, th["on_accent"], a,
                   _clamp((t - 0.25) / 0.4), stroke_w=2.4)
        cv.restore()
        if cursor:
            _cursor_tap(cv, r.centerX() + w * 0.18, r.centerY() + bh * 0.25, bh * 0.95, t, press_at, a, ctx)

    return _Row(bh, fn)


def _cursor_tap(cv, tx, ty, size, t, press_at, a, ctx):
    """Cursor (flecha) que llega, hace clic con onda y se retira."""
    t0 = press_at - 0.55
    if t < t0:
        return
    arr = ease.out_cubic(_clamp((t - t0) / 0.5))
    leave = ease.in_cubic(_clamp((t - press_at - 0.45) / 0.35))
    x = tx + (1 - arr) * size * 2.2 + leave * size * 0.8
    y = ty + (1 - arr) * size * 1.6 + leave * size * 0.9
    al = a * _clamp((t - t0) / 0.15) * (1 - leave)
    pr = t - press_at
    if 0 <= pr <= 0.5:
        rp = ease.out_cubic(pr / 0.5)
        cv.drawCircle(tx, ty, size * (0.15 + 0.55 * rp), C.stroke("#FFFFFF", size * 0.06, 0.8 * (1 - rp) * a))
    sc = 1.0 - 0.14 * math.exp(-((pr - 0.05) / 0.07) ** 2)
    if al <= 0.01:
        return
    cv.save()
    cv.translate(x, y)
    cv.scale(sc, sc)
    ps = icon_paths("pointer", "filled")
    s = size / 24.0
    cv.scale(s, s)
    cv.translate(-4.2, -4.2)
    sp = C.fill("#000000", 0.35 * al)
    sp.setMaskFilter(blur_mask(1.2))
    for p in ps:
        cv.save()
        cv.translate(0.5, 1.0)
        cv.drawPath(p, sp)
        cv.restore()
        cv.drawPath(p, C.fill("#FFFFFF", al))
        cv.drawPath(p, C.stroke("#15151B", 1.3, al))
    cv.restore()


def _row_stars(th, F, u, inner_w, value: float, count: int = 5):
    ss = 9.0 * u
    gap = 1.6 * u

    def star(cx, cy, r):
        p = skia.Path()
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rr = r if i % 2 == 0 else r * 0.46
            x, y = cx + math.cos(ang) * rr, cy + math.sin(ang) * rr
            p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
        p.close()
        return p

    def fn(cv, x, y, w, t, a):
        for i in range(count):
            e = spring(t - 0.1 - i * 0.09, 3.0, 0.45)
            if e <= 0.01:
                continue
            cx = x + ss / 2 + i * (ss + gap)
            cy = y + ss / 2
            p = star(0, 0, ss * 0.52)
            cv.save()
            cv.translate(cx, cy)
            cv.scale(e, e)
            cv.rotate((1 - e) * 40)
            c, al = _col_a(th["chip"])
            cv.drawPath(p, C.fill(c, al * 2.2 * a))
            fillk = _clamp(value - i)
            if fillk > 0:
                cv.save()
                cv.clipRect(skia.Rect.MakeLTRB(-ss, -ss, -ss * 0.52 + ss * 1.04 * fillk, ss))
                cv.drawPath(p, C.fill(th["accent"], a))
                cv.restore()
            cv.restore()

    return _Row(ss, fn)


def _row_bar(th, u, frac: float):
    bh = 2.6 * u

    def fn(cv, x, y, w, t, a):
        c, al = _col_a(th["chip"])
        cv.drawRoundRect(skia.Rect.MakeXYWH(x, y, w, bh), bh / 2, bh / 2, C.fill(c, al * 2 * a))
        e = ease.out_expo(_clamp((t - 0.1) / 1.1)) * _clamp(frac)
        if e > 0:
            cv.drawRoundRect(skia.Rect.MakeXYWH(x, y, w * e, bh), bh / 2, bh / 2, C.fill(th["accent"], a))

    return _Row(bh, fn)


def _card_rows(item, th, F, u, inner_w, ctx) -> list[_Row]:
    v = item.get("variant") or "generic"
    if v not in CARD_VARIANTS:
        v = "generic"
    title = str(item.get("title") or "")
    sub = str(item.get("subtitle") or "")
    value = item.get("value")
    items = item.get("items") or []
    icon = item.get("icon") or CARD_ICON[v]
    rows: list[_Row] = []
    if v == "price":
        rows.append(_row_header(item, th, F, u, inner_w, ctx, icon, title or "Precio", sub))
        rows.append(_row_value(item, th, F, u, inner_w, ctx, str(value or "$0"), counter=True, big=16.5))
        if items:
            rows.append(_row_divider(th, u))
            rows.append(_row_items(item, th, F, u, inner_w, ctx, items))
    elif v == "location":
        # `map: false` = tarjeta compacta (encabezado con pin, sin mapa): cuando no hay b-roll que
        # la hospede y la completa no cabe sobre la toma
        if item.get("map") is not False and str(item.get("map", "")).lower() not in ("false", "0", "no"):
            rows.append(_row_map(item, th, F, u, inner_w, ctx))
        rows.append(_row_header(item, th, F, u, inner_w, ctx, icon, title or str(value or ""), sub))
    elif v == "schedule":
        rows.append(_row_header(item, th, F, u, inner_w, ctx, icon, title or "Horarios disponibles", sub,
                                badge_fn=_cal_clock_badge(th, u), badge_scale=1.12))
        days = item.get("days")
        if days is not None:
            rows.append(_row_chips(th, F, u, inner_w, ["L", "M", "M", "J", "V", "S", "D"],
                                   active=[int(d) for d in days], delay=0.05))
        if items:
            rows.append(_row_chips(th, F, u, inner_w, [str(s) for s in items]))
        if item.get("label"):
            rows.append(_row_button(item, th, F, u, inner_w, ctx, str(item["label"]), "arrow-down",
                                    cursor=bool(_g(item, "cursor", False))))
    elif v == "link":
        rows.append(_row_header(item, th, F, u, inner_w, ctx, icon, title or "Agenda tu cita", sub))
        rows.append(_row_button(item, th, F, u, inner_w, ctx, str(item.get("label") or value or "Agenda aqui"),
                                "arrow-right", cursor=bool(_g(item, "cursor", True))))
    elif v == "phone":
        rows.append(_row_header(item, th, F, u, inner_w, ctx, icon, title or "Llamanos", sub,
                                badge_fn=_ringing_icon(icon, th, ctx)))
        if value:
            rows.append(_row_value(item, th, F, u, inner_w, ctx, str(value), counter=False, big=9.5, marker=False))
    elif v == "rating":
        rv = parse_amount(str(value or "5"))[1] or 5.0
        rows.append(_row_stars(th, F, u, inner_w, rv))
        rows.append(_row_header(item, th, F, u, inner_w, ctx, icon, title or f"{rv:.1f}", sub)
                    if title else _row_value(item, th, F, u, inner_w, ctx, f"{rv:.1f}", True, 12.0, False))
        if sub and not title:
            rows.append(_row_text(th, F, u, inner_w, sub))
    elif v == "checklist":
        if title:
            rows.append(_row_text(th, F, u, inner_w, title, size=5.6, font_key="title", col="fg"))
        rows.append(_row_items(item, th, F, u, inner_w, ctx, items or [sub or "Listo"], check_every=0.28))
    elif v == "stat":
        rows.append(_row_value(item, th, F, u, inner_w, ctx, str(value or "0"), counter=True, big=18.0, marker=False))
        if title:
            rows.append(_row_text(th, F, u, inner_w, title, size=5.0, font_key="title", col="fg"))
        if sub:
            rows.append(_row_text(th, F, u, inner_w, sub))
        frac = item.get("progress")
        if frac is not None:
            rows.append(_row_bar(th, u, float(frac)))
    else:
        rows.append(_row_header(item, th, F, u, inner_w, ctx, icon, title, sub))
        if value:
            rows.append(_row_value(item, th, F, u, inner_w, ctx, str(value), counter=False, big=11.0, marker=False))
        if items:
            rows.append(_row_items(item, th, F, u, inner_w, ctx, items))
    return rows


def _row_text(th, F, u, inner_w, s: str, size: float = 3.9, font_key: str = "body", col: str = "sub"):
    fs = font(F[font_key], size * u)
    lines = wrap(fs, s, inner_w, 2)
    lh = size * u * 1.3
    h = len(lines) * lh

    def fn(cv, x, y, w, t, a):
        e = ease.out_cubic(_clamp(t / 0.35))
        for i, ln in enumerate(lines):
            draw_text(cv, ln, x, y + lh * (i + 0.8) + (1 - e) * 2 * u, fs, th[col],
                      a * e * (th.get("sub_a", 1.0) if col == "sub" else 1.0))

    return _Row(h, fn)


def _ringing_icon(icon, th, ctx):
    t_icon = _spring_reach(0.85, 2.8, 0.5)

    def fn(cv, cx, cy, bs, t, a):
        q, ip = _badge_icon_q(t, t_icon, bs * 0.56, ctx)
        if q <= 0 or ip <= 0.01:
            return
        ring = math.sin(t * TAU * 7) * 14 * math.exp(-((t % 1.4) - 0.5) ** 2 / 0.02) if t > 0.3 else 0
        cv.save()
        cv.translate(cx, cy)
        cv.rotate(ring)
        cv.scale(ip, ip)
        paint_icon(cv, icon, 0, 0, bs * 0.56, th["on_badge"], a, q)
        cv.restore()
    return fn


def _card_matrix_flip(w: float, h: float, ang: float) -> skia.Matrix:
    """Perspectiva real: rotacion en X alrededor del centro de la tarjeta."""
    D = max(w, h) * 2.4
    ca, sa = math.cos(ang), math.sin(ang)
    src, dst = [], []
    for (x, y) in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        z = y * sa
        k = D / (D + z)
        src.append(skia.Point(x, y))
        dst.append(skia.Point(x * k, y * ca * k))
    m = skia.Matrix()
    m.setPolyToPoly(src, dst)
    return m


def card_layout(item: dict, ctx, width_px: float | None = None):
    """Layout de la tarjeta. Devuelve (theme, fuentes, u, pad, inner, rows, gap, Wc, Hc) en px.
    director.card_height usa el indice 8 (Hc): mantener el orden."""
    item = sanitize(item)
    th = card_theme(item, ctx)
    F = _fonts(ctx)
    Wc = width_px if width_px is not None else size_of(item, "card", DEFAULTS["card"]["size"]) * ctx.W
    u = Wc / 100.0
    pad = 6.4 * u
    inner = Wc - 2 * pad
    rows = _card_rows(item, th, F, u, inner, ctx)
    gap = 4.6 * u
    Hc = pad * 2 + sum(r.h for r in rows) + gap * max(0, len(rows) - 1)
    return th, F, u, pad, inner, rows, gap, Wc, Hc


def card_size(item: dict, ctx) -> tuple[float, float]:
    """(ancho, alto) de la tarjeta en px del cuadro de ctx (para que el director la acomode)."""
    r = card_layout(item, ctx)
    return float(r[7]), float(r[8])


def draw_card(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    item = sanitize(item)
    th, F, u, pad, inner, rows, gap, Wc, Hc = card_layout(item, ctx)
    inten = _clamp(_num(item, "intensity", 1.0))
    c = center_of(item, ctx, XC, 0.62)
    if c is None:
        return
    cx, cy = c
    # mantener la tarjeta dentro de la zona segura (vertical y horizontal)
    cy = _clamp(cy, C.SAFE["top"] * ctx.H + Hc / 2, C.SAFE["bottom"] * ctx.H - Hc / 2) \
        if Hc < (C.SAFE["bottom"] - C.SAFE["top"]) * ctx.H else cy
    cx = safe_cx(cx, Wc / 2, ctx)
    anim_in = item.get("anim_in") or "slide_up"
    anim_out = item.get("anim_out") or "slide_down"
    tin = _num(item, "in_dur", 0.5)
    tout = _num(item, "out_dur", 0.28)
    _, pout = ease.envelope(lt, dur, tin, tout)
    flip = anim_in == "flip"
    st = (enter_state("fade", lt, 0.12, ctx) if flip else enter_state(anim_in, lt, tin, ctx, inten, big=True)) \
        * exit_state(anim_out, pout, ctx)
    st.r += _num(item, "rotation", 0.0)
    if not _visible(st):
        return
    a = st.a
    with group(canvas, cx, cy, st):
        if flip:
            sp = spring(lt, freq=1.9, damping=0.55)
            canvas.concat(_card_matrix_flip(Wc, Hc, (1 - sp) * math.radians(-80)))
        rect = skia.Rect.MakeXYWH(-Wc / 2, -Hc / 2, Wc, Hc)
        rr = skia.RRect.MakeRectXY(rect, 5.6 * u, 5.6 * u)
        soft_shadow(canvas, rr, ctx, a * (0.9 if th["name"] != "dark" else 1.2))
        canvas.drawRRect(rr, C.fill(th["bg"], th["bg_a"] * a))
        bc, ba = _col_a(th["border"])
        if th["name"] == "light":
            # luz superior sutil (volumen)
            hl = skia.Paint(AntiAlias=True)
            hl.setDither(True)  # degradados sin anillos (8 bits)
            hl.setShader(skia.GradientShader.MakeLinear(
                [skia.Point(0, -Hc / 2), skia.Point(0, Hc / 2)],
                [C.color("#FFFFFF", 0.0), C.color("#F3F1EC", a)]))
            canvas.drawRRect(rr, hl)
        canvas.drawRRect(rr, C.stroke(bc, max(1.0, 0.25 * u), ba * a))
        # filas escalonadas, sincronizadas con el contenedor (la primera ya asoma en el cuadro 0:
        # un contenedor vacio varios cuadros se lee como placeholder de carga)
        y = -Hc / 2 + pad
        for i, r in enumerate(rows):
            tr = lt + 0.06 - i * 0.05
            e = ease.out_cubic(_clamp(tr / 0.3))
            if e > 0.0:
                canvas.save()
                canvas.translate(0, (1 - e) * 3.5 * u)
                r.fn(canvas, -Wc / 2 + pad, y, inner, max(0.0, tr), a * e)
                canvas.restore()
            y += r.h + gap


# ============================================================== lower third


def draw_lower_third(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    """Rotulo: barra de acento que crece -> panel que se despliega -> titulo y rol (versalitas con
    tracking). Todo se ajusta al ancho disponible hasta SAFE.right (titulo con fit_font, rol que
    se reduce y si no alcanza termina en elipsis).
    anim_in: 'draw' (default) = solo la revelacion propia; 'none' = sin revelacion; cualquier otra
    (slide_left, pop, fade...) se aplica como transformacion del grupo ademas de la revelacion.
    anim_out: 'undraw' (default) = el panel se pliega; 'none' = corte; otras = transformacion
    del grupo (fade, slide_down, pop_out...). `rotation` gira el grupo."""
    item = sanitize(item)
    pal = ctx.palette
    F = _fonts(ctx)
    title = str(item.get("title") or ctx.entities.get("name", "") or "")
    subtitle = str(item.get("subtitle") or ctx.entities.get("role", "") or "")
    if not title.strip() and not subtitle.strip():
        return
    inten = _clamp(_num(item, "intensity", 1.0))
    size = size_of(item, "lower_third", 0.056)
    theme = item.get("theme") or "dark"
    acc = item.get("color") or item.get("accent") or pal["accent"]
    if theme != "dark" and C.luminance(acc) > 0.8:  # acento blanco sobre panel blanco: no se ve
        acc = pal["accent2"]
    x0 = _clamp(_num(item, "x", 0.07), 0.0, C.SAFE["right"] - 0.2) * ctx.W
    yc = _num(item, "y", 0.70) * ctx.H
    padx, pady = ctx.px(28), ctx.px(20)
    barw = ctx.px(10)
    avail = max(ctx.px(120), (C.SAFE["right"] * ctx.W - x0) - barw - 2 * padx)
    ft = fit_font(F["title"], title, size * ctx.W, avail, min_px=size * ctx.W * 0.55)
    title = ellipsize(ft, title, avail)
    sub_u = subtitle.upper()
    fs_px = size * ctx.W * 0.5
    fs = font(F["label"], fs_px)
    trk = fs.getSize() * 0.14
    while sub_u and text_w(fs, sub_u, trk) > avail and fs.getSize() > fs_px * 0.7:
        fs = font(F["label"], fs.getSize() * 0.94)
        trk = fs.getSize() * 0.14
    sub_u = ellipsize(fs, sub_u, avail, trk)
    wt = ft.measureText(title)
    ws = text_w(fs, sub_u, trk)
    th_t = ft.getSize() * 1.05
    th_s = fs.getSize() * 1.25 if sub_u else 0
    gap = ctx.px(8) if sub_u else 0
    ph = pady * 2 + th_t + gap + th_s
    pw = max(wt, ws) + padx * 2
    anim_in = item.get("anim_in") or "draw"
    anim_out = item.get("anim_out") or "undraw"
    tin = _num(item, "in_dur", 0.7)
    tout = _num(item, "out_dur", 0.4)
    _, pout = ease.envelope(lt, dur, tin, tout)
    reveal = anim_in != "none"
    fold = anim_out == "undraw"
    lin = lt if reveal else 10.0
    pf = pout if fold else 0.0
    out = ease.in_cubic(pf)
    # entrada: barra crece -> panel se despliega -> textos
    bar_p = ease.out_expo(_clamp(lin / 0.32)) * (1 - ease.in_cubic(_clamp((pf - 0.55) / 0.45)))
    pan_p = ease.out_expo(_clamp((lin - 0.12) / 0.5)) * (1 - ease.in_out_cubic(_clamp(pf / 0.7)))
    if bar_p <= 0.002:
        return
    st = _St()
    if anim_in not in ("draw", "none"):
        st = enter_state(anim_in, lt, tin, ctx, inten, big=True)
    if not fold:
        st = st * exit_state(anim_out, pout, ctx)
    st.r += _num(item, "rotation", 0.0)
    if not _visible(st):
        return
    top = yc - ph / 2
    bg = "#0F0F14" if theme == "dark" else "#FFFFFF"
    bg_a = 0.78 if theme == "dark" else 0.96
    fg = "#FFFFFF" if theme == "dark" else "#15151B"
    # grupo con pivote en el borde izquierdo del rotulo (la barra)
    with group(canvas, x0, yc, st):
        canvas.translate(-x0, -yc)
        lay = st.a < 0.999
        if lay:
            canvas.saveLayer(None, skia.Paint(Alphaf=st.a))
        if pan_p > 0.002:
            pr = skia.Rect.MakeXYWH(x0 + barw, top, pw * pan_p, ph)
            rr = skia.RRect()
            rr.setRectRadii(pr, [skia.Point(0, 0), skia.Point(ctx.px(14), ctx.px(14)),
                                 skia.Point(ctx.px(14), ctx.px(14)), skia.Point(0, 0)])
            soft_shadow(canvas, rr, ctx, pan_p * 0.8, key=0.18, amb=0.14)
            canvas.drawRRect(rr, C.fill(bg, bg_a))
            canvas.save()
            canvas.clipRect(pr, skia.ClipOp.kIntersect, True)
            te = ease.out_cubic(_clamp((lin - 0.28) / 0.45)) * (1 - out)
            draw_text(canvas, title, x0 + barw + padx - (1 - te) * ctx.px(40), top + pady + ft.getSize() * 0.86,
                      ft, fg, te)
            se = ease.out_cubic(_clamp((lin - 0.42) / 0.45)) * (1 - out)
            if sub_u:
                draw_text(canvas, sub_u, x0 + barw + padx, top + pady + th_t + gap + fs.getSize() * 0.95
                          + (1 - se) * ctx.px(14), fs, acc if theme == "dark" else C.mix(acc, "#000000", 0.35), se,
                          tracking=trk)
            canvas.restore()
        # barra de acento
        bh = ph * bar_p
        canvas.drawRoundRect(skia.Rect.MakeXYWH(x0, yc - bh / 2, barw, bh), barw / 2, barw / 2, C.fill(acc))
        if lay:
            canvas.restore()


# ============================================================== CTA

CTA_VARIANTS = ("follow", "link_below", "comment", "save", "share")


def downsample(img: skia.Image, steps: int) -> skia.Image:
    """Piramide de cajas 2x2 (escala 0.5 lineal por paso): barato y sin aliasing, a diferencia
    de muestrear con mipmaps una imagen nueva en cada cuadro (reconstruye mipmaps)."""
    for _ in range(max(0, steps)):
        w, h = max(1, img.width() // 2), max(1, img.height() // 2)
        s = C.offscreen(w, h)
        s.getCanvas().drawImageRect(img, skia.Rect.MakeWH(w, h), C.SAMPLING_FAST)
        img = s.makeImageSnapshot()
    return img


def draw_background(canvas, kind: str, colors, lt: float, ctx) -> None:
    """Alias de cutaways.draw_background (los fondos animados viven alli, contrato 4.2b)."""
    from . import cutaways as _cu  # perezoso: cutaways importa graphics
    _cu.draw_background(canvas, kind, colors, lt, ctx)


def _avatar(canvas, cx, cy, d, initial: str, pal, a, ctx, face_img=None):
    ring = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=d * 0.07)
    ring.setDither(True)  # degradados sin anillos (8 bits)
    ring.setShader(skia.GradientShader.MakeSweep(cx, cy, [C.color(pal["accent"]), C.color(pal["warm"]),
                                                          C.color("#FF3B7F"), C.color(pal["accent"])]))
    ring.setAlphaf(a)
    canvas.drawCircle(cx, cy, d / 2, ring)
    inner = d * 0.8
    if face_img is not None:
        steps = 0
        while face_img.width() / (2 ** (steps + 1)) >= inner:
            steps += 1
        face_img = downsample(face_img, steps)
        canvas.save()
        pth = skia.Path()
        pth.addCircle(cx, cy, inner / 2)
        canvas.clipPath(pth, skia.ClipOp.kIntersect, True)
        canvas.drawImageRect(face_img, skia.Rect.MakeXYWH(cx - inner / 2, cy - inner / 2, inner, inner), C.SAMPLING_FAST,
                             skia.Paint(Alphaf=a))
        canvas.restore()
        return
    g = skia.Paint(AntiAlias=True)
    g.setDither(True)  # degradados sin anillos (8 bits)
    g.setShader(skia.GradientShader.MakeLinear([skia.Point(cx - inner / 2, cy - inner / 2),
                                                skia.Point(cx + inner / 2, cy + inner / 2)],
                                               [C.color(pal["accent2"]), C.color(C.mix(pal["accent2"], "#000000", 0.35))]))
    g.setAlphaf(a)
    canvas.drawCircle(cx, cy, inner / 2, g)
    if initial:
        f = font("Poppins-Bold", inner * 0.5)
        draw_text(canvas, initial, cx, cy + inner * 0.18, f, "#FFFFFF", a, align="center")
    else:
        paint_icon(canvas, "user", cx, cy, inner * 0.56, "#FFFFFF", a, 1.0, stroke_w=2.2)


def _face_crop(ctx) -> skia.Image | None:
    img = ctx.base_image()
    if img is None or not ctx.face:
        return None
    fx, fy, fw, fh = ctx.face
    s = max(fw * ctx.W, fh * ctx.H) * 1.5
    r = skia.IRect.MakeXYWH(int(fx * ctx.W - s / 2), int(fy * ctx.H - s / 2), int(s), int(s))
    try:
        return img.makeSubset(r)
    except Exception:
        return None


_CTA_LABEL = {"comment": "Comenta abajo", "save": "Guardalo para despues", "share": "Compartelo con alguien"}


def _cta_width(item: dict, key: str, ctx) -> float:
    """Ancho nominal en px del CTA (size acotado a un rango legible y dentro de SAFE)."""
    v = item.get("variant") if key == "cta" else key
    rk = v if v in ("follow", "link_below") else "cta"
    return size_of(item, rk, CTA_SIZE.get(v or "follow", 0.62)) * ctx.W


def _link_below_layout(item: dict, ctx):
    F = _fonts(ctx)
    label = str(item.get("label") or ctx.entities.get("link_label") or "Agenda aqui")
    size = _cta_width(item, "link_below", ctx)
    h = size * 0.2
    fb = fit_font(F["title"], label, h * 0.38, size * 0.9, min_px=h * 0.26)
    label = ellipsize(fb, label, size * 0.9)
    ib = h * 0.42
    bw = fb.measureText(label) + ib + h * 0.2 + h * 1.0
    return label, fb, h, ib, bw


def cta_extent(item: dict, ctx) -> float:
    """Medio ancho en px del CTA ya maquetado (para mantenerlo dentro de SAFE)."""
    v = item.get("variant") or "follow"
    if v == "follow":
        return _cta_width(item, "follow", ctx) / 2
    if v == "link_below":
        return _link_below_layout(item, ctx)[4] / 2
    lab = str(item.get("label") or _CTA_LABEL.get(v, ""))
    return _pill_layout(item, lab, ctx)[4] / 2


def follow_texts(item: dict, ctx) -> tuple[str, str]:
    """(nombre de la cuenta, subtitulo) del CTA follow.
    El nombre sale de `handle` o de las entidades (handle, name); `label` es el texto de llamada
    ('Sigueme'): va de subtitulo si no hay rol, y solo es el nombre si no hay ningun otro."""
    ent = ctx.entities or {}
    label = str(item.get("label") or "").strip()
    name = str(item.get("handle") or ent.get("handle") or ent.get("name") or "").strip()
    if not name:
        name = label or "Sigueme para mas"
        label = ""
    sub = str(item.get("subtitle") or "").strip()
    if not sub:
        role = "" if (item.get("handle") or ent.get("handle")) else str(ent.get("role") or "").strip()
        sub = role or (label if label and label.lower() != name.lower() else "")
    return name, sub


def _cta_follow(canvas, item, lt, dur, ctx, a):
    pal = ctx.palette
    F = _fonts(ctx)
    name, sub = follow_texts(item, ctx)
    btn_a, btn_b = str(item.get("button") or "Seguir"), str(item.get("button_done") or "Siguiendo")
    W_ = _cta_width(item, "follow", ctx)
    h = W_ * 0.17
    btn_col = item.get("button_color") or item.get("color") or "#FF3B5C"
    if C.luminance(btn_col) > 0.85:  # blanco (default del esquema) no sirve de boton sobre pastilla blanca
        btn_col = "#FF3B5C"
    btn_fg = "#141418" if C.luminance(btn_col) > 0.6 else "#FFFFFF"
    initial = re.sub(r"^(Dra?\.?|Lic\.?|Ing\.?|Mtr[oa]\.?)\s+", "", name.lstrip("@")).strip()[:1].upper()
    press_at = _num(item, "press_at", 1.05)
    rr = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(-W_ / 2, -h / 2, W_, h), h / 2, h / 2)
    soft_shadow(canvas, rr, ctx, a)
    canvas.drawRRect(rr, C.fill("#FFFFFF", a))
    d = h * 0.74
    ax = -W_ / 2 + h * 0.13 + d / 2
    face = _face_crop(ctx) if item.get("avatar") == "face" else None
    ae = spring(lt - 0.03, 3.0, 0.5)  # el contenido entra casi con la pastilla (sin 'placeholder')
    if ae > 0.01:
        canvas.save()
        canvas.translate(ax, 0)
        canvas.scale(ae, ae)
        _avatar(canvas, 0, 0, d, initial, pal, a, ctx, face)
        canvas.restore()
    done = lt >= press_at + 0.08
    fb = font(F["title"], h * 0.27)
    bw_a = fb.measureText(btn_a) + h * 0.7
    bw_b = fb.measureText(btn_b) + h * 0.7 + h * 0.3
    tb = ease.out_cubic(_clamp((lt - press_at - 0.08) / 0.3))
    bw = bw_a + (bw_b - bw_a) * tb
    bh = h * 0.56
    bx = W_ / 2 - h * 0.2 - bw
    tx = ax + d / 2 + h * 0.22
    # el texto se ajusta al boton en su ancho maximo ('Siguiendo'): no cambia al presionar
    maxw = (W_ / 2 - h * 0.2 - max(bw_a, bw_b)) - tx - h * 0.15
    fn_ = fit_font(F["title"], name, h * 0.31, maxw, min_px=h * 0.2)
    name = ellipsize(fn_, name, maxw)
    fs_ = fit_font(F["body"], sub, h * 0.22, maxw, min_px=h * 0.15) if sub else None
    if sub:
        sub = ellipsize(fs_, sub, maxw)
    te = ease.out_cubic(_clamp((lt - 0.08) / 0.35))
    if sub:
        draw_text(canvas, name, tx + (1 - te) * h * 0.2, -h * 0.02, fn_, "#15151B", a * te)
        draw_text(canvas, sub, tx + (1 - te) * h * 0.2, h * 0.25, fs_, "#6C6D7A", a * te)
    else:
        draw_text(canvas, name, tx + (1 - te) * h * 0.2, h * 0.1, fn_, "#15151B", a * te)
    pr = lt - press_at
    press = 1.0 - 0.1 * math.exp(-((pr - 0.05) / 0.07) ** 2)
    be = spring(lt - 0.14, 3.0, 0.5)
    if be > 0.01:
        br = skia.Rect.MakeXYWH(bx, -bh / 2, bw, bh)
        canvas.save()
        canvas.translate(br.centerX(), 0)
        canvas.scale(be * press, be * press)
        canvas.translate(-br.centerX(), 0)
        bc = C.mix(btn_col, "#EDEDF0", tb) if done else C.color(btn_col)
        canvas.drawRoundRect(br, bh / 2, bh / 2, C.fill(bc, a))
        if not done:
            draw_text(canvas, btn_a, br.centerX(), fb.getSize() * 0.36, fb, btn_fg, a, align="center")
        else:
            ib = h * 0.26
            wtxt = fb.measureText(btn_b)
            x1 = br.centerX() - (wtxt + ib + h * 0.08) / 2
            paint_icon(canvas, "check", x1 + ib / 2, 0, ib, "#15151B", a * tb, tb, stroke_w=3.0)
            draw_text(canvas, btn_b, x1 + ib + h * 0.08, fb.getSize() * 0.36, fb, "#15151B", a * tb)
        canvas.restore()
        # destellos al hacer clic
        if 0 < pr < 0.6:
            e = ease.out_cubic(pr / 0.6)
            for k in range(7):
                ang = -math.pi / 2 + (k - 3) * 0.42
                r0, r1 = bh * (0.55 + 0.5 * e), bh * (0.75 + 0.8 * e)
                cxb = br.centerX()
                canvas.drawLine(cxb + math.cos(ang) * r0 * 1.6, math.sin(ang) * r0,
                                cxb + math.cos(ang) * r1 * 1.6, math.sin(ang) * r1,
                                C.stroke(btn_col, h * 0.045, a * (1 - e)))
        if bool(_g(item, "cursor", True)):
            _cursor_tap(canvas, br.centerX() + bw * 0.12, bh * 0.1, h * 0.62, lt, press_at, a, ctx)


def _cta_link_below(canvas, item, lt, dur, ctx, a):
    pal = ctx.palette
    F = _fonts(ctx)
    acc = item.get("color") or pal["accent"]
    on = "#141418" if C.luminance(acc) > 0.55 else "#FFFFFF"
    label, fb, h, ib, bw = _link_below_layout(item, ctx)
    br = skia.Rect.MakeXYWH(-bw / 2, -h / 2, bw, h)
    rr = skia.RRect.MakeRectXY(br, h / 2, h / 2)
    soft_shadow(canvas, rr, ctx, a)
    canvas.drawRRect(rr, C.fill(acc, a))
    sh = ((lt - 0.5) % 1.8) / 0.65
    if 0 < sh < 1:
        canvas.save()
        canvas.clipRRect(rr, skia.ClipOp.kIntersect, True)
        sx = br.left() - bw * 0.3 + bw * 1.6 * ease.in_out_quad(sh)
        gp = skia.Paint(AntiAlias=True)
        gp.setDither(True)  # degradados sin anillos (8 bits)
        gp.setShader(skia.GradientShader.MakeLinear([skia.Point(sx - h, 0), skia.Point(sx + h, 0)],
                                                    [C.color("#FFFFFF", 0), C.color("#FFFFFF", 0.5 * a),
                                                     C.color("#FFFFFF", 0)]))
        canvas.drawRect(br, gp)
        canvas.restore()
    x1 = -(fb.measureText(label) + ib + h * 0.2) / 2
    paint_icon(canvas, item.get("icon") or "calendar-event", x1 + ib / 2, 0, ib, on, a,
               _clamp((lt - 0.15) / 0.5), stroke_w=2.3)
    draw_text(canvas, label, x1 + ib + h * 0.2, fb.getSize() * 0.36, fb, on, a)
    # flecha dibujada a mano que rebota hacia abajo; hereda el alpha del CTA (fade de salida)
    ap = _clamp((lt - 0.25) / 0.4)
    # `arrow_scale` (0.4..1): flecha mas corta cuando abajo hay subtitulos; 0 = sin flecha
    asc = _clamp(_num(item, "arrow_scale", 1.0), 0.0, 1.0)
    if ap > 0 and a > 0.004 and asc >= 0.35:
        sub = {"id": f"{item.get('id', 'cta')}:arrow", "name": "arrow_down", "size": h * 1.7 * asc / ctx.W,
               "x": 0, "y": 0, "color": item.get("arrow_color") or "#FFFFFF", "stroke": 17, "wiggle": 0.45,
               "anim_out": "none", "shadow": 0.8}
        canvas.save()
        canvas.translate(0, h / 2 + h * 0.3 + h * 0.85 * asc)
        if a < 0.999:
            with C.layer(canvas, a):
                draw_doodle(canvas, sub, lt - 0.25, max(0.1, dur - 0.25), ctx)
        else:
            draw_doodle(canvas, sub, lt - 0.25, max(0.1, dur - 0.25), ctx)
        canvas.restore()


def _pill_layout(item, label: str, ctx):
    F = _fonts(ctx)
    size = _cta_width(item, "cta", ctx)
    h = size * 0.17
    fb = fit_font(F["title"], label, h * 0.34, size * 0.8, min_px=h * 0.24)
    label = ellipsize(fb, label, size * 0.8)
    ib = h * 0.72
    w = fb.measureText(label) + ib + h * 0.95
    return label, fb, h, ib, w


def _cta_pill(canvas, item, lt, ctx, a, icon: str, label: str, icon_fn=None):
    label, fb, h, ib, w = _pill_layout(item, label, ctx)
    rr = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(-w / 2, -h / 2, w, h), h / 2, h / 2)
    soft_shadow(canvas, rr, ctx, a)
    canvas.drawRRect(rr, C.fill("#FFFFFF", a))
    bx = -w / 2 + h * 0.14 + ib / 2
    pal = ctx.palette
    be = spring(lt - 0.02, 3.0, 0.5)
    canvas.save()
    canvas.translate(bx, 0)
    canvas.scale(max(0.0, be), max(0.0, be))
    canvas.drawCircle(0, 0, ib / 2, C.fill(item.get("color") or pal["accent"], a))
    if icon_fn:
        icon_fn(canvas, ib, lt, a)
    else:
        paint_icon(canvas, icon, 0, 0, ib * 0.56, "#141418", a, _clamp((lt - 0.12) / 0.5))
    canvas.restore()
    return fb, bx + ib / 2 + h * 0.2, h, label


def _cta_comment(canvas, item, lt, dur, ctx, a):
    label = str(item.get("label") or _CTA_LABEL["comment"])
    fb, tx, h, label = _cta_pill(canvas, item, lt, ctx, a, "message-circle", label)
    # escribiendo... y luego el texto se teclea
    t_type = lt - 0.1
    if t_type < 0.5:
        for k in range(3):
            b = 0.5 + 0.5 * math.sin((lt * 3.2 - k * 0.25) * TAU)
            canvas.drawCircle(tx + h * 0.2 + k * h * 0.22, -b * h * 0.06, h * 0.06, C.fill("#9A9AA6", a))
        return
    n = int(_clamp((t_type - 0.5) / 0.6) * len(label) + 0.999)
    draw_text(canvas, label[:n], tx, fb.getSize() * 0.36, fb, "#15151B", a)


def _cta_save(canvas, item, lt, dur, ctx, a):
    label = str(item.get("label") or _CTA_LABEL["save"])

    def ic(cv, ib, t, al):
        fillk = ease.out_back(_clamp((t - 0.5) / 0.3))
        bounce = 1.0 + 0.15 * math.exp(-((t - 0.62) / 0.08) ** 2)
        cv.save()
        cv.scale(bounce, bounce)
        paint_icon(cv, "bookmark", 0, 0, ib * 0.56, "#141418", al, _clamp((t - 0.1) / 0.4))
        if fillk > 0:
            cv.save()
            cv.scale(fillk, fillk)
            paint_icon(cv, "bookmark", 0, 0, ib * 0.56, "#141418", al, 1.0, variant="filled")
            cv.restore()
        cv.restore()

    fb, tx, h, label = _cta_pill(canvas, item, lt, ctx, a, "bookmark", label, ic)
    e = ease.out_cubic(_clamp((lt - 0.08) / 0.35))
    draw_text(canvas, label, tx + (1 - e) * h * 0.2, fb.getSize() * 0.36, fb, "#15151B", a * e)


def _cta_share(canvas, item, lt, dur, ctx, a):
    label = str(item.get("label") or _CTA_LABEL["share"])

    def ic(cv, ib, t, al):
        # ciclo: dibujarse -> despegar (in_back: toma impulso y sale disparado del circulo con
        # alpha in_quad) -> el avion nuevo aparece con pop en cuanto el anterior se fue
        T0, FLY, REST = 0.6, 0.42, 1.4
        if t < T0:
            paint_icon(cv, "send", 0, 0, ib * 0.56, "#141418", al, _clamp((t - 0.1) / 0.4))
            return
        k = (t - T0) % (FLY + REST)
        if k < FLY:
            fly = k / FLY
            m = ease.in_back(fly, 1.6)
            fa = al * (1.0 - ease.in_quad(fly))
            if fa > 0.004:
                paint_icon(cv, "send", ib * 2.0 * m, -ib * 1.5 * m, ib * 0.56, "#141418", fa, 1.0)
        # el avion nuevo aparece con pop en cuanto el anterior salio del circulo (60% del vuelo)
        tn = k - FLY * 0.6
        if tn < 0:
            return
        ret = spring(tn, 3.2, 0.5)
        if ret <= 0.01:
            return
        cv.save()
        cv.scale(ret, ret)
        paint_icon(cv, "send", 0, 0, ib * 0.56, "#141418", al, 1.0)
        cv.restore()

    fb, tx, h, label = _cta_pill(canvas, item, lt, ctx, a, "send", label, ic)
    e = ease.out_cubic(_clamp((lt - 0.08) / 0.35))
    draw_text(canvas, label, tx + (1 - e) * h * 0.2, fb.getSize() * 0.36, fb, "#15151B", a * e)


def draw_cta(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    item = sanitize(item)
    v = item.get("variant") or "follow"
    if v not in CTA_VARIANTS:
        v = "follow"
    item["variant"] = v
    inten = _clamp(_num(item, "intensity", 1.0))
    c = center_of(item, ctx, XC, 0.7 if v != "link_below" else 0.68)
    if c is None:
        return
    cx, cy = c
    cx = safe_cx(cx, cta_extent(item, ctx), ctx)
    anim_in = item.get("anim_in") or "pop"
    anim_out = item.get("anim_out") or "pop_out"
    tin = _num(item, "in_dur", 0.45)
    tout = _num(item, "out_dur", 0.25)
    _, pout = ease.envelope(lt, dur, tin, tout)
    st = enter_state(anim_in, lt, tin, ctx, inten, big=True) * exit_state(anim_out, pout, ctx)
    st.r += _num(item, "rotation", 0.0)
    if not _visible(st):
        return
    with group(canvas, cx, cy, st):
        {"follow": _cta_follow, "link_below": _cta_link_below, "comment": _cta_comment,
         "save": _cta_save, "share": _cta_share}[v](canvas, item, lt, dur, ctx, st.a)


# ============================================================== progreso y sticker


def draw_progress(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    """Barra superior de progreso. Solo usa color, thickness, `bar_y` (borde superior, fraccion
    de H; default 0) y `margin` (px ref). Ignora x/y/size comunes a proposito (el esquema generico
    pone y=0.3 y la barra quedaria a media pantalla). anim_in: fade (default) | none;
    anim_out: none (default: llega al 100%) | fade."""
    item = sanitize(item)
    pal = ctx.palette
    col = item.get("color") or pal["accent"]
    th = ctx.px(_clamp(_num(item, "thickness", 8.0), 1.0, 40.0))
    y = _clamp(_num(item, "bar_y", 0.0), 0.0, 1.0) * ctx.H + th / 2 + ctx.px(_num(item, "margin", 0.0))
    y = min(y, ctx.H - th / 2)
    p = _clamp(lt / max(1e-3, dur))
    intro = 1.0 if item.get("anim_in") == "none" else ease.out_cubic(_clamp(lt / 0.35))
    if item.get("anim_out") in ("fade", "pop_out", "slide_up", "slide_down"):
        intro *= 1.0 - ease.in_quad(_clamp((lt - (dur - 0.3)) / 0.3))
    if intro <= 0.003:
        return
    W = ctx.W
    canvas.drawRect(skia.Rect.MakeLTRB(0, y - th / 2, W, y + th / 2), C.fill("#FFFFFF", 0.18 * intro))
    x1 = W * p
    if x1 <= 0.5:
        return
    glow = C.fill(col, 0.55 * intro)
    glow.setMaskFilter(blur_mask(th * 1.1))
    canvas.drawRoundRect(skia.Rect.MakeLTRB(0, y - th / 2, x1, y + th / 2), th / 2, th / 2, glow)
    canvas.drawRoundRect(skia.Rect.MakeLTRB(-th, y - th / 2, x1, y + th / 2), th / 2, th / 2, C.fill(col, intro))
    canvas.drawCircle(x1, y, th * 0.95, C.fill("#FFFFFF", intro))
    canvas.drawCircle(x1, y, th * 0.95, C.stroke(col, th * 0.35, intro))


def draw_sticker_text(canvas: skia.Canvas, item: dict, lt: float, dur: float, ctx) -> None:
    item = sanitize(item)
    pal = ctx.palette
    text = str(item.get("text") or item.get("label") or item.get("title") or "")
    if not text.strip():
        return
    bg = item.get("bg") or pal["accent"]
    fg = item.get("fg") or ("#141418" if C.luminance(bg) > 0.55 else "#FFFFFF")
    inten = _clamp(_num(item, "intensity", 1.0))
    size = size_of(item, "sticker_text", 0.062) * ctx.W
    border = ctx.px(8) if bool(_g(item, "border", True)) else 0.0
    # ancho maximo de la pastilla (con borde y padding) = ancho de SAFE
    max_tw = SAFE_W * ctx.W - 2 * border - size * 1.0
    fnt = fit_font(item.get("font") or "Poppins-Black", text, size, max_tw, min_px=size * 0.5)
    text = ellipsize(fnt, text, max_tw)
    c = center_of(item, ctx, XC, 0.35)
    if c is None:
        return
    cx, cy = c
    rnd = ctx.rand(f"st:{item.get('id', text)}")
    rot = _num(item, "rotation", (-5.0 + rnd * 4.0))
    anim_in = item.get("anim_in") or "pop"
    anim_out = item.get("anim_out") or "pop_out"
    tin, tout = _num(item, "in_dur", 0.45), _num(item, "out_dur", 0.22)
    _, pout = ease.envelope(lt, dur, tin, tout)
    st = enter_state(anim_in, lt, tin, ctx, inten) * exit_state(anim_out, pout, ctx)
    # tambaleo de sticker a 8 fps
    st.r += rot + math.sin(ease.hold_steps(lt, 8) * 5.1) * 1.4 * inten
    if not _visible(st):
        return
    tw = fnt.measureText(text)
    ph, pw = fnt.getSize() * 1.45, tw + fnt.getSize() * 1.0
    # medio ancho girado (la pastilla inclinada ocupa un poco mas)
    ang = math.radians(abs(rot) + 1.4)
    half = (pw / 2 + border) * math.cos(ang) + (ph / 2 + border) * math.sin(ang)
    cx = safe_cx(cx, half, ctx)
    r = skia.Rect.MakeXYWH(-pw / 2, -ph / 2, pw, ph)
    rad = ph * 0.28
    with group(canvas, cx, cy, st):
        a = st.a
        outer = skia.RRect.MakeRectXY(r.makeOutset(border, border), rad + border, rad + border)
        soft_shadow(canvas, outer, ctx, a, key=0.25, amb=0.12)
        if border:
            canvas.drawRRect(outer, C.fill("#FFFFFF", a))
        canvas.drawRRect(skia.RRect.MakeRectXY(r, rad, rad), C.fill(bg, a))
        draw_text(canvas, text, 0, fnt.getSize() * 0.35, fnt, fg, a, align="center")


RENDERERS = {
    "doodle": draw_doodle,
    "icon": draw_icon,
    "card": draw_card,
    "lower_third": draw_lower_third,
    "cta": draw_cta,
    "progress": draw_progress,
    "sticker_text": draw_sticker_text,
}
