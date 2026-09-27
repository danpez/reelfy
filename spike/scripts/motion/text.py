"""Tipografia del Motion Engine: subtitulos por preset y titulos cineticos.

Contrato (docs/MOTION-ENGINE.md, 3.1 y 4.2b):
  RENDERERS[preset](canvas, item, lt, dur, ctx) -> None     (overlay, tiempo local final)
  draw_captions(canvas, phrases_final, cfg, t, ctx) -> None (t = tiempo final absoluto)

Principios de calidad (referencia: ejemplo_objetivo.mp4):
  - Kerning real: las posiciones de glifo salen del shaper de skia (textlayout) y se cachean
    por par de letras; el texto se dibuja con posiciones explicitas (control por glifo).
  - Interlineado optico: las lineas se apilan por la tinta real (altura de mayuscula y
    descendentes sin acentos), no por el ascent de la fuente -> pilas compactas de editor.
  - Legibilidad: sombra doble (ambiental difusa + de contacto) en vez de contorno duro; se
    refuerza sola si el fondo bajo el texto es claro (muestrea ctx.base_image()).
  - Movimiento: resortes y out_expo; nada lineal. Revelado palabra por palabra al ritmo de
    la voz cuando el item trae `_words`.
  - Nunca fuera de SAFE en reposo (se reduce y desplaza); nunca se cortan palabras.

Coordenadas: `x` es el ancla horizontal (centro, borde izq. o der. segun `align`), `y` es el
centro vertical del bloque. `size` = tamano de fuente de la linea base como fraccion de W;
sin `size` el bloque se autoajusta al ancho util de SAFE.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import skia

from . import assets, ease
from . import ctx as C
from .ctx import SAFE

REF = 100.0          # tamano (px) al que se mide todo; la geometria escala lineal
_GREF = 400.0        # tamano para medir la tinta de glifos (evita redondeo de bounds)

ANIM_IN = ("pop", "rise", "slide_left", "slide_right", "type", "mask", "fade", "scale_down",
           "word_stagger", "letter_stagger")
ANIM_OUT = ("fade", "pop_out", "slide_up", "none")
CAPTION_PRESET_NAMES = ("clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps", "none")

# palabras funcionales (jerarquia automatica de `stack` y cortes de `cascade`)
_FUNC = set("""de del la las el los al a y e o u en con por para que te tu tus mi mis se su sus
un una unos unas lo le les me nos ya no si sin como muy mas pero es son hoy""".split()) - {"hoy"}
# palabras que no deben cerrar una linea ni un grupo de subtitulos (normalizadas, sin acentos)
_FUNC_END = _FUNC | set("""como cuando donde porque cual cuanto cuanta al del tan sobre entre hasta
desde hacia sin ante tras segun mientras aunque ni""".split())

# ---------------------------------------------------------------- defaults por preset
# role: rol de fuente en la paleta del estilo (title/caption/serif/script/impact)
PRESETS: dict[str, dict] = {
    "title_pop": dict(role="title", align="center", y=0.20, anim_in="pop", anim_out="pop_out",
                      shadow=0.8, max_size=0.125, min_size=0.06, cpl=13, max_lines=3, gap=0.13,
                      step=0.11, space=1.12),
    "stack": dict(role="title", align="center", y=0.62, anim_in="word_stagger", anim_out="slide_up",
                  shadow=0.8, max_size=0.24, min_size=0.05, cpl=12, max_lines=4, gap=0.13, step=0.13,
                  space=1.1),
    "cascade": dict(role="caption", align="left", y=0.20, anim_in="slide_left", anim_out="fade",
                    shadow=0.8, max_size=0.095, min_size=0.05, cpl=12, max_lines=4, gap=0.10,
                    step=0.13, last_scale=1.22, cascade_step=0.13),
    "serif_elegant": dict(role="serif", align="center", y=0.15, anim_in="fade", anim_out="fade",
                          shadow=0.6, max_size=0.135, min_size=0.06, cpl=15, max_lines=3, gap=0.06,
                          step=0.16, color_key="light"),
    "script_accent": dict(role="script", align="center", y=0.50, anim_in="mask", anim_out="fade",
                          shadow=0.55, glow=0.3, max_size=0.24, min_size=0.08, cpl=14, max_lines=2,
                          gap=0.02, step=0.12, rotation=-4.0),
    "behind_giant": dict(role="impact", align="center", y=0.30, anim_in="letter_stagger",
                         anim_out="fade", shadow=0.35, max_size=0.46, min_size=0.12, cpl=9,
                         max_lines=2, gap=0.035, step=0.05, case="upper", behind=True, drift=0.04,
                         tracking=0.01),
    "highlight_marker": dict(role="title", align="center", y=0.66, anim_in="rise", anim_out="fade",
                             shadow=0.65, max_size=0.10, min_size=0.05, cpl=16, max_lines=3,
                             gap=0.24, step=0.1, space=1.25),
    "typewriter": dict(font="SpaceMono-Bold", align="center", y=0.70, anim_in="type",
                       anim_out="fade", shadow=0.75, max_size=0.07, min_size=0.04, cpl=18,
                       max_lines=4, gap=0.28, cps=17.0),
    "counter": dict(role="title", align="center", y=0.45, anim_in="pop", anim_out="pop_out",
                    shadow=0.8, max_size=0.24, min_size=0.08, gap=0.12),
    "kicker": dict(role="title", align="center", y=0.22, anim_in="rise", anim_out="slide_up",
                   shadow=0.75, max_size=0.12, min_size=0.055, cpl=13, max_lines=3, gap=0.12,
                   step=0.11, space=1.1),
}

# campos comunes que el inspector puede editar (lo consume schema.py)
COMMON_FIELDS = ("text", "lines", "x", "y", "size", "font", "color", "accent", "accent_words",
                 "align", "stroke", "stroke_color", "shadow", "anim_in", "anim_out", "behind",
                 "box", "case", "rotation", "intensity")

# ---------------------------------------------------------------- cache LRU simple
class _LRU(OrderedDict):
    def __init__(self, cap: int):
        super().__init__()
        self.cap = cap

    def get_or(self, key, fn):
        v = self.get(key)
        if v is None:
            v = fn()
            self[key] = v
            if len(self) > self.cap:
                self.popitem(last=False)
        else:
            self.move_to_end(key)
        return v


_RUNS = _LRU(6000)
_BLOCKS = _LRU(400)
_TIMES = _LRU(400)
_CAPS = _LRU(8)


def clear_cache() -> None:
    for c in (_RUNS, _BLOCKS, _TIMES, _CAPS):
        c.clear()


# ---------------------------------------------------------------- texto
def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", str(s or ""))


def _norm(s: str) -> str:
    """Normaliza para comparar palabras: minusculas, sin acentos ni puntuacion."""
    s = unicodedata.normalize("NFKD", str(s).lower())
    return "".join(ch for ch in s if ch.isalnum())


def _strip_marks(s: str) -> str:
    """Quita acentos (para medir la tinta 'base' al apilar lineas)."""
    d = unicodedata.normalize("NFKD", s)
    return unicodedata.normalize("NFC", "".join(ch for ch in d if not unicodedata.combining(ch)))


def _apply_case(s: str, case: str | None) -> str:
    if case == "upper":
        return s.upper()
    if case == "lower":
        return s.lower()
    if case == "title":
        return " ".join(w[:1].upper() + w[1:] for w in s.split(" "))
    return s


@__import__("functools").lru_cache(maxsize=64)
def _coverage(fname: str):
    return assets.font(fname, REF)


def _clean(s: str, fname: str) -> str:
    """NFC + quita caracteres sin glifo (emojis, simbolos raros) para no dibujar 'tofu'."""
    s = _nfc(s)
    f = _coverage(fname)
    out = [ch for ch in s if ch.isspace() or f.unicharToGlyph(ord(ch)) != 0]
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


_ITALIC = {"DMSerifDisplay": "DMSerifDisplay-Italic", "InstrumentSerif": "InstrumentSerif-Italic",
           "Poppins-Black": "Poppins-BlackItalic"}


def _italic(fname: str) -> tuple[str, float]:
    """(fuente, skew) para italica: variante real si existe, si no oblicua sintetica."""
    if fname in _ITALIC:
        return _ITALIC[fname], 0.0
    if fname.endswith("Italic"):
        return fname, 0.0
    return fname, -0.18


# ---------------------------------------------------------------- kerning (shaper de skia)
class _Kerner:
    """Kerning por par de letras via skia.textlayout (HarfBuzz). Cae a 0 si no hay shaper."""

    def __init__(self):
        self.ok = True
        self._fc = None
        self._prov = None
        self._reg: set[str] = set()
        self._pairs: dict[tuple, float] = {}
        self._uni = None

    def _setup(self):
        tl = skia.textlayout
        self._prov = tl.TypefaceFontProvider()
        self._fc = tl.FontCollection()
        self._fc.setDefaultFontManager(self._prov)
        self._uni = skia.Unicodes.ICU.Make()

    def _width(self, fname: str, text: str) -> float:
        tl = skia.textlayout
        if fname not in self._reg:
            self._prov.registerTypeface(assets.typeface(fname), fname)
            self._reg.add(fname)
        ts = tl.TextStyle()
        ts.setFontFamilies([fname])
        ts.setFontSize(REF)
        ps = tl.ParagraphStyle()
        ps.setTextStyle(ts)
        b = tl.ParagraphBuilder(ps, self._fc, self._uni)
        b.addText(text)
        p = b.Build()
        p.layout(1e6)
        return float(p.MaxIntrinsicWidth)

    def pair(self, fname: str, a: str, b: str) -> float:
        """Ajuste (px a tamano REF) entre a y b."""
        if not self.ok or a.isspace() or b.isspace():
            return 0.0
        k = (fname, a, b)
        v = self._pairs.get(k)
        if v is not None:
            return v
        try:
            if self._fc is None:
                self._setup()
            v = self._width(fname, a + b) - self._width(fname, a) - self._width(fname, b)
            if abs(v) < 0.05 or abs(v) > REF * 0.5:
                v = 0.0
        except Exception:  # pragma: no cover - plataforma sin ICU/shaper
            self.ok = False
            v = 0.0
        self._pairs[k] = v
        return v


_KERN = _Kerner()


# ---------------------------------------------------------------- runs (palabras medidas)
@dataclass
class _Run:
    text: str
    fname: str
    size: float
    skew: float
    font: skia.Font
    glyphs: list
    xpos: list            # origen de cada glifo (px, relativo al origen de la palabra)
    adv: float            # avance total
    ink: tuple            # (l, t, r, b) tinta completa, relativa a (origen, baseline)
    cap: float            # altura de tinta sobre la baseline sin acentos (>0)
    desc: float           # descendente de tinta sin acentos (>=0)
    gink: list            # tinta por glifo (l, t, r, b) relativa al origen del glifo
    gbase: list           # tinta por glifo sin acentos (interlineado por solape horizontal)
    _blob: Any = None
    _gblobs: dict = field(default_factory=dict)
    _paths: dict = field(default_factory=dict)
    _sprites: dict = field(default_factory=dict)

    @property
    def blob(self) -> skia.TextBlob:
        if self._blob is None:
            bld = skia.TextBlobBuilder()
            bld.allocRunPosH(self.font, self.glyphs, self.xpos, 0.0)
            self._blob = bld.make()
        return self._blob

    def glyph_blob(self, i: int) -> skia.TextBlob:
        b = self._gblobs.get(i)
        if b is None:
            bld = skia.TextBlobBuilder()
            bld.allocRunPosH(self.font, [self.glyphs[i]], [0.0], 0.0)
            b = self._gblobs[i] = bld.make()
        return b

    def glyph_path(self, i: int) -> skia.Path:
        p = self._paths.get(i)
        if p is None:
            p = self.font.getPath(self.glyphs[i]) or skia.Path()
            self._paths[i] = p
        return p


@__import__("functools").lru_cache(maxsize=256)
def _gfont(fname: str, skew: float) -> skia.Font:
    f = assets.font(fname, _GREF)
    if skew:
        f.setSkewX(skew)
    return f


def _glyph_ink(fname: str, skew: float, text: str) -> list:
    f = _gfont(fname, skew)
    g = f.textToGlyphs(text)
    return [(r.left(), r.top(), r.right(), r.bottom()) for r in f.getBounds(g)]


def _run(text: str, fname: str, size: float, tracking: float = 0.0, skew: float = 0.0) -> _Run:
    # tamano casi exacto en la llave: dos items con tamanos parecidos no comparten run (sin
    # dependencia sub-pixel del orden de render entre workers)
    key = (text, fname, round(size, 4), round(tracking, 5), round(skew, 4))
    return _RUNS.get_or(key, lambda: _make_run(text, fname, size, tracking, skew))


def _make_run(text, fname, size, tracking, skew) -> _Run:
    f = assets.font(fname, size)
    if skew:
        f.setSkewX(skew)
    glyphs = list(f.textToGlyphs(text))
    widths = list(f.getWidths(glyphs)) if glyphs else []
    k = size / _GREF
    gi = [(l * k, t * k, r * k, b * k) for (l, t, r, b) in _glyph_ink(fname, skew, text)]
    base = _strip_marks(text)
    gb = _glyph_ink(fname, skew, base) if len(base) == len(text) else None
    xpos, x = [], 0.0
    for i, ch in enumerate(text):
        xpos.append(x)
        x += widths[i]
        if i + 1 < len(text):
            x += _KERN.pair(fname, ch, text[i + 1]) * size / REF + tracking * size
    adv = x
    if gi:
        l = min(xpos[i] + g[0] for i, g in enumerate(gi) if g[2] > g[0]) if any(g[2] > g[0] for g in gi) else 0.0
        r = max(xpos[i] + g[2] for i, g in enumerate(gi)) if gi else adv
        t = min(g[1] for g in gi)
        b = max(g[3] for g in gi)
    else:
        l, t, r, b = 0.0, 0.0, adv, 0.0
    src = [(a * k, bb * k, c * k, d * k) for (a, bb, c, d) in gb] if gb else gi
    cap = max((-g[1] for g in src if g[3] > g[1]), default=size * 0.7)
    desc = max((g[3] for g in src if g[3] > g[1]), default=0.0)
    return _Run(text, fname, size, skew, f, glyphs, xpos, adv, (l, t, r, b), cap, max(0.0, desc), gi, src)


def _space(fname: str, size: float) -> float:
    return assets.font(fname, size).measureText(" ")


# ---------------------------------------------------------------- estilo de pintado
@dataclass
class _Look:
    shadow: float = 0.7
    boost: float = 1.0
    shadow_color: Any = "#000000"
    stroke: float = 0.0          # ancho total del trazo en px reales
    stroke_color: Any = "#000000"
    glow: float = 0.0
    glow_color: Any = None
    max_sigma: float = 40.0      # tope del desenfoque (px reales) para texto enorme


def _blur(sigma: float):
    return skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(0.3, float(sigma)))


_SPRITE_MIN = 190.0   # px: por encima skia dibuja glifos como paths y el blur no se cachea


def _halo_sprite(run: "_Run", glyph, look: _Look, color, kind: str):
    """Sombra (kind='shadow') o resplandor ('glow') de una palabra/glifo grande pre-renderizada a
    media resolucion. -> (imagen, rect destino relativo al origen). Se cachea en el run."""
    key = (kind, glyph, str(look.shadow_color if kind == "shadow" else (look.glow_color or color)),
           round(look.max_sigma, 1))
    hit = run._sprites.get(key)
    if hit is not None:
        return hit
    fs = run.size
    if glyph is None:
        blob, (l, t, r, b) = run.blob, run.ink
    else:
        blob, (l, t, r, b) = run.glyph_blob(glyph), run.gink[glyph]
    if kind == "shadow":
        sig = min(0.17 * fs, look.max_sigma)
        pad = 3.0 * sig + 0.06 * fs
    else:
        sig = min(0.22 * fs, look.max_sigma * 0.85)
        pad = 3.0 * sig
    rect = skia.Rect.MakeLTRB(l - pad, t - pad, r + pad, b + pad)
    k = 0.5
    w, h = max(2, int(math.ceil(rect.width() * k))), max(2, int(math.ceil(rect.height() * k)))
    surf = C.offscreen(w, h)
    cv = surf.getCanvas()
    cv.clear(0)
    cv.scale(k, k)
    cv.translate(-rect.left(), -rect.top())
    if kind == "shadow":
        # alfas relativas horneadas con la intensidad maxima (se escala al dibujar)
        sc = look.shadow_color
        p = C.fill(sc, 0.36 / 0.46)
        p.setMaskFilter(_blur(sig))
        cv.drawTextBlob(blob, 0, 0.06 * fs, p)
        p = C.fill(sc, 1.0)
        p.setMaskFilter(_blur(0.032 * fs))
        cv.drawTextBlob(blob, 0, 0.03 * fs, p)
    else:
        p = C.fill(look.glow_color or color, 1.0)
        p.setMaskFilter(_blur(sig))
        cv.drawTextBlob(blob, 0, 0, p)
    hit = (surf.makeImageSnapshot(), rect)
    run._sprites[key] = hit
    return hit


_ALL_PARTS = frozenset(("shadow", "glow", "stroke", "fill"))


def _paint_blob(canvas, blob, x, y, look: _Look, fs, color, a, blur=0.0, shader=None, draft=False,
                shadow_only=False, no_shadow=False, run=None, glyph=None, parts=None):
    """Pinta un blob por capas. `parts` limita a un subconjunto de {shadow, glow, stroke, fill}
    (para pintar por pasadas: todas las sombras de una linea antes que los rellenos)."""
    if a <= 0.003:
        return
    if parts is None:
        parts = {"shadow"} if shadow_only else (_ALL_PARTS - {"shadow"} if no_shadow else _ALL_PARTS)
    sprite = run is not None and not draft and fs >= _SPRITE_MIN
    if "shadow" in parts and look.shadow > 0:
        s = look.shadow * look.boost
        if sprite:
            img, rect = _halo_sprite(run, glyph, look, color, "shadow")
            p = skia.Paint(Alphaf=ease.clamp(min(1.0, 0.46 * s) * a))
            canvas.drawImageRect(img, rect.makeOffset(x, y), C.SAMPLING_FAST, p)
        else:
            sc = look.shadow_color
            if not draft:
                p = C.fill(sc, min(1.0, 0.36 * s) * a)
                p.setMaskFilter(_blur(min(0.17 * fs, look.max_sigma) + blur))
                canvas.drawTextBlob(blob, x, y + 0.06 * fs, p)
            p = C.fill(sc, min(1.0, 0.46 * s) * a)
            p.setMaskFilter(_blur(0.032 * fs + blur * 0.7))
            canvas.drawTextBlob(blob, x, y + 0.03 * fs, p)
    if "glow" in parts and look.glow > 0:
        if sprite:
            img, rect = _halo_sprite(run, glyph, look, color, "glow")
            p = skia.Paint(Alphaf=ease.clamp(min(1.0, 0.55 * look.glow) * a))
            canvas.drawImageRect(img, rect.makeOffset(x, y), C.SAMPLING_FAST, p)
        elif not draft:
            p = C.fill(look.glow_color or color, min(1.0, 0.55 * look.glow) * a)
            p.setMaskFilter(_blur(min(0.22 * fs, look.max_sigma * 0.85) + blur))
            canvas.drawTextBlob(blob, x, y, p)
    if "stroke" in parts and look.stroke > 0:
        p = C.stroke(look.stroke_color, look.stroke, a)
        if blur > 0.3:
            p.setMaskFilter(_blur(blur))
        canvas.drawTextBlob(blob, x, y, p)
    if "fill" in parts:
        p = C.fill(color, a)
        if shader is not None:
            p.setShader(shader)
        if blur > 0.3:
            p.setMaskFilter(_blur(blur))
        canvas.drawTextBlob(blob, x, y, p)


def _draw_run(canvas, run: _Run, ox, oy, look: _Look, color, alpha, dx=0.0, dy=0.0, sc=1.0,
              rot=0.0, blur=0.0, shader=None, glyph=None, draft=False, no_shadow=False,
              shadow_only=False, pivot: float = 0.5, origin=None, parts=None, clip=None):
    """Dibuja una palabra (o un glifo) con origen en (ox, oy) = baseline izquierda.

    Las transformaciones (dx, dy, escala, rotacion) pivotean en la tinta (`pivot` = fraccion
    horizontal: 0 = borde izquierdo, 0.5 = centro) o en `origin` (x, y) si se da (p. ej. el
    centro de la linea para animaciones de linea completa). `clip` = rect (sin transformar).
    Con alpha parcial y todas las capas usa un layer acotado para no oscurecer el relleno.
    """
    if alpha <= 0.003:
        return
    if glyph is None:
        blob, gx = run.blob, 0.0
        l, t, r, b = run.ink
    else:
        blob, gx = run.glyph_blob(glyph), run.xpos[glyph]
        l, t, r, b = run.gink[glyph]
    x0 = ox + gx
    if origin is not None:
        cx, cy = origin
    else:
        cx, cy = x0 + l + (r - l) * pivot, oy + (t + b) * 0.5
    canvas.save()
    if clip is not None:
        canvas.clipRect(clip, True)
    if dx or dy or sc != 1.0 or rot:
        canvas.translate(cx + dx, cy + dy)
        if rot:
            canvas.rotate(rot)
        if sc != 1.0:
            canvas.scale(sc, sc)
        canvas.translate(-cx, -cy)
    fs = run.size
    whole = parts is None and not shadow_only
    layer = whole and (look.shadow > 0 or look.stroke > 0 or look.glow > 0) and alpha < 0.985
    a = alpha
    if layer:
        pad = fs * 0.55 + blur * 3
        canvas.saveLayerAlpha(skia.Rect.MakeLTRB(x0 + l - pad, oy + t - pad, x0 + r + pad,
                                                 oy + b + pad * 1.3), int(alpha * 255 + 0.5))
        a = 1.0
    _paint_blob(canvas, blob, x0, oy, look, fs, color, a, blur, shader, draft,
                shadow_only=shadow_only, no_shadow=no_shadow, run=run, glyph=glyph, parts=parts)
    if layer:
        canvas.restore()
    canvas.restore()


def _draw_units(canvas, units: list, draft: bool) -> None:
    """Pinta unidades (palabras/glifos) por pasadas: primero todas las sombras, luego glow,
    contornos y rellenos. Asi la sombra difusa de una letra no mancha el borde de la vecina.

    Cada unidad es un dict con los argumentos de _draw_run (run, ox, oy, look, color, alpha y
    opcionales). Las unidades a medio aparecer (alpha < 1) se pintan completas en un layer
    propio, entre las sombras y los rellenos de las opacas.
    """
    if not units:
        return
    opaque = [u for u in units if u["alpha"] >= 0.985]
    trans = [u for u in units if 0.003 < u["alpha"] < 0.985]

    def go(u, parts):
        lk = u["look"]
        if parts is not None:
            if "shadow" in parts and (lk.shadow <= 0 or u.get("no_shadow")):
                return
            if "glow" in parts and lk.glow <= 0:
                return
            if "stroke" in parts and lk.stroke <= 0:
                return
        kw = {k: v for k, v in u.items() if k not in ("run", "ox", "oy", "look", "color", "alpha", "word")}
        _draw_run(canvas, u["run"], u["ox"], u["oy"], lk, u["color"], u["alpha"], draft=draft,
                  parts=parts, **kw)

    for u in opaque:
        go(u, {"shadow"})
    for u in trans:
        go(u, None)
    for part in ("glow", "stroke", "fill"):
        for u in opaque:
            go(u, {part})


def _soft_scale(sc: float, width: float, pivot: float, room: tuple | None) -> float:
    """Limita suavemente el sobreimpulso de escala (>1) para que la palabra no invada el
    espacio de sus vecinas: la expansion hacia cada lado no pasa de `room` (px)."""
    if sc <= 1.0 or room is None or width <= 0:
        return sc
    rl, rr = room
    lim = float("inf")
    if pivot > 1e-3:
        lim = min(lim, rl / (pivot * width))
    if pivot < 1 - 1e-3:
        lim = min(lim, rr / ((1 - pivot) * width))
    if lim == float("inf"):
        return sc
    if lim <= 1e-4:
        return 1.0
    return 1.0 + lim * math.tanh((sc - 1.0) / lim)


def _rooms(lines_words: list, em: float = 0.14) -> dict:
    """Espacio libre a cada lado de cada palabra (id -> (izq, der)) para `_soft_scale`: cada
    vecina puede invadir a lo mas la mitad del hueco menos `em` (queda un hueco >= em)."""
    out = {}
    for ws in lines_words:
        ws = sorted(ws, key=lambda w: w.ox + w.run.ink[0])
        for i, w in enumerate(ws):
            s = w.run.size
            rl = rr = 1e9
            if i > 0:
                p = ws[i - 1]
                gap = (w.ox + w.run.ink[0]) - (p.ox + p.run.ink[2])
                rl = max(0.0, (gap - em * min(s, p.run.size)) / 2)
            if i + 1 < len(ws):
                n = ws[i + 1]
                gap = (n.ox + n.run.ink[0]) - (w.ox + w.run.ink[2])
                rr = max(0.0, (gap - em * min(s, n.run.size)) / 2)
            out[id(w)] = (rl, rr)
    return out


# ---------------------------------------------------------------- animaciones
def _in_state(kind: str, u: float, fs: float, amp: float = 1.0, final: bool = True):
    """Estado de entrada de una unidad (palabra/letra) a `u` s de su aparicion.

    -> (alpha, dx, dy, escala, blur). u < 0 => invisible.
    """
    if u < 0:
        return 0.0, 0.0, 0.0, 1.0, 0.0
    if kind == "pop":
        s = 0.5 + 0.5 * ease.spring(u, freq=3.1, damping=0.56 + 0.2 * (1 - amp))
        return ease.out_cubic(u / 0.09), 0.0, 0.0, s, 0.0
    if kind == "rise":
        p = u / 0.5
        return (ease.out_cubic(u / 0.22), 0.0, (1 - ease.out_expo(p)) * 0.55 * fs * amp,
                1.0, (1 - ease.out_cubic(u / 0.2)) * 0.03 * fs if final else 0.0)
    if kind == "word_stagger":
        s = 0.9 + 0.1 * ease.spring(u, freq=3.4, damping=0.55)
        return (ease.out_cubic(u / 0.16), 0.0, (1 - ease.out_cubic(u / 0.38)) * 0.32 * fs * amp, s, 0.0)
    if kind == "letter_stagger":
        p = u / 0.45
        return (ease.out_quad(u / 0.12), 0.0, (1 - ease.out_back(p, 1.25)) * 0.8 * fs * (0.6 + 0.4 * amp),
                1.0, 0.0)
    if kind in ("slide_left", "slide_right"):
        sgn = -1.0 if kind == "slide_left" else 1.0
        return (ease.out_cubic(u / 0.24), sgn * (1 - ease.out_expo(u / 0.55)) * 0.9 * fs * amp, 0.0, 1.0,
                (1 - ease.out_cubic(u / 0.25)) * 0.04 * fs if final else 0.0)
    if kind == "fade":
        p = u / 0.55
        return (ease.smoothstep(p), 0.0, (1 - ease.out_cubic(u / 0.7)) * 0.12 * fs * amp, 1.0,
                (1 - ease.out_cubic(p)) * 0.085 * fs if final else 0.0)
    if kind == "scale_down":
        p = u / 0.42
        return (ease.out_quad(u / 0.16), 0.0, 0.0, 1 + 0.8 * amp * (1 - ease.out_expo(p)),
                (1 - ease.out_cubic(p)) * 0.05 * fs if final else 0.0)
    if kind == "type":
        return 1.0, 0.0, 0.0, 1 + 0.16 * (1 - ease.out_cubic(u / 0.08)), 0.0
    # mask: la linea entera sube desde su baseline (se resuelve en el render de lineas)
    return 1.0, 0.0, 0.0, 1.0, 0.0


def _out_state(kind: str, p: float, fs: float, k: int = 0, n: int = 1):
    """Estado de salida (linea k de n) con p en [0,1]. -> (alpha, dy, escala)."""
    if p <= 0 or kind == "none":
        return (0.0 if (kind == "none" and p >= 1) else 1.0), 0.0, 1.0
    if kind == "slide_up":
        span = 0.35
        pk = ease.clamp((p * (1 + span * (n - 1)) - span * k) if n > 1 else p)
        return 1 - ease.in_quad(pk), -ease.in_cubic(pk) * 0.7 * fs, 1.0
    if kind == "pop_out":
        if p < 0.3:
            return 1.0, 0.0, 1 + 0.05 * ease.out_quad(p / 0.3)
        q = (p - 0.3) / 0.7
        return 1 - ease.in_quad(q), 0.0, 1.05 - 0.45 * ease.in_cubic(q)
    return 1 - ease.in_out_quad(p), 0.0, 1.0


_TOUT = {"fade": 0.28, "pop_out": 0.26, "slide_up": 0.32, "none": 0.0}


# ---------------------------------------------------------------- parametros del item
def _resolve_font(name: str | None, role: str | None, ctx) -> str:
    if name:
        return name if name in assets.FONT_FILES else name.replace(" ", "-")
    fonts = ctx.palette.get("fonts", {})
    return fonts.get(role or "title") or assets.DEFAULT_FONT


def _params(item: dict, preset: str, ctx) -> dict:
    P = dict(PRESETS.get(preset, PRESETS["title_pop"]))
    for k, v in item.items():
        if v is not None and not k.startswith("_"):
            P[k] = v
    pal = ctx.palette
    P["font"] = _resolve_font(item.get("font") or PRESETS.get(preset, {}).get("font"), P.get("role"), ctx)
    P["color"] = item.get("color") or pal.get(P.get("color_key", "primary"), "#FFFFFF")
    P["accent"] = item.get("accent") or pal.get("accent", "#FFD23F")
    P["intensity"] = float(item.get("intensity", 1.0) if item.get("intensity") is not None else 1.0)
    if P.get("align") not in ("left", "center", "right"):
        P["align"] = "center"
    if P.get("x") is None:
        P["x"] = {"left": SAFE["left"] + 0.02, "right": SAFE["right"] - 0.02}.get(P["align"], 0.5)
    if P.get("anim_in") not in ANIM_IN:
        P["anim_in"] = PRESETS.get(preset, {}).get("anim_in", "pop")
    if P.get("anim_out") not in ANIM_OUT:
        P["anim_out"] = PRESETS.get(preset, {}).get("anim_out", "fade")
    return P


def _look(P: dict, ctx, fs: float, color) -> _Look:
    sh = float(P.get("shadow", 0.7) or 0.0)
    dark_text = C.luminance(color) < 0.4
    lk = _Look(shadow=sh * (0.55 if dark_text else 1.0),
               shadow_color="#FFFFFF" if dark_text else "#000000",
               stroke=2.0 * ctx.px(float(P.get("stroke") or 0.0)),
               stroke_color=P.get("stroke_color") or ("#FFFFFF" if dark_text else "#000000"),
               glow=float(P.get("glow") or 0.0), glow_color=P.get("glow_color"), max_sigma=ctx.px(40))
    return lk


def _bg_lum(ctx, rect: skia.Rect) -> float | None:
    """Luminancia media (0..1) del cuadro base bajo `rect`, muestreada en una rejilla de 16x8
    (drawImageRect a una superficie diminuta). Se cachea por cuadro y rect en ctx._cache."""
    img = ctx.base_image() if hasattr(ctx, "base_image") else None
    if img is None:
        return None
    cache = ctx._cache.get("text_bg")
    if cache is None or cache.get("n") != ctx.n or cache.get("img") is not img:
        cache = {"n": ctx.n, "img": img, "v": {}}
        ctx._cache["text_bg"] = cache
    L = max(0.0, rect.left())
    T = max(0.0, rect.top())
    R = min(float(img.width()), rect.right())
    B = min(float(img.height()), rect.bottom())
    if R - L < 4 or B - T < 4:
        return None
    key = (int(L) >> 3, int(T) >> 3, int(R) >> 3, int(B) >> 3)
    v = cache["v"].get(key)
    if v is None:
        try:
            surf = C.offscreen(16, 8)
            cv = surf.getCanvas()
            cv.drawImageRect(img, skia.Rect.MakeLTRB(L, T, R, B), skia.Rect.MakeWH(16, 8),
                             C.SAMPLING_FAST, None, skia.Canvas.kFast_SrcRectConstraint)
            a = surf.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)[..., :3]
            a = a.astype("float32")
            v = float((0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]).mean() / 255.0)
        except Exception:
            v = -1.0
        cache["v"][key] = v
    return None if v < 0 else v


def _bg_boost(ctx, rect: skia.Rect, color, item: dict | None = None) -> float:
    """Refuerzo de sombra segun la luminancia del fondo bajo el texto (1.0 .. 1.7).

    `item['_bg_lum']` (0..1) lo fija quien sabe que fondo hay (p. ej. cutaways sobre su escena)."""
    if C.luminance(color) < 0.4:
        return 1.0
    lum = None
    if item is not None and item.get("_bg_lum") is not None:
        try:
            lum = float(item["_bg_lum"])
        except Exception:
            lum = None
    if lum is None:
        lum = _bg_lum(ctx, rect)
    if lum is None:
        return 1.0
    return 1.0 + 0.7 * ease.clamp((lum - 0.45) / 0.4)


def _plate(canvas, rect: skia.Rect, size: float, alpha: float) -> None:
    """Placa oscura muy difusa detras de una linea clara sobre fondo claro (legibilidad)."""
    if alpha <= 0.003:
        return
    p = C.fill("#000000", alpha)
    p.setMaskFilter(_blur(0.5 * size))
    r = rect.makeOutset(0.25 * size, 0.12 * size)
    canvas.drawRRect(skia.RRect.MakeRectXY(r, 0.4 * size, 0.4 * size), p)


# ---------------------------------------------------------------- layout
@dataclass
class _W:                     # palabra colocada
    run: _Run
    ox: float                 # origen x (baseline izquierda), px absolutos
    oy: float                 # baseline y
    line: int
    idx: int                  # indice global (orden de lectura)
    norm: str
    accent: bool
    color: Any


@dataclass
class _L:                     # linea colocada
    words: list
    size: float
    fname: str
    scale: float
    ink: tuple                # (l, t, r, b) absoluto
    baseline: float
    kind: str = "text"        # text | pill


@dataclass
class _B:                     # bloque colocado
    lines: list
    words: list
    rect: skia.Rect           # tinta en reposo (sin animacion)
    fs: float                 # tamano base (px)
    center: tuple
    extra: dict = field(default_factory=dict)


@dataclass
class _Spec:                  # linea logica antes de medir
    words: list               # [(texto, es_acento)]
    fname: str
    scale: float = 1.0
    color: Any = None
    skew: float = 0.0
    tracking: float = 0.0
    align: str | None = None
    kind: str = "text"
    accent_font: str | None = None   # fuente para palabras acento (p. ej. italica del serif)
    accent_skew: float = 0.0

    def wfont(self, accent: bool) -> tuple[str, float]:
        if accent and self.accent_font:
            return self.accent_font, self.accent_skew
        return self.fname, self.skew

    def clone(self, words=None) -> "_Spec":
        return _Spec(list(self.words if words is None else words), self.fname, self.scale, self.color,
                     self.skew, self.tracking, self.align, self.kind, self.accent_font, self.accent_skew)


def _accent_set(P: dict) -> set:
    aw = P.get("accent_words") or []
    if isinstance(aw, str):
        aw = [aw]
    return {_norm(w) for w in aw if _norm(w)}


def _split_words(text: str, acc: set) -> list:
    out = []
    for w in text.split():
        out.append((w, _norm(w) in acc))
    return out


def _specs_from_item(item: dict, P: dict, preset: str) -> list[_Spec]:
    acc = _accent_set(P)
    case = P.get("case")
    base_font = P["font"]
    tracking = float(P.get("tracking", 0.0) or 0.0)
    specs: list[_Spec] = []
    lines = item.get("lines")
    if lines:
        for ln in lines:
            if isinstance(ln, str):
                ln = {"text": ln}
            fname = _resolve_font(ln.get("font"), None, None) if ln.get("font") else base_font
            skew = 0.0
            if ln.get("italic"):
                fname, skew = _italic(fname)
            txt = _clean(_apply_case(ln.get("text", ""), ln.get("case", case)), fname)
            if not txt:
                continue
            specs.append(_Spec(_split_words(txt, acc), fname, float(ln.get("scale", 1.0) or 1.0),
                               ln.get("color"), skew, float(ln.get("tracking", tracking) or 0.0),
                               ln.get("align")))
        return specs
    raw = _apply_case(str(item.get("text", "") or ""), case)
    for part in raw.split("\n"):
        txt = _clean(part, base_font)
        if txt:
            specs.append(_Spec(_split_words(txt, acc), base_font, 1.0, None, 0.0, tracking))
    if preset == "serif_elegant":
        for sp in specs:
            sp.accent_font, sp.accent_skew = _italic(sp.fname)
    return specs


def _word_gap(spec: _Spec, size: float, space_k: float = 1.0) -> float:
    """Espacio entre palabras a `size` px. El tracking abre las letras, asi que el espacio
    crece con el (si no, 'A DOS CUADRAS' se lee pegado)."""
    return _space(spec.fname, size) * space_k + 1.5 * spec.tracking * size


def _spec_w(spec: _Spec, size: float = REF, space_k: float = 1.0) -> float:
    """Ancho de tinta de una linea a `size` (escala de la linea incluida)."""
    s = size * spec.scale
    if not spec.words:
        return 0.0
    sp = _word_gap(spec, s, space_k)
    x, l, r = 0.0, None, 0.0
    for i, (w, _a) in enumerate(spec.words):
        fn, sk = spec.wfont(_a)
        run = _run(w, fn, s, spec.tracking, sk)
        if l is None:
            l = x + run.ink[0]
        r = x + run.ink[2]
        x += run.adv + sp
    return r - (l or 0.0)


def _is_func(w: str) -> bool:
    return _norm(w) in _FUNC_END


def _balanced_split(widths: list[float], space: float, k: int, words: list | None = None) -> list[int]:
    """Parte palabras en k lineas minimizando el ancho maximo, con reglas del espanol: una
    linea (salvo la ultima) no termina en palabra funcional ('disponibles en el | link' no).
    -> indices de inicio de cada linea."""
    import itertools
    n = len(widths)
    k = max(1, min(k, n))
    if k == 1:
        return [0]
    pre = [0.0]
    for w in widths:
        pre.append(pre[-1] + w)

    def lw(i, j):  # palabras [i, j)
        return pre[j] - pre[i] + space * (j - i - 1)

    func = [_is_func(w) for w in words] if words is not None and len(words) == n else [False] * n
    avg = lw(0, n) / k
    if math.comb(n - 1, k - 1) <= 6000:
        best, bc = None, float("inf")
        for cuts in itertools.combinations(range(1, n), k - 1):
            b = (0,) + cuts + (n,)
            ws = [lw(a, c) for a, c in zip(b, b[1:])]
            cost = max(ws)
            # desempate: la linea de arriba un poco mas corta (forma de piramide)
            cost += 1e-4 * sum(ws[m] for m in range(0, k, 2))
            cost += sum(0.45 * avg for c in cuts if func[c - 1])
            if cost < bc:
                best, bc = list(b[:-1]), cost
        return best
    # textos muy largos: DP clasico de minimo del maximo
    INF = float("inf")
    best = [[INF] * (n + 1) for _ in range(k + 1)]
    cut = [[0] * (n + 1) for _ in range(k + 1)]
    best[0][0] = 0.0
    for m in range(1, k + 1):
        for j in range(1, n + 1):
            for i in range(m - 1, j):
                if best[m - 1][i] == INF:
                    continue
                v = max(best[m - 1][i], lw(i, j)) + (0.45 * avg if j < n and func[j - 1] else 0.0)
                if v < best[m][j]:
                    best[m][j], cut[m][j] = v, i
    starts, j = [], n
    for m in range(k, 0, -1):
        i = cut[m][j]
        starts.append(i)
        j = i
    return sorted(starts)


def _wrap_spec(spec: _Spec, k: int, space_k: float = 1.0) -> list[_Spec]:
    if k <= 1 or len(spec.words) <= 1:
        return [spec]
    s = REF * spec.scale
    widths = [_run(w, *_wf(spec, a, s)).adv for w, a in spec.words]
    starts = _balanced_split(widths, _word_gap(spec, s, space_k), k, [w for w, _ in spec.words])
    out = []
    for a, b in zip(starts, starts[1:] + [len(spec.words)]):
        if b > a:
            out.append(spec.clone(spec.words[a:b]))
    return out


def _wf(spec: _Spec, accent: bool, size: float) -> tuple:
    fn, sk = spec.wfont(accent)
    return fn, size, spec.tracking, sk


def _usable(P: dict, ctx) -> tuple[float, float]:
    """(ancho util px, alto util px) para el ancla del item."""
    x = ease.clamp(float(P["x"]), SAFE["left"], SAFE["right"])
    if P["align"] == "left":
        w = (SAFE["right"] - x)
    elif P["align"] == "right":
        w = (x - SAFE["left"])
    else:
        w = 2 * min(x - SAFE["left"], SAFE["right"] - x)
    w = max(0.3, w) * float(P.get("width", 1.0) or 1.0)
    grow = 1.0 + max(0.0, float(P.get("drift", 0.0) or 0.0))   # el empuje lento no debe salir de SAFE
    return w * ctx.W / grow, (SAFE["bottom"] - SAFE["top"]) * ctx.H / grow


def _func_only(spec: _Spec) -> bool:
    return bool(spec.words) and all(_is_func(w) for w, _ in spec.words)


def _fit(specs: list[_Spec], P: dict, ctx, maxw: float, auto_wrap: bool = True) -> tuple[float, list[_Spec]]:
    """Tamano base y lineas finales (con envoltura balanceada).

    Con `size` explicito solo se envuelve una linea logica unica o si `auto_wrap`; nunca se
    deja una linea hecha solo de palabras funcionales (en ese caso se reduce la linea)."""
    W = ctx.W
    size = P.get("size")
    space_k = float(P.get("space", 1.0))
    if size:
        fs = float(size) * W
        out = []
        can_wrap = auto_wrap or len(specs) == 1
        for sp in specs:
            parts = [sp]
            k = 1
            while can_wrap and k < len(sp.words) and max(_spec_w(x, fs, space_k) for x in parts) > maxw:
                k += 1
                cand = _wrap_spec(sp, k, space_k)
                if any(_func_only(c) for c in cand):
                    break
                parts = cand
            out.extend(parts)
        # lo que aun no cabe (palabra enorme o linea sin envolver) se reduce (nunca se corta)
        for sp in out:
            w = _spec_w(sp, fs, space_k)
            if w > maxw:
                sp.scale *= maxw / w * 0.995
        return fs, out
    maxfs = float(P.get("max_size", 0.12)) * W
    minfs = float(P.get("min_size", 0.05)) * W
    if auto_wrap and len(specs) == 1 and len(specs[0].words) > 1:
        n = sum(len(w) for w, _ in specs[0].words) + len(specs[0].words) - 1
        k = max(1, min(int(P.get("max_lines", 3)), math.ceil(n / float(P.get("cpl", 14)))))
        specs = _wrap_spec(specs[0], k, space_k)

    def fit_size(sps):
        return min(maxfs, min(maxw / max(1e-3, _spec_w(s, REF, space_k) / REF) for s in sps))

    fs = fit_size(specs)
    # si queda muy chico, envolver mas (hasta max_lines) antes de encoger
    while auto_wrap and fs < minfs and len(specs) < int(P.get("max_lines", 3)):
        longest = max(range(len(specs)), key=lambda i: _spec_w(specs[i], REF, space_k))
        if len(specs[longest].words) < 2:
            break
        parts = _wrap_spec(specs[longest], 2, space_k)
        if any(_func_only(c) for c in parts):
            break
        specs = specs[:longest] + parts + specs[longest + 1:]
        fs = fit_size(specs)
    return fs, specs


def _stack(specs: list[_Spec], fs: float, P: dict, ctx, base_color, accent) -> tuple[list[_L], list[_W]]:
    """Coloca lineas apiladas por tinta real. Coordenadas locales: x centrado en 0, y=0 arriba."""
    gap_em = float(P.get("gap", 0.1))
    space_k = float(P.get("space", 1.0))
    lines: list[_L] = []
    words: list[_W] = []
    prev = None
    idx = 0
    for li, sp in enumerate(specs):
        size = fs * sp.scale
        sp_w = _word_gap(sp, size, space_k)
        runs = [(_run(w, *_wf(sp, a, size)), w, a) for w, a in sp.words]
        x = 0.0
        placed = []
        for r, w, a in runs:
            placed.append((r, x, w, a))
            x += r.adv + sp_w
        l = placed[0][1] + placed[0][0].ink[0]
        r_ = placed[-1][1] + placed[-1][0].ink[2]
        cap = max(r.cap for r, *_ in placed)
        desc = max(r.desc for r, *_ in placed)
        top_full = -min(r.ink[1] for r, *_ in placed)
        bot_full = max(r.ink[3] for r, *_ in placed)
        if prev is None:
            base = top_full
        else:
            g = gap_em * min(prev["size"], size)
            base = prev["base"] + max(prev["desc"] + g + cap, prev["bot"] + 0.3 * g + top_full)
        prev = {"base": base, "desc": desc, "bot": bot_full, "size": size}
        line_words = []
        for r, x0, w, a in placed:
            col = accent if a else (sp.color or base_color)
            ww = _W(r, x0, base, li, idx, _norm(w), a, col)
            idx += 1
            line_words.append(ww)
            words.append(ww)
        lines.append(_L(line_words, size, sp.fname, sp.scale, (l, base - top_full, r_, base + bot_full), base,
                        sp.kind))
        lines[-1].align = sp.align  # type: ignore[attr-defined]
    return lines, words


def _shift_line(ln: _L, dy: float) -> None:
    l, t, r, b = ln.ink
    ln.ink = (l, t + dy, r, b + dy)
    ln.baseline += dy
    for w in ln.words:
        w.oy += dy


def _relead(lines: list[_L], gap_em: float) -> None:
    """Interlineado por solape horizontal (cascade): la distancia entre baselines sale de los
    glifos que de verdad quedan uno sobre otro, no del descendente maximo de toda la linea
    (la 'y' de 'y como se ven' no debe despegar 'tus dientes' si no esta encima)."""
    for i in range(1, len(lines)):
        a, b = lines[i - 1], lines[i]
        g = gap_em * min(a.size, b.size)
        m = 0.06 * min(a.size, b.size)
        ga = []
        for w in a.words:
            for j, (gl, gt, gr, gb) in enumerate(w.run.gink):
                if gr <= gl:
                    continue
                bb = w.run.gbase[j] if j < len(w.run.gbase) else w.run.gink[j]
                x0 = w.ox + w.run.xpos[j]
                ga.append((x0 + gl - m, x0 + gr + m, max(0.0, bb[3]), max(0.0, gb)))
        need = g + max(w.run.cap for w in b.words)
        for w in b.words:
            for j, (gl, gt, gr, gb) in enumerate(w.run.gink):
                if gr <= gl:
                    continue
                bb = w.run.gbase[j] if j < len(w.run.gbase) else w.run.gink[j]
                x0 = w.ox + w.run.xpos[j]
                l2, r2 = x0 + gl, x0 + gr
                cap2, top2 = max(0.0, -bb[1]), max(0.0, -gt)
                for l1, r1, d1, bot1 in ga:
                    if r1 <= l2 or r2 <= l1:
                        continue
                    need = max(need, d1 + g + cap2, bot1 + 0.3 * g + top2)
        dy = (a.baseline + need) - b.baseline
        if abs(dy) > 1e-6:
            for ln in lines[i:]:
                _shift_line(ln, dy)


def _align_lines(lines: list[_L], align: str, offsets: list[float] | None = None) -> float:
    """Alinea las lineas (x local, bloque centrado en 0). Devuelve el ancho del bloque."""
    bw = max(l.ink[2] - l.ink[0] for l in lines)
    for i, ln in enumerate(lines):
        l, t, r, b = ln.ink
        w = r - l
        a = getattr(ln, "align", None) or align
        if offsets is not None:
            tx = -bw / 2 + offsets[i] - l
        elif a == "left":
            tx = -bw / 2 - l
        elif a == "right":
            tx = bw / 2 - r
        else:
            tx = -w / 2 - l
        for ww in ln.words:
            ww.ox += tx
        ln.ink = (l + tx, t, r + tx, b)
    return bw


def _xform_half(w: float, h: float, rot: float, grow: float) -> tuple[float, float]:
    """Semiejes de la caja que envuelve un rect w x h rotado `rot` grados y escalado `grow`."""
    c, s = abs(math.cos(math.radians(rot))), abs(math.sin(math.radians(rot)))
    return (w * c + h * s) * 0.5 * grow, (w * s + h * c) * 0.5 * grow


def _place(lines: list[_L], words: list[_W], P: dict, ctx, bw: float) -> tuple[skia.Rect, skia.Rect]:
    """Ancla el bloque (x/align, y=centro vertical) y lo mete en SAFE contando la rotacion y el
    empuje (drift) del bloque. -> (tinta en reposo sin transformar, caja transformada)."""
    top = min(l.ink[1] for l in lines)
    bot = max(l.ink[3] for l in lines)
    left = min(l.ink[0] for l in lines)
    right = max(l.ink[2] for l in lines)
    ax = float(P["x"]) * ctx.W
    if P["align"] == "left":
        tx = ax - left
    elif P["align"] == "right":
        tx = ax - right
    else:
        tx = ax - (left + right) / 2
    ty = float(P["y"]) * ctx.H - (top + bot) / 2
    rot = float(P.get("rotation", 0.0) or 0.0)
    grow = 1.0 + max(0.0, float(P.get("drift", 0.0) or 0.0))
    hw, hh = _xform_half(right - left, bot - top, rot, grow)
    cx, cy = (left + right) / 2 + tx, (top + bot) / 2 + ty
    sl, sr = SAFE["left"] * ctx.W, SAFE["right"] * ctx.W
    st, sb = SAFE["top"] * ctx.H, SAFE["bottom"] * ctx.H
    if 2 * hw >= sr - sl:
        tx += (sl + sr) / 2 - cx
    elif cx - hw < sl:
        tx += sl - (cx - hw)
    elif cx + hw > sr:
        tx -= cx + hw - sr
    if 2 * hh >= sb - st:
        ty += (st + sb) / 2 - cy
    elif cy - hh < st:
        ty += st - (cy - hh)
    elif cy + hh > sb:
        ty -= cy + hh - sb
    for ln in lines:
        l, t, r, b = ln.ink
        ln.ink = (l + tx, t + ty, r + tx, b + ty)
        ln.baseline += ty
    for w in words:
        w.ox += tx
        w.oy += ty
    rect = skia.Rect.MakeLTRB(left + tx, top + ty, right + tx, bot + ty)
    cx, cy = rect.centerX(), rect.centerY()
    return rect, skia.Rect.MakeLTRB(cx - hw, cy - hh, cx + hw, cy + hh)


def _layout_key(item: dict, preset: str, ctx) -> str:
    d = {k: v for k, v in item.items() if not k.startswith("_") and k not in
         ("start", "end", "enabled", "src", "locked", "z", "anim_out", "intensity")}
    return json.dumps([preset, ctx.W, ctx.H, ctx.style, d], sort_keys=True, default=str, ensure_ascii=False)


def _stack_scales(specs: list[_Spec], acc: set) -> list[int]:
    """Jerarquia automatica de `stack`: la linea con mas peso es grande, conectores chicos.

    Devuelve el rango de cada linea (0 = protagonista). Empates: gana la linea posterior
    (el remate suele ir al final: "te revelo / el secreto"). Una linea hecha solo de palabras
    funcionales nunca es protagonista."""
    if len(specs) < 2:
        return [0] * len(specs)
    scores = []
    for i, sp in enumerate(specs):
        ws = [w for w, _ in sp.words]
        s = 0.0
        s += 1.0 if len(ws) == 1 else 0.0
        s += 0.8 if all(w.upper() == w and any(c.isalpha() for c in w) for w in ws) else 0.0
        s += 1.2 if any(a for _, a in sp.words) else 0.0
        s -= 0.7 if _norm(ws[0]) in _FUNC else 0.0
        s -= 4.0 if _func_only(sp) else 0.0
        s -= 0.03 * sum(len(w) for w in ws)
        s += 0.05 * i
        scores.append(s)
    top = max(scores)
    order = sorted(set(round(v, 6) for v in scores), reverse=True)
    ranks = []
    for sp, s in zip(specs, scores):
        rk = 0 if s >= top - 1e-6 else min(2, order.index(round(s, 6)))
        ranks.append(rk)
        sp.scale = (1.0, 0.62, 0.5)[rk]
    return ranks


_JUSTIFY = {1: (0.92, 0.62), 2: (0.76, 0.54)}   # rango -> (fraccion del ancho del bloque, escala max)


def _justify_stack(specs: list[_Spec], ranks: list[int], fs: float, space_k: float) -> None:
    """Pila justificada: las lineas secundarias se escalan hacia el ancho de la protagonista."""
    tops = [_spec_w(sp, fs, space_k) for sp, r in zip(specs, ranks) if r == 0]
    if not tops:
        return
    wb = max(tops)
    for sp, r in zip(specs, ranks):
        if r == 0:
            continue
        frac, hi = _JUSTIFY.get(r, _JUSTIFY[2])
        w1 = _spec_w(sp, fs, space_k) / max(1e-6, sp.scale)
        sp.scale = ease.clamp(wb * frac / max(1e-6, w1), 0.34, hi)


def _auto_lines(text_words: list, k: int) -> list[list]:
    """Corta una frase en k lineas prefiriendo cortar ANTES de palabras funcionales y nunca
    despues de una ('para llevar | la edicion | de video')."""
    n = len(text_words)
    if n <= 1 or k <= 1:
        return [text_words]
    k = min(k, n)
    best, best_cost = None, float("inf")
    import itertools
    for cuts in itertools.combinations(range(1, n), k - 1):
        parts = [text_words[a:b] for a, b in zip((0,) + cuts, cuts + (n,))]
        lens = [sum(len(w) for w, _ in p) for p in parts]
        cost = max(lens) - min(lens) * 0.3
        cost -= sum(2.5 for p in parts[1:] if _norm(p[0][0]) in _FUNC)
        cost += sum(6.0 for p in parts[:-1] if _is_func(p[-1][0]))
        if cost < best_cost:
            best, best_cost = parts, cost
    return best


def _kicker_split(item: dict, specs: list[_Spec]) -> tuple[str | None, list[_Spec], bool]:
    """Etiqueta del kicker ANTES de envolver: `kicker` explicito o la primera linea explicita
    (texto con salto de linea o `lines`). Sin eso, un texto corto es la etiqueta completa
    (solo pastilla); uno largo no lleva pastilla. -> (etiqueta, resto, solo_etiqueta)."""
    lab = item.get("kicker")
    if lab and str(lab).strip():
        return str(lab), specs, False
    if len(specs) >= 2:
        return " ".join(w for w, _ in specs[0].words), specs[1:], False
    if len(specs) == 1:
        ws = [w for w, _ in specs[0].words]
        if len(ws) <= 4 and len(" ".join(ws)) <= 26:
            return " ".join(ws), [], True
    return None, specs, False


def _kicker_spec(label: str, item: dict, P: dict, fs: float, ctx, maxw: float, only: bool) -> _Spec:
    kf = _resolve_font(item.get("kicker_font") or (item.get("font") if only else None) or "Poppins-Bold",
                       None, None)
    txt = _clean(str(label).upper(), kf)
    if only:
        scale = 1.0
    else:
        scale = max(float(item.get("kicker_scale", 0.36)), 0.036 * ctx.W / max(1.0, fs))
    sp = _Spec([(w, False) for w in txt.split()], kf, scale, None, 0.0, 0.12, None, "pill")
    # la pastilla (texto + 0.8 em por lado) debe caber en el ancho util
    w = _spec_w(sp, fs) + 1.6 * fs * sp.scale
    if w > maxw:
        sp.scale *= maxw / w * 0.995
    return sp


def _build_block(item: dict, P: dict, ctx, preset: str) -> _B:
    specs = _specs_from_item(item, P, preset)
    if not specs:
        return _B([], [], skia.Rect.MakeEmpty(), 0.0, (0.0, 0.0))
    acc = _accent_set(P)
    maxw, maxh = _usable(P, ctx)
    auto_wrap = True
    label, label_only = None, False
    if preset == "kicker":
        label, specs, label_only = _kicker_split(item, specs)
    if preset in ("stack", "cascade") and not item.get("lines") and len(specs) == 1 and len(specs[0].words) > 2:
        n = len(specs[0].words)
        k = 3 if n >= 4 else 2
        specs = [specs[0].clone(ws) for ws in _auto_lines(specs[0].words, k)]
        auto_wrap = False
    ranks = None
    if preset == "stack" and not item.get("lines"):
        ranks = _stack_scales(specs, acc)
    if preset == "cascade" and not item.get("lines") and len(specs) > 1:
        specs[-1].scale = float(P.get("last_scale", 1.22))
    if preset in ("stack", "cascade", "kicker"):
        auto_wrap = False
    if specs:
        fs, specs = _fit(specs, P, ctx, maxw, auto_wrap=auto_wrap)
    else:
        fs = float(P.get("size") or 0.045) * ctx.W
    if ranks is not None and len(ranks) == len(specs):
        _justify_stack(specs, ranks, fs, float(P.get("space", 1.0)))
        # sin volver a envolver: lo que no quepa se reduce (la jerarquia ya esta decidida)
        fs, specs = _fit(specs, dict(P, size=fs / ctx.W), ctx, maxw, auto_wrap=False)
    if preset == "cascade" and len(specs) > 1:
        # el escalon consume ancho: reducir hasta que la linea mas desplazada quepa
        step = float(P.get("cascade_step", 0.1)) * ctx.W
        need = max(i * step + _spec_w(sp, fs) for i, sp in enumerate(specs))
        if need > maxw:
            room = [(maxw - i * step) / max(1e-3, _spec_w(sp, fs)) for i, sp in enumerate(specs)]
            fs *= max(0.4, min(room)) * 0.995
    if label:
        specs = [_kicker_spec(label, item, P, fs, ctx, maxw, label_only)] + specs
    rot = float(P.get("rotation", 0.0) or 0.0)
    grow = 1.0 + max(0.0, float(P.get("drift", 0.0) or 0.0))
    safe_w = (SAFE["right"] - SAFE["left"]) * ctx.W / grow
    # serif_elegant: el acento es la italica real, en el color del texto (la referencia usa
    # serif blanca); solo con `accent` explicito cambia de color
    acc_col = P["color"] if preset == "serif_elegant" and not item.get("accent") else P["accent"]
    for _attempt in range(4):
        lines, words = _stack(specs, fs, P, ctx, P["color"], acc_col)
        offsets = _cascade_offsets(lines, float(P.get("cascade_step", 0.1)) * ctx.W, maxw) \
            if preset == "cascade" else None
        bw = _align_lines(lines, P["align"] if preset != "cascade" else "left", offsets)
        if preset == "cascade":
            _relead(lines, float(P.get("gap", 0.1)))
        if label:
            _kicker_pill_ink(lines, fs)
        top = min(l.ink[1] for l in lines)
        bot = max(l.ink[3] for l in lines)
        left = min(l.ink[0] for l in lines)
        right = max(l.ink[2] for l in lines)
        hw, hh = _xform_half(right - left, bot - top, rot, 1.0)
        kx = min(1.0, safe_w / max(1.0, 2 * hw))
        ky = min(1.0, maxh * float(P.get("max_h", 0.62)) / max(1.0, 2 * hh))
        k = min(kx, ky)
        if k >= 0.999:
            break
        fs *= k * 0.985
    rect, bbox = _place(lines, words, P, ctx, bw)
    B = _B(lines, words, rect, fs, (rect.centerX(), rect.centerY()))
    B.extra["bbox"] = bbox
    B.extra["rooms"] = _rooms([[w for w in ln.words] for ln in lines if ln.kind != "pill"])
    return B


def _cascade_offsets(lines: list[_L], step: float, maxw: float) -> list[float]:
    """Escalera: cada linea entra `step` px mas a la derecha que la anterior (sin pasar maxw)."""
    for i, ln in enumerate(lines):
        if i:
            step = min(step, max(0.0, (maxw - (ln.ink[2] - ln.ink[0])) / i))
    return [i * step for i in range(len(lines))]


def _get_block(item: dict, preset: str, ctx) -> tuple[dict, _B]:
    key = _layout_key(item, preset, ctx)

    def mk():
        P = _params(item, preset, ctx)
        return P, _build_block(item, P, ctx, preset)

    P, B = _BLOCKS.get_or(key, mk)
    B.extra["key"] = key
    # campos que no cambian el layout pero si el render (no forman parte de la llave)
    P = dict(P)
    ao = item.get("anim_out")
    P["anim_out"] = ao if ao in ANIM_OUT else PRESETS.get(preset, {}).get("anim_out", "fade")
    it = item.get("intensity")
    P["intensity"] = float(it) if it is not None else 1.0
    return P, B


# ---------------------------------------------------------------- sincronia con la voz
def _match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return False


def _unit_times(norms: list[str], spoken: list | None, dur: float, step: float,
                line_of: list[int] | None = None, lead: float = 0.05) -> list[float]:
    """Tiempo de aparicion (s locales) de cada palabra del item.

    Con `spoken` (_words) alinea por texto normalizado; lo no encontrado se interpola.
    Sin voz: escalonado con `step` (pausa extra al cambiar de linea).
    """
    n = len(norms)
    if n == 0:
        return []
    last_ok = max(0.0, dur - 0.45)
    if spoken:
        sp = [(_norm(w.get("text", "")), float(w.get("start", 0.0)), float(w.get("end", 0.0))) for w in spoken]
        times: list = [None] * n
        j = 0
        for i, nm in enumerate(norms):
            for k in range(j, min(len(sp), j + 6)):
                if _match(nm, sp[k][0]):
                    times[i] = sp[k][1]
                    j = k + 1
                    break
        known = [i for i, t in enumerate(times) if t is not None]
        if not known:
            m = len(sp)
            times = [sp[min(m - 1, int(i * m / n))][1] for i in range(n)]
        else:
            for i in range(n):
                if times[i] is not None:
                    continue
                prv = max((k for k in known if k < i), default=None)
                nxt = min((k for k in known if k > i), default=None)
                if prv is not None and nxt is not None:
                    times[i] = times[prv] + (times[nxt] - times[prv]) * (i - prv) / (nxt - prv)
                elif prv is not None:
                    times[i] = times[prv] + step * (i - prv)
                else:
                    times[i] = max(0.0, times[nxt] - step * (nxt - i))
        out = []
        acc = 0.0
        for t in times:
            t = max(acc, max(0.0, float(t) - lead))
            out.append(min(t, last_ok))
            acc = t
        return out
    out, t = [], 0.0
    for i in range(n):
        if i and line_of is not None and line_of[i] != line_of[i - 1]:
            t += step * 0.6
        out.append(min(t, last_ok))
        t += step
    return out


def _get_times(item: dict, P: dict, B: _B, dur: float, preset: str) -> list[float]:
    spoken = item.get("_words") or None
    sig = tuple((w.get("text"), round(float(w.get("start", 0)), 3)) for w in spoken) if spoken else ()
    key = (B.extra.get("key"), round(dur, 3), sig)
    step = float(P.get("step", 0.12))
    if P.get("anim_in") in ("pop", "scale_down", "fade", "mask") and not spoken and preset in (
            "title_pop", "counter", "behind_giant"):
        step = 0.08
    return _TIMES.get_or(key, lambda: _unit_times([w.norm for w in B.words], spoken, dur, step,
                                                  [w.line for w in B.words]))


def _glyph_times(B: _B, times: list[float], dur: float, char_dt: float) -> list[list[float]]:
    """Tiempos por glifo dentro de cada palabra (para type / letter_stagger / escritura)."""
    out = []
    n = len(B.words)
    for i, w in enumerate(B.words):
        t0 = times[i]
        nxt = times[i + 1] if i + 1 < n else t0 + char_dt * len(w.run.glyphs) + 0.1
        g = len(w.run.glyphs)
        dt = char_dt if nxt <= t0 else min(char_dt, max(0.012, (nxt - t0) / max(1, g)))
        out.append([t0 + j * dt for j in range(g)])
    return out


# ---------------------------------------------------------------- render generico de bloques
def _block_transform(canvas, B: _B, P: dict, lt: float, dur: float, osc: float) -> None:
    cx, cy = B.center
    rot = float(P.get("rotation", 0.0) or 0.0)
    drift = float(P.get("drift", 0.0) or 0.0)
    sc = osc * (1.0 + drift * ease.clamp(lt / max(0.1, dur)))
    if rot or sc != 1.0:
        canvas.translate(cx, cy)
        if rot:
            canvas.rotate(rot)
        if sc != 1.0:
            canvas.scale(sc, sc)
        canvas.translate(-cx, -cy)


def _line_box(canvas, ln: _L, color, alpha, fs, right: float | None = None) -> None:
    """Caja redondeada detras de una linea; `right` = borde derecho de tinta animado (la caja
    crece con las palabras que ya entraron)."""
    if alpha <= 0.003:
        return
    l, t, r, b = ln.ink
    if right is not None:
        r = max(l + 0.2 * ln.size, min(r, right))
    s = ln.size
    padx, pady = 0.34 * s, 0.2 * s
    cap = max(w.run.cap for w in ln.words)
    desc = max(0.12 * s, max(w.run.desc for w in ln.words))
    rect = skia.Rect.MakeLTRB(l - padx, ln.baseline - cap - pady, r + padx, ln.baseline + desc + pady)
    rad = min(rect.height() * 0.5, 0.26 * s)
    p = C.fill(color, alpha)
    sh = C.fill("#000000", 0.22 * alpha)
    sh.setMaskFilter(_blur(0.12 * s))
    canvas.drawRRect(skia.RRect.MakeRectXY(rect.makeOffset(0, 0.05 * s), rad, rad), sh)
    canvas.drawRRect(skia.RRect.MakeRectXY(rect, rad, rad), p)


# animaciones que mueven la linea completa (por palabra se atravesarian entre si)
_LINE_ANIMS = ("slide_left", "slide_right", "scale_down")


def _contrast(c) -> str:
    return "#141414" if C.luminance(c) > 0.55 else "#FFFFFF"


def _render_block(canvas, item: dict, lt: float, dur: float, ctx, preset: str,
                  hooks: dict | None = None) -> None:
    """Render generico de un bloque de lineas.

    hooks (todos opcionales):
      pre(canvas, B, P, lt, dur, ctx, times, pout) -> [skia.Path] | None: dibuja detras del texto;
          los paths devueltos se EXCLUYEN del pintado normal (p. ej. el marcador).
      pill(...): dibuja una linea tipo pastilla completa.
      word_color(w, lt, times, P) -> color | None.
      post(canvas, B, P, lt, dur, ctx, times, pout, gtimes, look, units=[...]): encima del texto.
      no_line_box: True = `box` no es caja por linea (marker/kicker lo usan como otro color).
      no_plate: True = sin placa difusa de legibilidad.
    """
    if dur <= 0 or lt < 0 or lt > dur + 1e-6:
        return
    P, B = _get_block(item, preset, ctx)
    if not B.words:
        return
    hooks = hooks or {}
    anim = P["anim_in"]
    amp = 0.55 + 0.45 * ease.clamp(P["intensity"])
    final = not ctx.draft
    times = _get_times(item, P, B, dur, preset)
    tout = min(_TOUT.get(P["anim_out"], 0.28), dur * 0.3)
    pout = ease.clamp((lt - (dur - tout)) / tout) if tout > 0 else (1.0 if lt >= dur else 0.0)
    if P["anim_out"] == "none" and lt >= dur:
        return
    color = P["color"]
    box = None if hooks.get("no_line_box") else P.get("box")
    look = _look(P, ctx, B.fs, color if not box else "#000000")
    if box:
        look.shadow = 0.0
        if not item.get("color"):
            color = "#111111" if C.luminance(box) > 0.6 else "#FFFFFF"
    look.boost = _bg_boost(ctx, B.rect, color, item)
    osc = _out_state(P["anim_out"], pout, B.fs)[2] if P["anim_out"] == "pop_out" else 1.0
    letters = anim in ("type", "letter_stagger")
    gtimes = _glyph_times(B, times, dur, 1.0 / float(P.get("cps", 17.0)) if anim == "type" else 0.045) \
        if letters else None
    rooms = B.extra.get("rooms") or {}
    plate_k = 0.0
    if not box and not hooks.get("no_plate") and C.luminance(color) >= 0.4 and look.boost > 1.4:
        plate_k = 0.22 * ease.clamp((look.boost - 1.4) / 0.25)
    n_lines = len(B.lines)
    canvas.save()
    _block_transform(canvas, B, P, lt, dur, osc)
    excl = hooks["pre"](canvas, B, P, lt, dur, ctx, times, pout) if "pre" in hooks else None
    units: list = []
    for li, ln in enumerate(B.lines):
        oa, ody, _ = _out_state(P["anim_out"], pout, ln.size, li, n_lines)
        if P["anim_out"] == "pop_out":
            oa = _out_state("pop_out", pout, ln.size)[0]
        if oa <= 0.003:
            continue
        if ln.kind == "pill" and "pill" in hooks:
            hooks["pill"](canvas, B, P, ln, lt, dur, ctx, times, oa, ody)
            continue
        ln_t0 = min(times[w.idx] for w in ln.words)
        lgeo = None
        if anim in _LINE_ANIMS:
            _la, ldx, ldy, lsc, _lb = _in_state(anim, lt - ln_t0, ln.size, amp, final)
            lgeo = (ldx, ldy, lsc, ((ln.ink[0] + ln.ink[2]) / 2, (ln.ink[1] + ln.ink[3]) / 2))
        if box:
            ba = ease.out_cubic((lt - ln_t0) / 0.2) * oa
            if ba > 0:
                canvas.save()
                canvas.translate(0, ody)
                if lgeo is not None:
                    ldx, ldy, lsc, (ocx, ocy) = lgeo
                    canvas.translate(ocx + ldx, ocy + ldy)
                    canvas.scale(lsc, lsc)
                    canvas.translate(-ocx, -ocy)
                s = 0.92 + 0.08 * ease.spring(lt - ln_t0, 3.4, 0.6)
                cx = (ln.ink[0] + ln.ink[2]) / 2
                cy = (ln.ink[1] + ln.ink[3]) / 2
                canvas.translate(cx, cy)
                canvas.scale(s, s)
                canvas.translate(-cx, -cy)
                # el borde derecho sigue (con resorte) a la ultima palabra que ya entro
                xr, prev = None, None
                for w in ln.words:
                    r = w.ox + w.run.ink[2]
                    if prev is None:
                        xr = r
                    else:
                        xr += (r - prev) * ease.spring(lt - times[w.idx] - 0.02, 3.2, 0.8)
                    prev = r
                _line_box(canvas, ln, box, ba, B.fs, right=None if anim == "mask" else xr)
                canvas.restore()
        clip = None
        mask_dy = 0.0
        pad = ln.size
        if anim == "letter_stagger":
            # las letras emergen de la baseline; la mascara se abre al terminar (sombra completa)
            last_t = max(gtimes[w.idx][-1] for w in ln.words if gtimes[w.idx])
            opened = ease.smoothstep((lt - last_t - 0.3) / 0.3)
            clip = skia.Rect.MakeLTRB(ln.ink[0] - pad, ln.ink[1] - pad * 2 + ody, ln.ink[2] + pad,
                                      ln.ink[3] + (0.03 + 0.6 * opened) * ln.size + ody)
        if anim == "mask":
            u = lt - ln_t0
            if u < 0:
                continue
            mask_dy = (1 - ease.out_expo(u / 0.6)) * (ln.ink[3] - ln.ink[1]) * 1.1
            clip = skia.Rect.MakeLTRB(ln.ink[0] - pad, ln.ink[1] - pad * 2 + ody,
                                      ln.ink[2] + pad, ln.ink[3] + 0.04 * ln.size + ody)
        n0 = len(units)
        for w in ln.words:
            ta = times[w.idx]
            col = w.color
            if box and not item.get("color") and not w.accent:
                col = color
            if "word_color" in hooks:
                col = hooks["word_color"](w, lt, times, P) or col
            if letters:
                for j in range(len(w.run.glyphs)):
                    u = lt - gtimes[w.idx][j]
                    a, dx, dy, sc, bl = _in_state(anim, u, w.run.size, amp, final)
                    if a <= 0:
                        continue
                    units.append(dict(run=w.run, ox=w.ox, oy=w.oy, look=look, color=col, alpha=a * oa,
                                      dx=dx, dy=dy + ody, sc=sc, blur=bl, glyph=j, clip=clip,
                                      pivot=0.0 if anim == "type" else 0.5, word=w))
                continue
            origin = None
            pivot = 0.5
            if anim == "mask":
                a, dx, dy, sc, bl = 1.0, 0.0, mask_dy, 1.0, 0.0
            elif lgeo is not None:
                a, _dx, _dy, _sc, bl = _in_state(anim, lt - ta, w.run.size, amp, final)
                dx, dy, sc, origin = lgeo
            else:
                a, dx, dy, sc, bl = _in_state(anim, lt - ta, w.run.size, amp, final)
                pivot = 0.35 if anim in ("pop", "word_stagger") else 0.5
                sc = _soft_scale(sc, w.run.ink[2] - w.run.ink[0], pivot, rooms.get(id(w)))
            if a <= 0:
                continue
            units.append(dict(run=w.run, ox=w.ox, oy=w.oy, look=look, color=col, alpha=a * oa, dx=dx,
                              dy=dy + ody, sc=sc, blur=bl, pivot=pivot, origin=origin, clip=clip, word=w))
        if plate_k > 0 and len(units) > n0:
            vis = max(u["alpha"] for u in units[n0:])
            canvas.save()
            if clip is not None:
                canvas.clipRect(clip, True)
            _plate(canvas, skia.Rect.MakeLTRB(*ln.ink).makeOffset(0, ody + mask_dy), ln.size, plate_k * vis)
            canvas.restore()
    if excl:
        canvas.save()
        for pth in excl:
            canvas.clipPath(pth, skia.ClipOp.kDifference, True)
    _draw_units(canvas, units, ctx.draft)
    if excl:
        canvas.restore()
    if "post" in hooks:
        hooks["post"](canvas, B, P, lt, dur, ctx, times, pout, gtimes, look, units=units)
    canvas.restore()


# ---------------------------------------------------------------- presets simples
def title_pop(canvas, item, lt, dur, ctx):
    """Titulo bold con pop por palabra (resorte), acentos en color."""
    _render_block(canvas, item, lt, dur, ctx, "title_pop")


def stack(canvas, item, lt, dur, ctx):
    """Lineas apiladas con jerarquia (p. ej. HOY / te revelo / el secreto)."""
    _render_block(canvas, item, lt, dur, ctx, "stack")


def cascade(canvas, item, lt, dur, ctx):
    """Lineas en escalera de izquierda a derecha; la ultima mas grande."""
    _render_block(canvas, item, lt, dur, ctx, "cascade")


def serif_elegant(canvas, item, lt, dur, ctx):
    """Serif grande que aparece palabra por palabra con enfoque suave; acentos en italica."""
    _render_block(canvas, item, lt, dur, ctx, "serif_elegant")


def behind_giant(canvas, item, lt, dur, ctx):
    """Texto enorme para ir DETRAS del sujeto (render lo dibuja antes del recorte)."""
    _render_block(canvas, item, lt, dur, ctx, "behind_giant")


# ---------------------------------------------------------------- items detras del sujeto
def _no_subject(ctx) -> bool:
    try:
        return ctx.subject_image() is None
    except Exception:
        return True


def _behind_fallback(fn, canvas, item, lt, dur, ctx) -> None:
    """Sin recorte del sujeto (matte fallido o sin sujeto) render dibuja el item 'detras' ENCIMA
    de la persona. Para no tapar la cara se abre un hueco difuso (ovalo de la cabeza) en el
    texto: parece que la cabeza queda delante."""
    face = ctx.face_rect_px() if hasattr(ctx, "face_rect_px") else None
    if face is None or face.isEmpty():
        # sin cara detectada no hay nada que proteger: se dibuja tal cual
        fn(canvas, item, lt, dur, ctx)
        return
    try:
        r = measure(item, ctx)
        bounds = r.makeOutset(0.35 * r.height() + ctx.px(60), 0.35 * r.height() + ctx.px(60))
    except Exception:
        bounds = None
    canvas.saveLayer(bounds, None)
    fn(canvas, item, lt, dur, ctx)
    fw, fh = face.width(), face.height()
    cx, cy = face.centerX(), face.centerY() - 0.12 * fh
    oval = skia.Rect.MakeLTRB(cx - 0.95 * fw, cy - 1.05 * fh, cx + 0.95 * fw, cy + 0.95 * fh)
    p = skia.Paint(AntiAlias=True, BlendMode=skia.BlendMode.kDstOut)
    p.setMaskFilter(_blur(0.14 * fw))
    canvas.drawOval(oval, p)
    canvas.restore()


def _behind_aware(fn):
    """Envuelve un renderer: si el item va detras del sujeto y no hay sujeto recortado,
    degrada con `_behind_fallback` en vez de tapar la cara."""
    def wrapped(canvas, item, lt, dur, ctx):
        if dur <= 0 or lt < 0 or lt > dur + 1e-6:
            return
        if is_behind(item) and _no_subject(ctx):
            _behind_fallback(fn, canvas, item, lt, dur, ctx)
            return
        fn(canvas, item, lt, dur, ctx)
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    wrapped.raw = fn
    return wrapped


# ---------------------------------------------------------------- script_accent
def script_accent(canvas, item, lt, dur, ctx):
    """Palabra manuscrita que se escribe sola.

    anim_in "mask" (default): un frente de tinta inclinado (como el trazo) avanza glifo por
    glifo de izquierda a derecha, con un ease propio en cada glifo (ritmo de escritura) y un
    borde casi duro. Otros anim_in usan el render generico.
    """
    P, B = _get_block(item, "script_accent", ctx)
    if P["anim_in"] != "mask":
        _render_block(canvas, item, lt, dur, ctx, "script_accent")
        return
    if dur <= 0 or lt < 0 or lt > dur + 1e-6 or not B.words:
        return
    times = _get_times(item, P, B, dur, "script_accent")
    tout = min(_TOUT.get(P["anim_out"], 0.28), dur * 0.3)
    pout = ease.clamp((lt - (dur - tout)) / tout) if tout > 0 else 0.0
    if P["anim_out"] == "none" and lt >= dur:
        return
    color = P["color"]
    look = _look(P, ctx, B.fs, color)
    look.boost = _bg_boost(ctx, B.rect, color, item)
    spoken = bool(item.get("_words"))
    k = 0.28  # inclinacion del frente
    canvas.save()
    _block_transform(canvas, B, P, lt, dur, 1.0)
    done_units = []
    for li, ln in enumerate(B.lines):
        oa, ody, _ = _out_state(P["anim_out"], pout, ln.size, li, len(B.lines))
        if oa <= 0.003:
            continue
        t0 = min(times[w.idx] for w in ln.words)
        ng = sum(len(w.run.glyphs) for w in ln.words)
        wdur = ease.clamp(0.085 * ng, 0.55, 1.9)
        if spoken:
            last = max(times[w.idx] for w in ln.words)
            wdur = max(wdur * 0.8, min(2.4, last - t0 + 0.35))
        wdur = min(wdur, max(0.3, dur - tout - t0 - 0.05))
        p = ease.clamp((lt - t0) / wdur)
        if p <= 0:
            continue
        if p >= 1.0:
            for w in ln.words:
                done_units.append(dict(run=w.run, ox=w.ox, oy=w.oy + ody, look=look, color=color, alpha=oa))
            continue
        yb = ln.baseline
        hx = _script_front(ln, p, k, ctx)
        feather = 0.08 * ln.size
        m = skia.Matrix.MakeAll(1, -k, k * yb, 0, 1, 0, 0, 0, 1)
        sh = skia.GradientShader.MakeLinear(
            [skia.Point(hx, yb), skia.Point(hx + feather, yb)],
            [C.color("#FFFFFF"), C.color("#FFFFFF", 0.0)], None, skia.TileMode.kClamp, 0, m)
        canvas.save()
        canvas.translate(0, ody)
        for w in ln.words:
            _script_word(canvas, w, look, color, oa, sh, ctx)
        canvas.restore()
    _draw_units(canvas, done_units, ctx.draft)
    canvas.restore()


def _script_segments(ln: _L, k: float) -> list:
    """Tramos del frente de escritura: uno por glifo (rango x de su tinta medido en el espacio
    inclinado del frente) y saltos cortos entre palabras. -> [(x0, x1, peso)]."""
    key = ("script_seg", round(k, 4))
    hit = getattr(ln, "_seg", None)
    if hit is not None and hit[0] == key:
        return hit[1]
    yb = ln.baseline
    segs = []
    prev = None
    s = ln.size
    for w in ln.words:
        first = True
        for j, (gl, gt, gr, gb) in enumerate(w.run.gink):
            if gr <= gl:
                continue
            gx = w.ox + w.run.xpos[j]
            x0 = gx + gl + k * ((w.oy + gt) - yb)
            x1 = gx + gr + k * ((w.oy + gb) - yb)
            if prev is not None and x0 > prev and first:
                segs.append((prev, x0, 0.04 * s))           # la pluma cruza el espacio en el aire
            a = x0 if prev is None else max(x0, prev)
            b = max(x1, a)
            segs.append((a, b, (b - a) + 0.1 * s))
            prev = b
            first = False
    ln._seg = (key, segs)  # type: ignore[attr-defined]
    return segs


def _script_front(ln: _L, p: float, k: float, ctx) -> float:
    """Posicion del frente (espacio inclinado) para el progreso p de la linea."""
    segs = _script_segments(ln, k)
    if not segs:
        return ln.ink[2]
    total = sum(sg[2] for sg in segs)
    target = p * total
    acc = 0.0
    for a, b, wgt in segs:
        if acc + wgt >= target:
            q = (target - acc) / max(1e-6, wgt)
            e = 0.45 * q + 0.55 * (0.5 - 0.5 * math.cos(math.pi * q))
            return a + (b - a) * e
        acc += wgt
    return segs[-1][1]


def _script_word(canvas, w: _W, look: _Look, color, alpha, shader, ctx):
    """Palabra (sombra y glow incluidos) enmascarada por el frente de escritura (DstIn)."""
    run = w.run
    fs = run.size
    l, t, r, b = run.ink
    pad = fs * 0.6
    bounds = skia.Rect.MakeLTRB(w.ox + l - pad, w.oy + t - pad, w.ox + r + pad, w.oy + b + pad)
    canvas.saveLayerAlpha(bounds, int(255 * alpha + 0.5))
    _paint_blob(canvas, run.blob, w.ox, w.oy, look, fs, color, 1.0, draft=ctx.draft, run=run)
    mp = skia.Paint(Shader=shader, BlendMode=skia.BlendMode.kDstIn)
    canvas.drawRect(bounds, mp)
    canvas.restore()


# ---------------------------------------------------------------- highlight_marker
def _marker_bands(B: _B, full: bool) -> list[tuple]:
    """Bandas (linea, primera palabra, ultima palabra) a resaltar: las palabras acento, o cada
    linea completa (`full`), o la ultima linea si no hay acentos."""
    bands = []
    has_acc = any(w.accent for w in B.words) and not full
    for ln in B.lines:
        cur = []
        for w in ln.words:
            if (w.accent if has_acc else True):
                cur.append(w)
            elif cur:
                bands.append((ln, cur[0], cur[-1]))
                cur = []
        if cur:
            bands.append((ln, cur[0], cur[-1]))
    if not has_acc and not full and bands:
        bands = [bands[-1]]
    return bands


def _marker_path(x0, x1, y0, y1, fs, jit) -> skia.Path:
    s = 0.12 * fs      # corte inclinado de la punta del marcador
    j1, j2, j3 = (jit[0] - 0.5) * 0.06 * fs, (jit[1] - 0.5) * 0.06 * fs, (jit[2] - 0.5) * 0.05 * fs
    p = skia.Path()
    p.moveTo(x0 + s, y0 + j1)
    p.cubicTo(x0 + (x1 - x0) * 0.35, y0 - 0.02 * fs + j2, x0 + (x1 - x0) * 0.7, y0 + 0.02 * fs, x1, y0 + j3)
    p.quadTo(x1 + 0.02 * fs, (y0 + y1) / 2, x1 - s, y1)
    p.cubicTo(x0 + (x1 - x0) * 0.65, y1 + 0.025 * fs + j1, x0 + (x1 - x0) * 0.3, y1 - 0.01 * fs, x0, y1 + j2)
    p.quadTo(x0 - 0.02 * fs, (y0 + y1) / 2, x0 + s, y0 + j1)
    p.close()
    return p


def _marker_colors(item: dict, P: dict) -> tuple:
    """(color del marcador, color del texto fuera del marcador, color del texto encima)."""
    mcol = item.get("box") or P["accent"]
    color = P["color"]
    out_col = color if C.luminance(color) >= 0.4 else "#FFFFFF"
    on_col = None
    for c in (item.get("accent"), item.get("color")):
        if c and abs(C.luminance(c) - C.luminance(mcol)) >= 0.4:
            on_col = c
            break
    return mcol, out_col, on_col or _contrast(mcol)


def highlight_marker(canvas, item, lt, dur, ctx):
    """Marcador que barre detras de la(s) palabra(s) acento; el texto cambia a contraste encima.

    `box` (o `accent` si no hay box) = color del marcador; `color` = texto fuera del marcador
    (si es oscuro, el texto va sobre el marcador en todas las lineas y fuera se ve claro);
    `accent` explicito que contraste con el marcador = color del texto encima."""
    P, B = _get_block(item, "highlight_marker", ctx)
    if not B.words:
        return
    mcol, out_col, on_col = _marker_colors(item, P)
    full = C.luminance(P["color"]) < 0.4 and not any(w.accent for w in B.words)
    bands = _marker_bands(B, full)
    state: dict = {}

    def geo(times):
        out = []
        for bi, (ln, w0, w1) in enumerate(bands):
            fs = ln.size
            ws = ln.words[ln.words.index(w0):ln.words.index(w1) + 1]
            key = f"{item.get('id', '')}:mk{bi}"
            jit = (ctx.rand(key + "a"), ctx.rand(key + "b"), ctx.rand(key + "c"))
            rot = (ctx.rand(key + "r") - 0.5) * 1.6 - 0.6
            x0 = w0.ox + w0.run.ink[0] - 0.13 * fs
            x1 = w1.ox + w1.run.ink[2] + 0.13 * fs
            # la banda cubre la tinta real (ascendentes, puntos, descendentes) + inclinacion
            tilt = abs(math.sin(math.radians(rot))) * (x1 - x0) * 0.5
            pad = 0.07 * fs + tilt + 0.03 * fs
            y0 = ln.baseline - max(-w.run.ink[1] for w in ws) - pad
            y1 = ln.baseline + max(max(w.run.ink[3] for w in ws), 0.1 * fs) + pad
            t0 = max(times[w0.idx] + 0.18, times[w1.idx] - 0.05)
            out.append((ln, _marker_path(x0, x1, y0, y1, fs, jit), x0, x1, y0, y1, rot, t0))
        return out

    def pre(canvas, B, P, lt, dur, ctx, times, pout):
        clips = []
        for ln, path, x0, x1, y0, y1, rot, t0 in geo(times):
            p = ease.out_cubic((lt - t0) / 0.42)
            if p <= 0:
                continue
            li = B.lines.index(ln)
            oa, ody, _ = _out_state(P["anim_out"], pout, ln.size, li, len(B.lines))
            if P["anim_out"] == "pop_out":
                oa = _out_state("pop_out", pout, ln.size)[0]
            if oa <= 0.003:
                continue
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            m = skia.Matrix.RotateDeg(rot, skia.Point(cx, cy))
            m.postTranslate(0, ody)
            pth = skia.Path(path)
            pth.transform(m)
            xe = x0 + (x1 - x0 + 0.2 * ln.size) * p
            sweep = skia.Path()
            s = 0.12 * ln.size
            sweep.addPoly([skia.Point(x0 - s * 2, y0 - ln.size), skia.Point(xe + s, y0 - ln.size),
                           skia.Point(xe - s, y1 + ln.size), skia.Point(x0 - s * 2, y1 + ln.size)], True)
            sweep.transform(m)
            canvas.save()
            canvas.clipPath(sweep, skia.ClipOp.kIntersect, True)
            shp = C.fill("#000000", 0.18 * oa)
            shp.setMaskFilter(_blur(0.08 * ln.size))
            canvas.save()
            canvas.translate(0, 0.04 * ln.size)
            canvas.drawPath(pth, shp)
            canvas.restore()
            top_c = C.mix(mcol, "#FFFFFF", 0.12)
            gp = skia.Paint(AntiAlias=True, Shader=skia.GradientShader.MakeLinear(
                [skia.Point(0, y0 + ody), skia.Point(0, y1 + ody)], [top_c, C.color(mcol)]))
            gp.setAlphaf(0.96 * oa)
            canvas.drawPath(pth, gp)
            canvas.restore()
            try:
                inter = skia.Op(pth, sweep, skia.PathOp.kIntersect_PathOp)
            except Exception:
                inter = None
            if inter is not None and not inter.isEmpty():
                clips.append((inter, li))
        state["clips"] = clips
        return [c for c, _ in clips]

    def post(canvas, B, P, lt, dur, ctx, times, pout, gtimes, look, units=None):
        clips = state.get("clips") or []
        if not clips or not units:
            return
        on_look = _Look(shadow=0.0)
        for inter, li in clips:
            us = [dict(u, color=on_col, look=on_look) for u in units if u["word"].line == li]
            if not us:
                continue
            canvas.save()
            canvas.clipPath(inter, skia.ClipOp.kIntersect, True)
            _draw_units(canvas, us, ctx.draft)
            canvas.restore()

    def word_color(w, lt, times, P):
        return out_col

    _render_block(canvas, item, lt, dur, ctx, "highlight_marker",
                  {"pre": pre, "post": post, "word_color": word_color, "no_line_box": True})


# ---------------------------------------------------------------- typewriter
def typewriter(canvas, item, lt, dur, ctx):
    """Maquina de escribir: letra por letra (al ritmo de la voz si hay _words) con cursor."""
    it = dict(item)
    if it.get("anim_in") not in (None, "type"):
        _render_block(canvas, it, lt, dur, ctx, "typewriter")
        return

    def post(canvas, B, P, lt, dur, ctx, times, pout, gtimes, look, units=None):
        if gtimes is None:
            return
        oa = _out_state(P["anim_out"], pout, B.fs)[0]
        # posicion del cursor: detras del ultimo glifo visible
        cur = None
        typing = False
        for w in B.words:
            for j, tg in enumerate(gtimes[w.idx]):
                if lt >= tg:
                    cur = (w, j)
                    typing = typing or (lt - tg) < 0.12
        if cur is None:
            w, x = B.words[0], B.words[0].ox + B.words[0].run.ink[0]
            ln = B.lines[w.line]
        else:
            w, j = cur
            ln = B.lines[w.line]
            x = w.ox + w.run.xpos[j] + w.run.gink[j][2] + 0.07 * ln.size
        all_done = cur is not None and cur[0] is B.words[-1] and cur[1] == len(B.words[-1].run.glyphs) - 1
        if typing:
            blink = 1.0
        else:
            ph = (lt % 1.0)
            blink = ease.smoothstep((0.5 - abs(ph - 0.5)) * 6 - 0.9) if all_done or cur is None else 1.0
            blink = 0.15 + 0.85 * blink
        s = ln.size
        rect = skia.Rect.MakeLTRB(x, ln.baseline - max(v.run.cap for v in ln.words) * 1.12,
                                  x + max(3.0, 0.12 * s), ln.baseline + 0.16 * s)
        a = oa * blink * ease.clamp(lt / 0.1)
        if a <= 0.01:
            return
        sp = C.fill("#000000", 0.45 * a * max(0.3, look.shadow))
        sp.setMaskFilter(_blur(0.06 * s))
        canvas.drawRect(rect.makeOffset(0, 0.03 * s), sp)
        canvas.drawRoundRect(rect, 0.025 * s, 0.025 * s, C.fill(P["accent"], a))

    _render_block(canvas, it, lt, dur, ctx, "typewriter", {"post": post})


# ---------------------------------------------------------------- counter
def _fmt_num(v: float, decimals: int, sep: str = ",", dec: str | None = None) -> str:
    if dec is None:
        dec = "," if sep == "." else "."
    s = f"{abs(v):,.{max(0, decimals)}f}"
    s = s.replace(",", "\x00").replace(".", dec).replace("\x00", sep)
    return ("-" if v < 0 else "") + s


def _num(v, default=0.0, sep: str = ",") -> float:
    """Numero de un campo del item. Acepta '1,000', '$1,000', '1.000' (con sep='.'), '12,5'
    (decimal con coma si sep='.')."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = re.sub(r"[^0-9,.\-]", "", str(v))
        dec = "," if sep == "." else "."
        s = s.replace(sep, "").replace(dec, ".")
        return float(s)
    except Exception:
        return default


def counter(canvas, item, lt, dur, ctx):
    """Numero que cuenta (from -> to) con digitos tabulares, prefijo volado y sufijo chico.

    Campos: from, to, prefix, suffix, decimals, sep (","), text (etiqueta opcional debajo),
    anchor (right = odometro: los digitos crecen hacia la izquierda y el sufijo no se mueve;
    center; left).
    """
    if dur <= 0 or lt < 0 or lt > dur + 1e-6:
        return
    key = "counter:" + _layout_key(item, "counter", ctx)
    P, G = _BLOCKS.get_or(key, lambda: _counter_layout(item, ctx))
    P = dict(P)
    if item.get("anim_out") in ANIM_OUT:
        P["anim_out"] = item["anim_out"]
    if P["anim_out"] == "none" and lt >= dur:
        return
    s, pc, cdur, t_in = _counter_state(item, lt, dur, G["sep"])
    done = pc >= 1.0
    tout = min(_TOUT.get(P["anim_out"], 0.26), dur * 0.3)
    pout = ease.clamp((lt - (dur - tout)) / tout) if tout > 0 else 0.0
    oa, ody, osc = _out_state(P["anim_out"], pout, G["fs"])
    amp = 0.55 + 0.45 * ease.clamp(P["intensity"])
    a_in, _, dy_in, sc_in, bl = _in_state(P["anim_in"], lt, G["fs"], amp, not ctx.draft)
    land = ease.clamp((lt - t_in - cdur) / 0.5)
    pulse = 1.0 + (0.07 * math.sin(math.pi * ease.out_quad(land)) * (1 - land) if done else 0.0)
    fs = G["fs"]
    font = G["fname"]
    # celdas tabulares mientras cuenta (sin temblor); al aterrizar los digitos se asientan
    # en espaciado proporcional con kerning (sin hueco alrededor del "1")
    cell = G["cell"]
    parts = []
    x = 0.0
    settle = ease.out_cubic((lt - t_in - cdur) / 0.3) if done else 0.0
    prop = _run(s, font, fs) if settle > 0 else None
    for i, ch in enumerate(s):
        r = _run(ch, font, fs)
        if ch.isdigit():
            xt = x + (cell - r.adv) / 2
            x += cell
        else:
            xt = x
            x += r.adv
        if prop is not None:
            xt = xt + (prop.xpos[i] - xt) * settle
        parts.append((r, xt))
    if prop is not None:
        x = x + (prop.adv - x) * settle
    pre_r, suf_r = G["pre"], G["suf"]
    gap_p = G["gap_p"]
    gap_s = G["gap_s"]
    pre_w = pre_r.adv + gap_p if pre_r else 0.0
    anchor = G["anchor"]
    if anchor == "right":
        dl = G["rd"] - x
    elif anchor == "left":
        dl = G["dl0"]
    else:
        total = pre_w + x + (gap_s + suf_r.adv if suf_r else 0.0)
        dl = G["cx"] - total / 2 + pre_w
    base = G["base"]
    color, accent = P["color"], P["accent"]
    look = _look(P, ctx, fs, color)
    look.boost = _bg_boost(ctx, G["rect"], color, item)
    pivot = (G["cx"], base - G["cap"] / 2)
    canvas.save()
    sc = sc_in * osc * pulse
    canvas.translate(pivot[0], pivot[1] + dy_in + ody)
    canvas.scale(sc, sc)
    canvas.translate(-pivot[0], -pivot[1])
    a = a_in * oa
    units = []
    if pre_r:
        units.append(dict(run=pre_r, ox=dl - pre_w, oy=base - G["cap"] + pre_r.cap, look=look, color=accent,
                          alpha=a, blur=bl))
    for r, dx in parts:
        units.append(dict(run=r, ox=dl + dx, oy=base, look=look, color=color, alpha=a, blur=bl))
    if suf_r:
        units.append(dict(run=suf_r, ox=dl + x + gap_s, oy=base, look=look, color=color, alpha=a * 0.92,
                          blur=bl))
    _draw_units(canvas, units, ctx.draft)
    lab = G["label"]
    if lab:
        la = a * ease.out_cubic((lt - t_in - 0.25) / 0.4)
        ldy = (1 - ease.out_expo((lt - t_in - 0.25) / 0.5)) * 0.2 * lab["fs"]
        _draw_units(canvas, [dict(run=w.run, ox=w.ox, oy=w.oy, look=look, color=lab["color"], alpha=la,
                                  dy=ldy) for w in lab["words"]], ctx.draft)
    canvas.restore()


def _counter_state(item: dict, lt: float, dur: float, sep: str = ","):
    """(texto del numero, progreso del conteo, duracion del conteo, retardo inicial)."""
    v0, v1 = _num(item.get("from", 0), 0.0, sep), _num(item.get("to", 0), 0.0, sep)
    dec = int(item.get("decimals", 0) or 0)
    cdur = float(item.get("count_dur") or min(1.3, max(0.4, dur * 0.55)))
    t_in = 0.12
    pc = ease.clamp((lt - t_in) / cdur)
    val = v0 + (v1 - v0) * (1 - (1 - pc) ** 3.2)
    return _fmt_num(v1 if pc >= 1.0 else val, dec, sep), pc, cdur, t_in


def counter_text(item: dict, lt: float, dur: float) -> str:
    """Numero que muestra el contador en `lt` (sin prefijo/sufijo). Util para tests/UI."""
    return _counter_state(item, lt, dur, str(item.get("sep", ",")))[0]


def _counter_layout(item: dict, ctx):
    P = _params(item, "counter", ctx)
    fname = P["font"]
    dec = int(item.get("decimals", 0) or 0)
    sep = str(item.get("sep", ","))
    v0, v1 = _num(item.get("from", 0), 0.0, sep), _num(item.get("to", 0), 0.0, sep)
    widest = _fmt_num(max(abs(v0), abs(v1)), dec, sep)
    final = _fmt_num(v1, dec, sep)
    pre = _clean(str(item.get("prefix", "") or ""), fname)
    suf = _clean(str(item.get("suffix", "") or ""), fname)
    maxw, _ = _usable(P, ctx)

    def width_at(fs):
        cell = max(_run(d, fname, fs).adv for d in "0123456789")
        w = sum(cell if ch.isdigit() else _run(ch, fname, fs).adv for ch in widest)
        if pre:
            w += _run(pre, fname, fs * 0.58).adv + 0.03 * fs
        if suf:
            w += _run(suf, fname, fs * 0.36).adv + 0.12 * fs
        return w, cell

    if P.get("size"):
        fs = float(P["size"]) * ctx.W
    else:
        fs = float(P.get("max_size", 0.24)) * ctx.W
    w, cell = width_at(fs)
    if w > maxw:
        fs *= maxw / w * 0.99
        w, cell = width_at(fs)
    pre_r = _run(pre, fname, fs * 0.58) if pre else None
    suf_r = _run(suf, fname, fs * 0.36) if suf else None
    gap_p = 0.03 * fs if pre_r else 0.0
    gap_s = 0.12 * fs if suf_r else 0.0
    pre_w = pre_r.adv + gap_p if pre_r else 0.0
    suf_w = gap_s + suf_r.adv if suf_r else 0.0
    wt = w - pre_w - suf_w                       # digitos tabulares del valor mas ancho
    wp = _run(final, fname, fs).adv              # valor final en espaciado proporcional
    digits = _run("0123456789", fname, fs)
    cap = digits.cap
    anchor = str(item.get("anchor") or "right")
    if anchor not in ("right", "center", "left"):
        anchor = "right"
    # etiqueta opcional
    label = None
    lab_h = 0.0
    txt = str(item.get("text", "") or "").strip()
    if txt:
        lf = _resolve_font(item.get("label_font"), "caption", ctx)
        lfs = max(0.042 * ctx.W, fs * 0.26)
        spec = _Spec(_split_words(_clean(txt, lf), _accent_set(P)), lf)
        P2 = dict(P, gap=0.1)
        lines, words = _stack([spec], lfs, P2, ctx, "#FFFFFF", P["accent"])
        _align_lines(lines, "center")
        lab_h = lines[-1].ink[3] - lines[0].ink[1]
        label = {"fs": lfs, "words": words, "lines": lines, "color": item.get("label_color") or "#FFFFFF"}
    total_h = cap + (0.3 * fs + lab_h if label else digits.desc * 0.2)
    cx = float(P["x"]) * ctx.W
    cy = float(P["y"]) * ctx.H
    top = cy - total_h / 2
    top = max(SAFE["top"] * ctx.H, min(top, SAFE["bottom"] * ctx.H - total_h))
    # posicion horizontal: el valor final queda centrado en cx; mientras cuenta (tabular, mas
    # ancho) el bloque puede extenderse `wt - wp` hacia el lado que crece
    final_w = pre_w + wp + suf_w
    ext_l = final_w / 2 + max(0.0, wt - wp) if anchor == "right" else (final_w / 2 if anchor == "left" else w / 2)
    ext_r = final_w / 2 + max(0.0, wt - wp) if anchor == "left" else (final_w / 2 if anchor == "right" else w / 2)
    lo = SAFE["left"] * ctx.W + ext_l
    hi = SAFE["right"] * ctx.W - ext_r
    cx = min(max(cx, lo), hi) if lo <= hi else (lo + hi) / 2
    base = top + cap
    if label:
        dy = base + 0.3 * fs - label["lines"][0].ink[1]
        for ww in label["words"]:
            ww.ox += cx
            ww.oy += dy
    x0f = cx - final_w / 2
    rect = skia.Rect.MakeLTRB(cx - ext_l, top, cx + ext_r, top + total_h)
    G = {"fs": fs, "fname": fname, "cell": cell, "pre": pre_r, "suf": suf_r, "cap": cap, "cx": cx,
         "base": base, "label": label, "sep": sep, "rect": rect, "gap_p": gap_p, "gap_s": gap_s,
         "anchor": anchor, "rd": x0f + pre_w + wp, "dl0": x0f + pre_w}
    return P, G


# ---------------------------------------------------------------- kicker
def _kicker_pill_ink(lines: list[_L], fs: float) -> None:
    """Ensancha la tinta de la linea pastilla para que el bloque (y SAFE) incluyan la pastilla."""
    for i, ln in enumerate(lines):
        if ln.kind != "pill":
            continue
        s = ln.size
        l, t, r, b = ln.ink
        cap = max(w.run.cap for w in ln.words)
        ln.ink = (l - 0.8 * s, ln.baseline - cap - 0.55 * s, r + 0.8 * s, ln.baseline + 0.55 * s)
        # separar la pastilla del titulo
        if i + 1 < len(lines):
            shift = 0.55 * s + 0.22 * fs
            for ln2 in lines[i + 1:]:
                l2, t2, r2, b2 = ln2.ink
                ln2.ink = (l2, t2 + shift, r2, b2 + shift)
                ln2.baseline += shift
                for w in ln2.words:
                    w.oy += shift


def kicker(canvas, item, lt, dur, ctx):
    """Etiqueta chica en pastilla sobre un titulo bold.

    Etiqueta = `kicker` o la primera linea explicita. Sin ellas, un texto corto (<= 4 palabras)
    es solo la pastilla; uno largo va sin pastilla (y `box` vuelve a ser caja por linea).
    `box` = color de la pastilla (default: acento)."""
    P0, B0 = _get_block(item, "kicker", ctx)
    has_pill = any(ln.kind == "pill" for ln in B0.lines)
    pill_col = item.get("box") or P0["accent"]
    fg = "#141414" if C.luminance(pill_col) > 0.55 else "#FFFFFF"

    def pill(canvas, B, P, ln, lt, dur, ctx, times, oa, ody):
        u = lt
        p = ease.out_expo(u / 0.5)
        if p <= 0:
            return
        l, t, r, b = ln.ink
        cx = (l + r) / 2 if P["align"] == "center" else (l if P["align"] == "left" else r)
        w = (r - l) * p
        x0 = cx - w / 2 if P["align"] == "center" else (l if P["align"] == "left" else r - w)
        rect = skia.Rect.MakeLTRB(x0, t + ody, x0 + w, b + ody)
        rad = rect.height() / 2
        sh = C.fill("#000000", 0.28 * oa)
        sh.setMaskFilter(_blur(0.35 * ln.size))
        canvas.drawRRect(skia.RRect.MakeRectXY(rect.makeOffset(0, 0.12 * ln.size), rad, rad), sh)
        canvas.drawRRect(skia.RRect.MakeRectXY(rect, rad, rad), C.fill(pill_col, oa))
        la = ease.out_cubic((u - 0.16) / 0.3) * oa
        if la <= 0:
            return
        canvas.save()
        canvas.clipRRect(skia.RRect.MakeRectXY(rect, rad, rad), True)
        dx = (1 - ease.out_expo((u - 0.12) / 0.5)) * -0.5 * ln.size
        for w in ln.words:
            _draw_run(canvas, w.run, w.ox, w.oy, _Look(shadow=0.0), fg, la, dx, ody, draft=ctx.draft)
        canvas.restore()

    _render_block(canvas, item, lt, dur, ctx, "kicker", {"pill": pill, "no_line_box": has_pill})


RENDERERS = {name: _behind_aware(fn) for name, fn in (
    ("title_pop", title_pop),
    ("stack", stack),
    ("cascade", cascade),
    ("serif_elegant", serif_elegant),
    ("script_accent", script_accent),
    ("behind_giant", behind_giant),
    ("highlight_marker", highlight_marker),
    ("typewriter", typewriter),
    ("counter", counter),
    ("kicker", kicker),
)}


def is_behind(item: dict) -> bool:
    """True si el item va DETRAS del sujeto (campo `behind` o default del preset)."""
    if item.get("behind") is not None:
        return bool(item["behind"])
    return bool(PRESETS.get(item.get("preset") or "", {}).get("behind", False))


def draw(canvas, item, lt, dur, ctx):
    """Despacho por `preset` (conveniencia para render/preview)."""
    fn = RENDERERS.get(item.get("preset") or "title_pop", title_pop)
    fn(canvas, item, lt, dur, ctx)


def measure(item: dict, ctx) -> skia.Rect:
    """Tinta del item en reposo (px de salida), incluida la rotacion y el empuje (drift) del
    bloque. Util para el director (SAFE, choque con cara)."""
    preset = item.get("preset") or "title_pop"
    if preset == "counter":
        key = "counter:" + _layout_key(item, "counter", ctx)
        _P, G = _BLOCKS.get_or(key, lambda: _counter_layout(item, ctx))
        return G["rect"]
    _P, B = _get_block(item, preset, ctx)
    if not B.lines:
        return skia.Rect.MakeEmpty()
    bb = B.extra.get("bbox")
    if bb is not None:
        return skia.Rect.MakeLTRB(bb.left(), bb.top(), bb.right(), bb.bottom())
    return skia.Rect.MakeLTRB(min(l.ink[0] for l in B.lines), min(l.ink[1] for l in B.lines),
                              max(l.ink[2] for l in B.lines), max(l.ink[3] for l in B.lines))


# ================================================================ SUBTITULOS
CAPTION_PRESETS: dict[str, dict] = {
    # active_color: la palabra que se pronuncia pasa al acento (transicion ~80 ms);
    # active_scale: crecimiento sutil de la palabra activa; kw_accent: una palabra clave por
    # grupo como maximo (y nunca en grupos seguidos) queda en acento; recenter: la linea se
    # recentra con resorte suave mientras entran las palabras.
    "clean_bold": dict(scale=1.0, shadow=0.95, stroke=0.0, reveal="word", anim="pop", active="color",
                       active_scale=0.03, kw_accent=True, max_words=4, gap=0.16, space=1.3, max_lines=1,
                       recenter=True),
    "karaoke": dict(scale=1.0, shadow=0.95, stroke=0.0, reveal="group", anim="rise", active="wipe",
                    active_scale=0.0, kw_accent=False, max_words=4, gap=0.16, space=1.2, max_lines=1,
                    recenter=False),
    "boxed": dict(scale=1.0, shadow=0.9, stroke=0.0, reveal="group", anim="rise", active="pill",
                  active_scale=0.0, kw_accent=False, max_words=4, gap=0.34, space=1.75, max_lines=1,
                  recenter=False),
    "minimal_small": dict(scale=0.74, shadow=1.0, stroke=0.0, reveal="word", anim="fade", active="none",
                          active_scale=0.0, kw_accent=False, max_words=5, gap=0.2, space=1.15, max_lines=2,
                          font="Poppins-SemiBold", recenter=True),
    "bold_caps": dict(scale=1.28, shadow=0.8, stroke=5.0, reveal="word", anim="pop_strong",
                      active="color", active_scale=0.0, kw_accent=False, max_words=3, max_words_cap=3,
                      gap=0.06, space=1.3, max_lines=2, case="upper", role="title", recenter=True),
}


@dataclass
class _Group:
    words: list               # [{text, start, end, kw}]
    start: float
    end: float
    fade_out: bool
    lines: list = field(default_factory=list)
    placed: list = field(default_factory=list)   # _W por palabra (mismo orden que words)
    rect: Any = None
    fs: float = 0.0
    acc: set = field(default_factory=set)        # indices de palabras clave en acento
    rooms: dict = field(default_factory=dict)


def _cap_cfg(cfg: dict, ctx) -> dict:
    preset = cfg.get("preset") or "clean_bold"
    D = dict(CAPTION_PRESETS.get(preset, CAPTION_PRESETS["clean_bold"]))
    D["preset"] = preset
    D["y"] = float(cfg.get("y", 0.72) if cfg.get("y") is not None else 0.72)
    D["x"] = float(cfg.get("x", 0.5) if cfg.get("x") is not None else 0.5)
    D["size"] = float(cfg.get("size") or 0.052) * D["scale"]
    fonts = ctx.palette.get("fonts", {})
    if preset == "minimal_small" and (not cfg.get("font") or cfg.get("font") == "Poppins-ExtraBold"):
        D["font"] = D.get("font") or "Poppins-SemiBold"
    elif preset == "bold_caps" and (not cfg.get("font") or cfg.get("font") == "Poppins-ExtraBold"):
        D["font"] = fonts.get("title") or "Poppins-Black"
    else:
        D["font"] = cfg.get("font") or fonts.get("caption") or assets.DEFAULT_FONT
    D["color"] = cfg.get("color") or "#FFFFFF"
    D["accent"] = cfg.get("accent") or ctx.palette.get("accent", "#FFD23F")
    case = cfg.get("case") or "none"
    D["case"] = "upper" if preset == "bold_caps" and case in ("none", None) else case
    D["max_words"] = int(cfg.get("max_words") or D["max_words"])
    if D.get("max_words_cap"):
        D["max_words"] = min(D["max_words"], int(D["max_words_cap"]))
    if cfg.get("stroke") is not None:
        D["stroke"] = float(cfg["stroke"])
    if cfg.get("shadow") is not None:
        D["shadow"] = float(cfg["shadow"])
    if cfg.get("kw_accent") is not None:
        D["kw_accent"] = bool(cfg["kw_accent"])
    if cfg.get("recenter") is not None:
        D["recenter"] = bool(cfg["recenter"])
    if cfg.get("max_lines") is not None:
        D["max_lines"] = max(1, int(cfg["max_lines"]))
    D["box"] = cfg.get("box")
    D["highlight"] = cfg.get("highlight") or D["accent"]
    return D


def _cap_metrics(D: dict, ctx) -> tuple[float, float, float]:
    """(ancho util px, tamano px, factor de espacio) de una linea de subtitulos."""
    P = {"x": D["x"], "y": D["y"], "align": "center", "width": 0.96}
    maxw, _ = _usable(P, ctx)
    return maxw, D["size"] * ctx.W, float(D.get("space", 1.0))


_HARD_GAP = 0.45     # s: pausa que siempre corta un grupo
_SQUEEZE = 1.1       # un grupo puede reducirse hasta ~9% para no dejar una palabra huerfana


def _segment(words: list, D: dict, ctx) -> list[list]:
    """Agrupa el flujo de palabras (tiempo final) en subtitulos por programacion dinamica.

    Los limites de frase del plan NO son cortes (en v1 son trozos de 3 palabras); se corta
    siempre en fin de oracion [.!?] o pausa > 0.45 s. Dentro de cada tramo se minimiza:
    desviacion del tamano ideal (2-4 palabras), grupos de una palabra (salvo largas o aisladas),
    terminar en palabra funcional ('y', 'de', 'como'...), ancho que no cabe; con bono por cortar
    tras coma o en una pausa."""
    n = len(words)
    if n == 0:
        return []
    maxw, fs, sk = _cap_metrics(D, ctx)
    max_lines = int(D.get("max_lines", 1))
    mw = max(1, int(D["max_words"]))
    ideal = min(mw, max(2, int(round(mw * 0.75))))
    hard = set()
    for i in range(n - 1):
        gap = words[i + 1]["start"] - words[i]["end"]
        if re.search(r"[.!?]$", words[i]["text"]) or gap > _HARD_GAP:
            hard.add(i)
    wcache: dict = {}

    def width(a, b):
        v = wcache.get((a, b))
        if v is None:
            v = wcache[(a, b)] = _spec_w(_Spec([(w["text"], False) for w in words[a:b]], D["font"]), fs, sk)
        return v

    def cost(a, b):
        m = b - a
        if m > mw:
            return float("inf")
        for i in range(a, b - 1):
            if i in hard:
                return float("inf")
        c = 0.3 * (m - ideal) ** 2
        # una coma dentro del grupo (no al final) es un corte natural desperdiciado
        c += sum(1.5 for i in range(a, b - 1) if re.search(r"[,;:]$", words[i]["text"]))
        seg_start = a == 0 or (a - 1) in hard
        seg_end = b == n or (b - 1) in hard
        if m > 1:
            over = width(a, b) / (maxw * max_lines)
            if over > _SQUEEZE:
                return float("inf")
            if over > 1.0:
                c += 1.2 + 6.0 * (over - 1.0)
            elif max_lines == 1:
                c += 0.6 * ease.clamp((over - 0.8) / 0.2)
        else:
            # una palabra sola se acepta si es larga, va aislada o cierra con coma ("Primero,")
            long_word = len(_norm(words[a]["text"])) >= 8 or bool(re.search(r"[,;:]$", words[a]["text"]))
            c += 0.2 if (seg_start and seg_end) else (0.8 if long_word else 2.2)
        last = words[b - 1]
        if not seg_end:
            if _is_func(last["text"]):
                c += 4.0
            if re.search(r"[,;:]$", last["text"]):
                c -= 1.2
            gap = words[b]["start"] - last["end"]
            c -= 1.0 * ease.clamp(gap / 0.35)
        dur = last["end"] - words[a]["start"]
        if dur > 2.4:
            c += 0.5 * (dur - 2.4)
        return c

    INF = float("inf")
    best = [INF] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for b in range(1, n + 1):
        for a in range(max(0, b - mw), b):
            if best[a] == INF:
                continue
            v = best[a] + cost(a, b)
            if v < best[b] - 1e-9:
                best[b], back[b] = v, a
    out, b = [], n
    while b > 0:
        a = back[b]
        out.append(words[a:b])
        b = a
    return out[::-1]


def _split_wide(gws: list, D: dict, ctx) -> list:
    """Red de seguridad: parte los grupos que no caben en `max_lines` lineas (balanceado)."""
    maxw, fs, sk = _cap_metrics(D, ctx)
    max_lines = int(D.get("max_lines", 1))
    out = []
    stack_ = list(reversed(gws))
    while stack_:
        g = stack_.pop()
        spec = _Spec([(w["text"], False) for w in g], D["font"])
        w = _spec_w(spec, fs, sk)
        if len(g) > 1 and w > maxw * max_lines * _SQUEEZE + 1e-6:
            cut = (len(g) + 1) // 2
            stack_.append(g[cut:])
            stack_.append(g[:cut])
        else:
            out.append(g)
    return out


def _caption_groups(phrases: list, cfg: dict, ctx) -> list[_Group]:
    D = _cap_cfg(cfg, ctx)
    words = []
    for ph in phrases or []:
        for w in ph or []:
            txt = _clean(_apply_case(str(w.get("text", "")), D["case"]), D["font"])
            if not txt:
                continue
            try:
                st = float(w.get("start", 0) or 0)
            except Exception:
                st = 0.0
            try:
                en = float(w.get("end", st) if w.get("end") is not None else st + 0.25)
            except Exception:
                en = st + 0.25
            words.append({"text": txt, "start": st, "end": en, "kw": bool(w.get("kw"))})
    # orden temporal estricto (las frases pueden venir encimadas)
    words.sort(key=lambda w: w["start"])
    for i in range(1, len(words)):
        if words[i]["start"] < words[i - 1]["start"]:
            words[i]["start"] = words[i - 1]["start"]
    for w in words:
        w["end"] = max(w["end"], w["start"] + 0.04)
    gws = _split_wide(_segment(words, D, ctx), D, ctx)
    groups = []
    for gi, g in enumerate(gws):
        start = g[0]["start"] - 0.06
        nxt = gws[gi + 1][0]["start"] - 0.06 if gi + 1 < len(gws) else None
        hold = 0.45
        end = g[-1]["end"] + hold
        fade = True
        if nxt is not None and nxt - g[-1]["end"] < 0.5:
            end = nxt
            fade = False
        elif nxt is not None:
            end = min(end, nxt)
        groups.append(_Group(g, start, max(end, start + 0.25), fade))
    # acento por palabra clave: como mucho una por grupo (la mas larga) y nunca en grupos seguidos
    if D.get("kw_accent"):
        prev = False
        for g in groups:
            cands = [i for i, w in enumerate(g.words) if w["kw"]]
            if cands and not prev:
                g.acc = {max(cands, key=lambda i: (len(_norm(g.words[i]["text"])), i))}
            prev = bool(g.acc)
    for gi, g in enumerate(groups):
        _layout_group(g, D, ctx, f"cap{gi}")
    return groups


def _layout_group(g: _Group, D: dict, ctx, key: str) -> None:
    P = {"x": D["x"], "y": D["y"], "align": "center", "gap": D["gap"], "size": D["size"],
         "space": float(D.get("space", 1.0)), "width": 0.96}
    maxw, _ = _usable(P, ctx)
    spec = _Spec([(w["text"], i in g.acc) for i, w in enumerate(g.words)], D["font"])
    fs = D["size"] * ctx.W
    sk = P["space"]
    specs = [spec]
    max_lines = int(D.get("max_lines", 1))
    if _spec_w(spec, fs, sk) > maxw and len(spec.words) > 1 and max_lines > 1:
        specs = _wrap_spec(spec, 2, sk)
        if max(_spec_w(s, fs, sk) for s in specs) > maxw and len(spec.words) > 2 and max_lines > 2:
            specs = _wrap_spec(spec, 3, sk)
    for s in specs:
        w = _spec_w(s, fs, sk)
        if w > maxw:
            s.scale *= maxw / w * 0.995
    lines, placed = _stack(specs, fs, P, ctx, D["color"], D["accent"])
    bw = _align_lines(lines, "center")
    g.rect, _bbox = _place(lines, placed, P, ctx, bw)
    g.lines, g.placed, g.fs = lines, placed, fs
    g.rooms = _rooms([ln.words for ln in lines])


def _hide_alpha(t: float, ranges, fade: float = 0.12) -> float:
    a = 1.0
    for r in ranges or []:
        try:
            s, e = float(r[0]), float(r[1])
        except Exception:
            continue
        d = max(s - t, t - e)
        a = min(a, ease.clamp(d / fade) if d > 0 else 0.0)
    return a


def _caps_key(phrases, cfg, ctx) -> tuple:
    """Llave de contenido (no de identidad): render puede reconstruir la lista cada cuadro."""
    n = sum(len(p or []) for p in phrases or [])
    h = hash("|".join(f"{w.get('text', '')}{'*' if w.get('kw') else ''}@{w.get('start', '')}-{w.get('end', '')}"
                      for p in phrases or [] for w in (p or [])))
    c = {k: v for k, v in (cfg or {}).items() if not k.startswith("_")}
    return (n, h, json.dumps(c, sort_keys=True, default=str), ctx.W, ctx.H, ctx.style)


def caption_groups(phrases_final: list, cfg: dict, ctx) -> list[dict]:
    """Grupos de subtitulos resultantes (para el Studio/tests): [{text, start, end, rect, lines, accent}]."""
    gs = _CAPS.get_or(_caps_key(phrases_final, cfg, ctx), lambda: _caption_groups(phrases_final, cfg, ctx))
    return [{"text": " ".join(w["text"] for w in g.words), "start": g.start, "end": g.end,
             "rect": (g.rect.left(), g.rect.top(), g.rect.right(), g.rect.bottom()),
             "lines": len(g.lines), "accent": [g.words[i]["text"] for i in sorted(g.acc)]} for g in gs]


def draw_captions(canvas, phrases_final, cfg, t, ctx) -> None:
    """Subtitulos por preset. `t` = tiempo final absoluto (s)."""
    if not cfg or not cfg.get("enabled", True) or (cfg.get("preset") or "clean_bold") == "none":
        return
    ha = _hide_alpha(t, cfg.get("_hide"))
    if ha <= 0.003:
        return
    groups = _CAPS.get_or(_caps_key(phrases_final, cfg, ctx), lambda: _caption_groups(phrases_final, cfg, ctx))
    if not groups:
        return
    # grupo activo (busqueda binaria por inicio)
    lo, hi = 0, len(groups)
    while lo < hi:
        mid = (lo + hi) // 2
        if groups[mid].start <= t:
            lo = mid + 1
        else:
            hi = mid
    gi = lo - 1
    if gi < 0:
        return
    g = groups[gi]
    D = _cap_cfg(cfg, ctx)
    # relevo entre grupos sin pausa (presets de grupo completo): el anterior sale hacia arriba
    if D["reveal"] == "group" and gi > 0:
        pg = groups[gi - 1]
        pe = ease.clamp((t - g.start) / 0.13)
        if not pg.fade_out and pe < 1.0 and abs(pg.end - g.start) < 1e-6:
            _draw_group(canvas, pg, D, t, ctx, ha, gi - 1, exit_p=pe)
    if t >= g.end:
        return
    _draw_group(canvas, g, D, t, ctx, ha, gi)


def _recenter_dx(g: _Group, t: float) -> dict:
    """Desplazamiento x por linea para que las palabras ya visibles queden centradas; cada
    palabra nueva mueve la linea con un resorte suave hacia el nuevo centro (llega a 0 cuando
    la linea esta completa)."""
    out = {}
    for li, ln in enumerate(g.lines):
        c = (ln.ink[0] + ln.ink[2]) / 2
        L = ln.ink[0]
        dx, prev = 0.0, None
        for i, pw in enumerate(g.placed):
            if pw.line != li:
                continue
            off = c - (L + pw.ox + pw.run.ink[2]) / 2
            tk = g.words[i]["start"] - 0.05
            if prev is None:
                dx = off
            else:
                dx += (off - prev) * ease.spring(t - tk, freq=2.8, damping=0.82)
            prev = off
        out[li] = dx
    return out


def _draw_group(canvas, g: _Group, D: dict, t: float, ctx, ha: float, gi: int, exit_p: float = 0.0) -> None:
    fs = g.fs
    final = not ctx.draft
    look = _Look(shadow=D["shadow"], stroke=2.0 * ctx.px(D["stroke"]), stroke_color="#000000")
    look.boost = _bg_boost(ctx, g.rect, D["color"])
    # salida del grupo (solo si hay pausa despues)
    ga = ha
    gdy = 0.0
    if g.fade_out:
        po = ease.clamp((t - (g.end - 0.16)) / 0.16)
        ga *= 1 - ease.in_quad(po)
        gdy = -0.12 * fs * ease.in_cubic(po)
    if exit_p > 0:
        ga *= 1 - ease.out_quad(exit_p)
        gdy -= 0.45 * fs * ease.out_cubic(exit_p)
        t = min(t, g.end - 1e-3)
    if ga <= 0.003:
        return
    reveal, anim, active = D["reveal"], D["anim"], D["active"]
    # entrada del grupo completo (karaoke / boxed)
    if reveal == "group" and exit_p <= 0:
        u = t - g.start
        ga *= ease.out_cubic(u / 0.14)
        gdy += (1 - ease.out_expo(u / 0.35)) * 0.22 * fs
    line_dx = _recenter_dx(g, t) if D.get("recenter") and reveal == "word" else None
    act_sc = float(D.get("active_scale", 0.0) or 0.0)
    units = []
    for i, (w, pw) in enumerate(zip(g.words, g.placed)):
        col = pw.color
        sc, dy, a, bl, rot = 1.0, 0.0, 1.0, 0.0, 0.0
        shader = None
        if reveal == "word":
            u = t - (w["start"] - 0.05)
            if u < 0:
                continue
            if anim == "pop":
                sc = 0.8 + 0.2 * ease.spring(u, freq=3.4, damping=0.6)
                a = ease.out_cubic(u / 0.08)
                dy = (1 - ease.out_cubic(u / 0.22)) * 0.1 * fs
            elif anim == "pop_strong":
                sc = 0.7 + 0.3 * ease.spring(u, freq=3.0, damping=0.62)
                a = ease.out_cubic(u / 0.07)
                rot = (ctx.rand(f"cap{gi}:{i}") - 0.5) * 5.0 * (1 - ease.out_cubic(u / 0.3))
            else:  # fade
                a = ease.smoothstep(u / 0.2)
                dy = (1 - ease.out_cubic(u / 0.3)) * 0.12 * fs
                bl = (1 - ease.out_cubic(u / 0.2)) * 0.05 * fs if final else 0.0
        if active == "color" and i not in g.acc:
            q = _active_bump(t, w)
            if q > 0.002:
                col = C.mix(D["color"], D["accent"], q)
        if act_sc > 0:
            q = _active_bump(t, w)
            sc *= 1 + act_sc * q
            dy -= 0.02 * fs * q
        elif active == "wipe":
            col = D["color"]
            if t >= w["end"]:
                col = D["accent"]
            elif t >= w["start"]:
                q = ease.clamp((t - w["start"]) / max(0.08, w["end"] - w["start"]))
                l, _t, r, _b = pw.run.ink
                x0 = pw.ox + l - 0.05 * fs
                xe = x0 + (r - l + 0.1 * fs) * q
                shader = skia.GradientShader.MakeLinear(
                    [skia.Point(xe - 0.12 * fs, 0), skia.Point(xe, 0)],
                    [C.color(D["accent"]), C.color(D["color"])])
        if a <= 0:
            continue
        sc = _soft_scale(sc, pw.run.ink[2] - pw.run.ink[0], 0.5, g.rooms.get(id(pw)))
        dx = line_dx.get(pw.line, 0.0) if line_dx else 0.0
        units.append(dict(run=pw.run, ox=pw.ox, oy=pw.oy, look=look, color=col, alpha=a * ga, dx=dx,
                          dy=dy + gdy, sc=sc, rot=rot, blur=bl, shader=shader, pivot=0.5))
    if D["preset"] == "boxed":
        rr = _boxed_pill(canvas, g, D, t, ga, gdy, ctx)
        if rr is not None:
            # el color de contraste sigue EXACTAMENTE a la pastilla: fuera, texto base con sombra;
            # dentro (mismo rect interpolado), texto en contraste sin sombra
            canvas.save()
            canvas.clipRRect(rr, skia.ClipOp.kDifference, True)
            _draw_units(canvas, units, ctx.draft)
            canvas.restore()
            on = _contrast(D["highlight"])
            on_look = _Look(shadow=0.0)
            canvas.save()
            canvas.clipRRect(rr, skia.ClipOp.kIntersect, True)
            _draw_units(canvas, [dict(u, color=on, look=on_look, shader=None) for u in units], ctx.draft)
            canvas.restore()
            return
    _draw_units(canvas, units, ctx.draft)


def _active_bump(t: float, w: dict) -> float:
    """0..1..0 mientras la palabra se pronuncia (sube en ~80 ms, baja suave)."""
    a = ease.out_cubic((t - w["start"] + 0.03) / 0.08)
    b = 1 - ease.in_out_quad((t - w["end"]) / 0.16) if t > w["end"] else 1.0
    return max(0.0, min(a, b))


def _pill_word(g: _Group, t: float) -> int:
    idx = -1
    for i, w in enumerate(g.words):
        if t >= w["start"] - 0.03:
            idx = i
    return idx


def _word_rect(pw: _W, fs: float) -> skia.Rect:
    l, t, r, b = pw.run.ink
    cap = pw.run.cap
    return skia.Rect.MakeLTRB(pw.ox + l - 0.2 * fs, pw.oy - cap - 0.19 * fs, pw.ox + r + 0.2 * fs,
                              pw.oy + 0.24 * fs)


def _boxed_rrect(g: _Group, t: float, gdy: float):
    """RRect (coordenadas del cuadro) de la pastilla de `boxed` en t, o None."""
    i = _pill_word(g, t)
    if i < 0:
        return None
    fs = g.fs
    cur = _word_rect(g.placed[i], fs)
    u = t - (g.words[i]["start"] - 0.03)
    if i > 0:
        prev = _word_rect(g.placed[i - 1], fs)
        k = ease.spring(u, freq=4.0, damping=0.78)
        rect = skia.Rect.MakeLTRB(*(pv + (cv - pv) * k for pv, cv in zip(
            (prev.left(), prev.top(), prev.right(), prev.bottom()),
            (cur.left(), cur.top(), cur.right(), cur.bottom()))))
        s = 1.0
    else:
        rect = cur
        s = 0.6 + 0.4 * ease.spring(u, freq=3.6, damping=0.62)
    rect = rect.makeOffset(0, gdy)
    cx, cy = rect.centerX(), rect.centerY()
    w, h = rect.width() * s, rect.height() * s
    r = skia.Rect.MakeLTRB(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    rad = 0.22 * fs * s
    return skia.RRect.MakeRectXY(r, rad, rad)


def _boxed_pill(canvas, g: _Group, D: dict, t: float, ga: float, gdy: float, ctx):
    """Pastilla de acento que se desliza (resorte) de palabra en palabra. -> RRect o None."""
    rr = _boxed_rrect(g, t, gdy)
    if rr is None:
        return None
    fs = g.fs
    sh = C.fill("#000000", 0.3 * ga)
    sh.setMaskFilter(_blur(0.14 * fs))
    r = rr.rect()
    rad = rr.getSimpleRadii().x() if hasattr(rr, "getSimpleRadii") else 0.22 * fs
    canvas.drawRRect(skia.RRect.MakeRectXY(r.makeOffset(0, 0.06 * fs), rad, rad), sh)
    canvas.drawRRect(rr, C.fill(D["highlight"], ga))
    return rr
