"""app/server.py: la hoja "Exportar" del lado del servidor. Opciones por formato (resoluciones con
su motivo), estimacion, ajustes recordados, selector NATIVO de carpetas (falso: REELFY_PICK_FOLDER_CMD),
"Mostrar en Finder"/"Abrir" (falso: REELFY_OPEN_CMD; solo archivos que la app exporto), validacion
de POST /render {export}, registro de la composicion (y su reconstruccion), recomposicion a mas
resolucion SOLO cuando hace falta y en cache, copia al destino sin sobrescribir, errores en espanol
y que las fuentes del registro no se borren.

Cada prueba importa el servidor en un proceso aparte con DATA temporal (REELFY_WORKERS=0: nada se
ejecuta solo) y la carpeta de siempre en tmp (REELFY_EXPORT_DIR): nada toca ~/Movies ni abre
ventanas. Videos sinteticos (lavfi) de 1440x2560: el video combinado se compone con tope 360x640
para que 1440p necesite recomponer (en la app el tope es 1620x2880 y 4K recompone)."""
from test_server_setup import _run


def _out(tmp_path):
    """Carpetas del usuario: FUERA de DATA (DATA = tmp_path; una carpeta interna se rechaza)."""
    d = tmp_path.parent / f"{tmp_path.name}_fuera"
    d.mkdir(exist_ok=True)
    return d


def _env(tmp_path):
    (tmp_path / "bin").mkdir(exist_ok=True)
    picker = tmp_path / "bin" / "picker"
    picker.write_text('#!/bin/bash\n'
                      'echo "$@" >> "$PICK_LOG"\n'
                      'case "$(cat "$PICK_MODE")" in\n'
                      '  cancel) echo "execution error: User canceled. (-128)" >&2; exit 1;;\n'
                      '  fail) echo "kaput" >&2; exit 2;;\n'
                      '  *) cat "$PICK_MODE"; echo;;\n'
                      'esac\n')
    opener = tmp_path / "bin" / "opener"
    opener.write_text('#!/bin/bash\necho "$1|$2" >> "$OPEN_LOG"\n')
    for f in (picker, opener):
        f.chmod(0o755)
    (tmp_path / "pick_mode").write_text("cancel")
    return {"REELFY_WORKERS": "0", "REELFY_EXPORT_DIR": str(_out(tmp_path) / "Movies" / "Reelfy"),
            "REELFY_PICK_FOLDER_CMD": str(picker), "REELFY_OPEN_CMD": str(opener),
            "PICK_MODE": str(tmp_path / "pick_mode"), "PICK_LOG": str(tmp_path / "pick.log"),
            "OPEN_LOG": str(tmp_path / "open.log"), "OUT_DIR": str(_out(tmp_path))}


PRELUDE = '''
import server as S, jobstore, exportkit as EK, compose as C, paths, subprocess
from fastapi import HTTPException
from pathlib import Path
S._kick_src_proxy = lambda *a, **k: None
S._kick_analysis = lambda *a, **k: None
TMP = Path(os.environ["OUT_DIR"])
S.SOURCES.mkdir(parents=True, exist_ok=True)
S.INPUT.mkdir(parents=True, exist_ok=True)

def src(sid, w=1440, h=2560, d=2.0, f=330):
    p = S.SOURCES / f"{sid}.mp4"
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=30:d={d}",
                    "-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=48000:duration={d}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)], check=True)
    return p

def item(p, start, a, b, **kw):
    it = {"path": str(p), "sid": p.stem, "type": "video", "name": p.name, "start": start, "layer": 0,
          "fadeIn": 0.0, "fadeOut": 0.0, "in": a, "out": b, "vol": 1.0, "mute": False}
    it.update(kw)
    return it

def status_code(fn, *a, **k):
    try:
        fn(*a, **k)
    except HTTPException as e:
        return e.status_code, e.detail
    return 200, None

def composed_job(jid, sids=("5a0000000001", "5a0000000002"), record=True, montage=False, cap=(360, 640)):
    """Proyecto compuesto (como lo deja /compose) con su plan v2 minimo y, si `record`, su registro."""
    ps = [S.SOURCES / f"{s}.mp4" if (S.SOURCES / f"{s}.mp4").exists() else src(s, f=300 + 50 * k)
          for k, s in enumerate(sids)]
    mm = (lambda k: {"montage": {"slot": f"s{k}", "cand": "c1", "join": "line" if k else None}}) if montage \\
        else (lambda k: {})
    spec = {"fps": 30, "overlays": [], "main": [item(p, 1.5 * k, 0.0, 1.5, **mm(k)) for k, p in enumerate(ps)]}
    vid = S.INPUT / f"{jid}.mp4"
    info = {}
    C.compose(spec, str(vid), paths.WORK / f"cw_{jid}", info=info, max_canvas=cap)
    rec = C.spec_record(spec, info); rec["t"] = time.time()
    dur = C.probe(vid)["dur"]
    shots = C.plan_shots(info["shots"], dur)
    plan = {"version": 2, "video": str(vid), "duration": dur, "dims": [info["W"], info["H"]], "shots": shots,
            "fx": {}, "cuts": [], "phrases": [],
            "highlights": [{"start": 0.2, "end": 2.6, "enabled": True, "title": "Corto"}],
            "director": {"script": {"topic": "Promo: clínica/centro"}}}
    if montage:
        plan["montage"] = C.montage_plan(None, info["main"], shots)
    jobstore.create(jid, kind="compose", ext=".mp4", params={"spec": spec}, plan=plan)
    jobstore.update(jid, state="review", phase="review")
    if record:
        jobstore.set_compose_spec(jid, rec)
    return plan, rec

class Prog:
    """Progress falso para llamar a _hires_video / _save_export directo."""
    class cancel:
        @staticmethod
        def canceled():
            return False
    def __init__(self):
        self.calls = []
    def update(self, **kw):
        self.calls.append(kw)

def pick(mode):
    Path(os.environ["PICK_MODE"]).write_text(mode)
'''


def test_opciones_resoluciones_nombre_y_ajustes(tmp_path):
    _run(PRELUDE + '''
J = "e00000000001"
plan, rec = composed_job(J)
assert rec["native"] == [1440, 2560] and (rec["W"], rec["H"]) == (360, 640)
o = S.export_options(J)
assert o["source"]["label"] == "1440p" and o["source"]["kind"] == "compose" and o["source"]["can_recompose"]
assert o["source"]["combined"] == [360, 640]
r916 = {r["id"]: r for r in o["formats"]["9:16"]["resolutions"]}
assert r916["1440p"]["enabled"] and r916["1440p"]["recompose"] and (r916["1440p"]["W"], r916["1440p"]["H"]) == (1440, 2560)
assert not r916["4k"]["enabled"] and r916["4k"]["reason"] == "Tu video original es 1440p; 4K no agregaría calidad"
assert not r916["1080p"]["recompose"] and not r916["720p"]["recompose"]       # <= 1080p: lo de siempre
assert set(o["formats"]) == {"9:16", "1:1", "4:5", "16:9", "original"}
# 16:9 de un vertical: la toma completa va a lo alto (pillar, sin recorte): 2560 px de alto dan 4K
r169 = {r["id"]: r for r in o["formats"]["16:9"]["resolutions"]}
assert r169["1440p"]["enabled"] and r169["4k"]["enabled"] and r169["4k"]["recompose"]
assert o["source"]["lossy"] is None and o["source"]["legacy"] is False
assert o["name"].startswith("Promo - clínica-centro - ") and "/" not in o["name"] and o["ext"] == ".mp4"
assert o["defaults"] == {"res": "1080p", "quality": "alta", "shorts": True, "fmt": "9:16"}
assert [q["id"] for q in o["qualities"]] == ["estandar", "alta", "maxima"]
f = o["folder"]
assert f["default"] and f["writable"] and f["path"] == str(Path(os.environ["REELFY_EXPORT_DIR"]).resolve())
assert Path(f["path"]).is_dir() and o["shorts_available"] == 1
assert o["picker"] is True and o["reveal_label"] == EK.reveal_label()
assert status_code(S.export_options, J, "2:1")[0] == 400
assert status_code(S.export_options, "noexiste")[0] == 404
# un proyecto de UN video (sin composicion): la resolucion real es la del video
src("5a00000000aa", 720, 1280)
J2 = "e00000000002"
import shutil
shutil.copyfile(S.SOURCES / "5a00000000aa.mp4", S.INPUT / f"{J2}.mp4")
jobstore.create(J2, kind="analyze", ext=".mp4", plan={"version": 2, "video": str(S.INPUT / f"{J2}.mp4"),
                "duration": 2.0, "fx": {}, "cuts": [], "phrases": [],
                "shots": [{"start": 0.0, "end": 1.0}, {"start": 1.0, "end": 2.0}]})   # tomas del director
jobstore.update(J2, state="review", phase="review")
o2 = S.export_options(J2)
assert o2["source"]["kind"] == "single" and o2["source"]["label"] == "720p"
assert [r["id"] for r in o2["formats"]["9:16"]["resolutions"] if r["enabled"]] == ["720p", "1080p"]
assert jobstore.get_compose_spec(J2) is None
# estimacion: 1440p con recomposicion (aun sin cache) cuesta mas que 1080p
e1 = S.export_estimate(J, res="1080p", fmt="9:16", quality="alta", shorts=1, folder=None)
e4 = S.export_estimate(J, res="1440p", fmt="9:16", quality="maxima", shorts=0, folder=None)
assert e4["recompose"] and not e4["recompose_cached"] and e4["parts"]["recompose"] > 0 and not e1["recompose"]
assert e4["bytes"] > e1["bytes_main"] and e1["bytes_shorts"] > 0 and e4["bytes_shorts"] == 0
assert e1["text"].startswith("Tardará ") and " · ~" in e1["text"] and e1["space_ok"]
assert e1["frames"] in (90, 91) and e1["W"] == 1080
assert e1["max_res"] == "1440p" and e1["source_res"] == "1440x2560"
assert S.export_estimate(J, res="1080p", fmt="16:9", quality="alta", shorts=0, folder=None)["max_res"] == "4k"
assert S.export_estimate(J, res="1080p", fmt="1:1", quality="alta", shorts=0, folder=None)["max_res"] == "1440p"
assert status_code(S.export_estimate, J, res="8k")[0] == 400
assert status_code(S.export_estimate, J, quality="ultra")[0] == 400
# ajustes recordados
s = S.export_settings_save({"res": "720p", "quality": "estandar", "shorts": False, "fmt": "1:1"})
assert s["last"]["res"] == "720p" and s["last"]["quality"] == "estandar" and s["last"]["shorts"] is False
o = S.export_options(J)
assert o["defaults"] == {"res": "720p", "quality": "estandar", "shorts": False, "fmt": "1:1"}
assert status_code(S.export_settings_save, {"folder": "/tmp"})[0] == 400       # carpeta no elegida
assert status_code(S.export_settings_save, {"res": "9k"})[0] == 400
print("OK")
''', tmp_path, _env(tmp_path))


def test_reconstruir_registro_y_fuentes_faltantes(tmp_path):
    _run(PRELUDE + '''
# montaje compuesto antes de guardar el registro y ya exportado: se reconstruye del montaje
J = "e00000000003"
plan, rec = composed_job(J, record=False, montage=True)
jobstore.requeue(J, kind="render", params={"pro": True}, plan=plan)       # el export reemplaza params
jobstore.update(J, state="done", phase="done")
assert jobstore.get_compose_spec(J) is None and "spec" not in jobstore.get(J)["params"]
src_ = S._source_info(J, plan)
assert src_["can_recompose"] and src_["native"] == [1440, 2560]
got = jobstore.get_compose_spec(J)
assert got["src"] == "backfill:montage" and len(got["spec"]["main"]) == 2
assert all(isinstance(it["vol"], float) for it in got["spec"]["main"])
assert [s[0] for s in got["shots"]] == [s["start"] for s in plan["shots"]]
# ensamble a mano aun sin exportar: del spec de sus parametros
J2 = "e00000000004"
plan2, _ = composed_job(J2, sids=("5a0000000001", "5a0000000002"), record=False)
S._source_info(J2, plan2)
assert jobstore.get_compose_spec(J2)["src"] == "backfill:params"
# fuente borrada: no se puede recomponer y el motivo lo dice
J3 = "e00000000005"
plan3, _ = composed_job(J3, sids=("5a0000000007", "5a0000000008"))
(S.SOURCES / "5a0000000008.mp4").unlink()
o = S.export_options(J3)
assert not o["source"]["can_recompose"] and o["source"]["missing"] == ["5a0000000008.mp4"]
r = {x["id"]: x for x in o["formats"]["9:16"]["resolutions"]}
assert not r["1440p"]["enabled"] and "Faltan clips originales en este equipo (5a0000000008.mp4)" in r["1440p"]["reason"]
assert "el máximo es 1080p" in r["1440p"]["reason"]
# montaje sin registro y con un clip borrado: no se reconstruye
J4 = "e00000000006"
plan4, _ = composed_job(J4, sids=("5a0000000009", "5a0000000010"), record=False, montage=True)
jobstore.requeue(J4, kind="render", params={"pro": True}, plan=plan4)
(S.SOURCES / "5a0000000010.mp4").unlink()
s4 = S._source_info(J4, plan4)
assert not s4["can_recompose"] and s4["missing"] and jobstore.get_compose_spec(J4) is None
# las fuentes que solo nombra el registro NO se las lleva la limpieza (sirven para el 4K)
old = time.time() - 3 * 86400
for p in S.SOURCES.iterdir():
    os.utime(p, (old, old))
huerfana = src("5a00000000ff"); os.utime(huerfana, (old, old))
S._gc_sources()
assert (S.SOURCES / "5a0000000001.mp4").exists() and (S.SOURCES / "5a0000000007.mp4").exists()
assert not huerfana.exists()
assert "5a0000000007" in jobstore.sids_by_job()[J3]
print("OK")
''', tmp_path, _env(tmp_path))


def test_render_valida_las_opciones_y_las_recuerda(tmp_path):
    env = _env(tmp_path)
    (_out(tmp_path) / "Elegida").mkdir()
    _run(PRELUDE + '''
J = "e00000000011"
plan, rec = composed_job(J)
def again():
    jobstore.update(J, state="review", phase="review")
def render(ex, **kw):
    again()
    return S.render(J, dict({"plan": plan, "fmt": "9:16", "export": ex}, **kw))
assert status_code(render, {"res": "8k"}) == (400, "Resolución desconocida: 8k. Usa 720p, 1080p, 1440p, 4K.")
assert status_code(render, {"quality": "ultra"})[0] == 400
c, d = status_code(render, {"res": "4k"})
assert c == 400 and d == "Tu video original es 1440p; 4K no agregaría calidad"
c, d = status_code(render, {"res": "1080p", "folder": "/tmp"})
assert c == 400 and "Cambiar" in d                       # una ruta que no vino del selector
c, d = status_code(render, {"res": "1080p", "folder": str(TMP / "Elegida" / ".." / "Elegida")})
assert c == 400                                          # tampoco disfrazada
# sin espacio en el disco: 507 con el motivo
real = S.shutil.disk_usage
S.shutil.disk_usage = lambda p: real(p)._replace(free=1000)
c, d = status_code(render, {"res": "1440p"})
assert c == 507 and d.startswith("No hay espacio suficiente para exportar en 1440p"), d
S.shutil.disk_usage = real
# carpeta elegida con el selector: se acepta
pick(str(TMP / "Elegida"))
pk = S.pick_folder({})
assert pk["ok"] and pk["path"] == str((TMP / "Elegida").resolve())
r = render({"res": "1440p", "quality": "maxima", "name": "Mi video: final/v2.mp4", "folder": pk["path"],
            "shorts": False})
ex = r["export"]
assert r["ok"] and r["pro"] and ex["res"] == "1440p" and (ex["W"], ex["H"]) == (1440, 2560)
assert ex["name"] == "Mi video - final-v2" and ex["file_name"] == "Mi video - final-v2.mp4"
assert ex["folder"] == pk["path"] and ex["recompose"] and ex["shorts"] is False and ex["quality"] == "maxima"
assert ex["estimate"]["text"].startswith("Tardará")
p = jobstore.get(J)["params"]
assert p["export"]["recompose"] == [1440, 2560] and p["export"]["base_W"] == 1440 and p["shorts"] is False
assert 10 <= p["export"]["rec_span"] <= 60
last = S.export_settings()["last"]
assert last["folder"] == pk["path"] and last["fmt"] == "9:16"
# 1080p: sin recomponer ni tamano propio (lo de siempre); sin nombre -> "<proyecto> - <fecha>"
r = render({"res": "1080p"})
p = jobstore.get(J)["params"]["export"]
assert p["recompose"] is None and p["base_W"] is None and S._export_size_opts(plan, "9:16", p) == {}
assert r["export"]["name"].startswith("Promo - clínica-centro - ") and r["export"]["folder"] == pk["path"]
# 720p: salida chica sobre el MISMO base de 1080
p = dict(p, res="720p", W=720, H=1280)
assert S._export_size_opts(plan, "9:16", p) == {"W": 720, "H": 1280, "base_W": 1080, "base_H": 1920}
# sin `export`: exactamente como antes
again()
S.render(J, {"plan": plan, "fmt": "9:16", "shorts": True})
assert "export" not in jobstore.get(J)["params"]
print("OK")
''', tmp_path, env)


def test_selector_de_carpetas_y_mostrar_en_finder(tmp_path):
    env = _env(tmp_path)
    (_out(tmp_path) / "Elegida").mkdir()
    ro = _out(tmp_path) / "SoloLectura"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        _run(PRELUDE + '''
pick("cancel")
assert S.pick_folder({}) == {"ok": False, "canceled": True, "path": None}
log = Path(os.environ["PICK_LOG"]).read_text().splitlines()
assert EK.PICK_PROMPT in log[-1] and os.environ["REELFY_EXPORT_DIR"].split("/")[-1] in log[-1]   # empieza en la de siempre
pick("fail")
c, d = status_code(S.pick_folder, {})
assert c == 501 and d == EK.PICK_FAIL and "kaput" not in d      # el stderr va al registro, no al usuario
pick(str(TMP / "SoloLectura"))
c, d = status_code(S.pick_folder, {})
assert c == 400 and d.startswith("No se puede escribir en la carpeta «SoloLectura»"), d
assert str((TMP / "SoloLectura").resolve()) not in S.EXPORT_STORE.allowed_folders()
pick(str(TMP / "Elegida") + "/")
r = S.pick_folder({"start": "/etc"})                     # un inicio que no se eligio se ignora
assert r["ok"] and r["path"] == str((TMP / "Elegida").resolve()) and r["writable"] and r["free_bytes"] > 0
assert "/etc" not in Path(os.environ["PICK_LOG"]).read_text().splitlines()[-1]
assert S.export_settings()["folder"]["path"] == r["path"]          # queda recordada
assert any(getattr(x, "path", "") == "/settings/export" for x in S.app.routes)    # alias
S._PICK_LOCK.acquire()
assert status_code(S.pick_folder, {})[0] == 409
S._PICK_LOCK.release()
# DATA (interna) nunca
pick(str(paths.DATA / "output"))
(paths.DATA / "output").mkdir(parents=True, exist_ok=True)
c, d = status_code(S.pick_folder, {})
assert c == 400 and "interna de Reelfy" in d
# Mostrar en Finder / Abrir: solo lo que la app exporto
f = TMP / "Elegida" / "Video.mp4"; f.write_bytes(b"x")
assert status_code(S.reveal, {"path": str(f)})[0] == 403
S.EXPORT_STORE.add_exported([str(f)], "e0")
assert S.reveal({"path": str(f)}) == {"ok": True, "path": str(f), "action": "reveal"}
assert S.open_export({"path": str(f)})["action"] == "open"               # POST /open = Abrir
for _ in range(50):                                      # el Finder se lanza sin esperar
    got = Path(os.environ["OPEN_LOG"]).read_text().splitlines() if Path(os.environ["OPEN_LOG"]).exists() else []
    if len(got) >= 2:
        break
    time.sleep(0.1)
assert sorted(got) == sorted([f"reveal|{f}", f"open|{f}"]), got
assert status_code(S.reveal, {"path": str(TMP / "Elegida" / ".." / "Elegida" / "Video.mp4")}) == (200, None)
assert status_code(S.reveal, {"path": str(TMP / "Elegida" / ".." / ".." / "etc" / "passwd")})[0] == 403
assert status_code(S.reveal, {"path": "relativo.mp4"})[0] == 403
assert status_code(S.reveal, {"path": str(f), "action": "rm"})[0] == 400
f.unlink()
assert status_code(S.reveal, {"path": str(f)})[0] == 404
print("OK")
''', tmp_path, env)
    finally:
        ro.chmod(0o755)


def test_recomponer_solo_cuando_hace_falta_y_en_cache(tmp_path):
    _run(PRELUDE + '''
J = "e00000000021"
plan, rec = composed_job(J)
ex = {"res": "1440p", "recompose": [1440, 2560], "W": 1440, "H": 2560}
pr = Prog()
t0 = time.time()
out, how, frames = S._hires_video(J, plan, ex, pr, 30.0, t0)
assert how == "new" and out.parent == paths.WORK and out.name.startswith(f"{J}_") and out.name.endswith("_1440x2560_hires.mp4")
i = C.probe(out)
assert (i["w"], i["h"]) == (1440, 2560) and abs(i["dur"] - C.probe(plan["video"])["dur"]) < 0.05
assert frames in (90, 91) and Path(str(out)[:-4] + ".json").exists()
assert any(c.get("stage") == "recompose" and "Preparando el video en 1440p" in (c.get("message") or "") for c in pr.calls)
assert all(c.get("pct", 0) <= 30.0 for c in pr.calls)
assert not list(paths.WORK.glob("*.part.mp4")) and not (paths.WORK / f"compose_{J}_hires").exists()
# segunda vez: de la cache (sin recomponer)
ino = out.stat().st_ino
from motion import video_io as V
sig = V._file_sig(out)                 # la clave del video base del export lleva el mtime del video
meta = Path(str(out)[:-4] + ".json"); os.utime(meta, (1, 1))
time.sleep(0.02)
out2, how2, _ = S._hires_video(J, plan, ex, Prog(), 30.0, t0)
assert out2 == out and how2 == "cache" and out.stat().st_ino == ino          # el mismo archivo
# usarlo NO toca el video (si no, el export en 4K rehacia el video base cada vez); el uso va al .json
assert V._file_sig(out) == sig and meta.stat().st_mtime > 1000
assert S._hires_cached(J, plan, (1440, 2560)) == out
e = S.export_estimate(J, res="1440p", fmt="9:16", quality="alta", shorts=0, folder=None)
assert e["recompose"] and e["recompose_cached"] and e["parts"]["recompose"] == 0
# el proyecto se volvio a componer (registro nuevo): otra clave; el viejo se poda (uno por proyecto)
rec2 = dict(jobstore.get_compose_spec(J), t=time.time() + 1)
jobstore.set_compose_spec(J, rec2)
out3, how3, _ = S._hires_video(J, plan, ex, Prog(), 30.0, t0)
assert how3 == "new" and out3 != out and not out.exists() and out3.exists()
# es cache regenerable: "Liberar espacio" se la lleva
ctx = S._storage_ctx()
assert out3 in S._job_units(J, ctx)["cache"]
# tomas que no coinciden con la edicion -> error claro (ofrece 1080p), sin restos
bad = dict(rec2, t=time.time() + 2, shots=[[0.0, 0.7], [0.7, 3.0]])
jobstore.set_compose_spec(J, bad)
try:
    S._hires_video(J, plan, ex, Prog(), 30.0, t0)
    raise SystemExit("debia fallar")
except S.ExportError as e:
    assert e.code == "recompose_failed" and e.fallback == "1080p" and e.public
    # en espanol y sin detalles tecnicos (duraciones, tomas: van al registro)
    assert str(e) == ("No se pudo preparar el video en 1440p: las tomas recompuestas no coinciden con tu "
                      "edición. Puedes exportarlo en 1080p."), str(e)
assert not list(paths.WORK.glob("*.part.mp4"))
# un fallo de ffmpeg al recomponer: la frase, sin el texto tecnico
_orig = C.compose
def _boom(*a, **k):
    raise RuntimeError("ffmpeg exited 1: Error while filtering: Cannot allocate memory")
C.compose = _boom
try:
    S._hires_video(J, plan, ex, Prog(), 30.0, t0)
    raise SystemExit("debia fallar")
except S.ExportError as e:
    assert e.code == "recompose_failed" and e.fallback == "1080p"
    assert str(e) == "No se pudo preparar el video en 1440p desde tus clips originales. Puedes exportarlo en 1080p.", str(e)
finally:
    C.compose = _orig
# fuente borrada
jobstore.set_compose_spec(J, rec2)
(S.SOURCES / "5a0000000002.mp4").rename(TMP / "fuera.mp4")
try:
    S._hires_video(J, plan, dict(ex, recompose=[1440, 2560]), Prog(), 30.0, t0)
    raise SystemExit("debia fallar")
except S.ExportError as e:
    assert e.code == "recompose_failed" and "faltan clips originales en este equipo (5a0000000002.mp4)" in str(e)
# a lo sumo HIRES_KEEP en total
(TMP / "fuera.mp4").rename(S.SOURCES / "5a0000000002.mp4")
for k in range(4):
    f = paths.WORK / f"f0000000000{k}_abc_10x10_hires.mp4"; f.write_bytes(b"x"); os.utime(f, (k, k))
S._prune_hires()
assert len(list(paths.WORK.glob("*_hires.mp4"))) == S.HIRES_KEEP
# lo usado hace poco (su .json) se queda aunque el video sea viejo
for f in paths.WORK.glob("*_hires.*"):
    f.unlink()
a = paths.WORK / "f00000000010_abc_10x10_hires.mp4"; a.write_bytes(b"x"); os.utime(a, (5, 5))
Path(str(a)[:-4] + ".json").write_text("{}")
for k in range(3):
    f = paths.WORK / f"f0000000001{k + 1}_abc_10x10_hires.mp4"; f.write_bytes(b"x"); os.utime(f, (100 + k, 100 + k))
S._prune_hires()
assert a.exists() and not (paths.WORK / "f00000000011_abc_10x10_hires.mp4").exists()
print("OK")
''', tmp_path, _env(tmp_path), timeout=400)


def test_guardar_en_la_carpeta_sin_sobrescribir(tmp_path):
    env = _env(tmp_path)
    (_out(tmp_path) / "Elegida").mkdir()
    _run(PRELUDE + '''
import errno
J = "e00000000031"
plan, rec = composed_job(J)
S.OUTPUT.mkdir(parents=True, exist_ok=True)
out = S.OUTPUT / f"{J}_abcdef.mp4"; out.write_bytes(os.urandom(4000))
sh = [S.OUTPUT / f"{J}_abcdef_short1.mp4", S.OUTPUT / f"{J}_abcdef_short2.mp4"]
for s in sh:
    s.write_bytes(os.urandom(500))
pick(str(TMP / "Elegida")); folder = S.pick_folder({})["path"]
ex = {"folder": folder, "name": "Promo", "res": "1080p", "quality": "alta"}
pr = Prog()
r = S._save_export(J, out, sh, ex, pr)
assert r["saved"] and r["path"] == str(Path(folder) / "Promo.mp4") and r["file_name"] == "Promo.mp4"
assert r["bytes"] == 4000 and r["how"] in ("clone", "link", "copy") and r["folder_label"] == EK.home_label(folder)
assert r["shorts"] == [str(Path(folder) / "Promo - Short 1.mp4"), str(Path(folder) / "Promo - Short 2.mp4")]
assert Path(r["path"]).read_bytes() == out.read_bytes()
assert any(c.get("stage") == "save" and "Guardando en «Elegida»" in c.get("message", "") for c in pr.calls)
r2 = S._save_export(J, out, sh, ex)
assert r2["file_name"] == "Promo (2).mp4" and r2["shorts"][0].endswith("Promo (2) - Short 1.mp4")
assert S.EXPORT_STORE.is_exported(r["path"]) and S.EXPORT_STORE.is_exported(r2["shorts"][1])
assert sorted(p.name for p in Path(folder).iterdir()) == ["Promo (2) - Short 1.mp4", "Promo (2) - Short 2.mp4",
    "Promo (2).mp4", "Promo - Short 1.mp4", "Promo - Short 2.mp4", "Promo.mp4"]
# borrar el export de DATA (se conservan 2 por proyecto) no borra el que se guardo
out.unlink()
assert Path(r["path"]).read_bytes()[:10] and Path(r["path"]).stat().st_size == 4000
out.write_bytes(os.urandom(4000))
# clips del resultado: ruta final, tamano y resolucion
clips = [{"name": "Video completo", "file": out.name, "W": 1080, "H": 1920, "duration": 3.0},
         {"name": "Short 1", "file": sh[0].name}, {"name": "Short 2", "file": sh[1].name}]
S._apply_saved(clips, r, ex)
assert clips[0]["path"] == r["path"] and clips[0]["saved"] and clips[0]["bytes"] == 4000
assert clips[0]["label"] == "1080p" and clips[1]["path"] == r["shorts"][0] and r["W"] == 1080
# sin espacio en la carpeta: el video queda en Reelfy y el mensaje lo dice
real = EK.place_file
def full(*a, **k):
    raise OSError(errno.ENOSPC, "No space left on device")
EK.place_file = full
r3 = S._save_export(J, out, [], ex)
assert not r3["saved"] and r3["save_error"].startswith("No hay espacio en «Elegida» para guardar el video")
EK.place_file = real
# carpeta que ya no se puede usar
(TMP / "Elegida").chmod(0o555)
r4 = S._save_export(J, out, [], ex)
(TMP / "Elegida").chmod(0o755)
assert not r4["saved"] and "No se puede escribir en la carpeta «Elegida»" in r4["save_error"]
# Guardar de nuevo (POST /export/save) el ultimo export del proyecto
jobstore.update(J, clips=[dict(clips[0], file_name="Promo.mp4")] + clips[1:])
r5 = S.export_save({"job": J})
assert r5["saved"] and r5["file_name"] == "Promo (3).mp4" and len(r5["shorts"]) == 2
assert jobstore.get(J)["clips"][0]["path"] == r5["path"]
r6 = S.export_save({"job": J, "file": out.name, "name": "Otro: nombre"})
assert r6["file_name"] == "Otro - nombre.mp4"
assert status_code(S.export_save, {"job": J, "file": "../../x.mp4"})[0] == 404
assert status_code(S.export_save, {"job": J, "folder": "/tmp"})[0] == 400
print("OK")
''', tmp_path, env)


def test_error_del_export_va_tal_cual_y_rendimiento_medido(tmp_path):
    _run(PRELUDE + '''
import threading
J = "e00000000041"
plan, rec = composed_job(J)
calls = []
def boom(jid, job):
    calls.append(jid)
    raise S.ExportError("No se pudo preparar el video en 4K (x). Puedes exportarlo en 1080p.", "recompose_failed", "1080p")
jobstore._handlers = {"render": boom}
jobstore.requeue(J, kind="render", params={"pro": True}, plan=plan)
stop = threading.Event()
th = threading.Thread(target=jobstore._worker_loop, args=(0, None, stop), daemon=True); th.start()
for _ in range(100):
    if jobstore.get(J)["state"] == "error":
        break
    time.sleep(0.05)
stop.set()
j = jobstore.get(J)
assert j["error"] == "No se pudo preparar el video en 4K (x). Puedes exportarlo en 1080p.", j["error"]
# rendimiento medido -> la estimacion pasa de "estimado" a "medido"
before = S.export_estimate(J, res="1080p", fmt="9:16", quality="alta", shorts=0, folder=None)
assert before["basis"] == "estimado"
S.OUTPUT.mkdir(parents=True, exist_ok=True)
o = S.OUTPUT / f"{J}_aaaaaa.mp4"; o.write_bytes(os.urandom(200000))
stats = {"n_frames": 90, "W": 1080, "H": 1920, "fps": 30, "t_base": 1.5, "cached_base": False, "shorts": []}
S._record_perf(stats, None, 2.4, 0.0, 0, o)
perf = S.EXPORT_STORE.load()["perf"]
assert abs(perf["render"][0] - 0.9 / (90 * 2.0736)) < 1e-4 and perf["size_ratio"][0] > 0
assert abs(perf["base"][0] - 1.5 / (90 * 2.0736)) < 1e-4
after = S.export_estimate(J, res="1080p", fmt="9:16", quality="alta", shorts=0, folder=None)
assert after["basis"] == "medido" and after["parts"]["render"] < before["parts"]["render"]
# la calidad del export vive en el entorno SOLO mientras dura (los procesos del compositor la heredan)
with S._quality_env("maxima", True):
    assert os.environ["REELFY_FINAL_BPP"] == "0.33" and os.environ["REELFY_FINAL_CRF"] == "15"
assert "REELFY_FINAL_BPP" not in os.environ
with S._quality_env("alta", True):
    assert "REELFY_FINAL_BPP" not in os.environ
print("OK")
''', tmp_path, _env(tmp_path))


def test_componer_guarda_el_registro_y_sin_espacio_en_el_render(tmp_path):
    """El handler `compose` (sin whisper: el analisis es falso) guarda el registro con los volumenes
    "auto" ya resueltos; un render que se queda sin disco termina con el mensaje en espanol."""
    env = _env(tmp_path)
    _run(PRELUDE + '''
import errno
ps = [src("5a0000000031", f=300), src("5a0000000032", f=600)]
mm = lambda k: {"slot": f"s{k}", "cand": "c1", "join": "line" if k else None}
spec = {"fps": 30, "overlays": [], "main": [
    item(p, 1.5 * k, 0.0, 1.5, vol="auto", montage=mm(k), words=[{"start": 0.1, "end": 1.2}])
    for k, p in enumerate(ps)]}
J = "e00000000051"
jobstore.create(J, kind="compose", ext=".mp4", params={"spec": spec})
def fake_analyze(video, gloss, n, on_step=None, **kw):
    d = C.probe(video)["dur"]
    return {"version": 1, "video": str(video), "duration": d, "dims": [360, 640], "phrases": [], "cuts": [],
            "highlights": []}
S.pipeline.analyze = fake_analyze
S._finish_analysis = lambda job_id, prog, plan, p, bar=None: jobstore.update(job_id, plan=plan, state="review",
                                                                              phase="review")
C.MAX_VERTICAL = (360, 640)                    # tope chico (en la app: 1620x2880)
S._compose(J, jobstore.get(J))
rec = jobstore.get_compose_spec(J)
assert rec and rec["native"] == [1440, 2560] and (rec["W"], rec["H"]) == (360, 640) and rec["t"] > 0
assert all(isinstance(it["vol"], float) and "words" not in it for it in rec["spec"]["main"])
assert len(rec["shots"]) == 2 and jobstore.get(J)["plan"]["shots"]
# el export lo usa sin reconstruir nada
o = S.export_options(J)
assert o["source"]["can_recompose"] and {r["id"]: r for r in o["formats"]["9:16"]["resolutions"]}["1440p"]["recompose"]
# render sin espacio -> ExportError no_space (mensaje en espanol, tarea lateral `export`)
plan = dict(jobstore.get(J)["plan"], version=2, fx={})
jobstore.update(J, plan=plan)
pick_dir = TMP / "Elegida"; pick_dir.mkdir()
pick(str(pick_dir)); folder = S.pick_folder({})["path"]
ex = S._export_params(J, plan, "9:16", {"res": "1080p", "folder": folder})
jobstore.requeue(J, kind="render", params={"pro": True, "fmt": "9:16", "shorts": False, "engine": {}, "export": ex},
                 plan=plan)
from motion import render as R
def lleno(*a, **k):
    raise RuntimeError("ffmpeg: Error writing trailer: No space left on device")
R.render_pro = lleno
try:
    S._render_pro(J, jobstore.get(J))
    raise SystemExit("debia fallar")
except S.ExportError as e:
    assert e.code == "no_space" and str(e) == S.NO_SPACE_MSG and e.fallback is None
side = jobstore.side_get(J, "export")
assert side["state"] == "error" and side["code"] == "no_space" and side["message"].startswith("No hay espacio")
assert list(pick_dir.iterdir()) == []
print("OK")
''', tmp_path, env, timeout=400)
