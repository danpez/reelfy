"""Foto y logo de los enlaces del Perfil (la misma vista previa de enlace de WhatsApp o iMessage).

Si el perfil menciona su pagina de reservas, su Instagram u otra pagina, Reelfy toma la foto de
ESE lugar sin que el usuario la suba:
  - foto: la imagen de vista previa publica de la pagina (og:image / twitter:image). En Instagram
    es la foto de perfil (100x100: lo unico publico sin sesion; no se usan APIs privadas).
  - logo: el icono del sitio (icon SVG > apple-touch-icon > el icon PNG mas grande). Las redes que
    Reelfy ya dibuja con icono propio (instagram, tiktok, youtube...) no descargan logo.

Es la UNICA red que usa esta funcion (el procesamiento sigue siendo local) y es opcional: sin red,
pagina bloqueada o sin imagen -> None, nunca lanza. Solo http(s), tamanos acotados, content-type de
imagen, y cada imagen se DECODIFICA y se vuelve a codificar a PNG (nunca se guarda el archivo tal
cual). El SVG se rasteriza con skia a 512 px.

Se guardan como medios propios (mismo formato que POST /sources: <sid>.png, <sid>_meta.json,
<sid>_t.jpg) con sid = sha1(url de la imagen)[:12]: el mismo perfil no duplica archivos y un
segundo intento no vuelve a descargar.

API:
  page_media(html, base_url) -> {"photo": [urls], "logo": [urls]}   (puro, para tests)
  fetch_link_media(url, platform, dest_dir, timeout=8.0, fetch=None) -> {"photo": media|None,
      "logo": media|None}   con media = {sid, type:'image', name, w, h, src}
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
      "Version/17.0 Safari/605.1.15 Reelfy-link-preview")
MAX_HTML = 3 << 20
MAX_IMG = 8 << 20
LOGO_PX = 512
# redes con icono propio en graphics (platform_icon / insignias): su logo no se descarga
OWN_ICON = {"instagram", "tiktok", "youtube", "facebook", "whatsapp", "x", "twitter", "threads",
            "linkedin", "telegram", "pinterest", "snapchat", "spotify", "email", "phone"}
# imagenes genericas del sitio que NO son la foto del perfil (muro de sesion, logo del sitio)
_GENERIC = re.compile(r"static\.cdninstagram\.com/rsrc|/rsrc\.php/|default[_-]?avatar|"
                      r"placeholder|/favicon", re.I)


class _Head(HTMLParser):
    """Lee <meta property/name=... content=...> y <link rel=... href=... sizes type>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: list[tuple[str, str]] = []
        self.links: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content"):
                self.meta.append((key, a["content"].strip()))
        elif tag == "link" and a.get("href"):
            self.links.append(a)


def _size_of(link: dict) -> int:
    m = re.match(r"(\d+)x(\d+)", (link.get("sizes") or "").strip())
    return int(m.group(1)) if m else 0


def page_media(html: str, base_url: str) -> dict:
    """Candidatos (en orden de preferencia) de foto y logo de una pagina. Puro."""
    p = _Head()
    try:
        p.feed(html[:MAX_HTML])
    except Exception:  # noqa: html roto -> lo que se alcanzo a leer
        pass
    photo = []
    for key in ("og:image:secure_url", "og:image", "og:image:url", "twitter:image",
                "twitter:image:src", "image"):
        for k, v in p.meta:
            if k == key:
                u = urljoin(base_url, _html.unescape(v))
                if u not in photo and not _GENERIC.search(u):
                    photo.append(u)
    svg, apple, icons = [], [], []
    for ln in p.links:
        rel = (ln.get("rel") or "").lower().split()
        href = urljoin(base_url, _html.unescape(ln["href"]))
        is_svg = (ln.get("type") or "").lower() == "image/svg+xml" or \
            urlparse(href).path.lower().endswith(".svg")
        if "icon" in rel and is_svg:
            svg.append(href)
        elif "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            apple.append((_size_of(ln), href))
        elif "icon" in rel:
            icons.append((_size_of(ln), href))
    logo = svg + [h for _s, h in sorted(apple, key=lambda t: -t[0])] + \
        [h for s, h in sorted(icons, key=lambda t: -t[0]) if s >= 96 or s == 0]
    return {"photo": photo, "logo": list(dict.fromkeys(logo))}


ALLOW_PRIVATE = False     # las pruebas con servidor local (127.0.0.1) lo activan


def _check_url(url: str) -> None:
    """Solo http(s) hacia hosts PUBLICOS: una pagina no puede hacer que Reelfy pida algo a la red
    local (og:image o redireccion a 192.168.x, localhost, 169.254.x...)."""
    import ipaddress
    import socket
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("solo http(s)")
    if ALLOW_PRIVATE:
        return
    for info in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80)):
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError("host no publico")


def _http_get(url: str, limit: int, timeout: float) -> tuple[bytes, str]:
    """GET acotado -> (cuerpo, content-type). Lanza si falla, excede `limit` o apunta (tambien por
    redireccion) a un host no publico."""
    import urllib.request

    class _Redir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _check_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es,en;q=0.8"})
    with urllib.request.build_opener(_Redir).open(req, timeout=timeout) as r:  # noqa: S310
        ctype = (r.headers.get("Content-Type") or "").lower()
        body = r.read(limit + 1)
    if len(body) > limit:
        raise ValueError("demasiado grande")
    return body, ctype


def _decode(body: bytes, ctype: str, url: str):
    """Bytes -> RGBA uint8 (o None). SVG -> rasterizado a LOGO_PX con skia."""
    import numpy as np
    is_svg = "svg" in ctype or urlparse(url).path.lower().endswith(".svg") or \
        body.lstrip()[:5] in (b"<svg ", b"<?xml") or b"<svg" in body[:600]
    if is_svg:
        import skia
        dom = skia.SVGDOM.MakeFromStream(skia.MemoryStream(body))
        if dom is None:
            return None
        surf = skia.Surface(LOGO_PX, LOGO_PX)
        c = surf.getCanvas()
        c.clear(skia.ColorTRANSPARENT)
        dom.setContainerSize(skia.Size(LOGO_PX, LOGO_PX))
        dom.render(c)
        return surf.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType,
                                                alphaType=skia.kUnpremul_AlphaType)
    import cv2
    arr = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        return None
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGBA)
    elif arr.shape[2] == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGBA)
    else:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA)
    return arr


def _save(rgba, dest: Path, sid: str, name: str, src: str) -> dict:
    """RGBA -> <sid>.png + miniatura + meta (formato de POST /sources)."""
    import cv2
    dest.mkdir(parents=True, exist_ok=True)
    h, w = rgba.shape[:2]
    cv2.imwrite(str(dest / f"{sid}.png"), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    tw = 320
    th = max(2, int(round(h * tw / max(1, w))) // 2 * 2)
    thumb = cv2.resize(cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR), (tw, th), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(dest / f"{sid}_t.jpg"), thumb)
    meta = {"name": name, "w": int(w), "h": int(h), "type": "image", "dur": 0, "src": src}
    (dest / f"{sid}_meta.json").write_text(json.dumps(meta))
    return {"sid": sid, "type": "image", "name": name, "w": int(w), "h": int(h), "src": src}


def _cached(dest: Path, sid: str) -> dict | None:
    m = dest / f"{sid}_meta.json"
    if (dest / f"{sid}.png").exists() and m.exists():
        try:
            d = json.loads(m.read_text())
            return {"sid": sid, "type": "image", "name": d.get("name", ""), "w": d.get("w"),
                    "h": d.get("h"), "src": d.get("src", "")}
        except Exception:  # noqa
            return None
    return None


def _grab(urls: list[str], dest: Path, name: str, get, timeout: float, min_px: int) -> dict | None:
    for u in urls[:4]:
        sid = hashlib.sha1(u.encode()).hexdigest()[:12]
        hit = _cached(dest, sid)
        if hit:
            return hit
        try:
            body, ctype = get(u, MAX_IMG, timeout)
            if ctype and not (ctype.startswith("image/") or "svg" in ctype or
                              "octet-stream" in ctype):
                continue
            rgba = _decode(body, ctype, u)
        except Exception:  # noqa: red, formato, tamano -> siguiente candidato
            continue
        if rgba is None or min(rgba.shape[:2]) < min_px:
            continue
        return _save(rgba, dest, sid, name, u)
    return None


def fetch_link_media(url: str, platform: str | None, dest_dir, timeout: float = 8.0,
                     fetch: Callable | None = None) -> dict:
    """Foto y logo del enlace `url` como medios propios en `dest_dir`. Nunca lanza.
    `fetch(url, limit, timeout) -> (bytes, content_type)` sustituye la red (tests)."""
    get = fetch or _http_get
    out = {"photo": None, "logo": None}
    try:
        body, ctype = get(url, MAX_HTML, timeout)
        html = body.decode("utf-8", "replace")
    except Exception:  # noqa: sin red / bloqueado
        return out
    cand = page_media(html, url)
    plat = (platform or "").lower()
    dest = Path(dest_dir)
    host = urlparse(url).hostname or "sitio"
    out["photo"] = _grab(cand["photo"], dest, f"foto_{plat or host}.png", get, timeout, 48)
    if plat not in OWN_ICON:
        out["logo"] = _grab(cand["logo"], dest, f"logo_{plat or host}.png", get, timeout, 64)
    return out
