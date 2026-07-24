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
import atexit
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from pathlib import Path

SPIKE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SPIKE / "scripts"))
import paths  # noqa: E402

# ---- LLM embebido: si el binario de ollama viene en el bundle, corre en un
# puerto propio con modelos en DATA. El pipeline (highlights/translate) apunta
# ahí por REELFY_OLLAMA_URL. En dev (sin bundle) usa el ollama del sistema. ----
OLLAMA_PORT = 11499
# "ollama-runtime" en la ruta marca el binario embebido (independiente de .exe en Windows)
BUNDLED_OLLAMA = "ollama-runtime" in str(paths.OLLAMA_BIN).replace("\\", "/")
if BUNDLED_OLLAMA:
    os.environ["REELFY_OLLAMA_URL"] = f"http://127.0.0.1:{OLLAMA_PORT}"
    os.environ["REELFY_LLM_MODEL"] = paths.LLM_MODEL

import pipeline  # noqa: E402  (lee REELFY_OLLAMA_URL ya definido)

from fastapi import FastAPI, UploadFile, Form, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, PlainTextResponse  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

INPUT, OUTPUT = paths.INPUT, paths.OUTPUT
STATIC = Path(__file__).resolve().parent / "static"

# ---- logging: tee de stdout/stderr al archivo DATA/logs/reelfy.log ----
LOG_FILE = paths.LOGS / "reelfy.log"


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s); st.flush()
            except Exception:  # noqa
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:  # noqa
                pass

    def isatty(self):
        return getattr(self.streams[0], "isatty", lambda: False)()

    def fileno(self):
        return self.streams[0].fileno()


_logf = open(LOG_FILE, "a", buffering=1)
sys.stdout = _Tee(sys.__stdout__, _logf)
sys.stderr = _Tee(sys.__stderr__, _logf)
print(f"\n===== Reelfy arranque {time.strftime('%Y-%m-%d %H:%M:%S')} "
      f"(LLM embebido={BUNDLED_OLLAMA}) =====")

app = FastAPI(title="Reelfy")

# Cola de trabajos persistente (SQLite en DATA) + workers. Ver app/jobstore.py.
import jobstore  # noqa: E402
from jobstore import Progress  # noqa: E402

# ---- ciclo de vida del ollama embebido ----
_ollama_proc = None


def _start_ollama():
    global _ollama_proc
    if not BUNDLED_OLLAMA:
        return
    env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{OLLAMA_PORT}",
               OLLAMA_MODELS=str(paths.OLLAMA_MODELS))
    try:
        _ollama_proc = subprocess.Popen([paths.OLLAMA_BIN, "serve"], env=env,
                                        stdout=_logf, stderr=_logf)
        atexit.register(lambda: _ollama_proc and _ollama_proc.terminate())
    except Exception as e:  # noqa
        print(f"ollama no arrancó: {e}")


def _ollama_up():
    url = os.environ.get("REELFY_OLLAMA_URL", "http://localhost:11434")
    try:
        urllib.request.urlopen(url + "/api/version", timeout=0.6)
        return True
    except Exception:  # noqa
        return False


def _llm_ready():
    """El modelo LLM está descargado en el ollama embebido/sistema."""
    url = os.environ.get("REELFY_OLLAMA_URL", "http://localhost:11434")
    try:
        r = json.loads(urllib.request.urlopen(url + "/api/tags", timeout=1).read())
        names = [m.get("name", "") for m in r.get("models", [])]
        return any(paths.LLM_MODEL.split(":")[0] in n for n in names)
    except Exception:  # noqa
        return False


if BUNDLED_OLLAMA:
    _start_ollama()

# ---- primer arranque: prepara TODO (voz + IA de lenguaje + alineador) ----
SETUP = {"state": "ready", "pct": 0, "msg": ""}


def _needs_setup():
    if not paths.engine_ready():
        return True
    if BUNDLED_OLLAMA and not _llm_ready():
        return True
    return False


SETUP["state"] = "missing" if _needs_setup() else "ready"


def _dl_file(url, dst, label, lo, hi, size_hint=0):
    """Descarga con progreso [lo,hi]. VERIFICA que se completó (Content-Length) y
    reintenta si se truncó — así una descarga interrumpida nunca queda como buena."""
    RETRIES = 5
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    done = total = 0
    last_err = ""
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Reelfy/0.1"})
            done = 0
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or size_hint) or 1
                while chunk := r.read(1 << 20):
                    f.write(chunk); done += len(chunk)
                    SETUP.update(pct=round(lo + (hi - lo) * done / total, 1),
                                 msg=f"{label}… {done // (1 << 20)} / {total // (1 << 20)} MB")
            if total <= 1 or done >= total:                 # completa y verificada
                tmp.rename(dst)
                return
            last_err = f"incompleta {done // (1 << 20)}/{total // (1 << 20)} MB"
        except Exception as e:                              # noqa: red caída, timeout, etc.
            last_err = str(e)
        SETUP.update(msg=f"{label}… reintentando ({attempt + 2}/{RETRIES}) — {last_err}")
        time.sleep(min(2 ** attempt, 8))                    # backoff
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"No se pudo descargar «{label}» tras {RETRIES} intentos ({last_err}). "
                       f"Revisa tu conexión a internet y reintenta.")


def _pull_llm(lo, hi):
    """ollama pull con progreso (stream JSON), reintenta y VERIFICA que quedó listo."""
    for attempt in range(4):
        try:
            url = os.environ["REELFY_OLLAMA_URL"] + "/api/pull"
            body = json.dumps({"model": paths.LLM_MODEL}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            err = None
            with urllib.request.urlopen(req, timeout=120) as r:
                for line in r:
                    try:
                        d = json.loads(line)
                    except Exception:  # noqa
                        continue
                    if d.get("error"):
                        err = d["error"]
                    tot, comp = d.get("total"), d.get("completed")
                    if tot and comp:
                        SETUP.update(pct=round(lo + (hi - lo) * comp / tot, 1),
                                     msg=f"Descargando IA de lenguaje… {comp // (1 << 20)} / {tot // (1 << 20)} MB")
            if not err and _llm_ready():                    # descargado y verificado
                return
        except Exception as e:  # noqa
            err = str(e)
        SETUP.update(msg=f"IA de lenguaje… reintentando ({attempt + 2}/4)")
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("No se pudo preparar la IA de lenguaje. Revisa tu conexión y reintenta.")


def _ensure_ollama(timeout=30):
    """Garantiza que el ollama embebido responde; lo reinicia si murió."""
    for _ in range(int(timeout * 2)):
        if _ollama_up():
            return True
        if _ollama_proc is not None and _ollama_proc.poll() is not None:
            print("ollama murió, reiniciando…"); _start_ollama()
        time.sleep(0.5)
    return _ollama_up()


def _disk_free_gb():
    import shutil
    return shutil.disk_usage(paths.DATA).free / (1 << 30)


def _run_setup():
    try:
        need_dl = not paths.engine_ready() or (BUNDLED_OLLAMA and not _llm_ready())
        if need_dl and _disk_free_gb() < 6:
            raise RuntimeError(f"Espacio en disco insuficiente ({_disk_free_gb():.1f} GB libres). "
                               f"Reelfy necesita ~6 GB para los modelos de IA. Libera espacio y reintenta.")
        # 1) modelo de voz (whisper) 0..55% — verifica tamaño; si estaba corrupto, lo re-baja
        if not paths.engine_ready():
            paths._MODEL_DATA.unlink(missing_ok=True)       # limpia un posible modelo truncado
            _dl_file(paths.MODEL_URL, paths._MODEL_DATA, "Descargando modelo de voz",
                     0, 55, paths.MODEL_SIZE)
            if not paths.engine_ready():
                raise RuntimeError("El modelo de voz quedó incompleto. Reintenta.")
        # 2) IA de lenguaje (LLM en el ollama embebido) 55..90%
        if BUNDLED_OLLAMA:
            SETUP.update(msg="Iniciando la IA de lenguaje…")
            _ensure_ollama()
            if not _llm_ready():
                _pull_llm(55, 90)
        # 3) afinador de subtítulos 90..100% (el "wedge": sincronía fina).
        #    Modelo MMS_FA en ONNX int8 (~357 MB) que corre con onnxruntime.
        #    Opcional: si falla, analyze sigue con el timing de whisper.
        if not paths.aligner_ready():
            try:
                paths._ALIGNER_DATA.unlink(missing_ok=True)   # limpia uno truncado
                _dl_file(paths.ALIGNER_URL, paths._ALIGNER_DATA,
                         "Descargando el afinador de subtítulos", 90, 98,
                         paths.ALIGNER_SIZE)
            except Exception:  # noqa
                traceback.print_exc()   # no bloquea el setup
        SETUP.update(pct=98, msg="Preparando la sincronía de subtítulos…")
        try:
            import align as align_mod
            for _ in range(3):
                if align_mod.preload():
                    break
                time.sleep(2)
        except Exception:  # noqa
            pass  # analyze funciona sin él (usa el timing de whisper)
        SETUP.update(state="ready", pct=100, msg="Listo")
    except Exception as e:  # noqa
        traceback.print_exc()
        SETUP.update(state="error", pct=SETUP.get("pct", 0),
                     msg=str(e) or "La preparación falló. Reintenta.")


@app.get("/setup")
def setup_status():
    # re-detecta: si algo se corrompió/borró tras estar listo, vuelve a "missing"
    if SETUP["state"] not in ("downloading", "error"):
        SETUP["state"] = "ready" if not _needs_setup() else "missing"
        if SETUP["state"] == "ready":
            SETUP["pct"] = 100
    return {**SETUP, "llm": _llm_ready(), **_lic_state()}


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
    if not _needs_setup() or SETUP["state"] == "downloading":
        return {"ok": True}
    SETUP.update(state="downloading", pct=0, msg="Preparando…")
    threading.Thread(target=_run_setup, daemon=True).start()
    return {"ok": True}


@app.get("/logs")
def get_logs():
    """El registro de la sesión, para diagnosticar y compartir reportes."""
    if not LOG_FILE.exists():
        return PlainTextResponse("(sin registro)")
    data = LOG_FILE.read_text(errors="replace")
    return PlainTextResponse(data[-200_000:])  # últimos ~200 KB


@app.get("/logs/download")
def download_logs():
    """Descarga el registro como .txt (se guarda directo, sin copiar a mano)."""
    data = LOG_FILE.read_text(errors="replace") if LOG_FILE.exists() else "(sin registro)"
    name = f"reelfy-log-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    return PlainTextResponse(data[-500_000:], media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---- handlers de los workers (corren en el pool de jobstore) ----

def _analyze(job_id, job):
    """Handler 'analyze': job['params'] = {glossary, n}."""
    prog = Progress(job_id)
    p = job["params"] or {}
    video = INPUT / f"{job_id}{job['ext']}"

    def step(pct, m):
        prog.update(pct=pct, message=m)
    plan = pipeline.analyze(video, p.get("glossary", ""), p.get("n", 2), on_step=step)
    jobstore.update(job_id, state="review", phase="review", pct=100,
                    message="Análisis listo", plan=plan)


def _compose(job_id, job):
    """Handler 'compose': arma la composición del timeline (varios clips/imágenes +
    overlays) en UN video y luego lo analiza como cualquier fuente. job['params'] =
    {spec, glossary, n}. La IA se aplica sobre la composición (timeline + IA)."""
    import compose as compose_mod
    prog = Progress(job_id)
    p = job["params"] or {}
    spec = p.get("spec") or {}
    video = INPUT / f"{job_id}.mp4"

    def cstep(m):
        prog.update(pct=4, message=m)
    prog.update(pct=2, message="Montando tu línea de tiempo…")
    compose_mod.compose(spec, str(video), paths.WORK / f"compose_{job_id}", on_step=cstep)

    def step(pct, m):
        prog.update(pct=pct, message=m)
    plan = pipeline.analyze(video, p.get("glossary", ""), p.get("n", 2), on_step=step)
    jobstore.update(job_id, state="review", phase="review", pct=100,
                    message="Análisis listo", plan=plan)


CUSTOM_KEYS = ("cap_color", "cap_font", "cap_scale", "cap_pos", "zoom_amt", "air", "logo",
               "base_color", "kw_color", "cap_outline", "cap_shadow", "cap_case",
               "cap_margin", "effect")


def _custom(body):
    return {k: body[k] for k in CUSTOM_KEYS if body.get(k) is not None}


def _render(job_id, job):
    """Handler 'render': job['params'] trae las opciones; job['plan'] el plan."""
    prog = Progress(job_id)
    p = job["params"] or {}
    plan = job["plan"]
    n_shorts = max(1, sum(1 for h in plan.get("highlights", []) if h.get("enabled", True)))
    t0 = time.time()

    def overall(stage, pct):
        # full render is the bulk (0..78%); shorts share the last 22%
        if stage == "full":
            o = pct * 0.78
        else:
            k = int(stage.replace("short", "")) if stage.startswith("short") else 1
            o = 78 + ((k - 1) + pct / 100) / n_shorts * 22
        el = time.time() - t0
        eta = (el / o * (100 - o)) if o > 1 else None
        prog.update(pct=round(o, 1), elapsed=round(el), eta=round(eta) if eta else None)

    def step(m):
        prog.update(force=True, message=m)
    clips = pipeline.render_from_plan(plan, OUTPUT, dynamic=p.get("dynamic", True),
                                      enhance_audio=p.get("enhance", True),
                                      style=p.get("style", "clasico"),
                                      anim=p.get("anim", "none"), fmt=p.get("fmt", "9:16"),
                                      music=p.get("music", False),
                                      music_track=p.get("track", "ambient"),
                                      music_volume=p.get("mvol", 0.26),
                                      hook=p.get("hook", False), lang=p.get("lang", "es"),
                                      custom=p.get("custom") or {},
                                      smart=p.get("smart", True),
                                      platform=p.get("platform", "none"),
                                      broll=p.get("broll", False),
                                      on_step=step, on_pct=overall)
    jobstore.update(job_id, state="done", phase="done", pct=100,
                    message="¡Listo!", clips=clips, eta=0)


jobstore.init()
jobstore.start_workers({"analyze": _analyze, "render": _render, "compose": _compose})


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
    jobstore.create(job_id, kind="analyze", ext=ext,
                    params={"glossary": glossary, "n": highlights})
    return {"job_id": job_id}


SOURCES = INPUT / "sources"


@app.post("/sources")
async def add_sources(files: list[UploadFile]):
    """Sube uno o varios archivos fuente (video o imagen) para el editor de timeline.
    Devuelve por cada uno: id, tipo, duración, dimensiones y una miniatura."""
    import compose as compose_mod
    SOURCES.mkdir(parents=True, exist_ok=True)
    out = []
    for up in files:
        ext = Path(up.filename or "f").suffix.lower()
        is_img = ext in (".jpg", ".jpeg", ".png", ".webp", ".heic")
        is_vid = ext in (".mp4", ".mov", ".m4v", ".mkv", ".webm")
        if not (is_img or is_vid):
            continue
        sid = uuid.uuid4().hex[:12]
        dst = SOURCES / f"{sid}{ext}"
        with open(dst, "wb") as f:
            while chunk := await up.read(1 << 20):
                f.write(chunk)
        info = compose_mod.probe(dst)
        typ = "image" if is_img else "video"
        thumb = SOURCES / f"{sid}_t.jpg"
        try:
            if is_img:
                subprocess.run([str(paths.FFMPEG), "-y", "-i", str(dst), "-vf",
                                "scale=320:-2", "-frames:v", "1", str(thumb)],
                               check=True, stderr=subprocess.DEVNULL)
            else:
                ss = min(1.0, (info.get("dur") or 2) / 2)
                subprocess.run([str(paths.FFMPEG), "-y", "-ss", str(ss), "-i", str(dst),
                                "-vf", "scale=320:-2", "-frames:v", "1", str(thumb)],
                               check=True, stderr=subprocess.DEVNULL)
        except Exception:  # noqa
            thumb = None
        out.append({"id": sid, "name": up.filename, "type": typ, "ext": ext,
                    "dur": info.get("dur", 0), "w": info.get("w", 0), "h": info.get("h", 0),
                    "thumb": f"/tlsource/{sid}/thumb" if thumb else None})
    return {"sources": out}


@app.get("/tlsource/{sid}/thumb")
def source_thumb(sid: str):
    sid = Path(sid).stem
    f = SOURCES / f"{sid}_t.jpg"
    if not f.exists():
        raise HTTPException(404, "No existe")
    return FileResponse(f, media_type="image/jpeg")


@app.get("/tlsource/{sid}")
def source_file(sid: str):
    sid = Path(sid).stem
    for p in SOURCES.glob(f"{sid}.*"):
        if not p.name.endswith("_t.jpg"):
            return FileResponse(p, headers={"Accept-Ranges": "bytes"})
    raise HTTPException(404, "No existe")


@app.post("/compose")
async def compose_timeline(req: Request):
    """Recibe la composición del timeline y arranca compose->analyze en un job.
    body: {main:[{sid,type,in,out|dur}], overlays:[{sid,type,start,dur,pos,size}],
           glossary, highlights}."""
    if not paths.engine_ready():
        raise HTTPException(409, "El motor de IA aún se está preparando (primer arranque).")
    if not _lic_state()["licensed"]:
        raise HTTPException(403, "Activa tu licencia de Reelfy para procesar videos.")
    body = await req.json()

    def resolve(sid):
        for p in SOURCES.glob(f"{Path(sid).stem}.*"):
            if not p.name.endswith("_t.jpg"):
                return str(p)
        return None
    main = []
    for s in body.get("main", []):
        path = resolve(s.get("sid", ""))
        if not path:
            continue
        item = {"path": path, "type": s.get("type", "video")}
        if item["type"] == "image":
            item["dur"] = float(s.get("dur", 3.0))
        else:
            item["in"] = float(s.get("in", 0)); item["out"] = float(s.get("out", 0) or 5)
        main.append(item)
    if not main:
        raise HTTPException(400, "Agrega al menos un clip a la pista principal.")
    overlays = []
    for o in body.get("overlays", []):
        path = resolve(o.get("sid", ""))
        if not path:
            continue
        overlays.append({"path": path, "type": o.get("type", "image"),
                         "start": float(o.get("start", 0)), "dur": float(o.get("dur", 2.5)),
                         "pos": o.get("pos", "center"), "size": float(o.get("size", 0.35))})
    spec = {"fps": 30, "main": main, "overlays": overlays}
    job_id = uuid.uuid4().hex[:12]
    jobstore.create(job_id, kind="compose", ext=".mp4",
                    params={"spec": spec, "glossary": body.get("glossary", ""),
                            "n": int(body.get("highlights", 2))})
    return {"job_id": job_id}


@app.post("/render/{job_id}")
async def render(job_id: str, req: Request):
    job = jobstore.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    body = await req.json()
    plan = body.get("plan") or job["plan"]
    if not plan:
        raise HTTPException(409, "El job no tiene plan; analiza el video primero.")
    params = dict(dynamic=bool(body.get("dynamic", True)),
                  enhance=bool(body.get("enhance_audio", True)),
                  style=body.get("style", "clasico"), anim=body.get("anim", "none"),
                  fmt=body.get("format", "9:16"), music=bool(body.get("music", False)),
                  track=body.get("music_track", "ambient"),
                  mvol=float(body.get("music_volume", 0.26)),
                  hook=bool(body.get("hook", False)), lang=body.get("lang", "es"),
                  smart=bool(body.get("smart", True)),
                  platform=body.get("platform", "none"),
                  broll=bool(body.get("broll", False)), custom=_custom(body))
    jobstore.requeue(job_id, kind="render", params=params, plan=plan,
                     message="Preparando el render…")
    return {"ok": True}


@app.post("/cancel/{job_id}")
def cancel(job_id: str):
    if not jobstore.get(job_id):
        raise HTTPException(404, "Job no encontrado")
    jobstore.request_cancel(job_id)
    return {"ok": True}


@app.get("/jobs")
def jobs_recent():
    return jobstore.recent()


@app.post("/preview/{job_id}")
async def preview(job_id: str, req: Request):
    """Render a fast low-res sample of the first seconds so the user sees the real
    look (captions/tracking/blur/zoom) before committing to the full export."""
    job = jobstore.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    body = await req.json()
    plan = body.get("plan") or job["plan"]
    enhance = bool(body.get("enhance_audio", True))
    style = body.get("style", "clasico"); anim = body.get("anim", "none")
    fmt = body.get("format", "9:16")
    music = bool(body.get("music", False))
    track = body.get("music_track", "ambient"); mvol = float(body.get("music_volume", 0.26))
    lang = body.get("lang", "es")
    # 3 modos de preview, todos con el pipeline REAL (=export, solo baja resolución):
    #  - full=true      -> PROXY del video COMPLETO (reproducción exacta, con audio)
    #  - start>0        -> VENTANA de 4s alrededor del playhead (frame exacto al pausar)
    #  - start=0        -> primeros 7s
    start = float(body.get("start", 0) or 0)
    full = bool(body.get("full", False))
    windowed = (not full) and start > 0.05
    if full:
        secs = float((plan or {}).get("duration") or 0) or 7
        start = 0.0
        out = OUTPUT / f"{job_id}_proxy.mp4"
    else:
        secs = 4 if windowed else 7
        out = OUTPUT / (f"{job_id}_win.mp4" if windowed else f"{job_id}_preview.mp4")
    try:
        if full:
            # PROXY = MISMO camino que el export (cortes/tracking/captions/audio/
            # música), solo a 540p y sin shorts -> reproducción 100% idéntica.
            await run_in_threadpool(
                pipeline.render_from_plan, plan, OUTPUT, bool(body.get("dynamic", True)),
                enhance, style, anim, fmt, music, track, mvol,
                bool(body.get("hook", False)), lang, _custom(body),
                bool(body.get("smart", True)), body.get("platform", "none"),
                bool(body.get("broll", False)), True, str(out))
        else:
            await run_in_threadpool(pipeline.render_preview, plan, out, secs, enhance, style, anim,
                                    fmt, music, track, mvol, lang, _custom(body),
                                    bool(body.get("smart", True)), start,
                                    body.get("platform", "none"), bool(body.get("broll", False)))
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


@app.get("/settings")
def get_settings():
    import broll as broll_mod
    return {"pexels": bool(broll_mod.get_key())}


@app.post("/settings")
async def set_settings(req: Request):
    import broll as broll_mod
    body = await req.json()
    if body.get("pexels_key"):
        broll_mod.set_key(body["pexels_key"])
    return {"ok": True, "pexels": bool(broll_mod.get_key())}


@app.get("/tracks")
def tracks():
    """Catálogo de música con metadata (título, género, moods, descripción) para el
    buscador del Studio. Cae a los .m4a sueltos si no hay tracks.json."""
    d = SPIKE / "assets/music"
    if not d.exists():
        return {"tracks": []}
    meta = {}
    mf = d / "tracks.json"
    if mf.exists():
        try:
            for t in json.loads(mf.read_text()).get("tracks", []):
                meta[t["id"]] = t
        except Exception:  # noqa
            pass
    out = []
    for p in sorted(d.glob("*.m4a")):
        m = meta.get(p.stem, {})
        out.append({"id": p.stem, "title": m.get("title", p.stem.capitalize()),
                    "genre": m.get("genre", ""), "moods": m.get("moods", []),
                    "desc": m.get("desc", ""), "bpm": m.get("bpm")})
    return {"tracks": out}


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
    job = jobstore.get(job_id)
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
    st = jobstore.status(job_id)
    if not st:
        raise HTTPException(404, "Job no encontrado")
    return JSONResponse(st)


@app.get("/source/{job_id}")
def source(job_id: str):
    job = jobstore.get(job_id)
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
