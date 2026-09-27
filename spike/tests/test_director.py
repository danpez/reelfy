"""Pruebas del IA director (motion/director.py).

- Plan sintetico (sin video ni LLM): catalogo, ritmo, transiciones, SFX por evento, CTA final,
  zonas sin encimar, SAFE, hook, determinismo, estilos, intensidad, redirect.
- Calidad (revision del critico): contraste WCAG de todo texto sobre caja, kit tipografico
  (<= 4 fuentes, <= 3 familias), bloques por clausula (>= 0.8 s, <= 2 presets por frase),
  acentos que no tocan la tinta de texto y duran lo suficiente, CTA final limpio, tinta segun el
  fondo real, respaldo sin detector de rostros, info_card con UNA tarjeta, presupuesto de
  escenas y transiciones de entrada/salida, SFX no monotono, otro rubro (sin sobreajuste),
  recortes IA propuestos una sola vez.
- Caso real (dentista): fixture v1 + crudo_completo.mp4 (se omite si no existen).
- LLM local: solo si ollama responde (usa la cache del director si ya existe).
"""
import copy
from collections import Counter
import json
import math
import os
import time

import pytest

from conftest import FIXTURES, TEST_MEDIA
from motion import director as D
from motion.ctx import SAFE

FIXTURE = FIXTURES / "dentista_plan_v1.json"
VIDEO = TEST_MEDIA / "crudo_completo.mp4"


# ---------------------------------------------------------------- utilidades
def _w(text, t0, dur=None, kw=False):
    dur = dur or max(0.18, 0.07 * len(text))
    d = {"text": text, "start": round(t0, 3), "end": round(t0 + dur, 3)}
    if kw:
        d["kw"] = True
    return d


def _phrase(text, t0, rate=0.3, kws=()):
    out, t = [], t0
    for tok in text.split():
        d = max(0.16, rate * 0.6 + 0.02 * len(tok))
        out.append(_w(tok, t, d, kw=D.norm(tok) in kws))
        t += d + 0.06
    return out, t


def synthetic_plan():
    """~27 s: hook con pregunta, frase punzante, beneficio, precio, direccion, CTA con link."""
    lines = [
        ("Si te duele la muela cuando tomas algo frio, ¿sabias que tiene solucion?", 0.4, ("muela", "frio")),
        ("Escucha esto.", None, ()),
        ("Una resina bien hecha protege tu diente y se ve completamente natural.", None, ("resina", "natural")),
        ("Cuesta quinientos pesos.", None, ()),
        ("Estamos en Calle Hidalgo 120, frente al Parque Rojo.", None, ("hidalgo",)),
        ("Agenda tu cita con el link de mi perfil y te veo pronto.", None, ("agenda",)),
    ]
    phrases, t = [], 0.4
    for txt, t0, kws in lines:
        t = t0 if t0 is not None else t + 0.55
        ws, t_end = _phrase(txt, t, kws=kws)
        if t < 19.3 and t_end > 15.3:            # la frase no puede caer en el b-roll mudo
            ws, t_end = _phrase(txt, 19.3, kws=kws)
        t = t_end
        for k in range(0, len(ws), 3):
            phrases.append(ws[k:k + 3])
    dur = t + 1.2
    shots = [
        {"start": 0.0, "end": 9.0, "person": True, "framing": "medio", "place": "consultorio",
         "text_seen": [], "use": "a_camara", "free_space": "arriba", "face": [0.5, 0.33, 0.14, 0.11]},
        {"start": 9.0, "end": 15.5, "person": True, "framing": "medio", "place": "consultorio",
         "text_seen": [], "use": "a_camara", "free_space": "arriba", "face": [0.45, 0.35, 0.15, 0.12]},
        {"start": 15.5, "end": 19.0, "person": False, "framing": "sin_persona", "place": "fachada",
         "text_seen": ["120"], "use": "broll", "free_space": "arriba", "face": None},
        {"start": 19.0, "end": dur, "person": True, "framing": "general", "place": "calle",
         "text_seen": [], "use": "a_camara", "free_space": "arriba", "face": [0.5, 0.3, 0.1, 0.08]},
    ]
    flat = [w for ph in phrases for w in ph]
    dur = flat[-1]["end"] + 1.0
    shots[-1]["end"] = round(dur, 3)
    cuts = [{"start": 0.0, "end": 0.3, "enabled": True},
            {"start": 5.2, "end": 5.6, "enabled": True}]
    return {"stem": "sintetico", "video": "", "duration": round(dur, 3), "dims": [1080, 1920],
            "phrases": phrases, "cuts": cuts, "beats": [], "camera": [], "highlights": [],
            "shots": shots}


@pytest.fixture(scope="module")
def synth():
    return D.direct(synthetic_plan(), style="dinamico", intensity=0.75, seed=7, use_llm=False)


def _tl(plan):
    return D._timeline(plan)


def _final_windows(plan, track):
    tl = _tl(plan)
    out = []
    for it in plan["fx"].get(track) or []:
        if track in ("transitions", "sfx"):
            a = tl.remap_clamped(float(it["at"]))
            out.append((a, a, it))
        else:
            out.append((tl.remap_clamped(float(it["start"])), tl.remap_clamped(float(it["end"])), it))
    return out


def _visual_starts(plan):
    ev = []
    for tr in ("text", "graphics", "cutaways"):
        ev += [(a, it) for a, b, it in _final_windows(plan, tr) if it.get("kind") != "progress"]
    return ev


def _check_catalog(plan):
    assert D.validate_min(plan) == []
    try:
        from motion import schema
    except Exception:
        return
    errs = [e for e in schema.validate(plan) if "falta 'video'" not in e]
    assert errs == [], errs[:10]


def _check_zones(plan):
    """Nunca dos elementos en la misma zona al mismo tiempo (acentos: separados en espacio)."""
    items = []
    for tr in ("text", "graphics", "cutaways"):
        for a, b, it in _final_windows(plan, tr):
            z = D.zone_of(tr, it)
            if z and z not in ("edge",):
                items.append((a, b, z, it))
    aspect = 16 / 9
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a1, b1, z1, i1 = items[i]
            a2, b2, z2, i2 = items[j]
            if not (a1 < b2 - 1e-3 and a2 < b1 - 1e-3):
                continue
            if "full" in (z1, z2) and z1 != "bottom" and z2 != "bottom":
                pytest.fail(f"escena completa encimada con {i1['id']} / {i2['id']}")
            if z1 != z2:
                continue
            if z1 == "accent":
                dist = math.hypot(i1["x"] - i2["x"], (i1["y"] - i2["y"]) * aspect)
                assert dist >= D.ACCENT_SEP - 1e-6, (i1["id"], i2["id"], dist)
            else:
                pytest.fail(f"zona {z1} encimada: {i1['id']} y {i2['id']}")


def _check_safe(plan):
    for it in plan["fx"]["text"]:
        lines = it.get("lines") or [{"text": ln, "scale": 1.0} for ln in str(it.get("text", "")).split("\n")]
        size = float(it["size"])
        w = max(D._text_w(ln.get("font") or it.get("font"), size * ln.get("scale", 1.0), ln["text"])
                for ln in lines)
        h = sum(size * ln.get("scale", 1.0) * 1.12 for ln in lines) / (16 / 9)
        if it.get("align") == "left":
            x0, x1 = it["x"], it["x"] + w
        else:
            x0, x1 = it["x"] - w / 2, it["x"] + w / 2
        assert x0 >= SAFE["left"] - 0.02 and x1 <= SAFE["right"] + 0.02, (it["id"], x0, x1)
        assert it["y"] - h / 2 >= SAFE["top"] - 0.01, (it["id"], it["y"], h)
        assert it["y"] + h / 2 <= SAFE["bottom"] + 0.01, (it["id"], it["y"], h)
    for it in plan["fx"]["graphics"]:
        if it["kind"] == "progress":
            continue
        assert SAFE["left"] - 0.01 <= it["x"] <= SAFE["right"] + 0.01, it["id"]
        assert SAFE["top"] - 0.01 <= it["y"] <= SAFE["bottom"] + 0.01, it["id"]
        if it["kind"] in ("card", "cta", "lower_third", "sticker_text"):
            half = it["size"] / 2
            if it["kind"] == "lower_third":
                assert it["x"] - half >= 0.0
            else:
                assert it["x"] - half >= SAFE["left"] - 0.02 and it["x"] + half <= SAFE["right"] + 0.02, it["id"]


def _check_rhythm(plan, style, intensity):
    st = D.stats(plan)
    gap_max = D._lerp(*D.STYLES[style]["gap"], intensity)
    assert st["max_gap"] <= gap_max + 0.35, st
    return st


def _check_sfx_per_event(plan, min_ratio=0.85):
    sfx = [a for a, _, _ in _final_windows(plan, "sfx")]
    ev = _visual_starts(plan)
    hits = sum(1 for a, _ in ev if any(-0.2 <= s - a <= 0.45 for s in sfx))
    assert ev and hits / len(ev) >= min_ratio, (hits, len(ev))


def _check_transitions(plan):
    tl = _tl(plan)
    changes = tl.shot_changes(plan["shots"])
    trs = [a for a, _, _ in _final_windows(plan, "transitions")]
    sfx = _final_windows(plan, "sfx")
    for c in changes:
        if c < 0.25 or c > tl.final_duration - 0.25:
            continue
        assert any(abs(c - t) < 0.12 for t in trs), f"sin transicion en {c:.2f}"
        assert any(abs(c - a) < 0.12 and it["name"] in ("whip", "whoosh_soft", "whoosh_fast", "swoosh_up",
                                                         "swipe", "glitch", "paper_rip")
                   for a, _, it in sfx), f"sin whoosh en {c:.2f}"


# ---------------------------------------------------------------- unidades
def test_parse_number_es():
    tk = lambda s: [D.norm(t) for t in s.split()]  # noqa: E731
    assert D.parse_number(tk("mil pesos"), 0) == (1000.0, 1)
    assert D.parse_number(tk("dos mil quinientos pesos"), 0) == (2500.0, 3)
    assert D.parse_number(tk("treinta y cinco"), 0) == (35.0, 3)
    assert D.parse_number(tk("quinientos"), 0) == (500.0, 1)
    assert D.parse_number(tk("2150"), 0) == (2150.0, 1)
    assert D.parse_number(tk("5 mil"), 0) == (5000.0, 2)
    assert D.parse_number(tk("costo"), 0) == (None, 0)
    assert D.fmt_money(1000, "MXN") == "$1,000 MXN"


def test_resolve_icon_no_absurd_matches():
    assert D.resolve_icon("tooth") == "dental"
    assert D.resolve_icon("dientes") == "dental"
    assert D.resolve_icon("money") == "cash"
    assert D.resolve_icon("bluetoothless") is None          # palabra completa, no subcadena
    assert D.resolve_icon("tiempo") == "hourglass"
    assert D.resolve_icon("sienten") == "hand-finger"          # lema por prefijo (general)
    # iconos fuera de tono se sustituyen por el set del rubro
    # "lengua" ya no mapea a un icono (assets.ICON_ES); nunca debe salir uno fuera de tono
    ic = D.tone_icon("lengua", "Dentista")
    assert ic is None or ic in D.TOPIC_ICONS["Dentista"]
    assert ic not in D.ICON_BLOCK


def test_entities_synthetic():
    p = synthetic_plan()
    words = D._flatten_words(p)
    sents = D._sentences(words)
    ent, anc = D.extract_entities(words, sents, p["shots"])
    assert ent["price"] == "$500 MXN"
    assert ent["address"] == "Calle Hidalgo 120"
    assert "Parque Rojo" in ent["landmark"]
    assert ent["link_label"] == "Agenda tu cita"
    assert ent["name"] == ""
    assert "price" in anc and "address" in anc and "link" in anc


def test_ocr_name_policy():
    # letrero tipo directorio (varias especialidades y nombres): el nombre NO se usa
    ent = {"name": "", "role": "Dentista", "address": "Calle Roble 512"}
    shots = [{"text_seen": ["512", "OFTALMOLOGIA", "Dra. Laura Pinos", "JUAN SOTO"]}]
    info = D._ocr_confirm(ent, shots)
    assert ent["name"] == "" and info["address_seen"] and info["directory_sign"]
    assert info["name_candidates"] == ["Dra. Laura Pinos"]
    ent2 = {"name": "", "role": "Dentista", "address": ""}
    info2 = D._ocr_confirm(ent2, [{"text_seen": ["Dra. Ana Ruiz Dentista"]}])
    assert ent2["name"] == "Dra. Ana Ruiz" and info2.get("name_from_ocr")


def test_llm_merge_is_validated():
    p = synthetic_plan()
    script = D.analyze_script(p, p["shots"], use_llm=False)
    fake = {"topic": "Resinas dentales", "ent": {"name": "Dra. Inventada", "role": "odontologa",
                                                  "price": "$9,999 MXN", "address": "", "landmark": "",
                                                  "schedule": "", "cta": "", "link_label": "Reserva ya",
                                                  "handle": "@nadie"},
            "f": [{"id": 0, "sec": "hook", "title": "palabras que no existen", "emph": ["xyz", "muela"],
                   "fx": "dramatico", "icon": ["tooth"], "nrg": "alta", "tone": "intriga"},
                  {"id": 2, "sec": "beneficio", "title": "completamente natural", "emph": ["natural"],
                   "fx": "detras", "icon": ["sparkle"], "nrg": "media", "tone": "alegre"},
                  {"id": 99, "sec": "otro", "title": "", "emph": [], "fx": "zoom", "icon": [],
                   "nrg": "baja", "tone": "serio"}]}
    D._merge_llm(script, fake, script["words"])
    e = script["entities"]
    assert e["name"] == "" and e["handle"] == "" and e["price"] == "$500 MXN"
    s0, s2 = script["sents"][0]["info"], script["sents"][2]["info"]
    assert s0["fx"] != "dramatico"                      # frase larga: no puede ser dramatica
    assert s0["title"] is None                          # titulo no copiado de la frase
    assert all(script["words"][i]["n"] != "xyz" for i in s0["emph"])
    assert s2["fx"] == "detras" and s2["title"] is not None
    assert "dental" in s0["icons"]


# ---------------------------------------------------------------- plan sintetico
def test_synthetic_catalog_and_structure(synth):
    _check_catalog(synth)
    assert synth["version"] == 2
    d = synth["director"]
    assert d["style"] == "dinamico" and d["llm"] == "heuristico" and "script" in d
    fx = synth["fx"]
    assert fx["captions"]["hide_during_titles"] is True
    assert fx["music"]["source"] == "ai" and fx["music"]["prompt"]
    assert fx["grade"]["look"] in D.LOOKS
    kinds = {it["kind"] for it in fx["graphics"]}
    assert {"card", "cta"} <= kinds
    assert any(it.get("variant") == "location" for it in fx["graphics"])
    assert fx["cutaways"] and fx["cutaways"][0]["text"]["preset"] == "counter"
    assert fx["cutaways"][0]["text"]["to"] == 500


def test_synthetic_rhythm_zones_safe(synth):
    _check_rhythm(synth, "dinamico", 0.75)
    _check_zones(synth)
    _check_safe(synth)


def test_synthetic_transitions_and_sfx(synth):
    _check_transitions(synth)
    _check_sfx_per_event(synth)
    names = {s["name"] for s in synth["fx"]["sfx"]}
    assert "cash" in names                                # precio
    assert names & {"notification", "ding"}               # ubicacion / horario
    assert "click" in names                               # CTA


def test_synthetic_hook_drama_cta(synth):
    tl = _tl(synth)
    D_ = tl.final_duration
    texts = _final_windows(synth, "text")
    assert min(a for a, _, _ in texts) <= 1.5                          # titulo del hook
    punches = [a for a, _, it in _final_windows(synth, "effects") if it["kind"] == "punch"]
    assert min(punches) <= 1.5                                         # punch del hook
    assert min(a for a, _, _ in _final_windows(synth, "sfx")) <= 1.5   # sfx del hook
    # dramatico en "Escucha esto."
    bw = [it for it in synth["fx"]["effects"] if it["kind"] == "bw"]
    assert len(bw) == 1
    assert synth["fx"]["music"]["stops"], "el momento dramatico corta la musica"
    # CTA en los ultimos segundos
    ctas = [(a, b, it) for a, b, it in _final_windows(synth, "graphics") if it["kind"] == "cta"]
    assert any(b >= D_ - 0.5 and a <= D_ - 1.5 for a, b, _ in ctas)
    # transicion en cada cambio de toma y kenburns en el b-roll
    assert any(it["kind"] == "kenburns" for it in synth["fx"]["effects"])
    # musica sube en el b-roll sin voz
    assert any(sw["start"] <= 15.6 and sw["end"] >= 18.9 for sw in synth["fx"]["music"]["swells"])


def test_deterministic():
    a = D.direct(synthetic_plan(), use_llm=False, seed=11)
    b = D.direct(synthetic_plan(), use_llm=False, seed=11)
    assert json.dumps(a["fx"], sort_keys=True) == json.dumps(b["fx"], sort_keys=True)
    c = D.direct(synthetic_plan(), use_llm=False, seed=12)
    assert json.dumps(a["fx"]["text"], sort_keys=True) != json.dumps(c["fx"]["text"], sort_keys=True) \
        or json.dumps(a["fx"]["transitions"], sort_keys=True) != json.dumps(c["fx"]["transitions"], sort_keys=True)


def test_input_not_mutated():
    p = synthetic_plan()
    before = json.dumps(p, sort_keys=True)
    D.direct(p, use_llm=False)
    assert json.dumps(p, sort_keys=True) == before


@pytest.mark.parametrize("style", list(D.STYLES))
def test_all_styles_valid(style):
    p = D.direct(synthetic_plan(), style=style, intensity=0.75, use_llm=False)
    _check_catalog(p)
    _check_zones(p)
    _check_safe(p)
    _check_transitions(p)
    _check_rhythm(p, style, 0.75)
    assert p["fx"]["captions"]["preset"] == D.STYLES[style]["captions"]["preset"]


def test_intensity_scales_density():
    lo = D.stats(D.direct(synthetic_plan(), intensity=0.1, use_llm=False))
    hi = D.stats(D.direct(synthetic_plan(), intensity=1.0, use_llm=False))
    assert hi["events"] > lo["events"]
    mn = D.stats(D.direct(synthetic_plan(), style="minimal", use_llm=False))
    en = D.stats(D.direct(synthetic_plan(), style="energetico", use_llm=False))
    assert en["events"] > mn["events"]


def test_no_speech_plan():
    p = synthetic_plan()
    p["phrases"] = []
    out = D.direct(p, use_llm=False)
    _check_catalog(out)
    assert out["fx"]["transitions"]
    assert out["fx"]["music"]["enabled"]


def test_llm_unreachable_falls_back(monkeypatch):
    monkeypatch.setenv("REELFY_OLLAMA_URL", "http://127.0.0.1:9")
    D._AVAIL.clear()
    t = time.time()
    out = D.direct(synthetic_plan(), use_llm=True)
    assert time.time() - t < 10
    assert out["director"]["llm"] == "heuristico"
    _check_catalog(out)
    D._AVAIL.clear()


# ---------------------------------------------------------------- redirect
def test_redirect_respects_locked_and_user(synth):
    p = copy.deepcopy(synth)
    locked = copy.deepcopy(p["fx"]["text"][1])
    p["fx"]["text"][1]["locked"] = True
    locked["locked"] = True
    user = {"id": "tx_mio", "preset": "kicker", "text": "MI TEXTO", "start": 7.0, "end": 9.0,
            "x": 0.47, "y": 0.2, "size": 0.05, "src": "user", "enabled": True, "locked": False,
            "intensity": 1.0}
    p["fx"]["text"].append(user)
    p["director"]["locked"] = ["music"]
    music = copy.deepcopy(p["fx"]["music"])
    out = D.redirect(p, style="energetico", intensity=0.9)
    ids = {it["id"]: it for it in out["fx"]["text"]}
    assert ids[locked["id"]]["text" if "text" in locked else "lines"] == locked.get("text", locked.get("lines"))
    assert ids[locked["id"]]["start"] == locked["start"] and ids[locked["id"]]["locked"]
    assert "tx_mio" in ids and ids["tx_mio"]["text"] == "MI TEXTO"
    assert out["fx"]["music"] == music
    assert out["director"]["style"] == "energetico" and out["director"]["rev"] == 1
    _check_catalog(out)
    _check_zones(out)


def test_redirect_only_ids(synth):
    p = copy.deepcopy(synth)
    target = p["fx"]["text"][2]["id"]
    out = D.redirect(p, only_ids=[target])
    before = {it["id"]: it for it in p["fx"]["text"] if it["id"] != target}
    after = {it["id"]: it for it in out["fx"]["text"]}
    for k, v in before.items():
        assert after[k] == v
    assert target not in after or after[target] != [it for it in p["fx"]["text"] if it["id"] == target][0]
    for tr in ("graphics", "effects", "transitions"):
        assert len(out["fx"][tr]) >= len(p["fx"][tr]) - 1
    _check_catalog(out)
    _check_zones(out)


# ---------------------------------------------------------------- caso real (dentista)
need_real = pytest.mark.skipif(not (FIXTURE.exists() and VIDEO.exists()),
                               reason="falta fixture o video de prueba")


@pytest.fixture(scope="module")
def dentista():
    plan = json.load(open(FIXTURE))
    plan["video"] = str(VIDEO)
    return plan


@need_real
def test_dentista_shots(dentista):
    shots = D.analyze_shots(dentista, use_llm=False)
    starts = [s["start"] for s in shots[1:]]
    ref = [8.0, 13.867, 17.267, 24.267, 28.867]
    assert len(starts) == len(ref)
    for a, b in zip(starts, ref):
        assert abs(a - b) < 0.1
    uses = [s["use"] for s in shots]
    assert uses[4] == "broll" and uses.count("a_camara") == 5
    assert shots[0]["framing"] == "general"


@need_real
def test_dentista_edit(dentista):
    p = D.direct(dentista, use_llm=False)
    _check_catalog(p)
    _check_zones(p)
    _check_safe(p)
    _check_transitions(p)
    _check_sfx_per_event(p)
    st = _check_rhythm(p, "dinamico", 0.75)
    assert st["events"] >= 30
    # entidades: se comparan contra la transcripcion (sin datos del cliente en el repo)
    e = p["entities"]
    words = D._flatten_words(dentista)
    ns = [w["n"] for w in words]
    nums = {D.parse_number(ns, i)[0] for i in range(len(ns))}
    assert e["price"].startswith("$") and e["price"].endswith("MXN")
    assert float(e["price"].split()[0].strip("$").replace(",", "")) in nums
    addr = e["address"].split()
    assert addr[0] == "Av." and addr[-1] in [w["text"].strip(",.") for w in words]
    assert e["landmark"].startswith("a 2 cuadras de")
    assert all(D.norm(t) in ns for t in e["landmark"].split()[4:])
    assert e["link_label"] == "Agenda tu cita" and e["role"] == "Dentista"
    assert e["name"] == ""                            # el letrero es un directorio: no se inventa
    fx = p["fx"]
    tl = _tl(p)
    # drama en la frase punzante: blanco y negro + texto gigante detras + stop de musica
    bw = [it for it in fx["effects"] if it["kind"] == "bw"]
    assert len(bw) == 1 and bw[0]["start"] <= 6.5 <= bw[0]["end"]
    assert any(it["preset"] == "behind_giant" and it["behind"] for it in fx["text"])
    assert any(abs(s["at"] - 6.4) < 0.3 for s in fx["music"]["stops"])
    # precio: escena con contador a 1000 y sonido de caja
    cw = fx["cutaways"][0]
    assert cw["text"]["preset"] == "counter" and cw["text"]["to"] == 1000
    assert any(s["name"] == "cash" and 15.0 <= s["at"] <= 16.3 for s in fx["sfx"])
    # ubicacion, horario y link
    variants = {g.get("variant") for g in fx["graphics"]}
    variants |= {g.get("variant") for c in fx["cutaways"] for g in c.get("graphics") or []}
    assert {"location", "schedule", "link_below"} <= variants
    # la referencia se titula al volver de la escena de ubicacion
    ref_word = e["landmark"].split()[-1].upper()
    assert any(ref_word in json.dumps(it, ensure_ascii=False).upper() for it in fx["text"])
    # b-roll de fachada: ken burns y la tarjeta con mapa (el dato completo, una sola vez)
    assert any(it["kind"] == "kenburns" and it["start"] >= 24.2 for it in fx["effects"])
    cards = [g for g in fx["graphics"] if g["kind"] == "card" and g.get("variant") == "location"]
    assert cards and all(24.2 <= g["start"] and g["end"] <= 28.9 for g in cards)
    assert not any(c["kind"] == "info_card" for c in fx["cutaways"])      # no tapa a la hablante
    shown = [it for tr in ("text", "graphics", "cutaways") for it in fx[tr]
             if addr[-1] in json.dumps(it, ensure_ascii=False)]
    assert len(shown) <= 2, [it["id"] for it in shown]                    # heroe + tarjeta
    # recorte IA del b-roll mudo largo (queda ~2.8 s) y aviso en notas
    ai = [c for c in p["cuts"] if c.get("src") == "ai"]
    assert ai and all(c["enabled"] for c in ai)
    broll = [s for s in p["shots"] if s["use"] == "broll"][0]
    kept = tl.remap_clamped(broll["end"]) - tl.remap_clamped(broll["start"])
    assert 2.0 <= kept <= 3.2, kept
    # precio: servicio sobre el numero y etiqueta de moneda (oculta subtitulos)
    assert any(g.get("kind") == "sticker_text" and g.get("text") for g in cw.get("graphics") or [])
    assert cw["text"].get("text")
    # escenas: fondo reemplazado + desenfoque + transiciones de entrada/salida de la escena
    kinds = {it["kind"] for it in fx["effects"]}
    assert {"bg_replace", "bg_blur", "flash_frame", "color_pop", "bw"} <= kinds
    trs = [tl.remap_clamped(t["at"]) for t in fx["transitions"]]
    for c in fx["cutaways"]:
        assert any(abs(t - tl.remap_clamped(c["start"])) < 0.12 for t in trs)
        assert any(abs(t - tl.remap_clamped(c["end"])) < 0.35 for t in trs)
    # CTA hasta el final
    cta = [it for it in fx["graphics"] if it["kind"] == "cta"]
    assert cta and tl.remap_clamped(cta[-1]["end"]) >= tl.final_duration - 0.5
    # hook en los primeros 1.5 s
    assert min(tl.remap_clamped(it["start"]) for it in fx["text"]) <= 0.2


@need_real
def test_dentista_llm_end_to_end(dentista):
    if not D.llm_available():
        pytest.skip("ollama no disponible")
    t = time.time()
    p = D.direct(dentista, use_llm=True)
    took = time.time() - t
    assert took < 60, took
    assert p["director"]["llm"] != "heuristico"
    _check_catalog(p)
    _check_zones(p)
    _check_safe(p)
    _check_transitions(p)
    assert p["entities"]["name"] == ""


def test_variety(synth):
    presets = [it["preset"] for it in sorted(synth["fx"]["text"], key=lambda it: it["start"])]
    for a, b, c in zip(presets, presets[1:], presets[2:]):
        assert not (a == b == c), presets
    kinds = [it["kind"] for it in sorted(synth["fx"]["transitions"], key=lambda it: it["at"])]
    for a, b in zip(kinds, kinds[1:]):
        assert a != b, kinds
    fonts = {f for it in synth["fx"]["text"] for f in _fonts_of(it)}
    assert len(fonts) >= 2                                 # jerarquia tipografica por rol
    assert any(s["name"].startswith("riser") for s in synth["fx"]["sfx"])


# ---------------------------------------------------------------- calidad (revision del critico)
def _fonts_of(it: dict) -> set:
    fs = {it.get("font")} if it.get("font") else set()
    fs |= {ln.get("font") for ln in it.get("lines") or [] if ln.get("font")}
    return fs


def _all_text(plan):
    """Items de texto del plan: pista text + texto embebido de escenas (sin subtitulos)."""
    out = [it for it in plan["fx"]["text"]]
    out += [c["text"] for c in plan["fx"]["cutaways"] if isinstance(c.get("text"), dict)]
    return out


def _sentence_of(plan, t0):
    sents = plan["director"]["script"]["sentences"]
    best = None
    for s in sents:
        if s["t0"] - 0.3 <= t0 + 0.12:
            best = s["id"]
    return best


@pytest.fixture(scope="module")
def styled():
    return {st: D.direct(synthetic_plan(), style=st, intensity=0.75, use_llm=False) for st in D.STYLES}


def test_box_text_contrast_wcag(styled):
    """Todo texto sobre caja (marcador, kicker, etiquetas) cumple WCAG 4.5:1 en los 4 estilos."""
    for st, p in styled.items():
        for it in _all_text(p):
            if it.get("box"):
                fg = D.box_text_color(it)
                assert D.contrast(fg, it["box"]) >= 4.5, (st, it.get("id"), fg, it["box"])
        for g in p["fx"]["graphics"]:
            if g["kind"] == "sticker_text":
                assert D.contrast(g["fg"], g["bg"]) >= 4.5, (st, g["id"])
        for c in p["fx"]["cutaways"]:
            for g in c.get("graphics") or []:
                if g.get("kind") == "sticker_text":
                    assert D.contrast(g["fg"], g["bg"]) >= 4.5, (st, c["id"])
    # minimal: acento blanco -> el marcador no puede ser blanco con texto blanco
    ch = D._Choreo(synthetic_plan(), synthetic_plan()["shots"],
                   D.analyze_script(synthetic_plan(), use_llm=False), "minimal", 0.75, 7)
    spec = {"preset": "highlight_marker", "color": "#FFFFFF", "accent": "#FFFFFF"}
    ch._apply_ink(spec, None)
    assert D.contrast(D.box_text_color(spec), spec["box"]) >= 4.5


def test_type_kit_limits(styled):
    """Como maximo 4 fuentes y 3 familias en titulos + escenas (sin subtitulos)."""
    for st, p in styled.items():
        fonts = set()
        for it in _all_text(p):
            fonts |= _fonts_of(it)
        fams = {D.font_family(f) for f in fonts}
        assert len(fonts) <= 4 and len(fams) <= 3, (st, fonts)
        assert set(p["director"]["kit"]) >= {"display", "bold", "text", "accent"}


def test_blocks_duration_and_presets(styled):
    """Bloques por clausula: todo texto >= 0.8 s y a lo mas 2 presets por frase."""
    for st, p in styled.items():
        tl = _tl(p)
        per_sent: dict = {}
        for it in p["fx"]["text"]:
            a, b = tl.remap_clamped(it["start"]), tl.remap_clamped(it["end"])
            assert b - a >= 0.8 - 1e-6, (st, it["id"], b - a)
            per_sent.setdefault(_sentence_of(p, it["start"]), set()).add(it["preset"])
        for sid, ps in per_sent.items():
            assert len(ps) <= 2, (st, sid, ps)


def _text_rects(p, style):
    tl = _tl(p)
    out = []
    for it in p["fx"]["text"]:
        r = D.measure_text(it, style)
        if r is None:
            continue
        x0, y0, x1, y1 = r
        if it.get("box") or it.get("preset") in ("highlight_marker", "kicker"):
            s = float(it["size"])
            x0, x1, y0, y1 = x0 - 0.35 * s, x1 + 0.35 * s, y0 - 0.25 * s / (16 / 9), y1 + 0.25 * s / (16 / 9)
        out.append((tl.remap_clamped(it["start"]), tl.remap_clamped(it["end"]), (x0, y0, x1, y1), it["id"]))
    return out


def _check_accents(p, style):
    tl = _tl(p)
    rects = _text_rects(p, style)
    for g in p["fx"]["graphics"]:
        if g["kind"] not in ("icon", "doodle") or g.get("name") in ("underline", "circle_scribble"):
            continue
        a, b = tl.remap_clamped(g["start"]), tl.remap_clamped(g["end"])
        assert b - a >= 0.8 - 1e-6, (style, g["id"], b - a)            # el icono alcanza a dibujarse
        hw, hh = g["size"] / 2, g["size"] / 2 / (16 / 9)
        box = (g["x"] - hw, g["y"] - hh, g["x"] + hw, g["y"] + hh)
        for ta, tb, r, tid in rects:
            if not (a < tb - 1e-3 and ta < b - 1e-3):
                continue
            inter = box[0] < r[2] and r[0] < box[2] and box[1] < r[3] and r[1] < box[3]
            assert not inter, (style, g["id"], tid, box, r)
        if g["kind"] == "icon" and g.get("badge", "none") != "none":
            assert g["name"] not in D.ICON_BLOCK


def test_accents_clear_of_text(styled):
    for st, p in styled.items():
        _check_accents(p, st)


def test_cta_does_not_crowd_hero_text(styled):
    for st, p in styled.items():
        tl = _tl(p)
        heroes = [(tl.remap_clamped(it["start"]), tl.remap_clamped(it["end"])) for it in p["fx"]["text"]
                  if it["preset"] in ("stack", "behind_giant")]
        for g in p["fx"]["graphics"]:
            if g["kind"] != "cta":
                continue
            a, b = tl.remap_clamped(g["start"]), tl.remap_clamped(g["end"])
            for ha, hb in heroes:
                assert min(b, hb) - max(a, ha) <= 1.5, (st, g["id"])
        # el cierre queda limpio: nada de titulos en el tramo final del CTA
        D_ = tl.final_duration
        assert not any(tl.remap_clamped(it["end"]) > D_ - 1.0 for it in p["fx"]["text"]), st


def _shot(start, end, rows=None, face=(0.5, 0.33, 0.14, 0.11), use="a_camara"):
    s = {"start": start, "end": end, "person": use == "a_camara", "framing": "medio",
         "place": "consultorio", "text_seen": [], "use": use, "free_space": "arriba",
         "face": list(face) if face else None}
    if rows is not None:
        s["rows"] = [list(rows)] * 16
    return s


def test_ink_follows_background():
    """Tinta con contraste contra el fondo real: clara sobre fondo oscuro, oscura sobre claro;
    el amarillo no va sobre un fondo de su mismo tono."""
    base = synthetic_plan()
    dur = base["duration"]
    for rows, want_dark in (((236, 234, 228), True), ((22, 22, 26), False)):
        p = copy.deepcopy(base)
        p["shots"] = [_shot(0.0, 15.5, rows), _shot(15.5, 19.0, rows, None, "broll"),
                      _shot(19.0, dur, rows)]
        out = D.direct(p, use_llm=False, trim=False)
        items = [it for it in out["fx"]["text"] if it["preset"] not in ("behind_giant",)]
        assert items
        for it in items:
            dark_ink = D.rel_lum(it["color"]) < 0.2
            assert dark_ink == want_dark, (rows, it["id"], it["color"])
            if not it.get("box"):
                assert D.contrast(it["color"], rows) >= 4.5, (it["id"], it["color"])
    ch = D._Choreo(base, base["shots"], D.analyze_script(base, use_llm=False), "dinamico", 0.75, 7)
    spec = {"preset": "stack", "accent": "#FFD23F"}
    olive = (74, 78, 46)
    ch._apply_ink(spec, {"rgb": olive, "L": D.rel_lum(olive), "sat": D._hsv(olive)[1]})
    assert spec["accent"] != "#FFD23F" and D.contrast(spec["accent"], olive) >= 3.0


def test_no_face_detector_still_directs():
    """Sin video ni tomas (o sin detector de rostros) la edicion no se degrada: hay zoom por
    corte, punch y blanco y negro dramatico, no ken burns sobre una cabeza parlante."""
    p = synthetic_plan()
    p.pop("shots")
    out = D.direct(p, use_llm=False)
    kinds = [it["kind"] for it in out["fx"]["effects"]]
    assert "zoom" in kinds and "punch" in kinds and "bw" in kinds
    assert "kenburns" not in kinds
    q = synthetic_plan()
    for s in q["shots"]:
        if s["use"] == "a_camara":
            s["face"] = None
    out = D.direct(q, use_llm=False)
    kinds = [it["kind"] for it in out["fx"]["effects"]]
    assert kinds.count("kenburns") <= 1 and "punch" in kinds and "bw" in kinds


def test_info_card_draws_one_card(monkeypatch):
    """Escena info_card: la tarjeta va en `card` y el renderer dibuja UNA sola tarjeta."""
    monkeypatch.setattr(D, "card_height", lambda item, style="dinamico": 0.95)   # no cabe sobre la toma
    p = synthetic_plan()
    p["shots"] = [p["shots"][0], p["shots"][1], dict(p["shots"][3], start=15.5)]   # sin b-roll mudo
    out = D.direct(p, use_llm=False, trim=False, intensity=1.0)
    infos = [c for c in out["fx"]["cutaways"] if c["kind"] == "info_card"]
    assert infos, [c["kind"] for c in out["fx"]["cutaways"]]
    for c in infos:
        assert isinstance(c.get("card"), dict) and c["card"]["kind"] == "card"
        assert not any(g.get("kind") == "card" for g in c.get("graphics") or [])
    from motion import cutaways, graphics
    from motion.ctx import FrameCtx, new_frame
    calls = []
    orig = graphics.draw_card
    monkeypatch.setattr(graphics, "draw_card", lambda *a, **k: (calls.append(1), orig(*a, **k))[1])
    ctx = FrameCtx(W=270, H=480, style="dinamico")
    _, surf = new_frame(270, 480)
    dur = infos[0]["end"] - infos[0]["start"]
    for lt in (dur * 0.5, dur * 0.95):
        calls.clear()
        cutaways.RENDERERS["info_card"](surf.getCanvas(), infos[0], lt, dur, ctx)
        assert len(calls) == 1


def test_scene_budget_and_transitions(styled):
    p = styled["dinamico"]
    tl = _tl(p)
    scene = sum(tl.remap_clamped(c["end"]) - tl.remap_clamped(c["start"]) for c in p["fx"]["cutaways"])
    scene += sum(tl.remap_clamped(e["end"]) - tl.remap_clamped(e["start"]) for e in p["fx"]["effects"]
                 if e["kind"] in ("bw", "bg_replace", "color_pop"))
    assert scene / tl.final_duration >= 0.12
    assert any(e["kind"] == "flash_frame" for e in p["fx"]["effects"])
    assert any(e["kind"] == "bg_replace" for e in p["fx"]["effects"])
    trs = [tl.remap_clamped(t["at"]) for t in p["fx"]["transitions"]]
    for c in p["fx"]["cutaways"]:
        assert any(abs(t - tl.remap_clamped(c["start"])) < 0.12 for t in trs)
        assert any(abs(t - tl.remap_clamped(c["end"])) < 0.35 for t in trs)
    # nunca el mismo tipo que alguna de las dos transiciones anteriores (en orden temporal)
    kinds = [t["kind"] for t in sorted(p["fx"]["transitions"], key=lambda t: t["at"])]
    for i in range(2, len(kinds)):
        assert kinds[i] not in kinds[i - 2:i], kinds


def test_sfx_not_monotonous(styled):
    for st, p in styled.items():
        names = [s["name"] for s in p["fx"]["sfx"]]
        top = Counter(names).most_common(1)[0]
        assert top[1] / len(names) <= 0.3, (st, top)
        for s in p["fx"]["sfx"]:
            if s["name"] == "pen_scribble":
                assert s["gain_db"] <= -2.0


def nutrition_plan():
    """Otro rubro (nutricion) para detectar reglas ajustadas al guion de prueba."""
    lines = ["¿Te cuesta bajar de peso aunque comes poco?", "Escucha bien.",
             "Soy nutrióloga y una dieta personalizada acelera tu metabolismo todo el día.",
             "La consulta cuesta seiscientos pesos.", "Estoy en Calle Morelos 250, frente al Parque Azul.",
             "Agenda en el link de mi perfil y empieza hoy."]
    phrases, t = [], 0.5
    for txt in lines:
        ws, t = _phrase(txt, t)
        t += 0.5
        for k in range(0, len(ws), 3):
            phrases.append(ws[k:k + 3])
    dur = round(t + 1.0, 3)
    shots = [_shot(0.0, 10.0), _shot(10.0, dur, face=(0.45, 0.3, 0.12, 0.1))]
    return {"stem": "nutri", "video": "", "duration": dur, "dims": [1080, 1920], "phrases": phrases,
            "cuts": [], "beats": [], "camera": [], "highlights": [], "shots": shots}


def test_other_topic_no_overfitting():
    p = D.direct(nutrition_plan(), use_llm=False)
    _check_catalog(p)
    _check_zones(p)
    _check_safe(p)
    e = p["entities"]
    assert e["price"] == "$600 MXN" and e["address"] == "Calle Morelos 250"
    assert e["role"] == "Nutricion" and e["link_label"] == "Agenda tu cita"
    icons = [g["name"] for g in p["fx"]["graphics"] if g["kind"] == "icon"]
    assert "dental" not in icons and not set(icons) & D.ICON_BLOCK
    assert any(it["kind"] == "bw" for it in p["fx"]["effects"])          # "Escucha bien."
    _check_accents(p, "dinamico")


def test_llm_keeps_question_intrigue():
    p = synthetic_plan()
    script = D.analyze_script(p, p["shots"], use_llm=False)
    assert script["sents"][0]["info"]["tone"] == "intriga"
    fake = {"topic": "", "ent": {}, "f": [{"id": 0, "sec": "hook", "title": "", "emph": [], "fx": "titulo",
                                             "icon": [], "nrg": "alta", "tone": "informativo"}]}
    D._merge_llm(script, fake, script["words"])
    assert script["sents"][0]["info"]["tone"] == "intriga"


def test_trims_proposed_once_and_respected():
    p = synthetic_plan()
    p["cuts"] = []
    words = D._flatten_words(p)
    trims = D.propose_trims(p, p["shots"], words, keep=2.0)
    assert trims and all(c["src"] == "ai" and c["enabled"] for c in trims)
    q = dict(p, cuts=trims)
    tl = D._timeline(q)
    b = p["shots"][2]
    assert tl.remap_clamped(b["end"]) - tl.remap_clamped(b["start"]) <= 2.05
    # el usuario desactivo el recorte: no se vuelve a proponer y el director lo respeta
    off = [dict(c, enabled=False) for c in trims]
    assert D.propose_trims(dict(p, cuts=off), p["shots"], words) == []
    out = D.direct(dict(p, cuts=off), use_llm=False)
    assert [c for c in out["cuts"] if c.get("src") == "ai"] == off
    # sin recorte: el b-roll largo queda anotado en las notas del director
    out = D.direct(p, use_llm=False, trim=False)
    assert not [c for c in out["cuts"] if c.get("src") == "ai"]


@need_real
def test_dentista_quality(dentista):
    """Revision del critico sobre el caso real: contraste, acentos, tipografia, bloques."""
    p = D.direct(dentista, use_llm=False)
    for it in _all_text(p):
        if it.get("box"):
            assert D.contrast(D.box_text_color(it), it["box"]) >= 4.5
    fonts = set()
    for it in _all_text(p):
        fonts |= _fonts_of(it)
    assert len(fonts) <= 4 and len({D.font_family(f) for f in fonts}) <= 3, fonts
    _check_accents(p, "dinamico")
    tl = _tl(p)
    for it in p["fx"]["text"]:
        assert tl.remap_clamped(it["end"]) - tl.remap_clamped(it["start"]) >= 0.8
    # la tinta sobre el fondo reemplazado (papel claro) es oscura
    bg = [e for e in p["fx"]["effects"] if e["kind"] == "bg_replace"]
    if bg and bg[0]["background"] == "paper":
        over = [it for it in p["fx"]["text"] if it["start"] < bg[0]["end"] and bg[0]["start"] < it["end"]
                and it["y"] < 0.3]
        assert over and all(D.rel_lum(it["color"]) < 0.2 for it in over)
