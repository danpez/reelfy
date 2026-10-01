"""Vista en vivo del Studio para los recursos flotantes (app/static/floatcard.js, window.FloatCard):
el port JS de motion/media.py da la MISMA geometria (tarjeta centrada, simetrica al pixel, arriba
de los subtitulos), la MISMA animacion (flotacion, vaiven 3D, paralaje) y la MISMA luz (franja,
irisado, franjas de la perspectiva) que el render, con cientos de casos al azar; mas sus pruebas
unitarias en node (tests/js/floatcard.test.js).

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
        for k in ("alpha", "scale", "dy", "tilt", "bg", "sway", "fdy", "lift"):
            assert _close(st[k], g["st"][k], 1e-9), (k, c, st, g["st"])
        assert _close(MF.kenburns_zoom(c["lt"], c["dur"], c["kb"] if c["kb"] is not None else MF.KENBURNS), g["kb"], 1e-9)
        it = {k: v for k, v in c["it"].items() if v is not None}
        assert _close(MF.speed_of(it), g["sp"], 1e-12), (c, g)
        assert _close(MF.src_time(it, c["lt"], c["sd"], 30), g["t"], 1e-9), (c, g)
        assert all(_close(a, b, 1e-9) for a, b in zip(MF.cover_rect(*c["cr"]), g["cr"])), (c, g)
        for (x, y), (gx, gy) in zip(MF.tilt_quad(*c["tq"]), g["tq"]):
            assert _close(x, gx, 1e-9) and _close(y, gy, 1e-9), (c, g)


LIVE_CONSTS = ("FLOAT_AMP", "FLOAT_T", "BREATH", "SWAY", "SWAY_T", "PERSP", "KB_LEAD", "PARALLAX",
               "PARALLAX_MAX", "SHEEN_A", "SHEEN_DELAY", "SHEEN_EVERY", "SHEEN_DUR", "SHEEN_MIN", "SHEEN_OUT",
               "SHEEN_EASE", "SHUTTER", "SHUTTER_N", "GLOSS_A", "HOLO_A", "RIM_A", "WARP_STEP", "WARP_MIN", "WARP_MAX")


@needs_node
def test_vida_luz_y_franjas_iguales_al_render(tmp_path):
    """La vida en reposo de la vista en vivo = la del render, paso a paso: paralaje, horario de la
    franja de luz, el degradado de la luz (con irisado y vaiven), las franjas de la perspectiva,
    el cuadrilatero animado y cuanto brillo lleva cada recurso."""
    rnd = random.Random(13)
    cases = []
    for _ in range(400):
        dur = round(rnd.uniform(0.4, 9.0), 3)
        cases.append({
            "lt": round(rnd.uniform(-0.3, dur + 0.3), 4), "dur": dur,
            "in": rnd.choice([None, 0.2, 0.35, 0.8]), "out": rnd.choice([None, 0.15, 0.25, 1.0]),
            "fl": rnd.choice([None, 0.0, 0.5, 1.0, 2.0]),
            "px": [round(rnd.uniform(-0.03, 0.03), 5), rnd.choice([384, 960, 1920]), rnd.choice([205, 513, 1026]),
                   rnd.choice([1.0, 1.024, 1.08, 1.3])],
            "ls": [rnd.choice([None, [round(rnd.uniform(-0.4, 1.4), 4), round(rnd.uniform(0, 1), 4)]]),
                   round(rnd.uniform(-6, 6), 3), rnd.choice([0.0, 0.3, 1.0, 1.5]), rnd.choice([0.0, 0.6, 1.0])],
            "ws": [rnd.choice([164, 821, 1642]), rnd.choice([205, 1026, 2053]), round(rnd.uniform(-16, 16), 3),
                   round(rnd.uniform(0.85, 1.05), 4)],
            "card": {"cy": round(rnd.uniform(0.3, 0.6), 5), "w": round(rnd.uniform(0.4, 0.76), 5),
                     "aspect": rnd.choice([0.5625, 0.8, 1.0])},
            "bw": rnd.choice([216, 540, 1080, 1081]),
            "it": rnd.choice([{}, {"kind": "image"}, {"kind": "video"}, {"kind": "video", "sheen": 0.4},
                              {"sheen": 2}, {"sheen": -1, "holo": 0.3}]),
            "baked": rnd.random() < 0.2})
    (tmp_path / "v.json").write_text(json.dumps(cases))
    got = _node_json(tmp_path, "v.js", "const d=JSON.parse(fs.readFileSync(%s,'utf8'));"
                     "process.stdout.write(JSON.stringify(d.map(c=>{const st=FC.cardAnim(c.lt,c.dur,c.in,c.out,c.fl);"
                     "return {px: FC.parallaxPx(...c.px), sh: FC.sheenState(c.lt,c.dur,c.in,c.out),"
                     "ls: FC.lightStops(...c.ls), ws: FC.warpStrips(...c.ws), q: FC.cardQuad(c.card,c.bw,c.bw*16/9,st),"
                     "am: FC.sheenAmount(c.it,c.baked), ho: FC.param(c.it,'holo'),"
                     "lsx: FC.lightStops(FC.sheenState(c.lt,c.dur,c.in,c.out), st.sway, FC.sheenAmount(c.it,c.baked), FC.param(c.it,'holo')),"
                     "la: FC.lightAt(c.lt,c.dur,st.sway,FC.sheenAmount(c.it,c.baked),FC.param(c.it,'holo'),c.in,c.out)};})));"
                     % json.dumps(str(tmp_path / "v.json")))
    n_light = n_blur = 0
    for c, g in zip(cases, got):
        inn = c["in"] if c["in"] is not None else MF.IN_DUR
        out = c["out"] if c["out"] is not None else MF.OUT_DUR
        st = MF.card_anim(c["lt"], c["dur"], inn, out, c["fl"] if c["fl"] is not None else 1.0)
        assert _close(MF.parallax_px(*c["px"]), g["px"], 1e-9), (c, g["px"])
        sh = MF.sheen_state(c["lt"], c["dur"], inn, out)
        assert (sh is None) == (g["sh"] is None), (c, sh, g["sh"])
        if sh is not None:
            assert _close(sh[0], g["sh"][0], 1e-9) and _close(sh[1], g["sh"][1], 1e-9), (c, sh, g["sh"])
        for mine, theirs in ((MF.light_stops(*c["ls"]), g["ls"]),
                             (MF.light_stops(sh, st["sway"], MF.sheen_amount(c["it"], c["baked"]),
                                             MF.param(c["it"], "holo")), g["lsx"])):
            assert len(mine) == len(theirs), (c, mine, theirs)
            n_light += bool(mine)
            for a, b in zip(mine, theirs):
                assert all(_close(x, y, 1e-7) for x, y in zip(a, b)), (c, a, b)
        # la luz que se DIBUJA (con desenfoque de movimiento): igual en la vista y en el render
        la = MF.light_at(c["lt"], c["dur"], st["sway"], MF.sheen_amount(c["it"], c["baked"]),
                         MF.param(c["it"], "holo"), inn, out)
        assert len(la) == len(g["la"]), (c, la, g["la"])
        n_blur += len(la) > 12
        for a, b in zip(la, g["la"]):
            assert all(_close(x, y, 1e-7) for x, y in zip(a, b)), (c, a, b)
        ws = MF.warp_strips(*c["ws"])
        assert len(ws) == len(g["ws"]), c
        for a, b in zip(ws, g["ws"]):
            assert all(_close(x, y, 1e-7) for x, y in zip(a, b)), (c, a, b)
        for (x, y), (gx, gy) in zip(MF.card_quad(c["card"], c["bw"], c["bw"] * 16 / 9, st), g["q"]):
            assert _close(x, gx, 1e-7) and _close(y, gy, 1e-7), (c, g["q"])
        assert _close(MF.sheen_amount(c["it"], c["baked"]), g["am"], 1e-12), c
        assert _close(MF.param(c["it"], "holo"), g["ho"], 1e-12), c
    assert n_light > 300                              # se probo luz de verdad (no solo listas vacias)
    assert n_blur > 20                                # y franjas promediadas en el obturador


@needs_node
def test_defaults_y_constantes_iguales(tmp_path):
    got = _node_json(tmp_path, "k.js", "process.stdout.write(JSON.stringify({d: FC.DEFAULTS, s: FC.STYLES, f: FC.FITS,"
                     "w: FC.CARD_W, a: FC.ASPECT, t: FC.ASPECT_TALL, r: FC.RADIUS, safe: FC.SAFE, live: FC.LIVE_DEFAULTS,"
                     "k: {%s}}));" % ",".join(f"{k}: FC.{k}" for k in LIVE_CONSTS))
    assert got["live"] == pytest.approx(MF.LIVE_DEFAULTS)
    for k in LIVE_CONSTS:
        assert got["k"][k] == pytest.approx(getattr(MF, k)), k
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


# ------------------------------------------------------------------ dibujo real en Chromium (opcional)
PW = __import__("os").environ.get("REELFY_PLAYWRIGHT")      # ruta a node_modules/playwright-core
needs_pw = pytest.mark.skipif(NODE is None or not PW, reason="sin playwright-core (REELFY_PLAYWRIGHT)")
_PW_JS = r"""
const { chromium } = require(process.env.REELFY_PLAYWRIGHT);
const fs = require("fs");
const d = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  const opt = { headless: true };
  if (process.env.REELFY_PW_EXE) opt.executablePath = process.env.REELFY_PW_EXE;
  else if (process.env.REELFY_PW_CHANNEL) opt.channel = process.env.REELFY_PW_CHANNEL;
  const b = await chromium.launch(opt);
  const pg = await b.newPage({ viewport: { width: d.W, height: d.H } });
  await pg.setContent(`<canvas id="c" width="${d.W}" height="${d.H}"></canvas>`);
  await pg.addScriptTag({ path: d.fc });
  const out = [];
  for (const t of d.ts) {
    out.push(await pg.evaluate(async ({ d, t }) => {
      const ld = s => new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = s; });
      const im = await ld(d.img), FC = window.FloatCard, g = document.getElementById("c").getContext("2d");
      const it = d.it, lt = t - it.start, dur = it.end - it.start, B = { x: 0, y: 0, w: d.W, h: d.H };
      const card = FC.cardLayout(im.naturalWidth, im.naturalHeight, B.w / B.h, FC.captionTop(d.cap, true, it.start, it.end, B.w / B.h),
        null, null, null, FC.captionBottom(d.cap, true, it.start, it.end, B.w / B.h));
      const st = FC.cardAnim(lt, dur, FC.param(it, "in_dur"), FC.param(it, "out_dur"), FC.param(it, "float"));
      g.fillStyle = d.bg; g.fillRect(0, 0, d.W, d.H);
      FC.drawCard(g, { src: im, w: im.naturalWidth, h: im.naturalHeight }, B, card, st, it, lt, dur);
      return document.getElementById("c").toDataURL("image/png");
    }, { d, t }));
  }
  await b.close();
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@needs_pw
def test_dibujo_de_la_vista_en_vivo_en_chromium_igual_al_render(tmp_path):
    """La tarjeta que dibuja floatcard.js en un canvas 2D REAL (Chromium) es la del render (Skia):
    misma perspectiva por franjas, Ken Burns con paralaje y franja de luz; diferencia media < 3
    niveles dentro de la tarjeta, con la franja de luz a media tarjeta y con el vaiven al maximo.
    Opcional (playwright-core no es dependencia): REELFY_PLAYWRIGHT=/ruta/playwright-core y
    REELFY_PW_EXE (ejecutable) o REELFY_PW_CHANNEL=chrome."""
    import base64
    import io

    import numpy as np
    from PIL import Image

    from motion import render as R
    from motion import video_io as V
    W, H = 540, 960
    bg = (32, 40, 48)
    src = tmp_path / "plano.mp4"
    subprocess.run([V.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x202830:s={W}x{H}:r=30:d=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv444p", "-qp", "0", str(src)], check=True)
    media = tmp_path / "media"
    media.mkdir()
    yy, xx = np.mgrid[0:1350, 0:1080]
    arr = np.zeros((1350, 1080, 3), np.uint8)
    arr[..., 0] = (60 + 150 * yy / 1350).astype(np.uint8)
    arr[..., 1] = (40 + 120 * xx / 1080).astype(np.uint8)
    arr[..., 2] = 140
    arr[(yy // 90) % 2 == 0] //= 2                                 # franjas: se nota el paralaje
    Image.fromarray(arr).save(media / "poster01.png")
    it = {"id": "md_p", "start": 0.5, "end": 5.5, "sid": "poster01", "path": None, "kind": "image", "fit": "hold",
          "speed": 1.0, "src_in": 0.0, "style": "float_card", "enabled": True, "locked": True, "src": "script",
          "name": "p", "shadow": 0, "holo": 0}
    plan = {"video": str(src), "duration": 6.0, "cuts": [], "version": 2, "phrases": [],
            "fx": {"captions": {"enabled": True}, "grade": {"enabled": False},
                   "music": {"enabled": False, "source": "none"}, "media": [it]}}
    o = {"W": W, "H": H, "dither": 0.0, "cache_dir": str(tmp_path / "cache"), "media_root": str(media),
         "enhance_audio": False}
    job = R.prepare(plan, o)["job"]
    lt_band = MF.IN_DUR + MF.SHEEN_DELAY + MF.SHEEN_DUR / 2
    ts = [round((0.5 + lt_band) * 30) / 30, round((0.5 + MF.SWAY_T * 3 / 4) * 30) / 30]
    # el fondo del render es el video plano oscurecido (sin textura: el desenfoque no cambia nada)
    py = [R.render_frame(plan, t, o)[..., :3].astype(int) for t in ts]
    bgc = py[0][4, 4]
    d = {"W": W, "H": H, "ts": ts, "it": it, "cap": job["captions"], "fc": str(STATIC / "floatcard.js"),
         "bg": "rgb(%d,%d,%d)" % tuple(int(v) for v in bgc),
         "img": "data:image/png;base64," + base64.b64encode((media / "poster01.png").read_bytes()).decode()}
    (tmp_path / "d.json").write_text(json.dumps(d))
    (tmp_path / "pw.js").write_text(_PW_JS)
    r = subprocess.run([NODE, str(tmp_path / "pw.js"), str(tmp_path / "d.json")], capture_output=True, text=True,
                       timeout=180, env=dict(__import__("os").environ))
    assert r.returncode == 0, r.stderr[-2000:]
    js = [np.asarray(Image.open(io.BytesIO(base64.b64decode(u.split(",")[1]))).convert("RGB")).astype(int)
          for u in json.loads(r.stdout)]
    for t, a, b in zip(ts, js, py):
        st = MF.card_anim(t - 0.5, 5.0)
        q = MF.card_quad(job["media"][0]["_card"], W, H, st)
        x0, x1 = int(max(q[0][0], q[3][0])) + 6, int(min(q[1][0], q[2][0])) - 6
        y0, y1 = int(q[0][1]) + 6, int(q[3][1]) - 6
        diff = np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean(axis=2)
        assert diff.mean() < 3.0 and np.percentile(diff, 95) < 10, (t, diff.mean(), np.percentile(diff, 95))
