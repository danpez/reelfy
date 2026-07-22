#!/usr/bin/env python3
"""Compara el alineador VIEJO (torch + ctc_forced_aligner) contra el NUEVO (ONNX)
sobre el mismo audio/transcripción, para verificar que la calidad se conserva.

Uso: validate_aligner.py <wav> <whisper.json>
"""
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def load_words(json_path):
    d = json.load(open(json_path))
    return [{"text": s["text"].strip(),
             "start": s["offsets"]["from"] / 1000,
             "end": s["offsets"]["to"] / 1000}
            for s in d["transcription"] if s["text"].strip()]


def run_old(wav, words):
    """Alineador viejo: torch + ctc_forced_aligner (código previo a la migración)."""
    import re, tempfile, unicodedata
    import ctc_forced_aligner as cfa
    import torchaudio

    def _norm(s):
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]", "", s.lower())

    model = torchaudio.pipelines.MMS_FA.get_model(with_star=False)
    txt = " ".join(w["text"] for w in words).strip()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(txt); tpath = f.name
    try:
        aligned, _, _ = cfa.get_word_stamps(str(wav), tpath, model=model)
    finally:
        Path(tpath).unlink(missing_ok=True)

    al = [(_norm(a["text"]), a["start"], a["end"]) for a in aligned if _norm(a["text"])]
    out, j = [], 0
    for w in words:
        nw = _norm(w["text"])
        if not nw:
            out.append(dict(w)); continue
        m = None
        for k in range(j, min(j + 3, len(al))):
            a = al[k][0]
            if a == nw or a.startswith(nw) or nw.startswith(a):
                m = k; break
        if m is not None:
            out.append({**w, "start": al[m][1], "end": al[m][2]}); j = m + 1
        else:
            out.append(dict(w))
    return out


def main():
    wav, jsn = sys.argv[1], sys.argv[2]
    words = load_words(jsn)
    print(f"palabras: {len(words)}")

    t0 = time.time(); old = run_old(wav, words); t_old = time.time() - t0
    print(f"viejo (torch): {t_old:.1f}s")

    import align
    t0 = time.time(); new = align.align_words(wav, words); t_new = time.time() - t0
    print(f"nuevo (onnx):  {t_new:.1f}s")

    # comparar solo palabras que AMBOS movieron respecto a whisper (i.e. alineadas)
    diffs, moved = [], 0
    for w, o, n in zip(words, old, new):
        o_moved = abs(o["start"] - w["start"]) > 1e-6
        n_moved = abs(n["start"] - w["start"]) > 1e-6
        if o_moved and n_moved:
            diffs.append(abs(o["start"] - n["start"])); moved += 1
    if not diffs:
        print("no hay palabras comparables"); return
    diffs.sort()
    print(f"\ncomparables: {moved}/{len(words)}")
    print(f"  dif media   : {statistics.mean(diffs)*1000:.0f} ms")
    print(f"  dif mediana : {statistics.median(diffs)*1000:.0f} ms")
    print(f"  p90         : {diffs[int(len(diffs)*0.90)]*1000:.0f} ms")
    print(f"  p99         : {diffs[int(len(diffs)*0.99)]*1000:.0f} ms")
    print(f"  máx         : {diffs[-1]*1000:.0f} ms")
    within = sum(1 for d in diffs if d <= 0.05) / len(diffs) * 100
    print(f"  dentro de 50 ms: {within:.1f}%")
    print("\nmuestra (whisper -> viejo | nuevo):")
    for i in range(0, min(len(words), 400), 60):
        print(f"  {words[i]['text']!r:16} {words[i]['start']:6.2f} -> "
              f"{old[i]['start']:6.2f} | {new[i]['start']:6.2f}")


if __name__ == "__main__":
    main()
