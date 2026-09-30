"""compose.py: RAMPAS DE VELOCIDAD (solo video) en los cambios de camara del montaje.

"ramp_in"/"ramp_out" {"dur", "speed"} en un item: el VIDEO de ese lado de la union corre a `speed`
x durante `dur` s de salida usando cuadros de mas de su fuente; el AUDIO es exactamente [in, out]
a 1x (identico, muestra por muestra, al de sin rampa) y la duracion no cambia.

Material SINTETICO: cada cuadro lleva su NUMERO en franjas binarias (dos franjas de referencia
blanca/negra + 9 bits), asi se lee exactamente que cuadro de la fuente se ve en cada cuadro de
salida (tambien tras rotar, tonemap HLG o pasar de 60 fps VFR a 30). Audio: un chirrido (no
periodico) para que cualquier corrimiento se note. Sin datos de clientes.
"""
import math
import subprocess

import numpy as np
import pytest

import compose
import paths

FF = str(paths.FFMPEG)
FPS = 30
W, H = 176, 352                  # lienzo vertical (las franjas son horizontales)
NB, BITS = 11, 9
BH = H // NB


# ------------------------------------------------------------------ material
def _frame(k):
    f = np.full((H, W), 16, np.uint8)
    f[0:BH] = 235                                    # referencia blanca; la 2a franja es negra
    for b in range(BITS):
        if (k >> (BITS - 1 - b)) & 1:
            f[(2 + b) * BH:(3 + b) * BH] = 235
    return f


def _make(path, dur, fps=30, base=0, rotation=None, hdr=False, audio=True, drop=None):
    """Clip con el numero de cuadro (base + k) en franjas y un chirrido de audio. `rotation`: los
    cuadros se guardan girados con la rotacion de display que los endereza (como un iPhone);
    `hdr`: 10 bits etiquetado HLG; `drop`: n -> tira el cuadro n (VFR con los tiempos originales)."""
    n = int(round(dur * fps))
    frames = [_frame(base + k) for k in range(n)]
    if rotation:
        # rotacion de display 90 (antihoraria): lo codificado va girado 90 en sentido horario
        frames = [np.ascontiguousarray(np.rot90(f, k=-1)) for f in frames]
    fh, fw = frames[0].shape
    tmp = path.with_name(f"_raw_{path.name}")
    cmd = [FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{fw}x{fh}",
           "-r", str(fps), "-i", "-"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"aevalsrc=0.25*sin(2*PI*(220*t+140*t*t)):s=48000:d={n / fps}"]
    vf = []
    if drop:
        vf.append("select='" + drop + "'")
    if hdr:
        vf.append("format=yuv420p10le,setparams=color_trc=arib-std-b67:color_primaries=bt2020:"
                  "colorspace=bt2020nc")
        cmd += ["-c:v", "libx264", "-crf", "8", "-pix_fmt", "yuv420p10le"]
    else:
        cmd += ["-c:v", "libx264", "-crf", "8", "-pix_fmt", "yuv420p"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    if drop:
        cmd += ["-fps_mode", "passthrough"]
    cmd += ["-g", "12"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    out = tmp if rotation else path
    p = subprocess.run(cmd + [str(out)], input=b"".join(f.tobytes() for f in frames),
                       capture_output=True)
    assert p.returncode == 0, p.stderr.decode()[-800:]
    if rotation:
        subprocess.run([FF, "-v", "error", "-y", "-display_rotation", str(rotation), "-i", str(tmp),
                        "-c", "copy", str(path)], check=True)
        tmp.unlink()
    return str(path)


def _read(path):
    """Numero de cuadro de fuente que se ve en cada cuadro de `path` (lienzo W x H)."""
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, H, W)
    out = []
    for f in a:
        m = [float(f[i * BH + 5:(i + 1) * BH - 5, 10:-10].mean()) for i in range(NB)]
        assert m[0] - m[1] > 40, m                       # las referencias se distinguen
        thr = (m[0] + m[1]) / 2
        k = 0
        for b in range(BITS):
            k = k * 2 + (1 if m[2 + b] > thr else 0)
        out.append(k)
    return out


def _frame_times(path):
    """{numero de cuadro: s de la fuente} con los tiempos REALES (VFR, exactos: pts enteros por la
    base de tiempo del stream) y el numero pintado en cada cuadro."""
    from fractions import Fraction
    r = subprocess.run([str(paths.FFPROBE), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=time_base", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    tb = Fraction(r.stdout.strip().split(",")[0])
    r = subprocess.run([str(paths.FFPROBE), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "frame=pts", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    ts = [float(int(x.strip().rstrip(",")) * tb) for x in r.stdout.split() if x.strip()]
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    n = len(raw) // (W * H)
    assert n == len(ts)
    ks = _read_raw(raw, n)
    return dict(zip(ks, ts))


def _read_raw(raw, n):
    a = np.frombuffer(raw, np.uint8).reshape(n, H, W)
    out = []
    for f in a:
        m = [float(f[i * BH + 5:(i + 1) * BH - 5, 10:-10].mean()) for i in range(NB)]
        thr = (m[0] + m[1]) / 2
        k = 0
        for b in range(BITS):
            k = k * 2 + (1 if m[2 + b] > thr else 0)
        out.append(k)
    return out


def _remap(u, dur, rin=None, rout=None):
    """Tiempo de salida de un cuadro que esta `u` s despues del inicio de la LECTURA del video."""
    pre = rin["dur"] * (rin["speed"] - 1.0) if rin else 0.0
    if rin and u < rin["dur"] * rin["speed"]:
        return u / rin["speed"]
    if rout:
        t1 = dur - rout["dur"]
        if u >= pre + t1:
            return t1 + (u - pre - t1) / rout["speed"]
    return u - pre


def _expected(times, t_in, n_out, rin=None, rout=None, fps=FPS):
    """Cuadro de fuente que DEBE verse en cada cuadro de salida: el filtro fps toma el ultimo
    cuadro cuyo tiempo remapeado, redondeado al cuadro de salida (mitades hacia arriba), no pasa
    de el. `times`: {numero: s de la fuente}."""
    dur = n_out / fps
    v0 = t_in - (rin["dur"] * (rin["speed"] - 1.0) if rin else 0.0)
    ks = sorted((k for k, t in times.items() if t >= v0 - 1e-6), key=lambda k: times[k])
    slot = {k: math.floor(_remap(times[k] - v0, dur, rin, rout) * fps + 0.5 + 1e-6) for k in ks}
    out = []
    for n in range(n_out):
        ok = [k for k in ks if slot[k] <= n]
        out.append(ok[-1] if ok else None)
    return out


def _pcm(path):
    x = compose._pcm(path, ch=2)
    assert x is not None and len(x) > 0
    return x


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("ramps")
    return {
        "d": d,
        "a": _make(d / "a.mp4", 4.0),                       # camara A: cuadros 0..119
        "b": _make(d / "b.mp4", 4.0, base=256),             # camara B: cuadros 256..375
    }


def _cfr(n, fps=30, base=0):
    return {base + k: k / fps for k in range(n)}


def _norm(media, name, src, t_in, t_out, **kw):
    out = media["d"] / f"{name}.mp4"
    eff = compose._norm_video(src, t_in, t_out, out, W, H, FPS, exact=True, **kw)
    return out, eff


# ------------------------------------------------------------------ puro
def test_ramp_req_saneada_en_el_item_o_en_su_montage():
    assert compose._ramp_req({"ramp_in": {"dur": 0.3, "speed": 2.0}}, "in") == {"dur": 0.3, "speed": 2.0}
    assert compose._ramp_req({"montage": {"ramp_out": {"dur": 0.3}}}, "out") == {"dur": 0.3, "speed": 2.0}
    assert compose._ramp_req({"ramp_in": {"dur": 5.0, "speed": 9.0}}, "in") == \
        {"dur": compose.RAMP_MAX, "speed": compose.RAMP_SPEED_MAX}
    for bad in ({"dur": 0}, {"dur": -1}, {"dur": 0.3, "speed": 1.0}, {"dur": "x"}, {"dur": float("nan")},
                None, 0.3, True):
        assert compose._ramp_req({"ramp_in": bad}, "in") is None
    assert compose._ramp_req({"ramp_in": {"dur": 0.3}}, "out") is None


def test_ramp_fit_manija_tope_y_cuadros_enteros():
    r = {"dur": 0.3, "speed": 2.0}
    f = compose.ramp_fit(2.0, 1.0, 30, 10.0, r, r)
    assert f["in"]["dur"] == pytest.approx(0.3) and f["in"]["pre"] == pytest.approx(0.3)
    assert f["out"]["dur"] == pytest.approx(0.3) and f["out"]["post"] == pytest.approx(0.3)
    # sin manija suficiente: se acorta a lo que hay (misma velocidad), en cuadros de salida enteros
    f = compose.ramp_fit(0.2, 1.0, 30, 1.25, r, r)
    assert f["in"]["dur"] == pytest.approx(0.2) and f["in"]["speed"] == 2.0 and f["in"]["req"] == 0.3
    assert f["out"] is None                         # 1.25 - 1.2 = 0.05 s < RAMP_MIN
    f = compose.ramp_fit(0.17, 1.0, 30, None, r, r)
    assert f["in"]["dur"] == pytest.approx(5 / 30)  # 0.17 -> 5 cuadros
    assert f["out"] is None                         # fin del video desconocido: sin rampa de salida
    assert compose.ramp_fit(0.05, 1.0, 30, 10.0, r, None) == {"in": None, "out": None}
    # velocidad 3: la manija rinde la mitad (0.15 s = 4.5 cuadros -> 4)
    f = compose.ramp_fit(0.3, 2.0, 30, None, {"dur": 0.3, "speed": 3.0}, None)
    assert f["in"]["dur"] == pytest.approx(4 / 30) and f["in"]["pre"] == pytest.approx(8 / 30)
    # un segmento corto: cada rampa <= RAMP_SHARE del segmento (las dos nunca se enciman)
    f = compose.ramp_fit(5.0, 0.5, 30, 10.0, r, r)
    assert f["in"]["dur"] + f["out"]["dur"] <= 0.5 * 2 * compose.RAMP_SHARE + 1e-9
    assert f["in"]["dur"] == pytest.approx(math.floor(0.5 * compose.RAMP_SHARE * 30 + 1e-6) / 30)


def _seq(*items):
    return [(("black", it, None) if isinstance(it, float) else ("clip", it, it.get("trans")))
            for it in items]


def test_seq_ramps_solo_en_uniones_validas():
    r = {"dur": 0.3, "speed": 2.0}
    a = {"path": "a", "ramp_out": r, "ramp_in": r}
    b = {"path": "b", "ramp_in": r, "ramp_out": r}
    c = {"path": "c", "ramp_in": r}
    rq = compose.seq_ramps(_seq(a, b, c))
    # el primero no tiene union de entrada ni el ultimo de salida; en la union a->b llegan los dos
    # lados: gana la de salida (una rampa por union)
    assert rq[0] == {"in": None, "out": r}
    assert rq[1] == {"in": None, "out": r}
    assert rq[2] == {"in": None, "out": None}
    # negro en medio (hueco): no hay union entre clips
    rq = compose.seq_ramps(_seq(dict(a), 1.0, dict(c)))
    assert rq[0]["out"] is None and rq[2]["in"] is None
    # crossfade real en la union: sin rampa
    cx = dict(c, trans={"type": "crossfade", "dur": 0.5})
    assert compose.seq_ramps(_seq({"path": "a", "ramp_out": r}, cx))[0]["out"] is None
    assert compose.seq_ramps(_seq({"path": "a"}, cx))[1]["in"] is None
    # el usuario apago la rampa de esa union ("ramp": false en el item que entra, o en su montage)
    assert compose.seq_ramps(_seq({"path": "a", "ramp_out": r}, {"path": "b", "ramp": False}))[0]["out"] is None
    assert compose.seq_ramps(_seq({"path": "a"}, {"path": "b", "ramp_in": r,
                                                  "montage": {"ramp": False}}))[1]["in"] is None
    # una imagen no tiene cuadros de mas
    assert compose.seq_ramps(_seq({"path": "a"}, {"path": "i", "type": "image", "ramp_in": r}))[1]["in"] is None
    # sin rampas pedidas: nada
    assert compose.seq_ramps(_seq({"path": "a"}, {"path": "b"})) == [{"in": None, "out": None}] * 2


def test_annotate_ramps_en_la_toma_que_entra():
    shots = [{"index": 0}, {"index": 1}, {"index": 2}]
    r = {"dur": 0.3, "speed": 2.0}
    req = [{"in": None, "out": r}, {"in": None, "out": None}, {"in": r, "out": None}]
    eff = [{"in": None, "out": {"dur": 0.3, "speed": 2.0, "post": 0.3, "req": 0.3}}, None,
           {"in": {"dur": 0.2, "speed": 2.0, "pre": 0.2, "req": 0.3}, "out": None}]
    summ = compose._annotate_ramps(shots, req, eff)
    assert "ramp" not in shots[0]
    assert shots[1]["ramp"] == {"side": "out", "dur": 0.3, "speed": 2.0}
    assert shots[2]["ramp"] == {"side": "in", "dur": 0.2, "speed": 2.0}
    assert [(s["index"], s["side"], s["dur"], s["req"]) for s in summ] == [(1, "out", 0.3, 0.3),
                                                                         (2, "in", 0.2, 0.3)]
    # pedida pero sin hacerse (sin manija): en el resumen con dur 0 y sin "ramp" en la toma
    shots = [{"index": 0}, {"index": 1}]
    summ = compose._annotate_ramps(shots, [{"in": None, "out": r}, {"in": None, "out": None}], [None, None])
    assert "ramp" not in shots[1] and summ == [{"index": 1, "side": "out", "dur": 0.0, "speed": 2.0, "req": 0.3}]


def test_los_silencios_no_se_comen_la_rampa():
    """La rampa cae en la pausa de la union y el analisis corta esa pausa: sin recortar el silencio,
    en el video final quedaban ~0.1 s de rampa (medido en un montaje real). Solo se recortan los
    silencios; lo de fuera de la rampa se sigue quitando; tomas y cortes del usuario no se tocan; y
    ningun corte deja cuadros sueltos de la otra camara junto al cambio."""
    shots = [{"start": 0.0, "end": 2.767}, {"start": 2.767, "end": 6.8, "join": "line",
                                             "ramp": {"side": "out", "dur": 0.3, "speed": 2.0}},
             {"start": 6.8, "end": 9.2, "join": "splice"},
             {"start": 9.2, "end": 12.0, "join": "section", "ramp": {"side": "in", "dur": 0.3, "speed": 2.0}}]
    assert compose.ramp_windows(shots) == [(2.467, 2.767), (9.2, 9.5)]
    cuts = [{"start": 1.0, "end": 1.4, "enabled": True},                       # lejos: igual
            {"start": 2.62, "end": 3.02, "enabled": True},                     # cruza la union con rampa de salida
            {"start": 6.66, "end": 7.04, "enabled": True},                     # union sin rampa: igual
            {"start": 9.03, "end": 9.43, "enabled": True},                     # rampa de entrada
            {"start": 9.3, "end": 9.4, "enabled": True},                       # adentro de la rampa: fuera
            {"start": 2.5, "end": 2.9, "src": "takes", "id": "t1"},            # toma: no se toca
            {"start": 9.1, "end": 9.45, "user": True, "enabled": True}]        # del usuario: no se toca
    got = compose.keep_ramps(cuts, shots)
    # el de la rampa de entrada sigue 0.08 s dentro de ella: nunca reanuda con un cuadro de la que sale
    assert got == [cuts[0], {"start": 2.767, "end": 3.02, "enabled": True}, cuts[2],
                   {"start": 9.03, "end": 9.28, "enabled": True}, cuts[5], cuts[6]]
    assert cuts[1] == {"start": 2.62, "end": 3.02, "enabled": True}           # copia
    # lo que quedaria < 0.05 s no se corta; tras el cambio con rampa de salida, lo que reanudaria a
    # menos de 0.1 s de la toma que entra tampoco (dejaria cuadros sueltos): se queda su inicio
    assert compose.keep_ramps([{"start": 2.44, "end": 2.78}], shots) == []
    assert compose.keep_ramps([{"start": 2.8, "end": 3.2}], shots) == [{"start": 2.767, "end": 3.2}]
    assert compose.keep_ramps([{"start": 2.9, "end": 3.2}], shots) == [{"start": 2.9, "end": 3.2}]
    # un silencio que termina justo antes del cambio con rampa de entrada tambien sigue dentro de ella
    assert compose.keep_ramps([{"start": 8.8, "end": 9.15}], shots) == [{"start": 8.8, "end": 9.28}]
    assert compose.keep_ramps([{"start": 8.5, "end": 9.0}], shots) == [{"start": 8.5, "end": 9.0}]
    assert compose.keep_ramps([{"start": 9.4, "end": 9.9}], shots) == [{"start": 9.5, "end": 9.9}]
    assert compose.keep_ramps(cuts, [{"start": 0.0}, {"start": 3.0}]) is cuts   # sin rampas: igual


def test_finish_plan_deja_la_rampa_en_el_video_final():
    info = {"shots": [{"start": 0.0, "end": 2.767, "index": 0, "kind": "clip", "src": "a.mov"},
                      {"start": 2.767, "end": 6.0, "index": 1, "kind": "clip", "src": "b.mov", "join": "line",
                       "ramp": {"side": "out", "dur": 0.3, "speed": 2.0}}]}
    plan = {"duration": 6.0, "cuts": [{"start": 2.62, "end": 3.02, "enabled": True}]}
    compose.finish_plan(plan, info, {"main": [{"montage": {"slot": "s1"}}]}, montage={"version": 1})
    assert plan["shots"][1]["ramp"]["side"] == "out"
    assert plan["cuts"] == [{"start": 2.767, "end": 3.02, "enabled": True}]


# ------------------------------------------------------------------ cuadro por cuadro
def test_el_generador_y_el_lector_de_cuadros_son_exactos(media):
    assert _read(media["a"]) == list(range(120))
    out, eff = _norm(media, "plain", media["a"], 2.0, 3.0)
    assert eff is None
    assert _read(out) == list(range(60, 90))
    assert _expected(_cfr(120), 2.0, 30) == list(range(60, 90))


def test_rampa_de_entrada_cuadro_por_cuadro(media):
    """La toma que ENTRA empieza 0.3 s antes en su fuente y corre a 2x 9 cuadros; luego 1x, ya en
    los mismos cuadros que sin rampa (alcanzo a su audio)."""
    r = {"dur": 0.3, "speed": 2.0}
    out, eff = _norm(media, "rin", media["a"], 2.0, 3.0, ramp_in=r)
    assert eff["in"]["dur"] == pytest.approx(0.3) and eff["out"] is None
    got = _read(out)
    assert got == _expected(_cfr(120), 2.0, 30, rin=r)
    assert got[:10] == [51, 53, 55, 57, 59, 61, 63, 65, 67, 69]        # 2x desde 1.7 s
    plain = _read(_norm(media, "plain_in", media["a"], 2.0, 3.0)[0])
    assert got[9:] == plain[9:]                                          # fuera de la rampa: igual
    assert len(got) == len(plain) == 30


def test_rampa_de_salida_cuadro_por_cuadro(media):
    """La toma que SALE sigue 0.3 s pasado su "out", comprimidos en sus ultimos 9 cuadros."""
    r = {"dur": 0.3, "speed": 2.0}
    out, eff = _norm(media, "rout", media["a"], 1.0, 2.0, ramp_out=r)
    assert eff["out"]["dur"] == pytest.approx(0.3) and eff["in"] is None
    got = _read(out)
    assert got == _expected(_cfr(120), 1.0, 30, rout=r)
    plain = _read(_norm(media, "plain_out", media["a"], 1.0, 2.0)[0])
    assert got[:21] == plain[:21] == list(range(30, 51))
    assert got[21:] == [51, 53, 55, 57, 59, 61, 63, 65, 67]              # 2x hasta 2.3 s de fuente
    assert len(got) == 30


def test_las_dos_rampas_y_velocidad_no_entera(media):
    rin, rout = {"dur": 0.2, "speed": 1.5}, {"dur": 0.3, "speed": 2.0}
    out, eff = _norm(media, "both", media["a"], 1.5, 2.7, ramp_in=rin, ramp_out=rout)
    assert eff["in"]["dur"] == pytest.approx(0.2) and eff["out"]["dur"] == pytest.approx(0.3)
    assert _read(out) == _expected(_cfr(120), 1.5, 36, rin=rin, rout=rout)


def test_audio_identico_y_duracion_igual_con_rampa(media):
    """El audio de un segmento con rampa es EL MISMO (muestra por muestra) que sin rampa, con sus
    micro-fundidos, volumen y limitador; mismos cuadros y misma duracion."""
    r = {"dur": 0.3, "speed": 2.0}
    kw = dict(vol=1.4, afin=0.015, afout=0.015, limit=True)
    a, _ = _norm(media, "au_plain", media["a"], 1.237, 2.411, **kw)
    b, eff = _norm(media, "au_ramp", media["a"], 1.237, 2.411, ramp_in=r, ramp_out=r, **kw)
    assert eff["in"] and eff["out"]
    xa, xb = _pcm(a), _pcm(b)
    assert xa.shape == xb.shape and np.array_equal(xa, xb)
    pa, pb = compose.probe(a), compose.probe(b)
    assert pa["dur"] == pb["dur"]
    assert len(_read(a)) == len(_read(b)) == round((2.411 - 1.237) * FPS)


def test_sin_manija_se_acorta_o_no_se_hace(media):
    r = {"dur": 0.3, "speed": 2.0}
    # 0.2 s antes de "in": rampa de 6 cuadros desde el primer cuadro del archivo
    out, eff = _norm(media, "clamp_in", media["a"], 0.2, 1.2, ramp_in=r)
    assert eff["in"]["dur"] == pytest.approx(0.2)
    got = _read(out)
    assert got[:7] == [0, 2, 4, 6, 8, 10, 12] and got == _expected(_cfr(120), 0.2, 30, rin=eff["in"])
    # 0.05 s: no alcanza ni RAMP_MIN -> sin rampa, igual que sin pedirla
    out, eff = _norm(media, "clamp_none", media["a"], 0.05, 1.05, ramp_in=r)
    plain, _ = _norm(media, "clamp_plain", media["a"], 0.05, 1.05)
    assert eff is None and _read(out) == _read(plain)
    assert np.array_equal(_pcm(out), _pcm(plain))
    # rampa de salida al final del archivo (4.0 s): 0.1 s de manija -> 3 cuadros
    out, eff = _norm(media, "clamp_out", media["a"], 2.9, 3.9, ramp_out=r)
    assert eff["out"]["dur"] == pytest.approx(0.1)
    got = _read(out)
    assert got == _expected(_cfr(120), 2.9, 30, rout=eff["out"])
    assert got[-4:] == [113, 114, 116, 118]
    # "out" en el final exacto: sin cuadros de mas, sin rampa
    _o, eff = _norm(media, "clamp_end", media["a"], 3.0, 4.0, ramp_out=r)
    assert eff is None


def test_rampa_con_fuente_rotada(media):
    d = media["d"]
    src = _make(d / "rot.mov", 3.0, rotation=90)
    assert compose.probe(src)["rotation"] in (90, 270) and (compose.probe(src)["w"], compose.probe(src)["h"]) == (W, H)
    assert _read(_norm(media, "rot_plain", src, 1.0, 2.0)[0]) == list(range(30, 60))
    r = {"dur": 0.3, "speed": 2.0}
    out, eff = _norm(media, "rot_ramp", src, 1.0, 2.0, ramp_in=r)
    assert eff["in"] and _read(out) == _expected(_cfr(90), 1.0, 30, rin=r)


def test_rampa_con_fuente_hlg_tonemap(media):
    d = media["d"]
    src = _make(d / "hlg.mp4", 3.0, hdr=True)
    assert compose.probe(src)["hdr"] == "hlg"
    r = {"dur": 0.3, "speed": 2.0}
    out, eff = _norm(media, "hlg_ramp", src, 1.0, 2.0, ramp_out=r)
    assert eff["out"] and _read(out) == _expected(_cfr(90), 1.0, 30, rout=r)
    info = compose.video_io.probe(str(out))
    assert info["pix_fmt"] == "yuv420p" and not info["hdr"]             # tonemap a SDR 8 bits


def test_rampa_con_fuente_60fps_vfr_a_30(media):
    """iPhone a ~60 fps VFR: el remapeo usa los tiempos REALES de cada cuadro y fps=30 toma el
    ultimo cuadro que cae en cada cuadro de salida (con huecos, el anterior)."""
    d = media["d"]
    src = _make(d / "vfr.mp4", 4.0, fps=60, drop="not(eq(mod(n,7),3))")
    times = _frame_times(src)
    assert len(times) < 240 and max(times) == 239                        # le faltan cuadros: VFR
    r = {"dur": 0.3, "speed": 2.0}
    for name, t_in, kw in (("vfr_in", 2.0, {"ramp_in": r}), ("vfr_out", 1.0, {"ramp_out": r})):
        out, eff = _norm(media, name, src, t_in, t_in + 1.0, **kw)
        assert eff is not None
        got = _read(out)
        assert len(got) == 30
        assert got == _expected(times, t_in, 30, rin=kw.get("ramp_in"), rout=kw.get("ramp_out"))
    # en la rampa se avanza ~4 cuadros de fuente (60 fps a 2x) por cuadro de salida
    got = _read(d / "vfr_in.mp4")
    steps = np.diff(got[:9])
    assert 3 <= steps.mean() <= 5 and np.diff(got[10:]).mean() == pytest.approx(2.0, abs=0.3)


def test_video_sin_audio_dura_lo_que_su_tramo(media):
    """Regresion: el -t quedaba como opcion de la entrada de silencio y un video sin audio salia
    desde "in" hasta el final de su fuente. Con y sin rampa dura lo que su tramo."""
    d = media["d"]
    src = _make(d / "mudo.mp4", 3.0, audio=False)
    a, _ = _norm(media, "mudo_plain", src, 0.5, 1.5)
    b, eff = _norm(media, "mudo_ramp", src, 0.5, 1.5, ramp_in={"dur": 0.3, "speed": 2.0})
    assert eff["in"]
    for p in (a, b):
        assert compose.probe(p)["dur"] == pytest.approx(1.0, abs=0.03)
        assert len(_read(p)) == 30
        x = _pcm(p)
        assert len(x) == pytest.approx(48000, abs=1100) and float(np.abs(x).max()) == 0.0


def _streams(path):
    r = subprocess.run([str(paths.FFPROBE), "-v", "error", "-show_entries",
                        "stream=codec_type,start_time,duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    out = {}
    for ln in r.stdout.split():
        kind, st, du = ln.split(",")[:3]
        out[kind] = (float(st), float(du))
    return out


def _frames_pts(path):
    """[(s del video, numero pintado)] de cada cuadro de `path` (pts exactos)."""
    from fractions import Fraction
    r = subprocess.run([str(paths.FFPROBE), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=time_base", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    tb = Fraction(r.stdout.strip().split(",")[0])
    r = subprocess.run([str(paths.FFPROBE), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "frame=pts", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    ts = [float(int(x.strip().rstrip(",")) * tb) for x in r.stdout.split() if x.strip()]
    ks = _read(path)
    assert len(ks) == len(ts)
    return list(zip(ts, ks))


def test_in_fuera_de_la_rejilla_el_video_empieza_en_su_cuadro(media):
    """Regresion (uniones exactas del montaje): con un "in" fuera de la rejilla de cuadros el primer
    cuadro tras el -ss caia a mas de medio cuadro de salida y el segmento empezaba un cuadro TARDE
    (en el video combinado ese corte se veia 33 ms despues que su audio, en ~la mitad de las uniones
    de un montaje real). Ahora el video empieza en 0 y dura lo mismo que su audio."""
    out, _ = _norm(media, "offgrid", media["a"], 1.237, 2.237)
    st = _streams(out)
    assert st["video"][0] == 0.0 and st["audio"][0] == 0.0
    assert st["video"][1] == pytest.approx(st["audio"][1], abs=1e-3) == pytest.approx(1.0, abs=1e-3)
    assert _read(out) == [38] + list(range(38, 67))   # el 1er cuadro despues de 1.237 s, sin hueco
    # en un montaje con "in" fuera de la rejilla cada toma ENTRA exactamente en su limite
    d = media["d"]
    main = [_item(media["a"], 0.537, 1.537, 0.0, "A", {"slot": "s1", "cand": "c1", "join": None}),
            _item(media["b"], 1.511, 2.411, 1.0, "B", {"slot": "s1", "cand": "c2", "join": "splice"}),
            _item(media["a"], 2.419, 3.319, 1.9, "A2", {"slot": "s2", "cand": "c1", "join": "line"})]
    info = {}
    out = compose.compose({"fps": FPS, "main": main}, str(d / "offgrid_mont.mp4"), d / "w_offgrid",
                          info=info)
    fr = _frames_pts(out)
    t0 = fr[0][0]                         # el video del mp4 arranca tras el relleno del AAC (~21 ms)
    fr = [(t - t0, k) for t, k in fr]
    sh = info["shots"]
    b0 = next(t for t, k in fr if k >= 256)
    a2 = next(t for t, k in fr if t > b0 and k < 256)
    assert b0 == pytest.approx(sh[1]["start"], abs=1e-3)
    assert a2 == pytest.approx(sh[2]["start"], abs=1e-3)
    assert [k for t, k in fr if t < b0][:2] == [17, 17]  # 0.537 s: el cuadro 17 (0.5667) desde 0


# ------------------------------------------------------------------ montaje completo
def _item(path, t_in, t_out, start, name, montage, **kw):
    it = {"path": path, "type": "video", "in": t_in, "out": t_out, "start": start, "name": name,
          "sid": name.lower(), "layer": 0, "fadeIn": 0.0, "fadeOut": 0.0, "vol": 1.0, "mute": False,
          "montage": montage}
    it.update(kw)
    return it


def _abA(media, ramps=True):
    """Intercalado A/B/A: A (0.5-1.5) -> B (1.5-2.4) empalme a media linea -> A de nuevo (2.4-3.3):
    rampa de salida en la primera union y de entrada en la segunda."""
    r = {"dur": 0.3, "speed": 2.0}
    x = [_item(media["a"], 0.5, 1.5, 0.0, "A", {"slot": "s1", "cand": "c1", "join": None, "setup": 0}),
         _item(media["b"], 1.5, 2.4, 1.0, "B", {"slot": "s1", "cand": "c2", "join": "splice", "setup": 1}),
         _item(media["a"], 2.4, 3.3, 1.9, "A2", {"slot": "s2", "cand": "c1", "join": "line", "setup": 0},
               sid="a")]
    if ramps:
        x[0]["ramp_out"] = dict(r)
        x[1]["ramp"] = True
        x[2]["ramp_in"] = dict(r)
        x[2]["ramp"] = True
        x[2]["ramp_auto"] = True
    return x


def test_montaje_con_rampas_audio_identico_y_misma_duracion(media):
    d = media["d"]
    outs, infos = {}, {}
    for tag, ramps in (("sin", False), ("con", True)):
        info = {}
        spec = {"fps": FPS, "main": _abA(media, ramps)}
        outs[tag] = compose.compose(spec, str(d / f"aba_{tag}.mp4"), d / f"w_aba_{tag}", info=info)
        infos[tag] = info
    # misma duracion, mismas uniones y el MISMO audio (muestra por muestra)
    assert compose.probe(outs["con"])["dur"] == compose.probe(outs["sin"])["dur"]
    key = [(s["start"], s["end"]) for s in infos["sin"]["shots"]]
    assert [(s["start"], s["end"]) for s in infos["con"]["shots"]] == key
    xa, xb = _pcm(outs["sin"]), _pcm(outs["con"])
    assert xa.shape == xb.shape and np.array_equal(xa, xb)
    # la rampa queda en la toma que ENTRA por cada union
    sh = infos["con"]["shots"]
    assert "ramp" not in sh[0]
    assert sh[1]["ramp"] == {"side": "out", "dur": 0.3, "speed": 2.0}
    assert sh[2]["ramp"] == {"side": "in", "dur": 0.3, "speed": 2.0}
    assert all("ramp" not in s for s in infos["sin"]["shots"])
    assert [(r["index"], r["side"], r["dur"]) for r in infos["con"]["ramps"]] == [(1, "out", 0.3), (2, "in", 0.3)]
    # cuadro por cuadro en el video combinado: A sigue a 2x pasado su "out" (1.5 s = cuadro 45)
    # hasta la union, B entra en su cuadro exacto, y A2 entra 0.3 s antes y alcanza a su audio
    got, plain = _read(outs["con"]), _read(outs["sin"])
    assert len(got) == len(plain) == 30 + 27 + 27
    assert got[:21] == plain[:21] == list(range(15, 36))
    assert got[21:30] == [36, 38, 40, 42, 44, 46, 48, 50, 52]
    assert got[30:57] == plain[30:57] == list(range(256 + 45, 256 + 72))
    assert got[57:66] == [63, 65, 67, 69, 71, 73, 75, 77, 79]
    assert got[66:] == plain[66:] == list(range(81, 99))
    # finish_plan: plan.shots con la rampa y la linea de tiempo del montaje la conserva
    plan = {"duration": compose.probe(outs["con"])["dur"]}
    spec = {"fps": FPS, "main": _abA(media, True)}
    compose.finish_plan(plan, infos["con"], spec, montage={"version": 1, "slots": []})
    ps = plan["shots"]
    assert [s.get("ramp") for s in ps] == [None, {"side": "out", "dur": 0.3, "speed": 2.0},
                                           {"side": "in", "dur": 0.3, "speed": 2.0}]
    tl = plan["montage"]["timeline"]
    assert tl[0]["ramp_out"] == {"dur": 0.3, "speed": 2.0} and "ramp_in" not in tl[0]
    assert tl[2]["ramp_in"] == {"dur": 0.3, "speed": 2.0} and tl[2]["ramp"] is True
    assert tl[2]["ramp_auto"] is True and tl[1]["ramp"] is True


def test_montaje_rampa_apagada_por_el_usuario(media):
    d = media["d"]
    main = _abA(media, True)
    main[2]["ramp"] = False                          # el usuario apago la rampa de la 2a union
    info = {}
    out = compose.compose({"fps": FPS, "main": main}, str(d / "aba_off.mp4"), d / "w_aba_off", info=info)
    sh = info["shots"]
    assert sh[1]["ramp"]["side"] == "out" and "ramp" not in sh[2]
    got = _read(out)
    assert got[57:] == list(range(72, 99))           # A2 entra en su cuadro, sin rampa


def test_sin_rampas_el_comando_de_siempre(media, monkeypatch):
    """Un item sin rampas se normaliza con UNA entrada y sin setpts (como siempre)."""
    cmds = []
    real = compose._run
    monkeypatch.setattr(compose, "_run", lambda c: (cmds.append([str(x) for x in c]), real(c))[1])
    _norm(media, "cmd_plain", media["a"], 1.0, 2.0)
    _norm(media, "cmd_ramp", media["a"], 1.0, 2.0, ramp_in={"dur": 0.3, "speed": 2.0})
    plain, ramp = cmds
    assert plain.count("-i") == 1 and not any("setpts" in c for c in plain)
    assert plain[plain.index("-ss") + 1] == "1.000"
    assert ramp.count("-i") == 2 and any(c.startswith("setpts=") for c in ramp)
    # video desde 0.3 s antes; audio con el mismo -ss que sin rampa
    ss = [ramp[k + 1] for k, c in enumerate(ramp) if c == "-ss"]
    assert ss == ["0.700000", "1.000"]
    assert ramp[ramp.index("-t") + 1] == plain[plain.index("-t") + 1]
