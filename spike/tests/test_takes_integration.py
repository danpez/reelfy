"""Limpieza de tomas integrada: shorts (highlights) sobre las palabras que se quedan y medidos en
tiempo FINAL, director (guion sin las palabras cortadas, tema de plan.takes, recortes IA con cortes
de tomas presentes, re-dirigir tras restaurar una toma), pipeline.analyze de punta a punta (con
las piezas pesadas simuladas) y resumen de proyectos (jobstore.plan_meta). Datos inventados."""
import json
import re
import sys
import types

import pytest

import highlights as hl
import takes as T
from motion import director as D

from test_takes import words_of


# ------------------------------------------------------------------ shorts (highlights)
def _sent_words(n_sents, per=8, t0=0.5, gap=0.6):
    """n oraciones inventadas de `per` palabras (0.35 s c/u)."""
    ws, t = [], t0
    for s in range(n_sents):
        for k in range(per):
            txt = f"palabra{s}x{k}" + ("." if k == per - 1 else "")
            ws.append({"text": txt, "start": round(t, 3), "end": round(t + 0.3, 3)})
            t += 0.35
        t += gap
    return ws


def test_short_se_mide_en_tiempo_final_y_no_termina_antes_de_una_toma(monkeypatch):
    ws = _sent_words(30)                                  # ~3.4 s por oracion
    sents_src = hl.build_sentences(ws)
    # toma cortada justo despues de la oracion 12 (la retoma de lo que se dice en la 13)
    s12, s13 = sents_src[12], sents_src[13]
    take = {"start": s12["end"] + 0.05, "end": s13["start"] + 1.4, "enabled": True, "src": "takes",
            "kind": "retoma", "level": "alta", "id": "tk_x"}
    # silencios largos cortados dentro del rango: el rango fuente mide > 60 s pero en final < 60
    sil = [{"start": sents_src[k]["end"] + 0.05, "end": sents_src[k]["end"] + 0.55, "enabled": True}
           for k in range(0, 30)]
    cuts = sil + [take]
    eff = T.effective_words(ws, cuts)
    sents = hl.build_sentences(eff, cuts=cuts)
    by_start = {round(s["start"], 2): s["i"] for s in sents}
    a = by_start[round(sents_src[2]["start"], 2)]
    b = next(s["i"] for s in sents if abs(s["end"] - s12["end"]) < 0.01)
    monkeypatch.setattr(hl, "_ollama_chat", lambda system, user, fmt="json": json.dumps(
        {"clips": [{"from_i": a, "to_i": b, "title": "Gancho", "reason": "x"}]}))
    clips = hl.select_highlights(sents, n=1, cuts=cuts, duration=ws[-1]["end"] + 1)
    assert len(clips) == 1
    c = clips[0]
    iv = hl._cut_iv(cuts)
    take_iv = hl._cut_iv(cuts, takes_only=True)
    # nunca termina dentro de una toma cortada ni justo antes de una
    assert hl._end_ok(c["end"], take_iv)
    assert not any(s <= c["end"] < e for s, e in take_iv)
    assert c["end"] > take["end"]                          # incluye la toma buena que sigue
    L = hl.final_len(c["start"], c["end"], iv)
    assert hl.MIN_SEC <= L <= hl.MAX_SEC
    # el prompt lleva tiempos FINALES y num_ctx va en la llamada al LLM
    seen = {}

    def spy(system, user, fmt="json"):
        seen["user"] = user
        return json.dumps({"clips": []})
    monkeypatch.setattr(hl, "_ollama_chat", spy)
    hl.select_highlights(sents, n=1, cuts=cuts)
    last_line = seen["user"].split("\n")[1 + len(sents) - 1]
    m = re.search(r"\(([\d.]+)-([\d.]+)s\)", last_line)
    assert float(m.group(2)) == pytest.approx(hl.final_time(sents[-1]["end"], iv), abs=0.06)
    assert hl._num_ctx("x" * 100, "y" * 100) == 4096 and hl._num_ctx("x", "y" * 20000) == 8192


def test_short_no_termina_antes_de_una_toma_sugerida(monkeypatch):
    """Una toma SUGERIDA (apagada, nivel baja) justo despues del final elegido: los shorts no se
    recalculan cuando el usuario sube el nivel o la enciende, asi que el final tampoco puede caer
    antes de ella (antes solo contaban las tomas habilitadas y el short acababa en la retoma)."""
    ws = _sent_words(30)
    sents_src = hl.build_sentences(ws)
    s12, s13 = sents_src[12], sents_src[13]
    take = {"start": s12["end"] + 0.05, "end": s13["start"] + 1.4, "enabled": False, "src": "takes",
            "kind": "retoma", "level": "baja", "id": "tk_sug"}
    sil = [{"start": sents_src[k]["end"] + 0.05, "end": sents_src[k]["end"] + 0.55, "enabled": True}
           for k in range(0, 30)]
    cuts = sil + [take]
    sents = hl.build_sentences(T.effective_words(ws, cuts), cuts=cuts)
    a = next(s["i"] for s in sents if abs(s["start"] - sents_src[4]["start"]) < 0.01)
    b = next(s["i"] for s in sents if abs(s["end"] - s12["end"]) < 0.01)
    monkeypatch.setattr(hl, "_ollama_chat", lambda system, user, fmt="json": json.dumps(
        {"clips": [{"from_i": a, "to_i": b, "title": "Gancho", "reason": "x"}]}))
    c = hl.select_highlights(sents, n=1, cuts=cuts)[0]
    assert not (take["start"] - hl.TAKE_NEAR <= c["end"] < take["end"]), c
    # el usuario enciende la sugerencia: el short sigue sin terminar dentro ni antes de la retoma
    on = [dict(x, enabled=True) if x is take else x for x in cuts]
    assert hl._end_ok(c["end"], hl._cut_iv(on, takes_only=True))
    assert hl.MIN_SEC <= hl.final_len(c["start"], c["end"], hl._cut_iv(on)) <= hl.MAX_SEC


def test_short_sin_llm_respeta_las_tomas(monkeypatch):
    ws = _sent_words(40)
    sents_src = hl.build_sentences(ws)
    mid = len(sents_src) // 2                             # el ancla del unico short cae aqui
    takes = [{"start": sents_src[k]["end"] + 0.05, "end": sents_src[k + 1]["start"] + 1.0,
              "enabled": True, "src": "takes"} for k in range(mid, mid + 12)]
    eff = T.effective_words(ws, takes)
    sents = hl.build_sentences(eff, cuts=takes)

    def down(*a, **k):
        raise OSError("sin ollama")
    monkeypatch.setattr(hl, "_ollama_chat", down)
    clips = hl.select_highlights(sents, n=1, cuts=takes)
    assert clips and clips[0]["title"] == "Momento 1"
    iv = hl._cut_iv(takes)
    assert hl._end_ok(clips[0]["end"], iv)
    assert hl.final_len(clips[0]["start"], clips[0]["end"], iv) <= hl.MAX_SEC


def test_oraciones_miden_duracion_final():
    ws = _sent_words(1, per=30)                            # una oracion de ~10.5 s fuente
    cut = [{"start": ws[5]["end"] + 0.01, "end": ws[20]["start"] - 0.01, "enabled": True, "src": "takes"}]
    eff = T.effective_words(ws, cut)
    assert len(hl.build_sentences(eff, max_sec=6)) == 2     # sin cortes: por la duracion fuente
    assert len(hl.build_sentences(eff, max_sec=6, cuts=cut)) == 1


# ------------------------------------------------------------------ director
def _plan_v1(words, cuts, **kw):
    phrases = [words[i:i + 3] for i in range(0, len(words), 3)]
    p = {"stem": "t", "video": "v.mp4", "duration": round(words[-1]["end"] + 1.0, 2), "dims": [1080, 1920],
         "phrases": [[dict(w) for w in ph] for ph in phrases], "cuts": cuts, "highlights": [], "beats": []}
    p.update(kw)
    return p


def test_director_guion_sin_palabras_cortadas():
    w = words_of("Hola a todos. |0.6| Agenda tu cita al, |0.9| agenda tu cita al número que aparece en "
                 "pantalla. |1.0| Gracias por vernos.")
    tk = T.detect(w, w[-1]["end"] + 1)["cuts"]
    assert tk
    k = next(i for i, x in enumerate(w) if x["text"] == "Gracias")
    w[k]["start"] = round(w[k]["start"] - 0.95, 3)            # 'Gracias' estirada sobre la pausa
    sil = {"start": w[k]["start"] + 0.1, "end": w[k]["end"] - 0.3, "enabled": True}   # su midpoint cae aqui
    ai = {"start": w[0]["start"], "end": w[1]["end"], "enabled": True, "src": "ai", "id": "cut_ai_1"}
    manual = {"start": w[-1]["start"] - 0.01, "end": w[-1]["end"] + 0.01, "enabled": True, "manual": True}
    plan = _plan_v1(w, [sil] + tk + [ai, manual])
    got = " ".join(x["text"] for x in D._flatten_words(plan))
    # fuera: la toma repetida y la palabra del corte manual; dentro: la estirada que cruza un
    # silencio y lo que cubre un recorte de la IA (lo propone el director despues del guion)
    assert got == "Hola a todos. agenda tu cita al número que aparece en pantalla. Gracias por"
    # restaurar la toma devuelve sus palabras
    plan2 = _plan_v1(w, [sil] + [dict(c, enabled=False) for c in tk])
    assert "Agenda tu cita al," in " ".join(x["text"] for x in D._flatten_words(plan2))


def test_oraciones_del_guion_no_se_parten_donde_se_quito_una_toma():
    w = words_of("Lo horneamos durante 40, |0.5| no, |0.5| 50 minutos a fuego medio. |0.7| Y listo.")
    tk = T.detect(w, w[-1]["end"] + 1)["cuts"]
    assert [c["text"] for c in tk] == ["40, no,"]
    sents = D._sentences(D._flatten_words(_plan_v1(w, tk)))
    assert [s["text"] for s in sents] == ["Lo horneamos durante 50 minutos a fuego medio.", "Y listo."]
    # sin el corte (restaurado), la pausa real si cuenta como antes
    sents = D._sentences(D._flatten_words(_plan_v1(w, [dict(c, enabled=False) for c in tk])))
    assert sents[0]["text"].startswith("Lo horneamos durante 40,")


def test_tema_del_director_viene_de_la_limpieza():
    w = words_of("Hola, soy de Panadería La Espiga. |0.6| Hoy les enseño pan de elote. |0.6| Lleva harina y elote.")
    plan = _plan_v1(w, [], takes={"version": 1, "topic": "Pan de elote casero"})
    sc = D.analyze_script(plan, [], use_llm=False, llm_result={"topic": "Panadería La Espiga", "ent": {}, "f": []})
    assert sc["topic"] == "Pan de elote casero"
    sc2 = D.analyze_script(_plan_v1(w, []), [], use_llm=False, llm_result={"topic": "Pan", "ent": {}, "f": []})
    assert sc2["topic"] == "Pan"
    # re-dirigir (guion guardado de antes): el tema de la limpieza tambien manda
    pub = D._script_public(sc2)
    assert D._script_from_public(plan, pub, [])["topic"] == "Pan de elote casero"
    assert D._script_from_public(_plan_v1(w, []), pub, [])["topic"] == "Pan"
    # juicios de valor fuera; un tema larguisimo no se usa
    assert D._takes_topic({"takes": {"topic": "Pan barato de elote"}}) == "Pan de elote"
    assert D._takes_topic({"takes": {"topic": "uno dos tres cuatro cinco seis siete"}}) == ""


def test_recortes_ia_con_cortes_de_tomas_presentes():
    w = words_of("Agenda tu cita al, |0.9| agenda tu cita al número. |4.0| Te esperamos en la clínica.")
    tk = T.detect(w, w[-1]["end"] + 3)["cuts"]
    assert tk and all(c["src"] == "takes" for c in tk)
    plan = _plan_v1(w, tk)
    words = D._flatten_words(plan)
    shots = [{"start": 0.0, "end": plan["duration"], "use": "a_camara"}]
    trims = D.propose_trims(plan, shots, words)
    assert trims and all(c["src"] == "ai" for c in trims)
    assert any(c["reason"] == "silencio largo" for c in trims)
    # con recortes 'ai' ya presentes no se proponen otros (el usuario pudo haberlos apagado)
    assert D.propose_trims(_plan_v1(w, tk + trims), shots, words) == []


def test_redirigir_tras_restaurar_una_toma_conserva_lo_de_la_ia():
    w = words_of("Hola a todos. |0.6| Agenda tu cita al, |0.9| agenda tu cita al número que aparece en "
                 "pantalla. |1.0| Nuestros tratamientos son rápidos y seguros. |0.8| Gracias por vernos.")
    tk = T.detect(w, w[-1]["end"] + 1)["cuts"]
    plan = _plan_v1(w, tk)
    sc = D.analyze_script(plan, [], use_llm=False)
    pub = D._script_public(sc)
    for s in pub["sentences"]:
        s["tone"] = "urgente"                             # lo que habia decidido la IA
    last = pub["sentences"][-1]
    last["emph"] = [last["w0"]]
    # el usuario restaura la toma: cambian las palabras (y los indices) pero no esas frases
    plan2 = _plan_v1(w, [dict(c, enabled=False, user=True) for c in tk])
    sc2 = D._script_from_public(plan2, pub, [])
    by_text = {s["text"]: s for s in sc2["sents"]}
    kept = by_text["Gracias por vernos."]
    assert kept["info"]["tone"] == "urgente"
    assert kept["info"]["emph"] == [kept["w0"]]           # indice trasladado
    assert sc2["words"][kept["w0"]]["text"] == "Gracias"


# ------------------------------------------------------------------ pipeline.analyze (punta a punta)
def test_analyze_integra_la_limpieza_antes_de_los_shorts(monkeypatch, tmp_path):
    import pipeline as P
    src = ("Hola, bienvenidos. |0.6| Hoy les enseño pan de elote. |0.8| La masa lleva harina de, |1.1| "
           "la masa lleva harina de maíz y un poco de mantequilla. |0.7| El molde va engrasado. |0.7| "
           "Horneamos cuarenta minutos. |0.7| El kit trae 19, no, 16 piezas. |0.7| Y listo, a disfrutar.")
    words = words_of(src)
    dur = round(words[-1]["end"] + 1.0, 2)
    steps = []
    monkeypatch.setattr(P, "extract_audio", lambda v, w: None)
    monkeypatch.setattr(P, "transcribe", lambda *a, **k: tmp_path / "t.json")
    monkeypatch.setattr(P, "load_words", lambda jp: [dict(x) for x in words])
    monkeypatch.setattr(P.align_mod, "align_words", lambda wav, ws: ws)
    monkeypatch.setitem(sys.modules, "captionsmart", types.SimpleNamespace(annotate=lambda ph: None))
    monkeypatch.setattr(P.edit_mod, "keep_segments", lambda wav, ws: ([(0.0, dur)], dur))
    monkeypatch.setattr(P.edit_mod, "emphasis_beats", lambda wav: ([1.0], dur))
    monkeypatch.setattr(P.broll_mod, "suggest", lambda sents, d, topic="": [{"query": "bread", "topic": topic}])
    monkeypatch.setattr(P.reframe_track, "build", lambda *a, **k: (None, None, None, []))
    monkeypatch.setattr(P, "probe_dims", lambda v: (1080, 1920))
    monkeypatch.setattr(P, "probe_duration", lambda v: dur)
    monkeypatch.setattr(T, "topic", lambda ws, timeout=T.TOPIC_TIMEOUT: {
        "topic": "Pan de elote", "summary": "Receta.", "sections": [{"start": 0.5, "title": "Inicio"}]})
    seen = {}

    def fake_llm(system, user, fmt="json"):
        seen["user"] = user
        return json.dumps({"clips": [{"from_i": 0, "to_i": 1, "title": "Pan", "reason": "r"}]})
    monkeypatch.setattr(P.hl, "_ollama_chat", fake_llm)
    plan = P.analyze(tmp_path / "video.mp4", on_step=lambda p, m: steps.append((p, m)))
    labels = [m for _, m in steps]
    assert "Detectando silencios…" in labels and "Buscando tomas repetidas y errores…" in labels
    assert not any("muletillas" in m for m in labels)
    assert labels.index("Buscando tomas repetidas y errores…") < labels.index("Eligiendo los mejores momentos (IA)…")
    assert [p for p, _ in steps] == sorted(p for p, _ in steps)          # barra monotona
    tk = [c for c in plan["cuts"] if c.get("src") == "takes"]
    assert {c["kind"] for c in tk} >= {"retoma", "autocorreccion"}
    assert plan["takes"]["topic"] == "Pan de elote" and plan["takes"]["llm"] is True
    assert plan["topic"] == "Pan de elote" and plan["takes"]["stats"]["enabled"] == len(tk)
    assert plan["broll"][0]["topic"] == "Pan de elote"                  # el stock se ancla al tema
    # los shorts se eligen sobre lo que se queda: la toma quitada no esta en el prompt
    assert "harina de," not in seen["user"] and "19, no," not in seen["user"]
    h = plan["highlights"][0]
    assert h["enabled"] and not any(c["start"] <= h["end"] < c["end"] for c in tk)
    # el plan se serializa y el director lo lee sin las palabras cortadas
    json.dumps(plan)
    got = " ".join(x["text"] for x in D._flatten_words(plan))
    assert "harina de, la masa" not in got and "19, no," not in got


def test_analyze_sigue_si_la_limpieza_falla(monkeypatch, tmp_path):
    import pipeline as P
    words = words_of("Hola a todos. |0.6| Hoy hablamos de pan.")
    dur = round(words[-1]["end"] + 1.0, 2)
    monkeypatch.setattr(P, "extract_audio", lambda v, w: None)
    monkeypatch.setattr(P, "transcribe", lambda *a, **k: tmp_path / "t.json")
    monkeypatch.setattr(P, "load_words", lambda jp: [dict(x) for x in words])
    monkeypatch.setattr(P.align_mod, "align_words", lambda wav, ws: ws)
    monkeypatch.setitem(sys.modules, "captionsmart", types.SimpleNamespace(annotate=lambda ph: None))
    monkeypatch.setattr(P.edit_mod, "keep_segments", lambda wav, ws: ([(0.0, dur)], dur))
    monkeypatch.setattr(P.edit_mod, "emphasis_beats", lambda wav: ([], dur))
    monkeypatch.setattr(P.broll_mod, "suggest", lambda *a, **k: [])
    monkeypatch.setattr(P.reframe_track, "build", lambda *a, **k: (None, None, None, []))
    monkeypatch.setattr(P, "probe_dims", lambda v: (1080, 1920))
    monkeypatch.setattr(P, "probe_duration", lambda v: dur)
    monkeypatch.setattr(P.hl, "_ollama_chat", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))

    def boom(*a, **k):
        raise RuntimeError("fallo inventado")
    monkeypatch.setattr(T, "detect", boom)
    plan = P.analyze(tmp_path / "video.mp4")
    assert "takes" not in plan and "topic" not in plan and isinstance(plan["cuts"], list)


# ------------------------------------------------------------------ resumen de proyectos
def test_plan_meta_resume_la_limpieza():
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
    import jobstore
    plan = {"version": 2, "duration": 20.0,
            "cuts": [{"start": 1, "end": 3}, {"start": 2, "end": 5, "src": "takes", "enabled": True},
                     {"start": 10, "end": 11, "src": "takes", "enabled": False}],
            "director": {"script": {"topic": "Pan de elote"}}}
    m = jobstore.plan_meta(plan)
    assert m["topic"] == "Pan de elote" and m["final_duration"] == 16.0
    assert m["takes"] == {"n": 2, "enabled": 1, "removed_s": 2.0}      # 2 s de mas sobre el silencio
    assert "takes" not in jobstore.plan_meta({"duration": 5.0, "cuts": [{"start": 1, "end": 2}]})
    assert jobstore.plan_meta({"duration": 5.0, "topic": "Tema del analisis"})["topic"] == "Tema del analisis"
