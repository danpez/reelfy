"""Export de punta a punta contra un servidor REAL (app/server.py en un puerto libre, DATA temporal):
la hoja "Exportar" por HTTP. Un montaje sintetico (2 clips 1440x2560, lavfi) compuesto con tope
720x1280 (en la app: fuentes 4K y tope 1620x2880) se exporta:
  - a 720p con nombre propio en una carpeta elegida con el selector (falso) -> el archivo y sus
    shorts quedan ahi, sin sobrescribir el segundo export con el mismo nombre;
  - a 1440p -> las tomas se RECOMPONEN desde las fuentes (etapa "recompose"), sale a 1440x2560 en
    los mismos cuadros que el 720p; repetirlo usa la cache;
  - con el registro roto -> error en espanol que ofrece 1080p (tarea lateral `export`).
"Mostrar en Finder" con un abridor falso. Nada toca ~/Movies."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

import paths
from test_server_api import Api, _free_port, _probe, synthetic_plan

SPIKE = Path(__file__).resolve().parents[1]
PY = sys.executable


def _src(path, w, h, d, f):
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=30:d={d}",
                    "-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=48000:duration={d}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    import compose as C
    data = tmp_path_factory.mktemp("export_e2e")
    out = tmp_path_factory.mktemp("export_e2e_fuera")          # carpetas del usuario (fuera de DATA)
    (out / "Elegida").mkdir()
    sources = data / "input" / "sources"
    sources.mkdir(parents=True)
    sids = ["5e0000000001", "5e0000000002"]
    for k, sid in enumerate(sids):
        _src(sources / f"{sid}.mp4", 1440, 2560, 3.0, 300 + 120 * k)
    job = "e2e" + os.urandom(5).hex()[:9]
    vid = data / "input" / f"{job}.mp4"
    spec = {"fps": 30, "overlays": [], "main": [
        {"path": str(sources / f"{sid}.mp4"), "sid": sid, "type": "video", "name": f"clip{k + 1}.mp4",
         "start": 2.5 * k, "layer": 0, "fadeIn": 0.0, "fadeOut": 0.0, "in": 0.2, "out": 2.7, "vol": 1.0,
         "mute": False, "montage": {"slot": f"s{k}", "cand": "c1", "join": "line" if k else None}}
        for k, sid in enumerate(sids)]}
    info = {}
    C.compose(spec, str(vid), data / "work" / "cw", info=info, max_canvas=(720, 1280))
    assert (info["W"], info["H"]) == (720, 1280) and info["native"] == [1440, 2560]
    rec = C.spec_record(spec, info)
    rec["t"] = time.time()
    dur = float(C.probe(vid)["dur"])
    plan = synthetic_plan(vid, dur)
    plan["dims"] = [720, 1280]
    plan["shots"] = C.plan_shots(info["shots"], dur)
    port = _free_port()
    (data / "bin").mkdir()
    picker = data / "bin" / "picker"
    picker.write_text(f'#!/bin/bash\necho "{out / "Elegida"}"\n')
    opener = data / "bin" / "opener"
    opener.write_text(f'#!/bin/bash\necho "$1|$2" >> "{data / "open.log"}"\n')
    for f in (picker, opener):
        f.chmod(0o755)
    env = dict(os.environ, REELFY_PORT=str(port), REELFY_DATA=str(data), REELFY_GATED="0",
               REELFY_AUTO_DOWNLOAD="0", REELFY_OLLAMA_URL="http://127.0.0.1:9",
               REELFY_ACE_BIN=str(data / "sin_ace"), REELFY_MUSIC_MODELS=str(data / "sin_musica"),
               REELFY_WORKERS="1", REELFY_BG_ANALYSIS="0", REELFY_EXPORT_DIR=str(out / "Movies" / "Reelfy"),
               REELFY_PICK_FOLDER_CMD=str(picker), REELFY_OPEN_CMD=str(opener))
    log = open(data / "server_stdout.log", "w")
    proc = subprocess.Popen([PY, "-u", str(SPIKE / "app" / "server.py")], cwd=str(SPIKE), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    api = Api(f"http://127.0.0.1:{port}")
    try:
        for _ in range(120):
            try:
                api.get("/tracks", timeout=2)
                break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError((data / "server_stdout.log").read_text()[-3000:])
                time.sleep(0.25)
        cx = sqlite3.connect(data / "jobs.db", timeout=30)
        now = time.time()
        cx.execute("INSERT INTO jobs (id, kind, state, phase, pct, message, ext, params, plan, created, updated, "
                   "plan_rev, compose_spec) VALUES (?, 'compose', 'review', 'review', 100, 'Análisis listo', "
                   "'.mp4', '{}', ?, ?, ?, 1, ?)", (job, json.dumps(plan), now, now, json.dumps(rec)))
        cx.commit()
        cx.close()
        v2 = api.post(f"/direct/{job}", {"full": True, "use_llm": False, "music": "none", "seed": 5})["plan"]
        yield {"api": api, "job": job, "data": data, "out": out, "plan": v2}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def _export(srv, ex, fmt="9:16"):
    api, job = srv["api"], srv["job"]
    r = api.post(f"/render/{job}", {"plan": srv["plan"], "fmt": fmt, "export": ex})
    stages = []

    def done(s):
        if s.get("stage") and (not stages or stages[-1] != s["stage"]):
            stages.append(s["stage"])
        return s["phase"] in ("done", "error")
    st = api.wait(job, done, 900)
    return r, st, stages


def _vinfo(p):
    v = [s for s in _probe(p)["streams"] if s["codec_type"] == "video"][0]
    return int(v["width"]), int(v["height"]), float(v["duration"])


def _frame(p, t, w, h):
    raw = subprocess.run([str(paths.FFMPEG), "-v", "error", "-ss", f"{t:.3f}", "-i", str(p), "-frames:v", "1",
                          "-vf", f"scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w).astype(np.float32)


def test_opciones_por_http(srv):
    o = srv["api"].get(f"/export/options?job={srv['job']}")
    r = {x["id"]: x for x in o["formats"]["9:16"]["resolutions"]}
    assert o["source"]["label"] == "1440p" and r["1440p"]["enabled"] and r["1440p"]["recompose"]
    assert not r["4k"]["enabled"] and "1440p" in r["4k"]["reason"]
    e = srv["api"].get(f"/export/estimate?job={srv['job']}&res=1440p&quality=alta&shorts=1")
    assert e["recompose"] and e["text"].startswith("Tardará") and e["W"] == 1440
    code, body, _ = srv["api"].get(f"/export/estimate?job={srv['job']}&res=9k", raw=True)
    assert code == 400


def test_exportar_720p_a_la_carpeta_elegida(srv):
    api, out = srv["api"], srv["out"]
    pk = api.post("/pick-folder", {})
    folder = str((out / "Elegida").resolve())
    assert pk["ok"] and pk["path"] == folder
    r, st, stages = _export(srv, {"res": "720p", "quality": "estandar", "name": "Promo: verano", "folder": folder,
                                  "shorts": True})
    assert st["phase"] == "done", st.get("error")
    assert r["export"]["file_name"] == "Promo - verano.mp4" and not r["export"]["recompose"]
    assert "recompose" not in stages and "render" in stages        # "save" dura milisegundos (clon APFS)
    c0 = st["clips"][0]
    assert c0["saved"] and c0["path"] == str(Path(folder) / "Promo - verano.mp4") and c0["label"] == "720p"
    assert (c0["W"], c0["H"]) == (720, 1280) and c0["bytes"] == Path(c0["path"]).stat().st_size
    w, h, d = _vinfo(c0["path"])
    assert (w, h) == (720, 1280) and abs(d - c0["duration"]) < 0.07
    assert st["clips"][1]["path"] == str(Path(folder) / "Promo - verano - Short 1.mp4")
    assert Path(st["clips"][1]["path"]).exists()
    assert st["export"]["state"] == "done" and st["export_state"] == "done" and st["export"]["path"] == c0["path"]
    # el render de siempre sigue en DATA/output (vista previa del resultado)
    assert (srv["data"] / "output" / c0["file"]).exists()
    # "Mostrar en Finder" del archivo exportado
    assert api.post("/reveal", {"path": c0["path"]})["ok"]
    for _ in range(50):
        if (srv["data"] / "open.log").exists():
            break
        time.sleep(0.1)
    assert (srv["data"] / "open.log").read_text().strip() == f"reveal|{c0['path']}"
    assert api.post("/reveal", {"path": str(srv["data"] / "output" / c0["file"])}, raw=True)[0] == 403
    # mismo nombre otra vez: no sobrescribe
    r2, st2, _ = _export(srv, {"res": "720p", "name": "Promo: verano", "folder": folder, "shorts": False})
    assert st2["phase"] == "done" and st2["clips"][0]["file_name"] == "Promo - verano (2).mp4"
    assert Path(c0["path"]).stat().st_size == c0["bytes"]
    srv["ref720"] = c0["path"]
    # las elecciones quedan recordadas
    last = api.get("/export/settings")["last"]
    assert last["folder"] == folder and last["res"] == "720p" and last["shorts"] is False


def test_exportar_1440p_recompone_las_tomas_y_usa_la_cache(srv):
    api = srv["api"]
    r, st, stages = _export(srv, {"res": "1440p", "name": "Alta", "shorts": False})
    assert st["phase"] == "done", st.get("error")
    assert r["export"]["recompose"] and stages.index("recompose") < stages.index("render")
    c0 = st["clips"][0]
    w, h, d = _vinfo(c0["path"])
    assert (w, h) == (1440, 2560) and c0["stats"]["export"]["recompose"] == "new"
    hires = list((srv["data"] / "work").glob(f"{srv['job']}_*_1440x2560_hires.mp4"))
    assert len(hires) == 1 and _vinfo(hires[0])[:2] == (1440, 2560)
    # mismos cuadros que el 720p (las tomas recompuestas caen donde estaban): 1 cuadro de desfase en
    # testsrc2 (se mueve en cada cuadro) da una diferencia mucho mayor
    ref = srv.get("ref720")
    if ref:
        assert abs(d - _vinfo(ref)[2]) < 0.04
        diffs = [float(np.mean(np.abs(_frame(c0["path"], t, 180, 320) - _frame(ref, t, 180, 320))))
                 for t in (0.5, 1.6, 3.4)]
        off = float(np.mean(np.abs(_frame(c0["path"], 1.6, 180, 320) - _frame(ref, 1.6 + 1 / 30, 180, 320))))
        assert max(diffs) < off, (diffs, off)
    # otra vez: la recomposicion sale de la cache
    _, st2, stages2 = _export(srv, {"res": "1440p", "name": "Alta", "shorts": False})
    assert st2["phase"] == "done" and st2["clips"][0]["stats"]["export"]["recompose"] == "cache"
    assert st2["clips"][0]["file_name"] == "Alta (2).mp4"
    e = api.get(f"/export/estimate?job={srv['job']}&res=1440p&shorts=0")
    assert e["recompose_cached"] and e["parts"]["recompose"] == 0 and e["basis"] == "medido"


def test_recomposicion_fallida_ofrece_1080p(srv):
    job = srv["job"]
    cx = sqlite3.connect(srv["data"] / "jobs.db", timeout=30)
    rec = json.loads(cx.execute("SELECT compose_spec FROM jobs WHERE id=?", (job,)).fetchone()[0])
    rec["t"] = time.time() + 5                              # otra clave (sin la cache)
    rec["shots"] = [[0.0, 0.9], [0.9, 5.0]]                 # tomas que ya no coinciden
    cx.execute("UPDATE jobs SET compose_spec=? WHERE id=?", (json.dumps(rec), job))
    cx.commit()
    cx.close()
    _, st, _ = _export(srv, {"res": "1440p", "name": "Falla", "shorts": False})
    assert st["phase"] == "error"
    assert st["error"].startswith("No se pudo preparar el video en 1440p") and st["error"].endswith("1080p.")
    assert st["export"]["code"] == "recompose_failed" and st["export"]["fallback_res"] == "1080p"
    assert not list((srv["data"] / "work").glob("*.part.mp4"))
    # 1080p sigue funcionando (lo de siempre)
    _, st2, stages2 = _export(srv, {"res": "1080p", "name": "Falla", "shorts": False})
    assert st2["phase"] == "done" and "recompose" not in stages2
    assert _vinfo(st2["clips"][0]["path"])[:2] == (1080, 1920)
