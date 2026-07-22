#!/usr/bin/env python3
"""
Forced alignment (the caption-sync wedge).

whisper.cpp gives accurate TEXT but its word timestamps drift +/- vs the audio.
Here we re-time those words with a wav2vec2 CTC forced aligner (MMS_FA, multilingual
incl. Spanish), which aligns the whole transcript to the audio globally -> tight,
non-drifting word timing. We keep whisper's original text (accents/punctuation) and
only replace start/end.

Corre con **onnxruntime** (modelo int8), NO torch: el mismo modelo pesa ~315 MB en
vez de arrastrar el framework completo (~1.5 GB), funciona igual en Win/Mac/Linux
y no depende de paquetes que no compilan en Windows.

Si el modelo no está disponible, devuelve las palabras intactas (el pipeline sigue
con el timing de whisper).
"""
import re
import unicodedata

import numpy as np

import paths

# Vocabulario de MMS_FA: blank + 26 letras + apóstrofe (sin dígitos ni separador).
VOCAB = {c: i for i, c in enumerate("-aienoutsrmkldghybpwcvjzf'qx")}
BLANK = 0
# Trozo de alineación: acota memoria y tiempo del DP (que crece ~O(duración²) si
# se alinea todo de una). A 60s la desviación vs alinear completo es mediana 6ms /
# p90 22ms — imperceptible (un frame a 30fps = 33ms) — y es 2.2x más rápido.
CHUNK_SECS = 60.0
_session = None


def _norm(s):
    """Texto -> caracteres del vocabulario (sin acentos, minúsculas)."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z']", "", s.lower())


def _get_session():
    """Sesión de onnxruntime cacheada; None si no está el modelo."""
    global _session
    if _session is not None:
        return _session
    p = paths.aligner_model_path()
    if not p or not p.exists():
        return None
    try:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        _session = ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"])
        return _session
    except Exception:  # noqa
        return None


def preload():
    """Carga el modelo para que el primer análisis no pague el costo.
    True si quedó listo; False si no (analyze funciona igual sin alineación)."""
    return _get_session() is not None


def _log_softmax(x):
    x = x - x.max(axis=-1, keepdims=True)
    return x - np.log(np.exp(x).sum(axis=-1, keepdims=True))


def _forced_align(log_probs, targets):
    """CTC forced alignment (Viterbi) sobre la secuencia EXACTA `targets`.

    log_probs: [T, V]; targets: [N] ids (sin blanks).
    Devuelve [(start_frame, end_frame)] por cada target, o (-1,-1) si no aparece.
    """
    T = log_probs.shape[0]
    N = len(targets)
    if N == 0 or T == 0:
        return [(-1, -1)] * N
    S = 2 * N + 1
    ext = np.full(S, BLANK, dtype=np.int64)
    ext[1::2] = targets
    NEG = np.float32(-1e30)

    idx = np.arange(S)
    nonblank = (idx % 2) == 1
    # Se puede saltar el blank intermedio solo entre tokens DISTINTOS.
    can_skip = np.zeros(S, dtype=bool)
    can_skip[2:] = nonblank[2:] & (ext[2:] != ext[:-2])

    prev = np.full(S, NEG, dtype=np.float32)
    prev[0] = log_probs[0, ext[0]]
    if S > 1:
        prev[1] = log_probs[0, ext[1]]
    bp = np.zeros((T, S), dtype=np.int8)   # 0=quedarse, 1=venir de s-1, 2=de s-2

    for t in range(1, T):
        one = np.full(S, NEG, dtype=np.float32)
        one[1:] = prev[:-1]
        two = np.full(S, NEG, dtype=np.float32)
        two[2:] = np.where(can_skip[2:], prev[:-2], NEG)
        cands = np.stack([prev, one, two])          # [3, S]
        best = np.argmax(cands, axis=0).astype(np.int8)
        prev = cands[best, idx] + log_probs[t, ext]
        bp[t] = best

    # backtrack desde el mejor final (último token o su blank posterior)
    s = int(S - 1 if prev[S - 1] >= prev[S - 2] else S - 2)
    path = np.zeros(T, dtype=np.int64)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(bp[t, s])          # backpointer del estado actual (no la fila)

    spans = []
    for n in range(N):
        fr = np.nonzero(path == (2 * n + 1))[0]
        spans.append((int(fr[0]), int(fr[-1]) + 1) if len(fr) else (-1, -1))
    return spans


def _align_chunk(sess, audio, words_tok):
    """Alinea un trozo. words_tok: [(indice_global, [ids])]. Devuelve {i: (ini,fin) seg}."""
    if not words_tok or audio.size == 0:
        return {}
    emissions = sess.run(None, {"waveform": audio[None, :].astype(np.float32)})[0][0]
    log_probs = _log_softmax(emissions.astype(np.float32))
    sec_per_frame = (audio.shape[0] / max(log_probs.shape[0], 1)) / 16000.0

    targets, owner = [], []
    for gi, ids in words_tok:
        for tid in ids:
            targets.append(tid); owner.append(gi)
    spans = _forced_align(log_probs, np.array(targets, dtype=np.int64))

    out = {}
    for (s, e), gi in zip(spans, owner):
        if s < 0:
            continue
        st, en = s * sec_per_frame, e * sec_per_frame
        if gi in out:
            out[gi] = (min(out[gi][0], st), max(out[gi][1], en))
        else:
            out[gi] = (st, en)
    return out


def align_words(wav_path, words):
    """Devuelve `words` con start/end re-timeados por alineación forzada.
    Conserva el timing original de las palabras que no se puedan alinear."""
    sess = _get_session()
    if sess is None:
        return [dict(w) for w in words]

    import soundfile as sf
    audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:                      # el wav del pipeline ya viene a 16k
        import librosa
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
    dur = len(audio) / 16000.0

    # tokens por palabra (las que no aportan caracteres se quedan con su timing)
    toks = [(i, [VOCAB[c] for c in _norm(w["text"]) if c in VOCAB]) for i, w in enumerate(words)]
    toks = [(i, t) for i, t in toks if t]
    if not toks:
        return [dict(w) for w in words]

    # Trocear acota la memoria del DP (O(T·S)) y es ~2.6x más rápido, pero cortar
    # en seco parte el habla y descuadra las palabras del borde. Por eso cortamos
    # en el SILENCIO más grande cerca del objetivo: cada trozo queda acústicamente
    # completo y el resultado iguala a alinear todo de una.
    chunks, cur, c_start = [], [], None
    k = 0
    while k < len(toks):
        i, ids = toks[k]
        if c_start is None:
            c_start = max(0.0, float(words[i].get("start", 0.0)) - 0.20)
        cur.append((i, ids))
        if float(words[i].get("end", 0.0)) - c_start >= CHUNK_SECS and k + 1 < len(toks):
            # buscar el mayor hueco entre palabras en la ventana siguiente
            best_gap, best_k = -1.0, k
            for j in range(k, min(k + 12, len(toks) - 1)):
                a_end = float(words[toks[j][0]].get("end", 0.0))
                b_start = float(words[toks[j + 1][0]].get("start", a_end))
                gap = b_start - a_end
                if gap > best_gap:
                    best_gap, best_k = gap, j
                if gap >= 0.35:            # silencio claro: cortar aquí
                    break
            for j in range(k + 1, best_k + 1):     # completar hasta el corte
                cur.append(toks[j])
            a_end = float(words[toks[best_k][0]].get("end", 0.0))
            b_start = float(words[toks[best_k + 1][0]].get("start", a_end))
            cut = (a_end + b_start) / 2.0
            chunks.append((c_start, cut, cur))
            cur, c_start, k = [], cut, best_k + 1
            continue
        k += 1
    if cur:
        chunks.append((c_start or 0.0, None, cur))

    timings = {}
    for c_ini, c_fin, items in chunks:
        a = int(c_ini * 16000)
        b = int(c_fin * 16000) if c_fin is not None else len(audio)
        for gi, (s, e) in _align_chunk(sess, audio[a:b], items).items():
            timings[gi] = (s + c_ini, e + c_ini)

    out = []
    for i, w in enumerate(words):
        if i in timings:
            s, e = timings[i]
            out.append({**w, "start": round(s, 3), "end": round(e, 3)})
        else:
            out.append(dict(w))

    # monotonía y duración mínima (seguridad)
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["start"]:
            out[i]["start"] = out[i - 1]["start"]
        if out[i]["end"] < out[i]["start"]:
            out[i]["end"] = out[i]["start"] + 0.05
    print(f"   forced-aligned {len(timings)}/{len(words)} words (onnx)")
    return out


if __name__ == "__main__":
    import json, sys
    d = json.load(open(sys.argv[2]))
    words = [{"text": s["text"].strip(), "start": s["offsets"]["from"] / 1000,
              "end": s["offsets"]["to"] / 1000} for s in d["transcription"] if s["text"].strip()]
    for w in align_words(sys.argv[1], words)[:10]:
        print(f'  {w["start"]:.2f}-{w["end"]:.2f}  {w["text"]!r}')
