#!/usr/bin/env python3
"""
Limpieza de tomas: retomas, arranques en falso, autocorrecciones, tartamudeos y errores.

Un creador que graba de corrido repite frases hasta que le salen bien, deja oraciones a medias,
se corrige ("son 19, no, 16") y se traba. Un editor quita los intentos malos y deja la ULTIMA
toma completa. Este modulo lo hace sobre las palabras de la transcripcion (tiempos de la FUENTE),
con reglas deterministas y generales del espanol hablado (sin frases propias de ningun video):

  R1 reinicio   : un punto de arranque j repite (difuso) el inicio de una toma anterior i dentro de
                  una ventana corta y la toma vieja [i, j) queda casi cubierta por la nueva -> se
                  queda la ultima. Cubre retomas completas, retomas de cola (solo se rehace el final
                  de la frase: se corta solo ese final), cadenas de intentos y n-gramas repetidos.
  R2 fragmento  : fragmento corto que queda colgado ("no lo,", "es que,") + pausa + reinicio con la
                  misma palabra.
  R2b muerto    : segundo intento corto que repite la entrada de una toma previa y muere colgado.
  R3 abandonada : oracion que Whisper cierra con "..." sobre una palabra colgante (articulo,
                  preposicion, intensificador, copula) -> se quita la oracion inconclusa.
  R4 correccion : "X, no, Y" / "perdon" / "mejor dicho" / ", digo," con un elemento anterior del
                  mismo tipo (numero por numero, palabra por palabra) -> se quita X + marcador.
  R5 error      : groserias o comentarios meta ("me equivoque", "va de nuevo"); solo se cortan solos
                  dentro de una cadena de retomas (aislados quedan como sugerencia).
  R6 tartamudeo : repeticion inmediata de una palabra funcional sin puntuacion ("de de", "el el").
  R7 reformula  : (nivel baja) oracion reformulada tras una pausa larga.
  R8 voz perdida: (nivel baja, con audio) palabra colgante + voz que Whisper no transcribio + pausa:
                  Whisper suele tragarse un intento repetido.
Vetos: anafora/listas ("es moderna, es versatil, es elegante", "uno, dos, tres") y enfasis
("muy, muy bueno", "no, no, no", "asi es, asi es").

Bordes del corte: [inicio de la primera palabra quitada, inicio de la primera palabra que se
queda) con el aire de edit.GAP_PAD_OUT/IN, y con el audio cada borde se pega a la ventana de
20 ms de menor energia a +-150 ms (los tiempos de Whisper derivan). Una palabra estirada
(> 1.2 s: Whisper se traga las pausas) se acota para que ningun corte se coma una palabra que se
queda; al final se verifica que ninguna palabra conservada tenga su punto medio dentro de un corte
de tomas (ni se pierda como fragmento < timeline.MIN_FRAG entre dos cortes).

Contrato (plan['cuts'], tiempos de la FUENTE; registros APARTE de los silencios, pueden
traslaparse: Timeline.keeps_from_cuts funde los habilitados):
  {"id": "tk_<hash>", "start", "end", "enabled", "src": "takes", "kind", "conf", "level",
   "reason", "text", "kept_text", "keep_ref": {"start", "end"} | None, "group": "tg_<n>",
   "w0", "w1", "user"?, "edges"?, "guard"?}
  kind: retoma | arranque_fallido | autocorreccion | tartamudeo | error | digresion
  level: alta (habilitado) | media (habilitado salvo en 'suave') | baja (sugerencia, apagado
  salvo en 'a_fondo'). `user: true` = el usuario lo toco: las re-ejecuciones lo respetan;
  `edges: true` = el usuario movio sus bordes (tambien se respeta). `guard: true` = apagado por
  seguridad (dejaria una palabra en un fragmento < MIN_FRAG, o es mas corto que MIN_CUT): ningun
  nivel lo vuelve a encender, solo el usuario.
  NUNCA src "ai": director.propose_trims no propone nada si hay cortes "ai".
plan['takes'] = {"version": 1, "sensitivity", "topic", "summary", "sections": [{start, title}],
                 "stats": {n, enabled, removed_s, suggested, by_kind: {kind: {n, s}}}, "llm", "ms"}

API:
  detect(words, dur, *, sensitivity="normal", audio=None, existing_cuts=None) -> {"cuts", "meta"}
  merge(old_cuts, take_cuts) -> cuts   (reemplaza los src "takes" salvo los user/edges; idempotente)
  apply_sensitivity(cuts, sensitivity) -> cuts ; stats(cuts) -> dict
  effective_words(words, cuts) -> palabras que se quedan ; plan_words(plan) -> palabras del plan
  topic(words, timeout) -> {"topic", "summary", "sections"} | None  (LLM local; None sin LLM)
Solo biblioteca estandar (numpy solo para el ajuste al audio, importado al usarlo).
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field

KINDS = ("retoma", "arranque_fallido", "autocorreccion", "tartamudeo", "error", "digresion")
LABELS = {"retoma": "Toma repetida", "arranque_fallido": "Arranque en falso",
          "autocorreccion": "Corrección", "tartamudeo": "Tartamudeo", "error": "Error",
          "digresion": "Fuera de tema", "silencio": "Silencio"}
LEVELS = ("alta", "media", "baja")
SENSITIVITIES = ("suave", "normal", "a_fondo")
SENS_ALIASES = {"conservador": "suave", "suave": "suave", "normal": "normal", "agresivo": "a_fondo",
                "intensa": "a_fondo", "a_fondo": "a_fondo", "a fondo": "a_fondo"}
# categoria interna (reglas) -> kind del contrato
_KIND = {"retake": "retoma", "false_start": "arranque_fallido", "self_correction": "autocorreccion",
         "stutter_repeat": "tartamudeo", "blooper": "error"}
_LEVEL = {"high": "alta", "medium": "media", "low": "baja"}

STRETCH_MAX = 1.2       # s: una palabra mas larga se trago una pausa (Whisper crudo)
SNAP_WIN = 0.15         # s: el borde busca la ventana de menor energia a +- esto
SNAP_FRAME = 0.02       # s: ventana de energia
SNAP_HOP = 0.01         # s: paso entre ventanas
QUIET_DB = -10.0        # dB bajo la voz: un borde que se mete en una palabra conservada solo si es silencio
GAP_DB = -20.0          # dB bajo la voz: hueco real entre palabras (no el cierre de una consonante)...
MIN_GAP = 0.05          # ...de al menos esto (s)
ONSET_BACK = 0.45       # s: lo mas que un inicio de Whisper llega tarde respecto a la voz
PAUSE_MIN = 0.25        # s: pausa entre dos tomas (no el hueco entre dos palabras)
LOST_VOICE = 0.6        # s: voz sin transcribir antes de la toma buena = su arranque que Whisper se trago
MIN_CUT = 0.04          # s: un corte mas corto no vale la pena
TEXT_MAX = 300          # caracteres del texto quitado que se guardan en el corte
KEPT_WORDS = 10         # palabras de la toma que se queda (kept_text)
TOPIC_TIMEOUT = 35.0    # s: tope del tema con el LLM (el analisis nunca espera mas de ~40 s)
TOPIC_WORDS = 16        # palabras por oracion en el prompt del tema
TOPIC_MAX_WORDS = 1800  # palabras totales del prompt (cabe en 4096 de contexto)


def _gap_pads():
    try:
        import edit as E
        return float(E.GAP_PAD_OUT), float(E.GAP_PAD_IN)
    except Exception:  # noqa: mismo aire que los silencios (edit.py)
        return 0.10, 0.08


GAP_PAD_OUT, GAP_PAD_IN = _gap_pads()

# ----------------------------------------------------------------------------- lexicos
STOP = set("""
a al ante bajo con contra de del desde durante en entre hacia hasta mediante para por segun sin so
sobre tras versus via el la lo los las un una unos unas y e o u ni que pero sino porque pues como
si cuando donde aunque mientras yo tu el ella usted nosotros ellos ellas ustedes me te se nos os le
les mi mis tu tus su sus nuestro nuestra nuestros nuestras este esta esto estos estas ese esa eso
esos esas aquel aquella aquello aquellos aquellas es son era eran fue fueron ser sera seria ha han
he has hay habia haber esta estan estoy estas estar estaba va van vas voy vamos ir iba puede pueden
puedo tiene tienen tengo tener hace hacen hago muy mas menos tan tanto bastante algo nada todo toda
todos todas ya no si tambien asi aqui ahi alli hoy bien mal solo cual cuales quien quienes cuyo
eso ok bueno entonces obviamente digamos pues este eh em mm
""".split())

FILLERS = {"pues", "eh", "em", "ehm", "mm", "mmm", "ah", "ay", "eeh", "emm"}
LEAD_STRIP = {"y", "e", "o", "pues", "bueno", "entonces", "eh", "em", "ah", "ay", "ok", "okay",
              "vale", "este", "ahora", "so"}

DANGLING = set("""
a al ante con contra de del desde en entre hacia hasta para por sin sobre tras el la lo los las un
una unos unas y e o u ni que pero porque pues como si cuando donde aunque mientras mi mis tu tus su
sus nuestro nuestra este esta estos estas ese esa esos esas cada muy mas menos tan bastante demasiado es
son era fue ser sera esta estan estar va van vas voy vamos hay ha han he me te se le les nos cual
cuyo cuya
""".split())

# conectores que NUNCA son falsos arranques aunque terminen "colgados"
CONNECTORS = [("es", "decir"), ("o", "sea"), ("por", "ejemplo"), ("sin", "embargo"),
              ("por", "lo", "tanto"), ("asi", "que"), ("ademas",), ("incluso",), ("en", "fin"),
              ("por", "otra", "parte"), ("por", "otro", "lado"), ("es", "que")]

# enfasis: repetir UNA de estas palabras es intencional ("muy, muy", "no, no, no")
EMPHASIS = {"muy", "mas", "no", "si", "nunca", "siempre", "mucho", "mucha", "bien", "super",
            "claro", "nada", "todo", "rapido", "poco", "ya", "otra", "cada", "dale", "vamos",
            "arriba", "fuerte", "ojo", "cuidado", "gracias", "hola", "adios", "bye", "jaja", "ja"}

# repeticion inmediata de estas palabras funcionales sin puntuacion = tartamudeo
STUTTER_FN = {"el", "la", "los", "las", "un", "una", "de", "del", "en", "con", "por", "para", "que",
              "y", "a", "al", "lo", "le", "se", "es", "me", "mi", "su", "tu", "este", "esta", "yo"}

EXPLETIVES = [("puta", "madre"), ("puta",), ("chingada",), ("chingado",), ("carajo",),
              ("mierda",), ("no", "mames"), ("joder",), ("verga",), ("chin",), ("coño",),
              ("me", "lleva"), ("rayos",), ("demonios",), ("maldita", "sea")]
META = [("me", "equivoque"), ("me", "trabe"), ("me", "equivoco"),
        ("la", "regue"), ("corte",), ("corta",), ("empiezo", "de", "nuevo"), ("va", "de", "nuevo"),
        ("repito",), ("perdon",), ("perdona",), ("perdoname",), ("ups",)]
CORR_MARKERS = [("mejor", "dicho"), ("perdon",), ("perdona",), ("digo",), ("quise", "decir"),
                ("mas", "bien"), ("corrijo",), ("no",)]

NUMWORDS = set("""cero uno una dos tres cuatro cinco seis siete ocho nueve diez once doce trece
catorce quince dieciseis veinte treinta cuarenta cincuenta sesenta setenta ochenta noventa cien
ciento mil millon millones medio media primero primera segundo segunda tercero tercera""".split())
_NUMRE = re.compile(r"(\d+([.,]\d+)?|dieci\w+|veinti\w+|\w+cient[oa]s|quinient[oa]s|"
                    r"setecient[oa]s|novecient[oa]s|millon(es)?|mil)")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9ñ]", "", strip_accents(str(s).lower().replace("ñ", "\x00")).replace("\x00", "ñ"))


def stem(n: str) -> str:
    """Raiz ligera: iguala genero/numero y 3a persona sing/plural (tomate(s), cocido/a,
    hace/hacen). Numeros intactos."""
    if n.isdigit() or len(n) <= 3:
        return n
    if n[-1] in "sn" and len(n) > 4:
        n = n[:-1]
    if n[-1] in "aeo" and len(n) > 3:
        n = n[:-1]
    return n


def is_num(n: str) -> bool:
    return n in NUMWORDS or bool(_NUMRE.fullmatch(n))


def fmt_time(s: float) -> str:
    """Segundos -> 'm:ss' (o 'h:mm:ss')."""
    s = max(0, int(round(float(s))))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


# ----------------------------------------------------------------------------- tokens
@dataclass
class Tok:
    i: int
    wi: int         # indice en la lista de palabras de entrada
    raw: str
    n: str          # normalizado
    s: str          # raiz
    start: float
    end: float
    punct: str      # '', 'comma', 'stop', 'ellipsis'
    lead_q: bool    # empieza con ¿ o ¡
    filler: bool
    content: bool
    pause_before: float = 0.0


def _punct(raw: str) -> str:
    r = raw.rstrip()
    if r.endswith("...") or r.endswith("…"):
        return "ellipsis"
    if r[-1:] in ".?!":
        return "stop"
    if r[-1:] in ",;:":
        return "comma"
    return ""


def _valid_words(words) -> list[tuple[int, dict]]:
    out = []
    for k, w in enumerate(words or []):
        if not isinstance(w, dict):
            continue
        try:
            a, b = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b < a:
            b = a
        out.append((k, {"text": str(w.get("text", "")), "start": a, "end": b}))
    out.sort(key=lambda kw: (kw[1]["start"], kw[1]["end"], kw[0]))
    return out


def _excess(t: Tok) -> float:
    """Segundos de mas de una palabra estirada (lo que dura sobre lo esperable por sus letras)."""
    est = 0.18 + 0.075 * len(t.n)
    return max(0.0, (t.end - t.start) - est - 0.25)


def tokenize(words: list[dict]) -> list[Tok]:
    toks: list[Tok] = []
    for wi, w in _valid_words(words):
        raw = w["text"].strip()
        n = norm(raw)
        if not n:
            continue
        p = _punct(raw)
        filler = n in FILLERS or (n in {"este", "bueno"} and p == "comma")
        content = (n not in STOP) and len(n) >= 3 and not filler
        toks.append(Tok(len(toks), wi, raw, n, stem(n), w["start"], w["end"], p,
                        raw[:1] in "¿¡", filler, content))

    # pausa efectiva antes de cada palabra: hueco + estiramiento anormal (Whisper alarga las
    # palabras junto a los silencios en lugar de dejar hueco)
    for k in range(1, len(toks)):
        gap = max(0.0, toks[k].start - toks[k - 1].end)
        toks[k].pause_before = gap + max(_excess(toks[k - 1]), _excess(toks[k]))
    if toks:
        toks[0].pause_before = 99.0
    return toks


def is_anchor(toks: list[Tok], j: int) -> bool:
    if j == 0:
        return True
    return toks[j - 1].punct in ("comma", "stop", "ellipsis") or toks[j].lead_q or \
        toks[j].pause_before >= 0.3


# ----------------------------------------------------------------------------- comparacion
def _core(toks: list[Tok], a: int, b: int) -> list[Tok]:
    return [t for t in toks[a:b] if not t.filler]


def _lead_stripped(seq: list[Tok]) -> list[Tok]:
    k = 0
    while k < len(seq) - 1 and seq[k].n in LEAD_STRIP:
        k += 1
    return seq[k:]


def _eq(x: Tok, y: Tok) -> bool:
    if x.s == y.s:
        return True
    # tolerancia a errores de ASR en palabras largas (mismo prefijo de 5)
    return len(x.n) >= 7 and len(y.n) >= 7 and x.n[:5] == y.n[:5]


def lcs(a: list[Tok], b: list[Tok]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for k, y in enumerate(b):
            cur[k + 1] = prev[k] + 1 if _eq(x, y) else max(prev[k + 1], cur[k])
        prev = cur
    return prev[-1]


def prefix_match(a: list[Tok], b: list[Tok]) -> int:
    h = 0
    while h < len(a) and h < len(b) and _eq(a[h], b[h]):
        h += 1
    return h


def is_dangling(t: Tok) -> bool:
    return t.n in DANGLING or t.filler


def is_connector(seq: list[Tok]) -> bool:
    ns = tuple(t.n for t in seq if not t.filler)
    return any(ns == c for c in CONNECTORS)


def abandoned(seq: list[Tok]) -> bool:
    """La toma termina inconclusa: '...' o palabra colgante (sin cierre de oracion)."""
    if not seq:
        return False
    last = seq[-1]
    if last.punct == "ellipsis":
        return True
    if last.punct == "stop":
        return False
    core = [t for t in seq if not t.filler]
    if not core:
        return True
    return is_dangling(seq[-1]) or is_dangling(core[-1])


@dataclass
class Cut:
    i0: int
    i1: int
    category: str
    confidence: str
    conf: float
    rule: str
    why: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Opts:
    window_s: float = 40.0        # distancia maxima entre inicio de toma vieja y nueva
    max_take_words: int = 70
    cov_min: float = 0.6          # cobertura LCS de la toma vieja por la nueva
    ccov_min: float = 0.5         # cobertura de palabras de contenido
    ellipsis_back_s: float = 30.0
    restatement: bool = True      # R7 (nivel baja: sugerencia salvo en 'a_fondo')
    candidates: int = 5


# ----------------------------------------------------------------------------- R1 reinicio
def _heads(toks: list[Tok], k: int, lim: int, n: int = 8) -> list[Tok]:
    return _lead_stripped(_core(toks, k, min(lim, k + n + 4)))[:n]


def _list_run(toks: list[Tok], heads: list[list[Tok]], i: int, frame: list[str]) -> list[list[Tok]]:
    """Items consecutivos (desde i) que arrancan con el mismo marco: devuelve sus colas.
    Recorre todas las posiciones (no solo anclas) porque Whisper a veces omite la puntuacion."""
    h = len(frame)
    reps: list[int] = []
    a = i
    while a < len(toks) and a <= i + 80:
        if reps and a - reps[-1] > h + 12:
            break
        hd = heads[a][:h]
        if len(hd) == h and hd[0].i == a + (0 if not toks[a].filler else hd[0].i - a) and \
                all(t.s == f for t, f in zip(hd, frame)) and (not reps or a >= reps[-1] + h):
            reps.append(a)
            a = hd[-1].i + 1
            continue
        a += 1
    tails = []
    for k, a in enumerate(reps):
        stop = reps[k + 1] if k + 1 < len(reps) else None
        if stop is None:
            e = a + 1
            while e < len(toks) and not is_anchor(toks, e) and e - a < 12:
                e += 1
            stop = e
        tails.append(_lead_stripped(_core(toks, a, stop))[h:])
    return tails


def _is_list(tails: list[list[Tok]], abandoned_any: bool) -> bool:
    """3+ items con colas cortas y de contenido disjunto = lista/anafora (no retoma)."""
    if len(tails) < 3 or abandoned_any:
        return False
    body = tails[:-1]  # la ultima cola puede seguir la oracion
    if any(len(t) == 0 for t in body) or sum(len(t) for t in body) / len(body) > 6:
        return False                      # colas vacias = repeticion literal, no lista
    if len({tuple(x.s for x in t) for t in body}) < len(body):
        return False
    seen: set[str] = set()
    for t in tails:
        cs = {x.s for x in t if x.content}
        if cs & seen:
            return False
        seen |= cs
    return True


def _prev_item(toks: list[Tok], heads: list[list[Tok]], i: int, As: list[Tok]) -> tuple[int, int] | None:
    """Item anterior de una anafora: clausula de la misma oracion (<= 20 palabras antes de i) que
    arranca con las mismas >= 3 palabras que la toma vieja ('es el primer horno que compro, | es el
    primer horno en el que cabe una charola, | es el primer horno en el cual...').
    (inicio, palabras del marco comun) o None."""
    for q in range(i - 1, max(-1, i - 21), -1):
        if toks[q].punct in ("stop", "ellipsis"):
            return None
        if toks[q].filler or not is_anchor(toks, q) or not heads[q] or heads[q][0].i >= i:
            continue
        c = prefix_match(heads[q], As)
        if c >= 3:
            return q, c
    return None


def compare(toks: list[Tok], i: int, j: int) -> dict:
    A = _core(toks, i, j)
    B = _core(toks, j, min(len(toks), j + int(len(A) * 2.0) + 10))
    As, Bs = _lead_stripped(A), _lead_stripped(B)
    h = prefix_match(As, Bs)
    hf = lcs(As[:4], Bs[:6]) if As and Bs and _eq(As[0], Bs[0]) else 0
    L = lcs(A, B)
    cov = L / max(1, len(A))
    half = max(4, (len(A) + 1) // 2)
    pc = lcs(A[:half], B) / max(1, min(half, len(A)))
    ca = [t for t in A if t.content]
    bcont = [y for y in B if not y.filler]
    cc = sum(1 for t in ca if any(_eq(t, y) for y in bcont))
    ccov = cc / len(ca) if ca else 1.0
    bset = {t.s for t in B}
    novel_tail = [t for t in As[h:] if t.s not in bset]
    return {"A": A, "B": B, "As": As, "Bs": Bs, "h": h, "hf": hf, "cov": cov, "pc": pc,
            "ccov": ccov, "nA": len(A), "novel_tail": len(novel_tail), "tail": len(As) - h}


def _extend_lead_fillers(toks: list[Tok], i: int) -> int:
    """Incluye en el corte los rellenos que abren la toma vieja ('bueno,' 'pues,') si arrancan
    tras pausa o cierre de oracion (son de esa toma, no de la frase anterior)."""
    while i > 0 and toks[i - 1].filler and (toks[i - 1].pause_before >= 0.3 or i - 1 == 0 or
                                              toks[i - 2].punct in ("stop", "ellipsis")):
        i -= 1
    return i


def detect_restarts(toks: list[Tok], opts: Opts) -> list[Cut]:
    n = len(toks)
    anchors = [k for k in range(n) if is_anchor(toks, k)]
    anchor_set = set(anchors)
    heads = [_heads(toks, k, n) for k in range(n)]
    cuts: list[Cut] = []
    for j in anchors:
        if j == 0:
            continue
        hj = heads[j]
        if not hj or toks[j].filler:
            continue
        cands = {}
        i = j - 1
        while i >= 0 and j - i <= opts.max_take_words and toks[j].start - toks[i].start <= opts.window_s:
            hi = heads[i]
            if toks[i].filler or not hi or hi[0].i >= j or not _eq(hi[0], hj[0]):
                i -= 1
                continue
            h = 0
            while h < len(hi) and h < len(hj) and hi[h].i < j and _eq(hi[h], hj[h]):
                h += 1
            key = hi[0].i
            if key not in cands or i < cands[key][1]:
                cands[key] = (h, i)
            i -= 1
        order = sorted(cands.values(), key=lambda x: (-x[0], -x[1]))
        for _h0, i in order[: opts.candidates]:
            dec = _decide(toks, heads, i, j, i in anchor_set, opts)
            if dec:
                cat, confl, conf, why = dec
                i2 = _extend_lead_fillers(toks, i)
                # la toma nueva conserva su muletilla de arranque ('Ahora,' 'Y') si abre clausula
                j2 = j
                while j2 - 1 > i and (toks[j2 - 1].filler or toks[j2 - 1].n in LEAD_STRIP) and (
                        toks[j2 - 1].pause_before >= 0.3 or toks[j2 - 2].punct in ("stop", "ellipsis")
                        or (not toks[j2 - 1].filler and (j2 - 1) in anchor_set)):
                    j2 -= 1
                cuts.append(Cut(i2, j2, cat, confl, conf, "R1", why))
                break
    # pares desplazados: (i+d, j+d) de un corte aceptado (i, j) se come el arranque de la toma
    # que se conserva; la retoma real es la que empieza antes
    out = []
    for c in cuts:
        shifted = any(0 < c.i0 - o.i0 == c.i1 - o.i1 and c.i0 < o.i1 for o in cuts)
        if not shifted:
            out.append(c)
    return out


def _decide(toks, heads, i, j, i_anchor, opts):
    """(categoria, confianza, conf 0-1, motivo) si [i, j) es una toma vieja que j rehace."""
    m = compare(toks, i, j)
    A, h, hf, cov, ccov, pc = m["A"], m["h"], m["hf"], m["cov"], m["ccov"], m["pc"]
    nA = m["nA"]
    if nA == 0 or hf == 0:
        return None
    # los rellenos que abren la toma nueva ('bueno,' tras la pausa) no son parte de la vieja
    a_end = j
    while a_end - 1 > i and (toks[a_end - 1].filler or toks[a_end - 1].n in LEAD_STRIP) and \
            (toks[a_end - 1].pause_before >= 0.3 or toks[a_end - 2].punct in ("stop", "ellipsis")):
        a_end -= 1
    pause = max(toks[k].pause_before for k in range(a_end, j + 1))
    Araw = toks[i:a_end]
    aband = abandoned(Araw)
    # palabra cortada: la ultima de la toma vieja es prefijo de una de la nueva ("comen" -> "comentarios")
    lastc = next((t for t in reversed(Araw) if not t.filler), None)
    trunc = lastc is not None and 2 <= len(lastc.n) < 6 and lastc.n not in STOP and \
        any(y.n.startswith(lastc.n) and len(y.n) > len(lastc.n) + 1 for y in m["B"])
    aband = aband or trunc
    has_corr = any(t.n in ("perdon", "perdona", "digo", "corrijo") or
                   (t.n == "dicho" and k > 0 and Araw[k - 1].n == "mejor") for k, t in enumerate(Araw))
    ell = Araw[-1].punct == "ellipsis"
    dur = toks[j].start - toks[i].start
    verbatim = cov >= 0.9 and nA >= 2
    if not i_anchor and any(t.punct in ("stop", "ellipsis") for t in Araw[:-1]):
        return None                       # toma vieja a media clausula no puede cruzar oraciones
    # toma vieja que cierra una oracion y sigue con otra: con una entrada de 1-2 palabras no es una
    # retoma, es la charla que sigue sobre lo mismo ('...conviene servir con las carnes. Ahora, la
    # salsa roja, ... la que' | 'conviene servir mas...'); y si la toma nueva arranca a MEDIA
    # oracion, lo que va tras el cierre es el inicio de la oracion que se queda ('...las carnes.
    # [Ahora, la salsa roja,] | en mi opinion, es la mas sabrosa...'): cortarlo la deja sin sujeto
    ends = [q for q, x in enumerate(Araw[:-1]) if x.punct in ("stop", "ellipsis")]
    if ends and sum(1 for x in Araw[ends[-1] + 1:] if not x.filler) >= 3:
        j_starts = toks[j - 1].punct in ("stop", "ellipsis") or toks[j].lead_q or toks[j].pause_before >= 1.0
        if h <= 2 or not j_starts:
            return None
    if not i_anchor and Araw[-1].punct == "stop" and i > 0 and is_dangling(toks[i - 1]):
        # cola que CIERRA la oracion tras una palabra colgante ('lo mas importante es [la X.] La X
        # es lo mas importante'): la oracion siguiente la reformula; cortarla deja 'es' colgando
        return None
    strong_head = h >= 4 or (h >= 3 and any(t.content for t in m["As"][:h]))
    adjacent_verbatim = verbatim and h >= 2 and j - a_end <= 1 and nA <= 6 and aband
    if not i_anchor and not (strong_head or hf >= 3 or adjacent_verbatim):
        return None                       # la toma vieja empieza a media clausula: pide mas evidencia
    if not i_anchor and cov < (0.5 if has_corr else 0.7):
        return None
    if all(t.n in EMPHASIS for t in A) and not ell:
        return None                       # "no, no, no" / "si, si": enfasis, no una retoma
    if nA == 1 and not ell:
        w1 = A[0]
        filler_between = any(t.filler for t in toks[i + 1:j])
        if w1.n in EMPHASIS or not (filler_between or (pause >= 0.5 and w1.n not in STOP)):
            return None                   # "muy, muy" / "no, no" / "sube, sube": enfasis
        return "stutter_repeat", "medium", 0.66, "palabra repetida con titubeo"
    if verbatim and nA <= 3 and pause < 0.8:
        bcopy = [t for t in toks[j:] if not t.filler][:nA]
        if bcopy and bcopy[-1].punct == "stop":
            return None                   # "Asi es, asi es." / "Muy bien, muy bien.": enfasis
    if is_connector(A) and not verbatim:
        return None
    frame = [t.s for t in m["As"][:max(1, h)]]
    tails = _list_run(toks, heads, i, frame)
    if _is_list(tails, aband):
        return None                       # anafora / lista de 3+ items
    p = _prev_item(toks, heads, i, m["As"]) if h >= 3 else None
    if p is not None and _is_list(_list_run(toks, heads, p[0], [t.s for t in m["As"][:p[1]]]), aband):
        return None                       # anafora cuyo primer item esta antes de la toma vieja
    if not verbatim:
        aset = {t.s for t in m["As"]}
        btail = m["Bs"][h:h + 1]
        if h <= 3 and m["tail"] <= 2 and m["novel_tail"] >= 1 and pause < 0.8 and not aband and \
                all(t.s not in aset for t in btail):
            return None                   # "es moderna, es versatil": marco + 1-2 palabras nuevas
    short = nA <= 6 and dur <= 4.0
    if verbatim and h >= 2:
        # tartamudeo = se repite de corrido; con una pausa de por medio ya es una retoma
        cat = "stutter_repeat" if short and pause < 0.5 else "retake"
        return cat, "high", 0.95, "repeticion literal"
    cov_min = opts.cov_min - (0.1 if has_corr else 0.0)
    # paralelismo de 2 items ('Es facil de usar. Es facil de limpiar.'): la toma vieja esta completa
    # y dice algo que la nueva no; salvo en una cadena de intentos (3+ con la misma entrada)
    chain = any(heads[q] and not toks[q].filler and heads[q][0].i == q and
                prefix_match(heads[q], m["As"]) >= min(3, h) for q in range(max(0, i - 60), i))
    if not aband and not chain and Araw[-1].punct in ("stop", "comma") and len(m["As"]) <= 8 and \
            1 <= m["tail"] <= 2:
        Bh = m["B"][:nA + 3]
        if any(t.content and not any(_eq(t, y) for y in Bh) for t in m["As"][h:]):
            return "retake", "low", 0.45, "paralelismo: la toma vieja dice algo que la nueva no"
    if h >= 2 and cov >= cov_min and ccov >= opts.ccov_min:
        hi = cov >= 0.75 or aband or pause >= 1.0
        return "retake", "high" if hi else "medium", 0.86 if hi else 0.7, "reinicio con la misma entrada"
    if hf >= 2 and pause >= 1.0 and cov >= 0.5 and ccov >= 0.7 and nA >= 8:
        return "retake", "medium", 0.66, "reinicio tras pausa (entrada difusa)"
    if strong_head and pause >= 1.0 and pc >= 0.8 and cov >= 0.4 and nA >= 8:
        return "retake", "medium", 0.64, "reinicio: primera mitad repetida, final distinto"
    if aband and (pause >= 0.4 or ell) and cov >= 0.5 and ccov >= 0.5:
        hi = cov >= 0.75
        return "false_start", "high" if hi else "medium", 0.86 if hi else 0.7, "toma abandonada y reiniciada"
    if h >= 3 and pause >= 0.5 and nA <= 8 and (aband or m["tail"] <= 3):
        return "false_start", "medium", 0.68, "intento corto con la misma entrada"
    if h >= 2 and aband and pause >= 0.8 and nA <= 10 and any(t.content for t in m["As"][:h]):
        return "false_start", "medium", 0.66, "arranque abandonado y reiniciado tras pausa"
    # sustitucion de un valor: mismo marco palabra por palabra, solo cambia un numero
    As, Bs = m["As"], m["Bs"]
    if len(As) >= 3 and len(Bs) >= len(As) and pause >= 0.8:
        diff = [(x, y) for x, y in zip(As, Bs[:len(As)]) if not _eq(x, y)]
        if diff and all(is_num(x.n) and is_num(y.n) for x, y in diff) and len(diff) <= 2:
            return "self_correction", "medium", 0.72, "valor corregido al repetir la frase"
    return None


def detect_dead_repeats(toks: list[Tok], opts: Opts) -> list[Cut]:
    """R2b: segundo intento corto que repite la entrada de una toma previa y muere colgado
    ('si quieres saber mas, |pausa|'). Se quita solo ese fragmento."""
    n = len(toks)
    anchors = [k for k in range(n) if is_anchor(toks, k)]
    heads = [_heads(toks, k, n) for k in range(n)]
    cuts = []
    for a in anchors:
        # unidad = desde a hasta la siguiente pausa/cierre (no hasta la siguiente coma)
        e = a + 1
        while e < n and not (toks[e].pause_before >= 0.8 or toks[e - 1].punct in ("stop", "ellipsis")):
            e += 1
        if e >= n or e - a > 6:
            continue
        seg = toks[a:e]
        core = [t for t in seg if not t.filler]
        if not (2 <= len(core) <= 5) or not abandoned(seg) or is_connector(core):
            continue
        if seg[-1].punct not in ("comma", "ellipsis"):
            continue          # solo si Whisper marco el corte (coma / '...'), no por un hueco de tiempos
        hd = heads[a]
        # busca una toma anterior (<= 20 s) con la misma entrada de >= 3 palabras
        k = a - 1
        found = False
        while k >= 0 and toks[a].start - toks[k].start <= 20:
            hk = heads[k]
            if hk and not toks[k].filler and hk[0].i < a:
                h = 0
                while h < len(hk) and h < len(hd) and hk[h].i < a and _eq(hk[h], hd[h]):
                    h += 1
                if h >= 3 and h >= len(_lead_stripped(core)) - 1:
                    found = True
                    break
            k -= 1
        if found:
            cuts.append(Cut(a, e, "false_start", "medium", 0.68, "R2b", "segundo intento que muere colgado"))
    return cuts


# ----------------------------------------------------------------------------- R2 fragmento
def detect_micro(toks: list[Tok], opts: Opts) -> list[Cut]:
    """Fragmento de <=3 palabras que queda colgado, pausa, y se reinicia con la misma palabra."""
    cuts = []
    anchors = [k for k in range(len(toks)) if is_anchor(toks, k)]
    for a, b in zip(anchors, anchors[1:]):
        seg = toks[a:b]
        core = [t for t in seg if not t.filler]
        if not (1 <= len(core) <= 3) or b >= len(toks):
            continue
        if toks[b].pause_before < 0.4 or seg[-1].punct == "stop":
            continue
        if not is_dangling(seg[-1]) and seg[-1].punct != "ellipsis":
            continue
        if is_connector(core):
            continue
        nxt = _lead_stripped(_core(toks, b, min(len(toks), b + 4)))
        cs = _lead_stripped(core)
        if nxt and cs and _eq(cs[0], nxt[0]):
            if len(cs) == 1 and cs[0].n in EMPHASIS and seg[-1].punct != "ellipsis":
                continue
            cuts.append(Cut(a, b, "false_start", "high", 0.88, "R2", "fragmento colgado y reinicio"))
    return cuts


# ----------------------------------------------------------------------------- R3 abandonada
R3_TAIL = 4           # palabras: la cola que se reinicia ('y que... Y que dentro de lo que cabe')
R3_LONG = 12          # palabras: una oracion mas larga lleva informacion...
R3_RESTATED = 0.3     # ...y solo se corta sola si lo que dice se vuelve a decir despues


def _expletive_near(toks: list[Tok], k: int, span: int = 3) -> bool:
    """Una groseria o comentario meta arranca en las `span` palabras siguientes a k."""
    for m in range(k + 1, min(len(toks), k + 1 + span)):
        for ex in EXPLETIVES + META:
            if tuple(t.n for t in toks[m:m + len(ex)]) == ex:
                return True
    return False


def _untranscribed(mask, a: float, b: float) -> float:
    """Segundos de VOZ (fuera de los huecos) entre a y b."""
    import numpy as np
    k0, k1 = max(0, _frame(a + 0.05)), min(len(mask), _frame(b - 0.05))
    return float(np.sum(~mask[k0:k1])) * SNAP_HOP if k1 > k0 else 0.0


def detect_abandoned(toks: list[Tok], opts: Opts, mask=None) -> list[Cut]:
    cuts = []
    n = len(toks)
    for k, t in enumerate(toks):
        if t.punct != "ellipsis" or t.filler or not is_dangling(t):
            continue
        # inicio de la oracion: tras el ultimo cierre (. ? ! ...) y no mas de N s atras
        s = k
        while s > 0 and toks[s - 1].punct not in ("stop", "ellipsis") and \
                t.start - toks[s - 1].start <= opts.ellipsis_back_s:
            s -= 1
        if t.start - toks[s].start > opts.ellipsis_back_s:
            continue
        nx = k + 1
        while nx < n and toks[nx].filler:
            nx += 1
        # la oracion siguiente arranca con las palabras de la COLA ('...de esta batidora y que...
        # |pausa| Y que ademas no hace ruido'; '...con todo y el relleno, no se... No se la pierdan'):
        # Whisper partio la oracion en la duda; lo abandonado es solo esa cola
        def tail_ok(f):
            if nx >= n or toks[f].filler or toks[nx].start - t.end > 3.0:
                return False
            m = prefix_match(_core(toks, f, k + 1), _core(toks, nx, min(n, nx + R3_TAIL)))
            # dos palabras iguales, o una si la cola abre clausula tras una coma
            return m >= 2 or (m >= 1 and toks[f - 1].punct == "comma")
        f = next((f for f in range(max(s + 1, k + 1 - R3_TAIL), k + 1) if tail_ok(f)), None)
        if f is not None:
            cuts.append(Cut(f, k + 1, "false_start", "high", 0.84, "R3", "cola abandonada y reiniciada"))
            continue
        nw = k + 1 - s
        hi = nw <= 25
        conf = ("high", 0.84) if hi else ("medium", 0.68)
        if nw > R3_LONG and not _expletive_near(toks, k):
            # oracion larga que se queda a medias ('Escribanme abajo que receta les gustaria ... sobre
            # estas...'): lleva informacion; solo se corta sola si se vuelve a decir despues
            A = [x for x in toks[s:k + 1] if x.content]
            B = toks[k + 1:min(n, k + 1 + nw + 15)]
            cov = sum(1 for x in A if any(_eq(x, y) for y in B)) / max(1, len(A))
            # con audio: voz sin transcribir pegada a la oracion siguiente = Whisper se trago su
            # arranque repetido ('...que le da un... |pausa| [El secreto de este cafe...] barro,
            # que le da un sabor'): si se volvio a decir
            lost = mask is not None and nx < n and _lost_onset(mask, t.end, toks[nx].start) is not None
            if cov < R3_RESTATED and not lost:
                conf = ("low", 0.45)
        # pausa dramatica: la oracion siguiente la COMPLETA en minuscula, con una cifra o con una
        # exclamacion corta ('El secreto es... la mantequilla.', 'Y el ganador es... ¡María!'); sin
        # voz sin transcribir pegada (Whisper no se trago un reinicio) no hay prueba de que sea un
        # arranque abandonado: solo sugerencia
        reveal = nx < n and toks[nx].raw.startswith("¡") and not _expletive_near(toks, k) and any(
            x.raw.rstrip().endswith("!") for x in toks[nx:min(n, nx + 3)])
        if nx < n and (toks[nx].raw[:1].islower() or toks[nx].raw[:1].isdigit() or reveal) and not (
                mask is not None and _untranscribed(mask, t.end, toks[nx].start) >= LOST_VOICE):
            conf = ("low", 0.45)
        cuts.append(Cut(s, k + 1, "false_start", conf[0], conf[1], "R3", "oracion abandonada"))
    return cuts


# ----------------------------------------------------------------------------- R4 correccion
def detect_corrections(toks: list[Tok], opts: Opts) -> list[Cut]:
    cuts = []
    n = len(toks)
    for m in range(1, n - 1):
        for mk in CORR_MARKERS:
            L = len(mk)
            if m + L >= n or tuple(t.n for t in toks[m:m + L]) != mk:
                continue
            last = toks[m + L - 1]
            # marcador entre comas: "..., no, ..." (excluye "¿no?", "no es", respuestas)
            if toks[m - 1].punct != "comma" or last.punct != "comma" or toks[m].lead_q:
                continue
            y = m + L
            while y < n and toks[y].filler:
                y += 1
            if y >= n:
                continue
            Y = toks[y]

            def same(tk: Tok) -> bool:
                return not tk.filler and ((is_num(tk.n) and is_num(Y.n)) or tk.s == Y.s)
            # elemento reparado mas cercano hacia atras (misma oracion, <= 6 palabras)
            p = None
            k = m - 1
            while k >= 0 and m - k <= 6:
                if toks[k].punct in ("stop", "ellipsis") and k < m - 1:
                    break
                if same(toks[k]):
                    p = k
                    break
                k -= 1
            if p is None or (toks[p].n in EMPHASIS and Y.n in EMPHASIS):
                continue          # "no, no, no" / "si, no, si": enfasis, no una correccion
            while p > 0 and is_num(toks[p].n) and is_num(toks[p - 1].n) and toks[p - 1].punct == "":
                p -= 1          # "tres millones", "dos mil": el valor completo
            # encadena alternativas anteriores separadas por comas ("12 piezas, creo que, 14 piezas")
            while True:
                q = None
                k = p - 1
                seen_comma = False
                while k >= 0 and p - k <= 6:
                    if toks[k].punct in ("stop", "ellipsis"):
                        break
                    if toks[k].punct == "comma":
                        seen_comma = True
                    if same(toks[k]) and seen_comma:
                        q = k
                        break
                    k -= 1
                if q is None:
                    break
                p = q
            cuts.append(Cut(p, y, "self_correction", "high", 0.9, "R4", "autocorreccion con '%s'" % " ".join(mk)))
    return cuts


# ----------------------------------------------------------------------------- R5 error
def detect_bloopers(toks: list[Tok], opts: Opts) -> list[Cut]:
    cuts = []
    n = len(toks)
    anchors = [k for k in range(n) if is_anchor(toks, k)]
    unit_bounds = [k for k in range(n) if k == 0 or toks[k].pause_before >= 0.5 or
                   toks[k - 1].punct in ("stop", "ellipsis")] + [n]
    for m in range(n):
        for ex in EXPLETIVES + META:
            L = len(ex)
            if tuple(t.n for t in toks[m:m + L]) != ex:
                continue
            is_meta = ex in META
            if is_meta:
                # meta solo si es interjeccion aislada: entre comas/pausas o fin de unidad
                before_ok = m == 0 or toks[m - 1].punct in ("comma", "stop", "ellipsis") or \
                    toks[m].pause_before >= 0.4
                after = toks[m + L - 1]
                after_ok = after.punct in ("comma", "stop", "ellipsis") or \
                    (m + L < n and toks[m + L].pause_before >= 0.4) or m + L == n
                if not (before_ok and after_ok):
                    continue
                if ex in {("perdon",), ("perdona",), ("perdoname",)}:
                    continue      # "perdon" lo maneja R4 si hay correccion
            # unidad que contiene la groseria
            us = max(b for b in unit_bounds if b <= m)
            ue = min(b for b in unit_bounds if b > m)
            # unidad larga, o con 4+ palabras antes de la groseria (la toma buena que termina en
            # 'no mames'): solo desde la clausula; si no, se iria la toma entera junto con la retoma
            if m - us > 15 or sum(1 for t in toks[us:m] if not t.filler) >= 4:
                us = max(a for a in anchors if a <= m)
            # la unidad debe ser corta o terminar en la groseria (no "esta de poca madre")
            if ue - (m + L) > 3:
                continue
            cuts.append(Cut(us, ue, "blooper", "high" if not is_meta else "medium",
                            0.85 if not is_meta else 0.7, "R5", "groseria o comentario"))
    return cuts


# ----------------------------------------------------------------------------- R6 tartamudeo
def detect_word_stutter(toks: list[Tok], opts: Opts) -> list[Cut]:
    cuts = []
    for k in range(len(toks) - 1):
        a, b = toks[k], toks[k + 1]
        if a.n == b.n and a.n in STUTTER_FN and a.punct == "" and b.pause_before < 0.8:
            cuts.append(Cut(k, k + 1, "stutter_repeat", "medium", 0.7, "R6", "palabra repetida"))
    return cuts


# ----------------------------------------------------------------------------- R7 reformulacion
def detect_restatements(toks: list[Tok], opts: Opts) -> list[Cut]:
    cuts = []
    sents = []
    s = 0
    for k, t in enumerate(toks):
        if t.punct in ("stop", "ellipsis") or k == len(toks) - 1:
            sents.append((s, k + 1))
            s = k + 1
    for (a0, a1), (b0, _b1) in zip(sents, sents[1:]):
        if toks[b0].pause_before < 1.5:
            continue
        A = _core(toks, a0, a1)
        ca = [t for t in A if t.content]
        if len(ca) < 4 or len(A) > 60:
            continue
        B = _core(toks, b0, min(len(toks), b0 + int(len(A) * 1.5) + 10))
        bset = {t.s for t in B}
        ccov = sum(1 for t in ca if t.s in bset) / len(ca)
        cov = lcs(A, B) / len(A)
        if ccov >= 0.6 and cov >= 0.45:
            cuts.append(Cut(a0, b0, "retake", "low", 0.45, "R7", "reformulacion tras pausa"))
    return cuts


def _drop_contained(cuts: list[Cut]) -> list[Cut]:
    """Quita cortes contenidos en otro (mismo rango o sub-rango); conserva el mas grande."""
    cuts = sorted(cuts, key=lambda c: (c.i0, -(c.i1 - c.i0), -c.conf))
    out: list[Cut] = []
    for c in cuts:
        if c.i1 <= c.i0 or any(o.i0 <= c.i0 and c.i1 <= o.i1 for o in out):
            continue
        out.append(c)
    return sorted(out, key=lambda c: (c.i0, c.i1))


def _merge_overlaps(cuts: list[Cut]) -> list[Cut]:
    """Dos intentos que se enciman en palabras (la toma vieja de uno empieza dentro del otro) son
    UN solo corte: una tarjeta que se restaura entera, sin dejar media toma. Manda el de mayor
    confianza."""
    out: list[Cut] = []
    for c in sorted(cuts, key=lambda c: (c.i0, c.i1)):
        if out and c.i0 < out[-1].i1:
            o = out[-1]
            best = o if o.conf >= c.conf else c
            out[-1] = Cut(o.i0, max(o.i1, c.i1), best.category, best.confidence, max(o.conf, c.conf),
                          best.rule, best.why)
        else:
            out.append(c)
    return out


def _dedupe(cuts: list[Cut]) -> list[Cut]:
    """Cortes finales: los de nivel alta/media sin contenidos ni encimados; los de nivel baja
    (sugerencias) solo en lo que no cubre ya un corte mas seguro (partidos alrededor de el: una
    reformulacion que contiene una autocorreccion no la apaga)."""
    main = _merge_overlaps(_drop_contained([c for c in cuts if c.confidence != "low"]))
    low = _drop_contained([c for c in cuts if c.confidence == "low"])
    pieces: list[Cut] = []
    for c in low:
        segs = [(c.i0, c.i1)]
        for m in main:
            nxt = []
            for a, b in segs:
                if m.i1 <= a or m.i0 >= b:
                    nxt.append((a, b))
                    continue
                if a < m.i0:
                    nxt.append((a, m.i0))
                if m.i1 < b:
                    nxt.append((m.i1, b))
            segs = nxt
        for a, b in segs:
            if b - a >= 2:
                pieces.append(Cut(a, b, c.category, c.confidence, c.conf, c.rule, c.why))
    return sorted(main + _merge_overlaps(pieces), key=lambda c: (c.i0, c.i1))


def detect_raw(words: list[dict], opts: Opts | None = None, mask=None) -> tuple[list[Tok], list[Cut]]:
    """Reglas sobre las palabras (sin tiempos de corte): (tokens, cortes por indice de token).
    `mask` (huecos de voz, con audio): R3 ve si Whisper se trago el arranque de la oracion siguiente."""
    opts = opts or Opts()
    toks = tokenize(words)
    raw: list[Cut] = []
    for fn in (detect_restarts, detect_micro, detect_dead_repeats, detect_abandoned,
               detect_corrections, detect_bloopers, detect_word_stutter):
        raw += fn(toks, opts, mask) if fn is detect_abandoned else fn(toks, opts)
    if opts.restatement:
        raw += detect_restatements(toks, opts)
    cuts = _dedupe(raw)
    # una muletilla pura ('pues,' 'eh') entre la toma quitada y una pausa es la cola del intento
    # malo, no el arranque de la toma buena: se va con el corte (la buena arranca tras la pausa)
    starts = {c.i0 for c in cuts}
    for c in cuts:
        while c.i1 < len(toks) - 1 and c.i1 not in starts and toks[c.i1].filler and \
                toks[c.i1].n in FILLERS and toks[c.i1 + 1].pause_before >= 0.3:
            c.i1 += 1
    # un error (groseria/comentario) solo se corta solo dentro de una cadena de retomas; aislado
    # puede ser parte del mensaje ("¡que rico, no mames!"): queda como sugerencia
    def gap(a: Cut, b: Cut) -> float:
        """Segundos entre dos cortes (0 si se tocan o enciman)."""
        if a.i1 <= b.i0:
            return max(0.0, toks[b.i0].start - toks[a.i1 - 1].end)
        if b.i1 <= a.i0:
            return max(0.0, toks[a.i0].start - toks[b.i1 - 1].end)
        return 0.0
    for c in cuts:
        if c.category != "blooper":
            continue
        if not any(o is not c and o.category in ("retake", "false_start") and gap(c, o) <= 1.5
                   for o in cuts):
            c.confidence, c.conf = "low", 0.4
    return toks, cuts


# ----------------------------------------------------------------------------- bordes
def _energy(audio):
    """Audio (ruta o arreglo mono 16 kHz) -> (dB por ventana de 20 ms con paso de 10 ms, dB de la
    voz). None si no se puede leer."""
    if audio is None:
        return None
    try:
        import numpy as np
        if isinstance(audio, (str, bytes)) or hasattr(audio, "__fspath__"):
            import edit as E
            y = E._mono16k(audio)
        else:
            y = np.asarray(audio, dtype=np.float32).reshape(-1)
        hop = int(16000 * SNAP_HOP)
        n = len(y) // hop
        if n < 4:
            return None
        e10 = np.sum(y[:n * hop].reshape(n, hop).astype(np.float64) ** 2, axis=1)
        e20 = (e10[:-1] + e10[1:]) / (2 * hop)
        db = 10 * np.log10(e20 + 1e-12)
        return db
    except Exception as e:  # noqa: sin audio legible -> bordes solo con los tiempos
        print(f"   tomas: sin audio para ajustar bordes ({e})")
        return None


def _snap(db, target: float, lo: float, hi: float, soft_lo: float, soft_hi: float, ref: float) -> float:
    """Centro de la ventana de 20 ms de menor energia a +-SNAP_WIN de `target` dentro de [lo, hi].
    Fuera de [soft_lo, soft_hi] (dentro de una palabra que se queda) solo si es silencio."""
    fallback = min(max(target, lo), hi) if lo <= hi else target
    a, b = max(lo, target - SNAP_WIN), min(hi, target + SNAP_WIN)
    if db is None or b <= a:
        return fallback
    k0 = max(0, int((a - SNAP_FRAME / 2) / SNAP_HOP + 0.999))
    k1 = min(len(db) - 1, int((b - SNAP_FRAME / 2) / SNAP_HOP))
    cands = []
    for k in range(k0, k1 + 1):
        t = k * SNAP_HOP + SNAP_FRAME / 2
        v = float(db[k])
        if (t < soft_lo or t > soft_hi) and v > ref + QUIET_DB:
            continue
        cands.append((t, v))
    if not cands:
        return fallback
    # empate (<= 1 dB del minimo): la mas cercana al objetivo
    vmin = min(v for _, v in cands)
    return min((t for t, v in cands if v <= vmin + 1.0), key=lambda t: abs(t - target))


def _ext(t: Tok) -> tuple[float, float]:
    """Extension efectiva de una palabra: si esta estirada (> STRETCH_MAX) se acota a su final (el
    timing crudo de Whisper mete la pausa ANTES de la palabra)."""
    return max(t.start, t.end - STRETCH_MAX), t.end


def _mid(ab: tuple[float, float]) -> float:
    return (ab[0] + ab[1]) / 2


def _gap_mask(db, thr: float, min_len: float):
    """Ventanas en un hueco de voz: energia < thr sostenida al menos min_len (un cierre de
    consonante dura menos y no cuenta)."""
    import numpy as np
    q = np.asarray(db) < thr
    out = np.zeros(len(q), bool)
    need = max(1, int(round(min_len / SNAP_HOP)))
    k = 0
    while k < len(q):
        if not q[k]:
            k += 1
            continue
        j = k
        while j < len(q) and q[j]:
            j += 1
        if j - k >= need:
            out[k:j] = True
        k = j
    return out


def _frame(t: float) -> int:
    return int(round((t - SNAP_FRAME / 2) / SNAP_HOP))


def _time(k: int) -> float:
    return k * SNAP_HOP + SNAP_FRAME / 2


def _gap_before(mask, t: float, lo: float) -> tuple[float, float] | None:
    """Si t cae en VOZ (Whisper suele marcar tarde el inicio de una palabra), el hueco que precede a
    esa racha de voz: (inicio, fin). None si t ya esta en un hueco o no hay hueco antes de `lo`."""
    if mask is None or not len(mask):
        return None
    k = min(max(0, _frame(t)), len(mask) - 1)
    if mask[k]:
        return None
    k0 = max(0, _frame(lo))
    while k >= k0 and not mask[k]:
        k -= 1
    if k < k0:
        return None
    a = k
    while a - 1 >= 0 and mask[a - 1]:
        a -= 1
    return _time(a) - SNAP_HOP / 2, _time(k) + SNAP_HOP / 2


def _lost_onset(mask, t_from: float, t_to: float) -> tuple[float, float] | None:
    """Whisper se traga el arranque REPETIDO de la toma buena ('...que le da un... |pausa| [El
    secreto de este cafe es la olla de] barro, que le da un sabor'): entre la ultima palabra quitada
    (t_from) y la primera que se queda (t_to), tras la ULTIMA pausa >= PAUSE_MIN hay >= LOST_VOICE s
    de voz sin transcribir pegada a la palabra que se queda. Esa voz es el inicio de la toma buena:
    el corte termina en esa pausa y no la deja a medias. Si tras la ultima pausa casi no hay voz, la
    voz sin transcribir de antes es otro intento que Whisper se trago y se va con el corte (camino
    normal). (inicio, fin) de la pausa, o None."""
    import numpy as np
    if mask is None or not len(mask) or t_to - t_from < PAUSE_MIN + LOST_VOICE:
        return None
    k0, k1 = max(0, _frame(t_from)), min(len(mask), _frame(t_to))
    need = max(1, int(round(PAUSE_MIN / SNAP_HOP)))
    j = k1
    while j > k0:
        if not mask[j - 1]:
            j -= 1
            continue
        k = j
        while k > k0 and mask[k - 1]:
            k -= 1
        if j - k >= need:
            if float(np.sum(~mask[j:k1])) * SNAP_HOP >= LOST_VOICE:
                return _time(k) - SNAP_HOP / 2, _time(j - 1) + SNAP_HOP / 2
            return None
        j = k
    return None


def _voice_gaps(toks: list[Tok], cuts: list[Cut], mask) -> list[Cut]:
    """R8 (nivel baja): una palabra colgante (o con '...') seguida de >= 0.8 s de VOZ que el
    transcriptor no escribio y luego una pausa: Whisper suele tragarse un intento repetido ('La
    [masa lleva harina de,] |pausa| La masa lleva harina de maiz'). Se sugiere quitar la palabra
    colgante y esa voz; la toma buena arranca tras la pausa."""
    import numpy as np
    if mask is None:
        return []
    busy = {k for c in cuts for k in range(c.i0 - 1, c.i1 + 1)}
    out = []
    for k in range(len(toks) - 1):
        a, b = toks[k], toks[k + 1]
        if k in busy or k + 1 in busy or b.start - a.end < 1.2:
            continue
        if not (a.punct == "ellipsis" or (a.punct != "stop" and is_dangling(a))):
            continue
        g = _gap_before(mask, b.start, a.end + 0.3) or (b.start - 0.05, b.start)
        k0, k1 = max(0, _frame(a.end + 0.05)), min(len(mask), _frame(g[0]))
        if k1 - k0 <= 0 or float(np.sum(~mask[k0:k1])) * SNAP_HOP < 0.8:
            continue
        out.append(Cut(k, k + 1, "false_start", "low", 0.45, "R8", "voz sin transcribir"))
    return out


def _edges(toks: list[Tok], c: Cut, dur: float, db, ref: float, mask=None) -> tuple[float, float] | None:
    """Tiempos [inicio, fin) del corte: aire de los silencios + ajuste a la energia del audio.
    Con audio, si el inicio de Whisper cae en plena voz (llego tarde) el borde va al hueco real que
    precede a esa voz; si no, a la ventana de menor energia a +-SNAP_WIN."""
    n = len(toks)
    first, last = toks[c.i0], toks[c.i1 - 1]
    prev = toks[c.i0 - 1] if c.i0 > 0 else None
    nxt = toks[c.i1] if c.i1 < n else None
    s0 = first.start
    # inicio: deja GAP_PAD_OUT de aire tras la palabra anterior y quita hasta GAP_PAD_IN antes de
    # la primera palabra quitada; dentro de la palabra anterior solo si ahi ya es silencio
    hi_s = max(s0, _mid((first.start, first.end)))
    if prev is not None:
        gap = s0 - prev.end
        tgt_s = s0 - min(GAP_PAD_IN, max(0.0, gap - GAP_PAD_OUT))
        lo_s, soft_lo = min(_mid(_ext(prev)), s0), min(prev.end, s0)
    else:
        tgt_s = max(0.0, s0 - GAP_PAD_IN)
        lo_s = soft_lo = 0.0
    g = _gap_before(mask, s0, max(soft_lo - 0.05, s0 - ONSET_BACK))
    if g is not None:
        L = g[1] - g[0]
        start = max(g[0] + min(GAP_PAD_OUT, L / 2), g[1] - GAP_PAD_IN)
    else:
        start = _snap(db, tgt_s, lo_s, hi_s, soft_lo, hi_s, ref)
    # fin: GAP_PAD_IN de aire antes de la primera palabra que se queda (en su tramo estirado solo
    # si ahi es silencio)
    lo_e = min(_mid((last.start, last.end)), last.end)
    if nxt is not None:
        e0 = nxt.start
        gap = e0 - last.end
        tgt_e = e0 - min(GAP_PAD_IN, max(0.0, gap))
        hi_e = max(e0, _mid(_ext(nxt)))
        g = _lost_onset(mask, last.end, e0) or _gap_before(mask, e0, max(lo_e, e0 - ONSET_BACK))
        if g is not None:
            end = g[1] - min(GAP_PAD_IN, (g[1] - g[0]) / 2)
        else:
            end = _snap(db, tgt_e, lo_e, hi_e, lo_e, e0, ref)
    else:
        end = last.end + GAP_PAD_OUT * 3
        end = max(min(end, float(dur)) if dur else end, lo_e)
    if dur:
        start, end = max(0.0, min(start, dur)), max(0.0, min(end, dur))
    if end - start < MIN_CUT:
        return None
    return start, end


def _fix_kept(toks: list[Tok], c: Cut, start: float, end: float) -> tuple[float, float] | None:
    """Ninguna palabra que se queda con su punto medio dentro del corte; las que se quitan, dentro.
    None si el corte ya no cubre su toma."""
    for _ in range(3):
        moved = False
        k = c.i0 - 1
        while k >= 0 and c.i0 - k <= 12 and toks[k].end > start - 1e-6:
            ab = _ext(toks[k])
            if start < _mid(ab) < end:
                start, moved = max(start, ab[1]), True
            k -= 1
        k = c.i1
        while k < len(toks) and k - c.i1 < 12 and toks[k].start < end + 1e-6:
            ab = _ext(toks[k])
            if start < _mid(ab) < end:
                end, moved = min(end, ab[0]), True
            k += 1
        if not moved:
            break
    removed = toks[c.i0:c.i1]
    if end - start < MIN_CUT or not removed:
        return None
    if not (start <= _mid((removed[0].start, removed[0].end)) + 1e-6 and
            end >= _mid((removed[-1].start, removed[-1].end)) - 1e-6):
        return None
    return start, end


# ----------------------------------------------------------------------------- registros
def _cut_id(kind: str, a: float, b: float, used: set) -> str:
    base = hashlib.sha1(f"{kind}|{a:.1f}|{b:.1f}".encode()).hexdigest()[:10]
    cid, k = f"tk_{base}", 1
    while cid in used:
        k += 1
        cid = f"tk_{base}_{k}"
    used.add(cid)
    return cid


def _clip(s: str, n: int = TEXT_MAX) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _keep_span(toks: list[Tok], i1: int, stop_at: int) -> tuple[int, int]:
    """Toma que se queda: desde i1 hasta el cierre de su oracion / pausa larga (max 30 palabras)."""
    e = i1
    while e < len(toks) and e < stop_at and e - i1 < 30:
        # pausa antes de e: hueco + lo estirado de e (no lo de la palabra anterior: la primera de la
        # toma buena suele traer estirada la pausa que la precede)
        if e > i1 and max(0.0, toks[e].start - toks[e - 1].end) + _excess(toks[e]) >= 0.8:
            break
        e += 1
        if toks[e - 1].punct in ("stop", "ellipsis"):
            break
    return i1, max(i1 + 1, e)


def _reason(kind: str, c: Cut, keep_t: float | None, kept: str) -> str:
    when = f" en {fmt_time(keep_t)}" if keep_t is not None else ""
    if kind == "retoma":
        return f"Lo repites mejor{when}" if keep_t is not None else "Toma que repites más adelante"
    if kind == "arranque_fallido":
        if c.rule == "R3":
            return "Frase que dejas a medias"
        if c.rule == "R8":
            return "Frase a medias y voz sin transcribir: quizá la repites (revísala)"
        return f"Arranque que dejas a medias; lo retomas{when}"
    if kind == "autocorreccion":
        head = " ".join(kept.split()[:4]).rstrip(",.;:")
        return f"Te corriges enseguida: se queda «{head}»" if head else "Te corriges enseguida"
    if kind == "tartamudeo":
        return "Palabras repetidas de seguido"
    if kind == "error":
        return ("Grosería o comentario en medio de una retoma" if c.confidence != "low"
                else "Grosería o comentario fuera de la toma (revísalo)")
    return "Fuera de tema"


def _norm_sens(sensitivity) -> str:
    return SENS_ALIASES.get(str(sensitivity or "normal").strip().lower(), "normal")


def enabled_for(level: str, sensitivity: str) -> bool:
    s = _norm_sens(sensitivity)
    if s == "a_fondo":
        return True
    if s == "suave":
        return level == "alta"
    return level in ("alta", "media")


def _intervals(cuts, pred=None) -> list[tuple[float, float]]:
    iv = []
    for c in cuts or []:
        if not isinstance(c, dict) or c.get("enabled", True) is False:
            continue
        if pred is not None and not pred(c):
            continue
        try:
            a, b = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b > a:
            iv.append((a, b))
    iv.sort()
    out: list[list[float]] = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _union_len(iv) -> float:
    return sum(b - a for a, b in iv)


def _is_take(c) -> bool:
    return isinstance(c, dict) and c.get("src") == "takes"


def _frag_guard(toks: list[Tok], recs: list[dict], existing, dur: float) -> None:
    """Un corte de tomas junto a un silencio no debe dejar entre ambos un tramo < MIN_FRAG con una
    palabra que se queda (la linea de tiempo lo absorberia y la palabra se perderia): se acorta el
    borde del corte de tomas y, si no alcanza, el corte queda como sugerencia."""
    try:
        from motion.timeline import MIN_FRAG, MIN_KEEP
    except Exception:  # noqa
        MIN_FRAG, MIN_KEEP = 0.15, 0.05
    if not dur or not recs:
        return
    other = [c for c in existing or [] if isinstance(c, dict) and not _is_take(c)]

    def keeps(cs):
        out, t = [], 0.0
        for a, b in _intervals(cs):
            if a - t > MIN_KEEP:
                out.append((t, a))
            t = max(t, b)
        if dur - t > MIN_KEEP:
            out.append((t, dur))
        return out

    def lost(ks, t):
        for a, b in ks:
            if a - 1e-6 <= t <= b + 1e-6:
                return b - a < MIN_FRAG and len(ks) > 1
        return True

    base = keeps(other)
    for it in range(3):
        en = [r for r in recs if r["enabled"]]
        removed = {k for r in en for k in range(r["_i0"], r["_i1"])}
        ks = keeps(other + en)
        bad = []
        for t in toks:
            if t.i in removed:
                continue
            m = _mid(_ext(t))
            if not lost(base, m) and lost(ks, m):
                bad.append((t, m))
        if not bad:
            return
        for t, m in bad:
            for r in en:
                if r["_i1"] <= t.i and 0 <= m - r["end"] < MIN_FRAG + 0.25:
                    # corte que termina justo antes: su fin retrocede (nunca antes de su ultima palabra)
                    floor = _mid((toks[r["_i1"] - 1].start, toks[r["_i1"] - 1].end))
                    new = r["end"] - (MIN_FRAG + 0.02)
                    if new >= floor and it < 2:
                        r["end"] = round(new, 3)
                    elif t.end - t.start >= 0.12:
                        r["enabled"] = False          # palabra real en juego: queda como sugerencia
                        r["guard"] = True             # ...que un nivel no vuelve a encender
                elif r["_i0"] > t.i and 0 <= r["start"] - m < MIN_FRAG + 0.25:
                    ceil = _mid((toks[r["_i0"]].start, toks[r["_i0"]].end))
                    new = r["start"] + (MIN_FRAG + 0.02)
                    if new <= ceil and it < 2:
                        r["start"] = round(new, 3)
                    elif t.end - t.start >= 0.12:
                        r["enabled"] = False
                        r["guard"] = True


def detect(words: list[dict], dur: float, *, sensitivity: str = "normal", audio=None,
           existing_cuts: list[dict] | None = None) -> dict:
    """Cortes de tomas (contrato arriba) + meta de plan['takes'] (sin tema: lo pone topic())."""
    t0 = time.time()
    sens = _norm_sens(sensitivity)
    toks = tokenize(words)
    try:
        dur = float(dur or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    if not dur and toks:
        dur = max(t.end for t in toks)
    db = _energy(audio) if toks else None
    ref = 0.0
    if db is not None:
        import numpy as np
        idx = [k for t in toks for k in range(int(t.start / SNAP_HOP), int(t.end / SNAP_HOP) + 1)
               if 0 <= k < len(db)]
        ref = float(np.median(db[idx])) if idx else float(np.median(db))
    mask = _gap_mask(db, ref + GAP_DB, MIN_GAP) if db is not None else None
    toks, raw = detect_raw(words, mask=mask)
    if mask is not None:
        raw = sorted(raw + _voice_gaps(toks, raw, mask), key=lambda c: (c.i0, c.i1))
    recs: list[dict] = []
    for c in raw:
        ed = _edges(toks, c, dur, db, ref, mask)
        if ed is None:
            continue
        ed = _fix_kept(toks, c, *ed)
        if ed is None:
            continue
        kind = _KIND[c.category]
        level = _LEVEL[c.confidence]
        recs.append({"_c": c, "_i0": c.i0, "_i1": c.i1, "kind": kind, "level": level,
                     "start": round(ed[0], 3), "end": round(ed[1], 3),
                     "enabled": enabled_for(level, sens)})
    # grupos: cortes seguidos (sin palabras conservadas entre ellos) = una cadena de intentos
    recs.sort(key=lambda r: (r["_i0"], r["_i1"]))
    groups: list[list[dict]] = []
    for r in recs:
        if groups and r["_i0"] <= groups[-1][-1]["_i1"]:
            p = groups[-1][-1]
            if r["_i0"] == p["_i1"] and p["start"] < p["end"] <= r["end"] - MIN_CUT:
                r["start"] = p["end"]           # intentos seguidos: sin astilla entre los dos cortes
            groups[-1].append(r)
        else:
            groups.append([r])
    used: set = set()
    out: list[dict] = []
    for gi, g in enumerate(groups, 1):
        last_i1 = g[-1]["_i1"]
        nxt_i0 = next((h[0]["_i0"] for h in groups[gi:]), len(toks))
        if last_i1 < len(toks):
            k0, k1 = _keep_span(toks, last_i1, nxt_i0)
            keep_ref = {"start": round(toks[k0].start, 3), "end": round(toks[k1 - 1].end, 3)}
            kept = " ".join(t.raw for t in toks[k0:min(k1, k0 + KEPT_WORDS)])
        else:
            keep_ref, kept = None, ""
        for r in g:
            c = r.pop("_c")
            i0, i1 = r["_i0"], r["_i1"]
            w0 = toks[i0].wi
            w1 = toks[i1 - 1].wi
            a_id = toks[i0].start
            b_id = toks[i1].start if i1 < len(toks) else toks[i1 - 1].end
            # la toma que se queda de este corte: la del final de la cadena (la de su propio
            # siguiente arranque para una autocorreccion o un tartamudeo)
            if r["kind"] in ("autocorreccion", "tartamudeo") and i1 < len(toks):
                ks0, ks1 = _keep_span(toks, i1, nxt_i0 if i1 >= last_i1 else i1 + 30)
                kref = {"start": round(toks[ks0].start, 3), "end": round(toks[ks1 - 1].end, 3)}
                ktxt = " ".join(t.raw for t in toks[ks0:min(ks1, ks0 + KEPT_WORDS)])
            else:
                kref, ktxt = keep_ref, kept
            text = " ".join(toks[k].raw for k in range(i0, i1))
            rec = {"id": _cut_id(r["kind"], a_id, b_id, used), "start": r["start"], "end": r["end"],
                   "enabled": r["enabled"], "src": "takes", "kind": r["kind"],
                   "conf": round(float(c.conf), 2), "level": r["level"],
                   "reason": _reason(r["kind"], c, kref["start"] if kref else None, ktxt),
                   "text": _clip(text), "kept_text": _clip(ktxt, 160),
                   "keep_ref": dict(kref) if kref else None, "group": f"tg_{gi}",
                   "w0": int(w0), "w1": int(w1), "_i0": i0, "_i1": i1}
            out.append(rec)
    _frag_guard(toks, out, existing_cuts, dur)
    for r in out:
        r.pop("_i0", None)
        r.pop("_i1", None)
        if r["end"] - r["start"] < MIN_CUT:
            r["enabled"] = False
            r["guard"] = True
    out.sort(key=lambda r: (r["start"], r["end"]))
    meta = {"version": 1, "sensitivity": sens, "topic": None, "summary": None, "sections": [],
            "stats": stats(out), "llm": False, "ms": int(round((time.time() - t0) * 1000))}
    return {"cuts": out, "meta": meta}


# ----------------------------------------------------------------------------- plan
def merge(old_cuts: list[dict] | None, take_cuts: list[dict] | None) -> list[dict]:
    """Cortes del plan con la deteccion nueva: se conservan los silencios, manuales, 'ai' y las
    tomas que el usuario toco (user: true, o edges: true si movio sus bordes); las demas tomas se
    reemplazan. Idempotente."""
    old = [c for c in old_cuts or [] if isinstance(c, dict)]
    keep = [c for c in old if not _is_take(c)]
    user = [c for c in old if _is_take(c) and (c.get("user") or c.get("edges"))]
    uid = {c.get("id") for c in user}
    new = [dict(c) for c in take_cuts or [] if isinstance(c, dict) and c.get("id") not in uid]
    new.sort(key=lambda c: (float(c.get("start", 0)), float(c.get("end", 0)), str(c.get("id"))))
    return keep + user + new


def apply_sensitivity(cuts: list[dict] | None, sensitivity: str) -> list[dict]:
    """Re-evalua `enabled` de los cortes de tomas que el usuario no toco (copia); uno apagado por
    seguridad (guard) sigue apagado."""
    out = []
    for c in cuts or []:
        if _is_take(c) and not c.get("user") and c.get("level") in LEVELS:
            c = dict(c, enabled=enabled_for(c["level"], sensitivity) and not c.get("guard"))
        out.append(c)
    return out


def stats(cuts: list[dict] | None) -> dict:
    """Resumen de los cortes de tomas: n, habilitados, s quitados (union), sugerencias y por tipo
    (n = todos los de ese tipo, s = segundos de los habilitados)."""
    tk = [c for c in cuts or [] if _is_take(c)]
    en = [c for c in tk if c.get("enabled", True) is not False]
    by: dict = {}
    for c in tk:
        k = c.get("kind") or "retoma"
        d = by.setdefault(k, {"n": 0, "s": 0.0})
        d["n"] += 1
    for k in by:
        by[k]["s"] = round(_union_len(_intervals([c for c in en if (c.get("kind") or "retoma") == k])), 2)
    return {"n": len(tk), "enabled": len(en), "removed_s": round(_union_len(_intervals(en)), 2),
            "suggested": sum(1 for c in tk if c.get("enabled", True) is False and not c.get("user")),
            "by_kind": by}


def plan_words(plan: dict | None) -> list[dict]:
    """Palabras del plan (frases aplanadas) como [{text, start, end}]."""
    out = []
    for ph in (plan or {}).get("phrases") or []:
        if not isinstance(ph, list):
            continue
        for w in ph:
            if isinstance(w, dict) and "start" in w and "end" in w:
                out.append({"text": str(w.get("text", "")), "start": w["start"], "end": w["end"]})
    return out


def _word_cut(c: dict) -> bool:
    """Corte que quita palabras a proposito (tomas, manual, del usuario)."""
    return _is_take(c) or bool(c.get("manual")) or c.get("src") in ("user", "manual")


def effective_words(words: list[dict] | None, cuts: list[dict] | None) -> list[dict]:
    """Palabras que se quedan en el video: fuera de los cortes de tomas/manuales habilitados (por
    su punto medio) y no enteramente dentro de un silencio cortado. Con los tiempos crudos de
    Whisper una palabra estirada cruza silencios cortados sin dejar de decirse: el punto medio
    solo manda en los cortes que quitan palabras a proposito."""
    wc = _intervals(cuts, _word_cut)
    sc = _intervals(cuts, lambda c: not _word_cut(c) and c.get("src") != "ai")
    out = []
    for w in words or []:
        try:
            a, b = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        m = (a + b) / 2
        if any(s < m < e for s, e in wc):
            continue
        if any(s - 1e-3 <= a and b <= e + 1e-3 for s, e in sc):
            continue
        out.append(w)
    return out


# ----------------------------------------------------------------------------- tema (LLM)
_TOPIC_SCHEMA = {
    "type": "object",
    "properties": {
        "tema": {"type": "string"},
        "resumen": {"type": "string"},
        "secciones": {"type": "array", "items": {
            "type": "object",
            "properties": {"start_word": {"type": "integer"}, "titulo": {"type": "string"}},
            "required": ["start_word", "titulo"]}},
    },
    "required": ["tema", "resumen", "secciones"],
}
_TOPIC_SYS = ("Eres editor de video. Recibes el inicio de cada oracion de la transcripcion de un video "
              "en espanol, con el indice de su primera palabra entre corchetes. Responde SOLO JSON: "
              "\"tema\" = de que trata el video en 2 a 5 palabras (el producto, servicio o asunto "
              "concreto; nunca un saludo ni el nombre del canal); \"resumen\" = 1 o 2 oraciones; "
              "\"secciones\" = de 3 a 8 partes que cubren TODO el video en orden, de la primera a la "
              "ultima oracion, cada una con \"start_word\" (el indice entre corchetes de la oracion "
              "donde empieza) y \"titulo\" (2 a 6 palabras).")


def _topic_lines(words: list[dict]) -> list[tuple[int, str]]:
    """(indice de la primera palabra, primeras palabras) por oracion."""
    sents: list[list[int]] = []
    cur: list[int] = []
    for k, w in enumerate(words):
        txt = str(w.get("text", "")).strip()
        if not txt:
            continue
        if cur:
            try:
                gap = float(w["start"]) - float(words[cur[-1]]["end"])
            except (KeyError, TypeError, ValueError):
                gap = 0.0
            if gap >= 0.8 and len(cur) >= 4:
                sents.append(cur)
                cur = []
        cur.append(k)
        if re.search(r"[.!?…]$", txt) or len(cur) >= 40:
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    if not sents:
        return []
    per = max(6, min(TOPIC_WORDS, TOPIC_MAX_WORDS // len(sents)))
    if per * len(sents) > TOPIC_MAX_WORDS:
        # muchisimas oraciones: se juntan de a varias (una linea por grupo)
        g = -(-per * len(sents) // TOPIC_MAX_WORDS)
        sents = [sum(sents[k:k + g], []) for k in range(0, len(sents), g)]
    return [(s[0], " ".join(str(words[k]["text"]).strip() for k in s[:per])) for s in sents]


def _clean_str(s, n: int) -> str:
    s = " ".join(str(s or "").replace("\n", " ").split()).strip().strip("\"'«»“”")
    return s[:n].rstrip() if len(s) > n else s


def topic(words: list[dict] | None, timeout: float = TOPIC_TIMEOUT) -> dict | None:
    """Tema, resumen y secciones del video con el LLM local (una llamada, 4096 de contexto, sin
    razonamiento, temperatura 0). {"topic", "summary", "sections": [{start, title}]} o None si el
    LLM no esta, tarda mas de `timeout` (tope duro: la llamada corre en un hilo) o responde mal."""
    ws = [w for w in words or [] if isinstance(w, dict) and str(w.get("text", "")).strip()]
    if len(ws) < 8:
        return None
    t0 = time.time()
    try:
        from motion import director as D
    except Exception:  # noqa
        return None
    try:
        if not D.llm_available(timeout=min(2.0, max(0.5, timeout / 10))):
            return None
    except Exception:  # noqa
        return None
    lines = _topic_lines(ws)
    if not lines:
        return None
    user = "Transcripcion (inicio de cada oracion):\n" + "\n".join(f"[{k}] {t}" for k, t in lines)
    msgs = [{"role": "system", "content": _TOPIC_SYS}, {"role": "user", "content": user}]
    left = timeout - (time.time() - t0)
    if left < 2.0:
        return None
    box: dict = {}

    def run():
        try:
            box["raw"] = D._ollama_chat(msgs, fmt=_TOPIC_SCHEMA, num_ctx=4096, temperature=0.0,
                                        num_predict=400, timeout=left)
        except Exception as e:  # noqa
            box["err"] = e
    th = threading.Thread(target=run, name="takes-topic", daemon=True)
    th.start()
    th.join(timeout=left + 0.5)
    raw = box.get("raw")
    if th.is_alive() or not raw:
        if th.is_alive() or box.get("err") is not None:
            print(f"   tema: sin respuesta del LLM ({box.get('err') or 'tiempo agotado'})")
        return None
    d = D._json_loose(raw)
    if not d:
        return None
    tema = _clean_str(d.get("tema"), 80).rstrip(".")
    if not tema:
        return None
    if len(tema.split()) > 8:
        tema = " ".join(tema.split()[:8])
    secs, last = [], -1e9
    # secciones pegadas (el modelo a veces parte una idea en dos seguidas) no son secciones
    span = float(ws[-1]["end"]) - float(ws[0]["start"])
    min_gap = max(3.0, 0.01 * span)
    for s in d.get("secciones") or []:
        if not isinstance(s, dict):
            continue
        try:
            k = int(s.get("start_word"))
        except (TypeError, ValueError):
            continue
        title = _clean_str(s.get("titulo"), 60).rstrip(".")
        if not title or not (0 <= k < len(ws)):
            continue
        t = round(float(ws[k]["start"]), 2)
        if t < last + min_gap:
            continue
        secs.append({"start": t, "title": title})
        last = t
    return {"topic": tema, "summary": _clean_str(d.get("resumen"), 400) or None, "sections": secs[:12]}


if __name__ == "__main__":
    import json
    import sys
    data = json.load(open(sys.argv[1]))
    ws = [{"text": s["text"].strip(), "start": s["offsets"]["from"] / 1000.0,
           "end": s["offsets"]["to"] / 1000.0} for s in data["transcription"] if s["text"].strip()]
    res = detect(ws, max(w["end"] for w in ws), sensitivity=sys.argv[2] if len(sys.argv) > 2 else "normal")
    for c in res["cuts"]:
        print(f"{fmt_time(c['start'])}-{fmt_time(c['end'])} {c['end'] - c['start']:5.1f}s {c['kind']:16s} "
              f"{c['level']:5s} {'ON ' if c['enabled'] else 'off'} | {c['text'][:90]}")
    print(json.dumps(res["meta"], ensure_ascii=False))
