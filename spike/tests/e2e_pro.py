#!/usr/bin/env python3
"""E2E real del motor pro POR HTTP contra un servidor de Reelfy ya levantado.

Flujo (docs/MOTION-ENGINE.md 6.1): sube los clips crudos (/sources) -> /compose con el
director -> espera `review` (plan v2 con shots y fx) -> /frame en 3 tiempos -> /preview pro
(render_draft con audio) -> /render final con shorts -> descarga y valida:
  - duracion = la del timeline del plan (+-1 cuadro), 1080x1920, H.264 + AAC
  - audio integrado -14 LUFS (+-1) y true peak <= -1.0 dBTP; la musica ENTRO a la mezcla
    (stats.music.source ai/procedural) y el render no omitio nada (stats.skipped == [])
  - barra del analisis monotona (nunca retrocede) y el montaje avanza (no se queda en 4 %)
  - orientacion vertical correcta: cara derecha arriba, cabeza y pies visibles (como en la fuente)
  - overlays presentes (cuadro exacto con y sin overlays difiere donde hay texto/graficos)
  - /frame coincide con el export (misma edicion, diferencia de compresion)
Avisos (no fallan la prueba, quedan en e2e_result.json -> warnings): VLM que cayo a
heuristicas, tomas que los recortes de la IA eliminaron completas (o casi) del final.
Guarda todo en --out (spike/work/e2e, ignorado por git) y reporta tiempos por etapa, con la
carga de la maquina y la etiqueta --label (frio: DATA nuevo; caliente: caches llenas).

No se versionan datos de clientes: los medios vienen de REELFY_TEST_MEDIA o spike/work/media.
Uso:  python tests/e2e_pro.py --base http://127.0.0.1:8013 [--media DIR] [--out DIR]
      (lo lanza tests/e2e_pro.sh, que levanta el servidor aislado)
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPIKE = HERE.parent
sys.path.insert(0, str(SPIKE / "scripts"))

import numpy as np  # noqa: E402

import paths  # noqa: E402

T0 = time.time()
LOG: list[str] = []


def log(msg: str) -> None:
    line = f"[e2e {time.time() - T0:7.1f}s] {msg}"
    print(line, flush=True)
    LOG.append(line)


def load() -> str:
    try:
        return os.getloadavg().__repr__()
    except Exception:  # noqa
        return "?"


# ---------------------------------------------------------------- HTTP
class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _req(self, method, path, data=None, headers=None, timeout=600, raw=False):
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return (body, dict(r.headers)) if raw else json.loads(body or b"null")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read()[:400]!r}") from None

    def get(self, path, raw=False, timeout=600):
        return self._req("GET", path, raw=raw, timeout=timeout)

    def post(self, path, obj=None, raw=False, timeout=600):
        return self._req("POST", path, json.dumps(obj or {}).encode(),
                         {"Content-Type": "application/json"}, raw=raw, timeout=timeout)

    def upload(self, path, files):
        bnd = "----reelfy" + uuid.uuid4().hex
        chunks = []
        for f in files:
            ct = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            chunks.append((f"--{bnd}\r\nContent-Disposition: form-data; name=\"files\"; "
                           f"filename=\"{f.name}\"\r\nContent-Type: {ct}\r\n\r\n").encode())
            chunks.append(f.read_bytes())
            chunks.append(b"\r\n")
        chunks.append(f"--{bnd}--\r\n".encode())
        return self._req("POST", path, b"".join(chunks),
                         {"Content-Type": f"multipart/form-data; boundary={bnd}"}, timeout=1800)

    def download(self, path, dst):
        body, _ = self.get(path, raw=True)
        Path(dst).write_bytes(body)
        return Path(dst)


def wait(api: Api, job: str, until, what: str, timeout: float, stages: dict | None = None,
         poll: float = 1.0, trace: list | None = None):
    """Sondea /status (sin plan: ?plan=0) hasta until(st). Registra el primer momento de
    cada `stage` (tiempos por etapa) y, con `trace`, la serie (t, stage, pct, eta)."""
    t0 = time.time()
    last_msg = None
    while True:
        st = api.get(f"/status/{job}?plan=0")
        if trace is not None and st.get("phase") not in ("review", "done", "error"):
            trace.append((round(time.time() - t0, 1), st.get("stage"), st.get("pct"), st.get("eta")))
        if stages is not None:
            for key in ("stage", "preview_stage", "analysis_stage"):
                sname = st.get(key)
                if sname and f"{key}:{sname}" not in stages:
                    stages[f"{key}:{sname}"] = round(time.time() - t0, 1)
        msg = (st.get("phase"), st.get("stage"), st.get("message"))
        if msg != last_msg:
            log(f"{what}: {st.get('phase')} / {st.get('stage')} {st.get('pct')}% - {st.get('message')}")
            last_msg = msg
        if st.get("phase") == "error":
            raise RuntimeError(f"{what}: error: {st.get('error')}")
        if until(st):
            return st, time.time() - t0
        if time.time() - t0 > timeout:
            raise TimeoutError(f"{what}: sin terminar en {timeout:.0f}s ({st.get('message')})")
        time.sleep(poll)


# ---------------------------------------------------------------- medicion
def ffprobe(path) -> dict:
    r = subprocess.run([str(paths.FFPROBE), "-v", "error", "-show_streams", "-show_format",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def loudness(path) -> dict:
    r = subprocess.run([str(paths.FFMPEG), "-hide_banner", "-nostats", "-i", str(path), "-vn",
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    txt = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", txt)
    p = re.search(r"Peak:\s+(-?[\d.]+) dBFS", txt)
    return {"lufs": float(i.group(1)) if i else None, "true_peak": float(p.group(1)) if p else None}


def frame_at(path, t, W=1080, H=1920) -> np.ndarray:
    r = subprocess.run([str(paths.FFMPEG), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf",
                        f"scale={W}:{H}:flags=bicubic,scale=in_color_matrix=bt709:in_range=tv,format=rgb24",
                        "-f", "rawvideo", "pipe:1"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(H, W, 3)


def jpeg_rgb(data: bytes) -> np.ndarray:
    import io
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def person_metrics(rgb: np.ndarray) -> dict:
    """Cara (YuNet) y extension vertical de la persona (PP-HumanSeg), normalizadas."""
    import cv2
    from motion import matte
    small = cv2.resize(rgb, (rgb.shape[1] // 2, rgb.shape[0] // 2), interpolation=cv2.INTER_AREA)
    faces = [f for f in matte.detect_faces(small) if f[4] >= 0.6]
    face = max(faces, key=lambda f: f[2] * f[3]) if faces else None
    out = {"face": [round(float(v), 3) for v in face] if face else None}
    try:
        p = matte.person_prob(rgb)
        rows = np.where((p > 0.5).mean(axis=1) > 0.02)[0]
        out["person_top"] = round(float(rows.min()) / p.shape[0], 3) if len(rows) else None
        out["person_bottom"] = round(float(rows.max() + 1) / p.shape[0], 3) if len(rows) else None
    except Exception as e:  # noqa: sin el modelo de persona solo se valida la cara
        out["person_error"] = str(e)
    return out


def contact_sheet(video, out_jpg, n=12, cols=6, W=270, H=480):
    from PIL import Image, ImageDraw
    info = ffprobe(video)
    dur = float(info["format"]["duration"])
    rows = math.ceil(n / cols)
    sheet = Image.new("RGB", (cols * W, rows * H), (0, 0, 0))
    for i in range(n):
        t = (i + 0.5) * dur / n
        im = Image.fromarray(frame_at(video, t, W, H))
        ImageDraw.Draw(im).text((6, 4), f"{t:5.2f}s", fill=(255, 255, 0))
        sheet.paste(im, ((i % cols) * W, (i // cols) * H))
    sheet.save(out_jpg, quality=86)
    return out_jpg


# ---------------------------------------------------------------- flujo
def plan_windows(plan):
    """Ventanas FINALES de items del plan: overlays (texto/graficos), escenas, camara y
    transiciones (para elegir tiempos limpios)."""
    from motion import timeline as TL
    tl = TL.Timeline.from_plan(plan, fps=30)
    fx = plan.get("fx") or {}
    over, busy = [], []
    for tr in ("text", "graphics"):
        for it in fx.get(tr) or []:
            if it.get("enabled", True) and not it.get("behind"):
                r = tl.map_item(it)
                if r:
                    over.append(r)
    for it in fx.get("cutaways") or []:
        r = tl.map_item(it) if it.get("enabled", True) else None
        if r:
            busy.append(r)
    for it in fx.get("effects") or []:
        # zoom/kenburns suaves se permiten (el director los pone en casi todo el video); se
        # excluyen los golpes de camara y los efectos que cambian el fondo o el cuadro entero
        if it.get("enabled", True) and it.get("kind") in ("punch", "shake", "bg_replace",
                                                          "flash_frame", "rgb_split"):
            r = tl.map_item(it)
            if r:
                busy.append(r)
    for it in fx.get("transitions") or []:
        a = tl.remap_clamped(float(it.get("at", 0)))
        d = float(it.get("dur", 0.4) or 0.4)
        busy.append((a - d, a + d))
    return tl, over, busy


def orientation_checks(final, plan, tl, busy, D, checks, n=6):
    """Orientacion vertical correcta en el export: en cuadros sin escenas/golpes/transiciones,
    la cara esta derecha (YuNet no detecta caras giradas) y arriba con la cabeza completa, y
    la persona llega abajo (pies) como en la FUENTE en el mismo instante."""
    def clean(t):
        return not any(a0 - 0.1 <= t <= b0 + 0.1 for a0, b0 in busy)
    cands = [float(t) for t in np.arange(0.5, D - 0.5, 0.25) if clean(t)]
    picks = [cands[int(i * (len(cands) - 1) / max(1, n - 1))] for i in range(n)] if len(cands) >= n else cands
    picks = sorted(set(picks))
    orient = []
    for tf in picks:
        m = person_metrics(frame_at(final, tf))
        t_src = tl.inverse(tf)
        ms = person_metrics(frame_at(plan["video"], t_src))   # la fuente compuesta (display)
        orient.append({"t_final": round(tf, 2), "t_src": round(t_src, 2), "out": m, "src": ms})
    faces_ok = [o for o in orient if o["out"]["face"] and 0.02 < o["out"]["face"][1] < 0.6
                and o["out"]["face"][1] - o["out"]["face"][3] / 2 > 0.0]
    feet_cmp = [o for o in orient if o["out"].get("person_bottom") is not None
                and o["src"].get("person_bottom") is not None and o["src"]["person_bottom"] >= 0.95]
    # con zoom suave el borde inferior puede recortarse un poco: basta con llegar al 85 %
    feet_ok = [o for o in feet_cmp if o["out"]["person_bottom"] >= 0.85]
    checks["vertical_head_visible"] = len(orient) >= 3 and len(faces_ok) >= int(0.8 * len(orient))
    checks["vertical_feet_like_source"] = len(feet_cmp) >= 2 and len(feet_ok) >= int(0.8 * len(feet_cmp))
    return {"orientation": orient}


def run(args, res: dict) -> dict:
    api = Api(args.base)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    media = Path(args.media)
    movs = sorted(media.glob("IMG_*.MOV"))
    if len(movs) < 6:
        raise SystemExit(f"faltan medios: {len(movs)} IMG_*.MOV en {media}")
    movs = movs[:6]
    res.update({"base": args.base, "label": args.label, "load_start": load(), "timings": {},
                "checks": {}, "warnings": []})
    T = res["timings"]

    st = api.get("/setup")
    log(f"setup: {st.get('state')} llm={st.get('llm')} ({st.get('llm_model')}) musica={st['optional']['music']['state']}")

    # 1) fuentes
    t = time.time()
    src = api.upload("/sources", movs)["sources"]
    T["upload_s"] = round(time.time() - t, 1)
    assert len(src) == 6, src
    for s in src:
        assert s["h"] > s["w"], f"fuente no vertical: {s}"
    log(f"subidos 6 clips en {T['upload_s']}s: {[round(s['dur'], 2) for s in src]}")

    # 2) compose + analisis + director
    main, acc = [], 0.0
    for s in src:
        main.append({"sid": s["id"], "type": "video", "in": 0, "out": s["dur"], "start": round(acc, 3)})
        acc += s["dur"]
    t = time.time()
    job = api.post("/compose", {"main": main, "highlights": 2, "glossary": "",
                                "director": {"style": args.style, "intensity": 0.75, "seed": 7,
                                             "music": args.music}})["job_id"]
    res["job"] = job
    stages: dict = {}
    trace: list = []
    wait(api, job, lambda s: s.get("phase") == "review", "analisis", 1800, stages, poll=0.5,
         trace=trace)
    T["compose_to_review_s"] = round(time.time() - t, 1)
    T["analysis_stages_first_seen_s"] = stages
    res["load_review"] = load()
    # barra: nunca retrocede y el montaje avanza (antes: fija en 4 % todo el compose)
    pcts = [float(p) for _t, _s, p, _e in trace if p is not None]
    back = [(a, b) for a, b in zip(pcts, pcts[1:]) if b < a - 0.05]
    comp = [float(p) for _t, s_, p, _e in trace if s_ == "compose" and p is not None]
    res["progress"] = {"n": len(trace), "backwards": back[:5], "compose_max": max(comp) if comp else None,
                       "etas": sum(1 for *_x, e in trace if e is not None),
                       "trace_sample": trace[:: max(1, len(trace) // 40)]}
    res["checks"]["progress_monotonic"] = len(pcts) >= 5 and not back
    res["checks"]["progress_compose_moves"] = bool(comp) and max(comp) >= 10.0
    got = api.get(f"/plan/{job}")
    plan = got["plan"]
    (out / "plan_v2.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    fx = plan.get("fx") or {}
    counts = {k: len(fx.get(k) or []) for k in ("text", "graphics", "cutaways", "effects", "transitions", "sfx")}
    res["plan"] = {"version": plan.get("version"), "shots": len(plan.get("shots") or []),
                   "fx": counts, "director": {k: (plan.get("director") or {}).get(k)
                                              for k in ("style", "llm", "vlm", "timings")},
                   "music": {k: (fx.get("music") or {}).get(k) for k in ("source", "enabled", "prompt")},
                   "plan_rev": got["plan_rev"]}
    st = api.get(f"/status/{job}?plan=0")
    res["review_warnings"] = st.get("warnings")
    dd = plan.get("director") or {}
    res["director_timings"] = dd.get("timings")
    res["director_notes"] = dd.get("notes")
    if dd.get("vlm") == "heuristico":
        res["warnings"].append("director: el VLM cayo a heuristicas (tomas sin descripcion de la IA)")
    log(f"review en {T['compose_to_review_s']}s: shots={res['plan']['shots']} fx={counts} "
        f"llm={res['plan']['director']['llm']} vlm={dd.get('vlm')} timings={dd.get('timings')} "
        f"warnings={st.get('warnings')}")
    ok_plan = plan.get("version") == 2 and len(plan.get("shots") or []) == 6 and \
        counts["text"] > 0 and counts["graphics"] > 0 and counts["transitions"] > 0
    res["checks"]["plan_v2_shots6_fx"] = ok_plan
    # limites de toma = limites reales de los clips
    bounds = [round(s["start"], 2) for s in plan["shots"]]
    exp, acc = [], 0.0
    for s in src:
        exp.append(round(acc, 2))
        acc += s["dur"]
    res["plan"]["shot_starts"] = bounds
    res["checks"]["shots_match_clips"] = len(bounds) == 6 and max(abs(a - b) for a, b in zip(bounds, exp)) < 0.1

    tl, over, busy = plan_windows(plan)
    D = tl.n_frames(30) / 30.0
    res["expected_duration"] = round(D, 3)
    # cuanto de cada toma (clip) queda en el final: una toma eliminada completa se marca
    kept = []
    for i, s in enumerate(plan["shots"]):
        r = tl.map_range(float(s["start"]), float(s["end"]))
        kept.append({"shot": i + 1, "src": s.get("src"), "src_s": round(float(s["end"]) - float(s["start"]), 2),
                     "final_s": round((r[1] - r[0]) if r else 0.0, 2)})
    res["shots_kept"] = kept
    gone = [k for k in kept if k["final_s"] < min(0.8, 0.25 * k["src_s"])]
    if gone:
        res["warnings"].append(f"tomas eliminadas (o casi) por los recortes de la IA: "
                               f"{[(k['shot'], k['src'], k['final_s']) for k in gone]}")

    # 3) /frame en 3 tiempos (el analisis de fondo puede estar corriendo)
    frames = []
    for k, frac in enumerate((0.15, 0.5, 0.85)):
        tf = round(D * frac, 3)
        t = time.time()
        data, hdr = api.post(f"/frame/{job}", {"plan": plan, "t_final": tf, "fmt": "9:16",
                                               "quality": "final"}, raw=True)
        dt = round(time.time() - t, 2)
        img = jpeg_rgb(data)
        (out / f"frame_{k + 1}_{tf:.2f}s.jpg").write_bytes(data)
        frames.append({"t_final": tf, "s": dt, "server_ms": int(hdr.get("X-Frame-Ms") or hdr.get("x-frame-ms") or 0),
                       "shape": list(img.shape)})
        log(f"/frame t={tf}s -> {img.shape[1]}x{img.shape[0]} en {dt}s")
    T["frames"] = frames
    res["checks"]["frame_1080x1920"] = all(f["shape"][:2] == [1920, 1080] for f in frames)
    st = api.get(f"/status/{job}?plan=0")
    res["analysis_at_frames"] = {k: st.get(k) for k in ("analysis_state", "analysis_pct", "analysis_stage")}

    # 4) vista previa real (render_draft con audio)
    t = time.time()
    api.post(f"/preview/{job}", {"plan": plan, "pro": True, "fmt": "9:16"})
    pst: dict = {}
    st, _ = wait(api, job, lambda s: s.get("preview_state") in ("done", "error", "canceled"),
                 "preview", 1800, pst)
    T["preview_s"] = round(time.time() - t, 1)
    T["preview_stages_first_seen_s"] = pst
    if st.get("preview_state") != "done":
        raise RuntimeError(f"preview: {st.get('preview_state')} {st.get('preview_error')}")
    pv = api.download(f"/video/{st['preview_file']}", out / "preview_draft.mp4")
    pi = ffprobe(pv)
    vs = [s for s in pi["streams"] if s["codec_type"] == "video"][0]
    res["preview"] = {"file": pv.name, "W": vs["width"], "H": vs["height"],
                      "duration": round(float(pi["format"]["duration"]), 3),
                      "audio": any(s["codec_type"] == "audio" for s in pi["streams"])}
    res["checks"]["preview_540x960_audio"] = (vs["width"], vs["height"]) == (540, 960) and res["preview"]["audio"]
    log(f"preview en {T['preview_s']}s: {res['preview']}")
    st = api.get(f"/status/{job}?plan=0")
    res["analysis_after_preview"] = {k: st.get(k) for k in ("analysis_state", "analysis_pct", "analysis_t")}

    # 5) render final con shorts
    t = time.time()
    api.post(f"/render/{job}", {"plan": plan, "fmt": "9:16", "shorts": True})
    rst: dict = {}
    st, _ = wait(api, job, lambda s: s.get("phase") == "done", "render", 3600, rst)
    T["render_s"] = round(time.time() - t, 1)
    T["render_stages_first_seen_s"] = rst
    res["load_render_end"] = load()
    clips = st["clips"]
    res["render_stats"] = clips[0].get("stats")
    res["render_warnings"] = st.get("warnings")
    final = api.download(f"/video/{clips[0]['file']}", out / "final.mp4")
    shorts = [api.download(f"/video/{c['file']}", out / f"short_{i}.mp4") for i, c in enumerate(clips[1:], 1)]
    log(f"render en {T['render_s']}s: {clips[0]['file']} + {len(shorts)} shorts")

    # 6) validaciones del export
    info = ffprobe(final)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    dur = float(info["format"]["duration"])
    vdur = float(v.get("duration") or dur)
    res["final"] = {"W": v["width"], "H": v["height"], "codec": v["codec_name"], "duration": round(dur, 3),
                    "video_duration": round(vdur, 3), "audio": bool(a),
                    "color": [v.get("color_primaries"), v.get("color_transfer"), v.get("color_space")],
                    "bit_rate": int(info["format"].get("bit_rate") or 0),
                    "rotation": v.get("side_data_list")}
    res["checks"]["final_1080x1920"] = (v["width"], v["height"]) == (1080, 1920)
    res["checks"]["final_duration"] = abs(vdur - D) <= 1.5 / 30 and D > 10
    loud = loudness(final)
    res["final"].update(loud)
    res["checks"]["loudness_-14"] = loud["lufs"] is not None and abs(loud["lufs"] + 14.0) <= 1.0 and \
        loud["true_peak"] is not None and loud["true_peak"] <= -1.0
    rs = res["render_stats"] or {}
    res["checks"]["render_no_skipped"] = rs.get("skipped") == []
    res["checks"]["music_in_mix"] = (rs.get("music") or {}).get("source") in ("ai", "procedural")
    res["shorts"] = []
    for sp in shorts:
        si = ffprobe(sp)
        sv = [s for s in si["streams"] if s["codec_type"] == "video"][0]
        res["shorts"].append({"file": sp.name, "W": sv["width"], "H": sv["height"],
                              "duration": round(float(si["format"]["duration"]), 2)})
    res["checks"]["shorts"] = len(shorts) >= 1 and all((s["W"], s["H"]) == (1080, 1920) for s in res["shorts"])

    # orientacion: tiempos limpios (sin escenas/camara/transiciones), cara y cuerpo vs la fuente
    res.update(orientation_checks(final, plan, tl, busy, D, res["checks"]))

    # overlays: cuadro exacto con overlays vs sin overlays (mismo tiempo), y export vs /frame
    bare = copy.deepcopy(plan)
    bfx = bare["fx"]
    for k in ("text", "graphics", "cutaways", "transitions"):
        bfx[k] = []
    bfx["captions"] = dict(bfx.get("captions") or {}, enabled=False)
    ov_t = sorted({round((a0 + b0) / 2, 2) for a0, b0 in over if b0 - a0 > 0.5})
    ov_t = ov_t[:: max(1, len(ov_t) // 5)][:5]
    ovs = []
    for tf in ov_t:
        full, _ = api.post(f"/frame/{job}", {"plan": plan, "t_final": tf, "fmt": "9:16"}, raw=True)
        nof, _ = api.post(f"/frame/{job}", {"plan": bare, "t_final": tf, "fmt": "9:16"}, raw=True)
        fa, fb = jpeg_rgb(full).astype(np.int16), jpeg_rgb(nof).astype(np.int16)
        d = np.abs(fa - fb).max(axis=2)
        ex = frame_at(final, math.floor(tf * 30 + 1e-6) / 30 + 1 / 60).astype(np.int16)
        ovs.append({"t_final": tf, "diff_mean": round(float(d.mean()), 2),
                    "px_changed": round(float((d > 40).mean()), 4),
                    "export_vs_frame_mean": round(float(np.abs(ex - fa).mean()), 2)})
        (out / f"overlay_{tf:.2f}s.jpg").write_bytes(full)
    res["overlays"] = ovs
    res["checks"]["overlays_present"] = len(ovs) >= 3 and \
        sum(o["px_changed"] > 0.003 for o in ovs) >= max(3, int(0.8 * len(ovs)))
    res["checks"]["frame_matches_export"] = len(ovs) > 0 and \
        float(np.median([o["export_vs_frame_mean"] for o in ovs])) < 8.0

    contact_sheet(final, out / "final_contact.jpg")
    res["ok"] = all(res["checks"].values())
    res["load_end"] = load()
    res["total_s"] = round(time.time() - T0, 1)
    log(f"checks: {json.dumps(res['checks'])}")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8013")
    ap.add_argument("--media", default=os.environ.get("REELFY_TEST_MEDIA") or str(SPIKE / "work" / "media"))
    ap.add_argument("--out", default=str(SPIKE / "work" / "e2e"))
    ap.add_argument("--style", default="dinamico")
    ap.add_argument("--music", default="ai")
    ap.add_argument("--label", default="", help="frio | caliente (solo para el reporte)")
    args = ap.parse_args()
    res: dict = {}
    try:
        run(args, res)
    except Exception as e:  # noqa
        import traceback
        traceback.print_exc()
        res.update(ok=False, error=str(e))
    finally:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "e2e_result.json").write_text(json.dumps(res, indent=1, default=str))
        (Path(args.out) / "e2e_log.txt").write_text("\n".join(LOG))
    print(json.dumps({"ok": res.get("ok"), "label": res.get("label"), "checks": res.get("checks"),
                      "warnings": res.get("warnings"), "timings": res.get("timings"),
                      "director_timings": res.get("director_timings")}, indent=1, default=str))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
