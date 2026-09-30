"""Contexto por cuadro y helpers de superficie/pintura compartidos por todos los renderers.

Contrato (docs/MOTION-ENGINE.md, seccion 4):
  - Overlay:   draw(canvas, item, lt, dur, ctx) -> None
  - Base:      apply(img: skia.Image, item, lt, dur, ctx) -> skia.Image
  - Transicion apply(img, prev, item, p, ctx) -> skia.Image
"""
from __future__ import annotations

import hashlib
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import skia

from . import assets

REF_W = 1080.0
# Zona segura SIMETRICA en x (regla de la marca: todo overlay deja el mismo espacio a la izquierda
# y a la derecha). El margen de 0.12 por lado viene del riel derecho de TikTok (likes/comentarios).
SAFE = {"top": 0.09, "bottom": 0.80, "left": 0.12, "right": 0.88}
XC = 0.5                                   # centro horizontal de todo overlay centrado
SYM_W = SAFE["right"] - SAFE["left"]       # ancho maximo de un overlay centrado (0.76)


# ---------------------------------------------------------------- color
def color(c: Any, alpha: float = 1.0) -> int:
    """'#RRGGBB' | '#RRGGBBAA' | (r,g,b[,a]) | int  ->  skia color (ARGB int)."""
    if c is None:
        c = "#FFFFFF"
    if isinstance(c, int):
        base = c
        a = (base >> 24) & 0xFF
        return skia.ColorSetARGB(int(a * alpha), (base >> 16) & 0xFF, (base >> 8) & 0xFF, base & 0xFF)
    if isinstance(c, (tuple, list)):
        r, g, b = (int(v) for v in c[:3])
        a = c[3] if len(c) > 3 else 255
        a = a if a > 1 else a * 255
        return skia.ColorSetARGB(int(max(0, min(255, a * alpha))), r, g, b)
    s = str(c).lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    a = int(s[6:8], 16) if len(s) >= 8 else 255
    return skia.ColorSetARGB(int(max(0, min(255, a * alpha))), r, g, b)


def rgb(c: Any) -> tuple[int, int, int]:
    v = color(c)
    return (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF


def mix(c1: Any, c2: Any, p: float) -> int:
    a, b = rgb(c1), rgb(c2)
    return skia.ColorSetRGB(*(int(a[i] + (b[i] - a[i]) * p) for i in range(3)))


def luminance(c: Any) -> float:
    r, g, b = (v / 255 for v in rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# ---------------------------------------------------------------- superficies
def new_frame(w: int, h: int) -> tuple[np.ndarray, skia.Surface]:
    """Buffer RGBA (premultiplicado) + surface skia que escribe directo en el."""
    arr = np.zeros((h, w, 4), np.uint8)
    surf = skia.Surface(arr, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
    return arr, surf


def image_from_rgba(arr: np.ndarray) -> skia.Image:
    """np uint8 HxWx4 (RGBA, alpha 255 o premultiplicado) -> skia.Image (copia)."""
    if arr.shape[2] == 3:
        a = np.empty(arr.shape[:2] + (4,), np.uint8)
        a[..., :3] = arr
        a[..., 3] = 255
        arr = a
    return skia.Image.fromarray(np.ascontiguousarray(arr), colorType=skia.kRGBA_8888_ColorType,
                                alphaType=skia.kPremul_AlphaType)


def image_from_gray(alpha: np.ndarray) -> skia.Image:
    """Mascara float [0,1] o uint8 HxW -> skia.Image A8 (para mascaras/matte)."""
    if alpha.dtype != np.uint8:
        alpha = np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return skia.Image.fromarray(np.ascontiguousarray(alpha), colorType=skia.kAlpha_8_ColorType)


def image_to_rgba(img: skia.Image) -> np.ndarray:
    return img.toarray(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)


def snapshot(surf: skia.Surface) -> skia.Image:
    return surf.makeImageSnapshot()


def offscreen(w: int, h: int) -> skia.Surface:
    return skia.Surface.MakeRaster(skia.ImageInfo.Make(w, h, skia.kRGBA_8888_ColorType,
                                                        skia.kPremul_AlphaType))


SAMPLING = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear)
SAMPLING_FAST = skia.SamplingOptions(skia.FilterMode.kLinear)
SAMPLING_CUBIC = skia.SamplingOptions(skia.CubicResampler.Mitchell())


# ---------------------------------------------------------------- pintura
def fill(c: Any, alpha: float = 1.0, aa: bool = True) -> skia.Paint:
    return skia.Paint(Color=color(c, alpha), AntiAlias=aa, Style=skia.Paint.kFill_Style)


def stroke(c: Any, width: float, alpha: float = 1.0, cap: str = "round", join: str = "round") -> skia.Paint:
    p = skia.Paint(Color=color(c, alpha), AntiAlias=True, Style=skia.Paint.kStroke_Style,
                   StrokeWidth=float(width))
    p.setStrokeCap({"round": skia.Paint.kRound_Cap, "butt": skia.Paint.kButt_Cap,
                    "square": skia.Paint.kSquare_Cap}[cap])
    p.setStrokeJoin({"round": skia.Paint.kRound_Join, "miter": skia.Paint.kMiter_Join,
                     "bevel": skia.Paint.kBevel_Join}[join])
    return p


def drop_shadow(dx: float, dy: float, blur: float, c: Any = "#000000", alpha: float = 0.45) -> skia.ImageFilter:
    return skia.ImageFilters.DropShadow(dx, dy, blur, blur, color(c, alpha))


@contextmanager
def layer(canvas: skia.Canvas, alpha: float = 1.0, bounds: skia.Rect | None = None,
          paint: skia.Paint | None = None):
    """saveLayer con alpha (para desvanecer grupos completos sin artefactos de solapado)."""
    if paint is None:
        paint = skia.Paint(Alphaf=max(0.0, min(1.0, alpha)))
    canvas.saveLayer(bounds, paint)
    try:
        yield canvas
    finally:
        canvas.restore()


@contextmanager
def transformed(canvas: skia.Canvas, x: float = 0, y: float = 0, scale: float = 1.0,
                rot_deg: float = 0.0, sx: float | None = None, sy: float | None = None):
    """Traslada a (x,y), rota y escala alrededor de ese punto."""
    canvas.save()
    canvas.translate(x, y)
    if rot_deg:
        canvas.rotate(rot_deg)
    canvas.scale(sx if sx is not None else scale, sy if sy is not None else scale)
    try:
        yield canvas
    finally:
        canvas.restore()


def trim_path_paint(base: skia.Paint, start: float, stop: float) -> skia.Paint:
    """Paint con TrimPathEffect: dibuja solo [start,stop] (0..1) del trazo (efecto 'se dibuja')."""
    p = skia.Paint(base)
    s, e = max(0.0, min(1.0, start)), max(0.0, min(1.0, stop))
    if e - s < 1e-4:
        p.setAlphaf(0.0)
        return p
    if s <= 1e-6 and e >= 1.0 - 1e-6:
        return p  # trazo completo: TrimPathEffect.Make(0,1) devuelve None
    eff = skia.TrimPathEffect.Make(s, e)
    if eff is not None:
        p.setPathEffect(eff)
    return p


def measure_text(fnt: skia.Font, text: str) -> tuple[float, float, float]:
    """(ancho, ascent(>0), descent(>0)) de un texto con la fuente dada."""
    w = fnt.measureText(text)
    m = fnt.getMetrics()
    return w, -m.fAscent, m.fDescent


# ---------------------------------------------------------------- contexto
def _hash32(s: str) -> int:
    return zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF


@dataclass
class FrameCtx:
    W: int = 1080
    H: int = 1920
    fps: float = 30.0
    n: int = 0
    t: float = 0.0                    # tiempo final
    t_orig: float = 0.0               # tiempo original equivalente
    quality: str = "final"            # "final" | "draft"
    seed: int = 7
    style: str = "dinamico"
    accent: str | None = None         # acento del PROYECTO (plan.fx.accent): gana al del estilo
    shot: dict | None = None
    face: tuple | None = None         # (cx, cy, w, h) normalizado en el cuadro de salida
    words: list = field(default_factory=list)   # palabras con tiempos FINALES [{text,start,end,kw}]
    entities: dict = field(default_factory=dict)
    _matte_fn: Callable[[], np.ndarray | None] | None = None
    _subject_fn: Callable[[], skia.Image | None] | None = None
    _cache: dict = field(default_factory=dict)

    # -------------------------------------------------- unidades
    @property
    def s(self) -> float:
        """Factor de escala px de referencia (1080 de ancho) -> px reales."""
        return self.W / REF_W

    def px(self, v: float) -> float:
        return float(v) * self.W / REF_W

    def X(self, x: float) -> float:
        return float(x) * self.W

    def Y(self, y: float) -> float:
        return float(y) * self.H

    @property
    def draft(self) -> bool:
        return self.quality == "draft"

    # -------------------------------------------------- aleatoriedad determinista
    def rng(self, key: str) -> np.random.Generator:
        return np.random.default_rng((self.seed * 1_000_003 + _hash32(str(key))) & 0xFFFFFFFF)

    def rand(self, key: str) -> float:
        """Numero [0,1) estable por key (no depende del cuadro)."""
        h = hashlib.blake2b(f"{self.seed}:{key}".encode(), digest_size=8).digest()
        return int.from_bytes(h, "little") / 2 ** 64

    # -------------------------------------------------- recursos
    @property
    def palette(self) -> dict:
        """Paleta del estilo; con acento del proyecto (plan.fx.accent) su "accent" es ese: lo que no
        trae color propio (una tarjeta del director, el titulo que el usuario agrego) sale con el
        acento elegido, igual que en la vista del Studio."""
        pal = assets.palette(self.style)
        if not self.accent:
            return pal
        got = self._cache.get("_palette")
        if got is None:
            got = self._cache["_palette"] = dict(pal, accent=self.accent)
        return got

    def font(self, name: str | None, size_px: float) -> skia.Font:
        return assets.font(name, size_px)

    def icon(self, name: str, variant: str = "outline"):
        return assets.icon_paths(name, variant)

    # -------------------------------------------------- sujeto
    def matte(self) -> np.ndarray | None:
        if "matte" not in self._cache:
            self._cache["matte"] = self._matte_fn() if self._matte_fn else None
        return self._cache["matte"]

    def subject_image(self) -> skia.Image | None:
        if "subject" not in self._cache:
            self._cache["subject"] = self._subject_fn() if self._subject_fn else None
        return self._cache["subject"]

    def base_image(self) -> skia.Image | None:
        """Cuadro base ya procesado (camara+grade+efectos), sin overlays. Lo fija render.py."""
        return self._cache.get("base_image")

    def words_in(self, start: float, end: float) -> list:
        """Palabras habladas (tiempo final) que intersectan [start, end)."""
        return [w for w in self.words if w["end"] > start and w["start"] < end]

    def face_rect_px(self) -> skia.Rect | None:
        if not self.face:
            return None
        cx, cy, w, h = self.face
        return skia.Rect.MakeXYWH((cx - w / 2) * self.W, (cy - h / 2) * self.H, w * self.W, h * self.H)


def demo_ctx(W: int = 1080, H: int = 1920, t: float = 0.0, fps: float = 30.0, **kw) -> FrameCtx:
    """Contexto de prueba para tests/demos de modulos (sin video)."""
    return FrameCtx(W=W, H=H, fps=fps, n=int(round(t * fps)), t=t, t_orig=t, **kw)
