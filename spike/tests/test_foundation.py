import numpy as np
import skia

from motion import assets, ctx as C, ease


def test_icons_parse():
    for n in ["dental", "map-pin", "clock", "cash", "sparkles", "calendar-event", "user-plus"]:
        ps = assets.icon_paths(n)
        assert ps, n
        b = ps[0].computeTightBounds()
        assert 0 <= b.left() and b.right() <= 24.5


def test_fonts_and_accents():
    for name in ["Poppins-Black", "Caveat-Bold", "Montserrat-Black", "DMSerifDisplay", "Anton"]:
        f = assets.font(name, 80)
        assert f.measureText("ñáéíóú") > 0


def test_surface_numpy_roundtrip():
    arr, s = C.new_frame(64, 32)
    s.getCanvas().clear(C.color("#FF0000"))
    assert arr[0, 0].tolist() == [255, 0, 0, 255]


def test_easing_bounds():
    for name, fn in ease.EASINGS.items():
        assert abs(fn(0.0)) < 1e-6 and abs(fn(1.0) - 1.0) < 1e-6, name
    assert ease.spring(5.0) > 0.99


def test_ctx_rng_deterministic():
    c1, c2 = C.demo_ctx(seed=3), C.demo_ctx(seed=3)
    assert c1.rand("x") == c2.rand("x")
    assert np.allclose(c1.rng("a").random(3), c2.rng("a").random(3))
