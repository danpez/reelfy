"""Stub de texto: titulo en caja y subtitulos palabra por palabra."""
import skia

from motion import assets, ctx as C, ease


def title(canvas, item, lt, dur, ctx):
    pin, pout = ease.envelope(lt, dur, 0.25, 0.2)
    a = ease.out_cubic(pin) * (1 - pout)
    f = assets.font("Poppins-Black", item.get("size", 0.08) * ctx.W)
    txt = str(item.get("text", "")).upper()
    w, asc, desc = C.measure_text(f, txt)
    x, y = item.get("x", 0.5) * ctx.W, item.get("y", 0.25) * ctx.H
    r = skia.Rect.MakeXYWH(x - w / 2 - ctx.px(30), y - asc - ctx.px(20), w + ctx.px(60),
                           asc + desc + ctx.px(40))
    with C.layer(canvas, a):
        canvas.drawRRect(skia.RRect.MakeRectXY(r, ctx.px(24), ctx.px(24)), C.fill("#111111", 0.85))
        canvas.drawString(txt, x - w / 2, y, f, C.fill(item.get("color", "#FFD23F")))
    # palabras sincronizadas (_words) como puntos
    for i, wd in enumerate(item.get("_words", [])):
        on = wd["start"] <= lt
        canvas.drawCircle(x - w / 2 + i * ctx.px(22), r.bottom() + ctx.px(20), ctx.px(7),
                          C.fill("#FFD23F" if on else "#FFFFFF", 0.9 * a))


RENDERERS = {"title_pop": title, "stack": title}


def draw_captions(canvas, phrases_final, cfg, t, ctx):
    for a, b in cfg.get("_hide", []):
        if a <= t < b:
            return
    for ph in phrases_final:
        if ph[0]["start"] <= t < ph[-1]["end"] + 0.15:
            f = assets.font(cfg.get("font"), cfg.get("size", 0.052) * ctx.W)
            txt = " ".join(w["text"] for w in ph)
            w, asc, desc = C.measure_text(f, txt)
            x, y = ctx.W / 2 - w / 2, cfg.get("y", 0.72) * ctx.H
            p = C.stroke("#000000", ctx.px(8))
            canvas.drawString(txt, x, y, f, p)
            canvas.drawString(txt, x, y, f, C.fill(cfg.get("color", "#FFFFFF")))
            return
