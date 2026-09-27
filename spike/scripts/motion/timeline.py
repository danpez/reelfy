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
SHOT_EPS = 0.1      # tolerancia (en cuadros) de tiempos de toma redondeados a ms


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
                fa = math.ceil(a * f - SHOT_EPS) if sa else math.floor(a * f + Q_EPS)
                fb = math.ceil(b * f - SHOT_EPS) if sb else math.ceil(b * f - Q_EPS)
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
        """Primer cuadro FINAL de cada toma nueva (tolera tiempos de toma redondeados a ms)."""
        f = float(fps or self.fps or 30.0)
        n = self.n_frames(f)
        out = sorted({math.ceil(t * f - SHOT_EPS) for t in self.shot_changes(shots)})
        return [k for k in out if 0 < k < n]

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

    def map_words(self, phrases: Iterable[Iterable[dict]] | None, min_dur: float = 0.01) -> list[list[dict]]:
        """Frases (listas de palabras con start/end originales) -> tiempos finales.

        Palabras enteramente dentro de un corte se descartan; las que lo cruzan se recortan.
        Frases que se quedan sin palabras se descartan. Se conservan los demas campos.
        """
        out = []
        for ph in phrases or []:
            nw = []
            for w in ph or []:
                try:
                    r = self.map_range(float(w["start"]), float(w["end"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if r is None or r[1] - r[0] < min_dur:
                    continue
                d = dict(w)
                d["start"], d["end"] = round(r[0], 4), round(r[1], 4)
                nw.append(d)
            if nw:
                out.append(nw)
        return out

    # ------------------------------------------------------------------ utilidades
    def frame_of(self, tf: float, fps: float | None = None) -> int:
        f = float(fps or self.fps or 30.0)
        return int(math.floor(tf * f + 1e-6))

    def __repr__(self) -> str:  # pragma: no cover
        return (f"Timeline({len(self.keeps)} keeps, {self.duration:.3f}s -> "
                f"{self.final_duration:.3f}s, fps={self.fps})")
