"""Videos de apoyo en fragmentos: el port del Studio (app/static/reelcard.js) es igual al render
(motion/reel.py) en la tarjeta de la ventana y el recorte de pantalla completa; el esquema trae sus
ajustes con los defaults del render; y el Studio los dibuja, los inspecciona y los edita (cableado)."""
import hashlib
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from motion import media as MF
from motion import reel as RL
from motion import schema as S
from motion import screen as SC

NODE = shutil.which("node")
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_defaults_del_esquema_igual_al_render():
    ps = {p["k"]: p for p in S.schema()["tracks"]["media"]["items"]["reel"]["params"]}
    for k, v in RL.DEFAULTS.items():
        assert k in ps, k
        d = ps[k]["default"]
        assert (d is v) if isinstance(v, bool) or v is None else d == pytest.approx(v), k
    assert ps["layout"]["opts"] == list(RL.LAYOUTS) and ps["motion"]["opts"] == list(SC.MOTIONS)
    assert ps["width"]["max"] == pytest.approx(0.76)
    # el mismo look de la tarjeta flotante aprobada
    for k in ("blur", "dim", "shadow"):
        assert RL.DEFAULTS[k] == MF.DEFAULTS[k]
    assert "broll" in S.ITEM_SRCS and "reel" in S.MEDIA_STYLES


@pytest.mark.skipif(not NODE, reason="sin node")
def test_ventana_igual_en_el_studio(tmp_path):
    rnd = random.Random(11)
    cases = []
    for _ in range(80):
        it = {"pieces": [{"in": 0, "out": 1, "win": rnd.choice([None, [0.3, 0.05, 0.7, 0.95], [0.1, 0.2, 0.9, 0.6]])}],
              "width": rnd.choice([None, 0.5, 0.76, 0.9])}
        cases.append({"it": it, "w": rnd.choice([1712, 640, 1080, 4096]), "h": rnd.choice([960, 360, 1920, 2296]),
                      "ba": rnd.choice([9 / 16, 1.0]), "ct": rnd.choice([None, 0.6, 0.66, 0.4]),
                      "cb": rnd.choice([None, 0.79, 0.7])})
    want = [RL.window_layout(c["it"], c["w"], c["h"], c["ba"], c["ct"], c["cb"]) for c in cases]
    js = tmp_path / "w.js"
    js.write_text(f"""
const R = require({json.dumps(str(STATIC / "reelcard.js"))});
const cases = {json.dumps(cases)};
console.log(JSON.stringify(cases.map(c => R.windowLayout(c.it, c.w, c.h, c.ba, c.ct, c.cb))));
""")
    r = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    for c, a, b in zip(cases, want, json.loads(r.stdout)):
        for k in ("cx", "cy", "w", "h", "aspect"):
            assert a[k] == pytest.approx(b[k], abs=1e-6), (c, k, a, b)


def test_recorte_de_ventana_crece_a_la_tarjeta_sin_cortar_al_sujeto():
    """Una toma cuyo recorte tiene otra forma que la tarjeta (un logo casi cuadrado en una tarjeta 16:9
    que abrio otra toma) se AGRANDA a la proporcion de la tarjeta: todo su recorte sigue a la vista (antes
    la normalizacion lo cubria y se comia el logo por arriba y por abajo)."""
    p = {"win": [0.42, 0.38, 0.59, 0.61]}
    v = RL.win_view(p, 1712, 960, 16 / 9)
    assert v[0] <= 0.42 and v[1] <= 0.38 + 1e-6 and v[2] >= 0.59 and v[3] >= 0.61 - 1e-6
    assert ((v[2] - v[0]) * 1712) / ((v[3] - v[1]) * 960) == pytest.approx(16 / 9, rel=1e-3)
    # pegado a la orilla: se corre, no se sale
    v = RL.win_view({"win": [0.0, 0.0, 0.1, 0.4]}, 1600, 900, 16 / 9)
    assert v[0] == 0.0 and v[1] == 0.0 and v[2] <= 1.0 and v[3] <= 1.0
    # sin recorte: el cuadro completo (y lo que no alcanza lo cubre la normalizacion)
    assert RL.win_view({"win": None}, 1712, 960, 1.0) is None
    assert RL.win_view({"win": [0.1, 0.1, 0.9, 0.9]}, 1600, 900, 16 / 9) is not None


@pytest.mark.skipif(not NODE, reason="sin node")
def test_recorte_de_ventana_igual_en_el_studio(tmp_path):
    rnd = random.Random(5)
    cases = []
    for _ in range(120):
        x0, y0 = rnd.uniform(0, 0.8), rnd.uniform(0, 0.8)
        win = rnd.choice([None, [x0, y0, min(1.0, x0 + rnd.uniform(0.1, 0.9)), min(1.0, y0 + rnd.uniform(0.1, 0.9))]])
        cases.append({"p": {"win": win}, "w": rnd.choice([1712, 1080, 640]), "h": rnd.choice([960, 1920, 360]),
                      "a": rnd.choice([16 / 9, 1.0, 0.8, 1.29])})
    want = [RL.win_view(c["p"], c["w"], c["h"], c["a"]) for c in cases]
    js = tmp_path / "v.js"
    js.write_text(f"""
const R = require({json.dumps(str(STATIC / "reelcard.js"))});
const cases = {json.dumps(cases)};
console.log(JSON.stringify(cases.map(c => R.winView(c.p, c.w, c.h, c.a))));
""")
    r = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    for c, a, b in zip(cases, want, json.loads(r.stdout)):
        assert (a is None) == (b is None), (c, a, b)
        if a is not None:
            assert a == pytest.approx(b, abs=1e-6), (c, a, b)


def test_version_del_asset_y_orden():
    """reelcard.js va una vez con ?v=<sha1 de su contenido>, despues de floatcard.js y screencard.js (los
    usa) y antes de fx.js."""
    html = (STATIC / "index.html").read_text()
    h = hashlib.sha1((STATIC / "reelcard.js").read_bytes()).hexdigest()[:8]
    assert re.findall(r'/static/reelcard\.js\?v=([0-9a-f]+)', html) == [h]
    assert html.index("/static/floatcard.js") < html.index("/static/screencard.js") < \
        html.index("/static/reelcard.js") < html.index("/static/fx.js")


def test_studio_cableado():
    """El Studio dibuja el fragmento (las dos <video> que se turnan, pantalla completa o ventana con la
    toma desenfocada detras), lo inspecciona (por que, formato, tomas con Cambiar/Quitar, tiempo, estilo) y
    el ensamble muestra los videos de apoyo y manda si se usan."""
    fx = (STATIC / "fx.js").read_text()
    for s in ("function drawReelItem", "R.fit(ps, L, 30)", 'slot: "r" + (k % 2)', "R.drawFull(", "R.drawWindow(",
              "R.winView(p, w0, h0, card.aspect)",
              "function inspectReel", "R.relayout(pl, it, alt", "R.options(pl, it, lay", "R.swap(it, j, sh2",
              "/broll/${encodeURIComponent(it.sid)}/shot/", 'id: "media.reel.shots"', 'id: "media.reel"',
              'mc.style === "reel"', "o.bgLum = r => canvasLum(c, r)"):
        assert s in fx, s
    html = (STATIC / "index.html").read_text()
    for s in ("function mtBrollHtml", "function mtBrollToggle", "body.broll=ASM.montage.broll.sources.map",
              "h+=mtBrollHtml(m);", 'data-act="boff"', "brollEdits"):
        assert s in html, s
    cap = (STATIC / "capdraw.js").read_text()
    assert "C.captionContrast" in cap and "o.bgLum" in cap
