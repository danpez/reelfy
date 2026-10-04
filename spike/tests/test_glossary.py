"""Glosario del usuario contra la transcripcion (scripts/glossary.py + pipeline.fix_glossary + montaje).

Textos y nombres FICTICIOS (los errores tipicos de Whisper con nombres propios: mayusculas, nombres
partidos, casi iguales y el simbolo hablado). Cada regla trae su candado: nada fuera del glosario cambia."""
import glossary as G
import pipeline

GL = "Nubis, nubi, dot, VozMax, CasaLuma, Pro, Ruta, Taller Brisa, Yaretzi, Ramira"


def W(text, t0=0.0, step=0.3):
    out, t = [], t0
    for tok in text.split():
        out.append({"text": tok, "start": round(t, 3), "end": round(t + step * 0.8, 3)})
        t += step
    return out


def txt(ws):
    return " ".join(w["text"] for w in ws)


def test_misma_palabra_toma_la_escritura_del_glosario():
    got = txt(G.apply(W("se llaman NUBIs, y con una NUBI tú le asignas algo. De hecho cada Nubi tiene su "
                        "computadora. los usuarios Pro, que son de ruta."), GL))
    assert got == ("se llaman Nubis, y con una nubi tú le asignas algo. De hecho cada nubi tiene su "
                   "computadora. los usuarios Pro, que son de Ruta.")


def test_termino_en_minusculas_conserva_mayuscula_de_inicio():
    assert txt(G.apply(W("Nubi es una idea. Nubi va."), GL)) == "Nubi es una idea. Nubi va."
    assert txt(G.apply(W("NUBI es una idea."), GL)) == "Nubi es una idea."


def test_acentos_y_signos_se_conservan():
    got = G.apply(W("¿Ruta? «vozmax» (pro)"), GL)
    assert txt(got) == "¿Ruta? «VozMax» (Pro)"


def test_varias_palabras_igual_y_casi_igual():
    assert txt(G.apply(W("la asistente de taller brisa."), GL)) == "la asistente de Taller Brisa."
    assert txt(G.apply(W("Hola, soy la asistente de Taller Prisa."), GL)) == "Hola, soy la asistente de Taller Brisa."


def test_casi_igual_solo_si_whisper_lo_escribio_como_nombre():
    # en minusculas son palabras comunes: no se tocan
    assert txt(G.apply(W("desde un taller prisa y el taller de bicis"), GL)) == "desde un taller prisa y el taller de bicis"


def test_palabras_que_juntas_forman_un_termino_se_unen():
    ws = W("habla con Voz Max. y Casa Luma hoy", step=0.5)
    got = G.apply(ws, GL)
    assert txt(got) == "habla con VozMax. y CasaLuma hoy"
    vm = next(w for w in got if w["text"].startswith("VozMax"))
    assert vm["start"] == ws[2]["start"] and vm["end"] == ws[3]["end"]      # tiempos de las dos
    assert len(got) == len(ws) - 2
    # nunca a traves de un signo
    assert txt(G.apply(W("es Voz, Max dijo"), GL)) == "es Voz, Max dijo"


def test_simbolo_hablado():
    ws = [{"text": "Con", "start": 0.0, "end": 0.2}, {"text": "un", "start": 0.2, "end": 0.3},
          {"text": ".no,", "start": 0.3, "end": 0.75}, {"text": "a", "start": 0.8, "end": 0.9}]
    got = G.apply(ws, GL)
    assert [w["text"] for w in got] == ["Con", "un", "dot", "no,", "a"]
    assert got[2]["start"] == 0.3 and got[3]["end"] == 0.75 and got[2]["end"] == got[3]["start"]
    assert 0.3 < got[2]["end"] < 0.75
    # un punto suelto DICHO (con duracion) es la palabra
    got = G.apply([{"text": "un", "start": 0.0, "end": 0.1}, {"text": ".", "start": 0.1, "end": 0.35}], GL)
    assert [w["text"] for w in got] == ["un", "dot"]
    # sin "dot" en el glosario los simbolos no se tocan
    assert [w["text"] for w in G.apply(ws, "Ruta")] == ["Con", "un", ".no,", "a"]


def test_casi_igual_de_una_palabra_solo_si_suena_igual():
    assert txt(G.apply(W("con Yaretsi y Ruta."), GL)) == "con Yaretzi y Ruta."
    # suena distinto: Ramiro no es Ramira
    assert txt(G.apply(W("con Ramiro hoy"), GL)) == "con Ramiro hoy"
    # en minusculas (palabra comun) no se corrige por parecido
    assert txt(G.apply(W("yaretsi"), GL)) == "yaretsi"


def test_same_count_no_une_ni_parte():
    ws = W("Voz Max y .no")
    got = G.apply(ws, GL, same_count=True)
    assert len(got) == len(ws) and txt(got) == "Voz Max y .no"
    assert txt(G.apply(W("los NUBIs"), GL, same_count=True)) == "los Nubis"


def test_tiempos_intactos_y_copias():
    ws = W("los NUBIs de ruta")
    got = G.apply(ws, GL)
    assert [(w["start"], w["end"]) for w in got] == [(w["start"], w["end"]) for w in ws]
    assert ws[1]["text"] == "NUBIs"          # la entrada no cambia


def test_terminos_del_glosario():
    assert G.terms("Nombres propios y términos: Nubis, nubi, VozMax.") == ["Nubis", "nubi", "VozMax"]
    assert G.terms("Ruta; ruta\nTaller  Brisa,  ") == ["Ruta", "Taller  Brisa"]
    assert G.terms("") == [] and G.terms(None) == []
    assert G.apply(W("los NUBIs"), "") == W("los NUBIs")


def test_fix_text():
    assert G.fix_text("Los NUBIs de voz max.\nTaller Prisa", GL) == "Los Nubis de VozMax.\nTaller Brisa"


def test_clave_fonetica():
    assert G.phonetic("Yaretsi") == G.phonetic("Yaretzi")
    assert G.phonetic("Vaca") == G.phonetic("baka")
    assert G.phonetic("Ramiro") != G.phonetic("Ramira")


def test_pipeline_fix_glossary():
    ws = W("los NUBIs de ruta")
    assert txt(pipeline.fix_glossary(ws, GL)) == "los Nubis de Ruta"
    assert pipeline.fix_glossary(ws, "") is ws


def test_montaje_usa_la_escritura_del_glosario():
    import montage
    words = W("VozMax lanzó las NUBIs. Con una NUBI tú le asignas una tarea a tu asistente.", step=0.35)
    clips = [{"sid": "c1", "name": "IMG_1.MOV", "dur": 30.0, "words": words, "audio": None, "takes": []}]
    m = montage.build(clips, glossary=GL, llm=None)
    said = " ".join(c["text"] for s in m["slots"] for c in s["cands"])
    assert "Nubis" in said and "nubi" in said and "NUBI" not in said
    assert "Nubis" in m["script"]["text"]
    # w0/w1 siguen siendo indices de las palabras originales (mismo largo)
    for s in m["slots"]:
        for c in s["cands"]:
            assert 0 <= c["w0"] <= c["w1"] < len(words)


# ------------------------------------------------------------------ marcas como las de un video real
# (FICTICIAS, con los mismos patrones: una marca en minusculas y su plural, un producto con siglas pegadas
# y un negocio de dos palabras que empieza con una palabra comun; el glosario trae ademas "dot" como
# palabra dicha, que es tambien el nombre de un simbolo)
GL2 = "nubi, Nubis, dot, VozGPT, Punto Norte"


def test_marca_en_minusculas_y_su_plural():
    assert txt(G.apply(W("Se llaman nubis. Cada Nubi tiene su computadora y un NUBI no se cansa."), GL2)) == \
        "Se llaman Nubis. Cada nubi tiene su computadora y un nubi no se cansa."
    # al inicio de una oracion conserva la mayuscula
    assert txt(G.apply(W("Hola. NUBI responde."), GL2)) == "Hola. Nubi responde."


def test_producto_con_siglas_pegadas_en_cualquier_escritura():
    for said in ("Voz GPT", "voz gpt", "VOZ GPT", "Voz-GPT", "vozgpt", "VozGpt"):
        assert txt(G.apply(W(f"le preguntas a {said}, y responde"), GL2)) == "le preguntas a VozGPT, y responde", said
    # con same_count (indices de palabra del montaje) se corrige solo la escritura, sin unir
    assert txt(G.apply(W("le preguntas a voz-gpt hoy"), GL2, same_count=True)) == "le preguntas a VozGPT hoy"


def test_negocio_de_dos_palabras_que_empieza_con_palabra_comun():
    assert txt(G.apply(W("hola, soy de punto norte. Bienvenido a Punto norte"), GL2)) == \
        "hola, soy de Punto Norte. Bienvenido a Punto Norte"
    # pegado o con guion: su escritura (una palabra con su espacio, mismos tiempos)
    ws = W("la cuenta de Puntonorte y punto-norte.")
    got = G.apply(ws, GL2)
    assert txt(got) == "la cuenta de Punto Norte y Punto Norte."
    assert [(w["start"], w["end"]) for w in got] == [(w["start"], w["end"]) for w in ws]
    # la palabra comun sola no cambia, y "punto" no es el simbolo "." aunque el glosario traiga "dot"
    assert txt(G.apply(W("es un punto de venta, punto."), GL2)) == "es un punto de venta, punto."


def test_subtitulos_del_analisis_con_el_glosario():
    """El analisis del video compuesto corrige las palabras de los SUBTITULOS (pipeline.fix_glossary
    antes de agrupar frases): las tres marcas salen bien escritas en plan.phrases."""
    ws = W("Se llaman nubis. Con un nubi le pides algo a voz gpt para punto norte.")
    phrases = pipeline.group_phrases(pipeline.fix_glossary(ws, GL2))
    shown = " ".join(w["text"] for ph in phrases for w in ph)
    assert shown == "Se llaman Nubis. Con un nubi le pides algo a VozGPT para Punto Norte."
