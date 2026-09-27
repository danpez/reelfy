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
import os
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


def _analysis_keys(plan, fmt):
    """Claves del ANALISIS de fondo -> (base_key, full_key). Solo cuenta lo que lo cambia:
    video + cortes + reencuadre/formato + audio (base y voz), pista de rostro, rangos de matte
    y grade. Editar textos, graficos o SFX (el autoguardado del Studio) NO cambia la clave:
    la vista previa y el export esperan y reutilizan el analisis en curso en vez de
    cancelarlo. Con la misma base_key tambien se espera (base y rostro se reutilizan; lo que
    falte del matte lo calcula quien lo necesita)."""
    fmt = str(fmt or "9:16")
    try:
        from motion import render as R
        fn = getattr(R, "analysis_key", None)          # API publica del motor (si existe)
        if callable(fn):
            k = fn(plan, {"fmt": fmt})
            if isinstance(k, dict) and k.get("base") and k.get("full"):
                return str(k["base"]), str(k["full"])
            if isinstance(k, (tuple, list)) and len(k) == 2:
                return str(k[0]), str(k[1])
        pr = R.prepare(plan, {"fmt": fmt, "workers": 0})
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


def _kick_analysis(job_id, plan, fmt="9:16", force=False):
    """Tras review (y tras re-dirigir): render.ensure_analysis en SEGUNDO PLANO para que
    /frame y la vista previa real sean rapidos. Cancelable; no corre durante un export.
    Clave = _analysis_keys: el mismo analisis en curso no se relanza; uno con la MISMA base
    (p. ej. solo cambio el matte) no se cancela: se encadena al terminar el actual."""
    if not _is_v2(plan):
        return False
    if os.environ.get("REELFY_BG_ANALYSIS", "1") == "0" and not force:
        return False
    fmt = str(fmt or "9:16")
    bkey, key = _analysis_keys(plan, fmt)
    plan = json.loads(json.dumps(plan))
    with _SIDE_LOCK:
        cur = _SIDE.get((job_id, "analysis"))
        if cur and cur["thread"].is_alive() and not cur["cancel"].is_set() and not cur.get("closing"):
            if cur.get("key") == key:
                cur.pop("next", None)                # el pedido vigente ya es este
                return False
            if cur.get("bkey") == bkey:
                cur["next"] = (plan, fmt, bkey, key)   # misma base: encadenar, no cancelar
                return True

    def run(jid, ev):
        from motion import render as R
        from motion import video_io as V
        req = (plan, fmt, bkey, key)
        while req is not None:
            p_, f_, bk_, k_ = req
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
                res = R.ensure_analysis(p_, {
                    "fmt": f_,
                    "on_pct": lambda st, p: tick(stage=st, pct=round(float(p), 1)),
                    "cancel": lambda: ev.is_set() or bool(_EXPORTING)})
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
    _kick_analysis(job_id, plan, d.get("fmt") or "9:16")


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
        _await_same_analysis(job_id, job["plan"], p.get("fmt") or "9:16")
    _EXPORTING.add(job_id)
    try:
        _cancel_side(name="analysis")           # el analisis de fondo no pisa el export
        if p.get("pro"):
            return _render_pro(job_id, job)
        return _render_legacy(job_id, job)
    finally:
        _EXPORTING.discard(job_id)


def _await_same_analysis(job_id, plan, fmt):
    """Si el analisis de fondo de ESTE trabajo corre con la MISMA base (mismo video, cortes,
    reencuadre y formato; los textos/graficos/SFX no cuentan), el export lo espera y reutiliza
    base, rostro y matte de la cache en vez de cancelarlo y repetirlo. Otra base: se cancela."""
    cur = _side_running(job_id, "analysis")
    if not cur:
        return
    bkey, _ = _analysis_keys(plan, fmt)
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

    opts = {"fmt": fmt, "shorts": bool(p.get("shorts", True)), "shorts_dir": str(OUTPUT),
            "on_pct": on_pct, "on_step": on_step, "cancel": prog.cancel.canceled}
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
    """Fuentes subidas (input/sources) que ningun trabajo referencia, tras SOURCE_AGE_S (una
    recien subida puede estar esperando su /compose)."""
    now = now or time.time()
    if not SOURCES.exists():
        return 0
    used = set()
    for params in jobstore.all_params().values():
        spec = (params or {}).get("spec") or {}
        for it in (spec.get("main") or []) + (spec.get("overlays") or []):
            if isinstance(it, dict) and it.get("path"):
                used.add(Path(str(it["path"])).name.split(".")[0].split("_")[0])
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


@app.post("/analyze")
async def analyze(video: UploadFile, glossary: str = Form(""), highlights: int = Form(2),
                  director: str = Form("")):
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
    await run_in_threadpool(jobstore.create, job_id, kind="analyze", ext=ext,
                            params={"glossary": glossary, "n": highlights,
                                    "director": _director_params(director)})
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
           glossary, highlights, director:{style,intensity,seed,music}}. `def`: resuelve
    fuentes en disco y escribe sqlite en el threadpool."""
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
    jobstore.create(job_id, kind="compose", ext=".mp4",
                    params={"spec": spec, "glossary": body.get("glossary", ""),
                            "n": int(body.get("highlights", 2)),
                            "director": _director_params(body.get("director"))})
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
        fmt = _fmt_of(body)
        from motion import render as R
        if fmt not in R.FORMAT_NAMES:
            raise HTTPException(400, f"Formato desconocido: {fmt}. Usa {', '.join(R.FORMAT_NAMES)}.")
        params = {"pro": True, "fmt": fmt, "shorts": bool(body.get("shorts", True))}
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


CANCEL_ONLY = ("preview", "analysis", "direct", "job")


@app.post("/cancel/{job_id}")
def cancel(job_id: str, only: str | None = None):
    """Cancela el trabajo principal (analisis/render) y sus tareas laterales (vista previa
    real, analisis de fondo, re-dirigir en segundo plano). `?only=preview|analysis|direct`
    cancela SOLO esa tarea lateral (cancelar la vista previa no mata un export en curso);
    `?only=job` solo el trabajo principal."""
    _job_or_404(job_id)
    if only and only not in CANCEL_ONLY:
        raise HTTPException(400, f"only debe ser uno de {', '.join(CANCEL_ONLY)}")
    if only and only != "job":
        return {"ok": True, "side": _cancel_side(job_id=job_id, name=only)}
    jobstore.request_cancel(job_id)
    n = 0 if only == "job" else _cancel_side(job_id=job_id)
    return {"ok": True, "side": n}


@app.get("/jobs")
def jobs_recent():
    return jobstore.recent()


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
        return _start_preview_pro(job_id, plan, _fmt_of(body))
    return _preview_legacy(job_id, plan, body)


def _start_preview_pro(job_id, plan, fmt):
    _require_license()
    if not _is_v2(plan):
        raise HTTPException(400, "La vista previa real necesita un plan v2 (dirigido).")
    from motion import render as R
    if fmt not in R.FORMAT_NAMES:
        raise HTTPException(400, f"Formato desconocido: {fmt}.")
    plan = json.loads(json.dumps(plan))
    bkey, key = _analysis_keys(plan, fmt)
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

    def run(jid, ev):
        from motion import video_io as V
        tick = _Throttle(jid, "preview", ev, tok)
        out = OUTPUT / f"{jid}_draft_{uuid.uuid4().hex[:6]}.mp4"
        lock = _job_lock(jid)
        got_sem = got_lock = False
        try:
            got_sem = _acquire(_PREVIEW_SEM, ev)
            got_lock = got_sem and _acquire(lock, ev)
            if not got_lock or ev.is_set():
                raise V.Canceled("cancelado")
            tick.set(state="running", message="Generando la vista previa real…")
            t0 = time.time()
            stats = R.render_draft(plan, out, {
                "fmt": fmt, "shorts": False,
                "on_pct": lambda st, p: tick(stage=st, pct=round(float(p), 1)),
                "on_step": lambda m: tick(force=True, message=m),
                "cancel": ev.is_set})
            if tick.set(state="done", pct=100, stage="done", file=out.name,
                        t=round(time.time() - t0, 1), duration=round(stats.get("duration", 0), 3),
                        warnings=list(stats.get("warnings") or []), done=True,
                        message="Vista previa lista") is None:
                out.unlink(missing_ok=True)          # ya la reemplazo otra vista previa
            else:
                for old in OUTPUT.glob(f"{jid}_draft_*.mp4"):
                    if old != out:
                        old.unlink(missing_ok=True)
        except V.Canceled:
            out.unlink(missing_ok=True)
            tick.set(state="canceled", message="Cancelada")
        except Exception as e:  # noqa
            traceback.print_exc()
            out.unlink(missing_ok=True)
            tick.set(state="error", error=str(e), message="La vista previa falló")
        finally:
            if got_lock:
                lock.release()
            if got_sem:
                _PREVIEW_SEM.release()

    _start_side(job_id, "preview", run, key=None)
    return {"ok": True, "state": "queued"}


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
    plan = _plan_from(body, job_id)
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
    seq = _FRAME_SEQ[job_id] = _FRAME_SEQ.get(job_id, 0) + 1
    try:
        with _FRAME_LOCK:          # render_frame comparte caches de modulo: uno a la vez
            if body.get("drop_stale") and _FRAME_SEQ.get(job_id) != seq:
                return Response(status_code=204)   # ya hay un cuadro mas nuevo en camino
            rgba = R.render_frame(plan, t, {"fmt": fmt, "quality": q})
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa
        traceback.print_exc()
        raise HTTPException(500, f"No se pudo generar el cuadro: {e}")
    data = _jpeg(rgba, body.get("jpeg_quality", 88))
    return Response(data, media_type="image/jpeg",
                    headers={"X-Frame-Ms": str(round((time.time() - t0) * 1000)),
                             "Cache-Control": "no-store"})


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
    try:
        if only:
            out = director.redirect(plan, style, intensity, seed, only_ids=list(only))
        elif full:
            out, warns = _direct_plan(plan, d2, use_llm=bool(body.get("use_llm", True)))
            _keep_src_names(plan, out)
        else:
            out = director.redirect(plan, style, intensity, seed)
    except Exception as e:  # noqa
        traceback.print_exc()
        raise HTTPException(500, f"No se pudo re-dirigir: {e}")
    if body.get("music") is not None and not full:
        out = _apply_music(out, d.get("music"))
    jobstore.update(job_id, plan=out)
    _kick_analysis(job_id, out, _fmt_of(body))
    return {"plan": out, "plan_rev": jobstore.get_light(job_id)["plan_rev"],
            "warnings": warns, "ms": round((time.time() - t0) * 1000)}


@app.get("/plan/{job_id}")
def get_plan(job_id: str):
    plan, rev = jobstore.get_plan(job_id)
    if plan is None:
        raise HTTPException(404, "Sin plan")
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
    ok, rev = jobstore.update_plan(job_id, plan, base)
    if not ok:
        return JSONResponse({"ok": False, "detail": "El plan cambió en el servidor (re-dirigido "
                                                     "u otra ventana); recarga antes de guardar.",
                             "plan_rev": rev}, status_code=409)
    return {"ok": True, "plan_rev": rev}


@app.post("/analysis/{job_id}")
def analysis_start(job_id: str, body: dict = Body(default={})):
    """(Re)lanza el analisis de fondo (render.ensure_analysis) del plan dado o guardado."""
    _job_or_404(job_id)
    plan = _plan_from(body, job_id)
    started = _kick_analysis(job_id, plan, _fmt_of(body), force=True)
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
def fx_icons(q: str = "", limit: int = 40, variant: str = "outline"):
    from motion import assets
    limit = max(1, min(200, int(limit)))
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
    return {"file": name, "url": f"/music-file/{name}", **{k: res.get(k) for k in keep},
            "sections": res.get("sections") or [], "ms": round((time.time() - t0) * 1000)}


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
    f = BRAND / "logo.png"
    if not f.exists():
        raise HTTPException(404, "Sin logo")
    return FileResponse(f, media_type="image/png")


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


@app.get("/settings")
def get_settings():
    import broll as broll_mod
    return {"pexels": bool(broll_mod.get_key())}


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


@app.post("/translate/{job_id}")
def translate(job_id: str, body: dict = Body(...)):
    """Translate captions + short titles to EN for LIVE preview/editing in the studio.
    The (possibly edited) EN phrases are then sent back inside the plan at export."""
    _job_or_404(job_id)
    plan = body.get("plan") or jobstore.get_plan(job_id)[0]
    try:
        phrases_en = pipeline.translate_mod.translate_phrases(plan["phrases"])
        titles = [h.get("title", "") for h in plan.get("highlights", [])]
        titles_en = pipeline.translate_mod.translate_texts(titles)
    except Exception as e:  # noqa
        msg = str(e)
        if "11434" in msg or "Connection refused" in msg or "urlopen" in msg:
            msg = ("La traducción usa la IA local (Ollama), que no está disponible. "
                   f"Revisa que la IA de lenguaje ({paths.LLM_MODEL}) esté instalada.")
        raise HTTPException(500, f"Traducción falló: {msg}")
    return {"phrases_en": phrases_en, "titles_en": titles_en}


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


@app.get("/source/{job_id}")
def source(job_id: str):
    job = _job_or_404(job_id)
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
