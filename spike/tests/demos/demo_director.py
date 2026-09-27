"""Demo visual del IA director (motion/director.py) sobre el caso real de la dentista.

Escribe en spike/work/demos/director/ (ignorado por git):
  plan_v2_<estilo>.json      plan v2 completo que decidio el director
  edicion_<estilo>.txt       tabla legible (tiempo final/original, pista, kind, detalle) + metricas
  timeline_<estilo>.png      pistas en tiempo final (tomas, texto, graficos, escenas, efectos,
                             transiciones, SFX, musica) para revisar ritmo y variedad
  storyboard_<estilo>_N.png  cuadros REALES del compositor (render.render_frame, draft) en cada
                             evento visual, con el tiempo final y la decision anotados
  anim_<estilo>_N.png        hoja de contacto por animacion: 4 cuadros a lo largo de cada titulo,
                             escena, tarjeta y CTA (tiempo local anotado)
  draft_<estilo>.mp4         (con --mp4) video completo draft 540x960 con audio (se renderiza
                             ANTES de las hojas para que usen el base y los mattes en cache)

Medios de prueba: REELFY_TEST_MEDIA (carpeta con crudo_completo.mp4) o conftest.TEST_MEDIA.

Uso (desde spike/):
  .venv/bin/python tests/demos/demo_director.py [--styles dinamico,elegante] [--no-llm] [--mp4]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
SPIKE = HERE.parents[2]
sys.path.insert(0, str(SPIKE / "scripts"))
sys.path.insert(0, str(HERE.parents[1]))

import numpy as np  # noqa: E402
import skia  # noqa: E402

from motion import assets, director as D  # noqa: E402
from motion import ctx as C  # noqa: E402

try:
    from conftest import TEST_MEDIA as _TM  # noqa: E402
except Exception:  # pragma: no cover
    _TM = None
import os  # noqa: E402

FIXTURE = SPIKE / "work" / "fixtures" / "dentista_plan_v1.json"
MEDIA = Path(os.environ.get("REELFY_TEST_MEDIA") or (_TM or SPIKE / "work" / "media"))
VIDEO = MEDIA / "crudo_completo.mp4"
OUT = SPIKE / "work" / "demos" / "director"

LANES = [("shots", "Tomas", "#6B7280"), ("text", "Texto", "#FFD23F"), ("graphics", "Graficos", "#2EC4B6"),
         ("cutaways", "Escenas", "#FF7A3D"), ("effects", "Efectos", "#A78BFA"),
         ("transitions", "Transiciones", "#F472B6"), ("sfx", "SFX", "#60A5FA"), ("music", "Musica", "#34D399")]


def _font(size, name="Inter-SemiBold"):
    return assets.font(name, size)


def _label(it: dict, track: str) -> str:
    if track == "text":
        t = it.get("text") or " / ".join(ln.get("text", "") for ln in it.get("lines") or [])
        return f"{it['preset']}: {t}".replace("\n", " / ")
    if track == "graphics":
        k = it["kind"]
        if k in ("doodle", "icon"):
            return f"{k}:{it.get('name')}"
        if k == "card":
            return f"card {it.get('variant')}"
        if k == "cta":
            return f"cta {it.get('variant')}"
        return f"{k}: {it.get('text') or it.get('title') or ''}"
    if track == "cutaways":
        t = it.get("text") or {}
        return f"{it['kind']} ({it.get('background')}) {t.get('preset', '')}"
    if track == "effects":
        return it["kind"]
    if track == "transitions":
        return it["kind"]
    if track == "sfx":
        return it["name"]
    return ""


def timeline_png(plan: dict, path: Path, title: str) -> None:
    tl = D._timeline(plan)
    Dur = tl.final_duration
    W, lane_h, left, top = 2200, 70, 150, 70
    H = top + lane_h * len(LANES) + 40
    arr, surf = C.new_frame(W, H)
    cv = surf.getCanvas()
    cv.clear(C.color("#0E0E12"))
    fx = plan["fx"]
    sx = (W - left - 30) / Dur
    X = lambda t: left + t * sx  # noqa: E731
    f_title = _font(26, "Poppins-SemiBold")
    cv.drawString(title, 20, 42, f_title, C.fill("#FFFFFF"))
    f_lane = _font(18)
    f_it = _font(13, "Inter-Medium")
    for k in range(int(Dur) + 1):
        cv.drawLine(X(k), top - 8, X(k), H - 30, C.stroke("#FFFFFF", 1, 0.07 if k % 5 else 0.18))
        cv.drawString(f"{k}s", X(k) - 8, H - 10, f_it, C.fill("#9CA3AF"))
    for li, (key, name, col) in enumerate(LANES):
        y0 = top + li * lane_h
        cv.drawString(name, 16, y0 + lane_h / 2 + 6, f_lane, C.fill("#D1D5DB"))
        cv.drawLine(left, y0 + lane_h, W - 20, y0 + lane_h, C.stroke("#FFFFFF", 1, 0.08))
        items = []
        if key == "shots":
            for s in plan.get("shots") or []:
                r = tl.map_range(s["start"], s["end"])
                if r:
                    items.append((r[0], r[1], ("persona" if s.get("use") == "a_camara" else "b-roll")
                                  + (f" {s.get('place', '')}" if s.get("place") else "")))
        elif key == "music":
            m = fx.get("music") or {}
            for st in m.get("stops") or []:
                a = tl.remap_clamped(st["at"])
                items.append((a, a + st["dur"], "stop"))
            for sw in m.get("swells") or []:
                items.append((tl.remap_clamped(sw["start"]), tl.remap_clamped(sw["end"]), f"+{sw['db']} dB"))
        else:
            for it in fx.get(key) or []:
                if key in ("transitions", "sfx"):
                    a = tl.remap_clamped(it["at"])
                    b = a + (it.get("dur") or 0.12)
                else:
                    a, b = tl.remap_clamped(it["start"]), tl.remap_clamped(it["end"])
                items.append((a, b, _label(it, key)))
        rows = []                                     # sub-carriles para no encimar etiquetas
        for a, b, lab in sorted(items):
            r = next((i for i, e in enumerate(rows) if e <= a), None)
            if r is None:
                rows.append(0)
                r = len(rows) - 1
            rows[r] = b + 0.02
            n = max(1, min(4, len(rows)))
            hh = (lane_h - 10) / n
            yy = y0 + 5 + (r % 4) * hh
            rect = skia.Rect.MakeLTRB(X(a), yy, max(X(b), X(a) + 3), yy + hh - 3)
            cv.drawRoundRect(rect, 4, 4, C.fill(col, 0.85))
            cv.save()
            cv.clipRect(rect)
            cv.drawString(lab, X(a) + 4, yy + hh * 0.65, f_it, C.fill("#0E0E12"))
            cv.restore()
    from PIL import Image
    Image.fromarray(arr[..., :3].copy()).save(str(path))


def storyboard(plan: dict, style: str, max_frames: int = 36) -> list[Path]:
    from motion import render as R
    tl = D._timeline(plan)
    Dur = tl.final_duration
    ev = []
    for tr in ("text", "graphics", "cutaways", "effects"):
        for it in plan["fx"][tr]:
            if tr == "effects" and it["kind"] not in ("bw", "color_pop", "kenburns"):
                continue
            a, b = tl.remap_clamped(it["start"]), tl.remap_clamped(it["end"])
            t = a + min(0.55, 0.6 * (b - a))
            ev.append((t, f"{tr[:4]} {_label(it, tr)}"))
    for it in plan["fx"]["transitions"]:
        a = tl.remap_clamped(it["at"])
        ev.append((a + 0.02, f"trans {it['kind']}"))
    ev.sort()
    picked = []
    for t, lab in ev:
        if picked and t - picked[-1][0] < 0.33:
            picked[-1] = (picked[-1][0], picked[-1][1] + " + " + lab)
            continue
        picked.append((t, lab))
    if len(picked) > max_frames:
        idx = np.linspace(0, len(picked) - 1, max_frames).round().astype(int)
        picked = [picked[i] for i in sorted(set(idx))]
    opts = {"W": 540, "H": 960, "quality": "draft", "fps": 30}
    thumbs = []
    t0 = time.time()
    for t, lab in picked:
        t = min(max(0.0, t), Dur - 0.05)
        try:
            fr = R.render_frame(plan, t, opts)
        except Exception as ex:  # el storyboard no debe caerse por un renderer ajeno
            fr = np.zeros((960, 540, 4), np.uint8)
            lab += f" [error: {ex}]"
        thumbs.append((t, lab, fr))
    print(f"   storyboard: {len(thumbs)} cuadros en {time.time() - t0:.1f}s")
    cols, tw, th, cap = 6, 270, 480, 58
    per = cols * 3
    out = []
    for si in range(0, len(thumbs), per):
        chunk = thumbs[si:si + per]
        rows = (len(chunk) + cols - 1) // cols
        arr, surf = C.new_frame(cols * tw, rows * (th + cap))
        cv = surf.getCanvas()
        cv.clear(C.color("#0E0E12"))
        f1 = _font(17, "Poppins-SemiBold")
        f2 = _font(12, "Inter-Medium")
        for k, (t, lab, fr) in enumerate(chunk):
            r, c = divmod(k, cols)
            x, y = c * tw, r * (th + cap)
            img = C.image_from_rgba(np.ascontiguousarray(fr))
            cv.drawImageRect(img, skia.Rect.MakeXYWH(x, y, tw, th), C.SAMPLING)
            cv.drawString(f"{t:5.2f}s", x + 6, y + th + 20, f1, C.fill("#FFD23F"))
            words = lab[:120]
            cv.drawString(words[:44], x + 6, y + th + 37, f2, C.fill("#D1D5DB"))
            cv.drawString(words[44:88], x + 6, y + th + 52, f2, C.fill("#D1D5DB"))
        p = OUT / f"storyboard_{style}_{si // per + 1}.png"
        from PIL import Image
        Image.fromarray(arr[..., :3].copy()).save(str(p))
        out.append(p)
    return out


def anim_sheets(plan: dict, style: str, per_item: int = 4) -> list[Path]:
    """Varios cuadros a lo largo de cada animacion (titulos, escenas, tarjetas, CTA)."""
    from motion import render as R
    tl = D._timeline(plan)
    items = []
    for tr in ("text", "cutaways", "graphics"):
        for it in plan["fx"][tr]:
            if tr == "graphics" and it["kind"] not in ("card", "cta", "icon"):
                continue
            a, b = tl.remap_clamped(it["start"]), tl.remap_clamped(it["end"])
            items.append((a, b, f"{tr[:4]} {_label(it, tr)}"))
    items.sort()
    opts = {"W": 540, "H": 960, "quality": "draft", "fps": 30}
    rows = []
    for a, b, lab in items:
        fr = []
        for k in range(per_item):
            lt = (b - a) * (0.08 + 0.84 * k / max(1, per_item - 1))
            try:
                img = R.render_frame(plan, min(tl.final_duration - 0.05, a + lt), opts)
            except Exception as ex:  # noqa
                img = np.zeros((960, 540, 4), np.uint8)
                lab += f" [error: {ex}]"
            fr.append((lt, img))
        rows.append((a, lab, fr))
    tw, th, cap = 216, 384, 40
    per_sheet = 4
    out = []
    for si in range(0, len(rows), per_sheet):
        chunk = rows[si:si + per_sheet]
        arr, surf = C.new_frame(per_item * (tw + 8) + 8, len(chunk) * (th + cap))
        cv = surf.getCanvas()
        cv.clear(C.color("#0E0E12"))
        f1 = _font(15, "Poppins-SemiBold")
        f2 = _font(12, "Inter-Medium")
        for r_, (a, lab, fr) in enumerate(chunk):
            y = r_ * (th + cap)
            cv.drawString(f"{a:5.2f}s  {lab[:150]}", 8, y + 16, f1, C.fill("#FFD23F"))
            for c_, (lt, img) in enumerate(fr):
                x = 8 + c_ * (tw + 8)
                sk = C.image_from_rgba(np.ascontiguousarray(img))
                cv.drawImageRect(sk, skia.Rect.MakeXYWH(x, y + 24, tw, th - 8), C.SAMPLING)
                cv.drawString(f"+{lt:.2f}s", x + 4, y + 40, f2, C.fill("#FFFFFF"))
        p = OUT / f"anim_{style}_{si // per_sheet + 1}.png"
        from PIL import Image
        Image.fromarray(arr[..., :3].copy()).save(str(p))
        out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--styles", default="dinamico")
    ap.add_argument("--intensity", type=float, default=0.75)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-frames", action="store_true")
    ap.add_argument("--mp4", action="store_true")
    ap.add_argument("--anim", action="store_true", help="hojas de contacto por animacion")
    a = ap.parse_args()
    if not (FIXTURE.exists() and VIDEO.exists()):
        print("falta el fixture o el video de prueba")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    plan = json.load(open(FIXTURE))
    plan["video"] = str(VIDEO)
    for style in a.styles.split(","):
        t0 = time.time()
        p2 = D.direct(plan, style=style, intensity=a.intensity, use_llm=not a.no_llm)
        took = time.time() - t0
        st = D.stats(p2)
        (OUT / f"plan_v2_{style}.json").write_text(json.dumps(p2, ensure_ascii=False, indent=1))
        txt = [f"Estilo {style}  intensidad {a.intensity}  llm={p2['director']['llm']}  "
               f"vlm={p2['director'].get('vlm')}  director={took:.1f}s  timings={p2['director']['timings']}",
               f"entidades: {json.dumps(p2['entities'], ensure_ascii=False)}",
               f"metricas: {json.dumps(st)}",
               f"musica: {json.dumps(p2['fx']['music'], ensure_ascii=False)}",
               f"subtitulos: {json.dumps(p2['fx']['captions'])}  color: {json.dumps(p2['fx']['grade'])}",
               f"avisos: {p2['director'].get('warnings')}", "", D.edit_table(p2)]
        (OUT / f"edicion_{style}.txt").write_text("\n".join(txt))
        timeline_png(p2, OUT / f"timeline_{style}.png",
                     f"Reelfy director - {style} ({st['events']} eventos, hueco max {st['max_gap']}s)")
        print(f"[{style}] director {took:.1f}s  {st}")
        if a.mp4:
            from motion import render as R
            t1 = time.time()
            res = R.render_draft(p2, OUT / f"draft_{style}.mp4", {"workers": 6})
            print(f"   draft mp4 {time.time() - t1:.1f}s -> {OUT / f'draft_{style}.mp4'}  {res}")
        if not a.no_frames:
            for pth in storyboard(p2, style):
                print("  ", pth)
        if a.anim:
            t1 = time.time()
            for pth in anim_sheets(p2, style):
                print("  ", pth)
            print(f"   hojas por animacion {time.time() - t1:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
