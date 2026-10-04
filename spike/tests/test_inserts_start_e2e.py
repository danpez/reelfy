"""Recursos del guion con su INICIO elegido por el usuario («Desde qué segundo del clip»).

Regresion v0.6.1: en v0.5.5 el usuario ponia un inicio distinto a cada recurso del guion ("- Agregar
demo") y todos salian desde el inicio de su archivo. La causa: compose.script_media no guardaba la
duracion del clip (media_dur) en el recurso y el inspector del Studio (mediaRefit) acotaba el inicio a
[0, duracion - tramo] con duracion 0 = siempre 0 (mientras el clip no se hubiera cargado en la vista en
vivo: el caso normal al elegir el recurso desde la pista). Ademas compose._media_fit tiraba a 0 un
inicio que no cabia en vez de acotarlo.

Unitarias (compose) + E2E en Chrome real contra un servidor AISLADO con un proyecto SINTETICO: el
recurso trae su clip numerado (cada cuadro lleva su numero en franjas); el usuario pone 9 s en el
inspector, se guarda, la vista en vivo lo usa y el video EXPORTADO con la hoja real muestra, en cada
cuadro del recurso, el cuadro 9 s + lo que lleva el recurso del clip. Se activa con REELFY_PLAYWRIGHT."""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest

import compose

SPIKE = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
CW, CH, CFPS, CDUR, BITS = 320, 400, 30, 20.0, 12
B0, BH = 40, 26                    # franjas del numero de cuadro (px del clip)


def _bands():
    return [(B0 + i * BH, B0 + (i + 1) * BH) for i in range(BITS)]


def _numbered(path: Path):
    """Clip 320x400 a 30 fps, 20 s: el cuadro n lleva n en 12 franjas (blanco = 1)."""
    import paths
    n = int(CDUR * CFPS)
    frames = np.full((n, CH, CW, 3), 90, np.uint8)
    for k in range(n):
        for i, (a, b) in enumerate(_bands()):
            frames[k, a + 3:b - 3, 30:CW - 30] = 235 if (k >> i) & 1 else 20
    p = subprocess.Popen([str(paths.FFMPEG), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{CW}x{CH}",
                          "-r", str(CFPS), "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8",
                          "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)], stdin=subprocess.PIPE)
    p.stdin.write(frames.tobytes())
    p.stdin.close()
    assert p.wait(timeout=300) == 0


# ================================================================== unitarias
def test_script_media_guarda_la_duracion_del_clip(tmp_path):
    clip = tmp_path / "demo.mp4"
    subprocess.run([str(compose.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x200:r=30:d=12",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clip)], check=True)
    mont = {"inserts": [{"id": "ins1", "line": "l1", "name": "demo", "sid": "demo", "kind": "video", "start": 2.0,
                         "end": 6.0, "fit": "trim", "speed": 1.0, "src_in": 0.0, "status": "ok"}],
            "timeline": [{"sid": "cam", "slot": "s1", "cand": "c1", "in": 0.0, "out": 10.0, "start": 0.0}]}
    info = {"segs": [{"start": 0.0, "end": 10.0, "kind": "clip", "sid": "cam", "in": 0.0, "slot": "s1", "cand": "c1"}]}
    items, _meta = compose.script_media(mont, info, 10.0, spec={"inserts": [{"sid": "demo", "path": str(clip)}]},
                                        resolve=lambda sid, it=None: str(clip))
    assert len(items) == 1 and items[0]["media_dur"] == pytest.approx(12.0, abs=0.05)


def test_media_fit_respeta_y_acota_el_inicio():
    # clip de 20 s en un hueco de 4 s: "trim" desde lo que pidio el usuario
    assert compose._media_fit("video", 20.0, 4.0, {"fit": "trim", "src_in": 9.0}) == ("trim", 1.0, 9.0)
    # un inicio que no cabe se ACOTA (antes volvia a 0 y salia desde el inicio del archivo)
    assert compose._media_fit("video", 20.0, 4.0, {"fit": "trim", "src_in": 18.0}) == ("trim", 1.0, 16.0)
    # clip apenas mas largo (hasta 1.4x): se acelera... salvo que el usuario eligio desde donde
    assert compose._media_fit("video", 5.0, 4.0, {"fit": "speed", "src_in": 0.0})[0] == "speed"
    assert compose._media_fit("video", 5.0, 4.0, {"fit": "trim", "src_in": 0.5}) == ("trim", 1.0, 0.5)
    assert compose._media_fit("image", None, 4.0, {"src_in": 3.0}) == ("hold", 1.0, 0.0)


# ================================================================== E2E
def _plan(video):
    sys.path.insert(0, str(SPIKE / "tests"))
    import test_estilo as TE
    from motion import schema
    p = TE._plan_estilo()
    p["video"] = str(video)
    p["fx"]["graphics"] = []
    p["fx"]["text"] = []
    # como lo deja compose (sin media_dur: lo que traian los proyectos de antes)
    p["fx"]["media"] = [{"id": "ins1", "start": 3.0, "end": 7.0, "sid": "demo01", "path": None, "kind": "video",
                         "fit": "trim", "speed": 1.0, "src_in": 0.0, "style": "float_card", "enabled": True,
                         "locked": True, "src": "script", "name": "demo", "float": 0.0}]
    return schema.normalize(p)


def _decode(fr, card_px, crop_h=CH):
    x0, y0, w, h = card_px
    xs = x0 + w // 2
    val = 0
    for i, (a, b) in enumerate(_bands()):
        y = y0 + int(round((a + b) / 2 * h / crop_h))
        v = int(fr[y, xs - 3:xs + 4, :3].mean())
        if 70 < v < 170:
            return None
        val |= (1 << i) if v >= 170 else 0
    return val


def test_inicio_del_recurso_e2e_studio_y_export(tmp_path):
    pw = os.environ.get("REELFY_PLAYWRIGHT")
    if not (pw and NODE):
        pytest.skip("E2E del inicio de los recursos desactivado (define REELFY_PLAYWRIGHT)")
    sys.path.insert(0, str(SPIKE / "tests"))
    import test_estilo as TE
    from motion import media as MF
    from motion import render as R
    from motion import video_io as V
    data = tmp_path / "data"
    src = data / "input" / "sources"
    src.mkdir(parents=True)
    (data / "music-models").mkdir()
    xdir = tmp_path / "exportados"
    vid = data / "input" / "ins_demo.mp4"
    TE._video_estilo(vid)
    _numbered(src / "demo01.mp4")
    subprocess.run([str(V.FFMPEG), "-y", "-v", "error", "-i", str(src / "demo01.mp4"), "-frames:v", "1",
                    str(src / "demo01_t.jpg")], check=True)        # su miniatura (como al subirlo)
    plan = _plan(vid)
    code = ("import json,sys; sys.path[:0]=[%r,%r]; import jobstore; jobstore.init(); d=json.load(sys.stdin)\n"
            "jobstore.create('ins_demo', kind='analyze', ext='.mp4', plan=d, phase='review', message='Análisis listo')\n"
            "jobstore.update('ins_demo', state='review', pct=100)\n") % (str(SPIKE / "app"), str(SPIKE / "scripts"))
    subprocess.run([sys.executable, "-c", code], input=json.dumps(plan), text=True, check=True,
                   env={**os.environ, "REELFY_DATA": str(data)}, timeout=120)
    port = None
    for p in range(8960, 9060):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                port = p
                break
    assert port, "sin puerto libre"
    srv = subprocess.Popen([str(SPIKE / "tests" / "devserver.sh"), str(port), str(data)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, start_new_session=True,
                           env={**os.environ, "REELFY_EXPORT_DIR": str(xdir)})
    try:
        for _ in range(120):
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{port}/setup", timeout=2).status == 200:
                    break
            except Exception:  # noqa: aun arrancando
                time.sleep(0.5)
        out = os.environ.get("REELFY_E2E_SHOTS") or str(tmp_path / "shots")
        r = subprocess.run([NODE, str(SPIKE / "tests" / "js" / "inserts_start_e2e.mjs"), f"http://127.0.0.1:{port}",
                            "ins_demo", str(xdir), out], capture_output=True, text=True, timeout=1800, env={**os.environ})
        assert r.returncode == 0, r.stdout[-8000:] + r.stderr[-3000:]
    finally:
        try:
            os.killpg(srv.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        srv.wait(timeout=30)
    res = json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("RESULT "))[7:])
    saved = res["plan"]
    file = max(res["files"], key=lambda f: float(V.probe(f).get("duration") or 0.0))     # el video (no un short)
    it = saved["fx"]["media"][0]
    assert it["src_in"] == 9.0 and it["fit"] == "trim"
    # los cuadros del recurso en el video EXPORTADO: cuadro (9 + lo que lleva el recurso) del clip
    info = V.probe(file)
    W, H = int(info["w"]), int(info["h"])
    job = R.prepare(saved, {"W": W, "H": H, "cache_dir": str(tmp_path / "cache"), "media_root": str(src)})["job"]
    jit = job["media"][0]
    box = R.layout_box(W, H)
    x0, y0, w, h = MF.card_px(jit["_card"], box[2], box[3])
    card = (box[0] + x0, box[1] + y0, w, h)
    fps = float(info.get("fps") or 30.0)
    checked = 0
    with V.FrameReader(file, W, H, 0, None, fps) as rd:
        for k, fr in enumerate(rd):
            t = k / fps
            if not (it["start"] + 1.2 <= t <= it["end"] - 0.4):
                continue
            got = _decode(fr, card)
            exp = round((9.0 + (t - it["start"])) * CFPS)
            assert got is not None and abs(got - exp) <= 1, (t, got, exp)
            checked += 1
    assert checked >= 60, checked


def test_servidor_completa_la_duracion_de_los_recursos(tmp_path):
    """Un proyecto de antes (sus recursos del guion sin media_dur) la recibe al abrirse (GET /plan y
    /status con el plan) y al guardarse: el inspector acota el inicio dentro del clip."""
    from test_server_setup import _run
    _run("""
import subprocess
import server as S
S.SOURCES.mkdir(parents=True, exist_ok=True)
subprocess.run([str(S.paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x80:r=30:d=7",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(S.SOURCES / "clip1.mp4")], check=True)
plan = {"fx": {"media": [{"id": "a", "sid": "clip1", "kind": "video", "src_in": 2.0},
                         {"id": "b", "sid": "clip1", "kind": "video", "media_dur": 3.5},
                         {"id": "c", "sid": "nada", "kind": "video"}, {"id": "d", "sid": "clip1", "kind": "image"}]}}
S._media_durs(plan)
m = {x["id"]: x for x in plan["fx"]["media"]}
assert abs(m["a"]["media_dur"] - 7.0) < 0.05 and m["a"]["src_in"] == 2.0, m["a"]
assert m["b"]["media_dur"] == 3.5 and "media_dur" not in m["c"] and "media_dur" not in m["d"]
print("OK")
""", tmp_path)
