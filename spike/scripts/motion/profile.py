"""Perfil de la persona o del negocio (docs/MOTION-ENGINE.md 3.2).

Quien sale en el video: nombre, profesion (con su genero), negocio, frase y ENLACES (redes
sociales, pagina de reservas, web, WhatsApp, mapa, correo, telefono). El director lo integra en
la edicion (rotulo con nombre + profesion, CTA Seguir con el @usuario y el icono de la red,
tarjeta de reservas cuando la voz dice "link", "agenda", "cita"...; tarjeta final con las redes)
y sus datos MANDAN sobre lo que infiere del audio o de un letrero.

Modulo PURO: sin video, skia ni numpy (lo usan el servidor, el director y las pruebas).

API:
  parse_link(texto, hint=None) -> dict | None
      URL o @usuario -> {url, platform, kind, handle, display, label, icon, primary[, ambiguous]}.
      kind: social | booking | web | messaging | maps | email | phone. `display` = texto corto
      para pantalla ("@dra.demo", "paseo.club/dra-demo"); `label` = etiqueta ("Instagram",
      "Agenda en PaseoClub"); `icon` = icono Tabler (brand-instagram, calendar-event, world...).
  find_links(texto) -> [link]   todos los enlaces de un texto libre (URL, dominio, @usuario por
      contexto, correo, telefono / WhatsApp), sin duplicados.
  parse_profile_text(texto, use_llm=True, llm=None) -> {profile, notes, llm}
      texto libre ("ella es la Dra. X, es odontologa, su instagram es @x y agenda en paseo.club/...")
      -> perfil. Enlaces deterministas; nombre/profesion/negocio por reglas y, si hay LLM local
      (qwen3.5:4b), refinados por el (validado contra el texto: nunca inventa).
  normalize_profile(p) -> dict | None   perfil saneado (lo usa schema.normalize en plan.profile);
      el Studio cambia la red de un enlace mandando otro `platform` (ver _norm_link).
  entities_from(profile) -> (dict, [claves])   entidades que el perfil fija (name_src 'perfil').
  primary_social(p), booking_link(p), all_links(p), initials(nombre), name_variants(nombre),
  name_span(palabras, nombre) / name_hits(...), link_cta(enlace, rubro).
  Foto y logo de cada enlace (photo/logo, media_src auto|user, media_off; ver _keep_link_media):
  link_media(enlace, 'photo'|'logo'), link_for(p, red, @usuario?), media_wanted(enlace) (lo que el
  servidor descarga solo), media_sids(p).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from typing import Any, Callable

# ---------------------------------------------------------------- catalogo de plataformas
# color = color de marca (insignia del icono); cta = etiqueta de accion de las de reservas
PLATFORMS: dict[str, dict] = {
    "instagram": {"label": "Instagram", "icon": "brand-instagram", "kind": "social", "color": "#E1306C"},
    "tiktok": {"label": "TikTok", "icon": "brand-tiktok", "kind": "social", "color": "#111111"},
    "youtube": {"label": "YouTube", "icon": "brand-youtube", "kind": "social", "color": "#FF0033"},
    "facebook": {"label": "Facebook", "icon": "brand-facebook", "kind": "social", "color": "#1877F2"},
    "x": {"label": "X", "icon": "brand-x", "kind": "social", "color": "#111111"},
    "threads": {"label": "Threads", "icon": "brand-threads", "kind": "social", "color": "#111111"},
    "linkedin": {"label": "LinkedIn", "icon": "brand-linkedin", "kind": "social", "color": "#0A66C2"},
    "pinterest": {"label": "Pinterest", "icon": "brand-pinterest", "kind": "social", "color": "#E60023"},
    "snapchat": {"label": "Snapchat", "icon": "brand-snapchat", "kind": "social", "color": "#FFFC00"},
    "spotify": {"label": "Spotify", "icon": "brand-spotify", "kind": "social", "color": "#1DB954"},
    "whatsapp": {"label": "WhatsApp", "icon": "brand-whatsapp", "kind": "messaging", "color": "#25D366"},
    "telegram": {"label": "Telegram", "icon": "brand-telegram", "kind": "messaging", "color": "#229ED9"},
    "linktree": {"label": "Linktree", "icon": "brand-linktree", "kind": "web", "color": "#1E2330"},
    "beacons": {"label": "Beacons", "icon": "world", "kind": "web", "color": "#1E2330"},
    "biolink": {"label": "bio.link", "icon": "world", "kind": "web", "color": "#1E2330"},
    "paseoclub": {"label": "PaseoClub", "icon": "calendar-event", "kind": "booking", "color": "#1E2330",
                  "cta": "Agenda en PaseoClub"},
    "holdyfy": {"label": "Holdyfy", "icon": "calendar-event", "kind": "booking", "color": "#1E2330",
                "cta": "Agenda en Holdyfy"},
    "calendly": {"label": "Calendly", "icon": "calendar-event", "kind": "booking", "color": "#006BFF",
                 "cta": "Agenda en Calendly"},
    "fresha": {"label": "Fresha", "icon": "calendar-event", "kind": "booking", "color": "#1E2330",
               "cta": "Reserva en Fresha"},
    "booksy": {"label": "Booksy", "icon": "calendar-event", "kind": "booking", "color": "#1E2330",
               "cta": "Reserva en Booksy"},
    "agendapro": {"label": "AgendaPro", "icon": "calendar-event", "kind": "booking", "color": "#1E2330",
                  "cta": "Agenda en AgendaPro"},
    "maps": {"label": "Google Maps", "icon": "map-pin", "kind": "maps", "color": "#EA4335",
             "cta": "Cómo llegar"},
    "email": {"label": "Correo", "icon": "mail", "kind": "email", "color": "#1E2330"},
    "phone": {"label": "Teléfono", "icon": "phone", "kind": "phone", "color": "#1E2330"},
    "web": {"label": "Sitio web", "icon": "world", "kind": "web", "color": "#1E2330"},
}
KINDS = ("social", "booking", "web", "messaging", "maps", "email", "phone")
USE_DEFAULTS = {"lower_third": True, "follow_cta": True, "booking_cta": True, "end_card": True,
                "handle_watermark": False}
MAX_LINKS = 12

# dominio -> plataforma (se compara el host sin www./m./mobile.; tambien subdominios)
_DOMAINS: list[tuple[tuple[str, ...], str]] = [
    (("instagram.com", "instagr.am", "ig.me"), "instagram"),
    (("tiktok.com",), "tiktok"),
    (("youtube.com", "youtu.be", "youtube-nocookie.com"), "youtube"),
    (("facebook.com", "fb.com", "fb.me", "fb.watch", "m.me"), "facebook"),
    (("x.com", "twitter.com"), "x"),
    (("threads.net", "threads.com"), "threads"),
    (("linkedin.com", "lnkd.in"), "linkedin"),
    (("pin.it",), "pinterest"),
    (("snapchat.com",), "snapchat"),
    (("spotify.com", "spotify.link"), "spotify"),
    (("wa.me", "whatsapp.com", "wa.link"), "whatsapp"),
    (("t.me", "telegram.me", "telegram.dog"), "telegram"),
    (("linktr.ee", "linktree.com"), "linktree"),
    (("beacons.ai", "beacons.page"), "beacons"),
    (("bio.link",), "biolink"),
    (("paseo.club",), "paseoclub"),
    (("holdyfy.mixiuh.online", "holdyfy.com", "holdyfy.app"), "holdyfy"),
    (("calendly.com",), "calendly"),
    (("fresha.com",), "fresha"),
    (("booksy.com",), "booksy"),
    (("agendapro.com",), "agendapro"),
    (("maps.app.goo.gl", "g.page", "waze.com", "maps.apple.com"), "maps"),
]

# palabras que en un texto libre dicen de que red es un @usuario suelto
_CONTEXT = [
    ("instagram", r"instagram|insta|ig"),
    ("tiktok", r"tik\s?tok|tt"),
    ("youtube", r"youtube|yt|canal"),
    ("facebook", r"facebook|face|fb"),
    ("x", r"twitter|x"),
    ("threads", r"threads"),
    ("telegram", r"telegram"),
    ("pinterest", r"pinterest"),
    ("snapchat", r"snapchat|snap"),
    ("linkedin", r"linkedin"),
    ("spotify", r"spotify"),
]
_CTX_RX = re.compile(r"\b(" + "|".join(f"(?P<{k}>{rx})" for k, rx in _CONTEXT) + r")\b", re.I)

_TLDS = ("com|net|org|mx|club|online|ee|me|ai|link|page|io|co|app|es|ar|cl|pe|us|info|biz|shop|"
         "store|site|xyz|dev|gl|am|be|ly|to|tv|it|ch|de|fr|uk|br|ca|gt|cr|pa|uy|bo|ec|hn|sv|ni|do|"
         "pr|ve|py|cu|salon|dental|clinic|health|beauty|studio|agency|pro|work|live|world|lat")
_URL_RX = re.compile(
    r"(?:(?:https?://|www\.)[^\s<>\"']+)"
    r"|(?:(?<![@\w.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:" + _TLDS + r")\b(?:/[^\s<>\"']*)?)",
    re.I)
_EMAIL_RX = re.compile(r"(?<![\w.+-])(?:mailto:)?([A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
_HANDLE_RX = re.compile(r"(?<![\w.@/])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?)")
_PHONE_RX = re.compile(r"(?<![\w/])(\+?\(?\d[\d\s().-]{7,}\d)(?![\w/])")
_HANDLE_OK = re.compile(r"^[A-Za-z0-9_.]{1,30}$")

# rutas de cada red que NO son un usuario
_RESERVED = {
    "instagram": {"p", "reel", "reels", "tv", "explore", "accounts", "direct", "stories", "about",
                  "developer", "legal", "web", "share"},
    "tiktok": {"video", "tag", "discover", "music", "t", "embed", "search", "foryou", "live"},
    "facebook": {"profile.php", "groups", "pages", "people", "watch", "events", "share", "sharer",
                 "sharer.php", "story.php", "photo", "photo.php", "photos", "reel", "reels",
                 "marketplace", "hashtag", "login", "permalink.php", "gaming", "help"},
    "x": {"i", "intent", "home", "search", "hashtag", "share", "explore", "messages",
          "notifications", "settings", "login"},
    "threads": {"t", "search", "login"},
    "pinterest": {"pin", "search", "ideas", "today", "explore", "business"},
    "telegram": {"joinchat", "s", "c", "addstickers", "share", "proxy"},
    "linktree": {"s"},
}


# ---------------------------------------------------------------- utilidades de texto
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c))


def norm(s: str) -> str:
    """minusculas, sin acentos, solo alfanumericos."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(str(s).lower()))


def _clean(v: Any, n: int = 80) -> str:
    """Texto de un campo: sin saltos, espacios colapsados, sin emojis/control, recortado."""
    if v is None or isinstance(v, bool):
        return ""
    s = re.sub(r"\s+", " ", str(v))
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C" and not
                (unicodedata.category(ch) == "So" and ord(ch) > 0x2600))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n].strip()


# acentos de nombres y apellidos frecuentes en espanol (solo se agregan si la palabra venia SIN
# acento: lo que la persona escribio con acento se respeta)
_ACCENT_NAMES = {w: w for w in """
Rodríguez González Hernández Martínez López Pérez Sánchez Ramírez Gómez Díaz Jiménez
Fernández Gutiérrez Álvarez Domínguez Vázquez Suárez Méndez Chávez Núñez Muñoz Ibáñez Beltrán
Martín Andrés Jesús José María Sofía Lucía Raúl Iván Rubén Julián Adrián Sebastián Fabián Tomás
Nicolás Ángel Ángela Mónica Verónica Inés Belén Héctor Víctor Óscar César Rocío Nohemí Noemí Zoé
Efraín Joaquín Germán Ramón Simón Agustín Valentín Benjamín Cristóbal Álvaro Juárez Márquez
Velázquez Vélez Téllez Cortés Rincón León Calderón Guzmán Durán Galván Román Muñiz Treviño
Cárdenas Ávila Sáenz Benítez Estévez Báez Quiñones Ordóñez Solís Ríos Mejía García Castañeda Peña
Ibáñez Gálvez Pérez Chacón Sandoval Ruíz Montaño Jerónimo Úrsula Aarón Elías Isaías Matías Tobías
Mía Leonardo Lía Darío Mario Dámaso Bárbara Débora Fátima Jazmín Karín Maité Maribel Nayeli Itzé
Xóchitl Citlalli Anaís Anahí Aída Eloísa Lourdes Sonia Zoila Rosalía Natalia Emilia Amalia Eugenia
Josué Moisés Ismael Rafael Gael Ximena Jimena Renée René Tadeo Paúl Saúl Abraham Adán Esteban
Fermín Hernán Iñaki Íñigo Nazaret Nazareth Ocaña Olmedo Orduña Patiño Quiñónez Robledo Salazar
Ávalos Alcántara Aldana Almanza Andrade Aragón Arévalo Argüello Arámbula Barragán Bermúdez
Cabañas Camacho Carreón Colín Córdova Córdoba Cuéllar Dávila Echeverría Escalón Espíndola Farías
Galindo Gamboa Garcés Girón Godínez Guillén Hinojosa Jáuregui Lázaro Limón Longoria Lugo Macías
Magaña Maldonado Manríquez Marín Medellín Mendívil Monzón Morán Muñiz Nájera Nevárez Ochoa Olivárez
Ordaz Ortíz Osuna Pacheco Padilla Páez Palacios Pantoja Paredes Rendón Rentería Rosales Sáez
Santibáñez Serrano Sotelo Tamez Tapia Tovar Ulloa Uribe Valdés Valdéz Valencia Varela Vargas
Villaseñor Yáñez Zúñiga Zamudio Zavala
""".split()}
_ACCENT_NAMES = {norm(k): k for k in _ACCENT_NAMES}
for _k in ("maria", "sandoval", "mario", "leonardo", "natalia", "emilia", "amalia", "eugenia", "sonia",
           "zoila", "lourdes", "ismael", "rafael", "gael", "ximena", "jimena", "esteban", "abraham",
           "salazar", "andrade", "camacho", "galindo", "gamboa", "hinojosa", "longoria", "lugo", "ochoa",
           "osuna", "pacheco", "padilla", "palacios", "pantoja", "paredes", "rosales", "serrano", "sotelo",
           "tamez", "tapia", "tovar", "ulloa", "uribe", "valencia", "varela", "vargas", "zamudio", "zavala",
           "maldonado", "robledo", "olmedo", "aldana", "almanza", "maribel", "citlalli", "ruiz", "ortiz"):
    _ACCENT_NAMES.pop(_k, None)             # se escriben sin acento (o hay dos grafias validas)
_ACCENT_NAMES["maria"] = "María"
for _k in ("ruiz", "ortiz", "valdez"):
    _ACCENT_NAMES.pop(_k, None)

# profesiones: (masculino, femenino) con su ortografia; clave = raiz normalizada
_PROFESSIONS: list[tuple[str, str]] = [
    ("Odontólogo", "Odontóloga"), ("Dentista", "Dentista"), ("Ortodoncista", "Ortodoncista"),
    ("Endodoncista", "Endodoncista"), ("Periodoncista", "Periodoncista"),
    ("Odontopediatra", "Odontopediatra"), ("Cirujano dentista", "Cirujana dentista"),
    ("Cirujano", "Cirujana"), ("Médico", "Médica"), ("Doctor", "Doctora"), ("Pediatra", "Pediatra"),
    ("Ginecólogo", "Ginecóloga"), ("Dermatólogo", "Dermatóloga"), ("Nutriólogo", "Nutrióloga"),
    ("Nutricionista", "Nutricionista"), ("Psicólogo", "Psicóloga"), ("Psiquiatra", "Psiquiatra"),
    ("Cardiólogo", "Cardióloga"), ("Oftalmólogo", "Oftalmóloga"), ("Optometrista", "Optometrista"),
    ("Traumatólogo", "Traumatóloga"), ("Ortopedista", "Ortopedista"), ("Neurólogo", "Neuróloga"),
    ("Fisioterapeuta", "Fisioterapeuta"), ("Quiropráctico", "Quiropráctica"), ("Podólogo", "Podóloga"),
    ("Enfermero", "Enfermera"), ("Terapeuta", "Terapeuta"), ("Veterinario", "Veterinaria"),
    ("Cosmetólogo", "Cosmetóloga"), ("Esteticista", "Esteticista"), ("Estilista", "Estilista"),
    ("Maquillista", "Maquillista"), ("Barbero", "Barbera"), ("Manicurista", "Manicurista"),
    ("Abogado", "Abogada"), ("Notario", "Notaria"), ("Contador", "Contadora"), ("Arquitecto", "Arquitecta"),
    ("Ingeniero", "Ingeniera"), ("Diseñador", "Diseñadora"), ("Fotógrafo", "Fotógrafa"),
    ("Entrenador", "Entrenadora"), ("Coach", "Coach"), ("Chef", "Chef"), ("Cocinero", "Cocinera"),
    ("Panadero", "Panadera"), ("Pastelero", "Pastelera"), ("Repostero", "Repostera"),
    ("Profesor", "Profesora"), ("Maestro", "Maestra"), ("Asesor", "Asesora"), ("Consultor", "Consultora"),
    ("Agente inmobiliario", "Agente inmobiliaria"), ("Perfumista", "Perfumista"),
    ("Emprendedor", "Emprendedora"), ("Creador de contenido", "Creadora de contenido"),
    ("Especialista", "Especialista"), ("Instructor", "Instructora"), ("Tatuador", "Tatuadora"),
    ("Joyero", "Joyera"), ("Florista", "Florista"), ("Músico", "Música"), ("Cantante", "Cantante"),
    ("Mecánico", "Mecánica"), ("Electricista", "Electricista"), ("Plomero", "Plomera"),
    ("Dueño", "Dueña"), ("Fundador", "Fundadora"), ("Director", "Directora"), ("Gerente", "Gerente"),
    ("Vendedor", "Vendedora"),
]
_PROF_BY_NORM: dict[str, tuple[str, str]] = {}
for _m, _f in _PROFESSIONS:
    _PROF_BY_NORM[norm(_m)] = (_m, _f)
    _PROF_BY_NORM[norm(_f)] = (_m, _f)
# palabras de un titulo compuesto que llevan acento ("en ortodoncia", "estetica dental")
_TITLE_WORDS = {norm(w): w for w in """estética estético clínica clínico pediátrica pediátrico
periodoncia ortodoncia implantología rehabilitación odontología cosmética cosmético
deportiva deportivo nutrición psicología dermatología cirugía maxilofacial ginecología obstetricia
fisioterapia rehabilitación terapéutico terapéutica médica médico físico física""".split()}

# honorificos -> forma corta y genero
_HONOR = {"dra": ("Dra.", "f"), "doctora": ("Dra.", "f"), "dr": ("Dr.", "m"), "doctor": ("Dr.", "m"),
          "lic": ("Lic.", ""), "licenciada": ("Lic.", "f"), "licenciado": ("Lic.", "m"),
          "ing": ("Ing.", ""), "ingeniera": ("Ing.", "f"), "ingeniero": ("Ing.", "m"),
          "mtra": ("Mtra.", "f"), "maestra": ("Mtra.", "f"), "mtro": ("Mtro.", "m"), "maestro": ("Mtro.", "m"),
          "arq": ("Arq.", ""), "arquitecta": ("Arq.", "f"), "arquitecto": ("Arq.", "m"),
          "psic": ("Psic.", ""), "profa": ("Profa.", "f"), "prof": ("Prof.", ""), "chef": ("Chef", ""),
          "cd": ("C.D.", ""), "lcda": ("Lcda.", "f"), "lcdo": ("Lcdo.", "m")}
_HONOR_RX = r"(?:Dra|Dr|Doctora|Doctor|Lic|Licenciada|Licenciado|Ing|Ingeniera|Ingeniero|Mtra|Mtro|" \
            r"Maestra|Maestro|Arq|Arquitecta|Arquitecto|Psic|Profa|Prof|Chef|C\.\s?D|Lcda|Lcdo)"
_NAME_WORD = r"[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü'’-]+"
# particulas de nombres y apellidos compuestos: 'de la Garza', 'de los Ángeles', 'del Río', 'van der'
# (una sola particula compuesta por union; 'y la' no: une dos cosas distintas)
_PARTICLE = r"(?:de\s+(?:la|las|los)|van\s+der|del|de|la|las|los|y|da|van|von)"
_NAME_TAIL = r"(?:\s+(?:" + _PARTICLE + r"\s+)?" + _NAME_WORD + r"){0,4}"
_NAME_RX = re.compile(r"\b(" + _HONOR_RX + r")\.?\s+(" + _NAME_WORD + _NAME_TAIL + r")")
_BUSINESS_TYPES = r"(?i:consultorio|cl[ií]nica|tienda|negocio|marca|empresa|estudio|sal[oó]n|spa|restaurante|" \
                  r"cafeter[ií]a|barber[ií]a|despacho|farmacia|[oó]ptica|laboratorio|academia|escuela|" \
                  r"gimnasio|gym|panader[ií]a|pasteler[ií]a|perfumer[ií]a|boutique|taller|agencia|hotel|bar)"
_BUSINESS_RX = re.compile(r"\b(" + _BUSINESS_TYPES + r")\s+(?i:(?:de\s+nombre|llamad[oa]|que\s+se\s+llama)\s+)?"
                          r"[\"“']?(" + _NAME_WORD + r"(?:\s+(?:" + _PARTICLE + r"\s+)?" + _NAME_WORD + r"){0,3})")
# direccion: 'Av. Demo 123', 'Calle Uno #45 int. 2, Col. Centro, Guadalajara'
_ADDRESS_RX = re.compile(
    r"\b((?:Av(?:enida)?\.?|Calle|C\.|Blvd\.?|Boulevard|Bulevar|Calz(?:ada)?\.?|Carretera|Carr\.?|Paseo|"
    r"Privada|Priv\.?|Prol(?:ongaci[oó]n)?\.?|Circuito|Cto\.?|Periférico|Perif\.?|Andador|Camino)\s+"
    r"[A-ZÁÉÍÓÚÑ0-9][^,;\n()]{0,38}?\s(?:#\s?|No\.?\s?|N[uú]m\.?\s?)?\d{1,5}[A-Za-z]?"
    r"(?:\s*(?:int\.?|interior|local|loc\.?|depto\.?)\s*[\w-]{1,5})?"
    r"(?:,\s*(?:Col\.?|Colonia|Fracc\.?|Fraccionamiento)\s+[^,.;\n()]{2,30})?"
    r"(?:,\s*[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+(?:\s+(?:de\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+){0,2})?)")


def fix_accents(name: str) -> str:
    """'Dra. Lucia Fernandez' -> 'Dra. Lucía Fernández' (solo palabras que venian SIN acento y
    estan en la lista de nombres/apellidos; respeta mayusculas)."""
    def rep(m):
        w = m.group(0)
        if strip_accents(w) != w:
            return w                      # ya traia acento: lo que escribio la persona manda
        good = _ACCENT_NAMES.get(norm(w))
        if not good:
            return w
        if w.isupper() and len(w) > 1:
            return good.upper()
        if w[:1].isupper():
            return good[:1].upper() + good[1:]
        return good.lower()
    return re.sub(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]+", rep, str(name or ""))


def fix_title(title: str, gender: str = "") -> str:
    """Profesion con ortografia y genero: ('odontologa', '') -> 'Odontóloga';
    ('odontologo', 'f') -> 'Odontóloga'; ('especialista en ortodoncia') -> 'Especialista en ortodoncia'.
    Sin genero se respeta el que venia escrito."""
    t = _clean(title, 60)
    if not t:
        return ""
    words = t.split()
    out: list[tuple[str, bool]] = []          # (palabra, es del diccionario)
    k = 0
    while k < len(words):
        pair, span = None, 1
        if k + 1 < len(words):
            pair = _PROF_BY_NORM.get(norm(words[k] + words[k + 1]))
            span = 2 if pair else 1
        pair = pair or _PROF_BY_NORM.get(norm(words[k]))
        written = " ".join(words[k:k + span])
        if pair:
            m, f = pair
            good = f if gender == "f" else m if gender == "m" else (f if norm(written) == norm(f) else m)
            out += [(w, True) for w in good.split()]
        else:
            w = words[k]
            acc = _TITLE_WORDS.get(norm(w))
            out.append((acc if acc and strip_accents(w) == w else w, bool(acc) or len(norm(w)) <= 3))
        k += span
    res = []
    for i, (w, known) in enumerate(out):
        if i == 0:
            res.append(w[:1].upper() + w[1:].lower() if not (w.isupper() and len(w) > 1 and not known) else w)
        elif w.isupper() and len(w) > 1 and not known:
            res.append(w)                     # siglas (IMSS, UNAM)
        else:
            res.append(w.lower() if known or w.islower() or w[:1].isupper() and w[1:].islower() and
                       norm(w) in _TITLE_WORDS else w)
    return " ".join(res)


# nombres de pila frecuentes que van en SEGUNDO lugar ('José Luis', 'María Fernanda'): distinguen
# 'nombre nombre apellido' de 'nombre apellido apellido' (iniciales y nombre corto del Seguir)
_GIVEN = {norm(w) for w in """
Luis Carlos José Jose María Maria Ángel Angel Antonio Manuel Miguel Eduardo Alberto Fernando Javier
Francisco Alejandro Daniel David Pablo Andrés Guadalupe Fernanda Isabel Elena Sofía Paola Andrea
Alejandra Gabriela Patricia Teresa Victoria Esther Carmen Rosa Eugenia Cristina Lucía Paula Ana
Mariana Jesús Enrique Ernesto Ricardo Roberto Rafael Arturo Emilio Felipe Gerardo Ignacio Jorge Juan
Julio Mario Martín Raúl Ramón Sergio Tomás Valeria Camila Renata Regina Ximena Montserrat Belén
Concepción Dolores Mercedes Pilar Rosario Soledad Josefina Margarita Beatriz Silvia Verónica Leticia
Adriana Alicia Laura Lorena Claudia Carolina Natalia Daniela Estefanía Jimena Inés Emiliano Santiago
Sebastián Mateo Diego Gustavo Alfonso Armando Joaquín Agustín Alfredo Salvador Guillermo Hugo Iván
Óscar Rubén Víctor Héctor Abigail Magdalena Fátima Lourdes Luz Ángeles Angeles Cruz Paz Jazmín
""".split()}
_PARTS = {"de", "del", "la", "las", "los", "y", "da", "van", "von", "der"}


def _strip_honor(name: str) -> tuple[str, str]:
    """('Dra.', 'Lucía Fernández') de 'Dra. Lucía Fernández' (honorifico tal como vino)."""
    m = re.match(r"^\s*(" + _HONOR_RX + r"\.?)\s+", str(name or ""), flags=re.I)
    if not m:
        return "", str(name or "").strip()
    return m.group(1).strip(), str(name)[m.end():].strip()


def _name_parts(rest: str) -> tuple[list[str], list[str]]:
    """(nombres de pila, apellidos) de un nombre sin honorifico: 'Guadalupe Fernández Castañeda' ->
    (['Guadalupe'], ['Fernández', 'Castañeda']); 'José Luis Pérez' -> (['José', 'Luis'], ['Pérez']);
    'María de los Ángeles Castañeda' -> (['María de los Ángeles'], ['Castañeda']); las particulas
    ('de la Garza') van pegadas al apellido que introducen."""
    toks = rest.split()
    groups: list[str] = []
    k = 0
    while k < len(toks):
        j = k
        while j < len(toks) - 1 and norm(toks[j]) in _PARTS:
            j += 1
        groups.append(" ".join(toks[k:j + 1]))
        k = j + 1
    if not groups:
        return [], []
    given = [groups[0]]
    i = 1
    while i < len(groups) and len(groups) - i >= 2 and len(given) < 2:
        g = groups[i]
        last = g.split()[-1]
        # 'María de los Ángeles', 'María de la Luz': nombre de pila con particula
        if " " in g and norm(last) in _GIVEN and norm(g.split()[0]) == "de":
            given[-1] = given[-1] + " " + g
            i += 1
            continue
        if " " not in g and norm(g) in _GIVEN:
            given.append(g)
            i += 1
            continue
        break
    return given, groups[i:]


def name_variants(name: str) -> list[str]:
    """Del mas completo al mas corto, para que un nombre largo quepa SIN puntos suspensivos:
    'Dra. Guadalupe Fernández Castañeda' -> [completo, 'Dra. Guadalupe Fernández',
    'Guadalupe Fernández', 'Guadalupe F.']; honorifico + nombre(s) + primer apellido."""
    full = _clean(name, 80)
    if not full:
        return []
    hon, rest = _strip_honor(full)
    given, sur = _name_parts(rest)
    out = [full]

    def add(s: str) -> None:
        s = re.sub(r"\s+", " ", s).strip()
        if s and s not in out:
            out.append(s)
    if given and sur:
        g1 = given[0].split()[0]
        add(" ".join([hon] + given + sur[:1]))
        add(" ".join([hon, g1] + sur[:1]))
        add(" ".join(given + sur[:1]))
        add(" ".join([g1] + sur[:1]))
        add(f"{g1} {sur[0].split()[-1][:1]}.")
    elif hon:
        add(rest)
    return out


def initials(name: str) -> str:
    """Iniciales para el avatar: 'Dra. Lucía Fernández' -> 'LF'; 'Dr. José Luis Pérez' -> 'JP';
    'Dra. Guadalupe Fernández Castañeda' -> 'GF' (nombre + primer apellido); 'Panaderia' -> 'P';
    '@la_espiga' -> 'E'."""
    s = re.sub(r"^\s*" + _HONOR_RX + r"\.?\s+", "", str(name or ""), flags=re.I).strip().lstrip("@")
    if not s:
        return ""
    if re.search(r"[\s]", s) and not re.search(r"[._]", s):
        given, sur = _name_parts(s)
        if given and sur:
            a = given[0].split()[0]
            b = [w for w in sur[0].split() if norm(w) not in _PARTS]
            if b and re.match(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü0-9]", a) and re.match(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü0-9]", b[-1]):
                return (a[:1] + b[-1][:1]).upper()
    ws = [w for w in re.split(r"[\s._-]+", s) if w and norm(w) not in _PARTS]
    ws = [w for w in ws if re.match(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü0-9]", w)]
    if not ws:
        return ""
    if len(ws) == 1:
        return ws[0][:1].upper()
    return (ws[0][:1] + ws[1][:1]).upper() if len(ws) == 2 else (ws[0][:1] + ws[-1][:1]).upper()


# ---------------------------------------------------------------- telefonos
def _digits(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def format_phone(raw: str) -> str:
    """'5215512345678' -> '+52 55 1234 5678'; '5512345678' -> '55 1234 5678';
    '+1 555 123 4567' -> '+1 555 123 4567'."""
    d = _digits(raw)
    plus = str(raw or "").strip().startswith("+")
    if len(d) == 13 and d.startswith("521"):
        d = "52" + d[3:]
    if len(d) == 12 and d.startswith("52"):
        n = d[2:]
        return f"+52 {n[:2]} {n[2:6]} {n[6:]}" if n[:2] in ("55", "33", "81") else f"+52 {n[:3]} {n[3:6]} {n[6:]}"
    if len(d) == 10 and not plus:
        return f"{d[:2]} {d[2:6]} {d[6:]}" if d[:2] in ("55", "33", "81") else f"{d[:3]} {d[3:6]} {d[6:]}"
    if len(d) == 11 and d.startswith("1"):
        return f"+1 {d[1:4]} {d[4:7]} {d[7:]}"
    if len(d) > 10 or (plus and len(d) >= 9):
        # prefijo de pais: 1 digito (1, 7), 3 digitos (familias 2xx/35x/37x/38x/42x/50x/59x/67x/68x/
        # 69x/80x/85x/88x/96x/97x/99x) o 2 digitos
        three = ("21", "22", "23", "24", "25", "26", "29", "35", "37", "38", "42", "50", "59", "67", "68", "69",
                 "80", "85", "88", "96", "97", "99")
        k = 1 if d[0] in "17" else 3 if d[:2] in three else 2
        cc, n = d[:k], d[k:]
        if len(n) == 9:
            return f"+{cc} {n[:3]} {n[3:6]} {n[6:]}"
        if len(n) == 10:
            return f"+{cc} {n[:3]} {n[3:6]} {n[6:]}"
        return f"+{cc} " + " ".join(n[i:i + 3] for i in range(0, len(n), 3))
    return d


def _intl(raw: str) -> str:
    """Numero internacional (solo digitos) para wa.me / tel: (10 digitos = Mexico, +52)."""
    d = _digits(raw)
    if len(d) == 10:
        return "52" + d
    return d


# ---------------------------------------------------------------- enlaces
def _host_platform(host: str, path: str) -> str | None:
    h = host.lower()
    for pre in ("www.", "m.", "mobile.", "web.", "es-la.", "es.", "mx.", "open."):
        if h.startswith(pre) and h.count(".") >= 2:
            h = h[len(pre):]
    if h == "open.spotify.com":
        return "spotify"
    for doms, plat in _DOMAINS:
        for d in doms:
            if h == d or h.endswith("." + d):
                return plat
    if re.match(r"^(?:[a-z]{2}\.)?pinterest\.[a-z.]{2,6}$", h):
        return "pinterest"
    if (re.match(r"^(?:maps\.)?google\.[a-z.]{2,6}$", h) and (h.startswith("maps.") or path.startswith("/maps"))) \
            or (h == "goo.gl" and path.startswith("/maps")):
        return "maps"
    if h.startswith("holdyfy."):
        return "holdyfy"
    return None


DISPLAY_MAX = 44          # largo maximo del enlace corto en pantalla (el renderer ajusta la fuente)


def _short_web(host: str, segs: list[str], skip: tuple = (), keep_slug: bool = False) -> str:
    """Texto corto de una web: dominio + ultima parte de la ruta si es legible. `keep_slug`
    (paginas de reservas): el slug identifica a la persona y nunca se pierde; si no cabe con el
    dominio completo se acorta el dominio a la marca ('holdyfy/dra-maria-de-los-angeles')."""
    h = re.sub(r"^www\.", "", host.lower())
    segs = [s for s in segs if s and s.lower() not in skip]
    if segs:
        last = urllib.parse.unquote(segs[-1])
        if re.fullmatch(r"[\w.~-]{1,60}", last) and not re.fullmatch(r"\d{4,}|[0-9a-f]{12,}", last, re.I):
            cand = f"{h}/{last}"
            if len(cand) <= (DISPLAY_MAX if keep_slug else 36):
                return cand
            if keep_slug and h.count(".") == 1 and len(cand) <= DISPLAY_MAX + 8:
                return cand                    # dominio corto (paseo.club, calendly.com): completo
            if keep_slug:
                brand = h.split(".")[0]
                cand = f"{brand}/{last}"
                return cand if len(cand) <= DISPLAY_MAX + 8 else last[:DISPLAY_MAX]
    return h


def _mk(platform: str, url: str, handle: str = "", display: str = "", label: str = "",
        ambiguous: bool = False) -> dict:
    meta = PLATFORMS[platform]
    h = ("@" + handle.lstrip("@")) if handle else ""
    out = {"url": url, "platform": platform, "kind": meta["kind"], "handle": h,
           "display": display or h or meta["label"], "label": label or meta.get("cta") or meta["label"],
           "icon": meta["icon"], "primary": False}
    if ambiguous:
        out["ambiguous"] = True
    return out


def _clean_handle(h: str) -> str:
    h = urllib.parse.unquote(str(h or "")).strip().lstrip("@").strip()
    h = re.sub(r"[?#].*$", "", h).strip("/").rstrip(".")
    return h if _HANDLE_OK.match(h) else ""


def _from_url(raw: str) -> dict | None:
    s = raw.strip().strip("<>\"'").rstrip(").,;:!?]}»”")
    if not s:
        return None
    if s.lower().startswith("mailto:"):
        em = s[7:].split("?")[0]
        return _mk("email", "mailto:" + em.lower(), display=em.lower()) if "@" in em else None
    if s.lower().startswith("tel:"):
        d = _digits(s[4:])
        return _mk("phone", "tel:+" + _intl(d), display=format_phone(s[4:])) if len(d) >= 8 else None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I):
        s = "https://" + s
    try:
        u = urllib.parse.urlsplit(s)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if not host or "." not in host:
        return None
    path = re.sub(r"/{2,}", "/", u.path or "")
    segs = [x for x in path.split("/") if x]
    q = urllib.parse.parse_qs(u.query)
    plat = _host_platform(host, path)
    bare = re.sub(r"^www\.", "", host)
    clean_url = f"https://{bare}{path.rstrip('/') if path not in ('', '/') else ''}"
    first = segs[0] if segs else ""
    if plat == "instagram":
        if host.endswith("ig.me") and segs[:1] == ["m"] and len(segs) > 1:
            first = segs[1]
        if first.lower() == "stories" and len(segs) > 1:
            first = segs[1]
        h = _clean_handle(first) if first.lower() not in _RESERVED["instagram"] else ""
        if h:
            return _mk("instagram", f"https://instagram.com/{h}", h)
        return _mk("instagram", clean_url)
    if plat == "tiktok":
        at = next((x for x in segs if x.startswith("@")), "")
        h = _clean_handle(at)
        if h:
            return _mk("tiktok", f"https://tiktok.com/@{h}", h)
        return _mk("tiktok", clean_url)
    if plat == "youtube":
        if host.endswith("youtu.be"):
            return _mk("youtube", clean_url)
        if first.startswith("@") and _clean_handle(first):
            h = _clean_handle(first)
            return _mk("youtube", f"https://youtube.com/@{h}", h)
        if first.lower() in ("c", "user") and len(segs) > 1:
            nm = urllib.parse.unquote(segs[1])
            return _mk("youtube", f"https://youtube.com/{first.lower()}/{segs[1]}", display=nm[:30])
        return _mk("youtube", clean_url + (f"?v={q['v'][0]}" if q.get("v") else ""))
    if plat == "facebook":
        if first.lower() == "pages" and len(segs) > 1:
            nm = urllib.parse.unquote(segs[1]).replace("-", " ")
            return _mk("facebook", clean_url, display=nm[:30])
        h = _clean_handle(first) if first.lower() not in _RESERVED["facebook"] else ""
        if h and not h.isdigit():
            return _mk("facebook", f"https://facebook.com/{h}", h)
        return _mk("facebook", clean_url + (f"?id={q['id'][0]}" if q.get("id") else ""))
    if plat == "x":
        h = _clean_handle(first) if first.lower() not in _RESERVED["x"] else ""
        if h:
            return _mk("x", f"https://x.com/{h}", h)
        return _mk("x", clean_url)
    if plat == "threads":
        h = _clean_handle(first) if first.lower().lstrip("@") not in _RESERVED["threads"] else ""
        if h:
            return _mk("threads", f"https://threads.net/@{h}", h)
        return _mk("threads", clean_url)
    if plat == "linkedin":
        if first.lower() in ("in", "company", "school", "showcase") and len(segs) > 1:
            slug = urllib.parse.unquote(segs[1])
            return _mk("linkedin", f"https://linkedin.com/{first.lower()}/{slug}", display=f"{first.lower()}/{slug}"[:36])
        return _mk("linkedin", clean_url)
    if plat == "pinterest":
        if host == "pin.it":
            return _mk("pinterest", clean_url)
        h = _clean_handle(first) if first.lower() not in _RESERVED["pinterest"] else ""
        if h:
            return _mk("pinterest", f"https://pinterest.com/{h}", h)
        return _mk("pinterest", clean_url)
    if plat == "snapchat":
        cand = segs[1] if first.lower() == "add" and len(segs) > 1 else (first if first.startswith("@") else "")
        h = _clean_handle(cand)
        if h:
            return _mk("snapchat", f"https://snapchat.com/add/{h}", h)
        return _mk("snapchat", clean_url)
    if plat == "spotify":
        return _mk("spotify", clean_url)
    if plat == "whatsapp":
        num = ""
        if host.endswith("wa.me") and first and first.lower() != "message":
            num = _digits(first)
        elif q.get("phone"):
            num = _digits(q["phone"][0])
        if len(num) >= 8:
            n = _intl(num)
            return _mk("whatsapp", f"https://wa.me/{n}", display=format_phone(n))
        if host.startswith("chat."):
            return _mk("whatsapp", clean_url, display="Grupo de WhatsApp")
        return _mk("whatsapp", clean_url)
    if plat == "telegram":
        h = _clean_handle(first) if first.lower() not in _RESERVED["telegram"] and not first.startswith("+") else ""
        if h:
            return _mk("telegram", f"https://t.me/{h}", h)
        return _mk("telegram", clean_url)
    if plat in ("linktree", "beacons", "biolink"):
        h = _clean_handle(first) if first.lower() not in _RESERVED.get(plat, set()) else ""
        disp = f"{bare}/{h}" if h else bare
        return _mk(plat, f"https://{bare}/{h}" if h else clean_url, display=disp)
    if plat in ("paseoclub", "holdyfy") and not segs:
        # la portada de la plataforma no es una pagina para agendar con alguien: es su sitio web
        # (un video de la propia marca no debe decir "Agenda en PaseoClub")
        return _mk("web", clean_url, display=bare)
    if plat == "paseoclub":
        disp = _short_web(host, segs, ("business", "negocio", "b", "p"), keep_slug=True)
        return _mk("paseoclub", clean_url, display=disp)
    if plat == "holdyfy":
        disp = _short_web(host, segs, ("profesional", "pro", "p", "negocio"), keep_slug=True)
        return _mk("holdyfy", clean_url, display=disp)
    if plat == "calendly":
        return _mk(plat, clean_url, display=_short_web(host, segs[:1], keep_slug=True))
    if plat == "agendapro":
        return _mk(plat, clean_url, display=_short_web(host, segs, ("mx", "co", "cl", "es", "pe", "ar", "sites"),
                                                       keep_slug=True))
    if plat in ("fresha", "booksy"):
        return _mk(plat, clean_url, display=_short_web(host, segs, ("a", "es", "es-mx", "lvp", "es-es")))
    if plat == "maps":
        return _mk("maps", s.split("#")[0], display="Cómo llegar")
    return _mk("web", clean_url, display=_short_web(host, segs))


def _ctx_platform(before: str) -> str | None:
    """Red mas cercana ANTES de un @usuario ("su instagram es @x", "TikTok: @x")."""
    last = None
    b = before[-48:]
    for m in _CTX_RX.finditer(b):
        for k, _rx in _CONTEXT:
            if m.group(k):
                # 'x' suelta solo como red si va como etiqueta ('x: @a', 'en X como @a', 'x @a');
                # 'twitter' siempre es la red
                if k == "x" and m.group(k).lower() == "x" and \
                        not re.match(r"\s*(?:[:(=]|es\b|como\b|$)", b[m.end():], re.I):
                    continue
                last = k
    return last


_HANDLE_PLATS = ("instagram", "tiktok", "youtube", "facebook", "x", "threads", "telegram", "pinterest",
                 "snapchat")


def parse_link(text: Any, hint: str | None = None) -> dict | None:
    """Un enlace -> dict (ver modulo) o None. Acepta URL (con o sin https://), dominio suelto,
    '@usuario' (con `hint` o palabra de contexto: 'tiktok @x'; si no, Instagram marcado como
    `ambiguous`), correo, 'mailto:', 'tel:' o un telefono (con 'whatsapp' cerca -> WhatsApp)."""
    s = re.sub(r"@{2,}", "@", _clean(text, 400))
    if not s:
        return None
    hint = hint if hint in PLATFORMS else None
    em = _EMAIL_RX.search(s)
    if em and not _URL_RX.search(s.replace(em.group(0), " ")) and not s.lower().startswith("http"):
        e = em.group(1).lower()
        return _mk("email", "mailto:" + e, display=e)
    mu = _URL_RX.search(s)
    if mu:
        r = _from_url(mu.group(0))
        if r is not None:
            return r
    mh = _HANDLE_RX.search(s)
    if mh:
        h = _clean_handle(mh.group(1))
        if h:
            plat = hint if hint in _HANDLE_PLATS else _ctx_platform(s[:mh.start()])
            if plat is None and re.search(r"(?<!\w)(?:tiktok|tik tok)", s, re.I):
                plat = "tiktok"
            amb = plat is None
            plat = plat or "instagram"
            return _mk(plat, _profile_url(plat, h), h, ambiguous=amb)
    if hint in _HANDLE_PLATS and _HANDLE_OK.match(s.lstrip("@")):
        h = _clean_handle(s)
        return _mk(hint, _profile_url(hint, h), h) if h else None
    mp = _PHONE_RX.search(s)
    if mp and len(_digits(mp.group(1))) >= 8:
        num = mp.group(1)
        wa = hint == "whatsapp" or re.search(r"whats|wsp|wha?ts?app|\bwa\b", s, re.I)
        if wa:
            n = _intl(num)
            return _mk("whatsapp", f"https://wa.me/{n}", display=format_phone(n))
        return _mk("phone", "tel:+" + _intl(num), display=format_phone(num))
    return None


def _profile_url(plat: str, h: str) -> str:
    return {"instagram": f"https://instagram.com/{h}", "tiktok": f"https://tiktok.com/@{h}",
            "youtube": f"https://youtube.com/@{h}", "facebook": f"https://facebook.com/{h}",
            "x": f"https://x.com/{h}", "threads": f"https://threads.net/@{h}",
            "telegram": f"https://t.me/{h}", "pinterest": f"https://pinterest.com/{h}",
            "snapchat": f"https://snapchat.com/add/{h}"}.get(plat, "")


def _link_key(l: dict) -> tuple:
    if l.get("handle"):
        return (l["platform"], l["handle"].lower())
    return (l["platform"], re.sub(r"^https?://(www\.)?", "", str(l.get("url") or "").lower()).rstrip("/"))


def dedupe_links(links: list[dict]) -> list[dict]:
    """Sin duplicados (misma red + usuario, o misma URL). Un @usuario AMBIGUO (sin red) que ya
    aparece con otra red se une a ella ('@x' suelto + 'instagram.com/x' = un solo enlace)."""
    out: list[dict] = []
    seen: dict[tuple, int] = {}
    known_handles = {l["handle"].lower(): l["platform"] for l in links
                     if l.get("handle") and not l.get("ambiguous")}
    for l in links:
        if l.get("ambiguous") and l.get("handle", "").lower() in known_handles:
            continue
        k = _link_key(l)
        if k in seen:
            if l.get("primary"):
                out[seen[k]]["primary"] = True
            keep = out[seen[k]]
            if l.get("from_field") is True:          # el del campo de contacto sigue siendolo
                keep["from_field"] = True
            for mk in ("photo", "logo"):          # la foto/logo del duplicado no se pierde
                if isinstance(l.get(mk), dict) and not keep.get(mk):
                    keep[mk] = l[mk]
                    src = (l.get("media_src") or {}).get(mk)
                    if src:
                        keep["media_src"] = dict(keep.get("media_src") or {}, **{mk: src})
                    if l.get("media_for") and not keep.get("media_for"):
                        keep["media_for"] = l["media_for"]
            continue
        seen[k] = len(out)
        out.append(l)
    return out


def find_links(text: Any) -> list[dict]:
    """Todos los enlaces de un texto libre, en orden de aparicion y sin duplicados."""
    s = str(text or "")
    found: list[tuple[int, dict]] = []
    taken: list[tuple[int, int]] = []

    def free(a, b):
        return not any(a < y and x < b for x, y in taken)

    for m in _EMAIL_RX.finditer(s):
        e = m.group(1).lower()
        found.append((m.start(), _mk("email", "mailto:" + e, display=e)))
        taken.append((m.start(), m.end()))
    for m in _URL_RX.finditer(s):
        if not free(m.start(), m.end()):
            continue
        r = _from_url(m.group(0))
        if r is not None:
            found.append((m.start(), r))
            taken.append((m.start(), m.end()))
    for m in _HANDLE_RX.finditer(s):
        if not free(m.start(), m.end()):
            continue
        h = _clean_handle(m.group(1))
        if not h:
            continue
        plat = _ctx_platform(s[max(0, m.start() - 48):m.start()])
        amb = plat is None
        plat = plat or "instagram"
        found.append((m.start(), _mk(plat, _profile_url(plat, h), h, ambiguous=amb)))
        taken.append((m.start(), m.end()))
    for m in _BARE_HANDLE_RX.finditer(s):
        # sin @: con 'Red:' vale cualquier usuario; con 'es/como' solo uno con forma de usuario
        # ('dra.demo', 'clinica_22'), no una palabra ('su instagram es muy bueno')
        a, b = m.start("h"), m.end("h")
        h = _clean_handle(m.group("h"))
        if not h or not free(a, b) or norm(h) in _NOT_HANDLE or len(h) < 3 or h.isdigit():
            continue
        if not m.group("colon") and not re.search(r"[._0-9]", h):
            continue
        plat = _ctx_platform(s[max(0, m.start()):a]) or _ctx_platform(m.group(1) + ":")
        if plat not in _HANDLE_PLATS:
            continue
        found.append((a, _mk(plat, _profile_url(plat, h), h)))
        taken.append((a, b))
    for m in _PHONE_RX.finditer(s):
        if not free(m.start(), m.end()) or len(_digits(m.group(1))) < 10:
            continue
        before = s[max(0, m.start() - 36):m.start()]
        num = m.group(1)
        if re.search(r"whats|wsp|wha?ts?app|\bwa\b", before, re.I):
            n = _intl(num)
            found.append((m.start(), _mk("whatsapp", f"https://wa.me/{n}", display=format_phone(n))))
        else:
            found.append((m.start(), _mk("phone", "tel:+" + _intl(num), display=format_phone(num))))
        taken.append((m.start(), m.end()))
    found.sort(key=lambda x: x[0])
    return dedupe_links([l for _k, l in found])


# ---------------------------------------------------------------- perfil
def _norm_avatar(v: Any) -> dict | None:
    """Avatar = medio propio (POST /sources): {sid, type:'image', name?, w?, h?, crop?}. Nunca
    una ruta."""
    if not isinstance(v, dict):
        return None
    sid = v.get("sid")
    if not isinstance(sid, str) or not re.match(r"^[A-Za-z0-9_-]{1,64}$", sid.strip()):
        return None
    out: dict[str, Any] = {"sid": sid.strip(), "type": "image"}
    if isinstance(v.get("name"), str):
        out["name"] = _clean(v["name"], 120)
    for k in ("w", "h"):
        try:
            if v.get(k) is not None and int(v[k]) > 0:
                out[k] = int(v[k])
        except (TypeError, ValueError):
            pass
    c = v.get("crop")
    if isinstance(c, (list, tuple)) and len(c) == 4:
        try:
            out["crop"] = [min(1.0, max(0.0, float(x))) for x in c]
        except (TypeError, ValueError):
            pass
    if isinstance(v.get("thumb"), str) and v["thumb"].startswith("/"):
        out["thumb"] = v["thumb"][:200]
    return out


def _norm_link(v: Any) -> dict | None:
    """Enlace del perfil saneado: se vuelve a reconocer (plataforma, handle, display) desde su
    `url`/`handle`; lo que el usuario edito a mano (label, display) se respeta si es texto."""
    if isinstance(v, str):
        v = {"url": v}
    if not isinstance(v, dict):
        return None
    hint = v.get("platform") if v.get("platform") in PLATFORMS else None
    src = v.get("url") or v.get("handle") or ""
    base = parse_link(src, hint=hint) if src else None
    if base is None and hint and (v.get("display") or v.get("handle")):
        h = _clean_handle(v.get("handle") or "")
        base = _mk(hint, _profile_url(hint, h) if h else "", h)
    if base is None:
        return None
    # el usuario cambio el @usuario de una red en el Studio: la url era la del perfil ARMADA con el
    # usuario anterior (instagram.com/<usuario>) -> se rehace con el nuevo (antes la url mandaba y el
    # cambio se perdia en silencio, con la foto de la cuenta vieja). Una url propia con otra ruta (una
    # publicacion, un canal) no es la del usuario: esa no se toca.
    edited_from = ""
    nh = _clean_handle(v.get("handle")) if isinstance(v.get("handle"), str) and v.get("url") else ""
    oh = str(base.get("handle") or "").lstrip("@")
    if nh and oh and nh.lower() != oh.lower() and base["platform"] in _HANDLE_PLATS and \
            _url_key(base["url"]) == _url_key(_profile_url(base["platform"], oh)):
        edited_from = oh
        amb = bool(base.get("ambiguous"))
        base = _mk(base["platform"], _profile_url(base["platform"], nh), nh, ambiguous=amb)
    old = base["platform"]
    switched = False
    if hint and hint != old and base.get("handle"):
        h = base["handle"].lstrip("@")
        # el Studio cambio la red de un @usuario (el parse lo tomo como Instagram o el usuario se
        # equivoco de red): la url era la del perfil armada con ese usuario -> se rehace en la red
        # elegida. Una url propia de otra red (con ruta distinta) manda sobre el selector.
        own_url = str(v.get("url") or "").rstrip("/").lower() in ("", _profile_url(old, h).lower())
        if hint in _HANDLE_PLATS and (base.get("ambiguous") or v.get("ambiguous") is True or own_url):
            base = _mk(hint, _profile_url(hint, h), h)
            switched = True
    base.pop("ambiguous", None)
    if v.get("ambiguous") is True and base["platform"] == "instagram" and not switched:
        base["ambiguous"] = True
    auto = {PLATFORMS[old]["label"], PLATFORMS[old].get("cta") or "", "@" + str(v.get("handle") or "").lstrip("@")}
    for k, n in (("label", 40), ("display", 48)):
        t = _clean(v.get(k), n)
        # al cambiar de red, la etiqueta/texto automaticos de la red anterior no se arrastran; al
        # cambiar el @usuario, tampoco el texto que mostraba el usuario anterior
        if t and not (switched and t in auto | {base.get("handle", "")}) and \
                not (edited_from and t.lstrip("@").lower() == edited_from.lower()):
            base[k] = t
    base["primary"] = bool(v.get("primary"))
    # foto/logo automaticos de OTRA cuenta (otra red, otro @usuario u otra url) nunca se quedan
    stale = switched or bool(edited_from) or \
        (isinstance(v.get("media_for"), str) and _url_key(v["media_for"]) != _url_key(base["url"]))
    _keep_link_media(base, v, stale)
    # enlace de telefono / WhatsApp / correo: si es el del campo del perfil (ver normalize_profile)
    if base["platform"] in FIELD_LINKS.values() and isinstance(v.get("from_field"), bool):
        base["from_field"] = v["from_field"]
    return base


# ---------------------------------------------------------------- telefono, WhatsApp y correo
# Cada campo (whatsapp, phone, email) tiene SU enlace (wa.me/..., tel:..., mailto:...), marcado
# `from_field: true`; los que el usuario agrego aparte van con `from_field: false`. El CAMPO manda:
# cambiarlo rehace su enlace (antes el enlace viejo se quedaba y la tarjeta final seguia con el numero
# anterior) y vaciarlo lo quita (antes se volvia a llenar con el enlace). Un perfil anterior a la
# marca (sin `from_field`): su primer enlace de esa red es el del campo.
FIELD_LINKS = {"whatsapp": "whatsapp", "phone": "phone", "email": "email"}


def _field_link(raw: str, plat: str) -> dict | None:
    """Enlace del valor de un campo (numero o correo) o None si no es uno de esa red."""
    l = parse_link(raw, hint=plat) if raw else None
    return l if l is not None and l["platform"] == plat else None


def _link_field_value(l: dict, plat: str) -> str:
    """Valor del campo para un enlace de esa red ('' si no da uno: wa.me/message/...)."""
    url = str(l.get("url") or "")
    canon = ""
    if plat == "email" and url.lower().startswith("mailto:"):
        canon = url[7:]
    elif plat == "phone" and url.lower().startswith("tel:") and len(_digits(url)) >= 8:
        canon = "+" + _digits(url)
    elif plat == "whatsapp":
        m = re.match(r"^https?://(?:api\.)?wa\.me/(\d{8,})/?$", url, re.I)
        canon = "+" + m.group(1) if m else ""
    for cand in (str(l.get("display") or ""), canon):
        x = _field_link(cand, plat) if cand else None
        if x is not None and _url_key(x["url"]) == _url_key(url):
            return x["display"]
    return ""


def _carry_link_text(old: dict, new: dict, plat: str) -> None:
    """El enlace del campo cambio de numero: el texto propio del usuario (etiqueta del boton, texto
    que no es el numero) y la estrella se quedan."""
    auto = {PLATFORMS[plat]["label"], PLATFORMS[plat].get("cta") or ""}
    lab = str(old.get("label") or "")
    if lab and lab not in auto:
        new["label"] = lab
    disp = str(old.get("display") or "")
    if disp and not _digits(disp) and "@" not in disp and disp not in auto:
        new["display"] = disp
    if old.get("primary"):
        new["primary"] = True


def _field_links(out: dict, p: dict, links: list[dict]) -> list[dict]:
    """Pone al dia el enlace de cada campo de contacto (FIELD_LINKS) y el campo -> enlaces."""
    for key, plat in FIELD_LINKS.items():
        raw = _clean(p.get(key), 80)
        out[key] = raw
        fl = _field_link(raw, plat)
        mine = [l for l in links if l["platform"] == plat]
        if fl is not None:
            out[key] = fl["display"]
            keep = next((l for l in mine if _url_key(l["url"]) == _url_key(fl["url"])), None)
            if keep is None:
                # el campo cambio: su enlace (o el primero de un perfil sin marca) se rehace en su lugar
                tgt = next((l for l in mine if l.get("from_field") is True), None) or \
                    next((l for l in mine if "from_field" not in l), None)
                if tgt is not None:
                    _carry_link_text(tgt, fl, plat)
                    links[next(i for i, x in enumerate(links) if x is tgt)] = fl
                else:
                    links.append(fl)
                keep = fl
            keep["from_field"] = True
            # otro enlace marcado de esa red tenia el valor ANTERIOR del campo: fuera
            links = [l for l in links if not (l["platform"] == plat and l is not keep and l.get("from_field") is True)]
        else:
            if key == "phone" and raw and len(_digits(raw)) >= 8:
                out[key] = format_phone(raw)
            # campo vaciado (o que ya no es un numero/correo): su enlace se va
            links = [l for l in links if not (l["platform"] == plat and l.get("from_field") is True)]
            if not raw:
                # sin campo: lo toma de un enlace que el usuario agrego (o de un perfil sin marca)
                for l in links:
                    v = _link_field_value(l, plat) if l["platform"] == plat else ""
                    if v:
                        out[key] = v
                        l["from_field"] = True
                        break
        for l in links:
            if l["platform"] == plat and l.get("from_field") is not True:
                l["from_field"] = False
    return links


# ---------------------------------------------------------------- foto y logo de los enlaces
# Cada enlace puede traer su foto (la vista previa de la pagina: og:image; en Instagram la foto de
# perfil) y el logo del sitio (icono), descargados SOLOS por el servidor (motion/linkmedia.py) o
# subidos por el usuario:
#   photo / logo: medio propio {sid, type:'image', name?, w?, h?, src?}   (nunca una ruta)
#   media_src {photo, logo}: 'auto' (lo descargo el servidor) | 'user' (lo subio el usuario: nunca
#       se pisa ni se vuelve a descargar)
#   media_off {photo, logo}: el usuario lo quito (no se vuelve a descargar solo)
#   media_for: url del enlace para la que se descargo lo 'auto' (si el enlace cambia, se descarta)
LINK_MEDIA_KINDS = ("photo", "logo")
MEDIA_SRC = ("auto", "user")
# enlaces sin vista previa util (correo, telefono, mapa, WhatsApp: su imagen es la de la app)
NO_PREVIEW = {"email", "phone", "maps", "whatsapp"}
_HTTP_RX = re.compile(r"^https?://[^\s<>\"']{1,500}$", re.I)


def _url_key(u: Any) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(u or "").strip().lower()).rstrip("/")


def _norm_media(v: Any) -> dict | None:
    """Foto o logo de un enlace: medio propio saneado como el avatar (sid, nunca una ruta) + `src`
    (url http(s) de la imagen de origen, solo informativa)."""
    out = _norm_avatar(v)
    if out is not None and isinstance(v.get("src"), str) and _HTTP_RX.match(v["src"].strip()):
        out["src"] = v["src"].strip()
    return out


def _keep_link_media(base: dict, v: dict, stale: bool) -> None:
    """Pasa a `base` (enlace reconocido de nuevo) la foto/logo de `v` saneados. Lo 'auto' de otra
    url o de otra red (el enlace cambio) se descarta; lo quitado por el usuario (media_off) no
    vuelve salvo que el mismo usuario suba uno ('user'). Sin media_src un medio es del usuario."""
    src_in = v.get("media_src") if isinstance(v.get("media_src"), dict) else {}
    off_in = v.get("media_off") if isinstance(v.get("media_off"), dict) else {}
    src: dict[str, str] = {}
    off: dict[str, bool] = {}
    for k in LINK_MEDIA_KINDS:
        if off_in.get(k) is True:
            off[k] = True
        m = _norm_media(v.get(k))
        if m is None:
            continue
        s = src_in.get(k) if src_in.get(k) in MEDIA_SRC else "user"
        if s == "auto" and (stale or off.get(k)):
            continue
        base[k] = m
        src[k] = s
    if src:
        base["media_src"] = src
    if off:
        base["media_off"] = off
    mf = v.get("media_for")
    if "auto" in src.values() and isinstance(mf, str) and _HTTP_RX.match(mf.strip()):
        base["media_for"] = mf.strip()


def link_media(link: dict | None, kind: str) -> dict | None:
    """Foto ('photo') o logo ('logo') utilizable del enlace (None si no hay o el usuario lo quito)."""
    if not isinstance(link, dict) or kind not in LINK_MEDIA_KINDS:
        return None
    if (link.get("media_off") or {}).get(kind) and (link.get("media_src") or {}).get(kind) != "user":
        return None
    m = link.get(kind)
    return m if isinstance(m, dict) and m.get("sid") else None


def link_for(p: dict | None, platform: str | None, handle: str | None = None) -> dict | None:
    """Enlace del perfil de esa red (con ese @usuario si se da; si no coincide ninguno, None)."""
    if not platform:
        return None
    h = str(handle or "").lower()
    cands = [l for l in all_links(p) if l.get("platform") == platform]
    if h:
        return next((l for l in cands if str(l.get("handle") or "").lower() == h), None)
    return cands[0] if cands else None


def media_wanted(link: dict | None) -> list[str]:
    """Lo que el servidor puede descargar SOLO para este enlace: foto/logo que no subio ni quito
    el usuario. [] para enlaces sin pagina (correo, telefono, mapa, WhatsApp) o sin http(s)."""
    if not isinstance(link, dict) or link.get("platform") in NO_PREVIEW or \
            not _HTTP_RX.match(str(link.get("url") or "")):
        return []
    from .linkmedia import OWN_ICON          # redes con icono propio: su logo no se descarga
    src = link.get("media_src") or {}
    off = link.get("media_off") or {}
    return [k for k in LINK_MEDIA_KINDS if src.get(k) != "user" and not off.get(k)
            and not (k == "logo" and link.get("platform") in OWN_ICON)]


def media_sids(p: dict | None) -> set[str]:
    """sids de los medios del perfil (avatar + foto/logo de cada enlace)."""
    out = set()
    av = (p or {}).get("avatar")
    if isinstance(av, dict) and av.get("sid"):
        out.add(str(av["sid"]))
    for l in all_links(p):
        for k in LINK_MEDIA_KINDS:
            m = l.get(k)
            if isinstance(m, dict) and m.get("sid"):
                out.add(str(m["sid"]))
    return out


def normalize_profile(p: Any) -> dict | None:
    """Perfil saneado o None si no trae nada util. Campos: name, title, business, tagline,
    links [...] (cada uno con photo/logo/media_src/media_off/media_for opcionales: ver
    _keep_link_media), phone, whatsapp, email, address, avatar, use {lower_third, follow_cta,
    booking_cta, end_card, handle_watermark} (+ id y gender opcionales)."""
    if not isinstance(p, dict):
        return None
    out: dict[str, Any] = {"name": _clean(p.get("name"), 80), "title": _clean(p.get("title"), 60),
                           "business": _clean(p.get("business"), 80), "tagline": _clean(p.get("tagline"), 120)}
    g = p.get("gender")
    if g in ("f", "m"):
        out["gender"] = g
    links = dedupe_links([l for l in (_norm_link(x) for x in (p.get("links") or [])[:40]) if l])
    # telefono, WhatsApp y correo: el campo y su enlace siempre dicen lo mismo (_field_links)
    links = _field_links(out, p, links)
    out["address"] = _clean(p.get("address"), 120)
    links = links[:MAX_LINKS]
    prim = [l for l in links if l.get("primary")]
    for l in links:
        l["primary"] = False
    # principal: la marcada; si no, la primera red con @usuario de red CONFIRMADA (un @ ambiguo
    # solo si no hay otra)
    socials = [l for l in links if l["kind"] == "social" and l.get("handle")]
    target = prim[0] if prim else next((l for l in socials if not l.get("ambiguous")),
                                       socials[0] if socials else None)
    if target is not None:
        target["primary"] = True
    out["links"] = links
    av = _norm_avatar(p.get("avatar"))
    if av:
        out["avatar"] = av
    u = p.get("use") if isinstance(p.get("use"), dict) else {}
    out["use"] = {k: bool(u.get(k, d)) if not isinstance(u.get(k), str) else u.get(k) in ("1", "true", "si")
                  for k, d in USE_DEFAULTS.items()}
    pid = p.get("id")
    if isinstance(pid, str) and re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", pid):
        out["id"] = pid
    if not any(out[k] for k in ("name", "title", "business", "links", "phone", "whatsapp", "email",
                                "address")):
        return None
    return out


def all_links(p: dict | None) -> list[dict]:
    return list((p or {}).get("links") or [])


def primary_social(p: dict | None) -> dict | None:
    """Red principal (la del CTA Seguir): la marcada `primary` con @usuario, o la primera red
    confirmada (un @ ambiguo solo si no hay otra)."""
    links = all_links(p)
    for l in links:
        if l.get("primary") and l.get("kind") == "social" and l.get("handle"):
            return l
    socials = [l for l in links if l.get("kind") == "social" and l.get("handle")]
    return next((l for l in socials if not l.get("ambiguous")), socials[0] if socials else None)


def booking_link(p: dict | None) -> dict | None:
    """Enlace de la CTA de reservas: pagina de reservas; si no, la web propia (o link en bio)."""
    links = all_links(p)
    return next((l for l in links if l.get("kind") == "booking"), None) or \
        next((l for l in links if l.get("kind") == "web"), None)


# accion de la pastilla cuando el enlace es una web ('Sitio web' no invita a nada): segun el rubro
_WEB_CTA = [
    (r"tienda|perfum|boutique|joyer|ropa|moda|zapat|accesori|cosmet|maquillaj|shop|store|venta|vend|"
     r"catalog|product", "Compra en línea"),
    (r"restaurante|cafe|comida|cocina|chef|pasteler|panader|reposter|pizz|taquer|antojit|postre|"
     r"hamburgues|sushi|marisc", "Ordena en línea"),
    (r"hotel|hostal|caba[nñ]a|hospedaje|viaje|tour|glamping", "Reserva en línea"),
    (r"inmobiliari|bienes\s+ra[ií]ces|propiedad", "Ve las propiedades"),
    (r"curso|clase|academia|escuela|coach|mentor|profesor|maestr|taller", "Inscríbete aquí"),
]


def link_cta(link: dict | None, context: str = "") -> str:
    """Etiqueta de accion del enlace de la pastilla de reservas/web. Respeta la que el usuario
    escribio; si es la automatica de una web ('Sitio web'), una accion segun el rubro
    (`context`: rubro, profesion, negocio): 'Compra en línea' en una tienda, 'Ordena en línea' en
    un restaurante; 'Todos mis links' en Linktree/Beacons/bio.link; si no, 'Visita mi página'."""
    if not link:
        return ""
    lab = str(link.get("label") or "")
    plat = link.get("platform") or "web"
    meta = PLATFORMS.get(plat) or PLATFORMS["web"]
    if link.get("kind") != "web" or (lab and lab != meta["label"]):
        return lab or meta.get("cta") or meta["label"]
    if plat in ("linktree", "beacons", "biolink"):
        return "Todos mis links"
    ctx = strip_accents(str(context or "").lower())
    for rx, lab_ in _WEB_CTA:
        if re.search(rx, ctx):
            return lab_
    return "Visita mi página"


def uses(p: dict | None, key: str) -> bool:
    if not p:
        return False
    u = p.get("use") if isinstance(p.get("use"), dict) else {}
    return bool(u.get(key, USE_DEFAULTS.get(key, False)))


def entities_from(p: dict | None) -> tuple[dict, list[str]]:
    """Entidades que el perfil FIJA (mandan sobre lo que el director infiere) y sus claves.
    name_src 'perfil' + name_confirmed: el nombre sale en rotulo y CTA; un nombre leido en un
    letrero nunca se usa si hay perfil."""
    if not p:
        return {}, []
    ent: dict[str, Any] = {}
    if p.get("name"):
        ent.update(name=p["name"], name_src="perfil", name_confirmed=True)
    if p.get("title"):
        ent["role"] = p["title"]
    if p.get("business"):
        ent["business"] = p["business"]
    ps = primary_social(p)
    if ps:
        ent["handle"] = ps["handle"]
        ent["handle_platform"] = ps["platform"]
    bk = booking_link(p)
    if bk:
        ent["link_label"] = link_cta(bk, " ".join(str(p.get(k) or "") for k in ("business", "title")))
        ent["link_display"] = bk["display"]
    if p.get("whatsapp") or p.get("phone"):
        ent["phone"] = p.get("whatsapp") or p.get("phone")
    if p.get("address"):
        ent["address"] = p["address"]
    keys = [k for k in ent if k not in ("name_src", "name_confirmed")]
    return ent, keys


# palabras que presentan un nombre justo antes ('soy la doctora Lucia', 'me llamo Lucia')
_INTRO_W = {"soy", "llamo", "habla", "hablo", "nombre", "doctora", "doctor", "dra", "dr", "licenciada",
            "licenciado", "lic", "ingeniera", "ingeniero", "ing", "maestra", "maestro", "mtra", "mtro",
            "chef", "arquitecta", "arquitecto", "arq", "presento", "llama"}
_HONOR_SPOKEN = {"doctora", "doctor", "dra", "dr", "licenciada", "licenciado", "lic", "ingeniera",
                 "ingeniero", "ing", "maestra", "maestro", "mtra", "mtro", "chef", "arquitecta", "arquitecto"}


def name_span(words: list[str], name: str) -> tuple[int, int] | None:
    """(i0, i1) de `words` (palabras habladas) donde la persona DICE el nombre del perfil, o None.
    Cuenta como nombre dicho: dos palabras del nombre seguidas ('Lucia Fernandez') o una sola
    precedida (a <= 2 palabras) por una presentacion ('soy', 'me llamo', 'la doctora'...). Asi
    'sonrisa blanca' no ancla a la 'Dra. Blanca Flores'. Tolerancia de transcripcion (una letra)
    solo en palabras de >= 6 letras ('Nohemi' ~ 'Noemi'). i0 incluye el honorifico dicho."""
    import difflib
    _hon, rest = _strip_honor(str(name or ""))
    toks = [norm(t) for t in re.split(r"[\s.]+", rest) if len(norm(t)) >= 3 and norm(t) not in _PARTS]
    if not toks:
        return None
    ws = [norm(w) for w in words]

    def hit(w: str) -> bool:
        if len(w) < 3:
            return False
        if w in toks:
            return True
        return len(w) >= 5 and any(len(t) >= 6 and difflib.SequenceMatcher(None, w, t).ratio() >= 0.8
                                   for t in toks)
    i = 0
    while i < len(ws):
        if not hit(ws[i]):
            i += 1
            continue
        j, n = i, 1
        k = i + 1
        while k < len(ws) and (hit(ws[k]) or (ws[k] in _PARTS and k + 1 < len(ws) and hit(ws[k + 1]))):
            if hit(ws[k]):
                n += 1
                j = k
            k += 1
        intro = any(w in _INTRO_W for w in ws[max(0, i - 2):i])
        if n >= 2 or intro:
            s = i
            while s > 0 and s > i - 2 and ws[s - 1] in _HONOR_SPOKEN:
                s -= 1
            return s, j
        i = k
    return None


def name_hits(words: list[str], name: str) -> list[int]:
    """Indices de `words` donde se dice el nombre del perfil (ver name_span; sin el honorifico)."""
    sp = name_span(words, name)
    if sp is None:
        return []
    ws = [norm(w) for w in words]
    s, e = sp
    return [i for i in range(s, e + 1) if ws[i] not in _HONOR_SPOKEN and ws[i] not in _PARTS]


# ---------------------------------------------------------------- texto libre -> perfil
def _gender_hint(text: str, honor_g: str) -> str:
    """Genero de la persona: el del honorifico; si no, 'ella' / 'es la <Nombre>' (f) o
    'el es' / 'es el <Nombre>' (m)."""
    if honor_g:
        return honor_g
    if re.search(r"\bella\b", text, re.I) or re.search(r"\b(?:es|soy)\s+la\s+[A-ZÁÉÍÓÚÑ]", text):
        return "f"
    if re.search(r"\b[eé]l\s+es\b", text, re.I) or re.search(r"\b(?:es|soy)\s+el\s+[A-ZÁÉÍÓÚÑ]", text):
        return "m"
    return ""


def _find_name(text: str) -> tuple[str, str]:
    """(nombre con honorifico, genero) por reglas: 'Dra. Ana Ruiz', 'Dr. Juan Carlos de la
    Garza'; 'soy / ella es / se llama / mi nombre es <Nombre>'; o '<profesion> <Nombre>'
    ('Nutrióloga Ana Sofía Martínez', 'la maestra Rosa')."""
    m = _NAME_RX.search(text)
    if m:
        hon = norm(m.group(1))
        short, g = _HONOR.get(hon, (m.group(1).capitalize() + ".", ""))
        words = m.group(2).split()
        while words and norm(words[-1]) in _PARTS:
            words.pop()
        # 'Dra. Ana Lopez Odontologa' -> sin la profesion
        while len(words) > 1 and norm(words[-1]) in _PROF_BY_NORM:
            words.pop()
        return fix_accents(f"{short} {' '.join(words)}"), g
    rx = re.compile(r"\b(?:(?:ella|[eé]l)\s+es|se\s+llama|mi\s+nombre\s+es|me\s+llamo|soy|nombre\s*:)\s+"
                    r"(?:la\s+|el\s+)?(" + _NAME_WORD + _NAME_TAIL + r")", re.I)
    for m in rx.finditer(text):
        words = m.group(1).split()
        pre = m.group(0)[:m.group(0).rfind(m.group(1))]
        # 'soy la maestra Rosa': la profesion antecede al nombre
        prof_g = ""
        if words and not words[0][:1].isupper() and norm(words[0]) in _PROF_BY_NORM:
            pm, pf = _PROF_BY_NORM[norm(words[0])]
            prof_g = "f" if norm(words[0]) == norm(pf) and pm != pf else "m" if norm(words[0]) == norm(pm) and pm != pf else ""
            words = words[1:]
        while words and norm(words[-1]) in _PROF_BY_NORM:
            words.pop()
        while words and norm(words[-1]) in _PARTS:
            words.pop()
        # solo palabras con mayuscula (un nombre propio), cortando en la primera que no lo es
        keep = []
        for w_ in words:
            if not w_[:1].isupper() and norm(w_) not in _PARTS:
                break
            keep.append(w_)
        while keep and norm(keep[-1]) in _PARTS:
            keep.pop()
        if keep and keep[0][:1].isupper() and norm(keep[0]) not in _PROF_BY_NORM:
            g = "f" if re.search(r"\bella\b|\bla\s*$", pre, re.I) else \
                "m" if re.search(r"\b[eé]l\b|\bel\s*$", pre, re.I) else prof_g
            return fix_accents(" ".join(keep)), g
    # '<Profesion> <Nombre Apellido>' al inicio de una frase o tras 'la/el'
    prof_rx = r"(?i:" + "|".join(sorted({re.escape(x) for pair in _PROFESSIONS for x in pair if " " not in x},
                                         key=len, reverse=True)) + r")"
    rx2 = re.compile(r"(?:^|[.!¡¿?\n]\s*|\b(?:la|el|con\s+la|con\s+el)\s+)(" + prof_rx + r")\s+("
                     + _NAME_WORD + r"(?:\s+(?:" + _PARTICLE + r"\s+)?" + _NAME_WORD + r"){0,3})")
    for m in rx2.finditer(text):
        words = m.group(2).split()
        while words and norm(words[-1]) in _PARTS:
            words.pop()
        if words:
            pm, pf = _PROF_BY_NORM.get(norm(m.group(1)), ("", ""))
            g = "f" if pm != pf and norm(m.group(1)) == norm(pf) else "m" if pm != pf else ""
            return fix_accents(" ".join(words)), g
    return "", ""


# palabras que cortan la cola de una profesion ('psicologa clinica - citas por whatsapp')
_TITLE_STOP = {"y", "su", "sus", "con", "que", "tiene", "trabaja", "atiende", "instagram", "tiktok", "pagina",
               "web", "facebook", "whatsapp", "ubicada", "ubicado", "esta", "citas", "cita", "por", "para",
               "agenda", "agendar", "agendas", "llama", "llamar", "llamame", "escribe", "escribeme", "reserva",
               "reservas", "contacto", "tel", "telefono", "correo", "sigueme", "siguenos", "ig", "fb", "wa",
               "consultas", "informes", "horario", "horarios", "desde", "hace", "tambien", "pero", "aqui"}
_TITLE_SEP = re.compile(r"[,.;:()\n|·/–—!?¡¿]|\s-|-\s")


def _find_title(text: str, gender: str) -> str:
    """Profesion: 'es odontologa', 'soy nutriologa deportiva', 'profesion: dentista', o una
    profesion conocida junto al nombre ('Dra. Ana Ruiz, odontologa pediatra'). La cola corta en
    separadores (' - ', '|', '·', '/') y en palabras que ya no son la profesion ('citas por...')."""
    toks = list(re.finditer(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]+", text))
    for k, m in enumerate(toks):
        w = norm(m.group(0))
        pair = None
        span = 1
        if k + 1 < len(toks) and not _TITLE_SEP.search(text[m.end():toks[k + 1].start()]):
            pair = _PROF_BY_NORM.get(w + norm(toks[k + 1].group(0)))
            span = 2 if pair else 1
        pair = pair or _PROF_BY_NORM.get(w)
        if not pair:
            continue
        # 'la doctora X' es un honorifico, no la profesion (salvo 'es doctora')
        before = strip_accents(text[max(0, m.start() - 12):m.start()].lower())
        if w in ("doctora", "doctor") and not re.search(r"\b(es|soy)\s+(una?\s+)?$", before):
            continue
        tail = []
        j = k + span
        # complemento corto: 'especialista en ortodoncia', 'odontologa pediatra'
        while j < len(toks) and len(tail) < 3:
            gap = text[toks[j - 1].end():toks[j].start()]
            if _TITLE_SEP.search(gap):
                break
            nw = norm(toks[j].group(0))
            if nw in _TITLE_STOP or toks[j].group(0)[:1].isupper() and nw not in _TITLE_WORDS:
                break
            tail.append(toks[j].group(0))
            j += 1
        while tail and norm(tail[-1]) in ("en", "de", "del", "la", "el", "los", "las", "a", "al"):
            tail.pop()
        raw = " ".join([toks[i].group(0) for i in range(k, k + span)] + tail)
        return fix_title(raw, gender)
    m = re.search(r"\b(?:profesi[oó]n|rol|puesto)\s*:\s*([^,.;\n]{3,40})", text, re.I)
    if m:
        return fix_title(m.group(1), gender)
    return ""


def _broken_title(t: str) -> bool:
    """La profesion por reglas trae palabras que no son de la profesion ('... citas por')."""
    ws = [norm(w) for w in t.split()]
    return any(w in _TITLE_STOP for w in ws[1:]) or (bool(ws) and ws[-1] in ("en", "de", "del", "la", "el"))


def _find_business(text: str, links: list[dict]) -> str:
    """Negocio: 'consultorio Sonrisa Plena' -> 'Sonrisa Plena'; con el tipo en mayuscula es parte
    del nombre: 'Clínica Dental Sonríe' -> 'Clínica Dental Sonríe', 'Barbería El Bigote'."""
    plat_names = {norm(v["label"]) for v in PLATFORMS.values()} | set(PLATFORMS)
    for m in _BUSINESS_RX.finditer(text):
        typ, b = m.group(1).strip(), m.group(2).strip()
        if not norm(b) or norm(b) in plat_names:
            continue
        glue = m.group(0)[len(m.group(1)):m.group(0).find(m.group(2))]
        if typ[:1].isupper() and re.fullmatch(r"\s+[\"“']?", glue):
            b = f"{typ} {b}"
        return fix_accents(b)
    return ""


def _find_address(text: str) -> str:
    """Direccion escrita ('Estamos en Av. Demo 123, Guadalajara') o ''."""
    m = _ADDRESS_RX.search(text)
    if not m:
        return ""
    return _clean(m.group(1).strip(" ,.;"), 120)


# usuario SIN @ tras el nombre de la red: 'Instagram: dra.demo', 'mi instagram es dra.demo'
_BARE_HANDLE_RX = re.compile(
    r"\b(instagram|insta|ig|tik\s?tok|youtube|yt|facebook|fb|twitter|x|threads|telegram|pinterest|snapchat|snap)"
    r"\s*(?:(?P<colon>[:=])|\b(?:es|como)\b)\s*(?![@\w.-]*(?:/|@))"
    r"(?P<h>[A-Za-z0-9_](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9_])?)(?![\w@/])", re.I)
_NOT_HANDLE = {"el", "la", "los", "las", "de", "del", "un", "una", "muy", "mi", "su", "nuestro", "nuestra",
               "este", "esta", "ese", "esa", "igual", "mismo", "misma", "nuevo", "nueva", "privado", "privada",
               "publico", "publica", "genial", "super", "increible", "buenisimo", "arroba", "sin", "con", "para"}


_LLM_SCHEMA = {"type": "object", "properties": {
    "name": {"type": "string"}, "title": {"type": "string"}, "gender": {"type": "string"},
    "business": {"type": "string"}, "tagline": {"type": "string"}, "address": {"type": "string"}},
    "required": ["name", "title", "gender", "business", "tagline", "address"]}
_LLM_SYS = ("Extraes el perfil de una persona o negocio de un texto en espanol para ponerlo en un "
            "video. Responde SOLO JSON. No inventes: si un dato no esta en el texto, dejalo vacio.")


def _llm_prompt(text: str) -> str:
    return ("Texto: \"\"\"" + text[:1200] + "\"\"\"\n\n"
            "Devuelve JSON con:\n"
            "- name: nombre COMPLETO de la persona con su titulo si aparece (Dra., Dr., Lic., Ing.), con "
            "todas sus partes (de la Garza, de los Ángeles) y los acentos correctos del espanol "
            "(Lucia -> Lucía, Fernandez -> Fernández). Vacio si no hay.\n"
            "- title: su profesion o rol en espanol, corta, con mayuscula inicial, acentos y el GENERO "
            "correcto de la persona (Odontóloga si es mujer, Odontólogo si es hombre). Vacio si no se dice.\n"
            "- gender: 'f', 'm' o ''.\n"
            "- business: nombre del negocio, consultorio, clinica o tienda si se dice, completo como se "
            "escribe (Clínica Dental Sonríe, Barbería El Bigote). NUNCA una red social ni una plataforma "
            "(Instagram, TikTok, PaseoClub, Calendly...). Vacio si no hay.\n"
            "- tagline: frase corta que la describa SOLO si el texto la da; si no, vacio.\n"
            "- address: direccion del negocio (calle y numero, colonia, ciudad) SOLO si el texto la da; "
            "si no, vacio.")


def _ollama_url() -> str:
    return (os.environ.get("REELFY_OLLAMA_URL") or "http://127.0.0.1:11498").rstrip("/")


def _llm_call(text: str, timeout: float = 25.0) -> dict | None:
    """LLM local (ollama, qwen3.5:4b, formato con esquema). None si no responde."""
    model = os.environ.get("REELFY_LLM_MODEL") or "qwen3.5:4b"
    body = {"model": model, "stream": False, "format": _LLM_SCHEMA, "think": False, "keep_alive": "10m",
            "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 220, "seed": 7},
            "messages": [{"role": "system", "content": _LLM_SYS},
                         {"role": "user", "content": _llm_prompt(text)}]}
    req = urllib.request.Request(_ollama_url() + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = json.loads(r.read())["message"]["content"]
    except Exception:
        return None
    try:
        v = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            v = json.loads(m.group(0)) if m else None
        except Exception:
            v = None
    return v if isinstance(v, dict) else None


def _in_text(val: str, text: str, need: int = 1, strict: bool = False) -> bool:
    """Todas (o `need`) las palabras significativas de `val` estan en el texto (sin acentos).
    Tolera una palabra faltante salvo con `strict` (todas deben estar)."""
    tn = " " + " ".join(norm(w) for w in re.findall(r"[\w'’-]+", text)) + " "
    ws = [norm(w) for w in re.findall(r"[\w'’-]+", val) if len(norm(w)) >= 3]
    ws = [w for w in ws if w not in _HONOR]
    if not ws:
        return False
    hits = sum(1 for w in ws if f" {w} " in tn or (len(w) >= 6 and w[:6] in tn))
    if strict:
        return hits == len(ws)
    return hits >= min(need, len(ws)) and hits >= max(1, len(ws) - 1)


def _merge_accents(base: str, llm_val: str) -> str:
    """Toma los acentos del LLM palabra por palabra si la palabra es la misma sin acentos."""
    b, l = base.split(), llm_val.split()
    if len(b) != len(l):
        return base
    out = []
    for x, y in zip(b, l):
        out.append(y if strip_accents(y).lower() == strip_accents(x).lower() and strip_accents(x) == x else x)
    return " ".join(out)


def parse_profile_text(text: Any, use_llm: bool = True, llm: Callable[[str], dict | None] | None = None
                       ) -> dict:
    """Texto libre -> {profile, notes, llm}. Enlaces deterministas (URL, dominio, @usuario por
    contexto o 'Instagram: usuario', correo, telefono/WhatsApp); nombre, profesion (con genero),
    negocio y direccion por reglas, refinados por el LLM local si responde (validado contra el
    texto: nunca inventa). `llm(texto) -> dict` inyectable (pruebas)."""
    s = _clean(text, 2000)
    notes: list[str] = []
    if not s:
        return {"profile": None, "notes": ["texto vacío"], "llm": False}
    links = find_links(s)
    # sin los enlaces el nombre y la profesion se leen mejor
    plain = _URL_RX.sub(" ", _EMAIL_RX.sub(" ", s))
    plain = _HANDLE_RX.sub(" ", plain)
    address = _find_address(plain)
    name, g_hon = _find_name(plain)
    gender = _gender_hint(plain, g_hon)
    title = _find_title(plain, gender)
    business = _find_business(plain, links)
    used_llm = False
    if use_llm:
        r = (llm or _llm_call)(s)
        if isinstance(r, dict):
            used_llm = True
            ln = _clean(r.get("name"), 80)
            lt = _clean(r.get("title"), 60)
            lg = r.get("gender") if r.get("gender") in ("f", "m") else ""
            lb = _clean(r.get("business"), 80)
            la = _clean(r.get("address"), 120)
            if ln and _in_text(ln, plain, need=2):
                rules_core = norm(_strip_honor(name)[1])
                llm_hon, llm_rest = _strip_honor(ln)
                llm_core = norm(llm_rest)
                full_in_text = _in_text(ln, plain, need=99, strict=True)
                if not name:
                    name = fix_accents(ln)
                elif llm_core == rules_core or llm_core.endswith(rules_core):
                    name = _merge_accents(name, fix_accents(ln))
                elif rules_core and rules_core in llm_core and len(llm_core) > len(rules_core) and full_in_text:
                    # nombre compuesto que las reglas cortaron ('Juan Carlos' de 'Juan Carlos de la
                    # Garza'): el del LLM esta completo en el texto
                    hon = _strip_honor(name)[0] or llm_hon
                    name = fix_accents((hon + " " if hon else "") + llm_rest)
            gender = gender or lg
            if lt and _in_text(lt.split()[0], plain) and not title:
                title = fix_title(lt, gender)
            elif lt and title and norm(lt) != norm(title) and _in_text(lt, plain):
                if len(lt) > len(title) and not _broken_title(lt):
                    title = fix_title(lt, gender)
                elif _broken_title(title) and not _broken_title(lt):
                    title = fix_title(lt, gender)            # 'Psicóloga clínica citas por' -> LLM
            plat_names = {norm(v["label"]) for v in PLATFORMS.values()} | set(PLATFORMS)
            lb_ok = lb and norm(lb) not in plat_names and \
                not any(norm(lb) in norm(l.get("url", "")) for l in links if l["kind"] == "booking")
            if lb_ok and not business and _in_text(lb, plain, need=2):
                business = fix_accents(lb)
            elif lb_ok and business and norm(business) in norm(lb) and len(lb) > len(business) \
                    and lb[:1].isupper() and strip_accents(lb) in strip_accents(plain):
                # 'Clínica Dental Sonríe' completo, tal como se escribio (el tipo en minuscula,
                # 'consultorio Sonrisa Plena', no es parte del nombre)
                business = fix_accents(lb)
            if la and not address and _in_text(la, plain, need=99, strict=True) and re.search(r"\d", la):
                address = la
        else:
            notes.append("IA local no disponible: nombre y profesión por reglas")
    if title and gender:
        title = fix_title(title, gender)
    for l in links:
        if l.get("ambiguous"):
            notes.append(f"{l['handle']} sin red indicada: se tomó como Instagram (cámbiala si es otra)")
    if not name:
        notes.append("no se encontró el nombre: escríbelo en el perfil")
    if not title:
        notes.append("no se encontró la profesión: agrégala para el rótulo")
    prof = {"name": name, "title": title, "business": business, "tagline": "", "links": links,
            "address": address}
    if gender:
        prof["gender"] = gender
    return {"profile": normalize_profile(prof), "notes": notes, "llm": used_llm}


def platforms_catalog() -> list[dict]:
    """[{id, label, icon, kind, color, cta?}] para el Studio (selector de red de cada enlace)."""
    return [dict({"id": k}, **v) for k, v in PLATFORMS.items()]
