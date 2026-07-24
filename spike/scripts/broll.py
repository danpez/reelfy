#!/usr/bin/env python3
"""B-roll automático (D3): cortes a metraje de stock según lo que dices.

- El LLM local lee el transcript (oraciones con tiempos) y propone 2-4 momentos
  con un término de búsqueda VISUAL en inglés (Pexels indexa mejor en inglés).
- Pexels Videos API busca y se descarga el mejor archivo (licencia Pexels:
  uso comercial gratis, sin atribución). Caché por consulta en WORK.
- El render inserta cada clip como cutaway a PANTALLA COMPLETA bajo los
  captions, sin tocar el audio (la voz sigue; el visual corta a stock).

La clave vive en DATA/settings.json ({"pexels_key": ...}) o env REELFY_PEXELS_KEY.
Sin clave o sin red, la feature se omite en silencio (local-first: nada se rompe).
"""
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request

import paths

WORK = paths.WORK
SETTINGS = paths.DATA / "settings.json"

CUT_SECS = 2.2          # duración de cada cutaway
MIN_GAP = 8.0           # separación mínima entre cutaways
SKIP_HEAD = 4.0         # nunca tapar el hook inicial


def get_key():
    k = os.environ.get("REELFY_PEXELS_KEY")
    if k:
        return k
    try:
        return json.loads(SETTINGS.read_text()).get("pexels_key")
    except Exception:  # noqa
        return None


def set_key(key):
    d = {}
    try:
        d = json.loads(SETTINGS.read_text())
    except Exception:  # noqa
        pass
    d["pexels_key"] = key.strip()
    SETTINGS.write_text(json.dumps(d))


def _llm(prompt, timeout=60):
    url = os.environ.get("REELFY_OLLAMA_URL", "http://localhost:11434") + "/api/generate"
    model = os.environ.get("REELFY_LLM_MODEL", "qwen2.5:7b")
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.3}, "format": "json"}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["response"]


def suggest(sentences, duration, max_n=4, topic=""):
    """sentences: [{text,start,end}]. Devuelve [{query,start,end,enabled}] con
    momentos donde un corte a stock APOYA lo dicho. `topic` (glosario/producto del
    video) ANCLA las búsquedas para que el stock sea RELEVANTE al tema real y no
    invente cosas ajenas. Nunca lanza. OFF por defecto en la UI: solo sugiere."""
    try:
        lines = "\n".join(f"[{s['start']:.1f}s] {s['text']}" for s in sentences)
        anchor = ""
        if topic and topic.strip():
            anchor = (f"\nTEMA/PRODUCTO REAL del video: «{topic.strip()}». TODAS las búsquedas "
                      f"deben ser coherentes con ese tema — NO inventes marcas/productos ajenos "
                      f"ni muestres cosas que contradigan lo que se ve. Si dudas, usa el tema real.")
        prompt = f"""Eres editor de video. Del siguiente guion hablado (español), elige hasta
{max_n} momentos donde un corte a video de stock REFUERCE visualmente lo que se dice
(objetos, lugares, acciones concretas — no elijas momentos donde el hablante es lo
importante). Para cada uno da un término de búsqueda VISUAL en INGLÉS (2-4 palabras,
concreto y GENÉRICO, sin marcas inventadas: "perfume bottle", "pouring coffee").{anchor}

Guion:
{lines}

Responde SOLO JSON: {{"cuts": [{{"t": <segundos>, "query": "<búsqueda en inglés>"}}]}}"""
        raw = json.loads(_llm(prompt))
        cuts = raw.get("cuts") or []
        out, last = [], -1e9
        for c in sorted(cuts, key=lambda x: float(x.get("t", 0))):
            t = float(c.get("t", 0))
            q = re.sub(r"[^a-zA-Z0-9 ]", "", str(c.get("query", ""))).strip()
            if not q or t < SKIP_HEAD or t - last < MIN_GAP or t + CUT_SECS > duration - 1:
                continue
            out.append({"query": q, "start": round(t, 2),
                        "end": round(min(t + CUT_SECS, duration - 0.5), 2), "enabled": True})
            last = t
            if len(out) >= max_n:
                break
        return out
    except Exception as e:  # noqa
        print(f"   broll: sugerencias no disponibles ({e})")
        return []


def fetch(query, key):
    """Busca en Pexels y descarga el mejor MP4 (vertical si hay). Caché en WORK.
    Devuelve la ruta o None."""
    dst = WORK / f"broll_{hashlib.sha1(query.encode()).hexdigest()[:12]}.mp4"
    if dst.exists() and dst.stat().st_size > 100_000:
        return str(dst)
    try:
        url = ("https://api.pexels.com/videos/search?" +
               urllib.parse.urlencode({"query": query, "per_page": 3,
                                       "orientation": "portrait", "size": "medium"}))
        req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "Reelfy/0.3"})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        videos = data.get("videos") or []
        if not videos:  # reintento sin orientación (hay términos sin vertical)
            url = ("https://api.pexels.com/videos/search?" +
                   urllib.parse.urlencode({"query": query, "per_page": 3}))
            req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "Reelfy/0.3"})
            videos = json.loads(urllib.request.urlopen(req, timeout=20).read()).get("videos") or []
        if not videos:
            return None
        # mejor archivo: altura >= 1080 preferida, el más chico que cumpla
        best = None
        for v in videos:
            files = sorted((f for f in v.get("video_files", []) if f.get("height")),
                           key=lambda f: (f["height"] < 1080, abs(f["height"] - 1440)))
            if files:
                best = files[0]
                break
        if not best:
            return None
        req = urllib.request.Request(best["link"], headers={"User-Agent": "Reelfy/0.3"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        return str(dst) if dst.stat().st_size > 100_000 else None
    except Exception as e:  # noqa
        print(f"   broll: '{query}' falló ({e})")
        return None


def resolve(plan_broll):
    """[{query,start,end,enabled}] -> [(path, start, end)] descargando lo habilitado."""
    key = get_key()
    if not key:
        print("   broll: sin clave de Pexels; se omite")
        return None
    out = []
    for b in plan_broll or []:
        if not b.get("enabled", True):
            continue
        p = fetch(b["query"], key)
        if p:
            out.append((p, float(b["start"]), float(b["end"])))
    return out or None
