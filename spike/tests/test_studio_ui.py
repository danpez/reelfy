"""Pruebas del Studio (app/static): logica pura en node, espejo JS de motion.timeline,
sintaxis de los scripts y coherencia entre el HTML, fx.js y el esquema del motor.

Las pruebas que necesitan node se SALTAN si no esta instalado (la app no depende de node)."""
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1]
STATIC = SPIKE / "app" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node no esta instalado")


def _inline_script() -> str:
    html = (STATIC / "index.html").read_text()
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(blocks) == 1, "index.html debe tener un solo <script> en linea"
    return blocks[0]


@needs_node
def test_fxcore_unitarias_node():
    r = subprocess.run([NODE, "--test", str(SPIKE / "tests" / "js" / "fxcore.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


@needs_node
def test_sintaxis_de_los_scripts(tmp_path):
    inline = tmp_path / "inline.js"
    inline.write_text(_inline_script())
    for f in (STATIC / "fxcore.js", STATIC / "capdraw.js", STATIC / "fx.js", STATIC / "estilo.js", STATIC / "profile.js", inline):
        r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"{f.name}: {r.stderr[-2000:]}"


def _random_plans(n=40, seed=3):
    rnd = random.Random(seed)
    plans = []
    for _ in range(n):
        dur = round(rnd.uniform(8, 90), 3)
        cuts = []
        for _ in range(rnd.randint(0, 9)):
            s = round(rnd.uniform(0, dur), 3)
            e = round(min(dur, s + rnd.choice([0.03, 0.1, 0.2, 0.6, 1.5, 4.0]) * rnd.uniform(0.5, 1.5)), 3)
            cuts.append({"start": s, "end": e, "enabled": rnd.random() > 0.2})
        shots = [{"start": 0.0}] + [{"start": round(rnd.uniform(0.5, dur - 0.5), 3)} for _ in range(rnd.randint(0, 4))]
        plans.append({"duration": dur, "cuts": cuts, "shots": sorted(shots, key=lambda s: s["start"])})
    return plans


@needs_node
def test_timeline_js_igual_a_python(tmp_path):
    """El tiempo final que calcula la UI (para /frame y la musica) es el del render."""
    from motion import timeline as T

    plans = _random_plans()
    probes = []
    expected = []
    for p in plans:
        tl = T.Timeline.from_plan(p, fps=30)
        ts = [round(i * p["duration"] / 37.0, 4) for i in range(38)]
        probes.append(ts)
        expected.append({"keeps": [list(k) for k in tl.keeps], "final": tl.final_duration,
                         "clamped": [tl.remap_clamped(t) for t in ts],
                         "inverse": [tl.inverse(t) for t in ts]})
    (tmp_path / "in.json").write_text(json.dumps({"plans": plans, "probes": probes}))
    script = tmp_path / "run.js"
    script.write_text(
        "const C=require(%s);const fs=require('fs');" % json.dumps(str(STATIC / "fxcore.js"))
        + "const d=JSON.parse(fs.readFileSync(%s,'utf8'));" % json.dumps(str(tmp_path / "in.json"))
        + "const out=d.plans.map((p,i)=>{const tl=C.timelineFromPlan(p,30);"
          "return {keeps:tl.keeps,final:tl.finalDuration,clamped:d.probes[i].map(t=>tl.remapClamped(t)),"
          "inverse:d.probes[i].map(t=>tl.inverse(t))};});"
        + "process.stdout.write(JSON.stringify(out));")
    r = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    for i, (g, e) in enumerate(zip(got, expected)):
        assert len(g["keeps"]) == len(e["keeps"]), (i, g["keeps"], e["keeps"])
        for a, b in zip(g["keeps"], e["keeps"]):
            assert abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6, (i, a, b)
        assert abs(g["final"] - e["final"]) < 1e-6
        for a, b in zip(g["clamped"], e["clamped"]):
            assert abs(a - b) < 1e-6, (i, a, b)
        for a, b in zip(g["inverse"], e["inverse"]):
            assert abs(a - b) < 1e-6, (i, a, b)


@needs_node
def test_aparicion_de_palabras_js_igual_a_text(tmp_path):
    """La vista previa en vivo revela las palabras de un titulo al mismo tiempo que text.py."""
    from motion import text as TX

    rnd = random.Random(11)
    # vocabulario ficticio: acentos, signos de apertura, MAYUSCULAS, puntuacion y numeros con coma
    vocab = ["Hola", "cuando", "miras", "la", "ventana", "¿ves", "el", "BRILLO?", "cien", "pesos.",
             "Calle", "tres", "bloques", "niño", "canción", "1234,", "un", "enlace", "aquí", "abajo"]
    cases = []
    for _ in range(60):
        words = [rnd.choice(vocab) for _ in range(rnd.randint(1, 8))]
        spoken = None
        if rnd.random() < 0.75:
            t, spoken = rnd.uniform(0, 0.3), []
            for w in (words if rnd.random() < 0.6 else [rnd.choice(vocab) for _ in range(rnd.randint(1, 9))]):
                spoken.append({"text": w, "start": round(t, 3), "end": round(t + 0.2, 3)})
                t += rnd.uniform(0.08, 0.5)
        dur = round(rnd.uniform(0.6, 4.0), 3)
        step = rnd.choice([0.08, 0.12])
        line_of = sorted(rnd.randint(0, 2) for _ in words)
        exp = TX._unit_times([TX._norm(w) for w in words], spoken, dur, step, line_of)
        cases.append({"words": words, "spoken": spoken, "dur": dur, "step": step, "line_of": line_of, "exp": exp})
    (tmp_path / "c.json").write_text(json.dumps(cases, ensure_ascii=False))
    script = tmp_path / "u.js"
    script.write_text(
        "const C=require(%s);const fs=require('fs');" % json.dumps(str(STATIC / "fxcore.js"))
        + "const cs=JSON.parse(fs.readFileSync(%s,'utf8'));" % json.dumps(str(tmp_path / "c.json"))
        + "process.stdout.write(JSON.stringify(cs.map(c=>C.unitTimes(c.words.map(C.normWord),"
          "c.spoken?c.spoken.map(w=>({norm:C.normWord(w.text),start:w.start})):null,c.dur,c.step,c.line_of))));")
    r = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    for c, got in zip(cases, json.loads(r.stdout)):
        assert len(got) == len(c["exp"])
        for a, b in zip(got, c["exp"]):
            assert abs(a - b) < 1e-9, (c, got)


def test_ids_referenciados_existen():
    """Cada $("#id") / getElementById del Studio apunta a un elemento que existe en el HTML
    o que el propio codigo crea (id="..." en plantillas)."""
    html = (STATIC / "index.html").read_text()
    fxjs = ((STATIC / "fx.js").read_text() + (STATIC / "profile.js").read_text() + (STATIC / "estilo.js").read_text()
            + (STATIC / "estilo_proyecto.js").read_text() + (STATIC / "estilo_catalog.js").read_text())
    declared = set(re.findall(r'id="([A-Za-z][\w-]*)"', html)) | set(re.findall(r'id="([A-Za-z][\w-]*)"', fxjs))
    declared |= set(re.findall(r'\.id\s*=\s*"([\w-]+)"', html + fxjs))
    used = set(re.findall(r'\$\("#([A-Za-z][\w-]*)"\)', html + fxjs))
    used |= set(re.findall(r'getElementById\("([A-Za-z][\w-]*)"\)', html + fxjs))
    missing = sorted(u for u in used if u not in declared)
    assert not missing, f"ids usados que no existen: {missing}"


def test_iconos_del_sprite_existen():
    html = (STATIC / "index.html").read_text()
    fxjs = ((STATIC / "fx.js").read_text() + (STATIC / "profile.js").read_text() + (STATIC / "estilo.js").read_text()
            + (STATIC / "estilo_proyecto.js").read_text() + (STATIC / "estilo_catalog.js").read_text())
    sprite = set(re.findall(r'<g id="i-([\w-]+)"', html))
    used = set(re.findall(r'#i-([a-z][\w-]*)', html + fxjs)) - {"${n}"}
    used |= set(re.findall(r'ICO\("([\w-]+)"\)', html + fxjs))
    used |= set(re.findall(r'icon: "([\w-]+)"', fxjs))
    missing = sorted(u for u in used if u not in sprite)
    assert not missing, f"iconos sin definir en el sprite: {missing}"


def test_inspector_cubre_el_esquema():
    """El inspector sabe dibujar cada tipo de parametro y cada pista del esquema del motor."""
    from motion import schema

    s = schema.schema()
    fxjs = (STATIC / "fx.js").read_text()
    for t in s["types"]:
        assert re.search(r't === "%s"' % re.escape(t), fxjs), f"tipo de parametro sin control: {t}"
    for track in s["tracks"]:
        assert re.search(r"\b%s: \{ label:" % re.escape(track), fxjs), f"pista sin metadatos en el Studio: {track}"
    for grp in ("captions", "music", "grade"):
        assert s[grp]["params"], grp


def test_sin_emojis_en_el_studio():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⭕]")
    for f in ("index.html", "fx.js", "fx.css", "fxcore.js", "profile.js", "capdraw.js", "estilo.js", "estilo_proyecto.js", "estilo_catalog.js"):
        txt = (STATIC / f).read_text()
        # drawEmoji pinta los emojis que vienen en el PLAN (dato del usuario), no UI propia
        hits = [m.group(0) for m in emoji.finditer(txt)]
        assert not hits, f"{f}: {hits[:5]}"


def test_estaticos_se_sirven(tmp_path, monkeypatch):
    """Los archivos nuevos del Studio se sirven por /static con su tipo MIME."""
    import mimetypes
    for f in ("fx.js", "fxcore.js", "capdraw.js", "estilo.js", "estilo_catalog.js", "profile.js", "fx.css"):
        assert (STATIC / f).exists()
        assert mimetypes.guess_type(f)[0] in ("application/javascript", "text/javascript", "text/css"), f
    html = (STATIC / "index.html").read_text()
    for ref in ("/static/fx.css", "/static/fxcore.js", "/static/capdraw.js", "/static/fx.js", "/static/estilo.js", "/static/profile.js"):
        assert ref in html
    # fxcore -> capdraw -> fx -> estilo -> profile, y todos antes del script en linea
    order = [html.index(f"/static/{f}") for f in ("fxcore.js", "capdraw.js", "fx.js", "estilo.js", "profile.js")]
    assert order == sorted(order) and order[-1] < html.index("<script>")


def test_humo_e2e_en_navegador():
    """E2E opcional en un navegador real (tests/studio_smoke.mjs). Se activa con
    REELFY_STUDIO_E2E_URL=http://127.0.0.1:PUERTO/?job=ID y REELFY_PLAYWRIGHT=ruta a playwright-core."""
    import os
    url, pw = os.environ.get("REELFY_STUDIO_E2E_URL"), os.environ.get("REELFY_PLAYWRIGHT")
    if not (url and pw and NODE):
        pytest.skip("E2E del Studio desactivado (define REELFY_STUDIO_E2E_URL y REELFY_PLAYWRIGHT)")
    args = [NODE, str(SPIKE / "tests" / "studio_smoke.mjs"), url]
    if os.environ.get("REELFY_STUDIO_E2E_EXPORT", "1") == "0":
        args.append("--no-export")
    r = subprocess.run(args, capture_output=True, text=True, timeout=3600, env={**os.environ})
    assert r.returncode == 0, r.stdout[-6000:] + r.stderr[-2000:]


def _hidden_ranges_render(plan):
    """Rangos (tiempo final) en que render.build_job oculta los subtitulos."""
    from motion import render as R
    from motion import schema as S
    from motion.timeline import Timeline
    p = S.normalize(json.loads(json.dumps(plan)))
    tl = Timeline.from_plan(p, fps=30)
    o = R._resolve_opts({"W": 216, "H": 384, "fps": 30, "dither": 0.0})
    job, _ = R.build_job(p, tl, o, R.load_modules(quiet=True))
    return tl, [tuple(r) for r in job["captions"].get("_hide") or []]


@needs_node
def test_regla_de_ocultar_subtitulos_igual_al_render(tmp_path):
    """La vista en vivo oculta los subtitulos EXACTAMENTE cuando el export los oculta
    (fxcore.captionsHiddenAt vs render.build_job): titulos de detras y kicker incluidos,
    keep_captions, desactivados, escenas con texto y hide_during_titles."""
    rnd = random.Random(5)
    presets = ["title_pop", "stack", "kicker", "behind_giant", "counter", "serif_elegant"]
    plans, probes, expected = [], [], []
    for n in range(24):
        dur = 20.0
        text = []
        for i in range(rnd.randint(1, 5)):
            s = round(rnd.uniform(0, dur - 2), 2)
            text.append({"id": f"tx_{i}", "preset": rnd.choice(presets), "start": s, "end": round(s + rnd.uniform(0.6, 3), 2),
                         "text": "Hola mundo", "y": round(rnd.uniform(0.1, 0.9), 2), "enabled": rnd.random() > 0.15,
                         **({"keep_captions": True} if rnd.random() < 0.2 else {}),
                         **({"behind": True} if rnd.random() < 0.2 else {})})
        cut = []
        for i in range(rnd.randint(0, 2)):
            s = round(rnd.uniform(1, dur - 3), 2)
            cut.append({"id": f"cw_{i}", "kind": "title_card", "start": s, "end": round(s + rnd.uniform(0.8, 2), 2),
                        "text": {"text": rnd.choice(["Precio", "", "  "]), "preset": "title_pop"}})
        cuts = [{"start": 6.0, "end": 7.0, "enabled": True}] if n % 3 == 0 else []
        cap = {"preset": "clean_bold"}
        if n % 4 == 1:
            cap["hide_during_titles"] = False
        plan = {"video": "x.mp4", "version": 2, "duration": dur, "cuts": cuts, "shots": [{"start": 0.0, "end": dur}],
                "phrases": [[{"text": "hola", "start": 0.2, "end": 0.6}]],
                "fx": {"text": text, "cutaways": cut, "captions": cap, "graphics": [], "effects": [], "transitions": [], "sfx": []}}
        tl, hide = _hidden_ranges_render(plan)
        ts, exp = [], []
        for k in range(160):
            t = round(k * dur / 160 + 0.013, 3)
            tf = tl.remap(t)
            if tf is None:
                continue
            # lejos de los bordes (cuantizacion a cuadros del render)
            edges = [x for it in text + cut for x in (it["start"], it["end"])]
            if any(abs(t - e) < 0.07 for e in edges):
                continue
            ts.append(t)
            exp.append(any(a <= tf < b for a, b in hide))
        plans.append(plan["fx"])
        probes.append(ts)
        expected.append(exp)
    (tmp_path / "h.json").write_text(json.dumps({"fx": plans, "ts": probes}))
    script = tmp_path / "h.js"
    script.write_text(
        "const C=require(%s);const fs=require('fs');" % json.dumps(str(STATIC / "fxcore.js"))
        + "const d=JSON.parse(fs.readFileSync(%s,'utf8'));" % json.dumps(str(tmp_path / "h.json"))
        + "process.stdout.write(JSON.stringify(d.fx.map((fx,i)=>d.ts[i].map(t=>C.captionsHiddenAt(fx,t)))));")
    r = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    checked = 0
    for i, (g, e) in enumerate(zip(got, expected)):
        for t, a, b in zip(probes[i], g, e):
            assert a == b, (i, t, a, b, plans[i])
            checked += 1
    assert checked > 1500


def test_etiquetas_del_esquema_quedan_acentuadas(tmp_path):
    """Toda palabra de las etiquetas del esquema que en espanol lleva tilde (-cion, -sion, ...) la
    corrige fxcore.accentEs (el motor las trae en ASCII)."""
    from motion import schema
    words = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("label", "hint", "desc") and isinstance(v, str):
                    words.update(re.findall(r"[A-Za-z]+", v))
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(schema.schema())
    sospechosas = sorted(w for w in words if re.search(r"(cion|sion|ulos?|afico|aficos|ximo|xima|tamano|detras|lineas?|musica|animo)$", w, re.I))
    if not NODE:
        pytest.skip("node no esta instalado")
    js = tmp_path / "a.js"
    js.write_text("const C=require(%s);process.stdout.write(JSON.stringify(%s.map(w=>[w,C.accentEs(w)])));"
                  % (json.dumps(str(STATIC / "fxcore.js")), json.dumps(sospechosas)))
    r = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    sin = [w for w, fixed in json.loads(r.stdout) if w == fixed and w.lower() not in ("modulo", "titulo_x", "calculo")
           and re.search(r"(cion|sion|detras|musica|tamano|lineas?|animo)$", w, re.I)]
    assert not sin, f"etiquetas sin acentuar en la UI: {sin}"


def test_version_de_assets_es_el_hash_del_contenido():
    """index.html pide fxcore.js/fx.js/profile.js/fx.css con ?v=<hash del contenido>: el navegador
    (y el WebView de escritorio) nunca usa una copia vieja tras actualizar."""
    import hashlib
    h = hashlib.sha1(b"".join((STATIC / f).read_bytes()
                              for f in ("fxcore.js", "fx.js", "profile.js", "fx.css"))).hexdigest()[:8]
    html = (STATIC / "index.html").read_text()
    vs = set(re.findall(r'/static/(?:fxcore\.js|fx\.js|profile\.js|fx\.css)\?v=([0-9a-f]+)', html))
    assert len(re.findall(r'/static/(?:fxcore\.js|fx\.js|profile\.js|fx\.css)\?v=', html)) == 4
    assert vs == {h}, f"version {vs} != {h}: recalcula el ?v= (sha1 de fxcore.js+fx.js+profile.js+fx.css)[:8]"


@needs_node
def test_transiciones_de_mezcla_igual_al_motor():
    """El aviso 'lejos de una union' del timeline usa la misma lista de mezclas que el render."""
    from motion import render as R
    r = subprocess.run([NODE, "-e", "process.stdout.write(JSON.stringify(require(%s).MIXERS))" % json.dumps(str(STATIC / "fxcore.js"))],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert sorted(json.loads(r.stdout)) == sorted(R._mixers(R.load_modules(quiet=True)))


def _node_json(tmp_path, name, code):
    """Corre `code` en node con fxcore cargado como C; devuelve el JSON que escribe."""
    js = tmp_path / name
    js.write_text("const C=require(%s);const fs=require('fs');" % json.dumps(str(STATIC / "fxcore.js")) + code)
    r = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@needs_node
def test_pip_automatico_igual_al_render(tmp_path):
    """El PiP con x/y vacios cae en la vista en vivo donde lo pone el export
    (fxcore.mediaAutoPlace vs render.media_auto_place, contrato 4.2b): caras en cualquier lado,
    sin cara, tarjetas grandes y chicas, subtitulos y otros overlays que esquivar."""
    from motion import render as R
    rnd = random.Random(11)
    cases = []
    for _ in range(300):
        face = None if rnd.random() < 0.15 else [round(rnd.uniform(0.2, 0.8), 4), round(rnd.uniform(0.2, 0.6), 4),
                                                   round(rnd.uniform(0.08, 0.45), 4), round(rnd.uniform(0.06, 0.3), 4)]
        w, h = round(rnd.uniform(0.12, 0.7), 4), round(rnd.uniform(0.1, 0.6), 4)
        cap = None if rnd.random() < 0.2 else round(rnd.uniform(0.55, 0.78), 4)
        avoid = []
        for _k in range(rnd.randint(0, 3)):
            x0, y0 = rnd.uniform(0, 0.8), rnd.uniform(0, 0.8)
            avoid.append([round(x0, 4), round(y0, 4), round(x0 + rnd.uniform(0.05, 0.5), 4), round(y0 + rnd.uniform(0.05, 0.3), 4)])
        cases.append({"face": face, "w": w, "h": h, "cap": cap, "avoid": avoid})
    (tmp_path / "pip.json").write_text(json.dumps(cases))
    got = _node_json(tmp_path, "pip.js", "const d=JSON.parse(fs.readFileSync(%s,'utf8'));"
                     "process.stdout.write(JSON.stringify(d.map(c=>C.mediaAutoPlace(c.face,c.w,c.h,c.cap,c.avoid))));"
                     % json.dumps(str(tmp_path / "pip.json")))
    for c, g in zip(cases, got):
        exp = R.media_auto_place(c["face"], c["w"], c["h"], c["cap"], c["avoid"])
        # round() de python redondea al par y Math.round hacia arriba: a lo mas 1e-4 en un empate
        assert abs(g[0] - exp[0]) < 1.5e-4 and abs(g[1] - exp[1]) < 1.5e-4 and abs(g[2] - exp[2]) < 1e-9, (c, g, exp)
    # tarjeta del PiP (graphics.media_box / render._pip_card)
    cards = [[round(rnd.uniform(0.05, 1.0), 3), rnd.choice([None, 0.5, 1.0, 1.78, 3.5]), rnd.choice([800, 1920, 3024]),
              rnd.choice([1000, 1080, 4032])] for _ in range(60)]
    got = _node_json(tmp_path, "card.js", "process.stdout.write(JSON.stringify(%s.map(c=>C.pipCard(c[0],c[1],c[2],c[3],1080))));"
                     % json.dumps(cards))
    for c, g in zip(cards, got):
        exp = R._pip_card(c[0], c[1], c[2], c[3], 1080)
        assert abs(g[0] - exp[0]) < 1e-6 and abs(g[1] - exp[1]) < 1e-6, (c, g, exp)


@needs_node
def test_foto_con_pie_mueve_los_subtitulos_igual_al_render(tmp_path):
    """Durante una escena photo con pie, la vista en vivo pone los subtitulos a la misma altura que
    el export (fxcore.photoCaptionY vs render.photo_caption_y: debajo del marco, sobre la foto,
    arriba, o 'hide' si no hay lugar)."""
    from motion import render as R
    rnd = random.Random(7)
    cases = [{"size": round(rnd.uniform(0.25, 0.85), 3), "y": round(rnd.uniform(0.2, 0.7), 3),
              "cap": round(rnd.uniform(0.3, 0.85), 3), "asp": rnd.choice([9 / 16, 1.0, 4 / 5])} for _ in range(400)]
    cases += [{"size": None, "y": None, "cap": 0.72, "asp": 9 / 16},
              {"size": 0.6, "y": 0.56, "cap": 0.8, "asp": 1.0}, {"size": 0.8, "y": 0.62, "cap": 0.85, "asp": 1.0}]
    (tmp_path / "ph.json").write_text(json.dumps(cases))
    got = _node_json(tmp_path, "ph.js", "const d=JSON.parse(fs.readFileSync(%s,'utf8'));"
                     "process.stdout.write(JSON.stringify(d.map(c=>C.photoCaptionY({size:c.size,y:c.y},c.cap,c.asp))));"
                     % json.dumps(str(tmp_path / "ph.json")))
    kinds = set()
    for c, g in zip(cases, got):
        exp = R.photo_caption_y({"size": c["size"], "y": c["y"]}, c["cap"], c["asp"])
        kinds.add("none" if exp is None else "hide" if exp == "hide" else "y")
        if isinstance(exp, float):
            assert isinstance(g, (int, float)) and abs(g - exp) < 1.5e-4, (c, g, exp)
        else:
            assert g == exp, (c, g, exp)
    assert kinds == {"none", "hide", "y"}, kinds


@needs_node
def test_presets_de_subtitulos_y_titulos_iguales_al_motor(tmp_path):
    """Las tablas que la vista en vivo copia del motor no se desfasan: presets de subtitulos
    (escala, espacio, separacion, palabra activa, lineas, contorno...), presets de titulos (altura,
    alineacion, espacio, tamanos, lineas, deriva), variantes italicas y palabras funcionales."""
    from motion import text as T
    got = _node_json(tmp_path, "pr.js", "process.stdout.write(JSON.stringify({cap: C.CAPTION_PRESETS, txt: C.TEXT_PRESETS,"
                     " it: C.ITALIC, fe: [...C.FUNC_END]}));")
    assert set(got["cap"]) == set(T.CAPTION_PRESETS)
    for name, P in T.CAPTION_PRESETS.items():
        g = got["cap"][name]
        for k in ("scale", "space", "gap", "active_scale", "max_lines", "max_words", "stroke", "shadow"):
            assert abs(float(g[k]) - float(P[k])) < 1e-9, (name, k, g[k], P[k])
        for k in ("reveal", "anim", "active"):
            assert g[k] == P[k], (name, k)
        assert g.get("case", "none") == P.get("case", "none"), name
        assert g.get("font") == P.get("font"), name
    assert set(got["txt"]) == set(T.PRESETS)
    for name, P in T.PRESETS.items():
        g = got["txt"][name]
        want = {"y": P["y"], "align": P["align"], "space": P.get("space", 1.0), "gap": P.get("gap", 0.1),
                "max_size": P["max_size"], "min_size": P["min_size"], "cpl": P.get("cpl", 14),
                "max_lines": P.get("max_lines", 3), "drift": P.get("drift", 0.0), "case": P.get("case")}
        have = {k: g.get(k, 0.0 if k == "drift" else None) for k in want}
        assert have == want, (name, have, want)
    assert got["it"] == T._ITALIC
    assert set(got["fe"]) == set(T._FUNC_END)


@needs_node
def test_envoltura_balanceada_igual_al_motor(tmp_path):
    """fxcore.balancedSplit = text._balanced_split (subtitulos y titulos se parten igual)."""
    from motion import text as T
    rnd = random.Random(5)
    pool = ["de", "la", "precio", "sonrisa", "con", "el", "tus", "dientes", "hoy", "para", "consultorio", "mas", "y"]
    cases = []
    for _ in range(400):
        n = rnd.randint(1, 8)
        cases.append({"w": [round(rnd.uniform(15, 320), 3) for _ in range(n)], "sp": round(rnd.uniform(4, 40), 3),
                      "k": rnd.randint(1, 3), "words": [rnd.choice(pool) for _ in range(n)]})
    (tmp_path / "bs.json").write_text(json.dumps(cases))
    got = _node_json(tmp_path, "bs.js", "const d=JSON.parse(fs.readFileSync(%s,'utf8'));"
                     "process.stdout.write(JSON.stringify(d.map(c=>C.balancedSplit(c.w,c.sp,c.k,c.words))));"
                     % json.dumps(str(tmp_path / "bs.json")))
    for c, g in zip(cases, got):
        assert g == T._balanced_split(c["w"], c["sp"], c["k"], c["words"]), c


@needs_node
def test_subtitulos_en_vivo_miden_lo_que_el_export(tmp_path):
    """La geometria de los subtitulos en vivo (fxcore.captionLayout SIN calibrar con el rect) da el
    ancho del grupo del export dentro de +-3 % y las mismas lineas, con las medidas reales de la
    fuente de cada preset (las del motor: avance y tinta de cada palabra). Frases ficticias."""
    from motion import ctx as CX
    from motion import text as T
    W, H = 1080, 1920
    ctx = CX.FrameCtx(W=W, H=H, fps=30, quality="final", seed=7, style="dinamico", words=[], entities={})
    words, t = [], 0.2
    txt = ("hola a todos tus dientes merecen una sonrisa consultorio especializado en ortodoncia hoy "
           "agenda tu cita con la doctora demo y recibe tu limpieza sin costo este mes")
    for w in txt.split():
        words.append({"text": w, "start": round(t, 3), "end": round(t + 0.3, 3)})
        t += 0.35 if w not in ("hoy", "mes") else 0.9
    cases = []
    for preset in ("clean_bold", "bold_caps", "minimal_small", "boxed", "karaoke"):
        for size in (0.052, 0.06):
            cfg = {"preset": preset, "size": size, "y": 0.72}
            D = T._cap_cfg(cfg, ctx)
            fs = D["size"] * W
            table = {" ": {"adv": T._space(D["font"], fs), "l": 0, "r": 0, "asc": 0, "desc": 0}}
            groups = []
            for g in T.caption_groups([words], cfg, ctx):
                ws = g["text"].split()
                for w in ws:
                    r = T._run(w, D["font"], fs)
                    table[w] = {"adv": r.adv, "l": r.ink[0], "r": r.ink[2], "asc": -r.ink[1], "desc": r.ink[3]}
                groups.append({"words": ws, "lines": g["lines"], "w": (g["rect"][2] - g["rect"][0]) / W})
            cases.append({"cfg": cfg, "fs": fs, "table": table, "groups": groups})
    (tmp_path / "cap.json").write_text(json.dumps(cases))
    got = _node_json(tmp_path, "cap.js", "const d=JSON.parse(fs.readFileSync(%s,'utf8'));"
                     "process.stdout.write(JSON.stringify(d.map(c=>{const m=(t,px)=>{const q=c.table[t],k=px/c.fs;"
                     "return {adv:q.adv*k,l:q.l*k,r:q.r*k,asc:q.asc*k,desc:q.desc*k};};"
                     "return c.groups.map(g=>{const a=C.captionLayout(g.words,c.cfg,1080,1920,m),b=C.captionLayout(g.words,c.cfg,1080,1920,m,{lines:g.lines});"
                     "return {auto:a.rows.length,w:Math.max(...b.rows.map(r=>r.w))/1080};});})));"
                     % json.dumps(str(tmp_path / "cap.json")))
    n, dev = 0, 0.0
    for c, gl in zip(cases, got):
        for g, r in zip(c["groups"], gl):
            n += 1
            assert r["auto"] == g["lines"], (c["cfg"], g["words"], r, g["lines"])
            dev = max(dev, abs(r["w"] - g["w"]) / g["w"])
            assert abs(r["w"] - g["w"]) <= 0.03 * g["w"], (c["cfg"], g["words"], r["w"], g["w"])
    assert n >= 40
    print(f'grupos: {n}, desviacion maxima del ancho: {dev * 100:.2f} %')


@needs_node
def test_foto_y_logo_del_enlace_igual_al_render(tmp_path):
    """La vista en vivo dibuja la foto y el logo de los enlaces del Perfil con la geometria del
    export: hueco de link_below (fxcore.linkSlot vs graphics.link_slot_geom), constantes de las
    insignias y el mismo criterio para presentar un logo (fxcore.logoLook vs graphics.logo_look)
    sobre imagenes sinteticas (a sangre, transparente, blanco, JPG ancho, semitransparente)."""
    import numpy as np
    from motion import graphics as G
    consts = _node_json(tmp_path, "k.js", "process.stdout.write(JSON.stringify([C.LINK_PHOTO,C.LINK_LOGO,C.LINK_BADGE,"
                        "C.LINK_PAD_R,C.FOLLOW_BADGE,C.LOGO_PAD,C.LOGO_RING,C.LOGO_PLATE,C.LOGO_PLATE_DARK]));")
    assert consts == [G.LINK_PHOTO, G.LINK_LOGO, G.LINK_BADGE, G.LINK_PAD_R, G.FOLLOW_BADGE, G.LOGO_PAD,
                      G.LOGO_RING, G.LOGO_PLATE, G.LOGO_PLATE_DARK]
    cases = [[k, h, two] for k in ("photo", "logo", "icon") for h in (60.0, 120.95, 151.2) for two in (True, False)]
    got = _node_json(tmp_path, "s.js", "process.stdout.write(JSON.stringify(%s.map(c=>C.linkSlot(c[0],c[1],c[2]))));"
                     % json.dumps(cases))
    for (k, h, two), g in zip(cases, got):
        e = G.link_slot_geom(k, h, two)
        for a, b in (("d", "d"), ("inset", "inset"), ("gap", "gap"), ("padR", "pad_r")):
            assert abs(g[a] - e[b]) < 1e-9, (k, h, two, a)
    rnd = np.random.default_rng(5)

    def img(w, h, bg, box=None, fg=None):
        a = np.zeros((h, w, 4), np.uint8)
        a[...] = bg
        if box:
            x0, y0, x1, y1 = box
            a[y0:y1, x0:x1] = fg
        return a
    imgs = [img(48, 48, (255, 120, 0, 255)),                                        # icono de app
            img(64, 40, (0, 0, 0, 0), (9, 4, 41, 30), (200, 30, 180, 255)),         # transparente
            img(40, 40, (0, 0, 0, 0), (8, 8, 32, 32), (255, 255, 255, 255)),        # blanco
            img(90, 30, (250, 244, 230, 255), (20, 8, 70, 22), (20, 40, 160, 255)),  # JPG ancho
            img(40, 40, (0, 0, 0, 0)),                                              # vacio
            np.concatenate([rnd.integers(0, 256, (36, 50, 3)), rnd.integers(0, 256, (36, 50, 1))], -1).astype(np.uint8)]
    (tmp_path / "logos.json").write_text(json.dumps(
        [{"w": int(a.shape[1]), "h": int(a.shape[0]), "px": a.reshape(-1).tolist()} for a in imgs]))
    got = _node_json(tmp_path, "l.js", "const d=JSON.parse(fs.readFileSync(%s,'utf8'));"
                     "process.stdout.write(JSON.stringify(d.map(c=>C.logoLook(Uint8ClampedArray.from(c.px),c.w,c.h))));"
                     % json.dumps(str(tmp_path / "logos.json")))
    for a, g in zip(imgs, got):
        e = G.logo_look(G._as_image(a))
        assert g["bleed"] == e["bleed"] and g["plate"] == e["plate"] and tuple(g["box"]) == tuple(e["box"]), (g, e)
    # cada caso distinto de verdad (el espejo no pasa por coincidencia)
    assert [g["bleed"] for g in got[:2]] == [True, False] and got[2]["plate"] == G.LOGO_PLATE_DARK
    assert got[3]["plate"] == "#FAF4E6" and got[1]["box"] == [9, 4, 41, 30]


def test_studio_dibuja_foto_y_logo_del_perfil():
    """fx.js dibuja en vivo lo mismo que graphics: foto redonda en el hueco de link_below con el logo
    en su esquina, logo solo en insignia redondeada, logo como insignia del Seguir, logo en el
    encabezado de la tarjeta y en los chips; el inspector deja quitar un medio opcional."""
    fx = (STATIC / "fx.js").read_text()
    for frag in ("C.linkSlot(kind", "drawLogoBadge(c, lg, bx, by, bd, \"circle\")", "drawLogoBadge(c, lg, scx, 0, d, \"squircle\")",
                 "drawLogoBadge(c, lgf, bx, by, bd, \"circle\")", "drawLogoBadge(c, lgc,", "C.logoLook(", "C.logoFit(",
                 "C.LINK_BADGE", "C.FOLLOW_BADGE", 'data-a="clear"'):
        assert frag in fx, frag
    pf = (STATIC / "profile.js").read_text()
    for frag in ("/profile/media/refresh", "media_status", "Obteniendo foto…", "media_off", "media_src", "/tlsource/",
                 "Cambiar", "PF.adoptLinkMedia", "PF.setLinkMedia"):
        assert frag in pf, frag


# ================================================================ limpieza de tomas (Guion)
@needs_node
def test_limpieza_unitarias_node():
    """Logica pura de la limpieza de tomas (tests/js/takes.test.js)."""
    r = subprocess.run([NODE, "--test", str(SPIKE / "tests" / "js" / "takes.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


def _cortes_con_tomas(rnd, dur):
    """Silencios + tomas (src "takes") que se solapan entre si y con los silencios."""
    cuts = []
    for _ in range(rnd.randint(0, 14)):
        s = round(rnd.uniform(0, dur), 3)
        cuts.append({"start": s, "end": round(min(dur, s + rnd.uniform(0.05, 2.5)), 3), "enabled": rnd.random() > 0.1})
    kinds = ["retoma", "arranque_fallido", "autocorreccion", "tartamudeo", "error", "digresion"]
    for k in range(rnd.randint(0, 10)):
        s = round(rnd.uniform(0, dur), 3)
        lvl = rnd.choice(["alta", "media", "baja"])
        c = {"id": f"tk_{k}", "start": s, "end": round(min(dur, s + rnd.uniform(0.2, 9.0)), 3), "src": "takes",
             "kind": rnd.choice(kinds), "level": lvl, "conf": {"alta": 0.9, "media": 0.6, "baja": 0.3}[lvl],
             "enabled": lvl != "baja", "group": f"tg_{k % 3}"}
        if rnd.random() < 0.3:
            c["enabled"], c["user"] = not c["enabled"], True
        cuts.append(c)
    rnd.shuffle(cuts)
    return cuts


@needs_node
def test_limpieza_duraciones_js_igual_a_python(tmp_path):
    """Lo que el Studio muestra de la limpieza sale igual que en el motor: tiempo final con tomas
    solapadas con silencios (Timeline), vista previa de cada nivel (takes.apply_sensitivity),
    resumen de las tomas (takes.stats) y lo quitado (jobstore._final_duration)."""
    import sys
    from motion import timeline as T
    import takes as TK
    sys.path.insert(0, str(SPIKE / "app"))
    import jobstore
    rnd = random.Random(17)
    plans = []
    for _ in range(50):
        dur = round(rnd.uniform(20, 400), 3)
        shots = [{"start": 0.0}] + [{"start": round(rnd.uniform(1, dur - 1), 3)} for _ in range(rnd.randint(0, 3))]
        plans.append({"duration": dur, "cuts": _cortes_con_tomas(rnd, dur), "shots": sorted(shots, key=lambda s: s["start"])})
    (tmp_path / "p.json").write_text(json.dumps(plans))
    got = _node_json(tmp_path, "d.js",
                     "const ps=JSON.parse(fs.readFileSync(%s,'utf8'));" % json.dumps(str(tmp_path / "p.json"))
                     + "process.stdout.write(JSON.stringify(ps.map(p=>{const tl=C.timelineFromPlan(p,30);"
                       "return {keeps:tl.keeps,final:tl.finalDuration,removed:C.removedByCategory(p.cuts,p.duration).total,"
                       "levels:['suave','normal','a_fondo'].map(l=>C.levelFinal(p,l,30)),"
                       "on:['suave','normal','a_fondo'].map(l=>C.applyLevel(p.cuts,l).map(c=>c.enabled!==false)),"
                       "stats:C.takeStats(p.cuts)};})));")
    for p, g in zip(plans, got):
        tl = T.Timeline.from_plan(p, fps=30)
        assert len(g["keeps"]) == len(tl.keeps)
        for a, b in zip(g["keeps"], tl.keeps):
            assert abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6
        assert abs(g["final"] - tl.final_duration) < 1e-6
        assert abs((p["duration"] - g["removed"]) - jobstore._final_duration(p["duration"], p["cuts"])) < 0.01
        for lv, fin, on in zip(("suave", "normal", "a_fondo"), g["levels"], g["on"]):
            cs = TK.apply_sensitivity(p["cuts"], lv)
            assert on == [c.get("enabled", True) is not False for c in cs], lv
            assert abs(fin - T.Timeline.from_plan(dict(p, cuts=cs), fps=30).final_duration) < 1e-6, lv
        e = TK.stats(p["cuts"])
        s = g["stats"]
        assert (s["n"], s["enabled"], s["suggested"]) == (e["n"], e["enabled"], e["suggested"])
        assert abs(s["removed_s"] - e["removed_s"]) < 0.011
        assert set(s["by_kind"]) == set(e["by_kind"])
        for k, v in e["by_kind"].items():
            assert s["by_kind"][k]["n"] == v["n"] and abs(s["by_kind"][k]["s"] - v["s"]) < 0.011, k


@needs_node
def test_formato_16_9_por_defecto_de_punta_a_punta(tmp_path):
    """Un solo video horizontal de mas de 3 min abre y se exporta en 16:9 (no vertical): el motor
    y el Studio conocen el formato, el Studio lo elige al abrir y lo pide al montar el proyecto."""
    from motion import render as R
    import pipeline
    assert R.FORMATS["16:9"] == (1920, 1080) and pipeline.FORMATS["16:9"] == (1920, 1080)
    html = (STATIC / "index.html").read_text()
    assert re.search(r'"16:9":\{cw:960,ch:540,ow:1920', html), "FMTS del Studio sin 16:9"
    assert 'data-v="16:9"' in html
    js = _inline_script()
    assert "fmtFromPlan()" in js and "FXCore.defaultFormat(PLAN" in js
    assert 'body.director.fmt="16:9"' in js and "FXCore.isLongLandscape(" in js      # /compose nace en 16:9
    assert 'seg("#segFmt",chooseFormat)' in js                                         # la eleccion se guarda
    got = _node_json(tmp_path, "f.js", "process.stdout.write(JSON.stringify(["
                     "C.defaultFormat({duration:1146,dims:[3840,2160],shots:[{start:0,src:'a.mov'}]}),"
                     "C.defaultFormat({duration:39,dims:[3840,2160]}),"
                     "C.defaultFormat({duration:1146,dims:[2160,3840]}),"
                     "C.defaultFormat({duration:600,dims:[1920,1080],studio:{fmt:'9:16'}})]));")
    assert got == ["16:9", "9:16", "9:16", "9:16"]


def test_clic_en_un_corte_o_short_solo_selecciona():
    """Auditoria UX: un clic en un bloque de Cortes (o en un short ya seleccionado) NO lo cambia;
    restaurar o apagar es explicito (boton del inspector, R o su interruptor) con deshacer."""
    js = _inline_script()
    cuts = js[js.index("function renderCutsLane"):js.index("function renderCapsLane")]
    shorts = js[js.index("function renderShortsLane"):js.index("/* ---- presets ---- */")]
    assert "c.enabled=!on" not in cuts and "c.enabled=" not in cuts
    assert "h.enabled=!h.enabled" not in shorts
    assert 'inspect({type:"cut",i:ci})' in cuts
    assert '(e.key==="r"||e.key==="R")&&!meta&&SEL&&SEL.type==="cut"' in js
    fx = (STATIC / "fx.js").read_text()
    clean = fx[fx.index("limpieza de tomas (Guion)"):fx.index("inicio del editor")]
    # en el guion el clic en un corte selecciona; cambiarlo solo desde el popover o con R
    assert 'if (cut) { select(+cut.dataset.c, { seek: true }); return; }' in clean
    assert 'C.toggleCut(c)' in clean and "src: \"ai\"" not in clean and "src:\"ai\"" not in clean


# ---- E2E en un navegador real con un servidor aislado y datos SINTETICOS -------------------
_GUION = [
    ("Bienvenida", ["Hola a todos, bienvenidos otra vez al canal.", "Hoy les traigo la bicicleta que uso todos los días para ir al trabajo.",
                    "Es una bici urbana sencilla, pero la he usado casi dos años y ya tengo una opinión clara."]),
    ("Por qué la compré", ["La compré porque vivo a seis kilómetros de la oficina y el tráfico en la mañana es terrible.",
                           "Quería algo ligero que pudiera subir por las escaleras sin sufrir."]),
    ("Los cambios", ["Tiene veintiún cambios, aunque honestamente uso tres o cuatro nada más.",
                     "Los cambios entran suaves, pero hay que ajustarlos cada tanto porque el cable se estira."]),
    ("Los frenos", ["Los frenos de disco mecánicos frenan muy bien, incluso con el piso mojado.",
                    "Las pastillas me duraron unos ocho meses y cambiarlas fue muy fácil."]),
    ("Accesorios", ["Le agregué una parrilla trasera, dos luces y un candado de cadena.",
                    "Las luces se cargan por cable y duran como una semana de trayectos."]),
    ("Precio", ["Me costó alrededor de nueve mil pesos, más unos dos mil en accesorios.",
                "Comparado con lo que gastaba en transporte, la recuperé en menos de un año."]),
    ("Conclusión", ["Si están pensando en moverse en bici por la ciudad, se las recomiendo mucho.",
                    "Cuéntenme en los comentarios qué bici usan ustedes y nos vemos en el próximo video."]),
]


def _plan_limpieza(n_words=2600, seed=5):
    """Plan v2 sintetico de un video largo: palabras, silencios y tomas con el contrato de datos."""
    from motion import schema
    rnd = random.Random(seed)
    words, takes, secs, t, gid, rep = [], [], [], 1.5, 0, 0

    def say(text, gap):
        nonlocal t
        a = len(words)
        toks = text.split()
        for k, tok in enumerate(toks):
            d = 0.16 + 0.035 * len(tok) + rnd.uniform(-0.03, 0.05)
            words.append({"text": tok, "start": round(t, 3), "end": round(t + d, 3)})
            t += d + rnd.uniform(0.04, 0.12)
            if tok.endswith(",") and k < len(toks) - 1 and rnd.random() < 0.5:
                t += rnd.uniform(0.5, 1.1)
        t += gap
        return a, len(words) - 1
    while len(words) < n_words:
        for title, sents in _GUION:
            secs.append({"start": round(t - 0.2, 3), "title": f"{title} ({rep + 1})"})
            for s in sents:
                r = rnd.random()
                if r < 0.22:
                    gid += 1
                    fails = [say(" ".join(s.split()[:rnd.randint(3, 5)]), rnd.uniform(0.5, 1.4)) for _ in range(1 + (rnd.random() < 0.3))]
                    keep = say(s, rnd.uniform(0.4, 1.3))
                    for fa, fb in fails:
                        takes.append(("retoma", fa, fb, rnd.choice([0.9, 0.7, 0.4]), f"tg_{gid}", keep))
                elif r < 0.32:
                    a, b = say(" ".join(s.split()[:2]) + "...", rnd.uniform(0.6, 1.2))
                    takes.append(("arranque_fallido", a, b, 0.75, None, say(s, rnd.uniform(0.4, 1.2))))
                elif r < 0.40:
                    a, b = say(f"{s.split()[0]} {s.split()[0]}", 0.08)
                    takes.append(("tartamudeo", a, b, rnd.choice([0.9, 0.35]), None, say(s, rnd.uniform(0.4, 1.2))))
                elif r < 0.46:
                    toks = s.split()
                    say(" ".join(toks[:3]), 0.05)
                    a, b = say("nueve, no,", 0.1)
                    takes.append(("autocorreccion", a, b, 0.66, None, say(" ".join(toks[3:]), rnd.uniform(0.4, 1.2))))
                elif r < 0.50:
                    a, b = say("Ay, perdón, se me fue la idea.", 0.8)
                    takes.append(("error", a, b, 0.84, None, say(s, rnd.uniform(0.4, 1.2))))
                else:
                    say(s, rnd.uniform(0.25, 1.6))
        rep += 1
    dur = round(t + 2.0, 3)
    cuts, prev = [], 0.0
    for w in words:
        if w["start"] - prev >= 0.45 and w["start"] - 0.08 - (prev + 0.1) > 0.1:
            cuts.append({"start": round(prev + (0.1 if prev else 0.0), 2), "end": round(w["start"] - 0.08, 2), "enabled": True})
        prev = w["end"]
    lvl = lambda c: "alta" if c >= 0.8 else "media" if c >= 0.5 else "baja"
    for k, (kind, a, b, conf, grp, keep) in enumerate(takes):
        cuts.append({"id": f"tk_{k:04d}", "start": round(max(words[a - 1]["end"] + 0.1 if a else 0.0, words[a]["start"] - 0.25), 2),
                     "end": round(words[b + 1]["start"] - 0.08, 2), "enabled": lvl(conf) != "baja", "src": "takes", "kind": kind,
                     "conf": conf, "level": lvl(conf), "reason": "", "text": " ".join(w["text"] for w in words[a:b + 1]),
                     "kept_text": " ".join(w["text"] for w in words[keep[0]:keep[0] + 8]),
                     "keep_ref": {"start": words[keep[0]]["start"], "end": words[keep[1]]["end"]}, "group": grp or f"tg_s{k}", "w0": a, "w1": b})
    cuts.sort(key=lambda c: c["start"])
    phrases, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == 3 or w["text"][-1] in ".?!,":
            phrases.append(cur); cur = []
    if cur:
        phrases.append(cur)
    plan = schema.normalize({"version": 2, "duration": dur, "dims": [320, 180], "phrases": phrases, "camera": [], "beats": [], "broll": [],
                             "highlights": [{"start": secs[1]["start"], "end": secs[1]["start"] + 50, "title": "Por qué la compré", "enabled": True}],
                             "shots": [{"start": 0, "end": dur, "src": "bici.mov"}], "fx": {}})
    plan["fx"]["music"] = {"enabled": False, "source": "none"}
    plan["fx"]["grade"]["enabled"] = False
    long_plan = dict(plan, cuts=cuts, takes={"version": 1, "sensitivity": "normal", "topic": "Reseña de una bicicleta urbana",
                                             "summary": None, "sections": secs, "stats": {}, "llm": True, "ms": 1})
    return long_plan, dict(plan, cuts=[c for c in cuts if c.get("src") != "takes"]), dur


def test_plan_sintetico_de_limpieza_es_largo_y_valido():
    """El plan del E2E cubre el caso de video largo (2500+ palabras, 250+ cortes)."""
    import takes as TK
    long_plan, old_plan, dur = _plan_limpieza()
    n_words = sum(len(p) for p in long_plan["phrases"])
    assert n_words >= 2500 and len(long_plan["cuts"]) >= 250 and dur > 180
    assert not any(c.get("src") == "ai" for c in long_plan["cuts"])
    assert TK.stats(long_plan["cuts"])["n"] >= 40
    assert "takes" not in old_plan and all(c.get("src") != "takes" for c in old_plan["cuts"])


def test_limpieza_e2e_en_navegador(tmp_path):
    """E2E opcional en un navegador real (tests/js/limpieza_e2e.mjs) contra un servidor AISLADO
    (tests/devserver.sh) con datos sinteticos. Se activa con REELFY_PLAYWRIGHT=ruta a
    playwright-core (usa Chrome; REELFY_PW_CHANNEL cambia el canal)."""
    import os
    import socket
    import sys
    import time
    import urllib.request
    pw = os.environ.get("REELFY_PLAYWRIGHT")
    if not (pw and NODE):
        pytest.skip("E2E de la limpieza desactivado (define REELFY_PLAYWRIGHT)")
    import paths
    sys.path.insert(0, str(SPIKE / "app"))
    data = tmp_path / "data"
    (data / "input").mkdir(parents=True)
    long_plan, old_plan, dur = _plan_limpieza()
    vid = data / "input" / "takesui_long.mp4"
    subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={dur}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "36", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "48k", "-shortest",
                    "-movflags", "+faststart", str(vid)], check=True, timeout=600)
    (data / "input" / "takesui_old.mp4").symlink_to(vid)        # el Studio pide /source/<job>
    code = ("import json,sys; sys.path[:0]=[%r,%r]; import jobstore; jobstore.init(); d=json.load(sys.stdin)\n"
            "for jid,p in d.items():\n"
            "    jobstore.create(jid, kind='analyze', ext='.mp4', plan=p, phase='review', message='Análisis listo')\n"
            "    jobstore.update(jid, state='review', pct=100)\n") % (str(SPIKE / "app"), str(SPIKE / "scripts"))
    long_plan["video"] = old_plan["video"] = str(vid)
    subprocess.run([sys.executable, "-c", code], input=json.dumps({"takesui_long": long_plan, "takesui_old": old_plan}),
                   text=True, check=True, env={**os.environ, "REELFY_DATA": str(data)}, timeout=120)
    port = None
    for p in range(8500, 8600):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                port = p
                break
    assert port, "sin puerto libre en 8500-8599"
    srv = subprocess.Popen([str(SPIKE / "tests" / "devserver.sh"), str(port), str(data)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        for _ in range(120):
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{port}/setup", timeout=2).status == 200:
                    break
            except Exception:  # noqa: aun arrancando
                time.sleep(0.5)
        out = os.environ.get("REELFY_E2E_SHOTS") or str(tmp_path / "shots")
        r = subprocess.run([NODE, str(SPIKE / "tests" / "js" / "limpieza_e2e.mjs"), f"http://127.0.0.1:{port}", out],
                           capture_output=True, text=True, timeout=1200, env={**os.environ})
        assert r.returncode == 0, r.stdout[-6000:] + r.stderr[-3000:]
    finally:
        import signal
        try:
            os.killpg(srv.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        srv.wait(timeout=30)


# ================================================================ armar con IA (montaje por guion)
@needs_node
def test_montaje_unitarias_node():
    """Logica pura de "Armar con IA" (tests/js/montage.test.js): orden de grabacion, linea de tiempo
    desde plan.montage, "Usar esta toma" + re-empaque, extras, panel "Tomas" y cuerpo de /compose."""
    r = subprocess.run([NODE, "--test", str(SPIKE / "tests" / "js" / "montage.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


def test_montaje_cableado_de_la_ui():
    """La UI de "Armar con IA" esta cableada al contrato: guion opcional con borrador, subida de a un
    archivo, POST /montage, fase assembly al reabrir y en recientes, montage_job en /compose."""
    html = (STATIC / "index.html").read_text()
    js = _inline_script()
    assert 'id="guion"' in html and "Guion (opcional)" in html and "Reelfy lo deduce de las tomas" in html
    assert 'id="goAI"' in html and "Armar con IA" in html and "Armar a mano" in js
    assert "modo lote automático" not in html, "texto viejo: varios archivos ya no son un lote"
    assert 'fetch("/montage"' in js and 'x.open("POST","/sources")' in js
    assert 'fd.append("files",file)' in js, "un archivo por peticion (progreso y transcripcion tempranos)"
    assert "localStorage" in js and "reelfy.guion.draft" in js
    for etapa in ("Subiendo", "Escuchando cada clip", "Eligiendo las mejores tomas"):
        assert etapa in js, etapa
    for txt in ("Usar esta toma", "Descartadas", "Sin toma", "Guion detectado", "Rearmar"):
        assert txt in js, txt
    assert 'j.phase==="assembly"' in js and "mtReopen" in js, "reabrir un armado (?job=)"
    assert "FXCore.projectState" in js, "proyectos recientes con armados"
    assert "montageJob:ASM.montage?ASM.montageJob:null" in js and "FXCore.composeBody(" in js
    assert "FXCore.recordingOrder" in js, "los clips entran en orden de grabacion"


def test_montaje_revision_ui_cableado():
    """Arreglos de la revision de "Armar con IA" que el E2E del navegador ejercita (este chequeo corre
    siempre, sin navegador): transcribir solo lo que puede armarse con IA, guardado de las tomas hasta
    que la composicion termine, perfil explicito, teclado del panel, "Escuchar" que un salto termina,
    subidas fallidas, guion visible desde el ensamble y limpieza al borrar un proyecto."""
    js = _inline_script()
    fx = (STATIC / "fx.js").read_text()
    css = (STATIC / "fx.css").read_text()
    # transcripcion por clip: la UI la pide solo cuando se puede armar con IA; el Studio nunca
    assert 'fd.append("transcribe",words?"1":"0")' in js and "uploadEach(files,prog,null,nv>1)" in js
    # Armar con IA pide la transcripcion de cada clip (los recursos del guion no se transcriben)
    assert "},my.hold,i=>!my.assetIdx.has(i));" in js, "Armar con IA pide la transcripcion de cada clip"
    assert 'fd.append("files", file); fd.append("transcribe", "0");' in fx
    # los cambios de tomas se guardan hasta que la composicion termina (review/done), no al aceptarla
    assert "if(ASM.montageJob) asmSaved(ASM.montageJob,null);" not in js
    assert "asmSaved(JOB,null);          // ya compuesto" in js and "if(ASM.montageJob) asmSaveNow();" in js
    # "Sin perfil" viaja explicito (null)
    assert "body.profile=prof&&body.director?prof:null;" in js
    # teclado: Espacio/Enter sobre botones y resumenes los activan; el foco vuelve tras re-dibujar
    assert "closest('button,summary,a,[role=\"tab\"],[role=\"button\"]')" in js and "mtRefocus(box,fk)" in js
    # "Escuchar" una parte: un salto del usuario la termina; el hueco tambien respeta su fin
    assert "function asmGoto(t,seek,internal){" in js and "if(!internal&&ASM.stopAt!=null) asmPause();" in js
    assert "asmGoto(clipEnd(at.clip)+0.001,true,true)" in js
    # dividir un clip con volumen automatico: las dos mitades lo conservan
    assert "if(c.volAuto) nc.volAuto=true;" in js
    # subidas fallidas: se decide antes de armar; desde el ensamble el guion se ve y se confirma
    assert "await mtRetryFailed(rows,files,my);" in js and "Reintentar la subida" in js and "Armar sin ellos" in js
    assert 'const draft=!$("#guionWrap").hidden?' in js and 'MT.back=true; MT.script=($("#guion")' not in js
    # fuentes agregadas en el ensamble de un montaje: registradas en el proyecto
    assert '"/montage/"+encodeURIComponent(ASM.montageJob)+"/sources"' in js
    # borrar un proyecto borra sus cambios guardados; los de proyectos que ya no existen se podan
    assert "asmSaved(j.id,null);" in js and "asmSavedPrune(" in js
    # "Rearmar" deshabilitado se ve deshabilitado (la regla va DESPUES de .mt-btn.pri)
    assert css.index(".mt-btn.pri:disabled") > css.index(".mt-btn.pri{")


def _clips_montaje(dst):
    """Los 8 clips SINTETICOS de tests/js/montage_fixture.js: color de prueba distinto, un tono y su
    creation_time (UTC) para que el servidor los ordene por fecha de grabacion."""
    import datetime as dt
    import paths
    code = "process.stdout.write(JSON.stringify(require(%s).CLIPS))" % json.dumps(str(SPIKE / "tests" / "js" / "montage_fixture.js"))
    clips = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True, timeout=60).stdout)
    dst.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(clips):
        utc = dt.datetime.fromisoformat(c["created"]).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        d = c["dur"]
        subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"testsrc2=size=360x640:rate=30:duration={d},hue=h={i * 45}", "-f", "lavfi", "-i",
                        f"sine=frequency={220 + 40 * i}:sample_rate=48000:duration={d}", "-c:v", "libx264", "-preset",
                        "ultrafast", "-crf", "34", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-shortest",
                        "-metadata", f"creation_time={utc}", "-f", "mov", str(dst / c["name"])], check=True, timeout=300)
    return clips


def test_montaje_e2e_en_navegador(tmp_path):
    """E2E opcional de "Armar con IA" en un navegador real (tests/js/montage_e2e.mjs) contra un
    servidor AISLADO (tests/devserver.sh) con 8 clips sinteticos. Se activa con REELFY_PLAYWRIGHT=ruta
    a playwright-core (usa Chrome; REELFY_PW_CHANNEL cambia el canal; REELFY_E2E_SHOTS guarda capturas)."""
    import os
    import signal
    import socket
    import sys
    import time
    import urllib.request
    pw = os.environ.get("REELFY_PLAYWRIGHT")
    if not (pw and NODE):
        pytest.skip("E2E del montaje desactivado (define REELFY_PLAYWRIGHT)")
    sys.path.insert(0, str(SPIKE / "scripts"))
    data, clipdir = tmp_path / "data", tmp_path / "clips"
    (data / "input").mkdir(parents=True)
    _clips_montaje(clipdir)
    port = None
    for p in range(8600, 8700):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                port = p
                break
    assert port, "sin puerto libre en 8600-8699"
    srv = subprocess.Popen([str(SPIKE / "tests" / "devserver.sh"), str(port), str(data)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        for _ in range(120):
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{port}/setup", timeout=2).status == 200:
                    break
            except Exception:  # noqa: aun arrancando
                time.sleep(0.5)
        out = os.environ.get("REELFY_E2E_SHOTS") or str(tmp_path / "shots")
        r = subprocess.run([NODE, str(SPIKE / "tests" / "js" / "montage_e2e.mjs"), f"http://127.0.0.1:{port}", str(clipdir), out],
                           capture_output=True, text=True, timeout=900, env={**os.environ})
        assert r.returncode == 0, r.stdout[-6000:] + r.stderr[-3000:]
    finally:
        try:
            os.killpg(srv.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        srv.wait(timeout=30)


# ================================================================ recursos del guion y aceleraciones
@needs_node
def test_recursos_y_aceleraciones_unitarias_node():
    """Logica pura de los recursos del guion (marcas, clasificacion, colocacion con su linea), los
    cambios de camara con aceleracion y el color de acento (tests/js/recursos.test.js)."""
    r = subprocess.run([NODE, "--test", str(SPIKE / "tests" / "js" / "recursos.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


def _port_scenarios(n=60, seed=11):
    """Lineas de tiempo SINTETICAS de un montaje (camaras, uniones, palabras y duraciones al azar)."""
    rnd = random.Random(seed)
    out = []
    for k in range(n):
        n_it = rnd.randint(2, 6)
        sids = [f"{c}{k}" for c in "ABCDEFG"][:max(2, n_it)]
        cams = {s: (rnd.choice([1, 2, 3, None]) if rnd.random() < 0.9 else None) for s in sids}
        order = [{"sid": s, "name": s + ".MOV", "setup": cams[s]} for s in sids]
        tl, slots, words, durs, t = [], [], {}, {}, 0.0
        for i in range(n_it):
            sid = rnd.choice(sids)
            a = round(rnd.uniform(0.0, 2.0), 2)
            b = round(a + rnd.uniform(0.6, 4.0), 2)
            durs[sid] = max(durs.get(sid, 0.0), round(b + rnd.uniform(0.0, 1.5), 2))
            slot, cand = f"s{i // 2 + 1:02d}", f"c{i}"
            flags = rnd.choice([[], ["intercalado"], ["parcial"], ["variante"]])
            slots.append({"id": slot, "lines": [f"L{i // 2 + 1:02d}"], "chosen": cand,
                          "cands": [{"id": cand, "sid": sid, "in": a, "out": b, "setup": rnd.choice([1, 2, None]),
                                     "flags": flags}]})
            tl.append({"slot": slot, "cand": cand, "sid": sid, "in": a, "out": b, "start": round(t, 6),
                       "join": None if not tl else rnd.choice(["splice", "line", "section"])})
            t += b - a
        for s in sids:
            ws, x = [], rnd.uniform(0.0, 0.5)
            while x < durs.get(s, 3.0) + 1.0:
                w = rnd.uniform(0.1, 0.6)
                ws.append({"text": "w", "start": round(x, 3), "end": round(x + w, 3)})
                x += w + rnd.choice([0.02, 0.05, 0.1, 0.3, 0.5, 0.9])
            words[s] = ws
            durs.setdefault(s, 3.0)
        # varias partes con el mismo id de slot: el JS los agrupa igual (slotClipIdx) que el motor (timeline)
        merged = {}
        for s in slots:
            merged.setdefault(s["id"], {"id": s["id"], "lines": s["lines"], "chosen": s["chosen"], "cands": []})
            merged[s["id"]]["cands"] += s["cands"]
        overrides = {i: rnd.choice([True, False]) for i in range(1, n_it) if rnd.random() < 0.25}
        out.append({"montage": {"order": order, "slots": list(merged.values()), "timeline": tl},
                    "words": words, "durs": durs, "overrides": overrides})
    return out


@needs_node
def test_port_de_montage_en_fxcore_igual_al_motor(tmp_path):
    """fxcore.js decide lo mismo que scripts/montage.py (el dueno de las reglas): marcas del guion
    (split_annotation), nombres de archivo (match_asset / classify) y aceleraciones en los cambios de
    camara (plan_ramps, con lo que eligio el usuario). Asi el timeline del ensamble muestra lo que
    compose va a hacer."""
    import copy
    import sys
    sys.path.insert(0, str(SPIKE / "scripts"))
    import montage as M
    lines = ["Ya abrimos. - Agregar video_horno", "Hola.- AÑADIR Foto Pan.jpg", "Masa — insertar foto_masa, el video_horno",
             "Cada mañana | Muestra la foto_pan.", "Lanzamos [imagen_cartel]", "Dos {logo} [Poner foto uno]", "Hola [pausa]",
             "[pausa] Hola", "un e-mail bien", "foto1 y foto2 - Pon foto1 y foto2", "Esto - add clip_a; clip_b",
             "Frase normal - agregando cosas", "Hola [Horno]", "Termina {x} [y_2] - Agregar z_3", "sin nada"]
    known = ["horno.mp4", "foto_masa.jpg"]
    files = [{"sid": "a1", "name": "PAN_0101.MOV", "kind": "video"}, {"sid": "b2", "name": "Video Horno.mp4", "kind": "video"},
             {"sid": "c3", "name": "foto_pan_dulce.jpg", "kind": "image"}, {"sid": "d4", "name": "video_a.mp4", "kind": "video"},
             {"sid": "e5", "name": "video_b.mp4", "kind": "video"}]
    names = ["video_horno", "foto_pan", "video", "b2", "video_horno_de_lena_grande", "nada", "VIDEO-A"]
    script = "Uno. - Agregar video_horno\nDos [foto_pan]\n- Agregar video_a\nTres {falta_x}"
    sc = _port_scenarios()
    got = _node_json(tmp_path, "port.js", """
const lines=%s, known=%s, files=%s, names=%s, script=%s, sc=%s;
const out={lines:lines.map(l=>[C.lineRefs(l).text,C.lineRefs(l).names,C.lineRefs(l,known.map(n=>({name:n}))).names]),
  match:names.map(n=>C.matchAsset(n,files)), cls:C.classifyFiles(files,script), ramps:[]};
sc.forEach(s=>{
  const src=Object.keys(s.durs).map(id=>({id,type:"video",dur:s.durs[id]}));
  const cs=C.montageClips(s.montage,src,(()=>{let n=0;return()=>"k"+n++;})());
  Object.keys(s.overrides).forEach(i=>{ cs[+i].rampUser=s.overrides[i]; });
  C.syncRamps(cs,{words:s.words});
  out.ramps.push(cs.map(c=>[c.ramp_in||null,c.ramp_out||null]));
});
process.stdout.write(JSON.stringify(out));
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (lines, known, files, names, script, sc)))
    keys = [M.asset_key(n) for n in known]
    for l, (t, ns, ns_known) in zip(lines, got["lines"]):
        assert [t, ns] == list(M.split_annotation(l, [])), l
        assert ns_known == M.split_annotation(l, keys)[1], l
    for n, i in zip(names, got["match"]):
        f = M.match_asset(n, files)
        assert i == (files.index(f) if f else -1), n
    cls = M.classify(files, script)
    assert [files[i]["sid"] for i in got["cls"]["clips"]] == [f["sid"] for f in cls["clips"]]
    assert sorted(files[i]["sid"] for i in got["cls"]["assets"]) == sorted(f["sid"] for f in cls["assets"])
    assert {r["name"]: (files[r["idx"]]["sid"] if r["idx"] >= 0 else None) for r in got["cls"]["refs"]} == cls["refs"]
    n_ramps = 0
    for s, js in zip(sc, got["ramps"]):
        m = copy.deepcopy(s["montage"])
        clips = [{"sid": sid, "words": w, "dur": s["durs"][sid]} for sid, w in s["words"].items()]
        tl = M.plan_ramps(m, clips, dict(s["overrides"]))
        py = [[x.get("ramp_in"), x.get("ramp_out")] for x in tl]
        assert js == py, (s["montage"]["timeline"], s["overrides"], js, py)
        n_ramps += sum(1 for a, b in py if a or b)
    assert n_ramps >= 10, "los escenarios deben ejercitar aceleraciones de verdad"


@needs_node
def test_colocacion_de_recursos_igual_al_motor(tmp_path):
    """fxcore.placeInserts con las tomas del montaje coloca cada recurso donde montage.plan_inserts
    (line_times de la toma en uso, reparto de varios por linea, minimo 2 s, sin encimarse y el ajuste
    del video: speed de 0.8x a 1.4x, trim o su propia duracion)."""
    import sys
    sys.path.insert(0, str(SPIKE / "scripts"))
    import montage as M
    rnd = random.Random(5)
    scen = []
    for k in range(40):
        n = rnd.randint(1, 5)
        lines, slots, tl, t = [], [], [], 0.0
        for i in range(n):
            lid = f"L{i + 1:02d}"
            ins = rnd.choice([[], ["foto_a"], ["video_b"], ["foto_a", "video_b"], ["falta_c"]])
            lines.append({"id": lid, "text": "linea", "inserts": ins})
            a = round(rnd.uniform(0, 1), 2)
            b = round(a + rnd.uniform(0.8, 5.0), 2)
            w0 = round(a + rnd.uniform(0.05, 0.3), 2)
            w1 = round(max(w0 + 0.1, b - rnd.uniform(0.05, 0.3)), 2)
            sid = f"S{i}"
            slots.append({"id": f"s{i}", "lines": [lid], "chosen": "c1", "cands": [
                {"id": "c1", "sid": sid, "in": a, "out": b, "line_times": {lid: [w0, w1]}}]})
            tl.append({"slot": f"s{i}", "cand": "c1", "sid": sid, "in": a, "out": b, "start": round(t, 6)})
            t += b - a
        m = {"script": {"lines": lines}, "slots": slots, "timeline": tl}
        assets = [{"sid": "FA", "name": "foto_a.jpg", "kind": "image", "dur": 0},
                  {"sid": "VB", "name": "video_b.mp4", "kind": "video", "dur": round(rnd.uniform(0.5, 9.0), 2)}]
        m["inserts"] = M.plan_inserts(m, assets)
        scen.append({"m": m, "assets": assets})
    got = _node_json(tmp_path, "ins.js", """
const sc=%s;
process.stdout.write(JSON.stringify(sc.map(s=>{
  const src=s.assets.map(a=>({id:a.sid,type:a.kind,dur:a.dur}));
  const cs=C.montageClips(s.m,src,(()=>{let n=0;return()=>"k"+n++;})());
  return C.placeInserts(s.m,cs,{sources:src}).map(x=>[x.id,x.start,x.end,x.fit,x.speed,x.status]);
})));
""" % json.dumps(scen))
    n = 0
    for s, js in zip(scen, got):
        py = [[x["id"], x["start"], x["end"], x["fit"], x["speed"], x["status"]] for x in s["m"]["inserts"]]
        assert len(js) == len(py)
        for a, b in zip(js, py):
            assert a[0] == b[0] and a[3:] == b[3:] and abs(a[1] - b[1]) < 2e-3 and abs(a[2] - b[2]) < 2e-3, (a, b)
            n += 1
    assert n >= 20


def test_recursos_y_aceleraciones_cableado():
    """La UI de los recursos del guion y de los cambios de camara esta cableada al contrato."""
    html = (STATIC / "index.html").read_text()
    js = _inline_script()
    fx = (STATIC / "fx.js").read_text()
    css = (STATIC / "fx.css").read_text()
    # inicio: imagenes elegibles, "Recursos del guion" con su estado y "Agregar archivo"
    assert 'accept:"video/*,image/*",multiple:true' in js and 'id="guionRefs"' in html
    for txt in ("Recursos del guion", "Encontrado entre tus archivos", "Agregar archivo", "Falta"):
        assert txt in js, txt
    assert "FXCore.classifyFiles(" in js and "assets:mtAssets().map(s=>s.id)" in js
    # un recurso no se transcribe ni cuenta como clip escuchado
    assert "i=>!my.assetIdx.has(i)" in js and '"Recurso del guion"' in js
    # ensamble: pista Recursos, marcadores de camara con deshacer, panel y cuerpo de /compose
    assert 'id="asmMediaTrk"' in html and 'id="asmMedia"' in html
    assert "FXCore.syncRamps(" in js and "FXCore.toggleRamp(" in js and "asmPush();" in js
    assert "Cambio de cámara con aceleración" in js and 'class="rampmk' not in js and 'b.className="rampmk"' in js
    assert '"/montage/"+encodeURIComponent(job)+"/asset"' in js
    assert "inserts:ASM.montage?asmInserts():null" in js
    assert "insEdits:ASM.insEdits||{}" in js, "los recursos editados viajan en deshacer y en el guardado local"
    assert "FloatCard" in js and "/static/floatcard.js" in html
    # Studio: pista Recursos (seleccionar, mover, recortar, quitar, cambiar archivo) y vista previa
    assert 'media: { label: "Recursos"' in fx and "function inspectMedia(" in fx and "FX.replaceMedia" in fx
    assert "FCm.drawCard(" in fx and "FCm.bgFilter(" in fx and 'push({ track: "media", id: it.id, bb, fixed: true })' in fx
    # color de tu marca: un solo control (Estilo › Color de tu marca), amarillo por defecto, sin azul;
    # la palabra que dices usa ese color (fila enlazada, sin selector propio)
    es = (STATIC / "estilo.js").read_text()
    assert 'id="stBrand"' in html and "C.applyAccent(PLAN" in fx and "FX.setAccent(" in es
    assert 'mode: "linked"' in (STATIC / "fxcore.js").read_text() and 'E.open("stBrand", { focus: "project.accent" })' in es
    for f in ("index.html", "fx.js", "fxcore.js", "fx.css"):
        assert "84a2f8" not in (STATIC / f).read_text().lower(), f"{f}: azul fijo"
    assert "--trk-media" in css and ".rampmk" in css and ".mt-res" in css
    # el repo es publico: nada del material real de un cliente
    for f in ("index.html", "fx.js", "fxcore.js", "fx.css"):
        low = (STATIC / f).read_text().lower()
        for w in ("carrusel", "meta_ads", "dentista", "paseo club", "paseoclub"):
            assert w not in low, f"{f}: {w}"


def test_version_de_floatcard_es_el_hash_del_contenido():
    """floatcard.js (vista en vivo de los recursos, port de motion/media.py) tambien va con ?v=<hash>."""
    import hashlib
    h = hashlib.sha1((STATIC / "floatcard.js").read_bytes()).hexdigest()[:8]
    vs = re.findall(r'/static/floatcard\.js\?v=([0-9a-f]+)', (STATIC / "index.html").read_text())
    assert vs == [h], f"version {vs} != {h}: recalcula el ?v= de floatcard.js"


@needs_node
def test_revision_marcas_y_nombres_igual_al_motor(tmp_path):
    """fxcore (lineRefs / matchAsset / classifyFiles) decide lo MISMO que montage.py en los casos de la
    revision: frases con guion largo y verbo (se dicen), '[1]' / '{a}' (nunca por prefijo), una toma
    cuyo nombre es el principio del recurso pedido (sigue siendo toma), al reves solo imagenes."""
    import sys
    sys.path.insert(0, str(SPIKE / "scripts"))
    import montage as M
    lines = ["Nuestra meta — agregar valor a cada familia del barrio.", "Súmate — agrega tu negocio hoy.",
             "Tenemos un reto - mostrar resultados reales.", "Hay una regla | pon tu marca primero.",
             "Te damos una muestra - muestra gratis para ti.", "La clave — poner al cliente primero",
             "Súmate — agrega 3 negocios hoy", "Hola. - Agregar foto_1, la tienda de mi barrio",
             "Abrimos temprano - agregar foto del local", "Hola. — Mostrar Foto Local.jpg", "Somos. [1]",
             "Y bien. {a}", "Hola [agregar foto del local]", "Uno - Pon foto1 y foto2", "Vamos - add e-mail"]
    known = ["Foto del local.HEIC", "foto1.png"]
    files = [{"sid": "1f3a9c0d2b4e", "name": "IMG_0375.MOV", "kind": "video"},
             {"sid": "a07c55e1d9f2", "name": "Marta.mov", "kind": "video"},
             {"sid": "b17c55e1d9f3", "name": "Marta 2.mov", "kind": "video"},
             {"sid": "c27c55e1d9f4", "name": "logo.png", "kind": "image"},
             {"sid": "d37c55e1d9f5", "name": "foto_local_final.jpg", "kind": "image"},
             {"sid": "e47c55e1d9f6", "name": "toma_pan_1.mov", "kind": "video", "has_words": True}]
    names = ["1", "1f3a", "1f3a9c0d2b4e", "img", "img_03", "marta_en_el_taller", "logo_nuevo", "foto_local",
             "foto", "toma_pan", "Marta"]
    script = ("Somos tres socios. [1]\nHola, soy Marta. - Agregar marta_en_el_taller\nY {logo_nuevo}\n"
              "Nuestra meta — agregar valor a cada familia.\nPan. {toma_pan}\nFin. [foto_local]")
    got = _node_json(tmp_path, "rev.js", """
const lines=%s, known=%s, files=%s, names=%s, script=%s;
process.stdout.write(JSON.stringify({lines:lines.map(l=>[C.lineRefs(l).text,C.lineRefs(l).names,C.lineRefs(l,known.map(n=>({name:n}))).names]),
  match:names.map(n=>C.matchAsset(n,files)), cls:C.classifyFiles(files,script)}));
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (lines, known, files, names, script)))
    keys = [M.asset_key(n) for n in known]
    for l, (t, ns, ns_known) in zip(lines, got["lines"]):
        assert [t, ns] == list(M.split_annotation(l, [])), l
        assert ns_known == M.split_annotation(l, keys)[1], l
    for n, i in zip(names, got["match"]):
        f = M.match_asset(n, files)
        assert i == (files.index(f) if f else -1), n
    cls = M.classify(files, script)
    assert [files[i]["sid"] for i in got["cls"]["clips"]] == [f["sid"] for f in cls["clips"]]
    assert sorted(files[i]["sid"] for i in got["cls"]["assets"]) == sorted(f["sid"] for f in cls["assets"])
    assert {r["name"]: (files[r["idx"]]["sid"] if r["idx"] >= 0 else None) for r in got["cls"]["refs"]} == cls["refs"]
    # las tomas se quedan como tomas; la imagen pedida al reves y la del prefijo por palabras, recursos
    assert [f["name"] for f in cls["clips"]] == ["IMG_0375.MOV", "Marta.mov", "Marta 2.mov", "toma_pan_1.mov"]
    assert cls["refs"] == {"marta_en_el_taller": None, "logo_nuevo": "c27c55e1d9f4",                  # "[1]": acotacion
                           "toma_pan": None, "foto_local": "d37c55e1d9f5"}


@needs_node
def test_ajuste_del_video_igual_en_ensamble_y_compose(tmp_path):
    """El ensamble (fxcore.insertFit) y compose._media_fit ajustan IGUAL un video a su tramo: mas largo
    hasta 1.4x se acelera, mas se recorta, un poco mas corto (desde 0.8x) se alenta, mas corto dura lo
    suyo (el ensamble acorta el recurso y compose lo pasa a 1x)."""
    import sys
    sys.path.insert(0, str(SPIKE / "scripts"))
    import compose
    grid = [round(0.5 + 0.05 * k, 2) for k in range(31)]          # d / slot de 0.5 a 2.0
    slot = 5.0
    got = _node_json(tmp_path, "fit.js", "process.stdout.write(JSON.stringify(%s.map(r=>C.insertFit('video', r*%r, %r, 0))));"
                     % (json.dumps(grid), slot, slot))
    for r, f in zip(grid, got):
        d = r * slot
        s2 = f.get("len", slot)                                    # el ensamble acorta el recurso
        fit, sp, _src = compose._media_fit("video", d, s2, {"fit": f["fit"], "speed": f["speed"], "src_in": 0})
        if f.get("len") is not None or (f["fit"] == "speed" and f["speed"] == 1):
            assert (fit, sp) == ("hold", 1.0), (r, f, fit, sp)                 # a 1x: lo mismo
        else:
            assert fit == f["fit"] and sp == pytest.approx(f["speed"], abs=1e-3), (r, f, fit, sp)


def test_revision_ui_cableado():
    """Regresiones de UI de la revision: la tarjeta de un recurso se dibuja ANTES de graficos (como el
    render); elegir un recurso suelta el clip (Supr quita el recurso, nunca un clip); 'Deshacer' tras
    cambiar el archivo de un recurso vuelve al de antes; el marcador de la aceleracion no tapa el
    agarre de recorte."""
    fx = (STATIC / "fx.js").read_text()
    # el cuerpo de la composicion en vivo (FX.drawLive -> FX.drawLiveTo -> liveBody)
    live = fx[fx.index("function liveBody("):]
    live = live[:live.index("pauseIdleMedia(")]
    assert live.index('activeRange("media", t)') < live.index('activeRange("graphics", t)')
    assert live.index('activeRange("cutaways", t)') < live.index('activeRange("media", t)')
    js = _inline_script()
    sel_ins = js[js.index("function asmSelectInsert("):]
    sel_ins = sel_ins[:sel_ins.index("\n}")]
    assert "ASM.sel=null" in sel_ins
    sel = js[js.index("function asmSelect(track,id){"):]
    assert "ASM.insSel=null" in sel[:sel.index("\n")]
    key = js[js.index('e.key==="Delete"||e.key==="Backspace"'):]
    key = key[:key.index('else if(e.key==="s"')]
    assert key.index("ASM.insSel&&!ASM.sel") < key.index("asmDelSel()") and "mtInsertToggle(" in key
    f = js[js.index("function mtInsertFile("):]
    f = f[:f.index("function mtInsertToggle(")]
    pin = f.index("ASM.insEdits[id]=Object.assign({},ASM.insEdits[id]||{},{sid:cur.sid||null")
    assert pin < f.index("asmPush();") < f.index('"/montage/"+encodeURIComponent(job)+"/asset"')
    assert f.rindex("{sid:src.id,") > f.index('"/montage/"+encodeURIComponent(job)+"/asset"')
    assert "delete e.sid" not in f
    css = (STATIC / "fx.css").read_text()
    rule = re.search(r"\.rampmk\{([^}]*)\}", css).group(1)
    assert "top:0" in rule and "top:50%" not in rule and "height:16px" in rule
    # el ajuste que alenta un video no dice "acelerado"
    assert "más lento" in js and "más lento" in fx


# ================================================================ hoja "Exportar"
@needs_node
def test_exportar_unitarias_node():
    """Logica pura de la hoja "Exportar" (tests/js/export.test.js): matriz de resoluciones por
    formato (nunca por encima de la fuente), nombre por defecto y saneado, preferencias recordadas,
    estimacion, rutas y errores en espanol."""
    r = subprocess.run([NODE, "--test", str(SPIKE / "tests" / "js" / "export.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


def test_exportar_hoja_cableada():
    """"Exportar" abre la hoja (no exporta directo); la hoja tiene formato, resolucion, calidad,
    nombre, carpeta (SOLO lectura: se cambia con el selector nativo) y shorts; el export manda
    resolution/quality/name/folder; el error ofrece "Exportar en 1080p"; la etapa "recompose"."""
    html = (STATIC / "index.html").read_text()
    js = _inline_script()
    fx = (STATIC / "fx.js").read_text()
    render = js[js.index('$("#render").onclick'):]
    assert render[:render.index("\n")].count("FX.openExport()") == 1
    sheet = html[html.index('id="xpSheet"'):]
    sheet = sheet[:sheet.index("</div>\n</div>")]
    for i in ("xpFmt", "xpRes", "xpQual", "xpName", "xpDir", "xpPick", "xpShorts", "xpEst", "xpGo", "xpCancel"):
        assert f'id="{i}"' in sheet, i
    # la carpeta no se escribe: ningun <input> para la ruta, solo el boton del selector nativo
    assert len(re.findall(r"<input", sheet)) == 2          # el nombre y el interruptor de shorts
    assert 'class="xp-path"' in sheet and "Cambiar…" in sheet
    assert '"/pick-folder"' in fx and '"/export/estimate?"' in fx and '"/settings/export"' in fx
    assert '"/export/options?"' in fx and '"/export/settings"' in fx and '"/export/save"' in fx
    assert '"/reveal"' in fx and '"/open"' in fx
    body = fx[fx.index("FX.exportBody = function"):]
    body = body[:body.index("\n  };")]
    for k in ("resolution:", "quality:", "name:", "folder:", "b.shorts =", "b.export = { res:", "b.export.shorts", "b.export.folder"):
        assert k in body, k
    assert "C.sanitizeFileName(" in body and "xpSrvQ(" in body      # calidad en el idioma del servidor
    start = js[js.index("async function startExport("):]
    start = start[:start.index("\n}")]
    assert "FX.exportBody(xo)" in start and "FXCore.exportError(" in start
    assert 'id="retry1080"' in html and "failExport(j.error" in js
    assert '["recompose", "Alta resolución"]' in fx and "Preparando el video en ${hiLbl}" in fx
    # el teclado de la hoja no llega al editor (Espacio reproducia el video detras)
    wire = fx[fx.index("function xpWire()"):]
    assert 'sh.addEventListener("keydown"' in wire and "e.stopPropagation()" in wire


def _plan_export(dur=24.0):
    """Plan v2 sintetico corto (vertical, dos shorts) para la hoja "Exportar"."""
    from motion import schema
    words, t = [], 0.6
    texto = ("Hoy les traigo la bicicleta que uso todos los días para ir al trabajo. Es una bici urbana "
             "sencilla y ya tengo una opinión clara. Los frenos de disco responden bien con lluvia. "
             "La cadena casi no suena y el asiento es cómodo para trayectos de media hora.").split()
    while t < dur - 1.5:
        for tok in texto:
            if t >= dur - 1.5:
                break
            d = 0.18 + 0.03 * len(tok)
            words.append({"text": tok, "start": round(t, 3), "end": round(t + d, 3)})
            t += d + 0.08
    phrases, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == 4 or w["text"][-1] in ".,":
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)
    plan = schema.normalize({"version": 2, "duration": dur, "dims": [1080, 1920], "phrases": phrases, "camera": [], "beats": [],
                             "broll": [], "cuts": [],
                             "highlights": [{"start": 1.0, "end": 9.0, "title": "Frenos con lluvia", "enabled": True},
                                            {"start": 11.0, "end": 19.0, "title": "La cadena", "enabled": True}],
                             "shots": [{"start": 0, "end": dur, "src": "bici.mov"}], "fx": {}})
    plan["fx"]["music"] = {"enabled": False, "source": "none"}
    plan["fx"]["grade"]["enabled"] = False
    plan["topic"] = "Reseña de una bici urbana"
    plan["studio"] = {"fmt": "9:16", "lang": "es"}
    return plan


def test_plan_sintetico_de_exportar_es_valido():
    from motion import schema
    p = _plan_export()
    assert p["version"] == 2 and isinstance(p["fx"], dict)
    assert schema.normalize(json.loads(json.dumps(p)))["duration"] == p["duration"]
    assert len([h for h in p["highlights"] if h["enabled"]]) == 2


def test_exportar_e2e_en_navegador(tmp_path):
    """E2E de la hoja "Exportar" en un navegador real (tests/js/export_e2e.mjs) contra un servidor
    AISLADO (tests/devserver.sh) con un proyecto sintetico. El render y los endpoints del export se
    simulan en el navegador (page.route), en la forma de app/server.py (GET /export/options, `export`
    en POST /render...) y en la minima del contrato: prueba la UI (formato, resolucion, calidad,
    nombre, carpeta con el selector nativo, estimacion, errores, resultados con Mostrar/Abrir,
    "Guardar de nuevo"), simetria, texto >= 11 px y contraste AA. Se activa con REELFY_PLAYWRIGHT=ruta a playwright-core;
    REELFY_E2E_SHOTS guarda las capturas."""
    import os
    import signal
    import socket
    import sys
    import time
    import urllib.request
    pw = os.environ.get("REELFY_PLAYWRIGHT")
    if not (pw and NODE):
        pytest.skip("E2E de la hoja Exportar desactivado (define REELFY_PLAYWRIGHT)")
    import paths
    data = tmp_path / "data"
    (data / "input").mkdir(parents=True)
    plan = _plan_export()
    dur = plan["duration"]
    vid = data / "input" / "xp_demo.mp4"
    subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=360x640:rate=30:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={dur}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "34", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "48k", "-shortest",
                    "-movflags", "+faststart", str(vid)], check=True, timeout=300)
    plan["video"] = str(vid)
    code = ("import json,sys; sys.path[:0]=[%r,%r]; import jobstore; jobstore.init(); d=json.load(sys.stdin)\n"
            "for jid,p in d.items():\n"
            "    jobstore.create(jid, kind='analyze', ext='.mp4', plan=p, phase='review', message='Análisis listo')\n"
            "    jobstore.update(jid, state='review', pct=100)\n") % (str(SPIKE / "app"), str(SPIKE / "scripts"))
    subprocess.run([sys.executable, "-c", code], input=json.dumps({"xp_demo": plan}), text=True, check=True,
                   env={**os.environ, "REELFY_DATA": str(data)}, timeout=120)
    port = None
    for p in range(8800, 8900):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                port = p
                break
    assert port, "sin puerto libre en 8800-8899"
    # la carpeta de siempre de los exports, aislada (nunca ~/Movies/Reelfy en una prueba)
    srv = subprocess.Popen([str(SPIKE / "tests" / "devserver.sh"), str(port), str(data)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, start_new_session=True,
                           env={**os.environ, "REELFY_EXPORT_DIR": str(tmp_path / "exports")})
    try:
        for _ in range(120):
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{port}/setup", timeout=2).status == 200:
                    break
            except Exception:  # noqa: aun arrancando
                time.sleep(0.5)
        out = os.environ.get("REELFY_E2E_SHOTS") or str(tmp_path / "shots")
        # "live": la hoja con los endpoints REALES de app/server.py (sin exportar); "server" y
        # "contract": el flujo completo con el servidor simulado en sus dos formas
        for mode in ("live", "server", "contract"):
            r = subprocess.run([NODE, str(SPIKE / "tests" / "js" / "export_e2e.mjs"), f"http://127.0.0.1:{port}", "xp_demo", out, mode],
                               capture_output=True, text=True, timeout=900, env={**os.environ})
            assert r.returncode == 0, mode + ": " + r.stdout[-6000:] + r.stderr[-3000:]
    finally:
        try:
            os.killpg(srv.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        srv.wait(timeout=30)
