"""Sin destellos de la camara entre recursos (timeline.overlay_continuity, render y compose).

En el video real la cara NITIDA de la toma aparecia 2 cuadros entre dos fragmentos de apoyo (el plan dejaba
brollplan.GAP = 0.04 s entre ellos) y la toma ANTERIOR 1 cuadro antes de un cambio de toma (el fragmento
terminaba 0.067 s antes). Regla: en tiempo FINAL (cuadros), dos recursos separados por menos de
OVERLAY_GAP se pegan, y un borde a menos de eso de un cambio de toma se lleva a el; la grabacion de
pantalla no se mueve. Se mide en el job del render (las ventanas finales) y recorriendo los cuadros."""
import subprocess

import pytest

import compose
from _broll_media import make_film
from motion import render as R
from motion import timeline as T
from motion import video_io as V

FPS = 30


def _camera_runs(wins, bounds, n):
    """Tramos de cuadros SIN recurso encima (camara a la vista) entre recursos o junto a un cambio de toma."""
    cov = [False] * n
    for a, b in wins:
        for k in range(int(round(a * FPS)), min(n, int(round(b * FPS)))):
            cov[k] = True
    runs, k = [], 0
    while k < n:
        if cov[k]:
            k += 1
            continue
        j = k
        while j < n and not cov[j]:
            j += 1
        runs.append((k, j))
        k = j
    return runs


def test_pura_pega_y_lleva_al_cambio_de_toma():
    # A termina 0.04 s antes de B; C termina 2 cuadros antes de un cambio de toma (cuadro 1577); D empieza
    # 3 cuadros despues de otro (cuadro 300); E es una pantalla (no se mueve)
    wins = [[5.0, 7.1287, True, True], [7.1687, 9.0, True, True], [48.8, 52.5003, True, True],
            [10.1, 12.0, True, True], [20.0, 30.0, False, False]]
    bounds = [0, 300, 1577, 600, 900]
    out = T.overlay_continuity(wins, bounds, FPS)
    assert out[0][1] == pytest.approx(7.1687) and out[1][0] == pytest.approx(7.1687)
    assert out[2][1] == pytest.approx(1577 / FPS)
    assert out[3][0] == pytest.approx(10.0)
    assert out[4] == [20.0, 30.0]
    # lejos (0.4 s) no se toca; nunca se pasa encima del vecino
    far = T.overlay_continuity([[1.0, 2.0, True, True], [2.4, 3.0, True, True]], [0, 90], FPS)
    assert far == [[1.0, 2.0], [2.4, 3.0]]
    nb = T.overlay_continuity([[1.0, 2.9, True, True], [2.95, 4.0, False, False]], [0, 89, 120], FPS)
    assert nb[0][1] == pytest.approx(2.95)          # pegado al vecino fijo (no al cambio de toma de 2.967)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("cont")
    make_film(d / "apoyo01.mp4", [("screen", 3.0), ("character", 3.0), ("screen", 3.0), ("title", 3.0),
                                  ("screen", 3.0), ("character", 3.0)], fps=30, size=(320, 180))
    for name, col in (("cam1", "0x335577"), ("cam2", "0x775533")):
        subprocess.run([V.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={col}:s=108x192:r=30:d=12",
                        "-f", "lavfi", "-i", "sine=f=330:r=48000:d=12", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / f"{name}.mp4")], check=True)
    return d


def _reel(iid, a, b, pieces):
    return {"id": iid, "start": a, "end": b, "sid": "apoyo01", "kind": "video", "style": "reel", "src": "broll",
            "layout": "full", "w": 320, "h": 180, "media_dur": 18.0, "enabled": True, "locked": True,
            "pieces": pieces}


def _plan(media):
    items = [_reel("br01", 1.0, 3.96, [{"shot": 0, "in": 0.1, "out": 1.5, "max": 2.9, "cx": 0.5},
                                       {"shot": 1, "in": 3.1, "out": 4.5, "max": 5.9, "cx": 0.5}]),
             _reel("br02", 4.0, 6.0, [{"shot": 2, "in": 6.1, "out": 7.1, "max": 8.9, "cx": 0.5},
                                      {"shot": 4, "in": 12.1, "out": 13.1, "max": 14.9, "cx": 0.5}]),
             # termina 2 cuadros antes del cambio de toma de 10.0
             _reel("br03", 7.0, 9.933, [{"shot": 3, "in": 9.1, "out": 10.5, "max": 11.9, "cx": 0.5},
                                        {"shot": 5, "in": 15.1, "out": 16.6, "max": 17.9, "cx": 0.5}])]
    return {"video": str(media / "cam1.mp4"), "duration": 12.0, "version": 2, "cuts": [],
            "shots": [{"start": 0.0, "end": 10.0, "src": "cam1.mp4"}, {"start": 10.0, "end": 12.0, "src": "cam2.mp4"}],
            "director": {"style": "minimal"}, "phrases": [],
            "fx": {"captions": {"enabled": False}, "grade": {"enabled": False},
                   "music": {"enabled": False, "source": "none"}, "media": items}}


def test_render_sin_camara_entre_fragmentos_ni_antes_del_cambio_de_toma(media, tmp_path):
    p = _plan(media)
    o = {"W": 108, "H": 192, "dither": 0.0, "cache_dir": str(tmp_path / "c"), "media_root": str(media),
         "enhance_audio": False}
    job = R.prepare(p, o)["job"]
    wins = sorted((it["_fs"], it["_fe"]) for it in job["media"])
    assert wins[0][1] == pytest.approx(wins[1][0])                    # pegados (antes: 0.04 s de camara)
    assert wins[2][1] == pytest.approx(10.0)                          # hasta el cambio de toma
    n = job["n_frames"]
    shots = [0, 300, n]
    for k0, k1 in _camera_runs(wins, shots, n):
        if k0 > 0 and k1 < n:
            assert k1 - k0 >= 8, (k0, k1)                             # nada de 1-7 cuadros de camara


def test_compose_guarda_la_continuidad_en_el_plan(media):
    """compose (finish_plan -> _broll_continuity) deja los fragmentos pegados en el PLAN (tiempo original),
    asi el Studio los ve igual que el export."""
    p = _plan(media)
    n = compose._broll_continuity(p)
    by = {it["id"]: it for it in p["fx"]["media"]}
    assert n >= 2
    assert by["br01"]["end"] == pytest.approx(4.0)
    assert by["br03"]["end"] == pytest.approx(10.0)
    # con un silencio recortado entre dos fragmentos (en tiempo final quedan a 0.03 s): tambien se pegan
    p2 = _plan(media)
    p2["fx"]["media"][0]["end"] = 3.5
    p2["cuts"] = [{"start": 3.52, "end": 3.98, "enabled": True}]
    compose._broll_continuity(p2)
    tl = T.Timeline.from_plan(p2, fps=FPS)
    a = tl.map_item(p2["fx"]["media"][0])
    b = tl.map_item(p2["fx"]["media"][1])
    assert a[1] == pytest.approx(b[0], abs=1e-6)
