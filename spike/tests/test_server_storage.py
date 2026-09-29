"""app/server.py: almacenamiento de proyectos. DELETE /jobs/{id} (todo lo que es SOLO del proyecto
a la Papelera, nada fuera de DATA), POST /jobs/{id}/free-space (solo lo regenerable), GET /storage
(por categoria y por proyecto, en cache), POST /storage/free-space, sin purga automatica (aviso de
poco espacio en /status) y deduplicacion de importaciones (enlaces duros).

Cada prueba importa el servidor en un proceso aparte con DATA temporal (como test_server_setup).
La Papelera es FALSA (REELFY_TRASH_BIN / REELFY_OSASCRIPT apuntan a scripts de la prueba): nada va
a la Papelera real. Archivos inventados; los videos son sinteticos (lavfi)."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import paths
from test_server_setup import _run

SPIKE = Path(__file__).resolve().parents[1]

FAKE_TRASH = '''#!/bin/bash
# Papelera falsa: registra cada argumento y lo mueve a $FAKE_TRASH_DIR
for f in "$@"; do echo "$f" >> "$FAKE_TRASH_LOG"; mv "$f" "$FAKE_TRASH_DIR/"; done
'''
FAKE_OSASCRIPT = '''#!/bin/bash
# osascript falso (Finder): el 3er argumento es la ruta (osascript -e <script> <ruta>)
echo "$3" >> "$FAKE_TRASH_LOG"; mv "$3" "$FAKE_TRASH_DIR/"
'''
FAILING = '#!/bin/bash\nexit 1\n'


def _env(tmp_path, trash=True, osa=False):
    """DATA en tmp_path/data, Papelera falsa en tmp_path/Papelera, registro en tmp_path/trash.log."""
    data, tdir, bindir = tmp_path / "data", tmp_path / "Papelera", tmp_path / "bin"
    for d in (data, tdir, bindir):
        d.mkdir(exist_ok=True)
    env = {"FAKE_TRASH_LOG": str(tmp_path / "trash.log"), "FAKE_TRASH_DIR": str(tdir)}
    for name, body in (("trash", FAKE_TRASH if trash else FAILING),
                       ("osascript", FAKE_OSASCRIPT if osa else FAILING)):
        f = bindir / name
        f.write_text(body)
        f.chmod(0o755)
    env["REELFY_TRASH_BIN"] = str(bindir / "trash")
    env["REELFY_OSASCRIPT"] = str(bindir / "osascript")
    env["REELFY_WORKERS"] = "0"
    return data, tdir, env


PRELUDE = '''
import server as S, jobstore
from fastapi import HTTPException
from pathlib import Path
S._kick_src_proxy = lambda *a, **k: None
S._kick_analysis = lambda *a, **k: None
OUT = Path(os.environ["OUTSIDE"])

def put(p, size=1000, t=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(os.urandom(size))
    if t:
        os.utime(p, (t, t))
    return p

def job(jid, main=(), overlays=(), plan=None, state="review", params=None):
    spec = {"main": [{"path": str(x), "type": "video"} for x in main],
            "overlays": [{"path": str(x), "type": "image"} for x in overlays]}
    jobstore.create(jid, kind="compose", ext=".mp4", params=dict(params or {}, spec=spec), plan=plan)
    jobstore.update(jid, state=state, phase="review" if state == "review" else "analyzing")
    return jid

def gc(reason):
    """disk_gc sin chocar con la limpieza del arranque (corre sola a los 5 s)."""
    for _ in range(100):
        res = S.disk_gc(reason)
        if "skipped" not in res:
            return res
        time.sleep(0.1)
    raise SystemExit("disk_gc siempre ocupado")

def status_code(fn, *a):
    try:
        fn(*a)
    except HTTPException as e:
        return e.status_code
    return 200

def snapshot_outside():
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes()[:64])
            for p in OUT.rglob("*") if p.is_file()}
'''


def _outside(tmp_path):
    """Los videos ORIGINALES del usuario: viven fuera de DATA y no se tocan jamas."""
    out = tmp_path / "fuera"
    (out / "sub").mkdir(parents=True, exist_ok=True)
    (out / "original.mov").write_bytes(os.urandom(4000))
    (out / "logo.png").write_bytes(os.urandom(300))
    (out / "sub" / "otro.mov").write_bytes(os.urandom(500))
    return out


def test_eliminar_mueve_a_la_papelera_solo_lo_del_proyecto(tmp_path):
    data, tdir, env = _env(tmp_path)
    env["OUTSIDE"] = str(_outside(tmp_path))
    code = PRELUDE + '''
A, B = "aaaaaaaaaaa1", "bbbbbbbbbbb2"
assert any(getattr(r, "path", "") == "/jobs/{job_id}" and "DELETE" in r.methods for r in S.app.routes)
assert any(getattr(r, "path", "") == "/jobs/{job_id}/free-space" for r in S.app.routes)
S.SOURCES.mkdir(parents=True, exist_ok=True)
# fuentes: una solo de A (con proxy, miniatura y meta), una compartida con B, una fuera de DATA
sa = put(S.SOURCES / "5a0000000001.mov", 3000)
for suf, n in (("_proxy.mp4", 700), ("_t.jpg", 50), ("_meta.json", 20)):
    put(S.SOURCES / f"5a0000000001{suf}", n)
ss = put(S.SOURCES / "5b0000000002.mov", 2500); put(S.SOURCES / "5b0000000002_proxy.mp4", 600)
job(A, main=[sa, ss, OUT / "original.mov"], overlays=[OUT / "logo.png"],
    plan={"version": 2, "duration": 10, "director": {"script": {"topic": "Pan: de caja/casero"}}})
job(B, main=[ss])
before = snapshot_outside()
# archivos de A (y un enlace simbolico con su nombre que apunta FUERA de DATA)
files_a = [put(S.INPUT / f"{A}.mp4", 5000), put(S.OUTPUT / f"{A}_a1b2c3.mp4", 900),
           put(S.OUTPUT / f"{A}_a1b2c3_short1.mp4", 300), put(S.OUTPUT / f"{A}_draft_d4e5f6.mp4", 200),
           put(S.paths.WORK / f"{A}.wav", 400), put(S.paths.WORK / f"{A}.json", 80),
           put(S.paths.WORK / f"{A}_src720.mp4", 350), put(S.paths.WORK / f"{A}_thumb.jpg", 30),
           put(S.paths.WORK / f"compose_{A}" / "seg_000.mp4", 120),
           put(S.MUSIC_OUT / f"music_{A}_0f0f0f.wav", 250)]
link = S.INPUT / f"{A}.mov"
link.symlink_to(OUT / "original.mov")
mc = S.paths.WORK / "motion_cache"
base_a = put(mc / "base_00000000000000a1.mp4", 1500)
(mc / "base_00000000000000a1.mp4.json").write_text(json.dumps({"src": str(S.INPUT / f"{A}.mp4")}))
put(mc / "base_00000000000000a1.mp4.lock", 0); put(mc / "face_00000000000000a1_3f3766ff.npy", 90)
put(mc / "fgbase_00000000000000a1.mp4", 110)
voice_a = put(mc / "voice_00000000000000a9.wav", 600); put(mc / "voice_00000000000000a9.wav.json", 10)
jobstore.claim_cache(A, [voice_a.name])
# de B y sin dueno: se quedan
files_b = [put(S.INPUT / f"{B}.mp4", 4000), put(S.OUTPUT / f"{B}_c0ffee.mp4", 800)]
base_b = put(mc / "base_00000000000000b1.mp4", 1200)
(mc / "base_00000000000000b1.mp4.json").write_text(json.dumps({"src": str(S.INPUT / f"{B}.mp4")}))
loose = put(mc / "voice_00000000000000ff.wav", 500)

snap = S.storage_snapshot(force=True)
za = snap["jobs"][A]
assert za["shared_bytes"] == 2500 + 600 and za["exports_bytes"] == 900 + 300
assert za["bytes"] > za["frees_bytes"] > 0 and za["cache_frees_bytes"] > 0
listed = next(j for j in S.jobs_recent() if j["id"] == A)
assert listed["size_bytes"] == za["bytes"] and listed["frees_bytes"] == za["frees_bytes"]

r = S.delete_job(A)
assert r["ok"] and r["trash"] == "trash", r
assert r["freed_bytes"] == za["frees_bytes"], (r, za)
assert "5b0000000002.mov" in r["shared"]
# nada de A queda en DATA; lo de B, lo compartido y lo sin dueno si
for p in files_a + [sa, base_a, voice_a, link, mc / "face_00000000000000a1_3f3766ff.npy",
                    mc / "fgbase_00000000000000a1.mp4", S.SOURCES / "5a0000000001_proxy.mp4"]:
    assert not os.path.lexists(p), p
for p in files_b + [base_b, loose, ss, S.SOURCES / "5b0000000002_proxy.mp4",
                    mc / "base_00000000000000b1.mp4.json"]:
    assert p.exists(), p
assert jobstore.get(A) is None and jobstore.get(B) is not None
assert A not in [j["id"] for j in S.jobs_recent()]
assert A not in S.storage_snapshot()["jobs"]
assert status_code(S.delete_job, A) == 404 and status_code(S.free_job_space, A) == 404
# fuera de DATA: intacto (ni el destino del enlace simbolico ni lo que el montaje referencia)
assert snapshot_outside() == before
# la Papelera recibio UNA carpeta (desde dentro de DATA) con el proyecto y su manifiesto
log = Path(os.environ["FAKE_TRASH_LOG"]).read_text().split("\\n")
log = [x for x in log if x]
assert len(log) == 1 and Path(log[0]).resolve().parent == S._STAGE.resolve(), log
folder = Path(os.environ["FAKE_TRASH_DIR"]) / Path(log[0]).name
assert folder.name == f"Reelfy - Pan de caja casero ({A})", folder.name
man = json.loads((folder / "proyecto.json").read_text())
assert man["id"] == A and man["job"]["plan"]["duration"] == 10
assert (folder / "input" / f"{A}.mp4").stat().st_size == 5000
assert (folder / "input" / "sources" / "5a0000000001.mov").exists()
assert (folder / "work" / "motion_cache" / "base_00000000000000a1.mp4").exists()
moved_link = folder / "input" / f"{A}.mov"
assert moved_link.is_symlink() and moved_link.resolve() == (OUT / "original.mov").resolve()
print("OK")
'''
    _run(code, data, env)


def test_eliminar_y_liberar_rechazan_mientras_se_procesa(tmp_path):
    data, tdir, env = _env(tmp_path)
    env["OUTSIDE"] = str(_outside(tmp_path))
    code = PRELUDE + '''
A = "aaaaaaaaaaa3"
job(A, state="running")
f = put(S.INPUT / f"{A}.mp4", 1000)
d = put(S.OUTPUT / f"{A}_draft_abcdef.mp4", 100)
assert status_code(S.delete_job, A) == 409 and status_code(S.free_job_space, A) == 409
jobstore.update(A, state="queued")
assert status_code(S.delete_job, A) == 409
jobstore.update(A, state="review", phase="review")
S._EXPORTING.add(A)                     # exportando (el render marca el trabajo)
assert status_code(S.delete_job, A) == 409 and status_code(S.free_job_space, A) == 409
S._EXPORTING.discard(A)
assert f.exists() and d.exists() and jobstore.get(A) is not None
assert not Path(os.environ["FAKE_TRASH_LOG"]).exists()
# una tarea lateral que no se deja cancelar (vista previa colgada): tampoco se borra
import threading
stop = threading.Event()
th = threading.Thread(target=stop.wait, daemon=True); th.start()
S._SIDE[(A, "preview")] = {"thread": th, "cancel": threading.Event(), "key": None}
orig = S._quiet_job
S._quiet_job = lambda jid, names=None, wait=10.0: orig(jid, names, wait=0.2)
assert status_code(S.delete_job, A) == 409 and jobstore.get(A) is not None and f.exists()
stop.set(); th.join()
# libre: ahora si
S._quiet_job = orig
assert S.delete_job(A)["ok"] and not f.exists()
print("OK")
'''
    _run(code, data, env)


def test_papelera_de_respaldo_finder_y_carpeta_local(tmp_path):
    """Sin /usr/bin/trash: el Finder (osascript); si tambien falla (o fuera de macOS): DATA/.trash."""
    data, tdir, env = _env(tmp_path, trash=False, osa=True)
    env["OUTSIDE"] = str(_outside(tmp_path))
    code = PRELUDE + '''
A, B = "aaaaaaaaaaa4", "aaaaaaaaaaa5"
for j in (A, B):
    job(j); put(S.INPUT / f"{j}.mp4", 700)
r = S.delete_job(A)
assert r["trash"] == "finder", r
assert (Path(os.environ["FAKE_TRASH_DIR"]) / f"Reelfy - Proyecto aaaaaa ({A})" / "input" / f"{A}.mp4").exists()
os.environ["REELFY_OSASCRIPT"] = ""                 # sin Finder: carpeta local dentro de DATA
r = S.delete_job(B)
assert r["trash"] == "local", r
assert (S.TRASH_DIR / f"Reelfy - Proyecto aaaaaa ({B})" / "input" / f"{B}.mp4").exists()
snap = S.storage_snapshot(force=True)
assert snap["trash_bytes"] >= 700 and snap["local_trash_bytes"] >= 700, snap
# la papelera de respaldo nadie la vaciaba: "Vaciar papelera de Reelfy" la borra (y solo a ella)
assert any(getattr(r, "path", "") == "/storage/empty-trash" and "POST" in r.methods for r in S.app.routes)
put(S.INPUT / "aaaaaaaaaaa6.mp4", 300)
e = S.empty_local_trash()
assert e["ok"] and e["freed_bytes"] >= 700 and e["files"] == 1, e
assert not any(S.TRASH_DIR.iterdir()) and (S.INPUT / "aaaaaaaaaaa6.mp4").exists()
snap = S.storage_snapshot()
assert snap["local_trash_bytes"] == 0 and snap["trash_bytes"] == 0, snap
assert S.empty_local_trash()["freed_bytes"] == 0
# nunca se manda a la Papelera algo fuera de DATA
try:
    S._to_trash(OUT / "original.mov")
    raise SystemExit("movio algo fuera de DATA")
except RuntimeError:
    pass
assert (OUT / "original.mov").exists()
print("OK")
'''
    _run(code, data, env)


def test_liberar_espacio_conserva_el_proyecto_editable(tmp_path):
    data, tdir, env = _env(tmp_path)
    env["OUTSIDE"] = str(_outside(tmp_path))
    code = PRELUDE + '''
A, B = "aaaaaaaaaaa6", "bbbbbbbbbbb7"
S.SOURCES.mkdir(parents=True, exist_ok=True)
sa = put(S.SOURCES / "5c0000000001.mov", 3000); pa = put(S.SOURCES / "5c0000000001_proxy.mp4", 700)
ss = put(S.SOURCES / "5c0000000002.mov", 2000); ps = put(S.SOURCES / "5c0000000002_proxy.mp4", 500)
job(A, main=[sa, ss], plan={"version": 2, "duration": 5}); job(B, main=[ss])
keep = [put(S.INPUT / f"{A}.mp4", 5000), put(S.OUTPUT / f"{A}_a1b2c3.mp4", 900),
        put(S.paths.WORK / f"{A}.wav", 400), put(S.paths.WORK / f"{A}.json", 80),
        put(S.MUSIC_OUT / f"music_{A}_0f0f0f.wav", 250), sa, ss, ps]
cache = [put(S.OUTPUT / f"{A}_draft_d4e5f6.mp4", 200), put(S.paths.WORK / f"{A}_src720.mp4", 350),
         put(S.paths.WORK / f"{A}_src720.json", 20), pa]
mc = S.paths.WORK / "motion_cache"
b = put(mc / "base_00000000000000c1.mp4", 1500)
(mc / "base_00000000000000c1.mp4.json").write_text(json.dumps({"src": str(S.INPUT / f"{A}.mp4")}))
v = put(mc / "voice_00000000000000c9.wav", 600)
jobstore.claim_cache(A, [v.name, "face_no_existe.npy"])
cache += [b, v, mc / "base_00000000000000c1.mp4.json"]
jobstore.side_update(A, "proxy", state="done", file=f"{A}_src720.mp4")
jobstore.side_update(A, "preview", state="done", file=f"{A}_draft_d4e5f6.mp4")
want = sum(p.stat().st_size for p in cache)
snap = S.storage_snapshot(force=True)
assert snap["jobs"][A]["cache_frees_bytes"] == want, (snap["jobs"][A], want)
r = S.free_job_space(A)
assert r["ok"] and r["freed_bytes"] == want, (r, want)
assert all(not p.exists() for p in cache) and all(p.exists() for p in keep)
st = jobstore.status(A)
assert st["proxy_state"] == "freed" and st["preview_state"] == "freed" and not st.get("preview_file")
assert jobstore.get_plan(A)[0]["duration"] == 5                      # la edicion sigue
assert S.storage_snapshot()["jobs"][A]["cache_frees_bytes"] == 0     # la cache se invalido
# el proxy del editor de la fuente se regenera al pedirla (en segundo plano)
calls = []
S._make_proxy = lambda src, sid, info: calls.append(sid)
S.source_file("5c0000000001")
t0 = time.time()
while not calls and time.time() - t0 < 5:
    time.sleep(0.05)
assert calls == ["5c0000000001"], calls
assert S.free_job_space(A)["freed_bytes"] == 0                        # nada mas que liberar
print("OK")
'''
    _run(code, data, env)


def test_storage_por_categoria_en_cache_y_liberar_todo(tmp_path):
    data, tdir, env = _env(tmp_path)
    env["OUTSIDE"] = str(_outside(tmp_path))
    code = PRELUDE + '''
A, B = "aaaaaaaaaaa8", "bbbbbbbbbbb9"
job(A); job(B)
put(S.INPUT / f"{A}.mp4", 10_000); put(S.INPUT / f"{B}.mp4", 20_000)
put(S.OUTPUT / f"{A}_a1b2c3.mp4", 3000); put(S.OUTPUT / f"{A}_a1b2c3_short1.mp4", 1000)
put(S.OUTPUT / f"{B}_draft_0a0b0c.mp4", 700); put(S.paths.WORK / f"{B}_src720.mp4", 800)
mc = S.paths.WORK / "motion_cache"
old = time.time() - 3600
orphan = put(mc / "matte_00000000000000ee" / "000001.png", 900, t=old)
os.utime(orphan.parent, (old, old))
put(S.paths.WORK / "music" / "raw_1.wav", 400)
put(S.paths.DATA / "models" / "modelo.bin", 5000)
st = S.get_settings()
assert "storage" in st["features"]
S._GC_LOCK.acquire()               # la limpieza del arranque no invalida la cache a media prueba
s0 = S.storage(refresh=1)
# un enlace duro (importacion deduplicada) se cuenta UNA vez en el total
S.SOURCES.mkdir(parents=True, exist_ok=True)
os.link(S.INPUT / f"{B}.mp4", S.SOURCES / "5d0000000001.mov")
s1 = S.storage(refresh=1)
assert abs(s1["total_bytes"] - s0["total_bytes"]) < 10_000, (s0["total_bytes"], s1["total_bytes"])
assert s1["exports_bytes"] == 4000, s1
assert s1["cache_bytes"] == 700 + 800 + 900 + 400, s1
assert s1["models_bytes"] == 5000 and s1["projects_bytes"] >= 30_000 + 5000
assert s1["total_bytes"] == s1["exports_bytes"] + s1["cache_bytes"] + s1["projects_bytes"] + s1["trash_bytes"]
assert s1["jobs"][A]["exports_bytes"] == 4000 and s1["jobs"][B]["cache_bytes"] == 1500
# "Liberar espacio" solo borra la cache de los proyectos y los intermedios sin dueno: la musica
# en cache (400) cuenta en cache_bytes pero no en lo que se puede liberar
assert s1["freeable_bytes"] == 700 + 800 + 900, s1
assert s1["jobs"][B]["bytes"] == 20_000 + 1500
assert s1["low_space"] is False and s1["message"] is None
# en cache: otro pedido no recorre el disco (mismo `at`); invalidar -> se recalcula
put(S.OUTPUT / f"{A}_ffffff.mp4", 5000)
s2 = S.storage()
assert s2["at"] == s1["at"] and s2["exports_bytes"] == 4000
S._storage_invalidate()
assert S.storage()["exports_bytes"] == 9000
S._GC_LOCK.release()
# liberar todo: cache de los proyectos + intermedios sin dueno; nada de los proyectos
r = S.free_all_space()
assert r["freed_bytes"] == 700 + 800 + 900, r
assert not orphan.exists() and (S.paths.WORK / "music" / "raw_1.wav").exists()
assert (S.INPUT / f"{A}.mp4").exists() and (S.OUTPUT / f"{A}_a1b2c3.mp4").exists()
s3 = S.storage()
assert s3["cache_bytes"] == 400 and s3["exports_bytes"] == 9000
assert s3["freeable_bytes"] == 0, s3                    # el boton queda apagado (nada que liberar)
print("OK")
'''
    _run(code, data, env)


def test_sin_purga_automatica_y_aviso_de_poco_espacio(tmp_path):
    """Antes: al arrancar se conservaban los 50 mas recientes y con < 5 GB libres solo 5, sin
    avisar. Ahora ningun proyecto se borra solo: /status y /storage avisan."""
    data, tdir, env = _env(tmp_path)
    env["OUTSIDE"] = str(_outside(tmp_path))
    code = PRELUDE + '''
ids = [f"c{i:011d}" for i in range(60)]
for jid in ids:
    job(jid); put(S.INPUT / f"{jid}.mp4", 100)
jobstore.KEEP_LAST = 2
assert jobstore.init() == []                          # arrancar no purga
res = gc("prueba")
assert "jobs_purged" not in res and res["low_space"] is False
S._disk_free_gb = lambda: 1.0                         # poco disco
S._DISK["t"] = 0
res = gc("poco disco")
assert res["low_space"] is True
assert sorted(jobstore.all_ids()) == sorted(ids)
assert all((S.INPUT / f"{j}.mp4").exists() for j in ids)
st = json.loads(S.status(ids[0]).body)
assert st["low_space"] is True and st["disk_free_gb"] == 1.0
assert st["low_space_message"] == "Poco espacio: libera espacio o elimina proyectos"
sg = S.storage()
assert sg["low_space"] is True and sg["message"] == st["low_space_message"]
S._disk_free_gb = lambda: 80.0
S._DISK["t"] = 0
st = json.loads(S.status(ids[0]).body)
assert st["low_space"] is False and "low_space_message" not in st
print("OK")
'''
    _run(code, data, env)


def _video(path, dur=1.0, tone=440):
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=320x240:r=30:d={dur}", "-f", "lavfi", "-i",
                    f"sine=frequency={tone}:sample_rate=48000:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True)
    return path


def test_importar_dos_veces_el_mismo_archivo_no_duplica(tmp_path):
    data, tdir, env = _env(tmp_path)
    env["OUTSIDE"] = str(_outside(tmp_path))
    vid = _video(tmp_path / "IMG_0001.MOV")
    other = tmp_path / "IMG_0002.MOV"
    raw = bytearray(vid.read_bytes())
    raw[len(raw) // 2] ^= 0xFF                         # mismo tamano, un byte distinto a la mitad
    other.write_bytes(bytes(raw))
    env["VID"], env["OTHER"] = str(vid), str(other)
    code = PRELUDE + '''
import asyncio, io
from starlette.datastructures import UploadFile
PROXIES = []
def fake_proxy(src, sid, info):
    PROXIES.append(sid)
    (S.SOURCES / f"{sid}_proxy.mp4").write_bytes(b"p" * 321)
S._make_proxy = fake_proxy

def upload(path, name):
    up = UploadFile(file=io.BytesIO(Path(path).read_bytes()), filename=name)
    return asyncio.run(S.add_sources([up]))["sources"][0]

a = upload(os.environ["VID"], "IMG_0001.MOV")
t0 = time.time()
while not PROXIES and time.time() - t0 < 10:
    time.sleep(0.05)
assert "dedup" not in a and PROXIES == [a["id"]]
b = upload(os.environ["VID"], "IMG_0001 copia.MOV")
assert b["dedup"] is True and b["id"] != a["id"] and b["dur"] == a["dur"] and b["thumb"]
fa, fb = S.SOURCES / f"{a['id']}.mov", S.SOURCES / f"{b['id']}.mov"
assert os.stat(fa).st_ino == os.stat(fb).st_ino and os.stat(fa).st_nlink == 2
assert fb.read_bytes() == Path(os.environ["VID"]).read_bytes()
pa, pb = S.SOURCES / f"{a['id']}_proxy.mp4", S.SOURCES / f"{b['id']}_proxy.mp4"
assert os.stat(pa).st_ino == os.stat(pb).st_ino           # el proxy del editor tambien se comparte
time.sleep(0.3)
assert PROXIES == [a["id"]]                                # sin volver a codificarlo
# mismo tamano pero otro contenido: se guarda aparte
c = upload(os.environ["OTHER"], "IMG_0002.MOV")
assert "dedup" not in c and os.stat(S.SOURCES / f"{c['id']}.mov").st_ino != os.stat(fa).st_ino
# /analyze: el video del trabajo identico a una fuente ya importada -> enlace
dst = S.INPUT / "dddddddddddd.mov"
dst.write_bytes(Path(os.environ["VID"]).read_bytes())
twin = S.dedupe_import(dst)
assert twin is not None and os.stat(dst).st_ino == os.stat(fa).st_ino
# el total cuenta los bytes una sola vez
size = fa.stat().st_size
job("aaaaaaaaaaaa", main=[fa]); job("bbbbbbbbbbbb", main=[fb])
snap = S.storage_snapshot(force=True)
assert snap["jobs"]["aaaaaaaaaaaa"]["bytes"] >= size and snap["jobs"]["aaaaaaaaaaaa"]["frees_bytes"] < size
# eliminar un proyecto no afecta al otro (el otro enlace sigue con los mismos bytes)
r = S.delete_job("aaaaaaaaaaaa")
assert not fa.exists() and fb.read_bytes() == Path(os.environ["VID"]).read_bytes()
assert r["freed_bytes"] < size                             # esos bytes siguen en uso
print("OK")
'''
    _run(code, data, env)


# ---------------------------------------------------------------- HTTP de punta a punta
def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_http_eliminar_liberar_y_storage(tmp_path):
    """Servidor real (uvicorn) con DATA temporal: GET /storage, GET /jobs con tamanos, POST
    /jobs/{id}/free-space, DELETE /jobs/{id} (404 despues, 409 mientras se procesa)."""
    from test_server_api import Api
    data, tdir, env_extra = _env(tmp_path)
    outside = _outside(tmp_path)
    vid = _video(tmp_path / "v.mp4", 1.0)
    port = _free_port()
    env = dict(os.environ, REELFY_PORT=str(port), REELFY_DATA=str(data), REELFY_GATED="0",
               REELFY_AUTO_DOWNLOAD="0", REELFY_OLLAMA_URL="http://127.0.0.1:9",
               REELFY_ACE_BIN=str(data / "sin_ace"), REELFY_MUSIC_MODELS=str(data / "sin_musica"),
               REELFY_SRC_PROXY="0", REELFY_BG_ANALYSIS="0", **env_extra)
    log = open(tmp_path / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-u", str(SPIKE / "app" / "server.py")], cwd=str(SPIKE),
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    api = Api(f"http://127.0.0.1:{port}")
    try:
        for _ in range(160):
            try:
                api.get("/tracks", timeout=2)
                break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError((tmp_path / "server.log").read_text()[-3000:])
                time.sleep(0.25)
        cx = sqlite3.connect(data / "jobs.db", timeout=30)
        now = time.time()
        for jid, state in (("e2e000000001", "review"), ("e2e000000002", "running")):
            cx.execute("INSERT INTO jobs (id, kind, state, phase, pct, message, ext, params, plan, "
                       "created, updated, plan_rev) VALUES (?, 'compose', ?, 'review', 100, '', '.mp4', "
                       "?, ?, ?, ?, 1)", (jid, state, json.dumps({"spec": {"main": [
                           {"path": str(outside / "original.mov")}]}}),
                           json.dumps({"version": 2, "duration": 1.0}), now, now))
            (data / "input" / f"{jid}.mp4").write_bytes(vid.read_bytes())
        cx.commit()
        cx.close()
        (data / "output" / "e2e000000001_a1b2c3.mp4").write_bytes(os.urandom(2000))
        (data / "output" / "e2e000000001_draft_abcdef.mp4").write_bytes(os.urandom(1500))
        st = api.get("/storage?refresh=1")
        assert st["exports_bytes"] == 2000 and st["cache_bytes"] >= 1500
        mine = next(j for j in api.get("/jobs") if j["id"] == "e2e000000001")
        assert mine["size_bytes"] == vid.stat().st_size + 3500 and mine["cache_frees_bytes"] == 1500
        r = api.post("/jobs/e2e000000001/free-space")
        assert r["freed_bytes"] == 1500 and not (data / "output" / "e2e000000001_draft_abcdef.mp4").exists()
        code, body, _ = api.req("DELETE", "/jobs/e2e000000002", raw=True)
        assert code == 409 and "procesando" in json.loads(body)["detail"]
        r = api.req("DELETE", "/jobs/e2e000000001")
        assert r["ok"] and r["trash"] == "trash" and r["freed_bytes"] == vid.stat().st_size + 2000
        assert api.req("DELETE", "/jobs/e2e000000001", raw=True)[0] == 404
        assert api.get("/status/e2e000000001", raw=True)[0] == 404
        assert "e2e000000001" not in [j["id"] for j in api.get("/jobs")]
        st2 = api.get("/storage")
        assert "e2e000000001" not in st2["jobs"] and st2["exports_bytes"] == 0
        assert (outside / "original.mov").stat().st_size == 4000
        s = api.get("/status/e2e000000002?plan=0")
        assert s["low_space"] in (True, False) and "disk_free_gb" in s
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def test_recorrido_del_disco_lista_cada_carpeta_una_vez(tmp_path):
    """GET /jobs y /storage recorren el disco: cada carpeta se lista UNA vez por recorrido (antes
    cada proyecto listaba y ordenaba INPUT, OUTPUT, WORK, musica y fuentes: con 400 proyectos
    tardaba 24 s con el candado tomado). Mismo resultado que por proyecto."""
    data, tdir, env = _env(tmp_path)
    env["OUTSIDE"] = str(_outside(tmp_path))
    code = PRELUDE + '''
S.SOURCES.mkdir(parents=True, exist_ok=True)
S._GC_LOCK.acquire()
def mk(a, b):
    ids = []
    for k in range(a, b):
        jid = f"{k:012x}"
        sa = put(S.SOURCES / f"5e{k:010x}.mov", 300); put(S.SOURCES / f"5e{k:010x}_proxy.mp4", 70)
        job(jid, main=[sa])
        put(S.INPUT / f"{jid}.mp4", 1000); put(S.OUTPUT / f"{jid}_a1b2c3.mp4", 400)
        put(S.OUTPUT / f"{jid}_draft_0a0b0c.mp4", 90); put(S.paths.WORK / f"{jid}.wav", 200)
        put(S.paths.WORK / f"{jid}_src720.mp4", 60); put(S.MUSIC_OUT / f"music_{jid}_0f0f0f.wav", 30)
        ids.append(jid)
    return ids
calls = []
real_ls = S._ls
S._ls = lambda d: (calls.append(str(d)), real_ls(d))[1]
counts = {}
for n in (5, 40):
    ids = mk(len(counts) and 5, n)
    calls.clear()
    snap = S._storage_scan()
    counts[n] = len(calls)
    for jid in ids:
        d = snap["jobs"][jid]
        assert d["project_bytes"] == 1000 + 200 + 30 + 300 and d["exports_bytes"] == 400, d
        assert d["cache_bytes"] == 90 + 60 + 70 and d["cache_frees_bytes"] == 220, d
assert counts[5] == counts[40], counts          # no crece con los proyectos
assert counts[40] <= 12, counts
# el mismo contexto sirve a todos los proyectos sin volver a listar
ctx = S._storage_ctx()
calls.clear()
for jid in ctx["jobs"]:
    S._job_units(jid, ctx)
assert len(calls) <= 6, calls
S._GC_LOCK.release()
print("OK")
'''
    _run(code, data, env)
