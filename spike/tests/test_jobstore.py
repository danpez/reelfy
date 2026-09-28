"""app/jobstore.py: migracion de un jobs.db de v0.4, plan_rev, estado liviano (sin plan),
tareas laterales (preview/analysis) y recuperacion tras un cierre."""
import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import jobstore  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(jobstore, "DB", tmp_path / "jobs.db")
    monkeypatch.setattr(jobstore, "_local", threading.local())
    jobstore.init()
    return jobstore


def test_migra_un_jobs_db_de_v04(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    cx = sqlite3.connect(db)
    cx.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT, state TEXT, phase TEXT,
        pct REAL DEFAULT 0, message TEXT DEFAULT '', eta INTEGER, elapsed INTEGER,
        ext TEXT, params TEXT, plan TEXT, clips TEXT, error TEXT,
        cancel INTEGER DEFAULT 0, created REAL, updated REAL)""")
    cx.execute("INSERT INTO jobs (id, kind, state, phase, plan, created, updated) "
               "VALUES ('viejo', 'analyze', 'review', 'review', '{\"version\": 1}', 1, 1)")
    cx.commit()
    cx.close()
    monkeypatch.setattr(jobstore, "DB", db)
    monkeypatch.setattr(jobstore, "_local", threading.local())
    jobstore.init()
    st = jobstore.status("viejo")
    assert st["phase"] == "review" and st["plan_rev"] == 0 and st["has_plan"]
    assert "plan" not in st
    assert jobstore.status("viejo", with_plan=True)["plan"] == {"version": 1}


def test_plan_rev_sube_con_cada_plan(store):
    store.create("j1", kind="analyze", ext=".mp4")
    assert store.status("j1")["plan_rev"] == 0 and not store.status("j1")["has_plan"]
    store.update("j1", state="review", phase="review", plan={"version": 2, "fx": {}})
    assert store.status("j1")["plan_rev"] == 1
    store.update("j1", message="sin plan")
    assert store.status("j1")["plan_rev"] == 1
    store.requeue("j1", kind="render", params={"pro": True}, plan={"version": 2, "fx": {"a": 1}})
    assert store.status("j1")["plan_rev"] == 2
    store.requeue("j1", kind="render", params={})          # sin plan: no cambia
    assert store.status("j1")["plan_rev"] == 2
    plan, rev = store.get_plan("j1")
    assert plan["fx"] == {"a": 1} and rev == 2


def test_estado_liviano_y_tareas_laterales(store):
    store.create("j2", kind="compose", ext=".mp4", plan={"version": 2, "big": "x" * 50000})
    store.update("j2", stage="director", warnings=["director: heuristicas"])
    store.side_update("j2", "preview", replace=True, state="running", pct=12.5, stage="base")
    store.side_update("j2", "preview", pct=40.0)
    store.side_update("j2", "analysis", state="done", pct=100)
    st = store.status("j2")
    assert "plan" not in st and len(json.dumps(st)) < 2000       # el plan pesa y no viaja
    assert st["stage"] == "director" and st["warnings"] == ["director: heuristicas"]
    assert st["preview_state"] == "running" and st["preview_pct"] == 40.0
    assert st["preview_stage"] == "base" and st["analysis_state"] == "done"
    store.side_update("j2", "preview", replace=True, state="queued")
    assert "pct" not in store.side_get("j2", "preview")


def test_ejecucion_vieja_no_pisa_a_la_nueva(store):
    store.create("j6", kind="compose", ext=".mp4")
    store.side_update("j6", "preview", replace=True, state="running", tok="viejo")
    store.side_update("j6", "preview", replace=True, state="queued", tok="nuevo")
    assert store.side_update("j6", "preview", match="viejo", state="canceled") is None
    assert store.status("j6")["preview_state"] == "queued"
    assert store.side_update("j6", "preview", match="nuevo", state="done")["state"] == "done"


def test_recover_marca_lo_que_corria(store):
    store.create("j3", kind="render", ext=".mp4")
    store.update("j3", state="running")
    store.side_update("j3", "preview", state="running", pct=50)
    store.side_update("j3", "analysis", state="done", pct=100)
    store.recover()
    st = store.status("j3")
    assert st["phase"] == "error" and "interrumpi" in st["error"]
    assert st["preview_state"] == "error" and st["analysis_state"] == "done"


def test_cancelacion_cooperativa(store):
    store.create("j4", kind="render", ext=".mp4")
    store.request_cancel("j4")                          # en cola: se cancela de inmediato
    assert store.get("j4")["state"] == "canceled"
    store.create("j5", kind="render", ext=".mp4")
    store.update("j5", state="running")
    chk = store.CancelChecker("j5")
    assert not chk.canceled()
    store.request_cancel("j5")
    chk._last = 0
    assert chk.canceled()
    with pytest.raises(store.JobCanceled):
        chk.check()
    store.requeue("j5", kind="render")                  # reencolar limpia el flag
    assert not store.is_canceled("j5")


def test_etiqueta_de_error_por_tipo():
    assert jobstore._error_label("compose") == "Análisis"
    assert jobstore._error_label("analyze") == "Análisis"
    assert jobstore._error_label("render") == "Render"


def test_plan_rev_solo_sube_si_cambia_el_contenido(store):
    plan = {"version": 2, "fx": {"text": [{"id": "t1"}]}, "video": "v.mp4"}
    store.create("k1", kind="compose", ext=".mp4", plan=plan)
    assert store.status("k1")["plan_rev"] == 1
    store.update("k1", plan=json.loads(json.dumps(plan, sort_keys=True)))   # mismo contenido
    assert store.status("k1")["plan_rev"] == 1
    store.requeue("k1", kind="render", params={"pro": True}, plan=plan)     # export del mismo plan
    assert store.status("k1")["plan_rev"] == 1
    store.requeue("k1", kind="render", params={}, plan=dict(plan, video="w.mp4"))
    assert store.status("k1")["plan_rev"] == 2


def test_update_plan_con_concurrencia_optimista(store):
    store.create("k2", kind="compose", ext=".mp4", plan={"version": 2, "a": 1})
    ok, rev = store.update_plan("k2", {"version": 2, "a": 2}, base_rev=1)
    assert ok and rev == 2
    ok, rev = store.update_plan("k2", {"version": 2, "a": 3}, base_rev=1)     # base vieja
    assert not ok and rev == 2 and store.get_plan("k2")[0]["a"] == 2
    ok, rev = store.update_plan("k2", {"version": 2, "a": 2}, base_rev=2)     # sin cambios
    assert ok and rev == 2
    ok, rev = store.update_plan("k2", {"version": 2, "a": 4})                 # sin base: gana
    assert ok and rev == 3
    assert store.update_plan("noexiste", {}) == (False, 0)


def test_export_cancelado_con_plan_vuelve_a_review(store):
    store.create("k3", kind="compose", ext=".mp4", plan={"version": 2})
    store.requeue("k3", kind="render", params={"pro": True})
    store.request_cancel("k3")                            # en cola: vuelve a review de inmediato
    st = store.status("k3")
    assert st["phase"] == "review" and st["state"] == "review" and st["error"] is None
    assert "Export cancelado" in st["message"] and not store.is_canceled("k3")
    store.requeue("k3", kind="render", params={"pro": True})
    store.update("k3", state="running")
    store.canceled_export("k3")
    st = store.status("k3")
    assert st["phase"] == "review" and not store.is_canceled("k3")


def test_recover_devuelve_el_export_interrumpido_a_review(store):
    store.create("k4", kind="compose", ext=".mp4", plan={"version": 2})
    store.requeue("k4", kind="render", params={"pro": True})
    store.update("k4", state="running")
    store.create("k5", kind="compose", ext=".mp4")
    store.update("k5", state="running")
    store.recover()
    assert store.status("k4")["phase"] == "review" and "export" in store.status("k4")["message"]
    assert store.status("k5")["phase"] == "error"


def test_housekeep_devuelve_los_ids_purgados(store):
    import time as _t
    for i in range(5):
        store.create(f"h{i}", kind="analyze", ext=".mp4")
        store.update(f"h{i}", state="done", phase="done")
        _t.sleep(0.01)
    store.update("h0", state="running")                   # en proceso: nunca se purga
    ids = store.housekeep(keep=2)
    assert sorted(ids) == ["h1", "h2"]
    assert sorted(store.all_ids()) == ["h0", "h3", "h4"]


def test_status_aplana_tiempo_y_mensaje_de_tareas_laterales(store):
    store.create("k6", kind="compose", ext=".mp4")
    store.side_update("k6", "analysis", state="done", t=3.2, message="listo")
    st = store.status("k6")
    assert st["analysis_t"] == 3.2 and st["analysis_message"] == "listo"


def test_resumen_del_plan_para_proyectos_recientes(store):
    """`meta` se recalcula en cada escritura del plan; recent() no deserializa planes y completa
    una vez las filas viejas (sin la columna)."""
    plan = {"version": 2, "duration": 20.0, "shots": [{}, {}],
            "cuts": [{"start": 1, "end": 3}, {"start": 2, "end": 4}, {"start": 10, "end": 11, "enabled": False}],
            "director": {"style": "elegante", "script": {"topic": "Pan de caja artesanal"}}}
    store.create("m1", kind="compose", ext=".mp4", plan=plan)
    r = store.recent()[0]
    assert r["id"] == "m1" and r["topic"] == "Pan de caja artesanal" and r["version"] == 2
    assert r["duration"] == 20.0 and r["final_duration"] == 17.0 and r["style"] == "elegante"
    assert r["shots"] == 2 and r["has_plan"] and r["plan_rev"] == 1 and r["ext"] == ".mp4"
    store.update_plan("m1", dict(plan, director={"script": {"topic": "Otro tema"}}))
    assert store.recent()[0]["topic"] == "Otro tema"
    store.update("m1", plan={"version": 1, "duration": 8.0,
                             "highlights": [{"title": "Titulo del momento"}]})
    r = store.recent()[0]
    assert r["topic"] == "Titulo del momento" and r["version"] == 1 and r["final_duration"] == 8.0
    # fila de antes de la columna `meta`: se completa al listar
    store._cx().execute("UPDATE jobs SET meta=NULL WHERE id='m1'")
    store._cx().commit()
    assert store.recent()[0]["topic"] == "Titulo del momento"
    assert store._cx().execute("SELECT meta FROM jobs WHERE id='m1'").fetchone()[0]
    store.create("m2", kind="analyze", ext=".mov")
    r = store.recent()[0]
    assert r["id"] == "m2" and not r["has_plan"] and r["topic"] is None


def test_fuentes_referenciadas_por_planes(store):
    store.create("s1", kind="compose", ext=".mp4", plan={"version": 2, "fx": {"graphics": [
        {"id": "g", "media": {"sid": "0123456789ab", "type": "image"}}],
        "cutaways": [{"id": "c", "media": {"sid": "abcdefabcdef", "type": "video"}}]}})
    store.create("s2", kind="compose", ext=".mp4", plan={"version": 2, "fx": {}})
    assert store.referenced_sids() == {"0123456789ab", "abcdefabcdef"}


def test_status_aplana_musica_pendiente_de_la_vista_previa(store):
    store.create("p1", kind="analyze", ext=".mp4")
    store.side_update("p1", "preview", state="done", file="a.mp4", music_pending=True)
    st = store.status("p1")
    assert st["preview_music_pending"] is True and st["preview_file"] == "a.mp4"
    store.side_update("p1", "proxy", state="done", url="/source/p1?proxy=1")
    assert store.status("p1")["proxy_url"] == "/source/p1?proxy=1"


def test_plan_y_su_aviso_se_guardan_juntos(store):
    """La foto del perfil llega en segundo plano: el plan nuevo y la tarea lateral que lo anuncia
    (profile_media) se escriben en la MISMA transaccion (antes, dos escrituras: un /status entre
    ambas veia el plan nuevo sin su aviso). Si otro escritor gano la revision, no se escribe nada."""
    store.create("j1", "analyze", ".mp4", plan={"version": 2, "fx": {}})
    rev = store.get("j1")["plan_rev"]
    ok, rev2 = store.update_plan("j1", {"version": 2, "fx": {"a": 1}}, rev,
                                 side=("profile_media", lambda r2: {"state": "applied", "plan_rev": r2}))
    assert ok and rev2 == rev + 1
    assert store.side_get("j1", "profile_media")["plan_rev"] == rev2 == store.get("j1")["plan_rev"]
    ok, cur = store.update_plan("j1", {"version": 2, "fx": {"a": 2}}, rev,       # revision vieja
                                side=("profile_media", lambda r2: {"state": "otro", "plan_rev": r2}))
    assert not ok and cur == rev2
    assert store.side_get("j1", "profile_media")["state"] == "applied"
    assert store.get("j1")["plan"]["fx"] == {"a": 1}
