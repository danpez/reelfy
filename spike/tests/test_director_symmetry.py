"""Regla de SIMETRIA del director (motion/director.py): todo overlay que agrega Reelfy deja el mismo
aire a la izquierda y a la derecha de su tinta.

- Invariante sobre planes que dirige el director (sin LLM, datos sinteticos): todo texto medido
  (text.measure) centrado a <= 0.005 de 0.5; graficos centrados en x = 0.5 con ancho <= SYM_W
  (tarjetas, CTA, etiquetas, chips, rotulo align 'center', marca de agua); acentos centrados o en
  PAR ESPEJO exacto ('<id>' y '<id>_m': mismo glifo simetrico, tamano, y, tiempos y animacion;
  x y 1-x; giro invertido; un solo SFX al centro); flechas verticales centradas; escenas con
  decorados en par espejo exacto y la lista con su caja comun centrada.
- symmetrize(plan) sobre un plan existente: cta/tarjeta en 0.47, rotulo en 0.07 (legado), icono en
  0.78, destello de un lado, '?' de un lado, flecha curva en 0.85, cascada a la izquierda,
  contador anclado a la derecha, escenas con decorados desparejos; lo del usuario, lo bloqueado y
  director.locked NUNCA se mueven; determinista e idempotente.
- Medicion por pixeles (como el arnes de QA) de lo estructural del plan dirigido.

Solo datos FICTICIOS (frases inventadas, sin clientes reales).
"""
import copy
import json

import numpy as np
import pytest

from motion import ctx as C
from motion import director as D
from motion.ctx import SAFE
import test_director as TD
import test_profile_director as TP

TOL = 0.005                     # |aire izq - aire der| / W (regla: <= 0.005 W en reposo)


# ---------------------------------------------------------------- invariante
def _movable(plan: dict):
    locked = set((plan.get("director") or {}).get("locked") or [])
    return lambda it: it.get("src", "ai") == "ai" and not it.get("locked") and it.get("id") not in locked


def _pair_ok(a: dict, b: dict) -> str | None:
    """None si (a, b) es un par espejo EXACTO; si no, el motivo."""
    if a.get("kind") != "doodle" or b.get("kind") != "doodle":
        return "par de no-garabatos"
    if a.get("name") not in D.SYM_GLYPHS:
        return f"glifo con mano en par ({a.get('name')})"
    for k in ("name", "size", "y", "start", "end", "anim_in", "anim_out", "color", "border_color", "offset"):
        if a.get(k) != b.get(k):
            return f"{k} distinto ({a.get(k)} vs {b.get(k)})"
    if abs(float(a["x"]) + float(b["x"]) - 1.0) > 1e-3:
        return f"x no espejo ({a['x']}, {b['x']})"
    if abs(float(a.get("rotation", 0) or 0) + float(b.get("rotation", 0) or 0)) > 1e-6:
        return "giro no invertido"
    if abs(float(a["x"]) - 0.5) < D.ACCENT_SEP / 2 - 1e-6:
        return "par demasiado junto"
    return None


def _accent_violation(g: dict, siblings: list[dict]) -> str | None:
    """Acento (garabato / icono) de la IA: centrado, flecha vertical centrada o par espejo."""
    if g.get("path_to"):
        if abs(float(g["x"]) - 0.5) > 1e-6 or abs(float(g["path_to"][0]) - 0.5) > 1e-6:
            return f"flecha no vertical centrada x={g['x']} path_to={g['path_to']}"
        if str(g.get("name", "")).startswith("arrow") and float(g.get("bend", 1.0) or 0.0) != 0.0:
            return "flecha curva (bend != 0)"
        return None
    if abs(float(g["x"]) - 0.5) <= 1e-6:
        return None
    gid = str(g.get("id") or "")
    by = {str(o.get("id")): o for o in siblings if o.get("id")}
    if gid.endswith("_m") and gid[:-2] in by:
        return _pair_ok(by[gid[:-2]], g)
    if gid + "_m" in by:
        return _pair_ok(g, by[gid + "_m"])
    return f"acento de un lado x={g['x']}"


def _union_center(gr: list[dict]) -> float:
    spans = [sp for sp in (D._Choreo._nested_span(g) for g in gr) if sp is not None]
    return (min(a for a, _ in spans) + max(b for _, b in spans)) / 2


def _violations(plan: dict, style: str = "dinamico") -> list[str]:
    out = []
    mv = _movable(plan)
    fx = plan["fx"]
    for it in fx["text"]:
        if not mv(it) or it.get("enabled") is False:
            continue
        if abs(float(it["x"]) - 0.5) > 1e-6 or it.get("align", "center") != "center":
            out.append(f"{it['id']}: texto x={it['x']} align={it.get('align')}")
            continue
        r = D.measure_text(it, style)
        if r is not None and abs((r[0] + r[2]) / 2 - 0.5) > TOL:
            # la caja de text.measure no cuenta el relleno de la caja (marcador / kicker): manda la
            # tinta real dibujada en reposo
            m = _ink_x(_draw("text", it))
            if m is None or abs(m[0] - m[1]) > TOL:
                out.append(f"{it['id']}: tinta centrada en {(r[0] + r[2]) / 2:.4f} (pixeles {m})")
        if it.get("preset") == "counter" and it.get("anchor", "center") != "center":
            out.append(f"{it['id']}: contador anchor {it.get('anchor')}")
    gl = fx["graphics"]
    for g in gl:
        if not mv(g) or g.get("enabled") is False or g["kind"] == "progress":
            continue
        if g["kind"] == "media" and g.get("x") is None:
            continue
        if g["kind"] in ("doodle", "icon"):
            why = _accent_violation(g, gl)
            if why:
                out.append(f"{g['id']}: {why}")
            continue
        if abs(float(g["x"]) - 0.5) > 1e-6:
            out.append(f"{g['id']}: {g['kind']} x={g['x']}")
        if float(g.get("width") or 0) > C.SYM_W + 1e-6:
            out.append(f"{g['id']}: width {g['width']} > SYM_W")
        if g["kind"] in ("card", "cta") and float(g.get("size", 0)) > C.SYM_W + 1e-6:
            out.append(f"{g['id']}: size {g['size']} > SYM_W")
        if g["kind"] == "lower_third" and g.get("align") != "center":
            out.append(f"{g['id']}: rotulo sin align center")
    for c in fx["cutaways"]:
        if not mv(c) or c.get("enabled") is False:
            continue
        t = c.get("text")
        if isinstance(t, dict) and t.get("x") is not None and abs(float(t["x"]) - 0.5) > 1e-6:
            out.append(f"{c['id']}>text x={t['x']}")
        cd = c.get("card")
        if isinstance(cd, dict) and (abs(float(cd.get("x", 0.5)) - 0.5) > 1e-6 or float(cd.get("size", 0)) > C.SYM_W + 1e-6):
            out.append(f"{c['id']}>card x={cd.get('x')} size={cd.get('size')}")
        gr = [g for g in c.get("graphics") or [] if isinstance(g, dict)]
        if not gr:
            continue
        if c["id"].startswith("cw_list"):
            if abs(_union_center(gr) - 0.5) > TOL:
                out.append(f"{c['id']}: caja comun de la lista en {_union_center(gr):.4f}")
            continue
        for g in gr:
            if g.get("x") is None:
                continue
            if g.get("kind") in ("doodle", "icon"):
                why = _accent_violation(g, gr)
                if why:
                    out.append(f"{c['id']}>{g.get('id') or g.get('name')}: {why}")
            elif abs(float(g["x"]) - 0.5) > 1e-6:
                out.append(f"{c['id']}>{g.get('kind')} x={g['x']}")
            elif float(g.get("width") or 0) > C.SYM_W + 1e-6:
                out.append(f"{c['id']}>{g.get('kind')} width {g['width']}")
    return out


def _pairs(plan: dict) -> list[tuple[dict, dict]]:
    by = {g["id"]: g for g in plan["fx"]["graphics"]}
    return [(by[i[:-2]], g) for i, g in by.items() if i.endswith("_m") and i[:-2] in by]


# ---------------------------------------------------------------- planes dirigidos
@pytest.fixture(scope="module")
def directed():
    out = {f"synth_{st}": D.direct(TD.synthetic_plan(), style=st, intensity=0.75, seed=7, use_llm=False)
           for st in D.STYLES}
    out["synth_i1"] = D.direct(TD.synthetic_plan(), style="energetico", intensity=1.0, seed=3, use_llm=False)
    out["closeup"] = D.direct(TD._closeup_plan(), use_llm=False, trim=False)
    out["prof"] = TP._direct(TD.synthetic_plan(), TP.PROF)
    out["prof_broll"] = TP._direct(TP._lines_plan(TP.LINK_LINES, broll_end=3.0), TP.RICH)
    out["prof_closeup"] = TP._direct(TD._closeup_plan(), TP.RICH)
    wm = TP._prof(use={"lower_third": True, "follow_cta": True, "booking_cta": True, "end_card": True,
                       "handle_watermark": True})
    out["prof_watermark"] = TP._direct(TD.synthetic_plan(), wm)
    return out


def test_every_directed_overlay_is_symmetric(directed):
    """Todo lo que coloca el director (texto, graficos, escenas y lo que va dentro) queda centrado
    en x = 0.5 o es un miembro de un par espejo exacto."""
    for name, p in directed.items():
        style = p["director"]["style"]
        v = _violations(p, style)
        assert not v, (name, v)
        TD._check_catalog(p)


def test_directed_plans_have_mirrored_pairs_with_one_centered_sfx(directed):
    """Los destellos van en par espejo ('<id>' y '<id>_m'): un solo SFX por par, al centro."""
    pairs = [(n, a, b) for n, p in directed.items() for a, b in _pairs(p)]
    assert pairs, "ningun par espejo en los planes sinteticos"
    for n, a, b in pairs:
        assert _pair_ok(a, b) is None, (n, a["id"], _pair_ok(a, b))
        p = directed[n]
        near = [s for s in p["fx"]["sfx"] if abs(s["at"] - (a["start"] + 0.03)) < 0.12
                and s["name"] in D._accent_sfx_names(a)]
        assert len(near) <= 1, (n, a["id"], near)
        for s in near:
            assert s["pan"] == 0.0, (n, a["id"], s)


def test_lower_third_is_centered_and_sized_to_its_text(directed):
    lts = [(n, g) for n, p in directed.items() for g in p["fx"]["graphics"] if g["kind"] == "lower_third"]
    assert lts
    for n, g in lts:
        assert g["align"] == "center" and g["x"] == 0.5, (n, g)
        w = D.lt_width(g["title"], g.get("subtitle", ""), g["size"], directed[n]["director"]["style"])
        assert 0.2 < w <= C.SYM_W + 1e-9, (n, w)


def test_end_card_stack_centered_with_symmetric_width(directed):
    """Pila del cierre (Seguir, chips, ubicacion, reservas) y la escena final: centro 0.5 y el
    ancho comun <= SYM_W (antes 0.47 con 0.80: 0.07 a la izquierda y 0.13 a la derecha)."""
    seen = 0
    for n, p in directed.items():
        for g in p["fx"]["graphics"]:
            if g["kind"] in ("cta", "socials") or (g["kind"] == "card" and g.get("variant") == "location"):
                assert g["x"] == 0.5, (n, g["id"])
                assert float(g.get("width") or g.get("size") or 0) <= C.SYM_W + 1e-9, (n, g["id"])
                seen += 1
        for c in p["fx"]["cutaways"]:
            if c["id"].startswith("cw_endcard"):
                assert {g["x"] for g in c["graphics"]} == {0.5}, (n, c["id"])
    assert seen


def test_watermark_top_center(directed):
    wm = [g for g in directed["prof_watermark"]["fx"]["graphics"] if g.get("variant") == "watermark"]
    assert wm
    for g in wm:
        assert g["x"] == 0.5 and g["y"] < 0.2, g


def test_cascade_is_centered_block():
    """La cascada se centra como bloque (la escalera va dentro de su caja)."""
    p = TD.synthetic_plan()
    script = D.analyze_script(p, use_llm=False)
    ch = D._Choreo(p, p["shots"], script, "dinamico", 0.75, 7)
    blk = [[w] for w in ch.words[:3]]
    spec = ch._text_spec("cascade", blk, set())
    assert spec["align"] == "center" and ch._x_for(spec) == 0.5
    it = {k: v for k, v in spec.items() if not k.startswith("_")}
    it.update(x=0.5, y=0.3)
    r = D.measure_text(it, "dinamico")
    assert r is not None and abs((r[0] + r[2]) / 2 - 0.5) <= TOL, r


def test_enum_list_scene_is_centered():
    """Lista de 3 en escena: columnas simetricas y la caja comun (iconos + etiquetas + punteados)
    centrada en 0.5."""
    p = TD.synthetic_plan()
    ws = [w for ph in p["phrases"] for w in ph]
    txt = "Un plan real mejora tu energia con dieta, ejercicio y agua limpia hoy."
    s2 = [w for w in ws if w["text"].startswith("Una")][0]
    k0 = ws.index(s2)
    for k, tok in enumerate(txt.split()):
        ws[k0 + k]["text"] = tok
    out = D.direct(p, use_llm=False)
    sc = [c for c in out["fx"]["cutaways"] if c["id"].startswith("cw_list")]
    assert sc
    gr = sc[0]["graphics"]
    icons = [g for g in gr if g["kind"] == "icon"]
    assert len(icons) == 3
    assert abs(_union_center(gr) - 0.5) <= TOL
    xs = sorted(g["x"] for g in icons)
    assert abs((xs[1] - xs[0]) - (xs[2] - xs[1])) < 1e-3            # columnas equiespaciadas
    assert sc[0]["text"]["x"] == 0.5
    assert not _violations(out)


def test_regenerate_keeps_everything_symmetric(directed):
    """'Regenerar' un titulo, un acento de un par o una escena: el plan sigue simetrico (el gemelo
    sigue a su base en espejo)."""
    p = copy.deepcopy(directed["synth_dinamico"])
    ids = [t["id"] for t in p["fx"]["text"] if t["preset"] != "counter"][:3]
    ids += [a["id"] for a, _b in _pairs(p)][:1] + [b["id"] for _a, b in _pairs(p)][:1]
    ids += [c["id"] for c in p["fx"]["cutaways"]][:1]
    assert ids
    for iid in ids:
        for sd in (3, 11):
            q = D.redirect(p, only_ids=[iid], seed=sd)
            assert not _violations(q), (iid, sd, _violations(q))


def test_restyle_and_profile_keep_everything_symmetric(directed):
    p = copy.deepcopy(directed["synth_dinamico"])
    q = D.redirect(p, style="energetico", intensity=0.9)
    assert not _violations(q, "energetico"), _violations(q, "energetico")
    r = D.apply_profile(p, TP.RICH)
    assert not _violations(r), _violations(r)


# ---------------------------------------------------------------- medicion por pixeles
def _ink_x(arr: np.ndarray) -> tuple[float, float] | None:
    cols = np.where(arr[..., 3].max(axis=0) > 24)[0]
    if not len(cols):
        return None
    return cols[0] / arr.shape[1], (arr.shape[1] - cols[-1] - 1) / arr.shape[1]


def _draw(track: str, it: dict, W: int = 1080) -> np.ndarray:
    from motion import graphics as G
    from motion import text as T
    H = W * 16 // 9
    s, e = float(it.get("start", 0)), float(it.get("end", 1))
    dur = max(0.2, e - s)
    lt = min(dur * 0.65, max(0.0, dur - 0.35))
    arr, surf = C.new_frame(W, H)
    ctx = C.demo_ctx(W, H, t=s + lt)
    if track == "text":
        T.draw(surf.getCanvas(), it, lt, dur, ctx)
    else:
        G.RENDERERS[it["kind"]](surf.getCanvas(), it, lt, dur, ctx)
    return arr


def test_pixels_structural_overlays_leave_equal_margins(directed):
    """Como el arnes de QA: cada titulo, tarjeta, CTA, etiqueta, chips y rotulo dibujado SOLO en
    reposo deja el mismo aire a los lados (1% de W: el sombreado del renderer suma pocos px)."""
    n = 0
    for name in ("synth_dinamico", "synth_elegante", "prof_broll", "prof"):
        p = directed[name]
        for tr in ("text", "graphics"):
            for it in p["fx"][tr]:
                if tr == "graphics" and it["kind"] in ("doodle", "icon", "progress", "media"):
                    continue
                m = _ink_x(_draw(tr, it))
                if m is None:
                    continue
                assert abs(m[0] - m[1]) <= 0.01, (name, it["id"], m)
                n += 1
    assert n >= 10


def test_pixels_mirrored_pairs_share_mirrored_margins(directed):
    for name in ("synth_dinamico", "synth_energetico"):
        for a, b in _pairs(directed[name]):
            ma, mb = _ink_x(_draw("graphics", a)), _ink_x(_draw("graphics", b))
            assert ma and mb
            # el aire izquierdo de uno es el derecho del otro (espejo)
            assert abs(ma[0] - mb[1]) <= 0.01 and abs(ma[1] - mb[0]) <= 0.01, (name, a["id"], ma, mb)


# ---------------------------------------------------------------- symmetrize (planes existentes)
def _base(**kw):
    d = {"enabled": True, "src": "ai", "locked": False, "intensity": 1.0}
    d.update(kw)
    return d


def _mini_plan() -> dict:
    """Plan v2 ficticio de 12 s: primer plano extremo (0-6 s: la cara llena el centro) y b-roll sin
    persona (6-12 s), con los items asimetricos que dejaba el director viejo."""
    phrases, t = [], 0.3
    for txt in ("Hola esta es una prueba de simetria.", "Todo debe quedar centrado en la pantalla.",
                "Cada acento se refleja o se centra."):
        ws, t = TD._phrase(txt, t)
        t += 0.4
        for k in range(0, len(ws), 3):
            phrases.append(ws[k:k + 3])
    shots = [{"start": 0.0, "end": 6.0, "person": True, "framing": "primer_plano", "place": "estudio",
              "text_seen": [], "use": "a_camara", "free_space": "abajo", "face": [0.5, 0.36, 0.6, 0.46]},
             {"start": 6.0, "end": 12.0, "person": False, "framing": "sin_persona", "place": "calle",
              "text_seen": [], "use": "broll", "free_space": "arriba", "face": None}]
    fx = {tr: [] for tr in D.TRACKS}
    fx["text"] = [
        _base(id="tx_title_s0", preset="cascade", lines=[{"text": "Una prueba", "scale": 1.0},
                                                          {"text": "de simetria", "scale": 1.15}],
              x=0.1, y=0.72, size=0.07, align="left", font="Poppins-Bold", color="#FFFFFF",
              anim_in="word_stagger", anim_out="fade", start=1.0, end=3.0),
        _base(id="tx_price_s1", preset="counter", **{"from": 0, "to": 500}, prefix="$", text="MXN",
              x=0.5, y=0.4, size=0.2, anchor="right", start=10.5, end=11.1),
    ]
    fx["graphics"] = [
        _base(id="gr_doodle_s0", kind="doodle", name="sparkle", x=0.2, y=0.3, size=0.1, draw=True,
              color="#FFD23F", rotation=8.0, anim_in="draw", anim_out="fade", start=2.0, end=3.2),
        _base(id="gr_doodle_s2", kind="doodle", name="sparkle", x=0.7, y=0.2, size=0.1, draw=True,
              color="#FFFFFF", border_color="#161616", rotation=10.0, anim_in="draw", anim_out="fade",
              start=6.5, end=8.0),
        _base(id="gr_doodle_s2b", kind="doodle", name="question", x=0.3, y=0.62, size=0.11, draw=True,
              color="#FFD23F", rotation=-6.0, anim_in="draw", anim_out="fade", start=8.8, end=10.2),
        _base(id="gr_icon_s2", kind="icon", name="star", x=0.78, y=0.2, size=0.12, badge="circle",
              badge_color="#FFD23F", color="#111111", anim_in="pop", anim_out="pop_out", start=9.0, end=10.4),
        _base(id="gr_location_s2", kind="card", variant="location", title="Calle Ficticia 1", subtitle="",
              x=0.47, y=0.5, size=0.8, theme="light", anim_in="rise", anim_out="fade", start=6.2, end=8.2),
        _base(id="gr_arrow_s2", kind="doodle", name="arrow_curve", x=0.85, y=0.3, size=0.14,
              path_to=[0.75, 0.38], draw=True, color="#FFD23F", anim_in="draw", anim_out="fade",
              start=6.6, end=8.2),
        _base(id="gr_cta_s2", kind="cta", variant="follow", label="Sigueme", handle="@demo.sym", x=0.47,
              y=0.62, size=0.78, width=0.8, anim_in="pop", anim_out="fade", start=10.3, end=12.0),
        _base(id="gr_name_s1", kind="lower_third", title="Ana Demo", subtitle="Prueba", x=0.07, y=0.66,
              size=0.064, theme="dark", anim_in="draw", anim_out="undraw", start=3.5, end=5.8),
        _base(id="gr_watermark_s0", kind="socials", variant="watermark",
              links=[{"platform": "instagram", "text": "@demo.sym"}], size=0.03, layout="row", x=0.15,
              y=0.11, backdrop=0.34, opacity=0.8, anim_in="fade", anim_out="fade", start=0.0, end=12.0),
        # lo que NUNCA se mueve: bloqueado, del usuario y en director.locked
        _base(id="gr_doodle_s1", kind="doodle", name="heart", x=0.3, y=0.25, size=0.1, locked=True,
              start=4.0, end=5.0),
        _base(id="gr_mio", kind="doodle", name="question", x=0.2, y=0.55, size=0.1, src="user",
              start=9.0, end=10.0),
        _base(id="gr_doodle_s1b", kind="doodle", name="star4", x=0.75, y=0.45, size=0.09,
              start=3.2, end=4.2),
    ]
    fx["cutaways"] = [
        _base(id="cw_flash_s0", kind="title_card", background="gradient", start=0.2, end=0.9,
              text={"preset": "stack", "text": "PRUEBA", "lines": [{"text": "PRUEBA", "scale": 1.0}],
                    "x": 0.47, "y": 0.5, "size": 0.14, "align": "left"},
              graphics=[{"kind": "doodle", "name": "sparkle", "x": 0.8, "y": 0.3, "size": 0.1, "color": "#FFD23F"},
                        {"kind": "doodle", "name": "star4", "x": 0.19, "y": 0.62, "size": 0.07, "color": "#FFFFFF"},
                        {"kind": "doodle", "name": "sparkle", "x": 0.78, "y": 0.72, "size": 0.1},
                        {"kind": "icon", "name": "star", "x": 0.3, "y": 0.2, "size": 0.2}]),
        _base(id="cw_location_s2", kind="info_card", background="blur_self", start=11.2, end=11.9,
              card={"kind": "card", "variant": "location", "title": "Calle Ficticia 1", "x": 0.47, "y": 0.5,
                    "size": 0.8}),
    ]
    fx["sfx"] = [
        _base(id="sf_sparkle_s0", name="sparkle", at=2.03, gain_db=-8.0, pan=-0.3),
        _base(id="sf_sparkle_s2", name="sparkle", at=6.53, gain_db=-8.0, pan=0.25),
    ]
    fx["captions"] = {"enabled": True, "preset": "clean_bold", "y": 0.72, "size": 0.05, "x": 0.47}
    fx["music"] = {"enabled": False, "source": "ai"}
    fx["grade"] = {"look": "natural"}
    return {"stem": "sym", "video": "", "version": 2, "duration": 12.0, "dims": [1080, 1920],
            "phrases": phrases, "cuts": [], "shots": shots, "entities": {}, "fx": fx,
            "director": {"style": "dinamico", "intensity": 0.75, "seed": 7, "rev": 0,
                         "locked": ["gr_doodle_s1b"], "notes": []}}


@pytest.fixture(scope="module")
def mini():
    p = _mini_plan()
    return p, D.symmetrize(p)


def _g(plan, iid):
    return next((g for g in plan["fx"]["graphics"] if g["id"] == iid), None)


def test_symmetrize_centers_structural_items(mini):
    p, q = mini
    assert p == _mini_plan()                                    # no modifica la entrada
    card, cta, lt = _g(q, "gr_location_s2"), _g(q, "gr_cta_s2"), _g(q, "gr_name_s1")
    assert card["x"] == 0.5 and card["size"] == C.SYM_W
    assert cta["x"] == 0.5 and cta["width"] == C.SYM_W
    assert lt["x"] == 0.5 and lt["align"] == "center"
    tx = {t["id"]: t for t in q["fx"]["text"]}
    assert tx["tx_title_s0"]["x"] == 0.5 and tx["tx_title_s0"]["align"] == "center"
    assert tx["tx_price_s1"]["anchor"] == "center"
    assert q["fx"]["captions"]["x"] == 0.5
    # mismos tiempos y contenido
    for iid in ("gr_location_s2", "gr_cta_s2", "gr_name_s1"):
        a, b = _g(p, iid), _g(q, iid)
        assert (a["start"], a["end"]) == (b["start"], b["end"])
        assert {k: v for k, v in a.items() if k not in ("x", "size", "width", "align")} == \
            {k: v for k, v in b.items() if k not in ("x", "size", "width", "align")}


def test_symmetrize_accents(mini):
    p, q = mini
    # destello de un lado sobre el b-roll (nada estorba): su gemelo en el lugar espejo
    s, m = _g(q, "gr_doodle_s2"), _g(q, "gr_doodle_s2_m")
    assert s is not None and m is not None and _pair_ok(s, m) is None
    assert s["x"] == 0.7 and m["x"] == 0.3 and m["border_color"] == s["border_color"]
    assert [x for x in q["fx"]["sfx"] if x["id"] == "sf_sparkle_s2"][0]["pan"] == 0.0      # un SFX, al centro
    # '?' (glifo con mano) e icono: solo centrados, nunca en par
    qm, ic = _g(q, "gr_doodle_s2b"), _g(q, "gr_icon_s2")
    for g in (qm, ic):
        assert g is None or g["x"] == 0.5
    assert qm is not None and ic is not None
    assert _g(q, "gr_doodle_s2b_m") is None and _g(q, "gr_icon_s2_m") is None
    assert qm["rotation"] == 0.0
    # flecha curva a un lado: vertical y centrada, llega por arriba a la tarjeta que senala
    ar, card = _g(q, "gr_arrow_s2"), _g(q, "gr_location_s2")
    assert ar is not None and ar["x"] == 0.5 and ar["path_to"][0] == 0.5 and ar["bend"] == 0.0
    assert ar["name"] == "arrow_straight" and ar["y"] < ar["path_to"][1] < card["y"]
    # destello sobre el primer plano (la cara llena el centro): no cabe simetrico -> fuera con su SFX
    assert _g(q, "gr_doodle_s0") is None and _g(q, "gr_doodle_s0_m") is None
    assert not [x for x in q["fx"]["sfx"] if x["id"] == "sf_sparkle_s0"]
    assert any(n.startswith("simetria:") and "gr_doodle_s0" in n for n in q["director"]["notes"])


def test_symmetrize_never_moves_user_or_locked(mini):
    p, q = mini
    for iid in ("gr_doodle_s1", "gr_mio", "gr_doodle_s1b"):
        assert _g(q, iid) == _g(p, iid), iid
        assert _g(q, iid + "_m") is None


def test_symmetrize_cutaways(mini):
    p, q = mini
    fl = next(c for c in q["fx"]["cutaways"] if c["id"] == "cw_flash_s0")
    assert fl["text"]["x"] == 0.5 and fl["text"]["align"] == "center"
    gr = fl["graphics"]
    dd = [g for g in gr if g["kind"] == "doodle"]
    assert len(dd) == 4                                         # dos pares espejo
    for g in dd:
        assert _accent_violation(g, gr) is None, (g, _accent_violation(g, gr))
    assert [g["x"] for g in gr if g["kind"] == "icon"] == [0.5]
    loc = next(c for c in q["fx"]["cutaways"] if c["id"] == "cw_location_s2")
    assert loc["card"]["x"] == 0.5 and loc["card"]["size"] == C.SYM_W


def test_symmetrize_watermark_top_center(mini):
    p, q = mini
    wm = _g(q, "gr_watermark_s0")
    assert wm is not None and wm["x"] == 0.5 and wm["y"] == 0.11
    # el primer plano ocupa el centro de arriba: solo en el b-roll
    assert wm["start"] >= 6.0 and wm["end"] - wm["start"] >= 2.0


def test_symmetrize_result_passes_the_invariant_and_is_idempotent(mini):
    p, q = mini
    assert not _violations(q), _violations(q)
    assert D.symmetrize(q)["fx"] == q["fx"]                     # idempotente
    assert D.symmetrize(p) == q                                 # determinista
    TD._check_catalog(q)


def test_symmetrize_is_a_noop_on_directed_plans(directed):
    for name in ("synth_dinamico", "prof_broll", "closeup"):
        p = directed[name]
        assert D.symmetrize(p)["fx"] == p["fx"], name


def test_symmetrize_twin_follows_a_user_fixed_or_disabled_base():
    """El usuario movio (y con eso bloqueo) o apago un miembro de un par: el gemelo de la IA lo
    sigue en espejo (o se va si la base quedo al centro); la base no se toca."""
    p = _mini_plan()
    base = _base(id="gr_doodle_s2c", kind="doodle", name="star4", x=0.82, y=0.35, size=0.09, color="#FFD23F",
                 rotation=6.0, start=9.0, end=10.0, locked=True)
    twin = _base(id="gr_doodle_s2c_m", kind="doodle", name="star4", x=0.3, y=0.4, size=0.09, color="#FFD23F",
                 rotation=-6.0, start=9.0, end=10.0)
    off = _base(id="gr_doodle_s2d", kind="doodle", name="sparkle", x=0.75, y=0.7, size=0.1, enabled=False,
                start=7.0, end=8.0)
    off_m = _base(id="gr_doodle_s2d_m", kind="doodle", name="sparkle", x=0.25, y=0.7, size=0.1, start=7.0, end=8.0)
    mid = _base(id="gr_doodle_s2e", kind="doodle", name="burst", x=0.5, y=0.3, size=0.1, src="user",
                start=10.5, end=11.5)
    mid_m = _base(id="gr_doodle_s2e_m", kind="doodle", name="burst", x=0.3, y=0.3, size=0.1, start=10.5, end=11.5)
    p["fx"]["graphics"] += [base, twin, off, off_m, mid, mid_m]
    q = D.symmetrize(p)
    assert _g(q, "gr_doodle_s2c") == base
    t = _g(q, "gr_doodle_s2c_m")
    assert t["x"] == 0.18 and t["y"] == 0.35 and t["rotation"] == -6.0 and t["src"] == "ai" and not t["locked"]
    assert _g(q, "gr_doodle_s2d")["enabled"] is False and _g(q, "gr_doodle_s2d_m")["enabled"] is False
    assert _g(q, "gr_doodle_s2e") == mid and _g(q, "gr_doodle_s2e_m") is None


def test_symmetrize_edge_cases():
    """Marcas casi centradas (se ajustan) o sobre una linea de lado (fuera), flecha centrada pero
    curva o girada (se endereza), par demasiado junto (queda uno centrado), gemelo fijado por el
    usuario (el par se queda), PiP con x propia (al colocador automatico) y escena con flecha y
    contador de lado."""
    p = _mini_plan()
    g = p["fx"]["graphics"]
    g += [
        _base(id="gr_mark_s2", kind="doodle", name="underline", x=0.52, y=0.75, size=0.4, start=7.0, end=8.0),
        _base(id="gr_mark_s2b", kind="doodle", name="circle_scribble", x=0.3, y=0.75, size=0.2, start=9.5, end=10.3),
        _base(id="gr_arrow_s2b", kind="doodle", name="arrow_curve", x=0.5, y=0.2, path_to=[0.5, 0.3], size=0.14,
              bend=0.3, rotation=5.0, start=10.5, end=11.0),
        _base(id="gr_doodle_s2f", kind="doodle", name="sparkle", x=0.55, y=0.45, size=0.08, start=10.4, end=11.1),
        _base(id="gr_doodle_s2f_m", kind="doodle", name="sparkle", x=0.45, y=0.45, size=0.08, start=10.4, end=11.1),
        _base(id="gr_doodle_s2g", kind="doodle", name="burst", x=0.8, y=0.7, size=0.1, start=6.2, end=6.9),
        _base(id="gr_doodle_s2g_m", kind="doodle", name="burst", x=0.25, y=0.7, size=0.1, start=6.2, end=6.9,
              locked=True),
        _base(id="gr_icon_s2c", kind="icon", name="star", x=0.501, y=0.3, size=0.1, start=11.2, end=11.8),
        _base(id="gr_media_s2", kind="media", x=0.3, y=0.3, size=0.4, start=6.0, end=7.0),
    ]
    p["fx"]["cutaways"].append(
        _base(id="cw_price_s1", kind="title_card", background="gradient", start=5.0, end=5.8,
              text={"preset": "counter", "from": 0, "to": 90, "x": 0.5, "y": 0.5, "size": 0.2, "anchor": "left"},
              graphics=[{"kind": "doodle", "name": "arrow_straight", "x": 0.7, "y": 0.3, "path_to": [0.6, 0.4],
                         "size": 0.12}]))
    q = D.symmetrize(p)
    assert _g(q, "gr_mark_s2")["x"] == 0.5 and _g(q, "gr_mark_s2b") is None
    ar = _g(q, "gr_arrow_s2b")
    assert ar["x"] == 0.5 and ar["bend"] == 0.0 and ar["rotation"] == 0.0 and ar["path_to"] == [0.5, 0.3]
    # par demasiado junto (0.45 / 0.55): se deshace y el destello vuelve a colocarse simetrico
    sf = _g(q, "gr_doodle_s2f")
    assert sf is None or _accent_violation(sf, q["fx"]["graphics"]) is None
    assert sf is None or abs(sf["x"] - 0.5) < 1e-9 or abs(sf["x"] - 0.5) >= D.ACCENT_SEP / 2
    assert _g(q, "gr_doodle_s2g") == _g(p, "gr_doodle_s2g") and _g(q, "gr_doodle_s2g_m") == _g(p, "gr_doodle_s2g_m")
    assert _g(q, "gr_icon_s2c")["x"] == 0.5 and _g(q, "gr_icon_s2c")["y"] == 0.3
    md = _g(q, "gr_media_s2")
    assert md["x"] is None and md["y"] is None
    cw = next(c for c in q["fx"]["cutaways"] if c["id"] == "cw_price_s1")
    assert cw["text"]["anchor"] == "center"
    assert cw["graphics"][0]["x"] == 0.5 and cw["graphics"][0]["path_to"][0] == 0.5 and cw["graphics"][0]["bend"] == 0.0


def test_pairs_enter_and_leave_together():
    """Durante la coreografia un par espejo entra completo (base + '<id>_m', una sola peticion de
    SFX, al centro) y se quita completo (con su SFX)."""
    p = TD.synthetic_plan()
    ch = D._Choreo(p, p["shots"], D.analyze_script(p, use_llm=False), "dinamico", 0.75, 7)
    f = {"start": 16.0, "end": 17.5, "kind": "doodle", "name": "sparkle", "y": 0.3, "size": 0.1, "draw": True,
         "anim_in": "draw", "anim_out": "fade"}
    a = ch.add_pair("beat", f, 0.3, 12.0)                      # la x de un lado basta: la base va a la derecha
    assert a is not None and a["x"] == 0.7 and a["rotation"] == 12.0
    tw = [g for g in ch.fx["graphics"] if g["id"] == a["id"] + "_m"][0]
    assert _pair_ok(a, tw) is None and tw["x"] == 0.3
    ch._doodle_sfx("sparkle", 16.03, 0.5, a["id"], pair=True)
    reqs = [r for r in ch.sfx_req if r["src"] == a["id"]]
    assert len(reqs) == 1 and reqs[0]["pan"] == 0.0
    assert ch.add_pair("beat", dict(f, start=16.2), 0.55) is None           # demasiado juntos
    ch._unplace(tw)
    assert not ch.fx["graphics"] and not [r for r in ch.sfx_req if r["src"].startswith(a["id"])]
    assert a["id"] not in ch.ids and a["id"] + "_m" not in ch.acc_pos


def test_float_accent_icon_is_centered_or_absent():
    """Acento de ritmo con icono (tiene mano): solo centrado sobre la cabeza, nunca a un lado."""
    p = TD.synthetic_plan()
    ch = D._Choreo(p, p["shots"], D.analyze_script(p, use_llm=False), "dinamico", 0.75, 7)
    got = 0
    for w in ch.words[::4]:
        g = ch._float_accent(w, ch.shot_at(w["t0"]), icon_only=True, icon="star")
        if g is not None:
            assert g["x"] == 0.5 and g["kind"] == "icon"
            got += 1
    for w in ch.words[1::4]:
        g = ch._float_accent(w, ch.shot_at(w["t0"]))
        if g is not None:
            tw = [o for o in ch.fx["graphics"] if o["id"] == g["id"] + "_m"]
            assert g["x"] == 0.5 or (tw and _pair_ok(g, tw[0]) is None), g
    assert got


def test_symmetrize_without_script_or_words():
    """Plan sin guion guardado ni palabras (solo tomas y fx): igual queda simetrico."""
    p = _mini_plan()
    p["phrases"] = []
    q = D.symmetrize(p)
    assert not _violations(q), _violations(q)


def test_director_emits_no_asymmetric_constant():
    """Nada de 0.47 / 0.07 / 0.78 como x en el director (la pila, el rotulo y los acentos van en 0.5
    o en par espejo)."""
    assert not hasattr(D._Choreo, "XS")
    assert D._Choreo.XC == 0.5 and D._Choreo.MAXW == C.SYM_W
    assert D.SYM_L == pytest.approx(1 - SAFE["right"]) and D.SYM_R == pytest.approx(SAFE["right"])
    assert D.lt_sub_fits("Odontologa", 0.064) and not D.lt_sub_fits("X" * 80, 0.064)
    assert D.lt_width("Ana", "Rol", 0.064) < D.lt_width("Nombre Largo De Prueba", "Rol Largo De Prueba", 0.064) \
        <= C.SYM_W


def test_symmetrize_arrow_moved_stays_legible():
    """La flecha que pasa de un lado al centro cae sobre otro fondo (p. ej. la ropa): si el color
    elegido para su lugar viejo no se lee ahi, pasa a blanco con contorno; si se lee, no cambia."""
    for rgb, legible in (([72, 78, 56], False), ([236, 234, 228], True)):        # oliva (ropa) / pared clara
        p = _mini_plan()
        p["shots"][1]["rows"] = [list(rgb)] * 16
        ar0 = _g(p, "gr_arrow_s2")
        ar0["color"] = "#23847D"
        q = D.symmetrize(p)
        ar = _g(q, "gr_arrow_s2")
        assert ar is not None and ar["x"] == 0.5 and ar["path_to"][0] == 0.5
        if legible:
            assert ar["color"] == "#23847D" and "border_color" not in ar
        else:
            assert ar["color"] == "#FFFFFF" and ar["border_color"] == "#161616"
        assert D.symmetrize(q) == q
