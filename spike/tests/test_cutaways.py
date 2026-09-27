"""Tests de motion/cutaways.py: fondos animados, escenas a pantalla completa, entradas/salidas,
items embebidos, fallback de texto, determinismo y presupuesto de tiempo."""
import os
import time

import numpy as np
import pytest

from motion import ctx as C
from motion import cutaways as CU

W, H = 540, 960

def _load_factor() -> float:
    """Holgura de los umbrales de rendimiento cuando la maquina esta cargada por otros procesos
    (la Mac se comparte con otros renders): carga/nucleos, entre 1 y 3."""
    try:
        la = os.getloadavg()[0]
    except (OSError, AttributeError):
        return 1.0
    return min(3.0, max(1.0, la / (os.cpu_count() or 8)))



def _base_img(w=W, h=H):
    """Cuadro base sintetico con estructura (para blur_self/photo/self_broll)."""
    y, x = np.mgrid[0:h, 0:w]
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0] = (x * 255 // max(1, w - 1)).astype(np.uint8)
    a[..., 1] = (y * 255 // max(1, h - 1)).astype(np.uint8)
    a[..., 2] = ((x // 40 + y // 40) % 2 * 200).astype(np.uint8)
    a[..., 3] = 255
    return C.image_from_rgba(a)


def _ctx(t=0.5, w=W, h=H, base=True, seed=7):
    c = C.demo_ctx(W=w, H=h, t=t, quality="draft" if w < 1000 else "final", seed=seed)
    c.n = int(t * 30)
    if base:
        c._cache["base_image"] = _base_img(w, h)
    return c


def _canvas(w=W, h=H, fill="#FF00FF"):
    arr, surf = C.new_frame(w, h)
    surf.getCanvas().clear(C.color(fill))
    return arr, surf.getCanvas()


MAGENTA = np.array([255, 0, 255])


def _magenta_frac(arr):
    return float((np.abs(arr[..., :3].astype(int) - MAGENTA).sum(axis=2) < 6).mean())


# -------------------------------------------------------------- fondos
def test_catalogs():
    assert set(CU.BACKGROUNDS) == {"grid_warp", "gradient", "paper", "dark_stars", "rays", "blur_self",
                                   "color", "halftone"}
    assert set(CU.RENDERERS) == {"title_card", "info_card", "photo", "self_broll"}


@pytest.mark.parametrize("kind", CU.BACKGROUNDS)
def test_background_covers_frame_and_animates(kind):
    arr, cv = _canvas()
    ctx = _ctx(0.3)
    CU.draw_background(cv, kind, None, 0.3, ctx)
    a = arr.copy()
    assert _magenta_frac(a) < 0.001, f"{kind}: no cubre todo el cuadro"
    assert (a[..., 3] == 255).all()
    arr2, cv2 = _canvas()
    ctx2 = _ctx(1.7)
    CU.draw_background(cv2, kind, None, 1.7, ctx2)
    if kind not in ("color",):  # color es estatico a proposito (solo grano en final)
        assert not np.array_equal(a, arr2), f"{kind}: deberia animarse"


@pytest.mark.parametrize("kind", CU.BACKGROUNDS)
def test_background_custom_colors(kind):
    arr, cv = _canvas()
    CU.draw_background(cv, kind, ["#102030", "#F0E0D0", "#00FF88", "#8800FF"], 0.8, _ctx(0.8))
    assert _magenta_frac(arr) < 0.001


def test_blur_self_without_base_falls_back():
    arr, cv = _canvas()
    CU.draw_background(cv, "blur_self", None, 0.5, _ctx(base=False))
    assert _magenta_frac(arr) < 0.001


def test_blur_self_is_blurred_and_darker():
    ctx = _ctx()
    arr, cv = _canvas()
    CU.draw_background(cv, "blur_self", None, 0.5, ctx)
    base = C.image_to_rgba(ctx.base_image())[..., :3].astype(float)
    out = arr[..., :3].astype(float)
    # menos detalle (gradiente local) y mas oscuro que el original
    gy_b = np.abs(np.diff(base, axis=0)).mean()
    gy_o = np.abs(np.diff(out, axis=0)).mean()
    assert gy_o < gy_b * 0.5
    assert out.mean() < base.mean()


@pytest.mark.parametrize("kind", CU.BACKGROUNDS)
def test_background_deterministic(kind):
    a, cv = _canvas()
    CU.draw_background(cv, kind, None, 1.1, _ctx(1.1))
    b, cv2 = _canvas()
    CU.draw_background(cv2, kind, None, 1.1, _ctx(1.1))
    assert np.array_equal(a, b)


def test_is_dark():
    ctx = _ctx()
    assert CU.is_dark("dark_stars", None, ctx)
    assert not CU.is_dark("paper", None, ctx)
    assert CU.is_dark("color", ["#101010"], ctx)
    assert not CU.is_dark("color", ["#FAFAFA"], ctx)


# -------------------------------------------------------------- escenas
SCENES = [
    ("title_card", {"background": "grid_warp", "text": "Necesitamos hablar."}),
    ("title_card", {"background": "dark_stars", "text": {"preset": "stack", "text": "Una buena\nlimpieza"},
                    "graphics": [{"kind": "doodle", "name": "sparkle", "x": 0.8, "y": 0.3, "offset": 0.2},
                                 {"kind": "icon", "name": "dental", "badge": "circle", "offset": 0.4}]}),
    ("info_card", {"background": "gradient", "variant": "price", "title": "Limpieza dental",
                   "value": "$1,000 MXN", "text": "Tiene un costo de"}),
    ("info_card", {"background": "blur_self", "card": {"variant": "location", "title": "Av. Central 123",
                                                       "subtitle": "a 2 cuadras de la Glorieta Norte"}}),
    ("photo", {"background": "paper", "caption": "Av. Central 123"}),
    ("self_broll", {"zoom_from": 1.0, "zoom_to": 1.3, "pan": [0.02, -0.01], "text": "123"}),
]


@pytest.mark.parametrize("kind,item", SCENES)
def test_scenes_cover_frame(kind, item):
    item = dict(item, id=f"s_{kind}")
    for lt in (0.0, 0.2, 1.0, 2.5, 2.99):
        arr, cv = _canvas()
        CU.RENDERERS[kind](cv, item, lt, 3.0, _ctx(lt))
        assert _magenta_frac(arr) < 0.001, f"{kind} @ {lt}: deja ver el cuadro base"


@pytest.mark.parametrize("ain", ["cut", "fade", "zoom", "slide_up", "slide_left", "circle", "wipe"])
def test_anim_in_reveals_progressively(ain):
    item = {"id": "ain", "background": "color", "bg_colors": ["#202020"], "anim_in": ain, "in_dur": 0.4}
    arr, cv = _canvas()
    CU.RENDERERS["title_card"](cv, item, 0.05, 3.0, _ctx(0.05))
    early = _magenta_frac(arr)
    arr2, cv2 = _canvas()
    CU.RENDERERS["title_card"](cv2, item, 1.0, 3.0, _ctx(1.0))
    assert _magenta_frac(arr2) < 0.001
    if ain == "cut":
        assert early < 0.001
    elif ain not in ("fade", "zoom"):  # fade y zoom mezclan (no hay magenta puro)
        assert early > 0.05, f"{ain}: al inicio deberia verse el cuadro base"


@pytest.mark.parametrize("aout", ["cut", "fade", "zoom", "slide_down", "slide_up", "circle"])
def test_anim_out(aout):
    item = {"id": "aout", "background": "color", "bg_colors": ["#202020"], "anim_out": aout, "out_dur": 0.4}
    arr, cv = _canvas()
    CU.RENDERERS["title_card"](cv, item, 2.99, 3.0, _ctx(2.99))
    if aout == "cut":
        assert _magenta_frac(arr) < 0.001
    else:
        assert _magenta_frac(arr) > 0.3 or aout in ("fade", "zoom")


def test_embedded_graphics_respect_offset():
    item = {"id": "emb", "background": "color", "bg_colors": ["#202020"],
            "graphics": [{"kind": "sticker_text", "text": "HOLA", "x": 0.5, "y": 0.5, "offset": 1.0,
                          "length": 1.0}]}
    frames = {}
    for lt in (0.5, 1.6, 2.5):
        arr, cv = _canvas()
        CU.RENDERERS["title_card"](cv, item, lt, 3.0, _ctx(lt))
        frames[lt] = arr.copy()
    # antes y despues del rango el sticker no esta; durante, si
    assert np.array_equal(frames[0.5][..., :3].mean(axis=(0, 1)).round(), frames[2.5][..., :3].mean(axis=(0, 1)).round()) \
        or abs(frames[0.5][..., :3].mean() - frames[2.5][..., :3].mean()) < 0.5
    assert abs(frames[1.6][..., :3].mean() - frames[0.5][..., :3].mean()) > 1.0


def test_fallback_title_draws_without_text_module():
    arr, cv = _canvas(fill="#101010")
    CU.fallback_title(cv, {"text": "Necesitamos hablar.", "accent_words": ["hablar."], "size": 0.1}, 1.0, 3.0,
                      _ctx())
    changed = (np.abs(arr[..., :3].astype(int) - 16).sum(axis=2) > 30).mean()
    assert changed > 0.01


def test_photo_uses_still_when_provided():
    still = _base_img()
    item = {"id": "ph", "background": "color", "bg_colors": ["#303030"], "_still": still}
    a, cv = _canvas()
    CU.RENDERERS["photo"](cv, item, 2.0, 3.0, _ctx(2.0, base=False))
    assert _magenta_frac(a) < 0.001
    # la foto trae colores del still (azul del patron de tablero)
    assert (a[..., 2] > 150).mean() > 0.05


def test_scene_deterministic():
    item = {"id": "det", "background": "dark_stars", "text": "Hola"}
    a, cv = _canvas()
    CU.RENDERERS["title_card"](cv, item, 1.3, 3.0, _ctx(1.3))
    b, cv2 = _canvas()
    CU.RENDERERS["title_card"](cv2, item, 1.3, 3.0, _ctx(1.3))
    assert np.array_equal(a, b)


# -------------------------------------------------------------- rendimiento (final 1080x1920)
@pytest.mark.parametrize("kind,budget", [("grid_warp", 14.0), ("gradient", 16.0), ("paper", 6.0),
                                         ("dark_stars", 14.0), ("rays", 10.0), ("blur_self", 16.0),
                                         ("color", 8.0), ("halftone", 10.0)])
def test_background_performance_final(kind, budget):
    arr, cv = _canvas(1080, 1920)
    ctx = _ctx(0.0, 1080, 1920)
    CU.draw_background(cv, kind, None, 0.1, ctx)  # calentar caches (texturas, capas)
    ts = []
    for i in range(10):
        t = 0.2 + i * 0.25
        ctx.n = int(t * 30)
        t0 = time.perf_counter()
        CU.draw_background(cv, kind, None, t, ctx)
        ts.append((time.perf_counter() - t0) * 1000)
    # percentil 25: costo propio (la maquina puede estar cargada por otros procesos)
    assert float(np.percentile(ts, 25)) < budget * _load_factor(), f"{kind}: {np.percentile(ts, 25):.2f} ms"


def test_garbage_scene_inputs_never_crash():
    junk = [None, "", "zzz", -5, 1e9, float("nan"), ["#12"], "#GG0000", {"x": 1}]
    keys = ["background", "bg_colors", "text", "graphics", "anim_in", "anim_out", "in_dur", "zoom_from",
            "zoom_to", "pan", "caption", "variant", "card", "tilt"]
    rng = np.random.default_rng(1)
    for kind in CU.RENDERERS:
        for _ in range(20):
            it = {"id": "junk"}
            for k in rng.choice(keys, 5, replace=False):
                it[str(k)] = junk[int(rng.integers(len(junk)))]
            for lt in (0.0, 1.0, 2.9):
                arr, cv = _canvas()
                try:
                    CU.RENDERERS[kind](cv, it, lt, 3.0, _ctx(lt))
                except Exception as e:  # pragma: no cover
                    raise AssertionError(f"{kind} {it} @ {lt}: {e!r}")


def test_background_bad_colors_fall_back():
    arr, cv = _canvas()
    CU.draw_background(cv, "grid_warp", ["no-es-color", None, 5], 0.5, _ctx())
    assert _magenta_frac(arr) < 0.001
    arr, cv = _canvas()
    CU.draw_background(cv, "no_existe", None, 0.5, _ctx())
    assert _magenta_frac(arr) < 0.001


def test_defaults_catalog():
    assert set(CU.DEFAULTS) == set(CU.RENDERERS)
    for d in CU.DEFAULTS.values():
        assert d.get("background", "gradient") in CU.BACKGROUNDS


# -------------------------------------------------------------- regresiones (revision critica)
DIR_COLS = ["#0E0E12", "#2EC4B6"]  # lo que manda el director a todos los fondos


def _director_card():
    return {"kind": "card", "variant": "location", "title": "Av. Central 123",
            "subtitle": "a 2 cuadras de la Glorieta Norte", "value": "", "icon": "map-pin", "theme": "light",
            "color": "#FFFFFF", "anim_in": "rise", "anim_out": "fade", "rotation": 0.0, "items": [],
            "x": 0.5, "y": 0.47, "size": 0.8}


def test_info_card_director_shape_has_no_ghost_card():
    # el director manda la tarjeta en graphics: el info_card NO debe dibujar otra tarjeta propia
    base = {"id": "ic", "background": "blur_self", "bg_colors": DIR_COLS, "graphics": [_director_card()]}
    assert not CU.has_own_card(base)
    for lt in (0.1, 0.8, 2.0):
        a, cv = _canvas()
        CU.RENDERERS["info_card"](cv, dict(base, kind="info_card"), lt, 2.6, _ctx(lt))
        b, cv2 = _canvas()
        CU.RENDERERS["title_card"](cv2, dict(base, kind="title_card"), lt, 2.6, _ctx(lt))
        assert np.array_equal(a, b), lt
    # pixeles fuera del rect de la tarjeta embebida = solo fondo
    a, cv = _canvas()
    CU.RENDERERS["info_card"](cv, base, 1.5, 2.6, _ctx(1.5))
    bg, cv2 = _canvas()
    CU.draw_background(cv2, "blur_self", DIR_COLS, 1.5, _ctx(1.5))
    diff = np.abs(a[..., :3].astype(int) - bg[..., :3].astype(int)).sum(axis=2) > 24
    ys, xs = np.nonzero(diff)
    from motion import graphics as G
    wc, hc = G.card_size(_director_card(), _ctx())
    cx = G.safe_cx(0.5 * W, wc / 2, _ctx())
    margin = 0.06 * W  # sombra suave
    assert xs.min() >= cx - wc / 2 - margin and xs.max() <= cx + wc / 2 + margin


def test_info_card_with_own_fields_draws_card():
    it = {"id": "ic2", "background": "color", "bg_colors": ["#202020"], "variant": "price", "value": "$1,000"}
    assert CU.has_own_card(it) and CU.has_own_card({"card": {"variant": "link"}})
    a, cv = _canvas()
    CU.RENDERERS["info_card"](cv, it, 1.5, 3.0, _ctx(1.5))
    b, cv2 = _canvas()
    CU.draw_background(cv2, "color", ["#202020"], 1.5, _ctx(1.5))
    assert (np.abs(a.astype(int) - b.astype(int)).sum(axis=2) > 30).mean() > 0.05


def _brightest(arr, q=99.7):
    a = arr[..., :3].astype(float)
    lum = a @ np.array([0.2126, 0.7152, 0.0722])
    m = lum >= np.percentile(lum, q)
    return a[m].mean(axis=0)


def test_bg_colors_semantics_director_colors():
    # [oscuro, acento]: reticula y estrellas BLANCAS (no del acento), como en el video objetivo
    for kind, q in (("grid_warp", 99.7), ("dark_stars", 99.98)):  # las estrellas son pocas y chicas
        arr, cv = _canvas()
        CU.draw_background(cv, kind, DIR_COLS, 0.8, _ctx(0.8))
        r, g, b = _brightest(arr, q)
        assert min(r, g, b) > 170 and max(r, g, b) - min(r, g, b) < 60, (kind, r, g, b)
        assert CU.is_dark(kind, DIR_COLS, _ctx())
    # indice 2 explicito = tinta de las lineas
    arr, cv = _canvas()
    CU.draw_background(cv, "grid_warp", DIR_COLS + ["#FF3355"], 0.8, _ctx(0.8))
    r, g, b = _brightest(arr)
    assert r > g + 60
    assert set(CU.BG_COLOR_ROLES) == set(CU.BACKGROUNDS)


def test_paper_dark_keeps_texture_and_reports_dark():
    ctx = _ctx()
    arr, cv = _canvas()
    CU.draw_background(cv, "paper", DIR_COLS, 0.5, ctx)
    lum = arr[..., :3].astype(float) @ np.array([0.2126, 0.7152, 0.0722])
    assert lum.mean() < 0.35 * 255
    import cv2
    detail = np.abs(lum - cv2.GaussianBlur(lum, (0, 0), 6)).mean()
    assert detail > 0.8, detail          # relieve visible (no un cuadro negro plano)
    assert CU.is_dark("paper", DIR_COLS, ctx)
    assert not CU.is_dark("paper", ["#F7F4EE", "#FFD23F"], ctx)
    assert not CU.is_dark("paper", None, ctx)


def test_rays_low_contrast_with_clear_title_patch():
    arr, cv = _canvas()
    CU.draw_background(cv, "rays", DIR_COLS, 1.0, _ctx(1.0))
    lum = arr[..., :3].astype(float) @ np.array([0.2126, 0.7152, 0.0722])
    assert np.percentile(lum, 98) - np.percentile(lum, 2) < 90   # cunas suaves, no franjas duras
    band = lum[int(0.40 * H):int(0.52 * H), int(0.25 * W):int(0.7 * W)]
    assert band.mean() <= lum.mean() + 2                          # el parche despeja el titulo
    # claro: default amarillo; el titulo oscuro se lee sobre un parche mas claro
    arr, cv = _canvas()
    CU.draw_background(cv, "rays", None, 1.0, _ctx(1.0))
    lum2 = arr[..., :3].astype(float) @ np.array([0.2126, 0.7152, 0.0722])
    assert lum2[int(0.40 * H):int(0.52 * H), int(0.3 * W):int(0.65 * W)].mean() >= lum2.mean()


def test_halftone_never_empties():
    for lt in np.linspace(0.0, 4.0, 17):
        arr, cv = _canvas()
        CU.draw_background(cv, "halftone", None, float(lt), _ctx(float(lt)))
        lum = arr[..., :3].astype(float).mean(axis=2)
        outside = np.concatenate([lum[:int(0.25 * H)].ravel(), lum[int(0.7 * H):].ravel()])
        assert (outside < 128).mean() > 0.04, lt   # siempre hay puntos fuera de la banda del titulo


def test_self_broll_clamps_zoom_and_pan_to_cover_frame():
    for it in ({"zoom_from": 0.5, "zoom_to": 0.8}, {"zoom_from": 1.0, "zoom_to": 1.1, "pan": [0.6, -0.6]},
               {"zoom_from": 1.2, "zoom_to": 1.2, "pan": [-2.0, 2.0]}):
        for lt in (0.0, 1.5, 2.99):
            arr, cv = _canvas()
            ctx = _ctx(lt)
            ctx.face = (0.8, 0.2, 0.2, 0.1)
            CU.RENDERERS["self_broll"](cv, dict(it, id="sb"), lt, 3.0, ctx)
            assert _magenta_frac(arr) < 0.001, (it, lt)


def test_frame_request_declares_what_render_must_inject():
    assert CU.frame_request({"kind": "photo", "start": 12.5}) == ("_still", "start", 12.5)
    assert CU.frame_request({"kind": "self_broll", "start": 3.0, "src_t": 20.0}) == ("_src_image", "src_t", 20.0)
    assert CU.frame_request({"kind": "title_card", "start": 1.0}) is None
    # con la imagen inyectada, self_broll usa ESE cuadro (no el base vivo)
    src = _base_img()
    arr = np.full((H, W, 4), 255, np.uint8)
    arr[..., 1] = 0
    ctx = _ctx(0.5)
    ctx._cache["base_image"] = C.image_from_rgba(arr)  # base vivo magenta
    a, cv = _canvas()
    CU.RENDERERS["self_broll"](cv, {"id": "sb", "_src_image": src}, 0.5, 3.0, ctx)
    assert _magenta_frac(a) < 0.001


def test_texture_cache_is_bounded_by_bytes():
    ctx = _ctx(w=1080, h=1920, base=False)
    for i in range(14):
        arr, cv = _canvas(1080, 1920)
        CU.draw_background(cv, "color", [f"#{i * 17:02X}2030"], 0.2, ctx)
    assert CU._TEX.nbytes <= CU._TEX.cap_bytes


def test_fallback_title_stays_inside_safe():
    arr, cv = _canvas(fill="#101010")
    CU.fallback_title(cv, {"text": "Una frase bastante larga para un titulo de escena completa",
                           "size": 0.12}, 1.5, 3.0, _ctx())
    m = np.abs(arr[..., :3].astype(int) - 16).sum(axis=2) > 60
    ys, xs = np.nonzero(m)
    assert xs.min() >= C.SAFE["left"] * W - 2 and xs.max() <= C.SAFE["right"] * W + 2


def test_scene_text_defaults_to_safe_center():
    from motion import graphics as G
    ti = CU._text_item({"text": "Hola"}, True, _ctx())
    assert abs(ti["x"] - G.XC) < 1e-9
    assert CU._text_item({"text": {"preset": "title_pop", "text": "  "}}, True, _ctx()) is None
    assert CU._text_item({"text": {"preset": "counter", "to": 1000}}, True, _ctx()) is not None


# -------------------------------------------------------------- ola 2
def test_scene_text_measures_scene_background():
    """El texto de una escena mide el fondo de la ESCENA (no el video tapado): _bg_lum."""
    dark = CU._text_item({"id": "s", "text": "Hola"}, True, _ctx())
    light = CU._text_item({"id": "s", "text": "Hola"}, False, _ctx())
    assert dark["_bg_lum"] < 0.3 and light["_bg_lum"] > 0.7
    import inspect
    src = inspect.getsource(CU)
    assert src.count("setShader(skia.GradientShader") == src.count("setDither(True)")
