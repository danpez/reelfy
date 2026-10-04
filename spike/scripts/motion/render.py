"""Compositor del Motion Engine: orquestacion del render, cuadro exacto, draft y shorts.

Contrato: docs/MOTION-ENGINE.md, secciones 1, 2 y 4.

  render_pro(plan, out_path, opts) -> stats
    0. prepare    plan normalizado + Timeline + job; la MUSICA arranca ya en segundo plano
                  (music.prefetch: ACE-Step en GPU corre en paralelo con todo lo demas)
    1. timeline   keeps (cuts habilitados) cuantizados a cuadro (expandiendo), fragmentos
                  absorbidos, uniones pegadas a cambios de toma; remap y uniones
    2. base pass  video base (tamano del formato) con cortes + voz de estudio (cache por hash,
                  con lock: seguro con ensure_base en segundo plano)
    3. analisis   matte.MatteProvider (solo rangos que lo piden; incremental) EN PARALELO con
                  camera.face_track + grade.Grader.prepare; luego anclas por item (_anchor de
                  camara, _face0 de graficos anclados a la cara)
    4. audio      EN PARALELO con el compositor: music.get_music (acierto de cache) +
                  audio.mix(voz, SFX con variant/pitch/dur, musica con su final horneado, seed).
                  En draft no espera a la musica IA en generacion (procedural + music_pending).
                  Cancelar no espera al hilo de audio (el candado de music.py sondea)
    5. compositor N procesos (spawn), cada uno rangos contiguos de cuadros con su
                  FrameReader y ChunkWriter; fronteras de chunk fuera de transiciones
    6. concat + mux (AAC 192k, +faststart) y, con opts.shorts, los highlights remapeados
  render_frame(plan, t_final, opts) -> np.ndarray RGBA (un cuadro exacto, para /frame)
  ensure_base(plan, opts) / ensure_analysis(plan, opts): precalculo en segundo plano (Studio)
  render_draft(plan, out_path, opts) -> stats (540x960, reutiliza el base del formato)
  render_shorts(plan, final_mp4, opts) -> [{path, start, end, title, index}]

Los modulos de efectos (camera, grade, matte, text, graphics, cutaways, transitions,
audio, music) se importan de forma perezosa y tolerante: si un modulo o un kind no existe,
se omite con un aviso. `opts["modules"]` permite sustituirlos (tests con stubs).

Formatos: 9:16 (referencia de diseno), 1:1, 4:5, 16:9 y 'original' (dims de display de la
fuente con el lado corto a 1080). Los overlays (texto, graficos, subtitulos, escenas) se
disenan para 9:16: en otros formatos se dibujan dentro de una CAJA DE LAYOUT 9:16 centrada
(ver layout_box): x, y y tamanos relativos a la caja, asi nada se deforma ni se encima.

Opciones (`opts`):
  fmt ('9:16'|'1:1'|'4:5'|'16:9'|'original'), W, H (explicitos), fps (30), quality
  ('final'|'draft'), workers (default min(4, cpu//3); 0/1 = en proceso), cache_dir,
  music_cache_dir (cache de music.py; default la suya), stems_dir (voz/musica/sfx por separado),
  enhance_audio (True), air (margen desenfocado para fuentes horizontales, 0 = sin margen),
  logo ({path,size,opacity,pos} o None), progress (bool: barra superior automatica),
  dither (1.0: grano/dither final de +-2 niveles sobre TODO el cuadro; 0 = sin),
  min_fragment (s, default timeline.MIN_FRAG), shorts (bool), shorts_dir,
  modules ({nombre: modulo_importable}), sys_path ([rutas extra para los workers]),
  on_pct(stage, pct_total), on_step(msg), cancel() -> bool, strict (errores de renderer
  se propagan), keep_chunks (debug), cache_keep, prefetch_music (ensure_analysis; True),
  threads (hilos de cv2/OMP por worker; default cpu//workers),
  draft_wait_music (False: en draft se mezcla con la procedural si la IA aun se genera),
  media_root (carpeta(s) de los medios propios; default paths.INPUT/sources), media_resolver
  (callable media -> ruta), media_bg (True: /frame normaliza en segundo plano un clip sin cache).
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import importlib
import inspect
import json
import math
import multiprocessing as mp
import os
import pickle
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import skia

import paths
from . import ctx as C
from . import media as MF
from . import schema
from . import reel as RL
from . import screen as SC
from . import timeline as T
from . import video_io as V

MODULE_NAMES = ("camera", "grade", "matte", "text", "graphics", "cutaways", "transitions",
                "audio", "music")
COMPOSE_MODULES = ("camera", "grade", "matte", "text", "graphics", "cutaways", "transitions")
FORMATS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}
FORMAT_NAMES = tuple(FORMATS) + ("original",)
MATTE_EFFECTS = set(schema.MATTE_EFFECTS)
BLUR_MIN_PX = 6.0            # px ref/cuadro: a partir de aqui la camara agrega motion blur
SHUTTER = 0.5                # obturador de 180 grados
SNAP_TRANSITION_S = 0.25     # una transicion se pega a la union/cambio de toma mas cercano
GRAIN_PAD = 64
STAGE_ORDER = ("timeline", "base", "analysis", "audio", "render", "mux")
STAGES = {"timeline": (0, 1), "base": (1, 30), "analysis": (30, 40), "audio": (40, 45),
          "render": (45, 97), "mux": (97, 100)}


def _mcopy(m: skia.Matrix) -> skia.Matrix:
    """Copia de una skia.Matrix (skia-python no trae constructor de copia)."""
    return skia.Matrix.Concat(skia.Matrix(), m)


def _log(msg: str) -> None:
    print(f"   [motion] {msg}", flush=True)


def _accepts(fn, name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


# ================================================================ modulos
def load_modules(overrides: dict | None = None, names=MODULE_NAMES, quiet: bool = False) -> dict:
    """Importa los modulos de efectos; los que faltan quedan en None (se omiten)."""
    mods = {}
    ov = overrides or {}
    for n in names:
        if n in ov and not ov[n]:             # override falsy = desactivar el modulo
            mods[n] = None
            continue
        target = ov.get(n) or f"motion.{n}"
        try:
            mods[n] = importlib.import_module(target)
        except ModuleNotFoundError as e:
            mods[n] = None
            if not quiet and (e.name or "").split(".")[-1] == target.split(".")[-1]:
                _log(f"modulo {n} no disponible ({target}); se omite")
            elif not quiet:
                _log(f"modulo {n} no se pudo importar: {e}")
        except Exception as e:  # noqa: un modulo roto no tumba el render
            mods[n] = None
            if not quiet:
                _log(f"modulo {n} fallo al importar: {type(e).__name__}: {e}")
    return mods


def _reg(mod, attr: str) -> dict:
    r = getattr(mod, attr, None) if mod is not None else None
    return r if isinstance(r, dict) else {}


def registries(mods: dict) -> dict:
    cam, grade, matte = mods.get("camera"), mods.get("grade"), mods.get("matte")
    gfx = dict(_reg(mods.get("graphics"), "RENDERERS"))
    return {
        "matrix": _reg(cam, "MATRIX_EFFECTS"),
        "post": _reg(cam, "POST_EFFECTS"),
        "grade_fx": _reg(grade, "RENDERERS"),
        "matte_fx": _reg(matte, "RENDERERS"),
        "text": _reg(mods.get("text"), "RENDERERS"),
        "graphics": gfx,
        "cutaways": _reg(mods.get("cutaways"), "RENDERERS"),
        "transitions": _reg(mods.get("transitions"), "RENDERERS"),
        "windows": _reg(mods.get("transitions"), "WINDOWS"),
        "captions": getattr(mods.get("text"), "draw_captions", None) if mods.get("text") else None,
    }


# ================================================================ formatos y opciones
def _even(v: float) -> int:
    v = int(round(v))
    return max(2, v - v % 2)


def output_size(fmt: str | None, src_info: dict | None = None) -> tuple[int, int]:
    """(W, H) de un formato. 'original' = dims de DISPLAY de la fuente con el lado corto a
    1080 (el lado largo se limita a 3840). Formato desconocido -> ValueError."""
    fmt = fmt or "9:16"
    if fmt in FORMATS:
        return FORMATS[fmt]
    if fmt == "original":
        w, h = int((src_info or {}).get("w") or 0), int((src_info or {}).get("h") or 0)
        if not w or not h:
            raise ValueError("El formato 'original' necesita las dimensiones de la fuente.")
        s = 1080.0 / min(w, h)
        s = min(s, 3840.0 / max(w, h))
        return _even(w * s), _even(h * s)
    raise ValueError(f"Formato desconocido: {fmt!r}. Usa uno de: {', '.join(FORMAT_NAMES)}.")


def layout_box(W: int, H: int) -> tuple[int, int, int, int]:
    """Caja de layout 9:16 centrada (x, y, w, h) donde se disenan los overlays.

    En 9:16 es el cuadro completo. En 1:1, 4:5 y 16:9 es la franja central de ancho H*9/16:
    posiciones normalizadas y tamanos (fraccion del ancho, ctx.px) se miden contra la caja,
    asi el texto conserva su proporcion respecto a la altura y nada se encima."""
    bw = min(W, _even(H * 9 / 16))
    if bw >= W - 1:
        return 0, 0, W, H
    return (W - bw) // 2, 0, bw, H


# opciones del proyecto (fuera del plan) que render_pro, render_draft y render_frame aplican:
# el servidor las anuncia en GET /settings (pro_opts) y el Studio habilita sus controles
PRO_OPTS = ("logo", "air", "enhance_audio")
# tc / bc = arriba / abajo al centro (regla de simetria: mismo margen a izquierda y derecha)
LOGO_POS = {"tl": "tl", "tr": "tr", "bl": "bl", "br": "br", "tc": "tc", "bc": "bc", "top-left": "tl",
            "top-right": "tr", "bottom-left": "bl", "bottom-right": "br", "top-center": "tc",
            "bottom-center": "bc", "top": "tc", "bottom": "bc"}


def logo_cfg(v: Any) -> dict | None:
    """Marca de agua del proyecto -> {path, pos, size, opacity} saneado, o None.

    Acepta None/False (sin logo), True (logo de la marca con los defaults) o un dict como el
    del Studio {pos: tl|tr|bl|br|tc|bc, size: fraccion del ancho de la caja, opacity}. `path`
    default = la marca del usuario (paths.BRAND/logo.png). Misma geometria que el legado: margen
    del 3 % de la caja de layout."""
    if not v:
        return None
    d = dict(v) if isinstance(v, dict) else {}
    if d.get("enabled") is False:
        return None
    pos = LOGO_POS.get(str(d.get("pos") or "br").strip().lower(), "br")
    return {"path": str(d.get("path") or (paths.BRAND / "logo.png")), "pos": pos,
            "size": min(0.5, max(0.03, _num_or(d.get("size"), 0.14))),
            "opacity": min(1.0, max(0.0, _num_or(d.get("opacity"), 0.9)))}


def _ram_gb() -> float:
    """RAM fisica en GB (0 si no se puede leer)."""
    try:
        if sys.platform == "darwin":
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                                      timeout=3).stdout.strip()) / 2 ** 30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except Exception:  # noqa: Windows u otro -> sin limite por RAM
        return 0.0


def default_workers(cpus: int | None = None, ram_gb: float | None = None) -> int:
    """Procesos del compositor: hasta 4, uno por cada 3 nucleos y ~2.5 GB de RAM libres para ellos
    (medido: ~2 GB por proceso a 1080x1920; se reservan ~4 GB para la app, la IA local y el sistema).
    En una Mac de 8 GB 4 procesos hacian swap; con 16 GB o mas no cambia nada."""
    cpus = cpus if cpus is not None else (os.cpu_count() or 4)
    ram = _ram_gb() if ram_gb is None else ram_gb
    n = min(4, cpus // 3)
    if ram > 0:
        n = min(n, int((ram - 4.0) // 2.5))
    return max(1, n)


def _resolve_opts(opts: dict | None, src_info: dict | None = None) -> dict:
    o = dict(opts or {})
    fmt = o.get("fmt") or "9:16"
    if fmt not in FORMAT_NAMES:
        raise ValueError(f"Formato desconocido: {fmt!r}. Usa uno de: {', '.join(FORMAT_NAMES)}.")
    o["fmt"] = fmt
    o.setdefault("quality", "final")
    if o.get("W") and o.get("H"):
        W, H = int(o["W"]), int(o["H"])
        bW, bH = int(o.get("base_W") or W), int(o.get("base_H") or H)
    else:
        bW, bH = output_size(fmt, src_info)
        W, H = (bW // 2, bH // 2) if o["quality"] == "draft" else (bW, bH)
    o["W"], o["H"] = W - W % 2, H - H % 2
    o["base_W"], o["base_H"] = bW - bW % 2, bH - bH % 2
    o["fps"] = float(o.get("fps") or 30)
    o.setdefault("cache_dir", str(paths.WORK / "motion_cache"))
    # enhance_audio: None (el Studio sin el control) = True; air: margen desenfocado 0..0.35
    o["enhance_audio"] = True if o.get("enhance_audio") is None else bool(o["enhance_audio"])
    air = _num_or(o.get("air"), 0.0)
    o["air"] = round(min(0.35, max(0.0, air)), 4) or None
    if o["air"] and src_info:
        # solo una fuente mas ANCHA que la salida lleva margen: en el resto el aire no cambia la
        # imagen y se descarta (la clave del base no cambia y su cache se reutiliza)
        try:
            if not V.reframe_mode(src_info, o["base_W"], o["base_H"], o["air"])[0].endswith("+air"):
                o["air"] = None
        except Exception:  # noqa: sin dims de la fuente se conserva
            pass
    o.setdefault("modules", {})
    o.setdefault("sys_path", [])
    o.setdefault("dither", 1.0)
    if "workers" not in o:
        o["workers"] = default_workers()
    return o


class _Progress:
    """Progreso global 0..100 monotono por etapas (rangos dinamicos) + cancelacion."""

    def __init__(self, opts: dict, stages: dict | None = None):
        import threading
        self.on_pct = opts.get("on_pct")
        self.on_step = opts.get("on_step")
        self.cancel = opts.get("cancel")
        self.stages = stages or STAGES
        self.last = -1.0
        self._lock = threading.Lock()   # lo llaman el hilo principal, el de audio y el matte

    def step(self, msg: str) -> None:
        _log(msg)
        if self.on_step:
            self.on_step(msg)

    def pct(self, stage: str, p: float) -> None:
        self.check()
        a, b = self.stages.get(stage, (self.last, self.last))
        v = round(a + (b - a) * max(0.0, min(100.0, p)) / 100.0, 1)
        if not self.on_pct:
            return
        with self._lock:
            if v <= self.last:
                return
            self.last = v
        self.on_pct(stage, v)

    def check(self) -> None:
        if self.cancel and self.cancel():
            raise V.Canceled("render cancelado")


def stage_plan(weights: dict) -> dict:
    """Pesos relativos por etapa -> rangos [a, b) de 0 a 100 (en el orden de STAGE_ORDER)."""
    tot = sum(max(0.0, float(weights.get(s, 0))) for s in STAGE_ORDER) or 1.0
    out, acc = {}, 0.0
    for s in STAGE_ORDER:
        w = max(0.0, float(weights.get(s, 0))) / tot * 100.0
        out[s] = (round(acc, 3), round(min(100.0, acc + w), 3))
        acc += w
    out["mux"] = (out["mux"][0], 100.0)
    return out


# ================================================================ preparacion del job
def _flat_words(phrases: list) -> list[dict]:
    out = []
    for ph in phrases:
        for w in ph:
            out.append({"text": w.get("text", ""), "start": float(w["start"]),
                        "end": float(w["end"]), "kw": bool(w.get("kw"))})
    return out


def _merge_ranges(rs: list[tuple[float, float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted(rs):
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _is_behind(it: dict, track: str, mods: dict) -> bool:
    """Item detras del sujeto: campo `behind` o default del preset (text.is_behind)."""
    if track == "text":
        f = getattr(mods.get("text"), "is_behind", None) if mods.get("text") else None
        if callable(f):
            try:
                return bool(f(it))
            except Exception:
                pass
        if it.get("behind") is not None:
            return bool(it["behind"])
        return bool(schema.TEXT_PRESET_DEFAULTS.get(it.get("preset") or "", {}).get("behind"))
    return bool(it.get("behind"))


def _num_or(v: Any, default: float) -> float:
    """Numero finito o `default` (parse robusto: None, texto, NaN, inf -> default)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _mixers(mods: dict) -> tuple:
    tr = mods.get("transitions")
    mx = getattr(tr, "MIXERS", None) if tr is not None else None
    if isinstance(mx, (tuple, list, set)):
        return tuple(mx)
    return tuple(k for k, v in _reg(tr, "WINDOWS").items() if float(v) < 0.5)


def transition_frac(it: dict, mods: dict) -> float:
    """Fraccion de la ventana ANTES de la union. Usa transitions.effective_frac (las mezclas
    A->B siempre 0.0: prev congelado solo existe despues de la union; las centradas respetan
    `align`). Sin el helper: misma regla con WINDOWS."""
    tr = mods.get("transitions")
    fn = getattr(tr, "effective_frac", None) if tr is not None else None
    if callable(fn):
        try:
            return min(1.0, max(0.0, float(fn(it))))
        except Exception:  # noqa: un item raro no tumba el render
            pass
    k = it.get("kind")
    if k in _mixers(mods):
        return 0.0
    a = it.get("align")
    frac = {"center": 0.5, "start": 0.0, "end": 1.0}.get(a.strip().lower() if isinstance(a, str) else a)
    return frac if frac is not None else float(_reg(tr, "WINDOWS").get(k, 0.5))


def transition_dur(it: dict, mods: dict) -> float:
    """Duracion (s) de la transicion con parse robusto: invalida, 0 o negativa -> default del
    kind (transitions.DEFAULTS) -> 0.35; tope 4 s. El corte seco es el kind 'cut' (no dur 0:
    schema.normalize acota dur al rango del kind, asi que 0 nunca llega del pipeline)."""
    tr = mods.get("transitions")
    defs = getattr(tr, "DEFAULTS", {}) if tr is not None else {}
    kd = _num_or((defs.get(it.get("kind")) or {}).get("dur") if isinstance(defs, dict) else None, 0.35)
    if kd <= 0:
        kd = 0.35
    d = _num_or(it.get("dur"), kd)
    return min(4.0, d if d > 0 else kd)


def build_job(plan: dict, tl: T.Timeline, opts: dict, mods: dict) -> tuple[dict, list[str]]:
    """Plan normalizado + timeline -> job serializable para el compositor.

    Mapea todos los items a tiempo final, descarta los que caen en cortes o cuyo
    renderer no existe (con aviso), calcula ventanas de transicion (pegadas a la union o
    cambio de toma real mas cercano) y rangos de matte.
    """
    W, H, fps = opts["W"], opts["H"], opts["fps"]
    n_frames = tl.n_frames(fps)
    fdur = n_frames / fps
    reg = registries(mods)
    fx = plan["fx"]
    skipped: list[str] = []
    warnings: list[str] = []

    def skip(msg):
        if msg not in skipped:
            skipped.append(msg)

    # uniones y cambios de toma (cuadros finales): un item que empieza/termina a <= 1.5 cuadros de uno
    # se pega a el (T.snap_window: sin un cuadro sin zoom justo antes del salto)
    bounds = tl.content_frames(plan.get("shots"), fps)

    def mapped(track):
        out = []
        for i, it in enumerate(fx.get(track) or []):
            if not it.get("enabled", True):
                continue
            r = tl.map_item(it)
            if r is None:
                continue
            fs, fe = T.snap_window(r[0], min(r[1], fdur), bounds, fps)
            if fe - fs <= 1e-6:
                continue
            d = copy.deepcopy(it)
            d["_fs"], d["_fe"], d["_ord"] = fs, fe, i
            out.append(d)
        return out

    phrases = tl.map_words(plan.get("phrases") or [], drop_spans=T.word_cut_spans(plan.get("cuts")))
    words = _flat_words(phrases)

    # ---- texto
    text = []
    for it in mapped("text"):
        if it.get("preset") not in reg["text"]:
            skip(f"text:{it.get('preset')}")
            continue
        fs, fe = it["_fs"], it["_fe"]
        it["_words"] = [dict(w, start=round(w["start"] - fs, 4), end=round(w["end"] - fs, 4))
                        for w in words if w["end"] > fs and w["start"] < fe]
        text.append(it)

    def by_registry(track, key, rg):
        out = []
        for it in mapped(track):
            if it.get(key) not in rg:
                skip(f"{track}:{it.get(key)}")
                continue
            out.append(it)
        return out

    def with_media(track, items):
        """Medios propios: `_media` (archivo resuelto por sid) en los kinds que los aceptan;
        un medio que no esta en disco omite el item (aviso en skipped)."""
        out = []
        for it in items:
            if it.get("kind") not in MEDIA_KINDS.get(track, ()):
                out.append(it)
                continue
            spec = media_spec(it, opts)
            optional = it.get("kind") in OPTIONAL_MEDIA.get(track, ())
            if optional and spec is not None and not spec.get("missing") and spec.get("type") != "image":
                spec = None                       # avatar: solo imagen (un clip no aplica)
            if spec is None:
                if track == "graphics" and not optional:   # PiP sin medio: nada que dibujar
                    skip(f"media:{it.get('id')}:sin_medio")
                    continue
            elif spec.get("missing"):
                if optional:                      # foto de perfil borrada: iniciales, con aviso
                    skip(f"avatar:{it.get('id')}:no_encontrado")
                    out.append(it)
                    continue
                skip(f"media:{it.get('id')}:no_encontrado")
                continue
            else:
                it["_media"] = spec
            out.append(it)
        return out

    graphics = with_media("graphics", by_registry("graphics", "kind", reg["graphics"]))
    if opts.get("progress") and "progress" in reg["graphics"] and \
            not any(g.get("kind") == "progress" for g in graphics):
        graphics.append({"id": "gr_progress", "kind": "progress", "enabled": True, "src": "auto",
                         "intensity": 1.0, "_fs": 0.0, "_fe": fdur, "_ord": 10_000,
                         **(opts["progress"] if isinstance(opts["progress"], dict) else {})})
    for it in text:
        it["_behind"] = _is_behind(it, "text", mods)
    for it in graphics:
        it["_behind"] = _is_behind(it, "graphics", mods)
    cutaways = with_media("cutaways", by_registry("cutaways", "kind", reg["cutaways"]))
    freq = getattr(mods.get("cutaways"), "frame_request", None) if mods.get("cutaways") else None
    for it in cutaways:
        if not callable(freq):
            break
        if it.get("_media"):                      # el medio del usuario reemplaza al cuadro
            continue
        try:
            r = freq(it)
        except Exception:
            r = None
        if r and r[0]:
            to = r[2] if r[2] is not None else it.get("start", 0.0)
            k = int(round(tl.remap_clamped(float(to)) * fps))
            it["_frame_req"] = [r[0], int(min(max(0, k), max(0, n_frames - 1)))]
    effects = []
    for it in mapped("effects"):
        k = it.get("kind")
        if k in reg["matrix"]:
            it["_stage"] = "matrix"
        elif k in reg["grade_fx"] or k in reg["matte_fx"]:
            it["_stage"] = "base"
        elif k in reg["post"]:
            it["_stage"] = "post"
        else:
            skip(f"effects:{k}")
            continue
        effects.append(it)

    # ---- transiciones: ventana en cuadros alrededor de la union REAL mas cercana
    cands = sorted(set(tl.join_frames(fps)) | set(tl.shot_change_frames(plan.get("shots"), fps)))
    snap = int(round(SNAP_TRANSITION_S * fps))
    transitions = []
    for i, it in enumerate(fx.get("transitions") or []):
        if not it.get("enabled", True) or it.get("kind") == "cut":
            continue
        k = it.get("kind")
        iid = it.get("id") or f"#{i}"
        if k not in reg["transitions"]:
            skip(f"transitions:{k}")
            continue
        try:
            J0 = int(round(tl.remap_clamped(float(it["at"])) * fps))
        except (KeyError, TypeError, ValueError):
            skip(f"transitions:{iid}:sin_tiempo")
            continue
        near = min(cands, key=lambda c: abs(c - J0)) if cands else None
        if near is not None and abs(near - J0) <= snap:
            J, snapped = near, True
        else:
            J, snapped = J0, False
        if J <= 0 or J >= n_frames:
            skip(f"transitions:{iid}:fuera_del_video")
            continue
        frac = transition_frac(it, mods)
        if not snapped and k in _mixers(mods):
            # mezcla A->B sin cambio de contenido: se dibuja igual (A congelado sobre el
            # video que sigue) pero se avisa para que la UI lo muestre
            warnings.append(f"transitions:{iid}:sin_union")
        dur = transition_dur(it, mods)
        L = max(1, int(round(dur * fps)))
        F0 = max(0, J - int(round(L * frac)))
        F1 = min(n_frames, F0 + L)
        d = copy.deepcopy(it)
        d.update(_J=J, _F0=F0, _F1=F1, _P=J - 1, _ord=i, _fs=F0 / fps, _fe=F1 / fps,
                 _dur_final=(F1 - F0) / fps, _snapped=snapped)
        transitions.append(d)

    # ---- captions (rangos ocultos durante titulos y movidos por fotos con pie: caption_layout)
    cap = copy.deepcopy(fx.get("captions") or {})
    captions_on = bool(cap.get("enabled", True)) and cap.get("preset") != "none"
    if captions_on and reg["captions"] is None:
        skip("captions:draw_captions")
        captions_on = False

    # ---- recursos flotantes del guion (fx.media, float_card): archivo por sid y tarjeta que
    # termina arriba de la franja de subtitulos (motion/media.py)
    float_media = []
    lb = layout_box(W, H)
    media_items = mapped("media")
    # sin destellos de la camara entre dos recursos ni junto a un cambio de toma (1-7 cuadros: la cara nitida
    # entre dos fragmentos de apoyo, o la toma anterior un cuadro antes del cambio): timeline.overlay_continuity.
    # Una grabacion de pantalla sincronizada no se mueve (su ventana es la de su toma)
    if media_items:
        wins = [[it["_fs"], it["_fe"], it.get("style") != SC.STYLE, it.get("style") != SC.STYLE]
                for it in media_items]
        for it, (a, b) in zip(media_items, T.overlay_continuity(wins, bounds, fps)):
            it["_fs"], it["_fe"] = a, min(b, fdur)
    for it in media_items:
        style = it.get("style") or "float_card"
        if style not in MF.STYLES and style not in (SC.STYLE, RL.STYLE):
            skip(f"media:{it.get('style')}")
            continue
        spec = float_spec(it, opts)
        if spec is None:
            skip(f"media:{it.get('id')}:no_encontrado")
            continue
        it["_media"], it["_float"] = spec, True
        if style == RL.STYLE:
            # video de apoyo en fragmentos: sus tomas a la medida del tramo FINAL, ventana o pantalla completa
            if not _reel_job(it, spec, plan, opts, lb, cap, captions_on):
                skip(f"media:{it.get('id')}:sin_tomas")
                continue
            float_media.append(it)
            continue
        if style == SC.STYLE:
            # grabacion de pantalla sincronizada: ventana, sincronia en tiempo FINAL, tarjeta y cara
            if not _screen_job(it, spec, tl, plan, opts, lb, cap, captions_on, bounds, fdur):
                skip(f"media:{it.get('id')}:sin_sincronia")
                continue
            float_media.append(it)
            continue
        wh = _media_src_wh(it)
        span = (cap, captions_on, _num_or(it.get("start"), 0.0), _num_or(it.get("end"), 0.0), lb[2] / lb[3])
        it["_card"] = MF.card_layout(wh[0] if wh else None, wh[1] if wh else None, lb[2] / lb[3],
                                     MF.caption_top(*span), it.get("width"), it.get("aspect"), it.get("y"),
                                     MF.caption_bottom(*span))
        spec["card"] = [it["_card"]["w"], it["_card"]["aspect"]]
        float_media.append(it)

    # ---- matte: rangos (cuadros finales) que necesitan al sujeto
    need = []
    for it in effects:
        if it["kind"] in MATTE_EFFECTS or (it["kind"] == "bw" and it.get("subject_color")):
            need.append((it["_fs"], it["_fe"]))
    for it in text + graphics:
        if it.get("_behind"):
            need.append((it["_fs"], it["_fe"]))
    matte_ranges = []
    for a, b in _merge_ranges(need):
        f0, f1 = max(0, int(math.floor(a * fps)) - 2), min(n_frames, int(math.ceil(b * fps)) + 2)
        if f1 > f0:
            matte_ranges.append((f0, f1))
    matte_ranges = [tuple(r) for r in _merge_ranges(matte_ranges)]
    if matte_ranges and mods.get("matte") is None:
        skip("matte:MatteProvider")

    director = plan.get("director") or {}
    shots = sorted(tl.map_shots(plan.get("shots") or []), key=lambda s: s["start"])
    grade_cfg = copy.deepcopy(fx.get("grade") or {})
    logo = logo_cfg(opts.get("logo"))
    if logo is not None and not Path(logo["path"]).exists():
        skip("logo:archivo")
        logo = None
    cuts_frames = sorted(set(tl.join_frames(fps)) | set(tl.shot_change_frames(plan.get("shots"), fps)))
    job = {
        "W": W, "H": H, "fps": fps, "quality": opts["quality"], "n_frames": n_frames,
        "final_duration": fdur, "seed": int(director.get("seed", 7)),
        "style": director.get("style", "dinamico"), "accent": project_accent(plan),
        "timeline": tl.to_dict(),
        "entities": dict(plan.get("entities") or {}), "shots": shots, "words": words,
        "phrases": phrases, "captions": cap, "captions_on": captions_on,
        "text": text, "graphics": graphics, "cutaways": cutaways, "effects": effects,
        "transitions": transitions, "media": float_media, "grade_cfg": grade_cfg,
        "grade_on": bool(grade_cfg.get("enabled", True)) and mods.get("grade") is not None,
        "matte_ranges": matte_ranges, "logo": logo, "cut_frames": cuts_frames,
        "layout": list(layout_box(W, H)), "warnings": warnings,
        "dither": float(opts.get("dither", 1.0) or 0.0),
        "modules": dict(opts.get("modules") or {}), "sys_path": list(opts.get("sys_path") or []),
        "strict": bool(opts.get("strict")),
        # carpeta(s) de los medios propios para lo que graphics resuelve por sid (logo del enlace,
        # foto de una tarjeta dentro de una escena): la MISMA que resuelve `media` (serializable)
        # (un `media_resolver` no viaja a los procesos: sin media_root, la carpeta de POST /sources)
        "media_root": [str(x) for x in _media_roots(opts)] if opts.get("media_root") is not None else None,
    }
    caption_layout(job)
    return job, skipped


def _screen_job(it: dict, spec: dict, tl: T.Timeline, plan: dict, opts: dict, lb, cap: dict,
                captions_on: bool, bounds, fdur: float) -> bool:
    """Grabacion de pantalla sincronizada (fx.media "screen", motion/screen.py) en el job: ventana final
    acotada a sus pedazos sincronizados, filas de sincronia en TIEMPO FINAL `_sync_out` = [[fs, fe,
    s0]] (el cuadro final T muestra la pantalla s0 + rate * (T - fs): cada pedazo por cada tramo
    conservado tras los recortes), ventana (screen.layout arriba de los subtitulos, con la proporcion de
    lo que hay dentro de su marco `crop`) y movimiento segun el estilo. False si no queda nada."""
    cov = SC.coverage(it)
    if cov is None:
        return False
    r = tl.map_range(cov[0], cov[1])
    if r is None:
        return False
    fps = float(opts["fps"])
    fs, fe = T.snap_window(r[0], min(r[1], fdur), bounds, fps)
    if fe - fs <= 1e-6:
        return False
    sync = it.get("sync") or {}
    off, rate = SC._num(sync.get("offset"), 0.0), SC._num(sync.get("rate"), 1.0) or 1.0
    rows = []
    for t0, t1, c in SC.sync_pieces(it):
        a0, b0 = max(t0, cov[0]), min(t1, cov[1])
        if b0 - a0 <= 1e-6:
            continue
        for ka, kb in tl.keeps:
            a, b = max(a0, ka), min(b0, kb)
            if b - a <= 1e-6:
                continue
            f0 = tl.remap(a)
            if f0 is None:
                continue
            rows.append([round(f0, 6), round(f0 + (b - a), 6), round(off + rate * (c + a - t0), 6)])
    if not rows:
        return False
    rows.sort(key=lambda x: x[0])
    it["_fs"], it["_fe"] = fs, fe
    it["_screen"] = True
    it["_sync_out"] = rows
    lo = min(x[2] for x in rows)
    hi = max(x[2] + rate * (x[1] - x[0]) for x in rows)
    it["_motion"] = SC.motion_of(it, (plan.get("director") or {}).get("style"))
    crop = SC.crop_of(it)
    wh = _media_src_wh(it)
    span = (cap, captions_on, cov[0], cov[1], lb[2] / lb[3])
    it["_card"] = SC.layout(wh[0] if wh else it.get("w"), wh[1] if wh else it.get("h"), lb[2] / lb[3],
                            MF.caption_top(*span), MF.caption_bottom(*span), it.get("width"), crop)
    spec.update(kind="screen", card=[it["_card"]["w"], it["_card"]["aspect"]], crop=crop, sync_out=rows,
                fs=fs, rate=rate, scr_lo=round(lo, 6), scr_hi=round(hi, 6), media_in=round(max(0.0, lo), 6))
    return True


def _reel_job(it: dict, spec: dict, plan: dict, opts: dict, lb, cap: dict, captions_on: bool) -> bool:
    """Video de apoyo en fragmentos (fx.media "reel", motion/reel.py) en el job: las tomas repartidas en su
    tramo FINAL (reel.fit -> spec["reel"] = [[t0, t1, src_in, recorte, j, src_max]] locales) con el recorte de cada una (pantalla completa:
    reel.full_crop a la proporcion de la caja; ventana: su recorte `win` agrandado a la proporcion de la
    tarjeta, reel.win_view, o el cuadro completo, que la normalizacion cubre) y la tarjeta (ventana) arriba
    de los subtitulos.
    False si no queda ninguna toma."""
    fps = float(opts["fps"])
    ps = RL.pieces_of(it)
    rows = RL.fit(ps, it["_fe"] - it["_fs"], fps)
    if not rows:
        return False
    wh = None
    try:
        info = V.probe(spec["src"])
        wh = (int(info.get("w") or 0), int(info.get("h") or 0))
    except Exception:  # noqa: las del plan
        wh = None
    if not wh or not wh[0] or not wh[1]:
        wh = (_num_or(it.get("w"), 0.0), _num_or(it.get("h"), 0.0))
    if not wh[0] or not wh[1]:
        return False
    ba = lb[2] / lb[3]
    lay = RL.layout_of(it)
    it["_reel"], it["_layout"] = True, lay
    it["_motion"] = RL.motion_of(it, (plan.get("director") or {}).get("style"))
    if lay == "window":
        span = (cap, captions_on, _num_or(it.get("start"), 0.0), _num_or(it.get("end"), 0.0), ba)
        it["_card"] = RL.window_layout(it, wh[0], wh[1], ba, MF.caption_top(*span), MF.caption_bottom(*span))
        spec["card"] = [it["_card"]["w"], it["_card"]["aspect"]]
    else:
        it["_card"] = None
    out = []
    for t0, t1, src_in, j, src_max in rows:
        p = ps[int(j)]
        crop = RL.full_crop(p.get("cx"), wh[0], wh[1], ba) if lay == "full" else \
            RL.win_view(p, wh[0], wh[1], it["_card"]["aspect"])
        out.append([t0, t1, src_in, crop, int(j), src_max])
    spec.update(kind="reel", layout=lay, reel=out, loop=False, speed=1.0, media_in=round(out[0][2], 6))
    return True


GRAPHIC_ANCHORS = ("face", "face_left", "face_right", "face_top")


def _box_face(f, W: int, H: int, box) -> list[float]:
    """Cara (cx, cy, w, h) normalizada al cuadro -> normalizada a la caja de layout."""
    bx, by, bw, bh = box
    cx, cy, fw, fh = (float(v) for v in f[:4])
    return [round((cx * W - bx) / bw, 5), round((cy * H - by) / bh, 5),
            round(fw * W / bw, 5), round(fh * H / bh, 5)]


def set_face0(job: dict, rows: np.ndarray | None, mods: dict) -> None:
    """item['_face0'] = cara al INICIO de cada grafico anclado a la cara (`anchor` face*, sin
    x/y): primera cara valida dentro de su rango, en coordenadas de la caja de layout. El
    renderer la usa en los huecos del detector (el grafico no desaparece ni salta)."""
    gr = mods.get("graphics")
    anchors = tuple(getattr(gr, "ANCHORS", GRAPHIC_ANCHORS) or GRAPHIC_ANCHORS)
    fps, N = job["fps"], job["n_frames"]
    box = tuple(job.get("layout") or (0, 0, job["W"], job["H"]))
    for it in job["graphics"]:
        if it.get("anchor") not in anchors or (it.get("x") is not None and it.get("y") is not None):
            continue
        it.pop("_face0", None)
        if rows is None:
            continue
        f0 = int(math.floor(it["_fs"] * fps))
        f1 = min(N, max(f0 + 1, int(math.ceil(it["_fe"] * fps))))
        seg = rows[max(0, f0):f1]
        ok = np.where(~np.isnan(seg).any(axis=1))[0] if len(seg) else []
        if len(ok):
            it["_face0"] = _box_face(seg[int(ok[0])], job["W"], job["H"], box)


def set_anchors(job: dict, face: np.ndarray | None, mods: dict) -> None:
    """Ancla de encuadre FIJA por efecto de camara (camera.item_anchor: mediana de la cara en
    su rango) y `_face0` de los graficos anclados a la cara. Determinista y estable: el mismo
    item encuadra igual en todos los procesos."""
    rows = _face_rows(face, job["n_frames"]) if face is not None else None
    set_face0(job, rows, mods)
    set_media_auto(job, rows, mods)
    cam = mods.get("camera")
    fn = getattr(cam, "item_anchor", None) if cam is not None else None
    if not callable(fn):
        return
    fps = job["fps"]
    for it in job["effects"]:
        if it.get("_stage") != "matrix" or it.get("center") is not None:
            continue
        try:
            a = fn(rows, int(math.floor(it["_fs"] * fps)), int(math.ceil(it["_fe"] * fps)))
        except Exception:  # noqa
            a = None
        if a is not None:
            it["_anchor"] = [round(float(a[0]), 5), round(float(a[1]), 5)]
        else:
            it.pop("_anchor", None)


# ================================================================ medios propios
# Imagen o clip del usuario (subido por POST /sources) en un item del plan:
#   item["media"] = {sid, type: image|video, name, w, h, dur}   (+ thumb, lo ignora el motor)
# en graphics.media (PiP), cutaways.photo (la foto de la escena) y cutaways.self_broll (toma a
# pantalla completa). Ajustes del item: `fit` (cover|contain), `focus` [x, y] (punto del medio
# que queda a la vista al recortar; null = centro), `zoom` (1..4 sobre el recorte), `crop`
# [x0, y0, x1, y1] normalizado a la fuente (opcional; no se expone en el inspector), y en video
# `media_in` (s: desde donde se reproduce) y `loop` (repetir [media_in, fin] si el clip es mas
# corto que el item; si no, se congela el ultimo cuadro). El audio del clip no se mezcla.
#
# Resolucion sid -> archivo (en este orden): opts["media_resolver"](media) -> ruta | None;
# opts["media_root"] (carpeta o lista de carpetas; el archivo es <root>/<sid>.<ext>); default
# paths.INPUT/"sources" (la carpeta de POST /sources del servidor).
#
# render normaliza cada medio UNA vez en la cache, al tamano del EXPORT (el draft lo lee
# escalado): imagen -> media_<clave>.png (orientacion EXIF/irot, HEIC en rejilla, perfil de color
# a sRGB, recorte, cabe en la caja de layout); clip -> media_<clave>.mp4 con los cuadros
# [media_in, media_in + duracion del item] a los fps del job (recorte, tonemap HDR) al tamano
# que pide su hueco (tarjeta del PiP, hueco de la foto, o la caja completa en self_broll cover,
# con el recorte del `focus` ya aplicado). Un medio que no se puede leer se recuerda (no se
# reintenta en cada cuadro) y en el export su item se omite (`media:<id>:ilegible`); en /frame
# se dibuja un marcador para que se vea en la edicion. En cada cuadro el compositor inyecta:
#   item["_media_image"]  skia.Image del medio en el tiempo local (sin ajustar al hueco; los
#                         renderers lo ajustan con graphics.fit_src o render.fit_media)
#   self_broll:           item["_src_image"] el medio ya ajustado a la caja de layout
#
# Recursos flotantes del guion (pista fx.media, estilo float_card; motion/media.py): el archivo
# va en el item ({sid, path, kind, fit, speed, src_in}) y se resuelve con float_spec (por sid como
# todo medio; `path` solo si cae dentro de un media_root). Imagen -> el mismo PNG normalizado
# (Ken Burns por cuadro sobre la tarjeta); clip -> media_<clave>.mp4 AL TAMANO DE LA TARJETA con
# el recorte cover y la VELOCIDAD horneados (cuadro k = src_in + k * speed / fps; sin loop: el
# ultimo cuadro se congela). El audio del clip nunca se mezcla (-an).
MEDIA_KINDS = {"graphics": ("media", "cta"), "cutaways": ("photo", "self_broll"), "media": MF.STYLES}
# kinds cuyo medio es OPCIONAL (foto de perfil del CTA Seguir, doc 3.2): solo imagen; sin medio,
# con el archivo ausente o ilegible el item se dibuja igual (iniciales) y nunca se omite
OPTIONAL_MEDIA = {"graphics": ("cta",)}
MEDIA_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".bmp", ".gif",
                   ".tif", ".tiff")
MEDIA_VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")
MEDIA_VERSION = 3          # 3: busqueda sin descarte (VFR) y tope de lectura de las tomas de apoyo
_HEIF_EXT = (".heic", ".heif", ".avif")
# hueco de la foto de cutaways.photo: ancho 0.91*pw y alto 1.104*pw (borde 4.5 % y marco)
PHOTO_ASPECT = 0.91 / 1.104
PHOTO_SIZE, PHOTO_Y = 0.72, 0.46          # defaults de cutaways.photo (tamano y centro)
_SID = __import__("re").compile(r"^[A-Za-z0-9_-]{1,64}$")
_MEDIA_FAILS: dict[str, str] = {}         # clave de fallo -> motivo (por proceso)
_MEDIA_BG: set[str] = set()               # clips normalizandose en segundo plano (/frame)
_MEDIA_LOCK = threading.Lock()


def _media_roots(opts: dict) -> list[Path]:
    r = opts.get("media_root")
    if r is None:
        return [paths.INPUT / "sources"]
    return [Path(x) for x in (r if isinstance(r, (list, tuple)) else [r]) if x]


def media_path(media: Any, opts: dict | None = None) -> Path | None:
    """Archivo local de un medio del plan ({sid, ...}) o None si no existe. Solo por `sid`
    (nunca una ruta que venga en el plan): resolver inyectable o <media_root>/<sid>.<ext>."""
    o = opts or {}
    if not isinstance(media, dict):
        return None
    fn = o.get("media_resolver")
    if callable(fn):
        try:
            p = fn(media)
        except Exception:  # noqa: un resolver roto equivale a "no existe"
            p = None
        return Path(p) if p and Path(p).is_file() else None
    sid = str(media.get("sid") or "").strip()
    if not _SID.match(sid):
        return None
    for root in _media_roots(o):
        for ext in MEDIA_IMAGE_EXT + MEDIA_VIDEO_EXT:
            for e in (ext, ext.upper()):
                p = root / f"{sid}{e}"
                if p.is_file():
                    return p
    return None


def _media_type(media: dict, path: Path) -> str:
    t = str(media.get("type") or "").lower()
    if t in ("image", "video"):
        return t
    return "image" if path.suffix.lower() in MEDIA_IMAGE_EXT else "video"


def _norm_crop(v: Any) -> list[float] | None:
    """[x0, y0, x1, y1] normalizado y valido (lado minimo 5 %), o None (sin recorte)."""
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(c))) for c in v)
    except (TypeError, ValueError):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if x1 - x0 < 0.05 or y1 - y0 < 0.05 or (x0, y0, x1, y1) == (0.0, 0.0, 1.0, 1.0):
        return None
    return [round(x0, 5), round(y0, 5), round(x1, 5), round(y1, 5)]


def _focus(it: dict):
    f = it.get("focus")
    if isinstance(f, (list, tuple)) and len(f) == 2:
        try:
            return min(1.0, max(0.0, float(f[0]))), min(1.0, max(0.0, float(f[1])))
        except (TypeError, ValueError):
            return None
    return None


def media_spec(it: dict, opts: dict) -> dict | None:
    """Datos serializables del medio de un item para el job (`_media`), o None si el item no
    trae medio. {'missing': True} si trae medio pero el archivo no esta."""
    media = it.get("media")
    if not isinstance(media, dict) or not media.get("sid"):
        return None
    p = media_path(media, opts)
    if p is None:
        return {"missing": True}
    kind = it.get("kind")
    foc = _focus(it)
    try:
        mw, mh = int(media.get("w") or 0), int(media.get("h") or 0)
    except (TypeError, ValueError):
        mw = mh = 0
    return {"src": str(p), "type": _media_type(media, p), "kind": kind,
            "crop": _norm_crop(it.get("crop") or media.get("crop")),
            "media_in": max(0.0, _num_or(it.get("media_in"), 0.0)),
            "loop": bool(it.get("loop", True)),
            "fit": "contain" if it.get("fit") == "contain" else "cover",
            "focus": list(foc) if foc else None,
            "zoom": 1.0 if kind == "self_broll" else min(4.0, max(1.0, _num_or(it.get("zoom"), 1.0))),
            "hint_wh": [mw, mh] if mw > 0 and mh > 0 else None}


def _inside(p: Path, roots: list[Path]) -> bool:
    try:
        rp = p.resolve()
    except OSError:
        return False
    for r in roots:
        try:
            rp.relative_to(r.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def project_accent(plan: dict) -> str | None:
    """Acento del proyecto (plan.fx.accent, "#RRGGBB") o None: el "accent" de la paleta del render
    (lo que no trae color propio sale con el; sin acento propio, el del estilo como siempre)."""
    fx = plan.get("fx") if isinstance(plan, dict) and isinstance(plan.get("fx"), dict) else {}
    v = str(fx.get("accent") or "").strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", v)
    return "#" + m.group(1).upper() if m else None


def float_spec(it: dict, opts: dict) -> dict | None:
    """`_media` de un recurso flotante (fx.media) o None si el archivo no esta. Por `sid` (como
    todo medio propio); `path` del plan solo se usa si esta DENTRO de un media_root (nunca una
    ruta arbitraria). Imagenes nunca se tratan como clip."""
    sid = it.get("sid")
    p = media_path({"sid": sid, "type": it.get("kind")}, opts) if isinstance(sid, str) and sid else None
    if p is None and isinstance(it.get("path"), str) and it["path"].strip() \
            and not callable(opts.get("media_resolver")):
        cand = Path(it["path"].strip())
        if cand.is_file() and cand.suffix.lower() in MEDIA_IMAGE_EXT + MEDIA_VIDEO_EXT \
                and _inside(cand, _media_roots(opts)):
            p = cand
    if p is None:
        return None
    typ = "image" if p.suffix.lower() in MEDIA_IMAGE_EXT else "video"
    return {"src": str(p), "type": typ, "kind": "float_card", "crop": None,
            "media_in": max(0.0, _num_or(it.get("src_in"), 0.0)) if typ == "video" else 0.0,
            "loop": False, "fit": "cover", "focus": None, "zoom": 1.0, "hint_wh": None,
            "speed": MF.speed_of(it) if typ == "video" else 1.0}


def _media_box(job: dict) -> tuple[int, int]:
    box = job.get("layout") or (0, 0, job["W"], job["H"])
    return int(box[2]), int(box[3])


def _export_box(job: dict) -> tuple[int, int]:
    """Caja de layout al tamano del EXPORT: los medios se normalizan a ese tamano y el draft
    (mitad de resolucion) lee los mismos archivos escalados (sin normalizar dos veces)."""
    bs = job.get("base_size")
    if bs and int(bs[0]) >= int(job["W"]):
        b = layout_box(int(bs[0]), int(bs[1]))
        return int(b[2]), int(b[3])
    return _media_box(job)


def _media_dims(sw: int, sh: int, crop, maxw: int, maxh: int) -> tuple[int, int]:
    """Tamano normalizado de una imagen: el recorte de la fuente cabe en (maxw, maxh) sin
    ampliar (pares)."""
    x0, y0, x1, y1 = crop or (0.0, 0.0, 1.0, 1.0)
    cw, ch = max(2.0, sw * (x1 - x0)), max(2.0, sh * (y1 - y0))
    s = min(1.0, maxw / cw, maxh / ch)
    return _even(cw * s), _even(ch * s)


def _media_key(m: dict, extra: list) -> str:
    return hashlib.sha1(json.dumps([MEDIA_VERSION, V._file_sig(m["src"]), m["type"], m.get("crop")]
                                   + list(extra), default=str).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- imagenes
def _ffmpeg_png(src) -> bytes:
    """Imagen -> PNG por ffmpeg SIN filtros. Los HEIC del iPhone son una rejilla de teselas que
    ffmpeg une con su propio filtergraph (un -vf choca con el: 'Simple and complex filtering
    cannot be used together'); la rotacion (irot/EXIF) ya sale aplicada."""
    r = subprocess.run([V.FFMPEG, "-v", "error", "-nostdin", "-i", str(src), "-frames:v", "1",
                        "-c:v", "png", "-compression_level", "1", "-pred", "none",
                        "-f", "image2pipe", "pipe:1"], capture_output=True, timeout=180)
    if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG"):
        raise RuntimeError("imagen ilegible: " + r.stderr.decode("utf-8", "replace")[-200:])
    return r.stdout


def _heif_color(src) -> tuple[str, Any] | None:
    """Perfil de color de un HEIC/HEIF (caja 'colr' de ipco): ('icc', bytes) o ('nclx',
    primarias ISO 23091-2). ffmpeg no lo pasa al PNG. Lee solo el inicio (la caja meta)."""
    try:
        with open(src, "rb") as f:
            data = f.read(4 << 20)
    except OSError:
        return None
    i = data.find(b"colrprof")
    if i >= 4:
        size = int.from_bytes(data[i - 4:i], "big")
        icc = data[i + 8:i - 4 + size]
        if size > 12 and len(icc) >= 128:
            return ("icc", icc)
    i = data.find(b"colrnclx")
    if i >= 0 and len(data) >= i + 10:
        return ("nclx", int.from_bytes(data[i + 8:i + 10], "big"))
    return None


# Display P3 (D65) lineal -> sRGB lineal (misma curva de transferencia sRGB)
_P3_TO_SRGB = np.array([[1.2249401, -0.2249404, 0.0], [-0.0420569, 1.0420571, 0.0],
                        [-0.0196376, -0.0786361, 1.0982735]], np.float32)


def _p3_to_srgb(arr: np.ndarray) -> np.ndarray:
    """RGBA uint8 en Display P3 -> sRGB (primarias con recorte de gamut; alfa intacto)."""
    x = np.arange(256, dtype=np.float32) / 255.0
    lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)
    rgb = np.clip(lin[arr[..., :3]] @ _P3_TO_SRGB.T, 0.0, 1.0)
    enc = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
    out = arr.copy()
    out[..., :3] = np.clip(enc * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return out


def _icc_to_srgb(im, icc: bytes | None):
    """PIL Image RGB/RGBA con perfil ICC (Display P3 del iPhone, Adobe RGB...) -> sRGB con
    gestion de color (littlecms). Sin perfil, ya sRGB o si falla, la devuelve igual."""
    if not icc or im.mode not in ("RGB", "RGBA"):
        return im
    try:
        import io
        from PIL import Image, ImageCms
        prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(prof) or "").lower():
            return im
        alpha = im.getchannel("A") if im.mode == "RGBA" else None
        rgb = ImageCms.profileToProfile(im.convert("RGB"), prof, ImageCms.createProfile("sRGB"),
                                        outputMode="RGB")
        if alpha is None:
            return rgb
        out = rgb.convert("RGBA")
        out.putalpha(alpha)
        return out
    except Exception:  # noqa: perfil roto o de otro espacio (gris): sin conversion
        return im


def _open_image(src):
    """(PIL Image con la orientacion aplicada, icc | None, es_p3). PIL para lo que abre; HEIC/
    HEIF/AVIF (sin pillow-heif) y lo que PIL no abra, por ffmpeg en dos pasos."""
    import io
    from PIL import Image, ImageOps
    ext = Path(src).suffix.lower()
    if ext not in _HEIF_EXT:
        try:
            im = Image.open(src)
            im.load()
            icc = im.info.get("icc_profile")
            if im.mode == "CMYK":                  # CMYK con su perfil -> sRGB antes de todo
                if icc:
                    try:
                        from PIL import ImageCms
                        im = ImageCms.profileToProfile(
                            im, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                            ImageCms.createProfile("sRGB"), outputMode="RGB")
                    except Exception:  # noqa
                        im = im.convert("RGB")
                    icc = None
                else:
                    im = im.convert("RGB")
            return ImageOps.exif_transpose(im), icc, False
        except Exception:  # noqa: formato que PIL no abre -> ffmpeg
            pass
    im = Image.open(io.BytesIO(_ffmpeg_png(src)))
    im.load()
    col = _heif_color(src) if ext in _HEIF_EXT else None
    icc = col[1] if col and col[0] == "icc" else None
    return im, icc, bool(col and col[0] == "nclx" and col[1] in (11, 12))


def _decode_image_rgba(src: str, crop, maxw: int, maxh: int) -> np.ndarray:
    """Imagen -> RGBA uint8 sRGB con la orientacion aplicada, recortada y reducida para caber
    en (maxw, maxh). HEIC en rejilla de teselas por ffmpeg (sus dims reales, no las de una
    tesela); perfil ICC o nclx Display P3 convertido a sRGB (skia asume sRGB)."""
    from PIL import Image
    im, icc, p3 = _open_image(src)
    im = im.convert("RGBA")
    sw, sh = im.size
    if not sw or not sh:
        raise RuntimeError("imagen ilegible")
    if crop:
        im = im.crop((int(round(crop[0] * sw)), int(round(crop[1] * sh)),
                      int(round(crop[2] * sw)), int(round(crop[3] * sh))))
    w, h = _media_dims(sw, sh, crop, maxw, maxh)
    if (w, h) != im.size:
        im = im.resize((w, h), Image.LANCZOS)
    im = _icc_to_srgb(im, icc)                  # despues de reducir: barato
    arr = np.ascontiguousarray(np.asarray(im.convert("RGBA"), np.uint8))
    return _p3_to_srgb(arr) if p3 else arr


def decode_image(src, max_w: int = 4096, max_h: int = 4096, crop=None) -> np.ndarray:
    """API publica: imagen del usuario -> RGBA uint8 sRGB (EXIF/irot aplicados, HEIC en
    rejilla, perfil de color) que cabe en (max_w, max_h) sin ampliar."""
    return _decode_image_rgba(str(src), _norm_crop(crop), int(max_w), int(max_h))


def image_size(src) -> tuple[int, int]:
    """(ancho, alto) de DISPLAY de una imagen (orientacion aplicada). Barato (cabecera) con PIL;
    HEIC/HEIF: el tamano de la rejilla completa (ffprobe da el de UNA tesela, p. ej. 512x512)."""
    from PIL import Image
    ext = Path(src).suffix.lower()
    if ext not in _HEIF_EXT:
        try:
            with Image.open(src) as im:
                w, h = im.size
                o = im.getexif().get(0x0112)
            return (h, w) if o in (5, 6, 7, 8) else (w, h)
        except Exception:  # noqa
            pass
    try:
        r = subprocess.run([V.FFPROBE, "-v", "error", "-show_stream_groups", "-show_streams",
                            "-of", "json", str(src)], capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout or "{}")
        w = h = rot = 0

        def rotation(sdl):
            for sd in sdl or []:
                if "rotation" in sd:
                    return int(round(float(sd["rotation"])))
            return 0
        for g in d.get("stream_groups") or []:        # rejilla: dims y rotacion del grupo
            for c in g.get("components") or []:
                if int(c.get("width") or 0) * int(c.get("height") or 0) > w * h:
                    w, h = int(c.get("width") or 0), int(c.get("height") or 0)
                    rot = rotation(c.get("side_data_list"))
        if not w:
            for s in d.get("streams") or []:
                if s.get("codec_type") == "video":
                    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
                    rot = rotation(s.get("side_data_list"))
                    break
        if w and h:
            return (h, w) if abs(rot) % 180 == 90 else (w, h)
    except Exception:  # noqa
        pass
    arr = _decode_image_rgba(str(src), None, 1 << 15, 1 << 15)
    return arr.shape[1], arr.shape[0]


def image_thumb(src, out, width: int = 320, quality: int = 85) -> tuple[int, int]:
    """Miniatura JPEG de una imagen del usuario (EXIF, HEIC en rejilla y color como el
    render). Devuelve las dims de DISPLAY de la imagen completa. Para POST /sources."""
    from PIL import Image
    im, icc, p3 = _open_image(str(src))
    W0, H0 = im.size
    im = im.convert("RGBA")
    w = max(2, min(int(width), W0))
    h = max(2, int(round(H0 * w / W0)))
    im = _icc_to_srgb(im.resize((w, h), Image.LANCZOS), icc)
    arr = np.asarray(im.convert("RGBA"), np.uint8)
    if p3:
        arr = _p3_to_srgb(arr)
    rgb = Image.fromarray(np.ascontiguousarray(arr[..., :3]))
    tmp = V._tmp_path(Path(out))
    rgb.save(str(tmp), "JPEG", quality=int(quality))
    os.replace(tmp, out)
    return W0, H0


# ---------------------------------------------------------------- clips
def _crop_filter(crop) -> list[str]:
    if not crop:
        return []
    x0, y0, x1, y1 = crop
    return [f"crop=w=trunc(iw*{x1 - x0:.5f}/2)*2:h=trunc(ih*{y1 - y0:.5f}/2)*2:"
            f"x=trunc(iw*{x0:.5f}):y=trunc(ih*{y0:.5f})"]


def _pip_card(size: float, asp: float | None, cw: float, ch: float, bw: float) -> tuple[float, float]:
    """Tarjeta del PiP en px (como graphics.media_box): ancho max size*bw, alto max 1.78x ese
    ancho; sin `aspect` toma la proporcion del medio."""
    maxw = min(1.0, max(0.05, size)) * bw
    maxh = maxw * 1.78
    if asp and asp > 0:
        asp = min(3.0, max(0.3, asp))
        w = min(maxw, maxh * asp)
        return w, w / asp
    k = min(maxw / cw, maxh / ch)
    return cw * k, ch * k


def _clip_target(it: dict, m: dict, sw: int, sh: int, box: tuple[int, int]):
    """Tamano (px, pares) y recorte efectivo del clip normalizado segun el hueco donde se
    dibuja, al tamano del export -> (w, h, crop, baked).

    self_broll cover: la caja completa con el recorte del `focus` ya aplicado (baked: el cuadro
    va tal cual, sin ajuste por cuadro); contain: cabe en la caja (se amplia si hace falta).
    photo y graphics.media: lo justo para cubrir (o caber en) su hueco con su `zoom`, sin
    ampliar; el renderer ajusta con focus/zoom sobre el cuadro de la misma proporcion."""
    bw, bh = box
    crop = m.get("crop")
    x0, y0, x1, y1 = crop or (0.0, 0.0, 1.0, 1.0)
    cw, ch = max(2.0, sw * (x1 - x0)), max(2.0, sh * (y1 - y0))
    fit, zoom = m.get("fit", "cover"), float(m.get("zoom") or 1.0)
    kind = m.get("kind")
    if kind == "reel":
        # fragmentos de apoyo: cada toma se recorta al normalizar (_reel_cmd); ventana = la tarjeta,
        # pantalla completa = la caja entera
        if m.get("layout") == "full":
            return _even(bw), _even(bh), None, True
        card = m.get("card") or [MF.CARD_W, RL.ASPECT_DEFAULT]
        _x0, _y0, tw, th = MF.card_px({"w": card[0], "aspect": card[1], "cy": 0.5}, bw, bh)
        return max(16, _even(tw)), max(16, _even(th)), None, True
    if kind in ("float_card", "screen"):
        # recurso flotante / pantalla: la tarjeta completa con el recorte cover centrado ya aplicado
        # (baked; la pantalla tiene la proporcion de su tarjeta: sin recorte)
        card = m.get("card") or [MF.CARD_W, MF.ASPECT]
        x0_, _y0, tw, th = MF.card_px({"w": card[0], "aspect": card[1], "cy": 0.5}, bw, bh)
        sx, sy, sw_, sh_ = MF.cover_rect(cw, ch, tw, th)
        eff = [x0 + sx / sw, y0 + sy / sh, x0 + (sx + sw_) / sw, y0 + (sy + sh_) / sh]
        eff = [round(min(1.0, max(0.0, v)), 5) for v in eff]
        return max(16, _even(tw)), max(16, _even(th)), (None if eff == [0.0, 0.0, 1.0, 1.0] else eff), True
    if kind == "self_broll":
        if fit == "cover":
            r = _fit_rect(cw, ch, bw, bh, "cover", m.get("focus"))
            eff = [x0 + r.x() / sw, y0 + r.y() / sh, x0 + r.right() / sw, y0 + r.bottom() / sh]
            eff = [round(min(1.0, max(0.0, v)), 5) for v in eff]
            return _even(bw), _even(bh), (None if eff == [0.0, 0.0, 1.0, 1.0] else eff), True
        s = min(bw / cw, bh / ch)
        return _even(cw * s), _even(ch * s), crop, False
    if kind == "photo":
        pw = bw * min(0.8, max(0.3, _num_or(it.get("_size0", it.get("size")), PHOTO_SIZE)))
        tw, th = pw * 0.91, pw * 1.104
    else:
        size = _num_or(it.get("_size0", it.get("size")), schema.GRAPHIC_KIND_DEFAULTS["media"]["size"])
        asp = _num_or(it.get("aspect"), 0.0) if it.get("aspect") is not None else 0.0
        tw, th = _pip_card(size, asp, cw, ch, bw)
    s = (max if fit == "cover" else min)(tw / cw, th / ch) * zoom
    s = min(1.0, s)
    return max(16, _even(cw * s)), max(16, _even(ch * s)), crop, False


def _clip_window(m: dict, info: dict, fps: float, n: int) -> tuple[float, int]:
    """(inicio efectivo en la fuente, cuadros utiles del clip desde ahi, <= n). Con `speed`
    (recurso flotante con fit 'speed') cada cuadro de salida avanza speed/fps en la fuente. Una
    grabacion de pantalla sincronizada normaliza TODO su tramo [scr_lo, scr_hi] (los recortes saltan
    dentro de el: _MediaFrames.local_frame elige el cuadro por la sincronia)."""
    dur = float(info.get("duration") or 0.0)
    if m.get("reel"):
        return float(m["reel"][0][2]), n            # cada toma va desde su propio segundo (_reel_cmd)
    if m.get("sync_out"):
        t0 = max(0.0, float(m.get("scr_lo") or 0.0))
        hi = float(m.get("scr_hi") or t0)
        if dur > 0:
            hi = min(hi, dur)
        return t0, max(1, int(math.ceil((hi - t0) * fps - 1e-6)) + 2)
    t0 = float(m.get("media_in") or 0.0)
    sp = float(m.get("speed") or 1.0)
    if dur > 0.2 and t0 >= dur - 0.5 / fps:
        t0 = (t0 % dur) if m.get("loop", True) else max(0.0, dur - 1.0 / fps)
    if dur <= 0:
        return t0, n
    return t0, max(1, min(n, int(math.floor((dur - t0) * fps / sp + 0.5))))


def _reel_crop(crop, sw: float, sh: float, w: int, h: int) -> list[float] | None:
    """Recorte efectivo de una toma de un fragmento: dentro de su recorte (pantalla completa o `win`, o
    el cuadro completo) lo que CUBRE la proporcion w x h, centrado (fracciones; None = todo)."""
    x0, y0, x1, y1 = crop or (0.0, 0.0, 1.0, 1.0)
    cw, ch = max(2.0, sw * (x1 - x0)), max(2.0, sh * (y1 - y0))
    sx, sy, sw_, sh_ = MF.cover_rect(cw, ch, w, h)
    eff = [x0 + sx / sw, y0 + sy / sh, x0 + (sx + sw_) / sw, y0 + (sy + sh_) / sh]
    eff = [round(min(1.0, max(0.0, v)), 5) for v in eff]
    return None if eff == [0.0, 0.0, 1.0, 1.0] else eff


# Busqueda de un clip desde t0 que aguanta fuentes de cuadros VARIABLES (ReplayKit no escribe cuadros
# mientras la pantalla esta quieta: huecos de 0.5-1.3 s o mas). Con la busqueda exacta normal (-ss antes
# de -i) ffmpeg TIRA los cuadros anteriores a t0 y, si t0 cae en un hueco, el clip arranca en el cuadro
# SIGUIENTE: todo lo leido por indice (sync_frame) se corre lo que dure el hueco. Sin descarte
# (-noaccurate_seek) la decodificacion arranca en el cuadro clave anterior (los mismos cuadros que ya
# decodificaba la busqueda exacta, sin costo extra) con t0 en 0, y `fps` con start_time=0 fija la
# reticula en t0: el cuadro 0 es el ultimo cuadro de la fuente en o antes de t0 (lo que se ve en
# pantalla en ese instante) y cada cuadro j el vigente en t0 + j/fps.
_FPS_AT_T0 = "fps=fps={fps}:start_time=0:round=near"


def _seek_t0(t0: float) -> list[str]:
    return ["-noaccurate_seek", "-ss", f"{max(0.0, float(t0)):.6f}"]


def _fit_row(row: list) -> list:
    """Fila del render [t0, t1, src_in, crop, j, src_max] -> fila de reel.fit [t0, t1, src_in, j, src_max]
    (sin src_max en filas viejas: sin tope)."""
    return [row[0], row[1], row[2], row[4] if len(row) > 4 else 0] + list(row[5:6])


def _reel_cmd(m: dict, info: dict, w: int, h: int, fps: float, n: int, out) -> list[str]:
    """ffmpeg de un fragmento de apoyo: cada toma (fila de m["reel"]: [t0, t1, src_in, crop, j, src_max])
    desde su segundo, sus cuadros exactos, recortada y escalada a (w, h), normalizada a SDR bt709; todas
    unidas con cortes secos (concat) -> n cuadros. Nunca lee la fuente en o despues de src_max (el fin de su
    toma: reel.hold_frames): si la toma no alcanza, su ultimo cuadro se queda. Sin -t de entrada: con la
    busqueda sin descarte (_seek_t0) el -t contaria desde el cuadro clave anterior; trim corta y ffmpeg deja
    de leer esa entrada en cuanto su cadena termina."""
    sw, sh = float(info.get("w") or w), float(info.get("h") or h)
    cmd = [V.FFMPEG, "-y", "-v", "error", "-nostdin"]
    hw = V.hwaccel_args(info)
    chains, F0 = [], 0
    rows = m.get("reel") or []
    for j, row in enumerate(rows):
        t0, t1, src_in, crop = row[:4]
        F1 = int(math.floor(t1 * fps + 0.5)) if j < len(rows) - 1 else int(n)
        k = max(1, F1 - F0)
        F0 = F1
        kk = min(k, RL.hold_frames(_fit_row(row), fps))
        cmd += hw + _seek_t0(float(src_in)) + ["-i", str(m["src"])]
        eff = _reel_crop(crop, sw, sh, w, h)
        chain = [_FPS_AT_T0.format(fps=fps)] + _crop_filter(eff) + [
            f"scale={w}:{h}:flags=lanczos+accurate_rnd+full_chroma_int"] + V.tonemap_filters(info) + [
            "setsar=1", f"trim=end_frame={kk}", "setpts=PTS-STARTPTS"]
        # una toma que se acaba antes (fin de su toma o del archivo): su ultimo cuadro se queda hasta completar
        chain += [f"tpad=stop_mode=clone:stop={k}", f"trim=end_frame={k}"]
        chains.append(f"[{j}:v:0]" + ",".join(chain) + f"[v{j}]")
    fc = ";".join(chains) + ";" + "".join(f"[v{j}]" for j in range(len(rows))) + \
        f"concat=n={len(rows)}:v=1:a=0[v]"
    return cmd + ["-filter_complex", fc, "-map", "[v]", "-an", "-frames:v", str(int(n)), "-r", str(fps)] + \
        V.encoder_args(w, h, fps, "inter") + [str(out)]


def _media_video_cmd(m: dict, info: dict, w: int, h: int, fps: float, n: int, t0: float,
                     out, single: bool = False) -> list[str]:
    """ffmpeg: clip del usuario desde t0 -> hasta n cuadros a fps (los que tenga), recortado,
    escalado a (w, h) y normalizado a SDR bt709 (tonemap si es HDR). El loop y el congelado
    del ultimo cuadro los resuelve la lectura (_MediaFrames), no el archivo. Un fragmento de apoyo
    ("reel") une sus tomas (_reel_cmd); el cuadro suelto usa el recorte de la toma que toca."""
    if m.get("reel") and not single:
        return _reel_cmd(m, info, w, h, fps, n, out)
    sp = float(m.get("speed") or 1.0)
    # velocidad horneada (recurso flotante 'speed'): la fuente corre sp veces mas rapido (t0 sigue en 0:
    # sin STARTPTS, que con la busqueda sin descarte seria el cuadro clave anterior)
    pre = [f"setpts=PTS/{sp:.6f}"] if abs(sp - 1.0) > 1e-6 and not single else []
    flags = "lanczos" if m.get("kind") in ("screen", "reel") else "bicubic"    # pantalla: texto nitido al ampliar
    chain = pre + [_FPS_AT_T0.format(fps=fps)] + _crop_filter(m.get("crop_eff", m.get("crop"))) \
        + [f"scale={w}:{h}:flags={flags}+accurate_rnd+full_chroma_int"] + V.tonemap_filters(info)
    if single:
        # misma conversion a RGBA que FrameReader sobre el clip (bt709, rango tv): el cuadro
        # suelto de /frame y el export se ven iguales
        chain += [f"scale={w}:{h}:in_color_matrix=bt709:in_range=tv:"
                  "flags=bicubic+accurate_rnd+full_chroma_int", "format=rgba"]
    # decodificacion por hardware en Mac (HEVC 4K HLG del iPhone: ~3x mas rapido)
    cmd = [V.FFMPEG, "-y", "-v", "error", "-nostdin"] + V.hwaccel_args()
    cmd += _seek_t0(t0) + ["-i", str(m["src"]), "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", ",".join(chain), "-frames:v", str(int(n))]
    if single:
        return cmd + ["-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
    return cmd + ["-r", str(fps)] + V.encoder_args(w, h, fps, "inter") + [str(out)]


def _item_frames(it: dict, fps: float) -> int:
    return max(1, int(math.ceil((it["_fe"] - it["_fs"]) * fps - 1e-6)) + 1)


def _clip_meta(out: Path, w: int, h: int) -> int:
    """Cuadros del clip normalizado en cache (0 si no esta o no coincide)."""
    mp_ = out.with_suffix(out.suffix + ".json")
    if not (out.exists() and mp_.exists()):
        return 0
    try:
        d = json.loads(mp_.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    if int(d.get("W") or 0) != w or int(d.get("H") or 0) != h:
        return 0
    return max(0, int(d.get("frames") or 0))


def _normalize_clip(m: dict, info: dict, out: Path, cancel=None) -> int:
    """Genera el clip normalizado (lock por archivo; otro proceso/hilo que ya lo hizo gana).
    Devuelve sus cuadros."""
    w, h = int(m["nw"]), int(m["nh"])
    with V.FileLock(out, cancel):
        got = _clip_meta(out, w, h)
        if got:
            return got
        tmp = V._tmp_path(out)
        try:
            V.run_ffmpeg_fallback(_media_video_cmd(m, info, w, h, m["fps"], m["n_seg"], m["t0"], tmp),
                                  cancel=cancel)
            got = V.count_frames(tmp)
            if got < 1:
                raise RuntimeError("clip sin cuadros")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, out)
        V._write_meta(out, {"frames": got, "W": w, "H": h, "fps": m["fps"], "req": m["n_seg"]})
    return got


def _fail_key(m: dict) -> str:
    return hashlib.sha1(json.dumps([MEDIA_VERSION, V._file_sig(m["src"]), m["type"]]).encode()) \
        .hexdigest()[:16]


def _known_failure(key: str, cache: Path) -> str | None:
    if key in _MEDIA_FAILS:
        return _MEDIA_FAILS[key]
    f = cache / f"media_{key}.failed"
    if f.exists():
        try:
            msg = f.read_text()[:200] or "ilegible"
        except OSError:
            msg = "ilegible"
        _MEDIA_FAILS[key] = msg
        return msg
    return None


def _remember_failure(key: str, cache: Path, msg: str) -> None:
    """Un medio ilegible no se reintenta (ni por cuadro en /frame ni en cada export): se
    recuerda por firma del archivo (ruta, tamano, mtime) y version de la normalizacion."""
    _MEDIA_FAILS[key] = msg
    try:
        f = cache / f"media_{key}.failed"
        tmp = V._tmp_path(f)
        tmp.write_text(msg)
        os.replace(tmp, f)
    except OSError:
        pass


def _normalize_bg(m: dict, info: dict, out: Path, cache: Path) -> None:
    """/frame con un clip aun sin normalizar: lo normaliza en un hilo de fondo (una vez por
    archivo destino); mientras tanto cada cuadro se decodifica suelto de la fuente."""
    key = str(out)
    with _MEDIA_LOCK:
        if key in _MEDIA_BG:
            return
        _MEDIA_BG.add(key)
    mm = dict(m)

    def run():
        try:
            _normalize_clip(mm, info, out)
        except Exception as e:  # noqa
            _log(f"medio {mm.get('src')}: normalizacion de fondo fallo: {e}")
            _remember_failure(_fail_key(mm), cache, str(e)[:200])
        finally:
            with _MEDIA_LOCK:
                _MEDIA_BG.discard(key)
    threading.Thread(target=run, name="reelfy-media-norm", daemon=True).start()


def prepare_media(job: dict, opts: dict, cancel=None, only_images: bool = False) -> list[str]:
    """Normaliza en la cache los medios de los items del job (una vez; lock por archivo) y
    completa item['_media'] (file, w/h de lectura, n, n_eff, t0...). Devuelve avisos.

    `only_images` (render_frame): los clips sin cache no se esperan; se normalizan en segundo
    plano (opts.media_bg, default True) y el cuadro pedido se decodifica suelto."""
    cache = Path(opts["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    fps = job["fps"]
    ebox = _export_box(job)
    rw_box = _media_box(job)[0]
    scale = rw_box / max(1, ebox[0])
    warns = []
    for it in _media_items(job):
        m = it["_media"]
        fk = _fail_key(m)
        err = _known_failure(fk, cache)
        if err:
            m["failed"] = err
            warns.append(f"media:{it.get('id')}:ilegible")
            continue
        try:
            if m["type"] == "image":
                out = cache / f"media_{_media_key(m, list(ebox))}.png"
                if not out.exists():
                    arr = _decode_image_rgba(m["src"], m.get("crop"), *ebox)
                    with V.FileLock(out, cancel):
                        if not out.exists():
                            from PIL import Image
                            tmp = V._tmp_path(out)
                            Image.fromarray(arr, "RGBA").save(str(tmp), "PNG", compress_level=1)
                            os.replace(tmp, out)
                m.update(file=str(out))
                V.touch(out)
                continue
            info = V.probe(m["src"])
            sw, sh = int(info.get("w") or 0), int(info.get("h") or 0)
            if not sw or not sh:
                raise RuntimeError("clip ilegible")
            nw, nh, crop, baked = _clip_target(it, m, sw, sh, ebox)
            n = _item_frames(it, fps) if not m.get("sync_out") else 1 << 30
            t0, n_seg = _clip_window(m, info, fps, n)
            if m.get("sync_out"):
                n = n_seg
            m.update(nw=nw, nh=nh, w=max(2, _even(nw * scale)), h=max(2, _even(nh * scale)),
                     crop_eff=crop, baked=baked, n=n, n_seg=n_seg, t0=round(t0, 4), fps=fps,
                     info_dur=float(info.get("duration") or 0.0))
            sp = float(m.get("speed") or 1.0)          # velocidad horneada (recurso flotante)
            extra = [nw, nh, fps, n_seg, round(t0, 3), crop] + ([round(sp, 4)] if abs(sp - 1.0) > 1e-6 else []) \
                + ([m.get("reel")] if m.get("reel") else [])
            out = cache / f"media_{_media_key(m, extra)}.mp4"
            got = _clip_meta(out, nw, nh)
            if not got and not only_images:
                got = _normalize_clip(m, info, out, cancel)
            if got:
                m.update(file=str(out), n_eff=got)
                V.touch(out)
            else:
                m["n_eff"] = n_seg
                if opts.get("media_bg", True):
                    _normalize_bg(m, info, out, cache)
        except V.Canceled:
            raise
        except Exception as e:  # noqa: un medio ilegible se omite con aviso (no tumba el render)
            _log(f"medio {it.get('id')} no disponible: {e}")
            m["failed"] = str(e)[:200] or "ilegible"
            _remember_failure(fk, cache, m["failed"])
            warns.append(f"media:{it.get('id')}:ilegible")
    return warns


def _media_items(job: dict) -> list[dict]:
    return [it for tr in MEDIA_KINDS for it in job.get(tr) or []
            if isinstance(it.get("_media"), dict) and not it["_media"].get("missing")]


def drop_failed_media(job: dict) -> list[str]:
    """Quita del job los items cuyo medio no se pudo leer (export: ni el marcador gris del PiP
    ni una escena de foto con la persona y el pie del usuario). Devuelve sus ids."""
    ids = []
    for tr in MEDIA_KINDS:
        keep = []
        for it in job.get(tr) or []:
            m = it.get("_media")
            if isinstance(m, dict) and m.get("failed") and it.get("kind") not in OPTIONAL_MEDIA.get(tr, ()):
                ids.append(str(it.get("id")))
                continue
            keep.append(it)
        job[tr] = keep
    if ids:
        caption_layout(job)
    return ids


def media_signature(job: dict) -> str | None:
    """Firma de lo que produce prepare_media (archivos, recortes, tiempos, huecos), o None si el
    job no tiene medios propios. Cambia al agregar/cambiar un medio; mover el item no."""
    items = _media_items(job)
    if not items:
        return None
    rows = []
    for it in sorted(items, key=lambda x: str(x.get("id"))):
        m = it["_media"]
        rows.append([str(it.get("id")), it.get("kind"), V._file_sig(m["src"]), m["type"], m.get("crop"),
                     round(m.get("media_in", 0.0), 3), m.get("loop"), m.get("fit"), m.get("focus"),
                     m.get("zoom"), it.get("_size0", it.get("size")), it.get("aspect"),
                     round(it["_fe"] - it["_fs"], 3)]
                    + ([m.get("speed"), m.get("card")] if it.get("_float") else [])
                    + ([m.get("sync_out"), m.get("scr_lo"), m.get("scr_hi")] if m.get("sync_out") else []))
    return hashlib.sha1(json.dumps([MEDIA_VERSION, job["fps"], list(_export_box(job)), rows],
                                   default=str).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- ajuste al hueco
def _fit_rect(iw: float, ih: float, dw: float, dh: float, fit: str, focus=None,
              zoom: float = 1.0) -> skia.Rect:
    """Rect FUENTE (en px de la imagen) que se dibuja en un destino dw x dh: cover = recorte
    que llena (centrado en `focus`), contain = la imagen completa (el destino la encaja)."""
    zoom = max(1.0, float(zoom or 1.0))
    if fit == "contain":
        sw, sh = iw / zoom, ih / zoom
    else:
        ar = dw / max(1e-6, dh)
        sw, sh = (ih * ar, ih) if iw / ih > ar else (iw, iw / ar)
        sw, sh = sw / zoom, sh / zoom
    fx, fy = focus if focus else (0.5, 0.5)
    x = min(max(0.0, fx * iw - sw / 2), iw - sw)
    y = min(max(0.0, fy * ih - sh / 2), ih - sh)
    return skia.Rect.MakeXYWH(x, y, sw, sh)


def _sampling(src_w: float, dst_w: float, dst_px: float = 0.0):
    """Muestreo segun la escala. 1:1 o ampliar: lineal (a 1:1 es copia exacta; bicubico en CPU
    cuesta ~30 ms por cuadro de 1080x1920); reducir hasta 2x: bicubico solo en destinos chicos
    (<= 0.4 MP), si no mipmaps; mas de 2x: mipmaps (sin aliasing en fotos de 12 MP)."""
    r = dst_w / max(1e-6, src_w)
    if r >= 0.99:
        return C.SAMPLING_FAST
    if r < 0.5 or dst_px > 400_000:
        return C.SAMPLING
    return C.SAMPLING_CUBIC


def fit_media(img: skia.Image, dw: int, dh: int, fit: str = "cover", focus=None,
              zoom: float = 1.0, fill: str = "blur") -> skia.Image:
    """El medio ajustado a un hueco de dw x dh px: cover (llena recortando) o contain (entero,
    con el resto relleno por la misma imagen desenfocada y oscurecida, calculada a 1/8)."""
    dw, dh = max(2, int(round(dw))), max(2, int(round(dh)))
    s = C.offscreen(dw, dh)
    c = s.getCanvas()
    c.clear(skia.ColorBLACK)
    iw, ih = img.width(), img.height()
    dst = skia.Rect.MakeWH(dw, dh)
    if fit == "contain":
        if fill == "blur":
            sw, sh = max(8, dw // 8), max(8, dh // 8)
            small = C.offscreen(sw, sh)
            sc = small.getCanvas()
            sig = max(1.5, 0.04 * max(sw, sh))
            sc.drawImageRect(img, _fit_rect(iw, ih, sw, sh, "cover"), skia.Rect.MakeWH(sw, sh),
                             C.SAMPLING, skia.Paint(ImageFilter=skia.ImageFilters.Blur(
                                 sig, sig, skia.TileMode.kClamp)))
            c.drawImageRect(small.makeImageSnapshot(), dst, C.SAMPLING_FAST)
            c.drawPaint(skia.Paint(Color=skia.ColorSetARGB(90, 0, 0, 0)))
        src = _fit_rect(iw, ih, dw, dh, "contain", focus, zoom)
        k = min(dw / src.width(), dh / src.height())
        w, h = src.width() * k, src.height() * k
        c.drawImageRect(img, src, skia.Rect.MakeXYWH((dw - w) / 2, (dh - h) / 2, w, h),
                        _sampling(src.width(), w, w * h), None)
    else:
        src = _fit_rect(iw, ih, dw, dh, "cover", focus, zoom)
        c.drawImageRect(img, src, dst, _sampling(src.width(), dw, dw * dh), None)
    return s.makeImageSnapshot()


def _placeholder_image(w: int, h: int) -> skia.Image:
    """Marcador de un medio ilegible (solo /frame: en el export el item se omite): gris oscuro
    con el icono de una foto tachada."""
    w, h = max(8, int(w)), max(8, int(h))
    s = C.offscreen(w, h)
    c = s.getCanvas()
    c.clear(C.color("#2A2A33"))
    u = min(w, h) * 0.16
    cx, cy = w / 2, h / 2
    p = C.stroke("#FFFFFF", max(2.0, u * 0.12), 0.75)
    c.drawRoundRect(skia.Rect.MakeXYWH(cx - u, cy - u * 0.75, 2 * u, 1.5 * u), u * 0.15, u * 0.15, p)
    c.drawCircle(cx - u * 0.4, cy - u * 0.3, u * 0.17, p)
    path = skia.Path()
    path.moveTo(cx - u, cy + u * 0.55)
    path.lineTo(cx - u * 0.2, cy - u * 0.05)
    path.lineTo(cx + u * 0.3, cy + u * 0.35)
    path.lineTo(cx + u * 0.55, cy + u * 0.15)
    path.lineTo(cx + u, cy + u * 0.55)
    c.drawPath(path, p)
    c.drawLine(cx - u * 1.25, cy + u * 1.0, cx + u * 1.25, cy - u * 1.0, p)
    return s.makeImageSnapshot()


class _MediaFrames:
    """Cuadros de los medios del usuario en un proceso: imagenes (PNG normalizado, una vez) y
    clips (lectura SECUENCIAL por item: los chunks son contiguos; un salto reabre el lector).
    El cuadro local k se mapea al clip normalizado: con loop k % n_eff (repite [media_in, fin]),
    sin loop min(k, n_eff - 1) (congela el ultimo)."""

    def __init__(self):
        self.images: dict[str, skia.Image] = {}
        self.readers: dict[str, list] = {}      # id -> [FrameReader, siguiente k, ultimo k, img]
        self.fitted: dict[tuple, skia.Image] = {}
        self.placeholders: dict[tuple, skia.Image] = {}

    @staticmethod
    def sync_frame(m: dict, lt: float, fps: float) -> int:
        """Grabacion de pantalla sincronizada: el cuadro del clip normalizado (desde scr_lo, a fps) que
        corresponde al cuadro final fs + lt (fila de `sync_out` que lo contiene; en una union gana la
        fila siguiente, como Timeline.inverse)."""
        T_ = float(m.get("fs") or 0.0) + lt
        rows = m.get("sync_out") or []
        hit = None
        for r in rows:
            if r[0] - 1e-6 <= T_ < r[1] - 1e-6:
                hit = r
                break
        if hit is None:
            hit = min(rows, key=lambda r: 0.0 if r[0] <= T_ <= r[1] else min(abs(T_ - r[0]), abs(T_ - r[1])))
        tt = min(max(T_, hit[0]), hit[1])
        s_ = hit[2] + float(m.get("rate") or 1.0) * (tt - hit[0])
        j = int(math.floor((s_ - float(m.get("t0") or 0.0)) * fps + 0.5))
        return min(max(0, j), max(0, int(m.get("n_eff") or m.get("n") or 1) - 1))

    @staticmethod
    def local_frame(m: dict, lt: float, fps: float) -> int:
        if m.get("sync_out"):
            return _MediaFrames.sync_frame(m, lt, fps)
        k = min(max(0, int(math.floor(lt * fps + 1e-6))), max(0, int(m.get("n") or 1) - 1))
        ne = max(1, int(m.get("n_eff") or m.get("n") or 1))
        return (k % ne) if m.get("loop", True) else min(k, ne - 1)

    def image(self, it: dict, lt: float, fps: float) -> skia.Image | None:
        m = it.get("_media") or {}
        if m.get("failed") or m.get("missing"):
            return None
        if m["type"] == "image":
            f = m.get("file")
            if not f:
                return None
            img = self.images.get(f)
            if img is None:
                img = skia.Image.MakeFromEncoded(skia.Data.MakeFromFileName(f))
                if img is None:
                    return None
                if len(self.images) > 8:
                    self.images.pop(next(iter(self.images)))
                self.images[f] = img
            return img
        k = self.local_frame(m, lt, fps)
        iid = str(it.get("id"))
        rd = self.readers.get(iid)
        if rd is not None and rd[2] == k and rd[3] is not None:
            return rd[3]                            # mismo cuadro (congelado / pedido repetido)
        if m.get("file"):
            if rd is None or rd[0] is None or rd[1] != k:
                if rd is not None and rd[0] is not None:
                    rd[0].close()
                rd = [V.FrameReader(m["file"], m["w"], m["h"], k, None, fps), k, None, None]
                self.readers[iid] = rd
            arr = rd[0].read()
            if arr is None:
                return rd[3]
            rd[1], rd[2], rd[3] = k + 1, k, C.image_from_rgba(arr)
            return rd[3]
        # sin clip en cache (cuadro suelto de /frame): un cuadro directo de la fuente
        try:
            info = V.probe(m["src"])
            w, h = int(m["w"]), int(m["h"])
            t_src = m.get("t0", 0.0) + k * float(m.get("speed") or 1.0) / fps
            mm = m
            if m.get("sync_out"):
                t_src = m.get("t0", 0.0) + k / fps
            if m.get("reel"):
                # la toma que toca en el cuadro local k, con su recorte
                rows = m["reel"]
                lt = k / fps
                j = RL.piece_at(rows, lt)
                r0 = rows[j]
                t_src = RL.src_cap(_fit_row(r0), float(r0[2]) + min(max(lt, r0[0]), r0[1]) - r0[0], fps)
                mm = dict(m, crop_eff=_reel_crop(r0[3], float(info.get("w") or w), float(info.get("h") or h),
                                                 int(m.get("nw") or w), int(m.get("nh") or h)))
            r = subprocess.run(_media_video_cmd(mm, info, w, h, fps, 1, t_src, None, single=True),
                               capture_output=True)
            if len(r.stdout) < w * h * 4:
                return None
            img = C.image_from_rgba(np.frombuffer(r.stdout[:w * h * 4], np.uint8).reshape(h, w, 4).copy())
            self.readers[iid] = [None, -1, k, img]
            return img
        except Exception:  # noqa
            return None

    def fitted_image(self, it: dict, img: skia.Image, dw: int, dh: int, fit: str) -> skia.Image:
        """fit_media con cache para imagenes (los clips cambian por cuadro)."""
        m = it.get("_media") or {}
        key = (m.get("file"), dw, dh, fit, json.dumps(it.get("focus")), it.get("zoom")) \
            if m.get("type") == "image" else None
        if key is not None and key in self.fitted:
            return self.fitted[key]
        out = fit_media(img, dw, dh, fit, _focus(it), _num_or(it.get("zoom"), 1.0))
        if key is not None:
            if len(self.fitted) > 16:
                self.fitted.pop(next(iter(self.fitted)))
            self.fitted[key] = out
        return out

    def placeholder(self, w: int, h: int) -> skia.Image:
        k = (int(w), int(h))
        if k not in self.placeholders:
            self.placeholders[k] = _placeholder_image(w, h)
        return self.placeholders[k]

    def release(self, active_ids: set) -> None:
        for iid in [i for i in self.readers if i not in active_ids]:
            rd = self.readers.pop(iid)
            if rd[0] is not None:
                rd[0].close()

    def close(self) -> None:
        self.release(set())


# ---------------------------------------------------------------- colocacion automatica del PiP
PIP_MARGIN = 0.015                  # separacion minima (fraccion de la caja)
# si no cabe centrado sin tapar la cara, la tarjeta se achica (antes de rendirse)
PIP_SCALES = (1.0, 0.85, 0.72, 0.6, 0.5, 0.4)
CAPTION_HALF = 0.05                 # media altura de la franja de subtitulos (fraccion de H)


def _overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def media_auto_place(face, w: float, h: float, cap_top: float | None = None,
                     avoid=()) -> tuple[float, float, float]:
    """Centro (x, y) y escala de un PiP SIN posicion (x/y null), en fracciones de la caja de
    layout. Regla de simetria: el PiP automatico SIEMPRE va centrado en x = 0.5 (mismo aire a
    izquierda y derecha); para librar la cara se mueve en y o se achica, nunca de lado.
    Contrato compartido con la vista en vivo del Studio (fxcore.mediaAutoPlace, doc 4.2b); el
    algoritmo EXACTO (el port JS debe ser identico paso a paso):

      S = ctx.SAFE (0.12..0.88, simetrica); m = PIP_MARGIN = 0.015; mid = 0.5
      PIP_SCALES = (1.0, 0.85, 0.72, 0.6, 0.5, 0.4)
      bottom = min(S.bottom, cap_top) si cap_top else S.bottom
      fit(cx, cy, ww, hh):
        lo_x, hi_x = S.left + ww/2 + m, S.right - ww/2 - m
        lo_y, hi_y = S.top + hh/2 + m, bottom - hh/2 - m
        cx = mid si lo_x > hi_x, si no clamp(cx, lo_x, hi_x)
        cy = (S.top + bottom)/2 si lo_y > hi_y, si no clamp(cy, lo_y, hi_y)
      con cara [fx, fy, fw, fh]:
        zone = (fx - fw/2 - 0.03, fy - fh/2 - 0.05, fx + fw/2 + 0.03, fy + fh/2 + 0.03)
        candidatos(ww, hh) = [(mid, zone[3] + m + hh/2),     # pecho (bajo la barbilla)
                              (mid, zone[1] - m - hh/2)]     # arriba de la cabeza
      sin cara: zone = None; candidatos = [(mid, 0.0), (mid, 1.0)]  # arriba, abajo (fit acota)
      for s in PIP_SCALES: ww, hh = w*s, h*s
        for (cx, cy) in candidatos(ww, hh): cx, cy = fit(cx, cy, ww, hh)
          r = (cx - ww/2, cy - hh/2, cx + ww/2, cy + hh/2)
          fo = overlap(r, zone) (0 sin cara); ao = suma de overlap(r, a) para a en avoid
          fo <= 1e-9 y ao <= 1e-9 -> return (round(cx, 4), round(cy, 4), s)
          fo <= 1e-9 y (free es None o ao < free.ao - 1e-9) -> free = (ao, cx, cy, s)
          best_face es None o fo < best_face.fo - 1e-9 -> best_face = (fo, cx, cy, s)
      pick = free o best_face -> (round(cx, 4), round(cy, 4), s)

    - `face` = [cx, cy, w, h] de la cara (mediana en el rango del item) o None; `w`, `h` = la
      tarjeta; `cap_top` = borde superior de la franja de subtitulos (y - 0.06) o None;
      `avoid` = rectangulos [x0, y0, x1, y1] de otros overlays activos (textos, graficos).
    - Zona prohibida = la cara ampliada (0.03 a los lados, 0.05 arriba por el cabello, 0.03
      abajo); zona util = SAFE y arriba de los subtitulos."""
    S = C.SAFE
    m = PIP_MARGIN
    bottom = min(S["bottom"], cap_top) if cap_top else S["bottom"]
    mid = C.XC
    avoid = [tuple(float(v) for v in r[:4]) for r in avoid or ()]

    def fit(cx, cy, ww, hh):
        lo_x, hi_x = S["left"] + ww / 2 + m, S["right"] - ww / 2 - m
        lo_y, hi_y = S["top"] + hh / 2 + m, bottom - hh / 2 - m
        cx = mid if lo_x > hi_x else min(max(cx, lo_x), hi_x)
        cy = (S["top"] + bottom) / 2 if lo_y > hi_y else min(max(cy, lo_y), hi_y)
        return cx, cy

    if face is None:
        zone = None
        order = lambda ww, hh: [(mid, 0.0), (mid, 1.0)]  # noqa: E731
    else:
        fx, fy, fw, fh = (float(v) for v in face[:4])
        zone = (fx - fw / 2 - 0.03, fy - fh / 2 - 0.05, fx + fw / 2 + 0.03, fy + fh / 2 + 0.03)

        def order(ww, hh):
            return [(mid, zone[3] + m + hh / 2), (mid, zone[1] - m - hh / 2)]
    free, best_face = None, None
    for s in PIP_SCALES:
        ww, hh = w * s, h * s
        for cx, cy in order(ww, hh):
            cx, cy = fit(cx, cy, ww, hh)
            r = (cx - ww / 2, cy - hh / 2, cx + ww / 2, cy + hh / 2)
            fo = _overlap(r, zone) if zone else 0.0
            ao = sum(_overlap(r, a) for a in avoid)
            if fo <= 1e-9 and ao <= 1e-9:
                return round(cx, 4), round(cy, 4), s
            if fo <= 1e-9 and (free is None or ao < free[0] - 1e-9):
                free = (ao, cx, cy, s)
            if best_face is None or fo < best_face[0] - 1e-9:
                best_face = (fo, cx, cy, s)
    pick = free or best_face
    return round(pick[1], 4), round(pick[2], 4), pick[3]


def _overlay_boxes(job: dict, fs: float, fe: float, mods: dict | None) -> list[tuple]:
    """Rectangulos aproximados (fracciones de la caja) de los textos y graficos con posicion
    activos en [fs, fe): el PiP automatico los evita. Texto = franja de 0.16 de alto en su `y`
    (o la del preset) a lo ancho de SAFE; grafico = cuadrado de `size` en su x/y."""
    S = C.SAFE
    bw, bh = _media_box(job)
    tx = (mods or {}).get("text")
    presets = getattr(tx, "PRESETS", {}) if tx is not None else {}
    out = []
    for it in job.get("text") or []:
        if it["_fe"] <= fs or it["_fs"] >= fe:
            continue
        y = it.get("y")
        if y is None:
            y = (presets.get(it.get("preset")) or {}).get("y")
        y = _num_or(y, 0.3)
        out.append((S["left"], y - 0.08, S["right"], y + 0.08))
    for it in job.get("graphics") or []:
        if it.get("kind") in ("media", "progress") or it["_fe"] <= fs or it["_fs"] >= fe:
            continue
        if it.get("x") is None or it.get("y") is None:
            continue
        half = _num_or(it.get("size"), 0.18) / 2
        x, y = _num_or(it["x"], 0.5), _num_or(it["y"], 0.5)
        out.append((x - half, y - half * bw / bh, x + half, y + half * bw / bh))
    return out


def _media_src_wh(it: dict) -> tuple[float, float] | None:
    """Dims de display del medio (con su recorte) para la tarjeta: el plan (media.w/h de POST
    /sources) o el archivo."""
    m = it.get("_media") or {}
    wh = None
    try:
        if m.get("type") == "image":
            wh = image_size(m["src"])
        else:
            info = V.probe(m["src"])
            wh = (int(info.get("w") or 0), int(info.get("h") or 0))
    except Exception:  # noqa: el del plan como respaldo
        wh = None
    if not wh or not wh[0] or not wh[1]:
        wh = m.get("hint_wh")
    if not wh or not wh[0] or not wh[1]:
        return None
    x0, y0, x1, y1 = m.get("crop") or (0.0, 0.0, 1.0, 1.0)
    return float(wh[0]) * (x1 - x0), float(wh[1]) * (y1 - y0)


def set_media_auto(job: dict, rows: np.ndarray | None, mods: dict | None = None) -> None:
    """graphics.media sin x/y: colocacion automatica lejos de la cara (media_auto_place) con la
    mediana de la cara en el rango del item. Escribe x, y y `size` (si se achico) en el item del
    job; `_size0` guarda el tamano pedido (la normalizacion del clip no depende de la cara)."""
    items = [it for it in job.get("graphics") or []
             if it.get("kind") == "media" and (it.get("x") is None or it.get("y") is None or it.get("_auto_xy"))]
    if not items:
        return
    fps, N = job["fps"], job["n_frames"]
    box = tuple(job.get("layout") or (0, 0, job["W"], job["H"]))
    bw, bh = box[2], box[3]
    cap = job.get("captions") or {}
    cap_top = (_num_or(cap.get("y"), 0.72) - CAPTION_HALF - 0.01) if job.get("captions_on") else None
    for it in items:
        if it.get("_auto_xy"):                      # recalculo (otra pista de rostro)
            it["x"], it["y"] = it["_xy0"]
            it["size"] = it["_size0"]
        size = _num_or(it.get("size"), schema.GRAPHIC_KIND_DEFAULTS["media"]["size"])
        wh = _media_src_wh(it)
        cw, ch = wh if wh else (4.0, 5.0)
        asp = _num_or(it.get("aspect"), 0.0) if it.get("aspect") is not None else 0.0
        pw_, ph_ = _pip_card(size, asp, cw, ch, bw)
        # marco blanco y sombra: ~3 % mas por lado
        w_n, h_n = pw_ * 1.06 / bw, ph_ * 1.06 / bh
        face = None
        if rows is not None:
            f0 = int(math.floor(it["_fs"] * fps))
            f1 = min(N, max(f0 + 1, int(math.ceil(it["_fe"] * fps))))
            seg = rows[max(0, f0):f1]
            ok = seg[~np.isnan(seg).any(axis=1)] if len(seg) else seg
            if len(ok):
                face = _box_face(np.median(ok, axis=0), job["W"], job["H"], box)
        cx, cy, s = media_auto_place(face, w_n, h_n, cap_top,
                                     _overlay_boxes(job, it["_fs"], it["_fe"], mods))
        if it.get("x") is not None or it.get("y") is not None:
            s = 1.0                                 # solo falta una coordenada: no se achica
        it["_xy0"] = [it.get("x"), it.get("y")]
        it["_size0"] = size
        it["_auto_xy"] = True
        it["x"] = cx if it.get("x") is None else it["x"]
        it["y"] = cy if it.get("y") is None else it["y"]
        it["size"] = round(size * s, 4)


# ---------------------------------------------------------------- subtitulos y foto con pie
def _photo_caption_text(it: dict) -> str:
    cap = it.get("caption")
    if cap is None and isinstance(it.get("text"), str):
        cap = it["text"]
    return str(cap or "").strip() if not isinstance(cap, (dict, list, bool)) else ""


def photo_caption_y(it: dict, cap_y: float, box_aspect: float = 9 / 16) -> float | str | None:
    """Donde van los subtitulos durante una escena photo con pie (misma geometria que
    cutaways.draw_photo, fracciones de la caja): None = el pie no toca la franja de los
    subtitulos (se quedan); un y = se mueven ahi; 'hide' = no hay lugar (se ocultan).

    pw = size*ancho; marco de alto 1.349 pw centrado en `y` (0.46); el pie escrito a mano va a
    0.559 pw bajo el centro con media altura ~0.075 pw (+0.015 por la inclinacion). Si choca,
    los subtitulos van (en orden): debajo del marco, sobre la parte baja de la foto (arriba del
    borde blanco del pie) o arriba del marco, siempre dentro de SAFE."""
    S = C.SAFE
    pw = min(0.8, max(0.3, _num_or(it.get("size"), PHOTO_SIZE))) * box_aspect
    y = _num_or(it.get("y"), PHOTO_Y)
    c_mid, c_half = y + 0.559 * pw, 0.075 * pw + 0.015
    if abs(c_mid - cap_y) >= c_half + CAPTION_HALF:
        return None
    half, bottom = 0.6745 * pw, 0.2 * pw
    lo, hi = S["top"] + CAPTION_HALF, S["bottom"] - CAPTION_HALF + 0.03
    below = y + half + 0.02 + CAPTION_HALF + 0.01
    over = y + half - bottom - 0.012 - CAPTION_HALF
    above = y - half - 0.035 * pw - 0.02 - CAPTION_HALF - 0.01
    if lo <= below <= hi:
        return round(below, 4)
    # sobre la foto: la franja completa dentro del hueco de la imagen (no sobre el pie)
    if lo <= over <= hi and over - CAPTION_HALF >= y - half + 0.2 * pw:
        return round(over, 4)
    if lo <= above <= hi:
        return round(above, 4)
    return "hide"


def caption_layout(job: dict) -> None:
    """Rangos donde los subtitulos se ocultan (`_hide`: titulos y escenas con texto) o se
    MUEVEN (`y_ranges` internos, `_auto`: escenas photo con pie que chocaria con ellos). Se
    recalcula si el job cambia (p. ej. al omitir items con un medio ilegible)."""
    cap = job["captions"]
    hide = []
    if cap.get("hide_during_titles", True):
        hide += [(it["_fs"], it["_fe"]) for it in job["text"] if not it.get("keep_captions")]
        hide += [(it["_fs"], it["_fe"]) for it in job["cutaways"]
                 if isinstance(it.get("text"), dict) and str(it["text"].get("text") or "").strip()]
    user = [r for r in (cap.get("y_ranges") or []) if isinstance(r, dict) and not r.get("_auto")]
    auto = []
    bw, bh = _media_box(job)
    for it in job["cutaways"]:
        if it.get("kind") != "photo" or it.get("keep_captions") or not _photo_caption_text(it):
            continue
        t0 = _num_or(it.get("start"), 0.0)
        cy = _num_or(cap.get("y"), 0.72)
        for r in user:                          # altura por tramo del usuario en ese momento
            try:
                if float(r["start"]) <= t0 < float(r["end"]):
                    cy = float(r["y"])
                    break
            except (KeyError, TypeError, ValueError):
                continue
        v = photo_caption_y(it, cy, bw / bh)
        if v == "hide":
            hide.append((it["_fs"], it["_fe"]))
        elif v is not None:
            auto.append({"start": t0, "end": _num_or(it.get("end"), t0), "y": v, "_auto": True,
                         "_id": it.get("id")})
    cap["_hide"] = _merge_ranges(hide)
    cap["y_ranges"] = auto + user


# ================================================================ compositor por cuadro
def _face_rows(face: np.ndarray | None, n_frames: int, max_gap: int = 15) -> np.ndarray | None:
    """Pista de rostro -> (n_frames, 4); huecos sin cara de hasta `max_gap` cuadros se
    interpolan (parpadeos del detector), los largos quedan NaN (sin cara)."""
    if face is None:
        return None
    f = np.asarray(face, np.float64)
    if f.ndim != 2 or f.shape[1] < 4 or len(f) == 0:
        return None
    f = f[:, :4]
    if len(f) != n_frames:                      # muestreado: al cuadro mas cercano
        src = np.clip(np.round(np.linspace(0, len(f) - 1, n_frames)).astype(int), 0, len(f) - 1)
        f = f[src]
    ok = ~np.isnan(f).any(axis=1)
    if ok.all() or not ok.any():
        return f
    idx = np.arange(len(f))
    out = f.copy()
    for j in range(4):
        out[:, j] = np.interp(idx, idx[ok], f[ok, j])
    i = 0
    while i < len(f):                           # huecos largos -> sin cara
        if ok[i]:
            i += 1
            continue
        k = i
        while k < len(f) and not ok[k]:
            k += 1
        if k - i > max_gap:
            out[i:k] = np.nan
        i = k
    return out


class _Grain:
    """Grano/dither final de luma (+-2 niveles, distribucion triangular) sobre el cuadro
    compuesto: rompe las bandas de los degradados oscuros de escenas y overlays (y ayuda al
    encoder a no hacer bloques) y da la cohesion de pelicula que el grano del grade solo da al
    video. Teselas precalculadas + desplazamiento determinista por cuadro: ~1 ms."""

    def __init__(self, W: int, H: int, amount: float = 1.0):
        rng = np.random.default_rng(90210)
        hh, ww = H + GRAIN_PAD, W + GRAIN_PAD
        amp = max(1, int(round(2 * min(1.5, max(0.0, amount)))))
        k = amp // 2 + (amp % 2)
        noise = rng.integers(-k, k + 1, (hh, ww), dtype=np.int16) + \
            rng.integers(-(amp - k), amp - k + 1, (hh, ww), dtype=np.int16)
        self.pos = np.zeros((hh, ww, 4), np.uint8)
        self.neg = np.zeros((hh, ww, 4), np.uint8)
        self.pos[..., :3] = np.clip(noise, 0, 255).astype(np.uint8)[..., None]
        self.neg[..., :3] = np.clip(-noise, 0, 255).astype(np.uint8)[..., None]
        self.W, self.H = W, H

    def apply(self, arr: np.ndarray, n: int) -> None:
        import cv2
        dy, dx = (n * 37 + 11) % GRAIN_PAD, (n * 61 + 29) % GRAIN_PAD
        cv2.add(arr, self.pos[dy:dy + self.H, dx:dx + self.W], dst=arr)
        cv2.subtract(arr, self.neg[dy:dy + self.H, dx:dx + self.W], dst=arr)


def _draw_full(cv, img, bw: int, bh: int, vignette: float = 0.0) -> None:
    """Toma de apoyo a pantalla completa (ya normalizada al tamano de la caja) + vineta opcional."""
    if img is not None:
        if img.width() == bw and img.height() == bh:
            cv.drawImage(img, 0, 0)
        else:
            cv.drawImageRect(img, skia.Rect.MakeWH(bw, bh), C.SAMPLING_CUBIC)
    v = max(0.0, min(1.0, float(vignette or 0.0)))
    if v > 1e-3:
        p = skia.Paint(Shader=skia.GradientShader.MakeRadial(
            skia.Point(bw / 2, bh * 0.45), bh * 0.75, [skia.Color4f(0, 0, 0, 0).toColor(),
                                                       skia.Color4f(0, 0, 0, 0.55 * v).toColor()], [0.33, 1.0]))
        cv.drawRect(skia.Rect.MakeWH(bw, bh), p)


class Compositor:
    """Compone cuadros finales segun el orden normativo 4.3. Uno por proceso."""

    def __init__(self, job: dict, mods: dict | None = None, grader=None, matte=None,
                 face: np.ndarray | None = None, base_video: str | None = None,
                 frame_source=None):
        self.job = job
        self.W, self.H, self.fps = job["W"], job["H"], job["fps"]
        self.mods = mods if mods is not None else load_modules(job.get("modules"),
                                                               names=COMPOSE_MODULES, quiet=True)
        self.reg = registries(self.mods)
        self.tl = T.Timeline.from_dict(job["timeline"])
        self.grader, self.matte = grader, matte
        self.face = _face_rows(face, job["n_frames"]) if face is not None else None
        self.base_video = base_video
        self.frame_source = frame_source          # k -> RGBA (render_frame sin base)
        self.arr, self.surf = C.new_frame(self.W, self.H)
        self.canvas = self.surf.getCanvas()
        self.cam_surf = C.offscreen(self.W, self.H)
        self.a8_info = skia.ImageInfo.MakeA8(self.W, self.H)
        self._mask_ctx = C.FrameCtx(W=self.W, H=self.H, fps=self.fps, quality=job["quality"])
        self.box = tuple(job.get("layout") or layout_box(self.W, self.H))
        self.boxed = self.box != (0, 0, self.W, self.H)
        self._box_surf = C.offscreen(self.box[2], self.box[3]) if self.boxed else None
        self.errors: dict[str, int] = {}
        self.prev_store: dict[int, skia.Image] = {}
        self.prev_needed = {t["_P"] for t in job["transitions"] if t["_P"] >= 0}
        self.shot_starts = [s["start"] for s in job["shots"]]
        self.matte_ranges = [tuple(r) for r in job.get("matte_ranges") or []]
        self.matte_box = job.get("matte_box")
        self.t_comp = 0.0
        self.prof: dict[str, float] = {}       # s acumulados por etapa/kind (perfil)
        self._logo = self._prep_logo(job.get("logo"))
        # logos / fotos que graphics lee por sid (graphics.media_file_image): mismo media_root que
        # los `media` del job (uno por proceso, como el compositor)
        gm = self.mods.get("graphics")
        if gm is not None and hasattr(gm, "MEDIA_OPTS"):
            root = job.get("media_root")
            gm.MEDIA_OPTS = {"media_root": list(root)} if root else {}
        dith = float(job.get("dither", 0.0) or 0.0)
        self._grain = _Grain(self.W, self.H, dith) if dith > 0 and job["quality"] != "draft" else None
        z = lambda it: (int(it.get("z", 0) or 0), it.get("_ord", 0))  # noqa: E731
        self.text = sorted(job["text"], key=z)
        self.graphics = sorted([g for g in job["graphics"] if g.get("kind") != "progress"], key=z)
        self.progress = [g for g in job["graphics"] if g.get("kind") == "progress"]
        self.cutaways = sorted(job["cutaways"], key=z)
        self.effects = job["effects"]
        self.transitions = sorted(job["transitions"], key=lambda t: t["_F0"])
        self.media = _MediaFrames()
        self.media_items = _media_items(job)
        self.float_media = sorted(job.get("media") or [], key=z)

    def _float_media(self, items: list, t: float) -> None:
        """Recursos flotantes del guion (fx.media, float_card): el cuadro compuesto hasta aqui se
        desenfoca y oscurece (lo que marque el recurso mas visible) y encima va cada tarjeta, en
        la caja de layout. Van ANTES de graficos, textos y subtitulos (estos quedan encima)."""
        ta = time.perf_counter()
        bw, bh = self.box[2], self.box[3]
        states = []
        for it in items:
            if it.get("_media_image") is None and not it.get("_media_placeholder"):
                continue                          # sin cuadro que mostrar: ni tarjeta ni fondo
            lt, dur = t - it["_fs"], it["_fe"] - it["_fs"]
            if it.get("_reel"):
                st = RL.anim(lt, dur, it.get("_motion") or "cut")
                if it.get("_layout") == "full":
                    st["bg"] = 0.0                    # la toma cubre todo: sin fondo desenfocado
            elif it.get("_screen"):
                st = SC.anim(lt, dur, it.get("_motion") or "cut")
            else:
                st = MF.card_anim(lt, dur, MF.param(it, "in_dur"), MF.param(it, "out_dur"),
                                  MF.param(it, "float"))
            k = _num_or(it.get("intensity"), 1.0)
            st["alpha"] *= min(1.0, max(0.0, k))
            states.append((it, st, lt, dur))
        if not states:
            return
        top = max(states, key=lambda s: s[1]["bg"])
        amt = top[1]["bg"] * min(1.0, max(0.0, _num_or(top[0].get("intensity"), 1.0)))
        P = RL.param if top[0].get("_reel") else SC.param if top[0].get("_screen") else MF.param
        cv = self.canvas
        try:
            if amt > 0.003:
                snap = self.surf.makeImageSnapshot()
                MF.draw_background(cv, snap, self.W, self.H, amt, P(top[0], "blur"), P(top[0], "dim"))
            for it, st, lt, dur in states:
                m = it.get("_media") or {}
                img = it.get("_media_image")
                baked = bool(it.get("_media_baked")) or (m.get("type") == "video" and m.get("baked"))
                cv.save()
                if self.boxed:
                    cv.translate(self.box[0], self.box[1])
                try:
                    if it.get("_reel") and it.get("_layout") == "full":
                        _draw_full(cv, img, bw, bh, _num_or(RL.param(it, "vignette"), 0.0))
                    elif it.get("_reel"):
                        MF.draw_card(cv, img, bw, bh, it["_card"], st, RL.look(it), lt, dur, baked=True)
                    elif it.get("_screen"):
                        MF.draw_card(cv, img, bw, bh, it["_card"], st, SC.look(it), lt, dur, baked=baked)
                    else:
                        MF.draw_card(cv, img, bw, bh, it["_card"], st, it, lt, dur, baked=baked)
                finally:
                    cv.restoreToCount(1)
        except Exception as e:  # noqa
            self._err("media:float_card", e)
        finally:
            self._prof("media:float_card", ta)

    def _inject_media(self, t: float) -> None:
        """Medios propios activos en t: item['_media_image'] (el cuadro del medio en su tiempo
        local, sin ajustar al hueco: los renderers lo ajustan) y, en self_broll, `_src_image` ya
        ajustado a la caja (el clip cover viene horneado a la caja: sin ajuste por cuadro).
        Medio ilegible con `_media_placeholder` (solo /frame): un marcador. Cierra los lectores
        de clips que ya no estan activos."""
        active = set()
        bw, bh = self.box[2], self.box[3]
        for it in self._active(self.media_items, t):
            iid = str(it.get("id"))
            active.add(iid)
            kind = it.get("kind")
            m = it["_media"]
            ta = time.perf_counter()
            try:
                if m.get("failed"):
                    it["_media_image"] = None
                    if it.get("_media_placeholder") and kind == "photo":
                        it["_media_image"] = self.media.placeholder(round(bw * 0.52), round(bw * 0.63))
                    elif it.get("_media_placeholder") and kind == "self_broll":
                        it["_src_image"] = self.media.placeholder(bw, bh)
                    elif it.get("_media_placeholder") and it.get("_float"):
                        _x, _y, cw_, ch_ = MF.card_px(it["_card"], bw, bh)
                        it["_media_image"] = self.media.placeholder(cw_, ch_)
                        it["_media_baked"] = True
                    continue
                img = self.media.image(it, t - it["_fs"], self.fps)
                it["_media_image"] = img
                if img is not None and kind == "self_broll":
                    it["_src_image"] = img if m.get("baked") else self.media.fitted_image(
                        it, img, bw, bh, m.get("fit", "cover"))
            except Exception as e:  # noqa
                self._err(f"media:{kind}", e)
                it["_media_image"] = None
            finally:
                self._prof("media", ta)
        self.media.release(active)

    # ------------------------------------------------------------ utilidades
    def _err(self, key: str, e: BaseException) -> None:
        if self.job.get("strict"):
            raise e
        c = self.errors.get(key, 0)
        self.errors[key] = c + 1
        if c == 0:
            _log(f"renderer {key} fallo: {type(e).__name__}: {e}")
            traceback.print_exc(limit=3)

    @staticmethod
    def _active(items: list, t: float) -> list:
        return [it for it in items if it["_fs"] - 1e-9 <= t < it["_fe"] - 1e-9]

    def _prep_logo(self, cfg: dict | None):
        if not cfg:
            return None
        try:
            img = skia.Image.MakeFromEncoded(skia.Data.MakeFromFileName(str(cfg["path"])))
        except Exception:
            img = None
        if img is None:
            return None
        bx, by, bw, bh = self.box if hasattr(self, "box") else (0, 0, self.W, self.H)
        lw = max(2, int(round(bw * float(cfg.get("size", 0.14)))))
        lh = max(2, int(round(img.height() * lw / img.width())))
        s = C.offscreen(lw, lh)
        s.getCanvas().drawImageRect(img, skia.Rect.MakeWH(lw, lh), C.SAMPLING_CUBIC)
        mx, my = round(bw * 0.03), round(bh * 0.03)
        pos = cfg.get("pos", "br")
        if pos in ("tc", "bc"):
            x = bx + (bw - lw) // 2                 # centrado: mismo margen a cada lado
        else:
            x = bx + (mx if pos in ("tl", "bl") else bw - lw - mx)
        y = by + (my if pos in ("tl", "tr", "tc") else bh - lh - my)
        return s.makeImageSnapshot(), x, y, float(cfg.get("opacity", 0.9))

    def shot_idx(self, t: float) -> int | None:
        if not self.shot_starts:
            return None
        i = bisect.bisect_right(self.shot_starts, t + 1e-9) - 1
        return max(0, i)

    def face_at(self, n: int):
        if self.face is None or n >= len(self.face):
            return None
        r = self.face[n]
        if np.isnan(r).any():
            return None
        return tuple(float(v) for v in r)

    def make_ctx(self, n: int) -> C.FrameCtx:
        t = n / self.fps
        si = self.shot_idx(t)
        return C.FrameCtx(W=self.W, H=self.H, fps=self.fps, n=n, t=t, t_orig=self.tl.inverse(t),
                          quality=self.job["quality"], seed=self.job["seed"],
                          style=self.job["style"], accent=self.job.get("accent"),
                          shot=self.job["shots"][si] if si is not None else None,
                          face=self.face_at(n), words=self.job["words"],
                          entities=self.job["entities"])

    def box_ctx(self, ctx: C.FrameCtx) -> C.FrameCtx:
        """Contexto de overlays en la caja de layout (identico al cuadro en 9:16)."""
        if not self.boxed:
            return ctx
        bx, by, bw, bh = self.box
        face = None
        if ctx.face is not None:
            cx, cy, fw, fh = ctx.face
            face = ((cx * self.W - bx) / bw, (cy * self.H - by) / bh, fw * self.W / bw,
                    fh * self.H / bh)
        lc = C.FrameCtx(W=bw, H=bh, fps=ctx.fps, n=ctx.n, t=ctx.t, t_orig=ctx.t_orig,
                        quality=ctx.quality, seed=ctx.seed, style=ctx.style, accent=ctx.accent, shot=ctx.shot,
                        face=face, words=ctx.words, entities=ctx.entities)
        ir = skia.IRect.MakeXYWH(bx, by, bw, bh)

        def matte_fn():
            m = ctx.matte()
            return None if m is None else np.ascontiguousarray(m[by:by + bh, bx:bx + bw])

        def subject_fn():
            s = ctx.subject_image()
            return None if s is None else s.makeSubset(ir)

        lc._matte_fn, lc._subject_fn = matte_fn, subject_fn
        base = ctx.base_image()
        if base is not None:
            lc._cache["base_image"] = base.makeSubset(ir)
        return lc

    # ------------------------------------------------------------ camara
    def _covers(self, M: skia.Matrix, pad: float = 0.01) -> bool:
        inv = skia.Matrix()
        if not M.invert(inv):
            return False
        for x, y in ((0, 0), (self.W, 0), (0, self.H), (self.W, self.H)):
            p = inv.mapXY(x, y)
            if p.x() < -pad or p.y() < -pad or p.x() > self.W + pad or p.y() > self.H + pad:
                return False
        return True

    def _cover_guard(self, M: skia.Matrix, extra: list[tuple[float, float]] = ()) -> skia.Matrix:
        """Si la transformacion deja ver bordes, agrega el zoom minimo (sobre el centro)."""
        def ok(m):
            if not self._covers(m):
                return False
            for dx, dy in extra:
                mm = _mcopy(m)
                mm.postTranslate(dx, dy)
                if not self._covers(mm):
                    return False
            return True

        if ok(M):
            return M

        def scaled(s):
            S = skia.Matrix()
            S.setScale(s, s, self.W / 2, self.H / 2)
            m = _mcopy(M)
            m.postConcat(S)
            return m

        lo, hi = 1.0, 3.0
        if not ok(scaled(hi)):
            return scaled(hi)
        for _ in range(14):
            mid = (lo + hi) / 2
            if ok(scaled(mid)):
                hi = mid
            else:
                lo = mid
        return scaled(hi)

    def camera(self, img: skia.Image, ctx: C.FrameCtx, t: float):
        """Compone las matrices de camera.MATRIX_EFFECTS activas y las aplica (+ motion blur).

        Si el modulo camera trae sus helpers (combine + apply_camera o warp/motion_blur) se
        usan: motion blur con obturador real entre la matriz previa y la actual. Si no, se
        compone aqui y el blur es direccional por traslacion. Siempre se garantiza que el
        cuadro quede cubierto (sin bordes).
        """
        cams = [it for it in self._active(self.effects, t) if it["_stage"] == "matrix"]
        if not cams:
            return img, None
        res = []
        for it in cams:
            try:
                r = self.reg["matrix"][it["kind"]](it, t - it["_fs"], it["_fe"] - it["_fs"], ctx)
            except Exception as e:  # noqa
                self._err(f"effects:{it['kind']}", e)
                continue
            if r is not None and r[0] is not None:
                res.append(r)
        if not res:
            return img, None
        cam = self.mods.get("camera")
        helpers = cam is not None and all(hasattr(cam, h) for h in
                                          ("combine", "warp", "needs_blur", "motion_blur"))
        if helpers:
            M, mv = cam.combine(res, ctx)
            vx, vy = float(mv[0]), float(mv[1])
        else:
            M = skia.Matrix()
            vx = vy = 0.0
            for m, v in res:
                M.postConcat(m)
                vx += float(v[0] or 0.0)
                vy += float(v[1] or 0.0)
            mv = (vx, vy)
        speed = math.hypot(vx, vy)
        blur = cam.needs_blur(mv, ctx) if helpers else speed > ctx.px(BLUR_MIN_PX)
        if M.isIdentity() and not blur:
            return img, None
        offs = []
        if blur and not helpers:
            k = int(min(8, max(3, math.ceil(speed * SHUTTER / ctx.px(3.0)))))
            offs = [((i / (k - 1)) - 0.5) * SHUTTER for i in range(k)]
        extra = [(vx * o, vy * o) for o in (offs[0], offs[-1])] if offs else []
        M = self._cover_guard(M, extra)
        if helpers:
            if hasattr(cam, "apply_camera"):
                return cam.apply_camera(img, M, mv, ctx), M
            out = cam.warp(img, M, ctx)
            if blur:
                out = cam.motion_blur(out, mv, ctx)
            return out, M
        c = self.cam_surf.getCanvas()
        samp = C.SAMPLING_FAST
        if not offs:
            c.save()
            c.concat(M)
            c.drawImage(img, 0, 0, samp)
            c.restore()
        else:
            for i, o in enumerate(offs):
                m = _mcopy(M)
                m.postTranslate(vx * o, vy * o)
                c.save()
                c.concat(m)
                c.drawImage(img, 0, 0, samp, skia.Paint(Alphaf=1.0 / (i + 1)))
                c.restore()
        return self.cam_surf.makeImageSnapshot(), M

    # ------------------------------------------------------------ sujeto
    def _matte_base(self, n: int) -> np.ndarray | None:
        if self.matte is None or not any(a <= n < b for a, b in self.matte_ranges):
            return None
        try:
            m = self.matte.get(n)
        except Exception as e:  # noqa
            self._err("matte:get", e)
            return None
        if m is None:
            return None
        m = np.asarray(m, np.float32)
        if self.matte_box:                        # matte del frente (inset) -> cuadro completo
            bx, by, bw, bh = self.matte_box
            if m.shape != (bh, bw):
                import cv2
                m = cv2.resize(m, (bw, bh), interpolation=cv2.INTER_LINEAR)
            full = np.zeros((self.H, self.W), np.float32)
            full[by:by + bh, bx:bx + bw] = m
            return full
        if m.shape != (self.H, self.W):
            import cv2
            m = cv2.resize(m, (self.W, self.H), interpolation=cv2.INTER_LINEAR)
        return m

    def matte_out(self, n: int, M: skia.Matrix | None) -> np.ndarray | None:
        """Matte en espacio de SALIDA: misma matriz de camara que el video."""
        m = self._matte_base(n)
        if m is None or M is None:
            return m
        cam = self.mods.get("camera")
        if cam is not None and hasattr(cam, "warp_mask"):
            try:
                return np.asarray(cam.warp_mask(m, M, self._mask_ctx), np.float32)
            except Exception as e:  # noqa
                self._err("camera:warp_mask", e)
        s = skia.Surface.MakeRaster(self.a8_info)
        c = s.getCanvas()
        c.clear(skia.ColorTRANSPARENT)
        c.concat(M)
        c.drawImage(C.image_from_gray(m), 0, 0, C.SAMPLING_FAST)
        a = s.makeImageSnapshot().toarray(colorType=skia.kAlpha_8_ColorType)
        return a.reshape(self.H, self.W).astype(np.float32) * (1.0 / 255.0)

    def subject(self, ctx: C.FrameCtx) -> skia.Image | None:
        m = ctx.matte()
        base = ctx.base_image()
        if m is None or base is None:
            return None
        s = C.offscreen(self.W, self.H)
        c = s.getCanvas()
        c.clear(skia.ColorTRANSPARENT)
        c.drawImage(base, 0, 0)
        c.drawImage(C.image_from_gray(m), 0, 0, C.SAMPLING_FAST,
                    skia.Paint(BlendMode=skia.BlendMode.kDstIn))
        return s.makeImageSnapshot()

    # ------------------------------------------------------------ cuadros inyectados
    def _still(self, k: int) -> skia.Image | None:
        """Cuadro base k con el color del grade (lo piden photo/self_broll via frame_request)."""
        arr = None
        try:
            if self.base_video:
                with V.FrameReader(self.base_video, self.W, self.H, k, 1, self.fps) as r:
                    arr = r.read()
            elif self.frame_source is not None:
                arr = self.frame_source(k)
        except Exception as e:  # noqa
            self._err("cutaways:frame_request", e)
        if arr is None:
            return None
        img = C.image_from_rgba(arr)
        if self.job.get("grade_on") and self.grader is not None:
            try:
                img = self.grader.apply(img, self.job["grade_cfg"], self.shot_idx(k / self.fps),
                                        self.make_ctx(k)) or img
            except Exception as e:  # noqa
                self._err("grade:apply", e)
        if self.boxed:
            bx, by, bw, bh = self.box
            img = img.makeSubset(skia.IRect.MakeXYWH(bx, by, bw, bh))
        return img

    def _inject(self, items: list) -> None:
        for it in items:
            req = it.get("_frame_req")
            if req and it.get(req[0]) is None and not it.get("_frame_failed"):
                img = self._still(int(req[1]))
                if img is None:
                    it["_frame_failed"] = True
                else:
                    it[req[0]] = img

    # ------------------------------------------------------------ overlays
    def _draw(self, reg_key: str, items: list, t: float, ctx: C.FrameCtx, key: str,
              boxed: bool = False) -> None:
        rg = self.reg[reg_key]
        for it in items:
            fn = rg.get(it.get(key))
            if fn is None:
                continue
            self.canvas.save()
            if boxed and self.boxed:
                self.canvas.translate(self.box[0], self.box[1])
            ta = time.perf_counter()
            try:
                fn(self.canvas, it, t - it["_fs"], it["_fe"] - it["_fs"], ctx)
            except Exception as e:  # noqa
                self._err(f"{reg_key}:{it.get(key)}", e)
            finally:
                self.canvas.restoreToCount(1)
                self._prof(f"{reg_key}:{it.get(key)}", ta)

    def _cutaways(self, items: list, t: float, lctx: C.FrameCtx) -> None:
        """Escenas a pantalla completa. Fuera de 9:16 se componen en la caja de layout y el
        resto del cuadro se llena con la misma escena desenfocada (como un pillarbox)."""
        if not items:
            return
        self._inject(items)
        if not self.boxed:
            self._draw("cutaways", items, t, lctx, "kind")
            return
        bx, by, bw, bh = self.box
        ta = time.perf_counter()
        bc = self._box_surf.getCanvas()
        bc.clear(skia.ColorTRANSPARENT)
        for it in items:
            fn = self.reg["cutaways"].get(it.get("kind"))
            if fn is None:
                continue
            bc.save()
            try:
                fn(bc, it, t - it["_fs"], it["_fe"] - it["_fs"], lctx)
            except Exception as e:  # noqa
                self._err(f"cutaways:{it.get('kind')}", e)
            finally:
                bc.restoreToCount(1)
        scene = self._box_surf.makeImageSnapshot()
        # relleno: la misma escena, ampliada para cubrir, muy desenfocada (sin formas
        # reconocibles) y un poco mas oscura; se calcula a 1/8 de resolucion (~1 ms)
        sw, sh = max(8, self.W // 8), max(8, self.H // 8)
        small = C.offscreen(sw, sh)
        sc = small.getCanvas()
        sc.clear(skia.ColorTRANSPARENT)
        k = max(sw / bw, sh / bh)
        dw, dh = bw * k, bh * k
        sig = max(3.0, 0.035 * max(sw, sh))
        sc.drawImageRect(scene, skia.Rect.MakeXYWH((sw - dw) / 2, (sh - dh) / 2, dw, dh),
                         C.SAMPLING, skia.Paint(ImageFilter=skia.ImageFilters.Blur(
                             sig, sig, skia.TileMode.kClamp)))
        sc.drawPaint(skia.Paint(Color=skia.ColorSetARGB(56, 0, 0, 0),
                                BlendMode=skia.BlendMode.kSrcATop))
        cv = self.canvas
        cv.drawImageRect(small.makeImageSnapshot(), skia.Rect.MakeWH(self.W, self.H), C.SAMPLING)
        # la escena nitida con los bordes laterales fundidos (sin costura visible)
        fe = max(2.0, 0.05 * bw)
        cv.saveLayer(skia.Rect.MakeXYWH(bx, by, bw, bh), None)
        cv.drawImage(scene, bx, by)
        sh_ = skia.GradientShader.MakeLinear([skia.Point(bx, 0), skia.Point(bx + fe, 0)],
                                             [skia.ColorSetARGB(0, 0, 0, 0), skia.ColorBLACK])
        cv.drawRect(skia.Rect.MakeXYWH(bx, by, fe, bh),
                    skia.Paint(Shader=sh_, BlendMode=skia.BlendMode.kDstIn))
        sh2 = skia.GradientShader.MakeLinear(
            [skia.Point(bx + bw - fe, 0), skia.Point(bx + bw, 0)],
            [skia.ColorBLACK, skia.ColorSetARGB(0, 0, 0, 0)])
        cv.drawRect(skia.Rect.MakeXYWH(bx + bw - fe, by, fe, bh),
                    skia.Paint(Shader=sh2, BlendMode=skia.BlendMode.kDstIn))
        cv.restore()
        self._prof("cutaways:box", ta)

    def _prof(self, key: str, t0: float) -> None:
        self.prof[key] = self.prof.get(key, 0.0) + (time.perf_counter() - t0)

    # ------------------------------------------------------------ cuadro
    def frame(self, n: int, base_rgba: np.ndarray, transitions: bool = True) -> np.ndarray:
        """Cuadro final n (RGBA uint8 HxWx4, vista del buffer interno: copiar si se guarda)."""
        t0 = time.perf_counter()
        t = n / self.fps
        ctx = self.make_ctx(n)
        img = C.image_from_rgba(base_rgba)
        # 1) camara -> grade -> efectos de video
        ta = time.perf_counter()
        img, M = self.camera(img, ctx, t)
        if M is not None:
            self._prof("camera", ta)
        cam = self.mods.get("camera")
        if M is not None and ctx.face is not None and cam is not None and hasattr(cam, "map_face"):
            ctx.face = cam.map_face(ctx.face, M, self.W, self.H)
        elif M is not None and ctx.face is not None:
            cx, cy, fw, fh = ctx.face
            r = M.mapRect(skia.Rect.MakeXYWH((cx - fw / 2) * self.W, (cy - fh / 2) * self.H,
                                             fw * self.W, fh * self.H))
            ctx.face = (r.centerX() / self.W, r.centerY() / self.H, r.width() / self.W,
                        r.height() / self.H)
        ctx._matte_fn = (lambda n=n, M=M: self.matte_out(n, M))
        ctx._subject_fn = (lambda ctx=ctx: self.subject(ctx))
        ctx._cache.pop("matte", None)
        ctx._cache.pop("subject", None)
        if self.job.get("grade_on") and self.grader is not None:
            ta = time.perf_counter()
            try:
                img = self.grader.apply(img, self.job["grade_cfg"], self.shot_idx(t), ctx) or img
            except Exception as e:  # noqa
                self._err("grade:apply", e)
            self._prof("grade", ta)
        active_fx = self._active(self.effects, t)
        for stage, regs in (("base", ("grade_fx", "matte_fx")), ("post", ("post",))):
            for it in active_fx:
                if it["_stage"] != stage:
                    continue
                fn = next((self.reg[r][it["kind"]] for r in regs if it["kind"] in self.reg[r]), None)
                if fn is None:
                    continue
                # base_image/subject_image = la ENTRADA actual de este efecto
                ctx._cache["base_image"] = img
                ctx._cache.pop("subject", None)
                ta = time.perf_counter()
                try:
                    out = fn(img, it, t - it["_fs"], it["_fe"] - it["_fs"], ctx)
                    if out is not None:
                        img = out
                except Exception as e:  # noqa
                    self._err(f"effects:{it['kind']}", e)
                self._prof(f"effects:{it['kind']}", ta)
        ctx._cache["base_image"] = img
        ctx._cache.pop("subject", None)
        # 2) base en canvas
        cv = self.canvas
        cv.clear(skia.ColorBLACK)
        cv.drawImage(img, 0, 0)
        lctx = self.box_ctx(ctx)
        if self.media_items:
            self._inject_media(t)
        # 3) capas detras + sujeto encima
        a_text = self._active(self.text, t)
        a_gfx = self._active(self.graphics, t)
        behind_t = [it for it in a_text if it.get("_behind")]
        behind_g = [it for it in a_gfx if it.get("_behind")]
        if behind_t or behind_g:
            ta = time.perf_counter()
            subj = ctx.subject_image()
            self._prof("subject", ta)
            if subj is not None:
                self._draw("graphics", behind_g, t, lctx, "kind", boxed=True)
                self._draw("text", behind_t, t, lctx, "preset", boxed=True)
                cv.drawImage(subj, 0, 0)
                a_text = [it for it in a_text if not it.get("_behind")]
                a_gfx = [it for it in a_gfx if not it.get("_behind")]
        # 4) escenas a pantalla completa
        a_cut = self._active(self.cutaways, t)
        self._cutaways(a_cut, t, lctx)
        # 4b) recursos flotantes del guion: fondo desenfocado + tarjeta (subtitulos encima)
        a_fm = self._active(self.float_media, t) if self.float_media else []
        if a_fm:
            self._float_media(a_fm, t)
        # 5) graficos, texto, subtitulos
        self._draw("graphics", a_gfx, t, lctx, "kind", boxed=True)
        self._draw("text", a_text, t, lctx, "preset", boxed=True)
        if self.job.get("captions_on") and self.reg["captions"] is not None:
            # contraste de los subtitulos con lo que de verdad queda detras: con una escena, una tarjeta, una
            # pantalla o un video de apoyo encima de la toma, el cuadro ya compuesto (text.caption_contrast)
            if a_cut or a_fm:
                snap = self.surf.makeImageSnapshot()
                lctx._cache["caption_bg"] = snap.makeSubset(skia.IRect.MakeXYWH(*self.box)) if self.boxed else snap
            cv.save()
            if self.boxed:
                cv.translate(self.box[0], self.box[1])
            ta = time.perf_counter()
            try:
                self.reg["captions"](cv, self.job["phrases"], self.job["captions"], t, lctx)
            except Exception as e:  # noqa
                self._err("captions", e)
            finally:
                cv.restoreToCount(1)
                lctx._cache.pop("caption_bg", None)
                self._prof("captions", ta)
        # 6) transiciones (prev = ultimo cuadro compuesto antes de la union, congelado)
        if n in self.prev_needed:
            self.prev_store[n] = self.surf.makeImageSnapshot()
        if transitions:
            for tr in self.transitions:
                if not (tr["_F0"] <= n < tr["_F1"]):
                    continue
                fn = self.reg["transitions"].get(tr["kind"])
                if fn is None:
                    continue
                L = tr["_F1"] - tr["_F0"]
                p = (n - tr["_F0"] + 0.5) / L
                ta = time.perf_counter()
                cur = self.surf.makeImageSnapshot()
                prev = self.prev_store.get(tr["_P"]) if n > tr["_P"] else None
                try:
                    out = fn(cur, prev if prev is not None else cur, tr, p, ctx)
                except Exception as e:  # noqa
                    self._err(f"transitions:{tr['kind']}", e)
                    continue
                if out is not None and out is not cur:
                    cv.clear(skia.ColorBLACK)
                    cv.drawImage(out, 0, 0)
                self._prof(f"transitions:{tr['kind']}", ta)
            for P in [p for p in self.prev_store if all(
                    not (tr["_P"] == p and n < tr["_F1"] - 1) for tr in self.transitions)]:
                self.prev_store.pop(P, None)
        # 6b) grano/dither final sobre todo el cuadro (antes de progreso/logo: esos quedan limpios)
        if self._grain is not None:
            ta = time.perf_counter()
            self.surf.flushAndSubmit()
            self._grain.apply(self.arr, n)
            self._prof("grain", ta)
        # 7) progreso / logo
        if self.progress:
            self._draw("graphics", self._active(self.progress, t), t, ctx, "kind")
        if self._logo is not None:
            li, x, y, op = self._logo
            cv.drawImage(li, x, y, C.SAMPLING_FAST, skia.Paint(Alphaf=op))
        self.surf.flushAndSubmit()
        self.t_comp += time.perf_counter() - t0
        return self.arr


# ================================================================ chunks y workers
def plan_chunks(n_frames: int, n_chunks: int, transitions: list[dict],
                min_len: int = 12) -> list[tuple[int, int]]:
    """Divide [0, n) en rangos contiguos; ninguna frontera cae dentro de una ventana de
    transicion (incluido el cuadro previo congelado)."""
    n_chunks = max(1, min(n_chunks, max(1, n_frames // max(1, min_len))))
    forb = [(min(t["_F0"], t["_P"]), t["_F1"]) for t in transitions]
    cuts = set()
    for k in range(1, n_chunks):
        b = int(round(k * n_frames / n_chunks))
        for _ in range(8):
            moved = False
            for lo, hi in forb:
                if lo < b < hi:
                    b = lo if (b - lo) <= (hi - b) else hi
                    moved = True
            if not moved:
                break
        if 0 < b < n_frames:
            cuts.add(b)
    bounds = [0] + sorted(cuts) + [n_frames]
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]


_W: dict[str, Any] = {}


def _grade_size(job: dict) -> tuple[int, int]:
    """Tamano con el que se MIDE el grade: el del base (el del formato). La igualacion no
    depende de la resolucion (mide a 360 px), asi el draft y /frame reutilizan la cache del
    export en vez de volver a medir (~4-7 s)."""
    bs = job.get("base_size")
    return (int(bs[0]), int(bs[1])) if bs else (job["W"], job["H"])


def _make_grader(mods: dict, base, shots, W, H, fps, cache_dir, on_pct=None):
    G = mods["grade"].Grader
    kw = {"cache_dir": cache_dir} if cache_dir and _accepts(G, "cache_dir") else {}
    g = G(base, shots, W, H, fps, **kw)
    if on_pct is not None and _accepts(g.prepare, "on_pct"):
        g.prepare(on_pct=on_pct)
    else:
        g.prepare()
    return g


def _make_analysis(job: dict, mods: dict, analysis: dict):
    grader = matte = face = None
    if analysis.get("grader_pickle"):
        try:
            grader = pickle.loads(analysis["grader_pickle"])
        except Exception as e:  # noqa
            _log(f"grader no se pudo deserializar ({e}); se reconstruye")
    if grader is None and job.get("grade_on") and mods.get("grade") is not None:
        # sin base (cuadro suelto de /frame) el Grader aplica el look sin igualar tomas
        try:
            grader = _make_grader(mods, analysis.get("base"), job["shots"], *_grade_size(job),
                                  job["fps"], analysis.get("cache_dir") if analysis.get("base") else None)
        except Exception as e:  # noqa
            _log(f"grade no disponible: {e}")
            grader = None
    if analysis.get("matte_pickle"):
        try:
            matte = pickle.loads(analysis["matte_pickle"])
        except Exception:
            matte = None
    if matte is None and job.get("matte_ranges") and mods.get("matte") is not None and analysis.get("base"):
        try:
            mb = job.get("matte_box")
            mw, mh = (mb[2], mb[3]) if mb else (job["W"], job["H"])
            matte = mods["matte"].MatteProvider(analysis.get("matte_src") or analysis["base"],
                                                mw, mh, job["fps"], job["matte_ranges"],
                                                analysis["cache_dir"], job["quality"])
            matte.prepare()
        except Exception as e:  # noqa
            _log(f"matte no disponible: {e}")
            matte = None
    if analysis.get("face_path") and Path(analysis["face_path"]).exists():
        face = np.load(analysis["face_path"])
    return grader, matte, face


def _worker_init(job: dict, analysis: dict, counter, threads: int = 0) -> None:
    for p in job.get("sys_path") or []:
        if p not in sys.path:
            sys.path.insert(0, p)
    if threads:
        try:
            import cv2
            cv2.setNumThreads(int(threads))
        except Exception:  # pragma: no cover
            pass
    mods = load_modules(job.get("modules"), names=COMPOSE_MODULES, quiet=True)
    grader, matte, face = _make_analysis(job, mods, analysis)
    _W["comp"] = Compositor(job, mods, grader, matte, face, base_video=analysis.get("base"))
    _W["job"], _W["analysis"], _W["counter"] = job, analysis, counter


def _render_range(comp: Compositor, base: str, f0: int, f1: int, out: str, kind: str,
                  counter=None, cancel=None) -> dict:
    job = comp.job
    t0 = time.perf_counter()
    reader = V.PrefetchReader(V.FrameReader(base, job["W"], job["H"], f0, f1 - f0, job["fps"]))
    writer = V.AsyncWriter(V.ChunkWriter(out, job["W"], job["H"], job["fps"], kind))
    last = None
    t_io = 0.0
    try:
        for n in range(f0, f1):
            if cancel and cancel():
                raise V.Canceled("render cancelado")
            ta = time.perf_counter()
            arr = reader.read()
            if arr is None:                    # no deberia pasar: base con n cuadros exactos
                arr = last if last is not None else np.zeros((job["H"], job["W"], 4), np.uint8)
            last = arr
            t_io += time.perf_counter() - ta
            fr = comp.frame(n, arr)
            ta = time.perf_counter()
            writer.write(fr)
            t_io += time.perf_counter() - ta
            if counter is not None:
                with counter.get_lock():
                    counter.value += 1
        reader.close()
        writer.close()
    except BaseException:
        reader.close()
        writer.abort()
        raise
    finally:
        comp.media.close()                     # lectores de clips del usuario de este rango
    return {"f0": f0, "f1": f1, "frames": f1 - f0, "t": time.perf_counter() - t0,
            "t_io": t_io, "pid": os.getpid()}


def _worker_chunk(task: tuple) -> dict:
    f0, f1, out, kind = task
    comp = _W["comp"]
    tc0 = comp.t_comp
    r = _render_range(comp, _W["analysis"]["base"], f0, f1, out, kind, _W["counter"])
    r["t_comp"] = comp.t_comp - tc0
    r["errors"] = dict(comp.errors)
    r["prof"] = dict(comp.prof)
    comp.prof.clear()
    return r


_SAFE_LOCK = threading.RLock()
_SAFE_STATE: dict[str, Any] = {"depth": 0, "mod": None, "saved": {}}


class _SafeMain:
    """Mientras se crean los procesos spawn: los workers NO deben re-ejecutar el script
    principal. spawn reimporta __main__ (por __spec__.name o por __file__) como __mp_main__ en
    cada worker, y p. ej. app/server.py arranca a nivel de modulo ollama, la revalidacion de
    licencia y FastAPI. Se ocultan __file__ y __spec__ SIEMPRE (los workers solo necesitan
    motion.render: el initializer y la tarea se serializan por referencia) y se limita el
    numero de hilos BLAS/OpenMP que heredan (evita sobresuscripcion con N procesos).

    Concurrencia: CANDADO DE MODULO (RLock) + CONTADOR. Dos hilos que crean procesos a la vez
    (un export y una vista previa en el servidor, o scripts con hilos) se serializan: el tramo
    protegido solo crea el pool y lanza los procesos (~0.1-1 s), y mientras un hilo esta dentro
    ningun otro puede restaurar __main__.__file__ ni pisar las variables OMP que heredan sus
    hijos (antes el primero que salia restauraba __file__ mientras el otro lanzaba procesos y
    el hijo reimportaba el script principal). El contador permite anidar en el mismo hilo:
    solo la entrada mas externa oculta y solo la ultima salida restaura."""

    def __init__(self, env: dict | None = None):
        self.env = {k: str(v) for k, v in (env or {}).items()}

    def __enter__(self):
        _SAFE_LOCK.acquire()
        try:
            st = _SAFE_STATE
            if st["depth"] == 0:
                mod = sys.modules.get("__main__")
                saved: dict[str, Any] = {}
                if mod is not None:
                    if hasattr(mod, "__file__"):
                        saved["__file__"] = mod.__file__
                        del mod.__file__
                    saved["__spec__"] = getattr(mod, "__spec__", None)
                    mod.__spec__ = None
                st["mod"], st["saved"] = mod, saved
            st["depth"] += 1
            self.mod = st["mod"]
            self.old_env = {k: os.environ.get(k) for k in self.env}
            os.environ.update(self.env)
        except BaseException:
            _SAFE_LOCK.release()
            raise
        return self

    def __exit__(self, *a):
        try:
            for k, v in self.old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            st = _SAFE_STATE
            st["depth"] = max(0, st["depth"] - 1)
            if st["depth"] == 0:
                mod, saved = st["mod"], st["saved"]
                if mod is not None:
                    if "__file__" in saved:
                        mod.__file__ = saved["__file__"]
                    mod.__spec__ = saved.get("__spec__")
                st["mod"], st["saved"] = None, {}
        finally:
            _SAFE_LOCK.release()


def worker_threads(nw: int, override: int | None = None) -> int:
    """Hilos de cv2 (y OMP/BLAS) por worker: los nucleos repartidos entre los procesos, asi
    N workers x hilos = nucleos (sin sobresuscripcion). `opts.threads` lo fija a mano."""
    if override:
        return max(1, int(override))
    return max(1, (os.cpu_count() or 4) // max(1, nw))


def _run_parallel(job: dict, analysis: dict, tasks: list, nw: int, n: int,
                  prog: "_Progress", threads: int | None = None) -> list[dict]:
    """Reparte los chunks en `nw` procesos spawn. Un worker que muere o falla al iniciar
    rompe el pool con error claro (nunca se cuelga); cancelar mata los procesos."""
    from concurrent.futures import ProcessPoolExecutor, FIRST_EXCEPTION, wait
    ctxm = mp.get_context("spawn")
    counter = ctxm.Value("i", 0)
    nproc = max(1, min(nw, len(tasks)))
    th = worker_threads(nproc, threads)
    env = {k: th for k in ("OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS",
                           "MKL_NUM_THREADS")}
    with _SafeMain(env):
        ex = ProcessPoolExecutor(nproc, mp_context=ctxm, initializer=_worker_init,
                                 initargs=(job, analysis, counter, th))
        futs = [ex.submit(_worker_chunk, t) for t in tasks]
    try:
        pending = set(futs)
        while pending:
            done, pending = wait(pending, timeout=0.25, return_when=FIRST_EXCEPTION)
            for f in done:
                if f.exception() is not None:
                    raise f.exception()
            prog.pct("render", counter.value / max(1, n) * 100)
        ex.shutdown(wait=True)
        return [f.result() for f in futs]
    except BaseException:
        for pr in list(getattr(ex, "_processes", {}).values()):
            try:
                pr.kill()
            except Exception:
                pass
        ex.shutdown(wait=False, cancel_futures=True)
        raise


# ================================================================ analisis
def face_path(cache_dir: Path, bkey: str, cam, cuts: list[int] | None = None) -> Path:
    """Cache de la pista de rostro: por video base, version del modulo de camara y uniones."""
    try:
        sig = f"{cam.__name__}|{int(Path(cam.__file__).stat().st_mtime)}"
    except Exception:
        sig = getattr(cam, "__name__", "camera")
    if cuts:
        sig += "|" + ",".join(str(int(c)) for c in cuts)
    return Path(cache_dir) / f"face_{bkey}_{hashlib.sha1(sig.encode()).hexdigest()[:8]}.npy"


def _face_file(job: dict, mods: dict, cache: Path, bkey: str) -> Path | None:
    cam = mods.get("camera")
    if cam is None or not hasattr(cam, "face_track"):
        return None
    cuts = job.get("cut_frames") if _accepts(cam.face_track, "cuts") else None
    return face_path(cache, bkey, cam, cuts)


def _ensure_face(job: dict, mods: dict, base_video: str, cache: Path, bkey: str,
                 prog: "_Progress | None" = None, on_pct=None, cancel=None) -> Path | None:
    """Pista de rostro del base (sin suavizar a traves de uniones/tomas), cacheada.
    on_pct/cancel se pasan a camera.face_track si los acepta (progreso y cancelacion)."""
    fp = _face_file(job, mods, cache, bkey)
    if fp is None:
        return None
    if not fp.exists():
        if prog:
            prog.step("Siguiendo el rostro…")
        cam = mods["camera"]
        kw = {"cuts": job.get("cut_frames")} if _accepts(cam.face_track, "cuts") else {}
        if on_pct is not None and _accepts(cam.face_track, "on_pct"):
            kw["on_pct"] = on_pct
        if cancel is not None and _accepts(cam.face_track, "cancel"):
            kw["cancel"] = cancel
        try:
            arr = np.asarray(cam.face_track(base_video, job["W"], job["H"], job["fps"],
                                            job["n_frames"], **kw), np.float32)
            tmp = fp.with_name(f"{fp.stem}.{os.getpid()}.{uuid.uuid4().hex[:6]}.part.npy")
            np.save(tmp, arr)
            os.replace(tmp, fp)
        except V.Canceled:
            raise
        except InterruptedError as e:
            raise V.Canceled("render cancelado") from e
        except Exception as e:  # noqa
            _log(f"face_track fallo: {e}")
    return fp if fp.exists() else None


def _analysis(job: dict, mods: dict, base: dict, cache_dir: Path, bkey: str, prog: _Progress,
              stats: dict) -> dict:
    """Pista de rostro + grade (en un hilo) EN PARALELO con el matte (hilo principal): las tres
    etapas leen el mismo base y son independientes. Antes iban en serie."""
    import threading
    W, H, fps = job["W"], job["H"], job["fps"]
    out = {"base": base["video"], "cache_dir": str(cache_dir), "face_path": None,
           "grader_pickle": None, "matte_pickle": None}
    t0 = time.time()
    side: dict[str, Any] = {}

    matte_on = bool(job["matte_ranges"]) and mods.get("matte") is not None

    def band(a, b):
        """Avance del hilo rostro/grade en [a, b] % del analisis. Con matte la barra la lleva
        el matte (el rostro corre en paralelo y suele terminar antes): este hilo no reporta."""
        if matte_on:
            return None
        return lambda p: prog.pct("analysis", a + (b - a) * max(0.0, min(100.0, float(p))) / 100.0)

    def face_and_grade():
        ta = time.time()
        try:
            side["fp"] = _ensure_face(job, mods, base["video"], cache_dir, bkey,
                                      on_pct=band(0, 60), cancel=prog.cancel)
        except BaseException as e:  # noqa: se reporta en el hilo principal
            side["face_err"] = e
        side["t_face"] = time.time() - ta
        if not matte_on:
            try:
                prog.pct("analysis", 60)
            except BaseException:  # noqa: la cancelacion la atiende el hilo principal
                pass
        if job["grade_on"] and not isinstance(side.get("face_err"), V.Canceled):
            tb = time.time()
            try:
                side["grader"] = _make_grader(mods, base["video"], job["shots"], *_grade_size(job), fps,
                                              str(cache_dir), on_pct=band(60, 90))
            except Exception as e:  # noqa
                side["grade_err"] = e
            side["t_grade"] = time.time() - tb

    need_face = _face_file(job, mods, cache_dir, bkey)
    if need_face is not None and not need_face.exists():
        prog.step("Siguiendo el rostro…")
    th = threading.Thread(target=face_and_grade, name="analysis-face-grade", daemon=True)
    th.start()
    if matte_on:
        prog.step("Recortando a la persona (matte)…")
        t1 = time.time()
        try:
            msrc, mw, mh = base["video"], W, H
            if job.get("matte_box"):
                msrc = _ensure_fg_video(base["video"], job, prog.cancel)
                mw, mh = job["matte_box"][2], job["matte_box"][3]
            out["matte_src"] = msrc
            mp_ = _final_matte_for_draft(job, mods, msrc, cache_dir)
            if mp_ is None:
                mp_ = mods["matte"].MatteProvider(msrc, mw, mh, fps, job["matte_ranges"],
                                                  str(cache_dir), job["quality"])
            mp_.prepare(on_pct=lambda p: prog.pct("analysis", 0.9 * p))
            if getattr(mp_, "dir", None) is not None:
                V.touch(mp_.dir)                 # LRU: el matte en uso no se poda
            _matte_stats(mp_, job, stats)
            try:
                out["matte_pickle"] = pickle.dumps(mp_)
            except Exception:
                out["matte_pickle"] = None
            out["_matte_obj"] = mp_
        except V.Canceled:
            raise
        except Exception as e:  # noqa
            _log(f"matte fallo: {e}")
            stats["skipped"].append("matte:error")
        stats["t_matte"] = round(time.time() - t1, 3)
    # espera el hilo de rostro/grade (atendiendo la cancelacion)
    while th.is_alive():
        th.join(0.2)
        prog.check()
    prog.pct("analysis", 95)
    if "face_err" in side:
        _log(f"face_track fallo: {side['face_err']}")
    fp = side.get("fp")
    face = None
    if fp is not None:
        out["face_path"] = str(fp)
        face = np.load(fp)
        V.touch(fp)
    set_anchors(job, face, mods)
    stats["t_face"] = round(side.get("t_face", 0.0), 3)
    if job["grade_on"]:
        g = side.get("grader")
        if g is None:
            _log(f"grade fallo al preparar: {side.get('grade_err')}")
            job["grade_on"] = False
        else:
            out["_grader_obj"] = g
            try:
                out["grader_pickle"] = pickle.dumps(g)
            except Exception:
                out["grader_pickle"] = None
        stats["t_grade_prep"] = round(side.get("t_grade", 0.0), 3)
    stats["t_analysis"] = round(time.time() - t0, 3)
    prog.pct("analysis", 100)
    return out


MATTE_QUALITY_WARN = 0.85


def _final_matte_for_draft(job: dict, mods: dict, msrc: str, cache_dir: Path):
    """En draft, si las mascaras FINALES (tamano del base) ya estan completas en la cache
    (ensure_analysis o un export previo), se usan tal cual: el compositor las reescala al
    cuadro draft. Evita recalcular mascaras draft (~10-15 s) en cada vista previa real."""
    if job.get("quality") != "draft" or job.get("matte_box") or not job.get("base_size"):
        return None
    bw, bh = job["base_size"]
    try:
        full = mods["matte"].MatteProvider(msrc, bw, bh, job["fps"], job["matte_ranges"],
                                           str(cache_dir), "final")
        fn = getattr(full, "missing_runs", None) or getattr(full, "missing_ranges", None)
        if fn is not None and not fn():
            return full
    except Exception:  # noqa
        pass
    return None


def _matte_stats(mp_, job: dict, stats: dict) -> None:
    """Calidad del matte por rango (quality_of: 1 = limpio) y avisos para la UI: sin compuerta
    de persona, o rango donde la persona tocaba muebles grandes (conviene evitar contorno o
    reemplazo de fondo ahi)."""
    rngs = []
    for a, b in job.get("matte_ranges") or []:
        q = None
        fn = getattr(mp_, "quality_of", None)
        if callable(fn):
            try:
                q = fn(int(a), int(b))
            except Exception:  # noqa
                q = None
        rngs.append({"start": int(a), "end": int(b), "quality": q})
        if q is not None and q < MATTE_QUALITY_WARN:
            stats.setdefault("warnings", []).append(f"matte:{a}-{b}:calidad_baja")
    warn = getattr(mp_, "warning", None)
    err = getattr(mp_, "error", None)
    stats["matte"] = {"ranges": rngs, "warning": warn, "error": err,
                      "step": getattr(mp_, "step", None)}
    if warn:
        stats.setdefault("warnings", []).append("matte:sin_compuerta_de_persona")


# claves del resultado de music.get_music que se copian a la cfg del mezclador. NO src_offset:
# en audio.py `offset` es buscar dentro del archivo y el wav de get_music ya viene ajustado.
MUSIC_KEYS = ("wav", "bpm", "beats", "downbeats", "sections", "drop", "hook_end", "ending",
              "keyscale", "lufs")
RISERS = ("riser_short", "riser_long")


def music_cfg(plan: dict, job: dict, tl: T.Timeline, skipped: list | None = None) -> tuple[dict, bool]:
    """cfg de musica en TIEMPO FINAL (stops, swells y drop_at remapeados) + si esta activa.
    Es la MISMA cfg para el precalentamiento (music.prefetch) y para el paso de audio: asi el
    get_music del paso 4 es un acierto de cache (o espera a la generacion en curso)."""
    mcfg = copy.deepcopy((plan.get("fx") or {}).get("music") or {})
    bad = skipped if skipped is not None else []

    def remap_list(key, fn):
        out = []
        for s in mcfg.get(key) or []:
            try:
                r = fn(s)
            except (KeyError, TypeError, ValueError, AttributeError):
                bad.append(f"music:{key}:dato_invalido")
                continue
            if r is not None:
                out.append(r)
        mcfg[key] = out

    def remap_stop(s):
        """at y dur a tiempo FINAL: el silencio va de at a at+dur en el original; si cruza un
        corte dura lo que queda en el video (antes dur quedaba en tiempo original y el hueco se
        comia la frase siguiente y movia el drop automatico, que entra en at+dur)."""
        at_o = float(s["at"])
        if not math.isfinite(at_o):
            raise ValueError("at")
        at = tl.remap_clamped(at_o)
        out = dict(s, at=round(at, 4))
        d = _num_or(s.get("dur"), float("nan"))
        if math.isfinite(d) and d > 0:
            out["dur"] = round(max(0.05, tl.remap_clamped(at_o + d) - at), 4)
        return out

    remap_list("stops", remap_stop)
    remap_list("swells", lambda s: (lambda r: dict(s, start=round(r[0], 4), end=round(r[1], 4))
                                    if r else None)(tl.map_range(float(s["start"]), float(s["end"]))))
    if mcfg.get("drop_at") is not None:
        try:
            mcfg["drop_at"] = round(tl.remap_clamped(float(mcfg["drop_at"])), 4)
        except (TypeError, ValueError):
            mcfg["drop_at"] = None
    mcfg.pop("src_offset", None)
    on = bool(mcfg.get("enabled", True)) and mcfg.get("source", "ai") != "none"
    return mcfg, on


def prefetch_music(plan: dict, job: dict, tl: T.Timeline, mods: dict, opts: dict):
    """Arranca la generacion de la musica (ACE-Step en GPU) en segundo plano al INICIO del job,
    en paralelo con el base pass y el analisis. Devuelve el hilo o None."""
    mus = mods.get("music")
    fn = getattr(mus, "prefetch", None) if mus is not None else None
    if not callable(fn):
        return None
    mcfg, on = music_cfg(plan, job, tl)
    if not on or job["final_duration"] <= 0.25:
        return None
    try:
        kw = {"cache_dir": opts["music_cache_dir"]} if opts.get("music_cache_dir") else {}
        return fn(mcfg, job["final_duration"], job["style"], job["seed"], **kw)
    except Exception as e:  # noqa: el precalentamiento nunca tumba el render
        _log(f"prefetch de musica fallo: {e}")
        return None


def sfx_events(plan: dict, job: dict, tl: T.Timeline, skipped: list) -> list[dict]:
    """Eventos de SFX en tiempo FINAL para audio.mix: {id, name, at, gain_db, pan, variant,
    pitch, dur}. `dur` se remapea: en los risers es la anticipacion (antes de `at`); en el resto
    el sonido va de `at` a `at+dur` (si el tramo cruza un corte, dura lo que queda en el video)."""
    dur = job["final_duration"]
    events = []
    for it in (plan.get("fx") or {}).get("sfx") or []:
        if not isinstance(it, dict) or not it.get("enabled", True):
            continue
        try:
            at_o = float(it["at"])
            if not math.isfinite(at_o):
                raise ValueError("at")
            at = tl.remap_clamped(at_o)
            ev = {"id": it.get("id"), "name": it.get("name"), "at": round(at, 4),
                  "gain_db": _num_or(it.get("gain_db"), -6.0), "pan": _num_or(it.get("pan"), 0.0)}
        except (KeyError, TypeError, ValueError):
            skipped.append(f"sfx:{it.get('id')}:dato_invalido")
            continue
        if it.get("variant") is not None:
            v = _num_or(it.get("variant"), -1)
            if v >= 0:
                ev["variant"] = int(v)
        if it.get("pitch") is not None:
            ev["pitch"] = max(-24.0, min(24.0, _num_or(it.get("pitch"), 0.0)))
        d = _num_or(it.get("dur"), 0.0)
        if d > 0:
            name = str(it.get("name") or "")
            if name.startswith(RISERS):
                fd = at - tl.remap_clamped(at_o - d)
            else:
                fd = tl.remap_clamped(at_o + d) - at
            ev["dur"] = round(fd if fd > 1e-3 else d, 4)
        if at < dur:
            events.append(ev)
    return events


def _music_pending(mus, mcfg: dict, dur: float, job: dict, kw: dict) -> bool:
    """True si la musica IA de esta cfg aun no esta en la cache (el prefetch la esta
    generando): music.pending, o False si el modulo no lo expone."""
    fn = getattr(mus, "pending", None)
    if not callable(fn):
        return False
    try:
        return bool(fn(mcfg, dur, job["style"], job["seed"], **kw))
    except Exception:  # noqa
        return False


def _audio(plan: dict, job: dict, tl: T.Timeline, mods: dict, voice: str, out_wav: Path,
           prog: _Progress, stats: dict, opts: dict | None = None, box: dict | None = None) -> str:
    """Paso 4: musica (get_music; normalmente acierto de cache gracias a prefetch_music) +
    audio.mix(voz, SFX, musica) con la seed del plan. Devuelve el wav a muxear.

    Corre en un hilo EN PARALELO con el compositor: no emite pasos (on_step) para no pisar la
    etiqueta del compositor; `box` recibe la fase ('music' | 'mix') y el avance de la musica
    para que el hilo principal los muestre si tiene que esperar antes del mux.
    En draft, si la musica IA aun no esta en cache (el prefetch la genera), NO se espera: se
    mezcla con la procedural y stats.music_pending = True (el Studio refresca la vista previa
    cuando el prefetch termina). opts.draft_wait_music = True fuerza la espera."""
    o = opts or {}
    bx = box if box is not None else {}
    t0 = time.time()
    dur = job["final_duration"]
    events = sfx_events(plan, job, tl, stats["skipped"])
    mcfg, music_on = music_cfg(plan, job, tl, stats["skipped"])
    mus, aud = mods.get("music"), mods.get("audio")
    if music_on and mus is not None and hasattr(mus, "get_music"):
        kw = {"cache_dir": o["music_cache_dir"]} if o.get("music_cache_dir") else {}
        gcfg = mcfg
        stats["music_pending"] = False
        if job.get("quality") == "draft" and not o.get("draft_wait_music") and \
                _music_pending(mus, mcfg, dur, job, kw):
            gcfg = dict(mcfg, source="procedural")
            stats["music_pending"] = True
            _log("la musica IA se sigue generando: la vista previa usa la procedural")
        else:
            _log("Preparando la música…")
        bx["phase"] = "music"

        def on_music(p):
            bx["music_pct"] = float(p)
            prog.pct("audio", 0.8 * p)          # tambien atiende la cancelacion

        try:
            m = mus.get_music(gcfg, dur, job["style"], job["seed"], on_music, **kw)
            if m:
                mcfg.update({k: m[k] for k in MUSIC_KEYS if k in m})
                stats["music"] = {k: m.get(k) for k in ("wav", "source", "engine", "bpm", "drop",
                                                        "keyscale", "cached", "gen_s", "lufs",
                                                        "fallback_from") if k in m}
                stats["music"]["ending"] = m.get("ending")
                if stats["music_pending"]:
                    stats["music"]["pending_source"] = mcfg.get("source", "ai")
                if m.get("warnings"):
                    stats["music"]["warnings"] = list(m["warnings"])
        except V.Canceled:
            raise
        except Exception as e:  # noqa
            if prog.cancel and prog.cancel():
                raise V.Canceled("render cancelado")
            _log(f"musica fallo: {e}")
            stats["skipped"].append("music:error")
    elif music_on:
        stats["skipped"].append("music:modulo")
    mcfg["enabled"] = music_on and bool(mcfg.get("wav"))
    result = voice
    if aud is not None and hasattr(aud, "mix") and (events or mcfg["enabled"]):
        bx["phase"] = "mix"
        _log("Mezclando voz, música y efectos…")
        prog.check()
        try:
            kw = {}
            if _accepts(aud.mix, "seed"):
                kw["seed"] = job["seed"]
            if o.get("stems_dir") and _accepts(aud.mix, "stems_dir"):
                kw["stems_dir"] = str(o["stems_dir"])
            info = aud.mix(voice, dur, events, mcfg, str(out_wav), **kw) or {}
            stats["audio"] = _json_safe(info)
            if out_wav.exists():
                result = str(out_wav)
        except Exception as e:  # noqa
            if prog.cancel and prog.cancel():
                raise V.Canceled("render cancelado")
            _log(f"mezcla fallo ({e}); se usa solo la voz")
            stats["skipped"].append("audio:error")
    elif events and aud is None:
        stats["skipped"].append("audio:modulo")
    bx["phase"] = "done"
    stats["sfx_events"] = len(events)
    stats["t_audio"] = round(time.time() - t0, 3)
    prog.pct("audio", 100)
    return result


def _json_safe(v: Any, depth: int = 0):
    """Copia serializable (escalares, listas y dicts) de un resultado de modulo."""
    if isinstance(v, (bool, int, str)) or v is None:
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (np.floating, np.integer)):
        return _json_safe(v.item(), depth)
    if depth > 4:
        return None
    if isinstance(v, dict):
        return {str(k): _json_safe(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x, depth + 1) for x in v]
    return str(v)


def _fit_audio(wav: str, n_samples: int, out: Path) -> str:
    """Asegura que el audio mida exactamente n_samples (pad/trim)."""
    import soundfile as sf
    a, sr = sf.read(wav, dtype="float32", always_2d=True)
    if sr == V.SR and len(a) == n_samples:
        return wav
    if sr != V.SR:
        a = V.read_audio(wav, V.SR, 2)
    b = np.zeros((n_samples, a.shape[1]), np.float32)
    b[: min(n_samples, len(a))] = a[:n_samples]
    V.write_wav(out, b)
    return str(out)


# ================================================================ orquestacion
def prepare(plan: dict, opts: dict | None = None) -> dict:
    """Normaliza el plan, arma timeline, modulos y job (sin tocar video). Uso interno y
    para /frame. Devuelve {plan, opts, timeline, mods, job, skipped}."""
    for p in (opts or {}).get("sys_path") or []:
        if p not in sys.path:
            sys.path.insert(0, p)
    pn = schema.normalize(plan)
    if not pn.get("video"):
        raise ValueError("El plan no tiene 'video'.")
    info = V.probe(pn["video"])
    o = _resolve_opts(opts, info)
    if not pn.get("duration"):
        pn["duration"] = info["duration"]
    tl = T.Timeline.from_plan(pn, fps=o["fps"],
                              min_frag=float(o.get("min_fragment", T.MIN_FRAG)))
    mods = load_modules(o.get("modules"))
    job, skipped = build_job(pn, tl, o, mods)
    # SFX que normalize descarto (nombre que no existe ni como alias): se avisa en vez de
    # desaparecer en silencio
    kept = {it.get("id") for it in pn["fx"].get("sfx") or []}
    for it in ((plan.get("fx") or {}).get("sfx") or []) if isinstance(plan.get("fx"), dict) else []:
        if isinstance(it, dict) and it.get("id") and it.get("enabled", True) and it["id"] not in kept:
            known = schema.resolve_sfx_name(it.get("name"))[0] is not None
            skipped.append(f"sfx:{it['id']}:{'dato_invalido' if known else 'nombre_desconocido'}")
    job["matte_box"], job["matte_box_base"] = _pillar_boxes(info, o)
    job["base_size"] = [o["base_W"], o["base_H"]]
    return {"plan": pn, "opts": o, "timeline": tl, "mods": mods, "job": job, "skipped": skipped,
            "source": info}


def _pillar_boxes(info: dict, o: dict):
    """Fuente vertical en salida ancha (inset con fondo desenfocado): region del FRENTE en el
    cuadro del job y en el base. El matte se calcula solo ahi (la cara es chica en el cuadro
    completo y el detector del matte no la encuentra)."""
    bW, bH = o["base_W"], o["base_H"]
    mode, (fw, fh) = V.reframe_mode(info, bW, bH, o.get("air"))
    if mode != "pillar" or fw >= bW:
        return None, None
    k = o["W"] / bW
    jw = min(o["W"], _even(fw * k))
    return [(o["W"] - jw) // 2, 0, jw, o["H"]], [(bW - fw) // 2, 0, fw, bH]


def _ensure_fg_video(base_video: str, job: dict, cancel=None) -> str:
    """Recorte del frente (inset) del video base, cacheado junto al base (para el matte)."""
    bx, by, bw, bh = job["matte_box_base"]
    bv = Path(base_video)
    out = bv.with_name("fg" + bv.name)                      # fgbase_<clave>.mp4
    n = job["n_frames"]
    if V._meta_ok(out, {"frames": n, "W": bw, "H": bh}):
        return str(out)
    with V.FileLock(out, cancel):
        if V._meta_ok(out, {"frames": n, "W": bw, "H": bh}):
            return str(out)
        tmp = V._tmp_path(out)
        cmd = ([V.FFMPEG, "-y", "-v", "error", "-i", str(bv), "-map", "0:v:0", "-an",
                "-vf", f"crop={bw}:{bh}:{bx}:{by},{V.SETPARAMS_709}"]
               + V.encoder_args(bw, bh, job["fps"], "inter") + [str(tmp)])
        try:
            V.run_ffmpeg_fallback(cmd, cancel=cancel)
            if V.count_frames(tmp) != n:
                raise RuntimeError("recorte del frente: cuadros incompletos")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, out)
        V._write_meta(out, {"frames": n, "W": bw, "H": bh})
    return str(out)


def _base_files(pr: dict) -> tuple[Path, Path]:
    o = pr["opts"]
    return V.base_paths(Path(o["cache_dir"]), pr["plan"], o["base_W"], o["base_H"], o["fps"],
                        pr["timeline"], o["enhance_audio"], o.get("air"))


def analysis_key(plan: dict, opts: dict | None = None, prep: dict | None = None) -> dict:
    """Claves del ANALISIS de fondo (lo que calculan ensure_base / ensure_analysis) ->
    {"base": str, "full": str, "media": str | None} (sha1 de 16 hex).

    base: video base + voz = fuente, cortes cuantizados, formato/reencuadre (`air`), fps,
          camara del plan y cadena de audio (`enhance_audio`): los nombres de base_*.mp4 y
          voice_*.wav de la cache ya codifican todo eso.
    full: base + pista de rostro (version del modulo camera y uniones), rangos de matte (con el
          inset de 16:9, la calidad y la version del algoritmo) y la medicion del grade (si esta
          activo y las tomas en tiempo final).
    media: firma de los medios propios (media_signature; None sin medios): archivo, recorte,
          media_in/loop, ajuste y hueco. Cambia al agregar o cambiar un medio; el servidor
          entonces llama ensure_media (no hace falta relanzar el analisis: base y full no
          cambian).
    Editar textos, graficos, escenas, SFX, musica o el look del grade NO cambia base ni full
    (el Studio autoguarda sin relanzar el analisis). Barata: 3-5 ms en caliente, no toca video;
    la PRIMERA llamada del proceso carga los modulos de efectos (segundos)."""
    o = dict(opts or {})
    o.setdefault("workers", 0)
    pr = prep or prepare(plan, o)
    o, job, mods = pr["opts"], pr["job"], pr["mods"]
    bv, bw = _base_files(pr)
    base = hashlib.sha1(f"{bv.name}|{bw.name}|{o['fmt']}|{o['base_W']}x{o['base_H']}".encode()) \
        .hexdigest()[:16]
    ff = _face_file(job, mods, Path(o["cache_dir"]), bv.stem.replace("base_", ""))
    mt = mods.get("matte")
    extra = json.dumps({
        "face": ff.name if ff is not None else None,
        "matte": [list(r) for r in job.get("matte_ranges") or []],
        "matte_box": job.get("matte_box"), "quality": job.get("quality"),
        "matte_algo": getattr(mt, "_ALGO", None) if mt is not None else None,
        "grade": bool(job.get("grade_on")),
        "shots": [[round(float(s.get("start", 0.0)), 4), round(float(s.get("end", 0.0)), 4)]
                  for s in job.get("shots") or []] if job.get("grade_on") else None,
    }, sort_keys=True, default=str)
    full = hashlib.sha1((base + extra).encode()).hexdigest()[:16]
    return {"base": base, "full": full, "media": media_signature(job)}


def ensure_media(plan: dict, opts: dict | None = None, prep: dict | None = None) -> dict:
    """Normaliza los medios propios del plan en la cache (lo mismo que hace ensure_analysis),
    sin tocar el base ni el analisis. Para el servidor cuando cambia analysis_key()['media']
    (el usuario agrego o cambio un medio): el primer /frame de ese clip ya no decodifica de la
    fuente. Devuelve {items, skipped, t}."""
    t0 = time.time()
    pr = prep or prepare(plan, opts)
    o, job = pr["opts"], pr["job"]
    skipped = [s for s in pr["skipped"] if s.startswith("media:")]
    if _media_items(job):
        skipped += prepare_media(job, o, o.get("cancel"))
    return {"items": len(_media_items(job)), "skipped": skipped, "t": round(time.time() - t0, 3)}


def ensure_base(plan: dict, opts: dict | None = None, prep: dict | None = None,
                prog: _Progress | None = None) -> dict:
    """Construye (o reutiliza de cache) el video base + voz. Seguro de llamar en segundo
    plano mientras corre un export (lock por clave). Devuelve el dict de base_pass.

    El base siempre se genera al tamano del FORMATO (el draft lo reutiliza escalado)."""
    pr = prep or prepare(plan, opts)
    o, tl = pr["opts"], pr["timeline"]
    prog = prog or _Progress(o)
    cache = Path(o["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    bv, bw = _base_files(pr)
    return V.base_pass(pr["plan"], bv, bw, o["base_W"], o["base_H"], o["fps"], tl,
                       enhance_audio=o["enhance_audio"],
                       on_pct=lambda p: prog.pct("base", p), cancel=prog.cancel,
                       air=o.get("air"), kind="inter")


def ensure_analysis(plan: dict, opts: dict | None = None, prep: dict | None = None) -> dict:
    """Base + pista de rostro + mattes de los rangos que los piden + grade, en cache.

    Pensado para el Studio: llamarlo en segundo plano al abrir un proyecto (cancelable con
    opts.cancel, progreso con opts.on_pct). Despues render_frame es exacto e inmediato y el
    export se salta todo el analisis."""
    pr = prep or prepare(plan, opts)
    o, mods, job = pr["opts"], pr["mods"], pr["job"]
    prog = _Progress(o, {"timeline": (0, 0), "base": (0, 55), "analysis": (55, 100)})
    t0 = time.time()
    # la musica del plan tambien se precalienta (la vista previa real y el export la usan)
    mth = prefetch_music(pr["plan"], job, pr["timeline"], mods, o) \
        if o.get("prefetch_music", True) else None
    base = ensure_base(plan, o, pr, prog)
    stats: dict[str, Any] = {"skipped": []}
    bkey = Path(base["video"]).stem.replace("base_", "")
    an = _analysis(job, mods, base, Path(o["cache_dir"]), bkey, prog, stats)
    if _media_items(job):                  # medios propios: listos para /frame, draft y export
        stats["skipped"] += prepare_media(job, o, prog.cancel)
    return {"base": base["video"], "voice": base["voice"], "face_path": an.get("face_path"),
            "matte": bool(an.get("_matte_obj")), "grade": bool(an.get("_grader_obj")),
            "music_prefetch": mth is not None, "t_total": round(time.time() - t0, 3), **stats}


def _stage_weights(pr: dict) -> dict:
    o, job, mods = pr["opts"], pr["job"], pr["mods"]
    bv, bw = _base_files(pr)
    n = job["n_frames"]
    base_cached = V._meta_ok(bv, {"frames": n, "W": o["base_W"], "H": o["base_H"]}) and \
        V._meta_ok(bw, {"samples": int(round(n / o["fps"] * V.SR))})
    bkey = bv.stem.replace("base_", "")
    ff = _face_file(job, mods, Path(o["cache_dir"]), bkey)
    face_cached = ff is None or ff.exists()
    fx = pr["plan"]["fx"]
    mus = fx.get("music") or {}
    music_on = bool(mus.get("enabled", True)) and mus.get("source", "ai") != "none" and \
        mods.get("music") is not None
    # el audio (y la musica, precalentada desde el inicio) corre EN PARALELO con el
    # compositor: su tramo de la barra es minimo. Si la musica IA aun no esta en cache y el
    # render la va a esperar (export), el mux se lleva un tramo visible para esa espera
    wait_music = 0
    if music_on and (job["quality"] != "draft" or o.get("draft_wait_music")):
        kw = {"cache_dir": o["music_cache_dir"]} if o.get("music_cache_dir") else {}
        mcfg, on = music_cfg(pr["plan"], job, pr["timeline"])
        if on and _music_pending(mods["music"], mcfg, job["final_duration"], job, kw):
            wait_music = 12
    return {"timeline": 0.5, "base": 1 if base_cached else 28,
            "analysis": 2 + (0 if face_cached else 5) + (14 if job["matte_ranges"] and mods.get("matte") else 0),
            "audio": 0.5 if (music_on or fx.get("sfx")) else 0.2,
            "render": 50, "mux": 3 + (3 if o.get("shorts") else 0) + wait_music}


def render_pro(plan: dict, out_path, opts: dict | None = None) -> dict:
    """Render completo del plan v2 a `out_path` (mp4). Devuelve estadisticas."""
    T0 = time.time()
    pr = prepare(plan, opts)
    o, tl, mods, job = pr["opts"], pr["timeline"], pr["mods"], pr["job"]
    prog = _Progress(o, stage_plan(_stage_weights(pr)))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache = Path(o["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    W, H, fps, n = job["W"], job["H"], job["fps"], job["n_frames"]
    stats: dict[str, Any] = {"out": str(out_path), "W": W, "H": H, "fps": fps, "n_frames": n,
                             "fmt": o["fmt"], "layout": job["layout"],
                             "duration": n / fps, "source_duration": tl.duration,
                             "removed": round(tl.removed, 3), "keeps": len(tl.keeps),
                             "quality": job["quality"], "skipped": list(pr["skipped"]),
                             "warnings": list(job.get("warnings") or []),
                             "modules": {k: v is not None for k, v in mods.items()},
                             "items": {k: len(job[k]) for k in ("text", "graphics", "cutaways",
                                                               "effects", "transitions")}}
    if job.get("media"):                   # recursos flotantes del guion (solo si hay)
        stats["items"]["media"] = len(job["media"])
    for s in pr["skipped"]:
        _log(f"se omite {s}")
    prog.pct("timeline", 100)
    # la musica (ACE-Step en GPU) se genera en paralelo desde YA: base pass, analisis y
    # compositor corren mientras tanto; el paso de audio la toma de la cache
    stats["music_prefetch"] = prefetch_music(pr["plan"], job, tl, mods, o) is not None

    # 2) base (al tamano del formato; el draft lo lee escalado)
    prog.step("Preparando el video base (reencuadre, color y cortes)…")
    base = ensure_base(plan, o, pr, prog)
    stats.update(t_base=round(base["t_video"], 3), t_voice=round(base["t_voice"], 3),
                 cached_base=base["cached_video"], cached_voice=base["cached_voice"],
                 base=base["video"], voice=base["voice"], reframe=base.get("mode"))
    bkey = Path(base["video"]).stem.replace("base_", "")

    # 3) analisis
    analysis = _analysis(job, mods, base, cache, bkey, prog, stats)
    grader_obj = analysis.pop("_grader_obj", None)
    matte_obj = analysis.pop("_matte_obj", None)
    # medios propios (imagenes y clips del usuario) normalizados en la cache
    if _media_items(job):
        prog.step("Preparando tus imágenes y clips…")
        tm = time.time()
        stats["skipped"] += prepare_media(job, o, prog.cancel)
        # un medio ilegible no sale en el export (ni marcador ni la escena sin su foto)
        dropped = drop_failed_media(job)
        stats["media"] = {"items": len(_media_items(job)), "t": round(time.time() - tm, 3),
                          "dropped": dropped}
        stats["items"] = {k: len(job[k]) for k in ("text", "graphics", "cutaways", "effects",
                                                  "transitions")}
        if job.get("media"):
            stats["items"]["media"] = len(job["media"])

    # 4) audio EN PARALELO con el compositor (solo el mux necesita los dos). Escribe en su
    # propia subcarpeta; si el render se cancela no se le espera (es daemon) y el hilo borra lo
    # suyo al terminar (tolera que el job ya no exista).
    work = cache / f"job_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    awork = work / "audio"
    awork.mkdir(parents=True, exist_ok=True)
    tmp_out = V._tmp_path(out_path)
    audio_box: dict[str, Any] = {"phase": "start"}

    def audio_job():
        try:
            a = _audio(pr["plan"], job, tl, mods, base["voice"], awork / "mix.wav", prog, stats, o,
                       box=audio_box)
            audio_box["wav"] = _fit_audio(a, int(round(n / fps * V.SR)), awork / "mix_fit.wav")
        except BaseException as e:  # noqa: se relanza en el hilo principal
            audio_box["err"] = e
        finally:
            if audio_box.get("abandon"):         # render cancelado: limpia lo suyo
                shutil.rmtree(awork, ignore_errors=True)
                if not o.get("keep_chunks"):
                    shutil.rmtree(work, ignore_errors=True)

    import threading
    audio_th = threading.Thread(target=audio_job, name="render-audio", daemon=True)
    try:
        audio_th.start()

        # 5) compositor
        prog.step("Componiendo efectos, textos y gráficos…")
        t0 = time.time()
        kind = "draft" if job["quality"] == "draft" else "final"
        nw = int(o["workers"])
        n_chunks = 1 if nw <= 1 else nw * 2
        chunks = plan_chunks(n, n_chunks, job["transitions"], min_len=max(12, int(fps)))
        tasks = [(a, b, str(work / f"chunk_{i:04d}.mp4"), kind) for i, (a, b) in enumerate(chunks)]
        results = []
        if nw <= 1 or len(tasks) == 1:
            if matte_obj is None or grader_obj is None:
                g2, m2, _ = _make_analysis(job, mods, analysis)
                grader_obj = grader_obj or g2
                matte_obj = matte_obj or m2
            face = np.load(analysis["face_path"]) if analysis.get("face_path") else None
            comp = Compositor(job, mods, grader_obj, matte_obj, face, base_video=base["video"])
            done = 0
            for a, b, out, k in tasks:
                tc0 = comp.t_comp
                r = _render_range(comp, base["video"], a, b, out, k, None, prog.cancel)
                r["t_comp"] = comp.t_comp - tc0
                r["errors"] = dict(comp.errors)
                r["prof"] = dict(comp.prof)
                comp.prof.clear()
                results.append(r)
                done += b - a
                prog.pct("render", done / n * 100)
            threads = 0
        else:
            results = _run_parallel(job, analysis, tasks, nw, n, prog, o.get("threads"))
            threads = worker_threads(min(nw, len(tasks)), o.get("threads"))
        t_comp_wall = time.time() - t0
        prog.pct("render", 100)
        errs: dict[str, int] = {}
        for r in results:
            for k, v in (r.get("errors") or {}).items():
                errs[k] = max(errs.get(k, 0), v)
        busy = sum(r["t"] for r in results)
        prof: dict[str, float] = {}
        for r in results:
            for k, v in (r.get("prof") or {}).items():
                prof[k] = prof.get(k, 0.0) + v
        stats["profile_ms_per_frame"] = {k: round(v / n * 1000, 2) for k, v in
                                         sorted(prof.items(), key=lambda kv: -kv[1])}
        stats.update(t_compose=round(t_comp_wall, 3), workers=max(1, nw), chunks=len(tasks),
                     threads_per_worker=threads,
                     ms_per_frame_wall=round(t_comp_wall / n * 1000, 2),
                     ms_per_frame_proc=round(busy / n * 1000, 2),
                     ms_per_frame_compose=round(sum(r.get("t_comp", 0) for r in results) / n * 1000, 2),
                     ms_per_frame_io=round(sum(r.get("t_io", 0) for r in results) / n * 1000, 2),
                     errors=errs)

        # 6) concat + mux (a un temporal unico; nunca queda un .part en la carpeta de salida)
        ta = time.time()
        shown = None
        while audio_th.is_alive():
            audio_th.join(0.2)
            prog.check()
            if time.time() - ta < 0.6:
                continue
            ph = audio_box.get("phase")
            if ph != shown and ph in ("music", "mix"):
                prog.step("Generando la música…" if ph == "music" else "Mezclando voz, música y efectos…")
                shown = ph
            if ph == "music":                   # la espera avanza en el tramo del mux
                prog.pct("mux", 70.0 * float(audio_box.get("music_pct") or 0.0) / 100.0)
        stats["t_audio_wait"] = round(time.time() - ta, 3)
        if "err" in audio_box:
            raise audio_box["err"]
        mix = audio_box["wav"]
        prog.step("Uniendo y exportando…")
        t0 = time.time()
        video_only = work / "video.mp4"
        if len(tasks) == 1:
            os.replace(tasks[0][2], video_only)
        else:
            V.concat([t[2] for t in sorted(tasks)], video_only, cancel=prog.cancel)
        prog.pct("mux", 75)
        V.mux(video_only, mix, tmp_out, cancel=prog.cancel)
        os.replace(tmp_out, out_path)
        stats["t_mux"] = round(time.time() - t0, 3)
        prog.pct("mux", 90)
        if o.get("shorts"):
            prog.step("Cortando los shorts…")
            stats["shorts"] = render_shorts(pr["plan"], out_path, o, tl)
        prog.pct("mux", 100)
    except BaseException:
        tmp_out.unlink(missing_ok=True)
        if audio_th.is_alive():
            # no se espera a la musica: get_music atiende la cancelacion (sondea el candado)
            # y el hilo es daemon; limpia su carpeta al terminar
            audio_box["abandon"] = True
            audio_th.join(0.5)
        raise
    finally:
        if not o.get("keep_chunks"):
            shutil.rmtree(work, ignore_errors=True)
        try:
            V.prune_cache(cache, keep=int(o.get("cache_keep", 6)))
        except Exception:
            pass
    stats["t_total"] = round(time.time() - T0, 3)
    _log(f"render listo: {n} cuadros en {stats['t_total']:.1f}s "
         f"({stats['ms_per_frame_proc']:.1f} ms/cuadro/proceso, {stats['workers']} procesos)")
    return stats


def render_draft(plan: dict, out_path, opts: dict | None = None) -> dict:
    """Vista previa real a 540x960 (mismo pipeline, calidad draft). Lee el MISMO base del
    formato que el export (escalado al decodificar): no repite el base pass."""
    o = dict(opts or {})
    o["quality"] = "draft"
    o.pop("W", None)
    o.pop("H", None)
    o.setdefault("workers", max(1, min(4, (os.cpu_count() or 4) // 3)))
    return render_pro(plan, out_path, o)


# ================================================================ shorts (paso 6)
def render_shorts(plan: dict, final_mp4, opts: dict | None = None,
                  timeline: T.Timeline | None = None) -> list[dict]:
    """Corta los highlights del plan (tiempo ORIGINAL) del video final ya renderizado.

    Remapea cada highlight con el Timeline (lo que cae en cortes se recorta), lo ajusta a la
    reticula de cuadros y lo re-codifica (corte exacto sin depender de keyframes; audio con
    micro fundidos de 15 ms para evitar clics). Devuelve [{path, start, end, title, index}]
    con tiempos FINALES. Highlights de menos de 1 s o fuera del video se omiten."""
    o = dict(opts or {})
    fps = float(o.get("fps") or 30)
    final_mp4 = Path(final_mp4)
    if timeline is None:
        pn = schema.normalize(plan)
        if not pn.get("duration"):
            pn["duration"] = V.probe(pn["video"])["duration"]
        timeline = T.Timeline.from_plan(pn, fps=fps,
                                        min_frag=float(o.get("min_fragment", T.MIN_FRAG)))
    info = V.probe(final_mp4)
    fdur = float(info.get("duration") or timeline.final_duration)
    W, H = int(info.get("w") or o.get("W") or 1080), int(info.get("h") or o.get("H") or 1920)
    out_dir = Path(o.get("shorts_dir") or final_mp4.parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    cancel = o.get("cancel")
    out = []
    for i, h in enumerate(plan.get("highlights") or []):
        if not isinstance(h, dict) or not h.get("enabled", True):
            continue
        try:
            r = timeline.map_range(float(h["start"]), float(h["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if r is None:
            continue
        fs = math.floor(r[0] * fps + 1e-6) / fps
        fe = min(fdur, math.ceil(r[1] * fps - 1e-6) / fps)
        if fe - fs < 1.0:
            continue
        dst = out_dir / f"{final_mp4.stem}_short{i + 1}.mp4"
        tmp = V._tmp_path(dst)
        d = fe - fs
        cmd = ([V.FFMPEG, "-y", "-v", "error", "-ss", f"{fs:.6f}", "-i", str(final_mp4),
                "-t", f"{d:.6f}", "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", f"fps={fps},{V.SETPARAMS_709}"]
               + V.encoder_args(W, H, fps, "final")
               + ["-af", f"afade=t=in:d=0.015,afade=t=out:st={max(0.0, d - 0.015):.6f}:d=0.015",
                  "-c:a", "aac", "-b:a", "192k", "-ar", str(V.SR), "-ac", "2",
                  "-movflags", "+faststart", str(tmp)])
        try:
            V.run_ffmpeg_fallback(cmd, cancel=cancel)
            os.replace(tmp, dst)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        out.append({"path": str(dst), "start": round(fs, 4), "end": round(fe, 4),
                    "title": h.get("title", ""), "index": i})
    return out


# ================================================================ cuadro exacto (/frame)
_FRAME_CACHE: dict[str, Any] = {}
_MINI_MATTE: dict[tuple, np.ndarray] = {}


def _source_frame(plan: dict, tl: T.Timeline, n: int, W: int, H: int, fps: float,
                  air=None, work_dir=None) -> np.ndarray:
    """Decodifica de la FUENTE el cuadro final n con el mismo reencuadre/tonemap del base."""
    src = plan["video"]
    info = V.probe(src)
    t_orig = tl.inverse(n / fps)
    sfps = info.get("fps") or fps
    reframe, meta = V.reframe_filters(info, W, H, plan, air, work_dir, single_t=t_orig,
                                      timeline=tl)
    chain = reframe + V.tonemap_filters(info)
    graph = (",".join(chain) + f",scale={W}:{H}:in_color_matrix=bt709:in_range=tv:"
             "flags=bicubic+accurate_rnd+full_chroma_int,format=rgba")
    cmd = [V.FFMPEG, "-v", "error", "-nostdin", "-ss", f"{max(0.0, t_orig - 0.5 / sfps):.6f}",
           "-i", str(src), "-filter_complex", f"[0:v:0]{graph}[v]", "-map", "[v]",
           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True)
    need = W * H * 4
    if len(r.stdout) < need:
        raise RuntimeError("No se pudo decodificar el cuadro de la fuente: "
                           + r.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(r.stdout[:need], np.uint8).reshape(H, W, 4).copy()


class _DictMatte:
    """Proveedor de matte de pocos cuadros (vista previa): get(n) de un dict."""

    def __init__(self, masks: dict[int, np.ndarray], W: int, H: int):
        self.masks, self.W, self.H = masks, W, H

    def get(self, n: int) -> np.ndarray:
        m = self.masks.get(int(n))
        return m if m is not None else np.zeros((self.H, self.W), np.float32)


def _runs(frames: list[int]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for k in sorted(set(frames)):
        if out and k == out[-1][1]:
            out[-1][1] = k + 1
        else:
            out.append([k, k + 1])
    return [(a, b) for a, b in out]


def _preview_matte(mods: dict, job: dict, frames: list[int], base_at, cache: Path,
                   bkey: str | None, base_video: str | None):
    """Matte SOLO de los cuadros que pide la vista previa.

    Si el cache completo (ensure_analysis/export) ya los tiene, se usa tal cual (exacto).
    Si no, se calcula sobre un mini video con esos cuadros (< 1 s) en vez de todos los
    rangos del plan (antes: 20 s en la primera llamada a /frame)."""
    M = mods.get("matte")
    if M is None or not frames:
        return None
    box = job.get("matte_box")
    W, H, fps, q = job["W"], job["H"], job["fps"], job["quality"]
    if box:
        W, H = box[2], box[3]
        fg = Path(base_video).with_name("fg" + Path(base_video).name) if base_video else None
        base_video = str(fg) if fg is not None and V._meta_ok(fg, {"frames": job["n_frames"]}) else None
        raw_at = base_at
        base_at = lambda k: np.ascontiguousarray(  # noqa: E731
            raw_at(k)[box[1]:box[1] + box[3], box[0]:box[0] + box[2]])
    runs = _runs(frames)
    if base_video:
        try:
            full = M.MatteProvider(base_video, W, H, fps, runs, str(cache), q)
            if hasattr(full, "missing_ranges") and not full.missing_ranges():
                return full
        except Exception:  # noqa
            pass
    masks: dict[int, np.ndarray] = {}
    todo = []
    for k in sorted(set(frames)):
        m = _MINI_MATTE.get((bkey, W, H, q, k)) if bkey else None
        if m is not None:
            masks[k] = m
        else:
            todo.append(k)
    for a, b in _runs(todo):
        tmpd = cache / f"_mini_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        try:
            tmpd.mkdir(parents=True, exist_ok=True)
            mini = tmpd / "mini.mp4"
            w = V.ChunkWriter(mini, W, H, fps, "inter")
            try:
                for k in range(a, b):
                    w.write(base_at(k))
                w.close()
            except BaseException:
                w.abort()
                raise
            mp_ = M.MatteProvider(str(mini), W, H, fps, [(0, b - a)], str(tmpd), q)
            mp_.prepare()
            for k in range(a, b):
                m = np.array(mp_.get(k - a), np.float32, copy=True)
                masks[k] = m
                if bkey:
                    if len(_MINI_MATTE) > 48:
                        _MINI_MATTE.pop(next(iter(_MINI_MATTE)))
                    _MINI_MATTE[(bkey, W, H, q, k)] = m
        except Exception as e:  # noqa
            _log(f"matte de vista previa no disponible: {e}")
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
    return _DictMatte(masks, W, H) if masks else None


def render_frame(plan: dict, t_final: float, opts: dict | None = None) -> np.ndarray:
    """Un cuadro final exacto (RGBA uint8 HxWx4) en el tiempo FINAL t_final (el cuadro que
    muestra un reproductor en pausa: floor(t*fps)).

    Con el base en cache usa su analisis: pista de rostro (la calcula y cachea si falta),
    mattes del cache o, si faltan, solo de los cuadros necesarios, y el grade. Sin base
    decodifica la fuente en el tiempo original mapeado con el mismo reencuadre/tonemap.
    """
    o = dict(opts or {})
    o["workers"] = 0
    pr = prepare(plan, o)
    o, tl, mods, job = pr["opts"], pr["timeline"], pr["mods"], pr["job"]
    W, H, fps, N = job["W"], job["H"], job["fps"], job["n_frames"]
    n = int(min(max(0, tl.frame_of(float(t_final), fps)), max(0, N - 1)))
    cache = Path(o["cache_dir"])
    bv, _ = _base_files(pr)
    have_base = V._meta_ok(bv, {"frames": N, "W": o["base_W"], "H": o["base_H"]})
    bkey = bv.stem.replace("base_", "") if have_base else None
    grader = face = None
    if have_base:
        ck = f"{cache}|{bkey}|{W}x{H}|{job['grade_on']}|{sorted(job['modules'].items())}|{json.dumps(job['shots'], default=str)}"
        if ck not in _FRAME_CACHE:
            fpth = _ensure_face(job, mods, str(bv), cache, bkey)
            g = None
            if job["grade_on"] and mods.get("grade") is not None:
                try:
                    g = _make_grader(mods, str(bv), job["shots"], *_grade_size(job), fps, str(cache))
                except Exception as e:  # noqa
                    _log(f"grade no disponible: {e}")
            if len(_FRAME_CACHE) > 3:
                _FRAME_CACHE.pop(next(iter(_FRAME_CACHE)))
            _FRAME_CACHE[ck] = (g, np.load(fpth) if fpth is not None else None)
        grader, face = _FRAME_CACHE[ck]
    else:
        grader, _, _ = _make_analysis(dict(job, matte_ranges=[]), mods, {"base": None})

    src_cache: dict[int, np.ndarray] = {}

    def base_at(k: int) -> np.ndarray:
        if k in src_cache:
            return src_cache[k]
        a = None
        if have_base:
            with V.FrameReader(bv, W, H, k, 1, fps) as r:
                a = r.read()
        if a is None:
            a = _source_frame(pr["plan"], tl, k, W, H, fps, o.get("air"), cache)
        if len(src_cache) > 16:
            src_cache.pop(next(iter(src_cache)))
        src_cache[k] = a
        return a

    def prefetch(a: int, b: int) -> None:
        """Lee [a, b) del base con UNA busqueda (antes: una por cuadro, ~0.18 s cada una)."""
        if not have_base or all(k in src_cache for k in range(a, b)):
            return
        with V.FrameReader(bv, W, H, a, b - a, fps) as r:
            for k in range(a, b):
                f = r.read()
                if f is None:
                    break
                src_cache[k] = f

    base_n = base_at(n)
    if face is None and not have_base:
        face = _single_face(mods, base_n, n, job, cache)
    set_anchors(job, face, mods)
    # cuadros que necesitan matte: n y los previos congelados de transiciones activas
    active_tr = [tr for tr in job["transitions"] if tr["_F0"] <= n < tr["_F1"] and 0 <= tr["_P"] < n]
    want = [n] + [tr["_P"] for tr in active_tr]
    in_rng = lambda k: any(a <= k < b for a, b in job["matte_ranges"])  # noqa: E731
    if have_base:
        need = sorted({k for c in want for k in range(c - 2, c + 3) if 0 <= k < N and in_rng(k)})
    else:
        need = sorted({k for k in want if in_rng(k)})
    for a, b in _runs(need):
        prefetch(a, b)
    matte = _preview_matte(mods, job, need, base_at, cache, bkey,
                           str(bv) if have_base else None) if need else None
    if _media_items(job):                  # imagenes normalizadas; clips: del cache o 1 cuadro
        prepare_media(job, o, o.get("cancel"), only_images=True)
        for it in _media_items(job):       # ilegible: marcador en la edicion (el export lo omite)
            if it["_media"].get("failed"):
                it["_media_placeholder"] = True
    comp = Compositor(job, mods, grader, matte, face,
                      base_video=str(bv) if have_base else None, frame_source=base_at)
    try:
        for tr in active_tr:
            comp.frame(tr["_P"], base_at(tr["_P"]), transitions=False)
        return comp.frame(n, base_n).copy()
    finally:
        comp.media.close()


def _single_face(mods: dict, rgba: np.ndarray, n: int, job: dict, cache: Path):
    """Rostro de un cuadro suelto (sin base cacheado): camera.face_track sobre un video de
    1 cuadro. Devuelve una pista (n_frames, 4) con ese rostro en el cuadro n, o None."""
    cam = mods.get("camera")
    if cam is None or not hasattr(cam, "face_track"):
        return None
    tmp = cache / f"_face1_{os.getpid()}_{uuid.uuid4().hex[:6]}.mp4"
    try:
        cache.mkdir(parents=True, exist_ok=True)
        w = V.ChunkWriter(tmp, job["W"], job["H"], job["fps"], "inter")
        w.write(rgba)
        w.close()
        f = np.asarray(cam.face_track(str(tmp), job["W"], job["H"], job["fps"], 1), np.float64)
        if f.ndim != 2 or len(f) < 1 or np.isnan(f[0, :4]).any():
            return None
        out = np.full((job["n_frames"], 4), np.nan)
        out[n] = f[0, :4]
        return out
    except Exception as e:  # noqa
        _log(f"rostro de cuadro suelto no disponible: {e}")
        return None
    finally:
        tmp.unlink(missing_ok=True)


def save_png(rgba: np.ndarray, path) -> None:
    """Guarda un cuadro RGBA como PNG (para /frame y demos)."""
    from PIL import Image
    Image.fromarray(np.ascontiguousarray(rgba[..., :3])).save(str(path))
