"""Demo visual/auditiva del catalogo de SFX sintetizado (motion/sfx.py).

Escribe en spike/work/demos/sfx/:
  wave/<archivo>.png      tarjeta por SFX y variante (onda estereo, espectrograma, hit, medidas,
                          perdida en altavoz de telefono, tonica y zona sostenida)
  sheet_sfx_<k>.png       hojas de contacto (primera variante de cada SFX)
  sheet_variants.png      todas las variantes de los SFX con variantes
  sfx_audition.mp4        todos los SFX en fila, con su tarjeta en pantalla (para escuchar)
  report.json             tiempos de sintesis y medidas

Uso: cd spike/scripts && ../.venv/bin/python ../tests/demos/demo_sfx.py [--force]
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402
from scipy import signal  # noqa: E402

import paths  # noqa: E402
from motion import assets, sfx  # noqa: E402
from motion.audio import load_audio, loudness, peak_window_lufs, write_wav  # noqa: E402
import _audioviz as V  # noqa: E402

OUT = paths.SPIKE / "work" / "demos" / "sfx"
_PHONE = signal.butter(4, 300, "highpass", fs=48000, output="sos")


def phone_db(x: np.ndarray) -> float:
    """Perdida de L100 en un altavoz de telefono (HPF 300 Hz de orden 4)."""
    return peak_window_lufs(signal.sosfilt(_PHONE, x, axis=0), 0.1) - peak_window_lufs(x, 0.1)


def main(force: bool = False) -> dict:
    (OUT / "wave").mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    cat = sfx.generate_all(force=force, verbose=True)
    t_gen = time.perf_counter() - t0
    cards_first, cards_var, audition, frames = [], [], [], []
    for name, e in cat.items():
        for i, v in enumerate(e["variants"]):
            x = load_audio(assets.SFX_DIR / v["file"])
            meta = {"desc": e["desc"], "lufs_100ms": round(peak_window_lufs(x, 0.1), 2), "peak_db": e["peak_db"],
                    "phone_db": phone_db(x), "root": v.get("root"), "loop": v.get("loop")}
            label = name if i == 0 else f"{name}  (variante {i + 1})"
            card = V.sfx_card(label, x, v["hit"], meta)
            png = OUT / "wave" / (Path(v["file"]).stem + ".png")
            V.save_png(card, png)
            if i == 0:
                cards_first.append(V.sfx_card(name, x, v["hit"], meta, w=800, h=360))
            if len(e["variants"]) > 1:
                cards_var.append(V.sfx_card(label, x, v["hit"], meta, w=800, h=360))
            audition.append(x)
            frames.append((png, x.shape[0] / 48000 + 0.45))
    per = 12
    sheets = []
    for k in range(0, len(cards_first), per):
        p = OUT / f"sheet_sfx_{k // per + 1}.png"
        V.save_png(V.grid(cards_first[k:k + per], 3), p)
        sheets.append(str(p))
    pv = OUT / "sheet_variants.png"
    V.save_png(V.grid(cards_var, 4), pv)
    # audicion: todo en fila con 0.45 s de silencio
    gap = np.zeros((int(0.45 * 48000), 2))
    seq = np.concatenate([np.concatenate([x, gap]) for x in audition])
    wav = OUT / "sfx_audition.wav"
    write_wav(wav, seq)
    lst = OUT / "audition_frames.txt"
    with open(lst, "w") as f:
        for png, d in frames:
            f.write(f"file '{png}'\nduration {d:.4f}\n")
        f.write(f"file '{frames[-1][0]}'\n")
    mp4 = OUT / "sfx_audition.mp4"
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-i", str(wav), "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-crf", "24",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(mp4)], check=True)
    wav.unlink(missing_ok=True)   # el MP4 ya lleva el audio (no dejar 25 MB de WAV)
    lst.unlink(missing_ok=True)
    # medidas de coherencia
    levels = {}
    for name, e in cat.items():
        x = load_audio(assets.SFX_DIR / e["file"])
        m = loudness(x)
        levels[name] = {"lufs_100ms": e["lufs_100ms"], "peak_db": e["peak_db"], "target": sfx.SPECS[name][3],
                        "phone_db": round(phone_db(x), 1), "momentary_max": round(m["momentary_max"], 2),
                        "dur": e["dur"], "hit": e["hit"], "root": e.get("root"), "loop": e.get("loop")}
    size = sum(p.stat().st_size for p in assets.SFX_DIR.glob("*.flac"))
    rep = {"generate_s": round(t_gen, 2), "files": sum(len(e["variants"]) for e in cat.values()),
           "sounds": len(cat), "flac_mb": round(size / 1e6, 2), "sheets": sheets,
           "variants_sheet": str(pv), "audition": str(mp4), "levels": levels}
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in rep.items() if k != "levels"}, indent=1))
    return rep


if __name__ == "__main__":
    main(force="--force" in sys.argv)
