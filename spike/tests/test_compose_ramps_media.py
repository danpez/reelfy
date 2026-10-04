"""compose.finish_plan: RECURSOS DEL GUION (plan.montage.inserts) -> plan.fx.media.

Los inserts vienen en s de la linea de tiempo del ensamble ("- Agregar <recurso>" al final de una
linea del guion). Tras componer se llevan al video combinado por su ancla en la FUENTE (clip +
segundo): cambiar la toma de OTRA linea no los mueve de su frase. Cada item de fx.media:
{id, start, end, sid, path, kind, fit, speed, src_in, style "float_card", enabled, locked, src
"script", name}. Material SINTETICO (lavfi), sin datos de clientes.
"""
import copy
import subprocess

import pytest

import compose
import paths

FF = str(paths.FFMPEG)
KEYS = {"id", "start", "end", "sid", "path", "kind", "fit", "speed", "src_in", "style", "enabled",
        "locked", "src", "name", "media_dur"}


@pytest.fixture(scope="module")
def assets(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    img = d / "imagen_prueba.png"
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=orange:s=216x270",
                    "-frames:v", "1", str(img)], check=True)
    vid = d / "video_prueba.mp4"
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=216x270:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=500:duration=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vid)], check=True)
    return {"d": d, "img": str(img), "vid": str(vid)}


def _mont():
    """Montaje de 3 partes (A, B, A mas adelante) con tres recursos: una imagen sobre la linea de
    B, un video sobre la de A2 y uno que falta."""
    return {"version": 1, "slots": [],
            "timeline": [
                {"slot": "s1", "cand": "c1", "sid": "ca", "in": 1.0, "out": 3.0, "start": 0.0, "join": None},
                {"slot": "s2", "cand": "c1", "sid": "cb", "in": 0.5, "out": 2.5, "start": 2.0, "join": "line"},
                {"slot": "s3", "cand": "c1", "sid": "ca", "in": 5.0, "out": 8.0, "start": 4.0,
                 "join": "section"}],
            "inserts": [
                {"id": "ins1", "line": "l2", "name": "imagen_demo", "sid": "img1", "kind": "image",
                 "start": 2.35, "end": 4.4, "fit": "hold", "speed": 1.0, "src_in": 0.0, "status": "ok"},
                {"id": "ins2", "line": "l3", "name": "clip_demo", "sid": "vid1", "kind": "video",
                 "start": 4.6, "end": 6.9, "fit": "speed", "speed": 1.3043, "src_in": 0.0, "status": "ok"},
                {"id": "ins3", "line": "l1", "name": "no_existe", "sid": None, "kind": None,
                 "start": 0.2, "end": 2.2, "fit": "hold", "speed": 1.0, "src_in": 0.0, "status": "missing"}]}


def _composed(parts):
    """info de compose() para partes contiguas [(sid, in, out, slot, cand)] (s del video = s de la
    linea de tiempo del ensamble, como las arma la UI)."""
    shots, segs, main, t = [], [], [], 0.0
    for i, (sid, a, b, slot, cand) in enumerate(parts):
        ln = b - a
        shots.append({"start": round(t, 3), "end": round(t + ln, 3), "index": i, "kind": "clip",
                      "src": f"{sid}.mov", "sid": sid, "slot": slot, "cand": cand,
                      "join": None if i == 0 else "line"})
        segs.append({"index": i, "start": round(t, 3), "end": round(t + ln, 3), "kind": "clip", "at": t,
                     "len": ln, "sid": sid, "in": a, "slot": slot, "cand": cand})
        main.append({"path": f"/x/{sid}.mov", "sid": sid, "type": "video", "in": a, "out": b, "start": t,
                     "layer": 0, "vol": 1.0,
                     "montage": {"slot": slot, "cand": cand, "join": None if i == 0 else "line"}})
        t += ln
    return {"shots": shots, "segs": segs, "main": main}, t


def _resolver(assets):
    return {"img1": assets["img"], "vid1": assets["vid"]}.get


def test_recursos_anclados_en_la_fuente_aunque_cambie_otra_toma(assets):
    """El usuario cambio la toma de la linea 1 por una mas corta (1.5 s en vez de 2): los recursos
    de las lineas 2 y 3 siguen sobre SUS frases (se corren 0.5 s), no sobre el mismo segundo."""
    mont = _mont()
    info, dur = _composed([("cc", 0.2, 1.7, "s1", "c2"), ("cb", 0.5, 2.5, "s2", "c1"),
                           ("ca", 5.0, 8.0, "s3", "c1")])
    plan = {"duration": dur}
    compose.finish_plan(plan, info, {"main": info["main"]}, montage=mont, resolve=_resolver(assets))
    allm = plan["fx"]["media"]
    # el faltante ENTRA sin archivo (sid/path None: el render lo omite con aviso y el Studio pide
    # subirlo en la pista Recursos); los demas con su archivo
    assert [m["id"] for m in allm] == ["ins3", "ins1", "ins2"]
    miss = allm[0]
    assert (miss["sid"], miss["path"], miss["kind"], miss["enabled"], miss["name"]) == (None, None, None, True, "no_existe")
    media = [m for m in allm if m["sid"]]
    for m in allm:
        assert set(m) == KEYS
        assert (m["style"], m["enabled"], m["locked"], m["src"]) == ("float_card", True, True, "script")
    a, b = media
    assert (a["start"], a["end"]) == (1.85, 3.9)          # 2.35 -> B+0.35 ; 4.4 -> A2+0.4
    assert (a["sid"], a["path"], a["kind"], a["fit"], a["speed"], a["src_in"], a["name"]) == \
        ("img1", assets["img"], "image", "hold", 1.0, 0.0, "imagen_demo")
    assert (b["start"], b["end"]) == (4.1, 6.4)
    # video de 3 s en un hueco de 2.3 s: <= 1.4x -> se acelera
    assert (b["kind"], b["fit"], b["src_in"]) == ("video", "speed", 0.0)
    assert b["speed"] == pytest.approx(3.0 / 2.3, abs=1e-3)
    # la duracion de su clip viaja con el recurso (el inspector acota «Desde qué segundo del clip»)
    assert b["media_dur"] == pytest.approx(3.0, abs=0.05) and a["media_dur"] is None and miss["media_dur"] is None
    # plan.montage.inserts: t0/t1 del video combinado, start/end en la linea de tiempo COMPUESTA y
    # el faltante sigue faltando (el ensamble muestra su aviso)
    ins = {i["id"]: i for i in plan["montage"]["inserts"]}
    assert (ins["ins1"]["t0"], ins["ins1"]["t1"], ins["ins1"]["start"], ins["ins1"]["end"]) == (1.85, 3.9, 1.85, 3.9)
    assert (ins["ins2"]["start"], ins["ins2"]["end"]) == (4.1, 6.4)
    # (su toma cambio: sin las dos anclas queda donde se vio en el video combinado, no en el
    # segundo viejo; asi recomponer no lo mueve)
    assert ins["ins3"]["status"] == "missing" and (ins["ins3"]["start"], ins["ins3"]["end"]) == (0.2, 1.7)
    assert mont["inserts"][0]["start"] == 2.35 and "t0" not in mont["inserts"][0]   # entrada intacta
    # recomponer con el plan ya compuesto (su linea de tiempo nueva) deja todo igual
    plan2 = {"duration": dur}
    compose.finish_plan(plan2, info, {"main": info["main"]}, montage=plan["montage"],
                        resolve=_resolver(assets))
    assert plan2["fx"]["media"] == allm


def test_inserts_del_ensamble_se_anclan_en_la_linea_de_tiempo_compuesta(assets):
    """La UI manda los recursos YA recolocados sobre los clips actuales (FXCore.placeInserts) y el
    servidor lo marca con spec["inserts_timeline"] = "main": se anclan en la linea de tiempo que se
    compone, no en la guardada del montaje (la de antes de cambiar la toma de la linea 1). Antes el
    final de la imagen caia 0.5 s antes (3.4 en vez de 3.9): se corria dos veces."""
    mont = _mont()
    mont["inserts"][0].update(start=1.85, end=3.9)          # como los recoloca el ensamble
    mont["inserts"][1].update(start=4.1, end=6.4)
    info, dur = _composed([("cc", 0.2, 1.7, "s1", "c2"), ("cb", 0.5, 2.5, "s2", "c1"),
                           ("ca", 5.0, 8.0, "s3", "c1")])
    plan = {"duration": dur}
    compose.finish_plan(plan, info, {"main": info["main"], "inserts_timeline": "main"}, montage=mont,
                        resolve=_resolver(assets))
    got = [(m["id"], m["start"], m["end"]) for m in plan["fx"]["media"] if m["sid"]]
    assert got == [("ins1", 1.85, 3.9), ("ins2", 4.1, 6.4)]
    ins = {i["id"]: i for i in plan["montage"]["inserts"]}
    assert (ins["ins1"]["start"], ins["ins1"]["end"]) == (1.85, 3.9)
    # sin la marca (un cliente que no recoloca) se toman como tiempos del montaje guardado
    plan2 = {"duration": dur}
    compose.finish_plan(plan2, info, {"main": info["main"]}, montage=_mont(), resolve=_resolver(assets))
    assert [(m["start"], m["end"]) for m in plan2["fx"]["media"] if m["sid"]] == [(1.85, 3.9), (4.1, 6.4)]


def test_sin_cambios_los_tiempos_son_los_del_ensamble(assets):
    mont = _mont()
    info, dur = _composed([("ca", 1.0, 3.0, "s1", "c1"), ("cb", 0.5, 2.5, "s2", "c1"),
                           ("ca", 5.0, 8.0, "s3", "c1")])
    plan = {"duration": dur}
    compose.finish_plan(plan, info, {"main": info["main"]}, montage=mont, resolve=_resolver(assets))
    got = [(m["id"], m["start"], m["end"]) for m in plan["fx"]["media"] if m["sid"]]
    assert got == [("ins1", 2.35, 4.4), ("ins2", 4.6, 6.9)]
    assert plan["fx"]["media"][2]["fit"] == "speed"


def test_la_toma_de_la_linea_del_recurso_cambio(assets):
    """Si cambio la toma de la MISMA linea (otro clip), el ancla ya no esta: se usa su lugar en la
    linea de tiempo del ensamble; y el ajuste del video se recalcula con el hueco nuevo."""
    mont = _mont()
    info, dur = _composed([("ca", 1.0, 3.0, "s1", "c1"), ("cb", 0.5, 2.5, "s2", "c1"),
                           ("cz", 0.0, 1.5, "s3", "c2")])          # A2 cambiada por una de 1.5 s
    plan = {"duration": dur}
    compose.finish_plan(plan, info, {"main": info["main"]}, montage=mont, resolve=_resolver(assets))
    m = {x["id"]: x for x in plan["fx"]["media"]}
    assert (m["ins1"]["start"], m["ins1"]["end"]) == (2.35, 4.4)
    # 4.6 -> A2 +0.6 ; 6.9 queda despues del final (5.5): acotado al video
    assert (m["ins2"]["start"], m["ins2"]["end"]) == (4.6, 5.5)
    assert (m["ins2"]["fit"], m["ins2"]["speed"]) == ("trim", 1.0)   # 3 s en 0.9 s: > 1.4x


def test_media_fit_reglas():
    f = compose._media_fit
    ins = {"fit": "hold", "speed": 1.0, "src_in": 0.0}
    assert f("image", None, 2.0, ins) == ("hold", 1.0, 0.0)
    assert f("video", 3.0, 2.5, ins) == ("speed", 1.2, 0.0)
    assert f("video", 3.0, 1.5, ins) == ("trim", 1.0, 0.0)
    # un poco mas corto (>= 0.8x del hueco): se alenta (= montage.plan_inserts / fxcore.insertFit)
    assert f("video", 3.0, 3.5, {"fit": "speed", "speed": 1.2}) == ("speed", 0.8571, 0.0)
    assert f("video", 3.0, 3.5, ins) == ("speed", 0.8571, 0.0)
    # mucho mas corto: dura lo suyo
    assert f("video", 2.0, 3.5, {"fit": "speed", "speed": 1.2}) == ("hold", 1.0, 0.0)
    assert f("video", 3.5, 3.5, ins) == ("hold", 1.0, 0.0)
    assert f("video", 10.0, 2.0, {"fit": "trim", "src_in": 4.0}) == ("trim", 1.0, 4.0)
    # el inicio que eligio el usuario se ACOTA para que el clip llene su hueco (antes volvia a 0: el
    # recurso salia desde el inicio del archivo)
    assert f("video", 5.0, 2.0, {"fit": "trim", "src_in": 4.0}) == ("trim", 1.0, 3.0)
    assert f("video", None, 2.0, {"fit": "speed", "speed": 1.3}) == ("speed", 1.3, 0.0)   # sin medir
    assert f("video", 3.0, 2.0, {"fit": "raro"}) == ("trim", 1.0, 0.0)


def test_sin_solaparse_y_sin_recursos_diminutos(assets):
    mont = _mont()
    mont["inserts"] = [
        {"id": "a", "name": "x", "sid": "img1", "kind": "image", "start": 0.5, "end": 3.0, "status": "ok"},
        {"id": "b", "name": "y", "sid": "img1", "kind": "image", "start": 2.2, "end": 4.5, "status": "ok"},
        {"id": "c", "name": "z", "sid": "img1", "kind": "image", "start": 4.6, "end": 4.9, "status": "ok"},
        {"id": "e", "name": "v", "sid": "img1", "kind": "image", "start": 4.3, "end": 4.45, "status": "ok"},
        {"id": "d", "name": "w", "sid": "img1", "kind": "image", "start": 5.0, "end": 4.0, "status": "ok"}]
    info, dur = _composed([("ca", 1.0, 3.0, "s1", "c1"), ("cb", 0.5, 2.5, "s2", "c1"),
                           ("ca", 5.0, 8.0, "s3", "c1")])
    plan = {"duration": dur}
    compose.finish_plan(plan, info, {"main": info["main"]}, montage=mont, resolve=_resolver(assets))
    got = [(m["id"], m["start"], m["end"]) for m in plan["fx"]["media"]]
    # "a" termina donde empieza "b"; "c" y "e" (diminutos) no entran y "e" no recorta a "b"
    assert got == [("a", 0.5, 2.2), ("b", 2.2, 4.5)]
    ins = {i["id"]: i for i in plan["montage"]["inserts"]}
    assert ins["c"]["status"] == "ok" and ins["c"]["t1"] - ins["c"]["t0"] < compose.MEDIA_MIN
    assert ins["e"]["t0"] == 4.3
    assert "t0" not in ins["d"]                                  # tiempos invalidos: se ignora


def test_resolucion_de_archivos_y_faltantes(assets, tmp_path, monkeypatch):
    mont = _mont()
    info, dur = _composed([("ca", 1.0, 3.0, "s1", "c1"), ("cb", 0.5, 2.5, "s2", "c1"),
                           ("ca", 5.0, 8.0, "s3", "c1")])
    # 1) spec["assets"] ([{sid, path}]) cuando el servidor no pasa `resolve`
    plan = {"duration": dur}
    spec = {"main": info["main"], "assets": [{"sid": "img1", "path": assets["img"]},
                                             {"sid": "vid1", "path": assets["vid"]}]}
    compose.finish_plan(plan, info, spec, montage=copy.deepcopy(mont))
    assert [m["path"] for m in plan["fx"]["media"]] == [None, assets["img"], assets["vid"]]
    # 2) como el servidor: input/sources/<sid>.* (la extension da el tipo si el insert no lo trae)
    src = tmp_path / "input" / "sources"
    src.mkdir(parents=True)
    (src / "img1.png").write_bytes(open(assets["img"], "rb").read())
    (src / "img1_proxy.mp4").write_bytes(b"x")
    monkeypatch.setattr(paths, "INPUT", tmp_path / "input")
    m2 = copy.deepcopy(mont)
    m2["inserts"][0]["kind"] = None
    plan = {"duration": dur}
    compose.finish_plan(plan, info, {"main": info["main"]}, montage=m2)
    assert [(m["id"], m["path"], m["kind"]) for m in plan["fx"]["media"]] == \
        [("ins3", None, None), ("ins1", str(src / "img1.png"), "image"), ("ins2", None, "video")]
    assert [m["sid"] for m in plan["fx"]["media"]] == [None, "img1", None]
    # el video ya no esta en disco: queda marcado como faltante (aviso en el ensamble)
    ins = {i["id"]: i for i in plan["montage"]["inserts"]}
    assert ins["ins2"]["status"] == "missing" and ins["ins1"]["status"] == "ok"


def test_conserva_los_recursos_del_usuario_y_no_duplica(assets):
    mont = _mont()
    info, dur = _composed([("ca", 1.0, 3.0, "s1", "c1"), ("cb", 0.5, 2.5, "s2", "c1"),
                           ("ca", 5.0, 8.0, "s3", "c1")])
    mine = {"id": "u1", "start": 0.1, "end": 1.0, "src": "user", "path": assets["img"]}
    plan = {"duration": dur, "fx": {"media": [dict(mine)], "captions": {"accent": "#FFD23F"}}}
    for _ in range(2):
        compose.finish_plan(plan, info, {"main": info["main"]}, montage=mont, resolve=_resolver(assets))
    ids = [m["id"] for m in plan["fx"]["media"]]
    assert ids == ["u1", "ins3", "ins1", "ins2"] and plan["fx"]["captions"] == {"accent": "#FFD23F"}


def test_sin_inserts_no_toca_fx():
    info, dur = _composed([("ca", 1.0, 3.0, "s1", "c1"), ("cb", 0.5, 2.5, "s2", "c1")])
    mont = _mont()
    mont.pop("inserts")
    plan = {"duration": dur}
    compose.finish_plan(plan, info, {"main": info["main"]}, montage=mont)
    assert "fx" not in plan
    mont["inserts"] = []
    compose.finish_plan(plan, info, {"main": info["main"]}, montage=mont)
    assert "fx" not in plan
    # sin montaje: nada de recursos
    plan = {"duration": dur}
    compose.finish_plan(plan, {"shots": info["shots"]}, {"main": [{"path": "a.mp4"}]})
    assert "fx" not in plan and "montage" not in plan


def test_de_punta_a_punta_con_compose(assets, tmp_path):
    """compose() real (montaje A -> B con union exacta) -> finish_plan: el recurso de la linea de B
    cae sobre B en el video combinado; info["segs"] trae el tramo de fuente de cada segmento."""
    d = tmp_path
    clips = {}
    for name, color, freq in (("ca", "white", 440), ("cb", "blue", 660)):
        p = d / f"{name}.mp4"
        subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=180x320:r=30:d=4",
                        "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration=4",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)],
                       check=True)
        clips[name] = str(p)
    main = [{"path": clips["ca"], "sid": "ca", "type": "video", "in": 0.5, "out": 2.5, "start": 0.0,
             "name": "A", "layer": 0, "vol": 1.0, "montage": {"slot": "s1", "cand": "c1", "join": None}},
            {"path": clips["cb"], "sid": "cb", "type": "video", "in": 1.0, "out": 3.0, "start": 2.0,
             "name": "B", "layer": 0, "vol": 1.0, "montage": {"slot": "s2", "cand": "c1", "join": "line"}}]
    mont = {"version": 1, "slots": [],
            "timeline": [{"slot": "s1", "cand": "c1", "sid": "ca", "in": 0.5, "out": 2.5, "start": 0.0},
                         {"slot": "s2", "cand": "c1", "sid": "cb", "in": 1.0, "out": 3.0, "start": 2.0}],
            "inserts": [{"id": "ins1", "line": "l2", "name": "marca", "sid": "img1", "kind": "image",
                         "start": 2.25, "end": 3.9, "fit": "hold", "speed": 1.0, "src_in": 0.0,
                         "status": "ok"}]}
    spec = {"fps": 30, "main": main, "assets": [{"sid": "img1", "path": assets["img"]}]}
    info = {}
    out = compose.compose(spec, str(d / "o.mp4"), d / "w", info=info)
    segs = info["segs"]
    assert [(s["sid"], s["in"], s["at"], s["len"], s["slot"]) for s in segs] == \
        [("ca", 0.5, 0.0, 2.0, "s1"), ("cb", 1.0, 2.0, 2.0, "s2")]
    assert "seg_in" not in info["shots"][0] and "in" not in info["shots"][0]   # info.shots como antes
    plan = {"duration": compose.probe(out)["dur"]}
    compose.finish_plan(plan, info, spec, montage=mont)
    (m,) = plan["fx"]["media"]
    b0 = info["shots"][1]["start"]
    assert m["start"] == pytest.approx(b0 + 0.25, abs=1e-3)
    assert m["end"] == pytest.approx(min(b0 + 1.9, plan["duration"]), abs=1e-3)
    assert m["path"] == assets["img"] and plan["montage"]["inserts"][0]["t0"] == m["start"]


def _two_clips():
    tl = [{"sid": "c1", "in": 0.0, "out": 10.0, "start": 0.0, "slot": "S1", "cand": "a"},
          {"sid": "c2", "in": 0.0, "out": 10.0, "start": 10.0, "slot": "S2", "cand": "a"}]
    info = {"segs": [{"start": 0.0, "end": 10.0, "kind": "clip", "sid": "c1", "in": 0.0, "at": 0.0, "len": 10.0,
                      "slot": "S1", "cand": "a"},
                     {"start": 10.0, "end": 20.0, "kind": "clip", "sid": "c2", "in": 0.0, "at": 10.0, "len": 10.0,
                      "slot": "S2", "cand": "a"}]}
    return tl, info


def test_recurso_quitado_no_ocupa_lugar(assets):
    """Un recurso QUITADO en el ensamble (enabled false; o sin lugar con tiempos viejos) no recorta ni
    descarta a uno visible: llega a fx.media oculto con su propio tramo (el Studio lo muestra apagado)."""
    tl, info = _two_clips()
    res = _resolver(assets)
    for b0, b1 in ((13.0, 17.0), (12.3, 16.5)):
        mont = {"timeline": tl, "inserts": [
            {"id": "C", "start": 12.0, "end": 16.0, "sid": "img1", "kind": "image", "status": "ok", "enabled": True},
            {"id": "B", "start": b0, "end": b1, "sid": "img1", "kind": "image", "status": "ok", "enabled": False}]}
        items, meta = compose.script_media(mont, info, 20.0, resolve=res, timeline=tl)
        got = {it["id"]: (it["start"], it["end"], it["enabled"]) for it in items}
        assert got == {"C": (12.0, 16.0, True), "B": (b0, b1, False)}, (b0, got)
        assert meta["C"]["status"] == "ok"
    # y uno VISIBLE si recorta al anterior (sin encimarse): C quedaria en 0.3 s (< MEDIA_MIN), no entra
    mont["inserts"][1]["enabled"] = True
    items, meta = compose.script_media(mont, info, 20.0, resolve=res, timeline=tl)
    assert [(it["id"], it["start"], it["end"]) for it in items] == [("B", 12.3, 16.5)]
    assert meta["C"]["status"] == "too_short"


def test_recurso_que_falta_llega_al_studio_para_subirlo(assets):
    """Un recurso que sigue faltando al componer entra a fx.media SIN archivo (sid/path None) en su
    lugar: el render lo omite con aviso ('media:<id>:no_encontrado') y el Studio pide subirlo."""
    tl, info = _two_clips()
    mont = {"timeline": tl, "inserts": [
        {"id": "L", "name": "imagen_logo", "start": 2.0, "end": 5.0, "sid": None, "kind": None, "status": "missing"},
        {"id": "sin_lugar", "name": "otro", "start": None, "end": None, "sid": None, "status": "missing"}]}
    items, meta = compose.script_media(mont, info, 20.0, resolve=_resolver(assets), timeline=tl)
    assert [(it["id"], it["sid"], it["path"], it["enabled"], it["name"], it["start"], it["end"]) for it in items] == \
        [("L", None, None, True, "imagen_logo", 2.0, 5.0)]
    assert meta["L"]["status"] == "missing" and "sin_lugar" not in meta
    from motion import render as R
    assert R.float_spec(items[0], {}) is None                      # el render lo salta (con aviso)
