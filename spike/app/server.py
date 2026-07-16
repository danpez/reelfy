#!/usr/bin/env python3
"""
Reelfy — local app server (FastAPI).

Wraps the CLI pipeline as a subprocess and exposes a tiny web UI so a non-editor
can: drop a video -> (optional options) -> watch progress -> preview & download
the AI-cut shorts. Everything runs locally; no cloud.

Run:  cd spike && ./app/run.sh      (opens http://127.0.0.1:8000)
"""
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

SPIKE = Path(__file__).resolve().parent.parent
PY = str(SPIKE / ".venv/bin/python")
PIPELINE = str(SPIKE / "scripts/pipeline.py")
INPUT = SPIKE / "input"
OUTPUT = SPIKE / "output"
STATIC = Path(__file__).resolve().parent / "static"
INPUT.mkdir(exist_ok=True); OUTPUT.mkdir(exist_ok=True)

app = FastAPI(title="Reelfy")
JOBS: dict[str, dict] = {}   # job_id -> {state, pct, step, message, clips, error}

# pipeline stdout marker -> (percent, friendly label)
STEPS = [
    (re.compile(r"\[1/5\] extract audio"),        (5,  "Extrayendo audio…")),
    (re.compile(r"\[2/5\] transcribe"),           (15, "Transcribiendo (español)…")),
    (re.compile(r"forced.aligned"),               (32, "Afinando la sincronía de subtítulos…")),
    (re.compile(r"\[4/5\] build"),                (38, "Preparando subtítulos…")),
    (re.compile(r"emphasis beats"),               (46, "Detectando momentos de énfasis…")),
    (re.compile(r"\[5/5\]"),                       (48, "Preparando el montaje…")),
    (re.compile(r"tracked \d+ samples"),          (52, "Montando el video vertical (esto tarda un poco)…")),
    (re.compile(r"✅ full:"),                      (78, "Eligiendo los mejores momentos (IA)…")),
    (re.compile(r"short 1:"),                      (82, "Cortando short 1…")),
    (re.compile(r"short 2:"),                      (90, "Cortando short 2…")),
    (re.compile(r"✅ short:"),                     (95, "Puliendo shorts…")),
]


def _run(job_id: str, video: Path, glossary: str, n: int, dynamic: bool):
    job = JOBS[job_id]
    cmd = [PY, "-u", PIPELINE, str(video), "--highlights", str(n)]  # -u: stream progress live
    if glossary.strip():
        cmd += ["--glossary", glossary.strip()]
    if dynamic:
        cmd += ["--dynamic"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, cwd=str(SPIKE))
        for line in proc.stdout:
            for pat, (pct, label) in STEPS:
                if pat.search(line):
                    job.update(pct=max(job["pct"], pct), message=label)
                    break
        proc.wait()
        if proc.returncode != 0:
            job.update(state="error", error="El procesamiento falló.")
            return
    except Exception as e:  # noqa
        job.update(state="error", error=str(e)); return

    stem = video.stem
    full = OUTPUT / f"{stem}_reelfy.mp4"
    shorts = sorted(OUTPUT.glob(f"{stem}_short*.mp4"))
    clips = [{"name": "Video completo", "file": full.name}] if full.exists() else []
    clips += [{"name": f"Short {i+1}", "file": s.name} for i, s in enumerate(shorts)]
    job.update(state="done", pct=100, message="¡Listo!", clips=clips)


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.post("/process")
async def process(video: UploadFile, glossary: str = Form(""),
                  highlights: int = Form(2), dynamic: bool = Form(True)):
    ext = Path(video.filename or "video.mp4").suffix.lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".m4v", ".mkv", ".webm"):
        raise HTTPException(400, "Formato no soportado. Usa MP4 o MOV.")
    job_id = uuid.uuid4().hex[:12]
    dst = INPUT / f"{job_id}{ext}"
    with open(dst, "wb") as f:
        while chunk := await video.read(1 << 20):
            f.write(chunk)
    JOBS[job_id] = dict(state="running", pct=2, step=0,
                        message="Subiendo…", clips=[], error=None)
    threading.Thread(target=_run, args=(job_id, dst, glossary, highlights, dynamic),
                     daemon=True).start()
    return {"job_id": job_id}


@app.get("/status/{job_id}")
def status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    return JSONResponse(job)


@app.get("/video/{filename}")
def video(filename: str):
    f = OUTPUT / Path(filename).name
    if not f.exists():
        raise HTTPException(404, "No existe")
    return FileResponse(f, media_type="video/mp4",
                        headers={"Accept-Ranges": "bytes"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
