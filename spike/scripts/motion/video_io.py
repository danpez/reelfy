"""E/S de video del motor: probe (rotacion/HDR), base pass, pipes de cuadros, encoders,
concat y mux.

Decisiones clave (docs/MOTION-ENGINE.md, seccion 1):
  - probe() reporta dimensiones de DISPLAY (un MOV de iPhone 3840x2160 con rotation -90
    es 2160x3840) y el tipo de HDR (hlg/pq) para tonemapear bien.
  - base_pass() produce UNA vez por (fuente, cortes, formato, fps) el video base ya
    reencuadrado + cortado + tonemapeado (intermedio de alta calidad, GOP corto para buscar
    rapido) y la voz de estudio cortada con microfundidos; se cachea por hash.
  - Los cortes se aplican por CUADRO sobre la reticula de salida (fps con start_time=0: t =
    k/fps, con medio cuadro de margen) y la voz por MUESTRA con los mismos limites cuantizados
    del Timeline: video y audio quedan en sincronia exacta. Cientos de keeps (video largo) se
    reparten en varios select encadenados por tramos (select_filters): av_expr no acepta una
    suma de mas de ~100 terminos.
  - Color: todo intermedio va etiquetado bt709/tv (primaries, transfer, matriz y rango: con
    setparams, porque el filtro scale fija las propiedades del cuadro y el encoder ignora
    -color_primaries/-color_trc) y las conversiones RGB<->YUV usan bt709 explicito. Fuentes
    SDR bt601 o de rango completo se convierten (no solo se re-etiquetan).
  - Concurrencia: el base y la voz se generan bajo un lock por clave (flock) con temporales
    unicos: ensure_base en segundo plano + export simultaneo nunca corrompen la cache.
  - Encoders: h264_videotoolbox en Mac (hardware), libx264 fuera. Nunca -hwaccel
    videotoolbox fuera de Mac; en Mac el base pass decodifica H.264 por software (mas rapido
    que VideoToolbox y mismos cuadros: SW_DECODE) y el resto con VideoToolbox.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np

import paths

FFMPEG = str(paths.FFMPEG)
FFPROBE = str(paths.FFPROBE)
IS_MAC = paths.IS_MAC

BASE_VERSION = "b4"          # cambia si cambia el algoritmo del base pass (invalida cache)
VOICE_VERSION = "v5"         # v5: sin NaN (deesser tras el relleno de arnndn); v4: fundido de salida
SR = 48000                   # voz / mezcla
XFADE_MS = 8.0               # microfundido en cada union de la voz (5-10 ms)
END_FADE_MS = 25.0           # fundido de salida de la voz si el ultimo keep corta la fuente
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")
COLOR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
              "-color_range", "tv"]
# propiedades de color del cuadro (el encoder las copia al VUI del stream; sin esto el mp4
# sale con primaries/transfer 'unknown' y ColorSync puede aplicar otra gamma)
SETPARAMS_709 = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
FACE_Y_VERSION = "fy1"
HEADROOM_Y = 0.40            # recorte vertical: centro de la cara a este alto de la salida
PILLAR_RATIO = 2.0           # salida mucho mas ancha que la fuente -> inset con fondo desenfocado


class Canceled(Exception):
    """El usuario cancelo el render."""


def _check(cancel: Callable[[], bool] | None) -> None:
    if cancel and cancel():
        raise Canceled("render cancelado")


# ================================================================ probe
def _ratio(s: str | None) -> float:
    try:
        a, b = str(s).split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, ZeroDivisionError):
        try:
            return float(s)
        except (TypeError, ValueError):
            return 0.0


def _rotation(stream: dict) -> int:
    """Rotacion de display en grados normalizada a {0, 90, 180, 270}."""
    rot = None
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            rot = sd["rotation"]
            break
    if rot is None:
        rot = (stream.get("tags") or {}).get("rotate")
    try:
        r = int(round(float(rot or 0))) % 360
    except (TypeError, ValueError):
        return 0
    return min((0, 90, 180, 270, 360), key=lambda v: abs(v - r)) % 360


_IMAGE_CODECS = {"png", "mjpeg", "jpeg2000", "webp", "bmp", "tiff", "gif", "heif", "jpegls"}


@lru_cache(maxsize=64)
def _probe_cached(path: str, size: int, mtime: int) -> dict:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json",
                        path], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        d = {}
    fmt = d.get("format") or {}
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    try:
        dur = float(fmt.get("duration") or (v or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    info = {"path": path, "w": 0, "h": 0, "coded_w": 0, "coded_h": 0, "rotation": 0,
            "fps": 0.0, "fps_nominal": 0.0, "duration": round(dur, 6), "codec": None,
            "pix_fmt": None, "bit_depth": 8, "hdr": None, "color_transfer": None,
            "color_primaries": None, "color_space": None, "color_range": None, "dovi": False,
            "has_audio": bool(auds), "audio_index": None, "audio_rate": 0, "audio_channels": 0,
            "nb_frames": 0, "type": "video" if dur > 0 else "image"}
    if v:
        cw, ch = int(v.get("width") or 0), int(v.get("height") or 0)
        rot = _rotation(v)
        w, h = (ch, cw) if rot in (90, 270) else (cw, ch)
        pf = v.get("pix_fmt") or ""
        trc = v.get("color_transfer")
        info.update(w=w, h=h, coded_w=cw, coded_h=ch, rotation=rot,
                    fps=_ratio(v.get("avg_frame_rate")) or _ratio(v.get("r_frame_rate")),
                    fps_nominal=_ratio(v.get("r_frame_rate")) or _ratio(v.get("avg_frame_rate")),
                    codec=v.get("codec_name"), pix_fmt=pf,
                    bit_depth=12 if "12" in pf else 10 if "10" in pf else 8,
                    hdr={"arib-std-b67": "hlg", "smpte2084": "pq"}.get(trc),
                    color_transfer=trc, color_primaries=v.get("color_primaries"),
                    color_space=v.get("color_space"), color_range=v.get("color_range"),
                    dovi=any("DOVI" in (sd.get("side_data_type") or "")
                             for sd in v.get("side_data_list") or []),
                    nb_frames=int(v.get("nb_frames") or 0))
        if v.get("codec_name") in _IMAGE_CODECS and (dur <= 0 or info["nb_frames"] <= 1):
            info["type"] = "image"
    if auds:
        a = auds[0]      # 0:a:0 (en iPhone la 2a pista suele ser APAC espacial: no decodificable)
        info.update(audio_index=0, audio_rate=int(a.get("sample_rate") or 0),
                    audio_channels=int(a.get("channels") or 0))
    return info


def probe(path) -> dict:
    """Metadatos con dimensiones de DISPLAY (rotacion aplicada) y HDR.

    Claves: w, h (display), coded_w, coded_h, rotation (0/90/180/270), fps (promedio),
    fps_nominal, duration, codec, pix_fmt, bit_depth, hdr (None|'hlg'|'pq'), dovi,
    has_audio, audio_index (indice entre pistas de audio), audio_rate, audio_channels,
    nb_frames, type ('video'|'image').
    """
    p = Path(path)
    try:
        st = p.stat()
        return dict(_probe_cached(str(p), st.st_size, int(st.st_mtime_ns)))
    except FileNotFoundError:
        return dict(_probe_cached(str(p), -1, -1))


_MATRIX = {"bt709": "bt709", "smpte170m": "bt601", "bt470bg": "bt601", "bt601": "bt601",
           "fcc": "fcc", "smpte240m": "smpte240m", "bt2020nc": "bt2020", "bt2020c": "bt2020"}


def source_matrix(info: dict) -> tuple[str, str]:
    """(matriz, rango) de una fuente SDR para convertir a bt709/tv.

    Sin etiqueta se usa la heuristica de los reproductores: HD (lado largo >= 1280 o corto
    > 576) = bt709, SD = bt601. Rango completo si color_range=pc o pix_fmt yuvj*."""
    cs = str(info.get("color_space") or "").lower()
    m = _MATRIX.get(cs)
    if m is None:
        w, h = int(info.get("w") or 0), int(info.get("h") or 0)
        m = "bt709" if (max(w, h) >= 1280 or min(w, h) > 576 or not w) else "bt601"
    pf = str(info.get("pix_fmt") or "")
    rng = "pc" if (info.get("color_range") in ("pc", "jpeg") or pf.startswith("yuvj")) else "tv"
    return m, rng


def tonemap_filters(info: dict) -> list[str]:
    """Normalizacion de color a SDR bt709/tv 8 bits (al final de la cadena).

    HDR (HLG/PQ): zscale lineal + tonemap hable ANTES de bajar a 8 bits. SDR bt709/tv: solo
    8 bits. SDR bt601 o rango completo (Android, grabaciones de pantalla): conversion real de
    matriz y rango. Siempre termina en setparams bt709 (etiqueta coherente con los pixeles)."""
    if info.get("hdr"):
        return [TONEMAP, "format=yuv420p", SETPARAMS_709]
    m, rng = source_matrix(info)
    if m == "bt709" and rng == "tv":
        return ["format=yuv420p", SETPARAMS_709]
    return [f"scale=in_color_matrix={m}:in_range={rng}:out_color_matrix=bt709:out_range=tv:"
            "flags=bicubic+accurate_rnd+full_chroma_int", "format=yuv420p", SETPARAMS_709]


# ================================================================ encoders
@lru_cache(maxsize=1)
def videotoolbox_ok() -> bool:
    """True si h264_videotoolbox codifica en esta maquina (solo se intenta en Mac)."""
    if not IS_MAC:
        return False
    try:
        r = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=s=256x256:r=30:d=0.3", "-c:v", "h264_videotoolbox",
                            "-b:v", "2M", "-f", "null", "-"], capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


# Calidad del video ENTREGADO (kind "final"): bits por pixel (VideoToolbox) y CRF (libx264). El
# export elige Estandar / Alta / Maxima y el servidor fija estas variables mientras dura el render
# (los procesos del compositor, spawn, las heredan); sin ellas = Alta, lo de siempre.
FINAL_BPP, FINAL_CRF = 0.22, 18


def final_bpp() -> float:
    try:
        v = float(os.environ.get("REELFY_FINAL_BPP") or FINAL_BPP)
    except ValueError:
        return FINAL_BPP
    return min(1.0, max(0.05, v)) if math.isfinite(v) else FINAL_BPP


def final_crf() -> int:
    try:
        v = int(os.environ.get("REELFY_FINAL_CRF") or FINAL_CRF)
    except ValueError:
        return FINAL_CRF
    return min(30, max(10, v))


def encoder_args(W: int, H: int, fps: float, kind: str = "final", gop: int | None = None,
                 force_sw: bool = False) -> list[str]:
    """Argumentos de encoder por plataforma.

    kind: 'inter' (intermedio casi sin perdida, GOP corto), 'final' (entrega: ~13.7 Mbps a
    1080p30, lo que TikTok/Reels re-codifican sin perder nada visible), 'draft'.
    """
    fps = float(fps)
    gop = int(gop or (max(1, int(round(fps / 2))) if kind == "inter" else int(round(fps * 2))))
    bpp = {"inter": 0.5, "final": final_bpp(), "draft": 0.15}.get(kind, 0.22)
    br = int(min(160e6, max(4e6 if kind != "draft" else 1.5e6, W * H * fps * bpp)))
    level = h264_level(W, H, fps) if kind != "inter" else None
    lv = ["-level:v", level] if level else []
    if not force_sw and videotoolbox_ok() and max(W, H) <= 4096:
        return ["-c:v", "h264_videotoolbox", "-b:v", str(br), "-maxrate", str(int(br * 1.5)),
                "-bufsize", str(int(br * 2)), "-profile:v", "high"] + lv + [
                "-g", str(gop), "-pix_fmt", "yuv420p"] + COLOR_TAGS
    crf = {"inter": 12, "final": final_crf(), "draft": 24}.get(kind, 18)
    preset = {"inter": "veryfast", "final": "faster", "draft": "ultrafast"}.get(kind, "faster")
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-g", str(gop),
            "-bf", "0" if kind == "inter" else "2"] + lv + ["-pix_fmt", "yuv420p"] + COLOR_TAGS


def h264_level(W: int, H: int, fps: float) -> str | None:
    """Nivel H.264 minimo para WxH@fps (4.1 = 1080p30, 4.2 = 1080p60; 5.1/5.2 arriba)."""
    mbs = math.ceil(W / 16) * math.ceil(H / 16)
    rate = mbs * float(fps)
    for lv, max_fs, max_rate in (("4.1", 8192, 245760), ("4.2", 8704, 522240),
                                 ("5.1", 36864, 983040), ("5.2", 36864, 2073600)):
        if mbs <= max_fs and rate <= max_rate:
            return lv
    return None


# codecs que decodifican MAS RAPIDO por software que con VideoToolbox (Apple Silicon). H.264 4K30
# real (19 min, base pass con recorte+escala): 90 s de fuente en 11.4 s por software vs 29.8 s con
# VideoToolbox, con cuadros identicos bit a bit (framemd5). HEVC/ProRes siguen en VideoToolbox
# (ahi el software es ~2.5x mas lento). Misma politica que el escaneo del director (SW_FIRST).
SW_DECODE = ("h264",)


def hwaccel_args(info: dict | None = None) -> list[str]:
    """Decodificacion por hardware SOLO en Mac. Con `info` (probe de la fuente) de un codec de
    SW_DECODE -> [] (software: mas rapido y mismos cuadros)."""
    if not IS_MAC:
        return []
    if info and str(info.get("codec") or "").lower() in SW_DECODE:
        return []
    return ["-hwaccel", "videotoolbox"]


def hq_bitrate(W: int, H: int, fps: float) -> str:
    """Bitrate de intermedio 'visualmente sin perdida' para h264 (compose)."""
    return str(int(min(160e6, max(10e6, W * H * float(fps) * 0.3))))


# ================================================================ ejecucion de ffmpeg
def run_ffmpeg(cmd: list[str], total_s: float | None = None,
               on_pct: Callable[[float], None] | None = None,
               cancel: Callable[[], bool] | None = None) -> None:
    """Corre ffmpeg con progreso real (out_time) y cancelacion. Mata el proceso si se
    cancela o si on_pct lanza. Errores con la cola de stderr."""
    full = [cmd[0], "-hide_banner", "-nostdin", "-progress", "pipe:1", "-nostats"] + cmd[1:]
    with tempfile.TemporaryFile() as err:
        p = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=err, text=True, bufsize=1)
        try:
            for line in p.stdout:
                _check(cancel)
                if on_pct and total_s and line.startswith("out_time_us="):
                    try:
                        on_pct(min(100.0, int(line.split("=")[1]) / 1e6 / total_s * 100))
                    except ValueError:
                        continue
            p.wait()
        except BaseException:
            p.kill()
            p.wait()
            raise
        if p.returncode != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace").strip().splitlines()[-6:]
            raise RuntimeError("ffmpeg fallo: " + " | ".join(tail))


def run_ffmpeg_fallback(cmd: list[str], **kw) -> None:
    """run_ffmpeg; si falla con videotoolbox reintenta con libx264 (p. ej. lado > 4096)."""
    try:
        run_ffmpeg(cmd, **kw)
    except RuntimeError:
        if "h264_videotoolbox" not in cmd:
            raise
        run_ffmpeg(paths._to_libx264(cmd), **kw)


# ================================================================ base pass
def _file_sig(path) -> str:
    p = Path(path)
    try:
        st = p.stat()
        return f"{p.resolve()}|{st.st_size}|{int(st.st_mtime_ns)}"
    except FileNotFoundError:
        return f"{p}|missing"


def base_key(plan: dict, W: int, H: int, fps: float, timeline, air: float | None = None) -> str:
    """Hash del video base: fuente + cortes (cuantizados) + formato + fps + camara."""
    cam = plan.get("camera") or []
    h = hashlib.sha1()
    h.update(json.dumps([BASE_VERSION, FACE_Y_VERSION, _file_sig(plan["video"]), int(W), int(H),
                         float(fps), timeline.frame_ranges(fps), round(float(air or 0.0), 4),
                         hashlib.sha1(json.dumps(cam).encode()).hexdigest()]).encode())
    return h.hexdigest()[:16]


def voice_key(plan: dict, fps: float, timeline, enhance_audio: bool) -> str:
    h = hashlib.sha1()
    h.update(json.dumps([VOICE_VERSION, _file_sig(plan["video"]), float(fps),
                         timeline.frame_ranges(fps), bool(enhance_audio), XFADE_MS,
                         _studio_chain() if enhance_audio else ""]).encode())
    return h.hexdigest()[:16]


def base_paths(cache_dir, plan: dict, W: int, H: int, fps: float, timeline,
               enhance_audio: bool = True, air: float | None = None) -> tuple[Path, Path]:
    cache_dir = Path(cache_dir)
    return (cache_dir / f"base_{base_key(plan, W, H, fps, timeline, air)}.mp4",
            cache_dir / f"voice_{voice_key(plan, fps, timeline, enhance_audio)}.wav")


def _studio_chain() -> str:
    try:
        import pipeline
        return pipeline.AUDIO_STUDIO
    except Exception:  # pragma: no cover - sin pipeline: cadena equivalente
        return (f"highpass=f=80,arnndn=m={paths.RNNOISE}:mix=0.85,deesser,"
                "acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=2,"
                "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000")


def touch(path) -> None:
    """Marca un intermedio de cache como usado (la poda es LRU por mtime).

    OJO: nunca tocar el .mp4 del base: su mtime entra en las claves de cache de matte,
    grade y rostro. Para bases/voces se toca su .json de metadatos."""
    try:
        os.utime(path, None)
    except OSError:
        pass


def _tmp_path(out: Path) -> Path:
    """Temporal UNICO junto al destino (dos procesos/hilos nunca escriben el mismo)."""
    out = Path(out)
    return out.with_name(f"{out.stem}.{os.getpid()}.{uuid.uuid4().hex[:6]}.part{out.suffix}")


class FileLock:
    """Lock exclusivo por archivo entre procesos E hilos (flock sobre `<path>.lock`).

    Espera sondeando para poder cancelar (cancel() -> Canceled). En plataformas sin fcntl
    usa msvcrt; si tampoco existe, solo excluye hilos del mismo proceso."""

    _local: dict[str, threading.Lock] = {}
    _guard = threading.Lock()

    def __init__(self, path, cancel: Callable[[], bool] | None = None, poll: float = 0.05):
        self.path = Path(str(path) + ".lock")
        self.cancel, self.poll = cancel, poll
        self.fd = None
        self.waited = 0.0

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows
            fcntl = None
        if fcntl is not None:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
            while True:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, PermissionError):
                    self._wait()
        else:  # pragma: no cover
            with FileLock._guard:
                lk = FileLock._local.setdefault(str(self.path), threading.Lock())
            while not lk.acquire(timeout=self.poll):
                _check(self.cancel)
            self._tl = lk
        self.waited = time.time() - t0
        return self

    def _wait(self):
        try:
            _check(self.cancel)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        time.sleep(self.poll)

    def __exit__(self, *a):
        if self.fd is not None:
            import fcntl
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
        elif getattr(self, "_tl", None) is not None:  # pragma: no cover
            self._tl.release()


# ---------------------------------------------------------------- seleccion de cortes (select/aselect)
# av_expr (el evaluador de expresiones de ffmpeg) rechaza arboles de mas de ~100 niveles: la suma
# 'between(..)+between(..)+...' anida un nivel por termino y con 101 terminos ya no parsea ("Error
# while parsing expression ... Cannot allocate memory"). Un video largo (o las pausas entre palabras)
# da cientos de keeps y el select unico reventaba el export. Medido con ffmpeg 9: 100 terminos OK,
# 101 fallan; if(tramo, 99 terminos, 1) OK; una suma agrupada entre parentesis cuenta la profundidad.
SELECT_MAX_TERMS = 60        # terminos between() por filtro select/aselect (margen bajo el limite)
SUM_LEAF = 40                # terminos por hoja de sum_expr (arbol balanceado)


def _merge_closed(intervals, digits: int) -> list[tuple[float, float]]:
    """Intervalos cerrados [a, b] redondeados a `digits`, ordenados y fundidos si se tocan."""
    out: list[list[float]] = []
    for a, b in sorted((round(float(a), digits), round(float(b), digits)) for a, b in intervals):
        if b < a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def select_chain(intervals, filt: str = "select", max_terms: int = SELECT_MAX_TERMS,
                 digits: int = 6) -> list[str]:
    """Filtros select/aselect ENCADENADOS que conservan los cuadros con t dentro de algun intervalo
    cerrado [a, b] (segundos), para CUALQUIER numero de intervalos.

    - Hasta `max_terms` intervalos: UN filtro 'between(t,a,b)+...' (lo de siempre, mismo costo).
    - Con mas: un filtro por grupo de intervalos consecutivos (<= max_terms cada uno). Cada filtro
      responde solo de su TRAMO [c_i, c_i+1) (c_i = inicio de su primer intervalo; el primero desde
      -inf y el ultimo hasta +inf: los tramos cubren toda la linea sin huecos ni solapes) y deja
      pasar intacto lo de fuera: if(tramo, between(t,..)+..., 1). Un cuadro sobrevive solo si el
      filtro de su tramo lo conserva: lo previo al primer keep y la cola tras el ultimo se descartan.
    - Se usa t y no n: los select encadenados preservan los PTS, pero n se renumera despues del
      primero. Un solo recorrido del stream y sin memoria extra (cada filtro decide cuadro a cuadro).
    Los limites se redondean a `digits` decimales ANTES de fundir y partir (tramos y terminos usan
    los mismos numeros). Sin intervalos -> descarta todo."""
    iv = _merge_closed(intervals, digits)
    if not iv:
        return [f"{filt}=0"]

    def num(x: float) -> str:
        return f"{x:.{digits}f}"

    terms = [f"between(t,{num(a)},{num(b)})" for a, b in iv]
    if len(terms) <= max(1, int(max_terms)):
        return [f"{filt}='{'+'.join(terms)}'"]
    k = -(-len(terms) // max(1, int(max_terms)))
    edges = [len(terms) * i // k for i in range(k + 1)]          # grupos parejos (211 -> 53,53,52,53)
    out = []
    for j in range(k):
        span = []
        if j > 0:
            span.append(f"gte(t,{num(iv[edges[j]][0])})")
        if j < k - 1:
            span.append(f"lt(t,{num(iv[edges[j + 1]][0])})")
        out.append(f"{filt}='if({'*'.join(span)},{'+'.join(terms[edges[j]:edges[j + 1]])},1)'")
    return out


def select_filters(frame_ranges, fps: float, max_terms: int = SELECT_MAX_TERMS) -> list[str]:
    """select de los rangos [ka, kb) de cuadros de la reticula t = k/fps (la salida del filtro fps
    con start_time=0). Cada limite lleva medio cuadro de margen ([ka - 0.5, kb - 0.5] / fps): exacto
    por cuadro aunque t sea flotante (29.97, 23.976...). Pocos keeps -> un solo select."""
    f = float(fps)
    return select_chain((((a - 0.5) / f, (b - 0.5) / f) for a, b in frame_ranges if b > a),
                        "select", max_terms, digits=6)


def sum_expr(terms, leaf: int = SUM_LEAF) -> str:
    """'a+b+...' con CUALQUIER numero de terminos que av_expr acepta, para expresiones que no se
    pueden partir en varios filtros (crop/scale evaluados por cuadro). Hasta `leaf` terminos, la
    suma plana de siempre; con mas, arbol balanceado entre parentesis (profundidad ~ leaf + log2 n)."""
    terms = [str(t) for t in terms]
    if len(terms) <= max(2, int(leaf)):
        return "+".join(terms)
    m = len(terms) // 2
    return f"({sum_expr(terms[:m], leaf)})+({sum_expr(terms[m:], leaf)})"


# ---------------------------------------------------------------- rostro en la fuente (recorte vertical)
def face_samples(src, info: dict, cache_dir=None, det_w: int = 320) -> list[list[float]]:
    """[(t_orig, cy, h)] de la cara principal en los cuadros clave de la FUENTE (normalizado
    a la altura de display). Decodifica solo keyframes (~1 s para 38 s de HEVC 4K); si hay
    muy pocos, muestrea a 2 fps. Cacheado por fuente. [] si no hay detector o no hay caras."""
    cache_dir = Path(cache_dir or (paths.WORK / "motion_cache"))
    key = hashlib.sha1(json.dumps([FACE_Y_VERSION, _file_sig(src), det_w]).encode()).hexdigest()[:16]
    cf = cache_dir / f"facey_{key}.json"
    if cf.exists():
        try:
            return json.loads(cf.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    try:
        import cv2
        yunet = getattr(paths, "YUNET", None)
        if not yunet or not Path(yunet).exists() or not hasattr(cv2, "FaceDetectorYN"):
            return []
    except Exception:  # pragma: no cover
        return []
    sw, sh = int(info.get("w") or 0), int(info.get("h") or 0)
    if not sw or not sh:
        return []
    dw = det_w - det_w % 2
    dh = max(2, int(round(dw * sh / sw / 2)) * 2)
    det = cv2.FaceDetectorYN.create(str(yunet), "", (dw, dh), 0.6, 0.3, 5000)

    def run(pre_in: list[str], vf_pre: str) -> list[list[float]]:
        cmd = ([FFMPEG, "-hide_banner", "-nostdin", "-v", "info"] + pre_in + hwaccel_args()
               + ["-i", str(src), "-map", "0:v:0", "-an", "-sn", "-dn", "-vf",
                  f"{vf_pre}scale={dw}:{dh}:flags=area,showinfo", "-fps_mode", "passthrough",
                  "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])
        fsz = dw * dh * 3
        found = []
        with tempfile.TemporaryFile() as err:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
            try:
                k = 0
                while True:
                    buf = p.stdout.read(fsz)
                    if not buf or len(buf) < fsz:
                        break
                    fr = np.frombuffer(buf, np.uint8).reshape(dh, dw, 3)
                    _, faces = det.detect(fr)
                    if faces is not None and len(faces):
                        f = max(faces, key=lambda r: r[2] * r[3] * (0.5 + r[14]))
                        found.append((k, (f[1] + f[3] / 2) / dh, f[3] / dh))
                    k += 1
                p.wait()
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()
            err.seek(0)
            times = [float(x) for x in re.findall(rb"pts_time:\s*([-+\d.eE]+)", err.read())]
        return [[round(times[k], 4), round(float(cy), 4), round(float(fh), 4)]
                for k, cy, fh in found if k < len(times)]

    try:
        dur = float(info.get("duration") or 0.0)
        out = run(["-skip_frame", "nokey"], "")
        if len(out) < max(3, dur / 3.0):
            out = run([], "fps=2,")
    except Exception:  # noqa: sin caras = recorte por defecto, nunca falla el base pass
        out = []
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = _tmp_path(cf)
        tmp.write_text(json.dumps(out))
        os.replace(tmp, cf)
    except OSError:
        pass
    return out


def _keep_cy(samples: list, a: float, b: float) -> float | None:
    """Centro vertical tipico de la cara en el tramo original [a, b] (mediana)."""
    if not samples:
        return None
    ins = [s[1] for s in samples if a - 0.3 <= s[0] <= b + 0.3]
    if ins:
        return float(np.median(ins))
    near = min(samples, key=lambda s: min(abs(s[0] - a), abs(s[0] - b)))
    return float(near[1])


def vfollow_y(cy: float | None, hs: int, H: int) -> int:
    """Desplazamiento vertical del recorte H dentro de una imagen de alto hs (par)."""
    c = 0.4 if cy is None else cy
    y = int(round(c * hs - HEADROOM_Y * H))
    y = max(0, min(hs - H, y))
    return y - y % 2


def reframe_mode(info: dict, W: int, H: int, air: float | None = None) -> tuple[str, tuple[int, int]]:
    """Modo de reencuadre que usara reframe_filters (sin construir filtros) y el tamano del
    frente (fg): 'cover' | 'vfollow' | 'pillar' | 'track'/'center' (+'+air')."""
    sw, sh = int(info.get("w") or 0), int(info.get("h") or 0)
    if not sw or not sh:
        return "cover", (W, H)
    src_ar, out_ar = sw / sh, W / H
    if out_ar / 1.05 <= src_ar <= out_ar * 1.05:
        return "cover", (W, H)
    if src_ar < out_ar:
        if out_ar / src_ar >= PILLAR_RATIO:
            return "pillar", (max(2, int(round(sw * H / sh / 2)) * 2), H)
        return "vfollow", (W, H)
    inset = 1 - min(0.35, max(0.0, float(air or 0.0)))
    fg_w = W if inset >= 0.995 else (round(W * inset) // 2) * 2
    return ("track" if fg_w >= W else "track+air"), (fg_w, H)


def reframe_filters(info: dict, W: int, H: int, plan: dict | None = None,
                    air: float | None = None, work_dir=None, single_t: float | None = None,
                    timeline=None) -> tuple[list[str], dict]:
    """Filtros de reencuadre. Devuelve (filtros, meta); meta['order'] = 'post' (van despues del
    select de cortes: mas barato) o 'pre' (antes: dependen del tiempo ORIGINAL).

    - Fuente con aspecto ~ salida (+-5%): cover-scale (sin margenes).
    - Fuente mas ANCHA (horizontal): recorte al aspecto de salida que sigue al rostro con
      la trayectoria de plan['camera'] (misma geometria de pipeline.reframe_and_burn,
      via reframe_track.write_cmds). `air` > 0 reproduce el inset con margen desenfocado.
    - Fuente mas ALTA (vertical -> 1:1, 4:5): ancho completo y recorte vertical que sigue a
      la cara (un encuadre fijo por keep: entre keeps ya hay un corte), con aire arriba.
    - Fuente MUCHO mas alta (vertical -> 16:9, relacion >= PILLAR_RATIO): la toma completa
      centrada sobre un fondo desenfocado de si misma (nunca corta cabeza ni pies).
    - `single_t`: para un solo cuadro (preview) usa el encuadre de ese instante original.
    """
    sw, sh = info["w"], info["h"]
    src_ar, out_ar = sw / max(1, sh), W / H
    meta = {"mode": "cover", "fg": (W, H), "order": "post"}
    cover = [f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos",
             f"crop={W}:{H}"]
    if not sw or out_ar / 1.05 <= src_ar <= out_ar * 1.05:
        return cover, meta
    if src_ar < out_ar:                      # fuente mas alta que la salida
        if out_ar / src_ar >= PILLAR_RATIO:
            fg_w = max(2, int(round(sw * H / sh / 2)) * 2)
            meta.update(mode="pillar", fg=(fg_w, H))
            return [_blur_bg_graph(W, H, [f"scale={fg_w}:{H}:flags=lanczos"])], meta
        hs = max(H, int(round(sh * W / sw / 2)) * 2)
        samples = face_samples(plan["video"], info, work_dir) if plan and plan.get("video") else []
        keeps = list(timeline.keeps) if timeline is not None and timeline.keeps else None
        if single_t is not None or keeps is None:
            if keeps:
                k = next((kk for kk in keeps if kk[0] - 1e-6 <= single_t <= kk[1] + 1e-6),
                         min(keeps, key=lambda kk: abs(kk[0] - single_t)))
                cy = _keep_cy(samples, k[0], k[1])
            else:
                cy = float(np.median([s[1] for s in samples])) if samples else None
            ys = [vfollow_y(cy, hs, H)]
            yexpr = str(ys[0])
        else:
            fr = timeline.frame_ranges(timeline.fps) if timeline.fps else None
            ys = [vfollow_y(_keep_cy(samples, a, b), hs, H) for a, b in keeps]
            if fr is None or len(ys) == 1:
                yexpr = str(ys[0])
            else:
                starts, acc = [], 0
                for ka, kb in fr:
                    starts.append(acc)
                    acc += kb - ka
                terms = [str(ys[0])] + [f"({ys[i] - ys[i - 1]})*gte(n,{starts[i]})"
                                        for i in range(1, len(ys)) if ys[i] != ys[i - 1]]
                yexpr = sum_expr(terms)          # cientos de keeps: arbol que av_expr acepta
        meta.update(mode="vfollow", y=ys, faces=len(samples))
        return [f"scale={W}:{hs}:flags=lanczos", f"crop={W}:{H}:0:'{yexpr}'"], meta
    # fuente mas ANCHA: seguimiento horizontal (tiempo original: van antes del select)
    import reframe_track
    meta["order"] = "pre"
    inset = 1 - min(0.35, max(0.0, float(air or 0.0)))
    fg_w = W if inset >= 0.995 else (round(W * inset) // 2) * 2
    cw, ch = reframe_track.crop_dims(sw, sh, fg_w, H)
    centers = (plan or {}).get("camera") or []
    meta.update(mode="track" if centers else "center", fg=(fg_w, H))
    if centers and single_t is not None:
        i = min(len(centers) - 1, max(0, int(round(single_t * reframe_track.OUT_FPS))))
        x = int(round(min(max(centers[i] * sw - cw / 2, 0), sw - cw)))
        fg = [f"crop={cw}:{ch}:{x}:(ih-{ch})/2", f"scale={fg_w}:{H}:flags=lanczos"]
    elif centers:
        cmds = Path(work_dir or paths.WORK) / f"motion_{hashlib.sha1(json.dumps(centers).encode()).hexdigest()[:10]}_{cw}.crop.txt"
        if not cmds.exists():
            reframe_track.write_cmds(centers, sw, cw, cmds)
        fg = [f"sendcmd=f='{cmds}'", f"crop@fgc={cw}:{ch}:x=(iw-{cw})/2:y=(ih-{ch})/2",
              f"scale={fg_w}:{H}:flags=lanczos"]
    else:
        fg = [f"crop={cw}:{ch}", f"scale={fg_w}:{H}:flags=lanczos"]
    if fg_w >= W:
        return fg, meta
    meta["mode"] += "+air"
    return [_blur_bg_graph(W, H, fg)], meta


def _blur_bg_graph(W: int, H: int, fg: list[str]) -> str:
    """Grafo con split: fondo = la misma toma desenfocada (cover), frente = `fg` centrado.
    Se expresa como una sola cadena para -filter_complex (continua despues del overlay)."""
    bw_ = 270
    bh_ = max(2, (round(bw_ * H / W) // 2) * 2)
    bg = [f"scale={bw_}:{bh_}:force_original_aspect_ratio=increase", f"crop={bw_}:{bh_}",
          "gblur=sigma=12", f"scale={W}:{H}", "eq=brightness=-0.08:saturation=0.9"]
    return (f"split=2[bgs][fgs];[bgs]{','.join(bg)}[bg];[fgs]{','.join(fg)}[fg];"
            f"[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2")


def base_pass(plan: dict, out_video, out_voice, W: int, H: int, fps: float, timeline,
              enhance_audio: bool = True, on_pct: Callable[[float], None] | None = None,
              cancel: Callable[[], bool] | None = None, air: float | None = None,
              kind: str = "inter") -> dict:
    """Fuente -> video base (WxH, fps, cortes, tonemap, GOP corto) + voz de estudio cortada.

    Idempotente: si out_video/out_voice ya existen con su .json de metadatos completo, no
    recalcula (cache). Cada salida se genera bajo un lock por clave (otro hilo/proceso que
    pida lo mismo espera y reutiliza el resultado) en un temporal unico que se renombra al
    final: nunca deja cache corrupta. Devuelve {video, voice, n_frames, duration,
    cached_video, cached_voice, t_video, t_voice, mode}.
    """
    src = Path(plan["video"])
    info = probe(src)
    if not info["w"]:
        raise RuntimeError(f"No se pudo leer el video fuente: {src}")
    out_video, out_voice = Path(out_video), Path(out_voice)
    out_video.parent.mkdir(parents=True, exist_ok=True)
    n = timeline.n_frames(fps)
    if n <= 0:
        raise RuntimeError("La linea de tiempo quedo vacia (todo el video esta cortado).")
    res = {"video": str(out_video), "voice": str(out_voice), "n_frames": n,
           "duration": n / float(fps), "cached_video": False, "cached_voice": False,
           "t_video": 0.0, "t_voice": 0.0, "source": info}
    pct = {"v": 0.0, "a": 0.0}
    vexp = {"frames": n, "W": W, "H": H}
    aexp = {"samples": int(round(n / float(fps) * SR))}

    def report():
        if on_pct:
            on_pct(pct["v"] * 0.8 + pct["a"] * 0.2)

    # ---------------- voz (hilo en paralelo al video: son procesos ffmpeg independientes)
    voice_err: list[BaseException] = []

    def do_voice():
        t0 = time.time()
        try:
            if _meta_ok(out_voice, aexp):
                res["cached_voice"] = True
                touch(out_voice.with_suffix(out_voice.suffix + ".json"))
            else:
                with FileLock(out_voice, cancel):
                    if _meta_ok(out_voice, aexp):          # otro lo termino mientras esperaba
                        res["cached_voice"] = True
                    else:
                        _voice_pass(src, info, out_voice, timeline, fps, enhance_audio, cancel)
            pct["a"] = 100.0
        except BaseException as e:  # noqa
            voice_err.append(e)
        res["t_voice"] = time.time() - t0

    th = threading.Thread(target=do_voice, daemon=True)
    th.start()

    t0 = time.time()
    try:
        if _meta_ok(out_video, vexp):
            res["cached_video"] = True
            touch(out_video.with_suffix(out_video.suffix + ".json"))
        else:
            with FileLock(out_video, cancel):
                if _meta_ok(out_video, vexp):
                    res["cached_video"] = True
                else:
                    _video_pass(src, info, plan, out_video, W, H, fps, timeline, air, kind,
                                lambda p: (pct.__setitem__("v", p), report()), cancel)
        pct["v"] = 100.0
    finally:
        res["t_video"] = time.time() - t0
        th.join()
    if voice_err:
        raise voice_err[0]
    try:
        res["mode"] = json.loads(out_video.with_suffix(".mp4.json").read_text()).get("mode")
    except (OSError, json.JSONDecodeError):
        res["mode"] = None
    report()
    return res


def _meta_ok(path: Path, expect: dict) -> bool:
    path = Path(path)
    mp = path.with_suffix(path.suffix + ".json")
    if not (path.exists() and mp.exists()):
        return False
    try:
        m = json.loads(mp.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return all(m.get(k) == v for k, v in expect.items())


def _write_meta(path: Path, meta: dict) -> None:
    mp = path.with_suffix(path.suffix + ".json")
    tmp = _tmp_path(mp)
    tmp.write_text(json.dumps(meta))
    os.replace(tmp, mp)


def _video_pass(src: Path, info: dict, plan: dict, out: Path, W: int, H: int, fps: float,
                timeline, air, kind, on_pct, cancel) -> None:
    n = timeline.n_frames(fps)
    ranges = timeline.frame_ranges(fps)
    reframe, meta = reframe_filters(info, W, H, plan, air, out.parent, timeline=timeline)
    # reticula de salida con t0=0 -> cola clonada (el ultimo keep puede pasar del ultimo
    # cuadro real) -> cortes por cuadro de la reticula (t = k/fps; con muchos keeps, varios
    # select encadenados por tramos: select_filters) -> reencuadre (antes si depende del tiempo
    # original) -> tonemap HDR / normalizacion de color y recien entonces 8 bits.
    tail = max(1.0, 2.0 / fps)
    cut = ([f"fps=fps={fps}:start_time=0:round=near",
            f"tpad=stop_mode=clone:stop_duration={tail:.3f}"]
           + select_filters(ranges, fps) + [f"setpts=N/({fps}*TB)"])
    chain = (cut + reframe) if meta.get("order") == "post" else (reframe + cut)
    graph = ",".join(chain + tonemap_filters(info))
    tmp = _tmp_path(out)
    cmd = ([FFMPEG, "-y", "-v", "error"] + hwaccel_args(info)
           + ["-i", str(src), "-filter_complex", f"[0:v:0]{graph}[v]", "-map", "[v]",
              "-frames:v", str(n), "-an", "-sn", "-dn", "-r", str(fps)]
           + encoder_args(W, H, fps, kind) + ["-movflags", "+faststart", str(tmp)])
    try:
        run_ffmpeg_fallback(cmd, total_s=n / float(fps), on_pct=on_pct, cancel=cancel)
        got = count_frames(tmp)
        if got != n:
            raise RuntimeError(f"base pass: se esperaban {n} cuadros y salieron {got}")
    except BaseException:
        tmp.unlink(missing_ok=True)          # nunca dejar intermedios a medias
        raise
    os.replace(tmp, out)
    _write_meta(out, {"frames": n, "W": W, "H": H, "fps": fps, "mode": meta["mode"],
                      "hdr": info.get("hdr"), "src": str(src), "y": meta.get("y")})


def count_frames(path) -> int:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
                        "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip().split(",")[0])
    except ValueError:
        return 0


def read_audio(src, sr: int = SR, channels: int = 2, af: str | None = None,
               stream: int = 0, cancel=None) -> np.ndarray:
    """Audio de un archivo como float32 (n, channels) a sr (con filtro opcional)."""
    cmd = [FFMPEG, "-v", "error", "-nostdin", "-i", str(src), "-map", f"0:a:{stream}", "-vn"]
    if af:
        cmd += ["-af", af]
    cmd += ["-ac", str(channels), "-ar", str(sr), "-f", "f32le", "pipe:1"]
    with tempfile.TemporaryFile() as err:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        chunks = []
        try:
            while True:
                b = p.stdout.read(1 << 20)
                if not b:
                    break
                chunks.append(b)
                _check(cancel)
            p.wait()
        except BaseException:
            p.kill()
            p.wait()
            raise
        if p.returncode != 0:
            err.seek(0)
            raise RuntimeError("ffmpeg (audio) fallo: " + err.read().decode("utf-8", "replace")[-400:])
    a = np.frombuffer(b"".join(chunks), np.float32)
    a = a[: len(a) // channels * channels].reshape(-1, channels).copy()
    # la cadena de estudio puede soltar NaN: arnndn rellena la cola con ceros exactos (bloques de
    # 480) y deesser divide 0/0 ahi. Un solo NaN en la voz envenena la mezcla ENTERA (actividad de
    # voz -> curvas de musica y SFX) y el export sale mudo: se limpian aqui, en la lectura
    if not np.isfinite(a).all():
        np.nan_to_num(a, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    return a


def cut_audio(audio: np.ndarray, timeline, fps: float, sr: int = SR,
              xfade_ms: float = XFADE_MS) -> np.ndarray:
    """Aplica los keeps (limites cuantizados a cuadro) con microfundidos de potencia constante
    en cada union. La longitud resultante es EXACTAMENTE n_frames/fps*sr muestras."""
    ch = audio.shape[1] if audio.ndim == 2 else 1
    a = audio.reshape(len(audio), ch)
    total = int(round(timeline.n_frames(fps) / float(fps) * sr))
    out = np.zeros((total, ch), np.float32)
    h = max(1, int(round(xfade_ms / 1000.0 * sr / 2)))       # medio fundido
    pos = 0
    ranges = [(int(round(ka / float(fps) * sr)), int(round(kb / float(fps) * sr)))
              for ka, kb in timeline.frame_ranges(fps)]

    def src_slice(s0, s1):
        """Muestras [s0, s1) de la fuente, con ceros fuera de rango."""
        buf = np.zeros((s1 - s0, ch), np.float32)
        lo, hi = max(s0, 0), min(s1, len(a))
        if hi > lo:
            buf[lo - s0:hi - s0] = a[lo:hi]
        return buf

    for i, (s0, s1) in enumerate(ranges):
        seg = src_slice(s0, s1)
        L = min(len(seg), total - pos)
        if L <= 0:
            break
        out[pos:pos + L] = seg[:L]
        if i > 0:
            # fundido centrado en la union: cola del keep anterior (continuando en la fuente)
            # contra la entrada del actual (con su pre-roll en la fuente)
            prev_end = ranges[i - 1][1]
            tail = src_slice(prev_end - h, prev_end + h)
            head = src_slice(s0 - h, s0 + h)
            th = np.linspace(0.0, np.pi / 2, 2 * h, dtype=np.float32)[:, None]
            mix = tail * np.cos(th) + head * np.sin(th)
            j0, j1 = pos - h, pos + h
            k0, k1 = max(0, j0), min(total, j1)
            out[k0:k1] = mix[k0 - j0:k1 - j0]
        pos += L
    if ranges and ranges[0][0] > 0:           # arranque recortado: micro fade-in
        k = min(h * 2, total)
        out[:k] *= np.linspace(0.0, 1.0, k, dtype=np.float32)[:, None]
    if ranges and ranges[-1][1] < len(a) and pos > 0:
        # final recortado (la fuente sigue): fundido de salida en coseno elevado, espejo del
        # de entrada pero mas largo (END_FADE_MS): la voz no termina a media onda (clic/pop al
        # final del export) y el pedazo de silaba que la cuantizacion a cuadro deja entrar se
        # apaga en vez de cortarse en seco
        k = min(max(h * 2, int(round(END_FADE_MS / 1000.0 * sr))), pos)
        r = (0.5 + 0.5 * np.cos(np.linspace(0.0, np.pi, k, dtype=np.float64))).astype(np.float32)
        out[pos - k:pos] *= r[:, None]
    return out


def write_wav(path, audio: np.ndarray, sr: int = SR) -> None:
    import soundfile as sf
    path = Path(path)
    tmp = _tmp_path(path)
    try:
        sf.write(str(tmp), audio, sr, subtype="FLOAT", format="WAV")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _voice_pass(src: Path, info: dict, out: Path, timeline, fps: float, enhance: bool,
                cancel) -> None:
    n = timeline.n_frames(fps)
    total = int(round(n / float(fps) * SR))
    if info.get("has_audio"):
        af = _studio_chain() if enhance else "aresample=48000"
        try:
            a = read_audio(src, SR, 2, af, info.get("audio_index") or 0, cancel)
        except RuntimeError:
            if not enhance:
                raise
            a = read_audio(src, SR, 2, "aresample=48000", info.get("audio_index") or 0, cancel)
        voice = cut_audio(a, timeline, fps)
    else:
        voice = np.zeros((total, 2), np.float32)
    write_wav(out, voice)
    _write_meta(out, {"samples": int(len(voice)), "sr": SR, "enhance": bool(enhance)})


# ================================================================ pipes de cuadros
class FrameReader:
    """Decodifica cuadros RGBA uint8 (H, W, 4) por pipe, con busqueda EXACTA por indice.

    La busqueda se hace a medio cuadro antes del objetivo: ffmpeg decodifica desde el
    keyframe previo y descarta hasta el primer cuadro con pts >= (k-0.5)/fps = cuadro k.
    """

    def __init__(self, path, W: int, H: int, start_frame: int = 0, n_frames: int | None = None,
                 fps: float | None = None, threads: int = 2):
        self.path, self.W, self.H = str(path), int(W), int(H)
        self.fps = float(fps or probe(path)["fps"] or 30.0)
        self.start, self.n = int(start_frame), n_frames
        self.read_count = 0
        self._fb = self.W * self.H * 4
        cmd = [FFMPEG, "-v", "error", "-nostdin", "-threads", str(threads)]
        if self.start > 0:
            cmd += ["-ss", f"{(self.start - 0.5) / self.fps:.6f}"]
        cmd += ["-i", self.path, "-map", "0:v:0", "-an", "-sn", "-dn"]
        if n_frames:
            cmd += ["-frames:v", str(int(n_frames))]
        cmd += ["-vf", f"scale={self.W}:{self.H}:in_color_matrix=bt709:in_range=tv:"
                       f"flags=bicubic+accurate_rnd+full_chroma_int,format=rgba",
                "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
        self._err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=self._err,
                                     bufsize=self._fb * 2)

    def read(self) -> np.ndarray | None:
        if self.n is not None and self.read_count >= self.n:
            return None
        arr = np.empty((self.H, self.W, 4), np.uint8)
        mv = memoryview(arr).cast("B")
        got = 0
        while got < self._fb:
            k = self.proc.stdout.readinto(mv[got:])
            if not k:
                break
            got += k
        if got < self._fb:
            return None
        self.read_count += 1
        return arr

    def __iter__(self):
        while True:
            f = self.read()
            if f is None:
                return
            yield f

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.stdout.close()
        except Exception:
            pass
        self.proc.wait()
        self._err.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class ChunkWriter:
    """Codifica cuadros RGBA (H, W, 4) uint8 a un archivo de video (bt709/tv)."""

    def __init__(self, path, W: int, H: int, fps: float, kind: str = "final",
                 force_sw: bool = False, threads: int | None = None):
        self.path, self.W, self.H, self.fps = str(path), int(W), int(H), float(fps)
        self.frames = 0
        enc = encoder_args(self.W, self.H, self.fps, kind, force_sw=force_sw)
        cmd = [FFMPEG, "-y", "-v", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", "rgba",
               "-s", f"{self.W}x{self.H}", "-r", f"{self.fps}", "-i", "pipe:0",
               "-vf", "scale=out_color_matrix=bt709:out_range=tv:"
                      "flags=bicubic+accurate_rnd+full_chroma_int,format=yuv420p," + SETPARAMS_709]
        if threads:
            cmd += ["-threads", str(threads)]
        cmd += enc + ["-an", self.path]
        self._err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=self._err,
                                     bufsize=self.W * self.H * 4 * 2)

    def write(self, rgba: np.ndarray) -> None:
        if rgba.shape[:2] != (self.H, self.W):
            raise ValueError(f"cuadro {rgba.shape[:2]} != {(self.H, self.W)}")
        self.proc.stdin.write(memoryview(np.ascontiguousarray(rgba)).cast("B"))
        self.frames += 1

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except BrokenPipeError:
            pass
        rc = self.proc.wait()
        self._err.seek(0)
        msg = self._err.read().decode("utf-8", "replace")
        self._err.close()
        if rc != 0:
            raise RuntimeError(f"encoder fallo ({rc}): {msg[-500:]}")

    def abort(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait()
        finally:
            self._err.close()
            Path(self.path).unlink(missing_ok=True)


class PrefetchReader:
    """Lee cuadros de un FrameReader en un hilo, `depth` cuadros por delante: el decode
    de ffmpeg (bloqueado por el pipe de 64 KB) se solapa con la composicion."""

    def __init__(self, reader: FrameReader, depth: int = 3):
        import queue
        self.reader = reader
        self.q: "queue.Queue" = queue.Queue(maxsize=max(1, depth))
        self._stop = False
        self._err: BaseException | None = None
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        try:
            while not self._stop:
                f = self.reader.read()
                self.q.put(f)
                if f is None:
                    return
        except BaseException as e:  # noqa
            self._err = e
            self.q.put(None)

    def read(self) -> np.ndarray | None:
        f = self.q.get()
        if self._err is not None:
            raise self._err
        return f

    def close(self) -> None:
        self._stop = True
        self.reader.close()                  # desbloquea al hilo si esta leyendo
        try:
            while True:
                self.q.get_nowait()
        except Exception:
            pass
        self.th.join(timeout=5)


class AsyncWriter:
    """Escribe cuadros a un ChunkWriter desde un hilo (copia cada cuadro al encolarlo)."""

    def __init__(self, writer: ChunkWriter, depth: int = 3):
        import queue
        self.writer = writer
        self.q: "queue.Queue" = queue.Queue(maxsize=max(1, depth))
        self._err: BaseException | None = None
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        while True:
            f = self.q.get()
            if f is None:
                return
            if self._err is None:
                try:
                    self.writer.write(f)
                except BaseException as e:  # noqa
                    self._err = e

    def write(self, rgba: np.ndarray) -> None:
        if self._err is not None:
            raise self._err
        self.q.put(np.array(rgba, copy=True))

    def close(self) -> None:
        self.q.put(None)
        self.th.join()
        if self._err is not None:
            self.writer.abort()
            raise self._err
        self.writer.close()

    def abort(self) -> None:
        try:
            while True:
                self.q.get_nowait()
        except Exception:
            pass
        self.q.put(None)
        self.writer.abort()
        self.th.join(timeout=5)


# ================================================================ concat / mux
def concat(chunks: list, out, cancel=None) -> None:
    """Une chunks del mismo encoder sin recodificar (concat demuxer)."""
    out = Path(out)
    lst = out.with_suffix(".concat.txt")
    lst.write_text("".join(f"file '{Path(c).resolve().as_posix()}'\n" for c in chunks))
    try:
        run_ffmpeg([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", "-an", str(out)], cancel=cancel)
    finally:
        lst.unlink(missing_ok=True)


def mux(video, audio, out, duration: float | None = None, cancel=None) -> None:
    """Video (copia) + audio (AAC 192k 48 kHz) con +faststart."""
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(video)]
    if audio:
        cmd += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k",
                "-ar", str(SR), "-ac", "2"]
    else:
        cmd += ["-map", "0:v:0"]
    cmd += ["-c:v", "copy"]
    if duration:
        cmd += ["-t", f"{duration:.6f}"]
    cmd += ["-movflags", "+faststart", str(out)]
    run_ffmpeg(cmd, cancel=cancel)


def stream_info(path) -> dict:
    """Duracion/start_time/cuadros por stream (para verificar sincronia)."""
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                        "stream=codec_type,start_time,duration,nb_frames,width,height,"
                        "r_frame_rate,sample_rate,channels,color_space,color_transfer,"
                        "color_primaries,color_range,pix_fmt,profile,level",
                        "-of", "json", str(path)], capture_output=True, text=True)
    out = {}
    for s in json.loads(r.stdout or "{}").get("streams", []):
        out.setdefault(s["codec_type"], s)
    return out


def prune_cache(cache_dir, keep: int = 6, patterns=("base_*.mp4", "voice_*.wav"),
                min_age: float = 1800.0) -> int:
    """Poda LRU de la cache del motor (por mtime; los usos la refrescan con touch()). Deja
    `keep` bases/voces; al borrar un base borra tambien su pista de rostro (face_<clave>_*),
    su recorte de frente y su lock. Limita las carpetas de mattes (un PNG por cuadro) a
    `keep`, las pistas de rostro a 3*keep y borra temporales '.part.' huerfanos. Nada usado
    en los ultimos `min_age` s se borra (otro render puede estar leyendolo)."""
    import shutil
    removed = 0
    d = Path(cache_dir)
    if not d.exists():
        return 0
    now = time.time()

    def mtime(p):
        """Ultimo uso: el mas reciente entre el archivo y su .json (que touch() refresca)."""
        t = 0.0
        for q in (p, p.with_name(p.name + ".json")):
            try:
                t = max(t, q.stat().st_mtime)
            except OSError:
                pass
        return t

    def old(files, n_keep):
        files = sorted(files, key=mtime, reverse=True)
        return [f for f in files[n_keep:] if now - mtime(f) >= min_age]

    for pat in patterns:
        for f in old((f for f in d.glob(pat) if ".part" not in f.name), keep):
            for g in (f, f.with_suffix(f.suffix + ".json"), Path(str(f) + ".lock")):
                g.unlink(missing_ok=True)
            if f.name.startswith("base_"):
                for fc in d.glob(f"face_{f.stem[len('base_'):]}_*.npy"):
                    fc.unlink(missing_ok=True)
                for g in (d / ("fg" + f.name), d / ("fg" + f.name + ".json"),
                          d / ("fg" + f.name + ".lock")):
                    g.unlink(missing_ok=True)
            removed += 1
    for f in old(d.glob("face_*.npy"), keep * 3):
        f.unlink(missing_ok=True)
        removed += 1
    for m in old((p for p in d.glob("matte_*") if p.is_dir()), keep):
        shutil.rmtree(m, ignore_errors=True)
        removed += 1
    for pat, n_keep in (("grade_*.json", keep * 4), ("facey_*.json", keep * 4),
                        ("motion_*.crop.txt", keep * 4)):
        for f in old(d.glob(pat), n_keep):
            f.unlink(missing_ok=True)
            removed += 1
    # temporales huerfanos (proceso muerto) y carpetas de trabajo de mas de 6 h
    for f in d.glob("*.part*"):
        if now - mtime(f) > 6 * 3600:
            f.unlink(missing_ok=True)
            removed += 1
    for j in d.glob("job_*"):
        try:
            if j.is_dir() and now - j.stat().st_mtime > 6 * 3600:
                shutil.rmtree(j, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    return removed
