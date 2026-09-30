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
from contextlib import asynccontextmanager
import hashlib
import io
import json
import math
import os
import re
import shutil
import signal
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

ENGINE_VERSION = "0.5.4"

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
OLLAMA_PID_F = paths.DATA / "ollama.pid"


def _pids_listening(port: int) -> list[int]:
    """PIDs que escuchan en 127.0.0.1:`port` (lsof en Mac/Linux, netstat en Windows)."""
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                                 timeout=5).stdout
            return sorted({int(c[-1]) for c in (ln.split() for ln in out.splitlines())
                           if len(c) >= 5 and c[3].upper() == "LISTENING"
                           and c[1].rsplit(":", 1)[-1] == str(port) and c[-1].isdigit()})
        out = subprocess.run(["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"], capture_output=True,
                             text=True, timeout=5).stdout
        return sorted({int(x) for x in out.split() if x.isdigit()})
    except Exception:  # noqa: sin lsof/netstat -> nada que limpiar
        return []


def _proc_cmd(pid: int) -> str:
    """Linea de comando (Mac/Linux) o nombre de imagen (Windows) de un PID ('' si no existe)."""
    try:
        if os.name == "nt":
            return subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                  capture_output=True, text=True, timeout=5).stdout
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except Exception:  # noqa
        return ""


def _is_our_ollama(cmd: str) -> bool:
    c = cmd.replace("\\", "/").lower()
    return "ollama-runtime" in c or (os.name == "nt" and "ollama.exe" in c)


def _stop_pid(pid: int, wait: float = 3.0) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
            return
        os.kill(pid, signal.SIGTERM)
        for _ in range(int(wait * 10)):
            time.sleep(0.1)
            os.kill(pid, 0)
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _kill_stale_ollama() -> list[int]:
    """Un ollama EMBEBIDO de una sesion anterior que quedo vivo (la app se cerro sin apagarlo o el
    motor murio de golpe) ocupa el puerto y respondia con binario y modelos viejos. Se termina el de
    la pidfile y cualquier 'ollama-runtime' escuchando en OLLAMA_PORT; nunca el ollama del sistema
    (otro binario y otro puerto). -> PIDs terminados."""
    cand = set(_pids_listening(OLLAMA_PORT))
    try:
        cand.add(int(OLLAMA_PID_F.read_text().strip()))
    except (OSError, ValueError):
        pass
    done = []
    for pid in sorted(cand):
        if pid != os.getpid() and _is_our_ollama(_proc_cmd(pid)):
            _stop_pid(pid)
            done.append(pid)
    if done:
        print(f"ollama huerfano de una sesion anterior terminado: {done}")
    return done


def _stop_ollama():
    p = _ollama_proc
    if p is not None and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:  # noqa
            p.kill()
    try:
        OLLAMA_PID_F.unlink()
    except OSError:
        pass


# uvicorn (>=0.29) vuelve a lanzar SIGTERM/SIGINT al terminar su apagado ordenado: el proceso
# muere por la senal y los atexit NO corren (por eso el ollama quedaba huerfano aunque la app
# mandara SIGTERM). El apagado de la app (lifespan) si corre antes: ahi se apaga el ollama.
_SERVING = False     # True solo en el servidor real (no al importarlo en pruebas)


def _descendants(pid: int) -> list[int]:
    """PIDs de los hijos, nietos... de `pid` (pgrep -P, macOS y Linux)."""
    out, todo = [], [pid]
    while todo:
        try:
            r = subprocess.run(["pgrep", "-P", str(todo.pop())], capture_output=True, text=True, timeout=5)
        except Exception:  # noqa: sin pgrep no hay a quien buscar
            break
        kids = [int(x) for x in r.stdout.split() if x.isdigit()]
        out += kids
        todo += kids
    return out


def _kill_children(wait: float = 1.5) -> list[int]:
    """Al apagar el motor termina lo que lanzo y sigue vivo (ace-synth generando musica, el
    ffmpeg de un proxy, workers de un export): antes el motor salia y esos procesos quedaban
    huerfanos comiendo CPU. SIGTERM y, a quien no salga en `wait` s, SIGKILL. -> PIDs."""
    if os.name == "nt":
        return []
    kids = _descendants(os.getpid())
    for k in kids:
        try:
            os.kill(k, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    t = time.time() + wait
    while kids and time.time() < t:
        time.sleep(0.1)
        kids = [k for k in kids if _proc_cmd(k)]          # los que siguen (sin zombis ya recogidos)
    for k in kids:
        try:
            os.kill(k, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return kids


# apagandose: los hilos que esperan turno (proxies de /sources, transcripcion por clip) ya no lanzan
# procesos nuevos
_SHUTDOWN = threading.Event()


@asynccontextmanager
async def _lifespan(_app):
    yield
    _stop_ollama()
    if _SERVING:
        _SHUTDOWN.set()
        _kill_children()
        # el proceso recien terminado de un hilo (ffmpeg de un proxy, whisper) se reintenta una vez
        # (libx264 / CPU) al fallar: una segunda pasada tambien barre ese reintento
        _kill_children(wait=0.5)


app.router.lifespan_context = _lifespan


def _start_ollama():
    global _ollama_proc
    if not BUNDLED_OLLAMA:
        return
    _kill_stale_ollama()
    env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{OLLAMA_PORT}",
               OLLAMA_MODELS=str(paths.OLLAMA_MODELS))
    try:
        _ollama_proc = subprocess.Popen([paths.OLLAMA_BIN, "serve"], env=env,
                                        stdout=_logf, stderr=_logf)
        try:
            OLLAMA_PID_F.write_text(str(_ollama_proc.pid))
        except OSError:
            pass
        atexit.register(_stop_ollama)
    except Exception as e:  # noqa
        print(f"ollama no arrancó: {e}")


def _parent_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32                                          # type: ignore[attr-defined]
        h = k.OpenProcess(0x00100000, False, pid)                           # SYNCHRONIZE
        if not h:
            return False
        try:
            return k.WaitForSingleObject(h, 0) != 0                          # 0 = ya termino
        finally:
            k.CloseHandle(h)
    if os.getppid() != pid:                                                 # re-emparentado: murio
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _watch_parent(pid: int, on_orphan, every: float = 2.0) -> threading.Thread:
    """Si la app de escritorio (REELFY_PARENT_PID) muere sin apagar al motor, el motor se apaga
    solo y con el su ollama: nada queda corriendo ni ocupando puertos."""
    def loop():
        while _parent_alive(pid):
            time.sleep(every)
        print("la app de escritorio ya no existe: apagando el motor")
        on_orphan()
    th = threading.Thread(target=loop, name="reelfy-parent-watch", daemon=True)
    th.start()
    return th


def _orphan_exit():
    _SHUTDOWN.set()
    _stop_ollama()
    _kill_children()
    os._exit(0)


if os.environ.get("REELFY_PARENT_PID", "").isdigit() and not _CHILD:
    _watch_parent(int(os.environ["REELFY_PARENT_PID"]), _orphan_exit)


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
    out = _sym(_accent_keep(plan, _apply_music(out, mover)))
    print(f"   director: {d['style']} {d['intensity']} seed {d['seed']} en {time.time() - t0:.1f}s "
          f"(llm={dd.get('llm')}, vlm={dd.get('vlm')}, music_sync={kw.get('music_sync', 'auto')}, "
          f"timings={dd.get('timings')})")
    return out, warns


def _is_v2(plan):
    return isinstance(plan, dict) and plan.get("version") == 2 and isinstance(plan.get("fx"), dict)


# ---- simetria (regla de la marca): todo overlay deja el mismo espacio a izquierda y derecha.
# director.symmetrize(plan) -> plan (puro, sin LLM, conserva ids/tiempos/contenido) centra o
# espeja lo que puso la IA (nunca lo del usuario ni lo bloqueado). Los planes de un director
# anterior a la regla se simetrizan UNA vez al abrirlos (director.sym = 1) y todo lo que sale
# del director (dirigir, re-dirigir, sincronizar con la musica, aplicar el perfil) pasa por el.
SYM_VERSION = 1


def _needs_sym(plan):
    return _is_v2(plan) and not ((plan.get("director") or {}).get("sym") or 0) >= SYM_VERSION


def _sym(plan):
    """Plan v2 -> copia simetrizada con director.sym marcado. Nunca lanza: sin
    director.symmetrize (motor viejo) o si falla, devuelve el plan tal cual."""
    if not _is_v2(plan):
        return plan
    try:
        from motion import director
        fn = getattr(director, "symmetrize", None)
        if fn is None:
            return _accent_apply(plan)
        out = fn(json.loads(json.dumps(plan)))
    except Exception:  # noqa: nunca se pierde el plan por la simetria
        traceback.print_exc()
        return _accent_apply(plan)
    if not _is_v2(out):
        return _accent_apply(plan)
    d = out.get("director")
    if not isinstance(d, dict):
        d = out["director"] = {}
    d["sym"] = SYM_VERSION
    return _accent_apply(out)


# ---- color de acento del proyecto (plan.fx.accent): la palabra activa de los subtitulos y el acento de
# titulos y tarjetas. Sin acento propio todo queda como siempre (amarillo #FFD23F; nunca un color de
# marca fijo). Con acento propio, lo que la IA pinto con el acento de su estilo (o el amarillo de
# siempre) pasa al del proyecto: al dirigir, re-dirigir o regenerar tambien (todo pasa por _sym).
# Un acento blanco/negro/gris (estilo Minimal) no se busca en los items. = fxcore.applyAccent.
DEFAULT_ACCENT = "#FFD23F"
_ACCENT_KEYS = ("accent", "color", "badge_color", "bg", "box", "highlight", "button_color", "border_color",
                "arrow_color")


def _hex(v):
    s = str(v or "").strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", s)
    if m:
        return "#" + m.group(1).upper()
    m = re.fullmatch(r"#?([0-9a-fA-F]{3})", s)
    return "#" + "".join(c * 2 for c in m.group(1)).upper() if m else None


def _chromatic(h):
    h = _hex(h)
    if not h:
        return False
    n = int(h[1:], 16)
    rgb = (n >> 16 & 255, n >> 8 & 255, n & 255)
    return max(rgb) - min(rgb) >= 40


def _accent_keep(src, out):
    """El acento del proyecto sobrevive a lo que devuelve el director (lo copia de `src` si falta)."""
    acc = _hex(((src or {}).get("fx") or {}).get("accent")) if isinstance(src, dict) else None
    if acc and _is_v2(out) and not _hex(out["fx"].get("accent")):
        out["fx"]["accent"] = acc
    return out


def _accent_apply(plan):
    """Plan v2 con acento propio -> el mismo plan (en sitio) con ese acento en subtitulos y en lo que
    la IA pinto con el acento de su estilo. Nunca lanza."""
    try:
        if not _is_v2(plan):
            return plan
        fx = plan["fx"]
        acc = _hex(fx.get("accent"))
        if not acc:
            return plan
        try:
            from motion import assets as A
            pal = A.palette((plan.get("director") or {}).get("style")).get("accent")
        except Exception:  # noqa: sin paleta, solo el amarillo de siempre
            pal = None
        olds = {x for x in (_hex(pal), DEFAULT_ACCENT) if x and _chromatic(x) and x != acc}

        def walk(x, depth=0):
            if depth > 4 or not isinstance(x, (dict, list)):
                return
            if isinstance(x, list):
                for y in x:
                    walk(y, depth + 1)
                return
            for k in _ACCENT_KEYS:
                if isinstance(x.get(k), str) and _hex(x[k]) in olds:
                    x[k] = acc
            for k in ("lines", "text", "card", "graphics", "items"):
                if isinstance(x.get(k), (dict, list)):
                    walk(x[k], depth + 1)
        # solo lo de la IA: lo bloqueado o lo del usuario conserva su color (un titulo que el usuario
        # dejo amarillo a proposito sigue amarillo al dirigir, re-dirigir o regenerar otra cosa)
        def ai(it):
            return isinstance(it, dict) and not it.get("locked") and it.get("src") != "user"
        for tk in ("text", "graphics", "cutaways"):
            for it in fx.get(tk) or []:
                if ai(it):
                    walk(it)
        for it in fx.get("text") or []:
            if ai(it) and it.get("accent_words") and not it.get("accent"):
                it["accent"] = acc
        caps = fx.get("captions") if isinstance(fx.get("captions"), dict) else {}
        if isinstance(caps.get("highlight"), str) and _hex(caps["highlight"]) in olds:
            caps["highlight"] = acc
        caps["accent"] = acc
        fx["captions"] = caps
    except Exception:  # noqa: el acento nunca tumba un plan
        traceback.print_exc()
    return plan


def _sym_stored(job_id, plan, rev):
    """Plan guardado de un director anterior a la regla de simetria (sin director.sym): se
    simetriza una vez y se guarda con concurrencia optimista (plan_rev sube: el Studio adopta
    la version nueva al cargarla). Con el trabajo ocupado (export, analisis, director) se deja
    para la siguiente carga. -> (plan, plan_rev)."""
    if not _needs_sym(plan):
        return plan, rev
    try:
        j = jobstore.get_light(job_id)
        if not j or j.get("state") in ("queued", "running") or _side_running(job_id, "direct"):
            return plan, rev
        out = _sym(plan)
        if out is plan:
            return plan, rev
        ok, rev2 = jobstore.update_plan(job_id, out, rev)
        if ok:
            print(f"   simetria: plan de {job_id} simetrizado (rev {rev} -> {rev2})")
            return out, rev2
        return jobstore.get_plan(job_id)        # otro escritor gano: se sirve lo guardado
    except Exception:  # noqa
        traceback.print_exc()
        return plan, rev


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


MONTAGE_PENDING_MSG = "Este proyecto todavía es un montaje: aplícalo para componer el video."


def _not_montage(plan):
    """Un plan que SOLO es un montaje (fase assembly, sin video compuesto ni analisis) no se
    previsualiza, dirige ni exporta: 409."""
    if jobstore.montage_only(plan):
        raise HTTPException(409, MONTAGE_PENDING_MSG)
    return plan


def _plan_from(body, job_id):
    plan = (body or {}).get("plan")
    if not plan:
        plan, _ = jobstore.get_plan(job_id)
    if not plan:
        raise HTTPException(409, "El job no tiene plan; analiza el video primero.")
    return _media_paths(_not_montage(plan))


def _media_paths(plan):
    """Recursos (plan.fx.media: imagen o video como tarjeta flotante) con la ruta de su archivo en
    este equipo: el Studio solo conoce el `sid` (uno agregado o reemplazado ahi no trae `path`). Sin
    archivo -> path None (el motor no lo dibuja). En sitio; nunca lanza."""
    try:
        fx = plan.get("fx") if isinstance(plan, dict) else None
        for it in (fx or {}).get("media") or []:
            if not isinstance(it, dict) or not it.get("sid"):
                continue
            src = _resolve_source(it["sid"])
            it["path"] = str(src) if src else None
            if src and it.get("kind") not in ("image", "video"):
                it["kind"] = "image" if _is_image(src) else "video"
    except Exception:  # noqa: sin rutas el recurso no sale, el resto del plan si
        traceback.print_exc()
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
                _claim_cache(jid, res)                     # base/voz de este proyecto
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
    out = _sym(_accent_keep(tgt["orig"], out))
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
# claves del montaje en plan.shots que sobreviven al director (compose.SHOT_KEYS)
SHOT_MONTAGE_KEYS = ("join", "slot", "cand", "conf", "setup", "sid")


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
        # perfil de la persona (nombre, profesion, redes): el director lo integra en la edicion,
        # con la foto/logo de sus enlaces que ya bajaron mientras se analizaba (lo que llegue
        # despues lo pone _lm_propagate al quedar en review)
        try:
            plan["profile"] = _lm_ready(params["profile"]) or params["profile"]
        except Exception:  # noqa
            traceback.print_exc()
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
        # lo que el montaje sabe de cada toma (union, setup de camara, parte del guion)
        carry = [{k: s[k] for k in SHOT_MONTAGE_KEYS if s.get(k) is not None}
                 for s in plan.get("shots") or []]
        try:
            plan, warns = _direct_plan(plan, d, on_step=dstep)
        finally:
            bar.stop_creep()
        shots = plan.get("shots") or []
        if src_names and len(src_names) == len(shots):
            for s, name, extra in zip(shots, src_names, carry):   # nombre del clip original (compose)
                if name:
                    s["src"] = name
                for k, v in extra.items():
                    s.setdefault(k, v)
        prog.cancel.check()
    if d.get("fmt") in ("9:16", "1:1", "4:5", "16:9", "original") and isinstance(plan, dict) and \
            not (plan.get("studio") or {}).get("fmt"):
        # formato con el que nace el proyecto (el analisis de fondo prepara el video base en el): el
        # Studio lo abre asi, sin re-analizar; un plan sin plan.studio.fmt ni plan.takes es de antes
        # del formato por proyecto y se queda en 9:16
        plan["studio"] = dict(plan.get("studio") or {}, fmt=d["fmt"])
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
    {spec, glossary, n, director, script?, sids?, montage_job?}. La IA se aplica sobre la
    composición (timeline + IA). Los limites REALES de cada clip quedan en plan.shots (director y
    render). Montaje de "Armar con IA" (el MISMO trabajo, antes kind "montage"): el plan guardado
    trae plan.montage; compose.finish_plan lo mapea a plan.shots (join/setup de cada union), las
    tomas del video combinado no cruzan uniones y las secciones salen del guion. Los nombres
    propios y cifras del guion van al glosario de whisper (nunca el guion entero)."""
    import re
    import compose as compose_mod
    _refresh_llm()
    prog = Progress(job_id)
    bar = _PhaseBar(job_id, prog)
    p = job["params"] or {}
    spec = p.get("spec") or {}
    video = INPUT / f"{job_id}.mp4"
    lo, hi = PHASE_COMPOSE
    stored = job.get("plan")
    montage = stored["montage"] if jobstore.montage_only(stored) else None
    gloss = _script_glossary(((p.get("script") or {}).get("text") or ""), p.get("glossary", ""))

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
    video.unlink(missing_ok=True)       # un reintento nunca reescribe un enlace duro compartido
    compose_mod.compose(spec, str(video), paths.WORK / f"compose_{job_id}", on_step=cstep,
                        info=info)
    shutil.rmtree(paths.WORK / f"compose_{job_id}", ignore_errors=True)   # segmentos 4K
    # el mismo montaje de otro proyecto (p. ej. el mismo video importado otra vez): un enlace
    twin = dedupe_import(video, full=True)
    how = "video copiado sin re-codificar" if info.get("copied") else \
        f"{(info.get('bitrate') or 0) / 1e6:.1f} Mbps"
    print(f"   compose: {len(spec.get('main') or [])} clips en {time.time() - t0:.1f}s "
          f"(lienzo {info.get('W')}x{info.get('H')}, {how}"
          f"{', igual a ' + twin.name if twin else ''}, carga {_load()})")
    kw = {}
    joins = _join_times(info.get("shots"))
    if joins and _accepts(pipeline.analyze, "joins"):
        kw["joins"] = joins                  # la limpieza de tomas no cruza uniones del montaje
    plan = pipeline.analyze(video, gloss, p.get("n", 2),
                            on_step=_analysis_progress(bar, *PHASE_ANALYZE_AFTER_COMPOSE), **kw)
    if hasattr(compose_mod, "finish_plan"):
        compose_mod.finish_plan(plan, info, spec=spec, montage=montage)
    else:                                    # compose sin montaje (version anterior)
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
        if montage is not None:
            plan["montage"] = montage
    _media_off(plan, montage)
    if isinstance(plan.get("montage"), dict) and p.get("script"):
        sc = plan["montage"].setdefault("script", {})
        if isinstance(sc, dict) and not sc.get("text"):
            sc.update(text=p["script"].get("text"), source=p["script"].get("source") or "user")
    _finish_analysis(job_id, prog, plan, p, bar)


def _media_off(plan, montage):
    """Los recursos que el usuario QUITO en el ensamble (insert "enabled": false) llegan a plan.fx.media
    ocultos (no se ven en el video; el Studio los muestra apagados para volver a ponerlos)."""
    try:
        off = {str(x.get("id")) for x in (montage or {}).get("inserts") or []
               if isinstance(x, dict) and x.get("enabled") is False}
        fx = plan.get("fx") if isinstance(plan, dict) else None
        for it in (fx or {}).get("media") or []:
            if isinstance(it, dict) and str(it.get("id")) in off:
                it["enabled"] = False
    except Exception:  # noqa: un recurso visible de mas nunca tumba la composicion
        traceback.print_exc()


def _join_times(shots):
    """Uniones del montaje (s del video compuesto) de info.shots -> [t] (takes.plan_joins)."""
    try:
        import takes as takes_mod
        fn = getattr(takes_mod, "plan_joins", None)
        return fn(shots or []) if fn else []
    except Exception:  # noqa: sin uniones la limpieza de tomas es la de siempre
        traceback.print_exc()
        return []


# ================================================================ montaje con IA ("Armar con IA")
# POST /montage {sids, script?, glossary?, intercut?, profile?, director?} crea un trabajo kind
# "montage" en fase "assembly" (carril propio: no espera detras de un export). Espera la
# transcripcion de cada clip (carril de palabras, ver _words_kick), llama a montage.build y guarda
# el resultado en plan.montage (contrato: version, order, script, slots, timeline, discarded,
# uncovered, extra, stats, ms). El trabajo queda en review/assembly: la pantalla de ensamble lo
# muestra, el usuario cambia tomas ("Usar esta toma") y POST /compose {montage_job} re-encola el
# MISMO id como compose (conserva plan.montage y el guion). Cancelado o fallido con un montaje
# guardado: vuelve a assembly (jobstore.back_to_assembly).
PHASE_MONTAGE_WORDS = (2.0, 60.0)
PHASE_MONTAGE_BUILD = (60.0, 98.0)
MONTAGE_MAX_CLIPS = int(os.environ.get("REELFY_MONTAGE_MAX_CLIPS", "300"))
SCRIPT_MAX_CHARS = 20000
MONTAGE_DONE_MSG = "Montaje listo: revisa las tomas"


def _montage(job_id, job):
    """Handler 'montage' (carril propio). job['params'] = {sids, script, glossary, intercut,
    director?, profile?}."""
    prog = Progress(job_id)
    bar = _PhaseBar(job_id, prog)
    p = job["params"] or {}
    sids = [s for s in p.get("sids") or [] if isinstance(s, str)]
    t0 = time.time()
    lo, hi = PHASE_MONTAGE_WORDS
    bar.set(lo, "Transcribiendo tus clips…", "words", force=True)
    _montage_wait_words(job_id, sids, bar, prog)
    t_words = time.time() - t0
    clips = _montage_clips(sids)
    if not clips:
        raise RuntimeError("Ningún clip de la sesión se pudo leer.")
    assets = [a for a in (_asset_item(x) for x in p.get("assets") or [] if isinstance(x, str)) if a]
    lo, hi = PHASE_MONTAGE_BUILD
    bar.set(lo, "Eligiendo las mejores tomas…", "montage", force=True)

    def step(pct=0.0, msg=None):
        bar.set(lo + (hi - lo) * max(0.0, min(100.0, float(pct or 0))) / 100.0,
                msg or "Eligiendo las mejores tomas…", "montage", force=True)
    sc = p.get("script") if isinstance(p.get("script"), dict) else None
    t1 = time.time()
    _refresh_llm()
    bar.creep(lo, hi, 2.0 + 0.4 * len(clips))      # el motor no reporta avance: la barra camina
    try:
        res = _montage_build(clips, script=(sc or {}).get("text") or None,
                             glossary=p.get("glossary") or "", intercut=p.get("intercut", True) is not False,
                             on_step=step, cancel=prog.cancel.canceled, llm=p.get("llm", True) is not False,
                             **({"assets": assets} if _accepts(_montage_build, "assets") else {}))
    finally:
        bar.stop_creep()
    prog.cancel.check()
    m = _montage_result(res, clips, p, time.time() - t1, assets=assets)
    m.setdefault("stats", {})
    if isinstance(m["stats"], dict):
        m["stats"].setdefault("words_wait_s", round(t_words, 1))
    n_ok = len(m.get("timeline") or [])
    msg = MONTAGE_DONE_MSG if n_ok else "No encontré tomas utilizables en estos clips"
    jobstore.update(job_id, state="review", phase="assembly", pct=100, stage="assembly", eta=None,
                    elapsed=round(time.time() - t0), message=msg, plan={"montage": m}, error=None)
    print(f"   montaje {job_id}: {len(clips)} clips, {len(m.get('slots') or [])} partes, "
          f"{n_ok} segmentos en {time.time() - t0:.1f}s (palabras {t_words:.1f}s)")


def _montage_wait_words(job_id, sids, bar, prog):
    """Espera la transcripcion de cada clip (el carril las hace; las que faltan se adelantan en su
    cola). Progreso real k/n en la barra y en la tarea lateral `words`. Cancelable."""
    lo, hi = PHASE_MONTAGE_WORDS
    n = len(sids)
    last = None
    lost_since = None
    states = {}
    while True:
        for sid in sids:                 # lo ya listo no se vuelve a leer en cada vuelta
            if states.get(sid) not in ("ready", "error", None) or sid not in states:
                states[sid] = _words_state(sid)
        pending = [s for s in sids if states[s] == "pending"]
        errors = [s for s in sids if states[s] == "error"]
        done = n - len(pending)
        if pending:
            _words_kick(pending, front=True)
        if done != last:
            jobstore.side_update(job_id, "words", state="running" if pending else "done", done=done,
                                 total=n, errors=len(errors),
                                 message=f"Transcribiendo tus clips ({done}/{n})…")
            bar.set(lo + (hi - lo) * done / max(1, n), f"Transcribiendo tus clips ({done}/{n})…",
                    "words", force=True)
            last = done
        if not pending:
            return states
        # un clip pendiente que el carril no tiene (motor de voz aun sin descargar): no esperar para
        # siempre
        with _WORDS_LOCK:
            lost = [s for s in pending if s not in _WORDS_Q and s not in _WORDS_RUN]
        if lost:
            lost_since = lost_since or time.time()
            if time.time() - lost_since > 30:
                raise RuntimeError("La transcripción de los clips no pudo empezar (el motor de voz "
                                   "aún se está preparando).")
        else:
            lost_since = None
        prog.cancel.check()
        time.sleep(0.3)


def _montage_clips(sids):
    """Clips de la sesion EN ORDEN DE GRABACION con lo que el motor de montaje (montage.build)
    necesita: [{sid, name, rec, created, dur, words, takes, audio (ruta del wav 16 kHz de su 1a
    pista, None sin voz), thumb (miniatura: grupos de camara), setup (None: lo decide el motor)}]
    + {path, wav, has_audio, created_src, w, h, fps, rotation, hdr, error}. `words`/`takes` en
    segundos del clip. `created` solo si es la fecha de GRABACION de los metadatos (la del archivo
    es la de la copia: el motor ordena esos clips por nombre, como rec_order)."""
    items = []
    for k, sid in enumerate(sids):
        src = _resolve_source(sid)
        if src is None:
            continue
        meta = _source_meta_full(sid, src)
        doc = _words_load(sid, src) or {}
        _jp, wav = _words_paths(sid)
        has = bool(doc.get("audio", meta.get("audio")))
        wav_s = str(wav) if has and wav.exists() else None
        thumb = SOURCES / f"{sid}_t.jpg"
        strong = meta.get("created_src") in ("apple", "creation_time")
        items.append({"sid": sid, "name": meta.get("name") or src.name, "upload": k,
                      "created": meta.get("created") if strong else None,
                      "created_src": meta.get("created_src"), "created_ts": meta.get("created_ts"),
                      "dur": meta.get("dur") or doc.get("dur"), "words": doc.get("words") or [],
                      "takes": doc.get("takes") or [], "audio": wav_s, "wav": wav_s, "has_audio": has,
                      "thumb": str(thumb) if thumb.exists() else None, "setup": None,
                      "path": str(src), "w": meta.get("w"), "h": meta.get("h"), "fps": meta.get("fps"),
                      "rotation": meta.get("rotation"), "hdr": bool(meta.get("hdr")),
                      "error": _WORDS_ERR.get(sid)})
    out = []
    for r, k in enumerate(rec_order(items)):
        it = dict(items[k], rec=r)
        it.pop("upload", None)
        out.append(it)
    return out


def _montage_build(clips, script=None, glossary="", intercut=True, on_step=None, cancel=None,
                   llm=True, assets=None):
    """montage.build (scripts/montage.py, importado al usarlo) -> dict del contrato plan.montage.
    Solo pasa los argumentos que build acepta (el motor puede crecer sin romper el servidor).
    `llm`: el LLM local como juez de casi-empates y del material extra (montage.local_llm; sin
    ollama queda None y el motor decide solo con reglas)."""
    try:
        import montage as montage_mod
    except ImportError as e:
        raise RuntimeError("El motor de montaje no está disponible en esta versión.") from e
    fn = montage_mod.build
    judge = None
    if llm and _accepts(fn, "llm") and hasattr(montage_mod, "local_llm"):
        try:
            judge = montage_mod.local_llm()
        except Exception:  # noqa: sin juez el montaje sale igual (reglas)
            traceback.print_exc()
    kw = {}
    for k, v in (("script", script), ("glossary", glossary), ("intercut", intercut),
                 ("on_step", on_step), ("cancel", cancel), ("llm", judge), ("assets", assets or [])):
        if _accepts(fn, k):
            kw[k] = v
    if script and "script" in kw and not _accepts(fn, "assets"):
        # un motor sin recursos del guion compara el guion SIN las anotaciones ("- Agregar x")
        kw["script"] = _strip_refs(script)
    return fn(clips, **kw)


# ---- recursos del guion: "texto. - Agregar video_x" / "[agregar x]" / "[x]" / "{x}" al final de una
# linea piden un recurso (imagen o video) que aparece como tarjeta flotante mientras se dice. El dueno
# de las reglas es montage.py (split_annotation, match_asset, classify, plan_inserts; fxcore.js es su
# port para la UI): aqui se usan esas funciones y, si el motor no las trae (version anterior), una
# version minima con el mismo contrato: el archivo nombrado es RECURSO, nunca toma; los nombres se
# comparan normalizados (minusculas, sin acentos ni extension, espacios y guiones -> "_"), primero
# iguales y luego por un prefijo unico.


def _mt(name):
    """Funcion `name` de scripts/montage.py (importado al usarlo) o None."""
    try:
        import montage as montage_mod
    except Exception:  # noqa: motor sin montaje
        return None
    return getattr(montage_mod, name, None)
_ANN_VERB = r"agregar|a[ñn]adir|insertar|mostrar|poner|add|insert"
_ANN_DASH = re.compile(r"(^|\s|[.!?,;:…)\"'»”])\s*[-–—|]\s*(?:" + _ANN_VERB + r")\s+([^\[\]{}]+?)\s*$",
                       re.IGNORECASE)
_ANN_BRACKET = re.compile(r"\s*(\[([^\[\]]+)\]|\{([^{}]+)\})\s*$")
_ANN_VERB_HEAD = re.compile(r"^\s*(?:" + _ANN_VERB + r")\s+", re.IGNORECASE)
INSERT_PRE, INSERT_POST, INSERT_MIN, INSERT_SPEED_MAX = 0.15, 0.25, 2.0, 1.4


def _asset_norm(s):
    """Nombre de un recurso normalizado: "Video Horno-Leña.MP4" -> "video_horno_lena"."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(s or "").strip())
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"\.[a-z0-9]{2,5}$", "", t)
    return re.sub(r"[\s_\-]+", "_", t).strip("_")


def _ann_names(s):
    out = []
    for x in re.split(r"\s*,\s*", str(s or "")):
        x = re.sub(r"[\"'»”.;:]+$", "", re.sub(r"^[\"'«“]+", "", x.strip())).strip()
        if x:
            out.append(x)
    return out


_MEDIA_NAME_EXT = re.compile(r"\.(?:jpe?g|png|heic|heif|webp|gif|bmp|tiff?|avif|mp4|mov|m4v|webm|mkv|avi|"
                             r"mts|m2ts|3gp|hevc)$", re.IGNORECASE)


def _ann_name_ok(n):
    """= montage._name_like sin archivos conocidos: extension de imagen/video o UNA palabra con letras
    y _ . - o digitos. "valor a cada familia" (el final de una frase con guion largo) no es recurso."""
    n = str(n or "").strip()
    ws = n.split()
    if not ws or len(ws) > 3:
        return False
    if _MEDIA_NAME_EXT.search(n):
        return True
    return len(ws) == 1 and re.search(r"[_.\-\d]", n) is not None and re.search(r"[^\W\d_]", n) is not None


def _line_refs(raw, known=None):
    """Una linea del guion -> (texto sin la anotacion, [recursos pedidos]). `known`: nombres de los
    archivos de la sesion (reconocen "[nombre]" sin verbo)."""
    fn = _mt("split_annotation")
    if fn is not None:
        try:
            keys = [_asset_norm(n) for n in known or [] if n]
            t, names = fn(str(raw or ""), keys)
            return str(t), [str(n) for n in names]
        except Exception:  # noqa: la version minima
            traceback.print_exc()
    text = str(raw or "").rstrip()
    names = []
    for _ in range(8):
        b = _ANN_BRACKET.search(text)
        if b:
            inner = b.group(2) if b.group(2) is not None else (b.group(3) or "")
            names[:0] = _ann_names(_ANN_VERB_HEAD.sub("", inner))
            text = text[:b.start()].rstrip()
            continue
        d = _ANN_DASH.search(text)
        if d and _ann_names(d.group(2)) and all(_ann_name_ok(n) for n in _ann_names(d.group(2))):
            names[:0] = _ann_names(d.group(2))
            cut = d.start() + (len(d.group(1)) if d.group(1) and not d.group(1).isspace() else 0)
            text = text[:cut].rstrip()
            continue
        break
    seen, uniq = set(), []
    for n in names:
        k = _asset_norm(n)
        if k and k not in seen:
            seen.add(k)
            uniq.append(n)
    return text.strip(), uniq


def _script_refs(text):
    """Recursos que pide un guion -> [(indice entre las lineas no vacias, texto limpio, [nombres])]
    (solo las lineas con recurso)."""
    out, i = [], 0
    for raw in str(text or "").splitlines():
        if not raw.strip():
            continue
        t, names = _line_refs(raw)
        if names:
            out.append((i, t, names))
        i += 1
    return out


def _strip_refs(text):
    """El guion sin anotaciones de recursos (lo que se compara con las tomas y va al glosario)."""
    return "\n".join(_line_refs(raw)[0] if raw.strip() else "" for raw in str(text or "").splitlines())


def _match_asset(name, names):
    """Indice del archivo (`names`: nombres de archivo) que corresponde al recurso `name` o -1."""
    fn = _mt("match_asset")
    if fn is not None:
        try:
            files = [{"name": n, "_i": i} for i, n in enumerate(names)]
            f = fn(name, files)
            return int(f["_i"]) if f else -1
        except Exception:  # noqa: la version minima
            traceback.print_exc()
    k = _asset_norm(name)
    if not k:
        return -1
    ns = [_asset_norm(n) for n in names]
    if k in ns:
        return ns.index(k)
    if len(k) < 4 or not re.search(r"[a-z]", k):
        return -1                  # "1", "a": nunca por prefijo (= montage._match_key)
    pre = [i for i, n in enumerate(ns) if n and n.startswith(k + "_")]
    if not pre:                    # al reves solo una imagen (una toma de video nunca pasa a recurso)
        pre = [i for i, n in enumerate(ns) if n and len(n) >= 3 and k.startswith(n + "_")
               and re.search(r"\.(?:jpe?g|png|heic|heif|webp|gif|bmp|tiff?|avif)$", str(names[i]), re.I)]
    return pre[0] if len(pre) == 1 else -1


INSERT_SLOW_MIN = 0.8


def _insert_fit(kind, media_dur, slot, src_in=0.0):
    """Como entra un medio de `media_dur` s en un hueco de `slot` s (= montage.plan_inserts y
    fxcore.insertFit): imagen -> hold; video de 0.8x a 1.4x del hueco -> speed (su velocidad se
    ajusta); mas largo -> trim (desde src_in); mas corto -> trim y dura lo suyo ("len")."""
    L = float(slot or 0)
    d = float(media_dur or 0)
    if kind != "video":
        return {"fit": "hold", "speed": 1.0, "src_in": 0.0}
    out = {"fit": "trim", "speed": 1.0, "src_in": round(max(0.0, min(float(src_in or 0), max(0.0, d - L))), 3)}
    if d > 0 and L > 0:
        r = d / L
        if INSERT_SLOW_MIN <= r <= INSERT_SPEED_MAX:
            return {"fit": "speed", "speed": round(r, 3), "src_in": 0.0}
        if r < INSERT_SLOW_MIN:
            out.update(src_in=0.0, len=round(d, 3))
    return out


def _asset_item(sid):
    """Fuente subida como recurso del guion -> {sid, name, kind, dur, w, h, path} o None."""
    src = _resolve_source(sid)
    if src is None:
        return None
    meta = _source_meta(sid)
    return {"sid": sid, "name": meta.get("name") or src.name, "kind": "image" if _is_image(src) else "video",
            "dur": float(meta.get("dur") or 0), "w": meta.get("w") or 0, "h": meta.get("h") or 0,
            "path": str(src)}


def _inserts_fallback(m, clips, assets):
    """plan.montage.inserts cuando el motor de montaje no los trae: cada recurso pedido por el guion,
    desde la primera palabra de su linea - 0.15 s hasta la ultima + 0.25 s (s del ensamble; minimo
    2 s y sin encimarse con el siguiente). Varias lineas en una parte: su tramo por proporcion de
    palabras. Sin archivo que corresponda: status "missing". Con montage.plan_inserts (line_times de
    cada toma) se usa ese."""
    fn = _mt("plan_inserts")
    if fn is not None:
        try:
            got = fn(m, [{k: a.get(k) for k in ("sid", "name", "kind", "dur")} for a in assets])
            # sin line_times (un motor que no los trae) no coloca ninguno: la version minima los
            # coloca por las palabras de cada parte
            if isinstance(got, list) and (any(isinstance(x, dict) and x.get("start") is not None for x in got)
                                          or not any((L or {}).get("inserts") for L in
                                                     ((m.get("script") or {}).get("lines") or []))):
                return got
        except Exception:  # noqa: la version minima
            traceback.print_exc()
    sc = m.get("script") if isinstance(m.get("script"), dict) else {}
    lines = [L for L in sc.get("lines") or [] if isinstance(L, dict)]
    words = {c["sid"]: c.get("words") or [] for c in clips}
    names = [a.get("name") or "" for a in assets]
    ntok = {L.get("id"): max(1, len(str(L.get("text") or "").split())) for L in lines}
    out = []
    for L in lines:
        for name in L.get("inserts") or []:
            slots = [s for s in m.get("slots") or [] if L.get("id") in (s.get("lines") or [])]
            ids = {s.get("id") for s in slots}
            items = sorted([t for t in m.get("timeline") or [] if t.get("slot") in ids],
                           key=lambda t: float(t.get("start") or 0))
            ws = []
            for t in items:
                a0, b0, st = float(t["in"]), float(t["out"]), float(t.get("start") or 0)
                for w in words.get(t.get("sid")) or []:
                    if float(w["end"]) > a0 and float(w["start"]) < b0:
                        ws.append((st + max(float(w["start"]), a0) - a0, st + min(float(w["end"]), b0) - a0))
            span = None
            if ws:
                ws.sort()
                lids = [x for s in slots for x in s.get("lines") or []]
                if len(lids) > 1 and L.get("id") in lids:
                    tot = sum(ntok.get(x, 1) for x in lids)
                    before = sum(ntok.get(x, 1) for x in lids[:lids.index(L["id"])])
                    i0 = min(len(ws) - 1, round(before / tot * len(ws)))
                    i1 = max(i0, min(len(ws) - 1, round((before + ntok.get(L["id"], 1)) / tot * len(ws)) - 1))
                    span = (ws[i0][0], ws[i1][1])
                else:
                    span = (ws[0][0], ws[-1][1])
            elif items:
                span = (float(items[0].get("start") or 0),
                        max(float(t.get("start") or 0) + float(t["out"]) - float(t["in"]) for t in items))
            k = _match_asset(name, names)
            a = assets[k] if k >= 0 else None
            ins = {"id": f"in{len(out) + 1:02d}", "line": L.get("id"), "name": name,
                   "sid": a["sid"] if a else None, "kind": a["kind"] if a else None,
                   "start": None, "end": None, "fit": "hold", "speed": 1.0, "src_in": 0.0,
                   "status": "ok" if a else "missing"}
            if span:
                s0 = max(0.0, span[0] - INSERT_PRE)
                ins["start"], ins["end"] = round(s0, 3), round(max(span[1] + INSERT_POST, s0 + INSERT_MIN), 3)
            out.append(ins)
    on = sorted([x for x in out if x["start"] is not None], key=lambda x: x["start"])
    for x, y in zip(on, on[1:]):
        if x["end"] > y["start"] - 0.05:
            x["end"] = round(max(x["start"] + 0.3, y["start"] - 0.05), 3)
    for x in out:
        if x["sid"] and x["start"] is not None:
            a = next((a for a in assets if a["sid"] == x["sid"]), None)
            f = _insert_fit(x["kind"], a and a.get("dur"), x["end"] - x["start"])
            if f.pop("len", None) is not None:
                x["end"] = round(x["start"] + float(a.get("dur") or 0), 3)
            x.update(f)
    return out


def _script_parse(text):
    """Guion en texto -> (sections, lines) del contrato: una linea por renglon, un renglon en
    blanco abre otra seccion. ids L01, L02... y secciones 1, 2... La anotacion de recursos de una
    linea ("- Agregar x") sale del texto y queda en lines[i]["inserts"] (montage.parse_script)."""
    fn = _mt("parse_script")
    if fn is not None:
        try:
            ps = fn(text)
            return ps["sections"], ps["lines"]
        except Exception:  # noqa: la version minima
            traceback.print_exc()
    sections, lines, cur = [], [], None
    for raw in str(text or "").splitlines():
        t = raw.strip()
        if not t:
            cur = None
            continue
        if cur is None:
            cur = {"id": len(sections) + 1, "lines": []}
            sections.append(cur)
        lid = f"L{len(lines) + 1:02d}"
        clean, names = _line_refs(t)
        ln = {"id": lid, "text": clean}
        if names:
            ln["inserts"] = names
        lines.append(ln)
        cur["lines"].append(lid)
    return sections, lines


def _montage_result(res, clips, params, build_s, assets=None):
    """Resultado de montage.build normalizado al contrato (lo que falte, vacio): la linea de tiempo
    solo con clips de ESTA sesion (nunca una fuente ajena), el orden de grabacion, el guion y los
    recursos del guion (assets + inserts)."""
    m = dict(res) if isinstance(res, dict) else {}
    known = {c["sid"] for c in clips}
    m["version"] = m.get("version") or 1
    if not m.get("order"):
        m["order"] = [{"sid": c["sid"], "name": c["name"], "rec": c["rec"], "created": c["created"]}
                      for c in clips]
    sc = m.get("script") if isinstance(m.get("script"), dict) else {}
    ps = params.get("script") if isinstance(params.get("script"), dict) else None
    if ps and ps.get("text"):
        sc.setdefault("source", ps.get("source") or "user")
        sc.setdefault("text", ps["text"])
    if sc.get("text") and not sc.get("lines"):
        sc["sections"], sc["lines"] = _script_parse(sc["text"])
    elif sc.get("text") and isinstance(sc.get("lines"), list):
        # lineas del motor: sin la anotacion de recursos en el texto y con sus "inserts" (por indice
        # solo si el motor no partio lineas: una por renglon no vacio)
        n_raw = sum(1 for x in str(sc["text"]).splitlines() if x.strip())
        refs = {i: names for i, _t, names in _script_refs(sc["text"])} if n_raw == len(sc["lines"]) else {}
        for i, L in enumerate(sc["lines"]):
            if not isinstance(L, dict):
                continue
            clean, names = _line_refs(L.get("text") or "")
            if names or clean != (L.get("text") or ""):
                L["text"] = clean
            if not L.get("inserts") and (names or refs.get(i)):
                L["inserts"] = names or refs.get(i)
    if sc:
        sc.setdefault("source", "detected")
        sc.setdefault("sections", [])
        sc.setdefault("lines", [])
    m["script"] = sc or None
    for k in ("slots", "timeline", "discarded", "uncovered", "extra"):
        if not isinstance(m.get(k), list):
            m[k] = []
    m["timeline"] = [t for t in m["timeline"] if isinstance(t, dict) and t.get("sid") in known]
    if not isinstance(m.get("stats"), dict):
        m["stats"] = {}
    m["ms"] = int(m.get("ms") or round(build_s * 1000))
    _montage_inserts(m, clips, assets or [])
    m["intercut"] = params.get("intercut", True) is not False
    # el glosario con el que se armo: el ensamble lo muestra (y lo que el usuario deje ahi manda)
    m["glossary"] = str(params.get("glossary") or "")
    return m


def _montage_inserts(m, clips, assets):
    """plan.montage.assets (los recursos de la sesion) e inserts saneados: los del motor o, si no los
    trae, los de _inserts_fallback. Un recurso solo apunta a un archivo de ESTA sesion."""
    by_sid = {a["sid"]: a for a in assets}
    m["assets"] = [{k: a.get(k) for k in ("sid", "name", "kind", "dur", "w", "h")} for a in assets]
    raw = m.get("inserts")
    wants = any(isinstance(L, dict) and L.get("inserts")
                for L in ((m.get("script") or {}).get("lines") or []))
    if not isinstance(raw, list) or (not raw and wants):
        raw = _inserts_fallback(m, clips, assets)
    out = []
    for k, x in enumerate(raw):
        if not isinstance(x, dict):
            continue
        sid = x.get("sid") if x.get("sid") in by_sid else None
        a = by_sid.get(sid) if sid else None
        ins = {"id": str(x.get("id") or f"in{k + 1:02d}")[:40], "line": x.get("line"),
               "name": str(x.get("name") or (a or {}).get("name") or "")[:200], "sid": sid,
               "kind": a["kind"] if a else (x.get("kind") if x.get("kind") in ("image", "video") else None),
               "start": _fnum(x.get("start")), "end": _fnum(x.get("end")),
               "fit": x.get("fit") if x.get("fit") in ("hold", "speed", "trim") else "hold",
               "speed": max(0.25, min(4.0, _fnum(x.get("speed")) or 1.0)),
               "src_in": max(0.0, _fnum(x.get("src_in")) or 0.0),
               "status": "ok" if a else "missing"}
        if a:
            ins["path"] = a["path"]
            ins["media_dur"] = a.get("dur") or 0
        out.append(ins)
    m["inserts"] = out
    return out


def _fnum(v):
    """Numero finito o None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None

# ---- glosario de whisper desde el guion: nombres propios y cifras (nunca el guion entero) ----
GLOSS_MAX_TERMS = 24
GLOSS_MAX_CHARS = 240
_GLOSS_WORD = re.compile(r"[^\W\d_][\w'’\-]*|\d[\d.,]*", re.UNICODE)
_GLOSS_QUOTED = re.compile(r"[«\"“]([^«»\"“”\n]{2,40})[»\"”]")
_GLOSS_MAGNITUDE = ("mil", "millones", "millón", "millon")     # "20 mil" (whisper: 20.000 / 20 mil)


def _script_terms(text):
    """Nombres propios (palabras con mayuscula que NO abren oracion, juntas si van seguidas: "Meta
    Ads"), palabras con mayusculas internas o siglas ("MetaAds", "IA"), terminos cortos entre
    comillas y cifras de 2+ digitos con su magnitud ("20 mil", "100", "15%") del guion, en orden de
    aparicion y sin repetir."""
    out = []

    def add(t):
        t = t.strip(" .,;:!?¿¡\"'«»“”")
        if t and len(t) <= 40 and t.lower() not in {x.lower() for x in out}:
            out.append(t)

    for line in str(text or "").splitlines():
        run, prev_end = [], None

        def flush():
            if run:
                add(" ".join(run))
                run.clear()
        for m in _GLOSS_WORD.finditer(line):
            w = m.group(0)
            if w[0].isdigit():
                flush()
                num = w.rstrip(".,")
                rest = line[m.end():].lstrip()
                nxt = re.match(r"[^\W\d_]+", rest)
                mag = nxt.group(0) if nxt and nxt.group(0).lower() in _GLOSS_MAGNITUDE else None
                if mag:
                    add(f"{num} {mag}")
                elif rest.startswith("%"):
                    add(f"{num}%")
                elif sum(c.isdigit() for c in num) >= 2:
                    add(num)
                prev_end = m.end()
                continue
            before = line[:m.start()].rstrip(" \t\"'«“¿¡(")
            initial = not before or before[-1] in ".!?:;…"
            inner = any(c.isupper() for c in w[1:]) and any(c.islower() for c in w)
            acro = len(w) >= 2 and w.isupper()
            contig = prev_end is not None and not line[prev_end:m.start()].strip()
            if (w[0].isupper() and not initial) or inner or acro:
                if run and not contig:
                    flush()
                run.append(w)
            else:
                flush()
            prev_end = m.end()
        flush()
        for q in _GLOSS_QUOTED.findall(line):
            if len(q.split()) <= 3:
                add(q)
    return out


def _script_glossary(script_text, user=""):
    """Glosario del video combinado: el del usuario + los nombres propios y cifras del guion
    (_script_terms), sin repetir y acotado (whisper recibe un prompt corto, no el guion)."""
    terms = []
    for t in re.split(r"[,;\n]+", str(user or "")) + _script_terms(_strip_refs(script_text)):
        t = t.strip()
        if t and t.lower() not in {x.lower() for x in terms}:
            terms.append(t)
    out = []
    for t in terms[:GLOSS_MAX_TERMS]:
        if len(", ".join(out + [t])) > GLOSS_MAX_CHARS:
            break
        out.append(t)
    return ", ".join(out)


def _natkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s or ""))]


def rec_order(items):
    """Orden de GRABACION de una sesion -> indices de `items` ([{created_ts, created_src, name}] en
    orden de subida). Primero los que traen fecha de grabacion en sus metadatos (Apple
    creationdate o creation_time), por fecha; luego los que solo tienen la fecha del archivo (la de
    la copia: no dice cuando se grabo), por nombre natural (IMG_2 antes que IMG_10). Empates: nombre
    natural y despues el orden de subida."""
    def key(k):
        it = items[k]
        ts = it.get("created_ts")
        if it.get("created_src") in ("apple", "creation_time") and ts is not None:
            return (0, float(ts), _natkey(it.get("name")), k)
        return (1, 0.0, _natkey(it.get("name")), k)
    return sorted(range(len(items)), key=key)


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
    _claim_cache(job_id, stats)
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
        # los clips de un montaje ("Armar con IA"), aun sin plan: todos, tambien las alternativas
        used |= jobstore.params_sids(params)
    # medios propios que un plan referencia ({sid, type} en fx): tampoco se borran
    used |= jobstore.referenced_sids()
    # fotos de perfil y foto/logo de los enlaces de los perfiles guardados (el Studio las reutiliza
    # en otros proyectos) y de la cache de enlaces (un perfil nuevo con el mismo enlace las usa)
    from motion import profile as PR
    for pr in _profiles_load():
        used |= PR.media_sids(pr)
    used |= _lm_cache_sids()
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
    """Recoleccion de basura del disco: huerfanos (archivos de trabajos que ya no estan en la
    base), fuentes sin uso y tope de las caches REGENERABLES. Los proyectos NUNCA se purgan solos
    (antes: los 50 mas recientes, o 5 con poco disco, sin avisar): con menos de MIN_FREE_GB libres
    solo se avisa (`low_space`: /status y /storage lo muestran) y las caches se aprietan."""
    if not _GC_LOCK.acquire(blocking=False):
        return {"skipped": "en curso"}
    t0 = time.time()
    try:
        res = {"reason": reason}
        res["orphans"] = _gc_orphans()
        res["sources"] = _gc_sources()
        free = _disk_free_gb()
        if not _busy():
            cap = CACHE_CAP_GB if free >= MIN_FREE_GB else max(0.5, min(CACHE_CAP_GB, free * 0.25))
            res["caches"] = _cap_caches(cap)
        free = _disk_free_gb()
        res["free_gb"] = round(free, 1)
        res["low_space"] = free < MIN_FREE_GB
        res["t"] = round(time.time() - t0, 2)
        if res["orphans"] or res["sources"]:
            print(f"   limpieza ({reason}): {res}")
        if res["low_space"]:
            print(f"   poco espacio en disco ({free:.1f} GB libres): se avisa; los proyectos se conservan")
        _storage_invalidate()
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


# ================================================================ almacenamiento (proyectos)
# Un proyecto guarda su video (entrada: el compuesto o el subido), sus fuentes subidas, el analisis
# (audio y transcripcion), exports, vistas previas, proxies e intermedios del motor. Nada se purga
# solo: GET /storage cuenta el disco por categoria y por proyecto (en cache: no se recorren 40 GB en
# cada pedido); POST /jobs/{id}/free-space borra SOLO lo regenerable (el proyecto sigue editable);
# DELETE /jobs/{id} mueve a la Papelera todo lo que es SOLO de ese proyecto. Jamas se toca nada
# fuera de DATA (los videos originales del usuario): lo de fuera ni se enumera y cada ruta se
# verifica antes de moverla; un enlace simbolico se mueve como enlace, nunca su destino.
LOW_SPACE_MSG = "Poco espacio: libera espacio o elimina proyectos"
STORAGE_TTL_S = float(os.environ.get("REELFY_STORAGE_TTL", "60"))
TRASH_DIR = paths.DATA / ".trash"            # Papelera de respaldo (fuera de macOS)
_STAGE = paths.DATA / ".eliminando"          # el proyecto se junta aqui antes de ir a la Papelera
_OUT_CACHE_RE = re.compile(r"_(?:draft_[0-9a-f]+(?:\.part)?\.mp4|proxy\.mp4|preview\.mp4|win\.mp4)$")
_WORK_CACHE_SUF = ("_src720.mp4", "_src720.json", "_src720.part.mp4")
_STORE = {"data": None, "t": 0.0, "dirty": True}
_STORE_LOCK = threading.Lock()
_DISK = {"t": 0.0, "free": None}
_QHASH = {}                                  # (ruta, tamano, mtime, inodo) -> huella rapida
DEDUP_CHUNK = 1 << 20
import stat as _stat  # noqa: E402


def _storage_invalidate():
    _STORE["dirty"] = True


def _disk_state():
    """{free_gb, low_space, message?}. Libre del volumen de DATA, en cache 10 s (/status se sondea
    varias veces por segundo)."""
    now = time.time()
    if _DISK["free"] is None or now - _DISK["t"] > 10:
        try:
            _DISK["free"] = _disk_free_gb()
        except OSError:
            _DISK["free"] = float("inf")
        _DISK["t"] = now
    free = _DISK["free"]
    low = free < MIN_FREE_GB
    out = {"free_gb": round(free, 1) if math.isfinite(free) else None, "low_space": low}
    if low:
        out["message"] = LOW_SPACE_MSG
    return out


def _in_data(p):
    """La ruta vive DENTRO de DATA (y no es DATA). Un enlace simbolico cuenta por donde esta el
    enlace (se mueve el enlace, jamas su destino); lo demas por su ruta real."""
    base = paths.DATA.resolve()
    p = Path(p)
    try:
        parent = p.parent.resolve()
    except OSError:
        return False
    if parent != base and base not in parent.parents:
        return False
    if p.is_symlink():
        return True
    try:
        real = p.resolve(strict=True)
    except OSError:
        return False
    return real != base and base in real.parents


def _sid_of(name):
    return str(name).split(".")[0].split("_")[0]


# derivados de una fuente subida (input/sources/{sid}...): no son el archivo que subio el usuario
SOURCE_DERIVED = ("_proxy.mp4", "_t.jpg", "_meta.json", "_words.json", "_16k.wav")
SOURCE_CACHE = ("_proxy.mp4", "_proxy.part.mp4", "_16k.wav", "_16k.part.wav")   # regenerables


def _is_source_original(p):
    n = p.name
    return p.is_file() and ".part" not in n and not n.startswith(".") and \
        not n.endswith(SOURCE_DERIVED)


def _source_users():
    """sid -> {ids de trabajos} que usan esa fuente subida: montaje (spec) y medios propios o del
    perfil en su plan/parametros. Perfiles guardados y la cache de enlaces cuentan como el usuario
    '*' (una fuente asi nunca se va con un proyecto)."""
    users = {}
    try:
        src_dir = SOURCES.resolve()
    except OSError:
        src_dir = SOURCES
    for jid, params in jobstore.all_params().items():
        spec = (params or {}).get("spec") or {}
        for it in (spec.get("main") or []) + (spec.get("overlays") or []):
            if isinstance(it, dict) and it.get("path"):
                p = Path(str(it["path"]))
                try:
                    inside = p.parent.resolve() == src_dir
                except OSError:
                    inside = False
                if inside:
                    users.setdefault(_sid_of(p.name), set()).add(jid)
    for jid, sids in jobstore.sids_by_job().items():
        for sid in sids:
            users.setdefault(sid, set()).add(jid)
    try:
        from motion import profile as PR
        keep = set()
        for pr in _profiles_load():
            keep |= PR.media_sids(pr)
        keep |= _lm_cache_sids()
    except Exception:  # noqa: sin perfiles legibles: nada extra que proteger
        traceback.print_exc()
        keep = set()
    for sid in keep:
        users.setdefault(sid, set()).add("*")
    return users


def _mc_head(n):
    """Intermedio principal del que depende un acompanante de work/motion_cache (o el mismo
    nombre): base_<k>.mp4(.json/.lock), fgbase_<k>.mp4(.json/.lock) y face_<k>_<firma>.npy ->
    base_<k>.mp4; voice_<k>.wav(.json/.lock) -> voice_<k>.wav."""
    if n.startswith("face_"):
        return "base_" + n[len("face_"):].split("_")[0] + ".mp4"
    if n.startswith("fgbase_"):
        n = n[len("fg"):]
    for suf in (".json", ".lock"):
        if n.endswith(suf):
            n = n[:-len(suf)]
    return n


def _mc_owners(job_ids):
    """Intermedios de work/motion_cache -> {nombre: id del trabajo}. El base dice su fuente en su
    .json (`src` = input/<id>.<ext>); la voz (y lo demas) por lo que el servidor anoto al generarlo
    (jobstore.claim_cache). Cada uno arrastra sus acompanantes (_mc_head): .json, .lock, pista de
    rostro y recorte del frente. Lo que no es de nadie (proyectos de antes de las anotaciones,
    mattes, medios) lo poda el tope LRU de siempre o el "Liberar espacio" global."""
    mc = paths.WORK / "motion_cache"
    try:
        names = set(os.listdir(mc))
    except OSError:
        return {}
    ids = set(job_ids)
    own = {}
    for n in names:
        if n.startswith("base_") and n.endswith(".mp4"):
            try:
                src = Path(str(json.loads((mc / f"{n}.json").read_text()).get("src") or ""))
            except Exception:  # noqa
                continue
            if src.parent.name == "input" and src.stem in ids:
                own[n] = src.stem
    for jid, files in jobstore.cache_claims().items():
        if jid in ids:
            for n in files:
                n = Path(str(n)).name
                if n in names:
                    own.setdefault(n, jid)
    out = {}
    for n in names:
        jid = own.get(n) or own.get(_mc_head(n))
        if jid:
            out[n] = jid
    return out


def _walk(p, seen):
    """(bytes, bytes_liberables) de una ruta sin seguir enlaces. Cada inodo cuenta una vez
    (`seen`: los enlaces duros de la deduplicacion no se suman dos veces); liberable = lo que se
    libera al quitar ESTA ruta (un archivo con otro enlace duro vivo no libera nada)."""
    try:
        st = os.lstat(p)
    except OSError:
        return 0, 0
    if _stat.S_ISDIR(st.st_mode):
        tot = fr = 0
        try:
            with os.scandir(p) as it:
                for e in it:
                    a, b = _walk(e.path, seen)
                    tot += a
                    fr += b
        except OSError:
            pass
        return tot, fr
    key = (st.st_dev, st.st_ino)
    if key in seen:
        return 0, 0
    seen.add(key)
    size = st.st_size
    return size, (size if st.st_nlink <= 1 or _stat.S_ISLNK(st.st_mode) else 0)


def _ls(d):
    try:
        return sorted(Path(d).iterdir())
    except OSError:
        return []


def _storage_ctx():
    """Lo que hace falta para repartir el disco entre proyectos (sin recorrer archivos)."""
    rows = jobstore.recent(100000)
    jobs = {r["id"]: r for r in rows}
    return {"jobs": jobs, "users": _source_users(), "mc": _mc_owners(jobs.keys())}


_UNIT_RE = re.compile(r"^([^._]+)(?:[._]|$)")      # id del trabajo = lo que va antes del primer . o _


def _unit_index(ctx):
    """Cada carpeta se lista UNA vez por recorrido (antes cada proyecto listaba y ordenaba INPUT,
    OUTPUT, WORK, MUSIC_OUT y SOURCES: con 400 proyectos /jobs tardaba 24 s) -> mapas
    {id del trabajo: [Path]} por carpeta, {sid: [Path]} de las fuentes, los intermedios del motor
    por trabajo y las fuentes de cada trabajo."""
    idx = {"input": {}, "output": {}, "work": {}, "music": {}, "sources": {}, "mc": {}, "sids": {}}
    for key, d in (("input", INPUT), ("output", OUTPUT), ("work", paths.WORK)):
        for p in _ls(d):
            m = _UNIT_RE.match(p.name)
            if m:
                idx[key].setdefault(m.group(1), []).append(p)
    for p in _ls(MUSIC_OUT):
        if p.name.startswith("music_"):
            jid = p.name[len("music_"):].split("_")[0]
            if p.name.startswith(f"music_{jid}_"):
                idx["music"].setdefault(jid, []).append(p)
    if ctx.get("users"):
        for p in _ls(SOURCES):
            idx["sources"].setdefault(_sid_of(p.name), []).append(p)
    for n, jid in (ctx.get("mc") or {}).items():
        idx["mc"].setdefault(jid, []).append(n)
    for sid, us in (ctx.get("users") or {}).items():
        for jid in us:
            idx["sids"].setdefault(jid, set()).add(sid)
    return idx


def _job_units(job_id, ctx):
    """Archivos (o carpetas) del proyecto por categoria -> {"project", "exports", "cache": [Path],
    "shared": [Path]}. `shared` = fuentes que otro proyecto (o un perfil) tambien usa: cuentan en
    su tamano pero no se borran con el. Solo rutas DENTRO de DATA. Las carpetas se listan una sola
    vez por `ctx` (_unit_index)."""
    idx = ctx.get("idx")
    if idx is None:
        idx = ctx["idx"] = _unit_index(ctx)
    out = {"project": [], "exports": [], "cache": [], "shared": []}
    for p in idx["input"].get(job_id, ()):
        out["project"].append(p)
    for p in idx["output"].get(job_id, ()):
        out["cache" if _OUT_CACHE_RE.search(p.name) else "exports"].append(p)
    for p in idx["work"].get(job_id, ()):
        out["cache" if p.name.endswith(_WORK_CACHE_SUF) else "project"].append(p)
    c = paths.WORK / f"compose_{job_id}"
    if c.exists():
        out["cache"].append(c)
    for p in idx["music"].get(job_id, ()):
        out["project"].append(p)
    for n in idx["mc"].get(job_id, ()):
        out["cache"].append(paths.WORK / "motion_cache" / n)
    for sid in sorted(idx["sids"].get(job_id, ())):
        for p in idx["sources"].get(sid, ()):
            if p.is_dir():
                continue
            if (ctx["users"].get(sid) or set()) - {job_id}:
                out["shared"].append(p)
            else:
                out["cache" if p.name.endswith(SOURCE_CACHE) else "project"].append(p)
    for k in out:
        out[k] = [p for p in out[k] if _in_data(p)]
    return out


def _storage_scan():
    """Recorrido COMPLETO de lo que Reelfy guarda en DATA (sin seguir enlaces) -> totales por
    categoria y por proyecto. Categorias: exports (videos exportados), cache (regenerable: vistas
    previas, proxies, intermedios del motor, musica en cache) y projects (proyectos: videos,
    fuentes, analisis; + modelos de IA, base, logs)."""
    t0 = time.time()
    ctx = _storage_ctx()
    seen = set()
    per = {}
    counted = set()                  # rutas ya contadas en algun proyecto
    units = {jid: _job_units(jid, ctx) for jid in ctx["jobs"]}
    for jid, u in units.items():
        d = {"bytes": 0, "project_bytes": 0, "exports_bytes": 0, "cache_bytes": 0,
             "frees_bytes": 0, "cache_frees_bytes": 0, "shared_bytes": 0}
        for cat in ("project", "exports", "cache", "shared"):
            for p in u[cat]:
                a, b = _walk(p, set())           # por proyecto: cada proyecto cuenta lo suyo
                d["bytes"] += a
                if cat == "shared":
                    d["shared_bytes"] += a
                else:
                    d[f"{cat}_bytes"] += a
                    d["frees_bytes"] += b
                    if cat == "cache":
                        d["cache_frees_bytes"] += b
        per[jid] = d
    tot = {"exports": 0, "cache": 0, "projects": 0, "models": 0, "trash": 0}

    def add(cat, p):
        a, _ = _walk(p, seen)
        tot[cat] += a
        counted.add(str(p))

    # 1) lo que es de algun proyecto (con su categoria)
    for u in units.values():
        for cat, key in (("project", "projects"), ("shared", "projects"), ("exports", "exports"),
                         ("cache", "cache")):
            for p in u[cat]:
                if str(p) not in counted:
                    add(key, p)
    # 2) el resto de las carpetas de Reelfy
    for p in _ls(OUTPUT):
        if str(p) in counted:
            continue
        if p == MUSIC_OUT:
            for q in _ls(p):
                if str(q) not in counted:
                    add("projects", q)
        else:
            add("cache" if _OUT_CACHE_RE.search(p.name) else "exports", p)
    for p in _ls(INPUT):
        if str(p) in counted:
            continue
        if p == SOURCES:
            for q in _ls(p):
                if str(q) not in counted:
                    add("cache" if q.name.endswith(SOURCE_CACHE) else "projects", q)
        else:
            add("projects", p)
    for p in _ls(paths.WORK):
        if str(p) in counted:
            continue
        if p.name == "motion_cache":
            for q in _ls(p):
                if str(q) not in counted:
                    add("cache", q)
        elif p.name in ("music", "director_cache") or p.name.startswith("compose_") or \
                p.name.endswith(_WORK_CACHE_SUF):
            add("cache", p)
        else:
            add("projects", p)
    for p in (paths.DATA / "models", paths.OLLAMA_MODELS, paths.DATA / "music-models"):
        add("models", p)
    for p in (paths.BRAND, paths.LOGS):
        add("projects", p)
    for p in _ls(paths.DATA):
        if p.name.startswith("jobs.db"):
            add("projects", p)
    local_trash = _walk(TRASH_DIR, set())[0]       # papelera de respaldo: solo "Vaciar" la libera
    for p in (TRASH_DIR, _STAGE):
        add("trash", p)
    # lo que "Liberar espacio" (free_all_space) de verdad borra: la cache de los proyectos y los
    # intermedios del motor sin dueno (> 10 min). La musica IA y las respuestas del director en
    # cache cuentan en cache_bytes pero no se borran (regenerarlas cuesta minutos de IA)
    freeable = sum(d["cache_frees_bytes"] for d in per.values())
    owned, now = set(ctx["mc"]), time.time()
    for p in _ls(paths.WORK / "motion_cache"):
        try:
            if p.name not in owned and now - p.lstat().st_mtime > 600:
                freeable += _walk(p, set())[1]
        except OSError:
            continue
    disk = _disk_state()
    total = sum(tot.values())
    return {"total_bytes": total, "exports_bytes": tot["exports"], "cache_bytes": tot["cache"],
            "freeable_bytes": freeable, "local_trash_bytes": local_trash,
            "projects_bytes": tot["projects"] + tot["models"], "models_bytes": tot["models"],
            "trash_bytes": tot["trash"], "free_gb": disk["free_gb"], "low_space": disk["low_space"],
            "min_free_gb": MIN_FREE_GB, "message": disk.get("message"), "jobs": per,
            "scan_s": round(time.time() - t0, 3), "at": round(time.time(), 1)}


def storage_snapshot(force=False):
    """Resultado de _storage_scan en cache (STORAGE_TTL_S, o hasta que algo lo invalide: borrar,
    liberar, exportar, importar). El libre del disco y el aviso siempre al dia."""
    with _STORE_LOCK:
        d = _STORE["data"]
        if force or d is None or _STORE["dirty"] or time.time() - _STORE["t"] > STORAGE_TTL_S:
            _STORE["dirty"] = False
            try:
                d = _storage_scan()
            except Exception:  # noqa: sin conteo no se cae nada
                traceback.print_exc()
                _STORE["dirty"] = True
                d = d or {"total_bytes": 0, "exports_bytes": 0, "cache_bytes": 0, "freeable_bytes": 0,
                          "local_trash_bytes": 0, "projects_bytes": 0, "models_bytes": 0,
                          "trash_bytes": 0, "jobs": {}}
            _STORE["data"], _STORE["t"] = d, time.time()
    out = dict(d)
    disk = _disk_state()
    out.update(free_gb=disk["free_gb"], low_space=disk["low_space"], message=disk.get("message"),
               age_s=round(time.time() - _STORE["t"], 1))
    return out


# ---- deduplicacion de importaciones: el mismo archivo importado dos veces ocupa UNA vez ----
def _quick_hash(p):
    """Huella rapida: tamano + sha1 de 1 MiB al inicio, a la mitad y al final (un 4K de 3 GB se
    compara en milisegundos, sin leerlo entero)."""
    st = os.stat(p)
    key = (str(p), st.st_size, st.st_mtime_ns, st.st_ino)
    if key in _QHASH:
        return _QHASH[key]
    n = st.st_size
    h = hashlib.sha1(str(n).encode())
    with open(p, "rb") as f:
        for off in sorted({0, max(0, n // 2 - DEDUP_CHUNK // 2), max(0, n - DEDUP_CHUNK)}):
            f.seek(off)
            h.update(f.read(DEDUP_CHUNK))
    if len(_QHASH) > 2000:
        _QHASH.clear()
    _QHASH[key] = f"{n}:{h.hexdigest()}"
    return _QHASH[key]


def _import_candidates(exclude):
    """Importaciones guardadas: videos de los trabajos (input/<id>.<ext>) y fuentes subidas
    originales (input/sources/<sid>.<ext>). Solo archivos reales dentro de DATA."""
    ex = Path(exclude)
    out = [p for p in _ls(INPUT) if p.is_file() and not p.is_symlink() and p != ex
           and ".part" not in p.name and not p.name.startswith(".")]
    out += [p for p in _ls(SOURCES) if not p.is_symlink() and p != ex and _is_source_original(p)]
    return out


def _find_twin(p, candidates, full=False):
    """Otro archivo IDENTICO a `p` (mismo tamano y misma huella rapida; con full=True ademas byte
    a byte) en el mismo volumen, o None. Nunca el mismo inodo."""
    import filecmp
    try:
        st = os.stat(p)
    except OSError:
        return None
    mine = None
    for c in candidates:
        try:
            cs = os.stat(c)
        except OSError:
            continue
        if cs.st_size != st.st_size or cs.st_dev != st.st_dev or cs.st_ino == st.st_ino:
            continue
        try:
            mine = mine or _quick_hash(p)
            if _quick_hash(c) != mine:
                continue
            if full and not filecmp.cmp(str(c), str(p), shallow=False):
                continue
        except OSError:
            continue
        return Path(c)
    return None


def _link_to(p, twin):
    """Reemplaza `p` por un ENLACE DURO a `twin` (mismos bytes en un solo lugar del disco; borrar
    uno de los dos proyectos no afecta al otro). Atomico: enlace temporal + renombrado."""
    tmp = Path(p).with_name(f".{Path(p).name}.{uuid.uuid4().hex[:6]}.lnk")
    try:
        os.link(twin, tmp)
        os.replace(tmp, p)
        return True
    except OSError as e:
        tmp.unlink(missing_ok=True)
        print(f"   deduplicacion: sin enlace duro ({e}); se conserva la copia")
        return False


def dedupe_import(p, full=False, candidates=None):
    """Si `p` (recien importado) es identico a una importacion guardada, lo reemplaza por un
    enlace a esa copia -> Path del gemelo o None."""
    twin = _find_twin(p, _import_candidates(p) if candidates is None else candidates, full=full)
    if twin is not None and _link_to(p, twin):
        print(f"   importacion repetida: {Path(p).name} = {twin.name} (no se guarda otra copia)")
        _storage_invalidate()
        return twin
    return None


# ---- a la Papelera ----
def _trash_bin():
    b = os.environ.get("REELFY_TRASH_BIN")
    if b is not None:
        return b or None                 # "" = sin herramienta (pruebas / respaldo)
    return "/usr/bin/trash" if paths.IS_MAC and Path("/usr/bin/trash").exists() else None


def _osascript():
    b = os.environ.get("REELFY_OSASCRIPT")
    if b is not None:
        return b or None
    return shutil.which("osascript") if paths.IS_MAC else None


def _to_trash(p):
    """Mueve `p` (dentro de DATA) a la Papelera del usuario: /usr/bin/trash (macOS 15+), si no el
    Finder (osascript) y, fuera de macOS o si ambos fallan, DATA/.trash. -> 'trash'|'finder'|'local'."""
    p = Path(p)
    if not _in_data(p):
        raise RuntimeError(f"fuera de la carpeta de Reelfy: {p}")
    b = _trash_bin()
    if b:
        try:
            subprocess.run([b, str(p)], capture_output=True, text=True, timeout=300)
        except Exception as e:  # noqa
            print(f"   papelera ({b}): {e}")
        if not os.path.lexists(p):
            return "trash"
    osa = _osascript()
    if osa:
        script = ('on run argv\ntell application "Finder" to delete (POSIX file (item 1 of argv) '
                  'as alias)\nend run')
        try:
            subprocess.run([osa, "-e", script, str(p)], capture_output=True, text=True, timeout=300)
        except Exception as e:  # noqa
            print(f"   papelera (Finder): {e}")
        if not os.path.lexists(p):
            return "finder"
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    dst = TRASH_DIR / p.name
    if os.path.lexists(dst):
        dst = TRASH_DIR / f"{p.name} {time.strftime('%Y-%m-%d %H.%M.%S')} {uuid.uuid4().hex[:4]}"
    os.rename(p, dst)
    return "local"


def _safe_name(s, n=60):
    s = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", " ", str(s or "")).strip(" .")
    return re.sub(r"\s+", " ", s)[:n].strip() or "Proyecto"


def _trash_project(job_id, title, items, manifest):
    """Junta los archivos del proyecto en UNA carpeta ("Reelfy - <nombre> (<id>)", con su
    proyecto.json y la estructura input/output/work de DATA) y la manda a la Papelera: un solo
    elemento que se puede recuperar a mano. Renombrar dentro del mismo volumen es instantaneo; lo
    que no se pueda juntar va a la Papelera suelto. -> (metodo, n_archivos)."""
    base = paths.DATA.resolve()
    folder = _STAGE / f"Reelfy - {_safe_name(title)} ({job_id})"
    if os.path.lexists(folder):
        folder = folder.with_name(f"{folder.name} {uuid.uuid4().hex[:4]}")
    folder.mkdir(parents=True)
    (folder / "proyecto.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1,
                                                     default=str))
    loose, n = [], 0
    for p in items:
        p = Path(p)
        if not os.path.lexists(p):
            continue
        if not _in_data(p):
            print(f"   eliminar {job_id}: se omite {p} (fuera de la carpeta de Reelfy)")
            continue
        try:
            rel = p.parent.resolve().relative_to(base) / p.name
            dst = folder / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(p, dst)              # un enlace simbolico se mueve como enlace
            n += 1
        except (OSError, ValueError):
            loose.append(p)
    how = _to_trash(folder)
    for p in loose:
        try:
            _to_trash(p)
            n += 1
        except Exception as e:  # noqa
            print(f"   eliminar {job_id}: {p}: {e}")
    try:
        _STAGE.rmdir()                     # vacia: no se deja la carpeta de paso en DATA
    except OSError:
        pass
    return how, n


# ---- proyectos: eliminar, liberar espacio ----
def _busy_job(job_id, job):
    """Motivo por el que el proyecto no se puede tocar ahora (o None)."""
    if (job or {}).get("state") in ("queued", "running"):
        return ("Este proyecto se está procesando (análisis o export). Espera a que termine o "
                "cancélalo.")
    if job_id in _EXPORTING:
        return "Este proyecto se está exportando. Espera a que termine o cancélalo."
    return None


def _quiet_job(job_id, names=None, wait=10.0):
    """Cancela y espera las tareas laterales del trabajo (leen y escriben sus archivos) y su
    analisis diferido. -> True si ya no corre ninguna."""
    with _TAKES_KICK_LOCK:
        tm = _TAKES_KICK.pop(job_id, None)
    if tm is not None:
        tm.cancel()
    for name in (names or [None]):
        _cancel_side(job_id=job_id, name=name, wait=wait)
    with _SIDE_LOCK:
        return not any(v["thread"].is_alive() for k, v in _SIDE.items()
                       if k[0] == job_id and (names is None or k[1] in names))


def _gb(b):
    return round(float(b or 0) / GB, 2)


def _rm_bytes(p):
    """Borra una ruta regenerable -> bytes que libera."""
    _a, b = _walk(p, set())
    return b if _rm(Path(p)) else 0


def _free_job_cache(job_id, ctx):
    """Borra lo REGENERABLE del proyecto: vistas previas, proxy 720p del Studio, proxies de sus
    fuentes (si no las comparte), intermedios del motor (base, voz, rostro) y restos del montaje.
    El video, el analisis, los exports y la edicion quedan: al abrirlo se regeneran solos."""
    u = _job_units(job_id, ctx)
    freed, n = 0, 0
    for p in u["cache"]:
        if os.path.lexists(p):
            freed += _rm_bytes(p)
            n += 1
            if p.parent == SOURCES:              # su proxy del editor se regenera al pedirlo
                _SRC_PROXY_KICKED.discard(_sid_of(p.name))
    if n:
        for name in ("proxy", "preview", "analysis"):
            if jobstore.side_get(job_id, name):
                jobstore.side_update(job_id, name, replace=True, state="freed",
                                     message="Espacio liberado: se vuelve a preparar al abrirlo")
    return freed, n


def _drop_job_memory(job_id):
    with _ENGINE_LOCK:
        _ENGINE.pop(job_id, None)
    _PHASE_SEEN.pop(job_id, None)
    with _SIDE_LOCK:
        for k in [k for k in _SIDE if k[0] == job_id]:
            _SIDE.pop(k, None)
        _JOB_LOCKS.pop(job_id, None)
        _THUMB_LOCKS.pop(job_id, None)


def _gc_lock(timeout=60.0):
    if not _GC_LOCK.acquire(timeout=timeout):
        raise HTTPException(409, "Se está limpiando el disco; inténtalo en unos segundos.")


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    """Elimina el proyecto: TODO lo que es solo suyo (su video, sus fuentes subidas que ningun otro
    proyecto usa, exports, vistas previas, proxies, analisis, musica e intermedios del motor) va a
    la Papelera en una carpeta con su proyecto.json, y sale de la lista. Nunca toca nada fuera de
    DATA (los videos originales). 409 mientras se analiza o exporta. -> {freed_bytes, freed_gb,
    trash: trash|finder|local, files, shared}. `freed` = lo que se libera al vaciar la Papelera."""
    job = _job_or_404(job_id)
    why = _busy_job(job_id, job)
    if why:
        raise HTTPException(409, why)
    if not _quiet_job(job_id):
        raise HTTPException(409, "Todavía se está cerrando una tarea de este proyecto; inténtalo "
                                 "en unos segundos.")
    _gc_lock()
    try:
        full = jobstore.get(job_id) or {}
        ctx = _storage_ctx()
        u = _job_units(job_id, ctx)
        items = u["project"] + u["exports"] + u["cache"]
        freed = sum(_walk(p, set())[1] for p in items)
        meta = (ctx["jobs"].get(job_id) or {})
        title = meta.get("topic") or f"Proyecto {job_id[:6]}"
        if not jobstore.delete(job_id):              # se encolo algo mientras tanto: gana eso
            raise HTTPException(409, _busy_job(job_id, jobstore.get_light(job_id)) or
                                "Este proyecto está ocupado.")
        manifest = {"reelfy": ENGINE_VERSION, "id": job_id, "title": title,
                    "deleted": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "files": [str(p) for p in items], "shared": [str(p) for p in u["shared"]],
                    "job": {k: full.get(k) for k in ("kind", "ext", "params", "plan", "created",
                                                      "updated", "plan_rev", "meta")}}
        try:
            how, n = _trash_project(job_id, title, items, manifest)
        except Exception as e:  # noqa: lo que ya se junto sigue en DATA/.eliminando (recuperable)
            traceback.print_exc()
            raise HTTPException(500, f"No se pudo mover el proyecto a la Papelera ({e}). Sus "
                                     f"archivos quedaron en {_STAGE}.")
    finally:
        _GC_LOCK.release()
    _drop_job_memory(job_id)
    _storage_invalidate()
    print(f"   proyecto {job_id} eliminado: {n} archivos a la papelera ({how}), {_gb(freed)} GB")
    return {"ok": True, "freed_bytes": freed, "freed_gb": _gb(freed), "trash": how, "files": n,
            "shared": [p.name for p in u["shared"]]}


@app.post("/jobs/{job_id}/free-space")
def free_job_space(job_id: str):
    """Libera espacio del proyecto SIN perderlo: borra solo lo regenerable (vistas previas, proxies,
    intermedios del motor de este proyecto). Sigue editable; al abrirlo se regenera en segundo
    plano. 409 mientras se analiza o exporta. -> {freed_bytes, freed_gb, files}."""
    job = _job_or_404(job_id)
    why = _busy_job(job_id, job)
    if why:
        raise HTTPException(409, why)
    if not _quiet_job(job_id, names=("analysis", "preview", "proxy")):
        raise HTTPException(409, "Todavía se está preparando este proyecto; inténtalo en unos "
                                 "segundos.")
    _gc_lock()
    try:
        freed, n = _free_job_cache(job_id, _storage_ctx())
    finally:
        _GC_LOCK.release()
    _storage_invalidate()
    return {"ok": True, "freed_bytes": freed, "freed_gb": _gb(freed), "files": n}


@app.post("/storage/empty-trash")
def empty_local_trash():
    """Vacia la papelera de RESPALDO de Reelfy (DATA/.trash: ahi van los proyectos eliminados cuando
    la Papelera del Mac no se pudo usar, p. ej. macOS 13/14 sin permiso para el Finder). Borrado
    definitivo; la UI lo confirma antes. Solo rutas dentro de DATA. -> {freed_bytes, freed_gb, files}."""
    _gc_lock()
    try:
        freed, n = 0, 0
        for p in _ls(TRASH_DIR):
            if _in_data(p):
                freed += _rm_bytes(p)
                n += 1
    finally:
        _GC_LOCK.release()
    _storage_invalidate()
    return {"ok": True, "freed_bytes": freed, "freed_gb": _gb(freed), "files": n}


@app.get("/storage")
def storage(refresh: int = 0):
    """Disco que usa Reelfy: total y por categoria (exports / cache regenerable / proyectos +
    modelos), por proyecto ({id: {bytes, frees_bytes, cache_frees_bytes, ...}}), libre del disco
    y el aviso de poco espacio. `freeable_bytes` = lo que libera "Liberar espacio";
    `local_trash_bytes` = la papelera de respaldo (POST /storage/empty-trash). En cache
    (`age_s`); ?refresh=1 lo recalcula."""
    return storage_snapshot(force=bool(refresh))


@app.post("/storage/free-space")
def free_all_space():
    """"Liberar espacio" global: lo regenerable de TODOS los proyectos que no se estan procesando
    (los ocupados se saltan) y los intermedios del motor que ya no son de ningun proyecto. Los
    proyectos siguen editables. -> {freed_bytes, freed_gb, jobs, skipped}."""
    _gc_lock()
    try:
        ctx = _storage_ctx()
        freed, touched, skipped = 0, 0, []
        with _SIDE_LOCK:
            active = {k[0] for k, v in _SIDE.items() if v["thread"].is_alive()}
        for jid, j in ctx["jobs"].items():
            if _busy_job(jid, j) or jid in active:     # tareas de ese proyecto en curso: se salta
                skipped.append(jid)
                continue
            f, n = _free_job_cache(jid, ctx)
            freed += f
            touched += 1 if n else 0
        if not _busy():                       # intermedios sin dueno (ningun render los usa ahora)
            mc = paths.WORK / "motion_cache"
            owned = set(ctx["mc"])
            now = time.time()
            for p in _ls(mc):
                try:
                    old = now - p.lstat().st_mtime > 600
                except OSError:
                    continue
                if p.name not in owned and old:
                    freed += _rm_bytes(p)
    finally:
        _GC_LOCK.release()
    _storage_invalidate()
    return {"ok": True, "freed_bytes": freed, "freed_gb": _gb(freed), "jobs": touched,
            "skipped": skipped}


def _claim_cache(job_id, d):
    """Anota los intermedios que el motor genero para el trabajo (base, voz, rostro)."""
    try:
        names = [Path(str(d[k])).name for k in ("base", "voice", "face_path")
                 if isinstance(d, dict) and d.get(k)]
        if names:
            jobstore.claim_cache(job_id, names)
    except Exception:  # noqa: es contabilidad, nunca tumba un render
        traceback.print_exc()


if not _CHILD:
    jobstore.init()
    jobstore.start_workers({"analyze": _analyze, "render": _render, "compose": _compose,
                            "montage": _montage}, lanes=("montage",))
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
    await run_in_threadpool(dedupe_import, dst)   # el mismo video otra vez: un enlace, no otra copia
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
            if _SHUTDOWN.is_set():         # la app se cierra: ningun ffmpeg nuevo
                return None
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


def _ingest_source(dst, sid, name, is_img, make_proxy=True, transcribe=True):
    """Probe + miniatura de una fuente subida (bloqueante: corre en el threadpool). El proxy
    del editor se genera en SEGUNDO PLANO (no retrasa la respuesta ni el event loop);
    make_proxy=False cuando ya se reutilizo el de una importacion identica. Un video entra al
    carril de transcripcion por clip ("Armar con IA"; transcribe=False lo omite)."""
    import compose as compose_mod
    info = compose_mod.probe(dst)
    if not is_img and transcribe:
        _words_kick([sid])
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
        _SRC_PROXY_KICKED.add(sid)       # /tlsource no lanza otro mientras este se codifica
    if not is_img and make_proxy:
        threading.Thread(target=_make_proxy, args=(dst, sid, info), daemon=True,
                         name=f"reelfy-proxy-{sid}").start()
    return info, thumb


def _reuse_source_proxy(old_sid, sid):
    """Importacion identica a la fuente `old_sid`: su proxy del editor tambien se comparte (enlace
    duro) en vez de volver a codificarlo. -> True si quedo enlazado."""
    pv = SOURCES / f"{old_sid}_proxy.mp4"
    if not pv.is_file():
        return False
    try:
        os.link(pv, SOURCES / f"{sid}_proxy.mp4")
        return True
    except OSError:
        return False


_SRC_PROXY_KICKED = set()


def _kick_source_proxy(sid, p):
    """El proxy del editor de una fuente se borro ("Liberar espacio"): se regenera en segundo plano
    la primera vez que se pide (mientras tanto se sirve el original). Una vez por proceso y fuente
    (_make_proxy no hace nada si la fuente no lo necesita); nunca junto al de la importacion (dos
    ffmpeg sobre el mismo temporal)."""
    if sid in _SRC_PROXY_KICKED or p.suffix.lower() not in (".mp4", ".mov", ".m4v", ".mkv", ".webm"):
        return False
    _SRC_PROXY_KICKED.add(sid)

    def run():
        try:
            info = _source_meta(sid)
            if not info.get("w"):
                import compose as compose_mod
                info = compose_mod.probe(p)
            _make_proxy(p, sid, info)
        except Exception:  # noqa: el proxy es una optimizacion
            traceback.print_exc()
    threading.Thread(target=run, daemon=True, name=f"reelfy-proxy-{sid}").start()
    return True


@app.post("/sources")
async def add_sources(files: list[UploadFile], transcribe: str = Form("1")):
    """Sube uno o varios archivos fuente (video o imagen) para el editor de timeline.
    Devuelve por cada uno (_source_item): id, nombre, tipo, duración, dimensiones, miniatura,
    fecha de grabacion (`created` ISO 8601: com.apple.quicktime.creationdate, creation_time o la
    fecha del archivo; `created_src` dice cual), fps, hdr (bool), rotacion, `audio`, estado de su
    transcripcion por clip (`words`: pending|ready|error; null en imagenes) y `rec` (lugar en el
    orden de grabacion de ESTA subida). Cada video se transcribe solo en segundo plano (carril
    propio); `transcribe=0` lo omite (p. ej. un medio propio del Studio)."""
    SOURCES.mkdir(parents=True, exist_ok=True)
    out = []
    want_words = str(transcribe).strip().lower() not in ("0", "false", "no")
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
        # el mismo archivo ya importado (este u otro proyecto): un enlace a esa copia, no otra
        twin = await run_in_threadpool(dedupe_import, dst)
        twin_src = twin is not None and twin.parent == SOURCES and not is_img
        reused = twin_src and await run_in_threadpool(_reuse_source_proxy, _sid_of(twin.name), sid)
        if twin_src:            # su transcripcion por clip tambien (mismo contenido, misma huella)
            await run_in_threadpool(_reuse_source_words, _sid_of(twin.name), sid)
        info, thumb = await run_in_threadpool(_ingest_source, dst, sid, up.filename or "", is_img,
                                              not reused, want_words)
        item = await run_in_threadpool(_source_item, sid, dst,
                                       {"name": up.filename or "", **info}, bool(thumb))
        if twin is not None:
            item["dedup"] = True
        out.append(item)
    for r, k in enumerate(rec_order(out)):
        out[k]["rec"] = r
    _storage_invalidate()
    return {"sources": out}


@app.get("/sources")
def list_sources(ids: str = ""):
    """Fuentes subidas con sus metadatos (mismo formato que POST /sources) y su orden de grabacion
    (`rec`). `ids` = sids separados por coma (en ese orden; los que no existen se omiten); sin ids,
    todas. El editor lo sondea para ver `words` (pending -> ready|error) de cada clip; lo pendiente
    que el carril no tenga (p. ej. tras reiniciar la app) se vuelve a encolar."""
    if ids.strip():
        sids = list(dict.fromkeys(Path(s).stem for s in re.split(r"[,\s]+", ids) if s.strip()))
    else:
        sids = sorted({_sid_of(p.name) for p in _ls(SOURCES) if _is_source_original(p)})
    out = []
    for sid in sids:
        src = _resolve_source(sid)
        if src is None:
            continue
        out.append(_source_item(sid, src))
    for r, k in enumerate(rec_order(out)):
        out[k]["rec"] = r
    pend = [it["id"] for it in out if it.get("words") == "pending"]
    if pend and ids.strip():          # solo lo que se pide (listar todo no transcribe fuentes viejas)
        _words_kick(pend)
    return {"sources": out}


@app.get("/sources/{sid}/words")
def source_words(sid: str):
    """Transcripcion por clip: {"key", "sid", "words": [{text, start, end}], "takes": [cortes de
    tomas dentro del clip], "audio", "dur", ...} (segundos del clip). 404 mientras no este lista
    (`words`: pending|error en el cuerpo; lo pendiente se encola) o si la fuente no existe."""
    sid = Path(sid).stem
    src = _resolve_source(sid)
    if src is None:
        raise HTTPException(404, "No existe")
    if _is_image(src):
        return JSONResponse({"detail": "Una imagen no tiene voz.", "words": None}, status_code=404)
    st = _words_state(sid, src)
    if st != "ready":
        if st == "pending":
            _words_kick([sid])
        return JSONResponse({"detail": "La transcripción de este clip aún no está lista." if
                             st == "pending" else "La transcripción de este clip falló.",
                             "words": st, "error": _WORDS_ERR.get(sid)}, status_code=404)
    return _words_load(sid, src)


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
        _kick_source_proxy(sid, p)       # borrado al liberar espacio: se regenera en 2o plano
        return FileResponse(p, headers={"Accept-Ranges": "bytes"})
    raise HTTPException(404, "No existe")


# ---- metadatos de las fuentes (/sources) ----
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic")


def _is_image(p):
    return Path(p).suffix.lower() in _IMG_EXT


def _source_meta_full(sid, src=None):
    """_meta.json de la fuente con fecha de grabacion y fps (una fuente importada antes de que el
    probe los trajera se completa una vez y se guarda)."""
    meta = _source_meta(sid)
    if meta.get("created") and "fps" in meta:
        return meta
    src = src or _resolve_source(sid)
    if src is None:
        return meta
    try:
        import compose as compose_mod
        info = compose_mod.probe(src)
        meta = {**info, **{k: v for k, v in meta.items() if k not in info}}
        meta.setdefault("name", src.name)
        (SOURCES / f"{sid}_meta.json").write_text(json.dumps(meta))
    except Exception:  # noqa: sin metadatos la fuente sigue sirviendo
        traceback.print_exc()
    return meta


def _source_item(sid, src=None, meta=None, thumb=None):
    """Fuente -> item de /sources: {id, name, type, kind (= type: image|video), ext, dur, w, h, thumb,
    created, created_src, fps, hdr (bool), rotation, audio, words (pending|ready|error; None en
    imagenes)}."""
    src = Path(src) if src else _resolve_source(sid)
    meta = meta if meta is not None else _source_meta_full(sid, src)
    img = _is_image(src) if src else meta.get("type") == "image"
    if thumb is None:
        thumb = (SOURCES / f"{sid}_t.jpg").exists()
    return {"id": sid, "name": meta.get("name") or (src.name if src else ""),
            "type": "image" if img else "video", "kind": "image" if img else "video",
            "ext": (src.suffix.lower() if src else ""),
            "dur": meta.get("dur", 0), "w": meta.get("w", 0), "h": meta.get("h", 0),
            "thumb": f"/tlsource/{sid}/thumb" if thumb else None,
            "created": meta.get("created"), "created_src": meta.get("created_src"),
            "created_ts": meta.get("created_ts"), "fps": meta.get("fps") or 0.0,
            "hdr": bool(meta.get("hdr")), "rotation": int(meta.get("rotation") or 0),
            "audio": bool(meta.get("audio")) if not img else False,
            "words": None if img else _words_state(sid, src)}


# ---- transcripcion por clip ("Armar con IA"): carril propio al importar ----
# Cada video importado (/sources) se transcribe SOLO, en segundo plano (nunca concatenado con
# otros: una pasada sobre todos juntos perdio una toma entera y corrio palabras entre clips):
# pipeline.transcribe_clip = 1a pista de audio a 16 kHz -> whisper -> alineador -> tomas dentro del
# clip. Queda en input/sources/{sid}_words.json ({key, sid, words, takes, audio, dur, ...}) y
# {sid}_16k.wav (el montaje mide la voz y ajusta los cortes con el). Un whisper a la vez con el del
# analisis (pipeline.WHISPER_GPU). La limpieza del disco los borra con su fuente (mismo prefijo
# sid); "Liberar espacio" borra solo el wav (regenerable en segundos: la transcripcion se conserva);
# una importacion repetida reusa la de su gemela. Tras reiniciar la app lo pendiente se retoma al
# pedirlo (GET /sources, /sources/{sid}/words o un montaje). Estado: ready (json valido para esta
# fuente + wav si tiene audio), error (fallo en este proceso; un montaje nuevo lo reintenta) o pending.
WORDS_VERSION = 1
_WORDS_LOCK = threading.Lock()
_WORDS_WAKE = threading.Event()
_WORDS_Q = []                      # sids en cola (un montaje adelanta los suyos)
_WORDS_RUN = {}                    # sid -> t0 del que se transcribe
_WORDS_ERR = {}                    # sid -> mensaje del fallo
_WORDS_THREAD = [None]


def _words_paths(sid):
    return SOURCES / f"{sid}_words.json", SOURCES / f"{sid}_16k.wav"


def _words_load(sid, src=None):
    """Transcripcion guardada de la fuente si es de ESTA fuente (misma version y huella) o None."""
    jp, _wav = _words_paths(sid)
    try:
        doc = json.loads(jp.read_text())
    except (OSError, ValueError):
        return None
    src = src or _resolve_source(sid)
    key = doc.get("key") if isinstance(doc, dict) else None
    if src is None or not isinstance(key, dict) or key.get("v") != WORDS_VERSION:
        return None
    try:
        if key.get("hash") != _quick_hash(src):
            return None
    except OSError:
        return None
    return doc


def _has_words(sid):
    """La fuente ya tiene su transcripcion con palabras (es una toma con voz)."""
    try:
        doc = _words_load(sid)
    except Exception:  # noqa: sin transcripcion legible = sin palabras
        return False
    return bool(doc and doc.get("words"))


def _words_state(sid, src=None):
    """ready | error | pending (None: no existe o es imagen)."""
    src = src or _resolve_source(sid)
    if src is None or _is_image(src):
        return None
    doc = _words_load(sid, src)
    if doc is not None and (not doc.get("audio") or _words_paths(sid)[1].exists()):
        return "ready"
    with _WORDS_LOCK:
        if sid in _WORDS_ERR and sid not in _WORDS_Q and sid not in _WORDS_RUN:
            return "error"
    return "pending"


def _words_kick(sids, front=False, retry=False):
    """Encola la transcripcion de estos clips (los que no esten listos, en cola o corriendo).
    `front`: al principio de la cola (un montaje que los espera); `retry`: tambien los que fallaron.
    Sin el modelo de voz descargado no encola nada (quedan pending). -> cuantos encolo."""
    if not paths.engine_ready():
        return 0
    todo = []
    for sid in dict.fromkeys(sids):
        src = _resolve_source(sid)
        if src is None or _is_image(src):
            continue
        st = _words_state(sid, src)
        if st == "ready" or (st == "error" and not retry):
            continue
        todo.append(sid)
    n = pos = 0
    with _WORDS_LOCK:
        for sid in todo:
            if sid in _WORDS_RUN:
                continue
            _WORDS_ERR.pop(sid, None)
            if sid in _WORDS_Q:
                if not front:
                    continue
                _WORDS_Q.remove(sid)        # adelantarlo (en el orden pedido)
            else:
                n += 1
            if front:
                _WORDS_Q.insert(pos, sid)
                pos += 1
            else:
                _WORDS_Q.append(sid)
        th = _WORDS_THREAD[0]
        if (n or _WORDS_Q) and (th is None or not th.is_alive()):
            th = threading.Thread(target=_words_loop, daemon=True, name="reelfy-words")
            _WORDS_THREAD[0] = th
            th.start()
        if _WORDS_Q:
            _WORDS_WAKE.set()
    return n


def _words_loop():
    """Carril de transcripcion: un clip a la vez (whisper comparte la GPU con el analisis)."""
    while not _SHUTDOWN.is_set():
        with _WORDS_LOCK:
            sid = _WORDS_Q.pop(0) if _WORDS_Q else None
            if sid is not None:
                _WORDS_RUN[sid] = time.time()
        if sid is None:
            _WORDS_WAKE.wait(timeout=5.0)
            _WORDS_WAKE.clear()
            continue
        try:
            _words_build(sid)
        except Exception as e:  # noqa: un clip que falla no frena a los demas
            traceback.print_exc()
            with _WORDS_LOCK:
                _WORDS_ERR[sid] = str(e)[:300] or e.__class__.__name__
        finally:
            with _WORDS_LOCK:
                _WORDS_RUN.pop(sid, None)
            _storage_invalidate()


def _transcribe_clip(src, wav):
    """pipeline.transcribe_clip (las pruebas lo cambian por uno falso)."""
    return pipeline.transcribe_clip(src, wav)


def _words_build(sid):
    """Transcribe la fuente `sid` y guarda {sid}_words.json + {sid}_16k.wav (temporal + renombrado:
    nunca queda a medias). Con la transcripcion valida y sin el wav (lo borro "Liberar espacio")
    solo re-extrae el audio."""
    src = _resolve_source(sid)
    if src is None or _is_image(src):
        return None
    jp, wav = _words_paths(sid)
    tmp_wav = wav.with_name(f"{sid}_16k.part.wav")
    doc = _words_load(sid, src)
    if doc is not None:
        if doc.get("audio") and not wav.exists():
            pipeline.extract_audio(src, tmp_wav)
            tmp_wav.replace(wav)
        return doc
    t0 = time.time()
    try:
        res = _transcribe_clip(src, tmp_wav)
        if tmp_wav.exists():
            tmp_wav.replace(wav)
    finally:
        tmp_wav.unlink(missing_ok=True)
    try:
        model = paths.model_path().name
    except Exception:  # noqa
        model = None
    doc = {"key": {"v": WORDS_VERSION, "hash": _quick_hash(src), "model": model,
                   "dtw": bool(res.get("dtw")), "glossary": ""},
           "sid": sid, "audio": bool(res.get("audio")), "dur": res.get("dur"),
           "words": res.get("words") or [], "takes": res.get("takes") or [],
           "align": res.get("align"), "ms": res.get("ms") or int((time.time() - t0) * 1000),
           "at": round(time.time(), 1)}
    tmp = jp.with_name(f"{sid}_words.part.json")
    tmp.write_text(json.dumps(doc, ensure_ascii=False))
    tmp.replace(jp)
    print(f"   palabras {sid}: {len(doc['words'])} en {time.time() - t0:.1f}s"
          f"{'' if doc['audio'] else ' (sin audio)'}")
    return doc


def _reuse_source_words(old_sid, sid):
    """Importacion identica a la fuente `old_sid`: su transcripcion por clip (misma huella) y su
    audio 16 kHz (enlace duro) se comparten. -> True si quedo lista."""
    doc = _words_load(old_sid)
    if doc is None:
        return False
    jp, wav = _words_paths(sid)
    ow = _words_paths(old_sid)[1]
    try:
        if doc.get("audio") and ow.is_file():
            os.link(ow, wav)
        tmp = jp.with_name(f"{sid}_words.part.json")
        tmp.write_text(json.dumps(dict(doc, sid=sid), ensure_ascii=False))
        tmp.replace(jp)
        return True
    except OSError:
        return False


@app.post("/compose")
def compose_timeline(body: dict = Body(...)):
    """Recibe la composición del timeline y arranca compose->analyze->director en un job.
    body: {main:[{sid,type,in,out|dur}], overlays:[{sid,type,start,dur,pos,size}],
           glossary, highlights, director:{style,intensity,seed,music},
           engine?:{air, enhance_audio, logo}, script?, montage_job?}. `def`: resuelve fuentes en
    disco y escribe sqlite en el threadpool.
    Montaje de "Armar con IA": `montage_job` = id del trabajo de /montage ya armado (fase
    assembly): se re-encola ESE MISMO id como compose (la UI sigue el mismo proyecto) conservando
    plan.montage y su guion; `script` (texto) = el guion editado en el ensamble (si no, el del
    montaje). Cada item principal puede traer "montage": {slot, cand, join, conf, setup?} y "vol":
    numero | "auto" (nivel de la voz medido; compose lo iguala entre segmentos). Director, perfil y
    glosario que no vengan se toman del montaje."""
    _require_engine()
    _require_license()
    mjob = str(body.get("montage_job") or "").strip() or None
    mparams, mont, stale_thumbs = {}, None, False
    if mjob:
        mj = jobstore.get(mjob)
        if not mj:
            raise HTTPException(404, "Montaje no encontrado")
        if mj.get("state") in ("queued", "running"):
            raise HTTPException(409, "El montaje todavía se está armando (o componiendo); espera a "
                                     "que termine.")
        mont = (mj.get("plan") or {}).get("montage") if isinstance(mj.get("plan"), dict) else None
        if not isinstance(mont, dict):
            raise HTTPException(409, "Este proyecto no tiene un montaje para aplicar.")
        if mjob in _EXPORTING:
            raise HTTPException(409, "Este proyecto se está exportando; espera a que termine.")
        mparams = mj.get("params") or {}
        if not jobstore.montage_only(mj.get("plan")):
            # ya compuesto (volvio al ensamble y cambio tomas): sus tareas de fondo leen el video
            # que se va a rehacer y la miniatura ya no es la suya
            if not _quiet_job(mjob):
                raise HTTPException(409, "Todavía se está cerrando una tarea de este proyecto; "
                                         "inténtalo en unos segundos.")
            stale_thumbs = True
    main, missing = [], []
    for s in body.get("main", []):
        path = _resolve_source(s.get("sid", ""))
        if not path:
            # una fuente que ya no esta en el disco (p. ej. la limpieza la borro): no se salta en
            # silencio (el video saldria sin ese clip)
            missing.append(str(s.get("sid") or "?"))
            continue
        item = {"path": str(path), "sid": path.stem, "type": s.get("type", "video"),
                "name": _source_meta(path.stem).get("name") or path.name,
                "start": float(s.get("start", 0) or 0), "layer": int(s.get("layer", 0) or 0),
                "fadeIn": float(s.get("fadeIn", 0) or 0), "fadeOut": float(s.get("fadeOut", 0) or 0)}
        if isinstance(s.get("trans"), dict) and s["trans"].get("type") == "crossfade":
            item["trans"] = {"type": "crossfade", "dur": float(s["trans"].get("dur", 0.5))}
        mm = _montage_item(s.get("montage"))
        if mm:
            item["montage"] = mm
        if item["type"] == "image":
            item["dur"] = float(s.get("dur", 3.0))
        else:
            item["in"] = float(s.get("in", 0)); item["out"] = float(s.get("out", 0) or 5)
            for k in ("ramp_in", "ramp_out"):
                r = _ramp_param(s.get(k), path.stem, item["in"], item["out"], k)
                if r:
                    item[k] = r
            item["vol"] = _vol_param(s.get("vol", None if mm else 1.0), bool(mm))
            item["mute"] = bool(s.get("mute", False))
            if item["vol"] == "auto":
                # la voz del tramo (s de la fuente): compose mide su nivel solo en las palabras
                item["words"] = _words_in(path.stem, item["in"], item["out"])
        main.append(item)
    if missing:
        names = [n for n in ((_montage_name(mont, sid) or "") for sid in missing) if n]
        raise HTTPException(400, f"Faltan {len(missing)} clip{'s' if len(missing) > 1 else ''} de tu línea de "
                                 f"tiempo en el disco{(': ' + ', '.join(names[:5])) if names else ''}. "
                                 "Vuelve a añadirlos desde Fuentes o quítalos del timeline.")
    if not main:
        raise HTTPException(400, "Agrega al menos un clip a la pista principal.")
    if stale_thumbs:                    # ya validado: la miniatura del video que se rehace se va
        for f in _thumb_paths(mjob):
            f.unlink(missing_ok=True)
    if any(it.get("vol") == "auto" for it in main):
        # una toma con volumen "auto" se nivela contra los tramos del montaje ya nivelados: sus voces
        # se miden igual (solo en las palabras)
        for it in main:
            if it.get("montage") and it.get("vol") != "auto" and it.get("type") == "video" and "words" not in it:
                it["words"] = _words_in(it["sid"], it["in"], it["out"])
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
    # perfil y glosario: lo que el cuerpo DICE manda (profile: null = "Sin perfil", glossary: "" =
    # borrado a proposito); solo si el cuerpo no los menciona se usan los del montaje
    if "profile" in body or "profile_id" in body:
        prof = _profile_param(body.get("profile"), body.get("profile_id"))
    else:
        prof = mparams.get("profile") or None
    script = _script_param(body.get("script")) if body.get("script") is not None else \
        mparams.get("script")
    glossary = body.get("glossary") if "glossary" in body else mparams.get("glossary")
    glossary = "" if glossary is None else glossary
    director = body.get("director") if body.get("director") is not None else mparams.get("director")
    params = {"spec": spec, "glossary": str(glossary or ""), "n": int(body.get("highlights", 2)),
              "director": _director_params(director), **({"profile": prof} if prof else {})}
    if script:
        params["script"] = script
    if mjob:
        # TODAS las fuentes de la sesion (tambien las alternativas no usadas): la limpieza del disco
        # no las borra mientras el proyecto exista (el usuario puede volver al ensamble)
        params["sids"] = list(dict.fromkeys([s for s in mparams.get("sids") or [] if isinstance(s, str)]
                                            + [it["sid"] for it in main]))
        params["montage_job"] = True
        for k in ("assets", "asset_items"):
            if mparams.get(k):
                params[k] = mparams[k]
        if isinstance(body.get("inserts"), list):
            # recursos del guion como quedaron en el ensamble (tiempos del ensamble, archivo, quitados):
            # compose.finish_plan los lleva al video combinado (plan.fx.media)
            mont = dict(mont, inserts=_inserts_param(body["inserts"], mont))
            spec["inserts_timeline"] = "main"    # ya en s del ensamble que se compone (no del montaje guardado)
        if mont.get("inserts"):
            spec["inserts"] = [dict(x) for x in mont["inserts"] if x.get("sid") and x.get("enabled", True)]
        jobstore.requeue(mjob, kind="compose", params=params, plan={"montage": mont},
                         phase="analyzing", message="En cola…")
        _initial_engine(mjob, body.get("engine"))
        _gc_later("nuevo trabajo")
        return {"job_id": mjob}
    job_id = uuid.uuid4().hex[:12]
    jobstore.create(job_id, kind="compose", ext=".mp4", params=params)
    _initial_engine(job_id, body.get("engine"))
    _gc_later("nuevo trabajo")
    return {"job_id": job_id}


RAMP_MAX_DUR, RAMP_MAX_SPEED = 1.5, 4.0


def _ramp_param(raw, sid, a, b, side):
    """"ramp_in"/"ramp_out" de un item de /compose -> {dur, speed} o None. Solo si la fuente tiene los
    cuadros de sobra: ramp_in empieza dur*(speed-1) s antes de `in`; ramp_out sigue ese tanto despues
    de `out` (el audio siempre es [in, out] a 1x)."""
    if not isinstance(raw, dict):
        return None
    d, v = _fnum(raw.get("dur")), _fnum(raw.get("speed")) if raw.get("speed") is not None else 2.0
    if d is None or v is None or d <= 0.01 or v <= 1.0001:
        return None
    d, v = min(RAMP_MAX_DUR, d), min(RAMP_MAX_SPEED, v)
    if d > (b - a) - 0.05:
        return None
    extra = d * (v - 1.0)
    if side == "ramp_in" and a - extra < -1e-3:
        return None
    if side == "ramp_out":
        sd = float(_source_meta(sid).get("dur") or 0)
        if sd > 0 and b + extra > sd + 1e-3:
            return None
    return {"dur": round(d, 3), "speed": round(v, 3)}


def _inserts_param(raw, mont):
    """"inserts" de /compose (recursos del guion como quedaron en el ensamble) -> lista saneada con la
    ruta de cada archivo. Un recurso que el montaje no conoce o con tiempos invalidos se ignora; uno
    SIN LUGAR (start y end null: su linea no esta en el video) se conserva sin tiempos (no ocupa
    tiempo ni recorta a otro; al volver al ensamble se coloca si su linea tiene toma); sin archivo
    queda "missing"."""
    known = {str(x.get("id")): x for x in (mont or {}).get("inserts") or [] if isinstance(x, dict)}
    out = []
    for x in raw[:200]:
        if not isinstance(x, dict):
            continue
        iid = str(x.get("id") or "")[:40]
        base = known.get(iid)
        if base is None:
            continue
        a, b = _fnum(x.get("start")), _fnum(x.get("end"))
        unplaced = x.get("start") is None and x.get("end") is None
        if not unplaced and (a is None or b is None or a < 0 or b - a < 0.1):
            continue
        sid = Path(str(x.get("sid") or "")).stem or None
        src = _resolve_source(sid) if sid else None
        ins = {"id": iid, "line": base.get("line"), "name": str(x.get("name") or base.get("name") or "")[:200],
               "sid": sid if src else None, "kind": ("image" if _is_image(src) else "video") if src else None,
               "start": None if unplaced else round(a, 3), "end": None if unplaced else round(b, 3),
               "fit": x.get("fit") if x.get("fit") in ("hold", "speed", "trim") else "hold",
               "speed": max(0.25, min(4.0, _fnum(x.get("speed")) or 1.0)),
               "src_in": max(0.0, _fnum(x.get("src_in")) or 0.0),
               "status": "ok" if src else "missing", "enabled": x.get("enabled", True) is not False}
        if src:
            ins["path"] = str(src)
            ins["media_dur"] = float(_source_meta(sid).get("dur") or 0)
        out.append(ins)
    return out

def _montage_name(mont, sid):
    """Nombre del clip `sid` en el orden de grabacion de un montaje (None si no esta)."""
    order = mont.get("order") if isinstance(mont, dict) else None
    for o in order or []:
        if isinstance(o, dict) and o.get("sid") == sid:
            return o.get("name")
    return None


def _montage_item(raw):
    """"montage" de un item de /compose saneado -> {slot, cand, join, conf, setup?, ramp?, ramp_user?}
    o None."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in ("slot", "cand"):
        if raw.get(k) is not None:
            out[k] = str(raw[k])[:40]
    j = raw.get("join")
    out["join"] = j if j in ("section", "line", "splice") else None
    for k in ("ramp", "ramp_user"):          # cambio de camara con aceleracion (lo que eligio el usuario manda)
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    for k in ("conf", "setup"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            out[k] = v
        elif isinstance(v, str) and v:
            out[k] = v[:20]
    return out


def _vol_param(v, montage=False):
    """"vol" de un item: numero (0..4) o "auto" (nivel de la voz medido; tambien un item del
    montaje sin vol); lo demas -> 1.0."""
    if v == "auto" or (v is None and montage):
        return "auto"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(4.0, f)) if math.isfinite(f) else 1.0


def _words_in(sid, t0, t1):
    """Palabras de la transcripcion por clip dentro de [t0, t1] (s de la fuente) o None."""
    doc = _words_load(sid)
    if not doc:
        return None
    return [{"start": w["start"], "end": w["end"]} for w in doc.get("words") or []
            if float(w.get("end", 0)) > t0 and float(w.get("start", 0)) < t1] or None


def _script_param(raw):
    """Guion del cuerpo (texto o {text, source}) -> {"text", "source"} o None (vacio)."""
    if isinstance(raw, dict):
        text, source = raw.get("text"), raw.get("source")
    else:
        text, source = raw, None
    text = str(text or "").replace("\r\n", "\n").strip()
    if not text:
        return None
    if len(text) > SCRIPT_MAX_CHARS:
        raise HTTPException(400, f"El guion es demasiado largo (máximo {SCRIPT_MAX_CHARS} caracteres).")
    return {"text": text, "source": source if source in ("user", "detected") else "user"}


@app.post("/montage")
def montage_start(body: dict = Body(...)):
    """"Armar con IA": body {sids: [...] (TODOS los clips de la sesion), script?: str, glossary?,
    intercut?: bool (default true), llm?: bool (juez LLM local, default true si esta disponible),
    profile?, profile_id?, director?, engine?, job_id?} ->
    {job_id}. Trabajo kind "montage", fase "assembly" (carril propio): espera la transcripcion de
    cada clip (progreso real en /status: stage words|montage, words_done/words_total), elige la
    mejor toma de cada parte del guion y deja el resultado en plan.montage (GET /plan, /status?plan=1).
    Cancelable (POST /cancel). `job_id` = re-armar un montaje existente (otro guion) con el mismo id."""
    _require_engine()
    _require_license()
    raw = body.get("sids")
    raw_assets = body.get("assets") if isinstance(body.get("assets"), list) else []
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "Selecciona los clips de tu sesión de grabación.")
    script = _script_param(body.get("script"))
    sids, assets, bad = _montage_classify(raw + raw_assets, (script or {}).get("text"))
    if bad:
        raise HTTPException(400, f"Estos archivos no existen o no son video: {', '.join(bad[:5])}")
    if not sids:
        raise HTTPException(400, "Todos los archivos son recursos del guion (imágenes o videos que el "
                                 "guion pide agregar): selecciona también los clips de tu grabación.")
    if len(sids) > MONTAGE_MAX_CLIPS:
        raise HTTPException(400, f"Demasiados clips para un montaje (máximo {MONTAGE_MAX_CLIPS}).")
    prof = _profile_param(body.get("profile"), body.get("profile_id"))
    params = {"sids": sids, "script": script, "glossary": str(body.get("glossary") or "")[:500],
              "assets": assets,
              # {"sid": ...}: la limpieza del disco no borra los recursos mientras el proyecto exista
              "asset_items": [{"sid": a} for a in assets],
              "intercut": body.get("intercut", True) is not False, "llm": body.get("llm", True) is not False,
              **({"director": _director_params(body["director"])} if body.get("director") is not None
                 else {}),
              **({"profile": prof} if prof else {})}
    job_id = str(body.get("job_id") or "").strip() or None
    if job_id:
        j = jobstore.get(job_id)
        if not j:
            raise HTTPException(404, "Proyecto no encontrado")
        if j.get("state") in ("queued", "running"):
            raise HTTPException(409, "Este proyecto se está procesando; espera a que termine.")
        if j.get("kind") not in ("montage", "compose") or \
                (j.get("kind") == "compose" and not jobstore.montage_only(j.get("plan"))):
            raise HTTPException(409, "Este proyecto ya no es un montaje.")
        jobstore.requeue(job_id, kind="montage", params=params, phase="assembly", message="En cola…")
    else:
        job_id = uuid.uuid4().hex[:12]
        jobstore.create(job_id, kind="montage", ext=".mp4", params=params, phase="assembly")
    _initial_engine(job_id, body.get("engine"))
    _words_kick(sids, front=True, retry=True)
    return {"job_id": job_id}


def _montage_classify(raw, script_text):
    """Archivos elegidos para "Armar con IA" -> (clips, recursos, invalidos). Una imagen siempre es
    recurso; un video que el guion nombra ("- Agregar x") tambien; los demas videos son las tomas.
    Sin duplicados y en el orden recibido."""
    ids, bad = [], []
    for s in raw:
        sid = Path(str(s or "")).stem
        src = _resolve_source(sid) if sid else None
        if src is None:
            bad.append(str(s))
        elif sid not in ids:
            ids.append(sid)
    names = [_source_meta(sid).get("name") or (_resolve_source(sid) or Path(sid)).name for sid in ids]
    fn = _mt("classify")
    if fn is not None:
        try:
            files = [{"sid": sid, "name": n, "kind": "image" if _is_image(_resolve_source(sid)) else "video"}
                     for sid, n in zip(ids, names)]
            if script_text:
                # una toma ya transcrita (con voz) nunca pasa a recurso por un nombre solo parecido
                for f in files:
                    f["has_words"] = f["kind"] == "video" and _has_words(f["sid"])
            res = fn(files, script_text)
            return [f["sid"] for f in res["clips"]], [f["sid"] for f in res["assets"]], bad
        except Exception:  # noqa: la version minima
            traceback.print_exc()
    ref = set()
    for _i, _t, wanted in _script_refs(script_text):
        for n in wanted:
            k = _match_asset(n, names)
            if k >= 0:
                ref.add(ids[k])
    clips, assets = [], []
    for sid in ids:
        (assets if sid in ref or _is_image(_resolve_source(sid)) else clips).append(sid)
    return clips, assets, bad

@app.post("/montage/{job_id}/sources")
def montage_add_sources(job_id: str, body: dict = Body(...)):
    """Fuentes que el usuario agrego ("Añadir") en el ensamble de un montaje: quedan en los `sids`
    del proyecto para que la limpieza del disco no las borre mientras el proyecto exista (antes de
    /compose ningun trabajo las nombraba). body {sids: [...]} -> {sids: todas las del proyecto}."""
    j = jobstore.get(job_id)
    if not j:
        raise HTTPException(404, "Proyecto no encontrado")
    params = j.get("params") or {}
    if not isinstance(params, dict) or not isinstance(params.get("sids"), list):
        raise HTTPException(409, "Este proyecto no es un armado con IA.")
    raw = body.get("sids")
    if not isinstance(raw, list):
        raise HTTPException(400, "Faltan los clips (sids).")
    add = []
    for s in raw[:MONTAGE_MAX_CLIPS]:
        sid = Path(str(s or "")).stem
        if sid and _resolve_source(sid) is not None and sid not in add:
            add.append(sid)
    sids = list(dict.fromkeys([x for x in params["sids"] if isinstance(x, str)] + add))
    if sids != params["sids"]:
        # solo la lista de fuentes (el resto de los parametros no cambia)
        jobstore.update(job_id, params=dict(params, sids=sids))
    return {"sids": sids}


@app.post("/montage/{job_id}/asset")
def montage_set_asset(job_id: str, body: dict = Body(...)):
    """Recurso del guion que faltaba (o que el usuario reemplaza) en el ensamble de un armado con IA:
    body {sid (ya subido con POST /sources), name? | id?} -> los inserts con ese nombre (o ese id)
    apuntan a ese archivo (tipo, ajuste al hueco). -> {inserts, assets}. Solo antes de componer (en
    el Studio el recurso se cambia en su pista Recursos)."""
    j = jobstore.get(job_id)
    if not j:
        raise HTTPException(404, "Proyecto no encontrado")
    if j.get("state") in ("queued", "running"):
        raise HTTPException(409, "Este proyecto se está procesando; espera a que termine.")
    plan = j.get("plan") if isinstance(j.get("plan"), dict) else None
    m = (plan or {}).get("montage")
    if not isinstance(m, dict) or not jobstore.montage_only(plan):
        raise HTTPException(409, "Este proyecto ya se compuso: cambia el recurso en el Studio (pista Recursos).")
    sid = Path(str(body.get("sid") or "")).stem
    a = _asset_item(sid) if sid else None
    if a is None:
        raise HTTPException(400, "Ese archivo no existe; vuelve a subirlo.")
    name, iid = _asset_norm(body.get("name")), str(body.get("id") or "")
    hit = [x for x in m.get("inserts") or [] if isinstance(x, dict) and
           ((iid and str(x.get("id")) == iid) or (not iid and name and _asset_norm(x.get("name")) == name))]
    if not hit:
        raise HTTPException(404, "El guion no pide ese recurso.")
    for x in hit:
        x.update(sid=a["sid"], kind=a["kind"], path=a["path"], media_dur=a["dur"], status="ok")
        if _fnum(x.get("start")) is not None and _fnum(x.get("end")) is not None:
            f = _insert_fit(a["kind"], a["dur"], x["end"] - x["start"])
            if f.pop("len", None) is not None:           # un video mas corto dura lo suyo
                x["end"] = round(float(x["start"]) + float(a["dur"]), 3)
            x.update(f)
    assets = [y for y in m.get("assets") or [] if isinstance(y, dict) and y.get("sid") != a["sid"]]
    m["assets"] = assets + [{k: a.get(k) for k in ("sid", "name", "kind", "dur", "w", "h")}]
    params = j.get("params") if isinstance(j.get("params"), dict) else {}
    upd = {"plan": dict(plan, montage=m)}
    if a["sid"] not in (params.get("assets") or []):
        upd["params"] = dict(params, assets=list(params.get("assets") or []) + [a["sid"]],
                             asset_items=list(params.get("asset_items") or []) + [{"sid": a["sid"]}])
    jobstore.update(job_id, **upd)
    return {"inserts": m["inserts"], "assets": m["assets"]}

@app.post("/render/{job_id}")
def render(job_id: str, body: dict = Body(...)):
    """`def`: importa el motor (skia/cv2) y escribe sqlite en el threadpool."""
    job = _job_or_404(job_id)
    plan = body.get("plan") or _sym_stored(job_id, *jobstore.get_plan(job_id))[0]
    if not plan:
        raise HTTPException(409, "El job no tiene plan; analiza el video primero.")
    _not_montage(plan)
    if job["state"] in ("queued", "running"):
        raise HTTPException(409, "Este proyecto ya está procesándose; espera o cancela.")
    _require_license()
    if _is_v2(plan) and not body.get("legacy"):
        plan = _lm_plan(_synced_plan(job_id, plan))
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
    style, shots, thumb, size_bytes, frees_bytes, cache_frees_bytes, exports_bytes}] (`thumb` =
    URL de la miniatura; null sin video. Tamanos: GET /storage, en cache; `frees_bytes` = lo que
    libera eliminarlo, `cache_frees_bytes` = lo que libera "Liberar espacio")."""
    out = jobstore.recent(max(1, min(500, int(limit))))
    try:
        sizes = storage_snapshot().get("jobs") or {}
    except Exception:  # noqa: sin tamanos la lista sigue
        traceback.print_exc()
        sizes = {}
    for j in out:
        z = sizes.get(j["id"]) or {}
        j.update(size_bytes=z.get("bytes"), frees_bytes=z.get("frees_bytes"),
                 cache_frees_bytes=z.get("cache_frees_bytes"), exports_bytes=z.get("exports_bytes"))
        ext = j.get("ext")
        ok = bool(ext) and (INPUT / f"{j['id']}{ext}").exists()
        if ok and not _thumb_paths(j["id"])[0].exists():
            # un trabajo con error y sin plan (video que no se pudo leer) o cuya miniatura ya
            # fallo no anuncia miniatura (cada pedido relanzaria ffmpeg para nada)
            ok = not (j.get("state") == "error" and not j.get("has_plan")) and \
                not _thumb_paths(j["id"])[1].exists()
        j["thumb"] = f"/thumb/{j['id']}" if ok else None
        if not ok and j.get("phase") == "assembly":
            j["thumb"] = _montage_thumb(j["id"])     # montaje aun sin componer: su primer clip
    return out


def _montage_thumb(job_id):
    """Miniatura de un montaje en fase assembly: la del primer clip de su linea de tiempo (o de la
    sesion) -> URL o None."""
    try:
        plan, _ = jobstore.get_plan(job_id)
        m = (plan or {}).get("montage") or {}
        cand = [t.get("sid") for t in m.get("timeline") or [] if isinstance(t, dict)] + \
            [o.get("sid") for o in m.get("order") or [] if isinstance(o, dict)]
        if not cand:
            cand = list(((jobstore.get_light(job_id) or {}).get("params") or {}).get("sids") or [])
        for sid in cand:
            if sid and (SOURCES / f"{Path(str(sid)).stem}_t.jpg").exists():
                return f"/tlsource/{Path(str(sid)).stem}/thumb"
    except Exception:  # noqa: sin miniatura la lista sigue
        traceback.print_exc()
    return None


@app.post("/preview/{job_id}")
def preview(job_id: str, body: dict = Body(...)):
    """Vista previa. `pro: true` (plan v2): lanza la tarea `preview` (render.render_draft
    540p con audio, mismo pipeline que el export); progreso en /status (preview_pct,
    preview_stage, preview_file). Sin `pro`: el preview legacy (sincrono, en el threadpool)."""
    _job_or_404(job_id)
    plan = body.get("plan") or jobstore.get_plan(job_id)[0]
    if not plan:
        raise HTTPException(409, "El job no tiene plan; analiza el video primero.")
    _not_montage(plan)
    if body.get("pro"):
        return _start_preview_pro(job_id, _lm_plan(_synced_plan(job_id, plan)), _fmt_of(body), _engine_opts(body))
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
            _claim_cache(jid, stats)
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
    plan = _lm_plan(_synced_plan(job_id, _plan_from(body, job_id)))
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
    from motion.timeline import word_cut_spans
    fin = tl.map_words(tagged, drop_spans=word_cut_spans(pr["plan"].get("cuts")))
    bx, by, bw, bh = job["layout"]
    ctx = CX.FrameCtx(W=bw, H=bh, fps=job["fps"], quality="final", seed=job["seed"],
                      style=job["style"], accent=job.get("accent"), words=job.get("words") or [],
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
    if isinstance(plan, dict) and isinstance(plan.get("profile"), dict) and "profile" not in (body or {}):
        # la foto/logo de sus enlaces que ya bajaron entran en la edicion nueva
        plan = dict(plan, profile=_lm_ready(plan["profile"]) or plan["profile"])
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
    if only and isinstance(out, dict) and out.get("profile"):
        try:                                    # lo regenerado lleva la foto/logo del perfil
            out = director.apply_profile_media(out)[0]
        except Exception:  # noqa
            traceback.print_exc()
    if not (full and not only):
        out = _sym(_accent_keep(plan, out))     # (con full, _direct_plan ya lo simetrizo)
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
    plan, rev = _sym_stored(job_id, plan, rev)     # planes de antes de la regla de simetria
    plan, rev = _lm_stored(job_id, plan, rev)      # foto/logo de los enlaces que ya bajaron
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
    _media_paths(plan)
    ok, rev = jobstore.update_plan(job_id, plan, base)
    if ok and prof_applied:
        # el perfil cambio en el autoguardado: rotulo, Seguir, reservas y cierre ya van con los
        # datos nuevos (el Studio adopta `plan`)
        if base is not None:
            _music_superseded(job_id, plan, base, rev)
        return {"ok": True, "plan_rev": rev, "plan": plan, "profile_applied": True}
    if not ok:
        pm = _pm_side(job_id)
        if pm.get("state") == "applied" and base == pm.get("from_rev") and rev == pm.get("plan_rev"):
            # la unica revision posterior es la foto/logo del perfil que llego sola (el servidor la
            # puso sobre `from_rev`): la edicion del usuario gana y ya la lleva (_reapply_profile)
            ok, rev2 = jobstore.update_plan(job_id, plan, rev)
            if ok:
                jobstore.side_update(job_id, "profile_media", state="merged", plan_rev=rev2)
                return {"ok": True, "plan_rev": rev2, "plan": plan, "profile_applied": True}
            rev = rev2
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


# ---- limpieza de tomas (retomas, arranques en falso, autocorrecciones, tartamudeos, errores) ----
# scripts/takes.py sobre las palabras del plan guardado. Los cortes de tomas son registros src
# "takes" APARTE de los silencios; los que el usuario toco (user: true, via PUT /plan) se respetan.
# Cambiar la sensibilidad varias veces seguidas relanza UN solo analisis de fondo (debounce).
TAKES_KICK_S = float(os.environ.get("REELFY_TAKES_KICK_S", "5"))
_TAKES_KICK: dict = {}
_TAKES_KICK_LOCK = threading.Lock()


def _kick_analysis_later(job_id, fmt="9:16", delay=None):
    """Analisis de fondo del plan GUARDADO tras `delay` s sin otro cambio (como el Studio)."""
    delay = TAKES_KICK_S if delay is None else float(delay)

    def fire():
        with _TAKES_KICK_LOCK:
            if _TAKES_KICK.get(job_id) is not tm:
                return
            _TAKES_KICK.pop(job_id, None)
        try:
            plan, _ = jobstore.get_plan(job_id)
            if plan:
                _kick_analysis(job_id, plan, fmt)
        except Exception:  # noqa: el analisis de fondo es una optimizacion
            traceback.print_exc()
    tm = threading.Timer(max(0.0, delay), fire)
    tm.daemon = True
    with _TAKES_KICK_LOCK:
        old = _TAKES_KICK.get(job_id)
        if old is not None:
            old.cancel()
        _TAKES_KICK[job_id] = tm
    tm.start()
    return tm


@app.post("/takes/{job_id}")
def takes_run(job_id: str, body: dict = Body(default={})):
    """Re-detecta las tomas del trabajo. body {sensitivity: suave|normal|a_fondo, use_llm, base_rev}
    -> {plan_rev, takes (plan.takes), cuts (los src "takes")}. Reemplaza los cortes de tomas que el
    usuario no toco y conserva silencios, manuales, recortes 'ai' y los user: true. Concurrencia
    optimista como PUT /plan: con `base_rev` viejo (o si otro escritor gano mientras corria) -> 409
    con la revision actual. El tema (LLM) solo se pide si el plan aun no lo tiene. Apagar o
    restaurar una toma = PUT /plan con su `enabled` cambiado y user: true."""
    import takes as takes_mod
    _job_or_404(job_id)
    body = body or {}
    base = body.get("base_rev")
    try:
        base = int(base) if base is not None else None
    except (TypeError, ValueError):
        raise HTTPException(400, "base_rev inválido")
    sens = takes_mod.SENS_ALIASES.get(str(body.get("sensitivity") or "normal").strip().lower())
    if sens is None:
        raise HTTPException(400, "Sensibilidad inválida (suave, normal o a_fondo).")
    use_llm = body.get("use_llm", True) is not False
    plan, rev = jobstore.get_plan(job_id)
    if not plan:
        raise HTTPException(409, "El job no tiene plan; analiza el video primero.")
    _not_montage(plan)

    def conflict(cur):
        return JSONResponse({"ok": False, "detail": "El plan cambió en el servidor (re-dirigido u otra "
                                                     "ventana); recarga antes de limpiar las tomas.",
                             "plan_rev": cur}, status_code=409)
    if base is not None and base != rev:
        return conflict(rev)
    t0 = time.time()
    words = takes_mod.plan_words(plan)
    try:
        dur = float(plan.get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    wav = plan.get("wav")
    audio = wav if wav and Path(str(wav)).exists() else None     # sin wav: bordes solo por tiempos
    old_cuts = [c for c in plan.get("cuts") or [] if isinstance(c, dict)]
    kw = {}
    joins = _join_times(plan.get("shots"))          # montaje con IA: ningun corte cruza una union
    if joins and _accepts(takes_mod.detect, "joins"):
        kw["joins"] = joins
    res = takes_mod.detect(words, dur, sensitivity=sens, audio=audio, existing_cuts=old_cuts, **kw)
    cuts = takes_mod.merge(old_cuts, res["cuts"])
    meta = res["meta"]
    prev = plan.get("takes") if isinstance(plan.get("takes"), dict) else {}
    if prev.get("topic"):
        meta.update({k: prev.get(k) for k in ("topic", "summary", "sections", "llm")})
    elif use_llm:
        tp = takes_mod.topic(takes_mod.effective_words(words, cuts))
        if tp:
            meta.update(topic=tp["topic"], summary=tp["summary"], sections=tp["sections"], llm=True)
    if prev.get("sections_src") and prev.get("sections"):
        # secciones sembradas desde el guion del montaje: el tema del LLM no las reemplaza
        meta.update(sections=prev["sections"], sections_src=prev["sections_src"])
    meta["stats"] = takes_mod.stats(cuts)          # con las tomas del usuario que se respetaron
    meta["ms"] = int(round((time.time() - t0) * 1000))
    if cuts == old_cuts and {k: v for k, v in prev.items() if k != "ms"} == \
            {k: v for k, v in meta.items() if k != "ms"}:
        meta["ms"] = prev.get("ms", meta["ms"])    # nada cambio: mismo plan, misma revision
    new = dict(plan, cuts=cuts, takes=meta)
    if meta.get("topic") and not plan.get("topic"):
        new["topic"] = meta["topic"]
    ok, new_rev = jobstore.update_plan(job_id, new, rev)
    if not ok:
        return conflict(new_rev)
    if new_rev != rev:
        _music_superseded(job_id, new, rev, new_rev)
        _kick_analysis_later(job_id, _fmt_of(body))
    return {"plan_rev": new_rev, "takes": meta, "cuts": [c for c in cuts if c.get("src") == "takes"]}


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
    # foto/logo de sus enlaces: lo ya descargado entra ya; lo que falta se baja en segundo plano
    return _lm_ready(PR.normalize_profile(raw))


def _profile_key(prof):
    """Huella del perfil (sin id, fecha de guardado ni estado de la descarga): detecta un cambio
    real (incluida la foto/logo de un enlace)."""
    if not prof:
        return ""
    return json.dumps({k: v for k, v in prof.items() if k not in ("id", "updated", "media_status")},
                      sort_keys=True, ensure_ascii=False)


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
    raw = PR.normalize_profile(plan.get("profile"))
    new = _lm_ready(raw)                   # foto/logo ya descargados (cache, sin red)
    if _profile_key(new) == _profile_key(old):
        if _profile_key(raw) == _profile_key(new) or not _is_v2(plan):
            return plan, False
        # el Studio guardo con el perfil de ANTES de que llegara la foto/logo (mismo perfil): se
        # ponen sin re-dirigir ni mover nada
        from motion import director
        try:
            out, _ids = director.apply_profile_media(plan, new)
        except Exception:  # noqa
            traceback.print_exc()
            return plan, False
        return out, True
    if plan.get("version") != 2 or not isinstance(plan.get("fx"), dict):
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
    return _sym(_accent_keep(plan, out)), True


# ---- foto y logo de los enlaces del perfil (motion/linkmedia.py) ----
# Si el perfil menciona su Instagram, su pagina de PaseoClub u otra pagina, la foto publica de ese
# lugar (vista previa og:image; en Instagram la foto de perfil) y el logo del sitio se descargan
# SOLOS en segundo plano como medios propios (input/sources, formato de POST /sources). Ninguna
# peticion espera a la red: solo LEEN la cache (DATA/assets/brand/link_media.json: url del enlace
# -> {ok, t, photo, logo}); una tarea por perfil (sin repetir urls en curso) baja lo que falta, lo
# guarda en los perfiles guardados (media_src 'auto') y lo pone en los proyectos que usan ese enlace
# (director.apply_profile_media: sin mover nada) con concurrencia optimista: plan_rev sube y
# /status lleva `profile_media` {state 'applied', plan_rev, from_rev} para que el Studio lo adopte.
# Lo del usuario (media_src 'user') y lo que quito (media_off) nunca se toca. REELFY_LINK_MEDIA=0 lo
# apaga; bajo pytest va apagado salvo REELFY_LINK_MEDIA=1 (las pruebas usan un servidor local).
LINK_MEDIA_F = paths.BRAND / "link_media.json"
LM_TIMEOUT_S = 8.0        # por peticion (pagina e imagenes)
LM_RETRY_S = 600.0        # una pagina que no respondio (sin red) se reintenta tras 10 min
LM_JOB_WAIT_S = 900.0     # un proyecto ocupado (analizando, exportando) se espera hasta 15 min
LM_POLL_S = 2.0
LM_CACHE_MAX = 300
_LM_LOCK = threading.Lock()      # cache en disco + registro de descargas en curso
_LM_RUNNING = {}                 # url -> hilo que la descarga


def _lm_enabled():
    v = os.environ.get("REELFY_LINK_MEDIA")
    if v is not None and v.strip():
        return v.strip().lower() not in ("0", "false", "no", "off")
    return "PYTEST_CURRENT_TEST" not in os.environ       # pruebas: sin red salvo que se pida


def _lm_http():
    """GET acotado de linkmedia (red real). Las pruebas lo sustituyen por un servidor local."""
    from motion import linkmedia as LM
    return LM._http_get


def _lm_cache():
    try:
        d = json.loads(LINK_MEDIA_F.read_text()) if LINK_MEDIA_F.exists() else {}
    except Exception:  # noqa: cache corrupta = sin cache (se vuelve a descargar)
        d = {}
    return d if isinstance(d, dict) else {}


def _lm_cache_put(url, ent):
    """Guarda el resultado de un enlace. Un intento SIN red no borra lo que ya se tenia."""
    with _LM_LOCK:
        d = _lm_cache()
        prev = d.get(url) if isinstance(d.get(url), dict) else None
        if not ent.get("ok") and prev and prev.get("ok"):
            prev["t_try"] = ent["t"]
            ent = prev
        d[url] = ent
        if len(d) > LM_CACHE_MAX:
            for k in sorted(d, key=lambda k_: float((d[k_] or {}).get("t") or 0))[:len(d) - LM_CACHE_MAX]:
                d.pop(k, None)
        LINK_MEDIA_F.parent.mkdir(parents=True, exist_ok=True)
        tmp = LINK_MEDIA_F.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False))
        os.replace(tmp, LINK_MEDIA_F)


def _lm_cache_sids():
    out = set()
    for ent in _lm_cache().values():
        for k in ("photo", "logo"):
            m = (ent or {}).get(k) if isinstance(ent, dict) else None
            if isinstance(m, dict) and m.get("sid"):
                out.add(str(m["sid"]))
    return out


def _lm_file(m):
    return isinstance(m, dict) and bool(m.get("sid")) and _resolve_source(m["sid"]) is not None


def _lm_links(prof):
    """Enlaces del perfil con algo que descargar solo -> [(enlace, [kinds])]."""
    from motion import profile as PR
    out = []
    for l in (prof or {}).get("links") or []:
        want = PR.media_wanted(l) if isinstance(l, dict) else []
        if want:
            out.append((l, want))
    return out


def _lm_fill(prof, cache=None):
    """Perfil -> copia saneada con la foto/logo 'auto' ya descargados (solo cache, sin red). Lo
    subido o quitado por el usuario no se toca; una pagina que no respondio deja lo que habia."""
    from motion import profile as PR
    prof = PR.normalize_profile(prof) if prof else None
    todo = _lm_links(prof)
    if not todo:
        return prof
    cache = _lm_cache() if cache is None else cache
    for l, want in todo:
        ent = cache.get(l.get("url"))
        if not isinstance(ent, dict) or not ent.get("ok"):
            continue
        src = dict(l.get("media_src") or {})
        for k in want:
            m = ent.get(k)
            if _lm_file(m):
                l[k] = m
                src[k] = "auto"
            else:
                l.pop(k, None)
                src.pop(k, None)
        if src:
            l["media_src"] = src
        else:
            l.pop("media_src", None)
        if "auto" in src.values():
            l["media_for"] = l["url"]
        else:
            l.pop("media_for", None)
    return PR.normalize_profile(prof)


def _lm_missing(prof, force=False):
    """[(url, plataforma)] que hay que descargar: sin cache, sin red hace > LM_RETRY_S, archivo
    borrado, o todo lo 'auto' con `force` (refrescar)."""
    cache = _lm_cache()
    now = time.time()
    out = []
    for l, want in _lm_links(prof):
        ent = cache.get(l["url"])
        if force or not isinstance(ent, dict):
            out.append((l["url"], l.get("platform")))
        elif not ent.get("ok"):
            if now - float(ent.get("t") or 0) >= LM_RETRY_S:
                out.append((l["url"], l.get("platform")))
        elif any(isinstance(ent.get(k), dict) and not _lm_file(ent[k]) for k in want):
            out.append((l["url"], l.get("platform")))
    return list(dict.fromkeys(out))


def _lm_running(url):
    th = _LM_RUNNING.get(url)
    return th is not None and th.is_alive()


def _lm_kick(prof, force=False):
    """Descarga en SEGUNDO PLANO lo que le falta a `prof` (nunca bloquea). -> urls lanzadas."""
    if not prof or not _lm_enabled():
        return []
    try:
        todo = _lm_missing(prof, force=force)
    except Exception:  # noqa
        traceback.print_exc()
        return []
    with _LM_LOCK:
        todo = [(u, p) for u, p in todo if not _lm_running(u)]
        if not todo:
            return []
        th = threading.Thread(target=_lm_run, args=(todo,), daemon=True,
                              name=f"reelfy-linkmedia-{hashlib.sha1(todo[0][0].encode()).hexdigest()[:6]}")
        for u, _p in todo:
            _LM_RUNNING[u] = th
        # se arranca DENTRO del candado: antes de start() is_alive() es False y otro pedido
        # simultaneo lanzaria la misma url dos veces (dos escritores del mismo <sid>.png)
        th.start()
    return [u for u, _p in todo]


def _lm_run(todo):
    """Hilo de descarga de un perfil: pagina -> foto + logo (linkmedia), cache y propagacion."""
    from motion import linkmedia as LM
    done = []
    try:
        for url, plat in todo:
            reached = []
            base = _lm_http()

            def get(u, limit, timeout, _b=base, _u=url, _r=reached):
                r = _b(u, limit, timeout)
                if u == _u:
                    _r.append(1)                 # la pagina respondio (con o sin imagen)
                return r
            t0 = time.time()
            try:
                res = LM.fetch_link_media(url, plat, SOURCES, timeout=LM_TIMEOUT_S, fetch=get) or {}
            except Exception:  # noqa: linkmedia no lanza; por si acaso
                traceback.print_exc()
                res = {}
            ok = bool(reached)
            _lm_cache_put(url, {"ok": ok, "t": round(time.time(), 1), "photo": res.get("photo"),
                                "logo": res.get("logo")})
            print(f"   perfil: foto/logo de {url[:60]} -> "
                  f"{'sin red' if not ok else ('foto' if res.get('photo') else 'sin foto') + (' + logo' if res.get('logo') else '')}"
                  f" ({time.time() - t0:.1f}s)")
            done.append(url)
    finally:
        with _LM_LOCK:
            for url, _p in todo:
                if _LM_RUNNING.get(url) is threading.current_thread():
                    _LM_RUNNING.pop(url, None)
    if done:
        try:
            _lm_propagate(done)
        except Exception:  # noqa: la foto es un extra: nunca tumba nada
            traceback.print_exc()


def _lm_uses(prof, urls):
    return any(isinstance(l, dict) and l.get("url") in urls for l in (prof or {}).get("links") or [])


def _lm_propagate(urls):
    """Descarga terminada: la foto/logo pasa a los perfiles guardados y a los proyectos que usan
    esos enlaces. Un proyecto ocupado (analizando, exportando, dirigiendo) se reintenta."""
    from motion import profile as PR
    urls = set(urls)
    cache = _lm_cache()
    with _PROFILES_LOCK:
        items = _profiles_load()
        changed = False
        for i, pr in enumerate(items):
            if not _lm_uses(pr, urls):
                continue
            cur = PR.normalize_profile(pr)
            new = _lm_fill(cur, cache)
            if cur and new and _profile_key(new) != _profile_key(cur):
                new["id"] = pr["id"]
                if pr.get("updated"):
                    new["updated"] = pr["updated"]
                items[i] = new
                changed = True
        if changed:
            _profiles_save(items)
    pending = list(jobstore.all_ids())
    deadline = time.time() + LM_JOB_WAIT_S
    while pending:
        nxt = []
        for jid in pending:
            try:
                r = _lm_apply_job(jid, urls)
            except Exception:  # noqa
                traceback.print_exc()
                r = "skip"
            if r == "busy":
                nxt.append(jid)
        pending = nxt
        if not pending or time.time() > deadline:
            break
        time.sleep(LM_POLL_S)


def _lm_apply_job(jid, urls):
    """Pone la foto/logo descargados en el plan de `jid` (sin re-dirigir: misma geometria).
    -> 'done' | 'skip' | 'busy' (ocupado u otro escritor gano: se reintenta)."""
    from motion import profile as PR
    j = jobstore.get_light(jid)
    if not j:
        return "skip"
    plan, rev = jobstore.get_plan(jid)
    uses = _lm_uses((plan or {}).get("profile"), urls)
    if j.get("state") in ("queued", "running") or _side_running(jid, "direct"):
        if uses:
            return "busy"
        params = (jobstore.get(jid) or {}).get("params") or {}
        return "busy" if _lm_uses(params.get("profile"), urls) else "skip"
    if not uses or not _is_v2(plan):
        return "skip"
    old = PR.normalize_profile(plan.get("profile"))
    new = _lm_fill(old)
    if _profile_key(new) == _profile_key(old):
        return "done"
    from motion import director
    out, ids = director.apply_profile_media(plan, new)
    fh = jobstore.plan_hash(plan)
    ok, rev2 = jobstore.update_plan(jid, out, rev, side=("profile_media", lambda r2: dict(
        state="applied", plan_rev=r2, from_rev=rev, from_hash=fh, items=ids[:40],
        message="Foto y logo del perfil listos")))
    if not ok:
        return "busy"
    print(f"   perfil: foto/logo en el proyecto {jid} (rev {rev} -> {rev2}; {len(ids)} elementos)")
    return "done"


def _lm_status(prof, cache=None):
    """Estado de la foto/logo automaticos del perfil: 'fetching' (descargando), 'offline' (alguna
    pagina no respondio; se reintenta sola) o 'done' (nada pendiente, o descarga apagada)."""
    urls = [l["url"] for l, _w in _lm_links(prof)]
    if not urls or not _lm_enabled():
        return "done"
    with _LM_LOCK:
        if any(_lm_running(u) for u in urls):
            return "fetching"
    cache = _lm_cache() if cache is None else cache
    if any(u not in cache for u in urls):
        return "fetching"                       # recien pedida (el hilo esta por arrancar)
    return "offline" if any(not (cache[u] or {}).get("ok") for u in urls) else "done"


def _lm_decorate(prof, cache=None):
    """Copia del perfil para la respuesta con `media_status` (no se guarda)."""
    if not isinstance(prof, dict):
        return prof
    try:
        return dict(prof, media_status=_lm_status(prof, cache))
    except Exception:  # noqa
        return prof


def _lm_decorate_all(items):
    cache = _lm_cache()
    return [_lm_decorate(x, cache) for x in items]


def _lm_ready(prof, kick=True):
    """Perfil saneado con lo ya descargado + descarga en segundo plano de lo que falta."""
    prof = _lm_fill(prof)
    if kick:
        _lm_kick(prof)
    return prof


def _lm_plan(plan):
    """Plan que manda el Studio con el perfil de ANTES de que bajara la foto/logo: se ponen (sin
    mover nada) para que el cuadro, la vista previa y el export ya salgan con ellos. Barato: sin
    enlaces con algo automatico no lee nada."""
    if not _is_v2(plan) or not isinstance(plan.get("profile"), dict) or not _lm_links(plan["profile"]):
        return plan
    try:
        from motion import profile as PR
        raw = PR.normalize_profile(plan["profile"])
        new = _lm_fill(raw)
        if _profile_key(new) == _profile_key(raw):
            return plan
        from motion import director
        return director.apply_profile_media(plan, new)[0]
    except Exception:  # noqa: la foto es un extra: el plan sale tal cual
        traceback.print_exc()
        return plan


def _lm_stored(job_id, plan, rev):
    """Al abrir un proyecto: la foto/logo de sus enlaces que ya estan en la cache (bajados para
    este u otro perfil) se ponen y se guardan (concurrencia optimista; plan_rev sube como con la
    simetria); lo que falta empieza a bajar en segundo plano. Ocupado: se deja para despues.
    -> (plan, plan_rev)."""
    if not _is_v2(plan) or not isinstance(plan.get("profile"), dict):
        return plan, rev
    try:
        _lm_kick(plan["profile"])
        out = _lm_plan(plan)
        if out is plan:
            return plan, rev
        j = jobstore.get_light(job_id)
        if not j or j.get("state") in ("queued", "running") or _side_running(job_id, "direct"):
            return plan, rev
        fh = jobstore.plan_hash(plan)
        ok, rev2 = jobstore.update_plan(job_id, out, rev, side=("profile_media", lambda r2: dict(
            state="applied", plan_rev=r2, from_rev=rev, from_hash=fh,
            message="Foto y logo del perfil listos")))
        if not ok:
            return jobstore.get_plan(job_id)
        return out, rev2
    except Exception:  # noqa: la foto es un extra
        traceback.print_exc()
        return plan, rev


def _pm_side(job_id):
    try:
        return jobstore.side_get(job_id, "profile_media") or {}
    except Exception:  # noqa
        return {}


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
    # la foto de su Instagram / de su pagina empieza a bajar mientras el usuario confirma
    prof = _lm_ready(r["profile"]) if r.get("profile") else r.get("profile")
    return {"profile": _lm_decorate(prof), "notes": list(dict.fromkeys(r.get("notes") or [])),
            "llm": r["llm"], "ms": round((time.time() - t0) * 1000)}


@app.post("/profile/media/refresh")
def profile_media_refresh(body: dict = Body(...)):
    """Vuelve a bajar la foto/logo AUTOMATICOS de los enlaces de un perfil ({profile} |
    {profile_id} | {job_id}); lo que subio el usuario nunca se toca. No espera a la red: la descarga
    va en segundo plano y al terminar actualiza los perfiles guardados y los proyectos que usan esos
    enlaces. -> {ok, profile (con media_status), urls, enabled}."""
    body = body or {}
    jid = str(body.get("job_id") or "").strip()
    if jid:
        plan, _rev = jobstore.get_plan(jid)
        if plan is None:
            raise HTTPException(404, "Sin plan")
        prof = _lm_fill(plan.get("profile"))
    else:
        prof = _lm_fill(_profile_param(body.get("profile"), body.get("profile_id")))
    if not prof:
        raise HTTPException(400, "Falta el perfil.")
    urls = _lm_kick(prof, force=True)
    return {"ok": True, "profile": _lm_decorate(prof), "urls": urls, "enabled": _lm_enabled()}


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
    return {"profiles": _lm_decorate_all(_profiles_load())}


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
    prof = _lm_ready(prof)                 # foto/logo de sus enlaces (lo que falta, en 2o plano)
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
    return {"ok": True, "id": pid, "profile": _lm_decorate(prof),
            "profiles": _lm_decorate_all(items[:100]), "replaced": replaced}


@app.delete("/profiles/{pid}")
def profiles_delete(pid: str):
    with _PROFILES_LOCK:
        items = _profiles_load()
        if not any(x.get("id") == pid for x in items):
            raise HTTPException(404, "Perfil no encontrado.")
        items = [x for x in items if x.get("id") != pid]
        _profiles_save(items)
    return {"ok": True, "profiles": _lm_decorate_all(items)}


@app.get("/profile/{job_id}")
def profile_get(job_id: str):
    plan, rev = jobstore.get_plan(job_id)
    if plan is None:
        raise HTTPException(404, "Sin plan")
    return {"profile": _lm_decorate(plan.get("profile")), "plan_rev": rev,
            "profile_media": _pm_side(job_id) or None}


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
    out = _sym(_accent_keep(plan, out))
    ok, rev = jobstore.update_plan(job_id, out, base)
    if not ok:
        return JSONResponse({"ok": False, "detail": "El plan cambió en el servidor (re-dirigido u otra "
                                                     "ventana); recarga antes de guardar el perfil.",
                             "plan_rev": rev}, status_code=409)
    _kick_analysis(job_id, out, _fmt_of(body))
    return {"ok": True, "plan": out, "plan_rev": rev, "profile": _lm_decorate(out.get("profile")),
            "ms": round((time.time() - t0) * 1000)}


# capacidades de la API que el Studio puede consultar antes de usarlas
API_FEATURES = ("captions", "source_proxy", "jobs_meta", "music_beats", "translate_texts",
                "translate_titles", "preview_music_refresh", "music_sync", "air_optin", "media",
                "profile", "profile_media", "takes", "storage")


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
        st["plan"], st["plan_rev"] = _lm_stored(job_id, *_sym_stored(job_id, *jobstore.get_plan(job_id)))
    # poco disco: se AVISA (antes se purgaban proyectos viejos sin decir nada)
    disk = _disk_state()
    st["low_space"], st["disk_free_gb"] = disk["low_space"], disk["free_gb"]
    if disk["low_space"]:
        st["low_space_message"] = disk["message"]
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
    _SERVING = True
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("REELFY_PORT", 8000)))
