#!/usr/bin/env python3
"""
Phase 2 — highlight selection for Reelfy spike.

From the word-level transcript, group into sentences (timestamped), then ask a
LOCAL LLM (ollama, local-first thesis — no cloud, no keys) to pick the best
contiguous span(s) that would make an engaging short. Returns time ranges +
a hook title, which the pipeline then renders (reframe + captions) as clips.
"""
import json
import os
import re
import urllib.request
from pathlib import Path

# apuntan al ollama EMBEBIDO cuando la app define REELFY_OLLAMA_URL; en dev, al del sistema
OLLAMA_URL = os.environ.get("REELFY_OLLAMA_URL", "http://localhost:11434") + "/api/chat"
MODEL = os.environ.get("REELFY_LLM_MODEL", "qwen3.5:4b")

# target short length window (seconds, en TIEMPO FINAL: ya sin silencios ni tomas cortadas)
MIN_SEC, MAX_SEC = 18, 60
TAKE_NEAR = 1.0   # s: un short no termina justo antes de una toma cortada (la idea sigue despues)


def load_words(json_path):
    data = json.load(open(json_path))
    out = []
    for seg in data["transcription"]:
        t = seg["text"].strip()
        if t:
            out.append({"text": t, "start": seg["offsets"]["from"] / 1000.0,
                        "end": seg["offsets"]["to"] / 1000.0})
    return out


def _cut_iv(cuts, takes_only=False, with_off=False):
    """Intervalos (fuente) de los cortes habilitados, ordenados y fundidos. `with_off`: tambien los
    apagados (una toma sugerida que el usuario puede encender despues)."""
    iv = []
    for c in cuts or []:
        if not isinstance(c, dict) or (c.get("enabled", True) is False and not with_off):
            continue
        if takes_only and c.get("src") != "takes":
            continue
        try:
            a, b = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a:
            iv.append((a, b))
    iv.sort()
    out = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def final_len(a, b, iv):
    """Duracion FINAL de [a, b] (fuente) sin los tramos cortados `iv`."""
    return max(0.0, (b - a) - sum(max(0.0, min(b, e) - max(a, s)) for s, e in iv))


def final_time(t, iv):
    """Tiempo final de t (fuente): t menos lo cortado antes (dentro de un corte, su inicio)."""
    return t - sum(max(0.0, min(t, e) - s) for s, e in iv if s < t)


def _src_at(start, target, iv, limit):
    """Tiempo fuente donde [start, x] llega a `target` s finales (tope `limit`)."""
    t, left = start, target
    for s, e in iv:
        if e <= t:
            continue
        if s >= limit:
            break
        if s - t >= left:
            return t + left
        left -= max(0.0, s - t)
        t = max(t, e)
    return min(limit, t + left)


def build_sentences(words, max_sec=12, cuts=None):
    """Group words into sentences by end punctuation or a max duration. Con `cuts` la duracion es
    la FINAL: una oracion que cruza una toma cortada no se parte en la union por el tiempo quitado."""
    iv = _cut_iv(cuts)
    sents, cur = [], []
    for w in words:
        cur.append(w)
        dur = final_len(cur[0]["start"], cur[-1]["end"], iv) if iv else cur[-1]["end"] - cur[0]["start"]
        if w["text"][-1:] in ".!?" or dur >= max_sec:
            sents.append(cur); cur = []
    if cur:
        sents.append(cur)
    return [{"i": i, "start": round(s[0]["start"], 2), "end": round(s[-1]["end"], 2),
             "text": " ".join(w["text"] for w in s)} for i, s in enumerate(sents)]


def _num_ctx(system, user, predict=700):
    """Contexto para el prompt (~3.2 caracteres por token en espanol): 4096 si cabe, si no 8192
    (sin num_ctx ollama usa el del modelo cargado y un video largo se truncaba en silencio)."""
    need = (len(system) + len(user)) / 3.2 + predict + 200
    return 4096 if need <= 4096 else 8192


def _ollama_chat(system, user, fmt="json"):
    # think False: con qwen3.5 (razonamiento) la respuesta llega vacia si no se apaga
    body = {"model": MODEL, "stream": False, "format": fmt, "think": False,
            "options": {"temperature": 0.2, "num_ctx": _num_ctx(system, user)},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["message"]["content"]


def _fit_prompt(sentences, iv, system, head, tail):
    """Lineas numeradas (tiempos FINALES) que caben en 8192 de contexto: un video muy largo
    recorta el texto de cada oracion antes que perder oraciones."""
    for cap in (None, 24, 14, 8):
        lines = []
        for s in sentences:
            words = s["text"].split()
            txt = " ".join(words[:cap]) + (" …" if cap and len(words) > cap else "")
            lines.append(f'[{s["i"]}] ({final_time(s["start"], iv):.1f}-{final_time(s["end"], iv):.1f}s) {txt}')
        user = head + "\n".join(lines) + tail
        if _num_ctx(system, user) <= 4096 or (len(system) + len(user)) / 3.2 + 900 <= 8192:
            return user
    return user


def _end_ok(end, take_iv):
    """Un short no termina dentro de una toma cortada ni justo antes de una (la toma que se queda
    viene despues: el short se quedaria sin la respuesta). `take_iv` lleva TODAS las tomas, tambien
    las sugeridas: los shorts no se recalculan al cambiar el nivel o encender una sugerencia."""
    return not any(s - 0.05 <= end < e or 0.0 <= s - end <= TAKE_NEAR for s, e in take_iv)


def _settle(sentences, a, b, iv, take_iv):
    """Rango de oraciones [a, b] -> (start, end) fuente con duracion FINAL en [MIN_SEC, MAX_SEC]
    (en lo posible) y un final valido (_end_ok). None si no queda nada razonable."""
    idx = {s["i"]: k for k, s in enumerate(sentences)}
    ka, kb = idx[a], idx[b]
    start = sentences[ka]["start"]

    def flen(k):
        return final_len(start, sentences[k]["end"], iv)
    while flen(kb) < MIN_SEC and kb + 1 < len(sentences) and flen(kb + 1) <= MAX_SEC:
        kb += 1
    while flen(kb) > MAX_SEC and kb > ka:
        kb -= 1
    # final valido: primero hacia adelante (incluye la toma buena), si no hacia atras
    if not _end_ok(sentences[kb]["end"], take_iv):
        fwd = next((k for k in range(kb + 1, len(sentences))
                    if flen(k) <= MAX_SEC and _end_ok(sentences[k]["end"], take_iv)), None)
        back = next((k for k in range(kb - 1, ka - 1, -1)
                     if _end_ok(sentences[k]["end"], take_iv) and flen(k) >= min(MIN_SEC, flen(kb) * 0.6)), None)
        kb = fwd if fwd is not None else (back if back is not None else kb)
    end = sentences[kb]["end"]
    if flen(kb) > MAX_SEC:                       # una sola oracion larguisima: se recorta
        end = _src_at(start, MAX_SEC, iv, end)
        for s, e in iv:                          # nunca termina dentro de un corte
            if s < end < e:
                end = s
    if end - start <= 0.5:
        return None
    return round(start, 2), round(end, 2)


def select_highlights(sentences, n=1, cuts=None, duration=None):
    """Ask the local LLM for the best n contiguous spans. Returns list of
    {start, end, title, reason} (tiempos FUENTE) con duracion FINAL (sin los `cuts` habilitados:
    silencios y tomas) entre MIN_SEC y MAX_SEC. `sentences` deben venir de las palabras que se
    quedan (takes.effective_words): un short nunca lleva una toma repetida ni termina dentro de
    una o justo antes de la toma buena."""
    if int(n or 0) <= 0:   # «¿Cuántos shorts sugerir?» = Ninguno: ni se le pregunta a la IA
        return []
    iv, take_iv = _cut_iv(cuts), _cut_iv(cuts, takes_only=True, with_off=True)
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
    tail = (
        f"\n\nElige los {n} mejores fragmentos para shorts de entre {MIN_SEC} y {MAX_SEC} "
        f"segundos. Cada fragmento es un rango de oraciones contiguas [from_i, to_i]. "
        "El 'title' debe reflejar lo que REALMENTE se dice en ese rango (no lo que se "
        "promete en otro lado). "
        'Devuelve JSON: {"clips":[{"from_i":int,"to_i":int,"title":"gancho corto",'
        '"reason":"qué valor concreto entrega y por qué engancha"}]}'
    )
    user = _fit_prompt(sentences, iv, system, "Transcripción:\n", tail)
    try:
        raw = _ollama_chat(system, user)
    except Exception:  # sin ollama: heurística local (la app degrada con gracia)
        return _fallback(sentences, n, cuts)
    data = json.loads(raw)
    by_i = {s["i"]: s for s in sentences}
    clips = []
    for c in data.get("clips", []):
        try:
            a, b = int(c["from_i"]), int(c["to_i"])
        except (KeyError, TypeError, ValueError):
            continue
        if a > b or a not in by_i or b not in by_i:
            continue
        span = _settle(sentences, a, b, iv, take_iv)
        if span is None:
            continue
        start, end = span
        # skip clips that overlap an already-selected one (want distinct shorts)
        if any(start < pc["end"] and end > pc["start"] for pc in clips):
            continue
        clips.append({"start": start, "end": end,
                      "title": c.get("title", ""), "reason": c.get("reason", "")})
    return clips


def _fallback(sentences, n, cuts=None):
    """Sin LLM: n ventanas de ~39 s FINALES repartidas por el video, alineadas a oraciones,
    priorizando las zonas con más densidad de palabras (más habla = más contenido)."""
    if not sentences:
        return []
    iv, take_iv = _cut_iv(cuts), _cut_iv(cuts, takes_only=True, with_off=True)
    dur = sentences[-1]["end"]
    target = (MIN_SEC + MAX_SEC) / 2
    clips = []
    for k in range(n):
        anchor = dur * (k + 0.5) / n
        i = min(range(len(sentences)), key=lambda j: abs(sentences[j]["start"] - anchor))
        start = sentences[i]["start"]; b = i
        while b + 1 < len(sentences) and final_len(start, sentences[b + 1]["end"], iv) < target:
            b += 1
        span = _settle(sentences, sentences[i]["i"], sentences[b]["i"], iv, take_iv)
        if span is None:
            continue
        start, end = span
        if final_len(start, end, iv) < MIN_SEC and not iv and start + MIN_SEC <= dur:
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
