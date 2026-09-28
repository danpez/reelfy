#!/usr/bin/env python3
"""
Caption translation (ES -> EN/PT/FR/DE/IT) via the local LLM (ollama), keeping timing.

Whisper gives Spanish words with tight timings. For translated captions we translate
each PHRASE (so meaning survives), keep the phrase's time span, and redistribute
word timings inside it proportionally to word length. Phrase-level sync stays
exact; word-highlight sync is approximate but natural. Local, no cloud.

Titulos del plan v2 (translate_titles): cada titulo se traduce COMPLETO (sus lineas son partes
de una misma frase) con la frase hablada que lo cubre como contexto, y vuelve con el MISMO numero
de lineas (la maquetacion del titulo no cambia). El idioma destino se nombra en su propia lengua
("português do Brasil", "Deutsch"): nombrado en espanol, el modelo local mezclaba gallego en pt.
Las funciones *_ex informan que textos quedaron sin traducir y el ultimo error (Ollama caido):
el servidor no devuelve espanol disfrazado de traduccion.
"""
import hashlib
import json
import re
import unicodedata
import urllib.request

import highlights as hl  # model name

_CACHE: dict[str, list] = {}   # per-process; keyed by content hash
LANGS = {"en": "inglés", "pt": "portugués brasileño", "fr": "francés",
         "de": "alemán", "it": "italiano"}
# el idioma destino nombrado en SU lengua (y lo que no debe ser): el modelo acierta mas
NATIVE = {"en": "English (natural US social-media English)",
          "pt": "português do Brasil (português brasileiro; NÃO galego, NÃO espanhol)",
          "fr": "français (français naturel, pas espagnol)",
          "de": "Deutsch (natürliches Deutsch, kein Spanisch)",
          "it": "italiano (italiano naturale, non spagnolo)"}
# palabras que delatan otro idioma en la salida (gallego en pt; espanol que se colo)
_WRONG = {"pt": {"non", "sen", "moi", "unha", "cando", "tamén", "xa", "máis", "dende", "ningún",
                 "pero", "muy", "cuando", "también", "ahora", "nuestro", "mucho", "hacer", "tienes"},
          "en": {"el", "los", "las", "una", "pero", "muy", "cuando", "también", "porque", "para"},
          "fr": {"el", "los", "una", "pero", "muy", "cuando", "también", "porque"},
          "de": {"el", "los", "las", "una", "pero", "muy", "cuando", "también", "porque", "para"},
          "it": {"el", "los", "las", "muy", "cuando", "también", "porque"}}
CHUNK = 16                      # lines per LLM call: keeps prompts well inside num_ctx
CHUNK_T = 8                     # titulos por llamada (cada uno con su contexto)
LAST_ERROR: dict[str, str] = {}  # ultimo error de conexion/modelo (para el mensaje del servidor)


def _chat(system, user):
    """Ollama chat with a LARGE context window. The default num_ctx (2048) silently
    truncates long transcripts -> garbled/partial JSON. Root cause of the EN bug."""
    body = {"model": hl.MODEL, "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0.2, "num_ctx": 8192},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    req = urllib.request.Request(hl.OLLAMA_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["message"]["content"]


def _chat_json(system, user):
    """_chat -> dict (o None). Recuerda el ultimo error en LAST_ERROR['msg']."""
    try:
        raw = json.loads(_chat(system, user))
        LAST_ERROR.pop("msg", None)
        return raw if isinstance(raw, dict) else None
    except Exception as e:  # noqa
        LAST_ERROR["msg"] = str(e)
        return None


def _native(lang):
    return NATIVE.get(lang, NATIVE["en"])


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _words(s):
    return re.findall(r"[a-záéíóúâêôãõàçñüäöß]+", str(s or "").lower())


def neutral(text):
    """Texto que no depende del idioma (numeros, precios, simbolos): puede quedar igual."""
    return len(re.sub(r"[\W\d_]+", "", str(text or ""))) < 3


def same_as_source(src, out):
    return _norm(src) == _norm(out)


def lang_ok(text, lang):
    """False si la salida trae palabras de otro idioma (gallego en pt, espanol que se colo)."""
    bad = _WRONG.get(lang) or set()
    return not any(w in bad for w in _words(text))


def _translate_one(text, lang="en"):
    tgt = _native(lang)
    got = _chat_json(
        f"Traductor profesional español -> {tgt} (subtítulos, registro hablado). "
        "Conserva nombres propios. Responde SOLO JSON.",
        json.dumps({"text": text}, ensure_ascii=False) +
        f'\n\nDevuelve {{"text": "<traducción al {tgt}>"}}')
    v = (got or {}).get("text")
    return v if isinstance(v, str) and v.strip() else None


def _translate_chunk(texts, base, lang="en", strict=False):
    """One numbered-dict LLM call for <=CHUNK lines. Returns {abs_index: tgt}."""
    tgt = _native(lang)
    numbered = {str(base + i): t for i, t in enumerate(texts)}
    system = (f"Eres un traductor profesional de subtítulos del español al {tgt} "
              "(registro hablado, conciso). Traduce CADA entrada por separado — "
              "NO unas ni dividas líneas. Conserva nombres propios. Responde SOLO JSON.")
    if strict:
        system += f" IMPORTANTE: la salida debe estar 100 % en {tgt}."
    user = (json.dumps(numbered, ensure_ascii=False) +
            "\n\nDevuelve el MISMO objeto JSON (mismas claves) con cada valor "
            f"traducido al {tgt}.")
    raw = _chat_json(system, user)
    if raw is None:
        print(f"   translation chunk@{base} failed ({LAST_ERROR.get('msg')})")
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, str) and v.strip()}


def translate_texts_ex(texts, lang="en"):
    """-> (out, untranslated, error). Como translate_texts; `untranslated` = indices que
    quedaron en espanol (o iguales al original sin ser neutros); `error` = ultimo error del LLM."""
    if not texts:
        return list(texts or []), [], None
    LAST_ERROR.pop("msg", None)
    got = {}
    for base in range(0, len(texts), CHUNK):
        part = _translate_chunk(texts[base:base + CHUNK], base, lang)
        bad = [k for k, v in part.items() if not lang_ok(v, lang)]
        if bad:                                  # otro idioma colado: una vez mas, estricto
            again = _translate_chunk(texts[base:base + CHUNK], base, lang, strict=True)
            for k in bad:
                if again.get(k) and lang_ok(again[k], lang):
                    part[k] = again[k]
        got.update(part)
    out, miss = [], []
    for i, t in enumerate(texts):
        v = got.get(str(i))
        if not v and not LAST_ERROR.get("msg"):
            v = _translate_one(t, lang)
        if not v or (same_as_source(t, v) and not neutral(t)):
            miss.append(i)
        out.append(v or t)
    if miss:
        print(f"   translation: {len(miss)}/{len(texts)} lines kept in Spanish")
    return out, miss, LAST_ERROR.get("msg")


def translate_texts(texts, lang="en"):
    """Translate Spanish strings with GUARANTEED 1:1 line coverage.

    Long transcripts are CHUNKED (long single prompts overflowed the LLM context
    and came back truncated). Numbered-dict keys stop line merging; any key the
    model still drops is retried individually. Worst case a line stays Spanish."""
    if not texts:
        return texts
    return translate_texts_ex(texts, lang)[0]


# ------------------------------------------------------------------ titulos del plan v2
def spoken_context(text, words, pad=6):
    """Frase hablada que cubre el titulo: la ventana de la transcripcion (lista de palabras) que
    contiene mas palabras del titulo EN ORDEN (subsecuencia; las palabras comunes sueltas no
    cuentan como coincidencia) + `pad` palabras a cada lado. None si el titulo no sale de lo que
    se dice (p. ej. un precio o un rotulo)."""
    tw = [w for w in _norm(text).split() if w]
    if not tw or not words:
        return None
    nw = [_norm(w) for w in words]
    k = len(tw)
    best = (0, 0, None, None)                     # (coincidencias, -largo, inicio, fin)
    for i in range(len(nw)):
        if nw[i] not in tw[:max(1, k // 2 + 1)]:
            continue
        j, m, last = tw.index(nw[i]) + 1, 1, i
        for pos in range(i + 1, min(len(nw), i + 2 * k + 4)):
            if j < k and nw[pos] in tw[j:j + 3]:
                j = tw.index(nw[pos], j) + 1
                m += 1
                last = pos
        cand = (m, -(last - i), i, last)
        if cand[:2] > best[:2]:
            best = cand
    m, _, a, b = best
    if a is None or m < max(2 if k > 1 else 1, int(0.6 * k + 0.5)):
        return None
    # solo lo dicho ANTES: con lo que sigue, el modelo local continuaba la frase en la traduccion
    before = " ".join(str(w) for w in words[max(0, a - pad):a])
    return f"...{before} [{' '.join(str(w) for w in words[a:b + 1])}]".replace("... [", "[")


def _split_lines(text, n):
    """Reparte `text` en n lineas balanceadas por caracteres (respaldo si el modelo no respeta
    el numero de lineas). Con menos palabras que lineas sobran lineas vacias."""
    ws = str(text).split()
    if n <= 1:
        return [" ".join(ws)]
    if len(ws) <= n:
        return ws + [""] * (n - len(ws))
    total = sum(len(w) for w in ws)
    out, cur, acc = [], [], 0
    for i, w in enumerate(ws):
        cur.append(w)
        acc += len(w)
        lines_left, words_left = n - len(out) - 1, len(ws) - i - 1
        if lines_left > 0 and words_left >= lines_left and \
                (acc >= total * (len(out) + 1) / n or words_left == lines_left):
            out.append(" ".join(cur))
            cur = []
    out.append(" ".join(cur))
    return out


def _tidy(src, out):
    """Quita los puntos suspensivos que el modelo agrega al final cuando el titulo termina a media
    frase (el original no los lleva)."""
    o = str(out).rstrip()
    if not str(src).rstrip().endswith(("...", "…")):
        o = re.sub(r"\s*(\.\.\.|…)+$", "", o)
    return o


def _case_like(src, out):
    s = str(src).strip()
    if s and s.upper() == s and any(c.isalpha() for c in s):
        return str(out).upper()
    return out


def _title_call(lines, lang, context=None, strict=False):
    """UN titulo (sus lineas) -> [lineas traducidas] o None. Un titulo por llamada: con varios
    titulos y sus contextos en la misma llamada el modelo local mezclaba el contexto de uno en
    la traduccion de otro."""
    tgt = _native(lang)
    n = len(lines)
    system = (
        f"Eres traductor profesional de TITULOS para videos cortos (Reels/TikTok) del español al {tgt}. "
        "El título está partido en líneas que son partes de UNA misma frase: tradúcelo COMPLETO con "
        f"sentido natural y repártelo en EXACTAMENTE {n} línea(s), en el mismo orden de ideas (cada "
        "línea traduce su parte). Traduce SOLO el título: no agregues palabras ni ideas que no estén en "
        "él (el contexto es solo para entender el sentido; NO lo traduzcas ni lo continúes). El título "
        "puede terminar a media frase (sigue en el título siguiente): tradúcelo igual de incompleto, "
        "sin completar la idea. Longitud parecida al original. Conserva nombres propios, marcas, "
        "números y precios. Responde SOLO JSON.")
    if strict:
        system += f" IMPORTANTE: 100 % en {tgt} y exactamente {n} línea(s)."
    user = ""
    if context:
        user += ("Contexto hablado (entre corchetes, la parte que el título muestra; NO traduzcas "
                 f"el resto): {json.dumps(context, ensure_ascii=False)}\n\n")
    user += (f"TÍTULO a traducir ({n} línea(s)): " + json.dumps({"lineas": lines}, ensure_ascii=False) +
             f'\n\nDevuelve {{"lineas": [...]}} con exactamente {n} línea(s) en {tgt}.')
    raw = _chat_json(system, user)
    v = (raw or {}).get("lineas")
    if v is None and raw and len(raw) == 1:
        v = next(iter(raw.values()))
    if isinstance(v, str):
        v = v.split("\n")
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return [x.strip() for x in v]
    return None


def translate_titles(texts, lang="en", contexts=None):
    """Titulos y textos de graficos (pueden tener varias lineas con '\\n') -> (out, untranslated,
    error). Conserva el numero de lineas de cada texto (y MAYUSCULAS); contexts[i] = frase
    hablada que lo cubre (o None). Lo que no respeta las lineas, sale en otro idioma o se alarga
    de mas (el modelo tradujo tambien el contexto) se reintenta estricto y sin contexto; ultimo
    respaldo: la traduccion completa repartida en las lineas del original."""
    texts = list(texts or [])
    if not texts:
        return [], [], None
    LAST_ERROR.pop("msg", None)
    contexts = list(contexts or []) + [None] * len(texts)
    out, miss = [], []
    down = False
    for i, t in enumerate(texts):
        lines = str(t).split("\n")
        if neutral(t) or down:
            out.append(t)
            if not neutral(t):
                miss.append(i)
            continue
        # una traduccion mucho mas larga que el original tradujo tambien el contexto
        k = 1.9 if lang == "de" else 1.7
        limit, wlimit = k * len(str(t)) + 10, 1.8 * len(str(t).split()) + 2

        def fine(v, need_n=True):
            return isinstance(v, list) and any(x for x in v) and \
                (not need_n or (len(v) == len(lines) and all(v))) and \
                lang_ok(" ".join(v), lang) and len(" ".join(v)) <= limit and \
                len(" ".join(v).split()) <= wlimit
        # el contexto solo ayuda a una clausula larga partida (p. ej. termina en "tus"); en un
        # rotulo corto el modelo tiende a traducir el contexto en vez del rotulo
        ctx = contexts[i] if len(str(t).split()) >= 4 else None
        got = _title_call(lines, lang, ctx)
        if got is None and LAST_ERROR.get("msg"):
            down = True                          # Ollama caido: no insistir con cada titulo
        elif not fine(got):
            again = _title_call(lines, lang, None, strict=True)
            if fine(again):
                got = again
            elif fine(again, need_n=False):
                got = _split_lines(" ".join(x for x in again if x), len(lines))
            elif fine(got, need_n=False):
                got = _split_lines(" ".join(x for x in got if x), len(lines))
            else:
                got = None
        if not got:
            out.append(t)
            miss.append(i)
            continue
        tr = "\n".join(_case_like(a, _tidy(a, b)) for a, b in zip(lines, got))
        if same_as_source(t, tr):
            miss.append(i)
        out.append(tr)
    if miss:
        print(f"   translation (titles): {len(miss)}/{len(texts)} kept in Spanish")
    return out, miss, LAST_ERROR.get("msg")


def translate_phrases(phrases, lang="en", info=None):
    """phrases: [[{text,start,end},...],...] in Spanish -> same shape in `lang`.
    Cached per content so preview + render don't pay the LLM twice. `info` (dict) recibe
    untranslated (indices de frases en espanol) y error."""
    src = [" ".join(w["text"] for w in ph) for ph in phrases]
    key = hashlib.sha1((lang + "\n" + "\n".join(src)).encode()).hexdigest()
    if key in _CACHE:
        if info is not None:
            info.update(untranslated=[], error=None)
        return _CACHE[key]
    en_lines, miss, err = translate_texts_ex(src, lang)
    if info is not None:
        info.update(untranslated=miss, error=err)
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
    if len(miss) < max(1, len(src)):             # no se cachea un fallo total (Ollama caido)
        _CACHE[key] = out
    return out


if __name__ == "__main__":
    demo = [[{"text": "Hola", "start": 0, "end": 0.4}, {"text": "gente,", "start": 0.4, "end": 0.9}],
            [{"text": "esto", "start": 1.2, "end": 1.5}, {"text": "es", "start": 1.5, "end": 1.7},
             {"text": "una", "start": 1.7, "end": 1.9}, {"text": "prueba", "start": 1.9, "end": 2.4}]]
    for ph in translate_phrases(demo):
        print([(w["text"], w["start"], w["end"]) for w in ph])
