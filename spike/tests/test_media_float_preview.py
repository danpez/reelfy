"""Vista en vivo del Studio para los recursos flotantes (app/static/floatcard.js, window.FloatCard):
el port JS de motion/media.py da la MISMA geometria (tarjeta centrada, simetrica al pixel, arriba
de los subtitulos) y la MISMA animacion que el render, con cientos de casos al azar; mas sus
pruebas unitarias en node (tests/js/floatcard.test.js).

Las pruebas que necesitan node se SALTAN si no esta instalado (la app no depende de node)."""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from motion import media as MF

SPIKE = Path(__file__).resolve().parents[1]
STATIC = SPIKE / "app" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node no esta instalado")


def _node_json(tmp_path, name, code):
    js = tmp_path / name
    js.write_text("const FC=require(%s);const fs=require('fs');" % json.dumps(str(STATIC / "floatcard.js")) + code)
    r = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _close(a, b, tol):
    return abs(float(a) - float(b)) <= tol


@needs_node
def test_floatcard_unitarias_node():
    r = subprocess.run([NODE, "--test", str(SPIKE / "tests" / "js" / "floatcard.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


@needs_node
def test_geometria_igual_al_render(tmp_path):
    rnd = random.Random(5)
    presets = list(MF.CAPTION_PRESET_LINES) + ["none"]
    cases = []
    for _ in range(400):
        cap = None if rnd.random() < 0.15 else {
            # tambien subtitulos SUBIDOS (0.12..0.45): la tarjeta va en la banda libre mas grande
            "y": round(rnd.uniform(0.12, 0.85), 4), "preset": rnd.choice(presets),
            "size": round(rnd.uniform(0.03, 0.11), 4), "enabled": rnd.random() > 0.05}
        if cap and rnd.random() < 0.3:
            cap["y_ranges"] = [{"start": round(rnd.uniform(0, 5), 3), "end": round(rnd.uniform(5, 10), 3),
                                "y": round(rnd.uniform(0.3, 0.8), 3), "hide": rnd.random() < 0.2}]
        if cap and rnd.random() < 0.2:
            cap["max_lines"] = rnd.choice([1, 2, 3])
        cases.append({
            "cap": cap, "on": rnd.random() > 0.1, "t0": round(rnd.uniform(0, 6), 3), "t1": round(rnd.uniform(6, 12), 3),
            "sw": rnd.choice([None, 1080, 1920, 720, 2160, 1080]), "sh": rnd.choice([None, 1350, 1080, 1280, 3840, 1920]),
            "ba": rnd.choice([9 / 16, 0.5625, 0.56]),
            "width": rnd.choice([None, None, round(rnd.uniform(0.3, 0.9), 3)]),
            "aspect": rnd.choice([None, None, round(rnd.uniform(0.4, 2.0), 3)]),
            "y": rnd.choice([None, None, round(rnd.uniform(0.0, 1.0), 3)]),
            "bw": rnd.choice([216, 540, 607, 608, 1080, 1081]),
        })
    (tmp_path / "c.json").write_text(json.dumps(cases))
    got = _node_json(tmp_path, "g.js", "const d=JSON.parse(fs.readFileSync(%s,'utf8'));"
                     "process.stdout.write(JSON.stringify(d.map(c=>{const top=FC.captionTop(c.cap,c.on,c.t0,c.t1,c.ba);"
                     "const bot=FC.captionBottom(c.cap,c.on,c.t0,c.t1,c.ba);"
                     "const card=FC.cardLayout(c.sw,c.sh,c.ba,top,c.width,c.aspect,c.y,bot);"
                     "return {top, bot, band: FC.cardBand(c.ba, top, bot), card, px: FC.cardPx(card, c.bw, c.bw / c.ba)};})));"
                     % json.dumps(str(tmp_path / "c.json")))
    for c, g in zip(cases, got):
        top = MF.caption_top(c["cap"], c["on"], c["t0"], c["t1"], c["ba"])
        bot = MF.caption_bottom(c["cap"], c["on"], c["t0"], c["t1"], c["ba"])
        assert (top is None) == (g["top"] is None) and (bot is None) == (g["bot"] is None), (c, top, g)
        if top is not None:
            assert _close(top, g["top"], 1e-9) and _close(bot, g["bot"], 1e-9), (c, top, bot, g)
        assert all(_close(a, b, 1e-9) for a, b in zip(MF.card_band(c["ba"], top, bot), g["band"])), (c, g)
        card = MF.card_layout(c["sw"], c["sh"], c["ba"], top, c["width"], c["aspect"], c["y"], bot)
        for k in ("cx", "cy", "w", "h", "aspect", "radius"):
            assert _close(card[k], g["card"][k], 1e-9), (k, c, card, g["card"])
        assert list(MF.card_px(card, c["bw"], c["bw"] / c["ba"])) == g["px"], (c, card, g)


@needs_node
def test_animacion_y_tiempos_iguales_al_render(tmp_path):
    rnd = random.Random(9)
    cases = [{"lt": round(rnd.uniform(-0.2, 6.5), 4), "dur": round(rnd.uniform(0.3, 6.0), 3),
              "in": rnd.choice([None, 0.2, 0.35, 0.8]), "out": rnd.choice([None, 0.15, 0.25, 1.0]),
              "fl": rnd.choice([None, 0.0, 1.0, 2.0]), "kb": rnd.choice([None, 0.0, 0.08, 0.3]),
              "it": {"fit": rnd.choice(["hold", "speed", "trim", None]), "speed": rnd.choice([None, 0.5, 1.3, 1.4, 9]),
                     "src_in": rnd.choice([None, 0.0, 1.25])},
              "sd": rnd.choice([None, 0.0, 2.5, 6.25]),
              "cr": [rnd.choice([1080, 1920, 3840]), rnd.choice([1350, 1080, 2160]), rnd.choice([600, 821]),
                     rnd.choice([750, 1026]), rnd.choice([1.0, 1.08])],
              "tq": [rnd.choice([100, 821]), rnd.choice([125, 1026]), round(rnd.uniform(-3, 16), 3),
                     round(rnd.uniform(0.85, 1.05), 4)]} for _ in range(400)]
    (tmp_path / "a.json").write_text(json.dumps(cases))
    got = _node_json(tmp_path, "a.js", "const d=JSON.parse(fs.readFileSync(%s,'utf8'));"
                     "process.stdout.write(JSON.stringify(d.map(c=>({st: FC.cardAnim(c.lt,c.dur,c.in,c.out,c.fl),"
                     "kb: FC.kenburnsZoom(c.lt,c.dur,c.kb), sp: FC.speedOf(c.it), t: FC.srcTime(c.it,c.lt,c.sd,30),"
                     "cr: FC.coverRect(...c.cr), tq: FC.tiltQuad(...c.tq)}))));" % json.dumps(str(tmp_path / "a.json")))
    for c, g in zip(cases, got):
        st = MF.card_anim(c["lt"], c["dur"], c["in"] if c["in"] is not None else MF.IN_DUR,
                          c["out"] if c["out"] is not None else MF.OUT_DUR,
                          c["fl"] if c["fl"] is not None else 1.0)
        for k in ("alpha", "scale", "dy", "tilt", "bg"):
            assert _close(st[k], g["st"][k], 1e-9), (k, c, st, g["st"])
        assert _close(MF.kenburns_zoom(c["lt"], c["dur"], c["kb"] if c["kb"] is not None else MF.KENBURNS), g["kb"], 1e-9)
        it = {k: v for k, v in c["it"].items() if v is not None}
        assert _close(MF.speed_of(it), g["sp"], 1e-12), (c, g)
        assert _close(MF.src_time(it, c["lt"], c["sd"], 30), g["t"], 1e-9), (c, g)
        assert all(_close(a, b, 1e-9) for a, b in zip(MF.cover_rect(*c["cr"]), g["cr"])), (c, g)
        for (x, y), (gx, gy) in zip(MF.tilt_quad(*c["tq"]), g["tq"]):
            assert _close(x, gx, 1e-9) and _close(y, gy, 1e-9), (c, g)


@needs_node
def test_defaults_y_constantes_iguales(tmp_path):
    got = _node_json(tmp_path, "k.js", "process.stdout.write(JSON.stringify({d: FC.DEFAULTS, s: FC.STYLES, f: FC.FITS,"
                     "w: FC.CARD_W, a: FC.ASPECT, t: FC.ASPECT_TALL, r: FC.RADIUS, safe: FC.SAFE}));")
    assert got["d"] == pytest.approx(MF.DEFAULTS) and got["s"] == list(MF.STYLES) and got["f"] == list(MF.FITS)
    assert got["w"] == pytest.approx(MF.CARD_W) and got["a"] == pytest.approx(MF.ASPECT)
    assert got["t"] == pytest.approx(MF.ASPECT_TALL) and got["r"] == pytest.approx(MF.RADIUS)
    from motion import ctx as C
    assert got["safe"] == pytest.approx(C.SAFE)


@needs_node
def test_tarjeta_debajo_de_subtitulos_subidos_igual_en_la_vista(tmp_path):
    """Subtitulos subidos (y = 0.2 / 0.35): la tarjeta va DEBAJO, del ancho minimo o mas, igual en la
    vista en vivo y en el render; con y = 0.5 arriba (la banda mas grande); nunca una miniatura."""
    ba = 9 / 16
    cases = [{"y": y} for y in (0.2, 0.35, 0.5, 0.72)]
    got = _node_json(tmp_path, "b.js", "const d=%s;process.stdout.write(JSON.stringify(d.map(c=>{"
                     "const t=FC.captionTop(c,true,0,1,%r),b=FC.captionBottom(c,true,0,1,%r);"
                     "return FC.cardLayout(1080,1350,%r,t,null,null,null,b);})));" % (json.dumps(cases), ba, ba, ba))
    for c, g in zip(cases, got):
        t, b = MF.caption_top(c, True, 0, 1, ba), MF.caption_bottom(c, True, 0, 1, ba)
        card = MF.card_layout(1080, 1350, ba, t, None, None, None, b)
        assert all(_close(card[k], g[k], 1e-9) for k in card), (c, card, g)
        assert card["w"] >= MF.CARD_W_MIN - 1e-9 and card["cx"] == 0.5
        top_edge, bot_edge = card["cy"] - card["h"] / 2, card["cy"] + card["h"] / 2
        if c["y"] < 0.45:
            assert top_edge >= b + MF.MARGIN - 1e-6, (c, card, b)     # debajo de los subtitulos
        else:
            assert bot_edge <= t - MF.MARGIN + 1e-6, (c, card, t)     # arriba
