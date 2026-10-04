"""Armar con IA + grabaciones de pantalla sincronizadas (montage.detect_screens / build(screens_off)),
el mapa al video compuesto (compose.screen_media / finish_plan) y la clasificacion por metadatos
(compose.capture, server._screen_like). Todo SINTETICO: voces con forma de voz (tests/test_screensync)
escritas a wav, palabras inventadas, videos de prueba de ffmpeg."""
import json
import subprocess
import wave

import numpy as np
import pytest

import compose
import montage
import screensync as S
from test_screensync import voice

SR = 16000


def wav(path, y, sr=SR):
    y = np.clip(np.asarray(y, np.float32), -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype(np.int16).tobytes())
    return str(path)


def words(text, t0, t1):
    toks = text.split()
    step = (t1 - t0) / len(toks)
    return [{"text": w, "start": round(t0 + i * step, 3), "end": round(t0 + (i + 0.8) * step, 3)}
            for i, w in enumerate(toks)]


INTRO = "Hoy les enseño una herramienta de notas que uso para organizar la semana del taller."
DEMO = ("Primero abro el calendario de la aplicación y elijo el martes en la tarde para la entrega. "
        "Le pongo una alarma de treinta minutos antes. Listo, la entrega quedó guardada en el calendario.")
CHAT = "Un segundo, déjame abrir la aplicación. Ok, empiezo."
OUTRO = "Si les sirvió, guárdenlo para después y prueben la herramienta."


@pytest.fixture()
def session(tmp_path):
    rng = np.random.default_rng(3)
    v_intro, _ = voice(9.0, SR, 50)
    v_demo, _ = voice(32.0, SR, 51)
    v_chat, _ = voice(12.0, SR, 52)
    v_outro, _ = voice(7.0, SR, 53)
    noise = lambda n, db: rng.standard_normal(n) * 10 ** (db / 20)  # noqa: E731
    cam1 = wav(tmp_path / "c1.wav", v_intro * 0.8 + noise(len(v_intro), -50))
    cam2 = wav(tmp_path / "c2.wav", v_demo * 0.8 + noise(len(v_demo), -50))
    cam3 = wav(tmp_path / "c3.wav", v_outro * 0.8 + noise(len(v_outro), -50))
    # pantalla: 12 s de charla propia (preparar la app) + la demo (mas bajita) + 4 s despues
    scr_y = np.concatenate([v_chat, v_demo * 0.3, voice(4.0, SR, 54)[0] * 0.5])
    scr = wav(tmp_path / "s.wav", scr_y + noise(len(scr_y), -44))
    clips = [
        {"sid": "c1", "name": "IMG_0001.MOV", "dur": 9.0, "words": words(INTRO, 0.4, 8.6), "audio": cam1,
         "takes": [], "capture": "camera"},
        {"sid": "c2", "name": "IMG_0002.MOV", "dur": 32.0, "words": words(DEMO, 0.5, 31.0), "audio": cam2,
         "takes": [], "capture": "camera"},
        {"sid": "c3", "name": "IMG_0003.MOV", "dur": 7.0, "words": words(OUTRO, 0.3, 6.6), "audio": cam3,
         "takes": [], "capture": "camera"},
        # la grabacion de pantalla trae su propia transcripcion: la charla y la demo otra vez
        {"sid": "sc", "name": "ScreenRecording_01.mov", "dur": 48.0, "w": 670, "h": 1436,
         "words": words(CHAT, 0.5, 11.0) + words(DEMO, 12.5, 43.0), "audio": scr, "takes": [], "capture": "screen"},
    ]
    return clips


def test_pantalla_sincronizada_nunca_es_toma(session):
    m = montage.build(session, llm=None)
    assert [x["sid"] for x in m["screens"]] == ["sc"]
    sc = m["screens"][0]
    assert sc["cam"] == "c2" and sc["cam_name"] == "IMG_0002.MOV"
    assert abs(sc["offset"] - 12.0) < 0.003, sc          # la pantalla empezo 12 s antes que la camara
    assert sc["corr"] >= 0.8 and sc["enabled"] is True and sc["w"] == 670
    assert sc["overlap"][0] == pytest.approx(0.0, abs=0.05) and sc["overlap"][1] == pytest.approx(32.0, abs=0.1)
    # ni toma, ni descartada, ni en el orden: su charla de preparacion nunca llega al video
    assert all(o["sid"] != "sc" for o in m["order"])
    assert all(t["sid"] != "sc" for t in m["timeline"])
    assert all(d["sid"] != "sc" for d in m["discarded"])
    assert all(c["sid"] != "sc" for s in m["slots"] for c in s["cands"])
    assert "segundo" not in m["script"]["text"] and "empiezo" not in m["script"]["text"]
    assert {t["sid"] for t in m["timeline"]} == {"c1", "c2", "c3"}
    assert m["stats"]["screens"][0]["cam"] == "c2"


def test_desfase_en_segundos_de_los_archivos(session):
    """El wav de cada clip empieza en su 1a muestra de audio: la grabacion de pantalla del iPhone trae
    el audio 0.128 s despues de su primer cuadro, asi que su cuadro es el del wav + 0.128 (y el de la
    camara, al reves). El desfase del contrato es el de los ARCHIVOS; el de los wavs queda aparte."""
    clips = [dict(c) for c in session]
    clips[3]["a_lead"] = 0.128
    clips[1]["a_lead"] = 0.02
    sc = montage.build(clips, llm=None)["screens"][0]
    assert sc["audio_offset"] == pytest.approx(12.0, abs=0.003)
    assert sc["offset"] == pytest.approx(12.0 + 0.128 - 0.02, abs=0.003)
    assert sc["lead"] == [0.02, 0.128]
    assert sc["overlap"][0] == pytest.approx(0.02, abs=0.05) and sc["other"][0] == pytest.approx(12.128, abs=0.05)
    assert sc["crop"] is None and sc["content"] is None          # sin archivo no hay marco que mirar


def test_una_grabacion_que_cubre_dos_tomas(tmp_path, session):
    """Se siguio grabando la pantalla mientras se grababan DOS tomas: una entrada por toma, cada una con
    su desfase; ninguna de las dos tomas ni la pantalla se vuelven toma de la otra."""
    clips = [dict(c) for c in session]
    rng = np.random.default_rng(9)
    y1, _ = S.read_wav(clips[0]["audio"])
    y2, _ = S.read_wav(clips[1]["audio"])
    chat, _ = voice(6.0, SR, 77)
    scr_y = np.concatenate([chat * 0.5, y1 * 0.3, np.zeros(int(2.0 * SR)), y2 * 0.3, np.zeros(SR)])
    clips[3] = dict(clips[3], audio=wav(tmp_path / "s2.wav", scr_y + rng.standard_normal(len(scr_y)) * 10 ** (-44 / 20)),
                    dur=len(scr_y) / SR)
    m = montage.build(clips, llm=None)
    got = [(x["cam"], round(x["offset"], 2)) for x in m["screens"]]
    assert got == [("c1", 6.0), ("c2", round(6.0 + 9.0 + 2.0, 2))], got
    assert [x["id"] for x in m["screens"]] == ["sc01", "sc02"] and {x["sid"] for x in m["screens"]} == {"sc"}
    assert {t["sid"] for t in m["timeline"]} == {"c1", "c2", "c3"}


def test_deshacer_no_es_grabacion_de_pantalla(session):
    m = montage.build(session, llm=None, screens_off=["sc"])
    assert m["screens"] == []
    assert any(o["sid"] == "sc" for o in m["order"])          # vuelve a ser un clip de la sesion


def test_dos_camaras_que_suenan_igual_no_son_pantalla(session):
    clips = [dict(c) for c in session]
    clips[3]["capture"] = "camera"                           # multicamara: otro angulo de la misma toma
    m = montage.build(clips, llm=None)
    assert m["screens"] == []


def test_sin_metadatos_solo_si_parece_pantalla(session):
    clips = [dict(c) for c in session]
    clips[3]["capture"] = None
    assert montage.build(clips, llm=None)["screens"] == []
    clips[3]["screen_like"] = True
    assert [x["sid"] for x in montage.build(clips, llm=None)["screens"]] == ["sc"]


def test_pantalla_sin_coincidencia_sigue_siendo_toma(tmp_path, session):
    clips = [dict(c) for c in session]
    other, _ = voice(40.0, SR, 99)
    clips[3]["audio"] = wav(tmp_path / "otra.wav", other)
    m = montage.build(clips, llm=None)
    assert m["screens"] == []
    assert any(o["sid"] == "sc" for o in m["order"])


def test_screen_like_por_metadatos():
    assert montage.screen_like({"capture": "screen"}) is True
    assert montage.screen_like({"capture": "camera", "screen_like": True}) is False
    assert montage.screen_like({"capture": None, "screen_like": True}) is True
    assert montage.screen_like({}) is False


# ----------------------------------------------------------------------------- metadatos (ffmpeg)
def _mk(path, tags):
    import paths
    cmd = [str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x320:rate=30:duration=0.5",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    for k, v in tags.items():
        cmd += ["-metadata", f"{k}={v}"]
    subprocess.run(cmd + ["-movflags", "use_metadata_tags", str(path)], check=True, timeout=60)
    return path


def test_capture_por_metadatos(tmp_path):
    assert compose.capture(_mk(tmp_path / "a.mov", {"com.apple.quicktime.author": "ReplayKitRecording"})) == "screen"
    assert compose.capture(_mk(tmp_path / "b.mov", {"com.apple.quicktime.make": "Apple",
                                                     "com.apple.quicktime.model": "iPhone"})) == "camera"
    assert compose.capture(_mk(tmp_path / "c.mov", {"comment": "Screen Recorder"})) == "screen"
    assert compose.capture(_mk(tmp_path / "d.mov", {})) is None
    p = compose.probe(tmp_path / "a.mov")
    assert p["capture"] == "screen"


# ----------------------------------------------------------------------------- video compuesto
def _info(segs):
    return {"segs": segs, "shots": [{"start": s["start"], "end": s["end"], "src": s["sid"]} for s in segs]}


def test_screen_media_sigue_los_cortes_del_montaje():
    mont = {"screens": [{"id": "sc01", "sid": "sc", "name": "ScreenRecording_01.mov", "cam": "c2",
                         "cam_name": "IMG_0002.MOV", "offset": 12.0, "rate": 1.0, "dur": 48.0, "w": 670,
                         "h": 1436, "enabled": True, "crop": [0.02836, 0.01811, 0.97164, 0.97354]}]}
    segs = [{"start": 0.0, "end": 8.0, "kind": "clip", "sid": "c1", "in": 0.4},
            {"start": 8.0, "end": 20.0, "kind": "clip", "sid": "c2", "in": 0.5},
            {"start": 20.0, "end": 37.0, "kind": "clip", "sid": "c2", "in": 14.0},   # el montaje corto 1.5 s
            {"start": 37.0, "end": 43.0, "kind": "clip", "sid": "c3", "in": 0.3}]
    items = compose.screen_media(mont, _info(segs), 43.0, spec={"screens": [{"sid": "sc", "path": "/x/sc.mov"}]})
    assert len(items) == 1
    it = items[0]
    assert it["style"] == "screen" and it["src"] == "screen" and it["kind"] == "video" and it["locked"]
    assert (it["start"], it["end"]) == (8.0, 37.0) and it["path"] == "/x/sc.mov"
    assert it["sync"]["pieces"] == [[8.0, 20.0, 0.5], [20.0, 37.0, 14.0]]
    assert it["crop"] == [0.02836, 0.01811, 0.97164, 0.97354] and it["media_dur"] == 48.0
    from motion import screen as SC
    assert SC.sync_time(it, 8.0) == pytest.approx(12.5)
    assert SC.sync_time(it, 20.0) == pytest.approx(26.0)       # despues del corte: 14 s de camara
    assert SC.sync_time(it, 36.9) == pytest.approx(12.0 + 14.0 + 16.9)


def test_screen_media_dos_tramos_y_quitada():
    mont = {"screens": [{"id": "sc01", "sid": "sc", "cam": "c2", "offset": 3.0, "dur": 60.0, "enabled": False}]}
    segs = [{"start": 0.0, "end": 5.0, "kind": "clip", "sid": "c2", "in": 0.0},
            {"start": 5.0, "end": 9.0, "kind": "clip", "sid": "c1", "in": 0.0},
            {"start": 9.0, "end": 15.0, "kind": "clip", "sid": "c2", "in": 20.0}]
    items = compose.screen_media(mont, _info(segs), 15.0, spec={"screens": [{"sid": "sc", "path": "/p.mov"}]})
    assert [(i["id"], i["start"], i["end"]) for i in items] == [("sc01", 0.0, 5.0), ("sc01_2", 9.0, 15.0)]
    assert all(i["enabled"] is False for i in items)


def test_finish_plan_pone_la_pantalla_en_fx_media():
    mont = {"version": 1, "timeline": [], "slots": [],
            "screens": [{"id": "sc01", "sid": "sc", "cam": "c2", "offset": 12.0, "dur": 48.0, "enabled": True}]}
    segs = [{"start": 0.0, "end": 10.0, "kind": "clip", "sid": "c2", "in": 1.0}]
    info = dict(_info(segs), main=[])
    plan = {"duration": 10.0, "fx": {"media": [{"id": "old", "src": "screen", "start": 0, "end": 1}]}}
    compose.finish_plan(plan, info, spec={"screens": [{"sid": "sc", "path": "/p.mov"}]}, montage=mont)
    media = plan["fx"]["media"]
    assert [m["id"] for m in media] == ["sc01"] and media[0]["sync"]["pieces"] == [[0.0, 10.0, 1.0]]


def test_un_recurso_del_guion_no_tapa_la_grabacion_de_pantalla():
    """Una linea del guion con "- Agregar logo" dicha durante la toma que cubre la grabacion de pantalla:
    las dos son tarjetas centradas arriba de los subtitulos y la del guion tapaba la demo. La pantalla
    manda: el recurso queda apagado (off_why "screen", el Studio dice por que); uno fuera de la toma, no."""
    mont = {"version": 1, "timeline": [], "slots": [],
            "screens": [{"id": "sc01", "sid": "sc", "cam": "c2", "offset": 12.0, "dur": 48.0, "enabled": True}]}
    segs = [{"start": 0.0, "end": 10.0, "kind": "clip", "sid": "c2", "in": 1.0},
            {"start": 10.0, "end": 16.0, "kind": "clip", "sid": "c1", "in": 0.0}]
    info = dict(_info(segs), main=[])
    plan = {"duration": 16.0, "fx": {"media": [
        {"id": "in01", "src": "script", "style": "float_card", "start": 3.0, "end": 6.0, "enabled": True},
        {"id": "in02", "src": "script", "style": "float_card", "start": 11.0, "end": 14.0, "enabled": True},
        {"id": "in03", "src": "script", "style": "float_card", "start": 9.95, "end": 12.0, "enabled": True}]}}
    compose.finish_plan(plan, info, spec={"screens": [{"sid": "sc", "path": "/p.mov"}]}, montage=mont)
    by = {m["id"]: m for m in plan["fx"]["media"]}
    assert by["sc01"]["enabled"] is True
    assert by["in01"]["enabled"] is False and by["in01"]["off_why"] == "screen"
    assert by["in02"]["enabled"] is True and "off_why" not in by["in02"]
    assert by["in03"]["enabled"] is True                       # 0.05 s de roce con el final: se queda
    # una pantalla quitada en el ensamble no apaga nada
    plan2 = {"duration": 16.0, "fx": {"media": [dict(by["in02"], id="in04", start=3.0, end=6.0)]}}
    mont2 = dict(mont, screens=[dict(mont["screens"][0], enabled=False)])
    compose.finish_plan(plan2, info, spec={"screens": [{"sid": "sc", "path": "/p.mov"}]}, montage=mont2)
    assert next(m for m in plan2["fx"]["media"] if m["id"] == "in04")["enabled"] is True
    # el Studio explica por que
    import json as _j
    from pathlib import Path as _P
    ui = _j.loads((_P(compose.__file__).parent / "motion" / "ui_copy.json").read_text())
    assert "grabación de pantalla" in ui["strings"]["media.file.under_screen"]
    fxjs = (_P(compose.__file__).parents[1] / "app" / "static" / "fx.js").read_text()
    assert 'it.off_why === "screen"' in fxjs and '"media.file.under_screen"' in fxjs


def _screen_file(path, still_from, dur=26.0, fps=30, vfr=False):
    """Grabacion de pantalla sintetica 120x260: cambia (una franja que baja y un numero) hasta `still_from`
    s y despues se queda QUIETA (con vfr: sin cuadros, como ReplayKit)."""
    import cv2
    n = int(dur * fps)
    frames = []
    for k in range(n):
        t = k / fps
        tt = min(t, still_from)
        img = np.full((260, 120, 3), 240, np.uint8)
        y = int(20 + (tt * 37) % 200)
        cv2.rectangle(img, (10, y), (110, y + 22), (40, 120, 230), -1)
        cv2.putText(img, f"{int(tt * 10)}", (20, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (20, 20, 20), 2)
        frames.append(img)
    tmp = path.with_name("raw_" + path.name)
    p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "120x260", "-r",
                          str(fps), "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(tmp)],
                         stdin=subprocess.PIPE)
    p.stdin.write(b"".join(f.tobytes() for f in frames))
    p.stdin.close()
    assert p.wait() == 0
    if vfr:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-vf",
                        f"select='lt(t\\,{still_from + 0.1})+gte(t\\,{dur - 0.2})'", "-fps_mode", "vfr", "-c:v", "libx264",
                        "-preset", "ultrafast", str(path)], check=True)
    else:
        tmp.rename(path)
    return path


def _demo_plan(words):
    mont = {"version": 1, "timeline": [], "slots": [],
            "screens": [{"id": "sc01", "sid": "sc", "cam": "c2", "offset": 2.0, "dur": 26.0, "enabled": True}]}
    segs = [{"start": 0.0, "end": 20.0, "kind": "clip", "sid": "c2", "in": 0.0},
            {"start": 20.0, "end": 24.0, "kind": "clip", "sid": "c1", "in": 0.0}]
    plan = {"duration": 24.0, "phrases": [words], "cuts": [], "takes": {"stats": {}}, "fx": {"media": []}}
    return mont, dict(_info(segs), main=[]), plan


DEMO_WORDS = (words("Abro la agenda elijo el martes y confirmo la cita.", 1.0, 9.0)
              + words("Listo.", 11.4, 12.1)
              + words("Pero no mejor quiero otro horario con otra persona pero ella si", 12.7, 19.2)
              + words("Nos vemos.", 20.5, 21.5))


@pytest.mark.parametrize("vfr", [False, True])
def test_demo_con_la_pantalla_quieta_al_final_propone_terminar_aqui(tmp_path, vfr):
    """La toma de la demo seguia 14 s despues de «Listo.» con la ventana congelada en la confirmacion
    mientras se cambiaba de opinion (v6 terminaba ahi). Se propone «Terminar aquí»: un corte de tomas
    (digresion, encendido) desde «Listo.» hasta el final de la toma, que no cruza la union."""
    scr = _screen_file(tmp_path / "sc.mp4", still_from=14.0, vfr=vfr)     # quieta desde el s 12 del video
    mont, info, plan = _demo_plan(DEMO_WORDS)
    compose.finish_plan(plan, info, spec={"screens": [{"sid": "sc", "path": str(scr)}]}, montage=mont)
    tk = [c for c in plan["cuts"] if c.get("src") == "takes"]
    assert len(tk) == 1, plan["cuts"]
    c = tk[0]
    perf = next(w for w in DEMO_WORDS if w["text"] == "Listo.")
    assert c["kind"] == "digresion" and c["enabled"] is True and c["level"] == "media"
    assert perf["end"] < c["start"] <= perf["end"] + 0.15 and c["end"] == 20.0
    assert "Listo." in c["reason"] and c["text"].startswith("Pero no mejor")
    assert plan["takes"]["stats"]["by_kind"]["digresion"]["n"] == 1
    import takes
    assert not takes.crosses_join(c, takes.plan_joins({"shots": [{"start": 0, "end": 20, "join": None},
                                                                 {"start": 20, "end": 24, "join": "line"}]}))
    # recomponer no lo duplica
    compose.finish_plan(plan, info, spec={"screens": [{"sid": "sc", "path": str(scr)}]}, montage=mont)
    assert len([c for c in plan["cuts"] if c.get("src") == "takes"]) == 1


def test_demo_sin_cola_quieta_o_sin_cierre_no_propone_nada(tmp_path):
    busy = _screen_file(tmp_path / "busy.mp4", still_from=99.0)            # cambia hasta el final
    mont, info, plan = _demo_plan(DEMO_WORDS)
    compose.finish_plan(plan, info, spec={"screens": [{"sid": "sc", "path": str(busy)}]}, montage=mont)
    assert not [c for c in plan["cuts"] if c.get("src") == "takes"]
    # quieta pero sin una oracion que cierre cuando se queda quieta (sigue explicando): nada
    still = _screen_file(tmp_path / "still.mp4", still_from=14.0)
    ws = words("Abro la agenda elijo el martes y confirmo la cita y aqui ves como queda todo listo para "
               "el martes con la doctora y el horario que elegi hace un momento en la app", 1.0, 19.2)
    mont, info, plan = _demo_plan(ws)
    compose.finish_plan(plan, info, spec={"screens": [{"sid": "sc", "path": str(still)}]}, montage=mont)
    assert not [c for c in plan["cuts"] if c.get("src") == "takes"]
    # quieta solo los ultimos 2 s: nada
    late = _screen_file(tmp_path / "late.mp4", still_from=20.0)
    mont, info, plan = _demo_plan(DEMO_WORDS)
    compose.finish_plan(plan, info, spec={"screens": [{"sid": "sc", "path": str(late)}]}, montage=mont)
    assert not [c for c in plan["cuts"] if c.get("src") == "takes"]


# ----------------------------------------------------------------------------- servidor
def test_server_screen_like_y_screens(tmp_path):
    from test_server_setup import _run
    _run("""
import numpy as np, cv2
import server as S
S.SOURCES.mkdir(parents=True, exist_ok=True)
# miniatura sin caras (una interfaz) y proporcion de celular -> parece pantalla
img = np.full((688, 320, 3), 245, np.uint8)
cv2.rectangle(img, (20, 30), (300, 80), (200, 200, 200), -1)
cv2.imwrite(str(S.SOURCES / "x_t.jpg"), img)
assert S._screen_like("x", {"w": 670, "h": 1436}) is True
assert S._screen_like("x", {"w": 1080, "h": 1920}) is False      # proporcion de camara: nunca
assert S._screen_like("y", {"w": 670, "h": 1436}) is False       # sin miniatura: toma
raw = [{"id": "sc01", "sid": "sc", "cam": "c2", "offset": 12.0, "overlap": [0, 32], "rate": 1.0,
        "crop": [0.03, 0.02, 0.97, 0.97], "content": [19, 26, 632, 1372], "lead": [0, 0.128], "audio_offset": 11.872},
       {"id": "x", "sid": "ajeno", "cam": "c2", "offset": 1.0, "overlap": [0, 1]},
       {"id": "y", "sid": "sc", "cam": "sc", "offset": 1.0, "overlap": [0, 1]}]
got = S._screens_norm(raw, {"sc", "c2"})
assert [g["id"] for g in got] == ["sc01"] and got[0]["enabled"] is True, got
assert got[0]["crop"] == [0.03, 0.02, 0.97, 0.97] and got[0]["content"] == [19, 26, 632, 1372]
assert got[0]["lead"] == [0.0, 0.128] and got[0]["audio_offset"] == 11.872
assert S._screens_norm([dict(raw[0], crop=[0.5, 0, 0.55, 1])], {"sc", "c2"})[0]["crop"] is None
out = S._screens_param([{"id": "sc01", "enabled": False}, {"id": "zz", "enabled": False}], {"screens": got})
assert out[0]["enabled"] is False and out[0]["offset"] == 12.0
print("OK")
""", tmp_path)


def _desktop_ui_frames(n=4, w=1920, h=1080):
    """Cuadros de una grabacion de la COMPU (sin datos de un cliente): barra de ventana, barra lateral,
    un documento con renglones de texto y un boton; el texto cambia un poco en cada cuadro."""
    import cv2
    out = []
    for k in range(n):
        img = np.full((h, w, 3), 250, np.uint8)
        cv2.rectangle(img, (0, 0), (w, 64), (236, 238, 242), -1)
        cv2.rectangle(img, (0, 64), (300, h), (244, 245, 248), -1)
        for j in range(12):
            cv2.putText(img, f"Carpeta {j + 1}", (30, 120 + j * 56), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 60, 70), 2)
        for j in range(14):
            cv2.putText(img, f"Linea {j + k} del documento con texto de ejemplo para la demo", (360, 140 + j * 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 30, 36), 2)
        cv2.rectangle(img, (1500, 900), (1820, 980), (240, 120, 40), -1)
        out.append(img)
    return out


def test_ui_like_reconoce_una_interfaz_y_no_una_toma():
    ui = [f[..., ::-1].copy() for f in _desktop_ui_frames(2)]
    assert S.ui_like(ui) is True
    rng = np.random.default_rng(5)
    # una toma de camara: textura y ruido en todo el cuadro, sin renglones de texto
    yy, xx = np.mgrid[0:540, 0:960]
    cam = np.stack([(90 + 60 * np.sin(xx / 37.0) + rng.normal(0, 9, xx.shape)),
                    (110 + 40 * np.cos(yy / 23.0) + rng.normal(0, 9, xx.shape)),
                    (100 + rng.normal(0, 9, xx.shape))], axis=2).clip(0, 255).astype(np.uint8)
    assert S.ui_like([cam, cam]) is False
    assert S.ui_like([ui[0], cam]) is False          # todos los cuadros deben verse como interfaz
    assert S.ui_like([]) is False


def test_server_reconoce_una_grabacion_de_la_compu_1920x1080_sin_etiquetas(tmp_path):
    """OBS, Loom, Zoom: 1920x1080 sin etiqueta del grabador (solo encoder=Lavf). Antes _screen_like la
    descartaba por tener proporcion de camara y detect_screens nunca probaba el audio: la grabacion de la
    compu con el mismo micro quedaba como TOMA con las mismas palabras que la camara."""
    import cv2
    vid = tmp_path / "desk.mp4"
    frames = _desktop_ui_frames(4)
    p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1920x1080",
                          "-r", "1", "-i", "-", "-f", "lavfi", "-i", "sine=f=300:d=4", "-c:v", "libx264",
                          "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vid)],
                         stdin=subprocess.PIPE)
    p.stdin.write(b"".join(f.tobytes() for f in frames))
    p.stdin.close()
    assert p.wait() == 0
    cam = tmp_path / "cam.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "nullsrc=s=1920x1080:d=4,geq=lum='128+60*sin(X/31+T)+30*random(1)':cb=128:cr=128",
                    "-f", "lavfi", "-i", "sine=f=300:d=4", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(cam)], check=True)
    assert compose.capture(vid) is None                 # sin etiquetas: ni pantalla ni camara
    from test_server_setup import _run
    _run(f"""
import shutil
import server as S
S.SOURCES.mkdir(parents=True, exist_ok=True)
shutil.copy({str(vid)!r}, S.SOURCES / "desk.mp4")
shutil.copy({str(cam)!r}, S.SOURCES / "cam9.mp4")
assert S._screen_like("desk", {{"w": 1920, "h": 1080, "dur": 4.0}}) is True
assert S._screen_like("cam9", {{"w": 1920, "h": 1080, "dur": 4.0}}) is False
assert S._screen_like("nada", {{"w": 1920, "h": 1080, "dur": 4.0}}) is False
print("OK")
""", tmp_path)
