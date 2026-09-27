"""Stub de transiciones: flash (centrada) y slide (mezcla prev congelado + actual)."""
import skia

from motion import ctx as C, ease

WINDOWS = {"flash": 0.5, "slide": 0.0}


def _surf(ctx):
    return skia.Surface.MakeRaster(skia.ImageInfo.Make(ctx.W, ctx.H, skia.kRGBA_8888_ColorType,
                                                       skia.kPremul_AlphaType))


def flash(img, prev, item, p, ctx):
    s = _surf(ctx)
    c = s.getCanvas()
    c.drawImage(img, 0, 0)
    a = 1 - abs(p - 0.5) * 2
    c.drawPaint(C.fill(item.get("color", "#FFFFFF"), ease.smoothstep(a) * 0.9))
    return s.makeImageSnapshot()


def slide(img, prev, item, p, ctx):
    s = _surf(ctx)
    c = s.getCanvas()
    e = ease.in_out_cubic(p)
    c.drawImage(prev, -e * ctx.W, 0)
    c.drawImage(img, (1 - e) * ctx.W, 0)
    return s.makeImageSnapshot()


RENDERERS = {"flash": flash, "slide": slide}
