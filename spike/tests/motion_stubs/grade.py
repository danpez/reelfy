"""Stub de color: tinte calido leve (Grader) y blanco y negro."""
import skia


def _cf(img, cf):
    s = skia.Surface.MakeRaster(skia.ImageInfo.Make(img.width(), img.height(),
                                                    skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType))
    s.getCanvas().drawImage(img, 0, 0, skia.SamplingOptions(), skia.Paint(ColorFilter=cf))
    return s.makeImageSnapshot()


class Grader:
    def __init__(self, base_video, shots_final, W, H, fps):
        self.base, self.shots, self.prepared = base_video, shots_final, False

    def prepare(self, on_pct=None):
        self.prepared = True
        return []

    def apply(self, img, cfg, shot_idx, ctx):
        m = [1.04, 0, 0, 0, 0.01,  0, 1.0, 0, 0, 0,  0, 0, 0.95, 0, 0,  0, 0, 0, 1, 0]
        return _cf(img, skia.ColorFilters.Matrix(m))


def bw(img, item, lt, dur, ctx):
    l = [0.30, 0.59, 0.11]
    m = l + [0, 0] + l + [0, 0] + l + [0, 0] + [0, 0, 0, 1, 0]
    return _cf(img, skia.ColorFilters.Matrix(m))


RENDERERS = {"bw": bw}
