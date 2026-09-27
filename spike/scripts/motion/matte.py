"""Mascara del sujeto (MODNet + compuerta semantica de persona) y efectos que la usan.

Contrato (docs/MOTION-ENGINE.md, 4.2b):
  ensure_model() -> Path            baja MODNet ONNX (Apache-2.0, Xenova/modnet) a DATA/models
  MatteProvider(base_video, W, H, fps, frame_ranges, cache_dir, quality)
      .prepare(on_pct=None)         calcula y cachea las mascaras de los rangos pedidos
      .get(n) -> np.float32 HxW     mascara del cuadro n (espacio del video base, sin camara)
  RENDERERS = {color_pop, bg_blur, bg_replace, subject_outline}   apply(img, item, lt, dur, ctx)

Modelos (descargados en el setup; revision fija + SHA-256):
  - MODNet (Apache-2.0): matting de retrato, bordes finos de cabello. Por si solo mete en el
    sujeto lo que toca a la persona (sillon, escritorio, respaldo).
  - PP-HumanSeg 2023mar de OpenCV Zoo (Apache-2.0, 6 MB, ~6 ms): segmentacion SEMANTICA de
    persona. Se usa como compuerta: m = modnet * dilatar(persona). Quita los muebles. Si no
    esta disponible, la compuerta es un prior geometrico del cuerpo desde la cara.

Pipeline de prepare(): INCREMENTAL (solo se infieren y escriben los huecos sin mascara de los
rangos pedidos, con unos cuadros de contexto), hueco por hueco (memoria acotada; lectura con
busqueda por tiempo). Una sola sesion ONNX por proceso y modelo (compartida entre hilos, sin
spinning) y executors persistentes (los hilos y las sesiones viven entre rangos y llamadas):
  A) caras (YuNet, 360 px) en los cuadros clave (cada `step` cuadros) de TODO el rango pedido,
     cacheadas por cuadro en faces.json (un hueco nuevo solo detecta sus claves).
  B) gating y recorte por RANGO PEDIDO (no por hueco): si al menos ~25% de las claves del rango
     tienen cara, se infieren TODAS (la cara de cada clave es la detectada mas cercana en el
     tiempo, aunque este fuera del hueco: perfiles y cabeza agachada no apagan la mascara); si
     no (fachada, b-roll), el rango queda vacio. El recorte y las dims de inferencia salen de
     las caras de todo el rango: calcular por huecos da lo mismo que calcular de una vez.
  C) MODNet sobre un recorte fijo alrededor del cuerpo + compuerta de persona + componentes
     que tocan el cuerpo; suavizado temporal simetrico sensible a bordes; histeresis de area
     (la mascara no se desploma de un cuadro clave al siguiente: se funde en ~0.25 s).
  D) cuadros intermedios interpolados y upsample guiado (fast guided filter con el cuadro a
     resolucion completa como guia) para que el borde siga el cabello real.
  E) cache en disco: PNG gris por cuadro (escritura atomica) + meta.json con la calidad por
     rango (quality_of(a, b): 1 = limpio; bajo = la persona tocaba muebles grandes).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import skia

from . import ctx as C
from . import ease

# ---------------------------------------------------------------- modelos
MODEL_REV = "fa2fa546052fba4c08921230a26cc69a333fca12"
MODEL_URL = f"https://huggingface.co/Xenova/modnet/resolve/{MODEL_REV}/onnx/model.onnx"
MODEL_SIZE = 25_888_640          # bytes del ONNX fp32 publicado
MODEL_SHA256 = "07c308cf0fc7e6e8b2065a12ed7fc07e1de8febb7dc7839d7b7f15dd66584df9"
MODEL_NAME = "modnet.onnx"
PERSON_REV = "e876e63603f6c65c1a22576bfd5fc7c07ecf9eb9"
MODELS = {
    "modnet": {"name": MODEL_NAME, "sub": "", "url": MODEL_URL, "size": MODEL_SIZE,
               "sha256": MODEL_SHA256, "env": "REELFY_MODNET_URL", "license": "Apache-2.0"},
    "person": {"name": "pphumanseg_2023mar.onnx", "sub": "matte",
               "url": ("https://huggingface.co/opencv/human_segmentation_pphumanseg/resolve/"
                       f"{PERSON_REV}/human_segmentation_pphumanseg_2023mar.onnx"),
               "size": 6_163_938,
               "sha256": "552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24",
               "env": "REELFY_PERSONSEG_URL", "license": "Apache-2.0"},
}
_ALGO = 7                        # version del pipeline (invalida la cache)
# 7: gating/recorte por rango pedido en el incremental (el 6 podia dejar huecos en cero)


def _log(msg: str) -> None:
    print(f"[matte] {msg}", file=sys.stderr, flush=True)


def model_path(which: str = "modnet") -> Path:
    """Ruta donde vive (o vivira) el modelo: DATA/models/modnet.onnx (y la compuerta de
    persona en DATA/models/matte/, fuera del control de versiones)."""
    import paths
    spec = MODELS[which]
    base = Path(paths.DATA) / "models"
    return (base / spec["sub"] / spec["name"]) if spec["sub"] else base / spec["name"]


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verified(p: Path, spec: dict) -> bool:
    """Tamano exacto + SHA-256. El resultado se recuerda en un archivo .ok junto al modelo
    (atado a tamano y mtime): la verificacion completa corre una sola vez."""
    try:
        st = p.stat()
    except OSError:
        return False
    if st.st_size != spec["size"]:
        return False
    ok = p.with_name(p.name + ".ok")
    sig = f"{spec['sha256']}:{st.st_size}:{int(st.st_mtime)}"
    try:
        if ok.read_text().strip() == sig:
            return True
    except OSError:
        pass
    if _sha256(p) != spec["sha256"]:
        return False
    try:
        ok.write_text(sig)
    except OSError:
        pass
    return True


def model_ready(p: Path | None = None, which: str = "modnet") -> bool:
    return _verified(p or model_path(which), MODELS[which])


def ensure_model(on_pct=None, retries: int = 4, timeout: float = 60.0, url: str | None = None,
                 which: str = "modnet") -> Path:
    """Baja el modelo si falta o no verifica. Revision fija, reintentos con espera creciente,
    descarga reanudable a .part y verificacion de tamano + SHA-256 antes de renombrar.
    Un .part completo y valido se renombra sin volver a bajar. Devuelve la ruta."""
    spec = MODELS[which]
    dst = model_path(which)
    if _verified(dst, spec):
        return dst
    import paths
    for alt in (Path(paths.SPIKE) / "models" / spec["name"],
                Path(paths.SPIKE) / "models" / spec["sub"] / spec["name"]):
        if alt != dst and _verified(alt, spec):
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".tmp")
            shutil.copyfile(alt, tmp)
            tmp.replace(dst)
            return dst
    import requests
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    url = url or os.environ.get(spec["env"]) or spec["url"]
    size = spec["size"]
    last = None
    for attempt in range(retries):
        try:
            done = part.stat().st_size if part.exists() else 0
            if done == size:
                if _sha256(part) == spec["sha256"]:
                    part.replace(dst)
                    _verified(dst, spec)
                    if on_pct:
                        on_pct(100.0)
                    return dst
                part.unlink()
                done = 0
            elif done > size:
                part.unlink()
                done = 0
            headers = {"Range": f"bytes={done}-"} if done > 0 else {}
            with requests.get(url, stream=True, timeout=timeout, headers=headers,
                              allow_redirects=True) as r:
                if r.status_code != 416:  # 416: ya completo
                    r.raise_for_status()
                    mode = "ab" if (r.status_code == 206 and done > 0) else "wb"
                    if mode == "wb":
                        done = 0
                    with open(part, mode) as f:
                        for chunk in r.iter_content(1 << 20):
                            if not chunk:
                                continue
                            f.write(chunk)
                            done += len(chunk)
                            if on_pct:
                                on_pct(min(99.0, 100.0 * done / size))
            got = part.stat().st_size if part.exists() else 0
            if got == size and _sha256(part) == spec["sha256"]:
                part.replace(dst)
                _verified(dst, spec)
                if on_pct:
                    on_pct(100.0)
                return dst
            last = RuntimeError(f"descarga invalida ({got} bytes)")
            if got >= size:
                part.unlink()        # completo pero corrupto: se baja de nuevo
        except Exception as e:  # red, timeout, HTTP
            last = e
        time.sleep(min(20.0, 1.5 * (2 ** attempt)))
    raise RuntimeError(f"no se pudo descargar {which}: {last}")


def ensure_models(on_pct=None) -> dict:
    """Setup: baja MODNet (obligatorio para los efectos de sujeto) y la compuerta de persona
    (opcional: sin ella se usa un prior geometrico). Devuelve {nombre: ruta o error}."""
    out = {}
    names = list(MODELS)
    for i, w in enumerate(names):
        cb = (lambda p, i=i: on_pct((i + p / 100.0) / len(names) * 100.0)) if on_pct else None
        try:
            out[w] = str(ensure_model(cb, which=w))
        except Exception as e:
            out[w] = f"error: {e}"
    return out


_SESS_LOCAL = threading.local()  # solo el detector de caras (cv2.FaceDetectorYN no es thread-safe)
_PERSON_FAILED: list[str] = []   # la descarga de la compuerta ya fallo en este proceso
# UNA sesion ONNX por proceso y modelo: InferenceSession.run es seguro entre hilos, asi que los
# hilos de inferencia la comparten (antes: una por hilo y un executor nuevo en cada prepare ->
# sesiones recreadas en cada rango). Sin "spinning": con varios procesos/hilos compitiendo por
# la CPU, los hilos ociosos de ORT no queman nucleos esperando trabajo.
_SESS: dict[tuple, object] = {}
_SESS_LOCK = threading.Lock()
_POOLS: dict[str, ThreadPoolExecutor] = {}
_POOL_LOCK = threading.Lock()
_META_LOCK = threading.Lock()    # meta.json / faces.json: leer-mezclar-escribir sin carreras


def session_threads() -> int:
    """Hilos intra-op de la sesion compartida (REELFY_MATTE_THREADS para forzar)."""
    try:
        v = int(os.environ.get("REELFY_MATTE_THREADS") or 0)
    except ValueError:
        v = 0
    return v if v > 0 else max(2, (os.cpu_count() or 4) // 3)


def _session(threads: int | None = None, which: str = "modnet"):
    """Sesion compartida del modelo `which` en CPU (se crea una sola vez por proceso).
    `threads` se conserva por compatibilidad: el numero de hilos lo fija session_threads()."""
    path = str(model_path(which))
    key = (which, path)
    s = _SESS.get(key)
    if s is not None:
        return s
    with _SESS_LOCK:
        s = _SESS.get(key)
        if s is None:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = session_threads()
            so.inter_op_num_threads = 1
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            so.log_severity_level = 3
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            s = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
            _SESS[key] = s
    return s


# ---- MODNet en el Neural Engine (macOS): ONNX Runtime + CoreML EP con forma fija ----------
# Medido (M2 Max, 512x928): CPU ~860 ms de CPU por cuadro (~150-250 ms de pared compitiendo);
# CoreML/ANE ~100 ms de CPU y ~45 ms de pared con 3 llamadas simultaneas. Diferencia del alfa
# crudo: media 2e-4 (fp16), invisible tras el suavizado y el upsample guiado. La forma de
# entrada es fija por sesion (el recorte varia por rango): una sesion por forma, con el modelo
# compilado en disco por forma (1.5 s la primera vez en la maquina, 0.2 s despues).
# REELFY_MATTE_COREML=0 lo desactiva; si CoreML falla se cae a CPU sin romper nada.
_COREML_OFF: list[str] = []


def coreml_enabled() -> bool:
    if _COREML_OFF or os.environ.get("REELFY_MATTE_COREML", "1") == "0" or sys.platform != "darwin":
        return False
    try:
        import onnxruntime as ort
        return "CoreMLExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


def coreml_dir() -> Path:
    """Carpeta de los modelos CoreML compilados (una subcarpeta por forma). Default
    DATA/models/matte/coreml; REELFY_MATTE_COREML_DIR la cambia (las pruebas usan un temporal
    para no llenar ni podar la cache real con formas de juguete)."""
    env = os.environ.get("REELFY_MATTE_COREML_DIR")
    if env:
        return Path(env)
    return model_path("modnet").parent / "matte" / "coreml"


# fallos al crear una sesion CoreML: {clave: (intentos, momento)}. No se guardan como None para
# siempre (un fallo transitorio, p. ej. la carpeta podada por otro proceso a media compilacion,
# dejaba esa forma en CPU todo el proceso): se reintenta tras COREML_RETRY_S, hasta 3 veces.
_COREML_FAILS: dict[tuple, tuple[int, float]] = {}
COREML_RETRY_S = 60.0


def _coreml_session(shape: tuple[int, int] | None, which: str = "modnet"):
    """Sesion CoreML del modelo `which` (MODNet: para la forma (alto, ancho) de entrada; la
    compuerta de persona ya es de forma fija), o None (se usa CPU)."""
    path = str(model_path(which))
    key = (f"{which}-coreml", path, tuple(shape or ()))
    s = _SESS.get(key)
    if s is not None:
        return s
    fails = _COREML_FAILS.get(key)
    if fails and (fails[0] >= 3 or time.time() - fails[1] < COREML_RETRY_S):
        return None
    with _SESS_LOCK:
        s = _SESS.get(key)
        if s is not None:
            return s
        fails = _COREML_FAILS.get(key)
        if fails and (fails[0] >= 3 or time.time() - fails[1] < COREML_RETRY_S):
            return None
        s = None
        try:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2
            so.inter_op_num_threads = 1
            so.log_severity_level = 3
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            tag = f"{which}_{str(MODELS[which]['sha256'])[:8]}"
            if shape:
                h, w = int(shape[0]), int(shape[1])
                for k, v in (("batch_size", 1), ("height", h), ("width", w)):
                    so.add_free_dimension_override_by_name(k, v)
                tag += f"_{h}x{w}"
            # una carpeta por forma: la cache de ORT no distingue formas fijadas por override
            cdir = coreml_dir() / tag
            cdir.mkdir(parents=True, exist_ok=True)
            _prune_coreml(cdir)
            opts = {"MLComputeUnits": "CPUAndNeuralEngine", "ModelFormat": "NeuralNetwork",
                    "RequireStaticInputShapes": "1", "ModelCacheDirectory": str(cdir)}
            s = ort.InferenceSession(path, so, providers=[("CoreMLExecutionProvider", opts),
                                                          "CPUExecutionProvider"])
        except Exception as e:  # noqa: sin CoreML para esta forma -> CPU (se reintenta luego)
            _log(f"CoreML no disponible para {which} {shape}: {type(e).__name__}: {str(e)[:160]}")
            n_fail = (_COREML_FAILS.get(key) or (0, 0.0))[0] + 1
            _COREML_FAILS[key] = (n_fail, time.time())
            return None
        _COREML_FAILS.pop(key, None)
        same = [k for k in _SESS if k[0] == key[0]]
        if len(same) >= 8:
            for k in same[:4]:
                _SESS.pop(k, None)
        _SESS[key] = s
        return s


COREML_KEEP = 8          # formas compiladas que se conservan en disco (~50 MB cada una)
COREML_FRESH_S = 300.0   # una carpeta usada hace menos de esto no se poda (otro proceso la usa)


def _prune_coreml(current: Path, keep: int = COREML_KEEP, fresh_s: float = COREML_FRESH_S) -> None:
    """LRU de los modelos CoreML compilados por forma: marca `current` como recien usado y
    borra los mas viejos por encima de `keep` (la cache no crece sin limite). Nunca borra una
    carpeta tocada hace menos de `fresh_s` s: puede estar compilandose o en uso en otro
    proceso (render en paralelo, ensure_analysis del Studio)."""
    try:
        os.utime(current, None)
        now = time.time()
        dirs = [d for d in current.parent.iterdir() if d.is_dir() and d.name.startswith("modnet_")]
        dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        for d in dirs[keep:]:
            try:
                if d == current or now - d.stat().st_mtime < fresh_s:
                    continue
            except OSError:
                continue
            shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def _run(which: str, feed: dict, shape: tuple | None, threads: int | None = None):
    """Inferencia con CoreML si esta disponible (y no fallo en este proceso); si no, CPU."""
    if coreml_enabled():
        s = _coreml_session(shape, which)
        if s is not None:
            try:
                return s.run(None, feed)[0]
            except Exception as e:  # noqa: un fallo de CoreML desactiva el EP en el proceso
                if not _COREML_OFF:
                    _COREML_OFF.append(str(e)[:200])
                    _log(f"CoreML fallo al inferir ({type(e).__name__}); se usa CPU")
    return _session(threads, which).run(None, feed)[0]


def engine() -> str:
    """Motor de inferencia de MODNet en este proceso: 'coreml' o 'cpu'."""
    return "coreml" if coreml_enabled() else "cpu"


def _pool(kind: str, n: int) -> ThreadPoolExecutor:
    """Executor PERSISTENTE por tipo de trabajo (inferencia / terminado): los hilos viven entre
    rangos y entre llamadas a prepare (antes se creaba y destruia uno por rango)."""
    n = max(1, int(n))
    with _POOL_LOCK:
        ex = _POOLS.get(kind)
        if ex is None or getattr(ex, "_max_workers", 0) < n or getattr(ex, "_shutdown", False):
            if ex is not None:
                ex.shutdown(wait=False)
            ex = ThreadPoolExecutor(n, thread_name_prefix=f"matte-{kind}")
            _POOLS[kind] = ex
        return ex


def _face_det(w: int, h: int):
    d = getattr(_SESS_LOCAL, "yunet", None)
    if d is None or getattr(_SESS_LOCAL, "yunet_size", None) != (w, h):
        import paths
        d = cv2.FaceDetectorYN.create(str(paths.YUNET), "", (w, h), 0.6, 0.3, 5000)
        _SESS_LOCAL.yunet = d
        _SESS_LOCAL.yunet_size = (w, h)
    return d


def infer(rgb: np.ndarray, threads: int = 3) -> np.ndarray:
    """MODNet sobre un cuadro RGB uint8 (ya al tamano de inferencia, multiplo de 32).
    Devuelve float32 HxW en [0,1]."""
    x = rgb.astype(np.float32) * (2.0 / 255.0) - 1.0
    x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
    out = _run("modnet", {"input": x}, rgb.shape[:2], threads)
    return np.clip(out[0, 0], 0.0, 1.0).astype(np.float32)


def person_prob(rgb: np.ndarray, threads: int = 1) -> np.ndarray:
    """PP-HumanSeg sobre el cuadro COMPLETO (se reduce a 192x192): probabilidad de persona
    float32 192x192 (hay que reescalarla al tamano que se necesite)."""
    x = cv2.resize(rgb[..., :3], (192, 192), interpolation=cv2.INTER_AREA).astype(np.float32)
    x = (x * (2.0 / 255.0) - 1.0).transpose(2, 0, 1)[None]
    out = _run("person", {"x": np.ascontiguousarray(x)}, None, threads)
    return np.clip(out[0, 1], 0.0, 1.0).astype(np.float32)


def detect_faces(rgb: np.ndarray) -> list[tuple]:
    """Caras (cx, cy, w, h, score) normalizadas en el cuadro RGB dado."""
    h, w = rgb.shape[:2]
    det = _face_det(w, h)
    _, faces = det.detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if faces is None:
        return []
    return [((f[0] + f[2] / 2) / w, (f[1] + f[3] / 2) / h, f[2] / w, f[3] / h, float(f[14])) for f in faces]


# ---------------------------------------------------------------- post-proceso
def body_gate(m: np.ndarray, faces: list[tuple], thr: float = 0.2) -> np.ndarray:
    """Conserva solo los componentes de la mascara que tocan la zona del cuerpo de alguna
    cara (ancho +-3.5 caras, desde un poco arriba de la cabeza hasta abajo). Borde suave."""
    if not faces:
        return np.zeros_like(m)
    h, w = m.shape
    binm = (m > thr).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binm, connectivity=8)
    if n <= 1:
        return m
    zone = np.zeros((h, w), np.uint8)
    for cx, cy, fw, fh, _s in faces:
        x0, x1 = int(max(0, (cx - 3.5 * fw) * w)), int(min(w, (cx + 3.5 * fw) * w))
        y0 = int(max(0, (cy - 1.6 * fh) * h))
        zone[y0:, x0:x1] = 1
        # la cabeza siempre cuenta aunque el cuerpo quede fuera
        hx0, hx1 = int(max(0, (cx - fw) * w)), int(min(w, (cx + fw) * w))
        hy0, hy1 = int(max(0, (cy - fh) * h)), int(min(h, (cy + fh) * h))
        zone[hy0:hy1, hx0:hx1] = 2
    keep = np.zeros(n, bool)
    # componente toca la zona y (si es enorme) tambien la cabeza/torso
    hits = np.bincount(lab[zone > 0].ravel(), minlength=n)
    head = np.bincount(lab[zone == 2].ravel(), minlength=n)
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if hits[i] > max(20, 0.15 * area) or head[i] > 20:
            keep[i] = True
    if keep[1:].all():
        return m
    km = keep[lab].astype(np.uint8) * 255
    km = cv2.dilate(km, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    km = cv2.GaussianBlur(km, (0, 0), 2.0).astype(np.float32) / 255.0
    return m * km


def body_prior(shape: tuple[int, int], faces: list[tuple]) -> np.ndarray:
    """Prior geometrico del cuerpo (float32 [0,1]) desde la cara: cabeza con cabello, hombros
    y brazos (+-2.4 caras), torso (+-1.9) y piernas (+-1.3), con borde suave. Compuerta de
    respaldo cuando no hay modelo de persona."""
    h, w = shape
    out = np.zeros((h, w), np.float32)
    for cx, cy, fw, fh, *_ in faces:
        X, Y, FW, FH = cx * w, cy * h, max(2.0, fw * w), max(2.0, fh * h)
        pts = np.array([
            (X - 1.25 * FW, Y - 1.35 * FH), (X + 1.25 * FW, Y - 1.35 * FH),
            (X + 1.3 * FW, Y + 0.6 * FH), (X + 2.4 * FW, Y + 1.5 * FH),
            (X + 2.3 * FW, Y + 4.5 * FH), (X + 1.9 * FW, Y + 6.0 * FH),
            (X + 1.3 * FW, Y + 7.5 * FH), (X + 1.3 * FW, h + 10),
            (X - 1.3 * FW, h + 10), (X - 1.3 * FW, Y + 7.5 * FH),
            (X - 1.9 * FW, Y + 6.0 * FH), (X - 2.3 * FW, Y + 4.5 * FH),
            (X - 2.4 * FW, Y + 1.5 * FH), (X - 1.3 * FW, Y + 0.6 * FH)], np.int32)
        cv2.fillPoly(out, [pts], 1.0)
    k = max(1.0, 0.35 * max(fw * w for _, _, fw, *_r in faces)) if faces else 1.0
    return cv2.GaussianBlur(out, (0, 0), k)


def person_gate(p: np.ndarray, dil_px: float) -> np.ndarray:
    """Probabilidad de persona (ya al tamano de la mascara) -> compuerta suave: region de
    persona dilatada `dil_px` (el cabello suelto y los dedos quedan dentro) con borde suave."""
    binm = (p > 0.45).astype(np.uint8) * 255
    r = max(1, int(round(dil_px)))
    binm = cv2.dilate(binm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    g = cv2.GaussianBlur(binm, (0, 0), max(1.0, r * 0.5)).astype(np.float32) / 255.0
    return np.clip(g * 1.25, 0.0, 1.0)


def gate_matte(m: np.ndarray, faces: list[tuple], p: np.ndarray | None = None,
               dil_px: float = 12.0) -> tuple[np.ndarray, float]:
    """MODNet (m) -> mascara solo de la persona: compuerta semantica (p = probabilidad de
    persona al tamano de m; sin ella, prior geometrico del cuerpo desde las caras) y luego
    solo los componentes que tocan el cuerpo. Devuelve (mascara, fraccion de masa quitada)."""
    gate = person_gate(p, dil_px) if p is not None else body_prior(m.shape, faces)
    mass = float(m.sum())
    g = body_gate(m * gate, faces)
    rem = 1.0 - float(g.sum()) / mass if mass > 1.0 else 0.0
    return g, max(0.0, rem)


def temporal_smooth(keys: list[np.ndarray], strength: float = 0.5, tol: float = 0.12) -> list[np.ndarray]:
    """Suavizado temporal simetrico (sin retraso) sensible a bordes: promedia con los vecinos
    solo donde la mascara casi no cambia (quita el parpadeo), respeta los bordes en
    movimiento (sin arrastre/estela)."""
    if len(keys) < 3:
        return keys
    out = []
    for i, m in enumerate(keys):
        acc = m.copy()
        wsum = np.ones_like(m)
        for j in (i - 1, i + 1):
            if 0 <= j < len(keys):
                d = np.abs(keys[j] - m)
                wj = (0.5 * strength) * np.exp(-(d / tol) ** 2)
                acc += wj * keys[j]
                wsum += wj
        out.append(acc / wsum)
    return out


def area_hysteresis(keys: list[np.ndarray], fps: float, step: int, max_drop: float = 0.4,
                    fade_s: float = 0.25) -> list[np.ndarray]:
    """Si el area de la mascara cae mas de `max_drop` de una clave a la siguiente (el modelo
    perdio a la persona), la mascara anterior se desvanece en ~fade_s en lugar de desaparecer
    de golpe (sin parpadeos de color en color_pop / B/N con sujeto a color)."""
    if len(keys) < 2:
        return keys
    out = [k for k in keys]
    nfade = max(1, int(round(fade_s * fps / max(1, step))))
    area = [float(k.mean()) for k in keys]
    for i in range(1, len(out)):
        ref = area[i - 1]                   # areas originales: un solo fundido por caida
        if ref > 1e-3 and area[i] < (1.0 - max_drop) * ref:
            src = out[i - 1]
            for j in range(nfade):
                if i + j >= len(out):
                    break
                w = 1.0 - (j + 1) / (nfade + 1)
                out[i + j] = np.maximum(out[i + j], src * w)
    return out


def guided_upsample(p_low: np.ndarray, guide_rgb: np.ndarray, r: int = 3, eps: float = 2e-3) -> np.ndarray:
    """Fast guided filter (He et al.): coeficientes a baja resolucion con la guia reducida,
    upsample de los coeficientes y salida a resolucion completa q = a * I + b.
    p_low float32 hxw [0,1]; guide_rgb uint8 HxWx3 (o x4). Devuelve float32 HxW."""
    H, W = guide_rgb.shape[:2]
    h, w = p_low.shape
    code = cv2.COLOR_RGBA2GRAY if guide_rgb.shape[2] == 4 else cv2.COLOR_RGB2GRAY
    I_full = cv2.cvtColor(guide_rgb, code).astype(np.float32) * (1.0 / 255.0)
    I = cv2.resize(I_full, (w, h), interpolation=cv2.INTER_AREA)
    k = (2 * r + 1, 2 * r + 1)
    box = lambda x: cv2.boxFilter(x, -1, k, borderType=cv2.BORDER_REFLECT)  # noqa: E731
    mI, mp = box(I), box(p_low)
    cov = box(I * p_low) - mI * mp
    var = box(I * I) - mI * mI
    a = cov / (var + eps)
    b = mp - a * mI
    ma = cv2.resize(box(a), (W, H), interpolation=cv2.INTER_LINEAR)
    mb = cv2.resize(box(b), (W, H), interpolation=cv2.INTER_LINEAR)
    q = ma * I_full + mb
    return q


def refine(q: np.ndarray, lo: float = 0.04, hi: float = 0.96) -> np.ndarray:
    """Limpia neblina y satura el interior: reescala [lo, hi] -> [0, 1]."""
    return np.clip((q - lo) * (1.0 / (hi - lo)), 0.0, 1.0)


def _merge_ranges(frame_ranges) -> list[tuple[int, int]]:
    rs = []
    for r in frame_ranges or []:
        try:
            a, b = int(r[0]), int(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        if b > a:
            rs.append((max(0, a), b))
    rs.sort()
    out: list[list[int]] = []
    for a, b in rs:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _read_range(video: str, a: int, b: int, w: int, h: int, fps: float, step: int | None = None):
    """Generador (n, rgb uint8 hxwx3) de los cuadros [a, b) del video, con busqueda por tiempo
    (solo se decodifica desde el keyframe previo). Con step: solo claves a, a+step, ... y b-1.
    Busqueda a medio cuadro antes (primer pts >= (a-0.5)/fps = cuadro a), como video_io."""
    import paths
    fsz = w * h * 3
    cnt = b - a
    if cnt <= 0:
        return
    if step and step > 1:
        ks = list(range(0, cnt, step))
        if ks[-1] != cnt - 1:
            ks.append(cnt - 1)
        sel = f"select=not(mod(n\\,{step}))+eq(n\\,{cnt - 1}),"
    else:
        ks = list(range(cnt))
        sel = ""
    cmd = [paths.FFMPEG, "-v", "error", "-nostdin"]
    if a > 0:
        cmd += ["-ss", f"{(a - 0.5) / fps:.6f}"]
    cmd += ["-i", str(video), "-an", "-frames:v", str(cnt), "-vf", f"{sel}scale={w}:{h}:flags=area",
            "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=fsz * 2)
    try:
        for k in ks:
            buf = proc.stdout.read(fsz)
            if not buf or len(buf) < fsz:
                break
            yield a + k, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def _read_frames(video: str, ranges: list[tuple[int, int]], w: int, h: int, step: int | None = None,
                 fps: float = 30.0):
    """Compatibilidad: todos los rangos (end exclusivo), en orden."""
    for a, b in sorted(ranges):
        yield from _read_range(video, a, b, w, h, fps, step)


def prune_cache(cache_dir=None, max_mb: float = 3000.0, keep: int = 3) -> dict:
    """Limpia la cache de mascaras: borra temporales huerfanos y los directorios matte_* mas
    viejos (por uso) hasta quedar por debajo de max_mb, conservando siempre los `keep` mas
    recientes. Devuelve {borrados, mb_final}."""
    if cache_dir is None:
        import paths
        cache_dir = Path(paths.WORK) / "motion_cache"
    root = Path(cache_dir)
    if not root.exists():
        return {"deleted": [], "mb": 0.0}
    dirs = []
    for d in root.glob("matte_*"):
        if not d.is_dir():
            continue
        for t in d.glob("*.tmp*"):
            try:
                t.unlink()
            except OSError:
                pass
        size = sum(f.stat().st_size for f in d.iterdir() if f.is_file())
        used = max((f.stat().st_mtime for f in d.iterdir() if f.is_file()), default=d.stat().st_mtime)
        dirs.append((used, size, d))
    dirs.sort(key=lambda x: -x[0])
    total = sum(s for _, s, _ in dirs)
    deleted = []
    for used, size, d in dirs[keep:][::-1]:
        if total / 1e6 <= max_mb:
            break
        shutil.rmtree(d, ignore_errors=True)
        total -= size
        deleted.append(d.name)
    return {"deleted": deleted, "mb": round(total / 1e6, 1)}


# ---------------------------------------------------------------- proveedor
class MatteProvider:
    """Mascaras del sujeto por cuadro para los rangos pedidos, cacheadas en disco.

    frame_ranges: [(a, b)] cuadros del video base, b exclusivo.
    quality: "final" (inferencia ~512 px, cada 2 cuadros) | "draft" (384 px, cada 3).
    Tras prepare(): .ok / .error, .warning (p. ej. sin compuerta de persona), .stats por rango
    y quality_of(a, b) (1 = mascara limpia; < ~0.85 = la persona tocaba muebles grandes y la
    compuerta tuvo que recortar mucho: conviene evitar contorno/reemplazo de fondo ahi).
    """

    CHUNK = 240          # cuadros por bloque de inferencia (memoria acotada)
    FACE_SHARE = 0.25    # fraccion minima de claves con cara para inferir el rango
    CONTEXT = 6          # cuadros de contexto a cada lado al completar un hueco (incremental)

    def __init__(self, base_video, W: int, H: int, fps: float, frame_ranges, cache_dir,
                 quality: str = "final", workers: int | None = None, step: int | None = None):
        self.base_video = str(base_video)
        self.W, self.H, self.fps = int(W), int(H), float(fps)
        self.ranges = _merge_ranges(frame_ranges)
        self.quality = quality
        self.draft = quality == "draft"
        iw = 384 if self.draft else 512
        ih = int(max(32, round(iw * self.H / self.W / 32.0) * 32))
        self.inf_size = (iw, ih)
        self.step = int(step) if step else (3 if self.draft else 2)
        cpu = os.cpu_count() or 4
        # llamadas de inferencia simultaneas sobre la sesion compartida (cada una usa
        # session_threads() hilos): entre las dos cubren la CPU sin sobresuscribirla
        self.workers = workers or max(1, min(4, cpu // session_threads()))
        self.threads = session_threads()
        self.error: str | None = None
        self.warning: str | None = None
        self.stats: dict[str, dict] = {}
        self._lru: dict[int, np.ndarray] = {}
        self._zero: np.ndarray | None = None
        self._faces: dict[int, list | None] | None = None
        key = self._key()
        if not cache_dir:
            import paths
            cache_dir = Path(paths.WORK) / "motion_cache"
        self.dir = Path(cache_dir) / f"matte_{key}"
        self._load_meta()

    def __getstate__(self):
        d = dict(self.__dict__)
        d["_lru"] = {}
        d["_zero"] = None
        d["_faces"] = None
        return d

    def _key(self) -> str:
        try:
            st = Path(self.base_video).stat()
            vid = f"{Path(self.base_video).name}:{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            vid = self.base_video
        raw = f"{vid}|{self.W}x{self.H}|{self.quality}|{MODEL_SIZE}|{_ALGO}"
        if self.step != (3 if self.draft else 2):          # paso no estandar: su propia cache
            raw += f"|s{self.step}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    @property
    def ok(self) -> bool:
        return self.error is None

    def _png(self, n: int) -> Path:
        return self.dir / f"{n:06d}.png"

    def in_ranges(self, n: int) -> bool:
        return any(a <= n < b for a, b in self.ranges)

    def missing_ranges(self) -> list[tuple[int, int]]:
        """Rangos pedidos a los que les falta al menos un cuadro."""
        out = []
        for a, b in self.ranges:
            if any(not self._png(n).exists() for n in range(a, b)):
                out.append((a, b))
        return out

    def missing_runs(self) -> list[tuple[int, int, int, int]]:
        """Huecos contiguos SIN mascara dentro de los rangos pedidos: [(a, b, ra, rb)] con
        (ra, rb) el rango pedido que los contiene. prepare() solo calcula estos huecos (con un
        poco de contexto a cada lado): agregar un efecto que pide al sujeto junto a otro ya
        calculado no rehace lo que ya esta en la cache."""
        out = []
        for ra, rb in self.ranges:
            a = None
            for n in range(ra, rb):
                miss = not self._png(n).exists()
                if miss and a is None:
                    a = n
                elif not miss and a is not None:
                    out.append((a, n, ra, rb))
                    a = None
            if a is not None:
                out.append((a, rb, ra, rb))
        return out

    # -------------------------------------------------- meta / calidad
    def _meta_file(self) -> Path:
        return self.dir / "meta.json"

    def _load_meta(self) -> None:
        try:
            self.stats = json.loads(self._meta_file().read_text()).get("ranges", {})
        except Exception:
            self.stats = {}

    def _save_meta(self) -> None:
        """Mezcla self.stats con lo que haya en disco y lo escribe de forma atomica. Temporal
        unico por proceso e hilo (dos prepare sobre la misma cache, p. ej. ensure_analysis en
        segundo plano y un export, no se pisan) y nunca lanza: perder la meta solo pierde la
        calidad por rango, no las mascaras."""
        with _META_LOCK:
            try:
                cur = json.loads(self._meta_file().read_text()).get("ranges", {})
            except Exception:
                cur = {}
            cur.update(self.stats)
            self.stats = cur
            self._atomic_json(self._meta_file(), {"ranges": cur, "algo": _ALGO})

    @staticmethod
    def _atomic_json(path: Path, obj: dict) -> None:
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}_{threading.get_ident() % 100000}")
        try:
            tmp.write_text(json.dumps(obj))
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as e:
            _log(f"no se pudo escribir {path.name}: {e}")
            try:
                tmp.unlink()
            except OSError:
                pass

    # -------------------------------------------------- caras por cuadro (cache)
    def _faces_file(self) -> Path:
        return self.dir / "faces.json"

    def _load_faces(self) -> dict[int, list | None]:
        """Caras (cx, cy, w, h, score) por cuadro detectadas a 360 px; None = cuadro fuera del
        video. Se guardan por cuadro ABSOLUTO del base: sirven para cualquier rango."""
        if self._faces is None:
            self._faces = {}
            try:
                d = json.loads(self._faces_file().read_text())
                if d.get("algo") == _ALGO:
                    self._faces = {int(k): (None if v is None else [tuple(f) for f in v])
                                   for k, v in (d.get("faces") or {}).items()}
            except Exception:
                pass
        return self._faces

    def _save_faces(self) -> None:
        with _META_LOCK:
            cur = {}
            try:
                d = json.loads(self._faces_file().read_text())
                if d.get("algo") == _ALGO:
                    cur = d.get("faces") or {}
            except Exception:
                pass
            cur.update({str(k): (None if v is None else [list(f) for f in v])
                        for k, v in (self._faces or {}).items()})
            self._atomic_json(self._faces_file(), {"algo": _ALGO, "faces": cur})

    def _keys_of(self, ra: int, rb: int) -> list[int]:
        """Cuadros clave de un rango pedido: ra, ra+step, ... y el ultimo (rb-1)."""
        ks = list(range(ra, rb, self.step))
        if ks and ks[-1] != rb - 1:
            ks.append(rb - 1)
        return ks

    def _range_faces(self, ra: int, rb: int) -> dict[int, list]:
        """Caras en las claves del rango pedido [ra, rb): solo se decodifican y detectan las
        claves que falten en la cache (un hueco nuevo no repite la deteccion del resto)."""
        W, H, fps = self.W, self.H, self.fps
        ks = self._keys_of(ra, rb)
        cache = self._load_faces()
        miss = [k for k in ks if k not in cache]
        if miss:
            dw = 360
            dh = int(round(dw * H / W / 2) * 2)
            a0, b0 = miss[0], miss[-1] + 1       # a0 esta en la reticula del rango
            got = set()
            for n, rgb in _read_range(self.base_video, a0, b0, dw, dh, fps, self.step):
                got.add(n)
                if n in cache:
                    continue
                fs = [tuple(float(v) for v in f) for f in detect_faces(rgb) if f[2] >= 0.025]
                cache[n] = sorted(fs, key=lambda f: -f[2] * f[3])[:2]
            last = max(got) if got else None
            for k in miss:
                if k not in cache and last is not None and k > last:
                    cache[k] = None                # mas alla del final del video
            self._save_faces()
        return {k: cache[k] for k in ks if cache.get(k) is not None}

    def _range_plan(self, faces: dict[int, list]) -> dict:
        """Gating y recorte de un rango pedido a partir de las caras de sus claves."""
        ks = sorted(faces)
        with_face = [k for k in ks if faces[k]]
        plan = {"faces": faces, "keys": len(ks), "with_face": with_face, "person": False,
                "crop": None, "dims": None}
        if not ks or len(with_face) < max(1, self.FACE_SHARE * len(ks)):
            return plan
        crop = self._crop_for([faces[k][0] for k in with_face])
        plan.update(person=True, crop=crop, dims=self._inf_dims(crop))
        return plan

    def quality_of(self, a: int, b: int) -> float | None:
        """Calidad media (1 = limpia) de la mascara en [a, b), ponderada por cuadros. None si
        no hay informacion (rango sin calcular)."""
        acc = wsum = 0.0
        for k, st in self.stats.items():
            try:
                ra, rb = (int(v) for v in k.split("-"))
            except ValueError:
                continue
            ov = min(b, rb) - max(a, ra)
            if ov > 0 and st.get("quality") is not None:
                acc += float(st["quality"]) * ov
                wsum += ov
        return round(acc / wsum, 3) if wsum > 0 else None

    # -------------------------------------------------- calculo
    def _crop_for(self, fs: list[tuple]) -> tuple[int, int, int, int] | None:
        """Recorte fijo (x0, y0, x1, y1) px que contiene el cuerpo de todas las caras del rango.
        Inferir sobre el recorte da mas resolucion al sujeto y menos distractores."""
        if not fs:
            return None
        W, H = self.W, self.H
        x0 = min((f[0] - 3.3 * f[2]) for f in fs) * W
        x1 = max((f[0] + 3.3 * f[2]) for f in fs) * W
        y0 = min((f[1] - 2.2 * f[3]) for f in fs) * H
        x0, x1, y0 = max(0.0, x0), min(float(W), x1), max(0.0, y0)
        minw = 0.5 * W
        if x1 - x0 < minw:
            c = (x0 + x1) / 2
            x0, x1 = max(0.0, c - minw / 2), min(float(W), c + minw / 2)
            if x1 - x0 < minw:
                x0, x1 = (0.0, minw) if x0 <= 0 else (W - minw, float(W))
        return int(x0), int(y0), int(math.ceil(x1)), H

    def _inf_dims(self, crop) -> tuple[int, int]:
        iw, ih = self.inf_size
        cw, ch = crop[2] - crop[0], crop[3] - crop[1]
        s = math.sqrt(iw * ih / max(1, cw * ch))
        s = min(s, 1.0)  # nunca ampliar por encima de la resolucion real
        return max(32, int(round(cw * s / 32)) * 32), max(32, int(round(ch * s / 32)) * 32)

    def _write(self, n: int, u8: np.ndarray) -> None:
        """Escritura atomica (tmp + replace): un render cancelado nunca deja PNG truncados."""
        p = self._png(n)
        tmp = p.with_name(p.name + f".tmp{os.getpid()}_{threading.get_ident() % 100000}.png")
        cv2.imwrite(str(tmp), u8, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        os.replace(tmp, p)

    def prepare(self, on_pct=None) -> bool:
        """Calcula las mascaras que falten. Incremental: solo se infieren y ESCRIBEN los huecos
        sin mascara dentro de los rangos pedidos (missing_runs), cada uno con CONTEXT cuadros de
        contexto a cada lado para el suavizado temporal (no se reescriben). Las caras, el
        gating y el recorte se deciden sobre el RANGO PEDIDO completo (no sobre el hueco): un
        hueco sin caras detectadas dentro de un rango con caras se infiere igual (antes quedaba
        en cero y la persona desaparecia), y el recorte es el mismo que al calcular de una vez.
        Devuelve True si quedaron listas."""
        todo = self.missing_runs()
        if not todo:
            if on_pct:
                on_pct(100.0)
            return True
        if not Path(self.base_video).exists():
            self.error = f"no existe el video base: {self.base_video}"
            return False
        try:
            if not model_ready():
                ensure_model()
        except Exception as e:
            self.error = str(e)
            return False
        use_person = True
        try:
            if not model_ready(which="person"):
                if _PERSON_FAILED:
                    raise RuntimeError(_PERSON_FAILED[0])
                ensure_model(which="person", retries=1)
        except Exception as e:
            if not _PERSON_FAILED:
                _PERSON_FAILED.append(str(e))     # no reintentar en cada render de este proceso
            use_person = False
            self.warning = f"sin compuerta de persona ({e}); se usa el prior geometrico"
            _log(self.warning)
        self.dir.mkdir(parents=True, exist_ok=True)
        pct = on_pct or (lambda p: None)
        total = sum(b - a for a, b, _, _ in todo)
        done = [0]
        lock = threading.Lock()
        stop: list[BaseException] = []            # excepcion de on_pct (cancelacion)

        def tick(k=1):
            with lock:
                done[0] += k
                try:
                    pct(min(99.5, 100.0 * done[0] / max(1, total)))
                except BaseException as e:
                    stop.append(e)
                    raise

        by_range: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for (a, b, ra, rb) in todo:
            by_range.setdefault((ra, rb), []).append((a, b))
        for (ra, rb), gaps in by_range.items():
            try:
                plan = self._range_plan(self._range_faces(ra, rb))
            except Exception as e:  # noqa: sin caras legibles -> cada hueco decide solo
                _log(f"caras del rango {ra}-{rb} fallaron: {type(e).__name__}: {e}")
                plan = None
            for (a, b) in gaps:
                ea, eb = max(ra, a - self.CONTEXT), min(rb, b + self.CONTEXT)
                try:
                    st = self._prepare_range(ea, eb, use_person, tick, write=(a, b), plan=plan)
                except Exception as e:  # un hueco que falla no tumba el render: queda vacio
                    if stop:           # cancelado desde on_pct: se propaga SIN escribir vacios
                        self._save_meta()
                        raise stop[0]
                    _log(f"rango {a}-{b} fallo: {type(e).__name__}: {e}")
                    z = np.zeros((self.H, self.W), np.uint8)
                    for n in range(a, b):
                        if not self._png(n).exists():
                            self._write(n, z)
                    st = {"quality": None, "error": str(e)}
                if (a, b) != (ra, rb):
                    st["range"] = [ra, rb]
                self.stats[f"{a}-{b}"] = st
        self._save_meta()
        pct(100.0)
        return True

    def _prepare_range(self, a: int, b: int, use_person: bool, tick,
                       write: tuple[int, int] | None = None, plan: dict | None = None) -> dict:
        """Mascaras del tramo [a, b) (claves y suavizado sobre todo el tramo); solo se ESCRIBEN
        los cuadros de `write` (default: todo el tramo). `plan` (de _range_plan) trae las caras,
        el gating y el recorte del rango pedido completo; sin plan se deciden sobre [a, b)."""
        W, H, fps, step = self.W, self.H, self.fps, self.step
        wa, wb = write if write else (a, b)
        wa, wb = max(a, wa), min(b, wb)
        # A+B) caras, gating y recorte (del rango pedido si viene el plan)
        if plan is None:
            plan = self._range_plan(self._range_faces(a, b))
        faces, with_face = plan["faces"], plan["with_face"]
        if not plan["person"]:
            z = np.zeros((H, W), np.uint8)
            got = 0
            for n in range(wa, wb):
                self._write(n, z)
                got += 1
                if got % 16 == 0:
                    tick(16)
            tick(got % 16)
            return {"quality": 1.0, "person": False, "keys": plan["keys"],
                    "face_keys": len(with_face)}
        crop, dims = plan["crop"], plan["dims"]
        x0, y0, x1, y1 = crop
        cw, ch = x1 - x0, y1 - y0
        iw, ih = dims

        def nearest_face(k):
            if faces.get(k):
                return faces[k]
            i = bisect.bisect_left(with_face, k)
            cands = [with_face[j] for j in (i - 1, i) if 0 <= j < len(with_face)]
            return faces[min(cands, key=lambda c: abs(c - k))]

        removed: list[float] = []
        infer_ex = _pool(f"infer{self.workers}", self.workers)
        nfin = max(2, self.workers * 2)
        finish_ex = _pool(f"finish{nfin}", nfin)
        # C+D por bloques (memoria acotada), con una clave de solape a cada lado
        for c0 in range(a, b, self.CHUNK):
            c1 = min(b, c0 + self.CHUNK)
            f0, f1 = max(c0, wa), min(c1, wb)
            if f1 <= f0:
                continue                          # bloque de puro contexto: nada que escribir
            ka = max(a, c0 - step)
            kb = min(b, c1 + step)
            keys: dict[int, np.ndarray] = {}

            def work(n, rgb_full):
                x = cv2.resize(rgb_full[y0:y1, x0:x1], dims, interpolation=cv2.INTER_AREA)
                m = infer(x, self.threads)
                fk = nearest_face(n)
                fc = [((cx * W - x0) / cw, (cy * H - y0) / ch, fw * W / cw, fh * H / ch, sc)
                      for cx, cy, fw, fh, sc in fk]
                pc = None
                if use_person:
                    p = person_prob(rgb_full)
                    pf = cv2.resize(p, (W // 4, H // 4), interpolation=cv2.INTER_LINEAR)
                    pc = pf[y0 // 4:max(y0 // 4 + 1, y1 // 4), x0 // 4:max(x0 // 4 + 1, x1 // 4)]
                    pc = cv2.resize(pc, dims, interpolation=cv2.INTER_LINEAR)
                g, rem = gate_matte(m, fc, pc, 16.0 * (W / 1080.0) * (iw / cw))
                return n, g, rem

            def collect(fu):
                n, g, rem = fu.result()
                keys[n] = g
                if f0 <= n < f1:
                    removed.append(rem)

            pend = []
            try:
                for n, rgb in _read_range(self.base_video, ka, kb, W, H, fps, step):
                    pend.append(infer_ex.submit(work, n, rgb))
                    if len(pend) > self.workers:  # limita memoria y concurrencia en vuelo
                        collect(pend.pop(0))
                for fu in pend:
                    collect(fu)
            except BaseException:
                for fu in pend:
                    fu.cancel()
                raise
            kk = sorted(keys)
            seq = temporal_smooth([keys[k] for k in kk])
            seq = area_hysteresis(seq, fps, step)
            for k, m in zip(kk, seq):
                keys[k] = m

            def finish(n, rgb):
                low = self._interp(keys, kk, n)
                if low is None or float(low.max()) < 0.02:
                    self._write(n, np.zeros((H, W), np.uint8))
                else:
                    q = refine(guided_upsample(low, rgb[y0:y1, x0:x1], r=2 if self.draft else 3, eps=2e-3))
                    full = np.zeros((H, W), np.uint8)
                    full[y0:y1, x0:x1] = np.clip(q * 255.0 + 0.5, 0, 255).astype(np.uint8)
                    self._write(n, full)
                tick()

            written = set()
            futs = []
            try:
                for n, rgb in _read_range(self.base_video, f0, f1, W, H, fps, None):
                    futs.append(finish_ex.submit(finish, n, rgb))
                    written.add(n)
                    if len(futs) > 24:  # limita memoria en vuelo
                        futs.pop(0).result()
                for fu in futs:
                    fu.result()
            except BaseException:
                for fu in futs:
                    fu.cancel()
                raise
            # cuadros pedidos mas alla del final del video: vacios (no se recalculan)
            z = None
            for n in range(f0, f1):
                if n not in written:
                    z = z if z is not None else np.zeros((H, W), np.uint8)
                    self._write(n, z)
                    tick()
            del keys
        rem = float(np.mean(removed)) if removed else 0.0
        return {"quality": round(max(0.0, 1.0 - rem), 3), "removed": round(rem, 3),
                "person": bool(use_person), "keys": plan["keys"], "face_keys": len(with_face),
                "engine": engine(), "dims": [int(iw), int(ih)], "crop": [int(v) for v in crop]}

    @staticmethod
    def _interp(keys: dict, ks: list[int], n: int) -> np.ndarray | None:
        """Mascara de baja resolucion del cuadro n interpolada entre sus cuadros clave."""
        if not ks:
            return None
        i = bisect.bisect_left(ks, n)
        if i < len(ks) and ks[i] == n:
            return keys.get(n)
        k0 = ks[i - 1] if i > 0 else None
        k1 = ks[i] if i < len(ks) else None
        if k0 is None:
            return keys.get(k1)
        if k1 is None:
            return keys.get(k0)
        t = (n - k0) / float(k1 - k0)
        return keys[k0] * (1.0 - t) + keys[k1] * t

    # -------------------------------------------------- lectura
    def _zeros(self) -> np.ndarray:
        if self._zero is None:
            self._zero = np.zeros((self.H, self.W), np.float32)
            self._zero.setflags(write=False)   # compartida: nadie la modifica en sitio
        return self._zero

    def get(self, n: int) -> np.ndarray:
        """Mascara float32 HxW [0,1] del cuadro n (ceros si no hay o fuera de los rangos
        pedidos). Un PNG ilegible o de otro tamano se borra (se recalcula en el proximo
        prepare) y se trata como vacio."""
        n = int(n)
        if not self.in_ranges(n):
            return self._zeros()
        m = self._lru.get(n)
        if m is not None:
            return m
        p = self._png(n)
        m = None
        if p.exists():
            u8 = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if u8 is not None and u8.shape == (self.H, self.W):
                m = u8.astype(np.float32) * (1.0 / 255.0)
            else:
                _log(f"mascara ilegible {p.name}: se descarta")
                try:
                    p.unlink()
                except OSError:
                    pass
        if m is None:
            return self._zeros()
        if len(self._lru) > 6:
            self._lru.pop(next(iter(self._lru)))
        self._lru[n] = m
        return m


# ---------------------------------------------------------------- efectos de sujeto
def _rgba(img) -> np.ndarray:
    return img if isinstance(img, np.ndarray) else C.image_to_rgba(img)


def _num(d, k: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
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


def _color(c, default="#FFFFFF"):
    try:
        C.rgb(c)
        return c
    except Exception:
        return default


def _env(item: dict, lt: float, dur: float, tin: float = 0.25, tout: float = 0.2) -> float:
    k = _num(item, "intensity", 1.0, 0.0, 1.0)
    pin, pout = ease.envelope(lt, dur, _num(item, "fade_in", tin, 0.0, 5.0), _num(item, "fade_out", tout, 0.0, 5.0))
    return k * ease.out_cubic(pin) * (1.0 - ease.in_out_quad(pout))


def blend(top: np.ndarray, bottom: np.ndarray, a: np.ndarray) -> np.ndarray:
    """top * a + bottom * (1 - a); RGB/RGBA uint8 y alfa float32 HxW (cv2.blendLinear)."""
    a = np.ascontiguousarray(a, dtype=np.float32)
    return cv2.blendLinear(top, bottom, a, 1.0 - a)


def composite(bottom, top, mask: np.ndarray) -> skia.Image:
    """top sobre bottom a traves de la mascara float HxW [0,1] (alfa real, sin halos)."""
    return C.image_from_rgba(blend(_rgba(top), _rgba(bottom), mask))


_SOLIDS: dict = {}


def solid(W: int, H: int, c, ch: int = 4) -> np.ndarray:
    """Cuadro de color solido (cacheado) para mezclar contornos, sombras y brillos."""
    key = (W, H, C.color(_color(c)), ch)
    a = _SOLIDS.get(key)
    if a is None:
        if len(_SOLIDS) > 8:
            _SOLIDS.clear()
        r, g, b = C.rgb(_color(c))
        a = np.empty((H, W, ch), np.uint8)
        a[...] = (r, g, b, 255)[:ch]
        _SOLIDS[key] = a
    return a


_SCALE_LUTS: dict = {}


def scale_lut(f: float) -> np.ndarray:
    """LUT RGBA que multiplica RGB por f (alfa identidad). Mas rapido que cv2.multiply."""
    k = round(float(f), 3)
    lut = _SCALE_LUTS.get(k)
    if lut is None:
        x = np.arange(256, dtype=np.float64)
        lut = np.empty((256, 1, 4), np.uint8)
        for c in range(3):
            lut[:, 0, c] = np.clip(np.round(x * k), 0, 255)
        lut[:, 0, 3] = np.arange(256)
        _SCALE_LUTS[k] = lut
    return lut


def _matte(ctx) -> np.ndarray | None:
    m = ctx.matte() if ctx is not None else None
    if m is None or float(m.max()) < 0.02:
        return None
    return m


def color_pop(img, item: dict, lt: float, dur: float, ctx) -> skia.Image:
    """Sujeto a color y fondo desaturado/oscurecido. Campos: bg_desat (0..1, 1.0),
    bg_dark (0..1, 0.25). El color se 'escurre' del fondo al entrar (0.3 s)."""
    item = item if isinstance(item, dict) else {}
    m = _matte(ctx)
    if m is None:
        return img
    e = _env(item, lt, dur, 0.3, 0.2)
    if e <= 0.002:
        return img
    arr = cv2.cvtColor(_rgba(img), cv2.COLOR_RGBA2RGB)
    desat = _num(item, "bg_desat", 1.0, 0.0, 1.0) * e
    dark = _num(item, "bg_dark", 0.25, 0.0, 1.0) * e
    g3 = cv2.cvtColor(cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
    # fondo: desaturar y oscurecer en una sola pasada
    bg = cv2.addWeighted(arr, (1.0 - desat) * (1.0 - dark), g3, desat * (1.0 - dark), 0)
    # el sujeto gana un poco de saturacion para despegar del fondo
    s = 1.0 + 0.10 * e
    subj = cv2.addWeighted(arr, s, g3, 1.0 - s, 0)
    return C.image_from_rgba(cv2.cvtColor(blend(subj, bg, m), cv2.COLOR_RGB2RGBA))


def _normalized_blur(arr: np.ndarray, m: np.ndarray, sigma: float, ds: int, gain: float = 1.0) -> np.ndarray:
    """Desenfoque del fondo sin que el sujeto 'sangre' en el (convolucion normalizada):
    blur(img * (1-m)) / blur(1-m), todo a resolucion reducida; sube en uint8."""
    H, W = arr.shape[:2]
    w, h = max(8, W // ds), max(8, H // ds)
    small = cv2.resize(arr, (w, h), interpolation=cv2.INTER_AREA)[..., :3].astype(np.float32)
    inv = np.clip(1.0 - cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA), 0.0, 1.0)
    # el borde del sujeto se excluye un poco mas (evita el halo del contorno)
    inv = cv2.erode(inv, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    sg = max(0.6, sigma / ds)
    num = cv2.GaussianBlur(small * inv[..., None], (0, 0), sg)
    den = cv2.GaussianBlur(inv, (0, 0), sg)
    plain = cv2.GaussianBlur(small, (0, 0), sg)
    wgt = np.clip(den / 0.2, 0.0, 1.0)[..., None]
    bg = (num / np.maximum(den, 1e-3)[..., None] * wgt + plain * (1.0 - wgt)) * gain
    out = np.empty((h, w, 4), np.uint8)
    out[..., :3] = np.clip(bg + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return cv2.resize(out, (W, H), interpolation=cv2.INTER_LINEAR)


def bg_blur(img, item: dict, lt: float, dur: float, ctx) -> skia.Image:
    """Modo retrato: fondo desenfocado (radius en px ref, 24) con 'rack focus' al entrar."""
    item = item if isinstance(item, dict) else {}
    m = _matte(ctx)
    if m is None:
        return img
    e = _env(item, lt, dur, 0.4, 0.25)
    if e <= 0.002:
        return img
    arr = _rgba(img)
    radius = ctx.px(_num(item, "radius", 24.0, 0.0, 200.0))
    sigma = 0.5 * radius * (0.4 + 0.6 * e)
    ds = 4 if (sigma >= 10 or getattr(ctx, "draft", False)) else 2
    bg = _normalized_blur(arr, m, sigma, ds, gain=1.0 - 0.08 * e)  # un toque mas oscuro: profundidad
    a = ease.smoothstep(min(1.0, e * 1.4))
    if a < 0.999:
        bg = cv2.addWeighted(arr, 1.0 - a, bg, a, 0)
    return C.image_from_rgba(blend(arr, bg, m))


def _own_background(canvas: skia.Canvas, colors: list, lt: float, ctx) -> None:
    """Fondo propio (si cutaways.draw_background no esta): degradado diagonal que respira,
    brillo radial detras de la cara y vinieta suave."""
    W, H = ctx.W, ctx.H
    pal = ctx.palette if hasattr(ctx, "palette") else {}
    cs = [c for c in (colors or []) if c and _color(c, None)] or [pal.get("dark", "#101014"),
                                                                    pal.get("accent2", "#2EC4B6")]
    if len(cs) == 1:
        cs = [cs[0], C.mix(cs[0], "#000000", 0.55)]
    c0, c1 = C.color(cs[0]), C.color(cs[1])
    ang = 0.35 + 0.08 * math.sin(lt * 0.6)
    dx, dy = math.cos(ang + math.pi / 2), math.sin(ang + math.pi / 2)
    p0 = skia.Point(W * (0.5 - 0.6 * dx), H * (0.5 - 0.6 * dy))
    p1 = skia.Point(W * (0.5 + 0.6 * dx), H * (0.5 + 0.6 * dy))
    canvas.drawPaint(skia.Paint(Shader=skia.GradientShader.MakeLinear([p0, p1], [c1, c0])))
    fx, fy = (0.5, 0.33)
    f = getattr(ctx, "face", None)
    if f is not None and len(f) >= 2 and all(np.isfinite(f[:2])):
        fx, fy = float(f[0]), float(f[1])
    glow = C.color(cs[2] if len(cs) > 2 else C.mix(cs[1], "#FFFFFF", 0.35), 0.55)
    r = W * (0.75 + 0.05 * math.sin(lt * 1.3))
    canvas.drawPaint(skia.Paint(Shader=skia.GradientShader.MakeRadial(
        skia.Point(fx * W, (fy + 0.08) * H), r, [glow, C.color("#000000", 0.0)])))
    canvas.drawPaint(skia.Paint(Shader=skia.GradientShader.MakeRadial(
        skia.Point(W * 0.5, H * 0.48), max(W, H) * 0.75,
        [C.color("#000000", 0.0), C.color("#000000", 0.0), C.color("#000000", 0.45)], [0.0, 0.55, 1.0])))


def background_rgba(item: dict, lt: float, ctx, channels: int = 4) -> np.ndarray:
    """Fondo de reemplazo como RGBA uint8: cutaways.draw_background si existe (perezoso),
    si no el degradado propio. Lleva el mismo grano que el grade (item.grain, default el del
    plan 0.025; tambien quita las bandas al codificar) y vinieta (item.vignette, 0.22), para
    que no se vea 'pegado' junto al video graduado."""
    item = item if isinstance(item, dict) else {}
    surf = C.offscreen(ctx.W, ctx.H)
    cv = surf.getCanvas()
    cv.clear(C.color("#000000"))
    kind = item.get("background", "gradient")
    colors = [c for c in (item.get("bg_colors") or []) if _color(c, None)] \
        if isinstance(item.get("bg_colors"), (list, tuple)) else []
    drawn = False
    try:
        from . import cutaways  # modulo de otro dueno
        fn = getattr(cutaways, "draw_background", None)
        if fn is not None:
            fn(cv, kind if isinstance(kind, str) else "gradient", colors, lt, ctx)
            drawn = True
    except Exception:
        drawn = False
    if not drawn:
        cv.clear(C.color("#000000"))
        _own_background(cv, colors, lt, ctx)
    arr = C.image_to_rgba(surf.makeImageSnapshot())
    if channels == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2RGB)
    from . import grade
    arr = grade.add_grain(arr, _num(item, "grain", 0.025, 0.0, 0.3) * 0.85 + 0.004,
                          int(getattr(ctx, "n", 0) or 0), int(getattr(ctx, "seed", 0) or 0))
    vig = _num(item, "vignette", 0.22, 0.0, 1.0)
    if vig > 0.005:
        v = _vig_rgba(ctx.W, ctx.H, round(vig, 2))
        arr = cv2.multiply(arr, v if channels == 4 else _VIGS[(ctx.W, ctx.H, round(vig, 2), "rgb")],
                           scale=1.0 / 255.0)
    return arr


_VIGS: dict = {}


def _vig_rgba(W: int, H: int, amount: float) -> np.ndarray:
    key = (W, H, amount)
    v = _VIGS.get(key)
    if v is None:
        from . import grade
        if len(_VIGS) > 4:
            _VIGS.clear()
        v = _VIGS[key] = grade.vignette_map(W, H, amount)
        _VIGS[key + ("rgb",)] = np.ascontiguousarray(v[..., :3])
    return v


def _shadow_map(m: np.ndarray, ctx, ds: int = 4, shade: bool = False) -> np.ndarray:
    """Sombra del sujeto sobre el fondo nuevo (float32 HxW, 0..~0.5), a baja resolucion.
    Cuerpo completo con los pies visibles: sombra de piso eliptica bajo los pies + contacto.
    Cuerpo cortado por el borde o por un mueble (escritorio): sombra proyectada suave
    desplazada (profundidad), nunca una sombra de piso flotando.
    shade=True: devuelve directamente el multiplicador uint8 (255 = sin sombra) a resolucion
    completa, convertido a baja resolucion (barato)."""
    H, W = m.shape
    sw, sh = max(8, W // ds), max(8, H // ds)
    sm = cv2.resize(m, (sw, sh), interpolation=cv2.INTER_AREA)
    rows = np.nonzero(sm.max(axis=1) > 0.5)[0]
    out = np.zeros((sh, sw), np.float32)
    if len(rows) == 0:
        return np.full((H, W), 255, np.uint8) if shade else np.zeros((H, W), np.float32)
    bottom = int(rows[-1])
    full_body = False
    f = getattr(ctx, "face", None)
    if f is not None and len(f) >= 4 and all(np.isfinite(f[:4])):
        # cuerpo completo: los pies quedan a mas de ~6 alturas de caja de cara bajo la cara
        full_body = (bottom / sh - float(f[1])) > 6.0 * float(f[3])
    if bottom < sh * 0.965 and full_body:
        # pies: ancho del sujeto en su 4% inferior
        band = sm[max(0, bottom - max(2, sh // 25)):bottom + 1]
        cols = np.nonzero(band.max(axis=0) > 0.5)[0]
        if len(cols):
            x0, x1 = int(cols[0]), int(cols[-1])
            cx = (x0 + x1) / 2.0
            ax = max(sw * 0.08, (x1 - x0) * 0.75 + ctx.px(20) / ds)
            ay = max(2.0, sh * 0.014)
            cv2.ellipse(out, (int(round(cx)), int(round(bottom + ay * 0.3))), (int(ax), int(ay)), 0, 0, 360,
                        0.55, -1, cv2.LINE_AA)
            out = cv2.GaussianBlur(out, (0, 0), max(1.0, ctx.px(18) / ds))
        contact = cv2.GaussianBlur(sm, (0, 0), max(0.8, ctx.px(6) / ds))
        out = np.maximum(out, contact * 0.35)
    else:
        dx, dy = int(round(ctx.px(22) / ds)), int(round(ctx.px(30) / ds))
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        sh_ = cv2.warpAffine(sm, M, (sw, sh), borderMode=cv2.BORDER_CONSTANT)
        out = cv2.GaussianBlur(sh_, (0, 0), max(1.0, ctx.px(34) / ds)) * 0.42
    if shade:
        return cv2.resize(cv2.convertScaleAbs(1.0 - out, alpha=255.0), (W, H), interpolation=cv2.INTER_LINEAR)
    return cv2.resize(out, (W, H), interpolation=cv2.INTER_LINEAR)


def _light_wrap(arr3: np.ndarray, bg3: np.ndarray, m: np.ndarray, ctx, amount: float = 0.4,
                tint: float = 0.07) -> np.ndarray:
    """Integra el sujeto al fondo nuevo: la luz del fondo 'envuelve' el borde interior del
    sujeto (light wrap) y un tinte leve de su color le da la misma luz ambiente. El color del
    fondo cercano se calcula a 1/8 (convolucion normalizada, sin el sujeto) y la franja del
    borde a 1/4 de resolucion: una sola mezcla a resolucion completa."""
    H, W = m.shape
    w8, h8 = max(8, W // 8), max(8, H // 8)
    m8 = cv2.resize(m, (w8, h8), interpolation=cv2.INTER_AREA)
    b8 = cv2.resize(bg3, (w8, h8), interpolation=cv2.INTER_AREA).astype(np.float32)
    inv = 1.0 - m8
    sg = max(1.0, ctx.px(28) / 8)
    num = cv2.GaussianBlur(b8 * inv[..., None], (0, 0), sg)
    den = cv2.GaussianBlur(inv, (0, 0), sg)
    mean = b8.reshape(-1, 3).mean(axis=0)
    wgt = np.clip(den / 0.15, 0.0, 1.0)[..., None]
    wrap_col = num / np.maximum(den, 1e-3)[..., None] * wgt + mean * (1.0 - wgt)
    w4, h4 = max(8, W // 4), max(8, H // 4)
    ms = cv2.resize(m, (w4, h4), interpolation=cv2.INTER_AREA)
    edge = ms * (1.0 - cv2.GaussianBlur(ms, (0, 0), max(0.8, ctx.px(12) / 4)))
    alpha = np.clip(edge * 2.0 * amount + ms * tint, 0.0, 0.6)
    col = cv2.resize(np.clip(wrap_col, 0, 255).astype(np.uint8), (W, H), interpolation=cv2.INTER_LINEAR)
    a = cv2.resize(alpha, (W, H), interpolation=cv2.INTER_LINEAR)
    return blend(col, arr3, a)


def _reveal_mask(q: float, ctx, feather_ref: float = 110.0, ds: int = 4) -> np.ndarray | None:
    """Mascara de revelado circular desde la cara (1 = fondo nuevo visible). q en [0, 1]."""
    if q >= 0.999:
        return None
    W, H = int(ctx.W), int(ctx.H)
    fx, fy = 0.5, 0.35
    f = getattr(ctx, "face", None)
    if f is not None and len(f) >= 2 and all(np.isfinite(f[:2])):
        fx, fy = float(f[0]), float(f[1])
    sw, sh = max(8, W // ds), max(8, H // ds)
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    d = np.hypot(xx - fx * sw, yy - fy * sh)
    feather = ctx.px(feather_ref) / ds
    rmax = float(d.max()) + feather
    r = q * rmax
    a = np.clip((r - d) / feather + 0.5, 0.0, 1.0)
    a = a * a * (3 - 2 * a)
    return cv2.resize(a, (W, H), interpolation=cv2.INTER_LINEAR)


def bg_replace(img, item: dict, lt: float, dur: float, ctx) -> skia.Image:
    """Reemplazo de fondo: background (como cutaways; usa cutaways.draw_background si
    existe, si no un degradado propio; 'blur_self' = el propio cuadro muy difuso), bg_colors.
    Integracion: light wrap + tinte ambiente en el borde, sombra (de piso bajo los pies o
    proyectada), grano y vinieta en el fondo. Entra con un revelado circular desde la cara
    (fade_in, 0.45 s) y sale cerrandolo (fade_out, 0.3 s)."""
    item = item if isinstance(item, dict) else {}
    m = _matte(ctx)
    if m is None:
        return img
    k = _num(item, "intensity", 1.0, 0.0, 1.0)
    pin, pout = ease.envelope(lt, dur, _num(item, "fade_in", 0.45, 0.0, 5.0),
                              _num(item, "fade_out", 0.3, 0.0, 5.0))
    q = ease.out_cubic(pin) * (1.0 - ease.in_out_cubic(pout))
    if q <= 0.002 or k <= 0.002:
        return img
    arr4 = _rgba(img)
    arr = cv2.cvtColor(arr4, cv2.COLOR_RGBA2RGB)
    H, W = arr.shape[:2]
    if item.get("background") == "blur_self":
        # el propio cuadro muy difuso y oscurecido (ctx.base_image aun no existe en esta etapa)
        bg = cv2.cvtColor(_normalized_blur(arr4, m, ctx.px(60.0), 4, gain=0.7), cv2.COLOR_RGBA2RGB)
    else:
        bg = background_rgba(item, lt, ctx, channels=3)
    shade = _shadow_map(m, ctx, shade=True)
    bg = cv2.multiply(bg, cv2.cvtColor(shade, cv2.COLOR_GRAY2RGB), scale=1.0 / 255.0)
    subj = _light_wrap(arr, bg, m, ctx, amount=0.4 * k)
    out = blend(subj, bg, m)
    rv = _reveal_mask(q * (0.999 if k < 0.999 else 1.0), ctx)
    if k < 0.999:
        out = cv2.addWeighted(arr, 1.0 - k, out, k, 0)
    if rv is not None:
        out = blend(out, arr, rv)
    return C.image_from_rgba(cv2.cvtColor(out, cv2.COLOR_RGB2RGBA))


def subject_outline(img, item: dict, lt: float, dur: float, ctx) -> skia.Image:
    """Contorno tipo sticker alrededor de la persona: color (#FFFFFF), width (px ref, 8),
    glow (0..1, 0.5). El borde crece con un resorte al entrar y se encoge al salir. Sombra,
    brillo y contorno se componen a baja resolucion y se mezclan en una sola pasada."""
    item = item if isinstance(item, dict) else {}
    m = _matte(ctx)
    if m is None:
        return img
    k = _num(item, "intensity", 1.0, 0.0, 1.0)
    grow = ease.spring(lt, 3.6, 0.55)
    tout = min(0.2, dur * 0.3)
    if lt > dur - tout:
        grow *= 1.0 - ease.in_cubic((lt - (dur - tout)) / max(1e-3, tout))
    grow = max(0.0, grow) * k
    if grow <= 0.01:
        return img
    arr = cv2.cvtColor(_rgba(img), cv2.COLOR_RGBA2RGB)
    H, W = m.shape
    ds = 4 if getattr(ctx, "draft", False) else 2
    col = _color(item.get("color", "#FFFFFF"))
    wd = ctx.px(_num(item, "width", 8.0, 0.0, 80.0)) * grow
    mh = cv2.resize(m, (W // ds, H // ds), interpolation=cv2.INTER_AREA)
    inside = (mh > 0.5).astype(np.uint8)
    dist = cv2.distanceTransform(1 - inside, cv2.DIST_L2, 5)
    rad = wd / ds
    ring = np.clip((rad + 0.75 - dist) * (1.0 / 1.5), 0.0, 1.0).astype(np.float32)
    glow = _num(item, "glow", 0.5, 0.0, 1.0) * grow
    # sombra negra (volumen) debajo; brillo del color + contorno encima. Los alfas se arman a
    # media resolucion (fuera del sujeto) y hay dos pasadas a resolucion completa: oscurecer
    # con la sombra y mezclar el color. El interior del sujeto no se toca.
    hw, hh = W // ds, H // ds
    rq = cv2.resize(ring, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    shq = cv2.GaussianBlur(rq, (0, 0), max(0.8, ctx.px(7) / 4))
    dy = max(1, int(round(ctx.px(6) / 4)))
    shq = np.vstack([np.zeros((dy, shq.shape[1]), np.float32), shq[:-dy]]) * (0.36 * grow)
    outside = 1.0 - mh
    a_col = ring
    if glow > 0.01:
        glq = cv2.GaussianBlur(rq, (0, 0), max(1.5, (rad * ds) * 2.2 / 4)) * min(0.85, glow * 1.1)
        gl = cv2.resize(glq, (hw, hh), interpolation=cv2.INTER_LINEAR) if ds != 4 else glq
        a_col = ring + gl * (1.0 - ring)
    sh = cv2.resize(shq, (hw, hh), interpolation=cv2.INTER_LINEAR) if ds != 4 else shq
    shade = cv2.convertScaleAbs(1.0 - sh * outside, alpha=255.0)
    shade = cv2.resize(shade, (W, H), interpolation=cv2.INTER_LINEAR)
    darker = cv2.multiply(arr, cv2.cvtColor(shade, cv2.COLOR_GRAY2RGB), scale=1.0 / 255.0)
    a_full = cv2.resize(np.clip(a_col, 0.0, 1.0) * outside, (W, H), interpolation=cv2.INTER_LINEAR)
    out = blend(solid(W, H, col, 3), darker, a_full)
    return C.image_from_rgba(cv2.cvtColor(out, cv2.COLOR_RGB2RGBA))


RENDERERS = {"color_pop": color_pop, "bg_blur": bg_blur, "bg_replace": bg_replace,
             "subject_outline": subject_outline}
