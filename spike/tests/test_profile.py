"""Pruebas de motion/profile.py (perfil de la persona, docs/MOTION-ENGINE.md 3.2).

Solo datos FICTICIOS (nombres, usuarios, telefonos y paginas inventados). Cubre:
- parse_link: decenas de formatos reales de URL de cada red (con y sin https, www, m., query,
  utm, rutas de publicaciones), paginas de reservas conocidas, mapas, correo, telefono,
  WhatsApp, @usuario suelto (por contexto o ambiguo) y casos raros.
- find_links / parse_profile_text: texto libre como el que escribe una persona (heuristicas y
  un LLM falso inyectado), acentos, genero de la profesion, negocio.
- normalize_profile, entities_from, iniciales, telefonos.
"""
import pytest

from motion import profile as P


# ---------------------------------------------------------------- parse_link: redes sociales
@pytest.mark.parametrize("url, plat, handle, display", [
    # Instagram
    ("https://instagram.com/dra.demo", "instagram", "@dra.demo", "@dra.demo"),
    ("https://www.instagram.com/dra.demo/", "instagram", "@dra.demo", "@dra.demo"),
    ("instagram.com/dra.demo?igsh=MTRhZ2x4", "instagram", "@dra.demo", "@dra.demo"),
    ("http://instagram.com/dra_demo_mx?utm_source=qr", "instagram", "@dra_demo_mx", "@dra_demo_mx"),
    ("https://m.instagram.com/clinica.ficticia/", "instagram", "@clinica.ficticia", "@clinica.ficticia"),
    ("https://instagr.am/dra.demo", "instagram", "@dra.demo", "@dra.demo"),
    ("https://www.instagram.com/stories/dra.demo/3123456789/", "instagram", "@dra.demo", "@dra.demo"),
    ("https://ig.me/m/dra.demo", "instagram", "@dra.demo", "@dra.demo"),
    ("https://www.instagram.com/p/Cx12AbCdEf/", "instagram", "", "Instagram"),
    ("https://www.instagram.com/reel/Cx12AbCdEf/?igsh=abc", "instagram", "", "Instagram"),
    # TikTok
    ("https://www.tiktok.com/@dra.demo", "tiktok", "@dra.demo", "@dra.demo"),
    ("tiktok.com/@dra.demo?lang=es", "tiktok", "@dra.demo", "@dra.demo"),
    ("https://www.tiktok.com/@dra.demo/video/7300000000000000000", "tiktok", "@dra.demo", "@dra.demo"),
    ("https://vm.tiktok.com/ZMabcdef/", "tiktok", "", "TikTok"),
    # YouTube
    ("https://youtube.com/@ClinicaDemo", "youtube", "@ClinicaDemo", "@ClinicaDemo"),
    ("https://www.youtube.com/@ClinicaDemo/videos", "youtube", "@ClinicaDemo", "@ClinicaDemo"),
    ("https://m.youtube.com/@ClinicaDemo?si=xyz", "youtube", "@ClinicaDemo", "@ClinicaDemo"),
    ("youtube.com/c/ClinicaDemo", "youtube", "", "ClinicaDemo"),
    ("https://www.youtube.com/user/ClinicaDemo", "youtube", "", "ClinicaDemo"),
    ("https://www.youtube.com/channel/UC0000000000000000000000", "youtube", "", "YouTube"),
    ("https://youtu.be/abcDEF12345?si=zz", "youtube", "", "YouTube"),
    ("https://www.youtube.com/watch?v=abcDEF12345&t=30s", "youtube", "", "YouTube"),
    # Facebook
    ("https://facebook.com/ClinicaDemo", "facebook", "@ClinicaDemo", "@ClinicaDemo"),
    ("https://www.facebook.com/ClinicaDemo/?ref=bookmarks", "facebook", "@ClinicaDemo", "@ClinicaDemo"),
    ("https://m.facebook.com/ClinicaDemo", "facebook", "@ClinicaDemo", "@ClinicaDemo"),
    ("https://es-la.facebook.com/ClinicaDemo/", "facebook", "@ClinicaDemo", "@ClinicaDemo"),
    ("fb.me/clinicademo", "facebook", "@clinicademo", "@clinicademo"),
    ("https://fb.com/clinicademo", "facebook", "@clinicademo", "@clinicademo"),
    ("https://www.facebook.com/profile.php?id=100000000000001", "facebook", "", "Facebook"),
    ("https://www.facebook.com/pages/Clinica-Demo/123456", "facebook", "", "Clinica Demo"),
    # X / Twitter
    ("https://x.com/dra_demo", "x", "@dra_demo", "@dra_demo"),
    ("https://twitter.com/dra_demo", "x", "@dra_demo", "@dra_demo"),
    ("https://mobile.twitter.com/dra_demo/status/1700000000000000000", "x", "@dra_demo", "@dra_demo"),
    ("https://twitter.com/intent/tweet?text=hola", "x", "", "X"),
    # Threads
    ("https://www.threads.net/@dra.demo", "threads", "@dra.demo", "@dra.demo"),
    ("threads.com/@dra.demo", "threads", "@dra.demo", "@dra.demo"),
    # LinkedIn
    ("https://www.linkedin.com/in/ana-demo-123/", "linkedin", "", "in/ana-demo-123"),
    ("https://mx.linkedin.com/company/clinica-demo", "linkedin", "", "company/clinica-demo"),
    # Pinterest, Snapchat, Spotify, Telegram
    ("https://www.pinterest.com.mx/clinicademo/", "pinterest", "@clinicademo", "@clinicademo"),
    ("https://pinterest.com/clinicademo/ideas-sonrisa/", "pinterest", "@clinicademo", "@clinicademo"),
    ("https://pin.it/abc123", "pinterest", "", "Pinterest"),
    ("https://www.snapchat.com/add/dra.demo", "snapchat", "@dra.demo", "@dra.demo"),
    ("https://snapchat.com/@dra.demo", "snapchat", "@dra.demo", "@dra.demo"),
    ("https://open.spotify.com/show/0000000000000000000000", "spotify", "", "Spotify"),
    ("https://t.me/clinicademo", "telegram", "@clinicademo", "@clinicademo"),
    ("https://t.me/+AbCdEfGh123", "telegram", "", "Telegram"),
])
def test_parse_link_social(url, plat, handle, display):
    l = P.parse_link(url)
    assert l is not None, url
    assert (l["platform"], l["handle"], l["display"]) == (plat, handle, display), (url, l)
    assert l["icon"] == P.PLATFORMS[plat]["icon"] and l["icon"].startswith(("brand-", "world"))
    assert l["url"].startswith("https://") and "utm_" not in l["url"] and "igsh" not in l["url"]
    assert not l.get("ambiguous")
    if handle:
        assert l["handle"].count("@") == 1 and l["handle"].startswith("@")


def test_parse_link_social_kinds_and_labels():
    assert P.parse_link("instagram.com/dra.demo")["kind"] == "social"
    assert P.parse_link("instagram.com/dra.demo")["label"] == "Instagram"
    assert P.parse_link("t.me/clinicademo")["kind"] == "messaging"
    assert P.parse_link("https://tiktok.com/@a.b")["icon"] == "brand-tiktok"
    assert P.parse_link("https://x.com/abc")["icon"] == "brand-x"


# ---------------------------------------------------------------- reservas, web, mapas...
@pytest.mark.parametrize("url, plat, kind, display, label, icon", [
    ("https://paseo.club/business/dra-lucia-demo", "paseoclub", "booking", "paseo.club/dra-lucia-demo",
     "Agenda en PaseoClub", "calendar-event"),
    ("paseo.club/business/dra-lucia-demo?utm_source=ig", "paseoclub", "booking", "paseo.club/dra-lucia-demo",
     "Agenda en PaseoClub", "calendar-event"),
    ("https://holdyfy.mixiuh.online/profesional/dr-demo", "holdyfy", "booking",
     "holdyfy.mixiuh.online/dr-demo", "Agenda en Holdyfy", "calendar-event"),
    ("https://calendly.com/dra-demo/30min", "calendly", "booking", "calendly.com/dra-demo",
     "Agenda en Calendly", "calendar-event"),
    ("https://www.fresha.com/a/clinica-demo-ciudad-ficticia-avenida-uno-123-abcd12", "fresha", "booking",
     "fresha.com", "Reserva en Fresha", "calendar-event"),
    ("https://booksy.com/es-mx/12345_clinica-demo", "booksy", "booking", "booksy.com/12345_clinica-demo",
     "Reserva en Booksy", "calendar-event"),
    ("https://agendapro.com/mx/clinica-demo", "agendapro", "booking", "agendapro.com/clinica-demo",
     "Agenda en AgendaPro", "calendar-event"),
    ("https://linktr.ee/dra.demo", "linktree", "web", "linktr.ee/dra.demo", "Linktree", "brand-linktree"),
    ("beacons.ai/drademo", "beacons", "web", "beacons.ai/drademo", "Beacons", "world"),
    ("https://bio.link/drademo", "biolink", "web", "bio.link/drademo", "bio.link", "world"),
    ("https://maps.app.goo.gl/AbCdEf123", "maps", "maps", "Cómo llegar", "Cómo llegar", "map-pin"),
    ("https://www.google.com/maps/place/Clinica+Demo/@19.4,-99.1,17z", "maps", "maps", "Cómo llegar",
     "Cómo llegar", "map-pin"),
    ("https://goo.gl/maps/AbCdEf", "maps", "maps", "Cómo llegar", "Cómo llegar", "map-pin"),
    ("https://maps.google.com/?q=Clinica+Demo", "maps", "maps", "Cómo llegar", "Cómo llegar", "map-pin"),
    ("https://www.clinicademo.mx/citas?utm_source=ig&utm_medium=bio", "web", "web", "clinicademo.mx/citas",
     "Sitio web", "world"),
    ("clinicademo.com.mx", "web", "web", "clinicademo.com.mx", "Sitio web", "world"),
    ("www.clinica-demo.com/", "web", "web", "clinica-demo.com", "Sitio web", "world"),
    ("https://clinicademo.mx/servicios/limpieza-dental-profunda-con-ultrasonido-y-pulido-final",
     "web", "web", "clinicademo.mx", "Sitio web", "world"),
])
def test_parse_link_booking_web_maps(url, plat, kind, display, label, icon):
    l = P.parse_link(url)
    assert l is not None, url
    assert (l["platform"], l["kind"], l["display"], l["label"], l["icon"]) == (plat, kind, display, label, icon), l
    assert "utm_" not in l["url"]


@pytest.mark.parametrize("raw, plat, display, url", [
    ("https://wa.me/5215512345678", "whatsapp", "+52 55 1234 5678", "https://wa.me/5215512345678"),
    ("wa.me/523312345678?text=hola", "whatsapp", "+52 33 1234 5678", "https://wa.me/523312345678"),
    ("https://api.whatsapp.com/send?phone=526641234567&text=Hola", "whatsapp", "+52 664 123 4567",
     "https://wa.me/526641234567"),
    ("https://wa.me/message/ABCDEFGHIJK", "whatsapp", "WhatsApp", "https://wa.me/message/ABCDEFGHIJK"),
    ("https://chat.whatsapp.com/AbCdEfGhIjK", "whatsapp", "Grupo de WhatsApp", None),
    ("whatsapp 55 1234 5678", "whatsapp", "+52 55 1234 5678", "https://wa.me/525512345678"),
    ("WhatsApp: +52 1 55 1234 5678", "whatsapp", "+52 55 1234 5678", None),
    ("tel:+523312345678", "phone", "+52 33 1234 5678", "tel:+523312345678"),
    ("33-1234-5678", "phone", "33 1234 5678", "tel:+523312345678"),
    ("citas@clinicademo.mx", "email", "citas@clinicademo.mx", "mailto:citas@clinicademo.mx"),
    ("mailto:Citas@ClinicaDemo.mx", "email", "citas@clinicademo.mx", "mailto:citas@clinicademo.mx"),
])
def test_parse_link_messaging_phone_email(raw, plat, display, url):
    l = P.parse_link(raw)
    assert l is not None and l["platform"] == plat and l["display"] == display, l
    if url:
        assert l["url"] == url


def test_bare_handle_by_context_or_ambiguous():
    a = P.parse_link("@dra.demo")
    assert a["platform"] == "instagram" and a["ambiguous"] is True and a["handle"] == "@dra.demo"
    assert P.parse_link("tiktok @dra.demo")["platform"] == "tiktok"
    assert not P.parse_link("tiktok @dra.demo").get("ambiguous")
    assert P.parse_link("mi canal @ClinicaDemo")["platform"] == "youtube"
    assert P.parse_link("ig: @dra.demo")["platform"] == "instagram"
    assert not P.parse_link("ig: @dra.demo").get("ambiguous")
    assert P.parse_link("en X como @dra_demo")["platform"] == "x"
    assert P.parse_link("@dra.demo", hint="threads")["platform"] == "threads"
    assert P.parse_link("dra.demo", hint="tiktok")["handle"] == "@dra.demo"
    # sin @ duplicado, sin punto final de la oracion
    assert P.parse_link("@@dra.demo.")["handle"] == "@dra.demo"


@pytest.mark.parametrize("raw", ["", "   ", "hola", "sin enlaces aqui", "12:30", "3.5 estrellas", None, 42])
def test_parse_link_rejects_junk(raw):
    assert P.parse_link(raw) is None


def test_email_is_not_a_handle_nor_a_web():
    links = P.find_links("Escribe a citas@clinicademo.mx o visita clinicademo.mx")
    kinds = [(l["platform"], l["display"]) for l in links]
    assert kinds == [("email", "citas@clinicademo.mx"), ("web", "clinicademo.mx")]


# ---------------------------------------------------------------- texto libre
KEVIN_STYLE = ("ella es la Dra. Lucia Fernandez, es Odontologa, su instagram es "
               "https://instagram.com/dra.lucia.demo (o simplemente @dra.lucia.demo) y su pagina web es "
               "de PaseoClub https://paseo.club/business/dra-lucia-demo")


def test_parse_profile_text_like_kevins_request_without_llm():
    r = P.parse_profile_text(KEVIN_STYLE, use_llm=False)
    pr = r["profile"]
    assert pr["name"] == "Dra. Lucía Fernández"          # acentos agregados
    assert pr["title"] == "Odontóloga"                    # genero y ortografia
    assert pr["business"] == ""                           # PaseoClub es la plataforma, no el negocio
    assert pr["gender"] == "f"
    plats = [(l["platform"], l["display"]) for l in pr["links"]]
    assert plats == [("instagram", "@dra.lucia.demo"), ("paseoclub", "paseo.club/dra-lucia-demo")]
    assert pr["links"][0]["primary"] is True and pr["links"][1]["primary"] is False
    assert pr["links"][1]["label"] == "Agenda en PaseoClub"
    assert not any(l.get("ambiguous") for l in pr["links"])   # el @ suelto se unio al de Instagram
    assert r["llm"] is False and r["notes"] == []
    assert pr["use"] == P.USE_DEFAULTS


@pytest.mark.parametrize("text, name, title, business", [
    ("Mi nombre es Jose Luis Hernandez, soy nutriologo deportivo en el consultorio Vida Activa.",
     "José Luis Hernández", "Nutriólogo deportivo", "Vida Activa"),
    ("El es el Dr. Ruben Martinez, cirujano dentista", "Dr. Rubén Martínez", "Cirujano dentista", ""),
    ("Doctora Maria Gonzalez, especialista en ortodoncia. IG @dra.maria.demo", "Dra. María González",
     "Especialista en ortodoncia", ""),
    ("Lic. Sofia Ramirez es abogada y atiende en el despacho Legal Norte", "Lic. Sofía Ramírez", "Abogada",
     "Legal Norte"),
    ("Soy la Ing. Andrea Lopez, ingeniera civil", "Ing. Andrea López", "Ingeniera civil", ""),
    ("Hola! soy Ivan Perez, fotografo de bodas", "Iván Pérez", "Fotógrafo de bodas", ""),
    ("Mtra. Ines Jimenez, psicologa infantil", "Mtra. Inés Jiménez", "Psicóloga infantil", ""),
    ("Chef Adrian Suarez del restaurante Olivo Norte", "Chef Adrián Suárez", "Chef", "Olivo Norte"),
])
def test_parse_profile_text_heuristics(text, name, title, business):
    pr = P.parse_profile_text(text, use_llm=False)["profile"]
    assert pr["name"] == name
    assert pr["title"] == title
    assert pr["business"] == business


def test_profession_gender_follows_the_person():
    assert P.fix_title("odontologo", "f") == "Odontóloga"
    assert P.fix_title("odontologa", "m") == "Odontólogo"
    assert P.fix_title("odontologa") == "Odontóloga"
    assert P.fix_title("CIRUJANO DENTISTA", "f") == "Cirujana dentista"
    assert P.fix_title("dentista", "f") == "Dentista"
    pr = P.parse_profile_text("Ella es la Dra. Ana Ruiz, es odontologo", use_llm=False)["profile"]
    assert pr["title"] == "Odontóloga"


def test_accents_only_where_missing():
    assert P.fix_accents("Dra. Nohemi Gomez") == "Dra. Nohemí Gómez"
    assert P.fix_accents("DRA. RAMON PEREZ") == "DRA. RAMÓN PÉREZ"
    assert P.fix_accents("Lucía Fernandez") == "Lucía Fernández"
    assert P.fix_accents("Ana Ruiz Ortiz") == "Ana Ruiz Ortiz"             # sin acento en la RAE
    assert P.fix_accents("Sofía") == "Sofía"


def test_parse_profile_text_links_of_every_kind():
    t = ("Encuentranos en tiktok @clinica.demo, youtube.com/@ClinicaDemo, facebook.com/ClinicaDemo, "
         "WhatsApp 55 1234 5678, correo hola@clinicademo.mx, web www.clinicademo.mx y reserva en "
         "calendly.com/clinica-demo/limpieza. Instagram @clinica.demo")
    pr = P.parse_profile_text(t, use_llm=False)["profile"]
    got = [(l["platform"], l["display"]) for l in pr["links"]]
    assert got == [("tiktok", "@clinica.demo"), ("youtube", "@ClinicaDemo"), ("facebook", "@ClinicaDemo"),
                   ("whatsapp", "+52 55 1234 5678"), ("email", "hola@clinicademo.mx"),
                   ("web", "clinicademo.mx"), ("calendly", "calendly.com/clinica-demo"),
                   ("instagram", "@clinica.demo")]
    assert pr["whatsapp"] == "+52 55 1234 5678" and pr["email"] == "hola@clinicademo.mx"
    assert P.primary_social(pr)["platform"] == "tiktok"
    assert P.booking_link(pr)["platform"] == "calendly"


def test_ambiguous_handle_note():
    r = P.parse_profile_text("Dr. Pablo Demo, nutriologo. @pablo.demo", use_llm=False)
    l = r["profile"]["links"][0]
    assert l["platform"] == "instagram" and l.get("ambiguous") is True
    assert any("sin red indicada" in n for n in r["notes"])


def test_llm_refines_but_never_invents():
    calls = []

    def fake(text):
        calls.append(text)
        return {"name": "Dra. Lucía Fernández", "title": "Odontóloga", "gender": "f", "business": "PaseoClub",
                "tagline": ""}
    r = P.parse_profile_text(KEVIN_STYLE, use_llm=True, llm=fake)
    assert calls and r["llm"] is True
    assert r["profile"]["business"] == ""          # la plataforma nunca es el negocio
    assert r["profile"]["name"] == "Dra. Lucía Fernández"

    def inventa(text):
        return {"name": "Dra. Paola Inventada", "title": "Cardióloga", "gender": "f",
                "business": "Hospital Imaginario", "tagline": ""}
    pr = P.parse_profile_text("es especialista, su instagram es @demo.cuenta", use_llm=True, llm=inventa)["profile"]
    assert pr["name"] == "" and pr["business"] == "" and pr["title"] == "Especialista"

    def llm_natural(text):
        return {"name": "Carla Méndez", "title": "Estilista", "gender": "f", "business": "Salón Brillo",
                "tagline": ""}
    pr = P.parse_profile_text("la chica del video se llama carla mendez y corta el pelo en su salon brillo, "
                              "es estilista", use_llm=True, llm=llm_natural)["profile"]
    assert pr["name"] == "Carla Méndez" and pr["title"] == "Estilista" and pr["business"] == "Salón Brillo"


def test_llm_down_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("REELFY_OLLAMA_URL", "http://127.0.0.1:9")
    r = P.parse_profile_text(KEVIN_STYLE, use_llm=True)
    assert r["llm"] is False and r["profile"]["name"] == "Dra. Lucía Fernández"
    assert any("reglas" in n for n in r["notes"])


# ---------------------------------------------------------------- normalize / entidades
def test_normalize_profile_sanitizes_and_keeps_user_edits():
    raw = {"name": "  Dra.   Lucía\nFernández ", "title": "Odontóloga", "links": [
        "instagram.com/dra.lucia.demo", {"url": "https://paseo.club/business/dra-lucia-demo", "label": "Mi agenda"},
        {"url": "https://instagram.com/dra.lucia.demo/"},          # duplicado
        {"platform": "tiktok", "handle": "@@dra.lucia.demo"},
        {"url": "javascript:alert(1)"}, 42, None, {"platform": "nada"}],
        "whatsapp": "55 1234 5678", "avatar": {"sid": "../../etc/passwd"},
        "use": {"handle_watermark": True, "lower_third": 0}, "extra": "se descarta", "id": "Mala Id"}
    pr = P.normalize_profile(raw)
    assert pr["name"] == "Dra. Lucía Fernández"
    got = [(l["platform"], l["display"], l["label"]) for l in pr["links"]]
    assert got == [("instagram", "@dra.lucia.demo", "Instagram"),
                   ("paseoclub", "paseo.club/dra-lucia-demo", "Mi agenda"),
                   ("tiktok", "@dra.lucia.demo", "TikTok"),
                   ("whatsapp", "+52 55 1234 5678", "WhatsApp")]
    assert sum(l["primary"] for l in pr["links"]) == 1 and pr["links"][0]["primary"]
    assert "avatar" not in pr and "extra" not in pr and "id" not in pr
    assert pr["use"]["handle_watermark"] is True and pr["use"]["lower_third"] is False
    assert pr["use"]["end_card"] is True
    ok = P.normalize_profile({"name": "X Demo", "avatar": {"sid": "abc_123", "type": "image", "w": 800,
                                                           "h": 800, "path": "/tmp/x"}, "id": "x-demo"})
    assert ok["avatar"] == {"sid": "abc_123", "type": "image", "w": 800, "h": 800} and ok["id"] == "x-demo"


def test_normalize_profile_primary_choice_and_empty():
    pr = P.normalize_profile({"links": ["tiktok.com/@a.demo", {"url": "instagram.com/b.demo", "primary": True}]})
    assert P.primary_social(pr)["handle"] == "@b.demo"
    assert P.normalize_profile({}) is None
    assert P.normalize_profile({"name": "  ", "links": ["hola"]}) is None
    assert P.normalize_profile("texto") is None
    assert P.normalize_profile({"business": "Panadería Demo"})["business"] == "Panadería Demo"


def test_entities_from_profile():
    pr = P.parse_profile_text(KEVIN_STYLE, use_llm=False)["profile"]
    ent, keys = P.entities_from(pr)
    assert ent["name"] == "Dra. Lucía Fernández" and ent["name_src"] == "perfil" and ent["name_confirmed"]
    assert ent["role"] == "Odontóloga" and ent["handle"] == "@dra.lucia.demo"
    assert ent["link_label"] == "Agenda en PaseoClub" and ent["link_display"] == "paseo.club/dra-lucia-demo"
    assert set(keys) == {"name", "role", "handle", "handle_platform", "link_label", "link_display"}
    assert P.entities_from(None) == ({}, [])


@pytest.mark.parametrize("name, ini", [
    ("Dra. Lucía Fernández", "LF"), ("Dr. José Luis Pérez", "JP"), ("Panaderia", "P"),
    ("María de la Luz Gómez", "MG"), ("@clinica.demo", "CD"), ("", ""), ("Lic. ana", "A"),
])
def test_initials(name, ini):
    assert P.initials(name) == ini


@pytest.mark.parametrize("raw, out", [
    ("5215512345678", "+52 55 1234 5678"), ("+52 33 1234 5678", "+52 33 1234 5678"),
    ("5512345678", "55 1234 5678"), ("6641234567", "664 123 4567"), ("+1 555 123 4567", "+1 555 123 4567"),
    ("+34 612 345 678", "+34 612 345 678"),
])
def test_format_phone(raw, out):
    assert P.format_phone(raw) == out


def test_name_hits_and_platform_catalog():
    ws = ["hola", "soy", "la", "doctora", "Lucia", "y", "hoy"]
    assert P.name_hits(ws, "Dra. Lucía Fernández") == [4]
    cat = P.platforms_catalog()
    ids = {c["id"] for c in cat}
    assert {"instagram", "tiktok", "youtube", "facebook", "x", "threads", "linkedin", "whatsapp", "telegram",
            "pinterest", "snapchat", "spotify", "paseoclub", "holdyfy", "web", "maps", "email", "phone"} <= ids
    from motion import assets
    for c in cat:
        assert assets.icon_exists(c["icon"]), c


# ---------------------------------------------------------------- ola de arreglos (critica)
@pytest.mark.parametrize("text, name", [
    ("Él es el Dr. Juan Carlos de la Garza, cirujano dentista, consultorio Sonrisa Plena", "Dr. Juan Carlos de la Garza"),
    ("Soy la Dra. María de los Ángeles Pérez, pediatra", "Dra. María de los Ángeles Pérez"),
    ("Lic. Ramón del Río Demo, abogado", "Lic. Ramón del Río Demo"),
    ("Dra. Ana Ruiz y la Clínica Norte", "Dra. Ana Ruiz"),                    # 'y la' no une el nombre
    ("Nutrióloga Ana Sofía Martinez. Agenda: calendly.com/anasofia-demo", "Ana Sofía Martínez"),
    ("Hola! Soy la maestra Rosa, doy clases de piano", "Rosa"),
])
def test_compound_names_are_not_truncated(text, name):
    assert P.parse_profile_text(text, use_llm=False)["profile"]["name"] == name


def test_llm_full_name_wins_when_rules_cut_it():
    text = "Ella es la Dra. Ana María Fernanda de los Ángeles Castañeda Villaseñor, odontóloga"
    rules = P.parse_profile_text(text, use_llm=False)["profile"]["name"]
    assert rules.startswith("Dra. Ana María") and "Villaseñor" not in rules

    def fake(t):
        return {"name": "Dra. Ana María Fernanda de los Ángeles Castañeda Villaseñor", "title": "Odontóloga",
                "gender": "f", "business": "", "tagline": "", "address": ""}
    pr = P.parse_profile_text(text, use_llm=True, llm=fake)["profile"]
    assert pr["name"] == "Dra. Ana María Fernanda de los Ángeles Castañeda Villaseñor"

    def inventa(t):          # un apellido que NO esta en el texto no se adopta
        return {"name": "Dra. Ana María Fernanda de los Ángeles Castañeda Inventado", "title": "",
                "gender": "f", "business": "", "tagline": "", "address": ""}
    assert "Inventado" not in P.parse_profile_text(text, use_llm=True, llm=inventa)["profile"]["name"]


@pytest.mark.parametrize("text, title", [
    ("Lic. Karla Ruiz - psicóloga clínica - citas por whatsapp +52 1 55 1234 5678 - fb.com/karla.demo",
     "Psicóloga clínica"),
    ("Mtra. Ana Demo | nutrióloga deportiva | consultas en línea", "Nutrióloga deportiva"),
    ("Dr. Pablo Demo · cirujano plástico · agenda en calendly.com/pablo-demo", "Cirujano plástico"),
    ("Dra. Eva Demo, dermatóloga / citas al 33 1234 5678", "Dermatóloga"),
    ("Karla Demo es psicóloga clínica por las tardes", "Psicóloga clínica"),
])
def test_title_stops_at_separators_and_non_title_words(text, title):
    assert P.parse_profile_text(text, use_llm=False)["profile"]["title"] == title


def test_llm_title_fixes_a_broken_rules_title():
    assert P._broken_title("Psicóloga clínica citas por") and not P._broken_title("Psicóloga clínica")

    def fake(t):
        return {"name": "", "title": "Psicóloga clínica", "gender": "f", "business": "", "tagline": "",
                "address": ""}
    pr = P.parse_profile_text("Karla Demo, psicóloga clínica", use_llm=True, llm=fake)["profile"]
    assert pr["title"] == "Psicóloga clínica"


@pytest.mark.parametrize("raw, plat", [
    ("su twitter es @drademo", "x"), ("twitter @drademo", "x"), ("Twitter: @drademo", "x"),
    ("x @drademo", "x"), ("x: @drademo", "x"), ("en X como @drademo", "x"), ("mi x es @drademo", "x"),
])
def test_twitter_and_x_context(raw, plat):
    l = P.parse_link(raw)
    assert l["platform"] == plat and not l.get("ambiguous"), l


def test_ambiguous_handle_is_never_primary_over_a_confirmed_one():
    pr = P.parse_profile_text("su twitter es @drademo y su instagram @dra.demo", use_llm=False)["profile"]
    assert [(l["platform"], l["handle"]) for l in pr["links"]] == [("x", "@drademo"), ("instagram", "@dra.demo")]
    pr2 = P.parse_profile_text("@suelto.demo y su tiktok @dra.demo", use_llm=False)["profile"]
    assert pr2["links"][0].get("ambiguous") and P.primary_social(pr2)["handle"] == "@dra.demo"
    assert [l["primary"] for l in pr2["links"]] == [False, True]
    solo = P.normalize_profile({"links": ["@solo.demo"]})
    assert P.primary_social(solo)["handle"] == "@solo.demo"             # sin otra: la ambigua


@pytest.mark.parametrize("text, plat, handle", [
    ("Instagram: dra.demo", "instagram", "@dra.demo"),
    ("mi instagram es dra.demo", "instagram", "@dra.demo"),
    ("TikTok: clinica_demo", "tiktok", "@clinica_demo"),
    ("x: marianademo", "x", "@marianademo"),
    ("IG: drademo22", "instagram", "@drademo22"),
])
def test_handle_without_at_after_the_network_name(text, plat, handle):
    ls = P.find_links(text)
    assert [(l["platform"], l["handle"]) for l in ls] == [(plat, handle)]


@pytest.mark.parametrize("text", ["su instagram es muy bueno", "su instagram es genial y lo sigue mucha gente",
                                  "Instagram: instagram.com/dra.demo"])
def test_bare_words_are_not_handles(text):
    ls = P.find_links(text)
    assert all(l["handle"] != "@muy" and l["handle"] != "@genial" for l in ls)
    assert len([l for l in ls if l["platform"] == "instagram"]) <= 1


@pytest.mark.parametrize("text, addr", [
    ("Clínica Dental Sonríe Demo. Dr. Luis Gómez, ortodoncista. Estamos en Av. Demo 123, Guadalajara",
     "Av. Demo 123, Guadalajara"),
    ("Visítanos en Calle Uno #45, Col. Centro, Monterrey", "Calle Uno #45, Col. Centro, Monterrey"),
    ("Dirección: Blvd. Ficticio 2020 local 4", "Blvd. Ficticio 2020 local 4"),
    ("Dra. Ana Demo, dentista", ""),
])
def test_address_from_text(text, addr):
    assert P.parse_profile_text(text, use_llm=False)["profile"]["address"] == addr


def test_llm_address_only_if_it_is_in_the_text():
    def fake(t):
        return {"name": "", "title": "", "gender": "", "business": "", "tagline": "",
                "address": "Avenida Inventada 999, Ciudad Falsa"}
    pr = P.parse_profile_text("Dra. Ana Demo, dentista", use_llm=True, llm=fake)["profile"]
    assert pr["address"] == ""

    def fake2(t):
        return {"name": "", "title": "", "gender": "", "business": "", "tagline": "",
                "address": "Plaza Demo local 12, Zapopan"}
    pr2 = P.parse_profile_text("Dra. Ana Demo, dentista, estamos en Plaza Demo local 12, Zapopan",
                               use_llm=True, llm=fake2)["profile"]
    assert pr2["address"] == "Plaza Demo local 12, Zapopan"


@pytest.mark.parametrize("text, business", [
    ("Clínica Dental Sonríe Demo. Dr. Luis Gómez", "Clínica Dental Sonríe Demo"),
    ("Soy Marco Pérez, barbero en Barbería El Bigote", "Barbería El Bigote"),
    ("su salón se llama Salón Bella Demo", "Salón Bella Demo"),
    ("Dr. Juan Demo, consultorio Sonrisa Plena", "Sonrisa Plena"),
    ("perfumería Aroma Demo, perfumes árabes", "Aroma Demo"),
])
def test_business_keeps_its_type_when_it_is_part_of_the_name(text, business):
    assert P.parse_profile_text(text, use_llm=False)["profile"]["business"] == business


def test_llm_business_only_as_written():
    def fake(t):
        return {"name": "", "title": "", "gender": "", "business": "Consultorio Sonrisa Plena", "tagline": "",
                "address": ""}
    pr = P.parse_profile_text("Dr. Juan Demo, consultorio Sonrisa Plena", use_llm=True, llm=fake)["profile"]
    assert pr["business"] == "Sonrisa Plena"          # el tipo en minuscula no es parte del nombre

    def fake2(t):
        return {"name": "", "title": "", "gender": "", "business": "Clínica Dental Sonrisa Plena", "tagline": "",
                "address": ""}
    pr2 = P.parse_profile_text("Dr. Juan Demo de la Clínica Dental Sonrisa Plena", use_llm=True, llm=fake2)["profile"]
    assert pr2["business"] == "Clínica Dental Sonrisa Plena"


def test_studio_can_change_the_network_of_a_link():
    base = P.parse_link("@otra.cuenta")
    assert base["platform"] == "instagram" and base["ambiguous"]
    # el Studio devuelve el mismo enlace con otra red: gana la elegida (y no arrastra 'Instagram')
    l = P._norm_link(dict(base, platform="tiktok"))
    assert (l["platform"], l["handle"], l["url"], l["label"]) == ("tiktok", "@otra.cuenta",
                                                                  "https://tiktok.com/@otra.cuenta", "TikTok")
    assert not l.get("ambiguous")
    # confirmado (sin ambiguous) pero la url es la armada con el usuario: tambien cambia
    conf = P._norm_link({"url": "https://instagram.com/otra.cuenta", "platform": "youtube", "handle": "@otra.cuenta",
                         "label": "Instagram", "display": "@otra.cuenta"})
    assert conf["platform"] == "youtube" and conf["url"] == "https://youtube.com/@otra.cuenta"
    assert conf["label"] == "YouTube"
    # una url PROPIA de Instagram pegada (con www) manda sobre un selector viejo
    own = P._norm_link({"url": "https://www.instagram.com/otra.cuenta/", "platform": "tiktok"})
    assert own["platform"] == "instagram"
    # etiqueta propia se respeta al cambiar de red
    keep = P._norm_link(dict(base, platform="tiktok", label="Mis videos"))
    assert keep["label"] == "Mis videos"
    # por normalize_profile (lo que hace PUT /profile)
    pr = P.normalize_profile({"name": "Demo", "links": [dict(base, platform="threads")]})
    assert pr["links"][0]["platform"] == "threads" and pr["links"][0]["primary"]


@pytest.mark.parametrize("url, display", [
    ("https://holdyfy.mixiuh.online/profesional/dra-maria-de-los-angeles-castaneda",
     "holdyfy/dra-maria-de-los-angeles-castaneda"),
    ("https://holdyfy.mixiuh.online/profesional/dra-ana-demo", "holdyfy.mixiuh.online/dra-ana-demo"),
    ("https://paseo.club/business/clinica-dental-sonrie-demo-guadalajara", "paseo.club/clinica-dental-sonrie-demo-guadalajara"),
    ("https://calendly.com/dra-maria-de-los-angeles-castaneda/limpieza", "calendly.com/dra-maria-de-los-angeles-castaneda"),
])
def test_booking_display_always_keeps_the_slug(url, display):
    l = P.parse_link(url)
    assert l["display"] == display and len(l["display"]) <= P.DISPLAY_MAX + 8


def test_link_cta_for_web_by_business_kind():
    web = P.parse_link("https://olfato-demo.mx/perfumes")
    assert web["label"] == "Sitio web"
    assert P.link_cta(web, "Perfumería Olfato") == "Compra en línea"
    assert P.link_cta(web, "restaurante La Cocina") == "Ordena en línea"
    assert P.link_cta(web, "Odontóloga") == "Visita mi página"
    assert P.link_cta(P.parse_link("linktr.ee/dra.demo"), "") == "Todos mis links"
    assert P.link_cta(dict(web, label="Ver catálogo"), "tienda") == "Ver catálogo"          # editada
    assert P.link_cta(P.parse_link("paseo.club/business/dra-demo"), "tienda") == "Agenda en PaseoClub"
    assert P.link_cta(None) == ""


@pytest.mark.parametrize("name, ini", [
    ("Dra. Guadalupe Fernández Castañeda", "GF"), ("Dr. José Luis Pérez", "JP"),
    ("Dr. Juan Carlos de la Garza", "JG"), ("María de los Ángeles Castañeda Villaseñor", "MC"),
    ("Marco Gómez", "MG"), ("Olfato", "O"),
])
def test_initials_given_name_and_first_surname(name, ini):
    assert P.initials(name) == ini


def test_name_variants_shorten_without_ellipsis():
    v = P.name_variants("Dra. Guadalupe Fernández Castañeda")
    assert v[0] == "Dra. Guadalupe Fernández Castañeda"
    assert v[1] == "Dra. Guadalupe Fernández" and "Guadalupe Fernández" in v
    v2 = P.name_variants("Dra. María de los Ángeles Castañeda Villaseñor")
    assert "Dra. María de los Ángeles Castañeda" in v2 and "Dra. María Castañeda" in v2
    assert P.name_variants("Olfato") == ["Olfato"]
    assert all("…" not in x and "..." not in x for x in v + v2)


@pytest.mark.parametrize("words, span", [
    (["mi", "sonrisa", "blanca", "y", "limpia"], None),                    # una palabra comun no es el nombre
    (["hola", "soy", "la", "doctora", "blanca", "y", "hoy"], (3, 4)),       # presentacion
    (["con", "blanca", "flores", "vas", "a", "estar", "bien"], (1, 2)),    # dos palabras del nombre
    (["me", "llamo", "blanka", "florez"], (2, 3)),                          # transcripcion con una letra
    (["las", "flores", "del", "jardin"], None),
])
def test_name_span_needs_two_words_or_an_introduction(words, span):
    assert P.name_span(words, "Dra. Blanca Flores") == span


@pytest.mark.parametrize("text", [
    "su twitter es @drademo y su instagram @dra.demo", "@suelto.demo y su tiktok @dra.demo",
    "Dra. Ana Demo, dentista. WhatsApp 33 1234 5678, citas@demo.mx, Av. Demo 123, Guadalajara. "
    "paseo.club/business/ana-demo, maps.app.goo.gl/AbC", KEVIN_STYLE])
def test_normalize_is_idempotent(text):
    """PUT /plan re-aplica el perfil solo si CAMBIO: normalizar dos veces no lo cambia."""
    import json
    p = P.parse_profile_text(text, use_llm=False)["profile"]
    q = P.normalize_profile(json.loads(json.dumps(p)))
    assert json.dumps(p, sort_keys=True) == json.dumps(q, sort_keys=True) == \
        json.dumps(P.normalize_profile(q), sort_keys=True)
