"""Linea de tiempo del render: keeps (tramos conservados), remapeo original -> final y uniones.

Convenciones (docs/MOTION-ENGINE.md, secciones 1 y 2):
  - El plan SIEMPRE guarda tiempos ORIGINALES de la fuente (segundos).
  - keeps = complemento de los cuts habilitados dentro de [0, duration] (mismo criterio que
    pipeline.render_from_plan: se descartan huecos <= MIN_KEEP s).
  - Con `fps`, los limites de cada keep se cuantizan a la reticula de cuadros de salida
    (k / fps). Asi video (cuadros) y voz (muestras) se cortan EXACTAMENTE en los mismos
    instantes y la duracion final es un numero entero de cuadros: sincronia perfecta.
    La cuantizacion EXPANDE el keep (inicio hacia abajo, fin hacia arriba): nunca corta la
    cola de una palabra ni una sibilante (a lo mas agrega < 1 cuadro de silencio).
  - `min_frag`: un keep conservado mas corto que esto (p. ej. 60 ms entre dos cortes) seria
    un flash de 1-2 cuadros con un "blip" de audio; se absorbe en el corte.
  - `shots`: si una union queda a <= `snap_s` s (min. 2 cuadros) de un cambio de toma, el
    limite del keep se mueve al cambio de toma (sin cuadros sueltos de otra toma).
  - Primer cuadro de una toma que empieza en t: shot_frame(t) = round(t * fps) (lo que hace el
    filtro fps con round=near). Un limite de keep en un cambio de toma (pegado, o que ya caia en
    el, o a menos de un cuadro) va a ese cuadro: el keep que empieza en la toma nueva no muestra el
    ultimo cuadro de la anterior ni el que termina en ella el primero de la siguiente.

Semantica de remap:
  - remap(t)          -> tiempo final, o None si t cae dentro de un corte.
  - remap_clamped(t)  -> igual, pero un t dentro de un corte se pega a la union donde
                         desemboca (antes del primer keep -> 0; despues del ultimo -> final).
  - inverse(tf)       -> tiempo original del cuadro final tf (en una union gana el keep
                         siguiente: el primer cuadro despues del corte).
"""
from __future__ import annotations

import bisect
import math
from typing import Iterable

MIN_KEEP = 0.05     # s: tramo minimo que vale la pena conservar (igual que pipeline.py)
MIN_FRAG = 0.15     # s: keeps mas cortos se absorben en el corte (flash frame de amateur)
SNAP_S = 0.12       # s: union tan cerca de un cambio de toma -> se pega al cambio (min 2 cuadros)
EPS = 1e-9
Q_EPS = 1e-6        # tolerancia (en cuadros) para limites que ya caen en la reticula
# cuadros: un item que empieza/termina tan cerca de una union o cambio de toma se pega a ella (un
# cuadro de la cuantizacion + el redondeo a ms de sus tiempos: 22/30 s se guarda como 0.7333)
FX_SNAP = 1.5


def shot_frame(t: float, fps: float) -> int:
    """Primer cuadro de la reticula de salida (k / fps) que muestra la toma que empieza en `t` s.

    El filtro fps (round=near) pone en el cuadro k el ultimo cuadro de la fuente cuyo pts
    redondeado es <= k: la toma nueva aparece en round(t * fps), con los empates hacia arriba (como
    av_rescale NEAR_INF). Vale a cualquier fps de la fuente y tolera los tiempos de toma redondeados
    a ms (< medio cuadro). (Antes: ceil(t * fps - 0.1), que con una toma de 30 fps redondeada 4 ms
    hacia arriba daba un cuadro de mas, y con otros fps se equivocaba de cuadro.)"""
    return int(math.floor(float(t) * float(fps) + 0.5 + Q_EPS))


def snap_window(fs: float, fe: float, frames: Iterable[int], fps: float,
                tol: float = FX_SNAP) -> tuple[float, float]:
    """Ventana FINAL (fs, fe) de un item con cada limite a <= `tol` cuadros de un limite de
    contenido (`frames`: cuadros finales de las uniones y cambios de toma, y 0 / n) pegado a el.

    Los tiempos de un item van a ms y los keeps a cuadros enteros: un zoom que termina en el inicio
    de un corte (60.93 s, el keep llega a 60.9333) o un cuadro antes quedaba fuera del ultimo cuadro
    del keep (un cuadro sin zoom justo antes del salto) y uno que empieza en un cambio de toma
    redondeado hacia arriba no cubria el primer cuadro de la toma. Asi el item cubre exactamente los
    cuadros de su tramo, sin un cuadro suelto de mas o de menos junto a la union. Si pegarlo lo
    dejaria con menos de un cuadro, se queda como estaba."""
    f = float(fps)
    fr = sorted(frames or [])
    if not fr or f <= 0:
        return fs, fe

    def near(t):
        k = t * f
        i = bisect.bisect_left(fr, k)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(fr) and abs(fr[j] - k) <= tol + 1e-6 and \
                    (best is None or abs(fr[j] - k) < abs(best - k)):
                best = fr[j]
        return t if best is None else best / f

    a, b = near(float(fs)), near(float(fe))
    if b - a < 1.0 / f - 1e-9:
        return fs, fe
    return a, b


def keeps_from_cuts(cuts: Iterable[dict] | None, duration: float,
                    min_keep: float = MIN_KEEP) -> list[tuple[float, float]]:
    """Complemento de los cuts HABILITADOS dentro de [0, duration].

    Mismo criterio que pipeline.render_from_plan: cuts ordenados por inicio, se ignoran
    los tramos conservados de <= min_keep s, cuts solapados se funden.
    """
    dur = max(0.0, float(duration or 0.0))
    act = []
    for c in cuts or []:
        if not isinstance(c, dict) or not c.get("enabled", True):
            continue
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e < s:
            s, e = e, s
        s, e = min(max(s, 0.0), dur), min(max(e, 0.0), dur)
        if e - s > EPS:
            act.append((s, e))
    act.sort()
    keeps, t = [], 0.0
    for s, e in act:
        if s - t > min_keep:
            keeps.append((t, s))
        t = max(t, e)
    if dur - t > min_keep:
        keeps.append((t, dur))
    return keeps


def word_cut_spans(cuts: Iterable[dict] | None) -> list[tuple[float, float]]:
    """Tramos (fuente, fundidos y ordenados) de los cortes habilitados que quitan PALABRAS a
    proposito: tomas (src "takes"), manuales y del usuario. Una palabra con el punto medio dentro
    se fue con el corte aunque la reticula de cuadros deje una astilla suya (map_words)."""
    iv = []
    for c in cuts or []:
        if not isinstance(c, dict) or c.get("enabled", True) is False:
            continue
        if not (c.get("src") in ("takes", "user", "manual") or c.get("manual")):
            continue
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e > s:
            iv.append((s, e))
    out: list[list[float]] = []
    for s, e in sorted(iv):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _shot_changes_orig(shots, end: float) -> list[float]:
    """Tiempos ORIGINALES de cambio de toma (inicio de cada toma salvo la primera)."""
    out = []
    for s in shots or []:
        try:
            t = float(s["start"]) if isinstance(s, dict) else float(s)
        except (KeyError, TypeError, ValueError):
            continue
        if EPS < t < end - EPS:
            out.append(t)
    return sorted(set(out))


def _merge(keeps: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted((float(a), float(b)) for a, b in keeps):
        if b - a <= EPS:
            continue
        if out and a <= out[-1][1] + EPS:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


# ---------------------------------------------------------------------- tiempos de palabras
# Subtitulos: ninguna palabra DICHA se pierde ni se estira sobre una pausa. Sin el alineador
# fino, whisper deja palabras de duracion 0 (el render las descartaba) y palabras que duran
# segundos (la pausa anterior queda dentro de la palabra). Valores medidos contra MMS_FA.
WORD_MIN = 0.08          # s: duracion minima visible de una palabra (karaoke)
WORD_S_PER_LETTER = 0.065  # s por letra al hablar (mediana en espanol)
WORD_S_PER_DIGIT = 0.2   # s por cifra ('2024' se dice 'dos mil veinticuatro')
WORD_TINY_TARGET = 0.30  # s: tope de lo que se presta a una palabra de duracion ~0
STRETCH_MIN_S = 1.0      # s: una palabra mas larga que esto...
STRETCH_FACTOR = 2.5     # ...y que 2.5x su duracion estimada esta estirada sobre una pausa
STRETCH_KEEP = 1.3       # al encogerla se queda con 1.3x su duracion estimada


def est_word_dur(text) -> float:
    """Duracion tipica de decir una palabra (s), por letras y cifras."""
    t = str(text or "")
    letters = sum(1 for c in t if c.isalpha())
    digits = sum(1 for c in t if c.isdigit())
    return min(1.5, max(WORD_MIN, WORD_S_PER_LETTER * letters + WORD_S_PER_DIGIT * digits))


def fix_word_times(words, keep: str = "end", min_dur: float = WORD_MIN, lo: float = 0.0,
                   hi: float | None = None, cap: bool = True) -> list[dict]:
    """Tiempos de palabras utilizables para subtitulos, SIN quitar ninguna (misma longitud y
    orden; copias que conservan los demas campos):
      1) inicios monotonos dentro de [lo, hi] y fin >= inicio;
      2) sin traslapes (una palabra termina a mas tardar cuando empieza la siguiente);
      3) `cap`: una palabra estirada (> STRETCH_MIN_S y > STRETCH_FACTOR x lo estimado) se
         encoge a STRETCH_KEEP x lo estimado conservando su final (`keep='end'`: el timing
         crudo de whisper mete la pausa ANTES de la palabra) o su inicio (`keep='start'`);
      4) duracion minima `min_dur`: una racha de palabras de duracion ~0 toma tiempo del hueco
         siguiente, de la palabra siguiente, del hueco anterior y de la anterior (en ese orden,
         sin dejar a las vecinas por debajo de su minimo ni de la mitad de lo que duran) y se
         reparte por letras.
    """
    out = [dict(w) for w in words or []]
    n = len(out)
    if not n:
        return out
    S, E = [], []
    for w in out:
        try:
            s = float(w.get("start") or 0.0)
            e = float(w.get("end") if w.get("end") is not None else s)
        except (TypeError, ValueError):
            s = e = S[-1] if S else float(lo or 0.0)
        if e < s:
            s, e = e, s
        S.append(s)
        E.append(e)
    lo = float(lo or 0.0)
    hi = float(hi) if hi is not None else max(max(E), lo)
    est = [est_word_dur(w.get("text")) for w in out]
    # 1) orden
    for i in range(n):
        S[i] = min(max(S[i], lo, S[i - 1] if i else lo), hi)
        E[i] = min(max(E[i], S[i]), hi)
    # 2) sin traslapes
    for i in range(n - 1):
        if E[i] > S[i + 1]:
            E[i] = S[i + 1]
    # 3) estiradas sobre una pausa
    if cap:
        for i in range(n):
            if E[i] - S[i] > max(STRETCH_MIN_S, STRETCH_FACTOR * est[i]):
                d = STRETCH_KEEP * est[i]
                if keep == "start":
                    E[i] = S[i] + d
                else:
                    S[i] = E[i] - d
    # 4) duracion minima (rachas contiguas de palabras de duracion ~0)
    if min_dur > 0:
        tiny = [E[i] - S[i] < min_dur - 1e-9 for i in range(n)]

        def spare(k):
            """Lo que la vecina k puede prestar sin bajar de su minimo (y de la mitad de lo
            que dura)."""
            cur = E[k] - S[k]
            return max(0.0, cur - max(min_dur, min(0.6 * est[k], 0.5 * cur)))

        i = 0
        while i < n:
            if not tiny[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and tiny[j + 1] and S[j + 1] - E[j] < 1e-3:
                j += 1
            tgt = [min(max(min_dur, est[k]), WORD_TINY_TARGET) for k in range(i, j + 1)]
            need = sum(tgt)
            s, e = S[i], E[j]
            nxt_lim = S[j + 1] if j + 1 < n else hi
            e += min(max(0.0, need - (e - s)), max(0.0, nxt_lim - e))    # hueco siguiente
            if need - (e - s) > 1e-9 and j + 1 < n and not tiny[j + 1]:  # palabra siguiente
                e += min(need - (e - s), spare(j + 1))
                S[j + 1] = max(S[j + 1], e)
            prv_lim = E[i - 1] if i else lo
            if need - (e - s) > 1e-9:                                     # hueco anterior
                s -= min(need - (e - s), max(0.0, s - prv_lim))
            if need - (e - s) > 1e-9 and i and not tiny[i - 1]:          # palabra anterior
                s -= min(need - (e - s), spare(i - 1))
                E[i - 1] = min(E[i - 1], s)
            have, t = e - s, s
            for k, g in zip(range(i, j + 1), tgt):
                d = have * g / need
                S[k], E[k] = t, t + d
                t += d
            i = j + 1
    for w, s, e in zip(out, S, E):
        w["start"], w["end"] = s, e
    return out


class Timeline:
    """Mapa entre el tiempo original de la fuente y el tiempo final (ya sin cortes)."""

    def __init__(self, keeps: Iterable[tuple[float, float]] | None, duration: float | None = None,
                 fps: float | None = None, min_frag: float = 0.0, shots=None,
                 snap_s: float = SNAP_S):
        self.fps = float(fps) if fps else None
        ks = _merge(keeps if keeps is not None else [(0.0, float(duration or 0.0))])
        if duration is not None:
            d = float(duration)
            ks = [(max(0.0, a), min(d, b)) for a, b in ks if a < d]
            ks = [(a, b) for a, b in ks if b - a > EPS]
        # 1) uniones pegadas a cambios de toma (sin cuadros sueltos de otra toma)
        snapped = [(a, b, False, False) for a, b in ks]
        changes = _shot_changes_orig(shots, ks[-1][1] if ks else 0.0)
        if changes and snap_s > 0:
            tol = max(float(snap_s), 2.0 / (self.fps or 30.0)) + 1e-6
            out = []
            for a, b, _, _ in snapped:
                sa = sb = False
                for s in changes:
                    if not (a + EPS < s < b - EPS):
                        continue
                    if s - a <= tol and b - s > tol:
                        a, sa = s, True
                    elif b - s <= tol and s - a > tol:
                        b, sb = s, True
                out.append((a, b, sa, sb))
            snapped = out
        # 2) fragmentos demasiado cortos -> dentro del corte (si queda al menos uno)
        if min_frag > 0 and len(snapped) > 1:
            big = [k for k in snapped if k[1] - k[0] >= min_frag - EPS]
            snapped = big or [max(snapped, key=lambda k: k[1] - k[0])]
        # 3) reticula de salida, expandiendo (un limite pegado a una toma va a su cuadro)
        if self.fps:
            f = self.fps
            q = []
            for a, b, sa, sb in snapped:
                fa = shot_frame(a, f) if sa else math.floor(a * f + Q_EPS)
                fb = shot_frame(b, f) if sb else math.ceil(b * f - Q_EPS)
                # un limite que ya caia en un cambio de toma (o a menos de un cuadro de el, del lado
                # de su toma) tampoco toma un cuadro de la otra: la expansion a la reticula se
                # detiene en el primer cuadro de la toma (solo mueve el limite si estaba a < 1 cuadro)
                i = bisect.bisect_right(changes, a + EPS) - 1      # ultimo cambio <= a
                if i >= 0:
                    fa = max(fa, shot_frame(changes[i], f))
                j = bisect.bisect_left(changes, b - EPS)            # primer cambio >= b
                if j < len(changes):
                    fb = min(fb, shot_frame(changes[j], f))
                if duration is not None:
                    fb = min(fb, max(fa, math.ceil(float(duration) * f - Q_EPS)))
                if fb > fa:
                    q.append((fa / f, fb / f))
            ks = _merge(q)
        else:
            ks = _merge((a, b) for a, b, _, _ in snapped)
        self.keeps: list[tuple[float, float]] = ks
        self.duration = float(duration) if duration is not None else (ks[-1][1] if ks else 0.0)
        self.offsets: list[float] = []      # inicio FINAL de cada keep
        acc = 0.0
        for a, b in ks:
            self.offsets.append(acc)
            acc += b - a
        self.final_duration = acc
        self._starts = [a for a, _ in ks]

    # ------------------------------------------------------------------ construccion
    @classmethod
    def from_plan(cls, plan: dict, fps: float | None = None, duration: float | None = None,
                  min_keep: float = MIN_KEEP, min_frag: float = MIN_FRAG,
                  snap_shots: bool = True) -> "Timeline":
        dur = float(duration if duration is not None else (plan.get("duration") or 0.0))
        keeps = keeps_from_cuts(plan.get("cuts") or [], dur, min_keep)
        return cls(keeps, dur, fps, min_frag=min_frag,
                   shots=plan.get("shots") if snap_shots else None)

    def to_dict(self) -> dict:
        return {"keeps": [list(k) for k in self.keeps], "duration": self.duration,
                "fps": self.fps, "final_duration": self.final_duration}

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        return cls([tuple(k) for k in d["keeps"]], d.get("duration"), d.get("fps"))

    # ------------------------------------------------------------------ propiedades
    @property
    def removed(self) -> float:
        return max(0.0, self.duration - self.final_duration)

    @property
    def is_identity(self) -> bool:
        return (len(self.keeps) == 1 and abs(self.keeps[0][0]) < EPS
                and abs(self.keeps[0][1] - self.duration) < 1e-6)

    def n_frames(self, fps: float | None = None) -> int:
        f = float(fps or self.fps or 30.0)
        return int(round(self.final_duration * f))

    def frame_ranges(self, fps: float | None = None) -> list[tuple[int, int]]:
        """Rangos [ka, kb) de cuadros de la reticula ORIGINAL (k/fps) que se conservan."""
        f = float(fps or self.fps or 30.0)
        return [(math.floor(a * f + Q_EPS), math.ceil(b * f - Q_EPS)) for a, b in self.keeps]

    # ------------------------------------------------------------------ remapeo
    def _idx(self, t: float) -> int:
        """Indice del ultimo keep con inicio <= t (o -1)."""
        return bisect.bisect_right(self._starts, t + EPS) - 1

    def remap(self, t: float) -> float | None:
        t = float(t)
        i = self._idx(t)
        if i < 0:
            return None
        a, b = self.keeps[i]
        if t <= b + EPS:
            return self.offsets[i] + min(max(t - a, 0.0), b - a)
        return None

    def remap_clamped(self, t: float) -> float:
        t = float(t)
        if not self.keeps:
            return 0.0
        i = self._idx(t)
        if i < 0:
            return 0.0
        a, b = self.keeps[i]
        if t <= b:
            return self.offsets[i] + max(0.0, t - a)
        return self.offsets[i] + (b - a)     # dentro del corte -> union siguiente

    def inverse(self, tf: float) -> float:
        tf = float(tf)
        if not self.keeps:
            return 0.0
        if tf <= 0:
            return self.keeps[0][0]
        if tf >= self.final_duration:
            return self.keeps[-1][1]
        i = bisect.bisect_right(self.offsets, tf + EPS) - 1
        i = max(0, min(i, len(self.keeps) - 1))
        a, b = self.keeps[i]
        return min(b, a + (tf - self.offsets[i]))

    def in_cut(self, t: float) -> bool:
        return self.remap(t) is None

    # ------------------------------------------------------------------ uniones / tomas
    @property
    def joins(self) -> list[dict]:
        """Uniones entre keeps consecutivos, en tiempo final.

        [{index, t (final), before (t orig donde termina el keep anterior),
          after (t orig donde empieza el siguiente), gap (s eliminados)}]
        """
        out = []
        for i in range(1, len(self.keeps)):
            before, after = self.keeps[i - 1][1], self.keeps[i][0]
            out.append({"index": i, "t": self.offsets[i], "before": before, "after": after,
                        "gap": after - before})
        return out

    def shot_changes(self, shots: Iterable[dict] | None) -> list[float]:
        """Tiempos FINALES donde cambia la toma (un cambio dentro de un corte cae en la union)."""
        sh = sorted((s for s in shots or [] if isinstance(s, dict) and "start" in s),
                    key=lambda s: float(s["start"]))
        tol = 0.5 / self.fps if self.fps else 0.02
        out: list[float] = []
        for s in sh[1:]:
            tf = self.remap_clamped(float(s["start"]))
            if tf <= tol or tf >= self.final_duration - tol:
                continue
            if out and abs(tf - out[-1]) <= tol:
                continue
            out.append(tf)
        return out

    def join_frames(self, fps: float | None = None) -> list[int]:
        """Cuadro FINAL donde empieza cada keep despues de una union (primer cuadro tras el corte)."""
        f = float(fps or self.fps or 30.0)
        return [int(round(j["t"] * f)) for j in self.joins]

    def shot_change_frames(self, shots: Iterable[dict] | None, fps: float | None = None) -> list[int]:
        """Primer cuadro FINAL de cada toma nueva (shot_frame: tolera tiempos de toma a ms)."""
        f = float(fps or self.fps or 30.0)
        n = self.n_frames(f)
        out = sorted({shot_frame(t, f) for t in self.shot_changes(shots)})
        return [k for k in out if 0 < k < n]

    def content_frames(self, shots: Iterable[dict] | None = None,
                       fps: float | None = None) -> list[int]:
        """Cuadros FINALES donde cambia el contenido: 0, cada union, cada cambio de toma y el final
        (los limites a los que snap_window pega los items)."""
        f = float(fps or self.fps or 30.0)
        n = self.n_frames(f)
        return sorted({0, n} | set(self.join_frames(f)) | set(self.shot_change_frames(shots, f)))

    def map_range(self, start: float, end: float, min_len: float = 1e-6) -> tuple[float, float] | None:
        """[start, end] original -> (fs, fe) final, recortando lo que caiga en cortes.
        None si el rango queda completamente dentro de cortes."""
        s, e = float(start), float(end)
        if e < s:
            s, e = e, s
        fs, fe = self.remap_clamped(s), self.remap_clamped(e)
        if fe - fs <= min_len:
            return None
        return fs, fe

    def map_item(self, item: dict) -> tuple[float, float] | None:
        """Ventana final de un item del plan (start/end, o `at` + `dur`)."""
        if "start" in item and "end" in item:
            try:
                return self.map_range(float(item["start"]), float(item["end"]))
            except (TypeError, ValueError):
                return None
        if "at" in item:
            try:
                tf = self.remap_clamped(float(item["at"]))
            except (TypeError, ValueError):
                return None
            d = float(item.get("dur") or 0.0)
            return tf, tf + d
        return None

    def map_shots(self, shots: Iterable[dict] | None) -> list[dict]:
        """Tomas con tiempos finales (se descartan las que quedan dentro de cortes).
        Cada toma conserva sus campos y agrega `index` (posicion original)."""
        out = []
        for i, s in enumerate(shots or []):
            if not isinstance(s, dict):
                continue
            try:
                r = self.map_range(float(s.get("start", 0.0)), float(s.get("end", self.duration)))
            except (TypeError, ValueError):
                continue
            if r is None:
                continue
            d = dict(s)
            d["start"], d["end"], d["index"] = r[0], r[1], i
            out.append(d)
        return out

    def map_words(self, phrases: Iterable[Iterable[dict]] | None, min_dur: float = 0.01,
                  drop_spans: Iterable[tuple[float, float]] | None = None) -> list[list[dict]]:
        """Frases (listas de palabras con start/end originales) -> tiempos finales.

        Palabras enteramente dentro de un corte se descartan; las que lo cruzan se recortan.
        Frases que se quedan sin palabras se descartan. Se conservan los demas campos.
        `drop_spans` (word_cut_spans: tomas y cortes manuales): una palabra con el punto medio
        dentro se descarta aunque la reticula de cuadros (que expande los keeps) deje una astilla
        suya en tiempo conservado ('el [el] agua' no sale como subtitulo de 30 ms).

        Ninguna palabra DICHA se pierde: una palabra de duracion ~0 (timing crudo de whisper)
        o que queda con menos de `min_dur` s se conserva si su punto medio cae en tiempo
        conservado, y en tiempo final `fix_word_times` le presta una duracion corta (hueco o
        vecinas) y encoge las palabras estiradas sobre una pausa (conserva su final).
        """
        flat: list[tuple[int, dict]] = []
        spans = list(drop_spans or [])
        for pi, ph in enumerate(phrases or []):
            for w in ph or []:
                try:
                    s, e = float(w["start"]), float(w["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                if e < s:
                    s, e = e, s
                if spans and any(a < (s + e) / 2.0 < b for a, b in spans):
                    continue                # la quito una toma o un corte manual
                r = self.map_range(s, e)
                if r is None or r[1] - r[0] < min_dur:
                    tm = self.remap((s + e) / 2.0)
                    if tm is None:
                        continue            # se fue con el corte
                    r = (tm, tm)
                d = dict(w)
                d["start"], d["end"] = r[0], r[1]
                flat.append((pi, d))
        fixed = fix_word_times([d for _, d in flat], keep="end", lo=0.0, hi=self.final_duration)
        out: list[list[dict]] = []
        last = None
        for (pi, _), d in zip(flat, fixed):
            d["start"], d["end"] = round(d["start"], 4), round(d["end"], 4)
            if pi != last:
                out.append([])
                last = pi
            out[-1].append(d)
        return out

    # ------------------------------------------------------------------ utilidades
    def frame_of(self, tf: float, fps: float | None = None) -> int:
        f = float(fps or self.fps or 30.0)
        return int(math.floor(tf * f + 1e-6))

    def __repr__(self) -> str:  # pragma: no cover
        return (f"Timeline({len(self.keeps)} keeps, {self.duration:.3f}s -> "
                f"{self.final_duration:.3f}s, fps={self.fps})")
