#!/usr/bin/env python3
"""
Reelfy spike pipeline — single-pass, local, Apple Silicon.

video_in -> [ffmpeg audio] -> [whisper.cpp word-timestamps] -> [ASS word-highlight captions]
         -> [ffmpeg 9:16 reframe + burn captions] -> video_out

This is a validation spike, NOT the product. Goal: prove we can render a 9:16 short
with Spanish captions whose word-level timing/sync is tight and "listo para publicar".

Reframe here is a simple center-crop cover (no subject tracking yet). Real subject
tracking must use a PERMISSIVE-licensed detector, NOT YOLOv11 (AGPL-3.0). See docs.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent.parent
WCLI = SPIKE / "whisper.cpp/build/bin/whisper-cli"
MODEL = SPIKE / "whisper.cpp/models/ggml-large-v3-turbo.bin"
# ffmpeg-full (keg-only) — needed for the libass `subtitles` filter to burn captions
FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"

# ---- caption styling (the wedge: word-by-word "viral" look) ----
W, H = 1080, 1920            # 9:16
FONT = "Arial"
FONT_SIZE = 78
BASE_COLOR = "&H00FFFFFF"    # white (ASS is &HAABBGGRR)
ACTIVE_COLOR = "&H0000E5FF"  # amber highlight for the spoken word
OUTLINE = 5
MARGIN_H = 120              # side margins so text never overflows
MARGIN_V = 540               # captions in lower third
MAX_WORDS_PER_LINE = 3       # short phrases, like Submagic/Opus
GAP_SPLIT_MS = 700           # start a new phrase after a pause this long


def run(cmd, **kw):
    print(f"$ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=True, **kw)


def extract_audio(video, wav):
    run([FFMPEG, "-y", "-i", str(video), "-ar", "16000", "-ac", "1",
         "-c:a", "pcm_s16le", str(wav)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def transcribe(wav, out_prefix):
    """whisper.cpp with word-level timestamps -> JSON."""
    run([str(WCLI), "-m", str(MODEL), "-f", str(wav),
         "-l", "es", "-ml", "1", "-sow", "-oj", "-of", str(out_prefix)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return Path(f"{out_prefix}.json")


def load_words(json_path):
    """Return [{'text','start','end'}] in seconds, cleaned."""
    data = json.load(open(json_path))
    words = []
    for seg in data["transcription"]:
        txt = seg["text"].strip()
        if not txt:
            continue
        words.append({
            "text": txt,
            "start": seg["offsets"]["from"] / 1000.0,
            "end": seg["offsets"]["to"] / 1000.0,
        })
    return words


def group_phrases(words):
    """Chunk words into short caption lines by count and pauses."""
    phrases, cur = [], []
    for i, w in enumerate(words):
        if cur:
            gap = w["start"] - cur[-1]["end"]
            ends_sentence = cur[-1]["text"][-1:] in ".!?"
            if len(cur) >= MAX_WORDS_PER_LINE or gap * 1000 > GAP_SPLIT_MS or ends_sentence:
                phrases.append(cur)
                cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    return phrases


def ass_time(t):
    h = int(t // 3600); m = int((t % 3600) // 60)
    s = int(t % 60); cs = int((t - int(t)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(phrases, ass_path):
    """One Dialogue event per (phrase, active-word) so the spoken word highlights
    in sync — the timing-quality demo."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Base,{FONT},{FONT_SIZE},{BASE_COLOR},&H00000000,&H80000000,1,{OUTLINE},2,2,{MARGIN_H},{MARGIN_H},{MARGIN_V}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for ph in phrases:
        for active in ph:
            parts = []
            for w in ph:
                token = w["text"].replace("{", "(").replace("}", ")")
                if w is active:
                    parts.append(f"{{\\c{ACTIVE_COLOR}\\fscx112\\fscy112}}{token}{{\\r}}")
                else:
                    parts.append(token)
            text = " ".join(parts)
            lines.append(
                f"Dialogue: 0,{ass_time(active['start'])},{ass_time(active['end'])},Base,,0,0,0,,{text}"
            )
    Path(ass_path).write_text(header + "\n".join(lines) + "\n")


def is_hdr(video):
    """Detect HLG/PQ HDR by color_transfer so we can tonemap to SDR."""
    r = subprocess.run(
        ["/opt/homebrew/bin/ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=color_transfer", "-of", "default=nk=1:nw=1", str(video)],
        capture_output=True, text=True)
    return r.stdout.strip() in ("arib-std-b67", "smpte2084")


def reframe_and_burn(video, ass_path, out, fps=30):
    """Cover-crop to 9:16 and burn the ASS captions. Center-crop only (spike).

    Apple Silicon fast path (~6x vs CPU/libx264, measured 3.3x realtime on 4K HDR):
      - VideoToolbox hardware DECODE (`-hwaccel videotoolbox`)
      - scale+crop to 1080x1920 BEFORE tonemap -> tonemap runs on ~4x fewer pixels
      - VideoToolbox hardware ENCODE (`h264_videotoolbox`)
    Vulkan/libplacebo GPU tonemap is NOT usable here (no loadable Vulkan runtime).
    """
    # Reduce to final frame size first so the CPU tonemap is cheap.
    chain = [
        f"scale={W}:{H}:force_original_aspect_ratio=increase",
        f"crop={W}:{H}",
        f"fps={fps}",
    ]
    if is_hdr(video):
        print("   HDR detected -> tonemapping HLG/PQ to SDR bt709 (post-downscale)")
        chain += [
            "zscale=transfer=linear:npl=100",
            "tonemap=tonemap=hable:desat=0",
            "zscale=transfer=bt709:matrix=bt709:primaries=bt709:range=tv",
        ]
    chain += ["format=yuv420p", f"subtitles={ass_path}"]
    vf = ",".join(chain)
    run([FFMPEG, "-y", "-hwaccel", "videotoolbox", "-i", str(video), "-vf", vf,
         "-c:v", "h264_videotoolbox", "-b:v", "12M",
         "-c:a", "aac", "-b:a", "128k", str(out)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="input video path")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    video = Path(args.video).resolve()
    if not video.exists():
        sys.exit(f"input not found: {video}")
    work = SPIKE / "work"; work.mkdir(exist_ok=True)
    out = Path(args.out).resolve() if args.out else SPIKE / "output" / f"{video.stem}_reelfy.mp4"
    out.parent.mkdir(exist_ok=True)

    stem = video.stem
    wav = work / f"{stem}.wav"
    prefix = work / stem
    ass = work / f"{stem}.ass"

    print("== [1/4] extract audio =="); extract_audio(video, wav)
    print("== [2/4] transcribe (whisper.cpp, es, word-level) =="); jp = transcribe(wav, prefix)
    words = load_words(jp)
    phrases = group_phrases(words)
    print(f"   {len(words)} words -> {len(phrases)} caption phrases")
    print("== [3/4] build word-highlight ASS =="); build_ass(phrases, ass)
    print("== [4/4] reframe 9:16 + burn captions =="); reframe_and_burn(video, ass, out)
    print(f"\n✅ output: {out}")


if __name__ == "__main__":
    main()
