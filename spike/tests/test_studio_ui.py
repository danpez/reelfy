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
    for f in (STATIC / "fxcore.js", STATIC / "fx.js", STATIC / "profile.js", inline):
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
    fxjs = (STATIC / "fx.js").read_text() + (STATIC / "profile.js").read_text()
    declared = set(re.findall(r'id="([A-Za-z][\w-]*)"', html)) | set(re.findall(r'id="([A-Za-z][\w-]*)"', fxjs))
    declared |= set(re.findall(r'\.id\s*=\s*"([\w-]+)"', html + fxjs))
    used = set(re.findall(r'\$\("#([A-Za-z][\w-]*)"\)', html + fxjs))
    used |= set(re.findall(r'getElementById\("([A-Za-z][\w-]*)"\)', html + fxjs))
    missing = sorted(u for u in used if u not in declared)
    assert not missing, f"ids usados que no existen: {missing}"


def test_iconos_del_sprite_existen():
    html = (STATIC / "index.html").read_text()
    fxjs = (STATIC / "fx.js").read_text() + (STATIC / "profile.js").read_text()
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
    for f in ("index.html", "fx.js", "fx.css", "fxcore.js", "profile.js"):
        txt = (STATIC / f).read_text()
        # drawEmoji pinta los emojis que vienen en el PLAN (dato del usuario), no UI propia
        hits = [m.group(0) for m in emoji.finditer(txt)]
        assert not hits, f"{f}: {hits[:5]}"


def test_estaticos_se_sirven(tmp_path, monkeypatch):
    """Los archivos nuevos del Studio se sirven por /static con su tipo MIME."""
    import mimetypes
    for f in ("fx.js", "fxcore.js", "profile.js", "fx.css"):
        assert (STATIC / f).exists()
        assert mimetypes.guess_type(f)[0] in ("application/javascript", "text/javascript", "text/css"), f
    html = (STATIC / "index.html").read_text()
    for ref in ("/static/fx.css", "/static/fxcore.js", "/static/fx.js", "/static/profile.js"):
        assert ref in html
    # fxcore antes que fx, fx antes que profile y todos antes del script en linea
    i1, i2, i4, i3 = (html.index("/static/fxcore.js"), html.index("/static/fx.js"), html.index("/static/profile.js"),
                      html.index("<script>"))
    assert i1 < i2 < i4 < i3


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
