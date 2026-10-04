"""Clips de cuadros VARIABLES (VFR) y tope de lectura de las tomas de apoyo (render.py).

ReplayKit (grabacion de pantalla del iPhone) NO escribe cuadros mientras la pantalla esta quieta: una
grabacion real tiene huecos de 0.5-1.3 s. Con la busqueda exacta normal (-ss antes de -i)
ffmpeg tiraba los cuadros anteriores a t0 y, si t0 caia en un hueco, el clip normalizado arrancaba en el
cuadro SIGUIENTE: la ventana sincronizada (leida por indice, sync_frame) quedaba corrida lo que durara el
hueco durante TODA la toma (1.3 s en la revision). Aqui la fuente lleva su numero de cuadro en franjas y
un hueco hecho con select (como ReplayKit); el cuadro j del clip debe ser el ultimo de la fuente en o
antes de t0 + j/fps (lo que se veia en pantalla), en el clip, en el cuadro suelto y en las tomas de un
fragmento de apoyo (_reel_cmd), que ademas nunca leen en o despues del fin de su toma (src_max)."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from motion import reel as RL
from motion import render as R
from motion import video_io as V

W, H, FPS, DUR, BITS = 64, 128, 30, 16.0, 10
GAP = (10.0, 11.49)            # sin cuadros (pantalla quieta)


def _bits_frames(n):
    fr = np.full((n, H, W, 3), 128, np.uint8)
    for k in range(n):
        for i in range(BITS):
            fr[k, 4 + i * 12:4 + i * 12 + 10, 8:W - 8] = 235 if (k >> i) & 1 else 20
    return fr


def _decode(a):
    v = 0
    for i in range(BITS):
        if a[4 + i * 12 + 5, W // 2, 0] >= 170:
            v |= 1 << i
    return v


@pytest.fixture(scope="module")
def vfr(tmp_path_factory):
    d = tmp_path_factory.mktemp("vfr")
    n = int(DUR * FPS)
    cfr = d / "cfr.mp4"
    p = subprocess.Popen([V.FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast",
                          "-pix_fmt", "yuv444p", str(cfr)], stdin=subprocess.PIPE)
    p.stdin.write(_bits_frames(n).tobytes())
    p.stdin.close()
    assert p.wait() == 0
    out = d / "vfr.mp4"
    subprocess.run([V.FFMPEG, "-y", "-v", "error", "-i", str(cfr), "-vf",
                    f"select='not(between(t\\,{GAP[0]}\\,{GAP[1]}))'", "-fps_mode", "vfr", "-c:v", "libx264",
                    "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv444p", str(out)], check=True)
    pts = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "frame=pts_time",
                          "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True).stdout.split()
    times = np.array([float(x.strip(",")) for x in pts if x.strip(",")])
    assert not ((times > GAP[0] + 0.01) & (times < GAP[1] - 0.01)).any()      # de verdad hay un hueco
    return out, times


def _expect(times, t):
    """Numero del cuadro de la fuente que se ve en t (el ultimo en o antes de t, con medio cuadro de
    redondeo de la reticula de salida)."""
    k = int(np.searchsorted(times, t + 0.5 / FPS - 1e-6, side="right")) - 1
    return int(round(times[max(0, k)] * FPS))


def _read_all(path, n=None):
    rd = V.FrameReader(str(path), W, H, 0, None, FPS)
    got = []
    while n is None or len(got) < n:
        a = rd.read()
        if a is None:
            break
        got.append(_decode(a))
    rd.close()
    return got


@pytest.mark.parametrize("t0", [10.2, 10.0 + 1 / 60, 5.0, 5.01, 11.2])
def test_clip_desde_un_hueco_muestra_lo_que_se_veia(vfr, tmp_path, t0):
    src, times = vfr
    info = V.probe(src)
    m = {"src": str(src), "kind": "screen", "type": "video"}
    out = tmp_path / "c.mp4"
    subprocess.run(R._media_video_cmd(m, info, W, H, FPS, 90, t0, out), check=True)
    got = _read_all(out)
    assert len(got) == 90
    want = [_expect(times, t0 + j / FPS) for j in range(90)]
    assert got == want, (t0, got[:5], want[:5])
    # el cuadro suelto (/frame sin clip en cache) es el mismo
    r = subprocess.run(R._media_video_cmd(m, info, W, H, FPS, 1, t0, None, single=True), capture_output=True,
                       check=True)
    one = np.frombuffer(r.stdout[:W * H * 4], np.uint8).reshape(H, W, 4)
    assert _decode(one) == want[0]


def test_antes_se_corria_todo_el_hueco(vfr, tmp_path):
    """Lo que hacia la busqueda exacta (el bug): el cuadro 0 era el primero DESPUES del hueco."""
    src, times = vfr
    info = V.probe(src)
    m = {"src": str(src), "kind": "screen", "type": "video"}
    cmd = R._media_video_cmd(m, info, W, H, FPS, 30, 10.2, tmp_path / "x.mp4")
    assert "-noaccurate_seek" in cmd and any("start_time=0" in c for c in cmd)
    old = [c for c in cmd if c != "-noaccurate_seek"]
    old = [c.replace(":start_time=0", "") if c.startswith("fps=") or ",fps=" in c else c for c in old]
    subprocess.run(old, check=True)
    assert _read_all(tmp_path / "x.mp4", 1)[0] == _expect(times, GAP[1] + 0.02)     # 345, no 299


def test_tomas_de_apoyo_desde_un_hueco_y_sin_pasar_de_su_toma(vfr, tmp_path):
    src, times = vfr
    info = V.probe(src)
    # toma A desde el hueco (t 10.2, su toma acaba en 12.0); toma B 3.0 s pedidos pero su toma acaba en 4.0
    # (src_max): de 4.0 en adelante se queda el ultimo cuadro de SU toma (nunca la toma siguiente)
    rows = [[0.0, 1.0, 10.2, None, 0, 12.0], [1.0, 4.0, 3.0, None, 1, 4.0]]
    m = {"src": str(src), "kind": "reel", "type": "video", "reel": rows}
    out = tmp_path / "r.mp4"
    subprocess.run(R._reel_cmd(m, info, W, H, FPS, 120, out), check=True)
    got = _read_all(out)
    assert len(got) == 120
    assert got[:30] == [_expect(times, 10.2 + j / FPS) for j in range(30)]
    last_ok = _expect(times, 4.0 - 1 / FPS)
    assert got[30:60] == [_expect(times, 3.0 + j / FPS) for j in range(30)]
    assert all(v == last_ok for v in got[60:]), got[55:70]
    assert all(v / FPS < 4.0 for v in got[30:])
    # el cuadro suelto de la toma B pasado su tope tambien congela (mismo cuadro que el export)
    lt = 3.5
    assert RL.piece_at(rows, lt) == 1
    t_src = RL.src_cap([1.0, 4.0, 3.0, 1, 4.0], 3.0 + lt - 1.0, FPS)
    assert t_src == pytest.approx(4.0 - 1 / FPS)


# ================================================================ reel.fit: tope de lectura (src_max)
def _fit_rows(ps, L):
    rows = RL.fit(ps, L, 30)
    return rows, [RL.src_time(rows, k / 30) for k in range(int(L * 30))]


def test_quitar_una_toma_no_pasa_a_la_toma_siguiente_del_video():
    """Studio 'Quitar' sobre la tercera pieza: el item conserva su largo (4.87 s) y fit() alarga la 9 mas
    alla de su max (41.42): antes se leia 1.49 s de la toma siguiente (gente)."""
    ps = [{"shot": 4, "in": 12.08, "out": 13.6, "max": 13.92}, {"shot": 9, "in": 40.08, "out": 41.4, "max": 41.42}]
    rows, ts = _fit_rows(ps, 4.87)
    assert len(rows[0]) == 5 and rows[-1][4] == pytest.approx(41.42)
    for r in rows:
        last = r[2] + (RL.hold_frames(r, 30) - 1) / 30
        assert last < r[4] - 1e-6
    assert max(ts) < 41.42 - 1e-6 and ts[-1] == pytest.approx(max(t for t in ts if t > 30))
    # al final se queda el ultimo cuadro de la toma 9
    assert ts[-1] == ts[-10]


def test_cambiar_por_una_toma_mas_corta_y_alargar_el_fin():
    ps = [{"shot": 1, "in": 2.08, "out": 3.5, "max": 3.92}, {"shot": 20, "in": 60.08, "out": 60.9, "max": 61.0}]
    for L in (3.0, 5.5, 9.0):           # el Fin del item alargado
        rows, ts = _fit_rows(ps, L)
        for k, t in enumerate(ts):
            r = rows[RL.piece_at(rows, k / 30)]
            assert t < r[4] - 1e-6, (L, k, t, r)


def test_filas_viejas_sin_tope_siguen_funcionando():
    assert RL.src_time([[0.0, 2.0, 1.0, 0]], 1.0) == pytest.approx(2.0)
    assert RL.hold_frames([0.0, 2.0, 1.0, 0]) > 10 ** 6


def test_spec_del_render_lleva_el_tope():
    src = Path(R.__file__).read_text()
    assert "out.append([t0, t1, src_in, crop, int(j), src_max])" in src
    assert 'f"trim=end_frame={kk}"' in src and "RL.hold_frames(_fit_row(row), fps)" in src
    assert R._fit_row([0.0, 1.0, 2.0, None, 3, 4.5]) == [0.0, 1.0, 2.0, 3, 4.5]
    assert json.dumps(RL.fit([{"in": 0, "out": 1, "max": 1.5}], 1.0, 30)) == "[[0.0, 1.0, 0.0, 0, 1.5]]"
