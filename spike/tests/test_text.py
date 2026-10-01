"""Tests de motion/text.py (tipografia: titulos cineticos y subtitulos).

Correr: cd spike && .venv/bin/python -m pytest tests/test_text.py -q
Sin video ni datos del cliente: frases sinteticas con tiempos por palabra.
"""
import os
import time

import numpy as np
import pytest
import skia

from motion import ctx as C
from motion import text as T
from motion.ctx import SAFE

W, H = 1080, 1920
PRESETS = ["title_pop", "stack", "cascade", "serif_elegant", "script_accent", "behind_giant",
           "highlight_marker", "typewriter", "counter", "kicker"]

def _load_factor() -> float:
    """Holgura de los umbrales de rendimiento cuando la maquina esta cargada por otros procesos
    (la Mac se comparte con otros renders): carga/nucleos, entre 1 y 3."""
    try:
        la = os.getloadavg()[0]
    except (OSError, AttributeError):
        return 1.0
    return min(3.0, max(1.0, la / (os.cpu_count() or 8)))



def _canvas(w=W, h=H):
    arr, surf = C.new_frame(w, h)
    return arr, surf.getCanvas()


def _ctx(t=0.0, quality="final", w=W, h=H, seed=7):
    return C.demo_ctx(W=w, H=h, t=t, quality=quality, seed=seed)


def _item(preset, **kw):
    base = {
        "title_pop": {"text": "¿Ya sientes sarro?", "accent_words": ["sarro"]},
        "stack": {"text": "HOY\nte revelo\nel secreto"},
        "cascade": {"text": "para llevar\nla edición\nde video"},
        "serif_elegant": {"text": "Te has preguntado", "accent_words": ["preguntado"]},
        "script_accent": {"text": "música"},
        "behind_giant": {"text": "dientes"},
        "highlight_marker": {"text": "Cuesta solo 800 pesos", "accent_words": ["800", "pesos"]},
        "typewriter": {"text": "Av. Central 123"},
        "counter": {"from": 0, "to": 1000, "prefix": "$", "suffix": "MXN", "text": "Limpieza"},
        "kicker": {"kicker": "Ubicación", "text": "A 2 cuadras de la Glorieta"},
    }[preset]
    it = {"id": f"it_{preset}", "preset": preset, **base}
    it.update(kw)
    return it


def _alpha_bbox(arr, thr=40):
    ys, xs = np.nonzero(arr[..., 3] > thr)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max(), ys.max()


def setup_function(_f):
    T.clear_cache()


# ---------------------------------------------------------------- registro y contrato
def test_registry_complete():
    assert set(PRESETS) <= set(T.RENDERERS)
    for name, fn in T.RENDERERS.items():
        assert callable(fn), name
    assert set(T.CAPTION_PRESETS) == {"clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps"}


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("quality", ["final", "draft"])
def test_renders_every_preset(preset, quality):
    w, h = (W, H) if quality == "final" else (W // 2, H // 2)
    dur = 2.4
    item = _item(preset)
    drawn = []
    for lt in (0.0, 0.05, 0.3, 0.8, 1.5, 2.2, 2.39):
        arr, cv = _canvas(w, h)
        T.RENDERERS[preset](cv, item, lt, dur, _ctx(lt, quality, w, h))
        drawn.append(int((arr[..., 3] > 0).sum()))
    assert max(drawn) > 500, (preset, drawn)
    # a media animacion hay texto en pantalla
    assert drawn[4] > 500, (preset, drawn)


@pytest.mark.parametrize("preset", PRESETS)
def test_nothing_outside_window(preset):
    item = _item(preset)
    for lt in (-0.1, 2.5):
        arr, cv = _canvas()
        T.RENDERERS[preset](cv, item, lt, 2.4, _ctx(lt))
        assert (arr[..., 3] > 0).sum() == 0, (preset, lt)


@pytest.mark.parametrize("preset", PRESETS)
def test_deterministic(preset):
    item = _item(preset)
    outs = []
    for _ in range(2):
        T.clear_cache()
        arr, cv = _canvas()
        T.RENDERERS[preset](cv, item, 0.9, 2.4, _ctx(0.9))
        outs.append(arr.copy())
    assert np.array_equal(outs[0], outs[1])


# ---------------------------------------------------------------- SAFE y autoajuste
@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("variant", ["default", "long", "huge", "edge"])
def test_rest_ink_inside_safe(preset, variant):
    kw = {}
    if variant == "long":
        kw["text"] = "Una buena rutina mejora muchísimo tu energía, tu sueño y también tu ánimo diario"
    elif variant == "huge":
        kw["size"] = 0.5
    elif variant == "edge":
        kw.update({"x": 0.95, "y": 0.97})
    item = _item(preset, **kw)
    r = T.measure(item, _ctx())
    assert not r.isEmpty()
    tol = 1.0
    assert r.left() >= SAFE["left"] * W - tol, (preset, variant, r)
    assert r.right() <= SAFE["right"] * W + tol, (preset, variant, r)
    assert r.top() >= SAFE["top"] * H - tol, (preset, variant, r)
    assert r.bottom() <= SAFE["bottom"] * H + tol, (preset, variant, r)


@pytest.mark.parametrize("preset", ["title_pop", "stack", "serif_elegant", "behind_giant", "kicker"])
def test_rendered_pixels_inside_safe(preset):
    """Sin sombra, la tinta dibujada en reposo no sale de SAFE (x) ni del area util (y)."""
    item = _item(preset, shadow=0, anim_out="none",
                 text="Reserva tu lugar hoy en el link de mi perfil")
    arr, cv = _canvas()
    T.RENDERERS[preset](cv, item, 1.9, 2.4, _ctx(1.9))
    bb = _alpha_bbox(arr)
    assert bb is not None
    x0, y0, x1, y1 = bb
    assert x0 >= SAFE["left"] * W - 3 and x1 <= SAFE["right"] * W + 3, (preset, bb)
    assert y0 >= SAFE["top"] * H - 3 and y1 <= SAFE["bottom"] * H + 3, (preset, bb)


def test_never_cuts_words():
    item = _item("title_pop", text="Otorrinolaringólogo", size=0.2)
    P, B = T._get_block(item, "title_pop", _ctx())
    assert [w.run.text for w in B.words] == ["Otorrinolaringólogo"]
    r = T.measure(item, _ctx())
    assert r.width() <= (SAFE["right"] - SAFE["left"]) * W + 1


def test_autofit_uses_width_and_wraps_balanced():
    item = _item("title_pop", text="Una buena asesoría lo cambia todo")
    P, B = T._get_block(item, "title_pop", _ctx())
    assert 2 <= len(B.lines) <= 3
    widths = [l.ink[2] - l.ink[0] for l in B.lines]
    assert max(widths) / min(widths) < 1.9      # lineas balanceadas, sin viudas
    usable = 2 * min(0.5 - SAFE["left"], SAFE["right"] - 0.5) * W
    assert max(widths) > 0.7 * usable or B.fs >= 0.124 * W  # llena el ancho o topa max_size


def test_explicit_size_respected():
    item = _item("title_pop", text="Hola", size=0.08)
    P, B = T._get_block(item, "title_pop", _ctx())
    assert abs(B.fs - 0.08 * W) < 1e-6


# ---------------------------------------------------------------- tipografia
def test_kerning_applied_for_serif():
    r = T._run("Te", "DMSerifDisplay", 200)
    f = r.font
    raw = f.measureText("T")
    # la 'e' se mete bajo la T (kerning negativo) respecto al avance sin shaping
    assert r.xpos[1] < raw - 1.0


def test_spanish_glyphs_and_emoji_stripped():
    s = T._clean("¿Ya sientes sarro? ñ \U0001F9B7", "Poppins-ExtraBold")
    assert s.startswith("¿") and "ñ" in s and "\U0001F9B7" not in s
    r = T._run("¿Añó?", "Poppins-ExtraBold", 100)
    assert all(g != 0 for g in r.glyphs)


def test_stack_auto_hierarchy():
    item = _item("stack", text="HOY\nte revelo\nel secreto")
    P, B = T._get_block(item, "stack", _ctx())
    sizes = [l.size for l in B.lines]
    assert sizes[0] == max(sizes)
    assert sizes[1] < sizes[0] * 0.8 and sizes[2] < sizes[0] * 0.8


def test_stack_tight_optical_leading():
    item = _item("stack", text="HOY\nte revelo")
    P, B = T._get_block(item, "stack", _ctx())
    gap = B.lines[1].ink[1] - B.lines[0].ink[3]
    assert gap < 0.25 * B.lines[1].size       # apilado compacto (no el ascent de la fuente)


def test_cascade_steps_right_and_grows():
    item = _item("cascade")
    P, B = T._get_block(item, "cascade", _ctx())
    lefts = [l.ink[0] for l in B.lines]
    assert lefts[0] < lefts[1] < lefts[2]
    assert B.lines[-1].size > B.lines[0].size


def test_serif_accent_is_italic():
    item = _item("serif_elegant")
    P, B = T._get_block(item, "serif_elegant", _ctx())
    acc = [w for w in B.words if w.accent]
    assert acc and all("Italic" in w.run.fname or w.run.skew for w in acc)


def test_accent_color_present():
    item = _item("title_pop", anim_out="none")
    arr, cv = _canvas()
    T.RENDERERS["title_pop"](cv, item, 1.5, 2.4, _ctx(1.5))
    acc = np.array(C.rgb("#FFD23F"))
    d = np.abs(arr[..., :3].astype(int) - acc).sum(-1)
    assert ((d < 30) & (arr[..., 3] > 250)).sum() > 200


def test_highlight_marker_band_and_contrast_text():
    item = _item("highlight_marker", anim_out="none")
    arr, cv = _canvas()
    T.RENDERERS["highlight_marker"](cv, item, 2.3, 2.4, _ctx(2.3))
    acc = np.array(C.rgb("#FFD23F"))
    d = np.abs(arr[..., :3].astype(int) - acc).sum(-1)
    band = (d < 60) & (arr[..., 3] > 240)
    assert band.sum() > 2000
    ys, xs = np.nonzero(band)
    sub = arr[ys.min():ys.max(), xs.min():xs.max(), :3].astype(int)
    dark = (sub.sum(-1) < 120).sum()
    assert dark > 300                              # texto oscuro sobre el marcador


def test_box_draws_rounded_background():
    item = _item("title_pop", text="Agenda tu cita", box="#FFFFFF", shadow=0, anim_out="none")
    arr, cv = _canvas()
    T.RENDERERS["title_pop"](cv, item, 1.5, 2.0, _ctx(1.5))
    white = (arr[..., :3].min(-1) > 245) & (arr[..., 3] > 250)
    dark = (arr[..., :3].max(-1) < 40) & (arr[..., 3] > 250)
    assert white.sum() > 20000 and dark.sum() > 2000


def test_behind_default_and_override():
    assert T.is_behind({"preset": "behind_giant"})
    assert not T.is_behind({"preset": "behind_giant", "behind": False})
    assert T.is_behind({"preset": "title_pop", "behind": True})
    assert not T.is_behind({"preset": "title_pop"})


# ---------------------------------------------------------------- contador
def test_number_format():
    assert T._fmt_num(1000, 0) == "1,000"
    assert T._fmt_num(1234567.891, 2) == "1,234,567.89"
    assert T._fmt_num(1500, 0, ".") == "1.500"
    assert T._fmt_num(-42, 0) == "-42"


def test_counter_counts_monotonic_and_lands():
    item = _item("counter")
    vals = [int(T.counter_text(item, lt, 2.4).replace(",", "")) for lt in np.linspace(0, 2.4, 30)]
    assert vals[0] == 0 and vals[-1] == 1000
    assert all(b >= a for a, b in zip(vals, vals[1:]))
    assert T.counter_text(item, 1.6, 2.4) == "1,000"


# ---------------------------------------------------------------- sincronia con la voz
def test_word_times_follow_voice():
    spoken = [{"text": "¿Ya", "start": 0.10, "end": 0.3}, {"text": "sientes", "start": 0.40, "end": 0.8},
              {"text": "sarro?", "start": 0.95, "end": 1.4}]
    ts = T._unit_times(["ya", "sientes", "sarro"], spoken, 3.0, 0.1)
    assert ts == pytest.approx([0.05, 0.35, 0.90], abs=1e-6)


def test_word_times_interpolate_unmatched_and_monotonic():
    spoken = [{"text": "una", "start": 0.0, "end": 0.2}, {"text": "buena", "start": 0.3, "end": 0.5},
              {"text": "limpieza", "start": 0.9, "end": 1.3}]
    ts = T._unit_times(["una", "super", "limpieza", "dental"], spoken, 3.0, 0.1)
    assert ts[0] == 0.0 and 0.0 < ts[1] < ts[2]
    assert ts[2] == pytest.approx(0.85)
    assert ts[3] > ts[2]
    assert all(b >= a for a, b in zip(ts, ts[1:]))


def test_word_times_without_voice_stagger_and_clamp():
    ts = T._unit_times(["a", "b", "c", "d"], None, 0.6, 0.2)
    assert ts[0] == 0.0 and all(b >= a for a, b in zip(ts, ts[1:]))
    assert max(ts) <= 0.6 - 0.45 + 1e-9


def test_synced_title_reveals_word_by_word():
    item = _item("title_pop", text="Necesitamos hablar",
                 _words=[{"text": "Necesitamos", "start": 0.0, "end": 0.6},
                         {"text": "hablar.", "start": 0.9, "end": 1.3}])
    counts = []
    for lt in (0.3, 1.2):
        arr, cv = _canvas()
        T.RENDERERS["title_pop"](cv, item, lt, 2.4, _ctx(lt))
        counts.append(int((arr[..., 3] > 128).sum()))
    assert counts[1] > counts[0] * 1.2


def test_typewriter_glyph_times_monotonic():
    item = _item("typewriter")
    P, B = T._get_block(item, "typewriter", _ctx())
    times = T._get_times(item, P, B, 2.4, "typewriter")
    g = T._glyph_times(B, times, 2.4, 1 / 17.0)
    flat = [t for w in g for t in w]
    assert all(b >= a for a, b in zip(flat, flat[1:]))
    assert flat[-1] < 2.4


# ---------------------------------------------------------------- subtitulos
def _phrases():
    words = "Una buena rutina mejora muchísimo tu energía, tu sueño y también tu ánimo diario.".split()
    ph, out, t = [], [], 1.0
    for i, w in enumerate(words):
        ph.append({"text": w, "start": round(t, 3), "end": round(t + 0.25, 3), "kw": w in ("rutina", "diario.")})
        t += 0.3
        if len(ph) == 5:
            out.append(ph)
            ph = []
            t += 0.6
    if ph:
        out.append(ph)
    return out


def _cfg(preset="clean_bold", **kw):
    c = {"enabled": True, "preset": preset, "y": 0.72, "size": 0.052, "font": "Poppins-ExtraBold",
         "color": "#FFFFFF", "accent": "#FFD23F", "case": "none", "max_words": 4, "hide_during_titles": True}
    c.update(kw)
    return c


def test_caption_groups_balanced_and_safe():
    groups = T.caption_groups(_phrases(), _cfg(), _ctx())
    sizes = [len(g["text"].split()) for g in groups]
    assert max(sizes) <= 4
    assert 1 not in sizes                         # 5 palabras -> 3+2, nunca 4+1
    for a, b in zip(groups, groups[1:]):
        assert a["end"] <= b["start"] + 1e-9      # sin encimarse
    for g in groups:
        l, t, r, b = g["rect"]
        assert l >= SAFE["left"] * W - 1 and r <= SAFE["right"] * W + 1
        assert t >= SAFE["top"] * H - 1 and b <= SAFE["bottom"] * H + 1


def test_caption_breaks_on_sentence_end():
    ph = [[{"text": "Hola.", "start": 0.0, "end": 0.3}, {"text": "Adiós", "start": 0.35, "end": 0.6}]]
    groups = T.caption_groups(ph, _cfg(), _ctx())
    assert [g["text"] for g in groups] == ["Hola.", "Adiós"]


def test_caption_overlapping_phrases_sorted():
    ph = [[{"text": "en", "start": 1.0, "end": 1.2}, {"text": "Avenida", "start": 1.25, "end": 1.6},
           {"text": "Central", "start": 1.65, "end": 2.1}],
          [{"text": "123,", "start": 1.9, "end": 2.4}]]
    groups = T.caption_groups(ph, _cfg(max_words=5), _ctx())
    text = " ".join(g["text"] for g in groups)
    assert text.index("Central") < text.index("123")


@pytest.mark.parametrize("preset", ["clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps"])
def test_captions_draw_and_hide(preset):
    ph = _phrases()
    cfg = _cfg(preset, _hide=[[1.5, 2.0]])
    arr, cv = _canvas()
    T.draw_captions(cv, ph, cfg, 1.35, _ctx(1.35))
    assert (arr[..., 3] > 0).sum() > 800
    arr, cv = _canvas()
    T.draw_captions(cv, ph, cfg, 1.7, _ctx(1.7))
    assert (arr[..., 3] > 0).sum() == 0          # dentro de _hide no se dibuja
    arr, cv = _canvas()
    T.draw_captions(cv, ph, cfg, 0.2, _ctx(0.2))
    assert (arr[..., 3] > 0).sum() == 0          # antes de la primera palabra


def test_hide_alpha_fades_at_edges():
    assert T._hide_alpha(1.0, [[2.0, 3.0]]) == 1.0
    assert 0.0 < T._hide_alpha(1.95, [[2.0, 3.0]]) < 1.0
    assert T._hide_alpha(2.5, [[2.0, 3.0]]) == 0.0


def test_captions_disabled_or_none():
    for cfg in (_cfg(enabled=False), _cfg("none"), {}):
        arr, cv = _canvas()
        T.draw_captions(cv, _phrases(), cfg, 1.35, _ctx(1.35))
        assert (arr[..., 3] > 0).sum() == 0


def test_clean_bold_reveals_word_by_word():
    ph = _phrases()
    counts = []
    for t in (1.1, 1.45, 1.75):
        arr, cv = _canvas()
        T.draw_captions(cv, ph, _cfg(), t, _ctx(t))
        counts.append(int((arr[..., 3] > 128).sum()))
    assert counts[0] < counts[1] < counts[2]


def test_karaoke_shows_whole_group():
    ph = _phrases()
    a1, cv = _canvas()
    T.draw_captions(cv, ph, _cfg("karaoke"), 1.2, _ctx(1.2))
    a2, cv = _canvas()
    T.draw_captions(cv, ph, _cfg("karaoke"), 1.7, _ctx(1.7))
    n1, n2 = (a1[..., 3] > 128).sum(), (a2[..., 3] > 128).sum()
    assert abs(int(n1) - int(n2)) < 0.15 * n2     # el grupo completo visible desde el inicio


def test_bold_caps_uppercase():
    groups = T.caption_groups(_phrases(), _cfg("bold_caps"), _ctx())
    assert all(g["text"] == g["text"].upper() for g in groups)
    assert max(len(g["text"].split()) for g in groups) <= 4


def test_captions_draft_scale():
    ph = _phrases()
    arr, cv = _canvas(W // 2, H // 2)
    T.draw_captions(cv, ph, _cfg(), 1.8, _ctx(1.8, "draft", W // 2, H // 2))
    assert (arr[..., 3] > 0).sum() > 200


# ---------------------------------------------------------------- rendimiento
def _bench(fn, n=40):
    best = 1e9
    for _ in range(3):
        s = time.perf_counter()
        for i in range(n):
            fn(i)
        best = min(best, (time.perf_counter() - s) / n * 1000)
    return best


@pytest.mark.parametrize("preset", PRESETS)
def test_perf_budget_final(preset):
    item = _item(preset)
    arr, cv = _canvas()
    T.RENDERERS[preset](cv, item, 0.5, 2.4, _ctx(0.5))

    def one(i):
        lt = 2.4 * i / 40
        T.RENDERERS[preset](cv, item, lt, 2.4, _ctx(lt))

    ms = _bench(one)
    assert ms < 15.0 * _load_factor(), (preset, ms)                # fraccion del presupuesto de 60 ms/cuadro


def test_perf_captions():
    ph = _phrases()
    arr, cv = _canvas()
    cfg = _cfg()
    T.draw_captions(cv, ph, cfg, 1.2, _ctx(1.2))

    def one(i):
        t = 1.0 + i * 0.05
        T.draw_captions(cv, ph, cfg, t, _ctx(t))

    assert _bench(one) < 6.0 * _load_factor()


# ================================================================ regresiones (revision critica)
import math  # noqa: E402

YELLOW = "#FFD23F"


def _near(arr, hexcol, tol=40, amin=240):
    c = np.array(C.rgb(hexcol))
    d = np.abs(arr[..., :3].astype(int) - c).sum(-1)
    return (d < tol) & (arr[..., 3] >= amin)


def _unit_rect(u):
    """Caja (x0, y0, x1, y1) de la tinta de una unidad ya transformada (como _draw_run)."""
    run, g = u["run"], u.get("glyph")
    if g is None:
        l, t, r, b = run.ink
        gx = 0.0
    else:
        l, t, r, b = run.gink[g]
        gx = run.xpos[g]
    x0, oy = u["ox"] + gx, u["oy"]
    if u.get("origin") is not None:
        cx, cy = u["origin"]
    else:
        pv = u.get("pivot", 0.5)
        cx, cy = x0 + l + (r - l) * pv, oy + (t + b) / 2
    sc, dx, dy = u.get("sc", 1.0), u.get("dx", 0.0), u.get("dy", 0.0)
    a = math.radians(u.get("rot", 0.0) or 0.0)
    ca, sa = math.cos(a), math.sin(a)
    pts = []
    for px, py in ((x0 + l, oy + t), (x0 + r, oy + t), (x0 + r, oy + b), (x0 + l, oy + b)):
        vx, vy = (px - cx) * sc, (py - cy) * sc
        pts.append((cx + dx + vx * ca - vy * sa, cy + dy + vx * sa + vy * ca))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


class _Capture:
    """Envuelve T._draw_units para registrar las unidades pintadas en cada llamada."""

    def __init__(self, monkeypatch):
        self.calls = []
        orig = T._draw_units

        def spy(canvas, units, draft):
            self.calls.append([dict(u) for u in units])
            return orig(canvas, units, draft)

        monkeypatch.setattr(T, "_draw_units", spy)

    def reset(self):
        self.calls = []


def _min_word_gap(units):
    """Hueco minimo (en em) entre palabras contiguas de la misma linea."""
    rows = {}
    for u in units:
        if u.get("glyph") is not None or u["alpha"] < 0.05:
            continue
        rows.setdefault(round(u["oy"], 1), []).append(u)
    worst = 9.0
    for us in rows.values():
        rs = sorted((_unit_rect(u), u["run"].size) for u in us)
        for (a, sa), (b, sb) in zip(rs, rs[1:]):
            worst = min(worst, (b[0] - a[2]) / min(sa, sb))
    return worst


# ---------------------------------------------------------------- highlight_marker con `box`
def _marker_director():
    return {"id": "m1", "preset": "highlight_marker", "text": "Cuesta solo\n800 pesos", "size": 0.072,
            "font": "Poppins-SemiBold", "color": "#FFFFFF", "accent": "#0E0E12", "shadow": 0.3,
            "anim_in": "pop", "anim_out": "none", "accent_words": ["800", "pesos."], "box": YELLOW,
            "y": 0.66}


def test_marker_director_item_box_is_marker_not_line_box():
    item = _marker_director()
    arr, cv = _canvas()
    T.RENDERERS["highlight_marker"](cv, item, 1.9, 2.4, _ctx(1.9))
    P, B = T._get_block(item, "highlight_marker", _ctx())
    yel = _near(arr, YELLOW, 60, 200)
    l0 = B.lines[0]
    # linea 1 (sin acentos): nada de amarillo y texto claro
    y0, y1 = int(l0.ink[1]) - 4, int(l0.ink[3]) + 4
    x0, x1 = int(l0.ink[0]) - 4, int(l0.ink[2]) + 4
    assert yel[y0:y1, x0:x1].sum() < 50
    bright = (arr[y0:y1, x0:x1, :3].min(-1) > 225) & (arr[y0:y1, x0:x1, 3] > 240)
    assert bright.sum() > 3000
    # el amarillo solo alrededor de "800 pesos" (no detras de "solo")
    acc = [w for w in B.words if w.accent]
    ax0 = min(w.ox + w.run.ink[0] for w in acc)
    ys, xs = np.nonzero(yel)
    assert len(xs) > 3000
    assert xs.min() >= ax0 - 0.2 * B.lines[1].size
    # encima del marcador el texto es oscuro (accent del item)
    band = arr[ys.min():ys.max(), xs.min():xs.max()]
    dark = (band[..., :3].max(-1) < 60) & (band[..., 3] > 240)
    assert dark.sum() > 1500
    # la banda cubre la tinta completa de las palabras marcadas (sin munones fuera)
    for w in acc:
        wl, wt, wr, wb = w.ox + w.run.ink[0], w.oy + w.run.ink[1], w.ox + w.run.ink[2], w.oy + w.run.ink[3]
        sub = arr[int(wt):int(wb), int(wl):int(wr)]
        light_ink = (sub[..., :3].min(-1) > 200) & (sub[..., 3] > 240) & ~_near(sub, YELLOW, 90, 200)
        assert light_ink.sum() < 30, w.run.text


def test_marker_schema_item_dark_text_all_lines_on_marker():
    item = {"id": "m2", "preset": "highlight_marker", "text": "Cuesta solo 800 pesos", "size": 0.065,
            "font": "Poppins-ExtraBold", "y": 0.3, "box": YELLOW, "color": "#111111", "anim_in": "mask",
            "anim_out": "none"}
    P, B = T._get_block(item, "highlight_marker", _ctx())
    arr, cv = _canvas()
    T.RENDERERS["highlight_marker"](cv, item, 2.0, 2.4, _ctx(2.0))
    yel = _near(arr, YELLOW, 60, 200)
    for ln in B.lines:   # cada linea con texto oscuro va sobre su marcador
        sub = yel[int(ln.ink[1]):int(ln.ink[3]), int(ln.ink[0]):int(ln.ink[2])]
        assert sub.mean() > 0.35
    # antes de que llegue el marcador, el texto se ve claro (no oscuro sobre el video)
    times = T._get_times(item, P, B, 2.4, "highlight_marker")
    lt = times[0] + 0.12
    arr, cv = _canvas()
    T.RENDERERS["highlight_marker"](cv, item, lt, 2.4, _ctx(lt))
    ln = B.lines[0]
    sub = arr[int(ln.ink[1]):int(ln.ink[3]), int(ln.ink[0]):int(ln.ink[2])]
    assert ((sub[..., :3].min(-1) > 220) & (sub[..., 3] > 240)).sum() > 300


def test_marker_colors_resolution():
    P = {"accent": YELLOW, "color": "#FFFFFF"}
    mcol, out_col, on_col = T._marker_colors({"box": YELLOW, "accent": "#0E0E12"}, P)
    assert out_col == "#FFFFFF" and on_col == "#0E0E12"
    mcol, out_col, on_col = T._marker_colors({"box": YELLOW, "color": "#111111"}, dict(P, color="#111111"))
    assert out_col == "#FFFFFF" and on_col == "#111111"
    # acento que no contrasta con el marcador -> contraste automatico
    mcol, out_col, on_col = T._marker_colors({}, P)
    assert mcol == YELLOW and on_col == "#141414"


# ---------------------------------------------------------------- kicker
def test_kicker_label_only_from_explicit_sources():
    long_item = {"id": "k1", "preset": "kicker", "text": "a dos cuadras de la Glorieta Norte", "size": 0.05,
                 "box": "#0E0E12"}
    P, B = T._get_block(long_item, "kicker", _ctx())
    assert not any(ln.kind == "pill" for ln in B.lines)
    short = {"id": "k2", "preset": "kicker", "text": "Limpieza dental", "size": 0.05}
    P, B = T._get_block(short, "kicker", _ctx())
    assert [ln.kind for ln in B.lines] == ["pill"]
    explicit = {"id": "k3", "preset": "kicker", "text": "Ubicación\nA dos cuadras de la Glorieta Norte"}
    P, B = T._get_block(explicit, "kicker", _ctx())
    assert B.lines[0].kind == "pill" and " ".join(w.run.text for w in B.lines[0].words) == "UBICACIÓN"
    assert all(ln.kind == "text" for ln in B.lines[1:])


def test_kicker_box_colors_only_the_pill():
    blue = "#2255FF"
    item = {"id": "k4", "preset": "kicker", "kicker": "Ubicación", "text": "Av. Central 123", "box": blue,
            "anim_out": "none"}
    P, B = T._get_block(item, "kicker", _ctx())
    arr, cv = _canvas()
    T.RENDERERS["kicker"](cv, item, 2.0, 2.4, _ctx(2.0))
    bl = _near(arr, blue, 50, 200)
    pill = B.lines[0]
    assert bl[int(pill.ink[1]):int(pill.ink[3]), :].sum() > 2000
    for ln in B.lines[1:]:
        assert bl[int(ln.ink[1]):int(ln.ink[3]) + 1, :].sum() < 20      # sin caja detras del titulo


def test_tracking_opens_word_space():
    item = {"id": "k5", "preset": "kicker", "kicker": "a dos cuadras de la glorieta", "text": "Av. Central"}
    P, B = T._get_block(item, "kicker", _ctx())
    ws = B.lines[0].words
    assert B.lines[0].kind == "pill" and len(ws) >= 3
    letter_gaps = []
    for w in ws:
        r = w.run
        for i in range(len(r.glyphs) - 1):
            letter_gaps.append((r.xpos[i + 1] + r.gink[i + 1][0]) - (r.xpos[i] + r.gink[i][2]))
    word_gaps = [(b.ox + b.run.ink[0]) - (a.ox + a.run.ink[2]) for a, b in zip(ws, ws[1:])]
    assert min(word_gaps) >= 2.5 * (sum(letter_gaps) / len(letter_gaps))


# ---------------------------------------------------------------- colisiones durante la animacion
@pytest.mark.parametrize("anim", ["pop", "word_stagger", "slide_left", "slide_right", "scale_down"])
def test_title_words_never_collide(monkeypatch, anim):
    cap = _Capture(monkeypatch)
    item = _item("title_pop", text="¿Te duele al comer? Escucha esto", anim_in=anim)
    worst = 9.0
    for i in range(0, 73):
        lt = i / 60
        cap.reset()
        arr, cv = _canvas()
        T.RENDERERS["title_pop"](cv, item, lt, 2.4, _ctx(lt))
        for units in cap.calls:
            worst = min(worst, _min_word_gap(units))
    assert worst >= 0.08, (anim, worst)


@pytest.mark.parametrize("preset", ["bold_caps", "clean_bold"])
def test_caption_words_never_collide(monkeypatch, preset):
    cap = _Capture(monkeypatch)
    ph = _phrases()
    worst = 9.0
    for i in range(0, 73):
        t = 1.0 + i / 60
        cap.reset()
        arr, cv = _canvas()
        T.draw_captions(cv, ph, _cfg(preset), t, _ctx(t))
        for units in cap.calls:
            worst = min(worst, _min_word_gap(units))
    assert worst >= 0.08, (preset, worst)


def test_soft_scale_limits_overshoot():
    assert T._soft_scale(1.2, 100.0, 0.5, (2.0, 2.0)) <= 1.0 + 2.0 / 50.0 + 1e-9
    assert T._soft_scale(0.8, 100.0, 0.5, (0.0, 0.0)) == 0.8          # encoger nunca se limita
    assert T._soft_scale(1.2, 100.0, 0.5, None) == 1.2


# ---------------------------------------------------------------- boxed: contraste sigue a la pastilla
def test_boxed_contrast_follows_pill(monkeypatch):
    rects = []
    orig = T._boxed_pill

    def spy(canvas, g, D, t, ga, gdy, ctx):
        rr = orig(canvas, g, D, t, ga, gdy, ctx)
        rects.append(rr)
        return rr

    monkeypatch.setattr(T, "_boxed_pill", spy)
    ph = _phrases()
    for t in (1.33, 1.36, 1.4, 1.45, 1.62, 1.66, 1.7):
        rects.clear()
        arr, cv = _canvas()
        T.draw_captions(cv, ph, _cfg("boxed"), t, _ctx(t))
        assert rects and rects[-1] is not None
        r = rects[-1].rect()
        inside = np.zeros(arr.shape[:2], bool)
        inside[int(r.top()) + 3:int(r.bottom()) - 3, int(r.left()) + 3:int(r.right()) - 3] = True
        near = np.zeros(arr.shape[:2], bool)
        near[max(0, int(r.top()) - 3):int(r.bottom()) + 3, max(0, int(r.left()) - 3):int(r.right()) + 3] = True
        white = (arr[..., :3].min(-1) > 235) & (arr[..., 3] > 245)
        dark = (arr[..., :3].max(-1) < 40) & (arr[..., 3] > 245)
        assert (white & inside).sum() < 15, t           # nada de texto blanco sobre la pastilla
        assert (dark & ~near).sum() < 15, t             # nada de texto oscuro fuera de la pastilla


# ---------------------------------------------------------------- agrupacion de subtitulos
_FUNC_BAD = {"y", "de", "en", "la", "el", "a", "por", "que", "como", "los", "las", "un", "una", "con", "mis",
             "tus", "te", "se"}


def _three_word_phrases():
    """Estructura de plan v1 (trozos de 3 palabras) con texto sintetico."""
    txt = ("Hoy te voy a contar como se prepara un buen cafe de olla en la casa de mis abuelos y por que "
           "sabe tan rico cuando llueve. Lo mejor es usar piloncillo de verdad y canela entera.")
    ws = txt.split()
    out, t = [], 0.5
    for i in range(0, len(ws), 3):
        ph = []
        for w in ws[i:i + 3]:
            ph.append({"text": w, "start": round(t, 3), "end": round(t + 0.22, 3), "kw": len(w) >= 6})
            t += 0.3
        out.append(ph)
        t += 0.05
    return out


@pytest.mark.parametrize("preset", ["clean_bold", "karaoke", "boxed", "minimal_small", "bold_caps"])
def test_caption_groups_ignore_phrase_chunks_and_function_endings(preset):
    groups = T.caption_groups(_three_word_phrases(), _cfg(preset), _ctx())
    texts = [g["text"] for g in groups]
    bad = [s for s in texts if not s.rstrip().endswith((".", "!", "?"))
           and T._norm(s.split()[-1]) in _FUNC_BAD]
    # con 3 palabras maximo, "y por que sabe" obliga a un corte malo; con 4+ no hay ninguno
    assert len(bad) <= (1 if preset == "bold_caps" else 0), (preset, texts)
    singles = [s for s in texts if len(s.split()) == 1]
    assert len(singles) <= 1, texts
    # los trozos de 3 del plan no dictan los cortes: hay grupos que cruzan esos limites
    sizes = [len(s.split()) for s in texts]
    assert max(sizes) <= T._cap_cfg(_cfg(preset), _ctx())["max_words"]


def test_caption_cuts_after_comma_and_pause():
    ph = [[{"text": "Primero,", "start": 0.0, "end": 0.4}, {"text": "lava", "start": 0.45, "end": 0.7},
           {"text": "bien", "start": 0.75, "end": 0.9}, {"text": "las", "start": 0.95, "end": 1.05},
           {"text": "fresas", "start": 1.1, "end": 1.5}]]
    groups = T.caption_groups(ph, _cfg(), _ctx())
    assert groups[0]["text"] == "Primero,"


def test_caption_keyword_accent_limited():
    groups = T.caption_groups(_three_word_phrases(), _cfg("clean_bold"), _ctx())
    acc = [len(g["accent"]) for g in groups]
    assert max(acc) <= 1
    assert all(not (a and b) for a, b in zip(acc, acc[1:]))       # nunca en grupos seguidos
    assert sum(acc) >= 2
    groups = T.caption_groups(_three_word_phrases(), _cfg("clean_bold", kw_accent=False), _ctx())
    assert all(not g["accent"] for g in groups)


def test_clean_bold_active_word_in_accent(monkeypatch):
    cap = _Capture(monkeypatch)
    ph = [[{"text": "Una", "start": 1.0, "end": 1.2}, {"text": "buena", "start": 1.3, "end": 1.6},
           {"text": "idea", "start": 1.7, "end": 2.0}]]
    cfg = _cfg("clean_bold", kw_accent=False)
    arr, cv = _canvas()
    T.draw_captions(cv, ph, cfg, 1.45, _ctx(1.45))
    units = cap.calls[-1]
    cols = {u["run"].text: u["color"] for u in units}
    assert C.rgb(cols["buena"]) == C.rgb("#FFD23F")                 # la que se pronuncia
    assert C.rgb(cols["Una"]) == C.rgb("#FFFFFF")                   # la anterior ya volvio al blanco


def test_clean_bold_recenters_visible_words(monkeypatch):
    cap = _Capture(monkeypatch)
    ph = [[{"text": "cambia", "start": 1.0, "end": 1.3}, {"text": "muchísimo", "start": 1.4, "end": 1.9},
           {"text": "todo", "start": 2.0, "end": 2.3}]]
    arr, cv = _canvas()
    T.draw_captions(cv, ph, _cfg("clean_bold"), 1.2, _ctx(1.2))
    r = _unit_rect(cap.calls[-1][0])
    assert abs((r[0] + r[2]) / 2 - W / 2) < 0.04 * W                # la primera palabra sola, centrada


def test_caps_cache_is_content_keyed():
    ph = _phrases()
    k1 = T._caps_key(ph, _cfg(), _ctx())
    k2 = T._caps_key([list(p) for p in ph], _cfg(), _ctx())
    assert k1 == k2
    ph2 = [list(p) for p in ph]
    ph2[0] = [dict(ph2[0][0], text="Otra")] + ph2[0][1:]
    assert T._caps_key(ph2, _cfg(), _ctx()) != k1


def test_perf_captions_rebuilt_list_each_frame():
    ph = _phrases()
    arr, cv = _canvas()
    cfg = _cfg()
    T.draw_captions(cv, [list(p) for p in ph], cfg, 1.2, _ctx(1.2))

    def one(i):
        t = 1.0 + i * 0.05
        T.draw_captions(cv, [list(p) for p in ph], cfg, t, _ctx(t))

    assert _bench(one) < 6.0 * _load_factor()


# ---------------------------------------------------------------- cortes de linea en espanol
def test_line_breaks_never_end_in_function_word():
    for preset, text in (("title_pop", "Reserva tu lugar hoy en el link de mi perfil"),
                         ("stack", "Reserva tu lugar hoy en el link de mi perfil"),
                         ("title_pop", "Una buena rutina mejora muchísimo tu energía de cada día"),
                         ("cascade", "para llevar la edición de video")):
        P, B = T._get_block(_item(preset, text=text), preset, _ctx())
        for ln in B.lines[:-1]:
            assert T._norm(ln.words[-1].run.text) not in T._FUNC_END, (preset, [
                " ".join(w.run.text for w in l.words) for l in B.lines])


def test_stack_explicit_size_never_isolates_function_word():
    item = _item("stack", text="para llevar la edición de video", size=0.12)
    P, B = T._get_block(item, "stack", _ctx())
    lines = [" ".join(w.run.text for w in ln.words) for ln in B.lines]
    for ln in B.lines:
        assert not all(T._norm(w.run.text) in T._FUNC_END for w in ln.words), lines
    big = max(B.lines, key=lambda l: l.size)
    assert not all(T._norm(w.run.text) in T._FUNC_END for w in big.words)


def test_cascade_leading_by_horizontal_overlap():
    # sin acentos: solo el descendente de la 'y' podria despegar la tercera linea
    item = _item("cascade", text="lo que comes\ny como duermes\ncada noche")
    P, B = T._get_block(item, "cascade", _ctx())
    gaps = []
    for a, b in zip(B.lines, B.lines[1:]):
        cap = max(w.run.cap for w in b.words)
        gaps.append((b.baseline - cap - a.baseline) / min(a.size, b.size))
    assert max(gaps) - min(gaps) < 0.12, gaps       # la 'y' no despega la tercera linea


# ---------------------------------------------------------------- caja por linea
def test_line_box_covers_tallest_letter():
    blue = "#2255FF"
    item = _item("title_pop", text="una Limpieza", box=blue, color="#FFFFFF", anim_out="none", size=0.09)
    P, B = T._get_block(item, "title_pop", _ctx())
    arr, cv = _canvas()
    T.RENDERERS["title_pop"](cv, item, 1.5, 2.0, _ctx(1.5))
    ys, xs = np.nonzero(_near(arr, blue, 50, 240))
    w = B.words[1]
    top_ink = w.oy - w.run.cap
    assert ys.min() < top_ink - 0.12 * w.run.size


# ---------------------------------------------------------------- script_accent
def test_script_accent_front_is_sharp_and_without_outline():
    item = _item("script_accent", text="limpieza", shadow=0, glow=0, rotation=0)
    P, B = T._get_block(item, "script_accent", _ctx())
    arr, cv = _canvas()
    T.RENDERERS["script_accent"](cv, item, 1.6, 2.4, _ctx(1.6))
    a_rest = arr[..., 3]
    rest_partial = ((a_rest > 30) & (a_rest < 225)).sum() / max(1, (a_rest > 30).sum())
    ln = B.lines[0]
    band = ln.size * (ln.ink[3] - ln.ink[1])
    for lt in (0.25, 0.4, 0.55):
        arr, cv = _canvas()
        T.RENDERERS["script_accent"](cv, item, lt, 2.4, _ctx(lt))
        a = arr[..., 3]
        vis = (a > 30).sum()
        assert vis > 500
        # pixeles a medio revelar de mas (frente difuso): con un feather de 0.35 em serian ~0.14
        excess = ((a > 30) & (a < 225)).sum() - rest_partial * vis
        assert excess < 0.05 * band, (lt, excess / band)
        # sin contornos por delante del frente: todo lo visible esta dentro de la tinta final
        assert ((a > 30) & (a_rest < 10)).sum() < 40, lt


def test_script_front_monotonic():
    item = _item("script_accent", text="limpieza dental")
    P, B = T._get_block(item, "script_accent", _ctx())
    ln = B.lines[0]
    xs = [T._script_front(ln, p, 0.28, _ctx()) for p in np.linspace(0, 1, 60)]
    assert all(b >= a - 1e-6 for a, b in zip(xs, xs[1:]))


# ---------------------------------------------------------------- detras del sujeto sin matte
def test_behind_without_subject_does_not_cover_face():
    face = (0.5, 0.3, 0.22, 0.14)
    item = _item("behind_giant", text="DIENTES", y=0.3, anim_out="none")
    ctx = C.demo_ctx(W=W, H=H, t=1.5, face=face)
    arr, cv = _canvas()
    T.RENDERERS["behind_giant"](cv, item, 1.5, 2.4, ctx)
    fr = ctx.face_rect_px()
    core = arr[int(fr.top() + 0.15 * fr.height()):int(fr.bottom() - 0.15 * fr.height()),
               int(fr.left() + 0.15 * fr.width()):int(fr.right() - 0.15 * fr.width()), 3]
    assert core.max() < 20
    assert (arr[..., 3] > 200).sum() > 20000                        # el resto del texto sigue ahi


def test_behind_with_subject_draws_normally():
    face = (0.5, 0.3, 0.22, 0.14)
    item = _item("behind_giant", text="DIENTES", y=0.3, anim_out="none")
    subj = C.image_from_rgba(np.zeros((H, W, 4), np.uint8))
    ctx = C.demo_ctx(W=W, H=H, t=1.5, face=face)
    ctx._subject_fn = lambda: subj
    arr, cv = _canvas()
    T.RENDERERS["behind_giant"](cv, item, 1.5, 2.4, ctx)
    fr = ctx.face_rect_px()
    core = arr[int(fr.top()):int(fr.bottom()), int(fr.left()):int(fr.right()), 3]
    assert (core > 200).sum() > 500        # en el paso 3 (con sujeto) se dibuja completo


def test_is_behind_used_for_any_preset_without_subject():
    item = _item("title_pop", text="HOLA", behind=True, y=0.3, anim_out="none", size=0.2)
    ctx = C.demo_ctx(W=W, H=H, t=1.5, face=(0.5, 0.3, 0.2, 0.12))
    arr, cv = _canvas()
    T.RENDERERS["title_pop"](cv, item, 1.5, 2.4, ctx)
    fr = ctx.face_rect_px()
    core = arr[int(fr.centerY()) - 20:int(fr.centerY()) + 20, int(fr.centerX()) - 40:int(fr.centerX()) + 40, 3]
    assert core.max() < 20


# ---------------------------------------------------------------- rotacion en measure/SAFE
@pytest.mark.parametrize("y", [0.97, 0.02])
def test_rotated_block_measure_and_pixels_inside_safe(y):
    item = _item("script_accent", text="limpieza", y=y, shadow=0, glow=0, anim_out="none")
    ctx = _ctx(1.8)
    r = T.measure(item, ctx)
    assert r.bottom() <= SAFE["bottom"] * H + 1 and r.top() >= SAFE["top"] * H - 1
    arr, cv = _canvas()
    T.RENDERERS["script_accent"](cv, item, 1.8, 2.4, ctx)
    bb = _alpha_bbox(arr)
    assert bb[3] <= SAFE["bottom"] * H + 2 and bb[1] >= SAFE["top"] * H - 2
    assert bb[3] <= r.bottom() + 2 and bb[1] >= r.top() - 2


def test_measure_includes_drift():
    item = _item("behind_giant", text="DIENTES", y=0.97)
    r = T.measure(item, _ctx())
    assert r.bottom() <= SAFE["bottom"] * H + 1
    P, B = T._get_block(item, "behind_giant", _ctx())
    assert r.height() >= B.rect.height() * (1 + 0.04) - 1           # incluye el empuje final


# ---------------------------------------------------------------- contador
def test_num_parses_separators():
    assert T._num("1.000", sep=".") == 1000
    assert T._num("1,000") == 1000
    assert T._num("$1,500.50") == 1500.5
    assert T._num("12,5", sep=".") == 12.5
    assert T._num(42) == 42.0


def test_counter_thousands_dot_separator():
    item = _item("counter", to="1.000", sep=".")
    assert T.counter_text(item, 2.3, 2.4) == "1.000"


def test_counter_suffix_does_not_slide_while_counting(monkeypatch):
    """anchor right (odometro, a pedido): los digitos crecen a la izquierda y el sufijo no se mueve."""
    cap = _Capture(monkeypatch)
    item = _item("counter", text="", anchor="right")
    xs = []
    for lt in np.linspace(0.13, 1.3, 25):
        cap.reset()
        arr, cv = _canvas()
        T.RENDERERS["counter"](cv, item, lt, 2.4, _ctx(lt))
        units = cap.calls[0]
        suf = [u for u in units if u["run"].text == "MXN"]
        xs.append(suf[0]["ox"])
    assert max(xs) - min(xs) < 1.0


# ---------------------------------------------------------------- serif y fondo claro
def test_serif_accent_italic_keeps_text_color():
    item = _item("serif_elegant", text="Un buen corte", accent_words=["corte"], color="#FFFFFF")
    P, B = T._get_block(item, "serif_elegant", _ctx())
    acc = [w for w in B.words if w.accent]
    assert acc and all(C.rgb(w.color) == C.rgb("#FFFFFF") for w in acc)
    item2 = dict(item, accent="#E8B04A")
    P, B = T._get_block(item2, "serif_elegant", _ctx())
    assert all(C.rgb(w.color) == C.rgb("#E8B04A") for w in B.words if w.accent)


def test_light_background_adds_soft_plate():
    item = _item("title_pop", text="Hola", anim_out="none", shadow=0.0)
    white = np.full((H, W, 4), 255, np.uint8)
    ctx = _ctx(1.5)
    ctx._cache["base_image"] = C.image_from_rgba(white)
    arr, cv = _canvas()
    T.RENDERERS["title_pop"](cv, item, 1.5, 2.4, ctx)
    P, B = T._get_block(item, "title_pop", ctx)
    r = B.rect
    ring = arr[int(r.bottom()) + 4:int(r.bottom()) + 14, int(r.left()):int(r.right()), 3]
    assert ring.mean() > 5          # placa difusa oscura alrededor (sin sombra configurada)
    arr, cv = _canvas()
    T.RENDERERS["title_pop"](cv, item, 1.5, 2.4, _ctx(1.5))
    ring = arr[int(r.bottom()) + 4:int(r.bottom()) + 14, int(r.left()):int(r.right()), 3]
    assert ring.mean() < 1          # sin fondo claro no hay placa


def test_bg_lum_coarse_and_cached():
    img = np.zeros((H, W, 4), np.uint8)
    img[..., :3] = 200
    img[..., 3] = 255
    ctx = _ctx()
    ctx._cache["base_image"] = C.image_from_rgba(img)
    v = T._bg_lum(ctx, skia.Rect.MakeLTRB(100, 100, 900, 500))
    assert abs(v - 200 / 255) < 0.02
    assert ctx._cache["text_bg"]["v"]
    assert T._bg_boost(ctx, skia.Rect.MakeLTRB(100, 100, 900, 500), "#FFFFFF", {"_bg_lum": 0.0}) == 1.0


def test_multipass_shadow_does_not_stain_neighbor_letters():
    """Typewriter: el render por letra (multipasada) coincide con el de la palabra completa."""
    item = _item("typewriter", anim_out="none")
    arr, cv = _canvas()
    T.RENDERERS["typewriter"](cv, item, 2.3, 2.6, _ctx(2.3))
    P, B = T._get_block(item, "typewriter", _ctx())
    ref, cv2 = _canvas()
    look = T._look(P, _ctx(), B.fs, P["color"])
    T._draw_units(cv2, [dict(run=w.run, ox=w.ox, oy=w.oy, look=look, color=P["color"], alpha=1.0) for w in B.words],
                  False)
    ink = ref[..., 3] > 250
    diff = np.abs(arr[..., :3].astype(int) - ref[..., :3].astype(int)).sum(-1)
    assert (diff[ink] > 60).sum() < 0.01 * ink.sum()


# ---------------------------------------------------------------- ola 2
def test_measure_lines_matches_block():
    item = _item("stack", text="HOY\nte revelo\nel secreto", y=0.4)
    rows = T.measure_lines(item, _ctx(), with_text=True)
    assert [t for _, t in rows] == ["HOY", "te revelo", "el secreto"]
    r = T.measure(item, _ctx())
    for (l, t, rr, b), _ in rows:
        assert r.left() - 1 <= l and rr <= r.right() + 1 and r.top() - 1 <= t and b <= r.bottom() + 1


def test_caption_group_hidden_at_start_stays_hidden():
    """Un subtitulo que un titulo tapo desde su inicio no reaparece cuando el titulo termina
    (no repite como subtitulo el precio que el titulo ya mostro)."""
    ph = _phrases()
    cfg = _cfg()
    groups = T.caption_groups(ph, cfg, _ctx())
    g = groups[0]
    cfg_h = dict(cfg, _hide=[[g["start"] - 0.2, g["end"] - 0.2]])     # el titulo acaba antes que el grupo
    arr, cv = _canvas()
    T.draw_captions(cv, ph, cfg_h, g["end"] - 0.05, _ctx(g["end"] - 0.05))
    assert _alpha_bbox(arr) is None
    arr2, cv2 = _canvas()
    T.draw_captions(cv2, ph, cfg, g["end"] - 0.05, _ctx(g["end"] - 0.05))
    assert _alpha_bbox(arr2) is not None


# ---------------------------------------------------------------- ola 3: contraste local y subtitulos por tramo
def _wcag(a, b):
    return T._wcag(a, b)


def test_line_box_ink_always_contrasts():
    """Caja por linea: una tinta pedida que no contrasta con la caja (blanco sobre amarillo) se
    sustituye por la de contraste (WCAG >= 4.5)."""
    item = _item("title_pop", text="REAL MEJORA TU ENERGIA DE HOY", box=YELLOW, color="#FFFFFF",
                 anim_in="pop", anim_out="none", accent_words=[])
    arr, cv = _canvas()
    T.RENDERERS["title_pop"](cv, item, 1.5, 2.0, _ctx(1.5))
    yel = _near(arr, YELLOW, 50, 200)
    ys, xs = np.nonzero(yel)
    assert len(xs) > 2000
    band = arr[ys.min():ys.max(), xs.min():xs.max()]
    white = (band[..., :3].min(-1) > 235) & (band[..., 3] > 240)
    dark = (band[..., :3].max(-1) < 60) & (band[..., 3] > 240)
    assert dark.sum() > 800 and white.sum() < dark.sum() * 0.1
    assert _wcag("#111111", YELLOW) >= 4.5


def test_accent_word_on_light_background_gets_a_plate():
    """El contraste se mide por PALABRA de acento contra su fondo local: acento amarillo sobre
    una pared blanca lleva placa oscura detras de su linea; sobre fondo oscuro, no."""
    item = _item("serif_elegant", text="y no te preocupes", accent_words=["preocupes"], color="#FFFFFF",
                 accent=YELLOW, shadow=0, glow=0, anim_out="none", y=0.4)
    for bg, want in (((250, 250, 250), True), ((20, 20, 24), False)):
        ctx = _ctx(1.5)
        img = np.zeros((H, W, 4), np.uint8)
        img[..., :3] = bg
        img[..., 3] = 255
        ctx._cache["base_image"] = C.image_from_rgba(img)
        P, B = T._get_block(item, "serif_elegant", ctx)
        ln = [ln_ for ln_ in B.lines if any(w.accent for w in ln_.words)][0]
        assert (T._accent_plate(ctx, ln) > 0) == want, bg


DEEP_GREEN = "#08794D"     # acento de marca oscuro (verde profundo)


def _title_on(bg, accent, text="la idea verde", word="verde"):
    item = _item("title_pop", text=text, accent_words=[word], color="#FFFFFF", accent=accent,
                 shadow=0.7, anim_in="pop", anim_out="none", y=0.45)
    arr, cv = _canvas()
    cv.clear(skia.Color(*bg, 255))
    T.RENDERERS["title_pop"](cv, item, 1.6, 2.0, _ctx(1.6))
    return arr


def _ring_contrast(arr, hexcol):
    """Contraste WCAG entre la tinta `hexcol` (mediana) y el anillo de 1-4 px a su alrededor."""
    import cv2
    m = _near(arr, hexcol, 60, 200).astype(np.uint8)
    assert m.sum() > 500
    k1 = cv2.dilate(m, np.ones((3, 3), np.uint8))
    k2 = cv2.dilate(m, np.ones((9, 9), np.uint8))
    ring = arr[(k2 > 0) & (k1 == 0)][..., :3]
    g = tuple(int(v) for v in np.median(arr[m > 0][..., :3], 0))
    r = tuple(int(v) for v in np.median(ring, 0))
    return T._wcag(g, r)


def test_dark_accent_word_in_light_title_gets_a_light_rim():
    """Un acento de marca OSCURO (verde profundo) en un titulo blanco con sombra negra: la sombra
    pegada al glifo lo apagaba (~2:1 contra su entorno inmediato, se perdia sobre una playera
    oscura). Lleva un contorno fino de la tinta clara del bloque: el verde se lee (>= 3:1 contra su
    anillo) y sigue siendo el verde de la marca; las palabras blancas no cambian ni un pixel."""
    dark_bg = (38, 52, 45)
    green = _title_on(dark_bg, DEEP_GREEN)
    assert _ring_contrast(green, DEEP_GREEN) >= 3.0
    assert T._wcag(DEEP_GREEN, dark_bg) < 3.0          # sin el contorno no se leeria
    # la tinta del acento sigue siendo el verde de la marca
    assert _near(green, DEEP_GREEN, 30, 240).sum() > 500
    # un acento claro (amarillo) no lleva contorno: su anillo es la sombra oscura de siempre
    yel = _title_on(dark_bg, YELLOW)
    assert T._rim_look(T._Look(), "#FFFFFF", 100).stroke > 0
    ring_y = _ring_contrast(yel, YELLOW)
    assert ring_y >= 3.0
    # las palabras blancas son identicas con un acento verde o amarillo (mismo layout, mismo look)
    x0 = int(np.nonzero(_near(green, DEEP_GREEN, 60, 200))[1].min()) - 12
    assert x0 > 100
    assert np.array_equal(green[:, :x0], yel[:, :x0])


def test_dark_accent_rim_not_on_boxes_or_dark_blocks():
    """Sin contorno cuando el bloque ya es oscuro (tinta oscura con sombra clara) ni con caja por
    linea (la tinta sobre la caja ya tiene su propio contraste WCAG)."""
    for kw in ({"color": "#111111", "accent": DEEP_GREEN}, {"box": "#FFFFFF", "color": "#111111", "accent": DEEP_GREEN}):
        item = _item("title_pop", text="la idea verde", accent_words=["verde"], shadow=0.7, anim_in="pop",
                     anim_out="none", y=0.45, **kw)
        seen = []
        orig = T._rim_look
        T._rim_look = lambda *a, **k: seen.append(1) or orig(*a, **k)
        try:
            arr, cv = _canvas()
            T.RENDERERS["title_pop"](cv, item, 1.6, 2.0, _ctx(1.6))
        finally:
            T._rim_look = orig
        assert not seen, kw


def test_captions_y_ranges_move_by_segment():
    """captions.y_ranges [{start, end, y}] (tiempo original): los subtitulos de ese tramo suben
    (p. ej. al pecho en una toma de cuerpo completo); fuera del tramo, la y global."""
    ph = _phrases()
    cfg = _cfg(y=0.73)
    t = ph[0][1]["start"] + 0.05
    base = _render_caps(ph, cfg, t, t_orig=t)
    moved = _render_caps(ph, dict(cfg, y_ranges=[{"start": 0.0, "end": t + 5.0, "y": 0.62}]), t, t_orig=t)
    out = _render_caps(ph, dict(cfg, y_ranges=[{"start": t + 5.0, "end": t + 9.0, "y": 0.62}]), t, t_orig=t)
    yb, ym, yo = (_alpha_bbox(a)[1] for a in (base, moved, out))
    assert abs((yb - ym) / H - 0.11) < 0.01 and yo == yb


def _render_caps(ph, cfg, t, t_orig):
    arr, cv = _canvas()
    ctx = _ctx(t)
    ctx.t_orig = t_orig
    T.draw_captions(cv, ph, cfg, t, ctx)
    return arr


# ---------------------------------------------------------------- ola 3: salida de los subtitulos
def test_caption_exit_is_short_and_fades_as_one_piece():
    """Un grupo seguido de una pausa se queda ~0.34 s tras su ultima palabra y se apaga en ~0.11 s
    COMO UNA PIEZA: sombra y relleno no se transparentan por separado (eso dejaba una copia oscura
    del subtitulo, un 'fantasma'). A media salida la tinta clara es la mezcla lineal esperada."""
    ph = [[{"text": "Arboleda.", "start": 1.0, "end": 1.5}], [{"text": "Mira", "start": 4.0, "end": 4.3}]]
    cfg = _cfg()
    g = T.caption_groups(ph, cfg, _ctx())[0]
    assert abs(g["end"] - (1.5 + 0.34)) < 1e-6
    bg = np.full((H, W, 4), (96, 96, 96, 255), np.uint8)

    def frame(t):
        arr, surf = C.new_frame(W, H)
        arr[:] = bg
        T.draw_captions(surf.getCanvas(), ph, cfg, t, _ctx(t))
        return arr[..., :3].astype(np.float32).mean(axis=2)

    full = frame(g["end"] - 0.2)                         # quieto, opaco
    mid_t = g["end"] - 0.055                             # a media salida (po = 0.5 -> alpha 0.75)
    mid = frame(mid_t)
    after = frame(g["end"] + 0.01)
    assert np.abs(after - 96).max() < 1.0                # ya no queda nada
    a = 0.75
    ink = full > 200                                     # relleno blanco del texto
    assert ink.sum() > 300
    exp = 96 + (full[ink] - 96) * a
    # capa unica: el relleno queda en la mezcla lineal (con partes separadas la sombra de debajo
    # lo oscurecia ~30 niveles)
    assert float(np.percentile(mid[ink], 50)) >= float(np.percentile(exp, 50)) - 12


# ---------------------------------------------------------------- ola 3 (revision del critico)
def test_captions_hide_rows_in_y_ranges():
    """`y_ranges` con hide:true (tiempo original): sin subtitulos en ese tramo (el CTA final de
    un primer plano ocupa su franja) y con fundido en el borde; sin mover el resto."""
    ph = _phrases()
    cfg = _cfg("clean_bold", y_ranges=[{"start": 1.5, "end": 2.0, "y": 0.73, "hide": True}])
    arr, cv = _canvas()
    T.draw_captions(cv, ph, cfg, 1.7, _ctx(1.7))
    assert (arr[..., 3] > 0).sum() == 0
    arr, cv = _canvas()
    T.draw_captions(cv, ph, cfg, 1.35, _ctx(1.35))
    assert (arr[..., 3] > 0).sum() > 800
    assert 0.0 < T._yr_hide_alpha(cfg, _ctx(1.45)) < 1.0
    assert T._cap_dy(cfg, {"y": 0.73}, _ctx(1.7)) == 0.0          # una fila hide no desplaza


def test_kicker_pill_never_starts_empty():
    """La pastilla del kicker arranca en ~40% de su ancho y la etiqueta entra casi con ella:
    nunca una pastilla vacia que crece desde ancho 0 (se veia como una falla sobre la cara)."""
    blue = "#2255FF"
    item = {"id": "k6", "preset": "kicker", "kicker": "Perfumería", "text": "nombres en inglés", "box": blue,
            "anim_in": "fade", "anim_out": "none"}
    P, B = T._get_block(item, "kicker", _ctx())
    pill = B.lines[0]
    full_w = pill.ink[2] - pill.ink[0]
    arr, cv = _canvas()
    T.RENDERERS["kicker"](cv, item, 0.04, 2.4, _ctx(0.04))
    bl = _near(arr, blue, 60, 120)
    cols = np.nonzero(bl[int(pill.ink[1]):int(pill.ink[3]) + 1, :].any(axis=0))[0]
    assert len(cols) and (cols.max() - cols.min()) >= 0.4 * full_w * 0.9
    arr, cv = _canvas()
    T.RENDERERS["kicker"](cv, item, 0.12, 2.4, _ctx(0.12))
    # la etiqueta (blanca sobre el azul) ya asoma a 0.12 s: pixeles claros dentro de la pastilla
    sub = arr[int(pill.ink[1]):int(pill.ink[3]) + 1, int(pill.ink[0]):int(pill.ink[2]), :3].astype(int)
    light = (sub.min(axis=2) > 90) & (arr[int(pill.ink[1]):int(pill.ink[3]) + 1,
                                          int(pill.ink[0]):int(pill.ink[2]), 3] > 10)
    assert light.sum() > 30


def test_word_sync_false_enters_whole_at_start():
    """word_sync: false (palabra gigante del director): entra entera con su animacion al inicio
    del item aunque la voz la diga despues; por defecto espera a que se diga."""
    item = _item("stack", text="DIENTES", anim_in="pop", anim_out="none", size=0.14)
    item["_words"] = [{"text": "dientes", "start": 1.2, "end": 1.6}]
    arr, cv = _canvas()
    T.RENDERERS["stack"](cv, dict(item), 0.5, 2.0, _ctx(0.5))
    synced = int((arr[..., 3] > 40).sum())
    arr, cv = _canvas()
    T.RENDERERS["stack"](cv, dict(item, word_sync=False), 0.5, 2.0, _ctx(0.5))
    free = int((arr[..., 3] > 40).sum())
    assert synced < 200 and free > 2000, (synced, free)
