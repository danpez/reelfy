"""Foto y logo de los enlaces del Perfil en los graficos (graphics: link_below con foto y logo,
insignia de logo del Seguir, logo en el encabezado de las tarjetas y en los chips de socials).

Imagenes 100 % sinteticas generadas aqui (el repo es publico: nada de personas, marcas ni
direcciones reales). Los archivos van a una carpeta temporal con el formato de POST /sources
(<sid>.png) y se resuelven con el mismo resolver de render (graphics.MEDIA_OPTS media_root)."""
import time

import cv2
import numpy as np
import pytest

from motion import ctx as C
from motion import graphics as G

W, H = 1080, 1920
REST = 1.95
PHOTO_RGB = (40, 200, 90)          # foto: verde plano (facil de reconocer en el cuadro)
LOGO_RGB = (220, 30, 200)          # logo transparente: disco magenta
APP_RGB = (255, 120, 0)            # icono de app opaco: naranja


# ------------------------------------------------------------------ imagenes sinteticas
def _photo(n=1024, rgb=PHOTO_RGB):
    a = np.zeros((n, n, 4), np.uint8)
    a[..., :3] = rgb
    a[..., 3] = 255
    return a


def _logo_tr(n=512, rgb=LOGO_RGB, r=0.3):
    a = np.zeros((n, n, 4), np.uint8)
    cv2.circle(a, (n // 2, n // 2), int(n * r), (*rgb, 255), -1, cv2.LINE_AA)
    return a


def _logo_app(n=180, rgb=APP_RGB):
    a = np.zeros((n, n, 4), np.uint8)
    a[..., :3] = rgb
    a[..., 3] = 255
    return a


def _logo_white(n=256):
    a = np.zeros((n, n, 4), np.uint8)
    cv2.circle(a, (n // 2, n // 2), int(n * 0.3), (255, 255, 255, 255), -1, cv2.LINE_AA)
    return a


def _logo_wide():
    a = np.zeros((100, 300, 4), np.uint8)
    a[..., :3] = (250, 244, 230)
    a[..., 3] = 255
    cv2.rectangle(a, (60, 30), (240, 70), (20, 40, 160, 255), -1)
    return a


def _save(root, sid, rgba):
    cv2.imwrite(str(root / f"{sid}.png"), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    return {"sid": sid, "type": "image", "name": f"{sid}.png", "w": rgba.shape[1], "h": rgba.shape[0]}


@pytest.fixture
def media(tmp_path, monkeypatch):
    """Carpeta de medios propios con fotos/logos sinteticos; graphics la usa como media_root."""
    root = tmp_path / "sources"
    root.mkdir()
    monkeypatch.setattr(G, "MEDIA_OPTS", {"media_root": str(root)})
    G.clear_media_cache()
    m = {"photo": _save(root, "syn_photo", _photo()), "photo100": _save(root, "syn_photo100", _photo(100)),
         "logo": _save(root, "syn_logo", _logo_tr()), "app": _save(root, "syn_app", _logo_app()),
         "white": _save(root, "syn_white", _logo_white()), "wide": _save(root, "syn_wide", _logo_wide()),
         "root": root}
    yield m
    G.clear_media_cache()


def _ctx(t=REST):
    return C.demo_ctx(W, H, t=t, entities={"name": "Ana Demo"})


def _render(fn, item, lt=REST, dur=3.0):
    arr, surf = C.new_frame(W, H)
    fn(surf.getCanvas(), item, lt, dur, _ctx(lt))
    return arr.copy()


def _near(arr, rgb, tol=40):
    """Mascara de pixeles opacos cercanos al color `rgb`."""
    d = np.abs(arr[..., :3].astype(int) - np.array(rgb)).sum(-1)
    return (d < tol) & (arr[..., 3] > 200)


def _bbox(mask):
    ys, xs = np.nonzero(mask)
    assert len(xs), "no aparecio el color buscado"
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


LB = {"id": "lb", "kind": "cta", "variant": "link_below", "label": "Agenda en ReservaDemo",
      "display": "reserva.demo/ana", "x": 0.5, "y": 0.4, "color": "#FFD23F", "arrow_scale": 0}


# ------------------------------------------------------------------ resolver de medios
def test_media_ref_only_accepts_sids():
    assert G.media_ref({"sid": "abc_12-x"}) == {"sid": "abc_12-x"}
    for bad in (None, "abc", {"sid": "../etc/passwd"}, {"sid": "/tmp/x.png"}, {"sid": ""}, {"sid": 5},
                {"path": "/tmp/x.png"}, {"sid": "a" * 65}):
        assert G.media_ref(bad) is None, bad


def test_sanitize_drops_path_logo_and_keeps_link_logos():
    it = G.sanitize({"kind": "cta", "logo": "/etc/passwd", "links": [
        {"platform": "web", "text": "a", "logo": {"sid": "lg1"}}, {"platform": "web", "text": "b", "logo": "/x.png"}]})
    assert "logo" not in it
    assert it["links"][0]["logo"] == {"sid": "lg1"} and "logo" not in it["links"][1]
    assert G.sanitize({"logo": {"sid": "ok_1"}})["logo"] == {"sid": "ok_1"}


def test_file_image_resolves_by_sid_premultiplied(media):
    img = G.media_file_image(media["logo"])
    assert img is not None and (img.width(), img.height()) == (512, 512)
    arr = C.image_to_rgba(img)
    # premultiplicado valido: ningun canal pasa a su alpha (sin halo claro en el borde del logo)
    assert int((arr[..., :3].max(axis=-1).astype(int) - arr[..., 3].astype(int)).max()) <= 0
    assert arr[0, 0, 3] == 0 and arr[256, 256, 3] == 255
    # cache: la misma imagen (mismo uniqueID) mientras el archivo no cambie
    assert G.media_file_image(media["logo"]).uniqueID() == img.uniqueID()


def test_file_image_missing_retries_and_reloads_on_change(media, monkeypatch):
    ref = {"sid": "syn_later", "type": "image"}
    assert G.media_file_image(ref) is None                    # aun no se descarga
    _save(media["root"], "syn_later", _logo_app())
    assert G.media_file_image(ref) is None                    # dentro de MISS_RETRY: no vuelve a buscar
    monkeypatch.setattr(G, "MISS_RETRY", 0.0)
    got = G.media_file_image(ref)
    assert got is not None and got.width() == 180
    # el usuario cambia el archivo del mismo sid: se relee (cache por mtime) en cuanto vence HIT_TTL
    # (sin stat del archivo en cada cuadro)
    time.sleep(0.01)
    _save(media["root"], "syn_later", _logo_app(96))
    assert G.media_file_image(ref).width() == 180
    monkeypatch.setattr(G, "HIT_TTL", 0.0)
    assert G.media_file_image(ref).width() == 96
    # el archivo desaparece: el dibujo de siempre (None), sin excepcion
    (media["root"] / "syn_later.png").unlink()
    assert G.media_file_image(ref) is None


def test_file_image_never_reads_paths_or_videos(media, tmp_path):
    (media["root"] / "syn_clip.mp4").write_bytes(b"\x00" * 64)
    assert G.media_file_image({"sid": "syn_clip", "type": "video"}) is None
    assert G.media_file_image({"sid": str(media["root"] / "syn_logo.png")}) is None
    assert G.media_file_image({"sid": "../sources/syn_logo"}) is None


def test_injected_logo_image_wins_and_is_converted_once(media):
    arr = _logo_app(64, (10, 20, 250))
    it = {"logo": media["logo"], "_logo_image": arr}
    a, b = G.logo_image(it), G.logo_image(it)
    assert a.width() == 64 and a.uniqueID() == b.uniqueID()
    assert G.logo_image({"logo": media["logo"]}).width() == 512
    assert G.logo_image({}) is None and G.logo_image(None) is None


def test_item_photo_prefers_render_injection(media):
    inj = _photo(40, (1, 2, 3))
    assert G.item_photo({"media": media["photo"], "_media_image": inj}).width() == 40
    assert G.item_photo({"media": media["photo"], "_media_image": None}) is None   # render ya decidio
    assert G.item_photo({"media": media["photo"]}).width() == 1024                  # sin inyeccion: archivo


# ------------------------------------------------------------------ como se presenta un logo
def test_logo_look_cases(media):
    look = lambda k: G.logo_look(G.media_file_image(media[k]))   # noqa: E731
    app = look("app")
    assert app["bleed"] is True                                  # icono de app: llena la insignia
    tr = look("logo")
    assert tr["bleed"] is False and tr["plate"] == G.LOGO_PLATE
    x0, y0, x1, y1 = tr["box"]                                   # caja de tinta sin el margen transparente
    assert abs((x1 - x0) - 0.6 * 512) < 6 and abs((y1 - y0) - 0.6 * 512) < 6
    assert look("white")["plate"] == G.LOGO_PLATE_DARK           # logo blanco: placa oscura
    wide = look("wide")
    assert wide["bleed"] is False and wide["plate"] == "#FAF4E6"   # opaco no cuadrado: placa del borde
    assert wide["box"] == (0, 0, 300, 100)


def test_logo_fit_keeps_aspect_and_padding():
    lk = {"box": (0, 0, 200, 100)}
    w, h = G.logo_fit(lk, 100.0, "squircle")
    assert w == pytest.approx(100 * (1 - 2 * G.LOGO_PAD)) and h == pytest.approx(w / 2)
    w, h = G.logo_fit(lk, 100.0, "circle")
    assert np.hypot(w, h) == pytest.approx(100 * G.LOGO_RING)


def test_resampled_downscale_has_no_aliasing_and_upscale_is_exact_size():
    stripes = np.zeros((1024, 1024, 4), np.uint8)
    stripes[..., 3] = 255
    stripes[:, ::2, :3] = 255                                   # rayas de 1 px: aliasing si se muestrea mal
    img = G._as_image(stripes)
    out = C.image_to_rgba(G._resampled(img, (0, 0, 1024, 1024), 60, 60))
    assert out.shape[:2] == (60, 60)
    g = out[..., 0].astype(float)
    assert abs(g.mean() - 127.5) < 10 and g.std() < 12           # gris parejo (no moire)
    small = G._as_image(_photo(100))
    up = G._resampled(small, (0, 0, 100, 100), 150, 150)
    assert (up.width(), up.height()) == (150, 150)
    assert G._resampled(small, (0, 0, 100, 100), 150, 150) is up   # cache


def test_tiny_instagram_photo_is_upscaled_smoothly(media):
    """La foto publica de Instagram mide 100 px: se amplia con Catmull-Rom (sin escalones)."""
    tiny = np.zeros((100, 100, 4), np.uint8)
    tiny[..., 3] = 255
    tiny[:, 50:, :3] = 255                                      # borde vertical nitido
    it = {"_media_image": tiny, "avatar": "image"}
    img = G._avatar_photo(it, 200.0)                            # px = 200 * 0.8 * 1.5 = 240
    arr = C.image_to_rgba(img)
    assert arr.shape[:2] == (240, 240)
    row = arr[120, :, 0].astype(int)
    ramp = np.nonzero((row > 20) & (row < 235))[0]
    assert 1 <= len(ramp) <= 8                                   # transicion corta: nitida, sin bloques


# ------------------------------------------------------------------ link_below
def test_link_below_photo_in_slot_with_logo_badge(media):
    it = dict(LB, avatar="image", media=media["photo"], logo=media["logo"])
    label, fb, h, ib, bw, disp, fd, S = G._link_below_layout(G.sanitize(it), _ctx())
    assert S["kind"] == "photo" and S["d"] == pytest.approx(h * G.LINK_PHOTO)
    arr = _render(G.draw_cta, it)
    px0, py0, px1, py1 = _bbox(_near(arr, PHOTO_RGB))
    d = px1 - px0
    assert abs(d - (S["d"] - 2 * max(1.5, S["d"] * 0.05))) <= 4    # foto redonda del hueco (dentro del aro)
    pill = _bbox(_near(arr, (0xFF, 0xD2, 0x3F)))
    # concentrica con la punta redonda: el aire a la izquierda es el mismo que arriba
    cy = (py0 + py1) / 2
    assert abs(cy - (pill[1] + pill[3]) / 2) <= 3
    assert abs((px0 - pill[0]) - (py0 - pill[1])) <= 4
    # logo del sitio: insignia en la esquina inferior derecha de la foto
    lx0, ly0, lx1, ly1 = _bbox(_near(arr, LOGO_RGB))
    assert (lx0 + lx1) / 2 > (px0 + px1) / 2 + d * 0.2 and (ly0 + ly1) / 2 > cy + d * 0.15


def test_link_below_logo_only_uses_rounded_badge(media):
    it = dict(LB, logo=media["app"])
    S = G._link_below_layout(G.sanitize(it), _ctx())[7]
    assert S["kind"] == "logo"
    arr = _render(G.draw_cta, it)
    x0, y0, x1, y1 = _bbox(_near(arr, APP_RGB))
    assert abs((x1 - x0) - S["d"]) <= 3 and abs((y1 - y0) - S["d"]) <= 3   # icono de app a sangre
    # esquinas redondeadas: la esquina del cuadrado no es naranja
    assert not _near(arr, APP_RGB)[y0 + 1, x0 + 1]


def test_link_below_without_files_is_todays_icon_pill(media):
    base = _render(G.draw_cta, LB)
    for extra in ({"avatar": "image", "media": {"sid": "syn_missing"}}, {"logo": {"sid": "syn_missing2"}},
                  {"avatar": "initial", "media": media["photo"]}):
        assert np.array_equal(_render(G.draw_cta, dict(LB, **extra)), base), extra


def test_link_below_icon_geometry_unchanged():
    """Sin foto ni logo la pastilla mide lo de siempre (tw + icono + 0.2h + 0.8h / 1.0h)."""
    ctx = _ctx()
    for disp, k_ib, k_pad in (("reserva.demo/ana", 0.36, 0.8), ("", 0.42, 1.0)):
        label, fb, h, ib, bw, d_, fd, S = G._link_below_layout(dict(LB, display=disp), ctx)
        tw = max(fb.measureText(label), fd.measureText(d_)) if d_ else fb.measureText(label)
        assert S["kind"] == "icon" and ib == pytest.approx(h * k_ib)
        assert bw == pytest.approx(min(G.SYM_W * W, tw + ib + h * 0.2 + h * k_pad))


def test_link_below_photo_width_is_what_the_director_measures(media):
    it = dict(LB, avatar="image", media=media["photo"], logo=media["logo"], label="Agenda tu cita con la especialista demo")
    ext = G.cta_extent(G.sanitize(it), _ctx())
    arr = _render(G.draw_cta, it)
    x0, _, x1, _ = _bbox(_near(arr, (0xFF, 0xD2, 0x3F), tol=60))
    assert abs((x1 - x0) - 2 * ext) <= 4
    assert 2 * ext <= G.SYM_W * W + 1                            # nunca pasa SAFE


def test_link_below_single_line_photo(media):
    it = dict(LB, display="", avatar="image", media=media["photo100"])
    S = G._link_below_layout(G.sanitize(it), _ctx())[7]
    assert S["kind"] == "photo" and S["logo"] is None
    arr = _render(G.draw_cta, it)
    assert _near(arr, PHOTO_RGB).sum() > 0.5 * np.pi * (S["d"] / 2 * 0.85) ** 2


# ------------------------------------------------------------------ follow, card, socials
FOL = {"id": "fo", "kind": "cta", "variant": "follow", "title": "Ana Demo", "handle": "ana.demo",
       "platform": "web", "x": 0.5, "y": 0.4, "cursor": False}


def test_follow_logo_badge_replaces_platform_icon(media):
    it = dict(FOL, logo=media["app"])
    L = G._follow_layout(G.sanitize(it), _ctx())
    arr = _render(G.draw_cta, it)
    x0, y0, x1, y1 = _bbox(_near(arr, APP_RGB))
    bd = L["d"] * G.FOLLOW_BADGE
    assert abs((x1 - x0) - bd) <= 3                               # insignia del tamano de la de la red
    base = _render(G.draw_cta, FOL)
    assert not _near(base, APP_RGB).any()
    # el ancho de la pastilla no cambia (la insignia va sobre el avatar)
    assert G.cta_extent(G.sanitize(it), _ctx()) == G.cta_extent(G.sanitize(FOL), _ctx())


def test_follow_photo_from_file_when_render_did_not_inject(media):
    it = dict(FOL, avatar="image", media=media["photo"], platform="instagram")
    arr = _render(G.draw_cta, it)
    assert _near(arr, PHOTO_RGB).sum() > 500


@pytest.mark.parametrize("variant", ["schedule", "booking", "link", "price", "location", "generic", "phone"])
def test_card_logo_replaces_header_icon(media, variant):
    it = {"id": "cd", "kind": "card", "variant": variant, "title": "Horarios disponibles", "subtitle": "En ReservaDemo",
          "value": "$500", "label": "Agendar", "map": False, "x": 0.5, "y": 0.5, "logo": media["logo"]}
    arr = _render(G.draw_card, it)
    x0, y0, x1, y1 = _bbox(_near(arr, LOGO_RGB))
    th, F, u, pad, inner, rows, gap, Wc, Hc = G.card_layout(it, _ctx())
    assert (x0 + x1) / 2 < W / 2 - Wc * 0.25                     # en el hueco del icono (izquierda del encabezado)
    base = {k: v for k, v in it.items() if k != "logo"}
    missing = dict(base, logo={"sid": "syn_nope"})
    assert np.array_equal(_render(G.draw_card, missing), _render(G.draw_card, base))   # sin archivo: el icono


def test_card_logo_does_not_change_card_size(media):
    it = {"id": "cd", "kind": "card", "variant": "booking", "title": "Agenda tu cita", "display": "reserva.demo/ana"}
    assert G.card_size(dict(it, logo=media["app"]), _ctx()) == G.card_size(it, _ctx())


def test_socials_chip_logo(media):
    it = {"id": "so", "kind": "socials", "x": 0.5, "y": 0.5, "links": [
        {"platform": "instagram", "text": "@ana.demo"}, {"platform": "web", "text": "reserva.demo/ana", "logo": media["logo"]}]}
    arr = _render(G.draw_socials, it)
    assert _near(arr, LOGO_RGB).sum() > 200
    assert not _near(_render(G.draw_socials, dict(it, icon_style="mono")), LOGO_RGB).any()   # mono: iconos parejos
    assert G.socials_extent(it, _ctx()) == G.socials_extent({**it, "links": [
        {k: v for k, v in l.items() if k != "logo"} for l in it["links"]]}, _ctx())


def test_badges_are_cached_across_frames(media):
    """Cuadro a cuadro no se vuelve a decodificar ni remuestrear (caches estables y rapido)."""
    it = dict(LB, avatar="image", media=media["photo"], logo=media["logo"])
    _render(G.draw_cta, it, lt=REST)
    n_res, n_img = len(G._RESAMPLED), len(G._FILE_IMG)
    t0 = time.perf_counter()
    for k in range(5):
        _render(G.draw_cta, it, lt=REST + 0.1 * (k + 1))
    dt = (time.perf_counter() - t0) / 5
    assert len(G._RESAMPLED) == n_res and len(G._FILE_IMG) == n_img
    assert dt < 0.25, f"{dt * 1000:.0f} ms por cuadro"


def test_icono_de_app_con_esquinas_redondeadas_llena_la_insignia():
    """Un apple-touch-icon / SVG con rx (fondo crema casi blanco y esquinas transparentes) es un
    icono de app: se presenta a sangre, no como 'logo blanco' sobre placa oscura."""
    import skia
    n, r = 120, 26
    surf = skia.Surface(n, n)
    c = surf.getCanvas()
    c.clear(skia.ColorTRANSPARENT)
    c.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeWH(n, n), r, r), skia.Paint(Color=skia.Color(245, 239, 235)))
    c.drawCircle(n / 2, n / 2, 30, skia.Paint(Color=skia.Color(8, 121, 77), Style=skia.Paint.kStroke_Style,
                                              StrokeWidth=10, AntiAlias=True))
    look = G.logo_look(surf.makeImageSnapshot())
    assert look["bleed"] is True and look["plate"] is None
