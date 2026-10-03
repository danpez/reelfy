"""Pruebas de motion/schema.py: defaults, migracion, normalize, validate y catalogo KINDS."""
import copy
import json

import pytest

from motion import schema as S
from conftest import load_plan_v1


@pytest.fixture
def v1():
    return load_plan_v1()


def test_defaults_contrato():
    d = S.DEFAULTS
    assert d["version"] == 2 and d["shots"] == []
    assert set(d["fx"]) == {"captions", "text", "graphics", "cutaways", "effects", "transitions",
                            "sfx", "music", "grade"}
    cap = d["fx"]["captions"]
    assert cap["preset"] == "clean_bold" and cap["y"] == 0.72 and cap["max_words"] == 4
    assert d["fx"]["music"]["gain_db"] == -17 and d["fx"]["music"]["duck_db"] == -9
    assert d["fx"]["grade"]["look"] == "warm_clean"
    assert d["director"] == {"style": "dinamico", "intensity": 0.75, "seed": 7, "locked": [],
                             "llm": "qwen3.5:4b"}
    assert all(v == "" for v in d["entities"].values())


def test_migrate_v1_conserva_claves_y_no_muta(v1):
    orig = copy.deepcopy(v1)
    p = S.migrate_v1(v1)
    assert v1 == orig                                     # sin efectos laterales
    assert p["version"] == 2
    for k in orig:
        assert p[k] == orig[k]
    assert p["fx"]["text"] == [] and p["fx"]["captions"]["preset"] == "clean_bold"
    assert S.validate(p) == []


def test_migrate_v1_custom_captions(v1):
    p = S.migrate_v1(v1, custom={"cap_pos": 0.68, "cap_color": "#ff00aa", "cap_case": "upper",
                                 "cap_scale": 1.2})
    cap = p["fx"]["captions"]
    assert cap["y"] == 0.68 and cap["accent"] == "#FF00AA" and cap["case"] == "upper"
    assert cap["size"] == pytest.approx(0.052 * 1.2, abs=1e-4)


def test_migrate_v2_es_idempotente(v1):
    p = S.migrate_v1(v1)
    assert S.migrate_v1(p) == p


def _plan(v1, **fx):
    p = S.migrate_v1(v1)
    p["fx"].update(fx)
    return p


def test_normalize_ids_unicos_y_estables(v1):
    p = _plan(v1, text=[
        {"preset": "title_pop", "text": "A", "start": 1, "end": 2, "id": "tx_3"},
        {"preset": "stack", "text": "B", "start": 2, "end": 3},
        {"preset": "stack", "text": "C", "start": 3, "end": 4, "id": "tx_3"},   # duplicado
        {"preset": "kicker", "text": "D", "start": 4, "end": 5, "id": "mio"},
    ], graphics=[{"kind": "doodle", "start": 1, "end": 2}],
        sfx=[{"name": "pop", "at": 1.0}, {"name": "ding", "at": 2.0}])
    n = S.normalize(p)
    ids = [t["id"] for t in n["fx"]["text"]]
    assert ids == ["tx_3", "tx_4", "tx_5", "mio"]
    assert n["fx"]["graphics"][0]["id"] == "gr_1"
    assert [s["id"] for s in n["fx"]["sfx"]] == ["sf_1", "sf_2"]
    # estable: normalizar de nuevo no cambia nada
    assert S.normalize(n) == n


def test_normalize_descarta_invalidos(v1):
    p = _plan(v1, text=[{"preset": "no_existe", "text": "x", "start": 1, "end": 2},
                        {"preset": "title_pop", "text": "ok", "start": 1, "end": 2},
                        {"preset": "title_pop", "text": "sin tiempos"},
                        "basura"],
              effects=[{"kind": "zoom", "start": 1, "end": 3}, {"kind": "teleport", "start": 1, "end": 2}],
              transitions=[{"kind": "flash", "at": 5}, {"kind": "morph", "at": 6}, {"kind": "slide"}],
              sfx=[{"name": "no_existe_sfx", "at": 1}])
    n = S.normalize(p)
    assert [t["text"] for t in n["fx"]["text"]] == ["ok"]
    assert [e["kind"] for e in n["fx"]["effects"]] == ["zoom"]
    assert [t["kind"] for t in n["fx"]["transitions"]] == ["flash"]
    assert n["fx"]["sfx"] == []


def test_normalize_clamps_y_campos_comunes(v1):
    dur = v1["duration"]
    p = _plan(v1, text=[{"preset": "title_pop", "text": "x", "start": -2, "end": 999,
                         "x": 3.0, "y": -1, "size": 9, "color": "fff", "intensity": 7,
                         "anim_in": "volar", "align": "diagonal", "box": "nada", "z": "3"}],
              effects=[{"kind": "zoom", "start": 5, "end": 5.01, "to": 9}],
              transitions=[{"kind": "flash", "at": 999, "dur": 30}])
    n = S.normalize(p)
    t = n["fx"]["text"][0]
    assert t["start"] == 0 and t["end"] == pytest.approx(dur, abs=1e-3)
    assert t["x"] == 1.0 and t["y"] == 0.0 and t["size"] == 0.45
    assert t["color"] == "#FFFFFF" and t["intensity"] == 1.0
    assert t["anim_in"] == "pop" and t["align"] == "center" and t["box"] is None
    assert t["z"] == 3
    assert t["enabled"] is True and t["locked"] is False and t["src"] == "ai"
    e = n["fx"]["effects"][0]
    zmax = next(q["max"] for q in S.item_params("effects", "zoom") if q["k"] == "to")
    assert e["end"] - e["start"] >= S.MIN_ITEM - 1e-9 and e["to"] == zmax
    tr = n["fx"]["transitions"][0]
    dmax = next(q["max"] for q in S.item_params("transitions", "flash") if q["k"] == "dur")
    assert tr["at"] == pytest.approx(dur, abs=1e-3) and tr["dur"] == min(2.0, dmax)
    assert S.validate(n) == []


def test_normalize_hasta_el_final_con_duracion_de_5_decimales(v1):
    """Un video compuesto (montaje) dura p. ej. 81.59069 s: un item hasta el final redondeado a 4
    decimales (81.5907) quedaba DESPUES del final y validate avisaba "rango fuera del video"
    (aviso falso en el Studio). Ahora se redondea sin pasarse."""
    p = _plan(v1, graphics=[{"kind": "cta", "variant": "follow", "start": 5.0, "end": 999}],
              effects=[{"kind": "zoom", "start": 3.0, "end": 81.59069}],
              transitions=[{"kind": "flash", "at": 999, "dur": 0.3}])
    p["duration"] = 81.59069
    n = S.normalize(p)
    g, e, tr = n["fx"]["graphics"][0], n["fx"]["effects"][0], n["fx"]["transitions"][0]
    assert g["end"] == 81.5906 and e["end"] == 81.5906 and tr["at"] == 81.5906
    assert not [x for x in S.validate(n) if "fuera del video" in x]
    p["duration"] = 81.757357                 # 81.7574 tambien se pasaba: se queda en 81.7573
    n = S.normalize(p)
    assert n["fx"]["graphics"][0]["end"] == 81.7573 and n["fx"]["effects"][0]["end"] == 81.5907
    assert not [x for x in S.validate(n) if "fuera del video" in x]


def test_normalize_completa_requeridos_no_opcionales(v1):
    p = _plan(v1, graphics=[{"kind": "doodle", "start": 1, "end": 2},
                            {"kind": "card", "start": 1, "end": 2}],
              transitions=[{"kind": "slide", "at": 3}])
    n = S.normalize(p)
    g0, g1 = n["fx"]["graphics"]
    assert g0["name"] == "sparkle" and g1["variant"] == "generic"
    assert "size" not in g0              # opcional: lo decide el renderer
    ddef = next(q["default"] for q in S.item_params("transitions", "slide") if q["k"] == "dur")
    assert n["fx"]["transitions"][0]["dur"] == ddef          # default del kind (o 0.35 base)
    full = S.normalize(p, fill=True)
    # el default lo publica graphics.DEFAULTS (adoptado): se compara contra el catalogo
    size_def = next(q["default"] for q in S.item_params("graphics", "doodle") if q["k"] == "size")
    assert full["fx"]["graphics"][0]["size"] == size_def


def test_normalize_listas_y_vectores(v1):
    p = _plan(v1, text=[{"preset": "title_pop", "text": "x", "start": 1, "end": 2,
                         "accent_words": "hola, mundo"}],
              effects=[{"kind": "kenburns", "start": 1, "end": 3, "pan": [5, "a"]},
                       {"kind": "bg_replace", "start": 1, "end": 3, "bg_colors": ["#123", "zz", "#abcdef"]}],
              graphics=[{"kind": "doodle", "start": 1, "end": 2, "path_to": [0.2, 1.4]}])
    n = S.normalize(p)
    assert n["fx"]["text"][0]["accent_words"] == ["hola", "mundo"]
    assert n["fx"]["effects"][0]["pan"] == [0.02, -0.01]           # invalido -> default
    assert n["fx"]["effects"][1]["bg_colors"] == ["#112233", "#ABCDEF"]
    assert n["fx"]["graphics"][0]["path_to"] == [0.2, 1.0]


def test_normalize_sfx_del_catalogo_generado(monkeypatch, v1):
    monkeypatch.setattr(S, "_sfx_extra", lambda: {"mi_sonido"})
    n = S.normalize(_plan(v1, sfx=[{"name": "mi_sonido", "at": 1}]))
    assert n["fx"]["sfx"][0]["name"] == "mi_sonido"


def test_normalize_config_invalida(v1):
    p = S.migrate_v1(v1)
    p["fx"]["captions"] = {"preset": "raro", "y": 5}
    p["fx"]["grade"] = "nada"
    p["director"]["style"] = "loco"
    p["entities"]["price"] = 1000
    n = S.normalize(p)
    assert n["fx"]["captions"]["preset"] == "clean_bold" and n["fx"]["captions"]["y"] == 0.9
    assert n["fx"]["grade"]["look"] == "warm_clean"
    assert n["director"]["style"] == "dinamico"
    assert n["entities"]["price"] == "1000"
    assert S.validate(n) == []


def test_normalize_acepta_v1_directo(v1):
    n = S.normalize(v1)
    assert n["version"] == 2 and S.validate(n) == []


def test_validate_detecta_problemas(v1):
    p = S.migrate_v1(v1)
    p["fx"]["text"] = [
        {"id": "a", "preset": "title_pop", "text": "", "start": 3, "end": 2, "enabled": True,
         "locked": False, "intensity": 2, "color": "rojo"},
        {"id": "a", "preset": "bogus", "start": 1, "end": 2},
    ]
    p["fx"]["transitions"] = [{"id": "t1", "kind": "flash", "enabled": True, "locked": False,
                               "dur": 0.3}]
    p["fx"]["captions"]["preset"] = "raro"
    errs = S.validate(p)
    joined = " | ".join(errs)
    for frag in ("'end' debe ser mayor", "intensity", "color invalido", "texto vacio",
                 "id duplicado", "preset desconocido", "falta 'at'", "captions"):
        assert frag in joined, (frag, errs)
    assert S.validate({"version": 1}) and S.validate("x") == ["el plan no es un objeto"]


def test_kinds_json_y_tipos():
    js = json.dumps(S.KINDS)
    k = json.loads(js)
    types = set(k["types"])
    assert types == {"num", "text", "color", "select", "bool", "seg", "font", "icon", "lines", "rows",
                     "media"}
    count = 0
    for track, t in k["tracks"].items():
        assert t["items"], track
        for name, spec in t["items"].items():
            assert spec["label"]
            for prm in spec["params"]:
                count += 1
                assert prm["type"] in types, (track, name, prm)
                assert prm["k"] and prm["label"]
                if prm["type"] == "num":
                    assert prm["min"] <= prm["max"] and prm["step"] > 0
                    if isinstance(prm["default"], (int, float)):
                        assert prm["min"] <= prm["default"] <= prm["max"], (name, prm)
                if prm["type"] in ("select", "seg"):
                    assert prm["default"] in prm["opts"] or (
                        prm.get("nullable") and prm["default"] is None), (name, prm)
                if prm["type"] in ("rows", "lines"):
                    assert prm["fields"] and all(f["k"] and f["type"] in types for f in prm["fields"])
    assert count > 150


def test_kinds_cubre_catalogo_normativo():
    tr = S.KINDS["tracks"]
    assert set(tr["text"]["items"]) == set(S.TEXT_PRESETS)
    assert set(tr["graphics"]["items"]) == set(S.GRAPHIC_KINDS)
    assert set(tr["cutaways"]["items"]) == set(S.CUTAWAY_KINDS)
    assert set(tr["effects"]["items"]) == set(S.EFFECT_KINDS)
    assert set(tr["transitions"]["items"]) == set(S.TRANSITION_KINDS)
    assert len(tr["sfx"]["items"]) == 32
    assert tr["effects"]["items"]["color_pop"]["needs_matte"] is True
    counter = {p["k"] for p in tr["text"]["items"]["counter"]["params"]}
    assert {"from", "to", "prefix", "suffix", "decimals"} <= counter
    bg = {p["k"] for p in tr["text"]["items"]["behind_giant"]["params"]}
    assert "behind" in bg
    assert next(p for p in tr["text"]["items"]["behind_giant"]["params"]
                if p["k"] == "behind")["default"] is True
    assert S.item_params("effects", "zoom")[0]["k"] == "start"
    assert S.item_params("effects", "nada") == []
    assert S.schema() == S.KINDS and S.schema() is not S.KINDS


def test_adopcion_de_defaults_de_modulos():
    """Si los modulos duenos publican defaults, el inspector los refleja por kind (sin
    contaminar otros kinds) y los colores con nombre (warm, white) son validos."""
    k = S.KINDS
    if "graphics" in k.get("adopted", []):
        card = {p["k"]: p for p in S.item_params("graphics", "card")}
        sticker = {p["k"]: p for p in S.item_params("graphics", "sticker_text")}
        assert card["y"]["default"] != sticker["y"]["default"] or card["size"]["default"] != sticker["size"]["default"]
        for kind in S.GRAPHIC_KINDS:
            ain = next(p for p in S.item_params("graphics", kind) if p["k"] == "anim_in")
            assert ain["default"] in ain["opts"]
    if "transitions" in k.get("adopted", []):
        leak = {p["k"]: p for p in S.item_params("transitions", "light_leak")}
        assert "dur" in leak and "align" in leak
        p = S.migrate_v1({"duration": 10, "video": "x"})
        p["fx"]["transitions"] = [{"kind": "light_leak", "at": 2, "color": "warm"},
                                  {"kind": "flash", "at": 4, "color": "rojo"}]
        n = S.normalize(p)
        assert n["fx"]["transitions"][0]["color"] == "warm"
        assert n["fx"]["transitions"][1]["color"] != "rojo"
        assert S.validate(n) == []
    json.dumps(k)


def test_normalize_musica_stops_swells_drop():
    """Datos malos de musica se descartan/acotan (antes tumbaban render_pro en _audio)."""
    p = {"video": "x", "duration": 10.0, "fx": {"music": {
        "source": "procedural", "bpm": 0,
        "stops": [{"at": "x"}, {"at": 3, "dur": 0.4}, {"at": 4, "dur": -1}, "nada", {"at": 50}],
        "swells": [{"start": 5, "end": 2, "db": 40}, {"start": 1}, {"start": 1, "end": 1.01}],
        "drop_at": "y"}}}
    n = S.normalize(p)
    m = n["fx"]["music"]
    assert m["source"] == "procedural" and m["bpm"] == 0         # opciones del modulo music
    assert m["stops"] == [{"at": 3.0, "dur": 0.4}, {"at": 10.0}]
    assert m["swells"] == [{"start": 2.0, "end": 5.0, "db": 12.0}]
    assert m["drop_at"] is None
    assert S.validate(n) == []
    bad = S.normalize({"video": "x", "duration": 10.0})
    bad["fx"]["music"]["stops"] = [{"at": "x"}]
    bad["fx"]["music"]["swells"] = [{"start": 3, "end": 1}]
    errs = " | ".join(S.validate(bad))
    assert "stops[0]" in errs and "swells[0]" in errs


def test_text_lines_en_catalogo_y_normalize():
    prm = next(q for q in S.item_params("text", "stack") if q["k"] == "lines")
    assert prm["type"] == "lines" and {f["k"] for f in prm["fields"]} == S.LINE_FIELDS
    p = {"video": "x", "duration": 10.0, "fx": {"text": [
        {"preset": "stack", "start": 1, "end": 2,
         "lines": ["HOLA", {"text": "mundo", "scale": 9, "color": "rojo", "italic": 1,
                            "tracking": 0.1}, 5]}]}}
    n = S.normalize(p)
    ln = n["fx"]["text"][0]["lines"]
    assert ln[0] == {"text": "HOLA", "scale": 1.0, "italic": False}
    assert ln[1]["scale"] == 4.0 and ln[1]["italic"] is True and "color" not in ln[1]
    assert ln[1]["tracking"] == 0.1                               # claves extra del renderer
    assert S.validate(n) == []                                    # lines sin text: valido
    n["fx"]["text"][0]["lines"] = [{"text": 3, "scale": 99, "color": "zz"}]
    errs = " | ".join(S.validate(n))
    assert "lines[0].text" in errs and "lines[0].scale" in errs and "lines[0].color" in errs


# ------------------------------------------------------------------ ola 2: todo editable
EXPUESTOS = {
    ("effects", "punch"): {"amount", "hold", "mode", "release", "attack", "target", "center"},
    ("effects", "zoom"): {"from", "to", "easing", "target"},
    ("effects", "shake"): {"amp", "freq", "decay", "rot", "attack"},
    ("effects", "kenburns"): {"from", "to", "pan", "easing"},
    ("effects", "rgb_split"): {"amount", "angle", "radial", "jitter", "attack", "hold"},
    ("effects", "flash_frame"): {"color", "peak", "bloom", "attack"},
    ("effects", "bw"): {"contrast", "subject_color", "vignette", "lift", "tone", "fade"},
    ("effects", "color_pop"): {"bg_desat", "bg_dark", "fade_in", "fade_out"},
    ("effects", "bg_blur"): {"radius", "fade_in", "fade_out"},
    ("effects", "bg_replace"): {"background", "bg_colors", "grain", "vignette", "fade_in", "fade_out"},
    ("graphics", "doodle"): {"style", "anchor", "bend", "glow", "shadow"},
    ("graphics", "icon"): {"style", "anchor"},
    ("graphics", "card"): {"accent", "label"},
    ("graphics", "cta"): {"subtitle", "button", "button_color"},
    ("graphics", "lower_third"): {"theme"},
    ("graphics", "progress"): {"bar_y", "thickness", "color"},
    ("cutaways", "photo"): {"caption", "tilt"},
    ("cutaways", "self_broll"): {"pan", "src_t", "zoom_from", "zoom_to"},
    ("text", "counter"): {"anchor", "sep"},
    ("sfx", "pop"): {"variant", "pitch", "dur", "gain_db", "pan"},
}


def test_schema_expone_parametros_de_los_modulos():
    """El inspector puede ajustar TODO lo que implementan los modulos (lista de la ola 1)."""
    for (track, kind), keys in EXPUESTOS.items():
        got = {p["k"] for p in S.item_params(track, kind)}
        assert keys <= got, (track, kind, keys - got)
    prog = {p["k"] for p in S.item_params("graphics", "progress")}
    assert not ({"x", "y", "size"} & prog)                       # la barra usa bar_y
    cap = {p["k"] for p in S.KINDS["captions"]["params"]}
    assert {"kw_accent", "recenter", "max_lines"} <= cap
    grade = {p["k"]: p for p in S.KINDS["grade"]["params"]}
    assert {"strength", "exposure", "temperature", "tint", "saturation", "contrast",
            "clarity"} <= set(grade)
    assert grade["strength"]["max"] == 1.5 and grade["clarity"]["default"] is None
    mus = {p["k"]: p for p in S.schema()["music"]["params"]}
    assert mus["bpm"]["default"] == 0 and "bpm_lock" in mus
    assert {"mood", "keyscale", "seed", "ending", "voice_carve_db"} <= set(mus)
    stops = {f["k"]: f for f in mus["stops"]["fields"]}
    assert mus["stops"]["type"] == "rows" and {"at", "dur", "mode", "fade", "fade_back"} <= set(stops)
    assert stops["mode"]["opts"] == ["tape", "cut"]
    # transiciones: parametros del modulo por kind; `align` solo en las centradas
    tr = S.KINDS["tracks"]["transitions"]["items"]
    for k in S.TRANSITION_MIXERS:
        assert "align" not in {p["k"] for p in tr[k]["params"]} and tr[k]["mixer"] is True
    for k in ("flash", "light_leak", "whip", "zoom_blur", "glitch"):
        assert "align" in {p["k"] for p in tr[k]["params"]}
    if "transitions" in S.KINDS.get("adopted", []):
        leak = {p["k"] for p in tr["light_leak"]["params"]}
        assert {"peak", "gate", "color"} <= leak
        assert {"mode", "x", "y"} <= {p["k"] for p in tr["torn_paper"]["params"]}
    # etiquetas en espanol en todo el catalogo (no la clave cruda) y sin emojis
    for t in S.KINDS["tracks"].values():
        for spec in t["items"].values():
            for p in spec["params"]:
                assert p["label"] and p["label"] != p["k"] or p["k"] in ("x", "y"), p
                # sin emojis; la copy de ui_copy.json usa flechas tipograficas (Izquierda ↔ Derecha)
                assert all(ord(ch) < 0x2000 or 0x2190 <= ord(ch) <= 0x21FF for ch in p["label"]), p


def test_normalize_conserva_parametros_nuevos():
    p = {"video": "x", "duration": 20.0, "fx": {
        "effects": [{"kind": "punch", "start": 1, "end": 3, "mode": "cut", "hold": None,
                     "release": "cut", "target": [0.5, 0.33]},
                    {"kind": "rgb_split", "start": 4, "end": 5, "angle": 400, "jitter": 0.5},
                    {"kind": "bw", "start": 6, "end": 7, "tone": "sepia", "lift": 9}],
        "sfx": [{"name": "typing", "at": 2, "variant": 1, "pitch": -30, "dur": 1.2}],
        "graphics": [{"kind": "doodle", "start": 1, "end": 2, "anchor": "face_left",
                      "x": None, "y": None, "style": "sketch"},
                     {"kind": "progress", "start": 0, "end": 20, "bar_y": 2}],
        "transitions": [{"kind": "film_burn", "at": 5}],
        "music": {"stops": [{"at": 4, "dur": 0.5, "mode": "cut", "fade": 0.03},
                            {"at": 6, "mode": "raro"}], "bpm_lock": True, "bpm": 128}}}
    n = S.normalize(p)
    fx = n["fx"]
    pu, rg, bw = fx["effects"]
    assert pu["mode"] == "cut" and pu["hold"] is None and pu["release"] == "cut"
    assert pu["target"] == [0.5, 0.33]
    assert rg["angle"] == 180.0 and rg["jitter"] == 0.5
    assert bw["tone"] == "sepia" and bw["lift"] == 0.2
    sf = fx["sfx"][0]
    assert sf["variant"] == 1 and sf["pitch"] == -12.0 and sf["dur"] == 1.2
    dd, pg = fx["graphics"]
    assert dd["x"] is None and dd["y"] is None and dd["anchor"] == "face_left"
    assert dd["style"] == "sketch" and pg["bar_y"] == 1.0
    fb = fx["transitions"][0]
    fbdef = next(q["default"] for q in S.item_params("transitions", "film_burn") if q["k"] == "dur")
    assert fb["dur"] == pytest.approx(fbdef)                     # default del kind, no 0.35
    m = fx["music"]
    assert m["bpm"] == 128 and m["bpm_lock"] is True
    assert m["stops"][0] == {"at": 4.0, "dur": 0.5, "mode": "cut", "fade": 0.03}
    assert "mode" not in m["stops"][1]                           # opcion invalida -> default
    assert S.validate(n) == []
    n["fx"]["music"]["stops"] = [{"at": 1, "fade": 9}]
    assert any("stops[0].fade" in e for e in S.validate(n))


# ---------------------------------------------------------------- ola 2: catalogo == renderers
def _kparam(track, kind, k):
    return next(q for q in S.KINDS["tracks"][track]["items"][kind]["params"] if q["k"] == k)


def test_defaults_y_rangos_del_catalogo_coinciden_con_los_renderers():
    """Lo que muestra el inspector es lo que se dibuja: default = comportamiento sin el campo,
    y el rango no pasa del que acota el renderer (fuera de el, el control no haria nada)."""
    import numpy as np
    from motion import ctx as C, cutaways as CW, graphics as G, music as M
    # sticker_text.border: el renderer dibuja borde si falta el campo
    q = _kparam("graphics", "sticker_text", "border")
    assert q["default"] is True

    def draw(item):
        arr, surf = C.new_frame(270, 480)
        surf.getCanvas().clear(C.color("#6E7B64"))
        G.RENDERERS["sticker_text"](surf.getCanvas(), item, 1.0, 3.0, C.demo_ctx(W=270, H=480, t=1.0))
        return arr.copy()
    base = {"id": "s", "text": "HOLA", "x": 0.5, "y": 0.5}
    assert np.array_equal(draw(base), draw(dict(base, border=q["default"])))
    assert not np.array_equal(draw(base), draw(dict(base, border=False)))
    # photo.size: el renderer lo acota a [0.3, 0.8]
    q = _kparam("cutaways", "photo", "size")
    assert (q["min"], q["max"]) == (0.3, 0.8) and q["default"] == CW.DEFAULTS["photo"]["size"]
    # musica: fade_out es la cola que decide music.py (0.6-3.0); fade_in no tiene efecto con el
    # arranque horneado y no se expone; la entrada suave explicita es intro_fade
    mp = {p["k"]: p for p in S.KINDS["music"]["params"]}
    cs = M.CFG_SCHEMA["fade_out"]
    assert (mp["fade_out"]["min"], mp["fade_out"]["max"], mp["fade_out"]["default"]) == \
        (cs["min"], cs["max"], cs["default"])
    assert "fade_in" not in mp and mp["intro_fade"]["default"] == 0.0
    assert S.DEFAULT_MUSIC["intro_fade"] == 0.0 and "fade_in" not in S.DEFAULT_MUSIC


def test_sfx_con_alias_se_resuelve_en_vez_de_descartarse():
    """'riser' o 'riser_short_2' (alias / sufijo de variante) se resuelven al nombre del
    catalogo (con la variante del sufijo) en vez de desaparecer sin aviso."""
    p = S.normalize({"video": "x.mp4", "duration": 10.0, "version": 2,
                     "fx": {"sfx": [{"id": "a", "name": "riser_short_2", "at": 1.0},
                                    {"id": "b", "name": "riser", "at": 2.0},
                                    {"id": "c", "name": "pop_3", "at": 3.0, "variant": 0},
                                    {"id": "d", "name": "no_existe_xyz", "at": 4.0}]}})
    sf = {it["id"]: it for it in p["fx"]["sfx"]}
    assert sf["a"]["name"] == "riser_short" and sf["a"]["variant"] == 1
    assert sf["b"]["name"].startswith("riser")
    assert sf["c"]["name"] == "pop" and sf["c"]["variant"] == 0      # la variante explicita gana
    assert "d" not in sf
    assert S.resolve_sfx_name("campana")[0] in ("bell", "ding", "chime")


# ---------------------------------------------------------------- ola 3: pedidos al catalogo
def _walk_labels(o, out):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("label", "hint", "desc") and isinstance(v, str):
                out.append(v)
            else:
                _walk_labels(v, out)
    elif isinstance(o, list):
        for x in o:
            _walk_labels(x, out)
    return out


def test_etiquetas_con_acentos_utf8():
    """El catalogo sale con su ortografia (UTF-8): el Studio ya no necesita su mapa de
    respaldo. Ninguna palabra del mapa queda sin acento, tampoco en lo que adoptan los modulos."""
    import re
    labels = _walk_labels(S.schema(), [])
    assert len(labels) > 200
    sin = sorted({w for s in labels for w in re.findall(r"[A-Za-z]+", s) if w.lower() in S._ACCENTS})
    assert sin == [], sin
    common = {p["k"]: p["label"] for p in S.item_params("text", "title_pop")}
    # etiquetas de ui_copy.json (es-MX, sin jerga): "Posición X" ahora es "Posición horizontal"
    assert common["size"] == "Tamaño" and common["x"] == "Posición horizontal"
    assert S.KINDS["tracks"]["graphics"]["label"] == "Gráficos"
    assert S.KINDS["tracks"]["transitions"]["items"]["circle"]["label"] == "Círculo"
    # accent_es es idempotente y respeta mayusculas
    assert S.accent_es("Duracion de la animacion") == "Duración de la animación"
    assert S.accent_es(S.accent_es("TITULO")) == "TÍTULO"
    assert S.accent_es("dinamico_x") == "dinamico_x"          # identificadores intactos
    # el bloque y su interruptor no repiten la misma etiqueta
    for blk in ("captions", "music", "grade"):
        en = next(p for p in S.KINDS[blk]["params"] if p["k"] == "enabled")
        assert en["label"] != S.KINDS[blk]["label"], blk
    json.dumps(S.schema(), ensure_ascii=False)


def test_mixer_marcado_en_todas_las_transiciones():
    from motion import transitions as TR
    items = S.schema()["tracks"]["transitions"]["items"]
    assert set(items) == set(S.TRANSITION_KINDS)
    for k, spec in items.items():
        assert isinstance(spec["mixer"], bool), k
        assert spec["mixer"] == (k in set(getattr(TR, "MIXERS", S.TRANSITION_MIXERS))), k


def test_count_dur_y_y_ranges_expuestos_y_normalizados():
    cnt = {p["k"]: p for p in S.item_params("text", "counter")}
    assert cnt["count_dur"]["default"] is None and cnt["count_dur"]["nullable"]
    cap = {p["k"]: p for p in S.schema()["captions"]["params"]}
    yr = cap["y_ranges"]
    assert yr["type"] == "rows" and {f["k"] for f in yr["fields"]} == {"start", "end", "y"}
    p = S.normalize({"video": "x", "duration": 10.0, "fx": {
        "captions": {"y_ranges": [{"start": 6, "end": 4, "y": 0.55}, {"start": 1, "end": 2, "y": 3},
                                  {"start": "a", "end": 2, "y": 0.5}, "basura",
                                  {"start": 8, "end": 8.01, "y": 0.5}, {"start": 9, "end": 30, "y": 0.4}]},
        "text": [{"preset": "counter", "start": 1, "end": 3, "to": 500, "count_dur": 99}]}})
    rows = p["fx"]["captions"]["y_ranges"]
    assert [(r["start"], r["end"], r["y"]) for r in rows] == [(1.0, 2.0, 0.9), (4.0, 6.0, 0.55),
                                                              (9.0, 10.0, 0.4)]
    assert p["fx"]["text"][0]["count_dur"] == 5.0
    assert S.validate(p) == []
    # sin el campo: lista vacia (el renderer usa `y` fijo)
    assert S.normalize({"video": "x", "duration": 5.0})["fx"]["captions"]["y_ranges"] == []


def test_normalize_conserva_music_grid_y_entities_extra():
    """El director guarda la rejilla de la musica y el nombre por confirmar: normalize no los
    toca (antes solo se probaba a mano)."""
    grid = {"bpm": 112, "beats": [0.5, 1.03], "downbeats": [0.5], "drop": 9.1, "key": "A minor",
            "_music_key": "abc"}
    plan = {"video": "x", "duration": 12.0, "version": 2,
            "director": {"style": "elegante", "music_grid": grid, "notes": ["n1"]},
            "entities": {"name": "", "name_candidate": "Dra. Ficticia", "name_src": "letrero",
                         "name_confirmed": False, "role": "Dentista"}}
    n = S.normalize(plan)
    assert n["director"]["music_grid"] == grid and n["director"]["notes"] == ["n1"]
    e = n["entities"]
    assert e["name_candidate"] == "Dra. Ficticia" and e["name_src"] == "letrero"
    assert e["name_confirmed"] is False and e["role"] == "Dentista" and e["name"] == ""
    assert S.normalize(n) == n                                   # idempotente


def test_medios_propios_en_el_catalogo_y_normalize():
    tr = S.schema()["tracks"]
    assert "media" in tr["graphics"]["items"]
    for track, kind in (("graphics", "media"), ("cutaways", "photo"), ("cutaways", "self_broll")):
        ps = {p["k"]: p for p in S.item_params(track, kind)}
        assert ps["media"]["type"] == "media" and ps["media"]["nullable"], (track, kind)
        assert {"fit", "focus", "media_in", "loop"} <= set(ps), (track, kind)
    pip = {p["k"]: p for p in S.item_params("graphics", "media")}
    assert pip["size"]["default"] == 0.36 and {"frame", "radius", "shadow", "aspect", "zoom"} <= set(pip)
    # sin posicion por defecto: el motor lo coloca lejos de la cara (render.media_auto_place);
    # el default de graphics.DEFAULTS (del renderer) no pisa la colocacion del motor
    assert pip["x"]["default"] is None and pip["y"]["default"] is None
    assert "automático" in pip["x"]["hint"] and pip["x"]["nullable"] and pip["y"]["nullable"]
    # los demas graficos conservan su default comun
    assert {p["k"]: p for p in S.item_params("graphics", "sticker_text")}["size"]["default"] != 0.45 \
        or "graphics" in S.KINDS.get("adopted", [])
    good = {"sid": "a1b2c3d4e5f6", "type": "video", "name": "clip.mov", "w": 1080, "h": 1920,
            "dur": 4.5, "thumb": "/tlsource/a1b2c3d4e5f6/thumb", "raro": 1}
    p = S.normalize({"video": "x", "duration": 10.0, "fx": {
        "graphics": [{"kind": "media", "start": 1, "end": 3, "media": good, "fit": "otra"},
                     {"kind": "media", "start": 4, "end": 5, "media": {"sid": "../../etc/passwd"}},
                     {"kind": "media", "start": 6, "end": 7, "media": "/ruta/local.png"}],
        "cutaways": [{"kind": "photo", "start": 2, "end": 4, "media": dict(good, type="image")}]}})
    g = p["fx"]["graphics"]
    assert g[0]["media"] == {k: v for k, v in good.items() if k != "raro"}
    assert g[0]["fit"] == "cover"                               # opcion invalida -> default
    assert g[1]["media"] is None and g[2]["media"] is None      # nunca una ruta
    assert p["fx"]["cutaways"][0]["media"]["type"] == "image"
    assert S.validate(p) == []
    bad = copy.deepcopy(p)
    bad["fx"]["graphics"][0]["media"] = {"sid": "x/y"}
    assert any("medio" in e for e in S.validate(bad))


def test_logo_y_foto_del_enlace_en_el_catalogo_y_normalize():
    """Perfil: `logo` (medio, nullable, solo imagen, auxiliar) en cta, card y en los enlaces de
    socials; link_below usa avatar/media como el Seguir. normalize los conserva saneados."""
    cta = {p["k"]: p for p in S.item_params("graphics", "cta")}
    card = {p["k"]: p for p in S.item_params("graphics", "card")}
    for ps in (cta, card):
        lg = ps["logo"]
        assert lg["type"] == "media" and lg["nullable"] and lg["accept"] == ["image"] and lg["aux"] is True
        assert lg["default"] is None
    assert "Link abajo" in cta["avatar"]["hint"] and "Link abajo" in cta["media"]["hint"]
    assert not cta["media"].get("aux")                          # el medio principal del CTA sigue siendo `media`
    soc = {p["k"]: p for p in S.item_params("graphics", "socials")}
    fl = {f["k"]: f for f in soc["links"]["fields"]}
    assert fl["logo"]["type"] == "media" and fl["logo"]["aux"] is True
    logo = {"sid": "lg0123abcd", "type": "image", "name": "logo_demo.png", "w": 512, "h": 512,
            "src": "https://reserva.demo/icon.svg"}
    photo = {"sid": "ph0123abcd", "type": "image", "name": "foto_demo.png", "w": 1024, "h": 1024}
    p = S.normalize({"video": "x", "duration": 10.0, "fx": {"graphics": [
        {"kind": "cta", "variant": "link_below", "start": 1, "end": 3, "avatar": "image", "media": photo, "logo": logo},
        {"kind": "cta", "variant": "follow", "start": 3, "end": 5, "logo": "/ruta/local.png"},
        {"kind": "card", "variant": "schedule", "start": 5, "end": 7, "logo": logo},
        {"kind": "socials", "start": 7, "end": 9, "links": [
            {"platform": "web", "text": "reserva.demo/ana", "logo": logo},
            {"platform": "web", "text": "otra.demo", "logo": {"sid": "../x"}}]}]}}, fill=True)
    g = p["fx"]["graphics"]
    clean = {k: v for k, v in logo.items() if k != "src"}      # norm_media: solo lo que usa el motor
    assert g[0]["logo"] == clean and g[0]["media"] == photo and g[0]["avatar"] == "image"
    assert g[1]["logo"] is None                                 # nunca una ruta
    assert g[2]["logo"] == clean
    assert g[3]["links"][0]["logo"] == clean and g[3]["links"][1]["logo"] is None
    assert S.validate(p) == []
    bad = copy.deepcopy(p)
    bad["fx"]["graphics"][3]["links"][0]["logo"] = {"sid": "x/y"}
    assert any("links[0].logo" in e for e in S.validate(bad))
    # un plan sin logo no gana la clave con fill=False (planes viejos intactos)
    q = S.normalize({"video": "x", "duration": 10.0, "fx": {"graphics": [
        {"kind": "card", "variant": "link", "start": 1, "end": 2}]}})
    assert "logo" not in q["fx"]["graphics"][0]
