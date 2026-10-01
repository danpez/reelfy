"""scripts/compose.py para el export en alta resolucion: el lienzo nativo (sin el tope vertical),
el tope por llamada (`max_canvas`), el registro de lo que se compuso (spec_record: volumenes "auto"
ya resueltos) y la reconstruccion desde plan.montage.timeline (spec_from_timeline). Recomponer las
MISMAS tomas a mas resolucion cae en los mismos cuadros (uniones exactas, rampas) y no vuelve a
medir el volumen. Videos sinteticos (lavfi); sin datos de clientes."""
import subprocess
from pathlib import Path

import pytest

import compose as C
import paths


def _src(path, w, h, d, freq=330):
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={w}x{h}:r=30:d={d}", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:sample_rate=48000:duration={d}", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def srcs(tmp_path_factory):
    d = tmp_path_factory.mktemp("export_compose")
    a = _src(d / "a00000000001.mp4", 720, 1280, 3.0, 300)
    b = _src(d / "a00000000002.mp4", 720, 1280, 3.0, 450)
    h = _src(d / "a00000000003.mp4", 640, 360, 2.0, 520)
    return {"dir": d, "a": a, "b": b, "h": h}


def _it(p, start, a, b, **kw):
    it = {"path": str(p), "sid": Path(p).stem, "type": "video", "name": Path(p).name, "start": start,
          "layer": 0, "fadeIn": 0.0, "fadeOut": 0.0, "in": a, "out": b, "vol": 1.0, "mute": False}
    it.update(kw)
    return it


def _montage_spec(s):
    m1 = {"slot": "s1", "cand": "c1", "join": None, "conf": 0.9}
    m2 = {"slot": "s2", "cand": "c1", "join": "line", "conf": 0.8}
    return {"fps": 30, "overlays": [], "main": [
        _it(s["a"], 0.0, 0.2, 1.9, vol="auto", montage=m1, ramp_out={"dur": 0.3, "speed": 2.0},
            words=[{"start": 0.3, "end": 1.0}]),
        _it(s["b"], 1.7, 0.5, 2.4, vol="auto", montage=m2, words=[{"start": 0.6, "end": 1.5}])]}


def _shots(info):
    return [[float(x["start"]), float(x["end"])] for x in info["shots"]]


def test_lienzo_nativo_y_tope_por_llamada(srcs, tmp_path):
    spec = {"fps": 30, "overlays": [], "main": [_it(srcs["a"], 0, 0, 1.0), _it(srcs["b"], 1.0, 0, 1.0)]}
    assert C.native_canvas(spec) == (720, 1280)
    assert C.native_canvas({"main": []}) is None
    assert C.native_canvas({"main": [{"path": str(tmp_path / "no.mp4"), "type": "video"}]}) is None
    assert C.canvas_size(2160, 3840) == C.MAX_VERTICAL or C.canvas_size(2160, 3840)[1] <= C.MAX_VERTICAL[1]
    assert C.canvas_size(2160, 3840, (2160, 3840)) == (2160, 3840)
    assert C.canvas_size(3840, 2160, (100, 100)) == (3840, 2160)         # horizontal: sin tope
    info = {}
    C.compose(spec, tmp_path / "low.mp4", tmp_path / "w1", info=info, max_canvas=(360, 640))
    assert (info["W"], info["H"]) == (360, 640) and info["native"] == [720, 1280]
    assert C.probe(tmp_path / "low.mp4")["w"] == 360
    # majority (montaje): la orientacion de la mayoria, sin tope
    mix = {"main": [_it(srcs["h"], 0, 0, 1), _it(srcs["a"], 1, 0, 1), _it(srcs["b"], 2, 0, 1)],
           "canvas": "majority"}
    assert C.native_canvas(mix) == (720, 1280)


def test_registro_y_recomposicion_identica(srcs, tmp_path, monkeypatch):
    spec = _montage_spec(srcs)
    info = {}
    C.compose(spec, tmp_path / "low.mp4", tmp_path / "w1", info=info, max_canvas=(360, 640))
    rec = C.spec_record(spec, info)
    assert rec["W"] == 360 and rec["native"] == [720, 1280] and len(rec["shots"]) == 2
    for it in rec["spec"]["main"]:
        assert isinstance(it["vol"], float) and "words" not in it and "_lufs" not in it
        assert isinstance(it["montage"], dict)
    assert rec["spec"]["main"][0]["ramp_out"] == {"dur": 0.3, "speed": 2.0}
    # el spec del llamador no cambia (params del trabajo)
    assert spec["main"][0]["vol"] == "auto" and "words" in spec["main"][0]

    # recomponer NO vuelve a medir la voz (los volumenes ya vienen resueltos)
    def no_measure(*a, **k):
        raise AssertionError("no debe medir")
    monkeypatch.setattr(C, "speech_lufs", no_measure)
    hi = {}
    C.compose(rec["spec"], tmp_path / "hi.mp4", tmp_path / "w2", info=hi, max_canvas=(720, 1280))
    assert (hi["W"], hi["H"]) == (720, 1280)
    tol = 1.5 / 30
    for (a0, a1), (b0, b1) in zip(rec["shots"], _shots(hi)):
        assert abs(a0 - b0) <= tol and abs(a1 - b1) <= tol
    assert abs(C.probe(tmp_path / "hi.mp4")["dur"] - C.probe(tmp_path / "low.mp4")["dur"]) <= 1 / 30 + 1e-3
    assert [r["side"] for r in hi.get("ramps") or []] == [r["side"] for r in info.get("ramps") or []]
    # mismo audio (mismas ganancias): niveles del primer segundo iguales
    import numpy as np
    x0 = C._pcm(tmp_path / "low.mp4", 0.0, 1.0)
    x1 = C._pcm(tmp_path / "hi.mp4", 0.0, 1.0)
    n = min(len(x0), len(x1))
    assert n > 40000 and abs(float(np.sqrt(np.mean(x0[:n] ** 2))) - float(np.sqrt(np.mean(x1[:n] ** 2)))) < 0.01


def test_reconstruir_desde_la_linea_de_tiempo_del_montaje(srcs, tmp_path):
    spec = _montage_spec(srcs)
    info = {}
    C.compose(spec, tmp_path / "low.mp4", tmp_path / "w1", info=info, max_canvas=(360, 640))
    shots = C.plan_shots(info["shots"], C.probe(tmp_path / "low.mp4")["dur"])
    m = C.montage_plan(None, info["main"], shots)
    tl = m["timeline"]
    assert all(isinstance(e["vol"], float) for e in tl) and tl[0].get("ramp_out")
    by_sid = {srcs["a"].stem: srcs["a"], srcs["b"].stem: srcs["b"]}
    sp = C.spec_from_timeline(tl, by_sid.get)
    assert [it["sid"] for it in sp["main"]] == [srcs["a"].stem, srcs["b"].stem]
    it0 = sp["main"][0]
    assert it0["montage"]["slot"] == "s1" and it0["ramp_out"] == {"dur": 0.3, "speed": 2.0}
    assert it0["in"] == pytest.approx(0.2) and it0["out"] == pytest.approx(1.9)
    assert sp["main"][1]["montage"]["join"] == "line" and sp["main"][1]["start"] == pytest.approx(1.7)
    hi = {}
    C.compose(sp, tmp_path / "hi.mp4", tmp_path / "w2", info=hi, max_canvas=(720, 1280))
    tol = 1.5 / 30
    for a, b in zip(_shots(info), _shots(hi)):
        assert abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol
    with pytest.raises(FileNotFoundError) as ei:
        C.spec_from_timeline(tl + [{"sid": "ffffffffffff", "in": 0, "out": 1, "start": 9}], by_sid.get)
    assert "ffffffffffff" in str(ei.value)


def test_registro_de_un_ensamble_a_mano(srcs, tmp_path):
    """Sin montaje (ensamble a mano): el registro conserva overlays, fundidos y volumen numerico."""
    spec = {"fps": 30, "main": [_it(srcs["a"], 0, 0, 1.0, fadeIn=0.2, vol=0.5), _it(srcs["b"], 1.5, 0, 1.0)],
            "overlays": [{"path": str(srcs["h"]), "type": "video", "start": 0.2, "dur": 0.5, "pos": "center",
                          "size": 0.3}]}
    info = {}
    C.compose(spec, tmp_path / "x.mp4", tmp_path / "w", info=info, max_canvas=(360, 640))
    rec = C.spec_record(spec, info)
    assert rec["spec"]["overlays"][0]["size"] == 0.3 and rec["spec"]["main"][0]["fadeIn"] == 0.2
    assert rec["spec"]["main"][0]["vol"] == 0.5 and len(rec["shots"]) == 3      # clip, negro, clip


def test_clips_de_distinta_resolucion_y_lienzo_exacto(srcs, tmp_path):
    """Regresion: el 'nativo' de un montaje era el primer clip. Con un clip chico primero se
    escondia la resolucion de los demas; el export la necesita por clip (clip_canvases) y recompone
    a un lienzo EXACTO (canvas=) aunque el primer clip sea de menos resolucion."""
    low = _src(tmp_path / "a00000000009.mp4", 360, 640, 2.0, 700)
    spec = {"fps": 30, "overlays": [], "main": [_it(low, 0, 0, 1.0), _it(srcs["a"], 1.0, 0, 1.5),
                                                _it(srcs["h"], 2.5, 0, 0.5)]}
    cl = C.clip_canvases(spec)
    assert [(c["w"], c["h"]) for c in cl] == [(360, 640), (720, 1280), (202, 360)]   # el horizontal se recorta
    assert [c["secs"] for c in cl] == [1.0, 1.5, 0.5] and cl[1]["name"] == "a00000000001.mp4"
    assert C.native_canvas(spec) == (720, 1280)          # el mejor clip, con el aspecto del lienzo
    assert C.clip_fill((1080, 1920), (2160, 3840)) == (1080, 1920)
    assert C.clip_fill((3840, 2160), (2160, 3840)) == (1214, 2160)
    info = {}
    C.compose(spec, tmp_path / "x.mp4", tmp_path / "w", info=info)
    # el video combinado es el de siempre (lienzo del primer clip), el registro sabe del resto
    assert (info["W"], info["H"]) == (360, 640) and info["native"] == [720, 1280]
    rec = C.spec_record(spec, info)
    assert rec["native"] == [720, 1280] and [(c["w"], c["h"]) for c in rec["clips"]] == [(360, 640), (720, 1280), (202, 360)]
    hi = {}
    C.compose(spec, tmp_path / "hi.mp4", tmp_path / "w2", info=hi, canvas=(720, 1280))
    p = C.probe(tmp_path / "hi.mp4")
    assert (hi["W"], hi["H"]) == (720, 1280) and (p["w"], p["h"]) == (720, 1280)
    tol = 1.5 / 30
    for a, b in zip(_shots(info), _shots(hi)):
        assert abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def test_cancelar_mata_el_ffmpeg_en_curso(tmp_path):
    """Regresion: cancelar durante "Preparando el video en 4K" esperaba a que terminara el segmento
    (minutos con una toma larga): compose(cancel=) revisa mientras corre cada ffmpeg y lo mata."""
    import os
    import threading
    import time
    long = _src(tmp_path / "a00000000010.mp4", 1440, 2560, 30.0, 300)
    spec = {"fps": 30, "overlays": [], "main": [_it(long, 0, 0.5, 29.5, vol=0.9)]}
    flag = {"c": False, "t": None}

    def go():
        flag.update(c=True, t=time.time())
    threading.Timer(0.8, go).start()
    with pytest.raises(C.Canceled):
        C.compose(spec, tmp_path / "c.mp4", tmp_path / "w", canvas=(1440, 2560), cancel=lambda: flag["c"])
    late = time.time() - flag["t"]
    assert late < 0.6, late                              # en cuanto se pide, no al final del segmento
    kids = subprocess.run(["pgrep", "-P", str(os.getpid()), "ffmpeg"], capture_output=True, text=True).stdout.strip()
    assert not kids, kids                                # el ffmpeg ya no corre
    assert not (tmp_path / "c.mp4").exists()
    # cancelar lanzando (JobCanceled del servidor) tambien mata y propaga ESE error
    class Stop(Exception):
        pass

    def boom():
        raise Stop()
    with pytest.raises(Stop):
        C.compose(spec, tmp_path / "c2.mp4", tmp_path / "w2", canvas=(1440, 2560), cancel=boom)
    # varios segmentos en paralelo (COMPOSE_JOBS): la cancelacion llega a los hilos
    two = {"fps": 30, "overlays": [], "main": [_it(long, 0, 0.5, 14.5), _it(long, 14.0, 15.0, 29.5)]}
    flag.update(c=False, t=None)
    threading.Timer(0.8, go).start()
    with pytest.raises(C.Canceled):
        C.compose(two, tmp_path / "c3.mp4", tmp_path / "w3", canvas=(1440, 2560), cancel=lambda: flag["c"])
    assert time.time() - flag["t"] < 0.8
    assert not subprocess.run(["pgrep", "-P", str(os.getpid()), "ffmpeg"], capture_output=True, text=True).stdout.strip()
