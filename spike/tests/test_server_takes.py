"""app/server.py: POST /takes/{job} (limpieza de tomas sobre el plan guardado). Concurrencia
optimista como PUT /plan (409 con base_rev viejo), las tomas que el usuario toco (user: true via
PUT /plan) se respetan al re-detectar, silencios / manuales / recortes 'ai' intactos, sensibilidad,
tema del LLM solo si falta y un solo analisis de fondo por rafaga de cambios (debounce).

Cada prueba importa el servidor en un proceso aparte con DATA temporal (como test_server_setup).
Transcripcion inventada; sin LLM real (el tema se simula)."""
import json

from test_server_setup import _run
from test_takes import words_of

SRC = ("Hola a todos. |0.6| Agenda tu cita al, |0.9| agenda tu cita al número que aparece en pantalla. "
       "|0.8| El kit trae 19, no, 16 piezas. |0.7| Ponemos la la mezcla en el molde. |1.0| Lo más "
       "importante es la constancia en el entrenamiento diario. |2.0| La constancia en el "
       "entrenamiento diario es lo más importante.")

PRELUDE = '''
import server as S, jobstore
from fastapi import HTTPException
import takes as TK
S._kick_src_proxy = lambda *a, **k: None
KICKS = []
S._kick_analysis = lambda jid, plan, fmt="9:16", **k: KICKS.append((jid, len(plan.get("cuts") or [])))
WORDS = json.loads(%r)

def plan_v1():
    ph = [WORDS[i:i + 3] for i in range(0, len(WORDS), 3)]
    dur = round(WORDS[-1]["end"] + 1.0, 2)
    return {"stem": "t", "video": "v.mp4", "wav": "/no/existe.wav", "duration": dur, "phrases": ph,
            "cuts": [{"start": 0.0, "end": 0.4, "enabled": True},
                     {"start": dur - 0.5, "end": dur, "enabled": True, "manual": True},
                     {"start": 1.0, "end": 1.2, "enabled": False, "src": "ai", "id": "cut_ai_1"}],
            "highlights": [], "beats": []}

def new_job(plan):
    jid = "t" + os.urandom(5).hex()
    now = time.time()
    cx = jobstore._cx()
    cx.execute("INSERT INTO jobs (id, kind, state, phase, pct, message, ext, params, plan, created, "
               "updated, plan_rev, plan_hash) VALUES (?, 'analyze', 'review', 'review', 100, '', '.mp4', "
               "'{}', ?, ?, ?, 1, ?)", (jid, json.dumps(plan), now, now, jobstore.plan_hash(plan)))
    cx.commit()
    return jid

def status_of(r):
    return getattr(r, "status_code", 200)
'''


def _prelude():
    return PRELUDE % json.dumps(words_of(SRC))


def test_post_takes_guarda_y_responde_el_contrato(tmp_path):
    code = _prelude() + '''
assert any(getattr(r, "path", "") == "/takes/{job_id}" for r in S.app.routes)
assert "takes" in S.get_settings()["features"]
jid = new_job(plan_v1())
r = S.takes_run(jid, {"sensitivity": "normal", "use_llm": False, "base_rev": 1})
assert status_of(r) == 200 and set(r) == {"plan_rev", "takes", "cuts"}, r
assert r["plan_rev"] == 2
tk = r["cuts"]
assert tk and all(c["src"] == "takes" for c in tk)
kinds = {c["kind"] for c in tk}
assert {"retoma", "autocorreccion", "tartamudeo"} <= kinds, kinds
st = r["takes"]["stats"]
assert r["takes"]["sensitivity"] == "normal" and st["n"] == len(tk) and st["suggested"] >= 1
assert r["takes"]["topic"] is None and r["takes"]["llm"] is False
stored, rev = jobstore.get_plan(jid)
assert rev == 2 and stored["takes"] == r["takes"]
old = plan_v1()["cuts"]
assert stored["cuts"][:3] == old                         # silencio, manual y 'ai' intactos
assert [c for c in stored["cuts"] if c.get("src") == "takes"] == tk
# resumen de proyectos con la limpieza
meta = jobstore.plan_meta(stored)
assert meta["takes"]["n"] == len(tk) and meta["takes"]["removed_s"] > 0
# misma deteccion otra vez: el plan no cambia, la revision tampoco
r2 = S.takes_run(jid, {"sensitivity": "normal", "use_llm": False, "base_rev": 2})
assert r2["plan_rev"] == 2 and r2["cuts"] == tk
# base_rev viejo -> 409 con la revision actual (no pisa)
r3 = S.takes_run(jid, {"sensitivity": "a_fondo", "base_rev": 1})
assert status_of(r3) == 409 and json.loads(r3.body)["plan_rev"] == 2
assert jobstore.get_plan(jid)[0]["takes"]["sensitivity"] == "normal"
# rechazos
for bad in ({"sensitivity": "extrema"}, {"base_rev": "x"}):
    try:
        S.takes_run(jid, bad)
        raise SystemExit("debio fallar")
    except HTTPException as e:
        assert e.status_code == 400
try:
    S.takes_run("noexiste0000", {})
    raise SystemExit("debio fallar")
except HTTPException as e:
    assert e.status_code == 404
jv = new_job(None)
try:
    S.takes_run(jv, {})
    raise SystemExit("debio fallar")
except HTTPException as e:
    assert e.status_code == 409
print("OK")
'''
    _run(code, tmp_path, env_extra={"REELFY_TAKES_KICK_S": "30"})


def test_toggle_del_usuario_sobrevive_a_re_detectar_y_sensibilidad(tmp_path):
    code = _prelude() + '''
jid = new_job(plan_v1())
r = S.takes_run(jid, {"sensitivity": "normal", "use_llm": False})
rev = r["plan_rev"]
plan, _ = jobstore.get_plan(jid)
# el Studio restaura una toma (PUT /plan: enabled False + user True)
target = next(c for c in plan["cuts"] if c.get("src") == "takes" and c["enabled"])
for c in plan["cuts"]:
    if c.get("id") == target["id"]:
        c["enabled"], c["user"] = False, True
put = S.put_plan(jid, {"plan": plan, "base_rev": rev})
assert put["ok"] and put["plan_rev"] == rev + 1
# re-detectar a fondo: todo lo demas se habilita, lo que toco el usuario queda como lo dejo
r2 = S.takes_run(jid, {"sensitivity": "a_fondo", "use_llm": False, "base_rev": rev + 1})
assert r2["plan_rev"] == rev + 2 and r2["takes"]["sensitivity"] == "a_fondo"
mine = [c for c in r2["cuts"] if c["id"] == target["id"]]
assert len(mine) == 1 and mine[0]["enabled"] is False and mine[0]["user"] is True
assert all(c["enabled"] for c in r2["cuts"] if not c.get("user"))
assert r2["takes"]["stats"]["enabled"] == len(r2["cuts"]) - 1
# suave: solo las de nivel alta
r3 = S.takes_run(jid, {"sensitivity": "suave", "use_llm": False, "base_rev": r2["plan_rev"]})
assert all(c["enabled"] == (c["level"] == "alta") for c in r3["cuts"] if not c.get("user"))
assert [c for c in r3["cuts"] if c["id"] == target["id"]][0]["enabled"] is False
print("OK")
'''
    _run(code, tmp_path, env_extra={"REELFY_TAKES_KICK_S": "30"})


def test_tema_solo_si_falta_y_un_analisis_por_rafaga(tmp_path):
    code = _prelude() + '''
CALLS = []
def fake_topic(words, timeout=TK.TOPIC_TIMEOUT):
    CALLS.append(len(words))
    return {"topic": "Citas en la clínica", "summary": "Cómo agendar.", "sections": [{"start": 0.5, "title": "Inicio"}]}
TK.topic = fake_topic
jid = new_job(plan_v1())
r = S.takes_run(jid, {"sensitivity": "normal"})
assert r["takes"]["topic"] == "Citas en la clínica" and r["takes"]["llm"] is True and len(CALLS) == 1
stored, _ = jobstore.get_plan(jid)
assert stored["topic"] == "Citas en la clínica"
# ya tiene tema: re-detectar no vuelve a llamar al LLM y lo conserva
r2 = S.takes_run(jid, {"sensitivity": "a_fondo"})
assert len(CALLS) == 1 and r2["takes"]["topic"] == "Citas en la clínica"
# use_llm false en un plan sin tema: no se pregunta
j2 = new_job(plan_v1())
S.takes_run(j2, {"use_llm": False})
assert len(CALLS) == 1
# rafaga de cambios -> UN analisis de fondo con el plan guardado mas reciente
KICKS.clear()
S.takes_run(jid, {"sensitivity": "suave"})
S.takes_run(jid, {"sensitivity": "normal"})
time.sleep(0.9)
mine = [k for k in KICKS if k[0] == jid]
assert len(mine) == 1, KICKS
assert mine[0][1] == len(jobstore.get_plan(jid)[0]["cuts"])
print("OK")
'''
    _run(code, tmp_path, env_extra={"REELFY_TAKES_KICK_S": "0.4"})


def test_bordes_que_movio_el_usuario_sobreviven_a_re_detectar(tmp_path):
    """El Studio mueve el fin de una toma (arrastre o inspector: user + edges via PUT /plan): ni
    re-detectar con otro nivel ni 'Buscar de nuevo' la devuelven a los bordes detectados."""
    code = _prelude() + '''
jid = new_job(plan_v1())
r = S.takes_run(jid, {"sensitivity": "normal", "use_llm": False})
plan, rev = jobstore.get_plan(jid)
target = next(c for c in plan["cuts"] if c.get("src") == "takes" and c["enabled"])
moved = round(target["end"] - 0.3, 3)
for c in plan["cuts"]:
    if c.get("id") == target["id"]:
        c["end"], c["user"], c["edges"] = moved, True, True
put = S.put_plan(jid, {"plan": plan, "base_rev": rev})
rev = put["plan_rev"]
for sens in ("a_fondo", "suave", "normal", "normal"):
    r = S.takes_run(jid, {"sensitivity": sens, "use_llm": False, "base_rev": rev})
    rev = r["plan_rev"]
    mine = [c for c in r["cuts"] if c["id"] == target["id"]]
    assert len(mine) == 1 and mine[0]["end"] == moved and mine[0]["edges"] is True, (sens, mine)
# solo edges (sin user) tambien se respeta en el servidor
plan, rev = jobstore.get_plan(jid)
for c in plan["cuts"]:
    if c.get("id") == target["id"]:
        c.pop("user", None)
rev = S.put_plan(jid, {"plan": plan, "base_rev": rev})["plan_rev"]
r = S.takes_run(jid, {"sensitivity": "normal", "use_llm": False, "base_rev": rev})
assert [c["end"] for c in r["cuts"] if c["id"] == target["id"]] == [moved]
print("OK")
'''
    _run(code, tmp_path, env_extra={"REELFY_TAKES_KICK_S": "30"})


def test_el_analisis_anota_el_formato_con_el_que_nace_el_proyecto(tmp_path):
    """/compose de un video horizontal largo pide al director fmt 16:9: el plan lo guarda en
    plan.studio.fmt y el Studio lo abre asi sin re-analizar. Sin fmt no se anota nada (un plan
    sin studio.fmt ni takes es de antes: el Studio lo deja en 9:16)."""
    code = _prelude() + '''
sys.path.insert(0, "tests")
from test_director import synthetic_plan
for jid, d, want in (("0123456789ab", {"style": "dinamico", "fmt": "16:9"}, "16:9"),
                     ("0123456789ac", {"style": "dinamico"}, None),
                     ("0123456789ad", {"style": "dinamico", "fmt": "raro"}, None)):
    jobstore.create(jid, kind="compose", ext=".mp4", params={})
    S._finish_analysis(jid, jobstore.Progress(jid), synthetic_plan(), {"director": d})
    plan, _ = jobstore.get_plan(jid)
    assert (plan.get("studio") or {}).get("fmt") == want, (jid, plan.get("studio"))
print("OK")
'''
    _run(code, tmp_path)
