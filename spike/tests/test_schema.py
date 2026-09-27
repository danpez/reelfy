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
    assert e["end"] - e["start"] >= S.MIN_ITEM - 1e-9 and e["to"] == 2.5
    tr = n["fx"]["transitions"][0]
    dmax = next(q["max"] for q in S.item_params("transitions", "flash") if q["k"] == "dur")
    assert tr["at"] == pytest.approx(dur, abs=1e-3) and tr["dur"] == min(2.0, dmax)
    assert S.validate(n) == []


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
    assert types == {"num", "text", "color", "select", "bool", "seg", "font", "icon", "lines"}
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
                    assert prm["default"] in prm["opts"], (name, prm)
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
