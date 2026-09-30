"""Rampas de velocidad SOLO de video en los cambios de camara de "Armar con IA"
(scripts/montage.py, plan_ramps): la parte que sale acelera sus ultimos 0.30 s mostrando cuadros de
despues de su "out" (ramp_out) o la que entra empieza 0.30 s antes en su fuente a 2x hasta
alcanzar su audio (ramp_in). El audio siempre es [in, out] a 1x. Solo sesiones SINTETICAS
inventadas (repo publico)."""
import copy

import pytest

import montage as M
from test_montage import chosen, mk

RAMP = {"dur": 0.3, "speed": 2.0}


def W(*spans):
    return [{"text": f"w{k}", "start": a, "end": b} for k, (a, b) in enumerate(spans)]


def item(k, sid, a, b, join, setup, **kw):
    return dict({"slot": f"s{k:02d}", "cand": None, "sid": sid, "in": a, "out": b, "join": join,
                 "setup": setup}, **kw)


def plan(*items):
    return {"timeline": [dict(x) for x in items], "slots": []}


def ramps_of(m):
    """[(indice de la union, lado, dur)] de las rampas puestas."""
    tl = m["timeline"]
    out = []
    for k in range(1, len(tl)):
        if tl[k - 1].get("ramp_out"):
            out.append((k, "out", tl[k - 1]["ramp_out"]["dur"]))
        if tl[k].get("ramp_in"):
            out.append((k, "in", tl[k]["ramp_in"]["dur"]))
    return out


# Dos camaras, la misma frase: A dice [1.0-3.0] pausa [4.0-6.0]; B igual. Un empalme en la pausa.
A = {"sid": "A", "dur": 8.0, "words": W((1.0, 3.0), (4.0, 6.0))}
B = {"sid": "B", "dur": 8.0, "words": W((1.0, 3.0), (4.0, 6.0))}


def splice(a_out=3.4, b_in=3.6, a_clip=A, b_clip=B, join="splice"):
    m = plan(item(1, "A", 0.7, a_out, None, 0), item(2, "B", b_in, 6.3, join, 1))
    return m, [a_clip, b_clip]


# ------------------------------------------------------------------ lado de la rampa
def test_rampa_de_salida_cuando_la_parte_que_sale_termina_en_pausa():
    m, clips = splice(a_out=3.4, b_in=3.6)
    M.plan_ramps(m, clips)
    x0, x1 = m["timeline"]
    assert x0["ramp_out"] == RAMP and "ramp_in" not in x1
    assert x1["ramp"] is True and x1["ramp_auto"] is True and "ramp" not in x0
    # el audio no se toca: mismos in/out
    assert (x0["in"], x0["out"], x1["in"], x1["out"]) == (0.7, 3.4, 3.6, 6.3)


def test_rampa_de_entrada_cuando_la_que_sale_no_tiene_pausa():
    m, clips = splice(a_out=3.1, b_in=3.6)          # A corta 0.1 s tras su palabra
    M.plan_ramps(m, clips)
    assert ramps_of(m) == [(1, "in", 0.3)]
    assert m["timeline"][1]["ramp_in"] == RAMP


def test_sin_cuadros_extra_en_la_fuente_se_usa_el_otro_lado():
    a_short = {"sid": "A", "dur": 3.5, "words": A["words"][:1]}    # el archivo acaba 0.1 s tras "out"
    m, clips = splice(a_out=3.4, b_in=3.6, a_clip=a_short)
    M.plan_ramps(m, clips)
    assert ramps_of(m) == [(1, "in", 0.3)]
    b_early = {"sid": "B", "dur": 8.0, "words": W((0.3, 0.4), (0.5, 3.0))}
    m = plan(item(1, "A", 0.7, 3.05, None, 0), item(2, "B", 0.2, 3.3, "splice", 1))
    M.plan_ramps(m, [A, b_early])                   # B no tiene 0.3 s antes de su "in" ni pausa
    assert ramps_of(m) == [] and m["timeline"][1]["ramp"] is False


def test_los_cuadros_extra_no_pueden_tener_palabras():
    # tras el "out" de A viene otra palabra enseguida: su rampa mostraria labios moviendose
    a_talks = {"sid": "A", "dur": 8.0, "words": W((1.0, 3.0), (3.55, 6.0))}
    m, clips = splice(a_out=3.3, b_in=3.6, a_clip=a_talks)
    M.plan_ramps(m, clips)
    assert ramps_of(m) == [(1, "in", 0.3)]


def test_pausa_corta_rampa_mas_corta_y_sin_pausa_ninguna():
    # A: 0.2 s de aire tras su palabra; B empieza a hablar 0.2 s tras su "in"
    m = plan(item(1, "A", 0.7, 3.2, None, 0), item(2, "B", 3.8, 6.3, "splice", 1))
    M.plan_ramps(m, [A, B])
    assert ramps_of(m) == [(1, "out", 0.2)]
    m = plan(item(1, "A", 0.7, 3.12, None, 0), item(2, "B", 3.93, 6.3, "splice", 1))
    M.plan_ramps(m, [A, B])
    assert ramps_of(m) == [(1, "out", 0.15)]
    m = plan(item(1, "A", 0.7, 3.05, None, 0), item(2, "B", 3.95, 6.3, "splice", 1))
    M.plan_ramps(m, [A, B])
    assert ramps_of(m) == [] and m["timeline"][1]["ramp"] is False and m["timeline"][1]["ramp_auto"] is False


def test_prefiere_el_lado_donde_el_orador_sigue_actuando():
    """Las dos rampas caben, pero la toma que sale TERMINA ahi (una toma parcial: despues ya no
    habla, puede salirse del personaje) y la que entra viene de hablar: se rampa la que entra. Si
    las dos siguen actuando, la de salida."""
    a_ends = {"sid": "A", "dur": 5.0, "words": W((1.0, 3.0))}
    m, clips = splice(a_out=3.4, b_in=3.6, a_clip=a_ends)
    M.plan_ramps(m, clips)
    assert ramps_of(m) == [(1, "in", 0.3)]
    m, clips = splice(a_out=3.4, b_in=3.6)
    M.plan_ramps(m, clips)
    assert ramps_of(m) == [(1, "out", 0.3)]


def test_la_parte_debe_durar_mas_que_sus_rampas():
    """Una parte corta (0.45 s) no cabe una rampa de 0.30 s y ademas 0.30 s a 1x: la union usa el
    otro lado, o una rampa mas corta si es lo unico que cabe."""
    short_b = {"sid": "B", "dur": 8.0, "words": W((3.6, 3.62))}
    m = plan(item(1, "A", 0.7, 3.4, None, 0), item(2, "B", 3.6, 4.05, "splice", 1),
             item(3, "A", 3.7, 6.3, "splice", 0))
    M.plan_ramps(m, [A, short_b])
    assert ramps_of(m) == [(1, "out", 0.3), (2, "in", 0.3)]
    m = plan(item(1, "A", 0.7, 3.4, None, 0), item(2, "B", 3.6, 4.05, "splice", 1),
             item(3, "A", 3.9, 6.3, "splice", 0))          # la que entra casi no tiene aire
    M.plan_ramps(m, [A, short_b])
    assert ramps_of(m) == [(1, "out", 0.3), (2, "out", 0.15)]


# ------------------------------------------------------------------ donde va una rampa
@pytest.mark.parametrize("setups, joins, want", [
    ((0, 1), ("splice",), [1]),                         # cambio de camara a media linea
    ((0, 0), ("splice",), []),                          # misma camara: nunca
    ((0, 1), ("line",), []),                            # otra camara en linea nueva: corte seco
    ((0, 1), ("section",), []),
    ((None, None), ("splice",), []),                    # camaras desconocidas
    ((0, 1, 0), ("line", "section"), [2]),              # A/B/A: vuelve a la camara de hace 2
    ((0, 1, 2), ("line", "line"), []),
])
def test_rampas_solo_en_cambios_de_camara_del_material_intercalado(setups, joins, want):
    clips = [dict(A, sid=f"c{k}") for k in range(len(setups))]
    m = plan(*[item(k + 1, f"c{k}", *((0.7, 3.4) if k % 2 == 0 else (3.6, 6.3)), ([None] + list(joins))[k], s)
               for k, s in enumerate(setups)])
    M.plan_ramps(m, clips)
    assert [k for k, x in enumerate(m["timeline"]) if x.get("ramp")] == want


def test_partes_del_intercalado_en_linea_nueva_si_llevan_rampa():
    """Intercalado de angulos (flags "intercalado"/"parcial" del candidato elegido): el cambio de
    camara al empezar una linea tambien se suaviza."""
    m = plan(item(1, "A", 0.7, 3.4, None, 0, cand="s01c1"), item(2, "B", 3.6, 6.3, "line", 1, cand="s02c1"))
    m["slots"] = [{"id": "s01", "cands": [{"id": "s01c1", "sid": "A", "setup": 0, "flags": ["intercalado"]}]},
                  {"id": "s02", "cands": [{"id": "s02c1", "sid": "B", "setup": 1, "flags": ["intercalado"]}]}]
    M.plan_ramps(m, [A, B])
    assert ramps_of(m) == [(1, "out", 0.3)]
    m["slots"][1]["cands"][0]["flags"] = []
    M.plan_ramps(m, [A, B])
    assert ramps_of(m) == []


# ------------------------------------------------------------------ lo que el usuario decide
def test_el_usuario_gana_apagar_encender_y_volver_a_automatico():
    m, clips = splice()
    M.plan_ramps(m, clips, overrides={"s02": False})
    x0, x1 = m["timeline"]
    assert ramps_of(m) == [] and x1["ramp"] is False and x1["ramp_auto"] is True and x1["ramp_user"] is False
    M.plan_ramps(m, clips)                               # se queda lo que eligio
    assert ramps_of(m) == []
    M.plan_ramps(m, clips, overrides={1: None})          # vuelve a automatico
    assert ramps_of(m) == [(1, "out", 0.3)] and "ramp_user" not in m["timeline"][1]
    # encender donde el motor no la pondria (otra camara en linea nueva)
    m, clips = splice(join="line")
    M.plan_ramps(m, clips)
    assert ramps_of(m) == []
    m["timeline"][1]["ramp_user"] = True
    M.plan_ramps(m, clips)
    assert ramps_of(m) == [(1, "out", 0.3)] and m["timeline"][1]["ramp_auto"] is False


def test_encendida_sin_pausa_limpia_usa_el_lado_con_mas_aire():
    m = plan(item(1, "A", 0.7, 3.05, None, 0), item(2, "B", 3.85, 6.3, "line", 1))
    M.plan_ramps(m, [A, B], overrides={"1": True})
    assert ramps_of(m) == [(1, "in", 0.15)] and m["timeline"][1]["ramp"] is True
    # sin cuadros extra en ningun lado no hay rampa aunque la pida
    a0 = {"sid": "A", "dur": 3.1, "words": W((1.0, 3.0))}
    b0 = {"sid": "B", "dur": 8.0, "words": W((0.1, 3.0))}
    m = plan(item(1, "A", 0.7, 3.08, None, 0), item(2, "B", 0.02, 3.3, "line", 1, ramp_user=True))
    M.plan_ramps(m, [a0, b0])
    assert ramps_of(m) == [] and m["timeline"][1]["ramp"] is False


def test_recalcular_es_idempotente_y_limpia_rampas_viejas():
    m, clips = splice()
    m["timeline"][1]["ramp_in"] = {"dur": 0.9, "speed": 3.0}     # de un calculo anterior
    M.plan_ramps(m, clips)
    once = copy.deepcopy(m)
    M.plan_ramps(m, clips)
    assert m == once and ramps_of(m) == [(1, "out", 0.3)]
    assert M.plan_ramps({"timeline": []}, []) == [] and M.plan_ramps({}, None) == []


# ------------------------------------------------------------------ build de punta a punta
def ab_session(tmp):
    g = ("Cada mañana prendemos el horno a las cinco, amasamos con calma durante una hora completa, "
         "y a las siete ya huele a pan recién hecho en toda la calle.\nVisítanos este sábado.")
    src = ("Cada mañana prendemos el horno a las cinco, |0.6| amasamos con calma durante una hora "
           "completa, |0.6| y a las siete ya huele a pan recién hecho en toda la calle.")
    return g, [mk(tmp, "a1", src, 0, setup=0), mk(tmp, "b1", src, 1, setup=1),
               mk(tmp, "a2", "Visítanos este sábado.", 2, setup=0)]


def test_build_pone_rampas_en_el_intercalado_A_B_A(tmp_path):
    g, clips = ab_session(tmp_path)
    r = M.build(copy.deepcopy(clips), g)
    assert chosen(r, "L01") == ["a1", "b1", "a1"]
    tl = r["timeline"]
    assert [x.get("ramp") for x in tl] == [None, True, True, False]      # a1 -> a2: misma camara
    assert r["stats"]["ramps"] == 2
    for k in (1, 2):
        side = tl[k - 1].get("ramp_out") or tl[k].get("ramp_in")
        assert side == RAMP
        # la rampa cae en la pausa: ninguna palabra en lo acelerado ni en los cuadros extra
        p, x = tl[k - 1], tl[k]
        if p.get("ramp_out"):
            sid, lo, hi = p["sid"], p["out"] - 0.3, p["out"] + 0.3
        else:
            sid, lo, hi = x["sid"], x["in"] - 0.3, x["in"] + 0.3
        ws = next(d for d in clips if d["sid"] == sid)["words"]
        assert not any(w["end"] > lo + M.P["ramp_tol"] and w["start"] < hi - M.P["ramp_tol"] for w in ws)
    # apagar las rampas no cambia nada del audio ni de la eleccion
    r0 = M.build(copy.deepcopy(clips), g, ramps={"s02": False, "s03": False})
    assert [(x["sid"], x["in"], x["out"], x["start"]) for x in r0["timeline"]] == \
        [(x["sid"], x["in"], x["out"], x["start"]) for x in tl]
    assert r0["stats"]["ramps"] == 0 and [x.get("ramp_user") for x in r0["timeline"][1:3]] == [False, False]
    assert all(x["setup"] == c["cands"][0]["setup"] for x, c in zip(r0["timeline"], r0["slots"]))


def test_build_toma_parcial_de_otra_camara_rampa_la_que_entra(tmp_path):
    """Como en una linea partida entre dos camaras: la toma parcial termina (ya no habla) y la otra
    viene hablando: la que entra empieza antes en su fuente a 2x."""
    g = "Probamos muchas recetas antes de abrir, y la de elote fue la favorita de todos los vecinos."
    half = mk(tmp_path, "h1", "Probamos muchas recetas antes de abrir...", 0, setup=0)
    full = mk(tmp_path, "f1", "Probamos muchas recetas antes de abrir, |0.6| y la de elote fue la favorita "
                              "de todos los vecinos.", 1, setup=1)
    r = M.build([half, full], g)
    assert chosen(r, "L01") == ["h1", "f1"]
    x0, x1 = r["timeline"]
    assert x1["join"] == "splice" and x1["ramp_in"] == RAMP and "ramp_out" not in x0
    # recalcular tras un cambio del usuario da lo mismo que build
    tl = copy.deepcopy(r["timeline"])
    M.plan_ramps(r, [half, full])
    assert r["timeline"] == tl


def test_build_sin_camaras_conocidas_no_rampa_sola(tmp_path):
    """Linea partida entre dos clips: con camaras distintas lleva rampa; si no se sabe la camara
    (podria ser la misma: un salto) no se pone sola, aunque el usuario la puede pedir."""
    g = "Cada mañana prendemos el horno a las cinco, y a las siete ya huele a pan en toda la calle."
    mk2 = lambda s0, s1: [mk(tmp_path, "p1", "Cada mañana prendemos el horno a las cinco, |0.8| y a las siete ya huele",  # noqa: E731
                             0, setup=s0),
                          mk(tmp_path, "p2", "y a las siete ya huele a pan en toda la calle.", 1, setup=s1)]
    r = M.build(mk2(0, 1), g)
    assert [x["join"] for x in r["timeline"]] == [None, "splice"] and r["timeline"][1]["ramp"] is True
    r = M.build(mk2(None, None), g)
    assert [o["setup"] for o in r["order"]] == [None, None]
    assert r["timeline"][1]["ramp"] is False and r["timeline"][1]["ramp_auto"] is False
    r = M.build(mk2(None, None), g, ramps={"s02": True})
    assert r["timeline"][1]["ramp"] is True and r["timeline"][1]["ramp_user"] is True
    r = M.build(mk2(0, 0), g)
    assert r["timeline"][1]["ramp"] is False
