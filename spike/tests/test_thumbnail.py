"""Pruebas de scripts/thumbnail.py: miniatura de los shorts con la fuente del motor (antes Arial
de macOS: en Windows y Linux la miniatura fallaba)."""
import subprocess

import numpy as np
import pytest

import thumbnail as TH
from motion import video_io as V


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    p = tmp_path_factory.mktemp("th") / "short.mp4"
    subprocess.run([V.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=270x480:r=30:d=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(p)], check=True)
    return p


def test_fuente_del_motor_y_no_de_macos():
    fp = TH._font_path()
    assert fp is not None and "/System/Library" not in fp
    assert str(TH.FONTS_DIR) in fp and fp.endswith(".ttf")
    assert not hasattr(TH, "FONT") or "/System/Library" not in str(getattr(TH, "FONT"))


def test_miniatura_con_titulo(clip, tmp_path):
    from PIL import Image
    out = tmp_path / "t.jpg"
    assert TH.make_thumbnail(clip, out, "Cómo elegir tu perfume ideal") == out
    im = np.asarray(Image.open(out).convert("RGB")).astype(int)
    assert im.shape == (480, 270, 3)
    # el titulo en blanco sobre el degradado oscuro de abajo
    low = im[int(480 * 0.7):]
    assert ((low > 235).all(axis=2)).sum() > 200


def test_miniatura_sin_assets_usa_la_fuente_de_pil(clip, tmp_path, monkeypatch):
    monkeypatch.setattr(TH, "FONTS_DIR", tmp_path / "no_existe")
    assert TH._font_path() is None
    out = tmp_path / "t2.jpg"
    assert TH.make_thumbnail(clip, out, "Titulo de prueba") == out and out.stat().st_size > 1000
