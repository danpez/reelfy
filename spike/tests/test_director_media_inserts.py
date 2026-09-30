"""Director con RECURSOS DEL GUION (plan.fx.media, tarjeta flotante) y RAMPAS de velocidad en los
cambios de camara del montaje (plan.shots[i].ramp).

- Un recurso del guion es contenido BLOQUEADO: el director no pone titulos, tarjetas, escenas,
  garabatos ni transiciones encima (los subtitulos se quedan), lo conserva tal cual al dirigir,
  re-dirigir, regenerar un item y simetrizar, y le da un aire suave de entrada.
- En una union con rampa (la rampa ES la transicion) no hay transicion ni whoosh, aunque sea una
  union 'section'. El interruptor del usuario (`ramp: false`) la apaga.
- Sin recursos ni rampas: la edicion de siempre.

Solo datos SINTETICOS (texto inventado, sin video real ni LLM/VLM).
"""
import copy

import pytest

from motion import director as D
from motion import schema as S
from test_director_joins import _LINES, montage_plan


def _direct(p, **kw):
    return D.direct(p, style=kw.pop("style", "dinamico"), intensity=kw.pop("intensity", 0.9), seed=7,
                    use_llm=False, trim=False, music_sync=False)


def _final(plan, t):
    return D._timeline(plan).remap_clamped(float(t))


def _line_span(plan, k):
    """(inicio, fin) ORIGINALES de las palabras de la linea k del guion sintetico."""
    words = [w for ph in plan["phrases"] for w in ph]
    n0 = sum(len(txt.split()) for txt, *_ in _LINES[:k])
    ws = words[n0:n0 + len(_LINES[k][0].split())]
    return ws[0]["start"], ws[-1]["end"]


def _media(plan, k, iid, **kw):
    a, b = _line_span(plan, k)
    it = {"id": iid, "start": round(a - 0.15, 3), "end": round(b + 0.25, 3), "sid": f"recurso{k}",
          "path": None, "kind": "image", "fit": "hold", "speed": 1.0, "src_in": 0.0,
          "style": "float_card", "enabled": True, "locked": True, "src": "script", "name": f"recurso_{k}"}
    it.update(kw)
    return it


def with_media(plan=None, lines=(2, 5)):
    p = copy.deepcopy(plan or montage_plan())
    p["fx"] = {"media": [_media(p, k, f"md_{i + 1}") for i, k in enumerate(lines)]}
    return p


@pytest.fixture(scope="module")
def directed_media():
    return _direct(with_media())


def _windows(p):
    return [(_final(p, m["start"]), _final(p, m["end"])) for m in p["fx"]["media"]]


def _overlaps(a, b, wins, pad=0.0):
    return [w for w in wins if a < w[1] + pad - 1e-6 and w[0] < b + pad - 1e-6]


# ---------------------------------------------------------------- recursos del guion
def test_recursos_se_conservan_tal_cual(directed_media):
    src = with_media()["fx"]["media"]
    got = directed_media["fx"]["media"]
    assert [m["id"] for m in got] == ["md_1", "md_2"]
    for a, b in zip(src, got):
        for k in ("start", "end", "sid", "kind", "fit", "speed", "src_in", "style", "locked", "src", "name"):
            assert b[k] == a[k], (k, a[k], b[k])
    assert not S.validate(directed_media) or all("media" not in e for e in S.validate(directed_media))


def test_nada_de_la_ia_encima_de_un_recurso(directed_media):
    p = directed_media
    wins = _windows(p)
    fx = p["fx"]
    placed = 0
    for tr in ("text", "graphics", "cutaways"):
        for it in fx[tr]:
            if tr == "graphics" and D.zone_of(tr, it) == "edge":
                continue                              # barra de progreso / marca de agua
            placed += 1
            a, b = _final(p, it["start"]), _final(p, it["end"])
            assert not _overlaps(a, b, wins), (tr, it["id"], a, b, wins)
    assert placed > 0                                  # la IA si edito el resto del video
    for it in fx["transitions"]:
        f = _final(p, it["at"])
        assert not _overlaps(f, f, wins, D.MEDIA_TRANS_PAD), (it["id"], f, wins)
    # los subtitulos se quedan (no se ocultan en los tramos del recurso)
    cap = fx["captions"]
    assert cap["enabled"] is True and cap.get("preset") != "none"
    for r in cap.get("y_ranges") or []:
        if r.get("hide"):
            assert not _overlaps(_final(p, r["start"]), _final(p, r["end"]), wins)


def test_aire_suave_al_entrar_la_tarjeta(directed_media):
    p = directed_media
    for a, _b in _windows(p):
        near = [s for s in p["fx"]["sfx"] if abs(_final(p, s["at"]) - a) < 0.2]
        assert any(s["name"] == D.MEDIA_SFX for s in near), (a, [(s["name"], s["at"]) for s in near])


def test_sin_recursos_la_edicion_de_siempre():
    a = _direct(montage_plan())
    b = _direct(dict(montage_plan(), fx={"media": []}))
    c = _direct(with_media(lines=()))
    assert a["fx"] == b["fx"] == c["fx"]
    assert a["fx"]["media"] == []
    # y un recurso deshabilitado no ocupa nada
    q = with_media()
    for m in q["fx"]["media"]:
        m["enabled"] = False
    d = _direct(q)
    assert {k: v for k, v in d["fx"].items() if k != "media"} == {k: v for k, v in a["fx"].items() if k != "media"}
    assert [m["enabled"] for m in d["fx"]["media"]] == [False, False]


def test_redirigir_y_regenerar_respetan_los_recursos(directed_media):
    p = directed_media
    r = D.redirect(p, style="energetico", music_sync=False)
    assert [m["id"] for m in r["fx"]["media"]] == ["md_1", "md_2"]
    wins = _windows(r)
    for tr in ("text", "graphics", "cutaways"):
        for it in r["fx"][tr]:
            if tr == "graphics" and D.zone_of(tr, it) == "edge":
                continue
            assert not _overlaps(_final(r, it["start"]), _final(r, it["end"]), wins), (tr, it["id"])
    # regenerar un item conserva la pista de recursos
    ids = [it["id"] for it in r["fx"]["text"]][:1]
    if ids:
        o = D.redirect(r, only_ids=ids, seed=11)
        assert o["fx"]["media"] == r["fx"]["media"]
    sym = D.symmetrize(r)
    assert sym["fx"]["media"] == r["fx"]["media"]


def test_lo_del_usuario_encima_de_un_recurso_se_queda():
    """El usuario manda: un titulo suyo sobre el recurso no se quita (solo la IA se aparta)."""
    q = with_media()
    a, b = q["fx"]["media"][0]["start"], q["fx"]["media"][0]["end"]
    q["fx"]["text"] = [{"id": "tx_mio", "preset": "title_pop", "text": "Mi titulo", "start": a + 0.1,
                        "end": b - 0.1, "src": "user", "locked": True, "y": 0.25}]
    p = _direct(q)
    assert any(it["id"] == "tx_mio" for it in p["fx"]["text"])


# ---------------------------------------------------------------- rampas en los cambios de camara
def _air_at(p, fj):
    return [(round(_final(p, s["at"]), 3), s["name"]) for s in p["fx"]["sfx"]
            if D.SFX_CAT.get(s["name"]) == "air" and -0.15 < _final(p, s["at"]) - fj < 0.4]


def _trans_at(p, fj):
    return [it for it in p["fx"]["transitions"] if abs(_final(p, it["at"]) - fj) < 0.3]


def test_rampa_en_union_section_sin_transicion_ni_whoosh():
    base = _direct(montage_plan())
    k = next(i for i, (_t, j, *_r) in enumerate(_LINES) if j == "section")
    fj = _final(base, base["shots"][k]["start"])
    assert _trans_at(base, fj)                         # sin rampa: la seccion lleva transicion
    for field, where in (("ramp", k), ("ramp_in", k), ("ramp_out", k - 1)):
        q = montage_plan()
        q["shots"][where][field] = {"dur": 0.3, "speed": 2.0}
        p = _direct(q)
        assert p["shots"][where].get(field) == {"dur": 0.3, "speed": 2.0}      # viaja a plan.shots
        assert not _trans_at(p, fj), (field, _trans_at(p, fj))
        assert not _air_at(p, fj), (field, _air_at(p, fj))
    # el interruptor del usuario manda: ramp false (o apagada) = la transicion de siempre
    for off in (False, {"dur": 0.3, "speed": 2.0, "enabled": False}):
        q = montage_plan()
        q["shots"][k]["ramp"] = off
        assert _trans_at(_direct(q), fj), off


def test_ramp_join_regla():
    sh = [{"start": 0}, {"start": 1, "ramp": True}, {"start": 2, "ramp_in": {"dur": 0.3, "speed": 2.0}},
          {"start": 3}, {"start": 4, "ramp": False}, {"start": 5}, {"start": 6, "ramp": {"dur": 0}}]
    sh[3]["ramp_out"] = {"dur": 0.3}
    sh[4]["ramp_out"] = {"dur": 0.3}
    # 4: ramp_out de la 3 pero la 4 dice ramp false (el interruptor del usuario manda)
    assert [D.ramp_join(sh, k) for k in range(len(sh))] == [False, True, True, False, False, True, False]
    q = copy.deepcopy(sh)
    q[4]["ramp_out"] = {"dur": 0.3, "enabled": False}
    assert D.ramp_join(q, 5) is False
    assert D.ramp_join(sh, 99) is False
    assert D._Choreo.hard_join({"ramp_join": True}) and D._Choreo.hard_join({"ramp": {"dur": 0.3}})
    assert not D._Choreo.hard_join({"ramp": False}) and not D._Choreo.hard_join({"join": "section"})
    assert {"ramp", "ramp_in", "ramp_out"} <= set(D.SHOT_CARRY)


def test_media_items_ignora_basura():
    p = {"fx": {"media": [None, {"start": 1, "end": 0.5}, {"start": "x", "end": 2},
                          {"start": 1, "end": 2, "enabled": False}, {"start": 1, "end": 2, "id": "ok"}]}}
    assert [m["id"] for m in D.media_items(p)] == ["ok"]
    assert D.media_items({}) == [] and D.media_items({"fx": None}) == []
