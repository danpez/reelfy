/* Reelfy Studio · Estilo, segunda parte (claridad de ajustes, fase 3): Edición con IA, Música,
   Quién sale y tu logo, y Formato, idioma y audio.
   - Audio + ListenButton: «Escuchar» donde algo suena (muestra de 6 s de cada ambiente con
     GET /fx/mood_sample, canciones de la biblioteca, la música creada, tu voz antes y después,
     5 s de tu video con la música, los sonidos del menú Agregar). Un solo sonido a la vez y las
     muestras se cortan solas.
   - Edición con IA: los cuatro estilos con TU cuadro, sus subtítulos, su título y su color
     (FXCore.DIRECTOR_CARDS), «Qué cambia», la cantidad de efectos con el conteo real del plan,
     rehacer la edición y otra versión.
   - Música: origen, ambiente y volumen con ejemplo para escuchar, y la música de tu video; en
     Avanzado el resto (ritmo, tonalidad, final...). Lo que depende del momento del video (golpe,
     pausas, subidas) queda en Elemento › Música.
   - Quién sale y tu logo: tus datos (perfil), dónde aparecen, el logo y las plantillas.
   - Formato, idioma y audio: los controles de siempre movidos desde el panel Proyecto con sus ids
     (segFmt, segLang, air, enhance, logoOn...), cada uno en su fila con ayuda y ejemplo.
   Se registra en estilo.js (Estilo.addSection) y usa sus piezas (Estilo._k). Todo texto viene de
   ui_copy.json; toda escritura al plan pasa por FX.edit (un paso de deshacer). Lee los globales de
   index.html (PLAN, V, FMT, LANG, LOGO_*, TRACKS...) solo dentro de funciones. */
(function () {
  "use strict";
  const E = window.Estilo;
  if (!E || !E._k) return;
  const C = window.FXCore;
  const K = E._k;
  const { $, $$, esc, ICO, CHK, clamp, REDUCED, uid, T, copy, optLabel, HelpRow, Segmented, Ticker, fitCanvas, radioKeys, roving } = K;
  const UI = () => K.UI();
  const has = n => typeof window[n] !== "undefined";
  const pro = () => K.hasPlan();

  // ------------------------------------------------------------------ Audio: un solo sonido a la vez
  const Au = E.Audio = (function () {
    let cur = null;
    const subs = new Set();
    const emit = () => subs.forEach(f => { try { f(); } catch (e) { /* un boton que ya no esta */ } });
    function finish(c) {
      if (!c || c.done) return;
      c.done = true; clearTimeout(c.timer);
      try { c.stop(); } catch (e) { /* ya estaba parado */ }
      if (cur === c) { cur = null; emit(); }
    }
    function filePlayer(url, offset, vol) {
      const a = new Audio(); a.preload = "auto"; a.volume = vol == null ? 0.8 : clamp(vol, 0, 1);
      if (offset) a.addEventListener("loadedmetadata", () => {
        try { a.currentTime = offset < 0 ? Math.max(0, a.duration + offset) : Math.min(offset, Math.max(0, a.duration - 0.2)); } catch (e) { /* sin buscar */ }
      }, { once: true });
      a.src = url;
      return a;
    }
    const release = a => { try { a.pause(); a.removeAttribute("src"); a.load(); } catch (e) { /* nada */ } };
    /** Tu video desde el cursor (la voz, con o sin la mejora; con la musica si se pide). */
    async function playVideo(c, spec) {
      if (typeof V === "undefined" || !V || !V.src) { finish(c); return; }
      if (has("ensureAudio")) window.ensureAudio();
      const t0 = V.currentTime || 0, keep = { sfx: FX.sfxOn, mus: FX.musicOn, muted: V.muted };
      if (spec.at != null && isFinite(+spec.at)) { try { V.currentTime = Math.max(0, +spec.at); } catch (e) { /* sin buscar */ } }
      const nd = typeof ND !== "undefined" ? ND : null;
      FX.sfxOn = false;
      let side = null;
      if (spec.kind === "voice") {
        FX.musicOn = false;
        if (nd) { nd.wet.gain.value = spec.enhance ? 1 : 0; nd.dry.gain.value = spec.enhance ? 0 : 1; }
      } else if (spec.kind === "range") {
        // un tramo de tu video tal como suena (Limpieza: el ejemplo de un tipo de corte se oye aunque este quitado)
        FX.musicOn = false;
        if (FX.clean) FX.clean.through = spec.through == null ? null : spec.through;
      } else {
        // la musica creada (si esta al dia) la sincroniza el reproductor; si no, la muestra del ambiente
        const st = FX.musicState(), useFile = !!st.file && !st.stale && !st.busy;
        FX.musicOn = useFile;
        if (!useFile) { side = filePlayer(spec.sample, 0, spec.vol); side.play().catch(() => {}); }
      }
      V.muted = false;
      c.stop = () => {
        V.pause(); try { V.currentTime = t0; } catch (e) { /* nada */ }
        V.muted = keep.muted; FX.sfxOn = keep.sfx; FX.musicOn = keep.mus;
        if (spec.kind === "range" && FX.clean) FX.clean.through = null;
        if (has("applyEnhance")) window.applyEnhance();
        if (side) release(side);
      };
      const tStart = V.currentTime || 0;
      c.t = () => Math.max(0, (V.currentTime || 0) - tStart);
      if (typeof AC !== "undefined" && AC && AC.state === "suspended") AC.resume();
      await V.play();
      if (c.done) return;
      c.timer = setTimeout(() => finish(c), (spec.max || 5) * 1000);
    }
    /** spec = {key, kind: mood|track|file|sfx|voice|voice_music, id, url?, offset?, max? (s), vol?, enhance?, sample?} */
    async function play(spec) {
      finish(cur);
      const c = { key: spec.key || `${spec.kind}:${spec.id}`, kind: spec.kind, id: spec.id, done: false, stop() {}, t: () => 0 };
      cur = c; emit();
      try {
        if (spec.kind === "sfx") {
          const h = await FX.playSfx(spec.id, spec.gainDb, spec.pan, 0, spec.variant);
          if (!h) { finish(c); return; }
          if (c.done) { h.stop(); return; }
          c.stop = () => h.stop(); c.t = () => h.t();
          c.timer = setTimeout(() => finish(c), Math.min(spec.max || 6, (h.dur || 6) + 0.05) * 1000);
          return;
        }
        if (spec.kind === "voice" || spec.kind === "voice_music" || spec.kind === "range") { await playVideo(c, spec); return; }
        const a = filePlayer(spec.url, spec.offset, spec.vol);
        let start = null;
        a.addEventListener("playing", () => { if (start == null) start = a.currentTime; });
        a.addEventListener("ended", () => finish(c));
        a.addEventListener("error", () => finish(c));
        c.el = a; c.stop = () => release(a); c.t = () => (start == null ? 0 : a.currentTime - start);
        await a.play();
        if (c.done) { release(a); return; }
        c.timer = setTimeout(() => finish(c), (spec.max || 6) * 1000);
      } catch (e) { finish(c); }
    }
    return {
      play, stopAll: () => finish(cur),
      isOn: key => !!cur && cur.key === key,
      /** Gancho de pruebas: lo que suena -> {kind, id, key, t (s sonando)} o null. */
      info: () => (cur ? { kind: cur.kind, id: cur.id, key: cur.key, t: +(+cur.t() || 0).toFixed(3) } : null),
      on(f) { subs.add(f); return () => subs.delete(f); },
    };
  })();

  // ------------------------------------------------------------------ ListenButton
  const ListenButton = E.ListenButton = {
    /** o = {key, name (lo que suena, para el lector de pantalla), spec() -> spec de Audio.play o null,
        why() -> texto si ahora no se puede (queda apagado con ese titulo), label?, cls?} */
    create(o) {
      const b = document.createElement("button"); b.type = "button"; b.className = "listen" + (o.cls ? " " + o.cls : "");
      function sync() {
        const on = Au.isOn(o.key), why = !on && o.why ? o.why() : null;
        b.setAttribute("aria-pressed", String(on)); b.classList.toggle("on", on);
        b.disabled = !!why; b.title = why || "";
        b.setAttribute("aria-label", T(on ? "listen.stop.aria" : "listen.aria", { nombre: o.name || "" }));
        b.innerHTML = `${ICO(on ? "stop" : "play")}<span>${esc(on ? T("listen.stop") : o.label || T("listen.play"))}</span>`;
      }
      b.addEventListener("click", e => {
        e.preventDefault(); e.stopPropagation();
        if (Au.isOn(o.key)) { Au.stopAll(); return; }
        const s = o.spec && o.spec(); if (s) Au.play(Object.assign({ key: o.key }, s));
      });
      // se da de baja cuando ya estuvo en la pagina y salio (una fila reconstruida)
      const off = Au.on(() => { if (b.isConnected) b._seen = true; else if (b._seen) { off(); return; } sync(); });
      sync();
      return { el: b, update: sync };
    },
  };

  // ------------------------------------------------------------------ piezas comunes
  const music = () => (PLAN.fx && PLAN.fx.music) || {};
  /** fx.music para escribir (dentro de un gesto: el paso de deshacer ya esta abierto). */
  const mus = () => { PLAN.fx.music = PLAN.fx.music || { enabled: true, source: "ai" }; return PLAN.fx.music; };
  const srcOf = () => C.musicSourceOf(music());
  const dirStyle = () => (window.FX && FX.dirStyle ? FX.dirStyle() : "dinamico");
  const musVol = () => { const g = music().gain_db; return clamp(Math.pow(10, (g != null ? +g : -17) / 20) * 2.2, 0, 1); };
  const moodUrl = mood => C.moodSampleUrl(mood || music().mood || "auto", dirStyle());
  function mEdit(fn) {
    FX.edit(() => { PLAN.fx.music = PLAN.fx.music || { enabled: true, source: "ai" }; fn(PLAN.fx.music); }, { inspector: false });
    FX.applyMusicGain();
    E.refreshSoon(0);
  }
  /** Por que no se puede escuchar la musica creada ahora (null = si se puede). */
  function fileWhy() {
    const st = FX.musicState();
    if (!st.file || st.busy) return T("listen.need_music");
    if (st.stale) return T("listen.stale");
    return null;
  }
  const exBox = (el, cls) => { const d = document.createElement("div"); d.className = "ex-listen" + (cls ? " " + cls : ""); if (el) d.appendChild(el); return d; };
  /** Opcion de una lista con «Escuchar» al lado (radio + boton, hermanos: nunca un boton dentro de otro). */
  function optionList(o) {
    // o = {aria, items: [{v, label, help, listen?: ListenButton|null, wide?}], value, onPick, cls}
    const g = document.createElement("div"); g.className = "mopts" + (o.cls ? " " + o.cls : ""); g.setAttribute("role", "radiogroup"); g.setAttribute("aria-label", o.aria);
    o.items.forEach((it, i) => {
      const row = document.createElement("div"); row.className = "mopt" + (it.wide ? " wide" : ""); row.dataset.v = it.v == null ? "" : String(it.v);
      const pick = document.createElement("button"); pick.type = "button"; pick.className = "mopt-pick"; pick.setAttribute("role", "radio"); pick.dataset.i = String(i);
      pick.innerHTML = `<span class="mopt-n">${esc(it.label)}${CHK}</span>${it.help ? `<span class="mopt-h">${esc(it.help)}</span>` : ""}`;
      pick.onclick = () => o.onPick(it.v);
      row.appendChild(pick);
      if (it.listen) row.appendChild(it.listen.el);
      g.appendChild(row);
    });
    radioKeys(g);
    function sync(v) {
      $$(".mopt", g).forEach((row, i) => { const on = o.items[i].v === v; row.classList.toggle("on", on); $(".mopt-pick", row).setAttribute("aria-checked", String(on)); });
      roving(g);
    }
    sync(o.value);
    return { el: g, sync, listens: o.items.map(x => x.listen).filter(Boolean) };
  }
  /** Fila de Segmented con valores humanos (basic_choices de la copy) y, opcional, un ejemplo. */
  function choiceRow(id, get, set, o) {
    const cp = copy(id), op = o || {}, list = op.choices || cp.basic_choices || [];
    const seg = Segmented.create({ choices: list.map(([v, l]) => ({ v, label: l })), value: get(), nearest: true, onPick: set });
    // con 4 opciones o mas, las opciones van en su propio renglon (de ancho igual, sin cortar el texto)
    const r = HelpRow.create({ id, ctx: {}, control: seg.el, example: op.example || null, exKind: op.exKind, layout: "wide", cls: list.length > 3 ? "stack" : "" });
    r.sync = () => { seg.update({ value: get(), disabled: !!(op.off && op.off()) }); (op.listens || []).forEach(l => l.update()); };
    return r;
  }
  /** Barra con su valor en palabras (Avanzado). */
  function rangeCtl(o) {
    const line = document.createElement("div"); line.className = "adv-line st-range";
    const rg = document.createElement("input"); rg.type = "range"; rg.min = o.min; rg.max = o.max; rg.step = o.step; rg.setAttribute("aria-label", o.aria);
    const hv = document.createElement("span"); hv.className = "hv";
    line.append(rg, hv);
    const show = () => { const s = o.text(+rg.value); hv.textContent = s; rg.setAttribute("aria-valuetext", s); };
    const set = v => { if (document.activeElement !== rg) rg.value = String(v); show(); };
    rg.addEventListener("input", show);
    return { el: line, rg, set };
  }
  const humanNum = x => C.humanNum(x);

  // ------------------------------------------------------------------ Edición con IA
  const DIR_IDS = ["director.style", "director.intensity", "director.redirect", "director.variation", "director.music_sync", "director.notes", "director.seed"];
  const DP = { sig: "", plans: {} };
  /** El titulo que muestra cada tarjeta: el primer titulo del usuario (corto) o uno de ejemplo. */
  function sampleTitle() {
    const it = (PLAN.fx.text || []).find(x => x && x.enabled !== false && x.preset !== "counter" && C.titleText(x).trim());
    const txt = it ? C.titleText(it).trim() : "";
    const lines = txt.split("\n").map(s => s.trim()).filter(Boolean);
    if (lines.length && lines.length <= 2 && lines.every(l => l.length <= 18)) return { text: lines.join("\n"), accent: (it.accent_words || []).slice() };
    const d = T("dir.demo.title") || "";
    return { text: d, accent: d.split(/\s+/).slice(-1) };
  }
  function dirPlan(k) {
    const st = sampleTitle(), sig = JSON.stringify([C.projectAccent(PLAN), st.text, st.accent]);
    if (sig !== DP.sig) { DP.sig = sig; DP.plans = {}; }
    if (DP.plans[k]) return DP.plans[k];
    const S = (FX.schema && FX.schema()) || null, card = C.DIRECTOR_CARDS[k];
    const it = S ? C.newItem(S, "text", card.title, 0, 3600, PLAN.fx) : { id: "dt_" + k, preset: card.title };
    // arriba (los subtitulos van abajo): en la tarjeta se ven los dos sin encimarse
    Object.assign(it, { start: 0, end: 3, text: st.text, accent_words: st.accent, font: card.titleFont, enabled: true, y: 0.24 });
    delete it.lines;
    DP.plans[k] = { version: 2, duration: 3600, phrases: [], cuts: [], shots: [], director: { style: k }, entities: PLAN.entities || {},
      fx: { text: [it], graphics: [], cutaways: [], effects: [], transitions: [], sfx: [], media: [], captions: { enabled: false },
        grade: { enabled: false }, music: { enabled: false }, accent: C.projectAccent(PLAN) } };
    return DP.plans[k];
  }
  /** Tarjeta de un estilo de edicion: tu cuadro con el color y los subtitulos que DE VERDAD quedarian
      (los tuyos se conservan: FXCore.directorOutcome), su titulo y sus subtitulos en bucle. */
  function drawDirCard(cv, k, t, ts) {
    fitCanvas(cv);
    const W = cv.width, H = cv.height, c = cv.getContext("2d"), oc = C.directorOutcome(PLAN, k);
    c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, W, H);
    // tu color ajustado se queda: el del proyecto (undefined); si no, el del estilo
    c.drawImage(K.frameLayer(W, H, oc.gradeKept ? (oc.grade.enabled === false ? null : undefined) : oc.grade.look), 0, 0);
    const u = REDUCED() || Ticker.frozen() != null ? 1.4 : ((ts / 1000) % 3.4);
    FX.drawLiveTo({ ctx: c, W, H, plan: dirPlan(k), t: Math.min(u, 2.6) });
    if (oc.captions.enabled !== false && oc.captions.preset !== "none") K.drawCapsCfg(c, W, H, t, Object.assign({}, K.cap(), oc.captions, { enabled: true }), C.STYLE_FONTS[k]);
  }
  function styleGallery() {
    const opts = (UI().options || {})["director.style"] || {};
    const wrap = document.createElement("div"); wrap.className = "dg-wrap";
    wrap.innerHTML = `<div class="gal dgal" role="radiogroup" aria-label="${esc(T("dir.aria"))}" style="--cols:2">`
      + C.DIRECTOR_ORDER.filter(k => opts[k]).map(k => `<button type="button" class="gcard dcard dircard" role="radio" aria-checked="false" data-k="${k}" data-v="${k}">`
        + `<span class="dshot"><canvas aria-hidden="true"></canvas></span>`
        + `<span class="gmeta"><span class="gname">${esc(opts[k].label)}</span><span class="gdesc">${esc(opts[k].help || "")}</span>`
        + `<span class="gchosen">${CHK}${esc(T("gallery.chosen"))}</span></span></button>`).join("")
      + `</div><details class="dqc"><summary></summary><p></p></details>`;
    const gal = $(".dgal", wrap), cards = $$(".dcard", wrap), qc = $(".dqc", wrap);
    let hov = null;
    // «Qué cambia» dice tambien lo que se queda (tus subtítulos y tu color, si los cambiaste)
    function setQc() {
      const k = hov || dirStyle(), o = opts[k] || {};
      $("summary", qc).textContent = T("dir.que_cambia", { estilo: o.label || "" });
      $("p", qc).textContent = C.directorQueCambia(o, C.directorOutcome(PLAN, k), { preset: K.presetName, look: K.lookName }, T);
    }
    cards.forEach(b => {
      const k = b.dataset.k, cv = $("canvas", b);
      b.onclick = () => { if (k !== dirStyle() && !FX.busy()) FX.applyDirector({ style: k }); };
      b.addEventListener("mouseenter", () => { hov = k; setQc(); });
      b.addEventListener("mouseleave", () => { hov = null; setQc(); });
      b.addEventListener("focus", () => { hov = k; setQc(); });
      b.addEventListener("blur", () => { hov = null; setQc(); });
      Ticker.add({ el: cv, name: "director", fps: 30, draw: (t, ts) => drawDirCard(cv, k, t, ts) });
    });
    radioKeys(gal);
    const sync = () => {
      const v = dirStyle(), busy = FX.busy();
      cards.forEach(b => { const on = b.dataset.k === v; b.setAttribute("aria-checked", String(on)); b.classList.toggle("on", on); b.disabled = busy; });
      roving(gal); setQc(); Ticker.kick();
    };
    sync();
    return { el: wrap, sync };
  }
  function intensityRow() {
    const id = "director.intensity", cp = copy(id);
    const ctl = document.createElement("div"); ctl.className = "num-ctl";
    const pickI = v => { if (!FX.busy() && Math.abs(v - FX.dirIntensity()) > 1e-6) FX.applyDirector({ intensity: v }); };
    const seg = Segmented.create({ choices: (cp.basic_choices || []).map(([v, l]) => ({ v, label: l })), value: FX.dirIntensity(), nearest: true, onPick: pickI });
    ctl.appendChild(seg.el);
    let rc = null;
    if (E.level() === "advanced" && cp.adv) {
      rc = rangeCtl({ min: 0, max: 100, step: cp.adv.step || 5, aria: cp.label, text: v => T("dir.pct", { v }) });
      rc.rg.addEventListener("change", () => pickI(+rc.rg.value / 100));
      ctl.appendChild(rc.el);
    }
    const ex = document.createElement("div"); ex.className = "ex-text ex-texto";
    const r = HelpRow.create({ id, ctx: {}, control: ctl, example: ex, layout: "wide" });
    r.sync = () => {
      const busy = FX.busy();
      seg.update({ value: FX.dirIntensity(), disabled: busy });
      if (rc) { rc.rg.disabled = busy; rc.set(Math.round(FX.dirIntensity() * 100)); }
      r.counts();
    };
    r.counts = () => {
      const lvl = (C.nearestChoice(cp.basic_choices || [], FX.dirIntensity()) || [0, ""])[1];
      ex.textContent = C.directorExample(cp.ex_tpl, Object.assign({ nivel: lvl, dur: C.fmtClock(FX.finalDuration()) }, C.directorCounts(PLAN)), cp.ex_ui);
    };
    r.sync();
    return r;
  }
  function actionRow(id, btnId, icon, label, onClick) {
    const b = document.createElement("button"); b.type = "button"; b.className = "btn ghost mini"; b.id = btnId;
    const r = HelpRow.create({ id, ctx: {}, control: b, layout: "wide" });
    r.sync = () => { const busy = FX.busy(); b.disabled = busy; b.innerHTML = `${busy ? '<span class="spinner"></span>' : ICO(icon)}${esc(label)}`; };
    b.onclick = onClick;
    r.sync();
    return r;
  }
  function musicSyncRow() {
    const box = document.createElement("div"); box.id = "dirMusic"; box.className = "dirmusic";
    const r = HelpRow.create({ id: "director.music_sync", ctx: {}, control: box, layout: "wide", full: true });
    const vis = () => { r.el.hidden = !box.childElementCount; };
    new MutationObserver(vis).observe(box, { childList: true });
    r.sync = vis; vis();
    return r;
  }
  function notesRow() {
    const box = document.createElement("div"); box.className = "dnotes";
    const r = HelpRow.create({ id: "director.notes", ctx: {}, control: box, layout: "wide", full: true });
    // las notas del motor con terminos tecnicos (p. ej. ritmo en pulsos y tiempos de la musica) van
    // con las tecnicas, plegadas: a la vista solo lo que se entiende sin jerga
    const deny = (UI().deny || []).map(w => new RegExp("(?<![\\wÁÉÍÓÚáéíóúñÑ])" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?![\\wÁÉÍÓÚáéíóúñÑ])", "i"));
    const jargon = t => deny.some(rx => rx.test(t));
    r.sync = () => {
      const n0 = FX.directorNotes(), n = { fonts: n0.fonts, notes: n0.notes.filter(x => !jargon(x)), tech: n0.notes.filter(jargon).concat(n0.tech) };
      box.innerHTML = (n.fonts.length ? `<p>${ICO("type")}<span>${esc(T("dir.fonts", { lista: n.fonts.join(", ") }))}</span></p>` : "")
        + n.notes.map(x => `<p>${ICO("insp")}<span>${esc(x)}</span></p>`).join("")
        + (n.tech.length ? `<details class="dirtech"><summary>${esc(T("dir.tech", { n: n.tech.length }))}</summary><div class="dt-body"></div></details>` : "")
        + (!n.fonts.length && !n.notes.length && !n.tech.length ? `<p class="dn-none">${esc(T("dir.notes.none"))}</p>` : "");
      // las notas tecnicas existen solo mientras estan abiertas (plegadas no hay nada que leer)
      const d = $(".dirtech", box);
      if (d) d.addEventListener("toggle", () => { $(".dt-body", d).innerHTML = d.open ? n.tech.map(x => `<div>${esc(x)}</div>`).join("") : ""; });
    };
    r.sync();
    return r;
  }
  function DirectorSection(body) {
    const S = { rows: {}, sig: "" };
    function build() {
      const lv = E.level(), ids = C.visibleRows(UI(), DIR_IDS, lv, {});
      S.sig = JSON.stringify([ids, lv]);
      body.innerHTML = ""; S.rows = {};
      ids.forEach(id => {
        let r = null;
        if (id === "director.style") {
          const g = styleGallery();
          r = HelpRow.create({ id, ctx: {}, control: g.el, layout: "wide", full: true, exKind: "ninguno" });
          r.sync = g.sync;
        } else if (id === "director.intensity") r = intensityRow();
        else if (id === "director.redirect") r = actionRow(id, "dirFull", "spark", T("dir.redirect.btn"), () => FX.redirect(true));
        else if (id === "director.variation") r = actionRow(id, "dirVar", "dice", T("dir.variation.btn"), () => {
          const s0 = PLAN.director && PLAN.director.seed != null ? +PLAN.director.seed : 7;
          FX.redirect(false, s0 + 1 + Math.floor(Math.random() * 97));
        });
        else if (id === "director.music_sync") r = musicSyncRow();
        else if (id === "director.notes") r = notesRow();
        if (r) { S.rows[id] = r; body.appendChild(r.el); }
      });
      K.ensureLookSet();
      FX.renderMusicSide();
    }
    function update() {
      const lv = E.level(), ids = C.visibleRows(UI(), DIR_IDS, lv, {});
      if (JSON.stringify([ids, lv]) !== S.sig) { build(); return; }
      Object.values(S.rows).forEach(r => r.sync && r.sync());
      K.ensureLookSet();
    }
    build();
    return { update, counts: () => { const r = S.rows["director.intensity"]; if (r) r.counts(); } };
  }
  E.directorChanged = () => { const v = E._extra && E._extra.stDirector; if (v) v.update(); };
  E.directorCounts = () => { const v = E._extra && E._extra.stDirector; if (v) v.counts(); };
  E.addSection({
    id: "stDirector",
    build(b) { const s = DirectorSection(b); (E._extra = E._extra || {}).stDirector = s; return s; },
    summary() {
      const o = (UI().options || {})["director.style"] || {}, cp = copy("director.intensity");
      const lvl = (C.nearestChoice(cp.basic_choices || [], FX.dirIntensity()) || [0, ""])[1];
      // si la musica IA esta lista pero la edicion aun no sigue su ritmo, el resumen lo dice (el boton vive en Estilo)
      const sync = FX.needsResync && FX.needsResync() ? T("summary.director.sync") : "";
      return { k: T("summary.director"), v: [(o[dirStyle()] || {}).label, lvl, sync].filter(Boolean).join(" · "), aria: T("summary.change.director") };
    },
  });

  // ------------------------------------------------------------------ Música
  const MUS_IDS = ["music.source", "music.track", "music.mood", "music.gain_db", "music.preview", "music.prompt", "music.bpm", "music.keyscale",
    "music.ending", "music.intro_fade", "music.fade_out", "music.duck_db", "music.voice_carve_db", "music.seed", "music.enabled", "music.bpm_lock"];
  // lo que usa cada origen (music.get_music): «De la biblioteca» toma ritmo y tono de la canción; el
  // texto para la IA solo con «Creada con IA»; «Tu canción» no se ofrece (no hay donde subirla aún)
  const MUS_FOR = {
    none: ["music.source"],
    ai: ["music.source", "music.mood", "music.gain_db", "music.preview", "music.prompt", "music.bpm", "music.keyscale", "music.ending", "music.intro_fade", "music.fade_out", "music.duck_db", "music.voice_carve_db", "music.seed"],
    procedural: ["music.source", "music.mood", "music.gain_db", "music.preview", "music.bpm", "music.keyscale", "music.ending", "music.intro_fade", "music.fade_out", "music.duck_db", "music.voice_carve_db", "music.seed"],
    library: ["music.source", "music.track", "music.gain_db", "music.preview", "music.ending", "music.intro_fade", "music.fade_out", "music.duck_db", "music.voice_carve_db"],
  };
  const SOURCES = ["ai", "procedural", "library", "none"];
  const libTracks = () => (typeof TRACKS !== "undefined" && Array.isArray(TRACKS) ? TRACKS : []);
  /** Por que no se puede escuchar la musica CON IA ahora (null = si: es la de este video y esta al dia). */
  const aiWhy = () => (srcOf() !== "ai" ? T("listen.ai.other") : fileWhy());
  function sourceListen(v) {
    if (v === "none") return null;
    const name = T("listen.source." + v);
    if (v === "library") return ListenButton.create({ key: "src:library", name, why: () => (libTracks().length ? null : T("mus.lib.loading")),
      spec: () => { const tr = music().track || (libTracks()[0] || {}).id; return tr ? { kind: "track", id: tr, url: `/music/${encodeURIComponent(tr)}` } : null; } });
    // «Creada con IA» suena solo cuando ya existe (antes tocaba la misma muestra rapida que «Rápida
    // (sin IA)»: dos opciones distintas sonaban igual); «Rápida (sin IA)», la muestra del ambiente
    if (v === "ai") return ListenButton.create({ key: "src:ai", name: T("listen.music.all"), why: aiWhy, spec: () => ({ kind: "file", id: "ai", url: FX.musicUrl(), max: 6 }) });
    return ListenButton.create({ key: "src:" + v, name, spec: () => ({ kind: "mood", id: music().mood || "auto", url: moodUrl() }) });
  }
  function sourceRow() {
    const opts = (UI().options || {})["music.source"] || {};
    const list = optionList({ aria: T("mus.source.aria"), cls: "two", value: srcOf(),
      items: SOURCES.filter(v => opts[v]).map(v => ({ v, label: opts[v].label, help: opts[v].help, listen: sourceListen(v) })),
      // «De la biblioteca» sin canción elegida: la primera queda elegida (la que se ve marcada es la que suena)
      onPick: v => { if (v !== srcOf()) mEdit(m => { if (v === "none") m.enabled = false; else { m.enabled = true; m.source = v; if (v === "library" && !m.track && libTracks()[0]) m.track = libTracks()[0].id; } }); } });
    // junto a la lista: por que «Creada con IA» todavia no suena (y que «Rápida» es una muestra)
    const note = document.createElement("p"); note.className = "hrow-rule src-note"; note.textContent = T("mus.source.note");
    const wrap = document.createElement("div"); wrap.className = "mood-wrap"; wrap.append(list.el, note);
    const r = HelpRow.create({ id: "music.source", ctx: {}, control: wrap, layout: "wide", full: true, exKind: "ninguno" });
    r.sync = () => { list.sync(srcOf()); list.listens.forEach(l => l.update()); note.hidden = !aiWhy(); };
    return r;
  }
  function moodRow() {
    const opts = (UI().options || {})["music.mood"] || {};
    const order = ["auto", "upbeat", "chill", "inspiring", "corporate", "playful", "dramatic"].filter(v => opts[v]);
    const list = optionList({ aria: T("mus.mood.aria"), cls: "two", value: music().mood || "auto",
      items: order.map(v => ({ v, label: opts[v].label, help: opts[v].help, wide: v === "auto",
        listen: ListenButton.create({ key: "mood:" + v, name: opts[v].label, spec: () => ({ kind: "mood", id: v, url: moodUrl(v) }) }) })),
      onPick: v => { if (v !== (music().mood || "auto")) mEdit(m => { m.mood = v === "auto" ? null : v; }); } });
    const note = document.createElement("p"); note.className = "hrow-rule mood-note"; note.textContent = T("mus.mood.note");
    const wrap = document.createElement("div"); wrap.className = "mood-wrap"; wrap.append(list.el, note);
    const r = HelpRow.create({ id: "music.mood", ctx: {}, control: wrap, layout: "wide", full: true, exKind: "ninguno" });
    r.sync = () => { list.sync(music().mood || "auto"); list.listens.forEach(l => l.update()); note.hidden = srcOf() !== "ai"; };
    r.sync();
    return r;
  }
  function trackRow() {
    const wrap = document.createElement("div"); wrap.className = "track-wrap";
    let list = null, sig = "";
    const r = HelpRow.create({ id: "music.track", ctx: {}, control: wrap, layout: "wide", full: true, exKind: "ninguno" });
    r.sync = () => {
      const ts = libTracks(), s = ts.map(t => t.id).join(",");
      if (!ts.length) {
        wrap.innerHTML = `<p class="hrow-rule">${esc(T("mus.lib.loading"))}</p>`; sig = "";
        if (has("loadTracks") && !r.loading) { r.loading = true; window.loadTracks().then(() => { r.loading = false; if (libTracks().length) r.sync(); else wrap.innerHTML = `<p class="hrow-rule">${esc(T("mus.lib.empty"))}</p>`; }); }
        return;
      }
      if (s !== sig) {
        sig = s;
        list = optionList({ aria: T("mus.track.aria"), value: music().track || null,
          items: ts.map(t => ({ v: t.id, label: t.title || t.id, help: t.desc || "",
            listen: ListenButton.create({ key: "track:" + t.id, name: t.title || t.id, spec: () => ({ kind: "track", id: t.id, url: `/music/${encodeURIComponent(t.id)}` }) }) })),
          onPick: v => { if (v !== music().track) mEdit(m => { m.track = v; }); } });
        wrap.innerHTML = ""; wrap.appendChild(list.el);
      }
      list.sync(music().track || null); list.listens.forEach(l => l.update());
    };
    r.sync();
    return r;
  }
  function gainRow() {
    const id = "music.gain_db", cp = copy(id);
    const get = () => (music().gain_db != null ? +music().gain_db : -17);
    const ctl = document.createElement("div"); ctl.className = "num-ctl";
    const seg = Segmented.create({ choices: (cp.basic_choices || []).map(([v, l]) => ({ v, label: l })), value: get(), nearest: true, onPick: v => mEdit(m => { m.gain_db = v; }) });
    ctl.appendChild(seg.el);
    let rc = null;
    if (E.level() === "advanced") {
      const p = (((FX.schema && FX.schema()) || {}).music || { params: [] }).params.find(x => x.k === "gain_db") || { min: -40, max: 0, step: 0.5 };
      rc = rangeCtl({ min: p.min, max: p.max, step: p.step || 0.5, aria: cp.label, text: v => T("mus.gain.v", { v: (v < 0 ? "−" : "") + humanNum(Math.abs(v)) }) });
      FX.gesture(rc.rg, () => { mus().gain_db = +rc.rg.value; FX.applyMusicGain(); seg.update({ value: get() }); }, d => { if (d) E.refreshSoon(0); });
      ctl.appendChild(rc.el);
    }
    const ls = ListenButton.create({ key: "voice_music", name: T("listen.voice_music"), why: () => (typeof V !== "undefined" && V && V.src ? null : T("listen.need_music")),
      spec: () => ({ kind: "voice_music", id: "gain", sample: moodUrl(), vol: musVol(), max: 5 }) });
    const r = HelpRow.create({ id, ctx: {}, control: ctl, example: exBox(ls.el), exKind: "audio", layout: "wide" });
    r.sync = () => { seg.update({ value: get() }); if (rc) rc.set(get()); ls.update(); };
    r.sync();
    return r;
  }
  function previewRow() {
    const ctl = document.createElement("div"); ctl.className = "mus-prev";
    const st = document.createElement("p"); st.className = "mus-st"; st.setAttribute("role", "status");
    const b = document.createElement("button"); b.type = "button"; b.className = "btn ghost mini"; b.id = "musGen";
    ctl.append(st, b);
    const ls = ListenButton.create({ key: "file:all", name: T("listen.music.all"), why: fileWhy, spec: () => ({ kind: "file", id: "all", url: FX.musicUrl(), max: 120 }) });
    const r = HelpRow.create({ id: "music.preview", ctx: {}, control: ctl, example: exBox(ls.el), exKind: "audio", layout: "wide", cls: "stack" });
    b.onclick = () => { Au.stopAll(); FX.genMusic(false); };
    r.sync = () => {
      const s = FX.musicState();
      // el ritmo (pulsos por minuto) y el golpe principal son de Avanzado (ahi estan sus controles)
      st.innerHTML = s.busy ? `<span class="spinner"></span>${esc(T("mus.busy"))}` : esc(!s.file ? T("mus.none") : s.stale ? T("mus.stale")
        : C.musicReadyText(s, E.level(), T));
      b.style.display = s.file && !s.stale && !s.busy ? "none" : ""; b.disabled = s.busy;
      b.innerHTML = `${ICO(s.file ? "undo" : "music")}${esc(s.file ? T("mus.update") : T("mus.gen"))}`;
      ls.update();
    };
    r.sync();
    return r;
  }
  function promptRow() {
    const id = "music.prompt", cp = copy(id);
    const inp = document.createElement("input"); inp.type = "text"; inp.className = "ftext mus-prompt"; inp.placeholder = T("mus.prompt.ph"); inp.maxLength = 160;
    // el texto del usuario se guarda en el plan (plan.studio.musicPrompt): el campo solo muestra el
    // texto de la musica si es el suyo (el del director, en ingles, nunca); re-dirigir lo conserva (server)
    FX.gesture(inp, () => { C.setMusicPrompt(PLAN, inp.value); }, d => { if (d) E.refreshSoon(0); });
    const ex = document.createElement("div"); ex.className = "ex-text ex-texto mus-ex";
    ex.append(document.createTextNode(T("mus.prompt.ex") + " "));
    ["mus.prompt.ex1", "mus.prompt.ex2", "mus.prompt.ex3"].forEach((k, i) => {
      if (i) ex.append(document.createTextNode(" · "));
      const b = document.createElement("button"); b.type = "button"; b.className = "linkbtn"; b.textContent = T(k);
      b.setAttribute("aria-label", T("mus.prompt.use", { texto: T(k) }));
      b.onclick = () => mEdit(() => { C.setMusicPrompt(PLAN, T(k)); });
      ex.append(b);
    });
    const r = HelpRow.create({ id, ctx: {}, control: inp, example: ex, exKind: "texto", layout: "wide" });
    r.sync = () => { if (document.activeElement !== inp) inp.value = C.musicPromptShown(PLAN); };
    r.sync();
    return r;
  }
  function keyRow() {
    const id = "music.keyscale", cp = copy(id);
    const sel = document.createElement("select"); sel.className = "fsel";
    sel.innerHTML = `<option value="">${esc(T("mus.key.auto"))}</option>` + C.KEYSCALES.map(k => `<option value="${esc(k)}">${esc(C.keyscaleEs(k))}</option>`).join("");
    sel.onchange = () => mEdit(m => { m.keyscale = sel.value || null; });
    const ex = document.createElement("div"); ex.className = "ex-text ex-texto";
    const r = HelpRow.create({ id, ctx: {}, control: sel, example: ex, exKind: "texto", layout: "wide" });
    r.sync = () => {
      const k = music().keyscale || "", es = C.keyscaleEs(k);
      if (document.activeElement !== sel) sel.value = C.KEYSCALES.includes(k) ? k : "";
      ex.textContent = es ? T("mus.key.ex", { tono: es }) : cp.ex_ui;
    };
    r.sync();
    return r;
  }
  function fadeOutRow() {
    const id = "music.fade_out", cp = copy(id);
    const get = () => (music().fade_out != null ? +music().fade_out : 1.5);
    const rc = rangeCtl({ min: 0.6, max: 3, step: 0.05, aria: cp.label, text: v => T("mus.fade_out.v", { v: humanNum(v) }) });
    FX.gesture(rc.rg, () => { mus().fade_out = Math.round(+rc.rg.value * 1000) / 1000; }, d => { if (d) E.refreshSoon(0); });
    const ls = ListenButton.create({ key: "file:end", name: T("listen.music.end"), why: fileWhy, spec: () => ({ kind: "file", id: "end", url: FX.musicUrl(), offset: -3.2, max: 3.4 }) });
    const r = HelpRow.create({ id, ctx: {}, control: rc.el, example: exBox(ls.el), exKind: "audio", layout: "wide" });
    r.sync = () => { rc.set(get()); ls.update(); };
    r.sync();
    return r;
  }
  function seedRow() {
    const b = document.createElement("button"); b.type = "button"; b.className = "btn ghost mini"; b.id = "musSeed";
    const r = HelpRow.create({ id: "music.seed", ctx: {}, control: b, layout: "wide" });
    b.onclick = () => { Au.stopAll(); FX.genMusic(true); };
    r.sync = () => { const busy = FX.music.busy; b.disabled = busy; b.innerHTML = `${busy ? '<span class="spinner"></span>' : ICO("dice")}${esc(T("mus.seed.btn"))}`; };
    r.sync();
    return r;
  }
  const fileListen = (part, offset, max) => ListenButton.create({ key: "file:" + part, name: T("listen.music." + (part === "end" ? "end" : "start")), why: fileWhy,
    spec: () => ({ kind: "file", id: part, url: FX.musicUrl(), offset, max }) });
  const vmListen = key => ListenButton.create({ key: "voice_music:" + key, name: T("listen.voice_music"), why: () => (typeof V !== "undefined" && V && V.src ? null : T("listen.need_music")),
    spec: () => ({ kind: "voice_music", id: key, sample: moodUrl(), vol: musVol(), max: 5 }) });
  function MusicSection(body) {
    const S = { rows: {}, sig: "" };
    const ids = () => { const allow = MUS_FOR[srcOf()] || MUS_FOR.ai; return C.visibleRows(UI(), MUS_IDS, E.level(), {}).filter(i => allow.includes(i)); };
    function build() {
      const list = ids();
      S.sig = JSON.stringify([list, E.level()]);
      body.innerHTML = ""; S.rows = {};
      list.forEach(id => {
        const k = id.split(".")[1];
        let r = null;
        if (id === "music.source") r = sourceRow();
        else if (id === "music.mood") r = moodRow();
        else if (id === "music.track") r = trackRow();
        else if (id === "music.gain_db") r = gainRow();
        else if (id === "music.preview") r = previewRow();
        else if (id === "music.prompt") r = promptRow();
        else if (id === "music.keyscale") r = keyRow();
        else if (id === "music.fade_out") r = fadeOutRow();
        else if (id === "music.seed") r = seedRow();
        else if (id === "music.bpm") {
          const ls = fileListen("start", 0, 6);
          r = choiceRow(id, () => C.musicBpmOf(music()), v => mEdit(m => Object.assign(m, C.musicBpmSet(v))), { example: exBox(ls.el), exKind: "audio", listens: [ls] });
        } else if (id === "music.ending") {
          const ls = fileListen("end", -3.2, 3.4), o = (UI().options || {})["music.ending"] || {};
          r = choiceRow(id, () => music().ending || "auto", v => mEdit(m => { m.ending = v; }),
            { choices: ["auto", "natural", "hit", "fade"].filter(v => o[v]).map(v => [v, o[v].label]), example: exBox(ls.el), exKind: "audio", listens: [ls] });
        } else if (id === "music.intro_fade") {
          const ls = fileListen("start", 0, 4);
          r = choiceRow(id, () => +(music().intro_fade || 0), v => mEdit(m => { m.intro_fade = v; }), { example: exBox(ls.el), exKind: "audio", listens: [ls] });
        } else if (id === "music.duck_db" || id === "music.voice_carve_db") {
          const ls = vmListen(k), dflt = k === "duck_db" ? -9 : -3;
          r = choiceRow(id, () => (music()[k] != null ? +music()[k] : dflt), v => mEdit(m => { m[k] = v; }), { example: exBox(ls.el), exKind: "audio", listens: [ls] });
        }
        if (r) { if (r.sync) r.sync(); S.rows[id] = r; body.appendChild(r.el); }
      });
      if (srcOf() === "none") body.insertAdjacentHTML("beforeend", `<p class="hrow-rule">${esc(T("mus.off"))}</p>`);
    }
    function update() {
      if (JSON.stringify([ids(), E.level()]) !== S.sig) { build(); return; }
      Object.values(S.rows).forEach(r => r.sync && r.sync());
    }
    build();
    return { update };
  }
  E.musicChanged = () => { const v = E._extra && E._extra.stMusic; if (v) v.update(); };
  E.addSection({
    id: "stMusic",
    build(b) { const s = MusicSection(b); (E._extra = E._extra || {}).stMusic = s; return s; },
    summary() {
      const s = srcOf(), m = music();
      const extra = s === "library" ? ((libTracks().find(t => t.id === m.track) || {}).title || "") : s === "none" ? "" : optLabel("music.mood", m.mood || "auto");
      return { k: T("summary.music"), v: [optLabel("music.source", s), extra].filter(Boolean).join(" · "), aria: T("summary.change.music") };
    },
  });

  // ------------------------------------------------------------------ Quién sale y tu logo
  function profileRow() {
    const box = document.createElement("div"); box.id = "profBody"; box.className = "prof-body";
    const cv = document.createElement("canvas"); cv.className = "who-ex"; cv.setAttribute("aria-hidden", "true");
    Ticker.add({ el: cv, name: "who", fps: 30, draw: (t, ts) => K.drawBrandMini(cv, "card", ts, "who.card") });
    const r = HelpRow.create({ id: "setup.profile", ctx: {}, control: box, example: cv, exKind: "vivo", layout: "wide", cls: "stack" });
    r.sync = () => { if (window.PF && (!box.childElementCount || !box.contains(document.activeElement))) PF.renderEditorGroup(); };
    r.sync();
    return r;
  }
  function useRow() {
    const wrap = document.createElement("div"); wrap.className = "pu-list"; wrap.setAttribute("role", "group"); wrap.setAttribute("aria-label", T("who.use.aria"));
    C.PROFILE_USE.forEach(([k, cid]) => {
      const cp = copy(cid), lb = document.createElement("label"); lb.className = "pu-item"; lb.title = cp.help;
      lb.innerHTML = `<input type="checkbox" class="toggle" data-u="${k}"><span class="pu-t"><b>${esc(cp.label)}</b><span>${esc(cp.help)}</span></span>`;
      wrap.appendChild(lb);
    });
    const cv = document.createElement("canvas"); cv.className = "who-ex"; cv.setAttribute("aria-hidden", "true");
    Ticker.add({ el: cv, name: "who", fps: 30, draw: (t, ts) => K.drawBrandMini(cv, "btn", ts, "who.btn") });
    const r = HelpRow.create({ id: "profile.use", ctx: {}, control: wrap, example: cv, exKind: "vivo", layout: "wide", cls: "stack" });
    let busy = false;
    $$("input", wrap).forEach(i => {
      i.onchange = async () => {
        if (busy) return;
        busy = true; r.sync();
        const ok = window.PF ? await PF.setUse(i.dataset.u, i.checked) : false;
        busy = false;
        if (!ok) i.checked = !i.checked;
        r.sync();
      };
    });
    let note = null;
    r.sync = () => {
      const p = PLAN.profile || null, use = C.profileUseOf(p), off = !p || busy || FX.busy();
      $$("input", wrap).forEach(i => { i.checked = !!use[i.dataset.u]; i.disabled = off; });
      const want = !p ? T("who.use.need") : null;
      if (want !== note) { note = want; r.update({ note: want || null }); }
    };
    r.sync();
    return r;
  }
  const hasLogo = () => typeof logoImg !== "undefined" && !!logoImg;
  /** El logo sobre tu cuadro, a lo ancho del ejemplo y recortado a la mitad de la esquina elegida
      (arriba o abajo): se lee DONDE va y DE QUE TAMANO sale (apagado o sin logo: tenue). */
  function drawLogoEx(cv) {
    fitCanvas(cv);
    const pw = cv.width, ph = cv.height, c = cv.getContext("2d");
    c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, pw, ph);
    const a = K.fmtAspect(), W = pw, H = Math.round(W / a);
    const on = typeof LOGO_ON !== "undefined" && LOGO_ON && hasLogo(), img = hasLogo() ? logoImg : null;
    const pos = typeof LOGO_POS !== "undefined" ? LOGO_POS : "br", size = typeof LOGO_SIZE !== "undefined" ? LOGO_SIZE : 0.14;
    // la mitad del cuadro (del alto del ejemplo) donde va la esquina: arriba o abajo
    const sy = H <= ph ? (H - ph) / 2 : pos[0] === "t" ? 0 : H - ph;
    c.drawImage(K.frameLayer(W, H), 0, -sy);
    const rr = C.logoRect(pos, size, W, H, img ? img.width : 1, img ? img.height : 1);
    c.globalAlpha = on ? 0.9 : 0.35;
    if (img) c.drawImage(img, rr.x, rr.y - sy, rr.w, rr.h);
    else { c.fillStyle = "#d7dce6"; c.beginPath(); if (c.roundRect) c.roundRect(rr.x, rr.y - sy, rr.w, rr.h, Math.max(2, rr.w * 0.18)); else c.rect(rr.x, rr.y - sy, rr.w, rr.h); c.fill(); }
    c.globalAlpha = 1;
    // la orilla del video (se ve la esquina de verdad: arriba o abajo, izquierda o derecha)
    const lw = Math.max(1, Math.round(ph / 40));
    c.strokeStyle = "rgba(255,255,255,.6)"; c.lineWidth = lw; c.strokeRect(lw / 2, lw / 2 - sy, W - lw, H - lw);
  }
  /** Sin logo subido, «Mostrar el logo», la esquina y el tamaño no harian nada: se apagan y la nota
      pide subirlo (el boton «Subir tu logo» queda a la mano). */
  function syncLogo() {
    const row = $("#brandLogoRow"); if (!row) return;
    const has = hasLogo(), gated = row.classList.contains("proff");
    const lo = $("#logoOn"), need = $("#logoNeed");
    if (!gated) {
      [lo, $("#logoSize")].concat($$("#segLogo button")).forEach(x => { if (x) x.disabled = !has; });
      if (lo) lo.checked = has && typeof LOGO_ON !== "undefined" && !!LOGO_ON;
    }
    if (need) need.hidden = has || gated;
    Ticker.kick();
  }
  E.syncLogo = syncLogo;
  function wireLogo() {
    const row = $("#brandLogoRow"); if (!row || row.dataset.wired) return;
    row.dataset.wired = "1";
    const cv = $(".logo-ex", row), hv = $("#logoSizeLbl"), rg = $("#logoSize");
    if (cv) Ticker.add({ el: cv, name: "logo", fps: 4, eager: true, draw: () => drawLogoEx(cv) });
    const setHv = () => { if (hv && rg) { const s = T("who.logo.size", { v: rg.value }); hv.textContent = s; rg.setAttribute("aria-valuetext", s); } };
    if (rg) rg.addEventListener("input", () => { setHv(); Ticker.kick(); });
    ["#logoOn", "#segLogo"].forEach(q => { const e = $(q); if (e) e.addEventListener(q === "#logoOn" ? "change" : "click", () => setTimeout(() => Ticker.kick(), 0)); });
    radioKeys($("#segLogo"));
    setHv(); syncLogo();
  }
  function WhoSection(body) {
    const S = { rows: {} };
    function build() {
      body.innerHTML = ""; S.rows = {};
      [["setup.profile", profileRow], ["profile.use", useRow]].forEach(([id, f]) => { const r = f(); S.rows[id] = r; body.appendChild(r.el); });
      E.placeStatic(body, ["brandLogoRow", "presetRow"]);
      wireLogo();
    }
    function update() {
      Object.values(S.rows).forEach(r => r.sync && r.sync());
      E.placeStatic(body, ["brandLogoRow", "presetRow"]);
      const rg = $("#logoSize"), hv = $("#logoSizeLbl");
      if (rg && hv) hv.textContent = T("who.logo.size", { v: rg.value });
      syncLogo();
    }
    build();
    return { update };
  }
  E.addSection({
    id: "stWho",
    build(b) { return WhoSection(b); },
    summary() {
      const p = PLAN.profile || null;
      return { k: T("summary.who"), v: (p && (p.name || p.business)) || T("summary.who.none"), aria: T("summary.change.who") };
    },
  });

  // ------------------------------------------------------------------ Formato, idioma y audio
  /** Tu cuadro en la forma del formato elegido, centrado en el ejemplo. */
  function drawFmtEx(cv) {
    fitCanvas(cv);
    const pw = cv.width, ph = cv.height, c = cv.getContext("2d"), a = K.fmtAspect(), pad = Math.round(ph * 0.08);
    c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, pw, ph);
    let H = ph - 2 * pad, W = Math.round(H * a);
    if (W > pw - 2 * pad) { W = pw - 2 * pad; H = Math.round(W / a); }
    const x = Math.round((pw - W) / 2), y = Math.round((ph - H) / 2);
    c.drawImage(K.frameLayer(W, H), x, y);
    c.strokeStyle = "rgba(255,255,255,.55)"; c.lineWidth = Math.max(1, ph / 64); c.strokeRect(x + 0.5, y + 0.5, W - 1, H - 1);
  }
  /** Antes y despues del margen desenfocado: el video a sangre y con el margen que DE VERDAD sale
      (FX.airApplied: 0 mientras no se aplica). Si tu video ya llena el cuadro, el «despues» va
      apagado (la nota dice por que). Esquema simetrico. */
  function airSvg(pct, dim) {
    const m = clamp(+pct || 0, 0, 30) / 100, ph = 46, pw = 26, gap = 18, x1 = (128 - (2 * pw + gap)) / 2, x2 = x1 + pw + gap, y = 9;
    const vw = pw * (1 - 2 * m), vh = vw * 9 / 16;
    const phone = (x, inner, op) => `<g opacity="${op == null ? 1 : op}"><rect x="${x}" y="${y}" width="${pw}" height="${ph}" rx="3.5" fill="#3a404c" stroke="#d7dce6" stroke-width="1"/>${inner}</g>`;
    const full = `<rect x="${x1 + 1}" y="${y + 1}" width="${pw - 2}" height="${ph - 2}" rx="2.5" fill="#8a93a6"/>`;
    const fit = m > 0.001 ? `<rect x="${x2 + 1}" y="${y + 1}" width="${pw - 2}" height="${ph - 2}" rx="2.5" fill="#596173"/><rect x="${(x2 + (pw - vw) / 2).toFixed(1)}" y="${(y + (ph - vh) / 2).toFixed(1)}" width="${vw.toFixed(1)}" height="${vh.toFixed(1)}" rx="1" fill="#8a93a6"/>`
      : `<rect x="${x2 + 1}" y="${y + 1}" width="${pw - 2}" height="${ph - 2}" rx="2.5" fill="#8a93a6"/>`;
    return `<svg viewBox="0 0 128 64" width="128" height="64" role="img" aria-hidden="true" data-air="${Math.round(m * 100)}"${dim ? ' data-dim="1"' : ""}>${phone(x1, full)}${phone(x2, fit, dim ? 0.35 : 1)}</svg>`;
  }
  /** El ejemplo del margen con lo que se aplica de verdad (y la nota al dia). */
  function drawAir() {
    const ex = $("#airRow .air-ex"); if (!ex) return;
    if (FX.syncAir) FX.syncAir();
    const fills = FX.airFills ? FX.airFills() : false;
    ex.innerHTML = airSvg(Math.round((FX.airApplied ? FX.airApplied() : 0) * 100), fills);
  }
  function wireFormat() {
    const fr = $("#fmtRow");
    if (fr && !fr.dataset.wired) {
      fr.dataset.wired = "1";
      const cv = $(".fmt-ex", fr);
      if (cv) Ticker.add({ el: cv, name: "format", fps: 4, eager: true, draw: () => drawFmtEx(cv) });
      radioKeys($("#segFmt"));
      // (despues del manejador de index.html: FMT ya cambio) la vista grande toma el formato en el mismo clic
      $("#segFmt").addEventListener("click", () => { E.refresh(); Ticker.kick(); });
    }
    const lr = $("#langRow");
    if (lr && !lr.dataset.wired) { lr.dataset.wired = "1"; radioKeys($("#segLang")); $("#segLang").addEventListener("click", () => setTimeout(() => E.refreshSoon(0), 0)); }
    const ar = $("#airRow");
    if (ar && !ar.dataset.wired) {
      ar.dataset.wired = "1";
      const rg = $("#air");
      // (despues del manejador de index.html: AIR y «el usuario lo movio» ya cambiaron)
      if (rg) { rg.addEventListener("input", () => setTimeout(drawAir, 0)); rg.addEventListener("change", () => setTimeout(drawAir, 0)); }
      if (typeof V !== "undefined" && V) V.addEventListener("loadedmetadata", drawAir);
      drawAir();
    }
    const er = $("#enhanceRow"), ex = $("#enhEx");
    if (er && ex && !er.dataset.wired) {
      er.dataset.wired = "1";
      const vOk = () => (typeof V !== "undefined" && V && V.src ? null : T("listen.need_music"));
      const a = ListenButton.create({ key: "voice:before", name: T("listen.voice.before"), label: T("listen.before"), cls: "sm", why: vOk, spec: () => ({ kind: "voice", id: "before", enhance: false, max: 5 }) });
      const b = ListenButton.create({ key: "voice:after", name: T("listen.voice.after"), label: T("listen.after"), cls: "sm", why: vOk, spec: () => ({ kind: "voice", id: "after", enhance: true, max: 5 }) });
      ex.append(a.el, b.el);
    }
  }
  /** Idiomas que la edicion IA aun no ofrece: apagados con su razon (FX.syncProControls pone el titulo). */
  function syncLangTitles() { $$("#segLang button").forEach(b => { if (b.disabled) b.title = T("lang.off"); }); }
  function FormatSection(body) {
    const IDS = ["fmtRow", "langRow", "airRow", "enhanceRow"];
    function build() { body.innerHTML = ""; E.placeStatic(body, IDS); wireFormat(); syncLangTitles(); }
    function update() {
      E.placeStatic(body, IDS);
      const rg = $("#air"), hv = $("#airLbl"); if (rg && hv) hv.textContent = T("air.v", { v: rg.value });
      drawAir();
      syncLangTitles();
      Ticker.kick();
    }
    build();
    return { update };
  }
  E.addSection({
    id: "stFormat",
    build(b) { return FormatSection(b); },
    summary() {
      const f = typeof FMT !== "undefined" ? FMT : "9:16", l = typeof LANG !== "undefined" ? LANG : "es";
      return { k: T("summary.format"), v: [T("fmt." + f) || f, T("lang." + l) || l].join(" · "), aria: T("summary.change.format") };
    },
  });
  // piezas para estilo_catalog.js (inspector y catalogos)
  E._kp = { optionList, rangeCtl, exBox };
})();
