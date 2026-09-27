"""app/server.py: actualizacion de v0.4 (LLM viejo no bloquea), musica elegida ANTES de
sincronizar, clave del analisis de fondo, barra de progreso, reanudar la musica IA y
recoleccion de basura del disco.

Cada prueba importa el servidor en un proceso aparte con DATA temporal (importarlo arranca
workers, logs y la base de trabajos). Sin red, sin LLM real y sin datos de clientes: un ollama
FALSO (http.server) hace de ollama embebido.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import paths

SPIKE = Path(__file__).resolve().parents[1]
PY = sys.executable
WHISPER = next((c / "ggml-large-v3-turbo.bin" for c in (
    Path.home() / "Library/Application Support/online.mixiuh.reelfy/models",
    Path.home() / "Library/Application Support/Reelfy/models")
    if (c / "ggml-large-v3-turbo.bin").exists()), None)

PRELUDE = '''
import os, sys, json, time, threading
sys.path.insert(0, "app")
'''


def _run(code, tmp_path, env_extra=None, timeout=240):
    env = dict(os.environ, REELFY_DATA=str(tmp_path), REELFY_GATED="0", REELFY_AUTO_DOWNLOAD="0",
               REELFY_OLLAMA_URL="http://127.0.0.1:9", REELFY_WORKERS="1",
               REELFY_ACE_BIN=str(tmp_path / "sin_ace"), REELFY_MUSIC_MODELS=str(tmp_path / "sin_musica"))
    env.pop("REELFY_LLM_MODEL", None)
    env.pop("REELFY_VLM_MODEL", None)
    env.update(env_extra or {})
    r = subprocess.run([PY, "-c", PRELUDE + textwrap.dedent(code)], cwd=str(SPIKE), env=env,
                       capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout


FAKE_OLLAMA = '''
import http.server, socket
MODELS = set(json.loads(os.environ["FAKE_MODELS"]))
DELETED = []

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._json({"models": [{"name": m} for m in sorted(MODELS)]})
        return self._json({"version": "0.30.11"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/pull":
            self.send_response(200); self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for c in (0, 50, 100):
                self.wfile.write((json.dumps({"status": "pulling", "total": 100, "completed": c}) + "\\n").encode())
            MODELS.add(body["model"])
            self.wfile.write(b'{"status": "success"}\\n')
            return
        return self._json({})

    def do_DELETE(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        MODELS.discard(body.get("model")); DELETED.append(body.get("model"))
        return self._json({})

srv = http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["REELFY_OLLAMA_PORT"])), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
'''


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _whisper_linked(tmp_path):
    (tmp_path / "models").mkdir(exist_ok=True)
    (tmp_path / "models" / WHISPER.name).symlink_to(WHISPER)


@pytest.mark.skipif(WHISPER is None, reason="sin el modelo de voz descargado en esta maquina")
def test_actualizacion_de_v04_no_bloquea_y_baja_el_llm_nuevo_en_segundo_plano(tmp_path):
    """App con qwen2.5:3b (v0.4) y sin qwen3.5:4b: NO muestra la pantalla bloqueante; usa el
    viejo (sin vision), baja el nuevo como opcional con progreso propio, lo activa y borra el
    viejo. Instalacion nueva (sin ningun LLM): el setup si bloquea."""
    _whisper_linked(tmp_path)
    fake_bin = tmp_path / "bin" / "ollama-runtime" / "ollama"      # marca de ollama embebido
    code = FAKE_OLLAMA + '''
import server as S
assert S.BUNDLED_OLLAMA and S.TARGET_LLM == "qwen3.5:4b"
assert not S._needs_setup(), "el LLM viejo no debe bloquear"
S._refresh_llm()
assert os.environ["REELFY_LLM_MODEL"] == "qwen2.5:3b"
assert os.environ["REELFY_VLM_MODEL"] == "qwen3.5:4b"      # vision: solo con el modelo nuevo
assert S.pipeline.hl.MODEL == "qwen2.5:3b"
S._refresh_optional()
assert S.OPT["llm"]["state"] == "missing" and S.OPT["llm"]["fallback"] == "qwen2.5:3b", S.OPT["llm"]
assert "llm" in S._optional_missing()
st = S.setup_status()
assert st["state"] == "ready" and st["llm_model"] == "qwen2.5:3b" and st["llm_target"] == "qwen3.5:4b"
assert S._required_missing_bytes() < S.paths.LLM_SIZE       # el LLM nuevo no cuenta como obligatorio
S._download_llm()
assert S.OPT["llm"]["state"] == "ready", S.OPT["llm"]
assert os.environ["REELFY_LLM_MODEL"] == "qwen3.5:4b" and "REELFY_VLM_MODEL" not in os.environ
assert S.pipeline.hl.MODEL == "qwen3.5:4b"
assert "qwen2.5:3b" in DELETED and "qwen2.5:3b" not in MODELS
assert "llm" not in S._optional_missing()
MODELS.clear()
assert S._needs_setup(), "instalacion nueva sin LLM: el setup obligatorio lo baja"
print("OK")
'''
    _run(code, tmp_path, {"REELFY_OLLAMA_BIN": str(fake_bin), "REELFY_OLLAMA_PORT": str(_free_port()),
                          "FAKE_MODELS": json.dumps(["qwen2.5:3b"])})


def test_musica_elegida_entra_antes_de_sincronizar(tmp_path):
    """Con musica distinta de la pista IA (procedural, ninguna, prompt/bpm/semilla propios) el
    director NO sincroniza a la rejilla de ACE (music_sync=False: ni golpes fuera de tiempo ni
    ACE en la ruta critica). Con la IA por defecto o solo claves de mezcla, sincroniza. Si el
    director acepta `music=`, recibe el override (se mezcla antes de la rejilla)."""
    code = '''
import server as S
from motion import director
calls = []

def fake(plan, style, intensity, seed, use_llm=True, on_step=None, trim=True, music_sync=None):
    calls.append({"music_sync": music_sync})
    return {"version": 2, "video": "v.mp4", "shots": [{"start": 0, "end": 1}],
            "fx": {"music": {"source": "ai", "enabled": True, "prompt": "pop"}},
            "director": {"llm": "qwen3.5:4b", "vlm": "heuristico"}}
director.direct = fake
cases = [("procedural", False), ("none", False), ("library", False), ("ai", None), (None, None),
         ({"prompt": "lofi"}, False), ({"bpm": 90}, False), ({"seed": 3}, False),
         ({"source": "ai", "gain_db": -3}, None), ({"ending": "fade"}, False)]
for m, want in cases:
    d = S._director_params({"music": m} if m is not None else {})
    out, warns = S._direct_plan({"video": "v.mp4"}, d, use_llm=True)
    assert calls[-1]["music_sync"] is want, (m, calls[-1])
    if m == "procedural":
        assert out["fx"]["music"]["source"] == "procedural"
    if m == "none":
        assert out["fx"]["music"]["enabled"] is False
assert any("visión" in w for w in warns), warns       # VLM cayo a heuristicas: se avisa
_, w2 = S._direct_plan({"video": "v.mp4", "director": {"vlm": "qwen3.5:4b"}},
                       S._director_params({}), use_llm=True)
assert not any("visión" in w for w in w2), w2          # re-dirigir con tomas ya descritas

def fake2(plan, style, intensity, seed, use_llm=True, on_step=None, trim=True, music_sync=None,
          music=None):
    calls.append({"music": music, "music_sync": music_sync})
    return fake(plan, style, intensity, seed)
director.direct = fake2
S._direct_plan({"video": "v.mp4"}, S._director_params({"music": "procedural"}), use_llm=False)
assert calls[-2]["music"] == {"source": "procedural"} and calls[-2]["music_sync"] is None, calls[-2:]
# re-dirigir conserva la musica que el usuario eligio en el plan
assert S._music_keep({"fx": {"music": {"source": "procedural", "enabled": True, "stops": [1]}}}) == \\
    {"source": "procedural", "enabled": True}
assert S._music_keep({"fx": {"music": {"source": "ai", "enabled": True}}}) is None
lk = S._music_keep({"fx": {"music": {"source": "ai", "prompt": "mio", "stops": [1]}},
                    "director": {"locked": ["music"]}})
assert lk == {"source": "ai", "prompt": "mio"} and not S._music_sync_ok(lk)
print("OK")
'''
    _run(code, tmp_path)


def test_barra_de_progreso_monotona_con_eta_y_avance_en_pasos_largos(tmp_path):
    code = '''
import server as S, jobstore
jid = "0123456789ab"
jobstore.create(jid, kind="compose", ext=".mp4")

class P:
    def __init__(self):
        self.calls = []
    def update(self, force=False, **kw):
        self.calls.append((force, kw)); jobstore.update(jid, **kw)
p = P()
bar = S._PhaseBar(jid, p, t0=time.time() - 30)
bar.set(40, "Transcribiendo", "analyze")
bar.set(30, "algo viejo", "analyze")                        # nunca retrocede
assert p.calls[-1][1]["pct"] == 40 and p.calls[-1][0] is True   # cambio de paso: force
assert p.calls[-1][1]["eta"] is not None and p.calls[-1][1]["eta"] > 0
lo, hi = S.DIRECTOR_STEPS["musica"]
assert S.DIRECTOR_STEPS["coreografia"][1] <= lo < hi <= 99 and S.DIRECTOR_LABELS["musica"]
bar.set(lo, "Dirigiendo (componiendo la música)", "director")
bar.creep(lo, hi, 1.0)
time.sleep(2.6)
bar.stop_creep()
pct = jobstore.status(jid)["pct"]
assert lo < pct < hi, pct
bar.set(S.DIRECTOR_STEPS["coreografia"][0], "x", "director")   # un paso anterior no la baja
assert p.calls[-1][1]["pct"] >= pct
assert S.PHASE_COMPOSE[1] < S.PHASE_ANALYZE_AFTER_COMPOSE[0] < S.PHASE_ANALYZE_AFTER_COMPOSE[1] <= S.DIRECTOR_STEPS["tomas"][0]
print("OK")
'''
    _run(code, tmp_path)


def test_reactivar_musica_ia_relanza_la_descarga(tmp_path):
    """Apagar y prender la musica IA enseguida: el hilo pausado termina y la descarga se
    relanza (antes quedaba 10 min sin reanudar por el respaldo entre intentos)."""
    code = '''
import server as S
runs = []
S._optional_missing = lambda: ["music"]
def fake_run():
    runs.append(time.time()); time.sleep(0.2)
S._run_optional = fake_run
old = threading.Thread(target=lambda: time.sleep(0.6)); old.start()
S._opt_thread = old
S._opt_last[0] = time.time()                  # un intento reciente (respaldo activo)
S.OPT["music"]["state"] = "disabled"
r = S.setup_music({"enabled": True})
assert r["music_ai"] is True and r["downloading"] is True, r
assert S._opt_thread is not old and runs, (S._opt_thread, runs)
r = S.setup_music({"enabled": False})
assert r["music_ai"] is False and S._music_stop.is_set()
print("OK")
'''
    _run(code, tmp_path)


def test_recoleccion_de_basura_del_disco(tmp_path):
    """Trabajos purgados -> se borran sus archivos; exports: solo los N mas recientes por
    trabajo; huerfanos y fuentes sin uso tras su antiguedad; caches con tope LRU."""
    code = '''
import server as S, jobstore
from pathlib import Path
old = time.time() - 3 * 86400
def touch(p, t=None, size=10):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x" * size)
    if t: os.utime(p, (t, t))
    return p
S.SOURCES.mkdir(parents=True, exist_ok=True)
ids = ["aaaaaaaaaaa1", "aaaaaaaaaaa2", "aaaaaaaaaaa3"]
srcs = ["5ce000000001", "5ce000000002", "5ce000000003"]
for jid, sid in zip(ids, srcs):
    for suf in (".mov", "_t.jpg", "_proxy.mp4", "_meta.json"):
        touch(S.SOURCES / f"{sid}{suf}", old)
    jobstore.create(jid, kind="compose", ext=".mp4",
                    params={"spec": {"main": [{"path": str(S.SOURCES / f"{sid}.mov")}]}})
    jobstore.update(jid, state="review", phase="review")
    time.sleep(0.02)
    touch(S.INPUT / f"{jid}.mp4"); touch(S.OUTPUT / f"{jid}_a1b2c3.mp4")
    touch(S.OUTPUT / f"{jid}_a1b2c3_short1.mp4"); touch(S.OUTPUT / f"{jid}_draft_d4e5f6.mp4")
    touch(S.paths.WORK / f"{jid}.wav"); touch(S.MUSIC_OUT / f"music_{jid}_0f0f0f.wav")
    (S.paths.WORK / f"compose_{jid}").mkdir(parents=True, exist_ok=True)
# fuentes sin trabajo: una vieja (se borra) y una recien subida (espera su /compose)
for suf in (".mov", "_t.jpg"):
    touch(S.SOURCES / f"5ce0000000aa{suf}", old); touch(S.SOURCES / f"5ce0000000bb{suf}")
# huerfanos (trabajos que ya no estan en la base): viejo se borra, reciente no
touch(S.INPUT / "dead00000000.mp4", old); touch(S.OUTPUT / "dead00000000_123456.mp4", old)
touch(S.INPUT / "dead00000001.mp4")
jobstore.KEEP_LAST = 2
res = S.disk_gc("prueba")
assert res["jobs_purged"] == 1, res
j1 = ids[0]
assert not list(S.INPUT.glob(f"{j1}*")) and not list(S.OUTPUT.glob(f"{j1}*"))
assert not list(S.paths.WORK.glob(f"{j1}*")) and not (S.paths.WORK / f"compose_{j1}").exists()
assert not list(S.MUSIC_OUT.glob(f"music_{j1}_*"))
for jid in ids[1:]:
    assert (S.INPUT / f"{jid}.mp4").exists() and (S.OUTPUT / f"{jid}_a1b2c3.mp4").exists()
assert not (S.SOURCES / f"{srcs[0]}.mov").exists()          # su trabajo se purgo
assert (S.SOURCES / f"{srcs[1]}.mov").exists() and (S.SOURCES / f"{srcs[2]}_proxy.mp4").exists()
assert not (S.SOURCES / "5ce0000000aa.mov").exists() and (S.SOURCES / "5ce0000000bb.mov").exists()
assert not (S.INPUT / "dead00000000.mp4").exists() and not (S.OUTPUT / "dead00000000_123456.mp4").exists()
assert (S.INPUT / "dead00000001.mp4").exists()
# exports: solo los 2 mas recientes del trabajo (con shorts, miniaturas y stats)
jid = ids[2]
for q in S.OUTPUT.glob(f"{jid}_a1b2c3*"):
    q.unlink()
for k, tok in enumerate(("0000a1", "0000a2", "0000a3", "0000a4")):
    t = time.time() - 100 + k
    for suf in (".mp4", "_short1.mp4", "_short1_thumb.jpg", "_stats.json"):
        touch(S.OUTPUT / f"{jid}_{tok}{suf}", t)
S._trim_exports(jid, keep=2, protect=f"{jid}_0000a4")
left = sorted(p.name for p in S.OUTPUT.glob(f"{jid}_0000a*"))
assert left == sorted(f"{jid}_{tok}{suf}" for tok in ("0000a3", "0000a4")
                      for suf in (".mp4", "_short1.mp4", "_short1_thumb.jpg", "_stats.json")), left
assert (S.OUTPUT / f"{jid}_draft_d4e5f6.mp4").exists()     # la vista previa no es un export
# caches: tope LRU (bases viejas se podan hasta caber)
mc = S.paths.WORK / "motion_cache"
for k in range(8):
    b = touch(mc / f"base_{k:016x}.mp4", old + k, size=200_000)
    touch(Path(str(b) + ".json"), old + k)
info = S._cap_caches(cap_gb=0.0008)                        # ~0.86 MB
assert len(list(mc.glob("base_*.mp4"))) < 8 and info["cache_gb"] <= 0.0008 + 1e-9, info
print("OK")
'''
    _run(code, tmp_path, {"REELFY_WORKERS": "0"})


def test_clave_del_analisis_ignora_textos(tmp_path):
    """Editar un texto no cambia la clave del analisis de fondo (base, rostro, matte, grade);
    otros cortes u otro formato si."""
    video = tmp_path / "sint.mp4"
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x640:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
                   check=True)
    code = f'''
import server as S, copy
from motion import director
words = [{{"text": w, "start": 0.2 + 0.4 * i, "end": 0.5 + 0.4 * i}} for i, w in enumerate(
    "hola te cuento algo nuevo hoy".split())]
v1 = {{"video": "{video}", "duration": 3.0, "phrases": [words[:3], words[3:]], "cuts": [],
      "highlights": []}}
plan = director.direct(v1, "dinamico", 0.7, 7, use_llm=False, music_sync=False)
b1, k1 = S._analysis_keys(plan, "9:16")
p2 = copy.deepcopy(plan)
t = p2["fx"]["text"][0]
if t.get("lines"):
    t["lines"][0]["text"] = "otro texto"
else:
    t["text"] = "otro texto"
p2["fx"]["sfx"] = []
assert S._analysis_keys(p2, "9:16") == (b1, k1)
p3 = copy.deepcopy(plan)
p3["cuts"] = [{{"start": 1.0, "end": 1.3, "enabled": True, "id": "c1"}}]
assert S._analysis_keys(p3, "9:16")[0] != b1
assert S._analysis_keys(plan, "1:1")[0] != b1
print("OK")
'''
    _run(code, tmp_path, timeout=300)


def test_hijo_spawn_que_reimporta_el_servidor_no_toca_la_base(tmp_path):
    """Un proceso hijo (spawn) que reimporta app/server.py como __mp_main__ NO corre
    recover() (marcaria el export en curso como interrumpido), ni arranca workers."""
    code = '''
import runpy
sys.path.insert(0, "scripts")
import jobstore
jobstore.init()
jobstore.create("0123456789ab", kind="render", ext=".mp4", plan={"version": 2})
jobstore.update("0123456789ab", state="running", phase="rendering")
import threading as _t
n0 = _t.active_count()
g = runpy.run_path("app/server.py", run_name="__mp_main__")
assert g["_CHILD"] is True
st = jobstore.status("0123456789ab")
assert st["state"] == "running" and st["phase"] == "rendering", st
assert not any(t.name.startswith("reelfy-worker") for t in _t.enumerate())
print("OK")
'''
    _run(code, tmp_path)
