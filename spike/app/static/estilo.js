/* Reelfy Studio: pestaña "Estilo" (claridad de ajustes).
   Cada ajuste dice que cambia y muestra un ejemplo con el video del usuario:
   - HelpRow: una fila = nombre + que hace + ejemplo + control + nivel (Basico / Avanzado).
   - Segmented / TriState: opciones humanas de ancho igual ("Segun el estilo · Si · No").
   - StyleGallery: los estilos de subtitulos animando TUS palabras sobre TU cuadro (capdraw.js).
   - ColorWhere: cada fila de color con un dibujo de DONDE va ese color.
   - AccentPreview: el color de tu marca en cuatro vistas vivas (subtitulo, titulo, tarjeta, boton).
   - LookPicker: los seis estilos de color hechos por el motor con TU cuadro (POST /fx/look_thumbs),
     mantener presionado muestra el original; "Comparar con el original" en la vista grande; los
     ajustes finos con ejemplos del motor (Menos · Así · Más). La vista grande y los ejemplos aplican
     el color del export con LookGL (tablas de POST /fx/look_luts), no un filtro CSS.
   - Ticker: un solo requestAnimationFrame para todo lo animado (30 fps en tarjetas, sin dibujar
     lo que no se ve, quieto con "reducir movimiento").
   - Edicion con IA, Musica, Quien sale y tu logo, y Formato, idioma y audio viven en
     estilo_proyecto.js (E.addSection); aqui quedan el armazon, las piezas comunes (E._k) y el
     resumen del panel Proyecto.
   Toda la copy viene de ui_copy.json (/fx/schema -> ui). Toda escritura al plan pasa por FX.edit
   (un paso de deshacer) o FX.gesture (arrastres y "Otro color").
   Depende de FXCore, CapDraw, LookGL y FX; lee los globales del script de index.html (PLAN, V, FMT, P,
   CL...) solo dentro de funciones. */
(function () {
  "use strict";
  const C = window.FXCore;
  const E = (window.Estilo = {});
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => [...(el || document).querySelectorAll(s)];
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICO = n => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  const CHK = '<svg class="chk" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const DPR = () => Math.min(2, window.devicePixelRatio || 1);
  const REDUCED = () => { try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; } };
  const lsGet = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* sin almacenamiento: solo esta vista */ } };
  let UID = 0;
  const uid = p => `${p || "es"}${++UID}`;
  const hasPlan = () => typeof PLAN !== "undefined" && PLAN && PLAN.fx && window.FX && FX.isPro && FX.isPro();

  // ------------------------------------------------------------------ copy (ui_copy.json)
  const UI = () => (window.FX && FX.uiCopy && FX.uiCopy()) || {};
  /** Texto fijo de la interfaz (ui.strings) con {variables}. */
  function T(k, vars) {
    let s = (UI().strings || {})[k];
    if (typeof s !== "string") return "";
    if (vars) s = s.replace(/\{(\w+)\}/g, (m, x) => (vars[x] != null ? String(vars[x]) : m));
    return s;
  }
  const copy = (id, ctx) => C.copyFor(UI(), id, ctx);
  const optLabel = (path, v) => { const o = (UI().options || {})[path]; return (o && o[v] && o[v].label) || ""; };
  E.UICopy = { get: UI, copy, t: T, optLabel };
  E.t = T;

  // ------------------------------------------------------------------ nivel (Basico / Avanzado)
  E.level = () => (lsGet("reelfy.level") === "advanced" ? "advanced" : "basic");
  E.setLevel = function (lv) {
    // el foco sigue en el selector (Estilo o inspector) aunque se redibuje
    const ae = document.activeElement, inIns = !!(ae && ae.closest && ae.closest("#inspBody .lvlsw")), inSt = !!(ae && ae.closest && ae.closest("#stCol .lvlsw"));
    lsSet("reelfy.level", lv === "advanced" ? "advanced" : "basic");
    renderAll();
    // el inspector de los elementos (titulos, graficos, efectos...) tambien cambia de nivel
    if (window.FX && FX.refreshInspector) FX.refreshInspector(true);
    if (inIns || inSt) { const b = $(`${inIns ? "#inspBody" : "#stCol"} .lvlsw [aria-checked="true"]`); if (b) b.focus({ preventScroll: true }); }
  };

  // ------------------------------------------------------------------ estado del proyecto
  const cap = () => { PLAN.fx.captions = PLAN.fx.captions || {}; return PLAN.fx.captions; };
  E.liveAccent = null;                                  // color mientras se arrastra "Otro color"
  const brand = () => E.liveAccent || C.projectAccent(PLAN);
  const cardOf = () => C.captionCardOf(cap());
  const presetName = k => optLabel("captions.preset", k);
  /** Configuracion efectiva de subtitulos con otro estilo (para tarjetas y vista previa). */
  function effCfg(preset) {
    const c = Object.assign({}, cap(), { preset: preset === "none" ? cap().preset : preset, enabled: preset !== "none" });
    if (E.liveAccent) {
      if (pillLinked()) c.highlight = null;
      c.accent = E.liveAccent;
    }
    return c;
  }
  /* La pastilla sigue a la marca con highlight vacio. Quitar la palomita escribe la marca misma (no
     cambia nada a la vista), prende las muestras y marca la pastilla como propia EN EL PLAN
     (plan.studio.pillOwn): se guarda con el proyecto, ⌘Z la deshace y cambiar la marca ya no la
     recolorea (FXCore.applyAccent). */
  const pillLinked = () => C.highlightLinked(cap(), C.projectAccent(PLAN), C.pillOwn(PLAN));
  /** Escribe el color de la pastilla y si es propia, en UN paso de deshacer. */
  function setPill(hex, own) {
    FX.edit(() => { cap().highlight = hex; C.setPillOwn(PLAN, own); }, { inspector: false });
    E.refreshSoon(0);
  }
  /** Escribe en fx.captions como UN paso de deshacer (sin reconstruir el inspector: se actualiza en sitio). */
  function editCaps(fn) {
    FX.edit(() => fn(cap()), { inspector: false });
    E.refreshSoon(0);
  }
  E.editCaps = editCaps;

  // ------------------------------------------------------------------ Ticker: un solo lazo de animacion
  const Ticker = E.Ticker = (function () {
    const items = new Set();
    const vis = new WeakMap(), byEl = new WeakMap();
    let raf = 0, frozen = null, io = null;
    const stats = { frames: 0, draws: {} };
    // al volver a la vista, un ejemplo quieto se redibuja (pudo cambiar mientras no se veia)
    if (typeof IntersectionObserver !== "undefined") io = new IntersectionObserver(es => {
      let any = false;
      es.forEach(e => { vis.set(e.target, e.isIntersecting); const it = byEl.get(e.target); if (e.isIntersecting && it) { it.dirty = true; any = true; } });
      if (any && !raf) raf = requestAnimationFrame(loop);
    }, { threshold: 0 });
    /** Tiempo de los ejemplos: la ventana de palabras de muestra en bucle (tiempo original). */
    function time(ts) {
      const w = Frames.win;
      if (!w) return 0;
      const a = w.t0 - 0.1, period = Math.max(1.2, (w.t1 - w.t0) + 0.6);
      return a + ((ts / 1000) % period);
    }
    function loop(ts) {
      raf = 0;
      if (document.hidden) return;
      const alive = step(ts);
      if (alive) raf = requestAnimationFrame(loop);
    }
    /** Una pasada: dibuja lo que toca. -> true si queda algo vivo. */
    function step(ts) {
      let alive = false;
      stats.frames++;
      items.forEach(it => {
        if (!it.el.isConnected) { items.delete(it); if (io) io.unobserve(it.el); return; }
        alive = true;
        if (it.el.offsetParent === null) return;                     // pestana o seccion escondida
        if (io && vis.get(it.el) === false && !it.eager) return;     // fuera de la vista (salvo los ejemplos chicos y lentos)
        const fps = it.fps || 30;
        if (ts - (it.last || 0) < 1000 / fps - 2) return;
        if (it.static && !it.dirty) return;                          // ejemplo quieto: solo si algo cambio
        const still = REDUCED() && !it.el.matches(":hover") && !it.el.contains(document.activeElement);
        const t = frozen != null ? frozen : still ? (it.still ? it.still() : stillTime()) : time(ts);
        if ((still || frozen != null) && it.lastT === t && !it.dirty) return;
        it.last = ts; it.lastT = t; it.dirty = false;
        try { it.draw(t, ts); } catch (e) { if (!it.err) { it.err = true; console.warn("Estilo: no se pudo dibujar un ejemplo", e); } }
        stats.draws[it.name || "x"] = (stats.draws[it.name || "x"] || 0) + 1;
      });
      return alive;
    }
    const kick = () => { items.forEach(it => { it.dirty = true; }); if (!raf) raf = requestAnimationFrame(loop); };
    document.addEventListener("visibilitychange", () => { if (!document.hidden) kick(); });
    // una fuente que termina de cargar cambia las medidas: todo se vuelve a dibujar
    try { document.fonts.addEventListener("loadingdone", () => kick()); } catch (e) { /* sin FontFaceSet */ }
    return {
      add(it) { items.add(it); byEl.set(it.el, it); if (io) io.observe(it.el); kick(); return it; },
      remove(it) { items.delete(it); if (io && it) io.unobserve(it.el); },
      kick, time,
      /** Gancho de pruebas (WebKit sin cuadros de animacion): una pasada ya, sin esperar rAF. */
      // (a la hora real: una hora futura dejaba `last` adelantado y el lazo normal no volvia a dibujar)
      flush() { items.forEach(it => { it.dirty = true; it.last = -Infinity; }); step(performance.now()); },
      /** Gancho de pruebas: congela el tiempo de todos los ejemplos (null lo suelta). */
      setTime(t) { frozen = t == null ? null : +t; kick(); },
      frozen: () => frozen,
      stats: () => JSON.parse(JSON.stringify(stats)),
      resetStats() { stats.frames = 0; stats.draws = {}; },
    };
  })();
  /** Cuadro quieto (reducir movimiento): a la mitad de la segunda palabra (la pastilla se ve). */
  function stillTime() {
    const w = Frames.win; if (!w || !w.words.length) return 0;
    const x = w.words[Math.min(1, w.words.length - 1)];
    return (x.start + x.end) / 2;
  }

  // ------------------------------------------------------------------ Frames: cuadro y palabras de muestra
  /* Un solo cuadro de muestra para todos los ejemplos: la toma de fondo mas oscuro en la franja de
     los subtitulos (FXCore.pickSampleShot), tomado de un <video> escondido con la misma fuente que
     el reproductor (el tiempo del reproductor nunca se toca). Sin video: un degradado neutro. */
  const Frames = E.Frames = { img: null, win: null, key: "", focusX: 0.5, neutral: true, pending: null, layers: new Map(),
    t: 0, shot: 0, face: null, img2: null, other: null };
  const srcOf = () => (typeof V !== "undefined" && V && (V.currentSrc || V.getAttribute("src"))) || "";
  const phrases = () => (typeof P === "function" ? P() : PLAN.phrases) || [];
  function frameKey() {
    const cuts = (PLAN.cuts || []).filter(c => c && c.enabled !== false).map(c => [c.start, c.end]);
    return JSON.stringify([typeof JOB !== "undefined" ? JOB : "", srcOf(), cuts, (PLAN.shots || []).map(s => [s.start, s.end]),
      Math.round((cap().y == null ? 0.72 : +cap().y) * 16), phrases().length, typeof LANG !== "undefined" ? LANG : "es"]);
  }
  let GRAB = null, GRAB_Q = Promise.resolve();
  /** Un cuadro del video en t (en cola: un solo <video> escondido, una captura a la vez). */
  function grabAt(src, t) { const r = GRAB_Q.then(() => grabOne(src, t)); GRAB_Q = r.catch(() => null); return r; }
  function grabOne(src, t) {
    return new Promise(resolve => {
      if (!src) { resolve(null); return; }
      if (!GRAB) {
        // en el documento (invisible): WebKit (la app de escritorio) decodifica cuadros de un <video> conectado
        GRAB = document.createElement("video"); GRAB.muted = true; GRAB.preload = "auto"; GRAB.playsInline = true; GRAB.setAttribute("aria-hidden", "true");
        GRAB.setAttribute("tabindex", "-1"); GRAB.className = "st-grab";
        document.body.appendChild(GRAB);
      }
      const v = GRAB;
      let done = false;
      const fin = ok => {
        if (done) return; done = true; clearTimeout(to);
        v.removeEventListener("seeked", onSeek); v.removeEventListener("loadeddata", onLoad); v.removeEventListener("error", onErr);
        if (!ok || !v.videoWidth) { resolve(null); return; }
        const s = Math.min(1, 960 / Math.max(v.videoWidth, v.videoHeight));
        const cv = document.createElement("canvas"); cv.width = Math.round(v.videoWidth * s); cv.height = Math.round(v.videoHeight * s);
        // WebKit (la app de escritorio): el cuadro de un <video> pausado recien buscado puede no llegar
        // al lienzo (sale negro, medido en el proyecto real); otro seek con la tuberia ya despierta si lo
        // trae. Se reintenta hasta 6 veces (un cuadro negro de verdad tambien llega, solo un poco despues)
        let tries = 0;
        const shoot = () => {
          try { cv.getContext("2d").drawImage(v, 0, 0, cv.width, cv.height); } catch (e) { resolve(null); return; }
          if (tries++ < 6 && blankCanvas(cv)) {
            let moved = false;
            const again = () => { if (moved) return; moved = true; v.removeEventListener("seeked", again); nextFrame(v, shoot); };
            v.addEventListener("seeked", again); setTimeout(again, 400);
            try { v.currentTime = Math.max(0, t) + tries * 0.001; } catch (e) { again(); }
            return;
          }
          resolve(cv);
        };
        shoot();
      };
      const onSeek = () => fin(true);
      const onErr = () => fin(false);
      // (WebKit: pedir un cuadro y dibujar una vez antes del seek despierta la tuberia de cuadros)
      const onLoad = () => { primeVideo(v); try { v.currentTime = Math.max(0, t); } catch (e) { fin(false); } };
      const to = setTimeout(() => fin(!!v.videoWidth && v.readyState >= 2), 6000);
      v.addEventListener("seeked", onSeek); v.addEventListener("error", onErr);
      if (v.getAttribute("src") !== src) { v.addEventListener("loadeddata", onLoad); v.src = src; v.load(); }
      else if (v.readyState >= 1) { if (Math.abs(v.currentTime - t) < 0.01 && v.readyState >= 2) fin(true); else onLoad(); }
      else v.addEventListener("loadeddata", onLoad);
    });
  }
  /** El lienzo quedo todo negro (muestreado): la captura del <video> aun no traia el cuadro. */
  function blankCanvas(cv) {
    try {
      const d = cv.getContext("2d", { willReadFrequently: true }).getImageData(0, 0, cv.width, cv.height).data;
      for (let i = 0; i < d.length; i += 4 * 97) if (d[i] > 3 || d[i + 1] > 3 || d[i + 2] > 3) return false;
      return true;
    } catch (e) { return false; }
  }
  /** `f` cuando el <video> presenta un cuadro (o a los 120 ms, lo que llegue primero). */
  function nextFrame(v, f) {
    let fired = false;
    const go = () => { if (!fired) { fired = true; f(); } };
    if (v.requestVideoFrameCallback) { try { v.requestVideoFrameCallback(go); } catch (e) { /* sin el */ } }
    setTimeout(go, 120);
  }
  function primeVideo(v) {
    try { if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(() => {}); } catch (e) { /* sin el */ }
    try { const c = document.createElement("canvas"); c.width = 2; c.height = 2; c.getContext("2d").drawImage(v, 0, 0, 2, 2); } catch (e) { /* aun sin cuadro */ }
  }
  E.blankCanvas = blankCanvas;
  /** Asegura el cuadro y las palabras de muestra (asincrono; los ejemplos se dibujan al llegar). */
  Frames.ensure = function () {
    if (!hasPlan()) return Promise.resolve(null);
    const key = frameKey();
    if (key === Frames.key && (Frames.img || Frames.pending)) return Frames.pending || Promise.resolve(Frames.img);
    Frames.key = key;
    const capY = cap().y == null ? 0.72 : +cap().y;
    const pick = C.pickSampleShot(Object.assign({}, PLAN, { phrases: phrases() }), capY);
    Frames.win = C.sampleWindow(phrases(), PLAN.cuts, pick.t0, 2.6);
    Frames.layers.clear();
    const tg = pick.t0 + 0.3;
    const cam = PLAN.camera && PLAN.camera.length ? PLAN.camera[Math.min(PLAN.camera.length - 1, Math.max(0, Math.floor(tg * 30)))] : null;
    Frames.focusX = cam != null && isFinite(+cam) ? +cam : 0.5;
    Frames.t = tg; Frames.shot = pick.index; Frames.face = shotFace(pick.index);
    Frames.other = C.otherShot(PLAN, pick.index); Frames.img2 = null;
    const src = srcOf();
    const job = Frames.pending = grabAt(src, tg).then(img => {
      if (Frames.key !== key) return Frames.img;
      Frames.img = img; Frames.neutral = !img; Frames.pending = null; Frames.layers.clear();
      Frames.bright = !!img && bandLuma(img, capY) > 170;
      Ticker.kick(); syncPreviewNote();
      lookSoon(0);
      // la otra toma (ejemplo de «Igualar el color entre tomas»), sin prisa
      if (img && Frames.other) grabAt(src, Frames.other.t).then(im2 => { if (Frames.key === key) { Frames.img2 = im2; Ticker.kick(); } });
      return img;
    });
    if (!src) { Frames.img = null; Frames.neutral = true; }
    Groups.reset();
    Ticker.kick();
    return job;
  };
  /** Luminancia media (0-255) de la franja de los subtitulos en el cuadro de muestra. */
  function bandLuma(img, y) {
    try {
      const c = img.getContext("2d"), h = img.height, y0 = Math.max(0, Math.floor((y - 0.06) * h)), hh = Math.max(1, Math.floor(0.12 * h));
      const d = c.getImageData(0, y0, img.width, Math.min(hh, h - y0)).data;
      let s = 0, n = 0; for (let i = 0; i < d.length; i += 16) { s += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]; n++; }
      return n ? s / n : 0;
    } catch (e) { return 0; }
  }
  /** Proporcion del formato del proyecto (ancho / alto). */
  function fmtAspect() {
    const f = typeof FMT !== "undefined" ? FMT : "9:16";
    const R = { "9:16": 9 / 16, "1:1": 1, "4:5": 4 / 5, "16:9": 16 / 9 };
    if (R[f]) return R[f];
    if (typeof V !== "undefined" && V && V.videoWidth) return V.videoWidth / V.videoHeight;
    return 9 / 16;
  }
  /** La cara de una toma del plan ([cx, cy, w, h] del cuadro del video) o null. */
  function shotFace(i) {
    const sh = (PLAN.shots || []).filter(x => x && isFinite(+x.start))[i];
    const f = sh && Array.isArray(sh.face) ? sh.face.map(Number) : null;
    return f && f.length >= 4 && f.every(isFinite) ? f.slice(0, 4) : null;
  }
  /** Recorte que cubre W x H (el de FX.drawCover): rectangulo fuente en pixeles del cuadro. */
  const coverRect = (img, W, H, focusX) => FX.coverRect(img, W, H, focusX);
  /** La cara del cuadro en las coordenadas del recorte (0..1). */
  const faceIn = (r, f) => (f ? [(f[0] * r.iw - r.sx) / r.sw, (f[1] * r.ih - r.sy) / r.sh, f[2] * r.iw / r.sw, f[3] * r.ih / r.sh] : null);
  let LGI = null;
  /** `img` cubriendo W x H con el color del export `p` (LookGL; sin WebGL, FXCore.applyLook). */
  function gradeInto(c, img, W, H, p, t, face) {
    const r = coverRect(img, W, H, Frames.focusX), fc = faceIn(r, face);
    if (window.LookGL) {
      if (!LGI) LGI = LookGL.create();
      if (LGI.payload() !== p) LGI.setLut(p);
      if (LGI.draw(c, img, r.sx, r.sy, r.sw, r.sh, 0, 0, W, H, t, { face: fc })) return;
    }
    c.drawImage(img, r.sx, r.sy, r.sw, r.sh, 0, 0, W, H);
    try { const d = c.getImageData(0, 0, W, H); C.applyLook(p, d.data, W, H, { t, face: fc }); c.putImageData(d, 0, 0); } catch (e) { /* lienzo sin lectura: sin color */ }
  }
  /** Capa del cuadro (cubre W x H) con el color del export, cacheada por tamano y color.
      look: undefined = el del proyecto; null = como salio de la camara; un nombre = ese estilo. */
  function frameLayer(W, H, look) {
    const p = lookPay(look);
    const k = `${W}x${H}|${Frames.key}|${Frames.img ? 1 : 0}|${payId(p)}`;
    let cv = Frames.layers.get(k);
    if (cv) return cv;
    cv = document.createElement("canvas"); cv.width = Math.max(2, W); cv.height = Math.max(2, H);
    const c = cv.getContext("2d");
    if (Frames.img) {
      if (p) gradeInto(c, Frames.img, W, H, p, Frames.t, Frames.face);
      else FX.drawCover(c, Frames.img, 0, 0, W, H, Frames.focusX);
      if (Frames.bright) { const g = c.createLinearGradient(0, H, 0, H * 0.6); g.addColorStop(0, "rgba(0,0,0,.35)"); g.addColorStop(1, "rgba(0,0,0,0)"); c.fillStyle = g; c.fillRect(0, H * 0.6, W, H * 0.4); }
    } else { const g = c.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#3a404c"); g.addColorStop(1, "#14171d"); c.fillStyle = g; c.fillRect(0, 0, W, H); }
    if (Frames.layers.size > 40) Frames.layers.clear();
    Frames.layers.set(k, cv);
    return cv;
  }

  // ------------------------------------------------------------------ color del video: tablas del motor
  /* El color del proyecto lo pide fx.js (FX.lookPayload: POST /fx/look_luts con la correccion de cada
     toma). Para la vista al pasar el mouse por un estilo de color, aqui se piden los seis con los
     ajustes finos de ahora (una sola llamada). */
  const LK = { set: {}, setKey: "", seq: 0, ids: new WeakMap(), n: 0 };
  const gcfg = () => { PLAN.fx.grade = PLAN.fx.grade || {}; return PLAN.fx.grade; };
  const curLook = () => gcfg().look || "warm_clean";
  const lookName = k => optLabel("grade.look", k);
  function payId(p) { if (!p) return "o"; let i = LK.ids.get(p); if (!i) { i = ++LK.n; LK.ids.set(p, i); } return "p" + i; }
  /** Tablas para dibujar: null = original; nombre = ese estilo; undefined = el color del proyecto. */
  function lookPay(look) {
    if (look === null) return null;
    const cur = window.FX && FX.lookPayload && FX.lookPayload(), on = cur && cur.enabled !== false ? cur : null;
    if (typeof look === "string") return on && on.look === look ? on : LK.set[look] || on;   // el elegido, con sus tablas por toma
    return on;
  }
  async function ensureLookSet() {
    if (typeof JOB === "undefined" || !JOB || JOB === "demo") return;
    const g = Object.assign({}, gcfg()), corr = sampleCorr(); delete g.look; delete g.enabled;
    const key = JSON.stringify([JOB, g, corr]);
    if (key === LK.setKey) return;
    LK.setKey = key; const seq = ++LK.seq;
    try {
      const r = await fetch(`/fx/look_luts/${JOB}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ grade: g, looks: C.LOOK_ORDER, corr }) });
      const d = r.ok ? await r.json() : null;
      if (seq !== LK.seq) return;
      if (d && d.looks) { LK.set = d.looks; if (PV.hoverLook) { Frames.layers.clear(); Ticker.kick(); } } else LK.setKey = "";
    } catch (e) { if (seq === LK.seq) LK.setKey = ""; }
  }

  // ------------------------------------------------------------------ grupos de subtitulos por estilo
  /* Los grupos REALES del export para cada estilo (POST /captions con el estilo cambiado, uno por
     estilo, despues de la primera pintada y sin prisa); mientras llegan, los aproximados con el
     max_words efectivo del estilo. Asi "Palabra en color" muestra su palabra clave. */
  const Groups = E.Groups = {
    srv: {}, ap: new Map(), queue: [], busy: false, failed: new Set(), sig: "",
    reset() { this.ap.clear(); this.sig = ""; },
    /** Cortes, tomas, formato, estilo e idioma (se recalcula en cada refresco, no en cada cuadro). */
    planSig() {
      if (!this.sig) this.sig = JSON.stringify([(PLAN.cuts || []).map(x => [x.start, x.end, x.enabled !== false]), (PLAN.shots || []).map(s => [s.start, s.end]),
        typeof FMT !== "undefined" ? FMT : "9:16", (PLAN.director || {}).style, typeof LANG !== "undefined" ? LANG : "es"]);
      return this.sig;
    },
    /** Solo lo que cambia los grupos o su caja (los colores no): cambiar un color no vuelve a pedirlos;
        la altura si (la caja del export viene en su lugar). */
    keyOf(preset) {
      const k = cap();
      return JSON.stringify([preset, k.y, k.size, k.font, k.case, k.max_words, k.max_lines, k.x, k.kw_accent]) + this.planSig();
    },
    get(preset) {
      const w = Frames.win; if (!w || !w.words.length || preset === "none") return { groups: [], srv: false };
      const e = this.srv[preset];
      if (e && e.key === this.keyOf(preset)) {
        const gs = e.groups.filter(g => +g.end > w.t0 - 0.05 && +g.start < w.t1 + 0.05);
        if (gs.length) return { groups: gs, srv: true };
      }
      if (!e || e.key !== this.keyOf(preset)) this.want(preset, this.keyOf(preset));
      const k = preset + "|" + JSON.stringify([cap().max_words, Frames.key]);
      if (!this.ap.has(k)) { if (this.ap.size > 40) this.ap.clear(); this.ap.set(k, C.captionGroupsFor([w.words], effCfg(preset), FX.capTitleFont(PLAN))); }
      return { groups: this.ap.get(k), srv: false };
    },
    want(preset, key) {
      if (typeof JOB === "undefined" || !JOB || JOB === "demo" || this.queue.includes(preset) || this.failed.has(key)) return;
      this.queue.push(preset);
      if (!this.busy) { this.busy = true; setTimeout(() => this.next(), 400); }
    },
    async next() {
      const preset = this.queue.shift();
      if (!preset) { this.busy = false; return; }
      this.busy = true;
      try {
        const key = this.keyOf(preset);
        // copia superficial: el plan del proyecto NUNCA se toca (planForServer puede devolver PLAN mismo)
        const base = FX.planForServer();
        const plan = Object.assign({}, base, { fx: Object.assign({}, base.fx, { captions: Object.assign({}, base.fx.captions, { preset, enabled: true }) }) });
        const r = await fetch(`/captions/${JOB}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan, fmt: typeof FMT !== "undefined" ? FMT : "9:16" }) });
        const g = r.ok ? await r.json() : null;
        if (Array.isArray(g)) { this.srv[preset] = { key, groups: g }; Ticker.kick(); } else this.failed.add(key);
      } catch (e) { /* sin servidor: los aproximados */ }
      setTimeout(() => this.next(), 120);
    },
  };

  /** Dibuja la escena de muestra (cuadro con su color + subtitulos de `preset` en t) en un lienzo
      W x H del formato del proyecto, desplazada `dy` px hacia arriba (look: ver frameLayer). -> caja
      de los subtitulos o null. */
  function drawScene(c, W, H, t, preset, dy, look) {
    c.save(); c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, c.canvas.width, c.canvas.height);
    c.translate(0, -(dy || 0));
    c.drawImage(frameLayer(W, H, look), 0, 0);
    let bb = null;
    if (preset !== "none") {
      const B = FX.layoutBox(W, H), cfg = effCfg(preset), g = Groups.get(preset);
      const gr = g.groups && C.capGroupAt(g.groups, t);
      if (gr) bb = CapDraw.drawGroup(c, B, gr, t, { cfg, accent: brand(), srv: g.srv, yFrac: cfg.y == null ? 0.72 : +cfg.y, rangeAlpha: 1,
        titleFont: FX.capTitleFont(PLAN), fontCss: FX.capFontCss, ensureFont: FX.ensureFont });
    }
    c.restore();
    return bb;
  }
  /** Tamano del lienzo de un canvas segun su caja en pantalla (x DPR, hasta 2). */
  function fitCanvas(cv) {
    const r = cv.getBoundingClientRect(), d = DPR();
    const w = Math.max(2, Math.round(r.width * d)), h = Math.max(2, Math.round(r.height * d));
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    return cv;
  }

  // ------------------------------------------------------------------ teclado en grupos de opciones
  function radioKeys(group, onMove) {
    group.addEventListener("keydown", e => {
      if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
      const btns = $$('[role="radio"]', group).filter(b => !b.disabled);
      const i = btns.indexOf(document.activeElement);
      if (i < 0 || !btns.length) return;
      e.preventDefault(); e.stopPropagation();
      let j = i;
      if (e.key === "Home") j = 0; else if (e.key === "End") j = btns.length - 1;
      else j = (i + (e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : -1) + btns.length) % btns.length;
      btns.forEach(b => { b.tabIndex = -1; }); btns[j].tabIndex = 0; btns[j].focus();
      if (onMove) onMove(btns[j]);
    });
  }
  function roving(group) {
    const btns = $$('[role="radio"]', group);
    const on = btns.find(b => b.getAttribute("aria-checked") === "true" && !b.disabled) || btns.find(b => !b.disabled);
    btns.forEach(b => { b.tabIndex = b === on ? 0 : -1; });
  }

  // ------------------------------------------------------------------ HelpRow
  /* Una fila de ajuste. Contrato del DOM (las pruebas lo leen):
     .hrow[data-setting][data-level] > .hrow-ex[data-ex] (ejemplo), .hrow-txt > .hrow-lb > label +
     .lvl ("Avanzado" solo en filas avanzadas), .hrow-help, .hrow-note (si hay nota), .hrow-ctl. */
  const HelpRow = E.HelpRow = {
    create(o) {
      const el = document.createElement("div");
      const st = { o: Object.assign({}, o) };
      function build() {
        const op = st.o, cp = copy(op.id, op.ctx), exKind = op.exKind || cp.ex || "ninguno";
        const hasEx = op.example && exKind !== "ninguno" && exKind !== "en_video";
        el.className = `hrow ${op.layout === "narrow" ? "narrow" : "wide"}${op.full ? " full" : ""}${hasEx ? "" : " noex"}${hasEx && exKind === "texto" ? " extext" : ""}${op.cls ? " " + op.cls : ""}`;
        el.dataset.setting = op.id; el.dataset.level = cp.level;
        if (cp.missing) el.dataset.missing = "1"; else delete el.dataset.missing;
        el.innerHTML = "";
        const lid = op.labelId || uid("hl");
        if (hasEx) {
          const ex = document.createElement("div"); ex.className = "hrow-ex"; ex.dataset.ex = exKind;
          if (typeof op.example === "string") ex.textContent = op.example; else ex.appendChild(op.example);
          el.appendChild(ex);
        }
        const tx = document.createElement("div"); tx.className = "hrow-txt";
        const note = op.note !== undefined ? op.note : cp.note;
        tx.innerHTML = `<div class="hrow-lb"><label id="${lid}">${esc(op.label || cp.label)}</label>${cp.level === "advanced" ? `<span class="lvl">${esc(T("level.badge"))}</span>` : ""}</div>`
          + `<p class="hrow-help">${esc(op.help || cp.help)}</p>` + (note ? `<p class="hrow-note">${esc(note)}</p>` : "");
        el.appendChild(tx);
        const ctl = document.createElement("div"); ctl.className = "hrow-ctl";
        if (op.control) ctl.appendChild(op.control);
        el.appendChild(ctl);
        // la etiqueta nombra al control: input -> for; grupo de opciones -> aria-labelledby
        const lab = tx.querySelector("label"), c0 = op.control;
        if (c0) {
          const inp = /INPUT|SELECT|TEXTAREA/.test(c0.tagName) ? c0 : c0.querySelector && c0.querySelector('input:not([type="checkbox"]):not([type="color"]),select,textarea');
          const grp = c0.getAttribute && c0.getAttribute("role") === "radiogroup" ? c0 : c0.querySelector && c0.querySelector('[role="radiogroup"]');
          if (grp && !grp.getAttribute("aria-label")) grp.setAttribute("aria-labelledby", lid);
          else if (inp) { if (!inp.id) inp.id = uid("hc"); lab.htmlFor = inp.id; }
        }
      }
      build();
      return {
        el,
        update(p) { Object.assign(st.o, p || {}); build(); },
        destroy() { el.remove(); },
      };
    },
    /** Marcas estaticas con data-setting: llena etiqueta, que hace y nivel (Inicio, Montar, Limpieza).
        Cada fila usa SOLO sus partes directas (.hrow-txt, .hrow-ctl, .hrow-ex hijas): una fila que
        envuelve otras (p. ej. «Qué quitar» con un renglon por tipo de corte) nunca escribe su nombre
        en la primera fila de adentro; si no trae su propio .hrow-txt, se le crea. */
    hydrate(root) {
      const rows = root && root.matches && root.matches(".hrow[data-setting]") ? [root] : $$(".hrow[data-setting]", root || document);
      const own = (el, cls) => [...el.children].find(c => c.classList.contains(cls)) || null;
      rows.forEach(el => {
        const cp = copy(el.dataset.setting, {});
        el.dataset.level = cp.level;
        if (cp.missing) el.dataset.missing = "1"; else delete el.dataset.missing;
        let tx = own(el, "hrow-txt");
        if (!tx) { tx = document.createElement("div"); tx.className = "hrow-txt"; el.insertBefore(tx, el.firstChild); }
        if (!own(tx, "hrow-help")) {
          const lid = uid("hl");
          tx.insertAdjacentHTML("afterbegin", `<div class="hrow-lb"><label id="${lid}">${esc(cp.label)}</label>${cp.level === "advanced" ? `<span class="lvl">${esc(T("level.badge"))}</span>` : ""}</div><p class="hrow-help">${esc(cp.help)}</p>`);
          // la etiqueta nombra al control: un grupo de opciones por aria-labelledby, un campo por for
          // (solo los controles de ESTA fila, no los de las filas de adentro)
          const ctl = own(el, "hrow-ctl"), mine = x => x && x.closest(".hrow[data-setting]") === el ? x : null;
          const grp = ctl && mine($('[role="radiogroup"]', ctl)), inp = ctl && mine($('input:not([type="checkbox"]),select,textarea', ctl));
          if (grp && !grp.getAttribute("aria-label")) grp.setAttribute("aria-labelledby", lid);
          else if (inp && inp.id && !grp) $("label", tx).htmlFor = inp.id;
        }
        // el ejemplo escrito de la copy («Ej.: …») en su lugar, si la marca no trae uno propio
        const ex = own(el, "hrow-ex");
        if (ex && ex.dataset.ex === "texto" && !ex.textContent.trim() && cp.ex === "texto" && cp.ex_ui) ex.textContent = cp.ex_ui;
      });
    },
  };

  /** Textos de marcas estaticas desde la copy: data-s (texto), data-s-title y data-s-aria. */
  E.hydrateStrings = function (root) {
    if (!(UI().strings)) return;
    $$("[data-s]", root || document).forEach(el => { const s = T(el.dataset.s); if (s) el.textContent = s; });
    $$("[data-s-title]", root || document).forEach(el => { const s = T(el.dataset.sTitle); if (s) el.title = s; });
    $$("[data-s-aria]", root || document).forEach(el => { const s = T(el.dataset.sAria); if (s) el.setAttribute("aria-label", s); });
  };

  // ------------------------------------------------------------------ Segmented y TriState
  const sameV = (a, b) => (a == null && b == null) || (typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-9 : a === b);
  const Segmented = E.Segmented = {
    /** o = {choices: [{v, label, sample?, icon?, disabled?, title?}], value, nearest, onPick(v), aria} */
    create(o) {
      const el = document.createElement("div"); el.className = "seg-h" + (o.cls ? " " + o.cls : ""); el.setAttribute("role", "radiogroup");
      if (o.aria) el.setAttribute("aria-label", o.aria);
      const st = { o: Object.assign({}, o) };
      function draw() {
        const op = st.o, v = op.value;
        let on = op.choices.findIndex(c => sameV(c.v, v));
        if (on < 0 && op.nearest && typeof v === "number") {
          const n = C.nearestChoice(op.choices.filter(c => typeof c.v === "number").map(c => [c.v, c.label]), v);
          if (n) on = op.choices.findIndex(c => sameV(c.v, n[0]));
        }
        el.innerHTML = op.choices.map((c, i) => `<button type="button" role="radio" aria-checked="${i === on}" data-i="${i}"${c.disabled || op.disabled ? " disabled" : ""}${c.title ? ` title="${esc(c.title)}"` : ""}>`
          + (c.icon || c.sample ? `<span class="sg-smp" aria-hidden="true">${c.icon || c.sample}</span>` : "")
          + `<span class="sg-lb">${esc(c.label)}</span>${i === on ? CHK : ""}</button>`).join("");
        $$("button", el).forEach(b => { b.onclick = () => { const c = op.choices[+b.dataset.i]; if (c && !c.disabled && op.onPick) op.onPick(c.v); }; });
        roving(el);
      }
      radioKeys(el);
      draw();
      return { el, update(p) { const f = document.activeElement && el.contains(document.activeElement) ? +document.activeElement.dataset.i : null; Object.assign(st.o, p || {}); draw(); if (f != null) { const b = $(`[data-i="${f}"]`, el); if (b) b.focus(); } }, destroy() { el.remove(); } };
    },
  };
  E.TriState = {
    /** «Según el estilo · Sí · No»: null = lo que dicta el estilo (nunca un interruptor que se ve
        apagado cuando el estilo lo tiene activo). */
    create(o) {
      return Segmented.create({ choices: [{ v: null, label: T("tri.auto") }, { v: true, label: T("tri.yes") }, { v: false, label: T("tri.no") }],
        value: o.value == null ? null : !!o.value, onPick: o.onPick, aria: o.aria, disabled: o.disabled });
    },
  };
  /** Selector de dos niveles en el encabezado de Estilo y del inspector. */
  E.LevelSwitch = {
    create() {
      const s = Segmented.create({ choices: [{ v: "basic", label: T("level.basic") }, { v: "advanced", label: T("level.advanced") }],
        value: E.level(), onPick: v => E.setLevel(v), aria: T("level.aria"), cls: "lvlsw" });
      return s;
    },
  };

  // ------------------------------------------------------------------ dibujos de ejemplo (SVG)
  const GREY = "#7f899c";
  const lumOf = hex => { const h = C.normHex(hex) || "#FFFFFF", n = parseInt(h.slice(1), 16); return (0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255; };
  /** «ya está en» con las palabras que pinta este color en ese color (y subrayadas): las de
      FXCore.colorWhereWords (en «Discreto», el texto son las tres palabras). */
  function whereSvg(role, col, key, preset) {
    const xs = [12, 33, 66], y = 32, words = ["ya", "está", "en"], span = [[11, 28], [32, 62], [65, 82]];
    const bg = lumOf(col) < 0.32 ? "#d7dce6" : "#1d222d";
    const grey = lumOf(col) < 0.32 ? "#5d6678" : GREY;
    const txt = (i, fill) => `<text x="${xs[i]}" y="${y}" fill="${fill}" font-family="Poppins, Helvetica, Arial, sans-serif" font-weight="800" font-size="13">${words[i]}</text>`;
    const on = C.colorWhereWords(role, preset);
    let under = "", body = "", marks = [];
    if (role === "text" || role === "active") { body = [0, 1, 2].map(i => txt(i, on.includes(i) ? col : grey)).join(""); marks = on.length === 3 ? [[11, 82]] : on.map(i => span[i]); }
    else if (role === "pill") { under = `<rect x="29" y="17" width="35" height="21" rx="6" fill="${col}"/>`; body = txt(0, grey) + txt(1, C.contrastOn(col)) + txt(2, grey); marks = [[29, 64]]; }
    else if (role === "fill") {
      under = `<defs><linearGradient id="cwf${key}" x1="0" x2="1"><stop offset=".55" stop-color="${col}"/><stop offset=".55" stop-color="${grey}"/></linearGradient></defs>`;
      body = txt(0, col) + txt(1, `url(#cwf${key})`) + txt(2, grey); marks = [[11, 62]];
    }
    const lines = marks.map(([a, b]) => `<line x1="${a}" x2="${b}" y1="45" y2="45" stroke="${lumOf(col) < 0.32 ? "#3a404c" : "#eceff5"}" stroke-width="1.5" stroke-dasharray="2 2"/>`).join("");
    return `<svg viewBox="0 0 96 56" width="96" height="56" role="img" aria-hidden="true"><rect width="96" height="56" rx="8" fill="${bg}"/>${under}${body}${lines}</svg>`;
  }
  const phoneSvg = y => `<svg viewBox="0 0 18 30" width="18" height="30" aria-hidden="true"><rect x="1" y="1" width="16" height="28" rx="3" fill="none" stroke="currentColor" stroke-width="1.4"/><rect x="4" y="${(1 + clamp(y, 0.05, 0.95) * 28 - 1.5).toFixed(1)}" width="10" height="3" rx="1.5" fill="currentColor"/></svg>`;
  function sampleWords() {
    const w = Frames.win && Frames.win.words.length >= 2 ? Frames.win.words.slice(0, 2).map(x => String(x.text || "").replace(/[.,;:!?¿¡]+/g, "")).join(" ") : "";
    return w && w.length <= 22 ? w.toLowerCase() : T("sample.fallback").toLowerCase();
  }
  const caseOf = (s, cs) => (cs === "upper" ? s.toUpperCase() : cs === "lower" ? s.toLowerCase() : cs === "title" ? s.replace(/(^|\s)(\S)/g, (m, a, b) => a + b.toUpperCase()) : s.charAt(0).toUpperCase() + s.slice(1));

  // ------------------------------------------------------------------ ColorWhere
  const SW_TEXT = ["#FFFFFF", "#111111", "#FFD23F", "#FF5A3C", "#2EC4B6", "#7C5CFF"];
  function swatchBtn(hex, cur, disabled, label) {
    const on = C.normHex(hex) === C.normHex(cur);
    const name = label || C.colorName(hex);
    return `<button type="button" class="cw-sw${on ? " on" : ""}" data-c="${hex}" style="--c:${hex}" aria-pressed="${on}" aria-label="${esc(T("color.swatch", { nombre: name }))}" title="${esc(label || name.charAt(0).toUpperCase() + name.slice(1))}"${disabled ? " disabled" : ""}></button>`;
  }
  const ColorWhere = E.ColorWhere = {
    /** o = {id, role, k (clave en fx.captions), mode: swatches|linked|pill, preset, layout} */
    create(o) {
      const ctlEl = document.createElement("div"); ctlEl.className = "cw-ctl";
      const exEl = document.createElement("div"); exEl.className = "cw-ex";
      const key = uid("cw");
      let row = null;
      function value() { return o.k === "accent" ? brand() : o.k === "highlight" ? (cap().highlight || brand()) : (cap().color || "#FFFFFF"); }
      function drawEx() { exEl.innerHTML = whereSvg(o.role, value(), key, o.preset); }
      function drawCtl() {
        const cur = value(), adv = E.level() === "advanced";
        if (o.mode === "linked") {
          ctlEl.innerHTML = `<div class="cw-linked"><span class="cw-dot" style="--c:${brand()}" aria-hidden="true"></span><span>${esc(T("color.linked"))}</span>`
            + `<button type="button" class="linkbtn" data-a="brand" aria-label="${esc(T("color.change.aria"))}">${esc(T("color.change"))}</button></div>`;
          $('[data-a="brand"]', ctlEl).onclick = () => E.open("stBrand", { focus: "project.accent" });
          return;
        }
        const linked = o.mode === "pill" && pillLinked();
        const list = [C.projectAccent(PLAN)].concat(SW_TEXT.filter(h => h !== C.projectAccent(PLAN)));
        const iid = uid("cwc");
        ctlEl.innerHTML = (o.mode === "pill" ? `<label class="cw-same"><input type="checkbox" data-a="same"${linked ? " checked" : ""}> ${esc(T("color.same_brand"))}</label>` : "")
          + `<div class="cw-sws">${list.map((h, i) => swatchBtn(h, cur, linked, i === 0 ? T("color.brand") : null)).join("")}`
          + `<label class="cw-other" title="${esc(T("color.other"))}"><input type="color" id="${iid}" value="${cur.toLowerCase()}" aria-label="${esc(T("color.other"))}"${linked ? " disabled" : ""}><span>${esc(T("color.other"))}</span></label></div>`
          + (adv ? `<input type="text" class="cw-hex" value="${esc(cur)}" maxlength="7" spellcheck="false" aria-label="${esc(T("color.hex"))}"${linked ? " disabled" : ""}>` : "");
        // la pastilla: cualquier color elegido a mano es propio (aunque sea el de la marca de ahora)
        const put = h => (o.k === "highlight" ? setPill(h, true) : editCaps(c => { c[o.k] = h; }));
        $$(".cw-sw", ctlEl).forEach(b => { b.onclick = () => { if (!b.disabled) put(b.dataset.c); }; });
        const same = $('[data-a="same"]', ctlEl);
        if (same) same.onchange = () => { const on = same.checked; setPill(on ? null : C.projectAccent(PLAN), !on); };
        const inp = $('input[type="color"]', ctlEl);
        FX.gesture(inp, () => { cap()[o.k] = inp.value.toUpperCase(); if (o.k === "highlight") C.setPillOwn(PLAN, true); drawEx(); Ticker.kick(); },
          done => { if (done) { FX.changed({ inspector: false }); E.refreshSoon(0); } });
        const hx = $(".cw-hex", ctlEl);
        if (hx) hx.addEventListener("change", () => { const h = C.normHex(hx.value); if (!h) { hx.value = value(); hx.setAttribute("aria-invalid", "true"); return; } hx.removeAttribute("aria-invalid"); put(h); });
      }
      /** Fila de la pastilla que sigue a la marca: las muestras estan apagadas y la nota dice por que. */
      function syncNote() {
        if (o.mode !== "pill") return;
        const linked = pillLinked(), tx = $(".hrow-txt", row.el);
        let n = $(".hrow-note", tx);
        if (linked && !n) { n = document.createElement("p"); n.className = "hrow-note"; tx.appendChild(n); }
        if (n) { if (linked) n.textContent = T("color.linked.note"); else n.remove(); }
      }
      function build() {
        drawEx(); drawCtl();
        const ctx = { preset: o.preset };
        if (!row) row = HelpRow.create({ id: o.id, ctx, control: ctlEl, example: exEl, layout: o.layout, cls: "cw" });
        else row.update({ ctx, control: ctlEl, example: exEl });
        row.el.dataset.role = o.role;
        syncNote();
      }
      build();
      return {
        el: row.el,
        /** Actualiza en sitio (sin perder el foco si se esta usando). */
        update(p) {
          Object.assign(o, p || {});
          const ae = document.activeElement, inside = ae && row.el.contains(ae);
          if (inside && ae.type === "color") { drawEx(); return; }
          const sig = JSON.stringify([value(), brand(), E.level(), cap().highlight || null, o.mode === "pill" && pillLinked()]);
          if (sig === row.el.dataset.sig && !(p && p.force)) return;
          row.el.dataset.sig = sig;
          const focusSel = inside ? (ae.dataset.c ? `[data-c="${ae.dataset.c}"]` : ae.dataset.a ? `[data-a="${ae.dataset.a}"]` : null) : null;
          drawEx(); drawCtl(); syncNote();
          if (focusSel) { const f = $(focusSel, ctlEl); if (f) f.focus(); }
        },
        destroy() { row.el.remove(); },
      };
    },
  };

  // ------------------------------------------------------------------ StyleGallery
  const PRESETS = () => Object.keys((UI().options || {})["captions.preset"] || { clean_bold: 1, karaoke: 1, boxed: 1, minimal_small: 1, bold_caps: 1, none: 1 });
  const StyleGallery = E.StyleGallery = {
    /** o = {cols: 3|2, onPick(preset), onHover(preset|null)} */
    create(o) {
      const el = document.createElement("div"); el.className = "gal"; el.style.setProperty("--cols", o.cols || 3);
      el.setAttribute("role", "radiogroup"); el.setAttribute("aria-label", T("gallery.aria"));
      const opts = (UI().options || {})["captions.preset"] || {};
      el.innerHTML = PRESETS().map(k => `<button type="button" class="gcard" role="radio" aria-checked="false" data-k="${k}">`
        + `<span class="gshot"><canvas aria-hidden="true"></canvas></span>`
        + `<span class="gmeta"><span class="gname">${esc((opts[k] || {}).label || k)}</span><span class="gdesc">${esc((opts[k] || {}).help || "")}</span>`
        + `<span class="gchosen">${CHK}${esc(T("gallery.chosen"))}</span></span></button>`).join("");
      const cards = $$(".gcard", el);
      cards.forEach(b => {
        b.onclick = () => o.onPick(b.dataset.k);
        // vista previa al pasar el mouse: solo con movimiento real (al desplazarse la columna tras
        // elegir, la tarjeta que queda bajo el puntero no cambia la vista grande)
        b.addEventListener("pointermove", e => { if (o.onHover && (e.movementX || e.movementY)) o.onHover(b.dataset.k); });
        b.addEventListener("pointerleave", () => o.onHover && o.onHover(null));
        b.addEventListener("focus", () => o.onHover && o.onHover(b.dataset.k));
        b.addEventListener("blur", () => o.onHover && o.onHover(null));
        const cv = $("canvas", b);
        Ticker.add({ el: cv, name: "card", fps: 30, draw: t => drawCard(cv, b.dataset.k, t) });
      });
      radioKeys(el);
      function sync() {
        const v = cardOf();
        cards.forEach(b => { const on = b.dataset.k === v; b.setAttribute("aria-checked", String(on)); b.classList.toggle("on", on); });
        roving(el);
      }
      sync();
      return { el, update: sync, destroy() { el.remove(); } };
    },
  };
  /** Una tarjeta: recorte horizontal (todo el ancho) del cuadro, centrado en la altura de los subtitulos. */
  function drawCard(cv, preset, t) {
    fitCanvas(cv);
    const cw = cv.width, ch = cv.height, W = cw, H = Math.round(cw / fmtAspect());
    const B = FX.layoutBox(W, H), y = clamp(cap().y == null ? 0.72 : +cap().y, 0.2, 0.8);
    const yc = B.y + y * B.h;
    const dy = H <= ch ? (H - ch) / 2 : clamp(yc - ch / 2, 0, H - ch);
    drawScene(cv.getContext("2d"), W, H, t, preset, dy);
  }

  // ------------------------------------------------------------------ vista previa grande
  /* hover: estilo de subtitulos bajo el mouse; hoverLook: estilo de color bajo el mouse; ab: la linea
     de «Comparar con el original» (0..1; a la izquierda, como salio de la camara). */
  const PV = { hover: null, hoverLook: null, holdOrig: false, cv: null, t: 0, ab: 0.5, abOn: false, abAuto: false };
  function drawPreview(t) {
    const cv = PV.cv; if (!cv || !hasPlan()) return;
    fitCanvas(cv);
    const W = cv.width, H = cv.height, c = cv.getContext("2d"), pr = PV.hover || cardOf();
    PV.t = t;
    PV.bb = drawScene(c, W, H, t, pr, 0, PV.holdOrig ? null : PV.hoverLook || undefined);
    if (PV.abOn) {
      // a la izquierda de la linea, el mismo instante como salio de la camara (mismas letras encima)
      c.save(); c.beginPath(); c.rect(0, 0, Math.round(PV.ab * W), H); c.clip();
      drawScene(c, W, H, t, pr, 0, null);
      c.restore();
    }
  }
  /** Gancho de pruebas: la vista grande de ahora sin el color (para medir la linea de comparar). */
  E.previewRef = function () {
    const cv = document.createElement("canvas"); cv.width = PV.cv.width; cv.height = PV.cv.height;
    drawScene(cv.getContext("2d"), cv.width, cv.height, PV.t, PV.hover || cardOf(), 0, null);
    return cv;
  };
  /** «Comparar con el original»: prende o apaga la linea de antes y despues. */
  function setAb(on) {
    PV.abOn = !!on;
    syncAb(); syncPreviewNote(); Ticker.kick();
  }
  E.setCompare = setAb;
  function syncAb() {
    const b = $("#stCompare"), ab = $("#stAb"), box = $("#stPvBox"), h = $("#stAbH");
    if (b) { b.setAttribute("aria-pressed", String(PV.abOn)); b.classList.toggle("on", PV.abOn); }
    if (ab) ab.hidden = !PV.abOn;
    if (box) { box.classList.toggle("abon", PV.abOn); box.style.setProperty("--ab", (PV.ab * 100).toFixed(2) + "%"); }
    if (h) { const v = Math.round(PV.ab * 100); h.setAttribute("aria-valuenow", String(v)); h.setAttribute("aria-valuetext", `${v} %`); }
  }
  function wireAb() {
    const box = $("#stPvBox"), h = $("#stAbH"), b = $("#stCompare"); if (!box || box.dataset.wired) return;
    box.dataset.wired = "1";
    if (b) { b.textContent = T("compare.on"); b.onclick = () => setAb(!PV.abOn); }
    const a = $("#stAbA"), z = $("#stAbB");
    if (a) a.textContent = T("compare.before");
    if (z) z.textContent = T("compare.after");
    if (h) h.setAttribute("aria-label", T("compare.aria"));
    let drag = false;
    const move = e => { const r = box.getBoundingClientRect(); PV.ab = clamp((e.clientX - r.left) / r.width, 0.02, 0.98); syncAb(); Ticker.kick(); };
    box.addEventListener("pointerdown", e => { if (!PV.abOn) return; drag = true; try { box.setPointerCapture(e.pointerId); } catch (x) { /* sin captura */ } move(e); e.preventDefault(); });
    box.addEventListener("pointermove", e => { if (drag) move(e); });
    const end = () => { drag = false; };
    box.addEventListener("pointerup", end); box.addEventListener("pointercancel", end);
    if (h) h.addEventListener("keydown", e => {
      const d = { ArrowLeft: -0.05, ArrowDown: -0.05, ArrowRight: 0.05, ArrowUp: 0.05 }[e.key];
      if (d == null && e.key !== "Home" && e.key !== "End") return;
      e.preventDefault(); e.stopPropagation();
      PV.ab = e.key === "Home" ? 0.02 : e.key === "End" ? 0.98 : clamp(PV.ab + d, 0.02, 0.98);
      syncAb(); Ticker.kick();
    });
    syncAb();
  }
  /** Gancho de pruebas: lo dibujado en la vista grande (caja, pastilla y centro de cada palabra). */
  E.previewInfo = function () {
    const out = { bb: PV.bb || null, pill: PV.bb ? PV.bb.pill : null, w: PV.cv ? PV.cv.width : 0, h: PV.cv ? PV.cv.height : 0, hover: PV.hover, preset: cardOf(), t: PV.t, words: [] };
    const preset = PV.hover || cardOf();
    if (!PV.cv || preset === "none") return out;
    const g = Groups.get(preset), gr = g.groups && C.capGroupAt(g.groups, PV.t);
    if (!gr) return out;
    const B = FX.layoutBox(out.w, out.h), cfg = effCfg(preset);
    const L = CapDraw.layout(gr, cfg, B, { cfg, srv: g.srv, titleFont: FX.capTitleFont(PLAN), fontCss: FX.capFontCss, ensureFont: FX.ensureFont });
    if (!L) return out;
    const top = B.y + L.top + ((cfg.y == null ? 0.72 : +cfg.y) - L.D.y) * B.h;
    out.words = CapDraw.pillRects(L, B, top).map((r, i) => ({ text: L.words[i].txt, start: +L.words[i].w.start, cx: r ? (r[0] + r[2]) / 2 : null }));
    out.group = { start: +gr.start, end: +gr.end, srv: g.srv };
    return out;
  };
  function syncPreviewState() {
    const s = $("#stPvState"); if (!s || !hasPlan()) return;
    const look = gcfg().enabled === false ? "" : lookName(curLook());
    s.textContent = PV.holdOrig ? T("look.orig") : PV.hover && PV.hover !== cardOf() ? T("preview.hover", { estilo: presetName(PV.hover) })
      : PV.hoverLook && (PV.hoverLook !== curLook() || gcfg().enabled === false) ? T("preview.hover", { estilo: lookName(PV.hoverLook) })
        : [presetName(cardOf()), look].filter(Boolean).join(" · ");
  }
  function syncPreviewNote() {
    const n = $("#stPvNote"); if (n) n.textContent = Frames.neutral && !Frames.pending ? T("preview.neutral") : PV.abOn ? T("compare.note") : T("preview.note");
  }
  /** Ancho de la vista previa: medido con el alto real del panel (nunca un numero fijo). */
  function sizePreview() {
    const pane = $("#stylePane"), pv = $("#stPreview"); if (!pane || !pv || pane.offsetParent === null) return;
    // las filas y secciones que se enfocan quedan bajo el encabezado fijo de la columna (medido)
    const hd = $("#stCol .st-head"); if (hd) pane.style.setProperty("--st-head", Math.ceil(hd.getBoundingClientRect().height + 8) + "px");
    const box = $("#stPvBox"), chrome = pv.getBoundingClientRect().height - (box ? box.getBoundingClientRect().height : 0);
    const ps = getComputedStyle(pane), padV = parseFloat(ps.paddingTop) + parseFloat(ps.paddingBottom);
    const hAvail = pane.clientHeight - padV - chrome - 8, a = fmtAspect();
    const w = clamp(Math.floor(hAvail * Math.min(a, 9 / 16)), 260, 380);
    pane.style.setProperty("--pvw", w + "px");
    pv.classList.toggle("narrowh", w < 330);
    if (box) box.style.aspectRatio = `${a}`;
  }

  // ------------------------------------------------------------------ sección Subtítulos (Estilo e inspector)
  const CAPS_IDS = ["captions.preset", "captions.color", "captions.accent", "captions.highlight", "captions.y", "captions.size",
    "captions.case", "captions.font", "captions.max_words", "captions.max_lines", "captions.kw_accent", "captions.recenter",
    "captions.stroke", "captions.shadow", "captions.hide_during_titles", "captions.x", "captions.box", "captions.enabled", "transcript.edit"];
  E.CAPS_IDS = CAPS_IDS;
  const INSP_IDS = ["captions.preset", "captions.color", "captions.accent", "captions.highlight", "captions.y", "captions.size", "captions.case", "captions.y_ranges"];

  /* Ejemplo de un ajuste de Avanzado: TU frase con cada opcion lado a lado (Sin · Grueso, No · Sí,
     1 · 2 renglones), con el estilo y los colores de ahora. El borde y la sombra van sobre un fondo
     claro (ahi es donde ayudan); «Centrar la frase» anima la frase apareciendo con y sin centrar. Si
     el ajuste no hace nada con el estilo elegido, una sola foto: asi se ve (la nota dice por que). */
  const CMP = {
    max_lines: { n: 6, vals: [[1, "cmp.lines1"], [2, "cmp.lines2"]] },
    kw_accent: { n: 3, vals: [[false, "tri.no"], [true, "tri.yes"]], kw: true },
    recenter: { n: 3, vals: [[false, "tri.no"], [true, "tri.yes"]], loop: true },
    stroke: { n: 2, vals: [[0, null], [6, null]], light: true },
    shadow: { n: 2, vals: [[0, null], [1, null]], light: true },
  };
  /** Las primeras `n` palabras dichas de tu video (desde la ventana de muestra, siguiendo con las que
      vienen si hacen falta) como un grupo de subtitulos (ejemplos). */
  function sampleGroup(n) {
    const w = Frames.win; if (!w || !w.words || w.words.length < 2) return null;
    let src = w.words;
    if (src.length < n) {
      const all = C.spokenWords(phrases(), PLAN.cuts), i0 = all.findIndex(x => +x.start >= +w.words[0].start - 1e-6);
      if (i0 >= 0) src = all.slice(i0, i0 + n);
    }
    const ws = src.slice(0, n).map(x => ({ text: String(x.text || "").trim(), start: +x.start, end: +x.end })).filter(x => x.text);
    if (ws.length < 2) return null;
    return { words: ws, start: ws[0].start - 0.06, end: ws[ws.length - 1].end + 0.6 };
  }
  /** La palabra clave del ejemplo: la mas larga (sin la ultima: ahi va la pastilla, que la taparia). */
  const sampleKw = gr => gr.words.slice(0, Math.max(1, gr.words.length - 1)).map(x => x.text).reduce((a, b) => (C.normWord(b).length > C.normWord(a).length ? b : a), "");
  /** «Renglones»: una frase tuya que NO cabe en un renglon con tu estilo (si no, 1 y 2 se verian iguales). */
  const LINES_N = { key: "", n: 0 };
  function linesGroup(W, H, B, pr) {
    const key = JSON.stringify([Frames.key, W, effCfg(pr), FX.capTitleFont(PLAN)]);
    if (LINES_N.key !== key) {
      LINES_N.key = key; LINES_N.n = 16;
      for (let n = 6; n <= 16; n++) {
        const g = sampleGroup(n); if (!g) break;
        const L = CapDraw.layout(g, Object.assign(effCfg(pr), { max_lines: 1 }), B, { cfg: effCfg(pr), srv: false, titleFont: FX.capTitleFont(PLAN), fontCss: FX.capFontCss, ensureFont: FX.ensureFont });
        if (L && L.rows[0] && L.rows[0].sc < 0.97) { LINES_N.n = n; break; }
        if (g.words.length < n) { LINES_N.n = g.words.length; break; }
      }
    }
    return sampleGroup(LINES_N.n);
  }
  const CMP_OFF = [];
  function cmpOff(i, W, H) { let o = CMP_OFF[i]; if (!o) o = CMP_OFF[i] = document.createElement("canvas"); if (o.width !== W || o.height !== H) { o.width = W; o.height = H; } return o; }
  /** Dibuja cada opcion (lado a lado) en su lienzo, con el mismo recorte para todas (comparables). */
  function drawCompare(cvs, k, vals, ts) {
    const sp = CMP[k], pr = cardOf();
    cvs.forEach(cv => { fitCanvas(cv); const c = cv.getContext("2d"); c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, cv.width, cv.height); });
    if (pr === "none") return;
    const W = 540, H = Math.round(W / fmtAspect()), B = FX.layoutBox(W, H);
    const gr = k === "max_lines" ? linesGroup(W, H, B, pr) : sampleGroup(sp.n);
    if (!gr) return;
    const last = gr.words[gr.words.length - 1];
    const full = last.end + 0.2;
    // el bucle de «Centrar la frase»: las palabras apareciendo, a no menos de 1.6 s (si hablas rapido,
    // en camara lenta: se alcanza a ver como se mueve la frase) y un momento quieta al final; quieto:
    // a media frase
    const rev = Math.max(0.05, last.start - gr.start), slow = clamp(rev / 1.6, 0.25, 1), per = rev / slow + 1.0;
    const t = !sp.loop || vals.length < 2 ? full : Ticker.frozen() != null || REDUCED() ? +gr.words[Math.min(1, gr.words.length - 1)].start + 0.12
      : gr.start + Math.min(((ts / 1000) % per) * slow, rev + 0.5);
    const opt = cfg => ({ cfg, accent: brand(), srv: false, keywords: sp.kw ? [sampleKw(gr)] : null, yFrac: cfg.y == null ? 0.72 : +cfg.y, rangeAlpha: 1,
      titleFont: FX.capTitleFont(PLAN), fontCss: FX.capFontCss, ensureFont: FX.ensureFont });
    let u = null;
    const outs = vals.map((v, i) => {
      const cfg = Object.assign(effCfg(pr), { [k]: v }), o = cmpOff(i, W, H), oc = o.getContext("2d");
      oc.setTransform(1, 0, 0, 1, 0, 0);
      // el recorte sale de la frase completa (igual en todas las opciones)
      oc.clearRect(0, 0, W, H);
      const bf = CapDraw.drawGroup(oc, B, gr, full, opt(cfg));
      if (bf) u = u ? { x0: Math.min(u.x0, bf.x), y0: Math.min(u.y0, bf.y), x1: Math.max(u.x1, bf.x + bf.w), y1: Math.max(u.y1, bf.y + bf.h) } : { x0: bf.x, y0: bf.y, x1: bf.x + bf.w, y1: bf.y + bf.h };
      if (sp.light) { oc.fillStyle = "#cfd5df"; oc.fillRect(0, 0, W, H); } else oc.drawImage(frameLayer(W, H), 0, 0);
      CapDraw.drawGroup(oc, B, gr, t, opt(cfg));
      return o;
    });
    if (!u) return;
    const fs = C.captionCfg(effCfg(pr), FX.capTitleFont(PLAN)).size * B.w, pad = 0.3 * fs;
    cvs.forEach((cv, i) => {
      const pw = cv.width, ph = cv.height, a = pw / ph;
      let cw = Math.max(u.x1 - u.x0 + 2 * pad, (u.y1 - u.y0 + 2 * pad) * a), ch = cw / a;
      const sx = clamp((u.x0 + u.x1) / 2 - cw / 2, 0, Math.max(0, W - cw)), sy = clamp((u.y0 + u.y1) / 2 - ch / 2, 0, Math.max(0, H - ch));
      cv.getContext("2d").drawImage(outs[i], sx, sy, Math.min(cw, W), Math.min(ch, H), 0, 0, pw, ph);
    });
  }
  /** El ejemplo de un ajuste `k` de los subtitulos (opciones lado a lado), o la foto sola si no aplica. */
  function capCompare(id, k) {
    const box = document.createElement("div");
    const sp = CMP[k], applies = C.captionOptionApplies(cardOf(), k) && cardOf() !== "none";
    const ch = copy(id).basic_choices || [];
    const label = ([v, key]) => (key ? T(key) : ((ch.find(c => sameV(c[0], v)) || [])[1] || String(v)));
    const vals = applies ? sp.vals.map(x => x[0]) : [cap()[k] == null ? null : cap()[k]];
    box.className = "tri cmp " + (vals.length === 2 ? "two" : "one");
    box.dataset.cmp = k;
    box.innerHTML = (applies ? sp.vals.map(label) : [T("ex.same")]).map(l => `<figure><canvas aria-hidden="true"></canvas><figcaption>${esc(l)}</figcaption></figure>`).join("");
    const cvs = $$("canvas", box);
    Ticker.add({ el: box, name: "cmp", fps: sp.loop && applies ? 15 : 8, static: !(sp.loop && applies), still: stillTime,
      draw: (t, ts) => drawCompare(cvs, k, vals, ts) });
    return box;
  }
  E.CMP = CMP;

  /** El ancestro que se desplaza (la columna de Estilo, el panel del inspector) o null. */
  function scrollBox(el) {
    for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const st = getComputedStyle(e);
      if (/(auto|scroll)/.test(st.overflowY) && e.scrollHeight > e.clientHeight + 1) return e;
    }
    return null;
  }
  /** Lo visible de `el`: la caja que se desplaza (menos su encabezado fijo) dentro de la ventana. */
  function viewOf(el) {
    const sc = scrollBox(el);
    let top = 0, bot = window.innerHeight, head = 0;
    if (sc) {
      const b = sc.getBoundingClientRect(); top = Math.max(top, b.top); bot = Math.min(bot, b.bottom);
      const hd = [...sc.children].find(c => getComputedStyle(c).position === "sticky");
      if (hd) { head = hd.getBoundingClientRect().height; top += head; }
    }
    return { sc, top, bot, head };
  }
  /** Lleva `el` a la vista si no se ve completo (lo justo, sin saltos; suave salvo con «reducir movimiento»). */
  function revealRow(el) {
    const v = viewOf(el), r = el.getBoundingClientRect();
    if (r.top >= v.top - 0.5 && r.bottom <= v.bot + 0.5) return false;
    // (que el encabezado fijo del panel no tape la fila)
    if (v.head && !el.closest("#stCol")) el.style.scrollMarginTop = Math.ceil(v.head + 8) + "px";
    el.scrollIntoView({ block: "nearest", behavior: REDUCED() ? "auto" : "smooth" });
    return true;
  }
  /** Gancho de pruebas: ¿se ve completo `el` dentro de su caja que se desplaza? */
  E.inView = function (el) { const v = viewOf(el), r = el.getBoundingClientRect(); return { ok: r.top >= v.top - 0.5 && r.bottom <= v.bot + 0.5, top: r.top, bottom: r.bottom, vTop: v.top, vBot: v.bot }; };
  /** Construye la sección Subtítulos en `box` (layout wide = Estilo, narrow = inspector). */
  function CapsSection(box, layout) {
    const narrow = layout === "narrow";
    const S = { box, layout, rows: {}, sig: "" };
    const ctxP = () => ({ preset: cardOf() });
    function pick(k) {
      const before = cardOf();
      FX.edit(() => { const c = cap(); if (k === "none") c.enabled = false; else { c.enabled = true; c.preset = k; } }, { inspector: false });
      PV.hover = null;
      E.refresh();                                   // las filas de color de este estilo, ya
      if (k === before) return;
      // la primera fila de color (p. ej. «Color de la pastilla») queda a la vista DENTRO de la columna
      // que se desplaza (Estilo) o del panel (inspector), no solo de la ventana; el foco sigue en la tarjeta
      const first = $(".hrow.cw", S.box);
      if (first) revealRow(first);
    }
    function build() {
      const lv = E.level(), pr = cardOf();
      const ids = C.visibleRows(UI(), narrow ? INSP_IDS : CAPS_IDS, lv, ctxP());
      S.sig = JSON.stringify([ids, lv, pr]);
      box.innerHTML = ""; S.rows = {};
      const off = pr === "none";
      ids.forEach(id => {
        const k = id.split(".").pop();
        let r = null;
        if (id === "captions.preset") {
          const g = StyleGallery.create({ cols: narrow ? 2 : 3, onPick: pick, onHover: h => { PV.hover = h; syncPreviewState(); Ticker.kick(); } });
          r = HelpRow.create({ id, ctx: ctxP(), control: g.el, layout, full: true, exKind: "ninguno" });
          r.gallery = g;
        } else if (["captions.color", "captions.accent", "captions.highlight"].includes(id)) {
          const role = C.captionColorRoles(pr).find(x => "captions." + x.k === id);
          if (!role) return;
          r = ColorWhere.create({ id, role: role.role, k: role.k, mode: role.mode, preset: pr, layout });
          S.rows[id] = r; box.appendChild(r.el);
          // la nota del estilo va despues de la ultima fila de color
          const roles = C.captionColorRoles(pr);
          if ("captions." + roles[roles.length - 1].k === id) { const n = C.captionStyleNote(UI(), pr); if (n) box.insertAdjacentHTML("beforeend", `<p class="hrow-rule">${esc(n)}</p>`); }
          return;
        } else if (id === "captions.y") r = numRow(id, "y", 0.72, layout, v => phoneSvg(v), off);
        else if (id === "captions.size") r = numRow(id, "size", 0.052, layout, null, off);
        else if (id === "captions.case") r = caseRow(layout, off);
        else if (id === "captions.font") r = fontRow(layout, off);
        else if (id === "captions.max_words") r = maxWordsRow(layout, off);
        else if (id === "captions.max_lines") r = segRow(id, "max_lines", [[null, T("tri.auto")], [1, "1"], [2, "2"]], layout, off, true);
        else if (id === "captions.kw_accent" || id === "captions.recenter") r = triRow(id, k, layout, off);
        else if (id === "captions.stroke" || id === "captions.shadow") r = segRow(id, k, copy(id).basic_choices, layout, off, true);
        else if (id === "captions.hide_during_titles") r = boolRow(id, k, layout, off);
        else if (id === "captions.y_ranges") r = rangesRow(layout);
        else if (id === "transcript.edit") {
          const b = document.createElement("button"); b.type = "button"; b.className = "btn ghost mini"; b.innerHTML = `${ICO("type")}${esc(T("transcript.open"))}`;
          b.onclick = () => E.openTranscript();
          r = HelpRow.create({ id, ctx: {}, control: b, layout });
        }
        if (r) { S.rows[id] = r; box.appendChild(r.el); }
      });
      if (pr === "none" && !narrow) box.insertAdjacentHTML("beforeend", `<p class="hrow-rule">${esc(C.captionStyleNote(UI(), "none") || "")}</p>`);
      if (narrow) {
        const more = document.createElement("button"); more.type = "button"; more.className = "linkbtn caps-more";
        more.textContent = T("caps.more"); more.onclick = () => E.open("stCaps");
        box.appendChild(more);
      }
    }
    /** Actualiza en sitio; reconstruye solo si cambian las filas (estilo o nivel). */
    function update() {
      const lv = E.level(), pr = cardOf();
      const ids = C.visibleRows(UI(), narrow ? INSP_IDS : CAPS_IDS, lv, ctxP());
      if (JSON.stringify([ids, lv, pr]) !== S.sig) { const ae = document.activeElement, k = ae && ae.closest && ae.closest(".gcard") ? ae.dataset.k : null; build(); if (k) { const c = $(`.gcard[data-k="${k}"]`, box); if (c) c.focus(); } return; }
      Object.values(S.rows).forEach(r => {
        if (r.gallery) r.gallery.update();
        else if (r.sync) r.sync();
        else if (r.el.classList.contains("cw")) r.update();
      });
    }
    build();
    return { update, rebuild: build, S };
  }

  /** Fila numerica con opciones humanas (Basico) y, en Avanzado, una barra con el valor en palabras. */
  function numRow(id, k, dflt, layout, iconOf, off) {
    const cp = copy(id, { preset: cardOf() });
    const choices = (cp.basic_choices || []).map(([v, l], i) => ({ v, label: l, icon: iconOf ? iconOf(v) : null,
      sample: !iconOf && k === "size" ? `<span style="font-size:${12 + i * 4}px">Aa</span>` : null }));
    const val = () => (cap()[k] == null ? dflt : +cap()[k]);
    const wrap = document.createElement("div"); wrap.className = "num-ctl";
    const seg = Segmented.create({ choices, value: val(), nearest: true, disabled: off, onPick: v => editCaps(c => { c[k] = v; }) });
    wrap.appendChild(seg.el);
    let rg = null, hv = null;
    if (E.level() === "advanced" && cp.adv) {
      const a = cp.adv, line = document.createElement("div"); line.className = "adv-line";
      rg = document.createElement("input"); rg.type = "range"; rg.min = a.min; rg.max = a.max; rg.step = a.step || 1; rg.disabled = !!off;
      rg.setAttribute("aria-label", cp.adv_label || cp.label);
      hv = document.createElement("span"); hv.className = "hv";
      line.appendChild(rg); line.appendChild(hv); wrap.appendChild(line);
      FX.gesture(rg, () => { cap()[k] = +(+rg.value / (a.scale || 1)).toFixed(4); setHv(); seg.update({ value: val() }); Ticker.kick(); }, d => { if (d) { FX.changed({ inspector: false }); E.refreshSoon(0); } });
    }
    const ex = document.createElement("div"); ex.className = "ex-icon";
    function setHv() {
      const ctx = { preset: cardOf() };
      if (rg) { rg.value = String(+(val() * (cp.adv.scale || 1)).toFixed(3)); const s = C.humanValue(cp.adv, val(), ctx); hv.textContent = s; rg.setAttribute("aria-valuetext", s); }
      if (iconOf) ex.innerHTML = iconOf(val());
      else ex.innerHTML = `<span class="aa" style="font-size:${Math.round(clamp(val() * (C.CAPTION_PRESETS[cardOf()] || { scale: 1 }).scale * 380, 10, 34))}px">Aa</span>`;
    }
    setHv();
    const note = off ? T("caps.off") : undefined;
    const r = HelpRow.create({ id, ctx: { preset: cardOf() }, control: wrap, example: ex, exKind: "vivo", layout, note });
    r.sync = () => { if (document.activeElement !== rg) { seg.update({ value: val() }); setHv(); } };
    return r;
  }
  function caseRow(layout, off) {
    const forced = cardOf() === "bold_caps", sw = sampleWords();
    const list = [["none", optLabel("captions.case", "none")], ["upper", optLabel("captions.case", "upper")], ["lower", optLabel("captions.case", "lower")]];
    if (E.level() === "advanced") list.push(["title", optLabel("captions.case", "title")]);
    const val = () => (forced ? "upper" : cap().case || "none");
    const seg = Segmented.create({ choices: list.map(([v, l]) => ({ v, label: l, sample: `<span class="smp-case">${esc(caseOf(sw, v))}</span>` })),
      value: val(), disabled: forced || off, onPick: v => editCaps(c => { c.case = v; }), cls: list.length > 3 ? "grid2" : "" });
    const ex = document.createElement("div"); ex.className = "ex-text";
    const setEx = () => { ex.textContent = caseOf(sw, val()); };
    setEx();
    const r = HelpRow.create({ id: "captions.case", ctx: { preset: cardOf() }, control: seg.el, example: ex, exKind: "vivo", layout, note: off ? T("caps.off") : undefined });
    r.sync = () => { seg.update({ value: val() }); setEx(); };
    return r;
  }
  function fontRow(layout, off) {
    const sel = document.createElement("select"); sel.className = "fsel"; sel.disabled = !!off;
    const fonts = (FX.fontList && FX.fontList()) || [];
    const cur = cap().font || "";
    sel.innerHTML = `<option value="">${esc(T("font.default"))}</option>` + fonts.map(f => `<option value="${esc(f.name)}"${f.name === cur ? " selected" : ""}>${esc(String(f.name).replace(/-/g, " "))}</option>`).join("");
    sel.onchange = () => editCaps(c => { c.font = sel.value || null; });
    const ex = document.createElement("div"); ex.className = "ex-text";
    const setEx = () => { const D = C.captionCfg(cap(), FX.capTitleFont(PLAN)); ex.innerHTML = `<span style="font:${esc(FX.capFontCss(D.font, 15))}">${esc(caseOf(sampleWords(), "none"))}</span>`; };
    setEx();
    const r = HelpRow.create({ id: "captions.font", ctx: { preset: cardOf() }, control: sel, example: ex, exKind: "vivo", layout, note: off ? T("caps.off") : undefined });
    r.sync = () => { if (document.activeElement !== sel) { sel.value = cap().font || ""; } setEx(); };
    return r;
  }
  function maxWordsRow(layout, off) {
    const D = () => C.captionCfg(cap(), FX.capTitleFont(PLAN));
    const choices = [2, 3, 4, 5, 6].map(n => ({ v: n, label: String(n), disabled: cardOf() === "bold_caps" && n > 3 }));
    const seg = Segmented.create({ choices, value: D().max_words, disabled: off, onPick: v => editCaps(c => { c.max_words = v; }) });
    const ex = document.createElement("div"); ex.className = "ex-text ex-texto";
    const setEx = () => {
      const cp = copy("captions.max_words", { preset: cardOf() });
      const gs = C.captionGroupsFor([phrases().flat().slice(0, 40)], cap(), FX.capTitleFont(PLAN)).slice(0, 3).map(g => g.words.map(w => w.text).join(" "));
      ex.textContent = C.fillExample(cp.ex_tpl, { grupos: gs.length ? gs.join(" · ") : null }, cp.ex_ui);
    };
    setEx();
    const r = HelpRow.create({ id: "captions.max_words", ctx: { preset: cardOf() }, control: seg.el, example: ex, layout, note: off ? T("caps.off") : undefined });
    r.sync = () => { seg.update({ value: D().max_words }); setEx(); };
    return r;
  }
  function segRow(id, k, choices, layout, off, tile) {
    const seg = Segmented.create({ choices: (choices || []).map(([v, l]) => ({ v, label: l })), value: cap()[k] == null ? null : +cap()[k], nearest: true, disabled: off,
      onPick: v => editCaps(c => { c[k] = v; }) });
    const r = HelpRow.create({ id, ctx: { preset: cardOf() }, control: seg.el, example: tile ? capCompare(id, k) : null, layout, note: off ? T("caps.off") : undefined });
    r.sync = () => seg.update({ value: cap()[k] == null ? null : +cap()[k] });
    return r;
  }
  /** «Según el estilo · Sí · No». Si con el estilo elegido el motor no usa el ajuste (centrar la frase
      en Karaoke o Pastilla de color; la palabra clave en Karaoke), queda apagado y su nota dice por que. */
  function triRow(id, k, layout, off) {
    const dead = !off && !C.captionOptionApplies(cardOf(), k);
    const tri = E.TriState.create({ value: cap()[k], disabled: off || dead, onPick: v => editCaps(c => { c[k] = v; }) });
    const r = HelpRow.create({ id, ctx: { preset: cardOf() }, control: tri.el, example: capCompare(id, k), layout, note: off ? T("caps.off") : undefined });
    r.sync = () => tri.update({ value: cap()[k] == null ? null : !!cap()[k] });
    return r;
  }
  function boolRow(id, k, layout, off) {
    const i = document.createElement("input"); i.type = "checkbox"; i.className = "toggle"; i.checked = cap()[k] !== false; i.disabled = !!off;
    i.onchange = () => editCaps(c => { c[k] = i.checked; });
    const r = HelpRow.create({ id, ctx: { preset: cardOf() }, control: i, layout, note: off ? T("caps.off") : undefined });
    r.sync = () => { i.checked = cap()[k] !== false; };
    return r;
  }
  /** "Mover los subtítulos en una parte del video" (inspector, Avanzado): el editor de tramos del Studio. */
  function rangesRow(layout) {
    const p = ((window.FX && FX.schema && FX.schema()) || {}).captions;
    const prm = p && (p.params || []).find(x => x.k === "y_ranges");
    const holder = document.createElement("div"); holder.className = "ranges-ctl";
    if (prm && FX.paramEditor) holder.appendChild(FX.paramEditor(prm, cap(), { noLock: true, ctx: "captions" }));
    return HelpRow.create({ id: "captions.y_ranges", ctx: { preset: cardOf() }, control: holder, layout });
  }

  // ------------------------------------------------------------------ AccentPreview: Color de tu marca
  const SW_BRAND = ["#FFD23F", "#FF5A3C", "#2EC4B6", "#7C5CFF", "#1F9D55", "#84A2F8", "#FFFFFF"];
  const AP = { plans: {}, zoom: {}, sig: "", ver: 0 };
  /** Planes chicos (un solo elemento) para Titulo, Tarjeta y Boton, con los elementos del usuario. */
  function miniPlans() {
    const sig = `${AP.ver}|${E.liveAccent || ""}`;
    if (sig === AP.sig) return AP.plans;
    AP.sig = sig;
    const S = (window.FX && FX.schema && FX.schema()) || null;
    const base = fxs => ({ version: 2, duration: 3600, phrases: [], cuts: [], shots: [], director: PLAN.director || {}, entities: PLAN.entities || {},
      fx: Object.assign({ text: [], graphics: [], cutaways: [], effects: [], transitions: [], sfx: [], media: [], captions: { enabled: false }, grade: { enabled: false }, music: { enabled: false }, accent: C.projectAccent(PLAN) }, fxs) });
    const mk = (track, kind, extra) => { const it = S ? C.newItem(S, track, kind, 0, 3600, PLAN.fx) : { kind, start: 0, end: 4 }; it.start = 0; it.end = 4; return Object.assign(it, extra); };
    // de tu video, el primero de cada tipo que SÍ se pinta con tu color (uno con su propio color no
    // cambiaria al elegir otro); si no hay ninguno, uno de ejemplo (los nuevos llevan tu color)
    const acc = C.projectAccent(PLAN);
    const tx = (PLAN.fx.text || []).find(it => it.enabled !== false && C.paintsAccent("text", it, acc));
    const title = tx ? Object.assign(C.clone(tx), { start: 0, end: 4 }) : mk("text", "title_pop", { text: `${T("brand.demo.title1")}\n${T("brand.demo.title2")}`, accent_words: T("brand.demo.title2").split(" "), y: 0.45 });
    const lt = (PLAN.fx.graphics || []).find(it => it.enabled !== false && it.kind === "lower_third" && C.paintsAccent("graphics", it, acc));
    const ent = PLAN.entities || {};
    const card = lt ? Object.assign(C.clone(lt), { start: 0, end: 4 }) : mk("graphics", "lower_third", { title: ent.name || T("brand.demo.name"), subtitle: ent.role || T("brand.demo.role") });
    const ct = (PLAN.fx.graphics || []).find(it => it.enabled !== false && it.kind === "cta" && C.paintsAccent("graphics", it, acc));
    const btn = ct ? Object.assign(C.clone(ct), { start: 0, end: 4 }) : mk("graphics", "cta", { variant: "follow", label: T("brand.demo.follow") });
    AP.plans = { title: base({ text: [title] }), card: base({ graphics: [card] }), btn: base({ graphics: [btn] }) };
    // el acercamiento se recalcula solo si cambia el elemento que se muestra (no en cada cambio del plan)
    const ids = JSON.stringify([title.id || title.text, card.id || card.title, btn.id || btn.label]);
    if (ids !== AP.ids) { AP.ids = ids; AP.zoom = {}; }
    if (E.liveAccent) Object.values(AP.plans).forEach(p => C.applyAccent(p, E.liveAccent));
    return AP.plans;
  }
  let MINI_OFF = null;
  function miniOff(W, H) { if (!MINI_OFF) MINI_OFF = document.createElement("canvas"); if (MINI_OFF.width !== W || MINI_OFF.height !== H) { MINI_OFF.width = W; MINI_OFF.height = H; } return MINI_OFF; }
  /** Una vista de la marca: el elemento sobre tu cuadro, recortado alrededor de lo dibujado. */
  function drawBrandMini(cv, k, ts, zk) {
    zk = zk || k;
    fitCanvas(cv);
    const pw = cv.width, ph = cv.height, c = cv.getContext("2d");
    if (k === "cap") {
      const pr = cardOf(), off = pr === "none" || pr === "minimal_small";
      const W = Math.round(pw * 1.6), H = Math.round(W / fmtAspect()), B = FX.layoutBox(W, H);
      const yc = B.y + (cap().y == null ? 0.72 : +cap().y) * B.h, dy = clamp(yc - ph / 2, 0, Math.max(0, H - ph));
      const o = miniOff(W, H);
      drawScene(o.getContext("2d"), W, H, Ticker.frozen() != null ? Ticker.frozen() : REDUCED() ? stillTime() : Ticker.time(ts), pr === "none" ? "clean_bold" : pr, 0);
      c.save(); c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, pw, ph);
      c.drawImage(o, (W - pw) / 2, dy, pw, ph, 0, 0, pw, ph); c.restore();
      return;
    }
    const plan = miniPlans()[k]; if (!plan) return;
    const it = (plan.fx.text[0] || plan.fx.graphics[0]);
    const u = REDUCED() || Ticker.frozen() != null ? 1.4 : ((ts / 1000) % 3.4);
    const t = Math.min(u, 1.7);
    const z = AP.zoom[zk] || 1;
    const W = Math.round(pw * z), H = Math.round(W / fmtAspect());
    const o = miniOff(W, H), oc = o.getContext("2d");
    oc.setTransform(1, 0, 0, 1, 0, 0);
    oc.clearRect(0, 0, W, H); oc.drawImage(frameLayer(W, H), 0, 0);
    const hits = FX.drawLiveTo({ ctx: oc, W, H, plan, t: (it.start || 0) + t });
    const hb = hits.find(h => !h.scene);
    if (hb && !AP.zoom[zk] && t >= 1.2) {
      // acercamiento: lo dibujado ocupa ~82 % de la vista, a lo ancho y a lo alto (una vez por plan)
      AP.zoom[zk] = clamp(Math.min(0.82 * pw / Math.max(4, hb.bb.w / z), 0.82 * ph / Math.max(4, hb.bb.h / z)), 1, 2.4);
      if (Math.abs(AP.zoom[zk] - z) > 0.01) { drawBrandMini(cv, k, ts, zk); return; }
    }
    const cx = hb ? hb.bb.x + hb.bb.w / 2 : W / 2, cy = hb ? hb.bb.y + hb.bb.h / 2 : H / 2;
    const sx = clamp(cx - pw / 2, 0, Math.max(0, W - pw)), sy = clamp(cy - ph / 2, 0, Math.max(0, H - ph));
    c.save(); c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, pw, ph); c.drawImage(o, sx, sy, pw, ph, 0, 0, pw, ph); c.restore();
  }
  const AccentPreview = E.AccentPreview = {
    create(o) {
      const layout = (o && o.layout) || "wide";
      const ctl = document.createElement("div"); ctl.className = "ap-pick";
      const ex = document.createElement("div"); ex.className = "ap-ex";
      function drawPick() {
        const cur = brand(), list = [cur].concat(SW_BRAND.filter(h => h !== C.normHex(cur)));
        const iid = uid("apc");
        ctl.innerHTML = `<div class="cw-sws">${list.map(h => swatchBtn(h, cur, false)).join("")}`
          + `<label class="cw-other" title="${esc(T("color.other"))}"><input type="color" id="${iid}" value="${cur.toLowerCase()}" aria-label="${esc(T("color.other"))}"><span>${esc(T("color.other"))}</span></label></div>`
          + `<input type="text" class="cw-hex" value="${esc(cur)}" maxlength="7" spellcheck="false" aria-label="${esc(T("color.hex"))}">`;
        $$(".cw-sw", ctl).forEach(b => { b.onclick = () => commit(b.dataset.c); });
        const inp = $('input[type="color"]', ctl);
        inp.addEventListener("input", () => { E.liveAccent = C.normHex(inp.value); AP.sig = ""; $(".cw-hex", ctl).value = E.liveAccent; Ticker.kick(); });
        inp.addEventListener("change", () => commit(inp.value));
        const hx = $(".cw-hex", ctl);
        hx.addEventListener("change", () => { const h = C.normHex(hx.value); if (!h) { hx.value = C.projectAccent(PLAN); hx.setAttribute("aria-invalid", "true"); return; } hx.removeAttribute("aria-invalid"); commit(h); });
      }
      function commit(v) {
        const h = C.normHex(v); E.liveAccent = null; AP.sig = "";
        if (h && h !== C.projectAccent(PLAN)) FX.setAccent(h);
        E.refreshSoon(0); Ticker.kick();
      }
      const PV4 = [["cap", T("brand.pv.cap")], ["title", T("brand.pv.title")], ["card", T("brand.pv.card")], ["btn", T("brand.pv.btn")]];
      ex.innerHTML = `<div class="ap-row">${PV4.map(([k, l]) => `<figure class="ap-mini" data-k="${k}"><canvas aria-hidden="true"></canvas><figcaption><b>${esc(l)}</b><span data-sub="${k}"></span></figcaption></figure>`).join("")}</div>`
        + `<p class="ap-chips"><span class="ap-uses">${esc(T("brand.used_in"))}</span>${["brand.chip.caps", "brand.chip.titles", "brand.chip.cards", "brand.chip.bg"].map(k => `<span class="ap-chip">${esc(T(k))}</span>`).join("")}</p>`;
      $$(".ap-mini", ex).forEach(f => { const cv = $("canvas", f); Ticker.add({ el: cv, name: "brand", fps: 30, draw: (t, ts) => drawBrandMini(cv, f.dataset.k, ts) }); });
      function subs() {
        const pr = cardOf(), off = pr === "none" || pr === "minimal_small";
        const s = $('[data-sub="cap"]', ex);
        // la pastilla con su propio color no sigue a la marca: se dice (y se ve atenuada), no se finge
        const own = pr === "boxed" && !pillLinked();
        if (s) s.textContent = off ? T("brand.pv.cap.off") : own ? T("brand.pv.cap.own") : pr === "boxed" ? T("brand.pv.cap.boxed") : pr === "karaoke" ? T("brand.pv.cap.karaoke") : T("brand.pv.cap.sub");
        $('.ap-mini[data-k="cap"]', ex).classList.toggle("dim", off || own);
        [["title", "brand.pv.title.sub"], ["card", "brand.pv.card.sub"], ["btn", "brand.pv.btn.sub"]].forEach(([k, sk]) => { const e = $(`[data-sub="${k}"]`, ex); if (e) e.textContent = T(sk); });
      }
      drawPick(); subs();
      const row = HelpRow.create({ id: "project.accent", ctx: {}, control: ctl, example: ex, exKind: "vivo", layout, full: true, cls: "ap" });
      let sig = "";
      return {
        el: row.el,
        update() {
          subs();
          const ae = document.activeElement;
          if (ae && ctl.contains(ae) && (ae.type === "color" || ae.classList.contains("cw-hex"))) return;
          const s = JSON.stringify([C.projectAccent(PLAN), E.liveAccent]);
          if (s === sig) return;
          sig = s;
          const f = ae && ctl.contains(ae) && ae.dataset.c ? ae.dataset.c : null;
          drawPick();
          if (f) { const b = $(`[data-c="${f}"]`, ctl); if (b) b.focus(); }
        },
      };
    },
  };

  // ------------------------------------------------------------------ LookPicker: Color del video
  /* Seis tarjetas con TU cuadro coloreado por el motor (POST /fx/look_thumbs): la foto es lo que sale
     en el export. Mantener presionada una tarjeta (o Espacio) muestra el original. La primera vez,
     mientras llegan, el original con un brillo que pasa; si fallan, el original y «Reintentar». */
  const LP = E.LP = { st: "idle", thumbs: {}, vars: {}, det: {}, vlist: null, key: "", dkey: "", src: null, face: null, seq: 0, dseq: 0, ms: null, timer: 0, views: new Set() };
  const views = () => { LP.views.forEach(v => { if (!v.el.isConnected) LP.views.delete(v); }); return [...LP.views]; };
  function thumbDims() {
    let w = 0;
    views().forEach(v => { const g = $(".gshot", v.el); if (g) w = Math.max(w, g.getBoundingClientRect().width); });
    let tw = Math.round(Math.max(160, w || 220) * DPR() / 8) * 8, th = Math.round(tw / fmtAspect());
    if (th > 540) { tw = Math.round(tw * 540 / th); th = 540; }
    return [tw, th];
  }
  /** El cuadro original (sin color) de las miniaturas: lo que se manda al motor y lo que se ve al presionar. */
  function lookSrc(tw, th) {
    const cv = document.createElement("canvas"); cv.width = tw; cv.height = th;
    const c = cv.getContext("2d");
    if (Frames.img) FX.drawCover(c, Frames.img, 0, 0, tw, th, Frames.focusX);
    else { const g = c.createLinearGradient(0, 0, 0, th); g.addColorStop(0, "#3a404c"); g.addColorStop(1, "#14171d"); c.fillStyle = g; c.fillRect(0, 0, tw, th); }
    return cv;
  }
  const VAR_KEYS = ["exposure", "temperature", "tint", "saturation", "contrast", "strength", "clarity"];
  const choicesOf = id => (copy(id).basic_choices || []).map(c => c[0]);
  const lookBase = () => (FX.lookPayload && FX.lookPayload() || {}).base || {};
  /** La correccion de la toma de muestra (si se igualan las tomas y el analisis ya la midio). */
  function sampleCorr() { const p = FX.lookPayload && FX.lookPayload(), i = p ? C.lookShotAt(p, Frames.t) : -1; return i >= 0 ? p.per_shot[i].corr || null : null; }
  const loadImg = u => new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => rej(new Error("imagen")); im.src = u; });
  async function postThumbs(srcCv, q) {
    const blob = await new Promise(res => srcCv.toBlob(res, "image/jpeg", 0.92));
    if (!blob) throw new Error("sin JPEG");
    const r = await fetch(`/fx/look_thumbs/${JOB}?${new URLSearchParams(q)}`, { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json(), th = {}, vv = {};
    await Promise.all([...Object.entries(d.thumbs || {}).map(async ([k, u]) => { th[k] = await loadImg(u); }),
      ...Object.entries(d.variants || {}).map(async ([k, u]) => { vv[k] = await loadImg(u); })]);
    return { thumbs: th, vars: vv, ms: d.server_ms };
  }
  function thumbQuery(w, h, extra) {
    const q = Object.assign({ w, h, grade: JSON.stringify(gcfg()) }, extra), corr = sampleCorr();
    if (corr) q.corr = JSON.stringify(corr);
    return q;
  }
  /** Las miniaturas del motor: los seis estilos + los ejemplos de los ajustes finos (Menos · Así · Más). */
  const shown = () => { const p = $("#stylePane"); return !!p && p.offsetParent !== null; };
  async function requestThumbs(force) {
    if (!hasPlan() || typeof JOB === "undefined" || !JOB || JOB === "demo" || !views().length || Frames.pending || !shown()) return;
    const [tw, th] = thumbDims(), vl = C.lookVariants(gcfg(), lookBase());
    const vars = [];
    VAR_KEYS.forEach(k => vl[k].forEach(x => vars.push(`${k}:${x}`)));
    choicesOf("grade.vignette").forEach(x => vars.push(`vignette:${x}`));
    const fc = Frames.img ? faceIn(coverRect(Frames.img, tw, th, Frames.focusX), Frames.face) : null;
    const q = thumbQuery(tw, th, Object.assign({ variants: [...new Set(vars)].join(",") }, fc ? { face: fc.map(v => v.toFixed(4)).join(",") } : {}));
    const key = JSON.stringify([Frames.key, !!Frames.img, q]);
    if (!force && key === LP.key && LP.st !== "error") return;
    LP.key = key; const seq = ++LP.seq;
    LP.src = lookSrc(tw, th); LP.face = fc; LP.st = "loading"; syncLp();
    try {
      const d = await postThumbs(LP.src, q);
      if (seq !== LP.seq) return;
      Object.assign(LP, { thumbs: d.thumbs, vars: d.vars, vlist: vl, ms: d.ms, st: "ready" });
    } catch (e) {
      if (seq !== LP.seq) return;
      LP.st = "error";
    }
    syncLp();
    requestDetail();
  }
  /** Grano y nitidez: un recorte chico del cuadro a su tamano completo (en miniatura no se notan). */
  async function requestDetail() {
    if (E.level() !== "advanced" || !Frames.img || !views().length || !shown()) return;
    // a la altura de los ojos (ahi se notan la nitidez y el grano): un pixel del cuadro por cada dos de pantalla
    const img = Frames.img, W = img.width, H = img.height, f = Frames.face || [0.5, 0.42, 0, 0];
    const cw = Math.min(1, 50 / W), ch = Math.min(1, 38 / H), x0 = clamp(f[0] - cw / 2, 0, 1 - cw), y0 = clamp(f[1] - 0.12 * f[3] - ch / 2, 0, 1 - ch);
    const vars = choicesOf("grade.grain").map(x => `grain:${x}`).concat(choicesOf("grade.sharpen").map(x => `sharpen:${x}`));
    const q = thumbQuery(W, H, { variants: vars.join(","), crop: [x0, y0, x0 + cw, y0 + ch].map(v => v.toFixed(4)).join(","), looks: 0 });
    const key = JSON.stringify([Frames.key, q]);
    if (key === LP.dkey) return;
    LP.dkey = key; const seq = ++LP.dseq;
    try { const d = await postThumbs(img, q); if (seq === LP.dseq) { LP.det = d.vars; Ticker.kick(); } } catch (e) { if (seq === LP.dseq) LP.dkey = ""; }
  }
  /** Pide de nuevo las miniaturas 250 ms despues del ultimo cambio (y las tablas de los seis estilos). */
  function lookSoon(ms) {
    clearTimeout(LP.timer);
    LP.timer = setTimeout(() => { requestThumbs(); requestDetail(); ensureLookSet(); }, ms == null ? 250 : ms);
  }
  function syncLp() { views().forEach(v => v.sync()); Ticker.kick(); }
  /** Recorte 4:3 de la tarjeta (todo el ancho, centrado en la cara). */
  function cardCrop(img, w, h) {
    const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height, sh = Math.min(ih, iw * h / w);
    return { sx: 0, sy: clamp((LP.face ? LP.face[1] : 0.42) * ih - sh / 2, 0, ih - sh), sw: iw, sh };
  }
  function paintCard(c, w, h, k, orig) {
    const img = !orig && LP.st !== "error" && LP.thumbs[k] ? LP.thumbs[k] : LP.src;
    c.save(); c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, w, h);
    if (img) { const r = cardCrop(img, w, h); c.drawImage(img, r.sx, r.sy, r.sw, r.sh, 0, 0, w, h); }
    else { const g = c.createLinearGradient(0, 0, 0, h); g.addColorStop(0, "#3a404c"); g.addColorStop(1, "#14171d"); c.fillStyle = g; c.fillRect(0, 0, w, h); }
    c.restore();
  }
  /** Gancho de pruebas: la tarjeta `k` pintada aparte (orig: como salio de la camara). */
  E.lookCardRef = function (k, orig) {
    const card = $(`.lcard[data-k="${k}"] canvas`); if (!card) return null;
    const cv = document.createElement("canvas"); cv.width = card.width; cv.height = card.height;
    paintCard(cv.getContext("2d"), cv.width, cv.height, k, orig);
    return cv;
  };
  const LookPicker = E.LookPicker = {
    /** o = {cols, onPick(look), onHover(look|null)} */
    create(o) {
      const wrap = document.createElement("div"); wrap.className = "lp-wrap";
      const opts = (UI().options || {})["grade.look"] || {};
      // si fallan, todas las fotos son el original: el aviso va ARRIBA de la galeria (se ve sin desplazarse)
      wrap.innerHTML = `<p class="lp-err" role="status" hidden><span>${esc(T("look.error"))}</span> <button type="button" class="linkbtn" data-a="retry">${esc(T("look.retry"))}</button></p>`
        + `<div class="gal lp" role="radiogroup" aria-label="${esc(T("look.aria"))}" style="--cols:${o.cols || 3}">`
        + C.LOOK_ORDER.filter(k => opts[k]).map(k => `<button type="button" class="gcard lcard" role="radio" aria-checked="false" data-k="${k}">`
          + `<span class="gshot"><canvas aria-hidden="true"></canvas>${opts[k].badge ? `<span class="lbadge">${esc(opts[k].badge)}</span>` : ""}`
          + `<span class="lchip lhold" aria-hidden="true">${esc(T("look.hold"))}</span><span class="lchip lorig" aria-hidden="true">${esc(T("look.orig"))}</span></span>`
          + `<span class="gmeta"><span class="gname">${esc(opts[k].label)}</span><span class="gdesc">${esc(opts[k].help || "")}</span>`
          + `<span class="gchosen">${CHK}${esc(T("gallery.chosen"))}</span></span></button>`).join("")
        + `</div><p class="lp-load" role="status" hidden>${esc(T("look.loading"))}</p>`;
      const gal = $(".lp", wrap), cards = $$(".lcard", wrap);
      $('[data-a="retry"]', wrap).onclick = () => requestThumbs(true);
      cards.forEach(b => {
        const cv = $("canvas", b), k = b.dataset.k;
        let t0 = 0, long = false;
        // mientras se mantiene presionada, la tarjeta y la vista grande muestran el original
        const hold = on => { b._held = on; b.classList.toggle("held", on); if (o.onHold) o.onHold(on); Ticker.kick(); };
        b.addEventListener("pointerdown", e => { if (e.button) return; t0 = performance.now(); long = false; hold(true); });
        const up = () => { if (!b._held) return; long = performance.now() - t0 > 350; hold(false); };
        b.addEventListener("pointerup", up);
        b.addEventListener("pointercancel", up);
        b.addEventListener("pointerleave", () => { up(); if (o.onHover) o.onHover(null); });
        // mantener presionado es para comparar: al soltar no elige (un clic normal si)
        b.addEventListener("click", e => { if (long) { long = false; e.preventDefault(); return; } o.onPick(k); });
        b.addEventListener("keydown", e => {
          if (e.key !== " ") return;
          e.preventDefault(); e.stopPropagation();
          if (!e.repeat && !b._held) { t0 = performance.now(); hold(true); }
        });
        b.addEventListener("keyup", e => {
          if (e.key !== " ") return;
          e.preventDefault(); e.stopPropagation();
          const short = performance.now() - t0 < 350; hold(false);
          if (short) o.onPick(k);
        });
        b.addEventListener("pointermove", e => { if (o.onHover && (e.movementX || e.movementY)) o.onHover(k); });
        b.addEventListener("focus", () => o.onHover && o.onHover(k));
        b.addEventListener("blur", () => { if (b._held) hold(false); if (o.onHover) o.onHover(null); });
        Ticker.add({ el: cv, name: "look", fps: 30, static: true, draw: () => { fitCanvas(cv); paintCard(cv.getContext("2d"), cv.width, cv.height, k, b._held); } });
      });
      radioKeys(gal);
      function sync() {
        const v = gcfg().enabled === false ? null : curLook();
        cards.forEach(b => { const on = b.dataset.k === v; b.setAttribute("aria-checked", String(on)); b.classList.toggle("on", on); });
        roving(gal);
      }
      const view = { el: wrap, sync: () => {
        // la primera vez (sin fotos todavia): el original con un brillo que pasa y la linea de estado
        const first = LP.st === "loading" && !Object.keys(LP.thumbs).length;
        gal.classList.toggle("loading", first); gal.setAttribute("aria-busy", String(first));
        $(".lp-load", wrap).hidden = !first;
        $(".lp-err", wrap).hidden = LP.st !== "error";
        sync();
      } };
      LP.views.add(view);            // (todavia fuera del documento: no se poda aqui)
      view.sync();
      return { el: wrap, update: sync, destroy() { LP.views.delete(view); wrap.remove(); } };
    },
  };

  // ------------------------------------------------------------------ Color del video: ajustes finos
  const LOOK_IDS = ["grade.look", "grade.match_shots", "grade.exposure", "grade.temperature", "grade.strength", "grade.tint",
    "grade.saturation", "grade.contrast", "grade.clarity", "grade.vignette", "grade.grain", "grade.sharpen", "grade.enabled"];
  E.LOOK_IDS = LOOK_IDS;
  const GDEF = { exposure: 0, temperature: 0, tint: 0, saturation: 1, contrast: 0, strength: 1, vignette: 0, grain: 0, sharpen: 0 };
  const gradeOff = () => gcfg().enabled === false;
  const gval = k => { const v = gcfg()[k]; return v == null || v === "" || !isFinite(+v) ? GDEF[k] : +v; };
  function dirText(k, x) {
    const v = Math.abs(x);
    if (k === "exposure") return x < 0 ? T("grade.dir.exposure.neg", { v }) : T("grade.dir.exposure.pos", { v });
    if (k === "temperature") return x < 0 ? T("grade.dir.temperature.neg", { v }) : T("grade.dir.temperature.pos", { v });
    return x < 0 ? T("grade.dir.tint.neg", { v }) : T("grade.dir.tint.pos", { v });
  }
  /** Nota de regla de una fila (se cambia en sitio, sin rehacer la fila). */
  function setNote(r, text) {
    const tx = $(".hrow-txt", r.el); let n = $(".hrow-note", tx);
    if (text && !n) { n = document.createElement("p"); n.className = "hrow-note"; tx.appendChild(n); }
    if (n) { if (text) { if (n.textContent !== text) n.textContent = text; } else n.remove(); }
  }
  /** Dos o tres fotos chicas con su nombre: el ejemplo del motor de un ajuste fino. */
  function exFigs(list) {
    const box = document.createElement("div"); box.className = "tri" + (list.length === 2 ? " two" : list.length === 1 ? " one" : "");
    box.innerHTML = list.map(x => `<figure><canvas aria-hidden="true"></canvas><figcaption>${esc(x.label)}</figcaption></figure>`).join("");
    $$("canvas", box).forEach((cv, i) => Ticker.add({ el: cv, name: "gex", fps: 8, static: true, draw: () => paintFig(cv, list[i]) }));
    return box;
  }
  function paintFig(cv, x) {
    fitCanvas(cv);
    const c = cv.getContext("2d"), w = cv.width, h = cv.height, own = x.img(), img = own || LP.src;
    const crop = x.crop === "zoom" && !own ? "face" : x.crop;     // sin el recorte del motor todavia: la cara
    c.save(); c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, w, h);
    if (img) {
      const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height, f = LP.face || [0.5, 0.42];
      let sw = iw, sh = ih, sx = 0, sy = 0;
      if (crop === "wide") { sh = Math.min(ih, iw * h / w); sy = clamp(f[1] * ih - sh / 2, 0, ih - sh); }
      else if (crop !== "zoom") { sw = iw * 0.55; sh = Math.min(ih, sw * h / w); sx = clamp(f[0] * iw - sw / 2, 0, iw - sw); sy = clamp(f[1] * ih - sh / 2, 0, ih - sh); }
      c.imageSmoothingEnabled = crop !== "zoom";            // al 200 %: cada pixel del cuadro, nitido
      c.drawImage(img, sx, sy, sw, sh, 0, 0, w, h);
    }
    c.restore();
  }
  const vimg = (k, i) => () => { const v = LP.vlist && LP.vlist[k]; return v ? LP.vars[`${k}:${v[i]}`] || null : null; };
  const thisImg = () => (LP.st === "ready" ? LP.thumbs[curLook()] || null : null);
  function triFor(k) {
    const lb = { exposure: ["ex.less", "ex.more"], temperature: ["ex.cold", "ex.warm"], tint: ["ex.green", "ex.pink"] }[k] || ["ex.less", "ex.more"];
    return [{ label: T(lb[0]), img: vimg(k, 0) }, { label: T("ex.same"), img: thisImg }, { label: T(lb[1]), img: vimg(k, 1) }];
  }
  /** Orillas: las tres opciones a lo ancho; grano y nitidez: sin y con (la mas marcada), en un recorte
      chico de tu cuadro a tamano completo (en miniatura no se notan). */
  function choiceFigs(id, k) {
    const ch = copy(id).basic_choices || [];
    if (k === "vignette") return ch.map(([v, l]) => ({ label: l, crop: "wide", img: () => LP.vars[`${k}:${v}`] || null }));
    return [ch[0], ch[ch.length - 1]].filter(Boolean).map(([v, l]) => ({ label: l, crop: "zoom", img: () => LP.det[`${k}:${v}`] || null }));
  }
  /** Ejemplo de «Igualar el color entre tomas»: la toma de muestra y otra, con el color de ahora
      (con las tablas de cada toma si se igualan y el analisis ya las midio). */
  const MC = { key: "", imgs: [] };
  function matchImg(i) {
    const p = FX.lookPayload && FX.lookPayload(), src = i ? Frames.img2 : Frames.img;
    if (!src) return null;
    const on = p && p.enabled !== false ? p : null, key = `${payId(on)}|${Frames.key}|${!!Frames.img2}`;
    if (MC.key !== key) { MC.key = key; MC.imgs = []; }
    if (MC.imgs[i]) return MC.imgs[i];
    const W = 96, H = Math.round(W / fmtAspect()), cv = document.createElement("canvas"); cv.width = W; cv.height = H;
    const c = cv.getContext("2d");
    FX.drawCover(c, src, 0, 0, W, H, Frames.focusX);
    if (on) { try { const d = c.getImageData(0, 0, W, H); C.applyLook(on, d.data, W, H, { t: i ? Frames.other.t : Frames.t }); c.putImageData(d, 0, 0); } catch (e) { /* sin lectura */ } }
    MC.imgs[i] = cv;
    return cv;
  }
  function toggleCtl(checked, onSet) {
    const i = document.createElement("input"); i.type = "checkbox"; i.className = "toggle"; i.checked = checked;
    i.onchange = () => onSet(i.checked);
    return i;
  }
  const gEdit = fn => { FX.edit(() => fn(gcfg()), { inspector: false }); E.refreshSoon(0); };
  const liveGrade = () => { FX.syncLook(); Ticker.kick(); };
  function matchRow() {
    const single = (PLAN.shots || []).length < 2;
    const i = toggleCtl(gcfg().match_shots !== false, v => gEdit(g => { g.match_shots = v; }));
    const figs = [{ label: T("ex.shot", { n: 1 }), crop: "wide", img: () => matchImg(0) }, { label: T("ex.shot", { n: 2 }), crop: "wide", img: () => matchImg(1) }];
    const r = HelpRow.create({ id: "grade.match_shots", ctx: {}, control: i, example: exFigs(single ? figs.slice(0, 1) : figs), exKind: "antes_despues", layout: "wide" });
    r.sync = () => {
      i.checked = gcfg().match_shots !== false; i.disabled = single || gradeOff();
      const p = FX.lookPayload && FX.lookPayload();
      setNote(r, single ? T("match.single") : gradeOff() ? T("grade.off") : i.checked && p && p.shots_state === "pending" ? T("match.pending") : null);
    };
    r.sync();
    return r;
  }
  function enabledRow() {
    const i = toggleCtl(!gradeOff(), v => gEdit(g => { g.enabled = v; }));
    const r = HelpRow.create({ id: "grade.enabled", ctx: {}, control: i, layout: "wide" });
    r.sync = () => { i.checked = !gradeOff(); };
    return r;
  }
  /** Barra con el valor en palabras («17 % más oscuro», «116 %») y, en Contraste, el total con el estilo. */
  function rangeRowG(id, k) {
    const cp = copy(id), a = cp.adv;
    const ctl = document.createElement("div"); ctl.className = "g-ctl";
    const line = document.createElement("div"); line.className = "adv-line";
    const rg = document.createElement("input"); rg.type = "range"; rg.min = a.min; rg.max = a.max; rg.step = a.step || 1;
    const hv = document.createElement("span"); hv.className = "hv";
    line.append(rg, hv); ctl.appendChild(line);
    const tot = k === "contrast" ? document.createElement("span") : null;
    if (tot) { tot.className = "g-total"; ctl.appendChild(tot); }
    const words = k === "exposure" || k === "temperature" || k === "tint";
    function show() {
      const x = Math.round(gval(k) * a.scale), s = words ? (x ? dirText(k, x) : a.zero) : C.humanValue(a, gval(k));
      hv.textContent = s; rg.setAttribute("aria-valuetext", s);
      if (tot) tot.textContent = T("grade.total", { v: C.humanValue(a, C.lookTotal(lookBase(), gcfg(), "contrast")) });
    }
    FX.gesture(rg, () => { gcfg()[k] = +(+rg.value / a.scale).toFixed(4); show(); liveGrade(); }, d => { if (d) { FX.changed({ inspector: false }); E.refreshSoon(0); } });
    // el ejemplo: Menos · Así · Más; con un estilo sin color (Blanco y negro) el ajuste no hace nada:
    // una sola foto (asi se ve) y la nota dice por que
    const exSlot = document.createElement("div"); exSlot.className = "g-ex";
    let exDead = null;
    const r = HelpRow.create({ id, ctx: {}, control: ctl, example: exSlot, exKind: "antes_despues", layout: "wide" });
    r.sync = () => {
      const dead = !C.gradeOptionApplies(curLook(), k) && !gradeOff();
      if (dead !== exDead) { exDead = dead; exSlot.innerHTML = ""; exSlot.appendChild(exFigs(dead ? [{ label: T("ex.same"), img: thisImg }] : triFor(k))); }
      rg.disabled = gradeOff() || dead;
      if (document.activeElement !== rg) rg.value = String(Math.round(gval(k) * a.scale));
      show(); setNote(r, gradeOff() ? T("grade.off") : dead ? copy(id, { kind: curLook() }).note : null);
    };
    r.sync();
    return r;
  }
  /** Definición: la del estilo (vacío) o la tuya; la barra muestra la que se usa. */
  function clarityRowG() {
    const ctl = document.createElement("div"); ctl.className = "g-ctl";
    const line = document.createElement("div"); line.className = "adv-line";
    const rg = document.createElement("input"); rg.type = "range"; rg.min = 0; rg.max = 100; rg.step = 5;
    const hv = document.createElement("span"); hv.className = "hv";
    line.append(rg, hv); ctl.appendChild(line);
    const rs = document.createElement("button"); rs.type = "button"; rs.className = "linkbtn g-reset"; rs.textContent = T("grade.reset"); rs.setAttribute("aria-label", T("grade.reset.aria"));
    ctl.appendChild(rs);
    const own = () => { const v = gcfg().clarity; return v == null || v === "" || !isFinite(+v) ? null : +v; };
    function show() {
      const eff = Math.round(C.lookTotal(lookBase(), gcfg(), "clarity") * 100), s = own() == null ? T("grade.auto", { v: `${eff} %` }) : `${Math.round(own() * 100)} %`;
      hv.textContent = s; rg.setAttribute("aria-valuetext", s); rs.hidden = own() == null;
      if (document.activeElement !== rg) rg.value = String(eff);
    }
    FX.gesture(rg, () => { gcfg().clarity = +(+rg.value / 100).toFixed(3); show(); liveGrade(); }, d => { if (d) { FX.changed({ inspector: false }); E.refreshSoon(0); } });
    rs.onclick = () => gEdit(g => { g.clarity = null; });
    const r = HelpRow.create({ id: "grade.clarity", ctx: {}, control: ctl, example: exFigs(triFor("clarity")), exKind: "antes_despues", layout: "wide" });
    r.sync = () => { rg.disabled = gradeOff(); show(); setNote(r, gradeOff() ? T("grade.off") : null); };
    r.sync();
    return r;
  }
  /** Sin · Suave · Fuerte (orillas, grano, nitidez); las orillas dicen el total con el estilo. */
  function choiceRowG(id, k) {
    const cp = copy(id);
    const ctl = document.createElement("div"); ctl.className = "g-ctl";
    const seg = Segmented.create({ choices: (cp.basic_choices || []).map(([v, l]) => ({ v, label: l })), value: gval(k), nearest: true, onPick: v => gEdit(g => { g[k] = v; }) });
    ctl.appendChild(seg.el);
    const tot = k === "vignette" ? document.createElement("span") : null;
    if (tot) { tot.className = "g-total"; ctl.appendChild(tot); }
    const r = HelpRow.create({ id, ctx: {}, control: ctl, example: exFigs(choiceFigs(id, k)), exKind: "antes_despues", layout: "wide" });
    r.sync = () => {
      seg.update({ value: gval(k), disabled: gradeOff() });
      if (tot) tot.textContent = T("grade.total", { v: C.humanValue({ scale: 100, fmt: "{v} %" }, C.lookTotal(lookBase(), gcfg(), "vignette")) });
      setNote(r, gradeOff() ? T("grade.off") : null);
    };
    r.sync();
    return r;
  }
  /** Construye la sección Color del video. */
  function LookSection(box) {
    const S = { rows: {}, sig: "" };
    function pick(k) {
      const g = gcfg();
      PV.hoverLook = null;
      if (g.look !== k || g.enabled === false) FX.edit(() => { const x = gcfg(); x.look = k; x.enabled = true; }, { inspector: false });
      // la primera vez que eliges un estilo de color, la vista grande compara con el original
      if (!PV.abAuto) { PV.abAuto = true; setAb(true); }
      E.refresh();
    }
    function hover(h) { PV.hoverLook = h; if (h) ensureLookSet(); syncPreviewState(); Ticker.kick(); }
    function build() {
      const lv = E.level(), ids = C.visibleRows(UI(), LOOK_IDS, lv, {});
      S.sig = JSON.stringify([ids, lv]);
      box.innerHTML = ""; S.rows = {};
      let sub = false;
      ids.forEach(id => {
        let r = null;
        const k = id.split(".")[1];
        if (id === "grade.look") {
          const g = LookPicker.create({ cols: 3, onPick: pick, onHover: hover, onHold: on => { PV.holdOrig = on; syncPreviewState(); Ticker.kick(); } });
          r = HelpRow.create({ id, ctx: {}, control: g.el, layout: "wide", full: true, exKind: "ninguno" });
          r.picker = g;
        } else {
          if (!sub) { sub = true; box.insertAdjacentHTML("beforeend", `<h4 class="g-sub">${esc(T("grade.fine"))}</h4>`); }
          if (k === "match_shots") r = matchRow();
          else if (k === "enabled") r = enabledRow();
          else if (k === "clarity") r = clarityRowG();
          else if (["vignette", "grain", "sharpen"].includes(k)) r = choiceRowG(id, k);
          else r = rangeRowG(id, k);
        }
        if (r) { S.rows[id] = r; box.appendChild(r.el); }
      });
      const n = (UI().notes || {})["grade.paused"];
      if (n) box.insertAdjacentHTML("beforeend", `<p class="hrow-rule">${esc(n)}</p>`);
    }
    function update() {
      const lv = E.level(), ids = C.visibleRows(UI(), LOOK_IDS, lv, {});
      if (JSON.stringify([ids, lv]) !== S.sig) { build(); return; }
      Object.values(S.rows).forEach(r => { if (r.picker) r.picker.update(); else if (r.sync) r.sync(); });
    }
    build();
    return { update, S };
  }
  /** Llegaron tablas nuevas del color del proyecto (fx.js): todo lo que muestra el color se redibuja. */
  E.lookChanged = function () {
    Frames.layers.clear(); MC.key = "";
    if (VIEW.look) VIEW.look.update();
    if (VIEW.mounted) lookSoon();
    syncPreviewState(); Ticker.kick();
  };
  /** Gancho de pruebas: vuelve a pedir las miniaturas ya (sin esperar 250 ms). */
  E.lookReload = () => requestThumbs(true);
  /** Gancho de pruebas: estado de las miniaturas y de la vista grande. */
  E.lookInfo = () => ({ st: LP.st, thumbs: Object.keys(LP.thumbs), vars: Object.keys(LP.vars), det: Object.keys(LP.det), ms: LP.ms,
    abOn: PV.abOn, ab: PV.ab, hoverLook: PV.hoverLook, gl: !!(window.LookGL && LookGL.supported()), fresh: !!(FX.lookFresh && FX.lookFresh()),
    shots: (FX.lookPayload && FX.lookPayload() || {}).shots_state || null, src: LP.src ? [LP.src.width, LP.src.height] : null });
  /** Gancho de pruebas: el color de cada estilo dibujado en el navegador (LookGL) contra la foto del
      motor, sobre el mismo cuadro -> {look: error medio 0..255}. */
  E.lookParity = function () {
    const out = {}, src = LP.src; if (!src) return out;
    const W = src.width, H = src.height, gl = window.LookGL ? LookGL.create() : null;
    C.LOOK_ORDER.forEach(k => {
      const p = LK.set[k], im = LP.thumbs[k]; if (!p || !im) return;
      const a = document.createElement("canvas"); a.width = W; a.height = H;
      const ca = a.getContext("2d");
      if (!(gl && (gl.setLut(p), gl.draw(ca, src, 0, 0, W, H, 0, 0, W, H, null, { face: LP.face })))) {
        ca.drawImage(src, 0, 0); const d = ca.getImageData(0, 0, W, H); C.applyLook(p, d.data, W, H, { face: LP.face }); ca.putImageData(d, 0, 0);
      }
      const b = document.createElement("canvas"); b.width = W; b.height = H;
      const cb = b.getContext("2d"); cb.drawImage(im, 0, 0, W, H);
      const da = ca.getImageData(0, 0, W, H).data, db = cb.getImageData(0, 0, W, H).data;
      let s = 0; for (let i = 0; i < da.length; i += 4) s += Math.abs(da[i] - db[i]) + Math.abs(da[i + 1] - db[i + 1]) + Math.abs(da[i + 2] - db[i + 2]);
      out[k] = +(s / (da.length / 4 * 3)).toFixed(2);
    });
    return out;
  };

  // ------------------------------------------------------------------ secciones, encabezado y montaje
  /* Secciones que agrega estilo_proyecto.js (Edicion con IA, Musica, Quien sale y tu logo, Formato):
     {id, build(body) -> {update()}, summary() -> {k, v, go, aria, focus?}}. */
  const EXTRA = [];
  E.addSection = function (def) { EXTRA.push(def); if (VIEW.mounted) renderEstilo(); };
  const closedSet = () => { try { return new Set(JSON.parse(lsGet("reelfy.estilo.closed") || "[]")); } catch (e) { return new Set(); } };
  /* Controles fijos de index.html movidos a Estilo con sus ids (formato, idioma, aire, audio de la voz,
     logo, plantillas): esperan en #stPark y cada seccion los pone en su lugar (E.placeStatic). En el
     modo clasico (sin edicion IA) van al grupo del panel Proyecto (#projLegacyRows). */
  function park(el) { const p = $("#stPark"); if (p) $$("[data-static]", el).forEach(r => p.appendChild(r)); }
  /** Pone las filas fijas `ids` (en ese orden) al final de `body`; las avanzadas no se ven en Basico. */
  E.placeStatic = function (body, ids) {
    const adv = E.level() === "advanced";
    ids.forEach(id => {
      const r = document.getElementById(id); if (!r || !body) return;
      if (r.parentElement !== body || r !== body.lastElementChild) body.appendChild(r);
      if (!r.dataset.hyd) { r.dataset.hyd = "1"; HelpRow.hydrate(r); E.hydrateStrings(r); }
      r.hidden = !adv && r.dataset.level === "advanced";
    });
  };
  /** Modo clasico (v1): las filas fijas al panel Proyecto; edicion IA: de vuelta a Estilo. */
  E.home = function (pro) {
    const L = $("#projLegacyRows"); if (!L) return;
    const rows = $$("[data-static]");
    // en el panel (angosto) van en la forma angosta de las filas; en Estilo, en la ancha
    rows.forEach(r => { r.classList.toggle("narrow", !pro); r.classList.toggle("wide", !!pro); });
    if (!pro) {
      rows.forEach(r => { if (r.parentElement !== L) L.appendChild(r); r.hidden = false; });
      if (UI().strings) { HelpRow.hydrate(L); E.hydrateStrings(L); rows.forEach(r => { r.dataset.hyd = "1"; }); }
      return;
    }
    if (rows.some(r => r.parentElement === L)) { park(L); if (VIEW.mounted) E.refresh(); }
  };
  function sectionShell(id) {
    const el = document.getElementById(id); if (!el) return null;
    park(el);
    const sec = (UI().sections || {})[id] || {};
    const open = !closedSet().has(id), bid = uid("sh");
    el.hidden = false;
    el.innerHTML = `<h3 class="st-h"><button type="button" class="st-sh" aria-expanded="${open}" aria-controls="${id}Body" id="${bid}" title="${esc(T("section.toggle"))}">`
      + `<span class="st-sh-t"><span class="st-sh-title">${esc(sec.title || "")}</span><span class="st-sh-help">${esc(sec.help || "")}</span></span><span class="st-chev" aria-hidden="true"></span></button></h3>`
      + `<div class="st-body" id="${id}Body" role="region" aria-labelledby="${bid}"${open ? "" : " hidden"}></div>`;
    $(".st-sh", el).onclick = () => {
      const b = $(".st-body", el), now = b.hidden;
      b.hidden = !now; $(".st-sh", el).setAttribute("aria-expanded", String(now));
      const s = closedSet(); if (now) s.delete(id); else s.add(id); lsSet("reelfy.estilo.closed", JSON.stringify([...s]));
      Ticker.kick();
    };
    return $(".st-body", el);
  }
  const VIEW = { caps: null, brand: null, look: null, insp: null, mounted: false, extra: {} };
  function renderEstilo() {
    if (!hasPlan() || !$("#stylePane")) return;
    const t = $("#stTitle"), h = $("#stHelp");
    if (t) t.textContent = T("estilo.title");
    if (h) h.textContent = T("estilo.help");
    const lv = $("#stLevel"); if (lv) { lv.innerHTML = ""; lv.appendChild(E.LevelSwitch.create().el); }
    const pt = $("#stPvTitle"); if (pt) pt.textContent = T("preview.title");
    const cv = $("#stPvCv"); if (cv) { cv.setAttribute("aria-label", T("preview.aria")); cv.setAttribute("role", "img"); }
    const cb = sectionShell("stCaps"); if (cb) VIEW.caps = CapsSection(cb, "wide");
    const bb = sectionShell("stBrand"); if (bb) { VIEW.brand = AccentPreview.create({ layout: "wide" }); bb.appendChild(VIEW.brand.el); }
    const lb = sectionShell("stLook"); if (lb) VIEW.look = LookSection(lb);
    EXTRA.forEach(d => { const b = sectionShell(d.id); VIEW.extra[d.id] = b ? d.build(b) : null; });
    E.hydrateStrings($("#stylePane"));
    wireAb(); syncAb();
    syncPreviewState(); syncPreviewNote();
    lookSoon(0);
  }
  function renderAll() {
    if (VIEW.mounted) renderEstilo();
    if (VIEW.insp && VIEW.insp.root.isConnected) renderInspector(VIEW.insp.box);
    E.summary();
  }
  /** Una vez, cuando el catalogo (con la copy) ya llego. */
  E.mount = function () {
    if (VIEW.mounted || !$("#stylePane") || !(UI().strings)) return false;
    VIEW.mounted = true;
    PV.cv = $("#stPvCv");
    if (PV.cv) Ticker.add({ el: PV.cv, name: "preview", fps: 60, draw: t => drawPreview(t) });
    window.addEventListener("resize", () => { sizePreview(); Ticker.kick(); });
    renderEstilo();
    return true;
  };
  E.show = function () {
    if (!hasPlan()) return;
    E.mount();
    if (typeof V !== "undefined" && V && !V.paused) V.pause();
    Frames.ensure();
    requestAnimationFrame(() => { sizePreview(); Ticker.kick(); lookSoon(60); });
    E.refresh();
  };
  E.hide = function () { PV.hover = null; PV.hoverLook = null; PV.holdOrig = false; };
  /** Abre Estilo en una seccion y enfoca un ajuste (los enlaces "Cambiar"). */
  E.open = function (section, o) {
    if (window.FX && FX.clean && FX.clean.setMode) FX.clean.setMode("style");
    E.mount();
    const el = document.getElementById(section); if (!el) return;
    const body = $(".st-body", el);
    if (body && body.hidden) $(".st-sh", el).click();
    requestAnimationFrame(() => {
      el.scrollIntoView({ block: "start", behavior: REDUCED() ? "auto" : "smooth" });
      const f = o && o.focus ? $(`[data-setting="${o.focus}"]`, el) : null;
      const tgt = f ? $("button:not([disabled]),input:not([disabled]),select:not([disabled])", f) : $(".st-sh", el);
      if (tgt) tgt.focus({ preventScroll: true });
    });
  };
  /** "Corregir el texto de los subtítulos": la transcripción del panel Proyecto (Edición). */
  E.openTranscript = function () {
    if (window.FX && FX.clean && FX.clean.setMode) FX.clean.setMode("edit");
    if (window.setPanelTab) setPanelTab("proj");
    requestAnimationFrame(() => { const s = $("#subs"); if (s) { s.scrollIntoView({ block: "center" }); const w = $("span,button", s); if (w && w.focus) w.focus({ preventScroll: true }); } });
  };

  // ------------------------------------------------------------------ inspector: Elemento › Subtítulos
  function renderInspector(b) {
    b.innerHTML = "";
    const h = document.createElement("div"); h.className = "ins-head caps-head"; h.style.setProperty("--c", "126,166,216");
    h.innerHTML = `<div class="ins-sw">${ICO("type")}</div><div class="ins-title"><b>${esc(T("caps.inspector.title"))}</b><span>${esc(T("caps.inspector.sub"))}</span></div>`;
    const lv = document.createElement("div"); lv.className = "caps-lvl"; lv.appendChild(E.LevelSwitch.create().el);
    b.appendChild(h); b.appendChild(lv);
    const box = document.createElement("div"); box.className = "caps-insp";
    b.appendChild(box);
    VIEW.insp = { box: b, root: box, sec: CapsSection(box, "narrow") };
  }
  E.renderCaptionsInspector = function (b) { if (!hasPlan() || !(UI().strings)) return false; Frames.ensure(); renderInspector(b); return true; };

  // ------------------------------------------------------------------ resumen en Proyecto
  E.summary = function () {
    const box = $("#styleSummary"); if (!box || !hasPlan() || !(UI().strings)) return;
    const c = cap(), pr = cardOf();
    const yName = (C.nearestChoice(copy("captions.y").basic_choices || [], c.y == null ? 0.72 : +c.y) || [0, ""])[1].toLowerCase();
    const colOf = pr === "boxed" ? (c.highlight || brand()) : pr === "minimal_small" ? (c.color || "#FFFFFF") : brand();
    const capTxt = pr === "none" ? T("summary.off") : [presetName(pr), C.colorName(colOf), yName].filter(Boolean).join(" · ");
    box.innerHTML = `<h4>${ICO("drop")}${esc(T("summary.title"))}</h4>`
      + `<div class="ss-line"><span class="ss-k">${esc(T("summary.caps"))}</span><span class="ss-v">${esc(capTxt)}</span><button type="button" class="linkbtn" data-go="stCaps" aria-label="${esc(T("summary.change.caps"))}">${esc(T("summary.change"))}</button></div>`
      + `<div class="ss-line"><span class="ss-k">${esc(T("summary.brand"))}</span><span class="ss-v"><i class="cw-dot" style="--c:${brand()}" aria-hidden="true"></i>${esc(C.colorName(brand()))} · ${esc(brand())}</span><button type="button" class="linkbtn" data-go="stBrand" aria-label="${esc(T("summary.change.brand"))}">${esc(T("summary.change"))}</button></div>`
      + `<div class="ss-line"><span class="ss-k">${esc(T("summary.look"))}</span><span class="ss-v">${esc(gcfg().enabled === false ? T("summary.look.off") : lookName(curLook()))}</span><button type="button" class="linkbtn" data-go="stLook" aria-label="${esc(T("summary.change.look"))}">${esc(T("summary.change"))}</button></div>`;
    EXTRA.forEach(d => {
      const x = d.summary && d.summary(); if (!x) return;
      box.insertAdjacentHTML("beforeend", `<div class="ss-line"><span class="ss-k">${esc(x.k)}</span><span class="ss-v">${esc(x.v)}</span><button type="button" class="linkbtn" data-go="${esc(d.id)}"${x.focus ? ` data-focus="${esc(x.focus)}"` : ""} aria-label="${esc(x.aria)}">${esc(T("summary.change"))}</button></div>`);
    });
    $$("[data-go]", box).forEach(b => { b.onclick = () => E.open(b.dataset.go, b.dataset.go === "stBrand" ? { focus: "project.accent" } : b.dataset.focus ? { focus: b.dataset.focus } : null); });
  };

  // ------------------------------------------------------------------ refresco (desde FX.changed)
  let rTimer = null;
  E.refreshSoon = function (ms) { clearTimeout(rTimer); rTimer = setTimeout(() => E.refresh(), ms == null ? 100 : ms); };
  /** Relee el plan y actualiza los componentes sin reconstruir el DOM (el foco sobrevive). */
  E.refresh = function () {
    if (!hasPlan() || !(UI().strings)) return;
    Groups.sig = ""; AP.ver++;
    if (Frames.key && frameKey() !== Frames.key && (VIEW.mounted || (VIEW.insp && VIEW.insp.root.isConnected))) Frames.ensure();
    if (VIEW.caps) VIEW.caps.update();
    if (VIEW.brand) VIEW.brand.update();
    if (VIEW.look) { VIEW.look.update(); lookSoon(); }
    if (VIEW.insp && VIEW.insp.root.isConnected && VIEW.insp.sec) VIEW.insp.sec.update();
    Object.values(VIEW.extra).forEach(x => { if (x && x.update) x.update(); });
    if (VIEW.mounted) sizePreview();                 // el formato del proyecto pudo cambiar (Estilo › Formato)
    syncPreviewState();
    E.summary();
    Ticker.kick();
  };
  /** Reemplazo del plan (deshacer, abrir otro proyecto): todo se relee. */
  E.planReplaced = function () { AP.sig = ""; E.refresh(); };

  // ------------------------------------------------------------------ teclado
  /** Teclas del Studio en Estilo: 1 · 2 · 3 cambian de modo; en Estilo las teclas del reproductor
      no actuan (la vista es un bucle, no la linea de tiempo). -> true si la tecla ya se atendio. */
  E.onKey = function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return false;
    const st = window.FX && FX.clean;
    if (!st || !hasPlan()) return false;
    if (["1", "2", "3"].includes(e.key) && !e.repeat) {
      const m = { 1: "clean", 2: "edit", 3: "style" }[e.key];
      e.preventDefault(); st.setMode(m); return true;
    }
    if (st.mode === "style") return !["Escape"].includes(e.key);   // Espacio, flechas...: los controles enfocados
    return false;
  };

  // ------------------------------------------------------------------ piezas para estilo_proyecto.js
  const CFG_GROUPS = new Map();
  /** Subtitulos de muestra con una configuracion dada (tarjetas de los estilos de edicion): grupos
      aproximados con su max_words, los colores del proyecto. -> caja dibujada o null. */
  function drawCapsCfg(c, W, H, t, cfg, fonts) {
    const w = Frames.win; if (!w || !w.words.length) return null;
    // (fonts: las letras de OTRO estilo de edicion, p. ej. su tarjeta; si no, las del proyecto)
    const F = fonts || FX.capTitleFont(PLAN);
    const key = JSON.stringify([cfg.preset, cfg.max_words, cfg.case, cfg.size, cfg.font, F, Frames.key]);
    let gs = CFG_GROUPS.get(key);
    if (!gs) { if (CFG_GROUPS.size > 24) CFG_GROUPS.clear(); gs = C.captionGroupsFor([w.words], cfg, F); CFG_GROUPS.set(key, gs); }
    const gr = C.capGroupAt(gs, t); if (!gr) return null;
    return CapDraw.drawGroup(c, FX.layoutBox(W, H), gr, t, { cfg, accent: brand(), srv: false, yFrac: cfg.y == null ? 0.72 : +cfg.y, rangeAlpha: 1,
      titleFont: F, fontCss: FX.capFontCss, ensureFont: FX.ensureFont });
  }
  E._k = { $, $$, esc, ICO, CHK, clamp, DPR, REDUCED, lsGet, lsSet, uid, hasPlan, UI, T, copy, optLabel, HelpRow, Segmented, Ticker, Frames,
    fitCanvas, radioKeys, roving, frameLayer, drawBrandMini, drawCapsCfg, fmtAspect, brand, cap, ensureLookSet, stillTime, miniOff: (W, H) => miniOff(W, H),
    presetName, lookName };
})();
