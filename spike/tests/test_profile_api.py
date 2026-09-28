"""API del perfil de la persona (docs/MOTION-ENGINE.md 6.1) contra un servidor REAL aislado.

POST /profile/parse, GET /profile/platforms, GET/POST/DELETE /profiles, PUT/GET /profile/{job},
`profile` en /direct, /compose y /analyze (el director lo integra) e iconos de marca por nombre en
/fx/icons. Sin LLM (ollama apagado: reglas) y solo datos FICTICIOS.
"""
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from test_server_api import srv  # noqa: F401  (servidor aislado con un trabajo en review)

SPIKE = Path(__file__).resolve().parents[1]
TEXT = ("ella es la Dra. Lucia Fernandez, es Odontologa, su instagram es https://instagram.com/dra.lucia.demo "
        "(o simplemente @dra.lucia.demo) y su pagina web es de PaseoClub https://paseo.club/business/dra-lucia-demo")


def _lower_thirds(plan):
    return [g for g in plan["fx"]["graphics"] if g["kind"] == "lower_third"]


def _follow(plan):
    return [g for g in plan["fx"]["graphics"] if g["kind"] == "cta" and g.get("variant") == "follow"]


def test_parse_platforms_and_icons(srv):  # noqa: F811
    api = srv["api"]
    t0 = time.time()
    r = api.post("/profile/parse", {"text": TEXT})
    assert time.time() - t0 < 10
    pr = r["profile"]
    assert pr["name"] == "Dra. Lucía Fernández" and pr["title"] == "Odontóloga"
    assert [(l["platform"], l["display"]) for l in pr["links"]] == [
        ("instagram", "@dra.lucia.demo"), ("paseoclub", "paseo.club/dra-lucia-demo")]
    assert pr["links"][1]["label"] == "Agenda en PaseoClub"
    assert r["llm"] is False and any("reglas" in n for n in r["notes"])      # sin ollama
    assert api.post("/profile/parse", {"text": "  "}, raw=True)[0] == 400
    assert api.post("/profile/parse", {"text": "x" * 5000}, raw=True)[0] == 400
    cat = api.get("/profile/platforms")
    ig = next(p for p in cat["platforms"] if p["id"] == "instagram")
    assert ig["icon"] == "brand-instagram" and ig["svg"].startswith("<svg") and ig["kind"] == "social"
    assert cat["use"]["handle_watermark"] is False and "booking" in cat["kinds"]
    icons = api.get("/fx/icons?names=brand-instagram,brand-tiktok,calendar-event,no-existe")["icons"]
    assert [i["name"] for i in icons] == ["brand-instagram", "brand-tiktok", "calendar-event"]
    assert "profile" in api.get("/settings")["features"]


def test_saved_profiles_crud(srv):  # noqa: F811
    api = srv["api"]
    prof = api.post("/profile/parse", {"text": TEXT})["profile"]
    r = api.post("/profiles", {"profile": prof})
    assert r["ok"] and r["id"] == "dra-lucia-fernandez" and r["profile"]["id"] == r["id"]
    f = srv["data"] / "assets" / "brand" / "profiles.json"
    assert f.exists() and json.loads(f.read_text())["profiles"][0]["name"] == "Dra. Lucía Fernández"
    assert r["replaced"] is False
    # solo el `id` explicito actualiza (mismo id); sin id, el mismo nombre NUNCA pisa en silencio:
    # crea otro con sufijo (homonimos o un guardado sin querer)
    r2 = api.post("/profiles", {"id": "dra-lucia-fernandez", "profile": dict(prof, title="Ortodoncista")})
    assert r2["id"] == "dra-lucia-fernandez" and r2["replaced"] is True and len(r2["profiles"]) == 1
    solo = api.post("/profiles", {"profile": {"name": "Dra. Lucía Fernández"}})
    assert solo["id"] == "dra-lucia-fernandez-2" and solo["replaced"] is False
    kept = next(p for p in solo["profiles"] if p["id"] == "dra-lucia-fernandez")
    assert kept["title"] == "Ortodoncista" and kept["links"]              # el original sigue completo
    r3 = api.post("/profiles", {"profile": {"name": "Panadería Ficticia",
                                            "links": ["instagram.com/panaderia.demo"]}})
    assert r3["id"] == "panaderia-ficticia"
    lst = api.get("/profiles")["profiles"]
    assert {p["id"] for p in lst} == {"dra-lucia-fernandez", "dra-lucia-fernandez-2", "panaderia-ficticia"}
    assert next(p for p in lst if p["id"] == "dra-lucia-fernandez")["title"] == "Ortodoncista"
    assert api.post("/profiles", {"profile": {"name": " "}}, raw=True)[0] == 400
    assert api.req("DELETE", "/profiles/panaderia-ficticia")["ok"]
    assert api.req("DELETE", "/profiles/dra-lucia-fernandez-2")["ok"]
    assert api.req("DELETE", "/profiles/panaderia-ficticia", raw=True)[0] == 404
    assert [p["id"] for p in api.get("/profiles")["profiles"]] == ["dra-lucia-fernandez"]


def test_direct_and_put_profile_update_the_edit(srv):  # noqa: F811
    api, job = srv["api"], srv["job"]
    prof = api.post("/profile/parse", {"text": TEXT})["profile"]
    r = api.post(f"/direct/{job}", {"full": True, "use_llm": False, "music": "procedural", "profile": prof})
    plan = r["plan"]
    assert plan["profile"]["name"] == "Dra. Lucía Fernández"
    assert plan["entities"]["name"] == "Dra. Lucía Fernández" and plan["entities"]["name_src"] == "perfil"
    lts = _lower_thirds(plan)
    assert all(lt["title"] == "Dra. Lucía Fernández" for lt in lts)
    assert api.get(f"/profile/{job}")["profile"]["name"] == "Dra. Lucía Fernández"
    # PUT del perfil: re-dirige al instante (sin LLM) y devuelve el plan
    other = {"name": "Dr. Tomás Ejemplo", "title": "Ortodoncista",
             "links": ["https://tiktok.com/@dr.tomas.demo", "https://calendly.com/dr-tomas-demo/cita"]}
    rev = r["plan_rev"]
    t0 = time.time()
    p2 = api.req("PUT", f"/profile/{job}", {"profile": other, "base_rev": rev})
    assert p2["ok"] and p2["plan_rev"] == rev + 1 and p2["ms"] < 8000 and time.time() - t0 < 15
    q = p2["plan"]
    assert q["profile"]["name"] == "Dr. Tomás Ejemplo" and q["entities"]["handle"] == "@dr.tomas.demo"
    assert all(lt["title"] == "Dr. Tomás Ejemplo" for lt in _lower_thirds(q))
    blob = json.dumps(q["fx"], ensure_ascii=False)
    assert "Lucía" not in blob and "dra.lucia.demo" not in blob
    fol = _follow(q)
    if fol:
        assert fol[-1]["handle"] == "@dr.tomas.demo" and fol[-1]["platform"] == "tiktok"
    assert api.get(f"/plan/{job}")["plan_rev"] == rev + 1
    # revision vieja: 409 sin pisar
    code, body, _ = api.req("PUT", f"/profile/{job}", {"profile": prof, "base_rev": rev}, raw=True)
    assert code == 409 and json.loads(body)["plan_rev"] == rev + 1
    # perfil guardado por id y quitarlo con null
    api.post("/profiles", {"profile": prof})
    p3 = api.req("PUT", f"/profile/{job}", {"profile_id": "dra-lucia-fernandez"})
    assert p3["profile"]["name"] == "Dra. Lucía Fernández"
    p4 = api.req("PUT", f"/profile/{job}", {"profile": None})
    assert "profile" not in p4["plan"] and p4["plan"]["entities"].get("name_src") != "perfil"
    assert api.req("PUT", f"/profile/{job}", {}, raw=True)[0] == 400
    assert api.req("PUT", f"/profile/{job}", {"profile": "no es un objeto"}, raw=True)[0] == 400
    assert api.req("PUT", f"/profile/{job}", {"profile_id": "no-existe"}, raw=True)[0] == 404
    # el Studio cambia la red de un enlace (el parse tomo un @ suelto como Instagram): se respeta
    amb = api.post("/profile/parse", {"text": "Dra. Lucía Fernández, odontóloga. @otra.cuenta.demo"})["profile"]
    l0 = amb["links"][0]
    assert l0["platform"] == "instagram" and l0.get("ambiguous") is True
    amb["links"][0] = dict(l0, platform="tiktok")
    p5 = api.req("PUT", f"/profile/{job}", {"profile": amb})
    assert [(l["platform"], l["handle"], l["label"]) for l in p5["profile"]["links"]] == [
        ("tiktok", "@otra.cuenta.demo", "TikTok")]
    assert not p5["profile"]["links"][0].get("ambiguous")
    assert _follow(p5["plan"])[-1]["platform"] == "tiktok" if _follow(p5["plan"]) else True
    # tambien sin `ambiguous` (el usuario se equivoco de red): la url armada con el usuario cambia
    conf = dict(p5["profile"])
    conf["links"] = [dict(conf["links"][0], platform="youtube")]
    p6 = api.req("PUT", f"/profile/{job}", {"profile": conf})
    assert p6["profile"]["links"][0]["platform"] == "youtube"
    assert p6["profile"]["links"][0]["url"] == "https://youtube.com/@otra.cuenta.demo"
    # PUT /plan (autoguardado) con el perfil cambiado: se RE-APLICA (rotulo y Seguir al dia) y
    # devuelve el plan; sin la clave `profile` se conserva la guardada
    cur = api.get(f"/plan/{job}")
    pl = cur["plan"]
    pl["profile"] = dict(pl["profile"], name="Dra. Paula Autoguardado")
    pp = api.req("PUT", f"/plan/{job}", {"plan": pl, "base_rev": cur["plan_rev"]})
    assert pp["ok"] and pp.get("profile_applied") is True
    assert all(lt["title"] == "Dra. Paula Autoguardado" for lt in _lower_thirds(pp["plan"]))
    assert api.get(f"/plan/{job}")["plan"]["entities"]["name"] == "Dra. Paula Autoguardado"
    cur = api.get(f"/plan/{job}")
    sin = {k: v for k, v in cur["plan"].items() if k != "profile"}
    ps = api.req("PUT", f"/plan/{job}", {"plan": sin, "base_rev": cur["plan_rev"]})
    assert ps["ok"] and not ps.get("profile_applied")
    assert api.get(f"/plan/{job}")["plan"]["profile"]["name"] == "Dra. Paula Autoguardado"
    # /direct sin la clave `profile` conserva el del plan; con null lo quita
    api.req("PUT", f"/profile/{job}", {"profile": other})
    keep = api.post(f"/direct/{job}", {"style": "elegante"})["plan"]
    assert keep["profile"]["name"] == "Dr. Tomás Ejemplo"
    gone = api.post(f"/direct/{job}", {"profile": None})["plan"]
    assert "profile" not in gone


PRELUDE = '''
import os, sys, json, time, threading
sys.path.insert(0, "app")
'''


def _run(code, tmp_path, timeout=240):
    env = dict(os.environ, REELFY_DATA=str(tmp_path), REELFY_GATED="0", REELFY_AUTO_DOWNLOAD="0",
               REELFY_OLLAMA_URL="http://127.0.0.1:9", REELFY_WORKERS="1", REELFY_BG_ANALYSIS="0",
               REELFY_ACE_BIN=str(tmp_path / "sin_ace"), REELFY_MUSIC_MODELS=str(tmp_path / "sin_musica"))
    r = subprocess.run([sys.executable, "-c", PRELUDE + textwrap.dedent(code)], cwd=str(SPIKE), env=env,
                       capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]


def test_profile_travels_from_compose_to_the_director(tmp_path):
    """/compose y /analyze guardan el perfil en el trabajo (o el guardado por `profile_id`) y
    _finish_analysis lo pone en el plan ANTES del director (rotulo con el nombre del perfil)."""
    code = f'''
import server as S, jobstore
sys.path.insert(0, "tests")
from test_director import synthetic_plan
S._require_engine = lambda: None
S._require_license = lambda: None
S.SOURCES.mkdir(parents=True, exist_ok=True)
(S.SOURCES / "5ce0000000c1.mp4").write_bytes(b"x")
text = {TEXT!r}
prof = S.profile_parse({{"text": text, "use_llm": False}})["profile"]
r = S.compose_timeline({{"main": [{{"sid": "5ce0000000c1", "type": "video", "in": 0, "out": 1}}],
                        "director": {{"style": "minimal"}}, "profile": prof}})
params = jobstore.get(r["job_id"])["params"]
assert params["profile"]["name"] == "Dra. Lucía Fernández", params
S.profiles_save({{"profile": prof}})
r2 = S.compose_timeline({{"main": [{{"sid": "5ce0000000c1", "type": "video"}}], "profile_id": "dra-lucia-fernandez"}})
assert jobstore.get(r2["job_id"])["params"]["profile"]["title"] == "Odontóloga"
try:
    S.compose_timeline({{"main": [{{"sid": "5ce0000000c1", "type": "video"}}], "profile": "{{roto"}})
    raise SystemExit("debio fallar")
except S.HTTPException as e:
    assert e.status_code == 400
# el director recibe el perfil desde los parametros del trabajo
jid = "0123456789ab"
jobstore.create(jid, kind="compose", ext=".mp4", params={{"profile": prof}})
S._finish_analysis(jid, jobstore.Progress(jid), synthetic_plan(), {{"profile": prof, "director": {{"style": "dinamico"}}}})
plan, _rev = jobstore.get_plan(jid)
assert plan["profile"]["name"] == "Dra. Lucía Fernández" and plan["entities"]["name_src"] == "perfil"
lts = [g for g in plan["fx"]["graphics"] if g["kind"] == "lower_third"]
assert lts and lts[0]["title"] == "Dra. Lucía Fernández" and lts[0]["subtitle"] == "Odontóloga"
# la foto de un perfil guardado no la borra la limpieza del disco
old = time.time() - 3 * 86400
for suf in (".png", "_t.jpg"):
    f = S.SOURCES / ("5ce0000000f1" + suf); f.write_bytes(b"x"); os.utime(f, (old, old))
S.profiles_save({{"profile": dict(prof, name="Otra Demo", avatar={{"sid": "5ce0000000f1", "type": "image"}})}})
S._gc_sources()
assert (S.SOURCES / "5ce0000000f1.png").exists()
print("OK")
'''
    _run(code, tmp_path)
