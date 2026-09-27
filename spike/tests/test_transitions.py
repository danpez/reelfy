"""Tests de motion/transitions.py (contrato 3.1 y 4.2b de docs/MOTION-ENGINE.md).

Usan cuadros sinteticos (A rojizo, B azulado, con textura) a resolucion reducida para ser
rapidos; la prueba de artefactos de circle y la de rendimiento usan 1080x1920 y 540x960.
"""
from __future__ import annotations

import colorsys
import math
import os
import time

import numpy as np
import pytest
import skia

from motion import ctx as C
from motion import transitions as T

W, H = 270, 480
CONTRACT_KINDS = {"cut", "flash", "light_leak", "whip", "zoom_blur", "glitch",
                  "film_burn", "torn_paper", "circle", "slide"}
CENTERED = ["flash", "light_leak", "whip", "zoom_blur", "glitch"]
MIXERS = ["film_burn", "torn_paper", "circle", "slide"]


def _frame(base, w=W, h=H, seed=0):
    rng = np.random.default_rng(seed)
    arr = np.empty((h, w, 4), np.uint8)
    noise = rng.integers(-25, 25, (h, w, 1))
    yy, xx = np.mgrid[0:h, 0:w]
    checker = (((xx // 12) + (yy // 12)) % 2 * 30 - 15)[..., None]
    arr[..., :3] = np.clip(np.array(base)[None, None, :] + noise + checker, 0, 255)
    arr[..., 3] = 255
    return arr


A_ARR = _frame((200, 45, 40), seed=1)
B_ARR = _frame((40, 70, 205), seed=2)


def _imgs(w=W, h=H):
    if (w, h) == (W, H):
        return C.image_from_rgba(A_ARR), C.image_from_rgba(B_ARR)
    return (C.image_from_rgba(_frame((200, 45, 40), w, h, 1)),
            C.image_from_rgba(_frame((40, 70, 205), w, h, 2)))


def _solid(rgb, w=W, h=H):
    arr = np.empty((h, w, 4), np.uint8)
    arr[..., :3] = rgb
    arr[..., 3] = 255
    return C.image_from_rgba(arr)


def _ctx(w=W, h=H, **kw):
    kw.setdefault("seed", 7)
    return C.demo_ctx(w, h, fps=30, **kw)


def _item(kind, **kw):
    it = {"id": f"t_{kind}", "kind": kind, "at": 5.0}
    it.update({k: v for k, v in T.DEFAULTS[kind].items() if v is not None})
    it.update(kw)
    return it


def _np(img):
    return C.image_to_rgba(img).astype(np.int16)


def _render(kind, p, ctx=None, prev=None, img=None, **kw):
    a, b = _imgs()
    ctx = ctx or _ctx()
    it = _item(kind, **kw)
    if kind in MIXERS:
        prev = a if prev is None else prev
        img = b if img is None else img
    else:
        img = (a if p < 0.5 else b) if img is None else img
        prev = a if prev is None else prev
    return T.apply(img, prev, it, p, ctx)


def _b_frac(arr):
    """Fraccion de pixeles 'de B' (azul domina al rojo)."""
    return float(np.mean(arr[..., 2] > arr[..., 0]))


def _sat(arr):
    """Saturacion HSV media de los pixeles."""
    rgb = arr[..., :3].reshape(-1, 3).astype(np.float32) / 255.0
    mx, mn = rgb.max(axis=1), rgb.min(axis=1)
    return float(np.mean(np.where(mx > 1e-3, (mx - mn) / np.maximum(mx, 1e-3), 0.0)))


# ------------------------------------------------------------------ catalogo / contrato
def test_catalog_matches_contract():
    assert set(T.RENDERERS) == CONTRACT_KINDS
    assert set(T.KINDS) == CONTRACT_KINDS
    assert set(T.WINDOWS) == CONTRACT_KINDS
    assert set(T.DEFAULTS) == CONTRACT_KINDS
    assert set(T.PARAMS) == CONTRACT_KINDS
    assert set(T.FIELDS) == CONTRACT_KINDS
    assert set(T.SFX) == CONTRACT_KINDS and set(T.HIT) == CONTRACT_KINDS
    assert set(T.MIXERS) == set(MIXERS) and set(T.CENTERED) == set(CENTERED)
    for k in MIXERS:
        assert T.WINDOWS[k] == 0.0, k
    for k in CENTERED:
        assert T.WINDOWS[k] == 0.5, k


def test_params_use_schema_format():
    """PARAMS[kind] = lista en formato de schema.P: k, type, label, default (+min/max/opts)."""
    types = {"num", "seg", "select", "bool", "color"}
    for kind, prm in T.PARAMS.items():
        keys = [p["k"] for p in prm]
        assert len(keys) == len(set(keys)), kind
        for p in prm:
            assert {"k", "type", "label", "default"} <= set(p), (kind, p)
            assert p["type"] in types, (kind, p)
            if p["type"] == "num":
                assert p["min"] <= p["max"]
                if p["default"] is not None:
                    assert p["min"] <= p["default"] <= p["max"], (kind, p)
            if p["type"] in ("seg", "select"):
                assert p["default"] in p["opts"], (kind, p)
            if p["k"] == "dir":
                assert set(p["opts"]) == set(T.DIRS)
            if p["k"] == "intensity":
                assert (p["min"], p["max"]) == (0.0, 1.0)  # contrato: intensity 0..1
            assert p["k"] in T.FIELDS[kind]
        if kind != "cut":
            assert "dur" in keys and "intensity" in keys
            assert keys[0] == "dur"
            dflt = next(p for p in prm if p["k"] == "dur")["default"]
            assert dflt == pytest.approx(T.DEFAULTS[kind]["dur"])
    # align solo tiene sentido en las centradas (las mezclas siempre empiezan en la union)
    for k in MIXERS:
        assert "align" not in [p["k"] for p in T.PARAMS[k]]
    for k in CENTERED:
        assert "align" in [p["k"] for p in T.PARAMS[k]]
    assert {"x", "y", "avoid"} <= T.FIELDS["film_burn"] and {"mode", "x", "y"} <= T.FIELDS["torn_paper"]
    assert {"gate", "peak"} <= T.FIELDS["light_leak"] and {"ring", "x", "y"} <= T.FIELDS["circle"]
    for k in CONTRACT_KINDS:
        assert {"id", "kind", "at", "enabled", "dur", "intensity"} <= T.FIELDS[k]


def test_defaults_of_mixers_are_short():
    # A esta congelado toda la ventana: que no dure demasiado
    assert T.DEFAULTS["film_burn"]["dur"] <= 0.65
    assert T.DEFAULTS["torn_paper"]["dur"] <= 0.65


def test_window_progress_hit():
    it = {"kind": "light_leak", "at": 10.0, "dur": 0.4}
    assert T.window(it) == pytest.approx((9.8, 10.2))
    assert T.progress(it, 10.0) == pytest.approx(0.5)
    assert T.progress(it, 9.7) is None and T.progress(it, 10.3) is None
    assert T.hit_time(it) == pytest.approx(10.0)
    it = {"kind": "slide", "at": 10.0, "dur": 0.4}
    assert T.window(it) == pytest.approx((10.0, 10.4))
    assert T.progress(it, 10.1) == pytest.approx(0.25)
    assert T.hit_time(it) == pytest.approx(10.2)
    # dur por defecto del kind y align explicito
    assert T.window({"kind": "flash", "at": 1.0})[1] == pytest.approx(1.0 + T.DEFAULTS["flash"]["dur"] / 2)
    assert T.window({"kind": "flash", "at": 1.0, "dur": 0.2, "align": "start"}) == pytest.approx((1.0, 1.2))
    assert T.progress({"kind": "flash", "at": 1.0}, "x") is None


@pytest.mark.parametrize("align,frac", [(None, 0.5), ("auto", 0.5), ("center", 0.5), ("start", 0.0),
                                        ("end", 1.0), ("CENTER", 0.5), ("raro", 0.5), (3, 0.5)])
def test_align_centered(align, frac):
    it = {"kind": "whip", "at": 10.0, "dur": 0.4, "align": align}
    assert T.effective_frac(it) == pytest.approx(frac)
    t0, t1 = T.window(it)
    assert (t0, t1) == pytest.approx((10.0 - 0.4 * frac, 10.0 + 0.4 * (1 - frac)))
    # el pico del efecto (p = 0.5) cae a mitad de la ventana: ahi va el SFX
    assert T.hit_time(it) == pytest.approx(0.5 * (t0 + t1))
    assert T.progress(it, T.hit_time(it)) == pytest.approx(0.5)


@pytest.mark.parametrize("kind", MIXERS)
@pytest.mark.parametrize("align", [None, "auto", "center", "start", "end"])
def test_align_is_ignored_by_mixers(kind, align):
    """Las mezclas necesitan a A congelado, que solo existe despues de la union."""
    it = {"kind": kind, "at": 10.0, "dur": 0.4, "align": align}
    assert T.effective_frac(it) == 0.0
    assert T.window(it) == pytest.approx((10.0, 10.4))
    assert T.hit_time(it) == pytest.approx(10.0 + 0.4 * T.HIT[kind])


def test_flash_hit_time_matches_visual_peak():
    """Con align=start el pico real (meseta blanca) cae a mitad de la ventana."""
    it = {"id": "f", "kind": "flash", "at": 10.0, "dur": 0.3, "align": "start"}
    a, _ = _imgs()
    ctx = _ctx()
    ht = T.hit_time(it)
    assert ht == pytest.approx(10.15)
    p = T.progress(it, ht)
    assert _np(T.apply(a, a, it, p, ctx))[..., :3].mean() > 250


# ------------------------------------------------------------------ forma y extremos
@pytest.mark.parametrize("kind", sorted(CONTRACT_KINDS))
@pytest.mark.parametrize("quality", ["final", "draft"])
def test_output_type_and_size(kind, quality):
    ctx = _ctx(quality=quality)
    for p in (0.0, 0.13, 0.37, 0.5, 0.62, 0.88, 1.0):
        out = _render(kind, p, ctx)
        assert isinstance(out, skia.Image)
        assert (out.width(), out.height()) == (W, H)
        arr = C.image_to_rgba(out)
        assert arr[..., 3].min() == 255, (kind, p)  # cuadro opaco


@pytest.mark.parametrize("kind", CENTERED)
def test_centered_endpoints_are_identity(kind):
    a, b = _imgs()
    ctx = _ctx()
    it = _item(kind)
    assert np.abs(_np(T.apply(a, a, it, 0.0, ctx)) - _np(a)).max() <= 2
    assert np.abs(_np(T.apply(b, a, it, 1.0, ctx)) - _np(b)).max() <= 2


@pytest.mark.parametrize("kind", MIXERS)
def test_mixer_endpoints(kind):
    a, b = _imgs()
    ctx = _ctx()
    it = _item(kind)
    assert np.abs(_np(T.apply(b, a, it, 0.0, ctx)) - _np(a)).max() <= 2
    assert np.abs(_np(T.apply(b, a, it, 1.0, ctx)) - _np(b)).max() <= 2
    # casi en los extremos tampoco hay saltos grandes
    assert np.abs(_np(T.apply(b, a, it, 0.01, ctx)) - _np(a)).mean() < 12
    assert np.abs(_np(T.apply(b, a, it, 0.995, ctx)) - _np(b)).mean() < 12


@pytest.mark.parametrize("kind", ["film_burn", "torn_paper", "slide"])
def test_mixers_move_from_first_render_frame(kind):
    """render usa p = 0.5/L en el primer cuadro: A no debe verse congelado (cuadro repetido)."""
    a, b = _imgs()
    ctx = _ctx()
    it = _item(kind)
    L = int(round(T.DEFAULTS[kind]["dur"] * 30))
    first = _np(T.apply(b, a, it, 0.5 / L, ctx))
    second = _np(T.apply(b, a, it, 1.5 / L, ctx))
    assert np.abs(first - _np(a)).mean() > 0.3, kind
    assert np.abs(second - first).mean() > 0.5, kind


@pytest.mark.parametrize("kind", [k for k in CONTRACT_KINDS if k != "cut"])
def test_effect_is_visible_mid(kind):
    a, b = _imgs()
    p = 0.45
    out = _np(_render(kind, p))
    ref = _np(a if (kind in CENTERED and p < 0.5) else (a if kind in MIXERS else b))
    assert np.abs(out - ref).mean() > 8, kind


def test_cut_is_passthrough():
    a, b = _imgs()
    assert T.apply(b, a, _item("cut"), 0.3, _ctx()) is b


# ------------------------------------------------------------------ mezclas
@pytest.mark.parametrize("kind", MIXERS)
def test_mixers_progress_towards_b(kind):
    fr = [_b_frac(_np(_render(kind, p))) for p in np.linspace(0, 1, 11)]
    assert fr[0] < 0.05 and fr[-1] > 0.95, (kind, fr)
    for x, y in zip(fr, fr[1:]):
        assert y >= x - 0.08, (kind, fr)  # esencialmente monotona


@pytest.mark.parametrize("d,side", [("left", "right"), ("right", "left"), ("up", "bottom"), ("down", "top")])
def test_slide_direction(d, side):
    arr = _np(_render("slide", 0.36, dir=d))
    strip = {"right": arr[:, -6:], "left": arr[:, :6], "bottom": arr[-6:, :], "top": arr[:6, :]}[side]
    opposite = {"right": arr[:, :W // 3], "left": arr[:, -W // 3:], "bottom": arr[:H // 3], "top": arr[-H // 3:]}[side]
    assert _b_frac(strip) > 0.6, (d, _b_frac(strip))
    assert _b_frac(opposite) < 0.2


def test_circle_center_and_custom_point():
    arr = _np(_render("circle", 0.4))
    assert _b_frac(arr[H // 2 - 5:H // 2 + 5, W // 2 - 5:W // 2 + 5]) > 0.9
    assert _b_frac(arr[:10, :10]) < 0.1
    arr = _np(_render("circle", 0.3, x=0.2, y=0.8))
    assert _b_frac(arr[int(0.8 * H) - 4:int(0.8 * H) + 4, int(0.2 * W) - 4:int(0.2 * W) + 4]) > 0.9
    assert _b_frac(arr[int(0.2 * H) - 4:int(0.2 * H) + 4, int(0.8 * W) - 4:int(0.8 * W) + 4]) < 0.1


def test_circle_uses_face_when_no_xy():
    ctx = _ctx(face=(0.3, 0.3, 0.2, 0.1))
    arr = _np(_render("circle", 0.28, ctx))
    assert _b_frac(arr[int(0.3 * H) - 4:int(0.3 * H) + 4, int(0.3 * W) - 4:int(0.3 * W) + 4]) > 0.9


@pytest.mark.parametrize("center", [None, (0.37, 0.41)])
def test_circle_has_no_pixels_of_b_outside_the_disc(center):
    """Regresion: la capa del iris con limites fraccionarios dejaba una linea de 1 px de B en
    el rectangulo envolvente. A negro y B blanco: todo pixel no negro debe estar dentro del
    disco (radio medido sobre la columna del centro) + 3 px."""
    w, h = 1080, 1920
    A, B = _solid((0, 0, 0), w, h), _solid((255, 255, 255), w, h)
    ctx = _ctx(w, h)
    kw = {} if center is None else {"x": center[0], "y": center[1]}
    it = _item("circle", **kw)
    cx, cy = (0.5 * w, 0.5 * h) if center is None else (center[0] * w, center[1] * h)
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.hypot(xx + 0.5 - cx, yy + 0.5 - cy)
    col = int(cx)
    for p in np.linspace(0.03, 0.6, 18):
        arr = C.image_to_rgba(T.apply(B, A, it, float(p), ctx))[..., 0]
        lit = arr > 2
        if not lit.any():
            continue
        ys = np.nonzero(lit[:, col])[0]
        r_col = max(abs(ys.max() + 0.5 - cy), abs(ys.min() + 0.5 - cy)) if ys.size else 0.0
        outside = lit & (dist > r_col + 3.0)
        assert int(outside.sum()) == 0, (p, int(outside.sum()), r_col)


def test_circle_ring_only_when_requested():
    """El director manda color '#FFFFFF' a todas: sin ring=true no hay aro."""
    base = _np(_render("circle", 0.35))
    white = _np(_render("circle", 0.35, color="#FFFFFF"))
    assert np.array_equal(base, white)
    ring = _np(_render("circle", 0.35, ring=True, color="#FFFFFF"))
    assert np.abs(ring - base).max() > 60
    assert np.array_equal(_np(_render("circle", 0.35, ring="false", color="#FFFFFF")), base)


def test_torn_split_opens_beside_the_face():
    """La linea de rasgado pasa a un costado del rostro (punto protegido), no por encima."""
    arr = _np(_render("torn_paper", 0.5, mode="split", dir="left"))
    cols = [(_b_frac(arr[:, x:x + 6])) for x in range(int(W * 0.1), int(W * 0.9), 3)]
    assert max(cols) > 0.9  # hay un hueco (B)
    assert _b_frac(arr[:, :4]) < 0.2 and _b_frac(arr[:, -4:]) < 0.2  # las piezas siguen en las orillas
    # durante el rasgado aun no se ve B, pero ya hay banda clara de papel, lejos del rostro
    for px_ in (None, 0.3, 0.7):
        kw = {} if px_ is None else {"x": px_, "y": 0.4}
        fx, fy = (T.PROTECT_DEFAULT if px_ is None else (px_, 0.4))
        early = _np(_render("torn_paper", 0.14, mode="split", **kw))
        assert _b_frac(early) < 0.02
        light = early[..., :3].min(axis=2) > 200
        assert light.mean() > 0.002
        row = light[int(fy * H) - 6:int(fy * H) + 6].any(axis=0)
        xs = np.nonzero(row)[0]
        assert xs.size, px_
        # la banda pasa a > 0.17 del ancho desde el centro del rostro (medio rostro ~0.15)
        assert np.min(np.abs(xs / W - fx)) > 0.17, (px_, xs.min() / W, xs.max() / W)


def test_torn_split_horizontal_line_goes_below_the_face():
    early = _np(_render("torn_paper", 0.14, mode="split", dir="up"))
    light = early[..., :3].min(axis=2) > 200
    ys = np.nonzero(light[:, W // 2 - 20:W // 2 + 20].any(axis=1))[0]
    assert ys.size and ys.min() / H > T.PROTECT_DEFAULT[1] + 0.08
    arr = _np(_render("torn_paper", 0.62, mode="split", dir="up"))
    band = arr[int(0.5 * H):int(0.62 * H)]
    assert _b_frac(band) > 0.85


def test_torn_sweep_direction_and_early_motion():
    arr = _np(_render("torn_paper", 0.5, mode="sweep", dir="left"))
    assert _b_frac(arr[:, -8:]) > 0.9 and _b_frac(arr[:, :8]) < 0.1
    arr = _np(_render("torn_paper", 0.5, mode="sweep", dir="right"))
    assert _b_frac(arr[:, :8]) > 0.9 and _b_frac(arr[:, -8:]) < 0.1
    # arranca con velocidad: a p=0.1 A ya se desplazo de forma visible
    a, _ = _imgs()
    early = _np(_render("torn_paper", 0.1, mode="sweep", dir="left"))
    assert np.abs(early - _np(a)).mean() > 5


@pytest.mark.parametrize("avoid", [None, [0.3, 0.6]])
def test_film_burn_starts_away_from_avoid_point(avoid):
    fx_n, fy_n = avoid or T.PROTECT_DEFAULT
    for seed in range(6):
        ctx = _ctx(seed=seed)
        arr = _np(_render("film_burn", 0.3, ctx, avoid=avoid))
        total = _b_frac(arr)
        fy, fx = int(fy_n * H), int(fx_n * W)
        at_face = _b_frac(arr[fy - 12:fy + 12, fx - 12:fx + 12])
        assert total > 0.05
        assert at_face <= total + 0.05, (seed, at_face, total)


def test_film_burn_xy_is_the_same_as_avoid():
    o1 = _np(_render("film_burn", 0.35, avoid=[0.3, 0.6]))
    o2 = _np(_render("film_burn", 0.35, x=0.3, y=0.6))
    assert np.array_equal(o1, o2)


def test_film_burn_ignores_per_frame_face():
    """El campo de quemado no depende del rostro por cuadro (evita parpadeo entre workers)."""
    T._CACHE.clear()
    o1 = _np(_render("film_burn", 0.4, _ctx(face=(0.2, 0.2, 0.1, 0.1))))
    T._CACHE.clear()
    o2 = _np(_render("film_burn", 0.4, _ctx(face=(0.8, 0.7, 0.1, 0.1))))
    assert np.array_equal(o1, o2)


def test_film_burn_has_hot_rim_and_char():
    """Frente con borde incandescente (casi blanco) y anillo de carbon (A oscurecido)."""
    a, b = _imgs()
    out = _np(_render("film_burn", 0.3))
    ref = _np(a)
    hot = (out[..., :3].min(axis=2) > 225).mean()
    lum_o = out[..., :3].mean(axis=2)
    lum_a = ref[..., :3].mean(axis=2)
    charred = ((lum_a - lum_o) > 35).mean()
    assert hot > 0.01, hot
    assert charred > 0.005, charred


# ------------------------------------------------------------------ centradas
def test_flash_peak_color():
    a, b = _imgs()
    out = _np(T.apply(b, a, _item("flash"), 0.5, _ctx()))
    assert out[..., :3].mean() > 250
    out = _np(T.apply(b, a, _item("flash", color="warm"), 0.5, _ctx()))
    r, g, bl = (out[..., i].mean() for i in range(3))
    assert r > 250 and g > 215 and bl < 200


def test_flash_curve_rises_fast_and_decays():
    a, _ = _imgs()
    ctx = _ctx()
    lum = [_np(T.apply(a, a, _item("flash"), p, ctx))[..., :3].mean() for p in (0.1, 0.3, 0.45, 0.5, 0.7, 0.9)]
    assert lum[0] < lum[1] < lum[2] <= lum[3]
    assert lum[3] > lum[4] > lum[5]


def test_flash_is_exposure_not_milky_fade():
    """Antes del pico la imagen se sobreexpone (conserva saturacion), no se lava a blanco."""
    a, _ = _imgs()
    ctx = _ctx()
    out = _np(T.apply(a, a, _item("flash"), 0.3, ctx))
    base = _np(a)
    assert out[..., :3].mean() > base[..., :3].mean() + 30
    assert _sat(out) > 0.6 * _sat(base)
    # solo la meseta (que cubre el corte y el cuadro anterior) llega a blanco
    assert _np(T.apply(a, a, _item("flash"), 0.4, ctx))[..., :3].mean() > 250
    assert _np(T.apply(a, a, _item("flash"), 0.62, ctx))[..., :3].mean() < 245
    # la caida dura mas que la subida
    up = sum(1 for p in np.linspace(0, 0.5, 51) if 0.05 < T._flash_level(p) < 0.95)
    down = sum(1 for p in np.linspace(0.5, 1, 51) if 0.05 < T._flash_level(p) < 0.95)
    assert down > up


def test_light_leak_warm_and_bright():
    a, b = _imgs()
    ctx = _ctx()
    base = _np(b)
    out = _np(T.apply(b, a, _item("light_leak"), 0.52, ctx))
    assert out[..., :3].mean() > base[..., :3].mean() + 50
    assert (out[..., 0] - out[..., 2]).mean() > (base[..., 0] - base[..., 2]).mean() + 40  # calido
    base_a = _np(a)
    cool = _np(T.apply(a, a, _item("light_leak", color="cool"), 0.4, ctx))
    assert (cool[..., 2] - cool[..., 0]).mean() > (base_a[..., 2] - base_a[..., 0]).mean() + 40


def test_light_leak_peak_keeps_a_warm_wash_not_white():
    """En el pico el lavado es crema-amarillo (como el objetivo), no blanco plano."""
    g = C.image_from_rgba(_frame((110, 110, 110), seed=3))
    out = _np(T.apply(g, g, _item("light_leak"), 0.5, _ctx()))
    med = np.median(out[..., :3].reshape(-1, 3), axis=0)
    assert med[0] > 240 and med[1] > 225 and med[2] < 225, med


def test_light_leak_partial_is_softer_than_whiteout():
    _, b = _imgs()
    ctx = _ctx()
    full = _np(T.apply(b, b, _item("light_leak"), 0.5, ctx))[..., :3].mean()
    part = _np(T.apply(b, b, _item("light_leak", peak="partial"), 0.5, ctx))[..., :3].mean()
    base = _np(b)[..., :3].mean()
    assert base + 20 < part < full - 15


@pytest.mark.parametrize("col", ["#FF7A3D", "#E8C27A", "#FF8A12", "orange"])
def test_light_leak_director_hex_keeps_preset_look(col):
    """Los hex que manda el director caen en el preset de tono cercano (no un lavado pastel)."""
    _, b = _imgs()
    ctx = _ctx()
    ref_name = "gold" if col == "#E8C27A" else "warm"
    ref = _np(T.apply(b, b, _item("light_leak", color=ref_name), 0.35, ctx))
    out = _np(T.apply(b, b, _item("light_leak", color=col), 0.35, ctx))
    assert abs(_sat(out) - _sat(ref)) <= 0.15 * _sat(ref)


@pytest.mark.parametrize("col", ["#FFFFFF", "#EEEEEE", "#808080", "white", "no-es-color", "#12", ["x"]])
def test_light_leak_neutral_color_uses_default_ramp(col):
    out = _np(_render("light_leak", 0.4, color=col))
    ref = _np(_render("light_leak", 0.4))
    assert np.array_equal(out, ref)


def test_light_leak_custom_hue_rotates_ramp():
    """Un tono lejos de los presets (verde) rota la rampa: se ve verde y saturado, no pastel."""
    _, b = _imgs()
    ctx = _ctx()
    out = _np(T.apply(b, b, _item("light_leak", color="#2FBF4A"), 0.35, ctx))
    ref = _np(T.apply(b, b, _item("light_leak"), 0.35, ctx))
    assert (out[..., 1] - out[..., 0]).mean() > (ref[..., 1] - ref[..., 0]).mean() + 30
    lut = T._lut_for("#2FBF4A")
    top = lut[-1, :3].astype(int)
    assert top.min() > 180 and top.max() - top.min() > 12  # termina en crema tenida, no blanco


def test_light_leak_gate_bar_and_strict_bool():
    _, b = _imgs()
    ctx = _ctx()
    it_off = _item("light_leak")
    no_gate = _np(T.apply(b, b, it_off, 0.5, ctx))
    gated = _np(T.apply(b, b, _item("light_leak", gate=True), 0.5, ctx))
    assert np.abs(gated - no_gate).max() > 60
    dark_rows = (gated[..., :3].mean(axis=(1, 2)) < no_gate[..., :3].mean(axis=(1, 2)) - 60).sum()
    assert 1 <= dark_rows <= 0.03 * H  # barra fina (~1% de H)
    for v in ("false", "0", "no", 0, False, None):
        assert np.array_equal(_np(T.apply(b, b, _item("light_leak", gate=v), 0.5, ctx)), no_gate), v
    assert np.array_equal(_np(T.apply(b, b, _item("light_leak", gate="true"), 0.5, ctx)), gated)


def _stripes():
    """R: franjas verticales (varia en x); G: franjas horizontales (varia en y)."""
    yy, xx = np.mgrid[0:H, 0:W]
    arr = np.full((H, W, 4), 255, np.uint8)
    arr[..., 0] = np.where((xx // 9) % 2 == 0, 230, 25)
    arr[..., 1] = np.where((yy // 9) % 2 == 0, 230, 25)
    arr[..., 2] = 60
    return C.image_from_rgba(arr)


@pytest.mark.parametrize("d", ["left", "right", "up", "down"])
def test_whip_blurs_along_direction(d):
    src = _stripes()
    out = _np(T.apply(src, src, _item("whip", dir=d), 0.35, _ctx()))
    ref = _np(src)

    def g(a, ch, axis):
        return np.abs(np.diff(a[..., ch].astype(np.float32), axis=axis)).mean()
    if d in ("left", "right"):  # movimiento horizontal: mueren las franjas verticales (R en x)
        assert g(out, 0, 1) < 0.25 * g(ref, 0, 1)
        assert g(out, 1, 0) > 0.7 * g(ref, 1, 0)
    else:
        assert g(out, 1, 0) < 0.25 * g(ref, 1, 0)
        assert g(out, 0, 1) > 0.7 * g(ref, 0, 1)


def test_zoom_blur_keeps_center_sharper_than_edges():
    arr = _np(_render("zoom_blur", 0.4, x=0.5, y=0.5))
    ref = _np(_imgs()[0])

    def energy(a, ys, xs):
        g = a[ys, xs, :3].astype(np.float32).mean(axis=2)
        return np.abs(np.diff(g, axis=0)).mean() + np.abs(np.diff(g, axis=1)).mean()
    c = (slice(H // 2 - 30, H // 2 + 30), slice(W // 2 - 30, W // 2 + 30))
    e = (slice(0, 60), slice(0, 60))
    assert energy(arr, *c) / energy(ref, *c) > 1.5 * energy(arr, *e) / energy(ref, *e)


def test_glitch_is_held_on_twos_and_seeded():
    a, _ = _imgs()
    it = _item("glitch", dur=0.36)
    ctx = _ctx()
    # misma ventana de 1/12 s -> mismo cuadro (animacion a doces)
    p1, p2 = 0.35, 0.42
    step = lambda p: math.floor(p * 0.36 * 12 + 1e-6)  # noqa: E731
    assert step(p1) == step(p2)
    o1, o2 = _np(T.apply(a, a, it, p1, ctx)), _np(T.apply(a, a, it, p2, ctx))
    assert np.array_equal(o1, o2)
    o3 = _np(T.apply(a, a, it, 0.46, _ctx(seed=99)))
    o4 = _np(T.apply(a, a, it, 0.46, ctx))
    assert not np.array_equal(o3, o4)


@pytest.mark.parametrize("col", ["#FFFFFF", "#DDDDDD", "white", "#000000"])
def test_glitch_ignores_neutral_accent(col):
    for p in (0.4, 0.46, 0.55):
        assert np.array_equal(_np(_render("glitch", p, color=col)), _np(_render("glitch", p))), (col, p)


@pytest.mark.parametrize("col", ["#FFFFFF", "#F0F0F0", "white", "warm", None])
def test_film_burn_neutral_color_uses_fire(col):
    assert np.array_equal(_np(_render("film_burn", 0.35, color=col)), _np(_render("film_burn", 0.35)))


# ------------------------------------------------------------------ centros y parametros basura
@pytest.mark.parametrize("face", [(float("nan"), 0.4, 0.2, 0.1), np.array([0.3, 0.4, 0.2, 0.1]),
                                  np.array([np.nan] * 4), ("a", "b"), [0.5], 7])
def test_zoom_blur_and_circle_with_bad_face(face):
    a, b = _imgs()
    ctx = _ctx(face=face)
    z = _np(T.apply(a, a, _item("zoom_blur"), 0.3, ctx))
    assert z[..., :3].mean() > 0.6 * _np(a)[..., :3].mean()  # no queda negro
    c = _np(T.apply(b, a, _item("circle"), 0.4, ctx))
    assert _b_frac(c) > 0.05


@pytest.mark.parametrize("xy", [(float("nan"), 0.5), ("a", 0.5), (None, float("inf")), ([1], {}), ("0.3", "0.6")])
def test_bad_xy_falls_back(xy):
    a, b = _imgs()
    ctx = _ctx()
    z = _np(T.apply(a, a, _item("zoom_blur", x=xy[0], y=xy[1]), 0.3, ctx))
    assert z[..., :3].mean() > 0.6 * _np(a)[..., :3].mean()
    c = _np(T.apply(b, a, _item("circle", x=xy[0], y=xy[1]), 0.4, ctx))
    assert _b_frac(c) > 0.05
    assert _b_frac(_np(T.apply(b, a, _item("circle", x=xy[0], y=xy[1]), 0.97, ctx))) > 0.95


def test_face_is_held_when_tracker_drops_it():
    """Si el tracker pierde la cara a media ventana, el centro no salta al default."""
    a, _ = _imgs()
    it = _item("zoom_blur", id="hold_test")
    T._CACHE.clear()
    c1 = _ctx(face=(0.25, 0.3, 0.2, 0.1))
    c1.n = 100
    T.apply(a, a, it, 0.3, c1)
    c2 = _ctx(face=None)
    c2.n = 101
    held = _np(T.apply(a, a, it, 0.35, c2))
    c3 = _ctx(face=(0.25, 0.3, 0.2, 0.1))
    c3.n = 101
    ref = _np(T.apply(a, a, it, 0.35, c3))
    assert np.array_equal(held, ref)
    # la retencion caduca (no se arrastra de una ventana vieja)
    c4 = _ctx(face=None)
    c4.n = 400
    T._CACHE.clear()
    dflt = _np(T.apply(a, a, it, 0.35, c4))
    assert not np.array_equal(dflt, ref)


@pytest.mark.parametrize("kind", sorted(CONTRACT_KINDS))
@pytest.mark.parametrize("bad", [
    {"dur": "abc"}, {"x": "a", "y": None}, {"at": None}, {"avoid": "cara"}, {"avoid": [0.5]},
    {"avoid": [float("nan"), 0.3]}, {"dir": ["x"]}, {"dir": "diagonal"}, {"mode": "holes"},
    {"gate": "quiza"}, {"intensity": "x"}, {"intensity": float("nan")}, {"color": ["x"]},
    {"peak": 5}, {"ring": "tal vez"}, {"align": ["center"]}, {"kind_extra": 1}, {"id": None},
])
def test_malformed_params_never_raise(kind, bad):
    a, b = _imgs()
    ctx = _ctx()
    it = _item(kind)
    it.update(bad)
    for p in (0.2, 0.5, 0.8):
        out = T.apply(b if kind in MIXERS else a, a, it, p, ctx)
        assert (out.width(), out.height()) == (W, H)
    T.window(it)
    T.progress(it, 5.0)
    T.hit_time(it)
    T.effective_frac(it)


def test_malformed_values_fall_back_to_defaults():
    a, b = _imgs()
    ctx = _ctx()
    base = _np(T.apply(b, a, _item("torn_paper"), 0.5, ctx))
    assert np.array_equal(_np(T.apply(b, a, _item("torn_paper", mode="holes"), 0.5, ctx)), base)
    assert np.array_equal(_np(T.apply(b, a, _item("torn_paper", dir=["x"]), 0.5, ctx)), base)
    assert np.array_equal(_np(T.apply(b, a, _item("torn_paper", dir="LEFT"), 0.5, ctx)), base)
    assert np.array_equal(_np(T.apply(b, a, _item("torn_paper", avoid=[0.5]), 0.5, ctx)), base)
    # dur invalido = dur por defecto del kind
    it = _item("slide", dur="abc")
    assert T.window(it) == pytest.approx((5.0, 5.0 + T.DEFAULTS["slide"]["dur"]))


# ------------------------------------------------------------------ determinismo y robustez
@pytest.mark.parametrize("kind", sorted(CONTRACT_KINDS))
def test_deterministic(kind):
    T._CACHE.clear()
    o1 = _np(_render(kind, 0.43))
    T._CACHE.clear()
    o2 = _np(_render(kind, 0.43))
    o3 = _np(_render(kind, 0.43))
    assert np.array_equal(o1, o2) and np.array_equal(o2, o3)


@pytest.mark.parametrize("kind", sorted(CONTRACT_KINDS - {"cut"}))
@pytest.mark.parametrize("p", [0.1, 0.45, 0.8])
def test_zero_intensity_is_a_cut(kind, p):
    """intensity 0 = corte seco en todos los kinds: devuelve img tal cual."""
    a, b = _imgs()
    img = b if (kind in MIXERS or p >= 0.5) else a
    out = T.apply(img, a, _item(kind, intensity=0.0), p, _ctx())
    assert np.abs(_np(out) - _np(img)).max() <= 2


def _energy(arr):
    g = arr[..., :3].astype(np.float32).mean(axis=2)
    return np.abs(np.diff(g, axis=0)).mean() + np.abs(np.diff(g, axis=1)).mean()


@pytest.mark.parametrize("kind", ["flash", "light_leak", "whip", "zoom_blur", "slide", "circle", "film_burn",
                                  "glitch", "torn_paper"])
def test_intensity_scales_strength(kind):
    a, b = _imgs()
    img = b if kind in MIXERS else a
    ref = _np(a)
    p = 0.3

    def run(k):
        return _np(T.apply(img, a, _item(kind, intensity=k), p, _ctx()))
    lo, hi = run(0.3), run(1.0)
    if kind in ("whip", "zoom_blur", "slide"):
        # mas intensidad = mas desenfoque de movimiento = menos detalle
        assert _energy(hi) < _energy(lo), kind
    elif kind in ("glitch", "torn_paper"):
        assert not np.array_equal(lo, hi), kind
    else:
        assert np.abs(hi - ref).mean() > np.abs(lo - ref).mean(), kind
    # por encima de 1 se acota (contrato 0..1)
    assert np.array_equal(run(1.5), hi)


def test_dispatcher_robustness():
    a, b = _imgs()
    ctx = _ctx()
    assert T.apply(b, a, {"kind": "no_existe", "dur": 0.4}, 0.5, ctx) is b
    assert T.apply(b, a, {"kind": ["slide"], "dur": 0.4}, 0.5, ctx) is b
    assert T.apply(b, a, {"kind": "slide", "dur": 0.0}, 0.5, ctx) is b
    assert T.apply(b, a, "no es dict", 0.5, ctx) is b
    out = T.apply(b, a, _item("slide"), float("nan"), ctx)
    assert np.array_equal(_np(out), _np(a))
    out = T.apply(b, a, _item("slide"), "x", ctx)
    assert np.array_equal(_np(out), _np(a))
    out = T.apply(b, a, _item("light_leak"), 7.0, ctx)  # p fuera de rango se acota
    assert np.abs(_np(out) - _np(b)).max() <= 2


@pytest.mark.parametrize("kind", MIXERS)
def test_mixers_without_prev_or_other_size(kind):
    a, b = _imgs()
    ctx = _ctx()
    out = T.apply(b, None, _item(kind), 0.5, ctx)
    assert (out.width(), out.height()) == (W, H)
    big_a, _ = _imgs(W * 2, H * 2)
    out = T.apply(b, big_a, _item(kind), 0.5, ctx)
    assert (out.width(), out.height()) == (W, H)


@pytest.mark.parametrize("kind", ["flash", "light_leak", "glitch", "film_burn", "torn_paper", "circle"])
@pytest.mark.parametrize("col", ["no-es-color", "#12", "red", "#3E7BFA", 123, (10, 200, 30)])
def test_color_param_is_robust(kind, col):
    out = _render(kind, 0.45, color=col)
    assert (out.width(), out.height()) == (W, H)


# ------------------------------------------------------------------ rendimiento
def _load_factor() -> float:
    """Holgura por carga: con otros procesos saturando la maquina (load > nucleos) los tiempos
    suben en proporcion; el limite se escala igual (tope 4x) en vez de fallar en falso."""
    try:
        return min(4.0, max(1.0, os.getloadavg()[0] / (os.cpu_count() or 8)))
    except OSError:
        return 1.0


@pytest.mark.skipif(os.environ.get("REELFY_SKIP_PERF") == "1", reason="perf desactivado")
def test_performance_budget():
    """Mejor de 3 corridas del promedio por llamada a lo largo de la ventana (el minimo es
    estable aunque haya otros procesos). Presupuesto del contrato: el cuadro completo <= 60 ms
    (final) / 15 ms (draft); una transicion debe ser una fraccion. Con la maquina saturada el
    limite se escala por la carga (ver _load_factor)."""
    lf = _load_factor()
    res = {}
    for q, (w, h), lim in (("final", (1080, 1920), 40.0), ("draft", (540, 960), 14.0)):
        lim *= lf
        a, b = _imgs(w, h)
        ctx = _ctx(w, h, quality=q)
        for kind in sorted(CONTRACT_KINDS - {"cut"}):
            it = _item(kind)
            ps = np.linspace(0.04, 0.96, 12)
            T.apply(b, a, it, 0.5, ctx)  # calienta caches del item
            best = 1e9
            for _ in range(3):
                t0 = time.perf_counter()
                for p in ps:
                    T.apply(b, a, it, float(p), ctx)
                best = min(best, (time.perf_counter() - t0) / len(ps) * 1000)
            res[(q, kind)] = best
            assert best < lim, (q, kind, best)
    print(f"load x{lf:.2f}", {f"{q}:{k}": round(v, 1) for (q, k), v in res.items()})
