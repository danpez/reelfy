"""linkmedia: foto (og:image) y logo (icono del sitio) de los enlaces del Perfil. Todo con un
servidor HTTP LOCAL y datos sinteticos (sin internet)."""
import http.server
import json
import threading

import cv2
import numpy as np
import pytest

from motion import linkmedia as LM


@pytest.fixture(autouse=True)
def _red_local(monkeypatch):
    """El servidor de prueba vive en 127.0.0.1: se permite la red local SOLO aqui."""
    monkeypatch.setattr(LM, "ALLOW_PRIVATE", True)

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
       b'<rect width="100" height="100" rx="20" fill="#f5efeb"/>'
       b'<circle cx="50" cy="50" r="30" fill="none" stroke="#08794d" stroke-width="10"/></svg>')


def _png(w, h, color=(20, 120, 80)):
    a = np.zeros((h, w, 3), np.uint8)
    a[:] = color[::-1]
    return cv2.imencode(".png", a)[1].tobytes()


PAGE = """<html><head>
<meta property="og:title" content="Negocio de prueba"/>
<meta property="og:image" content="/img/foto.png?v=1&amp;w=1200"/>
<link rel="icon" href="/icon.svg" type="image/svg+xml" sizes="any"/>
<link rel="apple-touch-icon" href="/apple.png" sizes="180x180"/>
<link rel="icon" href="/fav16.png" sizes="16x16"/>
</head><body>hola</body></html>"""

IG_PAGE = """<html><head>
<meta property="og:image" content="/pp/perfil.jpg" />
<link rel="icon" sizes="192x192" href="/rsrc.php/icon.webp" />
</head></html>"""

WALL = """<html><head><meta property="og:image" content="https://static.cdninstagram.com/rsrc.php/logo.png"/></head></html>"""


@pytest.fixture(scope="module")
def server():
    files = {
        "/negocio": (PAGE.encode(), "text/html; charset=utf-8"),
        "/img/foto.png": (_png(400, 400, (200, 50, 50)), "image/png"),
        "/icon.svg": (SVG, "image/svg+xml"),
        "/apple.png": (_png(180, 180), "image/png"),
        "/fav16.png": (_png(16, 16), "image/png"),
        "/ig/usuario/": (IG_PAGE.encode(), "text/html"),
        "/pp/perfil.jpg": (cv2.imencode(".jpg", np.full((100, 100, 3), 90, np.uint8))[1].tobytes(),
                           "image/jpeg"),
        "/muro": (WALL.encode(), "text/html"),
        "/noimg": (b"<html><head><title>x</title></head></html>", "text/html"),
        "/html_como_img": (b"<html>no soy imagen</html>", "text/html"),
    }
    hits = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            hits[path] = hits.get(path, 0) + 1
            if path not in files:
                self.send_response(404)
                self.end_headers()
                return
            body, ct = files[path]
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", hits
    srv.shutdown()


def test_page_media_orden_y_urls_absolutas():
    c = LM.page_media(PAGE, "https://ejemplo.test/negocio")
    assert c["photo"] == ["https://ejemplo.test/img/foto.png?v=1&w=1200"]
    # svg primero, luego apple-touch-icon; el favicon de 16 px no sirve de logo
    assert c["logo"] == ["https://ejemplo.test/icon.svg", "https://ejemplo.test/apple.png"]


def test_page_media_descarta_imagen_generica_del_muro_de_sesion():
    assert LM.page_media(WALL, "https://www.instagram.com/x/")["photo"] == []


def test_fetch_foto_y_logo_svg(server, tmp_path):
    base, _hits = server
    m = LM.fetch_link_media(f"{base}/negocio", "paseoclub", tmp_path)
    ph, lg = m["photo"], m["logo"]
    assert ph and ph["w"] == 400 and ph["h"] == 400 and ph["type"] == "image"
    assert (tmp_path / f"{ph['sid']}.png").exists() and (tmp_path / f"{ph['sid']}_t.jpg").exists()
    meta = json.loads((tmp_path / f"{ph['sid']}_meta.json").read_text())
    assert meta["w"] == 400 and meta["src"].endswith("/img/foto.png?v=1&w=1200")
    # logo: el SVG rasterizado a 512 con su color real (no negro, no transparente)
    assert lg and lg["w"] == LM.LOGO_PX
    img = cv2.imread(str(tmp_path / f"{lg['sid']}.png"), cv2.IMREAD_UNCHANGED)
    b, g, r, a = img[256, 256 - 30 * 512 // 100]            # sobre el aro verde
    assert a == 255 and g > r and g > b
    b, g, r, a = img[20, 256]                               # recuadro crema
    assert a == 255 and r > 230 and g > 225


def test_red_con_icono_propio_no_descarga_logo_y_usa_la_foto(server, tmp_path):
    base, hits = server
    m = LM.fetch_link_media(f"{base}/ig/usuario/", "instagram", tmp_path)
    assert m["photo"] and m["photo"]["w"] == 100
    assert m["logo"] is None and "/rsrc.php/icon.webp" not in hits


def test_cache_no_vuelve_a_descargar(server, tmp_path):
    base, hits = server
    LM.fetch_link_media(f"{base}/negocio", "paseoclub", tmp_path)
    n = hits.get("/img/foto.png", 0)
    m = LM.fetch_link_media(f"{base}/negocio", "paseoclub", tmp_path)
    assert m["photo"] and hits.get("/img/foto.png", 0) == n


@pytest.mark.parametrize("path", ["/muro", "/noimg", "/no-existe"])
def test_sin_imagen_o_bloqueado_devuelve_none(server, tmp_path, path):
    base, _ = server
    m = LM.fetch_link_media(f"{base}{path}", "web", tmp_path)
    assert m == {"photo": None, "logo": None}


def test_sin_red_nunca_lanza(tmp_path):
    def boom(*a, **k):
        raise OSError("sin red")
    assert LM.fetch_link_media("https://x.test/", "web", tmp_path, fetch=boom) == \
        {"photo": None, "logo": None}


def test_solo_http(tmp_path):
    assert LM.fetch_link_media("file:///etc/passwd", "web", tmp_path) == {"photo": None, "logo": None}


def test_contenido_que_no_es_imagen_se_ignora(server, tmp_path):
    base, _ = server
    html = f'<meta property="og:image" content="{base}/html_como_img"/>'

    def fetch(url, limit, timeout):
        if url.endswith("/pagina"):
            return html.encode(), "text/html"
        return LM._http_get(url, limit, timeout)
    m = LM.fetch_link_media("https://x.test/pagina", "web", tmp_path, fetch=fetch)
    assert m["photo"] is None


def test_hosts_no_publicos_se_rechazan(monkeypatch, tmp_path):
    monkeypatch.setattr(LM, "ALLOW_PRIVATE", False)
    for u in ("http://127.0.0.1:9/x", "http://localhost/x", "http://192.168.1.10/x", "http://169.254.169.254/x",
              "http://10.0.0.5/logo.png", "http://[::1]/x"):
        with pytest.raises(ValueError):
            LM._check_url(u)
    assert LM.fetch_link_media("http://127.0.0.1:9/pagina", "web", tmp_path) == {"photo": None, "logo": None}


def test_cada_imagen_tambien_pasa_el_filtro_de_host(server, monkeypatch, tmp_path):
    base, _ = server
    calls = []
    real = LM._check_url

    def check(u):
        calls.append(u)
        if "/negocio" not in u:              # la pagina se permite; cualquier otro destino no
            raise ValueError("host no publico")
        return None
    monkeypatch.setattr(LM, "_check_url", check)
    m = LM.fetch_link_media(f"{base}/negocio", "paseoclub", tmp_path)
    assert m == {"photo": None, "logo": None} and any("/img/foto.png" in c for c in calls)
    monkeypatch.setattr(LM, "_check_url", real)
