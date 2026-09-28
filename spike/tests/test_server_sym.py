"""app/server.py: regla de SIMETRIA de la marca (todo overlay deja el mismo espacio a la izquierda
y a la derecha). Los planes de un director anterior a la regla se simetrizan UNA vez al abrirlos
en el Studio o al exportar el plan guardado (director.symmetrize, marca director.sym = 1, sube
plan_rev); todo lo que sale del director (dirigir, re-dirigir, aplicar el perfil) pasa por la
misma funcion. Sin director.symmetrize (motor viejo) o si falla, el plan queda tal cual.

Cada prueba importa el servidor en un proceso aparte con DATA temporal (como test_server_setup).
director.symmetrize se sustituye por uno FALSO (centra x de lo que puso la IA) para probar solo
el cableado del servidor; datos ficticios, sin clientes reales.
"""
from test_server_setup import _run

PRELUDE = '''
import server as S, jobstore
from motion import director
S._kick_src_proxy = lambda *a, **k: None
CALLS = []

def fake_sym(plan):
    CALLS.append(1)
    for g in plan["fx"].get("graphics", []):
        if g.get("src") != "user" and not g.get("locked"):
            g["x"] = 0.5
    return plan

director.symmetrize = fake_sym

def plan_v2(**dd):
    return {"version": 2, "video": "v.mp4", "duration": 6.0, "shots": [{"start": 0, "end": 6}],
            "director": dict({"style": "dinamico"}, **dd),
            "fx": {"text": [], "graphics": [
                {"id": "gr_ai", "kind": "sticker_text", "text": "HOLA", "x": 0.3, "y": 0.4, "start": 1, "end": 2, "src": "ai"},
                {"id": "gr_user", "kind": "sticker_text", "text": "MIO", "x": 0.2, "y": 0.6, "start": 1, "end": 2, "src": "user"}]}}

def new_job(plan, state="review"):
    jid = "s" + os.urandom(5).hex()
    now = time.time()
    cx = jobstore._cx()
    cx.execute("INSERT INTO jobs (id, kind, state, phase, pct, message, ext, params, plan, created, "
               "updated, plan_rev, plan_hash) VALUES (?, 'compose', ?, ?, 100, '', '.mp4', '{}', ?, ?, ?, 1, ?)",
               (jid, state, state, json.dumps(plan), now, now, jobstore.plan_hash(plan)))
    cx.commit()
    return jid

def xs(plan):
    return {g["id"]: g["x"] for g in plan["fx"]["graphics"]}
'''


def test_plan_viejo_se_simetriza_una_vez_al_abrirlo(tmp_path):
    code = PRELUDE + '''
jid = new_job(plan_v2())
r = S.get_plan(jid)
assert r["plan"]["director"]["sym"] == 1 and r["plan_rev"] == 2, r["plan_rev"]
assert xs(r["plan"]) == {"gr_ai": 0.5, "gr_user": 0.2}          # lo del usuario no se mueve
stored, rev = jobstore.get_plan(jid)
assert stored["director"]["sym"] == 1 and rev == 2 and xs(stored)["gr_ai"] == 0.5
n = len(CALLS)
r2 = S.get_plan(jid)                                           # ya marcado: no se repite
assert len(CALLS) == n and r2["plan_rev"] == 2
# /status con el plan (reabrir el proyecto con ?job=): mismo camino
j2 = new_job(plan_v2())
st = json.loads(S.status(j2, plan=1).body)
assert st["plan"]["director"]["sym"] == 1 and st["plan_rev"] == 2 and xs(st["plan"])["gr_ai"] == 0.5
st0 = json.loads(S.status(j2, plan=0).body)
assert "plan" not in st0
# plan ya simetrico (director nuevo): no se toca ni sube la revision
j3 = new_job(plan_v2(sym=1))
n = len(CALLS)
r3 = S.get_plan(j3)
assert len(CALLS) == n and r3["plan_rev"] == 1 and xs(r3["plan"])["gr_ai"] == 0.3
# trabajo ocupado (export en curso): se deja para la siguiente carga
j4 = new_job(plan_v2(), state="running")
r4 = S.get_plan(j4)
assert "sym" not in r4["plan"]["director"] and r4["plan_rev"] == 1
# plan v1 (sin fx): nada
j5 = new_job({"video": "v.mp4", "duration": 3.0, "phrases": []})
assert S.get_plan(j5)["plan_rev"] == 1
print("OK")
'''
    _run(code, tmp_path)


def test_sin_symmetrize_o_si_falla_el_plan_queda_igual(tmp_path):
    code = PRELUDE + '''
del director.symmetrize                                      # motor viejo: el servidor igual arranca
jid = new_job(plan_v2())
r = S.get_plan(jid)
assert r["plan_rev"] == 1 and "sym" not in r["plan"]["director"] and xs(r["plan"])["gr_ai"] == 0.3
def boom(plan):
    raise RuntimeError("falla")
director.symmetrize = boom
r = S.get_plan(jid)
assert r["plan_rev"] == 1 and xs(r["plan"])["gr_ai"] == 0.3
assert S._sym({"version": 1}) == {"version": 1} and S._sym(None) is None
print("OK")
'''
    _run(code, tmp_path)


def test_salidas_del_director_y_del_perfil_salen_simetricas(tmp_path):
    code = PRELUDE + '''
# dirigir (analisis, re-dirigir todo): _direct_plan simetriza y marca
def fake_direct(plan, style, intensity, seed, use_llm=True, on_step=None, trim=True, music_sync=None):
    return plan_v2(llm="heuristico", vlm="heuristico")
director.direct = fake_direct
out, warns = S._direct_plan({"video": "v.mp4"}, S._director_params({}), use_llm=False)
assert out["director"]["sym"] == 1 and xs(out) == {"gr_ai": 0.5, "gr_user": 0.2}
# el autoguardado con un perfil nuevo re-aplica el perfil y sale simetrico
def fake_apply(plan, prof):
    p = json.loads(json.dumps(plan))
    p["profile"] = prof
    p["fx"]["graphics"].append({"id": "gr_lt", "kind": "lower_third", "title": "Ana", "x": 0.07,
                                "y": 0.7, "start": 0, "end": 2, "src": "ai"})
    return p
director.apply_profile = fake_apply
jid = new_job(plan_v2(sym=1))
p = plan_v2(sym=1)
p["profile"] = {"name": "Ana Demo", "title": "Doctora"}
out, applied = S._reapply_profile(jid, p)
assert applied and out["director"]["sym"] == 1
assert {g["id"]: g["x"] for g in out["fx"]["graphics"]}["gr_lt"] == 0.5
print("OK")
'''
    _run(code, tmp_path)


def test_symmetrize_real_al_abrir_un_plan_viejo(tmp_path):
    """Cableado con el director.symmetrize REAL (sin sustituto): un plan del director viejo
    (rotulo anclado a la izquierda, tarjeta en 0.47 y ancho 0.8, destello de un lado) sale
    centrado / en par espejo, marcado y guardado; lo del usuario queda igual."""
    code = '''
import server as S, jobstore
from motion import director, ctx as C
S._kick_src_proxy = lambda *a, **k: None
assert callable(getattr(director, "symmetrize", None))
plan = {"version": 2, "video": "v.mp4", "duration": 8.0, "dims": [1080, 1920], "cuts": [], "phrases": [],
        "entities": {},
        "shots": [{"start": 0, "end": 8, "person": False, "framing": "sin_persona", "use": "broll",
                   "free_space": "arriba", "face": None, "text_seen": [], "place": "calle"}],
        "director": {"style": "dinamico", "intensity": 0.75, "seed": 7, "rev": 0},
        "fx": {"text": [], "sfx": [], "transitions": [], "effects": [], "cutaways": [], "graphics": [
            {"id": "gr_name_s0", "kind": "lower_third", "title": "Ana Demo", "subtitle": "Prueba", "x": 0.07,
             "y": 0.66, "size": 0.064, "start": 0.5, "end": 2.5, "src": "ai"},
            {"id": "gr_place_s1", "kind": "card", "variant": "location", "title": "Calle Ficticia 1",
             "x": 0.47, "y": 0.45, "size": 0.8, "start": 3.0, "end": 5.0, "src": "ai"},
            {"id": "gr_doodle_s2", "kind": "doodle", "name": "sparkle", "x": 0.72, "y": 0.2, "size": 0.1,
             "color": "#FFFFFF", "border_color": "#161616", "start": 5.5, "end": 7.0, "src": "ai"},
            {"id": "gr_mio", "kind": "sticker_text", "text": "MIO", "x": 0.2, "y": 0.6, "start": 1, "end": 2,
             "src": "user"}]}}
now = time.time()
jid = "s" + os.urandom(5).hex()
cx = jobstore._cx()
cx.execute("INSERT INTO jobs (id, kind, state, phase, pct, message, ext, params, plan, created, "
           "updated, plan_rev, plan_hash) VALUES (?, 'compose', 'review', 'review', 100, '', '.mp4', '{}', ?, ?, ?, 1, ?)",
           (jid, json.dumps(plan), now, now, jobstore.plan_hash(plan)))
cx.commit()
r = S.get_plan(jid)
p = r["plan"]
assert p["director"]["sym"] == 1 and r["plan_rev"] == 2
g = {x["id"]: x for x in p["fx"]["graphics"]}
assert g["gr_name_s0"]["x"] == 0.5 and g["gr_name_s0"]["align"] == "center"
assert g["gr_place_s1"]["x"] == 0.5 and g["gr_place_s1"]["size"] <= C.SYM_W + 1e-9
assert g["gr_doodle_s2_m"]["x"] == round(1 - g["gr_doodle_s2"]["x"], 3)
assert g["gr_mio"] == plan["fx"]["graphics"][3]
stored, rev = jobstore.get_plan(jid)
assert stored["director"]["sym"] == 1 and rev == 2
print("OK")
'''
    _run(code, tmp_path)
