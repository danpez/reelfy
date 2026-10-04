"""R4b: un arranque corto que termina en "no," y se rehace enseguida repitiendo sus palabras ("Con un punto
no, a un punto le asignas una tarea", que Kevin quito a mano en todas las versiones aprobadas de un video
real; Limpieza de tomas decia "Arranques 0"). Texto inventado; la voz de la prueba E2E es `say -v
Paulina` transcrita con el whisper.cpp real (se omite sin `say` o sin el modelo)."""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import paths
import takes as T


def _words(text, t0=0.3, pause_after=None):
    out, t = [], t0
    for w in text.split():
        d = 0.08 + 0.045 * len(w.strip(".,"))
        out.append({"text": w, "start": round(t, 3), "end": round(t + d, 3)})
        t += d + (0.42 if pause_after and w in pause_after else 0.04)
    return out


def _cuts(words):
    dur = words[-1]["end"] + 0.5
    return [c for c in T.detect(words, dur)["cuts"]]


def test_arranque_corregido_con_no_se_quita():
    ws = _words("Y se acabó. Con un punto no, a un punto le asignas una tarea específica.", pause_after={"no,"})
    cs = _cuts(ws)
    fs = [c for c in cs if c["kind"] == "arranque_fallido"]
    assert len(fs) == 1 and fs[0]["enabled"] is True and fs[0]["level"] == "media"
    c = fs[0]
    assert c["text"].strip() == "Con un punto no,"
    con = next(w for w in ws if w["text"] == "Con")
    a = next(w for w in ws if w["text"] == "a")
    assert c["start"] <= con["start"] + 0.02 and con["start"] - 0.2 <= c["start"]
    assert c["end"] <= a["start"] + 1e-6 and c["end"] >= ws[ws.index(a) - 1]["end"]
    assert T.LABELS[c["kind"]] == "Arranque en falso"
    # con coma antes del "no" (como a veces puntua whisper) tambien
    ws2 = _words("Y se acabó. Con un punto, no, a un punto le asignas una tarea.", pause_after={"no,"})
    assert any(c["kind"] in ("arranque_fallido", "autocorreccion") and "Con un punto" in c["text"] for c in _cuts(ws2))


@pytest.mark.parametrize("txt", [
    "Esto no es para un día no, es para un año completo.",          # contraste con negacion: se queda
    "Es un producto no, es una experiencia completa.",               # contraste sin palabras repetidas
    "Te gustó el pan no, sino el café de la casa.",                  # 'sino'
    "Lo hice yo no, no, no lo hice yo.",                             # enfasis
    "Unos dicen que no, otros dicen que sí funciona.",               # el "no" es respuesta
    "Unos dicen que no. Otros dicen que sí funciona.",
    "Le preguntas si funciona, ¿no? Si funciona, te contesta.",       # pregunta
    "Hoy vamos a hablar de agentes y de cómo funcionan en tu día a día, no, de verdad.",
])
def test_vetos(txt):
    assert not [c for c in _cuts(_words(txt)) if c.get("text", "").strip().endswith("no,")
                and c["kind"] == "arranque_fallido"], txt


def _whisper_model():
    for c in (paths.model_path(),
              Path.home() / "Library/Application Support/online.mixiuh.reelfy/models/ggml-large-v3-turbo.bin",
              Path.home() / "Library/Application Support/Reelfy/models/ggml-large-v3-turbo.bin"):
        if c and Path(c).exists() and Path(c).stat().st_size > 1_000_000_000:
            return Path(c)
    return None


@pytest.mark.skipif(_whisper_model() is None or shutil.which("say") is None or not Path(paths.WCLI).exists(),
                    reason="sin `say`, whisper-cli o el modelo de voz en esta maquina")
def test_voz_real_con_un_punto_no(tmp_path):
    import soundfile as sf
    import pipeline
    SR = 16000
    parts = []
    for k, txt in enumerate(("Cuando entras a la aplicación le preguntas algo y se acabó.", "Con un punto no,",
                             "a un punto le asignas una tarea específica.")):
        aiff, wav = tmp_path / f"p{k}.aiff", tmp_path / f"p{k}.wav"
        r = subprocess.run(["say", "-v", "Paulina", "-o", str(aiff), txt], capture_output=True)
        if r.returncode != 0 or not aiff.exists():
            pytest.skip("la voz Paulina de `say` no esta instalada")
        subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-i", str(aiff), "-ar", str(SR), "-ac", "1", str(wav)],
                       check=True)
        a, _ = sf.read(str(wav), dtype="float32")
        idx = np.nonzero(np.abs(a) > 0.02)[0]
        parts.append(a[idx[0]:idx[-1] + 1])
    sil = lambda s: np.zeros(int(s * SR), np.float32)      # noqa: E731
    audio = np.concatenate([sil(0.3), parts[0], sil(0.6), parts[1], sil(0.35), parts[2], sil(0.5)])
    audio += np.random.default_rng(0).normal(0, 0.001, len(audio)).astype(np.float32)
    wav = tmp_path / "voz.wav"
    sf.write(str(wav), audio, SR, subtype="PCM_16")
    model = _whisper_model()
    mp = pytest.MonkeyPatch()
    mp.setattr(pipeline.paths_mod, "model_path", lambda: model)
    try:
        jp = pipeline.transcribe(wav, tmp_path / "voz")
    finally:
        mp.undo()
    words = pipeline.load_words(jp)
    txt = " ".join(w["text"] for w in words).lower()
    assert "punto" in txt and "asignas" in txt, txt
    cs = T.detect(words, len(audio) / SR)["cuts"]
    hit = [c for c in cs if c["enabled"] and "con un punto" in c["text"].lower()]
    assert hit, (txt, [(c["kind"], c["text"]) for c in cs])
    kept = " ".join(w["text"] for w in T.effective_words(words, cs)).lower()
    assert "a un punto le asignas" in kept and "con un punto" not in kept, kept
