"""Stub de escenas: tarjeta de titulo a pantalla completa."""
import skia

from motion import assets, ctx as C


def title_card(canvas, item, lt, dur, ctx):
    canvas.drawPaint(C.fill("#1B1B22"))
    t = (item.get("text") or {}).get("text", "")
    f = assets.font("DMSerifDisplay", 0.1 * ctx.W)
    w, a, d = C.measure_text(f, t)
    canvas.drawString(t, ctx.W / 2 - w / 2, ctx.H * 0.45, f, C.fill("#F7F4EE"))


RENDERERS = {"title_card": title_card}


def draw_background(canvas, kind, colors, lt, ctx):
    canvas.drawPaint(C.fill(colors[0] if colors else "#222222"))
