"""Color del motor: igualacion entre tomas, looks, blanco y negro, vinieta, grano y nitidez.

Contrato (docs/MOTION-ENGINE.md, 4.2b):
  Grader(base_video, shots_final, W, H, fps).prepare()
  Grader.apply(img, cfg, shot_idx, ctx) -> skia.Image   (igualacion + look + vinieta + grano + nitidez)
  RENDERERS = {"bw": apply}                              (efecto de video por rango)

Como funciona:
  - prepare() muestrea ~6 cuadros por toma (reducidos) y mide: piel del rostro (YuNet +
    filtro YCrCb), balance gris robusto (shades-of-gray p=4 sin pixeles muy saturados) y
    percentiles de luma. Por toma calcula ganancias lineales por canal (balance de blancos
    + exposicion) y niveles. Las tomas con persona se igualan sobre la PIEL (misma persona =
    mismo tono de piel: 80% piel / 20% balance gris) y la exposicion lleva la cara a la
    mediana de las caras de todas las tomas; las tomas sin persona se acercan con el balance
    gris al promedio de las tomas con persona.
  - apply() por cuadro, sobre RGBA uint8 (todo en pasadas de cv2 baratas en un hilo):
      nitidez (3x3) + claridad (contraste local a 1/4 de resolucion) -> grano (antes de la
      curva, sin repetirse) -> LUT por canal (correccion de toma + look: niveles, negros con
      pie suave, medios, curva S filmica, fade, temperatura, virado) -> saturacion tipo
      vibrance que protege la piel (una matriz 4x4, cv2.transform) -> vinieta.
  - Looks: el default del estilo dinamico (warm_clean) esta calibrado contra la referencia
    (negros profundos, medios un poco abajo, +25-35% de saturacion con piel natural, claridad
    y vinieta). natural es la version discreta.

cfg (plan.fx.grade): enabled, look, match_shots, vignette, grain, sharpen y opcionales
  strength (0..1.5, fuerza del look), exposure (pasos), temperature (-1..1), tint (-1..1),
  saturation (multiplicador), contrast (suma a la del look, -0.5..0.5), clarity (0..1).
"""
from __future__ import annotations

import json
import math
import subprocess
import zlib
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import skia

from . import ctx as C
from . import ease


# ---------------------------------------------------------------- parametros tolerantes
def num(d, k: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
    """Numero de un dict del plan tolerante a None, strings y NaN (cae al default) y acotado."""
    v = d.get(k, default) if isinstance(d, dict) else default
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = float(default)
    if not math.isfinite(v):
        v = float(default)
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


# ---------------------------------------------------------------- funciones de transferencia
def srgb_to_lin(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, np.float64)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(np.asarray(x, np.float64), 0.0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def luma(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def _smoothstep(a: float, b: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def s_curve(x: np.ndarray, contrast: float, pivot: float = 0.45, k: float = 7.0) -> np.ndarray:
    """Curva S filmica normalizada (0->0, 1->1) mezclada con la identidad segun `contrast`.
    contrast < 0 aplana (curva inversa suave)."""
    x = np.asarray(x, np.float64)
    if abs(contrast) < 1e-4:
        return x
    sig = lambda v: 1.0 / (1.0 + np.exp(-k * (v - pivot)))  # noqa: E731
    lo, hi = sig(0.0), sig(1.0)
    s = (sig(x) - lo) / (hi - lo)
    if contrast >= 0:
        return (1 - contrast) * x + contrast * s
    c = -contrast
    return (1 + c) * x - c * s


def crush_blacks(y: np.ndarray, b: float, soft: float = 0.35) -> np.ndarray:
    """Punto negro en `b` con pie suave (softplus): lo que esta por debajo de b va a ~0 sin
    quiebre visible y 1 sigue en 1."""
    y = np.asarray(y, np.float64)
    if b <= 1e-4:
        return y
    k = 1.0 / max(1e-3, soft * b)
    sp = lambda z: np.logaddexp(0.0, k * z) / k  # noqa: E731
    lo, hi = sp(-b), sp(1.0 - b)
    return np.clip((sp(y - b) - lo) / (hi - lo), 0.0, 1.0)


# ---------------------------------------------------------------- looks
# contrast: fuerza de la curva S; pivot: punto medio; sat: saturacion general (croma fuera del
# eje de piel); skin_sat: saturacion sobre el eje naranja de la piel (vibrance que la protege);
# temp/tint: balance; exposure: pasos; gamma: medios (>1 los baja); black: punto negro con pie
# suave; fade: negros levantados; shadows/highs: virado aditivo (R,G,B); clarity: contraste
# local; vig: vinieta que agrega el look (se suma a cfg.vignette).
LOOKS: dict[str, dict] = {
    "natural": dict(contrast=0.18, pivot=0.45, sat=1.06, skin_sat=1.02, temp=0.0, tint=0.0,
                    exposure=0.0, gamma=1.0, black=0.012, fade=0.0, shadows=(0.0, 0.0, 0.0),
                    highs=(0.0, 0.0, 0.0), clarity=0.08, vig=0.0),
    "warm_clean": dict(contrast=0.50, pivot=0.46, sat=1.26, skin_sat=1.08, temp=0.08, tint=0.02,
                       exposure=0.0, gamma=1.04, black=0.03, fade=0.0,
                       shadows=(-0.004, 0.0, 0.010), highs=(0.012, 0.004, -0.010), clarity=0.20,
                       vig=0.16),
    "cinematic": dict(contrast=0.48, pivot=0.48, sat=1.02, skin_sat=1.06, temp=0.02, tint=0.0,
                      exposure=-0.05, gamma=1.06, black=0.035, fade=0.03,
                      shadows=(-0.024, 0.010, 0.040), highs=(0.034, 0.010, -0.032), clarity=0.18,
                      vig=0.16),
    "bright_pop": dict(contrast=0.52, pivot=0.44, sat=1.40, skin_sat=1.12, temp=0.05, tint=0.0,
                       exposure=0.12, gamma=1.0, black=0.035, fade=0.0,
                       shadows=(0.0, 0.0, 0.006), highs=(0.008, 0.004, -0.006), clarity=0.22,
                       vig=0.05),
    "moody": dict(contrast=0.52, pivot=0.50, sat=0.84, skin_sat=0.95, temp=-0.05, tint=-0.02,
                  exposure=-0.28, gamma=1.04, black=0.03, fade=0.045,
                  shadows=(-0.012, 0.014, 0.028), highs=(0.014, 0.004, -0.010), clarity=0.24,
                  vig=0.20),
    "bw_contrast": dict(contrast=0.58, pivot=0.46, sat=0.0, skin_sat=0.0, temp=0.0, tint=0.0,
                        exposure=0.0, gamma=1.0, black=0.0, fade=0.0, shadows=(0.0, 0.0, 0.0),
                        highs=(0.0, 0.0, 0.0), clarity=0.22, vig=0.0, mono=True),
}
DEFAULT_LOOK = "warm_clean"
_ALGO = 6  # version del algoritmo de igualacion (invalida la cache al cambiar)

# Piel de referencia agradable (sRGB) para tirar suavemente el tono de piel objetivo
_SKIN_REF = srgb_to_lin(np.array([224, 170, 142]) / 255.0)
_SKIN_REF_RATIO = np.array([_SKIN_REF[0] / _SKIN_REF[1], 1.0, _SKIN_REF[2] / _SKIN_REF[1]])


def _look(cfg: dict) -> tuple[str, dict]:
    name = cfg.get("look") if isinstance(cfg, dict) else None
    if not isinstance(name, str) or name not in LOOKS:
        name = DEFAULT_LOOK
    return name, LOOKS[name]


# ---------------------------------------------------------------- lectura de cuadros
def _probe_frames(video: str) -> int | None:
    import paths
    try:
        out = subprocess.run([paths.FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
                              "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", video],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return int(out.split(",")[0])
    except Exception:
        return None


def _runs(idx: list[int]) -> list[tuple[int, int]]:
    """Indices ordenados -> corridas consecutivas [(a, b)] inclusivas."""
    out: list[list[int]] = []
    for i in idx:
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [(a, b) for a, b in out]


def decode_frames(video: str, indices, w: int, h: int, max_terms: int = 40) -> dict[int, np.ndarray]:
    """Decodifica solo los cuadros pedidos (indice de cuadro) reducidos a w x h. RGB uint8.
    Agrupa indices consecutivos (between) y parte en varias pasadas si hay muchos terminos
    (el evaluador de expresiones de ffmpeg tiene limite de profundidad)."""
    import paths
    idx = sorted(set(int(i) for i in indices if i >= 0))
    if not idx:
        return {}
    runs = _runs(idx)
    out: dict[int, np.ndarray] = {}
    fsz = w * h * 3
    for c in range(0, len(runs), max_terms):
        chunk = runs[c:c + max_terms]
        terms = [f"eq(n\\,{a})" if a == b else f"between(n\\,{a}\\,{b})" for a, b in chunk]
        want = [i for a, b in chunk for i in range(a, b + 1)]
        cmd = [paths.FFMPEG, "-v", "error", "-nostdin", "-i", str(video), "-an",
               "-vf", f"select={'+'.join(terms)},scale={w}:{h}:flags=area", "-fps_mode", "passthrough",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        k = 0
        try:
            while k < len(want):
                buf = proc.stdout.read(fsz)
                if not buf or len(buf) < fsz:
                    break
                out[want[k]] = np.frombuffer(buf, np.uint8).reshape(h, w, 3).copy()
                k += 1
        finally:
            proc.stdout.close()
            proc.kill()
            proc.wait()
    return out


# ---------------------------------------------------------------- medicion por toma
def _skin_pixels(rgb: np.ndarray, det) -> np.ndarray | None:
    """Pixeles de piel (float 0..1, N x 3) de la cara principal del cuadro, o None."""
    h, w = rgb.shape[:2]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    det.setInputSize((w, h))
    _, faces = det.detect(bgr)
    if faces is None or len(faces) == 0:
        return None
    f = max(faces, key=lambda r: r[2] * r[3] * r[14])
    x, y, fw, fh = f[:4]
    if fw < 12 or fh < 12:
        return None
    x0, x1 = int(max(0, x + 0.18 * fw)), int(min(w, x + 0.82 * fw))
    y0, y1 = int(max(0, y + 0.30 * fh)), int(min(h, y + 0.88 * fh))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    roi = rgb[y0:y1, x0:x1]
    ycc = cv2.cvtColor(roi, cv2.COLOR_RGB2YCrCb).reshape(-1, 3).astype(np.int32)
    px = roi.reshape(-1, 3).astype(np.float64) / 255.0
    ok = ((ycc[:, 1] >= 133) & (ycc[:, 1] <= 178) & (ycc[:, 2] >= 77) & (ycc[:, 2] <= 130)
          & (ycc[:, 0] > 40) & (ycc[:, 0] < 245))
    if ok.sum() < 25:
        return None
    return px[ok]


def _grayworld(px_lin: np.ndarray, px_gam: np.ndarray) -> np.ndarray:
    """Balance gris robusto (shades-of-gray p=4) sobre pixeles medios y poco saturados."""
    y = luma(px_gam)
    mx, mn = px_gam.max(axis=1), px_gam.min(axis=1)
    sat = (mx - mn) / np.maximum(mx, 1e-3)
    ok = (y > 0.06) & (y < 0.94) & (sat < 0.45)
    if ok.sum() < 200:
        ok = (y > 0.03) & (y < 0.97)
    v = px_lin[ok]
    if len(v) == 0:
        return np.ones(3)
    e = np.power(np.mean(np.power(v, 4.0), axis=0), 0.25)
    return e / max(1e-6, e[1])


def _percentiles(px_gam: np.ndarray, gains: np.ndarray) -> tuple[float, float, float]:
    lin = srgb_to_lin(px_gam) * gains
    y = luma(lin_to_srgb(np.clip(lin, 0, 1)))
    lo, mid, hi = np.percentile(y, [0.5, 50, 99.5])
    return float(lo), float(mid), float(hi)


def _shot_range(s, fps: float) -> tuple[float, float]:
    if isinstance(s, dict):
        return num(s, "start", 0.0), num(s, "end", 0.0)
    return float(s[0]), float(s[1])


def _burn_cap(px: np.ndarray, wb: np.ndarray, e: float) -> float:
    """Tope de exposicion para no quemar la escena: la mediana no pasa de ~0.66 y la zona que
    el knee de la LUT comprime (lineal > 0.82) no crece mas de 10 puntos porcentuales."""
    lin_px = luma(srgb_to_lin(px) * wb)
    hot0 = float(np.mean(lin_px > 0.82))
    for _ in range(16):
        ye = lin_px * e
        if float(lin_to_srgb(np.median(ye))) <= 0.66 and float(np.mean(ye > 0.82)) <= hot0 + 0.10:
            break
        e = max(1.0, e * 0.96)
    return e


# ---------------------------------------------------------------- Grader
class Grader:
    """Igualacion de tomas + look. Serializable (pickle) para los procesos de render."""

    def __init__(self, base_video, shots_final, W: int, H: int, fps: float, cache_dir=None,
                 samples_per_shot: int = 6):
        self.base_video = str(base_video) if base_video else None
        self.shots = [_shot_range(s, fps) for s in (shots_final or [])]
        self.W, self.H, self.fps = int(W), int(H), float(fps)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.samples_per_shot = int(samples_per_shot)
        self.corr: list[dict] = []      # correccion por toma (JSON)
        self._luts: dict = {}
        self._vig: dict = {}

    # -------------------------------------------------- estado serializable
    def __getstate__(self):
        d = dict(self.__dict__)
        d["_luts"], d["_vig"] = {}, {}
        return d

    def to_dict(self) -> dict:
        return {"shots": self.shots, "corr": self.corr}

    def load_dict(self, d: dict) -> "Grader":
        self.corr = list(d.get("corr") or [])
        self._luts.clear()
        return self

    def _cache_file(self) -> Path | None:
        if not self.cache_dir or not self.base_video or not Path(self.base_video).exists():
            return None
        import hashlib
        st = Path(self.base_video).stat()
        key = json.dumps([Path(self.base_video).name, st.st_size, int(st.st_mtime), self.W, self.H,
                          self.shots, _ALGO])
        return self.cache_dir / f"grade_{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"

    # -------------------------------------------------- medicion
    def prepare(self, on_pct=None) -> list[dict]:
        """Mide cada toma y calcula su correccion. Idempotente y cacheado en disco."""
        cf = self._cache_file()
        if cf is not None and cf.exists():
            try:
                self.load_dict(json.loads(cf.read_text()))
                if len(self.corr) == len(self.shots):
                    return self.corr
            except Exception:
                pass
        if not self.shots or not self.base_video:
            self.corr = []
            return self.corr
        sw = 360
        sh = int(round(sw * self.H / self.W / 2) * 2)
        want: dict[int, int] = {}
        nfr = int(round(max(e for _, e in self.shots) * self.fps)) + 1
        for si, (a, b) in enumerate(self.shots):
            dur = max(0.0, b - a)
            m = 0.12 if dur > 0.6 else 0.0
            K = self.samples_per_shot if dur > 0.4 else 1
            for j in range(K):
                t = a + m + (dur - 2 * m) * ((j + 0.5) / K)
                n = min(nfr - 1, max(0, int(round(t * self.fps))))
                want.setdefault(n, si)
        frames = decode_frames(self.base_video, want.keys(), sw, sh)
        if on_pct:
            on_pct(50)
        import paths
        det = cv2.FaceDetectorYN.create(str(paths.YUNET), "", (sw, sh), 0.7, 0.3, 5000)
        stats = []
        for si in range(len(self.shots)):
            fr = [frames[n] for n, s in want.items() if s == si and n in frames]
            if not fr:
                stats.append(None)
                continue
            px = np.concatenate([f.reshape(-1, 3)[::3] for f in fr]).astype(np.float64) / 255.0
            skins = [p for p in (_skin_pixels(f, det) for f in fr) if p is not None]
            skin = None
            if len(skins) >= max(1, len(fr) // 3):
                sp = np.concatenate(skins)
                skin = np.median(srgb_to_lin(sp), axis=0)
            lin = srgb_to_lin(px)
            stats.append({"gw": _grayworld(lin, px), "skin": skin, "px": px})
        self.corr = self._solve(stats)
        if cf is not None:
            cf.parent.mkdir(parents=True, exist_ok=True)
            tmp = cf.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.to_dict()))
            tmp.replace(cf)
        if on_pct:
            on_pct(100)
        return self.corr

    def _solve(self, stats: list) -> list[dict]:
        """De mediciones a correcciones por toma (ganancias lineales + niveles)."""
        n = len(stats)
        corr = [{"gains": [1.0, 1.0, 1.0], "black": 0.0, "white": 1.0, "face": False} for _ in range(n)]
        skin_ids = [i for i, s in enumerate(stats) if s is not None and s["skin"] is not None]
        valid = [i for i, s in enumerate(stats) if s is not None]
        # ---- balance de blancos. Con persona: la piel manda (90%; es la misma persona en
        # todas las tomas) y el balance gris de la escena evita sobrecorregir (10%). Sin
        # persona: balance gris contra el de las tomas con persona, con fuerza parcial.
        wb = [np.ones(3) for _ in range(n)]
        gw_all = {i: stats[i]["gw"] for i in valid}
        ref_ids = skin_ids if skin_ids else valid
        gw_ref = np.ones(3)
        if ref_ids:
            gw_ref = np.exp(np.mean([np.log(gw_all[i]) for i in ref_ids], axis=0))
            gw_ref = np.exp(0.5 * np.log(gw_ref / gw_ref[1]))  # a medio camino del neutro
        target = None
        ratios = {}
        if skin_ids:
            ratios = {i: np.array([stats[i]["skin"][0] / stats[i]["skin"][1], 1.0,
                                   stats[i]["skin"][2] / stats[i]["skin"][1]]) for i in skin_ids}
            target = np.exp(np.mean([np.log(r) for r in ratios.values()], axis=0))
            target = np.exp(0.8 * np.log(target) + 0.2 * np.log(_SKIN_REF_RATIO))
        for i in valid:
            g_gw = gw_ref / gw_all[i]
            if i in ratios:
                g_skin = target / ratios[i]
                g = np.exp(0.9 * np.log(g_skin) + 0.1 * np.log(g_gw))
            else:
                g = g_gw ** 0.6
            wb[i] = np.clip(g / g[1], 0.8, 1.25)
        # ---- exposicion: la piel de cada toma a la mediana de las pieles (90%) y la escena
        # hacia la mediana de las escenas (10%, acotado); sin cara, solo la escena
        face_y = {i: float(luma(lin_to_srgb(stats[i]["skin"] * wb[i]))) for i in skin_ids}
        exp_gain = [1.0] * n
        mids0 = {i: _percentiles(stats[i]["px"], wb[i])[1] for i in valid}
        ref_mid = float(np.median([mids0[i] for i in (skin_ids or list(mids0))])) if mids0 else 0.45
        ref_mid = float(np.clip(ref_mid, 0.36, 0.55))
        tgt = float(np.clip(np.median(list(face_y.values())), 0.35, 0.62)) if face_y else None
        for i in valid:
            s = stats[i]
            scene_e = srgb_to_lin(ref_mid) / max(1e-4, srgb_to_lin(mids0[i]))
            if i in face_y:
                # la escena solo matiza (paredes blancas o calle no deben oscurecer la cara)
                face_e = srgb_to_lin(tgt) / max(1e-4, srgb_to_lin(face_y[i]))
                e = math.exp(0.9 * math.log(face_e) + 0.1 * math.log(float(np.clip(scene_e, 0.8, 1.25))))
            else:
                e = scene_e ** 0.5
            e = float(np.clip(e, 2 ** -0.7, 2 ** 0.7))
            if e > 1.0:
                e = _burn_cap(s["px"], wb[i], e)
            exp_gain[i] = e
        # ---- niveles (y la cara compensada por ellos: el estirado de blancos no debe
        # deshacer la igualacion de exposicion)
        def levels(px, g):
            lo, mid, hi = _percentiles(px, g)
            black = max(0.0, lo - 0.012) * 0.7 if lo > 0.02 else 0.0
            white = 1.0
            if hi < 0.95:
                white = 1.0 - (1.0 - max(hi, 0.6) - 0.02) * 0.55
            return black, white, mid

        def face_pass(i, target):
            s = stats[i]
            skin_l = float(luma(s["skin"] * wb[i]))
            want = target * (white_of[i] - black_of[i]) + black_of[i]   # cara tras los niveles
            e2 = float(np.clip(float(srgb_to_lin(want)) / max(1e-4, skin_l), 2 ** -0.7, 2 ** 0.7))
            if e2 > exp_gain[i]:
                e2 = _burn_cap(s["px"], wb[i], e2)
            exp_gain[i] = e2
            black_of[i], white_of[i], mid_of[i] = levels(s["px"], wb[i] * e2)
            return (float(lin_to_srgb(skin_l * e2)) - black_of[i]) / max(0.3, white_of[i] - black_of[i])

        black_of, white_of, mid_of, fy = {}, {}, {}, {}
        for i in valid:
            black_of[i], white_of[i], mid_of[i] = levels(stats[i]["px"], wb[i] * exp_gain[i])
        for i in face_y:
            for _ in range(4):          # los niveles dependen de la exposicion: punto fijo
                fy[i] = face_pass(i, tgt)
                if abs(fy[i] - tgt) < 0.003:
                    break
        # si el tope anti-quemado no dejo llegar a alguna toma, las demas bajan a la mediana de
        # lo alcanzado (igualar importa mas que el brillo absoluto)
        if fy and min(fy.values()) < tgt - 0.004:
            tgt2 = float(np.median(list(fy.values())))
            for i in face_y:
                for _ in range(4):
                    if fy[i] <= tgt2 + 0.003:
                        break
                    fy[i] = face_pass(i, tgt2)
        for i in valid:
            g = wb[i] * exp_gain[i]
            corr[i] = {"gains": [float(v) for v in g], "black": float(black_of[i]),
                       "white": float(white_of[i]), "face": i in face_y, "mid": float(mid_of[i]),
                       "face_y": fy.get(i)}
        return corr

    # -------------------------------------------------- LUT
    def lut(self, cfg: dict, shot_idx: int | None) -> np.ndarray:
        look_name, L = _look(cfg)
        match = bool(cfg.get("match_shots", True))
        si = shot_idx if (match and isinstance(shot_idx, int) and 0 <= shot_idx < len(self.corr)) else -1
        key = (si, look_name) + tuple(round(num(cfg, k, d), 3) for k, d in
                                      (("strength", 1.0), ("exposure", 0.0), ("temperature", 0.0),
                                       ("tint", 0.0), ("contrast", 0.0)))
        lut = self._luts.get(key)
        if lut is None:
            corr = self.corr[si] if si >= 0 else None
            lut = build_lut(L, corr, cfg)
            if len(self._luts) > 64:
                self._luts.clear()
            self._luts[key] = lut
        return lut

    # -------------------------------------------------- aplicar
    def apply(self, img, cfg: dict | None, shot_idx: int | None, ctx) -> skia.Image:
        cfg = cfg if isinstance(cfg, dict) else {}
        if not cfg.get("enabled", True):
            return img
        arr = img if isinstance(img, np.ndarray) else C.image_to_rgba(img)
        out = grade_array(arr, cfg, self.lut(cfg, shot_idx), ctx, self._vig)
        return C.image_from_rgba(out)


def build_lut(L: dict, corr: dict | None, cfg: dict) -> np.ndarray:
    """LUT (256, 1, 4) uint8: correccion de toma + look. El canal alfa queda identidad."""
    cfg = cfg if isinstance(cfg, dict) else {}
    st = num(cfg, "strength", 1.0, 0.0, 1.5)
    x = np.arange(256, dtype=np.float64) / 255.0
    lin = srgb_to_lin(x)
    gains = np.array(corr["gains"], np.float64) if corr else np.ones(3)
    temp = L.get("temp", 0.0) * st + num(cfg, "temperature", 0.0, -1.0, 1.0) * 0.6
    tint = L.get("tint", 0.0) * st + num(cfg, "tint", 0.0, -1.0, 1.0) * 0.5
    tg = np.array([1.0 + 0.09 * temp, 1.0 - 0.04 * tint, 1.0 - 0.11 * temp])
    tg /= luma(tg)  # sin cambio de brillo
    expo = 2.0 ** (L.get("exposure", 0.0) * st + num(cfg, "exposure", 0.0, -2.0, 2.0))
    contrast = L.get("contrast", 0.0) * st + num(cfg, "contrast", 0.0, -0.5, 0.5)
    contrast = float(np.clip(contrast, -0.6, 0.95))
    mono = bool(L.get("mono"))
    if mono:
        contrast = 0.0  # la curva de B/N la pone bw_array
    gamma = 1.0 + (L.get("gamma", 1.0) - 1.0) * st
    crush = L.get("black", 0.0) * st
    fade = L.get("fade", 0.0) * st
    sh = np.array(L.get("shadows", (0, 0, 0))) * st
    hi = np.array(L.get("highs", (0, 0, 0))) * st
    black = float(corr.get("black", 0.0)) if corr else 0.0
    white = float(corr.get("white", 1.0)) if corr else 1.0
    out = np.empty((256, 1, 4), np.uint8)
    out[:, 0, 3] = np.arange(256)
    for c in range(3):
        g = gains[c] * (1.0 if mono else tg[c]) * expo
        v = lin * g
        m = g  # valor maximo posible
        knee = 0.82
        if m > 1.0:
            over = v > knee
            v = np.where(over, knee + (1 - knee) * np.tanh((v - knee) / (1 - knee))
                         / math.tanh((m - knee) / (1 - knee)), v)
        y = lin_to_srgb(v)
        y = np.clip((y - black) / max(0.3, white - black), 0.0, 1.0)
        y = crush_blacks(y, crush)
        if abs(gamma - 1.0) > 1e-4:
            y = np.power(y, gamma)
        y = s_curve(y, contrast, L.get("pivot", 0.45))
        if fade > 0:
            y = fade + y * (1.0 - fade * 0.6)
        if not mono:
            ws = 1.0 - _smoothstep(0.0, 0.55, y)
            wh = _smoothstep(0.45, 1.0, y)
            y = y + sh[c] * ws + hi[c] * wh
        y = np.maximum.accumulate(np.clip(y, 0.0, 1.0))  # monotona siempre
        out[:, 0, c] = np.clip(np.round(y * 255.0), 0, 255).astype(np.uint8)
    return out


# ---------------------------------------------------------------- saturacion (vibrance)
_W601 = np.array([0.299, 0.587, 0.114])
_SKIN_SRGB = np.array([224.0, 170.0, 142.0]) / 255.0


@lru_cache(maxsize=64)
def sat_matrix(sat: float, skin_sat: float) -> np.ndarray | None:
    """Matriz 4x4 (cv2.transform, RGBA) que escala el croma de forma anisotropica: `skin_sat`
    sobre el eje del tono de piel (naranja) y `sat` en la direccion perpendicular (verdes,
    cian, magentas, azules). Conserva la luma (Rec.601) y el alfa. None si es identidad."""
    if abs(sat - 1.0) < 0.005 and abs(skin_sat - 1.0) < 0.005:
        return None
    one = np.ones(3)
    c = _SKIN_SRGB - float(_W601 @ _SKIN_SRGB) * one      # croma de la piel (w . c = 0)
    u1 = c / np.linalg.norm(c)
    u2 = np.cross(u1, _W601)
    u2 -= (_W601 @ u2) * one                                # dentro del plano de croma
    u2 /= np.linalg.norm(u2)
    B = np.stack([one, u1, u2], axis=1)                     # rgb = B @ (Y, a, b)
    Bi = np.linalg.inv(B)
    # la primera fila de Bi debe ser la luma: se fuerza (B esta construida para eso)
    T = B @ np.diag([1.0, float(skin_sat), float(sat)]) @ Bi
    M = np.eye(4, dtype=np.float32)
    M[:3, :3] = T.astype(np.float32)
    return M


# ---------------------------------------------------------------- vinieta, grano, detalle
def vignette_map(W: int, H: int, amount: float) -> np.ndarray:
    """Mapa multiplicativo RGBA uint8 (alfa 255): eliptico, centro un poco arriba."""
    m8 = _vignette_gray(W, H, amount, 0.5, 0.46)
    out = np.empty((H, W, 4), np.uint8)
    out[..., 0] = out[..., 1] = out[..., 2] = m8
    out[..., 3] = 255
    return out


def _vignette_gray(W: int, H: int, amount: float, cx: float, cy: float, inner: float = 0.45,
                   outer: float = 1.45) -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    u = (xx / W - cx) * 2.0
    v = (yy / H - cy) * 2.0 / 1.04
    d = np.sqrt(u * u * 0.85 + v * v)
    t = np.clip((d - inner) / (outer - inner), 0, 1)
    t = t * t * (3 - 2 * t)
    m = 1.0 - np.clip(amount, 0, 1) * 1.6 * t ** 1.15
    return np.clip(m * 255.0 + 0.5, 0, 255).astype(np.uint8)


GRAIN_PAD = 64
GRAIN_N = 4


@lru_cache(maxsize=4)
def grain_textures(W: int, H: int, n: int = GRAIN_N) -> tuple[np.ndarray, ...]:
    """Texturas de grano (gris uint8 centrado en 128, desv ~32) precalculadas y deterministas,
    con GRAIN_PAD px de margen para recortarlas con un desplazamiento distinto en cada cuadro.
    Grano fino (~1.3 px a 1080p): ruido gaussiano filtrado."""
    rng = np.random.default_rng(90210)
    sigma = 0.55 * max(0.5, W / 1080.0)
    out = []
    for _ in range(n):
        g = rng.standard_normal((H + GRAIN_PAD, W + GRAIN_PAD)).astype(np.float32)
        g = cv2.GaussianBlur(g, (0, 0), sigma)
        g /= max(1e-6, float(g.std()))
        out.append(np.clip(128.0 + g * 32.0, 0, 255).astype(np.uint8))
    return tuple(out)


def grain_frame(W: int, H: int, n: int, seed: int = 0) -> np.ndarray:
    """Grano del cuadro n (gris uint8 HxW, vista): textura n % GRAIN_N recortada con un
    desplazamiento pseudoaleatorio por cuadro. Dos cuadros seguidos nunca repiten textura y la
    secuencia no tiene periodo visible (no se congela ni late)."""
    tex = grain_textures(int(W), int(H))
    h = zlib.crc32(f"grain:{int(seed)}:{int(n)}".encode()) & 0xFFFFFFFF
    dx, dy = h % GRAIN_PAD, (h >> 8) % GRAIN_PAD
    t = tex[int(n) % len(tex)]
    return t[dy:dy + H, dx:dx + W]


def grain_rgba(W: int, H: int, n: int, seed: int = 0) -> np.ndarray:
    return cv2.cvtColor(np.ascontiguousarray(grain_frame(W, H, n, seed)), cv2.COLOR_GRAY2RGBA)


def add_grain(arr: np.ndarray, amount: float, n: int, seed: int = 0) -> np.ndarray:
    """Suma grano (amount como en cfg.grade.grain) a un RGB/RGBA uint8. Devuelve arreglo nuevo
    (el alfa no cambia: la textura RGBA lleva alfa 128 y se compensa con el offset)."""
    if amount <= 0:
        return arr
    H, W = arr.shape[:2]
    k = amount * 255.0 * 0.62 / 32.0
    g = np.ascontiguousarray(grain_frame(W, H, n, seed))
    if arr.shape[2] == 3:
        tex = cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)
    else:
        tex = cv2.cvtColor(g, cv2.COLOR_GRAY2RGBA)
        tex[..., 3] = 128
    return cv2.addWeighted(arr, 1.0, tex, k, -128.0 * k)


def detail(arr: np.ndarray, sharpen: float, clarity: float, W: int, ctx) -> np.ndarray:
    """Nitidez fina (unsharp 3x3) + claridad (contraste local de radio grande calculado a 1/4
    de resolucion). Dos pasadas baratas en un hilo."""
    out = arr
    if sharpen > 0.005:
        a = sharpen * 2.4
        b3 = cv2.GaussianBlur(out, (3, 3), 0)
        out = cv2.addWeighted(out, 1.0 + a, b3, -a, 0)
    if clarity > 0.005:
        H = arr.shape[0]
        ds = 4
        small = cv2.resize(arr, (max(8, W // ds), max(8, H // ds)), interpolation=cv2.INTER_AREA)
        sg = (ctx.px(26.0) if ctx is not None else 26.0 * W / 1080.0) / ds
        k = max(3, int(round(sg * 4)) | 1)          # stackBlur ~ gaussiana de sigma sg
        small = cv2.stackBlur(small, (k, k))
        low = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR)
        c = clarity * 0.9
        out = cv2.addWeighted(out, 1.0 + c, low, -c, 0)
    return out


def detail_luma(arr: np.ndarray, sharpen: float, clarity: float, grain: float, W: int, ctx,
                n: int = 0, seed: int = 0) -> np.ndarray | None:
    """Capa de detalle de LUMA en UN canal (uint8 centrada en 128): nitidez 3x3 + claridad
    (contraste local de radio grande a 1/4 de resolucion) + grano, las tres sumadas:

        D = A*(Y - blur3(Y)) + c*(Y - low(Y)) + kg*(grano - 128) + 128

    con A = 2.4*sharpen*(1 + c) y c = 0.9*clarity: la misma formula que la cadena RGB de
    detail() + add_grain() (nitidez escalada por la claridad), pero sobre la luma. Cuesta ~1/3
    (todo en 1 canal) y es lo que se ve: el encoder guarda el color a media resolucion (4:2:0)
    y la nitidez de croma solo agrega bordes de color. El grano ya era monocromo. None si no hay
    detalle que sumar."""
    a = sharpen * 2.4 if sharpen > 0.005 else 0.0
    c = clarity * 0.9 if clarity > 0.005 else 0.0
    A = a * (1.0 + c)
    if A <= 0 and c <= 0 and grain <= 0:
        return None
    H = arr.shape[0]
    code = cv2.COLOR_RGBA2GRAY if arr.shape[2] == 4 else cv2.COLOR_RGB2GRAY
    Y = cv2.cvtColor(arr, code)
    s = A + c
    if s > 0:
        parts = []
        if A > 0:
            # 3x3 (sigma ~0.71 px) en 1080; arriba se escala para que el realce tenga el mismo
            # ancho en px de referencia (en 4K el 3x3 fijo afinaba la mitad de lo esperado)
            ks = ctx.px(1.0) if ctx is not None else W / 1080.0
            b3 = cv2.GaussianBlur(Y, (3, 3), 0) if ks <= 1.0 + 1e-3 else \
                cv2.GaussianBlur(Y, (0, 0), 0.7071 * ks)
            parts.append((b3, A))
        if c > 0:
            ds = 4
            small = cv2.resize(Y, (max(8, W // ds), max(8, H // ds)), interpolation=cv2.INTER_AREA)
            sg = (ctx.px(26.0) if ctx is not None else 26.0 * W / 1080.0) / ds
            k = max(3, int(round(sg * 4)) | 1)          # stackBlur ~ gaussiana de sigma sg
            small = cv2.stackBlur(small, (k, k))
            parts.append((cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR), c))
        bc = parts[0][0] if len(parts) == 1 else \
            cv2.addWeighted(parts[0][0], parts[0][1] / s, parts[1][0], parts[1][1] / s, 0.0)
        D = cv2.addWeighted(Y, s, bc, -s, 128.0)
    else:
        D = np.full_like(Y, 128)
    if grain > 0:
        gp, gn = grain_signed(W, H, n, seed, grain * 255.0 * 0.62 / 32.0)
        cv2.add(D, gp, dst=D)
        cv2.subtract(D, gn, dst=D)
    return D


_GSIGNED: dict = {}


def grain_signed(W: int, H: int, n: int, seed: int, kg: float) -> tuple[np.ndarray, np.ndarray]:
    """Grano ya escalado del cuadro n como dos planos uint8 (parte positiva y negativa de
    kg*(textura - 128)), recortes de texturas precalculadas: sumarlo cuesta dos pasadas enteras
    de 1 canal. Mismo grano que grain_frame (misma textura y desplazamiento por cuadro)."""
    kgr = round(float(kg), 4)
    key = (int(W), int(H), kgr)
    pair = _GSIGNED.get(key)
    if pair is None:
        if len(_GSIGNED) > 3:
            _GSIGNED.clear()
        pos, neg = [], []
        for t in grain_textures(int(W), int(H)):
            v = (t.astype(np.float32) - 128.0) * kgr
            pos.append(np.clip(np.round(v), 0, 255).astype(np.uint8))
            neg.append(np.clip(np.round(-v), 0, 255).astype(np.uint8))
        pair = _GSIGNED[key] = (tuple(pos), tuple(neg))
    h = zlib.crc32(f"grain:{int(seed)}:{int(n)}".encode()) & 0xFFFFFFFF
    dx, dy = h % GRAIN_PAD, (h >> 8) % GRAIN_PAD
    i = int(n) % len(pair[0])
    return pair[0][i][dy:dy + H, dx:dx + W], pair[1][i][dy:dy + H, dx:dx + W]


def grade_array(arr: np.ndarray, cfg: dict, lut: np.ndarray, ctx, vig_cache: dict | None = None) -> np.ndarray:
    """Pipeline por cuadro sobre RGBA uint8 (alfa 255): detalle de luma (nitidez + claridad +
    grano, ver detail_luma) -> LUT -> saturacion (vibrance) -> vinieta. El grano entra ANTES de
    la curva, asi la curva lo atenua en negros y altas luces (como el grano de pelicula) y queda
    en los medios tonos. Internamente trabaja en RGB de 3 canales; el detalle en 1 canal.
    ~8 ms por cuadro 1080x1920 con cv2 a 1 hilo (antes ~12.6 ms)."""
    H, W = arr.shape[:2]
    draft = bool(getattr(ctx, "draft", False))
    _, look = _look(cfg)
    st = num(cfg, "strength", 1.0, 0.0, 1.5)
    sharpen = 0.0 if draft else num(cfg, "sharpen", 0.0, 0.0, 1.0)
    clarity = num(cfg, "clarity", look.get("clarity", 0.0) * st, 0.0, 1.0)
    grain = num(cfg, "grain", 0.0, 0.0, 0.3)
    D = detail_luma(arr, sharpen, clarity, grain, W, ctx, int(getattr(ctx, "n", 0) or 0),
                    int(getattr(ctx, "seed", 0) or 0))
    # por PLANOS: la LUT de una tabla por plano es ~40% mas barata que la LUT de 3 tablas sobre
    # RGB entrelazado, y el detalle (con signo) se suma con dos pasadas enteras saturadas
    planes = cv2.split(arr)[:3]
    if D is not None:
        c128 = _const(D.shape, 128)
        dp, dn = cv2.subtract(D, c128), cv2.subtract(c128, D)
    luts = _lut_planes(lut)
    outp = []
    for c in range(3):
        x = planes[c]
        if D is not None:
            x = cv2.add(x, dp)
            cv2.subtract(x, dn, dst=x)
        outp.append(cv2.LUT(x, luts[c]))
    out = cv2.merge(outp)
    vig = num(cfg, "vignette", 0.0, 0.0, 1.0) + look.get("vig", 0.0) * st
    if look.get("mono"):
        c = 1.0 + (look.get("contrast", 0.5) * st + num(cfg, "contrast", 0.0, -0.5, 0.5)) * 0.9
        return bw_array(out, c, "neutral", _face(ctx), min(1.0, vig * 1.6 + 0.2), 0.05, ctx)
    sat = look.get("sat", 1.0) ** st * num(cfg, "saturation", 1.0, 0.0, 2.0)
    skin = look.get("skin_sat", 1.0) ** st * num(cfg, "saturation", 1.0, 0.0, 2.0)
    M = sat_matrix(round(sat, 3), round(skin, 3))
    if M is not None:
        out = cv2.transform(out, M[:3, :3])
    vig = min(1.0, vig)
    if vig > 0.002:
        key = (W, H, round(vig, 3))
        cache = vig_cache if vig_cache is not None else _VIG
        vm = cache.get(key)
        if vm is None:
            if len(cache) > 4:
                cache.clear()
            vm = cache[key] = cv2.cvtColor(_vignette_gray(W, H, vig, 0.5, 0.46), cv2.COLOR_GRAY2RGB)
        out = cv2.multiply(out, vm, scale=1.0 / 255.0)
    return cv2.cvtColor(out, cv2.COLOR_RGB2RGBA)


_LUT3: dict = {}
_LUTP: dict = {}
_CONST: dict = {}


def _const(shape: tuple, v: int) -> np.ndarray:
    """Plano uint8 constante (cacheado, solo lectura)."""
    k = (tuple(shape), int(v))
    a = _CONST.get(k)
    if a is None:
        if len(_CONST) > 8:
            _CONST.clear()
        a = np.full(shape, int(v), np.uint8)
        a.setflags(write=False)
        _CONST[k] = a
    return a


def _lut_planes(lut: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """LUT RGBA (256,1,4) -> 3 tablas contiguas (256,1) de un canal (cacheadas por identidad)."""
    k = id(lut)
    v = _LUTP.get(k)
    if v is None or v[0] is not lut:
        if len(_LUTP) > 64:
            _LUTP.clear()
        v = _LUTP[k] = (lut, tuple(np.ascontiguousarray(lut[:, 0, c]).reshape(256, 1)
                                   for c in range(3)))
    return v[1]


def _lut3(lut: np.ndarray) -> np.ndarray:
    """LUT RGBA (256,1,4) -> RGB (256,1,3) contigua (cacheada por identidad)."""
    if lut.shape[-1] == 3:
        return lut
    k = id(lut)
    v = _LUT3.get(k)
    if v is None or v[0] is not lut:
        if len(_LUT3) > 64:
            _LUT3.clear()
        v = _LUT3[k] = (lut, np.ascontiguousarray(lut[..., :3]))
    return v[1]


_VIG: dict = {}


def _face(ctx):
    f = getattr(ctx, "face", None) if ctx is not None else None
    try:
        if f is not None and len(f) >= 4 and all(math.isfinite(float(v)) for v in f[:4]):
            return tuple(float(v) for v in f[:4])
    except (TypeError, ValueError):
        pass
    return None


# ---------------------------------------------------------------- blanco y negro
# Mezcla de canales para B/N con piel luminosa (filtro rojizo suave).
BW_MIX = (0.40, 0.49, 0.11)
_BW_M = np.array([[BW_MIX[0], BW_MIX[1], BW_MIX[2], 0.0]], np.float32)
_BW_TOP = 0.88     # hombro: los blancos topan aqui (sin fotocopia); el brillo suma ~0.04
_BW_FACE = 0.64    # luma de la cara tras la curva


@lru_cache(maxsize=32)
def _bw_shape(contrast: float, lift: float) -> np.ndarray:
    """Curva filmica de B/N sobre [0,1] (1024 muestras): pie levantado (lift), hombro que
    topa en _BW_TOP y curva S con pivote en medios bajos. `contrast` (0.5..2) cambia la
    pendiente de forma perceptible."""
    x = np.linspace(0.0, 1.0, 1024)
    c = float(np.clip(contrast, 0.5, 2.0))
    k = 2.5 + 6.5 * (c - 0.5)                                         # 2.5 .. 12.25
    pivot = 0.42
    sig = lambda v: 1.0 / (1.0 + np.exp(-k * (v - pivot)))  # noqa: E731
    s = (sig(x) - sig(0.0)) / (sig(1.0) - sig(0.0))
    mixw = float(np.clip(0.35 + 0.45 * (c - 0.5), 0.2, 0.95))
    y = (1 - mixw) * x + mixw * s
    # mas contraste tambien abre el rango: negros mas bajos y blancos un poco mas altos
    lo = lift * (1.35 - 0.55 * (c - 0.5))
    hi = _BW_TOP + 0.03 * (c - 1.2)
    y = lo + (hi - lo) * y
    return np.maximum.accumulate(y)


@lru_cache(maxsize=256)
def _bw_lut(contrast: float, lift: float, gain: float) -> np.ndarray:
    """LUT uint8 gris: ganancia de exposicion (lineal) + curva filmica."""
    x = np.arange(256, dtype=np.float64) / 255.0
    xl = srgb_to_lin(x) * gain
    xe = lin_to_srgb(np.minimum(xl, 1.0) if gain <= 1.0 else
                     np.where(xl > 0.8, 0.8 + 0.2 * np.tanh((xl - 0.8) / 0.2), xl))
    shape = _bw_shape(contrast, lift)
    y = np.interp(xe, np.linspace(0, 1, len(shape)), shape)
    return np.clip(np.round(y * 255.0), 0, 255).astype(np.uint8)


def _bw_face_gain(gray: np.ndarray, face, contrast: float, lift: float) -> float:
    """Ganancia (lineal) para que la cara quede en ~_BW_FACE tras la curva (o la escena en
    medios si no hay cara). Parcial y acotada: no persigue cada cuadro."""
    H, W = gray.shape[:2]
    if face is not None:
        cx, cy, fw, fh = face
        x0, x1 = int((cx - 0.28 * fw) * W), int((cx + 0.28 * fw) * W)
        y0, y1 = int((cy - 0.10 * fh) * H), int((cy + 0.32 * fh) * H)
        x0, x1 = max(0, x0), min(W, x1)
        y0, y1 = max(0, y0), min(H, y1)
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            roi = gray[y0:y1:2, x0:x1:2]
            val = float(np.percentile(roi, 60)) / 255.0
            target, strength = _BW_FACE, 0.85
        else:
            face = None
    if face is None:
        val = float(np.median(gray[::8, ::8])) / 255.0
        target, strength = 0.46, 0.5
    shape = _bw_shape(contrast, lift)
    # entrada que produce `target` con la curva
    xt = float(np.interp(target, shape, np.linspace(0, 1, len(shape))))
    g = srgb_to_lin(xt) / max(1e-3, srgb_to_lin(max(0.02, val)))
    g = float(np.clip(g, 0.55, 1.8)) ** strength
    return round(g, 2)


@lru_cache(maxsize=8)
def _bw_vig_big(W: int, H: int, amount: float) -> np.ndarray:
    """Vinieta radial gris uint8 de 2W x 2H centrada en (W, H): se recorta alrededor de la
    cara en cada cuadro (sigue al sujeto sin recalcular). Fuerte y amplia: lleva la vista a la
    cara como la referencia."""
    q = 4  # se calcula a 1/4 y se amplia (es muy suave): primer uso barato
    hq, wq = max(2, 2 * H // q), max(2, 2 * W // q)
    yy, xx = np.mgrid[0:hq, 0:wq].astype(np.float32)
    u = (xx * (2 * W / wq) - W) / (0.62 * W)
    v = (yy * (2 * H / hq) - H) / (0.50 * H)
    d = np.sqrt(u * u + v * v)
    t = np.clip((d - 0.28) / 1.0, 0, 1)
    t = t * t * (3 - 2 * t)
    m = 1.0 - float(np.clip(amount, 0, 1)) * t
    m8 = np.clip(m * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return cv2.resize(m8, (2 * W, 2 * H), interpolation=cv2.INTER_LINEAR)


@lru_cache(maxsize=4)
def _bw_glow_lut() -> np.ndarray:
    """Brillo de altas luces (halacion suave): 0 hasta 0.55 y sube suave hasta ~0.07."""
    x = np.arange(256, dtype=np.float64) / 255.0
    y = np.clip((x - 0.55) / 0.45, 0, 1) ** 2 * 0.07
    return np.clip(np.round(y * 255.0), 0, 255).astype(np.uint8)


def bw_array(arr: np.ndarray, contrast: float = 1.2, tone: str = "neutral", face=None,
             vignette: float = 0.5, lift: float = 0.06, ctx=None) -> np.ndarray:
    """B/N filmico sobre RGB/RGBA uint8: mezcla de canales con piel luminosa (256 niveles),
    cara normalizada a ~0.64, curva con pie levantado y hombro suave, suavizado del micro
    contraste + halacion de altas luces (look de pelicula, no de fotocopia) y vinieta fuerte
    centrada en la cara. Devuelve RGBA."""
    H, W = arr.shape[:2]
    gray = cv2.transform(arr, np.ascontiguousarray(_BW_M[:, :arr.shape[2]]))
    if gray.ndim == 3:
        gray = gray[..., 0]
    c = round(float(np.clip(contrast, 0.5, 2.0)), 2)
    lf = round(float(np.clip(lift, 0.0, 0.2)), 3)
    gain = _bw_face_gain(gray, face, c, lf)
    g = cv2.LUT(gray, _bw_lut(c, lf, gain))
    # pelicula: 15% de una version difusa (baja el micro contraste duro) + brillo de altas luces
    small = cv2.resize(g, (max(8, W // 4), max(8, H // 4)), interpolation=cv2.INTER_AREA)
    k = max(3, int(round(W / 1080.0 * 14)) | 1)
    up = cv2.resize(cv2.stackBlur(small, (k, k)), (W, H), interpolation=cv2.INTER_LINEAR)
    g = cv2.addWeighted(g, 0.85, up, 0.15, 0)
    g = cv2.add(g, cv2.LUT(up, _bw_glow_lut()))
    vig = round(float(np.clip(vignette, 0.0, 1.0)), 2)
    if vig > 0.005:
        big = _bw_vig_big(W, H, vig)
        fx, fy = (face[0], min(0.8, face[1] + 0.12)) if face is not None else (0.5, 0.45)
        ox = int(round(W - fx * W))
        oy = int(round(H - fy * H))
        ox, oy = min(W, max(0, ox)), min(H, max(0, oy))
        g = cv2.multiply(g, big[oy:oy + H, ox:ox + W], scale=1.0 / 255.0)
    out = cv2.cvtColor(g, cv2.COLOR_GRAY2RGBA)
    tint = _bw_tone(tone)
    if tint is not None:
        out = cv2.LUT(out, tint)
    return out


@lru_cache(maxsize=8)
def _bw_tone(tone: str) -> np.ndarray | None:
    tones = {"warm": (1.035, 1.0, 0.93), "cool": (0.95, 0.99, 1.05), "sepia": (1.08, 0.98, 0.82)}
    if tone not in tones:
        return None
    x = np.arange(256, dtype=np.float64)
    tint = np.empty((256, 1, 4), np.uint8)
    w = 1.0 - np.abs(x / 255.0 - 0.5) * 2.0   # virado en medios tonos
    for c in range(3):
        tint[:, 0, c] = np.clip(np.round(x * (1.0 + (tones[tone][c] - 1.0) * w)), 0, 255)
    tint[:, 0, 3] = np.arange(256)
    return tint


def _mono(arr: np.ndarray, contrast: float = 1.2, tone: str = "neutral") -> np.ndarray:
    """Compatibilidad: B/N sin cara ni vinieta."""
    return bw_array(arr, contrast, tone, None, 0.0, 0.05)


def composite_masked(bottom, top, mask: np.ndarray) -> skia.Image:
    """top sobre bottom a traves de la mascara float HxW (0..1). skia.Image o RGBA uint8."""
    b = bottom if isinstance(bottom, np.ndarray) else C.image_to_rgba(bottom)
    t = top if isinstance(top, np.ndarray) else C.image_to_rgba(top)
    a = np.ascontiguousarray(mask, dtype=np.float32)
    return C.image_from_rgba(cv2.blendLinear(t, b, a, 1.0 - a))


def bw(img, item: dict, lt: float, dur: float, ctx) -> skia.Image:
    """Blanco y negro dramatico (filmico). Campos: contrast (0.5..2, 1.2), subject_color (bool:
    la persona queda a color via ctx.matte()), fade (s de entrada/salida, 0.06), tone
    (neutral|warm|cool|sepia), vignette (0..1, 0.55; centrada en la cara), lift (negros, 0.06).
    """
    item = item if isinstance(item, dict) else {}
    arr = img if isinstance(img, np.ndarray) else C.image_to_rgba(img)
    k = num(item, "intensity", 1.0, 0.0, 1.0)
    contrast = num(item, "contrast", 1.2, 0.5, 2.0)
    tone = item.get("tone", "neutral")
    tone = tone if isinstance(tone, str) else "neutral"
    out = bw_array(arr, contrast, tone, _face(ctx), num(item, "vignette", 0.55, 0.0, 1.0),
                   num(item, "lift", 0.06, 0.0, 0.2), ctx)
    fade = num(item, "fade", 0.06, 0.0, 2.0)
    a = k
    if fade > 0 and dur > 0:
        a *= ease.smoothstep(min(1.0, max(0.0, lt) / fade, max(0.0, dur - lt) / fade))
    if a < 0.999:
        out = cv2.addWeighted(arr, 1.0 - a, out, a, 0)
    if item.get("subject_color") is True or item.get("subject_color") in ("true", 1):
        m = ctx.matte() if ctx is not None else None
        if m is not None and float(m.max()) > 0.02:
            return composite_masked(out, arr, m)
    return C.image_from_rgba(out)


RENDERERS = {"bw": bw}
