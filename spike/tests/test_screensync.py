"""Sincronia por audio entre una toma de camara y una grabacion de pantalla (scripts/screensync.py).

Senales SINTETICAS con forma de voz (silabas de ruido filtrado con su envolvente y pausas al azar):
nada de material de clientes. Cubre: coincidencia con desfase positivo y negativo, traslape parcial
(la pantalla empieza antes o despues, termina antes), frecuencias de muestreo distintas (16, 22.05,
44.1 y 48 kHz), niveles y ruido distintos por microfono, precision fina (~1 ms), deriva de reloj, y
los negativos: otra voz, una RETOMA de la misma frase con otro ritmo, traslape demasiado corto y
silencio. Mas el mapa del video compuesto (pieces/runs/composed_to_screen)."""
import math
import wave

import numpy as np
import pytest

import screensync as S


def voice(dur, sr, seed, syl=(0.07, 0.28), gap=(0.03, 0.45), times=None):
    """Senal con forma de voz: silabas (ruido filtrado 200-3000 Hz con envolvente seno) y pausas.
    `times`: lista de (inicio, largo) de silabas ya decidida (para retomas con otro ritmo)."""
    rng = np.random.default_rng(seed)
    n = int(round(dur * sr))
    y = np.zeros(n, np.float64)
    if times is None:
        times, t = [], 0.2
        while t < dur - 0.3:
            L = rng.uniform(*syl)
            times.append((t, L))
            t += L + (rng.uniform(*gap) if rng.random() < 0.35 else rng.uniform(0.0, 0.06))
    for t, L in times:
        a, b = int(t * sr), min(n, int((t + L) * sr))
        if b - a < 8:
            continue
        seg = rng.standard_normal(b - a)
        # filtro pasa-banda barato: diferencia de dos promedios moviles
        k1, k2 = max(1, sr // 3000), max(2, sr // 200)
        lp = np.convolve(seg, np.ones(k1) / k1, "same")
        seg = lp - np.convolve(lp, np.ones(k2) / k2, "same")
        env = np.sin(np.linspace(0, math.pi, b - a)) ** 0.7 * rng.uniform(0.4, 1.0)
        y[a:b] += seg * env
    y /= max(1e-9, np.abs(y).max())
    return y.astype(np.float32), times


def resample(y, sr_in, sr_out, rate=1.0):
    """Reloj de otro aparato: remuestreo lineal (rate != 1: deriva)."""
    n = int(len(y) * sr_out / sr_in / rate)
    t = np.arange(n) * rate * sr_in / sr_out
    return np.interp(t, np.arange(len(y)), y).astype(np.float32)


def mic(y, sr, gain, noise_db, seed):
    rng = np.random.default_rng(seed)
    return (y * gain + rng.standard_normal(len(y)) * 10 ** (noise_db / 20)).astype(np.float32)


def scene(base_sr=16000, cam_sr=16000, scr_sr=48000, cam=(10.0, 40.0), scr_pre=6.0, scr=(4.0, 55.0),
          seed=1, rate=1.0):
    """Voz comun V (60 s). Camara = V[cam] ; pantalla = charla propia (scr_pre s) + V[scr].
    -> (cam_y, cam_sr, scr_y, scr_sr, offset esperado = t_pantalla - t_camara)."""
    V, _ = voice(62.0, base_sr, seed)
    other, _ = voice(scr_pre + 0.5, base_sr, seed + 100)
    cy = V[int(cam[0] * base_sr):int(cam[1] * base_sr)]
    sy = np.concatenate([other[:int(scr_pre * base_sr)], V[int(scr[0] * base_sr):int(scr[1] * base_sr)] * 0.35])
    cy = mic(resample(cy, base_sr, cam_sr), cam_sr, 0.9, -48, seed + 1)
    sy = mic(resample(sy, base_sr, scr_sr, rate), scr_sr, 1.0, -42, seed + 2)
    # V(cam[0] + tc) = pantalla(scr_pre + cam[0] + tc - scr[0])
    return cy, cam_sr, sy, scr_sr, scr_pre + cam[0] - scr[0]


def test_coincide_desfase_positivo_y_frecuencias_distintas():
    cy, csr, sy, ssr, off = scene()
    r = S.match(cy, csr, sy, ssr)
    assert r and r["ok"], r
    assert abs(r["offset"] - off) < 0.002, (r["offset"], off)
    assert r["corr"] >= 0.8 and r["margin"] >= S.MARGIN_MIN
    assert r["rate"] == 1.0
    # toda la toma tiene pantalla: traslape = la camara completa
    assert r["overlap"][0] == pytest.approx(0.0, abs=0.02) and r["overlap"][1] == pytest.approx(30.0, abs=0.05)
    assert r["other"][0] == pytest.approx(off, abs=0.01)
    # inicio, medio y final verificados por separado
    assert len(r["checks"]) == 3 and all(abs(c["offset"] - off) < 0.003 for c in r["checks"])


@pytest.mark.parametrize("cam_sr,scr_sr", [(16000, 16000), (44100, 22050), (48000, 44100), (22050, 48000)])
def test_frecuencias_de_muestreo(cam_sr, scr_sr):
    cy, csr, sy, ssr, off = scene(cam_sr=cam_sr, scr_sr=scr_sr, seed=3)
    r = S.match(cy, csr, sy, ssr)
    assert r and r["ok"] and abs(r["offset"] - off) < 0.003, (r, off)


def test_desfase_negativo_pantalla_empieza_despues():
    # la pantalla empieza 8 s DESPUES que la camara (sin charla antes): offset negativo
    cy, csr, sy, ssr, off = scene(cam=(5.0, 45.0), scr_pre=0.0, scr=(13.0, 50.0), seed=5)
    assert off < 0
    r = S.match(cy, csr, sy, ssr)
    assert r and r["ok"] and abs(r["offset"] - off) < 0.002, (r, off)
    # traslape parcial: la camara solo tiene pantalla desde el s 8
    assert r["overlap"][0] == pytest.approx(8.0, abs=0.05)
    assert r["other"][0] == pytest.approx(0.0, abs=0.05)


def test_traslape_parcial_pantalla_termina_antes():
    cy, csr, sy, ssr, off = scene(cam=(10.0, 50.0), scr_pre=3.0, scr=(2.0, 30.0), seed=7)
    r = S.match(cy, csr, sy, ssr)
    assert r and r["ok"] and abs(r["offset"] - off) < 0.002, (r, off)
    # la pantalla se acaba a los 3 + 28 = 31 s de su reloj -> camara hasta 31 - off
    assert r["overlap"][1] == pytest.approx(len(sy) / ssr - off, abs=0.05)


def test_precision_fina_submuestra():
    base = 16000
    V, _ = voice(40.0, base, 11)
    cy = V[int(4 * base):int(34 * base)]
    # la pantalla va 12.3456 s adelantada: desfase que no cae en la reticula de 10 ms
    pad, _ = voice(12.3456 + 4.0, base, 12)
    lead = pad[:int(round((12.3456) * base))]
    sy = np.concatenate([lead, V[int(4 * base):]])
    r = S.match(mic(cy, base, 1.0, -50, 1), base, mic(sy, base, 0.4, -45, 2), base)
    assert r and r["ok"]
    assert abs(r["offset"] - 12.3456) < 0.0015, r["offset"]


def test_deriva_de_reloj():
    # la pantalla corre 150 ppm lento: rate ~ 1.00015
    cy, csr, sy, ssr, off = scene(cam=(5.0, 60.0), scr_pre=2.0, scr=(1.0, 61.0), seed=13, rate=1 / 1.00015)
    r = S.match(cy, csr, sy, ssr)
    assert r and r["ok"], r
    assert r["rate"] == pytest.approx(1.00015, abs=4e-5), r
    for c in r["checks"]:
        pred = r["offset"] + r["rate"] * c["t"]
        assert abs((pred - c["t"]) - c["offset"]) < 0.002


def test_negativo_otra_voz():
    a, _ = voice(30.0, 16000, 21)
    b, _ = voice(80.0, 16000, 22)
    r = S.match(a, 16000, b, 16000)
    assert r is None or not r["ok"], r


def test_negativo_retoma_misma_frase_otro_ritmo():
    # la MISMA frase grabada otra vez (mismas silabas, otro ritmo): dos tomas, no una pantalla
    rng = np.random.default_rng(31)
    _, times = voice(20.0, 16000, 30)
    times2, t = [], 0.4
    for _t0, L in times:
        L2 = L * rng.uniform(0.85, 1.2)
        times2.append((t, L2))
        t += L2 + rng.uniform(0.0, 0.25)
    a, _ = voice(20.0, 16000, 30, times=times)
    b, _ = voice(t + 1.0, 16000, 30, times=times2)
    r = S.match(a, 16000, np.concatenate([np.zeros(16000 * 5, np.float32), b]), 16000)
    assert r is None or not r["ok"], r


def test_negativo_traslape_corto():
    V, _ = voice(30.0, 16000, 41)
    cam = V[: 16000 * 20]
    scr = np.concatenate([voice(40.0, 16000, 42)[0], V[16000 * 17: 16000 * 20]])    # 3 s en comun
    r = S.match(cam, 16000, scr, 16000)
    assert r is None or not r["ok"], r


def test_negativo_silencio():
    rng = np.random.default_rng(5)
    a = (rng.standard_normal(16000 * 20) * 1e-3).astype(np.float32)
    b = (rng.standard_normal(16000 * 40) * 1e-3).astype(np.float32)
    r = S.match(a, 16000, b, 16000)
    assert r is None or not r["ok"]


def test_corr_needed_mas_exigente_en_traslapes_cortos():
    assert S.corr_needed(S.OVERLAP_MIN) == pytest.approx(S.CORR_MIN_SHORT)
    assert S.corr_needed(S.OVERLAP_FULL) == pytest.approx(S.CORR_MIN)
    assert S.corr_needed(100) == pytest.approx(S.CORR_MIN)
    assert S.CORR_MIN_SHORT > S.corr_needed(8.0) > S.CORR_MIN


def test_read_wav_pcm16_y_estereo(tmp_path):
    p = tmp_path / "a.wav"
    y = (np.sin(np.arange(4800) / 10.0) * 0.5 * 32767).astype(np.int16)
    st = np.stack([y, y], 1).reshape(-1)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(st.tobytes())
    x, sr = S.read_wav(p)
    assert sr == 48000 and len(x) == 4800 and abs(float(x.max()) - 0.5) < 0.01
    assert S.read_wav(tmp_path / "no.wav") == (None, None)


def test_envolvente_en_segundos_a_cualquier_frecuencia():
    for sr in (16000, 22050, 44100, 48000):
        y = np.zeros(sr * 3, np.float32)
        y[int(1.5 * sr):int(1.6 * sr)] = 0.5
        e = S.envelope(y, sr)
        k = int(np.argmax(e))
        assert abs(k * S.HOP - 1.5) <= 0.02, (sr, k)


# ----------------------------------------------------------------------------- video compuesto
SEGS = [  # toma A, luego la camara "cam" en 3 pedazos (el montaje corto 2 silencios), luego toma B
    {"start": 0.0, "end": 4.0, "kind": "clip", "sid": "A", "in": 1.0},
    {"start": 4.0, "end": 9.0, "kind": "clip", "sid": "cam", "in": 1.5},
    {"start": 9.0, "end": 12.0, "kind": "clip", "sid": "cam", "in": 8.0},     # salto de 1.5 s en la toma
    {"start": 12.0, "end": 20.0, "kind": "clip", "sid": "cam", "in": 13.0},
    {"start": 20.0, "end": 24.0, "kind": "clip", "sid": "B", "in": 0.0},
]


def test_pieces_y_mapa_del_video_compuesto():
    sync = {"offset": 41.27, "rate": 1.0}
    ps = S.pieces(SEGS, "cam", sync, scr_dur=167.0)
    assert ps == [[4.0, 9.0, 1.5], [9.0, 12.0, 8.0], [12.0, 20.0, 13.0]]
    assert len(S.runs(ps)) == 1                 # contiguos: un solo elemento
    # inicio, medio (justo despues de un corte del montaje) y final
    assert S.composed_to_screen(ps, 4.0, 41.27) == pytest.approx(42.77)
    assert S.composed_to_screen(ps, 9.0, 41.27) == pytest.approx(49.27)      # salto: 8.0 de camara
    assert S.composed_to_screen(ps, 8.999, 41.27) == pytest.approx(41.27 + 6.499)
    assert S.composed_to_screen(ps, 19.5, 41.27) == pytest.approx(41.27 + 20.5)
    # fuera de los pedazos: el borde mas cercano
    assert S.composed_to_screen(ps, 2.0, 41.27) == pytest.approx(42.77)
    assert S.composed_to_screen([], 2.0, 1.0) is None


def test_pieces_solo_donde_la_pantalla_tiene_cuadros():
    # la pantalla empezo 3 s despues que la camara (offset -3) y dura 10 s: camara [3, 13]
    ps = S.pieces(SEGS, "cam", {"offset": -3.0}, scr_dur=10.0)
    assert ps == [[5.5, 9.0, 3.0], [9.0, 12.0, 8.0]]           # el 3er pedazo (camara 13-21) no tiene pantalla


def test_runs_separa_tramos_lejanos():
    ps = [[0.0, 2.0, 0.0], [2.2, 3.0, 5.0], [10.0, 12.0, 20.0]]
    rs = S.runs(ps)
    assert [len(r) for r in rs] == [2, 1]


# ----------------------------------------------------------------------------- relojes de los ARCHIVOS
def test_file_offset_con_el_inicio_del_audio_de_cada_archivo():
    """Cada wav empieza en la 1a muestra de audio de su archivo: una grabacion de pantalla del iPhone
    trae su audio 0.128 s despues del video, asi que el cuadro de la pantalla es el del wav + 0.128."""
    assert S.file_offset(41.27, 0.0, 0.128) == pytest.approx(41.398)
    assert S.file_offset(41.27, 0.05, 0.128) == pytest.approx(41.348)
    assert S.file_offset(-3.0) == -3.0


# ----------------------------------------------------------------------------- marco de la grabacion
def _framed(n=16, W=140, H=290, box=(10, 14, 130, 268), color=(216, 220, 232), seed=3, dark_bars=None):
    """Cuadros sinteticos (n, H, W, 3): marco fijo de `color` alrededor de `box` (x0, y0, x1, y1) y
    contenido que cambia en cada cuadro (como una app que se desplaza)."""
    rng = np.random.default_rng(seed)
    f = np.empty((n, H, W, 3), np.uint8)
    f[:] = color
    x0, y0, x1, y1 = box
    for k in range(n):
        f[k, y0:y1, x0:x1] = rng.integers(0, 255, (y1 - y0, x1 - x0, 3), dtype=np.uint8)
    return f


def test_marco_en_los_cuatro_lados():
    assert S.frame_box(_framed()) == (10, 14, 130, 268)
    # con un degradado suave y ruido del codec (+-3) sigue siendo marco
    f = _framed().astype(np.int16)
    f += np.linspace(0, 12, f.shape[1])[None, :, None, None].astype(np.int16)
    f += np.random.default_rng(1).integers(-3, 4, f.shape, dtype=np.int16)
    assert S.frame_box(np.clip(f, 0, 255).astype(np.uint8)) == (10, 14, 130, 268)


def test_banda_suelta_o_margenes_blancos_no_son_marco():
    # encabezado fijo (solo arriba): no se recorta
    f = _framed(box=(0, 30, 140, 290), color=(30, 90, 200))
    assert S.frame_box(f) is None
    # margenes blancos fijos a los lados de una app: tampoco
    f = _framed(box=(12, 0, 128, 290), color=(253, 253, 253))
    assert S.frame_box(f) is None
    # bandas negras arriba y abajo (letterbox): si
    f = _framed(box=(0, 40, 140, 250), color=(0, 0, 0))
    assert S.frame_box(f) == (0, 40, 140, 250)
    # una pantalla fija con detalle: el marco parejo se recorta igual
    still = np.repeat(_framed(n=1), 8, axis=0)
    assert S.frame_box(still) == (10, 14, 130, 268)
    # todo de un color (nada que mostrar): nada se recorta (lo que quedaria es demasiado chico)
    assert S.frame_box(np.full((8, 290, 140, 3), 40, np.uint8)) is None
    assert S.frame_box(None) is None


def _app(n=16, W=264, H=558, frame=(12, 16, 12, 26), color=(216, 220, 232), header=(8, 121, 77), seed=4,
         strip=0):
    """Una app QUIETA (encabezado de color fijo, margenes blancos fijos) con contenido que se desplaza
    en medio (renglones de "texto" oscuro sobre blanco), dentro de un marco de `color` (o sin marco:
    frame None). `strip`: una barra oscura de esos px pegada a la orilla de abajo (la del escritorio)."""
    f = np.empty((n, H, W, 3), np.uint8)
    f[:] = color
    fl, ft, fr, fb = frame or (0, 0, 0, 0)
    x0, y0, x1, y1 = fl, ft, W - fr, H - fb
    if strip:
        f[:, H - strip:] = (40, 70, 95)
    app = f[:, y0:y1, x0:x1]
    app[:] = 248
    app[:, :40] = header                                        # encabezado fijo (sin texto)
    rng = np.random.default_rng(seed)
    words = rng.integers(0, 2, (400, 24)).astype(bool)          # renglones de "texto"
    for k in range(n):
        for row in range(60, app.shape[1] - 60, 14):
            line = words[(row // 14 + 3 * k) % len(words)]      # se desplaza 3 renglones por cuadro
            for j, on in enumerate(line):
                if on:
                    app[k, row:row + 8, 16 + j * 9:16 + j * 9 + 7] = 30
    return f


def test_el_contenido_quieto_de_la_app_no_es_marco():
    """El encabezado fijo de la app (otro color) y sus margenes blancos fijos NO se recortan: el marco es
    del color de su orilla y se detiene donde empieza la app."""
    assert S.frame_box(_app()) == (12, 16, 252, 532)
    # una barra oscura pegada a la orilla de abajo (el escritorio) y despues el marco: todo es marco
    assert S.frame_box(_app(strip=7)) == (12, 16, 252, 532)
    # sin marco: la app blanca con margenes y encabezado quietos se queda completa
    assert S.frame_box(_app(frame=None)) is None
    # marco blanco alrededor de una app blanca (nada que lo distinga): no se toca
    assert S.frame_box(_app(color=(248, 248, 248), header=(248, 248, 248))) is None


def test_content_rect_solo_en_el_tramo_que_se_usa(tmp_path):
    """La grabacion muestra otra cosa ANTES de la toma (preparar la app, sin marco) y el marco solo
    despues: content_rect mira el tramo que se usa; la grabacion completa no tiene un marco fijo."""
    import subprocess
    from motion import video_io as V
    n1, n2, W, H = 30, 30, 140, 290
    a = np.random.default_rng(5).integers(0, 255, (n1, H, W, 3), dtype=np.uint8)
    b = _framed(n=n2, W=W, H=H)
    out = tmp_path / "pantalla.mp4"
    p = subprocess.Popen([V.FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", "10", "-i", "-", "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast",
                          "-pix_fmt", "yuv444p", str(out)], stdin=subprocess.PIPE)
    p.stdin.write(np.concatenate([a, b]).tobytes())
    p.stdin.close()
    assert p.wait() == 0
    r = S.content_rect(out, 3.2, 5.9)
    assert r == {"x": 10 + S.FRAME_INSET, "y": 14 + S.FRAME_INSET, "w": 120 - 2 * S.FRAME_INSET,
                 "h": 254 - 2 * S.FRAME_INSET, "W": W, "H": H}
    assert S.content_rect(out) is None
    assert S.crop_frac(r) == [round(12 / 140, 5), round(16 / 290, 5), round(128 / 140, 5), round(266 / 290, 5)]
    assert S.content_rect(tmp_path / "no_existe.mp4") is None and S.crop_frac(None) is None
