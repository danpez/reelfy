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

FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
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
    r = subprocess.run(["/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries",
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


def tighten(video_in, video_out):
    """Concatenate speech segments (drop silences) in one re-encode pass. Jump-cuts."""
    dur = _duration(video_in)
    sil = _silences(video_in)
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
    sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keeps)
    kept = sum(b - a for a, b in keeps)
    subprocess.run([FFMPEG, "-y", "-i", str(video_in),
                    "-vf", f"select='{sel}',setpts=N/FRAME_RATE/TB",
                    "-af", f"aselect='{sel}',asetpts=N/SR/TB",
                    "-c:v", "h264_videotoolbox", "-b:v", "12M",
                    "-c:a", "aac", "-b:a", "128k", str(video_out)],
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
    # zoom(t) = 1 + ZOOM_IN while t in any [b, b+HOLD], else 1  (step function -> hard cuts)
    windows = "+".join(f"between(t,{b:.3f},{b + HOLD:.3f})" for b in beats)
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
