"""Pruebas del sintetizador de SFX (motion/sfx.py) y del catalogo generado."""
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from scipy import signal

from motion import assets, sfx
from motion.audio import SR, peak_window_lufs, true_peak_db

CONTRACT = ("whoosh_soft whoosh_fast swoosh_up swoosh_down whip pop pop_soft bubble click tick typing ding "
            "bell chime sparkle cash coin riser_short riser_long impact_soft impact_boom sub_drop glitch "
            "shutter notification success swipe scratch paper_rip pen_scribble heartbeat record_stop").split()


@pytest.fixture(scope="module")
def cat():
    return sfx.generate_all()


def test_catalogo_completo_del_contrato(cat):
    assert set(CONTRACT) <= set(cat), set(CONTRACT) - set(cat)
    assert set(sfx.NAMES) == set(CONTRACT)
    for name, e in cat.items():
        for k in ("file", "dur", "hit", "tags", "variants"):
            assert k in e, (name, k)
        assert e["variants"][0]["file"] == e["file"]
        assert e["tags"] and isinstance(e["tags"], list)
        for v in e["variants"]:
            assert (assets.SFX_DIR / v["file"]).exists(), v["file"]
            assert 0.0 <= v["hit"] < v["dur"], (name, v)
    assert len(cat["whoosh_soft"]["variants"]) == 3
    assert len(cat["whoosh_fast"]["variants"]) == 3
    assert assets.sfx_catalog() == json.loads((assets.SFX_DIR / "catalog.json").read_text())


def test_formato_48k_estereo_y_picos(cat):
    for name, e in cat.items():
        for v in e["variants"]:
            x, sr = sf.read(str(assets.SFX_DIR / v["file"]), always_2d=True)
            assert sr == SR and x.shape[1] == 2, v["file"]
            assert abs(x.shape[0] / SR - v["dur"]) < 1e-3
            assert true_peak_db(x) <= sfx.PEAK_CEIL_DB + 0.15, (v["file"], true_peak_db(x))
            assert abs(float(x.mean())) < 1e-3, v["file"]        # sin DC
            pk = np.abs(x).max()
            assert np.abs(x[:2]).max() < 0.05 * pk + 1e-3, v["file"]  # arranca desde cero (sin escalon)
            tail = x[-int(0.01 * SR):]
            assert np.abs(tail).max() < 10 ** (-45 / 20) * pk + 2e-4, v["file"]  # termina en silencio


def test_loudness_coherente_por_categoria(cat):
    """L100 en el objetivo de su categoria salvo que el techo de pico lo limite."""
    for name, e in cat.items():
        target = sfx.SPECS[name][3]
        x, _ = sf.read(str(assets.SFX_DIR / e["file"]), always_2d=True)
        lu = peak_window_lufs(x, 0.1)
        limited = true_peak_db(x) > sfx.PEAK_CEIL_DB - 0.4
        assert abs(lu - target) < 0.6 or (limited and lu < target), (name, lu, target)


def _env(x, win_s):
    e = np.abs(x).max(axis=1)
    w = max(1, int(win_s * SR))
    return np.convolve(e, np.ones(w) / w, mode="same")


@pytest.mark.parametrize("name", ["pop", "pop_soft", "click", "tick", "whip", "shutter", "bubble"])
def test_hit_es_el_pico_en_puntuales(cat, name):
    for v in cat[name]["variants"]:
        x, _ = sf.read(str(assets.SFX_DIR / v["file"]), always_2d=True)
        pk = np.argmax(_env(x, 0.001)) / SR
        assert abs(pk - v["hit"]) <= 0.005, (v["file"], pk, v["hit"])


@pytest.mark.parametrize("name", ["ding", "bell", "coin", "chime", "notification", "success", "sparkle",
                                  "cash", "typing", "glitch", "heartbeat", "sub_drop", "impact_soft",
                                  "impact_boom"])
def test_hit_es_el_ataque_en_tonales(cat, name):
    """Sonidos con cola larga o cuerpo limitado (impactos): el golpe es el ataque (primer cruce
    del 50 % del maximo); en un impacto masterizado el cuerpo toca el mismo techo que el ataque,
    asi que el maximo de la envolvente no define el golpe: el oido se engancha al ataque."""
    for v in cat[name]["variants"]:
        x, _ = sf.read(str(assets.SFX_DIR / v["file"]), always_2d=True)
        e = _env(x, 0.001)
        h = int(v["hit"] * SR)
        w = int(0.005 * SR)
        loc = e[max(0, h - w):h + w]
        # a partir del hit hay energia comparable al maximo cercano, y antes no
        near = e[h:h + int(0.05 * SR)].max()
        assert loc.max() >= 0.5 * near, (v["file"],)
        assert e[: max(1, h - w)].max() < 0.5 * near + 1e-3, (v["file"],)


@pytest.mark.parametrize("name", ["whoosh_soft", "whoosh_fast", "swoosh_up", "swoosh_down", "swipe"])
def test_hit_es_el_paso_en_whooshes(cat, name):
    """El hit (paso mas cercano del disenio: doppler y cruce del paneo) cae en la zona mas fuerte.
    Con la limitacion del masterizado la cima es una meseta: el hit debe estar dentro de ella."""
    for v in cat[name]["variants"]:
        x, _ = sf.read(str(assets.SFX_DIR / v["file"]), always_2d=True)
        e = _env(x, 0.03)
        h = int(v["hit"] * SR)
        top = np.nonzero(e >= 0.9 * e.max())[0]
        assert e[h] >= 0.85 * e.max(), (v["file"], e[h] / e.max())
        assert top[0] / SR - 0.015 <= v["hit"] <= top[-1] / SR + 0.015, (v["file"], v["hit"], top[0] / SR, top[-1] / SR)
        if name in ("whoosh_fast", "swipe"):   # los rapidos siguen siendo un pico claro
            assert abs(np.argmax(e) / SR - v["hit"]) <= 0.03, v["file"]


def test_riser_culmina_en_el_hit(cat):
    for name in ("riser_short", "riser_long"):
        e = cat[name]
        x, _ = sf.read(str(assets.SFX_DIR / e["file"]), always_2d=True)
        env = _env(x, 0.05)
        h = int(e["hit"] * SR)
        before = env[: h - int(0.05 * SR)]
        assert env[h - int(0.04 * SR)] > 0.6 * env.max()
        # crescendo: el primer tercio es mucho mas bajo que el final
        assert before[: len(before) // 3].max() < 0.35 * env.max()


def test_whoosh_se_mueve_en_estereo(cat):
    """Paneo en movimiento: el balance L/R cambia de signo a lo largo del whoosh."""
    x, _ = sf.read(str(assets.SFX_DIR / cat["whoosh_fast"]["file"]), always_2d=True)
    h = int(cat["whoosh_fast"]["hit"] * SR)
    w = int(0.06 * SR)
    a = x[h - 3 * w:h - w]
    b = x[h + w:h + 3 * w]
    bal = lambda s: 10 * np.log10((s[:, 1] ** 2).sum() / (s[:, 0] ** 2).sum())  # noqa: E731
    assert bal(a) * bal(b) < 0 and abs(bal(a) - bal(b)) > 3


def test_swoosh_up_barre_hacia_arriba(cat):
    x, _ = sf.read(str(assets.SFX_DIR / cat["swoosh_up"]["file"]), always_2d=True)
    m = x.mean(axis=1)
    h = int(cat["swoosh_up"]["hit"] * SR)

    def centroid(s):
        S = np.abs(np.fft.rfft(s * np.hanning(len(s))))
        f = np.fft.rfftfreq(len(s), 1 / SR)
        return (S * f).sum() / S.sum()

    w = int(0.08 * SR)
    assert centroid(m[h - w:h]) > 1.8 * centroid(m[h // 3 - w // 2:h // 3 + w // 2])


def test_determinista_y_variantes_distintas():
    a, ha, _ = sfx.render("pop", 0)
    b, hb, _ = sfx.render("pop", 0)
    assert np.array_equal(a, b) and ha == hb
    c, _, _ = sfx.render("pop", 1)
    n = min(len(a), len(c))
    corr = np.corrcoef(a[:n, 0], c[:n, 0])[0, 1]
    assert corr < 0.9


def test_generate_all_idempotente_y_rapido(tmp_path, cat):
    t0 = time.perf_counter()
    cat2 = sfx.generate_all()
    assert time.perf_counter() - t0 < 1.0
    assert cat2 == cat
    t0 = time.perf_counter()
    out = sfx.generate_all(force=True, out_dir=tmp_path)
    dt = time.perf_counter() - t0
    assert dt < 30.0, dt
    assert set(out) == set(CONTRACT)
    assert len(list(tmp_path.glob("*.flac"))) == sum(len(e["variants"]) for e in out.values())
    # misma sintesis byte a byte (determinismo entre corridas y procesos)
    for e in out.values():
        a, _ = sf.read(str(tmp_path / e["file"]))
        b, _ = sf.read(str(assets.SFX_DIR / e["file"]))
        assert np.array_equal(a, b), e["file"]


def test_tamano_razonable_en_repo(cat):
    total = sum((assets.SFX_DIR / v["file"]).stat().st_size for e in cat.values() for v in e["variants"])
    assert total < 8e6, total


def test_hit_de_cash_es_la_campana(cat):
    e = cat["cash"]
    x, _ = sf.read(str(assets.SFX_DIR / e["file"]), always_2d=True)
    m = x.mean(axis=1)
    h = int(e["hit"] * SR)
    w = int(0.03 * SR)
    seg = m[h:h + w]
    S = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    assert 1800 < f[np.argmax(S)] < 6000
    assert math.isclose(e["hit"], 0.0795, abs_tol=0.01)


def test_compatible_con_mono(cat):
    """Telefonos en mono: la suma L+R no cancela (perdida <= 3.5 dB, sin correlacion negativa)."""
    for name, e in cat.items():
        for v in e["variants"]:
            x, _ = sf.read(str(assets.SFX_DIR / v["file"]), always_2d=True)
            m = (x[:, 0] + x[:, 1]) / 2
            loss = peak_window_lufs(x, 0.1) - peak_window_lufs(np.stack([m, m], 1), 0.1)
            assert loss <= 3.5, (v["file"], loss)
            assert np.corrcoef(x[:, 0], x[:, 1])[0, 1] > -0.2, v["file"]


def test_sin_energia_ultrasonica(cat):
    for name, e in cat.items():
        if name == "glitch":
            continue
        x, _ = sf.read(str(assets.SFX_DIR / e["file"]), always_2d=True)
        m = x.mean(axis=1)
        X = np.abs(np.fft.rfft(m)) ** 2
        f = np.fft.rfftfreq(len(m), 1 / SR)
        assert 10 * np.log10(X[f > 20500].sum() / X.sum() + 1e-20) < -35, name  # solo queda el dither


_PHONE_SOS = signal.butter(4, 300, "highpass", fs=SR, output="sos")


def test_traduce_en_telefono(cat):
    """Altavoz de telefono (HPF 300 Hz de orden 4): ningun SFX pierde mas de 6 dB de L100
    (sub_drop, que es grave por definicion, hasta 10 dB). Los golpes graves llevan armonicos
    psicoacusticos y knock de cuerpo para seguir sintiendose en un telefono."""
    for name, e in cat.items():
        for v in e["variants"]:
            x, _ = sf.read(str(assets.SFX_DIR / v["file"]), always_2d=True)
            loss = peak_window_lufs(signal.sosfilt(_PHONE_SOS, x, axis=0), 0.1) - peak_window_lufs(x, 0.1)
            assert loss >= (-10.0 if name == "sub_drop" else -6.0), (v["file"], round(loss, 1))


def test_impactos_conservan_el_sub(cat):
    """La traduccion a telefono no convierte los golpes en ruido medio: el sub sigue ahi."""
    lo = signal.butter(4, [30, 120], "bandpass", fs=SR, output="sos")
    for name in ("impact_soft", "impact_boom", "sub_drop", "heartbeat"):
        x, _ = sf.read(str(assets.SFX_DIR / cat[name]["file"]), always_2d=True)
        rel = peak_window_lufs(signal.sosfilt(lo, x, axis=0), 0.1) - peak_window_lufs(x, 0.1)
        assert rel > -7.0, (name, rel)


def test_cola_del_boom_no_enloda_la_voz(cat):
    """De 0.5 a 1.5 s despues del golpe, la zona de la F0 de la voz (80-250 Hz) queda al menos
    30 dB bajo el golpe (parciales metalicos y cuerpo del sub cortos)."""
    e = cat["impact_boom"]
    x, _ = sf.read(str(assets.SFX_DIR / e["file"]), always_2d=True)
    b = signal.sosfilt(signal.butter(4, [80, 250], "bandpass", fs=SR, output="sos"), x.mean(axis=1))
    h = int(e["hit"] * SR)
    rms = lambda a, c: 10 * np.log10((b[a:c] ** 2).mean() + 1e-20)  # noqa: E731
    assert rms(h + int(0.5 * SR), h + int(1.5 * SR)) < rms(h, h + int(0.1 * SR)) - 30


def test_metadatos_root_y_loop(cat):
    """Tonales con tonica (para transponer a la tonalidad de la musica) y continuos con zona
    sostenida valida (para alargarlos con `dur`)."""
    for name, roots in sfx.ROOTS.items():
        for i, v in enumerate(cat[name]["variants"]):
            assert v["root"] == roots[min(i, len(roots) - 1)]
            assert 0 <= v["root"] < 12
    looped = {n for n, e in cat.items() if any("loop" in v for v in e["variants"])}
    assert {"typing", "pen_scribble", "glitch", "heartbeat", "paper_rip"} <= looped
    for name in looped:
        for v in cat[name]["variants"]:
            a, b = v["loop"]
            assert 0 <= a < b <= v["dur"] and b - a > 0.2, (v["file"], a, b)
    # el loop del latido es un ciclo exacto
    a, b = cat["heartbeat"]["loop"]
    assert abs((b - a) - (sfx.HEART_BEATS[2][0] - sfx.HEART_BEATS[0][0])) < 1e-3


def test_firma_solo_depende_del_masterizado():
    """Cambiar el mezclador (mix, ducking...) no regenera los FLAC versionados: la firma solo
    incluye sfx.py y las funciones de medicion/limitador que usa el masterizado."""
    assert "mix" not in sfx._MASTER_DEPS and "music_gain_curve" not in sfx._MASTER_DEPS
    import inspect
    from motion import audio as A
    src = "".join(inspect.getsource(getattr(A, n)) for n in sfx._MASTER_DEPS)
    assert "def mix(" not in src and "def build_sfx_bus(" not in src
    assert sfx._code_hash() == sfx._code_hash()


def test_generacion_concurrente_atomica(tmp_path):
    """Dos procesos generando a la vez en la misma carpeta: candado + escritura atomica ->
    catalogo valido, FLAC completos y sin temporales sueltos."""
    names = ["pop", "click", "ding"]
    code = ("import sys; sys.path.insert(0, %r); from motion import sfx; "
            "sfx.generate_all(force=True, out_dir=%r, names=%r, workers=1)") % (
        str(Path(sfx.__file__).resolve().parents[1]), str(tmp_path), names)
    procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(2)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    cat2 = json.loads((tmp_path / "catalog.json").read_text())
    assert set(cat2) == set(names)
    for e in cat2.values():
        for v in e["variants"]:
            x, _ = sf.read(str(tmp_path / v["file"]), always_2d=True)
            assert abs(x.shape[0] / SR - v["dur"]) < 1e-3
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob("*.lock"))
