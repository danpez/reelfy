"""app/server.py: foto y logo AUTOMATICOS de los enlaces del perfil (motion/linkmedia.py).

Si el perfil menciona su Instagram o su pagina de PaseoClub, el servidor baja en SEGUNDO PLANO la
foto de ese lugar (og:image; en Instagram la foto de perfil) y el logo del sitio, los guarda como
medios propios (input/sources), los pone en los perfiles guardados (media_src 'auto') y en los
proyectos que usan ese enlace sin mover nada (plan_rev sube; /status lleva `profile_media`).
Ninguna peticion espera a la red; lo del usuario (media_src 'user') y lo quitado (media_off)
nunca se toca; REELFY_LINK_MEDIA=0 lo apaga y bajo pytest va apagado salvo que se pida.

Cada prueba importa el servidor en un proceso aparte con DATA temporal (como test_server_sym). La
"red" es un servidor HTTP LOCAL: las urls de paseo.club / instagram.com se reescriben a el y
cualquier otra falla como sin red. Solo datos FICTICIOS (paginas, usuarios e imagenes sinteticas).
"""
from test_server_setup import _run

ON = {"REELFY_LINK_MEDIA": "1"}

PRELUDE = r'''
import http.server, copy
sys.path.insert(0, "tests")
import cv2, numpy as np
import server as S, jobstore
from motion import director as D, profile as PR
import test_profile_director as TP
S._kick_src_proxy = lambda *a, **k: None
S._kick_analysis = lambda *a, **k: None
S.LM_POLL_S = 0.05

def png(w, h, c):
    a = np.zeros((h, w, 3), np.uint8)
    a[:] = c[::-1]
    return cv2.imencode(".png", a)[1].tobytes()

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
       b'<rect width="100" height="100" rx="20" fill="#f5efeb"/>'
       b'<circle cx="50" cy="50" r="30" fill="none" stroke="#08794d" stroke-width="10"/></svg>')
PAGE = b"""<html><head><meta property="og:image" content="/img/negocio.png?v=1"/>
<link rel="icon" href="/icon.svg" type="image/svg+xml"/></head><body>negocio ficticio</body></html>"""
IG = b"""<html><head><meta property="og:image" content="https://instagram.com/pp/perfil.jpg"/></head></html>"""
FILES = {"/pc/business/negocio-demo": (PAGE, "text/html"),
         "/pc/img/negocio.png": (png(400, 300, (200, 50, 50)), "image/png"),
         "/pc/icon.svg": (SVG, "image/svg+xml"),
         "/ig/perfil.demo": (IG, "text/html"),
         "/ig/pp/perfil.jpg": (cv2.imencode(".jpg", np.full((100, 100, 3), 90, np.uint8))[1].tobytes(), "image/jpeg")}
HITS = {}
DELAY = {"s": 0.0}

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        HITS[path] = HITS.get(path, 0) + 1
        if path not in FILES:
            self.send_response(404); self.end_headers(); return
        body, ct = FILES[path]
        if ct == "text/html":
            time.sleep(DELAY["s"])
        self.send_response(200); self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a):
        pass

SRV = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=SRV.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{SRV.server_address[1]}"
ROUTES = {"https://paseo.club/": BASE + "/pc/", "https://instagram.com/": BASE + "/ig/"}
REAL = S._lm_http()
from motion import linkmedia as _LM  # noqa: E402
_LM.ALLOW_PRIVATE = True     # el servidor de prueba vive en 127.0.0.1

def local_get(u, limit, timeout):
    for a, b in ROUTES.items():
        if u.startswith(a):
            u = b + u[len(a):]
    if not u.startswith(BASE):
        raise OSError("sin red en la prueba")
    return REAL(u, limit, timeout)

S._lm_http = lambda: local_get
PC_URL = "https://paseo.club/business/negocio-demo"
IG_URL = "https://instagram.com/perfil.demo"
TEXT = ("Ella es la Dra. Ana Demo, es odontologa, su instagram es " + IG_URL + " y agenda en " + PC_URL)
LINES = ["Si te duele la muela cuando tomas algo frio, esto te interesa.",
         "Una resina bien hecha protege tu diente y se ve natural.",
         "Checa mis horarios disponibles en el link aqui abajo.",
         "La limpieza profunda quita el sarro y deja tus dientes brillantes.",
         "Y no te preocupes, no te voy a juzgar por tu cepillo viejo."]

def idle():
    return not any(t.name.startswith("reelfy-linkmedia") and t.is_alive() for t in threading.enumerate())

def wait(cond, t=30):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond():
            return True
        time.sleep(0.05)
    raise AssertionError("timeout")

def link(prof, plat):
    return next(l for l in prof["links"] if l["platform"] == plat)

def files_ok(m):
    return all((S.SOURCES / f"{m['sid']}{x}").exists() for x in (".png", "_meta.json", "_t.jpg"))

def new_job(plan, state="review"):
    jid = "l" + os.urandom(5).hex()
    now = time.time()
    cx = jobstore._cx()
    cx.execute("INSERT INTO jobs (id, kind, state, phase, pct, message, ext, params, plan, created, "
               "updated, plan_rev, plan_hash) VALUES (?, 'compose', ?, ?, 100, '', '.mp4', '{}', ?, ?, ?, 1, ?)",
               (jid, state, state, json.dumps(plan), now, now, jobstore.plan_hash(plan)))
    cx.commit()
    return jid

def directed(prof):
    p = TP._lines_plan(LINES, broll_end=2.6)
    p["video"] = "v.mp4"
    p["profile"] = prof
    return S._sym(D.direct(p, use_llm=False))

def geo(plan):
    return [(g["id"], g.get("x"), g.get("y"), g.get("size"), g.get("width"), g["start"], g["end"])
            for g in plan["fx"]["graphics"]]
'''


def test_parse_and_save_get_photo_and_logo_in_the_background(tmp_path):
    """/profile/parse no espera a la red (media_status 'fetching'); al terminar, el perfil guardado
    trae la foto de la pagina (og:image) y el logo (icono SVG a 512 px) de PaseoClub y la foto de
    perfil de Instagram (sin logo: icono propio), como medios propios con miniatura y meta. Un
    perfil guardado ANTES de que termine la descarga tambien se actualiza solo."""
    code = PRELUDE + r'''
assert S._lm_enabled()
DELAY["s"] = 1.2
t0 = time.time()
r = S.profile_parse({"text": TEXT, "use_llm": False})
assert time.time() - t0 < 1.0, time.time() - t0              # la red nunca bloquea la peticion
assert r["profile"]["media_status"] == "fetching"
assert "photo" not in link(r["profile"], "paseoclub")
sv = S.profiles_save({"profile": r["profile"]})                 # se guarda mientras baja
pid = sv["id"]
assert sv["profile"]["media_status"] == "fetching" and "media_status" not in json.dumps(S._profiles_load())
wait(idle)
saved = next(x for x in S.profiles_list()["profiles"] if x["id"] == pid)
assert saved["media_status"] == "done"
pc, ig = link(saved, "paseoclub"), link(saved, "instagram")
assert pc["photo"]["w"] == 400 and pc["photo"]["h"] == 300 and pc["logo"]["w"] == 512
assert pc["media_src"] == {"photo": "auto", "logo": "auto"} and pc["media_for"] == PC_URL
assert ig["photo"]["w"] == 100 and "logo" not in ig and ig["media_src"] == {"photo": "auto"}
assert files_ok(pc["photo"]) and files_ok(pc["logo"]) and files_ok(ig["photo"])
assert all(x.get("updated") for x in S._profiles_load())
# la segunda vez sale de la cache: sin red y con todo desde la primera respuesta
n = dict(HITS)
r2 = S.profile_parse({"text": TEXT, "use_llm": False})
assert r2["profile"]["media_status"] == "done" and HITS == n
assert link(r2["profile"], "paseoclub")["photo"]["sid"] == pc["photo"]["sid"]
# GET /profiles, POST /profiles y /settings lo anuncian
assert "profile_media" in S.get_settings()["features"]
# refrescar: vuelve a pedir la pagina (lo del usuario no cuenta)
rr = S.profile_media_refresh({"profile_id": pid})
assert rr["urls"] and set(rr["urls"]) == {PC_URL, IG_URL} and rr["enabled"]
wait(idle)
assert HITS["/pc/business/negocio-demo"] == n["/pc/business/negocio-demo"] + 1
# la limpieza de fuentes no borra la foto/logo de perfiles guardados ni de la cache
S.SOURCE_AGE_S = 0
old = S.SOURCES / "vieja_sin_uso.png"
old.write_bytes(b"x")
os.utime(old, (time.time() - 10, time.time() - 10))
S._gc_sources(now=time.time() + 5)
assert not old.exists() and files_ok(pc["photo"]) and files_ok(pc["logo"]) and files_ok(ig["photo"])
print("OK")
'''
    _run(code, tmp_path, env_extra=ON)


def test_project_gets_the_media_without_moving_anything(tmp_path):
    """PUT /profile con un perfil cuyas paginas aun no bajan: responde al instante; al terminar la
    descarga el proyecto recibe foto (Seguir con la de Instagram; pastilla con la de PaseoClub +
    logo; horarios 'En PaseoClub' con el logo) SIN moverse nada (misma geometria, simetrico),
    plan_rev sube y /status lo anuncia. El autoguardado del Studio con el plan de antes gana (sus
    cambios se quedan) y lleva la foto. Un proyecto ocupado se espera."""
    code = PRELUDE + r'''
import test_director_symmetry as TS
prof = PR.parse_profile_text(TEXT, use_llm=False)["profile"]
plan = directed(prof)
jid = new_job(plan)
busy = new_job(plan, state="running")                      # exportando: se espera a que termine
DELAY["s"] = 0.8
t0 = time.time()
r = S.profile_put(jid, {"profile": prof})
assert time.time() - t0 < 5 and r["profile"]["media_status"] == "fetching"
rev1 = r["plan_rev"]
before = r["plan"]
assert all("media" not in g for g in before["fx"]["graphics"] if g["kind"] == "cta")
wait(lambda: jobstore.get_plan(jid)[1] > rev1)
p2, rev2 = jobstore.get_plan(jid)
pc, ig = link(p2["profile"], "paseoclub"), link(p2["profile"], "instagram")
fol = [g for g in p2["fx"]["graphics"] if g["kind"] == "cta" and g.get("variant") == "follow"]
lnk = [g for g in p2["fx"]["graphics"] if g["kind"] == "cta" and g.get("variant") == "link_below"]
sch = [g for g in p2["fx"]["graphics"] if g["kind"] == "card" and g.get("variant") == "schedule"]
assert fol and all(f["avatar"] == "image" and f["media"]["sid"] == ig["photo"]["sid"] for f in fol)
assert lnk and all(l["media"]["sid"] == pc["photo"]["sid"] and l["logo"]["sid"] == pc["logo"]["sid"] for l in lnk)
assert sch and sch[0]["platform"] == "paseoclub" and sch[0]["logo"]["sid"] == pc["logo"]["sid"]
assert geo(p2) == geo(before) and TS._violations(p2) == []
st = json.loads(S.status(jid, plan=0).body)
assert st["profile_media"]["state"] == "applied" and st["profile_media"]["plan_rev"] == rev2
assert st["profile_media"]["from_rev"] == rev1 and st["plan_rev"] == rev2
assert S.profile_get(jid)["profile"]["media_status"] == "done"
# el cuadro / la vista previa / el export con el plan de ANTES ya salen con la foto (sin moverse)
lp = S._lm_plan(before)
assert geo(lp) == geo(before) and json.dumps(lp["fx"], sort_keys=True) == json.dumps(p2["fx"], sort_keys=True)
assert S._lm_plan(p2) is p2
# el Studio autoguarda su plan de ANTES (rev1) con un cambio: gana y lleva la foto
edit = copy.deepcopy(before)
edit["fx"]["text"][0]["text"] = "TEXTO EDITADO"
res = S.put_plan(jid, {"plan": edit, "base_rev": rev1})
assert res["ok"] and res.get("profile_applied") and res["plan_rev"] == rev2 + 1, res
p3, rev3 = jobstore.get_plan(jid)
assert p3["fx"]["text"][0]["text"] == "TEXTO EDITADO"
assert [f["media"]["sid"] for f in p3["fx"]["graphics"] if f["kind"] == "cta" and f.get("variant") == "follow"]
assert json.loads(S.status(jid, plan=0).body)["profile_media"]["state"] == "merged"
# un autoguardado normal despues no re-aplica nada
same = S.put_plan(jid, {"plan": p3, "base_rev": rev3})
assert same["ok"] and not same.get("profile_applied") and same["plan_rev"] == rev3
# un proyecto viejo con el mismo perfil (sin foto) la recibe al ABRIRLO (ya esta en la cache)
old = new_job(plan)
g = S.get_plan(old)
assert g["plan_rev"] == 2 and geo(g["plan"]) == geo(plan)
assert all(f["media"]["sid"] == ig["photo"]["sid"] for f in g["plan"]["fx"]["graphics"]
           if f["kind"] == "cta" and f.get("variant") == "follow")
assert S.get_plan(old)["plan_rev"] == 2                        # ya al dia: no sube otra vez
# 'Regenerar' un elemento y re-dirigir con otro estilo: siguen con la foto/logo
fid = [f for f in p3["fx"]["graphics"] if f["kind"] == "cta" and f.get("variant") == "follow"][-1]["id"]
rg = S.direct(jid, {"only_ids": [fid], "seed": 5})["plan"]
f = next(g for g in rg["fx"]["graphics"] if g["id"] == fid)
assert f["media"]["sid"] == ig["photo"]["sid"] and f["avatar"] in ("image", "initial")
rd = S.direct(jid, {"style": "elegante"})["plan"]
assert all(l["media"]["sid"] == pc["photo"]["sid"] for l in rd["fx"]["graphics"]
           if l["kind"] == "cta" and l.get("variant") == "link_below")
assert not [e for e in rd["fx"]["effects"] if e["kind"] in ("bg_replace", "color_pop")]
# el proyecto ocupado se actualiza al quedar libre
assert S._lm_apply_job(busy, {PC_URL, IG_URL}) == "busy"
assert "media" not in json.dumps(jobstore.get_plan(busy)[0]["fx"]["graphics"])
jobstore.update(busy, state="review")
wait(lambda: jobstore.get_plan(busy)[1] > 1)
assert '"logo"' in json.dumps(jobstore.get_plan(busy)[0]["fx"]["graphics"])
print("OK")
'''
    _run(code, tmp_path, env_extra=ON)


def test_changing_the_handle_never_keeps_the_old_account_photo(tmp_path):
    """Reporte de Kevin («no se pueden actualizar / re-generar»): al cambiar el @usuario de Instagram
    en el Studio, POST /profile/normalize rehace la url al instante y NO trae la foto de la cuenta
    anterior (la nueva baja en segundo plano y luego llega); guardar con el id actualiza ESE perfil;
    aplicarlo al proyecto re-genera Seguir con el usuario nuevo y nada usa la foto vieja (tampoco
    cuando termina la descarga); «Volver a buscar» un solo enlace (forzado) vuelve a pedir SOLO esa
    pagina aunque ya tenga foto."""
    code = PRELUDE + r'''
IG2 = b"""<html><head><meta property="og:image" content="https://instagram.com/pp/otra.jpg"/></head></html>"""
FILES["/ig/otra.demo"] = (IG2, "text/html")
FILES["/ig/pp/otra.jpg"] = (cv2.imencode(".jpg", np.full((100, 100, 3), 200, np.uint8))[1].tobytes(), "image/jpeg")
IG2_URL = "https://instagram.com/otra.demo"
sv = S.profiles_save({"profile": S.profile_parse({"text": TEXT, "use_llm": False})["profile"]})
pid = sv["id"]
wait(idle)
saved = next(x for x in S.profiles_list()["profiles"] if x["id"] == pid)
old_sid = link(saved, "instagram")["photo"]["sid"]
# el Studio cambia el @usuario del enlace (lo demas igual): url nueva, sin la foto vieja
ig = dict(link(saved, "instagram"), handle="@otra.demo")
n = S.profile_normalize({"profile": {"links": [ig]}})["profile"]
l = n["links"][0]
assert l["url"] == IG2_URL and l["handle"] == "@otra.demo" and l["display"] == "@otra.demo", l
assert "photo" not in l and n["media_status"] == "fetching", n
wait(idle)
l2 = S.profile_normalize({"profile": {"links": [ig]}})["profile"]["links"][0]
assert l2["photo"]["sid"] != old_sid and l2["photo"]["src"].endswith("/pp/otra.jpg"), l2
# guardar con el id: el MISMO perfil, con el usuario nuevo y su foto (sin copia)
edited = dict(saved, links=[l2 if x["platform"] == "instagram" else x for x in saved["links"]])
up = S.profiles_save({"id": pid, "profile": edited})
assert up["replaced"] and [x["id"] for x in up["profiles"]] == [pid]
assert link(up["profile"], "instagram")["photo"]["sid"] == l2["photo"]["sid"]
# un proyecto con el perfil de ANTES (foto vieja en Seguir) -> aplicar el editado
plan = directed(saved)
jid = new_job(plan)
assert old_sid in json.dumps(plan["fx"])
r = S.profile_put(jid, {"profile": up["profile"]})
fol = [g for g in r["plan"]["fx"]["graphics"] if g["kind"] == "cta" and g.get("variant") == "follow"]
assert fol and all(f["handle"] == "@otra.demo" and f["media"]["sid"] == l2["photo"]["sid"] for f in fol), fol
assert old_sid not in json.dumps(r["plan"]["fx"]) and "perfil.demo" not in json.dumps(r["plan"]["fx"])
# aplicado SIN la foto nueva (el Studio aun no la tenia): llega sola y la vieja nunca vuelve
cut = dict(up["profile"], links=[dict(x, photo=None, media_src={}) if x["platform"] == "instagram" else x
                                 for x in up["profile"]["links"]])
r2 = S.profile_put(jid, {"profile": cut})
assert old_sid not in json.dumps(r2["plan"]["fx"])
assert all(f["media"]["sid"] == l2["photo"]["sid"] for f in r2["plan"]["fx"]["graphics"]
           if f["kind"] == "cta" and f.get("variant") == "follow")      # ya estaba en la cache
# «Volver a buscar» un solo enlace aunque ya tenga foto: solo esa pagina, otra vez
h0 = HITS.get("/ig/otra.demo", 0)
rr = S.profile_media_refresh({"profile": {"links": [l2]}})
assert rr["urls"] == [IG2_URL], rr["urls"]
wait(idle)
assert HITS["/ig/otra.demo"] == h0 + 1
assert S.profile_parse({"text": IG2_URL, "use_llm": False})["profile"]["links"][0]["photo"]["sid"] == l2["photo"]["sid"]
# /profile/normalize: 400 sin perfil o vacio
for bad in ({}, {"profile": None}, {"profile": "x"}, {"profile": {"name": " "}}):
    try:
        S.profile_normalize(bad)
        raise AssertionError(bad)
    except S.HTTPException as e:
        assert e.status_code == 400
assert "profile_normalize" in S.get_settings()["features"]
print("OK")
'''
    _run(code, tmp_path, env_extra=ON)


def test_user_media_off_and_offline_are_respected(tmp_path):
    """Lo subido por el usuario nunca se pisa ni se re-descarga; lo que quito (media_off) no
    vuelve; una pagina sin red deja media_status 'offline' sin tumbar nada y no borra lo que ya
    estaba; un proyecto sin la clave `profile` en el autoguardado conserva el suyo."""
    code = PRELUDE + r'''
mine = {"sid": "foto_subida_demo", "type": "image", "w": 600, "h": 600}
prof = PR.normalize_profile({"name": "Dra. Ana Demo", "links": [
    {"url": IG_URL, "photo": mine, "media_src": {"photo": "user"}},
    {"url": PC_URL, "media_off": {"photo": True}}]})
out = S._profile_param(prof)
wait(idle)
got = S._lm_fill(out)
assert link(got, "instagram")["photo"] == mine and link(got, "instagram")["media_src"] == {"photo": "user"}
pcl = link(got, "paseoclub")
assert "photo" not in pcl and pcl["logo"]["w"] == 512 and pcl["media_off"] == {"photo": True}
# refrescar no toca lo del usuario: Instagram ya no tiene nada automatico que bajar
rr = S.profile_media_refresh({"profile": got})
assert rr["urls"] == [PC_URL]
wait(idle)
assert link(S._lm_fill(got), "instagram")["photo"] == mine
# sin red: 'offline', al instante, sin excepcion; lo que ya se tenia se queda
off = PR.normalize_profile({"name": "Otra Demo", "links": ["https://sitio-sin-red.demo.test/negocio"]})
t0 = time.time()
r = S.profiles_save({"profile": off})
assert time.time() - t0 < 2
wait(idle)
assert S._lm_status(off) == "offline"
assert S.profiles_list()["profiles"][0]["media_status"] == "offline"
good = S._lm_cache()[PC_URL]
S._lm_cache_put(PC_URL, {"ok": False, "t": time.time(), "photo": None, "logo": None})
assert S._lm_cache()[PC_URL]["logo"] == good["logo"]
print("OK")
'''
    _run(code, tmp_path, env_extra=ON)


def test_disabled_by_env_and_by_default_under_pytest(tmp_path):
    """REELFY_LINK_MEDIA=0 (y bajo pytest sin pedirlo): nada sale a la red, media_status 'done',
    las peticiones responden igual."""
    code = PRELUDE + r'''
assert not S._lm_enabled()
r = S.profile_parse({"text": TEXT, "use_llm": False})
assert r["profile"]["media_status"] == "done"
S.profiles_save({"profile": r["profile"]})
assert S._lm_kick(r["profile"]) == [] and idle() and HITS == {}
assert not S.LINK_MEDIA_F.exists()
assert S.profile_media_refresh({"profile": r["profile"]})["enabled"] is False
print("OK")
'''
    _run(code, tmp_path, env_extra={"REELFY_LINK_MEDIA": "0"})
    _run(code, tmp_path / "default")


def test_simultaneous_requests_download_each_page_once(tmp_path):
    """Varios pedidos A LA VEZ con el mismo perfil (parse, guardar, abrir proyectos): cada pagina
    se baja UNA vez (el hilo se registra y arranca dentro del candado: antes de start() no esta
    vivo y otro pedido lo lanzaria de nuevo, con dos escritores del mismo <sid>.png)."""
    code = PRELUDE + r'''
prof = PR.normalize_profile({"name": "Ana Demo", "links": [IG_URL, PC_URL]})
DELAY["s"] = 0.3
bar = threading.Barrier(12)
out = []
def go():
    bar.wait()
    out.append(S._lm_kick(prof))
ths = [threading.Thread(target=go) for _ in range(12)]
[t.start() for t in ths]
[t.join() for t in ths]
wait(idle)
assert sorted(u for r in out for u in r) == sorted([IG_URL, PC_URL]), out
assert HITS["/pc/business/negocio-demo"] == 1 and HITS["/ig/perfil.demo"] == 1, HITS
assert S._lm_status(prof) == "done"
print("OK")
'''
    _run(code, tmp_path, env_extra=ON)


def test_refetch_without_an_image_never_wipes_the_photo(tmp_path):
    """«Volver a buscar la foto» (refresco forzado) cuando la pagina responde SIN imagen (el muro de
    inicio de sesion de Instagram, una pagina a medias): la foto/logo que ya se tenia de ESA url se
    queda en la cache, en los perfiles guardados y en los proyectos (antes se borraba de todos). Una
    imagen NUEVA si la reemplaza, y otra url nunca hereda la de la anterior."""
    code = PRELUDE + r'''
sv = S.profiles_save({"profile": S.profile_parse({"text": TEXT, "use_llm": False})["profile"]})
pid = sv["id"]
wait(idle)
saved = next(x for x in S.profiles_list()["profiles"] if x["id"] == pid)
ig, pc = link(saved, "instagram"), link(saved, "paseoclub")
old_ig, old_pc, old_logo = ig["photo"]["sid"], pc["photo"]["sid"], pc["logo"]["sid"]
plan = directed(saved)
jid = new_job(plan)
r0 = S.profile_put(jid, {"profile": saved})
fol = lambda pl: [g for g in pl["fx"]["graphics"] if g["kind"] == "cta" and g.get("variant") == "follow"]
assert fol(r0["plan"]) and all(f["media"]["sid"] == old_ig for f in fol(r0["plan"]))
# la pagina ahora responde sin imagen (muro de inicio de sesion) y sin icono
FILES["/ig/perfil.demo"] = (b"<html><head><title>Inicia sesion</title></head><body>login</body></html>", "text/html")
FILES["/pc/business/negocio-demo"] = (b"<html><head></head><body>sin vista previa</body></html>", "text/html")
h0 = dict(HITS)
rr = S.profile_media_refresh({"profile": {"links": [ig, pc]}})
assert sorted(rr["urls"]) == sorted([IG_URL, PC_URL]), rr
wait(idle)
assert HITS["/ig/perfil.demo"] == h0["/ig/perfil.demo"] + 1          # si se volvio a pedir
c = S._lm_cache()
assert c[IG_URL]["ok"] and c[IG_URL]["photo"]["sid"] == old_ig and c[IG_URL]["kept"] == ["photo"], c[IG_URL]
assert c[PC_URL]["photo"]["sid"] == old_pc and c[PC_URL]["logo"]["sid"] == old_logo
after = next(x for x in S.profiles_list()["profiles"] if x["id"] == pid)
assert link(after, "instagram")["photo"]["sid"] == old_ig and link(after, "paseoclub")["photo"]["sid"] == old_pc
assert link(after, "paseoclub")["logo"]["sid"] == old_logo
p2, _rev = jobstore.get_plan(jid)
assert link(p2["profile"], "instagram")["photo"]["sid"] == old_ig
assert fol(p2) and all(f["media"]["sid"] == old_ig for f in fol(p2))
# una imagen NUEVA en la pagina: esa si reemplaza a la anterior
FILES["/ig/perfil.demo"] = (b"""<html><head><meta property="og:image" content="https://instagram.com/pp/nueva.jpg"/></head></html>""", "text/html")
FILES["/ig/pp/nueva.jpg"] = (cv2.imencode(".jpg", np.full((100, 100, 3), 30, np.uint8))[1].tobytes(), "image/jpeg")
S.profile_media_refresh({"profile": {"links": [ig]}})
wait(idle)
new = link(next(x for x in S.profiles_list()["profiles"] if x["id"] == pid), "instagram")["photo"]
assert new["sid"] != old_ig and new["src"].endswith("/pp/nueva.jpg") and "kept" not in S._lm_cache()[IG_URL]
# otra url (otra cuenta) que responde sin imagen no hereda la foto de la anterior
FILES["/ig/otra.sin.foto"] = (b"<html><head></head></html>", "text/html")
o = S._profile_param({"links": ["https://instagram.com/otra.sin.foto"]})
wait(idle)
assert "photo" not in S._lm_fill(o)["links"][0]
print("OK")
'''
    _run(code, tmp_path, env_extra=ON)
