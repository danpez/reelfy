"""E2E de la grabacion de pantalla sincronizada en un navegador real (tests/js/screen_e2e.mjs) contra un
servidor AISLADO (tests/devserver.sh) con el motor REAL: transcripcion por clip (whisper), montaje,
composicion, analisis, director, /frame y el export en 4K con la hoja «Exportar» (recompone desde las
fuentes).

Medios SINTETICOS (repo publico): voces de macOS `say` (Paulina habla a camara; Eddy hace la charla de
preparacion que solo graba la pantalla), tomas 4K (2160x3840, 30 fps) de video de prueba de ffmpeg y una
"grabacion de pantalla" como la de un celular emulado en la compu: 60 fps, su AUDIO empieza 0.1 s
despues de su video (start_time, como el iPhone) y la app se ve dentro de un MARCO lavanda fijo
(mientras se prepara la app, antes de la toma, se ve otra cosa a pantalla completa). La app lleva el
numero de su cuadro en 12 franjas. La toma de la demo y la pantalla comparten la voz: el montaje debe
reconocerla como grabacion de pantalla sincronizada (nunca como toma), con el desfase EXACTO en segundos
de los archivos (la charla + el inicio del audio), recortar el marco, y el video final (en el Studio y
en el 4K exportado) debe mostrar el cuadro correcto de la pantalla al inicio, en medio y al final, en
una ventana centrada sin nada del marco.

Se activa con REELFY_PLAYWRIGHT=ruta a playwright-core (+ REELFY_PW_EXE: Chrome del sistema) y necesita
`say` (macOS) y el modelo de voz descargado; REELFY_E2E_SHOTS guarda capturas."""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
import pytest

SPIKE = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
SAY = shutil.which("say")
SR = 48000
FPS = 30                                     # tomas de camara
SFPS = 60                                    # la grabacion de pantalla (otro reloj)
CAM_W, CAM_H = 2160, 3840                    # tomas 4K: el export en 4K recompone desde ellas
SW, SH = 240, 516                            # la app (dentro del marco)
FL, FT, FR, FB = 12, 16, 12, 26              # marco del dispositivo emulado (px)
FW, FH = SW + FL + FR, SH + FT + FB
LAVANDA = (216, 220, 232)
A_DELAY = 0.128                              # el audio de la pantalla empieza despues de su video
BAND0, BANDH, BITS = 0.20, 0.05, 12          # franjas del numero de cuadro (fraccion del alto de la APP)
DEMO = ("Primero abro el calendario de la aplicación y elijo el martes en la tarde para la entrega. "
        "Le pongo una alarma de treinta minutos antes. Listo, la entrega quedó guardada en el calendario.")
INTRO = "Hoy les enseño una herramienta de notas que uso para organizar la semana del taller."
CHAT = "Un segundo, déjame abrir la aplicación. Ok, empiezo."


def _voices():
    out = subprocess.run([SAY, "-v", "?"], capture_output=True, text=True).stdout

    def has(v):
        return any(line.startswith(v) for line in out.splitlines())
    a = "Paulina" if has("Paulina") else None
    b = "Eddy (Spanish (Mexico))" if has("Eddy (Spanish (Mexico))") else a
    return a, b


def _say(text, voice, tmp):
    import paths
    aiff, wv = tmp / f"say_{abs(hash((text, voice)))}.aiff", tmp / f"say_{abs(hash((text, voice)))}.wav"
    subprocess.run([SAY, "-v", voice, "-o", str(aiff), text], check=True, timeout=120)
    subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-i", str(aiff), "-ar", str(SR), "-ac", "1",
                    "-c:a", "pcm_s16le", str(wv)], check=True, timeout=120)
    with wave.open(str(wv)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


def _wav(path, y):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())


def _cam(path, audio, idx, created, tmp):
    import paths
    dur = len(audio) / SR
    a = tmp / f"cam{idx}.wav"
    _wav(a, audio)
    subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size={CAM_W}x{CAM_H}:rate={FPS}:duration={dur:.3f},hue=h={idx * 50}", "-i", str(a),
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "128k", "-shortest", "-metadata", f"creation_time={created}",
                    "-metadata", "com.apple.quicktime.make=Apple", "-metadata", "com.apple.quicktime.model=iPhone",
                    "-movflags", "use_metadata_tags", "-f", "mov", str(path)], check=True, timeout=600)


def _screen(path, audio, created, framed_from, tmp):
    """Grabacion de pantalla FW x FH a 60 fps: hasta `framed_from` s, otra pantalla a todo el cuadro
    (preparar la app: cambia en cada cuadro, sin marco); despues, la app (interfaz blanca con barra de
    arriba, boton abajo y el numero de cuadro en franjas) dentro de un marco lavanda fijo. Su audio
    empieza A_DELAY s despues de su primer cuadro (start_time de la pista de audio)."""
    import paths
    n = int(len(audio) / SR * SFPS) + 1
    rng = np.random.default_rng(7)
    frames = np.empty((n, FH, FW, 3), np.uint8)
    k0 = int(framed_from * SFPS)
    for k in range(n):
        f = frames[k]
        if k < k0:
            f[:] = rng.integers(40, 220, 3, dtype=np.uint8)              # inicio: otra pantalla, sin marco
            f[FH // 3:FH // 3 + 40] = 255 - f[0, 0]
            continue
        f[:] = LAVANDA
        app = f[FT:FT + SH, FL:FL + SW]
        app[:] = 248
        app[: int(SH * 0.08)] = (8, 121, 77)                             # barra de la app
        app[int(SH * 0.86):int(SH * 0.94), 20:SW - 20] = (30, 30, 36)    # boton de abajo
        app[int(SH * 0.10):int(SH * 0.18), 16:SW - 16] = 120 + (k * 3) % 100   # contenido que cambia
        for i in range(BITS):
            a, b = int(SH * (BAND0 + i * BANDH)) + 2, int(SH * (BAND0 + (i + 1) * BANDH)) - 2
            app[a:b, 16:SW - 16] = 235 if (k >> i) & 1 else 25
    a = tmp / "scr.wav"
    _wav(a, audio)
    p = subprocess.Popen([str(paths.FFMPEG), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{FW}x{FH}",
                          "-r", str(SFPS), "-i", "-", "-itsoffset", f"{A_DELAY}", "-i", str(a), "-map", "0:v", "-map", "1:a",
                          "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", "-c:a", "aac",
                          "-b:a", "128k", "-metadata", f"creation_time={created}",
                          "-metadata", "com.apple.quicktime.author=ReplayKitRecording",
                          "-movflags", "use_metadata_tags", "-f", "mov", str(path)], stdin=subprocess.PIPE)
    p.stdin.write(frames.tobytes())
    p.stdin.close()
    assert p.wait(timeout=300) == 0


def _media(dst: Path):
    from motion import video_io as V
    tmp = dst / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    va, vb = _voices()
    intro, demo, chat = _say(INTRO, va, tmp), _say(DEMO, va, tmp), _say(CHAT, vb, tmp)
    pad = np.zeros(int(0.3 * SR), np.float32)
    cam1 = np.concatenate([pad, intro, pad])
    cam2 = np.concatenate([pad, demo, pad])
    lead = np.concatenate([chat, np.zeros(int(0.7 * SR), np.float32)])        # charla de preparacion
    scr = np.concatenate([lead, cam2 * 0.35, np.zeros(int(1.5 * SR), np.float32)])
    _cam(dst / "CAM_0001.MOV", cam1, 1, "2026-01-10T10:00:00.000000Z", tmp)
    _cam(dst / "CAM_0002.MOV", cam2, 2, "2026-01-10T10:02:00.000000Z", tmp)
    _screen(dst / "PANTALLA_01.mov", scr, "2026-01-10T10:01:50.000000Z", len(lead) / SR - 0.3, tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    lead_s = V.audio_lead(V.probe(dst / "PANTALLA_01.mov"))
    assert 0.08 < lead_s < 0.2, lead_s                  # el audio de la pantalla de verdad empieza despues
    return {"offset": len(lead) / SR + lead_s, "audio_offset": len(lead) / SR, "lead": lead_s, "fps": SFPS,
            "band0": BAND0, "bandh": BANDH, "bits": BITS, "frame": [FL, FT, FR, FB], "sw": SW, "sh": SH,
            "fw": FW, "fh": FH, "lavanda": list(LAVANDA),
            "files": ["CAM_0001.MOV", "PANTALLA_01.mov", "CAM_0002.MOV"], "cam": "CAM_0002.MOV",
            "screen": "PANTALLA_01.mov"}


def _model_ok():
    sup = Path.home() / "Library" / "Application Support"
    return any((sup / d / "models" / "ggml-large-v3-turbo.bin").exists() for d in ("online.mixiuh.reelfy", "Reelfy"))


def _read_bits(fr, card, crop, frame_wh, info):
    """Numero de cuadro de la pantalla en el cuadro `fr` (None si no se lee): las franjas de la app,
    llevadas al rect de la ventana con el recorte del marco."""
    x0, y0, w, h = card
    fw, fh = frame_wh
    val = 0
    for i in range(info["bits"]):
        yc = FT + (info["band0"] + (i + 0.5) * info["bandh"]) * SH            # px de la grabacion completa
        y = int(round(y0 + (yc / fh - crop[1]) / (crop[3] - crop[1]) * h))
        xs = int(round(x0 + w * 0.3))
        v = float(fr[y - 1:y + 2, xs - 2:xs + 3, :3].mean())
        if 90 < v < 170:
            return None
        val |= (1 << i) if v >= 170 else 0
    return val


def test_pantalla_sincronizada_e2e_en_navegador(tmp_path):
    pw = os.environ.get("REELFY_PLAYWRIGHT")
    if not (pw and NODE):
        pytest.skip("E2E de la pantalla sincronizada desactivado (define REELFY_PLAYWRIGHT)")
    if not SAY or not _voices()[0]:
        pytest.skip("sin `say` en espanol (macOS)")
    if not _model_ok():
        pytest.skip("sin el modelo de voz descargado")
    sys.path.insert(0, str(SPIKE / "scripts"))
    from motion import media as MF
    from motion import render as R
    from motion import screen as SC
    from motion import timeline as T
    from motion import video_io as V
    data, clipdir, xdir = tmp_path / "data", tmp_path / "clips", tmp_path / "exportados"
    (data / "input").mkdir(parents=True)
    (data / "music-models").mkdir(parents=True)        # carpeta real: el servidor no enlaza una carpeta ajena
    clipdir.mkdir()
    info = _media(clipdir)
    port = None
    for p in range(8700, 8800):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                port = p
                break
    assert port, "sin puerto libre en 8700-8799"
    srv = subprocess.Popen([str(SPIKE / "tests" / "devserver.sh"), str(port), str(data)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, start_new_session=True,
                           env={**os.environ, "REELFY_EXPORT_DIR": str(xdir)})
    try:
        for _ in range(120):
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{port}/setup", timeout=2).status == 200:
                    break
            except Exception:  # noqa: aun arrancando
                time.sleep(0.5)
        out = os.environ.get("REELFY_E2E_SHOTS") or str(tmp_path / "shots")
        r = subprocess.run([NODE, str(SPIKE / "tests" / "js" / "screen_e2e.mjs"), f"http://127.0.0.1:{port}", str(clipdir),
                            out, json.dumps(info), str(xdir)], capture_output=True, text=True, timeout=3600,
                           env={**os.environ})
        assert r.returncode == 0, r.stdout[-8000:] + r.stderr[-3000:]
    finally:
        try:
            os.killpg(srv.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        srv.wait(timeout=30)
    # ---------------------------------------------------------------- el video exportado en 4K
    res = json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("RESULT "))[7:])
    plan, files = res["plan"], res["files"]
    file = max(files, key=lambda f: float(V.probe(f).get("duration") or 0.0))
    vi = V.probe(file)
    assert (int(vi["w"]), int(vi["h"])) == (2160, 3840), (vi["w"], vi["h"])
    it = next(x for x in plan["fx"]["media"] if x.get("style") == "screen")
    crop = SC.crop_of(it)
    assert crop is not None
    W, H = 2160, 3840
    job = R.prepare(plan, {"W": W, "H": H, "base_W": W, "base_H": H, "cache_dir": str(tmp_path / "cache"),
                           "media_root": str(data / "input" / "sources")})["job"]
    jit = next(x for x in job["media"] if x.get("_screen"))
    box = R.layout_box(W, H)
    x0, y0, w, h = MF.card_px(jit["_card"], box[2], box[3])
    card = (box[0] + x0, box[1] + y0, w, h)
    assert box[0] + x0 == W - (box[0] + x0 + w)                       # simetrica al pixel
    # la ventana a 4K se dibuja con la fuente normalizada AL TAMANO de la ventana (no ampliada del 1080)
    assert w >= 0.4 * W, w
    fps = float(vi.get("fps") or 30.0)
    tl = T.Timeline.from_plan(plan, fps=fps)
    fs, fe = jit["_fs"], jit["_fe"]
    want = [fs + f * (fe - fs) for f in (0.15, 0.5, 0.85)]
    got_n = 0
    lav = np.array(LAVANDA)
    with V.FrameReader(file, W, H, 0, None, fps) as rd:
        for k, fr in enumerate(rd):
            tf = k / fps
            if not any(abs(tf - q) < 0.5 / fps for q in want):
                continue
            n = _read_bits(fr, card, crop, (FW, FH), info)
            exp = SC.sync_time(it, tl.inverse(tf))
            assert n is not None and abs(n / SFPS - exp) <= 1 / 30 + 1 / 60 + 1e-6, (tf, n, exp)
            # nada del marco lavanda dentro de la ventana (anillo interior, sin las esquinas redondeadas)
            cx0, cy0, cw, ch = card
            rr = int(0.07 * cw)
            ring = np.concatenate([fr[cy0 + rr:cy0 + ch - rr, cx0 + 6, :3], fr[cy0 + rr:cy0 + ch - rr, cx0 + cw - 7, :3],
                                   fr[cy0 + 6, cx0 + rr:cx0 + cw - rr, :3]]).astype(int)
            assert (np.abs(ring - lav).sum(axis=1) < 24).mean() < 0.02
            got_n += 1
    assert got_n == 3, got_n
