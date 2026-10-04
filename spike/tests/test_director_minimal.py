"""Minimal de verdad en un montaje ("Armar con IA"): la edicion aprobada (cortes secos, sin correccion de
color, el piano suave) y nada que distraiga. Datos ficticios (tests/test_director.synthetic_plan)."""
import copy

import pytest

import test_director as TD
from motion import director as D
from motion import profile as PR
from test_profile_director import PROF


def _montage_plan():
    p = TD.synthetic_plan()
    p["montage"] = {"version": 1, "timeline": [], "slots": [], "script": {"lines": []}}
    return p


@pytest.fixture(scope="module")
def mn():
    return D.direct(_montage_plan(), style="minimal", intensity=0.75, seed=7, use_llm=False)


def test_minimal_en_montaje_sin_transiciones_efectos_ni_garabatos(mn):
    fx = mn["fx"]
    assert fx["transitions"] == []                                   # cortes secos
    assert [e for e in fx["effects"] if e.get("src") == "ai"] == []  # sin acercamientos, golpes ni sacudidas
    kinds = {g["kind"] for g in fx["graphics"]}
    assert not kinds & {"doodle", "icon", "sticker_text"}
    assert not [c for c in fx["cutaways"] if c.get("kind") in ("title_card", "photo", "self_broll")]
    # sin titulos de gancho ni efectos de sonido (v6: fx.text = [] y fx.sfx = []); el cierre se queda
    assert fx["text"] == [] and fx["sfx"] == []
    assert [g for g in fx["graphics"] if g["kind"] == "cta"]
    TD._check_catalog(mn)


def test_minimal_en_montaje_sin_titulos_ni_sonidos_aunque_el_guion_los_pida():
    """Con un guion que da gancho (pregunta, cifra) y un perfil con seguir: Aplicar IA en Minimal deja
    fx.text y fx.sfx vacios (nada de title_pop con word_stagger sobre el pecho ni whoosh/pop/clics)."""
    p = _montage_plan()
    p["profile"] = PR.normalize_profile(copy.deepcopy(PROF))
    for seed in (1, 7, 23):
        q = D.direct(copy.deepcopy(p), style="minimal", intensity=1.0, seed=seed, use_llm=False)
        assert q["fx"]["text"] == [] and q["fx"]["sfx"] == [], seed
        # en otro estilo el mismo plan si lleva titulos y sonidos (la regla es de Minimal)
    q = D.direct(copy.deepcopy(p), style="dinamico", intensity=1.0, seed=7, use_llm=False)
    assert q["fx"]["text"] and q["fx"]["sfx"]


def test_minimal_color_de_camara_y_musica_aprobada(mn):
    g = mn["fx"]["grade"]
    assert g["enabled"] is False and g["look"] == "natural" and g["match_shots"] is False
    assert g["vignette"] == 0.0 and g["sharpen"] == 0.0 and g["grain"] == 0.0
    m = mn["fx"]["music"]
    # el prompt aprobado EXACTO (sin ", for a video about <tema>": seria otra pista)
    assert m["prompt"] == ("minimal elegant instrumental, soft felt piano and warm airy pads, gentle pulse, "
                           "calm premium technology mood, no vocals")
    assert (m["mood"], m["bpm"], m["gain_db"], m["duck_db"]) == ("chill", 92, -19.0, -9.0)
    assert m["intro_fade"] == 0.8 and m["fade_out"] == 2.0 and m["ending"] == "fade"
    assert m["stops"] == [] and m["swells"] == [] and "drop_at" not in m


def test_minimal_de_v060_sin_tocar_toma_el_minimal_nuevo(mn):
    """Un proyecto dirigido en Minimal ANTES de v0.6.1 (correccion encendida, vineta 0.08, nitidez 0.1, el
    _grade_cfg de entonces) NO es color del usuario: al re-dirigir queda sin correccion, y al cambiar a
    otro estilo toma el color de ese estilo (no el de Minimal)."""
    old = {"enabled": True, "look": "natural", "match_shots": True, "vignette": 0.08, "grain": 0.0, "sharpen": 0.1}
    assert D._grade_tuned(old, "minimal") is False
    assert D._grade_tuned(dict(old, vignette=0.2), "minimal") is True        # tocado a mano: se queda
    p = copy.deepcopy(mn)
    p["fx"]["grade"] = dict(old)
    q = D.redirect(p, style="minimal", music_sync=False)
    assert q["fx"]["grade"]["enabled"] is False and q["fx"]["grade"]["vignette"] == 0.0
    q = D.redirect(copy.deepcopy(p), style="dinamico", music_sync=False)
    g = q["fx"]["grade"]
    assert {k: g[k] for k in D._grade_cfg("dinamico")} == D._grade_cfg("dinamico")


def test_el_color_que_prende_el_usuario_se_queda_al_redirigir(mn):
    p = copy.deepcopy(mn)
    p["fx"]["grade"] = dict(p["fx"]["grade"], enabled=True, look="cinematic")
    q = D.redirect(p, style="minimal", music_sync=False)
    assert q["fx"]["grade"]["enabled"] is True and q["fx"]["grade"]["look"] == "cinematic"
    # y apagado a mano en otro estilo se queda apagado
    p2 = D.direct(_montage_plan(), style="dinamico", use_llm=False)
    p2["fx"]["grade"]["enabled"] = False
    q2 = D.redirect(p2, style="dinamico", music_sync=False)
    assert q2["fx"]["grade"]["enabled"] is False


def test_sin_rotulo_cuando_el_perfil_lo_apaga():
    p = _montage_plan()
    p["profile"] = PR.normalize_profile(dict(copy.deepcopy(PROF), use={"lower_third": False, "follow_cta": True,
                                                                       "booking_cta": True, "end_card": True}))
    q = D.direct(p, style="minimal", use_llm=False)
    assert not [g for g in q["fx"]["graphics"] if g["kind"] == "lower_third"]
    assert [g for g in q["fx"]["graphics"] if g["kind"] == "cta"]          # el cierre (seguir) se queda
    p["profile"] = PR.normalize_profile(copy.deepcopy(PROF))
    q = D.direct(p, style="minimal", use_llm=False)
    assert [g for g in q["fx"]["graphics"] if g["kind"] == "lower_third"]  # con el rotulo prendido, si


def test_minimal_fuera_de_un_montaje_queda_como_siempre():
    p = D.direct(TD.synthetic_plan(), style="minimal", intensity=0.75, seed=7, use_llm=False)
    fx = p["fx"]
    # (en un video suelto Minimal sigue con sus acercamientos y su marcador; el color, el de camara)
    assert fx["effects"] and any(t["preset"] == "highlight_marker" for t in fx["text"]) or fx["transitions"]
    assert fx["grade"]["enabled"] is False


def test_minimal_prompt_aprobado_aunque_el_guion_tenga_tema():
    p = _montage_plan()
    q = D.direct(copy.deepcopy(p), style="minimal", use_llm=False)
    script = {"entities": {"role": "dentista"}, "topic": "nubis citas", "sents": []}
    from motion.director import _music_cfg, _Choreo  # noqa: F401
    m = D._music_cfg("minimal", script, type("Ch", (), {"music_stops": [], "music_swells": [], "drop_at": None})())
    assert m["prompt"] == D.STYLES["minimal"]["music"]["prompt"]
    m2 = D._music_cfg("dinamico", script, type("Ch", (), {"music_stops": [], "music_swells": [], "drop_at": None})())
    assert m2["prompt"].endswith(", for a video about nubis citas")      # los demas estilos siguen igual
    assert q["fx"]["music"]["prompt"] == D.STYLES["minimal"]["music"]["prompt"]


def test_boton_de_seguir_toma_el_color_de_tu_marca_puesto_despues():
    """Minimal (acento blanco): la IA pinta el boton «Seguir» con el oscuro de la paleta (#111111) y lo
    marca button_auto; al poner #0B6E4F en Estilo el boton lo toma (servidor y Studio), salvo que el
    usuario haya elegido el color del boton."""
    import json as _j
    import subprocess
    import shutil
    from pathlib import Path as _P
    p = _montage_plan()
    p["profile"] = PR.normalize_profile(copy.deepcopy(PROF))
    q = D.direct(p, style="minimal", use_llm=False)
    fol = [g for g in q["fx"]["graphics"] if g["kind"] == "cta" and g.get("variant", "follow") == "follow"]
    assert fol and all(g["button_color"] == "#111111" and g.get("button_auto") is True for g in fol)
    # en un estilo con acento de color el boton ya es el acento (sin marca automatica)
    q2 = D.direct(copy.deepcopy(p), style="dinamico", use_llm=False)
    f2 = [g for g in q2["fx"]["graphics"] if g["kind"] == "cta" and g.get("variant", "follow") == "follow"]
    assert f2 and all(not g.get("button_auto") for g in f2)
    # servidor (re-dirigir con el acento del proyecto: server._accent_apply)
    from test_server_setup import _run
    qa = copy.deepcopy(q)
    qa["fx"]["accent"] = "#0B6E4F"
    tmp = _P(__import__("tempfile").mkdtemp())
    (tmp / "plan.json").write_text(_j.dumps(qa))
    _run(f"""
import json, server as S
p = json.loads(open({str(tmp / "plan.json")!r}).read())
S._accent_apply(p)
cta = [g for g in p["fx"]["graphics"] if g["kind"] == "cta" and g.get("button_auto")]
assert cta and all(g["button_color"] == "#0B6E4F" for g in cta), cta
print("OK")
""", tmp)
    node = shutil.which("node")
    if node:
        fxcore = _P(D.__file__).parents[2] / "app" / "static" / "fxcore.js"
        mine = copy.deepcopy(q)
        g0 = next(g for g in mine["fx"]["graphics"] if g.get("button_auto"))
        user = dict(g0, id="u1", src="user", button_color="#222222")
        user.pop("button_auto", None)
        mine["fx"]["graphics"].append(user)
        js = ("const C=require(%s);const p=%s;C.applyAccent(p,'#0B6E4F',{palette:'#FFFFFF'});"
              "process.stdout.write(JSON.stringify(p.fx.graphics.filter(g=>g.kind==='cta').map(g=>[g.id,g.button_color])));"
              % (_j.dumps(str(fxcore)), _j.dumps(mine)))
        r = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        got = dict(_j.loads(r.stdout))
        assert got[g0["id"]] == "#0B6E4F" and got["u1"] == "#222222"


def test_el_cierre_de_un_montaje_conserva_la_cola_despues_de_la_ultima_palabra():
    """El corte de silencio del analisis se comia la cola que el montaje dejo para el cierre: el video
    terminaba 0.10 s despues de «Nos vemos.» y el «Siguiendo» casi no se veia (v6: 0.9 s mas a mano). En un
    montaje el corte de silencio FINAL empieza END_TAIL_KEEP s despues de la ultima palabra; el seguir llega
    hasta el final del video. Un video suelto queda igual."""
    p = _montage_plan()
    p["profile"] = PR.normalize_profile(copy.deepcopy(PROF))
    last = max(float(w["end"]) for ph in p["phrases"] for w in ph)
    p["cuts"] = [c for c in p.get("cuts") or [] if float(c.get("end") or 0) < last] + \
        [{"start": round(last + 0.1, 3), "end": float(p["duration"]), "enabled": True}]
    q = D.direct(copy.deepcopy(p), style="minimal", use_llm=False)
    tl = D._timeline(q)
    tail = tl.final_duration - tl.remap_clamped(last)
    assert tail >= min(D.END_TAIL_KEEP, float(p["duration"]) - last) - 1 / 30, tail
    fol = [g for g in q["fx"]["graphics"] if g["kind"] == "cta" and g.get("variant", "follow") == "follow"]
    assert fol and abs(tl.remap_clamped(float(fol[-1]["end"])) - tl.final_duration) < 0.05
    # un video suelto (sin montaje): el corte de silencio final no se toca
    s = TD.synthetic_plan()
    s["cuts"] = [{"start": round(last + 0.1, 3), "end": float(s["duration"]), "enabled": True}]
    assert D._keep_end_tail(s) is False and s["cuts"][0]["enabled"] is True
    # con mas aire del que hace falta, el corte solo se corre
    m = _montage_plan()
    m["duration"] = last + 3.0
    m["cuts"] = [{"start": round(last + 0.1, 3), "end": last + 3.0, "enabled": True}]
    assert D._keep_end_tail(m) is True
    assert m["cuts"][0]["enabled"] is True and m["cuts"][0]["start"] == pytest.approx(last + D.END_TAIL_KEEP, abs=1e-3)
