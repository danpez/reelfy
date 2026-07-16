#!/usr/bin/env python3
"""
Caption translation (ES -> EN) via the local LLM (ollama), keeping timing.

Whisper gives Spanish words with tight timings. For English captions we translate
each PHRASE (so meaning survives), keep the phrase's time span, and redistribute
word timings inside it proportionally to word length. Phrase-level sync stays
exact; word-highlight sync is approximate but natural. Local, no cloud.
"""
import hashlib
import json

import highlights as hl  # reuse the ollama chat helper + model

_CACHE: dict[str, list] = {}   # per-process; keyed by content hash


def _translate_one(text):
    try:
        got = json.loads(hl._ollama_chat(
            "Traductor profesional español->inglés (subtítulos, registro hablado). "
            "Conserva nombres propios. Responde SOLO JSON.",
            json.dumps({"text": text}, ensure_ascii=False) +
            '\n\nDevuelve {"text": "<traducción al inglés>"}'))
        v = got.get("text")
        return v if isinstance(v, str) and v.strip() else None
    except Exception:  # noqa
        return None


def translate_texts(texts):
    """Translate Spanish strings to English with GUARANTEED 1:1 line coverage.

    A plain JSON array prompt lets the LLM merge/split lines (observed: 8 lines
    back for 12 in). Numbered-dict keys keep it honest; any key it still drops
    gets retried individually. Worst case a line stays Spanish."""
    if not texts:
        return texts
    numbered = {str(i): t for i, t in enumerate(texts)}
    system = ("Eres un traductor profesional de subtítulos español->inglés "
              "(registro hablado, conciso). Traduce CADA entrada por separado — "
              "NO unas ni dividas líneas. Conserva nombres propios. Responde SOLO JSON.")
    user = (json.dumps(numbered, ensure_ascii=False) +
            "\n\nDevuelve el MISMO objeto JSON (mismas claves) con cada valor "
            "traducido al inglés.")
    got = {}
    try:
        raw = json.loads(hl._ollama_chat(system, user))
        if isinstance(raw, dict):
            got = {k: v for k, v in raw.items() if isinstance(v, str) and v.strip()}
    except Exception as e:  # noqa
        print(f"   translation batch failed ({e}); retrying per-line")
    out, misses = [], 0
    for i, t in enumerate(texts):
        v = got.get(str(i)) or _translate_one(t)
        if not v:
            misses += 1
        out.append(v or t)
    if misses:
        print(f"   translation: {misses}/{len(texts)} lines kept in Spanish")
    return out


def translate_phrases(phrases):
    """phrases: [[{text,start,end},...],...] in Spanish -> same shape in English.
    Cached per content so preview + render don't pay the LLM twice."""
    src = [" ".join(w["text"] for w in ph) for ph in phrases]
    key = hashlib.sha1("\n".join(src).encode()).hexdigest()
    if key in _CACHE:
        return _CACHE[key]
    en_lines = translate_texts(src)
    out = []
    for ph, line in zip(phrases, en_lines):
        words = line.split() or [ph[0]["text"]]
        t0, t1 = ph[0]["start"], ph[-1]["end"]
        total = sum(len(x) for x in words) or 1
        t, new_ph = t0, []
        for x in words:
            d = (t1 - t0) * (len(x) / total)
            new_ph.append({"text": x, "start": round(t, 3), "end": round(t + d, 3)})
            t += d
        out.append(new_ph)
    _CACHE[key] = out
    return out


if __name__ == "__main__":
    demo = [[{"text": "Hola", "start": 0, "end": 0.4}, {"text": "gente,", "start": 0.4, "end": 0.9}],
            [{"text": "esto", "start": 1.2, "end": 1.5}, {"text": "es", "start": 1.5, "end": 1.7},
             {"text": "una", "start": 1.7, "end": 1.9}, {"text": "prueba", "start": 1.9, "end": 2.4}]]
    for ph in translate_phrases(demo):
        print([(w["text"], w["start"], w["end"]) for w in ph])
