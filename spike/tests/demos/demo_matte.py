"""Demo visual de matte.py: mascaras MODNet reales (gating, suavizado, upsample guiado) y los
efectos de sujeto (color_pop, bg_blur, bg_replace, subject_outline) sobre cuadros reales.

Uso: cd spike && .venv/bin/python tests/demos/demo_matte.py [--mp4]
Salida: spike/work/demos/matte/*.png (+ mp4 corto con --mp4)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _real_frames as RF  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import paths  # noqa: E402
from motion import camera, ctx as C, grade, matte  # noqa: E402

OUT = RF.SPIKE / "work" / "demos" / "matte"
FPS = 30


def provider(ranges, quality="final", W=1080, H=1920):
    base = RF.base_video(W, H)
    return matte.MatteProvider(base, W, H, FPS, ranges, RF.FRAMES / "matte_cache", quality)


def face_of(ft, n):
    f = ft[min(n, len(ft) - 1)]
    return tuple(float(v) for v in f) if np.all(np.isfinite(f)) else None


def mk_ctx(mp, n, face=None, W=1080, H=1920, quality="final"):
    c = C.FrameCtx(W=W, H=H, fps=FPS, n=n, t=n / FPS, t_orig=n / FPS, quality=quality, face=face)
    c._matte_fn = lambda: mp.get(n)
    return c


def overlay_mask(rgba, m):
    """Visualizacion: sujeto normal, fondo tenido de magenta oscuro, borde en amarillo."""
    out = rgba[..., :3].astype(np.float32)
    tint = np.array([90, 0, 70], np.float32)
    a = m[..., None]
    vis = out * (0.25 + 0.75 * a) + tint * (1 - a) * 0.6
    edge = cv2.Canny((m * 255).astype(np.uint8), 60, 160)
    vis[edge > 0] = (255, 220, 0)
    return np.clip(vis, 0, 255).astype(np.uint8)


def main(mp4: bool = False):
    OUT.mkdir(parents=True, exist_ok=True)
    T = RF.SHOT_T
    ranges = [(int(t * FPS) - 15, int(t * FPS) + 15) for t in T]
    mp = provider(ranges)
    t0 = time.time()
    mp.prepare()
    print(f"prepare (180 cuadros, cache si existe): {time.time() - t0:.1f} s, error={mp.error}")
    frames = RF.frames_at(T)
    g = grade.Grader(RF.base_video(), RF.SHOTS, 1080, 1920, FPS, cache_dir=RF.FRAMES)
    g.prepare()
    gcfg = {"look": "warm_clean", "vignette": 0.12, "grain": 0.02, "sharpen": 0.12}
    ft = camera.face_track(RF.base_video(), 1080, 1920, FPS, int(37.7 * FPS), cache_dir=RF.FRAMES)

    # 1) mascaras por toma: cuadro | alfa | recorte sobre gris (calidad del rango en la etiqueta)
    tiles, labels = [], []
    for i, (t, fr) in enumerate(zip(T, frames)):
        n = int(t * FPS)
        m = mp.get(n)
        q = mp.quality_of(*ranges[i])
        cut = (fr[..., :3].astype(np.float32) * m[..., None] + 90.0 * (1 - m[..., None])).astype(np.uint8)
        tiles += [fr, (np.dstack([m] * 3) * 255).astype(np.uint8), cut]
        labels += [f"{i} {RF.SHOT_NAMES[i]}", f"alfa (calidad {q})", "recorte"]
    RF.sheet(tiles, labels, cols=6, tile_w=260,
             title="matte: MODNet + compuerta de persona + gating por rango + guided upsample").save(OUT / "01_masks.png")

    # 1b) antes / despues de la compuerta semantica (MODNet solo mete muebles al sujeto)
    tiles, labels = [], []
    for i in (2, 5, 0):
        fr = np.ascontiguousarray(frames[i][..., :3])
        small = cv2.resize(fr, (360, 640), interpolation=cv2.INTER_AREA)
        f = max(matte.detect_faces(small), key=lambda f: f[2] * f[3])
        crop = mp._crop_for([f])
        dims = mp._inf_dims(crop)
        x0, y0, x1, y1 = crop
        x = cv2.resize(fr[y0:y1, x0:x1], dims, interpolation=cv2.INTER_AREA)
        raw = matte.infer(x)
        pf = cv2.resize(matte.person_prob(fr), (270, 480))
        pc = cv2.resize(pf[y0 // 4:y1 // 4, x0 // 4:x1 // 4], dims)
        fc = [((f[0] * 1080 - x0) / (x1 - x0), (f[1] * 1920 - y0) / (y1 - y0), f[2] * 1080 / (x1 - x0),
               f[3] * 1920 / (y1 - y0), 0.9)]
        gated, rem = matte.gate_matte(raw, fc, pc, 16.0 * dims[0] / (x1 - x0))
        prior, rem2 = matte.gate_matte(raw, fc, None)
        for m_, lab in ((raw, "MODNet solo"), (gated, f"con persona (-{rem:.0%})"), (prior, f"prior geometrico (-{rem2:.0%})")):
            vis = (x.astype(np.float32) * (0.18 + 0.82 * m_[..., None])).astype(np.uint8)
            tiles.append(vis)
            labels.append(f"{i} {lab}")
    RF.sheet(tiles, labels, cols=3, tile_w=320, title="matte: compuerta semantica de persona").save(OUT / "05_gate.png")

    # 2) recorte 100% del borde (cabeza/cabello) para juzgar el upsample guiado
    tiles, labels = [], []
    for i in (0, 3, 5):
        n = int(T[i] * FPS)
        f = face_of(ft, n) or (0.5, 0.3, 0.1, 0.06)
        cx, cy, fw = int(f[0] * 1080), int(f[1] * 1920), f[2] * 1080
        w = int(max(300, fw * 3.2))
        h = int(w * 1.25)
        x, y = max(0, cx - w // 2), max(0, int(cy - h * 0.45))
        fr = frames[i]
        m = mp.get(n)
        tiles += [fr[y:y + h, x:x + w], (np.dstack([m[y:y + h, x:x + w]] * 3) * 255).astype(np.uint8),
                  overlay_mask(fr, m)[y:y + h, x:x + w]]
        labels += [f"{i} cuadro", "alfa", "borde"]
    RF.sheet(tiles, labels, cols=3, tile_w=360, title="matte: borde a 100% (cabeza)").save(OUT / "02_edges.png")

    # 3) efectos (sobre el cuadro ya con grade), 3 tomas con persona
    fx = [("color_pop", {"bg_desat": 1.0, "bg_dark": 0.25}),
          ("bg_blur", {"radius": 24}),
          ("bg_replace", {"background": "gradient", "bg_colors": ["#0F2027", "#2C5364", "#7FD1C7"]}),
          ("subject_outline", {"color": "#FFFFFF", "width": 10, "glow": 0.5}),
          ("bw_subject", {"contrast": 1.3, "subject_color": True})]
    tiles, labels = [], []
    for i in (0, 3, 5, 2):
        n = int(T[i] * FPS)
        face = tuple(ft[n]) if np.all(np.isfinite(ft[n])) else None
        ctx = mk_ctx(mp, n, face)
        base = g.apply(C.image_from_rgba(frames[i]), gcfg, i, ctx)
        tiles.append(C.image_to_rgba(base))
        labels.append(f"{i} base (grade)")
        for name, it in fx:
            t = time.perf_counter()
            if name == "bw_subject":
                o = grade.bw(base, it, 1.0, 2.0, ctx)
            else:
                o = matte.RENDERERS[name](base, it, 1.0, 2.0, ctx)
            dt = (time.perf_counter() - t) * 1000
            tiles.append(C.image_to_rgba(o))
            labels.append(f"{name} {dt:.0f} ms")
    RF.sheet(tiles, labels, cols=len(fx) + 1, tile_w=270, title="matte: efectos de sujeto").save(OUT / "03_effects.png")

    # 4) animacion de entrada de cada efecto (hoja de tiempos)
    n0 = int(T[3] * FPS) - 12
    rng = [(n0, n0 + 24)]
    mp2 = provider(rng)
    mp2.prepare()
    fr2 = RF.frames_at([k / FPS for k in range(n0, n0 + 24, 4)])
    tiles, labels = [], []
    for name, it in fx[:4]:
        for j, k in enumerate(range(n0, n0 + 24, 4)):
            lt = (k - n0) / FPS
            ctx = mk_ctx(mp2, k, tuple(ft[k]) if np.all(np.isfinite(ft[k])) else None)
            base = g.apply(C.image_from_rgba(fr2[j]), gcfg, 3, ctx)
            o = matte.RENDERERS[name](base, it, lt, 1.6, ctx)
            tiles.append(C.image_to_rgba(o))
            labels.append(f"{name} t={lt:.2f}s")
    RF.sheet(tiles, labels, cols=6, tile_w=240, title="matte: entrada de los efectos (bg_replace: revelado desde la cara)").save(
        OUT / "04_anim.png")

    # 5) tiempos por llamada (final y draft), cv2 a 1 hilo como en un worker de render
    cv2.setNumThreads(1)
    for q, W, H in (("final", 1080, 1920), ("draft", 540, 960)):
        mpq = mp if q == "final" else provider(ranges, "draft", W, H)
        if q == "draft":
            mpq.prepare()
        n = int(T[0] * FPS)
        frq = RF.frame_at(T[0], W, H) if q == "draft" else frames[0]
        img = C.image_from_rgba(frq)
        for name, it in fx[:4]:
            ctx = mk_ctx(mpq, n, (0.5, 0.2, 0.1, 0.04), W, H, q)
            matte.RENDERERS[name](img, it, 1.0, 2.0, ctx)
            best = 1e9
            for _ in range(6):
                t = time.perf_counter()
                matte.RENDERERS[name](img, it, 1.0, 2.0, ctx)
                best = min(best, time.perf_counter() - t)
            print(f"{q} {name} (1 hilo): {best * 1000:.1f} ms")
        t = time.perf_counter()
        for _ in range(20):
            mpq._lru.clear()
            mpq.get(n)
        print(f"{q} get(n) desde cache: {(time.perf_counter() - t) / 20 * 1000:.1f} ms")
    cv2.setNumThreads(0)

    if mp4:
        make_mp4(mp2, n0, ft)


def make_mp4(mp2, n0, ft):
    """Clip de 3.6 s en la calle (con grade): color_pop entra, reemplazo de fondo con revelado
    y luego contorno tipo sticker."""
    n_frames = 108
    n0 = max(0, n0 - 30)
    rng = [(n0, n0 + n_frames)]
    mp3 = provider(rng)
    mp3.prepare()
    g = grade.Grader(RF.base_video(), RF.SHOTS, 1080, 1920, FPS, cache_dir=RF.FRAMES)
    g.prepare()
    gcfg = {"look": "warm_clean", "vignette": 0.12, "grain": 0.02, "sharpen": 0.12}
    frs = RF.frames_at([k / FPS for k in range(n0, n0 + n_frames)])
    out = OUT / "matte_demo.mp4"
    cmd = [paths.FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "540x960",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    segs = [("color_pop", {"bg_desat": 1.0, "bg_dark": 0.3}, 0, 36),
            ("bg_replace", {"background": "gradient", "bg_colors": ["#0F2027", "#2C5364", "#7FD1C7"]}, 36, 72),
            ("subject_outline", {"width": 10, "glow": 0.6}, 72, 108)]
    for j, k in enumerate(range(n0, n0 + n_frames)):
        ctx = mk_ctx(mp3, k, tuple(ft[k]) if np.all(np.isfinite(ft[k])) else None)
        img = g.apply(C.image_from_rgba(frs[j]), gcfg, 3, ctx)
        for name, it, a, b in segs:
            if a <= j < b:
                img = matte.RENDERERS[name](img, it, (j - a) / FPS, (b - a) / FPS, ctx)
        a = cv2.resize(C.image_to_rgba(img), (540, 960), interpolation=cv2.INTER_AREA)
        p.stdin.write(a.tobytes())
    p.stdin.close()
    p.wait()
    print("mp4:", out)


if __name__ == "__main__":
    main("--mp4" in sys.argv)
