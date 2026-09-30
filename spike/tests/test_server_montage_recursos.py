"""app/server.py: recursos del guion y cambios de camara con aceleracion en "Armar con IA".

  - POST /montage {sids, assets?}: el servidor clasifica TODO lo elegido: una imagen o un video que
    el guion nombra ("- Agregar x", "[x]", "{x}") es RECURSO (nunca toma ni se transcribe); los demas
    videos son las tomas. Un montaje sin tomas se rechaza.
  - plan.montage.assets / inserts (los del motor o, si no los trae, colocados con las palabras de
    cada linea); una linea pierde su marca en el texto y la guarda en lines[i].inserts.
  - POST /montage/{job}/asset: el archivo que faltaba (o otro) se asigna a su recurso.
  - POST /compose: los recursos como quedaron en el ensamble (saneados, con la ruta del archivo) y
    las aceleraciones por tramo (ramp_in/ramp_out solo si la fuente tiene los cuadros de sobra; la
    union apagada por el usuario viaja en montage.ramp = false).
  - /sources trae `kind`; fx.media recibe la ruta de su archivo al cargar/guardar el plan.
  - Color de acento del proyecto (plan.fx.accent): lo que la IA pinta con el acento de su estilo
    pasa al del proyecto; sin acento propio todo queda igual (amarillo).

Todo SINTETICO e inventado (el repo es publico): taller de bicicletas ficticio, clips lavfi y whisper
y montage.build FALSOS (los de test_server_montage.py)."""
import json
import sqlite3
import subprocess

import pytest

import paths
from test_server_montage import _events, _upload, msrv, session  # noqa: F401 (fixtures)
from test_server_setup import _run
from test_sources_meta import clip

SCRIPT_R = ("Hola, soy Marta y reparo bicicletas en Querétaro. - Agregar foto_taller\n"
            "Arreglamos más de 200 bicis al mes en Rueda Libre. [Mostrar video_rueda]\n\n"
            "Síguenos para más. {cartel_falta}\n"
            "Te espero en el taller.")


def _png(path, color="orange", size="540x675"):
    subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d=1",
                    "-frames:v", "1", str(path)], check=True, timeout=60)
    return path


@pytest.fixture(scope="module")
def recursos(msrv, session, tmp_path_factory):  # noqa: F811
    d = tmp_path_factory.mktemp("recursos")
    foto = _png(d / "foto_taller.png")
    rueda = clip(d / "video_rueda.mp4", dur=3.0, size="360x640", audio=True, comment="ruido de taller",
                 freq=700)
    items = _upload(msrv["api"], [foto, rueda], transcribe="0")
    return {"foto": items[0], "rueda": items[1], "dir": d}


def test_sources_trae_kind_de_imagen_y_video(recursos):
    assert recursos["foto"]["type"] == recursos["foto"]["kind"] == "image"
    assert recursos["rueda"]["type"] == recursos["rueda"]["kind"] == "video"
    assert recursos["foto"]["words"] is None


def test_montage_clasifica_recursos_y_los_coloca(msrv, session, recursos):  # noqa: F811
    api, sid = msrv["api"], session["sid"]
    clips = [sid[k] for k in ("E", "D", "A", "F", "C", "B")]
    foto, rueda = recursos["foto"]["id"], recursos["rueda"]["id"]
    # solo recursos: no hay tomas que armar
    code, body, _ = api.post("/montage", {"sids": [foto], "script": SCRIPT_R}, raw=True)
    assert code == 400 and "recursos del guion" in body.decode()
    # el video nombrado viaja entre los sids y la imagen como recurso: el servidor clasifica
    job = api.post("/montage", {"sids": clips + [rueda], "assets": [foto], "script": SCRIPT_R,
                                "intercut": False, "director": {"enabled": False}})["job_id"]
    st = api.wait(job, lambda s: s["state"] in ("review", "error", "canceled"), 90)
    assert (st["state"], st["phase"]) == ("review", "assembly"), st
    b = _events(msrv, "build")[-1]
    assert rueda not in [c["sid"] for c in b["clips"]] and foto not in [c["sid"] for c in b["clips"]]
    # el motor recibe el guion tal cual (sus marcas las entiende montage.parse_script)
    assert b["script"] == SCRIPT_R
    cx = sqlite3.connect(msrv["data"] / "jobs.db")
    params = json.loads(cx.execute("SELECT params FROM jobs WHERE id=?", (job,)).fetchone()[0])
    cx.close()
    assert params["sids"] == clips and sorted(params["assets"]) == sorted([rueda, foto])
    assert {x["sid"] for x in params["asset_items"]} == {rueda, foto}     # la limpieza no los borra
    # las tomas se transcriben; los recursos nunca
    assert rueda not in {e["sid"] for e in _events(msrv, "transcribe")}
    m = api.get(f"/plan/{job}")["plan"]["montage"]
    assert {a["sid"]: a["kind"] for a in m["assets"]} == {foto: "image", rueda: "video"}
    lines = m["script"]["lines"]
    assert lines[0]["text"] == "Hola, soy Marta y reparo bicicletas en Querétaro."
    assert lines[0]["inserts"] == ["foto_taller"] and lines[1]["inserts"] == ["video_rueda"]
    assert lines[2]["inserts"] == ["cartel_falta"] and not lines[3].get("inserts")
    ins = {x["name"]: x for x in m["inserts"]}
    assert set(ins) == {"foto_taller", "video_rueda", "cartel_falta"}
    assert (ins["foto_taller"]["sid"], ins["foto_taller"]["kind"], ins["foto_taller"]["fit"]) == (foto, "image", "hold")
    assert ins["video_rueda"]["sid"] == rueda and ins["video_rueda"]["kind"] == "video"
    assert ins["video_rueda"]["fit"] in ("speed", "trim") and ins["video_rueda"]["path"].endswith(".mp4")
    assert ins["cartel_falta"]["status"] == "missing" and ins["cartel_falta"]["sid"] is None
    placed = sorted((x for x in m["inserts"] if x["start"] is not None), key=lambda x: x["start"])
    assert placed and all(x["end"] - x["start"] >= 0.3 for x in placed)
    for a, b2 in zip(placed, placed[1:]):
        assert a["end"] <= b2["start"] + 1e-6                               # nunca encimados
    tl_end = max(t["start"] + t["out"] - t["in"] for t in m["timeline"])
    assert all(0 <= x["start"] < tl_end for x in placed)
    recursos["job"] = job
    recursos["montage"] = m


def test_asignar_el_recurso_que_faltaba(msrv, recursos):
    api, job = msrv["api"], recursos["job"]
    nuevo = _upload(api, [_png(recursos["dir"] / "cartel_nuevo.png", color="teal")], transcribe="0")[0]
    assert api.post(f"/montage/{job}/asset", {"sid": nuevo["id"], "name": "nada_asi"}, raw=True)[0] == 404
    assert api.post(f"/montage/{job}/asset", {"sid": "ffffffffffff", "name": "cartel_falta"}, raw=True)[0] == 400
    r = api.post(f"/montage/{job}/asset", {"sid": nuevo["id"], "name": "Cartel Falta"})
    x = next(i for i in r["inserts"] if i["name"] == "cartel_falta")
    assert (x["sid"], x["kind"], x["status"], x["fit"]) == (nuevo["id"], "image", "ok", "hold")
    assert nuevo["id"] in {a["sid"] for a in r["assets"]}
    m = api.get(f"/plan/{job}")["plan"]["montage"]
    assert next(i for i in m["inserts"] if i["name"] == "cartel_falta")["sid"] == nuevo["id"]
    cx = sqlite3.connect(msrv["data"] / "jobs.db")
    params = json.loads(cx.execute("SELECT params FROM jobs WHERE id=?", (job,)).fetchone()[0])
    cx.close()
    assert nuevo["id"] in params["assets"] and {"sid": nuevo["id"]} in params["asset_items"]
    recursos["montage"] = m


def test_compose_lleva_recursos_y_aceleraciones(msrv, recursos):
    api, job, m = msrv["api"], recursos["job"], recursos["montage"]
    tl = m["timeline"]
    main = []
    for k, t in enumerate(tl):
        it = {"sid": t["sid"], "type": "video", "in": t["in"], "out": t["out"], "start": t["start"], "vol": 1.0,
              "montage": {"slot": t["slot"], "cand": t["cand"], "join": t["join"], "conf": 0.9}}
        main.append(it)
    main[0]["ramp_out"] = {"dur": 0.3, "speed": 2.0}          # la fuente dura 2 s: hay cuadros de sobra
    main[1]["ramp_in"] = {"dur": 0.3, "speed": 2.0}           # "in" ~0.05 s: sin cuadros antes -> fuera
    main[1]["montage"]["ramp"] = True
    main[2]["ramp_out"] = {"dur": 5.0, "speed": 2.0}          # mas larga que el tramo -> fuera
    main[3]["montage"].update(ramp=False, ramp_user=True)     # el usuario la apago
    ins = [dict(x) for x in m["inserts"]]
    ins[0]["enabled"] = False
    ins.append({"id": "zz9", "name": "ajeno", "start": 0, "end": 3})           # no es del montaje
    ins.append(dict(ins[1], id=ins[1]["id"], start=None))                    # sin tiempos: fuera
    body = {"montage_job": job, "main": main, "director": {"enabled": False}, "inserts": ins}
    assert api.post("/compose", body)["job_id"] == job
    cx = sqlite3.connect(msrv["data"] / "jobs.db")
    params = json.loads(cx.execute("SELECT params FROM jobs WHERE id=?", (job,)).fetchone()[0])
    spec = params["spec"]["main"]
    assert spec[0]["ramp_out"] == {"dur": 0.3, "speed": 2.0}
    assert "ramp_in" not in spec[1] and spec[1]["montage"]["ramp"] is True
    assert "ramp_out" not in spec[2]
    assert spec[3]["montage"]["ramp"] is False and spec[3]["montage"]["ramp_user"] is True
    assert params["assets"] and params["asset_items"]
    # los recursos llegaron recolocados por el ensamble: compose los ancla en la linea de tiempo compuesta
    assert params["spec"]["inserts_timeline"] == "main"
    # los recursos del ensamble (sin el ajeno ni el de tiempos invalidos), con la ruta del archivo
    st = api.wait(job, lambda s: s["state"] in ("review", "error", "canceled") and s["kind"] == "compose", 180)
    assert st["state"] == "review", st
    plan = api.get(f"/plan/{job}")["plan"]
    pm = plan["montage"]
    got = {x["id"]: x for x in pm["inserts"]}
    assert "zz9" not in got and len(got) == len(m["inserts"])
    assert got[ins[0]["id"]]["enabled"] is False
    for x in got.values():
        if x["sid"]:
            assert x["path"].startswith(str(msrv["data"])) and x["status"] == "ok"
    # lo que compose llevo al video combinado (plan.fx.media): solo lo activo y con archivo
    media = (plan.get("fx") or {}).get("media")
    if media is not None:
        on = {x["id"] for x in got.values() if x["sid"] and x["enabled"]}
        assert {x["id"] for x in media if x.get("enabled", True)} <= on
        # el quitado llega oculto (el Studio lo muestra apagado para volver a ponerlo)
        assert all(x["enabled"] is False for x in media if x["id"] == ins[0]["id"])
        assert all(x.get("style") == "float_card" and x.get("path") for x in media)
    spec_ins = params["spec"].get("inserts") or []
    assert all(x["sid"] and x["enabled"] for x in spec_ins)
    cx.close()


def test_helpers_recursos_rampas_acento_y_rutas(tmp_path):
    """En proceso aparte: marcas del guion (reglas de montage.py), ajuste de un medio al hueco,
    rampas saneadas, recursos de /compose, rutas de fx.media y color de acento."""
    _run('''
import server as S
# marcas del guion (mismas reglas que montage.split_annotation / fxcore.lineRefs)
assert S._line_refs("Ya abrimos. - Agregar video_horno") == ("Ya abrimos.", ["video_horno"])
assert S._line_refs("Hola.- AÑADIR Foto Pan.jpg") == ("Hola.", ["Foto Pan.jpg"])
assert S._line_refs("Hola [pausa]") == ("Hola", [])
assert S._line_refs("Dos {logo} [insertar foto_a, foto_b]") == ("Dos", ["logo", "foto_a", "foto_b"])
assert S._line_refs("un e-mail bien") == ("un e-mail bien", [])
assert S._strip_refs("Uno. - Agregar x_1\\n\\nDos [y_2]") == "Uno.\\n\\nDos"
assert S._asset_norm("Video Horno-Leña.MP4") == "video_horno_lena"
assert S._match_asset("video_horno", ["otro.mov", "Video Horno.mp4"]) == 1
assert S._match_asset("video", ["video_a.mp4", "video_b.mp4"]) == -1          # prefijo no unico
assert S._match_asset("foto_pan", ["foto_pan_dulce.jpg"]) == 0                 # prefijo unico
secs, lines = S._script_parse("Uno. - Agregar a_1\\nDos.\\n\\nTres {b_2}")
assert [L["text"] for L in lines] == ["Uno.", "Dos.", "Tres"] and lines[0]["inserts"] == ["a_1"]
assert lines[2]["inserts"] == ["b_2"] and len(secs) == 2
# glosario de whisper: nunca el nombre de un recurso ni el verbo
g = S._script_glossary("Soy Marta de Rueda Libre. - Agregar video_rueda", "")
assert "Marta" in g and "Agregar" not in g and "video_rueda" not in g
# ajuste de un medio al hueco (montage.plan_inserts)
assert S._insert_fit("image", 0, 3.0) == {"fit": "hold", "speed": 1.0, "src_in": 0.0}
assert S._insert_fit("video", 3.6, 3.0) == {"fit": "speed", "speed": 1.2, "src_in": 0.0}
assert S._insert_fit("video", 2.7, 3.0)["fit"] == "speed"                      # un poco corto: 0.9x
assert S._insert_fit("video", 9.0, 3.0) == {"fit": "trim", "speed": 1.0, "src_in": 0.0}
f = S._insert_fit("video", 1.0, 3.0)
assert f["fit"] == "trim" and f["len"] == 1.0                                  # corto: dura lo suyo
# rampas: la fuente dice cuantos cuadros hay (sin metadatos: no se sabe el final, se deja)
assert S._ramp_param({"dur": 0.3, "speed": 2}, "ffffffffffff", 1.0, 3.0, "ramp_in") == {"dur": 0.3, "speed": 2.0}
assert S._ramp_param({"dur": 0.3, "speed": 2}, "ffffffffffff", 0.1, 3.0, "ramp_in") is None
assert S._ramp_param({"dur": 0.3, "speed": 1.0}, "ffffffffffff", 1.0, 3.0, "ramp_in") is None
assert S._ramp_param({"dur": 3.0}, "ffffffffffff", 1.0, 2.0, "ramp_out") is None      # mas larga que el tramo
assert S._ramp_param({"dur": 9, "speed": 9}, "ffffffffffff", 1.0, 20.0, "ramp_out") == {"dur": 1.5, "speed": 4.0}
assert S._ramp_param("x", "ffffffffffff", 1.0, 3.0, "ramp_out") is None
assert S._montage_item({"slot": "s1", "join": "splice", "ramp": False, "ramp_user": True}) == \\
    {"slot": "s1", "join": "splice", "ramp": False, "ramp_user": True}
# recursos de /compose: solo los del montaje, con tiempos validos
mont = {"inserts": [{"id": "i01", "line": "L01", "name": "a"}, {"id": "i02", "line": "L02", "name": "b"}]}
got = S._inserts_param([{"id": "i01", "start": 1, "end": 3.5, "fit": "raro", "speed": 99, "enabled": False},
                        {"id": "i02", "start": 2, "end": 2.05}, {"id": "i99", "start": 0, "end": 3}, "x"], mont)
assert [x["id"] for x in got] == ["i01"] and got[0]["fit"] == "hold" and got[0]["speed"] == 4.0
assert got[0]["enabled"] is False and got[0]["status"] == "missing" and got[0]["line"] == "L01"
# fx.media: la ruta sale del sid (el Studio no la conoce); sin archivo, None
src = S.SOURCES / "abcdef123456.png"
S.SOURCES.mkdir(parents=True, exist_ok=True)
src.write_bytes(b"x")
p = {"version": 2, "fx": {"media": [{"id": "md1", "sid": "abcdef123456"}, {"id": "md2", "sid": "0123456789ab", "path": "/viejo"}]}}
S._media_paths(p)
assert p["fx"]["media"][0]["path"] == str(src) and p["fx"]["media"][0]["kind"] == "image"
assert p["fx"]["media"][1]["path"] is None
# color de acento: sin acento propio nada cambia (amarillo de siempre)
def plan(style="dinamico", acc=None):
    fx = {"captions": {"accent": "#FFD23F"},
          "text": [{"id": "tx1", "text": "Hola", "accent_words": ["hola"], "start": 1, "end": 2, "x": 0.5,
                    "lines": [{"text": "Hola", "color": "#ffd23f"}]}],
          "graphics": [{"id": "gr1", "kind": "card", "accent": "#FFD23F", "color": "#FFFFFF", "start": 1, "end": 2, "x": 0.5},
                       {"id": "gr2", "kind": "cta", "bg": "#E8C27A", "start": 3, "end": 4, "x": 0.5}]}
    if acc:
        fx["accent"] = acc
    return {"version": 2, "director": {"style": style}, "fx": fx}
p0 = plan()
assert S._accent_apply(p0) is p0 and p0["fx"]["graphics"][0]["accent"] == "#FFD23F" and "accent" not in p0["fx"]["text"][0]
p1 = S._accent_apply(plan(acc="#3366ff"))
assert p1["fx"]["captions"]["accent"] == "#3366FF" or p1["fx"]["captions"]["accent"] == "#3366ff"
assert S._hex(p1["fx"]["captions"]["accent"]) == "#3366FF"
assert p1["fx"]["text"][0]["lines"][0]["color"] == "#3366FF" and p1["fx"]["text"][0]["accent"] == "#3366FF"
assert p1["fx"]["graphics"][0]["accent"] == "#3366FF" and p1["fx"]["graphics"][0]["color"] == "#FFFFFF"
assert p1["fx"]["graphics"][1]["bg"] == "#E8C27A"                     # no es el acento de este estilo
p2 = S._accent_apply(plan(style="elegante", acc="#3366FF"))
assert p2["fx"]["graphics"][1]["bg"] == "#3366FF"                     # el acento del estilo elegante
p3 = S._accent_apply(plan(style="minimal", acc="#FFFFFF"))
assert p3["fx"]["graphics"][0]["color"] == "#FFFFFF" and p3["fx"]["captions"]["accent"] == "#FFFFFF"
# el director devuelve un plan sin fx.accent: se conserva el del proyecto
out = S._accent_keep(plan(acc="#3366FF"), plan())
assert out["fx"]["accent"] == "#3366FF"
assert S._sym(plan(acc="#3366FF"))["fx"]["graphics"][0]["accent"] == "#3366FF"
print("OK")
''', tmp_path)


def test_limpieza_del_disco_conserva_los_recursos_de_un_armado_sin_plan(tmp_path):
    """Un armado que fallo (sin plan) y se reintenta dias despues: la limpieza del disco conserva sus
    tomas (params.sids) Y sus recursos del guion (params.assets / asset_items); lo que nadie usa se va."""
    _run('''
import os, time
import server as S
import jobstore
S.SOURCES.mkdir(parents=True, exist_ok=True)
toma, img, vid, suelta = "a1a1a1a1a1a1", "b2b2b2b2b2b2", "c3c3c3c3c3c3", "d4d4d4d4d4d4"
for sid, ext in ((toma, ".mp4"), (img, ".png"), (vid, ".mp4"), (suelta, ".png")):
    f = S.SOURCES / f"{sid}{ext}"
    f.write_bytes(b"x")
    os.utime(f, (1000, 1000))                       # subidas hace mucho
assert jobstore.params_sids({"sids": [toma], "assets": [img], "asset_items": [{"sid": vid}, "x", {"sid": 3}]}) == {toma, img, vid}
assert jobstore.params_sids({"sids": "abc", "assets": None}) == set()
jobstore.create("m1", kind="montage", ext=".mp4", phase="assembly",
                params={"sids": [toma], "assets": [img, vid], "asset_items": [{"sid": img}, {"sid": vid}]})
jobstore.update("m1", state="error", error="motor de voz no listo")
S._gc_sources(now=time.time())
left = {p.name.split(".")[0] for p in S.SOURCES.iterdir()}
assert {toma, img, vid} <= left and suelta not in left, left
print("OK")
''', tmp_path)


def test_marcas_con_guion_largo_y_nombres_parecidos_en_el_servidor(tmp_path):
    """La version minima del servidor (motor sin split_annotation/match_asset) sigue las MISMAS reglas:
    una frase con guion largo y verbo se dice; '1' o 'a' nunca por prefijo; al reves solo imagenes."""
    _run('''
import server as S
S._mt = lambda name: None                      # sin montage.py: la version minima
for l in ("Nuestra meta — agregar valor a cada familia del barrio.", "Súmate — agrega tu negocio hoy.",
          "Hay una regla | pon tu marca primero.", "La clave — poner al cliente primero"):
    assert S._line_refs(l) == (l, []), l
assert S._line_refs("Ya abrimos. - Agregar video_horno") == ("Ya abrimos.", ["video_horno"])
assert S._line_refs("Hola.- AÑADIR Foto Pan.jpg") == ("Hola.", ["Foto Pan.jpg"])
assert S._match_asset("1", ["1f3a.mov", "IMG_0375.MOV"]) == -1
assert S._match_asset("marta_en_el_taller", ["Marta.mov", "Marta 2.mov"]) == -1
assert S._match_asset("logo_nuevo", ["logo.png"]) == 0
assert S._match_asset("foto_pan", ["foto_pan_dulce.jpg"]) == 0
# y con el motor: una toma ya transcrita no pasa a recurso por un nombre solo parecido
import montage
S._mt = lambda name: getattr(montage, name, None)
files = [{"sid": "t1", "name": "toma_pan_1.mov", "kind": "video", "has_words": True}]
assert montage.classify(files, "Hola. {toma_pan}")["refs"] == {"toma_pan": None}
print("OK")
''', tmp_path, env_extra={"PYTHONPATH": "scripts"})
