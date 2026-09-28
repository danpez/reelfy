"""Pausas entre palabras (edit.word_gap_silences / keep_segments con words): una pausa con ruido de
fondo y chasquidos que silencedetect (-32 dB por muestra) no ve, se recorta por energia RELATIVA a
la voz; la voz que el transcriptor no reconocio (suena fuerte) nunca se corta. Audio sintetico."""
import numpy as np
import pytest
import soundfile as sf

import edit

SR = 16000


def _audio(tmp_path, parts):
    """parts: [(dur, kind)] kind: 'voz' (tono modulado fuerte), 'ruido' (ruido -38 dB con chasquidos
    a -20 dB), 'silencio' (digital), 'voz_suave' (voz sin palabras transcritas, fuerte)."""
    rng = np.random.default_rng(1)
    y = []
    for d, k in parts:
        n = int(d * SR)
        t = np.arange(n) / SR
        if k in ("voz", "voz_suave"):
            s = 0.25 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
        elif k == "ruido":
            s = rng.normal(0, 10 ** (-38 / 20), n)
            for c in range(0, n, int(0.13 * SR)):              # chasquidos que rompen silencedetect
                s[c:c + 40] += 0.1
        else:
            s = np.zeros(n)
        y.append(s.astype(np.float32))
    p = tmp_path / "a.wav"
    sf.write(p, np.concatenate(y), SR)
    return str(p)


def _keeps(path, words=None):
    k, dur = edit.keep_segments(path, words)
    return [(round(a, 2), round(b, 2)) for a, b in k], dur


def test_pausa_con_ruido_entre_palabras_se_recorta(tmp_path):
    # voz 0-1.5 | pausa con ruido 1.5-2.4 | voz 2.4-4.0
    p = _audio(tmp_path, [(1.5, "voz"), (0.9, "ruido"), (1.6, "voz")])
    words = [{"text": "uno", "start": 0.1, "end": 1.5}, {"text": "dos", "start": 2.4, "end": 3.9}]
    antes, _ = _keeps(p)
    assert len(antes) == 1                                          # silencedetect no la ve
    ahora, _ = _keeps(p, words)
    assert len(ahora) == 2
    (a0, a1), (b0, b1) = ahora
    assert a1 == pytest.approx(1.5 + edit.GAP_PAD_OUT, abs=0.02)     # aire tras la palabra
    assert b0 == pytest.approx(2.4 - edit.GAP_PAD_IN, abs=0.02)      # aire antes de la siguiente


def test_voz_no_transcrita_no_se_corta(tmp_path):
    # el hueco entre palabras transcritas tiene VOZ fuerte (p. ej. una palabra que whisper no oyo)
    p = _audio(tmp_path, [(1.5, "voz"), (0.9, "voz_suave"), (1.6, "voz")])
    words = [{"text": "uno", "start": 0.1, "end": 1.5}, {"text": "dos", "start": 2.4, "end": 3.9}]
    ahora, _ = _keeps(p, words)
    assert len(ahora) == 1


def test_pausa_corta_se_respeta(tmp_path):
    p = _audio(tmp_path, [(1.5, "voz"), (0.3, "ruido"), (1.6, "voz")])
    words = [{"text": "uno", "start": 0.1, "end": 1.5}, {"text": "dos", "start": 1.8, "end": 3.3}]
    ahora, _ = _keeps(p, words)
    assert len(ahora) == 1                                          # < GAP_MIN: natural


def test_pausa_al_inicio_de_un_clip_antes_de_hablar(tmp_path):
    # como el clip de iPhone: ~0.9 s de ruido antes de la primera palabra
    p = _audio(tmp_path, [(0.9, "ruido"), (2.0, "voz")])
    words = [{"text": "y", "start": 0.9, "end": 1.0}, {"text": "estoy", "start": 1.05, "end": 2.8}]
    ahora, _ = _keeps(p, words)
    assert ahora[0][0] == pytest.approx(0.9 - edit.GAP_PAD_IN, abs=0.02)


def test_sin_palabras_igual_que_antes(tmp_path):
    p = _audio(tmp_path, [(1.5, "voz"), (0.9, "silencio"), (1.6, "voz")])
    assert _keeps(p) == _keeps(p, None) == _keeps(p, [])
