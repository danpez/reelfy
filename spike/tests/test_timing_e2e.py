"""E2E de la sincronia de subtitulos SIN el alineador MMS_FA, con el whisper.cpp real.

Voz sintetica en espanol (`say -v Paulina`, texto inventado) con una pausa conocida de
1.5 s -> pipeline.transcribe (DTW encendido: -nfa -ojf) -> load_words -> align_words sin
modelo (plan B) -> grupos de subtitulos -> Timeline.map_words con la pausa cortada.
Regresiones: el DTW se apagaba en silencio con flash-attention; sin el modelo se usaba el
timing crudo de whisper y el render perdia palabras. Se salta sin `say` o sin el modelo de
voz descargado en esta maquina.
"""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import paths

SR = 16000
PAUSA = 1.5
FRASES = ("Hoy vamos a preparar una sopa de lentejas muy sencilla.",
          "Primero lavamos las lentejas con agua fria y las dejamos reposar.")


def _whisper_model():
    for c in (paths.model_path(),
              Path.home() / "Library/Application Support/online.mixiuh.reelfy/models/ggml-large-v3-turbo.bin",
              Path.home() / "Library/Application Support/Reelfy/models/ggml-large-v3-turbo.bin"):
        if c and Path(c).exists() and Path(c).stat().st_size > 1_000_000_000:
            return Path(c)
    return None


MODEL = _whisper_model()
pytestmark = pytest.mark.skipif(
    MODEL is None or shutil.which("say") is None or not Path(paths.WCLI).exists(),
    reason="sin `say`, whisper-cli o el modelo de voz en esta maquina")


@pytest.fixture(scope="module")
def caso(tmp_path_factory):
    """wav 16k con: 0.4 s de silencio + frase 1 + PAUSA + frase 2 + 0.5 s; bordes reales."""
    d = tmp_path_factory.mktemp("timing_e2e")
    partes = []
    for k, txt in enumerate(FRASES):
        aiff, wav = d / f"p{k}.aiff", d / f"p{k}.wav"
        r = subprocess.run(["say", "-v", "Paulina", "-o", str(aiff), txt], capture_output=True)
        if r.returncode != 0 or not aiff.exists():
            pytest.skip("la voz Paulina de `say` no esta instalada")
        subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-i", str(aiff), "-ar", str(SR),
                        "-ac", "1", str(wav)], check=True)
        a, _ = sf.read(str(wav), dtype="float32")
        idx = np.nonzero(np.abs(a) > 0.02)[0]
        partes.append(a[idx[0]:idx[-1] + 1])                     # recorta el silencio del TTS
    sil = lambda s: np.zeros(int(s * SR), dtype=np.float32)      # noqa: E731
    audio = np.concatenate([sil(0.4), partes[0], sil(PAUSA), partes[1], sil(0.5)])
    audio += np.random.default_rng(0).normal(0, 0.001, len(audio)).astype(np.float32)
    fin1 = 0.4 + len(partes[0]) / SR
    ini2 = fin1 + PAUSA
    wav = d / "voz.wav"
    sf.write(str(wav), audio, SR, subtype="PCM_16")
    return {"dir": d, "wav": wav, "fin1": fin1, "ini2": ini2, "dur": len(audio) / SR}


@pytest.fixture(scope="module")
def transcrito(caso):
    import pipeline
    mp = pytest.MonkeyPatch()
    mp.setattr(pipeline.paths_mod, "model_path", lambda: MODEL)
    mp.setattr(pipeline.paths_mod, "aligner_model_ready", lambda: False)   # sin MMS_FA: pide el DTW
    try:
        jp = pipeline.transcribe(caso["wav"], caso["dir"] / "voz")
    finally:
        mp.undo()
    return jp, pipeline.load_words(jp)


def test_whisper_entrega_tiempos_dtw(transcrito):
    """Regresion: con flash-attention (default de whisper.cpp) --dtw se apagaba en silencio."""
    jp, words = transcrito
    data = json.loads(Path(jp).read_text())
    toks = [t for s in data["transcription"] for t in s.get("tokens") or []]
    assert toks and any(t.get("t_dtw", -1) >= 0 for t in toks)
    assert len(words) >= 15
    assert sum(1 for w in words if w["tsrc"] == "dtw") >= 0.7 * len(words)


def test_plan_b_sin_modelo_pega_los_subtitulos_a_la_voz(monkeypatch, caso, transcrito):
    import align
    from motion.timeline import WORD_MIN
    _, words = transcrito
    monkeypatch.setattr(align, "_get_session", lambda: None)
    out = align.align_words(caso["wav"], words)
    assert align.LAST["method"] == "dtw" and align.last_note() == align.NOTE_DTW
    assert [w["text"] for w in out] == [w["text"] for w in words]      # ninguna se pierde
    assert all("tsrc" not in w for w in out)
    assert all(w["end"] - w["start"] >= WORD_MIN - 1e-3 for w in out)
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(out, out[1:]))
    # ninguna palabra se estira sobre la pausa; los bordes de la pausa quedan en la voz
    fin1, ini2 = caso["fin1"], caso["ini2"]
    assert not any(w["start"] < fin1 - 0.05 and w["end"] > ini2 + 0.05 for w in out)
    antes = [w for w in out if w["start"] < fin1]
    despues = [w for w in out if w["start"] >= fin1]
    assert antes and despues
    assert antes[-1]["end"] == pytest.approx(fin1, abs=0.15)
    assert despues[0]["start"] == pytest.approx(ini2, abs=0.12)
    assert out[0]["start"] == pytest.approx(0.4, abs=0.15)


@pytest.mark.skipif(not paths.aligner_model_ready(), reason="sin el modelo MMS_FA en esta maquina")
def test_plan_b_cerca_de_mms(monkeypatch, caso, transcrito):
    """Con MMS_FA disponible como referencia: el plan B queda a <= 80 ms de mediana."""
    import align
    _, words = transcrito
    ref = align.align_words(caso["wav"], words)
    assert align.LAST["method"] == "mms" and align.last_note() is None
    monkeypatch.setattr(align, "_get_session", lambda: None)
    out = align.align_words(caso["wav"], words)
    err = np.array([abs(a["start"] - b["start"]) for a, b in zip(out, ref)])
    assert np.median(err) <= 0.08 and np.percentile(err, 90) <= 0.2, err


def test_subtitulos_con_la_pausa_cortada_no_pierden_palabras(monkeypatch, caso, transcrito):
    import align
    import pipeline
    from motion.timeline import Timeline
    _, words = transcrito
    monkeypatch.setattr(align, "_get_session", lambda: None)
    out = align.align_words(caso["wav"], words)
    phrases = pipeline.group_phrases(out)
    plan = {"duration": caso["dur"], "phrases": phrases,
            "cuts": [{"start": round(caso["fin1"] + 0.1, 2), "end": round(caso["ini2"] - 0.08, 2),
                      "enabled": True}]}
    tl = Timeline.from_plan(plan, fps=30)
    fin = tl.map_words(plan["phrases"])
    flat = [w for ph in fin for w in ph]
    assert [w["text"] for w in flat] == [w["text"] for w in out]
    assert tl.final_duration < caso["dur"] - 1.0
    assert all(0 <= w["start"] < w["end"] <= tl.final_duration + 1e-6 for w in flat)
