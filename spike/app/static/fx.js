/* Reelfy Studio: editor pro del Motion Engine (plan v2).
   - Panel Director (estilo, intensidad, re-dirigir, musica).
   - Pistas Texto / Graficos / Escenas / Efectos / Transiciones / SFX / Musica en el timeline.
   - Inspector guiado por /fx/schema.
   - Vista previa en vivo aproximada (subtitulos con fuentes reales, texto, graficos, escenas,
     camara) + cuadro exacto del servidor (/frame) + vista previa real (/preview pro).
   - Export por etapas con cancelar.
   Depende de fxcore.js (logica pura) y de los globales del script de index.html
   (PLAN, JOB, V, CV, CTX, CW, CH, TL, mut, renderTL, ...), que solo se leen dentro de funciones. */
(function () {
  "use strict";
  const C = window.FXCore;
  const FX = (window.FX = {});
  const $ = s => document.querySelector(s);
  const $$ = (s, el) => [...(el || document).querySelectorAll(s)];
  const ICO = n => `<svg class="ic" viewBox="0 0 24 24"><use href="#i-${n}"/></svg>`;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const r3 = C.round3;

  // ------------------------------------------------------------------ metadatos de pistas
  const TRACK_META = {
    text: { label: "Texto", icon: "type", c: "var(--trk-text)", rgb: "167,139,250" },
    graphics: { label: "Gráficos", icon: "shapes", c: "var(--trk-gfx)", rgb: "244,114,182" },
    cutaways: { label: "Escenas", icon: "scene", c: "var(--trk-scene)", rgb: "45,212,191" },
    effects: { label: "Efectos", icon: "wand", c: "var(--trk-fx)", rgb: "96,165,250" },
    transitions: { label: "Transiciones", icon: "transition", c: "var(--trk-trans)", rgb: "251,146,60" },
    sfx: { label: "SFX", icon: "speaker", c: "var(--trk-sfx)", rgb: "250,204,21" },
    music: { label: "Música", icon: "music", c: "var(--trk-mus)", rgb: "52,211,153" },
    // recursos del guion (imagen o video como tarjeta flotante): fx.media
    media: { label: "Recursos", icon: "card", c: "var(--trk-media)", rgb: "129,140,248" },
  };
  FX.TRACK_META = TRACK_META;
  const TOP_TRACKS = ["text", "graphics", "cutaways", "effects", "transitions"];
  const STYLES = [
    { v: "dinamico", label: "Dinámico", icon: "bolt", desc: "Ritmo de TikTok: títulos, gráficos y cortes marcados." },
    { v: "elegante", label: "Elegante", icon: "feather", desc: "Serif, movimientos suaves y color cálido." },
    { v: "minimal", label: "Minimal", icon: "minus", desc: "Poco texto, cortes limpios y sin ruido." },
    { v: "energetico", label: "Energético", icon: "flame", desc: "Máxima densidad: sacudidas, glitch y SFX." },
  ];
  const STAGES = [
    ["director", "Dirigiendo"], ["timeline", "Línea de tiempo"], ["base", "Video base"], ["analysis", "Rostro y recorte"],
    ["audio", "Audio y música"], ["render", "Componiendo"], ["mux", "Uniendo"], ["shorts", "Shorts"],
  ];
  const LABELS_ES = {
    // opciones frecuentes de selects (se muestran en espanol; el valor sigue siendo el del esquema)
    left: "Izquierda", center: "Centro", right: "Derecha", up: "Arriba", down: "Abajo", auto: "Auto",
    none: "Ninguna", outline: "Contorno", filled: "Relleno", circle: "Círculo", squircle: "Squircle",
    light: "Claro", dark: "Oscuro", brand: "Marca", start: "Inicio", end: "Fin",
    upper: "MAYÚS", lower: "minús", title: "Título", natural: "Natural", hit: "Golpe", fade: "Fundido",
    ai: "IA", procedural: "Procedural", library: "Biblioteca", file: "Archivo", split: "Dividir", sweep: "Barrer",
    whiteout: "Blanco total", partial: "Parcial", tape: "Frenado", cut: "Corte seco", spring: "Resorte", smooth: "Suave",
    brush: "Pincel", sketch: "Boceto", clean: "Limpio", neutral: "Neutro", warm: "Cálido", cool: "Frío", sepia: "Sepia",
    clean_bold: "Limpio bold", karaoke: "Karaoke", boxed: "En caja", minimal_small: "Mínimo pequeño", bold_caps: "Mayúsculas bold",
    natural_look: "Natural", warm_clean: "Cálido limpio", cinematic: "Cinemático", bright_pop: "Brillante pop", moody: "Dramático", bw_contrast: "B/N contraste",
    initial: "Inicial", face: "Cara", face_left: "Izq. de la cara", face_right: "Der. de la cara", face_top: "Sobre la cara",
    // animo de la musica (el mismo nombre en Proyecto y en el inspector)
    upbeat: "Alegre", chill: "Tranquila", inspiring: "Inspiradora", corporate: "Corporativa", playful: "Divertida", dramatic: "Dramática",
    // estilos del director y presets de titulo
    dinamico: "Dinámico", elegante: "Elegante", minimal: "Minimal", energetico: "Energético",
    title_pop: "Título pop", stack: "Apilado", cascade: "Cascada", serif_elegant: "Serif elegante", script_accent: "Manuscrita",
    behind_giant: "Gigante detrás", highlight_marker: "Marcador", typewriter: "Máquina de escribir", counter: "Contador", kicker: "Etiqueta + título",
    // animaciones
    pop: "Pop", pop_out: "Pop de salida", rise: "Subir", drop: "Caer", scale: "Escala", scale_down: "Reducir", flip: "Giro", mask: "Máscara",
    draw: "Trazo", undraw: "Borrar trazo", wipe: "Barrido", type: "Máquina de escribir", word_stagger: "Palabra por palabra", letter_stagger: "Letra por letra",
    slide_up: "Deslizar arriba", slide_down: "Deslizar abajo", slide_left: "Deslizar a la izq.", slide_right: "Deslizar a la der.",
    linear: "Lineal", in_quad: "Acelera suave", in_cubic: "Acelera", in_expo: "Acelera fuerte", out_quad: "Frena suave", out_cubic: "Frena",
    out_quart: "Frena marcado", out_expo: "Frena fuerte", in_out_quad: "Suave", in_out_cubic: "Suave (cúbica)", in_out_expo: "Suave fuerte",
    smoothstep: "Suave (paso)", out_back: "Rebote al llegar", in_back: "Toma impulso", out_bounce: "Rebote", out_elastic: "Elástico",
    // garabatos, tarjetas y fondos
    arrow_curve: "Flecha curva", arrow_down: "Flecha abajo", arrow_straight: "Flecha recta", burst: "Estallido", check: "Palomita", cross: "Tache",
    circle_scribble: "Círculo a mano", dotted_path: "Camino punteado", exclaim: "Exclamación", heart: "Corazón", lightning: "Rayo", loop: "Bucle",
    question: "Pregunta", rays: "Rayos", sparkle: "Destello", squiggle: "Zigzag", star4: "Estrella de 4", star5: "Estrella de 5", swirl: "Espiral",
    underline: "Subrayado", checklist: "Lista", generic: "Genérica", link: "Enlace", location: "Ubicación", phone: "Teléfono", price: "Precio",
    rating: "Calificación", schedule: "Horario", stat: "Dato", follow: "Sígueme", link_below: "Link abajo", comment: "Comenta", save: "Guárdalo",
    share: "Compártelo", blur_self: "Video desenfocado", color: "Color sólido", dark_stars: "Estrellas", gradient: "Degradado", grid_warp: "Cuadrícula",
    halftone: "Semitono", paper: "Papel",
  };
  const lbl = v => LABELS_ES[v] || C.accentEs(String(v).replace(/_/g, " "));
  const es = C.accentEs;
  /** Etiquetas propias del Studio por clave (evita "Subtítulos" dentro del grupo "Subtítulos"). */
  const PARAM_LABEL = { "captions.enabled": "Mostrar subtítulos", "music.enabled": "Música activa" };
  const MOOD_ES = { upbeat: "Alegre", chill: "Tranquila", inspiring: "Inspiradora", corporate: "Corporativa", playful: "Divertida", dramatic: "Dramática" };

  // ------------------------------------------------------------------ estado
  let SCHEMA = null, FONTS = [], SFXCAT = {}, CAT_TRIES = 0;
  let PLATS = {};                         // catalogo de redes del perfil: id -> {label, icon, color, svg, kind}
  const FONT_STATE = {};                  // nombre -> "loading" | "ok" | "err"
  const ICON_CACHE = {};                  // nombre -> svg (string)
  const ICON_IMG = {};                    // nombre|color -> Image
  const ICON_DX = {};                     // nombre -> desfase de la tinta (unidades de la caja 24)
  let SELX = [];                          // seleccion multiple fx: [{track,id}]
  let CLIP = null;                        // portapapeles interno
  let TLCACHE = { key: "", tl: null };
  let CAPCACHE = { key: "", groups: [] };
  FX.exactOn = true; FX.sfxOn = true; FX.musicOn = true;

  const isPro = () => !!(typeof PLAN !== "undefined" && PLAN && PLAN.version === 2 && PLAN.fx);
  FX.isPro = isPro;
  const fx = () => PLAN.fx;
  const items = track => ((PLAN.fx && PLAN.fx[track]) || []);
  const dur = () => +(PLAN && PLAN.duration) || 1;

  /** Linea de tiempo cacheada (cambia con los cortes). */
  function tlObj() {
    // la clave se recalcula como mucho una vez cada ~50 ms (se consulta muchas veces por cuadro)
    const now = performance.now();
    if (TLCACHE.tl && TLCACHE.plan === PLAN && now - (TLCACHE.at || 0) < 50) return TLCACHE.tl;
    TLCACHE.at = now; TLCACHE.plan = PLAN;
    const key = JSON.stringify([PLAN.duration, (PLAN.cuts || []).map(c => [c.start, c.end, c.enabled !== false]), (PLAN.shots || []).map(s => s.start)]);
    if (key !== TLCACHE.key) { TLCACHE = { key, tl: C.timelineFromPlan(PLAN, 30), at: now, plan: PLAN }; FX.onCutsChanged && FX.onCutsChanged(); }
    return TLCACHE.tl;
  }
  FX.timeline = tlObj;
  FX.invalidateTimeline = () => { TLCACHE.at = 0; };
  FX.finalTime = t => tlObj().remapClamped(t);

  // ------------------------------------------------------------------ red
  async function jget(url) { const r = await fetch(url); if (!r.ok) throw Object.assign(new Error(`${r.status}`), { status: r.status }); return r.json(); }
  async function jpost(url, body, signal) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
    let d = null; try { d = await r.json(); } catch (e) { d = null; }
    if (!r.ok) throw Object.assign(new Error((d && d.detail) || `Error ${r.status}`), { status: r.status });
    return d || {};
  }
  /** Plan que viaja al servidor (con el idioma de subtitulos elegido). */
  function planForServer() { return C.planForLang(PLAN, typeof LANG === "undefined" ? "es" : LANG); }
  FX.planForServer = planForServer;
  /** Opciones del proyecto que no viven en el plan (marca, audio estudio, aire): viajan a /frame,
      /preview y /render para que el cuadro exacto y el export las respeten. */
  function engineOpts() {
    const o = {};
    try {
      o.logo = LOGO_ON ? { pos: LOGO_POS, size: LOGO_SIZE, opacity: 0.9 } : null;
      o.air = AIR;
      // aire OPT-IN en la edicion IA: solo cuenta si el usuario movio el control (air_user)
      if (isPro() && FX.airUser) o.air_user = true;
      const en = document.getElementById("enhance"); o.enhance_audio = en ? en.checked : true;
      o.platform = PLATFORM; o.lang = LANG;
    } catch (e) { /* editor aun sin iniciar */ }
    return o;
  }
  FX.engineOpts = engineOpts;
  /** El aire (margen desenfocado) se aplica en el export: en la edicion IA solo si el servidor lo
      aplica siempre (pro_opts) o si es opt-in y el usuario lo pidio (air_user). */
  FX.airUser = false;
  FX.airActive = () => !isPro() || PRO_OPTS.has("air") || (PRO_OPTIN.has("air") && FX.airUser);
  /** Opciones del proyecto guardadas por el servidor (/status.engine): el aire elegido y el audio
      de estudio sobreviven a recargar la pagina. */
  FX.restoreEngine = function (eng) {
    if (!eng || typeof eng !== "object") return;
    try {
      if (eng.air_user && +eng.air > 0) {
        FX.airUser = true; AIR = +eng.air;
        const a = $("#air"), l = $("#airLbl"); if (a) a.value = Math.round(AIR * 100); if (l) l.textContent = Math.round(AIR * 100) + "%";
      }
      if (eng.enhance_audio === false) { const en = $("#enhance"); if (en) en.checked = false; }
      // logo de la marca: el servidor guarda si iba (bool) o, en versiones nuevas, {pos, size}
      if (Object.prototype.hasOwnProperty.call(eng, "logo")) {
        LOGO_ON = !!eng.logo;
        if (eng.logo && typeof eng.logo === "object") {
          if (["tl", "tr", "bl", "br"].includes(eng.logo.pos)) LOGO_POS = eng.logo.pos;
          if (+eng.logo.size > 0) LOGO_SIZE = +eng.logo.size;
        }
        const lo = $("#logoOn"); if (lo) lo.checked = LOGO_ON;
        const ls = $("#logoSize"); if (ls) ls.value = Math.round(LOGO_SIZE * 100);
        if (typeof segSet === "function" && $("#segLogo")) segSet("#segLogo", LOGO_POS);
      }
    } catch (e) { /* editor aun sin iniciar */ }
  };

  /** Etiquetas, ayudas y descripciones del esquema con acentos (el motor las trae en ASCII). */
  function accentSchema(o) {
    if (Array.isArray(o)) { o.forEach(accentSchema); return o; }
    if (o && typeof o === "object") Object.keys(o).forEach(k => {
      if ((k === "label" || k === "hint" || k === "desc") && typeof o[k] === "string") o[k] = es(o[k]);
      else accentSchema(o[k]);
    });
    return o;
  }
  FX.useSchema = s => { SCHEMA = accentSchema(s); return SCHEMA; };   // tambien para pruebas
  async function loadCatalogs() {
    const tasks = [];
    if (!SCHEMA) tasks.push(jget("/fx/schema").then(s => { SCHEMA = accentSchema(s); }).catch(() => {}));
    if (!FONTS.length) tasks.push(jget("/fx/fonts").then(d => { FONTS = (d && d.fonts) || []; }).catch(() => {}));
    if (!Object.keys(SFXCAT).length) tasks.push(jget("/fx/sfx").then(d => { SFXCAT = d || {}; }).catch(() => {}));
    if (!SETTINGS_OK) tasks.push(jget("/settings").then(d => { SETTINGS_OK = true; FX.applySettings(d); }).catch(() => {}));
    if (!Object.keys(PLATS).length) tasks.push(jget("/profile/platforms").then(d => { FX.setPlatforms(d && d.platforms); }).catch(() => {}));
    await Promise.all(tasks);
    if (!FONTS.length && SCHEMA) {   // sin /fx/fonts: lista del esquema
      const p = (SCHEMA.captions && SCHEMA.captions.params || []).find(x => x.type === "font");
      FONTS = ((p && p.opts) || []).map(n => ({ name: n, family: n.split("-")[0], role: "" }));
    }
    if ((!SCHEMA || !FONTS.length) && CAT_TRIES++ < 40) setTimeout(() => loadCatalogs().then(() => { if (SCHEMA) { FX.renderAll(); } }), 4000);
  }
  FX.loadCatalogs = loadCatalogs;
  /* Opciones del proyecto que el render pro ya aplica (el servidor las anuncia en /settings.pro_opts:
     "logo", "air", "enhance_audio"). Las demas se muestran deshabilitadas en la edicion IA: la UI no
     promete lo que el export no hace. */
  let PRO_OPTS = new Set(), PRO_OPTIN = new Set(), FEATURES = new Set(), SETTINGS_OK = false;
  FX.proOpts = () => PRO_OPTS;
  FX.proOptin = () => PRO_OPTIN;
  FX.features = () => FEATURES;
  FX.setProOpts = (list, optin) => { PRO_OPTS = new Set(list || []); if (optin) PRO_OPTIN = new Set(optin); FX.syncProControls(); };   // pruebas / servidor nuevo
  /** GET /settings: opciones que el render pro aplica (pro_opts), las opt-in (pro_opts_optin),
      idiomas de la edicion IA (pro_langs) y capacidades de la API (features). */
  FX.applySettings = function (d) {
    d = d || {};
    PRO_OPTS = new Set(d.pro_opts || []); PRO_OPTIN = new Set(d.pro_opts_optin || []);
    FEATURES = new Set(d.features || []);
    if (Array.isArray(d.pro_langs) && d.pro_langs.length) C.setProLangs(d.pro_langs);
    FX.syncProControls();
  };
  FX.syncProControls = function () {
    const pro = isPro();
    const gate = (sel, key) => {
      const el = $(sel); if (!el) return false;
      const off = pro && !PRO_OPTS.has(key) && !PRO_OPTIN.has(key);
      el.classList.toggle("proff", off);
      el.querySelectorAll("input,button,select").forEach(x => { x.disabled = off; });
      return off;
    };
    const logoOff = gate("#brandLogoRow", "logo");
    gate("#airRow", "air");
    const enhOff = gate("#enhanceRow", "enhance_audio");
    if (enhOff) { const en = $("#enhance"); if (en && !en.checked) { en.checked = true; if (window.applyEnhance) applyEnhance(); } }
    const n = $("#proNoteBrand"); if (n) n.classList.toggle("on", logoOff);
    // aire opt-in: el control dice si se aplica (solo con una fuente mas ancha que la salida)
    const an = $("#airNote");
    if (an) {
      const opt = pro && PRO_OPTIN.has("air") && !PRO_OPTS.has("air");
      an.classList.toggle("on", opt);
      if (opt) an.textContent = FX.airUser ? `Aire aplicado: ${Math.round((typeof AIR !== "undefined" ? AIR : 0) * 100)} % de margen desenfocado (solo cambia un video horizontal).`
        : "Sin aire: el video va a sangre completa. Mueve el control para dejar margen (solo cambia un video horizontal).";
    }
    $$("#segLang button").forEach(b => {
      const off = pro && !C.PRO_LANGS.includes(b.dataset.v);
      b.disabled = off; b.classList.toggle("dis", off);
      b.title = off ? "Este idioma aún no está disponible en la edición IA." : "";
    });
    FX.syncLangNote();
  };
  const LANG_ES = { es: "español", en: "inglés", pt: "portugués", fr: "francés", de: "alemán", it: "italiano" };
  FX.langName = l => LANG_ES[l] || l;
  /** Estado de la traduccion de titulos y graficos (vive en el plan: sobrevive a recargar). */
  const trState = () => (PLAN && PLAN.studio && PLAN.studio.texts) || null;
  /** Idioma de los titulos y graficos del plan (el de su ultima traduccion; "es" = originales). */
  FX.textsLang = () => { const st = trState(); return st && st.lang ? st.lang : "es"; };
  /** Aviso del idioma en la edicion IA: los subtitulos se traducen al elegir el idioma; titulos y
      graficos, a peticion (y se pueden devolver a su texto original). Los editados a mano despues
      de traducir se respetan (y se ofrece volver a traducirlos). */
  FX.syncLangNote = function () {
    const n = $("#langNote");
    FX.syncLangWarn();
    if (!n) return;
    if (!isPro() || typeof LANG === "undefined") { n.style.display = "none"; return; }
    const st = trState(), tl = FX.textsLang(), S = C.textStatus(PLAN.fx, st);
    const plural = (k, a, b) => `${k} ${k === 1 ? a : b}`;
    if (LANG === "es") {
      if (tl === "es") { n.style.display = "none"; return; }
      n.style.display = "";
      n.innerHTML = `${ICO("alert")}<span>Los títulos y gráficos están en ${esc(FX.langName(tl))}.</span><button class="btn ghost mini" id="trBack">${ICO("undo")}Volver a los textos originales</button>`;
      const b = $("#trBack"); if (b) b.onclick = () => FX.restoreFxTexts();
      return;
    }
    n.style.display = "";
    const nm = FX.langName(LANG);
    let msg = `Subtítulos en ${esc(nm)}. `, btns = "";
    if (!S.total) msg += "";
    else if (tl !== LANG) {
      msg += `Los títulos y gráficos (${S.total}) siguen en ${esc(FX.langName(tl))}.`;
      btns += `<button class="btn ghost mini" id="trFx">${ICO("type")}Traducir títulos y gráficos</button>`;
    } else {
      msg += "Títulos y gráficos traducidos.";
      const miss = st && st.untranslated && st.untranslated.length ? st.untranslated.length : 0;
      if (miss) msg += ` ${plural(miss, "quedó igual", "quedaron igual")} (nombres o cifras).`;
      if (S.fresh.length) { msg += ` ${plural(S.fresh.length, "título nuevo sin traducir", "títulos nuevos sin traducir")}.`; btns += `<button class="btn ghost mini" id="trFx">${ICO("type")}Traducir ${S.fresh.length === 1 ? "el nuevo" : "los nuevos"}</button>`; }
      if (S.edited.length) { msg += ` ${plural(S.edited.length, "editado a mano (se respeta)", "editados a mano (se respetan)")}.`; btns += `<button class="btn ghost mini" id="trEd">${ICO("type")}Traducir ${S.edited.length === 1 ? "el editado" : "los editados"} de nuevo</button>`; }
    }
    n.innerHTML = `${ICO("alert")}<span>${msg}</span>${btns}` + (tl !== "es" ? `<button class="btn ghost mini" id="trBack">${ICO("undo")}Volver a los textos originales</button>` : "");
    const b = $("#trFx"); if (b) b.onclick = () => FX.translateFx();
    const e = $("#trEd"); if (e) e.onclick = () => FX.translateFx(null, { edited: true, only: "edited" });
    const bk = $("#trBack"); if (bk) bk.onclick = () => FX.restoreFxTexts();
  };
  /** Aviso junto a Exportar si los titulos y los subtitulos van en idiomas distintos. */
  FX.syncLangWarn = function () {
    const w = $("#langWarn"); if (!w) return;
    const tl = isPro() && typeof LANG !== "undefined" ? FX.textsLang() : "es";
    const bad = isPro() && tl !== "es" && tl !== LANG;
    w.classList.toggle("on", bad);
    w.innerHTML = bad ? `${ICO("alert")}Títulos en ${esc(FX.langName(tl))}, subtítulos en ${esc(FX.langName(LANG))}` : "";
    w.title = bad ? "El export saldría con dos idiomas. Clic para elegir el idioma o volver a los textos originales." : "";
    w.onclick = bad ? () => { if (window.setPanelTab) setPanelTab("proj"); const n = $("#langNote"); if (n) n.scrollIntoView({ block: "center", behavior: "smooth" }); } : null;
  };
  /** Traduce EN SITIO los textos de titulos, graficos y escenas al idioma elegido (un paso de
      deshacer): POST /translate {texts, spans, lang} -> texts_<lang> + untranslated. Cada item parte
      de su texto ORIGINAL completo (plan.studio.texts.src); lo editado a mano tras traducir se
      respeta salvo `o.edited`. 503 = la IA local no respondio: se avisa y NO se guarda nada. */
  FX.translateFx = async function (lang, o) {
    const opt = o || {};
    const L = lang || (typeof LANG !== "undefined" ? LANG : "en");
    if (!isPro() || FX.busy() || L === "es") return;
    const st0 = trState();
    const req = C.translationRequest(PLAN.fx, st0, L, { edited: !!opt.edited });
    if (opt.only === "edited") {
      const ed = new Set(C.textStatus(PLAN.fx, st0).edited);
      req.items = req.items.filter(x => ed.has(x.id)); req.list = req.list.filter(x => ed.has(x.id));
    }
    if (!req.list.length) { FX.syncLangNote(); return; }
    const span = x => { const f = C.findItem(PLAN.fx, x.id); const it = f && f.item; return it && it.start != null ? [+it.start, +it.end] : null; };
    if (window.chip) chip(`Traduciendo títulos y gráficos al ${FX.langName(L)}…`);
    try {
      const d = await jpost(`/translate/${JOB}`, { texts: req.list.map(x => x.text), spans: req.list.map(span), lang: L });
      const out = d[`texts_${L}`] || [];
      if (out.length !== req.list.length) throw new Error("respuesta incompleta");
      edit(() => {
        const nst = C.applyTranslation(PLAN.fx, st0, req, out, L, d.untranslated || []);
        PLAN.studio = Object.assign({}, PLAN.studio, { texts: nst });
      }, { rebuild: true });
      const kept = req.kept.length && !opt.edited ? ` ${req.kept.length} editado${req.kept.length > 1 ? "s" : ""} a mano se respetó${req.kept.length > 1 ? "aron" : ""}.` : "";
      toast(`Títulos y gráficos en ${FX.langName(L)}.${kept} ⌘Z o "Volver a los textos originales" los regresa.`, "type", true);
    } catch (e) {
      toast(e.status === 503 ? `La IA local no respondió: ${e.message}` : `No se pudo traducir: ${e.message || e}`, "alert");
    } finally { if (window.chip) chip(null); FX.syncLangNote(); }
  };
  /** Devuelve titulos y graficos a su texto original COMPLETO (un paso de deshacer). */
  FX.restoreFxTexts = function () {
    const st = trState(); if (!st || !st.src) return;
    let n = 0;
    edit(() => {
      n = C.restoreOriginalTexts(PLAN.fx, st);
      PLAN.studio = Object.assign({}, PLAN.studio); delete PLAN.studio.texts;
    }, { rebuild: true });
    toast(`Títulos y gráficos de vuelta a su texto original${n ? ` (${n})` : ""}.`, "undo", true);
    FX.syncLangNote();
  };
  FX.schema = () => SCHEMA;
  /** Catalogo de redes del perfil (GET /profile/platforms): sus iconos SVG quedan en la cache de
      iconos (insignias de chips, Seguir y el panel Perfil). */
  FX.setPlatforms = function (list) {
    (list || []).forEach(p => { if (!p || !p.id) return; PLATS[p.id] = p; if (p.icon && p.svg && !ICON_CACHE[p.icon]) ICON_CACHE[p.icon] = p.svg; });
  };
  FX.platforms = () => PLATS;

  // ------------------------------------------------------------------ fuentes reales (@font-face)
  const WEIGHT = n => { const f = FONTS.find(x => x.name === n); if (f && f.weight) return +f.weight;
    return /Black/.test(n) ? 900 : /ExtraBold/.test(n) ? 800 : /SemiBold/.test(n) ? 600 : /Bold/.test(n) ? 700 : /Medium/.test(n) ? 500 : 400; };
  /** Familia CSS lista para usar (o sans-serif mientras carga). */
  function ensureFont(name) {
    if (!name) return "sans-serif";
    const st = FONT_STATE[name];
    if (st === "ok") return `"rf-${name}"`;
    if (!st) {
      FONT_STATE[name] = "loading";
      try {
        const f = FONTS.find(x => x.name === name);
        const ff = new FontFace(`rf-${name}`, `url(${(f && f.url) || "/fx/font/" + encodeURIComponent(name)})`, { weight: String(WEIGHT(name)) });
        ff.load().then(f => { document.fonts.add(f); FONT_STATE[name] = "ok"; FX.onFontLoaded(); })
          .catch(() => { FONT_STATE[name] = "err"; });
      } catch (e) { FONT_STATE[name] = "err"; }
    }
    return "sans-serif";
  }
  FX.ensureFont = ensureFont;
  let fontTimer = null;
  FX.onFontLoaded = () => { clearTimeout(fontTimer); fontTimer = setTimeout(() => { $$(".fontpick [data-font]").forEach(el => { el.style.fontFamily = ensureFont(el.dataset.font); }); }, 60); };
  const fontCss = (name, px, italic) => `${italic ? "italic " : ""}${WEIGHT(name || "")} ${Math.max(1, px).toFixed(1)}px ${ensureFont(name)}, "Helvetica Neue", Arial, sans-serif`;

  // ------------------------------------------------------------------ iconos Tabler
  async function iconSvg(name) {
    if (!name) return null;
    if (ICON_CACHE[name] !== undefined) return ICON_CACHE[name];
    ICON_CACHE[name] = null;
    try {
      const d = await jget(`/fx/icons?q=${encodeURIComponent(name)}&limit=40`);
      (d.icons || []).forEach(ic => { if (ic && ic.name) ICON_CACHE[ic.name] = ic.svg; });
      if (!ICON_CACHE[name]) ICON_CACHE[name] = null;
    } catch (e) { /* sin catalogo */ }
    return ICON_CACHE[name];
  }
  function iconImage(name, color) {
    const key = `${name}|${color}`;
    if (ICON_IMG[key]) return ICON_IMG[key].complete ? ICON_IMG[key] : null;
    const svg = ICON_CACHE[name];
    if (svg === undefined) { iconSvg(name); return null; }
    if (!svg) return null;
    let s = svg.replace(/currentColor/g, color || "#fff");
    if (!/xmlns=/.test(s)) s = s.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
    const im = new Image();
    // tinta centrada como el render (graphics.icon_ink_dx): se mide una vez por glifo al cargar
    if (ICON_DX[name] == null) im.onload = () => { if (ICON_DX[name] == null) ICON_DX[name] = iconDxOf(im); };
    im.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(s);
    ICON_IMG[key] = im; return null;
  }
  function iconDxOf(im) {
    try {
      const n = 96, cv = document.createElement("canvas"); cv.width = cv.height = n;
      const g = cv.getContext("2d"); g.drawImage(im, 0, 0, n, n);
      const d = g.getImageData(0, 0, n, n).data, cols = new Array(n).fill(0);
      for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) cols[x] = Math.max(cols[x], d[(y * n + x) * 4 + 3]);
      return C.iconInkDx(cols);
    } catch (e) { return 0; }             // lienzo contaminado (navegador viejo): sin ajuste
  }

  // ------------------------------------------------------------------ utilidades de edicion
  function syncLocked() {
    if (!isPro()) return;
    PLAN.director = PLAN.director || {};
    PLAN.director.locked = C.lockedIds(PLAN.fx);
  }
  /** Una edicion del usuario sobre un item de la IA lo bloquea (el director no lo pisa al re-dirigir). */
  function autoLock(it) {
    if (it && it.src === "ai" && !it.locked) {
      it.locked = true;
      toast("Editado por ti: queda bloqueado para que la IA no lo cambie al re-dirigir.", "lock");
    }
  }
  /** Envuelve una mutacion atomica: mut() + fn + refresco. */
  function edit(fn, opts) {
    const o = opts || {};
    if (!o.noMut) mut();
    fn();
    syncLocked();
    FX.changed(o);
  }
  FX.edit = edit;
  /** Refresco tras cualquier cambio del plan. */
  FX.changed = function (o) {
    const opt = o || {};
    if (!opt.noLanes) FX.renderLanes();
    if (opt.inspector !== false) FX.refreshInspector(opt.rebuild);
    FX.renderDirectorStats();
    // el aviso del idioma cuenta los titulos traducidos / editados a mano / nuevos
    if (isPro() && ((typeof LANG !== "undefined" && LANG !== "es") || trState())) FX.syncLangNote();
    FX.planDirty();
  };
  /** Gestos de controles: mut() UNA vez al empezar (input) y cierre en change/blur. */
  function gesture(el, apply, after) {
    let open = false;
    const begin = () => { if (!open) { mut(); open = true; } };
    el.addEventListener("input", () => { begin(); apply(); FX.planDirty(); if (after) after(false); });
    const end = () => { if (open) { open = false; if (after) after(true); syncLocked(); } };
    el.addEventListener("change", () => { begin(); apply(); end(); FX.planDirty(); });
    el.addEventListener("blur", end);
  }

  // ------------------------------------------------------------------ seleccion
  FX.selection = () => SELX.slice();
  FX.selectedItems = () => SELX.map(s => ({ ...s, item: items(s.track).find(i => i.id === s.id) })).filter(s => s.item);
  function isSel(track, id) { return SELX.some(s => s.track === track && s.id === id); }
  FX.select = function (track, id, add) {
    if (track == null) { SELX = []; }
    else if (add) {
      const k = SELX.findIndex(s => s.track === track && s.id === id);
      if (k >= 0) SELX.splice(k, 1); else SELX.push({ track, id });
    } else SELX = [{ track, id }];
    if (SELX.length) { SEL = { type: "fx", track: SELX[SELX.length - 1].track, id: SELX[SELX.length - 1].id }; }
    else if (SEL && SEL.type === "fx") SEL = null;
    markSelection();
    FX.inspect(SEL);
    FX.drawOverlayNow && FX.drawOverlayNow();
  };
  function markSelection() {
    $$(".fxb.sel,.fxe.sel,.fxt.sel,.musb2.sel").forEach(e => e.classList.remove("sel"));
    if (SELX.length || (SEL && SEL.type === "music")) $$(".selel,.capb.sel").forEach(e => e.classList.remove("selel", "sel"));
    SELX.forEach(s => { const el = document.querySelector(`[data-fx="${s.track}:${CSS.escape(s.id)}"]`); if (el) el.classList.add("sel"); });
    if (SEL && SEL.type === "music") { const m = $(".musb2"); if (m) m.classList.add("sel"); }
    const n = SELX.length, badge = $("#elemCnt");
    if (badge) { badge.textContent = n > 1 ? n : ""; badge.classList.toggle("on", n > 1); }
  }
  FX.clearSelection = () => { SELX = []; markSelection(); };
  /** Seleccion explicita del usuario: lleva el panel a la pestana Elemento. */
  FX.focusElem = () => { if (window.setPanelTab) setPanelTab("elem"); };

  // ------------------------------------------------------------------ timeline: pistas fx
  const ROWH = 22;          // alto de cada fila de una pista (bloque de 18 px + aire)
  const ROWH_DENSE = 15;    // pista con mas de 3 filas: bloque de 13 px (solo icono y texto chico)
  const EROWH = 16;         // filas de eventos puntuales (SFX / transiciones): rombo de 11 px
  function trackRow(track, extraCls) {
    const m = TRACK_META[track];
    const row = document.createElement("div");
    const n = track === "music" ? "" : items(track).length;
    row.className = "trk fxrow " + (extraCls || "") + (n === 0 ? " empty" : ""); row.dataset.track = track;
    row.style.setProperty("--c", m.rgb);
    row.innerHTML = `<div class="tlab" title="${m.label}${n !== "" ? ` (${n})` : ""}">${ICO(m.icon)}<span class="tn">${m.label}</span>`
      + `${track === "music" ? "" : `<button class="add" title="Agregar a ${m.label} en el playhead">${ICO("plus")}</button>`}</div>`
      + `<div class="lane fxl" data-lane="${track}"></div>`;
    const add = row.querySelector(".add");
    if (add) add.onclick = e => { e.stopPropagation(); FX.addMenu(track, add); };
    return row;
  }

  FX.renderLanes = function () {
    const top = $("#fxRowsTop"), bot = $("#fxRowsBottom");
    if (!top || !bot) return;
    top.innerHTML = ""; bot.innerHTML = "";
    if (!isPro()) { FX.renderCutShade(); return; }
    const tops = mediaTrackOn() ? ["media"].concat(TOP_TRACKS) : TOP_TRACKS;
    tops.forEach(tk => { const r = trackRow(tk); top.appendChild(r); renderTrackLane(tk, r.querySelector(".lane")); });
    const rs = trackRow("sfx"); bot.appendChild(rs); renderTrackLane("sfx", rs.querySelector(".lane"));
    const rm = trackRow("music"); bot.appendChild(rm); renderMusicLane2(rm.querySelector(".lane"));
    markSelection();
    FX.renderCutShade();
    wireMediaDrop();
  };

  /** Sombrea los tramos cortados en TODAS las pistas (una capa sobre el timeline, sin eventos). */
  FX.renderCutShade = function () {
    const inner = $("#tlinner"); if (!inner || typeof PLAN === "undefined" || !PLAN) return;
    let sh = $("#cutShade");
    if (!sh) { sh = document.createElement("div"); sh.id = "cutShade"; inner.appendChild(sh); }
    const px = TL.px, lab = typeof LABW !== "undefined" ? LABW : 134;
    sh.innerHTML = (PLAN.cuts || []).filter(c => c.enabled !== false && c.end > c.start)
      .map(c => `<i style="left:${lab + c.start * px}px;width:${Math.max(1, (c.end - c.start) * px)}px"></i>`).join("");
  };

  function laneShots(lane) {
    (PLAN.shots || []).forEach((s, i) => { if (i === 0) return; const d = document.createElement("div"); d.className = "shot"; d.style.left = (s.start * TL.px) + "px"; lane.appendChild(d); });
  }
  const lockSvg = `<svg class="fxlock" viewBox="0 0 24 24"><use href="#i-lock"/></svg>`;

  function renderTrackLane(track, lane) {
    const arr = items(track), px = TL.px;
    laneShots(lane);
    if (C.isEvent(track)) return renderEventLane(track, lane, arr);
    const iv = arr.map(it => [it.start * px, Math.max(it.start * px + 6, it.end * px)]);
    const { rows, n } = C.packRows(iv, 2);
    // pistas densas (mas de 3 filas encimadas): filas compactas para que quepan mas pistas sin scroll
    const rh = n > 3 ? ROWH_DENSE : ROWH;
    lane.classList.toggle("dense", n > 3);
    lane.style.height = (arr.length ? n * rh + 2 : 16) + "px";
    arr.forEach((it, i) => {
      const el = document.createElement("div");
      const w = Math.max(6, (it.end - it.start) * px);
      el.className = "fxb" + (it.enabled === false ? " off" : "") + (it.src === "user" ? " user" : "") + (w < 16 ? " tiny" : w < 34 ? " narrow" : "");
      el.dataset.fx = `${track}:${it.id}`;
      el.style.left = (it.start * px) + "px"; el.style.width = w + "px"; el.style.top = (2 + rows[i] * rh) + "px";
      const label = C.itemLabel(SCHEMA, track, it);
      el.title = `${label}\n${C.fmtClock(it.start, .1)} - ${C.fmtClock(it.end, .1)}${it.locked ? "\nBloqueado" : ""}${it.src === "user" ? "\nCreado por ti" : ""}`;
      el.innerHTML = `${ICO(kindIcon(track, it))}<span class="lb">${esc(label)}</span>`
        + (it.locked ? lockSvg : "") + `<div class="hnd l"></div><div class="hnd r"></div>`;
      wireBlock(el, track, it);
      lane.appendChild(el);
    });
    wireLaneEmpty(lane);
  }

  function renderEventLane(track, lane, arr) {
    const px = TL.px, showLb = px >= 38;
    const cat = SFXCAT;
    const spans = arr.map(it => {
      const x = it.at * px;
      if (track === "transitions") { const d = Math.max(0.05, +it.dur || 0.4); const a = x - d / 2 * px, b = Math.max(a + 10, x + d / 2 * px); return [a, b + (showLb ? 8 + lbl(it.kind).length * 6 : 0)]; }
      const [sa, sb] = C.eventSpan("sfx", it, cat);
      return [Math.min(x - 7, sa * px), Math.max(x + 7, sb * px) + (showLb ? 10 + String(it.name || "").length * 5.4 : 0)];
    });
    const { rows, n } = C.packRows(spans, 2);
    lane.style.height = (arr.length ? n * EROWH + 6 : 16) + "px";
    const tl = track === "transitions" && arr.length ? tlObj() : null;
    arr.forEach((it, i) => {
      const el = document.createElement("div"); const x = it.at * px, top = 1 + rows[i] * EROWH;
      el.dataset.fx = `${track}:${it.id}`;
      if (track === "transitions") {
        const d = Math.max(0.05, +it.dur || 0.4), w = Math.max(10, d * px);
        const off = it.enabled !== false && C.transitionOffJoin(tl, PLAN, it.at, it.kind);
        el.className = "fxt" + (it.enabled === false ? " off" : "") + (off ? " warn" : "");
        el.style.left = (x - w / 2) + "px"; el.style.width = w + "px"; el.style.top = top + "px";
        el.innerHTML = (showLb ? `<span class="elb">${esc(C.kindLabel(SCHEMA, track, it.kind))}</span>` : "") + `<div class="hnd l"></div><div class="hnd r"></div>`
          + (it.locked ? lockSvg : "");
        el.title = `Transición: ${C.kindLabel(SCHEMA, track, it.kind)} en ${C.fmtClock(it.at, .1)} (${d.toFixed(2)} s)`
          + (off ? "\nLejos de un corte o cambio de toma: se verá como una mezcla sobre el mismo plano. Arrástrala a una unión (marcas rojas de la regla)." : "");
      } else {
        const c = cat[it.name] || {}, hit = +c.hit || 0, sd = +c.dur || 0;
        // un nombre que no esta en el catalogo no suena (el export lo omite: sfx:<id>:nombre_desconocido)
        const unknown = Object.keys(cat).length > 0 && !cat[it.name];
        el.className = "fxe" + (it.enabled === false ? " off" : "") + (unknown ? " warn" : "");
        el.style.left = x + "px"; el.style.top = top + "px"; el.style.width = "1px";
        el.innerHTML = (sd ? `<div class="tail" style="left:${-hit * px}px;width:${Math.max(4, sd * px)}px"></div>` : "")
          + `<div class="dia"></div>` + (showLb ? `<span class="elb">${esc(lbl(it.name))}</span>` : "");
        el.title = `SFX ${lbl(it.name)} en ${C.fmtClock(it.at, .1)} (${(+it.gain_db || 0).toFixed(1)} dB)` + (unknown ? "\nEste sonido no existe en el catálogo: no sonará en el export. Elige otro en el inspector." : "");
      }
      wireBlock(el, track, it);
      lane.appendChild(el);
    });
    wireLaneEmpty(lane);
  }

  /** La pista Recursos se muestra si el plan trae recursos o el motor la declara. */
  function mediaTrackOn() { return items("media").length > 0 || !!(SCHEMA && SCHEMA.tracks && SCHEMA.tracks.media); }
  FX.mediaTrackOn = mediaTrackOn;
  function kindIcon(track, it) {
    if (track === "media") return it && it.kind === "video" ? "film" : "image";
    if (track === "text") return "type";
    if (track === "graphics") return { doodle: "spark", icon: "star", card: "card", lower_third: "lowerthird", cta: "pointer", progress: "minus", sticker_text: "tag", media: "image" }[it.kind] || "shapes";
    if (track === "cutaways") return it.kind === "photo" ? "image" : "scene";
    if (track === "effects") return { zoom: "zoom", punch: "zoom", kenburns: "zoom", shake: "bolt", bw: "contrast", color_pop: "contrast", bg_blur: "drop", bg_replace: "image", subject_outline: "user", rgb_split: "layers", flash_frame: "spark" }[it.kind] || "wand";
    return TRACK_META[track].icon;
  }

  function wireLaneEmpty(lane) {
    lane.addEventListener("pointerdown", e => {
      if (e.target !== lane || FX.busy()) return;
      const t = clamp((e.clientX - lane.getBoundingClientRect().left) / TL.px, 0, dur());
      FX.select(null); window.inspect && inspect(null);
      V.currentTime = t;
    });
  }

  /** Arrastre de bloques / eventos: mover (con seleccion multiple), bordes, iman.
      El iman usa el playhead del INICIO del gesto: al arrastrar un borde el playhead sigue al
      borde (para ver el cuadro) y no debe volverse un objetivo de si mismo. */
  function wireBlock(el, track, it) {
    el.addEventListener("contextmenu", e => { e.preventDefault(); if (FX.busy()) return; if (!isSel(track, it.id)) FX.select(track, it.id); FX.itemMenu(e.clientX, e.clientY); });
    el.addEventListener("dblclick", e => { e.stopPropagation(); if (FX.busy()) return; FX.select(track, it.id); FX.focusElem(); const f = $("#inspBody textarea, #inspBody input[type=text]"); if (f) f.focus(); });
    el.addEventListener("pointerdown", e => {
      if (e.button !== 0 || FX.busy()) return;
      e.stopPropagation(); e.preventDefault();
      const add = e.shiftKey || e.metaKey || e.ctrlKey;
      const mode = e.target.classList.contains("hnd") ? (e.target.classList.contains("l") ? "l" : "r") : "m";
      if (add) { FX.select(track, it.id, true); FX.focusElem(); return; }
      if (!isSel(track, it.id)) FX.select(track, it.id);
      else { SEL = { type: "fx", track, id: it.id }; FX.inspect(SEL); }
      FX.focusElem();
      const group = mode === "m" ? FX.selectedItems() : [{ track, id: it.id, item: it }];
      const orig = group.map(g => ({ g, s: g.item.start, e: g.item.end, at: g.item.at, d: g.item.dur }));
      let began = false;
      const excl = new Set(group.map(g => g.item));
      const ph0 = V.currentTime || 0;
      tlDrag(e, (dt, ev, moved) => {
        if (!moved) return;
        if (!began) { mut(); began = true; el.classList.add("dragging"); }
        const D = dur();
        if (mode === "m") {
          // iman sobre el item principal (inicio o fin)
          const main = orig.find(o => o.g.item === it);
          let d = dt;
          if (TL.snap && !ev.altKey) {
            const a0 = C.isEvent(track) ? main.at : main.s, b0 = C.isEvent(track) ? main.at : main.e;
            const sA = tlSnap(a0 + dt, excl, ph0), sB = b0 !== a0 ? tlSnap(b0 + dt, excl, ph0) : null;
            if (sA != null && (sB == null || Math.abs(sA - (a0 + dt)) <= Math.abs(sB - (b0 + dt)))) { d = sA - a0; tlSnapShow(sA); }
            else if (sB != null) { d = sB - b0; tlSnapShow(sB); } else tlSnapShow(null);
          }
          // limites del grupo
          let lo = -1e9, hi = 1e9;
          orig.forEach(o => { if (C.isEvent(o.g.track)) { lo = Math.max(lo, -o.at); hi = Math.min(hi, D - o.at); } else { lo = Math.max(lo, -o.s); hi = Math.min(hi, D - o.e); } });
          d = clamp(d, lo, hi);
          orig.forEach(o => { const x = o.g.item; if (C.isEvent(o.g.track)) x.at = r3(o.at + d); else { x.start = r3(o.s + d); x.end = r3(o.e + d); } });
        } else if (track === "transitions") {
          const nd = clamp((mode === "r" ? 1 : -1) * dt * 2 + (orig[0].d || 0.4), 0.05, 2);
          it.dur = r3(nd);
        } else {
          let v = (mode === "l" ? orig[0].s : orig[0].e) + dt;
          if (TL.snap && !ev.altKey) { const s = tlSnap(v, excl, ph0); if (s != null) { v = s; tlSnapShow(s); } else tlSnapShow(null); }
          if (mode === "l") it.start = r3(clamp(v, 0, it.end - 0.1));
          else it.end = r3(clamp(v, it.start + 0.1, D));
        }
        FX.renderLanes(); FX.drawOverlayNow();
        if (mode === "l") V.currentTime = it.start + 0.001; else if (mode === "r") V.currentTime = Math.max(0, it.end - 0.04);
      }, moved => {
        tlSnapShow(null);
        if (began) { group.forEach(g => { autoLock(g.item); if (g.track === "media") mediaRefit(g.item); }); syncLocked(); FX.changed({ noMut: true }); }
        else if (mode === "m") {
          // un recurso se ve ya entrado (su entrada dura ~0.35 s): el cabezal va un poco adentro
          const t0 = C.isEvent(track) ? it.at : track === "media" ? Math.min(it.start + 0.6, (it.start + it.end) / 2) : it.start;
          if (V.paused && (V.currentTime < C.itemStart(track, it) - 0.01 || V.currentTime > C.itemEnd(track, it))) V.currentTime = Math.max(0, t0 + (C.isEvent(track) ? -0.15 : track === "media" ? 0 : 0.02));
        }
      });
    });
  }

  // ---- musica en el timeline
  function renderMusicLane2(lane) {
    const m = PLAN.fx.music || {}, px = TL.px, D = dur();
    lane.style.height = (ROWH + 2) + "px";
    const on = m.enabled !== false && m.source !== "none";
    const b = document.createElement("div");
    b.className = "musb2" + (on ? "" : " off"); b.style.left = "0px"; b.style.width = (D * px) + "px";
    const src = { ai: "IA", procedural: "Procedural", library: "Biblioteca", file: "Archivo", none: "Sin música" }[m.source || "ai"] || m.source;
    const bpm = FX.music.bpm ? Math.round(FX.music.bpm) : (+m.bpm > 0 ? +m.bpm : 0);
    b.innerHTML = `${ICO("music")}<span>${esc(src)}${m.mood ? " · " + esc(lbl(m.mood)) : ""}${bpm ? " · " + bpm + " bpm" : ""}${FX.musicStale() && FX.music.file ? " · desactualizada" : ""}</span>`;
    b.onclick = e => { if (e.target !== b && !e.target.closest("span,svg")) return; if (FX.busy()) return; FX.selectMusic(); FX.focusElem(); };
    lane.appendChild(b);
    // pulsos de la musica (tiempo FINAL de la pista -> original del timeline): los de la ultima
    // generada (/music) o, si aun no se genero, la rejilla con la que el director sincronizo
    const grid = (PLAN.director && PLAN.director.music_grid) || null;
    const beats = FX.music.beats && FX.music.beats.length ? FX.music.beats : (!FX.music.file && grid && grid.beats) || [];
    const downs = new Set((FX.music.beats && FX.music.beats.length ? FX.music.downbeats : (grid && grid.downbeats)) || []);
    if (beats.length && px > 6) {
      const tl = tlObj(), every = px > 12 ? 1 : 2;
      beats.forEach((bt, i) => {
        const db = downs.has(bt);
        if (!db && i % every) return;
        const d = document.createElement("div"); d.className = "mbeat" + (db ? " down" : ""); d.style.left = (tl.inverse(+bt) * px) + "px"; b.appendChild(d);
      });
      b.title = `${beats.length} pulsos${downs.size ? `, ${downs.size} tiempos fuertes` : ""}${FX.music.file ? "" : " (rejilla del director)"}`;
    }
    (m.stops || []).forEach(s => {
      const d = document.createElement("div"); d.className = "mstop"; d.style.left = (s.at * px) + "px"; d.style.width = Math.max(3, (+s.dur || 0.5) * px) + "px";
      d.title = `Silencio dramático en ${C.fmtClock(s.at, .1)} (${(+s.dur || 0).toFixed(2)} s) · arrastra para mover`;
      dragMusicPart(d, () => s.at, v => { s.at = r3(clamp(v, 0, D - 0.05)); });
      b.appendChild(d);
    });
    (m.swells || []).forEach(s => {
      const d = document.createElement("div"); d.className = "mswell"; d.style.left = (s.start * px) + "px"; d.style.width = Math.max(4, (s.end - s.start) * px) + "px";
      d.title = `Subida de música ${s.db > 0 ? "+" : ""}${s.db} dB · arrastra para mover`;
      dragMusicPart(d, () => s.start, v => { const L = s.end - s.start; s.start = r3(clamp(v, 0, D - L)); s.end = r3(s.start + L); });
      b.appendChild(d);
    });
    if (m.drop_at != null) {
      const d = document.createElement("div"); d.className = "mdrop"; d.style.left = (m.drop_at * px) + "px";
      d.title = `Golpe principal (drop) en ${C.fmtClock(m.drop_at, .1)} · arrastra`;
      dragMusicPart(d, () => m.drop_at, v => { m.drop_at = r3(clamp(v, 0, D)); });
      b.appendChild(d);
    }
  }
  function dragMusicPart(el, get, set) {
    el.addEventListener("pointerdown", e => {
      if (FX.busy()) return;
      e.stopPropagation(); e.preventDefault();
      const v0 = get(), ph0 = V.currentTime || 0; let began = false;
      tlDrag(e, (dt, ev, moved) => {
        if (!moved) return;
        if (!began) { mut(); began = true; }
        let v = v0 + dt;
        if (TL.snap && !ev.altKey) { const s = tlSnap(v, null, ph0); if (s != null) { v = s; tlSnapShow(s); } else tlSnapShow(null); }
        set(v); FX.renderLanes();
      }, () => { tlSnapShow(null); if (began) FX.changed({ noMut: true }); else { FX.selectMusic(); FX.focusElem(); } });
    });
  }
  FX.selectMusic = function () { SELX = []; SEL = { type: "music" }; markSelection(); FX.inspect(SEL); };

  // ------------------------------------------------------------------ agregar / menus
  let MENU = null;
  function menuEl() {
    if (!MENU) {
      MENU = document.createElement("div"); MENU.className = "fxmenu"; document.body.appendChild(MENU);
      document.addEventListener("pointerdown", e => { if (MENU.classList.contains("on") && !MENU.contains(e.target)) MENU.classList.remove("on"); }, true);
      window.addEventListener("resize", () => MENU.classList.remove("on"));
    }
    return MENU;
  }
  function openMenu(x, y, html, wire) {
    const m = menuEl(); m.innerHTML = html; m.classList.add("on");
    const r = m.getBoundingClientRect();
    m.style.left = clamp(x, 8, innerWidth - r.width - 8) + "px";
    m.style.top = clamp(y, 8, innerHeight - r.height - 8) + "px";
    wire(m);
  }
  FX.addMenu = function (track, anchor) {
    if (track === "media") { FX.pickMedia("media", null, f => FX.addMediaCard(f)); return; }
    if (!SCHEMA) { toast("El catálogo de efectos aún no está disponible. Reintentando…", "alert"); loadCatalogs(); return; }
    const ks = C.kinds(SCHEMA, track);
    const rect = anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: anchor.x, bottom: anchor.y };
    let html = `<div class="mh">Agregar a ${TRACK_META[track].label}</div>`;
    if (track === "sfx") {
      const byCat = {};
      ks.forEach(k => { const c = (SFXCAT[k] && SFXCAT[k].category) || "otros"; (byCat[c] = byCat[c] || []).push(k); });
      Object.keys(byCat).sort().forEach(c => { html += `<div class="mh">${esc(c)}</div>` + byCat[c].map(k => `<button data-k="${esc(k)}" style="--c:${TRACK_META.sfx.rgb}">${ICO("speaker")}${esc(lbl(k))}</button>`).join(""); });
    } else {
      // medios propios (imagen o clip): graphics -> PiP, escenas -> foto (si el motor los declara)
      const mk = C.mediaKindFor(SCHEMA, track);
      if (mk) html += `<button data-media="${esc(mk)}" style="--c:${TRACK_META[track].rgb}">${ICO("upload")}${track === "cutaways" ? "Foto o clip propio…" : "Imagen o video propio…"}</button>`;
      html += ks.map(k => `<button data-k="${esc(k)}" style="--c:${TRACK_META[track].rgb}">${ICO(kindIcon(track, { kind: k, preset: k }))}${esc(C.kindLabel(SCHEMA, track, k))}</button>`).join("");
    }
    openMenu(rect.left, rect.bottom + 4, html, m => {
      m.querySelectorAll("[data-k]").forEach(b => b.onclick = () => { m.classList.remove("on"); FX.addItem(track, b.dataset.k); });
      m.querySelectorAll("[data-media]").forEach(b => b.onclick = () => { m.classList.remove("on"); FX.pickMedia(track, b.dataset.media); });
    });
  };
  FX.addItem = function (track, kind, t, extra) {
    if (!isPro() || FX.busy()) return null;
    const at = t != null ? t : (V.currentTime || 0);
    let it;
    edit(() => {
      it = C.newItem(SCHEMA, track, kind, at, dur(), PLAN.fx);
      if (track === "text" && PLAN.director && PLAN.director.kit && PLAN.director.kit.display && !it._keepFont) it.font = it.font || PLAN.director.kit.display;
      if (extra) Object.keys(extra).forEach(k => C.setPath(it, k, extra[k]));
      PLAN.fx[track] = PLAN.fx[track] || []; PLAN.fx[track].push(it);
    }, { rebuild: true });
    FX.select(track, it.id);
    FX.focusElem();
    // el playhead entra un poco en el elemento para verlo ya animado
    if (!C.isEvent(track) && V.paused) V.currentTime = Math.min(it.end - 0.05, it.start + Math.min(0.6, (it.end - it.start) / 2));
    if (track === "sfx") FX.playSfx(kind);
    return it;
  };

  // ---- medios propios: subir (POST /sources) y crear el item en el playhead. transcribe=0: un medio
  // del Studio (b-roll, logo) no pasa por la transcripcion por clip de "Armar con IA" (no ocupa el whisper)
  async function uploadMedia(file) {
    const fd = new FormData(); fd.append("files", file); fd.append("transcribe", "0");
    const r = await fetch("/sources", { method: "POST", body: fd });
    if (!r.ok) throw new Error(`Error ${r.status}`);
    const d = await r.json(); const s = d && d.sources && d.sources[0];
    if (!s) throw new Error("Formato no admitido (usa JPG, PNG, WEBP, HEIC, MP4 o MOV).");
    return { sid: s.id, type: s.type, name: s.name, w: s.w, h: s.h, dur: s.dur, thumb: s.thumb };
  }
  FX.addMedia = async function (track, kind, file, t) {
    const p = C.mediaParam(SCHEMA, track, kind); if (!p || !file) return null;
    if (window.chip) chip("Subiendo el archivo…");
    try {
      const media = await uploadMedia(file);
      if (p.accept && media.type && !p.accept.includes(media.type)) throw new Error(media.type === "video" ? "aquí solo va una imagen" : "aquí solo va un clip");
      // la escena con foto propia no lleva el titulo de ejemplo; un clip dura lo que dura (hasta 6 s)
      const extra = Object.assign({ [p.k]: media }, track === "cutaways" ? { "text.text": "" } : {});
      const t0 = t != null ? t : (V.currentTime || 0);
      if (media.type === "video" && +media.dur > 0) extra.end = r3(Math.min(dur(), t0 + clamp(+media.dur, 1, 6)));
      const it = FX.addItem(track, kind, t0, extra);
      if (it && media.type === "image" && !media.thumb && /\.(heic|heif|avif)$/i.test(media.name || ""))
        toast("Foto HEIC agregada: el navegador no la muestra en vivo; pausa para ver el cuadro exacto.", "image");
      return it;
    } catch (e) { toast(`No se pudo agregar el archivo: ${e.message || e}`, "alert"); return null; }
    finally { if (window.chip) chip(null); }
  };
  FX.pickMedia = function (track, kind, onPick) {
    const fi = Object.assign(document.createElement("input"), { type: "file", accept: "image/*,video/*" });
    fi.onchange = () => { if (!fi.files[0]) return; if (onPick) onPick(fi.files[0]); else FX.addMedia(track, kind, fi.files[0]); };
    fi.click();
  };
  /** Soltar un archivo sobre la pista Escenas o Graficos lo inserta donde se solto. */
  function wireMediaDrop() {
    ["cutaways", "graphics"].forEach(track => {
      const lane = document.querySelector(`.lane[data-lane="${track}"]`); if (!lane || !SCHEMA) return;
      if (!C.mediaKindFor(SCHEMA, track)) return;
      lane.addEventListener("dragover", e => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { e.preventDefault(); lane.classList.add("dropok"); } });
      lane.addEventListener("dragleave", () => lane.classList.remove("dropok"));
      lane.addEventListener("drop", e => {
        e.preventDefault(); lane.classList.remove("dropok");
        const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]; if (!f) return;
        const t = clamp((e.clientX - lane.getBoundingClientRect().left) / TL.px, 0, dur());
        const kind = C.mediaKindFor(SCHEMA, track, /^video\//.test(f.type) ? "video" : "image");
        if (kind) FX.addMedia(track, kind, f, t);
      });
    });
  }
  FX.addAnyMenu = function (anchor) {
    const r = anchor.getBoundingClientRect();
    const html = `<div class="mh">Agregar en el playhead</div>` + ["text", "graphics", "cutaways", "effects", "transitions", "sfx", "media"]
      .map(t => `<button data-t="${t}" style="--c:${TRACK_META[t].rgb}">${ICO(TRACK_META[t].icon)}${t === "media" ? "Recurso (imagen o video)" : TRACK_META[t].label}</button>`).join("");
    openMenu(r.left, r.bottom + 4, html, m => m.querySelectorAll("[data-t]").forEach(b => b.onclick = () => { m.classList.remove("on"); FX.addMenu(b.dataset.t, anchor); }));
  };
  FX.itemMenu = function (x, y) {
    const sel = FX.selectedItems(); if (!sel.length) return;
    const one = sel.length === 1 ? sel[0] : null;
    const allLocked = sel.every(s => s.item.locked), allOff = sel.every(s => s.item.enabled === false);
    const html = `<div class="mh">${sel.length > 1 ? sel.length + " elementos" : esc(C.itemLabel(SCHEMA, one.track, one.item))}</div>`
      + `<button data-a="dup">${ICO("copy")}Duplicar<small>⌘D</small></button>`
      + `<button data-a="en">${ICO(allOff ? "eye" : "eyeoff")}${allOff ? "Activar" : "Desactivar"}<small>H</small></button>`
      + `<button data-a="lock">${ICO("lock")}${allLocked ? "Desbloquear" : "Bloquear"}<small>L</small></button>`
      + (sel.every(s => s.track === "media") ? `<button data-a="rfile">${ICO("upload")}Reemplazar archivo</button>` : `<button data-a="regen">${ICO("spark")}Regenerar con IA</button>`)
      + `<button data-a="del">${ICO("trash")}Eliminar<small>Supr</small></button>`;
    openMenu(x, y, html, m => m.querySelectorAll("[data-a]").forEach(b => b.onclick = () => { m.classList.remove("on"); FX.action(b.dataset.a); }));
  };

  /** Acciones sobre la seleccion actual. */
  FX.action = async function (a) {
    const sel = FX.selectedItems(); if (!sel.length || FX.busy()) return;
    if (a === "del") {
      edit(() => { sel.forEach(s => { const arr = PLAN.fx[s.track]; const k = arr.indexOf(s.item); if (k >= 0) arr.splice(k, 1); }); SELX = []; SEL = null; });
      FX.select(null); if (window.inspect) inspect(null);
      toast(sel.length > 1 ? `${sel.length} elementos eliminados.` : "Elemento eliminado.", "trash", true);
    } else if (a === "dup") {
      const news = [];
      edit(() => sel.forEach(s => {
        const L = C.isEvent(s.track) ? 0.25 : Math.max(0.2, s.item.end - s.item.start);
        const c = C.duplicateItem(s.track, s.item, L, PLAN.fx, dur());
        PLAN.fx[s.track].push(c); news.push({ track: s.track, id: c.id });
      }));
      SELX = news; FX.select(news[news.length - 1].track, news[news.length - 1].id); SELX = news; markSelection(); FX.focusElem();
    } else if (a === "lock") {
      const v = !sel.every(s => s.item.locked);
      // las piezas de una composicion (palabra gigante, frase y fondo) se bloquean juntas, como el director
      const grp = FX.groupOf(sel.map(s => s.item.id));
      edit(() => { sel.forEach(s => { s.item.locked = v; }); grp.forEach(g => { g.item.locked = v; }); });
      if (grp.length) toast(`${v ? "Bloqueada" : "Desbloqueada"} la composición completa (${grp.length + sel.length} piezas).`, "lock");
    } else if (a === "en") {
      const v = sel.every(s => s.item.enabled === false);
      edit(() => sel.forEach(s => { s.item.enabled = v; }));
    } else if (a === "regen") {
      const ids = sel.filter(s => s.track !== "media").map(s => s.item.id);
      if (ids.length) FX.regenerate(ids);
    } else if (a === "rfile") {
      const m = sel.find(s => s.track === "media");
      if (m) FX.replaceMedia(m.item);
    }
  };

  // ---- recursos (fx.media, style "float_card"): imagen o video como tarjeta flotante sobre el orador
  // desenfocado. Los del guion llegan bloqueados (src "script"); el usuario los mueve, recorta, quita o
  // cambia de archivo. El video va SIEMPRE sin audio.
  const MEDIA_LEN = 3.0;
  function mediaFitFor(it, mdur) {
    return C.insertFit(it.kind, mdur, (+it.end || 0) - (+it.start || 0), it.src_in);
  }
  FX.addMediaCard = async function (file, t) {
    if (!isPro() || FX.busy() || !file) return null;
    if (window.chip) chip("Subiendo el archivo…");
    try {
      const m = await uploadMedia(file);
      const t0 = clamp(t != null ? t : (V.currentTime || 0), 0, Math.max(0, dur() - 0.5));
      const L = m.type === "video" && +m.dur > 0 ? clamp(+m.dur, 1.5, 8) : MEDIA_LEN;
      let it;
      edit(() => {
        it = { id: C.newId("media", C.allIds(PLAN.fx)), start: r3(t0), end: r3(Math.min(dur(), t0 + L)), sid: m.sid, kind: m.type === "video" ? "video" : "image",
          name: String(m.name || "").replace(/\.[A-Za-z0-9]{2,5}$/, ""), style: "float_card", enabled: true, locked: true, src: "user", media_dur: +m.dur || 0 };
        Object.assign(it, mediaFitFor(it, m.dur));
        PLAN.fx.media = PLAN.fx.media || []; PLAN.fx.media.push(it);
      }, { rebuild: true });
      FX.select("media", it.id); FX.focusElem();
      if (V.paused) V.currentTime = Math.min(it.end - 0.05, it.start + 0.6);
      return it;
    } catch (e) { toast(`No se pudo agregar el archivo: ${e.message || e}`, "alert"); return null; }
    finally { if (window.chip) chip(null); }
  };
  FX.replaceMedia = function (it) {
    if (!it) return;
    FX.pickMedia("media", null, async f => {
      if (window.chip) chip("Subiendo el archivo…");
      try {
        const m = await uploadMedia(f);
        edit(() => {
          it.sid = m.sid; it.kind = m.type === "video" ? "video" : "image"; it.media_dur = +m.dur || 0; delete it.path;
          if (!it.name || it.src !== "script") it.name = String(m.name || "").replace(/\.[A-Za-z0-9]{2,5}$/, "");
          Object.assign(it, mediaFitFor(it, m.dur));
        }, { rebuild: true });
        toast("Archivo del recurso reemplazado.", "check", true);
      } catch (e) { toast(`No se pudo reemplazar: ${e.message || e}`, "alert"); }
      finally { if (window.chip) chip(null); }
    });
  };

  FX.copy = () => { const sel = FX.selectedItems(); if (!sel.length) return false; const t0 = Math.min(...sel.map(s => C.itemStart(s.track, s.item))); CLIP = { t0, items: sel.map(s => ({ track: s.track, item: C.clone(s.item) })) }; toast(`${sel.length} copiado(s). ⌘V pega en el playhead.`, "copy"); return true; };
  FX.paste = () => {
    if (!CLIP || !isPro()) return false;
    const shift = (V.currentTime || 0) - CLIP.t0, news = [];
    edit(() => CLIP.items.forEach(c => { const n = C.duplicateItem(c.track, c.item, shift, PLAN.fx, dur()); PLAN.fx[c.track] = PLAN.fx[c.track] || []; PLAN.fx[c.track].push(n); news.push({ track: c.track, id: n.id }); }));
    SELX = news; markSelection(); if (news.length) { SEL = { type: "fx", ...news[news.length - 1] }; FX.inspect(SEL); }
    return true;
  };
  /** Mueve la seleccion dt segundos (Alt + flechas). */
  FX.nudge = dt => {
    const sel = FX.selectedItems(); if (!sel.length) return false;
    edit(() => sel.forEach(s => { const x = s.item; if (C.isEvent(s.track)) x.at = r3(clamp(x.at + dt, 0, dur())); else { const L = x.end - x.start; x.start = r3(clamp(x.start + dt, 0, dur() - L)); x.end = r3(x.start + L); } autoLock(x); }));
    return true;
  };

  // ------------------------------------------------------------------ director (servidor)
  /* Mientras la IA dirige, el editor queda bloqueado (capa con progreso): una edicion hecha en ese
     lapso se perderia al llegar el plan nuevo. "Re-dirigir todo" corre en segundo plano en el
     servidor (progreso por paso y Cancelar); estilo, intensidad y regenerar son rapidos. */
  let DIRECTING = null;
  FX.busy = () => !!DIRECTING;
  FX.serverRev = null;               // plan_rev del servidor sobre el que edita el Studio
  function busyShow(msg, cancelable) {
    const ov = $("#busyOv"); if (!ov) return;
    ov.innerHTML = `<div class="box"><div class="hd"><span class="spinner"></span><b id="busyMsg">${esc(msg)}</b></div>`
      + `<div class="bar"><i id="busyFill" style="width:3%"></i></div><div class="sub" id="busySub">El editor se desbloquea al terminar (Espacio reproduce el video).</div>`
      + (cancelable ? `<button class="btn ghost mini" id="busyCancel">${ICO("x")}Cancelar</button>` : "") + `</div>`;
    ov.classList.add("on"); ov.classList.toggle("bg", !!cancelable);
    ["#render", "#cancel"].forEach(q => { const b = $(q); if (b) b.disabled = true; });
    const cb = $("#busyCancel"); if (cb) cb.onclick = async () => { cb.disabled = true; cb.innerHTML = `<span class="spinner"></span>Cancelando…`; try { await fetch(`/cancel/${JOB}?only=direct`, { method: "POST" }); } catch (e) { /* el sondeo lo resuelve */ } };
  }
  function busyProgress(pct, sub) {
    const f = $("#busyFill"); if (f && pct != null) f.style.width = clamp(+pct || 0, 3, 100) + "%";
    const s = $("#busySub"); if (s && sub) s.textContent = sub;
  }
  function busyHide() { const ov = $("#busyOv"); if (ov) { ov.classList.remove("on"); ov.innerHTML = ""; } ["#render", "#cancel"].forEach(q => { const b = $(q); if (b) b.disabled = false; }); }
  /** Sondeo de /direct en segundo plano -> {plan, plan_rev, warnings} o null si se cancelo. */
  async function waitDirect() {
    for (;;) {
      await new Promise(r => setTimeout(r, 800));
      let j; try { j = await jget(`/status/${JOB}?plan=0`); } catch (e) { continue; }
      const st = j.direct_state || (j.direct && j.direct.state);
      busyProgress(j.direct_pct, j.direct_message || (j.direct && j.direct.message));
      if (st === "done") { const p = await jget(`/plan/${JOB}`); return { plan: p.plan, plan_rev: p.plan_rev, warnings: (j.direct && j.direct.warnings) || [] }; }
      if (st === "canceled") return null;
      if (st === "error") throw new Error(j.direct_error || (j.direct && j.direct.error) || "error del director");
    }
  }
  async function direct(body, msg) {
    if (DIRECTING) return null;
    const bg = !!(body.full && !body.only_ids);
    DIRECTING = { msg, bg }; busyShow(msg, bg); FX.renderDirector();
    const snap = JSON.stringify(PLAN);
    try {
      const req = Object.assign({ plan: PLAN, fmt: FMT || "9:16" }, body);   // el director trabaja con el idioma original
      // lo que el director re-emite con sufijo (fx_hookbg_s0b...) no viaja: si no, se acumula una
      // copia mas en cada re-direccion. La propuesta que vuelve se adopta intacta.
      if (isPro() && req.plan && req.plan.fx) {
        const dr = C.dropReemitted(req.plan.fx);
        if (dr.removed.length) req.plan = Object.assign({}, req.plan, { fx: dr.fx });
      }
      let d;
      if (bg) {
        const r0 = await jpost(`/direct/${JOB}`, Object.assign(req, { background: true }));
        d = r0 && r0.plan ? r0 : await waitDirect();
        if (!d) { toast("Re-dirigir cancelado: la edición no cambió.", "alert"); return null; }
      } else d = await jpost(`/direct/${JOB}`, req);
      if (!d || !d.plan) throw new Error("El director no devolvió un plan.");
      if (d.plan_rev != null) FX.serverRev = d.plan_rev;
      if (JSON.stringify(PLAN) !== snap) {
        // no deberia pasar (el editor esta bloqueado); si pasa, gana lo que hizo el usuario
        toast("Cambiaste la edición mientras la IA trabajaba: se conservaron tus cambios y no se aplicó la propuesta.", "alert");
        FX.markDirty(); return null;
      }
      return d;
    } catch (e) {
      const m = e.status === 404 || e.status === 405 ? "El director todavía no está disponible en este servidor."
        : e.status === 409 ? "El proyecto está ocupado (export o re-dirigir en curso). Intenta en un momento." : `No se pudo dirigir: ${e.message || e}`;
      toast(m, "alert");
      return null;
    } finally { DIRECTING = null; busyHide(); FX.renderDirector(); }
  }
  /** Plan que el servidor acaba de guardar (director, perfil, sincronia con la musica) listo para
      el Studio: conserva las traducciones y el estado propio del Studio y quita las copias de la IA
      de lo bloqueado ANTES de tomar nota de el (no cuenta como edicion: nada se reenvia y la
      sincronizacion automatica con la musica IA sigue). -> quitados. */
  function takeServerPlan(plan) {
    const keep = {};
    Object.keys(PLAN || {}).forEach(k => { if (/^(phrases|titles)_[a-z]{2}$/.test(k) || k === "studio") keep[k] = PLAN[k]; });
    PLAN = plan;
    Object.keys(keep).forEach(k => { if (PLAN[k] == null) PLAN[k] = keep[k]; });
    const removed = isPro() ? C.dedupeFx(PLAN.fx) : [];
    syncLocked();
    FX.noteServerPlan(PLAN);
    noteGrid();
    return removed;
  }
  FX.takeServerPlan = takeServerPlan;
  /** Adopta el plan del director (un paso de deshacer). */
  function adoptPlan(d) {
    mut();
    const removed = takeServerPlan(d.plan);
    syncLocked();
    FX.afterPlanReplaced();
    FX.resetAnalysisSig();
    return removed;
  }
  FX.redirect = async function (full, seed) {
    const d = PLAN.director || {}, first = !isPro();
    const nLocked = isPro() ? C.lockedIds(PLAN.fx).length : 0;
    const r = await direct({ style: FX.dirStyle(), intensity: FX.dirIntensity(), seed: seed != null ? seed : (d.seed != null ? d.seed : 7), full: !!full },
      first ? "Dirigiendo la edición con IA…" : full ? "Re-dirigiendo toda la edición…" : "Aplicando el estilo…");
    if (!r) return false;
    const removed = adoptPlan(r);
    FX.proposal = JSON.stringify(PLAN);
    let msg = first ? "Edición dirigida por la IA: todo queda editable en el timeline." : full ? "Edición re-dirigida." : "Estilo aplicado.";
    if (nLocked) msg += nLocked === 1 ? " Se respetó el elemento bloqueado." : ` Se respetaron ${nLocked} elementos bloqueados.`;
    if (removed.length) msg += ` Se quitaron ${removed.length} repetido${removed.length > 1 ? "s" : ""}.`;
    // musica IA nueva generandose: la edicion se sincroniza sola con sus beats al terminar
    if (r.music && r.music.pending) {
      msg += " La música IA nueva se está componiendo: la edición se sincroniza sola con sus beats.";
      FX.musicSide = { state: "generating", pct: 0, message: "" }; FX.renderMusicSide();
      FX.watchStatus(null, 4000);
    } else if (FX.musicSide && (FX.musicSide.resync === "available" || FX.musicSide.state === "superseded")) {
      FX.musicSide = {}; FX.renderMusicSide(); FX.watchStatus(null, 3000);
    }
    toast(msg + (first ? "" : " ⌘Z para deshacer."), "spark", !first);
    return true;
  };
  FX.regenerate = async function (ids) {
    if (!ids.length || FX.busy()) return;
    // lo bloqueado se desbloquea SOLO en el plan que viaja (el local no cambia hasta el resultado)
    const req = C.clone(PLAN);
    ids.forEach(id => { const f = C.findItem(req.fx, id); if (f) f.item.locked = false; });
    req.director = Object.assign({}, req.director, { locked: C.lockedIds(req.fx) });
    const d = PLAN.director || {}, s0 = d.seed != null ? +d.seed : 7;
    const r = await direct({ plan: req, style: FX.dirStyle(), intensity: FX.dirIntensity(), seed: s0 + 1 + Math.floor(Math.random() * 997), only_ids: ids },
      ids.length > 1 ? `Regenerando ${ids.length} elementos con IA…` : "Regenerando con IA…");
    if (!r) return;
    // del plan del director solo se adopta la nueva version de lo pedido (fxcore.mergeRegenerate)
    const { fx: merged, results, linked } = C.mergeRegenerate(PLAN.fx, r.plan.fx || {}, ids);
    const changed = results.filter(x => x.status === "changed" || x.status === "replaced");
    FX.markDirty();                   // el servidor guardo SU plan: se vuelve a guardar el del Studio
    if (!changed.length && !(linked || []).length) {
      toast(ids.length > 1 ? "La IA no propuso cambios para estos elementos; quedaron como estaban." : "La IA no propuso cambios para este elemento; quedó como estaba.", "alert");
      return;
    }
    mut();
    PLAN.fx = merged; syncLocked();
    FX.afterPlanReplaced();
    SELX = results.map(x => ({ track: x.track, id: x.newId })).filter(x => items(x.track).some(i => i.id === x.id));
    SEL = SELX.length ? { type: "fx", ...SELX[SELX.length - 1] } : null; markSelection(); FX.inspect(SEL, true);
    const same = results.length - changed.length;
    // piezas ligadas que cambiaron con el elemento (sonido de la transicion, acentos, composicion)
    const lk = (linked || []).map(x => x.track === "sfx" ? "su sonido" : x.track === "graphics" ? (x.status === "removed" ? "un acento se quitó" : "sus acentos") : "la composición");
    const lkTxt = lk.length ? ` También cambió ${[...new Set(lk)].join(", ")}.` : "";
    toast(`Regenerado${changed.length > 1 ? ` (${changed.length})` : ""}.${same ? ` ${same} sin cambios.` : ""}${lkTxt} ⌘Z vuelve a la versión anterior.`, "spark", true);
  };
  // ------------------------------------------------------------------ estado del trabajo en segundo plano
  /* Sondeo liviano (/status?plan=0) mientras el editor espera algo del servidor:
     - la musica IA de la propuesta (tarea `music`): al terminar, el servidor guarda la propuesta
       sincronizada a sus beats; si el Studio no la edito desde entonces se adopta sola. Si ya la
       edito, ofrece "Sincronizar con la musica" (re-dirigir con la pista en cache: < 1 s);
     - el proxy 720p del video (seeks rapidos): al quedar listo se cambia la fuente del editor. */
  let WATCH = { timer: null, until: 0, busy: false };
  FX.musicSide = {};                 // {state, pct, message, resync, plan_rev}
  FX.proposal = null;                // JSON del plan tal como lo devolvio el ultimo /direct
  function needWatch() {
    const m = FX.musicSide, ms = m.state;
    // "ready" sin resync es un paso breve antes de sincronizar: se sigue como mucho 20 s
    const ready = ms === "ready" && !m.resync && Date.now() - (m.since || 0) < 20000;
    return Date.now() < WATCH.until || ["generating", "syncing"].includes(ms) || ready || SRC.state === "pending";
  }
  function scheduleWatch(ms) { clearTimeout(WATCH.timer); if (!JOB || JOB === "demo" || !needWatch()) return; WATCH.timer = setTimeout(tickWatch, ms || 1500); }
  async function tickWatch() {
    if (WATCH.busy) return scheduleWatch();
    WATCH.busy = true;
    try { handleStatus(await jget(`/status/${JOB}?plan=0`)); await checkProxy(); } catch (e) { /* se reintenta */ }
    finally { WATCH.busy = false; scheduleWatch(); }
  }
  /** Arranca (o prolonga `forMs`) el sondeo con el /status que ya se tenga. */
  FX.watchStatus = function (j, forMs) {
    if (j) handleStatus(j);
    if (forMs) WATCH.until = Math.max(WATCH.until, Date.now() + forMs);
    scheduleWatch(j ? 1500 : 200);
  };
  let FREED_KICK = null, LOW_WARNED = null;
  function handleStatus(j) {
    if (!j || typeof j !== "object") return;
    // "Liberar espacio" borro el video base y la voz de este proyecto: al abrirlo se vuelven a
    // preparar en segundo plano (si no, cuadro exacto y vista previa van lentos hasta exportar)
    const ana = j.analysis_state || (j.analysis && j.analysis.state) || null;
    if (ana === "freed" && FREED_KICK !== JOB && isPro()) { FREED_KICK = JOB; FX.forceAnalysis(); }
    // poco disco: el aviso sale tambien aqui (analizar y exportar son lo que llena el disco)
    if (j.low_space && LOW_WARNED !== JOB) {
      LOW_WARNED = JOB;
      toast(`${j.low_space_message || "Poco espacio en el disco"}${j.disk_free_gb != null ? ` (quedan ${(+j.disk_free_gb).toFixed(1)} GB)` : ""}. El export puede fallar: libera espacio desde la pantalla de inicio.`, "alert", false, null, true);
    } else if (j.low_space === false && LOW_WARNED === JOB) LOW_WARNED = null;
    // proxy del video: listo -> se cambia la fuente; cualquier otro estado final (error, cancelado)
    // o sin tarea de proxy -> se deja el original y se deja de sondear por el
    if (SRC.state === "pending") {
      const ps = j.proxy_state || (j.proxy && j.proxy.state) || null;
      if (ps === "done") checkProxy();
      else if (ps !== "queued" && ps !== "running") SRC.state = "none";
    }
    // musica IA de la propuesta
    const m = j.music || {};
    const prev = FX.musicSide.state, since = FX.musicSide.since;
    FX.musicSide = { state: j.music_state || m.state || null, pct: j.music_pct != null ? +j.music_pct : +(m.pct || 0),
      message: j.music_message || m.message || "", resync: j.music_resync || m.resync || null, plan_rev: m.plan_rev != null ? +m.plan_rev : null };
    const ms = FX.musicSide;
    ms.since = prev === ms.state && since ? since : Date.now();
    if (ms.state === "synced" && ms.plan_rev != null && FX.serverRev != null && ms.plan_rev > FX.serverRev) adoptSynced(ms.plan_rev);
    if (prev !== ms.state || ["generating", "syncing"].includes(ms.state)) FX.renderMusicSide();
  }
  /** La propuesta que el servidor sincronizo con la musica IA reemplaza a la local SOLO si el
      Studio no la edito (mismo contenido que devolvio /direct y nada pendiente de guardar). */
  let SYNCING = false;
  async function adoptSynced(rev) {
    if (SYNCING || DIRECTING || !isPro()) return;
    if (!FX.isClean()) {
      // el plan local tiene cambios: el autoguardado los manda y el servidor marca la musica
      // `superseded` (resync disponible): se sigue sondeando un rato para ofrecer "Sincronizar"
      WATCH.until = Math.max(WATCH.until, Date.now() + 15000); scheduleWatch(1500);
      FX.renderMusicSide(); return;
    }
    SYNCING = true;
    try {
      const p = await jget(`/plan/${JOB}`);
      if (!p || !p.plan || !FX.isClean()) return;
      FX.adoptServerPlan(p.plan, p.plan_rev);
      FX.proposal = JSON.stringify(PLAN);
      toast("Música IA lista: la edición quedó sincronizada con sus beats.", "music");
    } catch (e) { /* el siguiente sondeo reintenta */ }
    finally { SYNCING = false; FX.renderMusicSide(); }
  }
  /** Aviso de la musica IA de la propuesta en el panel Director (progreso o "Sincronizar"). */
  /* Rejilla de beats del plan (director.music_grid): el director la deja solo si corresponde a la
     pista vigente. Se anota la firma de la musica al recibir un plan con rejilla: mientras no cambie
     lo que cambia el archivo de musica, la edicion sigue sincronizada aunque /status diga
     `resync: available` (p. ej. tras "Sincronizar con la música", que sincroniza en la misma
     respuesta sin tocar el estado de la tarea `music`). */
  let GRID_SIG = null;
  function noteGrid() {
    try { GRID_SIG = isPro() && PLAN.director && PLAN.director.music_grid ? curMusicSig() : null; } catch (e) { GRID_SIG = null; }
  }
  FX.gridSynced = function () {
    try { return !!(isPro() && PLAN.director && PLAN.director.music_grid && GRID_SIG != null && GRID_SIG === curMusicSig()); } catch (e) { return false; }
  };
  /** La edicion no sigue el ritmo de la musica IA lista: el servidor ofrece re-sincronizar y el plan
      no trae la rejilla de la pista vigente. */
  FX.needsResync = function () {
    const ms = FX.musicSide || {};
    return (ms.resync === "available" || ms.state === "superseded") && !FX.gridSynced();
  };
  FX.renderMusicSide = function () {
    const el = $("#dirMusic"); if (!el) return;
    const ms = FX.musicSide || {};
    let h = "";
    if (ms.state === "generating") h = `<div class="dmrow"><span class="spinner"></span><span>Componiendo la música IA… ${Math.round(ms.pct || 0)}%. La edición se sincroniza sola con sus beats al terminar.</span></div>`;
    else if (ms.state === "syncing") h = `<div class="dmrow"><span class="spinner"></span><span>Sincronizando la edición con la música IA…</span></div>`;
    else if (FX.needsResync()) {
      // el motivo que da el servidor: la edicion cambio desde la propuesta, o la sincronia
      // automatica no alcanzo a terminar (p. ej. la maquina estaba ocupada)
      const why = /sin sincron/i.test(ms.message || "") && ms.state !== "superseded" ? "la sincronía automática no alcanzó a terminar" : "la edición cambió desde la propuesta";
      h = `<div class="dmrow warn">${ICO("music")}<span>La música IA está lista, pero ${why}: sus cortes y golpes aún no siguen el ritmo.</span><button class="btn mini" id="dirResync" ${DIRECTING ? "disabled" : ""}>${ICO("music")}Sincronizar con la música</button></div>`;
    }
    else if (ms.state === "error") h = `<div class="dmrow warn">${ICO("alert")}<span>${esc(ms.message || "La música IA falló; suena la procedural.")}</span></div>`;
    el.innerHTML = h;
    const b = $("#dirResync"); if (b) b.onclick = () => FX.redirect(false).then(ok => { if (ok) toast("Edición sincronizada con la música IA.", "music", true); });
  };
  /* Video del editor: proxy H.264 720p del servidor (GET /source?proxy=1: mismo reloj que el
     original, GOP corto = seeks rapidos). Cada URL que usa el <video> sirve SIEMPRE el mismo
     archivo: mientras el proxy no esta se usa el original por su propia URL (/source sin proxy;
     la misma URL con ?proxy=1 cambia de contenido al quedar listo y el navegador pediria rangos
     del archivo anterior: 416 y video roto). Al quedar listo (cabecera X-Reelfy-Proxy o /status)
     se cambia la fuente conservando el instante (y si estaba reproduciendo, sigue). */
  const SRC = { state: null, job: null, since: 0, probing: false };
  FX.srcState = () => SRC.state;
  /** Cabecera del proxy con una peticion de 1 byte -> {state: ready|pending|none, tag}. */
  async function probeProxy(job) {
    // sin cache: la misma URL cambia de contenido al quedar listo el proxy (una respuesta vieja
    // guardada por el navegador diria "pending" para siempre)
    const r = await fetch(`/source/${job}?proxy=1`, { cache: "no-store", headers: { Range: "bytes=0-0" } });
    const st = r.headers.get("X-Reelfy-Proxy") || "none";
    const tag = String(r.headers.get("etag") || r.headers.get("last-modified") || Date.now()).replace(/[^\w]/g, "").slice(-12);
    if (r.body && r.body.cancel) r.body.cancel().catch(() => {});
    return { state: st, tag };
  }
  const proxyUrl = (job, tag) => `/source/${job}?proxy=1&v=${tag || Date.now()}`;
  let VERR = false;
  FX.openSource = function () {
    const job = JOB;
    SRC.job = job; SRC.state = null; SRC.since = Date.now();
    if (!VERR && V) {
      VERR = true;
      // si el proxy no se puede reproducir, el original (mismo reloj) en el mismo instante
      V.addEventListener("error", () => {
        if (!SRC.job || !/proxy=1/.test(V.currentSrc || V.src || "")) return;
        const t = V.currentTime || 0;
        SRC.state = "none";
        V.addEventListener("loadedmetadata", () => { try { V.currentTime = t; } catch (e) { /* nada */ } }, { once: true });
        V.src = `/source/${SRC.job}`;
      });
    }
    probeProxy(job).then(p => {
      if (SRC.job !== job) return;
      SRC.state = p.state;
      V.src = p.state === "ready" ? proxyUrl(job, p.tag) : `/source/${job}`;
      if (p.state === "pending") FX.watchStatus(null);
    }).catch(() => { if (SRC.job !== job) return; SRC.state = "none"; V.src = `/source/${job}`; });
  };
  FX.useProxy = function (tag) {
    if (SRC.job !== JOB || SRC.state === "ready") return;
    SRC.state = "ready";
    const t = V.currentTime || 0, playing = !V.paused;
    const back = () => { try { V.currentTime = t; } catch (e) { /* nada */ } if (playing) V.play().catch(() => {}); };
    V.addEventListener("loadedmetadata", back, { once: true });
    V.src = proxyUrl(JOB, tag);
  };
  /** Mientras el proxy esta pendiente: la cabecera manda (el estado de /status puede quedarse
      atras). Tope de 15 min: despues se sigue con el original. */
  async function checkProxy() {
    if (SRC.state !== "pending" || SRC.probing || SRC.job !== JOB) return;
    if (Date.now() - SRC.since > 15 * 60000) { SRC.state = "none"; return; }
    SRC.probing = true;
    try {
      const p = await probeProxy(SRC.job);
      if (SRC.job !== JOB || SRC.state !== "pending") return;
      if (p.state === "ready") FX.useProxy(p.tag);
      else if (p.state !== "pending") SRC.state = "none";
    } catch (e) { /* se reintenta */ } finally { SRC.probing = false; }
  }

  /** Convierte el plan v1 en v2 con el director (primer "Dirigir con IA"). */
  FX.directFirst = async () => { if (await FX.redirect(true) && isPro()) FX.maybeAutoMusic(); };

  let DSTYLE = null, DINT = null;
  FX.dirStyle = () => DSTYLE || (PLAN.director && PLAN.director.style) || "dinamico";
  FX.dirIntensity = () => (DINT != null ? DINT : (PLAN.director && PLAN.director.intensity != null ? +PLAN.director.intensity : 0.75));

  FX.afterPlanReplaced = function () {
    TLCACHE.key = ""; CAPCACHE.key = ""; INSP_KEY = "";   // el inspector apunta a objetos del plan anterior
    DSTYLE = null; DINT = null;
    noteGrid();
    SELX = SELX.filter(s => items(s.track).some(i => i.id === s.id));
    flatWords = P().flat(); liveWordEl = null;
    FX.syncMode();
    if (window.renderTL) renderTL();
    if (window.renderList) renderList();
    if (window.renderSubs) renderSubs();
    FX.renderAll();
    if (isPro()) syncMusicCache();
    FX.clean.refresh();
    FX.planDirty();
  };

  // ------------------------------------------------------------------ panel Director + grupos del proyecto
  FX.syncMode = function () { document.body.classList.toggle("pro", isPro()); FX.syncProControls(); };
  FX.renderAll = function () {
    FX.syncMode(); FX.renderDirector(); FX.renderProjectGroups(); FX.renderLanes();
    if (window.PF && isPro()) PF.renderEditorGroup();
    FX.inspect(SEL);
  };

  /** Notas del director: las tecnicas (validacion con ids internos) van plegadas en "Detalles". */
  const TECH = /^[\w.]+\[[^\]]*\]\s*:|^[a-z_]+:[\w:.-]+$/i;
  FX.renderDirector = function () {
    const box = $("#dirBody"); if (!box || !PLAN) return;
    const pro = isPro(), st = FX.dirStyle(), inten = FX.dirIntensity(), busy = !!DIRECTING;
    let h = `<div class="dirgrid">` + STYLES.map(s => `<button class="dircard ${s.v === st ? "sel" : ""}" data-v="${s.v}" ${busy ? "disabled" : ""}><b>${ICO(s.icon)}${s.label}</b><span>${s.desc}</span></button>`).join("") + `</div>`;
    h += `<div class="dirrow"><label style="margin:0">Intensidad</label><input type="range" class="rng grow" id="dirInt" min="0" max="100" step="5" value="${Math.round(inten * 100)}" ${busy ? "disabled" : ""}><span class="val" id="dirIntV">${Math.round(inten * 100)}%</span></div>`;
    if (pro) {
      h += `<div class="btnrow" style="margin-bottom:10px">
        <button class="btn mini" id="dirFull" ${busy ? "disabled" : ""} title="Vuelve a dirigir toda la edición con la IA de lenguaje (~1 min). Lo bloqueado se respeta.">${busy ? '<span class="spinner"></span>' : ICO("spark")}Re-dirigir todo</button>
        <button class="btn ghost mini" id="dirVar" ${busy ? "disabled" : ""} title="Otra variación con el mismo estilo (rápido, sin IA de lenguaje)">${ICO("dice")}Otra variación</button></div>`;
      h += `<div id="dirMusic" class="dirmusic"></div><div class="dirstats" id="dirStats"></div>`;
      const d = PLAN.director || {}, all = (d.notes || []).concat(d.warnings || []).map(String);
      const notes = all.filter(n => !TECH.test(n)), tech = all.filter(n => TECH.test(n));
      const kit = d.kit ? Object.entries(d.kit).filter(([k, v]) => typeof v === "string" && k !== "accent").map(([, v]) => v) : [];
      if (notes.length || kit.length || tech.length) {
        h += `<div class="dirnotes">` + (kit.length ? `<div>${ICO("type")}<span>Tipografías: ${esc([...new Set(kit)].join(", "))}</span></div>` : "")
          + notes.map(n => `<div>${ICO("insp")}<span>${esc(es(n))}</span></div>`).join("")
          + (tech.length ? `<details class="dirtech"><summary>Detalles técnicos (${tech.length})</summary>${tech.map(n => `<div>${esc(n)}</div>`).join("")}</details>` : "") + `</div>`;
      }
      h += `<div class="hint" style="margin-top:8px">El estilo y la intensidad se aplican al instante. Lo que edites o bloquees se respeta. ⌘Z deshace.</div>`;
    } else {
      h += `<button class="btn full" id="dirFirst" ${busy ? "disabled" : ""}>${busy ? '<span class="spinner"></span>' : ICO("spark")}Dirigir la edición con IA</button>
        <div class="hint">La IA arma la edición completa (títulos animados, gráficos, escenas, efectos, transiciones, SFX y música) y todo queda editable en el timeline.</div>`;
    }
    box.innerHTML = h;
    const undoPick = ok => { if (!ok) { DSTYLE = null; DINT = null; FX.renderDirector(); } };
    box.querySelectorAll(".dircard").forEach(b => b.onclick = () => {
      if (FX.busy()) return;
      DSTYLE = b.dataset.v; box.querySelectorAll(".dircard").forEach(x => x.classList.toggle("sel", x === b));
      if (isPro()) FX.redirect(false).then(undoPick);
    });
    const ir = $("#dirInt");
    ir.oninput = () => { DINT = +ir.value / 100; $("#dirIntV").textContent = ir.value + "%"; };
    ir.onchange = () => { DINT = +ir.value / 100; if (isPro()) FX.redirect(false).then(undoPick); };
    const f = $("#dirFull"); if (f) f.onclick = () => FX.redirect(true);
    const v = $("#dirVar"); if (v) v.onclick = () => { const s0 = PLAN.director && PLAN.director.seed != null ? +PLAN.director.seed : 7; FX.redirect(false, s0 + 1 + Math.floor(Math.random() * 97)); };
    const ff = $("#dirFirst"); if (ff) ff.onclick = () => FX.directFirst();
    FX.renderDirectorStats();
    FX.renderMusicSide();
    FX.renderMusicPanel();
  };
  FX.renderDirectorStats = function () {
    const el = $("#dirStats"); if (!el || !isPro()) return;
    const n = t => items(t).filter(i => i.enabled !== false).length;
    el.innerHTML = [["text", "títulos"], ["graphics", "gráficos"], ["cutaways", "escenas"], ["effects", "efectos"], ["transitions", "transiciones"], ["sfx", "SFX"]]
      .map(([t, l]) => `<span><i>${n(t)}</i> ${l}</span>`).join("") + `<span><i>${C.lockedIds(PLAN.fx).length}</i> bloqueados</span>`;
  };

  // ---- musica (panel)
  /* Una sola semantica con el esquema: bpm 0 = automatico; los tiempos se muestran en tiempo
     ORIGINAL (el del timeline). La musica generada se marca desactualizada solo si cambia algo que
     cambia el ARCHIVO (fxcore.musicSig: animo, prompt, semilla, cortes...), no el volumen. */
  FX.music = { file: null, bpm: 0, drop: null, beats: [], downbeats: [], sig: null, busy: false, audio: null, node: null, gain: null, warnings: [], bySig: {} };
  const curMusicSig = () => C.musicSig((PLAN.fx && PLAN.fx.music) || {}, tlObj().keeps);
  FX.musicStale = () => !isPro() || !FX.music.file ? false : FX.music.sig !== curMusicSig();
  /** Tras deshacer/rehacer: si esa musica ya se genero, se vuelve a ella. */
  function syncMusicCache() {
    const hit = FX.music.bySig[curMusicSig()];
    if (hit && hit.file !== FX.music.file) { Object.assign(FX.music, hit); FX.loadMusicAudio(); }
  }
  FX.renderMusicPanel = function () {
    const box = $("#musBody"); if (!box) return;
    if (!isPro()) { box.innerHTML = ""; return; }
    const m = PLAN.fx.music = PLAN.fx.music || { enabled: true, source: "ai" };
    const src = m.enabled === false ? "none" : (m.source || "ai");
    const moods = (((SCHEMA && SCHEMA.music && SCHEMA.music.params) || []).find(p => p.k === "mood") || {}).opts || Object.keys(MOOD_ES);
    const bpmAuto = !(+m.bpm > 0);
    const stale = FX.musicStale();
    let h = `<div class="optrow"><div style="flex:1;min-width:0"><label>Origen</label><div class="seg sm" id="musSrc">`
      + [["ai", "IA"], ["procedural", "Procedural"], ["library", "Biblioteca"], ["none", "Ninguna"]].map(([v, l]) => `<button data-v="${v}" class="${v === src ? "sel" : ""}">${l}</button>`).join("") + `</div></div></div>`;
    if (src !== "none") {
      h += `<div class="optrow"><div style="flex:1;min-width:120px"><label>Ánimo</label><select class="fsel" id="musMood"><option value="">Automático</option>`
        + moods.map(o => `<option value="${o}" ${m.mood === o ? "selected" : ""}>${esc(lbl(o))}</option>`).join("") + `</select></div>`
        + `<div><label>BPM <span class="info" data-tip="Automático: la IA elige el tempo según el ánimo. Fijo: la música usa exactamente ese tempo.">i</span></label><div class="fctl"><label class="fnull"><input type="checkbox" id="musBpmAuto" ${bpmAuto ? "checked" : ""}>Auto</label>`
        + `<input type="number" class="fnum" id="musBpm" min="60" max="180" step="1" value="${bpmAuto ? (FX.music.bpm ? Math.round(FX.music.bpm) : 110) : m.bpm}" ${bpmAuto ? "disabled" : ""}></div></div></div>`;
      if (src === "ai") h += `<div class="optrow"><div style="flex:1;min-width:0"><label>Descripción para la IA <span class="info" data-tip="Describe el género y la energía. Siempre es instrumental (sin voces).">i</span></label><input type="text" class="ftext" id="musPrompt" value="${esc(m.prompt || "")}" placeholder="pop moderno, brillante, positivo"></div></div>`;
      if (src === "library") h += `<div id="musLib" class="mlist" style="max-height:160px;margin-bottom:8px"></div>`;
      const inF = +(m.intro_fade || 0), outF = m.fade_out != null ? +m.fade_out : 1.5;
      h += `<div class="optrow"><div style="flex:1;min-width:110px"><label>Entrada suave <span id="musInV">${inF > 0 ? inF.toFixed(2) + " s" : "con su golpe"}</span> <span class="info" data-tip="0 = la música entra con su arranque (golpe incluido). Más = sube en ese tiempo.">i</span></label><input type="range" class="rng w100" id="musIn" min="0" max="3" step="0.05" value="${inF}"></div>`
        + `<div style="flex:1;min-width:110px"><label>Cola final <span id="musOutV">${outF.toFixed(2)} s</span> <span class="info" data-tip="Dónde cae el golpe o el desvanecido final antes del último cuadro.">i</span></label><input type="range" class="rng w100" id="musOut" min="0.6" max="3" step="0.05" value="${outF}"></div></div>`;
      h += `<div class="optrow"><div style="flex:1;min-width:0"><label>Volumen bajo la voz <span id="musGainV">${(+(m.gain_db != null ? m.gain_db : -17)).toFixed(1)} dB</span></label><input type="range" class="rng w100" id="musGain" min="-40" max="0" step="0.5" value="${m.gain_db != null ? m.gain_db : -17}"></div></div>`;
      const busy = FX.music.busy;
      h += `<div class="btnrow">`
        + (FX.music.file && stale ? `<button class="btn mini" id="musUpd" ${busy ? "disabled" : ""} title="Vuelve a generar la música con los ajustes actuales (misma variación)">${busy ? '<span class="spinner"></span>' : ICO("undo")}Actualizar música</button>` : "")
        + `<button class="btn ghost mini" id="musGen" ${busy ? "disabled" : ""} title="Otra variación de la música">${busy && !(FX.music.file && stale) ? '<span class="spinner"></span>' : ICO("dice")}${FX.music.file ? "Otra música" : "Generar música"}</button></div>`;
      h += `<div class="musplayer"><button class="mplay" id="musPlay" title="Escuchar" ${FX.music.file ? "" : "disabled"}>${ICO(FX.music.audio && !FX.music.audio.paused && FX.music.solo ? "pause" : "play")}</button>`
        + `<div class="mi" id="musInfo">${musInfo()}<div class="musbar"><i id="musBar"></i></div></div></div>`;
      if (FX.music.warnings && FX.music.warnings.length) h += `<div class="hint">${FX.music.warnings.map(x => esc(es(x))).join("<br>")}</div>`;
      h += `<div class="hint">Silencios, subidas y el golpe principal se editan en la pista Música (clic en ella).</div>`;
    } else h += `<div class="hint">Sin música de fondo. Los SFX y la voz se mantienen.</div>`;
    box.innerHTML = h;
    // eventos
    box.querySelectorAll("#musSrc button").forEach(b => b.onclick = () => {
      edit(() => { const v = b.dataset.v; if (v === "none") { m.enabled = false; } else { m.enabled = true; m.source = v; } }, { inspector: false });
      FX.renderMusicPanel();
    });
    const mood = $("#musMood"); if (mood) mood.onchange = () => { edit(() => { m.mood = mood.value || null; }, { inspector: false }); FX.renderMusicPanel(); };
    const ba = $("#musBpmAuto"), bn = $("#musBpm");
    if (ba) ba.onchange = () => { edit(() => { if (ba.checked) { m.bpm = 0; m.bpm_lock = false; } else { m.bpm = clamp(Math.round(+bn.value || 110), 60, 180); m.bpm_lock = true; } }, { inspector: false }); FX.renderMusicPanel(); };
    if (bn) bn.onchange = () => { edit(() => { m.bpm = clamp(Math.round(+bn.value || 110), 60, 180); m.bpm_lock = true; }, { inspector: false }); FX.renderMusicPanel(); };
    const pr = $("#musPrompt"); if (pr) gesture(pr, () => { m.prompt = pr.value; }, d => d && FX.renderMusicPanelSoon());
    const g = $("#musGain"); if (g) gesture(g, () => { m.gain_db = +g.value; $("#musGainV").textContent = (+g.value).toFixed(1) + " dB"; FX.applyMusicGain(); });
    const mi = $("#musIn"); if (mi) gesture(mi, () => { m.intro_fade = r3(+mi.value); $("#musInV").textContent = +mi.value > 0 ? (+mi.value).toFixed(2) + " s" : "con su golpe"; }, d => d && FX.renderMusicPanelSoon());
    const mo = $("#musOut"); if (mo) gesture(mo, () => { m.fade_out = r3(+mo.value); $("#musOutV").textContent = (+mo.value).toFixed(2) + " s"; }, d => d && FX.renderMusicPanelSoon());
    const up = $("#musUpd"); if (up) up.onclick = () => FX.genMusic(false);
    const gen = $("#musGen"); if (gen) gen.onclick = () => FX.genMusic(!!FX.music.file);
    const pl = $("#musPlay"); if (pl) pl.onclick = () => FX.toggleMusicSolo();
    if (src === "library") renderMusicLib(m);
  };
  let musPanelTimer = null;
  FX.renderMusicPanelSoon = () => { clearTimeout(musPanelTimer); musPanelTimer = setTimeout(() => { const ae = document.activeElement; if (!ae || !$("#musBody") || !$("#musBody").contains(ae)) FX.renderMusicPanel(); }, 150); };
  function musInfo() {
    const M = FX.music;
    if (M.busy) return `<b>Generando música…</b> · con IA tarda 10-40 s`;
    if (!M.file) return `<b>Sin generar</b> · se genera al exportar`;
    const drop = M.drop != null ? tlObj().inverse(+M.drop) : null;     // el servidor da tiempo final
    return `<b>${M.bpm ? Math.round(M.bpm) + " bpm" : "Sin pulso"}</b>${drop != null ? ` · golpe en ${C.fmtClock(drop, .1)}` : ""}${FX.musicStale() ? ` · <span class="warnt">desactualizada</span>` : ""}`;
  }
  function renderMusicLib(m) {
    const box = $("#musLib"); if (!box) return;
    const list = (TRACKS || []);
    if (!list.length) { box.innerHTML = `<div class="hint">Cargando biblioteca…</div>`; if (window.loadTracks) loadTracks().then(() => renderMusicLib(m)); return; }
    box.innerHTML = list.map(t => `<div class="mrow ${t.id === m.track ? "sel" : ""}" data-id="${esc(t.id)}"><button class="mplay" data-id="${esc(t.id)}" title="Escuchar">${ICO("play")}</button>
      <div class="minfo"><div class="mtitle">${esc(t.title)}</div><div class="mtags">${esc(t.genre || "")}${t.moods && t.moods.length ? " · " + esc(t.moods.slice(0, 3).join(", ")) : ""}</div></div></div>`).join("");
    box.querySelectorAll(".mrow").forEach(r => r.onclick = e => { if (e.target.closest(".mplay")) return; edit(() => { m.track = r.dataset.id; }, { inspector: false }); FX.renderMusicPanel(); });
    box.querySelectorAll(".mplay").forEach(b => b.onclick = e => { e.stopPropagation(); if (window.previewTrack) previewTrack(b.dataset.id, b); });
  }

  FX.genMusic = async function (newSeed) {
    if (!isPro() || FX.music.busy) return;
    const m = PLAN.fx.music;
    if (newSeed) { mut(); m.seed = Math.floor(Math.random() * 999999); FX.planDirty(); }
    FX.music.busy = true; FX.renderMusicPanel();
    const sig = curMusicSig();
    try {
      const d = await jpost(`/music/${JOB}`, { plan: planForServer(), seed: m.seed != null ? m.seed : undefined, fmt: FMT || "9:16" });
      if (!d.file) throw new Error(d.detail || "sin archivo");
      const res = { file: d.file, bpm: +d.bpm || 0, drop: d.drop != null ? +d.drop : null, beats: d.beats || [], downbeats: d.downbeats || [], sig };
      Object.assign(FX.music, res, { warnings: d.warnings || [] });
      FX.music.bySig[sig] = res;
      FX.loadMusicAudio();
      if (newSeed) toast("Música nueva lista. ⌘Z vuelve a la anterior.", "music", true);
    } catch (e) {
      toast(e.status === 404 || e.status === 405 ? "La generación de música aún no está disponible en este servidor." : `No se pudo generar la música: ${e.message || e}`, "alert");
    } finally { FX.music.busy = false; FX.renderMusicPanel(); FX.renderLanes(); }
  };
  FX.loadMusicAudio = function () {
    const M = FX.music; if (!M.file) return;
    if (!M.audio) {
      M.audio = new Audio(); M.audio.preload = "auto"; M.audio.crossOrigin = "anonymous";
      M.audio.addEventListener("timeupdate", () => { const b = $("#musBar"); if (b && M.audio.duration) b.style.width = (M.audio.currentTime / M.audio.duration * 100) + "%"; });
      M.audio.addEventListener("ended", () => { M.solo = false; FX.renderMusicPanel(); });
    }
    const url = /^\/|^https?:/.test(M.file) ? M.file : `/music-file/${encodeURIComponent(M.file)}`;
    if (!M.audio.src.endsWith(url)) M.audio.src = url;
    FX.applyMusicGain();
  };
  FX.applyMusicGain = function () {
    const M = FX.music; if (!M.audio) return;
    const m = (PLAN.fx && PLAN.fx.music) || {};
    const lin = Math.pow(10, (m.gain_db != null ? +m.gain_db : -17) / 20);
    M.baseGain = clamp(lin * 2.2, 0, 1);      // la pista viene normalizada: se acerca al nivel de mezcla
    if (!M.gain) M.audio.volume = M.solo ? 0.8 : M.baseGain;
  };
  FX.toggleMusicSolo = function () {
    const M = FX.music; if (!M.audio) return;
    if (!V.paused) V.pause();
    if (M.solo && !M.audio.paused) { M.audio.pause(); M.solo = false; }
    else { M.solo = true; M.audio.volume = 0.8; if (M.gain) M.gain.gain.value = 0.8; M.audio.currentTime = FX.finalTime(V.currentTime || 0); M.audio.play().catch(() => {}); }
    FX.renderMusicPanel();
  };

  // ---- grupos Subtitulos (pro) y Color
  FX.renderProjectGroups = function () {
    const cb = $("#capProBody"), gb = $("#gradeBody");
    if (!isPro() || !SCHEMA) { if (cb) cb.innerHTML = isPro() ? `<div class="hint">Cargando catálogo…</div>` : ""; if (gb) gb.innerHTML = ""; return; }
    PLAN.fx.captions = PLAN.fx.captions || {};
    PLAN.fx.grade = PLAN.fx.grade || {};
    const ab = $("#accentBody"); if (ab) renderAccent(ab);
    // la palabra activa usa el Color de acento del proyecto (un solo control)
    if (cb) { cb.innerHTML = ""; renderParams(cb, SCHEMA.captions.params.filter(p => p.k !== "accent"), PLAN.fx.captions, { noLock: true, ctx: "captions", onChange: () => { CAPCACHE.key = ""; } }); }
    if (gb) { gb.innerHTML = ""; renderParams(gb, SCHEMA.grade.params, PLAN.fx.grade, { noLock: true, ctx: "grade" }); }
  };

  // ------------------------------------------------------------------ inspector guiado por esquema
  let INSP_KEY = "";
  FX.refreshInspector = function (rebuild) {
    // no reconstruye mientras el usuario escribe en el inspector (solo si cambia la seleccion o se pide)
    const ae = document.activeElement;
    if (!rebuild && ae && $("#inspBody") && $("#inspBody").contains(ae) && /INPUT|TEXTAREA|SELECT/.test(ae.tagName)) { updateTimeFields(); return; }
    FX.inspect(SEL, true);
  };
  function updateTimeFields() {
    const s = SEL; if (!s || s.type !== "fx") return;
    const it = items(s.track).find(i => i.id === s.id); if (!it) return;
    $$("#inspBody [data-k]").forEach(el => { const k = el.dataset.k; if (["start", "end", "at"].includes(k) && document.activeElement !== el) el.value = it[k]; });
  }

  /** Devuelve true si atendio la seleccion (fx/music/captions); false para que index.html siga. */
  FX.inspect = function (sel, force) {
    const b = $("#inspBody"); if (!b) return false;
    // la seleccion solo guarda elementos que siguen en el plan
    if (isPro()) {
      SELX = SELX.filter(x => items(x.track).some(i => i.id === x.id));
      if (sel && sel.type === "fx" && !items(sel.track).some(i => i.id === sel.id)) { sel = SELX.length ? { type: "fx", ...SELX[SELX.length - 1] } : null; SEL = sel; }
    }
    if (!sel || !["fx", "music", "captions"].includes(sel.type)) {
      if (!sel && !SELX.length) { b.innerHTML = emptyInspector(); INSP_KEY = ""; }
      return false;
    }
    // la pestana solo cambia por una seleccion explicita del usuario (FX.focusElem), nunca en un
    // refresco (re-dirigir, deshacer, regenerar): quien mira el Director sigue viendolo
    const key = JSON.stringify([sel, SELX.length]);
    if (!force && key === INSP_KEY && b.childElementCount) return true;
    INSP_KEY = key;
    b.innerHTML = "";
    if (sel.type === "music") { inspectMusic(b); return true; }
    if (sel.type === "captions") { inspectCaptions(b); return true; }
    if (SELX.length > 1) { inspectMulti(b); return true; }
    const it = items(sel.track).find(i => i.id === sel.id);
    if (!it) { b.innerHTML = emptyInspector(); return true; }
    if (sel.track === "media") { inspectMedia(b, it); return true; }
    inspectItem(b, sel.track, it);
    return true;
  };
  /** Duracion del archivo de un recurso (la del item o la del clip ya cargado). */
  function mediaDurOf(it) {
    if (+it.media_dur > 0) return +it.media_dur;
    const e = MEDIA_EL[`video|${it.sid}`];
    return e && e.vid && isFinite(e.vid.duration) ? +e.vid.duration : 0;
  }
  /** Ajuste de un recurso de video a su tramo (al moverlo, recortarlo o cambiar el ajuste). */
  function mediaRefit(it, fit) {
    if (!it) return;
    if (it.kind !== "video") { it.fit = "hold"; it.speed = 1; it.src_in = 0; return; }
    const d = mediaDurOf(it), L = Math.max(0.1, (+it.end || 0) - (+it.start || 0));
    it.fit = fit || it.fit || "hold";
    // la regla de compose._media_fit: se acelera hasta x4 o se alenta hasta x0.8 para llenar el tramo
    if (it.fit === "speed") it.speed = r3(d > 0 ? clamp(d / L, C.INS.slowMin, 4) : (+it.speed || 1));
    else it.speed = 1;
    it.src_in = it.fit === "trim" ? r3(clamp(+it.src_in || 0, 0, Math.max(0, d - L))) : 0;
  }
  FX.mediaRefit = mediaRefit;
  const MEDIA_FIT_ES = { hold: "Completo (se queda en su último cuadro)", speed: "Ajustar la velocidad para que llene su tramo", trim: "Recortar: se ve lo que cabe" };
  /** Inspector de un recurso (fx.media): archivo, tiempo y ajuste. Siempre centrado (simetria) y sin audio. */
  function inspectMedia(b, it) {
    head(b, "media", it, it.name || "Recurso", `Recursos · ${C.fmtClock(it.start, .1)} a ${C.fmtClock(it.end, .1)} (${(it.end - it.start).toFixed(2)} s)`);
    const act = document.createElement("div"); act.className = "ins-actions";
    act.innerHTML = `<button class="btn ghost mini" data-a="en" title="Activar o desactivar (H)">${ICO(it.enabled === false ? "eyeoff" : "eye")}${it.enabled === false ? "Oculto" : "Visible"}</button>`
      + `<button class="btn ghost mini" data-a="dup" title="Duplicar (⌘D)">${ICO("copy")}Duplicar</button>`
      + `<button class="btn ghost mini" data-a="del" title="Eliminar (Supr)">${ICO("trash")}Eliminar</button>`;
    act.querySelectorAll("[data-a]").forEach(x => x.onclick = () => FX.action(x.dataset.a));
    b.appendChild(act);
    const fs = section(b, "Archivo");
    const th = it.sid ? `/tlsource/${encodeURIComponent(it.sid)}/thumb` : "";
    const fr = document.createElement("div"); fr.className = "md-file";
    fr.innerHTML = `<span class="md-th">${th ? `<img alt="" src="${th}">` : ICO("alert")}</span><span class="md-nm"><b>${esc(it.name || "Recurso")}</b><span>${it.sid ? (it.kind === "video" ? "Video · sin audio" : "Imagen · acercamiento suave") : "Falta el archivo"}</span></span>`;
    const rb = document.createElement("button"); rb.className = "btn ghost mini"; rb.innerHTML = `${ICO("upload")}${it.sid ? "Reemplazar" : "Agregar archivo"}`;
    rb.onclick = () => FX.replaceMedia(it); fr.appendChild(rb); fs.appendChild(fr);
    fs.insertAdjacentHTML("beforeend", `<div class="hint">${it.src === "script" ? "Lo pidió el guion: aparece mientras se dice su línea. " : ""}Tarjeta flotante centrada (mismo margen a los lados) sobre el orador desenfocado; los subtítulos siguen encima.</div>`);
    const ts = section(b, "Tiempo");
    const num = (k, label) => {
      const inp = document.createElement("input"); inp.type = "number"; inp.className = "fnum"; inp.step = "0.01"; inp.value = it[k]; inp.dataset.k = k;
      inp.onchange = () => edit(() => {
        const v = r3(+inp.value || 0);
        if (k === "start") it.start = clamp(v, 0, it.end - 0.3); else it.end = clamp(v, it.start + 0.3, dur());
        mediaRefit(it);
      }, { rebuild: true });
      ts.appendChild(frow(label, inp));
    };
    num("start", "Inicio (s)"); num("end", "Fin (s)");
    const r = document.createElement("div"); r.className = "btnrow"; r.style.marginTop = "4px";
    r.innerHTML = `<button class="btn ghost mini" data-m="s">${ICO("tostart")}Inicio al playhead</button><button class="btn ghost mini" data-m="e">${ICO("toend")}Fin al playhead</button>`;
    r.querySelectorAll("[data-m]").forEach(x => x.onclick = () => edit(() => {
      const t = r3(V.currentTime || 0);
      if (x.dataset.m === "s") { const L = it.end - it.start; it.start = clamp(t, 0, dur() - 0.3); if (it.end <= it.start + 0.3) it.end = r3(Math.min(dur(), it.start + L)); }
      else it.end = r3(clamp(t, it.start + 0.3, dur()));
      mediaRefit(it);
    }, { rebuild: true }));
    ts.appendChild(r);
    if (it.kind === "video") {
      const vs = section(b, "Ajuste del video");
      const sel = document.createElement("select"); sel.className = "fsel";
      sel.innerHTML = Object.keys(MEDIA_FIT_ES).map(k => `<option value="${k}" ${k === (it.fit || "hold") ? "selected" : ""}>${MEDIA_FIT_ES[k]}</option>`).join("");
      sel.onchange = () => edit(() => mediaRefit(it, sel.value), { rebuild: true });
      vs.appendChild(frow("Ajuste", sel));
      if (it.fit === "trim") {
        const inp = document.createElement("input"); inp.type = "number"; inp.className = "fnum"; inp.step = "0.1"; inp.min = "0"; inp.value = +it.src_in || 0;
        inp.onchange = () => edit(() => { it.src_in = r3(Math.max(0, +inp.value || 0)); mediaRefit(it); }, { rebuild: true });
        vs.appendChild(frow("Empieza en (s del video)", inp));
      }
      const d = mediaDurOf(it);
      vs.insertAdjacentHTML("beforeend", `<div class="hint">${d ? `El video dura ${d.toFixed(1)} s y el tramo ${(it.end - it.start).toFixed(1)} s.` : ""}${it.fit === "speed" ? ` Se reproduce ${(+it.speed || 1) < 1 ? "más lento" : "acelerado"} a ×${(+it.speed || 1).toFixed(2)}.` : ""} Siempre sin audio.</div>`);
    }
    // estilo de la tarjeta (esquema del motor: desenfoque, oscurecido, esquinas, sombra, entrada...);
    // nunca una posicion X: la tarjeta va centrada (simetria)
    if (SCHEMA) {
      const skip = new Set(["sid", "path", "name", "kind", "fit", "speed", "src_in", "x"]);
      const ps = C.kindParams(SCHEMA, "media", it.style || "float_card").filter(p => !p.internal && !skip.has(p.k));
      if (ps.length) renderParams(section(b, "Estilo de la tarjeta", true), ps, it, { track: "media", item: it });
    }
  }
  function emptyInspector() {
    return `<div class="hint" style="margin:6px 0 12px">Selecciona un elemento del timeline o de la vista previa para editarlo.</div>`
      + (isPro() ? `<div class="subgroup" style="margin-top:0;border:0;padding-top:0"><h5>${ICO("plus")}Agregar en el playhead</h5><div class="btnrow">`
      + ["text", "graphics", "cutaways", "effects", "transitions", "sfx"].map(t => `<button class="btn ghost mini" style="flex:1 1 44%" onclick="FX.addMenu('${t}',this)">${ICO(TRACK_META[t].icon)}${TRACK_META[t].label}</button>`).join("") + `</div></div>` : "");
  }

  function head(b, track, it, title, sub) {
    const m = TRACK_META[track];
    const h = document.createElement("div"); h.className = "ins-head"; h.style.setProperty("--c", m.rgb);
    h.innerHTML = `<div class="ins-sw">${ICO(it ? kindIcon(track, it) : m.icon)}</div><div class="ins-title"><b>${esc(title)}</b><span>${esc(sub || "")}</span></div>`;
    b.appendChild(h);
  }

  function inspectItem(b, track, it) {
    const kk = C.kindKey(SCHEMA, track), kind = it[kk];
    const span = C.isEvent(track) ? `${TRACK_META[track].label} · en ${C.fmtClock(it.at, .1)}` : `${TRACK_META[track].label} · ${C.fmtClock(it.start, .1)} a ${C.fmtClock(it.end, .1)} (${(it.end - it.start).toFixed(2)} s)`;
    head(b, track, it, C.itemLabel(SCHEMA, track, it) || C.kindLabel(SCHEMA, track, kind), span);
    const tags = document.createElement("div"); tags.className = "ins-tags"; tags.id = "insTags";
    tags.innerHTML = tagsHtml(it);
    b.appendChild(tags);
    tags.addEventListener("click", e => {
      if (!e.target.closest("[data-grp]")) return;
      const all = [{ track, id: it.id }].concat(FX.groupOf([it.id]).map(g => ({ track: g.track, id: g.id })));
      SELX = all; SEL = { type: "fx", track, id: it.id }; markSelection(); FX.inspect(SEL, true);
    });
    const act = document.createElement("div"); act.className = "ins-actions";
    act.innerHTML = `<button class="btn ghost mini" data-a="en" title="Activar o desactivar (H)">${ICO(it.enabled === false ? "eyeoff" : "eye")}${it.enabled === false ? "Oculto" : "Visible"}</button>`
      + `<button class="btn ghost mini ${it.locked ? "on" : ""}" data-a="lock" title="La IA no lo cambia al re-dirigir (L)">${ICO("lock")}${it.locked ? "Bloqueado" : "Bloquear"}</button>`
      + `<button class="btn ghost mini" data-a="dup" title="Duplicar (⌘D)">${ICO("copy")}Duplicar</button>`
      + `<button class="btn ghost mini" data-a="del" title="Eliminar (Supr)">${ICO("trash")}Eliminar</button>`;
    act.querySelectorAll("[data-a]").forEach(x => x.onclick = () => FX.action(x.dataset.a));
    b.appendChild(act);
    const regen = document.createElement("button"); regen.className = "btn ghost mini full"; regen.style.marginBottom = "12px";
    regen.innerHTML = `${ICO("spark")}Regenerar con IA`; regen.title = "La IA propone otra versión de este elemento (se desbloquea para esta petición).";
    regen.onclick = () => FX.regenerate([it.id]); regen.disabled = !!DIRECTING;
    b.appendChild(regen);
    if (!SCHEMA) { b.insertAdjacentHTML("beforeend", `<div class="hint">Cargando el catálogo de parámetros…</div>`); return; }
    // tipo / preset
    const ks = C.kinds(SCHEMA, track);
    const tsec = section(b, track === "text" ? "Estilo de título" : track === "sfx" ? "Sonido" : "Tipo");
    if (track === "sfx") tsec.appendChild(sfxPicker(it));
    else {
      const sel = document.createElement("select"); sel.className = "fsel";
      sel.innerHTML = ks.map(k => `<option value="${esc(k)}" ${k === kind ? "selected" : ""}>${esc(C.kindLabel(SCHEMA, track, k))}</option>`).join("");
      sel.onchange = () => { edit(() => { C.changeKind(SCHEMA, track, it, sel.value); autoLock(it); }, { rebuild: true }); };
      tsec.appendChild(frow(track === "text" ? "Preset" : "Tipo", sel));
    }
    // tiempo y comunes
    const common = C.commonParams(SCHEMA, track).filter(p => !["enabled", "locked"].includes(p.k));
    const csec = section(b, "Tiempo");
    renderParams(csec, common, it, { track, item: it });
    if (!C.isEvent(track)) {
      const r = document.createElement("div"); r.className = "btnrow"; r.style.marginTop = "4px";
      r.innerHTML = `<button class="btn ghost mini" data-m="s">${ICO("tostart")}Inicio al playhead</button><button class="btn ghost mini" data-m="e">${ICO("toend")}Fin al playhead</button>`;
      r.querySelectorAll("[data-m]").forEach(x => x.onclick = () => edit(() => {
        const t = r3(V.currentTime || 0);
        if (x.dataset.m === "s") { const L = it.end - it.start; it.start = clamp(t, 0, dur() - 0.1); if (it.end <= it.start + 0.1) it.end = r3(Math.min(dur(), it.start + L)); }
        else it.end = r3(clamp(t, it.start + 0.1, dur()));
        autoLock(it);
      }, { rebuild: true }));
      csec.appendChild(r);
    } else {
      const r = document.createElement("div"); r.className = "btnrow"; r.style.marginTop = "4px";
      r.innerHTML = `<button class="btn ghost mini">${ICO("tostart")}Mover al playhead</button>`;
      r.firstChild.onclick = () => edit(() => { it.at = r3(V.currentTime || 0); autoLock(it); }, { rebuild: true });
      csec.appendChild(r);
    }
    // parametros del kind agrupados
    const ps = C.kindParams(SCHEMA, track, kind);
    const groups = { cont: [], pos: [], anim: [], style: [] };
    ps.forEach(p => {
      const k = p.k;
      if (["text", "lines", "title", "subtitle", "value", "items", "label", "handle", "text.text", "prefix", "suffix", "from", "to", "decimals", "accent_words", "name", "icon", "variant", "text.preset"].includes(k)) groups.cont.push(p);
      else if (["x", "y", "size", "rotation", "align", "path_to"].includes(k)) groups.pos.push(p);
      else if (/^anim_|easing/.test(k)) groups.anim.push(p);
      else groups.style.push(p);
    });
    if (groups.cont.length) {
      const cs = section(b, "Contenido");
      if (track === "text") {
        // text.py usa `lines` (si no esta vacio) e ignora `text`: el campo de texto escribe en las
        // lineas (conservando su estilo) y el editor de lineas queda para el estilo de cada una
        const tp = groups.cont.find(p => p.k === "text");
        if (tp) { const r = titleTextControl(tp, it); if (r) cs.appendChild(r); }
        renderParams(cs, groups.cont.filter(p => p.k !== "text"), it, { track, item: it });
      } else renderParams(cs, groups.cont, it, { track, item: it });
    }
    if (groups.pos.length) renderParams(section(b, "Posición y tamaño"), groups.pos, it, { track, item: it });
    if (groups.style.length) renderParams(section(b, "Estilo"), groups.style, it, { track, item: it });
    if (groups.anim.length) renderParams(section(b, "Animación", true), groups.anim, it, { track, item: it });
  }

  function inspectMulti(b) {
    const sel = FX.selectedItems();
    head(b, sel[0].track, null, `${sel.length} elementos seleccionados`, [...new Set(sel.map(s => TRACK_META[s.track].label))].join(", "));
    const act = document.createElement("div"); act.className = "ins-actions";
    act.innerHTML = `<button class="btn ghost mini" data-a="en">${ICO("eye")}Visibles</button><button class="btn ghost mini" data-a="lock">${ICO("lock")}Bloquear</button>`
      + `<button class="btn ghost mini" data-a="dup">${ICO("copy")}Duplicar</button><button class="btn ghost mini" data-a="del">${ICO("trash")}Eliminar</button>`;
    act.querySelectorAll("[data-a]").forEach(x => x.onclick = () => FX.action(x.dataset.a));
    b.appendChild(act);
    const regen = document.createElement("button"); regen.className = "btn ghost mini full"; regen.innerHTML = `${ICO("spark")}Regenerar con IA`;
    regen.onclick = () => FX.action("regen"); b.appendChild(regen);
    b.insertAdjacentHTML("beforeend", `<div class="hint">Arrastra cualquiera de los bloques seleccionados para moverlos juntos. Alt + flechas los empuja un cuadro.</div>`);
  }

  function inspectMusic(b) {
    const m = PLAN.fx.music = PLAN.fx.music || {};
    head(b, "music", null, "Música de fondo", { ai: "Generada con IA", procedural: "Procedural", library: "Biblioteca", none: "Sin música" }[m.enabled === false ? "none" : (m.source || "ai")]);
    const quick = document.createElement("div"); quick.id = "musBody2";
    b.appendChild(quick);
    const btns = document.createElement("div"); btns.className = "btnrow"; btns.style.marginBottom = "10px";
    btns.innerHTML = `<button class="btn ghost mini" data-m="stop">${ICO("pause")}Silencio en el playhead</button><button class="btn ghost mini" data-m="drop">${ICO("bolt")}Drop en el playhead</button><button class="btn ghost mini" data-m="swell">${ICO("plus")}Subida aquí</button>`;
    btns.querySelectorAll("[data-m]").forEach(x => x.onclick = () => edit(() => {
      const t = r3(V.currentTime || 0);
      if (x.dataset.m === "stop") { m.stops = m.stops || []; m.stops.push({ at: t, dur: 0.8 }); }
      else if (x.dataset.m === "drop") m.drop_at = t;
      else { m.swells = m.swells || []; m.swells.push({ start: t, end: r3(Math.min(dur(), t + 3)), db: 3 }); }
    }, { rebuild: true }));
    b.appendChild(btns);
    if (!SCHEMA) return;
    const skip = new Set(["enabled", "source", "track", "file", "stops", "swells"]);
    const mOpt = { noLock: true, ctx: "music", onChange: () => { FX.applyMusicGain(); FX.renderMusicPanelSoon(); } };
    const dropF = FX.music.drop != null ? ` El último golpe generado cae en ${C.fmtClock(tlObj().inverse(+FX.music.drop), .1)}.` : "";
    b.insertAdjacentHTML("beforeend", `<div class="hint" style="margin:-4px 0 10px">Los tiempos son del video original, como el timeline.${esc(dropF)} BPM 0 = automático.</div>`);
    renderParams(section(b, "Ajustes"), SCHEMA.music.params.filter(p => !skip.has(p.k)), m, mOpt);
    const rowsP = SCHEMA.music.params.filter(p => p.type === "rows");
    if (rowsP.length) { renderParams(section(b, "Silencios y subidas"), rowsP, m, mOpt); return; }
    // silencios y subidas editables
    const ls = section(b, "Silencios y subidas", true);
    (m.stops || []).forEach((s, i) => {
      const r = document.createElement("div"); r.className = "fctl"; r.style.marginBottom = "6px";
      r.innerHTML = `<span class="funit">Silencio</span><input type="number" class="fnum" step="0.01" value="${s.at}" title="Momento (s)"><input type="number" class="fnum" step="0.05" value="${s.dur}" title="Duración (s)"><button class="xbtn" title="Quitar">${ICO("trash")}</button>`;
      const [a, d] = r.querySelectorAll("input");
      a.onchange = () => edit(() => { s.at = r3(+a.value); }, { rebuild: true }); d.onchange = () => edit(() => { s.dur = r3(Math.max(0.05, +d.value)); }, { rebuild: true });
      r.querySelector("button").onclick = () => edit(() => m.stops.splice(i, 1), { rebuild: true });
      ls.appendChild(r);
    });
    (m.swells || []).forEach((s, i) => {
      const r = document.createElement("div"); r.className = "fctl"; r.style.marginBottom = "6px";
      r.innerHTML = `<span class="funit">Subida</span><input type="number" class="fnum" step="0.01" value="${s.start}"><input type="number" class="fnum" step="0.01" value="${s.end}"><input type="number" class="fnum" step="0.5" value="${s.db}" title="dB"><button class="xbtn" title="Quitar">${ICO("trash")}</button>`;
      const [a, e2, d] = r.querySelectorAll("input");
      a.onchange = () => edit(() => { s.start = r3(+a.value); }, { rebuild: true }); e2.onchange = () => edit(() => { s.end = r3(+e2.value); }, { rebuild: true });
      d.onchange = () => edit(() => { s.db = clamp(+d.value, -24, 12); }, { rebuild: true });
      r.querySelector("button").onclick = () => edit(() => m.swells.splice(i, 1), { rebuild: true });
      ls.appendChild(r);
    });
    if (!(m.stops || []).length && !(m.swells || []).length) ls.insertAdjacentHTML("beforeend", `<div class="hint">Sin silencios ni subidas. Usa los botones de arriba.</div>`);
  }

  function inspectCaptions(b) {
    head(b, "text", null, "Subtítulos", "Estilo global de los subtítulos");
    if (!SCHEMA) return;
    PLAN.fx.captions = PLAN.fx.captions || {};
    const as = section(b, "Color de acento del proyecto"); renderAccent(as);
    renderParams(section(b, "Estilo"), SCHEMA.captions.params.filter(p => p.k !== "accent"), PLAN.fx.captions, { noLock: true, ctx: "captions", onChange: () => { CAPCACHE.key = ""; FX.renderProjectGroupsSoon(); } });
  }
  /* Color de acento del proyecto (plan.fx.accent; fxcore.applyAccent): la palabra activa de los
     subtitulos y el acento de titulos y tarjetas. Por defecto el amarillo de siempre. */
  const STYLE_ACCENT = { dinamico: "#FFD23F", elegante: "#E8C27A", minimal: "#FFFFFF", energetico: "#FFE600" };
  FX.projectAccent = () => C.projectAccent(PLAN);
  /** Acento para lo que no trae color propio (= la paleta del render con plan.fx.accent). */
  function ACC() { return C.projectAccent(PLAN); }
  FX.setAccent = function (v) {
    const h = C.normHex(v); if (!h || !isPro() || h === C.projectAccent(PLAN) && PLAN.fx.accent) return false;
    edit(() => { C.applyAccent(PLAN, h, { palette: STYLE_ACCENT[FX.dirStyle()] }); }, { noLanes: false });
    CAPCACHE.key = ""; FX.renderProjectGroupsSoon();
    return true;
  };
  function renderAccent(box) {
    const acc = C.projectAccent(PLAN), dark = lum(acc) > 0.6;
    box.innerHTML = `<div class="acc-row"><input type="color" class="acc-sw" value="${acc.toLowerCase()}" aria-label="Color de acento">`
      + `<input type="text" class="acc-hex" value="${acc}" maxlength="7" spellcheck="false" aria-label="Color de acento en hexadecimal">`
      + `<button class="btn ghost mini" data-a="def"${acc === C.DEFAULT_ACCENT ? " disabled" : ""} title="Volver al amarillo de siempre (#FFD23F)">Amarillo</button></div>`
      + `<div class="acc-demo"><b>Así se ve tu <i style="background:${acc};color:${dark ? "#111" : "#fff"}">acento</i></b></div>`
      + `<div class="hint">Resalta la palabra que se está diciendo en los subtítulos y el acento de títulos y tarjetas.</div>`;
    const sw = box.querySelector(".acc-sw"), hx = box.querySelector(".acc-hex"), def = box.querySelector('[data-a="def"]');
    let open = false;
    const live = v => { const h = C.normHex(v); if (!h) return; if (!open) { mut(); open = true; }
      C.applyAccent(PLAN, h, { palette: STYLE_ACCENT[FX.dirStyle()] }); CAPCACHE.key = ""; FX.planDirty();
      const d = box.querySelector(".acc-demo i"); if (d) { d.style.background = h; d.style.color = lum(h) > 0.6 ? "#111" : "#fff"; } };
    const end = () => { if (open) { open = false; syncLocked(); FX.renderLanesSoon(); FX.renderProjectGroupsSoon(); } };
    sw.addEventListener("input", () => { hx.value = sw.value.toUpperCase(); live(sw.value); });
    sw.addEventListener("change", end);
    hx.addEventListener("change", () => { const h = C.normHex(hx.value); if (!h) { hx.value = C.projectAccent(PLAN); return; } sw.value = h.toLowerCase(); live(h); end(); });
    def.onclick = () => { live(C.DEFAULT_ACCENT); end(); toast("Color de acento: amarillo (predeterminado).", "check", true); };
  }

  function section(b, title, closed) {
    const s = document.createElement("div"); s.className = "ins-sec" + (closed ? " closed" : "");
    s.innerHTML = `<h6>${esc(title)}</h6><div class="ins-body"></div>`;
    s.querySelector("h6").onclick = () => s.classList.toggle("closed");
    b.appendChild(s); return s.querySelector(".ins-body");
  }
  let FID = 0;
  function frow(label, ctl, top, hint) {
    const r = document.createElement("div"); r.className = "frow" + (top === "wide" ? " wide" : top ? " top" : "");
    const l = document.createElement("label"); l.textContent = label; r.appendChild(l);
    if (hint) { const i = document.createElement("span"); i.className = "info"; i.dataset.tip = hint; i.textContent = "i"; l.appendChild(document.createTextNode(" ")); l.appendChild(i); }
    // accesibilidad: la etiqueta apunta al primer control editable
    const inp = /INPUT|SELECT|TEXTAREA/.test(ctl.tagName) ? ctl : ctl.querySelector && ctl.querySelector("input:not([type=checkbox]),select,textarea,input");
    if (inp) { if (!inp.id) inp.id = `fxf${++FID}`; l.htmlFor = inp.id; }
    const c = document.createElement("div"); c.className = "fctl"; c.appendChild(ctl); r.appendChild(c);
    return r;
  }

  /** Dibuja los controles de una lista de parametros del esquema sobre `obj`. */
  function renderParams(box, params, obj, o) {
    const opt = o || {};
    params.forEach(p => {
      if (p.k === "enabled" && opt.noLock === undefined) return;
      const row = paramControl(p, obj, opt);
      if (row) box.appendChild(row);
    });
  }
  FX.renderParams = renderParams;

  function after(obj, opt, done) {
    if (opt.onChange) opt.onChange();
    if (done) {
      if (opt.item) { const was = opt.item.locked; autoLock(opt.item); if (!was && opt.item.locked) { syncLocked(); refreshTags(opt.item); } }
      FX.renderLanes(); FX.renderDirectorStats();
    } else if (opt.item) FX.renderLanesSoon();
  }
  /** Actualiza solo etiquetas y botones del encabezado del inspector (sin reconstruirlo). */
  function refreshTags(it) {
    const tg = $("#insTags"); if (tg) tg.innerHTML = tagsHtml(it);
    const lk = $('#inspBody [data-a="lock"]'); if (lk) { lk.classList.toggle("on", !!it.locked); lk.innerHTML = `${ICO("lock")}${it.locked ? "Bloqueado" : "Bloquear"}`; }
  }
  /** Piezas de la misma composicion (tx_bghero_s<N>, tx_bghero_s<N>b, fx_bgswap_s<N>) que no
      estan en `ids`: el director las trata como una sola (se bloquean y regeneran juntas). */
  const anchorOf = id => { const m = /^(?:tx_bghero|fx_bgswap)_s(\d+)/.exec(String(id || "")); return m ? m[1] : null; };
  FX.groupOf = function (ids) {
    const want = new Set(ids), ancs = new Set(ids.map(anchorOf).filter(a => a != null)), out = [];
    if (!ancs.size) return out;
    C.TRACKS.forEach(tk => items(tk).forEach(it => { if (!want.has(it.id) && ancs.has(anchorOf(it.id))) out.push({ track: tk, id: it.id, item: it }); }));
    return out;
  };
  function tagsHtml(it) {
    const grp = anchorOf(it.id) != null ? FX.groupOf([it.id]) : [];
    return (grp.length ? `<span class="tagp comp" title="Palabra gigante, frase y fondo se editan por separado pero se bloquean y regeneran juntos">${ICO("layers")}Composición (${grp.length + 1} piezas) <button class="linkbtn" type="button" data-grp="1">Seleccionar</button></span>` : "")
      + (it.src === "user" ? `<span class="tagp user">${ICO("user")}Creado por ti</span>` : `<span class="tagp ai">${ICO("spark")}IA</span>`)
      + (it.locked ? `<span class="tagp lock">${ICO("lock")}Bloqueado</span>` : "") + (it.enabled === false ? `<span class="tagp off">${ICO("eyeoff")}Desactivado</span>` : "");
  }
  let laneTimer = null;
  FX.renderLanesSoon = () => { clearTimeout(laneTimer); laneTimer = setTimeout(() => FX.renderLanes(), 120); };

  function paramControl(p, obj, opt) {
    const get = () => C.getPath(obj, p.k);
    const set = v => C.setPath(obj, p.k, v);
    const o2 = Object.assign({}, opt, { _k: p.k });
    const label = PARAM_LABEL[`${opt.ctx}.${p.k}`] || p.label || p.k;
    const t = p.type;
    if (t === "bool") {
      const i = document.createElement("input"); i.type = "checkbox"; i.className = "toggle"; i.checked = !!get();
      i.onchange = () => { edit(() => { set(i.checked); after(obj, o2, true); }, { inspector: false }); };
      return frow(label, i, false, p.hint);
    }
    if (t === "num" && p.len) return vecControl(p, obj, o2, get, set, label);
    if (t === "num") {
      const v = get(), nullable = p.nullable;
      const wrap = document.createElement("div"); wrap.className = "fctl"; wrap.style.width = "100%";
      const leaf = p.k.split(".").pop();
      const isTime = ["start", "end", "at", "src_t", "drop_at"].includes(leaf);
      const bigRange = p.min == null || p.max == null || (p.max - p.min) > 1000;
      const range = !isTime && !bigRange;
      let rg = null;
      if (range) { rg = document.createElement("input"); rg.type = "range"; rg.className = "rng"; rg.min = p.min; rg.max = p.max; rg.step = p.step || 0.01; rg.value = v != null ? v : (p.default != null ? p.default : p.min); wrap.appendChild(rg); }
      const n = document.createElement("input"); n.type = "number"; n.className = "fnum" + (range ? "" : " w"); n.step = p.step || 0.01;
      if (p.min != null) n.min = p.min; if (p.max != null) n.max = p.max;
      n.value = v != null ? +(+v).toFixed(4) : ""; n.dataset.k = p.k;
      // sin valor: se ve el que usara el motor (default del esquema) o "auto"
      if (v == null) n.placeholder = nullable ? "auto" : (p.default != null ? String(p.default) : "");
      wrap.appendChild(n);
      if (p.unit) { const u = document.createElement("span"); u.className = "funit"; u.textContent = p.unit; wrap.appendChild(u); }
      if (nullable) {
        const nl = document.createElement("label"); nl.className = "fnull"; nl.innerHTML = `<input type="checkbox" ${v == null ? "checked" : ""}>Auto`;
        const cb = nl.querySelector("input");
        // x fijo nace centrado (simetria); un rotulo anclado a la izquierda, en el borde de SAFE
        const fixed = () => (p.k === "x" ? (obj.kind === "lower_third" && !C.ltCentered(obj) ? C.SAFE.left : C.XC) : p.default != null ? p.default : (p.min != null ? p.min : 0));
        cb.onchange = () => edit(() => { set(cb.checked ? null : fixed()); after(obj, o2, true); }, { rebuild: true });
        wrap.appendChild(nl);
        if (v == null) { n.disabled = true; if (rg) rg.disabled = true; }
      }
      const val = x => { let y = +x; if (!isFinite(y)) return; if (p.min != null) y = Math.max(p.min, y); if (p.max != null) y = Math.min(p.max, y); if (p.step >= 1) y = Math.round(y); return +y.toFixed(4); };
      if (rg) gesture(rg, () => { const y = val(rg.value); set(y); n.value = y; after(obj, o2, false); }, done => { if (done) after(obj, o2, true); });
      gesture(n, () => { const y = val(n.value); if (y == null) return; set(y); if (rg) rg.value = y; after(obj, o2, false); }, done => {
        if (done) { if (["start", "end"].includes(p.k) && opt.item) { const it = opt.item; if (it.end < it.start + 0.05) { if (p.k === "start") it.end = r3(it.start + 0.1); else it.start = r3(Math.max(0, it.end - 0.1)); } } after(obj, o2, true); }
      });
      return frow(label, wrap, false, p.hint);
    }
    if (t === "text") {
      let v = get();
      if (p.list) {
        const ta = document.createElement("textarea"); ta.className = "ftext"; ta.rows = 2; ta.placeholder = "Una por línea";
        ta.value = Array.isArray(v) ? v.join("\n") : (v || "");
        gesture(ta, () => { set(ta.value.split("\n").map(s => s.trim()).filter(Boolean)); after(obj, o2, false); }, d => d && after(obj, o2, true));
        return frow(label, ta, true);
      }
      const multi = p.multiline;
      const el = document.createElement(multi ? "textarea" : "input"); el.className = "ftext";
      if (!multi) el.type = "text"; else el.rows = 2;
      el.value = v == null ? "" : v;
      if (p.nullable && v == null) el.placeholder = "automático";
      gesture(el, () => { set(el.value === "" && p.nullable ? null : el.value); after(obj, o2, false); }, d => d && after(obj, o2, true));
      return frow(label, el, multi);
    }
    if (t === "color") return colorControl(p, obj, o2, get, set, label);
    if (t === "select" || t === "seg") {
      const v = get(), opts = p.opts || [];
      if (t === "seg" && opts.length <= 5) {
        const s = document.createElement("div"); s.className = "seg sm";
        // rotulo: centrado <-> izquierda cambia el significado de x (centro / borde): se recoloca
        const ltA = p.k === "align" && obj && obj.kind === "lower_third";
        const vv = ltA && v == null ? "left" : v;          // sin align = rotulo de un plan viejo
        s.innerHTML = opts.map(x => `<button data-v="${esc(x)}" class="${x === vv ? "sel" : ""}">${esc(lbl(x))}</button>`).join("");
        s.querySelectorAll("button").forEach(bt => bt.onclick = () => { edit(() => { if (ltA) C.setLowerThirdAlign(obj, bt.dataset.v); else set(bt.dataset.v); after(obj, o2, true); }, ltA ? { rebuild: true } : { inspector: false }); s.querySelectorAll("button").forEach(x => x.classList.toggle("sel", x === bt)); });
        return frow(label, s, false, p.hint);
      }
      const sel = document.createElement("select"); sel.className = "fsel";
      sel.innerHTML = (p.nullable ? `<option value="">Automático</option>` : "") + opts.map(x => `<option value="${esc(x)}" ${x === v ? "selected" : ""}>${esc(lbl(x))}</option>`).join("");
      sel.onchange = () => edit(() => { set(sel.value === "" && p.nullable ? null : sel.value); after(obj, o2, true); }, { inspector: false });
      return frow(label, sel, false, p.hint);
    }
    if (t === "font") return frow(label, fontPicker(get(), p.nullable, v => edit(() => { set(v); after(obj, o2, true); }, { inspector: false })));
    if (t === "icon") return frow(label, iconPicker(get(), v => edit(() => { set(v); after(obj, o2, true); }, { inspector: false })), true);
    if (t === "lines") return frow(label, linesEditor(p, obj, o2, get, set), "wide", p.hint);
    if (t === "rows") return frow(label, rowsEditor(p, obj, o2, get, set), "wide", p.hint);
    if (t === "sfx") return sfxPicker(obj);
    if (t === "media") return frow(label, mediaControl(obj, o2, get, set, p), true, p.hint);
    return null;
  }

  /** Medio propio (imagen o clip subido por /sources): miniatura, nombre, "Cambiar" y, si el
      parametro admite vacio (logo, foto de perfil), "Quitar". Un parametro solo de imagen (`accept`)
      rechaza un clip. */
  function mediaControl(obj, o2, get, set, p) {
    const w = document.createElement("div"); w.className = "mediapick";
    const only = p && p.accept && p.accept.length === 1 ? p.accept[0] : null;
    const draw = () => {
      const v = get() || {};
      const th = v.thumb || (v.sid ? `/tlsource/${encodeURIComponent(v.sid)}/thumb` : "");
      w.innerHTML = `<div class="mth">${th ? `<img src="${esc(th)}" alt="">` : ICO("image")}</div><div class="mnm"><b>${esc(v.name || "Sin archivo")}</b>`
        + `<span>${v.type === "video" ? `Video · ${(+v.dur || 0).toFixed(1)} s` : v.type ? "Imagen" : only === "image" ? "Elige una imagen" : "Elige una imagen o un clip"}${v.w ? ` · ${v.w}×${v.h}` : ""}</span></div>`
        + `<button class="btn ghost mini" type="button" data-a="pick">${ICO("upload")}${v.sid ? "Cambiar" : "Elegir"}</button>`
        + (p && p.nullable && v.sid ? `<button class="xbtn" type="button" data-a="clear" title="Quitar">${ICO("trash")}</button>` : "");
      w.querySelector('[data-a="pick"]').onclick = () => FX.pickMedia(null, null, async file => {
        if (window.chip) chip("Subiendo el archivo…");
        try {
          const m = await uploadMedia(file);
          if (only && m.type && m.type !== only) throw new Error(only === "image" ? "aquí solo va una imagen" : "aquí solo va un clip");
          edit(() => { set(m); after(obj, o2, true); }, { inspector: false }); draw();
        }
        catch (e) { toast(`No se pudo subir: ${e.message || e}`, "alert"); }
        finally { if (window.chip) chip(null); }
      });
      const cl = w.querySelector('[data-a="clear"]');
      if (cl) cl.onclick = () => { edit(() => { set(null); after(obj, o2, true); }, { inspector: false }); draw(); };
    };
    draw();
    return w;
  }

  /** Texto de un titulo: con `lines` escribe en ellas (fxcore.setTitleText) y ofrece quitar el
      estilo por linea; sin lines es el texto simple. Un gesto = un paso de deshacer. */
  function titleTextControl(p, it) {
    const w = document.createElement("div"); w.style.width = "100%";
    const ta = document.createElement("textarea"); ta.className = "ftext"; ta.rows = 2; ta.value = C.titleText(it);
    ta.dataset.k = "title-text";
    w.appendChild(ta);
    const o2 = { track: "text", item: it, _k: "text" };
    gesture(ta, () => { C.setTitleText(it, ta.value); after(it, o2, false); FX.refreshLinesEditor(); }, d => d && after(it, o2, true));
    if (C.hasLines(it)) {
      const note = document.createElement("div"); note.className = "hint"; note.style.margin = "5px 0 0";
      note.innerHTML = `Cada renglón es una línea con su propio estilo (abajo). <button class="linkbtn" type="button">Quitar estilo por línea</button>`;
      note.querySelector("button").onclick = () => edit(() => { C.clearLineStyles(it); autoLock(it); }, { rebuild: true });
      w.appendChild(note);
    }
    return frow(es(p.label || "Texto"), w, true, p.hint);
  }
  /** Refresca los textos del editor de lineas sin reconstruir el inspector (se esta escribiendo). */
  FX.refreshLinesEditor = function () {
    const s = SEL; if (!s || s.type !== "fx" || s.track !== "text") return;
    const it = items("text").find(i => i.id === s.id); if (!it || !C.hasLines(it)) return;
    const ed = $("#inspBody .linesed"); if (!ed) return;
    const rows = ed.querySelectorAll(".lrow");
    // solo se redibuja el editor de lineas: el campo de texto conserva el foco, el cursor y su gesto
    if (rows.length !== it.lines.length) { if (ed._redraw) ed._redraw(); return; }
    rows.forEach((r, i) => { const inp = r.querySelector(".lr1 input"); if (inp && document.activeElement !== inp) inp.value = (it.lines[i] && it.lines[i].text) || ""; });
  };
  let pgTimer = null;
  FX.renderProjectGroupsSoon = () => { clearTimeout(pgTimer); pgTimer = setTimeout(() => FX.renderProjectGroups(), 200); };

  function vecControl(p, obj, o2, get, set, label) {
    const w = document.createElement("div"); w.className = "fctl";
    let v = get();
    const nullOn = v == null;
    const vals = Array.isArray(v) ? v.slice() : (Array.isArray(p.default) ? p.default.slice() : [0.5, 0.5]);
    const ins = [0, 1].map(i => { const n = document.createElement("input"); n.type = "number"; n.className = "fnum"; n.step = p.step || 0.01; n.value = vals[i] != null ? vals[i] : ""; n.disabled = nullOn; w.appendChild(n); return n; });
    ins.forEach((n, i) => gesture(n, () => { vals[i] = clamp(+n.value, p.min != null ? p.min : -1e9, p.max != null ? p.max : 1e9); set(vals.slice()); after(obj, o2, false); }, d => d && after(obj, o2, true)));
    if (p.nullable) {
      const nl = document.createElement("label"); nl.className = "fnull"; nl.innerHTML = `<input type="checkbox" ${nullOn ? "checked" : ""}>Auto`;
      nl.querySelector("input").onchange = e => edit(() => { set(e.target.checked ? null : vals.slice()); after(obj, o2, true); }, { rebuild: true });
      w.appendChild(nl);
    }
    return frow(label, w);
  }

  const NAMED = { white: "#FFFFFF", warm: "#FFB36B", cool: "#8FD3FF", black: "#000000" };
  function colorControl(p, obj, o2, get, set, label) {
    const w = document.createElement("div"); w.className = "fcolor";
    const v = get();
    const toHex = c => { if (!c) return "#ffffff"; if (NAMED[c]) return NAMED[c]; return /^#[0-9a-f]{6}$/i.test(c) ? c : (/^#[0-9a-f]{3}$/i.test(c) ? "#" + c.slice(1).split("").map(x => x + x).join("") : "#ffffff"); };
    if (p.list) {
      const arr = Array.isArray(v) ? v.slice() : [];
      const draw = () => {
        w.innerHTML = "";
        arr.forEach((c, i) => {
          const ci = document.createElement("input"); ci.type = "color"; ci.value = toHex(c);
          gesture(ci, () => { arr[i] = ci.value.toUpperCase(); set(arr.slice()); after(obj, o2, false); }, d => d && after(obj, o2, true));
          const x = document.createElement("button"); x.className = "xbtn"; x.innerHTML = ICO("trash"); x.title = "Quitar";
          x.onclick = () => edit(() => { arr.splice(i, 1); set(arr.slice()); after(obj, o2, true); draw(); }, { inspector: false });
          w.appendChild(ci); w.appendChild(x);
        });
        const add = document.createElement("button"); add.className = "xbtn"; add.innerHTML = ICO("plus"); add.title = "Agregar color";
        add.onclick = () => edit(() => { arr.push(arr[arr.length - 1] || "#FFFFFF"); set(arr.slice()); after(obj, o2, true); draw(); }, { inspector: false });
        w.appendChild(add);
      };
      draw();
      return frow(label, w, true);
    }
    const ci = document.createElement("input"); ci.type = "color"; ci.value = toHex(v); ci.disabled = v == null && p.nullable;
    const hx = document.createElement("input"); hx.type = "text"; hx.className = "hex"; hx.value = v == null ? "" : v; hx.placeholder = p.nullable ? "ninguno" : "#FFFFFF";
    gesture(ci, () => { set(ci.value.toUpperCase()); hx.value = ci.value.toUpperCase(); after(obj, o2, false); }, d => d && after(obj, o2, true));
    gesture(hx, () => { const s = hx.value.trim(); if (/^#[0-9a-f]{6}$/i.test(s) || NAMED[s]) { set(s); ci.value = toHex(s); after(obj, o2, false); } else if (!s && p.nullable) { set(null); after(obj, o2, false); } }, d => d && after(obj, o2, true));
    w.appendChild(ci); w.appendChild(hx);
    (p.presets || []).forEach(pr => {
      const s = document.createElement("span"); s.className = "swatch" + (v === pr ? " sel" : ""); s.style.background = toHex(pr); s.title = lbl(pr);
      s.onclick = () => edit(() => { set(pr); after(obj, o2, true); }, { rebuild: true });
      w.appendChild(s);
    });
    if (p.nullable) {
      const nl = document.createElement("label"); nl.className = "fnull"; nl.innerHTML = `<input type="checkbox" ${v == null ? "checked" : ""}>Ninguno`;
      nl.querySelector("input").onchange = e => edit(() => { set(e.target.checked ? null : (p.default || "#FFD23F")); after(obj, o2, true); }, { rebuild: true });
      w.appendChild(nl);
    }
    return frow(label, w);
  }

  // ---- selector de fuentes con muestras reales
  let FONTLIST = null;
  function fontPicker(value, nullable, onPick) {
    const w = document.createElement("div"); w.className = "fontpick";
    const b = document.createElement("button"); b.type = "button";
    const show = v => { b.innerHTML = `<span data-font="${esc(v || "")}" style="font-family:${v ? ensureFont(v) : "inherit"};font-weight:${WEIGHT(v || "")}">${esc(v ? v.replace(/-/g, " ") : "Automática")}</span><small>${esc(roleOf(v))}</small>`; };
    show(value);
    b.onclick = e => {
      e.stopPropagation();
      if (!FONTLIST) { FONTLIST = document.createElement("div"); FONTLIST.className = "fontlist"; document.body.appendChild(FONTLIST);
        document.addEventListener("pointerdown", ev => { if (FONTLIST.classList.contains("on") && !FONTLIST.contains(ev.target)) FONTLIST.classList.remove("on"); }, true); }
      const list = FONTS.length ? FONTS : [];
      const roles = {}; list.forEach(f => { const r = f.role || "otras"; (roles[r] = roles[r] || []).push(f); });
      FONTLIST.innerHTML = (nullable ? `<div class="fo ${!value ? "sel" : ""}" data-v="">Automática<small>del estilo</small></div>` : "")
        + Object.keys(roles).map(r => `<div class="fgrp">${esc(ROLE_ES[r] || r)}</div>` + roles[r].map(f => `<div class="fo ${f.name === value ? "sel" : ""}" data-v="${esc(f.name)}"><span data-font="${esc(f.name)}" style="font-family:${ensureFont(f.name)};font-weight:${WEIGHT(f.name)}">${esc(f.name.replace(/-/g, " "))}</span><small>Aa 123</small></div>`).join("")).join("");
      const r = b.getBoundingClientRect();
      FONTLIST.style.left = clamp(r.left, 8, innerWidth - 300) + "px"; FONTLIST.style.width = Math.max(240, r.width) + "px";
      FONTLIST.classList.add("on");
      const lr = FONTLIST.getBoundingClientRect();
      FONTLIST.style.top = (r.bottom + lr.height + 8 > innerHeight ? Math.max(8, r.top - lr.height - 4) : r.bottom + 4) + "px";
      FONTLIST.querySelectorAll(".fo").forEach(o => o.onclick = () => { FONTLIST.classList.remove("on"); const v = o.dataset.v || null; show(v); onPick(v); });
    };
    w.appendChild(b); return w;
  }
  const ROLE_ES = { bold: "Negritas", impact: "Impacto", serif: "Serif", serif_italic: "Serif cursiva", script: "Manuscritas", fun: "Divertidas", body: "Texto", mono: "Monoespaciada", display: "Display", otras: "Otras" };
  function roleOf(v) { const f = FONTS.find(x => x.name === v); return f ? (ROLE_ES[f.role] || f.role || "") : ""; }

  // ---- selector de iconos con buscador
  function iconPicker(value, onPick) {
    const w = document.createElement("div"); w.className = "iconpick";
    w.innerHTML = `<div class="cur"><div class="ib"></div><b>${esc(value || "ninguno")}</b></div><input type="text" class="ftext" placeholder="Buscar icono (español o inglés): corazón, ubicación, dinero…"><div class="icongrid"></div>`;
    const ib = w.querySelector(".ib"), q = w.querySelector("input"), grid = w.querySelector(".icongrid");
    const setCur = n => { w.querySelector("b").textContent = n || "ninguno"; iconSvg(n).then(s => { ib.innerHTML = s || ""; }); };
    setCur(value);
    let timer = null, seq = 0;
    const search = async () => {
      const my = ++seq, term = q.value.trim() || value || "star";
      grid.innerHTML = `<div class="hint" style="grid-column:1/-1">Buscando…</div>`;
      try {
        const d = await jget(`/fx/icons?q=${encodeURIComponent(term)}&limit=48`);
        if (my !== seq) return;
        const ics = d.icons || [];
        ics.forEach(ic => { ICON_CACHE[ic.name] = ic.svg; });
        grid.innerHTML = ics.length ? "" : `<div class="hint" style="grid-column:1/-1">Sin resultados.</div>`;
        ics.forEach(ic => { const bt = document.createElement("button"); bt.type = "button"; bt.title = ic.name; bt.innerHTML = ic.svg; if (ic.name === value) bt.classList.add("sel");
          bt.onclick = () => { value = ic.name; grid.querySelectorAll("button").forEach(x => x.classList.toggle("sel", x === bt)); setCur(ic.name); onPick(ic.name); }; grid.appendChild(bt); });
      } catch (e) { grid.innerHTML = `<div class="hint" style="grid-column:1/-1">El buscador de iconos no está disponible.</div>`; }
    };
    q.oninput = () => { clearTimeout(timer); timer = setTimeout(search, 220); };
    search();
    return w;
  }

  // ---- editor de lineas con estilo propio
  function linesEditor(p, obj, o2, get, set) {
    const w = document.createElement("div"); w.className = "linesed";
    const syncTitle = () => {
      if (!C.hasLines(obj)) return;
      obj.text = C.titleText(obj);
      const tt = $('#inspBody [data-k="title-text"]'); if (tt && document.activeElement !== tt) tt.value = obj.text;
    };
    const draw = () => {
      w.innerHTML = "";
      const arr = get();
      if (!Array.isArray(arr) || !arr.length) {
        const bt = document.createElement("button"); bt.className = "btn ghost mini"; bt.innerHTML = `${ICO("plus")}Estilo por línea`;
        bt.title = "Divide el texto en líneas con tamaño, fuente y color propios";
        bt.onclick = () => edit(() => { const txt = String(obj.text || "").split("\n").filter(Boolean); set((txt.length ? txt : ["Linea"]).map((t, i) => ({ text: t, scale: i === 0 ? 1 : 0.6, font: null, color: null, italic: false }))); after(obj, o2, true); }, { rebuild: true });
        w.appendChild(bt); return;
      }
      arr.forEach((ln, i) => {
        const r = document.createElement("div"); r.className = "lrow";
        r.innerHTML = `<div class="lr1"><input type="text" value="${esc(ln.text || "")}"><button class="xbtn" data-a="up" title="Subir">${ICO("up")}</button><button class="xbtn" data-a="rm" title="Quitar">${ICO("trash")}</button></div>
          <div class="lr2"><div class="fp"></div><input type="number" class="fnum" step="0.05" min="0.2" max="4" value="${ln.scale != null ? ln.scale : 1}" title="Escala"><label class="fnull" title="Cursiva"><input type="checkbox" ${ln.italic ? "checked" : ""}>Cursiva</label></div>
          <div class="fcolor" style="margin-top:6px"><input type="color" value="${ln.color && /^#[0-9a-f]{6}$/i.test(ln.color) ? ln.color : "#ffffff"}" ${ln.color ? "" : "disabled"}><label class="fnull"><input type="checkbox" ${ln.color ? "" : "checked"}>Color del texto</label></div>`;
        const [ti] = r.querySelectorAll(".lr1 input"); const sc = r.querySelector(".lr2 .fnum"); const it = r.querySelector(".lr2 .fnull input");
        const cc = r.querySelector(".fcolor input[type=color]"), cn = r.querySelector(".fcolor .fnull input");
        gesture(ti, () => {
          ln.text = ti.value; obj.text = C.titleText(obj);          // `text` sigue al contenido de las lineas
          const tt = $('#inspBody [data-k="title-text"]'); if (tt && document.activeElement !== tt) tt.value = C.titleText(obj);
          after(obj, o2, false);
        }, d => d && after(obj, o2, true));
        gesture(sc, () => { ln.scale = clamp(+sc.value || 1, 0.2, 4); after(obj, o2, false); }, d => d && after(obj, o2, true));
        it.onchange = () => edit(() => { ln.italic = it.checked; after(obj, o2, true); }, { inspector: false });
        gesture(cc, () => { ln.color = cc.value.toUpperCase(); after(obj, o2, false); }, d => d && after(obj, o2, true));
        cn.onchange = () => edit(() => { ln.color = cn.checked ? null : "#FFD23F"; after(obj, o2, true); draw(); }, { inspector: false });
        r.querySelector(".fp").appendChild(fontPicker(ln.font, true, v => edit(() => { ln.font = v; after(obj, o2, true); }, { inspector: false })));
        r.querySelector('[data-a="rm"]').onclick = () => {
          const last = arr.length === 1;
          edit(() => { arr.splice(i, 1); if (!arr.length) set(null); syncTitle(); after(obj, o2, true); if (!last) draw(); }, last ? { rebuild: true } : { inspector: false });
        };
        r.querySelector('[data-a="up"]').onclick = () => { if (!i) return; edit(() => { const x = arr.splice(i, 1)[0]; arr.splice(i - 1, 0, x); syncTitle(); after(obj, o2, true); draw(); }, { inspector: false }); };
        w.appendChild(r);
      });
      const add = document.createElement("button"); add.className = "btn ghost mini"; add.innerHTML = `${ICO("plus")}Agregar línea`;
      add.onclick = () => edit(() => { arr.push({ text: "Nueva línea", scale: 0.6, font: null, color: null, italic: false }); syncTitle(); after(obj, o2, true); draw(); }, { inspector: false });
      w.appendChild(add);
    };
    w._redraw = draw;
    draw();
    return w;
  }

  // ---- lista de objetos (type rows: p. ej. music.stops / music.swells)
  function rowsEditor(p, obj, o2, get, set) {
    const w = document.createElement("div"); w.className = "linesed";
    const draw = () => {
      w.innerHTML = "";
      const arr = Array.isArray(get()) ? get() : [];
      arr.forEach((row, i) => {
        const box = document.createElement("div"); box.className = "lrow";
        const hd = document.createElement("div"); hd.className = "fctl"; hd.style.justifyContent = "space-between"; hd.style.marginBottom = "6px";
        hd.innerHTML = `<span class="funit">${i + 1}</span><span style="display:flex;gap:4px"><button class="xbtn" data-a="go" title="Ir al momento">${ICO("tostart")}</button><button class="xbtn" data-a="rm" title="Quitar">${ICO("trash")}</button></span>`;
        hd.querySelector('[data-a="rm"]').onclick = () => { edit(() => { arr.splice(i, 1); set(arr.slice()); after(obj, o2, true); }, { inspector: false }); draw(); };
        hd.querySelector('[data-a="go"]').onclick = () => { const t0 = row.at != null ? row.at : row.start; if (t0 != null) V.currentTime = Math.max(0, +t0); };
        box.appendChild(hd);
        (p.fields || []).forEach(f => { const r = paramControl(f, row, Object.assign({}, o2)); if (r) box.appendChild(r); });
        w.appendChild(box);
      });
      const add = document.createElement("button"); add.className = "btn ghost mini"; add.innerHTML = `${ICO("plus")}Agregar en el playhead`;
      add.onclick = () => {
        const t = r3(V.currentTime || 0), row = {};
        (p.fields || []).forEach(f => { if (f.default !== undefined && f.default !== null) row[f.k] = C.clone(f.default); });
        if ((p.fields || []).some(f => f.k === "at")) row.at = t;
        if ((p.fields || []).some(f => f.k === "start")) { row.start = t; row.end = r3(Math.min(dur(), t + 2.5)); }
        if ((p.fields || []).some(f => f.k === "dur") && row.dur == null) row.dur = 0.8;
        edit(() => { const a = Array.isArray(get()) ? get() : []; a.push(row); set(a); after(obj, o2, true); }, { inspector: false });
        draw();
      };
      w.appendChild(add);
    };
    draw();
    return w;
  }

  // ---- SFX: selector con escuchar
  function sfxPicker(it) {
    const w = document.createElement("div");
    const names = SCHEMA ? C.kinds(SCHEMA, "sfx") : Object.keys(SFXCAT);
    const byCat = {}; names.forEach(k => { const c = (SFXCAT[k] && SFXCAT[k].category) || "otros"; (byCat[c] = byCat[c] || []).push(k); });
    // nombre fuera del catalogo (el export lo omite): se muestra tal cual y marcado, no el primero de la lista
    const unknown = names.length > 0 && !names.includes(it.name);
    w.innerHTML = `<div class="sfxrow"><select class="fsel${unknown ? " bad" : ""}">${unknown ? `<option value="${esc(it.name)}" selected>${esc(lbl(it.name || "sin nombre"))} (no existe)</option>` : ""}${Object.keys(byCat).sort().map(c => `<optgroup label="${esc(c)}">${byCat[c].map(k => `<option value="${esc(k)}" ${k === it.name ? "selected" : ""}>${esc(lbl(k))}</option>`).join("")}</optgroup>`).join("")}</select>
      <button class="btn ghost mini" title="Escuchar">${ICO("play")}Escuchar</button></div><div class="sfxdesc"></div>`
      + (unknown ? `<div class="insnote bad" id="sfxUnknown">${ICO("alert")}<span>Este sonido no existe en el catálogo: no sonará y el export lo omite. Elige otro.</span></div>` : "");
    const sel = w.querySelector("select"), desc = w.querySelector(".sfxdesc");
    const setDesc = () => { const c = SFXCAT[sel.value]; desc.textContent = c ? `${c.desc || ""}${c.dur ? ` · ${(+c.dur).toFixed(2)} s` : ""}` : ""; };
    setDesc();
    sel.onchange = () => { const was = unknown; edit(() => { it.name = sel.value; autoLock(it); }, was ? { rebuild: true } : { inspector: false }); if (!was) setDesc(); FX.playSfx(sel.value, it.gain_db); };
    w.querySelector("button").onclick = () => FX.playSfx(sel.value, it.gain_db, it.pan);
    return w;
  }

  // ------------------------------------------------------------------ audio: SFX en vivo y musica sincronizada
  const SFXBUF = {};
  function audioCtx() { if (window.ensureAudio) ensureAudio(); return AC || null; }
  async function sfxBuffer(name) {
    const ac = audioCtx(); if (!ac) return null;
    if (SFXBUF[name]) return SFXBUF[name];
    SFXBUF[name] = fetch(`/fx/sfx/${encodeURIComponent(name)}`).then(r => { if (!r.ok) throw new Error(); return r.arrayBuffer(); })
      .then(b => ac.decodeAudioData(b)).catch(() => null);
    return SFXBUF[name];
  }
  FX.playSfx = async function (name, gainDb, pan, when) {
    const ac = audioCtx(); if (!ac) { const a = new Audio(`/fx/sfx/${encodeURIComponent(name)}`); a.play().catch(() => {}); return; }
    if (ac.state === "suspended") ac.resume();
    const buf = await sfxBuffer(name); if (!buf) return;
    const src = ac.createBufferSource(); src.buffer = buf;
    const g = ac.createGain(); g.gain.value = Math.pow(10, (gainDb != null ? +gainDb : -6) / 20) * 1.6;
    let node = src.connect(g);
    if (ac.createStereoPanner) { const p = ac.createStereoPanner(); p.pan.value = clamp(+pan || 0, -1, 1); g.connect(p); p.connect(ac.destination); }
    else g.connect(ac.destination);
    src.start(when || 0);
  };
  let lastT = null;
  /** Llamado en cada cuadro de draw(): SFX en vivo y musica generada sincronizada al tiempo final. */
  FX.tick = function (t, playing) {
    if (!isPro()) return;
    const M = FX.music;
    if (playing) {
      // en TIEMPO FINAL (como el mix): el golpe cae en remap(at) y el sonido arranca `hit` antes,
      // aunque ese arranque quede dentro de un corte que la reproduccion salta
      const tf = FX.finalTime(t);
      if (FX.sfxOn && lastT != null && tf > lastT && tf - lastT < 0.3) {
        items("sfx").forEach(s => {
          if (s.enabled === false) return;
          const c = SFXCAT[s.name] || {}; const tt = FX.finalTime(s.at) - (+c.hit || 0);
          if (tt > lastT && tt <= tf) FX.playSfx(s.name, s.gain_db, s.pan);
        });
        // precarga de lo que viene
        items("sfx").forEach(s => { if (s.at > t && s.at < t + 2.5 && !SFXBUF[s.name]) sfxBuffer(s.name); });
      }
      lastT = tf;
      // musica
      const m = PLAN.fx.music || {};
      if (M.audio && M.file && FX.musicOn && m.enabled !== false && m.source !== "none") {
        if (M.solo) { M.solo = false; }
        ensureMusicNode();
        const tf = FX.finalTime(t);
        if (M.audio.paused) { M.audio.currentTime = tf; M.audio.play().catch(() => {}); }
        else if (Math.abs(M.audio.currentTime - tf) > 0.2) M.audio.currentTime = tf;
        duckMusic(t);
      } else if (M.audio && !M.audio.paused && !M.solo) M.audio.pause();
    } else {
      lastT = null;
      if (M.audio && !M.audio.paused && !M.solo) M.audio.pause();
    }
  };
  function ensureMusicNode() {
    const M = FX.music, ac = audioCtx();
    if (!ac || M.node || !M.audio) return;
    try { M.node = ac.createMediaElementSource(M.audio); M.gain = ac.createGain(); M.node.connect(M.gain); M.gain.connect(ac.destination); M.audio.volume = 1; }
    catch (e) { M.node = null; }
  }
  function duckMusic(t) {
    const M = FX.music; if (!M.gain) { if (M.audio) M.audio.volume = M.baseGain || 0.3; return; }
    const m = PLAN.fx.music || {};
    let target = M.baseGain != null ? M.baseGain : 0.3;
    // voz presente (analizador del video) -> ducking
    if (ND && ND.an) {
      ND.an.getByteTimeDomainData(ND.buf); let s = 0; for (let i = 0; i < ND.buf.length; i++) { const d = (ND.buf[i] - 128) / 128; s += d * d; }
      if (Math.sqrt(s / ND.buf.length) > 0.028) target *= Math.pow(10, (m.duck_db != null ? +m.duck_db : -9) / 20);
    }
    (m.stops || []).forEach(st => { if (t >= st.at && t < st.at + (+st.dur || 0)) target = 0; });
    (m.swells || []).forEach(sw => { if (t >= sw.start && t < sw.end) target *= Math.pow(10, (+sw.db || 0) / 20); });
    const inF = +(m.intro_fade || 0); if (inF > 0) target *= clamp(FX.finalTime(t) / inF, 0, 1);    // entrada suave
    M.gain.gain.value += (target - M.gain.gain.value) * 0.18;
  }
  FX.onCutsChanged = () => { if (isPro() && $("#musBody")) FX.renderMusicPanelSoon(); };

  // ------------------------------------------------------------------ vista previa en vivo (aproximada)
  let LB = { x: 0, y: 0, w: 540, h: 960, s: 0.5 };
  function layoutBox() {
    const W = CW, H = CH;
    let w, h, x, y;
    if (W / H >= 9 / 16) { h = H; w = H * 9 / 16; x = (W - w) / 2; y = 0; } else { w = W; h = W * 16 / 9; x = 0; y = (H - h) / 2; }
    LB = { x, y, w, h, s: w / 1080 };
    return LB;
  }
  const LOOK = {
    natural: "", warm_clean: "saturate(1.08) contrast(1.04) sepia(0.06)", cinematic: "contrast(1.1) saturate(0.88) brightness(0.97)",
    bright_pop: "saturate(1.28) brightness(1.05) contrast(1.05)", moody: "contrast(1.15) brightness(0.9) saturate(0.82)", bw_contrast: "grayscale(1) contrast(1.25)",
  };
  /** Transformacion de camara y filtro del video base en t (para draw()). */
  FX.videoLook = function (t) {
    const cam = C.cameraAt(items("effects"), t);
    const g = PLAN.fx.grade || {};
    let f = g.enabled === false ? "" : (LOOK[g.look] || "");
    items("effects").forEach(e => {
      if (e.enabled === false || !(t >= e.start && t < e.end)) return;
      const k = e.intensity == null ? 1 : +e.intensity;
      if (e.kind === "bw") f += ` grayscale(${k}) contrast(${1 + ((+e.contrast || 1.2) - 1) * k})`;
      else if (e.kind === "color_pop") f += ` saturate(${1 - 0.55 * k * (e.bg_desat != null ? +e.bg_desat : 1)}) brightness(${1 - 0.3 * k * (e.bg_dark != null ? +e.bg_dark : 0.25)})`;
      else if (e.kind === "bg_blur") f += ` blur(${(0.6 * k).toFixed(2)}px)`;
    });
    // recurso flotante activo: el orador se desenfoca y oscurece detras de la tarjeta (FloatCard.bgFilter)
    const FCm = window.FloatCard, mc = FCm ? activeRange("media", t).find(it => it.sid) : null;
    if (mc) { const bf = FCm.bgFilter(mc, FCm.cardAnim(t - mc.start, mc.end - mc.start, FCm.param(mc, "in_dur"), FCm.param(mc, "out_dur"), FCm.param(mc, "float")).bg, LB.w); if (bf) f += " " + bf; }
    // el render ancla los zooms en la cara de la toma: se escala alrededor de ese punto
    const fc = faceAt(t);
    return { z: cam.z, dx: cam.dx, dy: cam.dy, ax: fc ? clamp(fc[0], 0.15, 0.85) : 0.5, ay: fc ? clamp(fc[1], 0.15, 0.85) : 0.5, filter: f.trim() || "none" };
  };

  /** Posicion normalizada de un item; con x/y vacios usa `anchor` junto a la cara de la toma. */
  function faceAt(t) { const sh = (PLAN.shots || []).find(x => t >= x.start && t < x.end); return sh && Array.isArray(sh.face) ? sh.face : null; }
  function itemXY(it, t) {
    let x = it.x, y = it.y;
    if (x == null || y == null) {
      const f = faceAt(t != null ? t : (V.currentTime || 0));
      const xy = f && it.anchor ? C.anchorXY(it, f) : null;
      if (xy) return [clamp(xy[0], 0, 1), clamp(xy[1], 0, 1)];
      const fx0 = f ? f[0] : 0.5, fy0 = f ? f[1] : 0.35, fw = f ? f[2] : 0.2, fh = f ? f[3] : 0.12;
      const a = it.anchor || "face";
      // sin ancla a la cara: centrado (graphics.center_of usa XC), nunca del lado de la cara
      if (x == null && !it.anchor) x = C.XC;
      if (x == null) x = a === "face_left" ? fx0 - fw * 1.3 : a === "face_right" ? fx0 + fw * 1.3 : fx0;
      if (y == null) y = a === "face_top" ? fy0 - fh * 1.4 : fy0;
    }
    return [clamp(+x, 0, 1), clamp(+y, 0, 1)];
  }
  const activeRange = (track, t) => items(track).filter(it => it.enabled !== false && t >= it.start && t < it.end);
  function animState(it, lt, L, dflt) {
    const ain = it.anim_in || dflt || "pop", aout = it.anim_out || "fade";
    const TI = 0.28, TO = 0.22;
    let a = 1, sc = 1, dx = 0, dy = 0, reveal = 1;
    const pi = clamp(lt / TI, 0, 1), po = clamp((L - lt) / TO, 0, 1);
    if (lt < TI) {
      if (ain === "pop" || ain === "scale") { sc = 0.55 + 0.45 * C.ease("out_back", pi); a = clamp(pi * 2.5, 0, 1); }
      else if (ain === "scale_down") { sc = 1.35 - 0.35 * C.ease("out_cubic", pi); a = pi; }
      else if (ain === "rise" || ain === "slide_up") { dy = 0.05 * (1 - C.ease("out_cubic", pi)); a = pi; }
      else if (ain === "slide_down" || ain === "drop") { dy = -0.05 * (1 - C.ease("out_cubic", pi)); a = pi; }
      else if (ain === "slide_left") { dx = 0.2 * (1 - C.ease("out_cubic", pi)); a = pi; }
      else if (ain === "slide_right") { dx = -0.2 * (1 - C.ease("out_cubic", pi)); a = pi; }
      else if (ain === "fade" || ain === "draw" || ain === "mask" || ain === "flip") a = pi;
    }
    if (["type", "word_stagger", "letter_stagger"].includes(ain)) reveal = clamp(lt / Math.max(0.35, Math.min(1.2, L * 0.55)), 0, 1);
    if (lt > L - TO && aout !== "none") {
      if (aout === "pop_out") { sc *= 0.7 + 0.3 * po; a *= po; }
      else if (aout === "slide_up") { dy -= 0.04 * (1 - po); a *= po; }
      else if (aout === "slide_down") { dy += 0.04 * (1 - po); a *= po; }
      else a *= po;
    }
    return { a, sc, dx, dy, reveal };
  }
  const normW = C.normWord;
  function hexA(hex, a) { const h = String(hex || "#000").replace("#", ""); const v = h.length === 3 ? h.split("").map(x => x + x).join("") : h.slice(0, 6); const n = parseInt(v, 16) || 0; return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; }
  function lum(hex) { const h = String(hex || "#fff").replace("#", ""); const v = h.length === 3 ? h.split("").map(x => x + x).join("") : h.slice(0, 6); const n = parseInt(v, 16) || 0; return (0.299 * (n >> 16 & 255) + 0.587 * (n >> 8 & 255) + 0.114 * (n & 255)) / 255; }
  function rrect(c, x, y, w, h, r) { r = Math.min(r, w / 2, h / 2); c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath(); }

  /** Fuente de una palabra de titulo como el render: italica real si existe (text._italic) o
      oblicua sintetica; en serif_elegant las palabras de acento van en la italica. */
  const hasFont = n => FONTS.some(f => f.name === n);
  function titleWordFont(font, italic) {
    if (!italic) return { f: font, synth: false };
    const io = C.italicOf(font);
    return !io.synth && hasFont(io.font) ? { f: io.font, synth: false } : { f: font, synth: true };
  }
  /** Texto cinetico aproximado con la geometria del render donde importa (text._build_block): espacio
      entre palabras y separacion entre lineas del preset, ancho util (x, alineacion, `width`,
      deriva), envoltura balanceada de una linea que no cabe y reduccion de la que aun no cabe,
      acentos de serif_elegant en italica. y = centro del bloque. */
  function drawTextItem(c, it, lt, L, box) {
    const B = box || LB, W = B.w, s = B.s;
    const st = animState(it, lt, L, "pop");
    if (st.a <= 0.01) return null;
    const PR = C.TEXT_PRESETS[it.preset] || C.TEXT_PRESETS.title_pop;
    const spK = +PR.space || 1, gapK = PR.gap != null ? +PR.gap : 0.12;
    const base = (+it.size || 0.08) * W;
    let lines, plain = false;
    if (it.preset === "counter") {
      const p = clamp(lt / Math.max(0.3, L * 0.7), 0, 1), e = C.ease("out_cubic", p);
      const v = (+it.from || 0) + ((+it.to || 0) - (+it.from || 0)) * e;
      const dec = +it.decimals || 0;
      const num = v.toLocaleString("es-MX", { minimumFractionDigits: dec, maximumFractionDigits: dec });
      lines = [{ text: `${it.prefix || ""}${num}${it.suffix || ""}`, scale: 1, font: it.font }];
      if (it.text) lines.push({ text: String(it.text), scale: 0.28, font: it.font });
    } else if (Array.isArray(it.lines) && it.lines.length) lines = it.lines.map(l => (typeof l === "string" ? { text: l, scale: 1, font: it.font } : { text: String((l && l.text) || ""), scale: +(l && l.scale) || 1, font: (l && l.font) || it.font, color: l && l.color, italic: l && l.italic }));
    else { lines = String(it.text || "").split("\n").map(x => ({ text: x, scale: 1, font: it.font })); plain = true; }
    let kick = null;
    if (it.preset === "kicker") {
      const ks = C.kickerSplit(it, lines);
      if (ks.label) {
        const ksc = ks.only ? 1 : Math.max(+(it.kicker_scale != null ? it.kicker_scale : 0.36), 0.036 * W / Math.max(1, base));
        kick = { text: String(ks.label).toUpperCase(), scale: ksc, font: it.kicker_font || (ks.only ? it.font : null) || "Poppins-Bold", pill: true };
        lines = [kick].concat(ks.rest);
      }
    }
    const cs = it.case || PR.case;
    if (cs === "upper") lines.forEach(l => { if (!l.pill) l.text = l.text.toUpperCase(); });
    lines = lines.filter(l => String(l.text || "").trim());
    if (!lines.length) return null;
    const acc = new Set((it.accent_words || []).map(normW));
    const serif = it.preset === "serif_elegant";
    const color = it.color || "#FFFFFF", accent = it.accent || ACC();
    const accCol = serif && !it.accent ? color : accent;       // serif_elegant: el acento es la italica
    // x es el CENTRO del bloque con cualquier alineacion (text._place, simetria): `align` solo alinea
    // las lineas dentro del bloque; en cascade la escalera va dentro y el bloque entero se centra
    const align = it.align || PR.align || "center", casc = it.preset === "cascade";
    c.save();
    c.textBaseline = "alphabetic";
    // medidas a 1 px (se escalan): avance y tinta de cada palabra con su fuente
    const measureLine = (l, words) => {
      const ms = words.map(w => {
        const isAcc = !l.pill && acc.has(normW(w));
        const wf = titleWordFont(l.font, !!l.italic || (serif && isAcc));
        c.font = fontCss(wf.f, 100, wf.synth); const r = c.measureText(w);
        return { f: wf.f, synth: wf.synth, isAcc, adv: r.width / 100, asc: (r.actualBoundingBoxAscent != null ? r.actualBoundingBoxAscent : 72) / 100, desc: (r.actualBoundingBoxDescent || 0) / 100 };
      });
      c.font = fontCss(l.font, 100); const sp = c.measureText(" ").width / 100 * (l.pill ? 1 : spK);
      return { ms, sp };
    };
    const [ix, iy] = itemXY(it);
    const maxw = C.textUsable(it.x != null ? +it.x : ix, align, it.width, PR.drift) * W;
    let meas = lines.map(l => { const words = l.text.split(/\s+/).filter(Boolean); return Object.assign({ l, words }, measureLine(l, words)); });
    const widthAt = (m, px) => (m.ms.reduce((q, x) => q + x.adv, 0) + m.sp * Math.max(0, m.ms.length - 1)) * px;
    // una sola linea logica que no cabe: se envuelve balanceada (text._fit con size explicito)
    const autoWrap = !["stack", "cascade", "kicker", "counter"].includes(it.preset);
    if (plain && !kick && (autoWrap || meas.length === 1) && meas.length === 1 && meas[0].words.length > 1 && widthAt(meas[0], base) > maxw) {
      const m0 = meas[0];
      for (let k = 2; k <= m0.words.length; k++) {
        const stt = C.balancedSplit(m0.ms.map(x => x.adv), m0.sp, k, m0.words);
        const parts = stt.map((a, i) => m0.words.slice(a, i + 1 < stt.length ? stt[i + 1] : m0.words.length));
        if (parts.some(ws => ws.every(w => C.FUNC_END.has(normW(w))))) break;
        meas = parts.map(ws => Object.assign({ l: Object.assign({}, m0.l, { text: ws.join(" ") }), words: ws }, measureLine(m0.l, ws)));
        if (Math.max(...meas.map(m => widthAt(m, base))) <= maxw) break;
      }
    }
    let totalH = 0, maxW = 0;
    meas.forEach((m, i) => {
      let px = base * m.l.scale;
      const w0 = widthAt(m, px);
      if (w0 > maxw && !m.l.pill) px *= maxw / w0 * 0.995;       // lo que aun no cabe se reduce
      m.px = px; m.ws = m.ms.map(x => x.adv * px); m.spx = m.sp * px; m.w = widthAt(m, px);
      m.asc = Math.max(...m.ms.map(x => x.asc)) * px; m.desc = Math.max(...m.ms.map(x => x.desc)) * px;
      m.h = m.asc + m.desc;
      totalH += m.h + (i ? base * gapK : 0); maxW = Math.max(maxW, m.l.pill ? m.w + px * 1.6 : m.w);
    });
    // escalera de cascade (text._cascade_offsets): cada linea entra cascade_step mas a la derecha
    // sin pasar el ancho util; el bloque mide lo que ocupa la escalera completa
    const offs = meas.map(() => 0);
    if (casc && meas.length > 1) {
      let stp = 0.13 * W;
      meas.forEach((m, i) => { if (i) stp = Math.min(stp, Math.max(0, (maxw - m.w) / i)); });
      meas.forEach((m, i) => { offs[i] = i * stp; });
      maxW = Math.max(maxW, ...meas.map((m, i) => offs[i] + m.w));
    }
    const cx = B.x + (ix + st.dx) * W, cy = B.y + (iy + st.dy) * B.h;
    c.globalAlpha *= st.a;
    c.translate(cx, cy); c.scale(st.sc, st.sc); if (it.rotation) c.rotate(+it.rotation * Math.PI / 180);
    let y = -totalH / 2, widx = 0;
    // aparicion por palabra como el render: alineada a la voz (`_words`) o escalonada
    const allW = []; meas.forEach((m, li) => m.words.forEach(w => allW.push({ w, li })));
    let wtimes = null;
    if (it.word_sync === false && allW.length) wtimes = allW.map(() => 0);     // entra entero al inicio
    else if (it.preset !== "counter" && allW.length) {
      let spoken = null;
      if (it.start != null && !box && typeof flatWords !== "undefined") {
        spoken = flatWords.filter(w => w.start >= +it.start - 0.05 && w.start < +it.end).map(w => ({ norm: C.normWord(w.text), start: w.start - it.start }));
      }
      const step = ["pop", "scale_down", "fade", "mask"].includes(it.anim_in) && !(spoken && spoken.length) && ["title_pop", "behind_giant"].includes(it.preset) ? 0.08 : 0.12;
      wtimes = C.unitTimes(allW.map(x => C.normWord(x.w)), spoken, L, step, allW.map(x => x.li));
    }
    const stroke = (+it.stroke || 0) * s * 2, sh = it.shadow != null ? +it.shadow : 0.6;
    const bb = { x: cx - maxW / 2, y: cy - totalH / 2, w: maxW, h: totalH };
    if (it.box && it.preset !== "highlight_marker" && it.preset !== "kicker") {
      c.fillStyle = it.box; rrect(c, -maxW / 2 - base * 0.25, y - base * 0.18, maxW + base * 0.5, totalH + base * 0.36, base * 0.18); c.fill();
    }
    meas.forEach((m, li) => {
      if (li) y += base * gapK;
      // lineas alineadas DENTRO del bloque centrado en cx (text._align_lines)
      const lx0 = casc ? -maxW / 2 + offs[li] : align === "left" ? -maxW / 2 : align === "right" ? maxW / 2 - m.w : -m.w / 2;
      const by = y + m.asc;
      if (m.l.pill) {
        c.font = fontCss(m.l.font, m.px);
        const pc = it.box || accent; c.fillStyle = pc; rrect(c, lx0 - m.px * 0.8, y - m.px * 0.3, m.w + m.px * 1.6, m.h + m.px * 0.6, (m.h + m.px * 0.6) / 2); c.fill();
      }
      let x = lx0;
      m.words.forEach((w, wi) => {
        const wt = wtimes ? wtimes[widx++] : 0;
        if (lt < wt) { x += m.ws[wi] + m.spx; return; }
        const mw = m.ms[wi];
        c.font = fontCss(mw.f, m.px, mw.synth);
        let txt = w;
        if (["type", "letter_stagger"].includes(it.anim_in)) { const nch = Math.floor((lt - wt) / 0.035) + 1; if (nch < w.length) txt = w.slice(0, nch); }
        c.save(); c.globalAlpha *= clamp((lt - wt) / 0.12, 0, 1);
        const isAcc = mw.isAcc;
        let fill = m.l.color || (isAcc ? accCol : color);
        if (m.l.pill) fill = lum(it.box || accent) > 0.6 ? "#111111" : "#FFFFFF";
        if (it.preset === "highlight_marker" && (isAcc || !acc.size)) {
          c.fillStyle = hexA(it.box || accent, 0.92); c.fillRect(x - m.px * 0.08, y + m.h * 0.12, m.ws[wi] + m.px * 0.16, m.h * 0.8);
          fill = lum(it.box || accent) > 0.6 ? "#111111" : "#FFFFFF";
        }
        if (sh > 0) { c.shadowColor = `rgba(0,0,0,${0.55 * sh})`; c.shadowBlur = 14 * s * sh; c.shadowOffsetY = 3 * s * sh; }
        if (stroke > 0) { c.lineJoin = "round"; c.lineWidth = stroke; c.strokeStyle = it.stroke_color || (lum(fill) > 0.5 ? "rgba(0,0,0,.85)" : "rgba(255,255,255,.9)"); c.strokeText(txt, x, by); }
        c.fillStyle = fill; c.fillText(txt, x, by);
        c.restore();
        x += m.ws[wi] + m.spx;
      });
      y += m.h;
    });
    c.restore();
    return bb;
  }

  // garabatos simplificados (forma reconocible, no el trazo exacto del render)
  function doodlePath(c, name, R) {
    c.beginPath();
    const star = (n, ri) => { for (let i = 0; i < n * 2; i++) { const a = -Math.PI / 2 + i * Math.PI / n, r = i % 2 ? R * ri : R; c.lineTo(Math.cos(a) * r, Math.sin(a) * r); } c.closePath(); };
    switch (name) {
      case "sparkle": case "star4": star(4, 0.3); break;
      case "star5": star(5, 0.45); break;
      case "burst": case "rays": for (let i = 0; i < 10; i++) { const a = i * Math.PI / 5; c.moveTo(Math.cos(a) * R * 0.45, Math.sin(a) * R * 0.45); c.lineTo(Math.cos(a) * R, Math.sin(a) * R); } break;
      case "arrow_curve": c.moveTo(-R, R * 0.4); c.quadraticCurveTo(-R * 0.2, -R, R, -R * 0.2); c.moveTo(R * 0.55, -R * 0.55); c.lineTo(R, -R * 0.2); c.lineTo(R * 0.6, R * 0.15); break;
      case "arrow_straight": c.moveTo(-R, 0); c.lineTo(R, 0); c.moveTo(R * 0.55, -R * 0.4); c.lineTo(R, 0); c.lineTo(R * 0.55, R * 0.4); break;
      case "arrow_down": c.moveTo(0, -R); c.lineTo(0, R); c.moveTo(-R * 0.4, R * 0.55); c.lineTo(0, R); c.lineTo(R * 0.4, R * 0.55); break;
      case "circle_scribble": c.ellipse(0, 0, R, R * 0.62, -0.08, 0, Math.PI * 2); break;
      case "underline": c.moveTo(-R, 0); c.quadraticCurveTo(0, R * 0.18, R, -R * 0.05); break;
      case "squiggle": for (let i = 0; i <= 24; i++) { const x = -R + i * R / 12; c.lineTo(x, Math.sin(i * 0.9) * R * 0.18); } break;
      case "dotted_path": for (let i = 0; i < 8; i++) { const x = -R + i * R / 3.5; c.moveTo(x + 2, 0); c.arc(x, 0, 2, 0, Math.PI * 2); } break;
      case "heart": c.moveTo(0, R * 0.8); c.bezierCurveTo(-R * 1.2, 0, -R * 0.6, -R, 0, -R * 0.35); c.bezierCurveTo(R * 0.6, -R, R * 1.2, 0, 0, R * 0.8); break;
      case "check": c.moveTo(-R * 0.8, 0); c.lineTo(-R * 0.2, R * 0.6); c.lineTo(R * 0.9, -R * 0.7); break;
      case "cross": c.moveTo(-R * 0.7, -R * 0.7); c.lineTo(R * 0.7, R * 0.7); c.moveTo(R * 0.7, -R * 0.7); c.lineTo(-R * 0.7, R * 0.7); break;
      case "exclaim": c.moveTo(0, -R); c.lineTo(0, R * 0.35); c.moveTo(0, R * 0.75); c.arc(0, R * 0.8, R * 0.04, 0, Math.PI * 2); break;
      case "question": c.moveTo(-R * 0.45, -R * 0.45); c.bezierCurveTo(-R * 0.45, -R, R * 0.5, -R, R * 0.5, -R * 0.4); c.bezierCurveTo(R * 0.5, 0, 0, 0, 0, R * 0.35); c.moveTo(0, R * 0.8); c.arc(0, R * 0.8, R * 0.04, 0, Math.PI * 2); break;
      case "swirl": case "loop": for (let i = 0; i < 60; i++) { const a = i * 0.25, r = R * i / 60; c.lineTo(Math.cos(a) * r, Math.sin(a) * r); } break;
      case "lightning": c.moveTo(R * 0.2, -R); c.lineTo(-R * 0.4, R * 0.1); c.lineTo(R * 0.1, R * 0.1); c.lineTo(-R * 0.2, R); break;
      default: star(4, 0.3);
    }
  }

  /** CTA con la geometria de graphics.draw_cta (size acotado por variante, alto de la pastilla y
      tipografia proporcionales al ancho). Devuelve la caja en coordenadas del canvas. */
  // ancho maximo de todo lo centrado = C.SYM_W (0.76): centrado en 0.5 nunca toca el borde de SAFE
  const CTA_SIZE = { follow: 0.72, link_below: 0.56 }, SYM_W = C.SYM_W;
  function drawCta(c, it, cx, cy, B, lt) {
    const v = it.variant || "follow", W = B.w;
    const rng = v === "follow" ? [0.56, SYM_W] : v === "link_below" ? [0.4, 0.74] : [0.45, SYM_W];
    const S = clamp(+(it.size != null ? it.size : (CTA_SIZE[v] || 0.62)), rng[0], rng[1]) * W;
    const ent = PLAN.entities || {}, acc = it.color && it.color !== "#FFFFFF" ? it.color : ACC();
    const bold = "Poppins-ExtraBold";
    c.shadowColor = "rgba(0,0,0,.35)"; c.shadowBlur = 14 * B.s; c.shadowOffsetY = 4 * B.s;
    c.textBaseline = "middle"; c.textAlign = "left";
    const wfix = it.width ? clamp(+it.width, 0, SYM_W) * W : 0;        // ancho comun de la pila del cierre
    const fitTo = (fname, txt, px, room, minPx) => { let f = px; c.font = fontCss(fname, f); const w0 = c.measureText(txt).width; if (w0 > room) { f = Math.max(minPx || px * 0.4, px * room / w0); c.font = fontCss(fname, f); } return f; };
    if (v === "link_below") {
      // graphics._link_below_layout: con `display` (enlace corto) la pastilla lleva dos lineas
      const label = String(it.label || ent.link_label || "Agenda aquí"), disp = String(it.display || "").trim();
      const on = lum(acc) > 0.55 ? "#141418" : "#FFFFFF";
      const icon = it.icon || (it.platform && PLATS[it.platform] && PLATS[it.platform].icon) || "calendar-event";
      // hueco del icono (graphics.link_slot): foto redonda del lugar (avatar image + media), si no el
      // logo del sitio en su insignia, si no el icono; foto y logo concentricos con la punta
      const ph = (it.avatar == null || it.avatar === "image") && it.media && it.media.sid ? mediaFrame(it, it.media, 0, false) : null;
      const lg = it.logo && it.logo.sid ? mediaFrame(it, it.logo, 0, false) : null;
      const kind = ph ? "photo" : lg ? "logo" : "icon";
      let h, bw, fb, fd = 0, tw, sl;
      if (disp) {
        h = S * 0.25; sl = C.linkSlot(kind, h, true);
        const side = sl.inset + sl.d + sl.gap + sl.padR;
        const room = kind === "icon" ? Math.max(S * 0.8, SYM_W * W - side) : SYM_W * W - side;
        fd = fitTo("Poppins-Medium", disp, h * 0.21, room, h * 0.13); const wd = c.measureText(disp).width;
        fb = fitTo(bold, label, h * 0.3, room, h * 0.2); const wl = c.measureText(label).width;
        tw = Math.max(wl, wd); bw = Math.min(SYM_W * W, Math.max(tw + side, wfix));
      } else {
        h = S * 0.2; sl = C.linkSlot(kind, h, false);
        const side = sl.inset + sl.d + sl.gap + sl.padR;
        fb = fitTo(bold, label, h * 0.38, kind === "icon" ? S * 0.9 : Math.min(S * 0.9, SYM_W * W - side), h * 0.26); tw = c.measureText(label).width;
        bw = Math.min(SYM_W * W, Math.max(tw + side, wfix));
      }
      c.fillStyle = acc; rrect(c, -bw / 2, -h / 2, bw, h, h / 2); c.fill(); c.shadowBlur = 0; c.shadowOffsetY = 0;
      const d = sl.d, scx = -(sl.inset + d + sl.gap + tw + sl.padR) / 2 + sl.inset + d / 2, tx = scx + d / 2 + sl.gap;
      if (kind === "photo") {
        const ring = Math.max(1.5, d * 0.05), inner = d - 2 * ring;
        c.fillStyle = "#FFFFFF"; c.beginPath(); c.arc(scx, 0, d / 2, 0, Math.PI * 2); c.fill();
        c.save(); c.beginPath(); c.arc(scx, 0, inner / 2, 0, Math.PI * 2); c.clip(); drawFit(c, ph, scx - inner / 2, -inner / 2, inner, inner, "cover", it.focus, 1); c.restore();
        if (lg) {   // logo del sitio en la esquina inferior derecha de la foto (aro blanco)
          const bd = d * C.LINK_BADGE, bx = scx + d * 0.36, by = d * 0.33;
          c.fillStyle = "#FFFFFF"; c.beginPath(); c.arc(bx, by, bd / 2 + Math.max(1.5, h * 0.028), 0, Math.PI * 2); c.fill();
          drawLogoBadge(c, lg, bx, by, bd, "circle");
        }
      } else if (kind === "logo") drawLogoBadge(c, lg, scx, 0, d, "squircle");
      else { const im = iconImage(icon, on); if (im) c.drawImage(im, scx - d / 2, -d / 2, d, d); }
      c.fillStyle = on; c.textBaseline = "alphabetic";
      if (disp) {
        c.font = fontCss(bold, fb); c.fillText(label, tx, -h * 0.03);
        c.globalAlpha *= 0.78 * clamp((lt - 0.2) / 0.35, 0, 1); c.font = fontCss("Poppins-Medium", fd); c.fillText(disp, tx, h * 0.27);
        c.globalAlpha /= Math.max(1e-3, 0.78 * clamp((lt - 0.2) / 0.35, 0, 1));
      } else { c.font = fontCss(bold, fb); c.textBaseline = "middle"; c.fillText(label, tx, fb * 0.06); }
      const asc = clamp(it.arrow_scale != null ? +it.arrow_scale : 1, 0, 1);
      if (asc >= 0.35 && lt > 0.25) {
        c.save(); c.translate(0, h / 2 + h * 0.3 + h * 0.85 * asc); c.strokeStyle = it.arrow_color || "#FFFFFF";
        c.lineWidth = 17 * B.s; c.lineCap = "round"; c.lineJoin = "round"; doodlePath(c, "arrow_down", h * 0.85 * asc); c.stroke(); c.restore();
      }
      return { x: cx - bw / 2, y: cy - h / 2, w: bw, h: h * (asc >= 0.35 ? 2.9 : 1) };
    }
    if (v === "follow") {
      // graphics._follow_layout: la pastilla crece para el nombre y el @usuario (hasta SAFE o
      // `width`); el @usuario nunca se corta; avatar con iniciales o foto e insignia de la red
      const h = S * 0.17, d = h * 0.74;
      const title = String(it.title || "").trim(), handle = String(it.handle || "").trim();
      const sub = title ? (handle || String(it.subtitle || "").trim()) : String(it.subtitle || "");
      const name0 = title || String(it.handle || ent.handle || ent.name || it.label || "Sígueme para más");
      const btn = String(it.button || "Seguir");
      const [kb, pb] = title ? [0.24, 0.46] : [0.27, 0.7];
      c.font = fontCss(bold, h * kb); const bwb = c.measureText(btn).width + h * pb;
      const lead = h * 0.13 + d + h * 0.22, trail = h * 0.35;
      c.font = fontCss(bold, h * 0.31); let need = c.measureText(name0).width;
      if (sub) { c.font = fontCss("Poppins-SemiBold", h * 0.22); need = Math.max(need, c.measureText(sub).width); }
      const Wd = Math.min(SYM_W * W, title ? Math.max(S, wfix, lead + need + trail + bwb) : Math.max(S, wfix));
      const maxw = Wd - lead - trail - bwb;
      // nombre largo: variantes mas cortas (sin el segundo apellido) antes de achicar
      const parts = name0.split(/\s+/);
      const variants = [name0]; for (let k = parts.length - 1; k >= 2; k--) variants.push(parts.slice(0, k).join(" "));
      let name = variants[variants.length - 1], fn = h * 0.19;
      for (const nm of variants) { c.font = fontCss(bold, h * 0.31); const w0 = c.measureText(nm).width; const f = w0 > maxw ? h * 0.31 * maxw / w0 : h * 0.31; if (f >= h * 0.215) { name = nm; fn = f; break; } }
      const fs = sub ? fitTo("Poppins-SemiBold", sub, h * 0.22, maxw, h * 0.104) : 0;
      c.fillStyle = "#FFFFFF"; rrect(c, -Wd / 2, -h / 2, Wd, h, h / 2); c.fill(); c.shadowBlur = 0; c.shadowOffsetY = 0;
      const ax = -Wd / 2 + h * 0.13 + d / 2;
      const avIm = it.media && it.media.sid && (it.avatar === "image" || !it.avatar) ? mediaFrame(it, it.media, 0, false) : null;
      c.save(); c.beginPath(); c.arc(ax, 0, d / 2, 0, Math.PI * 2); c.fillStyle = acc; c.fill();
      if (avIm) { c.clip(); drawFit(c, avIm, ax - d / 2, -d / 2, d, d, "cover", null, 1); }
      else {
        const ini = String(it.initials || name0.replace(/^@/, "").charAt(0)).toUpperCase().slice(0, 2);
        c.fillStyle = lum(acc) > 0.55 ? "#141418" : "#FFFFFF"; c.font = fontCss(bold, d * (ini.length > 1 ? 0.4 : 0.46)); c.textAlign = "center"; c.textBaseline = "middle";
        c.fillText(ini, ax, d * 0.03); c.textAlign = "left";
      }
      c.restore();
      const pl = title && it.platform && handle ? PLATS[it.platform] : null;
      const lgf = title && it.logo && it.logo.sid ? mediaFrame(it, it.logo, 0, false) : null;
      if (pl || lgf) {   // insignia de la red (o el logo del sitio) en la esquina del avatar (0.44 del diametro)
        const bd = d * C.FOLLOW_BADGE, bx = ax + d * 0.36, by = d * 0.33;
        c.fillStyle = "#FFFFFF"; c.beginPath(); c.arc(bx, by, bd / 2 + Math.max(1.5 * B.s, h * 0.028), 0, Math.PI * 2); c.fill();
        if (lgf) drawLogoBadge(c, lgf, bx, by, bd, "circle");
        else {
          c.fillStyle = pl.color || "#1E2330"; c.beginPath(); c.arc(bx, by, bd / 2, 0, Math.PI * 2); c.fill();
          const im = iconImage(pl.icon, lum(pl.color || "#000") > 0.6 ? "#141418" : "#FFFFFF"); if (im) c.drawImage(im, bx - bd * 0.32, by - bd * 0.32, bd * 0.64, bd * 0.64);
        }
      }
      const bcol = it.button_color || (it.color && lum(it.color) <= 0.85 ? it.color : "#FF3B5C");
      c.fillStyle = bcol; rrect(c, Wd / 2 - h * 0.15 - bwb, -h * 0.3, bwb, h * 0.6, h * 0.3); c.fill();
      c.fillStyle = lum(bcol) > 0.6 ? "#141418" : "#FFFFFF"; c.font = fontCss(bold, h * kb); c.textBaseline = "middle"; c.textAlign = "center";
      c.fillText(btn, Wd / 2 - h * 0.15 - bwb / 2, h * 0.01); c.textAlign = "left";
      c.fillStyle = "#141418"; c.font = fontCss(bold, fn); c.fillText(name, -Wd / 2 + lead, sub ? -h * 0.12 : 0);
      if (sub) { c.fillStyle = "#6C6D7A"; c.font = fontCss("Poppins-SemiBold", fs); c.fillText(sub, -Wd / 2 + lead, h * 0.2); }
      return { x: cx - Wd / 2, y: cy - h / 2, w: Wd, h };
    }
    const h = S * 0.17, ib = h * 0.72;
    const label = String(it.label || { comment: "Comenta abajo", save: "Guárdalo para después", share: "Compártelo con alguien" }[v] || "");
    let fs = h * 0.34; c.font = fontCss(bold, fs); let tw = c.measureText(label).width;
    if (tw > S * 0.8) { fs *= S * 0.8 / tw; c.font = fontCss(bold, fs); tw = c.measureText(label).width; }
    const w = tw + ib + h * 0.95;
    c.fillStyle = "#FFFFFF"; rrect(c, -w / 2, -h / 2, w, h, h / 2); c.fill(); c.shadowBlur = 0;
    const bx = -w / 2 + h * 0.14 + ib / 2;
    c.fillStyle = acc; c.beginPath(); c.arc(bx, 0, ib / 2, 0, Math.PI * 2); c.fill();
    const im = iconImage({ comment: "message-circle", save: "bookmark", share: "send" }[v] || "star", "#141418"); if (im) c.drawImage(im, bx - ib * 0.28, -ib * 0.28, ib * 0.56, ib * 0.56);
    c.fillStyle = "#141418"; c.fillText(label, bx + ib / 2 + h * 0.2, fs * 0.06);
    return { x: cx - w / 2, y: cy - h / 2, w, h };
  }

  function drawGraphic(c, it, lt, L, box, t) {
    const B = box || LB, W = B.w, s = B.s;
    const st = animState(it, lt, L, "pop");
    if (st.a <= 0.01) return null;
    const [ix, iy] = itemXY(it, t);
    const cx = B.x + (ix + st.dx) * W, cy = B.y + (iy + st.dy) * B.h;
    const size = (+it.size || 0.18) * W, col = it.color || "#FFFFFF";
    c.save(); c.globalAlpha *= st.a;
    let bb = { x: cx - size / 2, y: cy - size / 2, w: size, h: size };
    const k = it.kind;
    if (k === "progress") {
      const tf = FX.finalTime(t), T = tlObj().finalDuration || dur();
      const th = (+it.thickness || 8) * s;
      c.fillStyle = "rgba(255,255,255,.18)"; c.fillRect(B.x, B.y, W, th);
      c.fillStyle = col; c.fillRect(B.x, B.y, W * clamp(tf / T, 0, 1), th);
      c.restore(); return { x: B.x, y: B.y, w: W, h: th + 4 };
    }
    c.translate(cx, cy); c.scale(st.sc, st.sc); if (it.rotation) c.rotate(+it.rotation * Math.PI / 180);
    c.shadowColor = "rgba(0,0,0,.35)"; c.shadowBlur = 10 * s; c.shadowOffsetY = 3 * s;
    if (k === "doodle") {
      c.lineWidth = (+it.stroke || 12) * s; c.lineCap = "round"; c.lineJoin = "round"; c.strokeStyle = col;
      const draw = it.draw !== false && (it.anim_in === "draw" || !it.anim_in);
      if (draw && lt < 0.45) { c.setLineDash([size * 4 * clamp(lt / 0.45, 0, 1), size * 4]); }
      // contorno oscuro bajo un trazo claro (graphics.doodle border_color): se lee sobre fondos claros
      const bc = it.border_color || (it.border ? "#141418" : null);
      if (bc) { c.save(); c.strokeStyle = bc; c.lineWidth = (+it.stroke || 12) * s * 1.7; doodlePath(c, it.name, size / 2); c.stroke(); c.restore(); }
      doodlePath(c, it.name, size / 2); c.stroke();
      if (["sparkle", "star4", "star5", "heart"].includes(it.name) && !(draw && lt < 0.45)) { c.fillStyle = col; c.fill(); }
    } else if (k === "icon") {
      // graphics.draw_icon: badge de diametro `size` e icono de 0.56*size (sin badge: size)
      const gm = C.iconGeom(size, it.badge);
      let icol = it.color || "#FFFFFF";
      if (gm.badge) {
        const bcol = it.badge_color || ACC();
        c.fillStyle = bcol;
        if (it.badge === "circle") { c.beginPath(); c.arc(0, 0, gm.badge / 2, 0, Math.PI * 2); c.fill(); } else { rrect(c, -gm.badge / 2, -gm.badge / 2, gm.badge, gm.badge, gm.badge * 0.3); c.fill(); }
        icol = it.color || (lum(bcol) > 0.55 ? "#15151A" : "#FFFFFF");
        if (lum(icol) > 0.8 && lum(bcol) > 0.7) icol = "#15151A";
      }
      c.shadowBlur = gm.badge ? 0 : c.shadowBlur;
      const im = iconImage(it.name, icol), idx = (ICON_DX[it.name] || 0) * gm.icon / 24;
      if (im) c.drawImage(im, -gm.icon / 2 - idx, -gm.icon / 2, gm.icon, gm.icon);
      else { c.strokeStyle = icol; c.lineWidth = 2 * s * 3; c.beginPath(); c.arc(0, 0, gm.icon * 0.32, 0, Math.PI * 2); c.stroke(); }
    } else if (k === "card") {
      const w = clamp(+it.size || 0.74, 0.5, SYM_W) * W, h = w * (it.variant === "checklist" ? 0.62 : 0.4);
      const th = it.theme || "light";
      const bg = th === "dark" ? "#141414" : th === "brand" ? (it.color && it.color !== "#FFFFFF" ? it.color : ACC()) : "#FFFFFF";
      const fg = lum(bg) > 0.55 ? "#111111" : "#FFFFFF";
      c.fillStyle = bg; rrect(c, -w / 2, -h / 2, w, h, w * 0.06); c.fill(); c.shadowBlur = 0; c.shadowOffsetY = 0;   // texto sin sombra (como el render)
      const pad = w * 0.07; let ty = -h / 2 + pad;
      c.textBaseline = "top"; c.fillStyle = fg;
      const ic = iconImage(it.icon, fg); const isz = h * 0.28;
      let tx = -w / 2 + pad;
      // `logo` (logo de la pagina que menciona la tarjeta): reemplaza al icono en su insignia redondeada
      // con la geometria de graphics._row_header: insignia de 15u (x1.12 en horario) a 6.4u del borde
      const lgc = it.logo && it.logo.sid ? mediaFrame(it, it.logo, 0, false) : null;
      if (lgc) {
        const u = w / 100, bs = 15 * u * (it.variant === "schedule" ? 1.12 : 1), p0 = 6.4 * u;
        drawLogoBadge(c, lgc, -w / 2 + p0 + bs / 2, -h / 2 + p0 + bs / 2, bs, "squircle"); c.fillStyle = fg;
        tx = -w / 2 + p0 + bs + 4.2 * u; ty = -h / 2 + p0 + bs * 0.12;
      }
      else if (it.icon) { if (ic) c.drawImage(ic, tx, ty, isz, isz); tx += isz + pad * 0.6; }
      c.font = fontCss("Poppins-Bold", w * 0.065); c.fillText(String(it.title || "").slice(0, 40), tx, ty);
      c.globalAlpha *= 0.75; c.font = fontCss("Poppins-Medium", w * 0.045); c.fillText(String(it.subtitle || "").slice(0, 50), tx, ty + w * 0.08); c.globalAlpha /= 0.75;
      if (it.value) { c.font = fontCss("Poppins-ExtraBold", w * 0.1); c.fillText(String(it.value), -w / 2 + pad, h / 2 - pad - w * 0.11); }
      (it.items || []).slice(0, 4).forEach((x, i) => { c.font = fontCss("Poppins-Medium", w * 0.045); c.fillText("- " + x, -w / 2 + pad, ty + w * (0.18 + i * 0.065)); });
      bb = { x: cx - w / 2, y: cy - h / 2, w, h };
    } else if (k === "lower_third") {
      // graphics.draw_lower_third. Centrado (align "center", el default): x = CENTRO del panel, que
      // se despliega desde el centro, titulo y rol centrados que entran subiendo y el acento es una
      // barra en el borde inferior que crece del centro; todo simetrico en cada cuadro. Legado (sin
      // align o "left"): x = borde izquierdo, barra vertical a la izquierda y el panel se despliega a
      // la derecha. El nombre va en negritas y el rol en VERSALITAS con tracking
      c.restore(); c.save(); c.globalAlpha *= st.a;        // pivote propio (centro o borde izquierdo)
      const fs0 = clamp(+it.size || 0.056, 0.03, 0.09) * W, light = it.theme === "light";
      let acc2 = it.color || it.accent || ACC(); if (light && lum(acc2) > 0.8) acc2 = "#FF3B5C";
      const padx = 28 * s, pady = 20 * s, barw = 10 * s;
      const title = String(it.title || (PLAN.entities || {}).name || ""), sub = String(it.subtitle || (PLAN.entities || {}).role || "").toUpperCase();
      const avail = C.lowerThirdX(it, W, 0, barw, padx, 120 * s).avail;
      // lo que no cabe se reduce (fit_font hasta 0.55 del titulo; el rol hasta 0.7)
      let fs = fs0; c.font = fontCss("Poppins-Bold", fs); let wt = c.measureText(title).width;
      if (wt > avail) { fs = Math.max(fs0 * 0.55, fs * avail / wt); c.font = fontCss("Poppins-Bold", fs); wt = Math.min(avail, c.measureText(title).width); }
      const subW = f => { c.font = fontCss("Poppins-SemiBold", f); return sub ? c.measureText(sub).width + f * 0.14 * Math.max(0, sub.length - 1) : 0; };
      let fsub = fs0 * 0.5, ws = subW(fsub);
      if (ws > avail) { fsub = Math.max(fs0 * 0.35, fsub * avail / ws); ws = Math.min(avail, subW(fsub)); }
      const trk = fsub * 0.14;
      const tht = fs * 1.05, ths = sub ? fsub * 1.25 : 0, gap = sub ? 8 * s : 0;
      const ph = pady * 2 + tht + gap + ths, pw = Math.max(wt, ws) + padx * 2;
      const rv = C.lowerThirdReveal(it, lt, L), g = C.lowerThirdX(it, W, pw, barw, padx, 120 * s);
      const bgc = light ? "rgba(255,255,255,.96)" : "rgba(15,15,20,.78)", fg = light ? "#15151B" : "#FFFFFF";
      const px0 = B.x + (g.centered ? g.cx : g.x0) + st.dx * W;
      c.translate(px0, cy); c.scale(st.sc, st.sc); if (it.rotation) c.rotate(+it.rotation * Math.PI / 180);
      c.shadowBlur = 0; c.shadowOffsetY = 0; c.textBaseline = "alphabetic";
      const subText = (x, y) => {
        c.fillStyle = acc2; c.font = fontCss("Poppins-SemiBold", fsub);
        // letterSpacing tambien suma despues de la ultima letra: centrado se compensa medio tracking
        if ("letterSpacing" in c) { c.letterSpacing = `${trk.toFixed(1)}px`; c.fillText(sub, x + (c.textAlign === "center" ? trk / 2 : 0), y); c.letterSpacing = "0px"; }
        else c.fillText(sub, x, y);
      };
      if (rv.bar <= 0.002) bb = null;
      else if (g.centered) {
        const rad = 14 * s, barh = 8 * s;
        if (rv.pan > 0.002) {
          const vw = pw * rv.pan;
          c.fillStyle = bgc; rrect(c, -vw / 2, -ph / 2, vw, ph, rad); c.fill();
          c.save(); c.beginPath(); c.rect(-vw / 2, -ph / 2, vw, ph); c.clip(); c.textAlign = "center";
          c.save(); c.globalAlpha *= rv.te; c.fillStyle = fg; c.font = fontCss("Poppins-Bold", fs);
          c.fillText(title, 0, -ph / 2 + pady + fs * 0.86 + (1 - rv.te) * 14 * s); c.restore();
          if (sub) { c.save(); c.globalAlpha *= rv.se; subText(0, -ph / 2 + pady + tht + gap + fsub * 0.95 + (1 - rv.se) * 14 * s); c.restore(); }
          c.restore();
        }
        const bw = pw * rv.bar;          // acento: borde inferior, del centro hacia afuera
        if (bw > 0.5) { c.save(); rrect(c, -pw / 2, -ph / 2, pw, ph, rad); c.clip(); c.fillStyle = acc2; rrect(c, -bw / 2, ph / 2 - barh, bw, barh, barh / 2); c.fill(); c.restore(); }
        bb = { x: px0 - pw / 2, y: cy - ph / 2, w: pw, h: ph };
      } else {
        if (rv.pan > 0.002) {
          c.fillStyle = bgc; c.fillRect(barw, -ph / 2, pw * rv.pan, ph);
          c.save(); c.beginPath(); c.rect(barw, -ph / 2, pw * rv.pan, ph); c.clip(); c.textAlign = "left";
          c.save(); c.globalAlpha *= rv.te; c.fillStyle = fg; c.font = fontCss("Poppins-Bold", fs);
          c.fillText(title, barw + padx - (1 - rv.te) * 40 * s, -ph / 2 + pady + fs * 0.86); c.restore();
          if (sub) { c.save(); c.globalAlpha *= rv.se; subText(barw + padx, -ph / 2 + pady + tht + gap + fsub * 0.95 + (1 - rv.se) * 14 * s); c.restore(); }
          c.restore();
        }
        c.fillStyle = acc2; rrect(c, 0, -ph * rv.bar / 2, barw, ph * rv.bar, barw / 2); c.fill();
        bb = { x: px0, y: cy - ph / 2, w: barw + pw, h: ph };
      }
      c.textAlign = "left";
    } else if (k === "cta") {
      bb = drawCta(c, it, cx, cy, B, lt);
    } else if (k === "sticker_text") {
      const txt = it.text || "";
      const fs = (+it.size || 0.06) * W;
      c.font = fontCss("Poppins-ExtraBold", fs); const w = c.measureText(txt).width + fs * 1.4, h = fs * 1.7;
      const bg = k === "cta" ? (col === "#FFFFFF" ? "#FF3B5C" : col) : (it.bg || ACC());
      c.fillStyle = bg; rrect(c, -w / 2, -h / 2, w, h, h / 2); c.fill(); c.shadowBlur = 0;
      c.fillStyle = k === "cta" ? (lum(bg) > 0.6 ? "#111" : "#fff") : (it.fg || "#111111");
      c.textAlign = "center"; c.textBaseline = "middle"; c.fillText(txt, 0, fs * 0.06);
      bb = { x: cx - w / 2, y: cy - h / 2, w, h };
    } else if (k === "media") {
      c.restore(); c.save(); c.globalAlpha *= st.a;       // imagen/clip propio (PiP): geometria del render
      const r = drawPip(c, it, lt, L, B, t, st);
      if (r) bb = r;
    } else if (k === "socials") {
      c.restore(); c.save(); c.globalAlpha *= st.a;       // posicion propia (centro del grupo)
      const r = drawSocials(c, it, lt, L, B, st);
      if (r) bb = r;
    } else { c.strokeStyle = col; c.lineWidth = 3; c.strokeRect(-size / 2, -size / 2, size, size); }
    c.restore();
    return bb;
  }

  // ---- medios propios (imagen o clip de /sources) en la vista en vivo
  /* Imagen: /tlsource/<sid> (formatos que el navegador decodifica; un HEIC no: se ve un marcador y
     el cuadro exacto al pausar). Clip: un <video> silenciado por medio, sincronizado al tiempo local
     del item (media_in + t, con repeticion o congelado del ultimo cuadro como el render). */
  const MEDIA_EL = {};
  function mediaEl(m) {
    if (!m || !m.sid) return null;
    const key = `${m.type}|${m.sid}`;
    let e = MEDIA_EL[key];
    if (!e) {
      e = MEDIA_EL[key] = { err: false, used: 0 };
      const url = `/tlsource/${encodeURIComponent(m.sid)}`;
      if (m.type === "video") {
        const v = document.createElement("video"); v.muted = true; v.playsInline = true; v.preload = "auto"; v.src = url;
        v.addEventListener("error", () => { e.err = true; });
        e.vid = v;
        const th = new Image(); th.src = m.thumb || `/tlsource/${encodeURIComponent(m.sid)}/thumb`; e.thumb = th;
      } else {
        const im = new Image(); im.onerror = () => { e.err = true; };
        im.onload = () => { if (FX.drawOverlayNow && V && V.paused) FX.drawOverlayNow(); };   // foto/logo en pausa: aparece al cargar
        im.src = url; e.img = im;
      }
    }
    return e;
  }
  /** Cuadro dibujable del medio en el tiempo local `lt` (y sincroniza el clip). -> {src, w, h} o null. */
  function mediaFrame(it, m, lt, playing) {
    const e = mediaEl(m); if (!e) return null;
    e.used = performance.now();
    if (e.img) return e.img.complete && e.img.naturalWidth ? { src: e.img, w: e.img.naturalWidth, h: e.img.naturalHeight } : null;
    const v = e.vid, d = +(m.dur || v.duration || 0), m0 = Math.max(0, +(it.media_in || 0));
    let tt = m0 + Math.max(0, lt);
    if (d > m0 + 0.05) tt = it.loop === false ? Math.min(tt, d - 0.04) : m0 + ((tt - m0) % (d - m0));
    if (playing && !(it.loop === false && tt >= d - 0.05)) {
      if (v.paused) { try { v.currentTime = tt; } catch (x) { /* nada */ } v.play().catch(() => {}); }
      else if (Math.abs(v.currentTime - tt) > 0.3) { try { v.currentTime = tt; } catch (x) { /* nada */ } }
    } else {
      if (!v.paused) v.pause();
      if (Math.abs(v.currentTime - tt) > 0.04 && !v.seeking) { try { v.currentTime = tt; } catch (x) { /* nada */ } }
    }
    if (v.readyState >= 2 && v.videoWidth) return { src: v, w: v.videoWidth, h: v.videoHeight };
    const th = e.thumb;
    return th && th.complete && th.naturalWidth ? { src: th, w: th.naturalWidth, h: th.naturalHeight } : null;
  }
  /** Estado de los medios propios cargados (pruebas): [{key, type, t, paused, ready, err}]. */
  FX.mediaState = () => Object.entries(MEDIA_EL).map(([k, e]) => ({ key: k, err: e.err, t: e.vid ? +e.vid.currentTime.toFixed(3) : null,
    paused: e.vid ? e.vid.paused : null, ready: e.vid ? e.vid.readyState : (e.img && e.img.complete ? 4 : 0) }));
  /** Los clips que no se dibujaron en este cuadro se pausan. */
  function pauseIdleMedia(now) { Object.values(MEDIA_EL).forEach(e => { if (e.vid && !e.vid.paused && now - e.used > 150) e.vid.pause(); }); }
  /** Como presentar un logo (fxcore.logoLook, igual que graphics.logo_look), medido una vez por imagen. */
  function logoLookOf(f) {
    const im = f.src; if (im._look) return im._look;
    let look = { bleed: false, plate: C.LOGO_PLATE, box: [0, 0, f.w, f.h] };
    try {
      const k = Math.min(1, 512 / Math.max(f.w, f.h)), w = Math.max(1, Math.round(f.w * k)), h = Math.max(1, Math.round(f.h * k));
      const cv = document.createElement("canvas"); cv.width = w; cv.height = h;
      const g = cv.getContext("2d", { willReadFrequently: true }); g.drawImage(im, 0, 0, w, h);
      look = C.logoLook(g.getImageData(0, 0, w, h).data, w, h);
      if (k !== 1) look.box = look.box.map(v => v / k);
    } catch (e) { /* lienzo sin lectura: placa blanca */ }
    im._look = look; return look;
  }
  /** graphics.draw_logo_badge: logo en una insignia `shape` (circle | squircle) de lado d centrada en
      (cx, cy): icono de app -> llena la insignia; si no, cabe con aire sobre su placa; filo fino. */
  function drawLogoBadge(c, f, cx, cy, d, shape) {
    const look = logoLookOf(f);
    c.save(); c.shadowBlur = 0; c.shadowOffsetY = 0; c.imageSmoothingQuality = "high";
    c.beginPath(); if (shape === "circle") c.arc(cx, cy, d / 2, 0, Math.PI * 2); else rrect(c, cx - d / 2, cy - d / 2, d, d, d * 0.3);
    c.save(); c.clip();
    if (look.bleed) drawFit(c, f, cx - d / 2, cy - d / 2, d, d, "cover", null, 1);
    else {
      c.fillStyle = look.plate; c.fillRect(cx - d / 2, cy - d / 2, d, d);
      const [lw, lh] = C.logoFit(look, d, shape), b = look.box;
      c.drawImage(f.src, b[0], b[1], b[2] - b[0], b[3] - b[1], cx - lw / 2, cy - lh / 2, lw, lh);
    }
    c.restore();
    c.lineWidth = Math.max(1, d * 0.018); c.strokeStyle = "rgba(0,0,0,.1)"; c.stroke();
    c.restore();
  }
  /** graphics.fit_src: dibuja `f` en la ventana (x, y, w, h) con cover/contain, focus y zoom. */
  function drawFit(c, f, x, y, w, h, fit, focus, zoom) {
    const iw = f.w, ih = f.h, z = clamp(+(zoom || 1), 1, 4);
    const fx = Array.isArray(focus) ? clamp(+focus[0], 0, 1) : 0.5, fy = Array.isArray(focus) ? clamp(+focus[1], 0, 1) : 0.5;
    if (fit === "contain" && z <= 1) {
      const k = Math.min(w / iw, h / ih), dw = iw * k, dh = ih * k;
      c.fillStyle = "#1A1A20"; c.fillRect(x, y, w, h);
      c.drawImage(f.src, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh); return;
    }
    const ar = w / Math.max(1e-6, h);
    let sw, sh; if (iw / ih > ar) { sw = ih * ar; sh = ih; } else { sw = iw; sh = iw / ar; }
    sw /= z; sh /= z;
    const sx = clamp(fx * iw - sw / 2, 0, iw - sw), sy = clamp(fy * ih - sh / 2, 0, ih - sh);
    c.drawImage(f.src, sx, sy, sw, sh, x, y, w, h);
  }
  /** Marcador de un medio que el navegador no muestra (HEIC) o que no se pudo leer. */
  function mediaPlaceholder(c, x, y, w, h, m, err) {
    const bad = err && !/\.(heic|heif|avif)$/i.test((m && m.name) || "");
    c.fillStyle = bad ? "#3A2226" : "#2A2D36"; c.fillRect(x, y, w, h);
    const s = Math.min(w, h) * 0.28, im = iconImage("photo", "#9AA3B5");
    if (im) c.drawImage(im, x + w / 2 - s / 2, y + h / 2 - s * 0.75, s, s);
    c.fillStyle = "#C9D1E0"; c.textAlign = "center"; c.textBaseline = "middle";
    c.font = `600 ${Math.max(9, Math.min(w, h) * 0.075)}px -apple-system, Helvetica, Arial`;
    // HEIC/AVIF: el navegador no los decodifica pero el motor si (no es un error del archivo)
    const noBrowser = /\.(heic|heif|avif)$/i.test((m && m.name) || "");
    c.fillText(err && !noBrowser ? "No se pudo leer" : "Vista exacta al pausar", x + w / 2, y + h / 2 + s * 0.55, w * 0.9);
    c.textAlign = "left";
  }
  /** graphics.socials (redes del perfil): chips con la insignia de la red en columna, fila o
      rejilla (`layout`, `cols`, `width`), o marca de agua (`variant` watermark con `backdrop`). */
  function drawSocials(c, it, lt, L, B, st) {
    const wm = it.variant === "watermark", W = B.w;
    const h = (it.size != null ? +it.size : (wm ? 0.034 : 0.074)) * W, maxw = SYM_W * W;
    const links = (it.links || []).slice(0, 8).map(l => {
      const p = PLATS[l && l.platform] ? l.platform : "web";
      return { p, t: String((l && l.text) || "").trim() || (PLATS[p] ? PLATS[p].label : "Web"), lg: l && l.logo && l.logo.sid ? l.logo : null };
    });
    if (!links.length) return null;
    const fontN = wm ? "Poppins-SemiBold" : "Poppins-Bold";
    const fitF = (txt, px, room) => { let f = px; c.font = fontCss(fontN, f); const w0 = c.measureText(txt).width; if (w0 > room) { f = Math.max(px * 0.6, f * room / w0); c.font = fontCss(fontN, f); } return f; };
    let chips = links.map(l => {
      const f = fitF(l.t, wm ? h * 0.62 : h * 0.36, maxw - h * 1.4);
      const w = wm ? h * 1.05 + c.measureText(l.t).width : h * 0.14 + h * 0.72 + h * 0.2 + c.measureText(l.t).width + h * 0.42;
      return { p: l.p, t: l.t, f, w, lg: l.lg };
    });
    const gap = h * (wm ? 0.3 : 0.22);
    let rows = [];
    if (it.layout === "grid" && !wm) {
      const cols = Math.max(1, Math.min(4, +(it.cols || 2), chips.length));
      const wfix = it.width ? clamp(+it.width, 0, SYM_W) * W : 0;
      const cw = Math.min(wfix ? (wfix - gap * (cols - 1)) / cols : Math.max(...chips.map(x => x.w)), (maxw - gap * (cols - 1)) / cols);
      chips = chips.map(x => Object.assign(x, { f: fitF(x.t, h * 0.36, Math.max(h, cw - h * 1.48)), w: cw }));
      for (let i = 0; i < chips.length; i += cols) rows.push(chips.slice(i, i + cols));
    } else if (it.layout === "row" || wm) {
      let cur = [], cw = 0;
      chips.forEach(x => { if (cur.length && cw + gap + x.w > maxw) { rows.push(cur); cur = []; cw = 0; } cw += (cur.length ? gap : 0) + x.w; cur.push(x); });
      if (cur.length) rows.push(cur);
    } else rows = chips.map(x => [x]);
    const totW = Math.max(...rows.map(r => r.reduce((q, x) => q + x.w, 0) + gap * (r.length - 1)));
    const title = !wm && it.title ? String(it.title).toUpperCase() : "";
    const totH = rows.length * h + (rows.length - 1) * gap + (title ? h * 0.52 : 0);
    let cx = B.x + (it.x != null ? +it.x : C.XC) * W, cy = B.y + (it.y != null ? +it.y : 0.62) * B.h;
    cx = clamp(cx, B.x + C.SAFE.left * W + totW / 2, B.x + C.SAFE.right * W - totW / 2);
    if (totH < (C.SAFE.bottom - C.SAFE.top) * B.h) cy = clamp(cy, B.y + C.SAFE.top * B.h + totH / 2, B.y + C.SAFE.bottom * B.h - totH / 2);
    const dark = it.theme === "dark", mono = it.icon_style === "mono";
    const stagger = clamp(it.stagger != null ? +it.stagger : 0.09, 0, 0.5);
    c.translate(cx, cy); if (it.rotation) c.rotate(+it.rotation * Math.PI / 180);
    let y = -totH / 2;
    if (title) { c.font = fontCss("Poppins-SemiBold", h * 0.3); c.fillStyle = "#FFFFFF"; c.textAlign = "center"; c.textBaseline = "middle"; c.shadowColor = "rgba(0,0,0,.45)"; c.shadowBlur = 6 * B.s; c.fillText(title, 0, y + h * 0.18); c.shadowBlur = 0; c.textAlign = "left"; y += h * 0.52; }
    let k = 0;
    rows.forEach(row => {
      const rw = row.reduce((q, x) => q + x.w, 0) + gap * (row.length - 1);
      let x = -rw / 2;
      row.forEach(ch => {
        const tl = lt - (k++) * stagger;
        if (tl >= 0) {
          const e = clamp(tl / 0.25, 0, 1), sc = 0.8 + 0.2 * C.ease("out_back", e);
          c.save(); c.globalAlpha *= e; c.translate(x + ch.w / 2, y + h / 2); c.scale(sc, sc);
          const pl = PLATS[ch.p] || {}, bcol = mono ? "#15151B" : (pl.color || "#1E2330");
          c.textBaseline = "middle";
          if (wm) {
            const bk = clamp(it.backdrop != null ? +it.backdrop : 0.34, 0, 1), op = clamp(it.opacity != null ? +it.opacity : 0.72, 0.15, 1);
            if (bk > 0.01) { c.fillStyle = `rgba(0,0,0,${bk})`; rrect(c, -ch.w / 2 - h * 0.28, -h * 0.62, ch.w + h * 0.56, h * 1.24, h * 0.62); c.fill(); }
            const ib = h * 0.78, im = iconImage(pl.icon || "world", "#FFFFFF");
            c.globalAlpha *= op;
            if (im) c.drawImage(im, -ch.w / 2, -ib / 2, ib, ib);
            c.font = fontCss(fontN, ch.f); c.fillStyle = "#FFFFFF"; c.fillText(ch.t, -ch.w / 2 + h * 1.05, 0);
          } else {
            c.shadowColor = "rgba(0,0,0,.3)"; c.shadowBlur = 12 * B.s; c.shadowOffsetY = 4 * B.s;
            c.fillStyle = dark ? "rgba(17,17,22,.86)" : "#FFFFFF"; rrect(c, -ch.w / 2, -h / 2, ch.w, h, h / 2); c.fill();
            c.shadowBlur = 0; c.shadowOffsetY = 0;
            const d = h * 0.72, bx = -ch.w / 2 + h * 0.14 + d / 2;
            const lgf = ch.lg && !mono ? mediaFrame(it, ch.lg, 0, false) : null;   // logo del sitio (graphics: chips a color)
            if (lgf) drawLogoBadge(c, lgf, bx, 0, d, "circle");
            else {
              c.fillStyle = bcol; c.beginPath(); c.arc(bx, 0, d / 2, 0, Math.PI * 2); c.fill();
              const im = iconImage(pl.icon || "world", lum(bcol) > 0.6 ? "#15151B" : "#FFFFFF"); if (im) c.drawImage(im, bx - d * 0.3, -d * 0.3, d * 0.6, d * 0.6);
            }
            c.font = fontCss(fontN, ch.f); c.fillStyle = dark ? "#FFFFFF" : "#15151B"; c.fillText(ch.t, -ch.w / 2 + h * 0.14 + d + h * 0.2, 0);
          }
          c.restore();
        }
        x += ch.w + gap;
      });
      y += h + gap;
    });
    return { x: cx - totW / 2, y: cy - totH / 2, w: totW, h: totH };
  }

  /** Rostro de la toma en t ([cx, cy, w, h] en la caja) o null. */
  FX.faceAt = t => faceAt(t);
  /** Posicion efectiva del PiP (la misma colocacion automatica que el render: fxcore.pipPlace). */
  FX.pipPlace = function (it) {
    const m = it.media || {};
    // cara mediana en el rango del item (pista de rostro de las tomas), como el render
    return C.pipPlace(PLAN.fx, it, C.faceMedian(PLAN.shots, +it.start || 0, +it.end || 0), +m.w || 0, +m.h || 0, 1080, 1920);
  };
  /** graphics.media (PiP) con la geometria de graphics.draw_media. */
  function drawPip(c, it, lt, L, B, t, st) {
    const m = it.media && it.media.sid ? it.media : null;
    const pl = FX.pipPlace(it);
    const f = m ? mediaFrame(it, m, lt, V && !V.paused) : null;
    const cw = f ? f.w : +((m && m.w) || 0), ch = f ? f.h : +((m && m.h) || 0);
    const [w, h] = C.pipCard(pl.size, it.aspect, cw, ch, B.w);
    const cx = B.x + (pl.x + st.dx) * B.w, cy = B.y + (pl.y + st.dy) * B.h;
    const rad = clamp(it.radius != null ? +it.radius : 14, 0, 80) * B.s;
    const bw = it.frame !== false ? Math.max(4 * B.s, Math.min(w, h) * 0.018) : 0;
    c.translate(cx, cy); c.scale(st.sc, st.sc); if (it.rotation) c.rotate(+it.rotation * Math.PI / 180);
    const sh = it.shadow != null ? +it.shadow : 0.55;
    if (sh > 0) { c.shadowColor = `rgba(0,0,0,${0.5 * sh})`; c.shadowBlur = 26 * B.s; c.shadowOffsetY = 8 * B.s; }
    if (bw) { c.fillStyle = "#FFFFFF"; rrect(c, -w / 2 - bw, -h / 2 - bw, w + 2 * bw, h + 2 * bw, rad + bw); c.fill(); }
    else { c.fillStyle = "#000"; rrect(c, -w / 2, -h / 2, w, h, rad); c.fill(); }
    c.shadowBlur = 0; c.shadowOffsetY = 0;
    c.save(); rrect(c, -w / 2, -h / 2, w, h, rad); c.clip();
    if (f) drawFit(c, f, -w / 2, -h / 2, w, h, it.fit || "cover", it.focus, it.zoom);
    else mediaPlaceholder(c, -w / 2, -h / 2, w, h, m, m && MEDIA_EL[`${m.type}|${m.sid}`] && MEDIA_EL[`${m.type}|${m.sid}`].err);
    c.restore();
    return { x: cx - w / 2 - bw, y: cy - h / 2 - bw, w: w + 2 * bw, h: h + 2 * bw };
  }
  /** cutaways.photo: foto instantanea con borde, pie a mano y cinta (geometria de draw_photo). */
  function drawPhoto(c, it, lt, L) {
    const B = LB, W = B.w, H = B.h;
    const pw = W * clamp(it.size != null ? +it.size : 0.72, 0.3, 0.8);
    const phImg = pw * 0.92 * 1.2, border = pw * 0.045, bottom = pw * 0.2, fw = pw, fh = phImg + border + bottom;
    const sp = 1 - Math.pow(1 - clamp(lt / 0.55, 0, 1), 3);
    const tilt = it.tilt != null ? +it.tilt : -4;
    const rot = tilt + (1 - sp) * 14, dy = (1 - sp) * H * 0.55, sc = 0.92 + 0.08 * sp;
    const m = it.media && it.media.sid ? it.media : null;
    const f = m ? mediaFrame(it, m, lt, V && !V.paused) : (V.videoWidth ? { src: V, w: V.videoWidth, h: V.videoHeight } : null);
    c.save();
    c.translate(B.x + W * (it.x != null ? +it.x : C.XC), B.y + H * (it.y != null ? +it.y : 0.46) + dy); c.rotate(rot * Math.PI / 180); c.scale(sc, sc);
    c.shadowColor = "rgba(0,0,0,.4)"; c.shadowBlur = 24 * B.s; c.shadowOffsetY = 10 * B.s;
    c.fillStyle = "#FBFAF6"; rrect(c, -fw / 2, -fh / 2, fw, fh, 6 * B.s); c.fill(); c.shadowBlur = 0; c.shadowOffsetY = 0;
    const ix = -fw / 2 + border, iy = -fh / 2 + border, iw = fw - 2 * border;
    c.save(); c.beginPath(); c.rect(ix, iy, iw, phImg); c.clip();
    if (f) drawFit(c, f, ix, iy, iw, phImg, m ? (it.fit || "cover") : "cover", m ? it.focus : null, m ? it.zoom : 1);
    else mediaPlaceholder(c, ix, iy, iw, phImg, m, m && MEDIA_EL[`${m.type}|${m.sid}`] && MEDIA_EL[`${m.type}|${m.sid}`].err);
    const dev = 1 - (1 - Math.pow(1 - clamp((lt - 0.1) / 0.9, 0, 1), 3));
    if (dev > 0.01) { c.fillStyle = `rgba(244,241,234,${0.85 * dev})`; c.fillRect(ix, iy, iw, phImg); }
    c.restore();
    const cap = (it.caption != null ? it.caption : typeof it.text === "string" ? it.text : "") || "";
    if (cap) {
      const n = Math.ceil(String(cap).length * clamp((lt - 0.45) / 0.5, 0, 1));
      const kitScript = (PLAN.director && PLAN.director.kit && PLAN.director.kit.script) || "Caveat-Bold";
      let fs = bottom * 0.62; c.font = fontCss(kitScript, fs);
      const tw = c.measureText(String(cap)).width; if (tw > fw - 2 * border) { fs *= (fw - 2 * border) / tw; c.font = fontCss(kitScript, fs); }
      c.fillStyle = "#2A2A33"; c.textAlign = "center"; c.textBaseline = "middle";
      c.fillText(String(cap).slice(0, n), 0, fh / 2 - bottom * 0.5); c.textAlign = "left";
    }
    c.save(); c.rotate((-tilt * 0.8 + 3) * Math.PI / 180); c.fillStyle = "rgba(242,230,200,.78)"; c.fillRect(-pw * 0.14, -fh / 2 - pw * 0.035, pw * 0.28, pw * 0.08); c.restore();
    c.restore();
  }
  /** cutaways.self_broll con un medio propio: el archivo a pantalla completa (cover con focus). */
  function drawSelfBroll(c, it, lt) {
    const m = it.media && it.media.sid ? it.media : null; if (!m) return false;
    const f = mediaFrame(it, m, lt, V && !V.paused);
    if (f) drawFit(c, f, 0, 0, CW, CH, it.fit || "cover", it.focus, it.zoom);
    else mediaPlaceholder(c, LB.x + LB.w * 0.2, LB.y + LB.h * 0.35, LB.w * 0.6, LB.h * 0.3, m, MEDIA_EL[`${m.type}|${m.sid}`] && MEDIA_EL[`${m.type}|${m.sid}`].err);
    return true;
  }

  function drawSceneBg(c, bg, colors, lt) {
    const cols = (colors && colors.length ? colors : ["#101218", "#2EC4B6"]);
    const W = CW, H = CH;
    if (bg === "blur_self") { c.save(); c.filter = "blur(14px) brightness(.7)"; c.drawImage(V, -20, -20, W + 40, H + 40); c.restore(); return; }
    if (bg === "paper") { c.fillStyle = cols[0] && lum(cols[0]) > 0.5 ? cols[0] : "#EFE8DC"; c.fillRect(0, 0, W, H); return; }
    if (bg === "color") { c.fillStyle = cols[0]; c.fillRect(0, 0, W, H); return; }
    const g = c.createLinearGradient(0, 0, W * 0.3, H);
    g.addColorStop(0, cols[0]); g.addColorStop(1, cols[1] || cols[0]); c.fillStyle = g; c.fillRect(0, 0, W, H);
    if (bg === "dark_stars") { c.fillStyle = "#0B0C10"; c.globalAlpha = 0.72; c.fillRect(0, 0, W, H); c.globalAlpha = 1; c.fillStyle = "rgba(255,255,255,.55)"; for (let i = 0; i < 60; i++) { const x = (i * 137.5 % 100) / 100 * W, y = ((i * 71.3 + lt * 3) % 100) / 100 * H; c.fillRect(x, y, 1.5, 1.5); } }
    else if (bg === "grid_warp") { c.strokeStyle = "rgba(255,255,255,.12)"; c.lineWidth = 1; const st = W / 9; for (let x = 0; x < W; x += st) { c.beginPath(); c.moveTo(x, 0); c.lineTo(x, H); c.stroke(); } for (let y = (lt * 20) % st; y < H; y += st) { c.beginPath(); c.moveTo(0, y); c.lineTo(W, y); c.stroke(); } }
    else if (bg === "rays") { c.save(); c.translate(W / 2, H / 2); c.rotate(lt * 0.2); c.fillStyle = "rgba(255,255,255,.08)"; for (let i = 0; i < 12; i++) { c.rotate(Math.PI / 6); c.beginPath(); c.moveTo(0, 0); c.lineTo(H, -H * 0.12); c.lineTo(H, H * 0.12); c.fill(); } c.restore(); }
    else if (bg === "halftone") { c.fillStyle = "rgba(0,0,0,.18)"; const st = W / 30; for (let y = 0; y < H; y += st) for (let x = 0; x < W; x += st) { c.beginPath(); c.arc(x, y, st * 0.3 * (y / H), 0, Math.PI * 2); c.fill(); } }
  }

  /** fx.media "float_card" con la geometria y animacion del render (window.FloatCard = port de
      motion/media.py): tarjeta centrada (simetrica al pixel) sobre el orador desenfocado y oscurecido
      (el filtro va en FX.videoLook), entrada con resorte, flotado, Ken Burns en imagenes, salida
      limpia; deja libre la franja de los subtitulos. El video va silenciado en FloatCard.srcTime. */
  function drawMediaCard(c, it, t) {
    const FCm = window.FloatCard; if (!FCm) return null;
    const B = LB, lt = t - it.start, L = Math.max(0.05, it.end - it.start);
    const m = { sid: it.sid, type: it.kind === "video" ? "video" : "image", dur: +it.media_dur || 0, name: it.name };
    const e = mediaEl(m);
    let f = null;
    if (e) {
      e.used = performance.now();
      if (e.img) f = e.img.complete && e.img.naturalWidth ? { src: e.img, w: e.img.naturalWidth, h: e.img.naturalHeight } : null;
      else {
        const v = e.vid, d = +m.dur || (isFinite(v.duration) ? v.duration : 0), tt = FCm.srcTime(it, lt, d), playing = V && !V.paused;
        const rate = FCm.speedOf(it);
        if (playing) {
          if (Math.abs(v.playbackRate - rate) > 1e-3) v.playbackRate = rate;
          if (v.paused) { try { v.currentTime = tt; } catch (x) { /* nada */ } v.play().catch(() => {}); }
          else if (Math.abs(v.currentTime - tt) > 0.3) { try { v.currentTime = tt; } catch (x) { /* nada */ } }
        } else {
          if (!v.paused) v.pause();
          if (Math.abs(v.currentTime - tt) > 0.04 && !v.seeking) { try { v.currentTime = tt; } catch (x) { /* nada */ } }
        }
        if (v.readyState >= 2 && v.videoWidth) f = { src: v, w: v.videoWidth, h: v.videoHeight };
        else { const th = e.thumb; f = th && th.complete && th.naturalWidth ? { src: th, w: th.naturalWidth, h: th.naturalHeight } : null; }
      }
    }
    const capOn = !!(PLAN.fx.captions && PLAN.fx.captions.enabled !== false);
    const card = FCm.cardLayout(f ? f.w : 1080, f ? f.h : 1350, B.w / B.h, FCm.captionTop(PLAN.fx.captions, capOn, it.start, it.end, B.w / B.h), it.width, it.aspect, it.y,
      FCm.captionBottom(PLAN.fx.captions, capOn, it.start, it.end, B.w / B.h));
    const st = FCm.cardAnim(lt, L, FCm.param(it, "in_dur"), FCm.param(it, "out_dur"), FCm.param(it, "float"));
    // el render hornea el clip al tamano de la tarjeta (sin Ken Burns); las imagenes si lo llevan
    const r = FCm.drawCard(c, f, B, card, st, it.kind === "video" ? Object.assign({}, it, { kenburns: 0 }) : it, lt, L);
    if (!f && r && e && e.err) mediaPlaceholder(c, r.x, r.y, r.w, r.h, m, true);
    return r;
  }
  FX.drawMediaCard = drawMediaCard;
  /** Cajas de lo dibujado en el ultimo cuadro y la caja de layout (pruebas: simetria medida). */
  FX.hits = () => ({ hits: HITS.map(h => ({ track: h.track, id: h.id, bb: Object.assign({}, h.bb) })), box: Object.assign({}, LB) });

  let HITS = [];   // cajas de lo dibujado en el ultimo cuadro (para seleccionar en el canvas)
  let DRAG_GUIDE = false;   // guia vertical del centro mientras el arrastre queda imantado al centro
  /** Composicion en vivo sobre el canvas (despues del video base). */
  FX.drawLive = function (t) {
    if (!isPro()) return;
    const c = CTX; HITS = [];
    layoutBox();
    ensureCaptions();
    // vinieta del look
    const g = PLAN.fx.grade || {};
    if (g.enabled !== false && +g.vignette > 0) {
      const rg = c.createRadialGradient(CW / 2, CH / 2, Math.min(CW, CH) * 0.35, CW / 2, CH / 2, Math.max(CW, CH) * 0.72);
      rg.addColorStop(0, "rgba(0,0,0,0)"); rg.addColorStop(1, `rgba(0,0,0,${clamp(+g.vignette * 1.6, 0, 0.8)})`); c.fillStyle = rg; c.fillRect(0, 0, CW, CH);
    }
    // flash_frame
    items("effects").forEach(e => { if (e.enabled !== false && e.kind === "flash_frame" && t >= e.start && t < e.end) { const p = (t - e.start) / Math.max(0.05, e.end - e.start); c.fillStyle = hexA(e.color || "#FFFFFF", (1 - p) * 0.9 * (e.intensity == null ? 1 : e.intensity)); c.fillRect(0, 0, CW, CH); } });
    // texto detras del sujeto (sin recorte en vivo)
    const texts = activeRange("text", t);
    texts.filter(x => x.behind).forEach(it => { const bb = drawTextItem(c, it, t - it.start, it.end - it.start); if (bb) HITS.push({ track: "text", id: it.id, bb }); });
    // escenas
    const scenes = activeRange("cutaways", t);
    scenes.forEach(it => {
      const lt = t - it.start, L = it.end - it.start;
      const a = it.anim_in === "cut" ? 1 : clamp(lt / 0.2, 0, 1) * (it.anim_out === "cut" ? 1 : clamp((L - lt) / 0.2, 0, 1));
      c.save(); c.globalAlpha = a;
      if (!(it.kind === "self_broll" && drawSelfBroll(c, it, lt))) drawSceneBg(c, it.background, it.bg_colors, lt);
      if (it.kind === "photo") drawPhoto(c, it, lt, L);
      if (it.text && (it.text.text || it.text.preset === "counter")) {
        const tx = Object.assign({ x: C.XC, y: 0.45, size: 0.12, preset: "title_pop" }, it.text);
        drawTextItem(c, tx, lt, L);
      }
      // cutaways._card_from: centrada y del ancho maximo simetrico
      if (it.card) drawGraphic(c, Object.assign({ kind: "card", x: C.XC, y: 0.52, size: C.SYM_W }, it.card), lt, L, null, t);
      // graficos embebidos (cutaways._embedded): offset y length relativos a la escena
      (it.graphics || []).forEach(gr => {
        if (!gr || gr.enabled === false) return;
        const off = +(gr.offset || 0), ln = gr.length != null ? +gr.length : Math.max(0.05, L - off), lg = lt - off;
        if (lg >= 0 && lg < ln) drawGraphic(c, gr, lg, ln, null, t);
      });
      c.restore();
      HITS.push({ track: "cutaways", id: it.id, bb: { x: 0, y: 0, w: CW, h: CH }, scene: true });
    });
    // recursos (tarjeta flotante): centrados y fijos (simetria); se seleccionan pero no se arrastran.
    // Mismo orden que el render (Compositor.frame 4b): despues de las escenas y ANTES de graficos,
    // textos y subtitulos (esos quedan encima de la tarjeta)
    activeRange("media", t).filter(it => it.sid).forEach(it => { const bb = drawMediaCard(c, it, t); if (bb) HITS.push({ track: "media", id: it.id, bb, fixed: true }); });
    // graficos (por z)
    activeRange("graphics", t).sort((a, b) => (+a.z || 0) - (+b.z || 0)).forEach(it => { const bb = drawGraphic(c, it, t - it.start, it.end - it.start, null, t); if (bb) HITS.push({ track: "graphics", id: it.id, bb }); });
    // texto normal
    texts.filter(x => !x.behind).sort((a, b) => (+a.z || 0) - (+b.z || 0)).forEach(it => { const bb = drawTextItem(c, it, t - it.start, it.end - it.start); if (bb) HITS.push({ track: "text", id: it.id, bb }); });
    // subtitulos: misma regla que el render (fxcore.captionsHiddenAt)
    if (!C.captionsHiddenAt(PLAN.fx, t, LB.w / LB.h)) FX.drawCaptions(t);
    // transiciones
    items("transitions").forEach(tr => {
      if (tr.enabled === false || tr.kind === "cut") return;
      const d = Math.max(0.05, +tr.dur || 0.4), a0 = tr.at - d / 2;
      if (t < a0 || t > a0 + d) return;
      const p = (t - a0) / d, peak = 1 - Math.abs(2 * p - 1), k = tr.intensity == null ? 1 : +tr.intensity;
      if (tr.kind === "flash") { c.fillStyle = hexA(NAMED[tr.color] || tr.color || "#FFFFFF", peak * 0.95 * k); c.fillRect(0, 0, CW, CH); }
      else if (tr.kind === "light_leak" || tr.kind === "film_burn") {
        const rg = c.createRadialGradient(CW * 0.8, CH * 0.2, 0, CW * 0.8, CH * 0.2, CH * 0.9);
        rg.addColorStop(0, hexA(NAMED[tr.color] || tr.color || "#FF7A3D", peak * 0.95 * k)); rg.addColorStop(1, hexA("#FFB36B", 0)); c.fillStyle = rg; c.fillRect(0, 0, CW, CH);
      } else { c.fillStyle = `rgba(255,255,255,${peak * 0.35 * k})`; c.fillRect(0, 0, CW, CH); }
    });
    pauseIdleMedia(performance.now());
  };

  /** Subtitulos con el preset del plan y las fuentes reales. */
  /* Grupos de subtitulos: los REALES del export (POST /captions: text.caption_groups en tiempo
     original, con sus lineas y palabras de acento). Mientras llegan (o sin servidor) se usa la
     agrupacion aproximada. Se piden de nuevo solo si cambia algo que los cambia (frases del idioma,
     estilo de subtitulos, cortes, tomas, formato, estilo del director). */
  let CAPSRV = { key: "", groups: null, pending: "", timer: null, ctrl: null, fails: 0 };
  function capServerKey() {
    const cfg = PLAN.fx.captions || {};
    return JSON.stringify([P(), cfg, PLAN.cuts, (PLAN.shots || []).map(s => [s.start, s.end]), FMT, (PLAN.director || {}).style, LANG]);
  }
  function requestCaptions(key) {
    if (!JOB || JOB === "demo" || CAPSRV.pending === key || (FEATURES.size && !FEATURES.has("captions"))) return;
    clearTimeout(CAPSRV.timer); CAPSRV.pending = key;
    CAPSRV.timer = setTimeout(async () => {
      if (CAPSRV.ctrl) CAPSRV.ctrl.abort();
      const ctrl = CAPSRV.ctrl = new AbortController();
      try {
        const g = await jpost(`/captions/${JOB}`, { plan: planForServer(), fmt: FMT || "9:16" }, ctrl.signal);
        if (ctrl !== CAPSRV.ctrl) return;
        if (Array.isArray(g)) { CAPSRV.key = key; CAPSRV.groups = g; CAPSRV.fails = 0; CAPSRV.lang = LANG; }
      } catch (e) { if (e.name !== "AbortError") CAPSRV.fails++; }
      finally { if (ctrl === CAPSRV.ctrl) { CAPSRV.ctrl = null; if (CAPSRV.pending === key) CAPSRV.pending = ""; } }
    }, CAPSRV.groups ? 350 : 0);
  }
  FX.captionSource = () => (CAPSRV.groups && CAPSRV.key === capServerKey() ? "render" : "aprox");
  /** Pide los grupos del render cuando cambia lo que los cambia, aunque en este instante no se
      dibujen subtitulos (la clave se revisa como mucho cada 300 ms). */
  let capCheckAt = 0;
  function ensureCaptions() {
    const now = performance.now(); if (now - capCheckAt < 300) return;
    capCheckAt = now;
    const cfg = PLAN.fx.captions || {};
    if (cfg.enabled === false || cfg.preset === "none") return;
    const sk = capServerKey();
    if (sk !== CAPSRV.key && CAPSRV.fails < 3) requestCaptions(sk);
  }
  FX.captionGroupsNow = () => CAPSRV.groups;
  /* Geometria de los subtitulos en vivo = la del render (fxcore.captionLayout: tamano y espacio del
     preset, envoltura balanceada, ancho util). Con los grupos de /captions se calibra con su `rect`
     (la caja real del export): mismo ancho, mismo centro y mismo alto. Las medidas de cada palabra
     salen del canvas con la fuente real (se cachean por fuente cargada). */
  const CAPM = new Map();
  function capMeasure(font) {
    const fam = ensureFont(font), c = CTX;
    return (txt, px) => {
      const key = `${fam}|${font}|${txt}`;
      let m = CAPM.get(key);
      if (!m) {
        if (CAPM.size > 3000) CAPM.clear();
        c.save(); c.font = fontCss(font, 100); const r = c.measureText(txt); c.restore();
        const adv = r.width;
        m = { adv, l: r.actualBoundingBoxLeft != null ? -r.actualBoundingBoxLeft : 0, r: r.actualBoundingBoxRight != null ? r.actualBoundingBoxRight : adv,
          asc: r.actualBoundingBoxAscent != null ? r.actualBoundingBoxAscent : 72, desc: r.actualBoundingBoxDescent != null ? r.actualBoundingBoxDescent : 0 };
        CAPM.set(key, m);
      }
      const k = px / 100;
      return { adv: m.adv * k, l: m.l * k, r: m.r * k, asc: m.asc * k, desc: m.desc * k };
    };
  }
  const capTitleFont = () => (PLAN.director && PLAN.director.kit && typeof PLAN.director.kit.title === "string" ? PLAN.director.kit.title : null);
  const capCase = (w, cs) => (cs === "upper" ? w.toUpperCase() : cs === "lower" ? w.toLowerCase() : cs === "title" ? w.charAt(0).toUpperCase() + w.slice(1) : w);
  /** Layout (cacheado por grupo) del grupo `gr` en la caja B. srv = grupo del render (con rect/lines). */
  function capLayoutOf(gr, cfg, B, srv, noRect) {
    const D = C.captionCfg(cfg, capTitleFont());
    const words = gr.words.map(w => ({ w, txt: capCase(String(w.text || "").trim(), D.case) })).filter(x => x.txt);
    if (!words.length) return null;
    const key = `${B.w.toFixed(1)}|${B.h.toFixed(1)}|${ensureFont(D.font)}|${JSON.stringify(cfg)}|${noRect ? 1 : 0}`;
    const cache = noRect ? null : gr._lay;
    if (cache && cache.key === key) return cache.L;
    const L = C.captionLayout(words.map(x => x.txt), cfg, B.w, B.h, capMeasure(D.font),
      { lines: srv ? gr.lines : null, rect: srv && !noRect ? gr.rect : null, titleFont: capTitleFont() });
    if (!L) return null;
    L.words = words; L.D = D;
    if (!noRect) { try { Object.defineProperty(gr, "_lay", { value: { key, L }, writable: true, configurable: true, enumerable: false }); } catch (e) { /* grupo congelado */ } }
    return L;
  }
  /** Grupo de subtitulos activo en t (del render o aproximado). -> {gr, srv} o null. */
  function capGroupNow(t) {
    const cfg = PLAN.fx.captions || {};
    const sk = capServerKey();
    if (CAPSRV.groups && (sk === CAPSRV.key || (CAPSRV.pending === sk && CAPSRV.lang === LANG))) {
      const gr = C.capGroupAt(CAPSRV.groups, t); return gr ? { gr, srv: true } : null;
    }
    const phs = P();
    const key = JSON.stringify([cfg.max_words, phs.length, phs.length ? phs[0].length : 0, LANG, (UNDO || []).length]);
    if (key !== CAPCACHE.key) CAPCACHE = { key, groups: C.captionGroups(phs, cfg.max_words || 4) };
    const gr = CAPCACHE.groups.find(g => t >= g.start - 0.02 && t < g.end + 0.12);
    return gr ? { gr, srv: false } : null;
  }
  /** Diagnostico de paridad (pruebas): ancho del grupo en vivo con y sin calibrar contra el rect del
      export, en fraccion del ancho de la caja. */
  FX.captionDebug = function (t) {
    const cfg = PLAN.fx.captions || {}, g = capGroupNow(t);
    if (!g) return null;
    layoutBox();
    const L = capLayoutOf(g.gr, cfg, LB, g.srv), U = capLayoutOf(g.gr, cfg, LB, g.srv, true);
    const wOf = X => X ? Math.max(...X.rows.map(r => r.w)) / LB.w : null;
    return { srv: g.srv, text: g.gr.text || g.gr.words.map(w => w.text).join(" "), lines: L ? L.rows.length : null,
      rectW: g.srv && g.gr.rect ? +(g.gr.rect[2] - g.gr.rect[0]).toFixed(4) : null, liveW: wOf(L), portW: wOf(U), font: L && L.D.font,
      fontReady: L ? FONT_STATE[L.D.font] === "ok" : false };
  };
  function mixHex(a, b, q) {
    const p = h => { const s = String(h || "#fff").replace("#", ""); const v = s.length === 3 ? s.split("").map(x => x + x).join("") : s.slice(0, 6); const n = parseInt(v, 16) || 0; return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
    const x = p(a), y = p(b), k = clamp(q, 0, 1);
    return `rgb(${x.map((v, i) => Math.round(v + (y[i] - v) * k)).join(",")})`;
  }
  FX.drawCaptions = function (t) {
    const cfg = PLAN.fx.captions || {};
    if (cfg.enabled === false || cfg.preset === "none") return;
    const ra = C.captionRangeAlpha(cfg, t);          // filas y_ranges con hide (primer plano con CTA)
    if (ra <= 0.003) return;
    const sk = capServerKey();
    if (sk !== CAPSRV.key && CAPSRV.fails < 3) requestCaptions(sk);
    const g = capGroupNow(t);
    if (!g || !g.gr.words || !g.gr.words.length) return;
    const gr = g.gr;
    // como text.draw_captions: un grupo que empezo tapado por un titulo no reaparece al terminar
    // el titulo (repetiria lo que el titulo ya mostro)
    if (C.captionGroupSuppressed(PLAN.fx, gr, t, LB.w / LB.h)) return;
    const c = CTX, B = LB, s = B.s;
    const L = capLayoutOf(gr, cfg, B, g.srv);
    if (!L) return;
    const D = L.D, fs = L.fs;
    const color = cfg.color || "#FFFFFF", accent = cfg.accent || ACC(), hi = cfg.highlight || accent;
    const accentSet = g.srv && D.kw_accent !== false && cfg.kw_accent !== false ? new Set((gr.accent || []).map(C.normWord)) : null;
    // altura: la del export (y_ranges, foto con pie) respecto a la base del preset
    const dyY = (C.captionYAt(PLAN.fx, t, B.w / B.h) - D.y) * B.h;
    const top = B.y + L.top + dyY;
    // salida del grupo (la de los grupos del render ya viene en su permanencia)
    const out = g.srv ? clamp((+gr.end - t) / 0.11, 0, 1) : clamp((gr.end + 0.12 - t) / 0.12, 0, 1);
    let ga = out * ra, gdy = 0;
    if (D.reveal === "group") { const u = t - gr.start; ga *= C.ease("out_cubic", u / 0.14); gdy = (1 - C.ease("out_cubic", u / 0.35)) * 0.22 * fs; }
    if (ga <= 0.003) return;
    c.save(); c.textBaseline = "alphabetic"; c.lineJoin = "round";
    const stroke = 2 * D.stroke * s;
    // palabra de la pastilla (boxed): la ultima que ya empezo
    let pill = -1; if (D.active === "pill") L.words.forEach((x, i) => { if (t >= +x.w.start - 0.03) pill = i; });
    L.rows.forEach(r => {
      const vis = i => D.reveal !== "word" || t >= +L.words[i].w.start - 0.05;
      let v1 = r.a; while (v1 < r.b && vis(v1)) v1++;
      if (v1 === r.a) return;
      const sc = r.sc, M = L.M;
      // recentrado: la parte ya dicha queda centrada (text._recenter_dx; aqui sin resorte)
      let dx = 0;
      if (D.recenter && D.reveal === "word" && v1 < r.b) {
        const l = r.x0 + L.xs[r.a] + M[r.a].l * sc, rr = r.x0 + L.xs[v1 - 1] + M[v1 - 1].r * sc;
        dx = L.cx - (l + rr) / 2;
      }
      const base = top + r.base + gdy;
      for (let i = r.a; i < v1; i++) {
        const ww = L.words[i], w = ww.w, x = B.x + r.x0 + L.xs[i] + dx, wd = M[i].adv * sc;
        const u = t - (+w.start - 0.05);
        let a = 1, wsc = 1, wdy = 0;
        if (D.reveal === "word") {
          if (D.anim === "pop") { wsc = 0.8 + 0.2 * C.ease("out_back", u / 0.25); a = C.ease("out_cubic", u / 0.08); wdy = (1 - C.ease("out_cubic", u / 0.22)) * 0.1 * fs; }
          else if (D.anim === "pop_strong") { wsc = 0.7 + 0.3 * C.ease("out_back", u / 0.3); a = C.ease("out_cubic", u / 0.07); }
          else { a = C.ease("smoothstep", u / 0.2); wdy = (1 - C.ease("out_cubic", u / 0.3)) * 0.12 * fs; }
        }
        const kw = accentSet && accentSet.has(C.normWord(w.text));
        let fill = kw ? accent : color;
        const q = C.activeBump(t, w);
        if (D.active === "color" && !kw && q > 0.002) fill = mixHex(color, accent, q);
        if (D.active_scale > 0) { wsc *= 1 + D.active_scale * q; wdy -= 0.02 * fs * q; }
        if (D.active === "wipe") fill = t >= +w.end ? accent : color;
        c.save(); c.globalAlpha = ga * a;
        c.font = fontCss(D.font, fs * sc);
        const cy = base + wdy;
        if (wsc !== 1) { const px = x + wd / 2, py = cy - M[i].asc * sc / 2; c.translate(px, py); c.scale(wsc, wsc); c.translate(-px, -py); }
        if (i === pill) {
          const h = (M[i].asc + M[i].desc) * sc + 0.28 * fs;
          c.save(); c.shadowColor = "transparent"; c.fillStyle = hi; rrect(c, x + M[i].l * sc - 0.18 * fs, cy - M[i].asc * sc - 0.14 * fs, (M[i].r - M[i].l) * sc + 0.36 * fs, h, h * 0.3); c.fill(); c.restore();
          fill = lum(hi) > 0.6 ? "#111111" : "#FFFFFF";
        } else if (D.shadow > 0) { c.shadowColor = `rgba(0,0,0,${Math.min(0.85, 0.62 * D.shadow)})`; c.shadowBlur = 10 * s * D.shadow; c.shadowOffsetY = 3 * s; }
        if (stroke > 0) { c.lineWidth = stroke; c.strokeStyle = cfg.stroke_color || "#000000"; c.strokeText(ww.txt, x, cy); }
        c.fillStyle = fill; c.fillText(ww.txt, x, cy);
        if (D.active === "wipe" && t >= +w.start && t < +w.end) {
          // barrido del karaoke: la parte ya dicha de la palabra en acento
          const p = clamp((t - +w.start) / Math.max(0.08, +w.end - +w.start), 0, 1);
          c.save(); c.shadowColor = "transparent"; c.beginPath(); c.rect(x - fs, cy - fs * 1.5, (wd + fs) * p + fs * 0.05, fs * 2.2); c.clip(); c.fillStyle = accent; c.fillText(ww.txt, x, cy); c.restore();
        }
        c.restore();
      }
    });
    c.restore();
    const wmax = Math.max(...L.rows.map(r => r.w)), x0 = Math.min(...L.rows.map(r => r.x0));
    HITS.push({ track: "captions", id: "captions", bb: { x: B.x + x0, y: top, w: wmax, h: L.height } });
  };

  // ------------------------------------------------------------------ capa superior: seleccion y marcadores
  let OV = null, OVC = null;
  function ovCanvas() {
    if (!OV) { OV = $("#cvOver"); OVC = OV && OV.getContext("2d"); }
    return OVC;
  }
  FX.syncOverlayRect = function () {
    const wrap = $("#cvwrap"), ex = $("#pvExact"); if (!wrap || !CV) return;
    const r = CV.getBoundingClientRect(), w = wrap.getBoundingClientRect();
    [ov(), ex].forEach(el => { if (!el) return; el.style.left = (r.left - w.left) + "px"; el.style.top = (r.top - w.top) + "px"; el.style.width = r.width + "px"; el.style.height = r.height + "px"; });
    const o = ov(); if (o && (o.width !== CW || o.height !== CH)) { o.width = CW; o.height = CH; }
  };
  const ov = () => $("#cvOver");
  FX.drawOverlayNow = () => { if (typeof PLAN !== "undefined" && PLAN) FX.drawOverlay(V.currentTime || 0, true); };
  FX.drawOverlay = function (t, force) {
    const c = ovCanvas(); if (!c) return;
    c.clearRect(0, 0, OV.width, OV.height);
    if (!isPro()) return;
    // marcadores de elementos activos (efectos que el canvas no simula exacto)
    const exactShown = $("#pvExact") && $("#pvExact").classList.contains("on");
    if (!exactShown) {
      const tags = [];
      items("effects").forEach(e => { if (e.enabled !== false && t >= e.start && t < e.end) tags.push(C.kindLabel(SCHEMA, "effects", e.kind)); });
      items("transitions").forEach(tr => { const d = +tr.dur || 0.4; if (tr.enabled !== false && tr.kind !== "cut" && t >= tr.at - d / 2 && t <= tr.at + d / 2) tags.push("Transición: " + C.kindLabel(SCHEMA, "transitions", tr.kind)); });
      items("text").forEach(x => { if (x.enabled !== false && x.behind && t >= x.start && t < x.end) tags.push("Texto detrás (exacto al pausar)"); });
      if (tags.length) {
        c.save(); c.font = `600 ${Math.round(CW * 0.022)}px -apple-system, Helvetica, Arial`; c.textBaseline = "middle";
        let y = CH * 0.055;
        [...new Set(tags)].slice(0, 5).forEach(tg => {
          const w = c.measureText(tg).width + CW * 0.04, h = CW * 0.045, x = CW - w - CW * 0.03;
          c.fillStyle = "rgba(10,12,16,.72)"; rrect(c, x, y - h / 2, w, h, h / 2); c.fill();
          c.fillStyle = "#cfe0ff"; c.fillText(tg, x + CW * 0.02, y + 1); y += h + CW * 0.012;
        });
        c.restore();
      }
    }
    // guia del centro (arrastre imantado): la caja visible queda simetrica
    if (DRAG_GUIDE) {
      c.save(); c.strokeStyle = "rgba(255,210,63,.9)"; c.lineWidth = 1; c.setLineDash([5, 4]);
      const gx = Math.round(LB.x + LB.w / 2) + 0.5;
      c.beginPath(); c.moveTo(gx, LB.y); c.lineTo(gx, LB.y + LB.h); c.stroke(); c.restore();
    }
    // seleccion
    const sel = SEL;
    HITS.forEach(h => {
      const on = (sel && sel.type === "fx" && isSel(h.track, h.id)) || (sel && sel.type === "captions" && h.track === "captions");
      if (!on || h.scene) return;
      c.save(); c.strokeStyle = "#fff"; c.lineWidth = 1.5; c.setLineDash([6, 4]);
      c.strokeRect(h.bb.x - 4, h.bb.y - 4, h.bb.w + 8, h.bb.h + 8); c.setLineDash([]);
      if (h.track !== "captions" && !h.fixed) { c.fillStyle = "#fff"; c.strokeStyle = "rgba(0,0,0,.6)"; c.lineWidth = 1; const hs = 9; c.fillRect(h.bb.x + h.bb.w + 4 - hs / 2, h.bb.y + h.bb.h + 4 - hs / 2, hs, hs); c.strokeRect(h.bb.x + h.bb.w + 4 - hs / 2, h.bb.y + h.bb.h + 4 - hs / 2, hs, hs); }
      c.restore();
    });
  };

  /** Seleccion y arrastre directo en la vista previa: mover (x,y) y escalar (size). */
  FX.canvasPointer = function (e) {
    if (!isPro()) return false;
    if (FX.busy()) return true;
    const r = CV.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width * CW, y = (e.clientY - r.top) / r.height * CH;
    // tirador de escala del seleccionado
    const selH = HITS.find(h => SEL && SEL.type === "fx" && isSel(h.track, h.id) && !h.scene && !h.fixed);
    let mode = null, hit = null;
    if (selH && Math.abs(x - (selH.bb.x + selH.bb.w + 4)) < 12 && Math.abs(y - (selH.bb.y + selH.bb.h + 4)) < 12) { mode = "scale"; hit = selH; }
    if (!hit) {
      for (let i = HITS.length - 1; i >= 0; i--) { const h = HITS[i]; if (h.scene) continue; if (x >= h.bb.x - 6 && x <= h.bb.x + h.bb.w + 6 && y >= h.bb.y - 6 && y <= h.bb.y + h.bb.h + 6) { hit = h; break; } }
      if (!hit) { const sc = HITS.find(h => h.scene); if (sc) hit = sc; }
      mode = "move";
    }
    if (!hit) return false;
    e.preventDefault();
    if (hit.track === "captions") { SELX = []; SEL = { type: "captions" }; markSelection(); FX.inspect(SEL); }
    else if (!isSel(hit.track, hit.id)) FX.select(hit.track, hit.id);
    FX.focusElem();
    if (hit.scene || hit.fixed) return true;
    const obj = hit.track === "captions" ? PLAN.fx.captions : items(hit.track).find(i => i.id === hit.id);
    if (!obj) return true;
    const xy0 = hit.track === "captions" ? [0.5, obj.y != null ? +obj.y : 0.72] : itemXY(obj, V.currentTime || 0);
    const x0 = xy0[0], y0 = xy0[1], s0 = +(obj.size || 0.1);
    let began = false;
    V.pause();
    CV.setPointerCapture(e.pointerId);
    const bb0 = hit.bb;
    CV.onpointermove = ev => {
      const dx = (ev.clientX - e.clientX) / r.width * CW, dy = (ev.clientY - e.clientY) / r.height * CH;
      if (!began && Math.hypot(dx, dy) < 3) return;
      if (!began) { mut(); began = true; FX.hideExact(); }
      if (mode === "scale") {
        const k = clamp((bb0.w + dx) / Math.max(10, bb0.w), 0.2, 5);
        obj.size = r3(clamp(s0 * k, 0.01, 1.5));
      } else {
        if (hit.track !== "captions") {
          // iman al centro sobre la caja VISIBLE (no el ancla): un rotulo anclado a la izquierda,
          // un texto alineado o unas redes acotadas quedan centrados de verdad (simetria)
          const bcx = (bb0.x - LB.x + bb0.w / 2 + dx) / LB.w;
          const [nx, snapped] = C.snapCenterX(clamp(x0 + dx / LB.w, 0, 1), bcx);
          obj.x = r3(clamp(nx, 0, 1)); DRAG_GUIDE = snapped;
        }
        let ny = clamp(y0 + dy / LB.h, 0.02, 0.98);
        if (Math.abs(ny - 0.5) < 0.012) ny = 0.5;   // iman al centro
        obj.y = r3(ny);
      }
    };
    CV.onpointerup = () => {
      CV.onpointermove = null; CV.onpointerup = null; DRAG_GUIDE = false;
      if (began) { if (hit.track !== "captions") autoLock(obj); FX.changed({ noMut: true, rebuild: true }); if (hit.track === "captions") FX.renderProjectGroups(); }
    };
    return true;
  };

  // ------------------------------------------------------------------ cuadro exacto del servidor
  let EX = { timer: null, ctrl: null, key: "", url: null, fails: 0, until: 0, rev: 0 };
  FX.planDirty = function () { EX.rev++; FX.hideExact(); FX.scheduleExact(); FX.markDirty(); };
  /* Autoguardado (PUT /plan) con concurrencia optimista: base_rev = la revision del servidor sobre
     la que edita el Studio. Si otra ventana (o un re-dirigir) cambio el plan -> 409 y no se pisa:
     se avisa con opciones. Se vacia al salir (pagehide / Nuevo video). */
  let SAVE = { timer: null, off: false, dirty: false, inflight: null, conflict: false, hold: null };
  FX.isDirty = () => SAVE.dirty || !!SAVE.inflight;
  FX.markDirty = function () {
    if (SAVE.off || typeof JOB === "undefined" || !JOB || JOB === "demo") return;
    SAVE.dirty = true; saveHint();
    clearTimeout(SAVE.timer);
    SAVE.timer = setTimeout(() => FX.saveNow(), 1200);
  };
  FX.autosave = FX.markDirty;
  function saveHint(txt) {
    const h = $("#saveHint"); if (!h) return;
    h.textContent = txt != null ? txt : SAVE.conflict ? "Sin guardar: el proyecto cambió en otra ventana" : SAVE.dirty || SAVE.inflight ? "Guardando…" : "Guardado";
    h.classList.toggle("warn", !!SAVE.conflict);
  }
  /** Retiene el autoguardado mientras el motor escribe el plan (p. ej. POST /takes con base_rev):
      al terminar se guarda lo pendiente sobre la revision nueva en vez de chocar con ella (409). */
  FX.holdSaves = function (p) {
    SAVE.hold = p;
    p.then(() => { if (SAVE.hold === p) SAVE.hold = null; if (SAVE.dirty) FX.markDirty(); });
  };
  FX.saveNow = async function (force) {
    clearTimeout(SAVE.timer);
    if (SAVE.hold) await SAVE.hold;
    if (SAVE.off || !SAVE.dirty || typeof JOB === "undefined" || !JOB || JOB === "demo") return true;
    if (SAVE.inflight) { await SAVE.inflight; if (!SAVE.dirty) return true; }
    if (SAVE.conflict && !force) return false;
    SAVE.dirty = false; saveHint();
    const body = { plan: planToSave() };
    // el servidor ya tiene exactamente este plan (lo acaba de escribir el director o el perfil):
    // no se reenvia (su huella distingue 1.0 de 1 y subiria la revision sin cambios reales, lo que
    // rompe la sincronizacion automatica con la musica IA)
    const js = JSON.stringify(body.plan);
    if (!force && js === LAST_SAVED) { saveHint(); FX.kickAnalysis(); return true; }
    if (FX.serverRev != null && !force) body.base_rev = FX.serverRev;
    const sent = JSON.stringify(PLAN);
    let after = null;
    const run = (async () => {
      try {
        const r = await fetch(`/plan/${JOB}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        if (r.status === 404 || r.status === 405) { SAVE.off = true; return true; }
        const d = await r.json().catch(() => ({}));
        if (r.status === 409) { SAVE.dirty = true; SAVE.conflict = true; if (d.plan_rev != null) SAVE.remoteRev = d.plan_rev; conflictToast(); return false; }
        if (!r.ok) { SAVE.dirty = true; return false; }
        if (d.plan_rev != null) FX.serverRev = d.plan_rev;
        SAVE.conflict = false; SRV_PROF = !!(body.plan && body.plan.profile); LAST_SAVED = js;
        // el servidor re-aplico el perfil (rotulo, Seguir, reservas y cierre con los datos nuevos):
        // se adopta su plan si nada cambio mientras se guardaba
        if (d.profile_applied && d.plan && JSON.stringify(PLAN) === sent) after = () => FX.adoptServerPlan(d.plan, d.plan_rev);
        // la propuesta ya estaba sincronizada con la musica IA en el servidor: se adopta esa version
        else if (d.music_synced && JSON.stringify(PLAN) === sent) after = () => adoptSynced(d.plan_rev);
        // una edicion guardada sobre la propuesta sincronizada la deja `superseded`: se sondea un
        // momento para ofrecer "Sincronizar con la música"
        else if (["synced", "syncing", "ready"].includes(FX.musicSide.state) && !FX.gridSynced()) FX.watchStatus(null, 8000);
        FX.kickAnalysis();
        return true;
      } catch (e) { SAVE.dirty = true; return false; }   // sin red: se reintenta en el siguiente cambio
      finally { SAVE.inflight = null; saveHint(); if (after) setTimeout(after, 0); }
    })();
    SAVE.inflight = run;
    return run;
  };
  /** El plan que se guarda lleva SIEMPRE la clave `profile` (null = sin perfil): sin ella el servidor
      conserva el perfil guardado, y deshacer "aplicar perfil" dejaria el perfil viejo en el servidor. */
  let SRV_PROF = false;               // el plan guardado en el servidor tiene perfil
  let LAST_SAVED = null;              // JSON del plan tal como lo tiene el servidor (ultimo leido o escrito)
  /** Toma nota del plan que el servidor tiene guardado (al abrir, tras /direct, perfil, sincronia). */
  FX.noteServerPlan = p => { SRV_PROF = !!(p && p.profile); try { LAST_SAVED = p ? JSON.stringify(p) : null; } catch (e) { LAST_SAVED = null; } };
  /** El plan local es exactamente el que el servidor tiene guardado (nada editado ni pendiente). */
  FX.isClean = () => !SAVE.dirty && !SAVE.inflight && LAST_SAVED != null && JSON.stringify(planToSave()) === LAST_SAVED;
  function planToSave() { return PLAN && SRV_PROF && !PLAN.profile ? Object.assign({}, PLAN, { profile: null }) : PLAN; }
  /** Reemplaza el plan local por el que el servidor acaba de guardar (misma edicion re-aplicada),
      sin paso de deshacer y sin volver a guardarlo. Conserva traducciones y estado del Studio. */
  FX.adoptServerPlan = function (plan, rev) {
    if (!plan) return;
    takeServerPlan(plan);             // mismo filtro que tras /direct (antes de tomar nota)
    if (rev != null) FX.serverRev = rev;
    FX.afterPlanReplaced(true);
    clearTimeout(SAVE.timer); SAVE.dirty = false; saveHint();
  };
  function conflictToast() {
    toast("Este proyecto cambió en otra ventana: tus últimos cambios no se guardaron.", "alert", false, [
      ["Sobrescribir", () => { SAVE.conflict = false; FX.saveNow(true); }],
      ["Recargar", () => { location.reload(); }]], true);
  }
  /** Vaciado al salir: fetch keepalive (si cabe) para no perder los ultimos 1-2 s de edicion. */
  FX.flushOnExit = function () {
    if (!SAVE.dirty || SAVE.off || SAVE.conflict || typeof JOB === "undefined" || !JOB || JOB === "demo") return;
    const body = JSON.stringify(Object.assign({ plan: planToSave() }, FX.serverRev != null ? { base_rev: FX.serverRev } : {}));
    try { fetch(`/plan/${JOB}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body, keepalive: body.length < 60000 }); SAVE.dirty = false; } catch (e) { /* nada */ }
  };
  window.addEventListener("pagehide", () => FX.flushOnExit());
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") FX.saveNow(); });
  /** Si cambiaron los cortes o lo que necesita recorte de persona (matte), relanza el analisis de
      fondo para que el cuadro exacto y la vista previa real sigan siendo rapidos. */
  const MATTE_KINDS = new Set(["color_pop", "bg_blur", "bg_replace", "subject_outline"]);
  let ANA_SIG = null, anaTimer = null;
  function analysisSig() {
    if (!isPro()) return "";
    const r = v => Math.round(+v * 10) / 10;
    const m = items("effects").filter(e => e.enabled !== false && (MATTE_KINDS.has(e.kind) || (e.kind === "bw" && e.subject_color))).map(e => [r(e.start), r(e.end)]);
    const b = items("text").filter(x => x.enabled !== false && x.behind).map(x => [r(x.start), r(x.end)]);
    return JSON.stringify([tlObj().keeps.map(k => [r(k[0]), r(k[1])]), m, b, FMT]);
  }
  FX.resetAnalysisSig = () => { try { ANA_SIG = isPro() ? analysisSig() : null; } catch (e) { ANA_SIG = null; } };
  /** Relanza el analisis de fondo aunque la firma no cambio (p. ej. el formato por defecto del
      proyecto no es el del analisis inicial). */
  FX.forceAnalysis = function () { if (!isPro()) return; ANA_SIG = "*"; FX.kickAnalysis(); };
  FX.kickAnalysis = function () {
    const sig = analysisSig();
    if (ANA_SIG === null) { ANA_SIG = sig; return; }          // primer guardado: el servidor ya analizo este plan
    if (sig === ANA_SIG) return;
    clearTimeout(anaTimer);
    anaTimer = setTimeout(async () => {
      ANA_SIG = sig;
      try { await fetch(`/analysis/${JOB}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan: planForServer(), fmt: FMT || "9:16" }) }); }
      catch (e) { /* opcional */ }
    }, 5000);
  };
  FX.hideExact = function () { const im = $("#pvExact"); if (im) im.classList.remove("on"); setBadge(); };
  FX.scheduleExact = function (delay) {
    clearTimeout(EX.timer);
    if (!FX.exactOn || !isPro() || !V || !V.paused) { setBadge(); return; }
    EX.timer = setTimeout(requestExact, delay != null ? delay : 320);
    setBadge("wait");
  };
  async function requestExact() {
    if (!FX.exactOn || !isPro() || !V.paused || !JOB || JOB === "demo") { setBadge(); return; }
    if (Date.now() < EX.until) { setBadge("off"); return; }
    const t = V.currentTime || 0, tf = FX.finalTime(t);
    const key = `${EX.rev}|${t.toFixed(3)}|${FMT}|${LANG}`;
    if (key === EX.key && EX.url) { showExact(); return; }
    if (EX.ctrl) EX.ctrl.abort();
    const ctrl = EX.ctrl = new AbortController();
    setBadge("loading");
    try {
      const r = await fetch(`/frame/${JOB}`, { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal,
        body: JSON.stringify(Object.assign(engineOpts(), { plan: planForServer(), t_final: +tf.toFixed(4), t: +t.toFixed(4), fmt: FMT || "9:16", quality: "final", drop_stale: true })) });
      if (r.status === 204) return;          // superado por un pedido mas nuevo (drop_stale): ese llega enseguida
      if (!r.ok) { const e = new Error(String(r.status)); e.status = r.status; throw e; }
      const blob = await r.blob();
      if (ctrl !== EX.ctrl) return;
      if (EX.url) URL.revokeObjectURL(EX.url);
      EX.url = URL.createObjectURL(blob); EX.key = key; EX.fails = 0;
      const im = $("#pvExact");
      im.onload = () => { if (EX.key === key && V.paused && Math.abs((V.currentTime || 0) - t) < 1e-3) showExact(); };
      im.src = EX.url;
    } catch (e) {
      if (e.name === "AbortError") return;
      EX.fails++;
      if (e.status === 404 || e.status === 405) EX.until = Date.now() + 15000;
      else if (EX.fails > 2) EX.until = Date.now() + 5000;
      setBadge(e.status === 404 || e.status === 405 ? "off" : "err");
    } finally { if (ctrl === EX.ctrl) EX.ctrl = null; }
  }
  function showExact() { const im = $("#pvExact"); if (!im) return; FX.syncOverlayRect(); im.classList.add("on"); setBadge("exact"); FX.drawOverlayNow(); }
  FX.onSeekOrPause = () => {
    FX.hideExact(); FX.scheduleExact();
    // la musica sigue al video aunque el cuadro no se este dibujando (pestana oculta)
    const M = FX.music; if (M.audio && !M.audio.paused && !M.solo && V.paused) M.audio.pause();
  };
  FX.onPlay = () => {
    clearTimeout(EX.timer); if (EX.ctrl) EX.ctrl.abort(); FX.hideExact(); setBadge();
    FX.tick(V.currentTime || 0, true);
  };
  function setBadge(st) {
    const b = $("#pvBadge"); if (!b) return;
    b.classList.add("on"); b.classList.remove("busy", "exact", "warn");
    if (!isPro() || !FX.exactOn) { b.innerHTML = "<b>&#8226;</b> tiempo real"; return; }
    if (V && !V.paused) { b.innerHTML = "<b>&#8226;</b> en vivo (aproximado)"; return; }
    if (st === "loading" || st === "wait") { b.classList.add("busy"); b.innerHTML = `<span class="spinner"></span> cuadro exacto…`; return; }
    if (st === "exact") { b.classList.add("exact"); b.innerHTML = "<b>&#8226;</b> cuadro exacto"; return; }
    if (st === "err") { b.classList.add("warn"); b.innerHTML = "<b>&#8226;</b> sin cuadro exacto (reintento)"; return; }
    if (st === "off") { b.classList.add("warn"); b.innerHTML = "<b>&#8226;</b> cuadro exacto no disponible"; return; }
    b.innerHTML = "<b>&#8226;</b> en vivo (aproximado)";
  }
  FX.setExact = on => { FX.exactOn = on; const bt = $("#btnExact"); if (bt) bt.classList.toggle("on", on); if (on) { EX.until = 0; FX.scheduleExact(0); } else FX.hideExact(); try { localStorage.setItem("reelfy.exact", on ? "1" : "0"); } catch (e) { /* sin storage */ } };

  // ------------------------------------------------------------------ vista previa real (/preview)
  let PV = { polling: false, file: null };
  FX.realPreview = async function () {
    const m = $("#pvm"); if (!m) return;
    V.pause();
    const vid = $("#pvmVid"); vid.classList.remove("on"); vid.pause(); vid.removeAttribute("src");
    PV.file = null; const pw = $("#pvmWarn"); if (pw) pw.innerHTML = "";
    m.classList.add("on");
    $("#pvmMsg").textContent = "Preparando la vista previa real (540p con audio)…"; setBar("#pvmFill", 2); $("#pvmCancel").style.display = "";
    const body = isPro() ? Object.assign(engineOpts(), { plan: planForServer(), pro: true, fmt: FMT || "9:16", format: FMT || "9:16" })
      : Object.assign({ plan: PLAN, full: true }, typeof renderOpts === "function" ? renderOpts() : {});
    PV.t0 = Date.now(); PV.seenActive = false;
    try {
      const d = await jpost(`/preview/${JOB}`, body);
      if (d.file) { playPreview(d.file); PV.file = null; return; }
      PV.polling = true; pollPreview();
    } catch (e) {
      $("#pvmMsg").textContent = `No se pudo generar la vista previa: ${e.message || e}`; $("#pvmCancel").style.display = "none";
    }
  };
  const REFRESH_END = { done: 1, canceled: 1, timeout: 1, error: 1, superseded: 1, skipped: 1 };
  async function pollPreview() {
    if (!PV.polling) return;
    let j; try { j = await jget(`/status/${JOB}?plan=0`); } catch (e) { return setTimeout(pollPreview, 1200); }
    const pv = j.preview || {};
    const st = j.preview_state || pv.state || (j.preview_file ? "done" : "");
    const file = j.preview_file || pv.file;
    const msg = j.preview_message || pv.message;
    if (PV.file) {
      // ya se reproduce: la musica IA aun se generaba (suena la procedural) y el servidor rehace
      // la vista previa sola; al publicar el archivo nuevo se cambia conservando el instante
      const mr = pv.music_refresh;
      if (file && file !== PV.file && st === "done") {
        swapPreview(file); $("#pvmMsg").textContent = msg || "Vista previa actualizada con la música IA.";
        previewWarnings(pv);
        if (!(j.preview_music_pending || pv.music_pending)) { PV.polling = false; return; }
      } else if (mr && REFRESH_END[mr]) { PV.polling = false; $("#pvmMsg").textContent = msg || "Vista previa lista."; return; }
      else if (st === "canceled" || st === "error") { PV.polling = false; return; }
      else {
        const rp = pv.refresh_pct != null ? ` · ${Math.round(+pv.refresh_pct)}%` : "";
        $("#pvmMsg").textContent = mr === "rendering" ? `Actualizando la vista previa con la música IA${rp}…`
          : "Suena la música provisional: la vista previa se actualiza sola cuando la música IA termine de componerse.";
      }
      return setTimeout(pollPreview, 1500);
    }
    // no aceptar el resultado de una vista previa anterior
    const fresh = PV.seenActive || (pv.updated && pv.updated * 1000 >= PV.t0 - 1500);
    if (st === "queued" || st === "running") PV.seenActive = true;
    const pct = j.preview_pct != null ? +j.preview_pct : +(pv.pct || 0);
    setBar("#pvmFill", Math.max(2, pct || 0));
    $("#pvmMsg").textContent = st === "queued" ? (msg || "En cola…") : `${msg || stageLabel(j.preview_stage || pv.stage) || "Renderizando"}${pct ? ` · ${Math.round(pct)}%` : ""}`;
    if (fresh && st === "done" && file) {
      playPreview(file); previewWarnings(pv);
      if (j.preview_music_pending || pv.music_pending) {
        $("#pvmMsg").textContent = "Vista previa lista con música provisional: se actualiza sola con la música IA en cuanto termine.";
        return setTimeout(pollPreview, 1500);
      }
      PV.polling = false; return;
    }
    if (fresh && (st === "error" || st === "canceled")) {
      PV.polling = false; $("#pvmCancel").style.display = "none";
      $("#pvmMsg").textContent = st === "canceled" ? "Vista previa cancelada." : `La vista previa falló: ${j.preview_error || pv.error || ""}`;
      return;
    }
    setTimeout(pollPreview, 800);
  }
  function playPreview(file) {
    const vid = $("#pvmVid"); const url = /^\/|^https?:/.test(file) ? file : `/video/${encodeURIComponent(file)}`;
    PV.file = file;
    vid.src = url + (url.includes("?") ? "&" : "?") + "v=" + Date.now(); vid.classList.add("on");
    setBar("#pvmFill", 100); $("#pvmMsg").textContent = "Vista previa real lista: es el mismo motor del export, a 540p."; $("#pvmCancel").style.display = "none";
    vid.play().catch(() => {});
  }
  /** Cambia al archivo nuevo (musica IA) en el mismo instante y con el mismo estado de reproduccion. */
  function swapPreview(file) {
    const vid = $("#pvmVid"), t = vid.currentTime || 0, playing = !vid.paused;
    const url = /^\/|^https?:/.test(file) ? file : `/video/${encodeURIComponent(file)}`;
    PV.file = file;
    vid.addEventListener("loadedmetadata", () => { try { vid.currentTime = Math.min(t, (vid.duration || t) - 0.05); } catch (e) { /* nada */ } if (playing) vid.play().catch(() => {}); }, { once: true });
    vid.src = url + (url.includes("?") ? "&" : "?") + "v=" + Date.now();
    toast("Vista previa actualizada con la música IA.", "music");
  }
  /** Avisos del render de la vista previa (codigos -> texto humano, con enlace al elemento). */
  function previewWarnings(pv) {
    const box = $("#pvmWarn"); if (!box) return;
    FX.lastWarnings = (pv.warnings || []).concat((pv.skipped || []).map(x => `omitido: ${x}`));
    box.innerHTML = warningsHtml(FX.lastWarnings);
    wireWarnings(box, () => FX.closePreview());
  }
  /** Lista de avisos del render en lenguaje humano; los de un elemento llevan "Ver". */
  function warningsHtml(list, noGo) {
    const seen = new Set();
    return (list || []).map(x => C.humanSkip(typeof x === "string" ? x : JSON.stringify(x), PLAN)).filter(h => { if (seen.has(h.msg)) return false; seen.add(h.msg); return true; })
      .map(h => `<div class="wrow ${h.level}">${ICO("alert")}<span>${esc(h.msg)}</span>${h.id && !noGo ? `<button class="linkbtn" data-go="${esc(h.track)}:${esc(h.id)}">Ver</button>` : ""}</div>`).join("");
  }
  FX.warningsHtml = warningsHtml;
  function wireWarnings(box, before) {
    box.querySelectorAll("[data-go]").forEach(b => b.onclick = () => {
      const [track, id] = b.dataset.go.split(":");
      if (before) before();
      FX.goToItem(track, id);
    });
  }
  FX.wireWarnings = wireWarnings;
  /** Lleva el editor a un elemento: lo selecciona, mueve el playhead y abre el inspector. */
  FX.goToItem = function (track, id) {
    if (typeof backToEditor === "function" && !$("#s-editor").classList.contains("on")) backToEditor();
    const it = items(track).find(i => i.id === id); if (!it) return;
    V.pause(); V.currentTime = Math.max(0, C.itemStart(track, it) + (C.isEvent(track) ? -0.1 : 0.05));
    FX.select(track, id); FX.focusElem();
    const el = document.querySelector(`[data-fx="${track}:${CSS.escape(id)}"]`); if (el) el.scrollIntoView({ block: "nearest", inline: "center" });
  };
  FX.closePreview = function () { PV.polling = false; const m = $("#pvm"); m.classList.remove("on"); const v = $("#pvmVid"); v.pause(); };
  FX.cancelPreview = async function () { PV.polling = false; try { await fetch(`/cancel/${JOB}?only=preview`, { method: "POST" }); } catch (e) { /* nada */ } $("#pvmMsg").textContent = "Vista previa cancelada."; $("#pvmCancel").style.display = "none"; };
  function setBar(sel, pct) { const el = $(sel); if (el) el.style.width = Math.max(2, Math.min(100, pct)) + "%"; }

  // ------------------------------------------------------------------ export por etapas
  function stageLabel(s) { const f = STAGES.find(x => x[0] === s); return f ? f[1] : (s ? String(s) : ""); }
  /** Los shorts son verticales: con una salida horizontal no se piden (saldrian en 16:9). */
  FX.shortsOn = () => C.shortsFor(FMT || "9:16", typeof V !== "undefined" && V && V.videoWidth ? { w: V.videoWidth, h: V.videoHeight } : null);
  FX.shortsNote = function () {
    const hl = ((PLAN && PLAN.highlights) || []).filter(h => h && h.enabled !== false);
    if (!isPro() || !hl.length || FX.shortsOn()) return "";
    return `<div class="wrow skip">${ICO("alert")}<span>Los shorts son verticales: con el formato ${esc(FMT === "original" ? "original (horizontal)" : FMT)} no se generan. Exporta en 9:16 para tenerlos.</span></div>`;
  };
  FX.renderBody = function () {
    const base = typeof renderOpts === "function" ? renderOpts() : {};
    return Object.assign({}, base, { plan: planForServer(), fmt: FMT || "9:16", format: FMT || "9:16", shorts: FX.shortsOn() });
  };
  FX.renderStatus = function (j) {
    const box = $("#r-stages"); if (!box) return;
    let cur = j.stage || null;
    if (cur !== "shorts" && /short/i.test(j.message || "") && (cur === "mux" || cur === "done")) cur = "shorts";
    const order = STAGES.map(s => s[0]);
    const ci = order.indexOf(cur);
    box.innerHTML = isPro() || cur ? STAGES.filter(s => (s[0] !== "director" || cur === "director") && (s[0] !== "shorts" || cur === "shorts" || FX.shortsOn())).map(([k, l], i) => {
      const idx = order.indexOf(k);
      const cls = cur === k ? "cur" : (ci >= 0 && idx < ci ? "done" : "");
      return `<span class="stg ${cls}"><span class="d"></span>${l}</span>`;
    }).join("") : "";
    const w = $("#r-warn");
    if (w) w.innerHTML = FX.shortsNote() + warningsHtml(j.warnings || [], true);
  };
  FX.cancelRender = async function () {
    const b = $("#r-cancel"); if (b) { b.disabled = true; b.innerHTML = `<span class="spinner"></span> Cancelando…`; }
    window.RENDER_CANCELED = true;
    // solo el export (?only=job): la vista previa, la musica IA y el analisis de fondo siguen
    try { await fetch(`/cancel/${JOB}?only=job`, { method: "POST" }); } catch (e) { /* el poll lo resolvera */ }
  };

  // ------------------------------------------------------------------ presets de marca
  FX.gatherPreset = function () {
    if (!isPro()) return null;
    const f = PLAN.fx;
    return { style: FX.dirStyle(), intensity: FX.dirIntensity(), captions: C.clone(f.captions || {}), grade: C.clone(f.grade || {}),
      music: { source: f.music && f.music.source, mood: f.music && f.music.mood, gain_db: f.music && f.music.gain_db, duck_db: f.music && f.music.duck_db, prompt: f.music && f.music.prompt } };
  };
  FX.applyPreset = function (p) {
    if (!p || !isPro()) return;
    edit(() => {
      if (p.captions) PLAN.fx.captions = Object.assign(PLAN.fx.captions || {}, C.clone(p.captions));
      if (p.grade) PLAN.fx.grade = Object.assign(PLAN.fx.grade || {}, C.clone(p.grade));
      if (p.music) { PLAN.fx.music = PLAN.fx.music || {}; Object.keys(p.music).forEach(k => { if (p.music[k] !== undefined) PLAN.fx.music[k] = p.music[k]; }); }
      PLAN.director = PLAN.director || {};
      if (p.style) { PLAN.director.style = p.style; DSTYLE = p.style; }
      if (p.intensity != null) { PLAN.director.intensity = p.intensity; DINT = p.intensity; }
      CAPCACHE.key = "";
    }, { rebuild: true });
    FX.renderAll();
  };

  // ------------------------------------------------------------------ teclado
  FX.onKey = function (e) {
    if (!isPro()) return false;
    const meta = e.metaKey || e.ctrlKey;
    // mientras la IA dirige nada edita el plan (ni deshacer); reproducir si
    if (FX.busy()) { if (e.code === "Space" || /^Arrow|^Home$|^End$|^[,.]$/.test(e.key) && !e.altKey) return false; e.preventDefault(); return true; }
    const hasSel = SELX.length > 0;
    if ((e.key === "Delete" || e.key === "Backspace") && hasSel) { e.preventDefault(); FX.action("del"); return true; }
    if (meta && (e.key === "d" || e.key === "D") && hasSel) { e.preventDefault(); FX.action("dup"); return true; }
    if (meta && (e.key === "c" || e.key === "C") && hasSel) { e.preventDefault(); return FX.copy(); }
    if (meta && (e.key === "v" || e.key === "V") && CLIP) { e.preventDefault(); return FX.paste(); }
    if ((e.key === "l" || e.key === "L") && !meta && hasSel) { e.preventDefault(); FX.action("lock"); return true; }
    if ((e.key === "h" || e.key === "H") && !meta && hasSel) { e.preventDefault(); FX.action("en"); return true; }
    if (e.key === "Escape" && (hasSel || SEL)) { FX.select(null); if (window.inspect) inspect(null); return true; }
    if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight") && hasSel) { e.preventDefault(); FX.nudge((e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 10 : 1) / 30); return true; }
    return false;
  };

  // ------------------------------------------------------------------ aviso flotante
  let TOAST = null, toastTimer = null;
  /** Aviso flotante. actions: [[texto, fn], ...] (botones extra); sticky: no se cierra solo. */
  function toast(msg, icon, undoBtn, actions, sticky) {
    if (!TOAST) { TOAST = document.createElement("div"); TOAST.className = "toast"; TOAST.setAttribute("role", "status"); document.body.appendChild(TOAST); }
    const acts = (actions || []).slice();
    if (undoBtn) acts.unshift(["Deshacer", () => { if (window.undo) undo(); }]);
    TOAST.innerHTML = `${ICO(icon || "check")}<span>${esc(msg)}</span>` + acts.map((a, i) => `<button data-i="${i}">${esc(a[0])}</button>`).join("")
      + (sticky ? `<button class="tx" title="Cerrar">${ICO("x")}</button>` : "");
    TOAST.querySelectorAll("button[data-i]").forEach(b => b.onclick = () => { TOAST.classList.remove("on"); acts[+b.dataset.i][1](); });
    const x = TOAST.querySelector(".tx"); if (x) x.onclick = () => TOAST.classList.remove("on");
    TOAST.classList.add("on"); clearTimeout(toastTimer);
    if (!sticky) toastTimer = setTimeout(() => TOAST.classList.remove("on"), acts.length ? 5200 : 3600);
  }
  FX.toast = toast;

  // ------------------------------------------------------------------ limpieza de tomas (Guion)
  /* Modo "Limpieza de tomas": el guion completo con lo que se quita tachado (tomas repetidas,
     arranques en falso, correcciones, tartamudeos, errores y, si se piden, silencios), un mapa de
     los cortes en todo el video y el "Resumen de la IA" con el nivel de limpieza. Un clic en un
     corte lo SELECCIONA (nunca lo cambia); restaurar o quitar es explicito (boton o R) y se
     deshace con ⌘Z. Todo se guarda por la via de siempre (PUT /plan con base_rev). El guion de
     un video largo (2500+ palabras, 250+ cortes) se arma como un solo HTML y los parrafos fuera
     de pantalla no se maquetan (content-visibility); un cambio suelto re-dibuja solo su parrafo. */
  const KRGB = { retoma: "182,156,255", arranque_fallido: "255,138,196", autocorreccion: "108,180,255", tartamudeo: "63,214,194",
    error: "255,128,112", digresion: "245,196,81", silencio: "163,172,189", manual: "236,160,120", ai: "196,181,253" };
  const CAT_ORDER = ["silencio", "retoma", "arranque_fallido", "autocorreccion", "tartamudeo", "error", "digresion", "manual", "ai"];
  const WHY = {
    retoma: "Dijiste esta idea más de una vez; queda la última toma, la más completa.",
    arranque_fallido: "Empezaste la frase y la retomaste enseguida.",
    autocorreccion: "Te corregiste; queda la versión corregida.",
    tartamudeo: "Palabras repetidas sin querer.",
    error: "Un error o una expresión que no va en el video.",
    digresion: "Esta parte se sale del tema del video.",
    silencio: "Pausa sin voz.", manual: "Corte que hiciste a mano.", ai: "Recorte propuesto por la IA.",
  };
  const CL = FX.clean = { mode: "edit", filter: "todos", sil: false, sel: -1, busy: null, listenRange: null, userScroll: 0 };
  let GW = { words: [], starts: [], owner: [], removed: [], spans: {}, wordless: {}, groups: {}, ord: {}, kept: {}, paras: [], pstart: [], secs: [], sig: "", live: -1 };
  let CL_REQ = 0;
  CL.rgb = k => KRGB[k] || KRGB.silencio;
  const cleanOn = () => CL.mode === "clean" && !!PLAN && !!$("#s-editor") && $("#s-editor").classList.contains("on");
  const lsGet = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* sin storage */ } };
  const fmtM = s => (typeof fmtT === "function" ? fmtT(s) : C.fmtClock(s));

  /** Palabras, dueños de cada palabra, grupos, parrafos y secciones del plan actual. */
  function prep() {
    const cuts = PLAN.cuts || [];
    const words = C.planWords(PLAN.phrases);
    const { owner, removed } = C.wordsInCuts(words, cuts);
    const spans = C.cutSpans(owner);
    const { groups, ord } = C.takeGroups(cuts);
    const secs = C.cleanSections(PLAN.takes && PLAN.takes.sections);
    const paras = C.scriptParagraphs(words, owner, cuts, secs);
    GW = { words, starts: words.map(w => w.start), owner, removed, spans, wordless: C.wordlessAt(words, cuts, spans), groups, ord,
      kept: C.keptMarks(words, cuts, groups, owner), paras, pstart: paras.map(p => p.w0), secs,
      sig: `${words.length}|${paras.map(p => p.w0).join(",")}|${secs.length}|${CL.sil ? 1 : 0}|${CL.filter}`, live: -1 };
  }
  const paraOf = w => { let lo = 0, hi = GW.pstart.length - 1; while (lo < hi) { const m = (lo + hi + 1) >> 1; if (GW.pstart[m] <= w) lo = m; else hi = m - 1; } return lo; };
  const inFilter = ci => { const c = PLAN.cuts[ci]; return CL.filter === "todos" || C.cutKind(c) === CL.filter; };
  const reviewList = () => C.reviewItems(PLAN.cuts, CL.filter, CL.sil);

  // ---- guion
  function wordHtml(i, cls) { return `<span class="gw${cls || ""}" data-i="${i}">${esc(GW.words[i].text)}</span>`; }
  function tagOf(ci) {
    const c = PLAN.cuts[ci], k = C.cutKind(c), take = C.isTakeCut(c);
    if (C.cutOn(c)) return C.cutTag(c, GW.ord[ci]);
    return take && !c.user ? `¿Quitar? · ${C.TAKE_LABEL[k]}` : "Conservado";
  }
  function cutCls(ci) {
    const c = PLAN.cuts[ci], take = C.isTakeCut(c), on = C.cutOn(c);
    return `k-${C.cutKind(c)}${on ? " on" : take && !c.user ? " sug" : " kept"}${inFilter(ci) ? "" : " dim"}${ci === CL.sel ? " sel" : ""}`;
  }
  function chipHtml(ci) {
    const c = PLAN.cuts[ci], k = C.cutKind(c);
    if (k === "silencio" && !CL.sil) return "";
    const lbl = k === "silencio" ? "" : `${esc(C.TAKE_LABEL[k])} · `;
    return `<span class="gsil ${cutCls(ci)}" data-c="${ci}" style="--k:${CL.rgb(k)}" title="${esc(C.TAKE_LABEL[k])} de ${C.fmtLen(c.end - c.start)}">`
      + `<svg class="ic" viewBox="0 0 24 24"><use href="#i-${k === "silencio" ? "pausebar" : "scissors"}"/></svg>${lbl}${C.fmtLen(c.end - c.start)}</span> `;
  }
  function chipsAt(i) { return (GW.wordless[i] || []).map(chipHtml).join(""); }
  function keptHtml() { return `<span class="gkept">${ICO("check")}Toma elegida</span>`; }
  function paraHtml(k) {
    const p = GW.paras[k], ws = GW.words, tl = tlObj();
    let h = "", i = p.w0;
    while (i < p.w1) {
      h += chipsAt(i);
      if (GW.kept[i] != null) h += keptHtml();
      const ci = GW.owner[i];
      if (ci < 0) { h += wordHtml(i) + " "; i++; continue; }
      // palabras seguidas del mismo corte = un solo tramo tachado (los silencios de adentro no se muestran)
      let j = i, inner = [];
      while (j + 1 < p.w1 && GW.owner[j + 1] === ci) { j++; (GW.wordless[j] || []).forEach(x => { if (C.cutKind(PLAN.cuts[x]) !== "silencio") inner.push(x); }); }
      const c = PLAN.cuts[ci], on = C.cutOn(c);
      let wh = "";
      for (let w = i; w <= j; w++) wh += wordHtml(w, !on && GW.removed[w] ? " rm" : "") + (w < j ? " " : "");
      h += `<span class="gcut ${cutCls(ci)}" data-c="${ci}" style="--k:${CL.rgb(C.cutKind(c))}"><span class="gtag">${esc(tagOf(ci))}</span>${wh}</span> `
        + inner.map(chipHtml).join("");
      i = j + 1;
    }
    if (k === GW.paras.length - 1) h += chipsAt(ws.length);
    // tiempo FINAL de la primera palabra que se queda; un parrafo cortado entero no sale en el video
    // (su tiempo final seria el del parrafo siguiente, repetido): marca atenuada con el original
    let kw = p.w0; while (kw < p.w1 && GW.removed[kw]) kw++;
    const ts = kw < p.w1 ? `<span class="gts">${fmtM(tl.remapClamped(ws[kw].start))}</span>`
      : `<span class="gts" style="opacity:.5" title="Se corta entero (en el original: ${fmtM(ws[p.w0].start)})">—</span>`;
    return `<div class="gpara" data-p="${k}">${ts}<div class="gtx">${h}</div></div>`;
  }
  function chapHtml(si) {
    const s = GW.secs[si];
    return `<div class="gchap">${ICO("book")}<span class="gct">${si + 1} · ${esc(s.title || "Sección")}</span><span class="ln"></span><span class="t">${fmtM(tlObj().remapClamped(s.start))}</span></div>`;
  }
  function emptyHtml() {
    if (CL.busy && CL.busy.first) return `<div class="gempty"><span class="spinner"></span><div><b>Buscando tomas repetidas…</b><span>Leo el guion completo y comparo cada frase con las que siguen. En un video largo puede tardar hasta un minuto; puedes seguir revisando mientras tanto.</span></div></div>`;
    if (PLAN.takes) return "";
    return `<div class="gempty">${ICO("scissors")}<div><b>Buscar tomas repetidas</b><span>Este proyecto se analizó antes de la limpieza de tomas. La IA puede encontrar las frases que repetiste, los arranques en falso y las correcciones para quitarlas; todo queda para que lo revises aquí.</span></div>`
      + `<button class="btn mini" data-a="find">${ICO("spark")}Buscar tomas repetidas</button></div>`;
  }
  function renderScript() {
    const tr = $("#gTr"); if (!tr) return;
    const top = tr.scrollTop;
    let h = emptyHtml();
    if (!GW.words.length) h += `<div class="gnone">No encontré voz en este video. Puedes recortar a mano en Edición.</div>`;
    let sec = -1;
    GW.paras.forEach((p, k) => {
      if (p.sec >= 0 && p.sec !== sec) { sec = p.sec; h += chapHtml(sec); }
      h += paraHtml(k);
    });
    tr.innerHTML = h;
    tr.scrollTop = top;
    GW.live = -1;
  }
  /** Re-dibuja solo los parrafos de un corte (y el de su "Toma elegida"). */
  function renderParasOf(ci) {
    const tr = $("#gTr"); if (!tr) return false;
    const ks = new Set(), sp = GW.spans[ci];
    if (sp) { for (let k = paraOf(sp[0]); k <= paraOf(sp[1]); k++) ks.add(k); }
    else Object.keys(GW.wordless).forEach(w => { if (GW.wordless[w].includes(ci)) ks.add(paraOf(Math.min(+w, GW.words.length - 1))); });
    // su "Toma elegida" aparece o se va segun queden cortes habilitados en el grupo
    const G = GW.ord[ci] && GW.groups[GW.ord[ci].group];
    if (G && G.keep_ref) { let lo = 0, hi = GW.starts.length; while (lo < hi) { const m = (lo + hi) >> 1; if (GW.starts[m] < G.keep_ref.start - 0.08) lo = m + 1; else hi = m; } if (lo < GW.words.length) ks.add(paraOf(lo)); }
    if (!ks.size) return false;
    for (const k of ks) { const el = tr.querySelector(`.gpara[data-p="${k}"]`); if (!el) return false; el.outerHTML = paraHtml(k); }
    return true;
  }

  // ---- chips, pie, mapa y resumen
  function renderChips() {
    const box = $("#gChips"); if (!box) return;
    const cnt = C.kindCounts(PLAN.cuts);
    const kinds = ["retoma", "arranque_fallido", "autocorreccion", "tartamudeo", "error"].concat(cnt.digresion ? ["digresion"] : []);
    const chip = (v, label, n) => `<button class="gchip${CL.filter === v ? " on" : ""}" data-f="${v}" aria-pressed="${CL.filter === v}"${v !== "todos" ? ` style="--k:${CL.rgb(v)}"` : ""}${n || v === "todos" ? "" : " disabled"}>`
      + `${v !== "todos" ? '<span class="d"></span>' : ""}${label} <b>${n}</b></button>`;
    box.innerHTML = chip("todos", "Todos", C.reviewItems(PLAN.cuts, "todos", CL.sil).length) + kinds.map(k => chip(k, C.TAKE_CHIP[k], cnt[k] || 0)).join("");
  }
  function renderFoot() {
    const it = reviewList(), k = it.indexOf(CL.sel), pos = $("#gPos");
    if (pos) pos.textContent = !it.length ? "Sin cortes que revisar" : k >= 0 ? `Corte ${k + 1} de ${it.length}` : `${it.length} corte${it.length === 1 ? "" : "s"}`;
    ["#gPrev", "#gNext"].forEach(q => { const b = $(q); if (b) b.disabled = !it.length; });
    const sil = $("#gSil"); if (sil) { sil.setAttribute("aria-checked", String(CL.sil)); sil.classList.toggle("on", CL.sil); }
    const ra = $("#gRestAll");
    if (ra) { const anyOn = it.some(ci => C.cutOn(PLAN.cuts[ci])); ra.textContent = anyOn || !it.length ? "Restaurar todo…" : "Quitar todo…"; ra.disabled = !it.length; ra.dataset.on = anyOn ? "1" : "0"; }
  }
  function drawMap() {
    const cv = $("#cmapCv"), bar = $("#cmapBar"); if (!cv || !bar || !PLAN) return;
    const W = bar.clientWidth, H = bar.clientHeight; if (!W || !H) return;
    const dpr = window.devicePixelRatio || 1, d = dur();
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
    const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
    const cs = (PLAN.cuts || []).map((c, i) => [c, i]).filter(x => x[0] && +x[0].end > +x[0].start);
    // silencios abajo, tomas encima (se ven aunque se solapen)
    cs.sort((a, b) => (C.isTakeCut(a[0]) ? 1 : 0) - (C.isTakeCut(b[0]) ? 1 : 0) || a[0].start - b[0].start);
    cs.forEach(([c, i]) => {
      const k = C.cutKind(c), x = +c.start / d * W, w = Math.max(1.5, (c.end - c.start) / d * W);
      if (C.cutOn(c)) { g.fillStyle = `rgba(${CL.rgb(k)},${k === "silencio" ? 0.55 : 1})`; g.fillRect(x, 4, w, H - 8); }
      else if (C.isTakeCut(c)) { g.strokeStyle = `rgba(${CL.rgb(k)},.6)`; g.lineWidth = 1; g.strokeRect(x + 0.5, 5.5, Math.max(0.5, w - 1), H - 11); }
      if (i === CL.sel) { g.strokeStyle = "#e9edf4"; g.lineWidth = 1.5; g.strokeRect(x - 1.5, 1.5, w + 3, H - 3); }
    });
    g.fillStyle = "rgba(236,239,245,.28)";
    GW.secs.forEach((s, k) => { if (k) g.fillRect(Math.round(s.start / d * W), 0, 1, H); });
    const ch = $("#cmapCh");
    if (ch) {
      let last = -1e9, h = "";
      GW.secs.forEach(s => {
        const x = s.start / d * W, lw = Math.min(160, 12 + String(s.title).length * 6.4);
        const right = x + lw > W;
        if ((right ? W - lw : x) < last + 8) return;
        h += `<span style="${right ? "right:0" : `left:${x.toFixed(1)}px`};max-width:${Math.round(lw)}px">${esc(s.title || "Sección")}</span>`;
        last = right ? W : x + lw;
      });
      ch.innerHTML = h; ch.style.display = GW.secs.length ? "" : "none";
    }
    const md = $("#cmapDur"); if (md) md.textContent = `${fmtM(d)} original`;
  }
  function renderSum() {
    const box = $("#cSum"); if (!box || !PLAN) return;
    const tk = PLAN.takes || null, d = dur(), fin = tlObj().finalDuration, rem = C.removedByCategory(PLAN.cuts, d);
    const topic = (tk && tk.topic) || PLAN.topic || null;
    const cur = (tk && tk.sensitivity) || "normal";
    let h = `<div class="cs-h"><h3>${ICO("spark")}Resumen de la IA</h3><div class="cs-tot"><span>${fmtM(d)}</span>${ICO("right")}<b>${fmtM(fin)}</b><em>−${fmtM(Math.max(0, d - fin))}</em></div></div>`;
    h += `<p class="cs-topic">${topic ? `<b>Tema:</b> ${esc(topic)}` : `<span>La IA todavía no detectó el tema de este video.</span>`}</p>`;
    if (tk && tk.summary) h += `<p class="cs-sum">${esc(tk.summary)}</p>`;
    if (tk && tk.llm === false) h += `<div class="cs-deg">${ICO("alert")}<span>La IA de lenguaje no respondió: busqué las tomas solo con reglas.</span><button class="btn ghost mini" data-a="retry"${CL.busy ? " disabled" : ""}>Reintentar con IA</button></div>`;
    const cats = CAT_ORDER.filter(k => rem.cats[k] > 0.05);
    if (cats.length) {
      h += `<div class="cs-stack" role="img" aria-label="Tiempo quitado por categoría">${cats.map(k => `<i style="--k:${CL.rgb(k)};flex-grow:${rem.cats[k]}" title="${esc(C.TAKE_PLURAL[k])}: ${C.fmtLen(rem.cats[k])}"></i>`).join("")}</div>`;
      h += `<div class="cs-leg">${cats.map(k => `<span><i style="--k:${CL.rgb(k)}"></i>${esc(C.TAKE_PLURAL[k])} <b>${C.fmtLen(rem.cats[k])}</b></span>`).join("")}</div>`;
    } else h += `<p class="cs-none">Todavía no se quita nada del video.</p>`;
    if (tk) {
      const busy = CL.busy && !CL.busy.first;
      h += `<div class="cs-lvh"><h4>Nivel de limpieza</h4><span>${busy ? `<span class="spinner"></span> La IA está recalculando…` : "Lo que tocaste a mano se respeta"}</span></div>`;
      h += `<div class="cs-lv" role="radiogroup" aria-label="Nivel de limpieza">` + ["suave", "normal", "a_fondo"].map(lv =>
        `<button role="radio" aria-checked="${cur === lv}" class="${cur === lv ? "on" : ""}" data-lv="${lv}"${CL.busy ? " disabled" : ""}>${C.LEVEL_LABEL[lv]}<small>queda ${fmtM(C.levelFinal(PLAN, lv, 30))}</small></button>`).join("") + `</div>`;
    }
    const st = C.takeStats(PLAN.cuts), cnt = C.kindCounts(PLAN.cuts);
    const tile = (k, n, full) => {
      const on = C.kindOn(PLAN.cuts, k);
      return `<div class="cs-cat${on ? "" : " off"}${full ? " full" : ""}" style="--k:${CL.rgb(k)}"><i></i><div><b>${esc(C.TAKE_PLURAL[k])}</b><span>${n} · ${fmtM(rem.cats[k] || 0)}</span></div>`
        + `<button class="tgl${on ? " on" : ""}" role="switch" aria-checked="${on}" aria-label="${esc(C.TAKE_PLURAL[k])}" data-kind="${k}"${n ? "" : " disabled"}></button></div>`;
    };
    h += `<div class="cs-cats">` + (tk ? C.TAKE_KINDS.map(k => tile(k, (st.by_kind[k] || {}).n || 0)).join("") : "") + tile("silencio", cnt.silencio || 0, true) + `</div>`;
    box.innerHTML = h;
  }
  /** Contador de la pestaña y "Duración final" de la barra de modos (en los dos modos). */
  CL.chrome = function () {
    if (!PLAN) return;
    const n = (PLAN.cuts || []).filter(C.isTakeCut).length, b = $("#cleanCnt");
    if (b) { b.textContent = n ? String(n) : ""; b.classList.toggle("on", n > 0); }
    const f = $("#finLbl"); if (f) f.innerHTML = `Duración final <b>${fmtM(tlObj().finalDuration)}</b> de ${fmtM(dur())}`;
  };

  // ---- seleccion y popover
  function markSel() {
    const tr = $("#gTr"); if (!tr) return;
    tr.querySelectorAll("[data-c].sel").forEach(x => x.classList.remove("sel"));
    if (CL.sel >= 0) tr.querySelectorAll(`[data-c="${CL.sel}"]`).forEach(x => x.classList.add("sel"));
  }
  function popHtml(ci) {
    const c = PLAN.cuts[ci], k = C.cutKind(c), take = C.isTakeCut(c), on = C.cutOn(c), len = +c.end - +c.start;
    const lvl = take ? C.takeLevel(c) : null, bars = take ? C.confBars(c) : 0;
    const tl = tlObj(), why = C.takeReason(c, t => tl.remapClamped(t), fmtM) || WHY[k] || WHY.silencio;
    const kept = take && c.kept_text ? `<p class="gp-kept">Se queda: «${esc(String(c.kept_text).slice(0, 90))}${String(c.kept_text).length > 90 ? "…" : ""}»</p>` : "";
    const state = on ? `Se quitan ${C.fmtLen(len)}` : take && !c.user ? `Sugerencia: quitaría ${C.fmtLen(len)}` : `Se conservan ${C.fmtLen(len)}`;
    return `<div class="gph"><span class="gptag">${ICO(take ? "restore" : k === "silencio" ? "pausebar" : "scissors")}${esc(C.cutTag(c, GW.ord[ci], true))}</span>`
      + (take ? `<span class="gconf">Seguridad ${lvl} <i aria-hidden="true">${[1, 2, 3, 4].map(b => `<b${b > bars ? ' class="o"' : ""}></b>`).join("")}</i></span>` : "") + `</div>`
      + `<p>${esc(why)}</p>${kept}<div class="gpm">${fmtM(tlObj().remapClamped(+c.start))} · ${state}</div>`
      + `<div class="pair"><button class="btn ghost mini" data-a="listen">${ICO("play")}Escuchar cómo queda</button>`
      + `<button class="btn ghost mini" data-a="toggle">${ICO(on ? "restore" : "scissors")}${on ? "Restaurar" : "Quitar"}</button></div>`
      + `<div class="gkeys"><span><kbd>Espacio</kbd> escuchar</span><span><kbd>R</kbd> ${on ? "restaurar" : "quitar"}</span><span><kbd>↑</kbd><kbd>↓</kbd> otro corte</span></div>`;
  }
  function renderPop() {
    const tr = $("#gTr"); if (!tr) return;
    let pop = $("#gPop");
    if (CL.sel < 0 || !PLAN.cuts[CL.sel] || !tr.querySelector(`[data-c="${CL.sel}"]`)) { if (pop) pop.remove(); return; }
    if (!pop) { pop = document.createElement("div"); pop.id = "gPop"; pop.className = "gpop"; pop.setAttribute("role", "dialog"); }
    if (pop.parentNode !== tr) tr.appendChild(pop);
    const c = PLAN.cuts[CL.sel];
    pop.style.setProperty("--k", CL.rgb(C.cutKind(c)));
    pop.setAttribute("aria-label", C.TAKE_LABEL[C.cutKind(c)]);
    pop.innerHTML = popHtml(CL.sel);
    placePop();
  }
  /** Popover bajo la ultima linea del corte (o encima si abajo no cabe en lo visible). */
  function placePop() {
    const tr = $("#gTr"), pop = $("#gPop"); if (!tr || !pop || CL.sel < 0) return;
    const el = tr.querySelector(`[data-c="${CL.sel}"]`); if (!el) return;
    const rs = el.getClientRects(), tb = tr.getBoundingClientRect();
    const first = rs[0] || el.getBoundingClientRect(), last = rs[rs.length - 1] || first;
    const pw = Math.min(360, tr.clientWidth - 24);
    pop.style.width = pw + "px";
    const x0 = (rs.length > 1 ? last.left : first.left) - tb.left;
    const left = Math.max(12, Math.min(tr.clientWidth - pw - 12, x0));
    const ph = pop.offsetHeight;
    const below = last.bottom - tb.top + tr.scrollTop + 10, above = first.top - tb.top + tr.scrollTop - 10 - ph;
    const up = below + ph > tr.scrollTop + tr.clientHeight && above >= tr.scrollTop;
    pop.style.left = left + "px"; pop.style.top = (up ? above : below) + "px";
    pop.classList.toggle("up", up);
    pop.style.setProperty("--ax", Math.max(14, Math.min(pw - 26, x0 - left + 14)) + "px");
  }
  function select(ci, o) {
    const opt = o || {};
    CL.sel = ci >= 0 && PLAN.cuts[ci] ? ci : -1;
    markSel(); renderPop(); renderFoot(); drawMap();
    if (CL.sel < 0) return;
    const c = PLAN.cuts[CL.sel];
    if (opt.scroll) scrollToSel();
    else { const tr = $("#gTr"), el = tr.querySelector(`[data-c="${CL.sel}"]`); if (el) revealPop(el.getBoundingClientRect().top - tr.getBoundingClientRect().top + tr.scrollTop); }
    if (opt.seek && V.paused) V.currentTime = +c.start + 0.001;
  }
  CL.select = select;
  /** Lleva el corte al tercio superior del guion (flechas) y deja ver su popover. */
  function scrollToSel() {
    const tr = $("#gTr"), el = tr && tr.querySelector(`[data-c="${CL.sel}"]`); if (!el) return;
    const tb = tr.getBoundingClientRect(), r = el.getBoundingClientRect(), y = r.top - tb.top + tr.scrollTop;
    if (y < tr.scrollTop + 8 || r.bottom - tb.top > tr.clientHeight * 0.6) tr.scrollTop = Math.max(0, y - tr.clientHeight / 4);
    placePop(); revealPop(y);
  }
  /** Si el popover queda cortado abajo, desplaza lo justo (sin sacar de la vista el corte). */
  function revealPop(y) {
    const tr = $("#gTr"), pop = $("#gPop"); if (!tr || !pop || pop.classList.contains("up")) return;
    const pb = pop.offsetTop + pop.offsetHeight + 12;
    if (pb > tr.scrollTop + tr.clientHeight) { tr.scrollTop = Math.max(tr.scrollTop, Math.min(y - 8, pb - tr.clientHeight)); placePop(); }
  }
  function step(dir) {
    const it = reviewList(); if (!it.length) return;
    let k = it.indexOf(CL.sel);
    if (k < 0) {
      const t = V.currentTime || 0;
      if (dir > 0) { k = it.findIndex(ci => +PLAN.cuts[ci].start >= t - 0.01); if (k < 0) k = 0; }
      else { k = -1; it.forEach((ci, j) => { if (+PLAN.cuts[ci].start < t - 0.01) k = j; }); if (k < 0) k = it.length - 1; }
    } else k = Math.max(0, Math.min(it.length - 1, k + dir));
    select(it[k], { scroll: true, seek: true });
  }
  CL.step = step;
  /** Palabras del corte para los avisos: «las primeras cinco…» (o su duracion si no tiene). */
  const NOUN = { retoma: "la toma repetida", arranque_fallido: "el arranque en falso", autocorreccion: "la corrección",
    tartamudeo: "el tartamudeo", error: "el error", digresion: "la parte fuera de tema", silencio: "el silencio",
    manual: "el corte manual", ai: "el recorte de la IA" };
  function excerpt(ci) {
    const sp = GW.spans[ci], c = PLAN.cuts[ci];
    if (!sp) return `${NOUN[C.cutKind(c)] || "el corte"} de ${C.fmtLen(c.end - c.start)}`;
    const ws = GW.words.slice(sp[0], Math.min(sp[1] + 1, sp[0] + 5)).map(w => w.text).join(" ");
    return `«${ws}${sp[1] - sp[0] >= 5 ? "…" : ""}»`;
  }

  // ---- acciones
  /** Restaura o vuelve a cortar (un paso de deshacer; las tomas quedan user:true). */
  CL.toggle = function (ci) {
    const c = PLAN && PLAN.cuts && PLAN.cuts[ci]; if (!c || FX.busy()) return;
    mut();
    const on = C.toggleCut(c);
    if (typeof afterCutsChange === "function") afterCutsChange(ci);
    if (CL.mode !== "clean") prep();                 // el aviso cita las palabras del corte
    toast(`${on ? "Quitaste" : "Restauraste"} ${excerpt(ci)} (${fmtM(tlObj().remapClamped(+c.start))}).`, on ? "scissors" : "restore", true);
  };
  /** "Escuchar cómo queda": 2 s antes y 2 s despues del corte, saltando los cortes habilitados. */
  CL.listen = function (ci) {
    const c = PLAN.cuts[ci]; if (!c) return;
    if (CL.listenRange && !V.paused) { V.pause(); CL.listenRange = null; return; }
    if (SEL && SEL.type === "cut" && window.inspect) inspect(null);     // un corte seleccionado se reproduciria
    const [a, b] = C.listenRange(tlObj(), c, 2);
    CL.listenRange = { end: b };
    V.currentTime = a + 0.001;
    if (window.ensureAudio) ensureAudio();
    if (typeof AC !== "undefined" && AC) AC.resume();
    V.play().catch(() => {});
  };
  CL.setFilter = function (f) {
    CL.filter = f && f !== CL.filter ? f : "todos";
    if (CL.sel >= 0 && !inFilter(CL.sel)) CL.sel = -1;
    CL.render();
  };
  CL.setSil = function (on) {
    CL.sil = !!on; lsSet("reelfy.clean.sil", CL.sil ? "1" : "0");
    if (CL.sel >= 0 && C.cutKind(PLAN.cuts[CL.sel]) === "silencio" && !GW.spans[CL.sel] && !CL.sil) CL.sel = -1;
    CL.render();
  };
  function confirmBox(html, onOk, okLabel) {
    const box = $("#gConfirm"); if (!box) return;
    box.innerHTML = `<p>${html}</p><div class="pair"><button class="btn ghost mini" data-a="no">Cancelar</button><button class="btn mini" data-a="ok">${esc(okLabel)}</button></div>`;
    box.classList.add("on");
    const close = () => { box.classList.remove("on"); box.innerHTML = ""; document.removeEventListener("keydown", esc_, true); };
    const esc_ = e => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
    document.addEventListener("keydown", esc_, true);
    box.querySelector('[data-a="no"]').onclick = close;
    box.querySelector('[data-a="ok"]').onclick = () => { close(); onOk(); };
    box.querySelector('[data-a="ok"]').focus();
  }
  CL.restoreAll = function () {
    const it = reviewList(); if (!it.length || FX.busy()) return;
    const on = it.some(ci => C.cutOn(PLAN.cuts[ci]));
    const n = it.filter(ci => C.cutOn(PLAN.cuts[ci]) === on).length;
    const what = CL.filter === "todos" ? (CL.sil ? "cortes (con los silencios)" : "cortes de limpieza") : C.TAKE_PLURAL[CL.filter].toLowerCase();
    confirmBox(on ? `¿Restaurar ${n} ${what}? Todo vuelve a salir en el video. Puedes deshacerlo con ⌘Z.`
      : `¿Quitar ${n} ${what}? Se cortan del video. Puedes deshacerlo con ⌘Z.`, () => {
      mut();
      const k = C.setCutsEnabled(PLAN.cuts, it, !on);
      if (typeof afterCutsChange === "function") afterCutsChange();
      toast(`${on ? "Restauraste" : "Quitaste"} ${k} corte${k === 1 ? "" : "s"}.`, on ? "restore" : "scissors", true);
    }, on ? "Restaurar" : "Quitar");
  };
  CL.setKind = function (kind, on) {
    if (FX.busy()) return;
    mut();
    const n = C.setKindEnabled(PLAN.cuts, kind, on, (PLAN.takes && PLAN.takes.sensitivity) || "normal");
    if (typeof afterCutsChange === "function") afterCutsChange();
    toast(`${C.TAKE_PLURAL[kind]}: ${on ? "se cortan" : "se conservan"}${n ? ` (${n})` : ""}.`, on ? "scissors" : "restore", true);
  };
  /** Nivel de limpieza: se aplica al instante con lo detectado (lo que el usuario toco no cambia)
      y el motor re-detecta con ese nivel (POST /takes) sin otro paso de deshacer. */
  CL.setLevel = function (lv) {
    if (!PLAN.takes || CL.busy || FX.busy()) return;
    const cur = PLAN.takes.sensitivity || "normal"; if (lv === cur) return;
    const before = tlObj().finalDuration;
    mut();
    PLAN.cuts = C.applyLevel(PLAN.cuts, lv);
    PLAN.takes = Object.assign({}, PLAN.takes, { sensitivity: lv });
    CL.sel = -1;
    if (typeof afterCutsChange === "function") afterCutsChange();
    const after = tlObj().finalDuration, dd = after - before;
    toast(`Nivel de limpieza: ${C.LEVEL_LABEL[lv]}. Queda ${fmtM(after)}${Math.abs(dd) >= 0.5 ? ` (${dd < 0 ? "−" : "+"}${fmtM(Math.abs(dd))})` : ""}.`, "check", true);
    if (JOB && JOB !== "demo") CL.findTakes(lv, { llm: PLAN.takes.llm !== false, quiet: true });
  };
  /** POST /takes: detecta (o re-detecta) las tomas del proyecto con base_rev. Las tomas que el
      usuario toco (user:true) se conservan en el servidor y aqui. `first`: busqueda inicial. */
  CL.findTakes = async function (level, o) {
    const opt = o || {};
    if (CL.busy || !JOB || JOB === "demo") return;
    const lv = level || (PLAN.takes && PLAN.takes.sensitivity) || "normal";
    const req = ++CL_REQ, first = !PLAN.takes;
    CL.busy = { level: lv, first }; CL.render();
    let release = null;
    try {
      if (!(await FX.saveNow()) && FX.isDirty()) { toast("No se pudieron guardar tus últimos cambios; intenta de nuevo en un momento.", "alert"); return; }
      // mientras el motor trabaja, el autoguardado espera (si no, chocaria con la revision nueva)
      FX.holdSaves(new Promise(r => { release = r; }));
      const r = await fetch(`/takes/${JOB}`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sensitivity: lv, use_llm: opt.llm !== false, base_rev: FX.serverRev != null ? FX.serverRev : null,
          fmt: (typeof FMT !== "undefined" && FMT) || "9:16" }) });
      let d = null; try { d = await r.json(); } catch (e) { d = null; }
      d = d || {};
      if (r.status === 409) { toast("Este proyecto cambió en otra ventana: recárgalo para recalcular la limpieza.", "alert", false, [["Recargar", () => location.reload()]], true); return; }
      if (r.status === 405 || (r.status === 404 && /not found/i.test(d.detail || ""))) { toast("Esta versión del motor todavía no busca tomas repetidas.", "alert"); return; }
      if (!r.ok) throw new Error(d.detail || `Error ${r.status}`);
      if (d.plan_rev != null) FX.serverRev = d.plan_rev;
      // se deshizo o se cambio de nivel mientras tanto: el plan local manda (se guarda encima)
      if (req !== CL_REQ || (!first && (!PLAN.takes || (PLAN.takes.sensitivity || "normal") !== lv))) { FX.markDirty(); return; }
      const before = tlObj().finalDuration;
      if (first) mut();
      adoptTakes(d);
      if (typeof afterCutsChange === "function") afterCutsChange();
      const after = tlObj().finalDuration, n = (d.cuts || []).length;
      if (first) {
        toast(n ? `Encontré ${n} toma${n === 1 ? "" : "s"} para quitar. Queda ${fmtM(after)}.` : "No encontré tomas repetidas en este video.", "spark", !!n);
        if (n && CL.mode !== "clean") CL.setMode("clean");
      } else if (Math.abs(after - before) >= 0.5) toast(`La IA ajustó la limpieza: queda ${fmtM(after)}.`, "spark");
    } catch (e) {
      toast((first ? "No se pudieron buscar las tomas" : "No se pudo recalcular la limpieza") + ": " + (e.message || e), "alert");
    } finally {
      if (release) release();
      if (req === CL_REQ) CL.busy = null;
      CL.render();
    }
  };
  /** Toma las tomas del servidor: reemplaza las src "takes" (las que el usuario toco aqui ganan). */
  function adoptTakes(d) {
    PLAN.cuts = C.adoptTakeCuts(PLAN.cuts, d.cuts);
    if (d.takes && typeof d.takes === "object") PLAN.takes = d.takes;
    CL.sel = -1;
    if (SEL && SEL.type === "cut" && window.inspect) inspect(null);
  }

  // ---- modo, render y refresco
  CL.setMode = function (m) {
    CL.mode = m === "clean" ? "clean" : "edit";
    const st = document.querySelector(".studio");
    if (st) st.classList.toggle("mclean", CL.mode === "clean");
    document.body.classList.toggle("mclean", CL.mode === "clean");
    $$("#segMode button").forEach(b => { const on = b.dataset.v === CL.mode; b.classList.toggle("sel", on); b.setAttribute("aria-selected", String(on)); });
    if (JOB && JOB !== "demo") lsSet(`reelfy.mode.${JOB}`, CL.mode);
    toastTop();
    if (CL.mode === "clean") {
      // la seleccion del timeline pasa al guion (un corte seleccionado se reproduciria en vez de saltarse)
      // si ese corte se ve ahi: un silencio sin palabras con "Mostrar silencios" apagado no se ve, y
      // Espacio / R actuarian sobre un corte invisible
      if (SEL && SEL.type === "cut") {
        const i = SEL.i, c = PLAN.cuts[i];
        if (window.inspect) inspect(null);
        prep();
        CL.sel = c && (C.cutKind(c) !== "silencio" || CL.sil || GW.spans[i]) ? i : -1;
      }
      CL.render();
      requestAnimationFrame(() => { drawMap(); placePop(); if (CL.sel >= 0) scrollToSel(); });
    } else {
      const s = CL.sel; CL.sel = -1; const pop = $("#gPop"); if (pop) pop.remove();
      if (typeof renderTL === "function") renderTL();
      if (s >= 0 && PLAN.cuts[s] && window.inspect) { inspect({ type: "cut", i: s }); if (window.renderCutsLane) renderCutsLane(); }
    }
    setTimeout(() => FX.syncOverlayRect(), 60);
  };
  /** Los avisos del modo limpieza van justo bajo la barra de modos (no tapan el pie del guion). */
  function toastTop() {
    const mb = $("#modeBar"); if (!mb) return;
    const r = mb.getBoundingClientRect();
    if (r.height) document.body.style.setProperty("--toast-top", Math.round(r.bottom + 8) + "px");
  }
  CL.render = function () {
    if (!PLAN) return;
    CL.chrome();
    if (CL.mode !== "clean") { CL.stale = true; return; }
    CL.stale = false;
    prep();
    if (CL.sel >= 0 && !PLAN.cuts[CL.sel]) CL.sel = -1;
    renderChips(); renderScript(); renderFoot(); drawMap(); renderSum(); renderPop();
  };
  /** Tras cambiar los cortes: ci = el corte tocado (re-dibuja solo su parrafo), sin ci todo. */
  CL.refresh = function (ci) {
    if (!PLAN) return;
    CL.chrome();
    if (CL.mode !== "clean") { CL.stale = true; return; }
    const sig = GW.sig;
    if (typeof ci === "number" && ci >= 0 && PLAN.cuts[ci]) {
      prep();
      if (GW.sig === sig && renderParasOf(ci)) {
        markSel(); renderChips(); renderFoot(); drawMap(); renderSum(); renderPop(); GW.live = -1;
        return;
      }
    }
    CL.render();
  };
  /** Al abrir el proyecto: Limpieza si hay tomas detectadas (o si fue el ultimo modo usado). */
  CL.open = function () {
    CL.wire();
    CL.sil = lsGet("reelfy.clean.sil") === "1";
    CL.sel = -1; CL.filter = "todos"; CL.listenRange = null;
    const saved = JOB && JOB !== "demo" ? lsGet(`reelfy.mode.${JOB}`) : null;
    const hasTakes = (PLAN.cuts || []).some(C.isTakeCut);
    CL.setMode(saved === "clean" || saved === "edit" ? saved : hasTakes ? "clean" : "edit");
    CL.chrome();
  };
  /** Cada cuadro: playhead del mapa y palabra que suena en el guion. */
  CL.tick = function (t) {
    if (CL.mode !== "clean" || !PLAN) return;
    const ph = $("#cmapPh");
    if (ph) { const x = (Math.max(0, Math.min(1, t / dur())) * 100).toFixed(2) + "%"; if (ph._x !== x) { ph.style.left = x; ph._x = x; } }
    const st = GW.starts; if (!st.length) return;
    let lo = 0, hi = st.length; while (lo < hi) { const m = (lo + hi) >> 1; if (st[m] <= t) lo = m + 1; else hi = m; }
    let i = lo - 1; if (i >= 0 && t > GW.words[i].end + 0.3) i = -1;
    if (i === GW.live) return;
    const tr = $("#gTr"); if (!tr) return;
    const old = tr.querySelector(".gw.live"); if (old) old.classList.remove("live");
    GW.live = i;
    const el = i >= 0 ? tr.querySelector(`.gw[data-i="${i}"]`) : null;
    if (!el) return;
    el.classList.add("live");
    // al reproducir, el guion sigue a la voz (salvo que el usuario este desplazandose)
    if (!V.paused && Date.now() - CL.userScroll > 2500) {
      const tb = tr.getBoundingClientRect(), r = el.getBoundingClientRect();
      if (r.top < tb.top + 30 || r.bottom > tb.bottom - 30) tr.scrollTop += r.top - tb.top - tr.clientHeight / 3;
    }
  };
  CL.onKey = function (e) {
    if (!cleanOn() || e.metaKey || e.ctrlKey || e.altKey) return false;
    if ($("#gConfirm") && $("#gConfirm").classList.contains("on")) return false;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); step(e.key === "ArrowDown" ? 1 : -1); return true; }
    if (CL.sel < 0) return false;
    if (e.code === "Space") { e.preventDefault(); CL.listen(CL.sel); return true; }
    if (e.key === "r" || e.key === "R") { e.preventDefault(); CL.toggle(CL.sel); return true; }
    if (e.key === "Escape") { e.preventDefault(); select(-1); return true; }
    return false;
  };
  /** Eventos del modo (una sola vez; todo por delegacion: el guion se re-dibuja seguido). */
  CL.wire = function () {
    if (CL.wired) return; CL.wired = true;
    $$("#segMode button").forEach(b => b.onclick = () => CL.setMode(b.dataset.v));
    const u2 = $("#undoBtn2"), r2 = $("#redoBtn2");
    if (u2) u2.onclick = () => { if (window.undo) undo(); };
    if (r2) r2.onclick = () => { if (window.redo) redo(); };
    const tr = $("#gTr");
    tr.addEventListener("click", e => {
      const a = e.target.closest("[data-a]");
      if (a) {
        const act = a.dataset.a;
        if (act === "listen") CL.listen(CL.sel);
        else if (act === "toggle") CL.toggle(CL.sel);
        else if (act === "find") CL.findTakes("normal", { llm: true });
        a.blur(); tr.focus({ preventScroll: true });
        return;
      }
      if (e.target.closest(".gpop")) return;
      const cut = e.target.closest("[data-c]");
      if (cut) { select(+cut.dataset.c, { seek: true }); return; }
      const w = e.target.closest(".gw");
      if (CL.sel >= 0) select(-1);
      if (w && GW.words[+w.dataset.i]) V.currentTime = GW.words[+w.dataset.i].start + 0.001;
    });
    tr.addEventListener("dblclick", e => {
      const w = e.target.closest(".gw"); if (!w || e.target.closest("[data-c]") || !GW.words[+w.dataset.i]) return;
      V.currentTime = GW.words[+w.dataset.i].start + 0.001;
      if (window.ensureAudio) ensureAudio();
      V.play().catch(() => {});
    });
    ["wheel", "touchmove"].forEach(ev => tr.addEventListener(ev, () => { CL.userScroll = Date.now(); }, { passive: true }));
    $("#gChips").addEventListener("click", e => { const b = e.target.closest("[data-f]"); if (b && !b.disabled) CL.setFilter(b.dataset.f); });
    $("#gSil").onclick = () => CL.setSil(!CL.sil);
    $("#gPrev").onclick = () => step(-1);
    $("#gNext").onclick = () => step(1);
    $("#gRestAll").onclick = () => CL.restoreAll();
    $("#cSum").addEventListener("click", e => {
      const lv = e.target.closest("[data-lv]"); if (lv && !lv.disabled) { CL.setLevel(lv.dataset.lv); return; }
      const k = e.target.closest("[data-kind]"); if (k && !k.disabled) { CL.setKind(k.dataset.kind, k.getAttribute("aria-checked") !== "true"); return; }
      const a = e.target.closest("[data-a]");
      if (a && a.dataset.a === "retry") CL.findTakes((PLAN.takes && PLAN.takes.sensitivity) || "normal", { llm: true });
    });
    const bar = $("#cmapBar");
    const tAt = e => { const r = bar.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur(); };
    bar.addEventListener("click", e => { V.currentTime = tAt(e); });
    bar.addEventListener("mousemove", e => {
      const t = tAt(e), tol = 3 / Math.max(1, bar.clientWidth) * dur();
      let best = null;
      (PLAN.cuts || []).forEach(c => { if (c && t >= +c.start - tol && t <= +c.end + tol && (!best || C.isTakeCut(c) > C.isTakeCut(best))) best = c; });
      bar.title = best ? `${fmtM(+best.start)} · ${C.TAKE_LABEL[C.cutKind(best)]} · ${C.fmtLen(best.end - best.start)}${C.cutOn(best) ? "" : " (conservado)"}` : fmtM(t);
    });
    window.addEventListener("resize", () => { if (CL.mode === "clean") { drawMap(); placePop(); toastTop(); } });
  };

  // ------------------------------------------------------------------ inicio del editor
  FX.init = function () {
    // el plan recien leido del servidor: mismo filtro que tras /direct, antes de tomar nota de el
    if (isPro()) C.dedupeFx(PLAN.fx);
    syncLocked();
    FX.noteServerPlan(PLAN);
    noteGrid();
    try { const v = localStorage.getItem("reelfy.exact"); if (v === "0") FX.exactOn = false; } catch (e) { /* sin storage */ }
    const bt = $("#btnExact"); if (bt) bt.classList.toggle("on", FX.exactOn);
    FX.syncMode();
    loadCatalogs().then(() => { FX.renderAll(); if (isPro()) { FX.maybeAutoMusic(); } });
    FX.resetAnalysisSig();
    FX.renderAll();
    if (window.ResizeObserver && CV && !CV._fxro) { CV._fxro = new ResizeObserver(() => FX.syncOverlayRect()); CV._fxro.observe(CV); }
    window.addEventListener("resize", () => FX.syncOverlayRect());
    setTimeout(() => FX.syncOverlayRect(), 50);
    setBadge();
  };
  /** Si el plan ya trae musica generada o el servidor la tiene en cache, la deja lista para la vista previa. */
  FX.maybeAutoMusic = function () {
    const m = PLAN.fx.music || {};
    if (FX.music.file || FX.music.busy || m.enabled === false || m.source === "none" || JOB === "demo") return;
    FX.genMusic(false);
  };
})();
