#!/usr/bin/env python3
"""
Reelfy — local app server (FastAPI), interactive 2-phase flow.

  1) POST /analyze  -> fast: transcribe/align/highlights/tracking -> editable PLAN
  2) (user reviews: preview video, edit subtitles, pick shorts)
  3) POST /render/{id} (edited plan) -> heavy render with REAL progress + ETA
  4) preview & download results

Imports the pipeline in-process so it can pass the plan around and stream real
ffmpeg progress. Everything local; no cloud.
"""
import sys
import threading
import time
import uuid
from pathlib import Path

SPIKE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SPIKE / "scripts"))
import paths  # noqa: E402
import pipeline  # noqa: E402

from fastapi import FastAPI, UploadFile, Form, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

INPUT, OUTPUT = paths.INPUT, paths.OUTPUT
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Reelfy")
JOBS: dict[str, dict] = {}

# ---- primer arranque: el modelo grande de whisper se descarga a DATA ----
SETUP = {"state": "ready" if paths.engine_ready() else "missing", "pct": 0, "msg": ""}


def _download_model():
    import urllib.request
    dst = paths._MODEL_DATA
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    try:
        req = urllib.request.Request(paths.MODEL_URL, headers={"User-Agent": "Reelfy/0.1"})
        with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or paths.MODEL_SIZE)
            done = 0
            while chunk := r.read(1 << 20):
                f.write(chunk); done += len(chunk)
                SETUP.update(pct=round(done / total * 100, 1),
                             msg=f"Descargando modelo de voz… {done//(1<<20)} / {total//(1<<20)} MB")
        tmp.rename(dst)
        SETUP.update(state="ready", pct=100, msg="Listo")
    except Exception as e:  # noqa
        tmp.unlink(missing_ok=True)
        SETUP.update(state="error", msg=f"Descarga falló: {e}")


@app.get("/setup")
def setup_status():
    if SETUP["state"] != "downloading" and paths.engine_ready():
        SETUP.update(state="ready", pct=100)
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/version", timeout=0.6)
        ollama = True
    except Exception:  # noqa
        ollama = False
    return {**SETUP, "ollama": ollama, **_lic_state()}


# ---- licencias (solo la app empaquetada; el modo dev del repo no se gatea) ----
import json as _json  # noqa: E402
import os as _os  # noqa: E402
import urllib.request as _url  # noqa: E402

GATED = bool(_os.environ.get("REELFY_DATA"))
LIC_URL = _os.environ.get("REELFY_LICENSE_URL",
                          "https://reelfy.mixiuh.online/api/license/activate")
LIC_FILE = paths.DATA / "license.json"
LIC_GRACE = 7 * 86400  # revalida cada 7 días; offline sigue funcionando ese lapso


def _lic_state():
    if not GATED:
        return {"licensed": True, "name": "dev"}
    try:
        d = _json.loads(LIC_FILE.read_text())
    except Exception:  # noqa
        return {"licensed": False}
    if time.time() < d.get("valid_until", 0):
        return {"licensed": True, "name": d.get("name", "")}
    return {"licensed": False, "expired": True}


def _lic_activate(key):
    """Valida contra la API; True -> renueva la gracia local."""
    body = _json.dumps({"key": key}).encode()
    req = _url.Request(LIC_URL, data=body, headers={"Content-Type": "application/json"})
    r = _json.loads(_url.urlopen(req, timeout=10).read())
    if r.get("ok"):
        LIC_FILE.write_text(_json.dumps({"key": key, "name": r.get("name", ""),
                                         "valid_until": time.time() + LIC_GRACE}))
        return True, r.get("name", "")
    if r.get("reason") == "revoked":
        LIC_FILE.unlink(missing_ok=True)
    return False, r.get("reason", "invalid")


def _lic_revalidate():
    """Al arrancar: si hay licencia, renueva en silencio; sin red, la gracia manda."""
    try:
        key = _json.loads(LIC_FILE.read_text()).get("key")
        if key:
            _lic_activate(key)
    except Exception:  # noqa: sin red o sin licencia — la caché local decide
        pass


if GATED:
    threading.Thread(target=_lic_revalidate, daemon=True).start()


@app.post("/license")
async def license_activate(req: Request):
    body = await req.json()
    key = str(body.get("key", "")).strip().upper()
    if not key:
        raise HTTPException(400, "Falta la llave")
    try:
        ok, info = _lic_activate(key)
    except Exception:  # noqa
        raise HTTPException(502, "No se pudo contactar el servidor de licencias. "
                                 "Revisa tu conexión e intenta de nuevo.")
    if not ok:
        raise HTTPException(403, "Llave revocada." if info == "revoked"
                            else "Llave no válida.")
    return {"ok": True, "name": info}


@app.post("/setup/start")
def setup_start():
    if paths.engine_ready() or SETUP["state"] == "downloading":
        return {"ok": True}
    SETUP.update(state="downloading", pct=0, msg="Iniciando descarga…")
    threading.Thread(target=_download_model, daemon=True).start()
    return {"ok": True}


def _new(job_id, **kw):
    JOBS[job_id] = dict(phase="analyzing", pct=0, message="Iniciando…", eta=None,
                        elapsed=None, plan=None, clips=[], error=None, **kw)


def _analyze(job_id, video, glossary, n):
    job = JOBS[job_id]
    try:
        def step(p, m): job.update(pct=p, message=m)
        plan = pipeline.analyze(video, glossary, n, on_step=step)
        job.update(phase="review", pct=100, message="Análisis listo", plan=plan)
    except Exception as e:  # noqa
        job.update(phase="error", error=f"Análisis falló: {e}")


CUSTOM_KEYS = ("cap_color", "cap_font", "cap_scale", "cap_pos", "zoom_amt", "air", "logo")


def _custom(body):
    return {k: body[k] for k in CUSTOM_KEYS if body.get(k) is not None}


def _render(job_id, plan, dynamic, enhance, style, anim, fmt, music, track, mvol, hook, lang,
            custom):
    job = JOBS[job_id]
    n_shorts = max(1, sum(1 for h in plan.get("highlights", []) if h.get("enabled", True)))
    t0 = time.time()

    def overall(stage, p):
        # full render is the bulk (0..78%); shorts share the last 22%
        if stage == "full":
            o = p * 0.78
        else:
            k = int(stage.replace("short", "")) if stage.startswith("short") else 1
            o = 78 + ((k - 1) + p / 100) / n_shorts * 22
        el = time.time() - t0
        eta = (el / o * (100 - o)) if o > 1 else None
        job.update(pct=round(o, 1), elapsed=round(el), eta=round(eta) if eta else None)

    try:
        def step(m): job.update(message=m)
        clips = pipeline.render_from_plan(plan, OUTPUT, dynamic=dynamic, enhance_audio=enhance,
                                          style=style, anim=anim, fmt=fmt, music=music,
                                          music_track=track, music_volume=mvol, hook=hook,
                                          lang=lang, custom=custom,
                                          on_step=step, on_pct=overall)
        job.update(phase="done", pct=100, message="¡Listo!", clips=clips, eta=0)
    except Exception as e:  # noqa
        job.update(phase="error", error=f"Render falló: {e}")


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/static/{filename}")
def static_file(filename: str):
    f = STATIC / Path(filename).name
    if not f.exists():
        raise HTTPException(404, "No existe")
    return FileResponse(f)


@app.post("/analyze")
async def analyze(video: UploadFile, glossary: str = Form(""), highlights: int = Form(2)):
    ext = Path(video.filename or "v.mp4").suffix.lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".m4v", ".mkv", ".webm"):
        raise HTTPException(400, "Formato no soportado. Usa MP4 o MOV.")
    if not paths.engine_ready():
        raise HTTPException(409, "El motor de IA aún se está preparando (primer arranque).")
    if not _lic_state()["licensed"]:
        raise HTTPException(403, "Activa tu licencia de Reelfy para procesar videos.")
    job_id = uuid.uuid4().hex[:12]
    dst = INPUT / f"{job_id}{ext}"
    with open(dst, "wb") as f:
        while chunk := await video.read(1 << 20):
            f.write(chunk)
    _new(job_id, ext=ext)
    threading.Thread(target=_analyze, args=(job_id, dst, glossary, highlights),
                     daemon=True).start()
    return {"job_id": job_id}


@app.post("/render/{job_id}")
async def render(job_id: str, req: Request):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    body = await req.json()
    plan = body.get("plan") or job["plan"]
    dynamic = bool(body.get("dynamic", True))
    enhance = bool(body.get("enhance_audio", True))
    style = body.get("style", "clasico"); anim = body.get("anim", "none")
    fmt = body.get("format", "9:16")
    music = bool(body.get("music", False))
    track = body.get("music_track", "ambient")
    mvol = float(body.get("music_volume", 0.26))
    hook = bool(body.get("hook", False))
    lang = body.get("lang", "es")
    job.update(phase="rendering", pct=0, message="Preparando el render…", plan=plan)
    threading.Thread(target=_render,
                     args=(job_id, plan, dynamic, enhance, style, anim, fmt, music, track,
                           mvol, hook, lang, _custom(body)),
                     daemon=True).start()
    return {"ok": True}


@app.post("/preview/{job_id}")
async def preview(job_id: str, req: Request):
    """Render a fast low-res sample of the first seconds so the user sees the real
    look (captions/tracking/blur/zoom) before committing to the full export."""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    body = await req.json()
    plan = body.get("plan") or job["plan"]
    out = OUTPUT / f"{job_id}_preview.mp4"
    enhance = bool(body.get("enhance_audio", True))
    style = body.get("style", "clasico"); anim = body.get("anim", "none")
    fmt = body.get("format", "9:16")
    music = bool(body.get("music", False))
    track = body.get("music_track", "ambient"); mvol = float(body.get("music_volume", 0.26))
    lang = body.get("lang", "es")
    try:
        await run_in_threadpool(pipeline.render_preview, plan, out, 7, enhance, style, anim,
                                fmt, music, track, mvol, lang, _custom(body))
    except Exception as e:  # noqa
        raise HTTPException(500, f"Preview falló: {e}")
    return {"file": out.name}


BRAND = paths.BRAND
PRESETS_F = BRAND / "presets.json"


@app.post("/brand/logo")
async def upload_logo(logo: UploadFile):
    if not (logo.content_type or "").startswith("image/"):
        raise HTTPException(400, "Sube una imagen PNG/JPG (idealmente PNG con transparencia).")
    data = await logo.read()
    (BRAND / "logo.png").write_bytes(data)
    return {"ok": True}


@app.get("/brand/logo")
def get_logo():
    f = BRAND / "logo.png"
    if not f.exists():
        raise HTTPException(404, "Sin logo")
    return FileResponse(f, media_type="image/png")


@app.get("/presets")
def get_presets():
    import json
    return json.loads(PRESETS_F.read_text()) if PRESETS_F.exists() else {}


@app.post("/presets")
async def save_preset(req: Request):
    import json
    body = await req.json()
    name, config = body.get("name", "").strip(), body.get("config", {})
    if not name:
        raise HTTPException(400, "Falta el nombre del preset")
    data = json.loads(PRESETS_F.read_text()) if PRESETS_F.exists() else {}
    data[name] = config
    PRESETS_F.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return {"ok": True, "presets": list(data.keys())}


@app.delete("/presets/{name}")
def delete_preset(name: str):
    import json
    data = json.loads(PRESETS_F.read_text()) if PRESETS_F.exists() else {}
    data.pop(name, None)
    PRESETS_F.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return {"ok": True, "presets": list(data.keys())}


@app.get("/tracks")
def tracks():
    d = SPIKE / "assets/music"
    return [p.stem for p in sorted(d.glob("*.m4a"))] if d.exists() else []


@app.get("/music/{track}")
def music(track: str):
    f = SPIKE / "assets/music" / f"{Path(track).stem}.m4a"
    if not f.exists():
        raise HTTPException(404, "No existe")
    return FileResponse(f, media_type="audio/mp4", headers={"Accept-Ranges": "bytes"})


@app.post("/translate/{job_id}")
async def translate(job_id: str, req: Request):
    """Translate captions + short titles to EN for LIVE preview/editing in the studio.
    The (possibly edited) EN phrases are then sent back inside the plan at export."""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    body = await req.json()
    plan = body.get("plan") or job["plan"]
    try:
        phrases_en = await run_in_threadpool(pipeline.translate_mod.translate_phrases,
                                             plan["phrases"])
        titles = [h.get("title", "") for h in plan.get("highlights", [])]
        titles_en = await run_in_threadpool(pipeline.translate_mod.translate_texts, titles)
    except Exception as e:  # noqa
        msg = str(e)
        if "11434" in msg or "Connection refused" in msg or "urlopen" in msg:
            msg = ("La traducción usa la IA local (Ollama), que no está instalada. "
                   "Descárgala gratis de ollama.com y ejecuta: ollama pull qwen2.5:7b")
        raise HTTPException(500, f"Traducción falló: {msg}")
    return {"phrases_en": phrases_en, "titles_en": titles_en}


@app.get("/status/{job_id}")
def status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    return JSONResponse(job)


@app.get("/source/{job_id}")
def source(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "No existe")
    f = INPUT / f"{job_id}{job['ext']}"
    return FileResponse(f, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})


@app.get("/video/{filename}")
def video(filename: str):
    f = OUTPUT / Path(filename).name
    if not f.exists():
        raise HTTPException(404, "No existe")
    return FileResponse(f, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})


@app.get("/image/{filename}")
def image(filename: str):
    f = OUTPUT / Path(filename).name
    if not f.exists():
        raise HTTPException(404, "No existe")
    return FileResponse(f, media_type="image/jpeg")


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("REELFY_PORT", 8000)))
