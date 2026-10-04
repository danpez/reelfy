"""Subtitulos que se alcanzan a leer: ningun grupo parpadea (menos de text.MIN_SHOW s en pantalla antes
del siguiente) si se puede evitar, y una tinta clara sobre un fondo claro (la interfaz blanca de una app en
un video de apoyo o una grabacion de pantalla) lleva un trazo oscuro fino y la sombra completa
(text.caption_contrast; el Studio usa el mismo numero: FXCore.captionContrast)."""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from motion import ctx as C
from motion import text as TX

NODE = shutil.which("node")
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def _ctx():
    return C.FrameCtx(W=1080, H=1920, fps=30, n=1)


def _words(rng, n, fast=True):
    out, t = [], 0.3
    pool = ["hola", "sí", "claro,", "te", "cuento", "esto", "rápido", "mira", "aquí", "está", "la", "app,",
            "y", "ya", "funciona", "bien", "ahora", "sigue", "con", "el", "precio."]
    for _ in range(n):
        d = rng.uniform(0.08, 0.16) if fast else rng.uniform(0.2, 0.4)
        out.append({"text": rng.choice(pool), "start": round(t, 3), "end": round(t + d, 3)})
        t += d + rng.choice([0.0, 0.0, 0.02, 0.05])
    return out


def _shows(groups):
    """Lo que se ve cada grupo antes del siguiente (los finales de tramo no cuentan: se quedan un poco)."""
    out = []
    for g, nx in zip(groups, groups[1:]):
        gap = nx[0]["start"] - g[-1]["end"]
        if TX._HARD_GAP < gap or g[-1]["text"].endswith((".", "!", "?")):
            continue
        out.append(nx[0]["start"] - g[0]["start"])
    return out


def test_ningun_grupo_parpadea_si_se_puede_evitar(monkeypatch):
    rng = random.Random(5)
    D = TX._cap_cfg({"preset": "boxed", "max_words": 3}, _ctx())
    assert D["max_words"] == 3
    new_short, old_short, total = 0, 0, 0
    for _ in range(40):
        ws = _words(rng, rng.randint(6, 14))
        g_new = TX._segment(ws, D, _ctx())
        monkeypatch.setattr(TX, "SHOW_COST", 0.0)
        monkeypatch.setattr(TX, "SHOW_K", 0.0)
        g_old = TX._segment(ws, D, _ctx())
        monkeypatch.undo()
        sn, so = _shows(g_new), _shows(g_old)
        total += len(sn)
        # los que de verdad parpadean (dos palabras rapidas: menos de 1/4 de segundo)
        new_short += sum(1 for x in sn if x < 0.25)
        old_short += sum(1 for x in so if x < 0.25)
        # nunca peor que antes en lo mas corto que se ve
        if sn and so:
            assert min(sn) >= min(so) - 1e-6
        # las palabras siguen todas, en orden
        assert [w["text"] for g in g_new for w in g] == [w["text"] for w in ws]
    assert old_short > 0, "el caso de prueba debia tener grupos que parpadean con la regla vieja"
    assert new_short <= old_short // 3, (new_short, old_short)


def test_a_ritmo_normal_nada_cambia():
    rng = random.Random(9)
    D = TX._cap_cfg({"preset": "clean_bold", "max_words": 3}, _ctx())
    for _ in range(20):
        ws = _words(rng, 10, fast=False)
        assert all(x >= TX.MIN_SHOW for x in _shows(TX._segment(ws, D, _ctx())))


def test_contraste_tinta_clara_sobre_fondo_claro():
    f = TX.caption_contrast
    assert f("#FFFFFF", 0.9, 0.0, 0.95) == (TX.CONTRAST_STROKE, TX.CONTRAST_SHADOW)
    assert f("#FFFFFF", 0.9, 4.0, 1.4) == (4.0, 1.4)                # lo que ya tiene mas, se queda
    assert f("#FFFFFF", 0.3, 0.0, 0.95) == (0.0, 0.95)              # fondo oscuro: lo del estilo
    assert f("#1A1A1A", 0.9, 0.0, 0.95) == (0.0, 0.95)              # tinta oscura: no aplica
    assert f("#FFFFFF", None, 0.0, 0.95) == (0.0, 0.95)
    assert f("#FFFFFF", -1, 0.0, 0.95) == (0.0, 0.95)


@pytest.mark.skipif(not NODE, reason="sin node")
def test_contraste_igual_en_el_studio(tmp_path):
    rng = random.Random(3)
    cases = [[rng.choice(["#FFFFFF", "#FFD23F", "#0B7A52", "#111111", "#F0F0F0"]),
              rng.choice([None, round(rng.random(), 3)]), rng.choice([0, 1.0, 2.0, 5.0]), rng.choice([0, 0.8, 0.95, 1.4])]
             for _ in range(80)]
    want = [list(TX.caption_contrast(*c)) for c in cases]
    js = tmp_path / "c.js"
    js.write_text(f"""
const C = require({json.dumps(str(STATIC / "fxcore.js"))});
const cases = {json.dumps(cases)};
console.log(JSON.stringify(cases.map(c => {{ const r = C.captionContrast(c[0], c[1], c[2], c[3]); return [r.stroke, r.shadow]; }})));
""")
    r = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout)
    for c, a, b in zip(cases, want, got):
        assert a == pytest.approx(b), c
    assert (TX.CONTRAST_INK, TX.CONTRAST_LUM, TX.CONTRAST_STROKE, TX.CONTRAST_SHADOW) == (0.6, 0.55, 2.0, 1.0)
    assert "ink: 0.6, lum: 0.55, stroke: 2.0, shadow: 1.0" in (STATIC / "fxcore.js").read_text()


def test_ningun_grupo_asoma_entre_dos_titulos_o_al_terminar_uno():
    """Tres titulos de gancho que repiten lo dicho (0.23-1.61, 2.17-2.97, 2.99-3.83) escondian los subtitulos:
    un grupo de dos palabras se veia 0.058 s (2 cuadros) entre dos titulos y otros 1 cuadro al terminar
    uno. Los tramos ocultos separados por menos de MIN_SHOW se unen, el agrupado cuenta
    solo lo visible y un grupo que asoma menos de MIN_SHOW junto a un titulo que ya lo mostro no se dibuja;
    ninguna palabra dicha FUERA de los titulos se pierde."""
    hide = [[0.23, 1.61], [2.17, 2.97], [2.99, 3.83]]
    spans = [("Nubis acaba de sacar algo", 0.25, 1.58), ("que", 1.66, 2.08),
             ("va a cambiar mucho", 2.12, 2.95), ("como agendas citas", 3.0, 3.8),
             ("con tu equipo de la clinica y hoy te lo cuento", 3.86, 6.4)]
    ws = []
    for txt_, a, b in spans:
        tt = txt_.split()
        step = (b - a) / len(tt)
        ws += [{"text": w, "start": round(a + k * step, 3), "end": round(a + (k + 0.85) * step, 3)}
               for k, w in enumerate(tt)]
    assert TX.hide_spans(hide) == [[0.23, 1.61], [2.17, 3.83]]            # 0.02 s entre dos: uno solo
    assert TX.hide_spans([[0.0, 1.0], [1.1, 2.0], [3.0, 4.0]]) == [[0.0, 2.0], [3.0, 4.0]]
    ctx = _ctx()
    cfg = {"enabled": True, "preset": "clean_bold", "max_words": 3, "_hide": hide}
    gs = TX.caption_groups([ws], cfg, ctx)
    hs = TX.hide_spans(hide)
    assert " ".join(g["text"] for g in gs).split() == [w["text"] for w in ws]
    # cuadro a cuadro (30 cps): ningun grupo se dibuja menos de MIN_SHOW, y cada palabra dicha fuera de los
    # titulos se ve en algun cuadro
    small = C.FrameCtx(W=ctx.W // 4, H=ctx.H // 4, fps=30, n=1)
    arr, surf = C.new_frame(small.W, small.H)
    runs, cur, on_at = [], 0, []
    for k in range(int(7.0 * 30)):
        surf.getCanvas().clear(C.color("#000000"))
        TX.draw_captions(surf.getCanvas(), [ws], cfg, k / 30, small)
        on = bool((arr[..., :3] > 200).any())
        on_at.append(on)
        if on:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    assert runs and min(runs) >= int(TX.MIN_SHOW * 30) - 1, runs
    shown_by_titles = set(" ".join(x[0] for x in spans if x[0] != "que" and "cuento" not in x[0]).split())
    for w in ws:
        if w["text"] not in shown_by_titles:
            k0, k1 = int(w["start"] * 30), int(w["end"] * 30) + 1
            assert any(on_at[k0:k1 + 3]), w
    # sin titulos nada cambia (el mismo agrupado de siempre)
    g0 = TX.caption_groups([ws], {"enabled": True, "preset": "clean_bold", "max_words": 3}, ctx)
    assert [g["text"] for g in g0] == [g["text"] for g in TX.caption_groups(
        [ws], {"enabled": True, "preset": "clean_bold", "max_words": 3, "_hide": []}, ctx)]


def test_placa_sobre_una_interfaz_con_texto_y_no_sobre_la_camara():
    """Sobre un video de apoyo a pantalla completa con un menu (renglones claros/oscuros bajo el subtitulo)
    el grupo lleva una placa oscura (el subtitulo y el menu no se leen como un bloque; un acento verde sobre un
    personaje azul se lee); sobre la camara (sin cuadro compuesto debajo) o un fondo oscuro liso, no."""
    import numpy as np
    import skia
    W_, H_ = 540, 960
    cfg = {"enabled": True, "preset": "clean_bold", "max_words": 3}
    ws = [{"text": t, "start": 0.2 + 0.3 * k, "end": 0.45 + 0.3 * k} for k, t in enumerate("seguir trabajando para".split())]

    def frame(bg):
        arr, surf = C.new_frame(W_, H_)
        ctx = C.FrameCtx(W=W_, H=H_, fps=30, n=1)
        if bg is not None:
            ctx._cache["caption_bg"] = skia.Image.fromarray(np.ascontiguousarray(bg), colorType=skia.kRGBA_8888_ColorType)
            surf.getCanvas().drawImage(ctx._cache["caption_bg"], 0, 0)
        else:
            surf.getCanvas().clear(C.color("#303030"))
        TX.draw_captions(surf.getCanvas(), [ws], cfg, 1.2, ctx)
        g = TX.caption_groups([ws], cfg, ctx)[0]
        return arr.copy(), g["rect"], ctx
    menu = np.zeros((H_, W_, 4), np.uint8)
    menu[..., :3], menu[..., 3] = 246, 255
    for k in range(0, H_, 36):                                   # renglones de un menu (texto oscuro)
        menu[k + 10:k + 22, 40:W_ - 60, :3] = 40
    arr, rect, ctx = frame(menu)
    assert TX.caption_plate(ctx, skia.Rect.MakeLTRB(*rect)) == TX.PLATE_ALPHA
    x0, y0, x1, y1 = (int(v) for v in rect)
    # alrededor del texto (la placa difusa) queda mas oscuro que el menu sin subtitulo
    ring = np.concatenate([arr[y0 - 6:y0, x0:x1, :3].reshape(-1, 3), arr[y1:y1 + 6, x0:x1, :3].reshape(-1, 3)])
    assert ring.mean() < menu[y0 - 6:y0, x0:x1, :3].mean() - 25
    dark = np.zeros((H_, W_, 4), np.uint8)
    dark[..., :3], dark[..., 3] = 24, 255
    _arr, rect2, ctx2 = frame(dark)
    assert TX.caption_plate(ctx2, skia.Rect.MakeLTRB(*rect2)) == 0.0
    _arr, rect3, ctx3 = frame(None)
    assert TX.caption_plate(ctx3, skia.Rect.MakeLTRB(*rect3)) == 0.0


def test_habla_rapida_sin_grupos_que_parpadean():
    """Siete palabras en 1.3 s con 3 palabras por grupo dejaban un grupo de 0.18 s en pantalla (y otro de 0.34
    s al final de una frase rapida). Un grupo puede llevar UNA palabra mas que el maximo solo si asi nada
    parpadea; a ritmo normal sigue el maximo."""
    def ws(spec):
        return [{"text": t, "start": a, "end": b} for t, a, b in spec]
    fast = ws([("Nubis", 0.31, 0.81), ("acaba", 0.85, 1.09), ("de", 1.11, 1.24), ("sacar", 1.24, 1.491),
               ("algo", 1.511, 1.671), ("que", 1.711, 1.791), ("creo", 1.931, 2.131), ("que", 2.171, 2.251),
               ("va", 2.251, 2.351), ("a", 2.351, 2.431), ("cambiar", 2.431, 2.651), ("mucho", 2.671, 3.051),
               ("la", 3.071, 3.201), ("forma", 3.201, 3.351), ("agendamos.", 3.4, 4.1)])
    hist = ws([("mira", 31.9, 32.3), ("en", 32.73, 32.84), ("el", 32.86, 32.95), ("registro", 32.96, 33.4),
               ("que", 33.44, 33.54), ("tiene", 33.56, 33.76), ("con", 33.78, 33.88), ("ellos.", 33.9, 34.4)])
    cfg = {"enabled": True, "preset": "clean_bold", "max_words": 3}
    for w in (fast, hist):
        gs = TX.caption_groups([w], cfg, C.FrameCtx(W=1080, H=1920, fps=30, n=1))
        assert " ".join(g["text"] for g in gs).split() == [x["text"] for x in w]
        assert all(g["end"] - g["start"] >= 0.4 for g in gs), [(g["text"], round(g["end"] - g["start"], 3)) for g in gs]
        assert all(len(g["text"].split()) <= 4 for g in gs)
    # a ritmo normal: nunca mas del maximo
    slow = ws([(t, 0.3 + 0.45 * k, 0.65 + 0.45 * k) for k, t in enumerate(
        "hoy les quiero contar como reservamos una cita en la clinica sin llamar".split())])
    gs = TX.caption_groups([slow], cfg, C.FrameCtx(W=1080, H=1920, fps=30, n=1))
    assert all(len(g["text"].split()) <= 3 for g in gs)


def test_placa_cuando_el_acento_no_contrasta_con_el_color_de_atras():
    """El verde de la marca (la palabra que se dice) sobre un personaje AZUL de luminancia media tenia un
    contraste ~1:1 y no llevaba placa (la regla solo miraba si el fondo era claro o tenia texto). Ahora el
    acento se mide contra el COLOR medio de atras: < 3:1 -> placa firme; sobre gris oscuro o la camara, no."""
    import numpy as np
    import skia
    W_, H_ = 540, 960
    cfg = {"enabled": True, "preset": "clean_bold", "max_words": 3, "accent": "#2BB673"}
    ws = [{"text": t, "start": 0.2 + 0.3 * k, "end": 0.45 + 0.3 * k} for k, t in enumerate("una tarea nueva".split())]

    def plate(col):
        ctx = C.FrameCtx(W=W_, H=H_, fps=30, n=1)
        if col is not None:
            bg = np.zeros((H_, W_, 4), np.uint8)
            bg[..., :3], bg[..., 3] = col, 255
            ctx._cache["caption_bg"] = skia.Image.fromarray(bg, colorType=skia.kRGBA_8888_ColorType)
        g = TX.caption_groups([ws], cfg, ctx)[0]
        D = TX._cap_cfg(cfg, ctx)
        acc = D["accent"] if D.get("active") == "color" else None
        return TX.caption_plate(ctx, skia.Rect.MakeLTRB(*g["rect"]), acc or "#2BB673")
    assert plate((70, 100, 200)) == TX.PLATE_ALPHA_ACCENT          # azul medio: el verde no se lee
    assert plate((20, 20, 24)) == 0.0                              # casi negro: el verde se lee (> 3:1)
    assert plate(None) == 0.0                                      # sobre la camara nada cambia
    assert TX._wcag("#2BB673", (70, 100, 200)) < TX.ACCENT_MIN_CONTRAST
