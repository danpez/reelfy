"""Tono negativo del director (motion/director.py): un tropiezo no se celebra, se recalca.

Pedido de un cliente: en una frase de tropiezo el director ponia destellos y estrellas. Ahora:
- Deteccion: lexico del espanol con morfologia (equivoc-, fracas-, perd-...), negaciones de lo bueno
  ('no funciono', 'sin resultados', 'cero reservas', 'ni una venta'), dobles negaciones que son
  beneficio ('sin dolor', 'cero estres', 'no te preocupes', 'nunca falla'), lo que parece tropiezo y
  es beneficio, presuncion o invitacion ('¿Comisiones? Cero.', 'sin cita previa', 'nadie lo hace
  como nosotros', 'no te lo puedes perder', 'si tienes dudas') y la polaridad del LLM ('pol')
  cuando existe: 'negativa' sin senal lexica solo quita la celebracion; 'positiva' y 'neutra'
  quitan o bajan lo contextual del lexico (lo inequivoco, 'nos equivocamos', se queda).
- Tratamiento del tropiezo (nivel 2): look desaturado frio con vineta sobre SU clausula, glitch
  corto, golpe grave, la musica baja, titulo serio (pila con la palabra del tropiezo en blanco,
  entrada pesada) e icono negativo. Problema o duda (nivel 1): sin celebracion; look ligero solo si
  el LLM confirma la frase negativa.
- Nada celebratorio (destellos, estrellas, corazones, palomitas, iconos y sonidos de fiesta) en la
  frase del tropiezo ni a menos de NEG_PAD s; lo positivo sigue igual; determinista; lo bloqueado y
  lo del usuario no se toca; acento, color (grade ajustado) y perfil sobreviven al re-dirigir.

Solo datos SINTETICOS (frases genericas escritas aqui): sin video, sin LLM, sin datos de clientes.
"""
import copy

import pytest

from motion import director as D
from motion import schema as S
import test_director as TD

NEG_LINES = ["Abrimos la tienda en linea con muchisima emocion.",
             "Invertimos en anuncios durante todo el mes completo.",
             "Y al final nos equivocamos con el precio.",
             "Hoy tenemos clientes felices cada semana.",
             "Agenda tu cita con el link de mi perfil."]
POS_LINES = ["Abrimos la tienda en linea con muchisima emocion.",
             "Invertimos en anuncios durante todo el mes completo.",
             "Y al final encontramos el precio perfecto.",
             "Hoy tenemos clientes felices cada semana.",
             "Agenda tu cita con el link de mi perfil."]
MIXED_LINES = ["Abrimos la tienda en linea con muchisima emocion.",
               "Vendimos el doble en diciembre y abrimos dos sucursales, pero en enero perdimos todo el dinero.",
               "Hoy tenemos clientes felices cada semana.",
               "Agenda tu cita con el link de mi perfil."]


# ---------------------------------------------------------------- utilidades
def _words_of(text: str, t0: float = 0.0, step: float = 0.3) -> list[dict]:
    out, t = [], t0
    for i, tok in enumerate(text.split()):
        out.append({"text": tok, "n": D.norm(tok), "t0": t, "t1": t + 0.25, "kw": False, "i": i})
        t += step
    return out


def _beats(text: str, pol: str | None = None) -> list[dict]:
    ws = _words_of(text)
    s = {"id": 0, "w0": 0, "w1": len(ws) - 1, "info": {"pol": pol} if pol else {}}
    return D.negative_beats(ws, [s])


def _plan(lines, gap=0.55) -> dict:
    """Plan v1 sintetico: una frase por linea, dos tomas a camara."""
    phrases, t = [], 0.4
    for txt in lines:
        ws, t_end = TD._phrase(txt, t)
        t = t_end + gap
        for k in range(0, len(ws), 3):
            phrases.append(ws[k:k + 3])
    flat = [w for ph in phrases for w in ph]
    dur = round(flat[-1]["end"] + 1.2, 3)
    mid = round(dur / 2, 3)
    shot = {"person": True, "framing": "medio", "place": "oficina", "text_seen": [], "use": "a_camara",
            "free_space": "arriba"}
    shots = [dict(shot, start=0.0, end=mid, face=[0.5, 0.33, 0.14, 0.11]),
             dict(shot, start=mid, end=dur, face=[0.48, 0.34, 0.15, 0.12])]
    return {"stem": "neg", "video": "", "duration": dur, "dims": [1080, 1920], "phrases": phrases, "cuts": [],
            "beats": [], "camera": [], "highlights": [], "shots": shots}


def _direct(plan, seed=7, style="dinamico", off=False, monkeypatch=None):
    if off:
        monkeypatch.setattr(D, "negative_beats", lambda words, sents: [])
    out = D.direct(copy.deepcopy(plan), style=style, seed=seed, use_llm=False, music_sync=False)
    if off:
        monkeypatch.undo()
    return out


def _sent(plan, needle):
    ws = D._flatten_words(plan)
    return next(s for s in D._sentences(ws) if needle in D.norm(s["text"].replace(" ", "")))


def _celeb(out, a, b):
    """Graficos de fiesta de la IA que tocan [a, b] (tiempo original; sin cortes = final)."""
    return [g for g in out["fx"]["graphics"] if D.celebratory(g.get("kind"), g.get("name"))
            and g.get("src") != "user" and float(g["start"]) < b and a < float(g["end"])]


def _fx_by_id(out):
    return {it["id"]: it for tr in D.TRACKS for it in out["fx"][tr]}


# ---------------------------------------------------------------- deteccion (lexico)
@pytest.mark.parametrize("text, level, kind", [
    ("Fracasamos tres veces seguidas.", 2, "error"),
    ("Y ahi fue donde nos equivocamos.", 2, "error"),
    ("Me equivoque con el precio.", 2, "error"),
    ("Lanzamos la campana y no funciono.", 2, "error"),
    ("Nunca llegaron los pedidos.", 2, "cero"),
    ("No hubo ni una sola reserva.", 2, "cero"),
    ("Sin resultados en todo el mes.", 2, "cero"),
    ("Clientes nuevos? Cero.", 2, "cero"),
    ("Tuvimos 0 reservas esa semana.", 2, "cero"),
    ("Una app preciosa que nadie descargo.", 2, "nadie"),
    ("Las ventas bajaron muchisimo.", 2, "baja"),
    ("Perdimos dinero en enero.", 2, "baja"),
    ("Nos rechazaron en todos lados.", 2, "rechazo"),
    ("Es la parte mas dificil del proceso.", 1, "problema"),
    ("Pero algo nos faltaba para cerrar.", 1, "problema"),
    ("No sabemos si va a funcionar.", 1, "duda"),
    ("Tenia mucho miedo de empezar.", 1, "duda"),
])
def test_lexicon_detects_setbacks(text, level, kind):
    b = _beats(text)
    assert b, text
    top = max(b, key=lambda x: x["level"])
    assert (top["level"], top["kind"]) == (level, kind), (text, top)


@pytest.mark.parametrize("text", [
    "Sin dolor y sin complicaciones, cero estres.",
    "No te preocupes, no te lo pierdas.",
    "Lo que nadie te dice de los dentistas.",
    "Te atiende como nadie, mejor que nadie.",
    "Empezamos desde cero.",
    "Resolvemos tu problema en una sola cita.",
    "Si no funciona, te devolvemos tu dinero.",
    "No hay nada mejor que esto.",
    "Nunca falla.",
    "No fue un error, fue una decision.",
    "El costo de envio bajo muchisimo.",
    "Ahora falta disenar la nueva carta.",
    "Si te duele la muela, sabias que tiene solucion?",
    "No tiene comisiones ni letras chiquitas.",
    "Hace falta mas tiempo para disfrutar.",
    "Ya falta poco para la apertura.",
    "Vas a perder peso sin pasar hambre.",
    "Aqui pierdes el miedo al dentista.",
    # beneficios, presuncion e invitaciones (revision: salian como tropiezo fuerte)
    "¿Comisiones? Cero.", "¿Costo? Cero.", "Intereses: cero.", "Riesgo cero.", "Error cero.",
    "Cero errores.", "Margen de error minimo.", "Cero filas, cero espera.",
    "Atendemos sin cita previa.", "Sin registro y sin tarjeta.", "Puedes llegar sin agendar.",
    "No te quedes sin tu cita.", "No te quedes sin tu lugar.",
    "Nadie lo hace como nosotros.", "Nadie te atiende mejor.", "Nadie mas te ofrece esto.",
    "Nadie te lo habia dicho asi.", "Esto nadie te lo cuenta.", "Ganamos el premio, nadie lo esperaba.",
    "Ningun cliente se va insatisfecho.", "Nadie se queja del servicio.",
    "No te lo puedes perder.", "¿Te lo vas a perder?", "No perdimos ni un cliente.",
    "Y no tuvimos que pagar nada.", "No pude creer los resultados.", "No llegues tarde.",
    "Bajala y agenda tu cita.", "Terriblemente delicioso.",
    # invitaciones y modismos con 'dudas', 'faltar', 'dolor', 'peor' (nivel 1 sobre una invitacion)
    "Si tienes dudas, escribeme por mensaje.", "¿Tienes dudas? Mandame mensaje.",
    "Sin lugar a dudas es el mejor.", "Te ayudamos con tus dudas.", "Resolvemos todas tus dudas.",
    "Lo que no te puede faltar este verano.", "Faltan tres dias para el lanzamiento.",
    "Falta muy poquito.", "Despidete del dolor de espalda.", "Hasta el peor dia mejora con cafe.",
])
def test_lexicon_ignores_benefits_and_hooks(text):
    assert _beats(text) == [], (text, _beats(text))


@pytest.mark.parametrize("text, level, kind", [
    ("¿Reservas? Cero.", 2, "cero"),
    ("Y las ventas, cero.", 2, "cero"),
    ("Nos quedamos sin clientes y sin dinero.", 2, "cero"),
    ("No nos quedamos sin clientes, nos quedamos sin tiempo.", None, None),
    ("Ningun cliente volvio.", 2, "cero"),
    ("No vino nadie mas.", 2, "cero"),
    ("Nadie vino asi que cerramos.", 2, "nadie"),
    ("Pierdes clientes cada dia.", 1, "problema"),
    ("No nos fue bien.", 2, "error"),
    ("No pudimos vender nada.", 2, "error"),
    ("Tenemos muchas dudas.", 1, "duda"),
])
def test_lexicon_context_keeps_real_setbacks(text, level, kind):
    """Las excepciones de contexto no apagan los tropiezos de verdad."""
    b = _beats(text)
    if level is None:
        assert b == [], (text, b)
        return
    top = max(b, key=lambda x: x["level"])
    assert (top["level"], top["kind"]) == (level, kind), (text, top)


def test_terminal_zero_follows_its_subject():
    """'X? Cero.' es tropiezo si X es lo bueno del negocio, beneficio si es lo malo; sin sujeto claro
    ('... todo. Cero.') es nivel 1 sin look, que el LLM sube a 2 solo si lee la frase 'negativa'. El
    sujeto puede ser la frase anterior (el '¿Reservas?' de un 'Cero.' suelto)."""
    ws = _words_of("¿Reservas? Cero.")
    two = D.negative_beats(ws, [{"id": 0, "w0": 0, "w1": 0, "info": {}}, {"id": 1, "w0": 1, "w1": 1, "info": {}}])
    assert [(b["sid"], b["level"], b["look"]) for b in two] == [(1, 2, True)]
    ws = _words_of("¿Comisiones? Cero.")
    assert D.negative_beats(ws, [{"id": 0, "w0": 0, "w1": 0, "info": {}}, {"id": 1, "w0": 1, "w1": 1, "info": {}}]) == []
    b = _beats("Lo intentamos todo. Cero.")
    assert [(x["level"], x["kind"], x["look"]) for x in b] == [(1, "cero", False)]
    b = _beats("Lo intentamos todo. Cero.", pol="negativa")
    assert [(x["level"], x["kind"], x["look"]) for x in b] == [(2, "cero", True)]


def test_llm_polarity_vetoes_the_lexicon():
    """La polaridad del LLM tambien QUITA: 'positiva' descarta lo contextual (cero, sin, ningun,
    nadie, no + verbo, perder) y deja lo inequivoco en nivel 1 sin look; 'neutra' baja lo contextual
    a nivel 1 sin look y deja lo inequivoco en 2 (una frase de lista: 'que si funciona y en que nos
    equivocamos'); sin polaridad (heuristicas) o 'negativa' manda el lexico."""
    contextual = ["Nos quedamos sin clientes.", "Ninguna venta en todo el mes.", "No vino nadie.",
                  "Clientes nuevos? Cero.", "Perdimos dinero en enero.", "Una app que nadie descargo."]
    for text in contextual:
        assert max(b["level"] for b in _beats(text)) == 2, text
        assert max(b["level"] for b in _beats(text, pol="negativa")) == 2, text
        assert _beats(text, pol="positiva") == [], text
        assert [(b["level"], b["look"]) for b in _beats(text, pol="neutra")] == [(1, False)], text
    firm = "que si funciona y, sobre todo, en que nos equivocamos."
    assert [(b["level"], b["look"]) for b in _beats(firm, pol="neutra")] == [(2, True)]
    assert [(b["level"], b["look"]) for b in _beats(firm, pol="positiva")] == [(1, False)]
    assert [(b["level"], b["look"]) for b in _beats(firm)] == [(2, True)]
    # un problema o una duda (nivel 1) solo lleva look si el LLM confirma la frase negativa
    weak = "Es la parte mas dificil del proceso."
    assert [(b["level"], b["look"]) for b in _beats(weak)] == [(1, False)]
    assert [(b["level"], b["look"]) for b in _beats(weak, pol="negativa")] == [(1, True)]
    assert [(b["level"], b["look"]) for b in _beats(weak, pol="neutra")] == [(1, False)]


def test_mixed_sentence_beat_is_its_clause():
    """'Vendimos el doble..., pero en enero perdimos...': el tropiezo es SU clausula."""
    text = "Vendimos el doble en diciembre, pero en enero perdimos todo el dinero."
    ws = _words_of(text)
    b = _beats(text)
    assert len(b) == 1 and b[0]["level"] == 2
    clause = [w["text"] for w in ws[b[0]["w0"]:b[0]["w1"] + 1]]
    assert clause[0] == "pero" and "perdimos" in clause and "Vendimos" not in clause
    assert b[0]["t0"] > ws[0]["t0"]


def test_short_lead_in_belongs_to_the_punchline():
    """'lo que aprendimos y, al final, donde fallamos': la entrada corta va con el remate."""
    text = "lo que aprendimos y, al final, donde fallamos."
    ws = _words_of(text)
    b = _beats(text)
    clause = [w["n"] for w in ws[b[0]["w0"]:b[0]["w1"] + 1]]
    assert clause[:2] == ["al", "final"] and clause[-1] == "fallamos" and "aprendimos" not in clause


def test_llm_polarity_only_adds_a_weak_beat_without_look():
    """La polaridad del LLM suma la frase como nivel 1 (sin celebracion) si el lexico no vio nada;
    nunca sube a tratamiento completo ni pone look."""
    b = _beats("Y entonces paso esto con el cliente.", pol="negativa")
    assert len(b) == 1 and b[0]["level"] == 1 and b[0].get("llm")
    assert _beats("Y entonces paso esto con el cliente.", pol="positiva") == []
    # con senal lexica manda el lexico (sin la marca del LLM)
    b2 = _beats("Y al final nos equivocamos.", pol="negativa")
    assert b2[0]["level"] == 2 and not b2[0].get("llm")


def test_llm_pol_is_merged_validated_and_persisted():
    p = _plan(NEG_LINES)
    shots = D.analyze_shots(p, use_llm=False)
    llm = {"topic": "", "ent": {}, "f": [{"id": 1, "pol": "negativa"}, {"id": 3, "pol": "rara"}]}
    script = D.analyze_script(p, shots, use_llm=False, llm_result=llm)
    by = {s["id"]: s["info"] for s in script["sents"]}
    assert by[1].get("pol") == "negativa" and "pol" not in by[3]
    pub = D._script_public(script)
    back = D._script_from_public(p, pub, shots)
    assert {s["id"]: s["info"].get("pol") for s in back["sents"]}[1] == "negativa"
    # el esquema del LLM pide la polaridad y el prompt explica la regla
    assert "pol" in D._LLM_SCHEMA["properties"]["f"]["items"]["required"]
    assert "pol \"negativa\"" in D._llm_prompt(script["sents"], shots)


# ---------------------------------------------------------------- tratamiento en el plan
def test_negative_sentence_gets_no_party_and_a_negative_treatment(monkeypatch):
    """Frase de tropiezo: sin destellos/estrellas (ni a menos de NEG_PAD s) en ninguna semilla, aunque
    sin la regla el director si los ponia; y con su tratamiento negativo completo."""
    p = _plan(NEG_LINES)
    s = _sent(p, "equivocamos")
    a, b = s["t0"] - D.NEG_PAD, s["t1"] + D.NEG_PAD
    had_party = 0
    for seed in range(1, 9):
        off = _direct(p, seed, off=True, monkeypatch=monkeypatch)
        had_party += bool(_celeb(off, a, b))
        on = _direct(p, seed)
        assert _celeb(on, a, b) == [], (seed, _celeb(on, a, b))
        sfx = [x for x in on["fx"]["sfx"] if a <= float(x["at"]) <= b]
        assert not [x for x in sfx if x["name"] in D.CELEB_SFX], seed
        # look desaturado frio con vineta sobre la clausula del tropiezo
        looks = [e for e in on["fx"]["effects"] if e["kind"] == "bw" and e["id"].startswith("fx_neg")]
        assert len(looks) == 1, seed
        lk = looks[0]
        assert 0.6 <= lk["intensity"] < 1.0 and lk["tone"] == "cool" and lk["vignette"] > 0.3
        assert lk["start"] <= s["t1"] and lk["end"] >= s["t1"] - 0.3
        # glitch corto y golpe grave en la palabra del tropiezo
        hit = next(w for w in D._flatten_words(p) if w["n"] == "equivocamos")
        assert any(e["kind"] == "rgb_split" and abs(e["start"] - hit["t0"]) < 0.3 and e["end"] - e["start"] < 0.4
                   for e in on["fx"]["effects"]), seed
        assert any(x["name"] == "sub_drop" and abs(float(x["at"]) - hit["t0"]) < 0.4 for x in on["fx"]["sfx"]), seed
        # la musica baja en el tropiezo
        assert any(sw["db"] < 0 and sw["start"] <= hit["t0"] <= sw["end"] for sw in on["fx"]["music"]["swells"])
        # titulo serio: pila con la palabra del tropiezo grande, en tinta (no acento) y entrada pesada
        tits = [t for t in on["fx"]["text"] if float(t["start"]) <= hit["t0"] <= float(t["end"])]
        assert tits, seed
        st = tits[0]
        assert st["preset"] == "stack" and st["anim_in"] == "scale_down" and st["accent_words"] == []
        big = [ln for ln in st["lines"] if float(ln.get("scale", 1.0)) == 1.0]
        assert [ln["text"] for ln in big] == ["EQUIVOCAMOS"]
        assert all(ln.get("color", st["color"]) in ("#FFFFFF", st["color"]) for ln in st["lines"])
        # icono negativo sobrio (disco monocromo, derecho), centrado
        icons = [g for g in on["fx"]["graphics"] if g["kind"] == "icon" and a <= float(g["start"]) <= b]
        assert icons and all(g["name"] in ("circle-x", "x") for g in icons), seed
        assert all(abs(float(g["x"]) - 0.5) < 0.004 and g["rotation"] == 0.0 for g in icons)
        assert D.validate_min(on) == [] and on["director"]["warnings"] == []
    assert had_party >= 3          # sin la regla habia destellos/estrellas en ese tropiezo


def test_positive_plan_is_unchanged(monkeypatch):
    """Un guion sin tropiezos sale IDENTICO con o sin la regla (la regla no consume azar ni mueve
    nada en lo positivo)."""
    for mk in (lambda: _plan(POS_LINES), TD.synthetic_plan):
        p = mk()
        assert D.negative_beats(D._flatten_words(p), D._sentences(D._flatten_words(p))) == []
        for seed in (3, 7):
            off = _direct(p, seed, off=True, monkeypatch=monkeypatch)
            on = _direct(p, seed)
            for tr in D.TRACKS:
                assert on["fx"][tr] == off["fx"][tr], (tr, seed)
            assert on["fx"]["music"] == off["fx"]["music"] and on["fx"]["grade"] == off["fx"]["grade"]


def test_positive_sentences_keep_their_party(monkeypatch):
    """Lo positivo lejos del tropiezo sigue pudiendo celebrar (la regla no apaga todo el video)."""
    p = _plan(NEG_LINES)
    s = _sent(p, "equivocamos")
    a, b = s["t0"] - D.NEG_PAD, s["t1"] + D.NEG_PAD
    outside = 0
    for seed in range(1, 9):
        on = _direct(p, seed)
        outside += len([g for g in on["fx"]["graphics"] if D.celebratory(g.get("kind"), g.get("name"))
                        and not (float(g["start"]) < b and a < float(g["end"]))])
    assert outside >= 4


def test_mixed_sentence_treats_only_the_negative_clause():
    """'Vendimos el doble..., pero en enero perdimos...': el look y el titulo serio van en la
    clausula del tropiezo; la parte buena queda a color; nada de fiesta en la frase."""
    p = _plan(MIXED_LINES)
    s = _sent(p, "perdimos")
    ws = D._flatten_words(p)
    pero = next(w for w in ws if w["n"] == "pero")
    hit = next(w for w in ws if w["n"] == "perdimos")
    for seed in (2, 7, 11):
        out = _direct(p, seed)
        looks = [e for e in out["fx"]["effects"] if e["kind"] == "bw" and e["id"].startswith("fx_neg")]
        assert len(looks) == 1
        assert looks[0]["start"] >= pero["t0"] - 0.1          # la parte buena queda a color
        assert looks[0]["start"] <= hit["t0"] <= looks[0]["end"]
        assert _celeb(out, s["t0"] - D.NEG_PAD, s["t1"] + D.NEG_PAD) == []
        serious = [t for t in out["fx"]["text"] if t["preset"] == "stack" and t.get("anim_in") == "scale_down"]
        assert serious and all(float(t["start"]) >= pero["t0"] - 0.1 for t in serious)
        assert any("PERDIMOS" in [ln["text"] for ln in t["lines"]] for t in serious)


def test_deterministic_with_negative_beats():
    p = _plan(NEG_LINES)
    a = D.direct(copy.deepcopy(p), seed=5, use_llm=False, music_sync=False)
    b = D.direct(copy.deepcopy(p), seed=5, use_llm=False, music_sync=False)
    for tr in D.TRACKS:
        assert a["fx"][tr] == b["fx"][tr], tr
    r1 = D.redirect(copy.deepcopy(a), music_sync=False)
    r2 = D.redirect(copy.deepcopy(a), music_sync=False)
    for tr in D.TRACKS:
        assert r1["fx"][tr] == r2["fx"][tr], tr


@pytest.mark.parametrize("style", list(D.STYLES))
def test_all_styles_keep_the_rule(style):
    p = _plan(NEG_LINES)
    s = _sent(p, "equivocamos")
    out = _direct(p, 7, style=style)
    assert D.validate_min(out) == [] and out["director"]["warnings"] == []
    assert _celeb(out, s["t0"] - D.NEG_PAD, s["t1"] + D.NEG_PAD) == []
    looks = [e for e in out["fx"]["effects"] if e["kind"] == "bw" and e["id"].startswith("fx_neg")]
    assert len(looks) == 1
    glitch = [e for e in out["fx"]["effects"] if e["id"].startswith(("fx_neg_glitch", "fx_neg_shake"))]
    if style in ("elegante", "minimal"):
        assert glitch == []                  # sobrio: solo look, titulo, icono y golpe
    else:
        assert len(glitch) == 1


def test_llm_only_beat_removes_party_without_look():
    """Frase que solo el LLM marca negativa: sin fiesta en ella, pero sin look (el modelo chico se
    equivoca; el look pide senal lexica)."""
    p = _plan(POS_LINES)
    shots = D.analyze_shots(p, use_llm=False)
    sents = D._sentences(D._flatten_words(p))
    llm = {"topic": "", "ent": {}, "f": [{"id": s_["id"], "pol": "negativa" if s_["id"] == 3 else "neutra"}
                                         for s_ in sents]}
    script = D.analyze_script(p, shots, use_llm=False, llm_result=llm)
    for seed in range(1, 6):
        fx, ch = D.choreograph(copy.deepcopy(p), shots, script, "dinamico", 0.75, seed)
        assert [(b["sid"], b["level"], bool(b.get("llm"))) for b in ch.neg] == [(3, 1, True)]
        s3 = sents[3]
        assert not [g for g in fx["graphics"] if D.celebratory(g.get("kind"), g.get("name"))
                    and float(g["start"]) < s3["t1"] and s3["t0"] < float(g["end"])]
        assert not [e for e in fx["effects"] if e["kind"] == "bw"]


def _script_with_pol(p, pols: dict):
    shots = D.analyze_shots(p, use_llm=False)
    sents = D._sentences(D._flatten_words(p))
    llm = {"topic": "", "ent": {}, "f": [{"id": s_["id"], "pol": pols.get(s_["id"], "neutra")} for s_ in sents]}
    return shots, D.analyze_script(p, shots, use_llm=False, llm_result=llm), sents


@pytest.mark.parametrize("line", ["Nadie lo hace como nosotros.", "¿Comisiones? Cero.",
                                  "Atendemos sin cita previa todos los dias.",
                                  "Si tienes dudas, escribeme por mensaje directo."])
def test_benefit_lines_get_no_setback_treatment(line):
    """Presuncion, beneficio o invitacion (sin LLM): ni look, ni glitch, ni titulo serio, ni golpe
    grave, ni icono negativo, ni la musica baja; la frase puede llevar recursos e insertos."""
    p = _plan(NEG_LINES[:2] + [line] + NEG_LINES[3:])
    for seed in (3, 7):
        out = _direct(p, seed)
        fx = out["fx"]
        assert not [e for e in fx["effects"] if e["id"].startswith("fx_neg")], (line, seed)
        assert not [g for g in fx["graphics"] if "neg" in g["id"]], (line, seed)
        assert not [s_ for s_ in fx["music"].get("swells") or [] if s_["db"] < 0], (line, seed)
        assert not [t for t in fx["text"] if t["preset"] == "stack" and t.get("anim_in") == "scale_down"]
        assert D.validate_min(out) == [] and out["director"]["warnings"] == []


def test_weak_beat_look_needs_llm_confirmation():
    """Un problema o una duda (nivel 1 del lexico) quita la celebracion de su frase, pero el look
    ligero solo va si el LLM confirma la frase 'negativa'."""
    lines = NEG_LINES[:2] + ["Es la parte mas dificil de todo el proceso de venta."] + NEG_LINES[3:]
    p = _plan(lines)
    for pol, want in (("neutra", 0), ("negativa", 1)):
        shots, script, sents = _script_with_pol(p, {2: pol})
        for seed in (2, 5):
            fx, ch = D.choreograph(copy.deepcopy(p), shots, script, "dinamico", 0.75, seed)
            assert [(b["sid"], b["level"]) for b in ch.neg] == [(2, 1)]
            looks = [e for e in fx["effects"] if e["kind"] == "bw" and e["id"].startswith("fx_neg")]
            assert len(looks) == want, (pol, seed, looks)
            s2 = sents[2]
            assert not [g for g in fx["graphics"] if D.celebratory(g.get("kind"), g.get("name"))
                        and float(g["start"]) < s2["t1"] and s2["t0"] < float(g["end"])], (pol, seed)
    # sin LLM (heuristicas): tampoco hay look
    out = _direct(p, 5)
    assert not [e for e in out["fx"]["effects"] if e["kind"] == "bw" and e["id"].startswith("fx_neg")]


def test_llm_positive_polarity_removes_the_treatment():
    """El LLM lee 'positiva' una frase con un tropiezo contextual ('nos quedamos sin clientes' dicho
    como logro superado): sin tratamiento; con una raiz inequivoca ('nos equivocamos') queda sin
    celebracion pero sin look ni titulo serio. Con 'negativa' el tratamiento completo sigue."""
    lines = NEG_LINES[:2] + ["Y al final nos quedamos sin clientes nuevos."] + NEG_LINES[3:]
    for text, pol, strong in ((lines, "positiva", False), (lines, "negativa", True),
                              (NEG_LINES, "positiva", False), (NEG_LINES, "neutra", True)):
        p = _plan(text)
        shots, script, sents = _script_with_pol(p, {2: pol})
        fx, ch = D.choreograph(copy.deepcopy(p), shots, script, "dinamico", 0.75, 7)
        looks = [e for e in fx["effects"] if e["kind"] == "bw" and e["id"].startswith("fx_neg")]
        serious = [t for t in fx["text"] if t["preset"] == "stack" and t.get("anim_in") == "scale_down"]
        assert bool(looks) == strong and bool(serious) == strong, (text[2], pol, looks, serious)
        if text is NEG_LINES:                        # inequivoco: nunca se celebra
            s2 = sents[2]
            assert not [g for g in fx["graphics"] if D.celebratory(g.get("kind"), g.get("name"))
                        and float(g["start"]) < s2["t1"] and s2["t0"] < float(g["end"])], pol


# ---------------------------------------------------------------- lo del usuario y el proyecto
def test_locked_user_items_and_project_settings_survive():
    p = _plan(NEG_LINES)
    base = D.direct(copy.deepcopy(p), seed=4, use_llm=False, music_sync=False)
    s = _sent(p, "equivocamos")
    # un destello que el USUARIO puso sobre el tropiezo y un titulo bloqueado
    user = {"id": "gr_user_spark", "start": s["t0"], "end": s["t1"], "kind": "doodle", "name": "sparkle",
            "x": 0.5, "y": 0.3, "size": 0.1, "color": "#FFD23F", "src": "user", "locked": False, "enabled": True,
            "intensity": 1.0}
    base["fx"]["graphics"].append(user)
    tid = base["fx"]["text"][0]["id"]
    base["director"]["locked"] = [tid]
    locked_txt = copy.deepcopy(base["fx"]["text"][0])
    # ajustes del proyecto: acento, color afinado a mano y perfil
    grade = {"enabled": True, "look": "natural", "match_shots": True, "vignette": 0, "grain": 0, "sharpen": 0,
             "strength": 1.0, "exposure": -0.17, "temperature": 0.0, "tint": 0.0, "saturation": 1.16,
             "contrast": 0.28, "clarity": 0}
    base["fx"]["accent"] = "#08794d"
    base["fx"]["grade"] = copy.deepcopy(grade)
    base["profile"] = {"name": "Persona Prueba", "business": "Negocio Prueba", "links": [
        {"url": "https://instagram.com/cuenta.prueba", "platform": "instagram", "kind": "social",
         "handle": "@cuenta.prueba", "display": "@cuenta.prueba", "label": "Instagram", "primary": True}]}
    prof = S.normalize(copy.deepcopy(base))["profile"]          # el perfil tal cual (normalizado)
    assert prof["links"][0]["handle"] == "@cuenta.prueba"
    for kw in ({}, {"style": "elegante"}):
        out = D.redirect(copy.deepcopy(base), music_sync=False, **kw)
        by = _fx_by_id(out)
        assert by["gr_user_spark"] == user               # lo del usuario no se toca (ni el destello)
        assert by[tid] == locked_txt                     # lo bloqueado tampoco
        assert out["fx"]["accent"].upper() == "#08794D"
        assert out["fx"]["captions"]["accent"].upper() == "#08794D"
        assert out["fx"]["grade"] == grade               # el color afinado a mano sobrevive
        assert out["profile"] == prof
        # el resto del tropiezo sigue sin fiesta de la IA
        assert _celeb(out, s["t0"] - D.NEG_PAD, s["t1"] + D.NEG_PAD) == []
    full = D.direct(copy.deepcopy(base), seed=4, use_llm=False, music_sync=False)
    assert full["fx"]["grade"] == grade and full["fx"]["accent"].upper() == "#08794D"


def test_untouched_grade_still_follows_the_style():
    """Sin ajustes del usuario el color sigue siendo el look del estilo (re-dirigir con otro estilo
    lo cambia, como siempre)."""
    p = _plan(NEG_LINES)
    base = D.direct(copy.deepcopy(p), seed=4, use_llm=False, music_sync=False)
    assert not D._grade_tuned(base["fx"]["grade"], "dinamico")
    out = D.redirect(copy.deepcopy(base), style="elegante", music_sync=False)
    assert out["fx"]["grade"]["look"] == D.STYLES["elegante"]["look"]["look"]
    assert D._grade_tuned(dict(base["fx"]["grade"], exposure=-0.2), "dinamico")
    assert D._grade_tuned(dict(base["fx"]["grade"], enabled=False), "dinamico")


def test_user_caption_style_survives_redirect():
    """El estilo de subtitulos que eligio el usuario (preset 'boxed' con resaltado del acento: un
    acento oscuro se lee en la pastilla) sobrevive a dirigir y re-dirigir, con el mismo estilo o con
    otro; la geometria (y, tamano, tramos) sigue saliendo de la coreografia. Sin cambios propios, los
    subtitulos siguen el estilo nuevo; con 'captions' bloqueado se copian tal cual."""
    p = _plan(NEG_LINES)
    base = D.direct(copy.deepcopy(p), seed=4, use_llm=False, music_sync=False)
    assert base["fx"]["captions"]["preset"] == D.STYLES["dinamico"]["captions"]["preset"]
    untouched = D.redirect(copy.deepcopy(base), style="minimal", music_sync=False)
    assert untouched["fx"]["captions"]["font"] == D.STYLES["minimal"]["captions"]["font"]
    base["fx"]["accent"] = "#08794d"
    base["fx"]["captions"].update(preset="boxed", highlight="#08794d", accent="#08794d", case="upper")
    for kw in ({}, {"style": "elegante"}):
        out = D.redirect(copy.deepcopy(base), music_sync=False, **kw)
        cap = out["fx"]["captions"]
        assert (cap["preset"], cap["highlight"].upper(), cap["case"]) == ("boxed", "#08794D", "upper"), kw
        assert cap["accent"].upper() == "#08794D"
        st = kw.get("style", "dinamico")
        assert cap["font"] == D.STYLES[st]["captions"]["font"]      # lo que no toco sigue al estilo
        assert cap["size"] == D.STYLES[st]["captions"]["size"]
    full = D.direct(copy.deepcopy(base), seed=4, use_llm=False, music_sync=False)
    assert full["fx"]["captions"]["preset"] == "boxed" and full["fx"]["captions"]["highlight"].upper() == "#08794D"
    # 'Regenerar' los subtitulos (only_ids) SI propone otro preset
    regen = D.redirect(copy.deepcopy(base), only_ids=["captions"], seed=3)
    assert regen["fx"]["captions"]["preset"] != "boxed"


def test_regenerate_next_to_a_setback_never_celebrates():
    """'Regenerar' un garabato junto al tropiezo: la variante no es de fiesta."""
    p = _plan(NEG_LINES)
    base = D.direct(copy.deepcopy(p), seed=4, use_llm=False, music_sync=False)
    s = _sent(p, "equivocamos")
    for name in ("check", "heart", "underline"):
        q = copy.deepcopy(base)
        q["fx"]["graphics"].append({"id": "gr_doodle_probe_s2", "start": s["t0"], "end": s["t1"], "kind": "doodle",
                                    "name": name, "x": 0.5, "y": 0.25, "size": 0.1, "color": "#FFFFFF",
                                    "src": "ai", "locked": False, "enabled": True, "intensity": 1.0,
                                    "draw": True, "anim_in": "draw", "anim_out": "fade", "rotation": 0.0})
        for seed in (1, 2, 3):
            out = D.redirect(q, only_ids=["gr_doodle_probe_s2"], seed=seed)
            g = _fx_by_id(out).get("gr_doodle_probe_s2")
            if g is not None:
                assert g["name"] not in D.CELEB_DOODLES, (name, seed, g["name"])
