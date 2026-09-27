"""Curvas de easing y helpers de animacion (puros, sin skia).

Convencion: todas las funciones de easing reciben p en [0,1] y devuelven [0,1]
(salvo back/elastic, que pueden sobrepasar). Se usan en todos los renderers para que el
movimiento de Reelfy tenga un solo "idioma" (rapido al entrar, suave al asentarse).
"""
from __future__ import annotations

import math


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


def remap(x: float, a: float, b: float, c: float = 0.0, d: float = 1.0, clip: bool = True) -> float:
    """Mapea x de [a,b] a [c,d]."""
    if b == a:
        return d
    p = (x - a) / (b - a)
    if clip:
        p = clamp(p)
    return c + (d - c) * p


def linear(p: float) -> float:
    return clamp(p)


def in_quad(p: float) -> float:
    p = clamp(p)
    return p * p


def out_quad(p: float) -> float:
    p = clamp(p)
    return 1 - (1 - p) * (1 - p)


def in_out_quad(p: float) -> float:
    p = clamp(p)
    return 2 * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 2 / 2


def out_cubic(p: float) -> float:
    p = clamp(p)
    return 1 - (1 - p) ** 3


def in_cubic(p: float) -> float:
    p = clamp(p)
    return p ** 3


def in_out_cubic(p: float) -> float:
    p = clamp(p)
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def out_quart(p: float) -> float:
    p = clamp(p)
    return 1 - (1 - p) ** 4


def out_expo(p: float) -> float:
    p = clamp(p)
    return 1.0 if p >= 1 else 1 - 2 ** (-10 * p)


def in_expo(p: float) -> float:
    p = clamp(p)
    return 0.0 if p <= 0 else 2 ** (10 * p - 10)


def in_out_expo(p: float) -> float:
    p = clamp(p)
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    return 2 ** (20 * p - 10) / 2 if p < 0.5 else (2 - 2 ** (-20 * p + 10)) / 2


def out_back(p: float, s: float = 1.70158) -> float:
    p = clamp(p)
    c3 = s + 1
    return 1 + c3 * (p - 1) ** 3 + s * (p - 1) ** 2


def in_back(p: float, s: float = 1.70158) -> float:
    p = clamp(p)
    return (s + 1) * p ** 3 - s * p ** 2


def out_elastic(p: float) -> float:
    p = clamp(p)
    if p in (0.0, 1.0):
        return p
    c4 = (2 * math.pi) / 3
    return 2 ** (-10 * p) * math.sin((p * 10 - 0.75) * c4) + 1


def out_bounce(p: float) -> float:
    p = clamp(p)
    n1, d1 = 7.5625, 2.75
    if p < 1 / d1:
        return n1 * p * p
    if p < 2 / d1:
        p -= 1.5 / d1
        return n1 * p * p + 0.75
    if p < 2.5 / d1:
        p -= 2.25 / d1
        return n1 * p * p + 0.9375
    p -= 2.625 / d1
    return n1 * p * p + 0.984375


def smoothstep(p: float) -> float:
    p = clamp(p)
    return p * p * (3 - 2 * p)


def spring(t: float, freq: float = 3.2, damping: float = 0.42) -> float:
    """Respuesta de un resorte subamortiguado a un escalon (0 -> 1).

    t en segundos desde el inicio. freq en Hz, damping (zeta) en (0,1).
    Da el "pop" organico con un solo rebote que se usa en textos e iconos.
    """
    if t <= 0:
        return 0.0
    w = 2 * math.pi * freq
    z = clamp(damping, 0.01, 0.999)
    wd = w * math.sqrt(1 - z * z)
    return 1 - math.exp(-z * w * t) * (math.cos(wd * t) + (z * w / wd) * math.sin(wd * t))


EASINGS = {
    "linear": linear, "in_quad": in_quad, "out_quad": out_quad, "in_out_quad": in_out_quad,
    "in_cubic": in_cubic, "out_cubic": out_cubic, "in_out_cubic": in_out_cubic,
    "out_quart": out_quart, "in_expo": in_expo, "out_expo": out_expo, "in_out_expo": in_out_expo,
    "out_back": out_back, "in_back": in_back, "out_elastic": out_elastic, "out_bounce": out_bounce,
    "smoothstep": smoothstep,
}


def ease(name: str, p: float) -> float:
    return EASINGS.get(name, out_cubic)(p)


def envelope(lt: float, dur: float, tin: float = 0.25, tout: float = 0.2) -> tuple[float, float]:
    """Progreso de entrada y salida de un item.

    Devuelve (pin, pout): pin sube 0->1 en los primeros `tin` s; pout sube 0->1 en los
    ultimos `tout` s. Con pin=1 y pout=0 el item esta "asentado".
    """
    tin = max(1e-3, min(tin, dur * 0.5 if dur > 0 else tin))
    tout = max(1e-3, min(tout, dur * 0.5 if dur > 0 else tout))
    pin = clamp(lt / tin)
    pout = clamp((lt - (dur - tout)) / tout)
    return pin, pout


def stagger(i: int, lt: float, step: float = 0.06, dur: float = 0.3) -> float:
    """Progreso [0,1] del elemento i en una animacion escalonada."""
    return clamp((lt - i * step) / dur)


def pulse(t: float, period: float = 1.0) -> float:
    """Oscilacion suave 0..1..0 con el periodo dado."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * t / max(1e-6, period))


def wobble(t: float, seed: float = 0.0, speed: float = 1.0) -> float:
    """Ruido suave determinista en [-1,1] (suma de senos) para 'boil' de garabatos."""
    x = t * speed + seed * 13.37
    return (math.sin(x * 2.1) * 0.5 + math.sin(x * 3.7 + 1.3) * 0.3 + math.sin(x * 7.3 + 0.4) * 0.2)


def hold_steps(t: float, fps: float = 12.0) -> float:
    """Cuantiza el tiempo a 'fps' (animacion a doces, look hecho a mano)."""
    return math.floor(t * fps) / fps
