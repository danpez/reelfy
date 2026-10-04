"""Videos de apoyo en el montaje: del catalogo (plan.montage.broll) a plan.fx.media al componer
(compose.broll_media / finish_plan), y lo que hace el servidor (montage.split_broll, /compose con
"broll", la miniatura de cada toma, Minimal con cortes secos)."""
import copy

import pytest

import compose
import montage as M
from test_brollplan import lib, lines


def _montage(lns, sid="t"):
    """Un ensamble de UNA toma larga que dice todas las frases (s de la fuente = s del video compuesto)."""
    lt = {L["id"]: [L["start"], L["end"]] for L in lns}
    return {"version": 1,
            "script": {"source": "user", "text": "\n".join(L["text"] for L in lns),
                       "lines": [{"id": L["id"], "text": L["text"]} for L in lns],
                       "sections": [{"id": L["section"], "lines": [L["id"]]} for L in lns]},
            "slots": [{"id": "s1", "lines": [L["id"] for L in lns], "chosen": "c1",
                       "cands": [{"id": "c1", "sid": sid, "in": 0.0, "out": 37.5, "line_times": lt}]}],
            "timeline": [{"slot": "s1", "cand": "c1", "sid": sid, "in": 0.0, "out": 37.5, "start": 0.0}],
            "glossary": "Nubis", "broll": {"version": 1, "sources": lib()}}


def _info(sid="t"):
    segs = [{"start": 0.0, "end": 37.5, "kind": "clip", "sid": sid, "in": 0.0, "at": 0.0, "len": 37.5,
             "slot": "s1", "cand": "c1"}]
    main = [{"sid": sid, "type": "video", "in": 0.0, "out": 37.5, "start": 0.0,
             "montage": {"slot": "s1", "cand": "c1", "join": None}}]
    return {"segs": segs, "main": main,
            "shots": [{"start": 0.0, "end": 37.5, "src": "t.mov", "slot": "s1", "cand": "c1", "sid": sid}]}


def test_finish_plan_pone_los_fragmentos_de_apoyo_en_fx_media():
    lns = lines()
    plan = {"duration": 37.5, "fx": {"media": [
        # un recurso del guion ya colocado: el apoyo no se le encima
        {"id": "in01", "src": "script", "style": "float_card", "start": 16.0, "end": 20.0, "enabled": True}]}}
    compose.finish_plan(plan, _info(), spec={"broll": [{"sid": "apoyo01", "path": "/x/apoyo.mp4"}]},
                        montage=_montage(lns))
    media = plan["fx"]["media"]
    reels = [m for m in media if m.get("style") == "reel"]
    assert reels and all(m["src"] == "broll" and m["locked"] and m["kind"] == "video" for m in reels)
    assert all(m["sid"] == "apoyo01" and m["path"] == "/x/apoyo.mp4" for m in reels)
    assert any(m["id"] == "in01" for m in media)                         # el del guion se queda
    for m in reels:
        assert not (m["start"] < 20.0 + 0.25 and 16.0 - 0.25 < m["end"]), m
        assert m["layout"] in ("window", "full") and 2 <= len(m["pieces"]) <= 5 and m["why"].startswith("Mientras dices")
        assert m["w"] == 1712 and m["h"] == 960 and m["media_dur"] == 29.0
    assert media == sorted(media, key=lambda it: float(it.get("start") or 0.0))
    # el montaje compuesto conserva el catalogo (el Studio cambia tomas con el)
    assert plan["montage"]["broll"]["sources"][0]["sid"] == "apoyo01"
    # recomponer reemplaza los fragmentos (no se duplican)
    compose.finish_plan(plan, _info(), spec={"broll": [{"sid": "apoyo01", "path": "/x/apoyo.mp4"}]},
                        montage=_montage(lns))
    assert len([m for m in plan["fx"]["media"] if m.get("style") == "reel"]) == len(reels)


def test_un_video_de_apoyo_quitado_no_se_usa_y_el_llm_solo_elige():
    lns = lines()
    m = _montage(lns)
    m["broll"]["sources"][0]["enabled"] = False
    plan = {"duration": 37.5, "fx": {"media": []}}
    compose.finish_plan(plan, _info(), spec={}, montage=m)
    assert not [x for x in plan["fx"]["media"] if x.get("style") == "reel"]
    calls = []

    def pick(text, shots, k):
        calls.append(k)
        return list(range(k))
    plan = {"duration": 37.5, "fx": {"media": []}}
    compose.finish_plan(plan, _info(), spec={}, montage=_montage(lns), broll_pick=pick)
    assert calls and [x for x in plan["fx"]["media"] if x.get("style") == "reel"]


def test_un_video_mudo_de_la_sesion_es_apoyo_y_no_toma(tmp_path):
    from test_montage import mk
    a = mk(tmp_path, "a1", "Les quiero contar de Nubis, un asistente para tus citas.", 0)
    b = mk(tmp_path, "b2", "Síganme para más.", 1)
    film = {"sid": "pel1", "name": "presentacion.mov", "dur": 147.0, "words": [], "audio": None, "w": 1712, "h": 960,
            "path": "/x/pel1.mov", "has_audio": False}
    short = {"sid": "corto", "name": "corto.mov", "dur": 1.0, "words": [], "audio": None}
    r = M.build([a, film, b, short], None)
    assert [x["sid"] for x in r["broll_src"]] == ["pel1"]
    assert r["broll_src"][0]["has_audio"] is False and r["broll_src"][0]["w"] == 1712
    assert all(t["sid"] != "pel1" for t in r["timeline"]) and "pel1" not in [o["sid"] for o in r["order"]]
    assert not any(d["sid"] == "pel1" for d in r["discarded"])
    assert r["stats"]["broll"] == ["pel1"]
    # un clip que no se pudo transcribir no es apoyo; y si NINGUN clip tiene voz, nada cambia
    bad = dict(film, sid="err", error="whisper fallo")
    assert [x["sid"] for x in M.build([a, bad], None)["broll_src"]] == []
    assert M.split_broll([film, short]) == ([], [film, short])


def test_una_pelicula_con_musica_es_apoyo_aunque_whisper_invente_palabras(tmp_path):
    """Whisper (ggml-large-v3-turbo) sobre un instrumental de 25 s escribe "Gracias por ver el video.": la
    pelicula de producto con soundtrack quedaba como TOMA de cierre (con su musica y ese subtitulo) y no se
    usaba como apoyo. Las frases que whisper inventa sobre musica, o unas pocas palabras sueltas en un clip
    largo, no son voz (montage.voiced)."""
    from test_montage import mk
    a = mk(tmp_path, "c1", "Hola, hoy les presento Nubis. Es un asistente que agenda tus citas.", minute=1)
    b = mk(tmp_path, "c2", "Nubis te contesta en segundos, a cualquier hora del dia.", minute=2)
    film = mk(tmp_path, "film", "Gracias por ver el video.", minute=3, tail=140.0)      # 147 s de musica
    film["has_audio"], film["w"], film["h"] = True, 1712, 960
    broll, rest = M.split_broll([a, b, film])
    assert [x["sid"] for x in broll] == ["film"] and broll[0]["has_audio"] is True
    assert [d["sid"] for d in rest] == ["c1", "c2"]
    r = M.build([a, b, film])
    assert [x["sid"] for x in r["broll_src"]] == ["film"]
    assert all(t["sid"] != "film" for t in r["timeline"])
    # otras alucinaciones tipicas sobre musica y palabras sueltas en un clip largo
    for txt in ("Subtítulos realizados por la comunidad de Amara.org", "¡Suscríbete! Gracias.", "Música",
                "Okay vale bien"):
        f2 = mk(tmp_path, "f2", txt, minute=4, tail=60.0)
        assert not M.voiced(f2), txt
    # una toma de verdad que solo dice "¡Muchas gracias!" (corta) o una frase larga sigue siendo voz
    assert M.voiced(mk(tmp_path, "g1", "¡Muchas gracias!", minute=5))
    assert M.voiced(mk(tmp_path, "g2", "Gracias por ver el video.", minute=5, tail=1.0))
    assert M.voiced(a) and M.voiced(b)
    long_take = mk(tmp_path, "g3", "Hoy les cuento como reservar una cita sin llamar a nadie, en dos pasos.",
                   minute=6, tail=8.0)
    assert M.voiced(long_take)


def test_servidor_videos_de_apoyo_y_minimal_con_cortes_secos(tmp_path):
    from test_server_setup import _run
    _run("""
import json
import server as S
# /compose: el usuario quito un video de apoyo en el ensamble; uno desconocido se ignora
mont = {"broll": {"version": 1, "sources": [{"sid": "p1", "name": "a.mov", "shots": [], "enabled": True},
                                             {"sid": "p2", "name": "b.mov", "shots": [], "enabled": True}]}}
b = S._broll_param([{"sid": "p1", "enabled": False}, {"sid": "zz", "enabled": False}], mont)
assert [x["enabled"] for x in b["sources"]] == [False, True] and mont["broll"]["sources"][0]["enabled"] is True
# Minimal: las aceleraciones automaticas se quitan; la que el usuario prendio se queda
main = [{"sid": "a", "montage": {"slot": "s1", "ramp": True}, "ramp_out": {"dur": 0.3, "speed": 2}},
        {"sid": "b", "montage": {"slot": "s2", "ramp": True, "ramp_user": True}, "ramp_in": {"dur": 0.3, "speed": 2}},
        {"sid": "c", "montage": {"slot": "s3"}, "ramp_in": {"dur": 0.3, "speed": 2}}, {"sid": "d"}]
S._minimal_cuts(main)
assert "ramp_out" not in main[0] and main[0]["montage"]["ramp"] is False
assert main[1]["ramp_in"] == {"dur": 0.3, "speed": 2}
assert "ramp_in" not in main[2]
# el resultado del montaje conserva los videos de apoyo de ESTA sesion
res = {"timeline": [], "broll_src": [{"sid": "p1", "name": "a.mov", "dur": 9.0, "w": 10, "h": 5, "has_audio": False,
                                      "path": "/x"}, {"sid": "ajeno", "name": "b"}]}
clips = [{"sid": "p1", "name": "a.mov", "rec": 0, "created": None}, {"sid": "c1", "name": "c.mov", "rec": 1, "created": None}]
m = S._montage_result(res, clips, {}, 0.1)
assert m["broll_src"] == [{"sid": "p1", "name": "a.mov", "dur": 9.0, "w": 10, "h": 5, "has_audio": False}], m["broll_src"]
# el catalogo de cada video de apoyo (brollscan.analyze en cache) queda en plan.montage.broll
import brollscan
doc = {"version": 1, "sig": "x", "name": "a.mov", "dur": 9.0, "w": 10, "h": 5, "fps": 30, "vlm": None,
       "shots": [{"i": 0, "a": 0.0, "b": 2.0, "subject": "ui", "ok_window": True, "ok_full": True,
                  "full": {"cx": 0.5}, "texts": [[0, 0, 1, 1]], "why": ""}]}
S._broll_analyze = lambda sid, on_step=None, cancel=None: (on_step and on_step(0, 1, "x"), doc)[1]
m2 = {"broll_src": [{"sid": "p1", "name": "a.mov"}]}
S._montage_broll(m2)
src = m2["broll"]["sources"][0]
assert src["sid"] == "p1" and src["enabled"] is True and src["shots"][0]["cx"] == 0.5 and "texts" not in src["shots"][0]
assert src["summary"]["usable"] == 1
# un video que no se pudo ver queda sin tomas y con su error (no tumba el montaje)
def boom(*a, **k):
    raise RuntimeError("ilegible")
S._broll_analyze = boom
m3 = {"broll_src": [{"sid": "p1", "name": "a.mov"}]}
S._montage_broll(m3)
assert m3["broll"]["sources"][0]["shots"] == [] and "ilegible" in m3["broll"]["sources"][0]["error"]
m4 = {"broll_src": []}
S._montage_broll(m4)
assert "broll" not in m4
print("OK")
""", tmp_path)


def test_miniatura_de_cada_toma(tmp_path):
    from test_server_setup import _run
    _run("""
import numpy as np, cv2, subprocess
import server as S, brollscan, paths
from fastapi import HTTPException
S.SOURCES.mkdir(parents=True, exist_ok=True)
src = S.SOURCES / "abcdef123456.mp4"
subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=4",
                "-pix_fmt", "yuv420p", str(src)], check=True)
def code(sid, i):
    try:
        S.broll_shot_thumb(sid, i)
        return 200
    except HTTPException as e:
        return e.status_code
assert code("abcdef123456", 0) == 404                                   # aun no se ve
d = brollscan._dir(src)
d.mkdir(parents=True, exist_ok=True)
cv2.imwrite(str(d / "s0.jpg"), np.zeros((9, 16, 3), np.uint8))
r = S.broll_shot_thumb("abcdef123456", 0)
assert r.media_type == "image/jpeg" and str(r.path).endswith("s0.jpg")
assert code("nada", 0) == 404
print("OK")
""", tmp_path)


def test_servidor_ve_un_video_de_apoyo_real_sin_vlm(tmp_path):
    """Sin ollama (reglas): el montaje ve el video mudo toma por toma (cortes, personas, interfaz) y deja
    su catalogo y la miniatura de cada toma; la segunda vez sale de la cache."""
    from test_server_setup import _run
    _run("""
import sys, time
sys.path.insert(0, "tests")
import server as S, brollscan
from _broll_media import make_film
S.SOURCES.mkdir(parents=True, exist_ok=True)
make_film(S.SOURCES / "abcdef654321.mp4", [("title", 1.5), ("screen", 2.0), ("character", 1.5), ("screen", 1.5)], fps=30,
          size=(320, 180))
m = {"broll_src": [{"sid": "abcdef654321", "name": "producto.mp4"}]}
t0 = time.time()
S._montage_broll(m)
src = m["broll"]["sources"][0]
assert src["vlm"] is None and len(src["shots"]) == 4, src
assert [round(s["a"], 2) for s in src["shots"]] == [0.0, 1.5, 3.5, 5.0]
# (sin VLM la interfaz sintetica -un panel claro de pie sobre un fondo oscuro liso- es un aparato: va en ventana
# recortada a el; a pantalla completa se le cortarian los lados)
assert src["shots"][1]["subject"] == "screen" and src["shots"][1]["ok_window"] and not src["shots"][1]["ok_full"]
assert "aparato" in src["shots"][1]["why"]
assert brollscan.thumb_path(S.SOURCES / "abcdef654321.mp4", 1) is not None
t1 = time.time()
m2 = {"broll_src": [{"sid": "abcdef654321", "name": "producto.mp4"}]}
S._montage_broll(m2)
assert m2["broll"]["sources"][0]["shots"] == src["shots"] and time.time() - t1 < max(0.5, (t1 - t0) / 2)
print("OK")
""", tmp_path)


def test_cancelar_mientras_se_ve_el_video_de_apoyo_cancela_el_trabajo(tmp_path):
    """Cancelar durante "Viendo tu video de apoyo (12/77 tomas)…" (unos 4 min con la pelicula del producto):
    JobCanceled (de la barra) o "cancelado" (del analisis) se tragaban como "video de apoyo ilegible", el
    trabajo seguia y terminaba en revision sin el apoyo. Ahora se propaga; un error de verdad no tumba."""
    from test_server_setup import _run
    _run("""
import server as S, brollscan, jobstore
S.SOURCES.mkdir(parents=True, exist_ok=True)
for n in ("b1", "b2"):
    (S.SOURCES / f"{n}.mov").write_bytes(b"x")
calls = []
def analyze_bar(src, on_step=None, cancel=None, **k):
    calls.append(src.name)
    on_step(0, 77, "Viendo...")
    raise AssertionError("no llega")
class Bar:
    def set(self, *a, **k):
        raise jobstore.JobCanceled()
class Canc:
    def __init__(self, c): self.c = c
    def canceled(self): return self.c
    def check(self):
        if self.c: raise jobstore.JobCanceled()
class Prog:
    def __init__(self, c): self.cancel = Canc(c)
brollscan.analyze = analyze_bar
m = {"broll_src": [{"sid": "b1", "name": "b1.mov"}, {"sid": "b2", "name": "b2.mov"}]}
try:
    S._montage_broll(m, Bar(), Prog(True))
    raise SystemExit("no se propago JobCanceled")
except jobstore.JobCanceled:
    pass
assert calls == ["b1.mov"] and "broll" not in m, (calls, m)
# "cancelado" del analisis (detect_cuts / analyze revisan cancel()) -> JobCanceled
def analyze_cancel(src, on_step=None, cancel=None, **k):
    raise RuntimeError("cancelado")
brollscan.analyze = analyze_cancel
try:
    S._montage_broll({"broll_src": [{"sid": "b1"}]}, None, Prog(True))
    raise SystemExit("no se propago el cancelado")
except jobstore.JobCanceled:
    pass
# un video de verdad ilegible (sin cancelar) sigue sin tumbar el montaje
def analyze_bad(src, on_step=None, cancel=None, **k):
    raise RuntimeError("video ilegible")
brollscan.analyze = analyze_bad
m = {"broll_src": [{"sid": "b1"}, {"sid": "b2"}]}
S._montage_broll(m, None, Prog(False))
assert [x["error"] for x in m["broll"]["sources"]] == ["video ilegible", "video ilegible"]
# _montage revisa la cancelacion justo despues de ver el apoyo
import inspect
src = inspect.getsource(S._montage)
i = src.index("_montage_broll(m, bar, prog)")
assert "prog.cancel.check()" in src[i:i + 200]
print("OK")
""", tmp_path)
