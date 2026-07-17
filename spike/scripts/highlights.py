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
        "largo, numerada por oración con sus tiempos. Elige los mejores fragmentos "
        "CONTIGUOS que funcionen como short independiente.\n"
        "REGLAS CLAVE:\n"
        "- El fragmento DEBE ENTREGAR el valor, no solo prometerlo: que contenga la "
        "respuesta, el dato, la revelación o el remate concretos.\n"
        "- RECHAZA tramos que sean solo preparación, búsqueda, titubeos, relleno o "
        "'ahorita lo busco' sin que se diga el contenido prometido.\n"
        "- Debe tener un gancho al inicio y una idea que se completa dentro del clip.\n"
        "Responde SOLO JSON."
    )
    user = (
        f"Transcripción:\n" + "\n".join(lines) +
        f"\n\nElige los {n} mejores fragmentos para shorts de entre {MIN_SEC} y {MAX_SEC} "
        f"segundos. Cada fragmento es un rango de oraciones contiguas [from_i, to_i]. "
        "El 'title' debe reflejar lo que REALMENTE se dice en ese rango (no lo que se "
        "promete en otro lado). "
        'Devuelve JSON: {"clips":[{"from_i":int,"to_i":int,"title":"gancho corto",'
        '"reason":"qué valor concreto entrega y por qué engancha"}]}'
    )
    try:
        raw = _ollama_chat(system, user)
    except Exception:  # sin ollama: heurística local (la app degrada con gracia)
        return _fallback(sentences, n)
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


def _fallback(sentences, n):
    """Sin LLM: n ventanas de ~30s repartidas por el video, alineadas a oraciones,
    priorizando las zonas con más densidad de palabras (más habla = más contenido)."""
    if not sentences:
        return []
    dur = sentences[-1]["end"]
    target = (MIN_SEC + MAX_SEC) / 2
    clips = []
    for k in range(n):
        anchor = dur * (k + 0.5) / n
        i = min(range(len(sentences)), key=lambda j: abs(sentences[j]["start"] - anchor))
        start = sentences[i]["start"]; b = i
        while b + 1 < len(sentences) and sentences[b + 1]["end"] - start < target:
            b += 1
        end = min(sentences[b]["end"], start + MAX_SEC)
        if end - start < MIN_SEC and start + MIN_SEC <= dur:
            end = start + MIN_SEC
        if any(start < pc["end"] and end > pc["start"] for pc in clips):
            continue
        clips.append({"start": round(start, 2), "end": round(end, 2),
                      "title": f"Momento {k + 1}",
                      "reason": "selección automática (sin IA local instalada)"})
    return clips


if __name__ == "__main__":
    import sys
    words = load_words(sys.argv[1])
    sents = build_sentences(words)
    print(f"{len(words)} words -> {len(sents)} sentences")
    for c in select_highlights(sents, n=int(sys.argv[2]) if len(sys.argv) > 2 else 2):
        print(f'  {c["start"]:.1f}-{c["end"]:.1f}s ({c["end"]-c["start"]:.0f}s)  '
              f'"{c["title"]}" — {c["reason"]}')
