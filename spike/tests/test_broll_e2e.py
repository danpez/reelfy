"""E2E de los videos de apoyo en fragmentos en un navegador real (tests/js/broll_e2e.mjs) contra un servidor
AISLADO (tests/devserver.sh) con el motor REAL: transcripcion por clip (whisper), montaje (el video mudo
nunca es toma), el analisis toma por toma con el VLM local (ollama), Minimal (cortes secos), compose con
los fragmentos, el Studio (inspector: por que, formato, tomas, cambiar una) y el export con la hoja.

Medios SINTETICOS (repo publico): la voz de macOS `say` (Paulina) habla de un producto inventado
("Nubis") y luego de otra cosa; el video de apoyo (tests/_broll_media.make_film) tiene un titulo "nubis",
interfaces, personajes y PERSONAS (cuadros reales del material de prueba local o una silueta), sin audio,
con el numero de cada cuadro en 12 bits. En el video exportado se lee que cuadro del video de apoyo se ve
en medio de cada toma de cada fragmento: la toma correcta, al segundo correcto, y NUNCA una de persona.

Se activa con REELFY_PLAYWRIGHT=ruta a playwright-core (+ REELFY_PW_EXE: Chrome del sistema) y necesita
`say` (macOS), el modelo de voz descargado y ollama con el VLM; REELFY_E2E_SHOTS guarda capturas."""
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
CAM_W, CAM_H, FPS = 1080, 1920, 30
FW, FH = 1280, 720
TAKES = [
    ["Hoy les quiero contar de una herramienta nueva para su negocio.", "Se llama Nubis."],
    ["Nubis es un asistente que contesta el chat de tus clientes y agenda sus citas en el calendario.",
     "Además te manda reportes de tus ventas cada semana."],
    ["Mi abuela hace pan de elote los domingos en el mercado.", "Síganme para ver más."],
]
FILM = [("title", 2.5), ("screen", 2.2), ("person", 2.0), ("character", 2.0), ("screen", 2.3), ("person", 2.0),
        ("screen", 2.4), ("character", 2.0), ("screen", 2.2), ("person", 1.8), ("title", 2.0)]


def _voice():
    out = subprocess.run([SAY, "-v", "?"], capture_output=True, text=True).stdout
    return "Paulina" if any(line.startswith("Paulina") for line in out.splitlines()) else None


def _say(text, voice, tmp):
    import paths
    k = abs(hash((text, voice)))
    aiff, wv = tmp / f"say_{k}.aiff", tmp / f"say_{k}.wav"
    subprocess.run([SAY, "-v", voice, "-o", str(aiff), text], check=True, timeout=120)
    subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-i", str(aiff), "-ar", str(SR), "-ac", "1",
                    "-c:a", "pcm_s16le", str(wv)], check=True, timeout=120)
    with wave.open(str(wv)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


def _cam(path, audio, idx, created, tmp):
    import paths
    dur = len(audio) / SR
    a = tmp / f"cam{idx}.wav"
    with wave.open(str(a), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size={CAM_W}x{CAM_H}:rate={FPS}:duration={dur:.3f},hue=h={idx * 70}", "-i", str(a),
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "128k", "-shortest", "-metadata", f"creation_time={created}",
                    "-metadata", "com.apple.quicktime.make=Apple", "-metadata", "com.apple.quicktime.model=iPhone",
                    "-movflags", "use_metadata_tags", "-f", "mov", str(path)], check=True, timeout=600)


def _media(dst: Path):
    from _broll_media import make_film
    tmp = dst / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    v = _voice()
    pad = np.zeros(int(0.35 * SR), np.float32)
    gap = np.zeros(int(0.45 * SR), np.float32)
    names = []
    for k, lines in enumerate(TAKES):
        parts = [pad]
        for L in lines:
            parts += [_say(L, v, tmp), gap]
        name = f"TOMA_{k + 1:02d}.MOV"
        _cam(dst / name, np.concatenate(parts + [pad]), k + 1, f"2026-02-10T10:0{k}:00.000000Z", tmp)
        names.append(name)
    shots = make_film(dst / "producto.mp4", FILM, fps=FPS, size=(FW, FH))
    shutil.rmtree(tmp, ignore_errors=True)
    return {"files": names[:1] + ["producto.mp4"] + names[1:], "film": "producto.mp4", "shots": shots,
            "fps": FPS, "fw": FW, "fh": FH, "glossary": "Nubis"}


def _model_ok():
    sup = Path.home() / "Library" / "Application Support"
    return any((sup / d / "models" / "ggml-large-v3-turbo.bin").exists() for d in ("online.mixiuh.reelfy", "Reelfy"))


def _vlm_ok():
    url = (os.environ.get("REELFY_OLLAMA_URL") or "http://127.0.0.1:11498").rstrip("/")
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=3) as r:
            names = [m.get("name") for m in json.loads(r.read()).get("models", [])]
        return (os.environ.get("REELFY_VLM_MODEL") or os.environ.get("REELFY_LLM_MODEL") or "qwen3.5:4b") in names
    except Exception:  # noqa
        return False


def test_videos_de_apoyo_e2e_en_navegador(tmp_path):
    pw = os.environ.get("REELFY_PLAYWRIGHT")
    if not (pw and NODE):
        pytest.skip("E2E de los videos de apoyo desactivado (define REELFY_PLAYWRIGHT)")
    if not SAY or not _voice():
        pytest.skip("sin `say` en espanol (macOS)")
    if not _model_ok():
        pytest.skip("sin el modelo de voz descargado")
    if not _vlm_ok():
        pytest.skip("sin ollama con el VLM local")
    sys.path.insert(0, str(SPIKE / "scripts"))
    from _broll_media import bit_cells
    from motion import media as MF
    from motion import reel as RL
    from motion import render as R
    from motion import timeline as T
    from motion import video_io as V
    data, clipdir, xdir = tmp_path / "data", tmp_path / "clips", tmp_path / "exportados"
    (data / "input").mkdir(parents=True)
    (data / "music-models").mkdir(parents=True)        # carpeta real: el servidor no enlaza una carpeta ajena
    clipdir.mkdir()
    info = _media(clipdir)
    port = None
    for p in range(8800, 8900):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                port = p
                break
    assert port, "sin puerto libre en 8800-8899"
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
        r = subprocess.run([NODE, str(SPIKE / "tests" / "js" / "broll_e2e.mjs"), f"http://127.0.0.1:{port}", str(clipdir),
                            out, json.dumps(info), str(xdir)], capture_output=True, text=True, timeout=3600,
                           env={**os.environ})
        assert r.returncode == 0, r.stdout[-8000:] + r.stderr[-3000:]
    finally:
        try:
            os.killpg(srv.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        srv.wait(timeout=30)
    # ---------------------------------------------------------------- el video exportado
    res = json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("RESULT "))[7:])
    plan, files = res["plan"], res["files"]
    file = max(files, key=lambda f: float(V.probe(f).get("duration") or 0.0))
    vi = V.probe(file)
    W, H = int(vi["w"]), int(vi["h"])
    reels = [m for m in plan["fx"]["media"] if m.get("style") == "reel" and m.get("enabled", True) is not False]
    assert reels
    # cada pedazo de pantalla completa se recorta donde se midio para SU toma (el catalogo del plan)
    cat = {(s["sid"], sh["i"]): sh for s in plan["montage"]["broll"]["sources"] for sh in s.get("shots") or []}
    for m in reels:
        for pc in m["pieces"]:
            sh = cat[(m["sid"], pc["shot"])]
            if m["layout"] == "full":
                assert pc["cx"] is not None and abs(pc["cx"] - sh["cx"]) < 1e-6, (m["id"], pc, sh.get("cx"))
    truth = info["shots"]
    job = R.prepare(plan, {"W": W, "H": H, "base_W": W, "base_H": H, "cache_dir": str(tmp_path / "cache"),
                           "media_root": str(data / "input" / "sources")})["job"]
    jits = [j for j in job["media"] if j.get("_reel")]
    assert len(jits) == len(reels)
    fps = float(vi.get("fps") or 30.0)
    want = {}
    for jit in jits:
        for row in jit["_media"]["reel"]:
            t_mid = jit["_fs"] + (row[0] + row[1]) / 2
            want[int(round(t_mid * fps))] = (jit, row)
    box = R.layout_box(W, H)
    seen = 0
    with V.FrameReader(file, W, H, 0, None, fps) as rd:
        for k, fr in enumerate(rd):
            if k not in want:
                continue
            jit, row = want[k]
            if jit["_layout"] == "full":
                x0, y0, bw, bh = box
                eff = R._reel_crop(row[3], FW, FH, bw, bh) or [0.0, 0.0, 1.0, 1.0]
            else:
                cx0, cy0, bw, bh = MF.card_px(jit["_card"], box[2], box[3])
                x0, y0 = box[0] + cx0, box[1] + cy0
                eff = R._reel_crop(row[3], FW, FH, bw, bh) or [0.0, 0.0, 1.0, 1.0]
            val, bad, mask = 0, False, 0
            for i, (a, b, c, d) in enumerate(bit_cells(FW, FH)):
                u0, u1 = (a / FW - eff[0]) / (eff[2] - eff[0]), (c / FW - eff[0]) / (eff[2] - eff[0])
                v0, v1 = (b / FH - eff[1]) / (eff[3] - eff[1]), (d / FH - eff[1]) / (eff[3] - eff[1])
                if not (0.0 <= u0 and u1 <= 1.0 and 0.0 <= v0 and v1 <= 1.0):
                    # (la ventana recortada al APARATO -una pantalla de pie sobre un fondo liso- deja fuera los
                    # renglones de bits que el video de prueba pinta debajo de el: se comparan los que se ven)
                    continue
                mask |= 1 << i
                xa, xb = int(round(x0 + u0 * bw)), int(round(x0 + u1 * bw))
                ya, yb = int(round(y0 + v0 * bh)), int(round(y0 + v1 * bh))
                mx, my = max(1, (xb - xa) // 4), max(1, (yb - ya) // 4)
                v = float(fr[ya + my:yb - my, xa + mx:xb - mx, :3].mean())
                if 80 < v < 175:
                    bad = True
                val |= (1 << i) if v >= 175 else 0
            lt = k / fps - jit["_fs"]
            exp = RL.src_time(jit["_media"]["reel"], lt)
            assert not bad, (jit["id"], k)
            assert mask & 0xFF == 0xFF, (jit["id"], k, bin(mask))          # al menos los 8 bits bajos
            n_exp = int(round(exp * FPS))
            hit = [n for n in range(n_exp - 2, n_exp + 3) if (n & mask) == val]
            assert hit, (jit["id"], k, val / FPS, exp, bin(mask))
            val = hit[0] if mask != (1 << 12) - 1 else val
            kind = next(s["kind"] for s in truth if s["a"] <= val / FPS < s["b"])
            assert kind not in ("person", "dark"), (jit["id"], k, kind)     # nunca una persona
            seen += 1
    assert seen >= len(want) - 1, (seen, len(want))
