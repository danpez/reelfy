"""Demo visual de grade.py: igualacion de tomas, looks, B/N filmico y comparacion lado a lado
con la referencia (ejemplo_objetivo.mp4: cuadros 'despues').

Uso: cd spike && .venv/bin/python tests/demos/demo_grade.py
Salida: spike/work/demos/grade/*.png (+ metricas y tiempos por cuadro a 1 hilo en consola)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _real_frames as RF  # noqa: E402

import numpy as np  # noqa: E402

from motion import ctx as C, grade  # noqa: E402

OUT = RF.SPIKE / "work" / "demos" / "grade"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    base = RF.base_video()
    g = grade.Grader(base, RF.SHOTS, 1080, 1920, 30, cache_dir=RF.FRAMES)
    t0 = time.time()
    g.prepare()
    print(f"prepare: {time.time() - t0:.2f} s")
    for i, c in enumerate(g.corr):
        print(i, RF.SHOT_NAMES[i], [round(v, 3) for v in c["gains"]], round(c["black"], 3), round(c["white"], 3))
    frames = RF.frames_at(RF.SHOT_T)
    ctx = C.demo_ctx(1080, 1920)

    # 1) igualacion: original / igualado (natural, sin vinieta ni grano) / look por defecto
    tiles, labels = [], []
    for i, fr in enumerate(frames):
        img = C.image_from_rgba(fr)
        m = g.apply(img, {"look": "natural", "match_shots": True, "vignette": 0, "grain": 0, "sharpen": 0,
                          "strength": 0.0, "clarity": 0}, i, ctx)
        d = g.apply(img, {"look": "warm_clean", "match_shots": True, "vignette": 0.15, "grain": 0.025,
                          "sharpen": 0.15}, i, ctx)
        tiles += [fr, C.image_to_rgba(m), C.image_to_rgba(d)]
        labels += [f"{i} crudo", f"{i} igualado", f"{i} warm_clean"]
    RF.sheet(tiles, labels, cols=6, tile_w=300,
             title="grade: crudo | igualado | warm_clean (por toma)").save(OUT / "01_match.png")

    # 2) looks sobre 3 tomas
    looks = list(grade.LOOKS)
    tiles, labels = [], []
    for i in (0, 3, 5):
        img = C.image_from_rgba(frames[i])
        tiles.append(frames[i])
        labels.append(f"{i} crudo")
        for lk in looks:
            o = g.apply(img, {"look": lk, "match_shots": True, "vignette": 0.15, "grain": 0.025,
                              "sharpen": 0.15}, i, ctx)
            tiles.append(C.image_to_rgba(o))
            labels.append(lk)
    RF.sheet(tiles, labels, cols=len(looks) + 1, tile_w=260, title="grade: looks").save(OUT / "02_looks.png")

    # 3) recortes al 100% (piel y detalle): crudo vs looks
    crops = {0: (360, 300, 380, 520), 1: (420, 380, 420, 540), 2: (300, 560, 400, 540),
             3: (320, 560, 420, 540), 5: (200, 680, 420, 540)}
    tiles, labels = [], []
    for i, (x, y, w, h) in crops.items():
        fr = frames[i]
        img = C.image_from_rgba(fr)
        tiles.append(fr[y:y + h, x:x + w])
        labels.append(f"{i} crudo")
        for lk in ("natural", "warm_clean", "bright_pop", "cinematic"):
            o = C.image_to_rgba(g.apply(img, {"look": lk, "vignette": 0.15, "grain": 0.025, "sharpen": 0.15}, i, ctx))
            tiles.append(o[y:y + h, x:x + w])
            labels.append(lk)
    RF.sheet(tiles, labels, cols=5, tile_w=330, title="grade: recortes 100% (piel)").save(OUT / "03_crops.png")

    # 4) B/N filmico: contraste y sujeto a color (con la cara real de face_track)
    from motion import camera, matte
    ft = camera.face_track(RF.base_video(), 1080, 1920, 30, int(37.7 * 30), cache_dir=RF.FRAMES)
    ranges = [(int(t * 30) - 15, int(t * 30) + 15) for t in RF.SHOT_T]
    mp = matte.MatteProvider(RF.base_video(), 1080, 1920, 30, ranges, RF.FRAMES / "matte_cache", "final")
    mp.prepare()
    tiles, labels = [], []
    gcfg = {"look": "warm_clean", "vignette": 0.15, "grain": 0.025, "sharpen": 0.15}
    for i in (0, 3, 5):
        n = int(RF.SHOT_T[i] * 30)
        face = tuple(ft[n]) if np.all(np.isfinite(ft[n])) else None
        cx = C.FrameCtx(W=1080, H=1920, fps=30, n=n, face=face)
        cx._matte_fn = (lambda n=n: mp.get(n))
        base_img = g.apply(C.image_from_rgba(frames[i]), gcfg, i, cx)
        tiles.append(C.image_to_rgba(base_img))
        labels.append(f"{i} warm_clean")
        for it in ({"contrast": 0.8}, {"contrast": 1.2}, {"contrast": 1.8}, {"contrast": 1.3, "subject_color": True},
                   {"contrast": 1.2, "tone": "warm"}):
            o = C.image_to_rgba(grade.bw(base_img, dict(it, fade=0), 1.0, 2.0, cx))
            y = o[..., 0].astype(np.float32) / 255
            p2, p50, p98 = np.percentile(y, [2, 50, 98])
            tiles.append(o)
            labels.append(f"bw c={it['contrast']}{' color' if it.get('subject_color') else ''}"
                          f"{' ' + it['tone'] if it.get('tone') else ''} p2={p2:.2f} p98={p98:.2f}")
    RF.sheet(tiles, labels, cols=6, tile_w=260, title="grade.bw filmico (cara normalizada, vinieta en la cara)").save(
        OUT / "04_bw.png")

    # 5) referencia vs Reelfy (el 'despues' de ejemplo_objetivo.mp4, recortado del lado derecho)
    ref_video = RF.MEDIA / "ejemplo_objetivo.mp4"
    if ref_video.exists():
        import subprocess
        import paths
        tiles, labels = [], []
        for tr, (i, bwit) in ((0.5, (0, None)), (8.0, (3, None)), (14.0, (0, {"contrast": 1.3}))):
            png = RF.FRAMES / f"_ref_{tr:.1f}.png"
            if not png.exists():
                subprocess.run([paths.FFMPEG, "-v", "error", "-y", "-ss", str(tr), "-i", str(ref_video),
                                "-frames:v", "1", str(png)], check=True)
            from PIL import Image
            ref = np.asarray(Image.open(png).convert("RGB"))
            tiles += [ref[122:548, 20:260], ref[88:577, 281:555]]
            labels += [f"ref antes {tr}s", f"ref despues {tr}s"]
            n = int(RF.SHOT_T[i] * 30)
            face = tuple(ft[n]) if np.all(np.isfinite(ft[n])) else None
            cx = C.FrameCtx(W=1080, H=1920, fps=30, n=n, face=face)
            o = g.apply(C.image_from_rgba(frames[i]), gcfg, i, cx)
            if bwit:
                o = grade.bw(o, dict(bwit, fade=0), 1.0, 2.0, cx)
            tiles += [frames[i], C.image_to_rgba(o)]
            labels += [f"reelfy crudo {i}", "reelfy " + ("bw" if bwit else "warm_clean")]
        RF.sheet(tiles, labels, cols=4, tile_w=260, title="referencia (izq) vs Reelfy (der)").save(OUT / "05_vs_ref.png")

    # 6) metricas del look y tiempos a 1 hilo (como un worker de render)
    for i, fr in enumerate(frames):
        o = C.image_to_rgba(g.apply(C.image_from_rgba(fr), gcfg, i, ctx))
        y0 = fr[..., :3].astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32) / 255
        y1 = o[..., :3].astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32) / 255
        sat = lambda a: float(((a[..., :3].max(-1) - a[..., :3].min(-1)) / np.maximum(a[..., :3].max(-1), 1)).mean())  # noqa: E731
        print(f"toma {i}: p10 {np.percentile(y0, 10):.3f}->{np.percentile(y1, 10):.3f}  "
              f"p50 {np.percentile(y0, 50):.3f}->{np.percentile(y1, 50):.3f}  saturacion {sat(o) / sat(fr) - 1:+.0%}")
    import cv2
    cv2.setNumThreads(1)
    img = C.image_from_rgba(frames[0])
    cfg = {"look": "warm_clean", "match_shots": True, "vignette": 0.15, "grain": 0.025, "sharpen": 0.15}
    for q, W, H in (("final", 1080, 1920), ("draft", 540, 960)):
        cx = C.demo_ctx(W, H, quality=q)
        gg = grade.Grader(None, [], W, H, 30)
        gg.corr = g.corr
        im = C.image_from_rgba(np.ascontiguousarray(cv2.resize(frames[0], (W, H), interpolation=cv2.INTER_AREA)))
        gg.apply(im, cfg, 0, cx)
        best = 1e9
        for k in range(12):
            cx.n = k
            t = time.perf_counter()
            gg.apply(im, cfg, 0, cx)
            best = min(best, time.perf_counter() - t)
        print(f"apply {q} {W}x{H} (1 hilo): {best * 1000:.2f} ms")
        cxf = C.FrameCtx(W=W, H=H, fps=30, n=5, face=(0.5, 0.3, 0.1, 0.05), quality=q)
        grade.bw(im, {"contrast": 1.3}, 0.5, 2.0, cxf)
        best = 1e9
        for k in range(8):
            t = time.perf_counter()
            grade.bw(im, {"contrast": 1.3}, 0.5, 2.0, cxf)
            best = min(best, time.perf_counter() - t)
        print(f"bw {q} (1 hilo): {best * 1000:.2f} ms")
    cv2.setNumThreads(0)


if __name__ == "__main__":
    main()
