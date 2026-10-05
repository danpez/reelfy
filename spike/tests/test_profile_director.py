"""El perfil de la persona en la edicion (docs/MOTION-ENGINE.md 3.2, 4.2b y 5): director, esquema,
graficos (chips de redes, Seguir con red y foto, reservas) y render (foto de perfil opcional).

Solo datos FICTICIOS (persona, usuarios, paginas y telefono inventados) y videos sinteticos.
"""
import copy
import json
import time

import numpy as np
import pytest

from conftest import load_factor
from motion import ctx as C
from motion import director as D
from motion import graphics as G
from motion import profile as PR
from motion import schema as S
from motion.ctx import SAFE
import test_director as TD

TEXT = ("Ella es la Dra. Lucia Fernandez, es odontologa, su instagram es https://instagram.com/dra.lucia.demo "
        "y agenda en https://paseo.club/business/dra-lucia-demo")
PROF = PR.parse_profile_text(TEXT, use_llm=False)["profile"]


def _prof(**kw):
    p = copy.deepcopy(PROF)
    p.update(kw)
    return PR.normalize_profile(p)


def _tl(p):
    return D._timeline(p)


def _items(p, kind, variant=None):
    return [g for g in p["fx"]["graphics"] if g["kind"] == kind and (variant is None or g.get("variant") == variant)]


def _lines_plan(lines, broll_end=0.0):
    """Plan v1 ficticio con una frase por linea (dos tomas a camara y, si se pide, b-roll final)."""
    phrases, t = [], 0.4
    for txt in lines:
        ws, t_end = TD._phrase(txt, t)
        t = t_end + 0.55
        for k in range(0, len(ws), 3):
            phrases.append(ws[k:k + 3])
    talk_end = phrases[-1][-1]["end"] + 0.9
    dur = round(talk_end + broll_end, 3)
    mid = round(talk_end / 2, 3)
    cam = {"person": True, "framing": "medio", "place": "consultorio", "text_seen": [], "use": "a_camara",
           "free_space": "arriba"}
    shots = [dict(cam, start=0.0, end=mid, face=[0.5, 0.33, 0.14, 0.11]),
             dict(cam, start=mid, end=round(talk_end, 3), face=[0.47, 0.34, 0.15, 0.12])]
    if broll_end:
        shots.append({"start": round(talk_end, 3), "end": dur, "person": False, "framing": "sin_persona",
                      "place": "fachada", "text_seen": [], "use": "broll", "free_space": "arriba", "face": None})
    return {"stem": "sintetico", "video": "", "duration": dur, "dims": [1080, 1920], "phrases": phrases,
            "cuts": [], "beats": [], "camera": [], "highlights": [], "shots": shots}


def _direct(p, prof=None, style="dinamico"):
    p = copy.deepcopy(p)
    if prof is not None:
        p["profile"] = prof
    return D.direct(p, style=style, intensity=0.75, seed=7, use_llm=False)


@pytest.fixture(scope="module")
def synth_prof():
    return _direct(TD.synthetic_plan(), PROF)


# ---------------------------------------------------------------- director: rotulo
def test_lower_third_uses_the_profile_name_and_title(synth_prof):
    p = synth_prof
    lt = _items(p, "lower_third")
    assert len(lt) == 1
    assert lt[0]["title"] == "Dra. Lucía Fernández" and lt[0]["subtitle"] == "Odontóloga"
    ent = p["entities"]
    assert ent["name"] == "Dra. Lucía Fernández" and ent["name_src"] == "perfil" and ent["name_confirmed"]
    assert ent["role"] == "Odontóloga" and ent["handle"] == "@dra.lucia.demo"
    assert p["profile"]["links"][0]["platform"] == "instagram"            # normalize lo conserva
    assert any(n.startswith("perfil: Dra. Lucía Fernández") for n in p["director"]["notes"])
    TD._check_catalog(p)
    TD._check_safe(p)
    # el rotulo llega temprano (antes de la mitad del video) y sin titulos en su franja
    tl = _tl(p)
    a, b = tl.remap_clamped(lt[0]["start"]), tl.remap_clamped(lt[0]["end"])
    assert a < tl.final_duration * 0.5 and b - a >= 2.0 - 1e-3
    for it in p["fx"]["text"]:
        ia, ib = tl.remap_clamped(it["start"]), tl.remap_clamped(it["end"])
        if ia < b - 1e-3 and a < ib - 1e-3:
            assert D.zone_of("text", it) == "top", it["id"]


def test_without_profile_the_role_is_still_used():
    p = _direct(TD.synthetic_plan())
    assert "profile" not in p and p["entities"].get("name_src") != "perfil"
    assert all(g.get("title") != "Dra. Lucía Fernández" for g in _items(p, "lower_third"))


def test_lower_third_enters_when_the_name_is_said():
    lines = ["Si te duele la muela cuando tomas algo frio, esto te interesa.",
             "Una resina bien hecha protege tu diente y se ve natural.",
             "Hola, soy la doctora Lucia Fernandez y hoy te explico todo.",
             "La limpieza profunda quita el sarro y deja tus dientes brillantes.",
             "Sigueme para mas consejos de tu sonrisa."]
    p = _direct(_lines_plan(lines), PROF)
    words = D._flatten_words(p)
    said = next(w for w in words if D.norm(w["text"]) == "lucia")
    lt = _items(p, "lower_third")[0]
    tl = _tl(p)
    assert abs(tl.remap_clamped(lt["start"]) - tl.remap_clamped(said["t0"])) <= 0.9, (lt["start"], said["t0"])
    assert lt["title"] == "Dra. Lucía Fernández"


def test_sign_name_is_never_used_with_a_profile():
    p = TD.synthetic_plan()
    p["entities"] = {"name": "Dra. Paula Letrero", "name_src": "letrero", "name_confirmed": True,
                     "name_candidate": "Dra. Paula Letrero"}
    out = _direct(p, PROF)
    blob = json.dumps(out["fx"], ensure_ascii=False) + json.dumps(out["entities"], ensure_ascii=False)
    assert "Letrero" not in blob
    # perfil SIN nombre (solo redes): tampoco se usa el nombre del letrero
    solo = PR.normalize_profile({"links": ["instagram.com/clinica.demo"], "title": "Odontóloga"})
    out2 = _direct(p, solo)
    assert "Letrero" not in json.dumps(out2["fx"], ensure_ascii=False)
    assert out2["entities"]["handle"] == "@clinica.demo"


# ---------------------------------------------------------------- director: Seguir y reservas
def test_follow_cta_has_name_platform_handle_and_initials(synth_prof):
    p = synth_prof
    tl = _tl(p)
    fol = _items(p, "cta", "follow")
    assert fol
    f = fol[-1]
    assert f["title"] == "Dra. Lucía Fernández" and f["handle"] == "@dra.lucia.demo"
    assert f["platform"] == "instagram" and f["initials"] == "LF"
    assert abs(tl.remap_clamped(f["end"]) - tl.final_duration) < 1e-3      # hasta el ultimo cuadro
    assert "media" not in f                                               # sin foto: iniciales


def test_follow_cta_uses_the_profile_photo():
    prof = _prof(avatar={"sid": "foto_perfil_demo", "type": "image", "w": 600, "h": 600})
    p = _direct(TD.synthetic_plan(), prof)
    f = _items(p, "cta", "follow")[-1]
    assert f["avatar"] == "image" and f["media"]["sid"] == "foto_perfil_demo"
    assert S.validate(p) == [] or all("falta 'video'" in e for e in S.validate(p))


def test_booking_cta_when_the_voice_says_link_below():
    lines = ["Si te duele la muela cuando tomas algo frio, esto te interesa.",
             "Una resina bien hecha protege tu diente y se ve natural.",
             "Checa mis horarios disponibles en el link aqui abajo.",
             "La limpieza profunda quita el sarro y deja tus dientes brillantes.",
             "Y no te preocupes, no te voy a juzgar por tu cepillo viejo."]
    p = _direct(_lines_plan(lines), PROF)
    words = D._flatten_words(p)
    link_w = next(w for w in words if D.norm(w["text"]) == "link")
    tl = _tl(p)
    links = [g for g in _items(p, "cta", "link_below")
             if abs(tl.remap_clamped(g["start"]) - tl.remap_clamped(link_w["t0"])) < 0.6]
    assert links, D.edit_table(p)
    l = links[0]
    assert l["label"] == "Agenda en PaseoClub" and l["display"] == "paseo.club/dra-lucia-demo"
    assert l["platform"] == "paseoclub" and l["icon"] == "calendar-event"
    # la tarjeta de horarios dice DONDE (sin repetir el enlace)
    sched = [g for g in p["fx"]["graphics"] if g["kind"] == "card" and g.get("variant") == "schedule"]
    assert sched and sched[0]["subtitle"] == "En PaseoClub"
    TD._check_catalog(p)


def test_booking_cta_on_agenda_words_only_with_a_profile():
    lines = ["Si te duele la muela cuando tomas algo frio, esto te interesa.",
             "Una resina bien hecha protege tu diente y se ve natural.",
             "Agenda tu cita esta semana y te atiendo con gusto.",
             "La limpieza profunda quita el sarro y deja tus dientes brillantes.",
             "Y no te preocupes, no te voy a juzgar por tu cepillo viejo."]
    base = _lines_plan(lines)
    without = _direct(base)
    tl0 = _tl(without)
    agenda = next(w for w in D._flatten_words(without) if D.norm(w["text"]) == "agenda")
    early = [g for g in _items(without, "cta", "link_below")
             if tl0.remap_clamped(g["start"]) < tl0.remap_clamped(agenda["t1"]) + 1.0]
    assert not early                                  # sin 'link' ni perfil no hay pastilla ahi
    withp = _direct(base, PROF)
    tl = _tl(withp)
    near = [g for g in _items(withp, "cta", "link_below")
            if abs(tl.remap_clamped(g["start"]) - tl.remap_clamped(agenda["t0"])) < 0.6]
    assert near and near[0]["label"] == "Agenda en PaseoClub"


def test_end_card_with_social_row_booking_and_location():
    prof = PR.normalize_profile({
        "name": "Dra. Lucía Fernández", "title": "Odontóloga", "address": "Calle Olmo 8",
        "links": ["instagram.com/dra.lucia.demo", "tiktok.com/@dra.lucia.tt", "wa.me/5215512345678",
                  "https://paseo.club/business/dra-lucia-demo"]})
    lines = ["Si te duele la muela cuando tomas algo frio, esto te interesa.",
             "Una resina bien hecha protege tu diente y se ve natural.",
             "La limpieza profunda quita el sarro y deja tus dientes brillantes.",
             "Te espero en el consultorio."]
    p = _direct(_lines_plan(lines, broll_end=2.6), prof)
    tl = _tl(p)
    Dur = tl.final_duration
    closing = [g for g in p["fx"]["graphics"] if abs(tl.remap_clamped(g["end"]) - Dur) < 1e-3
               and g["kind"] in ("cta", "socials", "card")]
    kinds = {(g["kind"], g.get("variant")) for g in closing}
    assert ("cta", "follow") in kinds and ("cta", "link_below") in kinds, D.edit_table(p)
    soc = [g for g in closing if g["kind"] == "socials"]
    assert soc, D.edit_table(p)
    chips = [(l["platform"], l["text"]) for l in soc[0]["links"]]
    assert chips == [("tiktok", "@dra.lucia.tt"), ("whatsapp", "+52 55 1234 5678")]   # sin repetir IG
    link = [g for g in closing if g.get("variant") == "link_below"][0]
    assert link["label"] == "Agenda en PaseoClub" and link["display"] == "paseo.club/dra-lucia-demo"
    loc = [g for g in closing if g["kind"] == "card" and g.get("variant") == "location"]
    assert loc and loc[0]["title"] == "Calle Olmo 8"
    # la pila del cierre no se encima: follow arriba, chips, y la pastilla de reservas abajo
    ys = {("socials" if g["kind"] == "socials" else g.get("variant")): g["y"] for g in closing}
    assert ys["location"] < ys["follow"] < ys["socials"] < ys["link_below"]
    TD._check_catalog(p)
    TD._check_safe(p)


def test_use_flags_turn_pieces_off_and_watermark_on():
    prof = _prof(use={"lower_third": False, "follow_cta": False, "booking_cta": True, "end_card": True,
                      "handle_watermark": True})
    p = _direct(TD.synthetic_plan(), prof)
    assert not _items(p, "lower_third")
    assert not _items(p, "cta", "follow")
    wm = [g for g in _items(p, "socials") if g.get("variant") == "watermark"]
    assert wm and wm[0]["start"] <= 0.31                         # desde el primer cuadro conservado
    tl = _tl(p)
    shown = sum(tl.remap_clamped(g["end"]) - tl.remap_clamped(g["start"]) for g in wm)
    assert shown >= 0.4 * tl.final_duration, shown
    for g in wm:
        assert g["links"] == [{"platform": "instagram", "text": "@dra.lucia.demo"}]
        assert SAFE["left"] <= g["x"] <= SAFE["right"] and g["y"] < 0.2 and g.get("backdrop", 0) > 0.2
        assert D.zone_of("graphics", g) == "edge"
        assert tl.remap_clamped(g["end"]) - tl.remap_clamped(g["start"]) >= 2.0 - 1e-3
        # la esquina de la marca de agua nunca coincide con un titulo arriba ni con una escena
        ga, gb = tl.remap_clamped(g["start"]), tl.remap_clamped(g["end"])
        for t in p["fx"]["text"]:
            ta, tb = tl.remap_clamped(t["start"]), tl.remap_clamped(t["end"])
            if ta < gb - 0.05 and ga < tb - 0.05:
                box = D.measure_text(t)
                assert box is None or box[1] > g["y"] + 0.03, (g["id"], t["id"])
        for c in p["fx"]["cutaways"]:
            ca, cb = tl.remap_clamped(c["start"]), tl.remap_clamped(c["end"])
            assert not (ca < gb - 0.05 and ga < cb - 0.05), (g["id"], c["id"])
    off = _direct(TD.synthetic_plan(), _prof(use={"booking_cta": False, "end_card": False}))
    assert all(not g.get("display") for g in _items(off, "cta", "link_below"))
    assert not _items(off, "socials")


# ---------------------------------------------------------------- redirect / apply_profile
def test_apply_profile_updates_instantly_and_keeps_the_rest(synth_prof):
    p = synth_prof
    other = PR.normalize_profile({"name": "Dr. Tomás Ejemplo", "title": "Ortodoncista",
                                  "links": ["tiktok.com/@dr.tomas.demo", "calendly.com/dr-tomas-demo/cita"]})
    t0 = time.time()
    q = D.apply_profile(p, other)
    dt = time.time() - t0
    assert dt < 3.0 * load_factor(), dt
    lt = _items(q, "lower_third")[0]
    assert lt["title"] == "Dr. Tomás Ejemplo" and lt["subtitle"] == "Ortodoncista"
    f = _items(q, "cta", "follow")[-1]
    assert f["handle"] == "@dr.tomas.demo" and f["platform"] == "tiktok" and f["initials"] == "TE"
    assert all("Lucía" not in json.dumps(g, ensure_ascii=False) for g in q["fx"]["graphics"])
    assert q["entities"]["name"] == "Dr. Tomás Ejemplo" and q["entities"]["link_label"] == "Agenda en Calendly"
    # misma revision: los titulos (lo que no depende del perfil) quedan igual
    assert q["director"]["rev"] == p["director"].get("rev", 0)
    txt = lambda pl: [(t["id"], t.get("text") or [ln["text"] for ln in t.get("lines") or []]) for t in pl["fx"]["text"]]
    assert txt(q) == txt(p)
    # quitar el perfil: vuelve al rol de la voz y a las etiquetas genericas
    r = D.apply_profile(q, None)
    assert "profile" not in r and r["entities"].get("name_src") != "perfil"
    assert "profile_keys" not in r["entities"]
    assert all("Tomás" not in json.dumps(g, ensure_ascii=False) for g in r["fx"]["graphics"])
    assert r["entities"]["link_label"] != "Agenda en Calendly"


def test_redirect_style_change_keeps_the_profile(synth_prof):
    q = D.redirect(synth_prof, style="elegante")
    assert q["profile"]["name"] == "Dra. Lucía Fernández"
    assert _items(q, "lower_third")[0]["title"] == "Dra. Lucía Fernández"
    assert _items(q, "cta", "follow")[-1]["handle"] == "@dra.lucia.demo"


def test_regenerate_follow_keeps_profile_fields(synth_prof):
    f = _items(synth_prof, "cta", "follow")[-1]
    q = D.redirect(synth_prof, only_ids=[f["id"]], seed=11)
    g = next(x for x in q["fx"]["graphics"] if x["id"] == f["id"])
    assert g["title"] == f["title"] and g["handle"] == f["handle"] and g["platform"] == "instagram"


def test_profile_does_not_change_the_music_track(synth_prof):
    base = _direct(TD.synthetic_plan())
    assert base["fx"]["music"]["prompt"] == synth_prof["fx"]["music"]["prompt"]


# ---------------------------------------------------------------- esquema
def test_schema_publishes_the_profile_kinds_and_params():
    sch = S.schema()
    gr = sch["tracks"]["graphics"]["items"]
    assert "socials" in gr
    keys = {q["k"] for q in gr["socials"]["params"]}
    assert {"links", "layout", "variant", "theme", "icon_style", "title", "stagger", "opacity"} <= keys
    links = next(q for q in gr["socials"]["params"] if q["k"] == "links")
    plats = next(f for f in links["fields"] if f["k"] == "platform")["opts"]
    assert {"instagram", "tiktok", "paseoclub", "web", "whatsapp"} <= set(plats)
    cta = {q["k"]: q for q in gr["cta"]["params"]}
    assert {"title", "platform", "display", "initials", "media", "icon"} <= set(cta)
    assert cta["media"]["accept"] == ["image"] and "image" in cta["avatar"]["opts"]
    card = {q["k"]: q for q in gr["card"]["params"]}
    assert "booking" in card["variant"]["opts"] and {"platform", "display"} <= set(card)
    assert "booking" in D.CARD_VARIANTS and "socials" in D.GRAPHIC_KINDS


def test_normalize_keeps_a_valid_profile_and_drops_junk():
    base = {"version": 2, "video": "x.mp4", "duration": 5.0, "fx": {}}
    n = S.normalize(dict(base, profile=dict(PROF, extra=1)))
    assert n["profile"]["name"] == "Dra. Lucía Fernández" and "extra" not in n["profile"]
    assert "profile" not in S.normalize(dict(base, profile={"name": ""}))
    assert "profile" not in S.normalize(dict(base, profile="texto"))
    assert any("profile" in e for e in S.validate(dict(base, profile="texto")))
    it = {"id": "gr_s", "kind": "socials", "start": 0, "end": 2,
          "links": [{"platform": "tiktok", "text": "@a"}, {"platform": "nada", "text": "@b"}, "x"]}
    g = S.normalize(dict(base, fx={"graphics": [it]}))["fx"]["graphics"][0]
    assert g["links"][0] == {"platform": "tiktok", "text": "@a"} and "platform" not in g["links"][1]
    assert len(g["links"]) == 2


# ---------------------------------------------------------------- graficos
W, H = 540, 960


def _frame(bg="#5A6B58"):
    arr, surf = C.new_frame(W, H)
    surf.getCanvas().clear(C.color(bg))
    return arr, surf.getCanvas()


def _draw(kind, item, lt, dur=4.0):
    arr, cv = _frame()
    ctx = C.demo_ctx(W=W, H=H, t=lt, quality="draft", entities={})
    G.RENDERERS[kind](cv, item, lt, dur, ctx)
    return arr.copy()


BG = _frame()[0].copy()


def _mask(a):
    return np.abs(a.astype(np.int16) - BG.astype(np.int16)).sum(axis=2) > 12


SOC = {"id": "gr_soc", "kind": "socials", "x": 0.47, "y": 0.5, "size": 0.074, "layout": "column",
       "links": [{"platform": "instagram", "text": "@dra.lucia.demo"}, {"platform": "tiktok", "text": "@dra.lucia.tt"},
                 {"platform": "paseoclub", "text": "paseo.club/dra-lucia-demo"}]}


def test_socials_chips_draw_inside_safe_and_stagger():
    late = _mask(_draw("socials", SOC, 2.0))
    ys, xs = np.nonzero(late)
    assert late.sum() > 2000
    assert xs.min() >= SAFE["left"] * W - 30 and xs.max() <= SAFE["right"] * W + 30   # sombra suave
    assert ys.min() >= SAFE["top"] * H - 30 and ys.max() <= SAFE["bottom"] * H + 30
    # escalonado: al inicio solo el primer chip asoma
    early = _mask(_draw("socials", SOC, 0.05))
    rows = np.nonzero(early.any(axis=1))[0]
    assert early.sum() < late.sum() * 0.6 and (rows.max() if rows.size else 0) < H * 0.5
    # insignia de Instagram con su degradado (rosa/morado) en el primer chip
    img = _draw("socials", SOC, 2.0)
    top = img[:int(H * 0.5)]
    pink = ((top[..., 0] > 170) & (top[..., 2] > 80) & (top[..., 1] < 110)).sum()
    assert pink > 20
    w, h = G.socials_extent(SOC, C.demo_ctx(W=W, H=H))
    assert 0.2 * W < w <= (SAFE["right"] - SAFE["left"]) * W + 1 and h > 3 * 0.074 * W


def test_socials_row_wraps_and_watermark_is_subtle():
    row = dict(SOC, layout="row")
    w, h = G.socials_extent(row, C.demo_ctx(W=W, H=H))
    assert w <= (SAFE["right"] - SAFE["left"]) * W + 1                   # no se sale: parte en filas
    wm = {"id": "gr_wm", "kind": "socials", "variant": "watermark", "x": 0.3, "y": 0.12, "size": 0.03,
          "links": [{"platform": "instagram", "text": "@dra.lucia.demo"}], "opacity": 0.6}
    m = _mask(_draw("socials", wm, 1.5))
    assert 30 < m.sum() < 0.02 * W * H
    assert G.RENDERERS["socials"] is G.draw_socials


def test_follow_with_title_platform_and_photo():
    it = {"id": "gr_f", "kind": "cta", "variant": "follow", "title": "Dra. Lucía Fernández",
          "handle": "@dra.lucia.demo", "platform": "instagram", "initials": "LF", "x": 0.47, "y": 0.5,
          "size": 0.68, "button_color": "#FFD23F", "anim_in": "none"}
    ctx = C.demo_ctx(W=W, H=H)
    # el nombre largo agranda la pastilla (sin pasar de SAFE) en vez de cortarse
    assert G.cta_extent(it, ctx) * 2 > 0.68 * W
    assert G.cta_extent(it, ctx) * 2 <= (SAFE["right"] - SAFE["left"]) * W + 1
    a = _draw("cta", it, 1.5)
    assert _mask(a).sum() > 3000
    # foto de perfil: la imagen inyectada (roja) se ve dentro del avatar
    red = np.zeros((200, 160, 4), np.uint8)
    red[..., 0] = 230
    red[..., 3] = 255
    b = _draw("cta", dict(it, _media_image=red, avatar="image"), 1.5)
    x0 = int((0.47 * W) - G.cta_extent(it, ctx))
    band = b[int(0.5 * H) - 20:int(0.5 * H) + 20, max(0, x0):x0 + 80]
    reds = ((band[..., 0] > 190) & (band[..., 1] < 70) & (band[..., 2] < 70)).sum()
    assert reds > 150
    reds_initials = ((a[int(0.5 * H) - 20:int(0.5 * H) + 20, max(0, x0):x0 + 80][..., 0] > 190) &
                     (a[int(0.5 * H) - 20:int(0.5 * H) + 20, max(0, x0):x0 + 80][..., 1] < 70)).sum()
    assert reds_initials < reds / 4


def test_link_below_with_display_is_two_lines_and_booking_card():
    one = {"id": "gr_l", "kind": "cta", "variant": "link_below", "label": "Agenda en PaseoClub",
           "x": 0.47, "y": 0.5, "size": 0.66, "arrow_scale": 0, "anim_in": "none"}
    two = dict(one, display="paseo.club/dra-lucia-demo", platform="paseoclub")
    h1 = np.nonzero(_mask(_draw("cta", one, 1.5)).any(axis=1))[0]
    h2 = np.nonzero(_mask(_draw("cta", two, 1.5)).any(axis=1))[0]
    assert (h2.max() - h2.min()) > (h1.max() - h1.min()) * 1.1
    card = {"id": "gr_b", "kind": "card", "variant": "booking", "title": "Horarios disponibles",
            "display": "paseo.club/dra-lucia-demo", "label": "Agenda en PaseoClub", "platform": "paseoclub",
            "x": 0.47, "y": 0.5, "size": 0.7}
    assert _mask(_draw("card", card, 1.5)).sum() > 8000
    assert D.card_height(card) > D.card_height(dict(card, variant="generic"))


# ---------------------------------------------------------------- render: foto de perfil opcional
def test_render_keeps_the_follow_cta_when_the_photo_is_missing_or_broken(tmp_path):
    from PIL import Image
    from motion import render as R
    import test_render_media as TM
    src = TM._src(tmp_path / "fuente.mp4")
    media = tmp_path / "media"
    media.mkdir()
    Image.new("RGB", (400, 400), (230, 20, 20)).save(media / "cara01.png")
    (media / "rota01.png").write_bytes(b"no es una imagen")
    base = {"id": "gr_f", "kind": "cta", "variant": "follow", "start": 0.0, "end": 3.5, "x": 0.5, "y": 0.5,
            "size": 0.7, "title": "Dra. Lucía Fernández", "handle": "@dra.lucia.demo", "platform": "instagram",
            "anim_in": "none", "anim_out": "none", "avatar": "image"}
    o = TM._opts(tmp_path)
    for sid, ok in (("cara01", True), ("no_esta", False), ("rota01", False)):
        p = TM._plan(src, {"graphics": [dict(base, media={"sid": sid, "type": "image"})]})
        pr = R.prepare(p, o)
        assert [g["id"] for g in pr["job"]["graphics"]] == ["gr_f"], sid           # nunca se omite
        if sid == "no_esta":
            assert "avatar:gr_f:no_encontrado" in pr["skipped"]
        fr = R.render_frame(p, 1.0, o)
        assert fr.shape[2] == 4
        if sid == "rota01":
            job = pr["job"]
            R.prepare_media(job, pr["opts"])
            assert R.drop_failed_media(job) == [] and job["graphics"]
    # sin medio: el CTA sigue (iniciales) y no hay aviso de medio
    p = TM._plan(src, {"graphics": [dict(base, avatar="initial")]})
    pr = R.prepare(p, o)
    assert pr["job"]["graphics"] and not [s for s in pr["skipped"] if s.startswith(("media:", "avatar:"))]


def test_locked_profile_items_get_the_new_data(synth_prof):
    p = copy.deepcopy(synth_prof)
    lt = _items(p, "lower_third")[0]
    fol = _items(p, "cta", "follow")[-1]
    lt["locked"] = True
    fol["locked"] = True
    lt["y"] = 0.66                                              # el usuario lo movio
    other = PR.normalize_profile({"name": "Dra. Nora Demo", "title": "Endodoncista",
                                  "links": ["instagram.com/dra.nora.demo"],
                                  "avatar": {"sid": "foto_nora_demo", "type": "image"}})
    q = D.apply_profile(p, other)
    lt2 = next(g for g in q["fx"]["graphics"] if g["id"] == lt["id"])
    assert lt2["title"] == "Dra. Nora Demo" and lt2["subtitle"] == "Endodoncista" and lt2["y"] == 0.66
    f2 = next(g for g in q["fx"]["graphics"] if g["id"] == fol["id"])
    assert f2["handle"] == "@dra.nora.demo" and f2["initials"] == "ND" and f2["media"]["sid"] == "foto_nora_demo"
    assert any("bloqueado" in n for n in q["director"]["notes"])


def test_regenerate_socials_and_photo_follow():
    prof = _prof(avatar={"sid": "foto_perfil_demo", "type": "image"})
    prof["links"].append(PR.parse_link("tiktok.com/@dra.lucia.tt"))
    prof = PR.normalize_profile(prof)
    p = _direct(_lines_plan(["Si te duele la muela cuando tomas algo frio, esto te interesa.",
                             "Una resina bien hecha protege tu diente y se ve natural.",
                             "Te espero en el consultorio."], broll_end=2.6), prof)
    ids = [g["id"] for g in p["fx"]["graphics"] if g["kind"] in ("socials", "cta")]
    q = D.redirect(p, only_ids=ids, seed=5)
    for g in q["fx"]["graphics"]:
        if g["id"] in ids and g["kind"] == "cta" and g.get("variant") == "follow":
            assert g["avatar"] in ("image", "initial") and g["media"]["sid"] == "foto_perfil_demo"
        if g["id"] in ids and g["kind"] == "socials":
            assert g["links"] and g["theme"] in ("light", "dark") and g["icon_style"] in ("brand", "mono")
    TD._check_catalog(q)


# ---------------------------------------------------------------- ola de arreglos (critica)
RICH = PR.normalize_profile({
    "name": "Dra. Lucía Fernández", "title": "Odontóloga", "address": "Av. Demo 123, Col. Centro",
    "links": [{"url": "instagram.com/dra.lucia.demo", "primary": True}, "https://paseo.club/business/dra-lucia-demo",
              "tiktok.com/@clinica.demo.tt", "facebook.com/clinica.demo.fb", "wa.me/523312345678",
              "youtube.com/@ClinicaDemoYT"]})
LINK_LINES = ["Si sientes los dientes asperos, esto te interesa.",
              "Una resina bien hecha protege tu diente y se ve natural.",
              "La limpieza profunda quita el sarro y deja tus dientes brillantes.",
              "Te dejo el link aqui abajo para agendar tu cita.",
              "Sigueme para mas consejos de tu sonrisa."]


def _mid(lines, i, txt):
    ls = list(lines)
    ls[i] = txt
    return ls


@pytest.mark.parametrize("mid", ["Los dientes de abajo son los que mas sarro acumulan.",
                                 "Cepillate de arriba hacia abajo, nunca de lado.",
                                 "La pagina del libro dice que el sarro es malo.",
                                 "La cita dura una hora y no duele nada."])
def test_booking_pill_needs_a_real_invitation(mid):
    """'abajo', 'pagina' o 'cita' sueltos no mandan a reservar: sin pastilla a mitad del video."""
    lines = _mid(LINK_LINES, 3, mid)
    p = _direct(_lines_plan(lines, broll_end=3.0), PROF)
    assert p["director"]["script"]["anchors"].get("link") is None
    tl = _tl(p)
    for g in _items(p, "cta", "link_below"):
        assert tl.remap_clamped(g["start"]) >= tl.final_duration - 3.5, g     # solo la tarjeta final


@pytest.mark.parametrize("line", ["Te dejo el link aqui abajo para agendar tu cita.",
                                  "Agenda tu cita en mi pagina.",
                                  "Abajo en la descripcion esta mi agenda.",
                                  "Pide tu cita hoy mismo."])
def test_booking_pill_on_a_real_invitation_and_long_enough(line):
    lines = _mid(LINK_LINES, 3, line)
    p = _direct(_lines_plan(lines, broll_end=3.0), PROF)
    anc = p["director"]["script"]["anchors"].get("link")
    assert anc is not None
    tl = _tl(p)
    pills = _items(p, "cta", "link_below")
    assert pills
    for g in pills:
        assert tl.remap_clamped(g["end"]) - tl.remap_clamped(g["start"]) >= D._Choreo.LINK_MIN - 1e-3, g


def _stack_rects(p, t):
    """Rectangulos (x0, y0, x1, y1) de lo que forma el cierre en el tiempo final t."""
    tl = _tl(p)
    out = []
    for g in p["fx"]["graphics"]:
        if not (tl.remap_clamped(g["start"]) <= t < tl.remap_clamped(g["end"]) + 1e-6):
            continue
        k, v = g["kind"], g.get("variant")
        if k == "cta" and v == "follow":
            h = g["size"] * 0.17 * 1080 / 1920
        elif k == "cta" and v == "link_below":
            h = D._Choreo._link_h(g["size"], bool(g.get("display")))
        elif k == "socials" and v != "watermark":
            h = D.socials_size(g)[1]
        elif k == "card" and v == "location":
            h = D.card_height(g)
        else:
            continue
        w = g.get("width") or g["size"]
        out.append((g["id"], g["x"] - w / 2, g["y"] - h / 2, g["x"] + w / 2, g["y"] + h / 2, g))
    return out


def test_end_card_on_broll_has_socials_grid_aligned_with_everything():
    """Perfil con 6 enlaces y direccion sobre el b-roll final: ubicacion + Seguir + chips (fila o
    rejilla) + reservas, TODOS con el mismo ancho y centro (bordes alineados), sin encimarse y
    dentro de SAFE; sin notas de 'sin espacio' salvo lo que excede el tope de chips."""
    p = _direct(_lines_plan(LINK_LINES, broll_end=3.0), RICH)
    tl = _tl(p)
    t = tl.final_duration - 0.2
    rects = _stack_rects(p, t)
    kinds = {(r[5]["kind"], r[5].get("variant")) for r in rects}
    assert ("cta", "follow") in kinds and ("cta", "link_below") in kinds and ("card", "location") in kinds
    soc = [r[5] for r in rects if r[5]["kind"] == "socials"]
    assert soc and soc[0]["layout"] in ("row", "grid") and len(soc[0]["links"]) == 4
    assert {l["platform"] for l in soc[0]["links"]} == {"tiktok", "facebook", "whatsapp", "youtube"}
    xs = {round(r[5]["x"], 3) for r in rects}
    ws = {round(r[5].get("width") or r[5]["size"], 3) for r in rects}
    assert len(xs) == 1 and len(ws) == 1, (xs, ws)                    # alineados
    ys = sorted(rects, key=lambda r: r[2])
    for a, b in zip(ys, ys[1:]):
        assert a[4] <= b[2] + 1e-3, (a[0], b[0])                        # sin encimarse
    for r in rects:
        assert SAFE["left"] - 0.005 <= r[1] and r[3] <= SAFE["right"] + 0.005 and SAFE["top"] - 0.005 <= r[2] \
            and r[4] <= SAFE["bottom"] + 0.005, r[0]
    assert not any("sin espacio" in n for n in p["director"]["notes"]), p["director"]["notes"]
    TD._check_catalog(p)


def test_end_card_scene_when_it_does_not_fit_on_camera():
    """Primer plano (la barbilla sobre los subtitulos) con perfil de varias redes: la tarjeta final
    es una escena corta (video desenfocado) con Seguir + chips + reservas, >= 2 s, subtitulos
    ocultos y una nota; lo mismo a camara si las redes no caben en el pecho."""
    for plan in (TD._closeup_plan(), _lines_plan(LINK_LINES)):
        p = _direct(plan, RICH)
        tl = _tl(p)
        sc = [c for c in p["fx"]["cutaways"] if c["id"].startswith("cw_endcard")]
        assert len(sc) == 1, [c["id"] for c in p["fx"]["cutaways"]]
        c = sc[0]
        a, b = tl.remap_clamped(c["start"]), tl.remap_clamped(c["end"])
        assert c["background"] == "blur_self" and b - a >= 2.0 - 1e-3 and b >= tl.final_duration - 0.05
        kinds = [(g["kind"], g.get("variant")) for g in c["graphics"]]
        assert ("cta", "follow") in kinds and ("socials", "chips") in kinds and ("cta", "link_below") in kinds
        assert len({g["x"] for g in c["graphics"]}) == 1 and len({g.get("width") for g in c["graphics"]}) == 1
        soc = next(g for g in c["graphics"] if g["kind"] == "socials")
        assert soc["title"] and len(soc["links"]) == 4
        rows = [r for r in p["fx"]["captions"].get("y_ranges") or [] if r.get("hide")]
        assert any(r["start"] <= c["start"] + 0.01 and r["end"] >= c["end"] - 0.01 for r in rows)
        assert any("tarjeta final como escena" in n for n in p["director"]["notes"])
        # nada del video queda encima de la escena
        for g in p["fx"]["graphics"]:
            if g["kind"] in ("cta", "socials", "lower_third") and g.get("variant") != "watermark":
                assert tl.remap_clamped(g["end"]) <= a + 0.25, g["id"]
        TD._check_catalog(p)


def test_end_card_lasts_at_least_two_seconds_with_a_profile():
    p = _direct(TD.synthetic_plan(), PROF)
    tl = _tl(p)
    fol = _items(p, "cta", "follow")
    sc = [c for c in p["fx"]["cutaways"] if c["id"].startswith("cw_endcard")]
    start = min([tl.remap_clamped(g["start"]) for g in fol] + [tl.remap_clamped(c["start"]) for c in sc])
    assert tl.final_duration - start >= D._Choreo.END_MIN - 0.35


def test_missing_links_leave_a_note():
    many = PR.normalize_profile(dict(RICH, links=RICH["links"] + [
        {"url": "https://x.com/clinica_demo_x"}, {"url": "https://threads.net/@clinica.demo.th"}]))
    p = _direct(_lines_plan(LINK_LINES, broll_end=3.0), many)
    notes = [n for n in p["director"]["notes"] if "sin espacio" in n]
    assert notes and ("X @clinica_demo_x" in notes[0] or "Threads" in notes[0]), p["director"]["notes"]


def test_business_shows_in_the_lower_third_and_the_brand_follow():
    brand = PR.normalize_profile({"name": "Marco Demo", "title": "Perfumista", "business": "Olfato",
                                  "links": [{"url": "instagram.com/olfato.demo", "primary": True},
                                            "https://olfato-demo.mx/perfumes"]})
    p = _direct(TD.synthetic_plan(), brand)
    lt = _items(p, "lower_third")[0]
    assert lt["title"] == "Marco Demo" and lt["subtitle"] == "Perfumista · Olfato"
    fol = [g for g in _items(p, "cta", "follow")] + [g for c in p["fx"]["cutaways"] for g in c.get("graphics") or []
                                                      if g.get("variant") == "follow"]
    assert fol and fol[-1]["title"] == "Olfato" and fol[-1]["handle"] == "@olfato.demo"
    pills = [g for g in _items(p, "cta", "link_below")] + [g for c in p["fx"]["cutaways"]
                                                            for g in c.get("graphics") or []
                                                            if g.get("variant") == "link_below"]
    assert pills and pills[-1]["label"] == "Compra en línea" and pills[-1]["display"] == "olfato-demo.mx/perfumes"
    # red personal: el Seguir es de la persona
    pers = PR.normalize_profile(dict(brand, links=[{"url": "instagram.com/marco.demo", "primary": True}]))
    q = _direct(TD.synthetic_plan(), pers)
    fol2 = [g for g in _items(q, "cta", "follow")] + [g for c in q["fx"]["cutaways"] for g in c.get("graphics") or []
                                                       if g.get("variant") == "follow"]
    assert fol2[-1]["title"] == "Marco Demo"
    # un negocio largo que no cabe junto a la profesion: solo la profesion
    long_ = PR.normalize_profile(dict(brand, business="Perfumería Artesanal de Oriente Medio y Especias Finas"))
    r = _direct(TD.synthetic_plan(), long_)
    assert _items(r, "lower_third")[0]["subtitle"] == "Perfumista"
    # apply_profile de marca a marca actualiza el subtitulo combinado
    s = D.apply_profile(p, dict(brand, business="Aroma Demo", links=[{"url": "instagram.com/aromademo", "primary": True}]))
    assert _items(s, "lower_third")[0]["subtitle"] == "Perfumista · Aroma Demo"


def test_a_common_word_equal_to_the_name_does_not_anchor_the_lower_third():
    blanca = PR.normalize_profile({"name": "Dra. Blanca Flores", "title": "Odontóloga",
                                   "links": ["instagram.com/dra.blanca.demo"]})
    lines = ["Si quieres una sonrisa blanca esto te interesa.",
             "Una resina bien hecha protege tu diente y se ve natural.",
             "La limpieza profunda quita el sarro y deja tus dientes brillantes.",
             "Sigueme para mas consejos de tu sonrisa."]
    p = _direct(_lines_plan(lines), blanca)
    assert p["director"]["script"]["anchors"].get("name") is None
    said = _mid(lines, 2, "Hola, soy la doctora Blanca Flores y hoy te explico todo.")
    q = _direct(_lines_plan(said), blanca)
    na = q["director"]["script"]["anchors"].get("name")
    words = D._flatten_words(q)
    assert na is not None and D.norm(words[na[0]]["text"]) in ("doctora", "blanca")


@pytest.mark.parametrize("style", ["dinamico", "elegante", "divertido"])
def test_lower_third_never_straddles_a_background_change(style):
    for plan in (TD.synthetic_plan(), _lines_plan(LINK_LINES, broll_end=3.0)):
        p = _direct(plan, PROF, style=style)
        for lt in _items(p, "lower_third"):
            for e in p["fx"]["effects"]:
                if e["kind"] != "bg_replace":
                    continue
                la, lb, ea, eb = lt["start"], lt["end"], e["start"], e["end"]
                overlap = ea < lb and la < eb
                assert not overlap or (ea <= la + 1e-3 and lb <= eb + 1e-3), (style, lt["id"], e["id"], la, lb, ea, eb)


def test_follow_renders_long_names_and_handles_without_ellipsis():
    """El Seguir con un nombre largo usa su variante corta y el @usuario completo (sin '...')."""
    ctx = C.FrameCtx(W=1080, H=1920, style="dinamico")
    for title, handle in (("Dra. Guadalupe Fernández Castañeda", "@dra.guadalupe.fernandez.c"),
                          ("Dra. María de los Ángeles Castañeda Villaseñor", "@dra.mariadelosangeles.castaneda")):
        it = {"kind": "cta", "variant": "follow", "title": title, "handle": handle, "platform": "instagram",
              "size": 0.78, "width": 0.8}
        L = G._follow_layout(it, ctx)
        assert L["sub"] == handle and "…" not in L["name"] and L["name"] in PR.name_variants(title)
        assert L["fs"].measureText(L["sub"]) <= L["maxw"] + 1.0
        assert L["fn"].getSize() >= L["h"] * G.FOLLOW_NAME[2] - 0.5
        assert L["W"] <= (SAFE["right"] - SAFE["left"]) * 1080 + 1
    # la insignia de la red del Seguir es grande (~0.44 del avatar: > 40 px a 1080)
    it = {"kind": "cta", "variant": "follow", "title": "Dra. Demo", "handle": "@dra.demo", "platform": "instagram",
          "size": 0.78}
    L = G._follow_layout(it, ctx)
    assert L["badge"] == "instagram" and L["d"] * G.FOLLOW_BADGE >= 40


def test_socials_grid_and_common_width_render():
    ctx = C.FrameCtx(W=1080, H=1920, style="dinamico")
    links = [{"platform": p_, "text": t_} for p_, t_ in (("tiktok", "@a.demo"), ("facebook", "@b.demo"),
                                                           ("whatsapp", "+52 33 1234 5678"), ("youtube", "@c.demo"))]
    # ancho comun = lo mas ancho que cabe centrado (SYM_W, 0.76): el mismo aire a los dos lados
    wc = C.SYM_W
    it = {"kind": "socials", "links": links, "layout": "grid", "cols": 2, "size": 0.058, "width": wc}
    chips, rows, h, gap, tw, th, head = G._socials_layout(G.sanitize(it), ctx)
    assert [len(r) for r in rows] == [2, 2] and len({c[3] for c in chips}) == 1
    assert abs(tw - wc * 1080) < 2.0
    link = {"kind": "cta", "variant": "link_below", "label": "Agenda en PaseoClub", "display": "paseo.club/dra-demo",
            "size": 0.66, "width": wc}
    assert abs(G.cta_extent(link, ctx) * 2 - wc * 1080) < 1.0
    fol = {"kind": "cta", "variant": "follow", "title": "Dra. Demo", "handle": "@dra.demo", "size": 0.76, "width": wc}
    assert abs(G.cta_extent(fol, ctx) * 2 - wc * 1080) < 1.0
    arr, surf = C.new_frame(1080, 1920)
    cv = surf.getCanvas()
    for kind, x in (("socials", it), ("cta", link), ("cta", fol),
                    ("socials", {"kind": "socials", "variant": "watermark", "links": links[:1], "size": 0.03,
                                 "backdrop": 0.34, "x": 0.5, "y": 0.1})):
        G.RENDERERS[kind](cv, dict(x, id="z", x=x.get("x", 0.5), y=x.get("y", 0.5)), 1.5, 4.0, ctx)
    assert arr[..., 3].max() > 0


# ---------------------------------------------------------------- QA final (entrega)
def test_locked_title_right_after_the_link_keeps_the_booking_pill(monkeypatch=None):
    """Bloquear (o editar) el titulo que entra justo despues de 'link aqui abajo' no borra la
    pastilla de reservas al re-dirigir. El titulo bloqueado reservaba TODAS las franjas y la
    pastilla (1.5 s minimos) lo rozaba: re-dirigir la quitaba y solo quedaba en la tarjeta final."""
    lines = ["Si al sonreir notas manchas en tus dientes, tenemos que hablar.",
             "Una buena limpieza cambia muchisimo como se sienten tus dientes.",
             "Checa mis horarios disponibles en el link aqui abajo.",
             "Y tranquilo, aqui nadie te va a regañar por tu ultima visita."]
    p = _direct(_lines_plan(lines, broll_end=5.5), PROF)
    words = D._flatten_words(p)
    link_w = next(w for w in words if D.norm(w["text"]) == "link")
    t0 = float(link_w["t0"])
    # el usuario edito/bloqueo un titulo en la franja del pecho que entra 1.45 s despues del enlace
    p["fx"]["text"].append({"id": "tx_user_qa", "src": "user", "locked": True, "enabled": True,
                            "preset": "serif_elegant", "text": "tranquilo\nNADIE te regaña",
                            "start": round(t0 + 1.45, 3), "end": round(t0 + 3.2, 3),
                            "x": 0.5, "y": 0.5, "size": 0.07})
    q = D.redirect(copy.deepcopy(p), style="dinamico", music_sync=False)
    tl = _tl(q)
    near = [g for g in _items(q, "cta", "link_below")
            if abs(tl.remap_clamped(g["start"]) - tl.remap_clamped(t0)) < 0.6]
    assert near, D.edit_table(q)
    # la pastilla se sostiene y no pisa al titulo bloqueado (otra franja)
    pill = near[0]
    assert tl.remap_clamped(pill["end"]) - tl.remap_clamped(pill["start"]) >= D._Choreo.LINK_MIN
    assert abs(float(pill["y"]) - 0.5) > 0.06
    TD._check_catalog(q)


def test_editing_whatsapp_and_phone_regenerates_the_end_card_chips():
    """Reporte de Kevin («no se puede actualizar / re-generar»): cambiar el WhatsApp y el teléfono del
    perfil y aplicarlo re-genera los chips de la tarjeta final con los números NUEVOS (antes seguían los
    viejos: el enlace wa.me / tel: no cambiaba); vaciarlos los quita."""
    lines = ["Si te duele la muela cuando tomas algo frio, esto te interesa.",
             "Una resina bien hecha protege tu diente y se ve natural.",
             "La limpieza profunda quita el sarro y deja tus dientes brillantes.",
             "Te espero en el consultorio."]
    saved = PR.normalize_profile({"name": "Dra. Ana Demo", "title": "Odontóloga", "links": ["instagram.com/dra.ana.demo"],
                                  "whatsapp": "+52 55 0000 0001", "phone": "+52 33 0000 0001"})
    p = _direct(_lines_plan(lines, broll_end=2.6), saved)
    chips = lambda pl: [l["text"] for g in _items(pl, "socials", "chips") for l in g["links"]]
    assert chips(p) == ["+52 55 0000 0001", "+52 33 0000 0001"], D.edit_table(p)
    q = D.apply_profile(p, PR.normalize_profile(dict(saved, whatsapp="+52 55 0000 0002", phone="+52 33 0000 0002")))
    assert chips(q) == ["+52 55 0000 0002", "+52 33 0000 0002"], D.edit_table(q)
    assert "0000 0001" not in json.dumps(q["fx"], ensure_ascii=False)
    r = D.apply_profile(q, PR.normalize_profile(dict(saved, whatsapp="", phone="")))
    assert "0000 000" not in json.dumps(r["fx"], ensure_ascii=False)
    TD._check_safe(q)
