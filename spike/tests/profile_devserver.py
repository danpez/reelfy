"""Servidor de Reelfy AISLADO con red FICTICIA para los E2E del perfil (tests/js/profile_e2e.mjs y la
prueba en WebKit tests/webkit/profile_check.swift).

  uso: .venv/bin/python tests/profile_devserver.py <puerto> <carpeta_datos>

Como tests/devserver.sh (DATA propio, sin licencia) pero la foto y el logo de los enlaces del perfil
(motion/linkmedia.py) se bajan de un servidor HTTP LOCAL en vez de internet:
  - https://instagram.com/<usuario>        -> pagina con la foto de perfil /pp/<usuario>.png: un PNG
    LISO de un color que sale del usuario (fake_color): otra cuenta = otro color, asi la prueba sabe
    de que cuenta es la foto que se ve.
  - https://paseo.club/business/<negocio>  -> pagina con su vista previa (color del negocio) y logo.
  - cualquier otra url falla como sin red.
GET /__fake/hits -> {ruta: veces} (cuantas veces se pidio cada pagina) y POST /__fake/reset la limpia.
Sin IA de lenguaje (REELFY_OLLAMA_URL a un puerto cerrado): el texto pegado se entiende por reglas.
Solo datos ficticios.
"""
import hashlib
import http.server
import json
import os
import sys
import threading
from pathlib import Path

SPIKE = Path(__file__).resolve().parent.parent


def fake_color(name: str) -> tuple[int, int, int]:
    """Color (r, g, b) estable y saturado de una cuenta / negocio ficticio (lo mismo en la prueba JS)."""
    h = hashlib.sha1(name.lower().encode()).digest()
    k = h[0] % 6
    base = [(220, 40, 40), (40, 170, 70), (40, 80, 220), (230, 160, 20), (150, 50, 200), (20, 170, 190)][k]
    return base


DEMO = "pf_demo"
# el guion del proyecto sintetico: dice su nombre, invita a agendar y cierra (rotulo, Seguir, reservas,
# tarjeta final)
LINES = ["Hola soy la doctora Ana Demo y hoy te cuento algo de tus dientes.",
         "Si te duele la muela cuando tomas algo frio, esto te interesa.",
         "Una resina bien hecha protege tu diente y se ve natural.",
         "Checa mis horarios disponibles en el link aqui abajo.",
         "Y no te preocupes, no te voy a juzgar por tu cepillo viejo."]


def seed_demo(data: Path) -> None:
    """Proyecto SINTETICO `pf_demo` (video 540x960 de dos tomas con audio + plan dirigido SIN perfil, en
    revision) si no existe. Solo datos ficticios."""
    import subprocess
    import paths
    import jobstore
    from motion import director as D
    sys.path.insert(0, str(SPIKE / "tests"))
    import test_profile_director as TP
    jobstore.init()
    if jobstore.get_light(DEMO):
        return
    p = TP._lines_plan(LINES, broll_end=2.6)
    p["dims"] = [540, 960]
    vid = data / "input" / f"{DEMO}.mp4"
    vid.parent.mkdir(parents=True, exist_ok=True)
    dur = float(p["duration"])
    flt = ("[0:v]drawbox=x=0:y=480:w=540:h=480:color=0xd0d0d0:t=fill:enable='lt(t,6)',"
           "drawbox=x=0:y=480:w=540:h=480:color=0x303030:t=fill:enable='gte(t,6)'[v]")
    subprocess.run([str(paths.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x5a6272:size=540x960:rate=30:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={dur}", "-filter_complex", flt,
                    "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "48k", "-shortest", "-movflags", "+faststart", str(vid)], check=True, timeout=300)
    p["video"] = str(vid)
    plan = D.direct(p, use_llm=False)
    jobstore.create(DEMO, kind="analyze", ext=".mp4", plan=plan, phase="review", message="Análisis listo")
    jobstore.update(DEMO, state="review", pct=100)


def main() -> None:
    port, data = int(sys.argv[1]), Path(sys.argv[2]).resolve()
    data.mkdir(parents=True, exist_ok=True)
    os.environ.update({"REELFY_PORT": str(port), "REELFY_DATA": str(data), "REELFY_GATED": "0",
                       "REELFY_LINK_MEDIA": "1"})
    os.environ.setdefault("REELFY_OLLAMA_URL", "http://127.0.0.1:9")
    sys.path[:0] = [str(SPIKE / "app"), str(SPIKE / "scripts")]
    os.chdir(SPIKE)
    if "--demo" in sys.argv:
        seed_demo(data)
    import cv2
    import numpy as np

    def png(w, h, rgb):
        a = np.zeros((h, w, 3), np.uint8)
        a[:] = rgb[::-1]
        return cv2.imencode(".png", a)[1].tobytes()

    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" '
           b'rx="20" fill="#f5efeb"/><circle cx="50" cy="50" r="30" fill="none" stroke="#08794d" '
           b'stroke-width="10"/></svg>')
    hits: dict[str, int] = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            hits[path] = hits.get(path, 0) + 1
            parts = [p for p in path.split("/") if p]
            body, ct = None, "text/html"
            if parts[:1] == ["ig"] and len(parts) == 2:
                body = (f'<html><head><meta property="og:image" content="https://instagram.com/pp/{parts[1]}.png"/>'
                        f"</head><body>cuenta ficticia</body></html>").encode()
            elif parts[:2] == ["ig", "pp"] and len(parts) == 3:
                body, ct = png(160, 160, fake_color(parts[2].rsplit(".", 1)[0])), "image/png"
            elif parts[:2] == ["pc", "business"] and len(parts) == 3:
                body = (f'<html><head><meta property="og:image" content="/img/{parts[2]}.png"/>'
                        f'<link rel="icon" href="/icon.svg" type="image/svg+xml"/></head><body>negocio ficticio</body></html>').encode()
            elif parts[:2] == ["pc", "img"] and len(parts) == 3:
                body, ct = png(400, 300, fake_color(parts[2].rsplit(".", 1)[0])), "image/png"
            elif path == "/pc/icon.svg":
                body, ct = svg, "image/svg+xml"
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    net = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=net.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{net.server_address[1]}"
    routes = {"https://instagram.com/": base + "/ig/", "https://www.instagram.com/": base + "/ig/",
              "https://paseo.club/": base + "/pc/"}

    import server as S
    from motion import linkmedia as LM
    LM.ALLOW_PRIVATE = True          # la "red" ficticia vive en 127.0.0.1
    real = LM._http_get

    def local_get(u, limit, timeout):
        for a, b in routes.items():
            if u.startswith(a):
                u = b + u[len(a):]
        if not u.startswith(base):
            raise OSError("sin red (prueba)")
        return real(u, limit, timeout)

    S._lm_http = lambda: local_get

    from fastapi import Body

    @S.app.get("/__fake/hits")
    def _fake_hits():
        return {k: v for k, v in hits.items() if k.count("/") <= 3}

    @S.app.post("/__fake/reset")
    def _fake_reset(body: dict = Body(default={})):
        hits.clear()
        return {"ok": True}

    # las rutas de prueba antes del catch-all del Studio (si lo hay)
    r = S.app.router.routes
    mine = [x for x in r if getattr(x, "path", "").startswith("/__fake/")]
    for x in mine:
        r.remove(x)
    r[0:0] = mine

    import uvicorn
    S._SERVING = True
    print(json.dumps({"port": port, "data": str(data), "net": base}), flush=True)
    uvicorn.run(S.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
