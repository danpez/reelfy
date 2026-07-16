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
import urllib.request

import highlights as hl  # model name

_CACHE: dict[str, list] = {}   # per-process; keyed by content hash
CHUNK = 16                      # lines per LLM call: keeps prompts well inside num_ctx


def _chat(system, user):
    """Ollama chat with a LARGE context window. The default num_ctx (2048) silently
    truncates long transcripts -> garbled/partial JSON. Root cause of the EN bug."""
    body = {"model": hl.MODEL, "stream": False, "format": "json",
            "options": {"temperature": 0.2, "num_ctx": 8192},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    req = urllib.request.Request(hl.OLLAMA_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["message"]["content"]


def _translate_one(text):
    try:
        got = json.loads(_chat(
            "Traductor profesional español->inglés (subtítulos, registro hablado). "
            "Conserva nombres propios. Responde SOLO JSON.",
            json.dumps({"text": text}, ensure_ascii=False) +
            '\n\nDevuelve {"text": "<traducción al inglés>"}'))
        v = got.get("text")
        return v if isinstance(v, str) and v.strip() else None
    except Exception:  # noqa
        return None


def _translate_chunk(texts, base):
    """One numbered-dict LLM call for <=CHUNK lines. Returns {abs_index: en}."""
    numbered = {str(base + i): t for i, t in enumerate(texts)}
    system = ("Eres un traductor profesional de subtítulos español->inglés "
              "(registro hablado, conciso). Traduce CADA entrada por separado — "
              "NO unas ni dividas líneas. Conserva nombres propios. Responde SOLO JSON.")
    user = (json.dumps(numbered, ensure_ascii=False) +
            "\n\nDevuelve el MISMO objeto JSON (mismas claves) con cada valor "
            "traducido al inglés.")
    try:
        raw = json.loads(_chat(system, user))
        if isinstance(raw, dict):
            return {k: v for k, v in raw.items() if isinstance(v, str) and v.strip()}
    except Exception as e:  # noqa
        print(f"   translation chunk@{base} failed ({e})")
    return {}


def translate_texts(texts):
    """Translate Spanish strings to English with GUARANTEED 1:1 line coverage.

    Long transcripts are CHUNKED (long single prompts overflowed the LLM context
    and came back truncated). Numbered-dict keys stop line merging; any key the
    model still drops is retried individually. Worst case a line stays Spanish."""
    if not texts:
        return texts
    got = {}
    for base in range(0, len(texts), CHUNK):
        got.update(_translate_chunk(texts[base:base + CHUNK], base))
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
