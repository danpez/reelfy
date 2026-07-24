#!/usr/bin/env python3
"""Resaltado inteligente de captions (D1): el LLM local elige, por frase, las
palabras con carga (keywords) y un emoji opcional.

- Keywords -> se pintan de un color acento PERSISTENTE en los captions (solo
  color: las métricas del texto no cambian -> cero reflow, regla de oro).
- Emoji -> NO se quema en el texto (libass lo renderiza como tofu, verificado);
  se dibuja como overlay PNG flotante encima del bloque de captions
  (Pillow + Apple Color Emoji / fuente de emojis del sistema).

Si el LLM no está disponible, cae a una heurística (números y palabras largas).
"""
import json
import os
import re
import unicodedata
import urllib.request

# palabras funcionales del español que jamás deben resaltarse
_STOP = set("""el la los las un una unos unas de del al a ante con contra para por
según sin sobre tras en entre hacia hasta y o u e ni que como cuando donde quien
cual si no sé se su sus mi mis tu tus le les lo nos os me te yo tú él ella ellos
ellas esto esta este estos estas eso esa ese esos esas aquello aquel aquella es
son era eran fue fueron ser estar está están estaba estaban hay muy más menos ya
también pero porque pues así bien mal casi solo sólo cada toda todo todas todos
otra otro otras otros algo alguien nada nadie uno dos les etc vamos va voy""".split())

_EMOJI_OK = set("🔥💰🚀😱✨💡🎯⚡❌✅😍🤯👀💪🎉⭐️❤️😂🙌🏆⏰")


def _norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _heuristic(phrase_texts):
    """Fallback sin LLM: números y las 1-2 palabras más largas (no stopwords)."""
    out = []
    for txt in phrase_texts:
        words = txt.split()
        picks = []
        for w in words:
            if re.search(r"\d", w):
                picks.append(w)
        cands = sorted((w for w in words
                        if _norm(w) not in _STOP and len(_norm(w)) >= 6 and w not in picks),
                       key=lambda x: -len(x))
        picks += cands[:max(0, 2 - len(picks))]
        out.append({"keywords": picks[:2], "emoji": None})
    return out


def _llm(prompt, timeout=45):
    url = os.environ.get("REELFY_OLLAMA_URL", "http://localhost:11434") + "/api/generate"
    model = os.environ.get("REELFY_LLM_MODEL", "qwen2.5:7b")
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.2}, "format": "json"}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["response"]


def pick(phrases):
    """phrases: lista de frases (lista de word-dicts). Devuelve por frase:
    {"keywords": [textos], "emoji": str|None}. Nunca lanza (fallback heurístico)."""
    texts = [" ".join(w["text"] for w in ph) for ph in phrases]
    try:
        numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(texts))
        prompt = f"""Eres editor de videos virales en español. Para CADA línea de subtítulo,
elige las palabras con más carga (máximo 2: números, verbos fuertes, sustantivos clave,
nombres) y opcionalmente UN emoji si la línea lo amerita (máximo 1 de cada 3 líneas
con emoji; usa solo: 🔥 💰 🚀 😱 ✨ 💡 🎯 ⚡ ❌ ✅ 😍 🤯 👀 💪 🎉 ⭐️ ❤️ 🙌 🏆 ⏰).
Nunca elijas artículos, preposiciones ni muletillas. Copia las palabras EXACTAMENTE
como aparecen.

Líneas:
{numbered}

Responde SOLO JSON: {{"lines": [{{"i": 0, "keywords": ["palabra"], "emoji": "🔥" }}, ...]}}
(emoji null si no aplica; incluye TODAS las líneas)."""
        raw = json.loads(_llm(prompt))
        lines = raw.get("lines") or raw.get("items") or []
        out = _heuristic(texts)          # base: heurística; el LLM la refina
        emoji_budget = max(1, len(texts) // 3)
        used = 0
        for it in lines:
            i = it.get("i")
            if not isinstance(i, int) or not (0 <= i < len(texts)):
                continue
            words_in_line = {_norm(w): w for w in texts[i].split()}
            kws = [words_in_line[_norm(k)] for k in (it.get("keywords") or [])
                   if _norm(k) in words_in_line and _norm(k) not in _STOP][:2]
            if kws:
                out[i]["keywords"] = kws
            em = it.get("emoji")
            if em and em in _EMOJI_OK and used < emoji_budget:
                out[i]["emoji"] = em
                used += 1
        return out
    except Exception as e:  # noqa: LLM caído/timeout -> heurística
        print(f"   captionsmart: LLM no disponible ({e}); heurística")
        return _heuristic(texts)


def annotate(phrases):
    """Marca in-place: word['kw']=True en keywords y ph[0]['emoji'] por frase."""
    picks = pick(phrases)
    for ph, p in zip(phrases, picks):
        kw_norm = {_norm(k) for k in p["keywords"]}
        for w in ph:
            if _norm(w["text"]) in kw_norm and _norm(w["text"]):
                w["kw"] = True
        if p.get("emoji") and ph:
            ph[0]["emoji"] = p["emoji"]
    return phrases
