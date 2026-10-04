"""Videos de apoyo en fragmentos: que toma va mientras se dice cada frase (scripts/brollplan.py, puro).

Catalogo y frases SINTETICOS (un producto inventado, "Nubis"): solo tomas que sirven (nunca personas),
cada fragmento de 2 a 5 tomas a la medida de su frase (primera palabra - 0.1 s a la ultima + 0.2 s), sin
pasar del final de ninguna toma, sin repetir tomas, formatos alternados, el titulo del producto cuando se
nombra, nada en el gancho ni en el cierre ni encima de una grabacion de pantalla, a lo mas 2 frases
seguidas, solo mientras se habla del tema del video de apoyo, y el LLM (opcional) solo reordena."""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import brollplan as P
from motion import reel as RL

NODE = shutil.which("node")
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def _shot(i, a, b, subject="ui", what="", text=(), tags=(), win=True, full=True, cx=0.5, cut_a=True, cut_b=True):
    return {"i": i, "a": a, "b": b, "cut_a": cut_a, "cut_b": cut_b, "subject": subject, "what": what,
            "text": list(text), "tags": list(tags), "ok_window": win, "ok_full": full,
            "full": {"cx": cx}, "cx": cx, "win_crop": None, "people": 0.0}


def lib():
    sh = [
        _shot(0, 0.0, 3.0, "title", "El titulo de nubis", ["nubis"], ["titulo"]),
        _shot(1, 3.0, 5.0, "person", "Una persona sonrie", win=False, full=False),
        _shot(2, 5.0, 6.8, "ui", "La app de citas con un calendario", ["Agenda"], ["calendario", "citas"]),
        _shot(3, 6.8, 8.6, "character", "Un personaje azul de nubis", [], ["personaje", "asistente"]),
        _shot(4, 8.6, 10.2, "ui", "Chat con mensajes del asistente", ["Hola"], ["chat", "mensajes"], full=False),
        _shot(5, 10.2, 12.4, "phone", "Un telefono con una llamada", [], ["llamada", "telefono"], win=False),
        _shot(6, 12.4, 14.0, "person", "Dos personas caminan", win=False, full=False),
        _shot(7, 14.0, 16.0, "ui", "Pantalla con graficas de ventas", ["Ventas"], ["graficas", "ventas"]),
        _shot(8, 16.0, 17.6, "screen", "Una computadora con la app abierta", [], ["computadora", "pantalla"]),
        _shot(9, 17.6, 19.4, "character", "Un personaje verde saluda", [], ["personaje"], win=False),
        _shot(10, 19.4, 21.0, "ui", "Formulario de registro de la app", ["Registro"], ["registro", "formulario"]),
        _shot(11, 21.0, 23.0, "logo", "El logo de nubis", ["nubis"], ["logo"]),
        _shot(12, 23.0, 25.0, "ui", "Lista de tareas pendientes", ["Tareas"], ["tareas", "lista"]),
        _shot(13, 25.0, 27.0, "ui", "Notificaciones en la app", [], ["notificaciones"]),
        _shot(14, 27.0, 29.0, "screen", "Tablero con reportes", ["Reporte"], ["reportes", "tablero"]),
    ]
    return [{"sid": "apoyo01", "name": "presentacion.mp4", "dur": 29.0, "w": 1712, "h": 960, "shots": sh}]


def L(i, text, a, b, sec=1):
    return {"id": f"L{i:02d}", "text": text, "start": a, "end": b, "section": sec}


def lines():
    return [
        L(1, "Les quiero contar de algo nuevo que me encanta.", 0.3, 3.2, 1),
        L(2, "Se llama Nubis.", 3.7, 4.8, 1),
        L(3, "Es un asistente que te ayuda con tus citas y tu calendario todo el día.", 5.4, 10.1, 2),
        L(4, "La diferencia es esta", 10.6, 11.6, 3),
        L(5, "puede contestar el chat de tus clientes y llamarlos por teléfono.", 11.9, 15.6, 3),
        L(6, "Además arma tus reportes de ventas cada semana.", 16.2, 19.9, 4),
        L(7, "Y también tus tareas.", 20.4, 21.6, 4),
        L(8, "Nubis también tiene un formulario de registro para tus clientes.", 24.2, 28.0, 5),
        L(9, "Mi tía hace los mejores tamales de la ciudad.", 28.6, 32.0, 6),
        L(10, "Los domingos vende en el mercado.", 32.5, 35.0, 6),
        L(11, "Síganme para más.", 35.6, 37.0, 7),
    ]


def _check_invariants(items, lb, lns, busy=()):
    shots = {s["i"]: s for s in lb[0]["shots"]}
    used = set()
    prev = None
    for it in items:
        assert 2 <= len(it["pieces"]) <= 5, it
        assert it["layout"] in ("window", "full")
        for p in it["pieces"]:
            sh = shots[p["shot"]]
            ok = sh["ok_full"] if it["layout"] == "full" else sh["ok_window"]
            assert ok, (it["id"], p)                               # solo tomas que sirven en ESE formato
            assert sh["subject"] not in ("person", "people")
            assert sh["a"] <= p["in"] < p["out"] <= p["max"] <= sh["b"] + 1e-6   # nunca pasa de su toma
            assert p["in"] >= sh["a"] + P.MARGIN - 1e-6             # ni arranca en el cuadro del corte
            assert 0.3 <= p["out"] - p["in"] <= P.PIECE_MAX + 1e-6 or len(it["pieces"]) == 2
            assert (it["sid"], p["shot"]) not in used               # nunca se repite una toma
            used.add((it["sid"], p["shot"]))
        if prev is not None:
            assert it["start"] >= prev["end"] - 1e-6                # no se enciman
        for a, b in busy:
            assert not (it["start"] < b and a < it["end"]), it
        prev = it
    return used


def test_fragmentos_a_la_medida_de_cada_frase_sin_personas_ni_repetir():
    lb, lns = lib(), lines()
    items = P.place(lns, lb, total=37.5, names=["Nubis"])
    assert items, "debia poner apoyo mientras se habla de Nubis"
    _check_invariants(items, lb, lns)
    by_line = {it["line"]: it for it in items}
    # el gancho (primera frase, empieza antes de 1.5 s) y el cierre van a cuadro
    assert "L01" not in by_line and "L11" not in by_line
    # fuera del tema (la tia y sus tamales) no hay apoyo
    assert not any(set(it["lines"]) & {"L09", "L10"} for it in items)
    # cada fragmento empieza 0.1 s antes de su primera palabra (o donde termina el anterior) y dura lo que
    # su frase hasta 0.2 s despues de la ultima palabra (sin pasar a la siguiente frase)
    starts = {L_["id"]: L_ for L_ in lns}
    for it in items:
        L0, L1 = starts[it["lines"][0]], starts[it["lines"][-1]]
        assert it["start"] >= L0["start"] - P.PRE - 1e-6
        assert it["end"] <= L1["end"] + P.POST + 1e-6 or it["line"] == "L02"     # la corta sigue sobre la otra
        # (si la frase corta anterior siguio sobre esta, empieza donde termino aquella)
        assert it["end"] - it["start"] >= P.COVER_MIN * (L1["end"] + P.POST - it["start"]) - 0.05 \
            or it["line"] == "L02"
        assert sum(p["out"] - p["in"] for p in it["pieces"]) == pytest.approx(it["end"] - it["start"], abs=0.05)


def test_el_recorte_de_cada_toma_viaja_con_el_catalogo_compacto():
    """El servidor manda el catalogo COMPACTO (brollscan.compact: `cx` arriba, sin `full`): cada pedazo de
    pantalla completa lleva el centro de recorte que se midio para SU toma (no el centro del cuadro)."""
    lb = lib()
    for k, sh in enumerate(lb[0]["shots"]):
        sh.pop("full", None)
        sh["cx"] = round(0.2 + 0.04 * k, 4)
    items = P.place(lines(), lb, total=37.5, names=["Nubis"])
    by = {s["i"]: s for s in lb[0]["shots"]}
    fulls = [p for it in items if it["layout"] == "full" for p in it["pieces"]]
    assert fulls
    for it in items:
        for p in it["pieces"]:
            assert p["cx"] == pytest.approx(by[p["shot"]]["cx"]), (it["id"], p)
    # y del catalogo completo (analisis recien hecho) tambien
    assert P.shot_cx({"full": {"cx": 0.31}}) == pytest.approx(0.31)
    assert P.shot_cx({"cx": 0.7, "full": {"cx": 0.31}}) == pytest.approx(0.7)
    assert P.shot_cx({}) is None and P.shot_cx({"cx": "x"}) is None


def test_el_titulo_va_cuando_se_nombra_el_producto_y_los_formatos_se_alternan():
    lb, lns = lib(), lines()
    items = P.place(lns, lb, total=37.5, names=["Nubis"])
    first = next(it for it in items if it["line"] == "L02")
    # "Se llama Nubis." (1.1 s) sigue sobre la siguiente frase hasta EXT y abre con el titulo del producto
    assert first["pieces"][0]["shot"] in (0, 11)
    assert "Nubis" in first["pieces"][0]["why"] and "Dices" in first["pieces"][0]["why"]
    assert first["end"] - first["start"] >= P.MIN_INSERT
    # los formatos se alternan (cuando alcanzan las tomas)
    lays = [it["layout"] for it in items]
    assert sum(1 for a, b in zip(lays, lays[1:]) if a == b) <= 1, lays
    # nunca mas de MAX_RUN frases seguidas con apoyo
    run = 0
    for it, nx in zip(items, items[1:] + [None]):
        run = run + 1 if nx is not None and nx["start"] - it["end"] < P.RUN_GAP else 0
        assert run < P.MAX_RUN, [(x["line"], x["start"], x["end"]) for x in items]


def test_no_encima_de_una_grabacion_de_pantalla_ni_de_un_recurso():
    lb, lns = lib(), lines()
    busy = [(11.5, 20.0)]
    items = P.place(lns, lb, total=37.5, names=["Nubis"], busy=busy)
    _check_invariants(items, lb, lns, busy=[(a - P.BUSY_PAD, b + P.BUSY_PAD) for a, b in busy])
    assert items


def test_cobertura_y_unidades():
    us = P.units(lines())
    # "La diferencia es esta" (sin punto) + "puede contestar..." = una unidad
    assert ["L04", "L05"] in [u["lines"] for u in us]
    # una frase corta que sigue la idea ("Y lleva...") se une a la anterior de su seccion
    assert ["L06", "L07"] in [u["lines"] for u in us]
    lb, lns = lib(), lines()
    items = P.place(lns, lb, total=37.5, names=["Nubis"])
    assert sum(it["end"] - it["start"] for it in items) <= P.COVER_MAX * 37.5 + 1e-6


def test_sin_tomas_que_sirvan_no_hay_apoyo():
    lb = lib()
    for s in lb[0]["shots"]:
        s["ok_window"] = s["ok_full"] = False
    assert P.place(lines(), lb, total=37.5) == []
    assert P.place([], lib(), total=10) == []


def test_el_llm_elige_y_ordena_pero_las_reglas_mandan():
    lb, lns = lib(), lines()
    seen = []

    def pick(text, shots, k):
        seen.append((text, [s["i"] for s in shots], k))
        # escoge las ultimas candidatas (y basura que se ignora)
        return [len(shots) - 1, 99, -1, len(shots) - 1, len(shots) - 2][:k + 2]
    items = P.place(lns, lb, total=37.5, names=["Nubis"], pick=pick)
    _check_invariants(items, lb, lns)
    assert seen and all(len(c) <= P.POOL for _t, c, _k in seen)
    # lo que el LLM eligio dice que lo eligio la IA; el titulo nombrado sigue primero
    assert any(p["why"].startswith("Muestra lo que dices") for it in items for p in it["pieces"])
    first = next(it for it in items if it["line"] == "L02")
    assert first["pieces"][0]["shot"] in (0, 11)

    def boom(*a):
        raise RuntimeError("sin ollama")
    items2 = P.place(lns, lb, total=37.5, names=["Nubis"], pick=boom)
    assert [it["line"] for it in items2] == [it["line"] for it in P.place(lns, lb, total=37.5, names=["Nubis"])]


def test_relevancia_lexical_y_respuesta_del_llm():
    sc = P.lexical_scores(lines(), lib(), ["Nubis"])
    # "computadora" ~ "computer"; "llamarlos por telefono" ~ la llamada; el nombre del producto pesa mas
    assert sc["L02"][0][1] in (0, 11) and sc["L02"][0][3] == P.stem("Nubis")
    assert any(r[1] == 5 for r in sc["L05"])
    assert P.stem("Computadoras") == P.stem("computer") == "comput"
    assert P.tokens("Se llama Nubis y la gente lo usa") == [P.stem("Nubis"), "usa"]
    assert P.parse_pick('{"tomas": ["C", "a", "C", "Z", 5]}', 4) == [2, 0]
    assert P.parse_pick("basura", 4) == []
    prompt, schema = P.pick_prompt("Hola", lib()[0]["shots"][:3], 2)
    assert "A)" in prompt and "C)" in prompt and schema["properties"]["tomas"]["items"]["enum"] == ["A", "B", "C"]


def test_frases_del_ensamble_compuesto():
    m = {"script": {"lines": [{"id": "L01", "text": "Hola"}, {"id": "L02", "text": "Adios"}],
                    "sections": [{"id": 1, "lines": ["L01", "L02"]}]},
         "slots": [{"id": "s1", "cands": [{"id": "c1", "sid": "a", "line_times": {"L01": [1.0, 2.0], "L02": [2.5, 3.5]}}]},
                   {"id": "s2", "cands": [{"id": "c2", "sid": "b", "line_times": {"L02": [10.0, 11.0]}}]}],
         "timeline": [{"slot": "s1", "cand": "c1", "sid": "a", "in": 0.8, "out": 3.0, "start": 0.0, "t0": 0.0},
                      {"slot": "s2", "cand": "c2", "sid": "b", "in": 9.9, "out": 11.2, "start": 2.2, "t0": 2.2}]}
    ls = P.line_spans(m)
    assert [x["id"] for x in ls] == ["L01", "L02"]
    assert ls[0]["start"] == pytest.approx(0.2) and ls[0]["end"] == pytest.approx(1.2)
    # L02 empieza en el primer tramo (2.5 en la fuente a, cortado en 3.0) y termina en el segundo
    assert ls[1]["start"] == pytest.approx(1.7) and ls[1]["end"] == pytest.approx(2.2 + 1.1)


# ============================================================== reparto del tiempo (fit) y port JS
def test_fit_reparte_el_tramo_final():
    ps = [{"in": 1.0, "out": 3.0, "max": 3.5}, {"in": 10.0, "out": 12.0, "max": 12.4}]
    rows = RL.fit(ps, 4.0, 30)
    assert rows == [[0.0, 2.0, 1.0, 0, 3.5], [2.0, 4.0, 10.0, 1, 12.4]]
    # el render recorto 1 s de silencio dentro: cada toma se acorta en proporcion (desde su inicio)
    rows = RL.fit(ps, 3.0, 30)
    assert rows[0][1] == pytest.approx(1.5) and rows[1][1] == pytest.approx(3.0)
    # un tramo mas largo: las ultimas se alargan hasta su max y luego se queda el ultimo cuadro
    rows = RL.fit(ps, 5.0, 30)
    # (+0.4 a la ultima hasta su max, +0.5 a la primera hasta el suyo y el resto se queda en el ultimo cuadro)
    assert rows[-1][1] == pytest.approx(5.0) and rows[0][1] - rows[0][0] == pytest.approx(2.0 + 0.5, abs=1 / 30)
    assert RL.src_time(rows, 4.99) < 12.4            # nunca pasa del fin de su toma: se queda el ultimo cuadro
    assert RL.fit([], 3.0) == [] and RL.fit(ps, 0) == []
    assert P.fit(ps, 4.0, 30) == RL.fit(ps, 4.0, 30)
    # cuadros enteros
    for r in RL.fit(ps, 3.33, 30):
        assert abs(r[0] * 30 - round(r[0] * 30)) < 1e-4


@pytest.mark.skipif(not NODE, reason="sin node")
def test_fit_y_recortes_iguales_en_el_studio(tmp_path):
    import random
    rnd = random.Random(7)
    cases = []
    for _ in range(60):
        ps = []
        t = rnd.uniform(0, 5)
        for _k in range(rnd.randint(1, 5)):
            a = t + rnd.uniform(0, 3)
            b = a + rnd.uniform(0.2, 2.5)
            ps.append({"in": round(a, 3), "out": round(b, 3), "max": round(b + rnd.choice([0, 0.4, 1.0]), 3),
                       "cx": round(rnd.uniform(0, 1), 3)})
            t = b
        cases.append({"ps": ps, "L": round(rnd.uniform(0.5, 9), 3), "w": rnd.choice([1712, 1080, 640]),
                      "h": rnd.choice([960, 1920, 360]), "ba": rnd.choice([9 / 16, 0.5625, 1.0])})
    want = [{"rows": RL.fit(c["ps"], c["L"], 30), "crop": RL.full_crop(c["ps"][0]["cx"], c["w"], c["h"], c["ba"]),
             "at": [RL.piece_at(RL.fit(c["ps"], c["L"], 30), x) for x in (0.0, c["L"] / 2, c["L"] - 0.01)]}
            for c in cases]
    js = tmp_path / "t.js"
    js.write_text(f"""
const R = require({json.dumps(str(STATIC / "reelcard.js"))});
const cases = {json.dumps(cases)};
const out = cases.map(c => {{ const rows = R.fit(c.ps, c.L, 30);
  return {{ rows, crop: R.fullCrop(c.ps[0].cx, c.w, c.h, c.ba), at: [0.0, c.L / 2, c.L - 0.01].map(x => R.pieceAt(rows, x)) }}; }});
console.log(JSON.stringify(out));
""")
    r = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    for c, a, b in zip(cases, want, got):
        assert len(a["rows"]) == len(b["rows"]), c
        for x, y in zip(a["rows"], b["rows"]):
            assert x[3] == y[3] and all(abs(p - q) < 1e-6 for p, q in zip(x[:3] + x[4:], y[:3] + y[4:])), c
            assert len(x) == len(y) == 5, c
        assert (a["crop"] is None) == (b["crop"] is None) and (a["crop"] is None or
                                                             all(abs(p - q) < 1e-6 for p, q in zip(a["crop"], b["crop"])))
        assert a["at"] == b["at"]


def test_una_linea_con_dos_oraciones_se_parte():
    lns = [L(1, "Les quiero contar algo nuevo. Se llama Nubis.", 0.3, 4.8, 1),
           L(2, "Es un asistente para tus citas.", 5.4, 8.0, 2)]
    words = [{"text": t, "start": a, "end": b} for t, a, b in (
        ("Les", 0.3, 0.5), ("quiero", 0.5, 0.8), ("contar", 0.8, 1.2), ("algo", 1.2, 1.5), ("nuevo.", 1.5, 2.0),
        ("Se", 3.7, 3.9), ("llama", 3.9, 4.2), ("Nubis.", 4.2, 4.8),
        ("Es", 5.4, 5.6), ("un", 5.6, 5.7), ("asistente", 5.7, 6.4), ("para", 6.4, 6.6), ("tus", 6.6, 6.8),
        ("citas.", 6.8, 8.0))]
    got = P.sentences(lns, words)
    assert [g["id"] for g in got] == ["L01", "L01.2", "L02"]
    assert got[1]["text"] == "Se llama Nubis." and got[1]["start"] == 3.7 and got[1]["end"] == 4.8
    assert got[0]["end"] == 2.0 and got[1]["section"] == 1
    assert P.sentences(lns, []) == lns
    # la segunda oracion (ya no el gancho) lleva el titulo del producto
    items = P.place(got, lib(), total=20.0, names=["Nubis"])
    assert items and items[0]["line"] == "L01.2" and items[0]["pieces"][0]["shot"] in (0, 11)


def test_dos_fragmentos_pegados_nunca_en_el_mismo_formato():
    """br03 (ventana, demo del telefono) seguido sin pausa de br04 (ventana, 5 tomas): se veia UNA ventana de
    10.8 s con 7 tomas. Con solo tomas de ventana, el segundo de dos fragmentos pegados empieza RUN_GAP
    despues (la camara se ve entre los dos) o la frase va a cuadro; nunca dos ventanas seguidas."""
    for full_ok in (False, True):
        lb = lib()
        for sh in lb[0]["shots"]:
            if not full_ok:
                sh["ok_full"] = False
        lns = [L(1, "Les quiero contar de algo nuevo que me encanta.", 0.3, 3.2, 1),
               L(2, "Nubis te ayuda con tus citas y tu calendario todo el día.", 3.5, 7.9, 1),
               L(3, "Y contesta el chat de tus clientes con mensajes al momento.", 8.0, 12.4, 1),
               L(4, "Además arma tus reportes de ventas y tus tareas.", 12.5, 16.9, 1),
               L(5, "Nubis también tiene un formulario de registro para tus clientes.", 17.0, 21.0, 1),
               L(6, "Síganme para más.", 21.6, 23.0, 2)]
        items = P.place(lns, lb, total=40.0, names=["Nubis"])
        assert len(items) >= 2
        _check_invariants(items, lb, lns)
        # el caso de verdad: al menos dos frases pegadas con apoyo (sin la regla irian en el mismo formato)
        assert any(y["start"] - x["end"] < P.RUN_GAP + 1.0 for x, y in zip(items, items[1:])), items
        for x, y in zip(items, items[1:]):
            if y["start"] - x["end"] < P.RUN_GAP:
                assert x["layout"] != y["layout"], (full_ok, x["id"], y["id"], x["layout"])
        if not full_ok:
            assert all(it["layout"] == "window" for it in items)
            assert all(y["start"] - x["end"] >= P.RUN_GAP - 1e-6 for x, y in zip(items, items[1:]))


def test_logos_y_titulos_solo_cuando_se_nombra_la_marca():
    """«mira las nubes de tu agenda» ponia "un logo con nubes" (un juego de palabras) y despues, cuando
    de verdad se decia la marca, el logo ya estaba gastado. Un logo o titulo solo va cuando se nombra lo que
    dice; nunca por una palabra de su descripcion ni como relleno; junto al titulo nombrado van SOLO logos y
    titulos (v6: «Se llama X» = el titulo de cierre + el logo)."""
    lb = lib()
    sh = lb[0]["shots"]
    sh.append(_shot(15, 29.0, 31.0, "logo", "Un logo con nubes en un espacio oscuro", [], ["nubes", "logo"]))
    sh.append(_shot(16, 31.0, 33.0, "logo", "Un logo blanco de una espiral", [], ["espiral", "logo"]))
    lb[0]["dur"] = 33.0
    lns = [L(1, "Les quiero contar de algo nuevo que me encanta.", 0.3, 3.2, 1),
           L(2, "Mira las nubes de tu calendario de citas y dime si encuentras algo relevante.", 3.5, 8.5, 1),
           L(3, "Se llama Nubis.", 9.6, 10.6, 2),
           L(4, "Te contestan el chat de tus clientes al momento.", 10.8, 14.6, 2),
           L(5, "Síganme para más.", 20.0, 21.0, 3)]
    sc = P.lexical_scores(lns, lb, names=["Nubis"])
    assert not [r for r in sc.get("L02", []) if r[1] in (0, 11, 15, 16)], sc.get("L02")    # sin logos por «nubes»
    items = P.place(lns, lb, total=40.0, names=["Nubis"])
    _check_invariants(items, lb, lns)
    by = {s["i"]: s for s in sh}
    for it in items:
        subj = [by[p["shot"]]["subject"] for p in it["pieces"]]
        if "L03" in it["lines"]:
            assert subj[0] in ("title", "logo") and by[it["pieces"][0]["shot"]]["text"] == ["nubis"]
            assert all(x in ("title", "logo") for x in subj), subj         # sin relleno ajeno junto al titulo
        else:
            assert not set(subj) & {"title", "logo"}, (it["lines"], subj)
    assert any("L03" in it["lines"] for it in items)
