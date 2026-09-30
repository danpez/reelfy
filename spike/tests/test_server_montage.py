"""app/server.py + app/jobstore.py: "Armar con IA" (montaje por guion) del lado del servidor.

  - /sources: fecha de grabacion (`created`), fps, hdr, rotacion, orden de grabacion (`rec`) y el
    estado de la transcripcion POR CLIP (`words`); GET /sources y GET /sources/{sid}/words.
  - Carril de palabras: cada video importado se transcribe solo, en segundo plano, de a uno (un
    montaje adelanta los suyos), con cache {sid}_words.json + {sid}_16k.wav reutilizada por una
    importacion repetida, regenerable y borrada con su fuente por la limpieza del disco.
  - POST /montage: trabajo kind "montage" en fase "assembly" (carril propio del jobstore), espera
    las palabras, llama a montage.build y guarda plan.montage; progreso, cancelacion y re-armado.
  - POST /compose {montage_job}: el MISMO id pasa a compose con plan.montage, el guion y sus
    nombres propios en el glosario de whisper (nunca el guion entero); las uniones llegan a la
    limpieza de tomas y a plan.shots.

Todo SINTETICO e inventado (el repo es publico): clips lavfi cuyo "texto dicho" va en su comentario,
whisper y montage.build FALSOS en el servidor de prueba (los reales tienen sus propias pruebas)."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import paths
from test_server_api import Api, _free_port, _multipart
from test_server_setup import _run
from test_sources_meta import clip

SPIKE = Path(__file__).resolve().parents[1]
PY = sys.executable
sys.path.insert(0, str(SPIKE / "app"))
import jobstore  # noqa: E402

MODEL_NAME = "ggml-large-v3-turbo.bin"


def _fake_model(data):
    """Modelo de voz FALSO (archivo disperso del tamano esperado: no ocupa disco) para que el
    servidor se de por listo; el whisper de estas pruebas es falso."""
    (data / "models").mkdir(parents=True, exist_ok=True)
    with open(data / "models" / MODEL_NAME, "wb") as f:
        f.truncate(paths.MODEL_SIZE)


# ================================================================ jobstore (en proceso)
@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(jobstore, "DB", tmp_path / "jobs.db")
    monkeypatch.setattr(jobstore, "_local", threading.local())
    real = {"next": jobstore._claim_next, "lane": jobstore._claim_lane}
    # un worker vivo de otro modulo de pruebas (servidor importado en este proceso) no debe tomar
    # los trabajos de esta base temporal
    monkeypatch.setattr(jobstore, "_claim_next", lambda: None)
    monkeypatch.setattr(jobstore, "_claim_lane", lambda kinds: None)
    monkeypatch.setattr(jobstore, "_LANE_KINDS", frozenset({"montage"}))
    jobstore.init()
    return real


MONTAGE = {"version": 1, "order": [{"sid": "a" * 12, "name": "BICI_0010.MOV", "rec": 0}],
           "script": {"source": "user", "text": "Soy Marta y reparo bicicletas.\n\nTe espero.",
                      "sections": [{"id": 1, "lines": ["L01"]}, {"id": 2, "lines": ["L02"]}],
                      "lines": [{"id": "L01", "text": "Soy Marta y reparo bicicletas."},
                                {"id": "L02", "text": "Te espero."}]},
           "slots": [{"id": "s01", "lines": ["L01"], "chosen": "c1", "cands": []},
                     {"id": "s02", "lines": ["L02"], "chosen": "c1", "cands": []}],
           "timeline": [{"slot": "s01", "cand": "c1", "sid": "a" * 12, "in": 0.5, "out": 3.0,
                         "start": 0.0, "vol": 1.0, "join": None},
                        {"slot": "s02", "cand": "c1", "sid": "b" * 12, "in": 1.0, "out": 2.2,
                         "start": 2.5, "vol": 1.2, "join": "section"}],
           "discarded": [], "uncovered": [], "extra": [], "stats": {}, "ms": 3}


def test_carril_del_montaje_no_lo_toman_los_workers_generales(store):
    jobstore.create("m1", kind="montage", ext=".mp4", params={"sids": ["a" * 12]}, phase="assembly")
    time.sleep(0.01)
    jobstore.create("c1", kind="compose", ext=".mp4", params={})
    # el montaje es mas viejo, pero los generales lo saltan; su carril solo toma montajes
    assert store["next"]() == ("c1", "compose")
    assert store["next"]() is None
    assert store["lane"](("montage",)) == ("m1", "montage")
    assert store["lane"](("montage",)) is None
    st = jobstore.status("m1")
    assert st["kind"] == "montage" and st["phase"] == "assembly" and st["state"] == "running"
    assert jobstore._error_label("montage") == "Montaje"
    assert jobstore._error_label("compose") == "Análisis"


def test_recuperacion_y_cancelacion_vuelven_al_ensamble(store):
    only = {"montage": MONTAGE}
    # corriendo al cerrarse la app: el montaje se re-encola; uno cancelado queda cancelado
    jobstore.create("m_run", kind="montage", ext=".mp4", phase="assembly")
    jobstore.create("m_can", kind="montage", ext=".mp4", phase="assembly")
    # la composicion de un montaje interrumpida: de vuelta al ensamble (sin perder el montaje)
    jobstore.create("c_mon", kind="compose", ext=".mp4", plan=only)
    jobstore.create("c_sin", kind="compose", ext=".mp4")
    for j in ("m_run", "m_can", "c_mon", "c_sin"):
        jobstore.update(j, state="running")
    jobstore.update("m_can", cancel=1)
    jobstore.recover()
    s = {j: jobstore.status(j) for j in ("m_run", "m_can", "c_mon", "c_sin")}
    assert s["m_run"]["state"] == "queued" and s["m_run"]["phase"] == "assembly"
    assert s["m_can"]["state"] == "canceled"
    assert (s["c_mon"]["state"], s["c_mon"]["phase"]) == ("review", "assembly")
    assert "montaje sigue" in s["c_mon"]["message"] and s["c_mon"]["has_plan"]
    assert s["c_sin"]["state"] == "error"
    # cancelar en cola: con montaje guardado vuelve al ensamble; sin el, cancelado
    jobstore.create("q_mon", kind="compose", ext=".mp4", plan=only)
    jobstore.create("q_new", kind="montage", ext=".mp4", phase="assembly")
    jobstore.request_cancel("q_mon")
    jobstore.request_cancel("q_new")
    a, b = jobstore.status("q_mon"), jobstore.status("q_new")
    assert (a["state"], a["phase"], a["message"]) == ("review", "assembly", jobstore.MONTAGE_CANCELED_MSG)
    assert jobstore.get_plan("q_mon")[0] == only
    assert (b["state"], b["phase"]) == ("canceled", "error")
    # back_to_assembly solo con un plan que es SOLO un montaje
    jobstore.create("full", kind="compose", ext=".mp4", plan={"video": "/x.mp4", "montage": MONTAGE})
    assert not jobstore.back_to_assembly("full", "x")
    assert jobstore.montage_only(only) and not jobstore.montage_only({"video": "v", "montage": {}})


def test_worker_montaje_cancelado_o_fallido_con_montaje_previo(store, monkeypatch):
    """El worker: un re-armado cancelado o fallido de un proyecto que ya tenia montaje vuelve al
    ensamble con el montaje anterior; uno nuevo sin montaje queda cancelado/error."""
    calls = []

    def boom(job_id, job):
        calls.append(job_id)
        raise (jobstore.JobCanceled() if job_id.startswith("can") else RuntimeError("se rompio"))
    ids = iter([("can_old", "montage"), ("err_old", "montage"), ("can_new", "montage")])
    stop = threading.Event()

    def claim():
        nxt = next(ids, None)
        if nxt is None:
            stop.set()                # el hilo del worker termina (no queda vivo en el proceso)
        return nxt
    monkeypatch.setattr(jobstore, "_claim_next", claim)
    monkeypatch.setitem(jobstore._handlers, "montage", boom)
    for j in ("can_old", "err_old"):
        jobstore.create(j, kind="montage", ext=".mp4", plan={"montage": MONTAGE}, phase="assembly")
    jobstore.create("can_new", kind="montage", ext=".mp4", phase="assembly")
    th = threading.Thread(target=jobstore._worker_loop, args=(9, None, stop), daemon=True)
    th.start()
    th.join(10)
    assert not th.is_alive() and len(calls) == 3
    a, b, c = (jobstore.status(j) for j in ("can_old", "err_old", "can_new"))
    assert (a["state"], a["phase"]) == ("review", "assembly") and a["error"] is None
    assert (b["state"], b["phase"]) == ("review", "assembly") and "Montaje falló: se rompio" in b["error"]
    assert (c["state"], c["phase"], c["error"]) == ("canceled", "error", "Cancelado.")


def test_resumen_del_montaje_y_fuentes_de_sus_parametros(store):
    m = jobstore.plan_meta({"montage": MONTAGE})
    assert m["topic"] == "Soy Marta y reparo bicicletas." and m["duration"] == pytest.approx(3.7)
    assert m["shots"] == 2 and m["montage"] == {"clips": 1, "slots": 2, "uncovered": 0}
    # compuesto: el resumen normal (el montaje ya no manda)
    assert "montage" not in jobstore.plan_meta({"video": "/v.mp4", "duration": 9.0, "montage": MONTAGE})
    # guion sin lineas parseadas: su primer renglon
    assert jobstore.plan_meta({"montage": {"script": {"text": "\n  Hola mundo\nY mas"}}})["topic"] == "Hola mundo"
    sids = ["a" * 12, "b" * 12]
    jobstore.create("m1", kind="montage", ext=".mp4", params={"sids": sids + ["no-es-sid"]},
                    phase="assembly")
    assert jobstore.params_sids({"sids": sids + [3, "zz"]}) == set(sids)
    assert jobstore.params_sids('{"sids": ["' + "c" * 12 + '"]}') == {"c" * 12}
    assert jobstore.sids_by_job()["m1"] == set(sids)
    jobstore.update("m1", state="review", plan={"montage": MONTAGE})
    rec = next(r for r in jobstore.recent(10) if r["id"] == "m1")
    assert rec["phase"] == "assembly" and rec["topic"] == "Soy Marta y reparo bicicletas."


# ================================================================ carril de palabras y ayudantes
LANE_PRELUDE = '''
import server as S, jobstore, pipeline, subprocess, shutil
from pathlib import Path
DATA = Path(os.environ["REELFY_DATA"])
S.SOURCES.mkdir(parents=True, exist_ok=True)
CALLS, LOCK, GATE = [], threading.Lock(), {}
RUNNING = [0]

def fake_transcribe(src, wav):
    sid = Path(src).stem
    with LOCK:
        RUNNING[0] += 1
        CALLS.append((sid, RUNNING[0], threading.current_thread().name))
    ev = GATE.get(sid)
    if ev is not None:
        ev.wait(20)
    try:
        if sid in FAIL:
            FAIL.discard(sid)
            raise RuntimeError("whisper se cayo")
        has = pipeline.has_audio(src)
        if has:
            pipeline.extract_audio(src, wav)
        return {"audio": has, "words": [{"text": "hola", "start": 0.2, "end": 0.5}] if has else [],
                "takes": [], "dur": 1.0, "dtw": False, "align": "fake", "ms": 1}
    finally:
        with LOCK:
            RUNNING[0] -= 1
FAIL = set()
S._transcribe_clip = fake_transcribe

def add(sid, src):
    dst = S.SOURCES / f"{sid}{Path(src).suffix.lower()}"
    shutil.copyfile(src, dst)
    S._ingest_source(dst, sid, Path(src).name, False, make_proxy=False)
    return dst

def wait_ready(sids, t=30):
    t0 = time.time()
    while time.time() - t0 < t:
        if all(S._words_state(s) in ("ready", "error") for s in sids):
            return
        time.sleep(0.05)
    raise SystemExit(f"timeout: {[(s, S._words_state(s)) for s in sids]}")
'''


def _lane_env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _fake_model(data)
    clips = {}
    for k, audio in (("a", True), ("b", True), ("c", True), ("d", True), ("e", True), ("m", False)):
        clips[k] = str(clip(tmp_path / f"CLIP_{k}.mp4", dur=1.0, audio=audio, comment=f"clip {k}",
                            freq=300 + 40 * len(clips)))
    return data, {"CLIPS": json.dumps(clips), "REELFY_WORKERS": "0"}


def test_carril_de_palabras_de_a_uno_prioridad_error_y_cache(tmp_path):
    data, env = _lane_env(tmp_path)
    code = LANE_PRELUDE + '''
C = json.loads(os.environ["CLIPS"])
A, B, Cc, D, E, M = ("a0000000000a", "b0000000000b", "c0000000000c", "d0000000000d", "e0000000000e",
                     "f0000000000f")
# 1) el primero se queda trabado: mientras, entran otros dos al final y uno ADELANTE (un montaje)
GATE[A] = threading.Event()
add(A, C["a"])
for _ in range(100):
    if CALLS:
        break
    time.sleep(0.02)
assert CALLS and CALLS[0][0] == A and CALLS[0][2] == "reelfy-words", CALLS
assert S._words_state(A) == "pending"
item = S._source_item(A)
assert item["words"] == "pending" and item["type"] == "video" and item["audio"] is True
for sid, k in ((B, "b"), (Cc, "c")):
    add(sid, C[k])
dst_d = S.SOURCES / f"{D}.mp4"; shutil.copyfile(C["d"], dst_d)
S._ingest_source(dst_d, D, "CLIP_d.mp4", False, make_proxy=False, transcribe=False)
assert S._words_state(D) == "pending" and D not in S._WORDS_Q      # transcribe=False: no se encola
assert S._words_kick([D, A, B], front=True) == 1                   # A corre, B ya estaba: se adelanta
assert S._WORDS_Q[:2] == [D, B] and S._WORDS_Q[2] == Cc, S._WORDS_Q
GATE[A].set()
wait_ready([A, B, Cc, D])
assert [c[0] for c in CALLS] == [A, D, B, Cc], CALLS
assert max(c[1] for c in CALLS) == 1                               # nunca dos a la vez
for sid in (A, B, Cc, D):
    doc = S._words_load(sid)
    assert doc["sid"] == sid and doc["audio"] and doc["words"][0]["text"] == "hola"
    assert doc["key"]["v"] == S.WORDS_VERSION and doc["key"]["hash"] == S._quick_hash(S._resolve_source(sid))
    assert doc["key"]["model"] == "ggml-large-v3-turbo.bin" and (S.SOURCES / f"{sid}_16k.wav").exists()
assert not list(S.SOURCES.glob("*.part*"))
# 2) sin audio: listo (no es un error) y sin wav
add(M, C["m"]); wait_ready([M])
assert S._words_state(M) == "ready" and S._words_load(M)["audio"] is False
assert not (S.SOURCES / f"{M}_16k.wav").exists()
# 3) un fallo queda en error (no se reintenta solo); un montaje nuevo (retry) lo reintenta
FAIL.add(E); add(E, C["e"]); wait_ready([E])
assert S._words_state(E) == "error" and "whisper se cayo" in S._WORDS_ERR[E]
assert S._words_kick([E]) == 0 and S._words_state(E) == "error"
n = len(CALLS)
assert S._words_kick([E], retry=True) == 1
wait_ready([E]); assert S._words_state(E) == "ready" and len(CALLS) == n + 1
# 4) "Liberar espacio" borro el wav: solo se re-extrae el audio (whisper no vuelve a correr)
(S.SOURCES / f"{A}_16k.wav").unlink()
assert S._words_state(A) == "pending"
n = len(CALLS); S._words_kick([A]); wait_ready([A])
assert len(CALLS) == n and (S.SOURCES / f"{A}_16k.wav").exists()
# 5) la fuente cambio (otra huella): la transcripcion vieja ya no vale
with open(S._resolve_source(B), "ab") as f:
    f.write(b"x" * 10)
assert S._words_state(B) == "pending" and S._words_load(B) is None
# 6) importacion repetida: comparte la transcripcion y el audio (enlace duro) sin whisper
dup = "d0000000000e"
dst = S.SOURCES / f"{dup}.mp4"; shutil.copyfile(C["c"], dst)
assert S.dedupe_import(dst) is not None
n = len(CALLS)
assert S._reuse_source_words(Cc, dup)
S._ingest_source(dst, dup, "CLIP_c.mp4", False, make_proxy=False)
assert S._words_state(dup) == "ready" and len(CALLS) == n
assert S._words_load(dup)["sid"] == dup
assert os.stat(S.SOURCES / f"{dup}_16k.wav").st_ino == os.stat(S.SOURCES / f"{Cc}_16k.wav").st_ino
assert not S._reuse_source_words("0" * 12, "1" * 12)
# 7) los derivados NO son originales (deduplicacion y almacenamiento)
for suf in ("_words.json", "_16k.wav", "_meta.json", "_t.jpg"):
    p = S.SOURCES / f"{A}{suf}"
    assert p.exists() and not S._is_source_original(p), suf
assert S._is_source_original(S._resolve_source(A))
assert all(S._is_source_original(p) for p in S._import_candidates(Path("/x")) if p.parent == S.SOURCES)
print("OK")
'''
    _run(code, data, env)


def test_limpieza_del_disco_conserva_los_clips_del_montaje(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    code = '''
import server as S, jobstore
from pathlib import Path
S.SOURCES.mkdir(parents=True, exist_ok=True)
old = time.time() - 3 * 86400
KEEP, ALT, GONE = "a0000000000a", "b0000000000b", "c0000000000c"
for sid in (KEEP, ALT, GONE):
    for suf, n in ((".mov", 3000), ("_meta.json", 20), ("_t.jpg", 50), ("_words.json", 200),
                   ("_16k.wav", 900), ("_proxy.mp4", 400)):
        p = S.SOURCES / f"{sid}{suf}"
        p.write_bytes(os.urandom(n))
        os.utime(p, (old, old))
# montaje en ensamble: sus clips (tambien las alternativas) estan SOLO en sus parametros
jobstore.create("m00000000001", kind="montage", ext=".mp4", params={"sids": [KEEP, ALT]}, phase="assembly")
jobstore.update("m00000000001", state="review")
for _ in range(100):
    res = S.disk_gc("prueba")
    if "skipped" not in res:
        break
    time.sleep(0.1)
left = {p.name for p in S.SOURCES.iterdir()}
assert all(f"{sid}{suf}" in left for sid in (KEEP, ALT) for suf in (".mov", "_words.json", "_16k.wav")), left
assert not any(n.startswith(GONE) for n in left), left     # huerfana: se va con TODO lo suyo
# almacenamiento: el original y la transcripcion son del proyecto; el audio 16k y el proxy, cache
u = S._job_units("m00000000001", S._storage_ctx())
names = {k: {p.name for p in v} for k, v in u.items()}
assert f"{KEEP}.mov" in names["project"] and f"{KEEP}_words.json" in names["project"]
assert f"{KEEP}_16k.wav" in names["cache"] and f"{KEEP}_proxy.mp4" in names["cache"]
# la composicion del montaje (mismo id) sigue protegiendo las alternativas por sus parametros
jobstore.requeue("m00000000001", kind="compose", params={"spec": {"main": [
    {"path": str(S.SOURCES / f"{KEEP}.mov")}]}, "sids": [KEEP, ALT]})
jobstore.update("m00000000001", state="review")
for _ in range(100):
    res = S.disk_gc("prueba")
    if "skipped" not in res:
        break
    time.sleep(0.1)
assert (S.SOURCES / f"{ALT}.mov").exists() and (S.SOURCES / f"{ALT}_words.json").exists()
print("OK")
'''
    _run(code, data, {"REELFY_WORKERS": "0"})


def test_ayudantes_guion_glosario_orden_y_resultado(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    code = '''
import server as S, types
from pathlib import Path
# --- glosario de whisper desde el guion: nombres propios y cifras, nunca el guion entero
g = ("Soy Marta y con mi equipo abrimos Rueda Libre, un taller en Querétaro.\\n"
     "Arreglamos más de 200 bicis al mes y 15 mil kilómetros de cadena.\\n\\n"
     "¿Precio? Cero. Escríbenos por WhatsApp y di la palabra \\"Rueda\\".\\n"
     "La IA nos ayuda con 5 millones de datos.")
terms = S._script_terms(g)
assert terms == ["Marta", "Rueda Libre", "Querétaro", "200", "15 mil", "WhatsApp", "Rueda", "IA",
                 "5 millones"], terms
# lo que abre una oracion no cuenta ("Soy", "Arreglamos", "¿Precio?", "Cero", "Escríbenos", "La")
for w in ("Soy", "Arreglamos", "Precio", "Cero", "Escríbenos", "La"):
    assert w not in terms
gl = S._script_glossary(g, "Pedal Pro, marta")
assert gl.startswith("Pedal Pro, marta, Rueda Libre") and "Marta," not in gl   # sin repetir
assert len(gl) <= S.GLOSS_MAX_CHARS and "taller" not in gl
long = "\\n".join(f"Hablo con Persona{i} Apellido{i} hoy." for i in range(60))
assert len(S._script_glossary(long).split(", ")) <= S.GLOSS_MAX_TERMS
assert len(S._script_glossary(long)) <= S.GLOSS_MAX_CHARS
assert S._script_glossary("", "") == "" and S._script_glossary(None, "A, B") == "A, B"
# --- guion en secciones y lineas (un renglon en blanco abre otra seccion)
secs, lines = S._script_parse("Hola.\\n  Soy Marta.\\n\\n\\nTe espero.\\n")
assert [l["id"] for l in lines] == ["L01", "L02", "L03"] and lines[1]["text"] == "Soy Marta."
assert secs == [{"id": 1, "lines": ["L01", "L02"]}, {"id": 2, "lines": ["L03"]}]
# --- orden de grabacion: fecha de los metadatos, luego nombre natural, luego subida
items = [{"name": "toma_10.mp4", "created_src": "mtime", "created_ts": 5.0},
         {"name": "BICI_0011.mp4", "created_src": "creation_time", "created_ts": 300.0},
         {"name": "BICI_0010.MOV", "created_src": "apple", "created_ts": 100.0},
         {"name": "toma_2.mp4", "created_src": "mtime", "created_ts": 9.0},
         {"name": "BICI_0009.MOV", "created_src": "apple", "created_ts": 200.0},
         {"name": "BICI_0001.MOV", "created_src": "apple", "created_ts": 200.0},
         {"name": "copia.mp4", "created_src": None, "created_ts": None},
         {"name": "toma_2.mp4", "created_src": "mtime", "created_ts": 1.0}]
order = [items[k]["name"] + str(k) for k in S.rec_order(items)]
assert order == ["BICI_0010.MOV2", "BICI_0001.MOV5", "BICI_0009.MOV4", "BICI_0011.mp41",
                 "copia.mp46", "toma_2.mp43", "toma_2.mp47", "toma_10.mp40"], order
# --- resultado del motor normalizado al contrato
clips = [{"sid": "a0000000000a", "name": "A.MOV", "rec": 0, "created": "2026-03-14T10:00:00-06:00"},
         {"sid": "b0000000000b", "name": "B.MOV", "rec": 1, "created": None}]
res = {"timeline": [{"sid": "a0000000000a", "in": 0, "out": 1, "start": 0},
                    {"sid": "ffffffffffff", "in": 0, "out": 1, "start": 1}, "basura"],
       "slots": None, "stats": "x"}
m = S._montage_result(res, clips, {"script": {"text": "Uno.\\n\\nDos.", "source": "user"},
                                   "intercut": False}, 0.25)
assert m["version"] == 1 and [t["sid"] for t in m["timeline"]] == ["a0000000000a"]
assert m["order"] == [{"sid": "a0000000000a", "name": "A.MOV", "rec": 0, "created": "2026-03-14T10:00:00-06:00"},
                      {"sid": "b0000000000b", "name": "B.MOV", "rec": 1, "created": None}]
assert m["script"]["source"] == "user" and [l["text"] for l in m["script"]["lines"]] == ["Uno.", "Dos."]
assert m["slots"] == [] and m["discarded"] == [] and m["uncovered"] == [] and m["extra"] == []
assert m["stats"] == {} and m["ms"] == 250 and m["intercut"] is False
m2 = S._montage_result({"script": {"source": "detected", "text": "Hola.", "lines": [{"id": "L01", "text": "Hola."}],
                                   "sections": [{"id": 1, "lines": ["L01"]}]}, "ms": 7}, clips,
                       {"script": None}, 1.0)
assert m2["script"]["source"] == "detected" and m2["ms"] == 7
assert S._montage_result({}, clips, {"script": None}, 0)["script"] is None
# --- montage.build: solo los argumentos que acepta (el motor puede crecer sin romper el servidor)
got = {}
def build(clips, script=None, cancel=None):
    got.update(clips=clips, script=script, cancel=cancel)
    return {"timeline": []}
sys.modules["montage"] = types.SimpleNamespace(build=build)
S._montage_build(clips, script="Hola.", glossary="X", intercut=False, on_step=print, cancel=bool)
assert got == {"clips": clips, "script": "Hola.", "cancel": bool}
# con juez: el LLM local del motor (sin ollama, None) solo si build lo acepta y no se apago
judged = {}
def build2(clips, script=None, *, intercut=True, glossary=None, llm=None):
    judged.update(llm=llm, intercut=intercut, glossary=glossary)
    return {}
sys.modules["montage"] = types.SimpleNamespace(build=build2, local_llm=lambda: "JUEZ")
S._montage_build(clips, intercut=False, glossary="X")
assert judged == {"llm": "JUEZ", "intercut": False, "glossary": "X"}
S._montage_build(clips, llm=False)
assert judged["llm"] is None
sys.modules["montage"] = None                 # sin el motor: error claro, no un ImportError crudo
try:
    S._montage_build(clips)
    raise SystemExit("debio fallar")
except RuntimeError as e:
    assert "motor de montaje" in str(e)
# --- items de /compose
assert S._montage_item({"slot": "s01", "cand": "c2", "join": "splice", "conf": 0.8, "setup": 2,
                        "x": 1}) == {"slot": "s01", "cand": "c2", "join": "splice", "conf": 0.8, "setup": 2}
assert S._montage_item({"slot": 1, "join": "otra", "conf": float("nan")}) == {"slot": "1", "join": None}
assert S._montage_item(None) is None
assert S._vol_param("auto") == "auto" and S._vol_param(None, True) == "auto"
assert S._vol_param(None) == 1.0 and S._vol_param("2.5") == 2.5 and S._vol_param(9) == 4.0
assert S._script_param("  ") is None and S._script_param({"text": "Hola\\r\\nMundo", "source": "detected"}) == \\
    {"text": "Hola\\nMundo", "source": "detected"}
assert S._script_param("Hola")["source"] == "user"
print("OK")
'''
    _run(code, data, {"REELFY_WORKERS": "0"})


def test_takes_del_video_combinado_respetan_uniones_y_secciones_del_guion(tmp_path):
    """POST /takes sobre un montaje ya compuesto: la re-deteccion recibe las uniones de plan.shots
    (ningun corte de tomas las cruza) y conserva las secciones sembradas desde el guion."""
    data = tmp_path / "data"
    data.mkdir()
    code = '''
import server as S, jobstore, takes, functools
seen = []
real = takes.detect
@functools.wraps(real)
def spy(*a, **k):
    seen.append(k.get("joins"))
    return real(*a, **k)
takes.detect = spy
said = "Hoy reparo bicicletas de, hoy reparo bicicletas de montaña en el centro. Te espero.".split()
words, t = [], 0.3
for w in said:
    d = 0.1 + 0.06 * len(w)
    words.append({"text": w, "start": round(t, 3), "end": round(t + d, 3)})
    t += d + (0.9 if w.endswith(",") else 0.08)
dur = round(t + 0.5, 3)
j1 = words[2]["start"] - 0.02         # union a media retoma (entre "reparo" y "bicicletas")
j2 = words[-2]["end"] + 0.05
plan = {"version": 2, "fx": {}, "video": "/x.mp4", "duration": dur, "phrases": [words], "cuts": [],
        "shots": [{"start": 0.0, "end": j1, "src": "A.MOV"},
                  {"start": j1, "end": j2, "src": "B.MOV", "join": "splice", "setup": 1},
                  {"start": j2, "end": dur, "src": "C.MOV", "join": "section", "setup": 2}],
        "takes": {"version": 1, "sections": [{"start": 0.0, "title": "Quien soy"},
                                             {"start": j2, "title": "Te espero"}],
                  "sections_src": "guion", "topic": None}}
jobstore.create("t00000000001", kind="compose", ext=".mp4", plan=plan)
jobstore.update("t00000000001", state="review", phase="review")
res = S.takes_run("t00000000001", {"use_llm": False})
assert seen and seen[-1] == [round(j1, 3), round(j2, 3)], seen
stored, _ = jobstore.get_plan("t00000000001")
assert stored["takes"]["sections"] == plan["takes"]["sections"], stored["takes"]
assert stored["takes"]["sections_src"] == "guion"
for c in res["cuts"]:
    assert not (c["start"] + 0.02 < j1 < c["end"] - 0.02 or c["start"] + 0.02 < j2 < c["end"] - 0.02), c
# sin uniones (proyecto normal): detect no recibe joins
plan2 = dict(plan, shots=[{"start": 0.0, "end": dur, "src": "A.MOV"}], takes={"version": 1})
jobstore.create("t00000000002", kind="analyze", ext=".mp4", plan=plan2)
jobstore.update("t00000000002", state="review", phase="review")
S.takes_run("t00000000002", {"use_llm": False})
assert seen[-1] is None, seen
print("OK")
'''
    _run(code, data, {"REELFY_WORKERS": "0"})


# ================================================================ servidor real con whisper y motor falsos
LAUNCH = '''
import json, os, sys, threading, time, subprocess
from pathlib import Path
sys.path.insert(0, "app")
import server as S
import uvicorn
LOG = Path(os.environ["FAKE_LOG"])
GATE = Path(os.environ["FAKE_GATE"])
_L = threading.Lock()

def log(ev, **kw):
    with _L, open(LOG, "a") as f:
        f.write(json.dumps(dict(ev=ev, **kw), ensure_ascii=False) + "\\n")

def said(src):
    r = subprocess.run([str(S.paths.FFPROBE), "-v", "error", "-show_entries", "format_tags=comment",
                        "-of", "default=nk=1:nw=1", str(src)], capture_output=True, text=True)
    return r.stdout.strip()

def fake_transcribe(src, wav):
    """whisper FALSO: el texto 'dicho' viene en el comentario del clip sintetico."""
    text = said(src)
    log("transcribe", said=text, sid=Path(src).stem, thread=threading.current_thread().name)
    while GATE.exists():
        time.sleep(0.05)
    has = S.pipeline.has_audio(src)
    if has:
        S.pipeline.extract_audio(src, wav)
    dur = S.pipeline.probe_duration(src)
    words = [{"text": w, "start": round(0.15 + 0.12 * i, 3), "end": round(0.24 + 0.12 * i, 3)}
             for i, w in enumerate(text.split())] if has else []
    return {"audio": has, "words": words, "takes": [], "dur": dur, "dtw": False, "align": "falso", "ms": 1}

def fake_build(clips, script=None, glossary="", intercut=True, on_step=None, cancel=None, llm=True):
    """montage.build FALSO: una parte por clip con voz, en orden de grabacion (+ una fuente ajena
    que el servidor debe quitar)."""
    log("build", clips=[dict({k: c[k] for k in ("sid", "name", "rec", "created", "created_src",
                                                 "has_audio", "hdr", "fps", "rotation", "setup")},
                             words=len(c["words"]), wav=bool(c["audio"]) and Path(c["audio"]).exists(),
                             thumb=bool(c["thumb"]) and Path(c["thumb"]).exists(),
                             path=Path(c["path"]).exists()) for c in clips],
        script=script, glossary=glossary, intercut=intercut, llm=llm)
    if on_step:
        on_step(50, "Comparando tomas…")
    while GATE.exists():
        if cancel and cancel():
            raise S.JobCanceled()
        time.sleep(0.05)
    slots, tl, disc, t = [], [], [], 0.0
    for c in clips:
        if not c["audio"] or not c["words"]:          # "audio" = ruta del wav 16 kHz o None
            disc.append({"sid": c["sid"], "in": 0.0, "out": c["dur"], "text": "", "why": "sin_audio"})
            continue
        a = max(0.0, c["words"][0]["start"] - 0.1)
        b = min(float(c["dur"]), c["words"][-1]["end"] + 0.1)
        sl = f"s{len(slots) + 1:02d}"
        slots.append({"id": sl, "lines": [f"L{len(slots) + 1:02d}"], "chosen": "c1", "cands": [
            {"id": "c1", "sid": c["sid"], "in": a, "out": b, "w0": 0, "w1": len(c["words"]) - 1,
             "text": " ".join(w["text"] for w in c["words"]), "score": 0.9, "fidelity": 1.0,
             "coverage": 1.0, "flags": [], "reason": "Última toma completa y fiel al guion",
             "setup": c["rec"] % 2}]})
        tl.append({"slot": sl, "cand": "c1", "sid": c["sid"], "in": a, "out": b, "start": round(t, 3),
                   "vol": 1.0, "join": None if not tl else ("section" if len(tl) % 2 else "splice")})
        t += b - a
    tl.append({"slot": "sx", "cand": "c1", "sid": "ffffffffffff", "in": 0, "out": 1, "start": t})
    return {"version": 1, "slots": slots, "timeline": tl, "discarded": disc, "uncovered": [],
            "extra": [], "stats": {"clips": len(clips)}, "ms": 4}

def fake_analyze(video, glossary="", n=2, align=True, on_step=None, joins=None):
    """analisis FALSO del video compuesto (sin whisper): registra glosario y uniones."""
    log("analyze", glossary=glossary, joins=joins, video=str(video))
    if on_step:
        on_step(50, "Transcribiendo (español)…")
    dur = S.pipeline.probe_duration(video)
    w = [{"text": "hola", "start": 0.2, "end": 0.5}, {"text": "mundo", "start": 0.6, "end": 0.9}]
    return {"stem": Path(video).stem, "video": str(video), "wav": "", "dims": [360, 640],
            "duration": dur, "phrases": [w], "cuts": [], "highlights": [], "beats": [],
            "broll": [], "camera": [],
            "takes": {"version": 1, "sensitivity": "normal", "topic": None, "summary": None,
                      "sections": [{"start": 0.0, "title": "Tema del LLM"}],
                      "stats": {"n": 0, "enabled": 0, "removed_s": 0.0, "suggested": 0, "by_kind": {}},
                      "llm": False, "ms": 1}}

S._transcribe_clip = fake_transcribe
S._montage_build = fake_build
S.pipeline.analyze = fake_analyze
uvicorn.run(S.app, host="127.0.0.1", port=int(os.environ["REELFY_PORT"]), log_level="warning")
'''


@pytest.fixture(scope="module")
def msrv(tmp_path_factory):
    data = tmp_path_factory.mktemp("montage_data")
    _fake_model(data)
    port = _free_port()
    gate = data / "gate"
    env = dict(os.environ, REELFY_PORT=str(port), REELFY_DATA=str(data), REELFY_GATED="0",
               REELFY_AUTO_DOWNLOAD="0", REELFY_OLLAMA_URL="http://127.0.0.1:9",
               REELFY_ACE_BIN=str(data / "sin_ace"), REELFY_MUSIC_MODELS=str(data / "sin_musica"),
               REELFY_WORKERS="1", REELFY_SRC_PROXY="0", REELFY_BG_ANALYSIS="0",
               FAKE_LOG=str(data / "fake.log"), FAKE_GATE=str(gate))
    env.pop("REELFY_LLM_MODEL", None)
    log = open(data / "server_stdout.log", "w")
    proc = subprocess.Popen([PY, "-u", "-c", LAUNCH], cwd=str(SPIKE), env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    api = Api(f"http://127.0.0.1:{port}")
    try:
        for _ in range(160):
            try:
                api.get("/tracks", timeout=2)
                break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError((data / "server_stdout.log").read_text()[-3000:])
                time.sleep(0.25)
        yield {"api": api, "data": data, "gate": gate, "proc": proc}
    finally:
        gate.unlink(missing_ok=True)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def _events(srv, ev=None):
    f = srv["data"] / "fake.log"
    rows = [json.loads(ln) for ln in f.read_text().splitlines()] if f.exists() else []
    return [r for r in rows if ev is None or r["ev"] == ev]


def _upload(api, files, transcribe=None):
    data, ctype = _multipart(files)
    if transcribe is not None:
        bnd = ctype.split("boundary=")[1]
        data = data[:-len(f"--{bnd}--\r\n")] + (
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"transcribe\"\r\n\r\n{transcribe}\r\n"
            f"--{bnd}--\r\n").encode()
    req = urllib.request.Request(api.base + "/sources", data=data, method="POST",
                                 headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["sources"]


def _wait(fn, timeout=60, every=0.2):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timeout ({last})")


# sesion inventada: taller de bicicletas ficticio (grabado en desorden de nombre)
SESSION = [  # (clave, archivo, dicho, fecha Apple, creation_time, audio)
    ("A", "BICI_0010.MOV", "hola soy Marta y reparo bicicletas", "2026-03-14T10:00:05-0600", None, True),
    ("B", "BICI_0009.MOV", "hola soy Marta y reparo bicicletas en Querétaro",
     "2026-03-14T10:03:00-0600", None, True),
    ("C", "MUDO_0012.MOV", "", "2026-03-14T10:04:00-0600", None, False),
    ("D", "BICI_0011.mp4", "arreglamos más de 200 bicis al mes", None, "2026-03-14T16:05:00Z", True),
    ("E", "toma_10.mp4", "te espero en el taller", None, None, True),
    ("F", "toma_2.mp4", "síguenos para más", None, None, True),
]
SCRIPT = ("Hola, soy Marta y reparo bicicletas en Querétaro.\n"
          "Arreglamos más de 200 bicis al mes en Rueda Libre.\n\n"
          "Síguenos para más.\nTe espero en el taller.")


@pytest.fixture(scope="module")
def session(msrv, tmp_path_factory):
    d = tmp_path_factory.mktemp("sesion")
    files = {}
    for k, name, text, apple, ctime, audio in SESSION:
        files[k] = clip(d / name, dur=2.0, size="360x640", audio=audio, apple=apple, ctime=ctime,
                        comment=text or "mudo", freq=260 + 30 * len(files))
    upload = ["E", "D", "A", "F", "C", "B"]
    src = _upload(msrv["api"], [files[k] for k in upload])
    return {"files": files, "upload": upload, "items": src,
            "sid": {k: it["id"] for k, it in zip(upload, src)}}


def test_sources_metadatos_orden_y_estado_de_palabras(msrv, session):
    api, items = msrv["api"], session["items"]
    by = {k: it for k, it in zip(session["upload"], items)}
    for it in items:
        assert set(it) >= {"id", "name", "type", "ext", "dur", "w", "h", "thumb", "created", "fps",
                           "hdr", "rotation", "words", "rec", "audio"}
        assert it["type"] == "video" and it["fps"] == pytest.approx(30.0) and it["hdr"] is False
        assert it["rotation"] == 0 and (it["w"], it["h"]) == (360, 640)
        assert it["words"] in ("pending", "ready")
    assert by["A"]["created"] == "2026-03-14T10:00:05-06:00" and by["A"]["created_src"] == "apple"
    assert by["D"]["created"] == "2026-03-14T16:05:00+00:00" and by["D"]["created_src"] == "creation_time"
    assert by["E"]["created_src"] == "mtime" and by["C"]["audio"] is False
    # orden de GRABACION (no de subida ni de nombre): fechas y despues nombre natural
    rec = sorted(session["upload"], key=lambda k: by[k]["rec"])
    assert rec == ["A", "B", "C", "D", "F", "E"], rec
    # la transcripcion por clip llega sola (carril propio); el clip mudo queda listo sin palabras
    ids = ",".join(it["id"] for it in items)
    got = _wait(lambda: (lambda r: r if all(x["words"] == "ready" for x in r) else None)(
        api.get(f"/sources?ids={ids}")["sources"]), 60)
    assert [x["id"] for x in got] == [it["id"] for it in items]          # en el orden pedido
    assert [x["rec"] for x in got] == [it["rec"] for it in items]
    w = api.get(f"/sources/{session['sid']['B']}/words")
    assert [x["text"] for x in w["words"]] == "hola soy Marta y reparo bicicletas en Querétaro".split()
    assert w["audio"] is True and w["sid"] == session["sid"]["B"] and w["takes"] == []
    assert w["key"]["v"] == 1 and len(w["key"]["hash"]) > 10
    mudo = api.get(f"/sources/{session['sid']['C']}/words")
    assert mudo["audio"] is False and mudo["words"] == []
    tx = _events(msrv, "transcribe")
    assert {e["thread"] for e in tx} == {"reelfy-words"}
    assert sorted(e["sid"] for e in tx) == sorted(session["sid"].values())   # uno por clip, sin juntar
    src_dir = msrv["data"] / "input" / "sources"
    for sid in session["sid"].values():
        assert (src_dir / f"{sid}_words.json").exists()
    assert not (src_dir / f"{session['sid']['C']}_16k.wav").exists()
    assert (src_dir / f"{session['sid']['A']}_16k.wav").exists()
    # GET /sources sin ids: todas (con su orden); 404 de lo que no existe
    allsrc = api.get("/sources")["sources"]
    assert {x["id"] for x in items} <= {x["id"] for x in allsrc}
    assert api.get("/sources/ffffffffffff/words", raw=True)[0] == 404
    assert api.get("/sources?ids=ffffffffffff")["sources"] == []


def test_sources_importacion_repetida_imagen_y_sin_transcribir(msrv, session, tmp_path):
    api = msrv["api"]
    n0 = len(_events(msrv, "transcribe"))
    img = tmp_path / "foto.png"
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=64x64:d=1", "-frames:v", "1", str(img)], check=True)
    again = _upload(api, [session["files"]["A"], img])
    assert again[0]["dedup"] is True and again[0]["words"] == "ready"      # reusa la de su gemela
    assert again[0]["created"] == "2026-03-14T10:00:05-06:00"
    assert again[1]["type"] == "image" and again[1]["words"] is None and again[1]["audio"] is False
    code, body, _ = api.get(f"/sources/{again[1]['id']}/words", raw=True)
    assert code == 404 and json.loads(body)["words"] is None
    w = api.get(f"/sources/{again[0]['id']}/words")
    assert w["sid"] == again[0]["id"] and w["words"][0]["text"] == "hola"
    # transcribe=0 (un medio propio del Studio): no entra al carril
    other = clip(tmp_path / "BROLL.mp4", dur=1.0, comment="b roll sin voz util")
    b = _upload(api, [other], transcribe="0")[0]
    time.sleep(0.8)
    assert len(_events(msrv, "transcribe")) == n0
    code, body, _ = api.get(f"/sources/{b['id']}/words", raw=True)       # pedirlo si lo encola
    assert code == 404 and json.loads(body)["words"] == "pending"
    _wait(lambda: api.get(f"/sources?ids={b['id']}")["sources"][0]["words"] == "ready", 30)


def test_montage_de_punta_a_punta_y_compose_con_el_mismo_id(msrv, session):
    api, sid = msrv["api"], session["sid"]
    order = [sid[k] for k in ("E", "D", "A", "F", "C", "B")]
    # rechazos
    assert api.post("/montage", {"sids": []}, raw=True)[0] == 400
    assert api.post("/montage", {"sids": ["ffffffffffff"]}, raw=True)[0] == 400
    code, body, _ = api.post("/montage", {"sids": order + ["noexiste"]}, raw=True)
    assert code == 400 and "noexiste" in body.decode()
    # el montaje
    job = api.post("/montage", {"sids": order + [order[0]], "script": SCRIPT, "glossary": "Rueda Libre",
                                "intercut": False, "director": {"enabled": False}})["job_id"]
    st = api.wait(job, lambda s: s["state"] in ("review", "error", "canceled"), 90)
    assert st["state"] == "review" and st["phase"] == "assembly" and st["kind"] == "montage", st
    assert st["stage"] == "assembly" and st["pct"] == 100 and st["has_plan"]
    assert st["words_done"] == st["words_total"] == 6 and st["words_state"] == "done"
    b = _events(msrv, "build")[-1]
    # el motor recibe la sesion EN ORDEN DE GRABACION, con palabras, audio 16k y metadatos
    assert [c["sid"] for c in b["clips"]] == [sid[k] for k in ("A", "B", "C", "D", "F", "E")]
    assert [c["rec"] for c in b["clips"]] == list(range(6))
    assert all(c["path"] and c["thumb"] for c in b["clips"]) and all(c["setup"] is None for c in b["clips"])
    assert [c["wav"] for c in b["clips"]] == [c["has_audio"] for c in b["clips"]]
    assert [c["words"] for c in b["clips"]] == [6, 8, 0, 7, 3, 5]
    assert b["clips"][0]["created"] == "2026-03-14T10:00:05-06:00" and b["clips"][2]["has_audio"] is False
    # la fecha del archivo (la de la copia) no es de grabacion: el motor ordena esos por nombre
    assert [c["created"] for c in b["clips"]][-2:] == [None, None]
    assert [c["created_src"] for c in b["clips"]][-2:] == ["mtime", "mtime"]
    assert b["script"] == SCRIPT and b["glossary"] == "Rueda Libre" and b["intercut"] is False
    assert b["llm"] is True                               # juez LLM pedido (el motor lo resuelve)
    got = api.get(f"/plan/{job}")
    m = got["plan"]["montage"]
    assert set(got["plan"]) == {"montage"}
    assert m["version"] == 1 and m["intercut"] is False and m["ms"] == 4
    assert [o["sid"] for o in m["order"]] == [sid[k] for k in ("A", "B", "C", "D", "F", "E")]
    assert all(t["sid"] != "ffffffffffff" for t in m["timeline"])          # la ajena se quita
    assert len(m["timeline"]) == 5 and m["discarded"][0]["why"] == "sin_audio"
    assert m["script"]["source"] == "user" and m["script"]["text"] == SCRIPT
    assert [ln["id"] for ln in m["script"]["lines"]] == ["L01", "L02", "L03", "L04"]
    assert m["script"]["sections"] == [{"id": 1, "lines": ["L01", "L02"]}, {"id": 2, "lines": ["L03", "L04"]}]
    assert m["stats"]["clips"] == 6 and "words_wait_s" in m["stats"]
    # parametros: los sids (la limpieza los conserva) y el guion
    cx = sqlite3.connect(msrv["data"] / "jobs.db")
    params = json.loads(cx.execute("SELECT params FROM jobs WHERE id=?", (job,)).fetchone()[0])
    assert params["sids"] == order and params["script"] == {"text": SCRIPT, "source": "user"}
    assert params["director"]["enabled"] is False
    # "Proyectos recientes": el montaje aparece (fase assembly) con miniatura de su primer clip
    row = next(j for j in api.get("/jobs?limit=50") if j["id"] == job)
    assert row["phase"] == "assembly" and row["kind"] == "montage" and row["has_plan"]
    assert row["thumb"] == f"/tlsource/{sid['A']}/thumb"
    assert row["topic"] == "Hola, soy Marta y reparo bicicletas en Querétaro." and row["shots"] == 5
    assert api.get(row["thumb"], raw=True)[0] == 200
    # un montaje sin componer no se previsualiza, dirige ni exporta
    for path, body in ((f"/render/{job}", {}), (f"/preview/{job}", {"pro": True}),
                       (f"/takes/{job}", {}), (f"/direct/{job}", {}), (f"/frame/{job}", {"t": 0})):
        code, raw_, _ = api.post(path, body, raw=True)
        assert code == 409 and "montaje" in raw_.decode(), (path, code, raw_[:200])
    assert api.req("PUT", f"/plan/{job}", {"plan": {"montage": m}}, raw=True)[0] == 400
    # ---- "Aplicar": el MISMO id pasa a compose (con "Usar esta toma" ya aplicado por la UI)
    assert api.post("/compose", {"montage_job": "ffffffffffff", "main": []}, raw=True)[0] == 404
    main = []
    for t in m["timeline"]:
        main.append({"sid": t["sid"], "type": "video", "in": t["in"], "out": t["out"],
                     "start": t["start"], "vol": "auto",
                     "montage": {"slot": t["slot"], "cand": t["cand"], "join": t["join"], "conf": 0.9}})
    edited = SCRIPT.replace("Te espero en el taller.", "Te espero en el taller de Rueda Libre.")
    r = api.post("/compose", {"montage_job": job, "main": main, "director": {"enabled": False},
                              "script": edited})
    assert r["job_id"] == job
    st = api.wait(job, lambda s: s["state"] in ("review", "error", "canceled") and s["kind"] == "compose", 180)
    assert st["state"] == "review" and st["phase"] == "review", st
    plan = api.get(f"/plan/{job}")["plan"]
    assert Path(plan["video"]).name == f"{job}.mp4" and (msrv["data"] / "input" / f"{job}.mp4").exists()
    # el guion y sus nombres propios/cifras van al glosario de whisper (nunca el guion entero)
    an = _events(msrv, "analyze")[-1]
    for t in ("Rueda Libre", "Marta", "Querétaro", "200"):
        assert t in an["glossary"], an["glossary"]
    assert "reparo bicicletas" not in an["glossary"] and len(an["glossary"]) <= 240
    # plan.shots con la union de cada toma; plan.montage mapeado a las tomas
    shots = plan["shots"]
    assert len(shots) == 5 and shots[0].get("join") is None
    assert [s.get("join") for s in shots[1:]] == [t["join"] for t in m["timeline"][1:]]
    assert all(s.get("sid") for s in shots)
    pm = plan["montage"]
    assert pm["script"]["text"] == SCRIPT and pm.get("composed") is True
    assert [t.get("shot") for t in pm["timeline"]] == [0, 1, 2, 3, 4]
    # las uniones llegaron al analisis (la limpieza de tomas no las cruza)
    assert an["joins"] and len(an["joins"]) == 4
    assert an["joins"] == pytest.approx([s["start"] for s in shots[1:]], abs=0.06)
    # secciones sembradas desde el guion (no las del LLM)
    secs = plan["takes"]["sections"]
    assert secs and all(s.get("title") != "Tema del LLM" for s in secs)
    params = json.loads(cx.execute("SELECT params, kind FROM jobs WHERE id=?", (job,)).fetchone()[0])
    assert params["script"] == {"text": edited, "source": "user"} and params["montage_job"] is True
    assert set(params["sids"]) == set(order) and params["glossary"] == "Rueda Libre"
    spec_main = params["spec"]["main"]
    assert all(it["montage"]["slot"] for it in spec_main) and all(it["sid"] for it in spec_main)
    assert all(it["words"] for it in spec_main)             # la voz del tramo para medir su nivel
    row = next(j for j in api.get("/jobs?limit=50") if j["id"] == job)
    assert row["kind"] == "compose" and row["thumb"] == f"/thumb/{job}"
    # ya compuesto: /compose con montage_job vuelve a componer el MISMO id (desde el montaje)
    code, body, _ = api.post("/compose", {"montage_job": job, "main": main[:2],
                                          "director": {"enabled": False}}, raw=True)
    assert code == 200 and json.loads(body)["job_id"] == job
    st = api.wait(job, lambda s: s["state"] in ("review", "error") and s["kind"] == "compose", 180)
    assert st["state"] == "review" and len(api.get(f"/plan/{job}")["plan"]["shots"]) == 2
    cx.close()


def test_montage_cancelar_rearmar_y_volver_al_ensamble(msrv, tmp_path):
    api, gate = msrv["api"], msrv["gate"]
    g = clip(tmp_path / "RUEDA_0001.MOV", dur=2.0, comment="la cadena se limpia cada mes",
             apple="2026-03-15T09:00:00-0600")
    h = clip(tmp_path / "RUEDA_0002.MOV", dur=2.0, comment="y se engrasa con cuidado",
             apple="2026-03-15T09:02:00-0600")
    gate.write_text("1")
    try:
        sg = _upload(api, [g])[0]["id"]
        j1 = api.post("/montage", {"sids": [sg]})["job_id"]
        st = api.wait(j1, lambda s: s.get("stage") == "words" and s["state"] == "running", 30)
        assert st["phase"] == "assembly" and "Transcribiendo" in st["message"]
        # mientras se arma no se puede componer
        assert api.post("/compose", {"montage_job": j1, "main": [{"sid": sg}]}, raw=True)[0] == 409
        assert api.post(f"/cancel/{j1}")["ok"]
        st = api.wait(j1, lambda s: s["state"] in ("canceled", "error", "review"), 30)
        assert (st["state"], st["error"]) == ("canceled", "Cancelado.")
    finally:
        gate.unlink(missing_ok=True)
    j2 = api.post("/montage", {"sids": [sg], "script": "La cadena se limpia cada mes."})["job_id"]
    st = api.wait(j2, lambda s: s["state"] in ("review", "error", "canceled"), 60)
    assert (st["state"], st["phase"]) == ("review", "assembly")
    first = api.get(f"/plan/{j2}")["plan"]
    # re-armar el MISMO proyecto con otro clip y otro guion; cancelado a medias vuelve al ensamble
    # con el montaje anterior intacto
    gate.write_text("1")
    try:
        sh = _upload(api, [h])[0]["id"]
        r = api.post("/montage", {"job_id": j2, "sids": [sg, sh], "script": "Otro guion."})
        assert r["job_id"] == j2
        api.wait(j2, lambda s: s["state"] == "running" and s.get("stage") == "words", 30)
        code, body, _ = api.post("/montage", {"job_id": j2, "sids": [sg]}, raw=True)
        assert code == 409
        api.post(f"/cancel/{j2}")
        st = api.wait(j2, lambda s: s["state"] != "running", 30)
        assert (st["state"], st["phase"]) == ("review", "assembly"), st
        assert st["message"] == jobstore.MONTAGE_CANCELED_MSG
        assert api.get(f"/plan/{j2}")["plan"] == first
    finally:
        gate.unlink(missing_ok=True)
    # sin cancelar: el re-armado reemplaza el montaje (mismo id)
    api.post("/montage", {"job_id": j2, "sids": [sg, sh], "script": "Otro guion."})
    st = api.wait(j2, lambda s: s["state"] in ("review", "error", "canceled") and s["kind"] == "montage"
                  and s.get("stage") == "assembly", 60)
    m = api.get(f"/plan/{j2}")["plan"]["montage"]
    assert [o["sid"] for o in m["order"]] == [sg, sh] and m["script"]["text"] == "Otro guion."
    assert api.post("/montage", {"job_id": "ffffffffffff", "sids": [sg]}, raw=True)[0] == 404


def test_compose_del_montaje_perfil_glosario_fuentes_nuevas_y_faltantes(msrv, session, tmp_path):
    """Revision: (1) "Sin perfil" en el ensamble (profile: null) y un glosario borrado ("") mandan; solo
    si el cuerpo NO los menciona se usan los del armado. (2) Las fuentes que el ensamble agrega
    ("Añadir") quedan en los sids del proyecto (la limpieza del disco no las borra). (3) Un clip de la
    linea de tiempo que ya no esta en el disco no se salta en silencio. (4) Con una toma "auto", los
    tramos del montaje ya nivelados llevan su voz para medirse igual. (5) plan.montage trae el glosario."""
    api, sid = msrv["api"], session["sid"]
    order = [sid[k] for k in ("A", "B", "D")]
    cx = sqlite3.connect(msrv["data"] / "jobs.db")
    params = lambda j: json.loads(cx.execute("SELECT params FROM jobs WHERE id=?", (j,)).fetchone()[0])
    job = api.post("/montage", {"sids": order, "script": SCRIPT, "glossary": "Rueda Libre", "intercut": False,
                                "profile": {"name": "Marta Ruiz", "business": "Rueda Libre"},
                                "director": {"enabled": False}})["job_id"]
    st = api.wait(job, lambda s: s["state"] in ("review", "error", "canceled"), 90)
    assert (st["state"], st["phase"]) == ("review", "assembly"), st
    m = api.get(f"/plan/{job}")["plan"]["montage"]
    assert m["glossary"] == "Rueda Libre"
    assert params(job)["profile"]["name"] == "Marta Ruiz"
    # (2) fuentes agregadas en el ensamble
    extra = _upload(api, [clip(tmp_path / "LOGO_0001.MOV", dur=1.0, comment="logo")], transcribe="0")[0]["id"]
    r = api.post(f"/montage/{job}/sources", {"sids": [extra, "ffffffffffff", extra]})
    assert r["sids"] == order + [extra]
    assert params(job)["sids"] == order + [extra] and extra in jobstore.params_sids(params(job))
    assert api.post("/montage/ffffffffffff/sources", {"sids": [extra]}, raw=True)[0] == 404
    assert api.post(f"/montage/{job}/sources", {"sids": "x"}, raw=True)[0] == 400
    main = [{"sid": t["sid"], "type": "video", "in": t["in"], "out": t["out"], "start": t["start"],
             "vol": t["vol"], "montage": {"slot": t["slot"], "cand": t["cand"], "join": t["join"]}}
            for t in m["timeline"]]
    # (3) un clip que ya no esta en el disco: 400 claro, nada se encola
    code, body, _ = api.post("/compose", {"montage_job": job, "main": main + [dict(main[0], sid="0123456789ab")]},
                             raw=True)
    assert code == 400 and "Faltan 1 clip" in body.decode(), body
    assert api.get(f"/status/{job}")["kind"] == "montage"
    # (1a) sin mencionarlos: los del armado
    r = api.post("/compose", {"montage_job": job, "main": main, "director": {"enabled": False}})
    p = params(job)
    assert p["profile"]["name"] == "Marta Ruiz" and p["glossary"] == "Rueda Libre"
    st = api.wait(job, lambda s: s["state"] in ("review", "error") and s["kind"] == "compose", 180)
    assert st["state"] == "review", st
    # (1b) "Sin perfil" y glosario borrado a proposito: mandan (antes se rellenaban con los del armado)
    main[1]["vol"] = "auto"                             # (4) una toma cambiada sin nivel medido
    r = api.post("/compose", {"montage_job": job, "main": main, "profile": None, "glossary": "",
                              "director": {"enabled": False}})
    assert r["job_id"] == job
    p = params(job)
    assert "profile" not in p, "«Sin perfil» no se rellena con el perfil del armado"
    assert p["glossary"] == ""
    # (4) la toma "auto" y los tramos numericos del montaje llevan su voz (palabras) para medirse igual
    sm = p["spec"]["main"]
    assert sm[1]["vol"] == "auto" and sm[1]["words"]
    assert all(it.get("words") for it in sm), [it.get("words") for it in sm]
    st = api.wait(job, lambda s: s["state"] in ("review", "error") and s["kind"] == "compose", 180)
    assert st["state"] == "review", st
    cx.close()


# ================================================================ de punta a punta con whisper y motor REALES
WHISPER_MODEL = next((c / MODEL_NAME for c in (
    Path.home() / "Library/Application Support/online.mixiuh.reelfy/models",
    Path.home() / "Library/Application Support/Reelfy/models")
    if (c / MODEL_NAME).exists()), None)
SAY = Path("/usr/bin/say")


def _say_clip(path, text, apple, tmp):
    aiff = tmp / f"{path.stem}.aiff"
    subprocess.run([str(SAY), "-v", "Paulina", "-r", "175", "-o", str(aiff), text], check=True)
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=360x640:r=30:d=30", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=0.6",
                    "-i", str(aiff), "-filter_complex",
                    "[1:a][2:a]concat=n=2:v=0:a=1,apad=pad_dur=0.8[a]", "-map", "0:v", "-map", "[a]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    "-movflags", "use_metadata_tags", "-metadata",
                    f"com.apple.quicktime.creationdate={apple}", str(path)], check=True)
    return path


@pytest.mark.skipif(WHISPER_MODEL is None or not SAY.exists() or not (SPIKE / "scripts" / "montage.py").exists(),
                    reason="sin el modelo de voz, sin la voz del sistema (say) o sin el motor de montaje")
def test_montage_real_voz_sintetica_de_punta_a_punta(tmp_path):
    """Servidor REAL: /sources -> transcripcion por clip con whisper -> POST /montage (montage.build)
    -> POST /compose con el MISMO id -> analisis del video combinado. Voz sintetica (say) con un
    guion inventado; dos tomas de la misma linea (gana la mas reciente)."""
    data = tmp_path / "data"
    (data / "models").mkdir(parents=True)
    (data / "models" / MODEL_NAME).symlink_to(WHISPER_MODEL)
    d = tmp_path / "clips"
    d.mkdir()
    lines = ["Hola, soy Marta y reparo bicicletas en el centro.",
             "Arreglamos más de doscientas bicis cada mes.",
             "Síguenos para ver cómo trabajamos."]
    files = [_say_clip(d / "TALLER_0001.MOV", lines[0], "2026-03-14T10:00:00-0600", tmp_path),
             _say_clip(d / "TALLER_0002.MOV", lines[0], "2026-03-14T10:02:00-0600", tmp_path),
             _say_clip(d / "TALLER_0003.MOV", lines[1], "2026-03-14T10:04:00-0600", tmp_path),
             _say_clip(d / "TALLER_0004.MOV", lines[2], "2026-03-14T10:06:00-0600", tmp_path)]
    port = _free_port()
    env = dict(os.environ, REELFY_PORT=str(port), REELFY_DATA=str(data), REELFY_GATED="0",
               REELFY_AUTO_DOWNLOAD="0", REELFY_OLLAMA_URL="http://127.0.0.1:9",
               REELFY_ACE_BIN=str(data / "sin_ace"), REELFY_MUSIC_MODELS=str(data / "sin_musica"),
               REELFY_WORKERS="1", REELFY_SRC_PROXY="0", REELFY_BG_ANALYSIS="0")
    env.pop("REELFY_LLM_MODEL", None)
    log = open(data / "server_stdout.log", "w")
    proc = subprocess.Popen([PY, "-u", str(SPIKE / "app" / "server.py")], cwd=str(SPIKE), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    api = Api(f"http://127.0.0.1:{port}")
    try:
        for _ in range(160):
            try:
                api.get("/tracks", timeout=2)
                break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError((data / "server_stdout.log").read_text()[-3000:])
                time.sleep(0.25)
        up = [files[k] for k in (3, 1, 0, 2)]                   # subida en desorden
        src = _upload(api, up)
        sid = {f.name: it["id"] for f, it in zip(up, src)}
        assert [it["rec"] for it in src] == [3, 1, 0, 2]
        ids = ",".join(it["id"] for it in src)
        got = _wait(lambda: (lambda r: r if all(x["words"] in ("ready", "error") for x in r) else None)(
            api.get(f"/sources?ids={ids}")["sources"]), 240, 0.5)
        assert all(x["words"] == "ready" for x in got), got
        w = api.get(f"/sources/{sid['TALLER_0003.MOV']}/words")
        assert "bicis" in " ".join(x["text"].lower() for x in w["words"]), w["words"]
        assert 0.4 < w["words"][0]["start"] < 1.5                # tiempos DEL CLIP (0.6 s de silencio)
        job = api.post("/montage", {"sids": [it["id"] for it in src], "script": "\n".join(lines),
                                    "llm": False})["job_id"]
        st = api.wait(job, lambda s: s["state"] in ("review", "error", "canceled"), 240)
        assert (st["state"], st["phase"]) == ("review", "assembly"), st
        m = api.get(f"/plan/{job}")["plan"]["montage"]
        assert [o["name"] for o in m["order"]] == [f.name for f in files]
        assert [ln["text"] for ln in m["script"]["lines"]] == lines and m["uncovered"] == []
        order_sids = [t["sid"] for t in m["timeline"]]
        # la linea 1 con la toma MAS RECIENTE; las otras en orden del guion
        assert order_sids[0] == sid["TALLER_0002.MOV"], m["timeline"]
        assert order_sids[-2:] == [sid["TALLER_0003.MOV"], sid["TALLER_0004.MOV"]], m["timeline"]
        s1 = next(sl for sl in m["slots"] if "L01" in sl["lines"])
        assert {c["sid"] for c in s1["cands"]} >= {sid["TALLER_0001.MOV"], sid["TALLER_0002.MOV"]}
        assert all(c["reason"] for sl in m["slots"] for c in sl["cands"])
        # aplicar: compose + analisis REAL del video combinado (sin director: mas rapido)
        main = [{"sid": t["sid"], "type": "video", "in": t["in"], "out": t["out"], "start": t["start"],
                 "vol": t.get("vol", 1.0), "montage": {"slot": t["slot"], "cand": t["cand"],
                                                       "join": t.get("join")}} for t in m["timeline"]]
        assert api.post("/compose", {"montage_job": job, "main": main,
                                     "director": {"enabled": False}})["job_id"] == job
        st = api.wait(job, lambda s: s["state"] in ("review", "error", "canceled") and s["kind"] == "compose",
                      400)
        assert st["state"] == "review" and st["phase"] == "review", st
        plan = api.get(f"/plan/{job}")["plan"]
        said = " ".join(w["text"] for ph in plan["phrases"] for w in ph).lower()
        assert "marta" in said and "síguenos" in said.replace("siguenos", "síguenos"), said
        assert len(plan["shots"]) == len(main) and plan["shots"][0].get("join") is None
        assert all(s.get("join") in ("section", "line", "splice") for s in plan["shots"][1:])
        assert plan["montage"]["composed"] and [t["shot"] for t in plan["montage"]["timeline"]] == \
            list(range(len(main)))
        joins = [s["start"] for s in plan["shots"][1:]]
        for c in plan["cuts"]:                                   # ningun corte de tomas cruza una union
            if c.get("src") == "takes":
                assert not any(c["start"] + 0.02 < j < c["end"] - 0.02 for j in joins), c
        log_txt = (data / "server_stdout.log").read_text()
        assert "palabras " in log_txt and "montaje " in log_txt
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


SHUTDOWN_LAUNCH = '''
import os, sys, subprocess, threading
sys.path.insert(0, "app")
import server as S
import uvicorn
S._SERVING = True                     # como el servidor real: al apagarse barre sus procesos
MARK = os.environ["FAKE_MARK"]

def fake_transcribe(src, wav):
    """whisper FALSO que tarda: un proceso hijo; si lo matan se reintenta UNA vez (como el
    reintento en CPU de pipeline.transcribe)."""
    r = subprocess.run(["sleep", MARK])
    if r.returncode != 0:
        subprocess.run(["sleep", MARK + "1"])
    return {"audio": False, "words": [], "takes": [], "dur": 1.0, "dtw": False, "align": None, "ms": 1}
S._transcribe_clip = fake_transcribe
uvicorn.run(S.app, host="127.0.0.1", port=int(os.environ["REELFY_PORT"]), log_level="warning")
'''


@pytest.mark.skipif(os.name == "nt", reason="pgrep/procesos POSIX")
def test_al_cerrar_la_app_el_carril_no_deja_procesos_huerfanos(tmp_path):
    """Cerrar la app a media transcripcion: el proceso en curso, su reintento y lo que estaba en
    cola no quedan corriendo (antes un proxy en cola lanzaba su ffmpeg despues del barrido)."""
    data = tmp_path / "data"
    data.mkdir()
    _fake_model(data)
    mark = f"{29 + (os.getpid() % 50) / 100:.2f}"            # "sleep 29.xx": unico de esta prueba
    port = _free_port()
    env = dict(os.environ, REELFY_PORT=str(port), REELFY_DATA=str(data), REELFY_GATED="0",
               REELFY_AUTO_DOWNLOAD="0", REELFY_OLLAMA_URL="http://127.0.0.1:9", REELFY_WORKERS="1",
               REELFY_SRC_PROXY="0", FAKE_MARK=mark)
    log = open(data / "server_stdout.log", "w")
    proc = subprocess.Popen([PY, "-u", "-c", SHUTDOWN_LAUNCH], cwd=str(SPIKE), env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    api = Api(f"http://127.0.0.1:{port}")

    def sleeping():
        r = subprocess.run(["pgrep", "-f", f"^sleep {mark}"], capture_output=True, text=True)
        return [int(x) for x in r.stdout.split()]
    try:
        for _ in range(160):
            try:
                api.get("/tracks", timeout=2)
                break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError((data / "server_stdout.log").read_text()[-3000:])
                time.sleep(0.25)
        a = clip(tmp_path / "A.mp4", dur=1.0, comment="uno")
        b = clip(tmp_path / "B.mp4", dur=1.0, comment="dos", freq=500)
        _upload(api, [a, b])
        _wait(sleeping, 20, 0.1)                           # el primero "transcribe"; el otro espera
        proc.terminate()
        proc.wait(timeout=20)
        time.sleep(1.0)
        assert sleeping() == [], "quedo un proceso del carril corriendo"
    finally:
        if proc.poll() is None:
            proc.kill()
        for pid in sleeping():
            os.kill(pid, 9)
        log.close()
