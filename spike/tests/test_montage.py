"""Armar con IA (scripts/montage.py): auto-montaje de una sesion de clips por guion.

Solo sesiones SINTETICAS inventadas (el repo es publico: nada de material de clientes). Cada clip
es una lista de palabras estilo Whisper (mini-DSL de test_takes: |0.9| = pausa explicita) y, si
se pide, un wav 16 kHz con "voz" (tono modulado) SOLO donde hay palabras y ruido muy bajo en el
resto. Historia de prueba: una panaderia de barrio."""
import json
import re
import time
import wave

import numpy as np
import pytest

import montage as M
from conftest import load_factor

SR = 16000
DAY = "2026-03-14T10:{:02d}:{:02d}-06:00"

GUION = """Hace tres años abrí una panadería pequeña en el centro.
Al principio vendíamos apenas veinte panes al día.

Todo cambió cuando empezamos a publicar videos del horno.
En un mes llegamos a 5 mil seguidores y las ventas se duplicaron.

Si quieres probar nuestro pan de elote, visítanos este sábado."""


# ------------------------------------------------------------------ utilidades
def words_of(src, t=0.6):
    """Palabras separadas por espacio; |0.9| = pausa explicita (s) antes de la siguiente.
    Duracion = 0.10 + 0.06 por letra; hueco 0.04 (+0.12 tras coma, +0.35 tras punto)."""
    out, pend = [], None
    for tok in re.findall(r"\|[0-9.]+\||[^\s|]+", src):
        if tok.startswith("|"):
            pend = float(tok.strip("|"))
            continue
        if out:
            prev = out[-1]["text"]
            gap = 0.04 + (0.12 if prev.endswith((",", ";", ":")) else 0.0) + \
                (0.35 if prev.endswith((".", "?", "!")) else 0.0) + (0.5 if prev.endswith("...") else 0.0)
            ts = t + (pend if pend is not None else gap)
        else:
            ts = t
        d = 0.10 + 0.06 * len(re.sub(r"[^\wáéíóúñü]", "", tok))
        out.append({"text": tok, "start": round(ts, 3), "end": round(ts + d, 3)})
        t = ts + d
        pend = None
    return out


def synth(words, dur, level_db=-20.0, seed=0, extra=()):
    rng = np.random.default_rng(seed)
    y = rng.normal(0, 10 ** (-66 / 20), int(dur * SR)).astype(np.float32)
    amp = 10 ** (level_db / 20) * np.sqrt(2)
    for a, b in [(w["start"], w["end"]) for w in words] + list(extra):
        i0, i1 = int(a * SR), int(b * SR)
        if i1 - i0 < 16:
            continue
        t = np.arange(i1 - i0) / SR
        env = np.clip(np.minimum(t, t[-1] - t) / 0.015, 0, 1)
        y[i0:i1] += (amp * np.sin(2 * np.pi * 170 * t) * (0.75 + 0.25 * np.sin(2 * np.pi * 4 * t)) * env
                     ).astype(np.float32)
    return y


def write_wav(path, y):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())


def mk(tmp, sid, src, minute=0, second=0, setup=None, level_db=-20.0, audio=True, tail=0.9, name=None):
    w = words_of(src) if src else []
    dur = round((w[-1]["end"] if w else 1.5) + tail, 3)
    d = {"sid": sid, "name": name or f"{sid}.MOV", "created": DAY.format(minute, second), "dur": dur,
         "words": w, "audio": None, "setup": setup, "takes": None}
    if audio and tmp is not None:
        p = tmp / f"{sid}.wav"
        write_wav(p, synth(w, dur, level_db, seed=sum(map(ord, sid))))
        d["audio"] = str(p)
    return d


def slot_of(r, sid=None, line=None):
    for s in r["slots"]:
        if (line is None or line in s["lines"]) and (sid is None or s["cands"][0]["sid"] == sid):
            return s
    return None


def chosen(r, line):
    """sids elegidos (en orden) para una linea del guion."""
    out = []
    for x in r["timeline"]:
        s = next(s for s in r["slots"] if s["id"] == x["slot"])
        if line in s["lines"]:
            out.append(x["sid"])
    return out


def tl_words(r, clips):
    """Palabras que suenan en la linea de tiempo, en orden."""
    by = {d["sid"]: d for d in clips}
    out = []
    for x in r["timeline"]:
        out += [w["text"] for w in by[x["sid"]]["words"] if x["in"] <= (w["start"] + w["end"]) / 2 <= x["out"]]
    return out


def norm_words(ws):
    return [M.TK.norm(w) for w in ws if M.TK.norm(w)]


def panaderia(tmp, audio=True):
    """Sesion base: tomas viejas y nuevas, un blooper con groseria, charla del equipo."""
    return [
        mk(tmp, "c1", "Hace tres años abrí una panadería en el centro. |0.7| Al principio vendíamos veinte panes.",
           0, setup=0, audio=audio),
        mk(tmp, "c2", "Hace tres años abrí una panadería pequeña en el centro. |0.7| Al principio vendíamos "
                      "apenas veinte panes al día.", 1, setup=0, audio=audio),
        mk(tmp, "c3", "Hace tres años abrí una panadería pequeña en el centro. |0.7| Al principio vendíamos "
                      "apenas, |0.3| puta madre.", 2, setup=0, audio=audio),
        mk(tmp, "c4", "Todo cambió cuando empezamos a publicar videos del horno. |0.7| En un mes llegamos a "
                      "cinco mil seguidores y las ventas se duplicaron.", 5, setup=1, audio=audio),
        mk(tmp, "c5", "¿Ya está grabando? |0.5| ¿Le muevo a la luz? |0.4| Sí, dale.", 6, setup=1,
           level_db=-34.0, audio=audio),
        mk(tmp, "c6", "Si quieres probar nuestro pan de elote, visítanos este sábado.", 8, setup=2, audio=audio),
    ]


# ------------------------------------------------------------------ guion y orden
def test_parse_script_secciones_lineas_y_parrafos_largos():
    p = M.parse_script(GUION)
    assert [x["id"] for x in p["lines"]] == ["L01", "L02", "L03", "L04", "L05"]
    assert p["sections"] == [{"id": 1, "lines": ["L01", "L02"]}, {"id": 2, "lines": ["L03", "L04"]},
                             {"id": 3, "lines": ["L05"]}]
    assert p["lines"][3]["text"] == "En un mes llegamos a 5 mil seguidores y las ventas se duplicaron."
    # varias lineas en blanco = una sola seccion nueva; espacios de sobra se limpian
    q = M.parse_script("\n\n  Uno   dos tres.\n\n\n\nCuatro cinco.\r\nSeis siete.\n")
    assert [x["text"] for x in q["lines"]] == ["Uno dos tres.", "Cuatro cinco.", "Seis siete."]
    assert [s["lines"] for s in q["sections"]] == [["L01"], ["L02", "L03"]]
    # un parrafo pegado muy largo se parte en oraciones
    largo = " ".join(["Esta es una oración de prueba con varias palabras para llenar."] * 6)
    assert len(M.parse_script(largo)["lines"]) == 6
    assert M.parse_script("") == {"sections": [], "lines": []} == M.parse_script(None)


def test_orden_de_grabacion_fecha_nombre_natural_y_subida():
    clips = [{"name": "IMG_10.MOV", "created": None}, {"name": "IMG_2.MOV", "created": None},
             {"name": "B.MOV", "created": "2026-03-14T16:00:05Z"},            # 10:00:05 en -06:00
             {"name": "A.MOV", "created": "2026-03-14T10:00:00-0600"},
             {"name": "C.MOV", "created": "2026-03-14T10:00:00-06:00"},       # misma hora que A
             {"name": "IMG_2.MOV", "created": None}]
    assert M.recording_order(clips) == [3, 4, 2, 1, 5, 0]


def test_glosario_nombres_propios_y_numeros_del_guion():
    t = ("Soy Ana y con mi equipo creamos Tienda Luna, una panadería en Monterrey.\n"
         "Vendimos 300 conchas y 20 mil pesos en Mercado Libre.")
    assert M.glossary_terms(t) == ["Ana", "Tienda Luna", "Monterrey", "300", "20 mil", "Mercado Libre"]
    assert M.glossary_terms('Probamos la app "Pan Rico" en la CDMX, con 15% de descuento.') == \
        ["Pan Rico", "CDMX", "15%"]
    assert M.glossary_terms("") == [] and M.glossary_terms(None) == []


# ------------------------------------------------------------------ con guion
def test_con_guion_toma_limpia_mas_reciente_orden_y_contrato(tmp_path):
    clips = panaderia(tmp_path)
    r = M.build(clips, GUION)
    # contrato
    assert set(r) == {"version", "order", "script", "slots", "timeline", "discarded", "uncovered", "extra",
                      "inserts", "stats", "ms", "screens", "broll_src"}
    assert r["broll_src"] == []                          # ningun video de apoyo (todas las tomas tienen voz)
    assert r["screens"] == []               # sin grabaciones de pantalla en esta sesion
    assert r["inserts"] == [] and all(L["inserts"] == [] for L in r["script"]["lines"])
    assert r["version"] == 1 and isinstance(r["ms"], int)
    assert [o["sid"] for o in r["order"]] == ["c1", "c2", "c3", "c4", "c5", "c6"]
    assert [o["rec"] for o in r["order"]] == list(range(6))
    assert set(r["order"][0]) == {"sid", "name", "rec", "created", "setup"}
    assert [o["setup"] for o in r["order"]] == [0, 0, 0, 1, 1, 2]
    assert r["script"]["source"] == "user" and r["script"]["text"] == GUION
    assert [x["id"] for x in r["script"]["lines"]] == ["L01", "L02", "L03", "L04", "L05"]
    ids = set()
    for s in r["slots"]:
        assert {"id", "lines", "text", "chosen", "cands"} <= set(s)
        assert s["chosen"] == s["cands"][0]["id"]
        for c in s["cands"]:
            # vol/level_db: nivel de voz medido (con audio); conf: la confianza si fuera la elegida
            assert set(c) == {"id", "sid", "in", "out", "w0", "w1", "text", "score", "fidelity", "coverage",
                              "flags", "reason", "setup", "vol", "level_db", "conf", "line_times"}
            assert set(c["line_times"]) <= set(s["lines"])
            assert c["id"] not in ids and c["in"] < c["out"] and 0 <= c["score"] <= 1
            assert c["vol"] > 0 and 0 <= c["conf"] <= 1
            assert isinstance(c["reason"], str) and c["reason"] and isinstance(c["setup"], int)
            ids.add(c["id"])
    t = 0.0
    for k, x in enumerate(r["timeline"]):
        keys = {"slot", "cand", "sid", "in", "out", "start", "vol", "join", "conf", "setup"}
        assert set(x) - {"ramp_in", "ramp_out"} == (keys if k == 0 else keys | {"ramp", "ramp_auto"})
        assert x["start"] == pytest.approx(t, abs=2e-3) and x["vol"] > 0
        assert (x["join"] is None) == (k == 0) and x["join"] in (None, "section", "line", "splice")
        t += x["out"] - x["in"]
    # eleccion: la toma completa y fiel mas reciente; el blooper y la version vieja no entran
    assert chosen(r, "L01") == ["c2"] and chosen(r, "L02") == ["c2"]
    assert chosen(r, "L03") == ["c4"] and chosen(r, "L04") == ["c4"] and chosen(r, "L05") == ["c6"]
    assert [x["join"] for x in r["timeline"]] == [None, "section", "section"]
    assert r["uncovered"] == [] and r["stats"]["coverage"] == 1.0
    said = " ".join(tl_words(r, clips))
    assert "puta" not in said and "grabando" not in said and "dale" not in said
    # "cinco mil" = "5 mil": fiel al guion
    s4 = slot_of(r, line="L04")
    assert s4["cands"][0]["fidelity"] == 1.0 and s4["cands"][0]["reason"].startswith("Toma completa y fiel")
    # la version vieja es alternativa con su motivo en espanol; el blooper no cubre las dos lineas
    # del slot (se corta a media L02), asi que no es una alternativa que pueda reemplazarlo
    s1 = slot_of(r, line="L01")
    assert s1["lines"] == ["L01", "L02"]
    alt = {c["sid"]: c for c in s1["cands"][1:]}
    assert set(alt) == {"c1"}
    assert "Le falta" in alt["c1"]["reason"] and "variante" in alt["c1"]["flags"]
    # descartes con motivo
    why = {(d["sid"], d["why"]) for d in r["discarded"]}
    assert ("c3", "blooper") in why and ("c5", "fuera_de_guion") in why and ("c1", "retoma") in why
    blo = next(d for d in r["discarded"] if d["sid"] == "c3" and d["why"] == "blooper")
    assert "puta" in blo["text"]


def test_bordes_con_aire_en_el_silencio_y_nunca_dentro_de_una_palabra(tmp_path):
    clips = panaderia(tmp_path)
    r = M.build(clips, GUION)
    by = {d["sid"]: d for d in clips}
    for s in r["slots"]:
        for c in s["cands"]:
            ws = by[c["sid"]]["words"]
            for edge in (c["in"], c["out"]):
                assert not any(w["start"] + 1e-3 < edge < w["end"] - 1e-3 for w in ws), (c, edge)
            first, last = ws[c["w0"]], ws[c["w1"]]
            assert c["in"] <= first["start"] and c["out"] >= last["end"]
            assert c["text"].split()[0] == first["text"] and c["text"].split()[-1] == last["text"]
    for x in r["timeline"]:
        ws = [w for w in by[x["sid"]]["words"] if x["in"] <= (w["start"] + w["end"]) / 2 <= x["out"]]
        pre, post = ws[0]["start"] - x["in"], x["out"] - ws[-1]["end"]
        # (la ULTIMA parte se queda hasta END_TAIL s: el cierre con el boton de seguir)
        post_max = M.END_TAIL + 1e-3 if x is r["timeline"][-1] else 0.40
        assert 0.18 <= pre <= 0.42 and 0.15 <= post <= post_max, (x, pre, post)


def test_la_ultima_parte_se_queda_para_el_cierre(tmp_path):
    """El video terminaba 0.31 s despues de la ultima palabra: el «Siguiendo» del CTA final casi no se veia
    (v6: Kevin le dio 0.9 s mas a mano). La ultima parte se queda hasta END_TAIL s en la toma (sin voz
    nueva ni pasar del archivo); las demas no cambian."""
    clips = panaderia(tmp_path)
    for d in clips:                                   # toma con 3 s de aire al final
        d["dur"] = round(d["words"][-1]["end"] + 3.0, 3)
    r = M.build(clips, GUION)
    x = r["timeline"][-1]
    ws = [w for w in next(d for d in clips if d["sid"] == x["sid"])["words"] if w["start"] < x["out"]]
    post = x["out"] - ws[-1]["end"]
    assert abs(post - M.END_TAIL) < 0.02, post
    s = next(s for s in r["slots"] if s["id"] == x["slot"])
    assert s["cands"][0]["out"] == x["out"]
    # una racha de voz sin transcribir despues de la ultima palabra ("Ah, perdón." que whisper no escribio):
    # el cierre se detiene antes de ella
    clips = panaderia(tmp_path)
    c6 = mk(tmp_path, "c6", "Si quieres probar nuestro pan de elote, visítanos este sábado. |0.6| Ah perdón ya.", 8,
            setup=2, tail=2.0)
    spoken = c6["words"]
    c6["words"] = [w for w in spoken if w["text"] not in ("Ah", "perdón", "ya.")]
    clips[-1] = c6
    r3 = M.build(clips, GUION)
    x3 = r3["timeline"][-1]
    assert x3["sid"] == "c6"
    ah = next(w for w in spoken if w["text"] == "Ah")
    assert c6["words"][-1]["end"] + 0.15 <= x3["out"] < ah["start"], (x3["out"], ah)
    # sin audio (solo texto) no se sabe si hay voz sin transcribir: no se alarga
    r2 = M.build(panaderia(None, audio=False), GUION)
    x2 = r2["timeline"][-1]
    assert x2["out"] - max(w["end"] for w in next(d for d in panaderia(None, audio=False) if d["sid"] == x2["sid"])["words"]
                           if w["start"] < x2["out"]) <= 0.27 + 1e-6


def test_sin_audio_funciona_solo_con_texto(tmp_path):
    clips = panaderia(None, audio=False)
    r = M.build(clips, GUION)
    assert chosen(r, "L01") == ["c2"] and chosen(r, "L05") == ["c6"] and r["uncovered"] == []
    assert all(x["vol"] == 1.0 for x in r["timeline"])
    by = {d["sid"]: d for d in clips}
    for x in r["timeline"]:
        ws = by[x["sid"]]["words"]
        assert not any(w["start"] + 1e-3 < e < w["end"] - 1e-3 for w in ws for e in (x["in"], x["out"]))
        inside = [w for w in ws if x["in"] <= (w["start"] + w["end"]) / 2 <= x["out"]]
        # aire pedido, sin pasar la mitad del hueco con la palabra vecina
        assert inside[0]["start"] - x["in"] <= 0.30 + 1e-6 and x["out"] - inside[-1]["end"] <= 0.27 + 1e-6
    # sin audio no hay "sin_audio" por nivel (no se sabe)
    assert not any(d["why"] == "sin_audio" for d in r["discarded"])


def test_retoma_dentro_del_clip_se_usa_el_ultimo_intento(tmp_path):
    g = "Todo cambió cuando empezamos a publicar videos del horno."
    c = mk(tmp_path, "r1", "Todo cambió cuando empezamos a, |1.0| todo cambió cuando empezamos a publicar "
                           "videos del horno.", 0, setup=0)
    r = M.build([c], g)
    s = r["slots"][0]
    k2 = [k for k, w in enumerate(c["words"]) if w["text"].lower() == "todo"][1]
    assert s["cands"][0]["w0"] == k2 and chosen(r, "L01") == ["r1"]
    d = [x for x in r["discarded"] if x["sid"] == "r1"]
    assert d and d[0]["why"] == "retoma" and d[0]["text"].startswith("Todo cambió cuando empezamos a")


def test_la_misma_linea_dicha_dos_veces_en_un_clip_gana_la_segunda(tmp_path):
    g = "Nuestro pan de elote se hornea todos los días."
    c = mk(tmp_path, "d1", "Nuestro pan de elote se hornea todos los días. |1.2| Nuestro pan de elote se "
                           "hornea todos los días.", 0)
    r = M.build([c], g)
    s = r["slots"][0]
    k2 = [k for k, w in enumerate(c["words"]) if w["text"] == "Nuestro"][1]
    assert s["cands"][0]["w0"] == k2
    alt = s["cands"][1]
    assert "repetida" in alt["flags"] and "más adelante" in alt["reason"]


def test_toma_sin_microfono_pierde_contra_una_con_microfono(tmp_path):
    g = "Todo cambió cuando empezamos a publicar videos del horno.\nSi quieres probar nuestro pan, visítanos."
    loud = mk(tmp_path, "m1", "Todo cambió cuando empezamos a publicar videos del horno.", 0, level_db=-20)
    quiet = mk(tmp_path, "m2", "Todo cambió cuando empezamos a publicar videos del horno.", 1, level_db=-36)
    other = mk(tmp_path, "m3", "Si quieres probar nuestro pan, visítanos.", 2, level_db=-20)
    r = M.build([loud, quiet, other], g)
    assert chosen(r, "L01") == ["m1"]                  # la anterior, pero con microfono
    alt = next(c for c in slot_of(r, line="L01")["cands"] if c["sid"] == "m2")
    assert "sin_microfono" in alt["flags"] and "sin micrófono" in alt["reason"]
    assert any(d["sid"] == "m2" and d["why"] == "sin_audio" for d in r["discarded"])
    # si SOLO existe la toma sin micro, se usa (con aviso) y su volumen sube
    r2 = M.build([quiet, other], g)
    assert chosen(r2, "L01") == ["m2"]
    c0 = slot_of(r2, line="L01")["cands"][0]
    assert "sin_microfono" in c0["flags"] and "sin micrófono" in c0["reason"]
    v = {x["sid"]: x["vol"] for x in r2["timeline"]}
    assert v["m3"] <= 1.0 and 5.0 < v["m2"] / v["m3"] < 8.0   # ~16 dB: suenan parejo


def test_linea_partida_entre_dos_clips_se_completa(tmp_path):
    """Una toma dice solo la primera mitad; otra (retoma corta) solo la segunda: juntas cubren la
    linea completa, con un empalme a media linea."""
    g = "Cada mañana prendemos el horno a las cinco, y a las siete ya huele a pan en toda la calle."
    a = mk(tmp_path, "p1", "Cada mañana prendemos el horno a las cinco, |0.8| y a las siete ya huele",
           0, setup=0)
    b = mk(tmp_path, "p2", "y a las siete ya huele a pan en toda la calle.", 1, setup=0)
    r = M.build([a, b], g)
    assert chosen(r, "L01") == ["p1", "p2"] and r["uncovered"] == []
    assert [x["join"] for x in r["timeline"]] == [None, "splice"]
    assert r["stats"]["coverage"] == 1.0
    said = norm_words(tl_words(r, [a, b]))
    assert said == norm_words(g.split())               # sin palabras repetidas en el empalme


def test_linea_sin_toma_queda_en_uncovered(tmp_path):
    g = GUION + "\nY los domingos abrimos hasta las tres de la tarde."
    r = M.build(panaderia(tmp_path), g)
    assert r["uncovered"] == ["L06"]
    assert all("L06" not in s["lines"] for s in r["slots"])
    assert r["stats"]["coverage"] < 1.0 and chosen(r, "L05") == ["c6"]


def test_intercalado_de_angulos_misma_frase_otra_camara(tmp_path):
    """Dos tomas completas con las MISMAS palabras desde dos camaras: A/B/A cortando en pausas de
    las dos; abre la camara principal (la de mas tomas). intercut=False lo apaga."""
    g = ("Cada mañana prendemos el horno a las cinco, amasamos con calma durante una hora completa, "
         "y a las siete ya huele a pan recién hecho en toda la calle.\nVisítanos este sábado.")
    src = ("Cada mañana prendemos el horno a las cinco, |0.6| amasamos con calma durante una hora "
           "completa, |0.6| y a las siete ya huele a pan recién hecho en toda la calle.")
    main1 = mk(tmp_path, "a1", src, 0, setup=0)
    side = mk(tmp_path, "b1", src, 1, setup=1)
    main2 = mk(tmp_path, "a2", "Visítanos este sábado.", 2, setup=0)
    clips = [main1, side, main2]
    r = M.build(clips, g)
    seq = chosen(r, "L01")
    assert seq == ["a1", "b1", "a1"], seq
    joins = [x["join"] for x in r["timeline"]]
    assert joins == [None, "splice", "splice", "line"]
    assert any("intercalado" in c["flags"] for s in r["slots"] for c in s["cands"][:1])
    assert r["stats"]["intercut"] and r["stats"]["intercut"][0]["plan"] == "A/B/A"
    # los cortes caen en las pausas compartidas (>= 0.35 s) y cada parte dura >= 2 s
    for x in r["timeline"][:3]:
        assert x["out"] - x["in"] >= 2.0
    assert norm_words(tl_words(r, clips)) == norm_words(g.replace("\n", " ").split())
    r0 = M.build(clips, g, intercut=False)
    assert len(chosen(r0, "L01")) == 1 and r0["stats"]["intercut"] == []


def test_intercalado_con_toma_parcial_de_otra_camara(tmp_path):
    """Una camara grabo solo la primera mitad (a proposito) y otra la frase completa: se abre con la
    parcial y se completa con la otra camara."""
    g = "Probamos muchas recetas antes de abrir, y la de elote fue la favorita de todos los vecinos."
    half = mk(tmp_path, "h1", "Probamos muchas recetas antes de abrir...", 0, setup=0)
    full = mk(tmp_path, "f1", "Probamos muchas recetas antes de abrir, |0.6| y la de elote fue la favorita "
                              "de todos los vecinos.", 1, setup=1)
    r = M.build([half, full], g)
    assert chosen(r, "L01") == ["h1", "f1"]
    assert r["stats"]["intercut"][0]["mode"] == "parcial"
    r0 = M.build([half, full], g, intercut=False)
    assert chosen(r0, "L01") == ["f1"]


def test_sin_camaras_conocidas_no_hay_intercalado(tmp_path):
    g = ("Cada mañana prendemos el horno a las cinco, amasamos con calma durante una hora completa, "
         "y a las siete ya huele a pan recién hecho en toda la calle.")
    src = ("Cada mañana prendemos el horno a las cinco, |0.6| amasamos con calma durante una hora "
           "completa, |0.6| y a las siete ya huele a pan recién hecho en toda la calle.")
    r = M.build([mk(tmp_path, "a1", src, 0), mk(tmp_path, "b1", src, 1)], g)
    assert chosen(r, "L01") == ["b1"]                   # empate: la mas reciente
    s = r["slots"][0]
    assert "más reciente" in s["cands"][0]["reason"] and "anterior" in s["cands"][1]["reason"]
    # sin camara conocida cada clip tiene su propio id de camara (para el director)
    assert s["cands"][0]["setup"] != s["cands"][1]["setup"]


def test_numeros_y_compuestos_se_normalizan(tmp_path):
    g = "Vendimos 15 mil conchas en Mercado Libre y 300 roscas."
    a = mk(tmp_path, "n1", "Vendimos quince mil conchas en MercadoLibre y trescientas roscas.", 0)
    b = mk(tmp_path, "n2", "Vendimos 15,000 conchas en Mercado Libre y 300 roscas.", 1)
    for c in (a, b):
        r = M.build([c], g)
        cj = r["slots"][0]["cands"][0]
        assert cj["fidelity"] == 1.0 and cj["coverage"] == 1.0, cj


def test_glosario_iguala_nombres_propios_mal_escritos(tmp_path):
    g = "Ahora vendemos en Tolucalandia con la marca Panelotes."
    c = mk(tmp_path, "g1", "Ahora vendemos en Tolucalandya con la marca Panelotez.", 0)
    sin = M.build([c], g)["slots"][0]["cands"][0]
    con = M.build([c], g, glossary="Tolucalandia, Panelotes")["slots"][0]["cands"][0]
    assert sin["fidelity"] < 1.0 and con["fidelity"] == 1.0
    assert M.build([c], g, glossary=["Tolucalandia", "Panelotes"])["slots"][0]["cands"][0]["fidelity"] == 1.0


def test_casi_empate_con_distinta_redaccion_lo_decide_el_juez(tmp_path):
    g = "Horneamos pan dulce y salado para las panaderías del barrio."
    exacta = mk(tmp_path, "j1", "Horneamos pan dulce y salado para las panaderías del barrio.", 0)
    forma = mk(tmp_path, "j2", "Horneamos pan dulce y salado para las panadería del barrio.", 1)
    r = M.build([exacta, forma], g)
    assert chosen(r, "L01") == ["j1"]                                       # sin juez: la fiel
    assert "Dice «para las panadería» en vez de «para las panaderías»" in r["slots"][0]["cands"][1]["reason"]

    def prefiere_j2(prompt):
        uno = prompt.split("Toma 1:")[1].split("Toma 2:")[0]
        return "Sí" if "las panadería del" in uno else "No"
    r = M.build([exacta, forma], g, llm=prefiere_j2)
    assert chosen(r, "L01") == ["j2"] and r["stats"]["llm_calls"] == 2
    assert "juez LLM" in r["slots"][0]["cands"][0]["reason"]
    assert r["stats"]["judge"][0]["verdict"] == "B"
    # si el juez se contradice entre los dos ordenes, manda la eleccion determinista
    assert chosen(M.build([exacta, forma], g, llm=lambda p: "Sí"), "L01") == ["j1"]


def test_clips_vacios_cortos_y_sesion_vacia(tmp_path):
    r = M.build([], GUION)
    assert r["timeline"] == [] and r["slots"] == [] and r["uncovered"] == ["L01", "L02", "L03", "L04", "L05"]
    r = M.build([], None)
    assert r["script"] == {"source": "detected", "text": "", "sections": [], "lines": []} and r["timeline"] == []
    solo_signos = M.build([mk(tmp_path, "z9", "Si quieres probar nuestro pan, visítanos.", 0)], " ... \n\n ¿? ")
    assert solo_signos["script"]["source"] == "detected" and len(solo_signos["timeline"]) == 1
    mudo = mk(tmp_path, "z0", "", 0)
    corto = mk(tmp_path, "z1", "Hola.", 1)
    bueno = mk(tmp_path, "z2", "Si quieres probar nuestro pan de elote, visítanos este sábado.", 2)
    r = M.build([mudo, corto, bueno], GUION)
    assert chosen(r, "L05") == ["z2"] and r["uncovered"] == ["L01", "L02", "L03", "L04"]
    d = {x["sid"]: x for x in r["discarded"]}
    assert d["z0"]["why"] == "sin_audio" and d["z0"]["text"] == ""
    assert d["z1"]["why"] == "fuera_de_guion" and d["z1"]["text"] == "Hola."
    # wav flotante (no PCM16) tambien se lee (via soundfile) y da el mismo nivel
    import soundfile as sf
    y = M._read_audio(bueno["audio"])
    pf = tmp_path / "flotante.wav"
    sf.write(str(pf), y, SR, subtype="FLOAT")
    y2 = M._read_audio(str(pf))
    assert y2 is not None and len(y2) == len(y) and np.allclose(y, y2, atol=1e-4)
    assert M._read_audio(tmp_path / "no_hay.wav") is None and M._read_audio(None) is None
    # palabras rotas en la entrada no truenan
    raro = {"sid": "z3", "name": "z3.MOV", "created": None, "dur": 3.0, "audio": "/no/existe.wav",
            "words": [{"text": "hola"}, "x", {"text": "", "start": 0.1, "end": 0.2},
                      {"text": "visítanos", "start": 1.0, "end": 0.9}]}
    M.build([raro], GUION)


def test_el_inicio_que_repite_el_final_anterior_no_entra(tmp_path):
    """La entrada del clip siguiente que repite el final del segmento anterior no entra: la toma
    empieza en su primera palabra alineada DENTRO de su tramo del guion."""
    g = "Y así nació la receta de la abuela.\nLleva elote, mantequilla y canela."
    clips = [mk(tmp_path, "a", "Y así nació la receta de la abuela.", 0, setup=0),
             mk(tmp_path, "b", "La receta de la abuela. |0.4| Lleva elote, mantequilla y canela.", 1, setup=1)]
    r = M.build(clips, g, intercut=False)
    assert norm_words(tl_words(r, clips)) == norm_words(g.split())
    assert r["stats"]["coverage"] == 1.0 and r["stats"]["partial"] == []


def test_el_guion_que_repite_palabras_anadiplosis_se_conserva(tmp_path):
    """Regresion: el recorte de "inicio repetido" borraba las palabras del PROPIO guion cuando una
    linea empieza con el final de la anterior ("...con una pregunta." | "Una pregunta muy
    simple...") y la cobertura seguia en 100 %."""
    g = "Todo empezó con una pregunta.\nUna pregunta muy simple: por qué nadie vende pan de elote en el barrio."
    clips = [mk(tmp_path, "a1", "Todo empezó con una pregunta.", 0),
             mk(tmp_path, "b1", "Una pregunta muy simple: por qué nadie vende pan de elote en el barrio.", 1)]
    for audio in (True, False):
        cl = clips if audio else [dict(c, audio=None) for c in clips]
        r = M.build(cl, g, intercut=False)
        said = norm_words(tl_words(r, cl))
        assert said == norm_words(g.split()), said
        s2 = slot_of(r, line="L02")
        assert s2["cands"][0]["text"].startswith("Una pregunta muy simple")
        assert r["stats"]["coverage"] == 1.0 and r["uncovered"] == [] and r["stats"]["partial"] == []


# ------------------------------------------------------------------ fuera de guion y juez LLM
def test_fuera_de_guion_que_aporta_solo_con_el_juez(tmp_path):
    g = "Hace tres años abrí una panadería pequeña en el centro.\nSi quieres probar nuestro pan, visítanos."
    a = mk(tmp_path, "e1", "Hace tres años abrí una panadería pequeña en el centro. |0.9| Además cada viernes "
                           "regalamos café de olla a los primeros diez clientes.", 0)
    b = mk(tmp_path, "e2", "Si quieres probar nuestro pan, visítanos.", 1)
    # sin juez: solo sugerencia (no entra a la linea de tiempo)
    r = M.build([a, b], g)
    assert len(r["timeline"]) == 2
    ex = r["extra"]
    assert len(ex) == 1 and ex[0]["sid"] == "e1" and ex[0]["after"] == "L01" and not ex[0]["kept"]
    assert ex[0]["why"] == "sugerencia: sin juez LLM" and ex[0]["text"].startswith("Además")
    assert set(ex[0]) >= {"sid", "in", "out", "after", "why"}
    # juez que aprueba (dos preguntas, dos si): entra justo despues de su vecino
    calls = []

    def si(prompt):
        calls.append(prompt)
        return "Sí"
    r = M.build([a, b], g, llm=si)
    assert [x["sid"] for x in r["timeline"]] == ["e1", "e1", "e2"]
    xs = next(s for s in r["slots"] if s.get("extra"))
    assert xs["lines"] == [] and xs["cands"][0]["flags"] == ["fuera_de_guion"]
    assert r["timeline"][1]["join"] == "line" and r["extra"][0]["kept"] and len(calls) == 2
    assert r["stats"]["llm_calls"] == 2 and not any("regalamos" in d["text"] for d in r["discarded"])
    assert r["timeline"][1]["in"] >= r["timeline"][0]["out"] - 1e-6
    # continuacion pegada (pausa corta): un solo punto de corte, sin repetir el aire
    a2 = mk(tmp_path, "e3", "Hace tres años abrí una panadería pequeña en el centro. |0.45| Además cada viernes "
                            "regalamos café de olla a los primeros diez clientes.", 0)
    r2 = M.build([a2, b], g, llm=si)
    t0, t1 = r2["timeline"][0], r2["timeline"][1]
    assert t0["sid"] == t1["sid"] == "e3" and t0["out"] == t1["in"]
    w = a2["words"]
    k = next(i for i, x in enumerate(w) if x["text"] == "Además")
    assert w[k - 1]["end"] < t0["out"] < w[k]["start"]
    # juez que rechaza: fuera
    r = M.build([a, b], g, llm=lambda p: "No.")
    assert len(r["timeline"]) == 2 and r["extra"][0]["why"] == "el juez LLM no la aprueba"
    # juez que falla: como si no hubiera juez
    r = M.build([a, b], g, llm=lambda p: (_ for _ in ()).throw(RuntimeError("caido")))
    assert len(r["timeline"]) == 2 and r["extra"][0]["why"] == "sugerencia: sin juez LLM"


def test_charla_blooper_y_frases_cortas_nunca_son_sugerencia(tmp_path):
    r = M.build(panaderia(tmp_path), GUION, llm=lambda p: "Sí")
    assert r["extra"] == [] and r["stats"]["extras_kept"] == 0
    said = " ".join(tl_words(r, panaderia(tmp_path)))
    assert "grabando" not in said and "puta" not in said


def test_juez_de_pares_en_los_dos_ordenes():
    j = M._Judge(lambda p: "Sí" if "Toma 1: \"A\"" in p else "No")
    assert j.pair("guion", "A", "B") == "A" and j.pair("guion", "B", "A") == "B"
    assert M._Judge(lambda p: "Sí").pair("g", "A", "B") is None          # sesgo de posicion: nada
    assert M._Judge(None).pair("g", "A", "B") is None
    assert M._Judge(lambda p: "tal vez").enriches("x", "y") is None
    assert M._Judge.yes("  Sí, claro") is True and M._Judge.yes("NO") is False and M._Judge.yes("") is None


# ------------------------------------------------------------------ camaras
def _img(kind, seed):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:64, 0:36]
    if kind == 0:
        base = np.stack([80 + 2 * yy, 120 + xx, 60 + 0 * xx], -1)
    else:
        base = np.stack([200 - 2 * xx, 50 + yy, 150 + 0 * yy], -1)
    return np.clip(base + rng.normal(0, 6, base.shape), 0, 255).astype(np.uint8)


def test_grupos_de_camara_por_cuadros_y_miniaturas(tmp_path):
    from PIL import Image
    clips = [{"frames": [_img(0, 1)]}, {"frames": [_img(1, 2)]}, {"frames": [_img(0, 3), _img(0, 4)]},
             {}, {"frames": [_img(1, 5)[:, :, 0]]}]                 # gris: tambien
    p = tmp_path / "t.png"
    Image.fromarray(_img(1, 9)).resize((90, 160)).save(p)
    clips.append({"thumb": str(p)})
    ids = M.setups(clips)
    assert ids[0] == ids[2] and ids[1] == ids[5] and ids[0] != ids[1] and ids[3] is None
    assert ids[4] is not None
    assert M.setups([]) == [] and M.setups([{}, {}]) == [None, None]


def test_build_calcula_camaras_si_traen_cuadros(tmp_path):
    g = ("Cada mañana prendemos el horno a las cinco, amasamos con calma durante una hora completa, "
         "y a las siete ya huele a pan recién hecho en toda la calle.\nVisítanos este sábado.")
    src = ("Cada mañana prendemos el horno a las cinco, |0.6| amasamos con calma durante una hora "
           "completa, |0.6| y a las siete ya huele a pan recién hecho en toda la calle.")
    a1, b1, a2 = mk(tmp_path, "a1", src, 0), mk(tmp_path, "b1", src, 1), mk(tmp_path, "a2", "Visítanos este sábado.", 2)
    a1["frames"], b1["frames"], a2["frames"] = [_img(0, 1)], [_img(1, 2)], [_img(0, 3)]
    r = M.build([a1, b1, a2], g)
    assert chosen(r, "L01") == ["a1", "b1", "a1"]
    su = {c["sid"]: c["setup"] for s in r["slots"] for c in s["cands"]}
    assert su["a1"] == su["a2"] != su["b1"]


def test_cuadros_de_un_video_para_agrupar_camaras(tmp_path):
    import subprocess
    import paths
    vids = []
    for k, color in enumerate(("red", "blue", "red")):
        p = tmp_path / f"v{k}.mp4"
        subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c={color}:s=180x320:r=30:d=1.2", "-pix_fmt", "yuv420p", str(p)], check=True)
        vids.append(p)
    fr = [M.frames_of(p, 1.2) for p in vids]
    assert all(len(f) == 1 and f[0].shape == (64, 36, 3) and f[0].dtype == np.uint8 for f in fr)
    ids = M.setups([{"frames": f} for f in fr])
    assert ids[0] == ids[2] != ids[1]
    assert M.frames_of(tmp_path / "no_existe.mov", 3.0) == []


# ------------------------------------------------------------------ propiedades
def test_determinista(tmp_path):
    clips = panaderia(tmp_path)
    a, b = M.build(clips, GUION), M.build(clips, GUION)
    for x in (a, b):
        x.pop("ms")
        x["stats"].pop("timings_ms")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    # el orden de entrada no cambia el resultado (manda la fecha de grabacion)
    c = M.build(list(reversed(clips)), GUION)
    assert [x["sid"] for x in c["timeline"]] == [x["sid"] for x in a["timeline"]]


def test_rendimiento_40_clips_con_audio_en_menos_de_5_s(tmp_path):
    frases = [f"En la sucursal número {k} horneamos pan de {s} y lo vendemos antes de las nueve."
              for k, s in zip(range(1, 21), ["elote", "nata", "queso", "piña", "canela"] * 4)]
    g = "\n".join(frases)
    clips = []
    for k, f in enumerate(frases):
        vieja = f.replace("horneamos", "hacemos").replace(" antes de las nueve", "")
        clips.append(mk(tmp_path, f"v{k:02d}", vieja + " |0.8| " + vieja, 2 * k, setup=k % 3))
        clips.append(mk(tmp_path, f"n{k:02d}", f, 2 * k + 1, setup=k % 3))
    assert len(clips) == 40
    t = time.time()
    r = M.build(clips, g)
    el = time.time() - t
    assert el < 5.0 * load_factor(), el
    assert r["uncovered"] == [] and len(r["timeline"]) >= 20
    assert all(x["sid"].startswith("n") for x in r["timeline"])
    t = time.time()
    r2 = M.build(clips, None)
    assert time.time() - t < 5.0 * load_factor()
    assert r2["script"]["source"] == "detected" and r2["timeline"]


# ------------------------------------------------------------------ revision: lineas cortas, volumen, motivos
def test_linea_de_una_palabra_dicha_sola_se_usa(tmp_path):
    """Regresion: una linea de UNA palabra ("Sígueme.") grabada en su propio clip quedaba sin toma y
    el clip que la dice exacta se descartaba como fuera de guion (con y sin guion)."""
    g = "Todo cambió con los videos del horno.\n\nSígueme.\n\nVisítanos este sábado en la calle Hidalgo número doce."
    clips = [mk(tmp_path, "c4", "Todo cambió con los videos del horno.", 3, setup=2),
             mk(tmp_path, "c5", "Sígueme.", 4, setup=3),
             mk(tmp_path, "c6", "Visítanos este sábado en la calle Hidalgo número doce.", 5, setup=3)]
    for script in (g, None):
        r = M.build(clips, script, intercut=False)
        assert r["uncovered"] == [], (script, r["uncovered"])
        assert [x["sid"] for x in r["timeline"]] == ["c4", "c5", "c6"]
        assert not any(d["sid"] == "c5" for d in r["discarded"])
        assert r["stats"]["coverage"] == 1.0
    # un punchline de una palabra ("Cero.") tras una pregunta, cada uno en su propio clip
    g2 = "¿Y cuántas ventas tuvimos ese mes?\nCero.\nNi una sola venta en treinta días."
    cl2 = [mk(tmp_path, "q1", "¿Y cuántas ventas tuvimos ese mes?", 0),
           mk(tmp_path, "q2", "Cero.", 1),
           mk(tmp_path, "q3", "Ni una sola venta en treinta días.", 2)]
    r = M.build(cl2, g2, intercut=False)
    assert r["uncovered"] == [] and [x["sid"] for x in r["timeline"]] == ["q1", "q2", "q3"]


def test_linea_corta_no_se_toma_de_media_frase(tmp_path):
    """La palabra de una linea corta dicha DENTRO de otra frase (sin pausa ni puntuacion alrededor)
    no es esa linea: "Empezamos desde cero con un horno viejo" no cubre la linea "Cero."."""
    g = "Cero.\nY hoy horneamos quinientas piezas al día."
    clips = [mk(tmp_path, "m1", "Empezamos desde cero con un horno viejo.", 0),
             mk(tmp_path, "m2", "Y hoy horneamos quinientas piezas al día.", 1)]
    r = M.build(clips, g, intercut=False)
    assert r["uncovered"] == ["L01"] and [x["sid"] for x in r["timeline"]] == ["m2"]
    # una toma vieja de la linea corta, dicha exacta y sola, se descarta como retoma (no fuera de guion)
    clips = [mk(tmp_path, "s1", "Sígueme.", 0), mk(tmp_path, "s2", "Sígueme.", 1)]
    r = M.build(clips, "Sígueme.", intercut=False)
    assert [x["sid"] for x in r["timeline"]] == ["s2"]
    d = [x for x in r["discarded"] if x["sid"] == "s1"]
    assert d and d[0]["why"] == "retoma" and d[0]["reason"] == "Otra toma de la parte 1"


def test_sin_guion_charla_dicha_una_vez_no_entra(tmp_path):
    """Regresion: sin guion, en una sesion donde cada linea se dice UNA vez, la charla con el equipo
    en la cabeza o la cola de un clip ("¿Ya está grabando?", "¿Quedó bien?", "¿Así está bien o la
    repito?") entraba al guion detectado y al video."""
    clips = [
        mk(tmp_path, "c1", "¿Ya está grabando? |1.2| Hace tres años abrí una panadería pequeña en el centro. |0.8| "
                           "Al principio vendíamos apenas veinte panes al día.", 0, setup=0),
        mk(tmp_path, "c2", "Todo cambió cuando empezamos a publicar videos del horno.", 1, setup=1),
        mk(tmp_path, "c3", "Si quieres probar nuestro pan de elote, visítanos este sábado. |1.0| ¿Quedó bien?", 2, setup=2),
        mk(tmp_path, "c4", "¿Le doy? Sí, dale.", 3, setup=2),
        mk(tmp_path, "c5", "Te esperamos con café de olla. |1.2| ¿Así está bien o la repito?", 4, setup=2),
    ]
    r = M.build(clips, None, intercut=False)
    txt = r["script"]["text"].lower()
    for junk in ("grabando", "quedó bien", "le doy", "dale", "la repito"):
        assert junk not in txt, junk
    said = " ".join(tl_words(r, clips)).lower()
    for junk in ("grabando", "quedó", "doy", "dale", "repito"):
        assert junk not in said, junk
    assert "hace tres años" in said and "visítanos este sábado" in said and "café de olla" in said
    assert r["uncovered"] == []
    # con guion, lo mismo queda fuera (sin cambios)
    g = ("Hace tres años abrí una panadería pequeña en el centro.\nAl principio vendíamos apenas veinte panes "
         "al día.\n\nTodo cambió cuando empezamos a publicar videos del horno.\n\nSi quieres probar nuestro pan "
         "de elote, visítanos este sábado.\nTe esperamos con café de olla.")
    said = " ".join(tl_words(M.build(clips, g, intercut=False), clips)).lower()
    assert "grabando" not in said and "quedó" not in said and "repito" not in said


def test_sin_guion_pregunta_retorica_con_contenido_se_conserva(tmp_path):
    """La regla de charla no toca las preguntas del contenido ("¿Y las ventas? Cero.")."""
    clips = [mk(tmp_path, "r1", "Hace tres años abrí una panadería pequeña en el centro.", 0),
             mk(tmp_path, "r2", "¿Y las ventas? Cero.", 1),
             mk(tmp_path, "r3", "¿Por qué? Porque nadie conocía nuestro pan de elote.", 2)]
    r = M.build(clips, None, intercut=False)
    txt = r["script"]["text"].lower()
    assert "¿y las ventas?" in txt and "cero" in txt and "¿por qué?" in txt
    assert [x["sid"] for x in r["timeline"]] == ["r1", "r2", "r3"]
    assert M._is_chatter(M._text_toks("¿Ya está grabando?")) and M._is_chatter(M._text_toks("¿Quedó bien?"))
    assert M._is_chatter(M._text_toks("Perdón, repito.")) and M._is_chatter(M._text_toks("¿Otra vez?"))
    assert not M._is_chatter(M._text_toks("¿Y las ventas?")) and not M._is_chatter(M._text_toks("¿Por qué?"))
    assert not M._is_chatter(M._text_toks("Me encanta el corte de este pan."))
    assert not M._is_chatter(M._text_toks("Y está bien así.")) and not M._is_chatter(M._text_toks("¿Listos?"))
    assert M._is_chatter(M._text_toks("¿Así está bien?")) and M._is_chatter(M._text_toks("¿Me escuchas?"))


def test_cada_toma_trae_su_volumen_con_la_referencia_del_montaje(tmp_path):
    """Regresion: las alternativas no traian vol; "Usar esta toma" con una toma de un clip que no
    estaba en el video llegaba a /compose con vol "auto" y compose la nivelaba contra si misma
    (quedaba a 1.0, ~15 dB bajo sus vecinas si era la toma sin microfono)."""
    g = "Todo cambió cuando empezamos a publicar videos del horno.\nSi quieres probar nuestro pan, visítanos."
    loud = mk(tmp_path, "m1", "Todo cambió cuando empezamos a publicar videos del horno.", 0, level_db=-20)
    quiet = mk(tmp_path, "m2", "Todo cambió cuando empezamos a publicar videos del horno.", 1, level_db=-35)
    other = mk(tmp_path, "m3", "Si quieres probar nuestro pan, visítanos.", 2, level_db=-22)
    r = M.build([loud, quiet, other], g, intercut=False)
    tl = {x["slot"]: x for x in r["timeline"]}
    for s in r["slots"]:
        c0 = s["cands"][0]
        assert c0["vol"] == tl[s["id"]]["vol"]           # la elegida: el mismo vol que su tramo
    s1 = slot_of(r, line="L01")
    alt = next(c for c in s1["cands"] if c["sid"] == "m2")
    # la alternativa sin microfono sube ~15 dB: tras cambiarla, suena como sus vecinas
    eff = {c["sid"]: c["level_db"] + 20 * np.log10(c["vol"]) for s in r["slots"] for c in s["cands"]}
    assert abs(eff["m2"] - eff["m1"]) < 0.6 and abs(eff["m2"] - eff["m3"]) < 0.6
    assert 5.0 < alt["vol"] / s1["cands"][0]["vol"] < 7.5
    assert r["stats"]["level_ref_db"] is not None
    # sin audio: sin vol medido en los candidatos (el servidor lo mide)
    r2 = M.build([dict(d, audio=None) for d in (loud, quiet, other)], g, intercut=False)
    assert all("vol" not in c for s in r2["slots"] for c in s["cands"])


def test_confianza_por_toma_empate_igual_para_las_dos(tmp_path):
    """Cada toma trae su "conf" (la confianza si fuera la elegida): en un empate, la de la IA y la
    otra tienen la misma (no "Media" una y "Alta" la otra al cambiarla)."""
    g = "Nuestro pan de elote se hornea todos los días en horno de leña."
    a = mk(tmp_path, "e1", "Nuestro pan de elote se hornea todos los días en horno de leña.", 0)
    b = mk(tmp_path, "e2", "Nuestro pan de elote se hornea todos los días en horno de leña.", 1)
    r = M.build([a, b], g, intercut=False)
    s = r["slots"][0]
    assert len(s["cands"]) == 2 and s["cands"][0]["sid"] == "e2"
    c0, c1 = s["cands"]
    assert c0["conf"] == r["timeline"][0]["conf"] and c1["conf"] <= c0["conf"]
    # mismo nivel de confianza para las dos (media: hay otra igual de buena)
    assert 0.6 <= c1["conf"] < 0.8 and 0.6 <= c0["conf"] < 0.8
    # sin rival, la confianza es la calidad de la toma
    r1 = M.build([b], g, intercut=False)
    assert r1["slots"][0]["cands"][0]["conf"] == r1["timeline"][0]["conf"] >= 0.8


def test_motivos_sin_ids_de_linea_y_palabra_movida_una_vez(tmp_path):
    """Los motivos nombran las partes como el panel ("la parte 2", no "L02") y una palabra dicha en
    otro orden se dice una vez ("Cambia el orden de «x»", no "Le falta X; Agrega X")."""
    g = "Hace tres años abrí una panadería pequeña en el centro.\nO simplemente algo bonito que nadie va a usar."
    clips = [mk(tmp_path, "d1", "Hace tres años abrí una panadería pequeña en el centro.", 0),
             mk(tmp_path, "d2", "O algo bonito simplemente que nadie va a usar.", 1),
             mk(tmp_path, "d3", "O simplemente algo bonito que nadie va a", 2),
             mk(tmp_path, "d4", "O simplemente algo bonito que nadie va a usar.", 3)]
    r = M.build(clips, g, intercut=False)
    for d in r["discarded"]:
        assert not re.search(r"\bL\d+\b", d["reason"]), d
    inc = [d for d in r["discarded"] if d["sid"] == "d3"]
    assert inc and inc[0]["reason"] == "Intento incompleto de la parte 2"
    alt = next(c for c in slot_of(r, line="L02")["cands"] if c["sid"] == "d2")
    assert "Cambia el orden de «simplemente»" in alt["reason"]
    assert "Le falta «simplemente»" not in alt["reason"] and "Agrega «simplemente»" not in alt["reason"]
    # una linea sin toma se nombra por su texto
    assert M._parts_label(M._Script(g), ["L02"], {}) == "«O simplemente algo bonito que nadie…»"
    assert M._parts_label(None, ["L01", "L02"], {"L01": [1], "L02": [2, 3]}) == "las partes 1, 2 y 3"
