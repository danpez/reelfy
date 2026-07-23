"""Reelfy — cola de trabajos persistente + pool de workers.

Sustituye el dict JOBS en memoria por SQLite (DATA/jobs.db):
  - los trabajos SOBREVIVEN reinicios (un plan analizado o un render terminado
    no se pierden si la app se cierra);
  - los trabajos se ENCOLAN y un pool de workers los ejecuta de a uno (por
    defecto): dos análisis simultáneos ya no saturan la máquina;
  - CANCELACIÓN cooperativa: los callbacks de progreso consultan el flag y
    abortan (matando el ffmpeg en curso);
  - el mismo esquema store+workers es la costura para la nube (fase 3): correr
    este mismo loop de worker en N contenedores contra un store compartido.

Estados: queued -> running -> review|done | error | canceled
`phase` conserva el vocabulario que ya entiende la UI
(analyzing/review/rendering/done/error).
"""
import json
import os
import sqlite3
import threading
import time
import traceback

import paths

DB = paths.DATA / "jobs.db"
KEEP_LAST = 50          # housekeeping: conservar los N trabajos más recientes

_local = threading.local()
_handlers: dict = {}
_wake = threading.Event()

_JSON_COLS = ("params", "plan", "clips")
_COLS = ("id", "kind", "state", "phase", "pct", "message", "eta", "elapsed",
         "ext", "params", "plan", "clips", "error", "created", "updated")


class JobCanceled(Exception):
    """El usuario canceló el trabajo; el worker lo marca 'canceled' (no error)."""


def _cx() -> sqlite3.Connection:
    cx = getattr(_local, "cx", None)
    if cx is None:
        cx = sqlite3.connect(DB, timeout=30)
        cx.row_factory = sqlite3.Row
        cx.execute("PRAGMA journal_mode=WAL")
        cx.execute("PRAGMA synchronous=NORMAL")
        _local.cx = cx
    return cx


def init():
    cx = _cx()
    cx.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, kind TEXT, state TEXT, phase TEXT,
        pct REAL DEFAULT 0, message TEXT DEFAULT '', eta INTEGER, elapsed INTEGER,
        ext TEXT, params TEXT, plan TEXT, clips TEXT, error TEXT,
        cancel INTEGER DEFAULT 0, created REAL, updated REAL)""")
    cx.commit()
    recover()
    _housekeep()


def recover():
    """Al arrancar: lo que estaba corriendo murió con el proceso -> error claro.
    Lo encolado se queda encolado (el worker lo tomará)."""
    cx = _cx()
    cx.execute("""UPDATE jobs SET state='error', phase='error',
                  error='El proceso se interrumpió (la app se cerró a media tarea). Vuelve a intentarlo.',
                  updated=? WHERE state='running'""", (time.time(),))
    cx.commit()


def _housekeep():
    cx = _cx()
    cx.execute("""DELETE FROM jobs WHERE id NOT IN
                  (SELECT id FROM jobs ORDER BY created DESC LIMIT ?)""", (KEEP_LAST,))
    cx.commit()


def _dump(kw):
    for k in _JSON_COLS:
        if k in kw and kw[k] is not None and not isinstance(kw[k], str):
            kw[k] = json.dumps(kw[k])
    return kw


def create(job_id, kind, ext, params=None, plan=None, phase="analyzing",
           message="En cola…"):
    now = time.time()
    cx = _cx()
    cx.execute("""INSERT INTO jobs (id, kind, state, phase, pct, message, ext,
                  params, plan, created, updated)
                  VALUES (?,?, 'queued', ?, 0, ?, ?, ?, ?, ?, ?)""",
               (job_id, kind, phase, message, ext,
                json.dumps(params or {}),
                json.dumps(plan) if plan is not None else None, now, now))
    cx.commit()
    _wake.set()


def requeue(job_id, kind, params=None, plan=None, phase="rendering",
            message="En cola…"):
    """Reusar el MISMO job (la UI sigue el mismo id) para la siguiente etapa."""
    kw = _dump(dict(params=params or {}, plan=plan))
    cx = _cx()
    cx.execute("""UPDATE jobs SET kind=?, state='queued', phase=?, pct=0, message=?,
                  eta=NULL, elapsed=NULL, error=NULL, cancel=0, params=?,
                  plan=COALESCE(?, plan), updated=? WHERE id=?""",
               (kind, phase, message, kw["params"], kw.get("plan"),
                time.time(), job_id))
    cx.commit()
    _wake.set()


def update(job_id, **kw):
    kw = _dump(kw)
    sets = ", ".join(f"{k}=?" for k in kw)
    cx = _cx()
    cx.execute(f"UPDATE jobs SET {sets}, updated=? WHERE id=?",
               (*kw.values(), time.time(), job_id))
    cx.commit()


def get(job_id):
    row = _cx().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in _JSON_COLS:
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def status(job_id):
    """Shape que la UI ya conoce (phase/pct/message/eta/elapsed/plan/clips/error/ext).
    Si está en cola, el mensaje muestra la posición."""
    d = get(job_id)
    if not d:
        return None
    if d["state"] == "queued":
        pos = _cx().execute(
            "SELECT COUNT(*) FROM jobs WHERE state='queued' AND created <= ?",
            (d["created"],)).fetchone()[0]
        d["message"] = "En cola…" if pos <= 1 else f"En cola ({pos}º)…"
    return {k: d[k] for k in ("phase", "pct", "message", "eta", "elapsed",
                              "plan", "clips", "error", "ext")}


def recent(limit=20):
    rows = _cx().execute(
        "SELECT id, kind, state, phase, pct, message, error, created, updated "
        "FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---- cancelación cooperativa ----

def request_cancel(job_id):
    """Marca cancel. Si aún está en cola, se cancela de inmediato."""
    cx = _cx()
    cur = cx.execute("""UPDATE jobs SET state='canceled', phase='error',
                        error='Cancelado.', updated=? WHERE id=? AND state='queued'""",
                     (time.time(), job_id))
    if cur.rowcount == 0:
        cx.execute("UPDATE jobs SET cancel=1, updated=? WHERE id=?",
                   (time.time(), job_id))
    cx.commit()
    return True


class CancelChecker:
    """check() consulta el flag como mucho cada 0.5 s y lanza JobCanceled."""

    def __init__(self, job_id):
        self.job_id = job_id
        self._last = 0.0

    def check(self):
        now = time.monotonic()
        if now - self._last < 0.5:
            return
        self._last = now
        row = _cx().execute("SELECT cancel FROM jobs WHERE id=?",
                            (self.job_id,)).fetchone()
        if row and row["cancel"]:
            raise JobCanceled()


class Progress:
    """Acota la frecuencia de escritura de progreso (~4/s) y chequea cancelación."""

    def __init__(self, job_id):
        self.job_id = job_id
        self.cancel = CancelChecker(job_id)
        self._last = 0.0

    def update(self, force=False, **kw):
        self.cancel.check()
        now = time.monotonic()
        if not force and now - self._last < 0.25:
            return
        self._last = now
        update(self.job_id, **kw)


# ---- pool de workers ----

def _claim_next():
    cx = _cx()
    with cx:                     # BEGIN IMMEDIATE evita que 2 workers tomen el mismo
        cx.execute("BEGIN IMMEDIATE")
        row = cx.execute("SELECT id, kind FROM jobs WHERE state='queued' "
                         "ORDER BY created LIMIT 1").fetchone()
        if not row:
            return None
        cx.execute("UPDATE jobs SET state='running', message='Iniciando…', updated=? "
                   "WHERE id=?", (time.time(), row["id"]))
    return row["id"], row["kind"]


def _worker_loop(idx):
    while True:
        claimed = None
        try:
            claimed = _claim_next()
        except Exception:  # noqa
            traceback.print_exc()
        if not claimed:
            _wake.wait(timeout=2.0)
            _wake.clear()
            continue
        job_id, kind = claimed
        fn = _handlers.get(kind)
        try:
            if fn is None:
                raise RuntimeError(f"sin handler para '{kind}'")
            fn(job_id, get(job_id))
        except JobCanceled:
            update(job_id, state="canceled", phase="error", error="Cancelado.")
            print(f"[worker{idx}] {job_id} cancelado")
        except Exception as e:  # noqa
            traceback.print_exc()
            update(job_id, state="error", phase="error",
                   error=f"{'Análisis' if kind == 'analyze' else 'Render'} falló: {e}")


def start_workers(handlers, n=None):
    global _handlers
    _handlers = dict(handlers)
    n = n or int(os.environ.get("REELFY_WORKERS", "1"))
    for i in range(n):
        threading.Thread(target=_worker_loop, args=(i,), daemon=True,
                         name=f"reelfy-worker-{i}").start()
    print(f"[jobstore] {n} worker(s) listos; db={DB}")
