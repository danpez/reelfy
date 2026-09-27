"""API del motor pro por HTTP (docs/MOTION-ENGINE.md 6.1) contra un servidor REAL aislado.

Levanta app/server.py en un puerto libre con DATA temporal (sin licencia, sin descargas, sin
LLM: el director usa heuristicas) y un video sintetico vertical (lavfi; sin datos de
clientes). Recorre: catalogos /fx/*, /setup, /direct, /status liviano, /frame, /music,
/preview pro, /render pro con shorts, cancelacion y rechazos.
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

import paths

SPIKE = Path(__file__).resolve().parents[1]
PY = sys.executable


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _w(text, t0, d):
    return {"text": text, "start": round(t0, 3), "end": round(t0 + d, 3)}


def synthetic_plan(video, dur):
    """Plan v1 ficticio (texto inventado) sobre el video sintetico."""
    words, t = [], 0.3
    for tok in "Hola hoy te cuento como cuidar tu sonrisa agenda tu cita en el link".split():
        d = 0.18 + 0.02 * len(tok)
        words.append(_w(tok, t, d))
        t += d + 0.07
    phrases = [words[i:i + 3] for i in range(0, len(words), 3)]
    return {"stem": "sintetico", "video": str(video), "duration": dur, "dims": [720, 1280],
            "phrases": phrases, "cuts": [{"start": 2.9, "end": 3.2, "enabled": True}],
            "beats": [], "camera": [], "broll": [],
            "highlights": [{"start": 0.3, "end": 4.6, "title": "Prueba", "enabled": True}]}


class Api:
    def __init__(self, base):
        self.base = base

    def req(self, method, path, obj=None, raw=False, timeout=300):
        data = json.dumps(obj).encode() if obj is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                body = resp.read()
                return (resp.status, body, dict(resp.headers)) if raw else json.loads(body or b"null")
        except urllib.error.HTTPError as e:
            if raw:
                return e.code, e.read(), dict(e.headers)
            raise AssertionError(f"{method} {path} -> {e.code} {e.read()[:300]!r}") from None

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, obj=None, **kw):
        return self.req("POST", path, obj if obj is not None else {}, **kw)

    def wait(self, job, cond, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.get(f"/status/{job}?plan=0")
            if cond(st):
                return st
            time.sleep(0.4)
        raise AssertionError(f"timeout esperando {job}: {st}")


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    data = tmp_path_factory.mktemp("reelfy_data")
    video = data / "sintetico.mp4"
    dur = 5.0
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=s=720x1280:r=30:d={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(video)], check=True)
    # modelo de voz ya descargado en esta maquina (si hay): habilita /compose en la prueba
    for cand in (Path.home() / "Library/Application Support/online.mixiuh.reelfy/models",
                 Path.home() / "Library/Application Support/Reelfy/models"):
        w = cand / "ggml-large-v3-turbo.bin"
        if w.exists():
            (data / "models").mkdir(exist_ok=True)
            (data / "models" / w.name).symlink_to(w)
            break
    port = _free_port()
    env = dict(os.environ, REELFY_PORT=str(port), REELFY_DATA=str(data), REELFY_GATED="0",
               REELFY_AUTO_DOWNLOAD="0", REELFY_OLLAMA_URL="http://127.0.0.1:9",
               REELFY_ACE_BIN=str(data / "sin_ace"), REELFY_MUSIC_MODELS=str(data / "sin_musica"),
               REELFY_WORKERS="1")
    log = open(data / "server_stdout.log", "w")
    proc = subprocess.Popen([PY, "-u", str(SPIKE / "app" / "server.py")], cwd=str(SPIKE),
                            env=env, stdout=log, stderr=subprocess.STDOUT)
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
        # trabajo en `review` con el plan v1 (como lo deja el analisis)
        job = "t" + os.urandom(5).hex()
        cx = sqlite3.connect(data / "jobs.db", timeout=30)
        now = time.time()
        cx.execute("INSERT INTO jobs (id, kind, state, phase, pct, message, ext, params, plan, "
                   "created, updated, plan_rev) VALUES (?, 'compose', 'review', 'review', 100, "
                   "'Análisis listo', '.mp4', '{}', ?, ?, ?, 1)",
                   (job, json.dumps(synthetic_plan(video, dur)), now, now))
        cx.commit()
        cx.close()
        yield {"api": api, "job": job, "data": data, "video": video, "dur": dur, "proc": proc}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def _probe(path):
    r = subprocess.run([str(paths.FFPROBE), "-v", "error", "-show_streams", "-show_format", "-of",
                        "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_catalogos_fx(srv):
    api = srv["api"]
    sch = api.get("/fx/schema")
    assert {"types", "tracks", "music"} <= set(sch)
    fonts = api.get("/fx/fonts")["fonts"]
    assert len(fonts) >= 20 and all({"name", "family", "role", "url", "weight"} <= set(f) for f in fonts)
    code, body, hdr = api.get(fonts[0]["url"], raw=True)
    assert code == 200 and len(body) > 10_000 and "font" in hdr.get("content-type", "")
    assert api.get("/fx/font/NoExiste", raw=True)[0] == 404
    icons = api.get("/fx/icons?q=horario&limit=5")["icons"]
    assert icons and icons[0]["svg"].startswith("<svg") and "currentColor" in icons[0]["svg"]
    assert len(api.get("/fx/icons?limit=6")["icons"]) == 6
    filled = api.get("/fx/icons?q=corazon&limit=2&variant=filled")["icons"]
    assert filled
    sfx = api.get("/fx/sfx")
    assert "pop" in sfx and {"dur", "hit", "variants"} <= set(sfx["pop"])
    code, body, hdr = api.get("/fx/sfx/pop?variant=1", raw=True)
    assert code == 200 and len(body) > 1000 and hdr.get("content-type") == "audio/flac"
    assert api.get("/fx/sfx/no_existe", raw=True)[0] == 404


def test_setup_opcionales_y_musica_opcional(srv):
    api = srv["api"]
    st = api.get("/setup")
    assert st["llm_model"] == "qwen3.5:4b"
    assert {"aligner", "matte", "music"} <= set(st["optional"])
    assert st["optional"]["music"]["optional"] is True
    assert st["optional"]["music"]["fallback"] == "procedural"
    assert st["engine"]["version"] == "0.5.0" and st["engine"]["pro"] is True
    # la UI legacy llama /setup/start sin cuerpo (solo si ya esta listo: si no, bajaria modelos)
    if st["state"] == "ready":
        req = urllib.request.Request(api.base + "/setup/start", method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            assert json.loads(resp.read())["ok"] is True
    r = api.post("/setup/music", {"enabled": False})
    assert r["music_ai"] is False and r["music"]["state"] in ("disabled", "ready")
    assert api.get("/setup")["music_ai"] is False


def test_direct_status_liviano_y_plan_rev(srv):
    api, job = srv["api"], srv["job"]
    # UI legacy (sin parametros): el plan viaja solo al cambiar de fase
    first = api.get(f"/status/{job}")
    assert first["phase"] == "review" and first["plan"]["stem"] == "sintetico"
    second = api.get(f"/status/{job}")
    assert "plan" not in second
    assert "plan" in api.get(f"/status/{job}?plan=1")
    assert "plan" not in api.get(f"/status/{job}?plan=0")
    rev0 = second["plan_rev"]
    # plan v1 -> director completo (sin LLM: heuristicas)
    r = api.post(f"/direct/{job}", {"full": True, "use_llm": False, "style": "dinamico",
                                    "intensity": 0.8, "seed": 3, "music": "procedural"})
    plan = r["plan"]
    assert plan["version"] == 2 and r["plan_rev"] == rev0 + 1
    fx = plan["fx"]
    assert fx["text"] and fx["sfx"] and fx["music"]["source"] == "procedural"
    assert plan["director"]["style"] == "dinamico" and plan["director"]["seed"] == 3
    assert "plan" in api.get(f"/status/{job}?rev={rev0}")
    assert "plan" not in api.get(f"/status/{job}?rev={r['plan_rev']}")
    # re-dirigir solo un item (rapido, sin LLM)
    tid = fx["text"][0]["id"]
    r2 = api.post(f"/direct/{job}", {"plan": plan, "only_ids": [tid]})
    assert r2["plan_rev"] == r["plan_rev"] + 1 and r2["ms"] < 5000
    assert r2["plan"]["director"]["rev"] == plan["director"].get("rev", 0) + 1
    # el analisis de fondo se lanza tras re-dirigir
    st = api.wait(job, lambda s: s.get("analysis_state") in ("queued", "running", "done", "canceled"), 30)
    assert st["analysis"]["fmt"] == "9:16"
    got = api.get(f"/plan/{job}")
    assert got["plan_rev"] == r2["plan_rev"] and got["plan"]["version"] == 2
    # autoguardado del Studio
    edited = dict(got["plan"], entities=dict(got["plan"].get("entities") or {}, name="Negocio Ficticio"))
    put = api.req("PUT", f"/plan/{job}", {"plan": edited})
    assert put["ok"] and put["plan_rev"] == got["plan_rev"] + 1
    assert api.get(f"/plan/{job}")["plan"]["entities"]["name"] == "Negocio Ficticio"
    assert api.req("PUT", f"/plan/{job}", {"plan": {}}, raw=True)[0] == 400


def _plan(srv):
    """Plan v2 guardado del trabajo (si aun es v1, lo dirige con heuristicas)."""
    plan = srv["api"].get(f"/plan/{srv['job']}")["plan"]
    if plan.get("version") != 2:
        plan = srv["api"].post(f"/direct/{srv['job']}", {"full": True, "use_llm": False,
                                                          "music": "procedural"})["plan"]
    return plan


def test_frame_exacto(srv):
    api, job = srv["api"], srv["job"]
    plan = _plan(srv)
    code, body, hdr = api.post(f"/frame/{job}", {"plan": plan, "t_final": 1.0, "fmt": "9:16"}, raw=True)
    assert code == 200 and hdr.get("content-type") == "image/jpeg" and int(hdr["x-frame-ms"]) >= 0
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(body))
    assert im.size == (1080, 1920)
    code, body, _ = api.post(f"/frame/{job}", {"t_final": 1.0, "quality": "draft"}, raw=True)
    assert code == 200 and Image.open(io.BytesIO(body)).size == (540, 960)   # plan guardado
    assert api.post(f"/frame/{job}", {"plan": plan, "t_final": 1, "fmt": "3:2"}, raw=True)[0] == 400
    v1 = dict(plan, version=1)
    v1.pop("fx")
    assert api.post(f"/frame/{job}", {"plan": v1, "t_final": 1}, raw=True)[0] == 400
    assert api.post("/frame/noexiste", {"plan": plan, "t_final": 1}, raw=True)[0] == 404
    # arrastre del cabezal: los pedidos superados mientras esperan turno responden 204
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as ex:
        codes = list(ex.map(lambda t: api.post(f"/frame/{job}", {"plan": plan, "t_final": t,
                                                                 "drop_stale": True}, raw=True)[0],
                            (0.5, 1.5, 2.5, 3.5)))
    assert set(codes) <= {200, 204} and 200 in codes


def test_musica_escuchable(srv):
    api, job = srv["api"], srv["job"]
    r = api.post(f"/music/{job}", {"plan": _plan(srv), "seed": 11})
    assert r["file"].startswith(f"music_{job}_") and r["source"] == "procedural"
    assert r["duration"] > 3.0 and r["seed"] == 11
    code, body, hdr = api.get(r["url"], raw=True)
    assert code == 200 and len(body) > 100_000 and hdr["content-type"] == "audio/wav"


def test_preview_pro_con_audio(srv):
    api, job = srv["api"], srv["job"]
    assert api.post(f"/preview/{job}", {"plan": _plan(srv), "pro": True, "fmt": "9:16"})["ok"]
    st = api.wait(job, lambda s: s.get("preview_state") in ("done", "error", "canceled"), 600)
    assert st["preview_state"] == "done", st.get("preview_error")
    assert st["preview_done"] is True and st["preview_pct"] == 100
    code, body, _ = api.get(f"/video/{st['preview_file']}", raw=True)
    assert code == 200
    f = srv["data"] / "output" / st["preview_file"]
    info = _probe(f)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == (540, 960)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert st["phase"] == "review"          # la vista previa no mueve la fase del trabajo


def test_render_pro_shorts_y_cancelacion(srv):
    api, job = srv["api"], srv["job"]
    plan = _plan(srv)
    r = api.post(f"/render/{job}", {"plan": plan, "fmt": "9:16", "shorts": True})
    assert r["ok"] and r["pro"] is True
    # mientras procesa: no se acepta otro render ni re-dirigir
    code, _, _ = api.post(f"/render/{job}", {"plan": plan}, raw=True)
    assert code == 409
    assert api.post(f"/direct/{job}", {"plan": plan}, raw=True)[0] == 409
    stages = set()

    def done(s):
        if s.get("stage"):
            stages.add(s["stage"])
        assert s["phase"] != "error", s.get("error")
        return s["phase"] == "done"
    st = api.wait(job, done, 900)
    assert {"render", "done"} <= stages
    clips = st["clips"]
    assert clips[0]["name"] == "Video completo" and clips[0]["stats"]["n_frames"] > 0
    f = srv["data"] / "output" / clips[0]["file"]
    info = _probe(f)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == (1080, 1920)
    from motion import timeline as TL
    exp = TL.Timeline.from_plan(plan, fps=30).n_frames(30) / 30
    assert abs(float(v["duration"]) - exp) < 0.07
    assert len(clips) >= 2 and clips[1]["file"].endswith("_short1.mp4")
    assert (srv["data"] / "output" / clips[1]["file"]).exists()
    stats = clips[0]["stats"]
    assert stats["skipped"] == [] and stats["music"]["source"] == "procedural"
    assert "wav" not in stats["music"]                   # sin rutas locales en el estado
    # exportar el MISMO plan no sube plan_rev (la UI no vuelve a bajar el plan)
    assert st["plan_rev"] == r["plan_rev"]
    # /music del plan exportado: misma cfg que el render -> acierto de cache
    m = api.post(f"/music/{job}", {"plan": plan})
    assert m["cached"] is True, m
    # cancelar un export en curso: vuelve a `review` (el plan sigue valido; no es un error)
    api.post(f"/render/{job}", {"plan": plan, "fmt": "1:1", "shorts": False})
    api.wait(job, lambda s: s.get("phase") == "rendering" and s.get("stage") not in (None, "timeline"), 120)
    assert api.post(f"/cancel/{job}")["ok"]
    st = api.wait(job, lambda s: s["state"] != "running" and s["phase"] != "rendering", 120)
    assert st["phase"] == "review" and st["state"] == "review" and st["error"] is None
    assert "Export cancelado" in st["message"]
    assert not list((srv["data"] / "output").glob("*.part*"))
    # cancelar la vista previa NO mata un export en curso del mismo trabajo (?only=preview)
    api.post(f"/render/{job}", {"plan": plan, "fmt": "9:16", "shorts": False})
    api.post(f"/preview/{job}", {"plan": plan, "pro": True, "fmt": "9:16"})
    time.sleep(0.5)
    assert api.post(f"/cancel/{job}?only=preview")["ok"]
    st = api.wait(job, lambda s: s["phase"] in ("done", "error", "review") and s["state"] != "running", 300)
    assert st["phase"] == "done", st
    assert st["preview_state"] in ("canceled", "done")
    assert api.post(f"/cancel/{job}?only=bogus", raw=True)[0] == 400
    # se conservan solo los 2 exports mas recientes del trabajo (con sus shorts y stats)
    exps = [p for p in (srv["data"] / "output").glob(f"{job}_*.mp4")
            if len(p.stem) == len(job) + 7]
    assert len(exps) <= 2, exps


def test_formato_invalido_y_legacy(srv):
    api, job = srv["api"], srv["job"]
    code, body, _ = api.post(f"/render/{job}", {"plan": _plan(srv), "fmt": "2:1"}, raw=True)
    assert code == 400
    assert api.post("/render/noexiste", {"plan": {}}, raw=True)[0] == 404


def test_ayudantes_del_servidor(srv, tmp_path):
    """Funciones puras de app/server.py (parametros del director, espacio en disco)."""
    code = r'''
import sys, json
sys.path.insert(0, "app")
import server as S
d = S._director_params('{"style": "energetico", "intensity": 3, "seed": "5", "music": "none"}')
assert d == {"style": "energetico", "intensity": 1.0, "seed": 5, "enabled": True,
             "music": {"source": "none"}}, d
assert S._director_params(None)["style"] == "dinamico"
assert S._director_params({"enabled": False})["enabled"] is False
assert S._director_params({"music": {"prompt": "lofi", "bogus": 1}})["music"] == {"prompt": "lofi"}
p = S._apply_music({"fx": {"music": {"source": "ai", "enabled": True}}}, {"source": "none"})
assert p["fx"]["music"]["enabled"] is False
GB = 1 << 30
ok, okm, msg = S.space_check(5 * GB, 4 * GB, 20.0)
assert ok and okm and msg == ""
ok, okm, msg = S.space_check(5 * GB, 4 * GB, 6.0)
assert not ok and "insuficiente" in msg
ok, okm, msg = S.space_check(0, 4 * GB, 6.0)
assert ok and not okm and "procedural" in msg
print("OK")
'''
    env = dict(os.environ, REELFY_DATA=str(tmp_path), REELFY_GATED="0", REELFY_AUTO_DOWNLOAD="0",
               REELFY_OLLAMA_URL="http://127.0.0.1:9")
    r = subprocess.run([PY, "-c", code], cwd=str(SPIKE), env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]


def _multipart(files):
    import uuid as _u
    bnd = "----t" + _u.uuid4().hex
    parts = []
    for f in files:
        parts.append((f"--{bnd}\r\nContent-Disposition: form-data; name=\"files\"; "
                      f"filename=\"{f.name}\"\r\nContent-Type: video/mp4\r\n\r\n").encode())
        parts.append(f.read_bytes())
        parts.append(b"\r\n")
    parts.append(f"--{bnd}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={bnd}"


def test_fuentes_proxy_en_segundo_plano_y_compose(srv, tmp_path):
    api = srv["api"]
    big = tmp_path / "CLIP_ALTO.mp4"                     # lado > 1920: el editor pide proxy
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=s=1080x2400:r=30:d=1", "-f", "lavfi",
                    "-i", "sine=frequency=500:sample_rate=48000:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(big)],
                   check=True)
    data, ctype = _multipart([big])
    req = urllib.request.Request(api.base + "/sources", data=data, method="POST",
                                 headers={"Content-Type": ctype})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        src = json.loads(r.read())["sources"]
    assert len(src) == 1 and src[0]["name"] == "CLIP_ALTO.mp4"
    assert (src[0]["w"], src[0]["h"]) == (1080, 2400) and abs(src[0]["dur"] - 1.0) < 0.1
    sid = src[0]["id"]
    assert api.get(f"/tlsource/{sid}/thumb", raw=True)[0] == 200
    assert api.get(f"/tlsource/{sid}", raw=True)[0] == 200      # original o proxy, nunca a medias
    sources = srv["data"] / "input" / "sources"
    assert json.loads((sources / f"{sid}_meta.json").read_text())["name"] == "CLIP_ALTO.mp4"
    for _ in range(120):                                 # el proxy llega en segundo plano
        if (sources / f"{sid}_proxy.mp4").exists():
            break
        time.sleep(0.25)
    assert (sources / f"{sid}_proxy.mp4").exists() and not list(sources.glob("*.part.mp4"))
    assert time.time() - t0 < 60
    # /compose: sin el modelo de voz responde 409; con el, crea el trabajo (y se cancela)
    code, body, _ = api.post("/compose", {"main": [{"sid": sid, "type": "video", "in": 0, "out": 1}],
                                          "director": {"style": "minimal"}}, raw=True)
    if code == 409:
        assert b"prepar" in body
        return
    assert code == 200
    job = json.loads(body)["job_id"]
    assert api.post(f"/cancel/{job}")["ok"]
    st = api.wait(job, lambda s: s["phase"] in ("error", "review"), 180)
    assert st["phase"] == "error" and st["error"] == "Cancelado."
    assert api.post("/compose", {"main": [{"sid": "noexiste"}]}, raw=True)[0] == 400


def test_put_plan_concurrencia_optimista_y_rev_por_contenido(srv):
    api, job = srv["api"], srv["job"]
    got = api.get(f"/plan/{job}")
    plan, rev = got["plan"], got["plan_rev"]
    # mismo contenido (otro orden de claves): no sube la revision
    same = json.loads(json.dumps(plan, sort_keys=True))
    assert api.req("PUT", f"/plan/{job}", {"plan": same, "base_rev": rev})["plan_rev"] == rev
    edited = dict(plan, entities=dict(plan.get("entities") or {}, name="Otro Negocio"))
    r = api.req("PUT", f"/plan/{job}", {"plan": edited, "base_rev": rev})
    assert r["ok"] and r["plan_rev"] == rev + 1
    # un autoguardado que salio antes (base_rev viejo) no pisa el plan nuevo
    code, body, _ = api.req("PUT", f"/plan/{job}", {"plan": plan, "base_rev": rev}, raw=True)
    assert code == 409 and json.loads(body)["plan_rev"] == rev + 1
    assert api.get(f"/plan/{job}")["plan"]["entities"]["name"] == "Otro Negocio"
    assert api.req("PUT", f"/plan/{job}", {"plan": plan, "base_rev": "x"}, raw=True)[0] == 400


def test_editar_texto_no_cancela_el_analisis_de_fondo(srv):
    """La clave del analisis solo cuenta lo que lo cambia (video, cortes, formato, matte,
    grade): una vista previa con un texto editado ESPERA y reutiliza el analisis en curso."""
    api, job = srv["api"], srv["job"]
    plan = _plan(srv)
    plan = json.loads(json.dumps(plan))
    # cortes nuevos -> base nueva: el analisis tiene trabajo de verdad
    plan["cuts"] = list(plan.get("cuts") or []) + [{"start": 4.0, "end": 4.2, "enabled": True,
                                                     "id": "cut_prueba_%s" % os.urandom(3).hex()}]
    assert api.post(f"/analysis/{job}", {"plan": plan, "fmt": "9:16"})["ok"]
    edited = json.loads(json.dumps(plan))
    t0 = edited["fx"]["text"][0]
    for ln in t0.get("lines") or []:
        ln["text"] = "texto editado"
        break
    else:
        t0["text"] = "texto editado"
    states = []

    def seen(s):
        states.append(s.get("analysis_state"))
        return s.get("preview_state") in ("done", "error", "canceled")
    assert api.post(f"/preview/{job}", {"plan": edited, "pro": True, "fmt": "9:16"})["ok"]
    st = api.wait(job, seen, 600)
    assert st["preview_state"] == "done", st.get("preview_error")
    assert "canceled" not in states, states
    st = api.wait(job, lambda s: s.get("analysis_state") in ("done", "error", "canceled"), 300)
    assert st["analysis_state"] == "done" and st.get("analysis_t") is not None


def test_direct_en_segundo_plano_conserva_musica_elegida(srv):
    api, job = srv["api"], srv["job"]
    plan = _plan(srv)
    plan = json.loads(json.dumps(plan))
    plan["fx"]["music"] = dict(plan["fx"].get("music") or {}, source="procedural", enabled=True)
    rev0 = api.req("PUT", f"/plan/{job}", {"plan": plan})["plan_rev"]
    r = api.post(f"/direct/{job}", {"full": True, "background": True, "use_llm": False,
                                    "style": "elegante", "seed": 9})
    assert r["ok"] and r["background"] is True
    # mientras dirige no se acepta otro /direct
    st = api.get(f"/status/{job}?plan=0")
    if st.get("direct_state") == "running":
        assert api.post(f"/direct/{job}", {"full": True}, raw=True)[0] == 409
    st = api.wait(job, lambda s: s.get("direct_state") in ("done", "error", "canceled"), 300)
    assert st["direct_state"] == "done", st.get("direct_error")
    assert st["direct"]["plan_rev"] == st["plan_rev"] > rev0
    got = api.get(f"/plan/{job}")["plan"]
    assert got["director"]["style"] == "elegante" and got["director"]["seed"] == 9
    # re-dirigir sin `music` conserva la fuente que eligio el usuario (no vuelve a la IA)
    assert got["fx"]["music"]["source"] == "procedural"
    assert "plan" in api.get(f"/status/{job}?rev={rev0}")
    # cancelar la tarea `direct` descarta su resultado (el plan guardado no cambia)
    rev1 = st["plan_rev"]
    api.post(f"/direct/{job}", {"full": True, "background": True, "use_llm": False, "seed": 10})
    assert api.post(f"/cancel/{job}?only=direct")["ok"]
    st = api.wait(job, lambda s: s.get("direct_state") in ("done", "error", "canceled"), 300)
    assert st["direct_state"] == "canceled" and st["plan_rev"] == rev1
