#!/usr/bin/env python3
"""
Forced alignment (the caption-sync wedge).

whisper.cpp gives accurate TEXT but its word timestamps drift +/- vs the audio.
Here we re-time those words with a wav2vec2 CTC forced aligner (MMS_FA, multilingual
incl. Spanish), which aligns the whole transcript to the audio globally -> tight,
non-drifting word timing. We keep whisper's original text (accents/punctuation) and
only replace start/end, matching aligned tokens back by normalized text sequence.

Local, on-device (torch CPU), no cloud.
"""
import re
import tempfile
import unicodedata
from pathlib import Path

_model = None  # cache across calls in one process


def _norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def align_words(wav_path, words):
    """Return `words` with start/end replaced by forced-alignment timings.
    Falls back to the original timing for any word that can't be matched."""
    global _model
    import ctc_forced_aligner as cfa

    txt = " ".join(w["text"] for w in words).strip()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(txt); tpath = f.name
    try:
        aligned, _model, _ = cfa.get_word_stamps(str(wav_path), tpath, model=_model)
    finally:
        Path(tpath).unlink(missing_ok=True)

    al = [(_norm(a["text"]), a["start"], a["end"]) for a in aligned if _norm(a["text"])]
    out, j, fixed = [], 0, 0
    for w in words:
        nw = _norm(w["text"])
        if not nw:                       # punctuation-only token: keep timing
            out.append(dict(w)); continue
        m = None
        for k in range(j, min(j + 3, len(al))):   # small look-ahead tolerance
            a = al[k][0]
            if a == nw or a.startswith(nw) or nw.startswith(a):
                m = k; break
        if m is not None:
            out.append({**w, "start": al[m][1], "end": al[m][2]})
            j = m + 1; fixed += 1
        else:
            out.append(dict(w))
    # enforce monotonic non-overlapping starts (safety)
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["start"]:
            out[i]["start"] = out[i - 1]["start"]
        if out[i]["end"] < out[i]["start"]:
            out[i]["end"] = out[i]["start"] + 0.05
    print(f"   forced-aligned {fixed}/{len(words)} words")
    return out


if __name__ == "__main__":
    import json, sys
    d = json.load(open(sys.argv[2]))
    words = [{"text": s["text"].strip(), "start": s["offsets"]["from"] / 1000,
              "end": s["offsets"]["to"] / 1000} for s in d["transcription"] if s["text"].strip()]
    for w in align_words(sys.argv[1], words)[:10]:
        print(f'  {w["start"]:.2f}-{w["end"]:.2f}  {w["text"]!r}')
