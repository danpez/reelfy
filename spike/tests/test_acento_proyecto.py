"""COLOR DE ACENTO del proyecto (plan.fx.accent): amarillo #FFD23F por defecto, el que elija el usuario si
no. Nunca se pierde al re-dirigir, aplicar el perfil o sincronizar con la musica IA; el render lo usa
como acento de la paleta (lo que no trae color propio, una tarjeta del director, sale con el); y lo que
el usuario dejo con otro color a proposito (bloqueado o suyo) no se toca.

Solo datos SINTETICOS (plan del guion ficticio de test_director_joins; sin video real ni LLM)."""
import copy

import pytest

from motion import ctx as CX
from motion import director as D
from motion import graphics as G
from motion import render as R
from test_director_joins import montage_plan
from test_server_setup import _run

BLUE = "#3366FF"


@pytest.fixture(scope="module")
def directed_blue():
    p = D.direct(montage_plan(), style="dinamico", intensity=0.9, seed=7, use_llm=False, trim=False,
                 music_sync=False)
    p["fx"]["accent"] = BLUE
    p["fx"]["captions"]["accent"] = BLUE
    return p


def test_redirigir_y_perfil_conservan_el_acento(directed_blue):
    """director.redirect / apply_profile / direct reconstruyen fx: el acento del proyecto pasa al plan
    nuevo y a los subtitulos nuevos (antes volvia el amarillo)."""
    for out in (D.redirect(copy.deepcopy(directed_blue), music_sync=False),
                D.apply_profile(copy.deepcopy(directed_blue), None),
                D.direct(copy.deepcopy(directed_blue), style="elegante", intensity=0.9, seed=7,
                         use_llm=False, trim=False, music_sync=False)):
        assert out["fx"]["accent"] == BLUE
        assert out["fx"]["captions"]["accent"] == BLUE
        assert out["fx"]["captions"].get("highlight") in (None, BLUE)
    # sin acento propio: todo como siempre (el del estilo)
    p = copy.deepcopy(directed_blue)
    p["fx"].pop("accent")
    out = D.redirect(p, music_sync=False)
    assert "accent" not in out["fx"] and out["fx"]["captions"]["accent"] == "#FFD23F"


def test_render_usa_el_acento_del_proyecto_en_la_paleta():
    """Lo que no trae color propio (una tarjeta del director sin 'accent') sale con el acento del
    proyecto en el export, como en la vista del Studio."""
    assert CX.FrameCtx(style="dinamico").palette["accent"] == "#FFD23F"
    ctx = CX.FrameCtx(style="dinamico", accent=BLUE)
    assert ctx.palette["accent"] == BLUE and ctx.palette["accent2"] == CX.FrameCtx().palette["accent2"]
    card = {"kind": "card", "theme": "light", "color": "#FFFFFF"}
    th = G.card_theme(card, ctx)
    assert th["accent"] == BLUE and th["badge"] == BLUE
    assert G.card_theme(card, CX.FrameCtx(style="dinamico"))["accent"] == "#FFD23F"
    assert G.card_theme(dict(card, accent="#11AA44"), ctx)["accent"] == "#11AA44"   # el suyo manda
    assert R.project_accent({"fx": {"accent": "3366ff"}}) == BLUE
    assert R.project_accent({"fx": {"accent": "azul"}}) is None and R.project_accent({}) is None


def test_trabajo_de_render_lleva_el_acento(tmp_path):
    from test_media_float import _opts, _plan, _src
    src = _src(tmp_path / "fuente.mp4", dur=2.0)
    p = _plan(src, dur=2.0)
    assert R.prepare(p, _opts(tmp_path))["job"]["accent"] is None
    p["fx"]["accent"] = BLUE
    job = R.prepare(p, _opts(tmp_path))["job"]
    assert job["accent"] == BLUE
    r = R.Compositor(job) if hasattr(R, "Compositor") else None
    if r is not None and hasattr(r, "make_ctx"):
        assert r.make_ctx(0).palette["accent"] == BLUE


def test_servidor_conserva_el_acento_y_respeta_lo_del_usuario(tmp_path):
    """En proceso aparte: (1) PUT /plan con otro perfil (_reapply_profile) y la sincronia con la musica
    IA (_music_resync) no pierden fx.accent aunque el director devuelva un plan sin el; (2) al dirigir,
    lo BLOQUEADO o del usuario conserva su color (solo lo de la IA pasa al acento del proyecto)."""
    _run('''
import copy, threading
import server as S
import jobstore
from motion import director

def plan(acc=None, prof=None):
    fx = {"captions": {"accent": "#FFD23F"}, "text": [], "cutaways": [],
          "graphics": [{"id": "g_ia", "kind": "card", "accent": "#FFD23F", "start": 1, "end": 2, "x": 0.5},
                       {"id": "g_lock", "kind": "card", "accent": "#FFD23F", "locked": True, "start": 3, "end": 4, "x": 0.5},
                       {"id": "g_user", "kind": "card", "accent": "#FFD23F", "src": "user", "start": 5, "end": 6, "x": 0.5}]}
    if acc:
        fx["accent"] = acc
    p = {"version": 2, "director": {"style": "dinamico"}, "fx": fx, "shots": []}
    if prof:
        p["profile"] = prof
    return p

# (2) solo lo de la IA cambia de color
out = S._accent_apply(plan(acc="#3366FF"))
g = {x["id"]: x["accent"] for x in out["fx"]["graphics"]}
assert g == {"g_ia": "#3366FF", "g_lock": "#FFD23F", "g_user": "#FFD23F"}, g

# (1a) _reapply_profile: el director (simulado) devuelve un plan SIN acento
def sin_acento(p, prof):
    q = copy.deepcopy(p)
    q["fx"].pop("accent", None)
    q["fx"]["captions"] = {"accent": "#FFD23F"}
    if prof:
        q["profile"] = prof
    return q
director.apply_profile = sin_acento
director.apply_profile_media = lambda p, prof, old=None: (p, [])
jobstore.create("j1", kind="compose", ext=".mp4", params={}, plan=plan(acc="#3366FF", prof={"name": "Ana"}), phase="review")
jobstore.update("j1", state="review")
new, applied = S._reapply_profile("j1", plan(acc="#3366FF", prof={"name": "Beto"}))
assert applied and new["fx"]["accent"] == "#3366FF" and new["fx"]["captions"]["accent"] == "#3366FF", new["fx"]

# (1b) _music_resync: re-dirige con la pista IA (simulado sin acento) y guarda: el acento sigue
jobstore.create("j2", kind="compose", ext=".mp4", params={}, plan=plan(acc="#3366FF"), phase="review")
jobstore.update("j2", state="review")
_p, rev = jobstore.get_plan("j2")
def redirect(p, *a, **k):
    q = copy.deepcopy(p)
    q["fx"].pop("accent", None)
    q["fx"]["captions"] = {"accent": "#FFD23F"}
    q["director"]["music_grid"] = {"bpm": 100, "drop": 0}
    return q
director.redirect = redirect
S._kick_analysis = lambda *a, **k: None
class Tick:
    def __init__(self):
        self.last = {}
    def set(self, **k):
        self.last = k
tick = Tick()
S._music_resync("j2", threading.Event(), tick, {"rev": rev, "orig": _p})
saved, _r = jobstore.get_plan("j2")
assert tick.last.get("state") == "synced", tick.last
assert saved["fx"]["accent"] == "#3366FF" and saved["fx"]["captions"]["accent"] == "#3366FF", saved["fx"]
print("OK")
''', tmp_path)
