"""Tests de motion/music.py: prompts, localizacion/verificacion de modelos, analisis (tempo sin
errores de octava, pulso, rejilla en intros sin bateria, downbeats, drop), ajuste (duracion exacta,
hook con energia, drop coherente, costuras ocultas en stops y en el golpe del drop, finales), cache
(crudos reusables al cambiar la duracion), fallbacks, cancelacion y cfg tolerante.

- Crudos "tipo ACE" sinteticos (intro de pads + plucks a contratiempo SIN bateria, luego drop con
  bateria, remate y silencio final) con verdad conocida: prueban el analisis sin circularidad
  con el procedural.
- Crudos ACE reales (generados por Reelfy) en spike/work/fixtures/music (ignorado por git): si
  estan, se prueban contra referencias verificadas a mano; si no, esos tests se saltan.
- La generacion real con ACE-Step solo corre con REELFY_TEST_ACE=1 (~20-40 s)."""
import json
import os
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from motion import music as M

SR = M.SR
FIX = Path(__file__).resolve().parents[1] / "work" / "fixtures" / "music"
DIRECTOR_PROMPTS = {  # los que emite director._music_cfg (estilo -> prompt, bpm)
    "dinamico": ("upbeat modern pop instrumental, bright plucks, punchy drums, positive and confident, "
                 "social media ad, for a video about limpieza dental", 112),
    "elegante": ("warm elegant instrumental, soft piano and strings, lo-fi texture, calm premium mood, "
                 "for a video about limpieza dental", 88),
    "minimal": ("minimal ambient electronic instrumental, soft pulses, clean and airy, for a video about "
                "limpieza dental", 100),
    "energetico": ("high energy electronic trap instrumental, hype, heavy 808, big drops, for a video about "
                   "limpieza dental", 128),
}


# ---------------------------------------------------------------- utilidades
def _fake_ace_raw(dur: float, bpm: float = 112.0, seed: int = 3, tail_sil: float = 0.13) -> np.ndarray:
    """Crudo tipo ACE a partir del procedural: cancion con remate y ~13% de silencio al final."""
    music_len = dur * (1 - tail_sil)
    y, _ = M.synth_procedural(music_len, "dinamico", "upbeat", bpm, "A major", seed, hook=False)
    out = np.zeros((int(round(dur * SR)), 2), np.float32)
    out[: len(y)] = y * 0.8
    rng = np.random.default_rng(0)
    out[len(y):] = rng.standard_normal((len(out) - len(y), 2)).astype(np.float32) * 3e-4
    return out


def _ace_like(bpm: float = 112.0, intro_bars: int = 6, drop_bars: int = 10, seed: int = 1,
              tail_sil: float = 3.0, g0: float = 0.3) -> tuple[np.ndarray, dict]:
    """Crudo sintetico con la forma de ACE: intro de pads + plucks A CONTRATIEMPO sin bateria
    (-18 dB), drop con bombo en negras, clap en 2 y 4, bajo en corcheas; remate en downbeat y
    silencio de cola. Verdad: beats g0 + kT, downbeats cada 4, drop y remate."""
    T = 60.0 / bpm
    bar = 4 * T
    t_drop = g0 + intro_bars * bar
    t_end = t_drop + drop_bars * bar
    D = t_end + 1.6 + tail_sil
    n = int(round(D * SR))
    y = np.zeros(n)
    rng = np.random.default_rng(seed)
    syn = M._Synth(SR, rng, "pop")
    dr = syn.drums
    t = np.arange(n) / SR
    chords = [(57, 61, 64), (54, 57, 61), (50, 54, 57), (52, 56, 59)]   # A F#m D E

    def place(sig, at, g):
        i = int(round(at * SR))
        m = min(len(sig), n - i)
        if m > 0 and i >= 0:
            y[i:i + m] += sig[:m] * g

    # intro: pads por compas + plucks en los contratiempos
    for b in range(intro_bars):
        a = g0 + b * bar
        ch = chords[b % 4]
        seg = (t >= a) & (t < a + bar)
        env = np.clip((t[seg] - a) / 0.3, 0, 1)
        y[seg] += 0.05 * env * sum(np.sin(2 * np.pi * M._mf(m) * t[seg]) for m in ch)
        for k in range(4):
            tt = np.arange(int(0.25 * SR)) / SR
            pl = np.sin(2 * np.pi * M._mf(ch[k % 3] + 12) * tt) * np.exp(-tt / 0.06)
            place(pl, a + k * T + T / 2, 0.06)
    # drop: bateria + bajo + acordes
    for b in range(drop_bars):
        a = t_drop + b * bar
        ch = chords[b % 4]
        for k in range(4):
            place(dr["kick"], a + k * T, 0.9)
            if k in (1, 3):
                place(dr["clap"], a + k * T, 0.5)
            for h in (0, 0.5):
                tt = np.arange(int(0.3 * T * SR)) / SR
                place(np.sin(2 * np.pi * M._mf(ch[0] - 24) * tt) * np.exp(-tt / 0.2), a + (k + h) * T, 0.35)
        seg = (t >= a) & (t < a + bar)
        y[seg] += 0.07 * sum(np.sin(2 * np.pi * M._mf(m) * t[seg]) for m in ch)
    place(dr["crash"][:, 0], t_drop, 0.4)
    # remate
    place(dr["kick"], t_end, 1.0)
    place(dr["crash"][:, 0], t_end, 0.5)
    tt = np.arange(int(1.6 * SR)) / SR
    place(sum(np.sin(2 * np.pi * M._mf(m) * tt) for m in chords[0]) * np.exp(-tt / 0.45), t_end, 0.12)
    y[int((t_end + 1.6) * SR):] = rng.standard_normal(n - int((t_end + 1.6) * SR)) * 3e-4
    y = y / np.abs(y).max() * 0.8
    x = np.stack([y, y], axis=1).astype(np.float32)
    truth = {"T": T, "g0": g0, "drop": t_drop, "end_hit": t_end,
             "beats": g0 + T * np.arange(int((t_end - g0) / T) + 1),
             "downbeats": g0 + bar * np.arange(intro_bars + drop_bars + 1)}
    return x, truth


def _tail_quiet(y: np.ndarray, ms: float = 5.0) -> float:
    """Pico de los ultimos `ms` (el final cae a cero con un fade corto: sin clic)."""
    n = int(ms / 1000 * SR)
    assert np.abs(y[-1]).max() < 1e-4
    return float(np.abs(y[-n:]).max())


def _lu(y: np.ndarray, a: float, b: float) -> float:
    return M.loudness_lufs(y[int(a * SR):int(b * SR)], SR, gated=False)


@pytest.fixture(scope="module")
def raw_long():
    x = _fake_ace_raw(40.0)
    return x, M._analyze_raw(x, 112.0)


@pytest.fixture(scope="module")
def raw_short():
    x = _fake_ace_raw(18.0, seed=5)
    return x, M._analyze_raw(x, 112.0)


@pytest.fixture(scope="module")
def ace_like():
    x, truth = _ace_like()
    return x, truth, M._analyze_raw(x, 112.0)


def _real(name):
    f = FIX / f"{name}.flac"
    if not f.exists():
        pytest.skip(f"sin fixture real {f.name} (spike/work/fixtures/music)")
    truth = json.loads((FIX / "truth.json").read_text())[name]
    return M.load_audio(f), truth


# ---------------------------------------------------------------- prompts y catalogo
def test_prompts_always_instrumental():
    for style in M.STYLE_MUSIC:
        for mood in M.MOODS:
            p = M.build_prompt(style, mood, seed=3)
            assert p["lyrics"] == "[Instrumental]"
            assert "no vocals" in p["caption"] and "instrumental" in p["caption"]
            assert M.BPM_MIN <= p["bpm"] <= M.BPM_MAX
            root, mode = p["keyscale"].split()
            assert root.upper() in M._NOTE and mode in ("major", "minor")
    assert M.build_prompt("elegante", "dramatic")["keyscale"].endswith("minor")
    assert M.build_prompt("dinamico", bpm=97)["bpm"] == 97
    assert M.build_prompt("dinamico", bpm=300)["bpm"] == M.BPM_MAX


def test_custom_prompt_keeps_functional_suffix_and_strips_vocals():
    c = M.build_prompt("dinamico", custom="musica alegre con guitarras")
    assert c["caption"].startswith("musica alegre") and M.BED_WORDS in c["caption"] and "no vocals" in c["caption"]
    v = M.build_prompt("dinamico", custom="upbeat pop song with female vocals singing, catchy chorus")
    low = v["caption"].lower()
    assert "female" not in low and "singing, " not in low.split("instrumental")[0]
    assert low.startswith("upbeat pop song") and "catchy chorus" in low and M.BED_WORDS in v["caption"]
    assert M.strip_vocals("lofi beat con voz de mujer y piano") == "lofi beat, piano"
    # las palabras de animo solo si se pidio el animo explicito
    assert M.MOOD_MUSIC["dramatic"]["words"] in M.build_prompt("dinamico", "dramatic", custom="rock")["caption"]
    assert M.MOOD_MUSIC["upbeat"]["words"] not in M.build_prompt("dinamico", None, custom="rock")["caption"]


def test_director_prompts_map_to_procedural_genres():
    want = {"dinamico": "pop", "elegante": "piano", "minimal": "ambient", "energetico": "trap"}
    for style, (prompt, bpm) in DIRECTOR_PROMPTS.items():
        p = M.build_prompt(style, None, prompt, bpm)
        assert p["genre"] == want[style], style
        assert M.BED_WORDS in p["caption"] and p["bpm"] == bpm
    assert M.genre_from_text("lofi hip hop with piano") == "lofi"
    assert M.genre_from_text("cinematic orchestral trailer") == "cinematic"


def test_keyscale_validation():
    assert M.valid_keyscale("a minor") == "A minor"
    assert M.valid_keyscale("Bb-maj") == "Bb major"
    assert M.valid_keyscale("F# menor") == "F# minor"
    for bad in ("H# lydian", "A", "", None, 5, "C dorian"):
        assert M.valid_keyscale(bad) is None
    p = M.build_prompt("dinamico", keyscale="H# lydian", seed=0)
    assert p["keyscale"] in [k for k in M.STYLE_MUSIC["dinamico"]["keys"]]


def test_ace_request_is_dit_only_wav():
    r = M.ace_request(M.build_prompt("dinamico"), 45.0, 7)
    assert r["lyrics"] == "[Instrumental]" and r["output_format"] == "wav32" and r["peak_clip"] == 0
    assert r["synth_model"] == M.DIT_MODEL and "lm_model" not in r
    assert r["inference_steps"] == 8 and r["duration"] == 45.0 and r["seed"] == 7


def test_model_catalog_for_setup():
    assert len(M.MUSIC_MODEL_FILES) == 3
    for m in M.MUSIC_MODEL_FILES:
        assert m["url"].startswith("https://huggingface.co/Serveurperso/ACE-Step-1.5-GGUF/resolve/main/")
        assert m["url"].endswith(m["file"]) and m["file"].endswith(".gguf")
        assert isinstance(m["size"], int) and m["size"] > 100_000_000
        assert re.fullmatch(r"[0-9a-f]{64}", m["sha256"])
    assert M.MUSIC_MODELS_BYTES == sum(m["size"] for m in M.MUSIC_MODEL_FILES)


def test_not_ready_without_models(monkeypatch, tmp_path):
    monkeypatch.setenv("REELFY_MUSIC_MODELS", str(tmp_path))
    assert M.music_models_dir() == tmp_path
    assert not M.music_ready()
    st = M.music_model_status()
    assert not st["ready"] and st["missing_bytes"] == M.MUSIC_MODELS_BYTES
    monkeypatch.setenv("REELFY_ACE_BIN", str(tmp_path / "no-existe" / "ace-synth"))
    assert M.ace_bin() is None


def test_model_file_exact_size_and_sha_marker(tmp_path):
    data = b"reelfy" * 1000
    import hashlib
    m = {"file": "x.gguf", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    p = tmp_path / "x.gguf"
    p.write_bytes(data[:-10])                         # descarga cortada: no cuenta
    assert not M._model_file_ok(p, m)
    p.write_bytes(data)
    assert M._model_file_ok(p, m) and M._model_file_ok(p, m, verify=True)
    assert (tmp_path / "x.gguf.ok").exists()          # marca: no se vuelve a hashear
    bad = dict(m, sha256="0" * 64)
    M._VERIFIED.clear()
    (tmp_path / "x.gguf.ok").unlink()
    assert not M._model_file_ok(p, bad, verify=True)


def test_gen_duration_buckets():
    assert M.gen_duration(31.5) == M.gen_duration(33.0) == 45.0
    for D in (5, 20, 37.8, 60, 140):
        g = M.gen_duration(D)
        assert g >= D * 1.2 and g in M.GEN_BUCKETS
    assert M.gen_duration(400) == 180.0                 # tope: el resto con loops


# ---------------------------------------------------------------- analisis
def test_analysis_trims_tail_silence_and_finds_ending(raw_long):
    x, ana = raw_long
    assert ana["end"] < 40.0 * 0.9                      # el silencio final no cuenta
    assert ana["ending"]["mode"] == "hit" and abs(ana["ending"]["hit"] - ana["end"]) < 1.5
    assert abs(ana["bpm"] - 112.0) < 1.0 and ana["grid"] == "dp" and ana["beat_confidence"] >= M.PULSE_MIN
    assert not ana["silences"]


@pytest.mark.parametrize("style", ["dinamico", "energetico", "minimal", "elegante"])
def test_analysis_beats_and_downbeats_match_truth(style):
    y, info = M.synth_procedural(24.0, style, None, None, None, 7)
    ana = M.analyze(y, SR, info["bpm"])
    true, tdb = np.array(info["beats"]), np.array(info["downbeats"])
    b, db = np.array(ana["beats"]), np.array(ana["downbeats"])
    near = np.array([np.min(np.abs(true - t)) for t in b])
    assert np.mean(near < 0.03) >= 0.9, style           # en el beat (no en el contratiempo)
    neard = np.array([np.min(np.abs(tdb - t)) for t in db])
    assert np.mean(neard < 0.05) >= 0.9, style


def test_intro_without_drums_is_rephased_to_drum_grid(ace_like):
    """Intro de pads + plucks a contratiempo: la rejilla del intro debe seguir a la bateria que
    entra despues (no quedar medio beat corrida ni dejar un intervalo de 1.5T)."""
    x, truth, ana = ace_like
    T = truth["T"]
    b = np.array(ana["beats"])
    intro = b[b < truth["drop"] - 0.1]
    assert len(intro) >= 10
    err = np.array([np.min(np.abs(truth["beats"] - t)) for t in intro])
    assert np.mean(err < 0.04) >= 0.9, err
    d = np.diff(b[b < truth["drop"] + 4 * T]) / T
    assert np.all(np.abs(d - 1) < 0.2), d                # sin intervalos de 1.5T
    db = np.array(ana["downbeats"])
    assert np.min(np.abs(db - truth["drop"])) < 0.05     # el drop cae en un 1
    assert abs(ana["drop"] - truth["drop"]) < 0.05


def test_real_ace_intro_shift_and_drop():
    x, tr = _real("ace_pop113_intro_shift")
    ana = M.analyze(x, SR, tr["bpm_hint"])
    T = 60 / ana["bpm"]
    b = np.array(ana["beats"])
    assert abs(ana["bpm"] - tr["bpm"]) < 0.5
    d = np.diff(b) / T
    assert np.all(np.abs(d - 1) < 0.2), "sigue el intervalo de 1.5T entre intro y drop"
    db = np.array(ana["downbeats"])
    assert np.min(np.abs(db - tr["downbeat_ref"])) < 0.04     # fase de compas correcta
    assert abs(ana["drop"] - tr["drop"]) < 0.06                # la entrada audible de la bateria


@pytest.mark.parametrize("name", ["ace_pop112_long", "ace_piano90", "ace_edm128"])
def test_real_ace_downbeats_and_drop(name):
    x, tr = _real(name)
    ana = M.analyze(x, SR, tr["bpm_hint"])
    assert abs(ana["bpm"] - tr["bpm"]) < 0.5 and ana["grid"] == "dp"
    db = np.array(ana["downbeats"])
    assert np.min(np.abs(db - tr["downbeat_ref"])) < 0.04
    assert ana["drop"] is not None and abs(ana["drop"] - tr["drop"]) < 0.06


@pytest.mark.parametrize("hint", [110, None])
def test_tempo_of_user_file_without_octave_errors(tmp_path, hint):
    """Archivo de 128 BPM: con el bpm 110 del plan (sin bpm_lock) o sin bpm, el tempo sale bien."""
    y, info = M.synth_procedural(40.0, "energetico", None, None, None, 3)
    assert info["bpm"] == 128
    y, _ = M.normalize(y)
    f = M.write_audio(tmp_path / "user128.wav", y)
    cfg = {"source": "file", "file": str(f)}
    if hint:
        cfg["bpm"] = hint
    r = M.get_music(cfg, 25.0, "dinamico", 7, cache_dir=tmp_path / "c")
    assert r["source"] == "file"
    assert abs(r["bpm"] / 128.0 - 1) < 0.02, r["bpm"]
    b = np.array(r["beats"])
    assert abs(np.median(np.diff(b)) - 60 / 128) < 0.01
    # sin hint en el analisis, tampoco hay error de octava en pop (57 vs 114)
    y2, i2 = M.synth_procedural(24.0, "dinamico", None, None, None, 5)
    assert abs(M.analyze(y2, SR, None)["bpm"] - i2["bpm"]) < 1.5


def test_no_phantom_beats_on_pads_and_drones():
    t = np.arange(int(9.0 * SR)) / SR
    pad = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.18, 329.63)) / 3
    pad *= 0.3 * (0.8 + 0.2 * np.sin(2 * np.pi * 0.11 * t))
    x = np.stack([pad, pad], axis=1).astype(np.float32)
    ana = M._analyze_raw(x, None)
    assert ana["beats"] == [] and ana["downbeats"] == [] and ana["grid"] == "none"
    # drone con vaiven de volumen de ~1 Hz (como las pistas viejas de la biblioteca)
    drone = np.sin(2 * np.pi * 110 * t) * 0.3 * (0.6 + 0.4 * np.sin(2 * np.pi * 0.93 * t) ** 2)
    ana2 = M._analyze_raw(np.stack([drone, drone], 1).astype(np.float32), 120)
    assert ana2["beats"] == [] and ana2["grid"] == "none"


def test_fit_material_without_pulse():
    """Pad sin golpes: recorte/loop con crossfade largo, sin huecos y sin beats inventados."""
    t = np.arange(int(9.0 * SR)) / SR
    pad = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.18, 329.63)) / 3
    pad *= 0.3 * (0.8 + 0.2 * np.sin(2 * np.pi * 0.11 * t))
    x = np.stack([pad, pad], axis=1).astype(np.float32)
    ana = M._analyze_raw(x, None)
    D = 25.0
    y, info = M.fit(x, SR, ana, D)
    assert len(y) == int(round(D * SR)) and info["beats"] == []
    assert M.silence_runs(y[: -int(1.5 * SR)], SR) == []


# ---------------------------------------------------------------- ajuste a duracion
@pytest.mark.parametrize("D", [12.3, 23.71, 31.518])
def test_fit_exact_duration_from_longer(raw_long, D):
    x, ana = raw_long
    y, info = M.fit(x, SR, ana, D, tail=1.5)
    assert len(y) == int(round(D * SR))
    assert _tail_quiet(y) < 0.02                         # termina en silencio, sin clic
    assert M.silence_runs(y[: -int(1.0 * SR)], SR) == []  # sin huecos
    b, db = np.array(info["beats"]), np.array(info["downbeats"])
    assert len(b) > 4 and b.min() >= 0 and b.max() <= D
    assert all(np.min(np.abs(b - d)) < 1e-3 for d in db)
    assert info["ending"] in ("natural", "hit")
    assert 0.2 <= D - info["end_hit"] <= 2.8


def test_fit_loops_when_source_is_shorter(raw_short):
    x, ana = raw_short
    D = 47.0
    y, info = M.fit(x, SR, ana, D, tail=1.2)
    assert len(y) == int(round(D * SR))
    assert info["loops"] >= 1
    assert M.silence_runs(y[: -int(1.0 * SR)], SR) == []
    b = np.array(info["beats"])
    T = 60.0 / ana["bpm"]
    d = np.diff(b)
    assert abs(np.median(d) - T) < 0.02 * T
    assert np.max(np.abs(d - T)) < 0.3 * T               # las costuras respetan el pulso


@pytest.mark.parametrize("ending", ["hit", "fade"])
def test_forced_cut_endings(raw_long, ending):
    x, ana = raw_long
    D = 21.0
    y, info = M.fit(x, SR, ana, D, ending=ending, tail=1.2)
    assert len(y) == int(round(D * SR)) and info["ending"] == ending
    c = int(info["end_hit"] * SR)
    before = np.sqrt(np.mean(y[c - SR // 2: c] ** 2))
    last = np.sqrt(np.mean(y[-int(0.15 * SR):] ** 2))
    assert last < before * 0.25
    assert _tail_quiet(y) < 0.02


@pytest.mark.parametrize("hook,drop_at,tol", [(0.75, 0.5, 0.5), (0.45, None, 0.0)])
def test_hook_starts_with_energy(ace_like, hook, drop_at, tol):
    """Crudo con 6 compases de intro casi muda: el video no puede arrancar ahi."""
    x, truth, ana = ace_like
    D = 20.0
    y, info = M.fit(x, SR, ana, D, drop_at=drop_at, drop_tol=tol, hook=hook)
    z, L = M.normalize(y)
    assert _lu(z, 0, 2.0) >= L - 3.0, (_lu(z, 0, 2.0), L)
    if drop_at is not None:
        assert info["drop"] is not None and info["drop"] <= 1.0 + 1e-6


@pytest.mark.parametrize("want", [1.0, 2.5, 4.5, 9.0])
def test_returned_drop_matches_requested(ace_like, want):
    x, truth, ana = ace_like
    D = 22.0
    y, info = M.fit(x, SR, ana, D, drop_at=want, hook=0.75)
    bar = 4 * truth["T"]
    assert info["drop"] is not None and abs(info["drop"] - want) <= bar / 2
    # y donde dice que esta, esta: la energia salta ahi
    z, L = M.normalize(y)
    d = info["drop"]
    if info["hook_end"] is None and d > 0.6:
        assert _lu(z, d, d + 0.5) > _lu(z, d - 0.5, d) + 3.0
    secs = M.sections_for(z, SR, info["downbeats"], D, info["end_hit"], info["ending"], info["drop"], info["hook_end"])
    assert [s for s in secs if s["label"] == "drop"][0]["start"] == round(d, 3)
    # antes del drop no hay otra seccion llamada drop
    assert not [s for s in secs if s["label"] == "drop" and s["start"] < d - 1e-3]


def test_stop_hides_seam_and_drop_lands_on_return(ace_like):
    """Caso del director: stop dramatico a ~3 s y drop al volver. Antes del stop suena material
    con energia (desde el cuadro 0); la costura queda dentro del silencio y el drop entra exacto."""
    x, truth, ana = ace_like
    D = 24.0
    stops = [{"at": 3.2, "dur": 1.2}]
    y, info = M.fit(x, SR, ana, D, drop_at=4.52, stops=stops, hook=0.75)
    assert info["drop"] == pytest.approx(4.52, abs=1e-3)
    assert info["hook_end"] is not None and 3.2 < info["hook_end"] < 4.4
    assert all(3.2 < s < 4.4 for s in info["seams"])
    z, L = M.normalize(y)
    assert _lu(z, 0, 2.0) >= L - 3.0 and _lu(z, 1.0, 3.1) >= L - 3.0
    assert _lu(z, 4.52, 5.5) >= L - 2.0                     # el drop pega al volver
    # ningun beat reportado dentro del stop con la rejilla de la cabeza
    b = np.array(info["beats"])
    assert not np.any((b > 3.25) & (b < info["hook_end"]))
    assert len(y) == int(round(D * SR))


def test_drop_cut_splice_without_stop():
    """drop_at explicito tarde: si el material previo al drop del crudo es tranquilo, la costura
    va en el golpe del drop (el impacto la tapa) y antes suena material con energia."""
    x, truth = _ace_like(intro_bars=8, drop_bars=12)
    ana = M._analyze_raw(x, 112.0)
    D = 26.0
    y, info = M.fit(x, SR, ana, D, drop_at=7.0, hook=0.75)
    assert info["drop"] == pytest.approx(7.0, abs=0.02)
    z, L = M.normalize(y)
    assert _lu(z, 0, 2.0) >= L - 3.0
    assert info["seams"] and abs(info["seams"][0] - 7.0) < 0.05


@pytest.mark.parametrize("name", ["ace_pop112_long", "ace_edm128", "ace_piano90"])
def test_real_ace_hook_and_director_stop(name):
    x, tr = _real(name)
    ana = M._analyze_raw(x, tr["bpm_hint"])
    D = 31.518
    y, info = M.fit(x, SR, ana, D, drop_at=0.5, drop_tol=0.5, hook=0.75)
    z, L = M.normalize(y)
    assert _lu(z, 0, 2.0) >= L - 5.0 and info["drop"] is not None and info["drop"] <= 1.0
    y, info = M.fit(x, SR, ana, D, drop_at=5.14, stops=[(3.53, 1.576)], hook=0.75)
    assert abs(info["drop"] - 5.14) < 0.02
    z, L = M.normalize(y)
    assert _lu(z, 0, 3.4) >= L - 4.0


def test_normalize_hits_target_and_true_peak(raw_long):
    x, ana = raw_long
    y, _ = M.fit(x, SR, ana, 20.0)
    z, L = M.normalize(y)
    assert abs(L - M.TARGET_LUFS) < 1.0
    assert M.true_peak_db(z) <= M.PEAK_DB + 0.05


def test_normalize_compresses_very_dynamic_material():
    """Piano con picos muy por encima de su RMS: llega al objetivo (antes quedaba ~2 dB abajo)."""
    t = np.arange(int(12 * SR)) / SR
    rng = np.random.default_rng(1)
    y = 0.02 * np.sin(2 * np.pi * 220 * t)
    for a in np.arange(0.2, 12, 0.66):
        i = int(a * SR)
        k = np.arange(min(int(0.8 * SR), len(t) - i)) / SR
        y[i:i + len(k)] += 0.9 * np.exp(-k / 0.08) * np.sin(2 * np.pi * rng.choice([262, 330, 392]) * k)
    x = np.stack([y, y], 1).astype(np.float32)
    z, L = M.normalize(x)
    assert abs(L - M.TARGET_LUFS) < 1.0, L
    assert M.true_peak_db(z) <= M.PEAK_DB + 0.05


def test_loudness_matches_ffmpeg_ebur128(tmp_path):
    y, _ = M.synth_procedural(12.0, "dinamico", None, None, None, 2)
    y, _ = M.normalize(y)
    p = M.write_audio(tmp_path / "l.wav", y)
    r = subprocess.run([M.FFMPEG, "-hide_banner", "-nostats", "-i", str(p), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    assert m, r.stderr[-400:]
    assert abs(float(m[-1]) - M.loudness_lufs(y)) < 0.3


# ---------------------------------------------------------------- procedural
@pytest.mark.parametrize("style", ["dinamico", "elegante", "minimal", "energetico"])
def test_procedural_objective_quality(style):
    D = 18.0
    y, info = M.synth_procedural(D, style, None, None, None, 4)
    assert len(y) == int(round(D * SR))
    z, L = M.normalize(y)
    v = M.validate_music(z, bpm=info["bpm"])
    assert abs(v["lufs"] - M.TARGET_LUFS) < 1.5
    assert v["true_peak_db"] <= M.PEAK_DB + 0.05 and v["clipped"] == 0
    assert v["silences"] == []
    assert v["tempo_err"] < 0.03
    assert info["ending"] == "natural" and 0.25 <= D - info["end_hit"] <= 0.3 + 60 / info["bpm"]
    assert info["sections"][0]["start"] == 0.0 and info["sections"][-1]["end"] == round(D, 3)


@pytest.mark.parametrize("genre", ["trap", "ambient"])
def test_procedural_new_genres(genre):
    y, info = M.synth_procedural(16.0, "energetico", None, 128 if genre == "trap" else 100, None, 3, genre=genre)
    assert info["genre"] == genre
    z, L = M.normalize(y)
    v = M.validate_music(z, bpm=info["bpm"])
    assert v["clipped"] == 0 and v["silences"] == [] and abs(v["lufs"] - M.TARGET_LUFS) < 1.5
    ana = M.analyze(z, SR, None)
    assert min(abs(ana["bpm"] / info["bpm"] - r) for r in (0.5, 1.0, 2.0)) < 0.03


@pytest.mark.parametrize("style,margin", [("dinamico", 3.0), ("energetico", 3.0), ("elegante", 6.0), ("minimal", 6.0)])
def test_procedural_hook_energy_via_get_music(tmp_path, style, margin):
    r = M.get_music({"source": "procedural"}, 20.0, style, 5, cache_dir=tmp_path)
    z = M.load_audio(r["wav"])
    L = M.loudness_lufs(z)
    assert _lu(z, 0, 2.0) >= L - margin, (style, _lu(z, 0, 2.0), L)
    if style in M.EARLY_DROP_STYLES:
        assert r["drop"] is not None and r["drop"] <= 1.0


def test_procedural_ends_on_drop_not_on_verse():
    y, info = M.synth_procedural(31.5, "dinamico", None, None, None, 7)
    body = [s for s in info["sections"] if s["label"] not in ("ending",)]
    assert body[-1]["label"] == "drop"
    z, L = M.normalize(y)
    e = info["end_hit"]
    assert _lu(z, e - 3.0, e) >= L - 2.0                 # la energia no cae en el CTA


@pytest.mark.parametrize("ending,tail", [("hit", 0.8), ("hit", 2.0), ("fade", 2.0)])
def test_procedural_respects_ending(tmp_path, ending, tail):
    r = M.get_music({"source": "procedural", "ending": ending, "fade_out": tail}, 20.0, "dinamico", 3,
                    cache_dir=tmp_path)
    assert r["ending"]["mode"] == ending
    D = 20.0
    assert 0.45 <= D - r["ending"]["hit"] <= tail + 60 / r["bpm"] * 4 + 1e-6
    z = M.load_audio(r["wav"])
    c = int(r["ending"]["hit"] * SR)
    assert np.sqrt(np.mean(z[-int(0.1 * SR):] ** 2)) < 0.3 * np.sqrt(np.mean(z[c - SR // 2:c] ** 2))


def test_procedural_ending_changes_cache_key(tmp_path):
    wavs = set()
    for e in ("auto", "hit", "fade"):
        for fo in (0.6, 3.0):
            r = M.get_music({"source": "procedural", "ending": e, "fade_out": fo}, 14.0, "dinamico", 3,
                            cache_dir=tmp_path)
            wavs.add(r["wav"])
    assert len(wavs) == 6


def test_procedural_drop_exact_on_stop_return():
    y, info = M.synth_procedural(29.2, "dinamico", None, 112, None, 7, drop_at=5.14, stops=[(3.53, 1.576)])
    assert info["drop"] == pytest.approx(5.14, abs=0.01)
    assert info["sections"][1]["label"] == "hook"


def test_procedural_deterministic_and_seed_sensitive():
    a, _ = M.synth_procedural(8.0, "dinamico", None, None, None, 5)
    b, _ = M.synth_procedural(8.0, "dinamico", None, None, None, 5)
    c, _ = M.synth_procedural(8.0, "dinamico", None, None, None, 6)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_procedural_drop_alignment():
    y, info = M.synth_procedural(30.0, "dinamico", None, None, None, 1, drop_at=9.0)
    assert info["drop"] is not None and abs(info["drop"] - 9.0) <= 0.02


# ---------------------------------------------------------------- orquestacion, cache, fallbacks
def _fake_generate_factory(calls, maker=None):
    def fake(prompt, gen_dur, seed, work, on_pct):
        calls.append((gen_dur, seed, prompt["caption"]))
        work.mkdir(parents=True, exist_ok=True)
        if on_pct:
            on_pct(50)
        if maker:
            x = maker(gen_dur, prompt["bpm"], seed)
        else:
            x = _fake_ace_raw(gen_dur, prompt["bpm"], seed=seed % 97)
        return M.write_audio(work / "req0.wav", x, SR, subtype="FLOAT")
    return fake


def test_ai_path_cache_and_refit(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(M, "music_ready", lambda: True)
    monkeypatch.setattr(M, "_ace_generate", _fake_generate_factory(calls))
    cfg = {"enabled": True, "source": "ai"}
    r1 = M.get_music(cfg, 31.518, "dinamico", 7, cache_dir=tmp_path)
    assert r1["source"] == "ai" and len(calls) == 1 and not r1["cached"]
    assert Path(r1["wav"]).exists()
    info = __import__("soundfile").info(r1["wav"])
    assert info.frames == int(round(31.518 * SR)) and info.samplerate == SR and info.channels == 2
    r2 = M.get_music(cfg, 31.518, "dinamico", 7, cache_dir=tmp_path)
    assert r2["cached"] and r2["wav"] == r1["wav"] and len(calls) == 1
    r3 = M.get_music(cfg, 33.0, "dinamico", 7, cache_dir=tmp_path)      # re-ajusta, no regenera
    assert len(calls) == 1 and not r3["cached"] and r3["wav"] != r1["wav"]
    assert __import__("soundfile").info(r3["wav"]).frames == int(round(33.0 * SR))
    r4 = M.get_music(cfg, 31.518, "dinamico", 8, cache_dir=tmp_path)     # otra seed: genera
    assert len(calls) == 2 and r4["wav"] != r1["wav"]
    for r in (r1, r3):
        assert set(("wav", "bpm", "beats", "downbeats", "sections", "drop", "ending", "lufs")) <= set(r)
        assert r["beats"] and r["downbeats"] and r["sections"][-1]["end"] == round(r["duration"], 3)
        assert r["ending"]["mode"] in ("natural", "hit", "fade")


def test_trimming_video_reuses_same_raw(monkeypatch, tmp_path):
    """Caso del Studio: 31.5 s -> 29 -> 26 -> 34: una sola generacion y el mismo crudo."""
    calls = []
    monkeypatch.setattr(M, "music_ready", lambda: True)
    monkeypatch.setattr(M, "_ace_generate", _fake_generate_factory(calls))
    raws = set()
    for D in (31.5, 29.0, 26.0, 34.0):
        r = M.get_music({"source": "ai"}, D, "dinamico", 7, cache_dir=tmp_path)
        raws.add(Path(r["raw"]).name)
        assert __import__("soundfile").info(r["wav"]).frames == int(round(D * SR))
    assert len(calls) == 1 and len(raws) == 1
    r = M.get_music({"source": "ai"}, 70.0, "dinamico", 7, cache_dir=tmp_path)   # mucho mas largo: genera
    assert len(calls) == 2 and calls[-1][0] == M.gen_duration(70.0)


def test_director_cfg_end_to_end(monkeypatch, tmp_path):
    """cfg exacta del director (prompt, bpm, stop, drop_at en tiempo final): drop coherente,
    hook con energia, sufijo funcional en el prompt y secciones con el drop donde quedo."""
    calls = []
    monkeypatch.setattr(M, "music_ready", lambda: True)
    monkeypatch.setattr(M, "_ace_generate", _fake_generate_factory(
        calls, lambda gd, bpm, sd: _ace_like(bpm, intro_bars=7, drop_bars=int(gd * 0.8 / (240 / bpm)) - 7)[0]))
    prompt, bpm = DIRECTOR_PROMPTS["dinamico"]
    cfg = {"enabled": True, "source": "ai", "track": None, "file": None, "prompt": prompt, "bpm": bpm,
           "gain_db": -17.0, "duck_db": -9.0, "fade_in": 0.2, "fade_out": 1.5,
           "stops": [{"at": 3.53, "dur": 1.576}], "drop_at": 5.14, "ending": "auto"}
    r = M.get_music(cfg, 29.195, "dinamico", 7, cache_dir=tmp_path)
    assert calls and M.BED_WORDS in calls[0][2]
    assert r["drop"] == pytest.approx(5.14, abs=0.02) and r["hook_end"] is not None
    z = M.load_audio(r["wav"])
    L = M.loudness_lufs(z)
    assert _lu(z, 0, 2.0) >= L - 3.0 and _lu(z, 5.14, 6.0) >= L - 2.0
    assert [s for s in r["sections"] if s["label"] == "drop"][0]["start"] == pytest.approx(5.14, abs=1e-3)
    assert r["sections"][0]["label"] == "hook"


def test_fallback_to_procedural_without_models(monkeypatch, tmp_path):
    monkeypatch.setenv("REELFY_MUSIC_MODELS", str(tmp_path / "vacio"))
    r = M.get_music({"enabled": True, "source": "ai"}, 14.2, "energetico", 3, cache_dir=tmp_path)
    assert r["source"] == "procedural" and r["fallback_from"] and "ai" in r["fallback_from"][0]
    assert __import__("soundfile").info(r["wav"]).frames == int(round(14.2 * SR))
    v = M.validate_music(r["wav"], bpm=r["bpm"])
    assert v["tempo_err"] < 0.03 and v["silences"] == []


def test_fallback_follows_director_genre(monkeypatch, tmp_path):
    monkeypatch.setenv("REELFY_MUSIC_MODELS", str(tmp_path / "vacio"))
    for style, g in (("energetico", "trap"), ("minimal", "ambient"), ("elegante", "piano")):
        prompt, bpm = DIRECTOR_PROMPTS[style]
        r = M.get_music({"source": "ai", "prompt": prompt, "bpm": bpm}, 12.0, style, 3, cache_dir=tmp_path)
        assert r["source"] == "procedural" and r["engine"] == f"procedural-{g}" and r["bpm"] == bpm


def test_last_resort_library(monkeypatch, tmp_path):
    monkeypatch.setenv("REELFY_MUSIC_MODELS", str(tmp_path / "vacio"))

    def boom(*a, **k):
        raise RuntimeError("sin procedural")
    monkeypatch.setattr(M, "synth_procedural", boom)
    if not M.library_tracks():
        pytest.skip("sin biblioteca")
    r = M.get_music({"enabled": True, "source": "ai"}, 26.4, "minimal", 3, cache_dir=tmp_path)
    assert r["source"] == "library" and r.get("track")
    assert __import__("soundfile").info(r["wav"]).frames == int(round(26.4 * SR))
    z = M.load_audio(r["wav"])
    assert M.silence_runs(z[: -int(1.2 * SR)], SR) == []


def test_disabled_and_none():
    assert M.get_music({"enabled": False}, 10, "dinamico", 1)["wav"] is None
    assert M.get_music({"source": "none"}, 10, "dinamico", 1)["wav"] is None


def test_garbage_cfg_never_raises(tmp_path):
    cfg = {"source": "procedural", "drop_at": "x", "seed": "abc", "fade_out": "long", "bpm": "rapido",
           "keyscale": "H# lydian", "ending": "boom", "stops": [{"at": "?"}, "nada", {"at": 2.0, "dur": 0.5}],
           "mood": "furioso", "prompt": 12}
    r = M.get_music(cfg, 10.0, "dinamico", 4, cache_dir=tmp_path)
    assert r["wav"] and r["source"] == "procedural" and r["warnings"]
    assert M.valid_keyscale(r["keyscale"])
    assert M.get_music({"source": "procedural"}, float("nan"), "dinamico", 1)["wav"] is None
    assert M.get_music({"source": "procedural"}, "abc", "dinamico", 1)["wav"] is None
    r = M.get_music({"source": "procedural", "bpm": 300, "bpm_lock": True}, 8.0, "dinamico", 1, cache_dir=tmp_path)
    assert r["bpm"] == M.BPM_MAX and r["warnings"]


def test_bpm_110_is_auto_unless_locked(tmp_path):
    a = M.get_music({"source": "procedural", "bpm": 110}, 8.0, "elegante", 1, cache_dir=tmp_path)
    b = M.get_music({"source": "procedural", "bpm": 110, "bpm_lock": True}, 8.0, "elegante", 1, cache_dir=tmp_path)
    assert a["bpm"] != 110 and b["bpm"] == 110
    assert "bpm_lock" in M.CFG_SCHEMA and M.CFG_SCHEMA["bpm"]["default"] == 0


def test_stale_file_is_ignored_unless_source_file(monkeypatch, tmp_path):
    monkeypatch.setenv("REELFY_MUSIC_MODELS", str(tmp_path / "vacio"))
    r = M.get_music({"source": "procedural", "file": "/no/existe.mp3"}, 8.0, "dinamico", 1, cache_dir=tmp_path)
    assert r["source"] == "procedural" and not r["fallback_from"]
    r = M.get_music({"source": "file", "file": "/no/existe.mp3"}, 8.0, "dinamico", 1, cache_dir=tmp_path)
    assert r["source"] == "procedural" and "file" in r["fallback_from"][0]


def test_cached_result_survives_moving_the_cache(tmp_path):
    a = tmp_path / "a"
    r = M.get_music({"source": "procedural"}, 6.0, "dinamico", 1, cache_dir=a)
    b = tmp_path / "b"
    a.rename(b)
    r2 = M.get_music({"source": "procedural"}, 6.0, "dinamico", 1, cache_dir=b)
    assert r2["cached"] and Path(r2["wav"]).exists() and Path(r2["wav"]).parent == b


class _Cancel(Exception):
    pass


@pytest.mark.parametrize("at", [0, 1, 40])
def test_cancel_propagates_without_fallback(tmp_path, at):
    def on_pct(p):
        if p >= at:
            raise _Cancel()
    with pytest.raises(_Cancel):
        M.get_music({"source": "procedural"}, 12.0, "dinamico", 1, on_pct=on_pct, cache_dir=tmp_path)


def test_cancel_with_ace_server_down_does_not_start_cli(monkeypatch, tmp_path):
    """Si el servidor no responde al cancelar, igual se propaga la cancelacion y NO se arranca
    ace-synth despues."""
    import requests

    class R:
        def __init__(self, js):
            self._js = js
            self.headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._js

    def post(url, **kw):
        if url.endswith("/synth"):
            return R({"id": "j1"})
        raise requests.ConnectionError("servidor caido")
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", lambda url, **kw: R({"status": "running"}))
    cli = []
    monkeypatch.setattr(M, "_ace_cli", lambda *a, **k: cli.append(1))
    monkeypatch.setenv("REELFY_ACE_URL", "http://127.0.0.1:9")

    def on_pct(p):
        raise _Cancel()
    with pytest.raises(M.MusicCancelled):
        M._ace_generate(M.build_prompt("dinamico"), 30.0, 1, tmp_path / "w", lambda p: M._pct(on_pct, p))
    assert cli == []


def test_prefetch_then_cache_hit(tmp_path):
    th = M.prefetch({"source": "procedural"}, 7.0, "dinamico", 2, cache_dir=tmp_path)
    th.join(60)
    r = M.get_music({"source": "procedural"}, 7.0, "dinamico", 2, cache_dir=tmp_path)
    assert r["cached"]


def test_prune_cache_lru_and_stale_work_dirs(tmp_path):
    for i, name in enumerate(["raw_a.flac", "music_old.wav", "music_new.wav"]):
        p = tmp_path / name
        p.write_bytes(b"0" * 1000)
        p.with_suffix(".json").write_text("{}")
        os.utime(p, (1000 + i, 1000 + i))
    w = tmp_path / "ace_viejo"
    w.mkdir()
    (w / "req0.wav").write_bytes(b"0" * 500)
    os.utime(w, (1000, 1000))
    freed = M.prune_cache(tmp_path, max_bytes=2000)
    assert freed == 1500 and not w.exists()
    assert not (tmp_path / "music_old.wav").exists() and not (tmp_path / "music_old.json").exists()
    assert (tmp_path / "raw_a.flac").exists() and (tmp_path / "music_new.wav").exists()


@pytest.mark.skipif(os.environ.get("REELFY_TEST_ACE") != "1" or not M.music_ready(),
                    reason="generacion real con ACE-Step: REELFY_TEST_ACE=1")
def test_ace_real_generation(tmp_path):
    pct = []
    prompt, bpm = DIRECTOR_PROMPTS["dinamico"]
    cfg = {"source": "ai", "prompt": prompt, "bpm": bpm, "stops": [{"at": 3.53, "dur": 1.576}], "drop_at": 5.14}
    r = M.get_music(cfg, 20.0, "dinamico", 21, on_pct=pct.append, cache_dir=tmp_path)
    assert r["source"] == "ai" and r["gen_s"] > 0
    assert __import__("soundfile").info(r["wav"]).frames == int(round(20.0 * SR))
    v = M.validate_music(r["wav"], bpm=r["bpm"])
    assert v["silences"] == [] and v["true_peak_db"] <= M.PEAK_DB + 0.05
    assert pct == sorted(pct) and pct[-1] == 100
    assert abs(r["bpm"] - bpm) < 3 and r["beats"] and r["downbeats"]
    assert r["drop"] == pytest.approx(5.14, abs=0.3)
    z = M.load_audio(r["wav"])
    assert _lu(z, 0, 2.0) >= M.loudness_lufs(z) - 5.0
    assert not list(tmp_path.glob("ace_*"))                 # sin carpetas de trabajo huerfanas


# ---------------------------------------------------------------- casos limite
@pytest.mark.parametrize("D", [0.4, 1.5, 2.4])
def test_short_clips(raw_long, D):
    x, ana = raw_long
    y, info = M.fit(x, SR, ana, D)
    assert len(y) == int(round(D * SR)) and np.abs(y[-1]).max() < 1e-4
    assert np.sqrt(np.mean(y ** 2)) > 0.01                  # suena (no es silencio)
    p, pinfo = M.synth_procedural(D, "dinamico", None, None, None, 2)
    assert len(p) == int(round(D * SR)) and pinfo["end_hit"] <= D - 0.2 + 1e-6


def test_fit_long_video_with_many_loops(raw_short):
    x, ana = raw_short
    D = 150.0
    y, info = M.fit(x, SR, ana, D)
    assert len(y) == int(round(D * SR)) and info["loops"] >= 5
    assert M.silence_runs(y[: -int(1.0 * SR)], SR) == []


def test_fit_aligns_drop_when_requested(raw_long):
    x, ana = raw_long
    assert ana["drop"] is not None
    D, want = 26.0, 5.0
    y, info = M.fit(x, SR, ana, D, drop_at=want)
    bar = 4 * 60.0 / ana["bpm"]
    assert info["drop"] is not None and abs(info["drop"] - want) <= bar / 2


# ---------------------------------------------------------------- ola 2: candado cancelable
def test_candado_sondea_y_suelta_al_cancelar(tmp_path):
    """Mientras otro (el prefetch) genera con el candado tomado, un get_music cancelado suelta
    al instante: la espera sondea LOCK_NB y consulta poll() (antes: flock bloqueante sordo a la
    cancelacion; el render tardaba 10 s en soltar)."""
    import threading
    import time as _t
    held, release = threading.Event(), threading.Event()

    def holder():
        with M._lock(tmp_path):
            held.set()
            release.wait(10)
    th = threading.Thread(target=holder, daemon=True)
    th.start()
    assert held.wait(5)
    state = {"n": 0}

    class Cancel(Exception):
        pass

    def poll():
        state["n"] += 1
        if state["n"] >= 3:
            raise Cancel()
    t0 = _t.time()
    with pytest.raises(Cancel):
        with M._lock(tmp_path, poll=poll):
            pass
    assert _t.time() - t0 < 1.5 and state["n"] == 3
    release.set()
    th.join(5)
    with M._lock(tmp_path, poll=poll):                   # libre: entra sin sondear
        pass


def test_progress_poll_envuelve_la_cancelacion():
    calls = []

    def cb(p):
        calls.append(p)
        if len(calls) > 1:
            raise RuntimeError("cancelado")
    pr = M._Progress(cb)
    pr(40)
    with pytest.raises(M.MusicCancelled):
        pr.poll()
    assert calls == [40, 40]                              # poll repite el valor, no avanza


def test_pending_solo_para_ia_sin_crudo(tmp_path):
    """pending(): True solo si get_music tendria que GENERAR con ACE (fuente ai, motor listo y
    sin crudo reutilizable). Procedural, desactivada o duracion invalida -> False."""
    assert M.pending({"source": "procedural"}, 20.0, cache_dir=tmp_path) is False
    assert M.pending({"enabled": False}, 20.0, cache_dir=tmp_path) is False
    assert M.pending({"source": "ai"}, 0.1, cache_dir=tmp_path) is False
    assert M.pending({"source": "ai"}, 20.0, cache_dir=tmp_path) is M.music_ready()
    if M.music_ready():
        st = "dinamico"
        c, _ = M._coerce_cfg({"source": "ai"}, 20.0, 7)
        pr = M.build_prompt(st, c["mood"] or M.STYLE_DEFAULT_MOOD[st], c["prompt"], c["bpm"], c["seed"],
                            c["keyscale"])
        rb = M._raw_key(pr, c["seed"])
        (tmp_path / f"raw_{rb}_30.flac").write_bytes(b"x")
        (tmp_path / f"raw_{rb}_30.json").write_text(json.dumps({"start": 0.0, "end": 28.0, "gen_dur": 30}))
        assert M.pending({"source": "ai"}, 20.0, cache_dir=tmp_path) is False
