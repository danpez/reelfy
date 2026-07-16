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
import pipeline  # noqa: E402

from fastapi import FastAPI, UploadFile, Form, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

INPUT = SPIKE / "input"; OUTPUT = SPIKE / "output"
STATIC = Path(__file__).resolve().parent / "static"
INPUT.mkdir(exist_ok=True); OUTPUT.mkdir(exist_ok=True)

app = FastAPI(title="Reelfy")
JOBS: dict[str, dict] = {}


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


def _render(job_id, plan, dynamic):
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
        clips = pipeline.render_from_plan(plan, OUTPUT, dynamic=dynamic,
                                          on_step=step, on_pct=overall)
        job.update(phase="done", pct=100, message="¡Listo!", clips=clips, eta=0)
    except Exception as e:  # noqa
        job.update(phase="error", error=f"Render falló: {e}")


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.post("/analyze")
async def analyze(video: UploadFile, glossary: str = Form(""), highlights: int = Form(2)):
    ext = Path(video.filename or "v.mp4").suffix.lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".m4v", ".mkv", ".webm"):
        raise HTTPException(400, "Formato no soportado. Usa MP4 o MOV.")
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
    job.update(phase="rendering", pct=0, message="Preparando el render…", plan=plan)
    threading.Thread(target=_render, args=(job_id, plan, dynamic), daemon=True).start()
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
    try:
        await run_in_threadpool(pipeline.render_preview, plan, out, 7)
    except Exception as e:  # noqa
        raise HTTPException(500, f"Preview falló: {e}")
    return {"file": out.name}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
