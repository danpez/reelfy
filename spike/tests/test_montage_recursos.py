"""Recursos del guion en "Armar con IA" (scripts/montage.py): marcas al final de una linea
("... - Agregar video_horno", "[foto_local]"), archivos que son recurso y nunca toma, y su lugar en
la linea de tiempo (plan["montage"]["inserts"]). Solo sesiones SINTETICAS inventadas (repo
publico): la historia de prueba es una panaderia de barrio."""
import copy

import pytest

import montage as M
from test_montage import chosen, mk, tl_words

G2 = ("Cada mañana prendemos el horno a las cinco.\n"
      "A las siete ya huele a pan en toda la calle.")


def two_lines(tmp, second="A las siete ya huele a pan en toda la calle."):
    return [mk(tmp, "p1", "Cada mañana prendemos el horno a las cinco.", 0, setup=0),
            mk(tmp, "p2", second, 1, setup=1)]


def item_of(r, sid):
    return next(x for x in r["timeline"] if x["sid"] == sid)


def tl_time(r, clips, sid, word_idx, edge):
    """Tiempo en la LINEA DE TIEMPO del inicio/fin de una palabra del clip `sid`."""
    x = item_of(r, sid)
    w = next(d for d in clips if d["sid"] == sid)["words"][word_idx]
    return x["start"] + (w[edge] - x["in"])


# ------------------------------------------------------------------ marcas en el guion
@pytest.mark.parametrize("line, text, names", [
    ("Nos pasó esto. - Agregar video_marcas", "Nos pasó esto.", ["video_marcas"]),
    ("Nos pasó esto. – añadir video_marcas", "Nos pasó esto.", ["video_marcas"]),
    ("Nos pasó esto. — AÑADIR video_marcas", "Nos pasó esto.", ["video_marcas"]),
    ("Nos pasó esto. | Insertar video_marcas.", "Nos pasó esto.", ["video_marcas"]),
    ("Nos pasó esto.- Mostrar video_marcas", "Nos pasó esto.", ["video_marcas"]),
    ("Nos pasó esto. - poner foto_1, video_2", "Nos pasó esto.", ["foto_1", "video_2"]),
    ("Nos pasó esto. - add el video_marcas", "Nos pasó esto.", ["video_marcas"]),
    ("Nos pasó esto. - insert \"Video Marcas.mp4\"", "Nos pasó esto.", ["Video Marcas.mp4"]),
    ("Nos pasó esto. - Agregar foto_1 y foto_2", "Nos pasó esto.", ["foto_1", "foto_2"]),
    ("Nos pasó esto. [Agregar video_marcas]", "Nos pasó esto.", ["video_marcas"]),
    ("Nos pasó esto. [video_marcas]", "Nos pasó esto.", ["video_marcas"]),
    ("Nos pasó esto. {marcas}", "Nos pasó esto.", ["marcas"]),
    ("Nos pasó esto. [foto_a] [foto_b]", "Nos pasó esto.", ["foto_a", "foto_b"]),
    ("Nos pasó esto. - Agregar foto_a - Mostrar foto_b", "Nos pasó esto.", ["foto_a", "foto_b"]),
    # no son marcas: guion dentro de una palabra, algo que no es verbo, acotacion sin archivo
    ("Mándanos un e-mail agregar todo", "Mándanos un e-mail agregar todo", []),
    ("Somos tres - y nos va bien", "Somos tres - y nos va bien", []),
    ("Respira hondo. [pausa]", "Respira hondo.", []),
    ("Hola a todos", "Hola a todos", []),
])
def test_marcas_de_recursos_al_final_de_la_linea(line, text, names):
    assert M.split_annotation(line) == (text, names)


def test_corchete_sin_verbo_vale_si_el_archivo_existe():
    known = [M.asset_key("logo.png")]
    assert M.split_annotation("Somos Panadería Luna. [logo]", known) == ("Somos Panadería Luna.", ["logo"])
    assert M.split_annotation("Somos Panadería Luna. [logo]") == ("Somos Panadería Luna.", [])


def test_parse_script_quita_marcas_y_las_guarda_por_linea():
    g = ("Hace tres años abrí una panadería. - Agregar foto_local\n"
         "- Mostrar video_horno\n"
         "\n"
         "Visítanos este sábado. [mapa_tienda]")
    p = M.parse_script(g)
    assert [(L["id"], L["text"], L["inserts"]) for L in p["lines"]] == [
        ("L01", "Hace tres años abrí una panadería.", ["foto_local", "video_horno"]),   # la linea sola se suma
        ("L02", "Visítanos este sábado.", ["mapa_tienda"])]
    assert p["sections"] == [{"id": 1, "lines": ["L01"]}, {"id": 2, "lines": ["L02"]}]
    # un parrafo largo partido en oraciones: el recurso va con la ultima
    largo = " ".join(["Amasamos con calma."] * 15) + " Y horneamos. - Agregar video_masa"
    q = M.parse_script(largo)
    assert len(q["lines"]) > 1 and q["lines"][-1]["inserts"] == ["video_masa"]
    assert all(L["inserts"] == [] for L in q["lines"][:-1])
    assert all("Agregar" not in L["text"] for L in q["lines"])
    # guion detectado: sin marcas (el texto tal cual)
    d = M.parse_script("Somos tres - pon atención", annotations=False)
    assert d["lines"][0] == {"id": "L01", "text": "Somos tres - pon atención", "inserts": []}


def test_nombres_normalizados_y_emparejados_con_archivos():
    assert M.asset_key("Video Panadero.MP4") == "video_panadero"
    assert M.asset_key("vídeo--Panadero__final.mov") == "video_panadero_final"
    assert M.asset_key(" Imagen Pan-Dulce.JPG ") == "imagen_pan_dulce"
    files = [{"sid": "s1", "name": "video_horno.mp4"}, {"sid": "s2", "name": "video_horno_lento.mp4"},
             {"sid": "s3", "name": "Foto Local Final.jpg"}, {"sid": "s4", "name": "logo.png"},
             {"sid": "mapa_tienda", "name": "IMG_9.PNG"}]
    assert M.match_asset("video_horno", files)["sid"] == "s1"          # igual gana al prefijo
    assert M.match_asset("Video-Horno-Lento", files)["sid"] == "s2"
    assert M.match_asset("foto_local", files)["sid"] == "s3"           # prefijo unico
    assert M.match_asset("video", files) is None                       # prefijo ambiguo
    assert M.match_asset("logo_nuevo", files)["sid"] == "s4"           # el archivo es el principio
    assert M.match_asset("mapa_tienda", files)["sid"] == "mapa_tienda"  # por sid
    assert M.match_asset("receta", files) is None
    assert M.asset_kind({"name": "a.JPG"}) == "image" and M.asset_kind({"name": "a.mov"}) == "video"
    assert M.asset_kind({"name": "x", "kind": "image"}) == "image"
    assert M.asset_kind({"name": "x", "dur": 3.0}) == "video" and M.asset_kind({"name": "x.pdf"}) is None


def test_clasificar_archivos_imagenes_y_pedidos_son_recursos():
    files = [{"sid": "c1", "name": "IMG_1.MOV"}, {"sid": "c2", "name": "IMG_2.MOV"},
             {"sid": "v1", "name": "video_horno.mp4"}, {"sid": "i1", "name": "portada.png"}]
    g = "Prendemos el horno. - Agregar video_horno, foto_que_no_esta"
    r = M.classify(files, g)
    assert [f["sid"] for f in r["clips"]] == ["c1", "c2"]
    assert [f["sid"] for f in r["assets"]] == ["v1", "i1"]            # la imagen aunque nadie la pida
    assert r["refs"] == {"video_horno": "v1", "foto_que_no_esta": None}
    assert M.script_refs(g) == ["video_horno", "foto_que_no_esta"]
    assert [f["sid"] for f in M.classify(files, None)["clips"]] == ["c1", "c2", "v1"]


def test_glosario_ignora_las_marcas():
    g = "Abrimos en Monterrey con Tienda Luna. - Agregar Video_Tienda"
    terms = M.glossary_terms(g)
    assert "Monterrey" in terms and "Tienda Luna" in terms
    assert not any("Agregar" in t or "Video" in t for t in terms)


# ------------------------------------------------------------------ build: recursos nunca son tomas
def test_las_marcas_no_cambian_la_eleccion_de_tomas(tmp_path):
    clips = two_lines(tmp_path)
    base = M.build(copy.deepcopy(clips), G2)
    g = G2.replace("las cinco.", "las cinco. - Agregar foto_horno").replace("la calle.", "la calle. [video_calle]")
    r = M.build(copy.deepcopy(clips), g, assets=[{"sid": "a1", "name": "foto_horno.jpg"}])
    strip = lambda tl: [(x["sid"], x["in"], x["out"], x["join"]) for x in tl]   # noqa: E731
    assert strip(r["timeline"]) == strip(base["timeline"])
    assert r["stats"]["coverage"] == 1.0 and r["script"]["text"] == g          # el texto tal cual
    assert [L["text"] for L in r["script"]["lines"]] == [L["text"] for L in base["script"]["lines"]]
    assert [L["inserts"] for L in r["script"]["lines"]] == [["foto_horno"], ["video_calle"]]


def test_video_pedido_por_el_guion_nunca_es_toma_aunque_diga_la_linea(tmp_path):
    """Un video que el guion pide como recurso (aunque traiga voz que dice la linea, y sea la toma
    mas reciente) nunca entra como toma; sin la marca si entraria."""
    clips = two_lines(tmp_path)
    rec = mk(tmp_path, "vr", "A las siete ya huele a pan en toda la calle.", 5, setup=2, name="video_calle.mp4")
    r0 = M.build(copy.deepcopy(clips + [rec]), G2)
    assert chosen(r0, "L02") == ["vr"]                        # sin marca: es la toma mas reciente
    g = G2 + " - Agregar video_calle"
    r = M.build(copy.deepcopy(clips + [rec]), g)
    assert chosen(r, "L02") == ["p2"] and "vr" not in [o["sid"] for o in r["order"]]
    assert all(c["sid"] != "vr" for s in r["slots"] for c in s["cands"])
    assert all(d["sid"] != "vr" for d in r["discarded"])
    ins = r["inserts"]
    assert len(ins) == 1 and ins[0]["sid"] == "vr" and ins[0]["kind"] == "video" and ins[0]["status"] == "ok"
    assert r["stats"]["assets"] == ["vr"]


def test_imagen_entre_los_clips_nunca_es_toma(tmp_path):
    clips = two_lines(tmp_path) + [{"sid": "img", "name": "portada.JPG", "words": [], "dur": None}]
    r = M.build(clips, G2)
    assert [o["sid"] for o in r["order"]] == ["p1", "p2"] and r["stats"]["assets"] == ["img"]
    assert r["inserts"] == []                               # nadie la pidio


# ------------------------------------------------------------------ build: lugar en la linea de tiempo
def test_recurso_mientras_se_dice_su_linea(tmp_path):
    clips = two_lines(tmp_path)
    g = G2 + " - Agregar foto_calle"
    r = M.build(clips, g, assets=[{"sid": "f1", "name": "Foto Calle.jpg"}])
    (x,) = r["inserts"]
    assert set(x) == {"id", "line", "name", "sid", "kind", "start", "end", "fit", "speed", "src_in", "status"}
    assert x["id"] == "i01" and x["line"] == "L02" and x["name"] == "foto_calle"
    assert (x["sid"], x["kind"], x["fit"], x["speed"], x["src_in"], x["status"]) == ("f1", "image", "hold", 1.0, 0.0, "ok")
    # de la primera palabra de la linea - 0.15 s a la ultima + 0.25 s, en tiempo de la LINEA DE TIEMPO
    assert x["start"] == pytest.approx(tl_time(r, clips, "p2", 0, "start") - 0.15, abs=2e-3)
    assert x["end"] == pytest.approx(tl_time(r, clips, "p2", -1, "end") + 0.25, abs=2e-3)
    assert x["start"] > item_of(r, "p2")["start"]
    # el candidato trae los tiempos de cada linea en su fuente
    cj = next(c for s in r["slots"] for c in s["cands"] if c["id"] == item_of(r, "p2")["cand"])
    w = clips[1]["words"]
    assert cj["line_times"] == {"L02": [w[0]["start"], w[-1]["end"]]}


def test_recurso_de_una_linea_partida_entre_dos_tomas(tmp_path):
    """La linea se arma con dos tomas (empalme): el recurso va de la primera palabra de la primera
    parte a la ultima de la segunda."""
    g = "Cada mañana prendemos el horno a las cinco, y a las siete ya huele a pan en toda la calle. [video_horno]"
    a = mk(tmp_path, "p1", "Cada mañana prendemos el horno a las cinco, |0.8| y a las siete ya huele", 0, setup=0)
    b = mk(tmp_path, "p2", "y a las siete ya huele a pan en toda la calle.", 1, setup=0)
    clips = [a, b]
    r = M.build(clips, g, assets=[{"sid": "v", "name": "video_horno.mp4", "dur": 60.0}])
    assert chosen(r, "L01") == ["p1", "p2"]
    (x,) = r["inserts"]
    assert x["start"] == pytest.approx(tl_time(r, clips, "p1", 0, "start") - 0.15, abs=2e-3)
    assert x["end"] == pytest.approx(tl_time(r, clips, "p2", -1, "end") + 0.25, abs=2e-3)
    assert x["fit"] == "trim" and x["speed"] == 1.0 and x["src_in"] == 0.0     # 60 s: se recorta


def test_recurso_minimo_2_s_y_nunca_encima_del_siguiente(tmp_path):
    g = "Visítanos. [foto_a]\nTe esperamos. [foto_b]\nCada mañana prendemos el horno a las cinco."
    clips = [mk(tmp_path, "v1", "Visítanos.", 0, setup=0), mk(tmp_path, "v2", "Te esperamos.", 1, setup=0),
             mk(tmp_path, "v3", "Cada mañana prendemos el horno a las cinco.", 2, setup=0)]
    assets = [{"sid": "fa", "name": "foto_a.jpg"}, {"sid": "fb", "name": "foto_b.jpg"}]
    r = M.build(clips, g, assets=assets)
    a, b = r["inserts"]
    assert (a["line"], b["line"]) == ("L01", "L02")
    assert a["end"] == pytest.approx(b["start"], abs=1e-3)               # recortado al siguiente
    assert a["end"] - a["start"] < 2.0
    assert b["end"] - b["start"] == pytest.approx(2.0, abs=1e-3)         # linea corta: dura 2 s
    assert b["start"] == pytest.approx(tl_time(r, clips, "v2", 0, "start") - 0.15, abs=2e-3)


def test_varios_recursos_en_una_linea_se_reparten(tmp_path):
    g = ("Cada mañana prendemos el horno a las cinco y a las siete ya huele a pan recién hecho en toda "
         "la calle del barrio. - Agregar foto_a, foto_b")
    src = ("Cada mañana prendemos el horno a las cinco y a las siete ya huele a pan recién hecho en toda "
           "la calle del barrio.")
    clips = [mk(tmp_path, "l1", src, 0, setup=0)]
    r = M.build(clips, g, assets=[{"sid": "fa", "name": "foto_a.jpg"}, {"sid": "fb", "name": "foto_b.png"}])
    a, b = r["inserts"]
    assert (a["name"], b["name"], a["line"], b["line"]) == ("foto_a", "foto_b", "L01", "L01")
    assert a["end"] == pytest.approx(b["start"], abs=1e-3)
    assert a["end"] - a["start"] == pytest.approx(b["end"] - b["start"], abs=2e-3) and a["end"] - a["start"] >= 2.0


@pytest.mark.parametrize("ratio, fit, speed_ok, shorter", [
    (1.3, "speed", lambda s: s == pytest.approx(1.3, abs=2e-3), False),   # hasta 1.4x: se acelera
    (2.0, "trim", lambda s: s == 1.0, False),                              # mas largo: se recorta
    (0.9, "speed", lambda s: s == pytest.approx(0.9, abs=2e-3), False),   # poco mas corto: se alenta
    (0.5, "trim", lambda s: s == 1.0, True),                               # mucho mas corto: dura lo suyo
])
def test_video_se_ajusta_al_hueco(tmp_path, ratio, fit, speed_ok, shorter):
    clips = two_lines(tmp_path)
    r0 = M.build(copy.deepcopy(clips), G2 + " [video_calle]")
    slot = r0["inserts"][0]["end"] - r0["inserts"][0]["start"]
    r = M.build(copy.deepcopy(clips), G2 + " [video_calle]",
                assets=[{"sid": "v", "name": "video_calle.mov", "dur": round(slot * ratio, 3)}])
    (x,) = r["inserts"]
    assert x["kind"] == "video" and x["fit"] == fit and speed_ok(x["speed"]) and x["src_in"] == 0.0
    if shorter:
        assert x["end"] - x["start"] == pytest.approx(slot * ratio, abs=2e-3)
    else:
        assert x["end"] - x["start"] == pytest.approx(slot, abs=2e-3)


def test_recurso_que_falta_y_luego_se_sube(tmp_path):
    clips = two_lines(tmp_path)
    r = M.build(clips, G2 + " - Agregar video_calle")
    (x,) = r["inserts"]
    assert (x["status"], x["sid"], x["kind"]) == ("missing", None, None)
    assert x["end"] > x["start"] and r["stats"]["inserts"] == {"ok": 0, "missing": 1, "unplaced": []}
    # el usuario lo sube en el panel: se recalcula con el plan y el archivo nuevo
    up = M.plan_inserts(r, [{"sid": "nuevo", "name": "video_calle.mp4", "dur": 2.5}])
    assert up[0]["status"] == "ok" and up[0]["sid"] == "nuevo" and up[0]["start"] == x["start"]
    # un archivo que no es imagen ni video no sirve
    assert M.plan_inserts(r, [{"sid": "d", "name": "video_calle.pdf"}])[0]["status"] == "missing"


def test_linea_sin_toma_lista_su_recurso_sin_lugar(tmp_path):
    """Una linea sin toma ("Sin toma") no ubica su recurso, pero el recurso SI queda en
    plan.montage.inserts sin lugar (start/end None, con su archivo si existe): el panel lo lista y
    el ensamble lo coloca si el usuario le da una toma a esa linea."""
    g = G2 + "\nY los domingos abrimos hasta las tres. [foto_domingo]\nY cerramos el lunes. {cartel_lunes}"
    r = M.build(two_lines(tmp_path), g, assets=[{"sid": "d", "name": "foto_domingo.jpg"}])
    assert r["uncovered"] == ["L03", "L04"]
    assert [(x["line"], x["name"], x["sid"], x["kind"], x["start"], x["end"], x["status"]) for x in r["inserts"]] == [
        ("L03", "foto_domingo", "d", "image", None, None, "ok"),
        ("L04", "cartel_lunes", None, None, None, None, "missing")]
    assert len({x["id"] for x in r["inserts"]}) == 2
    assert r["stats"]["inserts"]["unplaced"] == ["foto_domingo", "cartel_lunes"]
    # recalcular (subir el archivo que faltaba) no los coloca mientras su linea siga sin toma
    up = M.plan_inserts(r, [{"sid": "d", "name": "foto_domingo.jpg"}, {"sid": "c", "name": "cartel_lunes.png"}])
    assert [(x["sid"], x["start"], x["status"]) for x in up] == [("d", None, "ok"), ("c", None, "ok")]


def test_recurso_sigue_a_la_toma_cambiada(tmp_path):
    """El usuario cambia la toma de la linea ("Usar esta toma"): el recurso se recalcula con los
    tiempos de la nueva (line_times del candidato), sin volver a armar."""
    g = G2 + " [foto_calle]"
    clips = two_lines(tmp_path) + [
        mk(tmp_path, "p3", "Mmm. |1.5| A las siete ya huele a pan en toda la calle.", 0, 30, setup=1)]
    r = M.build(clips, g, assets=[{"sid": "f", "name": "foto_calle.jpg"}])
    slot = next(s for s in r["slots"] if "L02" in s["lines"])
    alt = next(c for c in slot["cands"][1:] if c["sid"] != slot["cands"][0]["sid"])
    x = next(t for t in r["timeline"] if t["slot"] == slot["id"])
    x.update({"cand": alt["id"], "sid": alt["sid"], "in": alt["in"], "out": alt["out"]})
    (ins,) = M.plan_inserts(r, [{"sid": "f", "name": "foto_calle.jpg"}])
    w = next(d for d in clips if d["sid"] == alt["sid"])["words"]
    first = next(k for k, v in enumerate(w) if v["text"] == "A")
    assert ins["start"] == pytest.approx(x["start"] + w[first]["start"] - alt["in"] - 0.15, abs=2e-3)
    assert ins["end"] == pytest.approx(x["start"] + w[-1]["end"] - alt["in"] + 0.25, abs=2e-3)


def test_sin_guion_no_hay_recursos(tmp_path):
    r = M.build(two_lines(tmp_path), None, assets=[{"sid": "f", "name": "foto.jpg"}])
    assert r["script"]["source"] == "detected" and r["inserts"] == []
    assert all(L["inserts"] == [] for L in r["script"]["lines"])


# ------------------------------------------------------------------ frases con guion largo (revision 2)
@pytest.mark.parametrize("line", [
    "Nuestra meta — agregar valor a cada familia del barrio.",
    "Súmate — agrega tu negocio hoy.",
    "Tenemos un reto - mostrar resultados reales.",
    "Hay una regla | pon tu marca primero.",
    "Te damos una muestra - muestra gratis para ti.",
    "Nuestro objetivo es claro - mostrar resultados reales.",
    "La clave — poner al cliente primero",
    "Súmate — agrega 3 negocios hoy",
    "Es fácil – añade tu foto y listo",
])
def test_frase_con_guion_largo_y_verbo_no_es_marca(line):
    """Una frase normal con guion largo seguido de agregar/poner/mostrar ("— agrega tu negocio hoy")
    se DICE: la linea queda tal cual y no hay recurso (antes se cortaba y salia un 'Falta' falso)."""
    assert M.split_annotation(line) == (line, [])
    assert M.parse_script(line)["lines"][0] == {"id": "L01", "text": line, "inserts": []}


def test_marca_con_separador_solo_si_parece_archivo_o_existe():
    # parece archivo: _ . - digitos en UNA palabra, o extension de imagen/video
    assert M.split_annotation("Hola. - Agregar foto_1") == ("Hola.", ["foto_1"])
    assert M.split_annotation("Hola. — Mostrar Foto Local.jpg") == ("Hola.", ["Foto Local.jpg"])
    # varias: todas deben parecer archivo (si no, es una frase)
    assert M.split_annotation("Hola. - Agregar foto_1, la tienda de mi barrio") == \
        ("Hola. - Agregar foto_1, la tienda de mi barrio", [])
    # sin _ ni extension: solo si coincide con un archivo elegido
    line = "Abrimos temprano - agregar foto del local"
    assert M.split_annotation(line) == (line, [])
    assert M.split_annotation(line, [M.asset_key("Foto del local.HEIC")]) == ("Abrimos temprano", ["foto del local"])
    p = M.parse_script(line, [{"sid": "f1", "name": "foto del local.jpg"}])
    assert p["lines"][0]["text"] == "Abrimos temprano" and p["lines"][0]["inserts"] == ["foto del local"]
    # corchetes con verbo siguen siendo marca explicita; sin verbo, un numero es acotacion
    assert M.split_annotation("Hola [agregar foto del local]") == ("Hola", ["foto del local"])
    assert M.split_annotation("Somos tres socios. [1]") == ("Somos tres socios.", [])
    # el glosario conserva las palabras de la frase
    assert "Barrio Luna" in M.glossary_terms("Nuestra meta — agregar valor a Barrio Luna.")


def test_build_no_corta_una_frase_con_guion_largo(tmp_path):
    """Sesion sintetica: la linea 2 lleva un guion largo y 'agregar' como parte de lo que se dice.
    Sus palabras siguen en la linea de tiempo y no aparece ningun recurso."""
    l1, l3 = "Abrimos la panadería hace tres años.", "Te esperamos este sábado."
    l2 = "Nuestra meta — agregar valor a cada familia del barrio."
    clips = [mk(tmp_path, "p1", l1, 0, setup=0),
             mk(tmp_path, "p2", "Nuestra meta agregar valor a cada familia del barrio.", 1, setup=0),
             mk(tmp_path, "p3", l3, 2, setup=0)]
    r = M.build(clips, "\n".join([l1, l2, l3]))
    assert [L["inserts"] for L in r["script"]["lines"]] == [[], [], []]
    assert r["script"]["lines"][1]["text"] == l2
    words = tl_words(r, clips)
    for w in ("valor", "cada", "familia", "barrio."):
        assert w in words, (w, words)
    assert r["inserts"] == [] and r["stats"]["inserts"] == {"ok": 0, "missing": 0, "unplaced": []}


# ------------------------------------------------------------------ nombres: nada de prefijos peligrosos
def test_numero_o_letra_suelta_nunca_toma_un_clip():
    """'[1]' (una nota al pie) o '{a}' nunca se llevan una toma por prefijo del sid o del nombre."""
    files = [{"sid": "1f3a9c0d2b4e", "name": "IMG_0375.MOV"}, {"sid": "a07c55e1d9f2", "name": "IMG_0376.MOV"},
             {"sid": "c3d1e0f9a8b7", "name": "IMG_0377.MOV"},
             {"sid": "2b2b2b2b2b2b", "name": "20240518_101010_x.mp4"}]
    r = M.classify(files, "Somos tres socios. [1]\nY nos va bien. {a}\nCerramos. {2}")
    assert [f["sid"] for f in r["clips"]] == [f["sid"] for f in files] and r["assets"] == []
    assert r["refs"] == {"a": None, "2": None}
    # un sid solo por igual, nunca por prefijo
    assert M.match_asset("1f3a", files) is None and M.match_asset("1f3a9c0d2b4e", files)["name"] == "IMG_0375.MOV"
    # prefijo corto (< 4) o sin letras, o que no termina en palabra completa: no
    assert M.match_asset("img", files) is None and M.match_asset("2024", files) is None
    assert M.match_asset("img_03", files) is None
    # un prefijo por palabras completas si vale
    assert M.match_asset("foto_local", [{"sid": "s", "name": "foto_local_final.jpg"}])["sid"] == "s"


def test_nombre_del_guion_que_empieza_con_el_de_una_toma_no_la_roba():
    """'Marta.mov' es una TOMA; el guion pide 'marta_en_el_taller' (un b-roll que no se eligio): la
    toma sigue siendo toma y el recurso queda como faltante (antes pasaba a recurso por prefijo)."""
    files = [{"sid": "m1", "name": "Marta.mov"}, {"sid": "m2", "name": "Marta 2.mov"},
             {"sid": "m3", "name": "Marta 3.mov"}]
    g = "Hola, soy Marta y reparo bicicletas. - Agregar marta_en_el_taller"
    r = M.classify(files, g)
    assert [f["sid"] for f in r["clips"]] == ["m1", "m2", "m3"] and r["assets"] == []
    assert r["refs"] == {"marta_en_el_taller": None}
    # al reves solo una IMAGEN (nunca es toma): "logo" <- "logo_nuevo"
    assert M.match_asset("logo_nuevo", [{"sid": "l", "name": "logo.png"}])["sid"] == "l"
    assert M.match_asset("logo_nuevo", [{"sid": "l", "name": "logo.mov"}]) is None


def test_prefijo_nunca_se_lleva_una_toma_con_voz(tmp_path):
    """Un parecido SOLO por prefijo no convierte en recurso un video con voz transcrita (words o
    has_words del servidor): el recurso queda faltante y la toma sigue en el armado."""
    files = [{"sid": "t1", "name": "toma_pan_1.mov", "words": [{"text": "hola", "start": 0, "end": 0.3}]},
             {"sid": "t2", "name": "otra.mov", "has_words": True}]
    r = M.classify(files, "Hola. {toma_pan}")
    assert [f["sid"] for f in r["clips"]] == ["t1", "t2"] and r["refs"] == {"toma_pan": None}
    # igual (nombre exacto) si: el usuario la pidio por su nombre
    r = M.classify(files, "Hola. {toma_pan_1}")
    assert [f["sid"] for f in r["assets"]] == ["t1"]
    # sin voz transcrita (un b-roll mudo) el prefijo si vale
    r = M.classify([{"sid": "b", "name": "toma_pan_broll.mov"}], "Hola. {toma_pan}")
    assert [f["sid"] for f in r["assets"]] == ["b"]
    # en build: la toma con voz que el prefijo nombra sigue siendo toma
    clips = [mk(tmp_path, "p1", "Cada mañana prendemos el horno a las cinco.", 0, setup=0, name="horno_uno_a.MOV")]
    out = M.build(clips, "Cada mañana prendemos el horno a las cinco. {horno_uno}")
    assert out["timeline"] and out["timeline"][0]["sid"] == "p1"
    assert [(x["name"], x["status"]) for x in out["inserts"]] == [("horno_uno", "missing")]


# ------------------------------------------------------------------ colocacion: linea partida en dos tomas
def test_linea_partida_en_dos_tomas_no_estira_el_recurso():
    """s01 cubre L01+L02 partida en A [1, 6] y B [5.6, 8.6]; el candidato de B trae line_times de L01
    FUERA de su tramo: no debe estirar el recurso de L01 hasta la mitad de L02 (= fxcore.lineSpans)."""
    m = {"script": {"lines": [{"id": "L01", "text": "a", "inserts": ["foto_local"]},
                              {"id": "L02", "text": "b", "inserts": []}]},
         "slots": [{"id": "s01", "lines": ["L01", "L02"], "cands": [
             {"id": "c11", "sid": "A", "line_times": {"L01": [1.2, 3.0], "L02": [3.3, 9.0]}},
             {"id": "c12", "sid": "B", "line_times": {"L01": [0.7, 2.5], "L02": [2.8, 8.4]}}]}],
         "timeline": [{"slot": "s01", "cand": "c11", "sid": "A", "in": 1.0, "out": 6.0, "start": 0.0},
                      {"slot": "s01", "cand": "c12", "sid": "B", "in": 5.6, "out": 8.6, "start": 5.0}]}
    (x,) = M.plan_inserts(m, [{"sid": "F", "name": "foto_local.jpg", "kind": "image"}])
    assert (x["line"], x["start"], x["end"]) == ("L01", 0.05, 2.25)
