"""Videos de apoyo: tomas, de que trata cada una y si se puede usar (scripts/brollscan.py).

Funciones puras (cortes sobre puntajes, momentos de una toma larga, recortes 9:16 sin personas ni textos
cortados, mascaras de persona sin los personajes que la segmentacion confunde, reglas de uso) y el analisis
completo de un video SINTETICO (tests/_broll_media.py: interfaz, personaje, titulo, persona real del
material de prueba si existe) con un VLM de prueba: cortes al cuadro, una persona nunca sirve, la interfaz
sirve en ventana y pantalla completa, el titulo de producto sirve, una toma casi negra no, y la cache."""
import json
import time
from pathlib import Path

import numpy as np
import pytest

import brollscan as B
from _broll_media import make_film, read_bits


# ============================================================== cortes (puras)
def _scores(n, cuts, base=0.05, peak=1.5, dup_every=0):
    rng = np.random.default_rng(1)
    s = base + 0.02 * rng.random(n)
    if dup_every:
        s[::dup_every] = 0.0                    # cuadros repetidos (24 cps dentro de 60)
    for c in cuts:
        s[c] = peak
    s[0] = 0.0
    return s.astype(np.float32)


def test_cortes_en_los_picos_que_destacan():
    s = _scores(600, [100, 250, 400])
    assert B.pick_cuts(s, 30) == [100, 250, 400]
    # un pico bajo el piso absoluto no es corte; dos picos pegados cuentan como uno
    s2 = _scores(600, [100])
    s2[300] = 0.3
    s2[401], s2[403] = 1.2, 1.2
    assert B.pick_cuts(s2, 30) == [100, 401]
    # con cuadros repetidos (puntaje 0) la mediana no se hunde: el movimiento normal no es corte
    s3 = _scores(600, [200], base=0.25, dup_every=3)
    assert B.pick_cuts(s3, 60) == [200]
    # movimiento fuerte y parejo (sin pico que destaque) no es corte
    s4 = np.full(300, 0.6, np.float32)
    s4[0] = 0
    assert B.pick_cuts(s4, 30) == []


def test_tomas_desde_cortes_y_fundidos_cortos():
    assert B.shots_from_cuts([2.0, 5.0], 8.0) == [(0.0, 2.0), (2.0, 5.0), (5.0, 8.0)]
    # un destello de 0.2 s se une a la siguiente; la ultima corta, a la anterior
    assert B.shots_from_cuts([0.2, 3.0, 7.8], 8.0) == [(0.0, 3.0), (3.0, 8.0)]
    assert B.shots_from_cuts([], 4.0) == [(0.0, 4.0)]
    assert B.shots_from_cuts([-1, 9, 4.0, 4.0], 8.0) == [(0.0, 4.0), (4.0, 8.0)]


def test_momentos_de_una_toma_larga():
    assert B.moments(0.0, 4.0) == [(0.0, 4.0)]
    ms = B.moments(10.0, 22.0)
    assert ms[0][0] == 10.0 and ms[-1][1] == 22.0
    assert all(b - a >= B.MOMENT[0] - 1e-6 for a, b in ms) and len(ms) in (4, 5)
    assert all(ms[k][1] == ms[k + 1][0] for k in range(len(ms) - 1))
    # con puntajes: la frontera cae en el cuadro de mas cambio de su rango
    ch = np.zeros(int(30 * 30), np.float32)
    ch[int(15.4 * 30)] = 0.9
    ms = B.moments(10.0, 20.0, ch, 30.0)
    assert any(abs(b - 15.4) < 0.04 for _a, b in ms[:-1]), ms


# ============================================================== recortes y personas (puras)
def test_recorte_9x16_evita_personas_y_textos_y_contiene_al_sujeto():
    h, w = 90, 160
    m = np.zeros((h, w), bool)
    m[:, :40] = True                                    # alguien a la izquierda
    sub = [0.45, 0.2, 0.7, 0.8]                         # el sujeto (una pantalla) a la derecha del centro
    fc = B.full_crop(16 / 9, sub, [m], [])
    assert fc["people"] <= B.FULL_PEOPLE and fc["inside"] >= B.FULL_INSIDE and fc["cut_text"] == 0
    cw = fc["w"]
    assert cw == pytest.approx((9 / 16) / (16 / 9), abs=1e-3)
    assert fc["cx"] - cw / 2 >= 40 / 160 - 1e-6
    # una linea de texto ancha en el centro: el recorte no la corta a la mitad
    fc2 = B.full_crop(16 / 9, [0.3, 0.2, 0.7, 0.8], [np.zeros((h, w), bool)], [[0.42, 0.5, 0.62, 0.55]])
    x0, x1 = fc2["cx"] - fc2["w"] / 2, fc2["cx"] + fc2["w"] / 2
    assert not (0.42 < x0 < 0.62) and not (0.42 < x1 < 0.62) and fc2["cut_text"] == 0
    # una fuente vertical no se recorta de ancho
    assert B.full_crop(9 / 16, None, [], [])["cx"] == 0.5


def test_recorte_no_corta_ni_una_letra_ni_un_personaje_ni_un_globo():
    """Un borde que cruza una linea de texto o pasa a menos de media linea de ella la corta (antes se
    perdonaba si quedaba < 15 % de la linea de un lado: la primera palabra de un globo afuera, o el globo de
    chat rebanado junto a su texto); una cosa de color (personaje, globo) cortada tambien cuenta; un
    personaje cuya caja cabe va ENTERO."""
    cw = (9 / 16) / (16 / 9)
    x0, x1 = 0.5 - cw / 2, 0.5 + cw / 2
    asp = 16 / 9
    # el borde derecho deja afuera 0.012 del ancho (unas letras) de una linea de 0.02 de alto
    assert B._edge_cuts(x0, x1, [[x0 + 0.2, 0.40, x1 + 0.012, 0.42]], asp, "text") == 1
    # ... o la deja pegada a la orilla (sin el aire de su globo)
    assert B._edge_cuts(x0, x1, [[x0 + 0.2, 0.40, x1 - 0.002, 0.42]], asp, "text") == 1
    assert B._edge_cuts(x0, x1, [[x0 - 0.1, 0.40, x0 + 0.002, 0.42]], asp, "text") == 1
    # con aire de media linea, no
    assert B._edge_cuts(x0, x1, [[x0 + 0.02, 0.40, x1 - 0.02, 0.42]], asp, "text") == 0
    assert B._edge_cuts(x0, x1, [[x1 + 0.01, 0.40, x1 + 0.2, 0.42]], asp, "text") == 0
    # un recorte que no puede evitar cortar el texto lo dice (y usable() lo descarta)
    wide = [[0.2, 0.30 + 0.05 * k, 0.8, 0.33 + 0.05 * k] for k in range(4)]
    fc = B.full_crop(16 / 9, [0.2, 0.2, 0.8, 0.8], [], wide)
    assert fc["cut_text"] > 0
    assert B.usable({"a": 0.0, "b": 2.0, "subject": "screen", "full": fc})[1:] == (False, "Recortada a pantalla "
                                                                                    "completa cortaría un texto")
    # una cosa de color: el recorte se corre para no cortarla
    blob = [0.30, 0.3, 0.42, 0.6]
    fc = B.full_crop(16 / 9, [0.25, 0.2, 0.75, 0.8], [], [], objs=[blob])
    a, b = fc["cx"] - fc["w"] / 2, fc["cx"] + fc["w"] / 2
    assert fc["cut_obj"] == 0 and B._edge_cuts(a, b, [blob], asp, "obj") == 0
    assert B._edge_cuts(0.5 - cw / 2, 0.5 + cw / 2, [blob], asp, "obj") == 1    # centrado la cortaba
    # una fila de tarjetas de lado a lado: alguna se corta y se dice
    fc = B.full_crop(16 / 9, [0.1, 0.2, 0.9, 0.8], [], [],
                     objs=[[0.2 * k, 0.3, 0.2 * k + 0.2, 0.6] for k in range(5)])
    assert fc["cut_obj"] > 0
    w, f, why = B.usable({"a": 0.0, "b": 2.0, "subject": "screen", "full": fc, "intruder": True})
    assert (w, f) == (False, False) and why == "Sale una persona, y recortada a pantalla completa cortaría un " \
        "personaje o un globo de texto"
    # un recorte que no corta nada porque se fue a la pared (lejos del chat del sujeto) no sirve
    chat = [[0.02, 0.3 + 0.06 * k, 0.36, 0.33 + 0.06 * k] for k in range(5)]
    fc = B.full_crop(16 / 9, [0.0, 0.0, 0.56, 1.0], [], chat)
    assert fc["content"] < B.FULL_CONTENT or fc["cut_text"] > 0, fc
    assert B.usable({"a": 0.0, "b": 2.0, "subject": "screen", "full": fc})[1] is False
    fc = B.full_crop(16 / 9, [0.0, 0.0, 0.56, 1.0], [], [[0.05, 0.3 + 0.06 * k, 0.28, 0.33 + 0.06 * k] for k in range(5)])
    assert fc["content"] == pytest.approx(1.0) and fc["cut_text"] == 0
    assert B.usable({"a": 0.0, "b": 2.0, "subject": "screen", "full": fc})[1] is True
    # un personaje cuya caja cabe en el recorte va entero (no basta con 60 % adentro)
    ch = [0.05, 0.2, 0.3, 0.8]
    fc = B.full_crop(16 / 9, ch, [], [], whole=True)
    a, b = fc["cx"] - fc["w"] / 2, fc["cx"] + fc["w"] / 2
    assert fc["whole"] and a <= ch[0] + 1e-6 and b >= ch[2] - 0.02 and fc["inside"] >= B.FULL_WHOLE
    # una caja mas ancha que el recorte (la del VLM es holgada) no se exige entera
    fc = B.full_crop(16 / 9, [0.2, 0.1, 0.8, 0.9], [], [], whole=True)
    assert fc["whole"] is False and B.usable({"a": 0.0, "b": 2.0, "subject": "character", "full": fc})[1]


def test_cosas_de_color_personajes_y_globos_pero_no_tablas_ni_barras():
    h, w = 180, 320
    rgb = np.zeros((h, w, 3), np.uint8)
    rgb[:] = (120, 84, 52)                                      # madera
    rgb[:, 100:118] = (170, 118, 70)                            # una tabla iluminada (tira, mismo tono)
    rgb[20:160, 130:300] = (244, 244, 246)                      # una pantalla blanca
    rgb[24:38, 132:298] = (30, 120, 220)                        # su barra de titulo de lado a lado
    yy, xx = np.mgrid[0:h, 0:w]
    rgb[(yy - 90) ** 2 + (xx - 180) ** 2 < 28 ** 2] = (240, 200, 30)    # un personaje amarillo
    rgb[110:134, 220:290] = (60, 120, 220)                      # un globo de chat azul
    got = B.object_boxes(rgb)
    def has(x0, y0, x1, y1):
        return any(abs(b[0] - x0 / w) < 0.03 and abs(b[2] - x1 / w) < 0.03 and abs(b[1] - y0 / h) < 0.05
                   for b in got)
    assert has(152, 62, 208, 118), got                          # el personaje
    assert has(220, 110, 290, 134), got                         # el globo
    assert not any(b[2] - b[0] > 0.4 for b in got), got         # ni la barra de titulo
    assert not any(b[0] < 118 / w and b[2] > 100 / w and b[2] - b[0] < 0.08 for b in got), got   # ni la tabla


def test_ventana_se_corre_a_donde_no_sale_gente():
    h, w = 90, 160
    m = np.zeros((h, w), bool)
    m[50:90, 60:100] = True                                     # el publico abajo del logo
    sub = [0.42, 0.40, 0.58, 0.55]
    plain = B.window_crop(16 / 9, sub, True, 0.02)
    moved = B.window_crop(16 / 9, sub, True, 0.02, [m])
    assert B.person_frac(m, moved[0], moved[2], moved[1], moved[3]) < B.person_frac(m, plain[0], plain[2], plain[1], plain[3])
    assert moved[0] <= sub[0] and moved[1] <= sub[1] and moved[2] >= sub[2] and moved[3] >= sub[3]
    assert moved[2] - moved[0] == pytest.approx(plain[2] - plain[0], abs=1e-3)


def test_ventana_recorta_un_celular_sobre_fondo_liso():
    c = B.window_crop(16 / 9, [0.42, 0.05, 0.58, 0.95], True, 0.15)
    assert c is not None and c[2] - c[0] < 0.6
    asp = (c[2] - c[0]) * 16 / 9 / (c[3] - c[1])
    assert 9 / 16 - 1e-3 <= asp <= 16 / 9 + 1e-3
    assert B.window_crop(16 / 9, [0.42, 0.05, 0.58, 0.95], False, 0.15) is None       # fondo con cosas
    assert B.window_crop(16 / 9, [0.1, 0.1, 0.9, 0.9], True, 0.64) is None            # sujeto grande


def test_mascara_sin_personajes_ni_fondo_pero_con_personas_reales():
    h, w = 120, 200
    rgb = np.full((h, w, 3), 230, np.uint8)             # fondo liso claro
    m = np.zeros((h, w), bool)
    # un personaje de color vivo al centro (la segmentacion lo toma por persona) + un "cuerpo" inventado
    # del mismo color que el fondo debajo de el
    yy, xx = np.mgrid[0:h, 0:w]
    ball = (yy - 50) ** 2 + (xx - 100) ** 2 < 25 ** 2
    rgb[ball] = (220, 60, 160)
    m |= ball
    m[75:120, 80:120] = True
    # una persona real (oscura, entra por el borde derecho)
    rgb[30:120, 170:200] = (40, 30, 28)
    m[30:120, 170:200] = True
    out = B.discount(m, [0.35, 0.15, 0.65, 0.65], [], rgb)
    assert not out[ball].any() and not out[90:110, 85:115].any()
    assert out[30:120, 170:200].all()                   # la persona real se queda
    # con cara: nunca se quita
    out2 = B.discount(m, [0.35, 0.15, 0.65, 0.65], [(0.5, 0.42, 0.25, 0.42, 0.9)], rgb)
    assert out2[ball].any()
    assert B.intruder(out) is True                      # entra por el borde y mide > EDGE_PERSON
    small = np.zeros((h, w), bool)
    small[50:62, 90:96] = True                          # alguien chiquito al fondo
    assert B.intruder(small) is False
    tall = np.zeros((h, w), bool)
    tall[10:110, 90:100] = True                         # de cuerpo entero a media escena
    assert B.intruder(tall) is True


def test_reglas_de_uso():
    base = {"a": 0.0, "b": 2.0, "subject": "screen", "people": 0.0, "face": 0.0,
            "full": {"cx": 0.5, "people": 0.0, "cut_text": 0, "inside": 1.0}}
    assert B.usable(base) == (True, True, "")
    assert B.usable(dict(base, b=0.4))[2] == "Toma demasiado corta"
    assert B.usable(dict(base, dark=True))[:2] == (False, False)
    assert B.usable(dict(base, subject="person"))[2] == "Una persona es lo principal"
    assert B.usable(dict(base, face=0.05))[:2] == (False, False)
    assert B.usable(dict(base, subject="place"))[:2] == (False, False)
    w, f, why = B.usable(dict(base, intruder=True))
    assert (w, f) == (False, True) and "pantalla completa" in why
    w, f, why = B.usable(dict(base, full={"cx": 0.5, "people": 0.0, "cut_text": 2, "inside": 1.0}))
    assert (w, f) == (True, False) and "texto" in why
    assert B.usable(dict(base, person_seen=True))[0] is False
    # «Una mujer observa una pantalla…»: la descripcion del VLM dice que la toma es de una persona -> ni
    # recortada (antes pasaba a pantalla completa porque el recorte la evitaba: el personaje cortado
    # por la orilla y la camara moviendose con ella)
    lab = B.merge_label({"people": 0.05, "face": 0.0, "sal_box": None},
                        {"subject": "screen", "person_is_main": False, "text_seen": [],
                         "what": "Un hombre mira una pantalla con un logo de estrella", "tags": ["pantalla"],
                         "subject_box": [100, 100, 600, 600]})
    assert lab["person_what"] is True
    assert B.usable(dict(base, person_seen=True, person_what=True))[:3] == (False, False, "Una persona es lo principal")
    lab2 = B.merge_label({"people": 0.05, "face": 0.0, "sal_box": None},
                         {"subject": "screen", "person_is_main": False, "text_seen": ["Nubis"],
                          "what": "Pantalla de interfaz de Nubis con opciones", "tags": ["pantalla"],
                          "subject_box": [100, 100, 600, 600]})
    assert lab2["person_what"] is False


def test_etiqueta_del_vlm_y_las_reglas_de_personas_mandan():
    feat = {"people": 0.02, "face": 0.0, "texts": [], "sal_box": [0.3, 0.3, 0.7, 0.7]}
    v = {"subject": "character", "person_is_main": False, "text_seen": ["nubis"], "what": "Un personaje azul",
         "tags": ["Personaje", "azul"], "subject_box": [300, 200, 700, 800]}
    lab = B.merge_label(feat, v)
    assert lab["subject"] == "character" and lab["box"] == [0.3, 0.2, 0.7, 0.8] and lab["tags"] == ["personaje", "azul"]
    assert lab["src"] == "vlm" and not lab["person_seen"]
    # una cara grande gana aunque el VLM diga pantalla
    lab = B.merge_label(dict(feat, face=0.08), dict(v, subject="screen"))
    assert lab["subject"] == "person" and lab["person_main"]
    # "Una mujer observa una pantalla..." = hay alguien a la vista (sin ventana)
    lab = B.merge_label(feat, dict(v, subject="screen", what="Una mujer observa una pantalla"))
    assert lab["person_seen"] and lab["subject"] == "screen"
    # sin VLM: reglas
    assert B.merge_label(dict(feat, texts=[[0, 0, 1, 1]] * 4), None)["subject"] == "screen"
    assert B.merge_label(dict(feat, dark_bg=True, center_bright=True), None)["subject"] == "logo"


def test_cajas_de_texto_y_sujeto_llamativo():
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 180), (245, 245, 248))
    d = ImageDraw.Draw(im)
    for k in range(3):
        d.rectangle([40, 40 + 30 * k, 200 - 20 * k, 48 + 30 * k], fill=(30, 30, 36))
    d.ellipse([230, 60, 300, 130], fill=(200, 40, 120))
    a = np.asarray(im)
    tb = B.text_boxes(a)
    assert len(tb) >= 3 and all(b[0] >= 0.1 and b[2] <= 0.66 for b in tb[:3])
    sb = B.saliency_box(a)
    assert sb is not None
    assert B.saliency_box(np.full((90, 160, 3), 128, np.uint8)) is None


# ============================================================== analisis completo (video sintetico)
SHOTS = [("title", 2.0), ("screen", 1.6), ("person", 1.5), ("character", 1.8), ("screen", 2.2),
         ("dark", 0.3), ("character", 1.4), ("person", 2.0), ("screen", 6.0), ("wide", 2.0)]
TRUTH = {"title": ("title", False, ["nubis"]), "screen": ("ui", False, ["Hola"]), "wide": ("ui", False, ["Hola"]),
         "character": ("character", False, []), "person": ("person", True, []), "dark": ("object", False, [])}


@pytest.fixture(scope="module")
def film(tmp_path_factory):
    d = tmp_path_factory.mktemp("apoyo")
    shots = make_film(d / "producto.mp4", SHOTS, fps=30)
    return d, shots


def _stub_vlm(shots, calls, fps=30):
    """VLM de prueba: lee el numero de cuadro (bits del video sintetico) y contesta como el real lo haria
    para ese tipo de toma."""
    def fn(rgb):
        n = read_bits(rgb)
        t = (n or 0) / fps
        k = next((s["kind"] for s in shots if s["a"] <= t < s["b"]), "dark")
        calls.append(k)
        subj, main, texts = TRUTH[k]
        box = [300, 60, 700, 820] if k in ("screen", "wide", "title", "character") else [0, 0, 1000, 1000]
        return {"subject": subj, "person_is_main": main, "text_seen": texts,
                "what": {"screen": "Interfaz de una app con mensajes", "wide": "Interfaz de una app con mensajes",
                         "title": "El titulo nubis",
                         "character": "Un personaje de color", "person": "Una persona hablando",
                         "dark": "Casi negro"}[k], "tags": ["interfaz"], "subject_box": box}
    return fn


def test_analisis_de_un_video_de_apoyo_sintetico(film, tmp_path):
    d, shots = film
    calls = []
    doc = B.analyze(d / "producto.mp4", cache_dir=tmp_path / "c", vlm_fn=_stub_vlm(shots, calls))
    got = doc["shots"]
    # cortes al cuadro: cada toma real empieza donde se corto (el fundido de 0.3 s se une a la siguiente)
    starts = [s["a"] for s in got if s["cut_a"]]
    want = [s["a"] for k, s in enumerate(shots) if k == 0 or shots[k - 1]["kind"] != "dark"]
    assert len(starts) == len(want), (starts, want)
    for a0, a in zip(want, starts):
        assert abs(a0 - a) <= 1 / 30 + 1e-6, (starts, want)
    by_time = lambda t: next(x for x in got if x["a"] <= t < x["b"])   # noqa: E731
    # interfaz: sirve en ventana y en pantalla completa (centrada en la app)
    sc = by_time(2.8)
    assert sc["subject"] == "ui" and sc["ok_window"] and sc["ok_full"] and abs(sc["full"]["cx"] - 0.5) < 0.12
    # titulo del producto: sirve, y lo que se lee viaja en el catalogo
    tt = by_time(1.0)
    assert tt["ok_window"] and "nubis" in tt["text"]
    # personaje de color: la segmentacion lo confunde con persona; el analisis no
    ch = by_time(6.0)
    assert ch["subject"] == "character" and ch["people"] < B.WINDOW_PEOPLE and ch["ok_full"]
    # persona: nunca (ni en ventana ni recortada)
    for t in (4.3, 11.8):
        p = by_time(t)
        assert not p["ok_window"] and not p["ok_full"] and p["why"]
    # una interfaz mas ancha que un recorte 9:16 (globos de orilla a orilla): solo en ventana, y se dice
    wd = by_time(19.8)
    assert wd["subject"] == "ui" and wd["ok_window"] and not wd["ok_full"], wd["full"]
    assert wd["why"].startswith("Recortada a pantalla completa cortaría"), wd["why"]
    # la toma larga (6 s) se parte en momentos
    assert sum(1 for x in got if 12.8 - 1e-6 <= x["a"] < 18.8) >= 2
    # el VLM vio cada tipo de toma (un cuadro por toma); el fundido casi negro de 0.3 s no es toma propia
    assert {"title", "screen", "character"} <= set(calls) and len(calls) <= len(got)
    # miniatura por toma y resumen
    assert B.thumb_path(d / "producto.mp4", got[1]["i"], tmp_path / "c") is not None
    sm = B.summary(doc)
    assert sm["shots"] == len(got) and sm["usable"] >= 4 and sm["people"] >= 2
    # el catalogo compacto (lo que viaja en el plan) trae el recorte y las reglas, sin mascaras
    c = B.compact(doc)
    assert c["shots"][1]["cx"] == got[1]["full"]["cx"] and "texts" not in c["shots"][1]
    assert json.loads(json.dumps(c)) == c


def test_cache_por_archivo_y_por_modelo(film, tmp_path, monkeypatch):
    d, _shots = film
    monkeypatch.setattr(B, "vlm_model", lambda: None)            # sin ollama: reglas
    t0 = time.time()
    a = B.analyze(d / "producto.mp4", cache_dir=tmp_path / "c")
    assert a["vlm"] is None and all(s["src"] == "reglas" for s in a["shots"])

    def boom(*a, **k):
        raise AssertionError("no debia leer el video otra vez")
    monkeypatch.setattr(B, "detect_cuts", boom)
    t1 = time.time()
    b = B.analyze(d / "producto.mp4", cache_dir=tmp_path / "c")
    assert b == a and time.time() - t1 < max(0.5, 0.2 * (t1 - t0))
    assert B.cached(d / "producto.mp4", tmp_path / "c")["sig"] == a["sig"]
    # otro modelo del VLM = otro analisis (no el de reglas)
    monkeypatch.setattr(B, "vlm_model", lambda: "otro:1b")
    with pytest.raises(AssertionError):
        B.analyze(d / "producto.mp4", cache_dir=tmp_path / "c")


def test_sin_vlm_las_reglas_nunca_dejan_pasar_una_persona(film, tmp_path, monkeypatch):
    d, _shots = film
    monkeypatch.setattr(B, "vlm_model", lambda: None)
    doc = B.analyze(d / "producto.mp4", cache_dir=tmp_path / "r")
    for s in doc["shots"]:
        if 3.7 <= (s["a"] + s["b"]) / 2 <= 5.0 or 10.9 <= (s["a"] + s["b"]) / 2 <= 12.7:
            assert not (s["ok_window"] or s["ok_full"]), s
    ui = next(s for s in doc["shots"] if s["a"] <= 2.8 < s["b"])
    # (sin VLM la interfaz es "screen": un panel alto y angosto de pie sobre el fondo liso oscuro = un aparato:
    # en ventana recortada a el; a pantalla completa se le cortarian los lados)
    assert ui["ok_window"] and ui["device"] and not ui["ok_full"] and "aparato" in ui["why"]


# ============================================================== personas que no se borran / VLM caido
def _screen_scene(h=180, w=320):
    rgb = np.full((h, w, 3), (40, 44, 52), np.uint8)
    rgb[10:150, 30:290] = (245, 246, 250)                     # una pantalla grande con un chat
    for k in range(5):
        x0 = 45 + (k % 2) * 110
        rgb[20 + k * 25:38 + k * 25, x0:x0 + 105] = (60, 130, 240) if k % 2 else (225, 228, 235)
    return rgb


def test_una_persona_de_espaldas_frente_a_la_pantalla_no_se_borra():
    """Una persona sin cara visible (de espaldas), de cuerpo entero frente a la pantalla que el VLM llama
    sujeto, 62 % del alto y sin tocar el borde: antes discount() la quitaba (mancha sin cara >= 60 % en la
    caja holgada del VLM) y la toma pasaba como 'sirve en ventana'. Un personaje de color si se va."""
    h, w = 180, 320
    rgb = _screen_scene(h, w)
    m = np.zeros((h, w), bool)
    yy, xx = np.mgrid[0:h, 0:w]
    head = (yy - 70) ** 2 / 11 ** 2 + (xx - 180) ** 2 / 9 ** 2 < 1
    body = (yy >= 80) & (yy < 172) & (np.abs(xx - 180) < 16 + (yy - 80) * 0.08)
    person = head | body
    rgb[person] = (96, 80, 72)                                # pelo y ropa cafes, sin color saturado
    m |= person
    box = [0.08, 0.04, 0.92, 0.86]                            # la pantalla (holgada: cubre a la persona)
    out = B.discount(m, box, [], rgb)
    assert out[person].mean() > 0.95
    assert B.intruder(out) is True                            # de cuerpo entero: se VE una persona
    sh = {"a": 0.0, "b": 2.0, "subject": "screen", "person_main": False, "people": float(out.mean()), "face": 0.0,
          "full": None, "person_seen": False, "intruder": B.intruder(out), "dark": False}
    assert B.usable(sh)[0] is False                           # no sirve en ventana
    # un personaje de color del mismo tamano en el mismo lugar si se quita (la segmentacion lo confunde)
    rgb2 = _screen_scene(h, w)
    rgb2[person] = (150, 60, 220)
    out2 = B.discount(m, box, [], rgb2)
    assert not out2[person].any() and B.intruder(out2) is False
    # una mancha compacta sin color (un logo blanco que la segmentacion toma por persona) en la caja, si
    ball = (yy - 80) ** 2 + (xx - 160) ** 2 < 20 ** 2
    rgb3 = _screen_scene(h, w)
    rgb3[ball] = (250, 250, 250)
    assert not B.discount(ball.copy(), box, [], rgb3)[ball].any()


def test_persona_real_de_espaldas_con_los_modelos(tmp_path):
    """Lo mismo con PP-HumanSeg y YuNet reales sobre una persona real (material de prueba LOCAL, nunca
    versionado; sin el, se omite) con la cara pintada del color del pelo, frente a una pantalla."""
    import subprocess
    import cv2
    from conftest import TEST_MEDIA
    import paths
    src = Path(TEST_MEDIA) / "crudo_completo.mp4"
    if not src.exists():
        pytest.skip("sin material de prueba local")
    r = subprocess.run([str(paths.FFMPEG), "-v", "error", "-ss", "3", "-i", str(src), "-frames:v", "1", "-vf",
                        "scale=-2:720,format=rgb24", "-f", "rawvideo", "-"], capture_output=True)
    w0 = len(r.stdout) // 3 // 720
    person = np.frombuffer(r.stdout[:w0 * 720 * 3], np.uint8).reshape(720, w0, 3).copy()
    pm = B._person_mask(person)
    if pm is None or not pm.any():
        pytest.skip("sin modelo de segmentacion")
    for cx, cy, fw, fh, _s in B._faces(person):
        cv2.ellipse(person, (int(cx * w0), int(cy * 720)), (int(fw * w0 * 0.75), int(fh * 720 * 0.85)), 0, 0, 360,
                    (38, 30, 26), -1)
    ys, xs = np.where(pm)
    cut, cm = person[ys.min():ys.max(), xs.min():xs.max()], pm[ys.min():ys.max(), xs.min():xs.max()]
    H, W = 360, 640
    scene = np.full((H, W, 3), (40, 44, 52), np.uint8)
    cv2.rectangle(scene, (60, 20), (580, 300), (245, 246, 250), -1)
    for k in range(5):
        cv2.rectangle(scene, (90 + (k % 2) * 220, 40 + k * 50), (300 + (k % 2) * 220, 75 + k * 50),
                      (60, 130, 240) if k % 2 else (225, 228, 235), -1)
    th = int(H * 0.62)
    tw = int(cut.shape[1] * th / cut.shape[0])
    cut = cv2.resize(cut, (tw, th))
    cm = cv2.resize(cm.astype(np.uint8), (tw, th)) > 0
    ox, oy = W // 2 - tw // 2 + 40, H - th - 40
    reg = scene[oy:oy + th, ox:ox + tw]
    reg[cm] = cut[cm]
    f = B.frame_feat(scene)
    assert f["people"] > 0.02 and B.intruder(f["mask"])
    m2 = B.discount(f["mask"], [0.08, 0.04, 0.92, 0.86], f["faces"], scene)
    assert B.intruder(m2) is True, (float(m2.mean()), f["people"])


def test_un_vlm_caido_un_rato_no_deja_el_catalogo_degradado_para_siempre(film, tmp_path, monkeypatch):
    """3 fallos seguidos del VLM (60 s de espera mientras ollama carga, o la GPU con whisper) mandaban el
    resto de las tomas a las reglas y ese catalogo quedaba en cache para siempre (B-roll apagado para ese
    archivo). Ahora se guarda como `degraded` y el siguiente analyze() lo vuelve a ver, sin volver a
    preguntar lo que si contesto."""
    d, shots = film
    good = _stub_vlm(shots, [])
    calls = {"n": 0}

    def flaky(rgb, timeout=None):
        calls["n"] += 1
        return None if calls["n"] <= 3 else good(rgb)
    monkeypatch.setattr(B, "vlm_model", lambda: "qwen3.5:4b")
    monkeypatch.setattr(B, "_vlm_label", flaky)
    a = B.analyze(d / "producto.mp4", cache_dir=tmp_path / "c")
    assert a["degraded"] > 0 and any(s["src"] == "reglas" for s in a["shots"])
    n_bad = calls["n"]
    assert B.cached(d / "producto.mp4", tmp_path / "c")["degraded"] > 0     # el Studio ve algo mientras tanto

    def healthy(rgb, timeout=None):
        calls["n"] += 1
        return good(rgb)
    monkeypatch.setattr(B, "_vlm_label", healthy)
    b = B.analyze(d / "producto.mp4", cache_dir=tmp_path / "c")
    assert b["degraded"] == 0 and calls["n"] > n_bad
    ref = B.analyze(d / "producto.mp4", cache_dir=tmp_path / "ref", vlm_fn=good)
    assert B.summary(b)["usable"] == B.summary(ref)["usable"] > B.summary(a)["usable"]
    # ya completo: el tercero sale de la cache sin preguntar nada
    n2 = calls["n"]
    c = B.analyze(d / "producto.mp4", cache_dir=tmp_path / "c")
    assert c == b and calls["n"] == n2


def _phone_film(path, dur=3.0, fps=30, w=640, h=360):
    """Una demo de celular SINTETICA: un telefono vertical (casi todo el alto, un tercio del ancho) con un
    chat blanco, sobre un fondo lima liso con dos adornos verdes en las esquinas."""
    import subprocess
    import cv2
    n = int(dur * fps)
    frames = []
    for k in range(n):
        img = np.full((h, w, 3), (182, 239, 227), np.uint8)          # BGR del lima
        cv2.circle(img, (20, 20), 26, (60, 160, 70), -1)
        cv2.circle(img, (w - 18, h - 16), 22, (60, 160, 70), -1)
        x0, x1, y0 = 196, 444, 22
        cv2.rectangle(img, (x0, y0), (x1, h + 10), (30, 30, 30), -1)          # el marco del telefono
        cv2.rectangle(img, (x0 + 8, y0 + 8), (x1 - 8, h + 10), (250, 250, 250), -1)
        for j in range(6):
            yy = y0 + 30 + j * 48 + (k % 5)
            if j % 2:
                cv2.rectangle(img, (x0 + 90, yy), (x1 - 20, yy + 30), (120, 200, 90), -1)
            else:
                cv2.rectangle(img, (x0 + 20, yy), (x1 - 80, yy + 30), (232, 232, 236), -1)
                cv2.putText(img, "Hola que tal", (x0 + 26, yy + 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 30, 30), 1)
        frames.append(img)
    p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r",
                          str(fps), "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt",
                          "yuv420p", str(path)], stdin=subprocess.PIPE)
    p.stdin.write(b"".join(f.tobytes() for f in frames))
    p.stdin.close()
    assert p.wait() == 0
    return path, (x0 / w, y0 / h, x1 / w, 1.0)


def test_demo_de_celular_se_recorta_al_telefono_en_la_ventana(tmp_path):
    """Una demo de celular: el celular sobre un fondo lima se veia como TODO el cuadro 16:9 en la ventana (el
    telefono un cuarto del ancho; el chat no se leia) porque el sujeto ocupaba mas de 0.35 del area. Ahora un
    sujeto ANGOSTO sobre un fondo liso (bg_flat, con adornos) se recorta al aparato (device_box): recorte
    vertical y una tarjeta donde el telefono llena >= 80 % del alto."""
    from motion import reel as RL
    vid, ph = _phone_film(tmp_path / "demo.mp4")

    def vlm(rgb, timeout=None):
        # el VLM da una caja holgada (mas ancha que el telefono)
        return {"subject": "phone", "person_is_main": False, "text_seen": ["Hola que tal"],
                "what": "Un teléfono mostrando un chat", "tags": ["telefono", "chat"], "subject_box": [240, 40, 760, 990]}
    doc = B.analyze(vid, cache_dir=tmp_path / "c", vlm_fn=vlm)
    sh = doc["shots"][0]
    assert sh["bg_flat"] >= B.BG_FLAT and sh["ok_window"]
    # a pantalla completa (9:16 de alto completo) se le cortarian los lados a la pantalla del celular: no
    assert not sh["ok_full"] and "aparato" in sh["why"], sh["why"]
    wc = sh["win_crop"]
    assert wc is not None, sh
    asp = (wc[2] - wc[0]) * 640 / ((wc[3] - wc[1]) * 360)
    assert asp < 1.0, wc                                             # vertical, como el telefono
    assert wc[0] <= ph[0] + 0.01 and wc[2] >= ph[2] - 0.01            # el telefono entero de ancho
    assert (min(1.0, ph[3]) - ph[1]) / (wc[3] - wc[1]) >= 0.8         # y llena el alto de la tarjeta
    it = {"pieces": [{"shot": 0, "in": 0.1, "out": 2.0, "win": wc}]}
    card = RL.window_layout(it, 640, 360, 9 / 16, 0.66, 0.79)
    assert card["aspect"] < 1.0
    # un cuarto con cosas (fondo no liso) sigue como siempre: el cuadro completo
    rgb = np.random.default_rng(1).integers(0, 255, (180, 320, 3)).astype(np.uint8)
    assert B.bg_flat(rgb, [0.3, 0.1, 0.7, 0.9])[0] < B.BG_FLAT


def _slide_film(path, fps=30, w=320, h=180):
    """Una toma SIN cortes: 1.2 s del menu A (azul, quieto), un panel B (naranja) que entra deslizandose en
    0.27 s, y 1.6 s de B quieto; luego un corte a una toma C (verde) de 1.5 s."""
    import subprocess
    import cv2
    frames = []
    for k in range(int(4.6 * fps)):
        t = k / fps
        img = np.full((h, w, 3), (250, 245, 240), np.uint8)
        if t < 3.07:
            cv2.rectangle(img, (40, 30), (280, 150), (200, 120, 40), -1)            # A
            cv2.putText(img, "Menu", (60, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            u = min(1.0, max(0.0, (t - 1.2) / 0.27))
            if u > 0:
                x = int(w - u * (w - 40))
                cv2.rectangle(img, (x, 30), (x + 240, 150), (40, 140, 240), -1)      # B entra
                cv2.circle(img, (x + 120, 90), 30, (255, 255, 255), -1)
        else:
            cv2.rectangle(img, (40, 30), (280, 150), (60, 180, 60), -1)              # C
        frames.append(img)
    p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r",
                          str(fps), "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-pix_fmt",
                          "yuv420p", str(path)], stdin=subprocess.PIPE)
    p.stdin.write(b"".join(f.tobytes() for f in frames))
    p.stdin.close()
    assert p.wait() == 0
    return path


def test_una_animacion_de_la_interfaz_parte_la_toma():
    """El pedazo del menu abria con 2 cuadros del personaje deslizandose y el del personaje con 11 cuadros
    del menu anterior: las tomas solo se partian en los cortes. Una animacion de la interfaz (una racha corta
    de cambio entre tramos quietos) parte la toma y no se usa; una camara que se mueve parejo no."""
    s = np.zeros(int(4.6 * 60), np.float32) + 0.02
    s[::2] = 0.0                                             # 30 cps leido a 60
    s[int(1.2 * 60):int(1.47 * 60)] = 0.25                   # la animacion
    s[int(3.07 * 60)] = 2.5                                  # el corte
    got = B.split_bursts([(0.0, 3.07), (3.07, 4.6)], s, 60.0)
    assert len(got) == 3
    assert got[0][0] == 0.0 and abs(got[0][1] - 1.2) < 0.05 and abs(got[1][0] - 1.47) < 0.05
    assert all(not (a < 1.35 < b) for a, b, _x, _y in got)  # nadie cubre la mitad de la animacion
    # una camara que se mueve todo el tiempo (cambio parejo) no se parte
    pan = np.zeros(int(4.0 * 60), np.float32) + 0.12
    assert B.split_bursts([(0.0, 4.0)], pan, 60.0) == [(0.0, 4.0, True, True)]


def test_animacion_real_en_un_video(tmp_path):
    vid = _slide_film(tmp_path / "slide.mp4")
    doc = B.analyze(vid, cache_dir=tmp_path / "c", vlm_fn=lambda rgb, timeout=None: {
        "subject": "ui", "person_is_main": False, "text_seen": [], "what": "Una interfaz", "tags": ["interfaz"],
        "subject_box": [120, 160, 880, 840]})
    spans = [(x["a"], x["b"]) for x in doc["shots"]]
    assert not any(a < 1.32 < b for a, b in spans), spans    # la animacion (1.2-1.47) no es parte de una toma
    assert any(b <= 1.25 for a, b in spans) and any(1.4 <= a <= 1.6 for a, b in spans), spans
    assert any(abs(a - 3.07) < 0.05 for a, b in spans)       # y el corte de verdad sigue


def test_descripcion_en_ingles_se_muestra_en_espanol_y_no_esconde_personas():
    """El VLM a veces contesta en ingles ("A screen showing a chat message…"): el Studio mostraba eso. Se
    cambia por el sujeto en espanol y lo que se lee; una descripcion en ingles de una persona («A woman looks
    at a screen») sigue contando como persona."""
    f = {"people": 0.0, "face": 0.0, "sal_box": None}
    lab = B.merge_label(f, {"subject": "screen", "person_is_main": False, "text_seen": ["Ana"],
                            "what": "A screen showing a chat message from Ana", "tags": ["pantalla"],
                            "subject_box": [100, 100, 900, 900]})
    assert lab["what"] == "Pantalla con «Ana»" and lab["person_what"] is False
    lab = B.merge_label(f, {"subject": "screen", "person_is_main": False, "text_seen": [],
                            "what": "A woman looks at a screen in an office", "tags": ["screen"],
                            "subject_box": [100, 100, 900, 900]})
    assert lab["person_what"] is True and lab["person_seen"] is True and lab["what"] == "Pantalla"
    lab = B.merge_label(f, {"subject": "ui", "person_is_main": False, "text_seen": [],
                            "what": "Una pantalla muestra un chat de mensajes", "tags": [],
                            "subject_box": [100, 100, 900, 900]})
    assert lab["what"] == "Una pantalla muestra un chat de mensajes"
