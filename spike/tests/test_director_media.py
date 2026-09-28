"""Foto y logo de los enlaces del perfil en la edicion (motion/director.py) y efectos de recorte que
el director ya no propone solo.

- Seguir con la foto de SU red (la subida por el usuario manda), pastilla de reservas con la foto
  de la pagina + insignia del logo (o solo el logo), tarjeta de horarios 'En PaseoClub' con
  `platform` + `logo`, chips con el logo de las paginas sin icono propio.
- apply_profile_media: pone / cambia / quita esos medios en un plan ya dirigido SIN mover nada
  (mismos ids, tiempos, x/y/tamano/ancho), respeta lo elegido a mano, sigue simetrico y da lo
  mismo que dirigir con la foto desde el inicio.
- R2: ningun estilo ni intensidad propone bg_replace ni color_pop; un plan viejo que los traia los
  conserva al aplicar el perfil; re-dirigir ya no los propone; lo del usuario se queda.

Solo datos FICTICIOS (sids, usuarios y paginas inventados; no hay archivos ni red).
"""
import copy
import json

import pytest

from motion import director as D
from motion import profile as PR
from motion import schema as S
import test_director as TD
import test_director_symmetry as TS
import test_profile_director as TP

FOTO_IG = {"sid": "a1b2c3d4e5f6", "type": "image", "w": 100, "h": 100, "src": "https://cdn.demo.test/ig.jpg"}
FOTO_PC = {"sid": "0a1b2c3d4e5f", "type": "image", "w": 1024, "h": 768}
LOGO_PC = {"sid": "f0e1d2c3b4a5", "type": "image", "w": 512, "h": 512}
LOGO_LT = {"sid": "1234567890ab", "type": "image", "w": 256, "h": 256}
AVATAR = {"sid": "avatar_subido_demo", "type": "image", "w": 600, "h": 600}
IG_URL = "https://instagram.com/ana.demo.ig"
PC_URL = "https://paseo.club/business/ana-demo"
LINES = ["Si te duele la muela cuando tomas algo frio, esto te interesa.",
         "Una resina bien hecha protege tu diente y se ve natural.",
         "Checa mis horarios disponibles en el link aqui abajo.",
         "La limpieza profunda quita el sarro y deja tus dientes brillantes.",
         "Y no te preocupes, no te voy a juzgar por tu cepillo viejo."]


def _profile(ig_photo=True, pc_photo=True, pc_logo=True, lt_logo=True, **kw):
    ig = {"url": IG_URL}
    if ig_photo:
        ig.update(photo=FOTO_IG, media_src={"photo": "auto"})
    pc = {"url": PC_URL, "media_src": {}}
    if pc_photo:
        pc["photo"] = FOTO_PC
        pc["media_src"]["photo"] = "auto"
    if pc_logo:
        pc["logo"] = LOGO_PC
        pc["media_src"]["logo"] = "auto"
    lt = {"url": "https://linktr.ee/ana.demo"}
    if lt_logo:
        lt.update(logo=LOGO_LT, media_src={"logo": "auto"})
    return PR.normalize_profile(dict({"name": "Dra. Ana Demo", "title": "Odontóloga",
                                      "links": [ig, pc, lt, "tiktok.com/@ana.demo.tt"]}, **kw))


def _bare(prof):
    p = copy.deepcopy(prof)
    p.pop("avatar", None)
    for l in p["links"]:
        for k in ("photo", "logo", "media_src", "media_for", "media_off"):
            l.pop(k, None)
    return PR.normalize_profile(p)


def _plan(prof, style="dinamico"):
    return TP._direct(TP._lines_plan(LINES, broll_end=2.6), prof, style=style)


def _all_graphics(p):
    out = list(p["fx"]["graphics"])
    for c in p["fx"]["cutaways"]:
        out += [g for g in c.get("graphics") or [] if isinstance(g, dict)]
        if isinstance(c.get("card"), dict):
            out.append(c["card"])
    return out


def _by(p, kind, variant=None):
    return [g for g in _all_graphics(p) if g.get("kind") == kind and (variant is None or g.get("variant") == variant)]


def _geo(p):
    keys = ("id", "kind", "variant", "x", "y", "size", "width", "start", "end", "offset", "layout", "cols")
    rows = [tuple(g.get(k) for k in keys) for g in p["fx"]["graphics"]]
    rows += [(c["id"], c["start"], c["end"], json.dumps([tuple(g.get(k) for k in keys) for g in c.get("graphics") or []]))
             for c in p["fx"]["cutaways"]]
    return rows


@pytest.fixture(scope="module")
def with_media():
    return _plan(_profile())


@pytest.fixture(scope="module")
def without_media():
    return _plan(_bare(_profile()))


# ---------------------------------------------------------------- helpers puros
def test_follow_media_precedence():
    prof = _profile()
    assert D.follow_media(prof, "instagram", "@ana.demo.ig") == {
        "avatar": "image", "media": {"sid": FOTO_IG["sid"], "type": "image", "w": 100, "h": 100}}
    # la foto SUBIDA por el usuario manda sobre la de su red
    up = PR.normalize_profile(dict(prof, avatar=AVATAR))
    assert D.follow_media(up, "instagram", "@ana.demo.ig")["media"]["sid"] == AVATAR["sid"]
    # otra red (sin foto) -> iniciales; @usuario de otra cuenta -> nada de esa red
    assert D.follow_media(prof, "tiktok", "@ana.demo.tt") == {}
    assert D.follow_media(prof, "instagram", "@otra.cuenta") == {}
    assert D.follow_media(None, "instagram") == {}
    # red con icono propio: sin logo aunque la pagina lo tenga
    assert "logo" not in D.follow_media(prof, "instagram", "@ana.demo.ig")
    assert D.own_icon("instagram") and D.own_icon("tiktok") and not D.own_icon("paseoclub")


def test_book_card_and_chip_media():
    prof = _profile()
    pc = PR.link_for(prof, "paseoclub")
    assert D.book_media(pc) == {"avatar": "image", "media": {"sid": FOTO_PC["sid"], "type": "image", "w": 1024, "h": 768},
                                "logo": {"sid": LOGO_PC["sid"], "type": "image", "w": 512, "h": 512}}
    only_logo = PR.link_for(_profile(pc_photo=False), "paseoclub")
    assert D.book_media(only_logo) == {"logo": {"sid": LOGO_PC["sid"], "type": "image", "w": 512, "h": 512}}
    assert D.book_media(PR.link_for(_bare(prof), "paseoclub")) == {} and D.book_media(None) == {}
    assert D.card_media(prof, "paseoclub") == {"platform": "paseoclub", "logo": D.book_media(pc)["logo"]}
    assert D.card_media(_bare(prof), "paseoclub") == {"platform": "paseoclub"}
    assert D.card_media(prof, "calendly") == {} and D.card_media(None, "paseoclub") == {}
    assert D.chip_media(PR.link_for(prof, "linktree"))["logo"]["sid"] == LOGO_LT["sid"]
    assert D.chip_media(PR.link_for(prof, "tiktok")) == {}
    # el medio del item no lleva `src` (el renderer resuelve el sid)
    assert "src" not in D.follow_media(prof, "instagram", "@ana.demo.ig")["media"]
    # quitado por el usuario: ni foto ni logo
    off = PR.normalize_profile(dict(prof, links=[dict(l, media_off={"photo": True, "logo": True})
                                                 if l["platform"] == "paseoclub" else l for l in prof["links"]]))
    assert D.book_media(PR.link_for(off, "paseoclub")) == {}


# ---------------------------------------------------------------- dirigir con la foto
def test_directed_items_carry_the_link_photo_and_logo(with_media):
    p = with_media
    fol = _by(p, "cta", "follow")
    assert fol and all(f["avatar"] == "image" and f["media"]["sid"] == FOTO_IG["sid"] and "logo" not in f
                       for f in fol)
    links = _by(p, "cta", "link_below")
    assert links, D.edit_table(p)
    for l in links:
        assert l["platform"] == "paseoclub" and l["avatar"] == "image"
        assert l["media"]["sid"] == FOTO_PC["sid"] and l["logo"]["sid"] == LOGO_PC["sid"]
        assert l["icon"] == "calendar-event"                     # respaldo si falta el archivo
    sched = _by(p, "card", "schedule")
    assert sched and sched[0]["subtitle"] == "En PaseoClub"
    assert sched[0]["platform"] == "paseoclub" and sched[0]["logo"]["sid"] == LOGO_PC["sid"]
    chips = [c for s in _by(p, "socials") for c in s["links"]]
    lt = [c for c in chips if c["platform"] == "linktree"]
    assert lt and lt[0]["logo"]["sid"] == LOGO_LT["sid"]
    assert all("logo" not in c for c in chips if c["platform"] in ("tiktok", "instagram"))
    TD._check_catalog(p)
    assert not [w for w in p["director"]["warnings"] if "campos fuera" in w]
    assert not [e for e in S.validate(p) if "falta 'video'" not in e]
    assert TS._violations(p) == []


def test_uploaded_avatar_wins_and_no_media_keeps_initials():
    p = _plan(_profile(avatar=AVATAR))
    assert all(f["media"]["sid"] == AVATAR["sid"] for f in _by(p, "cta", "follow"))
    # la pastilla sigue con la foto de la PAGINA (no la del avatar)
    assert all(l["media"]["sid"] == FOTO_PC["sid"] for l in _by(p, "cta", "link_below"))
    q = _plan(_bare(_profile()))
    for f in _by(q, "cta", "follow"):
        assert "media" not in f and f.get("avatar") in (None, "initial") and f["initials"] == "AD"
    for l in _by(q, "cta", "link_below"):
        assert not {"media", "logo"} & set(l) and l.get("avatar") is None
    s = _by(q, "card", "schedule")[0]
    assert s["platform"] == "paseoclub" and "logo" not in s


def test_same_geometry_with_or_without_the_photo(with_media, without_media):
    """La foto y el logo ocupan el hueco del icono / las iniciales: la edicion es LA MISMA."""
    assert _geo(with_media) == _geo(without_media)
    strip = lambda p: json.dumps({tr: p["fx"][tr] for tr in ("text", "effects", "transitions", "sfx")}, sort_keys=True)
    assert strip(with_media) == strip(without_media)


# ---------------------------------------------------------------- apply_profile_media
def test_patch_equals_directing_with_the_photo(with_media, without_media):
    full = _profile()
    out, ids = D.apply_profile_media(without_media, full)
    assert set(ids) >= {g["id"] for g in _by(with_media, "cta")} | {g["id"] for g in _by(with_media, "card", "schedule")}
    assert out["profile"] == full
    assert json.dumps(out["fx"], sort_keys=True) == json.dumps(with_media["fx"], sort_keys=True)
    assert _geo(out) == _geo(without_media)
    assert TS._violations(out) == []
    again, ids2 = D.apply_profile_media(out, full)
    assert ids2 == [] and again["fx"] == out["fx"]                         # idempotente
    # el plan original no se toca (copia)
    assert all("media" not in f for f in _by(without_media, "cta", "follow"))


def test_patch_updates_and_removes_only_profile_media(with_media):
    p = copy.deepcopy(with_media)
    fol = _by(p, "cta", "follow")
    links = _by(p, "cta", "link_below")
    # el usuario eligio otra imagen en un Seguir, la cara en otro, e iniciales ('Regenerar') en la pastilla
    fol[0]["media"] = {"sid": "imagen_propia_demo", "type": "image"}
    if len(fol) > 1:
        fol[1]["avatar"] = "face"
    links[0]["avatar"] = "initial"
    # la pagina cambio de foto y el usuario quito el logo
    prof = _profile()
    new_pc = {"sid": "9f8e7d6c5b4a", "type": "image", "w": 800, "h": 800}
    for l in prof["links"]:
        if l["platform"] == "paseoclub":
            l["photo"] = new_pc
            l.pop("logo")
            l["media_src"] = {"photo": "auto"}
            l["media_off"] = {"logo": True}
    prof = PR.normalize_profile(prof)
    out, ids = D.apply_profile_media(p, prof)
    ofol = {g["id"]: g for g in _by(out, "cta", "follow")}
    assert ofol[fol[0]["id"]]["media"]["sid"] == "imagen_propia_demo"        # lo elegido a mano se queda
    if len(fol) > 1:
        assert ofol[fol[1]["id"]]["avatar"] == "face"
    olinks = {g["id"]: g for g in _by(out, "cta", "link_below")}
    l0 = olinks[links[0]["id"]]
    assert l0["avatar"] == "initial" and l0["media"]["sid"] == new_pc["sid"] and "logo" not in l0
    for i, l in olinks.items():
        assert l["media"]["sid"] == new_pc["sid"] and "logo" not in l
    assert all("logo" not in s for s in _by(out, "card", "schedule"))
    assert _geo(out) == _geo(p)
    # sin foto de Instagram: el Seguir vuelve a iniciales (sin foto huerfana)
    noig = PR.normalize_profile(dict(prof, links=[dict(l, media_off={"photo": True}) if l["platform"] == "instagram"
                                                  else l for l in prof["links"]]))
    out2, _ = D.apply_profile_media(out, noig)
    f2 = {g["id"]: g for g in _by(out2, "cta", "follow")}
    for g in _by(out, "cta", "follow"):
        if g["id"] == fol[0]["id"]:
            assert f2[g["id"]]["media"]["sid"] == "imagen_propia_demo"
            continue
        if g.get("avatar") == "face":
            assert f2[g["id"]]["avatar"] == "face" and "media" not in f2[g["id"]]
            continue
        assert "media" not in f2[g["id"]] and f2[g["id"]]["avatar"] == "initial"


def test_patch_skips_items_of_other_accounts_and_generic_links():
    p = copy.deepcopy(_plan(_bare(_profile())))
    base = {"enabled": True, "src": "user", "locked": False, "intensity": 1.0, "start": 1.0, "end": 2.5,
            "x": 0.5, "y": 0.3, "size": 0.6}
    p["fx"]["graphics"] += [
        dict(base, id="gr_user_follow", kind="cta", variant="follow", label="Sígueme", handle="@otra.cuenta.demo",
             platform="instagram"),
        dict(base, id="gr_user_link", kind="cta", variant="link_below", label="Link abajo", handle="")]
    out, ids = D.apply_profile_media(p, _profile())
    assert "gr_user_follow" not in ids and "gr_user_link" not in ids
    g = {x["id"]: x for x in out["fx"]["graphics"]}
    assert "media" not in g["gr_user_follow"] and "media" not in g["gr_user_link"]
    # sin perfil en el plan ni en la llamada: nada que hacer
    bare = copy.deepcopy(p)
    bare.pop("profile", None)
    assert D.apply_profile_media(bare)[1] == []


def test_apply_profile_with_media_keeps_the_edit_and_locked_items(without_media):
    p = copy.deepcopy(without_media)
    fol = _by(p, "cta", "follow")[-1]
    fol["locked"] = True
    fol["y"] = round(fol["y"] - 0.01, 4)                          # el usuario lo movio
    q = D.apply_profile(p, _profile())
    f = next(g for g in q["fx"]["graphics"] if g["id"] == fol["id"])
    assert f["media"]["sid"] == FOTO_IG["sid"] and f["avatar"] == "image" and f["y"] == fol["y"]
    txt = lambda pl: [(t["id"], t.get("text"), t["start"], t["end"], t.get("y")) for t in pl["fx"]["text"]]
    assert txt(q) == txt(p)
    assert all(l["media"]["sid"] == FOTO_PC["sid"] for l in _by(q, "cta", "link_below"))
    # quitar la foto del perfil: el bloqueado vuelve a iniciales
    r = D.apply_profile(q, _bare(_profile()))
    f2 = next(g for g in r["fx"]["graphics"] if g["id"] == fol["id"])
    assert "media" not in f2 and f2["avatar"] == "initial"


def test_restyle_with_media_stays_symmetric(with_media):
    for st in ("elegante", "energetico"):
        q = D.redirect(with_media, style=st, music_sync=False)
        assert TS._violations(q) == [], st
        assert all(f.get("media", {}).get("sid") == FOTO_IG["sid"] for f in _by(q, "cta", "follow")), st


# ---------------------------------------------------------------- R2: efectos de recorte
@pytest.mark.parametrize("style", list(D.STYLES))
def test_director_never_proposes_matte_effects(style):
    for plan in (TD.synthetic_plan(), TD._light_bg_plan(), TP._lines_plan(LINES, broll_end=2.6)):
        for inten in (0.4, 1.0):
            p = D.direct(plan, style=style, intensity=inten, use_llm=False)
            assert not [e["id"] for e in p["fx"]["effects"] if e["kind"] in D.MATTE_FX], (style, inten)
    # siguen disponibles a mano (catalogo del Studio) y el desenfoque / gigante detras se quedan
    assert {"bg_replace", "color_pop", "bg_blur"} <= set(D.EFFECT_KINDS) and "behind_giant" in D.TEXT_PRESETS


def test_old_plans_keep_their_matte_effects_when_the_profile_changes(monkeypatch):
    monkeypatch.setattr(D, "AUTO_MATTE_FX", True)
    base = TD.synthetic_plan()
    base["profile"] = _bare(_profile())
    old = D.direct(base, use_llm=False)                                # director anterior a la regla
    monkeypatch.setattr(D, "AUTO_MATTE_FX", False)
    mfx = [e for e in old["fx"]["effects"] if e["kind"] in D.MATTE_FX]
    assert {e["kind"] for e in mfx} == {"bg_replace", "color_pop"}
    keep = lambda pl: sorted((e["id"], e["kind"], e["start"], e["end"]) for e in pl["fx"]["effects"] if e["kind"] in D.MATTE_FX)
    txt = lambda pl: [(t["id"], t.get("text"), t["start"], t["end"]) for t in pl["fx"]["text"]]
    # llega la foto / el usuario edita el perfil (misma revision): la edicion queda igual, con sus fondos
    q = D.apply_profile(old, _profile())
    assert keep(q) == keep(old) and txt(q) == txt(old)
    assert all(f["media"]["sid"] == FOTO_IG["sid"] for f in _by(q, "cta", "follow"))
    q2 = D.apply_profile(q, dict(_profile(), title="Ortodoncista"))
    assert keep(q2) == keep(old) and txt(q2) == txt(old)
    patched, _ids = D.apply_profile_media(old, _profile())
    assert keep(patched) == keep(old)
    # re-dirigir de verdad (otro estilo) ya no los propone; lo que el usuario puso a mano se queda
    r = D.redirect(q, style="energetico", music_sync=False)
    assert keep(r) == []
    mine = copy.deepcopy(q)
    for e in mine["fx"]["effects"]:
        if e["kind"] == "bg_replace":
            e["src"] = "user"
    r2 = D.redirect(mine, style="energetico", music_sync=False)
    assert [x for x in keep(r2) if x[1] == "bg_replace"] == [x for x in keep(q) if x[1] == "bg_replace"]


def test_tarjeta_vieja_que_nombra_la_pagina_recibe_su_logo():
    """Plan anterior: la tarjeta 'Horarios disponibles / En PaseoClub' no traia `platform`; al
    aplicar los medios del perfil se reconoce la pagina por su nombre y lleva el logo. Una tarjeta
    del usuario o que no nombra ninguna pagina no se toca."""
    logo = {"sid": "a1b2c3d4e5f6", "type": "image", "name": "logo.png", "w": 512, "h": 512}
    prof = {"name": "Dra. Demo", "links": [
        {"url": "https://paseo.club/business/negocio-demo", "logo": logo, "media_src": {"logo": "auto"}}]}
    plan = {"fx": {"graphics": [
        {"id": "g1", "kind": "card", "variant": "schedule", "src": "ai", "title": "Horarios disponibles",
         "subtitle": "En Paseo Club", "x": 0.5, "y": 0.5},
        {"id": "g2", "kind": "card", "variant": "schedule", "src": "user", "title": "Horarios",
         "subtitle": "En PaseoClub", "x": 0.5, "y": 0.5},
        {"id": "g3", "kind": "card", "variant": "price", "src": "ai", "title": "Consulta", "x": 0.5}]}}
    p2, changed = D.apply_profile_media(plan, prof)
    g = {it["id"]: it for it in p2["fx"]["graphics"]}
    assert g["g1"]["platform"] == "paseoclub" and g["g1"]["logo"]["sid"] == logo["sid"]
    assert "platform" not in g["g2"] and "logo" not in g["g2"]
    assert "platform" not in g["g3"] and "logo" not in g["g3"]
    assert changed == ["g1"] and (g["g1"]["x"], g["g1"]["y"]) == (0.5, 0.5)
