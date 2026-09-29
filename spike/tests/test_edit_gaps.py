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


def _voz_db(tmp_path, parts):
    """parts: [(dur, dB)] tono de voz a ese nivel (None = silencio digital con ruido minimo)."""
    rng = np.random.default_rng(3)
    y = []
    for d, lvl in parts:
        n = int(d * SR)
        t = np.arange(n) / SR
        if lvl is None:
            s = rng.normal(0, 10 ** (-85 / 20), n)
        else:
            s = 10 ** (lvl / 20) * np.sqrt(2) * np.sin(2 * np.pi * 180 * t)
        y.append(s.astype(np.float32))
    p = tmp_path / "v.wav"
    sf.write(p, np.concatenate(y), SR)
    return str(p)


def test_final_de_frase_dicho_bajito_no_se_corta(tmp_path):
    # voz normal (-20 dB) | "vista." dicha bajito (-48 dB) | silencio | voz
    p = _voz_db(tmp_path, [(1.5, -20), (0.3, -48), (1.2, None), (1.5, -20)])
    words = [{"text": "punto", "start": 0.9, "end": 1.2}, {"text": "de", "start": 1.2, "end": 1.5},
             {"text": "vista.", "start": 1.5, "end": 1.8}, {"text": "Ahora", "start": 3.0, "end": 3.5}]
    keeps, _ = edit.keep_segments(p, words)
    assert any(a <= 1.5 and b >= 1.8 + 0.05 for a, b in keeps)      # la palabra queda entera
    # la pausa despues de la palabra si se corta
    assert not any(a < 2.4 < b for a, b in keeps)


def test_palabra_alucinada_en_silencio_no_protege(tmp_path):
    # whisper pone una palabra en pleno silencio: el silencio se corta igual
    p = _voz_db(tmp_path, [(1.5, -20), (1.5, None), (1.5, -20)])
    words = [{"text": "hola", "start": 1.0, "end": 1.5}, {"text": "gracias", "start": 2.0, "end": 2.4},
             {"text": "adios", "start": 3.0, "end": 3.5}]
    keeps, _ = edit.keep_segments(p, words)
    assert not any(a < 2.2 < b for a, b in keeps)


def test_palabra_estirada_solo_protege_su_nucleo(tmp_path):
    # "nada." dicha 1.5-1.8 pero con tiempo estirado hasta 3.9: la pausa se sigue cortando
    p = _voz_db(tmp_path, [(1.5, -20), (0.3, -24), (2.2, None), (1.5, -20)])
    words = [{"text": "para", "start": 1.1, "end": 1.5}, {"text": "nada.", "start": 1.5, "end": 3.9},
             {"text": "Ahora", "start": 4.0, "end": 4.5}]
    keeps, _ = edit.keep_segments(p, words)
    assert any(a <= 1.5 and b >= 1.8 for a, b in keeps)
    assert not any(a < 3.0 < b for a, b in keeps)
