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

Motor pro (v0.5):
  - `stage` (etapa fina: compose/analyze/director/timeline/base/analysis/audio/render/mux)
    y `warnings` (lista) por trabajo.
  - `plan_rev`: sube SOLO cuando el plan guardado cambia de contenido (`plan_hash` = sha1 del
    JSON canonico); /status manda el plan completo solo cuando hace falta (el plan v2 pesa).
    `update_plan(..., base_rev)` = escritura con concurrencia optimista (409 en el servidor).
  - Un export cancelado (o interrumpido por un cierre) con plan vuelve a `review`: el plan
    sigue siendo valido y el proyecto no aparece como error.
  - `side`: tareas laterales del mismo trabajo que NO mueven su fase (vista previa real,
    analisis en segundo plano, musica): {nombre: {state, pct, stage, file, error, ...}}.
  - `meta`: resumen liviano del plan (tema, duracion, version) que se recalcula en cada
    escritura del plan: "Proyectos recientes" (GET /jobs) no deserializa planes.
  - `cache_files`: nombres de los intermedios de work/motion_cache (base, voz) que el motor
    genero PARA este trabajo (el servidor los anota): "Liberar espacio" y "Eliminar proyecto"
    saben cuales son suyos (la voz no dice de que fuente salio).
  - Los proyectos NUNCA se purgan solos (antes: los 50 mas recientes, o 5 con poco disco). Solo
    se borran con delete() (DELETE /jobs/{id}: sus archivos van a la Papelera).
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import traceback

import paths

DB = paths.DATA / "jobs.db"
KEEP_LAST = 50          # housekeep() explicito (ya no corre solo: los proyectos no se purgan)
CACHE_FILES_MAX = 64    # nombres de intermedios anotados por trabajo (los mas recientes)

_local = threading.local()
_handlers: dict = {}
_wake = threading.Event()
_side_lock = threading.Lock()   # lectura-modificacion-escritura atomica de `side`

_JSON_COLS = ("params", "plan", "clips", "warnings", "side", "meta", "cache_files")
_COLS = ("id", "kind", "state", "phase", "pct", "message", "eta", "elapsed",
         "ext", "params", "plan", "clips", "error", "created", "updated",
         "stage", "warnings", "plan_rev", "side", "plan_hash", "meta", "cache_files")
# columnas agregadas despues de v0.4 (migracion en caliente de un jobs.db existente)
_NEW_COLS = {"stage": "TEXT", "warnings": "TEXT", "plan_rev": "INTEGER DEFAULT 0",
             "side": "TEXT", "plan_hash": "TEXT", "meta": "TEXT", "cache_files": "TEXT"}
CANCELED_EXPORT_MSG = "Export cancelado. Tu edición sigue aquí."
# claves de las tareas laterales que /status aplana como <tarea>_<clave>
SIDE_FLAT = ("state", "pct", "file", "stage", "error", "done", "t", "message", "music_pending",
             "url", "resync", "prev_file")


def _final_duration(dur, cuts):
    """Duracion final aproximada (duracion menos los cortes habilitados, fusionados)."""
    rs = []
    for c in cuts or []:
        if not isinstance(c, dict) or c.get("enabled") is False:
            continue
        try:
            a, b = max(0.0, float(c["start"])), min(dur, float(c["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if b > a:
            rs.append((a, b))
    rs.sort()
    cut, end = 0.0, -1.0
    for a, b in rs:
        if a > end:
            cut += b - a
            end = b
        elif b > end:
            cut += b - end
            end = b
    return max(0.0, dur - cut)


def plan_meta(plan):
    """Resumen del plan para listados: {version, topic, duration, final_duration, style,
    shots}. `topic` = tema que entendio el director (o el primer titulo de highlight)."""
    if plan is None:
        return None
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception:  # noqa
            return None
    if not isinstance(plan, dict):
        return None
    d = plan.get("director") if isinstance(plan.get("director"), dict) else {}
    sc = d.get("script") if isinstance(d.get("script"), dict) else {}
    topic = sc.get("topic") or plan.get("topic")
    if not topic:
        hl = [h for h in plan.get("highlights") or [] if isinstance(h, dict) and h.get("title")]
        topic = hl[0]["title"] if hl else None
    try:
        dur = float(plan.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    out = {"version": plan.get("version") or 1, "topic": str(topic)[:120] if topic else None,
           "duration": round(dur, 2),
           "final_duration": round(_final_duration(dur, plan.get("cuts")), 2) if dur else None,
           "style": d.get("style"), "shots": len(plan.get("shots") or [])}
    # limpieza de tomas (cortes src "takes"): cuantas hay, cuantas activas y cuanto quitan de mas
    # (sobre los silencios), contado de los cortes (los toggles del Studio no tocan plan.takes)
    tk = [c for c in plan.get("cuts") or [] if isinstance(c, dict) and c.get("src") == "takes"]
    if tk:
        rest = [c for c in plan.get("cuts") or [] if isinstance(c, dict) and c.get("src") != "takes"]
        extra = _final_duration(dur, rest) - _final_duration(dur, plan.get("cuts")) if dur else 0.0
        out["takes"] = {"n": len(tk), "enabled": sum(1 for c in tk if c.get("enabled") is not False),
                        "removed_s": round(max(0.0, extra), 2)}
    return out


def plan_hash(plan):
    """Huella del CONTENIDO del plan (independiente del orden de las claves)."""
    if plan is None:
        return None
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception:  # noqa
            return hashlib.sha1(plan.encode()).hexdigest()
    raw = json.dumps(plan, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha1(raw.encode()).hexdigest()


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
    have = {r["name"] for r in cx.execute("PRAGMA table_info(jobs)").fetchall()}
    for col, typ in _NEW_COLS.items():
        if col not in have:
            cx.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
    cx.commit()
    recover()
    return []       # sin purga automatica: los proyectos viejos se conservan (aviso de poco disco)


def recover():
    """Al arrancar: lo que estaba corriendo murió con el proceso. Un export con plan vuelve a
    `review` (la edicion sigue valida); lo demas -> error claro. Lo encolado se queda encolado
    (el worker lo tomará). Las tareas laterales en curso (vista previa, analisis, musica)
    tambien murieron."""
    cx = _cx()
    now = time.time()
    cx.execute("""UPDATE jobs SET state='review', phase='review', pct=100, stage='review',
                  message='El export se interrumpió (la app se cerró). Vuelve a exportar.',
                  error=NULL, cancel=0, eta=NULL, updated=?
                  WHERE state='running' AND kind='render' AND plan IS NOT NULL""", (now,))
    cx.execute("""UPDATE jobs SET state='error', phase='error',
                  error='El proceso se interrumpió (la app se cerró a media tarea). Vuelve a intentarlo.',
                  updated=? WHERE state='running'""", (now,))
    for row in cx.execute("SELECT id, side FROM jobs WHERE side IS NOT NULL").fetchall():
        try:
            side = json.loads(row["side"]) or {}
        except Exception:  # noqa
            continue
        changed = False
        for t in side.values():
            if isinstance(t, dict) and t.get("state") in ("queued", "running"):
                t.update(state="error", error="Interrumpido (la app se cerró).")
                changed = True
        if changed:
            cx.execute("UPDATE jobs SET side=? WHERE id=?", (json.dumps(side), row["id"]))
    cx.commit()


def _housekeep(keep=None):
    """Borra las filas de los trabajos mas viejos (conserva los `keep` mas recientes que no
    esten en proceso). Devuelve los ids purgados. Ya NO se llama sola (ni al arrancar ni con poco
    disco): perder un proyecto sin aviso es peor que quedarse sin espacio."""
    keep = KEEP_LAST if keep is None else int(keep)
    cx = _cx()
    ids = [r["id"] for r in cx.execute(
        """SELECT id FROM jobs WHERE state NOT IN ('queued', 'running') AND id NOT IN
           (SELECT id FROM jobs ORDER BY created DESC LIMIT ?)""", (keep,)).fetchall()]
    if ids:
        cx.executemany("DELETE FROM jobs WHERE id=?", [(i,) for i in ids])
        cx.commit()
    return ids


def housekeep(keep=None):
    return _housekeep(keep)


def all_ids():
    return [r["id"] for r in _cx().execute("SELECT id FROM jobs").fetchall()]


def delete(job_id):
    """Borra la fila del trabajo SOLO si no esta en proceso (en cola o corriendo): la condicion va
    en el mismo DELETE (atomico: un export que se encola a la vez gana y esto devuelve False).
    -> True si se borro."""
    cx = _cx()
    cur = cx.execute("DELETE FROM jobs WHERE id=? AND state NOT IN ('queued', 'running')", (job_id,))
    cx.commit()
    return cur.rowcount > 0


def claim_cache(job_id, names):
    """Anota intermedios de work/motion_cache (nombres de archivo) como de este trabajo. Los mas
    recientes al final; se conservan los ultimos CACHE_FILES_MAX."""
    names = [str(n) for n in names or [] if n]
    if not names:
        return None
    with _side_lock:
        cx = _cx()
        row = cx.execute("SELECT cache_files FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        try:
            cur = json.loads(row["cache_files"]) if row["cache_files"] else []
        except Exception:  # noqa
            cur = []
        cur = [n for n in cur if n not in names] + list(dict.fromkeys(names))
        cur = cur[-CACHE_FILES_MAX:]
        cx.execute("UPDATE jobs SET cache_files=? WHERE id=?", (json.dumps(cur), job_id))
        cx.commit()
        return cur


def cache_claims():
    """{id: [nombres de intermedios]} de todos los trabajos que anotaron alguno."""
    out = {}
    for r in _cx().execute("SELECT id, cache_files FROM jobs WHERE cache_files IS NOT NULL").fetchall():
        try:
            out[r["id"]] = list(json.loads(r["cache_files"]) or [])
        except Exception:  # noqa
            continue
    return out


def sids_by_job():
    """{id: set(sids)} de las fuentes subidas (/sources) que cada trabajo usa como medio propio o
    en el perfil ({"sid": ...} en su plan o en sus parametros). Las del montaje (spec.main/overlays)
    salen de all_params()."""
    out = {}
    for r in _cx().execute("SELECT id, params, plan FROM jobs WHERE plan LIKE '%\"sid\"%' "
                           "OR params LIKE '%\"sid\"%'").fetchall():
        s = set(_SID_RE.findall(r["plan"] or "")) | set(_SID_RE.findall(r["params"] or ""))
        if s:
            out[r["id"]] = s
    return out


def all_params():
    """{id: params} de todos los trabajos (para saber que fuentes siguen referenciadas)."""
    out = {}
    for r in _cx().execute("SELECT id, params FROM jobs").fetchall():
        try:
            out[r["id"]] = json.loads(r["params"]) if r["params"] else {}
        except Exception:  # noqa
            out[r["id"]] = {}
    return out


def _dump(kw):
    for k in _JSON_COLS:
        if k in kw and kw[k] is not None and not isinstance(kw[k], str):
            kw[k] = json.dumps(kw[k])
    return kw


def create(job_id, kind, ext, params=None, plan=None, phase="analyzing",
           message="En cola…"):
    now = time.time()
    cx = _cx()
    meta = plan_meta(plan)
    cx.execute("""INSERT INTO jobs (id, kind, state, phase, pct, message, ext,
                  params, plan, created, updated, plan_rev, plan_hash, meta)
                  VALUES (?,?, 'queued', ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
               (job_id, kind, phase, message, ext,
                json.dumps(params or {}),
                json.dumps(plan) if plan is not None else None, now, now,
                1 if plan is not None else 0, plan_hash(plan),
                json.dumps(meta) if meta is not None else None))
    cx.commit()
    _wake.set()


def requeue(job_id, kind, params=None, plan=None, phase="rendering",
            message="En cola…"):
    """Reusar el MISMO job (la UI sigue el mismo id) para la siguiente etapa. `plan_rev` sube
    solo si el plan cambia de contenido (un export del plan identico no obliga a la UI a
    bajarlo de nuevo)."""
    h = plan_hash(plan)
    meta = plan_meta(plan)
    kw = _dump(dict(params=params or {}, plan=plan))
    cx = _cx()
    cx.execute("""UPDATE jobs SET kind=?, state='queued', phase=?, pct=0, message=?,
                  eta=NULL, elapsed=NULL, error=NULL, cancel=0, params=?, stage=NULL,
                  warnings=NULL, plan=COALESCE(?, plan),
                  plan_rev=COALESCE(plan_rev, 0) + (CASE WHEN ? IS NULL OR
                      plan_hash IS ? THEN 0 ELSE 1 END),
                  plan_hash=COALESCE(?, plan_hash), meta=COALESCE(?, meta),
                  updated=? WHERE id=?""",
               (kind, phase, message, kw["params"], kw.get("plan"), h, h, h,
                json.dumps(meta) if meta is not None else None, time.time(), job_id))
    cx.commit()
    _wake.set()


def update(job_id, **kw):
    """Actualiza columnas. Si trae `plan` y su contenido cambio, sube `plan_rev` (la UI sabe
    que cambio)."""
    h = plan_hash(kw["plan"]) if kw.get("plan") is not None else None
    if h is not None:
        kw["meta"] = plan_meta(kw["plan"])
    kw = _dump(kw)
    sets = ", ".join(f"{k}=?" for k in kw)
    vals = list(kw.values())
    if h is not None:
        sets += (", plan_rev=COALESCE(plan_rev, 0) + (CASE WHEN plan_hash IS ? THEN 0 ELSE 1 END)"
                 ", plan_hash=?")
        vals += [h, h]
    cx = _cx()
    cx.execute(f"UPDATE jobs SET {sets}, updated=? WHERE id=?",
               (*vals, time.time(), job_id))
    cx.commit()


import contextlib as _ctx
_nolock = _ctx.nullcontext()


def update_plan(job_id, plan, base_rev=None, side=None):
    """Guarda el plan con concurrencia OPTIMISTA: con `base_rev`, solo si el plan guardado
    sigue en esa revision (si no, otro escritor -re-dirigir, otra pestaña- gano: no se pisa).
    `side` = (nombre, callable(rev_nueva) -> dict): la tarea lateral que anuncia el cambio se
    escribe en la MISMA transaccion (nadie ve el plan nuevo sin su aviso, ni el aviso sin el plan).
    -> (ok, plan_rev actual)."""
    h = plan_hash(plan)
    raw = json.dumps(plan)
    cx = _cx()
    with _side_lock if side else _nolock, cx:   # BEGIN IMMEDIATE: leer y escribir es atomico
        cx.execute("BEGIN IMMEDIATE")
        row = cx.execute("SELECT plan_rev, plan_hash, side FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return False, 0
        rev = int(row["plan_rev"] or 0)
        if base_rev is not None and int(base_rev) != rev:
            return False, rev
        if row["plan_hash"] == h:
            return True, rev
        sets, vals = "plan=?, plan_hash=?, plan_rev=?, meta=?, updated=?", \
            [raw, h, rev + 1, json.dumps(plan_meta(plan)), time.time()]
        if side:
            name, build = side
            sd = json.loads(row["side"]) if row["side"] else {}
            t = dict(build(rev + 1))
            t["updated"] = round(time.time(), 3)
            sd[name] = t
            sets += ", side=?"
            vals.append(json.dumps(sd))
        cx.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*vals, job_id))
    return True, rev + 1


def get(job_id):
    row = _cx().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in _JSON_COLS:
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def get_light(job_id):
    """Como get() pero SIN deserializar el plan (el plan v2 pesa: /status no lo necesita
    en cada sondeo). `plan_len` indica si hay plan."""
    row = _cx().execute(
        "SELECT id, kind, state, phase, pct, message, eta, elapsed, ext, params, clips, error, "
        "created, updated, stage, warnings, plan_rev, side, cancel, "
        "LENGTH(plan) AS plan_len FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in ("params", "clips", "warnings", "side"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def get_plan(job_id):
    row = _cx().execute("SELECT plan, plan_rev FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None, 0
    return (json.loads(row["plan"]) if row["plan"] else None), int(row["plan_rev"] or 0)


STATUS_KEYS = ("phase", "pct", "message", "eta", "elapsed", "clips", "error", "ext",
               "stage", "warnings", "plan_rev", "state", "kind")


def status(job_id, with_plan=False):
    """Shape que la UI ya conoce (phase/pct/message/eta/elapsed/clips/error/ext) + las claves
    del motor pro (stage, warnings, plan_rev, tareas laterales aplanadas). El plan completo
    solo va con with_plan=True. Si está en cola, el mensaje muestra la posición."""
    d = get_light(job_id)
    if not d:
        return None
    if d["state"] == "queued":
        pos = _cx().execute(
            "SELECT COUNT(*) FROM jobs WHERE state='queued' AND created <= ?",
            (d["created"],)).fetchone()[0]
        d["message"] = "En cola…" if pos <= 1 else f"En cola ({pos}º)…"
    out = {k: d.get(k) for k in STATUS_KEYS}
    out["plan_rev"] = int(out["plan_rev"] or 0)
    out["warnings"] = out["warnings"] or []
    out["has_plan"] = bool(d.get("plan_len"))
    side = d.get("side") or {}
    for name, t in side.items():
        if not isinstance(t, dict):
            continue
        out[name] = t
        # aplanado para la UI (contrato 6.1: preview_pct, preview_file)
        for k in SIDE_FLAT:
            if k in t:
                out[f"{name}_{k}"] = t[k]
    if with_plan:
        out["plan"], out["plan_rev"] = get_plan(job_id)
    return out


def side_update(job_id, name, replace=False, match=None, **kw):
    """Actualiza la tarea lateral `name` del trabajo (merge; replace=True la reinicia).
    `match`: solo si la tarea guardada tiene ese `tok` (una ejecucion vieja, ya reemplazada
    por otra, no pisa el estado de la nueva)."""
    with _side_lock:
        cx = _cx()
        row = cx.execute("SELECT side FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        side = json.loads(row["side"]) if row["side"] else {}
        if match is not None and (side.get(name) or {}).get("tok") != match:
            return None
        t = {} if replace else dict(side.get(name) or {})
        t.update(kw)
        t["updated"] = round(time.time(), 3)
        side[name] = t
        cx.execute("UPDATE jobs SET side=? WHERE id=?", (json.dumps(side), job_id))
        cx.commit()
        return t


def side_get(job_id, name):
    row = _cx().execute("SELECT side FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row or not row["side"]:
        return None
    return (json.loads(row["side"]) or {}).get(name)


def recent(limit=20):
    """Trabajos mas recientes con el resumen de su plan (sin deserializar planes: `meta` se
    calcula al escribir el plan; las filas de antes de la columna se completan una vez)."""
    cx = _cx()
    rows = cx.execute(
        "SELECT id, kind, state, phase, pct, message, error, created, updated, plan_rev, meta, "
        "ext, LENGTH(plan) AS plan_len FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        meta = None
        if d.get("meta"):
            try:
                meta = json.loads(d["meta"])
            except Exception:  # noqa
                meta = None
        if meta is None and d.get("plan_len"):
            row = cx.execute("SELECT plan FROM jobs WHERE id=?", (d["id"],)).fetchone()
            meta = plan_meta(row["plan"]) if row else None
            if meta is not None:
                cx.execute("UPDATE jobs SET meta=? WHERE id=?", (json.dumps(meta), d["id"]))
                cx.commit()
        d.pop("meta", None)
        d["has_plan"] = bool(d.pop("plan_len", None))
        d["plan_rev"] = int(d.get("plan_rev") or 0)
        d.update(meta or {"version": None, "topic": None, "duration": None,
                          "final_duration": None, "style": None, "shots": 0})
        out.append(d)
    return out


_SID_RE = re.compile(r'"sid"\s*:\s*"([0-9a-f]{12})"')


def referenced_sids():
    """Fuentes subidas (/sources) que algun PLAN referencia como medio propio ({sid, ...}):
    la limpieza del disco no debe borrarlas."""
    out = set()
    for r in _cx().execute("SELECT plan FROM jobs WHERE plan LIKE '%\"sid\"%'").fetchall():
        out.update(_SID_RE.findall(r["plan"] or ""))
    return out


# ---- cancelación cooperativa ----

def canceled_export(job_id):
    """Un export cancelado con plan vuelve a `review` (no es un error: la edicion sigue)."""
    update(job_id, state="review", phase="review", pct=100, stage="review", eta=None,
           message=CANCELED_EXPORT_MSG, error=None, cancel=0)


def request_cancel(job_id):
    """Marca cancel. Si aún está en cola, se cancela de inmediato (un export en cola con
    plan vuelve a `review`)."""
    cx = _cx()
    now = time.time()
    cur = cx.execute("""UPDATE jobs SET state='review', phase='review', pct=100, stage='review',
                        message=?, error=NULL, cancel=0, updated=?
                        WHERE id=? AND state='queued' AND kind='render' AND plan IS NOT NULL""",
                     (CANCELED_EXPORT_MSG, now, job_id))
    if cur.rowcount == 0:
        cur = cx.execute("""UPDATE jobs SET state='canceled', phase='error',
                            error='Cancelado.', updated=? WHERE id=? AND state='queued'""",
                         (now, job_id))
    if cur.rowcount == 0:
        cx.execute("UPDATE jobs SET cancel=1, updated=? WHERE id=? AND state='running'",
                   (time.time(), job_id))
    cx.commit()
    return True


def is_canceled(job_id):
    row = _cx().execute("SELECT cancel FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(row and row["cancel"])


class CancelChecker:
    """check() consulta el flag como mucho cada 0.5 s y lanza JobCanceled."""

    def __init__(self, job_id):
        self.job_id = job_id
        self._last = 0.0
        self._hit = False

    def canceled(self):
        """Como check() pero devuelve bool (para callbacks cancel() del motor pro)."""
        if self._hit:
            return True
        now = time.monotonic()
        if now - self._last < 0.5:
            return False
        self._last = now
        self._hit = is_canceled(self.job_id)
        return self._hit

    def check(self):
        if self.canceled():
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


def _error_label(kind):
    return "Análisis" if kind in ("analyze", "compose") else "Render"


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
            job = get_light(job_id) or {}
            if kind == "render" and job.get("plan_len"):
                canceled_export(job_id)          # el plan sigue valido: vuelve a review
            else:
                update(job_id, state="canceled", phase="error", error="Cancelado.")
            print(f"[worker{idx}] {job_id} cancelado")
        except Exception as e:  # noqa
            traceback.print_exc()
            update(job_id, state="error", phase="error",
                   error=f"{_error_label(kind)} falló: {e}")


def start_workers(handlers, n=None):
    global _handlers
    _handlers = dict(handlers)
    n = n or int(os.environ.get("REELFY_WORKERS", "1"))
    for i in range(n):
        threading.Thread(target=_worker_loop, args=(i,), daemon=True,
                         name=f"reelfy-worker-{i}").start()
    print(f"[jobstore] {n} worker(s) listos; db={DB}")
