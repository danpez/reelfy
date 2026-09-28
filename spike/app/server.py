#!/usr/bin/env python3
"""
Reelfy — local app server (FastAPI), interactive 2-phase flow.

  1) POST /analyze o /compose -> analisis (transcribir/alinear/momentos) + DIRECTOR
     (motor pro v0.5): el plan que llega a `review` ya es v2 con toda la edicion (fx)
  2) (el Studio revisa: cuadro exacto /frame, vista previa real /preview pro, re-dirigir
     /direct, musica /music, catalogos /fx/*)
  3) POST /render/{id} (plan editado) -> render pro (plan v2) o legacy, con progreso
     por etapa, ETA y cancelacion
  4) preview & download results

Contrato de la API del motor pro: docs/MOTION-ENGINE.md seccion 6.1.
Importa el pipeline en proceso para pasar el plan y reportar progreso real. Todo local.
"""
if __name__ == "__main__":
    # App empaquetada: los procesos hijos (spawn) del compositor no deben re-ejecutar el
    # servidor. render._SafeMain lo evita; freeze_support cubre builds congelados.
    import multiprocessing
    multiprocessing.freeze_support()
# Proceso hijo (spawn) que reimporto este archivo como __mp_main__: NO arranca nada (ni
# workers, ni recover() de la base -marcaria el export en curso como interrumpido-, ni
# ollama, ni el log). Pasaba con un export y una vista previa a la vez: dos hilos dentro de
# render._SafeMain se restauraban __file__ uno al otro. Ademas, al arrancar como __main__ el
# servidor oculta su __file__ (ver el final del modulo) para que spawn nunca lo reimporte.
_CHILD = __name__ == "__mp_main__"

import atexit
import hashlib
import io
import json
import math
import os
import re
import shutil
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

ENGINE_VERSION = "0.5.0"

# ---- LLM embebido: si el binario de ollama viene en el bundle, corre en un
# puerto propio con modelos en DATA. El pipeline (highlights/translate) y el director
# apuntan ahí por REELFY_OLLAMA_URL. En dev (sin bundle) usa el ollama del sistema. ----
OLLAMA_PORT = int(os.environ.get("REELFY_OLLAMA_PORT", "11499"))
# "ollama-runtime" en la ruta marca el binario embebido (independiente de .exe en Windows)
BUNDLED_OLLAMA = "ollama-runtime" in str(paths.OLLAMA_BIN).replace("\\", "/")
TARGET_LLM = paths.LLM_MODEL          # qwen3.5:4b (texto + vision, Apache-2.0)
if BUNDLED_OLLAMA and not _CHILD:
    os.environ["REELFY_OLLAMA_URL"] = f"http://127.0.0.1:{OLLAMA_PORT}"
    os.environ["REELFY_LLM_MODEL"] = TARGET_LLM
os.environ.setdefault("REELFY_LLM_MODEL", TARGET_LLM)

import pipeline  # noqa: E402  (lee REELFY_OLLAMA_URL ya definido)

from fastapi import Body, FastAPI, UploadFile, Form, HTTPException  # noqa: E402
from fastapi.responses import (HTMLResponse, FileResponse, JSONResponse,  # noqa: E402
                               PlainTextResponse, Response)
from starlette.concurrency import run_in_threadpool  # noqa: E402

INPUT, OUTPUT = paths.INPUT, paths.OUTPUT
STATIC = Path(__file__).resolve().parent / "static"
MUSIC_OUT = OUTPUT / "music"

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


_logf = open(LOG_FILE, "a", buffering=1) if not _CHILD else sys.__stderr__
if not _CHILD:
    sys.stdout = _Tee(sys.__stdout__, _logf)
    sys.stderr = _Tee(sys.__stderr__, _logf)
    print(f"\n===== Reelfy {ENGINE_VERSION} arranque {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"(LLM embebido={BUNDLED_OLLAMA}, modelo={os.environ.get('REELFY_LLM_MODEL')}) =====")

app = FastAPI(title="Reelfy")

# Cola de trabajos persistente (SQLite en DATA) + workers. Ver app/jobstore.py.
import jobstore  # noqa: E402
from jobstore import Progress, JobCanceled  # noqa: E402

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


def _ollama_url():
    return os.environ.get("REELFY_OLLAMA_URL", "http://localhost:11434")


def _ollama_up():
    try:
        urllib.request.urlopen(_ollama_url() + "/api/version", timeout=0.6)
        return True
    except Exception:  # noqa
        return False


def _ollama_models():
    try:
        r = json.loads(urllib.request.urlopen(_ollama_url() + "/api/tags", timeout=1).read())
        return [m.get("name", "") for m in r.get("models", [])]
    except Exception:  # noqa
        return None


def _llm_ready(model=None, names=None):
    """El modelo LLM está descargado en el ollama embebido/sistema (nombre EXACTO)."""
    model = model or os.environ.get("REELFY_LLM_MODEL") or paths.LLM_MODEL
    names = _ollama_models() if names is None else names
    names = names or []
    return model in names or (":" not in model and f"{model}:latest" in names)


def _old_llm(names=None):
    """LLM de una version anterior ya instalado en el ollama EMBEBIDO (qwen2.5:3b en v0.4) o
    None. Actualizar de v0.4 no debe bloquear la app: se usa ese modelo mientras el nuevo se
    baja en segundo plano."""
    if not BUNDLED_OLLAMA:
        return None
    names = _ollama_models() if names is None else names
    for old in paths.OLD_LLM_MODELS:
        if old != TARGET_LLM and _llm_ready(old, names or []):
            return old
    return None


def _use_llm(model):
    """Cambia el LLM activo del pipeline y del director (en caliente). Con un modelo viejo
    (solo texto) la vision del director apunta al modelo nuevo: mientras no este, el director
    describe las tomas con heuristicas (nunca manda imagenes a un modelo sin vision)."""
    prev = os.environ.get("REELFY_LLM_MODEL")
    os.environ["REELFY_LLM_MODEL"] = model
    if model == TARGET_LLM:
        if os.environ.get("REELFY_VLM_MODEL") == TARGET_LLM:
            os.environ.pop("REELFY_VLM_MODEL", None)
    else:
        os.environ["REELFY_VLM_MODEL"] = TARGET_LLM
    try:
        pipeline.hl.MODEL = model              # highlights/translate leen el modulo al importar
    except Exception:  # noqa
        pass
    if prev != model:
        print(f"LLM activo: {model} (antes {prev})")


def _refresh_llm():
    """App: usa qwen3.5:4b si ya esta; si no, el LLM viejo instalado (actualizacion)."""
    if not BUNDLED_OLLAMA:
        return
    names = _ollama_models()
    if names is None:
        return                                  # ollama aun no responde: se decide despues
    if _llm_ready(TARGET_LLM, names):
        _use_llm(TARGET_LLM)
    else:
        old = _old_llm(names)
        if old:
            _use_llm(old)


if BUNDLED_OLLAMA and not _CHILD:
    _start_ollama()

# ================================================================ primer arranque
# Obligatorio: modelo de voz (whisper) + IA de lenguaje (qwen3.5:4b, en la app).
# Opcional (en segundo plano, sin bloquear): afinador de subtitulos, modelos de sujeto
# (MODNet + PP-HumanSeg, 32 MB) y musica IA (ACE-Step, ~3.6 GB; sin ella: procedural).
SETUP = {"state": "ready", "pct": 0, "msg": ""}
OPT = {"aligner": {"state": "unknown"}, "matte": {"state": "unknown"},
       "music": {"state": "unknown"}, "llm": {"state": "unknown"}}
_opt_lock = threading.Lock()
_opt_thread = None
_music_stop = threading.Event()
PREFS_FILE = paths.DATA / "setup.json"
AUTO_DOWNLOAD = os.environ.get("REELFY_AUTO_DOWNLOAD", "1") != "0"
GB = 1 << 30


def _prefs():
    try:
        return json.loads(PREFS_FILE.read_text())
    except Exception:  # noqa
        return {}


def _set_pref(**kw):
    d = _prefs()
    d.update(kw)
    tmp = PREFS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(PREFS_FILE)


def _music_wanted():
    return bool(_prefs().get("music_ai", True))


def _needs_setup():
    """Solo lo OBLIGATORIO bloquea (pantalla de preparacion). Lo opcional se baja aparte.
    El LLM nuevo NO bloquea si hay uno de una version anterior (actualizacion de v0.4): la
    app sigue funcionando con ese y el nuevo se baja en segundo plano (OPT['llm'])."""
    if not paths.engine_ready():
        return True
    if BUNDLED_OLLAMA:
        names = _ollama_models()
        if not _llm_ready(TARGET_LLM, names) and not _old_llm(names):
            return True
    return False


def _llm_upgrade_pending(names=None):
    """App con el LLM viejo y sin el nuevo: el nuevo se baja como opcional."""
    if not BUNDLED_OLLAMA:
        return False
    names = _ollama_models() if names is None else names
    if names is None:
        return False
    return not _llm_ready(TARGET_LLM, names) and bool(_old_llm(names))


def _matte_ready():
    try:
        from motion import matte
        return matte.model_ready(which="modnet")
    except Exception:  # noqa
        return False


def _music_status():
    try:
        from motion import music
        return music.music_model_status()
    except Exception as e:  # noqa
        return {"ready": False, "error": str(e), "missing_bytes": 0, "files": []}


def _optional_missing():
    """Modelos opcionales que faltan: [nombre]. La musica solo cuenta si el usuario la quiere."""
    out = []
    if not paths.aligner_ready():
        out.append("aligner")
    if not _matte_ready():
        out.append("matte")
    if _llm_upgrade_pending():
        out.append("llm")
    if _music_wanted() and not _music_status().get("ready"):
        out.append("music")
    return out


def _refresh_optional():
    with _opt_lock:
        if OPT["aligner"].get("state") != "downloading":
            OPT["aligner"] = {"state": "ready" if paths.aligner_ready() else "missing",
                              "bytes": paths.ALIGNER_SIZE}
        if OPT["matte"].get("state") != "downloading":
            OPT["matte"] = {"state": "ready" if _matte_ready() else "missing", "bytes": 32_052_578}
        if OPT["music"].get("state") not in ("downloading", "verifying"):
            ms = _music_status()
            prev = OPT["music"].get("state")
            if ms.get("ready"):
                st = "ready"
            elif not _music_wanted():
                st = "disabled"
            elif prev in ("error", "skipped"):
                st = prev
            else:
                st = "missing"
            OPT["music"] = {"state": st, "msg": OPT["music"].get("msg", "") if st in ("error", "skipped") else "",
                            "bytes": _music_bytes(), "missing_bytes": ms.get("missing_bytes", 0),
                            "binary": bool(ms.get("bin")), "optional": True,
                            "fallback": "procedural"}
        if OPT["llm"].get("state") != "downloading":
            names = _ollama_models() if BUNDLED_OLLAMA else None
            prev = OPT["llm"]
            if not BUNDLED_OLLAMA:
                st = "external"               # dev: ollama del sistema (no se administra)
            elif names is None:
                st = prev.get("state", "unknown")
            elif _llm_ready(TARGET_LLM, names):
                st = "ready"
            elif _old_llm(names):
                st = prev.get("state") if prev.get("state") in ("error", "skipped") else "missing"
            else:
                st = "required"               # instalacion nueva: lo baja el setup obligatorio
            OPT["llm"] = {"state": st, "model": TARGET_LLM, "bytes": paths.LLM_SIZE,
                          "active": os.environ.get("REELFY_LLM_MODEL"),
                          "fallback": _old_llm(names) if names else None,
                          "msg": prev.get("msg", "") if st in ("error", "skipped") else ""}
    return OPT


def _music_bytes():
    try:
        from motion import music
        return int(music.MUSIC_MODELS_BYTES)
    except Exception:  # noqa
        return 0


SETUP["state"] = "ready" if _CHILD else ("missing" if _needs_setup() else "ready")


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
            req = urllib.request.Request(url, headers={"User-Agent": f"Reelfy/{ENGINE_VERSION}"})
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


def _dl_resumable(url, dst, size, sha256=None, on_bytes=None, stop=None, retries=6):
    """Descarga REANUDABLE (Range) a <dst>.part con tamano exacto y SHA-256 opcional.
    `on_bytes(done, size)`; `stop` (Event) pausa la descarga (el .part se conserva)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    last = ""
    for attempt in range(retries):
        if stop is not None and stop.is_set():
            raise RuntimeError("pausada")
        try:
            done = part.stat().st_size if part.exists() else 0
            if done > size:
                part.unlink(); done = 0
            if done < size:
                hdr = {"User-Agent": f"Reelfy/{ENGINE_VERSION}"}
                if done:
                    hdr["Range"] = f"bytes={done}-"
                req = urllib.request.Request(url, headers=hdr)
                with urllib.request.urlopen(req, timeout=60) as r:
                    mode = "ab" if (done and getattr(r, "status", 200) == 206) else "wb"
                    if mode == "wb":
                        done = 0
                    with open(part, mode) as f:
                        while chunk := r.read(1 << 20):
                            f.write(chunk); done += len(chunk)
                            if on_bytes:
                                on_bytes(done, size)
                            if stop is not None and stop.is_set():
                                raise RuntimeError("pausada")
            got = part.stat().st_size if part.exists() else 0
            if got != size:
                last = f"incompleta ({got}/{size} bytes)"
                continue
            if sha256:
                h = hashlib.sha256()
                with open(part, "rb") as f:
                    for chunk in iter(lambda: f.read(8 << 20), b""):
                        h.update(chunk)
                if h.hexdigest() != sha256:
                    part.unlink(missing_ok=True)
                    last = "archivo corrupto (SHA-256 no coincide)"
                    continue
            part.replace(dst)
            return dst
        except Exception as e:  # noqa: red caida, timeout, HTTP
            if "pausada" in str(e):
                raise
            last = str(e)
        time.sleep(min(2 ** attempt, 15))
    raise RuntimeError(f"no se pudo descargar {dst.name}: {last}")


def _pull_model(model, on_prog, on_msg, stop=None, attempts=4):
    """ollama pull con progreso (stream JSON; ollama reanuda las capas a medias), reintenta y
    VERIFICA que quedo listo. on_prog(frac, comp, tot); on_msg(texto)."""
    err = None
    for attempt in range(attempts):
        if stop is not None and stop.is_set():
            raise RuntimeError("pausada")
        try:
            url = _ollama_url() + "/api/pull"
            body = json.dumps({"model": model}).encode()
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
                        on_prog(comp / tot, comp, tot)
            if not err and _llm_ready(model):              # descargado y verificado
                return
        except Exception as e:  # noqa
            err = str(e)
        on_msg(f"IA de lenguaje… reintentando ({attempt + 2}/{attempts})")
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"No se pudo preparar la IA de lenguaje ({err}). Revisa tu conexión y reintenta.")


def _pull_llm(lo, hi):
    """Setup obligatorio (instalacion nueva): baja el LLM con progreso en SETUP."""
    def prog(f, comp, tot):
        SETUP.update(pct=round(lo + (hi - lo) * f, 1),
                     msg=f"Descargando IA de lenguaje… {comp // (1 << 20)} / {tot // (1 << 20)} MB")
    _pull_model(TARGET_LLM, prog, lambda m: SETUP.update(msg=m))
    _use_llm(TARGET_LLM)
    _drop_old_llms(TARGET_LLM)


def _download_llm():
    """Actualizacion de v0.4: el LLM nuevo se baja en SEGUNDO PLANO (la app sigue con el viejo)
    con progreso propio en OPT['llm']; al quedar listo se activa y se borra el viejo."""
    if not _llm_upgrade_pending():
        return
    ok, _, msg = space_check(paths.LLM_SIZE, 0, _disk_free_gb())
    if not ok:
        OPT["llm"].update(state="skipped", msg=msg)
        print(f"LLM nuevo omitido por espacio: {msg}")
        return
    OPT["llm"].update(state="downloading", pct=0, msg="Descargando la IA de lenguaje nueva…")

    def prog(f, comp, tot):
        OPT["llm"].update(pct=round(100 * f, 1),
                          msg=f"Descargando la IA de lenguaje nueva… {comp >> 20} / {tot >> 20} MB")
    try:
        _pull_model(TARGET_LLM, prog, lambda m: OPT["llm"].update(msg=m))
        _use_llm(TARGET_LLM)
        OPT["llm"].update(state="ready", pct=100, msg="", active=TARGET_LLM)
        _maybe_drop_old_llms()
    except Exception as e:  # noqa
        OPT["llm"].update(state="error", msg=f"No se pudo bajar la IA de lenguaje nueva ({e}); "
                                              f"se sigue usando {os.environ.get('REELFY_LLM_MODEL')}.")


def _maybe_drop_old_llms():
    """Borra el LLM viejo del ollama embebido SOLO con el nuevo activo y sin trabajos en
    proceso (un analisis podria estar usandolo). Si no se puede ahora, se reintenta en /setup."""
    if not BUNDLED_OLLAMA or os.environ.get("REELFY_LLM_MODEL") != TARGET_LLM:
        return False
    if not _old_llm():
        return False
    try:
        busy = any(j.get("state") in ("queued", "running") for j in jobstore.recent(50))
    except Exception:  # noqa
        busy = True
    if busy:
        return False
    _drop_old_llms(TARGET_LLM)
    return True


def _drop_old_llms(keep):
    """Libera espacio: borra del ollama EMBEBIDO (modelos en DATA, solo de Reelfy) los LLM
    de versiones anteriores (qwen2.5:3b en v0.4). Nunca toca el ollama del sistema."""
    if not BUNDLED_OLLAMA:
        return
    for old in paths.OLD_LLM_MODELS:
        if old == keep or old not in (_ollama_models() or []):
            continue
        try:
            req = urllib.request.Request(_ollama_url() + "/api/delete", method="DELETE",
                                         data=json.dumps({"model": old}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
            print(f"LLM anterior eliminado: {old}")
        except Exception as e:  # noqa
            print(f"no se pudo borrar el LLM anterior {old}: {e}")


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
    return shutil.disk_usage(paths.DATA).free / GB


def _required_missing_bytes():
    """Bytes que faltan de lo OBLIGATORIO (+ afinador y sujeto, chicos, del mismo paso)."""
    b = 0
    if not paths.engine_ready():
        b += paths.MODEL_SIZE
    if BUNDLED_OLLAMA and not _llm_ready(TARGET_LLM) and not _old_llm():
        b += paths.LLM_SIZE
    if not paths.aligner_ready():
        b += paths.ALIGNER_SIZE
    if not _matte_ready():
        b += 32_052_578
    return b


def space_check(required_bytes, music_bytes, free_gb):
    """Decide si hay espacio: -> (ok_obligatorio, ok_musica, mensaje). Deja margen de trabajo
    (renders, caches): 2 GB tras lo obligatorio y 3 GB tras la musica."""
    need = required_bytes / GB + (2.0 if required_bytes else 0.0)
    ok_req = free_gb >= need
    left = free_gb - required_bytes / GB
    ok_music = music_bytes <= 0 or left >= music_bytes / GB + 3.0
    msg = ""
    if not ok_req:
        msg = (f"Espacio en disco insuficiente ({free_gb:.1f} GB libres). Reelfy necesita "
               f"~{need:.1f} GB para los modelos de IA. Libera espacio y reintenta.")
    elif not ok_music:
        msg = (f"Sin espacio para la música IA ({music_bytes / GB:.1f} GB): se usará la "
               f"música procedural (libera espacio para activarla).")
    return ok_req, ok_music, msg


def _run_setup():
    try:
        need_dl = not paths.engine_ready() or (BUNDLED_OLLAMA and not _llm_ready(TARGET_LLM)
                                                and not _old_llm())
        if need_dl:
            ok, _, msg = space_check(_required_missing_bytes(), 0, _disk_free_gb())
            if not ok:
                raise RuntimeError(msg)
        # 1) modelo de voz (whisper) 0..45% — verifica tamaño; si estaba corrupto, lo re-baja
        if not paths.engine_ready():
            paths._MODEL_DATA.unlink(missing_ok=True)       # limpia un posible modelo truncado
            _dl_file(paths.MODEL_URL, paths._MODEL_DATA, "Descargando modelo de voz",
                     0, 45, paths.MODEL_SIZE)
            if not paths.engine_ready():
                raise RuntimeError("El modelo de voz quedó incompleto. Reintenta.")
        # 2) IA de lenguaje y vision (LLM en el ollama embebido) 45..88%
        if BUNDLED_OLLAMA:
            SETUP.update(msg="Iniciando la IA de lenguaje…")
            _ensure_ollama()
            if _llm_ready(TARGET_LLM):
                _use_llm(TARGET_LLM)
            elif _old_llm():
                _use_llm(_old_llm())     # actualizacion: el nuevo se baja en segundo plano
            else:
                _pull_llm(45, 88)
        # 3) afinador de subtítulos 88..96% (el "wedge": sincronía fina).
        #    Modelo MMS_FA en ONNX int8 (~357 MB) que corre con onnxruntime.
        #    Opcional: si falla, analyze sigue con el timing de whisper.
        if not paths.aligner_ready():
            try:
                paths._ALIGNER_DATA.unlink(missing_ok=True)   # limpia uno truncado
                _dl_file(paths.ALIGNER_URL, paths._ALIGNER_DATA,
                         "Descargando el afinador de subtítulos", 88, 96,
                         paths.ALIGNER_SIZE)
            except Exception:  # noqa
                traceback.print_exc()   # no bloquea el setup
        # 4) sujeto (MODNet + PP-HumanSeg, 32 MB): efectos de fondo/contorno. 96..98%
        if not _matte_ready():
            SETUP.update(pct=96, msg="Descargando los modelos de sujeto…")
            try:
                from motion import matte
                matte.ensure_models(lambda p: SETUP.update(pct=round(96 + 2 * p / 100, 1)))
            except Exception:  # noqa
                traceback.print_exc()   # sin sujeto el video sale igual (sin efectos de fondo)
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
        _start_optional()     # musica IA (opcional) en segundo plano
    except Exception as e:  # noqa
        traceback.print_exc()
        SETUP.update(state="error", pct=SETUP.get("pct", 0),
                     msg=str(e) or "La preparación falló. Reintenta.")


def _run_optional():
    """Baja en SEGUNDO PLANO lo opcional que falte (nunca bloquea el uso de la app):
    afinador, modelos de sujeto y musica IA (con progreso propio en OPT['music'])."""
    try:
        if not paths.aligner_ready():
            with _opt_lock:
                OPT["aligner"] = {"state": "downloading", "pct": 0, "bytes": paths.ALIGNER_SIZE}
            try:
                _dl_resumable(paths.ALIGNER_URL, paths._ALIGNER_DATA, paths.ALIGNER_SIZE,
                              on_bytes=lambda d, s: OPT["aligner"].update(pct=round(100 * d / s, 1)))
                OPT["aligner"] = {"state": "ready", "pct": 100}
            except Exception as e:  # noqa
                OPT["aligner"] = {"state": "error", "msg": str(e)}
        if not _matte_ready():
            with _opt_lock:
                OPT["matte"] = {"state": "downloading", "pct": 0}
            try:
                from motion import matte
                matte.ensure_models(lambda p: OPT["matte"].update(pct=round(p, 1)))
                OPT["matte"] = {"state": "ready" if _matte_ready() else "error", "pct": 100}
            except Exception as e:  # noqa
                OPT["matte"] = {"state": "error", "msg": str(e)}
        if _llm_upgrade_pending():
            _download_llm()
        if _music_wanted():
            _download_music()
    except Exception:  # noqa
        traceback.print_exc()
    finally:
        _refresh_optional()


def _download_music():
    """Modelos de ACE-Step (MIT) con progreso propio. Sin espacio o sin red: procedural."""
    from motion import music
    ms = music.music_model_status()
    if ms.get("ready"):
        OPT["music"].update(state="ready", pct=100)
        return
    missing = int(ms.get("missing_bytes") or 0)
    _, ok_music, msg = space_check(0, missing, _disk_free_gb())
    if not ok_music:
        OPT["music"].update(state="skipped", msg=msg)
        print(f"musica IA omitida: {msg}")
        return
    _music_stop.clear()
    dst_dir = Path(ms["dir"])
    total = sum(m["size"] for m in music.MUSIC_MODEL_FILES)
    base = 0
    OPT["music"].update(state="downloading", pct=0, msg="Descargando la música IA…")
    try:
        for m in music.MUSIC_MODEL_FILES:
            p = dst_dir / m["file"]
            if p.exists() and p.stat().st_size == m["size"]:
                base += m["size"]
                continue

            def prog(d, s, base=base):
                OPT["music"].update(pct=round(100 * (base + d) / total, 1),
                                    msg=f"Descargando la música IA… {(base + d) >> 20} / {total >> 20} MB")
            _dl_resumable(m["url"], p, m["size"], m.get("sha256"), prog, _music_stop)
            base += m["size"]
        OPT["music"].update(state="verifying", pct=100, msg="Verificando la música IA…")
        ok = music.music_ready()
        OPT["music"].update(state="ready" if ok else "error",
                            msg="" if ok else "Modelos de música incompletos o sin el motor ace-synth.")
    except Exception as e:  # noqa
        paused = "pausada" in str(e)
        OPT["music"].update(state="disabled" if paused else "error",
                            msg="Descarga pausada." if paused else
                            f"No se pudo bajar la música IA ({e}); se usará la procedural.")


_OPT_RETRY_S = 600          # tras un intento fallido (sin red/espacio) no reintentar en cada sondeo
_opt_last = [0.0]


def _start_optional(force=False):
    global _opt_thread
    if not (AUTO_DOWNLOAD or force):
        return False
    if _opt_thread is not None and _opt_thread.is_alive():
        return True
    if not force and time.time() - _opt_last[0] < _OPT_RETRY_S:
        return False
    if not _optional_missing():
        return False
    _opt_last[0] = time.time()
    _opt_thread = threading.Thread(target=_run_optional, daemon=True, name="reelfy-optional")
    _opt_thread.start()
    return True


@app.get("/setup")
def setup_status():
    _refresh_llm()                   # LLM activo: el nuevo si ya esta; si no, el de v0.4
    _maybe_drop_old_llms()
    # re-detecta: si algo se corrompió/borró tras estar listo, vuelve a "missing"
    if SETUP["state"] not in ("downloading", "error"):
        SETUP["state"] = "ready" if not _needs_setup() else "missing"
        if SETUP["state"] == "ready":
            SETUP["pct"] = 100
            _start_optional()        # opcionales que falten (o se borraron): en segundo plano
    _refresh_optional()
    free = _disk_free_gb()
    ms = OPT["music"]
    return {**SETUP, "llm": _llm_ready(), "llm_model": os.environ.get("REELFY_LLM_MODEL"),
            "llm_target": TARGET_LLM,
            **_lic_state(), "optional": OPT, "music_ai": _music_wanted(),
            "disk_free_gb": round(free, 1),
            "engine": {"version": ENGINE_VERSION, "pro": True,
                       "music_ai_ready": ms.get("state") == "ready",
                       "matte_ready": OPT["matte"].get("state") == "ready"}}


@app.post("/setup/start")
def setup_start(body: dict | None = Body(default=None)):
    """body opcional {music_ai: bool}: la musica IA (~3.6 GB) es opcional. `def`: consulta a
    ollama y al disco (bloqueante) en el threadpool, no en el event loop."""
    body = body if isinstance(body, dict) else {}
    if "music_ai" in body:
        _set_pref(music_ai=bool(body["music_ai"]))
    if not _needs_setup() or SETUP["state"] == "downloading":
        _start_optional(force=bool(isinstance(body, dict) and body.get("music_ai")))
        return {"ok": True}
    SETUP.update(state="downloading", pct=0, msg="Preparando…")
    threading.Thread(target=_run_setup, daemon=True).start()
    return {"ok": True}


@app.post("/setup/music")
def setup_music(body: dict = Body(default={})):
    """Activa (baja en segundo plano) o desactiva la musica IA. Desactivar pausa la
    descarga (se reanuda despues) y el motor usa la musica procedural."""
    on = bool((body or {}).get("enabled", True))
    _set_pref(music_ai=on)
    if on:
        # apagar y prender enseguida: el hilo pausado puede seguir vivo terminando; se espera
        # a que salga y se relanza (sin esperar el respaldo de 10 min entre intentos)
        _music_stop.clear()
        th = _opt_thread
        if th is not None and th.is_alive() and OPT["music"].get("state") != "downloading":
            th.join(timeout=5.0)
        _opt_last[0] = 0.0
        started = _start_optional(force=True)
    else:
        _music_stop.set()
        started = False
    _refresh_optional()
    return {"ok": True, "music_ai": on, "downloading": started, "music": OPT["music"]}


# ---- licencias (solo la app empaquetada; el modo dev del repo no se gatea) ----
import json as _json  # noqa: E402
import os as _os  # noqa: E402
import urllib.request as _url  # noqa: E402

# La licencia solo se exige en la app empaquetada (REELFY_DATA). En desarrollo/pruebas con
# DATA aislado se puede apagar con REELFY_GATED=0.
GATED = bool(_os.environ.get("REELFY_DATA")) and _os.environ.get("REELFY_GATED", "1") != "0"
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


if GATED and not _CHILD:
    threading.Thread(target=_lic_revalidate, daemon=True).start()


def _require_license():
    if not _lic_state()["licensed"]:
        raise HTTPException(403, "Activa tu licencia de Reelfy para procesar videos.")


def _require_engine():
    if not paths.engine_ready():
        raise HTTPException(409, "El motor de IA aún se está preparando (primer arranque).")


@app.post("/license")
def license_activate(body: dict = Body(...)):
    """`def`: valida contra la API por red (hasta 10 s) en el threadpool."""
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


# ================================================================ motor pro: utilidades
DIRECTOR_DEFAULTS = {"style": "dinamico", "intensity": 0.75, "seed": 7}
MUSIC_SOURCES = ("ai", "procedural", "library", "file", "none")


def _director_params(raw):
    """director {style, intensity, seed, music, enabled, fmt} saneado (defaults del contrato).
    `music`: fuente ("ai"|"procedural"|"library"|"none") o dict parcial de fx.music."""
    d = dict(DIRECTOR_DEFAULTS)
    d["enabled"] = True
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except Exception:  # noqa
            raw = {}
    if raw is False:
        raw = {"enabled": False}
    if not isinstance(raw, dict):
        raw = {}
    if raw.get("style") in ("dinamico", "elegante", "minimal", "energetico"):
        d["style"] = raw["style"]
    try:
        d["intensity"] = max(0.0, min(1.0, float(raw.get("intensity", d["intensity"]))))
    except (TypeError, ValueError):
        pass
    try:
        d["seed"] = int(raw.get("seed", d["seed"]))
    except (TypeError, ValueError):
        pass
    d["enabled"] = bool(raw.get("enabled", True))
    m = raw.get("music")
    if isinstance(m, str) and m in MUSIC_SOURCES:
        d["music"] = {"source": m}
    elif isinstance(m, dict):
        d["music"] = {k: v for k, v in m.items() if k in (
            "source", "prompt", "mood", "bpm", "bpm_lock", "keyscale", "seed", "enabled",
            "gain_db", "ending", "track") and v is not None}
    if raw.get("fmt"):
        d["fmt"] = str(raw["fmt"])
    return d


def _apply_music(plan, music_over):
    if not music_over:
        return plan
    fx = plan.setdefault("fx", {})
    m = dict(fx.get("music") or {})
    m.update(music_over)
    if m.get("source") == "none":
        m["enabled"] = False
    elif "source" in music_over:
        m.setdefault("enabled", True)
    fx["music"] = m
    return plan


# claves de fx.music que cambian la PISTA (y por tanto su rejilla de beats). gain/duck/fades
# solo afectan la mezcla: con ellas el director puede seguir sincronizando a la pista IA.
MUSIC_TRACK_KEYS = ("prompt", "mood", "bpm", "bpm_lock", "keyscale", "seed", "track", "file",
                    "ending")


def _music_sync_ok(music_over):
    """True si la musica que eligio el usuario es la MISMA pista IA que el director genera y
    sincroniza (sin override, o solo fuente 'ai' y claves de mezcla). Con otra fuente
    (procedural/biblioteca/ninguna) o prompt/bpm/semilla propios, sincronizar a la pista IA
    dejaria los golpes 'al beat' fuera de tiempo en el export."""
    m = music_over or {}
    if not m:
        return True
    if m.get("enabled") is False or m.get("source", "ai") != "ai":
        return False
    return not any(k in m for k in MUSIC_TRACK_KEYS)


def _accepts(fn, name):
    import inspect
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _music_keep(plan):
    """Musica que el usuario ya eligio en el plan (fuente distinta de la IA, apagada o
    bloqueada): re-dirigir no la cambia por la pista IA del director."""
    fx = (plan or {}).get("fx") if isinstance(plan, dict) else None
    m = (fx or {}).get("music") if isinstance(fx, dict) else None
    if not isinstance(m, dict) or not m:
        return None
    locked = m.get("locked") or "music" in ((plan.get("director") or {}).get("locked") or [])
    if locked:
        return {k: v for k, v in m.items() if k not in ("stops", "swells", "drop_at")}
    if m.get("source", "ai") != "ai" or m.get("enabled") is False:
        return {k: m[k] for k in ("source", "enabled", "track", "file", "gain_db") if k in m}
    return None


def _direct_plan(plan, d, on_step=None, use_llm=True):
    """director.direct con red de seguridad: IA -> heuristicas -> plan sin edicion.
    NUNCA lanza (el analisis no se cae por el director). -> (plan, warnings).

    La musica elegida por el usuario (d['music']) entra ANTES de sincronizar: si el director
    acepta `music=`, la mezcla en su cfg antes de la rejilla de beats; si no, con una musica
    distinta de la pista IA se llama con music_sync=False (sin generar ACE en la ruta critica
    ni alinear golpes a una pista que no va a sonar)."""
    from motion import director
    warns = []
    out = None
    t0 = time.time()
    mover = d.get("music") or None
    kw = {}
    if mover and _accepts(director.direct, "music"):
        kw["music"] = dict(mover)
    elif not _music_sync_ok(mover):
        kw["music_sync"] = False
    try:
        out = director.direct(plan, d["style"], d["intensity"], d["seed"], use_llm=use_llm,
                              on_step=on_step, **kw)
    except Exception as e:  # noqa
        traceback.print_exc()
        warns.append(f"director: fallo con IA ({e}); se usaron heurísticas")
        try:
            out = director.direct(plan, d["style"], d["intensity"], d["seed"], use_llm=False,
                                  on_step=on_step, **kw)
        except Exception as e2:  # noqa
            traceback.print_exc()
            warns.append(f"director: sin edición automática ({e2})")
            try:
                from motion import schema
                out = schema.migrate_v1(plan)
            except Exception:  # noqa
                return plan, warns
    dd = out.get("director") or {}
    if use_llm and dd.get("llm") == "heuristico":
        warns.append("director: IA de lenguaje no disponible; edición con heurísticas")
    # VLM que cayo a heuristicas. Al re-dirigir un plan cuyas tomas ya describio el VLM, el
    # director reusa la cache y las reporta como "heuristico" aunque no lo sean: sin aviso.
    prev_vlm = ((plan or {}).get("director") or {}).get("vlm")
    if use_llm and dd.get("vlm") == "heuristico" and (out.get("shots") or []) and \
            not (prev_vlm and prev_vlm != "heuristico"):
        warns.append("director: la IA de visión no describió las tomas (tiempo agotado o modelo "
                     "no disponible); tomas con heurísticas")
    out = _apply_music(out, mover)
    print(f"   director: {d['style']} {d['intensity']} seed {d['seed']} en {time.time() - t0:.1f}s "
          f"(llm={dd.get('llm')}, vlm={dd.get('vlm')}, music_sync={kw.get('music_sync', 'auto')}, "
          f"timings={dd.get('timings')})")
    return out, warns


def _is_v2(plan):
    return isinstance(plan, dict) and plan.get("version") == 2 and isinstance(plan.get("fx"), dict)


def _load():
    """Carga del sistema (1 min) para anotar junto a los tiempos (la maquina se comparte)."""
    try:
        return round(os.getloadavg()[0], 1)
    except (AttributeError, OSError):
        return None


def _sha(s):
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _analysis_keys(plan, fmt, eo=None):
    """Claves del ANALISIS de fondo -> (base_key, full_key). Solo cuenta lo que lo cambia:
    video + cortes + reencuadre/formato + audio (base y voz; con el aire y el audio de estudio
    del proyecto, `eo`), pista de rostro, rangos de matte y grade. Editar textos, graficos o SFX
    (el autoguardado del Studio) NO cambia la clave: la vista previa y el export esperan y
    reutilizan el analisis en curso en vez de cancelarlo. Con la misma base_key tambien se
    espera (base y rostro se reutilizan; lo que falte del matte lo calcula quien lo necesita)."""
    fmt = str(fmt or "9:16")
    bo = _base_part(eo or {})
    try:
        from motion import render as R
        fn = getattr(R, "analysis_key", None)          # API publica del motor (si existe)
        if callable(fn):
            k = fn(plan, {"fmt": fmt, **bo})
            if isinstance(k, dict) and k.get("base") and k.get("full"):
                return str(k["base"]), str(k["full"])
            if isinstance(k, (tuple, list)) and len(k) == 2:
                return str(k[0]), str(k[1])
        pr = R.prepare(plan, {"fmt": fmt, "workers": 0, **bo})
        bv, bw = R._base_files(pr)
        job, o = pr["job"], pr["opts"]
        ff = R._face_file(job, pr["mods"], Path(o["cache_dir"]), bv.stem.replace("base_", ""))
        base = _sha(f"{bv.name}|{bw.name}|{fmt}")
        extra = json.dumps({"face": ff.name if ff else None, "matte": job.get("matte_ranges"),
                            "box": job.get("matte_box"), "q": job.get("quality"),
                            "grade": job.get("grade_on") and job.get("grade_cfg"),
                            "shots": [(s_.get("start"), s_.get("end")) for s_ in job.get("shots") or []]},
                           sort_keys=True, default=str)
        return base, _sha(base + extra)
    except Exception as e:  # noqa: respaldo sin el motor (o plan invalido): subconjunto del plan
        print(f"   clave de analisis por respaldo ({e})")
        fx = (plan or {}).get("fx") or {}
        sub = {k: (plan or {}).get(k) for k in ("video", "cuts", "duration", "camera")}
        sub["fx_camera"], sub["fx_grade"] = fx.get("camera"), fx.get("grade")
        sub["engine"] = bo
        base = _sha(json.dumps(sub, sort_keys=True, default=str) + fmt)
        sub["shots"] = (plan or {}).get("shots")
        sub["fx_effects"] = fx.get("effects")
        return base, _sha(json.dumps(sub, sort_keys=True, default=str) + fmt)


def _fmt_of(body, default="9:16"):
    f = (body or {}).get("fmt") or (body or {}).get("format") or default
    return str(f)


def _job_or_404(job_id):
    job = jobstore.get_light(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    return job


def _plan_from(body, job_id):
    plan = (body or {}).get("plan")
    if not plan:
        plan, _ = jobstore.get_plan(job_id)
    if not plan:
        raise HTTPException(409, "El job no tiene plan; analiza el video primero.")
    return plan


# ---- opciones del proyecto para el motor pro (marca, aire, audio de estudio) ----
# No viven en el plan: el Studio las manda junto al plan en /frame, /preview y /render y el
# motor las aplica (render.render_pro/_draft/_frame: opts.logo, opts.air, opts.enhance_audio).
# /settings.pro_opts anuncia cuales aplica el render pro (la UI habilita esos controles).
# El AIRE es opt-in en la edicion IA: solo cambia la imagen de una fuente mas ANCHA que la
# salida (margen desenfocado a los lados) y un talking head horizontal con bandas se ve amateur.
# El servidor lo aplica solo si el cuerpo trae `air_user: true` (el usuario movio el control);
# el default del Studio (AIR=0.16) ya no lo activa. Mientras la UI no lo distinga, 'air' NO va
# en pro_opts (la vista en vivo del Studio dibuja a sangre completa = lo que exporta) sino en
# `pro_opts_optin`.
PRO_OPTS = ("logo", "enhance_audio")
PRO_OPTS_OPTIN = ("air",)
ENGINE_KEYS = ("logo", "air", "air_user", "enhance_audio")   # claves del cuerpo que se leen
LOGO_POS = ("tl", "tr", "bl", "br")
BASE_OPTS = ("air", "enhance_audio")      # las que cambian el video base / la voz (claves de cache)
_ENGINE = {}                              # job -> opciones vigentes (ultimo cuadro/vista/export)
_ENGINE_LOCK = threading.Lock()


def _num(v, lo, hi, default):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return max(lo, min(hi, x))


def _engine_opts(body):
    """Cuerpo del Studio -> opciones del motor: {enhance_audio, air?, logo?}. Sin las claves
    el resultado es el de siempre (audio de estudio, sin aire, sin logo). `air` solo cuenta con
    `air_user: true` (opt-in). `logo` {pos, size, opacity} usa el logo de la marca
    (POST /brand/logo)."""
    b = body if isinstance(body, dict) else {}
    eo = {"enhance_audio": b.get("enhance_audio") is not False}
    a = _num(b.get("air"), 0.0, 0.35, 0.0) if b.get("air") is not None else 0.0
    if a > 0.001 and b.get("air_user") is True:
        eo["air"] = round(a, 4)
    lg = b.get("logo")
    if isinstance(lg, dict) and lg.get("enabled", True) is not False:
        eo["logo"] = {"path": str(BRAND / "logo.png"),
                      "pos": lg.get("pos") if lg.get("pos") in LOGO_POS else "br",
                      "size": round(_num(lg.get("size"), 0.04, 0.4, 0.14), 4),
                      "opacity": round(_num(lg.get("opacity"), 0.05, 1.0, 0.9), 3)}
    return eo


def _eff_engine(plan, fmt, eo):
    """Quita el aire cuando no cambia la imagen (fuente con el aspecto de la salida o mas alta:
    solo una fuente mas ANCHA lleva margen). Asi la clave del video base no cambia y base,
    rostro y matte del analisis de fondo se reutilizan."""
    eo = dict(eo or {})
    if not eo.get("air"):
        eo.pop("air", None)
        return eo
    try:
        from motion import render as R
        from motion import video_io as V
        info = V.probe((plan or {}).get("video"))
        bW, bH = R.output_size(fmt or "9:16", info)
        mode, _ = V.reframe_mode(info, bW, bH, eo["air"])
        if mode != "track+air":
            eo.pop("air", None)
    except Exception:  # noqa: sin probe (plan invalido): se deja como vino
        pass
    return eo


def _base_part(eo):
    return {k: eo[k] for k in BASE_OPTS if k in (eo or {})}


def _saved_engine(job_id):
    """Opciones guardadas en `side.engine` (sobreviven un reinicio): solo las del video base. El
    aire guardado solo vale si fue elegido por el usuario (`air_user`; las versiones anteriores
    guardaban el default del Studio)."""
    try:
        saved = jobstore.side_get(job_id, "engine")
    except Exception:  # noqa
        return None
    if not saved:
        return None
    out = {"enhance_audio": saved.get("enhance_audio") is not False}
    if saved.get("air") and saved.get("air_user"):
        out["air"] = float(saved["air"])
    return out


def _get_engine(job_id):
    """Opciones vigentes del trabajo para el analisis de fondo (memoria; tras un reinicio, las
    guardadas; si nunca llego un pedido, las de siempre). Sin logo (no cambia el analisis)."""
    with _ENGINE_LOCK:
        cur = _ENGINE.get(job_id)
    if cur is not None:
        return _base_part(cur)
    return _saved_engine(job_id) or {"enhance_audio": True}


def _set_engine(job_id, eo, plan=None, fmt="9:16"):
    """Registra las opciones EFECTIVAS del ultimo pedido (cuadro, vista previa, export). -> True
    si cambio lo que afecta al video base (aire o audio de estudio): hay que relanzar el
    analisis. Con `plan` se aplica _eff_engine a lo nuevo y a lo anterior antes de comparar (un
    aire que no aplica a la fuente no cuenta como cambio)."""
    if plan is not None:
        eo = _eff_engine(plan, fmt, eo)
    with _ENGINE_LOCK:
        known = job_id in _ENGINE
        raw = _ENGINE.get(job_id)
        _ENGINE[job_id] = dict(eo or {})
        if len(_ENGINE) > 200:
            _ENGINE.pop(next(iter(_ENGINE)))
    if raw is None:
        raw = _saved_engine(job_id) or {"enhance_audio": True}
    prev = _eff_engine(plan, fmt, raw) if plan is not None and raw.get("air") else raw
    base_changed = _base_part(prev) != _base_part(eo)
    if base_changed or not known or _base_part(raw) != _base_part(eo) or \
            bool(raw.get("logo")) != bool((eo or {}).get("logo")):
        try:
            extra = {"air_user": True} if (eo or {}).get("air") else {}
            jobstore.side_update(job_id, "engine", replace=True, logo=bool((eo or {}).get("logo")),
                                 **_base_part(eo), **extra)
        except Exception:  # noqa
            pass
    return base_changed


def _motor_opts(eo, fmt, **kw):
    """opts comunes del motor: formato, opciones del proyecto y carpeta de los medios propios
    (`media_root`: el motor resuelve cada `{sid}` del plan a <media_root>/<sid>.<ext>; nunca
    usa una ruta que venga en el plan)."""
    o = {"fmt": fmt, "media_root": str(SOURCES)}
    for k in ("enhance_audio", "air", "logo"):
        if k in (eo or {}):
            o[k] = eo[k]
    o.update(kw)
    return o


# ---- tareas laterales por trabajo (vista previa real, analisis en 2o plano) ----
# Cada una corre en su hilo con su Event de cancelacion; el estado vive en jobstore.side
# (lo lee /status). Un candado por trabajo serializa analisis y vista previa del MISMO
# trabajo (la vista previa reutiliza lo que el analisis deja en cache); semaforos
# globales evitan que dos trabajos analicen/previsualicen a la vez (CPU/GPU compartidos).
_SIDE = {}                         # (job, nombre) -> {"thread", "cancel", "key"}
_SIDE_LOCK = threading.Lock()
_JOB_LOCKS = {}
_ANALYSIS_SEM = threading.Semaphore(1)
_PREVIEW_SEM = threading.Semaphore(1)
_EXPORTING = set()                 # trabajos exportando (el analisis de fondo no los pisa)


def _job_lock(job_id):
    with _SIDE_LOCK:
        return _JOB_LOCKS.setdefault(job_id, threading.Lock())


def _acquire(lock, ev, timeout=None):
    """Adquiere `lock` atendiendo la cancelacion (ev) -> True/False."""
    t0 = time.time()
    while not lock.acquire(timeout=0.5):
        if ev.is_set() or (timeout is not None and time.time() - t0 > timeout):
            return False
    return True


def _side_running(job_id, name):
    with _SIDE_LOCK:
        s = _SIDE.get((job_id, name))
        return s if s and s["thread"].is_alive() else None


def _cancel_side(job_id=None, name=None, wait=0.0):
    """Cancela tareas laterales (de un trabajo y/o un nombre). Espera hasta `wait` s."""
    with _SIDE_LOCK:
        items = [(k, v) for k, v in _SIDE.items()
                 if (job_id is None or k[0] == job_id) and (name is None or k[1] == name)]
    for _, v in items:
        v["cancel"].set()
    t_end = time.time() + wait
    for _, v in items:
        v["thread"].join(timeout=max(0.0, t_end - time.time()))
    return len(items)


def _start_side(job_id, name, fn, key=None, restart=True, **meta):
    """Lanza fn(job_id, cancel_event) como tarea lateral. Si ya corre una con la misma
    `key` no hace nada; con otra key la cancela y arranca la nueva. `meta` queda en el
    registro (p. ej. bkey del analisis)."""
    with _SIDE_LOCK:
        cur = _SIDE.get((job_id, name))
        if cur and cur["thread"].is_alive():
            if key is not None and cur.get("key") == key:
                return False
            if not restart:
                return False
            cur["cancel"].set()
        ev = threading.Event()
        th = threading.Thread(target=fn, args=(job_id, ev), daemon=True,
                              name=f"reelfy-{name}-{job_id}")
        _SIDE[(job_id, name)] = {"thread": th, "cancel": ev, "key": key, **meta}
    th.start()
    return True


class _Throttle:
    """Escribe progreso de una tarea lateral a lo mas ~4 veces por segundo."""

    def __init__(self, job_id, name, ev, tok=None):
        self.job_id, self.name, self.ev, self.tok = job_id, name, ev, tok
        self.last = 0.0
        self.t0 = time.time()

    def __call__(self, force=False, **kw):
        if self.ev.is_set():
            from motion import video_io as V
            raise V.Canceled("cancelado")
        now = time.monotonic()
        if not force and now - self.last < 0.25:
            return
        self.last = now
        kw.setdefault("elapsed", round(time.time() - self.t0, 1))
        jobstore.side_update(self.job_id, self.name, match=self.tok, **kw)

    def set(self, **kw):
        """Escritura inmediata (estados terminales), solo si esta ejecucion sigue vigente."""
        return jobstore.side_update(self.job_id, self.name, match=self.tok, **kw)


def _kick_analysis(job_id, plan, fmt="9:16", force=False, eo=None):
    """Tras review (y tras re-dirigir): render.ensure_analysis en SEGUNDO PLANO para que
    /frame y la vista previa real sean rapidos. Cancelable; no corre durante un export.
    Clave = _analysis_keys: el mismo analisis en curso no se relanza; uno con la MISMA base
    (p. ej. solo cambio el matte) no se cancela: se encadena al terminar el actual.
    `eo`: aire y audio de estudio del proyecto (default: los del ultimo pedido del Studio).
    La musica IA del plan NO la precalienta el motor (su prefetch no se puede cancelar: cada
    cambio de estilo dejaba una generacion ACE huerfana): la genera la tarea lateral `music`."""
    if not _is_v2(plan):
        return False
    _kick_music(job_id, plan, fmt)
    if os.environ.get("REELFY_BG_ANALYSIS", "1") == "0" and not force:
        return False
    fmt = str(fmt or "9:16")
    eo = _base_part(_eff_engine(plan, fmt, eo if eo is not None else _get_engine(job_id)))
    bkey, key = _analysis_keys(plan, fmt, eo)
    plan = json.loads(json.dumps(plan))
    with _SIDE_LOCK:
        cur = _SIDE.get((job_id, "analysis"))
        if cur and cur["thread"].is_alive() and not cur["cancel"].is_set() and not cur.get("closing"):
            if cur.get("key") == key:
                cur.pop("next", None)                # el pedido vigente ya es este
                return False
            if cur.get("bkey") == bkey:
                cur["next"] = (plan, fmt, bkey, key, eo)   # misma base: encadenar, no cancelar
                return True

    def run(jid, ev):
        from motion import render as R
        from motion import video_io as V
        req = (plan, fmt, bkey, key, eo)
        while req is not None:
            p_, f_, bk_, k_, e_ = req
            tok = uuid.uuid4().hex[:8]
            tick = _Throttle(jid, "analysis", ev, tok)
            jobstore.side_update(jid, "analysis", replace=True, state="queued", pct=0, stage="",
                                 key=k_, bkey=bk_, fmt=f_, tok=tok)
            got_sem = got_lock = False
            lock = _job_lock(jid)
            try:
                while _EXPORTING and not ev.is_set():      # nunca pisa un export
                    time.sleep(0.5)
                got_sem = _acquire(_ANALYSIS_SEM, ev)
                got_lock = got_sem and _acquire(lock, ev)
                if not got_lock or ev.is_set() or _EXPORTING:
                    raise V.Canceled("cancelado")
                tick.set(state="running", pct=0, stage="base")
                t0 = time.time()
                res = R.ensure_analysis(p_, _motor_opts(
                    e_, f_, prefetch_music=False,
                    on_pct=lambda st, p: tick(stage=st, pct=round(float(p), 1)),
                    cancel=lambda: ev.is_set() or bool(_EXPORTING)))
                tick.set(state="done", pct=100, stage="done", t=round(time.time() - t0, 1),
                         matte=bool(res.get("matte")), grade=bool(res.get("grade")),
                         skipped=res.get("skipped") or [])
            except (V.Canceled, JobCanceled):
                tick.set(state="canceled")
            except Exception as e:  # noqa: el analisis de fondo es una optimizacion, nunca un error
                traceback.print_exc()
                tick.set(state="error", error=str(e))
            finally:
                if got_lock:
                    lock.release()
                if got_sem:
                    _ANALYSIS_SEM.release()
            req = None
            with _SIDE_LOCK:
                me = _SIDE.get((jid, "analysis"))
                if me is not None and me["cancel"] is ev and not ev.is_set():
                    req = me.pop("next", None)
                    if req is not None:
                        me["key"], me["bkey"] = req[3], req[2]
                    else:
                        me["closing"] = True        # un pedido que llegue ahora arranca otro hilo

    return _start_side(job_id, "analysis", run, key=key, bkey=bkey)


# ---- musica IA de la propuesta vigente (tarea lateral `music`) ----
# Una sola generacion ACE por trabajo y siempre la de la edicion VIGENTE: otra pista cancela la
# anterior (mata ace-synth). Re-dirigir sin `full` con una pista nueva responde al instante sin
# sincronia (music_sync=False: el director no lanza su hilo huerfano) y esta tarea genera la pista;
# al terminar re-dirige la MISMA peticion (plan original, estilo, intensidad y semilla) con la
# rejilla de beats y la guarda si el plan guardado sigue siendo la propuesta (base_rev). Si el
# usuario ya edito, /status dice music_resync='available' (re-dirigir sincroniza al instante: la
# pista queda en cache). Estados: generating -> ready -> syncing -> synced | superseded;
# canceled | error.
REDIRECT_CACHED_WAIT_S = float(os.environ.get("REELFY_REDIRECT_CACHED_WAIT", "10"))
MUSIC_MIX_KEYS = ("swells", "gain_db", "duck_db", "fade_in", "intro_fade", "locked", "voice_carve_db")


def _music_gen_key(MU, cfg, dur, style, seed):
    """Identidad de la GENERACION ACE (el crudo): prompt, bpm, tonalidad y semilla, y el tramo de
    duracion que se genera. Cambiar stops, drop o la mezcla reutiliza el mismo crudo: no cancela."""
    try:
        c, _ = MU._coerce_cfg(cfg, dur, seed)
        st = style if style in MU.STYLE_MUSIC else "dinamico"
        mood = c["mood"] or MU.STYLE_DEFAULT_MOOD[st]
        prompt = MU.build_prompt(st, mood, c["prompt"], c["bpm"], c["seed"], c["keyscale"])
        return f"{MU._raw_key(prompt, c['seed'])}:{int(MU.gen_duration(dur))}"
    except Exception:  # noqa: sin las piezas internas del modulo: la cfg completa
        sub = {k: v for k, v in (cfg or {}).items() if k not in MUSIC_MIX_KEYS}
        return _sha(json.dumps([sub, round(float(dur), 2), style, seed], sort_keys=True, default=str))


def _music_track(plan, fmt="9:16", eo=None):
    """Pista IA que pedira el render de `plan` -> {cfg, dur, style, seed, key, pending} o None
    (sin musica IA, sin el motor de musica instalado o plan invalido). Misma cfg que
    render._audio (render.music_cfg en tiempo final). Barata (~ms)."""
    if not _is_v2(plan):
        return None
    try:
        from motion import music as MU
        from motion import render as R
        if not MU.music_ready():
            return None
        pr = R.prepare(plan, _motor_opts(eo or {}, fmt or "9:16", workers=0, quality="draft"))
        job = pr["job"]
        mcfg, on = R.music_cfg(pr["plan"], job, pr["timeline"])
        dur = float(job["final_duration"])
        if not on or (mcfg.get("source") or "ai") != "ai" or dur <= 0.25:
            return None
        style, seed = job["style"], job["seed"]
        return {"cfg": mcfg, "dur": dur, "style": style, "seed": seed,
                "key": _music_gen_key(MU, mcfg, dur, style, seed),
                "pending": bool(MU.pending(mcfg, dur, style, seed))}
    except Exception as e:  # noqa: la musica nunca tumba el pedido
        print(f"   musica: sin datos de la pista ({e})")
        return None


def _music_side(job_id):
    try:
        return jobstore.side_get(job_id, "music") or {}
    except Exception:  # noqa
        return {}


def _kick_music(job_id, plan, fmt="9:16", eo=None, target=None, info=None):
    """Genera en segundo plano la pista IA de `plan` (la edicion vigente). Si ya esta en cache o
    el plan no lleva musica IA, cancela una generacion obsoleta. `target` (propuesta de
    re-dirigir) se sincroniza al terminar. -> 'running' | 'started' | None."""
    info = info if info is not None else _music_track(plan, fmt, eo)
    run_ = _side_running(job_id, "music")
    if info is None or not info["pending"]:
        if run_ is not None and (info is None or run_.get("key") != info["key"]):
            run_["cancel"].set()                 # generaba la pista de otra edicion
        return None
    key = info["key"]
    with _SIDE_LOCK:
        cur = _SIDE.get((job_id, "music"))
        if cur and cur["thread"].is_alive() and not cur["cancel"].is_set() and cur.get("key") == key:
            if target is not None:               # misma pista: sincroniza la propuesta nueva
                cur["target"] = target
                jobstore.side_update(job_id, "music", match=cur.get("tok"), from_rev=target["rev"],
                                     resync=None)
            return "running"
    tok = uuid.uuid4().hex[:8]
    jobstore.side_update(job_id, "music", replace=True, state="generating", pct=0, tok=tok,
                         key=key, from_rev=(target or {}).get("rev"), resync=None,
                         message="Componiendo la música IA…")

    def run(jid, ev):
        from motion import music as MU
        from motion import video_io as V
        tick = _Throttle(jid, "music", ev, tok)
        t0 = time.time()
        try:
            r = MU.get_music(info["cfg"], info["dur"], info["style"], info["seed"],
                             lambda p=0, *_a: tick(pct=round(float(p or 0), 1)))
            if ev.is_set():
                raise V.Canceled("cancelado")
            if (r or {}).get("source") != "ai":
                tick.set(state="error", error="la música IA no se pudo generar (se usa la procedural)",
                         message="La música IA no se pudo generar; suena la procedural",
                         t=round(time.time() - t0, 1))
                return
            tick.set(state="ready", pct=100, t=round(time.time() - t0, 1), gen_s=r.get("gen_s"),
                     bpm=r.get("bpm"), message="Música IA lista")
            with _SIDE_LOCK:
                me = _SIDE.get((jid, "music"))
                tgt = me.get("target") if me is not None and me["cancel"] is ev else None
            if tgt:
                _music_resync(jid, ev, tick, tgt)
        except (V.Canceled, JobCanceled):
            tick.set(state="canceled", message="Generación cancelada (la edición cambió)")
        except Exception as e:  # noqa: la musica es un extra: la procedural sigue sonando
            traceback.print_exc()
            tick.set(state="error", error=str(e)[:300], message="La música IA falló; suena la procedural")

    _start_side(job_id, "music", run, key=key, restart=True, target=target, tok=tok)
    return "started"


def _music_resync(jid, ev, tick, tgt):
    """La pista de la propuesta ya esta en cache: re-dirige la MISMA peticion con sincronia (el
    director reutiliza la pista: < 1 s) y la guarda solo si el plan guardado sigue siendo la
    propuesta (concurrencia optimista). Si no, music_resync='available'."""
    from motion import director
    plan, rev = jobstore.get_plan(jid)
    if ev.is_set() or plan is None or rev != tgt["rev"]:
        if ((plan or {}).get("director") or {}).get("music_grid"):
            # otra re-direccion ya sincronizo el plan vigente (la pista estaba en cache)
            tick.set(state="ready", resync=None, message="Música IA lista")
        else:
            tick.set(state="ready", resync="available",
                     message="Música IA lista: re-dirige para sincronizar la edición (el plan cambió)")
        return
    tick.set(state="syncing", message="Sincronizando la edición con la música IA…")
    kw = {"music_sync": True}
    if _accepts(director.redirect, "music_timeout"):
        kw["music_timeout"] = max(REDIRECT_MUSIC_WAIT_S, REDIRECT_CACHED_WAIT_S)
    t0 = time.time()
    out = director.redirect(tgt["orig"], tgt.get("style"), tgt.get("intensity"), tgt.get("seed"), **kw)
    if tgt.get("music") is not None:
        out = _apply_music(out, tgt["music"])
    grid = (out.get("director") or {}).get("music_grid")
    if ev.is_set() or not grid:
        tick.set(state="ready", resync="available",
                 message="Música IA lista (sin sincronía automática: re-dirige para sincronizar)")
        return
    ok, new_rev = jobstore.update_plan(jid, out, base_rev=rev)
    if not ok:
        tick.set(state="ready", resync="available",
                 message="Música IA lista: re-dirige para sincronizar la edición (el plan cambió)")
        return
    tick.set(state="synced", resync=None, plan_rev=new_rev, from_rev=rev,
             from_hash=jobstore.plan_hash(plan), synced_hash=jobstore.plan_hash(out),
             bpm=grid.get("bpm"), sync_s=round(time.time() - t0, 2),
             message=f"Edición sincronizada con la música IA ({float(grid.get('bpm') or 0):.0f} BPM)")
    print(f"   musica: propuesta sincronizada (rev {rev} -> {new_rev}) en {time.time() - t0:.1f}s")
    _kick_analysis(jid, out, tgt.get("fmt") or "9:16")


def _synced_plan(job_id, plan):
    """El Studio manda la propuesta SIN sincronia y el servidor ya guardo su version sincronizada
    (mismo contenido que la propuesta y nadie edito despues): se usa la sincronizada. Asi la vista
    previa y el export salen al beat aunque el Studio no haya recargado el plan."""
    ms = _music_side(job_id)
    if ms.get("state") != "synced" or not isinstance(plan, dict):
        return plan
    try:
        if jobstore.plan_hash(plan) != ms.get("from_hash"):
            return plan
        stored, rev = jobstore.get_plan(job_id)
    except Exception:  # noqa
        return plan
    if stored is None or rev != ms.get("plan_rev"):
        return plan
    return stored


def _music_superseded(job_id, plan, base_rev, new_rev):
    """El autoguardado del Studio piso la version sincronizada con su plan sin rejilla de beats:
    se avisa (music_resync='available'; re-dirigir sincroniza al instante)."""
    ms = _music_side(job_id)
    if ms.get("state") != "synced" or base_rev != ms.get("plan_rev"):
        return
    if ((plan or {}).get("director") or {}).get("music_grid"):
        return
    jobstore.side_update(job_id, "music", state="superseded", resync="available", plan_rev=new_rev,
                         message="Editaste la propuesta: re-dirige para sincronizarla con la música")


# ================================================================ handlers de los workers
# Barra del analisis (monotona, con ETA): compose 2-32 %, analisis 34-70 % (o 2-70 % sin
# compose), director 70-99 % con sus pasos (la musica con su propio tramo y etiqueta).
PHASE_COMPOSE = (2.0, 32.0)
PHASE_ANALYZE_AFTER_COMPOSE = (34.0, 70.0)
PHASE_ANALYZE = (2.0, 70.0)
DIRECTOR_STEPS = {"tomas": (70.0, 86.0), "guion": (86.0, 90.0), "coreografia": (90.0, 92.0),
                  "musica": (92.0, 98.0), "listo": (99.0, 99.0)}
DIRECTOR_LABELS = {"tomas": "analizando las tomas", "guion": "entendiendo el guion",
                   "coreografia": "coreografiando la edición", "musica": "componiendo la música"}
# pasos sin sub-progreso: la barra avanza sola (asintotica) con esta duracion esperada (s)
DIRECTOR_CREEP = {"guion": 15.0, "musica": 25.0}


class _PhaseBar:
    """Progreso del trabajo de analisis: nunca retrocede, cada cambio de paso se escribe al
    instante (force) y lleva ETA. `creep` hace avanzar la barra en pasos largos sin
    sub-progreso (musica IA, guion con LLM) para que no parezca congelada."""

    def __init__(self, job_id, prog, t0=None):
        self.job_id, self.prog = job_id, prog
        self.t0 = t0 or time.time()
        self.pct = 0.0
        self.last = None
        self.last_step = None
        self._lock = threading.Lock()
        self._creep_ev = None

    def _eta(self, pct):
        el = time.time() - self.t0
        return round(el), (round(el / pct * (100.0 - pct)) if pct >= 8.0 else None)

    def set(self, pct, message=None, stage=None, force=False):
        self.stop_creep()
        with self._lock:            # calculo y escritura juntos: el hilo de creep no la baja
            pct = max(self.pct, min(99.5, float(pct)))
            self.pct = pct
            el, eta = self._eta(pct)
            key = (stage, message)
            kw = dict(pct=round(pct, 1), elapsed=el, eta=eta)
            if message is not None:
                kw["message"] = message
            if stage is not None:
                kw["stage"] = stage
            self.prog.update(force=force or key != self.last, **kw)
            self.last = key

    def creep(self, lo, hi, expected_s):
        """Hilo que sube la barra de lo a ~hi mientras nadie llame a set()."""
        self.stop_creep()
        ev = threading.Event()
        self._creep_ev = ev
        t0 = time.time()

        def run():
            import math
            while not ev.wait(1.0):
                p = lo + (hi - lo) * 0.95 * (1.0 - math.exp(-(time.time() - t0) / max(1.0, expected_s)))
                with self._lock:
                    if ev.is_set() or p <= self.pct:
                        continue
                    self.pct = p
                    el, eta = self._eta(p)
                    try:
                        jobstore.update(self.job_id, pct=round(p, 1), elapsed=el, eta=eta)
                    except Exception:  # noqa
                        pass
        threading.Thread(target=run, daemon=True, name=f"reelfy-creep-{self.job_id}").start()

    def stop_creep(self):
        if self._creep_ev is not None:
            self._creep_ev.set()
            self._creep_ev = None


def _analysis_progress(bar, lo, hi):
    def step(pct, m):
        bar.set(lo + (hi - lo) * float(pct) / 100.0, m, "analyze", force=True)
    return step


def _finish_analysis(job_id, prog, plan, params, bar=None):
    """Tras el analisis: director (fase 'Dirigiendo la edicion'; si falla, heuristicas y
    NUNCA tumba el analisis), plan a review y analisis de fondo del render."""
    d = _director_params(params.get("director"))
    warns = []
    bar = bar or _PhaseBar(job_id, prog)
    if params.get("profile"):
        # perfil de la persona (nombre, profesion, redes): el director lo integra en la edicion
        plan["profile"] = params["profile"]
    if d["enabled"]:
        bar.set(DIRECTOR_STEPS["tomas"][0], "Dirigiendo la edición…", "director", force=True)

        def dstep(name, pct=0.0, info=""):
            lo, hi = DIRECTOR_STEPS.get(name, (bar.pct, bar.pct))
            label = DIRECTOR_LABELS.get(name, "")
            try:
                bar.set(lo + (hi - lo) * float(pct or 0), stage="director",
                        message="Dirigiendo la edición…" + (f" ({label})" if label else ""),
                        force=name != bar.last_step)
                bar.last_step = name
                if name in DIRECTOR_CREEP:
                    bar.creep(lo + (hi - lo) * float(pct or 0), hi, DIRECTOR_CREEP[name])
            except JobCanceled:
                pass       # el director no se interrumpe a medias; se revisa al terminar
        src_names = [s.get("src") for s in plan.get("shots") or []]
        try:
            plan, warns = _direct_plan(plan, d, on_step=dstep)
        finally:
            bar.stop_creep()
        shots = plan.get("shots") or []
        if src_names and len(src_names) == len(shots):
            for s, name in zip(shots, src_names):   # nombre del clip original (compose)
                if name:
                    s["src"] = name
        prog.cancel.check()
    jobstore.update(job_id, state="review", phase="review", pct=100, stage="review",
                    message="Análisis listo", plan=plan, warnings=warns)
    fmt = d.get("fmt") or "9:16"
    if _is_v2(plan):
        # las opciones de /compose o /analyze llegaron crudas (sin video): se guardan las
        # EFECTIVAS (un aire que no aplica a esta fuente no se muestra ni relanza nada)
        with _ENGINE_LOCK:
            raw = _ENGINE.get(job_id)
        if raw and raw.get("air"):
            _set_engine(job_id, raw, plan, fmt)
    _kick_analysis(job_id, plan, fmt)
    _kick_src_proxy(job_id)            # proxy 720p del video para el Studio (seeks rapidos)


def _analyze(job_id, job):
    """Handler 'analyze': job['params'] = {glossary, n, director}."""
    _refresh_llm()
    prog = Progress(job_id)
    bar = _PhaseBar(job_id, prog)
    p = job["params"] or {}
    video = INPUT / f"{job_id}{job['ext']}"
    plan = pipeline.analyze(video, p.get("glossary", ""), p.get("n", 2),
                            on_step=_analysis_progress(bar, *PHASE_ANALYZE))
    _finish_analysis(job_id, prog, plan, p, bar)


def _compose(job_id, job):
    """Handler 'compose': arma la composición del timeline (varios clips/imágenes +
    overlays) en UN video y luego lo analiza como cualquier fuente. job['params'] =
    {spec, glossary, n, director}. La IA se aplica sobre la composición (timeline + IA).
    Los limites REALES de cada clip quedan en plan.shots (director y render)."""
    import re
    import compose as compose_mod
    _refresh_llm()
    prog = Progress(job_id)
    bar = _PhaseBar(job_id, prog)
    p = job["params"] or {}
    spec = p.get("spec") or {}
    video = INPUT / f"{job_id}.mp4"
    lo, hi = PHASE_COMPOSE

    def cstep(m):
        # "Preparando k/n…": k clips normalizados de n -> tramo 2-32 %
        mm = re.search(r"(\d+)\s*/\s*(\d+)", str(m))
        if mm and int(mm.group(2)) > 0:
            frac = min(1.0, int(mm.group(1)) / int(mm.group(2)))
            bar.set(lo + (hi - lo) * 0.92 * frac, m, "compose", force=True)
        else:
            bar.set(lo + (hi - lo) * 0.95, m, "compose", force=True)
    bar.set(lo, "Montando tu línea de tiempo…", "compose", force=True)
    t0 = time.time()
    info = {}
    compose_mod.compose(spec, str(video), paths.WORK / f"compose_{job_id}", on_step=cstep,
                        info=info)
    shutil.rmtree(paths.WORK / f"compose_{job_id}", ignore_errors=True)   # segmentos 4K
    print(f"   compose: {len(spec.get('main') or [])} clips en {time.time() - t0:.1f}s "
          f"(lienzo {info.get('W')}x{info.get('H')}, carga {_load()})")
    plan = pipeline.analyze(video, p.get("glossary", ""), p.get("n", 2),
                            on_step=_analysis_progress(bar, *PHASE_ANALYZE_AFTER_COMPOSE))
    dur = float(plan.get("duration") or 0) or None
    shots = []
    for s in info.get("shots") or []:
        a, b = float(s["start"]), float(s["end"])
        if dur:
            a, b = min(a, dur), min(b, dur)
        if b - a >= 0.05:
            shots.append({"start": round(a, 3), "end": round(b, 3), "src": s.get("src", "")})
    if shots:
        shots[-1]["end"] = round(dur or shots[-1]["end"], 3)
        plan["shots"] = shots
    _finish_analysis(job_id, prog, plan, p, bar)


CUSTOM_KEYS = ("cap_color", "cap_font", "cap_scale", "cap_pos", "zoom_amt", "air", "logo",
               "base_color", "kw_color", "cap_outline", "cap_shadow", "cap_case",
               "cap_margin", "effect")


def _custom(body):
    return {k: body[k] for k in CUSTOM_KEYS if body.get(k) is not None}


def _render(job_id, job):
    """Handler 'render': job['params'] trae las opciones; job['plan'] el plan.
    Plan v2 -> motor pro (render.render_pro, con shorts); si no -> pipeline legacy."""
    p = job["params"] or {}
    if p.get("pro"):
        _await_same_analysis(job_id, job["plan"], p.get("fmt") or "9:16", p.get("engine"))
    _EXPORTING.add(job_id)
    try:
        _cancel_side(name="analysis")           # el analisis de fondo no pisa el export
        if p.get("pro"):
            return _render_pro(job_id, job)
        return _render_legacy(job_id, job)
    finally:
        _EXPORTING.discard(job_id)


def _await_same_analysis(job_id, plan, fmt, eo=None):
    """Si el analisis de fondo de ESTE trabajo corre con la MISMA base (mismo video, cortes,
    reencuadre y formato; los textos/graficos/SFX no cuentan), el export lo espera y reutiliza
    base, rostro y matte de la cache en vez de cancelarlo y repetirlo. Otra base: se cancela."""
    cur = _side_running(job_id, "analysis")
    if not cur:
        return
    bkey, _ = _analysis_keys(plan, fmt, eo)
    if cur.get("bkey") != bkey:
        return
    if (jobstore.side_get(job_id, "analysis") or {}).get("state") != "running":
        return                                  # en cola (otro trabajo analiza): se cancela
    with _SIDE_LOCK:
        cur.pop("next", None)                   # el export calcula lo que falte
    prog = Progress(job_id)
    prog.update(force=True, pct=0, stage="analysis", message="Terminando el análisis previo…")
    while cur["thread"].is_alive():
        try:
            prog.cancel.check()
        except JobCanceled:
            cur["cancel"].set()
            raise
        cur["thread"].join(timeout=0.5)


def _render_legacy(job_id, job):
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
        prog.update(pct=round(o, 1), elapsed=round(el), eta=round(eta) if eta else None,
                    stage="render" if stage == "full" else "shorts")

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
    jobstore.update(job_id, state="done", phase="done", pct=100, stage="done",
                    message="¡Listo!", clips=clips, eta=0)


STAGE_MSG = {"timeline": "Preparando la línea de tiempo…",
             "base": "Preparando el video base (reencuadre, color y cortes)…",
             "analysis": "Analizando rostro y sujeto…", "audio": "Preparando música y efectos de sonido…",
             "render": "Componiendo efectos, textos y gráficos…", "mux": "Uniendo y exportando…"}
STATS_KEYS = ("duration", "n_frames", "W", "H", "fps", "fmt", "t_total", "t_base", "t_voice",
              "t_face", "t_matte", "t_analysis", "t_audio", "t_compose", "t_mux", "workers",
              "ms_per_frame_wall", "ms_per_frame_proc", "cached_base", "reframe", "sfx_events",
              "removed", "items", "skipped", "music")


def _render_pro(job_id, job):
    """Export del plan v2: render.render_pro con shorts, progreso por etapa (stage) + ETA,
    cancelable (mata ffmpeg y los procesos del compositor)."""
    from motion import render as R
    from motion import video_io as V
    prog = Progress(job_id)
    p = job["params"] or {}
    plan = job["plan"]
    fmt = p.get("fmt") or "9:16"
    eo = p.get("engine") or {}
    token = uuid.uuid4().hex[:6]
    out = OUTPUT / f"{job_id}_{token}.mp4"
    t0 = time.time()
    last_stage = [None]

    def on_pct(stage, pct):
        el = time.time() - t0
        eta = el / pct * (100 - pct) if pct > 3 else None
        kw = dict(pct=round(float(pct), 1), stage=stage, elapsed=round(el),
                  eta=round(eta) if eta else None)
        if stage != last_stage[0]:
            last_stage[0] = stage
            kw["message"] = STAGE_MSG.get(stage, "Renderizando…")
            prog.update(force=True, **kw)
        else:
            prog.update(**kw)

    def on_step(m):
        prog.update(force=True, message=m)

    opts = _motor_opts(eo, fmt, shorts=bool(p.get("shorts", True)), shorts_dir=str(OUTPUT),
                       on_pct=on_pct, on_step=on_step, cancel=prog.cancel.canceled)
    if p.get("workers"):
        opts["workers"] = int(p["workers"])
    prog.update(force=True, pct=0, stage="timeline", message="Preparando el render…")
    try:
        stats = R.render_pro(plan, out, opts)
    except V.Canceled:
        raise JobCanceled()
    st = {k: stats[k] for k in STATS_KEYS if k in stats}
    if isinstance(st.get("music"), dict):
        st["music"] = {k: v for k, v in st["music"].items() if k != "wav"}   # ruta local
    st["load"] = _load()
    st["engine"] = {"air": eo.get("air"), "logo": bool(eo.get("logo")),
                    "enhance_audio": eo.get("enhance_audio", True)}
    clips = [{"name": "Video completo", "file": out.name, "duration": round(stats.get("duration", 0), 3),
              "W": stats.get("W"), "H": stats.get("H"), "stats": st}]
    import thumbnail as thumb_mod
    for k, s in enumerate(stats.get("shorts") or [], 1):
        f = Path(s["path"])
        thumb = f.with_name(f.stem + "_thumb.jpg")
        try:
            thumb_mod.make_thumbnail(f, thumb, s.get("title", ""))
        except Exception as e:  # noqa: la miniatura es un extra
            print(f"   miniatura no disponible: {e}")
        clips.append({"name": f"Short {k}: {s.get('title', '')}".strip(" :"), "file": f.name,
                      "start": s.get("start"), "end": s.get("end"),
                      "thumb": thumb.name if thumb.exists() else None})
    warns = list(stats.get("warnings") or []) + [f"omitido: {x}" for x in stats.get("skipped") or []]
    try:
        (OUTPUT / f"{out.stem}_stats.json").write_text(json.dumps(stats, default=str, indent=1))
    except Exception:  # noqa
        pass
    jobstore.update(job_id, state="done", phase="done", pct=100, stage="done",
                    message="¡Listo!", clips=clips, eta=0, elapsed=round(time.time() - t0),
                    warnings=warns)
    _trim_exports(job_id, keep=KEEP_EXPORTS, protect=out.stem)
    _gc_later("export")


# ================================================================ disco (recoleccion de basura)
# Cada export deja {job}_{token}.mp4 (+ shorts, miniaturas, stats); cada conjunto de cortes un
# base nuevo en work/motion_cache; la musica su cache. Sin esto el disco crece sin limite.
KEEP_EXPORTS = max(1, int(os.environ.get("REELFY_KEEP_EXPORTS", "2")))   # exports por trabajo
CACHE_CAP_GB = float(os.environ.get("REELFY_CACHE_GB", "5"))             # motion_cache + musica
MIN_FREE_GB = float(os.environ.get("REELFY_MIN_FREE_GB", "5"))           # presion de disco
ORPHAN_AGE_S = 3600.0            # archivos de trabajos que ya no existen (tras 1 h)
SOURCE_AGE_S = 24 * 3600.0       # fuentes subidas que ningun trabajo usa (tras 24 h)
_JOB_RE = __import__("re").compile(r"^([0-9a-f]{12})(?:[._]|$)")
_GC_LOCK = threading.Lock()


def _rm(p):
    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _job_files(job_id):
    """Archivos pesados de un trabajo: entrada (video compuesto), exports, vistas previas,
    musica escuchable, audio/transcripcion del analisis y restos del montaje."""
    out = []
    for d in (INPUT, OUTPUT, paths.WORK):
        if d.exists():
            out += [p for p in d.glob(f"{job_id}*") if _JOB_RE.match(p.name)]
    if MUSIC_OUT.exists():
        out += list(MUSIC_OUT.glob(f"music_{job_id}_*"))
    c = paths.WORK / f"compose_{job_id}"
    if c.exists():
        out.append(c)
    return out


def _purge_job_files(ids):
    n = 0
    for jid in ids or []:
        for p in _job_files(jid):
            n += _rm(p)
    return n


def _trim_exports(job_id, keep=KEEP_EXPORTS, protect=None):
    """Conserva los `keep` exports mas recientes del trabajo (con sus shorts, miniaturas y
    stats); los anteriores se borran. `protect` = stem del export recien hecho."""
    import re
    rx = re.compile(rf"^{re.escape(job_id)}_([0-9a-f]{{6}})\.mp4$")
    exps = sorted((p for p in OUTPUT.glob(f"{job_id}_*.mp4") if rx.match(p.name)),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    n = 0
    for p in exps[keep:]:
        if protect and p.stem == protect:
            continue
        for q in OUTPUT.glob(f"{p.stem}*"):
            n += _rm(q)
    return n


def _gc_orphans(now=None):
    """Archivos de trabajos que ya no estan en la base (purgados antes de v0.5 o a mano)."""
    now = now or time.time()
    ids = set(jobstore.all_ids())
    n = 0
    for d in (INPUT, OUTPUT, paths.WORK, MUSIC_OUT):
        if not d.exists():
            continue
        for p in d.iterdir():
            name = p.name[len("music_"):] if d == MUSIC_OUT and p.name.startswith("music_") else p.name
            if d == paths.WORK and name.startswith("compose_"):
                name = name[len("compose_"):]
            m = _JOB_RE.match(name)
            if not m or m.group(1) in ids:
                continue
            try:
                if now - p.stat().st_mtime < ORPHAN_AGE_S:
                    continue
            except OSError:
                continue
            n += _rm(p)
    return n


def _gc_sources(now=None):
    """Fuentes subidas (input/sources) que ningun trabajo referencia (ni su montaje ni un
    medio propio de su plan), tras SOURCE_AGE_S (una recien subida puede estar esperando su
    /compose o su item de medio propio)."""
    now = now or time.time()
    if not SOURCES.exists():
        return 0
    used = set()
    for params in jobstore.all_params().values():
        spec = (params or {}).get("spec") or {}
        for it in (spec.get("main") or []) + (spec.get("overlays") or []):
            if isinstance(it, dict) and it.get("path"):
                used.add(Path(str(it["path"])).name.split(".")[0].split("_")[0])
    # medios propios que un plan referencia ({sid, type} en fx): tampoco se borran
    used |= jobstore.referenced_sids()
    # fotos de perfil de los perfiles guardados (el Studio las reutiliza en otros proyectos)
    used |= {str((pr.get("avatar") or {}).get("sid")) for pr in _profiles_load() if pr.get("avatar")}
    groups = {}
    for p in SOURCES.iterdir():
        sid = p.name.split(".")[0].split("_")[0]
        groups.setdefault(sid, []).append(p)
    n = 0
    for sid, files in groups.items():
        if sid in used:
            continue
        try:
            newest = max(f.stat().st_mtime for f in files)
        except (OSError, ValueError):
            continue
        if now - newest < SOURCE_AGE_S:
            continue
        for f in files:
            n += _rm(f)
    return n


def _dir_bytes(d):
    t = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                t += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return t


def _cap_caches(cap_gb=None):
    """Tope LRU de las caches regenerables: work/motion_cache (bases, voces, rostro, mattes) y
    work/music. Primero la poda propia de cada modulo; si sigue arriba del tope, bases y
    mattes menos recientes. Nada usado en los ultimos 10 min se toca."""
    cap = float(CACHE_CAP_GB if cap_gb is None else cap_gb) * GB
    mc = paths.WORK / "motion_cache"
    mdir = paths.WORK / "music"
    info = {}
    try:
        from motion import video_io as V
        from motion import matte as MT
        from motion import music as MU
    except Exception as e:  # noqa
        return {"error": str(e)}
    try:
        if mdir.exists():
            info["music_freed"] = MU.prune_cache(mdir, int(min(MU.CACHE_MAX_BYTES, cap * 0.3)))
        if mc.exists():
            V.prune_cache(mc, keep=6)
            MT.prune_cache(mc, max_mb=cap * 0.5 / 1e6, keep=3)
            k = 5
            while _dir_bytes(mc) + (_dir_bytes(mdir) if mdir.exists() else 0) > cap and k >= 1:
                V.prune_cache(mc, keep=k, min_age=600.0)
                MT.prune_cache(mc, max_mb=cap * 0.25 / 1e6, keep=max(1, k - 3))
                k -= 1
        info["cache_gb"] = round(((_dir_bytes(mc) if mc.exists() else 0) +
                                  (_dir_bytes(mdir) if mdir.exists() else 0)) / GB, 2)
    except Exception as e:  # noqa: la limpieza nunca tumba el servidor
        traceback.print_exc()
        info["error"] = str(e)
    return info


def _busy():
    """Hay export, vista previa o analisis en curso (la poda de caches espera)."""
    if _EXPORTING:
        return True
    with _SIDE_LOCK:
        return any(v["thread"].is_alive() for v in _SIDE.values())


def disk_gc(reason=""):
    """Recoleccion de basura del disco. Trabajos viejos (jobstore.KEEP_LAST) -> se borran sus
    archivos; huerfanos; fuentes sin uso; tope de caches. Con poco disco libre (< MIN_FREE_GB)
    purga tambien trabajos viejos hasta dejar los 5 mas recientes."""
    if not _GC_LOCK.acquire(blocking=False):
        return {"skipped": "en curso"}
    t0 = time.time()
    try:
        res = {"reason": reason}
        ids = jobstore.housekeep()
        res["jobs_purged"] = len(ids)
        res["files"] = _purge_job_files(ids)
        res["orphans"] = _gc_orphans()
        res["sources"] = _gc_sources()
        free = _disk_free_gb()
        if not _busy():
            cap = CACHE_CAP_GB if free >= MIN_FREE_GB else max(0.5, min(CACHE_CAP_GB, free * 0.25))
            res["caches"] = _cap_caches(cap)
        if _disk_free_gb() < MIN_FREE_GB:
            ids = jobstore.housekeep(keep=5)
            res["pressure_purged"] = len(ids)
            res["files"] += _purge_job_files(ids)
        res["free_gb"] = round(_disk_free_gb(), 1)
        res["t"] = round(time.time() - t0, 2)
        if res["jobs_purged"] or res["files"] or res["orphans"] or res["sources"] or \
                res.get("pressure_purged"):
            print(f"   limpieza ({reason}): {res}")
        return res
    finally:
        _GC_LOCK.release()


def _gc_later(reason, delay=2.0):
    def run():
        time.sleep(delay)
        try:
            disk_gc(reason)
        except Exception:  # noqa
            traceback.print_exc()
    threading.Thread(target=run, daemon=True, name="reelfy-gc").start()


if not _CHILD:
    jobstore.init()
    jobstore.start_workers({"analyze": _analyze, "render": _render, "compose": _compose})
    _gc_later("arranque", delay=5.0)
    if SETUP["state"] == "ready":
        _start_optional()   # modelos opcionales faltantes: en segundo plano, sin bloquear


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


@app.get("/static/{filename}")
def static_file(filename: str):
    f = STATIC / Path(filename).name
    if not f.exists():
        raise HTTPException(404, "No existe")
    return FileResponse(f)


def _initial_engine(job_id, raw):
    """Opciones del proyecto que el Studio ya conoce al crear el trabajo (`engine` en /compose
    y /analyze): el primer analisis de fondo prepara el video base con ellas (sin esto un
    aire distinto de 0 en una fuente ancha obliga a rehacer el base al abrir el Studio)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except Exception:  # noqa
            raw = None
    if isinstance(raw, dict) and any(k in raw for k in ENGINE_KEYS):
        _set_engine(job_id, _engine_opts(raw))


@app.post("/analyze")
async def analyze(video: UploadFile, glossary: str = Form(""), highlights: int = Form(2),
                  director: str = Form(""), engine: str = Form(""), profile: str = Form(""),
                  profile_id: str = Form("")):
    ext = Path(video.filename or "v.mp4").suffix.lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".m4v", ".mkv", ".webm"):
        raise HTTPException(400, "Formato no soportado. Usa MP4 o MOV.")
    _require_engine()
    _require_license()
    job_id = uuid.uuid4().hex[:12]
    dst = INPUT / f"{job_id}{ext}"
    with open(dst, "wb") as f:
        while chunk := await video.read(1 << 20):
            await run_in_threadpool(f.write, chunk)
    prof = await run_in_threadpool(_profile_param, profile, profile_id)
    await run_in_threadpool(jobstore.create, job_id, kind="analyze", ext=ext,
                            params={"glossary": glossary, "n": highlights,
                                    "director": _director_params(director),
                                    **({"profile": prof} if prof else {})})
    await run_in_threadpool(_initial_engine, job_id, engine)
    _gc_later("nuevo trabajo")
    return {"job_id": job_id}


SOURCES = INPUT / "sources"
_PROXY_SEM = threading.Semaphore(1)


def _vcodec(path) -> str:
    """Nombre del códec de video (h264, hevc, av1, prores, ...) o '' si falla."""
    try:
        r = subprocess.run(
            [str(paths.FFPROBE), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "default=nk=1:nw=1", str(path)],
            capture_output=True, text=True, timeout=20)
        return (r.stdout or "").strip().lower()
    except Exception:  # noqa
        return ""


def _make_proxy(src, sid, info):
    """Proxy web-friendly SOLO para el preview/editor. El WebView (WKWebView) no
    reproduce videos con un lado > ~4096 px ni códecs exóticos (av1, prores…).
    Genera un H.264 (≤1600 px de lado, yuv420p, faststart) para que el editor
    cargue CUALQUIER video. El render SIEMPRE usa el original (sin redimensionar).
    Se escribe a un temporal y se renombra: nunca se sirve un proxy a medias.
    Devuelve el Path del proxy o None si no hace falta / falla."""
    w0, h0 = int(info.get("w") or 0), int(info.get("h") or 0)
    codec = _vcodec(src)
    web_safe = codec in ("h264", "hevc")  # WKWebView reproduce H.264 y HEVC
    if max(w0, h0) <= 1920 and web_safe:
        return None  # ya es reproducible en el editor tal cual
    pv = SOURCES / f"{sid}_proxy.mp4"
    tmp = SOURCES / f"{sid}_proxy.part.mp4"
    # Mac: encoder por hardware (casi sin CPU: no compite con el montaje y el analisis que
    # arrancan enseguida); si falla, run_ff reintenta con libx264. Resto: libx264 veryfast.
    venc = (["-c:v", "h264_videotoolbox", "-b:v", "5M"] if paths.IS_MAC else
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "4"])
    # prioridad baja (sin preexec_fn: no es seguro con hilos)
    low = ["nice", "-n", "10"] if not paths.IS_WINDOWS and shutil.which("nice") else []
    try:
        with _PROXY_SEM:
            paths.run_ff(
                [*low, str(paths.FFMPEG), "-y", "-i", str(src), "-vf",
                 "scale='if(gte(iw,ih),min(1600,iw),-2)':'if(gte(iw,ih),-2,min(1600,ih))'",
                 *venc, "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)],
                stderr=subprocess.DEVNULL)
        tmp.replace(pv)
        return pv
    except Exception:  # noqa
        tmp.unlink(missing_ok=True)
        return None


def _source_meta(sid):
    try:
        return json.loads((SOURCES / f"{sid}_meta.json").read_text())
    except Exception:  # noqa
        return {}


def _ingest_source(dst, sid, name, is_img):
    """Probe + miniatura de una fuente subida (bloqueante: corre en el threadpool). El proxy
    del editor se genera en SEGUNDO PLANO (no retrasa la respuesta ni el event loop)."""
    import compose as compose_mod
    info = compose_mod.probe(dst)
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
    try:
        (SOURCES / f"{sid}_meta.json").write_text(json.dumps({"name": name, **info}))
    except Exception:  # noqa
        pass
    if not is_img:
        threading.Thread(target=_make_proxy, args=(dst, sid, info), daemon=True,
                         name=f"reelfy-proxy-{sid}").start()
    return info, thumb


@app.post("/sources")
async def add_sources(files: list[UploadFile]):
    """Sube uno o varios archivos fuente (video o imagen) para el editor de timeline.
    Devuelve por cada uno: id, tipo, duración, dimensiones y una miniatura."""
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
                await run_in_threadpool(f.write, chunk)
        info, thumb = await run_in_threadpool(_ingest_source, dst, sid, up.filename or "", is_img)
        typ = "image" if is_img else "video"
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


def _resolve_source(sid):
    stem = Path(str(sid)).stem
    for p in SOURCES.glob(f"{stem}.*"):
        if p.suffix.lower() in (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".jpg", ".jpeg",
                                ".png", ".webp", ".heic"):
            return p
    return None


@app.get("/tlsource/{sid}")
def source_file(sid: str):
    sid = Path(sid).stem
    # Preferimos el proxy web-friendly para el preview/editor (el original puede
    # ser HEVC/oversized que el WebView no decodifica). El render usa el original.
    proxy = SOURCES / f"{sid}_proxy.mp4"
    if proxy.exists():
        return FileResponse(proxy, headers={"Accept-Ranges": "bytes"})
    p = _resolve_source(sid)
    if p:
        return FileResponse(p, headers={"Accept-Ranges": "bytes"})
    raise HTTPException(404, "No existe")


@app.post("/compose")
def compose_timeline(body: dict = Body(...)):
    """Recibe la composición del timeline y arranca compose->analyze->director en un job.
    body: {main:[{sid,type,in,out|dur}], overlays:[{sid,type,start,dur,pos,size}],
           glossary, highlights, director:{style,intensity,seed,music},
           engine?:{air, enhance_audio, logo}}. `def`: resuelve fuentes en disco y escribe
    sqlite en el threadpool."""
    _require_engine()
    _require_license()
    main = []
    for s in body.get("main", []):
        path = _resolve_source(s.get("sid", ""))
        if not path:
            continue
        item = {"path": str(path), "type": s.get("type", "video"),
                "name": _source_meta(path.stem).get("name") or path.name,
                "start": float(s.get("start", 0) or 0), "layer": int(s.get("layer", 0) or 0),
                "fadeIn": float(s.get("fadeIn", 0) or 0), "fadeOut": float(s.get("fadeOut", 0) or 0)}
        if isinstance(s.get("trans"), dict) and s["trans"].get("type") == "crossfade":
            item["trans"] = {"type": "crossfade", "dur": float(s["trans"].get("dur", 0.5))}
        if item["type"] == "image":
            item["dur"] = float(s.get("dur", 3.0))
        else:
            item["in"] = float(s.get("in", 0)); item["out"] = float(s.get("out", 0) or 5)
            item["vol"] = float(s.get("vol", 1.0)); item["mute"] = bool(s.get("mute", False))
        main.append(item)
    if not main:
        raise HTTPException(400, "Agrega al menos un clip a la pista principal.")
    overlays = []
    for o in body.get("overlays", []):
        path = _resolve_source(o.get("sid", ""))
        if not path:
            continue
        ov = {"path": str(path), "type": o.get("type", "image"),
              "start": float(o.get("start", 0)), "dur": float(o.get("dur", 2.5)),
              "pos": o.get("pos", "center"), "size": float(o.get("size", 0.35))}
        if o.get("x") is not None and o.get("y") is not None:
            ov["x"] = float(o["x"]); ov["y"] = float(o["y"])
        overlays.append(ov)
    spec = {"fps": 30, "main": main, "overlays": overlays}
    job_id = uuid.uuid4().hex[:12]
    prof = _profile_param(body.get("profile"), body.get("profile_id"))
    jobstore.create(job_id, kind="compose", ext=".mp4",
                    params={"spec": spec, "glossary": body.get("glossary", ""),
                            "n": int(body.get("highlights", 2)),
                            "director": _director_params(body.get("director")),
                            **({"profile": prof} if prof else {})})
    _initial_engine(job_id, body.get("engine"))
    _gc_later("nuevo trabajo")
    return {"job_id": job_id}


@app.post("/render/{job_id}")
def render(job_id: str, body: dict = Body(...)):
    """`def`: importa el motor (skia/cv2) y escribe sqlite en el threadpool."""
    job = _job_or_404(job_id)
    plan = body.get("plan") or jobstore.get_plan(job_id)[0]
    if not plan:
        raise HTTPException(409, "El job no tiene plan; analiza el video primero.")
    if job["state"] in ("queued", "running"):
        raise HTTPException(409, "Este proyecto ya está procesándose; espera o cancela.")
    _require_license()
    if _is_v2(plan) and not body.get("legacy"):
        plan = _synced_plan(job_id, plan)
        fmt = _fmt_of(body)
        from motion import render as R
        if fmt not in R.FORMAT_NAMES:
            raise HTTPException(400, f"Formato desconocido: {fmt}. Usa {', '.join(R.FORMAT_NAMES)}.")
        eo = _eff_engine(plan, fmt, _engine_opts(body))
        _set_engine(job_id, eo, plan, fmt)
        params = {"pro": True, "fmt": fmt, "shorts": bool(body.get("shorts", True)),
                  "engine": eo}
        if body.get("workers"):
            params["workers"] = int(body["workers"])
    else:
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
    return {"ok": True, "pro": bool(params.get("pro")), "plan_rev": jobstore.get_light(job_id)["plan_rev"]}


CANCEL_ONLY = ("preview", "analysis", "direct", "music", "job")


@app.post("/cancel/{job_id}")
def cancel(job_id: str, only: str | None = None):
    """Cancela el trabajo principal (analisis/render) y sus tareas laterales (vista previa
    real, analisis de fondo, re-dirigir en segundo plano, musica IA). `?only=preview|analysis|
    direct|music` cancela SOLO esa tarea lateral (cancelar la vista previa no mata un export en
    curso); `?only=job` solo el trabajo principal. Con un EXPORT en curso, sin `only` se cancelan
    el export, el analisis de fondo y re-dirigir, pero NO la vista previa (ni su actualizacion con
    la musica IA) ni la generacion de la musica: son de la edicion, no del export."""
    job = _job_or_404(job_id)
    if only and only not in CANCEL_ONLY:
        raise HTTPException(400, f"only debe ser uno de {', '.join(CANCEL_ONLY)}")
    if only and only != "job":
        return {"ok": True, "side": _cancel_side(job_id=job_id, name=only)}
    exporting = job_id in _EXPORTING or (job.get("kind") == "render" and
                                         job.get("state") in ("queued", "running"))
    jobstore.request_cancel(job_id)
    if only == "job":
        return {"ok": True, "side": 0}
    if exporting:
        n = sum(_cancel_side(job_id=job_id, name=k) for k in ("analysis", "direct"))
    else:
        n = _cancel_side(job_id=job_id)
    return {"ok": True, "side": n}


@app.get("/jobs")
def jobs_recent(limit: int = 20):
    """Proyectos recientes con el resumen de su plan: [{id, kind, state, phase, pct, message,
    error, created, updated, plan_rev, has_plan, version, topic, duration, final_duration,
    style, shots, thumb}] (`thumb` = URL de la miniatura; null sin video)."""
    out = jobstore.recent(max(1, min(50, int(limit))))
    for j in out:
        ext = j.get("ext")
        ok = bool(ext) and (INPUT / f"{j['id']}{ext}").exists()
        if ok and not _thumb_paths(j["id"])[0].exists():
            # un trabajo con error y sin plan (video que no se pudo leer) o cuya miniatura ya
            # fallo no anuncia miniatura (cada pedido relanzaria ffmpeg para nada)
            ok = not (j.get("state") == "error" and not j.get("has_plan")) and \
                not _thumb_paths(j["id"])[1].exists()
        j["thumb"] = f"/thumb/{j['id']}" if ok else None
    return out


@app.post("/preview/{job_id}")
def preview(job_id: str, body: dict = Body(...)):
    """Vista previa. `pro: true` (plan v2): lanza la tarea `preview` (render.render_draft
    540p con audio, mismo pipeline que el export); progreso en /status (preview_pct,
    preview_stage, preview_file). Sin `pro`: el preview legacy (sincrono, en el threadpool)."""
    _job_or_404(job_id)
    plan = body.get("plan") or jobstore.get_plan(job_id)[0]
    if not plan:
        raise HTTPException(409, "El job no tiene plan; analiza el video primero.")
    if body.get("pro"):
        return _start_preview_pro(job_id, _synced_plan(job_id, plan), _fmt_of(body), _engine_opts(body))
    return _preview_legacy(job_id, plan, body)


# vista previa con la musica IA aun generandose: se espera (sondeo cancelable) a lo mas esto
PREVIEW_MUSIC_WAIT_S = float(os.environ.get("REELFY_PREVIEW_MUSIC_WAIT", "300"))


def _start_preview_pro(job_id, plan, fmt, eo=None):
    _require_license()
    if not _is_v2(plan):
        raise HTTPException(400, "La vista previa real necesita un plan v2 (dirigido).")
    from motion import render as R
    if fmt not in R.FORMAT_NAMES:
        raise HTTPException(400, f"Formato desconocido: {fmt}.")
    plan = json.loads(json.dumps(plan))
    eo = _eff_engine(plan, fmt, eo if eo is not None else {"enhance_audio": True})
    _set_engine(job_id, eo, plan, fmt)
    bkey, key = _analysis_keys(plan, fmt, eo)
    running = _side_running(job_id, "analysis")
    if running and running.get("bkey") != bkey:
        running["cancel"].set()        # analisis de otra base (otros cortes/formato): estorba
    # misma base (p. ej. solo cambiaron textos): la vista previa espera el candado del trabajo
    # y reutiliza base, rostro y matte que el analisis deja en cache

    # estado "queued" ANTES de responder: un /status inmediato no ve la vista previa anterior
    tok = uuid.uuid4().hex[:8]
    jobstore.side_update(job_id, "preview", replace=True, state="queued", pct=0,
                         stage="", key=key, fmt=fmt, file=None, tok=tok,
                         message="Esperando el análisis…")

    def draft(jid, ev, tick, lock):
        """Un render_draft con el semaforo global y el candado del trabajo -> (out, stats)."""
        from motion import video_io as V
        out = OUTPUT / f"{jid}_draft_{uuid.uuid4().hex[:6]}.mp4"
        got_sem = got_lock = False
        try:
            got_sem = _acquire(_PREVIEW_SEM, ev)
            got_lock = got_sem and _acquire(lock, ev)
            if not got_lock or ev.is_set():
                raise V.Canceled("cancelado")
            # la musica IA pendiente la genera la tarea lateral `music` (cancelable), no un
            # prefetch del motor (el motor lo respeta cuando soporta opts.prefetch_music)
            return out, R.render_draft(plan, out, _motor_opts(
                eo, fmt, shorts=False, prefetch_music=False,
                on_pct=lambda st, p: tick(stage=st, pct=round(float(p), 1)),
                on_step=lambda m: tick(force=True, message=m),
                cancel=ev.is_set))
        except BaseException:
            out.unlink(missing_ok=True)
            raise
        finally:
            if got_lock:
                lock.release()
            if got_sem:
                _PREVIEW_SEM.release()

    def publish(jid, out, stats, t0, tick, keep=None, **extra):
        """Deja la vista previa lista (si esta ejecucion sigue vigente) y borra las viejas.
        `keep`: la que se esta reemplazando sin que el Studio lo pida (actualizacion con la musica
        IA): el Studio puede seguir reproduciendola (pide rangos al hacer seek o repetir), asi
        que queda en `prev_file` hasta la siguiente vista previa."""
        if tick.set(state="done", pct=100, stage="done", file=out.name,
                    prev_file=keep.name if keep is not None else None,
                    t=round(time.time() - t0, 1), duration=round(stats.get("duration", 0), 3),
                    warnings=list(stats.get("warnings") or []), done=True, **extra) is None:
            out.unlink(missing_ok=True)          # ya la reemplazo otra vista previa
            return False
        for old in OUTPUT.glob(f"{jid}_draft_*.mp4"):
            if old != out and old != keep:
                old.unlink(missing_ok=True)
        return True

    def run(jid, ev):
        from motion import video_io as V
        tick = _Throttle(jid, "preview", ev, tok)
        lock = _job_lock(jid)
        t0 = time.time()
        try:
            tick.set(state="running", message="Generando la vista previa real…")
            out, stats = draft(jid, ev, tick, lock)
        except V.Canceled:
            tick.set(state="canceled", message="Cancelada")
            return
        except Exception as e:  # noqa
            traceback.print_exc()
            tick.set(state="error", error=str(e), message="La vista previa falló")
            return
        pending = bool(stats.get("music_pending"))
        msg = ("Vista previa lista (con música provisional: se actualizará sola con la música IA "
               "en cuanto termine de generarse)" if pending else "Vista previa lista")
        if not publish(jid, out, stats, t0, tick, message=msg, music_pending=pending,
                       music_refresh="waiting" if pending else None):
            return
        if pending:
            _refresh_preview_music(jid, ev, tick, plan, fmt, eo, draft, publish, lock, out)

    _start_side(job_id, "preview", run, key=None)
    return {"ok": True, "state": "queued"}


class _SubTick:
    """Progreso del refresco de la vista previa en claves propias (`refresh_pct`): no baja el
    pct ni cambia la etapa de una vista previa que ya esta lista. Sigue atendiendo la
    cancelacion (el _Throttle de abajo lanza Canceled)."""

    def __init__(self, tick):
        self.tick = tick

    def __call__(self, force=False, **kw):
        if "pct" in kw:
            self.tick(force=force, refresh_pct=kw["pct"])
        elif self.tick.ev.is_set():
            from motion import video_io as V
            raise V.Canceled("cancelado")

    def set(self, **kw):
        return self.tick.set(**kw)


PROVISIONAL_MSG = "Vista previa con la música provisional"


def _await_music(jid, ev, info, t0, wait_s):
    """Espera a que la pista IA `info` quede en cache, generada por la tarea lateral `music` (la
    UNICA generacion del trabajo). -> 'ready' | 'superseded' (la edicion cambio y esa pista ya no
    se genera) | 'error'. Lanza Canceled (ev) o TimeoutError."""
    from motion import music as MU
    from motion import video_io as V

    def cached():
        try:
            return not MU.pending(info["cfg"], info["dur"], info["style"], info["seed"])
        except Exception:  # noqa
            return False
    while True:
        if ev.is_set():
            raise V.Canceled("cancelado")
        if cached():
            return "ready"
        run_ = _side_running(jid, "music")
        if run_ is None or run_.get("key") != info["key"] or run_["cancel"].is_set():
            if cached():
                return "ready"
            ms = _music_side(jid)
            if ms.get("key") == info["key"] and ms.get("state") == "error":
                return "error"
            return "superseded"
        if time.time() - t0 > wait_s:
            raise TimeoutError(f"la música IA no terminó en {wait_s:.0f} s")
        ev.wait(0.5)


def _refresh_preview_music(jid, ev, tick, plan, fmt, eo, draft, publish, lock, first=None):
    """El draft sono con la musica procedural porque la IA aun se generaba (stats.music_pending):
    espera a que la tarea lateral `music` (una generacion por trabajo, la de la edicion vigente)
    deje la pista en cache, vuelve a hacer el draft (base, rostro y matte ya en cache) y publica
    el archivo nuevo en la misma tarea `preview` (`preview_file` cambia; `prev_file` = la
    provisional, que NO se borra hasta la siguiente vista previa: el Studio puede estar
    reproduciendola). Cancelable con /cancel?only=preview o con otra vista previa; nunca pisa una
    vista previa mas nueva. Con un export en curso no rehace el draft (music_refresh='skipped').
    Cada salida deja un mensaje veraz (nunca promete una actualizacion que ya no va a pasar)."""
    from motion import video_io as V
    t0 = time.time()
    try:
        info = _music_track(plan, fmt, eo)
        if info is None:
            tick.set(music_refresh="error", music_error="sin música IA para esta edición",
                     message=f"{PROVISIONAL_MSG} (la música IA no está disponible)")
            return
        if info["pending"]:
            _kick_music(jid, plan, fmt, eo, info=info)
            got = _await_music(jid, ev, info, t0, PREVIEW_MUSIC_WAIT_S)
            if got == "superseded":
                tick.set(music_refresh="superseded",
                         message=f"{PROVISIONAL_MSG} (la edición cambió: pide otra vista previa)")
                return
            if got == "error":
                tick.set(music_refresh="error", music_error=_music_side(jid).get("error"),
                         message=f"{PROVISIONAL_MSG} (la música IA no se pudo generar)")
                return
        if ev.is_set():
            raise V.Canceled("cancelado")
        if jid in _EXPORTING:
            tick.set(music_refresh="skipped", music_wait_s=round(time.time() - t0, 1),
                     message=f"{PROVISIONAL_MSG} (la música IA ya está lista y el export la usa)")
            return
        tick.set(music_refresh="rendering", message="Actualizando la vista previa con la música IA…")
        t1 = time.time()
        out, stats = draft(jid, ev, _SubTick(tick), lock)
        pending = bool(stats.get("music_pending"))
        publish(jid, out, stats, t1, tick, keep=first, music_pending=pending,
                music_refresh="done" if not pending else "error",
                music_wait_s=round(t1 - t0, 1),
                message="Vista previa actualizada con la música IA" if not pending else
                f"{PROVISIONAL_MSG} (la música IA no quedó disponible)")
    except V.Canceled:
        tick.set(music_refresh="canceled",
                 message=f"{PROVISIONAL_MSG} (la actualización con la música IA se canceló)")
    except TimeoutError as e:
        tick.set(music_refresh="timeout", music_error=str(e),
                 message=f"{PROVISIONAL_MSG} (la música IA no terminó a tiempo)")
    except Exception as e:  # noqa: refrescar es un extra: la vista previa ya estaba lista
        traceback.print_exc()
        tick.set(music_refresh="error", music_error=str(e)[:300],
                 message=f"{PROVISIONAL_MSG} (la actualización con la música IA falló)")


def _preview_legacy(job_id, plan, body):
    """Render a fast low-res sample of the first seconds so the user sees the real
    look (captions/tracking/blur/zoom) before committing to the full export."""
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
            # PROXY del preview: MISMO look que el export (tracking/zoom/captions/
            # colores/audio/música) a 540p, pero SIN recortar silencios (trim=False)
            # para que sincronice con el timeline original. Los cortes se aplican solo
            # al exportar (y en la reproducción se saltan con JS). Sin hook (solo shorts).
            pipeline.render_from_plan(
                plan, OUTPUT, bool(body.get("dynamic", True)),
                enhance, style, anim, fmt, music, track, mvol,
                False, lang, _custom(body),
                bool(body.get("smart", True)), body.get("platform", "none"),
                bool(body.get("broll", False)), True, str(out), False)
        else:
            pipeline.render_preview(plan, out, secs, enhance, style, anim,
                                    fmt, music, track, mvol, lang, _custom(body),
                                    bool(body.get("smart", True)), start,
                                    body.get("platform", "none"), bool(body.get("broll", False)))
    except Exception as e:  # noqa
        raise HTTPException(500, f"Preview falló: {e}")
    return {"file": out.name}


# ================================================================ motor pro: API 6.1
_FRAME_LOCK = threading.Lock()
_FRAME_SEQ = {}                    # job -> ultimo /frame pedido (para descartar los viejos)


def _jpeg(rgba, quality=88):
    from PIL import Image
    import numpy as np
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgba[..., :3])).save(buf, "JPEG", quality=int(quality))
    return buf.getvalue()


@app.post("/frame/{job_id}")
def frame(job_id: str, body: dict = Body(...)):
    """Cuadro EXACTO del plan en t_final (tiempo final) -> image/jpeg (render.render_frame).
    body: {plan, t_final, fmt, quality: "draft"|"final", drop_stale?}. Corre en el threadpool,
    uno a la vez. Con drop_stale:true (arrastre del cabezal) un pedido que espera turno y ya
    tiene otro mas nuevo del mismo job responde 204 sin renderizar."""
    _job_or_404(job_id)
    _require_license()
    plan = _synced_plan(job_id, _plan_from(body, job_id))
    if not _is_v2(plan):
        raise HTTPException(400, "El cuadro exacto necesita un plan v2 (dirigido).")
    try:
        t = float(body.get("t_final", body.get("t", 0)) or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "t_final inválido")
    q = body.get("quality") if body.get("quality") in ("draft", "final") else "final"
    fmt = _fmt_of(body)
    from motion import render as R
    if fmt not in R.FORMAT_NAMES:
        raise HTTPException(400, f"Formato desconocido: {fmt}.")
    t0 = time.time()
    eo = _eff_engine(plan, fmt, _engine_opts(body))
    if _set_engine(job_id, eo, plan, fmt):
        # otro aire/audio de estudio = otro video base: el analisis de fondo lo prepara
        _kick_analysis(job_id, plan, fmt, eo=eo)
    seq = _FRAME_SEQ[job_id] = _FRAME_SEQ.get(job_id, 0) + 1
    try:
        with _FRAME_LOCK:          # render_frame comparte caches de modulo: uno a la vez
            if body.get("drop_stale") and _FRAME_SEQ.get(job_id) != seq:
                return Response(status_code=204)   # ya hay un cuadro mas nuevo en camino
            rgba = R.render_frame(plan, t, _motor_opts(eo, fmt, quality=q))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa
        traceback.print_exc()
        raise HTTPException(500, f"No se pudo generar el cuadro: {e}")
    data = _jpeg(rgba, body.get("jpeg_quality", 88))
    return Response(data, media_type="image/jpeg",
                    headers={"X-Frame-Ms": str(round((time.time() - t0) * 1000)),
                             "Cache-Control": "no-store"})


# ---- grupos reales de subtitulos (los mismos que dibuja el render) ----
_CAPS_CACHE = {}
_CAPS_LOCK = threading.Lock()


def caption_groups_for(plan, fmt="9:16"):
    """Grupos de subtitulos EXACTOS del render (text.caption_groups sobre las frases en
    tiempo final, en la caja de layout del formato) -> tiempo ORIGINAL del plan:
    [{start, end, text, words: [{text, start, end, kw?}], lines, accent, rect, start_final,
    end_final}]. `words` son las palabras del plan (texto sin mayusculas del preset); `rect`
    = caja del grupo normalizada a la caja de layout 9:16 [x0, y0, x1, y1]. Sin subtitulos
    (apagados o preset none) -> []."""
    from motion import ctx as CX
    from motion import render as R
    pr = R.prepare(plan, {"fmt": fmt, "workers": 0, "quality": "final"})
    job, tl, mods = pr["job"], pr["timeline"], pr["mods"]
    fn = getattr(mods.get("text"), "caption_groups", None)
    if not job.get("captions_on") or not callable(fn):
        return []
    # mismas frases finales que el render (tl.map_words del plan normalizado) + indice original
    src = pr["plan"].get("phrases") or []
    tagged = [[dict(w, _oi=(i, j)) for j, w in enumerate(ph or []) if isinstance(w, dict)]
              for i, ph in enumerate(src)]
    fin = tl.map_words(tagged)
    bx, by, bw, bh = job["layout"]
    ctx = CX.FrameCtx(W=bw, H=bh, fps=job["fps"], quality="final", seed=job["seed"],
                      style=job["style"], words=job.get("words") or [],
                      entities=job.get("entities") or {})
    groups = fn(fin, job["captions"], ctx) or []
    flat = sorted((w for ph in fin for w in ph), key=lambda w: float(w["start"]))
    counts = [len(str(g.get("text") or "").split()) for g in groups]
    firsts = []
    if sum(counts) == len(flat):
        # caso normal: los grupos parten la lista de palabras en orden
        acc = 0
        for c in counts:
            firsts.append(acc)
            acc += c
    else:
        # el preset descarto alguna palabra (p. ej. solo signos): cada grupo empieza 0.06 s
        # antes de su primera palabra (text._caption_groups) -> se ubica por tiempo
        p = 0
        for g in groups:
            t_first = float(g["start"]) + 0.06
            while p < len(flat) and float(flat[p]["start"]) < t_first - 2e-3:
                p += 1
            firsts.append(min(p, len(flat)))
    out = []
    for k, g in enumerate(groups):
        a = firsts[k]
        b = firsts[k + 1] if k + 1 < len(firsts) else len(flat)
        words = []
        for w in flat[a:max(a, b)]:
            i, j = w["_oi"]
            ow = src[i][j]
            d = {"text": ow.get("text", ""), "start": round(float(ow["start"]), 3),
                 "end": round(float(ow["end"]), 3)}
            if ow.get("kw"):
                d["kw"] = True
            words.append(d)
        r = g.get("rect") or (0, 0, 0, 0)
        out.append({"start": round(tl.inverse(float(g["start"])), 3),
                    "end": round(tl.inverse(float(g["end"])), 3),
                    "start_final": round(float(g["start"]), 3), "end_final": round(float(g["end"]), 3),
                    "text": g.get("text", ""), "words": words, "lines": g.get("lines", 1),
                    "accent": g.get("accent") or [],
                    "rect": [round(r[0] / bw, 4), round(r[1] / bh, 4), round(r[2] / bw, 4),
                             round(r[3] / bh, 4)]})
    return out


@app.post("/captions/{job_id}")
def captions(job_id: str, body: dict = Body(default={})):
    """Grupos REALES de subtitulos del plan (los del export) para que la vista en vivo del
    Studio agrupe igual: body {plan?, fmt?} -> [{start, end, words, text, lines, accent, rect,
    start_final, end_final}] en tiempo ORIGINAL (salvo *_final). Cacheado por contenido
    (frases, subtitulos, cortes, estilo, formato); cabecera X-Captions-Ms."""
    _job_or_404(job_id)
    plan = _plan_from(body, job_id)
    if not _is_v2(plan):
        raise HTTPException(400, "Los grupos de subtitulos necesitan un plan v2 (dirigido).")
    fmt = _fmt_of(body)
    from motion import render as R
    if fmt not in R.FORMAT_NAMES:
        raise HTTPException(400, f"Formato desconocido: {fmt}.")
    t0 = time.time()
    fx = plan.get("fx") or {}
    key = _sha(json.dumps([plan.get("video"), plan.get("duration"), plan.get("phrases"),
                           plan.get("cuts"), plan.get("shots"), fx.get("captions"),
                           (plan.get("director") or {}).get("style"), fmt],
                          sort_keys=True, default=str))
    with _CAPS_LOCK:
        groups = _CAPS_CACHE.get(key)
    if groups is None:
        try:
            groups = caption_groups_for(plan, fmt)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # noqa
            traceback.print_exc()
            raise HTTPException(500, f"No se pudieron calcular los subtitulos: {e}")
        with _CAPS_LOCK:
            _CAPS_CACHE[key] = groups
            while len(_CAPS_CACHE) > 32:
                _CAPS_CACHE.pop(next(iter(_CAPS_CACHE)))
    return JSONResponse(groups, headers={"X-Captions-Ms": str(round((time.time() - t0) * 1000)),
                                         "X-Captions-Key": key})


def _keep_src_names(before, out):
    """El director reescribe shots[].src con el video compuesto: se conserva el nombre del
    clip original (compose) cuando las tomas son las mismas."""
    names = [s_.get("src") for s_ in (before or {}).get("shots") or []]
    shots = (out or {}).get("shots") or []
    if names and len(names) == len(shots):
        for s_, name in zip(shots, names):
            if name:
                s_["src"] = name
    return out


def _start_direct_side(job_id, plan, d, use_llm, fmt, base_rev):
    """/direct full en SEGUNDO PLANO (tarea lateral `direct`): progreso por paso del director
    en /status (direct_state/pct/stage/message) y cancelable. El director no se interrumpe a
    medias: si se cancela, su resultado se descarta (el plan guardado no cambia)."""
    plan = json.loads(json.dumps(plan))
    _cancel_side(job_id, "music")            # el director genera y sincroniza su propia pista
    tok = uuid.uuid4().hex[:8]
    jobstore.side_update(job_id, "direct", replace=True, state="running", pct=0, stage="tomas",
                         tok=tok, base_rev=base_rev, message="Dirigiendo la edición…")

    def run(jid, ev):
        tick = _Throttle(jid, "direct", ev, tok)
        t0 = time.time()
        last = [None]

        def dstep(name, pct=0.0, info=""):
            if ev.is_set():
                return
            lo, hi = DIRECTOR_STEPS.get(name, (0.0, 0.0))
            lo, hi = (lo - 70.0) / 29.0 * 100.0, (hi - 70.0) / 29.0 * 100.0   # 0-100 % propio
            label = DIRECTOR_LABELS.get(name, "")
            try:
                tick(force=name != last[0], stage=name, pct=round(lo + (hi - lo) * float(pct or 0), 1),
                     message="Dirigiendo la edición…" + (f" ({label})" if label else ""))
                last[0] = name
            except Exception:  # noqa: cancelado: el director termina y se descarta
                pass
        try:
            # solo pruebas: demora cancelable antes del director (con heuristicas termina en
            # milisegundos y la prueba de cancelacion quedaria en carrera con la respuesta)
            delay = float(os.environ.get("REELFY_TEST_DIRECT_DELAY", "0") or 0)
            if delay > 0 and ev.wait(delay):
                tick.set(state="canceled", message="Cancelado (el plan no cambió)")
                return
            out, warns = _direct_plan(plan, d, on_step=dstep, use_llm=use_llm)
            if ev.is_set():
                tick.set(state="canceled", message="Cancelado (el plan no cambió)")
                return
            _keep_src_names(plan, out)
            jobstore.update(jid, plan=out)
            rev = jobstore.get_light(jid)["plan_rev"]
            tick.set(state="done", pct=100, stage="done", done=True, plan_rev=rev,
                     warnings=warns, t=round(time.time() - t0, 1), message="Edición re-dirigida")
            _kick_analysis(jid, out, fmt)
        except Exception as e:  # noqa
            traceback.print_exc()
            tick.set(state="error", error=str(e), message="No se pudo re-dirigir")

    _start_side(job_id, "direct", run, key=None)
    return {"ok": True, "state": "running", "background": True}


# espera maxima (s) a la musica IA al re-dirigir sin `full` (ver direct())
REDIRECT_MUSIC_WAIT_S = float(os.environ.get("REELFY_REDIRECT_MUSIC_WAIT", "1.5"))


@app.post("/direct/{job_id}")
def direct(job_id: str, body: dict = Body(...)):
    """Re-dirige. Con only_ids: director.redirect (rapido, sin LLM, respeta locked/user);
    con full:true: director.direct completo con LLM; si no, redirect del plan entero.
    Persiste el plan en el job y relanza el analisis de fondo. -> {plan, plan_rev}.
    `background:true` (con full): tarea lateral `direct` con progreso y cancelacion; la
    respuesta es {ok, state} y el plan nuevo llega por /status?rev=N o GET /plan.
    Sin `music` en el cuerpo se conserva la musica que el usuario eligio en el plan."""
    job = _job_or_404(job_id)
    _require_license()
    if job["state"] in ("queued", "running"):
        raise HTTPException(409, "Este proyecto está procesándose; espera a que termine.")
    if _side_running(job_id, "direct"):
        raise HTTPException(409, "El director ya está trabajando en este proyecto.")
    plan = _plan_from(body, job_id)
    from motion import director
    if "profile" in (body or {}):
        # perfil de la persona (null = quitarlo): el director lo re-aplica al re-dirigir; lo
        # bloqueado que mostraba el perfil anterior recibe los datos nuevos
        from motion import profile as PR
        old_prof = PR.normalize_profile(plan.get("profile"))
        plan = _with_profile(plan, body.get("profile"))
        director.retext_locked(plan, old_prof, plan.get("profile"))
    d = _director_params({k: body.get(k) for k in ("style", "intensity", "seed", "music")
                          if body.get(k) is not None} or (plan.get("director") or {}))
    if body.get("music") is None:
        keep = _music_keep(plan)
        if keep:
            d["music"] = keep
        else:
            d.pop("music", None)
    style = body.get("style") if body.get("style") in director.STYLES else None
    intensity = d["intensity"] if body.get("intensity") is not None else None
    seed = d["seed"] if body.get("seed") is not None else None
    only = body.get("only_ids") or None
    full = bool(body.get("full")) or not _is_v2(plan)
    t0 = time.time()
    warns = []
    if full and not only:
        d2 = dict(d)
        d2.update({k: v for k, v in (("style", style), ("intensity", intensity), ("seed", seed))
                   if v is not None})
        if body.get("background"):
            return _start_direct_side(job_id, plan, d2, bool(body.get("use_llm", True)),
                                      _fmt_of(body), job.get("plan_rev"))
    # re-dirigir sin `full` va en la ruta critica del Studio (cambio de estilo, regenerar). Primero
    # SIN sincronia (music_sync=False: el director no espera ni deja un hilo ACE huerfano). Si la
    # pista IA de la propuesta ya esta en cache se repite CON sincronia (< 1 s: la toma del cache);
    # si hay que generarla, la genera la tarea lateral `music` (cancelable, una por trabajo) y al
    # terminar sincroniza la propuesta sola (ver _kick_music). "Re-dirigir todo" (full) sincroniza.
    rkw = {}
    if _accepts(director.redirect, "music_timeout"):
        rkw["music_timeout"] = REDIRECT_MUSIC_WAIT_S
    can_sync = _accepts(director.redirect, "music_sync")
    fmt = _fmt_of(body)
    music_body = d.get("music") if body.get("music") is not None else None
    minfo = None
    try:
        if only:
            out = director.redirect(plan, style, intensity, seed, only_ids=list(only), **rkw)
        elif full:
            _cancel_side(job_id, "music")        # el director genera y sincroniza su pista
            out, warns = _direct_plan(plan, d2, use_llm=bool(body.get("use_llm", True)))
            _keep_src_names(plan, out)
        else:
            if can_sync:
                rkw["music_sync"] = False
            out = director.redirect(plan, style, intensity, seed, **rkw)
            if music_body is not None:
                out = _apply_music(out, music_body)
            if can_sync and _music_sync_ok(music_body) and \
                    not (out.get("director") or {}).get("music_grid"):
                minfo = _music_track(out, fmt)
                if minfo is not None and not minfo["pending"]:
                    rkw.update(music_sync=True,
                               music_timeout=max(REDIRECT_MUSIC_WAIT_S, REDIRECT_CACHED_WAIT_S))
                    out = director.redirect(plan, style, intensity, seed, **rkw)
                    if music_body is not None:
                        out = _apply_music(out, music_body)
                    minfo = None
                elif minfo is not None:
                    dd = out.setdefault("director", {})
                    dd["notes"] = [n for n in dd.get("notes") or [] if not (
                        isinstance(n, str) and n.startswith("musica nueva generandose"))] + [
                        "musica nueva generandose: la edicion se sincroniza sola con sus beats al terminar"]
                    warns.append("La música IA nueva se está generando: la edición se sincroniza sola "
                                  "con sus beats cuando termine.")
    except Exception as e:  # noqa
        traceback.print_exc()
        raise HTTPException(500, f"No se pudo re-dirigir: {e}")
    if music_body is not None and only:
        out = _apply_music(out, music_body)     # (con full, _direct_plan ya la aplico)
    jobstore.update(job_id, plan=out)
    rev = jobstore.get_light(job_id)["plan_rev"]
    if minfo is not None:
        _kick_music(job_id, out, fmt, info=minfo, target={
            "rev": rev, "orig": json.loads(json.dumps(plan)), "style": style,
            "intensity": intensity, "seed": seed, "music": music_body, "fmt": fmt})
    _kick_analysis(job_id, out, fmt)
    return {"plan": out, "plan_rev": rev, "warnings": warns, "ms": round((time.time() - t0) * 1000),
            "music": {"pending": minfo is not None}}


@app.get("/plan/{job_id}")
def get_plan(job_id: str):
    plan, rev = jobstore.get_plan(job_id)
    if plan is None:
        raise HTTPException(404, "Sin plan")
    try:                               # proyectos de antes del proxy: se genera al abrirlos
        j = jobstore.get_light(job_id)
        if j and j.get("state") not in ("queued", "running"):
            _kick_src_proxy(job_id)
    except Exception:  # noqa
        pass
    return {"plan": plan, "plan_rev": rev}


@app.put("/plan/{job_id}")
def put_plan(job_id: str, body: dict = Body(...)):
    """Autoguardado del Studio: persiste el plan editado (plan_rev sube solo si el contenido
    cambio). Con `base_rev` (la revision sobre la que edito el Studio) es concurrencia
    optimista: si otro escritor (re-dirigir, otra pestaña) ya subio la revision -> 409 con la
    revision actual y el autoguardado NO pisa ese plan. No relanza el analisis de fondo (seria
    en cada tecla): para eso esta POST /analysis/{job}."""
    _job_or_404(job_id)
    plan = (body or {}).get("plan")
    if not isinstance(plan, dict) or not plan.get("video"):
        raise HTTPException(400, "Falta el plan.")
    base = body.get("base_rev")
    try:
        base = int(base) if base is not None else None
    except (TypeError, ValueError):
        raise HTTPException(400, "base_rev inválido")
    plan, prof_applied = _reapply_profile(job_id, plan)
    ok, rev = jobstore.update_plan(job_id, plan, base)
    if ok and prof_applied:
        # el perfil cambio en el autoguardado: rotulo, Seguir, reservas y cierre ya van con los
        # datos nuevos (el Studio adopta `plan`)
        if base is not None:
            _music_superseded(job_id, plan, base, rev)
        return {"ok": True, "plan_rev": rev, "plan": plan, "profile_applied": True}
    if not ok:
        # la unica revision posterior es la sincronizacion automatica con la musica IA (el
        # servidor la escribio sobre la propuesta `from_rev`): no es un conflicto real
        ms = _music_side(job_id)
        if ms.get("state") == "synced" and base == ms.get("from_rev") and rev == ms.get("plan_rev"):
            if jobstore.plan_hash(plan) == ms.get("from_hash"):
                return {"ok": True, "plan_rev": rev, "music_synced": True}   # misma propuesta
            ok, rev2 = jobstore.update_plan(job_id, plan, rev)   # la edicion del usuario gana
            if ok:
                _music_superseded(job_id, plan, rev, rev2)
                return {"ok": True, "plan_rev": rev2}
            rev = rev2
        return JSONResponse({"ok": False, "detail": "El plan cambió en el servidor (re-dirigido "
                                                     "u otra ventana); recarga antes de guardar.",
                             "plan_rev": rev}, status_code=409)
    if base is not None:
        _music_superseded(job_id, plan, base, rev)
    return {"ok": True, "plan_rev": rev}


@app.post("/analysis/{job_id}")
def analysis_start(job_id: str, body: dict = Body(default={})):
    """(Re)lanza el analisis de fondo (render.ensure_analysis) del plan dado o guardado."""
    _job_or_404(job_id)
    plan = _plan_from(body, job_id)
    fmt = _fmt_of(body)
    eo = None
    if any(k in (body or {}) for k in ENGINE_KEYS):
        eo = _eff_engine(plan, fmt, _engine_opts(body))
        _set_engine(job_id, eo, plan, fmt)
    started = _kick_analysis(job_id, plan, fmt, force=True, eo=eo)
    _kick_src_proxy(job_id)
    return {"ok": True, "started": started}


_FX_CACHE = {}


def _fx_cached(key, fn):
    if key not in _FX_CACHE:
        _FX_CACHE[key] = fn()
    return _FX_CACHE[key]


@app.get("/fx/schema")
def fx_schema():
    from motion import schema
    return JSONResponse(_fx_cached("schema", schema.schema))


def _fonts():
    from motion import assets
    roles = {}
    for role, names in assets.FONT_ROLES.items():
        for n in names:
            roles.setdefault(n, role)
    out = []
    for name in assets.list_fonts():
        fname, wght = assets.FONT_FILES[name]
        fam = fname.split("[")[0].split("-")[0].split(".")[0]
        weight = int(wght) if wght else (
            900 if "Black" in name else 800 if "ExtraBold" in name else 700 if "Bold" in name
            else 600 if "SemiBold" in name else 500 if "Medium" in name else 400)
        out.append({"name": name, "family": fam, "role": roles.get(name, ""),
                    "file": fname, "weight": weight, "italic": "Italic" in name,
                    "url": f"/fx/font/{name}"})
    return {"fonts": out, "default": assets.DEFAULT_FONT, "roles": assets.FONT_ROLES,
            "license": "SIL OFL 1.1 (Google Fonts)"}


@app.get("/fx/fonts")
def fx_fonts():
    return _fx_cached("fonts", _fonts)


@app.get("/fx/font/{name}")
def fx_font(name: str):
    from motion import assets
    name = Path(name).name
    fname = (assets.FONT_FILES.get(name) or (None,))[0]
    if fname is None and name in {v[0] for v in assets.FONT_FILES.values()}:
        fname = name
    f = assets.FONTS_DIR / fname if fname else None
    if not f or not f.exists():
        raise HTTPException(404, "Fuente no encontrada")
    return FileResponse(f, media_type="font/ttf",
                        headers={"Cache-Control": "public, max-age=86400"})


def icon_svg(name, variant="outline"):
    """SVG (24x24, currentColor) de un icono Tabler a partir de sus nodos."""
    from motion import assets
    from xml.sax.saxutils import quoteattr
    nodes = assets._tabler_nodes(variant).get(name)
    if not nodes and variant == "filled":
        nodes, variant = assets._tabler_nodes("outline").get(name), "outline"
    if not nodes:
        return None
    body = "".join(f"<{tag} " + " ".join(f"{k}={quoteattr(str(v))}" for k, v in attrs.items()
                                          if k != "key") + "/>" for tag, attrs in nodes)
    if variant == "filled":
        head = 'fill="currentColor" stroke="none"'
    else:
        head = ('fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
                'stroke-linejoin="round"')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
            f'{head}>{body}</svg>')


POPULAR_ICONS = ("dental", "map-pin", "clock", "cash", "phone", "calendar", "star", "heart",
                 "check", "sparkles", "link", "brand-instagram", "brand-whatsapp", "user",
                 "shopping-cart", "gift", "bolt", "flame", "trophy", "thumb-up")


@app.get("/fx/icons")
def fx_icons(q: str = "", limit: int = 40, variant: str = "outline", names: str = ""):
    """Iconos Tabler (SVG 24x24 currentColor). `names` = lista exacta separada por comas (p. ej.
    los iconos de marca de las redes del perfil: brand-instagram,brand-tiktok,calendar-event)."""
    from motion import assets
    limit = max(1, min(200, int(limit)))
    if names.strip():
        names = [n.strip() for n in names.split(",") if n.strip() and assets.icon_exists(n.strip())][:limit]
    else:
        names = assets.icon_search(q, limit) if q.strip() else \
            [n for n in POPULAR_ICONS if assets.icon_exists(n)][:limit]
    icons = []
    for n in names:
        svg = icon_svg(n, variant if variant in ("outline", "filled") else "outline")
        if svg:
            icons.append({"name": n, "svg": svg})
    return {"icons": icons, "license": "MIT (Tabler Icons)"}


@app.get("/fx/sfx")
def fx_sfx():
    from motion import assets
    return JSONResponse(assets.sfx_catalog())


@app.get("/fx/sfx/{name}")
def fx_sfx_file(name: str, variant: int = 0):
    from motion import assets
    name = Path(name).stem
    cat = assets.sfx_catalog().get(name)
    if not cat:
        raise HTTPException(404, "SFX no encontrado")
    vs = cat.get("variants") or [cat]
    v = vs[max(0, min(len(vs) - 1, int(variant)))]
    f = assets.SFX_DIR / Path(v.get("file") or "").name
    if not f.exists():
        f = assets.sfx_file(name)
    if not f or not Path(f).exists():
        raise HTTPException(404, "Archivo de SFX no encontrado")
    media = {".flac": "audio/flac", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
             ".wav": "audio/wav"}.get(Path(f).suffix.lower(), "application/octet-stream")
    return FileResponse(f, media_type=media, headers={"Cache-Control": "public, max-age=86400"})


def music_for_plan(plan, seed=None, fmt="9:16", on_pct=None):
    """(Re)genera la musica del plan EXACTAMENTE como la pide el render: la cfg en tiempo final
    sale de render.music_cfg (la misma funcion que usan el precalentamiento y el paso de
    audio), asi que tras un render /music es un acierto de cache y viceversa.
    -> resultado de music.get_music."""
    from motion import music
    from motion import render as R
    pr = R.prepare(plan, {"fmt": fmt, "workers": 0})
    job = pr["job"]
    mcfg, _on = R.music_cfg(pr["plan"], job, pr["timeline"])
    if seed is not None:
        mcfg["seed"] = int(seed)
    if mcfg.get("enabled") is False:  # escuchar en el inspector aunque este apagada en el plan
        mcfg["enabled"] = True
    if mcfg.get("source") == "none":
        mcfg["source"] = "ai"
    return music.get_music(mcfg, job["final_duration"], job["style"], job["seed"], on_pct)


@app.post("/music/{job_id}")
def music_gen(job_id: str, body: dict = Body(default={})):
    """(Re)genera la musica del plan (ACE-Step si esta instalada; si no, procedural) y
    devuelve un archivo escuchable: {file, bpm, drop, ...}. Sincrono (10-40 s con ACE)."""
    _job_or_404(job_id)
    _require_license()
    plan = _plan_from(body, job_id)
    if not _is_v2(plan):
        raise HTTPException(400, "La música necesita un plan v2 (dirigido).")
    t0 = time.time()
    try:
        res = music_for_plan(plan, body.get("seed"), _fmt_of(body))
    except Exception as e:  # noqa
        traceback.print_exc()
        raise HTTPException(500, f"No se pudo generar la música: {e}")
    if not res.get("wav"):
        raise HTTPException(500, f"Sin música: {res.get('reason') or res.get('warnings') or 'sin fuente'}")
    MUSIC_OUT.mkdir(parents=True, exist_ok=True)
    name = f"music_{job_id}_{uuid.uuid4().hex[:6]}{Path(res['wav']).suffix or '.wav'}"
    shutil.copyfile(res["wav"], MUSIC_OUT / name)
    for old in sorted(MUSIC_OUT.glob(f"music_{job_id}_*"), key=lambda p: p.stat().st_mtime)[:-4]:
        old.unlink(missing_ok=True)          # conserva las 4 mas recientes del trabajo
    keep = ("bpm", "drop", "source", "engine", "duration", "keyscale", "seed", "lufs",
            "hook_end", "cached", "warnings", "fallback_from", "prompt")

    def times(v):
        out = []
        for x in v or []:
            try:
                out.append(round(float(x), 3))
            except (TypeError, ValueError):
                continue
        return out
    # beats/downbeats en tiempo FINAL (el de la pista): la pista Musica del Studio los dibuja
    return {"file": name, "url": f"/music-file/{name}", **{k: res.get(k) for k in keep},
            "sections": res.get("sections") or [], "beats": times(res.get("beats")),
            "downbeats": times(res.get("downbeats")), "ms": round((time.time() - t0) * 1000)}


@app.get("/music-file/{name}")
def music_file(name: str):
    f = MUSIC_OUT / Path(name).name
    if not f.exists():
        raise HTTPException(404, "No existe")
    return FileResponse(f, media_type="audio/wav", headers={"Accept-Ranges": "bytes"})


BRAND = paths.BRAND
PRESETS_F = BRAND / "presets.json"


@app.post("/brand/logo")
async def upload_logo(logo: UploadFile):
    if not (logo.content_type or "").startswith("image/"):
        raise HTTPException(400, "Sube una imagen PNG/JPG (idealmente PNG con transparencia).")
    data = await logo.read()
    await run_in_threadpool((BRAND / "logo.png").write_bytes, data)
    return {"ok": True}


@app.get("/brand/logo")
def get_logo():
    """Logo de la marca; 204 (sin cuerpo) si no hay: la consola del Studio queda limpia."""
    f = BRAND / "logo.png"
    if not f.exists():
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return FileResponse(f, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/presets")
def get_presets():
    return json.loads(PRESETS_F.read_text()) if PRESETS_F.exists() else {}


@app.post("/presets")
def save_preset(body: dict = Body(...)):
    name, config = body.get("name", "").strip(), body.get("config", {})
    if not name:
        raise HTTPException(400, "Falta el nombre del preset")
    data = json.loads(PRESETS_F.read_text()) if PRESETS_F.exists() else {}
    data[name] = config
    PRESETS_F.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return {"ok": True, "presets": list(data.keys())}


@app.delete("/presets/{name}")
def delete_preset(name: str):
    data = json.loads(PRESETS_F.read_text()) if PRESETS_F.exists() else {}
    data.pop(name, None)
    PRESETS_F.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return {"ok": True, "presets": list(data.keys())}


# ================================================================ perfil de la persona (3.2)
# Nombre, profesion, negocio y enlaces (redes, reservas, web, WhatsApp...) de quien sale en el
# video: el director los integra (rotulo, Seguir con el @usuario, reservas, tarjeta final).
# Perfiles reutilizables en DATA/assets/brand/profiles.json. Todo `def` (disco / LLM local).
_PROFILES_LOCK = threading.Lock()


def _profiles_load():
    try:
        data = json.loads(paths.PROFILES.read_text()) if paths.PROFILES.exists() else {}
    except Exception:  # noqa: archivo corrupto = sin perfiles (nunca tumba el servidor)
        data = {}
    items = data.get("profiles") if isinstance(data, dict) else None
    return [x for x in items or [] if isinstance(x, dict) and x.get("id")]


def _profiles_save(items):
    paths.PROFILES.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.PROFILES.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps({"version": 1, "profiles": items}, ensure_ascii=False, indent=1))
    os.replace(tmp, paths.PROFILES)


def _profile_id(prof):
    """Id legible y estable: 'dra-ana-ruiz' (+ sufijo si choca)."""
    from motion import profile as PR
    base = prof.get("name") or prof.get("business") or \
        ((PR.primary_social(prof) or {}).get("handle") or "perfil")
    slug = re.sub(r"[^a-z0-9]+", "-", PR.strip_accents(str(base).lower())).strip("-")[:40] or "perfil"
    return slug


def _saved_profile(pid):
    pid = str(pid or "").strip()
    return next((x for x in _profiles_load() if x.get("id") == pid), None) if pid else None


def _profile_param(raw, pid=None):
    """Perfil del cuerpo de /compose o /analyze (dict o JSON en texto) o el guardado `profile_id`
    -> perfil saneado o None. Uno invalido -> 400 (el Studio lo manda armado)."""
    from motion import profile as PR
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except Exception:  # noqa
            raise HTTPException(400, "Perfil inválido (JSON).")
    if raw is None and pid:
        raw = _saved_profile(pid)
        if raw is None:
            raise HTTPException(404, "Perfil guardado no encontrado.")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HTTPException(400, "Perfil inválido.")
    return PR.normalize_profile(raw)


def _profile_key(prof):
    """Huella del perfil (sin id ni fecha de guardado): detecta un cambio real."""
    if not prof:
        return ""
    return json.dumps({k: v for k, v in prof.items() if k not in ("id", "updated")}, sort_keys=True,
                      ensure_ascii=False)


def _reapply_profile(job_id, plan):
    """PUT /plan con `plan.profile` distinto del guardado -> (plan con el perfil re-aplicado por
    director.apply_profile, True): el autoguardado nunca deja un perfil nuevo con el rotulo del
    anterior. Sin la clave `profile` en el plan se conserva la guardada (un cliente viejo no la
    borra); `profile: null` la quita. -> (plan, aplicado)."""
    from motion import profile as PR
    try:
        stored, _rev = jobstore.get_plan(job_id)
    except Exception:  # noqa
        stored = None
    old = PR.normalize_profile((stored or {}).get("profile"))
    if "profile" not in plan:
        if old:
            plan = dict(plan, profile=old)
        return plan, False
    new = PR.normalize_profile(plan.get("profile"))
    if _profile_key(new) == _profile_key(old) or plan.get("version") != 2 or not isinstance(plan.get("fx"), dict):
        return plan, False
    from motion import director
    base = json.loads(json.dumps(plan))
    if old:
        base["profile"] = old
    else:
        base.pop("profile", None)
    try:
        out = director.apply_profile(base, new)
    except Exception:  # noqa: si falla, se guarda tal cual (nunca se pierde la edicion)
        traceback.print_exc()
        return plan, False
    _keep_src_names(plan, out)
    return out, True


def _with_profile(plan, raw):
    """Copia del plan con el perfil puesto (o quitado con None/vacio)."""
    plan = json.loads(json.dumps(plan))
    prof = _profile_param(raw) if raw is not None else None
    if prof:
        plan["profile"] = prof
    else:
        plan.pop("profile", None)
    return plan


@app.post("/profile/parse")
def profile_parse(body: dict = Body(...)):
    """Texto libre -> perfil ({text, use_llm?}): enlaces (URL, @usuario, correo, telefono)
    reconocidos sin IA; nombre, profesion con genero y negocio por reglas + LLM local si esta.
    -> {profile, notes, llm, ms}. No se guarda: el Studio lo muestra para confirmar."""
    from motion import profile as PR
    text = str((body or {}).get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Escribe el texto del perfil.")
    if len(text) > 4000:
        raise HTTPException(400, "El texto es demasiado largo (máximo 4000 caracteres).")
    t0 = time.time()
    use_llm = bool((body or {}).get("use_llm", True)) and _ollama_up()
    r = PR.parse_profile_text(text, use_llm=use_llm)
    if not use_llm and (body or {}).get("use_llm", True):
        r["notes"] = list(r.get("notes") or []) + ["IA local no disponible: nombre y profesión por reglas"]
    return {"profile": r["profile"], "notes": list(dict.fromkeys(r.get("notes") or [])), "llm": r["llm"],
            "ms": round((time.time() - t0) * 1000)}


@app.get("/profile/platforms")
def profile_platforms():
    """Catalogo de plataformas del perfil: [{id, label, icon, kind, color, cta?, svg}]."""
    from motion import profile as PR
    out = []
    for p_ in PR.platforms_catalog():
        out.append(dict(p_, svg=icon_svg(p_["icon"])))
    return {"platforms": out, "kinds": list(PR.KINDS), "use": dict(PR.USE_DEFAULTS)}


@app.get("/profiles")
def profiles_list():
    return {"profiles": _profiles_load()}


@app.post("/profiles")
def profiles_save(body: dict = Body(...)):
    """Guarda un perfil reutilizable -> {ok, id, profile, profiles, replaced}. Con `id` (o
    `profile.id`) de uno guardado lo ACTUALIZA; sin id crea uno nuevo (id legible del nombre, con
    sufijo -2, -3... si ya existe: nunca pisa en silencio)."""
    from motion import profile as PR
    raw = (body or {}).get("profile")
    prof = PR.normalize_profile(raw)
    if not prof:
        raise HTTPException(400, "El perfil está vacío: agrega nombre, profesión o un enlace.")
    with _PROFILES_LOCK:
        items = _profiles_load()
        # solo un `id` EXPLICITO sobrescribe (editar un perfil guardado); sin id, un nombre que ya
        # existe crea otro con sufijo (dos personas homonimas, o guardar de nuevo sin querer
        # pisar): nunca se reemplaza en silencio
        pid = str((body or {}).get("id") or prof.get("id") or "").strip()
        replaced = False
        if re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", pid):
            replaced = any(x.get("id") == pid for x in items)
        else:
            pid = _profile_id(prof)
            if any(x.get("id") == pid for x in items):
                k = 2
                while any(x.get("id") == f"{pid}-{k}" for x in items):
                    k += 1
                pid = f"{pid}-{k}"
        prof["id"] = pid
        prof["updated"] = round(time.time())
        items = [x for x in items if x.get("id") != pid] + [prof]
        items.sort(key=lambda x: -float(x.get("updated") or 0))
        _profiles_save(items[:100])
    return {"ok": True, "id": pid, "profile": prof, "profiles": items[:100], "replaced": replaced}


@app.delete("/profiles/{pid}")
def profiles_delete(pid: str):
    with _PROFILES_LOCK:
        items = _profiles_load()
        if not any(x.get("id") == pid for x in items):
            raise HTTPException(404, "Perfil no encontrado.")
        items = [x for x in items if x.get("id") != pid]
        _profiles_save(items)
    return {"ok": True, "profiles": items}


@app.get("/profile/{job_id}")
def profile_get(job_id: str):
    plan, rev = jobstore.get_plan(job_id)
    if plan is None:
        raise HTTPException(404, "Sin plan")
    return {"profile": plan.get("profile"), "plan_rev": rev}


@app.put("/profile/{job_id}")
def profile_put(job_id: str, body: dict = Body(...)):
    """Pone (o quita con null) el perfil del proyecto y re-dirige AL INSTANTE sin LLM ni
    re-analizar (director.apply_profile: misma revision, el resto de la edicion igual): rotulo,
    Seguir, reservas y cierre se actualizan. {profile, plan?, base_rev?, profile_id?} ->
    {plan, plan_rev, profile, ms}. Con `base_rev` es concurrencia optimista (409 si cambio)."""
    job = _job_or_404(job_id)
    _require_license()
    if job["state"] in ("queued", "running"):
        raise HTTPException(409, "Este proyecto está procesándose; espera a que termine.")
    if _side_running(job_id, "direct"):
        raise HTTPException(409, "El director ya está trabajando en este proyecto.")
    body = body or {}
    if "profile" not in body and not body.get("profile_id"):
        raise HTTPException(400, "Falta el perfil (o null para quitarlo).")
    base = body.get("base_rev")
    try:
        base = int(base) if base is not None else None
    except (TypeError, ValueError):
        raise HTTPException(400, "base_rev inválido")
    plan = _plan_from(body, job_id)
    prof = _profile_param(body.get("profile"), body.get("profile_id"))
    from motion import director
    t0 = time.time()
    try:
        out = director.apply_profile(plan, prof)
    except Exception as e:  # noqa
        traceback.print_exc()
        raise HTTPException(500, f"No se pudo aplicar el perfil: {e}")
    _keep_src_names(plan, out)
    ok, rev = jobstore.update_plan(job_id, out, base)
    if not ok:
        return JSONResponse({"ok": False, "detail": "El plan cambió en el servidor (re-dirigido u otra "
                                                     "ventana); recarga antes de guardar el perfil.",
                             "plan_rev": rev}, status_code=409)
    _kick_analysis(job_id, out, _fmt_of(body))
    return {"ok": True, "plan": out, "plan_rev": rev, "profile": out.get("profile"),
            "ms": round((time.time() - t0) * 1000)}


# capacidades de la API que el Studio puede consultar antes de usarlas
API_FEATURES = ("captions", "source_proxy", "jobs_meta", "music_beats", "translate_texts",
                "translate_titles", "preview_music_refresh", "music_sync", "air_optin", "media",
                "profile")


@app.get("/settings")
def get_settings():
    """Ajustes y capacidades: `pro_opts` = opciones del proyecto que el render pro aplica
    siempre (logo, audio de estudio: la UI habilita esos controles); `pro_opts_optin` = las que
    aplica SOLO si el cuerpo lo pide explicitamente (aire: `air_user: true`); `logo` = hay logo
    de marca; `pro_langs` = idiomas de subtitulos de la edicion IA (/translate); `features`."""
    import broll as broll_mod
    return {"pexels": bool(broll_mod.get_key()), "pro_opts": list(PRO_OPTS),
            "pro_opts_optin": list(PRO_OPTS_OPTIN),
            "logo": (BRAND / "logo.png").exists(),
            "pro_langs": ["es"] + list(pipeline.translate_mod.LANGS),
            "features": list(API_FEATURES)}


@app.post("/settings")
def set_settings(body: dict = Body(...)):
    import broll as broll_mod
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


def _plan_words(plan):
    return [str(w.get("text", "")) for ph in (plan or {}).get("phrases") or [] if isinstance(ph, list)
            for w in ph if isinstance(w, dict)]


def _span_context(plan, a, b):
    """Palabras dichas entre a y b (tiempo original), con 1.5 s de margen."""
    try:
        a, b = float(a) - 1.5, float(b) + 1.5
    except (TypeError, ValueError):
        return None
    ws = [str(w.get("text", "")) for ph in (plan or {}).get("phrases") or [] if isinstance(ph, list)
          for w in ph if isinstance(w, dict) and a <= float(w.get("start", -1)) <= b]
    return " ".join(ws) or None


def _llm_unavailable_msg(err):
    return ("La traducción usa la IA local (Ollama), que no respondió"
            + (f" ({err})" if err else "") +
            f". Revisa que la IA de lenguaje ({paths.LLM_MODEL}) esté instalada y encendida.")


@app.post("/translate/{job_id}")
def translate(job_id: str, body: dict = Body(...)):
    """Traduccion con la IA local para editar EN VIVO en el Studio (no se guarda: el Studio
    decide y la guarda en el plan). body {plan?, lang? ('en' default; en|pt|fr|de|it), texts?,
    spans?}:
      - `texts` [str]: SOLO esos textos (titulos y graficos del plan v2) -> texts_<lang>, 1:1.
        Cada texto conserva su numero de lineas ('\n') y sus MAYUSCULAS; se traduce completo con
        la frase hablada que lo cubre como contexto (`spans` [[start, end]] en tiempo original,
        o la busqueda del titulo en la transcripcion del plan). `untranslated` = indices que
        quedaron iguales al original. Con `phrases: true` tambien traduce los subtitulos.
      - sin `texts`: subtitulos del plan -> phrases_<lang> (mismo timing por frase) y titulos
        de highlights -> titles_<lang>.
    Si la IA no tradujo NADA (Ollama caido o sin modelo) responde 503: nunca devuelve el
    espanol como si fuera la traduccion. `def`: el LLM tarda (5-60 s) y corre en el threadpool."""
    _job_or_404(job_id)
    body = body if isinstance(body, dict) else {}
    lang = str(body.get("lang") or "en").lower()
    tm = pipeline.translate_mod
    if lang != "es" and lang not in tm.LANGS:
        raise HTTPException(400, f"Idioma no soportado: {lang}. Usa es, {', '.join(tm.LANGS)}.")
    texts = body.get("texts")
    if texts is not None and (not isinstance(texts, list) or
                              not all(isinstance(x, str) for x in texts)):
        raise HTTPException(400, "texts debe ser una lista de textos.")
    spans = body.get("spans") if isinstance(body.get("spans"), list) else None
    want_phrases = texts is None or bool(body.get("phrases"))
    plan = body.get("plan") if isinstance(body.get("plan"), dict) else None
    if (want_phrases or texts) and (plan is None or not plan.get("phrases")):
        plan = jobstore.get_plan(job_id)[0] or plan or {}
    out = {"lang": lang}
    t0 = time.time()
    try:
        if texts is not None:
            if lang == "es":
                out["texts_es"], out["untranslated"] = list(texts), []
            else:
                words = _plan_words(plan)
                ctx = []
                for i, t in enumerate(texts):
                    sp = spans[i] if spans and i < len(spans) else None
                    c = _span_context(plan, sp[0], sp[1]) if isinstance(sp, (list, tuple)) and \
                        len(sp) == 2 else None
                    ctx.append(c or tm.spoken_context(t, words))
                tr, miss, err = tm.translate_titles(list(texts), lang, ctx)
                real = [i for i, t in enumerate(texts) if not tm.neutral(t)]
                if real and set(real) <= set(miss):
                    raise HTTPException(503, _llm_unavailable_msg(err) if err else
                                        "La IA local no tradujo ningún texto; intenta de nuevo.")
                out[f"texts_{lang}"], out["untranslated"] = tr, miss
        if want_phrases:
            phrases = (plan or {}).get("phrases") or []
            titles = [h.get("title", "") for h in (plan or {}).get("highlights") or []
                      if isinstance(h, dict)]
            if lang == "es":
                out["phrases_es"], out["titles_es"] = phrases, titles
            else:
                info = {}
                out[f"phrases_{lang}"] = tm.translate_phrases(phrases, lang, info) if phrases else []
                if phrases and len(info.get("untranslated") or []) >= len(phrases):
                    raise HTTPException(503, _llm_unavailable_msg(info.get("error")) if info.get("error")
                                        else "La IA local no tradujo los subtítulos; intenta de nuevo.")
                out["phrases_untranslated"] = info.get("untranslated") or []
                out[f"titles_{lang}"] = tm.translate_texts(titles, lang) if titles else []
    except HTTPException:
        raise
    except Exception as e:  # noqa
        msg = str(e)
        if "11434" in msg or "Connection refused" in msg or "urlopen" in msg:
            msg = _llm_unavailable_msg(None)
        raise HTTPException(500, f"Traducción falló: {msg}")
    out["ms"] = round((time.time() - t0) * 1000)
    return out


_PHASE_SEEN = {}


@app.get("/status/{job_id}")
def status(job_id: str, plan: int | None = None, rev: int | None = None):
    """Estado liviano. El plan completo (pesa) va SOLO si: ?plan=1; o ?rev=N y el plan
    guardado es mas nuevo (plan_rev > N); o, sin parametros (UI legacy), cuando la fase
    cambio desde el ultimo sondeo de este trabajo. ?plan=0 nunca lo manda."""
    st = jobstore.status(job_id)
    if not st:
        raise HTTPException(404, "Job no encontrado")
    if plan is not None:
        want = plan == 1
    elif rev is not None:
        want = st["has_plan"] and rev < st["plan_rev"]
    else:
        last = _PHASE_SEEN.get(job_id)
        want = st["has_plan"] and st["phase"] != last
    if plan is None and rev is None:
        _PHASE_SEEN[job_id] = st["phase"]
        if len(_PHASE_SEEN) > 500:
            _PHASE_SEEN.pop(next(iter(_PHASE_SEEN)))
    if want:
        st["plan"], st["plan_rev"] = jobstore.get_plan(job_id)
    return JSONResponse(st)


# ---- proxy del video del trabajo para el Studio (seeks rapidos) ----
# El video del trabajo (compuesto a 1620x2880 o el 4K subido) tiene GOP largo y mucha
# resolucion: cada seek del Studio decodifica mucho. El proxy es H.264 con el lado corto a 720,
# GOP de ~1/3 s, mismo reloj (el Studio usa el tiempo ORIGINAL del plan), color bt709 (HDR con
# el mismo tonemap que el render) y audio AAC. Se genera en segundo plano al llegar a review
# (tarea lateral `proxy`) y se sirve con GET /source/{job}?proxy=1.
PROXY_SHORT = 720


def _src_proxy_path(job_id):
    return paths.WORK / f"{job_id}_src720.mp4"


def _src_proxy_ok(job_id, src):
    """El proxy existe y corresponde a la fuente actual (tamano y fecha guardados al crearlo)."""
    pv = _src_proxy_path(job_id)
    meta = pv.with_suffix(".json")
    try:
        m = json.loads(meta.read_text())
        st = Path(src).stat()
        return pv.exists() and m.get("size") == st.st_size and m.get("mtime") == int(st.st_mtime)
    except Exception:  # noqa
        return False


def _ff_low(cmd, total_s=None, on_pct=None, cancel=None):
    """ffmpeg con progreso real y cancelacion (como video_io.run_ffmpeg) pero con PRIORIDAD BAJA
    (nice -n 10, como _make_proxy): el proxy corre junto al analisis de fondo al llegar a review
    y no debe quitarle CPU. Si falla con videotoolbox reintenta con libx264."""
    import tempfile
    from motion import video_io as V
    low = ["nice", "-n", "10"] if not paths.IS_WINDOWS and shutil.which("nice") else []

    def once(c):
        full = low + [c[0], "-hide_banner", "-nostdin", "-progress", "pipe:1", "-nostats"] + c[1:]
        with tempfile.TemporaryFile() as err:
            pr = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=err, text=True, bufsize=1)
            try:
                for line in pr.stdout:
                    if cancel and cancel():
                        raise V.Canceled("cancelado")
                    if on_pct and total_s and line.startswith("out_time_us="):
                        try:
                            on_pct(min(100.0, int(line.split("=")[1]) / 1e6 / total_s * 100))
                        except ValueError:
                            continue
                pr.wait()
            except BaseException:
                pr.kill()
                pr.wait()
                raise
            if pr.returncode != 0:
                err.seek(0)
                tail = err.read().decode("utf-8", "replace").strip().splitlines()[-4:]
                raise RuntimeError("ffmpeg fallo: " + " | ".join(tail))
    try:
        once(cmd)
    except RuntimeError:
        if "h264_videotoolbox" not in cmd:
            raise
        once(paths._to_libx264(cmd))


def make_src_proxy(src, dst, on_pct=None, cancel=None):
    """Codifica el proxy de `src` en `dst` (temporal + renombrado: nunca queda a medias). Mismo
    reloj que la fuente: `-enc_time_base demux` conserva el pts EXACTO de cada cuadro (sin eso el
    encoder redondea a 1/fps y el primer cuadro se corria ~12 ms)."""
    from motion import video_io as V
    info = V.probe(src)
    w, h = int(info.get("w") or 0), int(info.get("h") or 0)
    if not w or not h:
        raise RuntimeError("fuente sin video")
    k = min(1.0, PROXY_SHORT / float(min(w, h)))
    W, H = max(2, int(round(w * k / 2)) * 2), max(2, int(round(h * k / 2)) * 2)
    fps = float(info.get("fps") or 30.0) or 30.0
    gop = max(6, int(round(fps / 3)))
    vf = ",".join([f"scale={W}:{H}:flags=bicubic"] + V.tonemap_filters(info))
    dst = Path(dst)
    tmp = dst.with_name(dst.stem + ".part.mp4")
    cmd = ([str(paths.FFMPEG), "-y", "-v", "error"] + V.hwaccel_args() +
           ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
            "-enc_time_base", "demux"] +
           V.encoder_args(W, H, fps, "draft", gop=gop) +
           ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-movflags", "+faststart", str(tmp)])
    try:
        _ff_low(cmd, total_s=float(info.get("duration") or 0) or None, on_pct=on_pct, cancel=cancel)
        tmp.replace(dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return {"W": W, "H": H, "gop": gop}


def _src_sig(src):
    try:
        st = Path(src).stat()
        return {"src_size": st.st_size, "src_mtime": int(st.st_mtime)}
    except OSError:
        return {}


def _src_proxy_failed(job_id, src):
    """El proxy de ESTA fuente (mismo tamano y fecha) ya fallo: no se reintenta en cada pedido."""
    try:
        pv = jobstore.side_get(job_id, "proxy") or {}
    except Exception:  # noqa
        return False
    sig = _src_sig(src)
    return pv.get("state") == "error" and bool(sig) and \
        pv.get("src_size") == sig["src_size"] and pv.get("src_mtime") == sig["src_mtime"]


def _kick_src_proxy(job_id, restart=False):
    """Genera el proxy del trabajo en segundo plano (tarea lateral `proxy`). Una fuente cuyo
    proxy ya fallo no se reintenta salvo con restart=True."""
    job = jobstore.get_light(job_id)
    if not job:
        return False
    src = INPUT / f"{job_id}{job['ext']}"
    if not src.exists() or os.environ.get("REELFY_SRC_PROXY", "1") == "0":
        return False
    if _src_proxy_ok(job_id, src) and not restart:
        return False
    if _side_running(job_id, "proxy"):
        return False
    if not restart and _src_proxy_failed(job_id, src):
        return False
    tok = uuid.uuid4().hex[:8]
    sig = _src_sig(src)
    jobstore.side_update(job_id, "proxy", replace=True, state="queued", pct=0, tok=tok,
                         url=None, message="En cola", **sig)

    def run(jid, ev):
        from motion import video_io as V
        tick = _Throttle(jid, "proxy", ev, tok)
        t0 = time.time()
        dst = _src_proxy_path(jid)
        try:
            with _PROXY_SEM:           # uno a la vez (tambien los proxies de /sources)
                if ev.is_set():
                    raise V.Canceled("cancelado")
                tick.set(state="running", message="Preparando el video para editar…")
                st = src.stat()
                info = make_src_proxy(src, dst, on_pct=lambda p: tick(pct=round(float(p), 1)),
                                      cancel=ev.is_set)
            dst.with_suffix(".json").write_text(json.dumps(
                {"size": st.st_size, "mtime": int(st.st_mtime), **info}))
            tick.set(state="done", pct=100, file=dst.name, url=f"/source/{jid}?proxy=1",
                     t=round(time.time() - t0, 1), W=info["W"], H=info["H"], message="Listo")
        except V.Canceled:
            tick.set(state="canceled", message="Cancelado")
        except Exception as e:  # noqa: el proxy es una optimizacion: el original sigue sirviendo
            traceback.print_exc()
            tick.set(state="error", error=str(e)[:300], message="Sin proxy (se usa el original)")
    return _start_side(job_id, "proxy", run, key="proxy", restart=False)


@app.get("/source/{job_id}")
def source(job_id: str, proxy: int = 0):
    """Video del trabajo para el Studio. `?proxy=1`: el proxy H.264 720p (seeks rapidos, mismo
    reloj) si ya esta; si no, el original y lo genera en segundo plano (cabecera
    X-Reelfy-Proxy: ready|pending|none; `none` = no habra proxy, p. ej. ya fallo para esta
    fuente; el avance en /status `proxy_state/proxy_pct/proxy_url`)."""
    job = _job_or_404(job_id)
    f = INPUT / f"{job_id}{job['ext']}"
    if not f.exists():
        raise HTTPException(404, "No existe")
    hdr = {"Accept-Ranges": "bytes"}
    if proxy:
        if _src_proxy_ok(job_id, f):
            return FileResponse(_src_proxy_path(job_id), media_type="video/mp4",
                                headers={**hdr, "X-Reelfy-Proxy": "ready"})
        _kick_src_proxy(job_id)
        hdr["X-Reelfy-Proxy"] = "pending" if _side_running(job_id, "proxy") else "none"
    return FileResponse(f, media_type="video/mp4", headers=hdr)


# ---- miniatura del proyecto (Proyectos recientes) ----
def _thumb_time(plan):
    """Instante (original) para la miniatura: ~1 s dentro del primer tramo conservado."""
    try:
        from motion import timeline as TL
        tl = TL.Timeline.from_plan(plan)
        if tl.keeps:
            a, b = tl.keeps[0]
            return a + min(1.0, max(0.0, (b - a) * 0.4))
    except Exception:  # noqa
        pass
    return min(1.0, float((plan or {}).get("duration") or 2.0) / 2)


_THUMB_LOCKS = {}


def _thumb_paths(job_id):
    return paths.WORK / f"{job_id}_thumb.jpg", paths.WORK / f"{job_id}_thumb.err"


@app.get("/thumb/{job_id}")
def job_thumb(job_id: str):
    """Miniatura JPEG (alto 360) del video del trabajo; se genera una vez y se cachea. Pedidos
    simultaneos esperan al primero (candado por trabajo); un video que no se pudo decodificar
    deja una marca y responde 404 sin volver a lanzar ffmpeg."""
    job = _job_or_404(job_id)
    out, bad = _thumb_paths(job_id)
    hdr = {"Cache-Control": "max-age=300"}
    if out.exists():
        return FileResponse(out, media_type="image/jpeg", headers=hdr)
    src = INPUT / f"{job_id}{job['ext']}"
    if not src.exists():
        raise HTTPException(404, "Sin video")
    with _SIDE_LOCK:
        lock = _THUMB_LOCKS.setdefault(job_id, threading.Lock())
    with lock:
        if out.exists():                        # lo genero el pedido que tenia el candado
            return FileResponse(out, media_type="image/jpeg", headers=hdr)
        try:
            if bad.exists() and bad.read_text().strip() == json.dumps(_src_sig(src), sort_keys=True):
                raise HTTPException(404, "Sin miniatura")
        except OSError:
            pass
        from motion import video_io as V
        plan, _ = jobstore.get_plan(job_id)
        tmp = out.with_name(f"{out.stem}.{uuid.uuid4().hex[:8]}.part.jpg")
        try:
            t = _thumb_time(plan) if plan else 1.0
            info = V.probe(src)
            vf = ",".join(["scale=-2:360:flags=bicubic"] + V.tonemap_filters(info))
            subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src),
                            "-frames:v", "1", "-vf", vf, "-q:v", "4", str(tmp)],
                           check=True, timeout=60, stderr=subprocess.DEVNULL)
            if not tmp.exists() or tmp.stat().st_size == 0:
                raise RuntimeError("ffmpeg no escribio la miniatura")
            tmp.replace(out)
        except Exception as e:  # noqa
            tmp.unlink(missing_ok=True)
            print(f"   miniatura de {job_id}: {e}")
            try:
                bad.write_text(json.dumps(_src_sig(src), sort_keys=True))
            except OSError:
                pass
            raise HTTPException(404, "Sin miniatura")
    return FileResponse(out, media_type="image/jpeg", headers=hdr)


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


def _hide_main_file():
    """spawn reimporta __main__ en cada proceso hijo si tiene __file__/__spec__. Los hijos del
    compositor no lo necesitan (sus tareas se serializan por referencia a motion.render): se
    ocultan para siempre (las rutas del modulo ya se calcularon)."""
    mod = sys.modules.get("__main__")
    if mod is not None:
        if hasattr(mod, "__file__"):
            del mod.__file__
        mod.__spec__ = None


if __name__ == "__main__":
    import uvicorn
    _hide_main_file()
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("REELFY_PORT", 8000)))
