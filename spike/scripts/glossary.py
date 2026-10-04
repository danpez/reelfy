#!/usr/bin/env python3
"""
Glosario: corrige la transcripcion de Whisper con los nombres propios y terminos que dio el usuario.

Whisper recibe el glosario como prompt (pipeline.transcribe), pero un prompt solo SUGIERE: con
"Nubis, nubi, Ruta, Taller Brisa" en el glosario puede escribir "NUBIs", "NUBI", "cada Nubi", "ruta" o
"Taller Prisa", y escribir el SIMBOLO de una palabra dicha (".no" por "dot no"). Esta capa determinista
repara las palabras (sin tocar sus tiempos) contra el glosario, con candados para no "corregir"
palabras que no son del glosario:

  1. Igual sin mayusculas ni acentos -> la escritura del glosario ("NUBIs," -> "Nubis,", "pro" ->
     "Pro"). Un termino en minusculas ("nubi") conserva la mayuscula de inicio de oracion.
  2. Varias palabras iguales a un termino de varias palabras -> su escritura ("taller brisa" ->
     "Taller Brisa"); palabras que JUNTAS forman un termino de una palabra se unen en una sola (con el
     inicio de la primera y el final de la ultima): "Voz Max" -> "VozMax", "Casa Luma" ->
     "CasaLuma", "Voz GPT" -> "VozGPT". Nunca a traves de un punto o una coma. Al reves, una palabra
     que es un termino de varias palabras PEGADO ("Tallerbrisa", "taller-brisa") toma su escritura
     ("Taller Brisa", una sola palabra con su espacio: los tiempos no cambian).
  3. Termino de varias palabras casi igual: misma cantidad de palabras, la primera igual (o la ultima),
     el texto junto (y su clave fonetica) con parecido >= MULTI_RATIO y escrito como NOMBRE (cada
     palabra con mayuscula): "Taller Prisa" -> "Taller Brisa"; "un taller prisa" se queda.
  4. Una palabra con mayuscula casi igual a un termino de >= FUZZY_MIN letras: solo si SUENA igual
     (clave fonetica del espanol: b/v, s/z/c suave, y/ll, h muda, qu/k...) y su parecido es
     >= FUZZY_RATIO ("Yaretsi" -> "Yaretzi"; "Ramiro" NUNCA -> "Ramira": suena distinto).
  5. Simbolo hablado: si el glosario trae el nombre de un simbolo ("dot", "punto", "arroba",
     "hashtag"...), una palabra que empieza con ese simbolo pegado a letras (".no") se parte en el
     termino + el resto ("dot" + "no", tiempos en proporcion a las letras) y un simbolo suelto con
     duracion (". " dicho) pasa a ser el termino.

API
  terms(glossary) -> [str]            "Nubis, nubi; Ruta\\n..." (o "Nombres propios y términos: ...")
  apply(words, glossary, same_count=False) -> words   [{text, start, end, ...}] corregidas (copias;
                                      mismo orden y tiempos; pueden unirse o partirse palabras como
                                      arriba salvo con same_count)
  fix_text(text, glossary) -> str     lo mismo sobre un texto suelto (titulos, guion detectado)
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

FUZZY_MIN = 4            # letras minimas para corregir una palabra casi igual
FUZZY_RATIO = 0.80
MULTI_RATIO = 0.82
MERGE_MAX = 3            # palabras que pueden unirse en un termino de una palabra
_PREFIX = "\"'«“‘([¿¡"
_SUFFIX = "\"'»”’)].,;:!?…"
_SENT_END = ".!?…"
_HEAD_RE = re.compile(r"^\s*(nombres\s+propios(\s+y\s+t[eé]rminos)?|t[eé]rminos|glosario)\s*:\s*", re.IGNORECASE)
# simbolos que Whisper escribe en vez de su nombre dicho ("dot" -> ".")
SPOKEN_SYMBOLS = {".": ("dot", "punto"), "@": ("arroba", "at"), "#": ("hashtag", "numeral", "gato"),
                  "&": ("and",), "/": ("slash", "diagonal"), "+": ("plus", "mas")}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str) -> str:
    """minusculas, sin acentos (la ñ se queda) y solo letras y numeros."""
    s = str(s).lower().replace("ñ", "\x00")
    return re.sub(r"[^a-z0-9\x00]", "", _strip_accents(s)).replace("\x00", "ñ")


def phonetic(s: str) -> str:
    """Clave fonetica del espanol (lo que SUENA igual da la misma clave)."""
    n = norm(s)
    if not n:
        return ""
    rep = [("ch", "C"), ("ll", "y"), ("qu", "k"), ("gue", "ge"), ("gui", "gi"), ("ce", "se"), ("ci", "si"),
           ("ge", "je"), ("gi", "ji"), ("ph", "f"), ("sh", "C")]
    for a, b in rep:
        n = n.replace(a, b)
    n = n.replace("c", "k").replace("z", "s").replace("v", "b").replace("w", "u").replace("x", "ks")
    n = n.replace("h", "")
    n = re.sub(r"y$", "i", n)
    n = re.sub(r"(.)\1+", r"\1", n)        # letras dobles
    return n


def terms(glossary) -> list[str]:
    """Glosario (texto o lista) -> terminos limpios, sin repetir (el primero gana)."""
    if not glossary:
        return []
    raw = glossary if isinstance(glossary, (list, tuple)) else _HEAD_RE.sub("", str(glossary))
    parts = raw if isinstance(raw, (list, tuple)) else re.split(r"[,;\n]+", raw)
    out, seen = [], set()
    for t in parts:
        t = str(t).strip().strip(".").strip()
        t = _HEAD_RE.sub("", t).strip()
        if not t or len(t) > 60:
            continue
        k = " ".join(norm(w) for w in t.split())
        if not k.strip() or k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


class _Gloss:
    def __init__(self, glossary):
        self.terms = terms(glossary)
        self.single: dict[str, str] = {}          # norma -> termino (una palabra)
        self.multi: list[tuple[list[str], list[str]]] = []   # (normas, palabras) de varias palabras
        self.joined: dict[str, str] = {}          # normas juntas -> termino de una palabra
        self.glued: dict[str, str] = {}           # termino de varias palabras escrito pegado -> termino
        self.fuzzy: list[tuple[str, str, str]] = []           # (norma, clave fonetica, termino)
        for t in self.terms:
            ws = t.split()
            ns = [norm(w) for w in ws]
            if len(ws) == 1:
                if ns[0]:
                    self.single.setdefault(ns[0], t)
                    self.joined.setdefault(ns[0], t)
                    if len(ns[0]) >= FUZZY_MIN and not ns[0].isdigit():
                        self.fuzzy.append((ns[0], phonetic(t), t))
            elif all(ns):
                self.multi.append((ns, ws))
                if len("".join(ns)) >= FUZZY_MIN:
                    self.glued.setdefault("".join(ns), t)
        self.multi.sort(key=lambda m: -len(m[0]))
        self.symbols = {}
        for sym, names in SPOKEN_SYMBOLS.items():
            for nm in names:
                if nm in self.single:
                    self.symbols[sym] = self.single[nm]
                    break

    def __bool__(self):
        return bool(self.terms)


def _split(text: str) -> tuple[str, str, str]:
    """'¿NUBIs,' -> ('¿', 'NUBIs', ',')."""
    s = str(text)
    lead = s[: len(s) - len(s.lstrip())]
    s = s.strip()
    i = 0
    while i < len(s) and s[i] in _PREFIX:
        i += 1
    j = len(s)
    while j > i and s[j - 1] in _SUFFIX:
        j -= 1
    return lead + s[:i], s[i:j], s[j:]


def _cased(term: str, core: str, sent_start: bool) -> str:
    """Escritura del glosario; un termino en minusculas conserva la mayuscula de inicio de oracion."""
    if term == term.lower() and sent_start and core[:1].isupper():
        return term[:1].upper() + term[1:]
    return term


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def apply(words, glossary, same_count: bool = False) -> list[dict]:
    """Palabras corregidas contra el glosario (ver el docstring del modulo). Copias; mismo orden.
    `same_count`: sin unir ni partir palabras (los indices de palabra de quien llama siguen valiendo:
    el montaje guarda w0/w1 de la transcripcion por clip)."""
    g = glossary if isinstance(glossary, _Gloss) else _Gloss(glossary)
    src = [dict(w) for w in (words or []) if isinstance(w, dict)]
    if not g or not src:
        return src
    toks = [list(_split(w.get("text", ""))) for w in src]
    # 5) simbolo hablado: ".no" -> "dot" + "no"; "." suelto con duracion -> "dot"
    if g.symbols:
        out_w, out_t = [], []
        for w, (pre, core, suf) in zip(src, toks):
            raw = (pre + core + suf).strip()
            sym = raw[:1]
            if sym in g.symbols and len(raw) > 1 and raw[1:2].isalpha() and not same_count:
                term, rest = g.symbols[sym], raw[1:]
                a, b = float(w.get("start", 0.0)), float(w.get("end", 0.0))
                cut = a + (b - a) * len(term) / max(1, len(term) + len(norm(rest)))
                w1 = dict(w, text=term, start=round(a, 3), end=round(cut, 3))
                w2 = dict(w, text=rest, start=round(cut, 3), end=round(b, 3))
                for ww in (w1, w2):
                    out_w.append(ww)
                    out_t.append(list(_split(ww["text"])))
                continue
            if raw in g.symbols and float(w.get("end", 0)) - float(w.get("start", 0)) >= 0.08:
                w = dict(w, text=g.symbols[raw])
                out_w.append(w)
                out_t.append(list(_split(w["text"])))
                continue
            out_w.append(w)
            out_t.append([pre, core, suf])
        src, toks = out_w, out_t
    n = len(src)
    keep = [True] * n
    sent_start = [True] + [bool(toks[k - 1][2].strip()[-1:] in _SENT_END) if toks[k - 1][2].strip() else False
                           for k in range(1, n)]
    k = 0
    while k < n:
        nk = norm(toks[k][1])
        if not nk:
            k += 1
            continue
        done = False
        # 2) varias palabras = un termino de varias palabras (igual o casi igual: 3)
        for ns, ws in g.multi:
            L = len(ns)
            if k + L > n or any(toks[j][2].strip() for j in range(k, k + L - 1)):
                continue
            got = [norm(toks[j][1]) for j in range(k, k + L)]
            if not all(got):
                continue
            same = got == ns
            near = False
            # casi igual: solo si Whisper lo escribio como NOMBRE (cada palabra con mayuscula: "Taller
            # Prisa"); "un taller prisa" en minusculas nunca se vuelve "Taller Brisa"
            caps = all(toks[j][1][:1].isupper() for j in range(k, k + L))
            if not same and caps and (got[0] == ns[0] or got[-1] == ns[-1]):
                near = _ratio("".join(got), "".join(ns)) >= MULTI_RATIO and \
                    _ratio(phonetic("".join(got)), phonetic("".join(ns))) >= MULTI_RATIO
            if same or near:
                for j, w in zip(range(k, k + L), ws):
                    toks[j][1] = _cased(w, toks[j][1], sent_start[j])
                k += L
                done = True
                break
        if done:
            continue
        # 2b) palabras que juntas forman un termino de una palabra ("Voz Max" -> "VozMax")
        for L in range(MERGE_MAX, 1, -1) if not same_count else ():
            if k + L > n or any(toks[j][2].strip() for j in range(k, k + L - 1)) or \
                    any(toks[j][0].strip() for j in range(k + 1, k + L)):
                continue
            joined = "".join(norm(toks[j][1]) for j in range(k, k + L))
            term = g.joined.get(joined)
            if term:
                toks[k][1] = term
                toks[k][2] = toks[k + L - 1][2]
                src[k]["end"] = src[k + L - 1].get("end", src[k].get("end"))
                for j in range(k + 1, k + L):
                    keep[j] = False
                k += L
                done = True
                break
        if done:
            continue
        # 2c) un termino de varias palabras escrito pegado: "Tallerbrisa" -> "Taller Brisa"
        term = g.glued.get(nk) if nk not in g.single else None
        if term is not None:
            toks[k][1] = term
            k += 1
            continue
        # 1) igual
        term = g.single.get(nk)
        if term is not None:
            toks[k][1] = _cased(term, toks[k][1], sent_start[k])
            k += 1
            continue
        # 4) casi igual y suena igual
        if len(nk) >= FUZZY_MIN and not nk.isdigit() and toks[k][1][:1].isupper():
            pk = phonetic(nk)
            for tn, tp, t in g.fuzzy:
                if tn[:1] == nk[:1] and pk == tp and _ratio(nk, tn) >= FUZZY_RATIO:
                    toks[k][1] = _cased(t, toks[k][1], sent_start[k])
                    break
        k += 1
    out = []
    for keep_k, w, (pre, core, suf) in zip(keep, src, toks):
        if not keep_k:
            continue
        w["text"] = pre + core + suf
        out.append(w)
    return out


def fix_text(text: str, glossary) -> str:
    """apply() sobre un texto suelto (palabras separadas por espacios; los saltos de linea se quedan)."""
    g = glossary if isinstance(glossary, _Gloss) else _Gloss(glossary)
    if not g or not text:
        return text
    lines = []
    for line in str(text).split("\n"):
        ws = [{"text": t, "start": 0.0, "end": 0.0} for t in line.split()]
        lines.append(" ".join(w["text"] for w in apply(ws, g)) if ws else line)
    return "\n".join(lines)
