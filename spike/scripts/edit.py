#!/usr/bin/env python3
"""
Phase 2b — dynamism: silence trimming + subtle punch-in zoom.

Operates on the ALREADY-RENDERED vertical (captions/tracking/audio baked in), so
cutting silent gaps keeps everything in sync automatically and re-encodes 1080p
fast (VideoToolbox). Jump-cuts on silence = the "edited/snappy" feel (Opus/Submagic).
A gentle breathing zoom adds life on top.
"""
import re
import subprocess
from pathlib import Path

FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"

NOISE_DB = -32        # below this = silence
MIN_SIL = 0.45        # only cut silences longer than this (s)
PAD = 0.06            # keep this much around speech so words aren't clipped (s)
ZOOM_AMPL = 0.030     # breathing punch amplitude (fraction)
ZOOM_PERIOD = 5.0     # breathing period (s)


def _silences(video):
    r = subprocess.run(
        [FFMPEG, "-i", str(video), "-af", f"silencedetect=noise={NOISE_DB}dB:d={MIN_SIL}",
         "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends))


def _duration(video):
    r = subprocess.run(["/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=nk=1:nw=1", str(video)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def keep_segments(video):
    """Complement of the silences (padded) -> speech segments to keep."""
    dur = _duration(video)
    sil = _silences(video)
    keeps, t = [], 0.0
    for s, e in sil:
        a, b = min(s + PAD, dur), max(e - PAD, 0)
        if a - t > 0.15:
            keeps.append((t, a))
        t = b
    if dur - t > 0.15:
        keeps.append((t, dur))
    return keeps, dur


def tighten(video_in, video_out, zoom=True):
    """Concatenate the speech segments (drop silences) in one re-encode pass,
    with an optional gentle breathing punch-in zoom. Returns (new_dur, removed_s)."""
    keeps, dur = keep_segments(video_in)
    if not keeps:
        return dur, 0.0
    vsel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keeps)
    asel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keeps)
    vf = [f"select='{vsel}'", "setpts=N/FRAME_RATE/TB"]
    if zoom:
        # breathing punch-in: scale up by an oscillating factor (>=1), center-crop back.
        z = f"(1+{ZOOM_AMPL}+{ZOOM_AMPL}*sin(2*PI*t/{ZOOM_PERIOD}))"
        vf += [f"scale=w='ceil(1080*{z}/2)*2':h='ceil(1920*{z}/2)*2':eval=frame",
               "crop=1080:1920"]
    af = f"aselect='{asel}',asetpts=N/SR/TB"
    kept = sum(b - a for a, b in keeps)
    subprocess.run([FFMPEG, "-y", "-i", str(video_in),
                    "-vf", ",".join(vf), "-af", af,
                    "-c:v", "h264_videotoolbox", "-b:v", "12M",
                    "-c:a", "aac", "-b:a", "128k", str(video_out)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return kept, dur - kept


if __name__ == "__main__":
    import sys
    kept, removed = tighten(sys.argv[1], sys.argv[2], zoom="--no-zoom" not in sys.argv)
    print(f"tightened -> {kept:.1f}s kept, {removed:.1f}s of silence removed")
