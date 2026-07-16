#!/usr/bin/env python3
"""
Phase 2 — highlight selection for Reelfy spike.

From the word-level transcript, group into sentences (timestamped), then ask a
LOCAL LLM (ollama, local-first thesis — no cloud, no keys) to pick the best
contiguous span(s) that would make an engaging short. Returns time ranges +
a hook title, which the pipeline then renders (reframe + captions) as clips.
"""
import json
import re
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:7b"

# target short length window (seconds)
MIN_SEC, MAX_SEC = 18, 60


def load_words(json_path):
    data = json.load(open(json_path))
    out = []
    for seg in data["transcription"]:
        t = seg["text"].strip()
        if t:
            out.append({"text": t, "start": seg["offsets"]["from"] / 1000.0,
                        "end": seg["offsets"]["to"] / 1000.0})
    return out


def build_sentences(words, max_sec=12):
    """Group words into sentences by end punctuation or a max duration."""
    sents, cur = [], []
    for w in words:
        cur.append(w)
        dur = cur[-1]["end"] - cur[0]["start"]
        if w["text"][-1:] in ".!?" or dur >= max_sec:
            sents.append(cur); cur = []
    if cur:
        sents.append(cur)
    return [{"i": i, "start": round(s[0]["start"], 2), "end": round(s[-1]["end"], 2),
             "text": " ".join(w["text"] for w in s)} for i, s in enumerate(sents)]


def _ollama_chat(system, user, fmt="json"):
    body = {"model": MODEL, "stream": False, "format": fmt,
            "options": {"temperature": 0.2},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["message"]["content"]


def select_highlights(sentences, n=1):
    """Ask the local LLM for the best n contiguous spans. Returns list of
    {start, end, title, reason} clamped to MIN/MAX_SEC."""
    lines = [f'[{s["i"]}] ({s["start"]:.1f}-{s["end"]:.1f}s) {s["text"]}' for s in sentences]
    system = (
        "Eres un editor de video experto en shorts virales para redes (TikTok, "
        "Reels, Shorts) en español/LATAM. Se te da la transcripción de un video "
        "largo, numerada por oración con sus tiempos. Debes elegir los mejores "
        "fragmentos CONTIGUOS que funcionen como short independiente: un gancho "
        "claro, una idea completa y valor o entretenimiento. Responde SOLO JSON."
    )
    user = (
        f"Transcripción:\n" + "\n".join(lines) +
        f"\n\nElige los {n} mejores fragmentos para shorts de entre {MIN_SEC} y {MAX_SEC} "
        f"segundos. Cada fragmento es un rango de oraciones contiguas [from_i, to_i]. "
        'Devuelve JSON: {"clips":[{"from_i":int,"to_i":int,"title":"gancho corto",'
        '"reason":"por qué engancha"}]}'
    )
    raw = _ollama_chat(system, user)
    data = json.loads(raw)
    by_i = {s["i"]: s for s in sentences}
    clips = []
    for c in data.get("clips", []):
        a, b = int(c["from_i"]), int(c["to_i"])
        if a > b or a not in by_i or b not in by_i:
            continue
        start, end = by_i[a]["start"], by_i[b]["end"]
        if end - start < MIN_SEC:  # extend forward to reach min length
            while b + 1 in by_i and by_i[b + 1]["end"] - start < MAX_SEC:
                b += 1; end = by_i[b]["end"]
        end = min(end, start + MAX_SEC)
        # skip clips that overlap an already-selected one (want distinct shorts)
        if any(start < pc["end"] and end > pc["start"] for pc in clips):
            continue
        clips.append({"start": round(start, 2), "end": round(end, 2),
                      "title": c.get("title", ""), "reason": c.get("reason", "")})
    return clips


if __name__ == "__main__":
    import sys
    words = load_words(sys.argv[1])
    sents = build_sentences(words)
    print(f"{len(words)} words -> {len(sents)} sentences")
    for c in select_highlights(sents, n=int(sys.argv[2]) if len(sys.argv) > 2 else 2):
        print(f'  {c["start"]:.1f}-{c["end"]:.1f}s ({c["end"]-c["start"]:.0f}s)  '
              f'"{c["title"]}" — {c["reason"]}')
