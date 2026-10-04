"""Videos de apoyo en FRAGMENTOS: que toma va mientras se dice cada frase (puro, sin ffmpeg ni red).

Un video de apoyo largo y mudo (una presentacion del producto de 2-3 min) no entra entero ni de un solo
lugar: se usa en VARIOS fragmentos de momentos DISTINTOS, cada uno a la medida de lo que se dice y con lo
que se dice. Las tomas vienen de scripts/brollscan.py (catalogo con sujeto, textos, palabras clave y si
sirve en ventana o en pantalla completa) y SOLO entran las que sirven (el sujeto es una pantalla,
interfaz, personaje, logo, titulo o producto; nunca una persona).

  1. UNIDADES (units): las lineas que se dicen de corrido ("La diferencia es esta" + "cuando entras a...";
     una corta que sigue la idea "Y dime si..."), sin pasar de UNIT_MAX s ni cruzar una seccion.
  2. RELEVANCIA (lexical_scores, deterministica): raices de 6 letras de la unidad (sin tildes, plurales ni
     palabras funcionales) contra las palabras clave, la descripcion, los textos y el sujeto de cada toma,
     pesadas por que tan raras son entre las tomas; los NOMBRES (glosario y textos cortos de las tomas:
     "nubis", "Agenda") pesan el doble y un titulo o logo cuyo texto se nombra va primero. Un logo o
     titulo SOLO cuenta por el nombre que dice (nunca por su descripcion: «nubes» no trae "un logo con
     nubes") y nunca es relleno: va cuando se nombra la marca, con otros logos y titulos detras.
  3. EL TEMA: una unidad es FUERTE si nombra algo que se ve en el video de apoyo (_topic) o si el LLM la
     eligio; entre dos fuertes (a lo mas BLOCK_GAP de distancia) las unidades siguen en el tema y pueden
     llevar apoyo aunque no lo nombren; fuera del tema (otro negocio, una anecdota) nunca.
  4. CUALES llevan apoyo (_select, programacion dinamica): la mayor relevancia sin mas de MAX_RUN unidades
     seguidas (despues una a cuadro), nunca el gancho (la primera, antes de HOOK_S) ni el cierre (la
     ultima), ni encima de una grabacion de pantalla o un recurso del guion (`busy`), hasta COVER_MAX del
     video. Una frase corta con apoyo ("Se llama Nubis.", 1 s) sigue sobre la siguiente hasta EXT s.
  5. FORMATOS ALTERNADOS: ventana flotante (la toma completa en una tarjeta) y pantalla completa (un
     recorte 9:16 por toma, centrado en su sujeto: brollscan.full_crop); la unidad que nombra el titulo va
     en el formato en que ese titulo sirve; despues se alterna. Si en el que toca no alcanzan las tomas
     (2 a 5, cubriendo COVER_MIN de la frase), el otro; si tampoco, la frase va a cuadro. Dos fragmentos
     PEGADOS (a menos de RUN_GAP) nunca van en el mismo formato: si el otro no alcanza, el segundo empieza
     RUN_GAP despues (la camara se ve entre los dos) o va a cuadro.
  6. MINI MONTAJE (_mini): de 2 a 5 tomas (PIECE ~1.7 s a pantalla completa, PIECE_WINDOW ~2.2 s en
     ventana; hasta PIECE_MAX) con cortes secos, del tamano EXACTO de la frase (de su primera palabra - PRE
     a su ultima + POST): cada toma desde su inicio (MARGIN despues de su corte) y nunca pasa del final de
     su toma (`max`) a la siguiente. Una toma no se repite en el video. El orden: el titulo nombrado
     primero; luego, con `pick` (el LLM local: ollama_picker), las que el elige entre las POOL primeras
     candidatas en su orden; sin el, las relevantes por la palabra de la frase que les corresponde (la
     toma de «Agenda» cuando se dice agenda) y el relleno de tomas del producto cercanas.

fit(pieces, L, fps) = motion/reel.fit: el tramo FINAL (el render recorta silencios) se reparte entre las
tomas: si sobra, cada una se acorta en proporcion desde su inicio; si falta, las ultimas se alargan hasta su
`max` y despues el ultimo cuadro se queda. El Studio usa el mismo (app/static/reelcard.js).

API
  line_spans(montage) -> [{"id", "text", "start", "end", "section"}]   frases del ensamble compuesto
  sentences(lines, words) -> lineas partidas en oraciones con las palabras del video compuesto
  units(lines) -> [{"lines", "text", "start", "end", "section"}]
  lexical_scores(lines, lib, names=()) -> {line_id: [[sid, i, score, palabra]]}
  place(lines, lib, *, match=None, busy=(), total=None, names=(), style=None, pick=None) -> items
  fit(pieces, L, fps=30) -> [[t0, t1, src_in, j]] (s locales del fragmento)
  pick_prompt(text, shots, k) / parse_pick(raw, n) / ollama_picker() -> pick(text, shots, k) | None
Item: {"id", "line", "lines", "text", "start", "end", "layout": "window"|"full", "sid", "name",
       "pieces": [{"shot", "in", "out", "max", "cx", "win", "why"}], "why"}  (start/end en s del video
       compuesto; in/out/max en s del archivo de apoyo; cx: centro del recorte 9:16; win: recorte de la
       ventana o None; why: por que, en espanol, para el Studio).
"""
from __future__ import annotations

import math
import re
import unicodedata

LAYOUTS = ("window", "full")
PRE, POST = 0.10, 0.20          # s antes de la primera palabra / despues de la ultima (la receta aprobada)
MIN_INSERT = 2.0                # s: una frase mas corta va a cuadro
HOOK_S = 1.5                    # s: la primera frase que empieza antes va a cuadro (el gancho)
COVER_MAX = 0.5                 # fraccion maxima del video con apoyo
MAX_RUN = 2                     # frases seguidas con apoyo como maximo
PIECE = 1.7                     # s: largo objetivo de cada toma del mini montaje (pantalla completa)
PIECE_WINDOW = 2.2              # s: ... en ventana flotante
POOL = 12                       # candidatas que ve el LLM al elegir las tomas de una frase
PIECE_MIN, PIECE_MAX = 0.9, 2.5
PIECES_MIN, PIECES_MAX = 2, 5
MARGIN = 0.08                   # s despues de un corte (y antes del siguiente) que no se usan
BUSY_PAD = 0.25                 # s de aire con una pantalla o un recurso del guion
GAP = 0.04                      # s entre dos fragmentos seguidos (nunca se enciman)
COVER_MIN = 0.85                # fraccion minima de la frase que cubre su fragmento
UNIT_MAX = 9.0                  # s: largo maximo de una unidad (lineas que se dicen de corrido)
UNIT_GAP = 0.9                  # s: pausa maxima entre dos lineas de una unidad
RUN_GAP = 1.0                   # s: dos fragmentos separados por menos son "seguidos"
EXT = 3.2                       # s: una frase corta con apoyo sigue sobre la siguiente hasta este largo
BLOCK_GAP = 3                   # frases sin nombrar el tema entre dos que si: siguen en el tema
TERMINAL = re.compile(r"[.!?…]['\"»”)]*\s*$")
CONT = re.compile(r"^\s*(?:y|e|o|u|ni|pero|no se)\b")
MIN_SCORE = 0.9                 # relevancia minima de una frase para llevar apoyo
BRAND_SUBJECTS = ("logo", "title")   # solo cuando se nombra la marca (o junto al titulo nombrado)
SUBJECT_BONUS = {"ui": 0.25, "screen": 0.25, "phone": 0.25, "character": 0.2, "logo": 0.1, "title": 0.1,
                 "product": 0.15}
SUBJECT_WORDS = {"screen": "pantalla monitor aplicacion app", "phone": "telefono celular aplicacion app",
                 "ui": "aplicacion app interfaz pantalla", "character": "personaje agente asistente",
                 "logo": "logo marca", "title": "titulo nombre", "product": "producto"}
STOP = frozenset("""a al algo algun alguna algunas alguno algunos ante antes aqui asi aun bien cada como con
contra cual cuales cuando de del desde donde dos el ella ellas ellos en entre era eres es esa esas ese eso
esos esta estaba estamos estan estar estas este esto estos fue fueron ha habia hace hacer han has hasta hay
he la las le les lo los mas me mi mis mucho muy nada ni no nos nosotros o otra otras otro otros para pero
poco por porque puede pueden que quien se sea ser si sin sobre solo son su sus tambien tan tanto te tener
tiene tienen tu tus un una uno unos unas usted ustedes va vamos van vez y ya yo the and for with you your
this that are from was have will can just its our out all llama llaman llamo llamas llamamos
persona personas gente alguien nadie cosa cosas mismo misma ahora entonces bueno pues claro""".split())


# ================================================================ texto
def _norm(s: str) -> str:
    t = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm(s))


def stem(w: str) -> str:
    """Raiz corta y sin plural: "computadoras" -> "comput", "nubes" -> "nub"."""
    w = _norm(w)
    if len(w) > 4 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s"):
        w = w[:-1]
    return w[:6]


def tokens(s: str) -> list[str]:
    """Raices de contenido de un texto (sin palabras funcionales, >= 3 letras)."""
    return [stem(w) for w in _words(s) if len(w) >= 3 and w not in STOP and not w.isdigit()]


def _num(v, d: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return d
    return f if math.isfinite(f) else d


# ================================================================ frases del ensamble compuesto
def line_spans(montage: dict) -> list[dict]:
    """Frases del guion con su tramo en el video COMPUESTO: cada segmento de plan.montage.timeline (con
    t0 = su inicio en el video compuesto, in/out en su fuente) aporta las palabras de su candidato
    (line_times, s de la fuente) -> [{"id", "text", "start", "end", "section"}] en orden de tiempo."""
    m = montage if isinstance(montage, dict) else {}
    sc = m.get("script") if isinstance(m.get("script"), dict) else {}
    lines = {L.get("id"): L for L in sc.get("lines") or [] if isinstance(L, dict)}
    sec = {}
    for s in sc.get("sections") or []:
        for lid in (s or {}).get("lines") or []:
            sec[lid] = (s or {}).get("id")
    cands = {(s.get("id"), c.get("id")): c for s in m.get("slots") or [] if isinstance(s, dict)
             for c in s.get("cands") or [] if isinstance(c, dict)}
    span: dict = {}
    for e in m.get("timeline") or []:
        if not isinstance(e, dict):
            continue
        a, b = _num(e.get("in"), None), _num(e.get("out"), None)
        t0 = e.get("t0") if e.get("t0") is not None else e.get("start")
        t0 = _num(t0, None)
        if a is None or b is None or t0 is None or b <= a:
            continue
        c = cands.get((e.get("slot"), e.get("cand"))) or {}
        lt = c.get("line_times") if c.get("sid") in (None, e.get("sid")) else None
        for lid, v in (lt or {}).items():
            try:
                v0, v1 = float(v[0]), float(v[1])
            except (TypeError, ValueError, IndexError):
                continue
            s0, s1 = min(max(v0, a), b), min(max(v1, a), b)
            if s1 - s0 <= 1e-6:
                continue
            x0, x1 = t0 + s0 - a, t0 + s1 - a
            cur = span.get(lid)
            span[lid] = [min(cur[0], x0), max(cur[1], x1)] if cur else [x0, x1]
    out = [{"id": lid, "text": str((lines.get(lid) or {}).get("text") or ""), "start": round(v[0], 3),
            "end": round(v[1], 3), "section": sec.get(lid)} for lid, v in span.items() if lid in lines]
    out.sort(key=lambda x: (x["start"], str(x["id"])))
    return out


# ================================================================ relevancia
def _shots(lib) -> list[tuple[dict, dict]]:
    """(fuente, toma) utilizables (ventana o pantalla completa) de todas las fuentes."""
    out = []
    for src in lib or []:
        if not isinstance(src, dict) or not src.get("sid"):
            continue
        for sh in src.get("shots") or []:
            if isinstance(sh, dict) and (sh.get("ok_window") or sh.get("ok_full")):
                out.append((src, sh))
    return out


def shot_words(sh: dict) -> list[str]:
    txt = " ".join([str(sh.get("what") or "")] + [str(t) for t in sh.get("tags") or []]
                   + [str(t) for t in sh.get("text") or []] + [SUBJECT_WORDS.get(sh.get("subject"), "")])
    return tokens(txt)


def _names(lib, names=()) -> set:
    """Nombres propios (raiz): los del glosario + los textos de una o dos palabras de las tomas."""
    out = set()
    for n in names or []:
        for w in _words(n):
            if len(w) >= 3 and w not in STOP:
                out.add(stem(w))
    for _src, sh in _shots(lib):
        for t in sh.get("text") or []:
            ws = _words(t)
            if 1 <= len(ws) <= 2:
                out.update(stem(w) for w in ws if len(w) >= 3 and w not in STOP and not w.isdigit())
    return out


def _topic(lib, names=()) -> set:
    """Nombres del TEMA del video de apoyo: los nombres (glosario y textos cortos de las tomas) que de
    verdad aparecen en sus tomas (texto, descripcion o palabras clave). El nombre de otro negocio en el glosario
    no es tema de un video de apoyo que no lo muestra."""
    seen = set()
    for _src, sh in _shots(lib):
        seen.update(shot_words(sh))
    return {n for n in _names(lib, names) if n in seen}


def lexical_scores(lines, lib, names=()) -> dict:
    """Relevancia deterministica -> {line_id: [[sid, i, score, palabra]]} (mayor primero). Cada raiz de
    la frase que aparece en la toma suma log(1 + N / (1 + df)) (rara entre tomas = pesa mas); un nombre
    propio x2; un titulo o logo cuyo texto se nombra en la frase +3 (va primero)."""
    pool = _shots(lib)
    if not pool:
        return {}
    sw = [set(shot_words(sh)) for _s, sh in pool]
    stxt = [set(stem(w) for t in (sh.get("text") or []) for w in _words(t) if len(w) >= 3) for _s, sh in pool]
    df: dict = {}
    for s in sw:
        for t in s:
            df[t] = df.get(t, 0) + 1
    N = len(pool)
    nm = _names(lib, names)
    out = {}
    for L in lines or []:
        toks = tokens(L.get("text") or "")
        if not toks:
            continue
        rows = []
        for (src, sh), words, txt in zip(pool, sw, stxt):
            sc, best = 0.0, ("", 0.0)
            brand = sh.get("subject") in BRAND_SUBJECTS
            for t in set(toks):
                if t not in words:
                    continue
                if brand and not (t in txt and t in nm):
                    # un logo o titulo SOLO va cuando se nombra lo que dice (la marca, el producto): nunca por
                    # una palabra de su descripcion («nubes» -> "un logo con nubes")
                    continue
                w = math.log(1.0 + N / (1.0 + df.get(t, 0)))
                if t in nm:
                    w *= 2.0
                sc += w
                if w > best[1]:
                    best = (t, w)
            if sh.get("subject") in ("logo", "title") and any(t in txt and t in nm for t in toks):
                sc += 3.0
            if sc > 0:
                rows.append([src["sid"], int(sh["i"]), round(sc, 3), best[0]])
        rows.sort(key=lambda r: -r[2])
        if rows:
            out[L["id"]] = rows
    return out


# ================================================================ LLM (opcional)
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def pick_prompt(text: str, shots: list[dict], k: int) -> tuple[str, dict]:
    """Prompt y esquema JSON para que el LLM local elija `k` tomas (letras) que acompanen la frase."""
    shots = shots[:len(_LETTERS)]
    opts = "\n".join(f"{_LETTERS[j]}) {str(sh.get('what') or SUBJECT_WORDS.get(sh.get('subject'), 'toma'))[:90]}"
                     + (f" (textos: {', '.join(str(t) for t in (sh.get('text') or [])[:3])})" if sh.get("text") else "")
                     for j, sh in enumerate(shots))
    prompt = (f"Frase que dice la persona: «{str(text or '')[:300]}»\n\nTomas de un video de apoyo:\n{opts}\n\n"
              f"Elige las {k} tomas que mejor muestran lo que dice la frase, en el orden en que deben aparecer "
              "mientras la dice. Responde SOLO JSON {\"tomas\": [\"C\", \"A\"]}.")
    schema = {"type": "object", "properties": {"tomas": {"type": "array", "items": {
        "type": "string", "enum": list(_LETTERS[:len(shots)])}}}, "required": ["tomas"]}
    return prompt, schema


def parse_pick(raw, n: int) -> list[int]:
    """Respuesta del LLM -> indices (sin repetir, dentro de las n candidatas)."""
    import json
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:  # noqa
        m = re.search(r"\{.*\}", str(raw or ""), re.S)
        try:
            d = json.loads(m.group(0)) if m else {}
        except Exception:  # noqa
            d = {}
    out = []
    for x in (d.get("tomas") if isinstance(d, dict) else None) or []:
        x = str(x).strip().upper()[:1]
        j = _LETTERS.find(x) if x else -1
        if 0 <= j < n and j not in out:
            out.append(j)
    return out


def ollama_picker(timeout: float = 20.0, model: str | None = None):
    """pick(texto, tomas, k) -> [indices] con el LLM local (ollama del motor) o None si no esta. Dos
    fallos seguidos lo apagan (el resto va con el orden deterministico)."""
    try:
        from motion import director as D
        if not D.llm_available(model):
            return None
    except Exception:  # noqa
        return None
    state = {"fails": 0}

    def pick(text, shots, k):
        if state["fails"] >= 2 or not shots or k <= 0:
            return None
        prompt, schema = pick_prompt(text, shots, k)
        try:
            raw = D._ollama_chat([{"role": "user", "content": prompt}], fmt=schema, model=model or D._llm_model(),
                                 num_ctx=4096, temperature=0.0, num_predict=80, timeout=timeout)
        except Exception:  # noqa
            state["fails"] += 1
            return None
        got = parse_pick(raw, min(len(shots), len(_LETTERS)))
        state["fails"] = 0 if got else state["fails"] + 1
        return got[:k]
    return pick


# ================================================================ colocacion
def _overlaps(a: float, b: float, spans, pad: float = 0.0) -> bool:
    return any(a < y + pad and x - pad < b for x, y in spans or [])


def _avail(sh: dict) -> tuple[float, float]:
    """[in, max] utilizable de una toma: MARGIN despues de su corte y antes del siguiente."""
    a, b = _num(sh.get("a")), _num(sh.get("b"))
    a += MARGIN if sh.get("cut_a", True) else 0.0
    b -= MARGIN if sh.get("cut_b", True) else 0.0
    return a, max(a, b)


def _base_of(src: dict, sh: dict) -> tuple:
    """Toma "real" (entre cortes) de una toma o momento: los momentos de una toma larga la comparten."""
    shots = {int(x.get("i", -1)): x for x in src.get("shots") or [] if isinstance(x, dict)}
    i = int(sh["i"])
    while i - 1 in shots and not shots[i].get("cut_a", True):
        i -= 1
    return (src.get("sid"), i)


def _word_pos(line_text: str, word: str) -> float:
    """Posicion relativa (0..1) de la primera palabra de la frase con raiz `word` (0.5 si no esta)."""
    ws = [stem(w) for w in _words(line_text)]
    if word and word in ws and len(ws) > 1:
        return ws.index(word) / (len(ws) - 1)
    return 0.5


def _why_shot(sh: dict, word: str, line_text: str, named: bool) -> str:
    what = str(sh.get("what") or "").strip().rstrip(".")
    if named:
        t = next((str(x) for x in sh.get("text") or [] if word and word in tokens(x)), "")
        return f"Dices «{_orig_word(line_text, word)}»: va {('«' + t + '»') if t else 'el título'} del video de apoyo"
    if word == "*":
        return "Muestra lo que dices" + (f": {what[:1].lower() + what[1:]}" if what else "")
    if word:
        return f"Dices «{_orig_word(line_text, word)}»" + (f": {what[:1].lower() + what[1:]}" if what else "")
    return (what[:1].upper() + what[1:]) if what else "Toma del producto"


def _orig_word(text: str, st: str) -> str:
    for w in re.findall(r"[\w’']+", str(text or "")):
        if stem(w) == st:
            return w
    return st


def sentences(lines, words) -> list[dict]:
    """Las frases del guion partidas en ORACIONES con las palabras del video compuesto (`words`: [{text,
    start, end}] en el mismo tiempo): una linea detectada puede traer dos oraciones ("...para tu negocio. Se
    llama Nubis."); cada una queda como linea propia (id, id.2, id.3...) con sus tiempos. Sin palabras, igual."""
    ws = sorted([w for w in words or [] if isinstance(w, dict) and str(w.get("text") or "").strip()],
                key=lambda w: _num(w.get("start")))
    if not ws:
        return list(lines or [])
    out = []
    for L in lines or []:
        a, b = _num(L.get("start")), _num(L.get("end"))
        inside = [w for w in ws if a - 0.05 <= (_num(w.get("start")) + _num(w.get("end"))) / 2 <= b + 0.05]
        groups, cur = [], []
        for w in inside:
            cur.append(w)
            if TERMINAL.search(str(w.get("text") or "")) and len(cur) >= 2:
                groups.append(cur)
                cur = []
        if cur:
            if groups and len(cur) < 2:
                groups[-1] += cur
            else:
                groups.append(cur)
        if len(groups) <= 1:
            out.append(L)
            continue
        for k, g in enumerate(groups):
            out.append({"id": L.get("id") if k == 0 else f"{L.get('id')}.{k + 1}",
                        "text": " ".join(str(w.get("text") or "").strip() for w in g),
                        "start": round(_num(g[0].get("start")), 3), "end": round(_num(g[-1].get("end")), 3),
                        "section": L.get("section")})
    return out


def units(lines) -> list[dict]:
    """Frases -> UNIDADES (lo que se dice de corrido): una linea se une a la anterior de su seccion (sin
    pausa de mas de UNIT_GAP s y sin pasar de UNIT_MAX s) si la anterior no cierra la oracion ("La
    diferencia es esta" + "cuando entras a...") o si es corta y sigue la idea ("Y dime si...", "No se,
    ¿aprender frances?"). -> [{"lines", "text", "start", "end", "section"}]."""
    ls = sorted([L for L in lines or [] if isinstance(L, dict)], key=lambda L: _num(L.get("start")))
    out = []
    for L in ls:
        a, b, txt = _num(L.get("start")), _num(L.get("end")), str(L.get("text") or "")
        cur = out[-1] if out else None
        if cur is not None and L.get("section") == cur["section"] and a - cur["end"] < UNIT_GAP \
                and b - cur["start"] <= UNIT_MAX and (
                    not TERMINAL.search(cur["last"])
                    or (b - a < MIN_INSERT and CONT.match(_norm(txt)) is not None)):
            cur["lines"].append(L.get("id"))
            cur["text"] = (cur["text"] + " " + txt).strip()
            cur["end"], cur["last"] = b, txt
            continue
        out.append({"lines": [L.get("id")], "text": txt, "start": a, "end": b, "section": L.get("section"),
                    "last": txt})
    for u in out:
        u.pop("last", None)
    return out


def _unit_rows(u: dict, match: dict, by_key: dict) -> list:
    """Relevancia de una unidad: las de sus lineas sumadas por toma (la palabra de la mas alta)."""
    acc: dict = {}
    for lid in u["lines"]:
        for r in match.get(lid) or []:
            k = (r[0], int(r[1]))
            if k not in by_key:
                continue
            cur = acc.get(k)
            if cur is None:
                acc[k] = [r[0], int(r[1]), float(r[2]), r[3] if len(r) > 3 else ""]
            else:
                cur[2] += float(r[2])
                if not cur[3] and len(r) > 3:
                    cur[3] = r[3]
    rows = sorted(acc.values(), key=lambda r: -r[2])
    return [[r[0], r[1], round(r[2], 3), r[3]] for r in rows]


def _select(cands: list[dict]) -> set:
    """Unidades con apoyo: maximo de relevancia sin mas de MAX_RUN seguidas (las separadas por una pausa
    de mas de RUN_GAP s o por una a cuadro no cuentan como seguidas). Programacion dinamica."""
    n = len(cands)
    if n == 0:
        return set()
    NEG = -1e18
    dp = [[NEG] * (MAX_RUN + 1) for _ in range(n + 1)]
    back: list = [[None] * (MAX_RUN + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i, c in enumerate(cands):
        joined = i > 0 and c["a"] - cands[i - 1]["b"] < RUN_GAP
        for r in range(MAX_RUN + 1):
            v = dp[i][r]
            if v <= NEG / 2:
                continue
            if v > dp[i + 1][0]:                       # a cuadro
                dp[i + 1][0], back[i + 1][0] = v, (r, False)
            if c["ok"]:
                nr = (r if joined else 0) + 1
                if nr <= MAX_RUN and v + c["v"] > dp[i + 1][nr]:
                    dp[i + 1][nr], back[i + 1][nr] = v + c["v"], (r, True)
    r = max(range(MAX_RUN + 1), key=lambda q: dp[n][q])
    chosen = set()
    for i in range(n, 0, -1):
        pr, took = back[i][r]
        if took:
            chosen.add(i - 1)
        r = pr
    return chosen


def place(lines, lib, *, match=None, busy=(), total=None, names=(), style=None, pick=None) -> list[dict]:
    """Fragmentos de apoyo del video (ver el docstring del modulo). `lines`: line_spans(); `lib`: los
    catalogos de brollscan con su "sid"; `match`: relevancia por linea (llm/lexical; sin ella,
    lexical_scores); `busy`: [(a, b)] ocupados (grabaciones de pantalla, recursos del guion); `total`:
    duracion del video."""
    pool = _shots(lib)
    if not pool or not lines:
        return []
    by_key = {(src["sid"], int(sh["i"])): (src, sh) for src, sh in pool}
    match_given = isinstance(match, dict) and bool(match)
    match = match if match_given else lexical_scores(lines, lib, names)
    nm = _names(lib, names)
    topic = _topic(lib, names)
    total = _num(total) or max(_num(L.get("end")) for L in lines)
    us = units(lines)
    cands = []
    for k, u in enumerate(us):
        a, b = u["start"] - PRE, u["end"] + POST
        if k + 1 < len(us):
            b = min(b, us[k + 1]["start"] - GAP)
        if k > 0:
            a = max(a, us[k - 1]["end"] + GAP)
        a, b = max(0.0, a), min(total, b)
        rows = _unit_rows(u, match, by_key)
        named = any(by_key[(r[0], r[1])][1].get("subject") in ("logo", "title") and r[3] in nm for r in rows)
        ext = None
        if b - a < MIN_INSERT and k + 1 < len(us) and (named or rows):
            # una frase corta con apoyo ("Se llama Nubis.") sigue sobre el inicio de la siguiente
            ext = min(a + max(MIN_INSERT, EXT), us[k + 1]["end"] + POST, total)
        L = (ext or b) - a
        # FUERTE: la IA eligio tomas para esta frase o la frase nombra algo que se ve en el video de apoyo
        strong = any(r[3] == "*" or r[3] in topic for r in rows)
        ok = (L >= MIN_INSERT - 1e-6 and not (k == 0 and u["start"] < HOOK_S) and k != len(us) - 1
              and not _overlaps(a, ext or b, busy, BUSY_PAD))
        v = sum(r[2] for r in rows[:3]) + (3.0 if named else 0.0) + (3.0 if strong else 0.0)
        cands.append({"k": k, "u": u, "a": a, "b": ext or b, "rows": rows, "v": v, "ok": ok, "named": named,
                      "strong": strong})
    # el TEMA: de una frase fuerte a la siguiente (a lo mas BLOCK_GAP frases entre ellas); las de en medio
    # pueden llevar apoyo aunque no lo nombren (siguen hablando de lo mismo); fuera del tema, nada
    strong_k = [c["k"] for c in cands if c["strong"]]
    for c in cands:
        inside = any(i < c["k"] < j and j - i <= BLOCK_GAP + 1 for i, j in zip(strong_k, strong_k[1:]))
        c["ok"] = c["ok"] and (c["strong"] or (inside and (bool(c["rows"]) or not match_given)))
        if not c["strong"]:
            c["v"] += 1.0 if inside else 0.0
    chosen = _select(cands)
    # tope de cobertura: se quitan las de menos relevancia por segundo
    sel = [c for c in cands if c["k"] in chosen]
    while sel and sum(c["b"] - c["a"] for c in sel) > COVER_MAX * total + 1e-6:
        sel.remove(min(sel, key=lambda c: c["v"] / max(0.1, c["b"] - c["a"])))
    sel.sort(key=lambda c: c["a"])
    used: set = set()
    items = []
    prev_layout = None
    t_free = 0.0
    for c in sel:
        u = c["u"]
        a = max(c["a"], t_free)
        if c["b"] - a < MIN_INSERT - 1e-6:
            continue
        cc = dict(c, a=a)
        prefer = "window" if prev_layout in (None, "full") else "full"
        if c["named"]:
            # la frase nombra el titulo del video: el formato donde ese titulo se puede usar (pantalla
            # completa si se puede)
            nsh = [by_key[(r[0], r[1])][1] for r in c["rows"] if by_key[(r[0], r[1])][1].get("subject") in ("logo", "title")
                   and r[3] in nm and (r[0], r[1]) not in used]
            if nsh:
                prefer = "full" if any(sh.get("ok_full") for sh in nsh) else "window"
        best = None
        # pegado al fragmento anterior (a menos de RUN_GAP): SIEMPRE en el otro formato (dos ventanas seguidas
        # se ven como una sola de 10 s); si en ese no alcanzan las tomas, empieza RUN_GAP despues (la camara
        # se ve entre los dos) o la frase va a cuadro
        adjacent = prev_layout is not None and a - (t_free - GAP) < RUN_GAP
        tries = [(lay, cc) for lay in (prefer, "window" if prefer == "full" else "full")
                 if not (adjacent and lay == prev_layout)]
        if adjacent:
            a2 = t_free - GAP + RUN_GAP
            if c["b"] - a2 >= MIN_INSERT - 1e-6:
                tries += [(lay, dict(c, a=a2)) for lay in (prefer, "window" if prefer == "full" else "full")]
        for lay, cx in tries:
            got = _mini(cx, lay, by_key, used, nm, u["text"], pick)
            # 2 a 5 tomas y casi toda la frase (si no, en el otro formato o la frase va a cuadro)
            if got and len(got[0]) >= PIECES_MIN and got[1] >= COVER_MIN * (cx["b"] - cx["a"]):
                best = (lay, got)
                a = cx["a"]
                break
        if best is None:
            continue
        lay, (pieces, length, sid) = best
        for p in pieces:
            used.add((sid, p["shot"]))
            p.pop("_base", None)
        src = next(s for s in lib if s.get("sid") == sid)
        n = len(pieces)
        fmt = "pantalla completa" if lay == "full" else "ventana flotante"
        items.append({"id": f"br{len(items) + 1:02d}", "line": u["lines"][0], "lines": list(u["lines"]),
                      "text": u["text"][:240], "start": round(a, 3), "end": round(a + length, 3),
                      "layout": lay, "sid": sid, "name": src.get("name"), "pieces": pieces,
                      "why": f"Mientras dices «{_short(u['text'])}»: {n} {'toma' if n == 1 else 'tomas'} "
                             f"de {src.get('name') or 'tu video de apoyo'} en {fmt}"})
        prev_layout = lay
        t_free = a + length + GAP
    return items


def _short(t, n: int = 60) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def _ok(sh: dict, layout: str) -> bool:
    return bool(sh.get("ok_full" if layout == "full" else "ok_window"))


def _mini(c: dict, layout: str, by_key: dict, used: set, nm: set, text: str, pick=None):
    """Mini montaje de un fragmento en `layout`: (pedazos, largo, sid) o None. Una sola fuente por
    fragmento (la de la toma mas relevante con tomas en ese formato). Candidatas en orden: el titulo o
    logo que la frase nombra, las relevantes y el relleno (tomas del producto aun sin usar, de otra toma y
    cerca de la mas relevante en el video de apoyo). `pick(texto, [toma], k)` (LLM local, opcional) elige
    y ordena k de las primeras POOL candidatas; lo que no elija (o si falla) lo completa el orden de
    arriba. El titulo nombrado va siempre primero."""
    L = c["b"] - c["a"]
    # la ventana va con menos tomas y mas largas (se leen dentro de la tarjeta y son menos)
    k = max(PIECES_MIN, min(PIECES_MAX, int(round(L / (PIECE_WINDOW if layout == "window" else PIECE)))))
    rows = [r for r in c["rows"] if _ok(by_key[(r[0], int(r[1]))][1], layout) and (r[0], int(r[1])) not in used]
    if rows:
        sid = rows[0][0]
        src0 = by_key[(rows[0][0], int(rows[0][1]))][0]
    else:
        # sigue en el tema pero no nombra nada: tomas del producto de la fuente con mas tomas libres
        free: dict = {}
        for (sd, i), (src, sh) in by_key.items():
            if _ok(sh, layout) and (sd, i) not in used:
                free.setdefault(sd, [src, 0])[1] += 1
        if not free:
            return None
        sid, (src0, _n) = max(free.items(), key=lambda kv: kv[1][1])
    # ---- candidatas en orden
    order: list = []                         # [(src, sh, score, word)]

    def add(src, sh, score, word):
        if all(o[1] is not sh for o in order):
            a, mx = _avail(sh)
            if mx - a >= PIECE_MIN * 0.6:
                order.append((src, sh, score, word))
    named = [r for r in rows if r[0] == sid and by_key[(r[0], int(r[1]))][1].get("subject") in ("logo", "title")
             and r[3] in nm]
    for r in named + [r for r in rows if r[0] == sid and r not in named]:
        src, sh = by_key[(r[0], int(r[1]))]
        add(src, sh, r[2], r[3])
    n_named = sum(1 for o in order if o[1].get("subject") in ("logo", "title") and o[3] in nm)
    t_ref = _num(order[0][1].get("a")) if order else 0.0
    lead = bool(n_named)
    bases0 = {_base_of(src0, o[1]) for o in order[:k]}
    # el relleno nunca lleva un logo o titulo (son de cuando se nombra la marca), salvo junto al titulo
    # nombrado: ahi van SOLO logos y titulos (v6: «Se llama X» = el titulo de cierre + el logo) y otras
    # tomas solo si faltan para PIECES_MIN
    fill = [sh for sh in src0.get("shots") or [] if isinstance(sh, dict) and _ok(sh, layout)
            and (sid, int(sh["i"])) not in used and (lead or sh.get("subject") not in BRAND_SUBJECTS)]
    if lead:
        brand_fill = [sh for sh in fill if sh.get("subject") in BRAND_SUBJECTS]
        if n_named + len(brand_fill) >= PIECES_MIN:
            fill = brand_fill
            k = min(k, n_named + len(brand_fill))
    # de otra toma (no otro momento de la misma) salvo que siga al titulo con otro titulo o logo
    fill.sort(key=lambda sh: (0 if lead and sh.get("subject") in ("logo", "title") else 1,
                              1 if _base_of(src0, sh) in bases0 else 0,
                              -SUBJECT_BONUS.get(sh.get("subject"), 0.0), abs(_num(sh.get("a")) - t_ref)))
    for sh in fill:
        add(src0, sh, 0.0, "")
    if not order:
        return None
    # ---- eleccion: el titulo nombrado + (LLM | orden) hasta k tomas y hasta llenar la frase
    chosen = order[:n_named]
    rest = order[n_named:]
    if callable(pick) and rest and k > len(chosen):
        try:
            got = pick(text, [o[1] for o in rest[:POOL]], k - len(chosen))
        except Exception:  # noqa: el LLM es una mejora; sin el, el orden de arriba
            got = None
        seen = set()
        for j in got or []:
            if isinstance(j, int) and 0 <= j < min(POOL, len(rest)) and j not in seen:
                seen.add(j)
                o = rest[j]
                chosen.append((o[0], o[1], o[2], o[3] or "*"))
        rest = [o for j, o in enumerate(rest) if j not in seen]

    def short():
        return sum(min(PIECE_MAX, _avail(o[1])[1] - _avail(o[1])[0]) for o in chosen) < L - 1e-6
    for o in rest:
        if len(chosen) >= k and not (short() and len(chosen) < PIECES_MAX):
            break
        chosen.append(o)
    chosen = chosen[:PIECES_MAX]
    # orden: el titulo nombrado primero; lo elegido por el LLM en su orden; lo demas por la palabra de la
    # frase que le corresponde
    first = chosen[:n_named]
    others = chosen[n_named:]
    if not any(o[3] == "*" for o in others):
        pos = {id(o): k for k, o in enumerate(others)}
        others.sort(key=lambda o: (_word_pos(text, o[3]) if o[3] else 0.5, pos[id(o)]))
    chosen = first + others
    # duraciones: parejas, sin pasar del final de cada toma; lo que no cabe se reparte
    av = [_avail(o[1]) for o in chosen]
    durs = _split(L, [mx - a for a, mx in av])
    length = sum(durs)
    pieces = []
    for (src, sh, sc, word), (a, mx), d in zip(chosen, av, durs):
        if d <= 1e-3:
            continue
        pieces.append({"shot": int(sh["i"]), "in": round(a, 3), "out": round(a + d, 3), "max": round(mx, 3),
                       "cx": shot_cx(sh), "win": sh.get("win_crop"),
                       "why": _why_shot(sh, word, text, bool(word and word in nm and sh.get("subject") in ("logo", "title"))),
                       "_base": _base_of(src, sh)})
    return pieces, round(length, 3), sid


def shot_cx(sh: dict) -> float | None:
    """Centro del recorte de pantalla completa de una toma: el del catalogo compacto (`cx`, lo que viaja en
    el plan) o el del catalogo completo (`full.cx`)."""
    v = sh.get("cx")
    if v is None:
        v = (sh.get("full") or {}).get("cx")
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _split(L: float, avail: list[float]) -> list[float]:
    """Reparte L s entre pedazos con `avail` s disponibles cada uno: parejo, hasta PIECE_MAX cada uno
    (si asi no alcanza, hasta lo disponible); si ni asi alcanza, el total es la suma disponible. En
    cuadros enteros a 30 cps (el render ajusta al tramo final)."""
    n = len(avail)
    if n == 0:
        return []
    cap = [min(PIECE_MAX, max(0.0, a)) for a in avail]
    if sum(cap) < L - 1e-6:
        cap = [max(0.0, a) for a in avail]
    durs = [0.0] * n
    left = L
    open_ = [j for j in range(n) if cap[j] > 1e-6]
    while left > 1e-6 and open_:
        share = left / len(open_)
        nxt = []
        for j in open_:
            add = min(share, cap[j] - durs[j])
            durs[j] += add
            left -= add
            if cap[j] - durs[j] > 1e-6:
                nxt.append(j)
        open_ = nxt
    return [math.floor(d * 30 + 1e-6) / 30 for d in durs]


def fit(pieces, L: float, fps: float = 30.0) -> list[list[float]]:
    """Pedazos (in, out, max en s del archivo) -> [[t0, t1, src_in]] en s LOCALES de un tramo de L s, en
    cuadros enteros: si la suma pasa de L cada pedazo se acorta en proporcion (desde su inicio); si no
    alcanza, los ultimos se alargan hasta su `max`; despues, el ultimo se queda en su cuadro final.
    (= motion/reel.fit, el que usa el render.)"""
    from motion import reel
    return reel.fit(pieces, L, fps)
