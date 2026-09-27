"""Compositor del Motion Engine: orquestacion del render, cuadro exacto, draft y shorts.

Contrato: docs/MOTION-ENGINE.md, secciones 1, 4.2, 4.2b y 4.3.

  render_pro(plan, out_path, opts) -> stats
    1. timeline   keeps (cuts habilitados) cuantizados a cuadro (expandiendo), fragmentos
                  absorbidos, uniones pegadas a cambios de toma; remap y uniones
    2. base pass  video base (tamano del formato) con cortes + voz de estudio (cache por hash,
                  con lock: seguro con ensure_base en segundo plano)
    3. analisis   camera.face_track (sin suavizar a traves de uniones/tomas) + anclas de
                  encuadre por item + matte.MatteProvider (solo rangos que lo piden) + grade
    4. audio      music.get_music + audio.mix (si existen; si no, solo voz)
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
  enhance_audio (True), air (margen desenfocado para fuentes horizontales, 0 = sin margen),
  logo ({path,size,opacity,pos} o None), progress (bool: barra superior automatica),
  dither (1.0: grano/dither final de +-2 niveles sobre TODO el cuadro; 0 = sin),
  min_fragment (s, default timeline.MIN_FRAG), shorts (bool), shorts_dir,
  modules ({nombre: modulo_importable}), sys_path ([rutas extra para los workers]),
  on_pct(stage, pct_total), on_step(msg), cancel() -> bool, strict (errores de renderer
  se propagan), keep_chunks (debug), cache_keep.
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
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import skia

import paths
from . import ctx as C
from . import schema
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
    return {
        "matrix": _reg(cam, "MATRIX_EFFECTS"),
        "post": _reg(cam, "POST_EFFECTS"),
        "grade_fx": _reg(grade, "RENDERERS"),
        "matte_fx": _reg(matte, "RENDERERS"),
        "text": _reg(mods.get("text"), "RENDERERS"),
        "graphics": _reg(mods.get("graphics"), "RENDERERS"),
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
    o.setdefault("enhance_audio", True)
    o.setdefault("modules", {})
    o.setdefault("sys_path", [])
    o.setdefault("dither", 1.0)
    if "workers" not in o:
        o["workers"] = max(1, min(4, (os.cpu_count() or 4) // 3))
    return o


class _Progress:
    """Progreso global 0..100 monotono por etapas (rangos dinamicos) + cancelacion."""

    def __init__(self, opts: dict, stages: dict | None = None):
        self.on_pct = opts.get("on_pct")
        self.on_step = opts.get("on_step")
        self.cancel = opts.get("cancel")
        self.stages = stages or STAGES
        self.last = -1.0

    def step(self, msg: str) -> None:
        _log(msg)
        if self.on_step:
            self.on_step(msg)

    def pct(self, stage: str, p: float) -> None:
        self.check()
        a, b = self.stages.get(stage, (self.last, self.last))
        v = round(a + (b - a) * max(0.0, min(100.0, p)) / 100.0, 1)
        if self.on_pct and v > self.last:
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

    def mapped(track):
        out = []
        for i, it in enumerate(fx.get(track) or []):
            if not it.get("enabled", True):
                continue
            r = tl.map_item(it)
            if r is None:
                continue
            fs, fe = r[0], min(r[1], fdur)
            if fe - fs <= 1e-6:
                continue
            d = copy.deepcopy(it)
            d["_fs"], d["_fe"], d["_ord"] = fs, fe, i
            out.append(d)
        return out

    phrases = tl.map_words(plan.get("phrases") or [])
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

    graphics = by_registry("graphics", "kind", reg["graphics"])
    if opts.get("progress") and "progress" in reg["graphics"] and \
            not any(g.get("kind") == "progress" for g in graphics):
        graphics.append({"id": "gr_progress", "kind": "progress", "enabled": True, "src": "auto",
                         "intensity": 1.0, "_fs": 0.0, "_fe": fdur, "_ord": 10_000,
                         **(opts["progress"] if isinstance(opts["progress"], dict) else {})})
    for it in text:
        it["_behind"] = _is_behind(it, "text", mods)
    for it in graphics:
        it["_behind"] = _is_behind(it, "graphics", mods)
    cutaways = by_registry("cutaways", "kind", reg["cutaways"])
    freq = getattr(mods.get("cutaways"), "frame_request", None) if mods.get("cutaways") else None
    for it in cutaways:
        if not callable(freq):
            break
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
        align = it.get("align") or "auto"
        frac = {"center": 0.5, "start": 0.0, "end": 1.0}.get(align)
        if frac is None:
            frac = float(reg["windows"].get(k, 0.5))
        if not snapped and frac < 0.5:
            # mezcla A->B sin cambio de contenido: se dibuja igual (A congelado sobre el
            # video que sigue) pero se avisa para que la UI lo muestre
            warnings.append(f"transitions:{iid}:sin_union")
        L = max(1, int(round(float(it.get("dur") or 0.35) * fps)))
        F0 = max(0, J - int(round(L * frac)))
        F1 = min(n_frames, F0 + L)
        d = copy.deepcopy(it)
        d.update(_J=J, _F0=F0, _F1=F1, _P=J - 1, _ord=i, _fs=F0 / fps, _fe=F1 / fps,
                 _dur_final=(F1 - F0) / fps, _snapped=snapped)
        transitions.append(d)

    # ---- captions (+ rangos ocultos durante titulos)
    cap = copy.deepcopy(fx.get("captions") or {})
    captions_on = bool(cap.get("enabled", True)) and cap.get("preset") != "none"
    if captions_on and reg["captions"] is None:
        skip("captions:draw_captions")
        captions_on = False
    hide = []
    if cap.get("hide_during_titles", True):
        hide += [(it["_fs"], it["_fe"]) for it in text if not it.get("keep_captions")]
        hide += [(it["_fs"], it["_fe"]) for it in cutaways
                 if isinstance(it.get("text"), dict) and str(it["text"].get("text") or "").strip()]
    cap["_hide"] = _merge_ranges(hide)

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
    logo = opts.get("logo")
    if logo:
        logo = dict(logo)
        logo.setdefault("path", str(paths.BRAND / "logo.png"))
        if not Path(logo["path"]).exists():
            skip("logo:archivo")
            logo = None
    cuts_frames = sorted(set(tl.join_frames(fps)) | set(tl.shot_change_frames(plan.get("shots"), fps)))
    job = {
        "W": W, "H": H, "fps": fps, "quality": opts["quality"], "n_frames": n_frames,
        "final_duration": fdur, "seed": int(director.get("seed", 7)),
        "style": director.get("style", "dinamico"), "timeline": tl.to_dict(),
        "entities": dict(plan.get("entities") or {}), "shots": shots, "words": words,
        "phrases": phrases, "captions": cap, "captions_on": captions_on,
        "text": text, "graphics": graphics, "cutaways": cutaways, "effects": effects,
        "transitions": transitions, "grade_cfg": grade_cfg,
        "grade_on": bool(grade_cfg.get("enabled", True)) and mods.get("grade") is not None,
        "matte_ranges": matte_ranges, "logo": logo, "cut_frames": cuts_frames,
        "layout": list(layout_box(W, H)), "warnings": warnings,
        "dither": float(opts.get("dither", 1.0) or 0.0),
        "modules": dict(opts.get("modules") or {}), "sys_path": list(opts.get("sys_path") or []),
        "strict": bool(opts.get("strict")),
    }
    return job, skipped


def set_anchors(job: dict, face: np.ndarray | None, mods: dict) -> None:
    """Ancla de encuadre FIJA por efecto de camara (camera.item_anchor: mediana de la cara en
    su rango). Determinista y estable: el mismo item encuadra igual en todos los procesos."""
    cam = mods.get("camera")
    fn = getattr(cam, "item_anchor", None) if cam is not None else None
    if not callable(fn):
        return
    rows = _face_rows(face, job["n_frames"]) if face is not None else None
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
        dith = float(job.get("dither", 0.0) or 0.0)
        self._grain = _Grain(self.W, self.H, dith) if dith > 0 and job["quality"] != "draft" else None
        z = lambda it: (int(it.get("z", 0) or 0), it.get("_ord", 0))  # noqa: E731
        self.text = sorted(job["text"], key=z)
        self.graphics = sorted([g for g in job["graphics"] if g.get("kind") != "progress"], key=z)
        self.progress = [g for g in job["graphics"] if g.get("kind") == "progress"]
        self.cutaways = sorted(job["cutaways"], key=z)
        self.effects = job["effects"]
        self.transitions = sorted(job["transitions"], key=lambda t: t["_F0"])

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
        x = bx + (mx if pos in ("tl", "bl") else bw - lw - mx)
        y = by + (my if pos in ("tl", "tr") else bh - lh - my)
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
                          style=self.job["style"],
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
                        quality=ctx.quality, seed=ctx.seed, style=ctx.style, shot=ctx.shot,
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
        self._cutaways(self._active(self.cutaways, t), t, lctx)
        # 5) graficos, texto, subtitulos
        self._draw("graphics", a_gfx, t, lctx, "kind", boxed=True)
        self._draw("text", a_text, t, lctx, "preset", boxed=True)
        if self.job.get("captions_on") and self.reg["captions"] is not None:
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


def _make_grader(mods: dict, base, shots, W, H, fps, cache_dir):
    G = mods["grade"].Grader
    kw = {"cache_dir": cache_dir} if cache_dir and _accepts(G, "cache_dir") else {}
    g = G(base, shots, W, H, fps, **kw)
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
            grader = _make_grader(mods, analysis.get("base"), job["shots"], job["W"], job["H"],
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


class _SafeMain:
    """Mientras se crean los procesos spawn: los workers NO deben re-ejecutar el script
    principal. spawn reimporta __main__ (por __spec__.name o por __file__) como __mp_main__ en
    cada worker, y p. ej. app/server.py arranca a nivel de modulo ollama, la revalidacion de
    licencia y FastAPI. Se ocultan __file__ y __spec__ SIEMPRE (los workers solo necesitan
    motion.render: el initializer y la tarea se serializan por referencia) y se limita el
    numero de hilos BLAS/OpenMP que heredan (evita sobresuscripcion con N procesos)."""

    def __init__(self, env: dict | None = None):
        self.env = {k: str(v) for k, v in (env or {}).items()}

    def __enter__(self):
        self.mod = sys.modules.get("__main__")
        self.saved: dict[str, Any] = {}
        if self.mod is not None:
            if hasattr(self.mod, "__file__"):
                self.saved["__file__"] = self.mod.__file__
                del self.mod.__file__
            self.saved["__spec__"] = getattr(self.mod, "__spec__", None)
            self.mod.__spec__ = None
        self.old_env = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        return self

    def __exit__(self, *a):
        if self.mod is not None:
            if "__file__" in self.saved:
                self.mod.__file__ = self.saved["__file__"]
            self.mod.__spec__ = self.saved.get("__spec__")
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def worker_threads(nw: int) -> int:
    return max(1, (os.cpu_count() or 4) // max(1, nw))


def _run_parallel(job: dict, analysis: dict, tasks: list, nw: int, n: int,
                  prog: "_Progress") -> list[dict]:
    """Reparte los chunks en `nw` procesos spawn. Un worker que muere o falla al iniciar
    rompe el pool con error claro (nunca se cuelga); cancelar mata los procesos."""
    from concurrent.futures import ProcessPoolExecutor, FIRST_EXCEPTION, wait
    ctxm = mp.get_context("spawn")
    counter = ctxm.Value("i", 0)
    nproc = max(1, min(nw, len(tasks)))
    th = worker_threads(nproc)
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
                 prog: "_Progress | None" = None) -> Path | None:
    """Pista de rostro del base (sin suavizar a traves de uniones/tomas), cacheada."""
    fp = _face_file(job, mods, cache, bkey)
    if fp is None:
        return None
    if not fp.exists():
        if prog:
            prog.step("Siguiendo el rostro…")
        cam = mods["camera"]
        kw = {"cuts": job.get("cut_frames")} if _accepts(cam.face_track, "cuts") else {}
        try:
            arr = np.asarray(cam.face_track(base_video, job["W"], job["H"], job["fps"],
                                            job["n_frames"], **kw), np.float32)
            tmp = fp.with_name(f"{fp.stem}.{os.getpid()}.{uuid.uuid4().hex[:6]}.part.npy")
            np.save(tmp, arr)
            os.replace(tmp, fp)
        except Exception as e:  # noqa
            _log(f"face_track fallo: {e}")
    return fp if fp.exists() else None


def _analysis(job: dict, mods: dict, base: dict, cache_dir: Path, bkey: str, prog: _Progress,
              stats: dict) -> dict:
    W, H, fps = job["W"], job["H"], job["fps"]
    out = {"base": base["video"], "cache_dir": str(cache_dir), "face_path": None,
           "grader_pickle": None, "matte_pickle": None}
    t0 = time.time()
    fp = _ensure_face(job, mods, base["video"], cache_dir, bkey, prog)
    face = None
    if fp is not None:
        out["face_path"] = str(fp)
        face = np.load(fp)
        V.touch(fp)
    set_anchors(job, face, mods)
    stats["t_face"] = round(time.time() - t0, 3)
    prog.pct("analysis", 20)
    if job["matte_ranges"] and mods.get("matte") is not None:
        prog.step("Recortando a la persona (matte)…")
        t1 = time.time()
        try:
            msrc, mw, mh = base["video"], W, H
            if job.get("matte_box"):
                msrc = _ensure_fg_video(base["video"], job, prog.cancel)
                mw, mh = job["matte_box"][2], job["matte_box"][3]
            out["matte_src"] = msrc
            mp_ = mods["matte"].MatteProvider(msrc, mw, mh, fps, job["matte_ranges"],
                                              str(cache_dir), job["quality"])
            mp_.prepare(on_pct=lambda p: prog.pct("analysis", 20 + 0.7 * p))
            if getattr(mp_, "dir", None) is not None:
                V.touch(mp_.dir)                 # LRU: el matte en uso no se poda
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
    prog.pct("analysis", 90)
    if job["grade_on"]:
        try:
            g = _make_grader(mods, base["video"], job["shots"], W, H, fps, str(cache_dir))
            out["_grader_obj"] = g
            try:
                out["grader_pickle"] = pickle.dumps(g)
            except Exception:
                out["grader_pickle"] = None
        except Exception as e:  # noqa
            _log(f"grade fallo al preparar: {e}")
            job["grade_on"] = False
    stats["t_analysis"] = round(time.time() - t0, 3)
    prog.pct("analysis", 100)
    return out


def _audio(plan: dict, job: dict, tl: T.Timeline, mods: dict, voice: str, out_wav: Path,
           prog: _Progress, stats: dict, cache_dir: Path | None = None) -> str:
    t0 = time.time()
    fx = plan["fx"]
    dur = job["final_duration"]
    events = []
    for it in fx.get("sfx") or []:
        if not it.get("enabled", True):
            continue
        try:
            at = tl.remap_clamped(float(it["at"]))
            ev = {"id": it.get("id"), "name": it.get("name"), "at": round(at, 4),
                  "gain_db": float(it.get("gain_db", -6.0)), "pan": float(it.get("pan", 0.0))}
        except (KeyError, TypeError, ValueError):
            stats["skipped"].append(f"sfx:{it.get('id')}:dato_invalido")
            continue
        if at < dur:
            events.append(ev)
    mcfg = copy.deepcopy(fx.get("music") or {})

    def remap_list(key, fn):
        out = []
        for s in mcfg.get(key) or []:
            try:
                r = fn(s)
            except (KeyError, TypeError, ValueError, AttributeError):
                stats["skipped"].append(f"music:{key}:dato_invalido")
                continue
            if r is not None:
                out.append(r)
        mcfg[key] = out

    remap_list("stops", lambda s: dict(s, at=tl.remap_clamped(float(s["at"]))))
    remap_list("swells", lambda s: (lambda r: dict(s, start=r[0], end=r[1]) if r else None)(
        tl.map_range(float(s["start"]), float(s["end"]))))
    if mcfg.get("drop_at") is not None:
        try:
            mcfg["drop_at"] = tl.remap_clamped(float(mcfg["drop_at"]))
        except (TypeError, ValueError):
            mcfg["drop_at"] = None
    music_on = bool(mcfg.get("enabled", True)) and mcfg.get("source", "ai") != "none"
    mus, aud = mods.get("music"), mods.get("audio")
    if music_on and mus is not None and hasattr(mus, "get_music"):
        prog.step("Preparando la música…")
        try:
            m = mus.get_music(mcfg, dur, job["style"], job["seed"],
                              lambda p: prog.pct("audio", 0.8 * p))
            if m:
                mcfg.update({k: m[k] for k in ("wav", "bpm", "beats") if k in m})
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
        prog.step("Mezclando voz, música y efectos…")
        try:
            info = aud.mix(voice, dur, events, mcfg, str(out_wav)) or {}
            stats["audio"] = {k: v for k, v in info.items() if isinstance(v, (int, float, str, bool))}
            if out_wav.exists():
                result = str(out_wav)
        except Exception as e:  # noqa
            _log(f"mezcla fallo ({e}); se usa solo la voz")
            stats["skipped"].append("audio:error")
    elif events and aud is None:
        stats["skipped"].append("audio:modulo")
    stats["sfx_events"] = len(events)
    stats["t_audio"] = round(time.time() - t0, 3)
    prog.pct("audio", 100)
    return result


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
    job["matte_box"], job["matte_box_base"] = _pillar_boxes(info, o)
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
    base = ensure_base(plan, o, pr, prog)
    stats: dict[str, Any] = {"skipped": []}
    bkey = Path(base["video"]).stem.replace("base_", "")
    an = _analysis(job, mods, base, Path(o["cache_dir"]), bkey, prog, stats)
    return {"base": base["video"], "voice": base["voice"], "face_path": an.get("face_path"),
            "matte": bool(an.get("_matte_obj")), "grade": bool(an.get("_grader_obj")),
            "t_total": round(time.time() - t0, 3), **stats}


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
    return {"timeline": 0.5, "base": 1 if base_cached else 28,
            "analysis": 2 + (0 if face_cached else 5) + (14 if job["matte_ranges"] and mods.get("matte") else 0),
            "audio": 1 + (12 if music_on else 0) + (2 if fx.get("sfx") else 0),
            "render": 50, "mux": 3 + (3 if o.get("shorts") else 0)}


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
    for s in pr["skipped"]:
        _log(f"se omite {s}")
    prog.pct("timeline", 100)

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

    # 4) audio
    work = cache / f"job_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    tmp_out = V._tmp_path(out_path)
    try:
        mix = _audio(pr["plan"], job, tl, mods, base["voice"], work / "mix.wav", prog, stats)
        mix = _fit_audio(mix, int(round(n / fps * V.SR)), work / "mix_fit.wav")

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
            results = _run_parallel(job, analysis, tasks, nw, n, prog)
            threads = worker_threads(min(nw, len(tasks)))
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
        prog.step("Uniendo y exportando…")
        t0 = time.time()
        video_only = work / "video.mp4"
        if len(tasks) == 1:
            os.replace(tasks[0][2], video_only)
        else:
            V.concat([t[2] for t in sorted(tasks)], video_only, cancel=prog.cancel)
        prog.pct("mux", 40)
        V.mux(video_only, mix, tmp_out, cancel=prog.cancel)
        os.replace(tmp_out, out_path)
        stats["t_mux"] = round(time.time() - t0, 3)
        prog.pct("mux", 70)
        if o.get("shorts"):
            prog.step("Cortando los shorts…")
            stats["shorts"] = render_shorts(pr["plan"], out_path, o, tl)
        prog.pct("mux", 100)
    except BaseException:
        tmp_out.unlink(missing_ok=True)
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
                    g = _make_grader(mods, str(bv), job["shots"], W, H, fps, str(cache))
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
    comp = Compositor(job, mods, grader, matte, face,
                      base_video=str(bv) if have_base else None, frame_source=base_at)
    for tr in active_tr:
        comp.frame(tr["_P"], base_at(tr["_P"]), transitions=False)
    return comp.frame(n, base_n).copy()


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
