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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reframe_track
import highlights as hl
import align as align_mod
import edit as edit_mod

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
MARGIN_H = 140              # side margins so text never overflows
MARGIN_V = 430               # captions in lower third (chest), clear of face & bottom UI
MAX_WORDS_PER_LINE = 3       # short phrases, like Submagic/Opus
GAP_SPLIT_MS = 700           # start a new phrase after a pause this long

# ---- framing: FIXED blurred background + dynamic foreground video on top ----
# The bg is a static cover-crop of the scene, blurred, NEVER panned/zoomed (fixed).
# ALL motion (tracking pan, zoom punches) lives in the foreground layer only, so the
# frame/margins stay rock-steady and professional.
FG_SCALE = 0.84              # foreground video fills this fraction; rest = fixed blurred margin
BG_BLUR = 42                 # background blur strength
BG_DARKEN = -0.08            # slightly darken bg so fg pops
ZOOM_IN = 0.10               # emphasis punch-in on the FG content (display size stays fixed)
ZOOM_HOLD = 1.10             # punch-in hold (s)


def run(cmd, **kw):
    print(f"$ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=True, **kw)


def extract_audio(video, wav):
    run([FFMPEG, "-y", "-i", str(video), "-ar", "16000", "-ac", "1",
         "-c:a", "pcm_s16le", str(wav)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def transcribe(wav, out_prefix, glossary=""):
    """whisper.cpp with word-level timestamps -> JSON.

    `glossary` is a custom-vocabulary initial prompt (brand/proper-noun names)
    biasing the model — a real product feature. Carried through the whole audio.
    Fixed the visible 'Keruvin'->'Kerobin' error in testing.
    """
    # --dtw: token-level timestamps via cross-attention alignment (DTW).
    # Much tighter caption sync than the default heuristic timing.
    cmd = [str(WCLI), "-m", str(MODEL), "-f", str(wav),
           "-l", "es", "-ml", "1", "-sow", "--dtw", "large.v3.turbo",
           "-oj", "-of", str(out_prefix)]
    if glossary:
        cmd += ["--prompt", glossary, "--carry-initial-prompt"]
    run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    """Word-by-word highlight captions, ROCK-STABLE position.

    Bug fixed: highlighting the active word by SCALING it (\\fscx) changed the text
    width every word, so a centered line re-centered/re-wrapped -> the caption
    appeared to jump around mid-phrase. Now the highlight is COLOR-ONLY (glyph
    metrics never change) so the phrase stays pinned; only the color moves.
    Also: events are globally de-overlapped + min-duration'd + capped on long pauses,
    so captions never duplicate or vanish.
    """
    MIN_DUR = 0.06
    MAX_HOLD = 1.2   # after this much silence the caption clears (natural)
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
    events = []   # [start, end, text]
    for ph in phrases:
        for i, active in enumerate(ph):
            parts = []
            for w in ph:
                token = w["text"].replace("{", "(").replace("}", ")")
                if w is active:
                    parts.append(f"{{\\c{ACTIVE_COLOR}}}{token}{{\\r}}")  # COLOR ONLY
                else:
                    parts.append(token)
            start = active["start"]
            end = ph[i + 1]["start"] if i + 1 < len(ph) else active["end"]
            events.append([start, end, " ".join(parts)])

    events.sort(key=lambda e: e[0])
    lines = []
    for j, ev in enumerate(events):
        s, _, txt = ev
        nxt = events[j + 1][0] if j + 1 < len(events) else None
        end = ev[1]
        if nxt is not None:
            end = min(end, nxt)                      # never overlap next -> no duplicates
            if nxt - s > MAX_HOLD:                   # long pause -> let it clear
                end = min(end, s + MAX_HOLD)
        if end - s < MIN_DUR:                        # superseded by a ~simultaneous word: drop
            continue                                #   (avoids overlap AND flicker/dup)
        lines.append(f"Dialogue: 0,{ass_time(s)},{ass_time(end)},Base,,0,0,0,,{txt}")
    Path(ass_path).write_text(header + "\n".join(lines) + "\n")


def is_hdr(video):
    """Detect HLG/PQ HDR by color_transfer so we can tonemap to SDR."""
    r = subprocess.run(
        ["/opt/homebrew/bin/ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=color_transfer", "-of", "default=nk=1:nw=1", str(video)],
        capture_output=True, text=True)
    return r.stdout.strip() in ("arib-std-b67", "smpte2084")


def probe_dims(video):
    r = subprocess.run(
        ["/opt/homebrew/bin/ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video)],
        capture_output=True, text=True)
    nums = [int(n) for n in r.stdout.strip().split("x") if n.strip()]
    return nums[0], nums[1]


def _tonemap(video):
    if is_hdr(video):
        return ["zscale=transfer=linear:npl=100", "tonemap=tonemap=hable:desat=0",
                "zscale=transfer=bt709:matrix=bt709:primaries=bt709:range=tv"]
    return []


def reframe_and_burn(video, ass_path, out, fps=30, track=True, cmds_path=None, beats=None):
    """Composite a FIXED blurred background + a dynamic foreground video, burn captions.

    Layers (Kevin's design — the frame/margins never move; only the video moves):
      - BG: static cover-crop of the scene, blurred + darkened. No pan, no zoom -> fixed.
      - FG: the subject, YuNet-tracked pan + optional emphasis punch-in zoom (zoom is on
        the CONTENT; the fg display rect stays fixed, so margins are rock-steady).
      - Captions on top, fixed position.
    Apple Silicon fast path: VideoToolbox decode/encode; tonemap at low res.
    """
    src_w, src_h = probe_dims(video)
    tm = _tonemap(video)
    if tm:
        print("   HDR -> tonemap HLG/PQ to SDR (both layers)")
    fg_w = (round(W * FG_SCALE) // 2) * 2
    fg_h = (round(H * FG_SCALE) // 2) * 2

    # --- foreground: tracked (or static) 9:16 crop scaled to the inset size ---
    cmds = None
    if track:
        cw, ch, cmds = reframe_track.build(video, src_w, src_h, cmds_path)
    if cmds:
        fg = [f"sendcmd=f={cmds}", f"crop@fgc={cw}:{ch}:x=0:y=(ih-{ch})/2", f"scale={fg_w}:{fg_h}"]
    else:
        fg = [f"scale={fg_w}:{fg_h}:force_original_aspect_ratio=increase", f"crop={fg_w}:{fg_h}"]
    fg = fg + [f"fps={fps}"] + tm   # sendcmd MUST precede fps or its crop-x cmds don't land
    if beats:   # hard-cut punch-in on the fg CONTENT (display size fixed)
        win = "+".join(f"between(t,{b:.3f},{b + ZOOM_HOLD:.3f})" for b in beats)
        z = f"(1+{ZOOM_IN}*({win}))"
        fg += [f"scale=w='ceil({fg_w}*{z}/2)*2':h='ceil({fg_h}*{z}/2)*2':eval=frame",
               f"crop={fg_w}:{fg_h}"]
    fg += ["format=yuv420p"]

    # --- background: static blurred cover-fill of the scene (fixed framing) ---
    bg = ([f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}", f"fps={fps}"]
          + tm + [f"gblur=sigma={BG_BLUR}", f"eq=brightness={BG_DARKEN}:saturation=0.9",
                  "format=yuv420p"])

    vf = (f"split=2[bgsrc][fgsrc];"
          f"[bgsrc]{','.join(bg)}[bg];"
          f"[fgsrc]{','.join(fg)}[fg];"
          f"[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2[c];"
          f"[c]subtitles={ass_path}")
    run([FFMPEG, "-y", "-hwaccel", "videotoolbox", "-i", str(video), "-vf", vf,
         "-c:v", "h264_videotoolbox", "-b:v", "12M",
         "-c:a", "aac", "-b:a", "128k", str(out)])


def cut_clip(full_out, start, end, clip_out):
    """Cut a highlight [start,end] from the fully-rendered vertical (captions and
    tracking already baked, so timing stays correct). Re-encode for frame accuracy."""
    run([FFMPEG, "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(full_out),
         "-c:v", "h264_videotoolbox", "-b:v", "12M", "-c:a", "aac", "-b:a", "128k",
         str(clip_out)])


def make_highlights(json_path, full_out, n, out_dir, stem, dynamic=False):
    """Select n highlights via local LLM and cut each into its own short.
    If dynamic, also trim silences + add a breathing punch-in zoom (Phase 2b)."""
    sents = hl.build_sentences(hl.load_words(json_path))
    print(f"   {len(sents)} sentences -> asking local LLM ({hl.MODEL}) for {n} highlight(s)...")
    clips = hl.select_highlights(sents, n=n)
    results = []
    for k, c in enumerate(clips, 1):
        clip_out = out_dir / f"{stem}_short{k}.mp4"
        print(f"   short {k}: {c['start']:.1f}-{c['end']:.1f}s ({c['end']-c['start']:.0f}s) "
              f"\"{c['title']}\" — {c['reason']}")
        cut_clip(full_out, c["start"], c["end"], clip_out)
        if dynamic:   # zoom punches are baked in the full render; here just trim silences
            tmp = out_dir / f"{stem}_short{k}_dyn.mp4"
            kept, removed = edit_mod.tighten(clip_out, tmp)
            tmp.replace(clip_out)
            print(f"      dynamic: -{removed:.1f}s silencios (zoom ya horneado)")
        results.append((clip_out, c))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="input video path")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("-g", "--glossary", default="",
                    help="custom vocabulary / proper nouns to bias transcription "
                         "(e.g. 'Keruvin Store, Al Haramain, Oud, Lattafa')")
    ap.add_argument("--no-track", action="store_true",
                    help="disable subject tracking (use fixed center-crop)")
    ap.add_argument("--highlights", type=int, default=0, metavar="N",
                    help="also select N best highlight clips via local LLM and cut shorts")
    ap.add_argument("--no-align", action="store_true",
                    help="skip wav2vec2 forced alignment (use whisper's own timings)")
    ap.add_argument("--dynamic", action="store_true",
                    help="add dynamism to shorts: trim silences + breathing punch-in zoom")
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
    cmds = work / f"{stem}.crop.txt"

    print("== [1/4] extract audio =="); extract_audio(video, wav)
    glossary = (f"Nombres propios y términos: {args.glossary}."
                if args.glossary else "")
    print("== [2/5] transcribe (whisper.cpp, es, word-level) =="); jp = transcribe(wav, prefix, glossary)
    words = load_words(jp)
    if not args.no_align:
        print("== [3/5] forced alignment (wav2vec2, tight caption sync) ==")
        try:
            words = align_mod.align_words(wav, words)
        except Exception as e:
            print(f"   alignment failed ({e}); falling back to whisper timings")
    phrases = group_phrases(words)
    print(f"   {len(words)} words -> {len(phrases)} caption phrases")
    print("== [4/5] build word-highlight ASS =="); build_ass(phrases, ass)
    beats = None
    if args.dynamic:
        print("   emphasis beats (audio energy) for punch-in zoom...")
        beats, _ = edit_mod.emphasis_beats(wav)
    print("== [5/5] fixed blurred bg + tracked/zoomed fg + burn captions ==")
    reframe_and_burn(video, ass, out, track=not args.no_track, cmds_path=str(cmds), beats=beats)
    print(f"\n✅ full: {out}")

    if args.highlights:
        print(f"== [5] highlights (local LLM) ==")
        for clip_out, _ in make_highlights(jp, out, args.highlights, out.parent, stem,
                                           dynamic=args.dynamic):
            print(f"✅ short: {clip_out}")


if __name__ == "__main__":
    main()
