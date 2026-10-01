"""Regresiones de la revision del export (hoja "Exportar"): cada prueba fija un hallazgo.

- Un montaje compuesto antes de guardar su registro se reconstruye de plan.montage.timeline, que NO
  guarda clips silenciados, imagenes, transiciones ni stickers: se marca (no se recompone) o la
  verificacion despues de recomponer lo atrapa (nunca un 4K distinto de la edicion de 1080p).
- La carpeta recordada que no esta (disco desconectado) cae a la de siempre CON aviso y no se
  olvida.
- El rendimiento del video base de un export en 720p se mide sobre el base de 1080 que se armo.
- Cancelar durante "Preparando el video en 4K" mata el ffmpeg en curso.
- Un ensamble de antes del registro (ya exportado) no se presenta como "Tu video original es 1620p".
- Errores en espanol (sin el texto del sistema) y "Mostrar en..." con la app de la plataforma.
- Copias a medias (.reelfy-part) que dejo un Reelfy cerrado se barren.
- Montajes con clips de distinta resolucion: 4K si lo da la mayoria (avisando cuantos se escalan).
- Las fuentes de un registro solo se conservan si recomponer puede servir.

Mismo arnes que test_export_server.py (servidor en un proceso aparte, DATA temporal, selector y
Finder falsos). Videos sinteticos (lavfi); sin datos de clientes."""
from test_server_setup import _run
from test_export_server import PRELUDE, _env, _out

EXTRA = '''
import json, errno
def png(sid, color="red", size=200):
    p = S.SOURCES / f"{sid}.png"
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}x{size}",
                    "-frames:v", "1", str(p)], check=True)
    return p

def mm(k):
    return {"montage": {"slot": f"s{k}", "cand": "c1", "join": "line" if k else None}}

def spec_job(jid, main, overlays=(), montage=True, record=False, cap=(360, 640), exported=True):
    """Proyecto compuesto de un spec propio; `exported`: el export ya reemplazo params (sin
    params.spec) y, sin `record`, solo queda plan.montage.timeline."""
    spec = {"fps": 30, "overlays": list(overlays), "main": main}
    vid = S.INPUT / f"{jid}.mp4"
    info = {}
    C.compose(spec, str(vid), paths.WORK / f"cw_{jid}", info=info, max_canvas=cap)
    rec = C.spec_record(spec, info); rec["t"] = time.time()
    dur = C.probe(vid)["dur"]
    shots = C.plan_shots(info["shots"], dur)
    plan = {"version": 2, "video": str(vid), "duration": dur, "dims": [info["W"], info["H"]], "shots": shots,
            "fx": {}, "cuts": [], "phrases": [], "highlights": []}
    if montage:
        plan["montage"] = C.montage_plan(None, info["main"], shots)
    jobstore.create(jid, kind="compose", ext=".mp4", params={"spec": spec}, plan=plan)
    jobstore.update(jid, state="review", phase="review")
    if record:
        jobstore.set_compose_spec(jid, rec)
    if exported:
        jobstore.requeue(jid, kind="render", params={"pro": True}, plan=plan)
        jobstore.update(jid, state="review", phase="review")
    return plan, rec

def rows(jid, fmt="9:16"):
    return {x["id"]: x for x in S.export_options(jid)["formats"][fmt]["resolutions"]}

EX1440 = {"res": "1440p", "recompose": [1440, 2560], "W": 1440, "H": 2560}
'''


def test_montaje_reconstruido_con_detalles_perdidos_no_se_recompone(tmp_path):
    """Hallazgo mayor: la reconstruccion desde plan.montage.timeline perdia el silencio de un clip,
    los stickers, las imagenes y las transiciones, y el chequeo de tomas no lo notaba (el 4K salia
    con el clip silenciado sonando y sin el sticker)."""
    _run(PRELUDE + EXTRA + '''
ps = [src("5a0000000061"), src("5a0000000062", f=500)]
LOSSY = "Este montaje se armó con una versión anterior de Reelfy y no guardó todos sus detalles"
# 1) clip silenciado: la revision previa (audio de cada toma contra su clip) lo ve SIN recomponer
J = "e00000000061"
plan, _ = spec_job(J, [item(ps[0], 0.0, 0.0, 1.5, **mm(0)), item(ps[1], 1.5, 0.0, 1.5, mute=True, **mm(1))])
s1 = S._source_info(J, plan)
rec = jobstore.get_compose_spec(J)
assert rec["src"] == "backfill:montage" and rec["lossy"] == "tiene clips silenciados", rec.get("lossy")
assert not s1["can_recompose"] and s1["lossy"] == "tiene clips silenciados"
r = rows(J)
assert not r["1440p"]["enabled"] and r["1440p"]["reason"] == LOSSY + " (tiene clips silenciados): el máximo es 1080p"
assert r["1080p"]["enabled"] and S.export_options(J)["source"]["lossy"] == "tiene clips silenciados"
_orig = C.compose
def no_compose(*a, **k):
    raise AssertionError("no debe recomponer")
C.compose = no_compose
try:
    S._hires_video(J, plan, EX1440, Prog(), 30.0, time.time())
    raise SystemExit("debia fallar")
except S.ExportError as e:
    assert e.code == "recompose_failed" and e.fallback == "1080p" and "tiene clips silenciados" in str(e)
finally:
    C.compose = _orig
assert jobstore.compose_pins(json.dumps(jobstore.get_compose_spec(J))) == set()   # sin fuentes "para el 4K"

# 2) sticker: la linea de tiempo no lo guarda y la revision previa no lo ve; la verificacion
# despues de recomponer (cuadros de cada toma) si -> error que ofrece 1080p, sin cache, y la hoja
# ya no lo ofrece (no se hace esperar minutos otra vez)
J2 = "e00000000062"
stk = png("5a0000000063")
plan2, _ = spec_job(J2, [item(ps[0], 0.0, 0.0, 1.5, **mm(0)), item(ps[1], 1.5, 0.0, 1.5, **mm(1))],
                    overlays=[{"path": str(stk), "type": "image", "start": 0.0, "dur": 2.5, "pos": "top-right",
                               "size": 0.12}])
s2 = S._source_info(J2, plan2)
assert s2["can_recompose"] and s2["lossy"] is None and rows(J2)["1440p"]["recompose"]
pr = Prog()
try:
    S._hires_video(J2, plan2, EX1440, pr, 30.0, time.time())
    raise SystemExit("debia fallar")
except S.ExportError as e:
    assert e.code == "recompose_failed" and e.fallback == "1080p"
    assert str(e) == ("No se pudo preparar el video en 1440p: el video recompuesto no queda igual a tu "
                      "edición. Puedes exportarlo en 1080p."), str(e)
assert any("revisando" in (c.get("message") or "") for c in pr.calls)
assert not list(paths.WORK.glob(f"{J2}_*_hires.*")) and not list(paths.WORK.glob("*.part.mp4"))
vf = jobstore.get_compose_spec(J2)["verify_fail"]
assert vf["why"] == "no queda igual a tu edición" and vf["detail"].startswith("imagen de la toma 1 distinta")
assert not S._source_info(J2, plan2)["can_recompose"]
r = rows(J2)
assert not r["1440p"]["enabled"] and r["1440p"]["reason"] == LOSSY + " (no queda igual a tu edición): el máximo es 1080p"
C.compose = no_compose                                   # otra vez: falla al instante, sin recomponer
try:
    S._hires_video(J2, plan2, EX1440, Prog(), 30.0, time.time())
    raise SystemExit("debia fallar")
except S.ExportError as e:
    assert e.code == "recompose_failed" and "no queda igual a tu edición" in str(e)
finally:
    C.compose = _orig
# un registro de la composicion (no reconstruido) que no quedo igual: el motivo no habla de versiones
J6 = "e00000000066"
plan6, rec6 = composed_job(J6, sids=("5a0000000061", "5a0000000062"))
jobstore.set_compose_spec(J6, dict(rec6, verify_fail={"why": "no queda igual a tu edición", "detail": "x"}))
r = rows(J6)
assert not r["1440p"]["enabled"] and r["1440p"]["reason"] == "Este proyecto no se pudo recomponer igual a tu edición: el máximo es 1080p"

# 3) imagen en el montaje (la linea de tiempo no guarda su duracion) y 4) transicion
img = png("5a0000000064", "blue", 720)
J3 = "e00000000063"
plan3, _ = spec_job(J3, [item(ps[0], 0.0, 0.0, 1.5, **mm(0)),
                         {"path": str(img), "sid": img.stem, "type": "image", "name": img.name, "start": 1.5,
                          "layer": 0, "dur": 1.0, "fadeIn": 0.0, "fadeOut": 0.0, **mm(1)}])
assert S._source_info(J3, plan3)["lossy"] == "tiene imágenes"
J4 = "e00000000064"
plan4, _ = spec_job(J4, [item(ps[0], 0.0, 0.0, 1.8, **mm(0)),
                         item(ps[1], 1.3, 0.0, 1.8, trans={"type": "crossfade", "dur": 0.5}, **mm(1))])
assert S._source_info(J4, plan4)["lossy"] == "tiene transiciones o clips que no guardó"

# 5) el mismo montaje SIN nada de eso: se recompone, la verificacion pasa y queda en cache
J5 = "e00000000065"
plan5, _ = spec_job(J5, [item(ps[0], 0.0, 0.0, 1.5, **mm(0)), item(ps[1], 1.5, 0.0, 1.5, vol=0.6, **mm(1))])
assert S._source_info(J5, plan5)["can_recompose"]
out, how, _ = S._hires_video(J5, plan5, EX1440, Prog(), 30.0, time.time())
assert how == "new" and json.loads(Path(str(out)[:-4] + ".json").read_text())["verified"] is True
assert "verify_fail" not in jobstore.get_compose_spec(J5)
# y la verificacion directa: el video contra si mismo pasa; contra otro con un clip silenciado no
assert S._verify_hires(plan5["video"], str(out), [[s["start"], s["end"]] for s in plan5["shots"]]) is None
print("OK")
''', tmp_path, _env(tmp_path), timeout=500)


def test_carpeta_recordada_desconectada_avisa_y_no_se_olvida(tmp_path):
    """Hallazgo: con la carpeta recordada en un disco desconectado la hoja pasaba en silencio a
    ~/Movies/Reelfy y el export sobrescribia la recordada con la de siempre."""
    env = _env(tmp_path)
    usb = _out(tmp_path) / "USB" / "Entregas"
    usb.mkdir(parents=True)
    _run(PRELUDE + EXTRA + '''
import shutil as sh
J = "e00000000071"
plan, rec = composed_job(J)
usb = TMP / "USB" / "Entregas"
pick(str(usb)); got = S.pick_folder({})
assert got["path"] == str(usb.resolve()) and S.export_options(J)["folder"]["path"] == got["path"]
dflt = str(Path(os.environ["REELFY_EXPORT_DIR"]).resolve())
sh.rmtree(TMP / "USB")                                   # se desconecto el disco
f = S.export_options(J)["folder"]
assert f["path"] == dflt and f["default"] and f["error"] is None
assert f["fallback_from"]["path"] == got["path"] and f["fallback_from"]["name"] == "Entregas"
assert f["notice"] == "Tu carpeta «Entregas» no está disponible (¿disco desconectado?); se usará " + f["label"] + ".", f["notice"]
assert S.export_settings()["folder"]["notice"] == f["notice"]
# exportar a la de siempre (lo que la hoja manda) NO olvida la recordada
ex = S._export_params(J, plan, "9:16", {"res": "1080p", "folder": f["path"]})
assert ex["folder"] == dflt
assert S.EXPORT_STORE.load()["last"]["folder"] == got["path"]
# vuelve el disco: la hoja vuelve a proponer la recordada, sin aviso
usb.mkdir(parents=True)
f = S.export_options(J)["folder"]
assert f["path"] == got["path"] and not f.get("notice") and not f.get("fallback_from")
# elegir a proposito otra carpeta SI la recuerda
(TMP / "Otra").mkdir()
pick(str(TMP / "Otra")); o = S.pick_folder({})
S._export_params(J, plan, "9:16", {"res": "1080p", "folder": o["path"]})
assert S.EXPORT_STORE.load()["last"]["folder"] == o["path"]
# una carpeta que existe pero ya no deja escribir: aviso con ese motivo
(TMP / "Otra").chmod(0o555)
try:
    f = S.export_options(J)["folder"]
    assert f["path"] == dflt and f["notice"].startswith("Tu carpeta «Otra» no se puede escribir en ella")
finally:
    (TMP / "Otra").chmod(0o755)
print("OK")
''', tmp_path, env)


def test_rendimiento_del_base_en_720p_y_export_con_render_falso(tmp_path):
    """Hallazgo: un export en 720p dividia el tiempo del base (que se arma a 1080x1920) entre el
    area de la salida (720x1280): la muestra 'base' salia 2.25x alta e inflaba las estimaciones.
    De paso, el export completo con un render falso: 720p compone sobre el base de 1080, guarda en
    la carpeta y mide."""
    env = _env(tmp_path)
    (_out(tmp_path) / "Elegida").mkdir()
    _run(PRELUDE + EXTRA + '''
J = "e00000000081"
plan, rec = composed_job(J)
S.OUTPUT.mkdir(parents=True, exist_ok=True)
o = S.OUTPUT / f"{J}_aaaaaa.mp4"; o.write_bytes(os.urandom(100000))
st = {"n_frames": 300, "W": 720, "H": 1280, "fps": 30, "t_base": 10.0, "cached_base": False, "shorts": []}
S._record_perf(st, {"res": "720p"}, 30.0, 0.0, 0, o, base_wh=(1080, 1920))
b = S.EXPORT_STORE.load()["perf"]["base"][0]
assert abs(b - 10.0 / (300 * 1080 * 1920 / 1e6)) < 1e-5, b          # 0.01608, no 0.03617
S._record_perf(st, {"res": "1080p"}, 30.0, 0.0, 0, o)                  # sin base: el de la salida
assert abs(S.EXPORT_STORE.load()["perf"]["base"][0] - 10.0 / (300 * 0.9216)) < 1e-5
# el handler pasa el base que de verdad se armo (opts del motor)
pick(str(TMP / "Elegida")); folder = S.pick_folder({})["path"]
ex = S._export_params(J, plan, "9:16", {"res": "720p", "folder": folder, "name": "Promo 720", "shorts": False})
jobstore.requeue(J, kind="render", params={"pro": True, "fmt": "9:16", "shorts": False, "engine": {}, "export": ex},
                 plan=plan)
from motion import render as R
seen = {}
def fake_render(rplan, out, opts):
    seen.update(W=opts.get("W"), H=opts.get("H"), base_W=opts.get("base_W"), base_H=opts.get("base_H"))
    Path(out).write_bytes(os.urandom(50000))
    return {"n_frames": 90, "W": opts["W"], "H": opts["H"], "fps": 30, "t_base": 4.0, "cached_base": False,
            "duration": 3.0, "shorts": []}
R.render_pro = fake_render
S._claim_cache = lambda *a, **k: None
before = len(S.EXPORT_STORE.load()["perf"]["base"])
S._render_pro(J, jobstore.get(J))
assert seen == {"W": 720, "H": 1280, "base_W": 1080, "base_H": 1920}, seen
perf = S.EXPORT_STORE.load()["perf"]
assert len(perf["base"]) == before + 1 and abs(perf["base"][0] - 4.0 / (90 * 2.0736)) < 1e-5, perf["base"]
c0 = jobstore.get(J)["clips"][0]
assert c0["saved"] and c0["path"] == str(Path(folder) / "Promo 720.mp4") and c0["label"] == "720p"
assert jobstore.side_get(J, "export")["state"] == "done"
print("OK")
''', tmp_path, env)


def test_cancelar_mientras_se_recompone(tmp_path):
    """Hallazgo: cancelar durante "Preparando el video en 4K" solo surtia efecto al terminar el
    segmento en curso (minutos con una toma larga en 4K)."""
    _run(PRELUDE + EXTRA + '''
import threading, subprocess as sp
J = "e00000000091"
long = src("5a0000000091", 1440, 2560, d=24.0)
plan, rec = spec_job(J, [item(long, 0.0, 0.5, 23.5, vol=0.8)], montage=False, record=True, exported=False)
flag = {"c": False, "t": None}
class CProg(Prog):
    class cancel:
        @staticmethod
        def canceled():
            return flag["c"]
        @staticmethod
        def check():
            if flag["c"]:
                raise S.JobCanceled()
def go():
    flag.update(c=True, t=time.time())
threading.Timer(0.8, go).start()
try:
    S._hires_video(J, plan, EX1440, CProg(), 30.0, time.time())
    raise SystemExit("debia cancelarse")
except S.JobCanceled:
    pass
late = time.time() - flag["t"]
assert late < 0.8, late
assert not sp.run(["pgrep", "-P", str(os.getpid()), "ffmpeg"], capture_output=True, text=True).stdout.strip()
assert not list(paths.WORK.glob("*.part.mp4")) and not (paths.WORK / f"compose_{J}_hires").exists()
assert not list(paths.WORK.glob(f"{J}_*_hires.mp4"))
print("OK")
''', tmp_path, _env(tmp_path), timeout=300)


def test_ensamble_de_antes_del_registro_no_es_un_video_original(tmp_path):
    """Hallazgo: un ensamble a mano compuesto antes del registro y ya exportado (sin registro, sin
    params.spec, sin montaje) se trataba como un solo video: "Tu video original es 1620p"."""
    _run(PRELUDE + EXTRA + '''
ps = [src("5a00000000a1"), src("5a00000000a2", f=500)]
J = "e000000000a1"
plan, _ = spec_job(J, [item(ps[0], 0.0, 0.0, 1.5), item(ps[1], 1.5, 0.0, 1.5)], montage=False)
assert jobstore.get_compose_spec(J) is None and "spec" not in jobstore.get(J)["params"]
o = S.export_options(J)
assert o["source"]["kind"] == "compose" and o["source"]["legacy"] and not o["source"]["can_recompose"]
r = {x["id"]: x for x in o["formats"]["9:16"]["resolutions"]}
assert not r["1440p"]["enabled"] and not r["4k"]["enabled"]
assert r["4k"]["reason"] == "Este proyecto se armó antes de guardar sus clips originales: el máximo es 1080p"
assert "Tu video original" not in r["1440p"]["reason"]
assert jobstore.get_compose_spec(J) is None                         # no se inventa un registro
# al tope del compositor (1620x2880): la etiqueta es un nivel conocido, nunca "1620p"
J2 = "e000000000a2"
vid = S.INPUT / f"{J2}.mp4"
sp_ = subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1620x2880:r=30:d=1",
                      "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(vid)], check=True)
jobstore.create(J2, kind="render", ext=".mp4", params={"pro": True},
                plan={"version": 2, "video": str(vid), "duration": 1.0, "fx": {}, "cuts": [], "phrases": [],
                      "shots": [{"start": 0.0, "end": 1.0, "src": "clip.mov"}]})
jobstore.update(J2, state="review", phase="review")
o2 = S.export_options(J2)
assert o2["source"]["legacy"] and o2["source"]["label"] == "1440p+"
r2 = {x["id"]: x for x in o2["formats"]["9:16"]["resolutions"]}
assert r2["1440p"]["enabled"] and not r2["1440p"]["recompose"]       # el combinado alcanza para 1440p
assert r2["4k"]["reason"] == "Este proyecto se armó antes de guardar sus clips originales: el máximo es 1440p"
assert "1620p" not in json.dumps(o2, ensure_ascii=False)
# un video subido (tomas del director, todas del mismo archivo) sigue siendo "tu video original"
J3 = "e000000000a3"
v3 = src("5a00000000a3", 720, 1280)
import shutil as sh
sh.copyfile(v3, S.INPUT / f"{J3}.mp4")
jobstore.create(J3, kind="analyze", ext=".mp4", plan={"version": 2, "video": str(S.INPUT / f"{J3}.mp4"),
                "duration": 2.0, "fx": {}, "cuts": [], "phrases": [],
                "shots": [{"start": 0.0, "end": 1.0, "src": "mi.mov"}, {"start": 1.0, "end": 2.0, "src": "mi.mov"}]})
jobstore.update(J3, state="review", phase="review")
o3 = S.export_options(J3)
assert o3["source"]["kind"] == "single" and not o3["source"]["legacy"]
assert {x["id"]: x for x in o3["formats"]["9:16"]["resolutions"]}["4k"]["reason"] == \\
    "Tu video original es 720p; 4K no agregaría calidad"
print("OK")
''', tmp_path, _env(tmp_path))


def test_errores_en_espanol_y_mostrar_por_plataforma(tmp_path):
    """Hallazgo: el texto del sistema en ingles ("Input/output error") iba dentro del mensaje y
    /reveal decia "Finder" en Windows y Linux."""
    env = _env(tmp_path)
    (_out(tmp_path) / "USB").mkdir()
    _run(PRELUDE + EXTRA + '''
J = "e000000000b1"
plan, rec = composed_job(J)
S.OUTPUT.mkdir(parents=True, exist_ok=True)
out = S.OUTPUT / f"{J}_abcdef.mp4"; out.write_bytes(os.urandom(4000))
sh_ = S.OUTPUT / f"{J}_abcdef_short1.mp4"; sh_.write_bytes(os.urandom(500))
pick(str(TMP / "USB")); folder = S.pick_folder({})["path"]
ex = {"folder": folder, "name": "Promo", "res": "1080p", "quality": "alta"}
real = EK.place_file
def eio(*a, **k):
    raise OSError(errno.EIO, "Input/output error", folder)
EK.place_file = eio
r = S._save_export(J, out, [], ex)
assert not r["saved"] and r["save_error"] == ("Se perdió la conexión con «USB» (¿se desconectó el disco?). Tu video "
                                              "quedó en Reelfy: usa «Guardar de nuevo».")
for en, sub in ((errno.EROFS, "«USB» es de solo lectura"), (errno.ENAMETOOLONG, "demasiado largo"),
                (errno.EACCES, "no tiene permiso")):
    def bad(*a, en=en, **k):
        raise OSError(en, os.strerror(en), folder)
    EK.place_file = bad
    r = S._save_export(J, out, [], ex)
    assert sub in r["save_error"] and os.strerror(en) not in r["save_error"], r["save_error"]
# un short que falla: tambien en espanol
calls = []
def short_fails(src_, *a, **k):
    calls.append(src_)
    if len(calls) > 1:
        raise OSError(errno.EIO, "Input/output error")
    return real(src_, *a, **k)
EK.place_file = short_fails
r = S._save_export(J, out, [sh_], ex)
assert r["saved"] and r["shorts"] == [None]
assert r["save_error"] == "Un short no se pudo guardar: Se perdió la conexión con «USB» (¿se desconectó el disco?)."
assert "Input/output" not in r["save_error"]
EK.place_file = real
# /reveal que no puede abrir: el nombre de la app de ESTA plataforma
f = Path(r["path"])
EK.run_reveal = lambda p, action="reveal": False
c, d = status_code(S.reveal, {"path": str(f)})
assert c == 501 and d == EK.reveal_fail_msg("reveal")
c, d = status_code(S.reveal, {"path": str(f), "action": "open"})
assert c == 501 and d == "No se pudo abrir el video en este equipo."
import sys as _s
if _s.platform != "darwin":
    assert "Finder" not in d
print("OK")
''', tmp_path, env)


def test_barrer_copias_a_medias_en_las_carpetas_de_destino(tmp_path):
    """Hallazgo: una copia a otro volumen interrumpida dejaba ".<nombre>.<pid>.<hilo>.reelfy-part"
    (oculto, de GB) en la carpeta del usuario para siempre."""
    env = _env(tmp_path)
    (_out(tmp_path) / "USB").mkdir()
    _run(PRELUDE + EXTRA + '''
_real_gc = S.disk_gc
S.disk_gc = lambda reason="": {}                     # la limpieza de arranque (a los 5 s) no se cruza
J = "e000000000c1"
plan, rec = composed_job(J)
pick(str(TMP / "USB")); folder = Path(S.pick_folder({})["path"])
def part(name, pid=999999, age=0):
    p = folder / f".{name}.{pid}.140000{EK.PART_SUFFIX}"
    p.write_bytes(b"x" * 1000)
    if age:
        os.utime(p, (time.time() - age,) * 2)
    return p
a = part("Promo - 2026-10-01")                        # Reelfy que ya no existe: se borra
live = part("Copiando", pid=os.getpid())             # la copia de ESTE proceso: se queda
keep = folder / "Promo.mp4"; keep.write_bytes(b"y")
S.export_options(J)                                  # abrir la hoja barre la carpeta elegida
assert not a.exists() and live.exists() and keep.exists()
b = part("Otro")
pick(str(folder)); S.pick_folder({})                 # elegirla con el selector tambien
assert not b.exists()
c = part("Viejo", pid=os.getpid(), age=EK.PART_MAX_AGE + 60)
d = Path(os.environ["REELFY_EXPORT_DIR"]); d.mkdir(parents=True, exist_ok=True)
e = d / f".X.999999.1{EK.PART_SUFFIX}"; e.write_bytes(b"z")
res = _real_gc("arranque")                           # al arrancar: todas las carpetas conocidas
assert not c.exists() and not e.exists() and live.exists() and res["parts"] >= 1001
print("OK")
''', tmp_path, env)


def test_montaje_con_clips_de_distinta_resolucion(tmp_path):
    """Hallazgo: el 'nativo' era el del primer clip. Con clips 1440p y 720p: si el primero era de
    720p se escondia 1440p aunque casi todo fuera 1440p; si era de 1440p, los de 720p se escalaban
    sin avisar."""
    _run(PRELUDE + EXTRA + '''
low = src("5a00000000d1", 720, 1280, d=2.0, f=600)
hi1, hi2 = src("5a00000000d2", d=2.0), src("5a00000000d3", d=2.0, f=450)
# la mayoria en 1440p (el de 720p va PRIMERO y es corto): 1440p se ofrece y avisa
J = "e000000000d1"
plan, rec = spec_job(J, [item(low, 0.0, 0.0, 0.6), item(hi1, 0.6, 0.0, 1.8), item(hi2, 2.4, 0.0, 1.8)],
                     montage=False, record=True, exported=False, cap=(360, 640))
assert rec["native"] == [1440, 2560] and [(c["w"], c["h"]) for c in rec["clips"]] == [(720, 1280), (1440, 2560), (1440, 2560)]
o = S.export_options(J)
assert o["source"]["w"] == 1440 and o["source"]["label"] == "1440p"
r = {x["id"]: x for x in o["formats"]["9:16"]["resolutions"]}
assert r["1440p"]["enabled"] and r["1440p"]["recompose"] and r["1440p"]["warn"] == "1 de 3 clips es 720p: se verá escalado en 1440p"
e = S.export_estimate(J, res="1440p", fmt="9:16", quality="alta", shorts=0, folder=None)
assert e["warn"] == r["1440p"]["warn"] and e["max_res"] == "1440p"
# recompone al lienzo EXACTO de 1440p aunque el primer clip sea de 720p
out, how, _ = S._hires_video(J, plan, EX1440, Prog(), 30.0, time.time())
i = C.probe(out)
assert how == "new" and (i["w"], i["h"]) == (1440, 2560)
# la mayoria en 720p (el de 1440p va primero): 1440p NO, con el motivo
J2 = "e000000000d2"
plan2, rec2 = spec_job(J2, [item(hi1, 0.0, 0.0, 0.5), item(low, 0.5, 0.0, 1.9), item(low, 2.4, 0.0, 1.9)],
                       montage=False, record=True, exported=False, cap=(360, 640))
r2 = rows(J2)
assert not r2["1440p"]["enabled"] and r2["1440p"]["reason"] == "La mayoría de tus clips son 720p; 1440p no agregaría calidad"
assert r2["1080p"]["enabled"] and r2["720p"]["enabled"]
# un registro de antes del detalle por clip: se calcula una vez y se guarda
old = dict(jobstore.get_compose_spec(J)); old.pop("clips"); old["native"] = [720, 1280]
jobstore.set_compose_spec(J, old)
s = S._source_info(J, plan)
assert s["native"] == [1440, 2560] and len(jobstore.get_compose_spec(J)["clips"]) == 3
print("OK")
''', tmp_path, _env(tmp_path), timeout=400)


def test_fuentes_del_registro_solo_si_recomponer_sirve(tmp_path):
    """Hallazgo: el registro conservaba (fuera de la limpieza) todos los clips de cualquier ensamble,
    tambien de uno de clips 1080p que quedo en 1080p, donde recomponer no agrega nada."""
    _run(PRELUDE + EXTRA + '''
a, b = src("5a00000000e1", 360, 640), src("5a00000000e2", 360, 640, f=500)
J = "e000000000e1"
plan, rec = spec_job(J, [item(a, 0.0, 0.0, 1.5), item(b, 1.5, 0.0, 1.5)], montage=False, record=True,
                     exported=True, cap=(360, 640))
assert rec["native"] == [360, 640] and (rec["W"], rec["H"]) == (360, 640)
raw = json.dumps(jobstore.get_compose_spec(J))
assert jobstore.compose_pins(raw) == set()
assert J not in jobstore.sids_by_job() and not ({"5a00000000e1", "5a00000000e2"} & jobstore.referenced_sids())
# las 1440p de otro ensamble (combinado con tope 360x640) SI se conservan
J2 = "e000000000e2"
plan2, rec2 = composed_job(J2, sids=("5a00000000e3", "5a00000000e4"))
assert {"5a00000000e3", "5a00000000e4"} <= jobstore.referenced_sids()
assert jobstore.compose_pins(json.dumps(dict(rec2, lossy="tiene imágenes"))) == set()
assert jobstore.compose_pins(json.dumps(dict(rec2, native=None))) == {"5a00000000e3", "5a00000000e4"}
old = time.time() - 3 * 86400
for p in S.SOURCES.iterdir():
    os.utime(p, (old, old))
S._gc_sources()
assert not a.exists() and not b.exists()                         # tras exportar se pueden borrar
assert (S.SOURCES / "5a00000000e3.mp4").exists()                 # sirven para exportar en 1440p
print("OK")
''', tmp_path, _env(tmp_path))
