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
  const FONT_STATE = {};                  // nombre -> "loading" | "ok" | "err"
  const ICON_CACHE = {};                  // nombre -> svg (string)
  const ICON_IMG = {};                    // nombre|color -> Image
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
      const en = document.getElementById("enhance"); o.enhance_audio = en ? en.checked : true;
      o.platform = PLATFORM; o.lang = LANG;
    } catch (e) { /* editor aun sin iniciar */ }
    return o;
  }
  FX.engineOpts = engineOpts;

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
    if (!SETTINGS_OK) tasks.push(jget("/settings").then(d => { SETTINGS_OK = true; PRO_OPTS = new Set((d && d.pro_opts) || []); FX.syncProControls(); }).catch(() => {}));
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
  let PRO_OPTS = new Set(), SETTINGS_OK = false;
  FX.proOpts = () => PRO_OPTS;
  FX.setProOpts = list => { PRO_OPTS = new Set(list || []); FX.syncProControls(); };   // pruebas / servidor nuevo
  FX.syncProControls = function () {
    const pro = isPro();
    const gate = (sel, key) => {
      const el = $(sel); if (!el) return false;
      const off = pro && !PRO_OPTS.has(key);
      el.classList.toggle("proff", off);
      el.querySelectorAll("input,button,select").forEach(x => { x.disabled = off; });
      return off;
    };
    const logoOff = gate("#brandLogoRow", "logo");
    gate("#airRow", "air");
    const enhOff = gate("#enhanceRow", "enhance_audio");
    if (enhOff) { const en = $("#enhance"); if (en && !en.checked) { en.checked = true; if (window.applyEnhance) applyEnhance(); } }
    const n = $("#proNoteBrand"); if (n) n.classList.toggle("on", logoOff);
    $$("#segLang button").forEach(b => {
      const off = pro && !C.PRO_LANGS.includes(b.dataset.v);
      b.disabled = off; b.classList.toggle("dis", off);
      b.title = off ? "En la edición IA por ahora: español e inglés. Los demás idiomas siguen en el modo clásico." : "";
    });
    FX.syncLangNote();
  };
  /** Aviso del idioma en la edicion IA: los subtitulos se traducen; titulos y graficos, a peticion. */
  FX.syncLangNote = function () {
    const n = $("#langNote"); if (!n) return;
    if (!isPro() || typeof LANG === "undefined" || LANG !== "en") { n.style.display = "none"; return; }
    const list = C.fxTexts(PLAN.fx), done = FX.fxEnSig && FX.fxEnSig === JSON.stringify(list.map(x => x.text));
    n.style.display = "";
    n.innerHTML = `${ICO("alert")}<span>Subtítulos en inglés. ${!list.length ? "" : done ? "Títulos y gráficos traducidos." : `Los títulos y gráficos (${list.length}) siguen en el idioma original.`}</span>`
      + (list.length && !done ? `<button class="btn ghost mini" id="trFx">${ICO("type")}Traducir títulos y gráficos</button>` : "");
    const b = $("#trFx"); if (b) b.onclick = () => FX.translateFx();
  };
  /** Traduce EN SITIO los textos de titulos, graficos y escenas (un paso de deshacer). Usa
      /translate con los textos como `highlights[].title` (translate_texts, 1:1) hasta que el
      servidor acepte `texts`. */
  FX.translateFx = async function () {
    if (!isPro() || FX.busy()) return;
    const list = C.fxTexts(PLAN.fx); if (!list.length) return;
    if (window.chip) chip("Traduciendo títulos y gráficos…");
    try {
      const d = await jpost(`/translate/${JOB}`, { plan: { phrases: [], highlights: list.map(x => ({ title: x.text })) }, texts: list.map(x => x.text), lang: "en" });
      const out = d.texts_en || d.titles_en || [];
      if (out.length !== list.length) throw new Error("respuesta incompleta");
      edit(() => { C.applyFxTexts(PLAN.fx, list.map((x, i) => Object.assign({}, x, { to: out[i] }))); }, { rebuild: true });
      FX.fxEnSig = JSON.stringify(C.fxTexts(PLAN.fx).map(x => x.text));
      toast("Títulos y gráficos traducidos. ⌘Z vuelve al español.", "type", true);
    } catch (e) { toast(`No se pudo traducir: ${e.message || e}`, "alert"); }
    finally { if (window.chip) chip(null); FX.syncLangNote(); }
  };
  FX.schema = () => SCHEMA;

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
    const im = new Image(); im.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(s);
    ICON_IMG[key] = im; return null;
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
    TOP_TRACKS.forEach(tk => { const r = trackRow(tk); top.appendChild(r); renderTrackLane(tk, r.querySelector(".lane")); });
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
    lane.style.height = (arr.length ? n * ROWH + 2 : 16) + "px";
    arr.forEach((it, i) => {
      const el = document.createElement("div");
      const w = Math.max(6, (it.end - it.start) * px);
      el.className = "fxb" + (it.enabled === false ? " off" : "") + (it.src === "user" ? " user" : "") + (w < 16 ? " tiny" : w < 34 ? " narrow" : "");
      el.dataset.fx = `${track}:${it.id}`;
      el.style.left = (it.start * px) + "px"; el.style.width = w + "px"; el.style.top = (2 + rows[i] * ROWH) + "px";
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
        el.className = "fxe" + (it.enabled === false ? " off" : "");
        el.style.left = x + "px"; el.style.top = top + "px"; el.style.width = "1px";
        el.innerHTML = (sd ? `<div class="tail" style="left:${-hit * px}px;width:${Math.max(4, sd * px)}px"></div>` : "")
          + `<div class="dia"></div>` + (showLb ? `<span class="elb">${esc(lbl(it.name))}</span>` : "");
        el.title = `SFX ${lbl(it.name)} en ${C.fmtClock(it.at, .1)} (${(+it.gain_db || 0).toFixed(1)} dB)`;
      }
      wireBlock(el, track, it);
      lane.appendChild(el);
    });
    wireLaneEmpty(lane);
  }

  function kindIcon(track, it) {
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
        if (began) { group.forEach(g => autoLock(g.item)); syncLocked(); FX.changed({ noMut: true }); }
        else if (mode === "m") { const t0 = C.isEvent(track) ? it.at : it.start; if (V.paused && (V.currentTime < C.itemStart(track, it) - 0.01 || V.currentTime > C.itemEnd(track, it))) V.currentTime = Math.max(0, t0 + (C.isEvent(track) ? -0.15 : 0.02)); }
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
    // pulsos de la ultima musica generada (tiempo final -> original)
    if (FX.music.beats && FX.music.beats.length && px > 12) {
      const tl = tlObj();
      FX.music.beats.forEach(bt => { const t = tl.inverse(bt); const d = document.createElement("div"); d.className = "mbeat"; d.style.left = (t * px) + "px"; b.appendChild(d); });
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
    if (!SCHEMA) { toast("El catálogo de efectos aún no está disponible. Reintentando…", "alert"); loadCatalogs(); return; }
    const ks = C.kinds(SCHEMA, track);
    const rect = anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: anchor.x, bottom: anchor.y };
    let html = `<div class="mh">Agregar a ${TRACK_META[track].label}</div>`;
    if (track === "sfx") {
      const byCat = {};
      ks.forEach(k => { const c = (SFXCAT[k] && SFXCAT[k].category) || "otros"; (byCat[c] = byCat[c] || []).push(k); });
      Object.keys(byCat).sort().forEach(c => { html += `<div class="mh">${esc(c)}</div>` + byCat[c].map(k => `<button data-k="${esc(k)}" style="--c:${TRACK_META.sfx.rgb}">${ICO("speaker")}${esc(lbl(k))}</button>`).join(""); });
    } else {
      // medios propios (imagen o clip): solo si el motor los declara en el esquema
      const mk = C.mediaKinds(SCHEMA, track);
      if (mk.length) html += `<button data-media="${esc(mk[0])}" style="--c:${TRACK_META[track].rgb}">${ICO("upload")}Imagen o video propio…</button>`;
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

  // ---- medios propios: subir (POST /sources) y crear el item en el playhead
  async function uploadMedia(file) {
    const fd = new FormData(); fd.append("files", file);
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
      // la escena con foto propia no lleva el titulo de ejemplo
      return FX.addItem(track, kind, t, Object.assign({ [p.k]: media }, track === "cutaways" ? { "text.text": "" } : {}));
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
      const mk = C.mediaKinds(SCHEMA, track); if (!mk.length) return;
      lane.addEventListener("dragover", e => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { e.preventDefault(); lane.classList.add("dropok"); } });
      lane.addEventListener("dragleave", () => lane.classList.remove("dropok"));
      lane.addEventListener("drop", e => {
        e.preventDefault(); lane.classList.remove("dropok");
        const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]; if (!f) return;
        const t = clamp((e.clientX - lane.getBoundingClientRect().left) / TL.px, 0, dur());
        FX.addMedia(track, mk[0], f, t);
      });
    });
  }
  FX.addAnyMenu = function (anchor) {
    const r = anchor.getBoundingClientRect();
    const html = `<div class="mh">Agregar en el playhead</div>` + ["text", "graphics", "cutaways", "effects", "transitions", "sfx"]
      .map(t => `<button data-t="${t}" style="--c:${TRACK_META[t].rgb}">${ICO(TRACK_META[t].icon)}${TRACK_META[t].label}</button>`).join("");
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
      + `<button data-a="regen">${ICO("spark")}Regenerar con IA</button>`
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
      edit(() => sel.forEach(s => { s.item.locked = v; }));
    } else if (a === "en") {
      const v = sel.every(s => s.item.enabled === false);
      edit(() => sel.forEach(s => { s.item.enabled = v; }));
    } else if (a === "regen") {
      FX.regenerate(sel.map(s => s.item.id));
    }
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
  /** Adopta el plan del director: conserva las traducciones y quita duplicados de lo bloqueado. */
  function adoptPlan(d) {
    mut();
    const keepEn = PLAN.phrases_en, keepTitles = PLAN.titles_en;
    PLAN = d.plan;
    if (keepEn && !PLAN.phrases_en) PLAN.phrases_en = keepEn;
    if (keepTitles && !PLAN.titles_en) PLAN.titles_en = keepTitles;
    const removed = isPro() ? C.dedupeFx(PLAN.fx) : [];
    syncLocked();
    FX.afterPlanReplaced();
    FX.resetAnalysisSig();
    if (removed.length) FX.markDirty();
    return removed;
  }
  FX.redirect = async function (full, seed) {
    const d = PLAN.director || {}, first = !isPro();
    const nLocked = isPro() ? C.lockedIds(PLAN.fx).length : 0;
    const r = await direct({ style: FX.dirStyle(), intensity: FX.dirIntensity(), seed: seed != null ? seed : (d.seed != null ? d.seed : 7), full: !!full },
      first ? "Dirigiendo la edición con IA…" : full ? "Re-dirigiendo toda la edición…" : "Aplicando el estilo…");
    if (!r) return false;
    const removed = adoptPlan(r);
    let msg = first ? "Edición dirigida por la IA: todo queda editable en el timeline." : full ? "Edición re-dirigida." : "Estilo aplicado.";
    if (nLocked) msg += nLocked === 1 ? " Se respetó el elemento bloqueado." : ` Se respetaron ${nLocked} elementos bloqueados.`;
    if (removed.length) msg += ` Se quitaron ${removed.length} repetido${removed.length > 1 ? "s" : ""}.`;
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
    const { fx: merged, results } = C.mergeRegenerate(PLAN.fx, r.plan.fx || {}, ids);
    const changed = results.filter(x => x.status === "changed" || x.status === "replaced");
    FX.markDirty();                   // el servidor guardo SU plan: se vuelve a guardar el del Studio
    if (!changed.length) {
      toast(ids.length > 1 ? "La IA no propuso cambios para estos elementos; quedaron como estaban." : "La IA no propuso cambios para este elemento; quedó como estaba.", "alert");
      return;
    }
    mut();
    PLAN.fx = merged; syncLocked();
    FX.afterPlanReplaced();
    SELX = results.map(x => ({ track: x.track, id: x.newId })).filter(x => items(x.track).some(i => i.id === x.id));
    SEL = SELX.length ? { type: "fx", ...SELX[SELX.length - 1] } : null; markSelection(); FX.inspect(SEL, true);
    const same = results.length - changed.length;
    toast(`Regenerado${changed.length > 1 ? ` (${changed.length})` : ""}.${same ? ` ${same} sin cambios.` : ""} ⌘Z vuelve a la versión anterior.`, "spark", true);
  };
  /** Convierte el plan v1 en v2 con el director (primer "Dirigir con IA"). */
  FX.directFirst = async () => { if (await FX.redirect(true) && isPro()) FX.maybeAutoMusic(); };

  let DSTYLE = null, DINT = null;
  FX.dirStyle = () => DSTYLE || (PLAN.director && PLAN.director.style) || "dinamico";
  FX.dirIntensity = () => (DINT != null ? DINT : (PLAN.director && PLAN.director.intensity != null ? +PLAN.director.intensity : 0.75));

  FX.afterPlanReplaced = function () {
    TLCACHE.key = ""; CAPCACHE.key = ""; INSP_KEY = "";   // el inspector apunta a objetos del plan anterior
    DSTYLE = null; DINT = null;
    SELX = SELX.filter(s => items(s.track).some(i => i.id === s.id));
    flatWords = P().flat(); liveWordEl = null;
    FX.syncMode();
    if (window.renderTL) renderTL();
    if (window.renderList) renderList();
    if (window.renderSubs) renderSubs();
    FX.renderAll();
    if (isPro()) syncMusicCache();
    FX.planDirty();
  };

  // ------------------------------------------------------------------ panel Director + grupos del proyecto
  FX.syncMode = function () { document.body.classList.toggle("pro", isPro()); FX.syncProControls(); };
  FX.renderAll = function () {
    FX.syncMode(); FX.renderDirector(); FX.renderProjectGroups(); FX.renderLanes();
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
      h += `<div class="dirstats" id="dirStats"></div>`;
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
  FX.music = { file: null, bpm: 0, drop: null, beats: [], sig: null, busy: false, audio: null, node: null, gain: null, warnings: [], bySig: {} };
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
      const res = { file: d.file, bpm: +d.bpm || 0, drop: d.drop != null ? +d.drop : null, beats: d.beats || [], sig };
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
    if (cb) { cb.innerHTML = ""; renderParams(cb, SCHEMA.captions.params, PLAN.fx.captions, { noLock: true, ctx: "captions", onChange: () => { CAPCACHE.key = ""; } }); }
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
    inspectItem(b, sel.track, it);
    return true;
  };
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
    renderParams(section(b, "Estilo"), SCHEMA.captions.params, PLAN.fx.captions, { noLock: true, ctx: "captions", onChange: () => { CAPCACHE.key = ""; FX.renderProjectGroupsSoon(); } });
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
  function tagsHtml(it) {
    return (it.src === "user" ? `<span class="tagp user">${ICO("user")}Creado por ti</span>` : `<span class="tagp ai">${ICO("spark")}IA</span>`)
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
        cb.onchange = () => edit(() => { set(cb.checked ? null : (p.default != null ? p.default : (p.min != null ? p.min : 0))); after(obj, o2, true); }, { rebuild: true });
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
        s.innerHTML = opts.map(x => `<button data-v="${esc(x)}" class="${x === v ? "sel" : ""}">${esc(lbl(x))}</button>`).join("");
        s.querySelectorAll("button").forEach(bt => bt.onclick = () => { edit(() => { set(bt.dataset.v); after(obj, o2, true); }, { inspector: false }); s.querySelectorAll("button").forEach(x => x.classList.toggle("sel", x === bt)); });
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
    if (t === "media") return frow(label, mediaControl(obj, o2, get, set), true, p.hint);
    return null;
  }

  /** Medio propio (imagen o clip subido por /sources): miniatura, nombre y "Cambiar". */
  function mediaControl(obj, o2, get, set) {
    const w = document.createElement("div"); w.className = "mediapick";
    const draw = () => {
      const v = get() || {};
      const th = v.thumb || (v.sid ? `/tlsource/${encodeURIComponent(v.sid)}/thumb` : "");
      w.innerHTML = `<div class="mth">${th ? `<img src="${esc(th)}" alt="">` : ICO("image")}</div><div class="mnm"><b>${esc(v.name || "Sin archivo")}</b>`
        + `<span>${v.type === "video" ? `Video · ${(+v.dur || 0).toFixed(1)} s` : v.type ? "Imagen" : "Elige una imagen o un clip"}${v.w ? ` · ${v.w}×${v.h}` : ""}</span></div>`
        + `<button class="btn ghost mini" type="button">${ICO("upload")}${v.sid ? "Cambiar" : "Elegir"}</button>`;
      w.querySelector("button").onclick = () => FX.pickMedia(null, null, async file => {
        if (window.chip) chip("Subiendo el archivo…");
        try { const m = await uploadMedia(file); edit(() => { set(m); after(obj, o2, true); }, { inspector: false }); draw(); }
        catch (e) { toast(`No se pudo subir: ${e.message || e}`, "alert"); }
        finally { if (window.chip) chip(null); }
      });
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
    w.innerHTML = `<div class="sfxrow"><select class="fsel">${Object.keys(byCat).sort().map(c => `<optgroup label="${esc(c)}">${byCat[c].map(k => `<option value="${esc(k)}" ${k === it.name ? "selected" : ""}>${esc(lbl(k))}</option>`).join("")}</optgroup>`).join("")}</select>
      <button class="btn ghost mini" title="Escuchar">${ICO("play")}Escuchar</button></div><div class="sfxdesc"></div>`;
    const sel = w.querySelector("select"), desc = w.querySelector(".sfxdesc");
    const setDesc = () => { const c = SFXCAT[sel.value]; desc.textContent = c ? `${c.desc || ""}${c.dur ? ` · ${(+c.dur).toFixed(2)} s` : ""}` : ""; };
    setDesc();
    sel.onchange = () => { edit(() => { it.name = sel.value; autoLock(it); }, { inspector: false }); setDesc(); FX.playSfx(sel.value, it.gain_db); };
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

  /** Texto cinetico aproximado (misma geometria que el esquema: y = centro del bloque). */
  function drawTextItem(c, it, lt, L, box) {
    const B = box || LB, W = B.w, s = B.s;
    const st = animState(it, lt, L, "pop");
    if (st.a <= 0.01) return null;
    const base = (+it.size || 0.08) * W;
    let lines;
    if (it.preset === "counter") {
      const p = clamp(lt / Math.max(0.3, L * 0.7), 0, 1), e = C.ease("out_cubic", p);
      const v = (+it.from || 0) + ((+it.to || 0) - (+it.from || 0)) * e;
      const dec = +it.decimals || 0;
      const num = v.toLocaleString("es-MX", { minimumFractionDigits: dec, maximumFractionDigits: dec });
      lines = [{ text: `${it.prefix || ""}${num}${it.suffix || ""}`, scale: 1, font: it.font }];
      if (it.text) lines.push({ text: String(it.text), scale: 0.28, font: it.font });
    } else if (Array.isArray(it.lines) && it.lines.length) lines = it.lines.map(l => ({ text: String((l && l.text) || ""), scale: +(l && l.scale) || 1, font: (l && l.font) || it.font, color: l && l.color, italic: l && l.italic }));
    else lines = String(it.text || "").split("\n").map(x => ({ text: x, scale: 1, font: it.font }));
    let kick = null;
    if (it.preset === "kicker") {
      const ks = C.kickerSplit(it, lines);
      if (ks.label) {
        const ksc = ks.only ? 1 : Math.max(+(it.kicker_scale != null ? it.kicker_scale : 0.36), 0.036 * W / Math.max(1, base));
        kick = { text: String(ks.label).toUpperCase(), scale: ksc, font: it.kicker_font || (ks.only ? it.font : null) || "Poppins-Bold", pill: true };
        lines = [kick].concat(ks.rest);
      }
    }
    if (it.case === "upper") lines.forEach(l => { if (!l.pill) l.text = l.text.toUpperCase(); });
    const acc = new Set((it.accent_words || []).map(normW));
    const color = it.color || "#FFFFFF", accent = it.accent || "#FFD23F";
    const align = it.preset === "cascade" ? "left" : (it.align || "center");
    // medidas
    c.save();
    c.textBaseline = "alphabetic";
    let totalH = 0, maxW = 0;
    const meas = lines.map((l, i) => {
      const px = base * l.scale; c.font = fontCss(l.font, px, l.italic);
      const words = l.text.split(/\s+/).filter(Boolean);
      const sp = c.measureText(" ").width;
      const ws = words.map(w => c.measureText(w).width);
      const w = ws.reduce((a, b) => a + b, 0) + sp * Math.max(0, words.length - 1);
      const h = px * 1.05;
      totalH += h + (i ? base * 0.12 : 0); maxW = Math.max(maxW, l.pill ? w + px * 1.6 : w);
      return { l, px, words, ws, sp, w, h };
    });
    const [ix, iy] = itemXY(it);
    const cx = B.x + (ix + st.dx) * W, cy = B.y + (iy + st.dy) * B.h;
    c.globalAlpha *= st.a;
    c.translate(cx, cy); c.scale(st.sc, st.sc); if (it.rotation) c.rotate(+it.rotation * Math.PI / 180);
    let y = -totalH / 2, widx = 0;
    // aparicion por palabra como el render: alineada a la voz (`_words`) o escalonada
    const allW = []; meas.forEach((m, li) => m.words.forEach(w => allW.push({ w, li })));
    let wtimes = null;
    if (it.preset !== "counter" && allW.length) {
      let spoken = null;
      if (it.start != null && !box && typeof flatWords !== "undefined") {
        spoken = flatWords.filter(w => w.start >= +it.start - 0.05 && w.start < +it.end).map(w => ({ norm: C.normWord(w.text), start: w.start - it.start }));
      }
      const step = ["pop", "scale_down", "fade", "mask"].includes(it.anim_in) && !(spoken && spoken.length) && ["title_pop", "behind_giant"].includes(it.preset) ? 0.08 : 0.12;
      wtimes = C.unitTimes(allW.map(x => C.normWord(x.w)), spoken, L, step, allW.map(x => x.li));
    }
    const stroke = (+it.stroke || 0) * s, sh = it.shadow != null ? +it.shadow : 0.6;
    const bb = { x: cx - maxW / 2, y: cy - totalH / 2, w: maxW, h: totalH };
    if (it.box && it.preset !== "highlight_marker" && it.preset !== "kicker") {
      const bl = align === "left" ? 0 : align === "right" ? -maxW : -maxW / 2;
      c.fillStyle = it.box; rrect(c, bl - base * 0.25, y - base * 0.18, maxW + base * 0.5, totalH + base * 0.36, base * 0.18); c.fill();
    }
    if (align === "left") bb.x = cx; else if (align === "right") bb.x = cx - maxW;
    meas.forEach((m, li) => {
      if (li) y += base * 0.12;
      const lx0 = align === "left" ? (it.preset === "cascade" ? li * W * 0.05 : 0) : align === "right" ? -m.w : -m.w / 2;
      const by = y + m.h * 0.82;
      c.font = fontCss(m.l.font, m.px, m.l.italic);
      if (m.l.pill) {
        const pc = it.box || accent; c.fillStyle = pc; rrect(c, lx0 - m.px * 0.8, y - m.px * 0.12, m.w + m.px * 1.6, m.h + m.px * 0.24, (m.h + m.px * 0.24) / 2); c.fill();
      }
      let x = lx0;
      m.words.forEach((w, wi) => {
        const wt = wtimes ? wtimes[widx++] : 0;
        if (lt < wt) { x += m.ws[wi] + m.sp; return; }
        let txt = w;
        if (["type", "letter_stagger"].includes(it.anim_in)) { const nch = Math.floor((lt - wt) / 0.035) + 1; if (nch < w.length) txt = w.slice(0, nch); }
        c.save(); c.globalAlpha *= clamp((lt - wt) / 0.12, 0, 1);
        const isAcc = acc.has(normW(w));
        let fill = m.l.color || (isAcc ? accent : color);
        if (m.l.pill) fill = lum(it.box || accent) > 0.6 ? "#111111" : "#FFFFFF";
        if (it.preset === "highlight_marker" && (isAcc || !acc.size)) {
          c.fillStyle = hexA(it.box || accent, 0.92); c.fillRect(x - m.px * 0.08, y + m.h * 0.12, m.ws[wi] + m.px * 0.16, m.h * 0.8);
          fill = lum(it.box || accent) > 0.6 ? "#111111" : "#FFFFFF";
        }
        if (sh > 0) { c.shadowColor = `rgba(0,0,0,${0.55 * sh})`; c.shadowBlur = 14 * s * sh; c.shadowOffsetY = 3 * s * sh; }
        if (stroke > 0) { c.lineJoin = "round"; c.lineWidth = stroke; c.strokeStyle = lum(fill) > 0.5 ? "rgba(0,0,0,.85)" : "rgba(255,255,255,.9)"; c.strokeText(txt, x, by); }
        c.fillStyle = fill; c.fillText(txt, x, by);
        c.restore();
        x += m.ws[wi] + m.sp;
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
  const CTA_SIZE = { follow: 0.72, link_below: 0.56 }, SAFE_W = C.SAFE.right - C.SAFE.left;
  function drawCta(c, it, cx, cy, B, lt) {
    const v = it.variant || "follow", W = B.w;
    const rng = v === "follow" ? [0.56, SAFE_W] : v === "link_below" ? [0.4, 0.74] : [0.45, SAFE_W];
    const S = clamp(+(it.size != null ? it.size : (CTA_SIZE[v] || 0.62)), rng[0], rng[1]) * W;
    const ent = PLAN.entities || {}, acc = it.color && it.color !== "#FFFFFF" ? it.color : "#FFD23F";
    const bold = "Poppins-ExtraBold";
    c.shadowColor = "rgba(0,0,0,.35)"; c.shadowBlur = 14 * B.s; c.shadowOffsetY = 4 * B.s;
    c.textBaseline = "middle"; c.textAlign = "left";
    if (v === "link_below") {
      const h = S * 0.2, ib = h * 0.42, label = String(it.label || ent.link_label || "Agenda aquí");
      let fs = h * 0.38; c.font = fontCss(bold, fs); let tw = c.measureText(label).width;
      if (tw > S * 0.9) { fs *= S * 0.9 / tw; c.font = fontCss(bold, fs); tw = c.measureText(label).width; }
      const bw = tw + ib + h * 0.2 + h;
      c.fillStyle = acc; rrect(c, -bw / 2, -h / 2, bw, h, h / 2); c.fill(); c.shadowBlur = 0;
      const on = lum(acc) > 0.55 ? "#141418" : "#FFFFFF", x1 = -(tw + ib + h * 0.2) / 2;
      const im = iconImage(it.icon || "calendar-event", on); if (im) c.drawImage(im, x1, -ib / 2, ib, ib);
      c.fillStyle = on; c.fillText(label, x1 + ib + h * 0.2, fs * 0.06);
      const asc = clamp(it.arrow_scale != null ? +it.arrow_scale : 1, 0, 1);
      if (asc >= 0.35 && lt > 0.25) {
        c.save(); c.translate(0, h / 2 + h * 0.3 + h * 0.85 * asc); c.strokeStyle = it.arrow_color || "#FFFFFF";
        c.lineWidth = 17 * B.s; c.lineCap = "round"; c.lineJoin = "round"; doodlePath(c, "arrow_down", h * 0.85 * asc); c.stroke(); c.restore();
      }
      return { x: cx - bw / 2, y: cy - h / 2, w: bw, h: h * (asc >= 0.35 ? 2.9 : 1) };
    }
    if (v === "follow") {
      const h = S * 0.17, d = h * 0.74;
      const name = String(it.handle || ent.handle || ent.name || it.label || "Sígueme para más");
      c.fillStyle = "#FFFFFF"; rrect(c, -S / 2, -h / 2, S, h, h / 2); c.fill(); c.shadowBlur = 0;
      const ax = -S / 2 + h * 0.13 + d / 2;
      c.fillStyle = acc; c.beginPath(); c.arc(ax, 0, d / 2, 0, Math.PI * 2); c.fill();
      c.fillStyle = lum(acc) > 0.55 ? "#141418" : "#FFFFFF"; c.font = fontCss(bold, d * 0.46); c.textAlign = "center";
      c.fillText(name.replace(/^@/, "").charAt(0).toUpperCase(), ax, d * 0.03); c.textAlign = "left";
      const btn = String(it.button || "Seguir"); c.font = fontCss(bold, h * 0.24); const bw = c.measureText(btn).width + h * 0.6;
      const bcol = it.button_color || (it.color && lum(it.color) <= 0.85 ? it.color : "#FF3B5C");
      c.fillStyle = bcol; rrect(c, S / 2 - h * 0.13 - bw, -h * 0.3, bw, h * 0.6, h * 0.3); c.fill();
      c.fillStyle = lum(bcol) > 0.6 ? "#141418" : "#FFFFFF"; c.fillText(btn, S / 2 - h * 0.13 - bw + h * 0.3, h * 0.01);
      c.fillStyle = "#141418"; c.font = fontCss(bold, h * 0.27);
      c.fillText(name.slice(0, 28), ax + d / 2 + h * 0.2, it.subtitle ? -h * 0.12 : 0);
      if (it.subtitle) { c.fillStyle = "#6C6D7A"; c.font = fontCss("Poppins-Medium", h * 0.2); c.fillText(String(it.subtitle).slice(0, 36), ax + d / 2 + h * 0.2, h * 0.2); }
      return { x: cx - S / 2, y: cy - h / 2, w: S, h };
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
      doodlePath(c, it.name, size / 2); c.stroke();
      if (["sparkle", "star4", "star5", "heart"].includes(it.name) && !(draw && lt < 0.45)) { c.fillStyle = col; c.fill(); }
    } else if (k === "icon") {
      // graphics.draw_icon: badge de diametro `size` e icono de 0.56*size (sin badge: size)
      const gm = C.iconGeom(size, it.badge);
      let icol = it.color || "#FFFFFF";
      if (gm.badge) {
        const bcol = it.badge_color || "#FFD23F";
        c.fillStyle = bcol;
        if (it.badge === "circle") { c.beginPath(); c.arc(0, 0, gm.badge / 2, 0, Math.PI * 2); c.fill(); } else { rrect(c, -gm.badge / 2, -gm.badge / 2, gm.badge, gm.badge, gm.badge * 0.3); c.fill(); }
        icol = it.color || (lum(bcol) > 0.55 ? "#15151A" : "#FFFFFF");
        if (lum(icol) > 0.8 && lum(bcol) > 0.7) icol = "#15151A";
      }
      c.shadowBlur = gm.badge ? 0 : c.shadowBlur;
      const im = iconImage(it.name, icol);
      if (im) c.drawImage(im, -gm.icon / 2, -gm.icon / 2, gm.icon, gm.icon);
      else { c.strokeStyle = icol; c.lineWidth = 2 * s * 3; c.beginPath(); c.arc(0, 0, gm.icon * 0.32, 0, Math.PI * 2); c.stroke(); }
    } else if (k === "card") {
      const w = Math.max(0.4, +it.size || 0.7) * W, h = w * (it.variant === "checklist" ? 0.62 : 0.4);
      const th = it.theme || "light";
      const bg = th === "dark" ? "#141414" : th === "brand" ? (it.color && it.color !== "#FFFFFF" ? it.color : "#FFD23F") : "#FFFFFF";
      const fg = lum(bg) > 0.55 ? "#111111" : "#FFFFFF";
      c.fillStyle = bg; rrect(c, -w / 2, -h / 2, w, h, w * 0.06); c.fill(); c.shadowBlur = 0;
      const pad = w * 0.07; let ty = -h / 2 + pad;
      c.textBaseline = "top"; c.fillStyle = fg;
      const ic = iconImage(it.icon, fg); const isz = h * 0.28;
      let tx = -w / 2 + pad;
      if (it.icon) { if (ic) c.drawImage(ic, tx, ty, isz, isz); tx += isz + pad * 0.6; }
      c.font = fontCss("Poppins-Bold", w * 0.065); c.fillText(String(it.title || "").slice(0, 40), tx, ty);
      c.globalAlpha *= 0.75; c.font = fontCss("Poppins-Medium", w * 0.045); c.fillText(String(it.subtitle || "").slice(0, 50), tx, ty + w * 0.08); c.globalAlpha /= 0.75;
      if (it.value) { c.font = fontCss("Poppins-ExtraBold", w * 0.1); c.fillText(String(it.value), -w / 2 + pad, h / 2 - pad - w * 0.11); }
      (it.items || []).slice(0, 4).forEach((x, i) => { c.font = fontCss("Poppins-Medium", w * 0.045); c.fillText("- " + x, -w / 2 + pad, ty + w * (0.18 + i * 0.065)); });
      bb = { x: cx - w / 2, y: cy - h / 2, w, h };
    } else if (k === "lower_third") {
      const fs = (+it.size || 0.05) * W;
      c.font = fontCss("Poppins-Bold", fs); const w1 = c.measureText(it.title || "").width;
      c.font = fontCss("Poppins-Medium", fs * 0.62); const w2 = c.measureText(it.subtitle || "").width;
      const w = Math.max(w1, w2) + fs * 1.2, h = fs * 2.2;
      // x es el borde izquierdo del rotulo
      c.fillStyle = "rgba(12,12,16,.82)"; rrect(c, 0, -h / 2, w, h, fs * 0.2); c.fill();
      c.fillStyle = col === "#FFFFFF" ? "#FFD23F" : col; c.fillRect(0, -h / 2, fs * 0.18, h);
      c.shadowBlur = 0; c.fillStyle = "#fff"; c.textBaseline = "alphabetic";
      c.font = fontCss("Poppins-Bold", fs); c.fillText(it.title || "", fs * 0.5, -h / 2 + fs * 1.05);
      c.globalAlpha *= 0.8; c.font = fontCss("Poppins-Medium", fs * 0.62); c.fillText(it.subtitle || "", fs * 0.5, -h / 2 + fs * 1.85);
      bb = { x: cx, y: cy - h / 2, w, h };
    } else if (k === "cta") {
      bb = drawCta(c, it, cx, cy, B, lt);
    } else if (k === "sticker_text") {
      const txt = it.text || "";
      const fs = (+it.size || 0.06) * W;
      c.font = fontCss("Poppins-ExtraBold", fs); const w = c.measureText(txt).width + fs * 1.4, h = fs * 1.7;
      const bg = k === "cta" ? (col === "#FFFFFF" ? "#FF3B5C" : col) : (it.bg || "#FFD23F");
      c.fillStyle = bg; rrect(c, -w / 2, -h / 2, w, h, h / 2); c.fill(); c.shadowBlur = 0;
      c.fillStyle = k === "cta" ? (lum(bg) > 0.6 ? "#111" : "#fff") : (it.fg || "#111111");
      c.textAlign = "center"; c.textBaseline = "middle"; c.fillText(txt, 0, fs * 0.06);
      bb = { x: cx - w / 2, y: cy - h / 2, w, h };
    } else if (C.mediaParam(SCHEMA, "graphics", k) && C.getPath(it, C.mediaParam(SCHEMA, "graphics", k).k)) {
      c.shadowBlur = 0; c.restore(); c.save(); c.globalAlpha *= st.a;       // imagen/clip propio (PiP)
      const r = drawMediaCard(c, C.getPath(it, C.mediaParam(SCHEMA, "graphics", k).k), cx, cy, size, size * 1.78, it.rotation || 0, it.frame !== false);
      if (r) bb = r;
    } else { c.strokeStyle = col; c.lineWidth = 3; c.strokeRect(-size / 2, -size / 2, size, size); }
    c.restore();
    return bb;
  }

  /** Imagen de un medio propio para la vista en vivo (el clip se aproxima con su miniatura). */
  const MEDIA_IMG = {};
  function mediaImage(m) {
    if (!m || !m.sid) return null;
    const url = m.type === "video" ? (m.thumb || `/tlsource/${encodeURIComponent(m.sid)}/thumb`) : `/tlsource/${encodeURIComponent(m.sid)}`;
    let im = MEDIA_IMG[url];
    if (!im) { im = MEDIA_IMG[url] = new Image(); im.src = url; }
    return im.complete && im.naturalWidth ? im : null;
  }
  /** Foto/clip propio como tarjeta (borde blanco e inclinacion leve, como cutaways.photo). */
  function drawMediaCard(c, m, cx, cy, maxW, maxH, tilt, frame) {
    const im = mediaImage(m); if (!im) return null;
    const k = Math.min(maxW / im.naturalWidth, maxH / im.naturalHeight), w = im.naturalWidth * k, h = im.naturalHeight * k;
    c.save(); c.translate(cx, cy); if (tilt) c.rotate(tilt * Math.PI / 180);
    c.shadowColor = "rgba(0,0,0,.45)"; c.shadowBlur = 24; c.shadowOffsetY = 8;
    if (frame) { const pad = Math.max(6, w * 0.035); c.fillStyle = "#FFFFFF"; c.fillRect(-w / 2 - pad, -h / 2 - pad, w + 2 * pad, h + 2 * pad); }
    c.shadowBlur = 0; c.drawImage(im, -w / 2, -h / 2, w, h); c.restore();
    return { x: cx - w / 2, y: cy - h / 2, w, h };
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

  let HITS = [];   // cajas de lo dibujado en el ultimo cuadro (para seleccionar en el canvas)
  /** Composicion en vivo sobre el canvas (despues del video base). */
  FX.drawLive = function (t) {
    if (!isPro()) return;
    const c = CTX; HITS = [];
    layoutBox();
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
      c.save(); c.globalAlpha = a; drawSceneBg(c, it.background, it.bg_colors, lt);
      const mp = C.mediaParam(SCHEMA, "cutaways", it.kind);
      if (mp && C.getPath(it, mp.k)) drawMediaCard(c, C.getPath(it, mp.k), LB.x + LB.w / 2, LB.y + LB.h * 0.45, LB.w * (+it.size || 0.8), LB.h * 0.6, it.tilt != null ? +it.tilt : -3, true);
      if (it.text && (it.text.text || it.text.preset === "counter")) {
        const tx = Object.assign({ x: 0.5, y: 0.45, size: 0.12, preset: "title_pop" }, it.text);
        drawTextItem(c, tx, lt, L);
      }
      if (it.card) drawGraphic(c, Object.assign({ kind: "card", x: 0.5, y: 0.5, size: 0.74 }, it.card), lt, L, null, t);
      (it.graphics || []).forEach(gr => drawGraphic(c, gr, lt - ((gr.start || 0) - 0), L, null, t));
      c.restore();
      HITS.push({ track: "cutaways", id: it.id, bb: { x: 0, y: 0, w: CW, h: CH }, scene: true });
    });
    // graficos (por z)
    activeRange("graphics", t).sort((a, b) => (+a.z || 0) - (+b.z || 0)).forEach(it => { const bb = drawGraphic(c, it, t - it.start, it.end - it.start, null, t); if (bb) HITS.push({ track: "graphics", id: it.id, bb }); });
    // texto normal
    texts.filter(x => !x.behind).sort((a, b) => (+a.z || 0) - (+b.z || 0)).forEach(it => { const bb = drawTextItem(c, it, t - it.start, it.end - it.start); if (bb) HITS.push({ track: "text", id: it.id, bb }); });
    // subtitulos: misma regla que el render (fxcore.captionsHiddenAt)
    if (!C.captionsHiddenAt(PLAN.fx, t)) FX.drawCaptions(t);
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
  };

  /** Subtitulos con el preset del plan y las fuentes reales. */
  FX.drawCaptions = function (t) {
    const cfg = PLAN.fx.captions || {};
    if (cfg.enabled === false || cfg.preset === "none") return;
    const phs = P();
    const key = JSON.stringify([cfg.max_words, phs.length, phs.length ? phs[0].length : 0, LANG, (UNDO || []).length]);
    if (key !== CAPCACHE.key) CAPCACHE = { key, groups: C.captionGroups(phs, cfg.max_words || 4) };
    const gr = CAPCACHE.groups.find(g => t >= g.start - 0.02 && t < g.end + 0.12);
    if (!gr) return;
    // como text.draw_captions: un grupo que empezo tapado por un titulo no reaparece al terminar
    // el titulo (repetiria lo que el titulo ya mostro)
    if (C.captionGroupSuppressed(PLAN.fx, gr, t)) return;
    const c = CTX, B = LB, W = B.w, s = B.s;
    const preset = cfg.preset || "clean_bold";
    let fsz = (+cfg.size || 0.052) * W * (preset === "minimal_small" ? 0.82 : 1);
    const font = cfg.font || "Poppins-ExtraBold";
    const color = cfg.color || "#FFFFFF", accent = cfg.accent || "#FFD23F";
    const cs = cfg.case || (preset === "bold_caps" ? "upper" : "none");
    const fmt = w => cs === "upper" ? w.toUpperCase() : cs === "lower" ? w.toLowerCase() : cs === "title" ? w.charAt(0).toUpperCase() + w.slice(1).toLowerCase() : w;
    // como el render: las palabras entran al pronunciarse y la linea se recentra (karaoke muestra el grupo completo)
    let words = gr.words.map(w => ({ w, txt: fmt(String(w.text || "").trim()) }));
    if (preset !== "karaoke") { const vis = words.filter(x => +x.w.start <= t + 0.03); words = vis.length ? vis : words.slice(0, 1); }
    c.save(); c.textBaseline = "alphabetic"; c.lineJoin = "round";
    const maxW = W * 0.84;
    const setF = () => { c.font = fontCss(font, fsz); };
    setF();
    let sp = c.measureText(" ").width;
    words.forEach(x => { x.wd = c.measureText(x.txt).width; });
    let total = words.reduce((a, x) => a + x.wd, 0) + sp * (words.length - 1);
    let rows = [words];
    if (total > maxW && words.length > 1) {
      let best = 1, diff = 1e9;
      for (let k = 1; k < words.length; k++) { const a = words.slice(0, k).reduce((q, x) => q + x.wd, 0), b = words.slice(k).reduce((q, x) => q + x.wd, 0); if (Math.abs(a - b) < diff) { diff = Math.abs(a - b); best = k; } }
      rows = [words.slice(0, best), words.slice(best)];
      const mw = Math.max(...rows.map(r => r.reduce((q, x) => q + x.wd, 0) + sp * (r.length - 1)));
      if (mw > maxW) { const k = maxW / mw; fsz *= k; setF(); sp = c.measureText(" ").width; words.forEach(x => { x.wd = c.measureText(x.txt).width; }); }
    }
    const age = t - gr.start;
    const a = clamp(age / 0.08, 0, 1) * clamp((gr.end + 0.12 - t) / 0.12, 0, 1);
    c.globalAlpha = a;
    const lh = fsz * 1.18, cy = B.y + C.captionY(cfg, t) * B.h, y0 = cy - (rows.length - 1) * lh / 2 + fsz * 0.35;
    rows.forEach((row, ri) => {
      const rw = row.reduce((q, x) => q + x.wd, 0) + sp * (row.length - 1);
      let x = B.x + (W - rw) / 2; const y = y0 + ri * lh;
      if (preset === "boxed") { c.fillStyle = "rgba(0,0,0,.72)"; rrect(c, x - fsz * 0.3, y - fsz * 0.95, rw + fsz * 0.6, fsz * 1.3, fsz * 0.22); c.fill(); }
      row.forEach(ww => {
        const spoken = t >= ww.w.start, active = spoken && t < ww.w.end + 0.02;
        let fill = color;
        if (preset === "clean_bold" || preset === "bold_caps" || preset === "boxed") fill = active ? accent : color;
        else if (preset === "karaoke") fill = spoken ? accent : color;
        const sc = preset === "bold_caps" && active ? 1.08 : 1;
        c.save();
        if (sc !== 1) { c.translate(x + ww.wd / 2, y - fsz * 0.35); c.scale(sc, sc); c.translate(-(x + ww.wd / 2), -(y - fsz * 0.35)); }
        if (preset !== "boxed") { c.shadowColor = "rgba(0,0,0,.6)"; c.shadowBlur = 10 * s; c.shadowOffsetY = 3 * s; }
        const stroke = (cfg.stroke != null ? +cfg.stroke : preset === "bold_caps" ? 8 : preset === "minimal_small" ? 0 : 5) * s;
        if (stroke > 0) { c.lineWidth = stroke; c.strokeStyle = "rgba(0,0,0,.9)"; c.strokeText(ww.txt, x, y); }
        c.fillStyle = fill; c.fillText(ww.txt, x, y);
        c.restore();
        x += ww.wd + sp;
      });
    });
    c.restore();
    HITS.push({ track: "captions", id: "captions", bb: { x: B.x + W * 0.08, y: cy - lh * rows.length / 2 - fsz * 0.4, w: W * 0.84, h: lh * rows.length + fsz * 0.4 } });
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
    // seleccion
    const sel = SEL;
    HITS.forEach(h => {
      const on = (sel && sel.type === "fx" && isSel(h.track, h.id)) || (sel && sel.type === "captions" && h.track === "captions");
      if (!on || h.scene) return;
      c.save(); c.strokeStyle = "#fff"; c.lineWidth = 1.5; c.setLineDash([6, 4]);
      c.strokeRect(h.bb.x - 4, h.bb.y - 4, h.bb.w + 8, h.bb.h + 8); c.setLineDash([]);
      if (h.track !== "captions") { c.fillStyle = "#fff"; c.strokeStyle = "rgba(0,0,0,.6)"; c.lineWidth = 1; const hs = 9; c.fillRect(h.bb.x + h.bb.w + 4 - hs / 2, h.bb.y + h.bb.h + 4 - hs / 2, hs, hs); c.strokeRect(h.bb.x + h.bb.w + 4 - hs / 2, h.bb.y + h.bb.h + 4 - hs / 2, hs, hs); }
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
    const selH = HITS.find(h => SEL && SEL.type === "fx" && isSel(h.track, h.id) && !h.scene);
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
    if (hit.scene) return true;
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
        if (hit.track !== "captions") obj.x = r3(clamp(x0 + dx / LB.w, 0, 1));
        let ny = clamp(y0 + dy / LB.h, 0.02, 0.98);
        if (Math.abs(ny - 0.5) < 0.012) ny = 0.5;   // iman al centro
        obj.y = r3(ny);
        if (hit.track !== "captions" && Math.abs(obj.x - 0.5) < 0.012) obj.x = 0.5;
      }
    };
    CV.onpointerup = () => {
      CV.onpointermove = null; CV.onpointerup = null;
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
  let SAVE = { timer: null, off: false, dirty: false, inflight: null, conflict: false };
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
  FX.saveNow = async function (force) {
    clearTimeout(SAVE.timer);
    if (SAVE.off || !SAVE.dirty || typeof JOB === "undefined" || !JOB || JOB === "demo") return true;
    if (SAVE.inflight) { await SAVE.inflight; if (!SAVE.dirty) return true; }
    if (SAVE.conflict && !force) return false;
    SAVE.dirty = false; saveHint();
    const body = { plan: PLAN };
    if (FX.serverRev != null && !force) body.base_rev = FX.serverRev;
    const run = (async () => {
      try {
        const r = await fetch(`/plan/${JOB}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        if (r.status === 404 || r.status === 405) { SAVE.off = true; return true; }
        const d = await r.json().catch(() => ({}));
        if (r.status === 409) { SAVE.dirty = true; SAVE.conflict = true; if (d.plan_rev != null) SAVE.remoteRev = d.plan_rev; conflictToast(); return false; }
        if (!r.ok) { SAVE.dirty = true; return false; }
        if (d.plan_rev != null) FX.serverRev = d.plan_rev;
        SAVE.conflict = false;
        FX.kickAnalysis();
        return true;
      } catch (e) { SAVE.dirty = true; return false; }   // sin red: se reintenta en el siguiente cambio
      finally { SAVE.inflight = null; saveHint(); }
    })();
    SAVE.inflight = run;
    return run;
  };
  function conflictToast() {
    toast("Este proyecto cambió en otra ventana: tus últimos cambios no se guardaron.", "alert", false, [
      ["Sobrescribir", () => { SAVE.conflict = false; FX.saveNow(true); }],
      ["Recargar", () => { location.reload(); }]], true);
  }
  /** Vaciado al salir: fetch keepalive (si cabe) para no perder los ultimos 1-2 s de edicion. */
  FX.flushOnExit = function () {
    if (!SAVE.dirty || SAVE.off || SAVE.conflict || typeof JOB === "undefined" || !JOB || JOB === "demo") return;
    const body = JSON.stringify(Object.assign({ plan: PLAN }, FX.serverRev != null ? { base_rev: FX.serverRev } : {}));
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
    m.classList.add("on");
    $("#pvmMsg").textContent = "Preparando la vista previa real (540p con audio)…"; setBar("#pvmFill", 2); $("#pvmCancel").style.display = "";
    const body = isPro() ? Object.assign(engineOpts(), { plan: planForServer(), pro: true, fmt: FMT || "9:16", format: FMT || "9:16" })
      : Object.assign({ plan: PLAN, full: true }, typeof renderOpts === "function" ? renderOpts() : {});
    PV.t0 = Date.now(); PV.seenActive = false;
    try {
      const d = await jpost(`/preview/${JOB}`, body);
      if (d.file) return playPreview(d.file);
      PV.polling = true; pollPreview();
    } catch (e) {
      $("#pvmMsg").textContent = `No se pudo generar la vista previa: ${e.message || e}`; $("#pvmCancel").style.display = "none";
    }
  };
  async function pollPreview() {
    if (!PV.polling) return;
    let j; try { j = await jget(`/status/${JOB}?plan=0`); } catch (e) { return setTimeout(pollPreview, 1200); }
    const pv = j.preview || {};
    const st = j.preview_state || pv.state || (j.preview_file ? "done" : "");
    // no aceptar el resultado de una vista previa anterior
    const fresh = PV.seenActive || (pv.updated && pv.updated * 1000 >= PV.t0 - 1500);
    if (st === "queued" || st === "running") PV.seenActive = true;
    const pct = j.preview_pct != null ? +j.preview_pct : +(pv.pct || 0);
    setBar("#pvmFill", Math.max(2, pct || 0));
    const msg = j.preview_message || pv.message;
    $("#pvmMsg").textContent = st === "queued" ? (msg || "En cola…") : `${msg || stageLabel(j.preview_stage || pv.stage) || "Renderizando"}${pct ? ` · ${Math.round(pct)}%` : ""}`;
    if (fresh && st === "done" && (j.preview_file || pv.file)) { PV.polling = false; return playPreview(j.preview_file || pv.file); }
    if (fresh && (st === "error" || st === "canceled")) {
      PV.polling = false; $("#pvmCancel").style.display = "none";
      $("#pvmMsg").textContent = st === "canceled" ? "Vista previa cancelada." : `La vista previa falló: ${j.preview_error || pv.error || ""}`;
      return;
    }
    setTimeout(pollPreview, 800);
  }
  function playPreview(file) {
    const vid = $("#pvmVid"); const url = /^\/|^https?:/.test(file) ? file : `/video/${encodeURIComponent(file)}`;
    vid.src = url + (url.includes("?") ? "&" : "?") + "v=" + Date.now(); vid.classList.add("on");
    setBar("#pvmFill", 100); $("#pvmMsg").textContent = "Vista previa real lista: es el mismo motor del export, a 540p."; $("#pvmCancel").style.display = "none";
    vid.play().catch(() => {});
  }
  FX.closePreview = function () { PV.polling = false; const m = $("#pvm"); m.classList.remove("on"); const v = $("#pvmVid"); v.pause(); };
  FX.cancelPreview = async function () { PV.polling = false; try { await fetch(`/cancel/${JOB}?only=preview`, { method: "POST" }); } catch (e) { /* nada */ } $("#pvmMsg").textContent = "Vista previa cancelada."; $("#pvmCancel").style.display = "none"; };
  function setBar(sel, pct) { const el = $(sel); if (el) el.style.width = Math.max(2, Math.min(100, pct)) + "%"; }

  // ------------------------------------------------------------------ export por etapas
  function stageLabel(s) { const f = STAGES.find(x => x[0] === s); return f ? f[1] : (s ? String(s) : ""); }
  FX.renderBody = function () {
    const base = typeof renderOpts === "function" ? renderOpts() : {};
    return Object.assign({}, base, { plan: planForServer(), fmt: FMT || "9:16", format: FMT || "9:16", shorts: true });
  };
  FX.renderStatus = function (j) {
    const box = $("#r-stages"); if (!box) return;
    let cur = j.stage || null;
    if (cur !== "shorts" && /short/i.test(j.message || "") && (cur === "mux" || cur === "done")) cur = "shorts";
    const order = STAGES.map(s => s[0]);
    const ci = order.indexOf(cur);
    box.innerHTML = isPro() || cur ? STAGES.filter(s => s[0] !== "director" || cur === "director").map(([k, l], i) => {
      const idx = order.indexOf(k);
      const cls = cur === k ? "cur" : (ci >= 0 && idx < ci ? "done" : "");
      return `<span class="stg ${cls}"><span class="d"></span>${l}</span>`;
    }).join("") : "";
    const w = $("#r-warn");
    if (w) w.innerHTML = (j.warnings || []).map(x => `<div>${ICO("alert")}<span>${esc(typeof x === "string" ? x : JSON.stringify(x))}</span></div>`).join("");
  };
  FX.cancelRender = async function () {
    const b = $("#r-cancel"); if (b) { b.disabled = true; b.innerHTML = `<span class="spinner"></span> Cancelando…`; }
    window.RENDER_CANCELED = true;
    try { await fetch(`/cancel/${JOB}`, { method: "POST" }); } catch (e) { /* el poll lo resolvera */ }
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

  // ------------------------------------------------------------------ inicio del editor
  FX.init = function () {
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
