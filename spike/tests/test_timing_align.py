"""Sincronia de subtitulos sin el alineador MMS_FA (align.py, plan B): tiempos DTW de
whisper.cpp corregidos, reparacion por energia del audio, aviso en espanol y URL del modelo
configurable. Audio y transcripciones sinteticos (tonos con pausas conocidas)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import align as A
from motion.timeline import WORD_MIN

SR = 16000
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


# ------------------------------------------------------------------ fixtures
def voz(spans, total, seed=0, ruido=0.002, digital=False):
    """Audio sintetico: 'voz' (armonicos de 140 Hz con vibrato) en los tramos `spans` y ruido
    de fondo (o ceros exactos, `digital`) fuera de ellos."""
    rng = np.random.default_rng(seed)
    n = int(total * SR)
    a = np.zeros(n) if digital else rng.normal(0, ruido, n)
    t = np.arange(n) / SR
    for s, e in spans:
        i, j = int(s * SR), int(e * SR)
        tt = t[i:j]
        f0 = 140 * (1 + 0.03 * np.sin(2 * np.pi * 5 * tt))
        ph = 2 * np.pi * np.cumsum(f0) / SR
        sig = sum(np.sin(k * ph) / k for k in range(1, 6))
        env = np.minimum(1.0, np.minimum(tt - s, e - tt) / 0.01)      # rampas de 10 ms
        a[i:j] += 0.25 * sig * env
    return a.astype(np.float32)


def W(text, s, e, **kw):
    return {"text": text, "start": s, "end": e, **kw}


def seg(text, t0, t1, toks):
    """Segmento de whisper.cpp -ojf: toks = [(texto, t_dtw en s o None)]."""
    return {"text": text, "offsets": {"from": int(t0 * 1000), "to": int(t1 * 1000)},
            "tokens": [{"text": x, "t_dtw": -1 if d is None else int(round(d * 100)), "p": 0.9,
                        "id": 0} for x, d in toks]}


def js(*segs):
    return {"transcription": [seg("", 0, 0, [("[_BEG_]", None)])] + list(segs)}


# ------------------------------------------------------------------ whisper_words (DTW)
def test_whisper_words_usa_el_dtw_corregido():
    d = js(seg(" Hola", 0.0, 0.9, [(" Hola", 1.20)]),
           seg(" amigos.", 0.9, 2.5, [(" amig", 1.60), ("os", 1.80), (".", 2.10)]),
           seg(" Hoy", 2.5, 3.0, [(" Hoy", 2.60), ("[_TT_150]", None)]))
    ws = A.whisper_words(d)
    assert [w["text"] for w in ws] == ["Hola", "amigos.", "Hoy"]
    assert all(w["tsrc"] == "dtw" for w in ws)
    s0 = 1.20 - (A.DTW_START_A + A.DTW_START_B * 4)
    e0 = 1.20 + A.DTW_END_C + A.DTW_END_D * 4 + A.DTW_END_E * (1.60 - 1.20)
    assert ws[0]["start"] == pytest.approx(s0, abs=1e-3) and ws[0]["end"] == pytest.approx(e0, abs=1e-3)
    # el fin de 'amigos.' lo marca su signo (el token siguiente a su ultima letra)
    e1 = 1.60 + A.DTW_END_C + A.DTW_END_D * 6 + A.DTW_END_E * (2.10 - 1.60)
    assert ws[1]["end"] == pytest.approx(e1, abs=1e-3)
    assert ws[0]["start"] < 1.20 and ws[1]["start"] < 1.60          # el DTW marca tarde


def test_whisper_words_json_sin_tokens_usa_whisper():
    """El JSON -oj de versiones anteriores (sin tokens) sigue funcionando."""
    d = {"transcription": [{"text": "", "offsets": {"from": 0, "to": 0}},
                           {"text": " Hola", "offsets": {"from": 0, "to": 400}},
                           {"text": " mundo", "offsets": {"from": 400, "to": 900}}]}
    ws = A.whisper_words(d)
    assert ws == [W("Hola", 0.0, 0.4, tsrc="whisper"), W("mundo", 0.4, 0.9, tsrc="whisper")]
    assert A.whisper_words({}) == [] and A.whisper_words(None) == []


def test_whisper_words_ventana_dtw_colapsada_vuelve_a_whisper():
    """El DTW a veces pone toda una ventana en el mismo instante: esas palabras usan whisper."""
    buenas = [seg(f" palabra{k}", k * 0.5, k * 0.5 + 0.4, [(f" palabra{k}", k * 0.5 + 0.15)])
              for k in range(10)]
    malas = [seg(f" otra{k}", 5 + k * 0.4, 5 + k * 0.4 + 0.3, [(f" otra{k}", 5.9)]) for k in range(3)]
    ws = A.whisper_words(js(*buenas, *malas))
    assert [w["tsrc"] for w in ws] == ["dtw"] * 10 + ["whisper"] * 3
    assert ws[10]["start"] == pytest.approx(5.0)
    # si falla en mas del 30 % de las palabras se descarta el DTW entero
    ws = A.whisper_words(js(*buenas[:4], *malas))
    assert all(w["tsrc"] == "whisper" for w in ws)


def test_whisper_words_dtw_lejos_de_whisper_se_descarta():
    segs = [seg(f" palabra{k}", k * 0.5, k * 0.5 + 0.4, [(f" palabra{k}", k * 0.5 + 0.15)])
            for k in range(9)]
    segs.append(seg(" lejos", 4.5, 4.9, [(" lejos", 9.0)]))
    ws = A.whisper_words(js(*segs))
    assert ws[-1]["tsrc"] == "whisper" and ws[-1]["start"] == pytest.approx(4.5)


# ------------------------------------------------------------------ energia
def test_pausas_por_energia():
    a = voz([(0.5, 1.2), (1.25, 1.9), (2.6, 3.4)], 4.0)
    P = A._pauses(a)
    assert P is not None
    # 50 ms entre 1.2 y 1.25 no es pausa; 0-0.5, 1.9-2.6 y 3.4-4.0 si
    assert len(P) == 3, P
    for (ps, pe), (xs, xe) in zip(P, [(0.0, 0.5), (1.9, 2.6), (3.4, 4.0)]):
        assert ps == pytest.approx(xs, abs=0.03) and pe == pytest.approx(xe, abs=0.03)
    # silencio digital (camara con compuerta de ruido): el piso no se va a -100 dB
    Pd = A._pauses(voz([(0.5, 1.2), (2.6, 3.4)], 4.0, digital=True))
    assert Pd and Pd[1][0] == pytest.approx(1.2, abs=0.03) and Pd[1][1] == pytest.approx(2.6, abs=0.03)
    # sin contraste voz/fondo (ruido fuerte o musica) la energia no sirve -> None
    assert A._pauses(np.random.default_rng(1).normal(0, 0.2, 4 * SR)) is None
    assert A._pauses(np.zeros(100)) is None


SPANS = [(0.5, 0.9), (0.95, 1.4), (2.6, 3.1), (3.15, 3.6)]   # pausa de 1.2 s entre 1.4 y 2.6


def test_reparacion_whisper_crudo_estirada_sobre_la_pausa():
    """Whisper crudo mete la pausa DENTRO de la palabra siguiente (empieza segundos antes)."""
    a = voz(SPANS, 4.2)
    ws = [W("hola", 0.2, 0.9), W("que", 0.9, 1.4), W("tal", 1.4, 3.1), W("amigos", 3.1, 3.6)]
    out = A.repair_timings(ws, a, keep="end")
    assert [w["text"] for w in out] == ["hola", "que", "tal", "amigos"]
    assert out[0]["start"] == pytest.approx(0.5, abs=0.04)          # arranca con la voz
    assert out[2]["start"] == pytest.approx(2.6, abs=0.04)          # ya no se traga la pausa
    assert out[2]["end"] == pytest.approx(3.1, abs=0.04)
    assert out[1]["end"] == pytest.approx(1.4, abs=0.04)


def test_reparacion_dtw_tarde_tras_la_pausa_y_fin_dentro_de_la_pausa():
    a = voz(SPANS, 4.2)
    ws = [W("hola", 0.55, 0.92), W("que", 0.97, 1.75), W("tal", 2.75, 3.12), W("amigos", 3.2, 3.55)]
    out = A.repair_timings(ws, a, keep="start")
    assert out[1]["end"] == pytest.approx(1.4, abs=0.04)            # fin pegado a donde calla
    assert out[2]["start"] == pytest.approx(2.6, abs=0.04)          # inicio adelantado a la voz
    assert out[0]["start"] == pytest.approx(0.5, abs=0.06)
    # lejos de una pausa (habla continua) no se inventa nada
    assert out[3]["start"] == pytest.approx(3.2, abs=1e-3)


def test_reparacion_con_audio_no_pierde_palabras_de_duracion_cero():
    a = voz(SPANS, 4.2)
    ws = [W("hola", 0.5, 0.9), W("y", 0.9, 0.9), W("que", 0.9, 0.9), W("tal", 2.6, 3.1),
          W("amigos", 3.15, 3.6)]
    out = A.repair_timings(ws, a)
    assert len(out) == 5
    assert all(w["end"] - w["start"] >= WORD_MIN - 1e-3 for w in out)
    assert all(a_["end"] <= b["start"] + 1e-6 for a_, b in zip(out, out[1:]))


def test_racha_sin_dtw_se_reparte_sobre_la_voz_entre_sus_vecinas():
    a = voz([(0.5, 0.9), (1.2, 1.6), (1.9, 2.3), (2.9, 3.3)], 3.8)
    ws = [W("uno", 0.5, 0.9), W("dos", 0.0, 3.0), W("tres", 3.0, 3.1), W("cuatro", 2.9, 3.3)]
    out = A.repair_timings(ws, a, keep="start", loose=[1, 2])
    assert 1.1 <= out[1]["start"] <= 1.3 and out[2]["start"] >= out[1]["end"] - 1e-6
    assert out[2]["end"] <= 2.35 and out[3]["start"] == pytest.approx(2.9, abs=0.04)


# ------------------------------------------------------------------ align_words sin modelo
@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "voz.wav"
    sf.write(str(p), voz(SPANS, 4.2), SR, subtype="PCM_16")
    return p


def test_align_words_sin_modelo_avisa_y_no_pierde_palabras(monkeypatch, wav):
    monkeypatch.setattr(A, "_get_session", lambda: None)
    monkeypatch.setattr(A, "_session_err", "sin modelo")
    ws = [W("hola", 0.55, 0.92, tsrc="dtw"), W("que", 0.97, 1.75, tsrc="dtw"),
          W("tal", 2.75, 2.75, tsrc="dtw"), W("amigos", 3.2, 3.55, tsrc="dtw")]
    out = A.align_words(wav, ws)
    assert [w["text"] for w in out] == ["hola", "que", "tal", "amigos"]
    assert all("tsrc" not in w for w in out)
    assert all(w["end"] - w["start"] >= WORD_MIN - 1e-3 for w in out)
    assert A.LAST["method"] == "dtw" and A.LAST["level"] == "info"
    assert A.last_note() == A.NOTE_DTW and "Sincronía fina de subtítulos no disponible" in A.NOTE_DTW
    assert A.LAST["reason"] == "sin modelo" and A.LAST["words"] == 4
    # sin DTW (JSON viejo o DTW descartado): tiempos aproximados, con aviso
    out = A.align_words(wav, [W("hola", 0.2, 0.9, tsrc="whisper"), W("mundo", 0.9, 3.1)])
    assert len(out) == 2 and A.LAST["method"] == "aprox" and A.LAST["level"] == "warn"
    assert A.last_note() == "Sincronía fina de subtítulos no disponible; usé tiempos aproximados"
    assert out[1]["start"] == pytest.approx(2.6, abs=0.04)
    # sin palabras no hay nada que avisar
    assert A.align_words(wav, []) == [] and A.last_note() is None


def test_align_words_audio_ilegible_no_lanza(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "_get_session", lambda: None)
    ws = [W("hola", 0.0, 0.0), W("mundo", 0.0, 0.5)]
    out = A.align_words(tmp_path / "no_existe.wav", ws)
    assert len(out) == 2 and all(w["end"] - w["start"] >= WORD_MIN - 1e-3 for w in out)
    assert A.LAST["method"] == "aprox"


def test_align_words_si_mms_falla_usa_el_plan_b(monkeypatch, wav):
    class Rota:
        def run(self, *a, **k):
            raise RuntimeError("sin memoria")

    monkeypatch.setattr(A, "_get_session", lambda: Rota())
    out = A.align_words(wav, [W("hola", 0.55, 0.92, tsrc="dtw"), W("que", 0.97, 1.4, tsrc="dtw")])
    assert len(out) == 2
    assert A.LAST["method"] == "dtw" and "falló" in A.LAST["reason"] and "sin memoria" in A.LAST["reason"]


def test_align_words_palabra_que_el_alineador_mete_en_un_silencio_vuelve_al_dtw(monkeypatch, tmp_path):
    """La alineacion forzada a veces arrastra palabras a una pausa lejos de donde se dijeron
    ('bueno, pasamos...' 5 s antes, en silencio): el corte de ese silencio las borraba de los
    subtitulos aunque se oyen. Si el MMS discrepa del DTW en > 1 s, cae en silencio y el DTW en
    voz, manda el DTW; si los dos caen en voz (dos tomas iguales y el texto de una sola) se queda
    el MMS."""
    p = tmp_path / "v.wav"
    sf.write(str(p), voz([(0.5, 1.5), (7.0, 8.2), (10.0, 11.0), (13.0, 14.0)], 15.0), SR, subtype="PCM_16")
    ws = [W("hola", 0.5, 1.0, tsrc="dtw"), W("bueno,", 7.0, 7.4, tsrc="dtw"), W("pasamos", 7.5, 8.1, tsrc="dtw"),
          W("otra", 10.1, 10.5, tsrc="dtw"), W("vez", 10.5, 10.9, tsrc="dtw")]
    mms = [W("hola", 0.52, 1.0), W("bueno,", 2.5, 2.8), W("pasamos", 2.8, 3.3),     # en la pausa
           W("otra", 13.1, 13.5), W("vez", 13.5, 13.9)]                           # la otra toma: voz

    class Sesion:
        pass
    monkeypatch.setattr(A, "_get_session", lambda: Sesion())
    monkeypatch.setattr(A, "_align_mms", lambda sess, audio, words: ([dict(m) for m in mms], len(mms)))
    out = A.align_words(p, ws)
    assert A.LAST["method"] == "mms" and [w["text"] for w in out] == [w["text"] for w in ws]
    assert out[0]["start"] == pytest.approx(0.52, abs=1e-3)
    assert (out[1]["start"], out[2]["start"]) == (pytest.approx(7.0, abs=0.01), pytest.approx(7.5, abs=0.01))
    assert (out[3]["start"], out[4]["start"]) == (pytest.approx(13.1, abs=0.01), pytest.approx(13.5, abs=0.01))
    # sin DTW (tsrc whisper) no hay con que comparar: se queda el MMS
    out = A.align_words(p, [dict(w, tsrc="whisper") for w in ws])
    assert out[1]["start"] == pytest.approx(2.5, abs=0.01)


@pytest.mark.skipif(not A.paths.aligner_model_ready(), reason="sin el modelo MMS_FA en esta maquina")
def test_align_words_con_mms_repara_las_que_no_alinea(wav):
    """Con el modelo: metodo mms, sin aviso; las palabras sin letras del vocabulario (cifras)
    conservan su timing pero con duracion minima."""
    ws = [W("hola", 0.5, 0.9, tsrc="dtw"), W("2024", 1.0, 1.0, tsrc="dtw"), W("que", 1.0, 1.4, tsrc="dtw"),
          W("tal", 2.6, 3.1, tsrc="dtw"), W("amigos", 3.15, 3.6, tsrc="dtw")]
    out = A.align_words(wav, ws)
    assert A.LAST["method"] == "mms" and A.last_note() is None and A.LAST["aligned"] == 4
    assert len(out) == 5 and all("tsrc" not in w for w in out)
    assert all(w["end"] - w["start"] >= WORD_MIN - 1e-3 for w in out)
    assert all(a_["start"] <= b["start"] for a_, b in zip(out, out[1:]))


# ------------------------------------------------------------------ URL del modelo
def _paths_env(tmp_path, **env):
    code = ("import json, paths; print(json.dumps({'url': paths.ALIGNER_URL, "
            "'dl': paths.aligner_downloadable(), 'model': paths.aligner_model_ready(), "
            "'ready': paths.aligner_ready(), 'size': paths.ALIGNER_SIZE}))")
    e = {k: v for k, v in os.environ.items() if not k.startswith("REELFY_ALIGNER")}
    e.update(REELFY_HOME=str(tmp_path), REELFY_DATA=str(tmp_path), PYTHONPATH=str(SCRIPTS), **env)
    r = subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, check=True)
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_url_del_alineador_configurable_y_sin_github_por_defecto(tmp_path):
    d = _paths_env(tmp_path)
    assert d["url"] == "" and not d["dl"] and not d["model"]
    assert d["ready"], "sin URL no hay nada que bajar: el setup no debe intentarlo"
    assert "github.com" not in (SCRIPTS / "paths.py").read_text()     # cuenta suspendida
    d = _paths_env(tmp_path, REELFY_ALIGNER_URL="https://espejo.example/mms_fa_int8.onnx",
                   REELFY_ALIGNER_SIZE="1234")
    assert d["dl"] and not d["model"] and not d["ready"] and d["size"] == 1234
    d = _paths_env(tmp_path, REELFY_ALIGNER_URL="ftp://raro", REELFY_ALIGNER_SIZE="x")
    assert not d["dl"] and d["ready"] and d["size"] == 357_386_092
    # con el modelo completo en DATA queda listo aunque no haya URL
    (tmp_path / "models").mkdir(exist_ok=True)
    with open(tmp_path / "models" / "mms_fa_int8.onnx", "wb") as f:
        f.truncate(2_000_000)
    d = _paths_env(tmp_path, REELFY_ALIGNER_SIZE="2000000")
    assert d["model"] and d["ready"]


def test_preload_no_hace_esperar_al_setup_sin_modelo_ni_url(monkeypatch):
    """El setup reintenta preload() con esperas: sin modelo y sin URL no hay nada que esperar."""
    monkeypatch.setattr(A, "_get_session", lambda: None)
    monkeypatch.setattr(A.paths, "aligner_model_ready", lambda: False)
    monkeypatch.setattr(A.paths, "aligner_downloadable", lambda: False)
    assert A.preload() is True
    monkeypatch.setattr(A.paths, "aligner_downloadable", lambda: True)    # se esta bajando
    assert A.preload() is False
    monkeypatch.setattr(A.paths, "aligner_model_ready", lambda: True)     # esta pero no carga
    monkeypatch.setattr(A.paths, "aligner_downloadable", lambda: False)
    assert A.preload() is False
    monkeypatch.setattr(A, "_get_session", lambda: object())
    assert A.preload() is True


def test_whisper_apaga_flash_attention_solo_si_hace_falta_el_dtw(monkeypatch, tmp_path):
    """El DTW (-nfa: sin flash-attention) cambia lo que whisper transcribe (se saltaba retomas
    dichas): solo se pide sin el alineador MMS_FA o si no carga; con el alineador, flash-attn."""
    import pipeline as P
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        (tmp_path / "t.json").write_text("{}")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(P.subprocess, "run", fake_run)
    monkeypatch.setattr(P.paths_mod, "model_path", lambda: tmp_path / "m.bin")
    monkeypatch.setattr(A, "_get_session", lambda: object())
    monkeypatch.setattr(A.paths, "aligner_model_ready", lambda: True)
    P.transcribe(tmp_path / "a.wav", tmp_path / "t")
    assert "-nfa" not in calls[-1] and "--dtw" not in calls[-1] and "-ojf" in calls[-1]
    monkeypatch.setattr(A, "_get_session", lambda: None)                  # esta pero no carga
    P.transcribe(tmp_path / "a.wav", tmp_path / "t")
    assert "-nfa" in calls[-1] and "--dtw" in calls[-1]
    monkeypatch.setattr(A.paths, "aligner_model_ready", lambda: False)    # sin modelo
    monkeypatch.setattr(A.paths, "aligner_downloadable", lambda: False)
    P.transcribe(tmp_path / "a.wav", tmp_path / "t")
    assert "-nfa" in calls[-1]
    monkeypatch.setattr(A, "_get_session", lambda: object())
    monkeypatch.setattr(A.paths, "aligner_model_ready", lambda: True)
    P.transcribe(tmp_path / "a.wav", tmp_path / "t", dtw=True)            # analyze sin align
    assert "-nfa" in calls[-1] and "--dtw" in calls[-1]


# ------------------------------------------------ palabras estiradas por la alineación forzada
def test_palabra_estirada_despues_de_la_voz_se_queda_donde_hay_voz():
    # "nada." dicha 1.0-1.3 s y estirada por el alineador hasta 3.5 s (pausa 1.3-4.0)
    a = voz([(0.2, 0.9), (1.0, 1.3), (4.0, 4.6)], 5.0)
    ws = [W("para", 0.5, 0.9), W("nada.", 1.0, 3.5), W("Y", 4.0, 4.2)]
    out, n = A._shrink_stretched(ws, A._pauses(a))
    assert n == 1
    assert out[1]["start"] == pytest.approx(1.0, abs=0.02)
    assert out[1]["end"] == pytest.approx(1.3, abs=0.06)
    assert out[0] == ws[0] and out[2] == ws[2]


def test_palabra_estirada_antes_de_la_voz_empieza_donde_arranca():
    # la alineación pone "ahora" desde 1.0 pero se dice en 3.6-4.0 (pausa antes)
    a = voz([(0.2, 0.9), (3.6, 4.0)], 4.5)
    out, n = A._shrink_stretched([W("bueno,", 0.2, 0.9), W("ahora", 1.0, 4.0)], A._pauses(a))
    assert n == 1
    assert out[1]["start"] == pytest.approx(3.6, abs=0.06)
    assert out[1]["end"] == pytest.approx(4.0, abs=0.02)


def test_palabra_larga_de_verdad_no_se_toca():
    a = voz([(0.2, 1.4)], 2.0)
    ws = [W("extraordinariamente", 0.2, 1.4)]
    out, n = A._shrink_stretched(ws, A._pauses(a))
    assert n == 0 and out == ws
