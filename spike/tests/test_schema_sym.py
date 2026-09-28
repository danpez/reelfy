"""Regla de SIMETRIA de la marca en el catalogo (motion.schema): todo lo que el usuario agrega en
el Studio nace centrado en x = 0.5 (el esquema es la fuente de los defaults del inspector y de
fxcore.newItem), los anchos comunes no pasan de SYM_W (0.76, lo maximo que cabe centrado en la
zona segura) y el rotulo tiene alineacion centrada por defecto sin romper los planes viejos.
Datos ficticios."""
import copy
import importlib

import pytest

from motion import schema as S

CENTERED_GRAPHICS = ("doodle", "icon", "card", "lower_third", "cta", "sticker_text", "socials")


def _defaults(track, kind):
    return {p["k"]: p for p in S.item_params(track, kind)}


def test_constantes_espejo_de_ctx():
    ctx = importlib.import_module("motion.ctx")
    assert S.XC == ctx.XC == 0.5
    assert abs(S.SYM_W - ctx.SYM_W) < 1e-9
    assert abs(ctx.SAFE["left"] - (1 - ctx.SAFE["right"])) < 1e-9       # zona segura simetrica


def test_graficos_nacen_centrados():
    for kind in CENTERED_GRAPHICS:
        ps = _defaults("graphics", kind)
        assert ps["x"]["default"] == 0.5, (kind, ps["x"]["default"])
    # el PiP sigue sin posicion (colocacion automatica) y la barra no usa x
    assert _defaults("graphics", "media")["x"]["default"] is None
    assert "x" not in _defaults("graphics", "progress")


def test_rotulo_alineacion_centrada_por_defecto():
    al = _defaults("graphics", "lower_third")["align"]
    assert al["default"] == "center" and set(al["opts"]) == {"center", "left"}
    assert al["type"] == "seg" and al.get("nofill") is True and al.get("hint")


def test_titulos_nacen_centrados_y_el_contador_crece_desde_el_centro():
    for preset in S.TEXT_PRESETS:
        ps = _defaults("text", preset)
        assert ps["x"]["default"] == 0.5 and ps["align"]["default"] == "center", preset
    assert _defaults("text", "counter")["anchor"]["default"] == "center"


def test_anchos_comunes_maximo_sym_w():
    assert _defaults("graphics", "cta")["width"]["max"] == S.SYM_W
    assert _defaults("graphics", "socials")["width"]["max"] == S.SYM_W
    p = S.normalize({"video": "v", "duration": 10, "fx": {"graphics": [
        {"kind": "cta", "variant": "follow", "width": 0.82, "start": 1, "end": 2},
        {"kind": "socials", "width": 0.9, "start": 1, "end": 2}]}})
    assert [g["width"] for g in p["fx"]["graphics"]] == [S.SYM_W, S.SYM_W]


def test_red_de_seguridad_si_los_modulos_publican_defaults_asimetricos(monkeypatch):
    """Aunque graphics.DEFAULTS o text.PRESETS vuelvan a publicar x=0.47 / borde izquierdo /
    cascade a la izquierda, el catalogo sigue centrado (x y align son del esquema)."""
    G = importlib.import_module("motion.graphics")
    T = importlib.import_module("motion.text")
    defs = copy.deepcopy(G.DEFAULTS)
    for k in CENTERED_GRAPHICS:
        defs.setdefault(k, {})["x"] = 0.47
    defs["lower_third"]["x"] = 0.07
    pres = copy.deepcopy(T.PRESETS)
    pres["cascade"] = dict(pres["cascade"], align="left", x=0.1)
    monkeypatch.setattr(G, "DEFAULTS", defs)
    monkeypatch.setattr(T, "PRESETS", pres)
    kinds = S._build_kinds()
    used = S._adopt_modules(kinds)
    assert "graphics" in used and "text" in used
    gr = kinds["tracks"]["graphics"]["items"]
    for k in CENTERED_GRAPHICS:
        assert next(p for p in gr[k]["params"] if p["k"] == "x")["default"] == 0.5, k
    casc = {p["k"]: p for p in kinds["tracks"]["text"]["items"]["cascade"]["params"]}
    assert casc["x"]["default"] == 0.5 and casc["align"]["default"] == "center"
    # lo demas si se adopta (y, tamanos): la red de seguridad solo cubre lo horizontal
    assert next(p for p in gr["card"]["params"] if p["k"] == "y")["default"] == defs["card"]["y"]


@pytest.mark.parametrize("fill", [False, True])
def test_rotulo_de_plan_viejo_conserva_su_anclaje(fill):
    """Un rotulo sin `align` (plan de antes de la regla) sigue anclado por su borde izquierdo:
    normalize (ni con fill=True) le pone align ni el x centrado del catalogo."""
    p = S.normalize({"video": "v", "duration": 10, "fx": {"graphics": [
        {"kind": "lower_third", "title": "Ana Demo", "start": 1, "end": 3},
        {"kind": "lower_third", "title": "Ana Demo", "x": 0.07, "start": 1, "end": 3},
        {"kind": "lower_third", "title": "Ana Demo", "align": "center", "start": 1, "end": 3},
        {"kind": "lower_third", "title": "Ana Demo", "align": "diagonal", "start": 1, "end": 3}]}},
        fill=fill)
    a, b, c, d = p["fx"]["graphics"]
    assert "align" not in a and ("x" not in a)
    assert "align" not in b and b["x"] == 0.07
    assert c["align"] == "center" and (c["x"] == 0.5 if fill else "x" not in c)
    assert d["align"] == "center"                              # invalido -> default centrado
    assert S.validate(p) == []
