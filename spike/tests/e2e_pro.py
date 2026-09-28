#!/usr/bin/env python3
"""E2E real del motor pro POR HTTP contra un servidor de Reelfy ya levantado.

Flujo (docs/MOTION-ENGINE.md 6.1): sube los clips crudos (/sources) -> /compose con el
director -> espera `review` (plan v2 con shots y fx) -> /frame en 3 tiempos -> /preview pro
(render_draft con audio) -> /render final con shorts -> descarga y valida:
  - duracion = la del timeline del plan (+-1 cuadro), 1080x1920, H.264 + AAC
  - audio integrado -14 LUFS (+-1) y true peak <= -1.0 dBTP; la musica ENTRO a la mezcla
    (stats.music.source ai/procedural) y el render no omitio nada (stats.skipped == [])
  - barra del analisis monotona (nunca retrocede) y el montaje avanza (no se queda en 4 %)
  - orientacion vertical correcta: cara derecha arriba, cabeza y pies visibles (como en la fuente)
  - overlays presentes (cuadro exacto con y sin overlays difiere donde hay texto/graficos)
  - /frame coincide con el export (misma edicion, diferencia de compresion)
  - opciones del proyecto en el render pro (/settings.pro_opts): logo de marca (subido por
    /brand/logo) visible en la esquina del export; el aire no cambia el base de una fuente
    vertical (el export reutiliza el analisis de fondo); el aire es opt-in (pro_opts_optin): en
    una fuente HORIZONTAL sintetica (flujo real) el default va a sangre completa
  - API del Studio: grupos reales de subtitulos (/captions), proxy 720p del video
    (/source?proxy=1: lado corto 720, mismo reloj, GOP corto), /jobs con tema/duracion/version
    y miniatura, /music con beats, /translate {texts, lang} a pt y de (traducido, mismas lineas
    por titulo, sin gallego ni espanol colado)
  - vista previa que se actualiza sola con la musica IA sin borrar la provisional (prev_file)
  - cambio de estilo con la musica IA: responde rapido, una generacion por trabajo y la
    propuesta queda sincronizada a los beats sola (director.music_grid)
  - perfil de la persona (FICTICIO) desde /compose: rotulo con su nombre y profesion, Seguir con
    su red y @usuario, pastilla de reservas con el enlace corto; PUT /profile de ida y vuelta
    (< 1 s, 409 con base_rev viejo) y /frame del rotulo distinto sin perfil
Avisos (no fallan la prueba, quedan en e2e_result.json -> warnings): VLM que cayo a
heuristicas, tomas que los recortes de la IA eliminaron completas (o casi) del final.
Guarda todo en --out (spike/work/e2e, ignorado por git) y reporta tiempos por etapa, con la
carga de la maquina y la etiqueta --label (frio: DATA nuevo; caliente: caches llenas).

No se versionan datos de clientes: los medios vienen de REELFY_TEST_MEDIA o spike/work/media.
Uso:  python tests/e2e_pro.py --base http://127.0.0.1:8013 [--media DIR] [--out DIR]
      (lo lanza tests/e2e_pro.sh, que levanta el servidor aislado)
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPIKE = HERE.parent
sys.path.insert(0, str(SPIKE / "scripts"))

import numpy as np  # noqa: E402

import paths  # noqa: E402

T0 = time.time()
LOG: list[str] = []


def log(msg: str) -> None:
    line = f"[e2e {time.time() - T0:7.1f}s] {msg}"
    print(line, flush=True)
    LOG.append(line)


def load() -> str:
    try:
        return os.getloadavg().__repr__()
    except Exception:  # noqa
        return "?"


# ---------------------------------------------------------------- HTTP
class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _req(self, method, path, data=None, headers=None, timeout=600, raw=False):
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return (body, dict(r.headers)) if raw else json.loads(body or b"null")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read()[:400]!r}") from None

    def get(self, path, raw=False, timeout=600):
        return self._req("GET", path, raw=raw, timeout=timeout)

    def post(self, path, obj=None, raw=False, timeout=600):
        return self._req("POST", path, json.dumps(obj or {}).encode(),
                         {"Content-Type": "application/json"}, raw=raw, timeout=timeout)

    def upload(self, path, files):
        bnd = "----reelfy" + uuid.uuid4().hex
        chunks = []
        for f in files:
            ct = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            chunks.append((f"--{bnd}\r\nContent-Disposition: form-data; name=\"files\"; "
                           f"filename=\"{f.name}\"\r\nContent-Type: {ct}\r\n\r\n").encode())
            chunks.append(f.read_bytes())
            chunks.append(b"\r\n")
        chunks.append(f"--{bnd}--\r\n".encode())
        return self._req("POST", path, b"".join(chunks),
                         {"Content-Type": f"multipart/form-data; boundary={bnd}"}, timeout=1800)

    def download(self, path, dst):
        body, _ = self.get(path, raw=True)
        Path(dst).write_bytes(body)
        return Path(dst)

    def upload_logo(self, png: Path):
        bnd = "----reelfy" + uuid.uuid4().hex
        body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"logo\"; filename=\"logo.png\"\r\n"
                f"Content-Type: image/png\r\n\r\n").encode() + png.read_bytes() + f"\r\n--{bnd}--\r\n".encode()
        return self._req("POST", "/brand/logo", body,
                         {"Content-Type": f"multipart/form-data; boundary={bnd}"})


def wait(api: Api, job: str, until, what: str, timeout: float, stages: dict | None = None,
         poll: float = 1.0, trace: list | None = None):
    """Sondea /status (sin plan: ?plan=0) hasta until(st). Registra el primer momento de
    cada `stage` (tiempos por etapa) y, con `trace`, la serie (t, stage, pct, eta)."""
    t0 = time.time()
    last_msg = None
    while True:
        st = api.get(f"/status/{job}?plan=0")
        if trace is not None and st.get("phase") not in ("review", "done", "error"):
            trace.append((round(time.time() - t0, 1), st.get("stage"), st.get("pct"), st.get("eta")))
        if stages is not None:
            for key in ("stage", "preview_stage", "analysis_stage"):
                sname = st.get(key)
                if sname and f"{key}:{sname}" not in stages:
                    stages[f"{key}:{sname}"] = round(time.time() - t0, 1)
        msg = (st.get("phase"), st.get("stage"), st.get("message"))
        if msg != last_msg:
            log(f"{what}: {st.get('phase')} / {st.get('stage')} {st.get('pct')}% - {st.get('message')}")
            last_msg = msg
        if st.get("phase") == "error":
            raise RuntimeError(f"{what}: error: {st.get('error')}")
        if until(st):
            return st, time.time() - t0
        if time.time() - t0 > timeout:
            raise TimeoutError(f"{what}: sin terminar en {timeout:.0f}s ({st.get('message')})")
        time.sleep(poll)


# ---------------------------------------------------------------- medicion
def ffprobe(path) -> dict:
    r = subprocess.run([str(paths.FFPROBE), "-v", "error", "-show_streams", "-show_format",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def loudness(path) -> dict:
    r = subprocess.run([str(paths.FFMPEG), "-hide_banner", "-nostats", "-i", str(path), "-vn",
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    txt = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", txt)
    p = re.search(r"Peak:\s+(-?[\d.]+) dBFS", txt)
    return {"lufs": float(i.group(1)) if i else None, "true_peak": float(p.group(1)) if p else None}


def frame_at(path, t, W=1080, H=1920) -> np.ndarray:
    r = subprocess.run([str(paths.FFMPEG), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf",
                        f"scale={W}:{H}:flags=bicubic,scale=in_color_matrix=bt709:in_range=tv,format=rgb24",
                        "-f", "rawvideo", "pipe:1"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(H, W, 3)


def jpeg_rgb(data: bytes) -> np.ndarray:
    import io
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def person_metrics(rgb: np.ndarray) -> dict:
    """Cara (YuNet) y extension vertical de la persona (PP-HumanSeg), normalizadas."""
    import cv2
    from motion import matte
    small = cv2.resize(rgb, (rgb.shape[1] // 2, rgb.shape[0] // 2), interpolation=cv2.INTER_AREA)
    faces = [f for f in matte.detect_faces(small) if f[4] >= 0.6]
    face = max(faces, key=lambda f: f[2] * f[3]) if faces else None
    out = {"face": [round(float(v), 3) for v in face] if face else None}
    try:
        p = matte.person_prob(rgb)
        rows = np.where((p > 0.5).mean(axis=1) > 0.02)[0]
        out["person_top"] = round(float(rows.min()) / p.shape[0], 3) if len(rows) else None
        out["person_bottom"] = round(float(rows.max() + 1) / p.shape[0], 3) if len(rows) else None
    except Exception as e:  # noqa: sin el modelo de persona solo se valida la cara
        out["person_error"] = str(e)
    return out


def contact_sheet(video, out_jpg, n=12, cols=6, W=270, H=480):
    from PIL import Image, ImageDraw
    info = ffprobe(video)
    dur = float(info["format"]["duration"])
    rows = math.ceil(n / cols)
    sheet = Image.new("RGB", (cols * W, rows * H), (0, 0, 0))
    for i in range(n):
        t = (i + 0.5) * dur / n
        im = Image.fromarray(frame_at(video, t, W, H))
        ImageDraw.Draw(im).text((6, 4), f"{t:5.2f}s", fill=(255, 255, 0))
        sheet.paste(im, ((i % cols) * W, (i // cols) * H))
    sheet.save(out_jpg, quality=86)
    return out_jpg


# ---------------------------------------------------------------- flujo
def plan_windows(plan):
    """Ventanas FINALES de items del plan: overlays (texto/graficos), escenas, camara y
    transiciones (para elegir tiempos limpios)."""
    from motion import timeline as TL
    tl = TL.Timeline.from_plan(plan, fps=30)
    fx = plan.get("fx") or {}
    over, busy = [], []
    for tr in ("text", "graphics"):
        for it in fx.get(tr) or []:
            if it.get("enabled", True) and not it.get("behind"):
                r = tl.map_item(it)
                if r:
                    over.append(r)
    for it in fx.get("cutaways") or []:
        r = tl.map_item(it) if it.get("enabled", True) else None
        if r:
            busy.append(r)
    for it in fx.get("effects") or []:
        # zoom/kenburns suaves se permiten (el director los pone en casi todo el video); se
        # excluyen los golpes de camara y los efectos que cambian el fondo o el cuadro entero
        if it.get("enabled", True) and it.get("kind") in ("punch", "shake", "bg_replace",
                                                          "flash_frame", "rgb_split"):
            r = tl.map_item(it)
            if r:
                busy.append(r)
    for it in fx.get("transitions") or []:
        a = tl.remap_clamped(float(it.get("at", 0)))
        d = float(it.get("dur", 0.4) or 0.4)
        busy.append((a - d, a + d))
    return tl, over, busy


def orientation_checks(final, plan, tl, busy, D, checks, n=6):
    """Orientacion vertical correcta en el export: en cuadros sin escenas/golpes/transiciones,
    la cara esta derecha (YuNet no detecta caras giradas) y arriba con la cabeza completa, y
    la persona llega abajo (pies) como en la FUENTE en el mismo instante."""
    def clean(t):
        return not any(a0 - 0.1 <= t <= b0 + 0.1 for a0, b0 in busy)
    cands = [float(t) for t in np.arange(0.5, D - 0.5, 0.25) if clean(t)]
    picks = [cands[int(i * (len(cands) - 1) / max(1, n - 1))] for i in range(n)] if len(cands) >= n else cands
    picks = sorted(set(picks))
    orient = []
    for tf in picks:
        m = person_metrics(frame_at(final, tf))
        t_src = tl.inverse(tf)
        ms = person_metrics(frame_at(plan["video"], t_src))   # la fuente compuesta (display)
        orient.append({"t_final": round(tf, 2), "t_src": round(t_src, 2), "out": m, "src": ms})
    faces_ok = [o for o in orient if o["out"]["face"] and 0.02 < o["out"]["face"][1] < 0.6
                and o["out"]["face"][1] - o["out"]["face"][3] / 2 > 0.0]
    feet_cmp = [o for o in orient if o["out"].get("person_bottom") is not None
                and o["src"].get("person_bottom") is not None and o["src"]["person_bottom"] >= 0.95]
    # con zoom suave el borde inferior puede recortarse un poco: basta con llegar al 85 %
    feet_ok = [o for o in feet_cmp if o["out"]["person_bottom"] >= 0.85]
    checks["vertical_head_visible"] = len(orient) >= 3 and len(faces_ok) >= int(0.8 * len(orient))
    checks["vertical_feet_like_source"] = len(feet_cmp) >= 2 and len(feet_ok) >= int(0.8 * len(feet_cmp))
    return {"orientation": orient}


# logo de prueba (sintetico): cuadro magenta con borde transparente; opciones del proyecto
# que el Studio manda con /frame, /preview y /render (aire 0.16 = default del Studio)
LOGO_RGB = (255, 0, 200)
ENGINE = {"logo": {"pos": "tr", "size": 0.14, "opacity": 1.0}, "air": 0.16, "enhance_audio": True}
# perfil FICTICIO de la persona (docs/MOTION-ENGINE.md 3.2): nunca datos de un cliente
E2E_PROFILE = {"name": "Dra. Lucía Demo", "title": "Odontóloga",
               "links": [{"url": "https://instagram.com/dra.lucia.demo", "primary": True},
                         {"url": "https://paseo.club/business/dra-lucia-demo"}]}


def make_logo(path: Path) -> Path:
    from PIL import Image
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (180, 180), LOGO_RGB + (255,)), (10, 10))
    im.save(path)
    return path


def logo_score(rgb: np.ndarray) -> float:
    """Fraccion de pixeles magenta del logo en la esquina superior derecha (caja 9:16)."""
    H, W = rgb.shape[:2]
    reg = rgb[int(0.01 * H):int(0.03 * H + 0.16 * W), int(W * 0.80):int(W * 0.99)].astype(np.int16)
    d = np.abs(reg - np.array(LOGO_RGB, np.int16)).max(axis=2)
    return float((d < 60).mean())


_WRONG_WORDS = {"pt": {"non", "sen", "moi", "unha", "cando", "tamén", "xa", "dende", "pero", "muy",
                        "cuando", "también", "ahora", "nuestro"},
                 "de": {"el", "los", "las", "una", "pero", "muy", "cuando", "también", "porque", "para"}}


def provisional_ok(api: "Api", pv: dict, first: str | None) -> bool:
    """Tras actualizarse sola con la musica IA, la vista previa provisional sigue servible (el
    Studio de hoy la sigue reproduciendo: pide rangos al hacer seek)."""
    if not first or pv.get("prev_file") != first:
        return False
    try:
        body, _ = api.get(f"/video/{first}", raw=True)
        return len(body) > 1000
    except Exception:  # noqa
        return False


def translation_verdict(src: list, got, lang: str) -> dict:
    """Traduccion de titulos util: misma cantidad, a lo mas uno sin traducir, mismas lineas por
    titulo y sin palabras de otro idioma (gallego por portugues, espanol colado)."""
    import unicodedata

    def norm(x):
        x = unicodedata.normalize("NFD", str(x).lower())
        return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in x if unicodedata.category(c) != "Mn")).strip()
    if not isinstance(got, list) or len(got) != len(src):
        return {"ok": False, "why": "cantidad"}
    same = [i for i, (a, b) in enumerate(zip(src, got)) if norm(a) == norm(b) and len(norm(a)) >= 3]
    lines = [i for i, (a, b) in enumerate(zip(src, got)) if a.count("\n") != str(b).count("\n")]
    words = {w for b in got for w in re.findall(r"[a-záéíóúâêôãõàçñüäöß]+", str(b).lower())}
    wrong = sorted(words & _WRONG_WORDS.get(lang, set()))
    return {"ok": len(same) <= 1 and not lines and not wrong, "same": same, "lines_changed": lines,
            "wrong_words": wrong}


def changed_px(a: np.ndarray, b: np.ndarray) -> float:
    return float((np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > 25).mean())


def horizontal_check(api: "Api", out: Path, res: dict) -> None:
    """Caso HORIZONTAL sintetico corto (testsrc2 1280x720 + voz local con `say`, o un tono): por
    el flujo real (/sources -> /compose -> review) y /frame. El default del Studio (air 0.16 sin
    air_user) da el cuadro a sangre completa (igual que sin aire) y /status.engine no registra
    aire; con air_user aparece el margen."""
    T, C = res["timings"], res["checks"]
    clip = out / "horizontal_sintetico.mp4"
    voice = out / "horizontal_voz.aiff"
    dur = 5.0
    audio = ["-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=48000:duration={dur}"]
    try:
        subprocess.run(["say", "-v", "Paulina", "-o", str(voice),
                        "Hola, hoy te cuento cómo elegir tu perfume para el día."],
                       check=True, capture_output=True, timeout=60)
        audio = ["-i", str(voice)]
    except Exception:  # noqa: sin `say` (otro SO): un tono
        pass
    subprocess.run([str(paths.FFMPEG), "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"testsrc2=s=1280x720:r=30:d={dur}"] + audio +
                   ["-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-ar", "48000", "-t", str(dur), str(clip)], check=True)
    t = time.time()
    src = api.upload("/sources", [clip])["sources"][0]
    job = api.post("/compose", {"main": [{"sid": src["id"], "type": "video", "in": 0, "out": src["dur"],
                                          "start": 0}], "highlights": 1, "glossary": "",
                                "director": {"style": "minimal", "intensity": 0.6, "seed": 7, "music": "none"},
                                "engine": ENGINE})["job_id"]
    wait(api, job, lambda s: s.get("phase") == "review", "horizontal", 1200, poll=1.0)
    T["horizontal_review_s"] = round(time.time() - t, 1)
    plan = api.get(f"/plan/{job}")["plan"]
    bare = copy.deepcopy(plan)
    for k in ("text", "graphics", "cutaways", "transitions", "effects"):
        bare["fx"][k] = []
    bare["fx"]["captions"] = dict(bare["fx"].get("captions") or {}, enabled=False)

    def frame(**kw):
        data, _ = api.post(f"/frame/{job}", {"plan": bare, "t_final": 1.0, "fmt": "9:16", **kw}, raw=True)
        return jpeg_rgb(data)
    t = time.time()
    default = frame(**ENGINE)
    T["horizontal_frame_s"] = round(time.time() - t, 2)
    eng = (api.get(f"/status/{job}?plan=0").get("engine") or {})
    none = frame(**dict(ENGINE, air=0))
    user = frame(**dict(ENGINE, air_user=True))
    from PIL import Image
    Image.fromarray(np.concatenate([none, default, user], axis=1)).save(out / "horizontal_aire.jpg")
    res["horizontal"] = {"job": job, "src": [src["w"], src["h"]], "engine_after_default": eng,
                         "changed_default": round(changed_px(default, none), 4),
                         "changed_air_user": round(changed_px(user, none), 4),
                         "director": {k: (plan.get("director") or {}).get(k) for k in ("style", "llm", "vlm")}}
    C["air_optin_horizontal"] = res["horizontal"]["changed_default"] < 0.002 and \
        res["horizontal"]["changed_air_user"] > 0.012 and "air" not in eng
    log(f"horizontal: {res['horizontal']}")


def redirect_music_check(api: "Api", job: str, plan: dict, res: dict) -> None:
    """Cambio de estilo con la musica IA (re-dirigir sin full, como el Studio): responde rapido
    sin sincronia, UNA generacion por trabajo (el segundo cambio cancela la del primero) y al
    terminar la propuesta queda guardada sincronizada con los beats (director.music_grid)."""
    T, C = res["timings"], res["checks"]
    t = time.time()
    r1 = api.post(f"/direct/{job}", {"plan": plan, "style": "elegante", "fmt": "9:16"})
    T["redirect_style1_s"] = round(time.time() - t, 2)
    t = time.time()
    r2 = api.post(f"/direct/{job}", {"plan": plan, "style": "minimal", "fmt": "9:16"})
    T["redirect_style2_s"] = round(time.time() - t, 2)
    info = {"pending1": (r1.get("music") or {}).get("pending"), "pending2": (r2.get("music") or {}).get("pending"),
            "ms1": r1.get("ms"), "ms2": r2.get("ms"), "notes2": (r2["plan"].get("director") or {}).get("notes")}
    t = time.time()
    if info["pending2"]:
        st, _ = wait(api, job, lambda s: (s.get("music") or {}).get("state") in
                     ("synced", "ready", "error", "canceled", "superseded"), "musica IA (re-dirigir)", 900)
        m = st.get("music") or {}
        info.update(state=m.get("state"), gen_t=m.get("t"), sync_s=m.get("sync_s"), message=m.get("message"))
    now = api.get(f"/plan/{job}")
    d = now["plan"].get("director") or {}
    T["redirect_music_sync_s"] = round(time.time() - t, 1)
    info.update(style=d.get("style"), grid_bpm=(d.get("music_grid") or {}).get("bpm"),
                plan_rev=now["plan_rev"], load=load())
    res["redirect_music"] = info
    C["redirect_music_synced"] = d.get("style") == "minimal" and bool(d.get("music_grid")) and \
        max(T["redirect_style1_s"], T["redirect_style2_s"]) < 15.0
    log(f"re-dirigir con musica IA: {info}")


def api_checks(api: "Api", job: str, plan: dict, src: list, out: Path, res: dict) -> None:
    """API nueva del Studio sobre el trabajo real: settings, /jobs, miniatura, /captions,
    proxy 720p y /translate. Registra tiempos y validaciones en `res`."""
    T, C = res["timings"], res["checks"]
    sett = api.get("/settings")
    res["settings"] = sett
    # el aire es opt-in (air_user): no va en pro_opts sino en pro_opts_optin
    C["settings_pro_opts"] = set(sett.get("pro_opts") or []) >= {"logo", "enhance_audio"} and \
        "air" not in (sett.get("pro_opts") or []) and sett.get("pro_opts_optin") == ["air"]
    jobs = api.get("/jobs")
    mine = next((j for j in jobs if j["id"] == job), {})
    res["jobs_entry"] = mine
    thumb_ok = False
    if mine.get("thumb"):
        body, hdr = api.get(mine["thumb"], raw=True)
        (out / "thumb.jpg").write_bytes(body)
        thumb_ok = hdr.get("content-type", hdr.get("Content-Type", "")).startswith("image/jpeg") and \
            jpeg_rgb(body).shape[0] == 360
    C["jobs_meta_thumb"] = bool(mine) and mine.get("version") == 2 and bool(mine.get("topic")) and \
        abs(float(mine.get("duration") or 0) - float(plan.get("duration") or 0)) < 0.05 and thumb_ok
    # grupos reales de subtitulos (los mismos del render) en tiempo original
    t = time.time()
    groups = api.post(f"/captions/{job}", {"plan": plan, "fmt": "9:16"})
    T["captions_s"] = round(time.time() - t, 2)
    t = time.time()
    api.post(f"/captions/{job}", {"plan": plan, "fmt": "9:16"})
    T["captions_cached_s"] = round(time.time() - t, 3)
    (out / "captions.json").write_text(json.dumps(groups, ensure_ascii=False, indent=1))
    from motion import timeline as TL
    tl = TL.Timeline.from_plan(plan, fps=30)
    kept = [w for ph in plan.get("phrases") or [] for w in ph
            if (lambda r: r is not None and r[1] - r[0] >= 0.01)(tl.map_range(float(w["start"]), float(w["end"])))]
    got = [w for g in groups for w in g["words"]]
    order_ok = all(g["start"] < g["end"] for g in groups) and \
        all(a["start_final"] <= b["start_final"] for a, b in zip(groups, groups[1:]))
    res["captions"] = {"groups": len(groups), "words": len(got), "plan_words_kept": len(kept),
                       "max_words": max((len(g["words"]) for g in groups), default=0)}
    C["captions_groups"] = bool(groups) and order_ok and len(got) == len(kept) and \
        [w["text"] for w in got] == [w["text"] for w in kept]
    # proxy 720p del video del trabajo
    t = time.time()
    st, _ = wait(api, job, lambda s: s.get("proxy_state") in ("done", "error", "canceled"),
                 "proxy", 900)
    T["proxy_wait_s"] = round(time.time() - t, 1)
    res["proxy"] = {k: st.get(k) for k in ("proxy_state", "proxy_t", "proxy_error", "proxy_url")}
    if st.get("proxy_state") == "done":
        body, hdr = api.get(f"/source/{job}?proxy=1", raw=True)
        pv = out / "source_proxy.mp4"
        pv.write_bytes(body)
        pi = ffprobe(pv)
        vs = [x for x in pi["streams"] if x["codec_type"] == "video"][0]
        kf = subprocess.run([str(paths.FFPROBE), "-v", "error", "-select_streams", "v:0", "-skip_frame",
                             "nokey", "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(pv)],
                            capture_output=True, text=True).stdout.split()
        kf = [float(x.split(",")[0]) for x in kf if x.split(",")[0].strip()]
        gaps = [b - a for a, b in zip(kf, kf[1:])]
        src_info = ffprobe(plan["video"])
        src_dur = float(src_info["format"]["duration"])
        src_v = [x for x in src_info["streams"] if x["codec_type"] == "video"][0]
        res["proxy"]["start_time"] = [float(src_v.get("start_time") or 0), float(vs.get("start_time") or 0)]
        res["proxy"].update({"W": vs["width"], "H": vs["height"], "codec": vs["codec_name"],
                             "duration": round(float(pi["format"]["duration"]), 3),
                             "src_duration": round(src_dur, 3), "max_gop_s": round(max(gaps), 3) if gaps else None,
                             "bytes": pv.stat().st_size, "header": hdr.get("x-reelfy-proxy") or hdr.get("X-Reelfy-Proxy")})
        C["source_proxy_720"] = min(vs["width"], vs["height"]) == 720 and vs["codec_name"] == "h264" and \
            abs(res["proxy"]["duration"] - src_dur) < 0.15 and bool(gaps) and max(gaps) <= 0.6 and \
            res["proxy"]["header"] == "ready" and \
            abs(res["proxy"]["start_time"][0] - res["proxy"]["start_time"][1]) <= 0.001   # mismo reloj
    else:
        C["source_proxy_720"] = False
    # /translate {texts, lang}: titulos y graficos del plan v2 a portugues y aleman. Valida lo que
    # importa (antes solo se comparaban longitudes): que se tradujeron (a lo mas uno igual), que
    # cada titulo conserva su numero de lineas y que no hay gallego ni espanol colado
    texts = []
    for it in (plan.get("fx") or {}).get("text") or []:
        if it.get("text") and it.get("preset") != "counter":
            texts.append(str(it["text"]))
    for g in (plan.get("fx") or {}).get("graphics") or []:
        if isinstance(g.get("text"), str) and len(g["text"].split()) >= 2:
            texts.append(g["text"])
    texts = texts[:6] or ["Agenda tu cita hoy"]
    for lang in ("pt", "de"):
        t = time.time()
        try:
            tr = api.post(f"/translate/{job}", {"texts": texts, "lang": lang}, timeout=600)
            T[f"translate_{lang}_s"] = round(time.time() - t, 1)
            got = tr.get(f"texts_{lang}")
            v = translation_verdict(texts, got, lang)
            res[f"translate_{lang}"] = {"src": texts, lang: got, "untranslated": tr.get("untranslated"), **v}
            C[f"translate_texts_{lang}"] = v["ok"] and f"phrases_{lang}" not in tr
        except Exception as e:  # noqa
            res[f"translate_{lang}"] = {"error": str(e)}
            C[f"translate_texts_{lang}"] = False


def _closing_items(plan: dict, kind: str, variant: str | None = None) -> list:
    """Items de graphics (y los embebidos en la tarjeta final como escena) de ese tipo."""
    fx = plan.get("fx") or {}
    out = [g for g in fx.get("graphics") or [] if g.get("kind") == kind and (variant is None or g.get("variant") == variant)]
    for c in fx.get("cutaways") or []:
        out += [g for g in c.get("graphics") or [] if g.get("kind") == kind and
                (variant is None or g.get("variant") == variant)]
    return out


def profile_check(api: "Api", job: str, out: Path, res: dict) -> None:
    """El perfil FICTICIO de /compose esta en la edicion (rotulo nombre + profesion, Seguir con la
    red principal y el @usuario, reservas con el enlace corto); PUT /profile lo cambia al instante
    (< 1 s) y con base_rev viejo responde 409; /frame del rotulo cambia al quitar el perfil."""
    C, T = res["checks"], res["timings"]
    got = api.get(f"/plan/{job}")
    plan, rev = got["plan"], got["plan_rev"]
    prof = plan.get("profile") or {}
    C["profile_in_plan"] = prof.get("name") == E2E_PROFILE["name"] and \
        (plan.get("entities") or {}).get("name_src") == "perfil"
    lts = _closing_items(plan, "lower_third")
    C["profile_lower_third"] = bool(lts) and lts[0].get("title") == E2E_PROFILE["name"] and \
        str(lts[0].get("subtitle") or "").startswith(E2E_PROFILE["title"])
    fol = _closing_items(plan, "cta", "follow")
    C["profile_follow"] = bool(fol) and fol[-1].get("platform") == "instagram" and \
        fol[-1].get("handle") == "@dra.lucia.demo" and fol[-1].get("title") == E2E_PROFILE["name"]
    pills = _closing_items(plan, "cta", "link_below")
    C["profile_booking"] = bool(pills) and pills[-1].get("display") == "paseo.club/dra-lucia-demo" and \
        pills[-1].get("label") == "Agenda en PaseoClub"
    res["profile"] = {"lower_third": [(g.get("title"), g.get("subtitle")) for g in lts],
                      "follow": [(g.get("title"), g.get("platform"), g.get("handle")) for g in fol],
                      "booking": [(g.get("label"), g.get("display")) for g in pills],
                      "notes": [n for n in (plan.get("director") or {}).get("notes") or [] if n.startswith("perfil")]}
    # /frame en el rotulo con y sin perfil
    from motion import timeline as TL
    tl = TL.Timeline.from_plan(plan, fps=30)
    tf = None
    if lts:
        a, b = tl.map_range(float(lts[0]["start"]), float(lts[0]["end"])) or (None, None)
        tf = round((a + b) / 2, 2) if a is not None else None
    other = dict(E2E_PROFILE, name="Dr. Tomás Ejemplo", links=[{"url": "https://tiktok.com/@dr.tomas.demo"}])
    t = time.time()
    r = api._req("PUT", f"/profile/{job}", json.dumps({"profile": other, "base_rev": rev}).encode(),
                 {"Content-Type": "application/json"})
    T["profile_put_s"] = round(time.time() - t, 3)
    q = r["plan"]
    lt2 = _closing_items(q, "lower_third")
    fol2 = _closing_items(q, "cta", "follow")
    C["profile_put_fast"] = T["profile_put_s"] < 1.0 and r["plan_rev"] == rev + 1 and \
        bool(lt2) and lt2[0].get("title") == "Dr. Tomás Ejemplo" and \
        (not fol2 or (fol2[-1].get("platform") == "tiktok" and fol2[-1].get("handle") == "@dr.tomas.demo"))
    code, _body, _h = _put_raw(api, f"/profile/{job}", {"profile": E2E_PROFILE, "base_rev": rev})
    C["profile_put_409"] = code == 409
    # sin perfil vs con perfil en el tiempo del rotulo
    none = api._req("PUT", f"/profile/{job}", json.dumps({"profile": None}).encode(), {"Content-Type": "application/json"})
    back = api._req("PUT", f"/profile/{job}", json.dumps({"profile": E2E_PROFILE}).encode(),
                    {"Content-Type": "application/json"})
    ok_frame = False
    if tf is not None:
        with_p, _ = api.post(f"/frame/{job}", {"plan": back["plan"], "t_final": tf, "fmt": "9:16", **ENGINE}, raw=True)
        without, _ = api.post(f"/frame/{job}", {"plan": none["plan"], "t_final": tf, "fmt": "9:16", **ENGINE}, raw=True)
        (out / f"profile_frame_{tf:.2f}s.jpg").write_bytes(with_p)
        (out / f"profile_frame_none_{tf:.2f}s.jpg").write_bytes(without)
        ch = changed_px(jpeg_rgb(with_p), jpeg_rgb(without))
        res["profile"]["frame_changed_px"] = round(ch, 4)
        ok_frame = ch > 0.01
    C["profile_frame_changes"] = ok_frame
    C["profile_restored"] = (back["plan"].get("profile") or {}).get("name") == E2E_PROFILE["name"]
    log(f"perfil: {res['profile']} PUT {T['profile_put_s']}s")


def _put_raw(api: "Api", path: str, obj: dict) -> tuple:
    """PUT que devuelve (codigo, cuerpo, None) sin lanzar en 4xx."""
    req = urllib.request.Request(api.base + path, data=json.dumps(obj).encode(), method="PUT",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read(), None
    except urllib.error.HTTPError as e:
        return e.code, e.read(), None


def run(args, res: dict) -> dict:
    api = Api(args.base)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    media = Path(args.media)
    movs = sorted(media.glob("IMG_*.MOV"))
    if len(movs) < 6:
        raise SystemExit(f"faltan medios: {len(movs)} IMG_*.MOV en {media}")
    movs = movs[:6]
    res.update({"base": args.base, "label": args.label, "load_start": load(), "timings": {},
                "checks": {}, "warnings": []})
    T = res["timings"]

    st = api.get("/setup")
    log(f"setup: {st.get('state')} llm={st.get('llm')} ({st.get('llm_model')}) musica={st['optional']['music']['state']}")

    # 1) fuentes
    t = time.time()
    src = api.upload("/sources", movs)["sources"]
    T["upload_s"] = round(time.time() - t, 1)
    assert len(src) == 6, src
    for s in src:
        assert s["h"] > s["w"], f"fuente no vertical: {s}"
    log(f"subidos 6 clips en {T['upload_s']}s: {[round(s['dur'], 2) for s in src]}")

    # 2) compose + analisis + director
    main, acc = [], 0.0
    for s in src:
        main.append({"sid": s["id"], "type": "video", "in": 0, "out": s["dur"], "start": round(acc, 3)})
        acc += s["dur"]
    t = time.time()
    job = api.post("/compose", {"main": main, "highlights": 2, "glossary": "",
                                "director": {"style": args.style, "intensity": 0.75, "seed": 7,
                                             "music": args.music},
                                "engine": ENGINE, "profile": E2E_PROFILE})["job_id"]
    res["job"] = job
    stages: dict = {}
    trace: list = []
    wait(api, job, lambda s: s.get("phase") == "review", "analisis", 1800, stages, poll=0.5,
         trace=trace)
    T["compose_to_review_s"] = round(time.time() - t, 1)
    T["analysis_stages_first_seen_s"] = stages
    res["load_review"] = load()
    # barra: nunca retrocede y el montaje avanza (antes: fija en 4 % todo el compose)
    pcts = [float(p) for _t, _s, p, _e in trace if p is not None]
    back = [(a, b) for a, b in zip(pcts, pcts[1:]) if b < a - 0.05]
    comp = [float(p) for _t, s_, p, _e in trace if s_ == "compose" and p is not None]
    res["progress"] = {"n": len(trace), "backwards": back[:5], "compose_max": max(comp) if comp else None,
                       "etas": sum(1 for *_x, e in trace if e is not None),
                       "trace_sample": trace[:: max(1, len(trace) // 40)]}
    res["checks"]["progress_monotonic"] = len(pcts) >= 5 and not back
    res["checks"]["progress_compose_moves"] = bool(comp) and max(comp) >= 10.0
    got = api.get(f"/plan/{job}")
    plan = got["plan"]
    (out / "plan_v2.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    fx = plan.get("fx") or {}
    counts = {k: len(fx.get(k) or []) for k in ("text", "graphics", "cutaways", "effects", "transitions", "sfx")}
    res["plan"] = {"version": plan.get("version"), "shots": len(plan.get("shots") or []),
                   "fx": counts, "director": {k: (plan.get("director") or {}).get(k)
                                              for k in ("style", "llm", "vlm", "timings")},
                   "music": {k: (fx.get("music") or {}).get(k) for k in ("source", "enabled", "prompt")},
                   "plan_rev": got["plan_rev"]}
    st = api.get(f"/status/{job}?plan=0")
    res["review_warnings"] = st.get("warnings")
    dd = plan.get("director") or {}
    res["director_timings"] = dd.get("timings")
    res["director_notes"] = dd.get("notes")
    if dd.get("vlm") == "heuristico":
        res["warnings"].append("director: el VLM cayo a heuristicas (tomas sin descripcion de la IA)")
    log(f"review en {T['compose_to_review_s']}s: shots={res['plan']['shots']} fx={counts} "
        f"llm={res['plan']['director']['llm']} vlm={dd.get('vlm')} timings={dd.get('timings')} "
        f"warnings={st.get('warnings')}")
    ok_plan = plan.get("version") == 2 and len(plan.get("shots") or []) == 6 and \
        counts["text"] > 0 and counts["graphics"] > 0 and counts["transitions"] > 0
    res["checks"]["plan_v2_shots6_fx"] = ok_plan
    # limites de toma = limites reales de los clips
    bounds = [round(s["start"], 2) for s in plan["shots"]]
    exp, acc = [], 0.0
    for s in src:
        exp.append(round(acc, 2))
        acc += s["dur"]
    res["plan"]["shot_starts"] = bounds
    res["checks"]["shots_match_clips"] = len(bounds) == 6 and max(abs(a - b) for a, b in zip(bounds, exp)) < 0.1

    api_checks(api, job, plan, src, out, res)
    # logo de marca (sintetico) para validar las opciones del proyecto en el render pro
    api.upload_logo(make_logo(out / "logo_prueba.png"))

    tl, over, busy = plan_windows(plan)
    D = tl.n_frames(30) / 30.0
    res["expected_duration"] = round(D, 3)
    # cuanto de cada toma (clip) queda en el final: una toma eliminada completa se marca
    kept = []
    for i, s in enumerate(plan["shots"]):
        r = tl.map_range(float(s["start"]), float(s["end"]))
        kept.append({"shot": i + 1, "src": s.get("src"), "src_s": round(float(s["end"]) - float(s["start"]), 2),
                     "final_s": round((r[1] - r[0]) if r else 0.0, 2)})
    res["shots_kept"] = kept
    gone = [k for k in kept if k["final_s"] < min(0.8, 0.25 * k["src_s"])]
    if gone:
        res["warnings"].append(f"tomas eliminadas (o casi) por los recortes de la IA: "
                               f"{[(k['shot'], k['src'], k['final_s']) for k in gone]}")

    # 3) /frame en 3 tiempos (el analisis de fondo puede estar corriendo)
    frames = []
    for k, frac in enumerate((0.15, 0.5, 0.85)):
        tf = round(D * frac, 3)
        t = time.time()
        data, hdr = api.post(f"/frame/{job}", {"plan": plan, "t_final": tf, "fmt": "9:16",
                                               "quality": "final", **ENGINE}, raw=True)
        dt = round(time.time() - t, 2)
        img = jpeg_rgb(data)
        (out / f"frame_{k + 1}_{tf:.2f}s.jpg").write_bytes(data)
        frames.append({"t_final": tf, "s": dt, "server_ms": int(hdr.get("X-Frame-Ms") or hdr.get("x-frame-ms") or 0),
                       "shape": list(img.shape)})
        log(f"/frame t={tf}s -> {img.shape[1]}x{img.shape[0]} en {dt}s")
    T["frames"] = frames
    res["checks"]["frame_1080x1920"] = all(f["shape"][:2] == [1920, 1080] for f in frames)
    st = api.get(f"/status/{job}?plan=0")
    res["analysis_at_frames"] = {k: st.get(k) for k in ("analysis_state", "analysis_pct", "analysis_stage")}

    # 4) vista previa real (render_draft con audio)
    t = time.time()
    api.post(f"/preview/{job}", {"plan": plan, "pro": True, "fmt": "9:16", **ENGINE})
    pst: dict = {}
    st, _ = wait(api, job, lambda s: s.get("preview_state") in ("done", "error", "canceled"),
                 "preview", 1800, pst)
    res["preview_music"] = {"music_pending": st.get("preview_music_pending"),
                            "refresh": (st.get("preview") or {}).get("music_refresh"),
                            "first_file": st.get("preview_file")}
    T["preview_s"] = round(time.time() - t, 1)
    T["preview_stages_first_seen_s"] = pst
    if st.get("preview_state") != "done":
        raise RuntimeError(f"preview: {st.get('preview_state')} {st.get('preview_error')}")
    pv = api.download(f"/video/{st['preview_file']}", out / "preview_draft.mp4")
    pi = ffprobe(pv)
    vs = [s for s in pi["streams"] if s["codec_type"] == "video"][0]
    res["preview"] = {"file": pv.name, "W": vs["width"], "H": vs["height"],
                      "duration": round(float(pi["format"]["duration"]), 3),
                      "audio": any(s["codec_type"] == "audio" for s in pi["streams"])}
    res["checks"]["preview_540x960_audio"] = (vs["width"], vs["height"]) == (540, 960) and res["preview"]["audio"]
    log(f"preview en {T['preview_s']}s: {res['preview']} musica={res['preview_music']}")
    if st.get("preview_music_pending"):
        # sono con la procedural mientras la IA se generaba: el servidor la rehace solo
        t = time.time()
        st2, _ = wait(api, job, lambda s: (s.get("preview") or {}).get("music_refresh") in
                      ("done", "error", "canceled"), "preview (musica IA)", 900)
        pv2 = st2.get("preview") or {}
        res["preview_music"].update(refresh=pv2.get("music_refresh"), file=pv2.get("file"),
                                    wait_s=round(time.time() - t, 1),
                                    music_pending_after=pv2.get("music_pending"))
        res["checks"]["preview_music_refreshed"] = pv2.get("music_refresh") == "done" and \
            pv2.get("file") != res["preview_music"]["first_file"] and pv2.get("music_pending") is False
        res["checks"]["preview_provisional_kept"] = provisional_ok(api, pv2, res["preview_music"]["first_file"])
    st = api.get(f"/status/{job}?plan=0")
    res["analysis_after_preview"] = {k: st.get(k) for k in ("analysis_state", "analysis_pct", "analysis_t")}
    # 4b) vista previa con una musica IA NUEVA (otra semilla: ACE tiene que generarla): la vista
    # previa sale con la procedural y el servidor la rehace sola cuando la pista IA esta lista
    mus = (plan.get("fx") or {}).get("music") or {}
    if mus.get("source", "ai") == "ai" and mus.get("enabled", True) and \
            (api.get("/setup").get("optional") or {}).get("music", {}).get("state") == "ready":
        p2 = copy.deepcopy(plan)
        p2["fx"]["music"] = dict(mus, seed=int(time.time()) % 100000 + 17)
        t = time.time()
        api.post(f"/preview/{job}", {"plan": p2, "pro": True, "fmt": "9:16", **ENGINE})
        st, _ = wait(api, job, lambda s: s.get("preview_state") in ("done", "error", "canceled"),
                     "preview (musica nueva)", 1800)
        first = {"t": round(time.time() - t, 1), "file": st.get("preview_file"),
                 "music_pending": st.get("preview_music_pending"),
                 "refresh": (st.get("preview") or {}).get("music_refresh")}
        res["preview_new_music"] = first
        if st.get("preview_music_pending"):
            st2, _ = wait(api, job, lambda s: (s.get("preview") or {}).get("music_refresh") in
                          ("done", "error", "canceled", "timeout"), "preview (musica IA)", 900)
            pv2 = st2.get("preview") or {}
            res["preview_new_music"].update(total_s=round(time.time() - t, 1), refresh=pv2.get("music_refresh"),
                                            file2=pv2.get("file"), music_pending_after=pv2.get("music_pending"),
                                            music_wait_s=pv2.get("music_wait_s"), draft2_t=pv2.get("t"))
            res["checks"]["preview_music_refreshed"] = pv2.get("music_refresh") == "done" and \
                pv2.get("file") != first["file"] and pv2.get("music_pending") is False
            res["checks"]["preview_provisional_kept"] = provisional_ok(api, pv2, first["file"])
        log(f"preview con musica nueva: {res['preview_new_music']}")

    # 5) render final con shorts
    t = time.time()
    api.post(f"/render/{job}", {"plan": plan, "fmt": "9:16", "shorts": True, **ENGINE})
    rst: dict = {}
    st, _ = wait(api, job, lambda s: s.get("phase") == "done", "render", 3600, rst)
    T["render_s"] = round(time.time() - t, 1)
    T["render_stages_first_seen_s"] = rst
    res["load_render_end"] = load()
    clips = st["clips"]
    res["render_stats"] = clips[0].get("stats")
    res["render_warnings"] = st.get("warnings")
    final = api.download(f"/video/{clips[0]['file']}", out / "final.mp4")
    shorts = [api.download(f"/video/{c['file']}", out / f"short_{i}.mp4") for i, c in enumerate(clips[1:], 1)]
    log(f"render en {T['render_s']}s: {clips[0]['file']} + {len(shorts)} shorts")

    # 6) validaciones del export
    info = ffprobe(final)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    dur = float(info["format"]["duration"])
    vdur = float(v.get("duration") or dur)
    res["final"] = {"W": v["width"], "H": v["height"], "codec": v["codec_name"], "duration": round(dur, 3),
                    "video_duration": round(vdur, 3), "audio": bool(a),
                    "color": [v.get("color_primaries"), v.get("color_transfer"), v.get("color_space")],
                    "bit_rate": int(info["format"].get("bit_rate") or 0),
                    "rotation": v.get("side_data_list")}
    res["checks"]["final_1080x1920"] = (v["width"], v["height"]) == (1080, 1920)
    res["checks"]["final_duration"] = abs(vdur - D) <= 1.5 / 30 and D > 10
    loud = loudness(final)
    res["final"].update(loud)
    res["checks"]["loudness_-14"] = loud["lufs"] is not None and abs(loud["lufs"] + 14.0) <= 1.0 and \
        loud["true_peak"] is not None and loud["true_peak"] <= -1.0
    rs = res["render_stats"] or {}
    res["checks"]["render_no_skipped"] = rs.get("skipped") == []
    res["checks"]["music_in_mix"] = (rs.get("music") or {}).get("source") in ("ai", "procedural")
    # opciones del proyecto: el logo sale en la esquina; el aire no aplica a una fuente vertical
    # (no cambia el base: el export reutiliza el del analisis de fondo)
    lg = [logo_score(frame_at(final, tf)) for tf in (D * 0.2, D * 0.5, D * 0.8)]
    res["logo_scores"] = [round(x, 3) for x in lg]
    res["checks"]["logo_in_export"] = min(lg) > 0.08
    res["engine_stats"] = rs.get("engine")
    res["checks"]["air_ignored_vertical_reuses_base"] = (rs.get("engine") or {}).get("air") is None \
        and bool(rs.get("cached_base"))
    t = time.time()
    mus = api.post(f"/music/{job}", {"plan": plan, "fmt": "9:16"}, timeout=900)
    T["music_after_render_s"] = round(time.time() - t, 2)
    res["music_api"] = {k: mus.get(k) for k in ("source", "bpm", "cached", "duration")}
    res["music_api"]["beats"] = len(mus.get("beats") or [])
    res["music_api"]["downbeats"] = len(mus.get("downbeats") or [])
    res["checks"]["music_beats"] = len(mus.get("beats") or []) >= 8 and \
        all(0 <= b <= D + 0.5 for b in mus.get("beats") or [])
    res["shorts"] = []
    for sp in shorts:
        si = ffprobe(sp)
        sv = [s for s in si["streams"] if s["codec_type"] == "video"][0]
        res["shorts"].append({"file": sp.name, "W": sv["width"], "H": sv["height"],
                              "duration": round(float(si["format"]["duration"]), 2)})
    res["checks"]["shorts"] = len(shorts) >= 1 and all((s["W"], s["H"]) == (1080, 1920) for s in res["shorts"])

    # orientacion: tiempos limpios (sin escenas/camara/transiciones), cara y cuerpo vs la fuente
    res.update(orientation_checks(final, plan, tl, busy, D, res["checks"]))

    # overlays: cuadro exacto con overlays vs sin overlays (mismo tiempo), y export vs /frame
    bare = copy.deepcopy(plan)
    bfx = bare["fx"]
    for k in ("text", "graphics", "cutaways", "transitions"):
        bfx[k] = []
    bfx["captions"] = dict(bfx.get("captions") or {}, enabled=False)
    ov_t = sorted({round((a0 + b0) / 2, 2) for a0, b0 in over if b0 - a0 > 0.5})
    ov_t = ov_t[:: max(1, len(ov_t) // 5)][:5]
    ovs = []
    for tf in ov_t:
        full, _ = api.post(f"/frame/{job}", {"plan": plan, "t_final": tf, "fmt": "9:16", **ENGINE}, raw=True)
        nof, _ = api.post(f"/frame/{job}", {"plan": bare, "t_final": tf, "fmt": "9:16", **ENGINE}, raw=True)
        fa, fb = jpeg_rgb(full).astype(np.int16), jpeg_rgb(nof).astype(np.int16)
        d = np.abs(fa - fb).max(axis=2)
        # cuadro n = floor(tf*30) del export (el que muestra /frame): ffmpeg -ss entrega el primer
        # cuadro con pts >= t, asi que se busca un poco ANTES de n/30 (con + 1/60 salia el n+1)
        ex = frame_at(final, math.floor(tf * 30 + 1e-6) / 30 - 1 / 120).astype(np.int16)
        ovs.append({"t_final": tf, "diff_mean": round(float(d.mean()), 2),
                    "px_changed": round(float((d > 40).mean()), 4),
                    "export_vs_frame_mean": round(float(np.abs(ex - fa).mean()), 2)})
        (out / f"overlay_{tf:.2f}s.jpg").write_bytes(full)
    res["overlays"] = ovs
    res["checks"]["overlays_present"] = len(ovs) >= 3 and \
        sum(o["px_changed"] > 0.003 for o in ovs) >= max(3, int(0.8 * len(ovs)))
    res["checks"]["frame_matches_export"] = len(ovs) > 0 and \
        float(np.median([o["export_vs_frame_mean"] for o in ovs])) < 8.0

    contact_sheet(final, out / "final_contact.jpg")

    # 7) cambio de estilo con la musica IA (una generacion, sincronia automatica)
    if (plan.get("fx") or {}).get("music", {}).get("source", "ai") == "ai" and \
            (api.get("/setup").get("optional") or {}).get("music", {}).get("state") == "ready":
        redirect_music_check(api, job, plan, res)
    # 8) fuente HORIZONTAL: el aire es opt-in (el default va a sangre completa)
    if os.environ.get("REELFY_E2E_HORIZONTAL", "1") != "0":
        horizontal_check(api, out, res)
    # 9) perfil de la persona: integrado desde /compose y editable al instante
    profile_check(api, job, out, res)
    res["ok"] = all(res["checks"].values())
    res["load_end"] = load()
    res["total_s"] = round(time.time() - T0, 1)
    log(f"checks: {json.dumps(res['checks'])}")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8013")
    ap.add_argument("--media", default=os.environ.get("REELFY_TEST_MEDIA") or str(SPIKE / "work" / "media"))
    ap.add_argument("--out", default=str(SPIKE / "work" / "e2e"))
    ap.add_argument("--style", default="dinamico")
    ap.add_argument("--music", default="ai")
    ap.add_argument("--label", default="", help="frio | caliente (solo para el reporte)")
    args = ap.parse_args()
    res: dict = {}
    try:
        run(args, res)
    except Exception as e:  # noqa
        import traceback
        traceback.print_exc()
        res.update(ok=False, error=str(e))
    finally:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "e2e_result.json").write_text(json.dumps(res, indent=1, default=str))
        (Path(args.out) / "e2e_log.txt").write_text("\n".join(LOG))
    print(json.dumps({"ok": res.get("ok"), "label": res.get("label"), "checks": res.get("checks"),
                      "warnings": res.get("warnings"), "timings": res.get("timings"),
                      "director_timings": res.get("director_timings")}, indent=1, default=str))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
