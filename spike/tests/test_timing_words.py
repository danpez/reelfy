"""Tiempos de palabras para subtitulos (motion.timeline): ninguna palabra DICHA se pierde.

Regresion del bug de la app instalada: sin el alineador fino, whisper deja palabras de
duracion 0 que Timeline.map_words descartaba (271 de 2541 palabras desaparecian del
subtitulo) y palabras estiradas segundos sobre una pausa. Fixtures sinteticos.
"""
import pytest

from motion.timeline import (STRETCH_FACTOR, STRETCH_MIN_S, WORD_MIN, Timeline, est_word_dur,
                             fix_word_times)


def W(text, s, e, **kw):
    return {"text": text, "start": s, "end": e, **kw}


def _check_sane(ws, lo=0.0, hi=None, min_dur=WORD_MIN):
    for a, b in zip(ws, ws[1:]):
        assert a["start"] <= b["start"] + 1e-9, (a, b)
        assert a["end"] <= b["start"] + 1e-9, ("traslape", a, b)
    for w in ws:
        assert w["end"] - w["start"] >= min_dur - 1e-6, w
        assert w["start"] >= lo - 1e-9
        if hi is not None:
            assert w["end"] <= hi + 1e-9


# ------------------------------------------------------------------ duracion estimada
def test_est_word_dur_por_letras_y_cifras():
    assert est_word_dur("y") == pytest.approx(WORD_MIN)          # minimo visible
    assert est_word_dur("casa") == pytest.approx(0.26)
    assert est_word_dur("lentejas,") == pytest.approx(0.52)      # los signos no cuentan
    assert est_word_dur("2024") == pytest.approx(0.8)            # 'dos mil veinticuatro'
    assert est_word_dur("") == pytest.approx(WORD_MIN)
    assert est_word_dur("a" * 60) == pytest.approx(1.5)          # tope


# ------------------------------------------------------------------ fix_word_times
def test_racha_de_duracion_cero_toma_tiempo_de_la_siguiente_sin_perder_palabras():
    """Patron real de whisper crudo: dos palabras en 0 s pegadas a la siguiente."""
    ws = [W("preparamos", 6.65, 7.68), W("una", 8.32, 8.32), W("sopa", 8.32, 8.32),
          W("caliente", 8.32, 8.91), W("hoy", 8.91, 9.2)]
    out = fix_word_times(ws)
    assert [w["text"] for w in out] == [w["text"] for w in ws]
    _check_sane(out)
    # primero presta la palabra siguiente (hasta la mitad de lo que dura) y solo lo que falta
    # sale de la pausa anterior: las palabras de 0 s apenas entran en ella
    assert 8.0 <= out[1]["start"] < 8.32
    assert out[0] == ws[0]                                       # la pausa alcanzo: no presta
    assert out[3]["end"] == pytest.approx(8.91)
    assert out[3]["end"] - out[3]["start"] == pytest.approx(0.59 / 2)
    assert out[2]["end"] == pytest.approx(out[3]["start"])
    assert out[4] == ws[4]                                       # la vecina lejana ni se toca


def test_racha_cero_usa_el_hueco_siguiente_antes_que_a_las_vecinas():
    ws = [W("hola", 1.0, 1.3), W("que", 1.3, 1.3), W("tal", 1.3, 1.3), W("amigos", 2.5, 3.0)]
    out = fix_word_times(ws)
    _check_sane(out)
    assert out[0] == ws[0] and out[3] == ws[3]                   # vecinas intactas
    assert out[1]["start"] == pytest.approx(1.3)
    assert out[2]["end"] <= 2.5


def test_palabra_cero_al_final_del_video_toma_el_hueco_anterior():
    ws = [W("gracias", 3.0, 3.4), W("adios", 5.0, 5.0)]
    out = fix_word_times(ws, hi=5.0)
    _check_sane(out, hi=5.0)
    assert out[1]["end"] == pytest.approx(5.0)
    assert out[0] == ws[0]


def test_palabras_apretadas_sin_espacio_se_reparten_lo_que_haya():
    """Sin hueco ni vecinas que presten: no se pierde ninguna (duracion > 0, en orden)."""
    ws = [W("a", 1.0, 1.0), W("b", 1.0, 1.0), W("c", 1.0, 1.0)]
    out = fix_word_times(ws, lo=1.0, hi=1.0)
    assert len(out) == 3 and all(w["start"] == w["end"] == 1.0 for w in out)
    out = fix_word_times(ws, lo=0.0, hi=1.05)
    assert len(out) == 3
    assert all(w["end"] > w["start"] for w in out)
    assert all(a["end"] <= b["start"] + 1e-9 for a, b in zip(out, out[1:]))


def test_estirada_sobre_una_pausa_conserva_el_final_con_whisper_crudo():
    """'gente?' durando 6.8 s: whisper mete la pausa ANTES de la palabra."""
    ws = [W("toda", 10.0, 10.3), W("la", 10.3, 10.45), W("gente?", 10.45, 17.25),
          W("bueno", 17.4, 17.7)]
    out = fix_word_times(ws)
    _check_sane(out)
    g = out[2]
    assert g["end"] == pytest.approx(17.25)
    assert g["end"] - g["start"] == pytest.approx(1.3 * est_word_dur("gente?"))
    # keep='start' (DTW: la pausa queda DESPUES de la palabra)
    out = fix_word_times(ws, keep="start")
    assert out[2]["start"] == pytest.approx(10.45)
    assert out[2]["end"] - out[2]["start"] == pytest.approx(1.3 * est_word_dur("gente?"))


def test_palabra_larga_de_verdad_no_se_toca():
    lim = max(STRETCH_MIN_S, STRETCH_FACTOR * est_word_dur("extraordinariamente"))
    ws = [W("extraordinariamente", 1.0, 1.0 + lim - 0.01)]
    assert fix_word_times(ws) == ws
    ws = [W("mmm", 1.0, 1.9)]                                    # < 1 s: no es "estirada"
    assert fix_word_times(ws) == ws


def test_orden_traslapes_y_campos_extra_sin_mutar_la_entrada():
    ws = [W("uno", 1.0, 1.6, kw=True), W("dos", 1.4, 1.8, emoji="x"), W("tres", 1.2, 2.2)]
    snap = [dict(w) for w in ws]
    out = fix_word_times(ws)
    assert ws == snap                                            # copias
    assert out[0]["kw"] is True and out[1]["emoji"] == "x"
    _check_sane(out)
    assert out[0]["end"] == pytest.approx(1.4)                   # recortada al inicio de la siguiente
    assert fix_word_times([]) == []


def test_tiempos_invertidos_o_invalidos():
    out = fix_word_times([W("hola", 2.0, 1.5), {"text": "x", "start": "?", "end": None},
                          W("fin", 3.0, 3.4)])
    assert len(out) == 3
    _check_sane(out)


# ------------------------------------------------------------------ Timeline.map_words
def test_map_words_no_pierde_palabras_de_duracion_cero_en_tiempo_conservado():
    """El bug: map_range devolvia None (duracion 0) y la palabra desaparecia del subtitulo."""
    tl = Timeline.from_plan({"duration": 20.0, "cuts": [{"start": 5.0, "end": 8.0, "enabled": True}]},
                            fps=30)
    phrases = [[W("vamos", 1.0, 1.4), W("a", 1.4, 1.4), W("ver", 1.4, 1.4)],
               [W("esto", 6.0, 6.0)],                             # 0 s dentro del corte: se va
               [W("ahora", 9.0, 9.4), W("si", 9.4, 9.4)]]
    out = Timeline.map_words(tl, phrases)
    flat = [w for ph in out for w in ph]
    assert [w["text"] for w in flat] == ["vamos", "a", "ver", "ahora", "si"]
    assert len(out) == 2                                         # la frase del corte se va entera
    _check_sane(flat, hi=tl.final_duration)
    assert flat[3]["start"] == pytest.approx(6.0)                # 9.0 - 3 s de corte


def test_map_words_plan_con_timing_crudo_conserva_todas_las_palabras_dichas():
    """Plan sintetico con el patron de la app instalada: ~1/3 de palabras de duracion 0 y
    varias estiradas. Todas las que caen en tiempo conservado deben salir."""
    phrases, t = [], 0.0
    for k in range(120):
        dur = 0.0 if k % 3 == 1 else (4.0 if k % 17 == 0 else 0.3)
        phrases.append([W(f"palabra{k}", round(t, 2), round(t + dur, 2))])
        t += dur + (0.0 if dur == 0.0 else 0.05)
    cuts = [{"start": 10.0, "end": 12.0, "enabled": True}, {"start": 30.0, "end": 31.5, "enabled": True}]
    plan = {"duration": t + 1.0, "cuts": cuts}
    tl = Timeline.from_plan(plan, fps=30)
    expect = [w["text"] for ph in phrases for w in ph
              if tl.remap((w["start"] + w["end"]) / 2) is not None
              or (tl.map_range(w["start"], w["end"]) or (0, 0))[1] - (tl.map_range(w["start"], w["end"]) or (0, 0))[0] >= 0.01]
    out = tl.map_words(phrases)
    flat = [w for ph in out for w in ph]
    assert [w["text"] for w in flat] == expect
    assert len(flat) >= 0.9 * 120
    _check_sane(flat, hi=tl.final_duration, min_dur=0.0)
    assert sum(1 for w in flat if w["end"] - w["start"] < WORD_MIN - 1e-6) <= 2
    assert all(w["end"] - w["start"] <= max(STRETCH_MIN_S, STRETCH_FACTOR * est_word_dur(w["text"])) + 1e-6
               for w in flat)


def test_map_words_conserva_campos_y_estructura_de_frases():
    tl = Timeline(None, 10.0, fps=30)
    phrases = [[W("hola", 1.0, 1.3, kw=True, _oi=(0, 0)), W("mundo", 1.3, 1.3, _oi=(0, 1))],
               [W("adios", 5.0, 5.4, emoji="x", _oi=(1, 0))]]
    out = tl.map_words(phrases)
    assert [[w["_oi"] for w in ph] for ph in out] == [[(0, 0), (0, 1)], [(1, 0)]]
    assert out[0][0]["kw"] is True and out[1][0]["emoji"] == "x"
    assert out[0][1]["end"] - out[0][1]["start"] >= WORD_MIN - 1e-6
