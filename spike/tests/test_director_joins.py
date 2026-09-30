"""Director sobre el video compuesto de un MONTAJE CON IA (plan.shots[i].join / setup).

- 'splice' (a mitad de frase) y 'line' (entre lineas de la misma seccion): corte SECO, sin
  transicion ni whoosh; 'section': se permite transicion. Sin montaje: como siempre.
- 'splice' dentro del mismo setup de camara: jump-zoom (la toma entra con el encuadre contrario
  al que cerro la anterior).
- Setups de camara: el VLM describe UNA toma por setup (la mas larga) y la descripcion se reusa.
- join/setup viajan por analyze_shots (tambien desde la cache) hasta plan.shots.

Solo datos SINTETICOS (texto inventado, sin video real ni LLM/VLM).
"""
import copy

import numpy as np
import pytest

from motion import director as D
from test_director import _phrase
from test_director_long import _patch_shots, _scene, nocache  # noqa: F401 (fixture)

FACE = [0.5, 0.33, 0.14, 0.11]

# (texto, union con la toma anterior, setup, pausa antes)
_LINES = [
    ("Abrimos la panadería hace diez años,", None, 0, 0.4),
    ("y hoy horneamos más de mil piezas cada día.", "splice", 0, 0.5),
    ("Cada mañana llegamos a las cinco para prender el horno.", "line", 1, 0.6),
    ("Nuestro pan de elote es el favorito de todo el barrio.", "section", 2, 0.7),
    ("Los domingos vendemos tamales de rajas,", "line", 2, 0.6),
    ("y siempre se acaban antes de las diez de la mañana.", "splice", 3, 0.5),
    ("Ven a visitarnos y pruébalo tú mismo esta semana.", "section", 4, 0.7),
]


def montage_plan(with_montage=True):
    """Plan v1 de un montaje: una toma por fragmento del guion, cortando a la mitad de cada pausa."""
    phrases, words, t = [], [], 0.0
    starts = []
    for k, (txt, _j, _s, pause) in enumerate(_LINES):
        t += pause
        ws, t_end = _phrase(txt, t)
        starts.append(t)
        words += ws
        for i in range(0, len(ws), 3):
            phrases.append(ws[i:i + 3])
        t = t_end
    dur = round(t + 0.8, 3)
    cut = [0.0] + [round((words_end + st) / 2, 3) for words_end, st in zip(
        [max(w["end"] for w in words if w["start"] < s) for s in starts[1:]], starts[1:])] + [dur]
    shots = []
    for k, (_txt, j, setup, _p) in enumerate(_LINES):
        s = {"start": cut[k], "end": cut[k + 1], "person": True, "framing": "medio", "place": "",
             "text_seen": [], "use": "a_camara", "free_space": "arriba", "face": list(FACE), "src": ""}
        if with_montage:
            s.update({"join": j, "setup": setup, "slot": f"s{k + 1:02d}", "cand": "c1"})
        shots.append(s)
    return {"stem": "montaje_sintetico", "video": "", "duration": dur, "dims": [1080, 1920],
            "phrases": phrases, "cuts": [], "beats": [], "camera": [], "highlights": [], "shots": shots}


def _direct(p):
    return D.direct(p, style="dinamico", intensity=0.75, seed=7, use_llm=False, trim=False,
                    music_sync=False)


@pytest.fixture(scope="module")
def directed():
    return _direct(montage_plan())


def _final(plan, t):
    return D._timeline(plan).remap_clamped(float(t))


def test_join_y_setup_llegan_a_plan_shots(directed):
    got = [(s.get("join"), s.get("setup"), s.get("slot")) for s in directed["shots"]]
    assert got == [(j, st, f"s{k + 1:02d}") for k, (_t, j, st, _p) in enumerate(_LINES)]


def test_corte_seco_en_splice_y_line_transicion_en_section(directed):
    p = directed
    trs = [_final(p, it["at"]) for it in p["fx"]["transitions"]]
    air = [(_final(p, it["at"]), it["name"]) for it in p["fx"]["sfx"] if D.SFX_CAT.get(it["name"]) == "air"]
    hard = [s for s in p["shots"] if s.get("join") in ("splice", "line")]
    assert len(hard) == 4
    for s in hard:
        fj = _final(p, s["start"])
        assert not any(abs(fj - t) < 0.3 for t in trs), (s["join"], fj, trs)
        # sin whoosh en el corte (el golpe visual de una transicion ahi no existe)
        assert not any(-0.15 < a - fj < 0.4 for a, _n in air), (s["join"], fj, air)
    for s in (x for x in p["shots"] if x.get("join") == "section"):
        fj = _final(p, s["start"])
        assert any(abs(fj - t) < 0.12 for t in trs), (fj, trs)


def test_sin_montaje_hay_transicion_en_cada_cambio_de_toma():
    p = _direct(montage_plan(with_montage=False))
    trs = [_final(p, it["at"]) for it in p["fx"]["transitions"]]
    for s in p["shots"][1:]:
        fj = _final(p, s["start"])
        assert any(abs(fj - t) < 0.12 for t in trs), (fj, trs)
    assert all("join" not in s and "setup" not in s for s in p["shots"])


def test_campos_vacios_del_montaje_no_cambian_la_edicion():
    """Tomas con join/setup en None (un clip agregado a mano) = la edicion de siempre."""
    a = _direct(montage_plan(with_montage=False))
    q = montage_plan(with_montage=False)
    for s in q["shots"]:
        s.update({"join": None, "setup": None})
    b = _direct(q)
    assert a["fx"] == b["fx"]


def _cam_at(p, t, before):
    """`from` del zoom de camara que termina (before) o empieza en t."""
    cams = [it for it in p["fx"]["effects"] if it.get("kind") == "zoom" and "_cam" in it["id"]]
    if before:
        c = [it for it in cams if abs(float(it["end"]) - t) < 0.06]
    else:
        c = [it for it in cams if abs(float(it["start"]) - t) < 0.06]
    assert c, (t, [(it["start"], it["end"]) for it in cams])
    return float(c[0]["from"])


def test_jump_zoom_en_splice_del_mismo_setup(directed):
    p = directed
    s1 = p["shots"][1]                      # splice, mismo setup 0 que la toma 0
    assert s1["join"] == "splice" and s1["setup"] == p["shots"][0]["setup"]
    a, b = _cam_at(p, s1["start"], True), _cam_at(p, s1["start"], False)
    assert max(a, b) / min(a, b) > 1.03, (a, b)          # un encuadre cerrado y el otro abierto


def test_jump_start_regla():
    ch = D._Choreo.__new__(D._Choreo)
    prev = {"k": 0, "setup": 1, "tight": True}
    assert ch._jump_start({"k": 1, "join": "splice", "setup": 1}, prev) is False
    assert ch._jump_start({"k": 1, "join": "splice", "setup": None}, dict(prev, tight=False)) is True
    assert ch._jump_start({"k": 1, "join": "splice", "setup": 2}, prev) is None     # otro angulo
    assert ch._jump_start({"k": 1, "join": "line", "setup": 1}, prev) is None
    assert ch._jump_start({"k": 2, "join": "splice", "setup": 1}, prev) is None     # no contigua
    assert ch._jump_start({"k": 1, "join": "splice"}, None) is None
    assert D._Choreo.hard_join({"join": "line"}) and D._Choreo.hard_join({"join": "splice"})
    assert not D._Choreo.hard_join({"join": "section"}) and not D._Choreo.hard_join({})


# ---------------------------------------------------------------- VLM por setup
def test_setup_groups_representante_y_orden():
    out = [{"start": 0.0, "end": 2.0, "face": FACE, "setup": 0},
           {"start": 2.0, "end": 6.0, "face": FACE, "setup": 0},
           {"start": 6.0, "end": 7.0, "face": None, "setup": 5},
           {"start": 7.0, "end": 8.0, "face": FACE},
           {"start": 8.0, "end": 9.5, "face": FACE, "setup": 0}]
    g = D.setup_groups(out)
    assert g[0] == [2]                                    # b-roll primero
    assert g[1] == [1, 0, 4]                              # la mas larga del setup la representa
    assert g[2] == [3]                                    # sin setup: sola
    assert sorted(i for gg in g for i in gg) == list(range(5))


def _setup_scan(setups, L=6.0):
    """Escaneo sintetico: cada setup con su color (mismo setup = mismo cuadro)."""
    n = len(setups)
    cols = {s: ((53 * (s + 1)) % 256, (97 * (s + 2)) % 256, (151 * (s + 3)) % 256) for s in set(setups)}
    med_t = [j * 0.5 for j in range(int(n * L * 2))]
    med = [_scene(cols[setups[min(n - 1, int(t // L))]]) for t in med_t]
    sc = {"fps": 30.0, "med": med, "med_t": med_t, "diff": np.zeros(int(n * L * 30), np.float32),
          "duration": n * L}
    lens = [L * (1.0 + 0.1 * (i % 3)) for i in range(n)]
    shots, t = [], 0.0
    for i in range(n):
        shots.append({"start": round(t, 3), "end": round(t + lens[i], 3)})
        t += lens[i]
    return sc, shots


def test_vlm_una_descripcion_por_setup_en_un_video_corto(monkeypatch, tmp_path, nocache):  # noqa: F811
    setups = [0, 0, 1, 2, 2, 0, 1]
    sc, shots = _setup_scan(setups)
    for i, s in enumerate(shots):
        s.update({"setup": setups[i], "join": None if i == 0 else ("section" if i % 3 == 0 else "line"),
                  "slot": f"s{i}", "cand": "c1"})
    seen = []

    def vlm(img, timeout=30.0):
        c = tuple(int(v) for v in img[0, 0])
        seen.append(c)
        return {"person": True, "framing": "medio", "place": f"lugar {c}", "text_seen": [],
                "free_space": "arriba", "action": ""}
    video = _patch_shots(monkeypatch, ["x"] * len(shots), vlm, tmp_path)
    dur = shots[-1]["end"]
    assert dur < D.LONG_S                                   # corto: antes, una llamada por toma
    plan = {"video": str(video), "duration": dur, "phrases": [], "shots": copy.deepcopy(shots)}
    st = {}
    out = D.analyze_shots(plan, use_llm=True, scan=sc, stats=st)
    assert len(seen) == 3 and st["vlm_groups"] == 3 and st["vlm_reused"] == len(shots) - 3
    assert all(o["vlm"] for o in out)
    by_setup = {}
    for o in out:
        by_setup.setdefault(o["setup"], set()).add(o["place"])
    assert all(len(v) == 1 for v in by_setup.values()) and len(by_setup) == 3
    assert [(o["join"], o["slot"]) for o in out[1:]] == [(s["join"], s["slot"]) for s in shots[1:]]
    assert "join" not in out[0]                              # la primera no trae union
    assert st["matte_measured"] == 3                         # una medicion por setup


def test_cache_de_tomas_no_arrastra_joins_viejos(monkeypatch, tmp_path):
    """Una cache de tomas (mismos limites) nunca impone las uniones/setups de otra direccion."""
    sc, shots = _setup_scan([0, 1, 2])
    video = _patch_shots(monkeypatch, ["x"] * 3, lambda img, timeout=30.0: None, tmp_path)
    store = {}
    monkeypatch.setattr(D, "_cache_get", lambda kind, key: copy.deepcopy(store.get((kind, key))))
    monkeypatch.setattr(D, "_cache_put", lambda kind, key, val: store.__setitem__((kind, key), copy.deepcopy(val)))
    for i, s in enumerate(shots):
        s.update({"setup": i, "join": None if i == 0 else "splice"})
    D.analyze_shots({"video": str(video), "duration": shots[-1]["end"], "phrases": [],
                     "shots": copy.deepcopy(shots)}, use_llm=False, scan=sc)
    assert store                                              # se guardo (sin VLM)
    plain = [{"start": s["start"], "end": s["end"]} for s in shots]
    out = D.analyze_shots({"video": str(video), "duration": shots[-1]["end"], "phrases": [],
                           "shots": plain}, use_llm=False, scan=sc)
    assert all("join" not in o and "setup" not in o for o in out)
    again = D.analyze_shots({"video": str(video), "duration": shots[-1]["end"], "phrases": [],
                             "shots": copy.deepcopy(shots)}, use_llm=False, scan=sc)
    assert [o.get("join") for o in again] == [None, "splice", "splice"]
