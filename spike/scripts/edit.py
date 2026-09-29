#!/usr/bin/env python3
"""
Phase 2b — dynamism: silence trimming + HARD-CUT punch-in zoom on emphasis.

Two passes on the already-rendered vertical (captions/tracking/audio baked in, so
everything stays in sync and 1080p re-encodes fast on VideoToolbox):
  1) tighten(): drop silences -> jump-cuts (snappy pacing).
  2) punch_zoom(): detect audio-energy emphasis beats, hard-cut zoom IN on each
     (no transition), hold, hard-cut back OUT -> manual-edit dynamism.
Base stays at full frame (widest = most air); punches only zoom in, capped subtle.
"""
import re
import subprocess
from pathlib import Path

from paths import FFMPEG, FFPROBE  # noqa: E402,F401
W, H = 1080, 1920

# silence trim
NOISE_DB = -32
MIN_SIL = 0.45
PAD = 0.06
# punch-in zoom
ZOOM_IN = 0.10       # zoom factor on emphasis (1.10x); subtle to keep air
HOLD = 1.10          # how long a punch-in holds (s)
BEAT_SPACING = 7.0   # ~one emphasis punch per this many seconds (calmer, less frequent)


def _duration(video):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=nk=1:nw=1", str(video)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def _silences(video):
    r = subprocess.run(
        [FFMPEG, "-i", str(video), "-af", f"silencedetect=noise={NOISE_DB}dB:d={MIN_SIL}",
         "-f", "null", "-"], capture_output=True, text=True)
    s = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    e = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(s, e))


# Pausas ENTRE PALABRAS. silencedetect usa un umbral absoluto por muestra (-32 dB): una pausa con
# ruido de fondo o chasquidos (p. ej. el arranque de un clip de iPhone, ~-38 dB con picos) no
# cuenta como silencio y quedaba ~1 s muerto. Con las marcas de las palabras la pausa se reconoce
# por energia RELATIVA a la voz de esa persona: voz no transcrita suena fuerte y nunca se corta.
GAP_MIN = 0.40        # pausa entre palabras que se recorta (s)
GAP_PAD_OUT = 0.10    # aire que queda tras la palabra anterior
GAP_PAD_IN = 0.08     # aire que queda antes de la siguiente
GAP_REL_DB = -10.0    # p90 de la pausa (ventanas de 50 ms) al menos 10 dB bajo la mediana de la voz
_WIN = 0.05


def _mono16k(path):
    """Audio mono 16 kHz float32 (wav directo con soundfile; video via ffmpeg)."""
    import numpy as np
    p = str(path)
    if p.lower().endswith(".wav"):
        import soundfile as sf
        y, sr = sf.read(p, dtype="float32", always_2d=True)
        y = y.mean(axis=1)
        if sr != 16000:
            idx = (np.arange(int(len(y) * 16000 / sr)) * sr / 16000).astype(int)
            y = y[np.clip(idx, 0, len(y) - 1)]
        return y
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", p, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def word_gap_silences(audio, words, dur):
    """Pausas [(a, b)] entre palabras (y antes de la primera / tras la ultima) de al menos GAP_MIN
    cuya energia queda GAP_REL_DB bajo la voz. Devueltas en el formato de _silences (keep_segments
    les aplica PAD), de modo que quedan GAP_PAD_OUT / GAP_PAD_IN de aire junto a las palabras."""
    import numpy as np
    ws = sorted((w for w in (words or []) if w.get("end", 0) > w.get("start", 0)), key=lambda w: w["start"])
    if len(ws) < 2:
        return []
    y = audio if not isinstance(audio, (str, Path)) else _mono16k(audio)
    sr, hop = 16000, int(16000 * _WIN)
    n = len(y) // hop
    if n < 4:
        return []
    rms = np.sqrt(np.mean(y[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    speech = np.zeros(n, bool)
    for w in ws:
        speech[int(w["start"] / _WIN):int(np.ceil(w["end"] / _WIN))] = True
    if not speech.any():
        return []
    ref = float(np.median(db[speech]))
    out = []
    bounds = [(0.0, ws[0]["start"])] + [(a["end"], b["start"]) for a, b in zip(ws, ws[1:])] + \
        [(ws[-1]["end"], dur)]
    for a, b in bounds:
        if b - a < GAP_MIN:
            continue
        i0, i1 = int(np.ceil((a + 0.02) / _WIN)), int((b - 0.02) / _WIN)
        if i1 - i0 < 2:
            continue
        if float(np.percentile(db[i0:i1], 90)) > ref + GAP_REL_DB:
            continue                                   # suena (musica, risa, voz no transcrita)
        s = max(0.0, a + GAP_PAD_OUT - PAD) if a > 0 else 0.0
        e = min(dur, b - GAP_PAD_IN + PAD) if b < dur else dur
        if e - s > 0.2:
            out.append((s, e))
    return out


# Palabras dichas bajito. silencedetect (-32 dB por muestra) y la regla de pausas cuentan como
# silencio el final de frase que alguien dice en voz baja ("...a mi punto de vista." a -52 dB
# con la voz a -29 dB) y el corte se lo comia. Una palabra transcrita CON VOZ protege su nucleo
# (su tiempo, topado a lo que tarda en decirse: una palabra estirada sobre una pausa no la
# protege entera); las respiraciones y pausas sin palabra se siguen cortando.
PROTECT_BEFORE = 0.02   # s de aire antes de la palabra protegida
PROTECT_AFTER = 0.08    # s de aire despues (cola de la consonante final)
PROTECT_STRETCH = 1.5   # nucleo = hasta 1.5x la duracion tipica de la palabra (+0.1 s)
PROTECT_ABOVE_FLOOR = 10.0  # dB: la palabra tiene voz si su p90 queda 10 dB sobre el piso de ruido


def _protect_words(sil, words, audio, dur):
    """Silencios [(a, b)] (formato de _silences: el corte real es [a+PAD, b-PAD]) sin el
    nucleo de ninguna palabra con voz. Las palabras sobre el piso de ruido (whisper que alucina
    en el silencio) no protegen nada."""
    import numpy as np
    from motion.timeline import est_word_dur
    ws = [w for w in (words or []) if w.get("end", 0) >= w.get("start", 0)]
    if not sil or not ws:
        return sil
    y = audio if not isinstance(audio, (str, Path)) else _mono16k(audio)
    hop = int(16000 * 0.02)
    n = len(y) // hop
    if n < 10:
        return sil
    db = 20 * np.log10(np.sqrt(np.mean(y[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12) + 1e-9)
    # piso de ruido: el 10% mas bajo (con silencio digital queda en ~-90), y nunca a menos de
    # 15 dB de la mediana (grabacion casi sin pausas: el p10 seria voz bajita)
    floor = min(float(np.percentile(db, 10)), float(np.percentile(db, 50)) - 15.0)
    prot = []
    for w in ws:
        s = float(w["start"])
        e = min(float(w["end"]), s + PROTECT_STRETCH * est_word_dur(w.get("text")) + 0.1)
        e = max(e, s + 0.05)
        i0, i1 = int(s / 0.02), max(int(s / 0.02) + 1, int(np.ceil(e / 0.02)))
        seg = db[max(0, i0):min(n, i1)]
        if seg.size and float(np.percentile(seg, 90)) > floor + PROTECT_ABOVE_FLOOR:
            prot.append((s - PROTECT_BEFORE, e + PROTECT_AFTER))
    if not prot:
        return sil
    prot = _merge(prot)
    out = []
    for a, b in sil:
        ca, cb = a + PAD, b - PAD                      # el corte real
        pieces = [(ca, cb)]
        for pa, pb in prot:
            if pb <= ca or pa >= cb:
                continue
            nxt = []
            for x, z in pieces:
                if pb <= x or pa >= z:
                    nxt.append((x, z))
                    continue
                if pa > x:
                    nxt.append((x, pa))
                if pb < z:
                    nxt.append((pb, z))
            pieces = nxt
        for x, z in pieces:
            if z - x > 0.15:                            # un corte de < 0.15 s ya no vale la pena
                out.append((max(0.0, x - PAD) if x > 0 else 0.0, min(dur, z + PAD) if z < dur else dur))
    return out


def _merge(iv):
    out = []
    for s, e in sorted(iv):
        if out and s <= out[-1][1] + 0.02:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def keep_segments(video, words=None):
    """Speech segments to keep (complement of padded silences). Returns (keeps, dur).
    Con `words` (marcas de la transcripcion) tambien se recortan las pausas entre palabras que
    silencedetect no ve por ruido de fondo (word_gap_silences)."""
    dur = _duration(video)
    sil = _silences(video)
    if words:
        try:
            audio = _mono16k(video)
            sil = _merge(sil + word_gap_silences(audio, words, dur))
            sil = _protect_words(sil, words, audio, dur)
        except Exception as e:  # noqa: sin audio legible -> solo silencedetect
            print(f"   pausas entre palabras no disponibles ({e})")
    keeps, t = [], 0.0
    for s, e in sil:
        a, b = min(s + PAD, dur), max(e - PAD, 0)
        if a - t > 0.15:
            keeps.append((t, a))
        t = b
    if dur - t > 0.15:
        keeps.append((t, dur))
    if not keeps:
        keeps = [(0.0, dur)]
    return keeps, dur


def tighten(video_in, video_out):
    """Concatenate speech segments (drop silences) in one re-encode pass. Jump-cuts.
    Audio at 192k to limit cascaded-AAC generation loss."""
    from motion.video_io import select_chain
    keeps, dur = keep_segments(video_in)
    kept = sum(b - a for a, b in keeps)
    # un select/aselect si son pocos keeps; con muchos (video largo), varios encadenados por
    # tramos: av_expr no acepta una suma de mas de ~100 terminos
    vsel = ",".join(select_chain(keeps, "select", digits=3))
    asel = ",".join(select_chain(keeps, "aselect", digits=3))
    subprocess.run([FFMPEG, "-y", "-i", str(video_in),
                    "-vf", f"{vsel},setpts=N/FRAME_RATE/TB",
                    "-af", f"{asel},asetpts=N/SR/TB",
                    "-c:v", "h264_videotoolbox", "-b:v", "12M",
                    "-c:a", "aac", "-b:a", "192k", str(video_out)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return kept, dur - kept


def emphasis_beats(video):
    """One emphasis beat per ~BEAT_SPACING window, at that window's loudest moment."""
    import librosa, numpy as np
    y, sr = librosa.load(str(video), sr=16000, mono=True)
    dur = len(y) / sr
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    times = librosa.frames_to_time(range(len(rms)), sr=sr, hop_length=512)
    beats, t = [], 0.6
    while t < dur - HOLD:
        m = (times >= t) & (times < t + BEAT_SPACING)
        if m.any():
            beats.append(float(times[m][int(np.argmax(rms[m]))]))
        t += BEAT_SPACING
    return beats, dur


def punch_zoom(video_in, video_out):
    """Hard-cut zoom-in punches on emphasis beats (instant in/out, no transition)."""
    beats, dur = emphasis_beats(video_in)
    if not beats:
        Path(video_in).replace(video_out); return 0
    from motion.video_io import sum_expr
    # zoom(t) = 1 + ZOOM_IN while t in any [b, b+HOLD], else 1  (step function -> hard cuts)
    # sum_expr: un video largo da >100 beats y la suma plana ya no parsea en av_expr
    windows = sum_expr(f"between(t,{b:.3f},{b + HOLD:.3f})" for b in beats)
    z = f"(1+{ZOOM_IN}*({windows}))"
    vf = (f"scale=w='ceil({W}*{z}/2)*2':h='ceil({H}*{z}/2)*2':eval=frame,"
          f"crop={W}:{H}")
    subprocess.run([FFMPEG, "-y", "-i", str(video_in), "-vf", vf,
                    "-c:v", "h264_videotoolbox", "-b:v", "12M",
                    "-c:a", "aac", "-b:a", "128k", str(video_out)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return len(beats)


def dynamic(video_in, video_out, workdir):
    """Full dynamism: silence trim -> emphasis punch-in zoom."""
    tmp = Path(workdir) / (Path(video_out).stem + "_tight.mp4")
    kept, removed = tighten(video_in, tmp)
    n = punch_zoom(tmp, video_out)
    tmp.unlink(missing_ok=True)
    return removed, n


if __name__ == "__main__":
    import sys
    removed, n = dynamic(sys.argv[1], sys.argv[2], Path(sys.argv[2]).parent)
    print(f"dynamic -> -{removed:.1f}s silencios, {n} punch-in zooms")
