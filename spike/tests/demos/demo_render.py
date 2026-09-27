"""Demo visual del nucleo (timeline + base pass + compositor) sobre el caso real de la dentista.

Uso (desde spike/):
  .venv/bin/python tests/demos/demo_render.py            # director + modulos reales
  .venv/bin/python tests/demos/demo_render.py --stubs    # renderers stub (solo nucleo)
  .venv/bin/python tests/demos/demo_render.py --plan work/demos/render/plan_v2_rules.json
  opciones: --workers N, --draft, --llm (director con LLM/VLM local), --no-render,
            --shorts, --formats (hoja 9:16/4:5/1:1/16:9), --preview (tiempos de /frame con solo
            el base en cache y con ensure_analysis), --dither T (degradado con/sin grano final)

Salida en spike/work/demos/render/:
  <nombre>.mp4                 render completo
  <nombre>_antes_despues_N.jpg hojas de contacto: crudo (izq, tiempo original) vs editado
                               (der, tiempo final), cada ~1 s, con tiempos anotados
  <nombre>_transiciones.jpg    cuadros a lo largo de cada ventana de transicion
  <nombre>_stats.json          tiempos por etapa, ms/cuadro, errores/omitidos
Nada de esto se versiona (spike/work/ esta ignorado).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SPIKE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SPIKE / "scripts"))
sys.path.insert(0, str(SPIKE / "tests"))

from motion import render as R           # noqa: E402
from motion import video_io as V         # noqa: E402
from motion.timeline import Timeline     # noqa: E402

OUT = SPIKE / "work" / "demos" / "render"
MEDIA = Path(__import__("os").environ.get("REELFY_TEST_MEDIA") or Path(__file__).resolve().parents[2] / "work" / "media")
FIX = SPIKE / "work" / "fixtures" / "dentista_plan_v1.json"
TW, TH = 216, 384                        # miniatura 9:16


def _font(size=15):
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def grab(path: str, t: float, W: int = TW, H: int = TH) -> np.ndarray:
    cmd = [V.FFMPEG, "-v", "error", "-ss", f"{max(0.0, t):.4f}", "-i", str(path), "-frames:v", "1",
           "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  "scale=in_color_matrix=bt709:in_range=tv,format=rgb24",
           "-f", "rawvideo", "pipe:1"]
    b = subprocess.run(cmd, capture_output=True).stdout
    if len(b) < W * H * 3:
        return np.zeros((H, W, 3), np.uint8)
    return np.frombuffer(b[:W * H * 3], np.uint8).reshape(H, W, 3)


def grab_final(path: str, n: int, fps: float) -> np.ndarray:
    with V.FrameReader(path, TW, TH, n, 1, fps) as r:
        a = r.read()
    return a[..., :3] if a is not None else np.zeros((TH, TW, 3), np.uint8)


def sheet(pairs, title: str, out: Path, cols: int = 6):
    """pairs: [(img_izq|None, img_der, etiqueta_izq, etiqueta_der)]."""
    f, fs = _font(15), _font(20)
    cw = (TW * 2 + 6) if pairs and pairs[0][0] is not None else TW
    cell_w, cell_h = cw + 14, TH + 44
    rows = (len(pairs) + cols - 1) // cols
    img = Image.new("RGB", (cols * cell_w + 10, rows * cell_h + 44), (18, 18, 22))
    d = ImageDraw.Draw(img)
    d.text((10, 10), title, font=fs, fill=(240, 240, 240))
    for i, (a, b, la, lb) in enumerate(pairs):
        x, y = 10 + (i % cols) * cell_w, 44 + (i // cols) * cell_h
        if a is not None:
            img.paste(Image.fromarray(a), (x, y + 20))
            d.text((x + 2, y + 2), la, font=f, fill=(170, 170, 170))
            x += TW + 6
        img.paste(Image.fromarray(b), (x, y + 20))
        d.text((x + 2, y + 2), lb, font=f, fill=(255, 210, 63))
    img.save(out, quality=86)
    return out


def make_sheets(plan: dict, video: Path, name: str, fps: float) -> list[Path]:
    tl = Timeline.from_plan(plan, fps=fps)
    n = tl.n_frames()
    src = plan["video"]
    outs = []
    times = np.arange(0.25, tl.final_duration, 1.0)
    pairs = []
    for tf in times:
        k = int(round(tf * fps))
        to = tl.inverse(k / fps)
        pairs.append((grab(src, to), grab_final(str(video), k, fps), f"antes {to:05.2f}s",
                      f"despues {tf:05.2f}s"))
    per = 12
    for i in range(0, len(pairs), per):
        outs.append(sheet(pairs[i:i + per], f"{name}: antes (crudo, t original) / despues "
                          f"(Reelfy, t final)  [{i // per + 1}]",
                          OUT / f"{name}_antes_despues_{i // per + 1}.jpg", cols=4))
    # transiciones: cuadros a lo largo de cada ventana
    job = R.prepare(plan, {"modules": {}})["job"]
    tp = []
    for tr in job["transitions"][:6]:
        F0, F1 = tr["_F0"], tr["_F1"]
        ks = sorted(set([max(0, F0 - 2)] + [int(round(F0 + (F1 - F0) * q)) for q in
                                              (0.0, 0.2, 0.4, 0.6, 0.8)] + [min(n - 1, F1 + 1)]))
        for k in ks:
            tp.append((None, grab_final(str(video), k, fps), "",
                       f"{tr['kind']} {k / fps:05.2f}s p={(k - F0 + 0.5) / max(1, F1 - F0):.2f}"))
    if tp:
        outs.append(sheet(tp, f"{name}: transiciones (cuadros dentro de cada ventana)",
                          OUT / f"{name}_transiciones.jpg", cols=7))
    return outs


def frame_sheet(rows, title: str, out: Path, h: int = 384):
    """rows: [(etiqueta, [(img RGBA/RGB, texto), ...])] -> hoja con celdas de alto h."""
    f, fs = _font(15), _font(20)
    cells = [[(Image.fromarray(np.ascontiguousarray(im[..., :3])).resize(
        (max(2, int(round(im.shape[1] * h / im.shape[0]))), h), Image.LANCZOS), lb)
        for im, lb in r] for _, r in rows]
    width = max(sum(c.width + 8 for c, _ in r) for r in cells) + 130
    img = Image.new("RGB", (width, 44 + len(rows) * (h + 30)), (18, 18, 22))
    d = ImageDraw.Draw(img)
    d.text((10, 10), title, font=fs, fill=(240, 240, 240))
    for i, ((lab, _), r) in enumerate(zip(rows, cells)):
        y = 44 + i * (h + 30)
        d.text((10, y + h // 2), lab, font=f, fill=(255, 210, 63))
        x = 120
        for c, lb in r:
            img.paste(c, (x, y + 22))
            d.text((x + 2, y + 3), lb, font=f, fill=(170, 170, 170))
            x += c.width + 8
    img.save(out, quality=88)
    return out


def demo_formats(plan: dict, name: str, times=(1.2, 5.0, 11.0, 17.5, 21.0, 27.5)) -> Path:
    """El mismo plan en 9:16, 4:5, 1:1 y 16:9 (render_frame con modulos reales)."""
    rows = []
    fmts = ("9:16", "4:5", "1:1", "16:9")
    timing = {}
    for t in times:
        r = []
        for fm in fmts:
            t0 = time.time()
            fr = R.render_frame(plan, t, {"fmt": fm})
            timing.setdefault(fm, []).append(round(time.time() - t0, 2))
            r.append((fr, f"{fm}  {fr.shape[1]}x{fr.shape[0]}"))
        rows.append((f"t={t:05.2f}s", r))
    print("render_frame por formato (s):", json.dumps(timing))
    return frame_sheet(rows, f"{name}: formatos (caja de layout 9:16, reencuadre por formato)",
                       OUT / f"{name}_formatos.jpg", h=360)


def demo_preview(plan: dict, name: str, final_mp4: str, times=(4.4, 7.5, 14.5, 20.3)) -> dict:
    """Flujo del Studio: solo el base en cache (ensure_base ya corrio) -> /frame; despues
    ensure_analysis -> /frame exacto. Mide tiempos y compara contra el export."""
    import os
    import shutil
    cache = OUT / "_preview_cache"
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True)
    b = Path(json.load(open(OUT / f"{name}_stats.json"))["base"])
    os.symlink(b, cache / b.name)
    (cache / (b.name + ".json")).write_text(Path(str(b) + ".json").read_text())
    opts = {"cache_dir": str(cache)}
    res = {"solo_base": [], "con_analisis": []}
    rows = []
    R.render_frame(plan, 0.5, opts)                      # importa modulos (una vez por proceso)
    for key in ("solo_base", "con_analisis"):
        if key == "con_analisis":
            t0 = time.time()
            ea = R.ensure_analysis(plan, opts)
            res["t_ensure_analysis"] = round(time.time() - t0, 2)
            res["ensure_analysis"] = {k: v for k, v in ea.items() if k not in ("base", "voice")}
            R._FRAME_CACHE.clear()
            R.render_frame(plan, 0.5, opts)
        r = []
        for t in times:
            t0 = time.time()
            fr = R.render_frame(plan, t, opts)
            dt = time.time() - t0
            n = int(t * 30)
            with V.FrameReader(final_mp4, 1080, 1920, n, 1, 30) as rd:
                ref = rd.read()
            diff = float(np.abs(fr[..., :3].astype(int) - ref[..., :3].astype(int)).mean())
            res[key].append({"t": t, "s": round(dt, 2), "diff_vs_export": round(diff, 2)})
            r.append((fr, f"{key} t={t:.1f} {dt:.2f}s d={diff:.1f}"))
        rows.append((key, r))
    shutil.rmtree(cache, ignore_errors=True)
    frame_sheet(rows, f"{name}: vista previa /frame (solo base en cache vs con analisis)",
                OUT / f"{name}_preview.jpg", h=420)
    (OUT / f"{name}_preview_timing.json").write_text(json.dumps(res, indent=1))
    print("preview:", json.dumps(res, indent=1))
    return res


def demo_dither(plan: dict, name: str, t: float) -> Path:
    """Degradado oscuro de una escena: sin y con el grano/dither final (contraste estirado x6
    para que se vean las bandas)."""
    rows = []
    for d in (0.0, 1.0):
        fr = R.render_frame(plan, t, {"dither": d})[..., :3]
        crop = fr[1100:1700, 100:700].astype(np.float32)
        lo = np.percentile(crop, 1)
        st = np.clip((crop - lo) * 6.0, 0, 255).astype(np.uint8)
        rows.append((f"dither={d}", [(crop.astype(np.uint8), "normal"), (st, "contraste x6")]))
    return frame_sheet(rows, f"{name}: grano/dither final en t={t:.2f}s (bandas en degradados)",
                       OUT / f"{name}_dither.jpg", h=420)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stubs", action="store_true")
    ap.add_argument("--plan", default=None)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--formats", action="store_true", help="hoja 9:16 / 4:5 / 1:1 / 16:9")
    ap.add_argument("--preview", action="store_true", help="tiempos de /frame (Studio)")
    ap.add_argument("--dither", type=float, default=None, help="t final para la hoja de dither")
    ap.add_argument("--shorts", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    opts = {"quality": "draft" if a.draft else "final", "shorts": a.shorts}
    if a.workers is not None:
        opts["workers"] = a.workers
    if a.stubs:
        from _render_plan import STUBS, stub_plan
        plan, name = stub_plan(MEDIA / "crudo_completo.mp4"), "nucleo_stubs"
        opts.update(modules=STUBS, sys_path=[str(SPIKE / "tests")])
    elif a.plan:
        plan, name = json.load(open(a.plan)), Path(a.plan).stem.replace("_plan_v2", "")
    else:
        from motion import director as D
        p = json.load(open(FIX))
        p["video"] = str(MEDIA / "crudo_completo.mp4")
        t0 = time.time()
        plan = D.direct(p, style="dinamico", intensity=0.75, seed=7, use_llm=a.llm)
        print(f"director: {time.time() - t0:.1f}s")
        name = "dentista_llm" if a.llm else "dentista"
        json.dump(plan, open(OUT / f"{name}_plan_v2.json", "w"), ensure_ascii=False, indent=1)
    video = OUT / f"{name}{'_draft' if a.draft else ''}.mp4"
    if not a.no_render:
        st = R.render_pro(plan, video, opts)
        (OUT / f"{name}_stats.json").write_text(json.dumps(st, indent=1, default=str))
        print(json.dumps({k: st.get(k) for k in ("t_total", "t_base", "t_analysis", "t_audio",
                                                 "t_compose", "ms_per_frame_proc",
                                                 "ms_per_frame_compose", "errors", "skipped")},
                         indent=1, default=str))
    fps = 30.0
    if not (a.formats or a.preview or a.dither is not None) or not a.no_render:
        for s in make_sheets(plan, video, name + ("_draft" if a.draft else ""), fps):
            print("hoja:", s)
    if a.formats:
        print("hoja:", demo_formats(plan, name))
    if a.preview:
        demo_preview(plan, name, str(video))
    if a.dither is not None:
        print("hoja:", demo_dither(plan, name, a.dither))


if __name__ == "__main__":
    main()
