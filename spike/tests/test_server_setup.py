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
S._disk_free_gb = lambda: 100.0     # el espacio real del disco compartido no es parte de esta prueba
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
    """Los proyectos NUNCA se purgan solos (aunque pasen de jobstore.KEEP_LAST): sus archivos y
    sus fuentes se quedan; exports: solo los N mas recientes por trabajo; huerfanos y fuentes sin
    uso tras su antiguedad; caches con tope LRU."""
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
# medio propio (imagen vieja) que el PLAN de un trabajo vivo referencia: no se borra
touch(S.SOURCES / "5ce0000000cc.png", old); touch(S.SOURCES / "5ce0000000cc_t.jpg", old)
jobstore.update(ids[2], plan={"version": 2, "fx": {"graphics": [
    {"id": "g1", "kind": "media", "media": {"sid": "5ce0000000cc", "type": "image"}}]}})
# huerfanos (trabajos que ya no estan en la base): viejo se borra, reciente no
touch(S.INPUT / "dead00000000.mp4", old); touch(S.OUTPUT / "dead00000000_123456.mp4", old)
touch(S.INPUT / "dead00000001.mp4")
jobstore.KEEP_LAST = 2
for _ in range(100):                     # la limpieza del arranque (a los 5 s) puede estar corriendo
    res = S.disk_gc("prueba")
    if "skipped" not in res:
        break
    time.sleep(0.1)
assert "jobs_purged" not in res and "pressure_purged" not in res, res
assert sorted(jobstore.all_ids()) == sorted(ids)             # ningun proyecto se purga solo
for jid in ids:
    assert (S.INPUT / f"{jid}.mp4").exists() and (S.OUTPUT / f"{jid}_a1b2c3.mp4").exists()
    assert (S.paths.WORK / f"{jid}.wav").exists() and (S.MUSIC_OUT / f"music_{jid}_0f0f0f.wav").exists()
for sid in srcs:
    assert (S.SOURCES / f"{sid}.mov").exists() and (S.SOURCES / f"{sid}_proxy.mp4").exists()
assert not (S.SOURCES / "5ce0000000aa.mov").exists() and (S.SOURCES / "5ce0000000bb.mov").exists()
assert (S.SOURCES / "5ce0000000cc.png").exists() and (S.SOURCES / "5ce0000000cc_t.jpg").exists()
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
# opciones del proyecto: sin audio de estudio es otra voz (otra base); el aire en una fuente
# vertical se descarta antes (no cambia el base) y el logo nunca cuenta
assert S._analysis_keys(plan, "9:16", {{"enhance_audio": True}}) == (b1, k1)
assert S._analysis_keys(plan, "9:16", {{"enhance_audio": False}})[0] != b1
eo = S._eff_engine(plan, "9:16", S._engine_opts({{"air": 0.16, "air_user": True, "logo": {{"pos": "tl"}}}}))
assert "air" not in eo and S._analysis_keys(plan, "9:16", eo) == (b1, k1)
# el aire es opt-in: el default del Studio (AIR=0.16 sin air_user) no lo activa
assert "air" not in S._engine_opts({{"air": 0.16}}) and "air" not in S._engine_opts({{"air": 0.16, "air_user": 1}})
assert S._engine_opts({{"air": 0.16, "air_user": True}})["air"] == 0.16
# registro por trabajo: solo un cambio de aire/audio de estudio relanza el analisis
import jobstore
jobstore.create("0123456789ab", kind="analyze", ext=".mp4", plan=plan)
assert S._get_engine("0123456789ab") == {{"enhance_audio": True}}
assert S._set_engine("0123456789ab", {{"enhance_audio": True}}) is False
assert S._set_engine("0123456789ab", {{"enhance_audio": True, "logo": {{"path": "x"}}}}) is False
assert jobstore.side_get("0123456789ab", "engine")["logo"] is True
assert S._set_engine("0123456789ab", {{"enhance_audio": False}}) is True
assert S._get_engine("0123456789ab") == {{"enhance_audio": False}}
S._ENGINE.clear()                                    # tras un reinicio: lo guardado
assert S._get_engine("0123456789ab") == {{"enhance_audio": False}}
# opciones conocidas al crear el trabajo (/compose, /analyze `engine`): el primer analisis las usa
S._initial_engine("0123456789ab", '{{"air": 0.2, "air_user": true, "enhance_audio": true}}')
assert S._get_engine("0123456789ab") == {{"air": 0.2, "enhance_audio": True}}
S._initial_engine("0123456789ab", "no es json")          # se ignora
assert S._get_engine("0123456789ab") == {{"air": 0.2, "enhance_audio": True}}
# con el plan (fuente VERTICAL) se comparan las opciones EFECTIVAS: el aire crudo guardado no
# aplica, asi que no cuenta como cambio del base (antes relanzaba el analisis y /status mostraba
# un aire que nunca se aplico); lo guardado pasa a ser lo efectivo
assert S._set_engine("0123456789ab", {{"air": 0.2, "enhance_audio": True}}, plan, "9:16") is False
assert S._get_engine("0123456789ab") == {{"enhance_audio": True}}
assert "air" not in jobstore.side_get("0123456789ab", "engine")
# un aire guardado por una version anterior (sin air_user: era el default del Studio) no vale
jobstore.side_update("0123456789ab", "engine", replace=True, air=0.16, enhance_audio=True)
S._ENGINE.clear()
assert S._get_engine("0123456789ab") == {{"enhance_audio": True}}
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


# musica IA falsa para las pruebas de la tarea lateral `music` y de la vista previa: una pista por
# prompt; get_music espera su compuerta (sondeando la cancelacion como music._lock) y deja la
# pista "en cache"; pending() dice si falta generarla
FAKE_MUSIC = """
import copy
from motion import render as R, music as MU, director
CACHED, GATES, CALLS, CANCELED = set(), {}, [], []
def gate(k):
    return GATES.setdefault(k, threading.Event())
def fake_pending(cfg, dur, style="dinamico", seed=7, cache_dir=None):
    return cfg.get("prompt") not in CACHED
def fake_get_music(cfg, dur, style="dinamico", seed=7, on_pct=None, cache_dir=None):
    k = cfg.get("prompt")
    CALLS.append(k)
    try:
        while k not in CACHED and not gate(k).wait(0.05):
            if on_pct:
                on_pct(10)
    except BaseException:
        CANCELED.append(k)
        raise
    CACHED.add(k)
    return {"source": "ai", "wav": "x", "gen_s": 1.0, "bpm": 100.0}
MU.music_ready = lambda: True
MU.pending = fake_pending
MU.get_music = fake_get_music
R.prepare = lambda plan, opts=None: {"plan": plan, "timeline": None, "job": {
    "final_duration": 10.0, "style": (plan.get("director") or {}).get("style", "dinamico"), "seed": 7}}
R.music_cfg = lambda plan, job, tl, skipped=None: (
    {"source": "ai", "enabled": True, "prompt": plan["fx"]["music"]["prompt"]}, True)
S._music_gen_key = lambda MU_, cfg, dur, style, seed: cfg["prompt"]
S._analysis_keys = lambda plan, fmt, eo=None: ("b", "k")
RED = []
def fake_redirect(plan, style=None, intensity=None, seed=None, only_ids=None, use_llm=False,
                  music_sync=None, music_timeout=12.0):
    RED.append({"style": style, "sync": music_sync, "timeout": music_timeout, "only": only_ids})
    out = copy.deepcopy(plan)
    d = out.setdefault("director", {})
    st = style or d.get("style") or "dinamico"
    d.update(style=st, rev=int(d.get("rev", 0)) + 1)
    d.pop("music_grid", None)
    out["fx"]["music"] = {"source": "ai", "enabled": True, "prompt": "p_" + st}
    if music_sync and ("p_" + st) in CACHED:
        d["music_grid"] = {"bpm": 100.0, "beats": [0.5, 1.1], "key": "p_" + st}
    return out
director.redirect = fake_redirect
JOB = "0123456789ab"
PLAN = {"version": 2, "video": "no.mp4", "duration": 10.0,
        "fx": {"text": [{"id": "t1"}], "music": {"source": "ai", "enabled": True, "prompt": "p_dinamico"}},
        "director": {"style": "dinamico", "rev": 1}}
jobstore.create(JOB, kind="analyze", ext=".mp4", plan=PLAN)
jobstore.update(JOB, state="review", phase="review")
def side(name):
    return jobstore.status(JOB).get(name) or {}
def until(name, cond, t=20):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond(side(name)):
            return side(name)
        time.sleep(0.05)
    raise AssertionError((name, side(name)))
"""

PREVIEW_CODE = """
calls = []
def fake_draft(plan, out, opts):
    k = plan["fx"]["music"]["prompt"]
    calls.append(k)
    assert opts.get("prefetch_music") is False           # la genera la tarea `music`, no el motor
    Path(out).write_bytes(b"x" * 10)
    opts["on_pct"]("render", 50.0)
    return {"duration": 3.0, "music_pending": k not in CACHED, "warnings": []}
R.render_draft = fake_draft
S._start_preview_pro(JOB, PLAN, "9:16", {})
p1 = until("preview", lambda p: p.get("state") == "done" and p.get("music_refresh") == "waiting")
assert p1["music_pending"] is True and p1["pct"] == 100 and "provisional" in p1["message"]
f1 = p1["file"]
assert (S.OUTPUT / f1).exists()
assert until("music", lambda m: m.get("state") == "generating")["key"] == "p_dinamico"
gate("p_dinamico").set()
p2 = until("preview", lambda p: p.get("music_refresh") == "done")
assert p2["file"] != f1 and p2["music_pending"] is False and p2["state"] == "done" and p2["pct"] == 100
assert (S.OUTPUT / p2["file"]).exists()
assert p2["prev_file"] == f1 and (S.OUTPUT / f1).exists()     # la provisional sigue servible
assert jobstore.status(JOB)["preview_music_pending"] is False
assert CALLS == ["p_dinamico"], CALLS                        # una sola generacion
# la siguiente vista previa ya la borra
S._start_preview_pro(JOB, PLAN, "9:16", {})
p3 = until("preview", lambda p: p.get("state") == "done")
assert not (S.OUTPUT / f1).exists() and not (S.OUTPUT / p2["file"]).exists() and p3["prev_file"] is None
# cancelar mientras espera la musica: la primera se queda y el mensaje no promete nada
P2 = json.loads(json.dumps(PLAN)); P2["fx"]["music"]["prompt"] = "p_otra"
S._start_preview_pro(JOB, P2, "9:16", {})
p4 = until("preview", lambda p: p.get("state") == "done" and p.get("music_refresh") == "waiting")
S._cancel_side(JOB, "preview", wait=5.0)
p5 = until("preview", lambda p: p.get("music_refresh") == "canceled")
assert p5["file"] == p4["file"] and (S.OUTPUT / p4["file"]).exists()
assert "se canceló" in p5["message"] and "actualizará" not in p5["message"], p5["message"]
# la edicion cambio (otra pista): la vista previa en espera termina 'superseded'
P3 = json.loads(json.dumps(PLAN)); P3["fx"]["music"]["prompt"] = "p_tercera"
S._start_preview_pro(JOB, P3, "9:16", {})
until("preview", lambda p: p.get("state") == "done" and p.get("music_refresh") == "waiting")
until("music", lambda m: m.get("key") == "p_tercera" and m.get("state") == "generating")
P4 = json.loads(json.dumps(PLAN)); P4["fx"]["music"]["prompt"] = "p_cuarta"
S._kick_music(JOB, P4, "9:16")
p6 = until("preview", lambda p: p.get("music_refresh") == "superseded")
assert "cambió" in p6["message"]
t0 = time.time()
while "p_tercera" not in CANCELED and time.time() - t0 < 10:
    time.sleep(0.05)
assert "p_tercera" in CANCELED
# con un export en curso no rehace el draft (el export ya lleva la musica IA)
P5 = json.loads(json.dumps(PLAN)); P5["fx"]["music"]["prompt"] = "p_quinta"
S._start_preview_pro(JOB, P5, "9:16", {})
until("preview", lambda p: p.get("state") == "done" and p.get("music_refresh") == "waiting")
S._EXPORTING.add(JOB)
n = len(calls)
gate("p_quinta").set()
p7 = until("preview", lambda p: p.get("music_refresh") == "skipped")
S._EXPORTING.discard(JOB)
assert len(calls) == n and "export" in p7["message"]
print("OK")
"""

REDIRECT_CODE = """
CACHED.add("p_dinamico")
# 1) pista nueva: responde sin sincronia y sin esperar; la genera la tarea `music`
r1 = S.direct(JOB, {"plan": PLAN, "style": "elegante"})
assert RED[-1]["sync"] is False and len(RED) == 1
assert r1["music"]["pending"] is True and "music_grid" not in r1["plan"]["director"]
assert any("sincroniza sola" in n for n in r1["plan"]["director"]["notes"]) and r1["warnings"]
assert until("music", lambda m: m.get("state") == "generating")["key"] == "p_elegante"
# 2) otro cambio de estilo antes de que termine: cancela la generacion anterior
r2 = S.direct(JOB, {"plan": PLAN, "style": "minimal"})
until("music", lambda m: m.get("key") == "p_minimal" and m.get("state") == "generating")
t0 = time.time()
while "p_elegante" not in CANCELED and time.time() - t0 < 10:
    time.sleep(0.05)
assert "p_elegante" in CANCELED and "p_elegante" not in CACHED
# 3) la pista termina: la propuesta se guarda sincronizada (misma peticion, con rejilla)
gate("p_minimal").set()
m = until("music", lambda m: m.get("state") == "synced")
plan, rev = jobstore.get_plan(JOB)
assert plan["director"]["music_grid"]["key"] == "p_minimal" and plan["director"]["style"] == "minimal"
assert rev == r2["plan_rev"] + 1 and m["plan_rev"] == rev and m["from_rev"] == r2["plan_rev"]
assert RED[-1]["sync"] is True and RED[-1]["style"] == "minimal"
assert S._synced_plan(JOB, r2["plan"]) == plan                  # vista previa/export: la sincronizada
edited = json.loads(json.dumps(r2["plan"])); edited["fx"]["text"][0]["x"] = 0.3
assert S._synced_plan(JOB, edited) is edited
# 4) el autoguardado del Studio con la propuesta tal cual: se queda la sincronizada (sin 409)
r = S.put_plan(JOB, {"plan": r2["plan"], "base_rev": r2["plan_rev"]})
assert r["ok"] and r["plan_rev"] == rev and jobstore.get_plan(JOB)[0]["director"].get("music_grid")
# ... y con una edicion encima: gana el usuario y se avisa que se puede re-sincronizar
r = S.put_plan(JOB, {"plan": edited, "base_rev": r2["plan_rev"]})
assert r["ok"] and r["plan_rev"] == rev + 1
m = side("music")
assert m["state"] == "superseded" and m["resync"] == "available"
assert jobstore.status(JOB)["music_resync"] == "available"
# 5) el usuario edita mientras se genera: no se pisa su plan (music_resync='available')
r3 = S.direct(JOB, {"plan": PLAN, "style": "energetico"})
until("music", lambda m: m.get("key") == "p_energetico" and m.get("state") == "generating")
e2 = json.loads(json.dumps(r3["plan"])); e2["fx"]["text"][0]["x"] = 0.7
assert S.put_plan(JOB, {"plan": e2, "base_rev": r3["plan_rev"]})["ok"]
gate("p_energetico").set()
m = until("music", lambda m: m.get("state") == "ready" and m.get("resync") == "available")
assert jobstore.get_plan(JOB)[0]["fx"]["text"][0]["x"] == 0.7
assert "music_grid" not in jobstore.get_plan(JOB)[0]["director"]
# 6) pista ya en cache: sincroniza en la misma respuesta (segunda pasada con music_sync)
n = len(RED)
r4 = S.direct(JOB, {"plan": PLAN, "style": "dinamico"})
assert [x["sync"] for x in RED[n:]] == [False, True] and r4["music"]["pending"] is False
assert r4["plan"]["director"]["music_grid"]["key"] == "p_dinamico"
assert RED[-1]["timeout"] >= S.REDIRECT_MUSIC_WAIT_S and S.REDIRECT_MUSIC_WAIT_S <= 2.0
# 7) regenerar un item (only_ids) no toca la musica
n = len(RED)
S.direct(JOB, {"plan": PLAN, "only_ids": ["t1"]})
assert RED[n]["only"] == ["t1"] and len(RED) == n + 1
# 8) cancelar la generacion (/cancel?only=music o "Re-dirigir todo")
S.direct(JOB, {"plan": PLAN, "style": "elegante"})
until("music", lambda m: m.get("key") == "p_elegante" and m.get("state") == "generating")
assert S.cancel(JOB, only="music")["side"] == 1
until("music", lambda m: m.get("state") == "canceled")
print("OK")
"""


def test_vista_previa_se_actualiza_sola_con_la_musica_ia(tmp_path):
    """Draft con la musica IA aun generandose (stats.music_pending): la vista previa queda lista
    con la procedural; la tarea lateral `music` (la unica generacion del trabajo) genera la pista
    y el servidor rehace el draft y publica el archivo nuevo en la misma tarea. La provisional NO
    se borra (el Studio puede estar reproduciendola: prev_file) hasta la siguiente vista previa.
    Cancelada, superada por otra edicion o con un export en curso, el mensaje dice la verdad."""
    code = "import server as S, jobstore\nfrom pathlib import Path\n" + FAKE_MUSIC + PREVIEW_CODE
    _run(code, tmp_path, {"REELFY_WORKERS": "0", "REELFY_BG_ANALYSIS": "0", "REELFY_SRC_PROXY": "0"})


def test_redirigir_sin_full_sincroniza_con_la_musica_de_la_propuesta(tmp_path):
    """/direct sin `full` (cambio de estilo, regenerar) va en la ruta critica del Studio: nunca
    espera a ACE ni deja un hilo huerfano (music_sync=False). Con la pista en cache sincroniza en
    la misma respuesta; si hay que generarla, la tarea lateral `music` la genera (una por trabajo:
    el siguiente cambio de estilo CANCELA la anterior) y al terminar guarda la propuesta
    sincronizada si nadie la edito (si no: music_resync='available'). El autoguardado del Studio
    sobre la propuesta sin sincronia no choca (409) con esa escritura del servidor, y la vista
    previa/export de la propuesta usan la version sincronizada."""
    code = "import server as S, jobstore\n" + FAKE_MUSIC + REDIRECT_CODE
    _run(code, tmp_path, {"REELFY_WORKERS": "0", "REELFY_BG_ANALYSIS": "0", "REELFY_SRC_PROXY": "0"})


def test_cancelar_sin_only_con_export_conserva_la_vista_previa(tmp_path):
    """El 'Cancelar export' del Studio hace POST /cancel sin `only`: con un export en curso se
    cancelan el export, el analisis de fondo y re-dirigir, pero no la vista previa (ni su
    actualizacion con la musica IA) ni la generacion de la musica."""
    code = """
import server as S, jobstore
JOB = "0123456789ab"
jobstore.create(JOB, kind="render", ext=".mp4", plan={"version": 2, "fx": {}})
jobstore.update(JOB, state="running", phase="rendering")
def idle(jid, ev):
    ev.wait(30)
for name in ("preview", "analysis", "direct", "music"):
    S._start_side(JOB, name, idle)
S._EXPORTING.add(JOB)
r = S.cancel(JOB)
assert r["ok"] and r["side"] == 2, r
alive = {k[1] for k, v in S._SIDE.items() if k[0] == JOB and not v["cancel"].is_set()}
assert alive == {"preview", "music"}, alive
assert jobstore.get_light(JOB)["cancel"]
S._EXPORTING.discard(JOB)
jobstore.update(JOB, state="review", phase="review", kind="analyze")
S.cancel(JOB)                                            # sin export: todo
assert not any(not v["cancel"].is_set() for k, v in S._SIDE.items() if k[0] == JOB)
print("OK")
"""
    _run(code, tmp_path, {"REELFY_WORKERS": "0", "REELFY_BG_ANALYSIS": "0", "REELFY_SRC_PROXY": "0"})


def test_redirigir_conserva_el_texto_de_musica_del_usuario(tmp_path):
    """El texto que el usuario escribio para la musica con IA (plan.studio.musicPrompt) se queda al
    cambiar el estilo de edicion: /direct lo trata como si lo mandara (sin sincronia a la pista del
    director, que seria otra cancion) y el Studio nunca muestra el texto en ingles del director.
    Si ya no es el de su plan (lo cambio la IA), el director pone el suyo."""
    code = "import server as S, jobstore\n" + FAKE_MUSIC + """
TXT = "cumbia alegre con acordeón"
P = json.loads(json.dumps(PLAN))
P["fx"]["music"]["prompt"] = TXT
P["studio"] = {"musicPrompt": TXT}
assert S._user_music_prompt(P) == TXT
r = S.direct(JOB, {"plan": P, "style": "elegante"})
assert r["plan"]["fx"]["music"]["prompt"] == TXT, r["plan"]["fx"]["music"]
assert r["plan"]["studio"]["musicPrompt"] == TXT
assert RED[-1]["sync"] is False and len(RED) == 1, RED          # sin segunda pasada sincronizada
assert "music_grid" not in r["plan"]["director"]
# sin texto propio vigente: el del director (cambia con el estilo)
P2 = json.loads(json.dumps(PLAN)); P2["studio"] = {"musicPrompt": "otra cosa"}
assert S._user_music_prompt(P2) is None
r2 = S.direct(JOB, {"plan": P2, "style": "minimal"})
assert r2["plan"]["fx"]["music"]["prompt"] == "p_minimal", r2["plan"]["fx"]["music"]
# otra fuente, apagada o vacia: no cuenta
assert S._user_music_prompt(dict(P, fx={"music": dict(P["fx"]["music"], source="procedural")})) is None
assert S._user_music_prompt(dict(P, fx={"music": dict(P["fx"]["music"], enabled=False)})) is None
assert S._user_music_prompt(dict(P, studio={"musicPrompt": "  "})) is None
assert S._user_music_prompt(dict(P, studio={"musicPromptByUser": True})) is None
S.cancel(JOB, only="music")
print("OK")
"""
    _run(code, tmp_path, {"REELFY_WORKERS": "0", "REELFY_BG_ANALYSIS": "0", "REELFY_SRC_PROXY": "0"})


def test_limpieza_del_disco_no_borra_dentro_de_carpetas_enlazadas(tmp_path):
    """Una copia de datos para pruebas que ENLAZA una carpeta (input/sources, output...) a otro
    proyecto: la limpieza al arrancar no borra nada a traves del enlace (antes borro fuentes del
    proyecto real). Con enlaces archivo por archivo se quita el enlace, nunca el archivo de origen;
    y lo propio sin uso se sigue limpiando."""
    ext = tmp_path.parent / (tmp_path.name + "_otro")
    code = """
import server as S, shutil
from pathlib import Path
ext = Path(os.environ["EXT_DIR"]); (ext / "src").mkdir(parents=True, exist_ok=True); (ext / "out").mkdir(exist_ok=True)
old = time.time() - 10 ** 6
f = ext / "src" / "aaaaaaaaaaaa.png"; f.write_bytes(b"x"); os.utime(f, (old, old))
S.SOURCE_AGE_S = 0
# input/sources enlazada a la carpeta de otro proyecto: no se toca
if S.SOURCES.exists() and not S.SOURCES.is_symlink():
    shutil.rmtree(S.SOURCES)
S.SOURCES.parent.mkdir(parents=True, exist_ok=True)
os.symlink(ext / "src", S.SOURCES)
assert S._gc_dir_ok(S.SOURCES) is False
assert S._gc_sources(now=time.time() + 5) == 0 and f.exists()
# carpeta propia con un enlace por archivo: se quita el enlace, el archivo de origen se queda
os.unlink(S.SOURCES); S.SOURCES.mkdir()
os.symlink(f, S.SOURCES / f.name)
assert S._gc_dir_ok(S.SOURCES) is True
assert S._gc_sources(now=time.time() + 5) == 1
assert not (S.SOURCES / f.name).is_symlink() and f.exists()
# lo propio sin uso si se limpia
g = S.SOURCES / "bbbbbbbbbbbb.png"; g.write_bytes(b"y"); os.utime(g, (old, old))
assert S._gc_sources(now=time.time() + 5) == 1 and not g.exists()
# huerfanos: tampoco dentro de una carpeta enlazada (output/ de otro proyecto)
o = ext / "out" / "0123456789ab.mp4"; o.write_bytes(b"z"); os.utime(o, (old, old))
shutil.rmtree(S.OUTPUT); os.symlink(ext / "out", S.OUTPUT)
S._gc_orphans(now=time.time() + 10 ** 7)
assert o.exists()
print("OK")
"""
    try:
        _run(code, tmp_path, {"EXT_DIR": str(ext), "REELFY_WORKERS": "0", "REELFY_BG_ANALYSIS": "0", "REELFY_SRC_PROXY": "0"})
    finally:
        __import__("shutil").rmtree(ext, ignore_errors=True)
