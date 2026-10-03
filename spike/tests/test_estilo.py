"""Pestaña Estilo del Studio (claridad de ajustes): la copy de cada ajuste (scripts/motion/ui_copy.json)
cubre el esquema y no trae ingles ni jerga; la logica pura de fxcore y el dibujo de capdraw.js
coinciden con el motor (resorte, contraste de la pastilla, filas de color por estilo y la pastilla
que se DESLIZA con resorte, medida en pixeles contra text.draw_captions); los estaticos nuevos van con
?v=<hash> y sin emojis; y el E2E en un navegador real (opcional).

Datos ficticios: nunca el material de un cliente (el repo es publico)."""
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1]
STATIC = SPIKE / "app" / "static"
COPY = SPIKE / "scripts" / "motion" / "ui_copy.json"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node no esta instalado")
# Datos de un cliente real (nombre, marca, color) que nunca deben entrar al repo publico: se guardan
# como huella (sha256[:16] de la palabra en minusculas) para no escribirlos aqui.
_CLIENTE = {"68a32dd6b2c35412", "7b85175b455060e3", "cb962755a1d7647f"}


def _datos_de_cliente(txt):
    """Palabras de `txt` cuya huella es de datos de un cliente real (vacio = limpio)."""
    import hashlib
    return sorted({w for w in re.findall(r"[0-9a-záéíóúñü]+", str(txt).lower())
                   if hashlib.sha256(w.encode()).hexdigest()[:16] in _CLIENTE})

LEVELS = {"basic", "advanced", "hidden"}
# ajustes que NO vienen del esquema del motor (spec 3.1) + el selector del color de la marca
UI_IDS = {
    "setup.files", "setup.guion", "setup.glossary", "setup.highlights", "setup.profile", "asm.takes", "asm.clip.volume",
    "asm.clip.mute", "asm.clip.fades", "asm.clip.trans", "asm.clip.ramp", "asm.image.dur", "asm.overlay", "asm.snap",
    "asm.style", "clean.level", "clean.cats", "clean.cat.retoma", "clean.cat.arranque_fallido", "clean.cat.autocorreccion",
    "clean.cat.tartamudeo", "clean.cat.error", "clean.cat.digresion", "clean.cat.silencio", "clean.sil", "clean.restore",
    "stage.exact", "stage.audio", "stage.real", "tl.add", "tl.short", "tl.cut", "tl.trim", "tl.snap", "tl.fit",
    "director.redirect", "director.variation", "director.notes", "profile.fields", "profile.links", "profile.use",
    "profile.use.lower_third", "profile.use.follow", "profile.use.booking", "profile.use.end_card", "profile.use.watermark",
    "profile.actions", "brand.logo", "brand.preset", "project.format", "project.lang", "project.air", "proc.enhance",
    "transcript.edit", "shorts.list", "item.actions", "media.style", "export.button", "music.single_home", "project.accent",
    "music.preview", "director.music_sync",
    # fase 4: los selectores de tipo del inspector (galerias con su ejemplo) y el archivo de un recurso
    "item.kind.text", "item.kind.graphics", "item.kind.cutaways", "item.kind.effects", "item.kind.transitions", "item.kind.sfx", "media.file",
}
# claves de los textos fijos de la UI que usa estilo.js (todas deben existir)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⭕]")
INTERNAL = re.compile(r"[{}=]|\bnull\b|\btrue\b|\bfalse\b|\bNone\b")


def _ui():
    return json.loads(COPY.read_text(encoding="utf-8"))


def _node_json(tmp_path, name, code, capdraw=False):
    """Corre `code` en node con fxcore como C (y CapDraw si se pide); devuelve el JSON que escribe."""
    pre = "const C=require(%s);const fs=require('fs');" % json.dumps(str(STATIC / "fxcore.js"))
    if capdraw:
        pre += "global.window={FXCore:C};require(%s);const CD=window.CapDraw;" % json.dumps(str(STATIC / "capdraw.js"))
    js = tmp_path / name
    js.write_text(pre + code)
    r = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout)


# ================================================================ copy
def test_ui_copy_cubre_el_esquema():
    """Cada parametro del esquema tiene etiqueta, que hace (>= 12 letras salvo ocultos), nivel y tipo
    de ejemplo; cada opcion (salvo fuentes y redes) y cada kind tiene nombre; ninguna entrada de la
    copy sobra y los ajustes fuera del esquema son los de la lista. El catalogo trae la copy puesta."""
    from motion import schema as S
    s = S.schema()
    ui = _ui()
    P, O, A = ui["params"], ui["options"], ui.get("aliases") or {}
    EX = set(ui["example_kinds"])
    used = set()

    def entry(ids):
        for i in ids:
            if i in P:
                used.add(i)
                return i, P[i]
        return None, None

    def check(prm, ids, keys=()):
        i, e = entry(ids)
        assert e, f"sin copy: {ids}"
        assert e["label"].strip() and e["level"] in LEVELS and e["ex"] in EX, i
        if e["level"] != "hidden":
            assert len(e["help"]) >= 12, i
        lab = next((e["label_by"][k] for k in keys if k in (e.get("label_by") or {})), e["label"])
        lvl = next((e["level_by"][k] for k in keys if k in (e.get("level_by") or {})), e["level"])
        assert prm["label"] == lab and prm["level"] == lvl and prm["help"] == e["help"], (i, prm.get("label"), lab)
        for f in prm.get("fields") or []:
            if prm["type"] in ("rows", "lines") and f"{i}.{f['k']}" in P:
                check(f, [f"{i}.{f['k']}"], keys)
        return e

    for g in ("captions", "music", "grade", "director"):
        for p in s[g]["params"]:
            check(p, [f"{g}.{p['k']}"])
    for grp in s["common"].values():
        for p in grp:
            check(p, [f"common.{p['k']}"])
    for tk, T in s["tracks"].items():
        keyname = {"text": "text.preset", "sfx": "sfx.name"}.get(tk, f"{tk}.kind")
        for kind, spec in T["items"].items():
            if tk != "media":
                assert kind in O.get(keyname, {}), f"kind sin nombre: {keyname}={kind}"
                assert spec["label"] == O[keyname][kind]["label"]
            for p in spec["params"]:
                ids = [f"{tk}.{kind}.{p['k']}", f"{tk}.{p['k']}", f"common.{p['k']}"]
                e = check(p, ids, (kind, tk))
                if p["type"] in ("font",) or p["k"] in ("platform", "font") or e["level"] == "hidden":
                    continue
                for o in p.get("opts") or []:
                    paths = [f"{tk}.{p['k']}", A.get(f"{tk}.{p['k']}", "")]
                    assert any(str(o) in O.get(pp, {}) for pp in paths), f"opcion sin nombre: {tk}.{p['k']}={o}"
    for g, k in (("captions", "preset"), ("captions", "case"), ("grade", "look"), ("director", "style"),
                 ("music", "source"), ("music", "mood"), ("music", "ending")):
        p = next(q for q in s[g]["params"] if q["k"] == k)
        for o in p["opts"]:
            assert o in O[f"{g}.{k}"], f"{g}.{k}={o}"
    orphans = sorted(set(P) - used)
    assert not orphans, f"copy sin parametro: {orphans}"
    assert set(ui["ui"]) == UI_IDS, sorted(set(ui["ui"]) ^ UI_IDS)
    for i, e in ui["ui"].items():
        assert e["level"] in LEVELS and e["ex"] in EX and len(e["help"]) >= 12, i


def _shown(ui):
    """Todo texto que la UI muestra (5.4 de la spec): nunca notas del build (units, where, dev...)."""
    out = []
    for sec in ("params", "ui"):
        for i, e in ui[sec].items():
            if e["level"] == "hidden":
                continue
            out += [(f"{i}.label", e["label"]), (f"{i}.help", e["help"])]
            for d in ("label_by", "help_by", "note_by"):
                out += [(f"{i}.{d}.{k}", v) for k, v in (e.get(d) or {}).items()]
            if e["ex"] == "texto":
                out.append((f"{i}.ex_ui", e["ex_ui"]))
            if e.get("adv_label"):
                out.append((f"{i}.adv_label", e["adv_label"]))
            for c in (e.get("basic_choices") or []) + (e.get("relative_choices") or []):
                out.append((f"{i}.choice", c[1]))
    for p, vals in ui["options"].items():
        for v, e in vals.items():
            for f in ("label", "help", "badge", "warn", "que_cambia"):
                if e.get(f):
                    out.append((f"opt {p}={v}.{f}", e[f]))
    out += [(f"notes.{k}", v) for k, v in ui["notes"].items()]
    for k, v in ui["sections"].items():
        out += [(f"sections.{k}.title", v["title"]), (f"sections.{k}.help", v["help"])]
    out += [(f"strings.{k}", v) for k, v in ui["strings"].items()]
    return out


def test_ui_copy_sin_ingles_ni_jerga():
    """Nada de la lista negra (ingles o jerga: "en caja", "color de resaltado", "squircle"...), sin
    emojis, sin notas internas ({, }, =, null, true...) y los ejemplos escritos empiezan con «Ej».
    La ortografia ya viene puesta (accent_es no cambia nada) y nada de un cliente real."""
    from motion import schema as S
    ui = _ui()
    deny = [re.compile(r"(?<![\wÁÉÍÓÚáéíóúñÑ])" + re.escape(w) + r"(?![\wÁÉÍÓÚáéíóúñÑ])", re.I) for w in ui["deny"]]
    shown = _shown(ui)
    assert len(shown) > 900
    errs = []
    for where, txt in shown:
        for rx in deny:
            if rx.search(txt):
                errs.append(f"{where}: «{rx.pattern}» en «{txt}»")
        if EMOJI.search(txt):
            errs.append(f"{where}: emoji")
        plain = re.sub(r"\{[a-z]+\}", "", txt) if where.startswith("strings.") else txt   # {variables} de plantilla
        if INTERNAL.search(plain):
            errs.append(f"{where}: nota interna «{txt}»")
        if S.accent_es(txt) != txt:
            errs.append(f"{where}: sin acentos «{txt}» -> «{S.accent_es(txt)}»")
    for sec in ("params", "ui"):
        for i, e in ui[sec].items():
            if e["level"] != "hidden" and e["ex"] == "texto":
                assert e["ex_ui"].startswith("Ej"), i
                if e.get("ex_tpl"):
                    assert e["ex_tpl"].startswith("Ej"), i
    assert not errs, errs[:20]
    assert not _datos_de_cliente(COPY.read_text(encoding="utf-8")), "datos de un cliente en la copy"


def test_schema_trae_la_copy(tmp_path):
    """/fx/schema publica la copy entera (`ui`) y cada parametro ya trae la suya."""
    dst = tmp_path / "schema.json"
    code = ("import sys,json; sys.path[:0]=[%r,%r]; import server as S; r=S.fx_schema(); open(%r,'wb').write(r.body)"
            % (str(SPIKE / "app"), str(SPIKE / "scripts"), str(dst)))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=180,
                       env={**os.environ, "REELFY_DATA": str(tmp_path / "data"), "REELFY_GATED": "0"}, cwd=str(SPIKE))
    assert r.returncode == 0, r.stderr[-3000:]
    s = json.loads(dst.read_text(encoding="utf-8"))
    assert s["ui"]["strings"]["estilo.title"] == "Estilo" and "captions.preset" in s["ui"]["options"]
    cap = {p["k"]: p for p in s["captions"]["params"]}
    assert cap["highlight"]["label"] == "Color de la pastilla"
    assert cap["x"]["level"] == "hidden" and cap["box"]["level"] == "hidden" and cap["enabled"]["level"] == "hidden"
    assert cap["y"]["basic_choices"][-1] == [0.72, "Abajo"] and cap["accent"]["label_by"]["karaoke"] == "Color de relleno"
    assert s["tracks"]["effects"]["items"]["punch"]["label"] == "Golpe de zoom"
    assert s["tracks"]["transitions"]["items"]["flash"]["label"] == "Fogonazo"
    yr = {f["k"]: f for f in cap["y_ranges"]["fields"]}
    assert yr["start"]["label"] == "Desde" and yr["end"]["label"] == "Hasta"
    # accent_es sobre la copy no cambia nada (ya trae su ortografia)
    from motion import schema as S
    snap = json.dumps(S.UI_COPY, ensure_ascii=False)
    S._accent_catalog(json.loads(snap))
    assert json.dumps(S._accent_catalog(json.loads(snap)), ensure_ascii=False) == snap


def test_textos_de_estilo_existen_en_la_copy():
    """Cada texto fijo que usa estilo.js (T("clave")) existe en ui_copy.json → strings; la pestaña
    no escribe textos propios fuera de la copy (salvo numeros y el contenido del usuario)."""
    js = (STATIC / "estilo.js").read_text() + (STATIC / "estilo_proyecto.js").read_text() + (STATIC / "estilo_catalog.js").read_text()
    keys = set(re.findall(r'\bT\("([a-z0-9_.:]+)"', js)) | set(re.findall(r'"(brand\.(?:chip|pv)\.[a-z0-9_.]+)"', js))
    # el inspector (fx.js: ST("clave")) y Montar clips (index.html: asmT("clave")) tambien leen la copy
    fxi = (STATIC / "fx.js").read_text() + (STATIC / "index.html").read_text()
    keys |= set(re.findall(r'\b(?:ST|asmT)\("([a-z0-9_.:]+)"', fxi)) | {"insp.sec." + k for k in ("time", "content", "pos", "style", "anim", "sound")}
    keys |= {k for k in re.findall(r'\bST\(([a-z0-9_.:"? ]+)\)', fxi) for k in re.findall(r'"([a-z0-9_.]+)"', k)}
    strings = _ui()["strings"]
    # claves armadas en el codigo (T("fmt." + f)): cada familia existe completa
    dyn = {k for k in keys if k.endswith(".")}
    keys -= dyn
    fam = {"fmt.": ["9:16", "4:5", "1:1", "16:9", "original"], "lang.": ["es", "en", "pt", "fr", "de", "it"],
           "listen.music.": ["start", "end", "all"], "listen.source.": ["ai", "procedural", "library"],
           "insp.sec.": ["time", "content", "pos", "style", "anim", "sound"]}
    assert dyn <= set(fam), sorted(dyn - set(fam))
    keys |= {p + x for p in dyn for x in fam[p]}
    assert keys and not sorted(k for k in keys if k not in strings), sorted(k for k in keys if k not in strings)
    # sin palabras sueltas en espanol escritas en el codigo de la interfaz (todo pasa por la copy)
    visibles = [w for w in re.findall(r'>([A-ZÁÉÍÓÚ][a-záéíóúñ]+(?: [a-záéíóúñ]+)*)<', js) if w != "Aa"]   # "Aa": muestra de letra
    assert not visibles, visibles[:10]


# ================================================================ paridad con el motor
@needs_node
def test_estilo_js_unitarias():
    r = subprocess.run([NODE, "--test", str(SPIKE / "tests" / "js" / "estilo.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


@needs_node
def test_spring_js_igual_a_python(tmp_path):
    from motion import ease
    rnd = random.Random(3)
    cases = [[round(rnd.uniform(-0.2, 2.5), 4), round(rnd.uniform(0.5, 6.0), 3), round(rnd.uniform(0.0, 1.2), 3)] for _ in range(200)]
    cases += [[0.0, 3.2, 0.42], [0.08, 4.0, 0.78], [0.3, 3.6, 0.62], [1.0, 3.2, 0.42]]
    got = _node_json(tmp_path, "sp.js", "process.stdout.write(JSON.stringify(%s.map(c=>C.spring(c[0],c[1],c[2]))));" % json.dumps(cases))
    for (t, f, z), g in zip(cases, got):
        assert abs(g - ease.spring(t, freq=f, damping=z)) < 1e-9, (t, f, z, g)


@needs_node
def test_contraste_de_la_pastilla_igual_al_motor(tmp_path):
    from motion import text as T
    rnd = random.Random(9)
    cols = ["#%02X%02X%02X" % (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(500)]
    cols += ["#FFD23F", "#1F9D55", "#FFFFFF", "#000000", "#8C8C8C", "#8D8D8D"]
    got = _node_json(tmp_path, "ct.js", "process.stdout.write(JSON.stringify(%s.map(C.contrastOn)));" % json.dumps(cols))
    assert got == [T._contrast(c) for c in cols]


@needs_node
def test_roles_de_color_igual_al_motor(tmp_path):
    """Las filas de color de cada estilo salen de lo que el motor pinta (CAPTION_PRESETS[*].active)."""
    from motion import text as T
    got = _node_json(tmp_path, "ro.js", "process.stdout.write(JSON.stringify(Object.fromEntries(%s.map(p=>[p,C.captionColorRoles(p)]))));"
                     % json.dumps(list(T.CAPTION_PRESETS) + ["none"]))
    want = {"color": ["text", "active"], "wipe": ["text", "fill"], "pill": ["pill", "text"], "none": ["text"]}
    for p, D in T.CAPTION_PRESETS.items():
        assert [r["role"] for r in got[p]] == want[D["active"]], p
        keys = [r["k"] for r in got[p]]
        assert ("highlight" in keys) == (D["active"] == "pill") and ("accent" in keys) == (D["active"] in ("color", "wipe")), p
    assert got["none"] == []


def _cap_ctx(t):
    from motion import ctx as CX
    return CX.demo_ctx(W=1080, H=1920, t=t, quality="final", seed=7)


@needs_node
def test_pastilla_en_vivo_igual_al_motor(tmp_path):
    """La pastilla de la vista en vivo (capdraw.js con las medidas del motor) cae donde el export la
    dibuja (text.draw_captions, medida por color en el cuadro): al aparecer, al crecer, A MITAD DEL
    DESLIZAMIENTO entre dos palabras y ya quieta. Centro +-1.5 % del ancho, ancho +-3 %."""
    import numpy as np
    from motion import ctx as CX
    from motion import text as T
    W, H = 1080, 1920
    txt = "hoy cuido mis dientes"
    words, t = [], 1.0
    for w in txt.split():
        words.append({"text": w, "start": round(t, 3), "end": round(t + 0.24, 3)})
        t += 0.3
    cfg = {"enabled": True, "preset": "boxed", "y": 0.72, "size": 0.052, "font": "Poppins-ExtraBold", "color": "#FFFFFF",
           "accent": "#1F9D55", "highlight": "#FF00AA", "case": "none", "max_words": 4, "hide_during_titles": True}
    ctx0 = _cap_ctx(0.0)
    groups = T.caption_groups([words], cfg, ctx0)
    assert len(groups) == 1 and groups[0]["text"] == txt, groups
    g = groups[0]
    D = T._cap_cfg(cfg, ctx0)
    fs = D["size"] * W
    table = {" ": {"adv": T._space(D["font"], fs), "l": 0, "r": 0, "asc": 0, "desc": 0}}
    for w in txt.split():
        r = T._run(w, D["font"], fs)
        table[w] = {"adv": r.adv, "l": r.ink[0], "r": r.ink[2], "asc": -r.ink[1], "desc": r.ink[3]}
        base = T._strip_marks(w)
        if base != w:
            table[base] = dict(table[w], asc=r.cap)
    # (las dos primeras: la ENTRADA del grupo, que sube con out_expo como el motor)
    ts = [words[0]["start"] - 0.02, words[0]["start"] + 0.05, words[2]["start"] + 0.06, words[3]["start"] + 0.4]
    eng = []
    for tt in ts:
        arr, surf = CX.new_frame(W, H)
        T.draw_captions(surf.getCanvas(), [words], cfg, tt, _cap_ctx(tt))
        a = arr.astype(int)
        m = (a[..., 3] > 30) & (a[..., 0] - a[..., 1] > 60) & (a[..., 2] - a[..., 1] > 30)
        ys, xs = np.nonzero(m)
        assert len(xs) > 50, tt
        eng.append([float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)])
    rect = [g["rect"][0] / W, g["rect"][1] / H, g["rect"][2] / W, g["rect"][3] / H]
    gr = {"words": words, "start": g["start"], "end": g["end"], "lines": g["lines"], "rect": rect, "accent": []}
    code = ("const d=%s; const q=d.table, fs0=%r;"
            "const meas=(t,px)=>{const m=q[t]||q[t.normalize('NFKD').replace(/[\\u0300-\\u036f]/g,'')];const k=px/fs0;return {adv:m.adv*k,l:m.l*k,r:m.r*k,asc:m.asc*k,desc:m.desc*k};};"
            "const noop=()=>{};const ctx=new Proxy({canvas:{width:1080,height:1920},createLinearGradient:()=>({addColorStop:noop})},{get:(o,k)=>k in o?o[k]:noop,set:(o,k,v)=>{o[k]=v;return true;}});"
            "const B={x:0,y:0,w:1080,h:1920,s:1};"
            "const out=d.ts.map(t=>{const bb=CD.drawGroup(ctx,B,d.gr,t,{cfg:d.cfg,srv:true,rangeAlpha:1,yFrac:d.cfg.y,measure:meas,fontCss:()=>'x',ensureFont:()=>'x'});return bb&&bb.pill;});"
            "const L=CD.layout(d.gr,d.cfg,B,{srv:true,measure:meas,fontCss:()=>'x'});const rs=CD.pillRects(L,B,B.y+L.top);"
            "process.stdout.write(JSON.stringify({pills:out,centers:rs.map(r=>(r[0]+r[2])/2)}));"
            % (json.dumps({"table": table, "ts": ts, "gr": gr, "cfg": cfg}), fs))
    got = _node_json(tmp_path, "pill.js", code, capdraw=True)
    for i, (tt, e, p) in enumerate(zip(ts, eng, got["pills"])):
        assert p, tt
        ecx, ecy, ew = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2, e[2] - e[0]
        jcx, jcy, jw = p["x"] + p["w"] / 2, p["y"] + p["h"] / 2, p["w"]
        assert abs(jcx - ecx) <= 0.015 * W and abs(jcy - ecy) <= 0.015 * W, (tt, (jcx, jcy), (ecx, ecy))
        assert abs(jw - ew) <= 0.03 * ew + 2, (tt, jw, ew)
        if i < 2:
            # en la entrada la altura sigue al motor al pixel (antes, con out_cubic, ~3 px abajo)
            assert abs(jcy - ecy) <= 1.5, ("entrada", tt, jcy, ecy)
    # a mitad del deslizamiento: el centro esta ENTRE la palabra 2 y la 3 (se desliza, no salta)
    c1, c2 = got["centers"][1], got["centers"][2]
    p = got["pills"][2]
    jcx = p["x"] + p["w"] / 2
    assert c1 + 0.15 * (c2 - c1) < jcx < c2 - 0.15 * (c2 - c1), (c1, jcx, c2)
    ecx = (eng[2][0] + eng[2][2]) / 2
    assert c1 < ecx < c2, (c1, ecx, c2)


# ================================================================ estaticos
def test_version_de_assets_nuevos():
    """capdraw.js, lookgl.js, estilo.js y estilo_proyecto.js van una vez cada uno con ?v=<sha1 de su
    contenido>, en su orden: fxcore -> capdraw -> lookgl -> fx -> estilo -> estilo_proyecto -> profile
    -> script en linea."""
    import hashlib
    html = (STATIC / "index.html").read_text()
    for f in ("capdraw.js", "lookgl.js", "estilo.js", "estilo_proyecto.js", "estilo_catalog.js"):
        h = hashlib.sha1((STATIC / f).read_bytes()).hexdigest()[:8]
        vs = re.findall(r'/static/%s\?v=([0-9a-f]+)' % re.escape(f), html)
        assert vs == [h], f"{f}: version {vs} != {h}"
    order = [html.index(f"/static/{f}") for f in ("fxcore.js", "capdraw.js", "lookgl.js", "fx.js", "estilo.js", "estilo_proyecto.js", "estilo_catalog.js", "profile.js")]
    assert order == sorted(order) and order[-1] < html.index("<script>")


@needs_node
def test_sintaxis_y_sin_emojis_en_los_nuevos():
    for f in ("capdraw.js", "lookgl.js", "estilo.js", "estilo_proyecto.js", "estilo_catalog.js"):
        r = subprocess.run([NODE, "--check", str(STATIC / f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"{f}: {r.stderr[-2000:]}"
        txt = (STATIC / f).read_text()
        assert not EMOJI.findall(txt), f
        assert not _datos_de_cliente(txt), f"{f}: datos de un cliente"


def test_estilo_cableado():
    """Modo Estilo (tecla 3), panel con sus secciones, resumen en Proyecto, inspector compacto y lo que
    ya no existe: "Caja de fondo", "Posición X", "Mostrar subtítulos" y el viejo "Color de acento"."""
    html = (STATIC / "index.html").read_text()
    fx = (STATIC / "fx.js").read_text()
    es = (STATIC / "estilo.js").read_text()
    css = (STATIC / "fx.css").read_text()
    assert '<button data-v="style" role="tab"' in html and 'id="stylePane"' in html and 'id="styleSummary"' in html
    for sid in ("stCaps", "stBrand", "stLook", "stDirector", "stMusic", "stWho", "stFormat"):
        assert f'id="{sid}"' in html, sid
    assert 'id="accentBody"' not in html and 'id="capProBody"' not in html and "Color de acento" not in html
    # Color del video vive en Estilo (sale del panel Proyecto) y la vista grande compara con el original
    assert 'id="gradeBody"' not in html and 'id="stCompare"' not in html.split('id="stPreview"')[0]
    assert 'id="stCompare"' in html and 'id="stAb"' in html and 'role="slider"' in html
    assert "LookGL" in html and "LKG.draw(CTX,V," in html and "L.fxFilter" in html, "el reproductor dibuja con las tablas del motor"
    assert "FX.syncLook = function" in fx and "/fx/look_luts/" in fx and "fxFilter" in fx
    assert '"stLook"' in es and "LookPicker" in es and "/fx/look_thumbs/" in es and "E.lookChanged = function" in es
    assert "Estilo.onKey(e)" in html
    assert 'm === "style"' in fx and "Estilo.show()" in fx and "Estilo.renderCaptionsInspector(b)" in fx
    assert "function renderAccent" not in fx and "FX.drawLiveTo = function" in fx and "FX.renderMini = function" in fx
    assert "CapDraw.drawGroup(" in fx and "capLayoutOf" not in fx and "capMeasure" not in fx
    assert "C.captionGroupsFor(" in fx, "los grupos aproximados usan el max_words efectivo del estilo"
    assert "FX.setAccent(" in es and "C.applyAccent(" in es and "FX.gesture(" in es and "FX.edit(" in es
    assert "Ticker" in es and "IntersectionObserver" in es and "prefers-reduced-motion" in es
    assert ".studio.mstyle" in css and "--selring" in css
    # la seleccion es neutra (sin naranja) en tarjetas y opciones
    sel_rules = re.findall(r'(\.gcard\[aria-checked="true"\][^{]*\{[^}]*\}|\.seg-h button\[aria-checked="true"\][^{]*\{[^}]*\})', css)
    assert sel_rules and not any("brand" in r for r in sel_rules)
    # «Color de tu marca»: sus ejemplos son elementos que SÍ toman tu color (uno con color propio no
    # cambiaria al elegir otro); la pastilla con su propio color lo dice en vez de fingir que es la marca
    mp = es[es.index("  function miniPlans() {"):]
    mp = mp[:mp.index("  function miniOff(")]
    for k in ('C.paintsAccent("text", it, acc)', 'it.kind === "lower_third" && C.paintsAccent("graphics", it, acc)',
              'it.kind === "cta" && C.paintsAccent("graphics", it, acc)'):
        assert k in mp, k
    # (la pastilla propia se guarda en el plan: studio.pillOwn, con su ⌘Z y sin pasar a otro proyecto)
    assert 'T("brand.pv.cap.own")' in es and "!pillLinked()" in es and "C.highlightLinked(cap(), C.projectAccent(PLAN), C.pillOwn(PLAN))" in es
    assert "E.pillUnlinked" not in es
    # cuadro de muestra en WebKit (la app de escritorio): un <video> pausado recien buscado puede dar un
    # cuadro negro al lienzo (medido en un proyecto real): se despierta antes del seek y se reintenta
    go = es[es.index("  function grabOne(src, t) {"):]
    go = go[:go.index("  /** Asegura el cuadro")]
    assert "primeVideo(v); try { v.currentTime = Math.max(0, t);" in go and "blankCanvas(cv)" in go and "tries++ < 6" in go
    assert "v.currentTime = Math.max(0, t) + tries * 0.001;" in go and "v.requestVideoFrameCallback" in go


# ================================================================ color del video (fase 2)
def _look_img(W=270, H=480):
    """Imagen de prueba FICTICIA (campos suaves de color, un parche tono piel y dos manchas
    saturadas): sin bordes duros, asi la claridad del motor (que el cliente no replica) casi no pesa."""
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    img = np.stack([0.5 + 0.35 * np.sin(xx / W * 3.1 + 0.4) * np.cos(yy / H * 2.3), 0.45 + 0.3 * np.sin(yy / H * 4.0 + 1.0),
                    0.4 + 0.35 * np.cos(xx / W * 2.0 + yy / H * 1.5)], -1)

    def blob(cx, cy, rad, col):
        m = np.clip(1.4 - np.hypot(xx - cx, yy - cy) / rad, 0, 1)[..., None]
        return m * (np.array(col) - img)
    img = img + blob(W * 0.5, H * 0.31, W * 0.2, [0.88, 0.67, 0.56]) + blob(W * 0.22, H * 0.75, W * 0.15, [0.1, 0.6, 0.9]) \
        + blob(W * 0.78, H * 0.69, W * 0.15, [0.95, 0.2, 0.1])
    arr = np.clip(img * 255 + 0.5, 0, 255).astype(np.uint8)
    return np.dstack([arr, np.full((H, W), 255, np.uint8)])


def _apply_js(tmp_path, payload, arr, opts=None):
    """FXCore.applyLook (lo que hace lookgl.js en la GPU) sobre un RGBA -> RGBA."""
    import numpy as np
    H, W = arr.shape[:2]
    (tmp_path / "in.raw").write_bytes(np.ascontiguousarray(arr).tobytes())
    (tmp_path / "p.json").write_text(json.dumps(payload))
    _node_json(tmp_path, "ap.js", "const p=JSON.parse(fs.readFileSync(%s));const d=new Uint8ClampedArray(fs.readFileSync(%s));"
               "C.applyLook(p,d,%d,%d,%s);fs.writeFileSync(%s,Buffer.from(d.buffer));process.stdout.write('1');"
               % (json.dumps(str(tmp_path / "p.json")), json.dumps(str(tmp_path / "in.raw")), W, H, json.dumps(opts or {}),
                  json.dumps(str(tmp_path / "out.raw"))))
    return np.frombuffer((tmp_path / "out.raw").read_bytes(), np.uint8).reshape(H, W, 4)


@needs_node
def test_look_luts_igual_al_motor(tmp_path):
    """El color que el Studio dibuja en vivo (grade.lut_payload aplicado con la formula del cliente:
    tablas por canal -> saturacion o B/N -> vinieta) es el del export: grade_array con los mismos
    ajustes (vinieta del cfg, sin grano ni nitidez), error medio <= 4/255 a color y <= 7/255 en B/N,
    con y sin cara y con la correccion de una toma."""
    import numpy as np
    from motion import grade as G
    arr = _look_img()
    W = arr.shape[1]
    fine = {"exposure": -0.2, "temperature": 0.3, "saturation": 1.1, "contrast": 0.1, "vignette": 0.1}
    corr = {"gains": [0.97, 1.12, 1.08], "black": 0.02, "white": 0.98}
    face = [0.5, 0.31, 0.22, 0.14]
    for name in G.LOOKS:
        cfg = dict(fine, look=name, grain=0, sharpen=0)
        for cr, fc in ((None, None), (corr, face)):
            p = G.lut_payload(cfg, cr)
            assert p["look"] == name and len(p["lut"]["r"]) == 256 and p["mono"] == bool(G.LOOKS[name].get("mono"))
            assert p["base"]["contrast"] == round(G.LOOKS[name]["contrast"], 4)
            cli = _apply_js(tmp_path, p, arr, {"face": fc})[..., :3].astype(int)
            eng = G.grade_array(arr.copy(), cfg, G.build_lut(G.LOOKS[name], cr, cfg), G.ArrCtx(W, fc))[..., :3].astype(int)
            mae = float(np.abs(cli - eng).mean())
            assert mae <= (7.0 if p["mono"] else 4.0), (name, cr is not None, round(mae, 2))
    # apagado: el cliente no toca el cuadro
    off = _apply_js(tmp_path, G.lut_payload({"enabled": False, "look": "moody"}), arr)
    assert np.array_equal(off, arr)


@needs_node
def test_vineta_js_igual_a_python(tmp_path):
    """FXCore.vignetteAt = grade._vignette_gray (32 x 57, cantidades 0.08 / 0.16 / 0.3) y
    vignetteBwAt = la vinieta radial del B/N centrada en la cara (_bw_vig_big recortada)."""
    import numpy as np
    from motion import grade as G
    W, H = 32, 57
    got = _node_json(tmp_path, "vg.js", "const o={};for(const a of [0.08,0.16,0.3]){const g=[];for(let y=0;y<%d;y++)for(let x=0;x<%d;x++)g.push(C.vignetteAt(x/%d,y/%d,a));o[a]=g;}"
                     "const b=[];for(let y=0;y<%d;y++)for(let x=0;x<%d;x++)b.push(C.vignetteBwAt(x/%d,y/%d,0.6,0.45,0.42));o.bw=b;process.stdout.write(JSON.stringify(o));"
                     % (H, W, W, H, H, W, W, H))
    for a in (0.08, 0.16, 0.3):
        ref = G._vignette_gray(W, H, a, 0.5, 0.46).astype(float).ravel()
        js = np.array(got[str(a)]) * 255
        assert np.abs(js - ref).max() <= 1.0, (a, float(np.abs(js - ref).max()))
    # B/N: el mapa grande (calculado a 1/4 y ampliado) recortado alrededor de la cara (bw_array)
    W2, H2 = 128, 228
    big = G._bw_vig_big(W2, H2, 0.6).astype(float)
    ox, oy = int(round(W2 - 0.45 * W2)), int(round(H2 - 0.42 * H2))
    ref = big[oy:oy + H2, ox:ox + W2][::4, ::4][:H, :W].ravel()
    got2 = _node_json(tmp_path, "vb.js", "const b=[];for(let y=0;y<%d;y+=4)for(let x=0;x<%d;x+=4)b.push(C.vignetteBwAt(x/%d,y/%d,0.6,0.45,0.42));process.stdout.write(JSON.stringify(b));"
                      % (H2, W2, W2, H2))
    js2 = np.array(got2)[:len(ref)] * 255
    # (el motor calcula ese mapa a 1/4 de resolucion y lo amplia: unos niveles de diferencia)
    assert np.abs(js2 - ref).max() <= 8.0 and np.abs(js2 - ref).mean() <= 3.0, (float(np.abs(js2 - ref).max()), float(np.abs(js2 - ref).mean()))


@needs_node
def test_blanco_y_negro_js_igual_a_python(tmp_path):
    """La ganancia del B/N (cara o escena, como _bw_face_gain) y su tabla (grade.bw_table: curva +
    halacion) que arma el cliente son las del motor."""
    import numpy as np
    from motion import grade as G
    p = G.lut_payload({"look": "bw_contrast", "contrast": 0.1})
    bw = p["bw"]
    gains = [0.55, 0.74, 1.0, 1.09, 1.3, 1.65, 1.8]
    got = _node_json(tmp_path, "bt.js", "const bw=%s;process.stdout.write(JSON.stringify(%s.map(g=>Array.from(C.bwTable(bw,g)))));"
                     % (json.dumps(bw), json.dumps(gains)))
    for g, t in zip(gains, got):
        ref = G.bw_table(bw["contrast"], bw["lift"], g).astype(int)
        assert np.abs(np.array(t) - ref).max() <= 1, (g, int(np.abs(np.array(t) - ref).max()))
    assert got[2] == p["bw"]["table"] or np.abs(np.array(got[2]) - np.array(p["bw"]["table"])).max() <= 1
    rnd = np.random.default_rng(5)
    cases = []
    for k in range(12):
        H, W = 96, 54
        gray = np.clip(rnd.normal(60 + 12 * k, 30, (H, W)), 0, 255).astype(np.uint8)
        face = None if k % 2 else [0.5, 0.35, 0.4, 0.3]
        cases.append((gray, face))
    got = _node_json(tmp_path, "bg.js", "const bw=%s;const cs=%s;process.stdout.write(JSON.stringify(cs.map(c=>C.bwGain(Uint8Array.from(c.g),c.w,c.h,c.f,bw))));"
                     % (json.dumps(bw), json.dumps([{"g": g.ravel().tolist(), "w": g.shape[1], "h": g.shape[0], "f": f} for g, f in cases])))
    for (g, f), js in zip(cases, got):
        assert abs(js - G._bw_face_gain(g, f, bw["contrast"], bw["lift"])) <= 0.0100001, (f, js)


def _video_estilo(path, dur=12):
    """Video sintetico 540x960 de dos tomas (A: abajo gris claro hasta 6 s; B: gris oscuro), con audio."""
    import paths
    flt = ("[0:v]drawbox=x=0:y=480:w=540:h=480:color=0xd0d0d0:t=fill:enable='lt(t,6)',"
           "drawbox=x=0:y=480:w=540:h=480:color=0x303030:t=fill:enable='gte(t,6)'[v]")
    subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x5a6272:size=540x960:rate=30:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={dur}", "-filter_complex", flt,
                    "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "48k", "-shortest", "-movflags", "+faststart", str(path)], check=True, timeout=300)


def test_look_endpoints_sin_licencia_ni_estado_del_motor(tmp_path):
    """POST /fx/look_luts y /fx/look_thumbs en un servidor SIN licencia: tablas iguales a
    grade.lut_payload; los seis estilos con los mismos ajustes; la correccion de cada toma solo si el
    analisis del export ya la midio (antes: "pending"; nunca se mide aqui) y en tiempo ORIGINAL;
    miniaturas (seis + variantes) decodificables del tamano pedido, recorte y sin miniaturas; errores
    400; y nada de esto toca el estado del motor (_ENGINE, side.engine, analisis)."""
    data = tmp_path / "data"
    (data / "input").mkdir(parents=True)
    vid = data / "input" / "estilo_demo.mp4"
    _video_estilo(vid)
    plan = _plan_estilo()
    plan["video"] = str(vid)
    plan["fx"]["grade"] = {"enabled": True, "look": "warm_clean", "match_shots": True, "exposure": -0.2, "saturation": 1.1}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    code = r"""
import asyncio, base64, io, json, sys, time
sys.path[:0] = [%r, %r]
import numpy as np, cv2
import jobstore, server as S
from fastapi import HTTPException
from starlette.requests import Request
from motion import grade as G, render as R
jobstore.init()
plan = json.load(open(%r))
J = "look_demo"
jobstore.create(J, kind="analyze", ext=".mp4", plan=plan, phase="review", message="listo")
jobstore.update(J, state="review", pct=100)
out = {"licensed": S._lic_state()["licensed"]}
g = plan["fx"]["grade"]
body = lambda r: json.loads(r.body)
# tablas = lut_payload, sin tomas medidas todavia
d = body(S.fx_look_luts(J, {"grade": g, "shots": True, "plan": plan}))
ref = G.lut_payload(S._grade_cfg(g))
out["same"] = all(d[k] == ref[k] for k in ("look", "lut", "sat", "mono", "vig", "base"))
out["pending"] = d["shots_state"]
# el analisis del export mide las tomas (aqui se escribe su cache a mano): ahora si van por toma
eo = S._base_part(S._eff_engine(plan, "9:16", S._get_engine(J)))
pr = R.prepare(plan, {"fmt": "9:16", "workers": 0, **eo})
bv, _ = R._base_files(pr)
bv.parent.mkdir(parents=True, exist_ok=True)
bv.write_bytes(b"base")
job = pr["job"]
gr = G.Grader(bv, job["shots"], *R._grade_size(job), job["fps"], cache_dir=pr["opts"]["cache_dir"])
corr = [{"gains": [1.1, 1.0, 0.9], "black": 0.0, "white": 1.0, "face": False}, {"gains": [0.9, 1.0, 1.15], "black": 0.03, "white": 0.97, "face": False}]
gr._cache_file().write_text(json.dumps({"shots": gr.shots, "corr": corr}))
d = body(S.fx_look_luts(J, {"grade": g, "shots": True}))
out["ready"] = d["shots_state"]
out["ranges"] = [[s["start"], s["end"]] for s in d["per_shot"]]
out["tables_ok"] = all({k: s[k] for k in "rgb"} == G.lut_tables(S._grade_cfg(g), {"gains": c["gains"], "black": c["black"], "white": c["white"]})
                       for s, c in zip(d["per_shot"], corr))
out["differ"] = d["per_shot"][0]["r"] != d["per_shot"][1]["r"]
out["off"] = body(S.fx_look_luts(J, {"grade": dict(g, match_shots=False), "shots": True}))["shots_state"]
ts = []
for _ in range(6):
    t0 = time.perf_counter(); S.fx_look_luts(J, {"grade": g, "shots": True}); ts.append((time.perf_counter() - t0) * 1000)
out["luts_ms"] = min(ts)
sets = body(S.fx_look_luts(J, {"grade": g, "looks": ["natural", "bw_contrast", "nada"], "corr": corr[1]}))["looks"]
out["set"] = sorted(sets)
out["set_ok"] = sets["natural"]["lut"] == G.lut_payload(dict(S._grade_cfg(g), look="natural", enabled=True), {"gains": corr[1]["gains"], "black": 0.03, "white": 0.97})["lut"]
# miniaturas del motor
img = (np.random.default_rng(1).random((480, 270, 3)) * 255).astype(np.uint8)
jpg = cv2.imencode(".jpg", img)[1].tobytes()
async def thumbs(data, **q):
    sent = []
    async def receive():
        if sent: return {"type": "http.disconnect"}
        sent.append(1); return {"type": "http.request", "body": data, "more_body": False}
    req = Request({"type": "http", "method": "POST", "path": f"/fx/look_thumbs/{J}", "headers": [(b"content-type", b"image/jpeg")], "query_string": b""}, receive)
    try:
        return json.loads((await S.fx_look_thumbs(J, req, **q)).body)
    except HTTPException as e:
        return {"error": e.status_code}
dec = lambda u: cv2.imdecode(np.frombuffer(base64.b64decode(u.split(",", 1)[1]), np.uint8), cv2.IMREAD_COLOR).shape[:2]
r = asyncio.run(thumbs(jpg, w=270, h=480, grade=json.dumps(g), variants="exposure:-0.5,exposure:0.5,grain:0.12,sharpen:0.4", face="0.5,0.3,0.2,0.12"))
out["thumbs"] = sorted(r["thumbs"]); out["variants"] = sorted(r["variants"])
out["sizes"] = sorted({dec(u) for u in list(r["thumbs"].values()) + list(r["variants"].values())})
out["looks_differ"] = len({r["thumbs"][k] for k in r["thumbs"]}) == 6
jpg2 = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
t0 = time.perf_counter(); asyncio.run(thumbs(jpg2, w=270, h=480, grade=json.dumps(g))); out["thumbs_ms"] = (time.perf_counter() - t0) * 1000
t0 = time.perf_counter(); r2 = asyncio.run(thumbs(jpg2, w=270, h=480, grade=json.dumps(g))); out["cached_ms"] = (time.perf_counter() - t0) * 1000
out["cached"] = r2.get("cached")
r3 = asyncio.run(thumbs(jpg, w=540, h=960, grade=json.dumps(g), variants="grain:0,grain:0.06", crop="0.4,0.3,0.45,0.33", looks=0))
out["crop"] = [sorted(r3["thumbs"]), sorted({dec(u) for u in r3["variants"].values()})]
out["corr_changes"] = asyncio.run(thumbs(jpg, w=270, h=480, grade=json.dumps(g), corr=json.dumps(corr[1])))["thumbs"]["natural"] != r["thumbs"]["natural"]
out["errors"] = [asyncio.run(thumbs(jpg, w=270, h=480, variants="foo:1")).get("error"), asyncio.run(thumbs(b"no es jpeg", w=270, h=480)).get("error"),
                 asyncio.run(thumbs(jpg, w=4000, h=480)).get("error"), asyncio.run(thumbs(b"", w=270, h=480)).get("error"),
                 asyncio.run(thumbs(jpg, corr='{"gains": [1]}')).get("error"),
                 asyncio.run(thumbs(cv2.imencode(".jpg", np.zeros((60, 2000, 3), np.uint8))[1].tobytes())).get("error")]
try:
    S.fx_look_luts("no_existe", {"grade": g}); out["404"] = None
except HTTPException as e:
    out["404"] = e.status_code
out["engine"] = [J in S._ENGINE, jobstore.side_get(J, "engine"), [k for k in S._SIDE if k[0] == J]]
print("RESULT" + json.dumps(out))
""" % (str(SPIKE / "app"), str(SPIKE / "scripts"), str(tmp_path / "plan.json"))
    env = {k: v for k, v in os.environ.items() if k != "REELFY_GATED"}
    env.update(REELFY_DATA=str(data), REELFY_BG_ANALYSIS="0", REELFY_OLLAMA_URL="http://127.0.0.1:9")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600, env=env, cwd=str(SPIKE))
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT")), None)
    assert line, r.stdout[-3000:] + r.stderr[-3000:]
    out = json.loads(line[6:])
    assert out["licensed"] is False, "la prueba corre sin licencia"
    assert out["same"] and out["pending"] == "pending" and out["ready"] == "ready" and out["off"] == "off"
    assert out["ranges"] == [[0.0, 6.0], [6.0, 12.0]], out["ranges"]
    assert out["tables_ok"] and out["differ"]
    assert out["luts_ms"] < 20, out["luts_ms"]
    assert out["set"] == ["bw_contrast", "natural"] and out["set_ok"]
    assert out["thumbs"] == sorted(["natural", "warm_clean", "cinematic", "bright_pop", "moody", "bw_contrast"]) and out["looks_differ"]
    assert out["variants"] == sorted(["exposure:-0.5", "exposure:0.5", "grain:0.12", "sharpen:0.4"])
    assert out["sizes"] == [[480, 270]], out["sizes"]
    assert out["crop"][0] == [] and len(out["crop"][1]) == 1 and out["crop"][1][0][0] in (29, 30) and out["crop"][1][0][1] == 27, out["crop"]
    assert out["corr_changes"] and out["cached"] is True and out["cached_ms"] < 50
    assert out["errors"] == [400, 400, 400, 400, 400, 400] and out["404"] == 404, out["errors"]
    assert out["engine"] == [False, None, []], out["engine"]
    if not os.environ.get("CI"):
        assert out["thumbs_ms"] < 200, out["thumbs_ms"]


# ================================================================ fase 3: Edición con IA, Música, Quién sale, Formato
@needs_node
def test_tarjetas_de_edicion_igual_al_director(tmp_path):
    """Cada tarjeta de «Estilo de edición» (FXCore.DIRECTOR_CARDS) dibuja lo que pone el director con
    ese estilo: sus subtitulos, su primer estilo de titulo con la letra de su kit y su color."""
    from motion import director as D
    out = _node_json(tmp_path, "cards.js", "process.stdout.write(JSON.stringify({c: C.DIRECTOR_CARDS, o: C.DIRECTOR_ORDER}));")
    assert out["o"] == list(D.STYLES) and sorted(out["c"]) == sorted(D.STYLES)
    for k, st in D.STYLES.items():
        c = out["c"][k]
        for f in ("preset", "y", "size", "font", "case", "max_words"):
            assert c["captions"][f] == st["captions"][f], (k, f)
        title = st["presets"][0][0]
        assert c["title"] == title, k
        assert c["titleFont"] == D.TYPE_KIT[k][D.PRESET_ROLE[title]], k
        assert c["look"] == st["look"]["look"], k


def test_mood_sample(tmp_path):
    """GET /fx/mood_sample/{animo}: 6 s del generador rapido para cada ambiente (y «auto»), en AAC,
    guardados en DATA/cache/mood (la segunda vez < 100 ms), a -16 LUFS con su final desvanecido;
    animo desconocido -> 404, estilo desconocido -> dinamico. Sin licencia y sin tocar proyectos."""
    data = tmp_path / "data"
    code = r"""
import json, sys, time, subprocess
sys.path[:0] = [%r, %r]
import numpy as np, soundfile as sf
import server as S, paths
from fastapi import HTTPException
from motion import music as M
out = {"licensed": S._lic_state()["licensed"], "files": {}, "durs": {}, "ms": {}}
for m in ["auto", "upbeat", "chill", "inspiring", "corporate", "playful", "dramatic"]:
    t0 = time.time(); r = S.fx_mood_sample(m, "minimal"); out["ms"][m] = (time.time() - t0) * 1000
    f = str(r.path); out["files"][m] = [f.split("/")[-1], r.media_type]
    p = subprocess.run([str(paths.FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], capture_output=True, text=True)
    out["durs"][m] = float(p.stdout.strip())
t0 = time.time(); S.fx_mood_sample("upbeat", "minimal"); out["warm"] = (time.time() - t0) * 1000
out["fallback"] = str(S.fx_mood_sample("chill", "no_existe").path).split("/")[-1]
try:
    S.fx_mood_sample("reggaeton", "minimal"); out["404"] = None
except HTTPException as e:
    out["404"] = e.status_code
out["cache"] = sorted(p.name for p in (paths.DATA / "cache" / "mood").iterdir())
y = M.mood_sample("minimal", "upbeat")
out["len"] = [int(y.shape[0]), int(y.shape[1]), M.SR]
out["lufs"] = float(M.loudness_lufs(y, M.SR))
out["tail"] = float(np.abs(y[-200:]).max())
out["jobs"] = sorted(p.name for p in paths.INPUT.iterdir()) if paths.INPUT.exists() else []
print("RESULT" + json.dumps(out))
""" % (str(SPIKE / "app"), str(SPIKE / "scripts"))
    env = {k: v for k, v in os.environ.items() if k != "REELFY_GATED"}
    env.update(REELFY_DATA=str(data), REELFY_BG_ANALYSIS="0", REELFY_OLLAMA_URL="http://127.0.0.1:9")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600, env=env, cwd=str(SPIKE))
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT")), None)
    assert line, r.stdout[-3000:] + r.stderr[-3000:]
    out = json.loads(line[6:])
    assert out["licensed"] is False, "la prueba corre sin licencia"
    moods = ["auto", "upbeat", "chill", "inspiring", "corporate", "playful", "dramatic"]
    assert all(out["files"][m] == [f"minimal_{m}.m4a", "audio/mp4"] for m in moods), out["files"]
    assert all(5.5 <= out["durs"][m] <= 6.5 for m in moods), out["durs"]
    assert out["warm"] < 100, out["warm"]
    assert out["fallback"] == "dinamico_chill.m4a"
    assert out["404"] == 404
    assert out["cache"] == sorted([f"minimal_{m}.m4a" for m in moods] + ["dinamico_chill.m4a"]), out["cache"]
    assert out["len"] == [6 * 48000, 2, 48000] and abs(out["lufs"] + 16) < 1.0 and out["tail"] < 0.01, out
    assert out["jobs"] == []


def test_plantillas_de_la_copy_sin_acentos_en_sus_variables():
    """accent_es no toca las {variables} de una plantilla: el ejemplo de «Cantidad de efectos» que
    publica /fx/schema se llena en el Studio con {titulos}, {graficos} y {efectos} (antes llegaba
    {títulos} y el Studio mostraba el ejemplo fijo)."""
    from motion import schema as S
    raw = _ui()
    for sec in ("params", "ui"):
        for i, e in raw[sec].items():
            if e.get("ex_tpl"):
                assert S.UI_COPY[sec][i]["ex_tpl"] == e["ex_tpl"], i
    p = next(x for x in S.schema()["director"]["params"] if x["k"] == "intensity")
    assert "{titulos}" in p["ex_tpl"] and "{graficos}" in p["ex_tpl"]
    assert S.accent_es("lleva {titulos} titulos") == "lleva {titulos} títulos"


def test_proyecto_retirado_y_controles_movidos_a_estilo():
    """Formato, idioma, margen desenfocado, audio de la voz, logo y plantillas viven en Estilo con
    sus mismos ids (los manejadores de index.html siguen igual); el panel Proyecto queda con el
    resumen del estilo, Shorts y la transcripcion (el modo clasico conserva sus grupos v1)."""
    html = (STATIC / "index.html").read_text()
    pane = html[html.index('id="stylePane"'):html.index('id="cleanPane"')]
    proj = html[html.index('id="tabProj"'):html.index('id="tabElem"')]
    moved = ("segFmt", "segLang", "langNote", "air", "airLbl", "airNote", "logoOn", "logoUp", "segLogo", "logoSize", "enhance",
             "presetSel", "presetSave", "presetDel", "brandLogoRow", "airRow", "enhanceRow", "proNoteBrand")
    for i in moved:
        assert html.count(f'id="{i}"') == 1 and f'id="{i}"' in pane and f'id="{i}"' not in proj, i
    for sid, setting in (("fmtRow", "project.format"), ("langRow", "project.lang"), ("airRow", "project.air"), ("enhanceRow", "proc.enhance"),
                         ("brandLogoRow", "brand.logo"), ("presetRow", "brand.preset")):
        row = re.search(r'<div class="hrow[^"]*" data-setting="([^"]+)" data-static="(st\w+)" id="%s">' % sid, pane)
        assert row and row.group(1) == setting and row.group(2) in ("stFormat", "stWho"), sid
    # Proyecto: el resumen, Shorts y la transcripcion; lo del modo clasico (sin edicion IA) va en v1only
    assert 'id="styleSummary"' in proj and 'id="hls"' in proj and 'id="subs"' in proj and 'id="projLegacyRows"' in proj
    assert 'id="profGroup"' not in html and 'id="musBody"' not in html and 'id="dirStats"' not in html
    assert '<div class="pgroup v1only" id="dirGroup">' in proj and '<div class="pgroup v1only" id="projLegacy">' in proj
    for g in re.findall(r'<div class="(pgroup[^"]*)"', proj):
        assert g in ("pgroup v2only", "pgroup v1only", "pgroup") , g
    assert re.findall(r'<div class="pgroup">', proj) and len(re.findall(r'<div class="pgroup">', proj)) == 2, "solo Shorts y Transcripción para todos"
    # las opciones movidas son radios de ancho igual (Estilo) y siguen marcando .sel para el script de siempre
    assert 'const segMark=(b,on)=>' in html and 'aria-checked' in html
    for q in ("segFmt", "segLang", "segLogo"):
        assert re.search(r'class="seg seg-h seg-st[^"]*" id="%s" role="radiogroup"' % q, pane), q
    # nada en ingles ni jerga en lo que se lee de lo movido (antes: «Preset», «Eliminar preset», «Aire», «ES»)
    deny = [re.compile(r"(?<![\wÁÉÍÓÚáéíóúñÑ])" + re.escape(w) + r"(?![\wÁÉÍÓÚáéíóúñÑ])", re.I) for w in _ui()["deny"]]
    park = re.sub(r"<svg.*?</svg>", "", pane[pane.index('id="stPark"'):], flags=re.S)
    shown = re.findall(r">([^<>]+)<", park) + re.findall(r'(?<![\w-])(?:title|aria-label|placeholder)="([^"]+)"', park)
    shown = [t.strip() for t in shown if t.strip()]
    assert "Ninguna plantilla" in shown and "Vertical 9:16" in shown and "Español" in shown
    assert not [t for t in shown for rx in deny if rx.search(t)], [t for t in shown for rx in deny if rx.search(t)]
    es = (STATIC / "estilo_proyecto.js").read_text()
    for sid in ("stDirector", "stMusic", "stWho", "stFormat"):
        assert f'id: "{sid}"' in es, sid
    assert "E.addSection = function" in (STATIC / "estilo.js").read_text() and "E.home = function" in (STATIC / "estilo.js").read_text()
    fx = (STATIC / "fx.js").read_text()
    assert "FX.applyDirector = async function" in fx and "Estilo.home(isPro())" in fx and "Estilo.directorChanged" in fx
    # Elemento › Música: «Un solo lugar para la música» (el enlace a Estilo) y, en Avanzado, solo lo del
    # momento del video (golpe principal, pausas y subidas)
    mus = fx[fx.index("  function inspectMusic(b) {"):]
    mus = mus[:mus.index("\n  }\n")]
    assert '"music.single_home"' in mus and 'P("drop_at")' in mus and '["stops", "swells"]' in mus and 'Estilo.open("stMusic")' in mus, "Elemento › Música: solo lo del momento"
    assert not re.search(r'P\("(gain_db|mood|source|bpm|prompt)"\)', mus)


# ================================================================ E2E en navegador real
def _plan_estilo():
    """Plan v2 sintetico (12 s, dos tomas: A con la franja de los subtitulos clara, B oscura) con
    titulo con palabras destacadas, rotulo y boton; marca verde ficticia."""
    from motion import schema
    texto = ("hoy te muestro como cuidar tus dientes en casa con tres pasos muy simples que cualquiera puede "
             "hacer cada mañana antes de salir de casa y sin gastar mucho dinero")
    words, t = [], 0.4
    # cada palabra con mayuscula inicial: «Letras» (MAYÚSCULAS / minúsculas) siempre cambia la vista
    for tok in (w.capitalize() for w in texto.split()):
        d = 0.2 + 0.025 * len(tok)
        words.append({"text": tok, "start": round(t, 3), "end": round(t + d, 3)})
        t += d + 0.09
        if t > 11.2:
            break
    phrases = [words[i:i + 4] for i in range(0, len(words), 4)]
    light = [[90, 90, 90]] * 8 + [[208, 208, 208]] * 8
    dark = [[200, 200, 200]] * 8 + [[48, 48, 48]] * 8
    plan = schema.normalize({"version": 2, "duration": 12.0, "dims": [540, 960], "phrases": phrases, "camera": [], "beats": [],
                             "broll": [], "cuts": [], "highlights": [],
                             "shots": [{"start": 0, "end": 6.0, "src": "demo.mp4", "rows": light},
                                       {"start": 6.0, "end": 12.0, "src": "demo.mp4", "rows": dark}], "fx": {}})
    plan["fx"]["captions"] = {"enabled": True, "preset": "clean_bold", "y": 0.72, "size": 0.052, "font": "Poppins-ExtraBold",
                              "color": "#FFFFFF", "accent": "#1F9D55", "case": "none", "max_words": 4, "hide_during_titles": True}
    plan["fx"]["accent"] = "#1F9D55"
    plan["fx"]["grade"] = {"enabled": True, "look": "natural", "match_shots": False, "vignette": 0.0, "grain": 0.0, "sharpen": 0.0}
    plan["fx"]["music"] = {"enabled": False, "source": "none"}
    plan["fx"]["text"] = [{"id": "tx_demo", "preset": "title_pop", "text": "Tres pasos\nmuy simples", "accent_words": ["simples"],
                           "accent": "#1F9D55", "start": 0.4, "end": 2.2, "y": 0.22, "size": 0.09, "enabled": True, "src": "ai"}]
    plan["fx"]["graphics"] = [
        {"id": "gx_lt", "kind": "lower_third", "title": "Dra. Demo", "subtitle": "Odontóloga", "color": "#1F9D55", "start": 2.6,
         "end": 5.0, "y": 0.6, "size": 0.6, "x": 0.5, "align": "center", "enabled": True, "src": "ai"},
        {"id": "gx_cta", "kind": "cta", "variant": "follow", "label": "Sígueme", "color": "#1F9D55", "start": 9.0, "end": 11.5,
         "y": 0.55, "x": 0.5, "size": 0.5, "enabled": True, "src": "ai"}]
    plan["entities"] = {"name": "Dra. Demo", "role": "Odontóloga"}
    plan["topic"] = "Cuidado dental en casa"
    plan["video"] = "estilo_demo.mp4"
    plan["studio"] = {"fmt": "9:16", "lang": "es"}
    plan["director"] = dict(plan.get("director") or {}, style="minimal")
    return schema.normalize(plan)


def _plan_inspector():
    """El plan sintetico de Estilo con un elemento de cada pista que revisa el E2E del inspector: un
    golpe de zoom (en la toma A), un fogonazo entre las dos tomas y un sonido (pop)."""
    p = _plan_estilo()
    # el titulo dice lo que se escucha en ese tramo (sus palabras entran con la voz, como en el export)
    p["fx"]["text"][0].update({"text": "Hoy te muestro\ncómo cuidar", "accent_words": ["cuidar"], "start": 0.4, "end": 2.9})
    p["fx"]["effects"] = [{"id": "fx_punch", "kind": "punch", "start": 3.0, "end": 3.9, "amount": 0.14, "intensity": 1.0, "enabled": True, "src": "ai"}]
    p["fx"]["transitions"] = [{"id": "tr_flash", "kind": "flash", "at": 6.0, "dur": 0.3, "enabled": True, "src": "ai"}]
    p["fx"]["sfx"] = [{"id": "sf_pop", "name": "pop", "at": 1.2, "gain_db": -6, "enabled": True, "src": "ai"}]
    # una cara en la toma B (la del boton «Sígueme»): «Tu cara del video» se puede elegir y se dibuja
    p["shots"][1]["face"] = [0.5, 0.33, 0.2, 0.12]
    # pausa dramatica y subida de la musica: sus campos (Avanzado) tambien pasan el lint de claridad
    p["fx"]["music"].update({"stops": [{"at": 7.0, "dur": 0.8, "mode": "tape"}], "swells": [{"start": 8.0, "end": 10.0, "db": 3.0}]})
    from motion import schema
    return schema.normalize(p)


@needs_node
def test_boton_de_seguir_igual_al_motor(tmp_path):
    """El color del boton «Seguir» que muestran la vista en vivo y la fila «Color del botón»
    (FXCore.ctaButtonColor) es el que pinta el motor (graphics._cta_follow): button_color, si no
    `color`, si no rojo, y un casi blanco se vuelve rojo. Medido en pixeles del motor."""
    import numpy as np
    from motion import ctx as MC
    from motion import graphics as G
    cases = [{"button_color": "#2EC4B6", "color": "#7C5CFF"}, {"color": "#7C5CFF"}, {},
             {"button_color": "#FFFFFF", "color": "#2EC4B6"}, {"color": "#F4F4F4"}]
    js = _node_json(tmp_path, "btn.js", "process.stdout.write(JSON.stringify(%s.map(c => C.ctaButtonColor(c))))" % json.dumps(cases))
    hexrgb = lambda h: np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)])
    for case, want in zip(cases, js):
        arr, surf = MC.new_frame(540, 960)
        surf.getCanvas().clear(MC.color("#6E7B64"))
        ctx = MC.demo_ctx(W=540, H=960, t=1.5, quality="draft", entities={"name": "Ana Demo", "handle": "@ana.demo"})
        it = dict(case, id="f0", kind="cta", variant="follow", title="Ana Demo", handle="@ana.demo", x=0.5, y=0.5,
                  anim_in="none", anim_out="none", press_at=99)
        G.draw_cta(surf.getCanvas(), it, 1.5, 3.0, ctx)
        rgb = arr[..., :3].astype(int)
        n = int((np.abs(rgb - hexrgb(want)).max(axis=2) <= 10).sum())
        assert n >= 300, (case, want, n)
        other = case.get("color")
        if other and other.upper() != want.upper() and MC.luminance(other) < 0.85:
            m = int((np.abs(rgb - hexrgb(other)).max(axis=2) <= 10).sum())
            assert m < 50, (case, other, m)


def test_plan_sintetico_de_estilo_es_valido():
    from motion import schema
    p = _plan_estilo()
    assert schema.validate(p) == [], schema.validate(p)
    n = sum(len(x) for x in p["phrases"])
    assert n >= 20 and p["fx"]["captions"]["preset"] == "clean_bold" and p["fx"]["accent"] == "#1F9D55"
    assert not _datos_de_cliente(json.dumps(p)), "el plan sintetico no lleva datos de un cliente"
    q = _plan_inspector()
    assert schema.validate(q) == [], schema.validate(q)
    assert [x["kind"] for x in q["fx"]["effects"]] == ["punch"] and q["fx"]["transitions"][0]["kind"] == "flash" and q["fx"]["sfx"][0]["name"] == "pop"


def test_estilo_e2e_en_navegador(tmp_path):
    """E2E de Estilo en Chrome real (tests/js/estilo_e2e.mjs) contra un servidor AISLADO
    (tests/devserver.sh) con el plan sintetico: el caso de Kevin (la pastilla en un clic, con su color
    ahi mismo y el ejemplo en vivo), lint de claridad (B), sin controles muertos (C), estilos de color
    (D), escuchar (E: ambientes, la musica creada, la voz, los sonidos de «Agregar»), simetria medida
    en las siete secciones (F) y la fase 3 (Edición con IA, Música, Quién sale y tu logo, Formato),
    a 1440x900 y 1200x760. Se activa con REELFY_PLAYWRIGHT=ruta a playwright-core;
    REELFY_E2E_SHOTS guarda las capturas."""
    import signal
    import socket
    import time
    import urllib.request
    pw = os.environ.get("REELFY_PLAYWRIGHT")
    if not (pw and NODE):
        pytest.skip("E2E de Estilo desactivado (define REELFY_PLAYWRIGHT)")
    import paths
    data = tmp_path / "data"
    (data / "input").mkdir(parents=True)
    plan = _plan_estilo()
    vid = data / "input" / "estilo_demo.mp4"
    _video_estilo(vid)
    plan["video"] = str(vid)
    code = ("import json,sys; sys.path[:0]=[%r,%r]; import jobstore; jobstore.init(); d=json.load(sys.stdin)\n"
            "for jid,p in d.items():\n"
            "    jobstore.create(jid, kind='analyze', ext='.mp4', plan=p, phase='review', message='Análisis listo')\n"
            "    jobstore.update(jid, state='review', pct=100)\n") % (str(SPIKE / "app"), str(SPIKE / "scripts"))
    subprocess.run([sys.executable, "-c", code], input=json.dumps({"estilo_demo": plan}), text=True, check=True,
                   env={**os.environ, "REELFY_DATA": str(data)}, timeout=120)
    port = None
    for p in range(8900, 9000):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                port = p
                break
    assert port, "sin puerto libre en 8900-8999"
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
        r = subprocess.run([NODE, str(SPIKE / "tests" / "js" / "estilo_e2e.mjs"), f"http://127.0.0.1:{port}", "estilo_demo", out],
                           capture_output=True, text=True, timeout=1500, env={**os.environ})
        assert r.returncode == 0, r.stdout[-8000:] + r.stderr[-3000:]
    finally:
        try:
            os.killpg(srv.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        srv.wait(timeout=30)


# ================================================================ fase 4: el inspector y las demas pantallas
@needs_node
def test_inspector_cubre_la_copy(tmp_path):
    """Cada parametro que el inspector puede dibujar (cada pista y tipo del esquema, subtitulos y musica)
    encuentra su fila en la copy con FXCore.paramCopyId (nunca una fila «sin copy») y el nivel que
    resuelve FXCore.copyFor es el del esquema (Basico / Avanzado / oculto)."""
    from motion import schema as S
    sch = S.schema()
    (tmp_path / "s.json").write_text(json.dumps(sch))
    out = _node_json(tmp_path, "insp.js", """
const S = JSON.parse(fs.readFileSync(%r, "utf8")), ui = S.ui, bad = [], lv = [];
for (const [tk, T] of Object.entries(S.tracks)) for (const [kind, spec] of Object.entries(T.items))
  for (const p of spec.params) {
    const id = C.paramCopyId(ui, { k: p.k, track: tk, kind }), cp = C.copyFor(ui, id, { kind, track: tk, preset: kind });
    if (cp.missing) bad.push(`${tk}.${kind}.${p.k}`); else if (cp.level !== p.level) lv.push(`${tk}.${kind}.${p.k}: ${cp.level} != ${p.level}`);
  }
for (const g of ["captions", "music"]) for (const p of S[g].params) { const cp = C.copyFor(ui, C.paramCopyId(ui, { k: p.k, group: g }), {}); if (cp.missing) bad.push(`${g}.${p.k}`); }
for (const grp of Object.values(S.common)) for (const p of grp) { const cp = C.copyFor(ui, C.paramCopyId(ui, { k: p.k, track: "text", kind: "title_pop" }), {}); if (cp.missing) bad.push(`common.${p.k}`); }
process.stdout.write(JSON.stringify({ bad, lv }));
""" % str(tmp_path / "s.json"))
    assert out["bad"] == [], out["bad"][:20]
    assert out["lv"] == [], out["lv"][:20]


def test_inspector_cableado():
    """El inspector usa la fila de claridad (Estilo.HelpRow angosta) con su nivel; las galerias con su
    ejemplo (estilo_catalog.js); tiempos en m:ss del video final con «Aquí»; Inicio, Montar clips y
    Limpieza con data-setting; y nada en ingles ni los nombres viejos."""
    fx = (STATIC / "fx.js").read_text()
    cat = (STATIC / "estilo_catalog.js").read_text()
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "fx.css").read_text()
    # filas: HelpRow con la copy (paramCopyId), filtradas por nivel y por los campos que usa cada tipo
    rp = fx[fx.index("  function renderParams(box, params, obj, o) {"):]
    rp = rp[:rp.index("FX.renderParams = renderParams;")]
    assert "C.copyFor(SCHEMA.ui, copyIdOf(p, opt)" in rp and 'cp.level === "hidden"' in rp and "C.paramHidden(" in rp
    assert "ES().HelpRow.create({" in fx and 'layout: "narrow"' in fx and "function levelBar(b)" in fx and "E.LevelSwitch" not in fx
    pc = fx[fx.index("  function paramControl(p, obj, opt) {"):]
    pc = pc[:pc.index("  /** Medio propio")]
    for t in ("bool", "num", "text", "color", "select", "seg", "font", "icon", "lines", "rows", "media"):
        assert f't === "{t}"' in pc, t
    assert "e.TriState.create(" in pc and "e.Segmented.create(" in pc and "e.Catalog.timeField(" in pc and "e.Catalog.colorPick(" in pc
    assert "e.Catalog.optionGallery(" in pc and "C.relChoices(" in pc and "advRange(" in pc
    # la fuerza va en Animacion; tipo y acciones arriba; Delante / Detras solo titulos y graficos
    it = fx[fx.index("  function inspectItem(b, track, it) {"):]
    it = it[:it.index("  /** Sin la copy")]
    assert 'insSection(b, "anim"), common.filter(p => p.k === "intensity")' in it and "kindRow(track, it)" in it and "actionsRow(it)" in it
    # campos dentro de una lista (pausas, subidas, tramos): ayuda de la copy (nunca «tape = …»), seleccion
    # neutra, y la foto «Tu cara del video» con el mismo formato que una foto propia ({src, w, h})
    assert "const tipOf = p =>" in fx and "frow(label, ctl, xo.full ? \"wide\" : false, tipOf(p))" in fx and "p.hint);" not in fx
    assert 's.className = hasCopy() ? "seg sm seg-st" : "seg sm";' in fx
    assert "faceIm = { src: o, w: n, h: n };" in fx
    # la unidad junto a un numero y la (i) de ayuda tambien con letra >= 11 px
    for src, sel in ((css, ".funit{"), (html, ".info{"), (css, ".stagebar .info{")):
        rule = src[src.index(sel):]
        rule = rule[:rule.index("}")]
        fs = float(re.search(r"font-size:\s*([\d.]+)px", rule).group(1))
        assert fs >= 11, (sel, fs)
    # nada en ingles ni los nombres viejos en lo nuevo del inspector
    for bad in ("Inicio al playhead", "Fin al playhead", "Mover al playhead", "Agregar en el playhead", 'label: "SFX"', 'frow(track === "text" ? "Preset"', "Regenerar con IA`;"):
        assert bad not in fx, bad
    assert 'sfx: { label: "Sonidos"' in fx
    # catalogos con su ejemplo en bucle y el menu Agregar en rejilla
    for name in ("const ExampleLoop = E.ExampleLoop", "function kindGallery(", "function optionGallery(", "function drawEffect(", "function drawTransition(",
                 "function sfxPicker(", "function addMenuHtml(", "function timeField(", "function colorPick(", "function behindTile("):
        assert name in cat, name
    assert "e.Catalog.addMenuHtml(" in fx and '"amenu"' in fx
    # pantallas: Inicio (filas fijas), Montar clips (asmRow) y Limpieza (renderSum)
    for sid in ("setup.files", "setup.guion", "setup.glossary", "setup.highlights", "setup.profile"):
        assert f'data-setting="{sid}"' in html, sid
    assert 'id="hlSeg"' in html and 'type="hidden" id="highlights"' in html and 'type="number" id="highlights"' not in html
    for sid in ("asm.takes", "asm.image.dur", "asm.clip.mute", "asm.clip.volume", "asm.clip.fades", "asm.clip.trans", "asm.clip.ramp", "asm.overlay"):
        assert f'"{sid}"' in html, sid
    for sid in ("clean.level", "clean.cats", "clean.cat.", "clean.sil", "clean.restore"):
        assert f'data-setting="{sid}' in fx, sid
    assert "Estilo.Catalog.hydrateScreen($(\"#s-setup\"))" in html and "FX.loadCopy()" in html
    assert "#i-magnet" in html.split('id="asmSnapBtn"')[1][:200], "el imán del montaje con su ícono"
    assert "FX.clean.through!==ci" in html, "el ejemplo de un corte se oye aunque esté quitado"
    # galerias de ancho igual con el ultimo renglon centrado
    assert ".kgal{" in css and "justify-content:center" in css.split(".kgal{")[1][:200]


def test_ningun_short_no_pregunta_a_la_ia(monkeypatch):
    """«¿Cuántos shorts sugerir?» = Ninguno: el pipeline no le pregunta a la IA ni inventa shorts."""
    import highlights as HL

    def boom(*a, **k):
        raise AssertionError("no debe llamar a la IA")
    monkeypatch.setattr(HL, "_ollama_chat", boom)
    sents = [{"i": 0, "start": 0.0, "end": 3.0, "text": "Hola a todos"}, {"i": 1, "start": 3.0, "end": 9.0, "text": "Hoy les cuento algo"}]
    assert HL.select_highlights(sents, n=0) == []
    assert HL.select_highlights(sents, n=None) == []


def test_inspector_e2e_en_navegador(tmp_path):
    """E2E del inspector y de las demas pantallas en Chrome real (tests/js/inspector_e2e.mjs) contra un
    servidor AISLADO con datos sinteticos (insp_demo: titulo, rotulo, boton, golpe de zoom, fogonazo y
    sonido; clean_demo: limpieza de tomas): claridad (B) en Basico y Avanzado del inspector de cada pista,
    Inicio, Montar clips y Limpieza; galerias con su ejemplo y simetricas; ningun control muerto (C);
    escuchar (E), a 1440x900 y 1200x760. Se activa con REELFY_PLAYWRIGHT; REELFY_E2E_SHOTS guarda las capturas."""
    import signal
    import socket
    import time
    import urllib.request
    pw = os.environ.get("REELFY_PLAYWRIGHT")
    if not (pw and NODE):
        pytest.skip("E2E del inspector desactivado (define REELFY_PLAYWRIGHT)")
    import paths
    sys.path.insert(0, str(SPIKE / "tests"))
    import test_studio_ui as TU
    data = tmp_path / "data"
    (data / "input").mkdir(parents=True)
    plan = _plan_inspector()
    vid = data / "input" / "insp_demo.mp4"
    _video_estilo(vid)
    plan["video"] = str(vid)
    cplan, _old, cdur = TU._plan_limpieza(n_words=320)
    cvid = data / "input" / "clean_demo.mp4"
    subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={cdur}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={cdur}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "36", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "48k", "-shortest",
                    "-movflags", "+faststart", str(cvid)], check=True, timeout=600)
    cplan["video"] = str(cvid)
    code = ("import json,sys; sys.path[:0]=[%r,%r]; import jobstore; jobstore.init(); d=json.load(sys.stdin)\n"
            "for jid,p in d.items():\n"
            "    jobstore.create(jid, kind='analyze', ext='.mp4', plan=p, phase='review', message='Análisis listo')\n"
            "    jobstore.update(jid, state='review', pct=100)\n") % (str(SPIKE / "app"), str(SPIKE / "scripts"))
    subprocess.run([sys.executable, "-c", code], input=json.dumps({"insp_demo": plan, "clean_demo": cplan}), text=True, check=True,
                   env={**os.environ, "REELFY_DATA": str(data)}, timeout=120)
    port = None
    for p in range(8900, 9000):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                port = p
                break
    assert port, "sin puerto libre en 8900-8999"
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
        r = subprocess.run([NODE, str(SPIKE / "tests" / "js" / "inspector_e2e.mjs"), f"http://127.0.0.1:{port}", out],
                           capture_output=True, text=True, timeout=1500, env={**os.environ})
        assert r.returncode == 0, r.stdout[-8000:] + r.stderr[-3000:]
    finally:
        try:
            os.killpg(srv.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        srv.wait(timeout=30)


# ================================================================ revision: lo que la UI promete = lo que hace el motor
def _style_ctx(style):
    """Contexto minimo para text._cap_cfg: la paleta del estilo de edicion."""
    from types import SimpleNamespace
    from motion import assets
    return SimpleNamespace(palette=assets.palette(style))


@needs_node
def test_letra_del_estilo_igual_al_motor(tmp_path):
    """«La del estilo» (captions.font vacio) se dibuja en vivo con la letra que usa el export:
    FXCore.captionCfg con las letras de FXCore.captionFonts(plan) = text._cap_cfg con la paleta de
    cada estilo de edicion, para cada estilo de subtitulos y letra (vacia, la de siempre o una propia)."""
    from motion import assets
    from motion import text as T
    out = _node_json(tmp_path, "fonts.js", "const o={};for(const st of ['dinamico','elegante','minimal','energetico']){o[st]={fonts:C.captionFonts({director:{style:st}}),cfg:{}};"
                     "for(const p of Object.keys(C.CAPTION_PRESETS))for(const f of [null,'Poppins-ExtraBold','Anton'])o[st].cfg[p+'|'+f]=C.captionCfg({preset:p,font:f},C.captionFonts({director:{style:st}})).font;}"
                     "process.stdout.write(JSON.stringify(o));")
    assert sorted(out) == sorted(assets.PALETTES)
    for st, d in out.items():
        pal = assets.palette(st)["fonts"]
        assert d["fonts"] == {"title": pal["title"], "caption": pal["caption"]}, st
        for key, js in d["cfg"].items():
            p, f = key.split("|")
            eng = T._cap_cfg({"preset": p, "font": None if f == "null" else f}, _style_ctx(st))["font"]
            assert js == eng, (st, p, f, js, eng)


def _cap_hash(words, cfg, W=540, H=960):
    import hashlib
    from motion import ctx as CX
    from motion import text as T
    h = hashlib.sha1()
    t = words[0]["start"] - 0.1
    while t < words[-1]["end"] + 0.3:
        arr, surf = CX.new_frame(W, H)
        T.draw_captions(surf.getCanvas(), [words], cfg, t, CX.demo_ctx(W=W, H=H, t=t, quality="final", seed=7))
        h.update(arr.tobytes())
        t += 0.05
    return h.hexdigest()


@needs_node
def test_ajustes_que_el_motor_ignora_igual_al_motor(tmp_path):
    """«Centrar la frase mientras aparece» y «Resaltar una palabra clave» solo se ofrecen prendibles
    donde el export cambia algo (FXCore.captionOptionApplies): se dibuja cada estilo con el ajuste en
    Sí y en No con text.draw_captions y los cuadros cambian exactamente donde la UI dice que aplica.
    Sin palabra clave visible en Karaoke ni centrado en Karaoke / Pastilla de color."""
    from motion import text as T
    words, t = [], 1.0
    for w in "hoy cuidamos tus encías juntos".split():
        words.append({"text": w, "start": round(t, 3), "end": round(t + 0.24, 3), "kw": w == "encías"})
        t += 0.3
    base = {"enabled": True, "y": 0.72, "size": 0.052, "font": "Poppins-ExtraBold", "color": "#FFFFFF", "accent": "#1F9D55", "case": "none", "max_words": 5}
    got = _node_json(tmp_path, "applies.js", "const o={};for(const p of %s)o[p]={recenter:C.captionOptionApplies(p,'recenter'),kw_accent:C.captionOptionApplies(p,'kw_accent')};process.stdout.write(JSON.stringify(o));"
                     % json.dumps(list(T.CAPTION_PRESETS)))
    for p in T.CAPTION_PRESETS:
        for k in ("recenter", "kw_accent"):
            on, off = _cap_hash(words, dict(base, preset=p, **{k: True})), _cap_hash(words, dict(base, preset=p, **{k: False}))
            assert (on != off) == got[p][k], (p, k, "cambia" if on != off else "igual", got[p][k])
    ui = _ui()
    for p, d in got.items():
        for k, ok in d.items():
            if not ok:
                assert (ui["params"][f"captions.{k}"].get("note_by") or {}).get(p), (p, k)


@needs_node
def test_blanco_y_negro_sin_color_igual_al_motor(tmp_path):
    """Con un estilo de color sin color (grade.LOOKS[*].mono) el motor ignora Calidez, Tinte e
    Intensidad de los colores: FXCore.gradeOptionApplies lo dice igual (y la copy explica por que);
    con los demas estilos esos ajustes SI cambian el cuadro."""
    import numpy as np
    from motion import grade as G
    out = _node_json(tmp_path, "mono.js", "const o={mono:C.MONO_LOOKS,ap:{}};for(const l of C.LOOK_ORDER){o.ap[l]={};for(const k of ['temperature','tint','saturation','exposure','contrast'])o.ap[l][k]=C.gradeOptionApplies(l,k);}process.stdout.write(JSON.stringify(o));")
    assert sorted(out["mono"]) == sorted(k for k, L in G.LOOKS.items() if L.get("mono"))
    arr = _look_img(135, 240)
    W = arr.shape[1]

    def img(look, k, v):
        cfg = {"look": look, k: v, "grain": 0, "sharpen": 0}
        return G.grade_array(arr.copy(), cfg, G.build_lut(G.LOOKS[look], None, cfg), G.ArrCtx(W, None))[..., :3]
    span = {"temperature": (-0.7, 0.7), "tint": (-0.7, 0.7), "saturation": (0.3, 1.8), "exposure": (-0.5, 0.5), "contrast": (-0.3, 0.3)}
    for look, d in out["ap"].items():
        for k, ok in d.items():
            a, b = (img(look, k, v) for v in span[k])
            changes = float(np.abs(a.astype(int) - b.astype(int)).mean()) > 0.5
            assert changes == ok, (look, k, changes, ok)
    ui = _ui()
    for k in ("temperature", "tint", "saturation"):
        assert ui["params"][f"grade.{k}"]["note_by"]["bw_contrast"]


@needs_node
def test_tarjetas_de_edicion_conservan_lo_tuyo_igual_al_director(tmp_path):
    """Las tarjetas de «Estilo de edición» y «Qué cambia» dibujan lo que el director DEJA al cambiar de
    estilo: FXCore.directorOutcome = director._keep_captions / _keep_grade (con _grade_tuned) sobre
    los subtitulos y el color de cada estilo; el color de cada tarjeta = director._grade_cfg."""
    import copy
    from motion import director as D
    cards = _node_json(tmp_path, "dc.js", "process.stdout.write(JSON.stringify(C.DIRECTOR_CARDS));")
    for k in D.STYLES:
        g = D._grade_cfg(k)
        assert cards[k]["grade"] == {x: g[x] for x in ("look", "vignette", "grain", "sharpen")}, k
    caps = [{"enabled": True, "preset": "clean_bold", "font": "Poppins-ExtraBold", "color": "#FFFFFF", "case": "none", "max_words": 3},
            {"enabled": True, "preset": "boxed", "highlight": "#FF5A3C", "font": "Poppins-ExtraBold", "color": "#FFFFFF", "case": "none", "max_words": 3},
            {"enabled": False, "preset": "karaoke", "font": None, "color": "#FFD23F", "case": "upper", "max_words": 4, "stroke": 3, "recenter": False},
            {"enabled": True, "preset": "bold_caps", "font": "Anton", "color": "#FFFFFF", "case": "upper", "max_words": 3, "kw_accent": True}]
    grades = [{"enabled": True, "look": "warm_clean", "match_shots": True, "vignette": 0.15, "grain": 0.022, "sharpen": 0.15},
              {"enabled": True, "look": "natural", "match_shots": True, "vignette": 0.15, "grain": 0.022, "sharpen": 0.15},
              {"enabled": True, "look": "warm_clean", "match_shots": True, "vignette": 0.15, "grain": 0.022, "sharpen": 0.15, "exposure": 0.2},
              {"enabled": False, "look": "cinematic"}, {"look": "moody", "temperature": 0.0, "clarity": None}]
    cases = []
    for old in D.STYLES:
        for i, c in enumerate(caps):
            for j, g in enumerate(grades):
                for new in D.STYLES:
                    cases.append({"plan": {"director": {"style": old}, "fx": {"captions": c, "grade": g}}, "style": new})
    got = _node_json(tmp_path, "outcome.js", "const cs=%s;process.stdout.write(JSON.stringify(cs.map(c=>C.directorOutcome(c.plan,c.style))));" % json.dumps(cases))
    for c, o in zip(cases, got):
        old, new, f = c["plan"]["director"]["style"], c["style"], c["plan"]["fx"]
        fx = {"captions": dict(D.STYLES[new]["captions"], enabled=True), "grade": D._grade_cfg(new)}
        D._keep_captions(fx, copy.deepcopy(f), old)
        D._keep_grade(fx, copy.deepcopy(f), old)
        for k in D._CAP_LOOK + ("preset", "y", "size", "font", "case", "max_words"):
            assert o["captions"].get(k) == fx["captions"].get(k), (old, new, k, o["captions"].get(k), fx["captions"].get(k))
        assert o["grade"] == fx["grade"] and o["gradeKept"] == D._grade_tuned(f["grade"], old), (old, new, f["grade"])


@needs_node
def test_animacion_de_subtitulos_igual_al_motor(tmp_path):
    """capdraw.js anima los subtitulos con las curvas del motor (text._draw_group_body): resorte de
    pop (3.4 Hz / 0.6) y pop_strong (3.0 / 0.62), entrada del grupo con out_expo, salida con in_quad
    (y 4 % mas chico) y el recentrado con resorte de text._recenter_dx, medido sobre un grupo real
    del motor (renglon y palabras del export)."""
    from motion import ease
    from motion import text as T
    us = [0.0, 0.01, 0.03, 0.05, 0.08, 0.12, 0.2, 0.3, 0.5, 1.0]
    got = _node_json(tmp_path, "anim.js", "const us=%s;process.stdout.write(JSON.stringify({pop:us.map(u=>CD.wordAnim('pop',u,56)),ps:us.map(u=>CD.wordAnim('pop_strong',u,56)),"
                     "fade:us.map(u=>CD.wordAnim('fade',u,56)),gin:us.map(u=>CD.groupIn(u,56)),gout:us.map(u=>CD.groupOut(Math.min(1,u)))}));" % json.dumps(us), capdraw=True)
    fs = 56
    for i, u in enumerate(us):
        assert abs(got["pop"][i]["sc"] - (0.8 + 0.2 * ease.spring(u, freq=3.4, damping=0.6))) < 1e-9, u
        assert abs(got["pop"][i]["a"] - ease.out_cubic(u / 0.08)) < 1e-9 and abs(got["pop"][i]["dy"] - (1 - ease.out_cubic(u / 0.22)) * 0.1 * fs) < 1e-9
        assert abs(got["ps"][i]["sc"] - (0.7 + 0.3 * ease.spring(u, freq=3.0, damping=0.62))) < 1e-9, u
        assert abs(got["fade"][i]["a"] - ease.smoothstep(u / 0.2)) < 1e-9 and abs(got["fade"][i]["dy"] - (1 - ease.out_cubic(u / 0.3)) * 0.12 * fs) < 1e-9
        assert abs(got["gin"][i]["a"] - ease.out_cubic(u / 0.14)) < 1e-9 and abs(got["gin"][i]["dy"] - (1 - ease.out_expo(u / 0.35)) * 0.22 * fs) < 1e-9
        po = min(1.0, u)
        assert abs(got["gout"][i]["a"] - (1 - ease.in_quad(po))) < 1e-9 and abs(got["gout"][i]["sc"] - (1.0 - 0.04 * ease.in_quad(po))) < 1e-9
    # recentrado sobre un grupo real del motor (clean_bold, una linea)
    W = 1080
    words, t = [], 1.0
    for w in "hoy cuido mis dientes".split():
        words.append({"text": w, "start": round(t, 3), "end": round(t + 0.24, 3)})
        t += 0.3
    cfg = {"enabled": True, "preset": "clean_bold", "y": 0.72, "size": 0.052, "font": "Poppins-ExtraBold", "color": "#FFFFFF", "case": "none", "max_words": 4}
    ctx0 = _cap_ctx(0.0)
    g = T._caption_groups([words], cfg, ctx0)[0]
    assert len(g.lines) == 1
    D = T._cap_cfg(cfg, ctx0)
    fs0 = D["size"] * W
    table = {" ": {"adv": T._space(D["font"], fs0), "l": 0, "r": 0, "asc": 0, "desc": 0}}
    for w in words:
        r = T._run(w["text"], D["font"], fs0)
        table[w["text"]] = {"adv": r.adv, "l": r.ink[0], "r": r.ink[2], "asc": -r.ink[1], "desc": r.ink[3]}
    pg = T.caption_groups([words], cfg, ctx0)[0]
    gr = {"words": words, "start": pg["start"], "end": pg["end"], "lines": pg["lines"], "rect": [pg["rect"][0] / W, pg["rect"][1] / 1920, pg["rect"][2] / W, pg["rect"][3] / 1920], "accent": []}
    ts = [words[0]["start"] - 0.05, words[1]["start"] - 0.05 + 0.08, words[2]["start"], words[3]["start"] + 0.1, words[3]["start"] + 1.0]
    code = ("const d=%s;const q=d.table,fs0=%r;const meas=(t,px)=>{const m=q[t];const k=px/fs0;return {adv:m.adv*k,l:m.l*k,r:m.r*k,asc:m.asc*k,desc:m.desc*k};};"
            "const B={x:0,y:0,w:1080,h:1920,s:1};const L=CD.layout(d.gr,d.cfg,B,{srv:true,measure:meas,fontCss:()=>'x'});"
            "process.stdout.write(JSON.stringify(d.ts.map(t=>CD.recenterDx(L,L.rows[0],t))));") % (json.dumps({"table": table, "gr": gr, "cfg": cfg, "ts": ts}), fs0)
    js = _node_json(tmp_path, "rc.js", code, capdraw=True)
    for tt, x in zip(ts, js):
        eng = T._recenter_dx(g, tt)[0]
        assert abs(x - eng) <= 1.5, (tt, x, eng)
