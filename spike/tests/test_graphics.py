"""Tests de motion/graphics.py: contrato de renderers, catalogo completo, determinismo,
animacion (dibujo, boil, salida), tarjetas (contador, zona segura) y presupuesto de tiempo."""
import os
import math
import time

import numpy as np
import pytest

from motion import ctx as C
from motion import graphics as G

W, H = 540, 960  # resolucion de prueba (draft) salvo donde se indica

def _load_factor() -> float:
    """Holgura de los umbrales de rendimiento cuando la maquina esta cargada por otros procesos
    (la Mac se comparte con otros renders): carga/nucleos, entre 1 y 3."""
    try:
        la = os.getloadavg()[0]
    except (OSError, AttributeError):
        return 1.0
    return min(3.0, max(1.0, la / (os.cpu_count() or 8)))



def _frame(w=W, h=H, bg="#6E7B64"):
    arr, surf = C.new_frame(w, h)
    surf.getCanvas().clear(C.color(bg))
    return arr, surf.getCanvas()


def _ctx(t=0.0, w=W, h=H, **kw):
    kw.setdefault("entities", {"name": "Dra. Ana Ruiz", "role": "Dentista"})
    return C.demo_ctx(W=w, H=h, t=t, quality="draft" if w < 1000 else "final", **kw)


def _draw(kind, item, lt, dur=3.0, w=W, h=H, seed=7, face=None, bg="#6E7B64"):
    arr, cv = _frame(w, h, bg)
    ctx = _ctx(lt, w, h, seed=seed)
    ctx.face = face
    G.RENDERERS[kind](cv, item, lt, dur, ctx)
    return arr.copy()


def _diff(a, b):
    return np.abs(a.astype(np.int16) - b.astype(np.int16)).sum(axis=2) > 12


BG = _frame()[0].copy()  # fondo liso de referencia


def _changed(arr):
    return int(_diff(arr, BG).sum())


# -------------------------------------------------------------- contrato / catalogo
def test_renderers_registry():
    assert set(G.RENDERERS) == {"doodle", "icon", "card", "lower_third", "cta", "progress", "sticker_text"}
    for fn in G.RENDERERS.values():
        assert callable(fn)


@pytest.mark.parametrize("name", G.DOODLE_NAMES)
def test_every_doodle_draws_and_animates(name):
    it = {"id": f"t_{name}", "kind": "doodle", "name": name, "x": 0.5, "y": 0.45}
    early = _changed(_draw("doodle", it, 0.02))
    late = _changed(_draw("doodle", it, 1.2))
    assert late > 150, f"{name}: casi no dibuja nada"
    assert early < late, f"{name}: deberia dibujarse progresivamente"


@pytest.mark.parametrize("variant", G.CARD_VARIANTS)
@pytest.mark.parametrize("theme", ["light", "dark", "brand"])
def test_every_card_variant_theme(variant, theme):
    it = {"id": f"c_{variant}_{theme}", "variant": variant, "theme": theme, "title": "Limpieza dental",
          "subtitle": "a 2 cuadras de la Glorieta Norte", "value": "$1,000 MXN",
          "items": ["Uno", "Dos"], "label": "Agenda aqui"}
    for lt in (0.0, 0.3, 1.5, 2.99):
        _draw("card", it, lt)
    assert _changed(_draw("card", it, 1.5)) > 5000


@pytest.mark.parametrize("variant", G.CTA_VARIANTS)
def test_every_cta_variant(variant):
    it = {"id": f"cta_{variant}", "variant": variant, "label": "Dra. Ana Ruiz"}
    for lt in (0.0, 0.5, 1.1, 1.6, 2.9):
        _draw("cta", it, lt)
    assert _changed(_draw("cta", it, 1.5)) > 2000


@pytest.mark.parametrize("kind,item", [
    ("icon", {"name": "dental", "badge": "squircle"}),
    ("icon", {"name": "map-pin", "badge": "circle", "variant": "filled"}),
    ("icon", {"name": "thumb-up", "style": "sketch"}),
    ("icon", {"name": "no-existe-este-icono-xyz"}),
    ("lower_third", {"title": "Dra. Ana Ruiz", "subtitle": "Dentista"}),
    ("lower_third", {"title": "Dra. Ana Ruiz", "subtitle": "Dentista", "theme": "light"}),
    ("sticker_text", {"text": "$1,000"}),
    ("progress", {}),
])
def test_other_kinds(kind, item):
    item = dict(item, id=f"{kind}_x")
    for lt in (0.0, 0.2, 1.0, 2.9):
        _draw(kind, item, lt)
    assert _changed(_draw(kind, item, 1.0)) > 300


def test_unknown_names_fall_back():
    _draw("doodle", {"id": "u", "name": "no_existe"}, 1.0)
    _draw("card", {"id": "u", "variant": "no_existe", "title": "Hola"}, 1.0)
    _draw("cta", {"id": "u", "variant": "no_existe"}, 1.0)
    _draw("icon", {"id": "u"}, 1.0)
    _draw("doodle", {"id": "u", "name": "arrow_curve", "anim_in": "raro", "anim_out": "raro"}, 1.0)


@pytest.mark.parametrize("ain", G.ANIM_IN)
@pytest.mark.parametrize("aout", G.ANIM_OUT)
def test_all_anim_in_out_combinations(ain, aout):
    it = {"id": "anim", "name": "star5", "anim_in": ain, "anim_out": aout}
    for lt in (0.0, 0.05, 0.3, 1.0, 1.9, 1.999):
        _draw("doodle", it, lt, dur=2.0)


# -------------------------------------------------------------- determinismo
def test_deterministic_same_seed():
    it = {"id": "det", "name": "circle_scribble", "size": 0.5}
    a = _draw("doodle", it, 0.7, seed=3)
    b = _draw("doodle", it, 0.7, seed=3)
    assert np.array_equal(a, b)
    c = _draw("card", {"id": "det", "variant": "location", "title": "Av. Central 123"}, 1.2, seed=3)
    d = _draw("card", {"id": "det", "variant": "location", "title": "Av. Central 123"}, 1.2, seed=3)
    assert np.array_equal(c, d)


def test_seed_and_id_change_hand_drawn_variation():
    it = {"id": "var", "name": "circle_scribble", "size": 0.5}
    a = _draw("doodle", it, 1.0, seed=3)
    b = _draw("doodle", it, 1.0, seed=4)
    c = _draw("doodle", dict(it, id="otro"), 1.0, seed=3)
    assert not np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_boil_runs_at_12fps():
    # squiggle no tiene micro-movimiento: solo cambia por el boil (pasos de 1/12 s)
    it = {"id": "boil", "name": "squiggle", "wiggle": 0.8}
    a = _draw("doodle", it, 1.001)   # paso 12
    b = _draw("doodle", it, 1.06)    # mismo paso (12.7)
    c = _draw("doodle", it, 1.09)    # paso 13
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


# -------------------------------------------------------------- animacion
def test_exit_pop_out_vanishes():
    it = {"id": "out", "name": "heart", "anim_out": "pop_out"}
    mid = _changed(_draw("doodle", it, 1.0, dur=2.0))
    end = _changed(_draw("doodle", it, 1.999, dur=2.0))
    assert end < mid * 0.15


def test_undraw_erases_progressively():
    it = {"id": "und", "name": "underline", "anim_out": "undraw", "out_dur": 0.5}
    full = _changed(_draw("doodle", it, 1.0, dur=2.0))
    half = _changed(_draw("doodle", it, 1.75, dur=2.0))
    assert half < full * 0.8


def test_doodle_localized_around_xy():
    arr = _draw("doodle", {"id": "loc", "name": "sparkle", "x": 0.25, "y": 0.3, "size": 0.15}, 1.0)
    ys, xs = np.nonzero(_diff(arr, BG))
    assert len(xs) > 0
    assert abs(xs.mean() / W - 0.25) < 0.08 and abs(ys.mean() / H - 0.3) < 0.08


def test_path_to_arrow_reaches_target():
    it = {"id": "pt", "name": "arrow_curve", "x": 0.2, "y": 0.7, "path_to": [0.8, 0.3], "shadow": 0}
    arr = _draw("doodle", it, 1.5)
    m = _diff(arr, BG)
    # hay trazo cerca del inicio y cerca del destino
    assert m[int(0.66 * H):int(0.74 * H), int(0.16 * W):int(0.26 * W)].sum() > 20
    assert m[int(0.25 * H):int(0.36 * H), int(0.72 * W):int(0.86 * W)].sum() > 20


def test_anchor_face_places_next_to_face():
    ctx = _ctx()
    ctx.face = (0.5, 0.3, 0.2, 0.12)
    x, y = G.center_of({"anchor": "face_right"}, ctx)
    assert x / W > 0.6 and abs(y / H - 0.27) < 0.05
    x2, _ = G.center_of({"anchor": "face_left"}, ctx)
    assert x2 / W < 0.4


# -------------------------------------------------------------- tarjetas
@pytest.mark.parametrize("s,expect", [
    ("$1,000 MXN", ("$", 1000.0, " MXN", 0, ",")),
    ("$1.500,50", ("$", 1500.5, "", 2, ".")),
    ("4.9", ("", 4.9, "", 1, ",")),
    ("mil pesos", ("mil pesos", 0.0, "", 0, ",")),
    ("$12,345.67 USD", ("$", 12345.67, " USD", 2, ",")),
])
def test_parse_amount(s, expect):
    pre, val, suf, dec, sep = G.parse_amount(s)
    assert (pre, suf, dec, sep) == (expect[0], expect[2], expect[3], expect[4])
    assert math.isclose(val, expect[1])


def test_fmt_amount():
    assert G.fmt_amount(1000, 0, ",") == "1,000"
    assert G.fmt_amount(1500.5, 2, ".") == "1.500,50"


def test_price_counter_counts_up_then_settles():
    it = {"id": "cnt", "variant": "price", "title": "Limpieza", "value": "$1,000 MXN", "shadow": 0}
    a = _draw("card", it, 0.3)
    b = _draw("card", it, 0.6)
    c = _draw("card", it, 2.0)
    d = _draw("card", it, 2.5)
    assert not np.array_equal(a, b)          # contando
    assert np.array_equal(c, d)              # asentada: cuadro estable


def test_card_stays_inside_safe_zone():
    th, F, u, pad, inner, rows, gap, Wc, Hc = G.card_layout(
        {"variant": "location", "title": "Av. Central 123", "subtitle": "a 2 cuadras"}, _ctx())
    arr = _draw("card", {"id": "safe", "variant": "location", "title": "Av. Central 123",
                         "subtitle": "a 2 cuadras", "y": 0.95, "anim_in": "none"}, 2.0)
    ys, _ = np.nonzero(_diff(arr, BG))
    assert ys.max() / H <= C.SAFE["bottom"] + 0.03


def test_card_long_text_is_wrapped_and_fits():
    it = {"id": "long", "variant": "generic", "title": "Un titulo larguisimo " * 6,
          "subtitle": "subtitulo " * 20, "size": 0.8}
    arr = _draw("card", it, 2.0)
    _, xs = np.nonzero(_diff(arr, BG))
    assert xs.min() >= W * 0.05 and xs.max() <= W * 0.95


def test_wrap_and_fit():
    f = G.font("Poppins-Bold", 40)
    lines = G.wrap(f, "Horarios disponibles en el link de abajo", 200, 2)
    assert 1 <= len(lines) <= 2
    for ln in lines:
        assert f.measureText(ln) <= 200 + 1
    ff = G.fit_font("Poppins-Bold", "Avenida Central 1234", 80, 300)
    assert ff.measureText("Avenida Central 1234") <= 301


def test_trim_helpers_tolerate_full_range():
    assert G.trim(0.0, 1.0) is None
    p = G.trim_paint(C.stroke("#FFFFFF", 3), 0.0, 1.0)
    assert p.getPathEffect() is None
    assert G.compose(None, None) is None


# -------------------------------------------------------------- rendimiento (final 1080x1920)
@pytest.mark.parametrize("kind,item,budget", [
    ("doodle", {"name": "arrow_curve"}, 8.0),
    ("doodle", {"name": "circle_scribble", "size": 0.5}, 8.0),
    ("icon", {"name": "dental", "badge": "squircle"}, 6.0),
    ("card", {"variant": "location", "title": "Av. Central 123", "subtitle": "a 2 cuadras"}, 14.0),
    ("card", {"variant": "price", "title": "Limpieza", "value": "$1,000 MXN"}, 12.0),
    ("cta", {"variant": "follow", "label": "Dra. Ana Ruiz"}, 10.0),
    ("lower_third", {"title": "Dra. Ana Ruiz", "subtitle": "Dentista"}, 6.0),
])
def test_performance_final(kind, item, budget):
    arr, cv = _frame(1080, 1920)
    ctx = _ctx(0.0, 1080, 1920)
    item = dict(item, id=f"perf_{kind}")
    G.RENDERERS[kind](cv, item, 0.5, 3.0, ctx)
    ts = []
    for i in range(12):
        t = 0.1 + i * 0.2
        t0 = time.perf_counter()
        G.RENDERERS[kind](cv, item, t, 3.0, ctx)
        ts.append((time.perf_counter() - t0) * 1000)
    # percentil 25: costo propio del renderer (la maquina puede estar cargada por otros procesos)
    assert float(np.percentile(ts, 25)) < budget * _load_factor(), f"{kind}: {np.percentile(ts, 25):.2f} ms"


# -------------------------------------------------------------- robustez / integracion con schema
def test_garbage_inputs_never_crash():
    rng = np.random.default_rng(0)
    junk = [None, "", "abc", -1, 0, 1e9, float("nan"), [1], {"a": 1}, "#GGHHII", "red", True]
    keys = ["x", "y", "size", "color", "accent", "badge_color", "bg", "fg", "rotation", "stroke", "wiggle",
            "anim_in", "anim_out", "path_to", "title", "subtitle", "value", "items", "variant", "theme",
            "name", "label", "text", "intensity", "draw", "badge", "style", "thickness"]
    for kind in G.RENDERERS:
        for _ in range(25):
            it = {"id": "junk"}
            for k in rng.choice(keys, 6, replace=False):
                it[str(k)] = junk[int(rng.integers(len(junk)))]
            for lt in (0.0, 0.7, 2.9):
                try:
                    _draw(kind, it, lt)
                except Exception as e:  # pragma: no cover - el mensaje ayuda a depurar
                    raise AssertionError(f"{kind} {it} @ {lt}: {e!r}")


def test_schema_anim_names_are_accepted():
    # schema.py usa "rise" y "scale_down": deben equivaler a slide_up y scale
    for alias, name in (("rise", "slide_up"), ("scale_down", "scale")):
        for kind, it in (("sticker_text", {"id": "al", "text": "HOLA"}),
                         ("card", {"id": "al", "variant": "price", "value": "$1,000"})):
            a = _draw(kind, dict(it, anim_in=alias), 0.1)
            b = _draw(kind, dict(it, anim_in=name), 0.1)
            c = _draw(kind, dict(it, anim_in=name), 1.5)
            assert np.array_equal(a, b)
            assert not np.array_equal(a, c)


def test_draw_true_with_generic_pop_still_draws_on():
    it = {"id": "gp", "name": "arrow_curve", "anim_in": "pop", "draw": True}
    early = _changed(_draw("doodle", it, 0.12))
    late = _changed(_draw("doodle", it, 1.2))
    assert early < late * 0.6


def test_schema_filled_items_render():
    schema = pytest.importorskip("motion.schema")
    plan = {"version": 2, "duration": 20.0, "fx": {
        "graphics": [{"kind": k, "start": 1.0, "end": 4.0} for k in G.RENDERERS]
        + [{"kind": "doodle", "name": n, "start": 1.0, "end": 3.0} for n in G.DOODLE_NAMES]
        + [{"kind": "card", "variant": v, "start": 1.0, "end": 3.0, "title": "T"} for v in G.CARD_VARIANTS]}}
    try:
        norm = schema.normalize(plan, fill=True)
    except Exception as e:  # el contrato de schema puede cambiar; no bloquea este modulo
        pytest.skip(f"schema.normalize no disponible con este plan: {e!r}")
    items = norm.get("fx", {}).get("graphics", [])
    assert items
    for it in items:
        fn = G.RENDERERS[it["kind"]]
        for lt in (0.0, 1.0, 2.9):
            arr, cv = _frame()
            fn(cv, it, lt, 3.0, _ctx(lt))


def test_defaults_catalog_covers_kinds():
    assert set(G.DEFAULTS) == set(G.RENDERERS)
    assert G.DEFAULTS["card"]["size"] >= 0.6


# -------------------------------------------------------------- items como los arma el director
def test_lower_third_without_texts_draws_nothing():
    arr, cv = _frame()
    ctx = _ctx()
    ctx.entities = {}
    G.draw_lower_third(cv, {"id": "lt0", "title": "", "subtitle": ""}, 1.0, 3.0, ctx)
    assert _changed(arr) == 0


def test_follow_without_name_uses_generic_label():
    arr, cv = _frame()
    ctx = _ctx()
    ctx.entities = {"name": "", "handle": ""}
    G.draw_cta(cv, {"id": "f0", "variant": "follow"}, 1.5, 3.0, ctx)
    assert _changed(arr) > 3000


def test_brand_card_ignores_schema_white_color():
    it = {"id": "br", "variant": "generic", "theme": "brand", "title": "Hola", "color": "#FFFFFF",
          "anim_in": "none", "x": 0.5, "y": 0.5, "shadow": 0}
    arr = _draw("card", it, 2.0)
    # esquina interior de la tarjeta (sin texto): debe ser el color de marca, no blanco
    th, F, u, pad, inner, rows, gap, Wc, Hc = G.card_layout(it, _ctx())
    x = int(W / 2 - Wc / 2 + pad * 0.5)
    y = int(H / 2 + Hc / 2 - pad * 0.5)
    px = arr[y, x, :3].astype(int)
    assert px.min() < 235, px


def test_director_style_items_render():
    items = [
        ("icon", {"name": "dental", "variant": "outline", "badge": "circle", "badge_color": "#2EC4B6",
                  "color": "#FFFFFF", "draw": True, "x": 0.8, "y": 0.3, "size": 0.14, "anim_in": "pop"}),
        ("doodle", {"name": "sparkle", "draw": True, "stroke": 7.0, "anim_in": "draw", "anim_out": "fade",
                    "rotation": 0.0, "x": 0.3, "y": 0.2, "size": 0.1}),
        ("doodle", {"name": "arrow_curve", "draw": True, "stroke": 7.0, "anim_in": "draw", "x": 0.2, "y": 0.6,
                    "path_to": [0.5, 0.35]}),
        ("card", {"variant": "price", "title": "Limpieza", "subtitle": "", "value": "$1,000 MXN", "icon": "cash",
                  "theme": "light", "color": "#FFFFFF", "anim_in": "rise", "anim_out": "fade", "rotation": 0.0,
                  "items": []}),
        ("cta", {"variant": "follow", "label": "", "color": "#FFD23F", "anim_in": "pop", "anim_out": "fade",
                 "rotation": 0.0}),
        ("cta", {"variant": "link_below", "label": "Agenda tu cita", "color": "#FFD23F", "anim_in": "pop"}),
        ("lower_third", {"title": "Dra. Ana Ruiz", "subtitle": "Dentista", "size": 0.056, "color": "#FFD23F",
                         "anim_in": "slide_left", "anim_out": "fade"}),
        ("sticker_text", {"text": "123", "bg": "#F7F4EE", "fg": "#0E0E12", "anim_in": "pop",
                          "anim_out": "pop_out", "rotation": -2.0, "color": "#FFFFFF"}),
    ]
    for kind, it in items:
        it = dict(it, id=f"dir_{kind}")
        for lt in (0.0, 0.4, 1.2, 2.9):
            _draw(kind, it, lt)
        assert _changed(_draw(kind, it, 1.2)) > 200, kind


def test_card_layout_contract_for_director():
    ctx = _ctx(w=1080, h=1920)
    it = {"variant": "location", "title": "Av. Central 123", "subtitle": "a 2 cuadras", "size": 0.74}
    r = G.card_layout(it, ctx)
    assert len(r) == 9
    w, h = G.card_size(it, ctx)
    assert abs(w - 0.74 * 1080) < 1 and h == r[8] and 0.1 < h / 1920 < 0.5
    # mas contenido -> mas alto
    h2 = G.card_size(dict(it, variant="price", value="$1,000", items=["a", "b", "c"]), ctx)[1]
    h3 = G.card_size(dict(it, variant="price", value="$1,000"), ctx)[1]
    assert h2 > h3


@pytest.mark.parametrize("name", ["arrow_curve", "star5", "sparkle", "lightning", "dotted_path"])
def test_sketch_style(name):
    it = {"id": f"sk_{name}", "name": name, "style": "sketch", "size": 0.3}
    brush = _changed(_draw("doodle", dict(it, style="brush"), 1.3))
    sk = _changed(_draw("doodle", it, 1.3))
    assert sk > 200
    if name not in ("dotted_path",):
        assert not np.array_equal(_draw("doodle", it, 1.3), _draw("doodle", dict(it, style="brush"), 1.3))
    assert brush > 0


# -------------------------------------------------------------- regresiones (revision critica)
def _mask(arr):
    return _diff(arr, BG)


def test_follow_director_item_takes_name_from_entities():
    ctx = _ctx()
    ctx.entities = {"name": "Dra. Ana Ruiz", "role": "Dentista", "handle": ""}
    it = {"id": "fw", "variant": "follow", "label": "Sigueme", "handle": ""}
    name, sub = G.follow_texts(it, ctx)
    assert name == "Dra. Ana Ruiz" and sub == "Dentista"
    # sin rol: 'Sigueme' pasa a subtitulo, nunca a nombre
    ctx.entities = {"name": "Dra. Ana Ruiz"}
    assert G.follow_texts(it, ctx) == ("Dra. Ana Ruiz", "Sigueme")
    # handle explicito gana y el rol no se usa (la cuenta no es la persona)
    ctx.entities = {"name": "Dra. Ana Ruiz", "role": "Dentista", "handle": "@ana.dental"}
    assert G.follow_texts(it, ctx)[0] == "@ana.dental"
    # sin ningun nombre: el label es el nombre
    ctx.entities = {}
    assert G.follow_texts({"label": "Consultorio Sol"}, ctx)[0] == "Consultorio Sol"
    arr, cv = _frame()
    ctx.entities = {"name": "Dra. Ana Ruiz", "role": "Dentista"}
    G.draw_cta(cv, dict(it, x=0.47, y=0.6, size=0.62, anim_in="pop", anim_out="fade"), 1.5, 3.0, ctx)
    assert _changed(arr) > 3000


def test_link_below_arrow_fades_with_cta():
    it = {"id": "lb", "variant": "link_below", "label": "Agenda tu cita", "x": 0.47, "y": 0.5, "size": 0.46,
          "anim_in": "pop", "anim_out": "fade"}
    mid = _mask(_draw("cta", it, 1.5, dur=3.0))
    end = _mask(_draw("cta", it, 2.98, dur=3.0))
    y0 = int(0.5 * H + 0.46 * W * 0.2 * 0.7)  # debajo de la pastilla: zona de la flecha
    assert mid[y0:].sum() > 200
    assert end[y0:].sum() < mid[y0:].sum() * 0.05


def test_lower_third_long_role_stays_inside_safe():
    it = {"id": "l3", "title": "Dra. Ana Ruiz Hernandez de la Fuente",
          "subtitle": "Cirujana dentista especialista en ortodoncia y estetica dental avanzada", "x": 0.07,
          "y": 0.6, "shadow": 0}
    for w, h in ((W, H), (1080, 1920)):
        arr = _draw("lower_third", it, 1.5, w=w, h=h)
        m = np.abs(arr.astype(np.int16) - _frame(w, h)[0].astype(np.int16)).sum(axis=2) > 12
        ys, xs = np.nonzero(m)
        # la sombra suave del panel puede asomar unos px; el panel y el texto, no
        assert xs.max() <= C.SAFE["right"] * w + 0.02 * w, (w, xs.max() / w)


def test_lower_third_honors_anim_in_out_and_rotation():
    base = {"id": "l3a", "title": "Dra. Ana Ruiz", "subtitle": "Dentista"}
    a = _draw("lower_third", dict(base, anim_in="draw"), 0.2)
    b = _draw("lower_third", dict(base, anim_in="slide_left"), 0.2)
    assert not np.array_equal(a, b)
    c = _draw("lower_third", dict(base, anim_in="none"), 0.0)
    assert _changed(c) > 1000  # sin revelacion: completo desde el cuadro 0
    d = _draw("lower_third", dict(base, anim_out="undraw"), 2.9)
    e = _draw("lower_third", dict(base, anim_out="fade"), 2.9)
    assert not np.array_equal(d, e)
    r = _draw("lower_third", dict(base, rotation=8.0), 1.5)
    assert not np.array_equal(r, _draw("lower_third", base, 1.5))


def test_wrap_every_line_fits_and_long_words_break():
    f = G.font("Poppins-Medium", 30)
    url = "https://beacons.ai/consultorio-dental-agenda-en-linea"
    for s in (url, "hola " + url, "a b c " * 40):
        for n in (1, 2, 3):
            lines = G.wrap(f, s, 220, n)
            assert 1 <= len(lines) <= n
            for ln in lines:
                assert f.measureText(ln) <= 221, (s, ln)
    lines = G.wrap(f, "uno dos tres cuatro cinco seis siete ocho nueve diez", 150, 2)
    assert lines[-1].endswith(G.ELLIPSIS)
    assert G.ellipsize(f, "corto", 500) == "corto"


def _card_rect(it, w=W, h=H):
    ctx = _ctx(w=w, h=h)
    th, F, u, pad, inner, rows, gap, Wc, Hc = G.card_layout(it, ctx)
    cx, cy = G.center_of(G.sanitize(it), ctx, G.XC, 0.62)
    cx = G.safe_cx(cx, Wc / 2, ctx)
    return cx - Wc / 2, cx + Wc / 2, Wc, Hc


def test_card_long_chips_and_url_stay_inside_card():
    it = {"id": "chips", "variant": "schedule", "title": "Horarios disponibles",
          "subtitle": "https://beacons.ai/consultorio-dental-agenda-en-linea-con-un-nombre-largo",
          "items": ["Lunes a viernes de 9:00 a 19:00 horas, sabados de 9:00 a 14:00 horas", "Domingo"],
          "x": 0.47, "y": 0.45, "anim_in": "none"}
    arr = _draw("card", it, 2.0)
    left, right, Wc, Hc = _card_rect(it)
    ys, xs = np.nonzero(_mask(arr))
    # nada del contenido pasa del borde de la tarjeta (la sombra se extiende ~30 px ref)
    assert xs.max() <= right + 0.035 * W and xs.min() >= left - 0.035 * W
    # texto oscuro (contenido) estrictamente dentro de la tarjeta
    ink = (arr[..., :3].astype(int).sum(axis=2) < 200)
    ys, xs = np.nonzero(ink)
    assert xs.max() <= right and xs.min() >= left


def test_card_and_sticker_respect_safe_horizontally():
    it = {"id": "cs", "variant": "price", "title": "Limpieza", "value": "$1,000 MXN", "x": 0.5, "size": 0.8,
          "anim_in": "none"}
    left, right, Wc, Hc = _card_rect(it)
    assert right <= C.SAFE["right"] * W + 1 and left >= C.SAFE["left"] * W - 1
    st = {"id": "st", "text": "PROMOCION DE TEMPORADA LIMITADA SOLO HOY", "anim_in": "none", "rotation": 0}
    arr = _draw("sticker_text", st, 1.0)
    ink = (np.abs(arr[..., :3].astype(int) - np.array([255, 210, 63])).sum(axis=2) < 40) | \
          (arr[..., :3].astype(int).sum(axis=2) > 740)  # pastilla amarilla + borde blanco
    ys, xs = np.nonzero(ink)
    assert xs.max() <= C.SAFE["right"] * W + 2 and xs.min() >= C.SAFE["left"] * W - 2


def test_map_cache_key_follows_accent():
    it = {"id": "mapk", "variant": "location", "title": "Av. Central 123", "anim_in": "none"}
    a = _draw("card", dict(it, accent="#FF3366"), 2.0)
    _draw("card", dict(it, accent="#22AAFF"), 2.0)
    b = _draw("card", dict(it, accent="#FF3366"), 2.0)
    G._PIC_CACHE.clear()
    c = _draw("card", dict(it, accent="#FF3366"), 2.0)
    assert np.array_equal(a, b) and np.array_equal(b, c)
    G._PIC_CACHE.clear()
    d = _draw("card", dict(it, accent="#22AAFF"), 2.0)
    G._PIC_CACHE.clear()
    e = _draw("card", dict(it, accent="#FF3366"), 2.0)
    assert np.array_equal(e, a) and not np.array_equal(d, a)


def test_sketch_merges_overlapping_brush_polygons(monkeypatch):
    # el contorno sketch se calcula sobre la UNION de los poligonos del pincel (skia.Simplify):
    # un solo contorno, sin aristas internas en cruces ni en la union punta-cuerpo
    import skia
    raw = skia.Path()
    segs_a = [np.array([[-120.0, -120.0], [0.0, 0.0], [120.0, 120.0]])]
    segs_b = [np.array([[120.0, -120.0], [0.0, 0.0], [-120.0, 120.0]])]
    G.brush(raw, [G._catmull(s, 8) for s in segs_a], 40.0)
    G.brush(raw, [G._catmull(s, 8) for s in segs_b], 40.0)
    merged = skia.Simplify(raw)
    assert merged is not None and merged.countVerbs() < raw.countVerbs()
    ctx = _ctx()

    def paint(p):
        arr, cv = _frame(400, 400, "#101010")
        cv.translate(200, 200)
        G._draw_sketch(cv, p, C.color("#FFFFFF"), 1.0, 0.0, 3, ctx)
        return arr.copy()
    calls = []
    real = skia.Simplify
    monkeypatch.setattr(skia, "Simplify", lambda p: calls.append(1) or real(p))
    a = paint(raw)
    assert calls, "el sketch debe fusionar el path antes de trazar el contorno"
    monkeypatch.setattr(skia, "Simplify", real)
    b = paint(merged)
    # mismo resultado salvo el ruido del DiscretePathEffect (depende del inicio del contorno)
    assert (np.abs(a.astype(int) - b.astype(int)).sum(axis=2) > 60).mean() < 0.03
    # el centro del cruce no tiene trazo de contorno: solo rayado (mucho menos cubierto)
    ring = a[200 - 12:200 + 12, 200 - 12:200 + 12, :3].astype(int).sum(axis=2) > 500
    assert ring.mean() < 0.45


def test_badge_icon_waits_for_badge_to_settle():
    it = {"id": "bi", "name": "dental", "badge": "circle", "badge_color": "#FFD23F", "size": 0.3, "x": 0.5,
          "y": 0.5, "anim_out": "none"}
    t85 = G._spring_reach(0.85, 2.9, 0.42)
    assert 0.05 < t85 < 0.4

    def dark_px(lt):
        arr = _draw("icon", it, lt, bg="#6E7B64")
        a = arr[..., :3].astype(int)
        return int(((a.sum(axis=2) < 120)).sum())
    assert dark_px(t85 * 0.8) == 0            # badge rebotando: sin trazos sueltos del icono
    assert dark_px(t85 + 0.05) > 30           # arranca con q minimo (un trazo legible, no puntos)
    assert dark_px(1.2) > 800                 # icono completo


def test_card_content_visible_from_first_frames():
    it = {"id": "cf", "variant": "price", "title": "Limpieza dental", "value": "$1,000 MXN", "x": 0.47,
          "y": 0.5, "anim_in": "slide_up"}
    arr = _draw("card", it, 1 / 30)
    ink = (arr[..., :3].astype(int).sum(axis=2) < 600)  # texto oscuro (aun translucido) sobre blanco
    ys, xs = np.nonzero(ink)
    assert ink.sum() > 50  # el titulo ya asoma en el cuadro 1 (no hay contenedor vacio)


def test_price_suffix_does_not_jump_while_counting():
    it = {"id": "pr", "variant": "price", "theme": "dark", "title": "Precio", "value": "$1,000 MXN", "x": 0.47,
          "y": 0.5, "anim_in": "none", "size": 0.74}

    def right_edge(lt):
        arr = _draw("card", it, lt)
        a = arr[..., :3].astype(int)
        bright = np.abs(a - np.array([22, 22, 28])).sum(axis=2) > 90
        left, right, Wc, Hc = _card_rect(it)
        bright[:, :int(left + 2)] = False
        bright[:, int(right - 2):] = False
        ys, xs = np.nonzero(bright)
        return xs.max()
    counting, final = right_edge(0.36), right_edge(1.6)
    assert abs(counting - final) <= 2, (counting, final)
    pre, val, suf, dec, sep = G.parse_amount("Desde $1,000")
    assert pre == "Desde $" and val == 1000.0
    _draw("card", dict(it, value="Desde $1,000 MXN", theme="light"), 1.0)


def test_anchor_without_any_face_draws_nothing_and_uses_face0():
    ctx = _ctx()
    ctx.face = None
    assert G.center_of({"anchor": "face"}, ctx) is None
    arr = _draw("doodle", {"id": "an", "name": "circle_scribble", "anchor": "face"}, 1.0, face=None)
    assert _changed(arr) == 0
    x, y = G.center_of({"anchor": "face", "_face0": (0.3, 0.3, 0.2, 0.12)}, ctx)
    assert abs(x / W - 0.3) < 0.01 and abs(y / H - 0.3) < 0.01
    # con cara en el cuadro, la del cuadro manda
    ctx.face = (0.6, 0.4, 0.2, 0.12)
    x, _ = G.center_of({"anchor": "face", "_face0": (0.3, 0.3, 0.2, 0.12)}, ctx)
    assert abs(x / W - 0.6) < 0.01
    # x/y explicitos no dependen de la cara
    ctx.face = None
    assert G.center_of({"anchor": "face", "x": 0.2, "y": 0.2}, ctx) is not None


def test_progress_ignores_common_y_and_uses_bar_y():
    arr = _draw("progress", {"id": "pg", "y": 0.3, "color": "#FFFFFF"}, 1.5, dur=3.0)
    ys, _ = np.nonzero(_mask(arr))
    assert ys.max() < 0.05 * H
    arr = _draw("progress", {"id": "pg", "bar_y": 0.5}, 1.5, dur=3.0)
    ys, _ = np.nonzero(_mask(arr))
    assert abs(np.median(ys) / H - 0.5) < 0.03
    a = _draw("progress", {"id": "pg", "anim_out": "fade"}, 2.999, dur=3.0)
    assert _changed(a) < _changed(_draw("progress", {"id": "pg"}, 2.999, dur=3.0)) * 0.1


def test_generic_schema_sizes_stay_legible():
    # size=0.18 (default generico del esquema) no deja un CTA/tarjeta microscopicos
    for kind, it in (("cta", {"variant": "follow", "label": "Hola"}), ("cta", {"variant": "save"}),
                     ("card", {"variant": "price", "value": "$1,000"})):
        arr = _draw(kind, dict(it, id="leg", size=0.18, x=0.47, y=0.5, anim_in="none"), 1.5)
        ys, xs = np.nonzero(_mask(arr))
        assert (xs.max() - xs.min()) / W > 0.4, kind


def test_defaults_are_adoptable_by_schema():
    assert G.DEFAULTS["progress"]["y"] == 0.0 and G.DEFAULTS["progress"]["bar_y"] == 0.0
    assert G.DEFAULTS["cta"]["size"] >= 0.45
    assert G.DEFAULTS["doodle"]["stroke"] >= 11
    schema = pytest.importorskip("motion.schema")
    try:
        norm = schema.normalize({"version": 2, "duration": 10.0, "fx": {"graphics": [
            {"kind": "cta", "variant": "follow", "start": 1.0, "end": 3.0},
            {"kind": "progress", "start": 0.0, "end": 10.0}]}}, fill=True)
    except Exception as e:  # el contrato de schema puede cambiar; no bloquea este modulo
        pytest.skip(f"schema.normalize no disponible: {e!r}")
    for it in norm["fx"]["graphics"]:
        arr = _draw(it["kind"], it, 1.5)
        ys, xs = np.nonzero(_mask(arr))
        if it["kind"] == "progress":
            assert ys.max() < 0.05 * H
        else:
            assert (xs.max() - xs.min()) / W > 0.4


def test_icon_filled_uses_resolved_name_and_public_api():
    assert G.has_filled("map-pin") and not G.has_filled("dental")
    assert G.resolve_icon("no-existe-este-icono-xyz") in ("star",) or G.icon_paths("no-existe-este-icono-xyz")
    a = _draw("icon", {"id": "fi", "name": "map-pin", "variant": "filled", "anim_in": "none", "shadow": 0}, 1.5)
    b = _draw("icon", {"id": "fi", "name": "map-pin", "variant": "outline", "anim_in": "none", "shadow": 0}, 1.5)
    assert _changed(a) > _changed(b)


def test_share_cta_never_leaves_empty_circle_for_long():
    it = {"id": "sh", "variant": "share", "x": 0.47, "y": 0.5, "anim_in": "none", "anim_out": "none"}
    ctx = _ctx()
    lab, fb, h, ib, w = G._pill_layout(G.sanitize(it), G._CTA_LABEL["share"], ctx)
    bx = int(G.safe_cx(0.47 * W, w / 2, ctx) - w / 2 + h * 0.14 + ib / 2)
    by = int(0.5 * H)
    empty = 0
    for i in range(60):
        lt = 0.6 + i / 30
        arr = _draw("cta", it, lt, dur=10.0)
        patch = arr[by - int(ib * 0.3):by + int(ib * 0.3), bx - int(ib * 0.3):bx + int(ib * 0.3), :3].astype(int)
        if (patch.sum(axis=2) < 200).sum() < 3:
            empty += 1
    assert empty <= 4, empty  # a lo mas ~0.13 s sin avion en el circulo por ciclo


# -------------------------------------------------------------- ola 2
def test_location_card_compact_without_map():
    """`map: false`: tarjeta de ubicacion compacta (pin + direccion) cuando no hay b-roll."""
    base = {"id": "loc", "kind": "card", "variant": "location", "title": "Av. Central 123",
            "subtitle": "a 2 cuadras de la Glorieta Norte", "size": 0.74}
    ctx = _ctx(1.0)
    h_full = G.card_size(dict(base), ctx)[1]
    h_compact = G.card_size(dict(base, map=False), ctx)[1]
    assert h_compact < 0.6 * h_full
    a = _draw("card", dict(base, map=False), 1.2)
    assert _diff(a, BG).mean() > 0.001


def test_link_below_arrow_scale_shortens_arrow():
    """`arrow_scale` acorta la flecha del link (para no pisar los subtitulos); 0 la quita."""
    it = {"id": "lk", "kind": "cta", "variant": "link_below", "label": "Agenda tu cita", "x": 0.47,
          "y": 0.45, "size": 0.62, "anim_out": "none"}

    def bottom(img):
        base = _frame()[0]
        d = np.abs(img[..., :3].astype(int) - base[..., :3].astype(int)).sum(-1) > 30
        ys = np.nonzero(d.any(1))[0]
        return ys.max() if len(ys) else 0
    full = bottom(_draw("cta", dict(it), 1.5))
    short = bottom(_draw("cta", dict(it, arrow_scale=0.5), 1.5))
    none = bottom(_draw("cta", dict(it, arrow_scale=0.0), 1.5))
    assert none < short < full


def test_gradients_are_dithered():
    """Los degradados de fondos/tarjetas se dibujan con dither (sin anillos de 8 bits)."""
    import inspect
    src = inspect.getsource(G)
    assert src.count("setShader(skia.GradientShader") == src.count("setDither(True)")
