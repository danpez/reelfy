"""Director en videos LARGOS (motion/director.py, modo largo): guion por tramos, VLM por grupos de
tomas, presupuesto sub-lineal y escaneo que escala.

Caso real que lo motivo (19 min 4K H.264, 2541 palabras): el escaneo con VideoToolbox tardaba ~6-8
min (VT decodifica ese H.264 a ~2.4x tiempo real; por software ~10x), el VLM nunca corria (el
presupuesto de 50 s ya se habia ido) y el guion iba en UNA llamada imposible (5 k tokens de prompt +
~7 k de salida en un contexto de 8192): todo quedaba 'heuristico'.

- Tramos: oraciones completas, equilibrados, deterministas; cada uno con su prompt (`part`),
  num_predict proporcional y timeout por tokens esperados; union determinista (ids globales, ids
  locales remapeados, tema del primero, cta/link del ultimo); un tramo que falla solo deja SUS
  frases con heuristicas; el `deadline` corta los tramos que no alcanzan (reloj falso, sin
  esperas reales).
- Videos cortos: UNA llamada con el prompt de siempre (sin 'Tramo', con la regla del hook).
- Tomas: grupos de tomas casi iguales (b-roll primero), una descripcion del VLM por grupo que se
  reusa en sus tomas, tope de llamadas y de tiempo, piso tras un escaneo largo, turno FIFO del
  modelo entre guion y VLM.
- Escaneo: decodificador por codec (H.264 por software, HEVC con VideoToolbox), cadencia de
  cuadros medianos acotada (MED_MAX) y diferencias por bloques identicas a las de siempre.
- Presupuesto: 50 s hasta LONG_S, raiz de la duracion despues, con tope.

Solo datos SINTETICOS (texto generado, LLM/VLM falsos): no hace falta ollama ni video real.
"""
import json
import random
import re
import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

from conftest import load_factor
from motion import director as D
import test_director as TD


# ---------------------------------------------------------------- guion largo sintetico
_VOCAB = ("hoy vamos hablar perfume madera ambar vainilla salida fondo notas cuero jazmin "
          "rosa pimienta cedro almizcle intenso fresco dulce duracion estela proyeccion botella "
          "precio tienda invierno verano noche oficina regalo clasico moderno coleccion marca "
          "diseno aroma piel cambia mejora resultado sientes probamos comparamos elegimos quiero "
          "puedes recomiendo encanta tiene llega dura queda funciona gusta mezcla abre cierra").split()
_OPEN = ("Entonces", "Bueno", "Ahora", "Mira", "Yo", "Pero", "Hoy", "Ademas")


def long_plan(n_words: int = 2500, seed: int = 3, wps: float = 2.2) -> dict:
    """Plan v1 sintetico de ~n_words palabras en espanol con puntuacion (oraciones de 6-40
    palabras) a ~wps palabras/s: 2500 palabras ~ 19 min."""
    rnd = random.Random(seed)
    words, t = [], 0.4
    while len(words) < n_words:
        n = rnd.randint(6, 40)
        toks = [rnd.choice(_OPEN)] + [rnd.choice(_VOCAB) for _ in range(n - 1)]
        toks[-1] += rnd.choice((".", ".", "?", "!"))
        for tok in toks:
            d = max(0.16, 1.0 / wps - 0.08)
            words.append({"text": tok, "start": round(t, 3), "end": round(t + d, 3)})
            t += d + 0.08
        t += 0.5
    words = words[:n_words]
    words[-1]["text"] = words[-1]["text"].rstrip(".?!") + "."
    phrases = [words[k:k + 3] for k in range(0, len(words), 3)]
    dur = round(words[-1]["end"] + 1.0, 3)
    return {"stem": "largo_sintetico", "video": "", "duration": dur, "dims": [1920, 1080],
            "phrases": phrases, "cuts": [], "beats": [], "camera": [], "highlights": [],
            "shots": [{"start": 0.0, "end": dur, "person": True, "framing": "medio", "place": "",
                       "text_seen": [], "use": "a_camara", "free_space": "arriba",
                       "face": [0.5, 0.35, 0.12, 0.2]}]}


def _sents(plan):
    return D._sentences(D._flatten_words(plan))


_LINE = re.compile(r"^(\d+) \| [\d.]+-[\d.]+ \| (.+)$", re.M)


class FakeLLM:
    """_ollama_chat falso: responde el JSON del esquema con una entrada por frase del prompt.
    `fail`: indices de llamada que fallan (timeout); `local_ids`: numera el tramo desde 0;
    `clock`: reloj falso que avanza `cost` s por llamada."""

    def __init__(self, fail=(), local_ids=False, clock=None, cost=10.0, tone="alegre"):
        self.calls, self.fail, self.local_ids = [], set(fail), local_ids
        self.clock, self.cost, self.tone = clock, cost, tone
        self.lock = threading.Lock()

    def __call__(self, messages, fmt="json", model=None, num_ctx=4096, temperature=0.1,
                 num_predict=900, timeout=60.0):
        user = messages[-1]["content"]
        with self.lock:
            k = len(self.calls)
            self.calls.append({"prompt": user, "num_predict": num_predict, "timeout": timeout,
                               "num_ctx": num_ctx})
        if self.clock is not None:
            self.clock.advance(self.cost)
        if k in self.fail:
            raise TimeoutError("timed out")
        rows = _LINE.findall(user.split("\nTomas:")[0].split("\nDevuelve:")[0])
        part = re.search(r"Tramo (\d+) de (\d+)", user)
        kk = int(part.group(1)) - 1 if part else 0
        f = []
        for j, (sid, text) in enumerate(rows):
            toks = text.split()
            f.append({"id": j if self.local_ids else int(sid), "sec": "beneficio",
                      "title": " ".join(toks[1:3]), "emph": [toks[-1].strip(".?!")], "fx": "titulo",
                      "icon": ["star"], "nrg": "alta", "tone": self.tone})
        ent = {k_: "" for k_ in D.LLM_ENT_KEYS}
        ent["role"] = "perfumista" if kk == 0 else "vendedor"
        ent["link_label"] = f"Boton {kk}"
        ent["cta"] = f"sigueme tramo {kk}"
        return json.dumps({"topic": f"tema {kk}", "ent": ent, "f": f})


class Clock:
    """Reloj falso para D._now: avanza solo cuando el LLM/VLM falso 'trabaja'."""

    def __init__(self, t=1000.0):
        self.t = t
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.t

    def advance(self, s):
        with self.lock:
            self.t += s


@pytest.fixture
def nocache(monkeypatch):
    monkeypatch.setattr(D, "_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(D, "_cache_put", lambda *a, **k: None)
    monkeypatch.setattr(D, "llm_available", lambda *a, **k: True)


# ---------------------------------------------------------------- tramos
def test_windows_cover_every_sentence_once_in_order():
    sents = _sents(long_plan())
    total = D._n_words(sents)
    assert 2400 <= total <= 2500 and len(sents) > 60
    wins = D.llm_windows(sents)
    flat = [s["id"] for w in wins for s in w]
    assert flat == [s["id"] for s in sents]                      # todas, una vez, en orden
    assert len(wins) == pytest.approx(total / D.LLM_WIN_WORDS, abs=1.01)
    for w in wins:
        n = D._n_words(w)
        assert n <= 1.3 * D.LLM_WIN_WORDS or len(w) == 1          # acotado (salvo oracion gigante)
    sizes = [D._n_words(w) for w in wins[:-1]]
    assert max(sizes) - min(sizes) <= 0.8 * D.LLM_WIN_WORDS        # equilibrados
    assert D.llm_windows(sents) == wins                           # deterministas


def test_giant_sentence_goes_alone():
    """Whisper sin puntos en una toma larga: una 'oracion' de 356 palabras va sola en su tramo."""
    p = long_plan(900)
    sents = _sents(p)
    giant = {"id": 999, "w0": 0, "w1": 355, "t0": 0.0, "t1": 1.0, "text": "x"}
    wins = D.llm_windows(sents[:3] + [giant] + sents[3:6])
    assert [giant] in wins


def test_window_prompt_and_limits_scale_with_the_window():
    sents = _sents(long_plan())
    wins = D.llm_windows(sents)
    p0 = D._llm_prompt(wins[0], None, part=(0, len(wins)))
    p1 = D._llm_prompt(wins[1], None, part=(1, len(wins)))
    assert f"Tramo 1 de {len(wins)}" in p0 and 'La primera frase es "hook"' in p0
    assert f"Tramo 2 de {len(wins)}" in p1 and 'La primera frase es "hook"' not in p1
    assert "NO es el hook" in p1 and "compacto" in p1
    # solo las frases del tramo (acotado), no el guion entero
    assert len(p1) < len(D._llm_prompt(sents, None)) / 3
    assert all(f"\n{s['id']} | " in "\n" + p1 for s in wins[1])
    # timeout segun tokens esperados: crece con las frases, no es un 55 fijo
    short, big = D.llm_timeout(p1, 2), D.llm_timeout(p1, 20)
    assert 15 < short < big and big > 55


def test_short_video_keeps_the_single_call_and_its_prompt(monkeypatch, nocache):
    """Video corto: UNA llamada, prompt de siempre (sin tramos) y num_predict de siempre."""
    fake = FakeLLM()
    monkeypatch.setattr(D, "_ollama_chat", fake)
    plan = TD.synthetic_plan()
    out = D.direct(plan, use_llm=True, trim=False, music_sync=False)
    assert len(fake.calls) == 1
    c = fake.calls[0]
    sents = _sents(plan)
    assert "Tramo" not in c["prompt"] and 'La primera frase es "hook"' in c["prompt"]
    assert c["prompt"] == D._llm_prompt(sents, plan["shots"])      # el de siempre, con sus tomas
    assert c["num_predict"] == 160 + 90 * len(sents) and c["timeout"] >= 55.0
    dd = out["director"]
    assert dd["llm"] != "heuristico"
    assert "llm_windows" not in dd["timings"] and "budget_s" not in dd["timings"]


def test_long_script_is_windowed_and_merged(monkeypatch, nocache):
    fake = FakeLLM()
    monkeypatch.setattr(D, "_ollama_chat", fake)
    plan = long_plan()
    sents = _sents(plan)
    wins = D.llm_windows(sents)
    t0 = time.perf_counter()
    out = D.direct(plan, use_llm=True, trim=False, music_sync=False)
    wall = time.perf_counter() - t0
    dd = out["director"]
    assert len(fake.calls) == len(wins) > 1
    for k, c in enumerate(fake.calls):
        assert f"Tramo {k + 1} de {len(wins)}" in c["prompt"]
        n = len(wins[k])
        assert c["num_predict"] == 200 + 130 * n                  # proporcional al tramo
        assert c["num_ctx"] == 8192 and c["timeout"] <= D.llm_timeout(c["prompt"], n) + 1e-6
    assert dd["llm"] != "heuristico"
    tm = dd["timings"]
    assert tm["llm_windows"] == len(wins) and tm["llm_windows_ok"] == len(wins)
    assert tm["budget_s"] == pytest.approx(D.director_budget(plan["duration"]), abs=0.1)
    # union: cada frase trae lo del LLM (tono falso 'alegre', energia 'alta')
    ss = dd["script"]["sentences"]
    assert len(ss) == len(sents)
    assert all(s["energy"] == "alta" for s in ss)
    # tono del LLM salvo lo que las heuristicas protegen (humor, intriga de una pregunta)
    assert all(s["tone"] == "alegre" for s in ss if "?" not in s["text"] and s["tone"] != "humor")
    assert dd["script"]["topic"] == "tema 0"                      # tema del primer tramo
    r = D._llm_script_windows(sents, None)                       # union de entidades
    assert r["ent"]["role"] == "perfumista"                       # primer valor no vacio
    assert r["ent"]["cta"] == f"sigueme tramo {len(wins) - 1}"    # el llamado a la accion: el ultimo
    assert wall < 90 * load_factor()                              # 19 min dirigidos sin modelo real
    # el plan es valido y trae edicion en todo el video (no solo al inicio)
    tl = D._timeline(out)
    ts = [tl.remap_clamped(float(it["start"])) for it in out["fx"]["text"]]
    assert ts and max(ts) > 0.8 * tl.final_duration


def test_merge_windows_is_deterministic():
    a = {"topic": "", "ent": {"role": "", "cta": "a", "link_label": "L1", "name": "Ana"},
         "f": [{"id": 0}, {"id": 1}]}
    b = {"topic": "tema b", "ent": {"role": "chef", "cta": "b", "link_label": "", "name": "Eva"},
         "f": [{"id": 1}, {"id": 2}]}
    m = D.merge_windows([a, None, b])
    assert [e["id"] for e in m["f"]] == [0, 1, 2]                 # sin duplicados, en orden
    assert m["topic"] == "tema b" and m["ent"]["role"] == "chef" and m["ent"]["name"] == "Ana"
    assert m["ent"]["cta"] == "b" and m["ent"]["link_label"] == "L1"
    assert D.merge_windows([None, None]) is None
    assert D.merge_windows([a, None, b]) == m


def test_failed_window_only_falls_back_that_window(monkeypatch, nocache):
    """El tramo 2 falla (timeout): sus frases quedan EXACTAMENTE con las heuristicas; el resto con IA."""
    plan = long_plan()
    sents = _sents(plan)
    wins = D.llm_windows(sents)
    heur = {s["id"]: s["info"] for s in D.analyze_script(plan, use_llm=False)["sents"]}
    fake = FakeLLM(fail={2})
    monkeypatch.setattr(D, "_ollama_chat", fake)
    box = {}
    r = D._llm_script_windows(sents, None, box=box)
    assert len(fake.calls) == len(wins)                           # no se detiene por un fallo
    assert box["stat"]["failed"] == 1 and box["stat"]["ok"] == len(wins) - 1
    sc = D.analyze_script(plan, use_llm=False, llm_result=r)
    bad = {s["id"] for s in wins[2]}
    for s in sc["sents"]:
        if s["id"] in bad:
            assert s["info"]["energy"] == heur[s["id"]]["energy"]
            assert s["info"]["tone"] == heur[s["id"]]["tone"]
        else:
            assert s["info"]["energy"] == "alta"
    assert sc["llm"] is True
    # en direct: nota del tramo que quedo con heuristicas
    fake2 = FakeLLM(fail={2})
    monkeypatch.setattr(D, "_ollama_chat", fake2)
    out = D.direct(plan, use_llm=True, trim=False, music_sync=False)
    assert out["director"]["llm"] != "heuristico"
    assert out["director"]["timings"]["llm_windows_ok"] == len(wins) - 1
    assert any("tramos con IA" in n for n in out["director"]["notes"])


def test_local_ids_are_remapped(monkeypatch, nocache):
    fake = FakeLLM(local_ids=True)
    monkeypatch.setattr(D, "_ollama_chat", fake)
    sents = _sents(long_plan())
    r = D._llm_script_windows(sents, None)
    assert sorted(int(e["id"]) for e in r["f"]) == [s["id"] for s in sents]


def test_all_windows_fail_means_heuristic(monkeypatch, nocache):
    plan = long_plan(1200)
    fake = FakeLLM(fail=set(range(100)))
    monkeypatch.setattr(D, "_ollama_chat", fake)
    out = D.direct(plan, use_llm=True, trim=False, music_sync=False)
    assert out["director"]["llm"] == "heuristico"
    assert out["director"]["timings"]["llm_windows_ok"] == 0


def test_windows_stop_at_the_deadline(monkeypatch, nocache):
    """Reloj falso: cada tramo 'tarda' 20 s; con 65 s de plazo caben 3 y el resto se salta sin
    llamar al modelo (ni esperar)."""
    clock = Clock()
    monkeypatch.setattr(D, "_now", clock)
    fake = FakeLLM(clock=clock, cost=20.0)
    monkeypatch.setattr(D, "_ollama_chat", fake)
    sents = _sents(long_plan())
    wins = D.llm_windows(sents)
    box = {}
    t0 = time.perf_counter()
    r = D._llm_script_windows(sents, None, deadline=clock() + 65.0, box=box)
    assert time.perf_counter() - t0 < 5.0
    st = box["stat"]
    assert st["ok"] == 3 and st["skipped"] == len(wins) - 3 and len(fake.calls) == 3
    got = {int(e["id"]) for e in r["f"]}
    assert got == {s["id"] for w in wins[:3] for s in w}          # el hook primero
    # cada llamada recibio un timeout recortado al plazo
    assert all(c["timeout"] <= 65.0 for c in fake.calls)


def test_partial_windows_are_used_when_the_director_stops_waiting(monkeypatch, nocache):
    """El director deja de esperar al guion en su presupuesto: usa los tramos que YA respondieron
    (parcial > todo heuristico) aunque el hilo siga."""
    gate = threading.Event()
    real = FakeLLM()
    calls = []

    def slow(messages, **kw):
        calls.append(1)
        if len(calls) > 2:
            gate.wait(10)                      # del tercer tramo en adelante: 'colgado'
            raise TimeoutError("timed out")
        return real(messages, **kw)
    monkeypatch.setattr(D, "_ollama_chat", slow)
    monkeypatch.setattr(D, "llm_timeout", lambda prompt, n: 0.5)   # tramos 'cortos': si se llaman
    monkeypatch.setenv("REELFY_DIRECTOR_BUDGET", "0.5")
    try:
        out = D.direct(long_plan(), use_llm=True, trim=False, music_sync=False)
    finally:
        gate.set()
    dd = out["director"]
    assert dd["llm"] != "heuristico"
    assert dd["timings"]["llm_windows_ok"] == 2
    assert any("tramos con IA" in n for n in dd["notes"])


# ---------------------------------------------------------------- presupuesto
def test_budget_is_the_same_for_short_and_sublinear_for_long(monkeypatch):
    monkeypatch.delenv("REELFY_DIRECTOR_BUDGET", raising=False)
    assert D.director_budget(30) == D.director_budget(D.LONG_S) == 50.0
    b5, b10, b19 = D.director_budget(300), D.director_budget(600), D.director_budget(1146)
    assert 80 < b5 < b10 < b19 < 200
    assert b19 / b5 < 1146 / 300 / 1.5                            # sub-lineal
    assert D.director_budget(10 * 3600) == 50.0 * D.BUDGET_MAX_X  # tope


# ---------------------------------------------------------------- tomas / VLM
def _scene(color, h=64, w=36):
    return np.full((h, w, 3), color, np.uint8)


def _long_scan(n_shots=40, dur=1200.0, broll_every=10):
    """Escaneo sintetico (sin video): tomas alternando DOS escenas a camara (mismo rostro) y un
    b-roll cada `broll_every` tomas, cuadros medianos a 2 fps."""
    L = dur / n_shots
    kinds = ["b" if i % broll_every == broll_every - 1 else ("x" if i % 2 else "y") for i in range(n_shots)]
    col = {"x": (200, 40, 40), "y": (40, 40, 200), "b": (30, 160, 30)}
    med_t = [j * 0.5 for j in range(int(dur * 2))]
    med = [_scene(col[kinds[min(n_shots - 1, int(t // L))]]) for t in med_t]
    sc = {"fps": 30.0, "med": med, "med_t": med_t, "diff": np.zeros(int(dur * 30), np.float32),
          "duration": dur}
    shots = [{"start": round(i * L, 3), "end": round((i + 1) * L, 3)} for i in range(n_shots)]
    return sc, shots, kinds


def _patch_shots(monkeypatch, kinds, vlm_fn, tmp_path):
    face = [0.5, 0.35, 0.12, 0.2]
    monkeypatch.setattr(D, "_shot_faces", lambda sc_, shots: [
        {"face": None if k == "b" else face, "face_ratio": 0.0 if k == "b" else 1.0,
         "face_spread": 0.0, "track": []} for k in kinds[:len(shots)]])
    monkeypatch.setattr(D, "_matte_quality", lambda v, s: 0.97)
    monkeypatch.setattr(D, "_grab_frame", lambda *a, **k: None)
    monkeypatch.setattr(D, "_vlm_describe", vlm_fn)
    video = tmp_path / "largo.mp4"
    video.write_bytes(b"0")
    return video


def test_vlm_describes_one_shot_per_group_and_reuses(monkeypatch, tmp_path, nocache):
    sc, shots, kinds = _long_scan()
    seen = []

    def vlm(img, timeout=30.0):
        c = tuple(int(v) for v in img[0, 0])
        seen.append(c)
        return {"person": c != (30, 160, 30), "framing": "medio", "place": f"lugar {c}",
                "text_seen": ["LETRERO"] if c == (30, 160, 30) else [], "free_space": "arriba",
                "action": ""}
    video = _patch_shots(monkeypatch, kinds, vlm, tmp_path)
    plan = {"video": str(video), "duration": 1200.0, "phrases": [], "shots": shots}
    st = {}
    D._VLM_STATUS.clear()
    out = D.analyze_shots(plan, use_llm=True, scan=sc, stats=st)
    assert len(out) == 40
    assert len(seen) == 3 and st["vlm_calls"] == 3 and st["vlm_groups"] == 3   # 3 escenas
    assert seen[0] == (30, 160, 30)                               # el b-roll primero
    assert all(o["vlm"] for o in out) and st["vlm_reused"] == 37
    assert {o["place"] for o in out if o["use"] == "broll"} == {"lugar (30, 160, 30)"}
    assert all(o["text_seen"] == ["LETRERO"] for o in out if o["use"] == "broll")
    assert all(o["matte_q"] == 0.97 for o in out if o["face"])
    assert st["matte_measured"] == 2                              # una medicion por grupo con cara
    assert str(video) not in D._VLM_STATUS                        # todo descrito: sin causa


def test_vlm_groups_respect_the_time_budget(monkeypatch, tmp_path, nocache):
    """Muchas escenas distintas y un VLM lento (reloj falso): se describe lo que cabe en el plazo
    (b-roll primero), el resto queda con heuristicas y la causa queda registrada."""
    clock = Clock()
    monkeypatch.setattr(D, "_now", clock)
    n = 60
    L = 20.0
    kinds = ["b" if i % 3 == 0 else "x" for i in range(n)]
    med_t = [j * 0.5 for j in range(int(n * L * 2))]
    med = [_scene(((37 * int(t // L)) % 256, (91 * int(t // L)) % 256, (153 * int(t // L)) % 256))
           for t in med_t]                                        # cada toma de otro color
    sc = {"fps": 30.0, "med": med, "med_t": med_t, "diff": np.zeros(int(n * L * 30), np.float32),
          "duration": n * L}
    shots = [{"start": i * L, "end": (i + 1) * L} for i in range(n)]

    def vlm(img, timeout=30.0):
        clock.advance(8.0)
        return {"person": False, "framing": "general", "place": "x", "text_seen": [],
                "free_space": "arriba", "action": ""}
    video = _patch_shots(monkeypatch, kinds, vlm, tmp_path)
    plan = {"video": str(video), "duration": n * L, "phrases": [], "shots": shots}
    st = {}
    D._VLM_STATUS.clear()
    out = D.analyze_shots(plan, use_llm=True, scan=sc, deadline=clock() + 60.0, stats=st)
    assert st["vlm_groups"] > 20
    assert 1 <= st["vlm_calls"] <= 60 / 8 + 1                     # lo que cabe en el plazo
    assert st["vlm_calls"] <= D.vlm_max_calls(n * L)
    described = [o for o in out if o["vlm"]]
    assert described and all(o["use"] == "broll" for o in described)   # b-roll primero
    assert "tiempo agotado" in D._VLM_STATUS.get(str(video), "")
    assert sum(1 for o in out if not o["vlm"]) == st["vlm_missing"] > 0


def test_vlm_gets_a_floor_after_a_long_scan(monkeypatch, tmp_path, nocache):
    """El escaneo se comio el presupuesto (deadline ya pasado): el VLM aun describe lo mas
    importante (piso VLM_LONG_MIN) en vez de quedar todo heuristico."""
    sc, shots, kinds = _long_scan(n_shots=6, dur=600.0, broll_every=3)
    calls = []
    video = _patch_shots(monkeypatch, kinds, lambda img, timeout=30.0: calls.append(timeout) or {
        "person": True, "framing": "medio", "place": "sala", "text_seen": [], "free_space": "arriba",
        "action": ""}, tmp_path)
    plan = {"video": str(video), "duration": 600.0, "phrases": [], "shots": shots}
    out = D.analyze_shots(plan, use_llm=True, scan=sc, deadline=time.perf_counter() - 30.0)
    assert calls and any(o["vlm"] for o in out)


def test_short_video_vlm_is_unchanged(monkeypatch, tmp_path, nocache):
    """Video corto: una descripcion por toma (sin grupos), aunque las tomas se parezcan."""
    sc, shots, kinds = _long_scan(n_shots=6, dur=60.0, broll_every=3)
    calls = []
    video = _patch_shots(monkeypatch, kinds, lambda img, timeout=30.0: calls.append(1) or {
        "person": True, "framing": "medio", "place": "sala", "text_seen": [], "free_space": "arriba",
        "action": ""}, tmp_path)
    st = {}
    D.analyze_shots({"video": str(video), "duration": 60.0, "phrases": [], "shots": shots},
                    use_llm=True, scan=sc, stats=st)
    assert len(calls) == 6 and "vlm_groups" not in st and "matte_measured" not in st


def test_model_turn_is_fifo_and_times_out():
    turn = D._ModelTurn()
    order = []
    assert turn.acquire(1.0)
    th = threading.Thread(target=lambda: (turn.acquire(5.0), order.append("vlm"), turn.release()))
    th.start()
    t_end = time.perf_counter() + 5.0
    while not turn._q and time.perf_counter() < t_end:          # el otro hilo ya espera su turno
        time.sleep(0.01)
    turn.release()
    # el hilo que libera y vuelve a pedir el turno va DETRAS del que ya esperaba
    assert turn.acquire(5.0)
    order.append("guion")
    turn.release()
    th.join(5)
    assert order == ["vlm", "guion"]
    assert turn.acquire(1.0)
    t0 = time.perf_counter()
    assert turn.acquire(0.2) is False and time.perf_counter() - t0 < 2.0
    turn.release()
    assert turn.acquire(0.5)                                      # el turno abandonado no bloquea
    turn.release()


def test_turn_call_skips_when_no_time_is_left():
    assert D._turn_call(None, time.perf_counter() + 1.0, 30.0, lambda to: "x", min_s=4.0) is None
    assert D._turn_call(None, time.perf_counter() + 60.0, 30.0, lambda to: to, min_s=4.0) == 30.0


# ---------------------------------------------------------------- escaneo
def test_decoder_choice_by_codec(monkeypatch):
    monkeypatch.setattr(D._paths, "IS_MAC", True, raising=False)
    monkeypatch.setattr(D, "_probe_codec", lambda v: "h264")
    assert D._scan_decoders("v.mp4") == [[], ["-hwaccel", "videotoolbox"]]
    monkeypatch.setattr(D, "_probe_codec", lambda v: "hevc")
    assert D._scan_decoders("v.mp4") == [["-hwaccel", "videotoolbox"], []]
    monkeypatch.setattr(D._paths, "IS_MAC", False, raising=False)
    assert D._scan_decoders("v.mp4") == [[]]


def test_frame_diffs_match_the_whole_array_computation():
    rng = np.random.default_rng(5)
    for n in (0, 1, 2, 50, 4097, 9001):
        s = rng.integers(0, 256, (n, 48, 48), dtype=np.uint8)
        ref = np.abs(np.diff(s.astype(np.float32), axis=0)).mean(axis=(1, 2)) if n > 1 else np.zeros(0)
        assert np.array_equal(D._frame_diffs(s), ref)


@pytest.mark.skipif(not shutil.which(D._ffmpeg()) and not __import__("os").path.exists(D._ffmpeg()),
                    reason="sin ffmpeg")
def test_scan_caps_medium_frames_on_long_videos(monkeypatch, tmp_path):
    """Con MED_MAX cuadros como maximo la cadencia baja y med_t sigue a la cadencia real."""
    v = tmp_path / "sint.mp4"
    r = subprocess.run([D._ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=320x180:rate=30:duration=8", "-pix_fmt", "yuv420p", str(v)],
                       capture_output=True)
    if r.returncode != 0:
        pytest.skip("ffmpeg sin lavfi")
    monkeypatch.setattr(D, "_work_dir", lambda: tmp_path)
    sc = D.scan_video(str(v), 8.0)
    assert len(sc["med"]) == pytest.approx(16, abs=1) and sc["med_fps"] == 2.0
    assert sc["med_t"][:3] == [0.0, 0.5, 1.0] and len(sc["diff"]) == pytest.approx(239, abs=2)
    monkeypatch.setattr(D, "MED_MAX", 8)
    sc2 = D.scan_video(str(v), 8.0)
    assert len(sc2["med"]) == pytest.approx(8, abs=1) and sc2["med_fps"] == 1.0
    assert sc2["med_t"][:3] == [0.0, 1.0, 2.0]
    assert np.array_equal(sc2["diff"], sc["diff"])                # el flujo de 30 fps no cambia
    assert sc["decoder"] in ("software", "videotoolbox")
    # con avance (video largo): mismos datos y fracciones crecientes hasta ~1
    fr = []
    sc3 = D.scan_video(str(v), 8.0, on_progress=fr.append)
    assert np.array_equal(sc3["diff"], sc2["diff"]) and len(sc3["med"]) == len(sc2["med"])
    assert fr and fr == sorted(fr) and fr[-1] > 0.9
