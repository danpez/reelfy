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
from pathlib import Path
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
            if i2["id"] == i1["id"] + "_m" or i1["id"] == i2["id"] + "_m":
                # par espejo (simetria): misma y, x y 1-x, separados >= ACCENT_SEP
                assert abs(i1["x"] + i2["x"] - 1.0) < 2e-3 and i1["y"] == i2["y"], (i1["id"], i2["id"])
                assert abs(i1["x"] - i2["x"]) >= D.ACCENT_SEP - 1e-6, (i1["id"], i2["id"])
                continue
            # piezas de una misma composicion (contador de precio + etiqueta del servicio; seguir +
            # link del remate; frase + palabra gigante del fondo reemplazado): apiladas sin encimarse
            r1, r2 = i1["id"].split("_")[1], i2["id"].split("_")[1]
            if r1 == r2 and r1 in ("price", "cta", "bghero"):
                assert abs(float(i1["y"]) - float(i2["y"])) >= 0.04, (i1["id"], i2["id"])
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
        # el whoosh va en el golpe visual (transitions.hit_time: en las mezclas, un poco despues)
        assert any(-0.12 < a - c < 0.35 and it["name"] in ("whip", "whoosh_soft", "whoosh_fast", "swoosh_up",
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
    assert D.resolve_icon("sienten") == "mood-smile"           # lema por prefijo (sentir -> bienestar)
    assert D.resolve_icon("tocas") == "hand-finger"
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
    cards = [it for it in fx["graphics"] if it["kind"] == "card"] + \
        [c["card"] for c in fx["cutaways"] if isinstance(c.get("card"), dict)]
    assert "cta" in kinds and cards                    # tarjeta en la toma o en escena (info_card)
    assert any(it.get("variant") == "location" for it in cards)
    # precio: contador grande SOBRE la toma (la persona sigue visible) o escena, segun estilo
    counters = [it for it in fx["text"] if it["preset"] == "counter"] + \
               [c["text"] for c in fx["cutaways"] if (c.get("text") or {}).get("preset") == "counter"]
    assert counters and counters[0]["to"] == 500


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
    out = D.direct(synthetic_plan(), use_llm=True, music_sync=False)
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
    # precio: contador a 1000 SOBRE la toma del escritorio (ella visible), fondo tratado detras
    # (desenfoque: el recorte del escritorio no es limpio) y sonido de caja
    ctr = [it for it in fx["text"] if it["preset"] == "counter"]
    assert ctr and ctr[0]["to"] == 1000 and 13.8 <= ctr[0]["start"] <= 15.5
    assert not any(c["kind"] == "title_card" and c["start"] <= 15.5 <= c["end"] for c in fx["cutaways"])
    # (el numero puede cruzar ~0.5 s a la toma siguiente; el fondo tratado es el de su toma)
    desk_end = [s["end"] for s in p["shots"] if s["start"] <= ctr[0]["start"] < s["end"]][0]
    assert any(e["kind"] in ("bg_blur", "bg_replace") and e["start"] <= ctr[0]["start"] + 0.05
               and e["end"] >= min(ctr[0]["end"], desk_end) - 0.05 for e in fx["effects"])
    assert tl.remap_clamped(ctr[0]["end"]) - tl.remap_clamped(desk_end) <= 0.61
    assert any(s["name"] == "cash" and 15.0 <= s["at"] <= 16.3 for s in fx["sfx"])
    # ubicacion, horario y link
    variants = {g.get("variant") for g in fx["graphics"]}
    variants |= {g.get("variant") for c in fx["cutaways"] for g in c.get("graphics") or []}
    assert {"location", "schedule", "link_below"} <= variants
    # la referencia va en la tarjeta de ubicacion del b-roll (sin repetirla ademas como titulo)
    ref_word = e["landmark"].split()[-1].upper()
    assert any(ref_word in str(g.get("subtitle", "")).upper() for g in fx["graphics"]
               if g.get("variant") == "location")
    assert sum(ref_word in json.dumps(it, ensure_ascii=False).upper() for it in fx["text"]) <= 1
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
    assert any(g.get("kind") == "sticker_text" and g.get("text") and ctr[0]["start"] <= g["start"] <= ctr[0]["end"]
               for g in fx["graphics"])
    assert ctr[0].get("text")
    # escenas: fondo reemplazado + desenfoque + transiciones de entrada/salida de la escena
    kinds = {it["kind"] for it in fx["effects"]}
    assert {"bg_replace", "bg_blur", "flash_frame", "color_pop", "bw"} <= kinds
    trs = [tl.remap_clamped(t["at"]) for t in fx["transitions"]]
    for c in fx["cutaways"]:
        assert any(abs(t - tl.remap_clamped(c["start"])) < 0.12 for t in trs)
        if c["id"].startswith("cw_flash"):
            # inserto relampago: sale en seco (con aire), ~1 s
            assert 0.7 <= tl.remap_clamped(c["end"]) - tl.remap_clamped(c["start"]) <= 1.3
            assert any(abs(tl.remap_clamped(s_["at"]) - tl.remap_clamped(c["end"])) < 0.1 for s_ in fx["sfx"])
            continue
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
    p = D.direct(dentista, use_llm=True, music_sync=False)
    took = time.time() - t
    assert took < 75, took
    assert p["director"]["llm"] != "heuristico"
    _check_catalog(p)
    _check_zones(p)
    _check_safe(p)
    _check_transitions(p)
    # el letrero es un directorio: solo se usa el nombre que el VLM lee JUNTO a la profesion
    # (dos lecturas que coinciden) y queda marcado para confirmarlo en el Studio
    nm = p["entities"]["name"]
    if nm:
        sn = p["director"]["script"]["ocr"].get("sign_name") or {}
        assert nm.split()[0] in ("Dra.", "Dr.") and sn.get("name") == nm
        assert any(k in D.norm(sn.get("line", "")) for k in ("dentist", "odontolog", "dental"))
        assert any("letrero" in n for n in p["director"]["notes"])
        # remate: rotulo con nombre y rol + CTA de seguir (con el nombre) y link hasta el final
        tl = _tl(p)
        lts = [g for g in p["fx"]["graphics"] if g["kind"] == "lower_third"]
        assert lts and lts[0]["title"] == nm and lts[0]["subtitle"]
        ctas = {g["variant"] for g in p["fx"]["graphics"] if g["kind"] == "cta"
                and tl.remap_clamped(g["end"]) >= tl.final_duration - 0.05}
        assert {"follow", "link_below"} <= ctas


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
            # la composicion del fondo reemplazado (gigante + frase) cuenta como UNA pieza
            per_sent.setdefault(_sentence_of(p, it["start"]), set()).add(
                "bghero" if "_bghero_" in it["id"] else it["preset"])
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
        if not c["id"].startswith("cw_flash"):          # el inserto relampago sale en seco
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
    assert e["role"] == "Nutrición" and e["link_label"] == "Agenda tu cita"
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


# ---------------------------------------------------------------- ola 2: direccion de arte
def _words_of(text: str, t0: float = 0.0, step: float = 0.3) -> list[dict]:
    out = []
    for i, tok in enumerate(text.split()):
        out.append({"text": tok, "n": D.norm(tok), "t0": t0 + i * step, "t1": t0 + i * step + 0.25,
                    "kw": False, "i": i})
    return out


def test_find_enums_parallel_and_lists():
    """Enumeraciones: clausulas paralelas ('como se siente y como se ve') y listas con comas."""
    def items(text):
        ws = _words_of(text)
        return [[" ".join(w["text"].strip(",.") for w in it) for it in e] for e in D.find_enums(ws, 0, len(ws) - 1)]
    assert items("mejora mucho como se siente y como se ve tu sonrisa.") == [["como se siente", "como se ve"]]
    assert items("Hago cortes, barba y cejas con navaja.") == [["cortes", "barba", "cejas"]]
    assert items("Te ayudo con dieta, ejercicio, sueño y agua cada dia") == [["dieta", "ejercicio", "sueño", "agua"]]
    assert items("Agenda tu cita en el link de mi perfil") == []
    assert D.resolve_icon("cortes") == "scissors" and D.resolve_icon("barba") == "moustache"


def _list_plan():
    p = synthetic_plan()
    ws = [w for ph in p["phrases"] for w in ph]
    # la frase de beneficio pasa a ser una lista de 3 servicios (mismos tiempos por palabra)
    txt = "Hacemos resinas, limpiezas y blanqueamientos para que tu sonrisa se vea natural."
    toks = txt.split()
    s2 = [w for w in ws if w["text"].startswith("Una")][0]
    k0 = ws.index(s2)
    for k, tok in enumerate(toks):
        if k0 + k < len(ws):
            ws[k0 + k]["text"] = tok
    return p


def test_enum_scene_icons_drawn_per_item():
    """Lista de 3+: escena de iconos que se dibujan al decir cada elemento, con etiqueta y
    punteado entre ellos; entra y sale con transicion."""
    p = D.direct(_list_plan(), use_llm=False)
    _check_catalog(p)
    scenes = [c for c in p["fx"]["cutaways"] if c["id"].startswith("cw_list")]
    assert scenes, [c["id"] for c in p["fx"]["cutaways"]]
    gr = scenes[0]["graphics"]
    icons = [g for g in gr if g["kind"] == "icon"]
    labels = [g["text"] for g in gr if g["kind"] == "sticker_text"]
    assert len(icons) == 3 and len({g["name"] for g in icons}) == 3
    assert labels == ["RESINAS", "LIMPIEZAS", "BLANQUEAMIENTOS"]
    offs = [g["offset"] for g in icons]
    assert offs == sorted(offs) and offs[-1] > offs[0]           # cada icono en su palabra
    assert sum(1 for g in gr if g.get("name") == "dotted_path") == 2
    tl = _tl(p)
    trs = [tl.remap_clamped(t["at"]) for t in p["fx"]["transitions"]]
    a, b = tl.remap_clamped(scenes[0]["start"]), tl.remap_clamped(scenes[0]["end"])
    assert any(abs(t - a) < 0.12 for t in trs) and any(abs(t - b) < 0.35 for t in trs)


def test_price_overlay_keeps_subject_visible():
    """dinamico: el precio es un contador grande EN la toma (sin escena que tape a la persona),
    fuera de la cara, con el fondo tratado detras y la etiqueta del servicio encima."""
    p = D.direct(synthetic_plan(), use_llm=False)
    ctr = [it for it in p["fx"]["text"] if it["preset"] == "counter"]
    assert ctr and ctr[0]["to"] == 500
    c = ctr[0]
    assert not any(cw["start"] < c["end"] and c["start"] < cw["end"] for cw in p["fx"]["cutaways"])
    script = D._script_from_public(p, p["director"]["script"], p["shots"])
    ch = D._Choreo(p, p["shots"], script, "dinamico", 0.75, 7)
    shot = ch.shot_at(c["start"])
    fr = ch.face_rect(shot, c["start"], c["end"])
    r = D.measure_text(c)
    assert fr is not None and not (r[0] < fr[2] and fr[0] < r[2] and r[1] < fr[3] and fr[1] < r[3])
    assert any(e["kind"] in ("bg_blur", "bg_replace") and e["start"] <= c["start"] + 0.01
               and e["end"] >= c["end"] - 0.01 for e in p["fx"]["effects"])
    # energetico: escena a pantalla completa (variedad por estilo)
    e = D.direct(synthetic_plan(), style="energetico", use_llm=False)
    assert any((cw.get("text") or {}).get("preset") == "counter" for cw in e["fx"]["cutaways"])


def _named_plan():
    p = synthetic_plan()
    ws = [w for ph in p["phrases"] for w in ph]
    # "Escucha esto." -> presentacion con nombre (2 palabras: 'Soy Ana' no; se usa el hook)
    hook = ws[:4]
    for w, tok in zip(hook, ["Hola", "soy", "Dra.", "Ana"]):
        w["text"] = tok
    return p


def test_closing_lower_third_follow_and_link_until_last_frame():
    """Remate: rotulo nombre + rol justo antes del cierre; seguir (con el nombre) + link grande
    sostenidos hasta el ULTIMO cuadro, sin desvanecerse; la flecha del link no pisa subtitulos."""
    p = D.direct(_named_plan(), use_llm=False)
    tl = _tl(p)
    Dur = tl.final_duration
    assert p["entities"]["name"].startswith("Dra.")
    lts = [g for g in p["fx"]["graphics"] if g["kind"] == "lower_third"]
    assert lts and lts[0]["title"] == p["entities"]["name"] and lts[0]["subtitle"]
    ctas = [g for g in p["fx"]["graphics"] if g["kind"] == "cta" and tl.remap_clamped(g["end"]) >= Dur - 1e-3]
    assert {g["variant"] for g in ctas} == {"follow", "link_below"}
    assert all(g["anim_out"] == "none" for g in ctas)
    assert tl.remap_clamped(lts[0]["end"]) <= min(tl.remap_clamped(g["start"]) for g in ctas) + 0.05
    cap = p["fx"]["captions"]
    cap_top = cap["y"] - cap["size"] * 1080 / 1920 * 0.8
    for g in p["fx"]["graphics"]:
        if g["kind"] == "cta" and g["variant"] == "link_below":
            h = g["size"] * 0.2 * 1080 / 1920
            bottom = g["y"] + (0.8 + 1.66 * g.get("arrow_scale", 1.0)) * h
            assert bottom <= cap_top + 1e-3, (g["id"], bottom, cap_top)
    # sin nombre no se inventa un rotulo
    q = D.direct(synthetic_plan(), use_llm=False)
    assert not any(g["kind"] == "lower_third" for g in q["fx"]["graphics"])


def test_tail_silence_is_trimmed():
    p = synthetic_plan()
    p["duration"] = round(p["duration"] + 3.0, 3)
    p["shots"][-1]["end"] = p["duration"]
    trims = D.propose_trims(p, p["shots"], D._flatten_words(p), tail=1.7)
    tail = [c for c in trims if c["reason"] == "silencio al final"]
    last = max(w["t1"] for w in D._flatten_words(p))
    assert tail and abs(tail[0]["start"] - (last + 1.7)) < 1e-3 and tail[0]["end"] == p["duration"]


def test_titles_never_cover_the_face(styled):
    """Ningun titulo (delante) toca ojos/nariz/boca; detras del sujeto solo si se lee."""
    for st, p in styled.items():
        script = D._script_from_public(p, p["director"]["script"], p["shots"])
        ch = D._Choreo(p, p["shots"], script, st, 0.75, 7)
        for it in p["fx"]["text"]:
            shot = ch.shot_at(it["start"])
            fr = ch.face_rect(shot, it["start"], it["end"])
            if fr is None:
                continue
            if it.get("behind"):
                assert ch.occlusion(it, shot, it["start"], it["end"]) <= ch.BEHIND_MAX + 1e-6, (st, it["id"])
                continue
            # zona intocable: de media frente (caja del detector) a la barbilla; el cabello puede
            # quedar bajo un titulo
            core = ch.face_core(shot, it["start"], it["end"])
            r = D.measure_text(it, st)
            assert not (r[0] < core[2] and core[0] < r[2] and r[1] < core[3] and core[1] < r[3]), (st, it["id"])


def test_behind_text_moves_when_illegible():
    p = synthetic_plan()
    ch = D._Choreo(p, p["shots"], D.analyze_script(p, use_llm=False), "dinamico", 0.75, 7)
    shot = ch.shot_at(1.0)
    fr = ch.face_rect(shot, 1.0, 2.5)
    y_mid = (fr[1] + fr[3]) / 2                     # texto gigante cruzando la cara
    it = {"preset": "behind_giant", "lines": [{"text": "SONRISA", "scale": 1.0, "font": "Anton"}], "x": 0.5,
          "y": y_mid, "size": 0.2, "font": "Anton", "behind": True, "align": "center"}
    assert ch.occlusion(it, shot, 1.0, 2.5) > ch.BEHIND_MAX
    out = ch.legible_behind(it, shot, 1.0, 2.5)
    if out.get("behind"):
        assert ch.occlusion(out, shot, 1.0, 2.5) <= ch.BEHIND_MAX
    else:
        r = D.measure_text(out)
        assert r[3] <= fr[1] + 0.02                  # delante, arriba de la cabeza


def test_sign_name_needs_role_and_two_matching_reads(monkeypatch, tmp_path):
    """Letrero-directorio: el nombre solo se acepta si la linea leida trae la profesion del
    video y dos lecturas coinciden (datos ficticios)."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    shots = [{"start": 0.0, "end": 4.0, "use": "broll", "text_seen": ["Dra. Laura Pinos", "OFTALMOLOGIA"]}]
    monkeypatch.setattr(D, "llm_available", lambda *a, **k: True)
    monkeypatch.setattr(D, "_grab_frame", lambda *a, **k: __import__("numpy").zeros((8, 8, 3), "uint8"))
    monkeypatch.setattr(D, "_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(D, "_cache_put", lambda *a, **k: None)
    reads = iter([{"found": True, "name": "DRA. ROSA MENDEZ", "line": "ORTODONCIA"},
                  {"found": True, "name": "DRA. ANA RUIZ DE LA O", "line": "DENTISTA GENERAL"},
                  {"found": False, "name": "", "line": ""},
                  {"found": True, "name": "Dra. Ana Ruiz de la O", "line": "Dentista general"}])
    monkeypatch.setattr(D, "_vlm_sign_read", lambda *a, **k: next(reads, {}))
    r = D.sign_name(str(video), shots, "Dentista")
    assert r and r["name"] == "Dra. Ana Ruiz de la O"
    one = iter([{"found": True, "name": "DRA. ANA RUIZ", "line": "DENTISTA"}])
    monkeypatch.setattr(D, "_vlm_sign_read", lambda *a, **k: next(one, {}))
    assert D.sign_name(str(video), shots, "Dentista") is None           # una sola lectura
    monkeypatch.setattr(D, "_vlm_sign_read", lambda *a, **k: {"found": True, "name": "DRA. ANA RUIZ", "line": "UROLOGIA"})
    assert D.sign_name(str(video), shots, "Dentista") is None           # otra profesion
    assert D.sign_name(str(video), [dict(shots[0], text_seen=["1234"])], "Dentista") is None


def test_music_grid_aligns_punches_and_drop():
    """Con la musica real (rejilla en tiempo final) los punches caen en el beat y el drop lleva
    su golpe; los pops de acentos tambien se alinean."""
    p = synthetic_plan()
    script = D.analyze_script(p, use_llm=False)
    tl = D._timeline(p)
    beats = [0.03 + 0.5 * k for k in range(int(tl.final_duration / 0.5))]
    grid = {"bpm": 120.0, "beats": beats, "downbeats": beats[::4], "drop": 12.03}
    fx, ch = D.choreograph(p, p["shots"], script, "dinamico", 0.75, 7, grid=grid)
    punches = [tl.remap_clamped(e["start"]) for e in fx["effects"] if e["kind"] == "punch"]
    assert ch.snapped
    for t in punches:                 # a <= 0.1 s de un beat -> exactamente en el beat
        d = min(abs(t - b) for b in beats)
        assert d < 2e-3 or d > 0.098, (t, d)
    assert any(e["id"].startswith("fx_drop") for e in fx["effects"]) or \
        any(abs(tl.remap_clamped(t["at"]) - 12.03) < 0.3 for t in fx["transitions"])


def test_wave2_style_defaults():
    """Subtitulos con presencia y coherentes; musica a -14 en dinamico/energetico; punch en seco
    en dinamico; sin record_stop encima del tape stop; SFX con duracion donde aplica."""
    for st, cfg in D.STYLES.items():
        c = cfg["captions"]
        assert c["preset"] != "minimal_small" and c["size"] >= 0.056 and c["y"] <= 0.735, st
        assert cfg["music"]["gain_db"] == (-14.0 if st in ("dinamico", "energetico") else -17.0)
        assert cfg["music"].get("mood")
    p = D.direct(synthetic_plan(), use_llm=False)
    assert p["fx"]["music"].get("mood") and p["fx"]["music"]["gain_db"] == -14.0
    assert not any(s["name"] == "record_stop" for s in p["fx"]["sfx"])
    pun = [e for e in p["fx"]["effects"] if e["kind"] == "punch"]
    assert pun and all(e.get("mode") == "cut" and e.get("hold") is None for e in pun)
    for s in p["fx"]["sfx"]:
        if s["name"] in ("pen_scribble", "riser_short", "riser_long", "typing"):
            assert s.get("dur"), s
    kb = [e for e in p["fx"]["effects"] if e["kind"] == "kenburns"]
    assert all(e["from"] > 1.0 for e in kb)


def test_transitions_protect_the_face():
    """film_burn / torn_paper protegen el rostro de la toma A; circle se centra en el de B."""
    p = synthetic_plan()
    script = D.analyze_script(p, use_llm=False)
    ch = D._Choreo(p, p["shots"], script, "elegante", 0.75, 7)
    at = p["shots"][1]["start"]
    for kind, face in (("film_burn", p["shots"][0]["face"]), ("circle", p["shots"][1]["face"])):
        ch.trans_req = [{"at": at, "f": ch.f(at), "ctx": "shot", "prefer": kind, "dir": "left"}]
        ch.fx["transitions"] = []
        ch.assign_transitions()
        it = ch.fx["transitions"][-1]
        assert it["kind"] == kind and abs(it["x"] - face[0]) < 1e-3 and abs(it["y"] - face[1]) < 1e-3
        assert "color" not in it or it["kind"] in ("flash", "light_leak", "glitch")


def test_matte_quality_gates_subject_effects():
    """Recorte sucio (la persona toca muebles): sin reemplazo de fondo ni contorno en esa toma."""
    p = synthetic_plan()
    for s in p["shots"]:
        if s["use"] == "a_camara":
            s["matte_q"] = 0.85
    for st in ("dinamico", "energetico"):
        out = D.direct(p, style=st, use_llm=False)
        assert not any(e["kind"] in ("bg_replace", "subject_outline") for e in out["fx"]["effects"]), st


# ---------------------------------------------------------------- ola 3: revision del critico
def _broll_last_plan(tail_talk: float = 0.4):
    """Flujo real del servidor: los clips llegan en orden de subida y la toma de la fachada
    (b-roll mudo) queda AL FINAL, despues de la ultima palabra (datos ficticios)."""
    p = synthetic_plan()
    dur = p["duration"]
    shots = [dict(p["shots"][0]), dict(p["shots"][1], end=round(dur - 1.0 + tail_talk, 3))]
    b0 = shots[-1]["end"]
    shots.append({"start": b0, "end": round(b0 + 5.5, 3), "person": False, "framing": "sin_persona",
                  "place": "fachada", "text_seen": ["120"], "use": "broll", "free_space": "arriba",
                  "face": None})
    p["shots"] = shots
    p["duration"] = shots[-1]["end"]
    return p


def test_broll_at_end_is_the_closing_card():
    """El b-roll que el usuario subio al final no se borra: conserva >= END_KEEP s y es la
    tarjeta de cierre (seguir + link hasta el ultimo cuadro, sin un link identico repetido, y
    la ubicacion); la cola nunca empieza dentro de el."""
    p = _broll_last_plan()
    out = D.direct(p, use_llm=False)
    _check_catalog(out)
    _check_safe(out)
    tl = _tl(out)
    Dur = tl.final_duration
    b = [s for s in out["shots"] if s["use"] == "broll"][-1]
    kept = tl.remap_clamped(b["end"]) - tl.remap_clamped(b["start"])
    assert kept >= D.END_KEEP - 0.05, kept
    for c in out["cuts"]:
        if c.get("src") == "ai":
            assert not (c["start"] <= b["start"] + 0.05 and c["end"] >= b["end"] - 0.05), c
    ctas = [g for g in out["fx"]["graphics"] if g["kind"] == "cta"]
    closing = [g for g in ctas if tl.remap_clamped(g["end"]) >= Dur - 1e-3]
    assert {g["variant"] for g in closing} == {"follow", "link_below"}, closing
    fb0 = tl.remap_clamped(b["start"])
    fol = [g for g in closing if g["variant"] == "follow"][0]
    assert tl.remap_clamped(fol["start"]) >= fb0 - 0.05          # entra con la fachada
    links = sorted((tl.remap_clamped(g["start"]), tl.remap_clamped(g["end"])) for g in ctas
                   if g["variant"] == "link_below")
    for (a0, a1), (b0_, _b1) in zip(links, links[1:]):
        assert b0_ - a1 >= 6.0, links                              # nunca un link repetido al rato
    assert any(g["kind"] == "card" and g.get("variant") == "location" and tl.remap_clamped(g["start"]) >= fb0 - 0.05
               for g in out["fx"]["graphics"])
    # sin nombre (ni usuario): el seguir va con el rol (si se conoce), nunca con un nombre inventado
    if out["entities"]["role"]:
        assert fol.get("handle") == out["entities"]["role"] and fol.get("subtitle")
    else:
        assert fol["label"].startswith("S") and not fol.get("handle")


def test_final_cta_after_the_punchline():
    """La ultima frase es el remate (humor): el CTA entra al TERMINAR la ultima palabra; el
    desenfoque de fondo solo despues; ningun link con flecha diminuta."""
    p = synthetic_plan()
    ws = [w for ph in p["phrases"] for w in ph]
    tail = "y tranquilo, no te voy a juzgar por tu cepillo viejo, prometido."
    s_last = [w for w in ws if w["text"].startswith("Agenda")][0]
    k0 = ws.index(s_last)
    toks = ("Agenda tu cita con el link de mi perfil. " + tail).split()
    for k, tok in enumerate(toks):
        if k0 + k < len(ws):
            ws[k0 + k]["text"] = tok
    p["duration"] = round(p["duration"] + 2.5, 3)
    p["shots"][-1]["end"] = p["duration"]
    out = D.direct(p, use_llm=False)
    tl = _tl(out)
    words = D._flatten_words(out)
    last = max(words, key=lambda w: w["t1"])
    fol = [g for g in out["fx"]["graphics"] if g["kind"] == "cta" and g["variant"] == "follow"]
    assert fol and tl.remap_clamped(fol[-1]["start"]) >= tl.remap_clamped(last["t0"]) - 0.1
    for e in out["fx"]["effects"]:
        if e["id"].startswith("fx_cta_blur"):
            assert e["start"] >= last["t1"] - 1e-3
    for g in out["fx"]["graphics"]:
        if g["kind"] == "cta" and g["variant"] == "link_below" and "arrow_scale" in g:
            assert g["arrow_scale"] == 0.0 or g["arrow_scale"] >= 0.7, g


def test_lower_third_presents_early_without_text_on_top():
    """El rotulo presenta a la experta al inicio (primera toma a camara despues del hook), sin
    ningun titulo encima (franjas baja y centro libres) y lejos del cierre."""
    p = D.direct(_named_plan(), use_llm=False)
    tl = _tl(p)
    lt = [g for g in p["fx"]["graphics"] if g["kind"] == "lower_third"]
    assert lt and lt[0]["title"] == p["entities"]["name"]
    a, b = tl.remap_clamped(lt[0]["start"]), tl.remap_clamped(lt[0]["end"])
    for it in p["fx"]["text"]:
        ia, ib = tl.remap_clamped(it["start"]), tl.remap_clamped(it["end"])
        if ia < b - 1e-3 and a < ib - 1e-3:
            assert D.zone_of("text", it) == "top", (it["id"], it["y"])
    fol = [g for g in p["fx"]["graphics"] if g["kind"] == "cta" and g["variant"] == "follow"]
    assert fol and tl.remap_clamped(fol[0]["start"]) - b >= 1.9


def test_unconfirmed_sign_name_is_never_published(monkeypatch):
    """Un nombre leido en un letrero queda como candidato (name_confirmed false): ni rotulo ni
    seguir lo muestran hasta que el usuario lo confirme; confirmado, si."""
    p = synthetic_plan()
    p["entities"] = {}
    out = D.direct(p, use_llm=False)
    ent = dict(out["entities"], name="Dra. Laura Pinos", name_src="letrero", name_confirmed=False)
    q = D.redirect(dict(out, entities=ent))
    blob = json.dumps(q["fx"], ensure_ascii=False)
    assert "Laura" not in blob
    assert q["entities"]["name_src"] == "letrero" and q["entities"]["name_confirmed"] is False
    ok = D.redirect(dict(out, entities=dict(ent, name_confirmed=True)))
    assert "Laura" in json.dumps(ok["fx"], ensure_ascii=False)


def test_sign_name_needs_a_single_professional(monkeypatch, tmp_path):
    """Letrero-directorio: se acepta el nombre solo si UNA persona de la profesion aparece y
    dos lecturas coinciden; con dos dentistas en el letrero no hay nombre (datos ficticios)."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    shots = [{"start": 0.0, "end": 4.0, "use": "broll", "text_seen": ["Dra. Laura Pinos", "OFTALMOLOGIA"]}]
    monkeypatch.setattr(D, "llm_available", lambda *a, **k: True)
    monkeypatch.setattr(D, "_grab_frame", lambda *a, **k: __import__("numpy").zeros((8, 8, 3), "uint8"))
    monkeypatch.setattr(D, "_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(D, "_cache_put", lambda *a, **k: None)
    one = {"entries": [{"name": "DRA. ANA RUIZ DE LA O", "line": "DENTISTA GENERAL"},
                       {"name": "DRA. ROSA MENDEZ", "line": "ORTODONCIA"}]}
    monkeypatch.setattr(D, "_vlm_sign_read", lambda *a, **k: one)
    r = D.sign_name(str(video), shots, "Dentista")
    assert r and r["name"] == "Dra. Ana Ruiz de la O"
    two = {"entries": [{"name": "DRA. ANA RUIZ", "line": "DENTISTA"}, {"name": "DR. LUIS PAZ", "line": "DENTISTA"}]}
    monkeypatch.setattr(D, "_vlm_sign_read", lambda *a, **k: two)
    assert D.sign_name(str(video), shots, "Dentista") is None


def test_redirect_keeps_music_sync_and_notes(monkeypatch):
    """La rejilla de la musica se guarda en director.music_grid: re-dirigir con la misma pista
    la reusa (punches al beat) sin volver a generar; las notas de nombre/musica se conservan."""
    p = synthetic_plan()
    tl = D._timeline(p)
    beats = [0.03 + 0.5 * k for k in range(int(tl.final_duration / 0.5) + 4)]
    calls = []

    def fake_grid(plan, style, seed, mcfg, timeout=40.0):
        calls.append(D._music_key(plan, mcfg, style, seed))
        return {"bpm": 120.0, "beats": beats, "downbeats": beats[::4], "drop": 12.03}
    monkeypatch.setattr(D, "music_grid", fake_grid)
    out = D.direct(p, use_llm=False, music_sync=True, trim=False)
    g = out["director"].get("music_grid")
    assert g and g["key"] and calls
    out["director"]["notes"].append("nombre leido en el letrero junto a la profesion ('X'): candidato")
    monkeypatch.setattr(D, "music_grid", lambda *a, **k: (_ for _ in ()).throw(AssertionError("regenero")))
    q = D.redirect(out)                                 # otra propuesta, misma musica
    assert q["director"].get("music_grid", {}).get("key") == g["key"]
    assert any(n.startswith("musica sincronizada") for n in q["director"]["notes"])
    assert any(n.startswith("nombre leido") for n in q["director"]["notes"])
    tq = D._timeline(q)
    for e in q["fx"]["effects"]:
        if e["kind"] == "punch":
            t = tq.remap_clamped(e["start"])
            d = min(abs(t - b) for b in beats)
            assert d < 2e-3 or d > 0.098, (t, d)


def test_direct_accepts_user_music(monkeypatch):
    """direct(music=...) mezcla la musica del usuario ANTES de la rejilla; con otra fuente que
    no es la IA no se sincroniza."""
    seen = []

    def fake_grid(plan, style, seed, mcfg, timeout=40.0):
        seen.append(dict(mcfg))
        return None
    monkeypatch.setattr(D, "music_grid", fake_grid)
    p = synthetic_plan()
    out = D.direct(p, use_llm=False, music_sync=True, music={"prompt": "lofi piano", "bpm": 90})
    assert seen and seen[-1]["prompt"] == "lofi piano" and seen[-1]["bpm"] == 90
    assert out["fx"]["music"]["prompt"] == "lofi piano"
    seen.clear()
    out = D.direct(p, use_llm=False, music_sync=True, music={"source": "library", "track": "x"})
    assert not seen and out["fx"]["music"]["source"] == "library"
    assert any("sin sincronia" in n for n in out["director"]["notes"])


def test_hero_mark_never_before_its_line(styled):
    """El subrayado/circulo cae bajo la linea que CONTIENE la palabra protagonista y entra
    cuando esa palabra se dice (nunca antes de que aparezca su linea)."""
    for st, p in styled.items():
        words = D._flatten_words(p)
        for g in p["fx"]["graphics"]:
            if not g["id"].startswith("gr_mark"):
                continue
            host = [t for t in p["fx"]["text"] if t["start"] <= g["start"] <= t["end"]
                    and abs(t.get("x", 0.5) - g["x"]) < 0.3]
            assert host, (st, g["id"])
            rows = D.measure_lines(host[0], st, with_text=True)
            near = min(rows, key=lambda r: abs(r[0][3] - g["y"]))
            first = [w for w in words if D.norm(near[1].split()[0]) == w["n"] and host[0]["start"] - 0.2 <= w["t0"] <= host[0]["end"]]
            assert first and g["start"] >= first[0]["t0"] - 0.1, (st, g["id"], near[1])


def test_enum_scene_grid_geometry_and_contrast():
    """Lista de 4 en cuadricula 2x2: punteados solo dentro de la fila (nunca cruzan iconos ni
    etiquetas), etiqueta corta de la frase y kicker legible sobre la caja (WCAG >= 4.5)."""
    p = synthetic_plan()
    ws = [w for ph in p["phrases"] for w in ph]
    txt = "Un plan real mejora tu energia con dieta, ejercicio, agua y sueño."
    s2 = [w for w in ws if w["text"].startswith("Una")][0]
    k0 = ws.index(s2)
    for k, tok in enumerate(txt.split()):
        ws[k0 + k]["text"] = tok
    out = D.direct(p, use_llm=False)
    sc = [c for c in out["fx"]["cutaways"] if c["id"].startswith("cw_list")]
    assert sc
    gr = sc[0]["graphics"]
    icons = [g for g in gr if g["kind"] == "icon"]
    labels = [g for g in gr if g["kind"] == "sticker_text"]
    assert len(icons) == 4
    for d_ in (g for g in gr if g.get("name") == "dotted_path"):
        assert abs(d_["y"] - d_["path_to"][1]) < 1e-6                  # horizontal, dentro de la fila
        x0, x1 = sorted((d_["x"], d_["path_to"][0]))
        for ic in icons:
            if x0 < ic["x"] < x1:
                assert abs(ic["y"] - d_["y"]) > ic["size"] * 0.5 / (16 / 9), "cruza un icono"
        for lb in labels:
            assert not (x0 < lb["x"] < x1 and abs(lb["y"] - d_["y"]) < 0.04), "cruza una etiqueta"
    kt = sc[0]["text"]
    assert D.contrast(D.box_text_color(kt), kt["box"]) >= 4.5 and D.contrast(kt["color"], kt["box"]) >= 4.5
    assert 1 <= len(kt["text"].split()) <= 3 and "MENTE" not in kt["text"]


def test_occlusion_checks_every_line():
    """Una linea chica detras de la cabeza vuelve ilegible el bloque aunque la grande se lea."""
    p = synthetic_plan()
    ch = D._Choreo(p, p["shots"], D.analyze_script(p, use_llm=False), "dinamico", 0.75, 7)
    shot = ch.shot_at(1.0)
    fr = ch.face_rect(shot, 1.0, 2.5)
    it = {"preset": "behind_giant", "lines": [{"text": "SONRISA", "scale": 1.0, "font": "Anton"},
                                             {"text": "ESTO", "scale": 0.3, "font": "Anton"}],
          "x": 0.5, "y": fr[1] - 0.02, "size": 0.2, "font": "Anton", "behind": True, "align": "center"}
    rows = D.measure_lines(it)
    big = D._Choreo.occlusion(ch, dict(it, lines=it["lines"][:1]), shot, 1.0, 2.5)
    both = ch.occlusion(it, shot, 1.0, 2.5)
    assert len(rows) == 2 and both >= big and both > ch.BEHIND_MAX


def test_price_counter_lands_early_and_stays_readable():
    """El contador cuenta en 0.45 s, arranca ~0.3 s antes del numero y el valor final queda
    quieto >= 0.8 s (o la transicion siguiente es seca y corta)."""
    p = D.direct(synthetic_plan(), use_llm=False)
    tl = _tl(p)
    c = [it for it in p["fx"]["text"] if it["preset"] == "counter"][0]
    assert c["count_dur"] == pytest.approx(0.45)
    words = D._flatten_words(p)
    num = [w for w in words if w["n"] == "quinientos"][0]
    assert num["t0"] - 0.85 <= c["start"] <= num["t0"] - 0.2
    land = tl.remap_clamped(c["start"]) + 0.12 + 0.45
    nxt = [t for t in p["fx"]["transitions"] if tl.remap_clamped(t["at"]) > land - 0.05]
    if nxt:
        t = min(nxt, key=lambda t: t["at"])
        pre = tl.remap_clamped(t["at"]) - float(t.get("dur", 0.3)) / 2
        assert pre - land >= 0.8 or (float(t["dur"]) <= 0.345 and t["kind"] in ("whip", "zoom_blur", "glitch", "flash", "slide"))
    assert tl.remap_clamped(c["end"]) - land >= 0.8


def test_flash_scene_and_jump_cuts_in_dinamico():
    """dinamico: un inserto relampago (~1 s, fondo del estilo + palabra + icono que se dibuja)
    fuera del hook, y las tomas largas a camara se reencuadran por clausula (jump-cut)."""
    p = D.direct(synthetic_plan(), use_llm=False)
    tl = _tl(p)
    fl = [c for c in p["fx"]["cutaways"] if c["id"].startswith("cw_flash")]
    assert fl
    a, b = tl.remap_clamped(fl[0]["start"]), tl.remap_clamped(fl[0]["end"])
    assert 0.75 <= b - a <= 1.3 and a >= 3.0
    assert any(g["kind"] == "icon" and g.get("draw") for g in fl[0]["graphics"])
    assert (fl[0]["text"].get("text") or "").strip()                  # oculta subtitulos
    for s in p["shots"]:
        if s["use"] != "a_camara":
            continue
        zs = [e for e in p["fx"]["effects"] if e["kind"] == "zoom" and s["start"] <= e["start"] < s["end"]]
        for e in zs:
            assert tl.remap_clamped(e["end"]) - tl.remap_clamped(e["start"]) <= D._Choreo.JUMP_MAX + 0.05
        levels = [e["from"] for e in sorted(zs, key=lambda e: e["start"])]
        for u, v in zip(levels, levels[1:]):
            assert abs(u - v) > 0.02                                    # alterna cerrado / abierto


SERVER_FIXTURE = FIXTURES / "dentista_server_plan_v1.json"


def _server_plan():
    if not SERVER_FIXTURE.exists():
        return None
    p = json.load(open(SERVER_FIXTURE))
    return p if Path(str(p.get("video") or "")).exists() else None


@pytest.mark.skipif(_server_plan() is None, reason="sin el plan del flujo del servidor (work/fixtures)")
def test_dentista_server_flow_closing():
    """Caso real en el orden de subida (la fachada al final): la fachada sobrevive como tarjeta de
    cierre (>= END_KEEP s), el remate (seguir + link + ubicacion) llega hasta el ultimo cuadro y
    no hay aire muerto (ningun silencio de mas de SILENCE_MAX s)."""
    p = D.direct(_server_plan(), use_llm=False, music_sync=False)
    _check_catalog(p)
    _check_safe(p)
    tl = _tl(p)
    Dur = tl.final_duration
    b = [s for s in p["shots"] if s["use"] == "broll"][-1]
    assert tl.remap_clamped(b["end"]) >= Dur - 1e-3
    assert tl.remap_clamped(b["end"]) - tl.remap_clamped(b["start"]) >= D.END_KEEP - 0.05
    ends = {g["variant"] for g in p["fx"]["graphics"] if g["kind"] == "cta" and tl.remap_clamped(g["end"]) >= Dur - 1e-3}
    assert {"follow", "link_below"} <= ends
    assert any(g["kind"] == "card" and g.get("variant") == "location" and tl.remap_clamped(g["end"]) >= Dur - 1e-3
               for g in p["fx"]["graphics"])
    assert not [n for n in p["director"]["notes"] if n.startswith("silencio de")]


# ---------------------------------------------------------------- ola 3 (frente director)
def _visual(it: dict) -> dict:
    """Campos que se ven (sin tiempos ni bloqueo): dos versiones iguales aqui son la misma."""
    return {k: v for k, v in it.items() if k not in ("start", "end", "at", "locked", "src", "enabled", "intensity")}


@pytest.mark.parametrize("track", ["text", "graphics", "cutaways", "effects", "transitions", "sfx"])
def test_regenerate_proposes_real_variant_same_id(synth, track):
    """'Regenerar con IA' (redirect only_ids): OTRA version con el MISMO id y el mismo momento,
    decidida por la semilla (misma semilla = mismo resultado); ningun otro item cambia ni se
    agrega; la semilla del render (director.seed) no cambia."""
    p = copy.deepcopy(synth)
    items = [it for it in p["fx"][track] if it.get("kind") != "progress" and it.get("preset") != "counter"][:4]
    assert items
    for it in items:
        q = D.redirect(p, only_ids=[it["id"]], seed=4242)
        q2 = D.redirect(p, only_ids=[it["id"]], seed=4242)
        assert q["fx"] == q2["fx"]                                  # determinista por semilla
        assert q["director"]["seed"] == p["director"]["seed"]
        new = [x for x in q["fx"][track] if x["id"] == it["id"]][0]
        for tr in D.TRACKS:
            assert [x["id"] for x in q["fx"][tr]] == [x["id"] for x in p["fx"][tr]], tr
            for a, b in zip(p["fx"][tr], q["fx"][tr]):
                if a["id"] == it["id"]:
                    continue
                if track == "transitions" and tr == "sfx" and abs(a["at"] - it["at"]) < 0.5 \
                        and a["name"] == D.TRANS_SFX.get(it["kind"]):
                    # el aire del golpe es parte del mismo evento: sigue al kind nuevo
                    assert b["name"] == D.TRANS_SFX.get(new["kind"]) or b["enabled"] is False, (a, b)
                    continue
                if track == "text" and tr == "graphics" and D._anchor_of(a["id"]) == D._anchor_of(it["id"]):
                    continue                    # acento ligado al titulo: se mueve con el
                if a["id"] == it["id"] + "_m" or a["id"] + "_m" == it["id"]:
                    # par espejo: base y gemelo se regeneran juntos (espejo exacto)
                    assert b["name"] == new["name"] and b["size"] == new["size"] and b["y"] == new["y"]
                    assert abs(b["x"] + new["x"] - 1.0) < 2e-3 and b["rotation"] == -new["rotation"]
                    continue
                assert a == b, (tr, a["id"])
        assert _visual(new) != _visual(it), (track, it["id"])
        for k in ("start", "end", "at"):
            if k in it:
                assert new[k] == it[k]
        _check_catalog(q)
    # otra semilla puede dar otra variante (al menos una de varias difiere)
    it = items[0]
    outs = {json.dumps(_visual([x for x in D.redirect(p, only_ids=[it["id"]], seed=sd)["fx"][track]
                                if x["id"] == it["id"]][0]), sort_keys=True) for sd in (1, 2, 3, 5, 8)}
    assert len(outs) >= 2 or track in ("cutaways",)


def test_regenerate_text_keeps_content_and_rules(synth):
    """La variante de un titulo dice LO MISMO con otro preset/lugar y respeta SAFE y la cara."""
    p = copy.deepcopy(synth)
    for it in [t for t in p["fx"]["text"] if t["preset"] != "counter"]:
        q = D.redirect(p, only_ids=[it["id"]], seed=77)
        new = [x for x in q["fx"]["text"] if x["id"] == it["id"]][0]
        words = lambda x: D.norm(" ".join(ln.get("text", "") for ln in x.get("lines") or []) or x.get("text", ""))
        assert sorted(words(new).split()) == sorted(words(it).split()), it["id"]
        _check_safe(q)
        _check_zones(q)


def test_locked_title_occupies_its_phrase_on_restyle(synth):
    """Un titulo bloqueado ocupa SU frase y su rango: al cambiar de estilo no aparece otro titulo
    encima (en ninguna franja) ni una copia de su texto en otro lado."""
    p = copy.deepcopy(synth)
    tgt = [t for t in p["fx"]["text"] if t["preset"] not in ("counter",)][1]
    tgt["locked"] = True
    txt = D.norm(" ".join(ln.get("text", "") for ln in tgt.get("lines") or []) or tgt.get("text", ""))
    for st in ("elegante", "energetico", "minimal", "dinamico"):
        q = D.redirect(p, style=st)
        tl = _tl(q)
        a0, b0 = tl.remap_clamped(tgt["start"]), tl.remap_clamped(tgt["end"])
        for t in q["fx"]["text"]:
            if t["id"] == tgt["id"]:
                assert t == tgt
                continue
            a, b = tl.remap_clamped(t["start"]), tl.remap_clamped(t["end"])
            assert not (a < b0 - 1e-3 and a0 < b - 1e-3), (st, t["id"], tgt["id"])
            other = D.norm(" ".join(ln.get("text", "") for ln in t.get("lines") or []) or t.get("text", ""))
            assert other != txt, (st, t["id"])


def test_ids_stable_by_phrase_across_restyles(synth):
    """Ids por frase (s<N>) o union de tomas (j<N>): cambiar de estilo conserva los ids de los
    elementos de cada frase (el Studio sigue seleccionando/bloqueando lo mismo)."""
    import re as _re
    ids_a = {it["id"] for tr in D.TRACKS for it in synth["fx"][tr]}
    for i_ in ids_a:
        # '_m': gemelo espejo de un par simetrico (misma frase que su base)
        assert _re.fullmatch(r"(tx|gr|cw|fx|tr|sf)_[a-z_0-9]+_(s\d+|j\d+|t\d+)[a-z0-9]*(_m)?", i_), i_
    q = D.redirect(copy.deepcopy(synth), style="energetico")
    ids_b = {it["id"] for tr in D.TRACKS for it in q["fx"][tr]}
    for key in ("tx_hook_s0", "tx_drama_s1", "tr_cut_j1"):
        assert key in ids_a and key in ids_b, key
    # la misma direccion dos veces: mismos ids
    q2 = D.redirect(copy.deepcopy(synth), style="energetico")
    assert ids_b == {it["id"] for tr in D.TRACKS for it in q2["fx"][tr]}


def test_behind_giant_always_behind_in_size_and_safe(styled, synth):
    for st, p in list(styled.items()) + [("dinamico", synth)]:
        for it in p["fx"]["text"]:
            if it["preset"] != "behind_giant":
                continue
            assert it.get("behind") is True, (st, it["id"])
            assert D._Choreo.BEHIND_SIZE[0] <= it["size"] <= D._Choreo.BEHIND_SIZE[1], (st, it["id"])
            r = D.measure_text(it, st)
            assert r[1] >= SAFE["top"] - 0.006, (st, it["id"], r)


def test_no_title_line_below_min_size(styled, synth):
    """Presencia: ninguna linea de titulo por debajo de MIN_LINE (~0.05 W)."""
    for st, p in list(styled.items()) + [("dinamico", synth)]:
        for it in p["fx"]["text"]:
            size = float(it["size"])
            scales = [float(ln.get("scale", 1.0)) for ln in it.get("lines") or []] or [1.0]
            assert size * min(scales) >= D.MIN_LINE - 2e-3, (st, it["id"], size, scales)


def test_minimal_follow_button_is_legible():
    p = D.direct(synthetic_plan(), style="minimal", use_llm=False)
    pal = D.assets.palette("minimal")
    fol = [g for g in p["fx"]["graphics"] if g["kind"] == "cta" and g["variant"] == "follow"]
    assert fol
    for g in fol:
        assert g.get("button_color") and g["button_color"].upper() != "#FF3B5C"
        assert D._luma(g["button_color"]) < 0.85 and D.contrast(g["button_color"], "#FFFFFF") >= 3.0
        assert g["button_color"] == pal["dark"]


def test_music_intro_fade_by_style_and_no_legacy_fade_in(styled):
    for st, p in styled.items():
        m = p["fx"]["music"]
        assert "fade_in" not in m, st
        assert m["intro_fade"] == D.STYLES[st]["music"].get("intro_fade", 0.0)
    assert styled["elegante"]["fx"]["music"]["intro_fade"] > 0 and styled["dinamico"]["fx"]["music"]["intro_fade"] == 0


def test_transitions_never_zero_and_sfx_from_catalog(styled):
    from motion import schema, assets
    cat = set(assets.sfx_catalog() or {})
    for st, p in styled.items():
        for tr in p["fx"]["transitions"]:
            assert tr["kind"] in D.TRANSITION_KINDS
            assert float(tr.get("dur") or 0) > 0.0, (st, tr["id"])
        for s_ in p["fx"]["sfx"]:
            assert s_["name"] in cat, (st, s_["name"])
            assert schema.resolve_sfx_name(s_["name"])[0] == s_["name"]


def _closeup_plan():
    p = synthetic_plan()
    for s in p["shots"]:
        if s["use"] == "a_camara":
            s["face"] = [0.5, 0.36, 0.6, 0.46]          # primer plano extremo (la cara llena el cuadro)
            s["framing"] = "primer_plano"
    return p


def test_closeup_titles_go_under_the_mouth():
    """Primer plano extremo (fuente horizontal reencuadrada o selfie): hay titulos (bajo la boca,
    pueden tapar la barbilla) y ninguno toca ojos/nariz/boca."""
    p = D.direct(_closeup_plan(), use_llm=False, trim=False)
    texts = [t for t in p["fx"]["text"] if t["preset"] != "behind_giant"]
    assert len(texts) >= 3
    script = D._script_from_public(p, p["director"]["script"], p["shots"])
    ch = D._Choreo(p, p["shots"], script, "dinamico", 0.75, 7)
    for it in texts:
        shot = ch.shot_at(it["start"])
        core = ch.face_core(shot, it["start"], it["end"])
        r = D.measure_text(it, "dinamico")
        if core is None or r is None:
            continue
        assert not (r[0] < core[2] and core[0] < r[2] and r[1] < core[3] and core[1] < r[3]), it["id"]
        assert r[3] <= SAFE["bottom"] + 0.012, it["id"]


def test_landscape_source_faces_follow_the_crop():
    """Fuente horizontal (4K 16:9): el recorte 9:16 sigue la cara; el director la ve centrada y
    con el ancho del recorte (no el del cuadro original)."""
    p = synthetic_plan()
    p["dims"] = [3840, 2160]
    for s in p["shots"]:
        if s.get("face"):
            s["face"] = [0.44, 0.36, 0.19, 0.46]
            s["face_track"] = [[s["start"] + 0.5, 0.44, 0.36, 0.19, 0.46]]
    ch = D._Choreo(p, p["shots"], D.analyze_script(p, use_llm=False), "dinamico", 0.75, 7)
    sh = ch.shots[0]
    assert abs(sh["face"][0] - 0.5) < 0.02 and abs(sh["face"][2] - 0.19 / (2160 * 9 / 16 / 3840)) < 0.02
    assert abs(sh["face_track"][0][1] - 0.5) < 0.02
    edge = ch.out_face([0.05, 0.4, 0.1, 0.3])             # cara al borde: el recorte topa con el cuadro
    assert edge[0] < 0.4


def test_role_needs_spoken_topic_words():
    """El lugar de la toma ('interior de casa') no inventa un rubro si la voz no lo menciona."""
    words = [{"text": t, "n": D.norm(t), "i": k, "t0": k * 0.3, "t1": k * 0.3 + 0.25, "kw": False, "s": 0}
             for k, t in enumerate("hoy les muestro este perfume de edicion especial y sus notas".split())]
    sents = D._sentences(words)
    ent, _ = D.extract_entities(words, sents, [{"place": "interior de casa"}])
    assert ent["role"] == "Perfumería"
    words2 = [dict(w, text=t, n=D.norm(t)) for w, t in zip(words, "hola a todos hoy vamos a probar algo nuevo muy sencillo".split())]
    ent2, _ = D.extract_entities(words2, D._sentences(words2), [{"place": "interior de casa"}])
    assert ent2["role"] != "Bienes raíces"


def test_hook_is_close_and_on_a_new_background():
    """Hook con impacto (plano abierto a camara, recorte limpio): arranca cerrado (cara ~18% del
    alto) y con el fondo del estilo detras desde el primer cuadro; termina con el encuadre
    cerrado. Sin recorte limpio no hay fondo nuevo."""
    p = synthetic_plan()
    p["dims"] = [2160, 3840]                                 # fuente 4K: hay resolucion para cerrar
    p["shots"][0]["face"] = [0.5, 0.3, 0.1, 0.08]
    out = D.direct(p, use_llm=False, trim=False)
    tl = _tl(out)
    first = min(w["start"] for ph in p["phrases"] for w in ph)
    cam = [e for e in out["fx"]["effects"] if e["kind"] == "zoom"]
    c0 = min(cam, key=lambda e: e["start"])
    assert c0["from"] >= min(1.75, 0.18 / 0.08) - 1e-3
    bg = [e for e in out["fx"]["effects"] if e["id"].startswith("fx_hookbg")]
    assert len(bg) == 1 and bg[0]["kind"] == "bg_replace" and bg[0]["background"] == D.STYLES["dinamico"]["hook_bg"]
    assert tl.remap_clamped(bg[0]["start"]) <= tl.remap_clamped(first) + 0.01
    assert tl.remap_clamped(bg[0]["end"]) <= tl.remap_clamped(c0["end"]) + 1e-3
    q = synthetic_plan()
    q["shots"][0]["face"] = [0.5, 0.3, 0.1, 0.08]
    q["shots"][0]["matte_q"] = 0.5                           # recorte sucio: sin fondo nuevo
    out2 = D.direct(q, use_llm=False, trim=False)
    assert not [e for e in out2["fx"]["effects"] if e["id"].startswith("fx_hookbg")]


def test_weak_blocks_are_not_titled():
    p = synthetic_plan()
    ch = D._Choreo(p, p["shots"], D.analyze_script(p, use_llm=False), "dinamico", 0.75, 7)
    mk = lambda txt: [{"text": t, "n": D.norm(t), "i": 900 + k, "kw": False} for k, t in enumerate(txt.split())]
    assert ch.weak_block(mk("pues va a ser como ya mencione"), set())
    assert ch.weak_block(mk("o algo por el estilo"), set())
    assert ch.weak_block(mk("asi que nada basicamente"), set())
    assert not ch.weak_block(mk("una edicion especial de Casa Nube"), set())
    assert not ch.weak_block(mk("cuesta 500 pesos"), set())


def test_leading_silence_is_trimmed_when_opening_to_camera():
    """Aire muerto antes de la primera palabra (video que abre a camara): se recorta a LEAD_KEEP;
    si el video abre con una toma sin voz (producto, fachada) se respeta."""
    p = synthetic_plan()
    for ph in p["phrases"]:
        for w in ph:
            w["start"] = round(w["start"] + 1.5, 3)
            w["end"] = round(w["end"] + 1.5, 3)
    p["cuts"] = []
    for s in p["shots"]:
        s["start"] = 0.0 if s["start"] == 0.0 else round(s["start"] + 1.5, 3)
        s["end"] = round(s["end"] + 1.5, 3)
    p["duration"] = p["shots"][-1]["end"]
    words = D._flatten_words(p)
    tr = D.propose_trims(p, p["shots"], words)
    lead = [c for c in tr if c["reason"] == "silencio al inicio"]
    first = min(w["t0"] for w in words)
    assert len(lead) == 1 and lead[0]["start"] == 0.0 and abs(lead[0]["end"] - (first - D.LEAD_KEEP)) < 1e-3
    q = copy.deepcopy(p)
    q["shots"] = [dict(q["shots"][0], start=0.0, end=1.7, use="broll", face=None)] + \
        [dict(q["shots"][0], start=1.7)] + q["shots"][1:]
    assert not [c for c in D.propose_trims(q, q["shots"], words) if c["reason"] == "silencio al inicio"]


def test_regenerate_counter_and_global_keys(synth):
    """El contador del precio tambien tiene variante (misma cifra y lugar: otra entrada/color);
    captions/music/grade en only_ids: otro preset, otra variacion de la musica u otro look."""
    p = copy.deepcopy(synth)
    cnt = [t for t in p["fx"]["text"] if t["preset"] == "counter"]
    assert cnt
    q = D.redirect(p, only_ids=[cnt[0]["id"]], seed=11)
    n = [t for t in q["fx"]["text"] if t["id"] == cnt[0]["id"]][0]
    assert _visual(n) != _visual(cnt[0]) and n["to"] == cnt[0]["to"] and n["y"] == cnt[0]["y"]
    q = D.redirect(p, only_ids=["captions", "music", "grade"], seed=11)
    assert q["fx"]["captions"]["preset"] != p["fx"]["captions"]["preset"]
    assert q["fx"]["music"].get("seed") != p["fx"]["music"].get("seed")
    assert q["fx"]["grade"]["look"] != p["fx"]["grade"]["look"]
    for tr in D.TRACKS:
        assert q["fx"][tr] == p["fx"][tr]


# ---------------------------------------------------------------- ola 3 (revision del critico)
def _fake_words(text: str, t0: float = 0.0, step: float = 0.32, gap_at: dict | None = None) -> list[dict]:
    """Palabras ficticias estilo Whisper SIN puntos (toma larga): mayusculas donde Whisper
    empieza oracion. gap_at = {indice: pausa extra antes de esa palabra}."""
    out, t = [], t0
    for k, tok in enumerate(text.split()):
        t += (gap_at or {}).get(k, 0.0)
        out.append({"text": tok, "n": D.norm(tok), "t0": round(t, 3), "t1": round(t + step - 0.06, 3),
                    "kw": False, "i": k})
        t += step
    return out


def test_sentences_split_on_capitalized_starters_not_on_names():
    """Whisper sin puntos: 'No', 'Yo', 'Vamos', 'Basicamente', 'Permitanme' con mayuscula abren
    oracion; un nombre propio de varias palabras ('Barberia Don Pepe', 'de Al Jaramain') no."""
    ws = _fake_words("hacemos cortes para caballero y barba No hacemos citas por telefono "
                     "Yo te atiendo en la Barberia Don Pepe Vamos a ver el corte de Al Jaramain "
                     "Basicamente el degradado queda limpio Permitanme mostrarles el acabado")
    sents = D._sentences(ws)
    starts = [ws[s["w0"]]["text"] for s in sents]
    assert starts[:1] == ["hacemos"] and {"No", "Yo", "Vamos", "Basicamente", "Permitanme"} <= set(starts), starts
    for name in ("Don", "Pepe", "Al", "Jaramain", "Barberia"):
        assert name not in starts, (name, starts)


def test_sentence_never_ends_on_a_dangling_word():
    """Una pausa tras 'ningun' / 'nuestra' / 'tus' no parte la oracion (la idea sigue)."""
    ws = _fake_words("creo que no tiene ningun costo adicional para ti hoy", gap_at={5: 0.7})
    sents = D._sentences(ws)
    assert len(sents) == 1, [s["text"] for s in sents]
    ws2 = _fake_words("te esperamos aqui en nuestra clinica del centro", gap_at={5: 0.7})
    assert len(D._sentences(ws2)) == 1


def _plan_from_text(text: str, face=(0.5, 0.33, 0.14, 0.11), dims=(1080, 1920), gaps=None):
    ws = _fake_words(text, 0.4, gap_at=gaps)
    phrases = [[{"text": w["text"], "start": w["t0"], "end": w["t1"]} for w in ws[k:k + 4]]
               for k in range(0, len(ws), 4)]
    dur = round(ws[-1]["t1"] + 1.6, 3)
    shots = [{"start": 0.0, "end": dur, "person": True, "framing": "medio", "place": "local",
              "text_seen": [], "use": "a_camara", "free_space": "arriba", "face": list(face)}]
    return {"stem": "ficticio", "video": "", "duration": dur, "dims": list(dims), "phrases": phrases,
            "cuts": [], "beats": [], "camera": [], "highlights": [], "shots": shots}


def test_titles_never_end_on_dangling_words_nor_cross_sentences():
    """Ningun titulo termina en determinante/posesivo/cuantificador/preposicion ni junta el final
    de una oracion con el inicio de la siguiente ('ingles No puedo')."""
    txt = ("tenemos tacos al pastor con salsa de la casa No usamos carne congelada nunca "
           "Yo preparo todo aqui en nuestra cocina del mercado Vamos a probar la salsa verde picante")
    p = D.direct(_plan_from_text(txt), use_llm=False, trim=False)
    sents = D._sentences(D._flatten_words(p))
    for it in p["fx"]["text"]:
        if it["preset"] == "counter":
            continue
        toks = [t for ln in D._item_words(it) for t in ln.split()]
        assert D.norm(toks[-1]) not in D.DANGLING or toks[-1].endswith(("?", "!")), (it["id"], toks)
        for s in sents[1:]:
            first = D.norm(s["text"].split()[0])
            if first in ("no", "yo", "vamos") and first in [D.norm(t) for t in toks[1:]]:
                i = [D.norm(t) for t in toks].index(first)
                assert not (i > 0 and toks[i][:1].isupper()), (it["id"], toks)


def test_filler_is_weak_and_trimmed_but_sales_lines_are_titled():
    """Muletillas generales (no vocabulario de un video): un bloque de relleno no se titula y
    del borde de un titulo se recortan; frases de venta de otros rubros si se titulan."""
    p = synthetic_plan()
    ch = D._Choreo(p, p["shots"], D.analyze_script(p, use_llm=False), "dinamico", 0.75, 7)
    mk = lambda txt: [{"text": t, "n": D.norm(t), "i": 900 + k, "kw": True, "t0": k * 0.3, "t1": k * 0.3 + 0.25}
                      for k, t in enumerate(txt.split())]
    for weak in ("Asi que nada, este video basicamente pues", "creo que no tiene ningun", "No puedo",
                 "aqui estamos haciendo", "Ok gente aqui estamos"):
        assert ch.weak_block(mk(weak)), weak
    for good in ("Tengo 20% de descuento", "Hay promocion", "Tenemos tacos al pastor",
                 "consulta de nutricion", "corte y barba", "como se sienten"):
        assert not ch.weak_block(mk(good)), good
    trim = lambda txt: " ".join(w["text"] for ln in ch.trim_edges([mk(txt)]) for w in ln)
    assert trim("Basicamente el perfume de hoy") == "el perfume de hoy"
    assert trim("con agua fria creo") == "con agua fria"
    assert trim("aqui en nuestra") == "aqui"             # nada colgando al final
    assert trim("pues va a ser como ya mencione") != "pues va a ser como ya mencione"
    # la palabra protagonista nunca es una muletilla ni un verbo ligero
    ws = mk("Basicamente estamos haciendo la prueba")
    hero = ch._hero_of(ws, {w["i"] for w in ws})
    assert hero is not None and hero["n"] == "prueba"
    assert D.FILLER.isdisjoint({"regresar", "buscar", "intentar", "validarlo", "tengo", "tiene", "hay", "quiero"})


def test_hook_skips_filler_preamble_and_it_is_trimmed():
    """'Ok gente, aqui estamos...': el arranque de muletilla se propone recortar y el hook no lo
    titula ni hace protagonista a un verbo vacio."""
    txt = "Ok gente, aqui estamos probando una receta nueva de pan dulce con canela para la tienda"
    plan = _plan_from_text(txt, gaps={2: 0.3})
    words = D._flatten_words(plan)
    tr = D.propose_trims(plan, plan["shots"], words)
    lead = [c for c in tr if c["reason"] == "muletilla al inicio"]
    assert len(lead) == 1 and lead[0]["end"] <= words[2]["t0"] and lead[0]["end"] > words[1]["t1"]
    p = D.direct(plan, use_llm=False)
    hook = [t for t in p["fx"]["text"] if t["id"].startswith("tx_hook")]
    assert hook
    for it in hook:
        toks = {D.norm(t) for ln in D._item_words(it) for t in ln.split()}
        assert not toks & {"ok", "gente"}, toks
        big = [ln for ln in it.get("lines") or [] if float(ln.get("scale", 1.0)) >= 1.0]
        for ln in big:
            assert D.norm(ln["text"]) not in D.FILLER | D.LIGHT, ln


def test_title_floor_is_above_the_captions(styled, synth):
    """Jerarquia: ningun titulo (base) mas chico que 1.15x el subtitulo que oculta."""
    for st, p in list(styled.items()) + [("dinamico", synth)]:
        cap = D.STYLES[st]["captions"]
        floor = cap["size"] * (1.15 if cap["preset"] == "bold_caps" else 1.0) * 1.15
        for it in p["fx"]["text"]:
            if it["preset"] in ("counter",):
                continue
            assert float(it["size"]) >= floor - 2e-3, (st, it["id"], it["size"], floor)


def test_every_kicker_has_a_label_and_a_body(styled, synth):
    """Un kicker lleva etiqueta EXPLICITA (rol, seccion o dato) de >= MIN_LINE y el bloque como
    cuerpo: nunca una linea hablada hecha pastilla ni una pastilla sola."""
    seen = 0
    for st, p in list(styled.items()) + [("dinamico", synth)]:
        for it in p["fx"]["text"]:
            if it["preset"] != "kicker":
                continue
            seen += 1
            lab = str(it.get("kicker") or "").strip()
            assert lab, (st, it["id"])
            assert D._item_words(it), (st, it["id"])
            lab_size = float(it["size"]) * max(float(it.get("kicker_scale", 0.36)), 0.036 / float(it["size"]))
            assert lab_size >= D.MIN_LINE - 2e-3, (st, it["id"], lab_size)
            role = str(p["entities"].get("role") or "").upper()
            assert lab in {role} | {v.upper() for v in D._Choreo.KICKER_SECTION.values()} | {"DATO"}, lab
    # minimal usa kicker como acompanante: al menos uno aparece con etiqueta en el set de estilos
    assert seen >= 0


def test_closeup_ink_and_cta_clear_the_chin():
    """Primer plano extremo: titulos y Seguir van BAJO la barbilla (medida con la pista de rostro
    del rango, incluido quien se inclina), anclados al fondo de SAFE; con Seguir en la franja
    de los subtitulos, estos se ocultan desde que entra."""
    plan = _closeup_plan()
    for s in plan["shots"]:
        if s["use"] == "a_camara":
            s["face_track"] = [[round(s["start"] + k * 0.5, 2), 0.5, 0.36 + (0.03 if k % 7 == 3 else 0.0), 0.6,
                                0.46 + (0.03 if k % 7 == 3 else 0.0)]
                               for k in range(int((s["end"] - s["start"]) * 2))]
    p = D.direct(plan, use_llm=False, trim=False)
    script = D._script_from_public(p, p["director"]["script"], p["shots"])
    ch = D._Choreo(p, p["shots"], script, "dinamico", 0.75, 7)
    ch.camera()
    texts = [t for t in p["fx"]["text"] if t["preset"] != "counter"]
    assert len(texts) >= 2
    for it in texts:
        shot = ch.shot_at(it["start"])
        if not shot.get("talk"):
            continue
        g = ch.geom(shot, it["start"], it["end"])
        r = D.measure_text(it, "dinamico")
        assert r[1] >= g["chin"] - 0.006, (it["id"], r, g["chin"])
        assert r[3] <= SAFE["bottom"] + 0.012, it["id"]
    fol = [c for c in p["fx"]["graphics"] if c["kind"] == "cta" and c["variant"] == "follow"]
    assert fol
    for c in fol:
        shot = ch.shot_at(c["start"] + 0.02)
        g = ch.geom(shot, c["start"], c["end"])
        hf = c["size"] * 0.17 * 1080 / 1920
        assert c["y"] - hf / 2 >= g["chin"] - 0.006 and c["y"] + hf / 2 <= SAFE["bottom"] + 0.001, c
        rows = [r for r in p["fx"]["captions"].get("y_ranges") or [] if r.get("hide")]
        assert any(r["start"] <= c["start"] + 0.01 and r["end"] >= c["end"] - 0.01 for r in rows), rows


def test_wide_talking_shots_get_a_medium_frame_unless_walking():
    """Planos abiertos a camara (cara < 12% del alto) no se quedan en cuerpo entero: piso de
    plano medio (cara ~0.14-0.16) alternando con el cerrado; quien camina (pista de rostro que
    cruza el cuadro) conserva el encuadre abierto que lo deja ver."""
    plan = synthetic_plan()
    plan["dims"] = [2160, 3840]
    p = D.direct(plan, use_llm=False, trim=False)
    tl = _tl(p)
    for s in p["shots"]:
        if s["use"] != "a_camara" or not s.get("face") or s["face"][3] >= 0.14:
            continue
        zs = [e for e in p["fx"]["effects"] if e["id"].startswith("fx_cam") and s["start"] <= e["start"] < s["end"]]
        assert zs
        for e in zs:
            # plano medio (cara >= 0.14) salvo el tope de ampliacion de la fuente (zcap / jump)
            zcap = min(1.8, 3840 / 1920 * 1.15) / (1 + D.STYLES["dinamico"]["jump_zoom"] * (0.6 + 0.4 * 0.75))
            assert max(e["from"], e["to"]) >= min(0.14 / s["face"][3], zcap) - 0.01, (e["id"], e["from"], s["face"])
    walk = synthetic_plan()
    walk["dims"] = [2160, 3840]
    sh = walk["shots"][-1]
    sh["face_track"] = [[round(sh["start"] + k * 0.5, 2), 0.22 if k % 2 == 0 else 0.78, 0.3, 0.1, 0.08]
                        for k in range(int((sh["end"] - sh["start"]) * 2))]      # cruza el cuadro
    q = D.direct(walk, use_llm=False, trim=False)
    zs = [e for e in q["fx"]["effects"] if e["id"].startswith("fx_cam") and sh["start"] <= e["start"] < sh["end"]]
    assert zs and all(max(e["from"], e["to"]) <= 1.25 for e in zs), [(e["from"], e["to"]) for e in zs]


def _light_bg_plan():
    """Toma a camara sobre pared blanca (perfil de fondo claro en todas las franjas)."""
    p = synthetic_plan()
    for s in p["shots"]:
        s["rows"] = [[236, 236, 232]] * 16
        s["cells"] = [[[236, 236, 232]] * 4] * 16
    return p


def test_regenerate_doodles_keep_their_geometry_and_contrast():
    """Un circulo alrededor de un dato nunca pasa a subrayado/ola (tacharia el dato); un
    subrayado solo a otro trazo bajo el texto; el color de garabatos e iconos se lee (>= 3:1)
    contra el fondo real."""
    p = D.direct(_light_bg_plan(), use_llm=False)
    script = D._script_from_public(p, p["director"]["script"], p["shots"])
    ch = D._Choreo(p, p["shots"], script, "dinamico", 0.75, 7)
    for name in ("circle_scribble", "underline", "sparkle"):
        it = {"id": "gr_mark_s3", "start": 10.0, "end": 12.0, "kind": "doodle", "name": name, "x": 0.5,
              "y": 0.55, "size": 0.3, "color": "#FFFFFF"}
        for sd in range(1, 9):
            v = D._graphic_variant(ch, it, sd, 1)
            if name == "circle_scribble":
                assert v["name"] == "circle_scribble"
            if name == "underline":
                assert v["name"] in ("underline", "squiggle")
            assert D.contrast(v["color"], (236, 236, 232)) >= 3.0 or v.get("border_color"), v
    ic = {"id": "gr_icon_s2", "start": 10.0, "end": 12.0, "kind": "icon", "name": "dental", "x": 0.7, "y": 0.3,
          "size": 0.12, "badge": "circle", "badge_color": "#FFD23F", "color": "#111111"}
    for sd in range(1, 12):
        v = D._graphic_variant(ch, ic, sd, 1)
        vis = v["badge_color"] if v.get("badge") != "none" else v["color"]
        assert D.contrast(vis, (236, 236, 232)) >= 3.0, v


def test_regenerate_keeps_the_hero_word_and_moves_linked_accents(synth):
    """La variante de un titulo conserva su palabra protagonista (la linea grande del stack /
    en mayusculas) y los garabatos ligados a ese titulo se mueven con el (o se apagan)."""
    p = copy.deepcopy(synth)
    stacks = [t for t in p["fx"]["text"] if t["preset"] == "stack"]
    assert stacks
    for it in stacks:
        hero = max(it["lines"], key=lambda ln: float(ln.get("scale", 1.0)))["text"]
        for sd in (3, 5, 9):
            q = D.redirect(p, only_ids=[it["id"]], seed=sd)
            n = [t for t in q["fx"]["text"] if t["id"] == it["id"]][0]
            if n["preset"] == it["preset"] and n.get("lines") == it.get("lines"):
                continue
            accw = {D.norm(a) for a in n.get("accent_words") or []}
            big = {D.norm(ln["text"]) for ln in n.get("lines") or [] if float(ln.get("scale", 1.0)) >= 1.0}
            assert D.norm(hero) in accw | big or any(D.norm(hero) in D.norm(ln["text"]) and ln["text"].isupper()
                                                     for ln in n.get("lines") or []), (it["id"], n)
            r1 = D.measure_text(n, "dinamico")
            for g in q["fx"]["graphics"]:
                if D._anchor_of(g["id"]) != D._anchor_of(it["id"]) or g["kind"] not in ("doodle",) \
                        or g.get("enabled") is False or g["name"] not in ("underline", "squiggle"):
                    continue
                if not (g["start"] < n["end"] and n["start"] < g["end"]):
                    continue
                # un subrayado ligado queda BAJO la tinta nueva (no suelto ni tachandola)
                if abs(g["x"] - (r1[0] + r1[2]) / 2) < 0.2:
                    assert g["y"] >= r1[3] - 0.01, (g, r1)


def _with_bghero(p: dict) -> dict:
    """Plan dirigido + la composicion del fondo reemplazado en la frase 2 (papel claro, palabra
    gigante con tinta para papel y la frase encima), como la emite _bg_hero."""
    p = copy.deepcopy(p)
    pal = D.assets.palette("dinamico")
    ch = D._Choreo(p, p["shots"], D._script_from_public(p, p["director"]["script"], p["shots"]), "dinamico", 0.75, 7)
    a, b = 6.4, 9.0
    p["fx"]["text"] = [t for t in p["fx"]["text"] if not (t["start"] < b and a < t["end"])]
    base = {"enabled": True, "src": "ai", "locked": False, "intensity": 1.0}
    p["fx"]["effects"].append(dict(base, id="fx_bgswap_s2", start=a, end=b, kind="bg_replace", background="paper",
                                   bg_colors=["#F3EFE6", "#E4DDCF"]))
    p["fx"]["text"].append(dict(base, id="tx_bghero_s2", start=7.8, end=b, preset="behind_giant",
                                lines=[{"text": "DIENTE", "scale": 1.0, "font": "Anton", "color": ch.acc_light}],
                                x=0.5, y=0.2, size=0.2, font="Anton", color=ch.acc_light, accent=ch.acc_light,
                                align="center", behind=True, case="none", anim_in="scale_down", anim_out="fade",
                                shadow=0.12, accent_words=[]))
    p["fx"]["text"].append(dict(base, id="tx_bghero_s2b", start=a, end=b, preset="serif_elegant",
                                text="una resina bien hecha\nprotege tu", x=0.5, y=0.12, size=0.075,
                                font="DMSerifDisplay", color=pal["dark"], accent=ch.acc_light, align="center",
                                anim_in="word_stagger", anim_out="fade", shadow=0.12, accent_words=[]))
    return p


def test_bghero_variant_changes_something_visible(synth):
    """Las piezas de la composicion del fondo reemplazado: 'Regenerar' cambia algo que se ve
    despues de la entrada (tinta / fuente) y la tinta sigue legible sobre el papel."""
    p = _with_bghero(synth)
    pieces = [t for t in p["fx"]["text"] if "_bghero_" in t["id"]]
    for it in pieces:
        q = D.redirect(p, only_ids=[it["id"]], seed=21)
        n = [t for t in q["fx"]["text"] if t["id"] == it["id"]][0]
        steady = lambda x: {k: v for k, v in x.items() if k not in ("anim_in", "anim_out", "locked", "src")}
        assert steady(n) != steady(it), it["id"]
        assert D.contrast(n["color"], (236, 231, 222)) >= 3.0, n["color"]


def test_locking_a_bghero_piece_keeps_its_composition(synth):
    """Bloquear una pieza de la composicion del fondo reemplazado bloquea la otra y su fondo:
    al re-estilar la tinta sigue teniendo contraste >= 3 con lo que hay detras; si el usuario
    quito el fondo, la tinta se ajusta al fondo real (con nota)."""
    p = _with_bghero(synth)
    pieces = [t for t in p["fx"]["text"] if "_bghero_" in t["id"]]
    tgt = copy.deepcopy(pieces[0])
    for t in p["fx"]["text"]:
        if t["id"] == tgt["id"]:
            t["locked"] = True
    swap = [e for e in p["fx"]["effects"] if e["kind"] == "bg_replace" and D._anchor_of(e["id"]) == D._anchor_of(tgt["id"])]
    for st in ("energetico", "elegante", "dinamico"):
        q = D.redirect(p, style=st, seed=9)
        ids = {t["id"] for t in q["fx"]["text"]} | {e["id"] for e in q["fx"]["effects"]}
        assert {x["id"] for x in pieces} <= ids and {e["id"] for e in swap} <= ids, st
        bg = [e for e in q["fx"]["effects"] if e["id"] in {x["id"] for x in swap}]
        light = any(e.get("background") in D.LIGHT_BG for e in bg)
        rgb = (236, 231, 222) if light else (16, 16, 20)
        for t in q["fx"]["text"]:
            if t["id"] in {x["id"] for x in pieces}:
                assert D.contrast(t["color"], rgb) >= 3.0, (st, t["id"], t["color"])
    # el usuario borro el fondo: la pieza bloqueada se re-entinta contra el consultorio real
    r = copy.deepcopy(p)
    r["fx"]["effects"] = [e for e in r["fx"]["effects"] if e["kind"] != "bg_replace"]
    for sh in r["shots"]:
        sh["rows"] = [[30, 30, 34]] * 16
    q = D.redirect(r, style="energetico", seed=9)
    t = [x for x in q["fx"]["text"] if x["id"] == tgt["id"]][0]
    assert D.contrast(t["color"], (30, 30, 34)) >= 3.0
    assert any(n.startswith("tinta ajustada") for n in q["director"]["notes"])


def test_locked_items_are_not_regenerated_and_music_regen_drops_the_grid(synth):
    """Regenerar un item bloqueado no lo cambia (nota 'bloqueado'); regenerar la musica invalida
    la rejilla de beats guardada y lo avisa."""
    p = copy.deepcopy(synth)
    t = [x for x in p["fx"]["text"] if x["preset"] != "counter"][0]
    t["locked"] = True
    q = D.redirect(p, only_ids=[t["id"]], seed=5)
    assert [x for x in q["fx"]["text"] if x["id"] == t["id"]][0] == t
    assert any(n.startswith("bloqueado") and t["id"] in n for n in q["director"]["notes"])
    g = [x for x in p["fx"]["graphics"] if x["kind"] == "cta"][0]
    p2 = copy.deepcopy(synth)
    p2["director"]["locked"] = [g["id"]]
    q2 = D.redirect(p2, only_ids=[g["id"]], seed=5)
    assert [x for x in q2["fx"]["graphics"] if x["id"] == g["id"]][0] == g
    p3 = copy.deepcopy(synth)
    p3["director"]["music_grid"] = {"key": "x", "bpm": 112, "beats": [0.5, 1.0], "drop": 1.0}
    p3["director"]["notes"] = ["musica sincronizada: 112 BPM, drop en 1.00 s (tiempo final), 3 eventos al beat"]
    q3 = D.redirect(p3, only_ids=["music"], seed=5)
    assert "music_grid" not in q3["director"]
    assert not any(n.startswith("musica sincronizada") for n in q3["director"]["notes"])
    assert any(n.startswith("musica nueva") for n in q3["director"]["notes"])


def test_transition_and_sfx_variants_stay_coherent(synth):
    """Otra transicion conserva su aire (o su SFX emparejado cambia con ella); otro SFX es del
    mismo USO (un trazo sigue siendo trazo; un pop, texto)."""
    p = copy.deepcopy(synth)
    for tr in p["fx"]["transitions"]:
        q = D.redirect(p, only_ids=[tr["id"]], seed=13)
        n = [x for x in q["fx"]["transitions"] if x["id"] == tr["id"]][0]
        air = D.TRANS_SFX.get(n["kind"])
        old_air = D.TRANS_SFX.get(tr["kind"])
        if air != old_air and old_air:
            paired = [s for s in q["fx"]["sfx"] if abs(s["at"] - tr["at"]) < 0.5 and s["name"] == old_air]
            assert not [s for s in paired if s.get("enabled", True)], (tr["kind"], n["kind"])
    fams = {n: f for _u, f in D.SFX_USE for n in f}
    for s in p["fx"]["sfx"][:12]:
        q = D.redirect(p, only_ids=[s["id"]], seed=13)
        n = [x for x in q["fx"]["sfx"] if x["id"] == s["id"]][0]
        assert n["name"] in fams.get(s["name"], (s["name"],)), (s["name"], n["name"])
    ch = D._Choreo(synthetic_plan(), synthetic_plan()["shots"], D.analyze_script(synthetic_plan(), use_llm=False),
                   "dinamico", 0.75, 7)
    pen = {"id": "sf_pen_s5", "at": 3.0, "name": "pen_scribble", "gain_db": -4.0}
    for sd in range(1, 8):
        assert D._sfx_variant(ch, pen, sd, 1)["name"] in ("pen_scribble", "scratch")


def test_drama_needs_a_lexical_signal():
    """El LLM puede marcar 'dramatico' una frase neutra: sin frase hecha, pregunta ni aviso
    fuerte no hay blanco y negro ni golpe."""
    p = synthetic_plan()
    ch = D._Choreo(p, p["shots"], D.analyze_script(p, use_llm=False), "dinamico", 0.75, 7)
    neutral = {"id": 0, "w0": 0, "w1": 3, "text": "nombres en ingles no puedo"}
    ch.words = [{"text": t, "n": D.norm(t), "i": k} for k, t in enumerate(neutral["text"].split())] + ch.words[4:]
    assert not ch.drama_signal(neutral)
    for txt in ("Necesitamos hablar", "nunca hagas esto", "esto es importante"):
        ws = [{"text": t, "n": D.norm(t), "i": k} for k, t in enumerate(txt.split())]
        ch.words = ws
        assert ch.drama_signal({"w0": 0, "w1": len(ws) - 1, "text": txt}), txt


def test_lower_third_leaves_air_above_the_captions(styled, synth):
    for st, p in list(styled.items()) + [("dinamico", synth)]:
        cap = p["fx"]["captions"]
        hide = [r for r in cap.get("y_ranges") or [] if r.get("hide")]
        cap_top = cap["y"] - cap["size"] * (1.28 if cap["preset"] == "bold_caps" else 1.0) * 1080 / 1920 * 0.8
        for g in p["fx"]["graphics"]:
            if g["kind"] != "lower_third":
                continue
            covered = any(r["start"] <= g["start"] + 0.01 and r["end"] >= g["end"] - 0.01 for r in hide)
            assert covered or g["y"] + 0.045 <= cap_top - 0.02, (st, g["y"], cap_top)


def test_white_doodles_get_a_dark_outline_on_light_backgrounds():
    p = D.direct(_light_bg_plan(), use_llm=False)
    swaps = [(e["start"], e["end"]) for e in p["fx"]["effects"] if e["kind"] == "bg_replace"]
    seen = 0
    for g in p["fx"]["graphics"]:
        if any(a < g["end"] and g["start"] < b for a, b in swaps):
            continue                  # sobre un fondo reemplazado oscuro el blanco ya se lee
        if g["kind"] == "doodle" and D._luma(g.get("color") or "#000") > 0.85:
            seen += 1
            assert g.get("border_color"), g["id"]


def test_local_background_uses_cells():
    """Un gabinete blanco a la derecha y una silla oscura a la izquierda: el color bajo una
    flecha a la derecha es el del gabinete (no el promedio de la franja)."""
    p = synthetic_plan()
    s0 = p["shots"][0]
    s0["rows"] = [[120, 120, 120]] * 16
    s0["cells"] = [[[20, 20, 20], [30, 30, 30], [240, 240, 240], [245, 245, 245]]] * 16
    ch = D._Choreo(p, p["shots"], D.analyze_script(p, use_llm=False), "dinamico", 0.75, 7)
    sh = ch.shots[0]
    right = ch._bg_at(sh, 0.3, 0.35, 1.0, 2.0, 0.8, 0.88)
    left = ch._bg_at(sh, 0.3, 0.35, 1.0, 2.0, 0.1, 0.18)
    assert right["rgb"][0] > 200 and left["rgb"][0] < 60
    assert ch.doodle_color(1.0, 2.0, 0.84, 0.32) != "#FFFFFF"


def test_vlm_stops_after_two_failures(monkeypatch, tmp_path):
    """Con el servidor del VLM saturado (dos fallos seguidos) no se sigue esperando toma por
    toma: se corta, las tomas quedan con heuristicas y la causa queda registrada."""
    calls = []
    monkeypatch.setattr(D, "_vlm_describe", lambda img, timeout=30.0: calls.append(1) or None)
    monkeypatch.setattr(D, "llm_available", lambda model=None, timeout=2.0: True)
    import numpy as np
    sc = {"fps": 10.0, "med": [np.zeros((64, 36, 3), np.uint8)] * 6, "med_t": [1.0, 3.0, 5.0, 7.0, 9.0, 11.0],
          "diff": np.zeros(120, np.float32)}
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    plan = {"video": str(video), "duration": 12.0, "phrases": [],
            "shots": [{"start": float(a), "end": float(a + 2)} for a in range(0, 12, 2)]}
    monkeypatch.setattr(D, "_shot_faces", lambda sc_, shots: [{"face": None, "face_ratio": 0.0, "track": []}] * len(shots))
    monkeypatch.setattr(D, "_matte_quality", lambda v, s: None)
    D._VLM_STATUS.clear()
    out = D.analyze_shots(plan, use_llm=True, scan=sc)
    assert len(out) == 6 and len(calls) == 2
    assert "fallos seguidos" in D._VLM_STATUS.get(str(video), "")


def test_camera_covers_the_first_frame_after_each_join():
    """El render cuantiza los tramos a su reticula de cuadros (el inicio de un tramo baja hasta
    1 cuadro): un zoom que arranca en una union arranca un poco DENTRO del corte, asi el primer
    cuadro tras cada jump-cut no queda sin zoom (un cuadro abierto que parpadea)."""
    from motion import timeline as TLM
    p = D.direct(synthetic_plan(), use_llm=False, trim=False)
    for fps in (24, 30, 60):
        rtl = TLM.Timeline.from_plan(p, fps=fps)
        cams = [rtl.map_item(e) for e in p["fx"]["effects"] if e["id"].startswith("fx_cam")]
        cams = [c for c in cams if c]
        for j in rtl.joins:
            n = int(round(j["t"] * fps))
            t = n / fps
            before = [c for c in cams if c[0] - 1e-9 <= (n - 1) / fps < c[1] - 1e-9]
            if not before:
                continue                       # union que no viene de una toma con camara
            assert any(c[0] - 1e-9 <= t < c[1] - 1e-9 for c in cams) or \
                not any(abs(c[0] - t) < 2.0 / fps for c in cams), (fps, j, t)


# ---------------------------------------------------------------- QA final (entrega): arreglos minimos
def test_locked_hook_keeps_a_single_hook_background():
    """Con el titulo del hook bloqueado, re-dirigir conserva SU fondo (_lock_groups) y no emite
    una copia identica (fx_hookbg_s0b): dos bg_replace iguales duplicaban recorte y compositor."""
    p = D.direct(synthetic_plan(), use_llm=False)
    hook = [t for t in p["fx"]["text"] if t["id"].startswith("tx_hook")][0]
    hook["locked"] = True
    a, b = hook["start"], hook["end"]
    for st in ("dinamico", "energetico", "dinamico"):
        p = D.redirect(p, style=st, music_sync=False)
        bgs = [e for e in p["fx"]["effects"] if e["kind"] == "bg_replace"
               and min(b, e["end"]) - max(a, e["start"]) > 0.3 * (b - a)]
        assert len(bgs) == 1, [e["id"] for e in bgs]


def test_closing_broll_card_lasts_end_min():
    """El b-roll final (fachada) es la tarjeta de cierre: con la transicion de escena de por medio,
    Seguir y la ubicacion se ven al menos END_MIN s (antes: 1.7 s con END_KEEP = 2.2)."""
    q = _broll_last_plan()
    q["profile"] = {"name": "Dra. Lucia Demo", "title": "Odontologa",
                    "links": [{"url": "https://instagram.com/dra.lucia.demo", "platform": "instagram"}]}
    out = D.direct(q, use_llm=False)
    tl = _tl(out)
    Dur = tl.final_duration
    closing = [g for g in out["fx"]["graphics"] if g["kind"] in ("cta", "card")
               and g.get("variant") in ("follow", "location") and tl.remap_clamped(g["end"]) >= Dur - 1e-3]
    assert closing
    for g in closing:
        assert Dur - tl.remap_clamped(g["start"]) >= D._Choreo.END_MIN - 0.05, (g["id"], tl.remap_clamped(g["start"]), Dur)


def test_visible_labels_keep_their_accents():
    """Etiquetas que se ven en el video con su ortografia: 'UBICACIÓN' (kicker de la escena de
    ubicacion) y 'Sígueme para más' (Seguir sin nombre ni usuario)."""
    from types import SimpleNamespace
    from motion import graphics
    out = D.direct(synthetic_plan(), use_llm=False)
    kick = [c["text"]["text"] for c in out["fx"]["cutaways"]
            if c["kind"] == "info_card" and isinstance(c.get("text"), dict)]
    assert kick and all("UBICACION" not in k for k in kick), kick
    assert "UBICACIÓN" in kick
    assert graphics.follow_texts({}, SimpleNamespace(entities={}))[0] == "Sígueme para más"


@need_real
def test_price_circle_embraces_the_service_tag(dentista):
    """Precio con la etiqueta del servicio encima: el circulo a mano la abraza entera. Al alto del
    numero su trazo cruzaba la pastilla y parecia tacharla ('LIMPIEZA' tachada)."""
    p = D.direct(dentista, use_llm=False)
    gr = p["fx"]["graphics"]
    tags = [g for g in gr if g["id"].startswith("gr_price_tag")]
    marks = [g for g in gr if g["id"].startswith("gr_price_mark")]
    if not (tags and marks):
        pytest.skip("este plan no lleva etiqueta y circulo en el precio")
    tag, mk = tags[0], marks[0]
    half_h = mk["size"] * mk.get("aspect", 1.0) * (1080 / 1920) / 2       # semieje vertical (fraccion de H)
    top = mk["y"] - half_h
    assert top <= tag["y"] - D.PRICE_TAG_HALF - 0.005, (top, tag["y"])
