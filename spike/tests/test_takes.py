"""Limpieza de tomas (scripts/takes.py): retomas, arranques en falso, autocorrecciones, tartamudeos
y errores sobre las palabras de la transcripcion. Solo transcripciones SINTETICAS inventadas (el
repo es publico: nada de contenido de clientes). Tiempos de palabra estilo Whisper; `stretch`
imita el timing crudo: la pausa queda DENTRO de la palabra siguiente (sin hueco)."""
import re
import time

import numpy as np
import pytest

import takes as T
from motion.timeline import Timeline, keeps_from_cuts

SR = 16000


def words_of(src, stretch=False, t=0.5):
    """Mini-DSL: palabras separadas por espacio y |0.9| = pausa explicita (s) antes de la siguiente.
    Duracion = 0.10 + 0.06 por letra; hueco 0.04 (+0.12 tras coma, +0.35 tras punto, +0.5 tras '...')."""
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
        if stretch and out and pend is not None and pend >= 0.4:
            out.append({"text": tok, "start": round(t, 3), "end": round(ts + d, 3)})
        else:
            out.append({"text": tok, "start": round(ts, 3), "end": round(ts + d, 3)})
        t = ts + d
        pend = None
    return out


def run(src, stretch=False, **kw):
    w = words_of(src, stretch)
    return w, T.detect(w, w[-1]["end"] + 0.6, **kw)


def removed_idx(cuts, only_enabled=True):
    return {k for c in cuts if c["enabled"] or not only_enabled for k in range(c["w0"], c["w1"] + 1)}


def removed_text(words, cuts):
    return " ".join(words[k]["text"] for k in sorted(removed_idx(cuts)))


def clamp_mid(w):
    a, b = w["start"], w["end"]
    a = max(a, b - T.STRETCH_MAX)
    return (a + b) / 2


def assert_kept_safe(words, cuts):
    """Ninguna palabra que se queda con su punto medio dentro de un corte de tomas; las quitadas
    tienen el punto medio dentro de su corte."""
    for c in cuts:
        for k, w in enumerate(words):
            m = clamp_mid(w)
            if c["w0"] <= k <= c["w1"]:
                mm = (w["start"] + w["end"]) / 2
                assert c["start"] - 1e-6 <= mm <= c["end"] + 1e-6, (w, c)
            else:
                assert not (c["start"] < m < c["end"]), (w, c)


# ------------------------------------------------------------------ contrato
def test_contrato_de_los_cortes_y_del_resumen():
    w, r = run("El kit trae 19, no, 16 piezas. |0.7| Ponemos la la mezcla en el molde. |0.8| "
               "Este pastel quedó delicioso, no mames. |0.9| Ahora vamos con el relleno de fresa.")
    keys = {"id", "start", "end", "enabled", "src", "kind", "conf", "level", "reason", "text",
            "kept_text", "keep_ref", "group", "w0", "w1"}
    assert r["cuts"]
    for c in r["cuts"]:
        assert set(c) - {"guard"} == keys, set(c) ^ keys            # guard: solo si lo apago la seguridad
        assert c["src"] == "takes" and c["id"].startswith("tk_") and c["group"].startswith("tg_")
        assert c["kind"] in T.KINDS and c["level"] in T.LEVELS and 0 <= c["conf"] <= 1
        assert c["start"] < c["end"] and isinstance(c["reason"], str) and c["reason"]
        assert c["keep_ref"] is None or c["keep_ref"]["start"] >= c["start"]
    m = r["meta"]
    assert set(m) == {"version", "sensitivity", "topic", "summary", "sections", "stats", "llm", "ms"}
    assert m["version"] == 1 and m["sensitivity"] == "normal" and m["llm"] is False
    st = m["stats"]
    assert set(st) == {"n", "enabled", "removed_s", "suggested", "by_kind"}
    assert st["n"] == len(r["cuts"]) and st["enabled"] == sum(c["enabled"] for c in r["cuts"])
    assert st["suggested"] == sum(not c["enabled"] for c in r["cuts"])
    assert sum(v["n"] for v in st["by_kind"].values()) == st["n"]
    # nunca src "ai" (director.propose_trims se apaga si hay cortes "ai")
    assert all(c["src"] != "ai" for c in r["cuts"])


def test_ids_estables_entre_corridas_y_con_o_sin_audio():
    src = "Agenda tu cita al, |0.9| agenda tu cita al número que aparece en pantalla."
    w, r1 = run(src)
    _, r2 = run(src)
    audio = synth_audio(w, 8.0)
    r3 = T.detect(w, 8.0, audio=audio)
    ids = [c["id"] for c in r1["cuts"]]
    assert ids and ids == [c["id"] for c in r2["cuts"]] == [c["id"] for c in r3["cuts"]]
    assert T.detect([], 5.0)["cuts"] == [] and T.detect(None, 0)["meta"]["stats"]["n"] == 0


# ------------------------------------------------------------------ reglas
@pytest.mark.parametrize("stretch", [False, True])
def test_cadena_de_retomas_se_queda_la_ultima(stretch):
    src = ("Hoy les enseño a preparar pan de elote. |0.8| La masa lleva harina de, |1.1| la masa "
           "lleva harina de maíz y, |1.2| la masa lleva harina de maíz y un poco de mantequilla "
           "derretida. |0.6| Luego se hornea.")
    w, r = run(src, stretch)
    cuts = [c for c in r["cuts"] if c["enabled"]]
    assert removed_text(w, cuts) == "La masa lleva harina de, la masa lleva harina de maíz y,"
    last = next(k for k, x in enumerate(w) if x["text"] == "mantequilla") - 10    # 'la' de la ultima toma
    assert w[last]["text"] == "la" and w[last + 1]["text"] == "masa"
    assert len({c["group"] for c in cuts}) == 1                   # una cadena = un grupo
    for c in cuts:
        assert c["keep_ref"]["start"] == pytest.approx(w[last]["start"], abs=1e-3)
        assert c["kept_text"].startswith("la masa lleva harina de maíz y un poco")
        assert c["reason"].startswith("Lo repites mejor en 0:")
    assert_kept_safe(w, r["cuts"])


@pytest.mark.parametrize("stretch", [False, True])
def test_retoma_de_cola_solo_quita_el_final_rehecho(stretch):
    src = ("Esta crema se aplica por la mañana y también por la noche antes, |1.0| y también por "
           "la noche antes de dormir.")
    w, r = run(src, stretch)
    assert removed_text(w, r["cuts"]) == "y también por la noche antes,"
    assert r["cuts"][0]["kind"] == "retoma"
    assert_kept_safe(w, r["cuts"])


def test_tartamudeos():
    w, r = run("Ponemos la la mezcla en el molde y lo metemos al horno.")
    assert removed_text(w, r["cuts"]) == "la" and r["cuts"][0]["kind"] == "tartamudeo"
    w, r = run("Recuerda tomar agua entre cada, entre cada, entre cada serie de ejercicios.")
    assert removed_text(w, r["cuts"]) == "entre cada, entre cada,"
    assert {c["kind"] for c in r["cuts"]} == {"tartamudeo"}


@pytest.mark.parametrize("stretch", [False, True])
def test_arranque_en_falso_con_puntos_suspensivos(stretch):
    w, r = run("Si el dolor sigue es porque la... |1.4| Si el dolor sigue, ve con tu médico de confianza.",
               stretch)
    assert removed_text(w, r["cuts"]) == "Si el dolor sigue es porque la..."
    c = r["cuts"][0]
    assert c["kind"] == "arranque_fallido" and c["level"] == "alta" and c["enabled"]
    assert c["kept_text"].startswith("Si el dolor sigue, ve con tu")


@pytest.mark.parametrize("src, gone", [
    # Whisper parte la oracion en la duda: la siguiente arranca con la COLA ('y que... Y que')
    ("Aparte del buen precio de esta batidora y que... |1.2| Y que además no hace ruido, se las recomiendo.",
     "y que..."),
    # la cola tras la coma abre clausula y se reinicia con las mismas palabras ('no se... No se')
    ("Si quieren la receta del pan de muerto, con todo y el relleno de nata, no se... |1.5| "
     "No se la pierdan, la subo el viernes.", "no se..."),
])
@pytest.mark.parametrize("stretch", [False, True])
def test_oracion_partida_en_la_duda_solo_se_quita_la_cola(src, gone, stretch):
    """Una oracion con '...' cuya siguiente arranca con las mismas palabras de su cola no esta
    abandonada entera: lo que se abandona es la cola (antes se iba la oracion completa, con su
    informacion)."""
    w, r = run(src, stretch)
    on = [c for c in r["cuts"] if c["enabled"]]
    assert removed_text(w, on) == gone
    assert on[0]["kind"] in ("arranque_fallido", "retoma") and on[0]["level"] == "alta"
    assert_kept_safe(w, r["cuts"])


def test_oracion_larga_a_medias_sin_repetirse_queda_como_sugerencia():
    """Una oracion larga que se queda en '...' y NO se vuelve a decir lleva informacion: queda como
    sugerencia (nivel baja). Si se vuelve a decir despues, o la sigue una groseria, se corta."""
    src = ("Escríbanme abajo qué receta les gustaría que preparemos la próxima semana con todo "
           "detalle y paso a paso sobre estas... |1.5| Bueno, ahora vamos con el relleno de fresa.")
    w, r = run(src)
    assert len(r["cuts"]) == 1
    c = r["cuts"][0]
    assert (c["kind"], c["level"], c["enabled"]) == ("arranque_fallido", "baja", False)
    assert c["text"].startswith("Escríbanme abajo") and c["text"].endswith("sobre estas...")
    assert T.detect(w, w[-1]["end"] + 0.6, sensitivity="a_fondo")["cuts"][0]["enabled"]
    redicho = ("El toque de vainilla y canela de este postre tan rico y fácil de preparar en casa, "
               "pues, lo vuelve algo bastante... |1.5| La vainilla y la canela lo vuelven muy especial.")
    w, r = run(redicho)
    on = [c for c in r["cuts"] if c["enabled"]]
    assert on and removed_text(w, on).startswith("El toque de vainilla") and on[0]["level"] == "alta"
    groseria = ("Lo que mis tíos no sabían en esa época era que la receta original llevaba mucho "
                "más tiempo de horneado que esta versión, un poco se... |1.0| ¡Chin! |1.0| "
                "Ahora vamos con el relleno.")
    w, r = run(groseria)
    on = [c for c in r["cuts"] if c["enabled"]]
    assert on and removed_text(w, on).startswith("Lo que mis tíos no sabían")


@pytest.mark.parametrize("stretch", [False, True])
def test_retoma_no_cruza_a_la_oracion_que_se_queda(stretch):
    """'...la que, en mi opinión, conviene servir con las carnes. Ahora, la salsa roja, en mi
    opinión, es la más sabrosa de la mesa, o sea, sería la que conviene servir más...'. La primera
    oracion se retoma, pero ni 'en mi opinión' ni 'conviene servir' (a media oracion) hacen de la
    segunda oracion un intento fallido: su inicio ('Ahora, la salsa roja,') y su cuerpo se quedan."""
    src = ("Y ahora, la salsa roja, para mí, es la más picante de la mesa, la que, en mi opinión, "
           "conviene servir con, pues, las carnes asadas. |2.4| Ahora, la salsa roja, en mi opinión, "
           "es la más sabrosa de la mesa, o sea, sería la que |0.5| conviene servir más con los "
           "tacos de pescado, los mariscos y las tostadas de atún. |0.8| Luego vamos con la salsa verde.")
    w, r = run(src, stretch)
    on = [c for c in r["cuts"] if c["enabled"]]
    k2 = next(k for k in range(1, len(w)) if w[k]["text"] == "Ahora," and w[k - 1]["text"].endswith("."))
    kept = {k for k in range(len(w)) if not any(c["w0"] <= k <= c["w1"] for c in on)}
    assert set(range(k2, len(w))) <= kept, removed_text(w, on)
    assert removed_text(w, on).startswith("Y ahora, la salsa roja, para mí")
    assert_kept_safe(w, r["cuts"])


def test_anafora_con_el_primer_item_antes_de_la_toma_no_se_corta():
    """'es el primer horno que..., es el primer horno en el que..., es el primer horno en el
    cual...': cada item agrega algo; el segundo y el tercero comparten 6 palabras pero no es una
    retoma (el primer item, antes, completa la lista de tres)."""
    _, r = run("Este horno es especial, ya que, pues, es el primer horno que compro con mi sueldo, "
               "es el primer horno en el que horneo, es el primer horno en el cual, pues, tengo que "
               "usar dos charolas.")
    assert r["cuts"] == []


@pytest.mark.parametrize("src", [
    "Y el secreto de este pan es... |1.0| la mantequilla.",
    "Y el precio final es de... |1.0| 500 pesos.",
    "Lo que nadie te dice de los brackets es que... |1.0| duelen los primeros días.",
    "Y el ganador es... |1.2| ¡María!",
])
@pytest.mark.parametrize("stretch", [False, True])
def test_pausa_dramatica_con_puntos_suspensivos_no_se_corta(src, stretch):
    """Whisper escribe '...' en la pausa antes de un remate: la oracion siguiente la COMPLETA (en
    minuscula, con una cifra o con una exclamacion corta). Sin voz sin transcribir de por medio no
    es un arranque abandonado: queda como sugerencia y en ningun nivel salvo 'a fondo' se corta
    (antes se quitaba el planteamiento en alta, incluso en 'suave')."""
    w, _ = run(src, stretch)
    dur = w[-1]["end"] + 0.6
    for sens in ("suave", "normal"):
        r = T.detect(w, dur, sensitivity=sens)
        assert not [c for c in r["cuts"] if c["enabled"]], (sens, r["cuts"])
        assert [(c["kind"], c["level"]) for c in r["cuts"]] == [("arranque_fallido", "baja")]
        assert len(T.effective_words(w, r["cuts"])) == len(w)
    if stretch:
        return
    # con voz sin transcribir pegada a la oracion siguiente sigue siendo un reinicio tragado: alta
    k = next(k for k, x in enumerate(w) if x["text"].endswith("..."))
    gap = (w[k]["end"], w[k + 1]["start"])
    y = synth_audio(w, dur, extra=[(gap[0] + 0.15, gap[1] - 0.03)])
    c = T.detect(w, dur, audio=y)["cuts"][0]
    assert (c["kind"], c["level"], c["enabled"]) == ("arranque_fallido", "alta", True)


@pytest.mark.parametrize("src, kept", [
    ("Este café es de Chiapas y la... |0.8| Este café es de Chiapas y la verdad está buenísimo, "
     "no mames. |0.6| Ahora vamos a molerlo.", "Este café es de Chiapas y la verdad está buenísimo,"),
    ("El pan quedó bien esponjoso... |0.9| El pan quedó súper esponjoso, carajo. |0.6| Ahora lo cortamos.",
     "El pan quedó súper esponjoso,"),
    ("La salsa lleva tomate, cebolla y... |0.8| La salsa lleva tomate, cebolla, chile y ajo, qué rico, "
     "no mames. |0.6| Luego la licuamos.", "La salsa lleva tomate, cebolla, chile y ajo, qué rico,"),
])
def test_groseria_al_final_de_la_toma_buena_no_se_lleva_la_toma(src, kept):
    """La toma buena tras una retoma termina en una groseria: el corte de la groseria ya no se lleva
    la oracion entera (antes quedaba pegado a la retoma, en alta, y se perdian las dos tomas)."""
    w, _ = run(src)
    for sens in ("suave", "normal"):
        r = T.detect(w, w[-1]["end"] + 0.6, sensitivity=sens)
        on = [c for c in r["cuts"] if c["enabled"]]
        assert [c["kind"] for c in on] == ["retoma"], (sens, on)
        left = " ".join(x["text"] for x in T.effective_words(w, r["cuts"]))
        assert left.startswith(kept), left
        err = [c for c in r["cuts"] if c["kind"] == "error"]
        assert err and all(c["level"] == "baja" and not c["enabled"] for c in err)
        assert all(c["text"].split()[0] not in kept.split() for c in err)
        assert_kept_safe(w, r["cuts"])


@pytest.mark.parametrize("src", [
    "Es muy fácil de usar. Es muy fácil de limpiar.",
    "Agregas una taza de harina. Agregas una taza de azúcar.",
    "Este café tiene notas de chocolate. Este café tiene notas de caramelo.",
    "Lo puedes usar en la mañana, lo puedes usar en la noche.",
    "Mezcla por dos minutos. |1.0| Mezcla por cinco minutos más.",
])
@pytest.mark.parametrize("stretch", [False, True])
def test_paralelismo_de_dos_items_queda_como_sugerencia(src, stretch):
    """Dos oraciones paralelas que comparten el marco y cambian el ultimo dato no son una retoma:
    la primera dice algo que la segunda no (antes se cortaba en alta y se perdia 'harina')."""
    w, r = run(src, stretch)
    assert not [c for c in r["cuts"] if c["enabled"]], r["cuts"]
    assert all(c["level"] == "baja" for c in r["cuts"])
    assert len(T.effective_words(w, r["cuts"])) == len(w)


def test_cadena_de_intentos_con_la_misma_entrada_no_es_paralelismo():
    """Con un intento anterior con la misma entrada es una cadena de retomas: el penultimo intento
    (completo, con un dato distinto) se sigue cortando."""
    w, r = run("Hoy hacemos galletas. |1.0| Bueno, vamos a dejar a un lado, |1.2| bueno, vamos a dejar a "
               "un lado los postres, |1.5| bueno, vamos a dejar a un lado los pasteles de la tienda, ya "
               "que este video es de galletas caseras.")
    on = [c for c in r["cuts"] if c["enabled"]]
    assert removed_text(w, on) == "Bueno, vamos a dejar a un lado, bueno, vamos a dejar a un lado los postres,"
    assert all(c["level"] == "alta" for c in on)


def test_autocorreccion_numero_no_numero():
    w, r = run("El kit trae 19, no, 16 piezas de repuesto.")
    assert removed_text(w, r["cuts"]) == "19, no,"
    c = r["cuts"][0]
    assert c["kind"] == "autocorreccion" and c["enabled"]
    assert "«16 piezas de repuesto»" in c["reason"]
    assert_kept_safe(w, r["cuts"])


@pytest.mark.parametrize("src", [
    "Esta chamarra es muy, muy cómoda. |0.6| Es moderna, es versátil, es elegante.",
    "Es moderna, es versátil, es elegante y además es barata.",
    "Uno, dos, tres, así de sencillo. |0.6| No, no, no, así no se hace.",
    "Si quieres crecer, lee. |0.6| Si quieres crecer, escucha. |0.6| Si quieres crecer, actúa.",
    "¿Lo recomiendo? Sí, sí lo recomiendo. |0.6| Así es, así es.",
    "Poco a poco, día a día, paso a paso.",
])
@pytest.mark.parametrize("stretch", [False, True])
def test_enfasis_listas_y_anaforas_no_se_cortan(src, stretch):
    _, r = run(src, stretch)
    assert r["cuts"] == []


def test_groseria_aislada_queda_como_sugerencia_y_en_cadena_se_corta():
    w, r = run("Este pastel quedó delicioso, no mames. |0.9| Ahora vamos con el relleno de fresa.")
    assert len(r["cuts"]) == 1 and r["cuts"][0]["kind"] == "error"
    assert r["cuts"][0]["level"] == "baja" and not r["cuts"][0]["enabled"]
    w, r = run("Vamos a dejar el tema de, |0.9| vamos a dejar en, chin, |1.1| vamos a dejar el tema de "
               "los precios para el final.")
    on = [c for c in r["cuts"] if c["enabled"]]
    assert removed_text(w, on) == "Vamos a dejar el tema de, vamos a dejar en, chin,"


# ------------------------------------------------------------------ sensibilidad
SENS_SRC = ("El kit trae 19, no, 16 piezas. |0.7| Ponemos la la mezcla en el molde. |1.0| "
            "Lo más importante es la constancia en el entrenamiento diario. |2.0| La constancia en "
            "el entrenamiento diario es lo más importante.")


def test_niveles_de_sensibilidad():
    got = {}
    for s in ("suave", "normal", "a_fondo"):
        w, r = run(SENS_SRC, sensitivity=s)
        got[s] = {c["level"]: c["enabled"] for c in r["cuts"]}
        assert r["meta"]["sensitivity"] == s
    assert set(got["normal"]) == {"alta", "media", "baja"}
    assert got["suave"] == {"alta": True, "media": False, "baja": False}
    assert got["normal"] == {"alta": True, "media": True, "baja": False}
    assert got["a_fondo"] == {"alta": True, "media": True, "baja": True}
    # la reformulacion (baja) quita la oracion entera, nunca solo su cola ('es' colgando)
    baja = next(c for c in r["cuts"] if c["level"] == "baja")
    assert baja["text"] == "Lo más importante es la constancia en el entrenamiento diario."
    # alias y valores desconocidos
    assert T.detect(words_of(SENS_SRC), 20, sensitivity="agresivo")["meta"]["sensitivity"] == "a_fondo"
    assert T.detect(words_of(SENS_SRC), 20, sensitivity="???")["meta"]["sensitivity"] == "normal"
    # re-evaluar sin volver a detectar respeta lo que toco el usuario
    cuts = r["cuts"]
    cuts[0] = dict(cuts[0], enabled=False, user=True)
    re_ = T.apply_sensitivity(cuts, "suave")
    assert re_[0]["enabled"] is False
    assert {c["level"]: c["enabled"] for c in re_[1:]} == {"media": False, "baja": False}


# ------------------------------------------------------------------ bordes
def synth_audio(words, dur, real=None, extra=()):
    """Voz (tono modulado) SOLO donde se habla de verdad (`real`: {indice: (a, b)} para palabras
    cuyo tiempo de Whisper miente; `extra`: [(a, b)] voz que nadie transcribio); silencio con un
    poco de ruido en el resto."""
    rng = np.random.default_rng(3)
    y = rng.normal(0, 1e-4, int(dur * SR)).astype(np.float32)
    spans = [(real or {}).get(k, (w["start"], w["end"])) for k, w in enumerate(words)] + list(extra)
    for a, b in spans:
        if b - a < 0.01:
            continue
        i0, i1 = int(a * SR), int(b * SR)
        t = np.arange(i1 - i0) / SR
        y[i0:i1] += (0.3 * np.sin(2 * np.pi * 180 * t) * (0.7 + 0.3 * np.sin(2 * np.pi * 5 * t))).astype(np.float32)
    return y


def energy_at(y, t):
    k = int(t * SR)
    seg = y[max(0, k - 160):k + 160]
    return 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9)


def test_inicio_de_whisper_tardio_el_corte_va_al_hueco_real():
    """Whisper marca tarde el inicio de '40,' y de '50' (la voz empezo 0.25 s antes): el corte no
    se mete en la palabra (ni deja 'cuaren-' ni se come 'cin-'): va a los huecos reales."""
    w = words_of("Lo horneamos durante |0.4| 40, |0.5| no, |0.6| 50 minutos a fuego medio.")
    k40 = next(k for k, x in enumerate(w) if x["text"] == "40,")
    k50 = next(k for k, x in enumerate(w) if x["text"] == "50")
    real = {k40: (w[k40]["start"] - 0.25, w[k40]["end"]), k50: (w[k50]["start"] - 0.25, w[k50]["end"])}
    y = synth_audio(w, w[-1]["end"] + 1, real)
    c = T.detect(w, w[-1]["end"] + 1, audio=y)["cuts"][0]
    assert c["text"] == "40, no,"
    assert w[k40 - 1]["end"] <= c["start"] <= real[k40][0]                  # antes de la voz de '40'
    assert w[k50 - 1]["end"] <= c["end"] <= real[k50][0] - 0.02              # antes de la voz de '50'
    assert energy_at(y, c["start"]) < -60 and energy_at(y, c["end"]) < -60


def test_voz_sin_transcribir_tras_palabra_colgante_es_sugerencia():
    """Whisper se trago un intento repetido ('La [masa lleva harina de,] |pausa| La masa...'):
    queda 'La' + voz sin palabras + pausa. Con audio se sugiere quitarlo (nivel baja)."""
    w = words_of("Hoy hacemos pan de elote. |0.7| La |3.2| masa lleva harina de maíz y huevo.")
    kla = next(k for k, x in enumerate(w) if x["text"] == "La")
    km = kla + 1
    lost = (w[kla]["end"] + 0.05, w[kla]["end"] + 1.3)                     # 'masa lleva harina de,'
    real = {km: (w[km]["start"] - 0.3, w[km]["end"])}                      # el segundo 'La' pegado
    dur = w[-1]["end"] + 1
    y = synth_audio(w, dur, real, extra=[lost])
    assert T.detect(w, dur)["cuts"] == []                                  # sin audio no se ve
    for sens, on in (("normal", False), ("a_fondo", True)):
        cuts = T.detect(w, dur, audio=y, sensitivity=sens)["cuts"]
        assert len(cuts) == 1
        c = cuts[0]
        assert (c["w0"], c["w1"], c["kind"], c["level"], c["enabled"]) == (kla, kla, "arranque_fallido", "baja", on)
        assert c["start"] <= w[kla]["start"] and lost[1] <= c["end"] <= real[km][0]
        assert "revísala" in c["reason"]


def _retoma_con_voz_perdida(extra_of, largo=False):
    first = ("El secreto de esta salsa casera es el chile seco, que le da un..." if largo else
             "El secreto de esta salsa es el chile, que le da un...")
    w = words_of(f"Dejamos reposar la salsa. |0.7| {first} |3.4| chile, que le da un sabor ahumado.")
    k_un = next(k for k, x in enumerate(w) if x["text"] == "un...")
    k_ch = k_un + 1
    dur = w[-1]["end"] + 1
    extra = extra_of(w[k_un]["end"], w[k_ch]["start"])
    return w, k_un, k_ch, dur, extra, synth_audio(w, dur, extra=[extra])


def test_whisper_se_traga_el_arranque_de_la_toma_buena_el_corte_no_la_deja_a_medias():
    """Whisper escribe solo la cola de la toma buena ('[El secreto de esta salsa es el] chile, que le
    da un sabor'): la voz sin transcribir PEGADA a 'chile,' es su arranque y se queda; el corte de la
    toma mala termina en la pausa entre las dos tomas (antes: llegaba hasta 'chile,' y el video
    decia '...la salsa. chile, que le da un sabor'). Sin audio no hay prueba de ese reinicio: 'un...
    chile,' podria ser una pausa dramatica, asi que la oracion abandonada queda como sugerencia y
    solo se corta la cola que si se repite ('chile, que le da un...')."""
    w, k_un, k_ch, dur, lost, y = _retoma_con_voz_perdida(lambda a, b: (a + 1.0, b - 0.03))
    k_el = next(k for k, x in enumerate(w) if x["text"] == "El")
    cuts = T.detect(w, dur, audio=y)["cuts"]
    assert len(cuts) == 1
    c = cuts[0]
    assert (c["w0"], c["w1"], c["level"]) == (k_el, k_un, "alta") and c["enabled"]
    assert w[k_un]["end"] <= c["end"] <= lost[0] - 0.02                    # en la pausa, antes de la voz
    assert energy_at(y, c["end"]) < -60 and energy_at(y, c["start"]) < -60
    assert_kept_safe(w, cuts)
    sin = T.detect(w, dur)["cuts"]
    r3 = next(x for x in sin if x["w0"] == k_el)
    assert (r3["kind"], r3["level"], r3["enabled"]) == ("arranque_fallido", "baja", False)
    assert removed_text(w, [x for x in sin if x["enabled"]]) == "chile, que le da un..."
    assert all(x["end"] <= w[k_ch]["start"] for x in sin)                  # la toma buena queda entera
    assert_kept_safe(w, sin)


def test_oracion_larga_a_medias_cuyo_reinicio_se_trago_whisper_se_corta_entera():
    """Oracion larga (> 12 palabras) que queda en '...': sin audio no hay prueba de que se vuelva a
    decir (Whisper se trago el arranque de la toma buena) y solo se corta la cola que si se repite;
    con audio, la voz sin transcribir pegada a 'chile,' es esa repeticion: se corta la oracion
    entera y el corte termina en la pausa (la toma buena queda completa)."""
    w, k_un, k_ch, dur, lost, y = _retoma_con_voz_perdida(lambda a, b: (a + 1.0, b - 0.03), largo=True)
    k_el = next(k for k, x in enumerate(w) if x["text"] == "El")
    sin = [c for c in T.detect(w, dur)["cuts"] if c["enabled"]]
    assert removed_text(w, sin) == "chile seco, que le da un..."
    con = [c for c in T.detect(w, dur, audio=y)["cuts"] if c["enabled"]]
    assert len(con) == 1 and (con[0]["w0"], con[0]["w1"]) == (k_el, k_un)
    assert w[k_un]["end"] <= con[0]["end"] <= lost[0] - 0.02


def test_intento_que_whisper_se_trago_antes_de_una_pausa_se_va_con_el_corte():
    """Si tras la voz sin transcribir hay una pausa antes de la toma buena, esa voz es OTRO intento
    (Whisper se lo trago entero): se va con el corte, que llega hasta la pausa antes de 'chile,'."""
    w, k_un, k_ch, dur, extra, y = _retoma_con_voz_perdida(lambda a, b: (a + 0.9, b - 0.9))
    c = T.detect(w, dur, audio=y)["cuts"][0]
    assert c["w1"] == k_un and extra[1] <= c["end"] <= w[k_ch]["start"]
    assert energy_at(y, c["end"]) < -60
    assert_kept_safe(w, [c])


def test_bordes_con_aire_y_en_el_silencio():
    w = words_of("Hola a todos. |0.6| Agenda tu cita al, |0.9| agenda tu cita al número que aparece en pantalla.")
    y = synth_audio(w, w[-1]["end"] + 1)
    c = T.detect(w, w[-1]["end"] + 1, audio=y)["cuts"][0]
    k_first = next(k for k, x in enumerate(w) if x["text"] == "Agenda")
    k_kept = next(k for k, x in enumerate(w) if x["text"] == "agenda")
    assert (c["w0"], c["w1"]) == (k_first, k_kept - 1)
    # el corte arranca en el silencio antes de 'Agenda' (sin comerse 'todos.') y termina en el
    # silencio antes de 'agenda', con aire antes de la toma buena
    assert w[k_first - 1]["end"] <= c["start"] <= w[k_first]["start"]
    assert w[k_kept]["start"] - T.SNAP_WIN - 0.01 <= c["end"] <= w[k_kept]["start"]
    assert energy_at(y, c["start"]) < -60 and energy_at(y, c["end"]) < -60
    assert_kept_safe(w, [c])


def test_bordes_con_palabras_estiradas_y_de_duracion_cero():
    w = words_of("Hola a todos. |0.6| Agenda tu cita al, |0.9| agenda tu cita al número que aparece en pantalla.")
    k_first = next(k for k, x in enumerate(w) if x["text"] == "Agenda")
    k_kept = next(k for k, x in enumerate(w) if x["text"] == "agenda")
    real = {k_first - 1: (w[k_first - 1]["start"], w[k_first - 1]["end"]),
            k_kept: (w[k_kept]["start"], w[k_kept]["end"])}
    w2 = [dict(x) for x in w]
    # 'todos.' estirada 2.5 s por Whisper sobre la pausa (su final queda pegado a 'Agenda')
    w2[k_first - 1]["start"] = round(w2[k_first - 1]["end"] - 2.5, 3)
    # la primera palabra de la toma buena, de duracion 0 (timing crudo)
    w2[k_kept]["end"] = w2[k_kept]["start"]
    # la ultima palabra quitada estirada sobre la pausa hasta la toma buena
    w2[k_kept - 1]["end"] = w2[k_kept]["start"]
    y = synth_audio(w, w[-1]["end"] + 1, real)
    for audio in (None, y):
        r = T.detect(w2, w[-1]["end"] + 1, audio=audio)
        on = [c for c in r["cuts"] if c["enabled"]]
        assert removed_text(w2, on) == "Agenda tu cita al,"
        assert_kept_safe(w2, r["cuts"])
        # la linea de tiempo del render conserva todas las palabras que se quedan
        tl = Timeline(keeps_from_cuts(on, w[-1]["end"] + 1), w[-1]["end"] + 1, fps=30, min_frag=0.15)
        for k, x in enumerate(w2):
            if k in removed_idx(on):
                continue
            assert tl.remap(clamp_mid(x)) is not None, x


def test_un_corte_de_tomas_no_deja_una_palabra_en_un_fragmento_menor_a_min_frag():
    w = words_of("Agenda tu cita al, |0.9| agenda tu cita. |1.5| Gracias.")
    k_kept = next(k for k, x in enumerate(w) if x["text"] == "agenda")
    dur = w[-1]["end"] + 1
    # silencio (de edit.keep_segments) que empieza a 0.13 s del inicio de la toma buena: con el
    # corte de tomas terminando GAP_PAD_IN antes, quedaria un fragmento < MIN_FRAG
    kw = w[k_kept]
    sil = [{"start": round(kw["start"] + 0.05, 3), "end": round(kw["start"] + 0.06, 3), "enabled": True}]
    w2 = [dict(x) for x in w]
    w2[k_kept]["end"] = round(kw["start"] + 0.05, 3)            # palabra corta ('ag-') antes del silencio
    base = T.detect(w2, dur)["cuts"]
    guarded = T.detect(w2, dur, existing_cuts=sil)["cuts"]
    assert base and guarded
    on = [c for c in guarded if c["enabled"]] + sil
    tl = Timeline(keeps_from_cuts(on, dur), dur, fps=30, min_frag=0.15)
    assert tl.remap((w2[k_kept]["start"] + w2[k_kept]["end"]) / 2) is not None
    assert guarded[0]["end"] < base[0]["end"]                    # el corte de tomas cedio espacio


def test_corte_apagado_por_seguridad_no_lo_enciende_un_nivel():
    """El tartamudeo 'la la' junto a un silencio dejaria la 'la' que se queda en un fragmento
    < MIN_FRAG: detect lo apaga y lo marca guard. Ni apply_sensitivity ('a fondo') ni una nueva
    corrida lo vuelven a encender (antes el nivel lo prendia y la palabra se perdia en el export)."""
    w = words_of("Ponemos la la mezcla en el molde y lo metemos al horno.")
    dur = w[-1]["end"] + 1
    w[2]["start"] = w[1]["end"]
    w[2]["end"] = round(w[2]["start"] + 0.13, 3)
    w[3]["start"] = round(w[2]["end"] + 0.3, 3)
    sil = [{"start": w[2]["end"], "end": round(w[3]["start"] - 0.01, 3), "enabled": True}]
    assert T.detect(w, dur)["cuts"][0]["enabled"] and "guard" not in T.detect(w, dur)["cuts"][0]
    for sens in ("normal", "a_fondo"):
        c = T.detect(w, dur, existing_cuts=sil, sensitivity=sens)["cuts"][0]
        assert (c["kind"], c["enabled"], c.get("guard")) == ("tartamudeo", False, True)
    cuts = T.merge(sil, T.detect(w, dur, existing_cuts=sil)["cuts"])
    for sens in ("suave", "normal", "a_fondo"):
        tk = [c for c in T.apply_sensitivity(cuts, sens) if c.get("src") == "takes"]
        assert tk and not any(c["enabled"] for c in tk)
    # el usuario si puede encenderlo (user: true manda)
    mine = [dict(c, enabled=True, user=True) if c.get("src") == "takes" else c for c in cuts]
    assert [c["enabled"] for c in T.apply_sensitivity(mine, "suave") if c.get("src") == "takes"] == [True]


def test_merge_conserva_los_bordes_que_movio_el_usuario():
    """Mover el borde de una toma (edges: true) la protege de la re-deteccion como un toggle."""
    w, r = run("Agenda tu cita al, |0.9| agenda tu cita al número que aparece en pantalla.")
    det = r["cuts"][0]
    moved = dict(det, end=round(det["end"] - 0.3, 3), edges=True)
    m = T.merge([moved], r["cuts"])
    tk = [c for c in m if c.get("src") == "takes"]
    assert len(tk) == 1 and tk[0]["end"] == moved["end"] and tk[0]["edges"] is True
    assert T.merge(m, r["cuts"]) == m


# ------------------------------------------------------------------ merge / stats / palabras
def test_merge_idempotente_y_respeta_lo_del_usuario():
    w, r = run(SENS_SRC)
    new = r["cuts"]
    sil = {"start": 0.0, "end": 0.4, "enabled": True}
    manual = {"start": 30.0, "end": 31.0, "enabled": True, "manual": True}
    ai = {"start": 40.0, "end": 41.0, "enabled": True, "src": "ai", "id": "cut_ai_1"}
    user = dict(new[0], enabled=False, user=True)                 # el usuario restauro esta toma
    stale = {"id": "tk_viejo", "start": 50.0, "end": 51.0, "enabled": True, "src": "takes",
             "kind": "retoma", "level": "alta"}
    old = [sil, manual, ai, user, stale]
    m1 = T.merge(old, new)
    assert m1[:3] == [sil, manual, ai]                            # silencios, manuales, 'ai': intactos
    assert user in m1 and not any(c.get("id") == "tk_viejo" for c in m1)
    ids = [c["id"] for c in m1 if c.get("src") == "takes"]
    assert len(ids) == len(set(ids)) == len(new)                  # la del usuario no se duplica
    assert next(c for c in m1 if c.get("id") == new[0]["id"])["enabled"] is False
    assert T.merge(m1, new) == m1                                 # idempotente
    assert T.merge(T.merge(m1, new), new) == m1
    assert T.merge(None, None) == [] and T.merge([sil], []) == [sil]
    st = T.stats(m1)
    assert st["n"] == len(new) and st["suggested"] == sum(1 for c in m1 if c.get("src") == "takes"
                                                          and not c["enabled"] and not c.get("user"))


def test_palabras_efectivas():
    w = words_of("Agenda tu cita al, |0.9| agenda tu cita al número. |1.0| Gracias por vernos.")
    cuts = T.detect(w, 20)["cuts"]
    k = len(w) - 3
    stretched = dict(w[k], start=w[k]["start"] - 1.5)             # 'Gracias' estirada sobre la pausa
    ws = w[:k] + [stretched] + w[k + 1:]
    sil = {"start": stretched["start"] + 0.1, "end": stretched["end"] - 0.3, "enabled": True}
    zero = {"text": "eh", "start": sil["start"] + 0.2, "end": sil["start"] + 0.2}
    eff = T.effective_words(ws + [zero], cuts + [sil])
    txt = " ".join(x["text"] for x in eff)
    assert txt == "agenda tu cita al número. Gracias por vernos."   # sin la toma, sin el 'eh' en el silencio
    assert T.effective_words(ws, [dict(c, enabled=False) for c in cuts]) == ws
    assert [x["text"] for x in T.plan_words({"phrases": [[{"text": "a", "start": 0, "end": 1}], "x"]})] == ["a"]


# ------------------------------------------------------------------ tema con el LLM (simulado)
LONG = ("Hola, bienvenidos a la cocina. Hoy preparamos pan de elote casero. Primero mezclamos la "
        "harina con el elote molido. Después agregamos la mantequilla derretida. Horneamos cuarenta "
        "minutos a fuego medio. Al final lo dejamos enfriar y listo.")


def test_tema_con_llm_simulado(monkeypatch):
    from motion import director as D
    w = words_of(LONG)
    seen = {}

    def fake_chat(msgs, fmt=None, num_ctx=None, temperature=None, num_predict=None, timeout=None, **kw):
        seen.update(msgs=msgs, fmt=fmt, num_ctx=num_ctx, temperature=temperature, timeout=timeout)
        return ('{"tema": "Pan de elote casero.", "resumen": "Receta paso a paso.", "secciones": '
                '[{"start_word": 0, "titulo": "Bienvenida"}, {"start_word": 11, "titulo": "Mezcla"}, '
                '{"start_word": 12, "titulo": "pegada"}, {"start_word": 999, "titulo": "fuera"}, '
                '{"start_word": 5, "titulo": "atras"}]}')
    monkeypatch.setattr(D, "llm_available", lambda *a, **k: True)
    monkeypatch.setattr(D, "_ollama_chat", fake_chat)
    tp = T.topic(w)
    assert tp == {"topic": "Pan de elote casero", "summary": "Receta paso a paso.",
                  "sections": [{"start": round(w[0]["start"], 2), "title": "Bienvenida"},
                               {"start": round(w[11]["start"], 2), "title": "Mezcla"}]}
    assert seen["num_ctx"] == 4096 and seen["temperature"] == 0.0 and seen["timeout"] <= T.TOPIC_TIMEOUT
    assert seen["fmt"]["required"] == ["tema", "resumen", "secciones"]
    user = seen["msgs"][1]["content"]
    assert "[0] Hola, bienvenidos a la cocina." in user and "[11]" in user   # inicio de cada oracion


def test_tema_sin_llm_o_con_timeout_no_bloquea(monkeypatch):
    from motion import director as D
    w = words_of(LONG)
    monkeypatch.setattr(D, "llm_available", lambda *a, **k: False)
    assert T.topic(w) is None
    monkeypatch.setattr(D, "llm_available", lambda *a, **k: True)

    def boom(*a, **k):
        raise TimeoutError("timed out")
    monkeypatch.setattr(D, "_ollama_chat", boom)
    assert T.topic(w) is None

    def slow(*a, **k):
        time.sleep(3.0)
        return '{"tema": "tarde", "resumen": "", "secciones": []}'
    monkeypatch.setattr(D, "_ollama_chat", slow)
    t0 = time.time()
    assert T.topic(w, timeout=2.2) is None
    assert time.time() - t0 < 3.0                                  # tope duro (hilo), no espera al LLM
    monkeypatch.setattr(D, "_ollama_chat", lambda *a, **k: "no es json")
    assert T.topic(w) is None
    monkeypatch.setattr(D, "_ollama_chat", lambda *a, **k: '{"tema": "", "resumen": "x", "secciones": []}')
    assert T.topic(w) is None
    assert T.topic(w[:3]) is None                                  # muy poco texto: ni se pregunta
