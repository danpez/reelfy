"""Pruebas de scripts/compose.py con los MOV reales de iPhone (HEVC HLG, rotation -90):
lienzo vertical con dims de display, sin recorte de cabeza/pies y con tonemap HDR -> SDR.

Deja una hoja de contacto en spike/work/tests_compose/compose_contact.jpg para revisarla.
"""
import subprocess

import numpy as np
import pytest

import compose
import paths
from motion import video_io as V
from conftest import TEST_MEDIA

MOVS = sorted(TEST_MEDIA.glob("IMG_*.MOV")) if TEST_MEDIA.exists() else []
OUT = paths.WORK / "tests_compose"
pytestmark = pytest.mark.skipif(len(MOVS) < 6, reason="sin los 6 MOV de prueba")
SEG = 1.5


def _frame(path, t, w, h, vf_extra=""):
    vf = (vf_extra + "," if vf_extra else "") + (
        f"scale={w}:{h}:flags=lanczos,scale=in_color_matrix=bt709:in_range=tv,format=rgb24")
    cmd = [V.FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
           "-vf", vf, "-f", "rawvideo", "pipe:1"]
    b = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(b, np.uint8).reshape(h, w, 3).astype(int)


def test_probe_compose_dims_de_display():
    p = compose.probe(MOVS[0])
    assert (p["w"], p["h"]) == (2160, 3840) and p["rotation"] in (90, 270)
    assert p["hdr"] == "hlg" and p["audio"] and p["type"] == "video"
    assert set(p) >= {"w", "h", "dur", "audio", "type"}


@pytest.fixture(scope="module")
def composed():
    OUT.mkdir(parents=True, exist_ok=True)
    spec = {"fps": 30, "main": []}
    t = 0.0
    starts = []
    for m in MOVS:
        d = V.probe(m)["duration"]
        tin = round(max(0.0, d / 2 - SEG / 2), 3)
        spec["main"].append({"path": str(m), "type": "video", "in": tin, "out": tin + SEG,
                             "start": t})
        starts.append((m, tin, t))
        t += SEG
    out = OUT / "compose_6mov.mp4"
    compose.compose(spec, str(out), OUT / "work")
    import shutil
    shutil.rmtree(OUT / "work", ignore_errors=True)       # segmentos 4K intermedios
    return out, starts


def test_compose_vertical_sdr_y_duracion(composed):
    out, starts = composed
    si = V.stream_info(out)
    v, a = si["video"], si["audio"]
    assert (v["width"], v["height"]) == (2160, 3840)             # lienzo = display vertical
    assert v["color_transfer"] == "bt709" and v["color_space"] == "bt709"
    assert v["color_primaries"] == "bt709" and v["color_range"] == "tv"
    assert float(v["duration"]) == pytest.approx(SEG * len(MOVS), abs=0.15)
    assert float(a["duration"]) == pytest.approx(SEG * len(MOVS), abs=0.15)
    assert V.probe(out)["rotation"] == 0


def test_compose_sin_recorte_y_con_tonemap(composed):
    """Cada clip compuesto coincide con su fuente autorrotada+tonemapeada (mismo encuadre)."""
    out, starts = composed
    w, h = 270, 480
    tiles = []
    for m, tin, t in starts:
        got = _frame(out, t + SEG / 2, w, h)
        # 60 -> 30 fps: el cuadro compuesto puede ser el vecino a 1/60 s de la fuente
        refs = [_frame(m, tin + SEG / 2 + dt, w, h, V.TONEMAP) for dt in (-1 / 60, 0.0, 1 / 60)]
        ds = [np.abs(got - r).mean() for r in refs]
        ref = refs[int(np.argmin(ds))]
        raw = _frame(m, tin + SEG / 2, w, h)                    # sin tonemap (lavado)
        d = min(ds)
        assert d < 6.0, (m.name, d)
        # el perfil vertical (cabeza arriba, pies abajo) coincide: sin recorte ni rotacion
        assert np.corrcoef(got.mean(axis=(1, 2)), ref.mean(axis=(1, 2)))[0, 1] > 0.97
        assert np.corrcoef(got.mean(axis=(0, 2)), ref.mean(axis=(0, 2)))[0, 1] > 0.97
        # el tonemap satura mas que la senal HLG vista como SDR
        sat = lambda x: (x.max(-1) - x.min(-1)).mean()          # noqa: E731
        assert sat(got) > sat(raw) * 1.05, m.name
        tiles.append((got, raw, m.name))
    _contact(tiles)


def _contact(tiles):
    from PIL import Image, ImageDraw
    w, h = 270, 480
    sheet = Image.new("RGB", (len(tiles) * (w + 10), 2 * (h + 26)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (got, raw, name) in enumerate(tiles):
        x = i * (w + 10)
        sheet.paste(Image.fromarray(got.astype(np.uint8)), (x, 22))
        sheet.paste(Image.fromarray(raw.astype(np.uint8)), (x, h + 48))
        d.text((x + 4, 4), f"{name} compuesto (SDR)", fill="black")
        d.text((x + 4, h + 30), "fuente sin tonemap", fill="black")
    sheet.save(OUT / "compose_contact.jpg", quality=88)
