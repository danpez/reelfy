"""Vista en vivo del Studio para la grabacion de pantalla sincronizada (app/static/screencard.js,
window.ScreenCard): el port JS de motion/screen.py da la MISMA geometria (ventana centrada, simetrica al
pixel, arriba de los subtitulos, con la proporcion de lo que hay dentro de su marco), la MISMA animacion
y la MISMA sincronia que el render,
con cientos de casos al azar; los defaults del esquema son los del motor; y sus pruebas unitarias en
node (tests/js/screencard.test.js). Las que necesitan node se SALTAN sin node."""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from motion import media as MF
from motion import schema as S
from motion import screen as SC

SPIKE = Path(__file__).resolve().parents[1]
STATIC = SPIKE / "app" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node no esta instalado")


def _node(tmp_path, code):
    js = tmp_path / "p.js"
    js.write_text("const SC=require(%s);const FC=require(%s);const fs=require('fs');" % (
        json.dumps(str(STATIC / "screencard.js")), json.dumps(str(STATIC / "floatcard.js"))) + code)
    r = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _close(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


@needs_node
def test_unitarias_node():
    r = subprocess.run([NODE, "--test", str(SPIKE / "tests" / "js" / "screencard.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


@needs_node
def test_geometria_animacion_y_sincronia_iguales_al_render(tmp_path):
    rnd = random.Random(9)
    cases = []
    for _ in range(400):
        cap = None if rnd.random() < 0.15 else {"y": round(rnd.uniform(0.3, 0.85), 4),
                                                "preset": rnd.choice(list(MF.CAPTION_PRESET_LINES)),
                                                "size": round(rnd.uniform(0.03, 0.09), 4)}
        n = rnd.randint(1, 4)
        t, ps = rnd.uniform(0, 5), []
        for _k in range(n):
            L = rnd.uniform(0.5, 8)
            ps.append([round(t, 3), round(t + L, 3), round(rnd.uniform(0, 60), 3)])
            t += L + (0 if rnd.random() < 0.6 else rnd.uniform(0.1, 3))
        it = {"start": round(rnd.uniform(0, 6), 3), "end": round(rnd.uniform(6, 40), 3),
              "sync": {"offset": round(rnd.uniform(-90, 90), 3), "rate": rnd.choice([1.0, 1.0001, 0.99995]), "pieces": ps},
              "motion": rnd.choice([None, "cut", "calm", "lively", "otro"]),
              "crop": rnd.choice([None, None, [0.028, 0.017, 0.972, 0.973], [0.0, 0.1, 1.0, 0.9], [0.2, 0.2, 0.25, 0.9],
                                  [-0.1, 0.0, 1.2, 1.0], "x"])}
        cases.append({"cap": cap, "on": rnd.random() > 0.1, "sw": rnd.choice([None, 670, 1920, 1080, 4096, 300]),
                      "sh": rnd.choice([None, 1436, 1080, 1920, 2296, 900]), "ba": rnd.choice([9 / 16, 0.56]),
                      "width": rnd.choice([None, None, round(rnd.uniform(0.25, 0.9), 3)]),
                      "bw": rnd.choice([216, 540, 1080, 1081, 2160]), "it": it,
                      "ts": [round(rnd.uniform(-2, 45), 3) for _ in range(6)] + [p[0] for p in ps] + [p[1] for p in ps],
                      "lt": round(rnd.uniform(0, 12), 3), "dur": round(rnd.uniform(0.3, 12), 3),
                      "style": rnd.choice(["minimal", "dinamico", "elegante", "energetico", None])})
    (tmp_path / "c.json").write_text(json.dumps(cases))
    got = _node(tmp_path, """const d=JSON.parse(fs.readFileSync(%s,'utf8'));
process.stdout.write(JSON.stringify(d.map(c=>{const top=FC.captionTop(c.cap,c.on,0,10,c.ba), bot=FC.captionBottom(c.cap,c.on,0,10,c.ba);
const crop=SC.cropOf(c.it); const card=SC.layout(c.sw,c.sh,c.ba,top,bot,c.width,crop); const bh=c.bw/c.ba; const m=SC.motionOf(c.it,c.style);
const st=SC.anim(c.lt,c.dur,m);
return {crop, card, px: FC.cardPx(card,c.bw,bh), m, st, band: SC.band(c.ba,top,bot),
 sync: c.ts.map(t=>SC.syncTime(c.it,t)), cov: SC.coverage(c.it)};})));""" % json.dumps(str(tmp_path / "c.json")))
    for c, g in zip(cases, got):
        top = MF.caption_top(c["cap"], c["on"], 0, 10, c["ba"])
        bot = MF.caption_bottom(c["cap"], c["on"], 0, 10, c["ba"])
        crop = SC.crop_of(c["it"])
        assert (crop is None and g["crop"] is None) or all(_close(a, b) for a, b in zip(crop, g["crop"])), (c["it"], g["crop"])
        assert all(_close(a, b) for a, b in zip(SC.band(c["ba"], top, bot), g["band"]))
        card = SC.layout(c["sw"], c["sh"], c["ba"], top, bot, c["width"], crop)
        for k in ("cx", "cy", "w", "h", "aspect", "radius"):
            assert _close(card[k], g["card"][k]), (k, c, card, g["card"])
        bh = c["bw"] / c["ba"]
        assert list(MF.card_px(card, c["bw"], bh)) == g["px"]
        m = SC.motion_of(c["it"], c["style"])
        assert m == g["m"]
        st = SC.anim(c["lt"], c["dur"], m)
        for k in ("alpha", "scale", "dy", "tilt", "bg", "sway", "fdy", "lift"):
            assert _close(st[k], g["st"][k], 1e-9), (k, c, st[k], g["st"][k])
        for t, gs in zip(c["ts"], g["sync"]):
            assert _close(SC.sync_time(c["it"], t), gs, 1e-6), (t, c["it"], gs)
        cov = SC.coverage(c["it"])
        assert (cov is None and g["cov"] is None) or all(_close(a, b) for a, b in zip(cov, g["cov"]))


def test_defaults_del_esquema_espejo_del_motor():
    s = S.schema()
    ps = {p["k"]: p for p in s["tracks"]["media"]["items"]["screen"]["params"]}
    for k, v in SC.DEFAULTS.items():
        assert ps[k]["default"] == v, (k, ps[k]["default"], v)
    assert ps["motion"]["opts"] == list(SC.MOTIONS)
    assert ps["cam"]["internal"] and ps["cam_name"]["internal"] and ps["crop"]["internal"]
    assert "speaker" not in ps and "in_dur" not in ps          # sin controles muertos


@needs_node
def test_constantes_del_port(tmp_path):
    got = _node(tmp_path, "process.stdout.write(JSON.stringify({d:SC.DEFAULTS,r:SC.RADIUS,c:SC.CALM_FROM,cr:SC.CALM_RISE,"
                "lf:SC.LIVELY_FLOAT,amin:SC.ASPECT_MIN,amax:SC.ASPECT_MAX,top:SC.TOP,ind:SC.IN_DUR,m:SC.MOTIONS,mb:SC.MOTION_BY_STYLE}))")
    assert got["d"] == SC.DEFAULTS
    assert (got["r"], got["c"], got["cr"], got["lf"]) == (SC.RADIUS, SC.CALM_FROM, SC.CALM_RISE, SC.LIVELY_FLOAT)
    assert (got["amin"], got["amax"], got["top"], got["ind"]) == (SC.ASPECT_MIN, SC.ASPECT_MAX, SC.TOP, SC.IN_DUR)
    assert got["m"] == list(SC.MOTIONS) and got["mb"] == SC.MOTION_BY_STYLE


def test_mismo_look_que_la_tarjeta_flotante_aprobada():
    """La ventana usa el look de la tarjeta flotante de la edicion aprobada (fondo calmado, sombra suave,
    borde claro, esquinas 5.5 %) y, en Minimal, aparece con el corte (sin animacion)."""
    for k in ("blur", "dim", "shadow"):
        assert SC.DEFAULTS[k] == MF.DEFAULTS[k]
    assert SC.DEFAULTS["radius"] == MF.RADIUS and SC.DEFAULTS["border"] is True
    assert SC.motion_of({}, "minimal") == "cut" and SC.motion_of({}, None) == "cut"
    assert SC.motion_of({}, "elegante") == "calm" and SC.motion_of({}, "dinamico") == "lively"
    st = SC.anim(0.0, 10.0, "cut")
    assert (st["alpha"], st["scale"], st["dy"], st["bg"]) == (1.0, 1.0, 0.0, 1.0)


def test_version_del_asset_y_orden():
    """screencard.js va una vez con ?v=<sha1 de su contenido>, despues de floatcard.js (lo usa) y antes de fx.js."""
    import hashlib
    import re
    html = (STATIC / "index.html").read_text()
    h = hashlib.sha1((STATIC / "screencard.js").read_bytes()).hexdigest()[:8]
    assert re.findall(r'/static/screencard\.js\?v=([0-9a-f]+)', html) == [h]
    assert html.index("/static/floatcard.js") < html.index("/static/screencard.js") < html.index("/static/fx.js")
