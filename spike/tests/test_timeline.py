"""Pruebas de motion/timeline.py: keeps, remapeo, uniones, rangos, palabras, cuantizacion
expansiva, fragmentos absorbidos y uniones pegadas a cambios de toma."""
import pytest

from motion.timeline import Timeline, keeps_from_cuts
from conftest import load_plan_v1


def cut(s, e, en=True):
    return {"start": s, "end": e, "enabled": en}


# ------------------------------------------------------------------ keeps
def test_keeps_sin_cortes():
    assert keeps_from_cuts([], 10.0) == [(0.0, 10.0)]
    assert keeps_from_cuts(None, 5.0) == [(0.0, 5.0)]


def test_keeps_complemento_basico():
    ks = keeps_from_cuts([cut(2, 3), cut(5, 6)], 10.0)
    assert ks == [(0.0, 2.0), (3.0, 5.0), (6.0, 10.0)]


def test_keeps_ignora_deshabilitados_y_ordena():
    ks = keeps_from_cuts([cut(5, 6), cut(2, 3, en=False), cut(1, 1.5)], 10.0)
    assert ks == [(0.0, 1.0), (1.5, 5.0), (6.0, 10.0)]


def test_keeps_cortes_al_inicio_y_al_final():
    ks = keeps_from_cuts([cut(0, 1.07), cut(9.5, 10.0)], 10.0)
    assert ks == [(1.07, 9.5)]


def test_keeps_solapados_y_huecos_minimos():
    # cortes solapados se funden; un hueco de 0.03 s (<= 0.05) no se conserva
    ks = keeps_from_cuts([cut(2, 4), cut(3, 5), cut(5.03, 6)], 10.0)
    assert ks == [(0.0, 2.0), (6.0, 10.0)]


def test_keeps_clamp_a_la_duracion_y_basura():
    ks = keeps_from_cuts([cut(-3, 1), cut(8, 99), {"start": "x"}, "nada", cut(6, 5)], 10.0)
    assert ks == [(1.0, 5.0), (6.0, 8.0)]


def test_keeps_todo_cortado():
    tl = Timeline(keeps_from_cuts([cut(0, 10)], 10.0), 10.0, fps=30)
    assert tl.keeps == [] and tl.final_duration == 0 and tl.n_frames() == 0
    assert tl.remap(3) is None and tl.remap_clamped(3) == 0.0 and tl.inverse(1) == 0.0


def test_mismo_criterio_que_pipeline():
    """Replica exacta del algoritmo de pipeline.render_from_plan con el plan real."""
    plan = load_plan_v1()
    src_dur = plan["duration"]
    cuts = sorted((c for c in plan["cuts"] if c.get("enabled", True)), key=lambda c: c["start"])
    ref, t = [], 0.0
    for c in cuts:
        if c["start"] - t > 0.05:
            ref.append((t, c["start"]))
        t = max(t, c["end"])
    if src_dur - t > 0.05:
        ref.append((t, src_dur))
    assert keeps_from_cuts(plan["cuts"], src_dur) == ref


# ------------------------------------------------------------------ remap / inverse
@pytest.fixture
def tl():
    # keeps: [1,3) [4,6) [8,10) -> duracion final 6
    return Timeline([(1, 3), (4, 6), (8, 10)], 10.0)


def test_final_duration_y_offsets(tl):
    assert tl.final_duration == pytest.approx(6.0)
    assert tl.offsets == [0.0, 2.0, 4.0]
    assert tl.removed == pytest.approx(4.0)
    assert not tl.is_identity


def test_remap_en_keeps(tl):
    assert tl.remap(1.0) == pytest.approx(0.0)
    assert tl.remap(2.5) == pytest.approx(1.5)
    assert tl.remap(4.0) == pytest.approx(2.0)
    assert tl.remap(9.99) == pytest.approx(5.99)
    assert tl.remap(10.0) == pytest.approx(6.0)


def test_remap_en_cortes_es_none(tl):
    for t in (0.0, 0.5, 3.5, 7.0, 10.5, -1):
        assert tl.remap(t) is None, t


def test_remap_bordes_de_keep(tl):
    assert tl.remap(3.0) == pytest.approx(2.0)          # fin de keep = inicio del siguiente final
    assert tl.remap(3.0000001) is None


def test_remap_clamped(tl):
    assert tl.remap_clamped(0.2) == 0.0                   # antes del primer keep
    assert tl.remap_clamped(3.5) == pytest.approx(2.0)    # dentro de corte -> union
    assert tl.remap_clamped(7.0) == pytest.approx(4.0)
    assert tl.remap_clamped(50) == pytest.approx(6.0)     # despues del final
    assert tl.remap_clamped(5.0) == pytest.approx(3.0)


def test_inverse_ida_y_vuelta(tl):
    for tf in (0.0, 0.3, 1.99, 2.0, 2.5, 3.9999, 4.0, 5.5):
        to = tl.inverse(tf)
        assert tl.remap(to) == pytest.approx(tf, abs=1e-6), tf
    assert tl.inverse(2.0) == pytest.approx(4.0)          # en la union gana el keep siguiente
    assert tl.inverse(-1) == pytest.approx(1.0)
    assert tl.inverse(99) == pytest.approx(10.0)


def test_identity():
    t = Timeline(None, 7.5)
    assert t.is_identity and t.final_duration == 7.5 and t.joins == []
    assert t.remap(3.3) == pytest.approx(3.3) and t.inverse(3.3) == pytest.approx(3.3)


# ------------------------------------------------------------------ cuantizacion a cuadro
def test_cuantizacion_fps():
    t = Timeline([(1.07, 3.3), (4.34, 5.64)], 10.0, fps=30)
    for a, b in t.keeps:
        assert abs(a * 30 - round(a * 30)) < 1e-9 and abs(b * 30 - round(b * 30)) < 1e-9
    # expansiva: el inicio baja (floor) y el fin sube (ceil) -> nunca corta una cola de palabra
    assert t.frame_ranges() == [(32, 99), (130, 170)]
    assert t.n_frames() == 67 + 40
    for (a, b), (qa, qb) in zip([(1.07, 3.3), (4.34, 5.64)], t.keeps):
        assert qa <= a + 1e-9 and qb >= b - 1e-9 and qa > a - 1 / 30 and qb < b + 1 / 30
    assert t.final_duration * 30 == pytest.approx(t.n_frames())


def test_cuantizacion_keep_subcuadro_y_fragmentos():
    # sin min_frag un keep de 10 ms se expande a 1 cuadro; con min_frag se absorbe en el corte
    t = Timeline([(1.0, 1.01), (2.0, 3.0)], 5.0, fps=30)
    assert t.frame_ranges() == [(30, 31), (60, 90)]
    t = Timeline([(1.0, 1.01), (2.0, 3.0)], 5.0, fps=30, min_frag=0.15)
    assert t.keeps == [(2.0, 3.0)]


def test_fragmento_entre_cortes_se_absorbe():
    """cuts (2,3) y (3.06,4): el keep de 60 ms seria 2 cuadros + un blip de audio."""
    plan = {"duration": 10.0, "cuts": [cut(2, 3), cut(3.06, 4)]}
    t = Timeline.from_plan(plan, fps=30)
    assert t.keeps == [(0.0, 2.0), (4.0, 10.0)] and t.n_frames() == 240
    t0 = Timeline.from_plan(plan, fps=30, min_frag=0)
    assert len(t0.keeps) == 3 and t0.frame_ranges()[1] == (90, 92)
    # nunca se queda sin nada: si todos son fragmentos se conserva el mas largo
    t1 = Timeline([(1.0, 1.05), (2.0, 2.12)], 5.0, fps=30, min_frag=0.15)
    assert t1.frame_ranges() == [(60, 64)]


def test_union_pegada_al_cambio_de_toma():
    shots = [{"start": 0.0, "end": 5.0}, {"start": 5.0, "end": 10.0}]
    # el keep empieza 1 cuadro ANTES del cambio de toma: sin el ajuste mostraria 1 cuadro suelto
    # de la toma anterior justo despues de la union
    plan = {"duration": 10.0, "cuts": [cut(3.0, 4.97)], "shots": shots}
    t = Timeline.from_plan(plan, fps=30)
    assert t.frame_ranges() == [(0, 90), (150, 300)]
    assert Timeline.from_plan(plan, fps=30, snap_shots=False).frame_ranges() == [(0, 90), (149, 300)]
    assert t.join_frames() == [90] and t.shot_change_frames(shots) == [90]
    # el keep TERMINA 1.5 cuadros despues del cambio: esos cuadros de la toma siguiente se quitan
    plan = {"duration": 10.0, "cuts": [cut(5.05, 7.0)], "shots": shots}
    t = Timeline.from_plan(plan, fps=30)
    assert t.frame_ranges() == [(0, 150), (210, 300)]
    # tiempos de toma redondeados a ms (4.9997 ~ cuadro 150) y cambios lejos de la union: intactos
    shots2 = [{"start": 0.0}, {"start": 4.9997}, {"start": 8.0}]
    t = Timeline.from_plan({"duration": 10.0, "cuts": [cut(3.0, 4.97)], "shots": shots2}, fps=30)
    assert t.frame_ranges() == [(0, 90), (150, 300)]
    assert t.shot_change_frames(shots2) == [90, 180]
    # a 60 fps la tolerancia sigue siendo ~0.12 s
    t = Timeline.from_plan({"duration": 10.0, "cuts": [cut(3.0, 4.9)], "shots": shots}, fps=60)
    assert t.frame_ranges() == [(0, 180), (300, 600)]


def test_to_dict_ida_y_vuelta_con_ajustes():
    plan = {"duration": 10.0, "cuts": [cut(1.013, 2.02), cut(3.0, 4.97), cut(6.0, 6.07)],
            "shots": [{"start": 0.0}, {"start": 5.0}]}
    t = Timeline.from_plan(plan, fps=30)
    d = Timeline.from_dict(t.to_dict())
    assert d.keeps == t.keeps and d.frame_ranges() == t.frame_ranges()
    assert d.n_frames() == t.n_frames() == sum(b - a for a, b in t.frame_ranges())


def test_from_plan_real():
    plan = load_plan_v1()
    t = Timeline.from_plan(plan, fps=30)
    assert len(t.keeps) == 6
    # cuantizacion expansiva: cada keep contiene su tramo original y agrega < 1 cuadro por lado
    ref = keeps_from_cuts(plan["cuts"], plan["duration"])
    for (a, b), (qa, qb) in zip(ref, t.keeps):
        assert qa <= a + 1e-9 and b - 1e-9 <= qb and a - qa < 1 / 30 and qb - b < 1 / 30
    assert t.n_frames() == 951
    assert t.final_duration == pytest.approx(31.7)
    d = Timeline.from_dict(t.to_dict())
    assert d.keeps == t.keeps and d.final_duration == t.final_duration


# ------------------------------------------------------------------ uniones y tomas
def test_joins(tl):
    j = tl.joins
    assert [x["t"] for x in j] == [pytest.approx(2.0), pytest.approx(4.0)]
    assert j[0]["before"] == 3 and j[0]["after"] == 4 and j[0]["gap"] == pytest.approx(1)
    assert j[1]["before"] == 6 and j[1]["after"] == 8


def test_shot_changes(tl):
    shots = [{"start": 0, "end": 2}, {"start": 2, "end": 3.5}, {"start": 3.5, "end": 9},
             {"start": 9, "end": 10}]
    # 2.0 -> 1.0 ; 3.5 (en corte) -> union 2.0 ; 9 -> 5.0
    assert tl.shot_changes(shots) == [pytest.approx(1.0), pytest.approx(2.0), pytest.approx(5.0)]


def test_shot_changes_descarta_bordes_y_duplicados(tl):
    shots = [{"start": 0, "end": 1}, {"start": 0.5, "end": 3.2}, {"start": 3.2, "end": 3.6},
             {"start": 3.6, "end": 10}]
    # 0.5 -> 0 (borde, fuera); 3.2 y 3.6 caen en el mismo corte -> una sola union
    assert tl.shot_changes(shots) == [pytest.approx(2.0)]
    assert tl.shot_changes([]) == [] and tl.shot_changes(None) == []


def test_map_shots(tl):
    shots = [{"start": 0, "end": 3.5, "src": "a"}, {"start": 3.5, "end": 3.9, "src": "b"},
             {"start": 3.9, "end": 10, "src": "c"}]
    ms = tl.map_shots(shots)
    assert [s["src"] for s in ms] == ["a", "c"]           # b queda entero en un corte
    assert ms[0]["start"] == 0 and ms[0]["end"] == pytest.approx(2.0)
    assert ms[1]["start"] == pytest.approx(2.0) and ms[1]["end"] == pytest.approx(6.0)
    assert ms[1]["index"] == 2


# ------------------------------------------------------------------ rangos e items
def test_map_range_dentro_y_cruzando(tl):
    assert tl.map_range(1.5, 2.5) == (pytest.approx(0.5), pytest.approx(1.5))
    # cruza el corte [3,4): se colapsa
    assert tl.map_range(2.5, 4.5) == (pytest.approx(1.5), pytest.approx(2.5))
    # cruza dos cortes
    assert tl.map_range(2.0, 9.0) == (pytest.approx(1.0), pytest.approx(5.0))


def test_map_range_en_corte_y_bordes(tl):
    assert tl.map_range(3.2, 3.8) is None                 # entero en un corte
    assert tl.map_range(0.0, 0.9) is None                 # antes del primer keep
    assert tl.map_range(10.2, 12) is None                 # despues del final
    assert tl.map_range(0.0, 1.5) == (0.0, pytest.approx(0.5))   # empieza en corte inicial
    assert tl.map_range(3.5, 4.5) == (pytest.approx(2.0), pytest.approx(2.5))
    assert tl.map_range(2.5, 1.5) == (pytest.approx(0.5), pytest.approx(1.5))  # invertido


def test_map_item(tl):
    assert tl.map_item({"start": 1.5, "end": 2.5}) == (pytest.approx(0.5), pytest.approx(1.5))
    assert tl.map_item({"at": 3.5, "dur": 0.4}) == (pytest.approx(2.0), pytest.approx(2.4))
    assert tl.map_item({"foo": 1}) is None
    assert tl.map_item({"start": "x", "end": 2}) is None


def test_map_words(tl):
    phrases = [
        [{"text": "hola", "start": 1.1, "end": 1.4, "kw": True},
         {"text": "cortada", "start": 3.2, "end": 3.6}],            # en corte -> fuera
        [{"text": "cruza", "start": 2.8, "end": 4.2}],               # se recorta
        [{"text": "solo", "start": 7.0, "end": 7.5}],                # frase entera fuera
        [{"text": "fin", "start": 9.5, "end": 10.4}],
    ]
    out = tl.map_words(phrases)
    assert len(out) == 3
    assert out[0] == [{"text": "hola", "start": pytest.approx(0.1), "end": pytest.approx(0.4), "kw": True}]
    assert out[1][0]["start"] == pytest.approx(1.8) and out[1][0]["end"] == pytest.approx(2.2)
    assert out[2][0]["end"] == pytest.approx(6.0)
    # tiempos monotonos
    flat = [w for ph in out for w in ph]
    assert all(a["start"] <= b["start"] for a, b in zip(flat, flat[1:]))


def test_map_words_plan_real_sin_palabras_en_cortes():
    plan = load_plan_v1()
    t = Timeline.from_plan(plan, fps=30)
    out = t.map_words(plan["phrases"])
    n_in = sum(len(p) for p in plan["phrases"])
    n_out = sum(len(p) for p in out)
    assert 0.9 * n_in <= n_out <= n_in
    for ph in out:
        for w in ph:
            assert 0 <= w["start"] < w["end"] <= t.final_duration + 1e-6


def test_frame_of():
    t = Timeline(None, 2.0, fps=30)
    assert t.frame_of(0.0) == 0 and t.frame_of(1 / 30) == 1 and t.frame_of(0.999) == 29
