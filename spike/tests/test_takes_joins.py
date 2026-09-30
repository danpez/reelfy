"""Limpieza de tomas sobre el video compuesto de un MONTAJE CON IA (takes.py con `joins`).

El montaje ya eligio la toma de cada parte del guion: sobre el video compuesto la limpieza solo
trabaja DENTRO de cada segmento. Una retoma que sobro junto a una union (el segmento B arranca
repitiendo la cola de A) no debe cortar el final de A (dejaria una astilla junto a la union), los
bordes de un corte nunca pasan de las uniones de su segmento, y sin uniones todo queda igual.

Solo transcripciones SINTETICAS inventadas (el repo es publico)."""
import pytest

import takes as T
from test_takes import assert_kept_safe, words_of

# A: "... Cada manana horneamos pan de elote recien hecho." | B: repite la cola y sigue
SRC_DUP = ("Abrimos la panadería en el centro. |0.6| Cada mañana horneamos pan de elote recién hecho. "
           "|0.7| horneamos pan de elote recién hecho. |0.6| Y los domingos vendemos tamales de rajas "
           "con queso.")
# retoma completa DENTRO del segmento B (la limpieza la sigue quitando)
SRC_IN_B = ("Abrimos la panadería en el centro. |0.9| Y los domingos vendemos tamales. |0.7| "
            "Y los domingos vendemos tamales de rajas con queso.")


def _join_between(words, k):
    """Union a la mitad del hueco entre la palabra k-1 y la k."""
    return round((words[k - 1]["end"] + words[k]["start"]) / 2, 3)


def test_sin_uniones_la_deteccion_no_cambia():
    for src in (SRC_DUP, SRC_IN_B):
        w = words_of(src)
        base = T.detect(w, w[-1]["end"] + 0.6)
        for j in (None, [], ()):
            r = T.detect(w, w[-1]["end"] + 0.6, joins=j)
            assert r["cuts"] == base["cuts"]
        assert T.merge([], base["cuts"]) == T.merge([], base["cuts"], joins=None) == \
            T.merge([], base["cuts"], joins=[])


def test_retoma_que_cruza_la_union_no_corta_el_final_del_segmento():
    w = words_of(SRC_DUP)
    dur = w[-1]["end"] + 0.6
    k = next(i for i, x in enumerate(w) if i > 8 and x["text"] == "horneamos")
    j = _join_between(w, k)
    # sin uniones (un solo video): la retoma de cola corta el final de la primera toma y cruza j
    free = T.detect(w, dur)["cuts"]
    assert any(c["start"] < j < c["end"] for c in free)
    # con la union del montaje: ningun corte la cruza ni se come el final del segmento A
    r = T.detect(w, dur, joins=[j])
    for c in r["cuts"]:
        assert not T.crosses_join(c, [j]), c
        assert not (c["start"] < j and c["end"] > w[k - 1]["start"]), c
    assert_kept_safe(w, r["cuts"])


def test_retoma_dentro_de_un_segmento_se_sigue_quitando():
    w = words_of(SRC_IN_B)
    dur = w[-1]["end"] + 0.6
    k = next(i for i, x in enumerate(w) if x["text"] == "Y")
    j = _join_between(w, k)
    base = T.detect(w, dur)["cuts"]
    r = T.detect(w, dur, joins=[j])["cuts"]
    assert r and [(c["w0"], c["w1"], c["kind"]) for c in r] == [(c["w0"], c["w1"], c["kind"]) for c in base]
    assert all(c["start"] >= j - 1e-6 for c in r)
    assert_kept_safe(w, r)


def test_bordes_acotados_a_las_uniones_del_segmento():
    """El aire antes de la primera palabra quitada no pasa de la union que abre el segmento."""
    w = words_of(SRC_IN_B)
    dur = w[-1]["end"] + 0.6
    k = next(i for i, x in enumerate(w) if x["text"] == "Y")
    j = round(w[k]["start"] - 0.03, 3)          # union pegada a la primera palabra de B
    base = T.detect(w, dur)["cuts"]
    first = min(base, key=lambda c: c["start"])
    assert first["start"] < j                   # sin uniones el aire se mete antes de j
    r = T.detect(w, dur, joins=[j])["cuts"]
    c0 = min(r, key=lambda c: c["start"])
    assert c0["start"] == pytest.approx(j, abs=1e-3)
    assert not any(T.crosses_join(c, [j]) for c in r)


def test_uniones_como_dicts_y_desordenadas():
    w = words_of(SRC_DUP)
    dur = w[-1]["end"] + 0.6
    k = next(i for i, x in enumerate(w) if i > 8 and x["text"] == "horneamos")
    j = _join_between(w, k)
    a = T.detect(w, dur, joins=[j, 2.0 + 1.25])["cuts"]
    b = T.detect(w, dur, joins=[{"t": 3.25}, {"start": j}, 0, -1, "x"])["cuts"]
    assert a == b


def test_plan_joins_y_respect_joins():
    shots = [{"start": 0.0, "end": 3.0, "join": None}, {"start": 3.0, "end": 5.0, "join": "splice"},
             {"start": 5.0, "end": 7.0, "join": "line"}, {"start": 7.0, "end": 9.0},
             {"start": 9.0, "end": 12.0, "join": "section"}, {"start": 12.0, "end": 13.0, "join": "otro"}]
    assert T.plan_joins(shots) == [3.0, 5.0, 9.0]
    assert T.plan_joins({"shots": shots}) == [3.0, 5.0, 9.0]
    assert T.plan_joins({"shots": [{"start": 0, "end": 4}]}) == []
    cuts = [{"id": "s1", "start": 2.5, "end": 3.5, "enabled": True},                       # silencio
            {"id": "t1", "start": 2.5, "end": 3.5, "enabled": True, "src": "takes"},      # cruza
            {"id": "t2", "start": 3.0, "end": 4.0, "enabled": True, "src": "takes"},      # toca
            {"id": "t3", "start": 4.5, "end": 5.4, "enabled": True, "src": "takes", "user": True},
            {"id": "t4", "start": 8.0, "end": 9.5, "enabled": False, "src": "takes"},     # cruza
            {"id": "a1", "start": 8.0, "end": 9.5, "enabled": True, "src": "ai"}]
    out = T.respect_joins(cuts, T.plan_joins(shots))
    assert [c["id"] for c in out] == ["s1", "t2", "t3", "a1"]
    assert T.respect_joins(cuts, []) == cuts
    merged = T.merge(cuts, [{"id": "n1", "start": 4.0, "end": 5.5, "src": "takes", "enabled": True},
                            {"id": "n2", "start": 5.5, "end": 6.0, "src": "takes", "enabled": True}],
                     joins=[5.0])
    ids = [c["id"] for c in merged]
    assert "n1" not in ids and "n2" in ids and "t3" in ids       # la del usuario se respeta


def _montage():
    return {"version": 1,
            "script": {"source": "user", "text": "...",
                       "sections": [{"id": 1, "lines": ["L01", "L02"]}, {"id": 2, "lines": ["L03"]},
                                    {"id": 3, "lines": ["L04"]}],
                       "lines": [{"id": "L01", "text": "Abrimos la panadería en el centro."},
                                 {"id": "L02", "text": "Cada mañana horneamos pan de elote."},
                                 {"id": "L03", "text": "Y los domingos vendemos tamales de rajas con queso"},
                                 {"id": "L04", "text": "Síguenos."}]},
            "slots": [{"id": "s01", "lines": ["L01"]}, {"id": "s02", "lines": ["L02"]},
                      {"id": "s03", "lines": ["L03"]}, {"id": "s04", "lines": ["L04"]}],
            "timeline": [{"slot": "s01", "cand": "c1", "shot": 0, "t0": 0.0},
                         {"slot": "s02", "cand": "c1", "shot": 1},
                         {"slot": "s03", "cand": "c2", "shot": 2, "t0": 6.4}]}


def test_secciones_desde_el_guion():
    shots = [{"start": 0.0, "end": 3.1}, {"start": 3.1, "end": 6.4}, {"start": 6.4, "end": 11.0}]
    secs = T.script_sections(_montage(), shots)
    # L04 nunca se grabo: su seccion no aparece
    assert secs == [{"start": 0.0, "title": "Abrimos la panadería en el centro"},
                    {"start": 6.4, "title": "Y los domingos vendemos tamales"}]
    assert T.script_sections(None) == [] and T.script_sections({"version": 1}) == []
    assert T._section_title("Hola a todos los que nos siguen desde hace mucho") == "Hola a todos"
