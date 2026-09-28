"""Pruebas del mezclador (motion/audio.py): BS.1770, duraciones, alineacion por hit, ducking,
stops/swells/tape stop, loudness final y true peak, robustez, dur de SFX, tonalidad y memoria."""
import math
import shutil
import signal as _sig
import time
import tracemalloc
from contextlib import contextmanager

import numpy as np
import pytest
import soundfile as sf

from motion import audio as A
from motion import sfx

SR = A.SR


@pytest.fixture(scope="module", autouse=True)
def _catalogo():
    sfx.generate_all()


def _write(path, x):
    sf.write(str(path), np.asarray(x, np.float32), SR, subtype="FLOAT")
    return path


def _speechlike(dur, rng, on=((0.5, 1.7), (3.0, 4.4), (6.0, 7.2)), level=0.2):
    """Voz sintetica: ruido rosa con formantes y silabas a ~5 Hz, solo en los tramos `on`."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = rng.standard_normal(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = 1
    X *= 1 / np.sqrt(f) * (np.exp(-((f - 500) / 400) ** 2) + 0.6 * np.exp(-((f - 1500) / 700) ** 2) + 0.1)
    x = np.fft.irfft(X, n)
    x /= np.abs(x).max()
    syl = 0.55 + 0.45 * np.sin(2 * np.pi * 4.8 * t) ** 2
    gate = np.zeros(n)
    for a, b in on:
        gate[(t >= a) & (t < b)] = 1.0
    k = int(0.01 * SR)
    gate = np.convolve(gate, np.ones(k) / k, mode="same")
    y = x * syl * gate * level
    return np.stack([y, y], axis=1)


def _music(dur, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    tone = sum(np.sin(2 * np.pi * f * t) for f in (110, 220, 330, 440, 660, 990, 1320, 2640)) / 8
    noise = rng.standard_normal(n) * 0.05
    y = (tone * 0.3 + noise)
    return np.stack([y, np.roll(y, 111)], axis=1)


# ---------------------------------------------------------------- BS.1770
def test_bs1770_seno_referencia():
    t = np.arange(int(5 * SR)) / SR
    s = 0.1 * np.sin(2 * np.pi * 997 * t)
    x = np.stack([s, s], axis=1)
    assert abs(A.integrated_lufs(x) - (-20.0)) < 0.1
    assert abs(A.true_peak_db(x) - (-20.0)) < 0.1


def test_bs1770_coincide_con_ffmpeg(tmp_path):
    if not shutil.which(A._ffmpeg_bin()) and not __import__("os").path.exists(A._ffmpeg_bin()):
        pytest.skip("sin ffmpeg")
    rng = np.random.default_rng(1)
    x = _speechlike(8.0, rng) + 0.05 * _music(8.0, rng)
    p = _write(tmp_path / "x.wav", x)
    ff = A.ffmpeg_ebur128(p)
    m = A.loudness(x)
    assert abs(m["integrated"] - ff["integrated"]) < 0.15
    assert abs(m["true_peak"] - ff["true_peak"]) < 0.3
    assert abs(m["lra"] - ff["lra"]) < 1.0


# ---------------------------------------------------------------- mezcla
@pytest.mark.parametrize("dur", [1.0, 3.3333, 7.77])
def test_duracion_exacta_48k_estereo(tmp_path, dur):
    rng = np.random.default_rng(2)
    v = _write(tmp_path / "v.wav", _speechlike(dur + 1.0, rng))  # voz mas larga: se recorta
    out = tmp_path / "mix.wav"
    info = A.mix(v, dur, [{"at": 0.5, "name": "pop"}], None, out)
    y, sr = sf.read(str(out), always_2d=True)
    assert sr == 48000 and y.shape[1] == 2
    assert y.shape[0] == round(dur * 48000) == info["samples"]


@contextmanager
def _limite(seg):
    """Falla (en vez de colgarse) si el bloque tarda mas de `seg` segundos."""
    def _boom(*_):
        raise TimeoutError(f"tardo mas de {seg} s")
    old = _sig.signal(_sig.SIGALRM, _boom)
    _sig.alarm(int(seg))
    try:
        yield
    finally:
        _sig.alarm(0)
        _sig.signal(_sig.SIGALRM, old)


@pytest.mark.parametrize("name", ["pop", "click", "impact_soft", "impact_boom", "whip", "tick"])
def test_hit_alineado_a_1s(tmp_path, name):
    out = tmp_path / "m.wav"
    A.mix(None, 2.5, [{"at": 1.0, "name": name}], None, out, target_lufs=None, humanize=False)
    y, _ = sf.read(str(out), always_2d=True)
    e = np.abs(y).max(axis=1)
    e = np.convolve(e, np.ones(48) / 48, mode="same")
    if name.startswith("impact"):
        # impacto masterizado: el cuerpo toca el techo igual que el ataque; el golpe es el ataque
        a = int(0.9 * SR)
        loc = e[int(0.995 * SR):int(1.05 * SR)].max()
        on = a + int(np.argmax(e[a:] >= 0.5 * loc))
        assert abs(on / SR - 1.0) <= 0.005, (name, on / SR)
        assert e[:int(0.99 * SR)].max() < 0.1 * loc
    else:
        assert abs(np.argmax(e) / SR - 1.0) <= 0.005, (name, np.argmax(e) / SR)


def test_hit_alineado_con_loudness_y_humanizado(tmp_path):
    """Con normalizacion, limitador y leve cambio de tono el golpe sigue en su sitio."""
    out = tmp_path / "m.wav"
    info = A.mix(None, 3.0, [{"at": 1.0, "name": "pop"}, {"at": 2.0, "name": "click"}], None, out)
    y, _ = sf.read(str(out), always_2d=True)
    e = np.convolve(np.abs(y).max(axis=1), np.ones(48) / 48, mode="same")
    for at in (1.0, 2.0):
        a, b = int((at - 0.2) * SR), int((at + 0.2) * SR)
        assert abs((a + np.argmax(e[a:b])) / SR - at) <= 0.005
    assert all(abs(p["pitch"]) <= 0.25 for p in info["sfx"])


def test_whoosh_y_riser_alineados(tmp_path):
    out = tmp_path / "m.wav"
    A.mix(None, 4.0, [{"at": 2.0, "name": "whoosh_fast", "variant": 0}], None, out, target_lufs=None, humanize=False)
    y, _ = sf.read(str(out), always_2d=True)
    e = np.convolve(np.abs(y).max(axis=1), np.ones(1440) / 1440, mode="same")
    assert abs(np.argmax(e) / SR - 2.0) < 0.03
    # riser que empezaria antes de 0: se recorta la cabeza y culmina en `at`
    info = A.mix(None, 2.0, [{"at": 0.8, "name": "riser_short"}], None, out, target_lufs=None)
    assert info["sfx"][0]["trimmed_head"]
    y, _ = sf.read(str(out), always_2d=True)
    e = np.convolve(np.abs(y).max(axis=1), np.ones(2400) / 2400, mode="same")
    assert abs(np.argmax(e) / SR - 0.8) < 0.06
    assert np.abs(y[:48]).max() < 0.05  # la cabeza recortada entra con fundido


def _music_levels(stem, act_audio, t, mask):
    m2 = (stem ** 2).mean(axis=1)
    on = m2[mask & (act_audio > 0.97)].mean()
    off = m2[mask & (act_audio < 0.03)].mean()
    return 10 * np.log10(on / off)


@pytest.mark.parametrize("duck", [-9.0, -6.0, -12.0])
def test_ducking_bajo_la_voz(tmp_path, duck):
    rng = np.random.default_rng(3)
    dur = 8.0
    v = _write(tmp_path / "v.wav", _speechlike(dur, rng))
    mu = _write(tmp_path / "m.wav", _music(dur, rng))
    out = tmp_path / "mix.wav"
    info = A.mix(v, dur, [], {"wav": str(mu), "gain_db": -17, "duck_db": duck, "fade_in": 0, "fade_out": 0,
                              "voice_carve_db": 0}, out, stems_dir=tmp_path / "st")
    voice, _ = sf.read(info["stems"]["voice"], always_2d=True)
    mus, _ = sf.read(info["stems"]["music"], always_2d=True)
    act = A.ctrl_to_audio(A.music_activity(voice), voice.shape[0])
    t = np.arange(voice.shape[0]) / SR
    drop = _music_levels(mus, act, t, t > 0.2)
    assert drop <= duck + 2.0, (duck, drop)   # baja al menos |duck|-2 dB
    assert drop >= duck - 3.0, (duck, drop)   # y no mucho mas (sin sobre-ducking)


def test_ducking_sin_bombeo(tmp_path):
    """Entre silabas (voz continua) la ganancia de la musica no oscila."""
    rng = np.random.default_rng(4)
    dur = 6.0
    v = _speechlike(dur, rng, on=((0.5, 5.5),))
    # huecos cortos entre palabras (120 ms) dentro de la frase
    t = np.arange(v.shape[0]) / SR
    for g in (1.5, 2.3, 3.1, 4.0):
        v[(t > g) & (t < g + 0.12)] *= 0.0
    act = A.voice_activity(v)
    seg = act[int(1.0 * A.CTRL_HZ):int(5.0 * A.CTRL_HZ)]
    assert seg.min() > 0.85, seg.min()
    # y el ataque/release son suaves (sin saltos de un paso de control)
    assert np.abs(np.diff(act)).max() < 0.08


def test_stop_y_swell(tmp_path):
    rng = np.random.default_rng(5)
    dur = 8.0
    mu = _write(tmp_path / "m.wav", _music(dur, rng))
    out = tmp_path / "mix.wav"
    cfg = {"wav": str(mu), "gain_db": -12, "duck_db": -9, "fade_in": 0.1, "fade_out": 0.5,
           "stops": [{"at": 2.0, "dur": 1.0, "mode": "cut"}], "swells": [{"start": 5.0, "end": 6.5, "db": 6}]}
    info = A.mix(None, dur, [], cfg, out, target_lufs=None, stems_dir=tmp_path / "st")
    mus, _ = sf.read(info["stems"]["music"], always_2d=True)
    rms = lambda a, b: 10 * np.log10(((mus[int(a * SR):int(b * SR)]) ** 2).mean() + 1e-20)  # noqa: E731
    base = rms(3.5, 4.5)
    assert rms(2.05, 2.95) < base - 60          # hueco dramatico: silencio real
    assert rms(1.5, 1.95) > base - 1.0          # antes del stop suena normal
    assert rms(3.1, 3.5) > base - 1.0           # regresa rapido
    assert abs(rms(5.3, 6.4) - base - 6.0) < 1.0  # swell de +6 dB


def test_loudness_final_y_true_peak(tmp_path):
    rng = np.random.default_rng(6)
    dur = 12.0
    v = _write(tmp_path / "v.wav", _speechlike(dur, rng, on=((0.3, 4.0), (4.6, 8.0), (8.5, 11.5)), level=0.9))
    mu = _write(tmp_path / "m.wav", _music(20.0, rng))
    evs = [{"at": a, "name": n} for a, n in ((0.5, "whoosh_fast"), (1.2, "pop"), (2.5, "impact_boom"),
                                            (4.4, "cash"), (6.0, "riser_short"), (8.2, "sparkle"), (9.0, "glitch"))]
    for target in (-14.0, -16.0):
        out = tmp_path / f"mix{target}.wav"
        info = A.mix(v, dur, evs, {"wav": str(mu)}, out, target_lufs=target)
        y, _ = sf.read(str(out), always_2d=True)
        m = A.loudness(y)
        assert abs(m["integrated"] - target) <= 0.5, m
        assert m["true_peak"] <= A.TRUE_PEAK_DB + 0.05, m
        assert abs(info["lufs"] - m["integrated"]) < 0.05


def test_voz_muy_baja_o_muy_alta_termina_en_objetivo(tmp_path):
    rng = np.random.default_rng(7)
    for lvl in (0.01, 1.0):
        v = _write(tmp_path / f"v{lvl}.wav", np.clip(_speechlike(5.0, rng, level=lvl) * (4 if lvl == 1.0 else 1), -1, 1))
        out = tmp_path / f"o{lvl}.wav"
        info = A.mix(v, 5.0, [], None, out)
        assert abs(info["lufs"] - (-14.0)) <= 0.5 and info["true_peak_dbtp"] <= -1.45


def test_variantes_deterministas_y_rotan():
    evs = [{"at": 0.2 + 0.4 * i, "name": "whoosh_fast"} for i in range(6)]
    _, p1, _ = A.build_sfx_bus(int(3 * SR), evs, seed=7)
    _, p2, _ = A.build_sfx_bus(int(3 * SR), evs, seed=7)
    assert [p["variant"] for p in p1] == [p["variant"] for p in p2]
    vs = [p["variant"] for p in p1]
    assert all(a != b for a, b in zip(vs, vs[1:]))
    assert len(set(vs)) >= 2
    _, p3, _ = A.build_sfx_bus(int(3 * SR), [dict(e, variant=2) for e in evs], seed=7)
    # entrada y salida 0-based (lo que reporta la mezcla se puede guardar tal cual en el plan)
    assert all(p["variant"] == 2 and p["variant_label"] == 3 for p in p3)
    assert all(p["file"] == "whoosh_fast_3.flac" for p in p3)


def test_nombres_desconocidos_alias_y_fuera_de_rango(tmp_path):
    cat = A._catalog()
    assert A.resolve_sfx("whoosh", cat) == "whoosh_fast"
    assert A.resolve_sfx("Impact Boom", cat) == "impact_boom"
    assert A.resolve_sfx("pop_2", cat) == "pop"
    assert A.resolve_sfx("no_existe_xyz", cat) is None
    out = tmp_path / "m.wav"
    info = A.mix(None, 2.0, [{"at": 0.5, "name": "no_existe_xyz"}, {"at": 9.0, "name": "pop"},
                             {"at": 1.0, "name": "whoosh"}, {"at": 1.5, "name": "pop", "enabled": False}], None, out)
    reasons = [s["name"] for s in info["sfx_skipped"]]
    assert "no_existe_xyz" in reasons and "pop" in reasons
    assert [p["name"] for p in info["sfx"]] == ["whoosh_fast"]


def test_paneo(tmp_path):
    out = tmp_path / "m.wav"
    A.mix(None, 1.5, [{"at": 0.5, "name": "pop", "pan": 1.0}], None, out, target_lufs=None, humanize=False)
    y, _ = sf.read(str(out), always_2d=True)
    assert np.abs(y[:, 0]).max() < 1e-3 and np.abs(y[:, 1]).max() > 0.05


def test_musica_corta_se_repite_y_larga_se_recorta(tmp_path):
    rng = np.random.default_rng(8)
    mu = _write(tmp_path / "m.wav", _music(3.0, rng))
    out = tmp_path / "mix.wav"
    info = A.mix(None, 10.0, [], {"wav": str(mu), "fade_out": 0.5}, out, target_lufs=None, stems_dir=tmp_path / "s")
    mus, _ = sf.read(info["stems"]["music"], always_2d=True)
    for a in (1.0, 4.0, 7.0, 9.0):
        seg = mus[int(a * SR):int((a + 0.4) * SR)]
        assert np.sqrt((seg ** 2).mean()) > 1e-3, a
    assert np.abs(mus[-48:]).max() < 1e-3  # fade out al final


def test_sin_voz_ni_musica_no_falla(tmp_path):
    out = tmp_path / "m.wav"
    info = A.mix(None, 1.0, [], None, out)
    y, _ = sf.read(str(out), always_2d=True)
    assert y.shape == (48000, 2) and np.abs(y).max() == 0 and info["lufs"] is None


def test_rendimiento_mezcla_40s(tmp_path):
    rng = np.random.default_rng(9)
    dur = 40.0
    v = _write(tmp_path / "v.wav", _speechlike(dur, rng, on=tuple((a, a + 2.5) for a in np.arange(0.5, 39, 3.0))))
    mu = _write(tmp_path / "m.wav", _music(dur, rng))
    evs = [{"at": 0.4 + 1.1 * i, "name": sfx.NAMES[i % len(sfx.NAMES)]} for i in range(34)]
    t0 = time.perf_counter()
    info = A.mix(v, dur, evs, {"wav": str(mu), "stops": [{"at": 10, "dur": 0.8}]}, tmp_path / "o.wav")
    dt = time.perf_counter() - t0
    assert dt < 6.0 * _load_factor(), dt
    assert abs(info["lufs"] + 14.0) <= 0.5
    assert math.isfinite(info["true_peak_dbtp"]) and info["true_peak_dbtp"] <= -1.45


def test_musica_desactivada(tmp_path):
    rng = np.random.default_rng(10)
    mu = _write(tmp_path / "m.wav", _music(2.0, rng))
    for cfg in ({"wav": str(mu), "enabled": False}, {"wav": str(mu), "source": "none"}):
        info = A.mix(None, 2.0, [], cfg, tmp_path / "o.wav")
        assert "music" not in info and info["lufs"] is None


# ---------------------------------------------------------------- sidechain: direccion y cama
def test_hold_y_lookahead_direccion():
    """La actividad empieza ~40 ms ANTES de la voz (look-ahead) y se sostiene ~160 ms DESPUES
    (hold); antes estaba invertido (160 ms antes y solo 40 ms despues)."""
    rng = np.random.default_rng(11)
    v = _speechlike(3.0, rng, on=((1.0, 1.5),))
    act = A.voice_activity(v)
    t = np.arange(act.size) / A.CTRL_HZ
    assert act[t < 0.93].max() < 0.1
    assert act[(t >= 1.0) & (t <= 1.64)].min() >= 0.9
    assert act[t >= 1.95].max() < 0.1          # y despues suelta (release 250 ms)
    # la de la musica sostiene 600 ms: la cama no sube entre frases cercanas
    am = A.music_activity(v)
    assert am[t < 0.9].max() < 0.1
    assert am[(t >= 0.98) & (t <= 2.08)].min() >= 0.9


def test_cama_no_respira_en_pausas_cortas():
    """Con los defaults (-17/-9/-3) la musica no se abre en pausas < 0.6 s (antes subia 12 dB
    en cada hueco) y si sube en una pausa real; sin saltos bruscos."""
    rng = np.random.default_rng(12)
    on = ((0.3, 1.6), (1.9, 3.0), (3.45, 4.6), (5.0, 6.2), (7.7, 9.0), (9.3, 10.5))
    v = _speechlike(11.0, rng, on=on)
    g, info = A.music_gain_curve(11.0, {"gain_db": -17, "duck_db": -9}, A.music_activity(v), 0.0, 0.0)
    gdb = 20 * np.log10(np.maximum(g, 1e-9))
    t = np.arange(g.size) / A.CTRL_HZ
    ref = gdb[(t > 1.0) & (t < 1.5)].mean()
    opens = []
    for (a0, a1), (b0, _b1) in zip(on[:-1], on[1:]):
        m = (t >= a1) & (t <= b0)
        opens.append((b0 - a1, gdb[m].max() - ref))
    short = [o for gap, o in opens if gap < 0.6]
    assert sum(o > 4.0 for o in short) == 0 and max(short) < 1.0, opens
    assert max(o for gap, o in opens if gap > 1.0) > 4.0, opens
    # el carve cuenta dentro del presupuesto: total 9 = 6 de banda ancha + 3 en la banda de voz
    assert info["duck_db"] == -9.0 and info["broadband_duck_db"] == -6.0 and info["voice_carve_db"] == -3.0
    rise = np.diff(gdb[(t > 0.5) & (t < 10.5)]) * A.CTRL_HZ
    assert rise.max() < 60.0          # sube despacio (release 500 ms)
    assert -rise.max() > -60.0 and rise.min() > -250.0   # baja rapido pero sin escalon


def test_carve_dentro_del_presupuesto(tmp_path):
    """Musica de banda ancha: con voz, los graves bajan |duck|-|carve| (6 dB) y la banda de la
    voz |duck| (9 dB)."""
    from scipy import signal as ss
    rng = np.random.default_rng(13)
    dur = 10.0
    v = _write(tmp_path / "v.wav", _speechlike(dur, rng, on=((1.0, 4.0), (6.5, 9.0))))
    n = int(dur * SR)
    mus = rng.standard_normal((n, 2)) * 0.05
    mu = _write(tmp_path / "m.wav", mus)
    info = A.mix(v, dur, [], {"wav": str(mu), "fade_in": 0, "fade_out": 0}, tmp_path / "o.wav",
                 stems_dir=tmp_path / "st")
    voice, _ = sf.read(info["stems"]["voice"], always_2d=True)
    m, _ = sf.read(info["stems"]["music"], always_2d=True)
    act = A.ctrl_to_audio(A.music_activity(voice), n)
    lo = ss.sosfilt(ss.butter(4, 150, "lowpass", fs=SR, output="sos"), m.mean(axis=1))
    mid = ss.sosfilt(ss.butter(4, [700, 2500], "bandpass", fs=SR, output="sos"), m.mean(axis=1))
    on, off = act > 0.97, act < 0.03
    d = lambda b: 10 * np.log10((b[on] ** 2).mean() / (b[off] ** 2).mean())  # noqa: E731
    assert abs(d(lo) + 6.0) < 1.5, d(lo)
    assert abs(d(mid) + 9.0) < 1.5, d(mid)


# ---------------------------------------------------------------- robustez
def test_eventos_malformados_no_tiran_la_mezcla(tmp_path):
    rng = np.random.default_rng(14)
    v = _write(tmp_path / "v.wav", _speechlike(3.0, rng, on=((0.3, 2.5),)))
    mu = _write(tmp_path / "m.wav", _music(3.0, rng))
    evs = [{"at": float("nan"), "name": "pop"}, {"at": 0.5, "name": "pop", "gain_db": "abc"},
           {"at": "x", "name": "click"}, {"at": 0.8, "name": "click", "pan": None, "pitch": "?"},
           "no soy un evento", None, {"at": 1.0, "name": None}, {"at": 1.2, "name": "ding", "variant": "zz", "dur": "?"},
           {"at": 1.4, "name": "whoosh_fast", "gain_db": float("inf")}, {"at": None, "name": "tick"}]
    cfg = {"wav": str(mu), "gain_db": None, "duck_db": None, "fade_in": "abc", "fade_out": None, "offset": "?",
           "voice_carve_db": None, "stops": [{"at": None}, {"at": 1.0, "dur": "x"}, "malo"],
           "swells": [{"start": 0.5}, {"start": 1, "end": "q"}, {"start": 2.0, "end": 1.0}]}
    out = tmp_path / "o.wav"
    info = A.mix(v, 3.0, evs, cfg, out)
    y, _ = sf.read(str(out), always_2d=True)
    assert y.shape == (3 * SR, 2) and np.isfinite(y).all()
    assert "music" in info and abs(info["lufs"] + 14.0) < 0.5
    assert {p["name"] for p in info["sfx"]} == {"pop", "click", "ding", "whoosh_fast"}
    reasons = [s["reason"] for s in info["sfx_skipped"]]
    assert reasons.count("valor invalido: at") == 3 and "evento invalido" in reasons
    # no-dict en la lista de musica tampoco la tira
    A.mix(None, 1.0, "no es lista", {"wav": str(mu), "stops": "x", "swells": 3}, out)
    for bad in (0, -1.0, float("nan"), None, "abc"):
        with pytest.raises(ValueError):
            A.mix(None, bad, [], None, out)


def test_musica_vacia_o_corta_no_cuelga(tmp_path):
    """Una musica de 0 o 1 muestra (fallo de generacion) se trata como ausente; una muy corta
    pero valida se repite en tiempo lineal. Antes: bucle infinito / O(n^2)."""
    out = tmp_path / "o.wav"
    for k in (0, 1, 200):
        p = tmp_path / f"m{k}.wav"
        sf.write(str(p), np.zeros((k, 2), np.float32) + 0.1, SR, subtype="FLOAT")
        with _limite(30 * _load_factor()):
            t0 = time.perf_counter()
            info = A.mix(None, 20.0, [], {"wav": str(p)}, out)
            assert time.perf_counter() - t0 < 5.0 * _load_factor()
        assert info.get("music_skipped") == "muy corta" and "music" not in info
    rng = np.random.default_rng(15)
    p = _write(tmp_path / "corta.wav", _music(0.6, rng))
    with _limite(30 * _load_factor()):
        t0 = time.perf_counter()
        info = A.mix(None, 60.0, [], {"wav": str(p), "offset": 0.05}, out, stems_dir=tmp_path / "st")
        assert time.perf_counter() - t0 < 5.0 * _load_factor()
    mus, _ = sf.read(info["stems"]["music"], always_2d=True)
    for a in (5.0, 30.0, 55.0):
        assert np.sqrt((mus[int(a * SR):int((a + 0.3) * SR)] ** 2).mean()) > 1e-3
    # offset al final del archivo: queda 1 muestra -> se ignora el offset (no se cuelga)
    info = A.mix(None, 5.0, [], {"wav": str(p), "offset": 0.6 - 1 / SR}, out)
    assert "music" in info or info.get("music_skipped")


def test_loop_fit_lineal_y_continuo():
    rng = np.random.default_rng(16)
    m = _music(1.0, rng)
    with _limite(30 * _load_factor()):
        y = A._loop_fit(m, int(120 * SR))
    assert y.shape == (int(120 * SR), 2) and y.dtype == np.float32
    # union con fundido de igual potencia: sin huecos de nivel
    e = np.sqrt(np.convolve((y[:, 0].astype(np.float64)) ** 2, np.ones(4800) / 4800, mode="valid"))
    assert e[4800:-4800].min() > 0.5 * np.median(e)
    assert A._loop_fit(np.zeros((0, 2)), 100).shape == (100, 2)


def test_stop_fade_cero_sin_nan(tmp_path):
    """fade=0 / fade_back=0 no generan NaN (antes: 0/0 -> hueco de 2 ms en TODA la mezcla)."""
    rng = np.random.default_rng(17)
    v = _write(tmp_path / "v.wav", _speechlike(5.0, rng, on=((0.3, 4.7),)))
    mu = _write(tmp_path / "m.wav", _music(5.0, rng))
    for mode in ("cut", "tape"):
        cfg = {"wav": str(mu), "stops": [{"at": 2.0, "dur": 1.0, "mode": mode, "fade": 0, "fade_back": 0}]}
        g, _ = A.music_gain_curve(5.0, cfg, np.zeros(5000), -20.0)
        assert np.isfinite(g).all()
        info = A.mix(v, 5.0, [], cfg, tmp_path / "o.wav")
        y, _ = sf.read(str(tmp_path / "o.wav"), always_2d=True)
        assert np.isfinite(y).all() and "nan_fixed" not in info and info["true_peak_dbtp"] is not None
        z = (np.abs(y[int(0.5 * SR):int(4.5 * SR)]).max(axis=1) == 0).astype(int)
        assert np.max(np.convolve(z, np.ones(24), mode="valid")) < 24     # ningun hueco digital en la voz


def test_ending_de_music_py_sin_doble_fundido(tmp_path):
    """Si music.py ya hizo el final (natural/hit/fade), el golpe final a D-0.5 conserva su nivel
    (antes el fade_out coseno de 1.5 s lo bajaba ~12 dB)."""
    rng = np.random.default_rng(18)
    mu = _write(tmp_path / "m.wav", _music(10.0, rng))
    lv = {}
    for k, cfg in {"hit": {"ending": {"mode": "hit", "hit": 9.5}}, "texto": {"ending": "natural"},
                   "sin": {}}.items():
        info = A.mix(None, 10.0, [], dict(cfg, wav=str(mu), fade_out=1.5, fade_in=0.2), tmp_path / "o.wav",
                     target_lufs=None, stems_dir=tmp_path / k)
        m, _ = sf.read(info["stems"]["music"], always_2d=True)
        r = lambda a, b: 10 * np.log10((m[int(a * SR):int(b * SR)] ** 2).mean() + 1e-20)  # noqa: E731
        lv[k] = (r(9.4, 9.6) - r(5.0, 6.0), r(0.0, 0.05) - r(5.0, 6.0), info["music"]["ending"])
    assert abs(lv["hit"][0]) < 1.0 and abs(lv["texto"][0]) < 1.0, lv
    assert lv["hit"][1] > -1.5, lv                 # el primer downbeat no se suaviza
    assert lv["sin"][0] < -6.0, lv                 # sin final de music.py: fade normal
    assert lv["hit"][2] == "hit" and lv["sin"][2] is None


# ---------------------------------------------------------------- tape stop
def _centroid(x):
    m = x.mean(axis=1) if x.ndim == 2 else x
    S = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return float((S * f).sum() / (S.sum() + 1e-20))


def test_tape_stop_frena_la_propia_pista(tmp_path):
    """mode tape (default): la pista real baja de tono y se apaga en [at-0.5, at], silencio hasta
    at+dur y regreso; el record_stop sintetico junto al stop se omite (lo reemplaza el frenado)."""
    n = 6 * SR
    t = np.arange(n) / SR
    tone = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((440, 880, 1320, 1760), 1)) * 0.2
    mu = _write(tmp_path / "m.wav", np.stack([tone, tone], axis=1))
    cfg = {"wav": str(mu), "fade_in": 0, "fade_out": 0, "stops": [{"at": 3.0, "dur": 1.0}]}
    evs = [{"at": 3.0, "name": "record_stop"}, {"at": 1.0, "name": "record_stop"}]
    info = A.mix(None, 6.0, evs, cfg, tmp_path / "o.wav", target_lufs=None, stems_dir=tmp_path / "st")
    m, _ = sf.read(info["stems"]["music"], always_2d=True)
    r = lambda a, b: 10 * np.log10((m[int(a * SR):int(b * SR)] ** 2).mean() + 1e-20)  # noqa: E731
    base = r(1.5, 2.3)
    assert abs(r(2.0, 2.45) - base) < 1.0              # antes del frenado suena normal
    c0, c1 = _centroid(m[int(2.2 * SR):int(2.45 * SR)]), _centroid(m[int(2.75 * SR):int(2.9 * SR)])
    assert c1 < 0.55 * c0, (c0, c1)                    # el tono baja (se frena)
    assert r(2.85, 2.98) < r(2.5, 2.6) - 6             # y el nivel cae
    assert r(3.05, 3.95) < base - 60                   # silencio real
    assert abs(r(4.2, 4.8) - base) < 1.0               # regreso
    assert info["music"]["tape_stops"] == 1
    sk = [s for s in info["sfx_skipped"] if s["name"] == "record_stop"]
    assert len(sk) == 1 and "tape" in sk[0]["reason"]
    assert [p["at"] for p in info["sfx"]] == [1.0]      # el que no coincide con el stop si suena
    # modo cut: corte seco y el record_stop se conserva
    cfg["stops"] = [{"at": 3.0, "dur": 1.0, "mode": "cut"}]
    info = A.mix(None, 6.0, evs, cfg, tmp_path / "o.wav", target_lufs=None, stems_dir=tmp_path / "st2")
    m, _ = sf.read(info["stems"]["music"], always_2d=True)
    assert abs(r(2.6, 2.95) - base) < 1.0 and len(info["sfx"]) == 2


# ---------------------------------------------------------------- SFX: dur, nombres, tonalidad
@pytest.mark.parametrize("name,d", [("typing", 3.0), ("typing", 0.5), ("pen_scribble", 2.4),
                                    ("pen_scribble", 0.4), ("glitch", 1.2), ("paper_rip", 1.6)])
def test_dur_de_sfx_continuos(tmp_path, name, d):
    """`dur` corta o extiende (loop de la zona sostenida) los SFX continuos: el sonido termina
    en at+dur (+-10 ms) y sigue sonando hasta el final."""
    at = 1.0
    info = A.mix(None, 6.0, [{"at": at, "name": name, "dur": d, "variant": 0}], None, tmp_path / "o.wav",
                 target_lufs=None, humanize=False)
    y, _ = sf.read(str(tmp_path / "o.wav"), always_2d=True)
    e = np.abs(y).max(axis=1)
    pk = e.max()
    assert e[int((at + d + 0.01) * SR):].max() < pk * 10 ** (-50 / 20)
    assert e[int((at + d - 0.25) * SR):int((at + d - 0.03) * SR)].max() > pk * 10 ** (-30 / 20)
    p = info["sfx"][0]
    assert abs(p["dur"] - d) <= 0.01 and p["dur_mode"] in ("corte", "loop")
    if d > 2.0:
        assert p["dur_mode"] == "loop"


def test_dur_latido_y_riser(tmp_path):
    out = tmp_path / "o.wav"
    info = A.mix(None, 6.0, [{"at": 0.5, "name": "heartbeat", "dur": 4.0}], None, out, target_lufs=None)
    y, _ = sf.read(str(out), always_2d=True)
    e = np.convolve(np.abs(y).max(axis=1), np.ones(480) / 480, mode="same")
    assert e[int(4.51 * SR):].max() < e.max() * 0.003
    assert e[int(3.4 * SR):int(4.4 * SR)].max() > 0.3 * e.max()     # sigue latiendo (loop)
    # riser: dur = anticipacion; se recorta la cabeza y culmina igual en `at`
    info = A.mix(None, 4.0, [{"at": 3.0, "name": "riser_long", "dur": 1.2}], None, out, target_lufs=None)
    y, _ = sf.read(str(out), always_2d=True)
    e = np.abs(y).max(axis=1)
    assert e[:int(1.79 * SR)].max() == 0.0
    e2 = np.convolve(e, np.ones(2400) / 2400, mode="same")
    assert 2.85 <= np.argmax(e2) / SR <= 3.02 and e2[int(2.96 * SR)] > 0.6 * e2.max()
    assert e2[int(1.9 * SR)] < 0.35 * e2.max()          # entra con fundido y crece
    assert info["sfx"][0]["dur"] == pytest.approx(1.2, abs=0.01)


def test_resolucion_de_nombres():
    cat = A._catalog()
    for k in ("s", "b", "c", "t", "p", "wh"):
        assert A.resolve_sfx(k, cat) is None, k        # prefijos de 1-2 letras ya no adivinan
    want = {"campana": "bell", "brillo": "sparkle", "camara": "shutter", "impacto": "impact_soft",
            "Golpe": "impact_soft", "typewriter": "typing", "transition": "whoosh_fast",
            "Transicion": "whoosh_fast", "Transición": "whoosh_fast", "latigazo": "whip",
            "whoosh fast": "whoosh_fast", "Whoosh-Soft": "whoosh_soft", "exito": "success",
            "whoosh_fast_extra": "whoosh_fast", "riser": "riser_short", "rasgar": "paper_rip"}
    for k, v in want.items():
        assert A.resolve_sfx(k, cat) == v, (k, A.resolve_sfx(k, cat), v)
    assert A.parse_sfx_name("pop_2", cat) == ("pop", 1)
    assert A.parse_sfx_name("whoosh_fast_3", cat) == ("whoosh_fast", 2)
    assert A.parse_sfx_name("pop_soft_2", cat) == ("pop_soft", 1)
    # el sufijo elige la variante si el evento no trae variant; variant explicito manda
    _, p, _ = A.build_sfx_bus(SR, [{"at": 0.2, "name": "pop_3"}, {"at": 0.5, "name": "pop_3", "variant": 0}], cat=cat)
    assert [x["variant"] for x in p] == [2, 0]


def _peak_hz(x, a, b):
    m = x[int(a * SR):int(b * SR)].mean(axis=1)
    S = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return f[np.argmax(S)]


def test_sfx_tonales_se_transponen_a_la_tonalidad(tmp_path):
    rng = np.random.default_rng(19)
    mu = _write(tmp_path / "m.wav", _music(3.0, rng))
    ev = [{"at": 1.0, "name": "ding", "variant": 0}]            # Mi6 = 1318.5 Hz, root 4
    res = {}
    for key, semis in (("G major", 3), ("A minor", -4), ("E major", 0), ("Bb minor", -3)):
        info = A.mix(None, 3.0, ev, {"wav": str(mu), "keyscale": key, "gain_db": -60}, tmp_path / "o.wav",
                     target_lufs=None, stems_dir=tmp_path / "st")
        s, _ = sf.read(info["stems"]["sfx"], always_2d=True)
        res[key] = (info["sfx"][0].get("transpose", 0), _peak_hz(s, 1.02, 1.5))
        assert res[key][0] == semis, res
        assert abs(res[key][1] / (1318.51 * 2 ** (semis / 12)) - 1) < 0.01, res
    # pitch explicito manda; sin tonalidad no se transpone (solo humanizado +-0.25)
    info = A.mix(None, 3.0, [dict(ev[0], pitch=0)], {"wav": str(mu), "keyscale": "G major", "gain_db": -60},
                 tmp_path / "o.wav", target_lufs=None)
    assert "transpose" not in info["sfx"][0] and info["sfx"][0]["pitch"] == 0
    info = A.mix(None, 3.0, ev, None, tmp_path / "o.wav", target_lufs=None)
    assert "transpose" not in info["sfx"][0] and abs(info["sfx"][0]["pitch"]) <= 0.25
    assert A.key_root("C major") == 0 and A.key_root("A minor") == 0 and A.key_root("F# minor") == 9
    assert A.key_root("Eb") == 3 and A.key_root(None) is None and A.key_root("xyz") is None


# ---------------------------------------------------------------- niveles, limitador, memoria
def test_limitador_sostenido_con_tope(tmp_path):
    """Musica sola muy picuda: en vez de aplastarla 8 dB, la limitacion sostenida se queda en
    MAX_GR_DB y se informa cuanto falto para el objetivo."""
    n = 8 * SR
    t = np.arange(n) / SR
    x = 0.02 * np.sin(2 * np.pi * 220 * t)
    for k in np.arange(0.1, 8.0, 0.25):
        i = int(k * SR)
        x[i:i + 480] += np.hanning(480) * 0.9 * np.sign(np.sin(np.arange(480) * 0.3))
    mu = _write(tmp_path / "m.wav", np.stack([x, x], axis=1))
    info = A.mix(None, 8.0, [], {"wav": str(mu), "fade_in": 0, "fade_out": 0}, tmp_path / "o.wav")
    assert info["limiter_max_gr_db"] <= A.MAX_GR_DB + 0.1, info
    assert info["lufs_short_by"] > 0.5 and info["true_peak_dbtp"] <= A.TRUE_PEAK_DB + 0.05


def test_sfx_muy_fuerte_lo_absorbe_su_bus(tmp_path):
    """Un SFX a +12 dB se limita en el bus de SFX: la voz conserva su loudness y el limitador
    final no bombea."""
    rng = np.random.default_rng(20)
    v = _write(tmp_path / "v.wav", _speechlike(6.0, rng, on=((0.3, 5.7),)))
    evs = [{"at": 2.0, "name": "impact_boom", "gain_db": 12}, {"at": 3.5, "name": "pop", "gain_db": 12}]
    info = A.mix(v, 6.0, evs, None, tmp_path / "o.wav")
    assert info["sfx_bus_gr_db"] > 6.0
    assert info["limiter_max_gr_db"] <= A.MAX_GR_ABS_DB + 0.1
    assert abs(info["lufs"] + 14.0) <= 0.5


def test_cola_grave_de_impacto_se_agacha_bajo_voz(tmp_path):
    from scipy import signal as ss
    rng = np.random.default_rng(21)
    v = _write(tmp_path / "v.wav", _speechlike(4.0, rng, on=((0.2, 3.8),)))
    lvl = {}
    for k, voice in (("con", v), ("sin", None)):
        info = A.mix(voice, 4.0, [{"at": 1.0, "name": "impact_boom"}], None, tmp_path / "o.wav",
                     target_lufs=None, sfx_duck_db=0, stems_dir=tmp_path / k)
        s, _ = sf.read(info["stems"]["sfx"], always_2d=True)
        lo = ss.sosfilt(ss.butter(4, 120, "lowpass", fs=SR, output="sos"), s.mean(axis=1))
        lvl[k] = 10 * np.log10((lo[int(1.4 * SR):int(2.2 * SR)] ** 2).mean() + 1e-20)
    assert lvl["con"] < lvl["sin"] - 4.0, lvl


def test_memoria_acotada_en_mezclas_largas(tmp_path):
    """120 s con voz, musica y 40 SFX: pico de memoria de numpy acotado (buses float32, carve y
    BS.1770 por bloques). Antes: ~2 GB para 120 s."""
    rng = np.random.default_rng(22)
    dur = 120.0
    v = _write(tmp_path / "v.wav", _speechlike(dur, rng, on=tuple((a, a + 2.5) for a in np.arange(0.5, 117, 3.0))))
    mu = _write(tmp_path / "m.wav", _music(30.0, rng))
    evs = [{"at": 0.4 + 2.9 * i, "name": sfx.NAMES[i % len(sfx.NAMES)]} for i in range(40)]
    tracemalloc.start()
    try:
        info = A.mix(v, dur, evs, {"wav": str(mu), "stops": [{"at": 30, "dur": 0.8}]}, tmp_path / "o.wav")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 450e6, peak / 1e6
    assert abs(info["lufs"] + 14.0) <= 0.5



# ---------------------------------------------------------------- ola 2: fundidos de seguridad
def test_mezcla_termina_en_silencio_aunque_la_voz_no(tmp_path):
    """La voz (o un SFX) termina a media onda en la ultima muestra: la mezcla completa lleva un
    fundido de seguridad de END_FADE_S y la ultima muestra queda en ~0 (sin clic al final)."""
    dur = 4.0
    t = np.arange(int(dur * SR)) / SR
    v = (0.5 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    vp = tmp_path / "v.wav"
    sf.write(str(vp), np.stack([v, v], 1), SR)
    info = A.mix(str(vp), dur, [], {"enabled": False}, tmp_path / "o.wav")
    y, _ = sf.read(str(tmp_path / "o.wav"), always_2d=True)
    assert np.abs(y[-1]).max() < 1e-3 and np.abs(y[0]).max() < 1e-3
    k = int(A.END_FADE_S * SR)
    mid = np.abs(y[SR:2 * SR]).max()
    assert np.abs(y[-k - 4000:-k - 100]).max() > 0.9 * mid        # antes del fundido: intacta
    assert abs(info["lufs"] + 14.0) < 0.5


def test_intro_fade_explicito_se_respeta_con_arranque_horneado(tmp_path):
    """fade_in (0.2 de los planes viejos) NO suaviza el arranque horneado por music.py; la
    entrada suave explicita (intro_fade) si se respeta."""
    rng = np.random.default_rng(3)
    mu = _write(tmp_path / "m.wav", _music(10.0, rng))
    lv = {}
    for k, extra in {"legacy": {"fade_in": 0.2}, "suave": {"intro_fade": 1.0}}.items():
        cfg = dict({"ending": {"mode": "hit", "hit": 9.5}, "wav": str(mu), "fade_out": 1.5}, **extra)
        info = A.mix(None, 10.0, [], cfg, tmp_path / "o.wav", target_lufs=None, stems_dir=tmp_path / k)
        m, _ = sf.read(info["stems"]["music"], always_2d=True)
        r = lambda a, b: 10 * np.log10((m[int(a * SR):int(b * SR)] ** 2).mean() + 1e-20)  # noqa: E731
        lv[k] = (r(0.0, 0.1) - r(5.0, 6.0), info["music"]["fade_in"])
    assert lv["legacy"][1] <= 0.01 and lv["suave"][1] == pytest.approx(1.0)      # info redondea a 0.01
    assert lv["suave"][0] < lv["legacy"][0] - 10, lv


def _load_factor() -> float:
    """Holgura por carga comun a la suite (conftest.load_factor)."""
    from conftest import load_factor
    return load_factor()
