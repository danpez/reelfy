"""Stub de graficos: circulo que se dibuja y barra de progreso."""
import skia

from motion import ctx as C, ease


def doodle(canvas, item, lt, dur, ctx):
    x, y, r = item.get("x", 0.5) * ctx.W, item.get("y", 0.3) * ctx.H, item.get("size", 0.2) * ctx.W / 2
    p = skia.Path()
    p.addCircle(x, y, r)
    pr = ease.out_cubic(min(1.0, lt / 0.5))
    paint = C.stroke(item.get("color", "#FFFFFF"), ctx.px(10))
    # ctx.trim_path_paint(0, 1) falla (TrimPathEffect.Make devuelve None en rango completo)
    canvas.drawPath(p, paint if pr >= 0.999 else C.trim_path_paint(paint, 0, pr))


def progress(canvas, item, lt, dur, ctx):
    canvas.drawRect(skia.Rect.MakeXYWH(0, 0, ctx.W * (lt / max(1e-6, dur)), ctx.px(10)),
                    C.fill(item.get("color", "#FFD23F")))


RENDERERS = {"doodle": doodle, "progress": progress}
