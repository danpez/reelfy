"""Regla de simetria (dueno del producto): TODO overlay que Reelfy agrega deja el mismo aire a la
izquierda y a la derecha de su tinta visible (centrado en x = 0.5). Cada item se dibuja SOLO sobre
un cuadro transparente de 1080x1920 (ctx.demo_ctx) en reposo y se mide la columna de tinta
(alpha > 24, como el QA de simetria): |margen izq - margen der| <= 0.5 % del ancho.

Contenido 100 % sintetico (el repo es publico: nada de datos de clientes)."""
import math

import numpy as np
import pytest

from motion import ctx as C
from motion import cutaways as CU
from motion import graphics as G
from motion import text as T

W, H = 1080, 1920
TOL = 0.005 * W
DUR = 3.0
REST = 1.95                        # min(dur*0.65, dur-0.35): ya entro, aun no sale
ENT = {"name": "Ana Demo", "role": "Especialista"}
LINKS = [{"platform": "instagram", "text": "@estudio.demo"}, {"platform": "tiktok", "text": "@estudio.demo"},
         {"platform": "whatsapp", "text": "55 0000 0000"}]


def _ctx(t=REST):
    return C.demo_ctx(W, H, t=t, entities=dict(ENT))


def _render(fn, item, lt=REST, dur=DUR):
    arr, surf = C.new_frame(W, H)
    fn(surf.getCanvas(), item, lt, dur, _ctx(lt))
    return arr


def _cols(arr):
    cols = np.where(arr[..., 3].max(axis=0) > 24)[0]
    assert len(cols), "no dibujo nada"
    return int(cols[0]), W - (int(cols[-1]) + 1)


def _margins(fn, item, lt=REST, dur=DUR):
    return _cols(_render(fn, item, lt, dur))


def _assert_sym(fn, item, lt=REST, dur=DUR, tol=TOL):
    L, R = _margins(fn, item, lt, dur)
    assert abs(L - R) <= tol, f"{item.get('kind') or item.get('preset')}: izq {L} der {R} (dif {R - L:+d} px)"
    return L, R


# ------------------------------------------------------------------ contrato de constantes
def test_constants_are_symmetric():
    S = C.SAFE
    assert abs(S["left"] - (1 - S["right"])) < 1e-9 and C.XC == 0.5
    assert abs(C.SYM_W - (S["right"] - S["left"])) < 1e-9 and abs(C.SYM_W - 0.76) < 1e-9
    assert G.XC == C.XC and G.SAFE_W == C.SYM_W == G.SYM_W and CU.XC == C.XC


def test_defaults_are_centered():
    for kind in ("doodle", "icon", "card", "cta", "sticker_text", "socials", "media"):
        assert G.DEFAULTS[kind]["x"] == 0.5, kind
    assert G.DEFAULTS["lower_third"]["x"] == 0.5 and G.DEFAULTS["lower_third"]["align"] == "center"
    for k in ("card", "follow", "cta"):
        assert G.SIZE_RANGE[k][1] <= C.SYM_W + 1e-9, k
    assert T.PRESETS["cascade"]["align"] == "center"


def test_safe_cx_never_pushes_sideways():
    ctx = _ctx()
    # centrado y dentro de la banda: no se mueve
    assert G.safe_cx(0.5 * W, 0.38 * W, ctx) == pytest.approx(0.5 * W)
    # demasiado ancho: se centra en el cuadro (antes: 0.47)
    assert G.safe_cx(0.3 * W, 0.45 * W, ctx) == pytest.approx(0.5 * W)
    # acotado simetrico: el mismo margen a cada lado
    lo, hi = G.safe_cx(0.0, 0.1 * W, ctx), G.safe_cx(W, 0.1 * W, ctx)
    assert lo - 0.1 * W == pytest.approx(W - (hi + 0.1 * W))


# ------------------------------------------------------------------ graficos
@pytest.mark.parametrize("name", G.DOODLE_NAMES)
def test_doodle_ink_centered(name):
    _assert_sym(G.draw_doodle, {"id": f"sym_{name}", "kind": "doodle", "name": name, "x": 0.5, "y": 0.45})


@pytest.mark.parametrize("name", ["sparkle", "swirl", "circle_scribble", "burst"])
def test_doodle_unit_geometry_recentered(name):
    """La geometria unitaria se centra en el punto medio de su extension x (antes 11-20 px)."""
    ctx = _ctx()
    elems, *_ = G._doodle_geometry(name, {"id": "u"}, ctx, 100.0, 0.0, 0.0)
    xs = [v for e in elems for v in G._elem_xs(e)]
    assert abs((min(xs) + max(xs)) / 2) < 1e-6


def test_doodle_default_x_is_center():
    _assert_sym(G.draw_doodle, {"id": "dflt", "kind": "doodle", "name": "star4", "y": 0.45})


@pytest.mark.parametrize("name", G.DOODLE_SYM)
def test_doodle_mirrored_pair_is_exact_mirror(name):
    """Par espejo del director: mismo glifo, x y 1-x, rotacion -rot, ids '<id>' y '<id>_m'. El
    gemelo se dibuja reflejado: el par es simetrico y el gemelo es el espejo del original."""
    a = {"id": "acc", "kind": "doodle", "name": name, "x": 0.3, "y": 0.4, "size": 0.12, "rotation": 12.0}
    b = dict(a, id="acc_m", x=0.7, rotation=-12.0)
    ra, rb = _render(G.draw_doodle, a), _render(G.draw_doodle, b)
    La, Ra = _cols(ra)
    Lb, Rb = _cols(rb)
    assert abs(La - Rb) <= 2 and abs(Ra - Lb) <= 2, (La, Ra, Lb, Rb)
    # el gemelo es (casi pixel a pixel) el reflejo del original
    alpha_a = ra[..., 3].astype(np.int16)
    alpha_b = rb[..., 3].astype(np.int16)[:, ::-1]
    on = (alpha_a > 24) | (alpha_b > 24)
    assert np.mean(np.abs(alpha_a - alpha_b)[on]) < 12


def test_doodle_twin_of_handed_glyph_is_not_flipped():
    """Un glifo con mano (signo de pregunta) nunca se refleja (se leeria al reves)."""
    a = {"id": "q", "kind": "doodle", "name": "question", "x": 0.5, "y": 0.4}
    ra, rb = _render(G.draw_doodle, a), _render(G.draw_doodle, dict(a, id="q_m"))
    fa = ra[..., 3].astype(np.int16)
    assert np.mean(np.abs(fa - rb[..., 3].astype(np.int16))) < np.mean(np.abs(fa - rb[..., 3][:, ::-1].astype(np.int16)))


def test_doodle_ink_centered_while_it_rests_with_boil():
    """Con boil (la geometria tiembla a 12 fps) la tinta sigue centrada cuadro a cuadro."""
    it = {"id": "boil", "kind": "doodle", "name": "question", "x": 0.5, "y": 0.45, "wiggle": 1.0}
    for lt in (1.0, 1.37, 1.62, 2.2):
        _assert_sym(G.draw_doodle, it, lt=lt)


@pytest.mark.parametrize("name", ["arrow_curve", "arrow_straight", "arrow_down", "dotted_path"])
@pytest.mark.parametrize("y0,y1", [(0.3, 0.5), (0.72, 0.56)])
def test_vertical_pointing_arrow_centered(name, y0, y1):
    """Flecha que apunta a una tarjeta/CTA centrada: vertical en x = path_to.x = 0.5, bend 0."""
    it = {"id": "ar", "kind": "doodle", "name": name, "x": 0.5, "y": y0, "path_to": [0.5, y1], "bend": 0}
    _assert_sym(G.draw_doodle, it)
    _assert_sym(G.draw_doodle, it, lt=2.4)


@pytest.mark.parametrize("name", ["arrow_straight", "arrow_curve"])
def test_short_pointing_arrow_has_a_readable_head(name):
    """Flecha corta (~0.08 H) que apunta a una tarjeta: la cabeza tiene ala >= ~2.8 trazos (en px,
    no proporcional al largo) -> se lee como flecha y no como una raya; sigue centrada."""
    it = {"id": "ar", "kind": "doodle", "name": name, "x": 0.5, "y": 0.66, "path_to": [0.5, 0.58], "bend": 0}
    L, R = _assert_sym(G.draw_doodle, it)
    assert W - L - R >= 2.5 * 12.0, f"cabeza de {W - L - R} px"


def test_diagonal_arrow_still_goes_tail_to_tip():
    """Una flecha diagonal (plan viejo o del usuario) no se recentra: va de (x, y) a path_to."""
    it = {"id": "dg", "kind": "doodle", "name": "arrow_straight", "x": 0.25, "y": 0.3, "path_to": [0.6, 0.5]}
    L, R = _margins(G.draw_doodle, it)
    assert abs(L - 0.25 * W) < 0.04 * W and abs((W - R) - 0.6 * W) < 0.05 * W


def test_doodle_with_absurd_size_does_not_crash():
    _render(G.draw_doodle, {"id": "big", "kind": "doodle", "name": "sparkle", "x": 0.5, "y": 0.5, "size": 1e9})


@pytest.mark.parametrize("name", ["star", "heart", "map-pin", "phone", "calendar-event", "brand-instagram", "dental"])
@pytest.mark.parametrize("badge", ["none", "circle"])
def test_icon_ink_centered(name, badge):
    _assert_sym(G.draw_icon, {"id": f"ic_{name}", "kind": "icon", "name": name, "badge": badge, "x": 0.5, "y": 0.4})


def test_icon_glyph_offset_compensated():
    assert abs(G.icon_ink_dx("phone")) > 0.2           # el glifo phone no esta centrado en su caja
    assert abs(G.icon_ink_dx("no-existe-xyz")) < 12


@pytest.mark.parametrize("variant", G.CARD_VARIANTS)
def test_card_centered(variant):
    _assert_sym(G.draw_card, {"id": f"c_{variant}", "kind": "card", "variant": variant, "title": "Servicio demo",
                              "subtitle": "a dos cuadras del parque", "value": "$1,000 MXN", "items": ["Uno", "Dos"],
                              "label": "Agenda aqui", "x": 0.5, "y": 0.55})


def test_wide_card_is_capped_and_centered():
    L, R = _assert_sym(G.draw_card, {"id": "cw", "kind": "card", "variant": "price", "title": "Precio",
                                     "value": "$900", "size": 0.9, "y": 0.55})
    assert L >= C.SAFE["left"] * W - 0.03 * W        # tope SYM_W (la sombra asoma unos px)


@pytest.mark.parametrize("variant", G.CTA_VARIANTS)
def test_cta_centered(variant):
    _assert_sym(G.draw_cta, {"id": f"t_{variant}", "kind": "cta", "variant": variant, "y": 0.6})


@pytest.mark.parametrize("extra", [
    {"variant": "follow", "title": "Estudio Demo Sonrisas", "platform": "instagram", "handle": "estudio.demo"},
    {"variant": "follow", "width": 0.9},
    {"variant": "link_below", "label": "Agenda tu cita", "display": "demo.example/cita"},
    {"variant": "link_below", "width": 0.8},
])
def test_cta_profile_and_wide_centered(extra):
    _assert_sym(G.draw_cta, dict({"id": "cp", "kind": "cta", "x": 0.5, "y": 0.6}, **extra))


def _rgba(w, h, rgb, alpha=255):
    img = np.zeros((h, w, 4), np.uint8)
    img[..., :3] = rgb
    img[..., 3] = alpha
    return img


def _logo_tr():
    """Logo transparente, descentrado a proposito en su lienzo (la caja de tinta lo recentra)."""
    img = np.zeros((256, 256, 4), np.uint8)
    img[40:150, 20:200] = (200, 30, 180, 255)
    return img


PHOTO = _rgba(400, 300, (40, 200, 90))
APP = _rgba(180, 180, (255, 120, 0))


@pytest.mark.parametrize("extra", [
    # Perfil: foto del lugar en el hueco + logo en su esquina, solo logo, foto sin display, ancho comun
    {"label": "Agenda en ReservaDemo", "display": "reserva.demo/ana", "avatar": "image", "_media_image": PHOTO,
     "_logo_image": _logo_tr()},
    {"label": "Agenda en ReservaDemo", "display": "reserva.demo/ana", "_logo_image": APP},
    {"label": "Agenda en ReservaDemo", "avatar": "image", "_media_image": PHOTO, "_logo_image": APP},
    {"label": "Agenda tu cita con la especialista demo de siempre", "display": "reserva.demo/una-ruta-bastante-larga",
     "avatar": "image", "_media_image": PHOTO},
    {"label": "Agenda", "display": "reserva.demo/ana", "_logo_image": _logo_tr(), "width": 0.74},
])
def test_link_below_with_photo_and_logo_centered(extra):
    it = dict({"id": "lp", "kind": "cta", "variant": "link_below", "x": 0.5, "y": 0.6}, **extra)
    L, R = _assert_sym(G.draw_cta, it)
    assert L >= C.SAFE["left"] * W - 0.03 * W                   # cabe en SYM_W (sombra aparte)


def test_follow_with_logo_badge_centered():
    _assert_sym(G.draw_cta, {"id": "fl", "kind": "cta", "variant": "follow", "title": "Estudio Demo", "handle": "estudio.demo",
                             "platform": "web", "avatar": "image", "_media_image": PHOTO, "_logo_image": APP,
                             "x": 0.5, "y": 0.6})


@pytest.mark.parametrize("variant", ["schedule", "booking", "link", "generic"])
def test_card_with_logo_centered(variant):
    _assert_sym(G.draw_card, {"id": f"cl_{variant}", "kind": "card", "variant": variant, "title": "Horarios disponibles",
                              "subtitle": "En ReservaDemo", "label": "Agendar", "x": 0.5, "y": 0.55,
                              "_logo_image": _logo_tr()})


@pytest.mark.parametrize("layout", ["column", "row", "grid"])
def test_socials_with_logo_centered(layout):
    links = LINKS + [{"platform": "web", "text": "reserva.demo/ana", "_logo_image": APP}]
    _assert_sym(G.draw_socials, {"id": f"sl_{layout}", "kind": "socials", "layout": layout, "links": links, "y": 0.55})


@pytest.mark.parametrize("layout", ["column", "row", "grid"])
def test_socials_centered(layout):
    _assert_sym(G.draw_socials, {"id": f"s_{layout}", "kind": "socials", "layout": layout, "links": LINKS,
                                 "y": 0.55})


def test_socials_watermark_top_center():
    _assert_sym(G.draw_socials, {"id": "wm", "kind": "socials", "variant": "watermark", "links": LINKS[:1],
                                 "x": 0.5, "y": 0.13})


@pytest.mark.parametrize("txt", ["OJO", "Esto cambia todo", "Una frase bastante larga para un sticker de prueba"])
def test_sticker_centered(txt):
    _assert_sym(G.draw_sticker_text, {"id": "st", "kind": "sticker_text", "text": txt, "y": 0.35})


@pytest.mark.parametrize("size", [0.45, 0.76, 0.9])
def test_media_centered(size):
    img = np.zeros((400, 600, 4), np.uint8)
    img[..., 3] = 255
    img[..., 0] = 200
    _assert_sym(G.draw_media, {"id": f"m{size}", "kind": "media", "size": size, "x": 0.5, "y": 0.4,
                               "_media_image": img})


def test_progress_track_is_full_width():
    L, R = _margins(G.draw_progress, {"id": "pg", "kind": "progress"})
    assert L == R == 0


# ------------------------------------------------------------------ lower third
LT = {"id": "lt", "kind": "lower_third", "title": "Ana Demo", "subtitle": "Especialista en sonrisas", "y": 0.7}


def test_lower_third_defaults_centered():
    it = dict(G.DEFAULTS["lower_third"], **LT)
    _assert_sym(G.draw_lower_third, it)
    assert G.lower_third_align(it) == "center"
    assert G.lower_third_align({"x": 0.07}) == "left" and G.lower_third_align({"align": "left"}) == "left"


def test_lower_third_long_text_fits_sym_width():
    it = dict(LT, x=0.5, align="center", title="Nombre Demo Muy Largo Para Probar El Ancho Del Rotulo",
              subtitle="Una especialidad con un nombre bastante largo para el rotulo")
    L, R = _assert_sym(G.draw_lower_third, it)
    assert L >= C.SAFE["left"] * W - 0.03 * W           # el panel cabe en SYM_W (sombra aparte)


def test_lower_third_centered_reveal_grows_from_center():
    """align center: el panel se despliega DESDE EL CENTRO -> simetrico en cada cuadro de la
    entrada y de la salida (antes crecia desde la barra izquierda: +370 px a media entrada)."""
    it = dict(LT, x=0.5, align="center", shadow=0)
    widths = []
    for lt in (0.02, 0.05, 0.1, 0.16, 0.24, 0.32, 0.45, 0.7, 1.2):
        L, R = _assert_sym(G.draw_lower_third, it, lt=lt)
        widths.append(W - L - R)
    assert widths[0] < widths[-1] * 0.6                 # empieza angosto (barra que crece)...
    assert all(b >= a - 2 for a, b in zip(widths, widths[1:]))  # ...y solo se ensancha
    for lt in (2.65, 2.75, 2.85):                       # salida (undraw): se pliega al centro
        _assert_sym(G.draw_lower_third, it, lt=lt)


def test_lower_third_centered_accent_bar_at_bottom_center():
    """El acento es una barra horizontal (alto px(8)) en el borde INFERIOR del panel, centrada."""
    it = dict(LT, x=0.5, align="center", theme="light", color="#FF0000")  # tema claro: rol en rojo oscuro
    arr = _render(G.draw_lower_third, it)
    red = (arr[..., 0] > 200) & (arr[..., 1] < 60) & (arr[..., 2] < 60) & (arr[..., 3] > 200)
    ys, xs = np.nonzero(red)
    assert len(xs) > 200
    assert abs((xs.min() + xs.max() + 1) / 2 - W / 2) <= 2
    ink_rows = np.nonzero(arr[..., 3].max(axis=1) > 200)[0]
    assert ys.max() >= ink_rows.max() - 2                 # pegada al borde inferior del panel
    assert ys.max() - ys.min() <= 10                      # barra delgada (8 px de referencia)


def test_lower_third_legacy_left_unchanged():
    """Sin `align` (planes viejos) x sigue siendo el BORDE IZQUIERDO y crece hacia la derecha."""
    it = dict(LT, x=0.07, shadow=0)
    L, R = _margins(G.draw_lower_third, it)
    assert abs(L - 0.07 * W) <= 3 and R > L + 100
    ls = [_margins(G.draw_lower_third, it, lt=lt) for lt in (0.2, 0.35, 1.2)]
    assert all(abs(l - ls[0][0]) <= 3 for l, _ in ls)    # el borde izquierdo no se mueve...
    assert ls[0][1] > ls[-1][1]                           # ...el panel crece a la derecha
    assert _margins(G.draw_lower_third, dict(it, align="left")) == (L, R)


# ------------------------------------------------------------------ texto
TXT = {"title_pop": "Tu sonrisa merece lo mejor", "stack": "lo que nadie\nte dice del\ncafe",
       "cascade": "para llevar\nla edicion\nde video", "serif_elegant": "Un detalle elegante",
       "script_accent": "con estilo propio", "behind_giant": "WOW", "highlight_marker": "Cuesta solo\n800 pesos",
       "typewriter": "escribiendo algo", "kicker": "tres errores comunes"}


def _text_item(preset, **kw):
    it = {"id": f"tx_{preset}", "preset": preset, "text": TXT.get(preset, "Texto de prueba")}
    if preset == "counter":
        it.update(text="", value=1500, prefix="$", suffix=" MXN")
    if preset == "kicker":
        it["label"] = "TIP"
    it.update(kw)
    return it


@pytest.mark.parametrize("preset", list(T.PRESETS))
def test_text_preset_centered(preset):
    _assert_sym(T.draw, _text_item(preset))


@pytest.mark.parametrize("preset", ["title_pop", "stack", "cascade", "highlight_marker", "kicker", "serif_elegant"])
@pytest.mark.parametrize("align", ["left", "right"])
def test_text_align_only_aligns_lines_inside_block(preset, align):
    """align left/right alinea las lineas DENTRO del bloque; el bloque sigue centrado en x=0.5."""
    it = _text_item(preset, align=align)
    P = T._params(it, preset, _ctx())
    assert P["x"] == 0.5
    _assert_sym(T.draw, it)


def test_text_usable_width_symmetric_for_every_align():
    ctx = _ctx()
    ws = {a: T._usable({"x": 0.5, "align": a}, ctx)[0] for a in ("left", "center", "right")}
    assert ws["left"] == ws["center"] == ws["right"] == pytest.approx(C.SYM_W * W)


@pytest.mark.parametrize("rot", [-6.0, 4.0, 9.0])
def test_rotated_text_block_centered_by_rotated_ink(rot):
    _assert_sym(T.draw, _text_item("stack", rotation=rot))


def test_long_text_shrinks_and_stays_centered():
    it = _text_item("title_pop", text="Una buena rutina mejora muchisimo tu energia tu sueno y tambien tu animo diario")
    L, R = _assert_sym(T.draw, it)
    assert L >= C.SAFE["left"] * W - 0.02 * W


def test_counter_center_default_symmetric_while_counting():
    it = _text_item("counter")
    assert "anchor" not in it
    for lt in (0.2, 0.4, 0.6, 1.0, REST):
        _assert_sym(T.draw, it, lt=lt, tol=TOL + 2)


def test_cascade_keeps_its_staircase_but_block_is_centered():
    it = _text_item("cascade")
    P, B = T._get_block(it, "cascade", _ctx())
    assert B.lines[-1].ink[0] > B.lines[0].ink[0] + 5      # la escalera sigue (interna)
    _assert_sym(T.draw, it)


# ------------------------------------------------------------------ escenas (cutaways)
@pytest.fixture
def no_bg(monkeypatch):
    """Fondo de escena transparente: se mide solo lo que la escena pone encima."""
    monkeypatch.setattr(CU, "draw_background", lambda *a, **k: None)


def _photo_img():
    img = np.zeros((500, 400, 4), np.uint8)
    img[..., 3] = 255
    img[..., 1] = 180
    return img


@pytest.mark.parametrize("kind,extra", [
    ("title_card", {"text": "Hola que tal"}),
    ("title_card", {"text": {"preset": "stack", "text": "tres cosas\nque debes\nsaber"}}),
    ("info_card", {"text": "Precio", "card": {"variant": "price", "title": "Servicio", "value": "$900"}}),
    ("info_card", {"variant": "schedule", "title": "Horario", "subtitle": "Lun a Vie"}),
    ("info_card", {"text": "Tarjeta embebida", "graphics": [{"kind": "card", "variant": "location",
                                                             "title": "Centro", "x": 0.5, "y": 0.55}]}),
    ("photo", {"caption": "Recuerdo"}),
    ("photo", {"caption": "Sin giro", "tilt": 0}),
    ("self_broll", {"text": "Mira esto"}),
])
def test_cutaway_content_centered(no_bg, kind, extra):
    it = dict({"id": f"cw_{kind}", "kind": kind}, **extra)
    if kind == "photo":
        it["_media_image"] = _photo_img()
    _assert_sym(CU.RENDERERS[kind], it)


def test_cutaway_own_card_defaults(no_bg):
    c = CU._card_from({"variant": "price", "title": "Servicio", "value": "$900"})
    assert c["x"] == 0.5 and c["size"] == pytest.approx(C.SYM_W)


def test_photo_honors_item_x(no_bg):
    it = {"id": "ph", "kind": "photo", "tilt": 0, "_media_image": _photo_img()}
    L0, R0 = _margins(CU.draw_photo, it)
    L1, R1 = _margins(CU.draw_photo, dict(it, x=0.4))
    assert abs(L0 - R0) <= 2 and L1 < L0 - 0.08 * W and R1 > R0 + 0.08 * W


def test_fallback_title_centered():
    it = {"text": "Una frase de escena bastante larga para el titulo", "size": 0.1}
    _assert_sym(CU.fallback_title, it)
    L, R = _margins(CU.fallback_title, dict(it, x=0.5))
    assert abs(L - R) <= TOL and L >= C.SAFE["left"] * W - 2


def test_scene_text_default_x_center():
    ti = CU._text_item({"text": "Hola"}, True, _ctx())
    assert ti["x"] == 0.5
    assert math.isclose(CU._card_from({})["x"], 0.5)
