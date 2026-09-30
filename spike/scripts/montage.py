#!/usr/bin/env python3
"""
Armar con IA: auto-montaje de TODA una sesion de grabacion, con guion o sin el.

El usuario selecciona todos los clips de una sesion (+ un guion opcional). Cada clip llega transcrito
por separado (palabras con tiempos de la FUENTE; nunca se concatenan clips para transcribir) y con
sus cortes de tomas internos (takes.detect). Este modulo es puro y determinista: no llama ffmpeg
ni la red (el LLM es opcional, solo desempata y nunca da tiempos) y decide:

  1. Orden de grabacion: fecha de creacion (ISO 8601) > nombre natural (IMG_2 < IMG_10) > orden
     de subida.
  2. Con guion: cada intento de cada clip se alinea contra el guion COMPLETO (Smith-Waterman local
     multi-hit, vectorizado; similitud difusa: igual 1.0, misma raiz 0.8, casi igual 0.6;
     numeros normalizados "15 mil" = "15,000" = 15000; compuestos "Mercado Libre" = "MercadoLibre").
     Una programacion dinamica en orden del guion cubre cada tramo (entre signos de puntuacion)
     con la toma que minimiza
         L * (1 - fidelidad^2 * precision * limpieza * audio * recencia) + entrega
       + costo por segmento/empalme (mas caro a media linea, sin pausa o misma camara)
       + costo del guion sin cubrir.
     Asi una toma cubre varias lineas, una linea se arma con dos tomas (una toma parcial de otra
     camara), el mismo clip se usa dos veces y el orden sigue al guion. Empates: la mas reciente.
     Casi-empates con distinta redaccion: juez LLM opcional (dos ordenes, deben coincidir).
     Intercalado de angulos (intercut=True): si hay dos tomas completas de la misma frase, con
     las mismas palabras, desde camaras distintas, se alterna A/B (o A/B/A) cortando en una pausa
     de las dos; tambien completa con una toma parcial de otra camara grabada justo para un
     principio/final de la frase.
  3. Sin guion: infiere un "Guion detectado" editable. Alineacion multiple progresiva de todos
     los intentos en orden de grabacion (anclas de >= 4 palabras + LCS entre anclas); gana la
     redaccion de la ULTIMA toma limpia y fluida (una ruta nueva con titubeo o de una toma
     "sospechosa" -groseria, comentario meta, charla- no reemplaza a una limpia); lo dicho una
     sola vez junto a material repetido solo entra si pasa la regla de enriquecimiento (oracion
     completa, >= 5 palabras / 3 de contenido, en microfono, no es parafrasis, ni pregunta ni
     comentario meta). Despues se arma igual que con guion (source "detected").
  4. Bordes: en limites de palabra, ~0.30 s antes y ~0.27 s despues, pegados a la ventana de
     menor energia, nunca dentro de una palabra (el inicio/fin real de la voz se busca en el
     audio: Whisper deriva). Cada toma empieza en su primera palabra alineada al tramo del guion
     que cubre: lo que repite el final del segmento anterior ("...la receta de la abuela." |
     "La receta de la abuela. Lleva elote...") nunca entra, y lo que el GUION repite (anadiplosis:
     "...con una pregunta." | "Una pregunta muy simple...") se conserva.
     Sin audio funciona igual, solo con los tiempos de las palabras.
  5. Volumen por segmento ("vol", lineal) desde el nivel de voz medido: todos suenan parejo. Cada
     candidato trae tambien su "vol" con la misma referencia (una toma cambiada con "Usar esta
     toma" suena igual que sus vecinas) y su "conf" (la confianza si fuera la elegida).
  6. Todo lo que no entra queda en "discarded" con su motivo; las lineas sin toma en
     "uncovered"; el material fuera de guion que podria aportar, en "extra" (solo entra a la
     linea de tiempo si el juez LLM lo aprueba dos veces).

API
  build(clips, script=None, *, intercut=True, glossary=None, llm=None) -> plan["montage"]
      clips = [{"sid", "name", "created", "dur", "words": [{text,start,end}], "audio": ruta wav
                16 kHz | None, "setup": int | None, "takes": [cortes de takes.detect] | None}]
      (sin "takes" se corre takes.detect aqui). llm: None (sin juez), callable(prompt) -> str | None
      o True (= local_llm()). Con "frames"/"thumb" y sin "setup" se agrupan las camaras aqui.
  parse_script(text) -> {"sections": [{"id", "lines": [ids]}], "lines": [{"id", "text"}]}
      (una linea de texto = una linea del guion; linea en blanco = seccion nueva).
  recording_order(clips) -> [indices]           orden de grabacion
  setups(clips, threshold) -> [int | None]      grupos de camara con 1-3 cuadros chicos por clip
  frames_of(path, dur) -> [arreglos]            (ayuda con ffmpeg para quien llama; build no lo usa)
  glossary_terms(text) -> [str]                 nombres propios y numeros del guion (para whisper)
  local_llm(timeout) -> callable | None         el LLM local (ollama) como juez si/no

Contrato de salida (plan["montage"]):
  {"version": 1, "order": [{"sid", "name", "rec", "created"}],
   "script": {"source": "user"|"detected", "text", "sections": [{"id", "lines"}],
              "lines": [{"id", "text"}]},
   "slots": [{"id", "lines": [ids], "text", "chosen": cand_id,
              "cands": [{"id", "sid", "in", "out", "w0", "w1", "text", "score", "fidelity",
                         "coverage", "flags": [], "reason", "setup", "conf",
                         "vol", "level_db" (estos dos solo con audio)}]}],
   "timeline": [{"slot", "cand", "sid", "in", "out", "start", "vol", "join"}],
   "discarded": [{"sid", "in", "out", "text", "why", "reason"}],
   "uncovered": [line ids], "extra": [{"sid", "in", "out", "after", "why", "text", "kept", "vol"?}],
   "stats": {...}, "ms": int}
  join: None en el primero; "section" (empieza seccion: se permite transicion), "line" (empieza
  linea: corte seco) o "splice" (corte a media linea: corte seco, a lo mas un salto de zoom).
  why: retoma | blooper | fuera_de_guion | incompleta | sin_audio. setup: grupo de camara (si no
  se conoce, un id propio por clip). w0/w1: indices en la lista "words" del clip. conf: la
  confianza si esa toma fuera la elegida (misma escala que timeline.conf). vol: ganancia con la
  MISMA referencia que la linea de tiempo (una toma cambiada suena como sus vecinas).
"""
from __future__ import annotations

import math
import re
import time
import wave
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache

import numpy as np

import takes as TK

VERSION = 1

# ----------------------------------------------------------------------------- parametros
P = {
    # alineacion con el guion (Smith-Waterman)
    "sw_match": 2.0, "sw_mis": -1.0, "sw_gap_clip": -0.8, "sw_gap_script": -1.2,
    "hit_min_score": 5.0, "hit_min_matched": 3,
    "short_pause": 0.30,       # s: una linea de 1-2 palabras dicha sola va entre pausas (o puntuacion)
    "cand_min_cov": 0.6,       # cada linea (o pedazo de linea) de un candidato cubierta >= esto
    # candidato: L * (1 - F^2 * Pr * C * A * R) + entrega
    "clean_flub": 0.4,         # C si el tramo tiene palabras marcadas (retoma, groseria...)
    "clean_superseded": 0.85,  # C si el mismo clip vuelve a decir el tramo despues
    "clean_reaction": 0.8,     # C si tras la toma el orador reacciona (groseria, "me equivoque")
    "reaction_s": 4.0,         # ... dentro de estos segundos
    "audio_free_db": 6.0,      # A: dB bajo la referencia (p75 de la voz de la sesion) sin castigo...
    "audio_slope": 0.01,       # ...y luego -1 % por dB...
    "audio_floor": 0.88,       # ...hasta este piso (una toma sin micro completa > una incompleta)
    "weak_audio_db": 8.0,      # "sin_microfono" desde aqui
    "recency_eps": 0.01,       # R: la toma mas vieja pierde 1 % (desempate suave)
    "filler_w": 0.5,           # muletillas cuentan media palabra extra
    "w_hesitation": 1.0, "hes_pause": 0.30, "hes_cap": 1.5,   # pausa a media frase (s)
    "tighten_over": 0.70, "tighten_to": 0.40,                  # los silencios luego se recortan
    "w_rush_tok": 0.3, "rush_ratio": 1.15,                     # dicha muy rapido
    # desempates
    "tie_eps": 0.10,
    "tie_margin": 0.5,         # casi-empate con distinta redaccion (una forma de palabra ~0.4) -> juez
    # empalmes
    "cut_base": 0.5, "cut_inner": 0.3, "cut_midline": 0.6, "cut_nopause": 1.0, "nopause_s": 0.20,
    "cut_same_setup": 0.4,     # empalme a media linea entre dos tomas de la misma camara
    "miss_base": 2.0, "miss_tok": 1.5,
    # bordes
    "pre_roll": 0.30, "post_roll": 0.27, "min_pre": 0.06, "min_post": 0.06,
    "onset_back": 0.45, "offset_fwd": 0.45, "neighbour_guard": 0.03,
    # intercalado de angulos
    "ic_min": 6.0, "ic_aba": 9.0, "ic_part": 2.0, "ic_pause": 0.35, "ic_tol": 0.3,
    "ic_level_db": 4.0,
    # volumen
    "max_gain_up_db": 15.0,    # lo mas que sube una toma (sin microfono: ~15 dB bajo las demas; = compose)
    "max_gain_down_db": 12.0,  # lo mas que baja
    # enriquecimiento (fuera de guion que aporta)
    "ex_words": 6, "ex_content": 3, "ex_mic_db": 6.0,
    # alternativas por slot
    "max_alts": 6,
}

# sin guion (alineacion multiple): parametros del prototipo B validados en una sesion real
MIN_ANCHOR = 4             # palabras de un ancla entre clips (menos = anclas falsas "en el centro")
MIN_ANCHOR_SCORE = 6.0     # contenido 2, funcion 1
SWB_MIS, SWB_GAP_T, SWB_GAP_M = -1.0, -1.0, -0.35
HES = 0.60                 # s: pausa a media clausula (las otras tomas no pausan ahi) = titubeo
PHRASE_PAUSE = 0.25
LINE_PAUSE = 0.50          # s: pausa que parte oraciones
QUESTION_RE = re.compile(r"[¿?]")

# charla con el equipo sobre la grabacion (sin guion, dicha una vez en la cabeza o la cola de un clip):
# palabras de contenido que solo hablan de la toma, y frases tipicas (normas sin acentos)
CHATTER_MAX = 6
CHATTER_WORDS = {
    "grabando", "grabar", "grabas", "graba", "grabamos", "grabo", "grabe", "grabado", "grabada", "grabacion",
    "quedo", "salio", "toma", "tomas", "repito", "repetir", "repetimos", "repite", "repitela", "repitelo",
    "otra", "vez", "doy", "empiezo", "empezamos", "empiezas", "arranco", "arrancamos", "dale", "camara",
    "micro", "microfono", "audio", "sonido", "escucha", "escucho", "escuchas", "oye", "oyo", "enfoque",
    "enfocado", "encuadre", "accion", "corte", "corta", "cortamos",
}
CHATTER_SIGNALS = {"bien", "asi", "ya"}          # funcionales que, en una pregunta corta, son charla
# frases que, aun sin signo de pregunta, solo son charla sobre la toma ("¿Así está bien?" o "¿Se ve bien?"
# solo cuentan como pregunta: "Y está bien así." puede ser contenido)
CHATTER_PHRASES = [("esta", "grabando"), ("estas", "grabando"), ("quedo", "bien"), ("salio", "bien"),
                   ("le", "doy"), ("otra", "vez"), ("la", "repito"), ("lo", "repito"), ("se", "escucha"),
                   ("se", "oye")]


def _is_chatter(toks) -> bool:
    """Oracion corta (<= CHATTER_MAX palabras) que es charla sobre la toma: todas sus palabras de
    contenido hablan de la grabacion (CHATTER_WORDS o un comentario meta de takes.META) y es una
    pregunta con alguna de ellas ("¿Quedó bien?", "¿Le doy?", "¿Así está bien?") o trae una frase
    tipica o un comentario meta ("Perdón, repito."). Una pregunta retorica con contenido ("¿Y las
    ventas?", "¿Por qué?") no lo es."""
    if not toks or len(toks) > CHATTER_MAX:
        return False
    ns = [t.n for t in toks]
    meta_words = {w for pat in TK.META for w in pat}
    content = [t.n for t in toks if t.content]
    if not all(n in CHATTER_WORDS or n in meta_words for n in content):
        return False
    phrase = any(tuple(ns[k:k + len(p)]) == tuple(p) for p in CHATTER_PHRASES + TK.META for k in range(len(ns)))
    question = any("?" in t.raw or t.lead_q for t in toks)
    signal = bool(content) or any(n in CHATTER_SIGNALS for n in ns)
    return phrase or (question and signal)

_TRAIL = "\"'»”’)]"
_TERMINAL = ("stop", "ellipsis")


# ----------------------------------------------------------------------------- numeros
_UNITS = {"cero": 0, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
          "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
          "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
          "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintiun": 21, "veintidos": 22,
          "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
          "veintisiete": 27, "veintiocho": 28, "veintinueve": 29}
_TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
         "ochenta": 80, "noventa": 90}
_HUND = {"cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200, "trescientos": 300,
         "trescientas": 300, "cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500,
         "quinientas": 500, "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
         "setecientas": 700, "ochocientos": 800, "ochocientas": 800, "novecientos": 900,
         "novecientas": 900}
_MULT = {"mil": 1000, "millon": 10 ** 6, "millones": 10 ** 6}


def _numval(n: str):
    if n.isdigit():
        return int(n), "d"
    for tab, kind in ((_UNITS, "u"), (_TENS, "t"), (_HUND, "h")):
        if n in tab:
            return tab[n], kind
    return None, None


# ----------------------------------------------------------------------------- tokens
@dataclass
class Tok:
    i: int
    wi0: int
    wi1: int
    raw: str
    n: str
    s: str
    start: float
    end: float
    punct: str = ""
    lead_q: bool = False
    filler: bool = False
    content: bool = False
    pause_before: float = 0.0


def _punct(raw: str) -> str:
    r = str(raw).rstrip().rstrip(_TRAIL)
    return TK._punct(r) if r else ""


def _collapse_numbers(toks: list[Tok]) -> list[Tok]:
    """Numeros dichos o escritos -> un token con el valor ("20 mil" -> "20000", "doscientos" ->
    "200", "mil" -> "1000"). 'un'/'una' valen 1 solo antes de mil/millon."""
    out: list[Tok] = []
    k = 0
    while k < len(toks):
        n = toks[k].n
        v, kind = _numval(n)
        nxt = toks[k + 1].n if k + 1 < len(toks) else ""
        if v is None and n in ("un", "una") and nxt in _MULT:
            v, kind = 1, "u"
        total, cur, last_mag, j = 0, 0, 0, k + 1
        if v is None and n in _MULT:
            total, cur, last_mag = _MULT[n], 0, _MULT[n]
        elif v is None:
            out.append(toks[k])
            k += 1
            continue
        else:
            cur, last_mag = v, v
        while j < len(toks):
            m = toks[j].n
            if m in _MULT:
                total += max(cur, 1) * _MULT[m]
                cur = 0
                last_mag = _MULT[m]
                j += 1
                continue
            if m == "y" and j + 1 < len(toks):
                v2, _ = _numval(toks[j + 1].n)
                if v2 is not None and v2 < 10 and cur % 100 >= 30 and cur % 10 == 0:
                    cur += v2
                    last_mag = v2
                    j += 2
                    continue
                break
            v2, k2 = _numval(m)
            if v2 is None or k2 == "d" or v2 >= last_mag:
                break
            cur += v2
            last_mag = v2
            j += 1
        total += cur
        first, last = toks[k], toks[j - 1]
        out.append(Tok(first.i, first.wi0, last.wi1, " ".join(t.raw for t in toks[k:j]), str(total),
                       str(total), first.start, last.end, last.punct, first.lead_q, False, True,
                       first.pause_before))
        k = j
    return out


def _clip_toks(words, gloss: dict | None = None) -> list[Tok]:
    """Palabras de un clip -> tokens normalizados (takes.tokenize + numeros + glosario)."""
    toks = []
    for t in TK.tokenize(words):
        toks.append(Tok(0, t.wi, t.wi, t.raw, t.n, t.s, float(t.start), float(t.end), _punct(t.raw),
                        t.lead_q, t.filler, t.content, float(t.pause_before)))
    toks = _collapse_numbers(toks)
    if gloss:
        for t in toks:
            g = gloss.get(t.n)
            if g is None and len(t.n) >= 4 and not t.n.isdigit():
                for term in gloss.values():
                    if _sim(t.n, term) >= 0.6:
                        g = term
                        break
                gloss[t.n] = g if g is not None else t.n    # cache (identidad = sin cambio)
            if g and g != t.n:
                t.n, t.s = g, TK.stem(g)
    for k, t in enumerate(toks):
        t.i = k
    return toks


def _text_toks(line: str) -> list[Tok]:
    """Texto (guion) -> tokens normalizados sin tiempos."""
    toks = []
    for raw in str(line).split():
        n = TK.norm(raw)
        if not n:
            continue
        filler = n in TK.FILLERS
        toks.append(Tok(len(toks), -1, -1, raw, n, TK.stem(n), 0.0, 0.0, _punct(raw), raw[:1] in "¿¡",
                        filler, (n not in TK.STOP) and len(n) >= 3 and not filler))
    toks = _collapse_numbers(toks)
    for k, t in enumerate(toks):
        t.i = k
    return toks


def _sim(a: str, b: str) -> float:
    """Similitud de dos palabras normalizadas: igual 1.0, misma raiz 0.8, casi igual 0.6."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if la < 4 or lb < 4 or a.isdigit() or b.isdigit():
        return 0.0
    if TK.stem(a) == TK.stem(b):
        return 0.8
    return 0.6 if _ratio(a, b) >= 0.8 else 0.0


@lru_cache(maxsize=1 << 16)
def _ratio_c(a: str, b: str) -> float:
    sm = SequenceMatcher(None, a, b)
    if sm.real_quick_ratio() < 0.8 or sm.quick_ratio() < 0.8:
        return 0.0
    return sm.ratio()


def _ratio(a: str, b: str) -> float:
    """Parecido de letras (0..1); 0 si las longitudes ya impiden llegar a 0.8 (sin calcular)."""
    if 2.0 * min(len(a), len(b)) / max(1, len(a) + len(b)) < 0.8:
        return 0.0
    return _ratio_c(a, b) if a <= b else _ratio_c(b, a)


def _gloss_map(glossary) -> dict:
    """Glosario ("Tienda Luna, Mercado Libre, Monterrey") -> {norma: norma canonica} de sus palabras
    sueltas de >= 4 letras (Whisper escribe variantes de los nombres propios)."""
    if not glossary:
        return {}
    terms = glossary if isinstance(glossary, (list, tuple)) else re.split(r"[,;\n]+", str(glossary))
    out = {}
    for term in terms:
        for w in str(term).split():
            n = TK.norm(w)
            if len(n) >= 4 and not n.isdigit():
                out[n] = n
    return out


def _natkey(s: str):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(s or ""))]


def _parse_created(s) -> float | None:
    if not s:
        return None
    s2 = str(s).strip().replace("Z", "+00:00")
    s2 = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", s2)
    try:
        return datetime.fromisoformat(s2).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def recording_order(clips: list[dict]) -> list[int]:
    """Indices de `clips` en orden de grabacion: fecha de creacion, luego nombre natural, luego
    el orden de subida (los clips sin fecha van despues de los fechados)."""
    keys = []
    for k, d in enumerate(clips):
        t = _parse_created(d.get("created"))
        keys.append((0 if t is not None else 1, t or 0.0, _natkey(d.get("name") or d.get("sid") or ""), k))
    return sorted(range(len(clips)), key=keys.__getitem__)


# ----------------------------------------------------------------------------- guion
def _split_long(line: str) -> list[str]:
    """Una linea muy larga (un parrafo pegado) se parte en oraciones."""
    if len(line.split()) <= 40:
        return [line]
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", line) if p.strip()]
    return parts or [line]


def parse_script(text: str | None) -> dict:
    """Guion -> {"sections": [{"id": 1, "lines": ["L01", ...]}], "lines": [{"id", "text"}]}.
    Cada linea de texto es una linea del guion; una linea en blanco empieza seccion."""
    raw_secs: list[list[str]] = [[]]
    for raw in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = " ".join(raw.split())
        if not s:
            if raw_secs[-1]:
                raw_secs.append([])
            continue
        raw_secs[-1].extend(_split_long(s))
    raw_secs = [s for s in raw_secs if s]
    total = sum(len(s) for s in raw_secs)
    width = 2 if total <= 99 else len(str(total))
    lines, sections, k = [], [], 0
    for si, sec in enumerate(raw_secs, 1):
        ids = []
        for s in sec:
            k += 1
            lid = f"L{k:0{width}d}"
            lines.append({"id": lid, "text": s})
            ids.append(lid)
        sections.append({"id": si, "lines": ids})
    return {"sections": sections, "lines": lines}


class _Script:
    """Guion tokenizado: limites de corte tras puntuacion y en fin de linea."""

    def __init__(self, text: str, parsed: dict | None = None):
        self.text = text
        self.parsed = parsed or parse_script(text)
        sec_of_line = {lid: s["id"] for s in self.parsed["sections"] for lid in s["lines"]}
        self.lines = []
        self.toks: list[Tok] = []
        self.line_of: list[int] = []
        for li, L in enumerate(self.parsed["lines"]):
            t0 = len(self.toks)
            lt = _text_toks(L["text"])
            if lt:
                lt[-1].punct = lt[-1].punct or "stop"
            for t in lt:
                t.i = len(self.toks)
                self.toks.append(t)
                self.line_of.append(li)
            self.lines.append({"id": L["id"], "text": L["text"], "section": sec_of_line.get(L["id"], 1),
                               "t0": t0, "t1": len(self.toks)})
        self.n = len(self.toks)
        self.line_start = {L["t0"] for L in self.lines if L["t1"] > L["t0"]}
        self.sec_start = set()
        prev = None
        for L in self.lines:
            if L["t1"] > L["t0"]:
                if L["section"] != prev:
                    self.sec_start.add(L["t0"])
                prev = L["section"]
        self.bounds = sorted({0, self.n} | {t.i + 1 for t in self.toks if t.punct} | self.line_start)
        # indices para el SW: filas (1-based) por norma y por par de normas pegadas (compuestos)
        self.vocab: dict[str, list[int]] = defaultdict(list)
        for t in self.toks:
            self.vocab[t.n].append(t.i)
        self.rows_eq = {n: np.asarray(ix, int) + 1 for n, ix in self.vocab.items()}
        r21: dict[str, list[int]] = defaultdict(list)
        for i in range(2, self.n + 1):
            r21[self.toks[i - 2].n + self.toks[i - 1].n].append(i)
        self.rows21 = {k: np.asarray(v, int) for k, v in r21.items()}
        self._simv: dict[str, np.ndarray] = {}

    def simvec(self, n: str) -> np.ndarray:
        v = self._simv.get(n)
        if v is None:
            v = np.zeros(self.n)
            for u, ix in self.vocab.items():
                s = _sim(n, u)
                if s > 0:
                    v[ix] = s
            self._simv[n] = v
        return v

    def lines_in(self, a: int, b: int) -> list[str]:
        out = []
        for i in range(max(0, a), min(b, self.n)):
            lid = self.lines[self.line_of[i]]["id"]
            if not out or out[-1] != lid:
                out.append(lid)
        return out

    def raw(self, a: int, b: int) -> str:
        return " ".join(t.raw for t in self.toks[a:b])

    def is_line_start(self, i: int) -> bool:
        return i <= 0 or i >= self.n or i in self.line_start

    def is_section_start(self, i: int) -> bool:
        return i <= 0 or i in self.sec_start


# ----------------------------------------------------------------------------- Smith-Waterman
_CODES = np.array([0, 1, 4, 5, 3], np.int8)    # stop, diagonal, 2:1, 1:2, salta palabra del clip


def _sw(diag: list, n: int, *, gap_col: float, gap_row: float, blocked_cols=None, blocked_rows=None,
        c21=None, c12=None, c21_score: float = 0.0, c12_score: float = 0.0,
        prefer_last_row: bool = False):
    """Smith-Waterman local columna por columna (columnas = palabras del clip; filas = guion o
    maestro, vectorizadas). diag[j]: vector (n,) del puntaje de emparejar fila i con columna j
    (> 0 coincide, < 0 no). La cadena vertical (saltar filas) es un maximo acumulado. Filas o
    columnas bloqueadas son barreras. c21[j]: filas i (1-based) donde dos filas pegadas = la
    palabra j (2:1); c12[j]: filas donde la fila = palabras j-1 + j (1:2).
    Devuelve (score, [(fila, columna, tipo)]) con tipo 1 diagonal, 4 2:1, 5 1:2."""
    m = len(diag)
    if not m or not n:
        return 0.0, []
    H = np.zeros((m + 1, n + 1))
    Bm = np.zeros((m + 1, n + 1), np.int8)
    kg = np.arange(n + 1, dtype=float) * gap_row
    NEG = -1e18
    br = None
    if blocked_rows is not None and np.any(blocked_rows):
        br = np.concatenate([[False], np.asarray(blocked_rows, bool)])
        seg = np.cumsum(br) * 1e7
    stack = np.empty((5, n + 1))
    for j in range(1, m + 1):
        if blocked_cols is not None and blocked_cols[j - 1]:
            continue
        prev = H[j - 1]
        stack[0] = 0.0
        stack[1, 0] = NEG
        stack[1, 1:] = prev[:-1] + diag[j - 1]
        stack[2] = NEG
        if c21 is not None and c21[j - 1] is not None:
            rows = c21[j - 1]
            stack[2, rows] = prev[rows - 2] + c21_score
        stack[3] = NEG
        if c12 is not None and j >= 2 and c12[j - 1] is not None and not (
                blocked_cols is not None and blocked_cols[j - 2]):
            rows = c12[j - 1]
            stack[3, rows] = H[j - 2][rows - 1] + c12_score
        stack[4] = prev + gap_col
        X = stack.max(0)
        code = _CODES[stack.argmax(0)]
        X[0] = 0.0
        code[0] = 0
        if br is not None:
            X[br] = 0.0
            code[br] = 0
            Hj = np.maximum.accumulate(X - kg + seg) - seg + kg
            Hj[br] = 0.0
        else:
            Hj = np.maximum.accumulate(X - kg) + kg
        code = np.where(Hj > X + 1e-9, 2, code).astype(np.int8)
        H[j] = Hj
        Bm[j] = code
    mx = float(H.max())
    if mx <= 1e-9:
        return 0.0, []
    if prefer_last_row:
        cells = np.argwhere(H >= mx - 1e-9)
        rmax = cells[:, 1].max()
        bj = int(cells[cells[:, 1] == rmax][:, 0].min())
        bi = int(rmax)
    else:
        bj, bi = np.unravel_index(int(np.argmax(H)), H.shape)
        bj, bi = int(bj), int(bi)
    pairs = []
    j, i = bj, bi
    while j > 0 and i > 0 and Bm[j, i] != 0:
        b = Bm[j, i]
        if b == 1:
            pairs.append((i - 1, j - 1, 1))
            i, j = i - 1, j - 1
        elif b == 4:
            pairs.append((i - 1, j - 1, 4))
            pairs.append((i - 2, j - 1, 4))
            i, j = i - 2, j - 1
        elif b == 5:
            pairs.append((i - 1, j - 1, 5))
            pairs.append((i - 1, j - 2, 5))
            i, j = i - 1, j - 2
        elif b == 2:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return mx, pairs


# ----------------------------------------------------------------------------- audio
def _read_pcm16(p: str) -> np.ndarray | None:
    """wav PCM de 16 bits -> mono 16 kHz float32; None si es otro formato (flotante, etc.)."""
    try:
        w = wave.open(p)
    except (wave.Error, EOFError):
        return None
    with w:
        if w.getsampwidth() != 2:
            return None
        ch, sr, nf = w.getnchannels(), w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(nf), np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(1)
    if sr != 16000 and len(x):
        idx = (np.arange(int(len(x) * 16000 / sr)) * sr / 16000).astype(int)
        x = x[np.clip(idx, 0, len(x) - 1)]
    return x


def _read_audio(path) -> np.ndarray | None:
    """Audio mono 16 kHz float32 (wav PCM16 directo; otro formato via edit._mono16k)."""
    if not path:
        return None
    p = str(path)
    try:
        x = _read_pcm16(p) if p.lower().endswith(".wav") else None
        if x is None:
            import edit as E
            x = np.asarray(E._mono16k(p), np.float32)
        return x
    except Exception as e:  # noqa: sin audio legible -> solo texto
        print(f"   montaje: sin audio legible ({p}: {e})")
        return None


def _energy_db(y: np.ndarray) -> np.ndarray | None:
    """dB por ventana de 20 ms con paso de 10 ms (ventana k centrada en k*0.01 + 0.01)."""
    hop = 160
    nfr = len(y) // hop
    if nfr < 4:
        return None
    e10 = np.sum(y[: nfr * hop].reshape(nfr, hop).astype(np.float64) ** 2, axis=1)
    e20 = np.concatenate([(e10[:-1] + e10[1:]) / (2 * hop), [e10[-1] / hop]])
    return 10.0 * np.log10(e20 + 1e-12)


# ----------------------------------------------------------------------------- clip
class _Clip:
    def __init__(self, d: dict, idx: int, rec: int, gloss: dict | None):
        self.d = d
        self.idx = idx
        self.rec = rec
        self.sid = str(d.get("sid") or f"clip{idx}")
        self.name = str(d.get("name") or self.sid)
        self.created = d.get("created")
        self.words = d.get("words") or []
        self.toks = _clip_toks(self.words, gloss)
        try:
            dur = float(d.get("dur") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        self.dur = max(dur, max((t.end for t in self.toks), default=0.0))
        s = d.get("setup")
        self.setup = int(s) if isinstance(s, (int, np.integer)) or (isinstance(s, str) and s.isdigit()) else None
        self.flag: list[str | None] = [None] * len(self.toks)
        self.cuts: list[dict] = []
        self.y = None
        self.db = None
        self.speech_db = None
        self.voice_thr = None
        self.tok_db: list[float | None] = [None] * len(self.toks)
        self.level: float | None = None
        self.hits: list = []
        self.claimed = [False] * len(self.toks)

    # ---- audio
    def prep_audio(self):
        self.y = _read_audio(self.d.get("audio"))
        if self.y is None or not len(self.y):
            self.y = None
            return
        self.dur = max(self.dur, len(self.y) / 16000.0)
        self.db = _energy_db(self.y)
        if self.db is None:
            self.y = None
            return
        db = self.db
        fr = [k for t in self.toks for k in range(max(0, self.t2k(t.start)), min(len(db), self.t2k(t.end) + 1))]
        self.speech_db = float(np.median(db[fr])) if fr else float(np.median(db))
        floor = float(np.percentile(db, 10))
        self.voice_thr = floor + 0.35 * (self.speech_db - floor)
        for t in self.toks:
            a, b = max(0, self.t2k(t.start)), min(len(db), self.t2k(t.end) + 1)
            seg = db[a:b] if b > a else db[max(0, a - 1):a + 1]
            self.tok_db[t.i] = float(np.percentile(seg, 90)) if len(seg) else None

    @staticmethod
    def t2k(t: float) -> int:
        return int(round((t - 0.01) / 0.01))

    @staticmethod
    def k2t(k: int) -> float:
        return k * 0.01 + 0.01

    def speech_level(self, t0: float, t1: float) -> float | None:
        """Mediana de dB de las ventanas con voz en [t0, t1] (ignora las pausas)."""
        if self.db is None:
            return None
        a, b = max(0, self.t2k(t0)), min(len(self.db), self.t2k(t1) + 1)
        seg = self.db[a:b]
        if not len(seg):
            return None
        v = seg[seg > self.voice_thr]
        return float(np.median(v)) if len(v) else float(np.median(seg))

    # ---- marcas de mala entrega dentro del clip
    def prep_flags(self, script_norms: set | None = None):
        cuts = self.d.get("takes")
        if cuts is None and self.toks:
            try:
                r = TK.detect(self.words, self.dur, sensitivity="normal", audio=self.y)
                cuts = r["cuts"]
            except Exception as e:  # noqa
                print(f"   montaje: takes.detect fallo en {self.name} ({e})")
                cuts = []
        self.cuts = [c for c in (cuts or []) if isinstance(c, dict)]
        for c in self.cuts:
            if not (c.get("enabled") or c.get("kind") == "error"):
                continue
            try:
                a, b = float(c["start"]), float(c["end"])
            except (KeyError, TypeError, ValueError):
                continue
            for t in self.toks:
                if a <= (t.start + t.end) / 2 <= b:
                    self.flag[t.i] = c.get("kind") or "retoma"
        # groserias y comentarios meta de varias palabras en cualquier lugar (los de una palabra,
        # como "corte" o "perdon", solo si takes.detect los marco: pueden ser contenido)
        ns = [t.n for t in self.toks]
        for pat in TK.EXPLETIVES + [p for p in TK.META if len(p) > 1]:
            if script_norms and all(w in script_norms for w in pat):
                continue
            L = len(pat)
            for k in range(len(ns) - L + 1):
                if tuple(ns[k:k + L]) == tuple(pat):
                    for q in range(k, k + L):
                        self.flag[q] = self.flag[q] or "error"
        self.removed = {k: f for k, f in enumerate(self.flag) if f}
        self.kept = [t.i for t in self.toks if t.i not in self.removed]
        self.kpos = {ti: k for k, ti in enumerate(self.kept)}
        kd = [self.tok_db[i] for i in self.kept if self.tok_db[i] is not None]
        self.level = float(np.median(kd)) if kd else None

    def text(self, a: int, b: int) -> str:
        return " ".join(t.raw for t in self.toks[a:b + 1])


# ----------------------------------------------------------------------------- juez LLM
class _Judge:
    """LLM opcional (callable(prompt) -> str | None) como juez si/no. Nunca da tiempos."""

    def __init__(self, llm, max_calls: int = 24):
        self.llm = llm if callable(llm) else None
        self.calls = 0
        self.max_calls = max_calls
        self.log: list[dict] = []

    def ask(self, prompt: str):
        if self.llm is None or self.calls >= self.max_calls:
            return None
        self.calls += 1
        t0 = time.time()
        try:
            out = self.llm(prompt)
        except Exception as e:  # noqa
            self.log.append({"q": prompt[-120:], "a": None, "err": str(e)[:100]})
            return None
        self.log.append({"q": prompt[-120:], "a": (str(out) if out is not None else None), "s": round(time.time() - t0, 2)})
        return None if out is None else str(out)

    @staticmethod
    def yes(ans):
        if ans is None:
            return None
        a = TK.strip_accents(str(ans)).strip().lower()
        a = re.sub(r"[^a-z ]", " ", a).split()
        if not a:
            return None
        if a[0] in ("si", "yes"):
            return True
        if a[0] == "no":
            return False
        return None

    def pair(self, script_text: str, a_text: str, b_text: str):
        """¿A es mas fiel y natural que B? En los dos ordenes; 'A', 'B' o None."""
        if self.llm is None:
            return None
        q = ("Eres editor de video. El guion dice:\n\"{s}\"\n\nToma 1: \"{x}\"\nToma 2: \"{y}\"\n\n"
             "¿La toma 1 es más fiel al guion y más natural que la toma 2? Responde solo sí o no.")
        r1 = self.yes(self.ask(q.format(s=script_text, x=a_text, y=b_text)))
        r2 = self.yes(self.ask(q.format(s=script_text, x=b_text, y=a_text)))
        if r1 is True and r2 is False:
            return "A"
        if r1 is False and r2 is True:
            return "B"
        return None

    def enriches(self, fragment: str, story: str):
        """True solo si las dos preguntas dicen si; False si las dos dicen no; None si no hay juez."""
        if self.llm is None:
            return None
        ctx = story[:2500]
        q1 = (f"Historia de un video: \"{ctx}\"\n\nFrase extra grabada en la sesión: \"{fragment}\"\n\n"
              "¿La frase extra le habla a la audiencia y aporta información nueva, clara y coherente con "
              "la historia (no es charla con el equipo, ni un comentario sobre la toma, ni un error, ni "
              "otra forma de decir algo que ya está)? Responde solo sí o no.")
        q2 = (f"Frase dicha una vez durante una grabación:\n\"{fragment}\"\n\nGuion del video:\n\"{ctx}\"\n\n"
              "¿Conviene agregar la frase al video final porque enriquece la historia para el público? "
              "Si es charla con el equipo, un comentario sobre cómo salió la toma, una repetición o algo "
              "incompleto, responde no. Responde solo sí o no.")
        y1, y2 = self.yes(self.ask(q1)), self.yes(self.ask(q2))
        if y1 is True and y2 is True:
            return True
        if y1 is False and y2 is False:
            return False
        return None

    def unfinished(self, sentence: str):
        if self.llm is None:
            return None
        s = re.sub(r"[^\wáéíóúñü ]", " ", sentence).strip()
        ans = []
        for o1, o2 in (("COMPLETA", "INCOMPLETA"), ("INCOMPLETA", "COMPLETA")):
            q = ("La transcripción automática no trae puntuación, así que ignora la falta de punto final.\n"
                 f"Frase: \"{s}\"\n\n¿La idea de la frase termina (se entiende completa) o se interrumpe a "
                 "la mitad (por ejemplo termina en 'que', 'para', 'porque...' sin verbo)? Responde "
                 f"una palabra: {o1} o {o2}.")
            a = self.ask(q)
            ans.append(None if a is None else ("INCOMPLETA" in a.upper()))
        return ans[0] is True and ans[1] is True


def local_llm(timeout: float = 20.0, model: str | None = None):
    """Callable(prompt) -> str | None con el LLM local (ollama del motor, sin razonamiento,
    temperatura 0, 8 tokens). None si no esta disponible."""
    try:
        from motion import director as D
        if not D.llm_available(model, timeout=min(2.0, timeout)):
            return None
        url, mdl = D._ollama_url(), model or D._llm_model()
    except Exception:  # noqa
        return None

    def ask(prompt: str) -> str | None:
        import json
        import urllib.request
        body = {"model": mdl, "stream": False, "think": False, "keep_alive": "10m",
                "options": {"temperature": 0, "num_predict": 8, "num_ctx": 4096, "seed": 7},
                "messages": [{"role": "user", "content": prompt}]}
        try:
            req = urllib.request.Request(url + "/api/chat", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return (json.loads(r.read()).get("message") or {}).get("content", "")
        except Exception:  # noqa
            return None
    return ask


# ============================================================================= CON GUION
class _Hit:
    __slots__ = ("clip", "score", "pairs", "s0", "s1", "t0", "t1", "id", "si_list")

    def __init__(self, clip, score, pairs, s0, s1, t0, t1, hid):
        self.clip, self.score, self.pairs = clip, score, pairs
        self.s0, self.s1, self.t0, self.t1, self.id = s0, s1, t0, t1, hid
        self.si_list = [p[0] for p in pairs]


def _find_hits(sc: _Script, c: _Clip, hid0: int) -> list[_Hit]:
    """Todos los intentos del guion dentro de un clip (SW multi-hit contra el guion completo)."""
    T = [t.n for t in c.toks]
    m = len(T)
    if not m or not sc.n:
        return []
    M, MIS = P["sw_match"], P["sw_mis"]
    sv = [sc.simvec(t) for t in T]
    diag = [np.where(v > 0, M * v, MIS) for v in sv]
    c21 = [sc.rows21.get(t) for t in T]
    c12 = [None] + [sc.rows_eq.get(T[j - 1] + T[j]) for j in range(1, m)]
    blocked = np.zeros(m, bool)
    hits: list[_Hit] = []
    while True:
        score, pr = _sw(diag, sc.n, gap_col=P["sw_gap_clip"], gap_row=P["sw_gap_script"], blocked_cols=blocked,
                        c21=c21, c12=c12, c21_score=2 * M, c12_score=M)
        if not pr:
            break
        pairs = [(si, tj, 1.0 if kind in (4, 5) else float(sv[tj][si])) for si, tj, kind in pr]
        matched = sorted({si for si, tj, s in pairs if s > 0})
        if not matched:
            break
        ok = score >= P["hit_min_score"] and len(matched) >= P["hit_min_matched"]
        if not ok:
            # una linea corta dicha completa y exacta tambien cuenta ("¿Clientes nuevos? Ninguno.")
            li = sc.line_of[matched[0]]
            L = sc.lines[li]
            ok = (all(sc.line_of[s] == li for s in matched) and len(matched) == L["t1"] - L["t0"]
                  and all(p[2] == 1.0 for p in pairs) and len(matched) >= 2)
        if not ok:
            break                      # lo mejor que queda ya no alcanza: no hay mas intentos
        while pairs and pairs[0][2] == 0:
            pairs = pairs[1:]
        while pairs and pairs[-1][2] == 0:
            pairs = pairs[:-1]
        tjs = [p[1] for p in pairs]
        blocked[min(tjs):max(tjs) + 1] = True
        # un "intento" hecho solo de palabras funcionales ("que la de") no es un intento
        if len(matched) > 2 and sum(1 for si in matched if sc.toks[si].content) < 2:
            continue
        pairs.sort()
        hits.append(_Hit(c, score, pairs, matched[0], matched[-1], min(tjs), max(tjs), hid0 + len(hits)))
    hits += _short_line_hits(sc, c, blocked, hid0 + len(hits))
    hits.sort(key=lambda h: h.t0)
    return hits


def _short_line_hits(sc: _Script, c: _Clip, blocked: np.ndarray, hid0: int) -> list[_Hit]:
    """Lineas de 1-2 palabras ("Sígueme.", "Cero.", "Cero ventas.") dichas EXACTAS y solas: el SW
    no las acepta (no llegan al puntaje minimo ni a 3 palabras, y la busqueda para en el primer
    intento que no alcanza). Cuentan si el clip las dice con las mismas palabras, en palabras que
    ningun otro intento uso, acotadas por pausas (>= short_pause), puntuacion o los bordes del clip
    ("desde cero con un horno" no es la linea "Cero."), y si alguna palabra es de contenido."""
    T = [t.n for t in c.toks]
    m = len(T)
    out: list[_Hit] = []
    if not m:
        return out
    used = np.asarray(blocked, bool).copy()
    for L in sc.lines:
        n = L["t1"] - L["t0"]
        if not 1 <= n <= 2:
            continue
        norms = [sc.toks[i].n for i in range(L["t0"], L["t1"])]
        if not any(sc.toks[i].content for i in range(L["t0"], L["t1"])):
            continue
        for j in range(m - n + 1):
            if used[j:j + n].any() or T[j:j + n] != norms:
                continue
            first, last = c.toks[j], c.toks[j + n - 1]
            before = j == 0 or c.toks[j - 1].punct in ("stop", "ellipsis", "comma") or \
                first.pause_before >= P["short_pause"]
            after = j + n == m or last.punct in ("stop", "ellipsis", "comma") or \
                c.toks[j + n].pause_before >= P["short_pause"]
            if not (before and after):
                continue
            pairs = [(L["t0"] + q, j + q, 1.0) for q in range(n)]
            out.append(_Hit(c, P["sw_match"] * n, pairs, L["t0"], L["t1"] - 1, j, j + n - 1, hid0 + len(out)))
            used[j:j + n] = True
    return out


class _Cand:
    __slots__ = ("h", "a", "b", "tj0", "tj1", "best", "F", "cov", "exact", "prec", "extra", "fillers",
                 "nflag", "flag_kinds", "later", "react", "gap_in", "gap_out", "dl", "level", "cost", "score",
                 "bits", "tie_note", "lose_note", "intercut", "note", "L", "pairs")


def _delivery(c: _Clip, tj0: int, tj1: int, free_after: frozenset) -> dict:
    """hes: segundos de pausa a media frase sobre hes_pause (tras palabra alineada a puntuacion
    del guion es gratis; una pausa > tighten_over cuenta como tighten_to: luego se recorta);
    rate: silabas/s."""
    toks = c.toks[tj0:tj1 + 1]
    gaps = [toks[k + 1].start - toks[k].end for k in range(len(toks) - 1)]
    span = toks[-1].end - toks[0].start
    speak = span - sum(g for g in gaps if g > 0.25)
    syl = sum(_syllables(t.raw) for t in toks)
    hes = 0.0
    for k, g in enumerate(gaps):
        if (tj0 + k) in free_after:
            continue
        eff = P["tighten_to"] if g > P["tighten_over"] else g
        hes += max(0.0, eff - P["hes_pause"])
    return {"pause_max": max(gaps) if gaps else 0.0, "rate": syl / max(0.3, speak), "span": span, "hes": hes}


def _reaction_after(c: _Clip, tj1: int) -> bool:
    """Tras la toma (a < reaction_s y antes de otro intento del guion) hay una groseria o un
    comentario sobre la toma: el orador no quedo conforme con ella."""
    end = c.toks[tj1].end
    nxt = min([h.t0 for h in c.hits if h.t0 > tj1], default=len(c.toks))
    for k in range(tj1 + 1, nxt):
        if c.toks[k].start - end > P["reaction_s"]:
            break
        if c.flag[k] == "error":
            return True
    return False


def _syllables(word: str) -> int:
    w = TK.strip_accents(str(word).lower())
    if w.isdigit():
        return max(1, len(w))
    return max(1, len(re.findall(r"[aeiouy]+", w)))


def _make_cand(sc: _Script, h: _Hit, a: int, b: int, check_lines: bool = True) -> _Cand | None:
    import bisect
    lo, hi = bisect.bisect_left(h.si_list, a), bisect.bisect_left(h.si_list, b)
    pr = h.pairs[lo:hi]
    while pr and pr[0][2] == 0:
        pr = pr[1:]
    while pr and pr[-1][2] == 0:
        pr = pr[:-1]
    if not pr:
        return None
    best: dict[int, float] = {}
    for si, tj, s in pr:
        if s > best.get(si, -1.0):
            best[si] = s
    L = b - a
    F = sum(best.values()) / L
    if F < P["cand_min_cov"]:
        return None
    if check_lines:
        for li in {sc.line_of[i] for i in range(a, b)}:
            Ln = sc.lines[li]
            x, y = max(a, Ln["t0"]), min(b, Ln["t1"])
            if y > x and sum(best.get(i, 0.0) for i in range(x, y)) / (y - x) < P["cand_min_cov"]:
                return None
    c = h.clip
    tjs = sorted({p[1] for p in pr})
    tj0, tj1 = tjs[0], tjs[-1]
    inside = set(tjs)
    extra_toks = [k for k in range(tj0, tj1 + 1) if k not in inside]
    o = _Cand()
    o.h, o.a, o.b, o.tj0, o.tj1, o.L, o.pairs = h, a, b, tj0, tj1, L, pr
    o.best = best
    o.F = F
    o.cov = sum(1 for v in best.values() if v > 0) / L
    o.exact = sum(1 for v in best.values() if v == 1.0) / L
    o.fillers = sum(1 for k in extra_toks if c.toks[k].filler or c.toks[k].n in TK.FILLERS)
    o.extra = len(extra_toks) - o.fillers
    nm = len(inside)
    o.prec = nm / (nm + o.extra + P["filler_w"] * o.fillers) if nm else 0.0
    fl = [c.flag[k] for k in range(tj0, tj1 + 1) if c.flag[k]]
    o.nflag, o.flag_kinds = len(fl), sorted(set(fl))
    o.later = any(x.clip is c and x.t0 > h.t1 and x.s0 < b and x.s1 >= a for x in c.hits)
    o.react = _reaction_after(c, tj1)
    o.gap_in = c.toks[tj0].start - c.toks[tj0 - 1].end if tj0 > 0 else 9.0
    o.gap_out = c.toks[tj1 + 1].start - c.toks[tj1].end if tj1 + 1 < len(c.toks) else 9.0
    free = frozenset(tj for si, tj, _ in pr if sc.toks[si].punct)
    o.dl = _delivery(c, tj0, tj1, free)
    o.level = c.speech_level(c.toks[tj0].start, c.toks[tj1].end)
    o.tie_note = o.lose_note = o.note = None
    o.intercut = None
    return o


def _score(o: _Cand, sess: dict, n_rec: int) -> _Cand:
    """Costo del candidato: L * (1 - F^2 * Pr * C * A * R) + entrega (aditivo por tramo)."""
    c = o.h.clip
    C = 1.0
    if o.nflag:
        C *= P["clean_flub"]
    if o.later:
        C *= P["clean_superseded"]
    if o.react:
        C *= P["clean_reaction"]
    A = 1.0
    if o.level is not None and sess.get("ref_level") is not None:
        deficit = sess["ref_level"] - o.level
        A = max(P["audio_floor"], 1.0 - P["audio_slope"] * max(0.0, deficit - P["audio_free_db"]))
    R = 1.0 - P["recency_eps"] * (1.0 - c.rec / max(1, n_rec - 1))
    q = (o.F ** 2) * o.prec * C * A
    bits = {"calidad": o.L * (1.0 - q), "recencia": o.L * q * (1.0 - R)}
    bits["titubeo"] = min(P["hes_cap"], P["w_hesitation"] * o.dl["hes"])
    med_rate = sess.get("med_rate") or 0.0
    bits["rapida"] = (P["w_rush_tok"] * o.L * max(0.0, o.dl["rate"] / med_rate - P["rush_ratio"])
                      if o.L >= 4 and med_rate else 0.0)
    o.bits = bits
    o.cost = sum(bits.values())
    o.score = round(q, 3)
    return o


def _cands_for_hits(sc: _Script, hits: list[_Hit], sess: dict, n_rec: int) -> dict:
    """Candidatos (tramo del guion entre limites, intento) por intento: solo los tramos que tocan
    su zona del guion."""
    B = sc.bounds
    out: dict[tuple, list] = defaultdict(list)
    for h in hits:
        lo = max(x for x in B if x <= h.s0)
        hi = min(x for x in B if x >= h.s1 + 1)
        starts = [x for x in B if lo <= x <= h.s1]
        ends = [x for x in B if h.s0 + 1 <= x <= hi]
        for a in starts:
            for b in ends:
                if b <= a:
                    continue
                o = _make_cand(sc, h, a, b)
                if o is not None:
                    out[(a, b)].append(_score(o, sess, n_rec))
    return out


def _join_cost(sc: _Script, prev: _Cand | None, cur: _Cand | None, a: int) -> float:
    if cur is None:
        return 0.0
    if prev is None:
        # inicio (o tras un hueco sin toma): su entrada tambien necesita una pausa
        return P["cut_nopause"] if cur.gap_in < P["nopause_s"] else 0.0
    if cur.h is prev.h and cur.tj0 == prev.tj1 + 1:
        return 0.0
    cost = P["cut_inner"] if (cur.h is prev.h and cur.tj0 > prev.tj1) else P["cut_base"]
    if not sc.is_line_start(a):
        cost += P["cut_midline"]
        sa, sb = prev.h.clip.setup, cur.h.clip.setup
        if sa is not None and sa == sb and prev.h.clip is not cur.h.clip:
            cost += P["cut_same_setup"]
    if prev.gap_out < P["nopause_s"]:
        cost += P["cut_nopause"]
    if cur.gap_in < P["nopause_s"]:
        cost += P["cut_nopause"]
    return cost


def _select(sc: _Script, cands: dict):
    """Programacion dinamica sobre los limites del guion: cada tramo con un candidato o sin toma."""
    B = sc.bounds
    NONE = ("none",)
    states: dict[int, dict] = {0: {NONE: (0.0, None, None)}}
    ends_at: dict[int, list] = defaultdict(list)
    for (a, b), lst in cands.items():
        for o in lst:
            ends_at[b].append((a, o))
    for bi in range(1, len(B)):
        b = B[bi]
        st: dict = {}
        # sin toma: solo entre limites consecutivos (tramos largos = varios pasos)
        a = B[bi - 1]
        own = P["miss_base"] + P["miss_tok"] * (b - a)
        for pk, (pc, _, pcand) in states.get(a, {}).items():
            tc = pc + own
            if NONE not in st or tc < st[NONE][0]:
                st[NONE] = (tc, (a, pk), None)
        for a, o in ends_at.get(b, []):
            if a not in states:
                continue
            key = (o.h.id, o.tj1)
            for pk, (pc, _, pcand) in states[a].items():
                tc = pc + o.cost + _join_cost(sc, pcand, o, a)
                if key not in st or tc < st[key][0]:
                    st[key] = (tc, (a, pk), o)
        states[b] = st
    n = sc.n
    best_key, best_cost = None, math.inf
    for k, (cst, _, o) in states.get(n, {}).items():
        cst2 = cst + (P["cut_nopause"] if o is not None and o.gap_out < P["nopause_s"] else 0.0)
        if cst2 < best_cost - 1e-12:
            best_key, best_cost = k, cst2
    chain = []
    b, key = n, best_key
    while key is not None:
        cst, back, o = states[b][key]
        if back is None:
            break
        a, pk = back
        chain.append((a, b, o))
        b, key = a, pk
    chain.reverse()
    return chain, best_cost


def _cands_range(sc: _Script, all_hits: list[_Hit], a: int, b: int, sess: dict, n_rec: int,
                 cache: dict) -> list[_Cand]:
    lst = cache.get((a, b))
    if lst is None:
        lst = []
        for h in all_hits:
            if h.s1 < a or h.s0 >= b:
                continue
            o = _make_cand(sc, h, a, b)
            if o is not None:
                lst.append(_score(o, sess, n_rec))
        cache[(a, b)] = lst
    return lst


def _merge_chain(sc, chain, all_hits, sess, n_rec, cache):
    """Pedazos contiguos del mismo intento -> un segmento (el candidato del tramo unido)."""
    out = []
    for a, b, o in chain:
        if out and o is not None and out[-1][2] is not None and out[-1][2].h is o.h and \
                o.tj0 == out[-1][2].tj1 + 1 and not o.intercut and not out[-1][2].intercut:
            a0 = out[-1][0]
            m = next((x for x in _cands_range(sc, all_hits, a0, b, sess, n_rec, cache) if x.h is o.h), None)
            if m is not None:
                out[-1] = (a0, b, m)
                continue
        out.append((a, b, o))
    return out


def _edge_pen(o: _Cand) -> float:
    return (P["cut_nopause"] if o.gap_in < P["nopause_s"] else 0.0) + \
        (P["cut_nopause"] if o.gap_out < P["nopause_s"] else 0.0)


def _tie_cost(o: _Cand) -> float:
    """Costo para decidir empates: sin la recencia (el empate se resuelve por recencia aparte)."""
    return o.cost - o.bits.get("recencia", 0.0) + _edge_pen(o)


def _norms(o: _Cand) -> list[str]:
    return [t.n for t in o.h.clip.toks[o.tj0:o.tj1 + 1]]


def _refine(sc, chain, all_hits, sess, n_rec, cache, judge: _Judge):
    """Por segmento final [a, b): entre las tomas de exactamente ese tramo, empate (costo a
    tie_eps) -> la mas reciente; casi-empate con distinta redaccion -> juez LLM (dos ordenes)."""
    decisions = []
    out = []
    for a, b, o in chain:
        if o is None:
            out.append((a, b, o))
            continue
        lst = _cands_range(sc, all_hits, a, b, sess, n_rec, cache)
        if o not in lst:
            lst = [o] + lst
        tot = {id(x): _tie_cost(x) for x in lst}
        bestc = min(tot.values())
        tied = [x for x in lst if tot[id(x)] <= bestc + P["tie_eps"]]
        pick = max(tied, key=lambda x: (x.h.clip.rec, x.h.t0)) if len(tied) > 1 else \
            min(lst, key=lambda x: tot[id(x)])
        if len(tied) > 1:
            pick.tie_note = f"empate con {len(tied) - 1} toma(s): se usa la más reciente"
            for x in tied:
                if x is not pick:
                    x.lose_note = "Igual de buena, pero anterior (en empate se usa la última)"
        if judge.llm is not None:
            pn = _norms(pick)
            rivals = sorted([x for x in lst if x is not pick and tot[id(x)] <= tot[id(pick)] + P["tie_margin"]
                             and _norms(x) != pn], key=lambda x: tot[id(x)])
            if rivals:
                r = rivals[0]
                v = judge.pair(sc.raw(a, b), pick.h.clip.text(pick.tj0, pick.tj1), r.h.clip.text(r.tj0, r.tj1))
                decisions.append({"lines": sc.lines_in(a, b), "chosen": pick.h.clip.name, "rival": r.h.clip.name,
                                  "verdict": v})
                if v == "B":
                    r.tie_note = "el juez LLM la prefiere (coincide en los dos órdenes)"
                    pick.lose_note = "El juez LLM prefirió otra toma"
                    pick = r
        out.append((a, b, pick))
    return out, decisions


# ----------------------------------------------------------------------------- intercalado
def _aligned(o: _Cand) -> dict:
    m: dict[int, list] = {}
    for si, tj, v in o.pairs:
        if o.a <= si < o.b and o.tj0 <= tj <= o.tj1 and v > 0:
            m.setdefault(si, []).append(tj)
    return m


def _pause_at(o: _Cand, m: int) -> float | None:
    """Pausa de la toma entre las palabras alineadas a los tokens m-1 y m del guion (None si no
    son palabras seguidas de la toma)."""
    al = _aligned(o)
    if (m - 1) not in al or m not in al:
        return None
    tp, tn = max(al[m - 1]), min(al[m])
    if tn != tp + 1:
        return None
    cl = o.h.clip
    return cl.toks[tn].start - cl.toks[tp].end


def _span_dur(o: _Cand) -> float:
    cl = o.h.clip
    return cl.toks[o.tj1].end - cl.toks[o.tj0].start + P["pre_roll"] + P["post_roll"]


def _same_words(x: _Cand, y: _Cand) -> bool:
    return _norms(x) == _norms(y)


def _intercut(sc, chain, all_hits, sess, n_rec, cache, setup_count: dict):
    """Intercalado de angulos (lo que hace un editor por variedad visual). Para un segmento de
    >= ic_min s, solo si la sesion ofrece:
      (1) otra toma COMPLETA del mismo tramo, con las MISMAS palabras, desde otra camara (limpia,
          costo a ic_tol, nivel a ic_level_db) -> A/B (o A/B/A desde ic_aba s), cortando solo
          donde las dos tomas pausan >= ic_pause. Abre y cierra la camara principal (la que tiene
          mas tomas en la sesion; si empatan, la elegida); o
      (2) una toma limpia de otra camara grabada para exactamente el principio o el final del
          tramo (una toma parcial "a proposito") con las mismas palabras -> B/A o A/B.
    Cada parte dura >= ic_part s. Las partes de la otra toma cuestan a lo mas ic_tol mas que el
    mismo tramo de la elegida."""
    out, log = [], []

    def sub(h, x, y):
        o = _make_cand(sc, h, x, y)
        return _score(o, sess, n_rec) if o else None

    def ok_part(o, ref):
        return o is not None and not o.nflag and ref is not None and o.cost <= ref.cost + P["ic_tol"]

    for a, b, c in chain:
        if c is None or _span_dur(c) < P["ic_min"] or c.h.clip.setup is None or c.nflag:
            out.append((a, b, c))
            continue
        sc0 = c.h.clip.setup
        want3 = _span_dur(c) >= P["ic_aba"]
        plans = []   # (clave de orden, tipo, partes, "angulo" | "parcial")
        for o in _cands_range(sc, all_hits, a, b, sess, n_rec, cache):
            so = o.h.clip.setup
            if o is c or so is None or so == sc0 or o.nflag or not _same_words(o, c):
                continue
            if o.cost > c.cost + P["ic_tol"]:
                continue
            if o.level is not None and c.level is not None and abs(o.level - c.level) > P["ic_level_db"]:
                continue
            ms = [m for m in range(a + 1, b)
                  if (_pause_at(c, m) or 0) >= P["ic_pause"] and (_pause_at(o, m) or 0) >= P["ic_pause"]]
            if not ms:
                continue
            for A, Bt in ((c, o), (o, c)):
                na, nb = setup_count.get(A.h.clip.setup, 0), setup_count.get(Bt.h.clip.setup, 0)
                main = 2 if na > nb else (1 if na == nb and A is c else 0)

                def part(h, x, y):
                    q = sub(h, x, y)
                    if q is None or q.nflag:
                        return None
                    return q if h is c.h or ok_part(q, sub(c.h, x, y)) else None
                for m1 in ms:
                    p2 = [part(A.h, a, m1), part(Bt.h, m1, b)]
                    if all(p2) and min(_span_dur(p) for p in p2) >= P["ic_part"]:
                        plans.append(((not want3, main, round(min(_span_dur(p) for p in p2), 3),
                                       -round(sum(p.cost for p in p2), 4)), "A/B", p2, "angulo"))
                    for m2 in ms:
                        if m2 <= m1:
                            continue
                        p3 = [part(A.h, a, m1), part(Bt.h, m1, m2), part(A.h, m2, b)]
                        if all(p3) and min(_span_dur(p) for p in p3) >= P["ic_part"]:
                            plans.append(((want3, main, round(min(_span_dur(p) for p in p3), 3),
                                           -round(sum(p.cost for p in p3), 4)), "A/B/A", p3, "angulo"))
        if not plans:
            # (2) tomas parciales de otra camara para un principio / final del tramo
            for h in all_hits:
                cl2 = h.clip
                if cl2 is c.h.clip or cl2.setup is None or cl2.setup == sc0:
                    continue
                if any(cl2.flag[k] for k in range(h.t0, h.t1 + 1)):
                    continue
                if any(x.clip is cl2 and x.t0 > h.t1 and x.s0 <= h.s1 and x.s1 >= h.s0 for x in cl2.hits):
                    continue
                for m, kind in ((h.s1 + 1, "B/A"), (h.s0, "A/B")):
                    if kind == "B/A" and not (h.s0 == a and a < m < b):
                        continue
                    if kind == "A/B" and not (h.s1 == b - 1 and a < m < b):
                        continue
                    if (_pause_at(c, m) or 0) < P["ic_pause"]:
                        continue
                    if kind == "B/A":
                        Bp, Ap, ref = sub(h, a, m), sub(c.h, m, b), sub(c.h, a, m)
                        parts = [Bp, Ap]
                    else:
                        Ap, Bp, ref = sub(c.h, a, m), sub(h, m, b), sub(c.h, m, b)
                        parts = [Ap, Bp]
                    if Ap is None or Ap.nflag or not ok_part(Bp, ref) or not _same_words(Bp, ref):
                        continue
                    if min(_span_dur(p) for p in parts) >= P["ic_part"]:
                        plans.append(((True, 1, round(min(_span_dur(p) for p in parts), 3),
                                       -round(sum(p.cost for p in parts), 4)), kind, parts, "parcial"))
        if not plans:
            out.append((a, b, c))
            continue
        plans.sort(key=lambda pl: pl[0], reverse=True)
        _, kind, parts, mode = plans[0]
        for p in parts:
            p.intercut = mode
            p.tie_note = ("toma parcial de otra cámara que completa la línea" if mode == "parcial" else
                          "intercalado de ángulos: misma frase desde otra cámara")
            out.append((p.a, p.b, p))
        log.append({"lines": sc.lines_in(a, b), "plan": kind, "mode": mode,
                    "parts": [(p.h.clip.name, sc.raw(p.a, p.b)) for p in parts]})
    return out, log


# ----------------------------------------------------------------------------- bordes
def _quiet_point(c: _Clip, lo: float, hi: float, target: float) -> float:
    """Tiempo en [lo, hi] mas cercano a target entre las ventanas a 3 dB de la mas callada."""
    db = c.db
    if hi <= lo:
        return (lo + hi) / 2
    k0, k1 = max(0, c.t2k(lo)), min(len(db) - 1, c.t2k(hi))
    if k1 < k0:
        return min(max(target, lo), hi)
    seg = db[k0:k1 + 1]
    vmin = float(seg.min())
    ok = [k0 + i for i, v in enumerate(seg) if v <= max(vmin + 3.0, c.voice_thr - 6)]
    kbest = min(ok, key=lambda k: abs(c.k2t(k) - target))
    return min(max(c.k2t(kbest), lo), hi)


def _voice_onset(c: _Clip, t: float, lo: float) -> float:
    db = c.db
    k = min(max(0, c.t2k(t)), len(db) - 1)
    kmin = max(c.t2k(lo), c.t2k(t - P["onset_back"]), 0)
    while k - 1 >= kmin and db[k - 1] > c.voice_thr:
        k -= 1
    return c.k2t(k) - 0.005


def _voice_offset(c: _Clip, t: float, hi: float) -> float:
    db = c.db
    k = min(max(0, c.t2k(t)), len(db) - 1)
    kmax = min(c.t2k(hi), c.t2k(t + P["offset_fwd"]), len(db) - 1)
    while k + 1 <= kmax and db[k + 1] > c.voice_thr:
        k += 1
    return c.k2t(k) + 0.005


def _voiced_runs(c: _Clip, a: float, b: float, min_frames: int = 5) -> list[tuple[float, float]]:
    """Rachas de voz sin transcribir (>= 50 ms) en [a, b]: un murmullo, un comentario, el arranque
    de una retoma."""
    if c.db is None or b <= a:
        return []
    k0, k1 = max(0, c.t2k(a)), min(len(c.db) - 1, c.t2k(b))
    runs, cur = [], []
    for k in range(k0, k1 + 1):
        if c.db[k] > c.voice_thr:
            cur.append(k)
        else:
            if len(cur) >= min_frames:
                runs.append((c.k2t(cur[0]), c.k2t(cur[-1])))
            cur = []
    if len(cur) >= min_frames:
        runs.append((c.k2t(cur[0]), c.k2t(cur[-1])))
    return runs


def _cut_in(c: _Clip, tj0: int) -> float:
    w = c.toks[tj0]
    lo = c.toks[tj0 - 1].end + P["neighbour_guard"] if tj0 > 0 else 0.0
    if c.db is None:
        # solo texto: el aire pedido, sin pasar la mitad del hueco con la palabra anterior
        t = w.start - P["pre_roll"]
        if tj0 > 0:
            t = max(t, (c.toks[tj0 - 1].end + w.start) / 2, lo if lo < w.start else w.start)
        return round(max(0.0, min(t, w.start)), 3)
    onset = _voice_onset(c, w.start, lo)
    hi = onset - P["min_pre"]
    target = w.start - P["pre_roll"]
    # voz sin transcribir en el aire de entrada: se empieza despues de ella
    runs = _voiced_runs(c, lo, onset - 0.12)
    if runs:
        lo = max(lo, min(hi, runs[-1][1] + 0.05))
    if hi <= lo:
        return round(max(0.0, _quiet_point(c, max(0.0, lo - P["neighbour_guard"]), max(lo, onset - 0.01), onset)), 3)
    return round(max(0.0, _quiet_point(c, max(lo, target - 0.12), hi, target)), 3)


def _cut_out(c: _Clip, tj1: int) -> float:
    w = c.toks[tj1]
    last = tj1 + 1 >= len(c.toks)
    hi = c.toks[tj1 + 1].start - P["neighbour_guard"] if not last else c.dur - 0.02
    if c.db is None:
        t = w.end + P["post_roll"]
        if not last:
            t = min(t, (w.end + c.toks[tj1 + 1].start) / 2)
        return round(max(w.end, min(t, c.dur if c.dur > w.end else t)), 3)
    offset = _voice_offset(c, w.end, hi)
    lo = offset + P["min_post"]
    target = w.end + P["post_roll"]
    runs = _voiced_runs(c, offset + 0.12, hi)
    if runs:
        hi = max(lo, min(hi, runs[0][0] - 0.05))
    if hi <= lo:
        return round(min(c.dur, _quiet_point(c, min(offset + 0.01, hi), hi + P["neighbour_guard"], offset)), 3)
    return round(min(c.dur, _quiet_point(c, lo, min(hi, target + 0.12), target)), 3)


# ----------------------------------------------------------------------------- textos (UI)
def _clean(raw: str) -> str:
    return str(raw).strip().strip("¿¡.,;:!?…\"'«»“”")


def _diff_chunks(sc: _Script, o: _Cand) -> list[tuple[str, str]]:
    """Diferencias del texto dicho contra el guion: [(dice, guion)] por bloque."""
    cl = o.h.clip
    al = {}
    for si, tj, v in o.pairs:
        if o.a <= si < o.b:
            al.setdefault(si, (tj, v))
    ops = []   # (tipo, token guion | None, token clip | None)
    tj_used = {tj for tj, v in al.values()}
    si = o.a
    tj = o.tj0
    while si < o.b or tj <= o.tj1:
        if si < o.b and si in al and al[si][0] == tj:
            ops.append(("eq" if al[si][1] == 1.0 else "sub", si, tj))
            si += 1
            tj += 1
        elif tj <= o.tj1 and tj not in tj_used:
            ops.append(("ins", None, tj))
            tj += 1
        elif si < o.b and (si not in al or al[si][0] > tj):
            if si in al and tj <= o.tj1 and al[si][0] > tj:
                ops.append(("ins", None, tj))
                tj += 1
            else:
                ops.append(("del", si, None))
                si += 1
        else:
            if tj <= o.tj1:
                ops.append(("ins", None, tj))
                tj += 1
            else:
                ops.append(("del", si, None))
                si += 1
    chunks = []
    k = 0
    while k < len(ops):
        if ops[k][0] == "eq":
            k += 1
            continue
        j = k
        while j < len(ops) and ops[j][0] != "eq":
            j += 1
        # contexto (hasta 2 palabras iguales antes) solo en un cambio corto de palabras: "Dice «en la
        # que» en vez de «en la cual»"; lo que falta o sobra se nombra solo
        kinds = {op[0] for op in ops[k:j]}
        ctx = [op for op in ops[max(0, k - 2):k] if op[0] == "eq"] if (j - k <= 2 and ("sub" in kinds or
                                                                                        kinds == {"ins", "del"})) else []
        said = [cl.toks[op[2]].raw for op in ctx + ops[k:j] if op[2] is not None]
        want = [sc.toks[op[1]].raw for op in ctx + ops[k:j] if op[1] is not None]
        chunks.append((" ".join(_clean(x) for x in said).strip(), " ".join(_clean(x) for x in want).strip()))
        k = j
    return chunks


def _diff_text(sc: _Script, o: _Cand, maxn: int = 2) -> str:
    """Diferencias contra el guion en espanol. Lo que falta en un lugar y sobra en otro (la misma
    palabra en otro orden) se dice una vez: "Cambia el orden de «simplemente»" (no "Le falta X;
    Agrega X")."""
    chunks = _diff_chunks(sc, o)
    missing = {want.lower() for said, want in chunks if want and not said}
    added = {said.lower() for said, want in chunks if said and not want}
    moved = missing & added
    out, told = [], set()
    for said, want in chunks:
        if len(out) >= maxn:
            break
        key = (want or said).lower()
        if key in moved and not (said and want):
            if key not in told:
                told.add(key)
                out.append(f"Cambia el orden de «{want or said}»")
            continue
        if said and want and said.lower() != want.lower():
            out.append(f"Dice «{said}» en vez de «{want}»")
        elif want and not said:
            out.append(f"Le falta «{want}»")
        elif said and not want:
            out.append(f"Agrega «{said}»")
    return "; ".join(out)


def _flags_of(o: _Cand, sess: dict) -> list[str]:
    f = []
    if o.nflag:
        f.append("blooper" if "error" in o.flag_kinds else "retoma")
    if o.later:
        f.append("repetida")
    if o.react:
        f.append("reaccion")
    if o.level is not None and sess.get("ref_level") is not None and o.level < sess["ref_level"] - P["weak_audio_db"]:
        f.append("sin_microfono")
    if o.exact < 0.999 or o.extra:
        f.append("variante")
    if o.cov < 0.999:
        f.append("incompleta")
    if o.bits.get("titubeo", 0) > 0.05:
        f.append("titubeo")
    if o.bits.get("rapida", 0) > 0.05:
        f.append("rapida")
    if o.intercut == "angulo":
        f.append("intercalado")
    elif o.intercut == "parcial":
        f.append("parcial")
    return f


def _reason_chosen(sc: _Script, o: _Cand, n_alts: int, latest: bool, sess: dict) -> str:
    fl = _flags_of(o, sess)
    bits = []
    if o.intercut:
        bits.append(o.tie_note[0].upper() + o.tie_note[1:])
    faithful = o.exact >= 0.999 and not o.extra
    if faithful and not o.nflag:
        bits.append(("Última toma completa y fiel al guion" if latest and n_alts else
                     "Toma completa y fiel al guion"))
    else:
        d = _diff_text(sc, o)
        bits.append(d or f"Fiel al guion en un {o.F:.0%}")
    if o.nflag:
        bits.append("tiene una retoma dentro (no hay una toma mejor)")
    if "sin_microfono" in fl:
        bits.append("toma sin micrófono (no hay una mejor con micrófono)")
    if o.tie_note and not o.intercut:
        bits.append(o.tie_note)
    if not n_alts:
        bits.append("única toma de esta parte")
    s = "; ".join(bits)
    return s[0].upper() + s[1:] if s else s


def _reason_alt(sc: _Script, o: _Cand, win: _Cand, sess: dict) -> str:
    if o.lose_note:
        return o.lose_note
    fl = _flags_of(o, sess)
    out = []
    if "blooper" in fl:
        out.append("Tiene un error o grosería dentro")
    elif "retoma" in fl:
        out.append("Tiene una retoma o trabada dentro")
    if "repetida" in fl:
        out.append("La vuelve a decir más adelante en el mismo clip")
    if "reaccion" in fl:
        out.append("Después de la toma hay una grosería o un comentario sobre ella")
    if "sin_microfono" in fl:
        out.append("Toma sin micrófono")
    d = _diff_text(sc, o)
    if d:
        out.append(d)
    if "titubeo" in fl and "titubeo" not in _flags_of(win, sess):
        out.append("Titubea a media frase")
    if "rapida" in fl:
        out.append("Dicha muy rápido")
    if not out:
        if win.intercut and _same_words(o, win) and o.h.clip.setup != win.h.clip.setup:
            out.append("Igual de buena; se usa la otra cámara para intercalar ángulos")
        elif o.h.clip.rec < win.h.clip.rec:
            out.append("Toma anterior (en empate se usa la última)")
        else:
            out.append("Igual de buena; se prefirió la otra")
    s = "; ".join(out[:3])
    return s[0].upper() + s[1:]


# ----------------------------------------------------------------------------- enriquecimiento
def _enrichment(sc: _Script, clips: list[_Clip], judge: _Judge) -> list[dict]:
    """Material fuera de guion que podria enriquecerlo. Entra solo si cumple TODO:
      E1 libre     : no es parte de ningun intento del guion ni esta marcado (retoma, error).
      E2 completo  : enunciado (se corta en pausas >= 0.6 s / puntuacion final) de >= 6 palabras,
                     >= 3 de contenido, sin quedar colgado ('...' o palabra colgante). Una
                     continuacion dicha a < 1 s de un segmento elegido del mismo clip basta con 2.
      E3 microfono : nivel de voz a <= 6 dB de la referencia de la sesion (p75; no charla fuera de
                     micro).
      E4 nuevo     : no comparte >= 3 palabras de contenido (y >= 50 %) con una linea del guion.
      E5 unico     : de intentos repetidos de lo mismo solo el ultimo.
      E6 juez      : el LLM dice que si a dos preguntas distintas. Sin LLM solo es sugerencia."""
    script_sets = [set(t.n for t in sc.toks[L["t0"]:L["t1"]]) for L in sc.lines]
    levels = [c.speech_db for c in clips if c.speech_db is not None]
    med = float(np.percentile(levels, 75)) if levels else None
    utts = []
    for c in clips:
        cover = set()
        for h in c.hits:
            cover.update(range(h.t0, h.t1 + 1))
        cur: list[int] = []
        for k, t in enumerate(c.toks):
            if c.claimed[k] or c.flag[k] or k in cover:
                if cur:
                    utts.append((c, cur))
                    cur = []
                continue
            if cur and t.start - c.toks[cur[-1]].end >= 0.6:
                utts.append((c, cur))
                cur = []
            cur.append(k)
            if t.punct in _TERMINAL:
                utts.append((c, cur))
                cur = []
        if cur:
            utts.append((c, cur))
    out = []
    for c, ks in utts:
        toks = [c.toks[k] for k in ks]
        ns = [t.n for t in toks]
        content = [t.n for t in toks if t.content]
        fails = []
        cont_prev = ks[0] > 0 and c.claimed[ks[0] - 1] and toks[0].start - c.toks[ks[0] - 1].end < 1.0
        cont_next = ks[-1] + 1 < len(c.toks) and c.claimed[ks[-1] + 1] and \
            c.toks[ks[-1] + 1].start - toks[-1].end < 1.0
        adjacent = cont_prev or cont_next
        if (len(ns) < P["ex_words"] or len(content) < P["ex_content"]) and not (adjacent and len(ns) >= 2):
            fails.append("fragmento corto")
        if toks[-1].punct == "ellipsis" or TK.is_dangling(toks[-1]):
            fails.append("queda colgada")
        if QUESTION_RE.search(" ".join(t.raw for t in toks)) and sum(t.raw.count("?") for t in toks) >= 2:
            fails.append("varias preguntas (charla)")
        lvl = c.speech_level(toks[0].start, toks[-1].end)
        if lvl is not None and med is not None and lvl < med - P["ex_mic_db"]:
            fails.append(f"fuera de micrófono ({lvl - med:+.0f} dB)")
        cset = set(content)
        shared = max((len(cset & s) for s in script_sets), default=0)
        frac = max((len(cset & s) / max(1, len(cset)) for s in script_sets), default=0.0)
        if shared >= 3 and frac >= 0.5:
            fails.append("otra forma de decir una línea del guion")
        out.append({"clip": c, "ks": ks, "text": " ".join(t.raw for t in toks), "fails": fails,
                    "adjacent": adjacent})
    passing = [u for u in out if not u["fails"]]
    for u in passing:
        su = set(u["clip"].toks[k].n for k in u["ks"])
        for o in passing:
            if o is u or o["clip"].rec <= u["clip"].rec:
                continue
            so = set(o["clip"].toks[k].n for k in o["ks"])
            if len(su & so) / max(1, min(len(su), len(so))) >= 0.6:
                u["fails"].append("se repite más tarde")
                break
    story = " ".join(L["text"] for L in sc.lines)
    for u in out:
        u["keep"], u["judge"] = False, None
        if u["fails"]:
            continue
        u["judge"] = judge.enriches(u["text"], story)
        u["keep"] = u["judge"] is True
    return out


# ----------------------------------------------------------------------------- descartes
def _parts_label(sc: _Script | None, lines: list[str], part_of: dict | None) -> str:
    """Como se nombran unas lineas del guion en los motivos (el panel numera las partes 1..N y no
    muestra ids de linea): "la parte 12", "las partes 16 y 17"; una linea sin parte (sin toma), por
    su texto: «Ahora toca la parte…»."""
    if not lines:
        return "el guion"
    part_of = part_of or {}
    nums = sorted({n for lid in lines for n in part_of.get(lid, [])})
    if nums and all(lid in part_of for lid in lines):
        if len(nums) == 1:
            return f"la parte {nums[0]}"
        return "las partes " + ", ".join(str(n) for n in nums[:-1]) + f" y {nums[-1]}"
    txt = next((L["text"] for L in (sc.lines if sc else []) if L["id"] == lines[0]), "")
    ws = str(txt).split()
    if not ws:
        return "el guion"
    q = " ".join(ws[:6]).rstrip(".,;:") + ("…" if len(ws) > 6 else "")
    more = len(lines) - 1
    return f"«{q}»" + (f" y {more} línea{'s' if more > 1 else ''} más" if more else "")


def _line_norms(sc: _Script) -> dict:
    """{tupla de normas de una linea del guion: id} (para reconocer una linea dicha exacta)."""
    out = {}
    for L in sc.lines:
        key = tuple(t.n for t in sc.toks[L["t0"]:L["t1"]])
        if key:
            out.setdefault(key, L["id"])
    return out


def _discarded(sc: _Script | None, clips: list[_Clip], sess: dict, used: dict,
               part_of: dict | None = None) -> list[dict]:
    """Todo lo transcrito que no entra, por rachas con su motivo. `part_of` = {linea: [numeros de
    parte del panel]} para nombrar las lineas en los motivos."""
    exact = _line_norms(sc) if sc else {}
    out = []
    for c in clips:
        if not c.toks:
            out.append({"sid": c.sid, "in": 0.0, "out": round(c.dur, 3), "text": "", "why": "sin_audio",
                        "reason": "El clip no tiene voz transcrita"})
            continue
        spans = used.get(c.sid, [])
        hit_of = {}
        for h in c.hits:
            for k in range(h.t0, h.t1 + 1):
                hit_of[k] = h
        runs, cur = [], []
        for k, t in enumerate(c.toks):
            mid = (t.start + t.end) / 2
            if any(x <= mid <= y for x, y in spans):
                if cur:
                    runs.append(cur)
                    cur = []
                continue
            if c.flag[k] == "error" or (c.flag[k] is None and _lex_blooper(c, k)):
                key = "blooper"
            elif c.flag[k] == "arranque_fallido":
                key = "incompleta"
            elif c.flag[k]:
                key = "retoma"
            elif k in hit_of:
                key = ("hit", hit_of[k].id)
            else:
                key = "fuera_de_guion"
            if cur and cur[-1][1] != key:
                runs.append(cur)
                cur = []
            cur.append((k, key))
        if cur:
            runs.append(cur)
        for r in runs:
            ks = [k for k, _ in r]
            key = r[0][1]
            lines = []
            if isinstance(key, tuple):
                h = hit_of[ks[0]]
                si = sorted({p[0] for p in h.pairs if ks[0] <= p[1] <= ks[-1] and p[2] > 0})
                lines = sc.lines_in(si[0], si[-1] + 1) if (sc and si) else []
                lvl = c.speech_level(c.toks[ks[0]].start, c.toks[ks[-1]].end)
                weak = lvl is not None and sess.get("ref_level") is not None and \
                    lvl < sess["ref_level"] - P["weak_audio_db"]
                cov = _hit_line_cov(sc, h) if sc else 1.0
                ends_clip = ks[-1] == h.t1 and h.t1 + 1 >= len(c.toks)
                label = _parts_label(sc, lines, part_of)
                if cov < P["cand_min_cov"] or (ends_clip and TK.abandoned([_as_tk(c.toks[ks[-1]])])):
                    why, reason = "incompleta", f"Intento incompleto de {label}"
                elif weak:
                    why, reason = "sin_audio", f"Toma sin micrófono de {label}"
                else:
                    why, reason = "retoma", f"Otra toma de {label}"
            elif key == "blooper":
                why, reason = "blooper", "Error, grosería o comentario sobre la toma"
            elif key == "incompleta":
                why, reason = "incompleta", "Arranque en falso"
            elif key == "retoma":
                why, reason = "retoma", "Retoma dentro del clip (se queda el último intento)"
            elif tuple(c.toks[k].n for k in ks) in exact:
                # dice EXACTO una linea del guion (p. ej. una linea corta dicha junto a otra cosa): no
                # es material fuera de guion, es otra toma de esa linea
                lines = [exact[tuple(c.toks[k].n for k in ks)]]
                why, reason = "retoma", f"Otra toma de {_parts_label(sc, lines, part_of)}"
            else:
                why, reason = "fuera_de_guion", "Fuera de guion"
            t0, t1 = c.toks[ks[0]].start, c.toks[ks[-1]].end
            d = {"sid": c.sid, "in": round(t0, 3), "out": round(t1, 3), "text": c.text(ks[0], ks[-1]),
                 "why": why, "reason": reason}
            if lines:
                d["lines"] = lines
            out.append(d)
    return out


def _as_tk(t: Tok):
    return TK.Tok(t.i, t.wi0, t.raw, t.n, t.s, t.start, t.end, t.punct, t.lead_q, t.filler, t.content,
                  t.pause_before)


def _lex_blooper(c: _Clip, k: int) -> bool:
    n = c.toks[k].n
    return any(n in pat for pat in TK.EXPLETIVES if len(pat) == 1)


def _hit_line_cov(sc: _Script, h: _Hit) -> float:
    """Cobertura minima de las lineas que el intento toca (un intento que abandona la linea)."""
    best = {}
    for si, tj, s in h.pairs:
        best[si] = max(best.get(si, 0.0), s)
    covs = []
    for li in {sc.line_of[i] for i in best}:
        L = sc.lines[li]
        n = L["t1"] - L["t0"]
        if n:
            covs.append(sum(best.get(i, 0.0) for i in range(L["t0"], L["t1"])) / n)
    return min(covs) if covs else 0.0


# ============================================================================= SIN GUION
class _Master:
    """Secuencia maestra de columnas (palabras) de todos los intentos, en orden de grabacion."""

    def __init__(self, clips: list[_Clip]):
        self.clips = clips
        self.order: list[int] = []
        self.att: dict[int, dict] = {}
        self.nid = 0
        self.idx_n: dict[str, set] = defaultdict(set)
        self.idx_s: dict[str, set] = defaultdict(set)

    def new_col(self) -> int:
        self.nid += 1
        self.att[self.nid] = {}
        return self.nid

    def attest(self, cid: int, rec: int, ti: int):
        self.att[cid][rec] = ti
        t = self.clips[rec].toks[ti]
        self.idx_n[t.n].add(cid)
        self.idx_s[t.s].add(cid)

    def scores(self, T: list[Tok]) -> np.ndarray:
        """S[a, k]: puntaje de la palabra a del clip contra la columna k del maestro (contenido 2,
        funcion 1; casi igual (>= 0.8, >= 6 letras) 0.75 de eso)."""
        pos = {cid: k for k, cid in enumerate(self.order)}
        S = np.zeros((len(T), len(self.order)))
        vocab6 = [v for v in self.idx_n if len(v) >= 6]
        for a, t in enumerate(T):
            w = 2.0 if t.content else 1.0
            for cid in self.idx_n.get(t.n, set()) | self.idx_s.get(t.s, set()):
                S[a, pos[cid]] = w
            if len(t.n) >= 6:
                for v in vocab6:
                    if v != t.n and _ratio(t.n, v) >= 0.8:
                        for cid in self.idx_n[v]:
                            k = pos[cid]
                            if S[a, k] < 0.75 * w:
                                S[a, k] = 0.75 * w
        return S


def _lcs_pairs(S, t0, t1, m0, m1) -> list[tuple[int, int]]:
    """LCS ponderado dentro de la banda T[t0:t1] x M[m0:m1] (solo coincidencias positivas)."""
    if t1 <= t0 or m1 <= m0:
        return []
    sub = S[t0:t1, m0:m1].tolist()
    T, M = len(sub), len(sub[0])
    D = [[0.0] * (M + 1) for _ in range(T + 1)]
    for i in range(1, T + 1):
        Di, Dp, row = D[i], D[i - 1], sub[i - 1]
        for j in range(1, M + 1):
            s = row[j - 1]
            Di[j] = max(Dp[j], Di[j - 1], Dp[j - 1] + s if s > 0 else -1)
    out = []
    i, j = T, M
    while i > 0 and j > 0:
        s = sub[i - 1][j - 1]
        if s > 0 and D[i][j] == D[i - 1][j - 1] + s:
            out.append((t0 + i - 1, m0 + j - 1))
            i, j = i - 1, j - 1
        elif D[i][j] == D[i - 1][j]:
            i -= 1
        else:
            j -= 1
    return out[::-1]


def _align_master(ms: _Master, c: _Clip):
    """Alinea las palabras que se quedan del clip al maestro; lo no alineado entra como columnas
    nuevas (dentro de la burbuja que reemplaza, antes/despues de su ancla o al final)."""
    T = [c.toks[i] for i in c.kept]
    c.col = {}
    if not T:
        return
    if not ms.order:
        for t in T:
            cid = ms.new_col()
            ms.attest(cid, c.rec, t.i)
            ms.order.append(cid)
            c.col[t.i] = cid
        return
    M = list(ms.order)
    S = ms.scores(T)
    diag = [np.where(S[a] > 0, S[a], SWB_MIS) for a in range(len(T))]
    hits = []
    bt, bm = np.zeros(len(T), bool), np.zeros(len(M), bool)
    while True:
        sc, pr = _sw(diag, len(M), gap_col=SWB_GAP_T, gap_row=SWB_GAP_M, blocked_cols=bt, blocked_rows=bm)
        pairs = [(tj, mi) for mi, tj, _ in pr if S[tj, mi] > 0]
        if sc < MIN_ANCHOR_SCORE or len(pairs) < MIN_ANCHOR:
            break
        ta, tb = pairs[0][0], pairs[-1][0]
        ma, mb = pairs[0][1], pairs[-1][1]
        hits.append({"score": sc, "pairs": pairs, "t": (ta, tb), "m": (ma, mb)})
        bt[ta:tb + 1] = True
        bm[ma:mb + 1] = True
    if not hits:
        # retoma corta (una linea de 2-3 palabras otra vez): sin ancla de 4 palabras. Se acepta si
        # coincide >= 80 % del clip en un lugar compacto (el mas reciente del maestro)
        sc, pr = _sw(diag, len(M), gap_col=SWB_GAP_T, gap_row=SWB_GAP_M, prefer_last_row=True)
        pairs = [(tj, mi) for mi, tj, _ in pr if S[tj, mi] > 0]
        if pairs:
            span = pairs[-1][1] - pairs[0][1] + 1
            if len(pairs) >= max(2, math.ceil(0.8 * len(T))) and span <= 2 * len(T) + 2 and \
                    any(S[a, b] >= 1.5 for a, b in pairs):
                hits.append({"score": sc, "pairs": pairs, "t": (pairs[0][0], pairs[-1][0]),
                             "m": (pairs[0][1], pairs[-1][1]), "short": True})
    # cadena monotona de anclas (LIS ponderado)
    hits.sort(key=lambda h: h["t"][0])
    best = [h["score"] for h in hits]
    prev = [-1] * len(hits)
    for i, h in enumerate(hits):
        for j in range(i):
            g = hits[j]
            if g["t"][1] < h["t"][0] and g["m"][1] < h["m"][0] and best[j] + h["score"] > best[i]:
                best[i], prev[i] = best[j] + h["score"], j
    chain = []
    if hits:
        i = int(np.argmax(best))
        while i >= 0:
            chain.append(hits[i])
            i = prev[i]
        chain.reverse()
    pairs = []
    for h in chain:
        pairs += h["pairs"]
    if chain:
        filled = []

        def band(pp, t_anchor, m_anchor):
            # bandas abiertas (antes de la primera / tras la ultima ancla): palabras funcionales
            # sueltas son ruido; >= 2 con una de contenido, o una de contenido cerca del ancla
            if len(pp) >= 2 and any(S[a, b] >= 2 for a, b in pp):
                return pp
            return [(a, b) for a, b in pp if S[a, b] >= 1.5 and abs(a - t_anchor) <= 4 and abs(b - m_anchor) <= 6]
        ta, ma = chain[0]["t"][0], chain[0]["m"][0]
        w = 2 * ta + 4
        filled += band(_lcs_pairs(S, 0, ta, max(0, ma - w), ma), ta, ma)
        for g, h in zip(chain, chain[1:]):
            filled += _lcs_pairs(S, g["t"][1] + 1, h["t"][0], g["m"][1] + 1, h["m"][0])
        tz, mz = chain[-1]["t"][1], chain[-1]["m"][1]
        w = 2 * (len(T) - tz - 1) + 4
        filled += band(_lcs_pairs(S, tz + 1, len(T), mz + 1, min(len(M), mz + 1 + w)), tz, mz)
        for h in chain:
            got_t = {p[0] for p in h["pairs"]}
            got_m = {p[1] for p in h["pairs"]}
            hp = sorted(h["pairs"])
            for p, q in zip(hp, hp[1:]):
                if q[0] - p[0] > 1 and q[1] - p[1] > 1:
                    filled += [x for x in _lcs_pairs(S, p[0] + 1, q[0], p[1] + 1, q[1])
                               if x[0] not in got_t and x[1] not in got_m]
        pairs = sorted(set(pairs + filled))
        mono, last_m = [], -1
        for p in pairs:
            if p[1] > last_m:
                mono.append(p)
                last_m = p[1]
        pairs = mono
    matched = {a: M[b] for a, b in pairs}
    for a, cid in matched.items():
        ms.attest(cid, c.rec, T[a].i)
        c.col[T[a].i] = cid
    a = 0
    while a < len(T):
        if a in matched:
            a += 1
            continue
        b = a
        while b < len(T) and b not in matched:
            b += 1
        nxt = matched.get(b) if b < len(T) else None
        prv = matched.get(a - 1) if a > 0 else None
        cids = []
        for k in range(a, b):
            cid = ms.new_col()
            ms.attest(cid, c.rec, T[k].i)
            c.col[T[k].i] = cid
            cids.append(cid)
        if nxt is not None:
            idx = ms.order.index(nxt)
        elif prv is not None:
            idx = ms.order.index(prv) + 1
        else:
            idx = len(ms.order)
        ms.order[idx:idx] = cids
        a = b


class _Resolver:
    """Redaccion final: gana la ultima ruta limpia y fluida (prototipo B)."""

    def __init__(self, ms: _Master, clips: list[_Clip], suspects=()):
        self.ms, self.clips = ms, clips
        self.suspects = set(suspects)
        self.seq = {c.rec: [(i, c.col[i]) for i in c.kept if i in c.col] for c in clips}
        self.status: dict[int, str] = {}
        self.why: dict[int, str] = {}
        self.sup_events: dict[int, list] = {}
        self.orphans: list[dict] = []

    def n_att(self, cid):
        return len(self.ms.att[cid])

    def bstr(self, cid, exclude=None):
        """Fraccion de las OTRAS tomas que cortan (puntuacion/pausa/corte/fin) tras la columna."""
        votes = []
        for rec, ti in self.ms.att[cid].items():
            if rec == exclude:
                continue
            c = self.clips[rec]
            k = c.kpos[ti]
            t = c.toks[ti]
            if k + 1 >= len(c.kept):
                votes.append(1.0)
                continue
            nx = c.toks[c.kept[k + 1]]
            cut_between = any(j in c.removed for j in range(ti + 1, nx.i))
            votes.append(1.0 if (t.punct in ("stop", "comma", "ellipsis") or nx.pause_before >= 0.3
                                 or cut_between) else 0.0)
        return (sum(votes) / len(votes)) if votes else None

    def hesitation_after(self, c: _Clip, ti: int) -> bool:
        k = c.kpos[ti]
        if k + 1 >= len(c.kept):
            return False
        nx = c.toks[c.kept[k + 1]]
        t = c.toks[ti]
        if nx.pause_before < HES or t.punct in ("stop", "comma", "ellipsis"):
            return False
        b = self.bstr(c.col[ti], exclude=c.rec) if ti in c.col else None
        return b is None or b < 0.3

    def clean_route(self, c: _Clip, ti0: int, ti1: int) -> bool:
        if c.rec in self.suspects:
            return False
        if ti1 < ti0:
            return True
        if any(j in c.removed for j in range(ti0, ti1 + 1)):
            return False
        ks = [i for i in c.kept if ti0 <= i <= ti1]
        return not any(self.hesitation_after(c, i) for i in ks[:-1])

    def line_break_after(self, c: _Clip, ti: int) -> bool:
        k = c.kpos[ti]
        if k + 1 >= len(c.kept):
            return True
        nx = c.toks[c.kept[k + 1]]
        if c.toks[ti].punct in _TERMINAL or nx.lead_q or any(j in c.removed for j in range(ti + 1, nx.i)):
            return True
        return nx.pause_before >= LINE_PAUSE and not self.hesitation_after(c, ti)

    def supersede(self, cids, by: _Clip, why: str):
        for cid in cids:
            self.sup_events.setdefault(cid, []).append((by.rec, why))

    def _until_break(self, c, tis):
        out = []
        for ti in tis:
            out.append(ti)
            if self.line_break_after(c, ti):
                break
        return out

    def _until_break_rev(self, c, tis):
        out = []
        for ti in reversed(tis):
            if out and self.line_break_after(c, ti):
                break
            out.insert(0, ti)
        return out

    def pairwise(self):
        clips = self.clips
        lost: dict[int, str] = {}
        for q in clips:
            for k in clips:
                if k.rec >= q.rec:
                    continue
                sq, sk = self.seq[q.rec], self.seq[k.rec]
                colq = {cid: ti for ti, cid in sq}
                shared = [(ti, cid) for ti, cid in sk if cid in colq]
                if len(shared) < 3:
                    continue
                kidx = {cid: n for n, (ti, cid) in enumerate(sk)}
                qidx = {cid: n for n, (ti, cid) in enumerate(sq)}
                # burbujas interiores
                for (ta, ca), (tb, cb) in zip(shared, shared[1:]):
                    Bk = [cid for ti, cid in sk[kidx[ca] + 1:kidx[cb]]]
                    Rq = [cid for ti, cid in sq[qidx[ca] + 1:qidx[cb]]]
                    if not Bk or set(Bk) <= set(Rq):
                        continue
                    qa, qb = colq[ca], colq[cb]
                    if qb - qa > 40:
                        continue
                    if self.clean_route(q, qa, qb):
                        self.supersede([x for x in Bk if x not in Rq], q, f"reemplazada por {q.name}")
                    elif self.clean_route(k, ta, tb) and Rq:
                        for x in Rq:
                            if x not in Bk:
                                lost[x] = f"variante con titubeo; {k.name} la dijo limpia"
                # extremos abiertos de la misma oracion (cola / cabeza)
                (tf, cf), (tl, cl) = shared[0], shared[-1]
                kt = [ti for ti, cid in sk[kidx[cl] + 1:]]
                qt = [ti for ti, cid in sq[qidx[cl] + 1:]]
                if kt and qt and not self.line_break_after(k, tl) and not self.line_break_after(q, colq[cl]):
                    Bt, Rt = self._until_break(k, kt), self._until_break(q, qt)
                    if Rt and len(Bt) <= 2 * len(Rt) + 4:
                        bc, rc = [k.col[i] for i in Bt], [q.col[i] for i in Rt]
                        if self.clean_route(q, colq[cl], Rt[-1]):
                            self.supersede([x for x in bc if x not in rc], q, f"final reemplazado por {q.name}")
                        elif self.clean_route(k, tl, Bt[-1]):
                            for x in rc:
                                if x not in bc:
                                    lost[x] = f"final con titubeo; {k.name} lo dijo limpio"
                kh = [ti for ti, cid in sk[:kidx[cf]]]
                qh = [ti for ti, cid in sq[:qidx[cf]]]
                if kh and qh and not self.line_break_after(k, kh[-1]) and not self.line_break_after(q, qh[-1]):
                    Bh, Rh = self._until_break_rev(k, kh), self._until_break_rev(q, qh)
                    if Rh and len(Bh) <= 2 * len(Rh) + 4:
                        bc, rc = [k.col[i] for i in Bh], [q.col[i] for i in Rh]
                        if self.clean_route(q, Rh[0], colq[cf]):
                            self.supersede([x for x in bc if x not in rc], q, f"inicio reemplazado por {q.name}")
                        elif self.clean_route(k, Bh[0], tf):
                            for x in rc:
                                if x not in bc:
                                    lost[x] = f"inicio con titubeo; {k.name} lo dijo limpio"
        return lost

    def _fresh_junk(self, c: _Clip, sq, sess_level):
        """Oraciones de una toma nueva con senales de basura: rafaga de preguntas cortas (charla
        con el equipo: "¿Ya esta grabando? ¿Le doy?"), dos preguntas en una oracion, final '...',
        voz muy por debajo de la sesion (fuera de microfono) o una marca de retoma dentro; la
        charla dicha UNA vez en la cabeza o la cola del clip ("¿Ya está grabando?", "¿Quedó
        bien?", "¿Así está bien o la repito?": _is_chatter); y lo muy corto pegado a esa basura
        ("Si, dale.")."""
        sids = sorted({c.sent_of[i] for i in c.kept})
        sent_toks = {sid: [i for i in c.kept if c.sent_of[i] == sid] for sid in sids}
        text = {sid: " ".join(c.toks[i].raw for i in sent_toks[sid]) for sid in sids}
        isq = {sid: "?" in text[sid] and len(sent_toks[sid]) <= 6 for sid in sids}
        # charla en los bordes del clip: desde la cabeza (y desde la cola) se salta lo muy corto
        # ("Sí, dale.") hasta la primera oracion con contenido
        chat = set()
        for seq in (sids, sids[::-1]):
            for sid in seq:
                toks = [c.toks[i] for i in sent_toks[sid]]
                if _is_chatter(toks):
                    chat.add(sid)
                elif sum(1 for t in toks if t.content) >= 2:
                    break
        junk = {}
        for k, sid in enumerate(sids):
            sent = sent_toks[sid]
            lv = [c.tok_db[i] for i in sent if c.tok_db[i] is not None]
            lvl = float(np.median(lv)) if lv else None
            burst = isq[sid] and ((k > 0 and isq[sids[k - 1]]) or (k + 1 < len(sids) and isq[sids[k + 1]]))
            junk[sid] = burst or sid in chat or \
                (QUESTION_RE.search(text[sid]) is not None and text[sid].count("¿") >= 2) or \
                c.toks[sent[-1]].punct == "ellipsis" or \
                (lvl is not None and sess_level is not None and lvl < sess_level - 12) or \
                any(i in c.removed for i in range(sent[0], sent[-1] + 1))
        for k, sid in enumerate(sids):
            if not junk[sid] and sum(1 for i in sent_toks[sid] if c.toks[i].content) < 2 and \
                    ((k > 0 and junk[sids[k - 1]]) or (k + 1 < len(sids) and junk[sids[k + 1]])):
                junk[sid] = True
        by_sent: dict[int, list] = {}
        for ti, cid in sq:
            if self.status[cid] == "single":
                by_sent.setdefault(c.sent_of[ti], []).append(ti)
        for sid, tis in by_sent.items():
            if not junk.get(sid):
                continue
            sent = sent_toks[sid]
            where = "head" if sent[0] == c.kept[0] else ("tail" if sent[-1] == c.kept[-1] else "interior")
            self.orphans.append({"clip": c.rec, "tis": tis, "sent": sent, "cids": [c.col[t] for t in tis],
                                 "where": where + " (toma nueva con señales de basura)"})
            for t in tis:
                self.status[c.col[t]] = "orphan"

    def _valid(self, d: _Clip, ti0: int, ti1: int) -> bool:
        if any(j in d.removed for j in range(ti0 + 1, ti1)):
            return False
        return all(self.status.get(d.col.get(x)) in ("multi", "single", "variant") for x in d.kept if ti0 < x < ti1)

    def _edge_majority(self, cid, after: bool, exclude=None) -> bool:
        votes = []
        for rec, ti in self.ms.att[cid].items():
            if rec == exclude:
                continue
            d = self.clips[rec]
            if after:
                votes.append(self.line_break_after(d, ti))
            else:
                k = d.kpos[ti]
                votes.append(k == 0 or self.line_break_after(d, d.kept[k - 1]))
        return not votes or sum(votes) / len(votes) >= 0.5

    def _has_alternative(self, c: _Clip, lc, rc) -> bool:
        for d in self.clips:
            if d.rec == c.rec:
                continue
            if lc is not None and rc is not None:
                a, b = self.ms.att[lc].get(d.rec), self.ms.att[rc].get(d.rec)
                if a is not None and b is not None and a < b and self._valid(d, a, b) and \
                        not any(self.status.get(d.col.get(x)) in ("single", "variant") and
                                len(self.ms.att[d.col[x]]) == 1 for x in d.kept if a < x < b):
                    return True
            elif lc is not None:
                a = self.ms.att[lc].get(d.rec)
                if a is not None and self.line_break_after(d, a) and self._edge_majority(lc, True, d.rec):
                    s0 = [x for x in d.kept if d.sent_of[x] == d.sent_of[a] and x <= a]
                    if self._valid(d, s0[0] - 1, a + 1):
                        return True
            elif rc is not None:
                b = self.ms.att[rc].get(d.rec)
                if b is not None and self._edge_majority(rc, False, d.rec):
                    k = d.kpos[b]
                    if k == 0 or self.line_break_after(d, d.kept[k - 1]):
                        s1 = [x for x in d.kept if d.sent_of[x] == d.sent_of[b] and x >= b]
                        if self._valid(d, b - 1, s1[-1] + 1):
                            return True
        return False

    def resolve(self, sess_level):
        lost = self.pairwise()
        for cid in self.ms.order:
            last_att = max(self.ms.att[cid])
            ev = [e for e in self.sup_events.get(cid, []) if e[0] > last_att]
            if ev:
                self.status[cid] = "superseded"
                self.why[cid] = ev[-1][1]
            elif cid in lost and self.n_att(cid) == 1:
                self.status[cid] = "superseded"
                self.why[cid] = lost[cid]
            elif self.n_att(cid) >= 2:
                self.status[cid] = "multi"
            else:
                self.status[cid] = "single"
        self.orphans = []
        for c in self.clips:
            sq = self.seq[c.rec]
            if not sq:
                continue
            n_multi = sum(1 for _, cid in sq if self.status[cid] == "multi")
            if n_multi < MIN_ANCHOR:
                # toma nueva (su material no se dijo en otra): sus oraciones son guion, salvo las
                # que tienen senales de basura, que pasan por la regla de enriquecimiento
                self._fresh_junk(c, sq, sess_level)
                continue
            n = 0
            orphan_tis = []
            while n < len(sq):
                ti, cid = sq[n]
                if self.status[cid] != "single":
                    n += 1
                    continue
                m = n
                while m < len(sq) and self.status[sq[m][1]] == "single":
                    m += 1
                run = sq[n:m]
                lt, lc = sq[n - 1] if n > 0 else (None, None)
                rt, rc = sq[m] if m < len(sq) else (None, None)
                parts, cur = [], []
                for t, x in run:
                    cur.append((t, x))
                    if self.line_break_after(c, t):
                        parts.append(cur)
                        cur = []
                if cur:
                    parts.append(cur)
                for pi, part in enumerate(parts):
                    ljoin = pi == 0 and lc is not None and self.status[lc] == "multi" and \
                        not self.line_break_after(c, lt)
                    rjoin = pi == len(parts) - 1 and rc is not None and self.status[rc] == "multi" and \
                        not self.line_break_after(c, part[-1][0])
                    if ljoin or rjoin:
                        opt = self._has_alternative(c, lc if ljoin else None, rc if rjoin else None)
                        for _, x in part:
                            self.status[x] = "variant" if opt else "single"
                            self.why[x] = (f"palabras opcionales de {c.name}" if opt else f"única redacción ({c.name})")
                    else:
                        orphan_tis += [t for t, _ in part]
                        for _, x in part:
                            self.status[x] = "orphan"
                n = m
            by_sent: dict[int, list] = {}
            for t in orphan_tis:
                by_sent.setdefault(c.sent_of[t], []).append(t)
            for sid, tis in by_sent.items():
                sent = [i for i in c.kept if c.sent_of[i] == sid]
                where = "head" if sent[0] == c.kept[0] else ("tail" if sent[-1] == c.kept[-1] else "interior")
                self.orphans.append({"clip": c.rec, "tis": tis, "sent": sent, "cids": [c.col[t] for t in tis],
                                     "where": where})
        return self


def _col_text(ms: _Master, clips, cid) -> str:
    att = ms.att[cid]
    rec = max(att)
    return clips[rec].toks[att[rec]].raw


def _script_lines(rs: _Resolver, clips):
    """Columnas que sobreviven en orden del maestro, partidas en lineas por los votos de pausa y
    puntuacion de las tomas que dicen cada par; seccion nueva donde ninguna toma dice las dos."""
    ms = rs.ms
    keep = {"multi", "single", "variant", "extra_keep"}
    SC = [cid for cid in ms.order if rs.status[cid] in keep]
    btype, nobody = [], []
    for a, b in zip(SC, SC[1:]):
        adj, nonadj = [], []
        for rec, ti in ms.att[a].items():
            c = clips[rec]
            tb = ms.att[b].get(rec)
            if tb is None:
                continue
            k, kb = c.kpos[ti], c.kpos[tb]
            between = c.kept[k + 1:kb]
            if kb <= k or any(rs.status.get(c.col.get(x)) in keep for x in between):
                continue
            ch = [ti] + between + [tb]
            gap = max(c.toks[y].start - c.toks[x].end for x, y in zip(ch, ch[1:]))
            punct = {c.toks[x].punct for x in ch[:-1]}
            v = ("line" if ({"stop", "ellipsis"} & punct or gap >= LINE_PAUSE) else
                 "phrase" if ("comma" in punct or gap >= PHRASE_PAUSE) else "mid")
            (nonadj if between else adj).append(v)
        votes = adj or nonadj
        if not votes:
            btype.append("line")
            nobody.append(True)
            continue
        nobody.append(False)
        nl = votes.count("line") / len(votes)
        npz = (votes.count("line") + votes.count("phrase")) / len(votes)
        btype.append("line" if nl >= 0.5 else ("phrase" if npz >= 0.5 else "mid"))
    lines, secs = [], []
    cur = [SC[0]] if SC else []
    sec = 0
    for k, bt in enumerate(btype):
        if bt == "line":
            lines.append(cur)
            secs.append(sec)
            cur = []
            if nobody[k]:
                sec += 1
        cur.append(SC[k + 1])
    if cur:
        lines.append(cur)
        secs.append(sec)
    return SC, btype, lines, secs


def _enrich_checks(c: _Clip, tis: list[int], base: list[list[str]], sess_level=None) -> dict:
    toks = [c.toks[i] for i in tis]
    content = [t for t in toks if t.content]
    text = " ".join(t.raw for t in toks)
    last = toks[-1]
    k_last = c.kpos[last.i]
    ends_ok = (last.punct == "stop" or k_last + 1 >= len(c.kept) or
               c.toks[c.kept[k_last + 1]].pause_before >= LINE_PAUSE) and not TK.is_dangling(_as_tk(last)) and \
        last.punct != "ellipsis"
    k_first = c.kpos[toks[0].i]
    starts_ok = k_first == 0 or c.toks[c.kept[k_first - 1]].punct in _TERMINAL or \
        toks[0].pause_before >= LINE_PAUSE or toks[0].lead_q or toks[0].raw[:1].isupper()
    lv = [c.tok_db[i] for i in tis if c.tok_db[i] is not None]
    other = [c.tok_db[i] for i in c.kept if i not in tis and c.tok_db[i] is not None]
    lvl = float(np.median(lv)) if lv else None
    ref = float(np.median(other)) if other else c.level
    nq = len(QUESTION_RE.findall(text)) // 2 + (1 if text.rstrip().endswith("?") and "¿" not in text else 0)
    sents = max(1, sum(1 for t in toks if t.punct in _TERMINAL))
    meta = any(tuple(t.n for t in toks[m:m + len(ex)]) == tuple(ex) for ex in TK.META + TK.EXPLETIVES
               for m in range(len(toks)))
    stems = [t.s for t in content]

    def lcs_len(a, b):
        prev = [0] * (len(b) + 1)
        for x in a:
            cur = [0] * (len(b) + 1)
            for k, y in enumerate(b):
                cur[k + 1] = prev[k] + 1 if x == y else max(prev[k + 1], cur[k])
            prev = cur
        return prev[-1]
    para = max((lcs_len(stems, L) / max(1, len(stems)) for L in base), default=0.0)
    checks = {
        "complete_sentence": bool(ends_ok and starts_ok),
        "long_enough": len(toks) >= 5 and len(content) >= 3,
        "on_mic": lvl is None or ((ref is None or lvl >= ref - 6.0) and
                                  (sess_level is None or lvl >= sess_level - 12.0)),
        "not_paraphrase": para < 0.6,
        "not_question_or_meta": (nq / sents) < 0.5 and not meta,
        "no_flags": not any(i in c.removed for i in range(tis[0], tis[-1] + 1)),
    }
    return {"text": text, "checks": checks, "pass": all(checks.values())}


def _orphan_checks(rs: _Resolver, clips, sess_level=None):
    ms = rs.ms
    _, _, lines0, _ = _script_lines(rs, clips)
    base = []
    for L in lines0:
        st = []
        for cid in L:
            rec, ti = max(ms.att[cid].items())
            t = clips[rec].toks[ti]
            if t.content:
                st.append(t.s)
        base.append(st)
    return [(o, _enrich_checks(clips[o["clip"]], o["sent"], base, sess_level)) for o in rs.orphans]


def _drop_unfinished(rs: _Resolver, clips, SC, btype, lines, judge: _Judge):
    """Una linea que TODA toma deja sin puntuacion al final del clip puede ser una oracion
    abandonada: el juez decide (dos formas, deben coincidir); si si, sale su ultima clausula."""
    ms = rs.ms
    jpos = {cid: j for j, cid in enumerate(SC)}
    out = []
    for li, L in enumerate(lines):
        last = L[-1]
        stops = True
        for rec, ti in ms.att[last].items():
            c = clips[rec]
            t = c.toks[ti]
            k = c.kpos[ti]
            if t.punct or (k + 1 < len(c.kept) and not any(j in c.removed for j in range(ti + 1, c.kept[k + 1]))):
                stops = False
                break
        if not stops or len(L) < 3:
            continue
        text = " ".join(_col_text(ms, clips, cid) for cid in L)
        v = judge.unfinished(text)
        out.append({"line": li, "text": text, "unfinished": v})
        if v:
            b = [jpos[L[k]] for k in range(len(L) - 1) if btype[jpos[L[k]]] in ("phrase", "line")]
            cut_from = (b[-1] + 1) if b else jpos[L[0]]
            dropped = SC[cut_from:jpos[last] + 1]
            if len(dropped) <= 8:
                for cid in dropped:
                    rs.status[cid] = "unfinished"
    return out


def _detect_script(clips: list[_Clip], judge: _Judge) -> tuple[str, dict]:
    """Sin guion: infiere el guion de la sesion (texto editable: una linea por renglon, linea en
    blanco entre secciones) + informacion de diagnostico."""
    info: dict = {"suspects": [], "extras": [], "unfinished": []}
    usable = [c for c in clips if c.kept]
    if not usable:
        return "", info
    # vista de sesion en orden de grabacion (indice = rec interno)
    view = usable
    for r, c in enumerate(view):
        c.vrec = r
    recmap = {c.vrec: c for c in view}
    for c in view:
        c.sent_of = {}
        sid = 0
        for k, i in enumerate(c.kept):
            t = c.toks[i]
            if k and (c.toks[c.kept[k - 1]].punct in _TERMINAL or t.pause_before >= LINE_PAUSE):
                sid += 1
            c.sent_of[i] = sid
    lvls = [c.level for c in view if c.level is not None]
    sess_level = float(np.median(lvls)) if lvls else None
    # el maestro trabaja con rec = posicion en la vista
    saved = {id(c): c.rec for c in view}
    for c in view:
        c.rec = c.vrec
    try:
        clist = [recmap[r] for r in range(len(view))]
        ms = _Master(clist)
        for c in clist:
            _align_master(ms, c)
        # pasada 1: tomas "sospechosas" (el orador reacciono: groseria, comentario meta, charla,
        # un 'no...' tras material del guion): no imponen su redaccion
        rs = _Resolver(ms, clist).resolve(sess_level)
        suspects = {c.rec for c in clist if any(k == "error" for k in c.removed.values())}
        for o, ev in _orphan_checks(rs, clist, sess_level):
            ck = ev["checks"]
            junk = (not ck["not_question_or_meta"]) or (not ck["on_mic"]) or ev["text"].rstrip().endswith(("...", "…"))
            if junk and not o["where"].startswith("head"):
                suspects.add(o["clip"])
        rs = _Resolver(ms, clist, suspects).resolve(sess_level)
        _, _, lines0, _ = _script_lines(rs, clist)
        ctx = " ".join(" ".join(_col_text(ms, clist, cid) for cid in L) for L in lines0)[:2500]
        for o, ev in _orphan_checks(rs, clist, sess_level):
            verdict = ev["pass"]
            if verdict:
                v = judge.enriches(ev["text"], ctx)
                ev["judge"] = v
                verdict = v is not False        # el LLM solo puede vetar (dos "no")
            ev["keep"] = bool(verdict)
            for cid in o["cids"]:
                rs.status[cid] = "extra_keep" if verdict else "extra_drop"
            info["extras"].append({"clip": clist[o["clip"]].name, "text": ev["text"], "keep": ev["keep"],
                                   "checks": ev["checks"]})
        info["suspects"] = sorted(clist[r].name for r in suspects)
        SC, btype, lines, secs = _script_lines(rs, clist)
        if judge.llm is not None and lines:
            info["unfinished"] = _drop_unfinished(rs, clist, SC, btype, lines, judge)
            if any(u.get("unfinished") for u in info["unfinished"]):
                SC, btype, lines, secs = _script_lines(rs, clist)
        text = _script_text(ms, clist, lines, secs)
        info["status"] = {k: sum(1 for v in rs.status.values() if v == k) for k in set(rs.status.values())}
    finally:
        for c in view:
            c.rec = saved[id(c)]
    return text, info


def _script_text(ms: _Master, clips, lines, secs) -> str:
    """Texto del guion detectado: palabras de la ultima toma, mayusculas solo de nombres propios
    (mayuscula a media oracion en alguna toma) y al inicio de linea."""
    out_secs: list[list[str]] = []
    prev_sec = None
    for L, s in zip(lines, secs):
        words = []
        for cid in L:
            att = ms.att[cid]
            rec = max(att)
            c = clips[rec]
            raw = c.toks[att[rec]].raw.strip()
            if raw[:1].isupper() and not _proper(ms, clips, cid):
                raw = raw[:1].lower() + raw[1:]
            words.append(raw)
        if not words:
            continue
        txt = " ".join(words)
        k = 0
        while k < len(txt) and txt[k] in "¿¡\"'«“":
            k += 1
        txt = txt[:k] + txt[k:k + 1].upper() + txt[k + 1:]
        if s != prev_sec:
            out_secs.append([])
            prev_sec = s
        out_secs[-1].append(txt)
    return "\n\n".join("\n".join(sec) for sec in out_secs if sec)


def _proper(ms: _Master, clips, cid) -> bool:
    """Nombre propio: las tomas que la dicen a media oracion (no tras punto, pausa o al inicio) la
    escriben con mayuscula mas veces que sin ella (Whisper capitaliza el inicio de sus segmentos
    aunque no haya punto)."""
    up = low = 0
    for rec, ti in ms.att[cid].items():
        c = clips[rec]
        t = c.toks[ti]
        if ti == 0 or c.toks[ti - 1].punct in _TERMINAL or t.pause_before >= 0.3:
            continue
        r = t.raw.strip().lstrip("¿¡\"'«“")
        if r[:1].isupper():
            up += 1
        elif r[:1].islower():
            low += 1
    return up > low


# ============================================================================= armado
def _prep_clips(clips: list[dict], gloss: dict, script_norms: set | None) -> list[_Clip]:
    order = recording_order(clips)
    out = [_Clip(clips[idx], idx, rec, gloss) for rec, idx in enumerate(order)]
    for c in out:
        c.prep_audio()
        c.prep_flags(script_norms)
    if all(c.setup is None for c in out) and any(d.get("frames") or d.get("thumb") for d in clips):
        try:
            ids = setups([c.d for c in out])
            for c, s in zip(out, ids):
                c.setup = s
        except Exception as e:  # noqa
            print(f"   montaje: sin grupos de camara ({e})")
    return out


def _session_stats(clips: list[_Clip], hits: list[_Hit]) -> dict:
    levels, rates = [], []
    for h in hits:
        c = h.clip
        lv = c.speech_level(c.toks[h.t0].start, c.toks[h.t1].end)
        if lv is not None:
            levels.append(lv)
        rates.append(_delivery(c, h.t0, h.t1, frozenset())["rate"])
    if not levels:
        levels = [c.speech_db for c in clips if c.speech_db is not None]
    # referencia = percentil 75: la voz "en microfono" aunque media sesion sea sin micro
    return {"ref_level": float(np.percentile(levels, 75)) if levels else None,
            "med_rate": float(np.median(rates)) if rates else None}


def build(clips: list[dict], script: str | None = None, *, intercut: bool = True, glossary=None,
          llm=None) -> dict:
    """Auto-montaje de la sesion -> plan["montage"] (contrato en el docstring del modulo)."""
    t_start = time.time()
    tm: dict[str, float] = {}
    if llm is True:
        llm = local_llm()
    judge = _Judge(llm)
    gloss = _gloss_map(glossary)
    user_text = str(script or "").strip()
    script_norms = {t.n for t in _text_toks(user_text)} if user_text else None
    if not script_norms:
        user_text, script_norms = "", None      # un guion sin palabras = sin guion
    t = time.time()
    cl = _prep_clips(list(clips or []), gloss, script_norms)
    tm["prep"] = time.time() - t
    order = [{"sid": c.sid, "name": c.name, "rec": c.rec, "created": c.created} for c in cl]
    source = "user"
    det_info = None
    text = user_text
    if not text:
        t = time.time()
        text, det_info = _detect_script(cl, judge)
        source = "detected"
        tm["detect"] = time.time() - t
    sc = _Script(text)
    res = _assemble(cl, sc, intercut=intercut, judge=judge, enrich=(source == "user"), tm=tm)
    res["script"] = {"source": source, "text": text, "sections": sc.parsed["sections"],
                     "lines": sc.parsed["lines"]}
    res["order"] = order
    res["stats"]["timings_ms"] = {k: int(round(v * 1000)) for k, v in tm.items()}
    res["stats"]["llm_calls"] = judge.calls
    if det_info is not None:
        res["stats"]["detected"] = {"suspects": det_info.get("suspects", []),
                                    "extras": det_info.get("extras", []),
                                    "status": det_info.get("status", {})}
    res["ms"] = int(round((time.time() - t_start) * 1000))
    out = {"version": VERSION, "order": res["order"], "script": res["script"], "slots": res["slots"],
           "timeline": res["timeline"], "discarded": res["discarded"], "uncovered": res["uncovered"],
           "extra": res["extra"], "stats": res["stats"], "ms": res["ms"]}
    return out


def _copy_cand(o: _Cand, tj0: int, tj1: int) -> _Cand:
    n = _Cand()
    for k in _Cand.__slots__:
        setattr(n, k, getattr(o, k, None))
    n.tj0, n.tj1 = tj0, tj1
    return n


def _segments(sc, chain, all_hits, sess, n_rec, cache) -> list[dict]:
    """Cadena final -> segmentos (pedazos contiguos del mismo intento en uno; huecos sin toma
    juntos)."""
    segs: list[dict] = []
    for a, b, o in chain:
        if o is None:
            if segs and segs[-1]["cand"] is None:
                segs[-1]["b"] = b
            else:
                segs.append({"a": a, "b": b, "cand": None})
            continue
        last = segs[-1] if segs else None
        if last and last["cand"] is not None and last["cand"].h is o.h and o.tj0 == last["tj1"] + 1 and \
                not o.intercut and not last["cand"].intercut:
            m = next((x for x in _cands_range(sc, all_hits, last["a"], b, sess, n_rec, cache) if x.h is o.h), None)
            if m is not None:
                last.update({"b": b, "cand": m, "tj1": m.tj1})
                continue
        segs.append({"a": a, "b": b, "cand": o, "tj0": o.tj0, "tj1": o.tj1})
    return segs


def _place_extras(sc, cl, covered, judge) -> tuple[list, list]:
    """Sugerencias fuera de guion (regla E1-E6) y las que entran a la linea de tiempo."""
    out, kept = [], []
    for u in _enrichment(sc, cl, judge):
        if u["fails"]:
            continue
        c, ks = u["clip"], u["ks"]
        prev = None
        for s in covered:
            if s["cand"].h.clip is c and s["tj1"] < ks[0]:
                prev = s
        if prev is None:
            for s in covered:
                if s["cand"].h.clip.rec <= c.rec:
                    prev = s
        lids = sc.lines_in(prev["a"], prev["b"]) if prev is not None else []
        why = ("nutre el guion" if u["keep"] else
               "sugerencia: sin juez LLM" if u["judge"] is None else "el juez LLM no la aprueba")
        ex = {"sid": c.sid, "in": _cut_in(c, ks[0]), "out": _cut_out(c, ks[-1]), "after": lids[-1] if lids else None,
              "why": why, "text": u["text"], "kept": bool(u["keep"]),
              "_lvl": c.speech_level(c.toks[ks[0]].start, c.toks[ks[-1]].end)}
        out.append(ex)
        if u["keep"]:
            kept.append((prev, c, ks, ex))
            for k in ks:
                c.claimed[k] = True
    return out, kept


def _cand_json(cid: str, x: _Cand, why: str, sess: dict, cl: list[_Clip], conf: float | None = None) -> dict:
    """Candidato del contrato. `_lvl` (nivel de voz medido) es interno: al final se vuelve su "vol"
    con la MISMA referencia que la linea de tiempo. `conf`: la confianza si fuera la elegida."""
    xc = x.h.clip
    d = {"id": cid, "sid": xc.sid, "in": _cut_in(xc, x.tj0), "out": _cut_out(xc, x.tj1),
         "w0": int(xc.toks[x.tj0].wi0), "w1": int(xc.toks[x.tj1].wi1), "text": xc.text(x.tj0, x.tj1),
         "score": x.score, "fidelity": round(x.F * x.prec, 3), "coverage": round(x.cov, 3),
         "flags": _flags_of(x, sess), "reason": why, "setup": _setup_id(xc, cl),
         "_lvl": xc.speech_level(xc.toks[x.tj0].start, xc.toks[x.tj1].end)}
    if conf is not None:
        d["conf"] = round(conf, 3)
    return d


def _gain(ref: float | None, lv: float | None) -> float | None:
    """`vol` lineal que lleva el nivel de voz `lv` (dB) a la referencia `ref` (topes de subida y
    bajada iguales a los de compose.level_gains)."""
    if ref is None or lv is None:
        return None
    g = float(np.clip(ref - lv, -P["max_gain_down_db"], P["max_gain_up_db"]))
    return round(10 ** (g / 20.0), 3)


def _assemble(cl: list[_Clip], sc: _Script, *, intercut: bool, judge: _Judge, enrich: bool, tm: dict) -> dict:
    n_rec = len(cl)
    t = time.time()
    all_hits: list[_Hit] = []
    for c in cl:
        c.hits = _find_hits(sc, c, len(all_hits)) if sc.n else []
        all_hits.extend(c.hits)
    tm["align"] = time.time() - t
    sess = _session_stats(cl, all_hits)
    t = time.time()
    cands = _cands_for_hits(sc, all_hits, sess, n_rec)
    cache = dict(cands)
    chain, total = _select(sc, cands) if sc.n else ([], 0.0)
    tm["select"] = time.time() - t
    t = time.time()
    chain = _merge_chain(sc, chain, all_hits, sess, n_rec, cache)
    chain, decisions = _refine(sc, chain, all_hits, sess, n_rec, cache, judge)
    ic_log = []
    if intercut:
        count: dict = defaultdict(int)
        for c in cl:
            if c.setup is not None:
                count[c.setup] += 1
        chain, ic_log = _intercut(sc, chain, all_hits, sess, n_rec, cache, count)
    tm["refine"] = time.time() - t
    segs = _segments(sc, chain, all_hits, sess, n_rec, cache)
    covered = [s for s in segs if s["cand"] is not None]
    # (sin recorte del texto repetido al inicio: el candidato ya empieza en su primera palabra
    # alineada DENTRO de su tramo del guion, asi que una entrada que repite el final del segmento
    # anterior nunca entra; lo repetido que queda es del propio guion y se conserva)
    for s in covered:
        if s["tj0"] != s["cand"].tj0 or s["tj1"] != s["cand"].tj1:
            s["cand"] = _copy_cand(s["cand"], s["tj0"], s["tj1"])
        c = s["cand"].h.clip
        for k in range(s["tj0"], s["tj1"] + 1):
            c.claimed[k] = True
    extras_out, extras_kept = [], []
    if enrich and sc.n:
        t = time.time()
        extras_out, extras_kept = _place_extras(sc, cl, covered, judge)
        tm["enrich"] = time.time() - t
    # orden final: segmentos del guion y, tras su vecino, lo fuera de guion aprobado
    uncovered, items = [], []
    for s in segs:
        if s["cand"] is None:
            for lid in sc.lines_in(s["a"], s["b"]):
                L = next(x for x in sc.lines if x["id"] == lid)
                inside = min(s["b"], L["t1"]) - max(s["a"], L["t0"])
                if inside >= 0.5 * max(1, L["t1"] - L["t0"]) and lid not in uncovered:
                    uncovered.append(lid)
            continue
        items.append(("seg", s))
        items += [("extra", pe) for pe in extras_kept if pe[0] is s]
    items = [("extra", pe) for pe in extras_kept if pe[0] is None] + items
    slots, timeline = [], []
    for kind, it in items:
        slot_id = f"s{len(slots) + 1:02d}"
        if kind == "seg":
            s, o = it, it["cand"]
            c = o.h.clip
            a, b = s["a"], s["b"]
            alts = [x for x in _cands_range(sc, all_hits, a, b, sess, n_rec, cache)
                    if x is not o and x.h is not o.h]
            alts.sort(key=lambda x: (round(x.cost + _edge_pen(x), 3), -x.h.clip.rec))
            seen, uniq = set(), []
            for x in alts:                       # una alternativa por intento
                if x.h.id not in seen:
                    seen.add(x.h.id)
                    uniq.append(x)
            uniq = uniq[:P["max_alts"]]
            latest = all(x.h.clip.rec <= c.rec for x in uniq)
            pool = [o] + uniq
            cj = [_cand_json(f"{slot_id}c1", o, _reason_chosen(sc, o, len(uniq), latest, sess), sess, cl,
                             _conf(o, uniq))]
            cj += [_cand_json(f"{slot_id}c{k}", x, _reason_alt(sc, x, o, sess), sess, cl,
                              _conf(x, [y for y in pool if y is not x]))
                   for k, x in enumerate(uniq, 2)]
            slots.append({"id": slot_id, "lines": sc.lines_in(a, b), "text": sc.raw(a, b),
                          "chosen": cj[0]["id"], "cands": cj})
            join = None if not timeline else ("section" if sc.is_section_start(a) else
                                              "line" if sc.is_line_start(a) else "splice")
            timeline.append({"slot": slot_id, "cand": cj[0]["id"], "sid": c.sid, "in": cj[0]["in"],
                             "out": cj[0]["out"], "start": 0.0, "vol": 1.0, "join": join,
                             "_lvl": c.speech_level(c.toks[o.tj0].start, c.toks[o.tj1].end),
                             "_conf": _conf(o, uniq), "_c": c, "_k": (o.tj0, o.tj1)})
        else:
            prev, c, ks, ex = it
            cid = f"{slot_id}c1"
            slots.append({"id": slot_id, "lines": [], "text": ex["text"], "chosen": cid, "extra": True,
                          "cands": [{"id": cid, "sid": c.sid, "in": ex["in"], "out": ex["out"],
                                     "w0": int(c.toks[ks[0]].wi0), "w1": int(c.toks[ks[-1]].wi1),
                                     "text": ex["text"], "score": 1.0, "fidelity": 0.0, "coverage": 0.0,
                                     "flags": ["fuera_de_guion"],
                                     "reason": "Fuera de guion pero aporta a la historia (el juez LLM la aprueba)",
                                     "setup": _setup_id(c, cl),
                                     "_lvl": c.speech_level(c.toks[ks[0]].start, c.toks[ks[-1]].end)}]})
            timeline.append({"slot": slot_id, "cand": cid, "sid": c.sid, "in": ex["in"], "out": ex["out"],
                             "start": 0.0, "vol": 1.0, "join": None if not timeline else "line",
                             "_lvl": c.speech_level(c.toks[ks[0]].start, c.toks[ks[-1]].end), "_conf": 0.5,
                             "_c": c, "_k": (ks[0], ks[-1])})
    # dos segmentos seguidos del mismo clip y contiguos en la fuente: un solo punto de corte en la
    # pausa (el aire de uno no se repite como aire del otro)
    slot_by = {s["id"]: s for s in slots}
    for k in range(1, len(timeline)):
        p, x = timeline[k - 1], timeline[k]
        if p["_c"] is x["_c"] and p["_k"][1] < x["_k"][0] and x["in"] < p["out"]:
            c = x["_c"]
            a, b = c.toks[p["_k"][1]].end, c.toks[x["_k"][0]].start
            cut = (a + b) / 2 if (c.db is None or b - a < 0.06) else _quiet_point(c, a + 0.02, b - 0.02, (a + b) / 2)
            p["out"] = x["in"] = round(cut, 3)
            slot_by[p["slot"]]["cands"][0]["out"] = p["out"]
            slot_by[x["slot"]]["cands"][0]["in"] = x["in"]
    for x in timeline:
        x.pop("_c", None)
        x.pop("_k", None)
    # volumen parejo por nivel de voz medido + tiempos contiguos
    # referencia = p75 de los niveles elegidos: se sube lo bajo en vez de bajar lo que esta bien
    levels = [x["_lvl"] for x in timeline if x["_lvl"] is not None]
    ref = float(np.percentile(levels, 75)) if levels else None
    tpos = 0.0
    used: dict[str, list] = defaultdict(list)
    for x in timeline:
        lv = x.pop("_lvl")
        x["conf"] = round(x.pop("_conf"), 3)
        g = _gain(ref, lv)
        if g is not None:
            x["vol"] = g
        x["start"] = round(tpos, 3)
        tpos += max(0.0, x["out"] - x["in"])
        used[x["sid"]].append((x["in"], x["out"]))
    # cada candidato (y cada sugerencia fuera de guion) con su "vol" a la MISMA referencia: una toma
    # cambiada en el ensamble ("Usar esta toma") o un extra agregado suena igual que sus vecinas
    for s in slots:
        for cj in s["cands"]:
            lv = cj.pop("_lvl", None)
            g = _gain(ref, lv)
            if g is not None:
                cj["vol"], cj["level_db"] = g, round(float(lv), 2)
    for ex in extras_out:
        lv = ex.pop("_lvl", None)
        g = _gain(ref, lv)
        if g is not None:
            ex["vol"], ex["level_db"] = g, round(float(lv), 2)
    # numero de parte de cada linea (como las numera el panel: las partes del guion, sin extras)
    part_of: dict[str, list] = {}
    nparts = 0
    for s in slots:
        if s.get("extra"):
            continue
        nparts += 1
        for lid in s["lines"]:
            part_of.setdefault(lid, []).append(nparts)
    discarded = _discarded(sc if sc.n else None, cl, sess, used, part_of)
    covered_toks, partial = set(), []
    for s in covered:
        o = s["cand"]
        covered_toks.update(i for i, v in o.best.items() if v > 0)
        miss = [i for i in range(s["a"], s["b"]) if o.best.get(i, 0.0) <= 0]
        if miss:
            partial.append({"slot": next((x["slot"] for x in timeline if x["sid"] == o.h.clip.sid and
                                          abs(x["in"] - _cut_in(o.h.clip, o.tj0)) < 1e-6), None),
                            "lines": sc.lines_in(s["a"], s["b"]),
                            "missing": " ".join(_clean(sc.toks[i].raw) for i in miss)})
    stats = {
        "clips": len(cl), "clips_used": len({x["sid"] for x in timeline}), "segments": len(timeline),
        "duration": round(tpos, 3), "script_lines": len(sc.lines), "script_tokens": sc.n,
        "covered_tokens": len(covered_toks), "coverage": round(len(covered_toks) / sc.n, 4) if sc.n else 0.0,
        "hits": len(all_hits), "candidates": sum(len(v) for v in cands.values()),
        "dp_cost": round(float(total), 3) if math.isfinite(total) else None,
        "partial": partial, "intercut": ic_log, "judge": decisions, "extras_kept": len(extras_kept),
        "session": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in sess.items()},
        "level_ref_db": round(ref, 2) if ref is not None else None,
    }
    return {"slots": slots, "timeline": timeline, "discarded": discarded, "uncovered": uncovered,
            "extra": extras_out, "stats": stats}


def _conf(o: _Cand, alts: list[_Cand]) -> float:
    """Confianza de la eleccion (0..1): calidad de la toma y margen sobre la siguiente."""
    q = o.score
    if not alts:
        return q
    margin = min(x.cost for x in alts) - o.cost
    return max(0.0, min(1.0, q * (0.6 + 0.4 * min(1.0, max(0.0, margin) / 2.0))))


def _setup_id(c: _Clip, cl: list[_Clip]) -> int:
    if c.setup is not None:
        return int(c.setup)
    base = 1 + max([x.setup for x in cl if x.setup is not None], default=-1)
    return base + c.rec


# ============================================================================= camaras
def _small(img, size: int = 32) -> np.ndarray | None:
    a = np.asarray(img)
    if a.ndim == 2:
        a = a[:, :, None].repeat(3, 2)
    if a.ndim != 3 or a.shape[0] < 2 or a.shape[1] < 2:
        return None
    a = a[:, :, :3].astype(np.float32)
    h, w = a.shape[:2]
    ys = np.linspace(0, h, size + 1).astype(int)
    xs = np.linspace(0, w, size + 1).astype(int)
    ys[-1], xs[-1] = h, w
    ys = np.maximum.accumulate(np.clip(ys, 0, h))
    xs = np.maximum.accumulate(np.clip(xs, 0, w))
    rows = np.add.reduceat(a, ys[:-1], axis=0) / np.maximum(1, np.diff(ys))[:, None, None]
    out = np.add.reduceat(rows, xs[:-1], axis=1) / np.maximum(1, np.diff(xs))[None, :, None]
    return out


def _frame_feat(a: np.ndarray) -> tuple:
    """Luma normalizada + histograma de color 6x6x6 de un cuadro chico (+ si es plano)."""
    la = a.mean(2)
    sd = float(la.std())
    la = (la - la.mean()) / (sd + 1e-6)
    h = np.histogramdd(a.reshape(-1, 3), bins=(6, 6, 6), range=[(0, 256)] * 3)[0].ravel()
    return la, h / max(1.0, h.sum()), sd < 1.0


def _frame_sim(fa, fb) -> float:
    """0.5 * correlacion de luma + 0.5 * interseccion de histogramas (solo histograma si un cuadro
    es plano: la correlacion no existe)."""
    hist = float(np.minimum(fa[1], fb[1]).sum())
    if fa[2] or fb[2]:
        return hist
    return 0.5 * float((fa[0] * fb[0]).mean()) + 0.5 * hist


def _load_thumb(path):
    try:
        from PIL import Image
        with Image.open(str(path)) as im:
            im = im.convert("RGB")
            im.thumbnail((96, 96))
            return np.asarray(im)
    except Exception:  # noqa
        return None


def setups(clips: list[dict], threshold: float = 0.66) -> list[int | None]:
    """Grupos de camara/encuadre de los clips (mismo lugar y angulo = mismo id). Cada clip trae
    "frames" (1-3 cuadros chicos: arreglos HxW o HxWx3 uint8) o "thumb" (ruta de una imagen).
    Similitud = 0.5 * correlacion de luma + 0.5 * interseccion de histograma de color (el mejor
    par de cuadros); agrupamiento por enlace promedio con `threshold`. Sin imagen -> None."""
    feats = []
    for d in clips:
        imgs = []
        for f in (d.get("frames") or []):
            sm = _small(f)
            if sm is not None:
                imgs.append(_frame_feat(sm))
        if not imgs and d.get("thumb"):
            im = _load_thumb(d["thumb"])
            sm = _small(im) if im is not None else None
            if sm is not None:
                imgs.append(_frame_feat(sm))
        feats.append(imgs)
    idx = [k for k, f in enumerate(feats) if f]
    ids: list[int | None] = [None] * len(clips)
    if not idx:
        return ids
    S = {}
    for x in idx:
        for y in idx:
            if x < y:
                S[(x, y)] = S[(y, x)] = max(_frame_sim(a, b) for a in feats[x] for b in feats[y])
    groups = [[k] for k in idx]
    while len(groups) > 1:
        best, bi, bj = -1e9, None, None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                v = float(np.mean([S[(a, b)] for a in groups[i] for b in groups[j]]))
                if v > best:
                    best, bi, bj = v, i, j
        if best < threshold:
            break
        groups[bi] = groups[bi] + groups[bj]
        del groups[bj]
    groups.sort(key=lambda g: min(g))
    for gi, g in enumerate(groups):
        for k in g:
            ids[k] = gi
    return ids


def frames_of(path, dur: float, at=(0.35,), size: tuple[int, int] = (36, 64)) -> list[np.ndarray]:
    """1-3 cuadros chicos RGB del video (ffmpeg, autorrotado) para `setups`. Ayuda para quien
    llama (build no la usa: el modulo no corre ffmpeg). [] si falla."""
    import subprocess
    try:
        import paths
        ff = str(paths.FFMPEG)
    except Exception:  # noqa
        ff = "ffmpeg"
    out = []
    w, h = size
    for fr in at:
        t = max(0.0, min(float(dur or 0) * fr, max(0.0, float(dur or 0) - 0.1)))
        try:
            raw = subprocess.run([ff, "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                                  "-vf", f"scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 capture_output=True, timeout=30).stdout
        except Exception:  # noqa
            continue
        if len(raw) == w * h * 3:
            out.append(np.frombuffer(raw, np.uint8).reshape(h, w, 3))
    return out


# ============================================================================= glosario
def glossary_terms(text: str | None) -> list[str]:
    """Nombres propios (palabras con mayuscula a media oracion, juntas: "Tienda Luna") y numeros
    escritos del guion, para el glosario de Whisper del video combinado (no el guion completo)."""
    out: list[str] = []
    seen = set()

    def add(term):
        if term and term.lower() not in seen:
            seen.add(term.lower())
            out.append(term)
    wrap = "¿¡\"'«»“”‘’()[]"
    for line in str(text or "").splitlines():
        words = line.split()
        run: list[str] = []
        sent_start = True
        for k, raw in enumerate(words):
            core = raw.strip(wrap + ".,;:!?…")
            ends = re.search(r"[.?!…:][\"'»”’)\]]*$", raw) is not None
            pause = ends or re.search(r"[,;][\"'»”’)\]]*$", raw) is not None
            cap = core[:1].isupper() and (not core.isupper() or len(core) >= 2)
            if core and cap and not sent_start and TK.norm(core) not in TK.STOP:
                run.append(core)
            elif run:
                add(" ".join(run))
                run = []
            if re.fullmatch(r"\d[\d.,]*%?", core):
                nxt = words[k + 1].strip(wrap + ".,;:!?…").lower() if k + 1 < len(words) else ""
                add(core + (" " + nxt if nxt in ("mil", "millones", "millón") else ""))
            if pause and run:
                add(" ".join(run))
                run = []
            sent_start = ends
        if run:
            add(" ".join(run))
    return out


if __name__ == "__main__":
    # uso: montage.py clips.json [guion.txt]   (clips.json = lista de clips del contrato)
    import json
    import sys
    if len(sys.argv) < 2:
        print(__doc__.split("\nAPI")[0])
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        _clips = json.load(f)
    _script = None
    if len(sys.argv) > 2:
        with open(sys.argv[2], encoding="utf-8") as f:
            _script = f.read()
    _r = build(_clips, _script)
    for _x in _r["timeline"]:
        _s = next(s for s in _r["slots"] if s["id"] == _x["slot"])
        print(f"{_x['start']:7.2f}  {_x['sid']:>14} {_x['in']:7.2f}-{_x['out']:7.2f}  {str(_x['join']):7s} "
              f"{','.join(_s['lines']):9s} {_s['cands'][0]['reason'][:70]}")
    print("sin toma:", _r["uncovered"], "| descartes:", len(_r["discarded"]), "| ms:", _r["ms"])
