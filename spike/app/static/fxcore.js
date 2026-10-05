/* Reelfy Studio: logica PURA del editor pro (sin DOM ni red).
   En el navegador queda como window.FXCore; en node (pruebas) se usa con require().
   Espejo de spike/scripts/motion/timeline.py para que la UI calcule el tiempo final
   igual que el render (cortes -> tramos conservados -> reticula de cuadros). */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FXCore = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ------------------------------------------------------------------ constantes
  const MIN_KEEP = 0.05;   // s: tramo minimo que vale la pena conservar
  const MIN_FRAG = 0.15;   // s: keeps mas cortos se absorben en el corte
  const SNAP_S = 0.12;     // s: union tan cerca de un cambio de toma -> se pega
  const EPS = 1e-9, Q_EPS = 1e-6;
  const TRACKS = ["text", "graphics", "cutaways", "effects", "transitions", "sfx", "media"];
  const EVENT_TRACKS = { transitions: 1, sfx: 1 };
  const RULER_STEPS = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];

  // ------------------------------------------------------------------ linea de tiempo
  /** Complemento de los cortes HABILITADOS dentro de [0, duration] (timeline.keeps_from_cuts). */
  function keepsFromCuts(cuts, duration, minKeep) {
    const mk = minKeep == null ? MIN_KEEP : minKeep;
    const dur = Math.max(0, +duration || 0);
    const act = [];
    (cuts || []).forEach(c => {
      if (!c || typeof c !== "object" || c.enabled === false) return;
      let s = +c.start, e = +c.end;
      if (!isFinite(s) || !isFinite(e)) return;
      if (e < s) { const x = s; s = e; e = x; }
      s = Math.min(Math.max(s, 0), dur); e = Math.min(Math.max(e, 0), dur);
      if (e - s > EPS) act.push([s, e]);
    });
    act.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const keeps = []; let t = 0;
    act.forEach(([s, e]) => { if (s - t > mk) keeps.push([t, s]); t = Math.max(t, e); });
    if (dur - t > mk) keeps.push([t, dur]);
    return keeps;
  }

  function merge(keeps) {
    const out = [];
    keeps.map(k => [+k[0], +k[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).forEach(([a, b]) => {
      if (b - a <= EPS) return;
      if (out.length && a <= out[out.length - 1][1] + EPS) out[out.length - 1][1] = Math.max(out[out.length - 1][1], b);
      else out.push([a, b]);
    });
    return out;
  }

  function shotChanges(shots, end) {
    const out = new Set();
    (shots || []).forEach(s => {
      const t = typeof s === "object" && s ? +s.start : +s;
      if (isFinite(t) && t > EPS && t < end - EPS) out.add(t);
    });
    return [...out].sort((a, b) => a - b);
  }

  function bisectRight(arr, x) { let lo = 0, hi = arr.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (x < arr[m]) hi = m; else lo = m + 1; } return lo; }
  function bisectLeft(arr, x) { let lo = 0, hi = arr.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < x) lo = m + 1; else hi = m; } return lo; }

  /** Primer cuadro (k / fps) de la toma que empieza en t: round(t*fps), empates hacia arriba
   *  (timeline.shot_frame: lo que hace el filtro fps con round=near). */
  function shotFrame(t, fps) { return Math.floor(+t * +fps + 0.5 + Q_EPS); }

  /** Mapa tiempo original <-> tiempo final (port de motion.timeline.Timeline). */
  function Timeline(keeps, duration, fps, minFrag, shots, snapS) {
    this.fps = fps ? +fps : null;
    const mf = minFrag || 0, ss = snapS == null ? SNAP_S : snapS;
    let ks = merge(keeps != null ? keeps : [[0, +duration || 0]]);
    if (duration != null) {
      const d = +duration;
      ks = ks.filter(k => k[0] < d).map(k => [Math.max(0, k[0]), Math.min(d, k[1])]).filter(k => k[1] - k[0] > EPS);
    }
    let snapped = ks.map(k => [k[0], k[1], false, false]);
    const changes = shotChanges(shots, ks.length ? ks[ks.length - 1][1] : 0);
    if (changes.length && ss > 0) {
      const tol = Math.max(ss, 2 / (this.fps || 30)) + 1e-6;
      snapped = snapped.map(([a, b]) => {
        let sa = false, sb = false;
        changes.forEach(s => {
          if (!(a + EPS < s && s < b - EPS)) return;
          if (s - a <= tol && b - s > tol) { a = s; sa = true; }
          else if (b - s <= tol && s - a > tol) { b = s; sb = true; }
        });
        return [a, b, sa, sb];
      });
    }
    if (mf > 0 && snapped.length > 1) {
      const big = snapped.filter(k => k[1] - k[0] >= mf - EPS);
      snapped = big.length ? big : [snapped.reduce((m, k) => (k[1] - k[0] > m[1] - m[0] ? k : m))];
    }
    if (this.fps) {
      const f = this.fps, q = [];
      snapped.forEach(([a, b, sa, sb]) => {
        let fa = sa ? shotFrame(a, f) : Math.floor(a * f + Q_EPS);
        let fb = sb ? shotFrame(b, f) : Math.ceil(b * f - Q_EPS);
        // un limite en (o a < 1 cuadro de) un cambio de toma no toma un cuadro de la otra toma
        const i = bisectRight(changes, a + EPS) - 1;
        if (i >= 0) fa = Math.max(fa, shotFrame(changes[i], f));
        const j = bisectLeft(changes, b - EPS);
        if (j < changes.length) fb = Math.min(fb, shotFrame(changes[j], f));
        if (duration != null) fb = Math.min(fb, Math.max(fa, Math.ceil(+duration * f - Q_EPS)));
        if (fb > fa) q.push([fa / f, fb / f]);
      });
      ks = merge(q);
    } else ks = merge(snapped.map(k => [k[0], k[1]]));
    this.keeps = ks;
    this.duration = duration != null ? +duration : (ks.length ? ks[ks.length - 1][1] : 0);
    this.offsets = []; let acc = 0;
    ks.forEach(([a, b]) => { this.offsets.push(acc); acc += b - a; });
    this.finalDuration = acc;
    this._starts = ks.map(k => k[0]);
  }
  Timeline.prototype._idx = function (t) { return bisectRight(this._starts, t + EPS) - 1; };
  /** Tiempo final de t (null si t cae dentro de un corte). */
  Timeline.prototype.remap = function (t) {
    const i = this._idx(+t); if (i < 0) return null;
    const [a, b] = this.keeps[i];
    if (t <= b + EPS) return this.offsets[i] + Math.min(Math.max(t - a, 0), b - a);
    return null;
  };
  /** Como remap, pero dentro de un corte devuelve la union siguiente. */
  Timeline.prototype.remapClamped = function (t) {
    if (!this.keeps.length) return 0;
    const i = this._idx(+t); if (i < 0) return 0;
    const [a, b] = this.keeps[i];
    if (t <= b) return this.offsets[i] + Math.max(0, t - a);
    return this.offsets[i] + (b - a);
  };
  Timeline.prototype.inverse = function (tf) {
    if (!this.keeps.length) return 0;
    if (tf <= 0) return this.keeps[0][0];
    if (tf >= this.finalDuration) return this.keeps[this.keeps.length - 1][1];
    let i = bisectRight(this.offsets, tf + EPS) - 1;
    i = Math.max(0, Math.min(i, this.keeps.length - 1));
    const [a, b] = this.keeps[i];
    return Math.min(b, a + (tf - this.offsets[i]));
  };
  /** Uniones (tiempo original del final del keep anterior) -> util para imanes. */
  Timeline.prototype.joinsOrig = function () {
    const out = [];
    for (let i = 1; i < this.keeps.length; i++) out.push({ before: this.keeps[i - 1][1], after: this.keeps[i][0], t: this.offsets[i] });
    return out;
  };

  function timelineFromPlan(plan, fps) {
    const dur = +((plan && plan.duration) || 0);
    const keeps = keepsFromCuts((plan && plan.cuts) || [], dur, MIN_KEEP);
    return new Timeline(keeps, dur, fps === undefined ? 30 : fps, MIN_FRAG, plan && plan.shots, SNAP_S);
  }

  // ------------------------------------------------------------------ esquema / items
  function getPath(o, path) {
    if (!o) return undefined;
    const ks = String(path).split(".");
    let v = o;
    for (const k of ks) { if (v == null || typeof v !== "object") return undefined; v = v[k]; }
    return v;
  }
  function setPath(o, path, val) {
    const ks = String(path).split(".");
    let v = o;
    for (let i = 0; i < ks.length - 1; i++) {
      if (v[ks[i]] == null || typeof v[ks[i]] !== "object") v[ks[i]] = {};
      v = v[ks[i]];
    }
    if (val === undefined) delete v[ks[ks.length - 1]]; else v[ks[ks.length - 1]] = val;
    return o;
  }
  const clone = o => (o == null ? o : JSON.parse(JSON.stringify(o)));
  const isEvent = track => !!EVENT_TRACKS[track];
  const kindKey = (schema, track) => (schema && schema.tracks && schema.tracks[track] && schema.tracks[track].key) || (track === "text" ? "preset" : track === "sfx" ? "name" : "kind");
  const itemKind = (schema, track, it) => (it ? it[kindKey(schema, track)] : undefined);

  function kindParams(schema, track, kind) {
    const tr = schema && schema.tracks && schema.tracks[track];
    const k = tr && tr.items && tr.items[kind];
    return (k && k.params) || [];
  }
  function commonParams(schema, track) {
    const tr = schema && schema.tracks && schema.tracks[track];
    const c = (tr && tr.common) || (isEvent(track) ? "event" : "range");
    return (schema && schema.common && schema.common[c]) || [];
  }
  function kinds(schema, track) {
    const tr = schema && schema.tracks && schema.tracks[track];
    return tr && tr.items ? Object.keys(tr.items) : [];
  }
  function kindLabel(schema, track, kind) {
    const tr = schema && schema.tracks && schema.tracks[track];
    const k = tr && tr.items && tr.items[kind];
    return (k && k.label) || String(kind || "").replace(/_/g, " ");
  }

  /** Rellena los parametros que falten de un kind con sus defaults (no pisa lo existente). */
  function fillDefaults(schema, track, item) {
    kindParams(schema, track, itemKind(schema, track, item)).forEach(p => {
      if (getPath(item, p.k) === undefined && p.default !== undefined && p.default !== null) setPath(item, p.k, clone(p.default));
    });
    return item;
  }

  function newId(track, existing) {
    const pre = { text: "tx", graphics: "gr", cutaways: "cw", effects: "fx", transitions: "tr", sfx: "sf", media: "md" }[track] || "it";
    const ids = existing instanceof Set ? existing : new Set(existing || []);
    let id;
    do { id = `${pre}_u${Math.floor(Math.random() * 1e8).toString(36)}`; } while (ids.has(id));
    return id;
  }

  function allIds(fx) {
    const s = new Set();
    TRACKS.forEach(t => ((fx && fx[t]) || []).forEach(it => it && it.id && s.add(it.id)));
    return s;
  }

  const DEFAULT_LEN = { text: 2.0, graphics: 2.0, cutaways: 2.5, effects: 1.5 };
  /** Item nuevo de usuario en el tiempo t (tiempo original). */
  function newItem(schema, track, kind, t, duration, fx) {
    const it = { id: newId(track, allIds(fx)), enabled: true, src: "user", locked: false };
    it[kindKey(schema, track)] = kind;
    const dur = +duration || 1e9;
    if (isEvent(track)) it.at = round3(Math.min(Math.max(0, t), dur));
    else {
      const L = DEFAULT_LEN[track] || 2;
      const s = Math.min(Math.max(0, t), Math.max(0, dur - 0.3));
      it.start = round3(s); it.end = round3(Math.min(dur, s + L)); it.intensity = 1;
    }
    fillDefaults(schema, track, it);
    symDefaults(track, it);
    // con acento propio del proyecto (fx.accent), los colores de acento del catalogo (el amarillo de
    // siempre) nacen con el del proyecto: un titulo o tarjeta nuevos combinan con los subtitulos
    const acc = normHex(fx && fx.accent);
    if (acc && acc !== DEFAULT_ACCENT) ACCENT_KEYS.forEach(k => { if (typeof it[k] === "string" && normHex(it[k]) === DEFAULT_ACCENT) it[k] = acc; });
    if (track === "text" && !it.text && !(it.lines && it.lines.length)) it.text = "Tu texto";
    if (track === "graphics" && it.kind === "sticker_text" && !it.text) it.text = "NUEVO";
    if (track === "graphics" && it.kind === "cta" && !it.label) it.label = "Sígueme";
    if (track === "graphics" && it.kind === "card" && !it.title) { it.title = "Título"; it.value = "Valor"; }
    if (track === "graphics" && it.kind === "lower_third" && !it.title) { it.title = "Nombre"; it.subtitle = "Descripción"; }
    if (track === "graphics" && it.kind === "icon" && !it.name) it.name = "star";
    if (track === "cutaways" && !getPath(it, "text.text")) setPath(it, "text.text", "Título");
    if (track === "transitions" && !it.dur) it.dur = 0.4;
    return it;
  }

  /** Red de seguridad de la SIMETRIA (regla de la marca) para lo que agrega el usuario: todo nace
      centrado en x = XC aunque el catalogo traiga otro default (el PiP sin posicion y la barra de
      progreso no usan x). Las posiciones explicitas que pasa quien llama (`extra`) se aplican
      despues y mandan. */
  function symDefaults(track, it) {
    if (track === "text") { it.x = XC; it.align = "center"; if (it.preset === "counter") it.anchor = "center"; }
    else if (track === "graphics" && it.kind !== "media" && it.kind !== "progress") {
      it.x = XC;
      if (it.kind === "lower_third") it.align = "center";
    }
    return it;
  }
  /** lower_third: con align "center" x es el CENTRO del panel; con "left" o sin align (planes
      viejos) es su borde izquierdo. */
  const ltCentered = it => !!it && it.kind === "lower_third" && it.align === "center";
  /** Cambia la alineacion de un rotulo: al cambiar el significado de x se recoloca simetrico
      (centrado en XC; a la izquierda en el borde de SAFE, el espejo de la columna de botones). */
  function setLowerThirdAlign(it, align) {
    const was = ltCentered(it);
    it.align = align === "left" ? "left" : "center";
    const now = ltCentered(it);
    if (was !== now) it.x = now ? XC : SAFE.left;
    return it;
  }

  /** Copia de un item con id nuevo, desplazada `shift` segundos. */
  function duplicateItem(track, item, shift, fx, duration) {
    const c = clone(item);
    c.id = newId(track, allIds(fx));
    c.src = "user"; c.locked = false;
    const dur = +duration || 1e9;
    if (isEvent(track)) c.at = round3(Math.min(dur, (+c.at || 0) + shift));
    else {
      const L = (+c.end || 0) - (+c.start || 0);
      let s = (+c.start || 0) + shift;
      if (s + L > dur) s = Math.max(0, dur - L);
      c.start = round3(s); c.end = round3(s + L);
    }
    return c;
  }

  /** Cambia el kind/preset de un item conservando campos comunes y rellenando defaults. */
  function changeKind(schema, track, item, kind) {
    const key = kindKey(schema, track);
    const oldP = new Set(kindParams(schema, track, item[key]).map(p => p.k.split(".")[0]));
    const newP = new Set(kindParams(schema, track, kind).map(p => p.k.split(".")[0]));
    // x cambia de significado con un rotulo anclado a la izquierda (borde izquierdo vs centro):
    // se recentra; un rotulo nuevo nace centrado (x sigue siendo un centro)
    const wasEdge = item.kind === "lower_third" && !ltCentered(item);
    oldP.forEach(k => { if (!newP.has(k) && !["x", "y", "size", "color", "text"].includes(k)) delete item[k]; });
    if (track === "graphics" && kind === "lower_third" && item.kind !== "lower_third") item.align = "center";
    if (track === "graphics" && wasEdge && kind !== "lower_third") { item.x = XC; delete item.align; }
    item[key] = kind;
    return fillDefaults(schema, track, item);
  }

  function itemStart(track, it) { return isEvent(track) ? eventSpan(track, it)[0] : +it.start; }
  function itemEnd(track, it) { return isEvent(track) ? eventSpan(track, it)[1] : +it.end; }
  /** Ventana visible de un evento (transicion: dur centrada; sfx: punto). */
  function eventSpan(track, it, sfxCat) {
    const at = +it.at || 0;
    if (track === "transitions") { const d = Math.max(0, +it.dur || 0.4); return [at - d / 2, at + d / 2]; }
    if (track === "sfx" && sfxCat && sfxCat[it.name]) {
      const c = sfxCat[it.name]; const hit = +c.hit || 0, d = +c.dur || 0.3;
      return [at - hit, at - hit + d];
    }
    return [at, at];
  }

  /** Items activos en t (tiempo original). Eventos: dentro de su ventana (o +-tol). */
  function activeAt(track, items, t, tol) {
    const tl = tol == null ? 0.0 : tol;
    return (items || []).filter(it => {
      if (!it || it.enabled === false) return false;
      if (isEvent(track)) { const [a, b] = eventSpan(track, it); return t >= a - tl && t <= b + tl; }
      return t >= +it.start && t < +it.end;
    });
  }

  function lockedIds(fx) {
    const out = [];
    TRACKS.forEach(t => ((fx && fx[t]) || []).forEach(it => it && it.locked && it.id && out.push(it.id)));
    return out;
  }

  function findItem(fx, id) {
    for (const t of TRACKS) {
      const arr = (fx && fx[t]) || [];
      const i = arr.findIndex(it => it && it.id === id);
      if (i >= 0) return { track: t, index: i, item: arr[i] };
    }
    return null;
  }

  /** Etiqueta corta para el bloque del timeline. */
  function itemLabel(schema, track, it) {
    if (!it) return "";
    const lines = t => (Array.isArray(it.lines) && it.lines.length ? it.lines.map(l => l && l.text).filter(Boolean).join(" ") : String(t || "")).replace(/\s+/g, " ").trim();
    if (track === "text") {
      if (it.preset === "counter") return `${it.prefix || ""}${it.to != null ? it.to : ""}${it.suffix || ""}`;
      return lines(it.text) || kindLabel(schema, track, it.preset);
    }
    if (track === "graphics") {
      const k = kindLabel(schema, track, it.kind);
      const extra = it.kind === "doodle" || it.kind === "icon" ? it.name : (it.title || it.label || it.text || it.variant || "");
      return extra ? `${k}: ${extra}` : k;
    }
    if (track === "cutaways") { const tx = getPath(it, "text.text"); return tx ? `${kindLabel(schema, track, it.kind)}: ${String(tx).replace(/\s+/g, " ")}` : kindLabel(schema, track, it.kind); }
    if (track === "sfx") return String(it.name || "").replace(/_/g, " ");
    if (track === "media") {
      // un fragmento de un video de apoyo: lo que se dice mientras se ve (las primeras palabras)
      if (it.style === "reel" && it.text) return "Apoyo: " + String(it.text).replace(/\s+/g, " ").trim().split(" ").slice(0, 5).join(" ");
      return it.name ? String(it.name) : "Recurso";
    }
    return kindLabel(schema, track, itemKind(schema, track, it));
  }

  // ------------------------------------------------------------------ geometria del timeline
  /** Asigna filas sin solape: intervals = [[a,b],...] (mismas unidades). Devuelve {rows, n}. */
  function packRows(intervals, gap) {
    const g = gap || 0;
    const order = intervals.map((iv, i) => i).sort((i, j) => intervals[i][0] - intervals[j][0] || intervals[i][1] - intervals[j][1]);
    const ends = []; const rows = new Array(intervals.length).fill(0);
    order.forEach(i => {
      const [a, b] = intervals[i];
      let r = ends.findIndex(e => a >= e + g);
      if (r < 0) { r = ends.length; ends.push(b); } else ends[r] = b;
      rows[i] = r;
    });
    return { rows, n: Math.max(1, ends.length) };
  }

  /** Iman: el objetivo mas cercano dentro de thresholdPx (en px) o null. */
  function snapTo(t, targets, pxPerSec, thresholdPx) {
    const thr = (thresholdPx == null ? 8 : thresholdPx) / Math.max(1e-6, pxPerSec);
    let best = null, bd = thr;
    (targets || []).forEach(g => { const v = typeof g === "number" ? g : g.t; const d = Math.abs(v - t); if (d < bd) { bd = d; best = v; } });
    return best;
  }

  function rulerStep(pxPerSec, minPx) {
    const m = minPx || 70;
    return RULER_STEPS.find(s => s * pxPerSec >= m) || 600;
  }
  function fmtClock(s, step) {
    s = Math.max(0, +s || 0);
    const m = Math.floor(s / 60), sec = s - m * 60;
    if (step != null && step < 1) {
      const dec = Math.abs(Math.round(step * 10) - step * 10) < 1e-9 ? 1 : 2;
      return m ? `${m}:${sec.toFixed(dec).padStart(3 + dec, "0")}` : `${sec.toFixed(dec)}s`;
    }
    return `${m}:${String(Math.floor(sec)).padStart(2, "0")}`;
  }
  function round3(v) { return Math.round((+v || 0) * 1000) / 1000; }

  /** Zoom del timeline: slider 0..1000 (log) <-> multiplicador [1, zmax]. */
  function zoomFromSlider(v, zmax) { const z = Math.max(1, zmax || 1); return Math.exp((Math.max(0, Math.min(1000, v)) / 1000) * Math.log(z)); }
  function sliderFromZoom(z, zmax) { const m = Math.max(1.0001, zmax || 1); return Math.round(Math.log(Math.max(1, z)) / Math.log(m) * 1000); }

  // ------------------------------------------------------------------ subtitulos (aprox. de vista previa)
  /** Grupos de subtitulos aproximados: <= maxWords, corte duro en . ! ? o pausa > 0.45 s. */
  function captionGroups(phrases, maxWords, hardMax) {
    const mw = Math.max(1, +maxWords || 4), cap = +hardMax > 0 ? +hardMax : mw + 1;
    const words = []; (phrases || []).forEach(p => (p || []).forEach(w => w && words.push(w)));
    const groups = []; let cur = [];
    const flush = () => { if (cur.length) groups.push(cur); cur = []; };
    words.forEach((w, i) => {
      const prev = words[i - 1];
      if (cur.length && prev && (/[.!?]$/.test(String(prev.text || "").trim()) || (+w.start - +prev.end) > 0.45)) flush();
      cur.push(w);
      if (cur.length >= mw) flush();
    });
    flush();
    // evita grupos de una sola palabra pegandolos al anterior si cabe
    for (let i = groups.length - 1; i > 0; i--) {
      if (groups[i].length === 1 && groups[i - 1].length < Math.min(mw + 1, cap) && (+groups[i][0].start - +groups[i - 1][groups[i - 1].length - 1].end) < 0.45) {
        groups[i - 1] = groups[i - 1].concat(groups[i]); groups.splice(i, 1);
      }
    }
    return groups.map(g => ({ words: g, start: +g[0].start, end: +g[g.length - 1].end }));
  }

  /** Grupos aproximados con el max_words EFECTIVO del estilo (captionCfg) y el tope duro de
      "Impacto en mayusculas" (text.CAPTION_PRESETS.bold_caps.max_words_cap = 3). */
  function captionGroupsFor(phrases, cfg, titleFont) {
    const D = captionCfg(cfg, titleFont);
    return captionGroups(phrases, D.max_words, D.preset === "bold_caps" ? 3 : null);
  }

  // ------------------------------------------------------------------ subtitulos: geometria del render
  /** Espejo de text.CAPTION_PRESETS (una prueba lo compara con el motor): escala del tamano,
      espacio entre palabras, separacion entre lineas (em), crecimiento de la palabra activa, lineas
      maximas, contorno, sombra, revelado y mayusculas. */
  const CAPTION_PRESETS = {
    clean_bold: { scale: 1.0, space: 1.3, gap: 0.16, active_scale: 0.03, max_lines: 1, max_words: 4, stroke: 0.0, shadow: 0.95, reveal: "word", anim: "pop", active: "color" },
    karaoke: { scale: 1.0, space: 1.2, gap: 0.16, active_scale: 0.0, max_lines: 1, max_words: 4, stroke: 0.0, shadow: 0.95, reveal: "group", anim: "rise", active: "wipe" },
    boxed: { scale: 1.0, space: 1.75, gap: 0.34, active_scale: 0.0, max_lines: 1, max_words: 4, stroke: 0.0, shadow: 0.9, reveal: "group", anim: "rise", active: "pill" },
    minimal_small: { scale: 0.74, space: 1.15, gap: 0.2, active_scale: 0.0, max_lines: 2, max_words: 5, stroke: 0.0, shadow: 1.0, reveal: "word", anim: "fade", active: "none", font: "Poppins-SemiBold" },
    bold_caps: { scale: 1.28, space: 1.3, gap: 0.06, active_scale: 0.0, max_lines: 2, max_words: 3, stroke: 5.0, shadow: 0.8, reveal: "word", anim: "pop_strong", active: "color", case: "upper" },
  };
  /** Palabras que no deben cerrar una linea (text._FUNC_END, normalizadas). */
  const FUNC_END = new Set(("de del la las el los al a y e o u en con por para que te tu tus mi mis se su sus un una unos "
    + "unas lo le les me nos ya no si sin como muy mas pero es son como cuando donde porque cual cuanto cuanta al del tan "
    + "sobre entre hasta desde hacia sin ante tras segun mientras aunque ni").split(" "));
  /** Configuracion efectiva de los subtitulos (text._cap_cfg): preset, fuente, tamano (size*scale),
      espacio, lineas, mayusculas, contorno y sombra. `titleFont` = fuente de titulo del estilo
      (bold_caps la usa si el plan no fija otra). */
  function captionCfg(cfg, fonts) {
    const c = cfg || {}, preset = CAPTION_PRESETS[c.preset] ? c.preset : "clean_bold", P = CAPTION_PRESETS[preset];
    // fonts: las letras del estilo de edicion ({title, caption}: captionFonts(plan)); un texto = solo
    // la de titulo (llamadas de antes). Sin letra elegida («La del estilo»), la de su estilo, como el motor
    const F = fonts && typeof fonts === "object" ? fonts : { title: fonts || null, caption: null };
    let font = c.font;
    if (preset === "minimal_small" && (!font || font === "Poppins-ExtraBold")) font = P.font;
    else if (preset === "bold_caps" && (!font || font === "Poppins-ExtraBold")) font = F.title || "Poppins-Black";
    else font = font || F.caption || "Poppins-ExtraBold";
    const cs = c.case && c.case !== "none" ? c.case : (P.case || "none");
    const mw = Math.min(+c.max_words || P.max_words, preset === "bold_caps" ? 3 : 99);
    return Object.assign({}, P, { preset, font, case: cs, max_words: mw,
      size: (+c.size || 0.052) * P.scale, x: c.x != null ? +c.x : 0.5, y: c.y != null ? +c.y : 0.72,
      max_lines: c.max_lines != null ? Math.max(1, Math.floor(+c.max_lines)) : P.max_lines,
      stroke: c.stroke != null ? +c.stroke : P.stroke, shadow: c.shadow != null ? +c.shadow : P.shadow,
      kw_accent: c.kw_accent != null ? !!c.kw_accent : preset === "clean_bold",
      recenter: c.recenter != null ? !!c.recenter : ["clean_bold", "minimal_small", "bold_caps"].includes(preset) });
  }
  /** Letras de la paleta de cada estilo de edicion (assets.PALETTES[estilo].fonts): sin letra elegida,
      los subtitulos usan la `caption` de su estilo e «Impacto en mayúsculas» la `title`
      (text._cap_cfg con ctx.palette). Paridad: test_estilo.py. */
  const STYLE_FONTS = {
    dinamico: { title: "Poppins-Black", caption: "Poppins-ExtraBold" },
    elegante: { title: "DMSerifDisplay", caption: "Poppins-SemiBold" },
    minimal: { title: "Montserrat-ExtraBold", caption: "Montserrat-Bold" },
    energetico: { title: "Anton", caption: "Poppins-Black" },
  };
  /** Las letras de los subtitulos del plan (las del estilo con que se dirigio; dinamico si ninguno). */
  function captionFonts(plan) {
    const st = ((plan && plan.director) || {}).style;
    return Object.assign({}, STYLE_FONTS[st] || STYLE_FONTS.dinamico);
  }
  /** Ancho util (fraccion de W) de un bloque centrado en x (text._usable con width 0.96). */
  function captionUsable(x) {
    const xc = Math.min(Math.max(+x, SAFE.left), SAFE.right);
    return Math.max(0.3, 2 * Math.min(xc - SAFE.left, SAFE.right - xc)) * 0.96;
  }
  /** text._balanced_split: parte las palabras (anchos de avance) en k lineas minimizando la mas
      ancha; una linea (salvo la ultima) no termina en palabra funcional. -> inicio de cada linea. */
  function balancedSplit(widths, space, k, words) {
    const n = widths.length;
    k = Math.max(1, Math.min(k, n));
    if (k === 1) return [0];
    const pre = [0]; widths.forEach(w => pre.push(pre[pre.length - 1] + w));
    const lw = (i, j) => pre[j] - pre[i] + space * (j - i - 1);
    const func = (words && words.length === n ? words : []).map(w => FUNC_END.has(normWord(w)));
    const avg = lw(0, n) / k;
    let best = null, bc = Infinity;
    const cuts = [];
    const rec = (from, left) => {
      if (!left) {
        const b = [0].concat(cuts, [n]), ws = [];
        for (let q = 0; q < b.length - 1; q++) ws.push(lw(b[q], b[q + 1]));
        // mismo orden de operaciones que el motor (empates identicos)
        let cost = Math.max(...ws), even = 0, pen = 0;
        for (let m = 0; m < k; m += 2) even += ws[m];
        cost += 1e-4 * even;
        cuts.forEach(c => { if (func[c - 1]) pen += 0.45 * avg; });
        cost += pen;
        if (cost < bc) { bc = cost; best = [0].concat(cuts); }
        return;
      }
      for (let c = from; c <= n - left; c++) { cuts.push(c); rec(c + 1, left - 1); cuts.pop(); }
    };
    rec(1, k - 1);
    return best;
  }
  /** Geometria de un grupo de subtitulos como text._layout_group: tamano del preset, espacio entre
      palabras, envoltura balanceada (hasta max_lines, o las `lines` que dio /captions) y reduccion
      de la linea que no cabe. `measure(txt, px)` -> {adv, l, r, asc, desc} (avance y tinta relativa
      al origen). Con `rect` (el de POST /captions, fraccion de la caja) el tamano se calibra para
      que la linea mas ancha mida lo del export y el bloque queda en su lugar exacto.
      -> {fs, sp, cx, top, height, rows: [{a, b, sc, x0, base, w}], xs: [x de cada palabra]} en px. */
  function captionLayout(texts, cfg, W, H, measure, o) {
    const opt = o || {}, D = captionCfg(cfg, opt.titleFont), n = (texts || []).length;
    if (!n) return null;
    const fs = D.size * W;
    const M = texts.map(t => measure(t, fs)), sp = measure(" ", fs).adv * D.space;
    const maxw = captionUsable(D.x) * W;
    const inkW = (a, b) => { let x = 0, l = null, r = 0; for (let i = a; i < b; i++) { if (l == null) l = x + M[i].l; r = x + M[i].r; x += M[i].adv + sp; } return r - (l || 0); };
    const adv = M.map(m => m.adv);
    const rowsOf = k => { const st = balancedSplit(adv, sp, k, texts); return st.map((a, i) => [a, i + 1 < st.length ? st[i + 1] : n]); };
    let rows;
    if (opt.lines) rows = rowsOf(Math.max(1, Math.min(n, Math.floor(opt.lines))));
    else {
      rows = [[0, n]];
      if (inkW(0, n) > maxw && n > 1 && D.max_lines > 1) {
        rows = rowsOf(2);
        if (Math.max(...rows.map(r => inkW(r[0], r[1]))) > maxw && n > 2 && D.max_lines > 2) rows = rowsOf(3);
      }
    }
    // la linea que no cabe se reduce (text._layout_group: *0.995)
    const out = rows.map(([a, b]) => { const w = inkW(a, b); return { a, b, sc: w > maxw ? maxw / w * 0.995 : 1, w }; });
    // calibracion con el rect del export: misma anchura de la linea mas ancha
    let k = 1;
    const rect = Array.isArray(opt.rect) && opt.rect.length >= 4 && +opt.rect[2] > +opt.rect[0] ? opt.rect.map(Number) : null;
    const widest = Math.max(...out.map(r => r.w * r.sc));
    if (rect && widest > 1) k = Math.min(2, Math.max(0.5, (rect[2] - rect[0]) * W / widest));
    const cx = rect ? (rect[0] + rect[2]) / 2 * W : D.x * W;
    const xs = new Array(n).fill(0);
    let hsum = 0;
    out.forEach(r => {
      r.sc *= k;
      let x = 0, asc = 0, desc = 0;
      for (let i = r.a; i < r.b; i++) { xs[i] = x; x += (M[i].adv + sp) * r.sc; asc = Math.max(asc, M[i].asc || 0); desc = Math.max(desc, M[i].desc || 0); }
      const l = M[r.a].l * r.sc, rr = xs[r.b - 1] + M[r.b - 1].r * r.sc;
      r.w = rr - l; r.x0 = cx - (l + rr) / 2;
      r.asc = asc * r.sc; r.desc = desc * r.sc; hsum += r.asc + r.desc;
    });
    // lineas apiladas por tinta con `gap` em entre ellas (text._stack); con rect, su alto exacto
    let gap = D.gap * fs * k, height = hsum + gap * (out.length - 1), top;
    if (rect) {
      const rh = (rect[3] - rect[1]) * H;
      if (out.length > 1) gap = Math.max(0, (rh - hsum) / (out.length - 1));
      height = rh; top = rect[1] * H;
    } else top = D.y * H - height / 2;
    let y = 0;
    out.forEach(r => { r.base = y + r.asc; y += r.asc + r.desc + gap; });
    return { fs: fs * k, sp: sp * k, cx, top, height, rows: out, xs, cfg: D, M };
  }
  /** text._active_bump: 0..1..0 mientras la palabra se pronuncia (sube en ~80 ms, baja en 0.16 s). */
  function activeBump(t, w) {
    const a = ease("out_cubic", (t - +w.start + 0.03) / 0.08);
    const b = t > +w.end ? 1 - ease("in_out_quad", (t - +w.end) / 0.16) : 1;
    return Math.max(0, Math.min(a, b));
  }

  // ------------------------------------------------------------------ titulos: geometria del render
  /** Espejo de text.PRESETS (una prueba lo compara): altura por defecto, alineacion, espacio entre
      palabras, separacion entre lineas (em), tamanos maximo/minimo, caracteres por linea, lineas
      maximas, deriva y mayusculas. */
  const TEXT_PRESETS = {
    title_pop: { y: 0.2, align: "center", space: 1.12, gap: 0.13, max_size: 0.125, min_size: 0.06, cpl: 13, max_lines: 3 },
    stack: { y: 0.62, align: "center", space: 1.1, gap: 0.13, max_size: 0.24, min_size: 0.05, cpl: 12, max_lines: 4 },
    cascade: { y: 0.2, align: "center", space: 1.0, gap: 0.1, max_size: 0.095, min_size: 0.05, cpl: 12, max_lines: 4 },
    serif_elegant: { y: 0.15, align: "center", space: 1.0, gap: 0.06, max_size: 0.135, min_size: 0.06, cpl: 15, max_lines: 3 },
    script_accent: { y: 0.5, align: "center", space: 1.0, gap: 0.02, max_size: 0.24, min_size: 0.08, cpl: 14, max_lines: 2 },
    behind_giant: { y: 0.3, align: "center", space: 1.0, gap: 0.035, max_size: 0.46, min_size: 0.12, cpl: 9, max_lines: 2, drift: 0.04, case: "upper" },
    highlight_marker: { y: 0.66, align: "center", space: 1.25, gap: 0.24, max_size: 0.1, min_size: 0.05, cpl: 16, max_lines: 3 },
    typewriter: { y: 0.7, align: "center", space: 1.0, gap: 0.28, max_size: 0.07, min_size: 0.04, cpl: 18, max_lines: 4 },
    counter: { y: 0.45, align: "center", space: 1.0, gap: 0.12, max_size: 0.24, min_size: 0.08, cpl: 14, max_lines: 3 },
    kicker: { y: 0.22, align: "center", space: 1.1, gap: 0.12, max_size: 0.12, min_size: 0.055, cpl: 13, max_lines: 3 },
  };
  const TEXT_PRESET_Y = {};
  Object.keys(TEXT_PRESETS).forEach(k => { TEXT_PRESET_Y[k] = TEXT_PRESETS[k].y; });
  /** text._ITALIC: variante italica real de una fuente (si no hay, oblicua sintetica). */
  const ITALIC = { DMSerifDisplay: "DMSerifDisplay-Italic", InstrumentSerif: "InstrumentSerif-Italic", "Poppins-Black": "Poppins-BlackItalic" };
  /** text._italic -> {font, synth}: synth = sin variante real (se inclina en el navegador). */
  function italicOf(font) {
    const f = String(font || "");
    if (ITALIC[f]) return { font: ITALIC[f], synth: false };
    if (/Italic$/.test(f)) return { font: f, synth: false };
    return { font: f, synth: true };
  }
  /** Ancho util de un titulo (text._usable): segun x, `width` y la deriva del preset. x es el
      CENTRO del bloque con cualquier alineacion (simetria: `align` solo alinea las lineas dentro
      del bloque), asi que el ancho es simetrico alrededor de x (`align` se ignora). */
  function textUsable(x, align, width, drift) {
    const xc = Math.min(Math.max(+x, SAFE.left), SAFE.right);
    const w = 2 * Math.min(xc - SAFE.left, SAFE.right - xc);
    return Math.max(0.3, w) * (width != null && +width > 0 ? +width : 1) / (1 + Math.max(0, +drift || 0));
  }

  // ------------------------------------------------------------------ texto: aparicion por palabra
  /** Igual que text._norm: minusculas, sin acentos ni puntuacion (letras y digitos Unicode). */
  function normWord(s) { return String(s || "").toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^\p{L}\p{N}]/gu, ""); }
  function wordMatch(a, b) {
    if (!a || !b) return false;
    if (a === b) return true;
    return a.length >= 3 && b.length >= 3 && (a.startsWith(b) || b.startsWith(a));
  }
  /** Tiempo local de aparicion de cada palabra de un titulo (port de text._unit_times).
      spoken = [{norm, start}] en tiempo local del item, o null (escalonado con `step`). */
  function unitTimes(norms, spoken, dur, step, lineOf, lead) {
    const n = norms.length; if (!n) return [];
    const ld = lead == null ? 0.05 : lead, st = step || 0.12;
    const lastOk = Math.max(0, dur - 0.45);
    if (spoken && spoken.length) {
      const times = new Array(n).fill(null); let j = 0;
      norms.forEach((nm, i) => {
        for (let k = j; k < Math.min(spoken.length, j + 6); k++) if (wordMatch(nm, spoken[k].norm)) { times[i] = spoken[k].start; j = k + 1; break; }
      });
      const known = times.map((t, i) => (t != null ? i : -1)).filter(i => i >= 0);
      if (!known.length) { const m = spoken.length; for (let i = 0; i < n; i++) times[i] = spoken[Math.min(m - 1, Math.floor(i * m / n))].start; }
      else for (let i = 0; i < n; i++) {
        if (times[i] != null) continue;
        const prv = known.filter(k => k < i).pop(), nxt = known.find(k => k > i);
        if (prv != null && nxt != null) times[i] = times[prv] + (times[nxt] - times[prv]) * (i - prv) / (nxt - prv);
        else if (prv != null) times[i] = times[prv] + st * (i - prv);
        else times[i] = Math.max(0, times[nxt] - st * (nxt - i));
      }
      const out = []; let acc = 0;
      times.forEach(t => { t = Math.max(acc, Math.max(0, +t - ld)); out.push(Math.min(t, lastOk)); acc = t; });
      return out;
    }
    const out = []; let t = 0;
    for (let i = 0; i < n; i++) { if (i && lineOf && lineOf[i] !== lineOf[i - 1]) t += st * 0.6; out.push(Math.min(t, lastOk)); t += st; }
    return out;
  }

  // ------------------------------------------------------------------ easing
  const EASE = {
    linear: p => p, in_quad: p => p * p, out_quad: p => 1 - (1 - p) * (1 - p),
    in_out_quad: p => (p < .5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2),
    in_cubic: p => p * p * p, out_cubic: p => 1 - Math.pow(1 - p, 3),
    in_out_cubic: p => (p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2),
    out_quart: p => 1 - Math.pow(1 - p, 4), smoothstep: p => p * p * (3 - 2 * p),
    out_expo: p => (p >= 1 ? 1 : 1 - Math.pow(2, -10 * p)),
    out_back: p => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); },
  };
  function ease(name, p) { p = Math.max(0, Math.min(1, p)); return (EASE[name] || EASE.in_out_cubic)(p); }

  /** Transformacion aproximada de camara en t (orig): {z, dx, dy} (dx,dy en fraccion del ancho). */
  function cameraAt(effects, t) {
    let z = 1, dx = 0, dy = 0;
    (effects || []).forEach(e => {
      if (!e || e.enabled === false || !(t >= e.start && t < e.end)) return;
      const k = e.intensity == null ? 1 : +e.intensity, L = Math.max(1e-3, e.end - e.start), lt = t - e.start;
      if (e.kind === "zoom" || e.kind === "kenburns") {
        const f = +(e.from != null ? e.from : 1), to = +(e.to != null ? e.to : 1.15);
        const zz = f + (to - f) * ease(e.easing || "in_out_cubic", lt / L);
        z *= 1 + (zz - 1) * k;
        if (e.kind === "kenburns" && Array.isArray(e.pan)) { dx += (+e.pan[0] || 0) * (lt / L) * k; dy += (+e.pan[1] || 0) * (lt / L) * k; }
      } else if (e.kind === "punch") {
        const a = +(e.amount != null ? e.amount : 0.12), hold = Math.max(0.05, +(e.hold != null ? e.hold : 0.6));
        const att = 0.07, rel = 0.18;
        let g = lt < att ? lt / att : lt < hold ? 1 : Math.max(0, 1 - (lt - hold) / rel);
        z *= 1 + a * g * k;
      } else if (e.kind === "shake") {
        const amp = (+(e.amp != null ? e.amp : 10)) / 1080, fr = +(e.freq != null ? e.freq : 12), dec = +(e.decay != null ? e.decay : 0.6);
        const env = Math.pow(1 - Math.min(1, lt / L), 1 + dec * 3);
        dx += amp * env * Math.sin(2 * Math.PI * fr * lt) * k;
        dy += amp * env * Math.cos(2 * Math.PI * fr * 0.83 * lt + 1.3) * k;
      }
    });
    return { z, dx, dy };
  }

  // ------------------------------------------------------------------ acentos de etiquetas
  /* El esquema del motor trae etiquetas ASCII ('Tamano', 'Posicion Y'). La UI las muestra con
     su ortografia correcta. Claves en minusculas; se respeta Mayuscula inicial y TODO MAYUS. */
  const ACC = {
    accion: "acción", alineacion: "alineación", amortiguacion: "amortiguación", angulo: "ángulo", angulos: "ángulos",
    animacion: "animación", animaciones: "animaciones", animo: "ánimo", anticipacion: "anticipación", aspero: "áspero",
    automatica: "automática", automatico: "automático", boton: "botón", caida: "caída", calido: "cálido", calificacion: "calificación",
    camara: "cámara", cancion: "canción", circulo: "círculo", codigo: "código", descripcion: "descripción", detras: "detrás",
    dinamico: "dinámico", direccion: "dirección", dramatica: "dramática", dramatico: "dramático", dramaticos: "dramáticos",
    duracion: "duración", energetico: "energético", estadistica: "estadística", exposicion: "exposición", fotografia: "fotografía",
    frio: "frío", genero: "género", grafico: "gráfico", graficos: "gráficos", inclinacion: "inclinación", informacion: "información",
    linea: "línea", lineas: "líneas", maquina: "máquina", mas: "más", maxima: "máxima", maximas: "máximas", maximo: "máximo",
    mayusculas: "mayúsculas", minima: "mínima", minimo: "mínimo", minusculas: "minúsculas", musica: "música", numero: "número",
    opcion: "opción", pagina: "página", pelicula: "película", posicion: "posición", rapida: "rápida", rapido: "rápido",
    rotacion: "rotación", rotulo: "rótulo", saturacion: "saturación", separacion: "separación", sobreexposicion: "sobreexposición",
    subtitulo: "subtítulo", subtitulos: "subtítulos", tamano: "tamaño", tambien: "también", telefono: "teléfono",
    tipografia: "tipografía", tipografias: "tipografías", titulo: "título", titulos: "títulos", transicion: "transición",
    ubicacion: "ubicación", ultima: "última", ultimo: "último", union: "unión", unico: "único", vacio: "vacío", vacios: "vacíos",
    variacion: "variación", vinieta: "viñeta", vineta: "viñeta",
  };
  /** Etiqueta del esquema con acentos (solo cambia palabras conocidas). */
  function accentEs(s) {
    return String(s == null ? "" : s).replace(/[A-Za-zÀ-ſ]+/g, w => {
      const lo = w.toLowerCase(), fix = ACC[lo];
      if (!fix) return w;
      if (w === w.toUpperCase() && w.length > 1) return fix.toUpperCase();
      if (w[0] === w[0].toUpperCase()) return fix.charAt(0).toUpperCase() + fix.slice(1);
      return fix;
    });
  }

  // ------------------------------------------------------------------ titulos: text / lines
  /* text.py (_specs_from_item) usa `lines` si no esta vacio e ignora `text`. El inspector edita
     el texto de las lineas conservando su estilo (escala, fuente, color). */
  const hasLines = it => !!(it && Array.isArray(it.lines) && it.lines.length);
  function titleText(it) {
    if (!it) return "";
    if (hasLines(it)) return it.lines.map(l => (typeof l === "string" ? l : String((l && l.text) || ""))).join("\n");
    return String(it.text == null ? "" : it.text);
  }
  /** Escribe el texto (Enter = nueva linea). Con `lines`, cada renglon va a su linea y conserva su
      estilo; renglones nuevos copian el estilo de la ultima linea; sobrantes se quitan. */
  function setTitleText(it, str) {
    const parts = String(str == null ? "" : str).split("\n");
    if (hasLines(it)) {
      const old = it.lines.map(l => (typeof l === "string" ? { text: l } : l || {}));
      it.lines = parts.map((p, i) => Object.assign({}, clone(old[Math.min(i, old.length - 1)]), { text: p }));
    }
    it.text = parts.join("\n");
    return it;
  }
  /** Quita el estilo por linea: vuelve a un texto simple con el mismo contenido. */
  function clearLineStyles(it) { const t = titleText(it); it.lines = null; it.text = t; return it; }

  // ------------------------------------------------------------------ subtitulos ocultos
  /** Espejo de render.build_job: los subtitulos se ocultan mientras haya CUALQUIER titulo activo
      (salvo keep_captions; tambien los de detras y los kicker, sin importar y) o una escena con
      texto, si hide_during_titles (default true). t en tiempo original. */
  function captionsHiddenAt(fx, t, boxAspect) {
    const cap = (fx && fx.captions) || {};
    const on = it => it && it.enabled !== false && t >= +it.start && t < +it.end;
    // foto con pie sin lugar para los subtitulos (render.caption_layout: "hide")
    if (photoCaptionRows(fx, boxAspect).some(r => r.hide && r.start <= t && t < r.end)) return true;
    const h = Object.prototype.hasOwnProperty.call(cap, "hide_during_titles") ? cap.hide_during_titles : true;
    if (!h) return false;
    if (((fx && fx.text) || []).some(it => on(it) && !it.keep_captions)) return true;
    return ((fx && fx.cutaways) || []).some(it => on(it) && it.text && typeof it.text === "object" && String(it.text.text || "").trim() !== "");
  }
  /** Grupo de subtitulos del render (POST /captions: tiempo original) activo en t: el ultimo que
      ya empezo, mientras no termine (text.draw_captions). */
  function capGroupAt(groups, t) {
    const gs = groups || [];
    let lo = 0, hi = gs.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (+gs[m].start <= t) lo = m + 1; else hi = m; }
    const g = gs[lo - 1];
    return g && t < +g.end ? g : null;
  }

  /** text.draw_captions: si el grupo ya estaba oculto al empezar (inicio + 0.13 s y su ultima
      palabra caen tapados por un titulo) no vuelve a salir al terminar el titulo. */
  function captionGroupSuppressed(fx, g, t, aspect) {
    if (!g || !g.words || !g.words.length) return false;
    const s0 = +g.start + 0.13, last = Math.max(+g.start, +g.words[g.words.length - 1].start);
    return t > s0 && captionsHiddenAt(fx, s0, aspect) && captionsHiddenAt(fx, last, aspect);
  }
  /** Altura (0..1) de los subtitulos en t (original): `y_ranges` [{start, end, y}] la cambia por
      tramo (las filas `hide` no cuentan: ocultan, ver captionRangeAlpha). */
  function captionY(cfg, t) {
    const c = cfg || {};
    const r = (Array.isArray(c.y_ranges) ? c.y_ranges : []).find(x => x && !x.hide && +x.start <= t && t < +x.end && x.y != null);
    return r ? +r.y : (c.y != null ? +c.y : 0.72);
  }
  /** Filas de `y_ranges` con `hide: true` (tiempo original): sin subtitulos en ese tramo, con un
      fundido de 0.12 s (text._yr_hide_alpha). -> alfa 0..1. */
  function captionRangeAlpha(cfg, t, fade) {
    const f = fade == null ? 0.12 : fade;
    let a = 1;
    ((cfg && cfg.y_ranges) || []).forEach(r => {
      if (!r || !r.hide) return;
      const s = +r.start, e = +r.end;
      if (!isFinite(s) || !isFinite(e)) return;
      const d = Math.max(s - t, t - e);
      a = Math.min(a, d > 0 ? Math.max(0, Math.min(1, d / f)) : 0);
    });
    return a;
  }

  // ------------------------------------------------------------------ foto con pie: subtitulos que se mueven
  const CAPTION_HALF = 0.05, PHOTO_SIZE = 0.72, PHOTO_Y = 0.46;
  function photoCaptionText(it) {
    let cap = it && it.caption;
    if (cap == null && it && typeof it.text === "string") cap = it.text;
    return cap != null && typeof cap !== "object" && typeof cap !== "boolean" ? String(cap).trim() : "";
  }
  /** Espejo de render.photo_caption_y: donde van los subtitulos durante una escena photo con pie.
      null = el pie no toca su franja; un numero = se mueven ahi; "hide" = no hay lugar. */
  function photoCaptionY(it, capY, boxAspect) {
    const S = SAFE, asp = boxAspect || 9 / 16;
    const num = (v, d) => (v == null || !isFinite(+v) ? d : +v);
    const pw = Math.min(0.8, Math.max(0.3, num(it.size, PHOTO_SIZE))) * asp;
    const y = num(it.y, PHOTO_Y);
    const cMid = y + 0.559 * pw, cHalf = 0.075 * pw + 0.015;
    if (Math.abs(cMid - capY) >= cHalf + CAPTION_HALF) return null;
    const half = 0.6745 * pw, bottom = 0.2 * pw;
    const lo = S.top + CAPTION_HALF, hi = S.bottom - CAPTION_HALF + 0.03;
    const below = y + half + 0.02 + CAPTION_HALF + 0.01;
    const over = y + half - bottom - 0.012 - CAPTION_HALF;
    const above = y - half - 0.035 * pw - 0.02 - CAPTION_HALF - 0.01;
    const r4 = v => Math.round(v * 1e4) / 1e4;
    if (lo <= below && below <= hi) return r4(below);
    if (lo <= over && over <= hi && over - CAPTION_HALF >= y - half + 0.2 * pw) return r4(over);
    if (lo <= above && above <= hi) return r4(above);
    return "hide";
  }
  /** Filas automaticas de render.caption_layout (escenas photo con pie que chocarian con los
      subtitulos): [{start, end, y | hide, id}] en tiempo original. */
  function photoCaptionRows(fx, boxAspect) {
    const cap = (fx && fx.captions) || {};
    const user = (Array.isArray(cap.y_ranges) ? cap.y_ranges : []).filter(r => r && !r._auto && !r.hide);
    const out = [];
    ((fx && fx.cutaways) || []).forEach(it => {
      if (!it || it.enabled === false || it.kind !== "photo" || it.keep_captions || !photoCaptionText(it)) return;
      const t0 = +it.start || 0;
      let cy = cap.y != null ? +cap.y : 0.72;
      const u = user.find(r => +r.start <= t0 && t0 < +r.end && r.y != null);
      if (u) cy = +u.y;
      const v = photoCaptionY(it, cy, boxAspect);
      if (v === "hide") out.push({ start: t0, end: +it.end, hide: true, id: it.id });
      else if (v != null) out.push({ start: t0, end: +it.end, y: v, id: it.id });
    });
    return out;
  }
  /** Altura de los subtitulos en t como el render: primero las filas automaticas de la foto con pie,
      luego las del usuario (y_ranges), luego captions.y. */
  function captionYAt(fx, t, boxAspect) {
    const cfg = (fx && fx.captions) || {};
    const auto = photoCaptionRows(fx, boxAspect).find(r => r.y != null && r.start <= t && t < r.end);
    return auto ? auto.y : captionY(cfg, t);
  }

  // ------------------------------------------------------------------ iman: objetivos
  /** Objetivos del iman (tiempo original). o: {phrases, playhead (numero o null = sin playhead),
      exclude: Set de objetos (cortes, shorts, items), words: bool (bordes de palabras)}. El
      playhead que se pasa es el del INICIO del gesto: un arrastre que mueve el playhead no puede
      pegarse a si mismo. */
  function snapTargets(plan, o) {
    const opt = o || {}, ex = opt.exclude || null, out = [0, +(plan && plan.duration) || 0];
    if (opt.playhead != null && isFinite(+opt.playhead)) out.push(+opt.playhead);
    ((plan && plan.cuts) || []).forEach(c => { if (!ex || !ex.has(c)) out.push(+c.start, +c.end); });
    ((plan && plan.highlights) || []).forEach(h => { if (!ex || !ex.has(h)) out.push(+h.start, +h.end); });
    (opt.phrases || []).forEach(p => {
      if (!p || !p.length) return;
      out.push(+p[0].start, +p[p.length - 1].end);
      if (opt.words) p.forEach(w => out.push(+w.start, +w.end));
    });
    ((plan && plan.shots) || []).forEach(s => out.push(+s.start));
    const fx = plan && plan.fx;
    if (fx && plan.version === 2) {
      TRACKS.forEach(tk => (fx[tk] || []).forEach(it => {
        if (!it || (ex && ex.has(it))) return;
        if (isEvent(tk)) out.push(+it.at); else out.push(+it.start, +it.end);
      }));
      const m = fx.music || {};
      if (m.drop_at != null) out.push(+m.drop_at);
      (m.stops || []).forEach(s => out.push(+s.at));
    }
    return out.filter(v => isFinite(v));
  }

  // ------------------------------------------------------------------ regenerar con IA (fusion)
  const IGN = { locked: 1, src: 1, id: 1 };
  function sameVal(a, b) {
    if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) < 1e-3;
    if (a == null && b == null) return true;
    if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => sameVal(x, b[i]));
    if (a && b && typeof a === "object" && typeof b === "object") {
      const ks = new Set([...Object.keys(a), ...Object.keys(b)]);
      for (const k of ks) if (k[0] !== "_" && !sameVal(a[k], b[k])) return false;
      return true;
    }
    return a === b;
  }
  /** Igualdad VISIBLE de dos items (ignora id/src/locked y `text` si hay estilo por linea). */
  function sameVisual(a, b) {
    if (!a || !b) return false;
    const ks = new Set([...Object.keys(a), ...Object.keys(b)]);
    const lines = hasLines(a) || hasLines(b);
    for (const k of ks) {
      if (IGN[k] || k[0] === "_" || (lines && k === "text")) continue;
      if (!sameVal(a[k], b[k])) return false;
    }
    return true;
  }
  function overlap(track, a, b) {
    const a0 = itemStart(track, a), a1 = itemEnd(track, a), b0 = itemStart(track, b), b1 = itemEnd(track, b);
    return Math.min(a1, b1) - Math.max(a0, b0);
  }
  /** "Regenerar con IA": del plan que devuelve /direct (only_ids) se adopta SOLO la nueva version
      de cada item pedido: el mismo id en la misma pista, o (si el director lo reemplazo con otro id)
      el item NUEVO de la misma pista que mas se solapa con el original y que no duplica algo que ya
      existe. Lo demas que el director haya agregado o cambiado se ignora.
      -> {fx, results: [{id, track, status: "changed"|"replaced"|"same"|"none", newId}]} */
  /** Piezas LIGADAS a los items pedidos que el director puede cambiar al regenerarlos (y que el
      Studio adopta junto con ellos): el SFX emparejado de una transicion (mismo evento, +-0.2 s),
      los acentos de un titulo (garabatos, iconos y etiquetas de la IA que se solapan con el) y las
      piezas de una composicion (tx_bghero_s<N>, tx_bghero_s<N>b y fx_bgswap_s<N>). Lo bloqueado o
      creado por el usuario nunca es pieza ligada. -> Map id -> track. */
  function regenCompanions(fx, ids) {
    const out = new Map(), want = new Set(ids || []);
    const free = it => it && it.id && !want.has(it.id) && !it.locked && it.src !== "user";
    const anchor = id => { const m = /^(?:tx_bghero|fx_bgswap)_s(\d+)/.exec(String(id || "")); return m ? m[1] : null; };
    (ids || []).forEach(id => {
      const f = findItem(fx, id); if (!f) return;
      const it = f.item;
      if (f.track === "transitions") ((fx && fx.sfx) || []).forEach(s => { if (free(s) && Math.abs((+s.at || 0) - (+it.at || 0)) <= 0.2) out.set(s.id, "sfx"); });
      if (f.track === "text") ((fx && fx.graphics) || []).forEach(g => {
        if (free(g) && ["doodle", "icon", "sticker_text"].includes(g.kind) && Math.min(+g.end, +it.end) - Math.max(+g.start, +it.start) > 0) out.set(g.id, "graphics");
      });
      const a = anchor(id);
      if (a != null) TRACKS.forEach(tk => ((fx && fx[tk]) || []).forEach(o => { if (free(o) && anchor(o.id) === a) out.set(o.id, tk); }));
    });
    return out;
  }
  function mergeRegenerate(localFx, serverFx, ids) {
    const fx = clone(localFx) || {};
    const known = allIds(localFx);
    const used = new Set();
    const results = [];
    const comps = regenCompanions(localFx, ids);
    (ids || []).forEach(id => {
      const loc = findItem(fx, id);
      if (!loc) return;
      const { track } = loc, orig = loc.item;
      const sArr = (serverFx && serverFx[track]) || [];
      const same = sArr.find(it => it && it.id === id);
      let pick = null, status = "none";
      if (same) {
        if (sameVisual(orig, same)) status = "same";
        else { pick = same; status = "changed"; }
      } else {
        const tol = 0.3;
        const cands = sArr.filter(it => it && it.id && !known.has(it.id) && !used.has(it.id)
          && overlap(track, it, orig) > -tol
          && !(fx[track] || []).some(x => x !== orig && sameVisual(x, it)));
        cands.sort((x, y) => overlap(track, y, orig) - overlap(track, x, orig));
        if (cands.length) { pick = cands[0]; status = "replaced"; used.add(pick.id); }
      }
      if (pick) {
        const n = clone(pick);
        n.locked = !!orig.locked;
        fx[track][loc.index] = n;
      }
      results.push({ id, track, status, newId: pick ? pick.id : id });
    });
    // piezas ligadas: se adopta su version nueva (o se quitan si el director las apago/borro)
    const linked = [];
    comps.forEach((track, id) => {
      const arr = fx[track] || [], k = arr.findIndex(x => x && x.id === id);
      if (k < 0) return;
      const s = ((serverFx && serverFx[track]) || []).find(x => x && x.id === id);
      if (!s) { arr.splice(k, 1); linked.push({ id, track, status: "removed" }); }
      else if (!sameVisual(arr[k], s)) { arr[k] = clone(s); linked.push({ id, track, status: "changed" }); }
    });
    return { fx, results, linked };
  }

  // ------------------------------------------------------------------ duplicados tras re-dirigir
  function normText(s) { return String(s || "").split(/\s+/).map(normWord).filter(Boolean).join(" "); }
  /** Firma de contenido: dos items con la misma firma que se solapan son el mismo elemento. */
  function contentSig(track, it) {
    if (!it) return "";
    if (track === "text") return "t:" + (it.preset === "counter" ? `${it.prefix || ""}${it.to}${it.suffix || ""}` : normText(titleText(it)));
    // graficos: tambien la posicion (dos destellos iguales en lugares distintos son decoracion, no copia)
    if (track === "graphics") return `g:${it.kind}:${normText(it.name || it.title || it.label || it.text || it.variant || "")}:${it.x == null ? "a" : Math.round(+it.x * 20)}:${it.y == null ? "a" : Math.round(+it.y * 20)}`;
    if (track === "cutaways") return `c:${it.kind}:${normText(getPath(it, "text.text") || it.caption || "")}`;
    // efectos, transiciones y SFX: el tipo Y sus parametros (dos zooms distintos que se enciman son
    // una decision del director, no una copia: solo se quita el duplicado exacto)
    const o = {};
    Object.keys(it).sort().forEach(k => { if (!SIG_SKIP[k] && k[0] !== "_") o[k] = it[k]; });
    return `${track}:${JSON.stringify(o)}`;
  }
  const SIG_SKIP = { id: 1, src: 1, locked: 1, start: 1, end: 1, at: 1, enabled: 1 };
  function overlapFrac(track, a, b) {
    if (isEvent(track)) return Math.abs((+a.at || 0) - (+b.at || 0)) < 0.15 ? 1 : 0;
    const o = overlap(track, a, b), L = Math.min(+a.end - +a.start, +b.end - +b.start);
    return L > 1e-6 ? o / L : 0;
  }
  /** Quita (en sitio) los items de la IA (no bloqueados ni del usuario) que repiten el contenido de
      uno BLOQUEADO o creado por el usuario y se solapan con el mas de la mitad (el director a veces
      vuelve a generar un titulo que el usuario bloqueo). Lo que la IA propone entre si NO se toca
      (tampoco los efectos que re-emite con sufijo, fx_hookbg_s0b...): la propuesta debe quedar
      identica a la que guardo el servidor para que la sincronizacion con la musica IA sea
      automatica (esas copias son un defecto del director y se reportan alla). El Studio aplica este
      filtro ANTES de tomar nota del plan del servidor, asi que no cuenta como edicion.
      -> [{track, id, of}] quitados. */
  function dedupeFx(fx) {
    const removed = [];
    if (!fx) return removed;
    TRACKS.forEach(tk => {
      const arr = fx[tk] || [];
      const mine = arr.filter(k => k && (k.locked || k.src === "user"));
      if (!mine.length) return;
      const out = arr.filter(it => {
        if (!it || it.locked || it.src === "user") return true;
        const sig = contentSig(tk, it);
        const dup = mine.find(k => contentSig(tk, k) === sig && overlapFrac(tk, it, k) > 0.5);
        if (dup) { removed.push({ track: tk, id: it.id, of: dup.id }); return false; }
        return true;
      });
      if (out.length !== arr.length) fx[tk] = out;
    });
    return removed;
  }

  /** Efectos que el director RE-EMITE con sufijo sobre el mismo tramo (fx_hookbg_s0, fx_hookbg_s0b...:
      la pieza vieja queda por su grupo bloqueado y la coreografia agrega otra). Solo se quitan de la
      COPIA del plan que se manda a re-dirigir (asi no se acumulan en cada re-direccion); la propuesta
      que vuelve se adopta intacta. -> fx nuevo (copia superficial por pista) y los ids quitados. */
  function dropReemitted(fx) {
    const out = Object.assign({}, fx || {}), removed = [];
    const stem = id => { const m = /^(.*_s\d+)[a-z]?$/.exec(String(id || "")); return m ? m[1] : null; };
    const firsts = new Map();
    out.effects = ((fx && fx.effects) || []).filter(it => {
      if (!it || it.src === "user" || it.locked) return true;
      const k = stem(it.id); if (!k) return true;
      const key = `${k}|${it.kind}`, f = firsts.get(key);
      if (!f) { firsts.set(key, it); return true; }
      if (overlapFrac("effects", it, f) > 0.9) { removed.push(it.id); return false; }
      return true;
    });
    return { fx: out, removed };
  }

  // ------------------------------------------------------------------ geometria (espejo del render)
  const SAFE = { top: 0.09, bottom: 0.80, left: 0.12, right: 0.88 };  // simetrica en x (ctx.SAFE)
  const XC = 0.5, SYM_W = SAFE.right - SAFE.left;   // ctx.XC / ctx.SYM_W: centro y ancho max centrado
  const SNAP_X = 0.012;
  /** Revelacion del rotulo (graphics.draw_lower_third): la barra de acento crece, el panel se
      despliega y entran titulo y rol; con anim_out 'undraw' (default) se pliega al final.
      -> {bar, pan, te, se} en 0..1. */
  function lowerThirdReveal(it, lt, L) {
    const ain = it.anim_in || "draw", aout = it.anim_out || "undraw";
    const t0 = it.out_dur != null ? +it.out_dur : 0.4;
    const tout = Math.max(1e-3, L > 0 ? Math.min(t0, L * 0.5) : t0);
    const pout = Math.max(0, Math.min(1, (lt - (L - tout)) / tout));
    const lin = ain !== "none" ? lt : 10, pf = aout === "undraw" ? pout : 0, out = ease("in_cubic", pf);
    return {
      bar: ease("out_expo", lin / 0.32) * (1 - ease("in_cubic", (pf - 0.55) / 0.45)),
      pan: ease("out_expo", (lin - 0.12) / 0.5) * (1 - ease("in_out_cubic", pf / 0.7)),
      te: ease("out_cubic", (lin - 0.28) / 0.45) * (1 - out),
      se: ease("out_cubic", (lin - 0.42) / 0.45) * (1 - out),
    };
  }
  /** Colocacion horizontal del rotulo en px (graphics.draw_lower_third). Centrado: el centro es
      safe_cx(x*W, pw/2) (dentro de [SAFE.left, SAFE.right]; si no cabe, el centro del cuadro) y el
      ancho util del texto es SYM_W*W - 2*padx. Legado (sin align o 'left'): x0 = borde izquierdo
      (x o 0.07, hasta SAFE.right - 0.2) y el texto llega hasta SAFE.right. `pw` = panel (texto +
      2*padx), `barw` = barra vertical del legado. -> {centered, cx, x0, left, right, avail}. */
  function lowerThirdX(it, W, pw, barw, padx, minAvail) {
    const pad2 = 2 * (+padx || 0), mn = +minAvail || 0;
    if (ltCentered(it)) {
      const lo = SAFE.left * W + pw / 2, hi = SAFE.right * W - pw / 2, x = (it.x != null ? +it.x : XC) * W;
      const cx = lo > hi ? XC * W : Math.min(Math.max(x, lo), hi);
      return { centered: true, cx, x0: cx - pw / 2, left: cx - pw / 2, right: cx + pw / 2, avail: Math.max(mn, SYM_W * W - pad2) };
    }
    const x0 = Math.min(Math.max(it.x != null ? +it.x : 0.07, 0), SAFE.right - 0.2) * W, bw = +barw || 0;
    return { centered: false, cx: x0 + (bw + pw) / 2, x0, left: x0, right: x0 + bw + pw, avail: Math.max(mn, SAFE.right * W - x0 - bw - pad2) };
  }
  /** Iman al centro al arrastrar en el canvas: `nx` = x propuesto del ancla, `bcx` = centro de la
      caja VISIBLE en la misma posicion (fracciones del ancho). Si la caja queda a < tol del centro
      se corre el ancla lo que falta para que la caja quede exactamente centrada (un rotulo
      anclado a la izquierda, un texto alineado, una pila de redes...). -> [x, iman?]. */
  function snapCenterX(nx, bcx, tol) {
    const d = XC - +bcx;
    if (Math.abs(d) < (tol == null ? SNAP_X : tol)) return [Math.round((+nx + d) * 1e3) / 1e3, true];
    return [+nx, false];
  }
  /** graphics.draw_icon: el badge mide `size` de diametro y el icono 0.56*size; sin badge, size. */
  function iconGeom(sizePx, badge) {
    const b = badge && badge !== "none";
    return { badge: b ? sizePx : 0, icon: b ? sizePx * 0.56 : sizePx, box: sizePx };
  }
  /** graphics.icon_ink_dx: desfase x del centro de la tinta de un glifo respecto al centro de su
      caja de 24 (unidades de la caja). `cols` = alfa maximo por columna del glifo rasterizado en
      `cols.length` px (umbral 24 como el QA de simetria). Sin tinta: 0. El icono se dibuja
      corrido -dx*caja/24 para que su tinta quede centrada en x (phone, sparkles, globe). */
  function iconInkDx(cols) {
    const n = cols ? cols.length : 0;
    let a = -1, b = -1;
    for (let i = 0; i < n; i++) if (cols[i] > 24) { if (a < 0) a = i; b = i; }
    if (a < 0 || !n) return 0;
    return Math.round(((a + b + 1) / 2 * 24 / n - 12) * 1e3) / 1e3;
  }
  // ---- perfil: foto y logo de los enlaces (graphics.link_slot_geom / logo_look / logo_fit)
  const LINK_PHOTO = 0.72, LINK_LOGO = 0.62, LINK_BADGE = 0.46, LINK_PAD_R = 0.42, FOLLOW_BADGE = 0.44;
  const LOGO_PAD = 0.17, LOGO_RING = 0.8, LOGO_PLATE = "#FFFFFF", LOGO_PLATE_DARK = "#17171D";
  /** Hueco del icono de la pastilla link_below de alto h: kind "photo" | "logo" | "icon", con o sin
      `display` (dos lineas) -> {d, inset, gap, padR}. Foto y logo concentricos con la punta redonda. */
  function linkSlot(kind, h, twoLines) {
    if (kind === "photo" || kind === "logo") {
      const d = h * (kind === "photo" ? LINK_PHOTO : LINK_LOGO);
      return { d, inset: (h - d) / 2, gap: h * 0.2, padR: h * LINK_PAD_R };
    }
    const pad = h * (twoLines ? 0.4 : 0.5);
    return { d: h * (twoLines ? 0.36 : 0.42), inset: pad, gap: h * 0.2, padR: pad };
  }
  /** Como presentar un logo desde sus pixeles RGBA RECTOS (ImageData.data, w x h); mismo criterio
      que graphics.logo_look (que mide premultiplicado: aqui se premultiplica igual):
      bleed (opaco hasta el borde y casi cuadrado: llena la insignia) o contain sobre `plate` (color
      del borde si es opaco, blanca, u oscura si la tinta es casi blanca); box = caja de tinta. */
  function logoLook(px, w, h) {
    const pm = (i, c) => Math.floor((px[i * 4 + c] * px[i * 4 + 3] + 127) / 255);
    const ring = [];
    for (let x = 0; x < w; x++) ring.push(x);
    for (let x = 0; x < w; x++) ring.push((h - 1) * w + x);
    for (let y = 0; y < h; y++) ring.push(y * w);
    for (let y = 0; y < h; y++) ring.push(y * w + w - 1);
    const mean = ring.reduce((q, i) => q + px[i * 4 + 3], 0) / Math.max(1, ring.length);
    if (mean > 0.8 * 255) {
      const r = w / Math.max(1, h);
      if (r >= 0.85 && r <= 1 / 0.85) return { bleed: true, plate: null, box: [0, 0, w, h] };
      const med = c => { const v = ring.map(i => pm(i, c)).sort((a, b) => a - b), n = v.length; return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2; };
      return { bleed: false, plate: "#" + [0, 1, 2].map(c => Math.floor(med(c) + 0.5).toString(16).padStart(2, "0").toUpperCase()).join(""), box: [0, 0, w, h] };
    }
    // icono de app con esquinas redondeadas: centro de cada borde opaco y casi todo cubierto (= graphics)
    const qs = (n, f) => { const o = []; for (let k = Math.floor(n / 4); k < n - Math.floor(n / 4); k++) o.push(f(k)); return o; };
    const mids = [].concat(qs(w, x => x), qs(w, x => (h - 1) * w + x), qs(h, y => y * w), qs(h, y => y * w + w - 1));
    const midA = mids.reduce((q, i) => q + px[i * 4 + 3], 0) / Math.max(1, mids.length);
    let cov = 0;
    for (let i = 0; i < w * h; i++) if (px[i * 4 + 3] > 128) cov++;
    const rr = w / Math.max(1, h);
    if (rr >= 0.85 && rr <= 1 / 0.85 && midA > 0.9 * 255 && cov / Math.max(1, w * h) > 0.85)
      return { bleed: true, plate: null, box: [0, 0, w, h] };
    let x0 = w, y0 = h, x1 = -1, y1 = -1, lum = 0, n = 0;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = y * w + x, a = px[i * 4 + 3];
      if (a > 12) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
      if (a > 128) {
        const ch = c => Math.min(1, Math.max(0, pm(i, c) / Math.max(a, 1)));
        lum += 0.2126 * ch(0) + 0.7152 * ch(1) + 0.0722 * ch(2); n++;
      }
    }
    if (x1 < 0) return { bleed: false, plate: LOGO_PLATE, box: [0, 0, w, h] };
    return { bleed: false, plate: n && lum / n > 0.86 ? LOGO_PLATE_DARK : LOGO_PLATE, box: [x0, y0, x1 + 1, y1 + 1] };
  }
  /** (ancho, alto) del logo contain en una insignia de lado d: redondeada con aire LOGO_PAD por lado;
      redonda (`circle`) con la diagonal en LOGO_RING del diametro. */
  function logoFit(look, d, shape) {
    const b = look.box, bw = Math.max(1, b[2] - b[0]), bh = Math.max(1, b[3] - b[1]);
    const k = shape === "circle" ? d * LOGO_RING / Math.hypot(bw, bh) : d * (1 - 2 * LOGO_PAD) / Math.max(bw, bh);
    return [bw * k, bh * k];
  }
  /** text._kicker_split: etiqueta del kicker (explicita, primera linea, o el texto corto entero). */
  function kickerSplit(item, lines) {
    const lab = item && item.kicker;
    if (lab && String(lab).trim()) return { label: String(lab), rest: lines, only: false };
    if (lines.length >= 2) return { label: lines[0].text, rest: lines.slice(1), only: false };
    if (lines.length === 1) {
      const ws = String(lines[0].text || "").split(/\s+/).filter(Boolean);
      if (ws.length <= 4 && ws.join(" ").length <= 26) return { label: ws.join(" "), rest: [], only: true };
    }
    return { label: null, rest: lines, only: false };
  }
  /** graphics.center_of: posicion (0..1) de un item sin x/y anclado a la cara ([cx, cy, w, h]).
      null si no hay cara (el render no lo dibuja). */
  function anchorXY(item, face, dx, dy) {
    let x = item.x, y = item.y;
    const a = item.anchor;
    if ((x == null || y == null) && a && ["face", "face_left", "face_right", "face_top"].includes(a)) {
      if (!Array.isArray(face)) return null;
      const [fx, fy, fw, fh] = face.map(Number);
      const off = { face_left: [-0.5 * fw - 0.09, -0.25 * fh], face_right: [0.5 * fw + 0.09, -0.25 * fh], face_top: [0, -0.5 * fh - 0.05], face: [0, 0] }[a];
      const ax = Math.min(Math.max(fx + off[0], SAFE.left + 0.04), SAFE.right - 0.04);
      const ay = Math.min(Math.max(fy + off[1], SAFE.top + 0.03), SAFE.bottom - 0.03);
      if (x == null) x = ax;
      if (y == null) y = ay;
    }
    return [x == null ? (dx == null ? 0.5 : dx) : +x, y == null ? (dy == null ? 0.5 : dy) : +y];
  }

  // ------------------------------------------------------------------ PiP (graphics.media) automatico
  /* Contrato compartido con render.media_auto_place (doc 4.2b): un PiP con x/y vacios se coloca
     CENTRADO en x (simetria), lejos de la cara (en el pecho o arriba de la cabeza, achicandose),
     dentro de SAFE, arriba de los subtitulos y esquivando textos y graficos. */
  const PIP_MARGIN = 0.015, PIP_SCALES = [1.0, 0.85, 0.72, 0.6, 0.5, 0.4];
  /** Tarjeta del PiP en px (graphics.media_box / render._pip_card): ancho max size*W, alto max
      1.78x ese ancho; sin `aspect` toma la proporcion del medio (cw x ch). */
  function pipCard(size, aspect, cw, ch, W) {
    const maxw = Math.min(1, Math.max(0.05, +size || 0.36)) * W, maxh = maxw * 1.78;
    let asp = +aspect || 0;
    if (asp > 0) { asp = Math.min(3, Math.max(0.3, asp)); const w = Math.min(maxw, maxh * asp); return [w, w / asp]; }
    const iw = +cw > 0 ? +cw : 4, ih = +ch > 0 ? +ch : 5;
    const k = Math.min(maxw / iw, maxh / ih);
    return [iw * k, ih * k];
  }
  function ovl(a, b) { return Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1])); }
  /** Port exacto de render.media_auto_place -> [cx, cy, escala] (fracciones de la caja). Siempre
      centrado en x = XC: para librar la cara baja al pecho, sube arriba de la cabeza o se achica,
      nunca se va de lado. */
  function mediaAutoPlace(face, w, h, capTop, avoid) {
    const S = SAFE, m = PIP_MARGIN;
    const bottom = capTop ? Math.min(S.bottom, capTop) : S.bottom;
    const mid = XC;
    const av = (avoid || []).map(r => r.slice(0, 4).map(Number));
    const fit = (cx, cy, ww, hh) => {
      const lx = S.left + ww / 2 + m, hx = S.right - ww / 2 - m, ly = S.top + hh / 2 + m, hy = bottom - hh / 2 - m;
      return [lx > hx ? mid : Math.min(Math.max(cx, lx), hx), ly > hy ? (S.top + bottom) / 2 : Math.min(Math.max(cy, ly), hy)];
    };
    let zone = null, order;
    if (!face) order = () => [[mid, 0.0], [mid, 1.0]];                       // arriba, abajo (fit acota)
    else {
      const [fx, fy, fw, fh] = face.map(Number);
      zone = [fx - fw / 2 - 0.03, fy - fh / 2 - 0.05, fx + fw / 2 + 0.03, fy + fh / 2 + 0.03];
      order = (ww, hh) => [[mid, zone[3] + m + hh / 2], [mid, zone[1] - m - hh / 2]];   // pecho, arriba de la cabeza
    }
    let free = null, bestFace = null;
    const r4 = v => Math.round(v * 1e4) / 1e4;
    for (const s of PIP_SCALES) {
      const ww = w * s, hh = h * s;
      for (const [x0, y0] of order(ww, hh)) {
        const [cx, cy] = fit(x0, y0, ww, hh);
        const r = [cx - ww / 2, cy - hh / 2, cx + ww / 2, cy + hh / 2];
        const fo = zone ? ovl(r, zone) : 0, ao = av.reduce((q, a) => q + ovl(r, a), 0);
        if (fo <= 1e-9 && ao <= 1e-9) return [r4(cx), r4(cy), s];
        if (fo <= 1e-9 && (free == null || ao < free[0] - 1e-9)) free = [ao, cx, cy, s];
        if (bestFace == null || fo < bestFace[0] - 1e-9) bestFace = [fo, cx, cy, s];
      }
    }
    const p = free || bestFace;
    return [r4(p[1]), r4(p[2]), p[3]];
  }
  /** render._overlay_boxes: textos activos en [t0, t1) = franja de 0.16 en su y (o la del preset) a
      lo ancho de SAFE; graficos con x/y = cuadrado de `size` (sin medios ni barra). boxAspect = W/H. */
  function overlayBoxes(fx, t0, t1, boxAspect) {
    const S = SAFE, asp = boxAspect || 9 / 16, out = [];
    const on = it => it && it.enabled !== false && +it.end > t0 && +it.start < t1;
    ((fx && fx.text) || []).forEach(it => {
      if (!on(it)) return;
      let y = it.y != null ? +it.y : TEXT_PRESET_Y[it.preset];
      if (y == null || !isFinite(y)) y = 0.3;
      out.push([S.left, y - 0.08, S.right, y + 0.08]);
    });
    ((fx && fx.graphics) || []).forEach(it => {
      if (!on(it) || it.kind === "media" || it.kind === "progress" || it.x == null || it.y == null) return;
      const half = (it.size != null ? +it.size : 0.18) / 2, x = +it.x, y = +it.y;
      out.push([x - half, y - half * asp, x + half, y + half * asp]);
    });
    return out;
  }
  /** Cara mediana en [t0, t1) (tiempo original) con la pista de rostro de las tomas
      (shots[].face_track [[t, cx, cy, w, h], ...]); sin muestras, la cara de la toma en t0. */
  function faceMedian(shots, t0, t1) {
    const rows = [];
    (shots || []).forEach(sh => (sh && Array.isArray(sh.face_track) ? sh.face_track : []).forEach(r => {
      if (Array.isArray(r) && r.length >= 5 && +r[0] >= t0 - 1e-6 && +r[0] < t1 && r.slice(1, 5).every(v => isFinite(+v))) rows.push(r.slice(1, 5).map(Number));
    }));
    if (!rows.length) {
      const sh = (shots || []).find(x => x && t0 >= +x.start && t0 < +x.end);
      return sh && Array.isArray(sh.face) ? sh.face.map(Number) : null;
    }
    const med = k => { const v = rows.map(r => r[k]).sort((a, b) => a - b), n = v.length; return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2; };
    return [med(0), med(1), med(2), med(3)];
  }
  /** Posicion efectiva de un PiP (render.set_media_auto): con x/y vacios lo coloca con la cara de la
      toma (face = [cx, cy, w, h] o null); `cw, ch` = dims del medio. -> {x, y, size, auto}. */
  function pipPlace(fx, it, face, cw, ch, boxW, boxH) {
    const size = it.size != null ? +it.size : 0.36;
    if (it.x != null && it.y != null) return { x: +it.x, y: +it.y, size, auto: false };
    const cap = (fx && fx.captions) || {};
    const capOn = cap.enabled !== false && cap.preset !== "none";
    const capTop = capOn ? (cap.y != null ? +cap.y : 0.72) - CAPTION_HALF - 0.01 : null;
    const [pw, ph] = pipCard(size, it.aspect, cw, ch, boxW);
    const [cx, cy, s0] = mediaAutoPlace(face, pw * 1.06 / boxW, ph * 1.06 / boxH, capTop,
      overlayBoxes(fx, +it.start, +it.end, boxW / boxH));
    const s = it.x != null || it.y != null ? 1 : s0;
    return { x: it.x != null ? +it.x : cx, y: it.y != null ? +it.y : cy, size: Math.round(size * s * 1e4) / 1e4, auto: true };
  }

  // ------------------------------------------------------------------ transiciones lejos de una union
  /** Transiciones que MEZCLAN el plano anterior con el siguiente (espejo de transitions.MIXERS):
      lejos de una union se ven como una mezcla del plano consigo mismo y el render avisa. */
  const MIXERS = ["circle", "film_burn", "slide", "torn_paper"];
  /** true si una transicion de mezcla cae lejos (> 0.25 s en tiempo final) de toda union o cambio
      de toma: el render la dibuja sobre el mismo plano y avisa `sin_union`. */
  function transitionOffJoin(tl, plan, at, kind) {
    if (kind != null && !MIXERS.includes(kind)) return false;
    const tf = tl.remapClamped(+at);
    const cands = tl.offsets.slice(1);
    ((plan && plan.shots) || []).forEach(s => { const v = tl.remap(+s.start); if (v != null && v > 1e-6) cands.push(v); });
    if (!cands.length) return true;
    return Math.min(...cands.map(c => Math.abs(c - tf))) > 0.25 + 1 / 30;
  }

  // ------------------------------------------------------------------ idioma del plan que viaja
  /** Idiomas de subtitulos de la edicion IA (GET /settings.pro_langs; el servidor traduce con
      /translate). Se actualiza en sitio con setProLangs. */
  const PRO_LANGS = ["es", "en", "pt", "fr", "de", "it"];
  function setProLangs(list) {
    const l = (Array.isArray(list) ? list : []).map(String).filter(Boolean);
    if (!l.length) return PRO_LANGS;
    if (!l.includes("es")) l.unshift("es");
    PRO_LANGS.splice(0, PRO_LANGS.length, ...l);
    return PRO_LANGS;
  }
  /** Plan que se manda a /frame, /preview y /render con el idioma elegido: subtitulos traducidos
      (phrases_<lang>). Los textos de titulos/graficos ya van en el plan (se traducen en sitio). */
  function planForLang(plan, lang) {
    if (!plan || !lang || lang === "es") return plan;
    const ph = plan[`phrases_${lang}`];
    if (Array.isArray(ph) && ph.length) return Object.assign({}, plan, { phrases: ph });
    return plan;
  }
  /** Textos traducibles de fx (titulos, graficos, escenas): [{track, id, path, text}]. */
  function fxTexts(fx) {
    const out = [];
    ((fx && fx.text) || []).forEach(it => {
      if (!it || it.preset === "counter") return;
      if (hasLines(it)) it.lines.forEach((l, i) => { const t = typeof l === "string" ? l : l && l.text; if (t && String(t).trim()) out.push({ track: "text", id: it.id, path: `lines.${i}.text`, text: String(t) }); });
      else if (it.text && String(it.text).trim()) out.push({ track: "text", id: it.id, path: "text", text: String(it.text) });
    });
    ((fx && fx.graphics) || []).forEach(it => ["title", "subtitle", "label", "text"].forEach(k => { if (it && typeof it[k] === "string" && it[k].trim()) out.push({ track: "graphics", id: it.id, path: k, text: it[k] }); }));
    ((fx && fx.cutaways) || []).forEach(it => { const t = getPath(it, "text.text"); if (t && String(t).trim()) out.push({ track: "cutaways", id: it.id, path: "text.text", text: String(t) }); });
    return out;
  }
  /** Aplica traducciones (misma lista de fxTexts + `to`) en sitio. */
  function applyFxTexts(fx, list) {
    (list || []).forEach(x => {
      if (!x || x.to == null) return;
      const f = findItem(fx, x.id); if (!f) return;
      const ks = x.path.split(".");
      if (ks[0] === "lines") { const l = f.item.lines && f.item.lines[+ks[1]]; if (l && typeof l === "object") l.text = x.to; else if (f.item.lines) f.item.lines[+ks[1]] = x.to; if (f.item.text != null) f.item.text = titleText(f.item); }
      else setPath(f.item, x.path, x.to);
    });
    return fx;
  }

  /* Traduccion de titulos y graficos por ITEM (no por ruta): plan.studio.texts =
     {lang, src: {id: campos originales}, out: {id: campos tal como quedaron traducidos}, untranslated}.
     Los campos son el item completo de texto (`text` y `lines` enteros; titulo/subtitulo/etiqueta de
     un grafico; texto de una escena): corregir a mano una traduccion con menos lineas no pierde nada
     al volver al original, y re-traducir respeta lo editado a mano salvo que se pida. */
  const TEXT_TRACKS = ["text", "graphics", "cutaways"];
  /** Campos de texto de un item (instantanea completa) o null si no tiene texto traducible. */
  function textFields(track, it) {
    if (!it) return null;
    if (track === "text") {
      if (it.preset === "counter" || !titleText(it).trim()) return null;
      return { text: it.text == null ? null : it.text, lines: hasLines(it) ? clone(it.lines) : null };
    }
    if (track === "graphics") {
      const o = {}; let any = false;
      ["title", "subtitle", "label", "text"].forEach(k => { if (typeof it[k] === "string") { o[k] = it[k]; if (it[k].trim()) any = true; } });
      return any ? o : null;
    }
    if (track === "cutaways") { const t = getPath(it, "text.text"); return typeof t === "string" && t.trim() ? { "text.text": t } : null; }
    return null;
  }
  function setTextFields(it, f) {
    Object.keys(f || {}).forEach(k => {
      if (k === "lines") { if (f.lines) it.lines = clone(f.lines); else if (hasLines(it)) it.lines = null; }
      else setPath(it, k, f[k] == null ? undefined : f[k]);
    });
    return it;
  }
  function eachTextItem(fx, fn) {
    TEXT_TRACKS.forEach(tk => ((fx && fx[tk]) || []).forEach(it => { const f = it && it.id ? textFields(tk, it) : null; if (f) fn(tk, it, f); }));
  }
  /** Estado de la traduccion guardada (acepta el formato anterior: src = [{id, path, text}]). */
  function textState(st, fx) {
    if (!st || typeof st !== "object") return null;
    if (!Array.isArray(st.src)) return st;
    // formato anterior (por ruta): el original de cada item = el actual con sus rutas originales
    const src = {};
    st.src.forEach(s => {
      const f = findItem(fx, s && s.id); if (!f || src[s.id]) return;
      const tmp = clone(f.item);
      st.src.filter(x => x.id === s.id).forEach(x => applyFxTexts({ [f.track]: [tmp] }, [{ id: x.id, path: x.path, to: x.text }]));
      const tf = textFields(f.track, tmp); if (tf) src[s.id] = tf;
    });
    return { lang: st.lang, src, out: {}, untranslated: st.untranslated || [] };
  }
  /** Clasifica los textos contra la traduccion guardada: traducidos (igual a lo que se tradujo),
      editados a mano despues de traducir, y nuevos (nunca traducidos). -> {translated, edited, fresh, total} (ids). */
  function textStatus(fx, st0) {
    const st = textState(st0, fx), r = { translated: [], edited: [], fresh: [], total: 0 };
    eachTextItem(fx, (tk, it, cur) => {
      r.total++;
      const o = st && st.out && st.out[it.id];
      if (!o) r.fresh.push(it.id);
      else if (sameVal(cur, o)) r.translated.push(it.id);
      else r.edited.push(it.id);
    });
    return r;
  }
  /** Que traducir a `lang`: cada item parte de su ORIGINAL (nunca de otra traduccion). Lo ya
      traducido a ese idioma se omite; lo editado a mano tras traducir se respeta salvo `edited`;
      `all` fuerza todo. -> {items: [{track, id, orig}], list: [{track, id, path, text}], kept: [ids editados]} */
  function translationRequest(fx, st0, lang, opts) {
    const o = opts || {}, st = textState(st0, fx), items = [], kept = [];
    eachTextItem(fx, (track, it, cur) => {
      const out = st && st.out && st.out[it.id];
      const edited = !!(out && !sameVal(cur, out));
      if (edited && !o.edited && !o.all) { kept.push(it.id); return; }
      if (!o.all && !edited && out && st.lang === lang) return;       // ya esta en ese idioma
      const orig = st && st.src && st.src[it.id] ? clone(st.src[it.id]) : cur;
      items.push({ track, id: it.id, orig });
    });
    const list = [];
    items.forEach(x => {
      const tmp = setTextFields(clone(findItem(fx, x.id).item), x.orig);
      fxTexts({ [x.track]: [tmp] }).forEach(y => list.push(y));
    });
    return { items, list, kept };
  }
  /** Aplica la traduccion (textos en el orden de req.list) EN SITIO: cada item vuelve a su original
      y recibe su traduccion por linea. -> estado nuevo para plan.studio.texts. */
  function applyTranslation(fx, st0, req, texts, lang, untranslated) {
    const st = textState(st0, fx);
    req.items.forEach(x => { const f = findItem(fx, x.id); if (f) setTextFields(f.item, x.orig); });
    applyFxTexts(fx, req.list.map((x, i) => Object.assign({}, x, { to: texts[i] })));
    const src = Object.assign({}, st && st.src), out = Object.assign({}, st && st.out);
    req.items.forEach(x => {
      const f = findItem(fx, x.id); if (!f) return;
      src[x.id] = x.orig; out[x.id] = textFields(x.track, f.item);
    });
    return { lang, src, out, untranslated: Array.isArray(untranslated) ? untranslated : [] };
  }
  /** "Volver a los textos originales": cada item traducido recupera su texto original COMPLETO
      (todas sus lineas, aunque se hayan editado). -> numero de items restaurados. */
  function restoreOriginalTexts(fx, st0) {
    const st = textState(st0, fx); let n = 0;
    if (!st || !st.src) return 0;
    Object.keys(st.src).forEach(id => { const f = findItem(fx, id); if (f) { setTextFields(f.item, st.src[id]); n++; } });
    return n;
  }

  // ------------------------------------------------------------------ musica: que la cambia
  /** Firma de lo que cambia el ARCHIVO de musica (no el volumen de mezcla): si cambia, la musica
      generada queda desactualizada. keeps = tramos conservados (la duracion final la cambia). */
  const MIX_ONLY = { gain_db: 1, duck_db: 1, voice_carve_db: 1, enabled: 1 };
  function musicSig(music, keeps) {
    const m = music || {}, o = {};
    Object.keys(m).sort().forEach(k => { if (!MIX_ONLY[k] && k[0] !== "_") o[k] = m[k]; });
    return JSON.stringify([o, (keeps || []).map(k => [round3(k[0]), round3(k[1])])]);
  }

  // ------------------------------------------------------------------ medios propios
  /** Parametro de medio propio (type "media": {sid, type, name, w, h, dur}) de un kind, o null. Los
      medios auxiliares (`aux`: el logo de una tarjeta o de un CTA) no hacen del kind uno "de medio":
      soltar una foto nunca crea una tarjeta ni reemplaza un logo. */
  function mediaParam(schema, track, kind) {
    return kindParams(schema, track, kind).find(p => p.type === "media" && !p.aux) || null;
  }
  function mediaKinds(schema, track) { return kinds(schema, track).filter(k => mediaParam(schema, track, k)); }
  /** Kind con el que se agrega un medio propio en una pista: graphics -> PiP (`media`), escenas ->
      foto (`photo`); si el esquema no los trae, el primero que acepte medios de ese tipo. */
  function mediaKindFor(schema, track, type) {
    const pref = { graphics: ["media"], cutaways: ["photo", "self_broll"] }[track] || [];
    const ok = k => { const p = mediaParam(schema, track, k); return p && (!type || !p.accept || p.accept.includes(type)); };
    return pref.find(ok) || mediaKinds(schema, track).find(k => k !== "cta" && ok(k)) || null;
  }

  // ------------------------------------------------------------------ avisos del render en lenguaje humano
  /* Codigos de `skipped` y `warnings` del render (stats del export y de la vista previa; en el
     export llegan como "omitido: <codigo>"). -> {msg, id, level}; id = item del plan si aplica. */
  const SKIP_MSG = {
    nombre_desconocido: "el sonido no existe en el catálogo",
    dato_invalido: "tiene un dato inválido",
    ilegible: "el archivo no se pudo leer (quedó fuera del export)",
    no_encontrado: "no se encontró el archivo",
    sin_medio: "no tiene archivo elegido",
    sin_union: "cae lejos de un corte: se ve como una mezcla sobre el mismo plano",
    sin_tiempo: "no tiene momento",
    fuera_del_video: "cae fuera del video",
    calidad_baja: "el recorte de la persona salió con poca calidad",
  };
  const TRACK_ES = { sfx: "SFX", media: "Medio propio", avatar: "Foto de perfil", transitions: "Transición", text: "Título",
    effects: "Efecto", graphics: "Gráfico", cutaways: "Escena", music: "Música", matte: "Recorte de la persona",
    audio: "Audio", captions: "Subtítulos", logo: "Logo" };
  function humanSkip(code, plan) {
    let c = String(code == null ? "" : code).trim(), level = "warn";
    if (/^omitido:\s*/i.test(c)) { c = c.replace(/^omitido:\s*/i, ""); level = "skip"; }
    const parts = c.split(":");
    const tk = parts[0], mid = parts.length >= 3 ? parts.slice(1, -1).join(":") : parts[1], why = parts.length >= 3 ? parts[parts.length - 1] : null;
    const f = mid && plan && plan.fx ? findItem(plan.fx, mid) : null;
    const name = f ? itemLabel(null, f.track, f.item) : null;
    const what = TRACK_ES[tk] || tk;
    let msg;
    if (tk === "matte" && /^\d/.test(mid || "")) msg = `${what} (${mid.replace("-", " a ")} s del video final): ${SKIP_MSG[why] || why || "aviso"}`;
    else if (why && SKIP_MSG[why]) msg = `${what}${name ? ` "${name}"` : !f && tk === "music" ? ` (${mid})` : ""}: ${SKIP_MSG[why]}`;
    else if (why == null && mid && ["error", "modulo", "archivo", "draw_captions", "MatteProvider"].includes(mid))
      msg = `${what}: ${mid === "archivo" ? "no se encontró el archivo" : mid === "error" ? "falló (se omitió)" : "no está disponible en este equipo"}`;
    else if (why == null && mid) msg = `${what}: tipo "${mid}" no disponible en este motor (se omitió)`;
    else msg = c;
    return { code: c, msg, id: f ? f.item.id : null, track: f ? f.track : null, level };
  }

  // ------------------------------------------------------------------ limpieza de tomas
  /* Cortes de limpieza: registros de plan.cuts con src "takes" (tomas repetidas, arranques en
     falso, correcciones...), APARTE de los silencios; pueden solaparse con ellos y keepsFromCuts
     funde los habilitados (igual que timeline.keeps_from_cuts). Todo en segundos de la FUENTE.
     `enabled` es reversible; `user: true` = lo decidio el usuario (re-detectar lo respeta);
     `edges: true` = el usuario movio sus bordes; `guard: true` = el motor lo dejo apagado para no
     perder una palabra (ningun nivel lo enciende solo). */
  const TAKE_KINDS = ["retoma", "arranque_fallido", "autocorreccion", "tartamudeo", "error", "digresion"];
  const TAKE_LABEL = { retoma: "Toma repetida", arranque_fallido: "Arranque en falso", autocorreccion: "Corrección",
    tartamudeo: "Tartamudeo", error: "Comentario fuera de toma", digresion: "Fuera de tema", silencio: "Silencio", manual: "Manual", ai: "Recorte IA" };
  const TAKE_PLURAL = { retoma: "Tomas repetidas", arranque_fallido: "Arranques en falso", autocorreccion: "Correcciones",
    tartamudeo: "Tartamudeos", error: "Comentarios fuera de toma", digresion: "Fuera de tema", silencio: "Silencios", manual: "Cortes manuales", ai: "Recortes IA" };
  const TAKE_CHIP = { retoma: "Tomas repetidas", arranque_fallido: "Arranques", autocorreccion: "Correcciones",
    tartamudeo: "Tartamudeos", error: "Comentarios", digresion: "Fuera de tema" };
  /** Niveles de limpieza: que seguridades quedan cortadas por defecto (lo demas, sugerencia). */
  const TAKE_LEVELS = { suave: ["alta"], normal: ["alta", "media"], a_fondo: ["alta", "media", "baja"] };
  const LEVEL_LABEL = { suave: "Suave", normal: "Normal", a_fondo: "A fondo" };
  const LONG_VIDEO_S = 180;             // s: "video largo" (formato 16:9 por defecto si es horizontal)
  const FORMATS = ["9:16", "1:1", "4:5", "16:9", "original"];

  const isTakeCut = c => !!c && typeof c === "object" && c.src === "takes";
  const cutOn = c => !!c && c.enabled !== false;
  /** Categoria de un corte: tipo de toma, "manual", "ai" (recorte del director) o "silencio". */
  function cutKind(c) {
    if (isTakeCut(c)) return TAKE_KINDS.includes(c.kind) ? c.kind : "retoma";
    if (c && c.manual) return "manual";
    if (c && c.src === "ai") return "ai";
    return "silencio";
  }
  function cutPrio(c) { return isTakeCut(c) ? 3 : c && c.manual ? 2 : c && c.src === "ai" ? 1 : 0; }
  /** Seguridad de una toma ("alta" | "media" | "baja"); sin `level`, por su `conf`. */
  function takeLevel(c) {
    if (c && ["alta", "media", "baja"].includes(c.level)) return c.level;
    const f = c ? +c.conf : NaN;
    return f >= 0.8 ? "alta" : f >= 0.5 ? "media" : "baja";
  }
  /** Rayas del medidor de seguridad (1..4). */
  function confBars(c) {
    const f = c ? +c.conf : NaN;
    if (isFinite(f)) return Math.max(1, Math.min(4, Math.round(f * 4)));
    return { alta: 4, media: 3, baja: 1 }[takeLevel(c)];
  }

  /** Palabras del plan en orden: [{text, start, end}] (sin vacias; un `end` roto = `start`). */
  function planWords(phrases) {
    const out = [];
    (phrases || []).forEach(p => (p || []).forEach(w => {
      if (!w || typeof w !== "object") return;
      const t = String(w.text == null ? "" : w.text).trim(), s = +w.start, e = +w.end;
      if (!t || !isFinite(s)) return;
      out.push({ text: t, start: s, end: isFinite(e) && e >= s ? e : s });
    }));
    return out.sort((a, b) => a.start - b.start);
  }

  /** A que corte pertenece cada palabra, por su punto medio (start <= m < end).
      owner[i] = indice en `cuts` o -1; si varios la cubren gana toma > manual > IA > silencio y,
      a igualdad, el habilitado y luego el que empieza antes. removed[i] = 1 si algun corte
      HABILITADO la cubre (no sale en el video). Un silencio solo cuenta si cubre la palabra entera
      (+-SIL_TOL): una palabra dicha y estirada sobre la pausa sigue en el video. O((n + m) log). */
  const SIL_TOL = 0.05;
  function wordsInCuts(words, cuts) {
    const ws = words || [], cs = cuts || [], n = ws.length;
    const owner = new Array(n).fill(-1), removed = new Array(n).fill(0);
    const idx = [];
    cs.forEach((c, i) => { if (c && isFinite(+c.start) && isFinite(+c.end) && +c.end > +c.start) idx.push(i); });
    idx.sort((a, b) => +cs[a].start - +cs[b].start || a - b);
    const mid = ws.map(w => (w.start + w.end) / 2);
    const order = ws.map((_, i) => i).sort((a, b) => mid[a] - mid[b] || a - b);
    let k = 0, act = [];
    order.forEach(i => {
      const m = mid[i];
      while (k < idx.length && +cs[idx[k]].start <= m) act.push(idx[k++]);
      if (act.length) act = act.filter(j => +cs[j].end > m);
      let best = -1;
      act.forEach(j => {
        const c = cs[j];
        // un SILENCIO que solo tapa lo estirado de una palabra dicha no la quita ('café.' con la
        // pausa siguiente dentro): el render la conserva (map_words) y aqui no sale tachada
        if (cutPrio(c) === 0 && !(+c.start <= ws[i].start + SIL_TOL && +c.end >= ws[i].end - SIL_TOL)) return;
        if (cutOn(c)) removed[i] = 1;
        if (best < 0) { best = j; return; }
        const b = cs[best], pc = cutPrio(c), pb = cutPrio(b);
        if (pc > pb || (pc === pb && cutOn(c) && !cutOn(b))) best = j;
      });
      owner[i] = best;
    });
    return { owner, removed };
  }
  /** Primera y ultima palabra de cada corte: {ci: [w0, w1]} (inclusivo). */
  function cutSpans(owner) {
    const sp = {};
    (owner || []).forEach((ci, i) => { if (ci < 0) return; const s = sp[ci]; if (!s) sp[ci] = [i, i]; else s[1] = i; });
    return sp;
  }
  function lowerBound(arr, x) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < x) lo = m + 1; else hi = m; } return lo; }
  /** Cortes sin palabras (silencios, recortes entre palabras): donde van en el texto.
      -> {indice de palabra: [ci, ...]} = antes de esa palabra (n = al final). */
  function wordlessAt(words, cuts, spans) {
    const ws = words || [], mids = ws.map(w => (w.start + w.end) / 2), out = {};
    (cuts || []).forEach((c, ci) => {
      if (!c || (spans && spans[ci]) || !(+c.end > +c.start)) return;
      const at = lowerBound(mids, (+c.start + +c.end) / 2);
      (out[at] = out[at] || []).push(ci);
    });
    Object.keys(out).forEach(k => out[k].sort((a, b) => +cuts[a].start - +cuts[b].start || a - b));
    return out;
  }

  /** Grupos de tomas (mismo `group`): {groups: {g: {cuts: [ci por inicio], keep_ref}},
      ord: {ci: {group, i, n}}}. n cuenta la toma elegida (keep_ref): "1 de 3" = dos cortadas y
      una que se queda. Una toma sin grupo es su propio grupo. */
  function takeGroups(cuts) {
    const cs = cuts || [], groups = {}, ord = {};
    cs.forEach((c, i) => {
      if (!isTakeCut(c)) return;
      const g = c.group != null && c.group !== "" ? String(c.group) : `_${i}`;
      const G = groups[g] = groups[g] || { cuts: [], keep_ref: null };
      G.cuts.push(i);
      if (!G.keep_ref && c.keep_ref && isFinite(+c.keep_ref.start)) G.keep_ref = { start: +c.keep_ref.start, end: +c.keep_ref.end };
    });
    Object.keys(groups).forEach(g => {
      const G = groups[g];
      G.cuts.sort((a, b) => +cs[a].start - +cs[b].start || a - b);
      const n = G.cuts.length + (G.keep_ref ? 1 : 0);
      G.cuts.forEach((ci, k) => { ord[ci] = { group: g, i: k + 1, n }; });
    });
    return { groups, ord };
  }
  /** Etiqueta de un corte en el guion: "Toma repetida · 1 de 3", las siguientes "2 de 3". */
  function cutTag(c, o, full) {
    const k = cutKind(c), base = TAKE_LABEL[k] || "Corte";
    if (k === "retoma" && o && o.n > 1) return o.i === 1 || full ? `${base} · ${o.i} de ${o.n}` : `${o.i} de ${o.n}`;
    return base;
  }
  /** Donde va la marca "Toma elegida": {indice de palabra: grupo} (primera palabra de keep_ref
      que no esta en el grupo), solo en grupos de tomas repetidas con algun corte habilitado. */
  function keptMarks(words, cuts, groups, owner) {
    const ws = words || [], starts = ws.map(w => w.start), out = {};
    Object.keys(groups || {}).forEach(g => {
      const G = groups[g];
      if (!G.keep_ref || !G.cuts.some(ci => cutKind(cuts[ci]) === "retoma" && cutOn(cuts[ci]))) return;
      const mine = new Set(G.cuts);
      let i = lowerBound(starts, G.keep_ref.start - 0.08);
      while (i < ws.length && owner && owner[i] >= 0 && mine.has(owner[i])) i++;
      if (i < ws.length && ws[i].start <= G.keep_ref.end + 0.05) out[i] = g;
    });
    return out;
  }

  function unionLen(iv) {
    const a = iv.filter(x => x[1] > x[0]).sort((x, y) => x[0] - y[0]);
    let tot = 0, s = -Infinity, e = -Infinity;
    a.forEach(([x, y]) => { if (x > e) { if (e > s) tot += e - s; s = x; e = y; } else if (y > e) e = y; });
    if (e > s) tot += e - s;
    return tot;
  }
  /** Resumen de las tomas (forma de plan.takes.stats): n, habilitadas, sugerencias (apagadas sin
      tocar) y segundos que quita cada tipo (union de sus cortes habilitados). */
  function takeStats(cuts) {
    const by = {}, all = []; let n = 0, en = 0, sug = 0;
    (cuts || []).forEach(c => {
      if (!isTakeCut(c)) return;
      n++;
      const k = cutKind(c), b = by[k] = by[k] || { n: 0, enabled: 0, iv: [] };
      b.n++;
      if (cutOn(c)) { en++; b.enabled++; b.iv.push([+c.start, +c.end]); all.push([+c.start, +c.end]); }
      else if (!c.user) sug++;
    });
    const out = { n, enabled: en, suggested: sug, removed_s: round3(unionLen(all)), by_kind: {} };
    Object.keys(by).forEach(k => { out.by_kind[k] = { n: by[k].n, enabled: by[k].enabled, s: round3(unionLen(by[k].iv)) }; });
    return out;
  }
  /** Tiempo quitado por categoria sin contar dos veces lo solapado: cada instante cortado es del
      corte de mayor prioridad que lo cubre (toma > manual > IA > silencio).
      -> {total, cats: {categoria: s}} (segundos de la fuente, antes de la reticula de cuadros). */
  function removedByCategory(cuts, duration) {
    const dur = +duration > 0 ? +duration : Infinity, ev = [];
    (cuts || []).forEach(c => {
      if (!cutOn(c)) return;
      const s = Math.max(0, +c.start), e = Math.min(dur, +c.end);
      if (e > s) ev.push([s, e, c]);
    });
    ev.sort((a, b) => a[0] - b[0]);
    const pts = [...new Set(ev.flatMap(x => [x[0], x[1]]))].sort((a, b) => a - b);
    const cats = {}; let total = 0, k = 0, act = [];
    for (let j = 0; j + 1 < pts.length; j++) {
      const a = pts[j], b = pts[j + 1];
      while (k < ev.length && ev[k][0] <= a) act.push(ev[k++]);
      act = act.filter(x => x[1] > a);
      if (!act.length) continue;
      let best = act[0];
      act.forEach(x => { if (cutPrio(x[2]) > cutPrio(best[2])) best = x; });
      const kd = cutKind(best[2]);
      cats[kd] = (cats[kd] || 0) + (b - a); total += b - a;
    }
    Object.keys(cats).forEach(kd => { cats[kd] = round3(cats[kd]); });
    return { total: round3(total), cats };
  }
  /** Cuantos cortes hay de cada categoria (chips del guion). */
  function kindCounts(cuts) {
    const out = {};
    (cuts || []).forEach(c => { if (c && +c.end > +c.start) { const k = cutKind(c); out[k] = (out[k] || 0) + 1; } });
    return out;
  }

  /** La toma queda cortada en este nivel (solo para las que el usuario no toco). Una apagada por
      seguridad (guard: dejaria una palabra en un fragmento que la linea de tiempo absorbe) no la
      enciende ningun nivel: solo el usuario. */
  function levelOn(c, level) { return !(c && c.guard) && (TAKE_LEVELS[level] || TAKE_LEVELS.normal).includes(takeLevel(c)); }
  /** Cortes con las tomas como las deja el nivel (copias; las de user:true no cambian). */
  function applyLevel(cuts, level) {
    return (cuts || []).map(c => (isTakeCut(c) && !c.user ? Object.assign({}, c, { enabled: levelOn(c, level) }) : c));
  }
  /** Duracion final si se aplicara el nivel (vista previa de "queda m:ss"). */
  function levelFinal(plan, level, fps) {
    return timelineFromPlan(Object.assign({}, plan, { cuts: applyLevel((plan && plan.cuts) || [], level) }), fps).finalDuration;
  }
  /** El usuario movio los bordes de un corte (arrastre en el timeline o Inicio/Fin del
      inspector): una toma queda user:true y edges:true (re-detectar y los niveles la respetan). */
  function markEdges(c) {
    if (isTakeCut(c)) { c.user = true; c.edges = true; }
    return c;
  }
  /** Tomas que devuelve el motor (POST /takes) sobre los cortes locales: reemplazan las src "takes"
      salvo las que el usuario toco (user o edges: esas ganan, las devuelva o no el motor); los
      silencios, manuales y recortes de la IA quedan. -> cortes nuevos ordenados por inicio. */
  function adoptTakeCuts(local, fromServer) {
    const mine = new Map((local || []).filter(c => isTakeCut(c) && (c.user || c.edges) && c.id).map(c => [c.id, c]));
    const got = (fromServer || []).filter(isTakeCut).map(c => mine.get(c.id) || c);
    const ids = new Set(got.map(c => c.id));
    mine.forEach((c, id) => { if (!ids.has(id)) got.push(c); });
    return (local || []).filter(c => !isTakeCut(c)).concat(got).sort((a, b) => +a.start - +b.start);
  }
  /** Motivo de una toma con la hora de la toma que se queda en el reloj que se muestra: el motor
      la escribe en tiempo de la FUENTE ("Lo repites mejor en 16:05"); el guion de Limpieza cuenta
      en tiempo FINAL (map = tl.remapClamped), donde esa toma puede estar minutos antes. */
  function takeReason(c, map, fmt) {
    const r = c && c.reason != null ? String(c.reason).trim() : "";
    const k = c && c.keep_ref ? +c.keep_ref.start : NaN;
    if (!r || !isTakeCut(c) || !isFinite(k) || typeof map !== "function") return r;
    const t = (fmt || fmtClock)(map(k));
    return r.replace(/\ben \d+:\d{2}(?::\d{2})?(?=$|[\s.,;)])/, `en ${t}`);
  }
  /** Restaura (false) o vuelve a cortar (true) en su lugar; una toma queda user:true. */
  function setCutEnabled(c, on) {
    if (!c) return false;
    c.enabled = !!on;
    if (isTakeCut(c)) c.user = true;
    return c.enabled;
  }
  function toggleCut(c) { return setCutEnabled(c, !cutOn(c)); }
  /** Varios cortes a la vez ("Restaurar todo…"). -> cuantos cambiaron. */
  function setCutsEnabled(cuts, idxs, on) {
    let n = 0;
    (idxs || []).forEach(i => { const c = (cuts || [])[i]; if (c && cutOn(c) !== !!on) { setCutEnabled(c, on); n++; } });
    return n;
  }
  const inKind = (c, kind) => (kind === "silencio" ? !!c && !isTakeCut(c) && !c.manual && c.src !== "ai" : isTakeCut(c) && cutKind(c) === kind);
  /** La categoria esta encendida: algun corte suyo habilitado. */
  function kindOn(cuts, kind) { return (cuts || []).some(c => inKind(c, kind) && cutOn(c)); }
  /** Enciende o apaga una categoria completa. Apagar = todos restaurados (user:true). Encender =
      vuelven a lo que decide el nivel (sin user, salvo los de bordes movidos); si el nivel no corta
      ninguno, todos los que no estan apagados por seguridad (guard) o, si solo quedan esos, todos.
      Silencios: todos dentro o fuera. -> cuantos cambiaron. */
  function setKindEnabled(cuts, kind, on, level) {
    const cs = (cuts || []).filter(c => inKind(c, kind));
    const want = c => (!on ? false : kind === "silencio" ? true : levelOn(c, level));
    const any = on && kind !== "silencio" && cs.some(want);
    const safe = cs.some(c => !c.guard);
    let n = 0;
    cs.forEach(c => {
      const v = on && kind !== "silencio" && !any ? !(safe && c.guard) : want(c);
      if (cutOn(c) !== v) n++;
      c.enabled = v;
      if (isTakeCut(c)) { if (!on || !any) c.user = true; else if (!c.edges) delete c.user; }
    });
    return n;
  }
  /** Cortes que se recorren en la revision (por inicio): los del filtro (tipo de toma) o, con
      "todos", los de limpieza: tomas (+ silencios si se muestran). Los cortes manuales y los
      recortes de la IA se ven en el guion pero son de Edicion: ni cuentan en "Todos" ni los
      toca "Restaurar todo". */
  function reviewItems(cuts, filter, sil) {
    const cs = cuts || [], out = [];
    cs.forEach((c, i) => {
      if (!c || !(+c.end > +c.start)) return;
      const k = cutKind(c);
      if (filter && filter !== "todos") { if (k !== filter) return; }
      else if (k === "silencio" ? !sil : !isTakeCut(c)) return;
      out.push(i);
    });
    return out.sort((a, b) => +cs[a].start - +cs[b].start || a - b);
  }

  /** Secciones utiles para el guion: ordenadas, la primera desde 0 si empieza en los primeros
      30 s (lo de antes es su introduccion) y sin capitulos pegados: una que empieza a menos de
      `minGap` s de la anterior se descarta (la IA a veces devuelve dos casi en el mismo punto). */
  function cleanSections(sections, o) {
    const minGap = (o && o.minGap) || 20, out = [];
    (sections || []).filter(s => s && isFinite(+s.start))
      .map(s => ({ start: Math.max(0, +s.start), title: String(s.title == null ? "" : s.title).trim() }))
      .sort((a, b) => a.start - b.start)
      .forEach(s => {
        if (!out.length) out.push(s.start < 30 ? Object.assign(s, { start: 0 }) : s);
        else if (s.start - out[out.length - 1].start >= minGap) out.push(s);
      });
    return out;
  }
  /** Parrafos del guion: [{w0, w1 (exclusivo), sec}] sobre `words`. Nuevo parrafo en fin de
      oracion (. ? ! …) con >= minWords, en una pausa >= gap s, al llegar a maxWords o al empezar
      una seccion; nunca parte un corte de toma. sec = indice en `sections` (ordenadas) o -1. Una
      seccion (tiempos de la IA) abre parrafo al inicio de su oracion: hasta 3 palabras atras o,
      si no hay, al terminar la oracion en curso (hasta 6 palabras adelante, sin partir una toma). */
  function scriptParagraphs(words, owner, cuts, sections, o) {
    const opt = o || {}, minW = opt.minWords || 24, maxW = opt.maxWords || 90, gapS = opt.gap || 2.0;
    const ws = words || [], n = ws.length, secs = (sections || []).map(s => +s.start);
    if (!n) return [];
    const secAt = t => { let k = -1; secs.forEach((s, j) => { if (s <= t + 1e-6) k = j; }); return k; };
    const sameTake = i => owner && owner[i] >= 0 && owner[i] === owner[i - 1] && isTakeCut(cuts[owner[i]]);
    const endS = t => /[.?!…]["»”)]?$/.test(t);
    const out = [];
    let cur = { w0: 0, sec: secAt(ws[0].start) }, ns = cur.sec + 1, pend = null;
    for (let i = 1; i < n; i++) {
      const prev = ws[i - 1], w = ws[i], len = i - cur.w0;
      let brk = -1;
      while (ns < secs.length && secs[ns] <= w.start + 1e-6) {
        let back = -1;
        for (let q = i; q >= Math.max(cur.w0 + 1, i - 3); q--) if (endS(ws[q - 1].text)) { if (!sameTake(q)) back = q; break; }
        pend = { sec: ns, until: i + 6, back };
        ns++;
      }
      if (pend) {
        if (pend.back >= 0) brk = pend.back;
        else if ((endS(prev.text) || i >= pend.until) && !sameTake(i)) brk = i;
      } else if (!sameTake(i) && (len >= maxW || (endS(prev.text) && len >= minW) || (w.start - prev.end >= gapS && len >= 8))) brk = i;
      if (brk >= 0) { cur.w1 = brk; out.push(cur); cur = { w0: brk, sec: pend ? pend.sec : cur.sec }; pend = null; }
    }
    cur.w1 = n; out.push(cur);
    return out;
  }

  /** "Escuchar como queda": de `pad` s antes a `pad` s despues del corte en tiempo FINAL (los
      cortes habilitados se saltan). -> [inicio, fin] en tiempo original. */
  function listenRange(tl, c, pad) {
    const p = pad == null ? 2 : +pad;
    const a = Math.max(0, tl.remapClamped(+c.start) - p), b = Math.min(tl.finalDuration, tl.remapClamped(+c.end) + p);
    return [tl.inverse(a), tl.inverse(b)];
  }
  /** Duracion legible: "8.4 s" (menos de un minuto) o "m:ss". */
  function fmtLen(s) {
    const v = Math.max(0, +s || 0);
    return v < 59.95 ? `${v.toFixed(1)} s` : fmtClock(v);
  }

  /** Es UN solo video horizontal de mas de 3 min (un video de YouTube, no un short). `dims`
      = {w, h} de display (el reproductor ya aplica la rotacion); si no, plan.dims. */
  function isLongLandscape(plan, dims) {
    if (!plan) return false;
    const d = dims && +dims.w > 0 && +dims.h > 0 ? [+dims.w, +dims.h] : Array.isArray(plan.dims) ? [+plan.dims[0], +plan.dims[1]] : null;
    if (!d || !(d[0] > d[1]) || !(+plan.duration > LONG_VIDEO_S)) return false;
    return new Set((plan.shots || []).map(s => s && s.src).filter(Boolean)).size <= 1;
  }
  /** Formato de salida del proyecto: el que eligio el usuario (plan.studio.fmt) o, por defecto,
      16:9 para un solo video horizontal largo y 9:16 para lo demas. */
  function defaultFormat(plan, dims) {
    const st = plan && plan.studio && plan.studio.fmt;
    if (FORMATS.includes(st)) return st;
    return isLongLandscape(plan, dims) ? "16:9" : "9:16";
  }
  /** Formato al ABRIR el proyecto -> {fmt, legacy}. El elegido (plan.studio.fmt, que el analisis
      anota al nacer el proyecto) manda. Sin el, un plan de antes del formato por proyecto (tampoco
      tiene plan.takes) se edito y exporto en 9:16: se queda en 9:16 (legacy: el Studio lo anota,
      nunca cambia de formato ni re-analiza solo por abrirlo). Uno nuevo: defaultFormat. */
  function projectFormat(plan, dims) {
    const st = plan && plan.studio && plan.studio.fmt;
    if (FORMATS.includes(st)) return { fmt: st, legacy: false };
    if (!plan || !plan.takes) return { fmt: "9:16", legacy: !!plan };
    return { fmt: defaultFormat(plan, dims), legacy: false };
  }
  /** Los shorts son VERTICALES (Reels, TikTok, Shorts) y el motor los corta del video final: con
      una salida horizontal (16:9, u "original" de una fuente horizontal) no se piden. */
  function shortsFor(fmt, dims) {
    if (fmt === "16:9") return false;
    if (fmt === "original" && dims && +dims.w > +dims.h) return false;
    return true;
  }

  // ------------------------------------------------------------------ armado con IA (montaje)
  /* "Armar con IA": el servidor (POST /montage) escucha cada clip de la grabacion, elige la mejor
     toma de cada parte del guion y devuelve plan.montage {order, script, slots (cada parte con su
     toma elegida y sus alternativas), timeline (mismos campos que los items de /compose + slot,
     cand y join), discarded, uncovered, extra}. Aqui: el orden de grabacion, la linea de tiempo del
     ensamblado a partir del montaje, cambiar una toma ("Usar esta toma") re-empacando lo que sigue,
     el panel "Tomas" y el cuerpo de /compose. */
  const CONF_ALTA = 0.8, CONF_MEDIA = 0.6;
  const CONF_LABEL = { alta: "Alta", media: "Media", baja: "Baja" };
  const DISCARD_LABEL = { retoma: "Retoma", blooper: "Blooper", fuera_de_guion: "Fuera del guion", incompleta: "Incompleta", sin_audio: "Sin audio" };
  const JOIN_KINDS = ["section", "line", "splice"];
  // aviso del servidor al volver un montaje a las tomas sin error (jobstore: "...Tu montaje sigue aquí.")
  const MONTAGE_BACK_RE = /sigue aqu[ií]/i;

  const mClipLen = c => (c && c.type === "image" ? +c.dur || 0 : (+c.out || 0) - (+c.in || 0));
  const mClipEnd = c => (+c.start || 0) + mClipLen(c);
  /** Fecha de grabacion ISO 8601 -> ms (NaN si no hay). Acepta "-0600" y "AAAA-MM-DD hh:mm:ss"
      (el WebView de escritorio no siempre los entiende). */
  function createdMs(s) {
    if (s == null || s === "") return NaN;
    if (typeof s === "number") return s > 1e11 ? s : s * 1000;
    const t = String(s).trim().replace(/^(\d{4}-\d{2}-\d{2}) (\d)/, "$1T$2").replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
    return Date.parse(t);
  }
  /** Orden natural de nombres de archivo ("IMG_9" < "IMG_10"), sin distinguir mayusculas. */
  function natCmp(a, b) {
    return String(a == null ? "" : a).localeCompare(String(b == null ? "" : b), undefined, { numeric: true, sensitivity: "base" });
  }
  /** Fecha de GRABACION de una fuente (ms) o NaN. La del archivo (created_src "mtime": la de la
      copia) no dice cuando se grabo: cuenta como sin fecha (igual que server.rec_order). */
  function recordedMs(s) {
    if (!s) return NaN;
    if (s.created_src != null && !["apple", "creation_time"].includes(s.created_src)) return NaN;
    if (s.created_ts != null && isFinite(+s.created_ts)) return +s.created_ts * 1000;
    return createdMs(s.created);
  }
  /** Fuentes en ORDEN DE GRABACION: fecha de grabacion, luego nombre natural, luego orden de subida
      (`_idx` o su posicion). Sin fecha van al final. -> copia ordenada. */
  function recordingOrder(sources) {
    return (sources || []).filter(Boolean).map((s, i) => ({ s, t: recordedMs(s), k: s._idx != null && isFinite(+s._idx) ? +s._idx : i }))
      .sort((a, b) => {
        const fa = isFinite(a.t), fb = isFinite(b.t);
        if (fa && fb && a.t !== b.t) return a.t - b.t;
        if (fa !== fb) return fa ? -1 : 1;
        return natCmp(a.s.name, b.s.name) || a.k - b.k;
      }).map(x => x.s);
  }
  /** Nivel de confianza de una toma (su score 0..1). */
  function montageConf(score) {
    const v = +score;
    if (!isFinite(v)) return "baja";
    return v >= CONF_ALTA ? "alta" : v >= CONF_MEDIA ? "media" : "baja";
  }
  function slotById(montage, id) { return ((montage && montage.slots) || []).find(s => s && s.id === id) || null; }
  function findCand(montage, slotId, candId) {
    const s = slotById(montage, slotId);
    const c = s && (s.cands || []).find(x => x && x.id === candId);
    if (c) return c;
    for (const t of (montage && montage.slots) || []) { const x = (t.cands || []).find(y => y && y.id === candId); if (x) return x; }
    return null;
  }
  /** Numero (1..n) de cada parte en orden del guion: el que se ve en el panel y en el timeline. */
  function slotNumbers(montage) {
    const out = {};
    let n = 0;
    ((montage && montage.slots) || []).forEach(s => { if (s && s.id != null && !isExtraSlot(s)) out[s.id] = ++n; });
    return out;
  }
  /** Parte "extra": material fuera del guion que el montaje puso porque lo enriquece (sin lineas). */
  function isExtraSlot(s) { return !!s && (s.extra === true || !(s.lines || []).length); }
  /** Confianza de un tramo: la del montaje (timeline.conf: calidad y margen sobre la siguiente) o la
      de su toma. */
  function itemConf(montage, it) {
    if (it && isFinite(+it.conf) && it.conf !== null && it.conf !== "") return round3(+it.conf);
    return candConf(it ? findCand(montage, it.slot, it.cand) : null);
  }
  /** Confianza de una toma si se usa: la que calcula el montaje para ella (cand.conf, con el mismo
      margen que la elegida: en un empate las dos quedan igual) o, si no viene, su puntaje. */
  function candConf(c) {
    if (!c) return 0;
    if (isFinite(+c.conf) && c.conf !== null && c.conf !== "") return round3(+c.conf);
    return round3(+c.score || 0);
  }
  /** Volumen de una toma: el suyo, el de un tramo del montaje con esa toma o ese clip (misma
      sonoridad medida) o, con LUFS, la ganancia hasta el objetivo del montaje. */
  function candVol(montage, cand) {
    const v = candVolKnown(montage, cand);
    return v == null ? 1 : v;
  }
  /** Como candVol pero null si no hay con que medirla (el servidor la mide: vol "auto"). */
  function candVolKnown(montage, cand) {
    if (!cand) return null;
    if (isFinite(+cand.vol) && cand.vol !== null && cand.vol !== "") return +cand.vol;
    const tl = (montage && montage.timeline) || [];
    const hit = tl.find(it => it && it.cand === cand.id && isFinite(+it.vol)) || tl.find(it => it && it.sid === cand.sid && isFinite(+it.vol));
    if (hit) return +hit.vol;
    const st = (montage && montage.stats) || {}, tgt = +(st.target_lufs != null ? st.target_lufs : st.lufs_target);
    if (isFinite(+cand.lufs) && cand.lufs !== null && isFinite(tgt)) return Math.round(Math.max(0.1, Math.min(4, Math.pow(10, (tgt - +cand.lufs) / 20))) * 1000) / 1000;
    return null;
  }
  function srcMap(sources, montage) {
    const m = {};
    ((montage && montage.order) || []).forEach(o => { if (o && o.sid) m[o.sid] = { id: o.sid, name: o.name, created: o.created }; });
    (sources || []).forEach(s => { if (s && s.id) m[s.id] = Object.assign({}, m[s.id], s); });
    return m;
  }
  function mkClip(src, it, id) {
    const s = src || {}, known = isFinite(+it.vol) && it.vol !== null && it.vol !== "" && it.vol !== "auto";
    const c = { _id: id, sid: it.sid, type: "video", name: s.name || it.sid, thumb: s.thumb || null, srcdur: +s.dur || 0,
      vol: known ? +it.vol : 1, mute: false, fadeIn: 0, fadeOut: 0, layer: 0,
      start: +it.start || 0, in: +it.in || 0, out: +it.out || 0 };
    if (!known) c.volAuto = true;        // sin nivel medido: el servidor lo mide (vol "auto")
    ["ramp_in", "ramp_out"].forEach(k => { const r = rampOf(it[k]); if (r) c[k] = r; });
    return c;
  }
  /** Camara real de un tramo y si es del intercalado (como montage.plan_ramps): order[].setup de su
      clip si el montaje la trae (null = desconocida), si no la del candidato (del mismo clip) o la
      del tramo. -> {cam, ic}. */
  function camInfo(montage, sid, slotId, candId, itemSetup) {
    const o = ((montage && montage.order) || []).find(x => x && x.sid === sid && "setup" in x);
    let c = slotId != null ? findCand(montage, slotId, candId) : null;
    if (c && c.sid != null && c.sid !== sid) c = null;
    const cam = o ? o.setup : (c && c.setup != null ? c.setup : (itemSetup != null ? itemSetup : null));
    const fl = (c && c.flags) || [];
    return { cam: cam == null || cam === "" ? null : cam, ic: fl.includes("intercalado") || fl.includes("parcial") };
  }
  /** Clips del ensamblado (ASM.clips) desde plan.montage.timeline: en su lugar (start del
      montaje; sin start, uno tras otro) y cada uno con montage {slot, cand, join, conf}. */
  function montageClips(montage, sources, idgen) {
    const src = srcMap(sources, montage), gen = idgen || (k => "m" + k);
    let t = 0;
    return ((montage && montage.timeline) || []).filter(it => it && it.sid && +it.out > +it.in).map((it, k) => {
      const start = isFinite(+it.start) && it.start !== null ? +it.start : t;
      const c = mkClip(src[it.sid], Object.assign({}, it, { start }), gen(k));
      t = start + mClipLen(c);
      if (it.slot != null) {
        c.montage = { slot: it.slot, cand: it.cand != null ? it.cand : null, join: JOIN_KINDS.includes(it.join) ? it.join : null,
          conf: itemConf(montage, it) };
        const cd = findCand(montage, it.slot, it.cand);
        if (cd && isFinite(+cd.setup) && cd.setup !== null) c.montage.setup = +cd.setup;   // camara/encuadre (el director describe una toma por setup)
        Object.assign(c.montage, camInfo(montage, it.sid, it.slot, it.cand, it.setup));   // cambios de camara (aceleraciones)
      } else {
        c.extra = true;                // material fuera del guion que el montaje ya puso (plan.montage.extra)
        c.cam = camInfo(montage, it.sid, null, null, it.setup).cam;   // su camara (aceleraciones)
      }
      // lo que el usuario eligio en la union de entrada (compose.montage_plan lo guarda al componer):
      // manda sobre lo automatico al volver al ensamble
      if (it.ramp_user === true || it.ramp_user === false) c.rampUser = it.ramp_user;
      return c;
    });
  }
  /** Tramos para usar la toma `candId` en la parte `slotId`: la elegida por la IA vuelve tal como
      la armo el montaje (puede venir intercalada con otro angulo); otra toma entra completa. */
  function slotItems(montage, slotId, candId) {
    const slot = slotById(montage, slotId);
    if (!slot) return [];
    if (candId === slot.chosen) {
      const tl = ((montage && montage.timeline) || []).filter(it => it && it.slot === slotId && +it.out > +it.in);
      if (tl.length) return tl.map(it => Object.assign({ sid: it.sid, in: +it.in, out: +it.out, vol: isFinite(+it.vol) && it.vol !== null && it.vol !== "auto" ? +it.vol : candVolKnown(montage, findCand(montage, slotId, it.cand)), cand: it.cand, join: it.join || null, conf: itemConf(montage, it) },
        rampOf(it.ramp_in) ? { ramp_in: rampOf(it.ramp_in) } : {}, rampOf(it.ramp_out) ? { ramp_out: rampOf(it.ramp_out) } : {}));
    }
    const c = findCand(montage, slotId, candId);
    if (!c || !(+c.out > +c.in)) return [];
    return [{ sid: c.sid, in: +c.in, out: +c.out, vol: candVolKnown(montage, c), cand: c.id, join: null }];
  }
  /** Corre `delta` s todo lo que empieza en `t` o despues (copias; lo demas se conserva). */
  function shiftAfter(items, t, delta) {
    if (!delta) return (items || []).slice();
    return (items || []).map(x => ((+x.start || 0) >= t - 1e-6 ? Object.assign({}, x, { start: round3(Math.max(0, (+x.start || 0) + delta)) }) : x));
  }
  /** Segundos libres en la pista base desde `t` hasta el siguiente clip (Infinity si no hay). */
  function gapAfter(clips, t) {
    let nx = Infinity;
    (clips || []).forEach(c => { const s0 = +c.start || 0; if ((c.layer || 0) === 0 && s0 >= t - 1e-6 && s0 < nx) nx = s0; });
    return Math.max(0, nx - t);
  }
  /** Indices (en la pista base) de los clips de una parte del montaje, en orden de tiempo. */
  function slotClipIdx(clips, slotId) {
    return (clips || []).map((c, i) => [c, i]).filter(([c]) => c && c.montage && c.montage.slot === slotId && (c.layer || 0) === 0)
      .sort((a, b) => (+a[0].start || 0) - (+b[0].start || 0)).map(x => x[1]);
  }
  /** Toma en uso de una parte (la del primer tramo) o null si se quito del video. */
  function currentCand(clips, slotId) {
    const ix = slotClipIdx(clips, slotId);
    return ix.length ? clips[ix[0]].montage.cand : null;
  }
  /** "Usar esta toma": cambia la toma de la parte `slotId` por `candId`. Los tramos de esa parte se
      reemplazan (sid, in, out, vol, nombre, miniatura) en el mismo lugar y todo lo que sigue se corre
      lo que cambio el largo (los huecos se conservan: salen en negro). Si la parte ya no esta en el
      video, entra despues de la parte anterior que si este. o = {sources, idgen, overlays}.
      -> {clips, overlays, first (id del primer clip nuevo), delta} o null. */
  function swapTake(clips, montage, slotId, candId, o) {
    const opt = o || {}, src = srcMap(opt.sources, montage);
    let seq = 0;
    const gen = opt.idgen || (() => "s" + Date.now().toString(36) + (seq++));
    const items = slotItems(montage, slotId, candId);
    if (!items.length) return null;
    const cs = (clips || []).slice();
    const ix = slotClipIdx(cs, slotId);
    let start, end, at;
    const old = ix.map(i => cs[i]);
    if (old.length) {
      start = Math.min(...old.map(c => +c.start || 0)); end = Math.max(...old.map(mClipEnd)); at = Math.min(...ix);
    } else {
      // la parte se quito: entra al final de la parte anterior (en el orden del guion) que siga en el video
      const order = ((montage && montage.slots) || []).map(s => s.id), k = order.indexOf(slotId);
      let prev = null;
      for (let j = k - 1; j >= 0 && !prev; j--) { const pi = slotClipIdx(cs, order[j]); if (pi.length) prev = pi; }
      if (prev) { const last = prev.reduce((a, i) => (mClipEnd(cs[i]) > mClipEnd(cs[a]) ? i : a), prev[0]); start = end = mClipEnd(cs[last]); at = Math.max(...prev) + 1; }
      else { start = end = 0; at = 0; }
      // el hueco que dejo al quitarse se vuelve a llenar: lo de despues solo se corre si no cabe
      const len = items.reduce((a, it) => a + Math.max(0, it.out - it.in), 0);
      end = start + Math.min(len, gapAfter(cs, start));
    }
    const first = old[0] || null, last = old.length ? old.reduce((a, c) => (mClipEnd(c) > mClipEnd(a) ? c : a), old[0]) : null;
    const slot = slotById(montage, slotId);
    let t = start;
    const neu = items.map((it, k) => {
      const c = mkClip(src[it.sid], Object.assign({}, it, { start: round3(t) }), k === 0 && first ? first._id : gen());
      if (first) { c.mute = !!first.mute; if (k === 0) { c.fadeIn = +first.fadeIn || 0; if (first.trans) c.trans = clone(first.trans); } }
      if (last && k === items.length - 1) c.fadeOut = +last.fadeOut || 0;
      const cand = findCand(montage, slotId, it.cand);
      const j0 = first && first.montage ? first.montage.join : (slot && (montage.timeline || []).find(x => x.slot === slotId) || {}).join;
      c.montage = { slot: slotId, cand: it.cand, join: k === 0 ? (JOIN_KINDS.includes(j0) ? j0 : null) : (JOIN_KINDS.includes(it.join) ? it.join : "splice"),
        conf: it.conf != null ? it.conf : candConf(cand) };
      if (cand && isFinite(+cand.setup) && cand.setup !== null) c.montage.setup = +cand.setup;
      Object.assign(c.montage, camInfo(montage, it.sid, slotId, it.cand, it.setup));
      t += mClipLen(c);
      return c;
    });
    const delta = round3(t - end);
    const drop = new Set(ix);
    const rest = shiftAfter(cs.filter((c, i) => !drop.has(i)), end, delta);
    const pos = cs.slice(0, at).filter((c, i) => !drop.has(i)).length;
    rest.splice(pos, 0, ...neu);
    return { clips: rest, overlays: opt.overlays ? shiftAfter(opt.overlays, end, delta) : undefined, first: neu[0]._id, delta };
  }
  /** El material extra `x` ({sid, in, out}) esta en el video: indice del clip o -1. */
  function extraInVideo(clips, x) {
    if (!x) return -1;
    return (clips || []).findIndex(c => c && c.sid === x.sid && !(c.montage && c.montage.slot != null) &&
      Math.min(+c.out, +x.out) - Math.max(+c.in, +x.in) > 0.5 * Math.max(0.05, +x.out - +x.in));
  }
  /** Agrega el material extra `x` (plan.montage.extra: {sid, in, out, after}) despues de la parte que
      tiene su linea `after` (sin parte: la anterior en el guion; si esa se quito, la anterior que
      siga en el video) y corre lo que sigue. o = {sources, idgen, overlays}. -> como swapTake. */
  function addExtra(clips, montage, x, o) {
    const opt = o || {}, src = srcMap(opt.sources, montage), cs = (clips || []).slice();
    if (!x || !(+x.out > +x.in)) return null;
    const slots = (montage && montage.slots) || [], order = slots.map(s => s.id);
    const lines = (((montage && montage.script) || {}).lines || []).map(L => L && L.id);
    let k = -1;
    for (let j = lines.indexOf(x.after); j >= 0 && k < 0; j--) { const s = slots.find(t => (t.lines || []).includes(lines[j])); if (s) k = order.indexOf(s.id); }
    if (k < 0) { const s = slots.find(t => (t.lines || []).includes(x.after)); k = s ? order.indexOf(s.id) : -1; }
    let prev = null;
    for (let j = k; j >= 0 && !prev; j--) { const pi = slotClipIdx(cs, order[j]); if (pi.length) prev = pi; }
    let start = 0, at = 0;
    if (prev) { const last = prev.reduce((a, i) => (mClipEnd(cs[i]) > mClipEnd(cs[a]) ? i : a), prev[0]); start = mClipEnd(cs[last]); at = Math.max(...prev) + 1; }
    else if (k < 0 && lines.indexOf(x.after) < 0) {   // sin linea conocida: al final de la pista base
      cs.forEach((c, i) => { if ((c.layer || 0) === 0 && mClipEnd(c) >= start) { start = mClipEnd(c); at = i + 1; } });
    }
    // otro extra ya puesto en ese mismo punto: este va despues
    for (;;) { const e = cs.findIndex(c => c && c.extra && Math.abs((+c.start || 0) - start) < 1e-3 && (c.layer || 0) === 0); if (e < 0) break; start = mClipEnd(cs[e]); at = Math.max(at, e + 1); }
    let seq = 0;
    const gen = opt.idgen || (() => "e" + Date.now().toString(36) + (seq++));
    const c = mkClip(src[x.sid], { sid: x.sid, in: +x.in, out: +x.out, start: round3(start), vol: candVolKnown(montage, { id: null, sid: x.sid, vol: x.vol, lufs: x.lufs }) }, gen());
    c.extra = true;
    // su union de entrada es una linea nueva (corte seco: el director no pone transicion ni whoosh
    // a media seccion y la limpieza de tomas no cruza su inicio); sin parte (slot null)
    c.montage = Object.assign({ slot: null, cand: null, join: "line" }, camInfo(montage, x.sid, null, null, null));
    // si ahi hay un hueco, lo usa; lo de despues solo se corre lo que no cabe
    const len = mClipLen(c), d = Math.max(0, len - gapAfter(cs, start)), end = start + (len - d);
    const rest = shiftAfter(cs, end, d);
    rest.splice(Math.min(at, rest.length), 0, c);
    return { clips: rest, overlays: opt.overlays ? shiftAfter(opt.overlays, end, d) : undefined, first: c._id, delta: round3(d) };
  }
  /** Quita un clip y cierra el hueco (lo que sigue se corre hacia atras). -> {clips, overlays, delta} */
  function removeClip(clips, id, o) {
    const cs = (clips || []).slice(), i = cs.findIndex(c => c && c._id === id);
    if (i < 0) return null;
    const c = cs[i], len = mClipLen(c), end = mClipEnd(c);
    cs.splice(i, 1);
    const opt = o || {};
    return { clips: shiftAfter(cs, end, -len), overlays: opt.overlays ? shiftAfter(opt.overlays, end, -len) : undefined, delta: round3(-len) };
  }
  /** Quita del video todos los tramos de una parte (p. ej. un extra) y cierra el hueco. */
  function removeSlot(clips, slotId, o) {
    const ix = slotClipIdx(clips, slotId);
    if (!ix.length) return null;
    let cs = (clips || []).slice(), ov = o && o.overlays ? o.overlays.slice() : undefined, delta = 0;
    // de la ultima a la primera: cada una cierra su propio hueco
    ix.map(i => cs[i]).sort((a, b) => (+b.start || 0) - (+a.start || 0)).forEach(c => {
      const r = removeClip(cs, c._id, { overlays: ov });
      cs = r.clips; ov = r.overlays; delta += r.delta;
    });
    return { clips: cs, overlays: ov, delta: round3(delta) };
  }
  /** Filas del panel "Tomas" en orden del guion: {type:"section", n}, {type:"slot", ...} (una por
      parte, en su primera linea), {type:"uncovered", line} (linea sin toma) y {type:"extra", ...}
      (material fuera del guion que lo enriquece, despues de su linea). */
  function montageRows(montage, clips) {
    const m = montage || {}, sc = m.script || {}, lines = sc.lines || [], sections = sc.sections || [];
    const lineText = {}; lines.forEach(L => { if (L) lineText[L.id] = L.text; });
    const secOf = {}; sections.forEach((s, k) => (s.lines || []).forEach(l => { if (!(l in secOf)) secOf[l] = k; }));
    const slotOf = {}; (m.slots || []).forEach(s => (s.lines || []).forEach(l => { if (!(l in slotOf)) slotOf[l] = s; }));
    const nums = slotNumbers(m), rows = [], seenL = new Set(), lineIdx = {};
    lines.forEach((L, i) => { if (L) lineIdx[L.id] = i; });
    // una linea partida entre dos partes (empalme): cada tarjeta muestra SU pedazo del guion y "1/2"
    const slotsOfLine = {};
    (m.slots || []).forEach(s => { if (s && !isExtraSlot(s)) (s.lines || []).forEach(l => { (slotsOfLine[l] = slotsOfLine[l] || []).push(s.id); }); });
    const slotRow = s => {
      const ix = slotClipIdx(clips, s.id), cur = ix.length ? clips[ix[0]].montage.cand : null;
      const cands = (s.cands || []).slice().sort((a, b) => (+b.score || 0) - (+a.score || 0));
      const curC = cands.find(c => c.id === cur) || null, ex = isExtraSlot(s);
      const used = [...new Set(ix.map(i => clips[i].montage.cand))];
      const conf = ix.length ? +clips[ix[0]].montage.conf : NaN;
      const chosenC = cands.find(c => c.id === s.chosen) || cands[0] || {};
      const shared = ex ? null : (s.lines || []).find(l => (slotsOfLine[l] || []).length > 1);
      const part = shared ? { i: slotsOfLine[shared].indexOf(s.id) + 1, n: slotsOfLine[shared].length } : null;
      const full = (s.lines || []).map(l => lineText[l] || "").filter(Boolean).join(" ");
      const edited = cur != null && cur !== s.chosen;
      return { type: "slot", id: s.id, n: nums[s.id] || null, extra: ex, lines: (s.lines || []).map(l => ({ id: l, text: lineText[l] || "" })),
        text: ex ? String(s.text || chosenC.text || "") : (part ? String(s.text || (curC || chosenC).text || full) : full || String(s.text || "")),
        part, chosen: s.chosen, cur, curCand: curC, inVideo: cur != null, edited,
        said: curC ? String(curC.text || "") : "", score: curC ? +curC.score || 0 : null,
        reason: curC ? usedReason(curC, edited) : "",
        conf: isFinite(conf) ? conf : candConf(chosenC), level: montageConf(isFinite(conf) ? conf : candConf(chosenC)),
        mixed: used.filter(c => c !== cur), alts: ex ? [] : cands.filter(c => c.id !== cur) };
    };
    // sugerencias fuera del guion que el montaje NO puso (las puestas son partes "extra")
    const sugg = (m.extra || []).map((x, k) => (x && x.kept !== true ? Object.assign({ type: "extra", k }, x) : null)).filter(Boolean);
    const extrasAfter = ids => sugg.filter(x => ids.includes(x.after)).forEach(x => { x.done = true; rows.push(x); });
    let lastSec = null;
    const sec = id => { const k = secOf[id]; if (sections.length > 1 && k != null && k !== lastSec) { rows.push({ type: "section", n: k + 1 }); lastSec = k; } };
    const unc = L => { sec(L.id); rows.push({ type: "uncovered", line: { id: L.id, text: L.text || "" } }); seenL.add(L.id); extrasAfter([L.id]); };
    // las partes van en el orden del montaje (el de la linea de tiempo); antes de cada una, las lineas
    // del guion anteriores que no tienen toma
    (m.slots || []).forEach(s => {
      if (!s) return;
      if (!isExtraSlot(s)) {
        const first = Math.min(...(s.lines || []).map(l => (l in lineIdx ? lineIdx[l] : Infinity)));
        lines.forEach((L, i) => { if (L && i < first && !seenL.has(L.id) && !slotOf[L.id]) unc(L); });
        if (s.lines && s.lines.length) sec(s.lines[0]);
      }
      rows.push(slotRow(s));
      (s.lines || []).forEach(l => seenL.add(l));
      extrasAfter(s.lines || []);
    });
    lines.forEach(L => { if (L && !seenL.has(L.id) && !slotOf[L.id]) unc(L); });
    sugg.filter(x => !x.done).forEach(x => rows.push(x));
    return rows;
  }
  /** Motivo de la toma EN USO. El de una alternativa esta escrito comparandola con la elegida ("Igual
      de buena; se usa la otra cámara para intercalar ángulos", "Toma anterior (en empate...)"): si el
      usuario la eligio y va sola, eso ya no aplica y se dice lo que es. Sus defectos se conservan. */
  function usedReason(c, edited) {
    const r = String((c && c.reason) || "");
    if (!edited) return r;
    if (/^(Igual de buena|Toma anterior)/i.test(r)) return "Toma completa y fiel al guion (igual de buena que la de la IA)";
    if (/^El juez LLM prefiri/i.test(r)) return "Toma completa; el juez LLM prefería la de la IA";
    return r;
  }
  /** Resumen del panel: partes, sin toma, cambiadas por el usuario y por nivel de confianza. */
  function montageSummary(montage, clips) {
    const rows = montageRows(montage, clips), out = { slots: 0, extras: 0, uncovered: 0, edited: 0, removed: 0, alta: 0, media: 0, baja: 0,
      discarded: ((montage && montage.discarded) || []).length };
    rows.forEach(r => {
      if (r.type === "uncovered") out.uncovered++;
      if (r.type !== "slot") return;
      if (r.extra) { if (r.inVideo) out.extras++; return; }
      out.slots++;
      if (!r.inVideo) out.removed++;
      else { out[r.level]++; if (r.edited) out.edited++; }
    });
    return out;
  }
  /** Texto del guion para editarlo: una linea por renglon y una linea en blanco entre secciones. */
  function scriptText(script) {
    const sc = script || {}, lines = sc.lines || [], secs = sc.sections || [];
    if (!lines.length) return String(sc.text || "").trim();
    // una linea con recursos (plan.montage.script.lines[i].inserts) vuelve con su anotacion: rearmar
    // con el guion editado no los pierde
    const txt = {}; lines.forEach(L => { if (L) txt[L.id] = withInserts(String(L.text || "").trim(), L.inserts); });
    if (secs.length) {
      const used = new Set(), out = [];
      secs.forEach(s => { const ls = (s.lines || []).filter(l => l in txt && !used.has(l)); ls.forEach(l => used.add(l)); if (ls.length) out.push(ls.map(l => txt[l]).join("\n")); });
      const rest = lines.filter(L => L && !used.has(L.id)).map(L => txt[L.id]);
      if (rest.length) out.push(rest.join("\n"));
      return out.join("\n\n");
    }
    return lines.map(L => txt[L.id]).join("\n");
  }
  /** Items `main` de POST /compose desde los clips del ensamblado (con montage si lo tienen). Un extra
      (fuera del guion, agregado desde el panel) va con su union: "line" si no trae otra. */
  function composeMain(clips) {
    return (clips || []).map(c => {
      const base = c.type === "image"
        ? { sid: c.sid, type: "image", dur: c.dur, start: c.start || 0, layer: c.layer || 0, fadeIn: c.fadeIn || 0, fadeOut: c.fadeOut || 0, trans: c.trans }
        : { sid: c.sid, type: "video", in: c.in, out: c.out, vol: c.volAuto && (c.montage || c.extra) ? "auto" : c.vol, mute: c.mute, start: c.start || 0, layer: c.layer || 0, fadeIn: c.fadeIn || 0, fadeOut: c.fadeOut || 0, trans: c.trans };
      if (c.type !== "image") ["ramp_in", "ramp_out"].forEach(k => { const r = rampOf(c[k]); if (r) base[k] = r; });
      const mm = c.montage || null;
      if (mm && mm.slot != null)
        base.montage = Object.assign({ slot: mm.slot, cand: mm.cand != null ? mm.cand : null, join: mm.join || null, conf: mm.conf != null ? +mm.conf : null },
          mm.setup != null ? { setup: mm.setup } : {});
      else if (c.extra || mm)
        base.montage = { slot: null, cand: null, join: mm && JOIN_KINDS.includes(mm.join) ? mm.join : "line", conf: null };
      // cambio de camara con aceleracion en la union de ENTRADA de este clip: el estado (lo que se ve
      // en el timeline) y si lo decidio el usuario (su eleccion manda al componer)
      if (base.montage && c.rampJoin != null) {
        base.montage.ramp = !!c.rampJoin;
        // = montage.plan_ramps: ramp_user es LO QUE ELIGIO (true = con aceleracion, false = corte seco)
        if (c.rampUser === true || c.rampUser === false) base.montage.ramp_user = c.rampUser;
      }
      return base;
    });
  }
  /** «¿Cuántos shorts sugerir?»: 0 (Ninguno) a 5; vacio o invalido -> 2 (lo de siempre). «Ninguno»
      viaja como 0: el servidor no le pide shorts a la IA (highlights.select_highlights). */
  function shortsWanted(v) {
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? Math.max(0, Math.min(5, n)) : 2;
  }
  /** Cuerpo de POST /compose. o = {clips, overlays, glossary, highlights, director, montageJob, script}:
      desde un montaje manda montage_job (el servidor re-encola ESE trabajo como compose y conserva
      plan.montage) y el guion. */
  function composeBody(o) {
    const b = {
      main: composeMain(o.clips),
      overlays: (o.overlays || []).map(v => {
        const x = { sid: v.sid, type: v.type, start: v.start, dur: v.dur, pos: v.pos, size: v.size };
        if (v.x != null && v.y != null) { x.x = v.x; x.y = v.y; }
        return x;
      }),
      glossary: o.glossary || "", highlights: shortsWanted(o.highlights),
      director: o.director === undefined ? false : o.director,
    };
    if (o.montageJob) {
      b.montage_job = o.montageJob;
      const s = o.script == null ? "" : String(o.script).trim();
      b.script = s || null;
      // recursos del guion (imagen o video flotante mientras se dice su linea), en segundos del ensamble
      if (Array.isArray(o.inserts)) b.inserts = o.inserts.map(insertForCompose).filter(Boolean);
    }
    return b;
  }
  /** Estado de un trabajo de montaje (GET /status): {state: "running" | "ready" | "error" | "other",
      stage: "queued" | "words" | "match", n, N (clips escuchados), message, pct}. */
  function montageStatus(j) {
    if (!j) return { state: "other" };
    if (j.phase === "error" || j.state === "error" || j.state === "canceled")
      return { state: "error", error: j.error || "No se pudo armar el video." };
    if (j.phase && j.phase !== "assembly") return { state: "other", phase: j.phase };
    const running = j.state === "queued" || j.state === "running";
    const m = j.plan && j.plan.montage;
    const msg = String(j.message || ""), nn = /(\d+)\s*(?:\/|de)\s*(\d+)/.exec(msg);
    // un rearmado o una composicion que fallo vuelve a las tomas de antes con su error (nada se pierde);
    // una composicion interrumpida (la app se cerro) o cancelada lo dice en el mensaje, sin error
    if (!running && (m || j.has_plan)) return { state: "ready", montage: m || null, note: j.error || (MONTAGE_BACK_RE.test(msg) ? msg : null) };
    const w = j.words && typeof j.words === "object" ? j.words : null;
    let stage = j.state === "queued" ? "queued" : "words";
    if (j.stage === "words") stage = "words";
    else if (j.stage === "montage" || j.stage === "match") stage = "match";
    // viendo los videos de apoyo (despues de elegir las tomas): sigue en "Eligiendo..." con su mensaje
    else if (j.stage === "broll") return { state: "running", stage: "match", broll: true, n: null, N: null, errors: 0, message: msg, pct: +j.pct || 0 };
    else if (/escuch|transcrib/i.test(msg)) stage = "words";
    else if (/elig|arm|compar|guion/i.test(msg)) stage = "match";
    const n = w && isFinite(+w.done) && w.done !== null ? +w.done : nn ? +nn[1] : null;
    const N = w && isFinite(+w.total) && +w.total > 0 ? +w.total : nn ? +nn[2] : null;
    return { state: "running", stage, n, N, errors: w && +w.errors > 0 ? +w.errors : 0, message: msg, pct: +j.pct || 0 };
  }
  /** Transcripcion por clip ya lista: {n (videos), ready, error, pending}. */
  function wordsProgress(sources) {
    const out = { n: 0, ready: 0, error: 0, pending: 0 };
    (sources || []).forEach(s => {
      if (!s || s.type === "image") return;
      out.n++;
      if (s.words === "ready") out.ready++; else if (s.words === "error") out.error++; else out.pending++;
    });
    return out;
  }
  /** Progreso global de la subida (0..100) desde [{loaded, total}] por archivo. */
  function uploadPct(rows) {
    let l = 0, t = 0;
    (rows || []).forEach(r => { const tt = Math.max(0, +r.total || 0); t += tt; l += Math.min(tt, Math.max(0, +r.loaded || 0)); });
    return t > 0 ? Math.round(l / t * 1000) / 10 : 0;
  }
  /** Un proyecto reciente se puede abrir y en que estado esta -> {open, label}. Un armado con IA
      se abre armandose (progreso) o listo (revisar las tomas). */
  function projectState(j) {
    if (!j) return { open: false, label: "" };
    if (j.phase === "assembly") {
      const running = j.state === "queued" || j.state === "running";
      return { open: true, label: running ? "armando con IA" : "tomas por revisar" };
    }
    const open = j.has_plan !== false && ["review", "done"].includes(j.phase);
    return { open, label: open ? (j.phase === "done" ? "exportado" : "en edición") : "no se terminó" };
  }
  /** Tiempo de la fuente con decimas: "0:04.1". */
  function fmtSrc(s) {
    const v = Math.max(0, Math.round((+s || 0) * 10) / 10), m = Math.floor(v / 60), sec = v - m * 60;
    return `${m}:${sec.toFixed(1).padStart(4, "0")}`;
  }

  // ------------------------------------------------------------------ recursos del guion
  /* El guion puede pedir un recurso al final de una linea: "texto. - Agregar video_marcas" (separador
     - – — | y verbo agregar/anadir/insertar/mostrar/poner/add/insert o su imperativo, sin importar
     mayusculas ni acentos) o "[agregar nombre]" / "[nombre]" / "{nombre}". Varios nombres van
     separados por coma. El archivo nombrado es un RECURSO (nunca una toma): mientras se dice esa
     linea aparece como tarjeta flotante. Port PASO A PASO de montage.split_annotation / asset_key /
     match_asset / classify (tests/test_studio_ui.py::test_port_de_montage_en_fxcore_igual_al_motor compara ambos): un "[texto]" sin verbo que
     no parece archivo ni coincide con uno elegido es una acotacion ("[pausa]") y no es recurso. */
  const ANN_VERBS = new Set(["agregar", "anadir", "insertar", "mostrar", "poner", "add", "insert", "agrega", "anade", "inserta", "muestra", "pon"]);
  const ANN_SEPS = "-–—|";
  const ANN_SEP_RE = /[-–—|]\s*(\p{L}+)\s+(\S.*)$/uy;
  const ANN_BRACKET_RE = /(\[([^\[\]{}]*)\]|\{([^\[\]{}]*)\})[\s.,;:!?…]*$/u;
  const ANN_ARTICLES = new Set(["el", "la", "los", "las", "un", "una", "the", "a", "an"]);
  const ANN_QUOTES = "\"'«»“”‘’`";
  const IMG_EXT = new Set(["jpg", "jpeg", "png", "heic", "heif", "webp", "gif", "bmp", "tif", "tiff", "avif"]);
  const VID_EXT = new Set(["mp4", "mov", "m4v", "webm", "mkv", "avi", "mts", "m2ts", "3gp", "hevc"]);
  const stripAcc = s => String(s).normalize("NFKD").replace(/[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]/g, "");
  const stripChars = (s, cs) => { let a = 0, b = s.length; while (a < b && cs.includes(s[a])) a++; while (b > a && cs.includes(s[b - 1])) b--; return s.slice(a, b); };
  const rstripChars = (s, cs) => { let b = s.length; while (b > 0 && cs.includes(s[b - 1])) b--; return s.slice(0, b); };
  /** montage.asset_key: "Video Horno.MP4" -> "video_horno". */
  function assetNorm(name) {
    let s = stripAcc(stripChars(String(name == null ? "" : name).trim(), ANN_QUOTES).toLowerCase());
    s = s.split("/").pop().replace(/\.[a-z0-9]{2,5}$/, "");
    return s.replace(/[\s_\-]+/g, "_").replace(/^_+|_+$/g, "");
  }
  const fileLike = n => /[_.\-\d]/.test(String(n).trim());
  const isVerb = w => ANN_VERBS.has(stripAcc(String(w).toLowerCase()));
  const PREFIX_MIN = 4;
  /** montage._match_key: igual primero; luego prefijo unico por palabras completas ("foto_local" ->
      "foto_local_final"; `k` de 4+ caracteres y con letras: "1", "a" nunca toman un archivo por
      prefijo); `rev` ([bool] por clave): tambien al reves solo en esas claves (imagenes). prefix =
      false: solo igual (ids). -> indice o -1. */
  function matchKey(k, keys, rev, prefix) {
    if (!k) return -1;
    const ex = keys.indexOf(k);
    if (ex >= 0) return ex;
    if (prefix === false || k.length < PREFIX_MIN || !/[a-z]/.test(k)) return -1;
    let pre = []; keys.forEach((x, i) => { if (x && x.startsWith(k + "_")) pre.push(i); });
    if (pre.length === 1) return pre[0];
    if (pre.length || !rev) return -1;
    pre = []; keys.forEach((x, i) => { if (x && rev[i] && x.length >= 3 && /[a-z]/.test(x) && k.startsWith(x + "_")) pre.push(i); });
    return pre.length === 1 ? pre[0] : -1;
  }
  const MEDIA_EXT_RE = new RegExp("\\.(?:" + [...IMG_EXT, ...VID_EXT].sort().join("|") + ")$", "i");
  /** montage._name_like: un nombre de la marca parece ARCHIVO (no el final de una frase): coincide con
      uno elegido, termina en extension de imagen/video, o es UNA palabra con letras y _ . - o digitos. */
  function nameLike(name, keys) {
    const n = stripChars(stripChars(String(name == null ? "" : name).trim(), ANN_QUOTES).trim(), " ");
    if (!n) return false;
    if (keys && keys.length && matchKey(assetNorm(n), keys) >= 0) return true;
    const ws = n.split(/\s+/).filter(Boolean);
    if (ws.length > 3) return false;
    if (MEDIA_EXT_RE.test(n)) return true;
    return ws.length === 1 && fileLike(n) && /[^\P{L}]/u.test(n);
  }
  const knownKeys = files => {
    const out = [];
    (files || []).forEach(f => [f && typeof f === "object" ? f.name : f, f && typeof f === "object" ? (f.sid || f.id) : null].forEach(v => {
      const k = v ? assetNorm(v) : ""; if (k && !out.includes(k)) out.push(k); }));
    return out;
  };
  function splitNames(s, keys) {
    const out = [];
    String(s).split(/\s*[,;]\s*/).forEach(p0 => {
      let p = stripChars(stripChars(rstripChars(stripChars(p0.trim(), ANN_QUOTES), ".,;:!?…").trim(), ANN_QUOTES).trim(), " ");
      const ws = p.split(/\s+/).filter(Boolean);
      if (ws.length > 1 && ANN_ARTICLES.has(ws[0].toLowerCase())) p = ws.slice(1).join(" ");
      if (!p) return;
      const sub = p.split(/\s+(?:y|e|and|\+|&)\s+/).map(x => x.trim()).filter(Boolean);
      if (sub.length > 1 && sub.every(x => fileLike(x) || matchKey(assetNorm(x), keys || []) >= 0)) out.push(...sub);
      else out.push(p);
    });
    return out;
  }
  /** montage.split_annotation: una linea -> {text (sin las marcas del final), names}. `known` =
      archivos elegidos ([{name}] o nombres) para reconocer "[nombre]" sin verbo. */
  function lineRefs(raw, known) {
    let s = String(raw == null ? "" : raw).replace(/\s+$/, "");
    const keys = Array.isArray(known) ? knownKeys(known) : [];
    let names = [];
    for (let guard = 0; s && guard < 50; guard++) {
      const m = ANN_BRACKET_RE.exec(s);
      if (m) {
        const inner = (m[2] != null ? m[2] : (m[3] || "")).trim(), curly = m[3] != null;
        const ws = inner.split(/\s+/);
        let got;
        if (ws.length >= 2 && isVerb(ws[0])) got = splitNames(inner.slice(inner.indexOf(ws[0]) + ws[0].length).trim(), keys);
        else got = splitNames(inner, keys).filter(n => curly || nameLike(n, keys));
        names = got.concat(names);
        s = s.slice(0, m.index).replace(/\s+$/, "");
        continue;
      }
      let hit = null;
      for (let i = 0; i < s.length; i++) {
        if (ANN_SEPS.includes(s[i]) && (i === 0 || !/[\p{L}\p{N}]/u.test(s[i - 1]))) {
          ANN_SEP_RE.lastIndex = i;
          const r = ANN_SEP_RE.exec(s);
          if (r && isVerb(r[1])) hit = { index: i, names: r[2] };
        }
      }
      if (!hit) break;
      const got = splitNames(hit.names, keys);
      if (!got.length || !got.every(n => nameLike(n, keys))) break;   // una frase ("— agrega tu negocio hoy"): se dice
      names = got.concat(names);
      s = s.slice(0, hit.index).replace(/\s+$/, "");
    }
    return { text: s.trim(), names };
  }
  /** Recursos que pide un guion (montage.parse_script: una linea que solo trae la marca se suma a la
      anterior): {lines: [{i (linea no vacia), text, names}] (solo las con recurso), names: [cada uno
      una vez, en orden]}. */
  function scriptRefs(text, known) {
    const rows = [];
    let pending = [], last = null, i = 0;
    String(text == null ? "" : text).replace(/\r\n?/g, "\n").split("\n").forEach(raw => {
      const s = raw.split(/\s+/).filter(Boolean).join(" ");
      if (!s) return;
      const r = lineRefs(s, known);
      if (!r.text) { if (last) last.names.push(...r.names); else pending.push(...r.names); return; }
      last = { i: i++, text: r.text, names: pending.concat(r.names) }; pending = [];
      rows.push(last);
    });
    const names = [];
    rows.forEach(L => L.names.forEach(n => { if (!names.includes(n)) names.push(n); }));
    return { lines: rows.filter(L => L.names.length), names };
  }
  /** El guion sin marcas de recursos (lo que se compara con las tomas). */
  function stripRefs(text, known) {
    return String(text == null ? "" : text).split(/\r?\n/).map(raw => (raw.trim() ? lineRefs(raw, known).text : "")).join("\n");
  }
  /** Linea del guion con sus recursos: si su texto ya no trae la marca, se le agrega ("- Agregar x";
      entre corchetes si algun nombre no parece archivo, asi se vuelve a leer igual sin los archivos). */
  function withInserts(text, names) {
    const ns = (names || []).map(n => String(n || "").trim()).filter(Boolean);
    if (!ns.length || lineRefs(text).names.length) return text;
    return ns.every(n => nameLike(n, [])) ? `${text} - Agregar ${ns.join(", ")}` : `${text} [Agregar ${ns.join(", ")}]`;
  }
  /** montage._match_asset_ex: {idx, exact} (exacto = mismo nombre normalizado o mismo id). */
  function matchAssetEx(name, files) {
    const k = assetNorm(name), fs = files || [];
    const nk = fs.map(f => { const v = f && typeof f === "object" ? f.name : f; return v ? assetNorm(v) : ""; });
    let i = matchKey(k, nk, fs.map(f => fileKind(f) === "image"));
    if (i >= 0) return { idx: i, exact: nk[i] === k };
    i = matchKey(k, fs.map(f => { const v = f && typeof f === "object" ? (f.sid || f.id) : null; return v ? assetNorm(v) : ""; }), null, false);
    return { idx: i, exact: i >= 0 };
  }
  /** montage.match_asset: indice del archivo ([{name, sid|id}] o nombres) que corresponde al recurso
      `name`: por su nombre (igual, luego prefijo unico por palabras; al reves solo una imagen) y, si
      no, por su id (solo igual). -1 si ninguno. */
  function matchAsset(name, files) { return matchAssetEx(name, files).idx; }
  /** montage.asset_kind: "image" | "video" | null de un archivo ({name, type|kind (mime o image/video)}). */
  function fileKind(f) {
    const o = f && typeof f === "object" ? f : { name: f };
    const k = String(o.kind || o.type || o.mime || "").toLowerCase();
    if (k.startsWith("image")) return "image";
    if (k.startsWith("video")) return "video";
    const m = /\.([A-Za-z0-9]{2,5})$/.exec(String(o.name || o.path || ""));
    const e = m ? m[1].toLowerCase() : "";
    if (IMG_EXT.has(e)) return "image";
    if (VID_EXT.has(e)) return "video";
    return +o.dur > 0 ? "video" : null;
  }
  /** montage.classify: archivos elegidos + guion -> {refs: [{name, idx (-1: falta)}], assets: [idx],
      clips: [idx]}. Toda imagen y todo archivo que el guion pide es recurso; los demas videos, tomas. */
  function classifyFiles(files, script) {
    const fs = files || [];
    // montage._ref_asset: un parecido solo por prefijo nunca se lleva una toma con voz transcrita
    const spoken = f => !!(f && typeof f === "object" && ((Array.isArray(f.words) && f.words.length) || f.has_words === true));
    const refAsset = name => { const r = matchAssetEx(name, fs); return r.idx >= 0 && !r.exact && fileKind(fs[r.idx]) !== "image" && spoken(fs[r.idx]) ? -1 : r.idx; };
    const refs = scriptRefs(script, fs).names.map(name => ({ name, idx: refAsset(name) }));
    const asset = new Set(refs.filter(r => r.idx >= 0).map(r => r.idx));
    fs.forEach((f, i) => { if (fileKind(f) === "image") asset.add(i); });
    const clips = [];
    fs.forEach((f, i) => { if (!asset.has(i) && fileKind(f) === "video") clips.push(i); });
    return { refs, assets: [...asset].sort((a, b) => a - b), clips };
  }

  /* Colocacion de un recurso en el ensamble (port de montage.plan_inserts): desde la primera palabra
     de su linea - 0.15 s hasta la ultima + 0.25 s (s del ensamble; minimo 2 s; varios en una linea
     se reparten; nunca encima del siguiente). Las palabras de cada linea salen de "line_times" del
     candidato en uso de cada parte, asi que un recurso sigue a su linea si el usuario cambia tomas. */
  const INS = { pre: 0.15, post: 0.25, min: 2.0, speedMax: 1.4, slowMin: 0.8 };
  /** Como entra un medio de `mediaDur` s en un hueco de `slot` s (montage.plan_inserts): imagen ->
      "hold"; video de 0.8x a 1.4x del hueco -> "speed" (su velocidad se ajusta); mas largo -> "trim"
      (desde src_in); mas corto -> "trim" y el recurso dura lo suyo (`len`). -> {fit, speed, src_in, len?}. */
  function insertFit(kind, mediaDur, slot, srcIn) {
    const L = +slot || 0, d = +mediaDur || 0;
    if (kind !== "video") return { fit: "hold", speed: 1, src_in: 0 };
    const out = { fit: "trim", speed: 1, src_in: round3(Math.max(0, Math.min(+srcIn || 0, Math.max(0, d - L)))) };
    if (d > 0 && L > 0) {
      const r = d / L;
      if (r >= INS.slowMin && r <= INS.speedMax) return { fit: "speed", speed: round3(r), src_in: 0 };
      if (r < INS.slowMin) { out.src_in = 0; out.len = d; }
    }
    return out;
  }
  /** {linea: [t0, t1]} en segundos del ensamble para los clips actuales (line_times de su toma). */
  function lineSpans(montage, clips) {
    const span = {};
    (clips || []).filter(c => c && (c.layer || 0) === 0 && c.type !== "image" && c.montage && c.montage.slot != null).forEach(c => {
      const cd = findCand(montage, c.montage.slot, c.montage.cand);
      const lt = cd && (cd.sid == null || cd.sid === c.sid) ? cd.line_times : null;
      Object.keys(lt || {}).forEach(lid => {
        const v = lt[lid]; if (!Array.isArray(v) || v.length < 2) return;
        const a = +c.in, b = +c.out;
        let s0 = +v[0], s1 = +v[1]; if (!isFinite(s0) || !isFinite(s1)) return;
        s0 = Math.min(Math.max(s0, a), b); s1 = Math.min(Math.max(s1, a), b);
        if (s1 - s0 <= 1e-6 && (+v[1] < a || +v[0] > b)) return;     // la linea no esta en este tramo
        const t0 = (+c.start || 0) + s0 - a, t1 = (+c.start || 0) + s1 - a, cur = span[lid];
        span[lid] = cur ? [Math.min(cur[0], t0), Math.max(cur[1], t1)] : [t0, t1];
      });
    });
    return span;
  }
  /** Tramo de una linea sin line_times: el del montaje corrido con su parte (o en proporcion si la
      toma cambio) o, si nada, el de su parte completa. */
  function lineSpanFallback(montage, clips, ins0) {
    const m = montage || {}, cs = clips || [];
    const slotIds = (m.slots || []).filter(s => s && (s.lines || []).includes(ins0.line)).map(s => s.id);
    const cur = [].concat(...slotIds.map(id => slotClipIdx(cs, id))).map(i => cs[i]);
    if (!cur.length) return null;
    const orig = (m.timeline || []).filter(it => it && slotIds.includes(it.slot));
    const cs0 = Math.min(...cur.map(c => +c.start || 0)), ce0 = Math.max(...cur.map(mClipEnd));
    if (!orig.length || !isFinite(+ins0.start) || ins0.start === null) return [cs0 + INS.pre, ce0 - INS.post];
    const os = Math.min(...orig.map(it => +it.start || 0)), oe = Math.max(...orig.map(it => (+it.start || 0) + (+it.out - +it.in)));
    const k = oe > os ? (ce0 - cs0) / (oe - os) : 1;
    return [cs0 + (+ins0.start + INS.pre - os) * k, cs0 + (+ins0.end - INS.post - os) * k];
  }
  /** Los recursos del montaje colocados sobre los clips ACTUALES del ensamble. o = {sources: [{id,
      type, dur}], edits: {id: {enabled, sid, kind, status}}}. -> [{...insert, start, end, fit, speed,
      src_in, placed, enabled}] (mismos ids que plan.montage.inserts). */
  function placeInserts(montage, clips, o) {
    const opt = o || {}, m = montage || {}, cs = clips || [];
    const srcOf = sid => (opt.sources || []).find(s => s && s.id === sid) || null;
    const span = lineSpans(m, cs);
    const total = cs.filter(c => c && (c.layer || 0) === 0).reduce((a, c) => Math.max(a, mClipEnd(c)), 0);
    const base = (m.inserts || []).filter(x => x && x.id != null);
    const merged = base.map(ins0 => Object.assign({}, ins0, (opt.edits && opt.edits[ins0.id]) || {}));
    // por linea: sus recursos VISIBLES, en el orden del montaje (varios en una linea se reparten su
    // tramo); uno quitado (enabled false) no le quita lugar a los demas: se coloca en toda su linea
    const byLine = {};
    merged.forEach((x, k) => { if (x.enabled !== false) (byLine[x.line] = byLine[x.line] || []).push(k); });
    const out = merged.map((ins, k) => {
      const ins0 = base[k];
      let sp = span[ins.line] || null;
      if (!sp) { const f = lineSpanFallback(m, cs, ins0); sp = f; }
      if (!sp) return Object.assign(ins, { placed: false });
      const s = Math.max(0, sp[0] - INS.pre), e = Math.min(total || Infinity, sp[1] + INS.post);
      const sib = ins.enabled === false ? [k] : (byLine[ins.line] || [k]), j = Math.max(0, sib.indexOf(k)), n = sib.length || 1;
      let a = s + (e - s) * j / n, b = s + (e - s) * (j + 1) / n;
      if (b - a < INS.min) { b = Math.min(total || Infinity, a + INS.min); a = Math.max(0, Math.min(a, b - INS.min)); }
      return Object.assign(ins, { start: a, end: b, placed: true });
    });
    // sin encimarse: solo entre los visibles (uno quitado no recorta a nadie)
    const on = out.filter(x => x.placed && x.enabled !== false).sort((x, y) => x.start - y.start || String(x.id).localeCompare(String(y.id)));
    for (let k = 0; k + 1 < on.length; k++) if (on[k].end > on[k + 1].start) on[k].end = Math.max(on[k].start, on[k + 1].start);
    out.forEach(ins => {
      const src = ins.sid ? srcOf(ins.sid) : null;
      const kind = src ? (src.type === "image" ? "image" : "video") : (ins.sid ? ins.kind : null);
      if (ins.sid && kind && ins.placed) {
        const f = insertFit(kind, src ? +src.dur : +ins.media_dur, ins.end - ins.start, ins.src_in);
        Object.assign(ins, { kind, fit: f.fit, speed: f.speed, src_in: f.src_in, status: "ok" });
        if (f.len != null) ins.end = ins.start + f.len;
      } else if (!ins.sid) ins.status = "missing";
      if (ins.placed) { ins.start = round3(ins.start); ins.end = round3(ins.end); }
    });
    return out;
  }
  /** Un recurso tal como va en POST /compose (lo que el servidor sanea). Uno sin lugar (su linea no
      esta en el video) va con start/end null: no ocupa tiempo ni recorta a otro. */
  function insertForCompose(x) {
    if (!x || x.id == null) return null;
    const on = x.placed !== false;
    return { id: String(x.id), line: x.line || null, name: x.name || "", sid: x.sid || null, kind: x.kind || null,
      start: on && x.start != null && isFinite(+x.start) ? round3(+x.start) : null, end: on && x.end != null && isFinite(+x.end) ? round3(+x.end) : null,
      fit: x.fit || "hold", speed: isFinite(+x.speed) && +x.speed > 0 ? +x.speed : 1, src_in: isFinite(+x.src_in) ? +x.src_in : 0,
      status: x.sid ? "ok" : "missing", enabled: x.enabled !== false && x.placed !== false };
  }

  // ------------------------------------------------------------------ cambio de camara con aceleracion
  /* Port PASO A PASO de montage.plan_ramps (_ramp_side, _ramp_pick, _ramp_force): en un cambio de
     camara de material intercalado (A/B/A, las dos partes del intercalado) o partido a media linea,
     el VIDEO de un lado acelera (x2) mientras el audio sigue a 1x. ramp_out = el video sigue
     d*(v-1) s despues de "out", comprimido en los ultimos d s; ramp_in = empieza d*(v-1) s antes de
     "in" y alcanza a su audio en los primeros d s. Solo donde lo acelerado no tiene palabras y la
     fuente tiene esos cuadros; la mas larga (0.30 s, luego hasta 0.15 s); con la misma, el lado donde
     el orador sigue a media actuacion y luego el de salida. El usuario manda (clip.rampUser en el
     clip que ENTRA): apagada, o prendida aunque no haya pausa limpia (el lado con mas aire). */
  const RAMP = { dur: 0.30, min: 0.15, step: 0.05, speed: 2.0, tol: 0.04, room: 0.30, guard: 0.05, ctx: 1.5 };
  function rampOf(r) {
    if (!r || typeof r !== "object") return null;
    const d = +r.dur, v = r.speed == null ? RAMP.speed : +r.speed;
    if (!(d > 0.01) || !(v > 1.0001) || !isFinite(d) || !isFinite(v)) return null;
    return { dur: round3(Math.min(1.5, d)), speed: round3(Math.min(4, v)) };
  }
  function rampDurs() { const out = []; for (let d = RAMP.dur; d >= RAMP.min - 1e-9; d -= RAMP.step) out.push(round3(d)); return out; }
  /** {sid: [[inicio, fin] de TODAS las palabras, duracion]} (montage._ramp_spans); sin palabras
      conocidas de un clip no hay entrada (no se decide a ciegas). */
  function rampSpans(clips, o) {
    const opt = o || {}, W = opt.words || {}, out = {};
    (clips || []).forEach(c => {
      if (!c || !c.sid || out[c.sid] || !Array.isArray(W[c.sid])) return;
      const ws = W[c.sid].map(w => [+w.start, +w.end]).filter(([a, b]) => isFinite(a) && isFinite(b) && b >= a).sort((x, y) => x[0] - y[0] || x[1] - y[1]);
      const d0 = +((opt.srcdur && opt.srcdur[c.sid]) || c.srcdur || 0);
      out[c.sid] = [ws, Math.max(d0 > 0 ? d0 : 0, ws.reduce((a, w) => Math.max(a, w[1]), 0))];
    });
    return out;
  }
  function rampSide(x, side, d, spans, strict) {
    const sp = spans[x.sid]; if (!sp) return null;
    const [ws, dur] = sp, aIn = +x.in, aOut = +x.out, h = d * (RAMP.speed - 1);
    const other = (side === "out" ? x.ramp_in : x.ramp_out) || {};
    if (aOut - aIn < d + (+other.dur || 0) + RAMP.room - 1e-6) return null;
    let lo, hi;
    if (side === "out") { lo = aOut - d; hi = aOut + h; if (hi > dur - RAMP.guard + 1e-6) return null; }
    else { lo = aIn - h; hi = aIn + d; if (lo < RAMP.guard - 1e-6) return null; }
    const tol = RAMP.tol;
    if (strict !== false && ws.some(([a, b]) => b > lo + tol && a < hi - tol)) return null;
    if (side === "out") return ws.some(([a]) => hi - tol <= a && a <= hi + RAMP.ctx);
    return ws.some(([, b]) => lo - RAMP.ctx <= b && b <= lo + tol);
  }
  function rampPick(p, x, spans) {
    for (const d of rampDurs()) {
      const opts = [];
      const o = rampSide(p, "out", d, spans); if (o !== null) opts.push([o ? 0 : 1, 0, "out"]);
      const i = rampSide(x, "in", d, spans); if (i !== null) opts.push([i ? 0 : 1, 1, "in"]);
      if (opts.length) { opts.sort((a, b) => a[0] - b[0] || a[1] - b[1]); return [opts[0][2], d]; }
    }
    return null;
  }
  function rampForce(p, x, spans) {
    let best = null;
    [["out", p], ["in", x]].forEach(([side, y]) => {
      const sp = spans[y.sid]; if (!sp) return;
      const ws = sp[0], aIn = +y.in, aOut = +y.out;
      const free = side === "out" ? aOut - Math.max(aIn, ...ws.filter(w => w[1] <= aOut + RAMP.tol).map(w => w[1]))
        : Math.min(aOut, ...ws.filter(w => w[0] >= aIn - RAMP.tol).map(w => w[0])) - aIn;
      const d = round3(Math.min(RAMP.dur, Math.max(RAMP.min, free)));
      if (rampSide(y, side, d, spans, false) === null) return;
      if (!best || free > best[0] + 1e-9) best = [free, side, d];
    });
    return best ? [best[1], best[2]] : null;
  }
  const camOf = c => { const m = c && c.montage; const v = m ? (m.cam !== undefined ? m.cam : m.setup) : (c && c.cam); return v == null || v === "" ? null : v; };
  /** Uniones de la pista base: clips de video contiguos, en orden. -> [{prev, next, at, k, base}]. */
  function mainJoins(clips) {
    const base = (clips || []).filter(c => c && (c.layer || 0) === 0).sort((a, b) => (+a.start || 0) - (+b.start || 0));
    const out = [];
    for (let k = 1; k < base.length; k++) {
      const p = base[k - 1], n = base[k];
      if (p.type === "image" || n.type === "image") continue;
      if (Math.abs(mClipEnd(p) - (+n.start || 0)) > 0.02) continue;
      if (n.trans && n.trans.type === "crossfade" && +n.trans.dur > 0.05) continue;   // compose: sin rampa en un crossfade
      out.push({ prev: p, next: n, at: round3(+n.start || 0), k, base });
    }
    return out;
  }
  const sameIO = (c, it) => it && c.sid === it.sid && Math.abs(+c.in - +it.in) < 1e-3 && Math.abs(+c.out - +it.out) < 1e-3;
  /** Recalcula las aceleraciones de TODAS las uniones (en sitio) como montage.plan_ramps. Una union
      cuyas palabras aun no llegaron (o. words) conserva lo que traia. o = {words: {sid: [...]},
      srcdur: {sid: s}}. -> marcas por union con cambio de camara: [{at, prevId, nextId, switch: true,
      eligible, on, side, dur, user, possible}]. */
  function syncRamps(clips, o) {
    const opt = o || {}, js = mainJoins(clips), spans = rampSpans(clips, opt), marks = [];
    const known = c => !!spans[c.sid];
    const keep = new Map();
    // lo que ya traen las uniones sin palabras conocidas se queda (el montaje lo decidio con ellas)
    js.forEach(j => { if (!(known(j.prev) && known(j.next))) keep.set(j, { out: rampOf(j.prev.ramp_out), in: rampOf(j.next.ramp_in) }); });
    const joinedOut = new Set(js.map(j => j.prev)), joinedIn = new Set(js.map(j => j.next));
    (clips || []).forEach(c => { if (!c) return; if (!joinedOut.has(c) || known(c)) delete c.ramp_out; if (!joinedIn.has(c) || known(c)) delete c.ramp_in; delete c.rampJoin; });
    js.forEach(j => {
      const p = j.prev, x = j.next, b = j.base, k = j.k;
      const sp = camOf(p), sx = camOf(x);
      const change = sp != null && sx != null && String(sp) !== String(sx);
      const pp = b[k - 2], aba = !!pp && camOf(pp) != null && String(camOf(pp)) === String(sx);
      const icp = !!(p.montage && p.montage.ic), icx = !!(x.montage && x.montage.ic);
      const eligible = change && ((x.montage && x.montage.join === "splice") || aba || (icp && icx));
      const user = x.rampUser === true || x.rampUser === false ? x.rampUser : null;
      let pick = null;
      if (keep.has(j)) {
        const kp = keep.get(j);
        if (user !== false && (kp.out || kp.in)) {
          if (kp.out) { p.ramp_out = kp.out; pick = ["out", kp.out.dur]; } else { x.ramp_in = kp.in; pick = ["in", kp.in.dur]; }
        }
      } else {
        pick = eligible ? rampPick(p, x, spans) : null;
        if (user === false) pick = null;
        else if (user === true && !pick) pick = rampPick(p, x, spans) || rampForce(p, x, spans);
        if (pick) (pick[0] === "out" ? p : x)["ramp_" + pick[0]] = { dur: pick[1], speed: RAMP.speed };
      }
      if (change || pick) {
        x.rampJoin = !!pick;
        const possible = !!pick || (known(p) && known(x) ? !!(rampPick(p, x, spans) || rampForce(p, x, spans)) : true);
        marks.push({ at: j.at, prevId: p._id, nextId: x._id, switch: change, eligible, on: !!pick, side: pick ? pick[0] : null,
          dur: pick ? pick[1] : 0, user: user != null, possible });
      }
    });
    return marks;
  }
  /** El usuario prende o apaga la aceleracion de la union que ENTRA al clip `nextId`. -> {marks, ok}
      (ok false: la pidio y no cabe: sin cuadros de sobra en ningun lado). */
  function toggleRamp(clips, nextId, on, o) {
    const c = (clips || []).find(x => x && x._id === nextId);
    if (!c) return { marks: syncRamps(clips, o), ok: false };
    c.rampUser = !!on;
    const marks = syncRamps(clips, o), mk = marks.find(x => x.nextId === nextId);
    return { marks, ok: !on || !!(mk && mk.on) };
  }

  // ------------------------------------------------------------------ color de acento del proyecto
  /* UN color de acento por proyecto (plan.fx.accent): la palabra activa de los subtitulos y el acento
     de titulos y tarjetas. Por defecto el amarillo de siempre; nunca un color de marca fijo. Cambiarlo
     reemplaza el acento anterior en lo que ya existe (y lo que la IA agregue despues lo recibe en el
     servidor: _accent_carry). Un acento blanco/negro/gris (estilo Minimal) no se busca en los items:
     solo se cambian los campos de acento. */
  const DEFAULT_ACCENT = "#FFD23F";
  function normHex(v) {
    const s = String(v == null ? "" : v).trim();
    let m = /^#?([0-9a-f]{6})$/i.exec(s);
    if (m) return "#" + m[1].toUpperCase();
    m = /^#?([0-9a-f]{3})$/i.exec(s);
    return m ? "#" + m[1].split("").map(x => x + x).join("").toUpperCase() : null;
  }
  function chromatic(hex) {
    const h = normHex(hex); if (!h) return false;
    const n = parseInt(h.slice(1), 16), r = n >> 16 & 255, g = n >> 8 & 255, b = n & 255;
    return Math.max(r, g, b) - Math.min(r, g, b) >= 40;
  }
  function projectAccent(plan) {
    const fx = (plan && plan.fx) || {};
    return normHex(fx.accent) || normHex(fx.captions && fx.captions.accent) || DEFAULT_ACCENT;
  }
  const ACCENT_KEYS = ["accent", "color", "badge_color", "bg", "box", "highlight", "button_color", "border_color", "arrow_color"];
  /** Pone `acc` como acento del proyecto (en sitio). `o.palette` = acento del estilo del director (lo
      que la IA uso). -> numero de colores cambiados en los items. */
  function applyAccent(plan, acc, o) {
    const a = normHex(acc); if (!plan || !a) return 0;
    const fx = plan.fx = plan.fx || {};
    const olds = new Set([projectAccent(plan), normHex(o && o.palette)].filter(x => x && chromatic(x) && x !== a));
    let n = 0;
    const walk = (x, depth) => {
      if (!x || typeof x !== "object" || depth > 4) return;
      if (Array.isArray(x)) { x.forEach(y => walk(y, depth + 1)); return; }
      ACCENT_KEYS.forEach(k => { if (typeof x[k] === "string" && olds.has(normHex(x[k]))) { x[k] = a; n++; } });
      ["lines", "text", "card", "graphics", "items"].forEach(k => { if (x[k] && typeof x[k] === "object") walk(x[k], depth + 1); });
    };
    // tambien transiciones (el glitch se tine del acento) y efectos (= server._accent_apply)
    ["text", "graphics", "cutaways", "transitions", "effects"].forEach(tk => (fx[tk] || []).forEach(it => walk(it, 0)));
    // los titulos con palabras de acento sin color propio lo toman del proyecto
    (fx.text || []).forEach(it => { if (it && (it.accent_words || []).length && !it.accent) { it.accent = a; n++; } });
    // el boton «Seguir» que la IA pinto con el oscuro de la paleta (acento del estilo blanco: Minimal;
    // button_auto) toma el color de tu marca si se lee como boton (= server._accent_apply)
    (fx.graphics || []).forEach(it => {
      if (it && it.kind === "cta" && it.button_auto && it.src !== "user" && engineLum(a) <= 0.85 && normHex(it.button_color) !== a) { it.button_color = a; n++; }
    });
    fx.captions = fx.captions || {};
    // la pastilla propia (aunque sea del color de la marca de antes) no se recolorea
    if (fx.captions.highlight && olds.has(normHex(fx.captions.highlight)) && !pillOwn(plan)) fx.captions.highlight = a;
    fx.captions.accent = a;
    fx.accent = a;
    return n;
  }

  // ------------------------------------------------------------------ exportar (hoja "Exportar")
  /* La hoja de exportacion: formato (el del proyecto), resolucion, calidad, nombre del archivo y
     carpeta. POST /render recibe {resolution, quality, name, folder}; el servidor renderiza en
     DATA/output y copia a <carpeta>/<nombre>.mp4 sin sobrescribir. Aqui solo lo puro: la matriz de
     resoluciones por formato (nunca por encima de la resolucion real de las fuentes; 720p y 1080p
     siempre, 1080p es el export de siempre), el nombre por defecto y saneado, los textos de la
     estimacion y del resultado, y los errores en espanol. */
  const EXPORT_RES = ["720p", "1080p", "1440p", "4k"];
  const EXPORT_RES_LABEL = { "720p": "720p", "1080p": "1080p", "1440p": "1440p", "4k": "4K" };
  const EXPORT_RES_SHORT = { "720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160 };   // lado corto (px)
  const EXPORT_QUALITY = ["standard", "high", "max"];
  const EXPORT_QUALITY_LABEL = { standard: "Estándar", high: "Alta", max: "Máxima" };
  const EXPORT_QUALITY_HINT = {
    standard: "Archivo más ligero: ideal para subir rápido a redes.",
    high: "La calidad recomendada (la de siempre).",
    max: "Máximo detalle para editar o archivar; el archivo pesa más.",
  };
  const EXPORT_DEFAULTS = { resolution: "1080p", quality: "high", shorts: true, name_pattern: "{proyecto} - {fecha}" };
  const FMT_RATIO = { "9:16": [9, 16], "1:1": [1, 1], "4:5": [4, 5], "16:9": [16, 9] };
  const evenPx = v => Math.max(2, Math.round(v / 2) * 2);

  /** Dimensiones de salida [W, H] de `fmt` a la resolucion `res` (lado corto = 720/1080/1440/2160).
      "original" sigue el aspecto de la fuente `src` {w, h} (sin ella, vertical 9:16). */
  function exportDims(fmt, res, src) {
    const s = EXPORT_RES_SHORT[res] || EXPORT_RES_SHORT["1080p"];
    let r = FMT_RATIO[fmt];
    if (!r) {
      const w = src ? +src.w : 0, h = src ? +src.h : 0;
      r = w > 0 && h > 0 ? [w, h] : [9, 16];
    }
    return r[0] <= r[1] ? [evenPx(s), evenPx(s * r[1] / r[0])] : [evenPx(s * r[0] / r[1]), evenPx(s)];
  }
  /** Clase de resolucion de un video de w x h por su lado corto (5 % de tolerancia: 1076 x 1916
      sigue siendo 1080p). Menos de 720p -> "sd"; sin medidas -> null. */
  function resClass(w, h) {
    const s = Math.min(+w || 0, +h || 0);
    if (!(s > 0)) return null;
    for (let i = EXPORT_RES.length - 1; i >= 0; i--) if (s >= EXPORT_RES_SHORT[EXPORT_RES[i]] * 0.95) return EXPORT_RES[i];
    return "sd";
  }
  /** "1920x1080" / "1920 x 1080" / [1920, 1080] / {w, h} / {W, H} -> {w, h} o null. */
  function parseDims(v) {
    if (!v) return null;
    let w, h;
    if (Array.isArray(v)) { w = +v[0]; h = +v[1]; }
    else if (typeof v === "object") { w = +(v.w != null ? v.w : v.W); h = +(v.h != null ? v.h : v.H); }
    else { const m = /^\s*(\d+)\s*[x×]\s*(\d+)\s*$/i.exec(String(v)); if (m) { w = +m[1]; h = +m[2]; } }
    return w > 0 && h > 0 ? { w, h } : null;
  }
  /** La resolucion mas alta que tiene sentido: la del servidor (`max_res`, calculada con las fuentes
      ORIGINALES) o, sin ella, la clase de `src`. Nunca menos de 1080p (el export de siempre). */
  function exportMaxRes(maxRes, src) {
    const i1080 = EXPORT_RES.indexOf("1080p");
    let i = EXPORT_RES.indexOf(maxRes);
    if (i < 0) { const d = parseDims(src); i = d ? EXPORT_RES.indexOf(resClass(d.w, d.h)) : -1; }
    return EXPORT_RES[Math.max(i1080, i)];
  }
  /** Opciones de resolucion del formato: [{id, label, W, H, enabled}]. */
  function exportResOptions(fmt, maxRes, src) {
    const mi = EXPORT_RES.indexOf(exportMaxRes(maxRes, src));
    const d = parseDims(src);
    return EXPORT_RES.map((id, i) => {
      const [W, H] = exportDims(fmt, id, d);
      return { id, label: EXPORT_RES_LABEL[id], W, H, enabled: i <= mi };
    });
  }
  /** La resolucion a usar: la pedida si esta disponible; si no, la mas alta disponible por debajo. */
  function pickExportRes(want, opts) {
    const ok = (opts || []).filter(o => o.enabled).map(o => o.id);
    if (ok.includes(want)) return want;
    const wi = EXPORT_RES.indexOf(want);
    const below = ok.filter(id => EXPORT_RES.indexOf(id) <= wi);
    if (wi >= 0 && below.length) return below[below.length - 1];
    return ok.includes("1080p") ? "1080p" : ok[0] || "1080p";
  }
  /** Por que no se ofrecen las resoluciones altas: "Tu video original es 1080p; 4K no agregaría
      calidad." `srcCls` = clase real de la fuente (resClass); `multi` = varias fuentes (montaje). */
  function exportResNote(srcCls, opts, multi) {
    const off = (opts || []).filter(o => !o.enabled).map(o => o.label);
    if (!off.length) return "";
    const lbl = EXPORT_RES_LABEL[srcCls];
    const who = multi ? (lbl ? `Tus clips originales son ${lbl}` : "Tus clips originales tienen menos resolución")
      : (lbl ? `Tu video original es ${lbl}` : srcCls === "sd" ? "Tu video original es de menos de 720p" : "Tu video original no tiene más resolución");
    const list = off.length === 1 ? off[0] : `${off.slice(0, -1).join(", ")} y ${off[off.length - 1]}`;
    return `${who}; ${list} no ${off.length === 1 ? "agregaría" : "agregarían"} calidad.`;
  }
  /** Etiqueta de un resultado: "2160 × 3840 · 4K". */
  function resLabel(W, H) {
    const w = +W || 0, h = +H || 0;
    if (!(w > 0 && h > 0)) return "";
    const c = resClass(w, h);
    return `${w} × ${h}` + (EXPORT_RES_LABEL[c] ? ` · ${EXPORT_RES_LABEL[c]}` : "");
  }

  // nombre del archivo: sin caracteres prohibidos en macOS/Windows/Linux, sin rutas ni ocultos,
  // sin nombres reservados de Windows y con un largo seguro (bytes UTF-8)
  const NAME_MAX_BYTES = 180;          // deja lugar a " (2)", " - Short 3" y ".mp4" bajo los 255 bytes
  const WIN_RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;
  const utf8Len = s => { let n = 0; for (const ch of s) { const c = ch.codePointAt(0); n += c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4; } return n; };
  /** Nombre de archivo seguro SIN extension (la agrega el servidor): quita / \ : * ? " < > | y los
      caracteres de control, los puntos y espacios de los extremos (nada de ".." ni ocultos) y un
      ".mp4" final. Vacio -> `fallback` (o "Reelfy"). */
  function sanitizeFileName(s, fallback) {
    let t = String(s == null ? "" : s).normalize("NFC")
      .replace(/[\t\n\r\v\f\u2028\u2029]+/g, " ")
      // invisibles que no son parte de ningun texto: control, espacio de ancho cero (U+200B),
      // marcas de direccion (U+200E/F), controles bidi (U+202A-E, U+2066-9: "video\u202Ekcod" se ve
      // como otro nombre) y el BOM. Se CONSERVAN ZWNJ/ZWJ (U+200C/D: emoji compuestos como hombre+ZWJ+laptop y
      // escrituras como el persa) y los selectores de variante (U+FE0F) (= exportkit.sanitize_name)
      .replace(/[\u0000-\u001f\u007f-\u009f\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g, "")
      .replace(/[/\\:*?"<>|]+/g, " ")
      .replace(/\s+/g, " ");
    for (let k = 0; k < 3; k++) t = t.replace(/^[\s.]+|[\s.]+$/g, "").replace(/\.mp4$/i, "");
    t = t.replace(/^[\s.]+|[\s.]+$/g, "");
    if (utf8Len(t) > NAME_MAX_BYTES) {
      let out = "";
      for (const ch of t) { if (utf8Len(out + ch) > NAME_MAX_BYTES) break; out += ch; }
      t = out.replace(/[\s.\u200c\u200d]+$/g, "");      // sin un ZWJ colgando del emoji cortado
    }
    const dot = t.indexOf("."), stem = dot < 0 ? t : t.slice(0, dot);
    if (WIN_RESERVED.test(stem.trim())) t = stem.trim() + "_" + (dot < 0 ? "" : t.slice(dot));
    if (!t) return fallback != null ? sanitizeFileName(fallback, "Reelfy") : "Reelfy";
    return t;
  }
  /** "2026-09-30" (se ordena bien en el Finder y no lleva diagonales). */
  function exportDate(d) {
    const x = d instanceof Date ? d : new Date(d == null ? Date.now() : d);
    const z = n => String(n).padStart(2, "0");
    return `${x.getFullYear()}-${z(x.getMonth() + 1)}-${z(x.getDate())}`;
  }
  /** Nombre del proyecto: su tema (plan.topic o plan.takes.topic) o "Proyecto <id>"; un tema largo
      se corta en una palabra (maximo 60 caracteres). */
  function projectTitle(plan, job) {
    const id = String(job || "").slice(0, 6);
    let t = plan && (plan.topic || (plan.takes && plan.takes.topic) || (plan.studio && plan.studio.title));
    t = typeof t === "string" ? t.replace(/\s+/g, " ").trim() : "";
    if (t.length > 60) { const cut = t.slice(0, 61); const sp = cut.lastIndexOf(" "); t = (sp > 30 ? cut.slice(0, sp) : cut.slice(0, 60)).replace(/[\s,.;:\-–—]+$/, ""); }
    if (t) return t;
    if (plan && plan.montage) return id ? `Armado con IA ${id}` : "Armado con IA";
    return id ? `Proyecto ${id}` : "Proyecto";
  }
  /** Nombre por defecto: "<proyecto> - <fecha>" (o el patron guardado: {proyecto}, {fecha}, {hora}). */
  function defaultExportName(title, date, pattern) {
    const d = date instanceof Date ? date : new Date(date == null ? Date.now() : date);
    const z = n => String(n).padStart(2, "0");
    const p = typeof pattern === "string" && /\{(proyecto|fecha|hora)\}/.test(pattern) ? pattern : EXPORT_DEFAULTS.name_pattern;
    const s = p.replace(/\{proyecto\}/g, () => title || "Reelfy").replace(/\{fecha\}/g, exportDate(d)).replace(/\{hora\}/g, `${z(d.getHours())}.${z(d.getMinutes())}`);
    return sanitizeFileName(s, `Reelfy - ${exportDate(d)}`);
  }
  /** Ultimas elecciones: las de este navegador (`local`) y luego las del servidor (GET
      /settings/export). La carpeta SOLO viene del servidor (la recordada) o del selector nativo. */
  function mergeExportPrefs(server, local) {
    const s = server && typeof server === "object" ? server : {}, l = local && typeof local === "object" ? local : {};
    const pick = (k, ok, def) => ok(l[k]) ? l[k] : ok(s[k]) ? s[k] : def;
    return {
      resolution: pick("resolution", v => EXPORT_RES.includes(v), EXPORT_DEFAULTS.resolution),
      quality: pick("quality", v => EXPORT_QUALITY.includes(v), EXPORT_DEFAULTS.quality),
      shorts: typeof l.shorts === "boolean" ? l.shorts : typeof s.shorts === "boolean" ? s.shorts : EXPORT_DEFAULTS.shorts,
      folder: typeof s.folder === "string" && s.folder.trim() ? s.folder : null,
      name_pattern: typeof s.name_pattern === "string" && s.name_pattern.trim() ? s.name_pattern : EXPORT_DEFAULTS.name_pattern,
    };
  }

  /** Tamano exacto legible (decimal, como el Finder): "498 MB", "1.25 GB", "812 KB". */
  function fmtSize(n) {
    const b = +n || 0;
    if (!(b > 0)) return "";
    if (b < 1e6) return `${Math.max(1, Math.round(b / 1e3))} KB`;
    if (b < 1e9) { const mb = b / 1e6; return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`; }
    return `${(b / 1e9).toFixed(2)} GB`;
  }
  /** Tamano aproximado (2 cifras significativas): "~500 MB", "~1.2 GB", "~62 MB". */
  function approxSize(n) {
    const b = +n || 0;
    if (!(b > 0)) return "";
    if (b < 1e6) return "~1 MB";
    if (b < 1e9) { const mb = b / 1e6; const r = mb < 10 ? +mb.toFixed(1) : +mb.toPrecision(2); return `~${r} MB`; }
    return `~${+(b / 1e9).toFixed(1)} GB`;
  }
  /** Tiempo aproximado: "menos de 1 min", "~3 min", "~1 h 10 min". */
  function approxTime(s) {
    const v = +s;
    if (!isFinite(v) || v < 0) return "";
    if (v < 60) return "menos de 1 min";
    const m = Math.round(v / 60);
    if (m < 60) return `~${m} min`;
    const h = Math.floor(m / 60), r = m % 60;
    return r ? `~${h} h ${r} min` : `~${h} h`;
  }
  /** Linea de la estimacion: "Tardará ~3 min · ~180 MB" ("" sin datos). */
  function estimateText(est) {
    if (!est) return "";
    const t = approxTime(est.seconds), b = approxSize(est.bytes);
    if (!t && !b) return "";
    return [t && `Tardará ${t}`, b].filter(Boolean).join(" · ");
  }
  /** Duracion de un resultado: "1:14", "12:05", "1:02:09". */
  function fmtDuration(s) {
    const v = Math.max(0, Math.round(+s || 0));
    const h = Math.floor(v / 3600), m = Math.floor(v % 3600 / 60), x = v % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
  }
  /** Ruta para mostrar: la carpeta personal como "~" (macOS, Linux y Windows). */
  function prettyPath(p) {
    const s = String(p || "");
    let m = /^\/(?:Users|home)\/[^/]+(\/.*)?$/.exec(s);
    if (m) return "~" + (m[1] || "");
    m = /^[A-Za-z]:\\Users\\[^\\]+(\\.*)?$/i.exec(s);
    if (m) return "~" + (m[1] || "");
    return s;
  }
  /** Ruta corta para mostrar en una linea: "~/…/Clientes/Reelfy" cuando no cabe en `max`
      caracteres (conserva el inicio y los ultimos segmentos completos). */
  function shortPath(p, max) {
    const s = prettyPath(p), m = Math.max(8, max || 44);
    if (s.length <= m) return s;
    const sep = s.includes("\\") && !s.includes("/") ? "\\" : "/";
    const segs = s.split(sep).filter((x, i) => x || i === 0);
    const head = segs[0], rest = segs.slice(1);
    const fit = tail => `${head}${sep}\u2026${sep}${tail.join(sep)}`;
    let tail = [];
    for (let i = rest.length - 1; i >= 1; i--) {
      if (fit([rest[i]].concat(tail)).length > m) break;
      tail.unshift(rest[i]);
    }
    if (!tail.length) { const last = rest[rest.length - 1] || s; return "\u2026" + last.slice(-(m - 1)); }
    return fit(tail);
  }
  /** Ultimo segmento de una ruta (/ o \). */
  function pathBase(p) { const s = String(p || "").replace(/[/\\]+$/, ""); return s.slice(Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\")) + 1); }
  /** Carpeta de una ruta (/ o \). */
  function pathDir(p) { const s = String(p || "").replace(/[/\\]+$/, ""); const i = Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\")); return i > 0 ? s.slice(0, i) : i === 0 ? s.slice(0, 1) : ""; }
  /** "Mostrar en Finder" / "Mostrar en el Explorador" / "Mostrar en la carpeta" segun el sistema. */
  function revealLabel(platform) {
    const p = String(platform || "");
    if (/mac|darwin|iphone|ipad/i.test(p)) return "Mostrar en Finder";
    if (/win/i.test(p)) return "Mostrar en el Explorador";
    return "Mostrar en la carpeta";
  }
  /** Error de un export en espanol -> {code: "space"|"folder"|"recompose"|null, text}. El `code`
      del servidor manda (no_space, recompose_failed, folder; o los del contrato); si no, se reconoce
      el mensaje (ENOSPC, permisos, recomponer). Un mensaje del servidor que ya esta en espanol y es
      para el usuario (sin rastros tecnicos) se respeta: suele decir cuanto falta o que carpeta. */
  const EXPORT_ERR = {
    space: "No hay espacio suficiente en el disco para exportar. Libera espacio o elige una carpeta en otro disco.",
    folder: "No se puede guardar en la carpeta elegida. Elige otra carpeta con «Cambiar…».",
    recompose: "No se pudo preparar el video en alta resolución desde tus clips originales. Puedes exportarlo en 1080p.",
  };
  const ERR_CODE = { space: "space", no_space: "space", folder: "folder", folder_error: "folder", recompose: "recompose", recompose_failed: "recompose" };
  const TECH_RE = /errno|traceback|exception|\berror:|permission denied|no space left|ENOSPC|EACCES|EPERM|read-only file system|not writable|ffmpeg|\.py\b|0x[0-9a-f]{4,}/i;
  const ES_RE = /[áéíóúñ¿¡«»]|\b(no|el|la|los|las|de|del|que|para|tu|tus|en|hay)\b/i;
  function exportError(msg, code) {
    const m = String(msg || "").trim();
    let c = ERR_CODE[code] || null;
    if (!c) {
      if (/no space left|ENOSPC|errno 28|disk (is )?full|espacio (suficiente|insuficiente)|sin espacio|no hay espacio/i.test(m)) c = "space";
      else if (/permission denied|EACCES|EPERM|read-only file system|not writable|no se puede (escribir|guardar)|sin permiso/i.test(m)) c = "folder";
      else if (/recompo|alta resoluci|preparar el video en|preparando el video en/i.test(m)) c = "recompose";
    }
    const own = m && ES_RE.test(m) && !TECH_RE.test(m);
    return { code: c, text: c ? (own ? m : EXPORT_ERR[c]) : (m || "Ocurrió un error al exportar.") };
  }
  /** Calidad: ids del contrato (standard/high/max) y del servidor (estandar/alta/maxima). */
  const QUALITY_ALIAS = { standard: "standard", estandar: "standard", "estándar": "standard", high: "high", alta: "high",
    max: "max", maxima: "max", "máxima": "max" };
  function normQuality(q) { return QUALITY_ALIAS[String(q == null ? "" : q).toLowerCase()] || null; }
  /** El id que entiende el servidor para la calidad `q`: el de su lista (GET /export/options) o, sin
      lista, el del servidor de Reelfy (estandar/alta/maxima). */
  function serverQuality(q, ids) {
    const c = normQuality(q) || "high";
    const list = (ids || []).map(x => (x && typeof x === "object" ? x.id : x)).filter(Boolean);
    const hit = list.find(x => normQuality(x) === c);
    return hit || { standard: "estandar", high: "alta", max: "maxima" }[c];
  }
  /** Filas de resolucion del servidor (GET /export/options: formats[fmt].resolutions) -> el mismo
      formato que exportResOptions (+ reason, recompose); null si no sirven. */
  function serverResRows(rows) {
    if (!Array.isArray(rows)) return null;
    const out = rows.filter(r => r && EXPORT_RES.includes(String(r.id).toLowerCase()) && +r.W > 0 && +r.H > 0).map(r => ({
      id: String(r.id).toLowerCase(), label: r.label || EXPORT_RES_LABEL[String(r.id).toLowerCase()], W: +r.W, H: +r.H,
      enabled: r.enabled !== false, reason: r.reason || "", recompose: !!r.recompose,
      warn: typeof r.warn === "string" ? r.warn : "" }));
    out.sort((a, b) => EXPORT_RES.indexOf(a.id) - EXPORT_RES.indexOf(b.id));
    return out.length ? out : null;
  }
  /** Un aviso para las resoluciones apagadas a partir de sus motivos: los que solo cambian en la
      resolucion se juntan ("…; 1440p y 4K no agregarían calidad."); si no, el primero. */
  function combineReasons(rows) {
    const off = (rows || []).filter(r => !r.enabled && r.reason);
    if (!off.length) return "";
    const tpl = off.map(r => r.reason.split(r.label).join("{r}"));
    const dot = t => /[.!?…]$/.test(t) ? t : t + ".";
    if (off.length > 1 && tpl.every(t => t === tpl[0]) && tpl[0].split("{r}").length === 2) {
      const list = `${off.slice(0, -1).map(r => r.label).join(", ")} y ${off[off.length - 1].label}`;
      return dot(tpl[0].replace("{r}", list).replace(/agregaría\b/, "agregarían").replace(/\bes posible\b/, "son posibles"));
    }
    return dot(off[0].reason);
  }
  /** Ultimas elecciones del servidor en cualquiera de sus formas: GET /export/settings ({last,
      folder}), GET /settings/export (plano) u options.defaults -> {resolution, quality, shorts,
      folder, folder_label, name_pattern}. */
  function normSettings(s) {
    if (!s || typeof s !== "object") return {};
    const last = s.last && typeof s.last === "object" ? s.last : s.defaults && typeof s.defaults === "object" ? s.defaults : s;
    const f = s.folder;
    const out = {};
    const r = String(last.resolution || last.res || "").toLowerCase();
    if (EXPORT_RES.includes(r)) out.resolution = r;
    const q = normQuality(last.quality); if (q) out.quality = q;
    if (typeof last.shorts === "boolean") out.shorts = last.shorts;
    if (f && typeof f === "object") { if (typeof f.path === "string" && f.path) { out.folder = f.path; out.folder_label = f.label || null; } }
    else if (typeof f === "string" && f) out.folder = f;
    else if (typeof last.folder === "string" && last.folder) out.folder = last.folder;
    if (typeof s.name_pattern === "string") out.name_pattern = s.name_pattern;
    return out;
  }

  // ------------------------------------------------------------------ Estilo: claridad de ajustes
  /* Logica pura de la pestana Estilo: el resorte y la pastilla del motor (text._boxed_rrect), la
     letra en contraste dentro de la pastilla (text._contrast), que filas de color usa cada estilo de
     subtitulos (text.CAPTION_PRESETS[*].active), el cuadro y las palabras de muestra, y la copy de
     cada ajuste (scripts/motion/ui_copy.json, que llega en /fx/schema como `ui`). */

  /** ease.spring del motor: resorte subamortiguado a un escalon (0 -> 1); t en segundos. */
  function spring(t, freq, damping) {
    t = +t;
    if (!(t > 0)) return 0;
    const f = freq == null ? 3.2 : +freq;
    const z = Math.min(0.999, Math.max(0.01, damping == null ? 0.42 : +damping));
    const w = 2 * Math.PI * f, wd = w * Math.sqrt(1 - z * z);
    return 1 - Math.exp(-z * w * t) * (Math.cos(wd * t) + (z * w / wd) * Math.sin(wd * t));
  }
  /** text._word_rect: caja de la pastilla de una palabra [l, t, r, b] (px). ink = tinta {l, r}
      relativa al origen de la palabra; (ox, oy) = origen y linea base; cap = altura de la tinta sobre
      la base sin acentos; fs = tamano de la letra. */
  function pillWordRect(ink, ox, oy, cap, fs) {
    return [ox + ink.l - 0.2 * fs, oy - cap - 0.19 * fs, ox + ink.r + 0.2 * fs, oy + 0.24 * fs];
  }
  /** text._pill_word + text._boxed_rrect: la pastilla en t -> {x, y, w, h, r, i} o null. Entre
      palabras se DESLIZA con resorte (4.0 / 0.78); la primera crece de 0.6 a 1 (3.6 / 0.62); radio
      0.22 * fs * escala. rects = pillWordRect de cada palabra; starts = inicio de cada palabra;
      gdy = desplazamiento vertical del grupo (entrada). */
  function pillAt(rects, starts, t, fs, gdy) {
    let i = -1;
    for (let k = 0; k < (starts || []).length; k++) if (t >= +starts[k] - 0.03) i = k;
    if (i < 0 || !rects || !rects[i]) return null;
    const cur = rects[i], u = t - (+starts[i] - 0.03);
    let r = cur, s = 1;
    if (i > 0 && rects[i - 1]) { const pv = rects[i - 1], k = spring(u, 4.0, 0.78); r = pv.map((v, j) => v + (cur[j] - v) * k); }
    else s = 0.6 + 0.4 * spring(u, 3.6, 0.62);
    const cx = (r[0] + r[2]) / 2, cy = (r[1] + r[3]) / 2 + (+gdy || 0), w = (r[2] - r[0]) * s, h = (r[3] - r[1]) * s;
    return { x: cx - w / 2, y: cy - h / 2, w, h, r: 0.22 * fs * s, i };
  }
  /** text._contrast: letra legible sobre `hex` (luminancia del motor sobre sRGB, umbral 0.55). */
  function contrastOn(hex) {
    const h = normHex(hex) || "#FFFFFF", n = parseInt(h.slice(1), 16);
    const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 ? "#141414" : "#FFFFFF";
  }
  /** Filas de color de cada estilo de subtitulos, en orden (de CAPTION_PRESETS[*].active):
      k = clave en fx.captions, role = que pinta (dibujo de "donde va"), mode = control. */
  const CAPTION_ROLES = {
    color: [{ k: "color", role: "text", mode: "swatches" }, { k: "accent", role: "active", mode: "linked" }],
    wipe: [{ k: "color", role: "text", mode: "swatches" }, { k: "accent", role: "fill", mode: "linked" }],
    pill: [{ k: "highlight", role: "pill", mode: "pill" }, { k: "color", role: "text", mode: "swatches" }],
    none: [{ k: "color", role: "text", mode: "swatches" }],
  };
  function captionColorRoles(preset) {
    if (preset === "none") return [];
    const P = CAPTION_PRESETS[preset] || CAPTION_PRESETS.clean_bold;
    return (CAPTION_ROLES[P.active] || CAPTION_ROLES.none).map(r => Object.assign({}, r));
  }
  /** Nota de regla de un estilo de subtitulos (ui.notes["captions.<preset>"]) o null. */
  function captionStyleNote(ui, preset) {
    const n = ui && ui.notes && ui.notes["captions." + preset];
    return typeof n === "string" && n ? n : null;
  }
  /** Que palabras de «ya está en» pinta cada fila de color (el dibujito de la fila): el texto, las que
      no estas diciendo (1 y 3), salvo en un estilo sin palabra resaltada («Discreto»: las tres); la
      palabra que dices y la pastilla, la 2; el relleno del karaoke, la 1 y media 2. */
  function colorWhereWords(role, preset) {
    const P = CAPTION_PRESETS[preset];
    if (role === "text") return P && P.active === "none" ? [0, 1, 2] : [0, 2];
    if (role === "active" || role === "pill") return [1];
    if (role === "fill") return [0, 1];
    return [];
  }
  /** Ajustes de los subtitulos que el motor ignora con un estilo (text._draw_group_body): «Centrar la
      frase» solo cuenta cuando las palabras salen una por una; la palabra clave no se ve en Karaoke
      (el relleno la pinta encima). -> false = no hace nada con ese estilo (la fila se apaga y dice por que). */
  function captionOptionApplies(preset, k) {
    const P = CAPTION_PRESETS[preset];
    if (!P) return true;
    if (k === "recenter") return P.reveal === "word";
    if (k === "kw_accent") return P.active !== "wipe";
    return true;
  }
  /** Tarjeta de la galeria que corresponde a la configuracion: "none" si estan apagados. */
  function captionCardOf(cfg) {
    const c = cfg || {};
    if (c.enabled === false || c.preset === "none") return "none";
    return CAPTION_PRESETS[c.preset] ? c.preset : "clean_bold";
  }
  /** La pastilla sigue al color de la marca: highlight vacio, o igual a la marca SIN que el usuario la
      haya separado (`own` = pillOwn(plan): quito «Igual al color de tu marca» o eligio su color). */
  function highlightLinked(cfg, brand, own) {
    const h = cfg && cfg.highlight;
    return !h || (!own && normHex(h) === normHex(brand));
  }
  /** El usuario separo la pastilla de la marca (plan.studio.pillOwn): vive en el plan, asi que se
      guarda con el proyecto, se deshace con ⌘Z y no pasa a otro proyecto. */
  const pillOwn = plan => !!(plan && plan.studio && plan.studio.pillOwn);
  /** Marca o desmarca la pastilla como propia (dentro del mismo paso de deshacer que el color). */
  function setPillOwn(plan, on) {
    if (!plan) return;
    const st = Object.assign({}, plan.studio);
    if (on) st.pillOwn = true; else delete st.pillOwn;
    plan.studio = st;
  }
  /** El elemento se pinta con el color de la marca `acc` (lo que applyAccent recolorea): un titulo
      con palabras destacadas (o marcador / antetitulo) sin color propio o con el de la marca; un
      grafico con algun color igual al de la marca. Un elemento con su propio color no lo muestra:
      los ejemplos de «Color de tu marca» no deben usarlo. */
  function paintsAccent(track, it, acc) {
    const a = normHex(acc); if (!it || !a) return false;
    if (track === "text") {
      if (!((it.accent_words || []).length || ["highlight_marker", "kicker"].includes(it.preset))) return false;
      return !it.accent || normHex(it.accent) === a;
    }
    return ACCENT_KEYS.some(k => typeof it[k] === "string" && normHex(it[k]) === a);
  }
  /** Luminancia como el motor (ctx.luminance: pesos 709 sobre sRGB, sin linealizar). */
  function engineLum(hex) {
    const n = parseInt((normHex(hex) || "#FFFFFF").slice(1), 16);
    return 0.2126 * ((n >> 16) & 255) / 255 + 0.7152 * ((n >> 8) & 255) / 255 + 0.0722 * (n & 255) / 255;
  }
  /** = text.caption_contrast: (trazo, sombra) de un grupo de subtitulos de tinta `color` sobre un fondo de
      luminancia `bgLum` (0..1, null = no se sabe). Tinta clara (>= 0.6) sobre fondo claro (>= 0.55): al
      menos un trazo oscuro fino (2) y la sombra completa (1.0); si no, lo del estilo. */
  const CONTRAST = { ink: 0.6, lum: 0.55, stroke: 2.0, shadow: 1.0 };
  function captionContrast(color, bgLum, stroke, shadow) {
    const st = +stroke || 0, sh = +shadow || 0, lum = bgLum == null ? null : +bgLum;
    if (lum == null || !isFinite(lum) || lum < 0 || engineLum(color || "#FFFFFF") < CONTRAST.ink || lum < CONTRAST.lum) return { stroke: st, shadow: sh };
    return { stroke: Math.max(st, CONTRAST.stroke), shadow: Math.max(sh, CONTRAST.shadow) };
  }
  /** Color del boton «Seguir» de la invitacion (graphics._cta_follow): button_color, si no `color`,
      si no rojo; uno casi blanco (> 0.85) no se ve sobre la pastilla blanca y el motor lo pinta rojo. */
  const CTA_BTN_FALLBACK = "#FF3B5C";
  function ctaButtonColor(it) {
    const c = normHex(it && it.button_color) || normHex(it && it.color) || CTA_BTN_FALLBACK;
    return engineLum(c) > 0.85 ? CTA_BTN_FALLBACK : c;
  }
  /** Palabras dichas (orden de tiempo) que NO caen en un corte habilitado. */
  function spokenWords(phrases, cuts) {
    const ws = [];
    (phrases || []).forEach(p => (p || []).forEach(w => {
      if (w && isFinite(+w.start) && isFinite(+w.end) && String(w.text == null ? "" : w.text).trim()) ws.push(w);
    }));
    ws.sort((a, b) => +a.start - +b.start);
    const on = (cuts || []).filter(c => c && cutOn(c));
    const rm = on.length ? wordsInCuts(ws.map(w => ({ start: +w.start, end: +w.end })), on).removed : [];
    return ws.filter((w, i) => !rm[i]);
  }
  function sampleRows(rows) {
    let r = rows;
    if (typeof r === "string") { try { r = JSON.parse(r); } catch (e) { return null; } }
    return Array.isArray(r) && r.length ? r : null;
  }
  const rowLuma = v => (Array.isArray(v) ? 0.2126 * +v[0] + 0.7152 * +v[1] + 0.0722 * +v[2] : +v);
  /** Toma para los ejemplos: >= 1 s, >= 6 palabras dichas fuera de cortes y, entre esas, la de
      fondo mas oscuro en la franja de los subtitulos (shots[i].rows en floor(capY * 16) +- 1), para
      que la letra blanca se lea. Empate: la primera. Sin `rows`: la primera con palabras.
      -> {index, t0} (t0 = primera palabra dicha de la toma, tiempo original). */
  function pickSampleShot(plan, capY) {
    const p = plan || {}, words = spokenWords(p.phrases, p.cuts);
    const dur = +p.duration || (words.length ? +words[words.length - 1].end : 0);
    let shots = (p.shots || []).filter(s => s && isFinite(+s.start));
    if (!shots.length) shots = [{ start: 0, end: dur }];
    const y = capY == null || !isFinite(+capY) ? 0.72 : Math.min(1, Math.max(0, +capY));
    let best = null, bestL = Infinity, first = null;
    shots.forEach((s, i) => {
      const a = +s.start, b = s.end != null && isFinite(+s.end) ? +s.end : (shots[i + 1] ? +shots[i + 1].start : dur);
      if (b - a < 1) return;
      const inside = words.filter(w => +w.start >= a && +w.start < b);
      if (inside.length < 6) return;
      const cand = { index: i, t0: +inside[0].start };
      if (!first) first = cand;
      const rows = sampleRows(s.rows);
      if (!rows) return;
      const k = Math.min(rows.length - 1, Math.floor(y * rows.length));
      let sum = 0, n = 0;
      for (let j = k - 1; j <= k + 1; j++) { const v = rowLuma(rows[Math.max(0, Math.min(rows.length - 1, j))]); if (isFinite(v)) { sum += v; n++; } }
      if (!n) return;
      const L = sum / n;
      if (L < bestL - 1e-9) { bestL = L; best = cand; }
    });
    return best || first || { index: 0, t0: words.length ? +words[0].start : 0 };
  }
  /** Otra toma para comparar con la de muestra (igualar tomas): la siguiente de >= 1 s con un
      instante fuera de los cortes habilitados (si no hay, la anterior). -> {index, t} o null. */
  function otherShot(plan, index) {
    const p = plan || {}, dur = +p.duration || 0;
    const shots = (p.shots || []).filter(s => s && isFinite(+s.start));
    const cuts = (p.cuts || []).filter(c => c && c.enabled !== false);
    const inCut = t => cuts.some(c => t >= +c.start && t < +c.end);
    const at = i => {
      const s = shots[i], a = +s.start, b = s.end != null && isFinite(+s.end) ? +s.end : (shots[i + 1] ? +shots[i + 1].start : dur);
      if (b - a < 1) return null;
      for (let j = 0; ; j++) {
        const t = Math.round((a + 0.3 + j * 0.1) * 1000) / 1000;
        if (t >= b - 0.1) return null;
        if (!inCut(t)) return { index: i, t };
      }
    };
    for (let i = index + 1; i < shots.length; i++) { const r = at(i); if (r) return r; }
    for (let i = Math.min(index, shots.length) - 1; i >= 0; i--) { const r = at(i); if (r) return r; }
    return null;
  }
  /** Palabras de muestra: las dichas en [t0, t0 + len] fuera de cortes habilitados; si son menos
      de 6, la ventana crece hasta 4 s. -> {words (tiempos originales), t0, t1, len}. */
  function sampleWindow(phrases, cuts, t0, len) {
    const ws = spokenWords(phrases, cuts), a = +t0 || 0;
    const pick = L => ws.filter(w => +w.start >= a - 1e-6 && +w.start < a + L);
    let L = len == null ? 2.6 : Math.max(0.2, +len), out = pick(L);
    while (out.length < 6 && L < 4 - 1e-9) { L = Math.min(4, L + 0.2); out = pick(L); }
    const words = out.map(w => Object.assign({}, w, { start: +w.start, end: +w.end }));
    return { words, t0: words.length ? words[0].start : a, t1: words.length ? words[words.length - 1].end : a + L, len: L };
  }
  /** Opcion de una lista [[valor, etiqueta], ...] mas cercana a v (null solo coincide con null). */
  function nearestChoice(choices, v) {
    const cs = (choices || []).filter(c => Array.isArray(c));
    if (v == null) return cs.find(c => c[0] == null) || null;
    if (typeof v !== "number") return cs.find(c => c[0] === v) || null;
    let best = null, bd = Infinity;
    cs.forEach(c => { if (typeof c[0] !== "number") return; const d = Math.abs(c[0] - v); if (d < bd - 1e-12) { bd = d; best = c; } });
    return best;
  }
  /** Ejemplo escrito con los datos reales del usuario: llena {nombre} de un ex_tpl; si falta un
      valor devuelve `fallback` (el ex_ui fijo). Nunca deja llaves. */
  function fillExample(tpl, values, fallback) {
    const fb = fallback == null ? "" : String(fallback);
    if (typeof tpl !== "string" || !tpl) return fb;
    let miss = false;
    const out = tpl.replace(/\{(\w+)\}/g, (m, k) => {
      const v = values ? values[k] : null;
      if (v == null || v === "" || (typeof v === "number" && !isFinite(v))) { miss = true; return m; }
      return String(v);
    });
    return miss || /[{}]/.test(out) ? fb : out;
  }
  /** Numero para leer: entero, o un decimal si es menor que 10 (6.4). */
  function humanNum(x) {
    const a = Math.abs(x);
    if (a >= 10 || Math.abs(a - Math.round(a)) < 0.05) return String(Math.round(a));
    return (Math.round(a * 10) / 10).toFixed(1);
  }
  /** Valor de Avanzado en unidades humanas: adv = {scale, fmt, min, effective?, zero?}.
      0.73 con {scale: 100, fmt: "{v} % desde arriba"} -> "73 % desde arriba". effective
      "size*scale" multiplica por la escala del estilo (ctx.scale o la de ctx.preset). */
  function humanValue(adv, v, ctx) {
    if (!adv || v == null || v === "" || !isFinite(+v)) return "";
    const c = ctx || {};
    let x = +v * (adv.scale != null ? +adv.scale : 1);
    if (adv.effective === "size*scale") {
      const P = CAPTION_PRESETS[c.preset];
      x *= c.scale != null ? +c.scale : P ? P.scale : 1;
    }
    if (adv.zero && Math.abs(x) < 1e-9) return String(adv.zero);
    let s = humanNum(x);
    if (s !== "0" && x < 0) s = "−" + s;
    else if (s !== "0" && x > 0 && adv.min != null && +adv.min < 0) s = "+" + s;
    return String(adv.fmt || "{v}").replace("{v}", s);
  }
  /** Copy de un ajuste (ui_copy.json) resuelta para el contexto (preset, kind o pista): etiqueta,
      que hace, nota, nivel y ejemplo. Sin entrada: {missing: true} con la etiqueta de respaldo. */
  function copyFor(ui, id, ctx) {
    const U = ui || {}, c = ctx || {};
    let e = (U.params && U.params[id]) || (U.ui && U.ui[id]) || null;
    if (!e && U.aliases && U.aliases[id] && U.params) e = U.params[U.aliases[id]] || null;
    if (!e) return { id, label: c.fallback || id, help: "", note: null, level: "basic", ex: "ninguno", ex_ui: "", ex_tpl: null, missing: true };
    const keys = [c.preset, c.kind, c.track].filter(k => k != null && k !== "");
    const by = (m, d) => { if (m && typeof m === "object") for (const k of keys) if (Object.prototype.hasOwnProperty.call(m, k)) return m[k]; return d; };
    return {
      id, label: by(e.label_by, e.label), help: by(e.help_by, e.help || ""), note: by(e.note_by, null),
      level: by(e.level_by, e.level || "basic"), ex: e.ex || "ninguno", ex_ui: e.ex_ui || "", ex_tpl: e.ex_tpl || null,
      units: e.units || "", basic_choices: e.basic_choices || null, relative_choices: e.relative_choices || null,
      adv: e.adv || null, adv_label: e.adv_label || null, missing: false,
    };
  }
  const CAPTION_COLOR_IDS = ["captions.color", "captions.accent", "captions.highlight"];
  /** Filas que se dibujan: sin las ocultas, sin las avanzadas en Basico y, en subtitulos, solo las
      filas de color que usa el estilo (en su orden) en el lugar de la primera fila de color. */
  function visibleRows(ui, ids, level, ctx) {
    const c = ctx || {}, adv = level === "advanced";
    let list = (ids || []).slice();
    const at = list.findIndex(i => CAPTION_COLOR_IDS.includes(i));
    if (c.preset != null && at >= 0) {
      const roles = captionColorRoles(c.preset).map(r => "captions." + r.k);
      list = list.filter(i => !CAPTION_COLOR_IDS.includes(i));
      list.splice(at, 0, ...roles);
    }
    return list.filter(i => { const cp = copyFor(ui, i, c); return cp.level !== "hidden" && (adv || cp.level !== "advanced"); });
  }
  /** Nombre de un color en palabras (resumen del estilo, nombres de las muestras). */
  function colorName(hex) {
    const h = normHex(hex); if (!h) return "";
    const n = parseInt(h.slice(1), 16), r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn;
    const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
    if (l >= 0.93) return "blanco";
    if (l <= 0.1) return "negro";
    if (s < 0.18) return l > 0.62 ? "gris claro" : l < 0.3 ? "gris oscuro" : "gris";
    let hue = 0;
    if (mx === r) hue = 60 * (((g - b) / d) % 6); else if (mx === g) hue = 60 * ((b - r) / d + 2); else hue = 60 * ((r - g) / d + 4);
    if (hue < 0) hue += 360;
    if (hue < 14 || hue >= 340) return "rojo";
    if (hue < 40) return l < 0.35 ? "café" : "naranja";
    if (hue < 66) return "amarillo";
    if (hue < 160) return "verde";
    if (hue < 196) return "turquesa";
    if (hue < 245) return "azul";
    if (hue < 292) return "morado";
    return "rosa";
  }
  /** Plantilla de marca (POST /presets) con lo que define el estilo del video, incluido el color
      de la marca (antes se perdia al aplicarla en otro video). */
  function presetFromPlan(plan, o) {
    const f = (plan && plan.fx) || {}, m = f.music || {}, x = o || {};
    return { style: x.style, intensity: x.intensity, captions: clone(f.captions || {}), grade: clone(f.grade || {}),
      music: { source: m.source, mood: m.mood, gain_db: m.gain_db, duck_db: m.duck_db, prompt: m.prompt },
      accent: projectAccent(plan) };
  }

  // ------------------------------------------------------------------ Estilo: edicion con IA, musica, quien sale, formato
  /** Lo que muestra la tarjeta de cada estilo de edicion (director.STYLES): sus subtitulos, su primer
      estilo de titulo con su letra (TYPE_KIT por PRESET_ROLE) y su color. Paridad: test_estilo.py. */
  const DIRECTOR_CARDS = {
    dinamico: { captions: { preset: "clean_bold", y: 0.73, size: 0.064, font: "Poppins-ExtraBold", case: "none", max_words: 3 }, title: "stack", titleFont: "Anton", look: "warm_clean",
      grade: { look: "warm_clean", vignette: 0.15, grain: 0.022, sharpen: 0.15 } },
    elegante: { captions: { preset: "clean_bold", y: 0.73, size: 0.058, font: "Poppins-Bold", case: "none", max_words: 3 }, title: "serif_elegant", titleFont: "DMSerifDisplay", look: "cinematic",
      grade: { look: "cinematic", vignette: 0.24, grain: 0.03, sharpen: 0.1 } },
    minimal: { captions: { preset: "clean_bold", y: 0.73, size: 0.06, font: "Montserrat-ExtraBold", case: "none", max_words: 3 }, title: "title_pop", titleFont: "Montserrat-ExtraBold", look: "natural",
      grade: { look: "natural", vignette: 0.0, grain: 0.0, sharpen: 0.0, enabled: false, match_shots: false } },
    energetico: { captions: { preset: "bold_caps", y: 0.725, size: 0.056, font: "Poppins-Black", case: "upper", max_words: 3 }, title: "stack", titleFont: "Anton", look: "bright_pop",
      grade: { look: "bright_pop", vignette: 0.12, grain: 0.02, sharpen: 0.2 } },
  };
  const DIRECTOR_ORDER = ["dinamico", "elegante", "minimal", "energetico"];
  /* Lo que el director CONSERVA del usuario al dirigir o re-dirigir con otro estilo (director.py:
     _keep_captions, _grade_tuned / _keep_grade): cada campo de los subtitulos que el usuario cambio
     respecto al estilo con que se dirigio, y todo el color si lo ajusto. Las tarjetas de «Estilo de
     edición» y «Qué cambia» muestran ESO (no el estilo puro). Paridad: test_estilo.py. */
  const GRADE_NEUTRAL = { strength: 1.0, exposure: 0.0, temperature: 0.0, tint: 0.0, saturation: 1.0, contrast: 0.0, clarity: null };
  const CAP_LOOK = ["enabled", "preset", "font", "color", "case", "max_words", "highlight", "stroke", "shadow", "box", "kw_accent", "recenter", "max_lines"];
  const dirStyleOf = st => (DIRECTOR_CARDS[st] ? st : "dinamico");
  const sameKeep = (a, b) => (a !== null && b !== null && typeof a === "object" && typeof b === "object" ? JSON.stringify(a) === JSON.stringify(b) : a === b);
  /* = director._GRADE_PREV: el color que dejaba cada estilo en versiones anteriores (sin tocar = no es
     del usuario; un Minimal de v0.6.0 con correccion encendida toma el Minimal nuevo al re-dirigir). */
  const GRADE_PREV = { minimal: [{ enabled: true, look: "natural", match_shots: true, vignette: 0.08, grain: 0.0, sharpen: 0.1 }] };
  const gradeDiffers = (g, base) => Object.keys(g).some(k => {
    if (!Object.prototype.hasOwnProperty.call(base, k)) return false;
    const d = base[k], v = g[k];
    if (typeof d === "number" && typeof v === "number") return Math.abs(v - d) > 1e-4;
    return !sameKeep(v, d);
  });
  /** El color del plan lo ajusto el usuario (algun valor difiere del color del estilo `style`, el de
      ahora y los de versiones anteriores). */
  function gradeTuned(g, style) {
    if (!g || typeof g !== "object" || Array.isArray(g)) return false;
    const st = dirStyleOf(style), gc = DIRECTOR_CARDS[st].grade;
    const bases = [{ enabled: gc.enabled !== false, look: gc.look, match_shots: gc.match_shots !== false, vignette: gc.vignette, grain: gc.grain, sharpen: gc.sharpen }]
      .concat(GRADE_PREV[st] || []);
    return bases.every(b => gradeDiffers(g, Object.assign({}, GRADE_NEUTRAL, b)));
  }
  /** Subtitulos y color que deja el director al dirigir `plan` con `style`:
      {captions, capsKept: [campos del usuario], grade, gradeKept}. */
  function directorOutcome(plan, style) {
    const d = (plan && plan.director) || {}, f = (plan && plan.fx) || {}, old = dirStyleOf(d.style), st = dirStyleOf(style);
    const locked = new Set(Array.isArray(d.locked) ? d.locked : []);
    const oc = f.captions && typeof f.captions === "object" ? f.captions : {};
    let captions, capsKept;
    if (locked.has("captions")) { captions = clone(oc); capsKept = CAP_LOOK.filter(k => oc[k] != null); }
    else {
      const c0 = DIRECTOR_CARDS[old].captions;
      const base = { enabled: true, preset: c0.preset, font: c0.font, color: "#FFFFFF", case: c0.case, max_words: c0.max_words };
      captions = Object.assign({ enabled: true }, clone(DIRECTOR_CARDS[st].captions));
      capsKept = [];
      CAP_LOOK.forEach(k => { if (oc[k] != null && !sameKeep(oc[k], base[k])) { captions[k] = clone(oc[k]); capsKept.push(k); } });
    }
    const gc = DIRECTOR_CARDS[st].grade;
    const gradeKept = locked.has("grade") || gradeTuned(f.grade, old);
    const grade = gradeKept ? clone(f.grade || {}) : { enabled: gc.enabled !== false, look: gc.look, match_shots: gc.match_shots !== false, vignette: gc.vignette, grain: gc.grain, sharpen: gc.sharpen };
    return { captions, capsKept, grade, gradeKept };
  }
  /** «Qué cambia con …» con lo que se conserva: el renglon de los subtitulos y el del color cambian
      por «Tus subtítulos se quedan en …» / «tu color se queda en …». opt = la opcion de la copy
      (que_cambia, qc_caps, qc_look); names = {preset(k), look(k)}; T = textos de la copy. */
  function directorQueCambia(opt, outcome, names, T) {
    const o = opt || {}, parts = String(o.que_cambia || "").split(" · ").filter(Boolean), out = outcome || {};
    return parts.map(p => {
      if (p === o.qc_caps && (out.capsKept || []).includes("preset")) return T("dir.keep.caps", { estilo: names.preset(out.captions.preset) });
      if (p === o.qc_look && out.gradeKept) return T("dir.keep.look", { color: out.grade && out.grade.enabled === false ? T("summary.look.off") : names.look(out.grade.look || "warm_clean") });
      return p;
    }).join(" · ");
  }
  /** Cuantos titulos, graficos y efectos visibles lleva el plan (el ejemplo de «Cantidad de efectos»). */
  function directorCounts(plan) {
    const f = (plan && plan.fx) || {}, n = t => (f[t] || []).filter(i => i && i.enabled !== false).length;
    return { titulos: n("text"), graficos: n("graphics"), efectos: n("effects") };
  }
  /** El ejemplo escrito de la cantidad de efectos con los numeros del plan (en singular cuando es 1). */
  function directorExample(tpl, values, fallback) {
    return fillExample(tpl, values, fallback).replace(/(^|[^\d.,])1 (título|gráfico|efecto)s(?=[\s,.]|$)/g, (m, a, w) => `${a}1 ${w}`);
  }
  /** Origen de la musica que se oye: "none" si esta apagada (music._coerce_cfg). */
  function musicSourceOf(m) {
    const x = m || {};
    if (x.enabled === false || x.source === "none") return "none";
    return ["ai", "procedural", "library", "file"].includes(x.source) ? x.source : "ai";
  }
  /** Ritmo que usa el motor (pulsos por minuto; 0 = automatico): 110 sin bpm_lock cuenta como automatico. */
  function musicBpmOf(m) {
    const x = m || {}, b = +x.bpm || 0;
    return b > 0 && (x.bpm_lock || b !== 110) ? b : 0;
  }
  /** El texto para la musica con IA que escribio el usuario (plan.studio.musicPrompt), solo si sigue
      siendo el de la musica del plan; si no (lo eligio la IA: texto en ingles del director), "". */
  function musicPromptShown(plan) {
    const up = plan && plan.studio && plan.studio.musicPrompt, m = (plan && plan.fx && plan.fx.music) || {};
    return typeof up === "string" && (m.prompt || "") === up ? up : "";
  }
  /** Escribe el texto del usuario en la musica y lo recuerda en el plan (dentro del paso de deshacer). */
  function setMusicPrompt(plan, text) {
    const t = String(text == null ? "" : text);
    plan.fx = plan.fx || {};
    plan.fx.music = plan.fx.music || { enabled: true, source: "ai" };
    plan.fx.music.prompt = t;
    const st = Object.assign({}, plan.studio, { musicPrompt: t });
    delete st.musicPromptByUser;
    plan.studio = st;
  }
  /** El margen desenfocado («aire») solo cambia un video MAS ANCHO que el formato de salida
      (video_io.reframe_mode: "+air" solo con src_ar > out_ar * 1.05). Sin medidas todavia: se asume
      que si (no se apaga a ciegas). "Como el original" nunca: el video llena su cuadro. */
  function airApplies(src, fmt) {
    if (fmt === "original") return false;
    const r = FMT_RATIO[fmt] || FMT_RATIO["9:16"];
    if (!src || !(+src.w > 0) || !(+src.h > 0)) return true;
    return +src.w / +src.h > (r[0] / r[1]) * 1.05;
  }
  /** La linea de «La música de tu video» ya creada: en Basico solo «Lista.»; en Avanzado con el ritmo y
      el golpe principal (sus controles son de Avanzado). `T(clave, vars)` = textos de la copy. */
  function musicReadyText(st, level, T) {
    const s = st || {};
    if (level !== "advanced") return T("mus.ready.plain");
    return [s.bpm ? T("mus.ready", { n: s.bpm }) : T("mus.ready.plain"), s.drop ? T("mus.drop", { t: s.drop }) : ""].filter(Boolean).join(" ");
  }
  /** El cambio del plan para un ritmo elegido (0 = automatico): fijo siempre con bpm_lock. */
  const musicBpmSet = v => (+v > 0 ? { bpm: +v, bpm_lock: true } : { bpm: 0, bpm_lock: false });
  /** Tonalidades que entiende el motor (music.valid_keyscale) y su nombre en espanol. */
  const KEY_ROOTS = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
  const KEYSCALES = KEY_ROOTS.flatMap(r => [`${r} major`, `${r} minor`]);
  const SOLFEO = { C: "Do", D: "Re", E: "Mi", F: "Fa", G: "Sol", A: "La", B: "Si" };
  /** "Bb major" -> "Si bemol mayor"; "f#-min" -> "Fa sostenido menor"; invalida -> "". */
  function keyscaleEs(k) {
    if (typeof k !== "string") return "";
    const p = k.replace(/-/g, " ").replace(/\u266F/g, "#").replace(/\u266D/g, "b").trim().split(/\s+/);
    if (p.length !== 2) return "";
    const r = p[0], m = p[1].toLowerCase(), base = SOLFEO[(r[0] || "").toUpperCase()];
    if (!base || r.length > 2 || (r.length === 2 && !"#b".includes(r[1]))) return "";
    const acc = r.length === 2 ? (r[1] === "#" ? " sostenido" : " bemol") : "";
    const mode = ["major", "maj", "mayor"].includes(m) ? "mayor" : ["minor", "min", "menor"].includes(m) ? "menor" : "";
    return mode ? `${base}${acc} ${mode}` : "";
  }
  /** Muestra de 6 s de un ambiente (GET /fx/mood_sample): null/"" = automatico (el del estilo). */
  const moodSampleUrl = (mood, style) => `/fx/mood_sample/${encodeURIComponent(mood || "auto")}?style=${encodeURIComponent(style || "dinamico")}`;
  /** Donde aparecen los datos de la persona: [clave en profile.use, id de la copy]. */
  const PROFILE_USE = [["lower_third", "profile.use.lower_third"], ["follow_cta", "profile.use.follow"], ["booking_cta", "profile.use.booking"],
    ["end_card", "profile.use.end_card"], ["handle_watermark", "profile.use.watermark"]];
  const PROFILE_USE_DEF = { lower_third: true, follow_cta: true, booking_cta: true, end_card: true, handle_watermark: false };
  /** profile.use con sus valores por defecto (los del panel del perfil). */
  const profileUseOf = p => Object.assign({}, PROFILE_USE_DEF, (p && p.use) || {});
  /** url comparable de un enlace (sin esquema, www ni barra final). */
  const profileUrlKey = u => String(u || "").trim().toLowerCase().replace(/^https?:\/\/(www\.)?/, "").replace(/\/+$/, "");
  /** Huella de los DATOS de un perfil (lo que la persona escribio o eligio): sin id, fecha de guardado,
      estado de la descarga, campos internos del Studio (_...) ni la foto/logo que el servidor baja
      solo (media_src 'auto'). Dos copias con la misma huella son el mismo perfil: el panel sabe si hay
      cambios sin guardar y el Studio si el video usa otra version del perfil guardado.
      o.use === false: sin los interruptores de «Dónde aparece» (eleccion de cada video). */
  function profileDataKey(p, o) {
    if (!p || typeof p !== "object") return "";
    const s = v => String(v == null ? "" : v).trim();
    const user = (l, k) => (l.media_src && l.media_src[k] === "user" && l[k] && l[k].sid) ? l[k].sid : "";
    const out = {};
    ["name", "title", "business", "tagline", "phone", "whatsapp", "email", "address"].forEach(k => { out[k] = s(p[k]); });
    out.avatar = p.avatar && p.avatar.sid ? p.avatar.sid : "";
    out.links = (p.links || []).filter(l => l && (l.url || l.handle)).map(l => ({
      platform: s(l.platform), url: profileUrlKey(l.url), handle: s(l.handle).replace(/^@/, "").toLowerCase(),
      display: s(l.display), label: s(l.label), primary: !!l.primary,
      photo: user(l, "photo"), logo: user(l, "logo"),
      off: ["photo", "logo"].filter(k => l.media_off && l.media_off[k]).join(","),
    }));
    if (!o || o.use !== false) out.use = profileUseOf(p);
    return JSON.stringify(out);
  }
  /** Logo del proyecto en un cuadro W x H (index.html drawLogo, opts.logo del motor): pos tl|tr|bl|br,
      size = fraccion del ancho, margen 3 % del ancho. -> {x, y, w, h}. */
  function logoRect(pos, size, W, H, iw, ih) {
    const w = W * (+size > 0 ? +size : 0.14), h = w * ((+ih || 1) / (+iw || 1)), m = W * 0.03;
    const left = pos === "tl" || pos === "bl", top = pos === "tl" || pos === "tr";
    return { x: left ? m : W - w - m, y: top ? m : H - h - m, w, h };
  }
  /** Formatos de salida en el orden de Estilo › Formato y su proporcion (ancho, alto); "original" = la del video. */
  const FORMAT_ORDER = ["9:16", "4:5", "1:1", "16:9", "original"];
  const FORMAT_SHAPE = { "9:16": [9, 16], "4:5": [4, 5], "1:1": [1, 1], "16:9": [16, 9] };

  // ------------------------------------------------------------------ color del video (estilos de color)
  /* El color del export en el cliente: lo que manda POST /fx/look_luts (grade.lut_payload) aplicado
     con la misma aritmetica que grade_array (tablas por canal -> saturacion o B/N -> vinieta).
     lookgl.js hace lo mismo en la GPU; applyLook es la version de referencia (pruebas contra el
     motor) y el respaldo sin WebGL para cuadros quietos. */
  const LOOK_ORDER = ["natural", "warm_clean", "cinematic", "bright_pop", "moody", "bw_contrast"];
  /** Estilos de color sin color (grade.LOOKS[*].mono): el motor ignora en ellos la calidez y el tinte
      (build_lut usa 1.0 en vez de tg) y la intensidad de los colores (grade_array sale antes de la
      matriz de saturacion). Paridad: test_estilo.py. */
  const MONO_LOOKS = ["bw_contrast"];
  const GRADE_COLOR_KEYS = ["temperature", "tint", "saturation"];
  /** El ajuste fino `k` cambia algo con el estilo de color `look`. -> false = la fila se apaga y dice por que. */
  const gradeOptionApplies = (look, k) => !(MONO_LOOKS.includes(look) && GRADE_COLOR_KEYS.includes(k));
  const lk01 = v => (v < 0 ? 0 : v > 1 ? 1 : v);
  const srgbToLin = x => (x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4));
  const linToSrgb = x => { x = Math.max(0, x); return x <= 0.0031308 ? x * 12.92 : 1.055 * Math.pow(x, 1 / 2.4) - 0.055; };
  /** grade._vignette_gray en (u, v) del cuadro (0..1) -> multiplicador 0..1 (orillas oscuras). */
  function vignetteAt(u, v, amount, cx, cy) {
    const x = (u - (cx == null ? 0.5 : cx)) * 2, y = (v - (cy == null ? 0.46 : cy)) * 2 / 1.04;
    let t = lk01((Math.sqrt(x * x * 0.85 + y * y) - 0.45) / 1.0);
    t = t * t * (3 - 2 * t);
    return lk01(1 - lk01(+amount || 0) * 1.6 * Math.pow(t, 1.15));
  }
  /** grade._bw_vig_big: vinieta radial del B/N centrada en (fx, fy) -> multiplicador 0..1. */
  function vignetteBwAt(u, v, amount, fx, fy) {
    const x = (u - fx) / 0.62, y = (v - fy) / 0.5;
    let t = lk01(Math.sqrt(x * x + y * y) - 0.28);
    t = t * t * (3 - 2 * t);
    return lk01(1 - lk01(+amount || 0) * t);
  }
  /** Centro de la vinieta: el B/N la centra en la cara (un poco abajo), los demas en 0.5, 0.46. */
  function lookVigCenter(p, face) {
    const v = (p && p.vig) || {};
    if (v.shape === "bw") return face ? [+face[0], Math.min(v.max_cy == null ? 0.8 : +v.max_cy, +face[1] + (v.face_dy == null ? 0.12 : +v.face_dy))] : [v.cx == null ? 0.5 : +v.cx, v.cy == null ? 0.45 : +v.cy];
    return [v.cx == null ? 0.5 : +v.cx, v.cy == null ? 0.46 : +v.cy];
  }
  /** np.interp(x, linspace(0, 1, n), shape). */
  function shapeAt(x, sh) {
    const n = sh.length - 1;
    if (x <= 0) return sh[0];
    if (x >= 1) return sh[n];
    const f = x * n, i = Math.floor(f);
    return sh[i] + (sh[Math.min(n, i + 1)] - sh[i]) * (f - i);
  }
  /** Inversa de la curva (np.interp(y, shape, linspace(0, 1, n))): la curva es monotona. */
  function shapeInv(y, sh) {
    const n = sh.length - 1;
    if (y <= sh[0]) return 0;
    if (y >= sh[n]) return 1;
    let lo = 0, hi = n;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (sh[m] <= y) lo = m; else hi = m; }
    const d = sh[hi] - sh[lo];
    return (lo + (d > 0 ? (y - sh[lo]) / d : 0)) / n;
  }
  /** np.percentile (interpolacion lineal) de una lista. */
  function percentileOf(vals, q) {
    if (!vals.length) return 0;
    const a = Float64Array.from(vals).sort(), i = (a.length - 1) * q / 100, lo = Math.floor(i), hi = Math.ceil(i);
    return a[lo] + (a[hi] - a[lo]) * (i - lo);
  }
  /** grade._bw_face_gain: ganancia de exposicion del B/N para que la cara (o la escena) quede en su
      luma objetivo. gray = luma 0..255 (W x H, ya con las tablas por canal); face = [cx, cy, w, h];
      step = paso del muestreo (el motor: 8 en la escena y 2 en la cara; una muestra chica: 1). */
  function bwGain(gray, W, H, face, bw, step) {
    const s8 = step || 8, s2 = step || 2;
    let val = null, target = bw.scene, k = bw.scene_k;
    if (face) {
      const [cx, cy, fw, fh] = face.map(Number);
      const x0 = Math.max(0, Math.trunc((cx - 0.28 * fw) * W)), x1 = Math.min(W, Math.trunc((cx + 0.28 * fw) * W));
      const y0 = Math.max(0, Math.trunc((cy - 0.10 * fh) * H)), y1 = Math.min(H, Math.trunc((cy + 0.32 * fh) * H));
      if (x1 - x0 >= 4 && y1 - y0 >= 4) {
        const v = [];
        for (let y = y0; y < y1; y += s2) for (let x = x0; x < x1; x += s2) v.push(gray[y * W + x]);
        val = percentileOf(v, 60) / 255; target = bw.face; k = bw.face_k;
      }
    }
    if (val == null) {
      const v = [];
      for (let y = 0; y < H; y += s8) for (let x = 0; x < W; x += s8) v.push(gray[y * W + x]);
      val = percentileOf(v, 50) / 255;
    }
    return bwGainOf(val, target === bw.face, bw);
  }
  /** La ganancia del B/N para una luma medida (0..1) de la cara (face) o de la escena. */
  function bwGainOf(val, face, bw) {
    const target = face ? bw.face : bw.scene, k = face ? bw.face_k : bw.scene_k;
    const g = srgbToLin(shapeInv(target, bw.shape)) / Math.max(1e-3, srgbToLin(Math.max(0.02, val)));
    return Math.round(Math.pow(Math.min(1.8, Math.max(0.55, g)), k) * 100) / 100;
  }
  /** grade.bw_table: tabla gris del B/N con esa ganancia y la halacion de altas luces. */
  function bwTable(bw, gain) {
    const out = new Uint8Array(256), sh = bw.shape, glow = bw.glow;
    for (let i = 0; i < 256; i++) {
      const xl = srgbToLin(i / 255) * gain;
      const xe = gain <= 1 ? linToSrgb(Math.min(xl, 1)) : linToSrgb(xl > 0.8 ? 0.8 + 0.2 * Math.tanh((xl - 0.8) / 0.2) : xl);
      const v = Math.min(255, Math.max(0, Math.round(shapeAt(xe, sh) * 255)));
      out[i] = Math.min(255, v + (glow ? +glow[v] : 0));
    }
    return out;
  }
  /** Espera (ms) antes de volver a preguntar por las tablas de cada toma mientras el analisis de
      fondo no termina (shots_state "pending"): 8 s las primeras 40 veces (~5 min) y luego cada 30 s,
      sin rendirse nunca (un video largo o 4K puede tardar mas; antes se dejaba de preguntar y la
      vista nunca igualaba las tomas aunque el export si). */
  const LOOK_POLL = { fast: 8000, slow: 30000, fastN: 40 };
  const lookPollDelay = n => ((+n || 0) < LOOK_POLL.fastN ? LOOK_POLL.fast : LOOK_POLL.slow);
  /** Tablas por canal en el tiempo ORIGINAL t: las de su toma (igualar tomas) o las generales. */
  function lookShotAt(p, t) {
    const ps = (p && p.per_shot) || [];
    if (t == null || !ps.length) return -1;
    for (let i = 0; i < ps.length; i++) if (t >= ps[i].start && t < ps[i].end) return i;
    return -1;
  }
  function lookTablesAt(p, t) { const i = lookShotAt(p, t); return i >= 0 ? p.per_shot[i] : p.lut; }
  /** Luma de B/N (mezcla de canales de grade.BW_MIX) de un RGBA ya con las tablas de su toma. */
  function lookGray(p, data, W, H, T) {
    const m = p.mix || [0.4, 0.49, 0.11], gray = new Uint8Array(W * H);
    for (let i = 0, k = 0; i < gray.length; i++, k += 4) gray[i] = Math.min(255, Math.round(m[0] * T.r[data[k]] + m[1] * T.g[data[k + 1]] + m[2] * T.b[data[k + 2]]));
    return gray;
  }
  /** El color del export sobre un RGBA (W x H) EN SITIO. o = {t (tiempo original: tablas de la
      toma), face ([cx, cy, w, h] del cuadro), gain (B/N; si falta se mide como el motor)}. */
  function applyLook(p, data, W, H, o) {
    if (!p || p.enabled === false || !p.lut) return data;
    const x = o || {}, T = lookTablesAt(p, x.t), amt = lk01(+((p.vig || {}).amount) || 0), [cx, cy] = lookVigCenter(p, x.face);
    const bwv = p.mono && (p.vig || {}).shape === "bw";
    const vig = (px, py) => {
      if (amt <= 0.002) return 255;
      const m = bwv ? vignetteBwAt(px / W, py / H, amt, cx, cy) : vignetteAt(px / W, py / H, amt, cx, cy);
      return Math.floor(m * 255 + 0.5);
    };
    if (p.mono) {
      const gray = lookGray(p, data, W, H, T);
      const tb = bwTable(p.bw, x.gain != null ? +x.gain : bwGain(gray, W, H, x.face, p.bw));
      for (let y = 0, i = 0; y < H; y++) for (let xx = 0; xx < W; xx++, i++) {
        const v8 = vig(xx, y), v = Math.round(tb[gray[i]] * v8 / 255), k = i * 4;
        data[k] = data[k + 1] = data[k + 2] = v;
      }
      return data;
    }
    const M = p.sat;
    for (let y = 0, i = 0; y < H; y++) {
      for (let xx = 0; xx < W; xx++, i++) {
        const k = i * 4;
        let r = T.r[data[k]], g = T.g[data[k + 1]], b = T.b[data[k + 2]];
        if (M) {
          const r2 = Math.round(M[0][0] * r + M[0][1] * g + M[0][2] * b), g2 = Math.round(M[1][0] * r + M[1][1] * g + M[1][2] * b), b2 = Math.round(M[2][0] * r + M[2][1] * g + M[2][2] * b);
          r = Math.min(255, Math.max(0, r2)); g = Math.min(255, Math.max(0, g2)); b = Math.min(255, Math.max(0, b2));
        }
        const v8 = vig(xx, y);
        if (v8 !== 255) { r = Math.round(r * v8 / 255); g = Math.round(g * v8 / 255); b = Math.round(b * v8 / 255); }
        data[k] = r; data[k + 1] = g; data[k + 2] = b;
      }
    }
    return data;
  }
  /** "Total con el estilo": lo del estilo de color (ya por su intensidad) + lo que el usuario suma.
      base = payload.base; key: contrast | vignette | clarity. */
  function lookTotal(base, user, key) {
    const b = base || {}, u = user || {};
    if (key === "contrast") return Math.max(-0.6, Math.min(0.95, (+b.contrast || 0) + (+u.contrast || 0)));
    if (key === "vignette") return Math.min(1, (+b.vig || 0) + (+u.vignette || 0));
    if (key === "clarity") return u.clarity != null && u.clarity !== "" ? +u.clarity : +b.clarity || 0;
    return 0;
  }
  /** Ejemplos de los ajustes finos ("Menos · Así · Más"): valores absolutos alrededor del actual,
      dentro del rango del control. -> {clave: [menos, mas]} (redondeados a 3 decimales). */
  const LOOK_STEPS = { exposure: [0.5, -1, 1, 0], temperature: [0.7, -1, 1, 0], tint: [0.6, -1, 1, 0], saturation: [0.5, 0, 2, 1],
    contrast: [0.25, -0.5, 0.5, 0], strength: [0.5, 0, 1.5, 1] };
  function lookVariants(g, base) {
    const out = {}, u = g || {}, r3 = v => Math.round(v * 1000) / 1000;
    Object.entries(LOOK_STEPS).forEach(([k, [d, lo, hi, def]]) => {
      const cur = u[k] == null || u[k] === "" || !isFinite(+u[k]) ? def : +u[k];
      out[k] = [r3(Math.max(lo, cur - d)), r3(Math.min(hi, cur + d))];
    });
    const cl = lookTotal(base, u, "clarity");
    out.clarity = [r3(Math.max(0, cl - 0.3)), r3(Math.min(1, cl + 0.3))];
    return out;
  }

  // ------------------------------------------------------------------ inspector con claridad (fase 4)
  /** Tiempo para leer y escribir en el inspector: «m:ss.d» (0:05.2, 1:02.0). */
  function fmtTime(s) {
    const d = Math.round(Math.max(0, +s || 0) * 10) / 10, m = Math.floor(d / 60 + 1e-9), sec = d - m * 60;
    return `${m}:${sec.toFixed(1).padStart(4, "0")}`;
  }
  /** Lo que escribe el usuario -> segundos: «1:02.5», «62.5», «62,5», «0:05»; null si no se entiende. */
  function parseTime(str) {
    const s = String(str == null ? "" : str).trim().replace(",", ".").replace(/\s*s$/i, "");
    if (!s) return null;
    let m = /^(\d+):(\d{1,2}(?:\.\d+)?)$/.exec(s);
    if (m) { const sec = +m[2]; return sec < 60 ? +m[1] * 60 + sec : null; }
    m = /^\d+(?:\.\d+)?$/.exec(s);
    return m ? +s : null;
  }
  /** Id de la copy (ui_copy.json) de un parametro del inspector. o = {k, track, kind} | {k, group}
      (subtitulos, musica) | {k, parent} (campo de una lista). Orden: <pista>.<tipo>.<k>, <pista>.<k>,
      common.<k> (el contador usa text.counter.<k>). Sin entrada: el ultimo id (copyFor la marca missing). */
  function paramCopyId(ui, o) {
    const U = ui || {}, P = U.params || {}, A = U.aliases || {}, k = o.k;
    const ids = o.parent ? [`${o.parent}.${k}`] : o.group ? [`${o.group}.${k}`] : [`${o.track}.${o.kind}.${k}`, `${o.track}.${k}`, `common.${k}`];
    for (const i of ids) if (P[i] || (U.ui && U.ui[i]) || (A[i] && P[A[i]])) return i;
    return ids[ids.length - 1];
  }
  /** Campos que de verdad usa cada tipo de tarjeta y de invitacion (graphics.py: _card_rows y los
      _cta_*). Los campos de la lista que su tipo no usa no se muestran. */
  const VARIANT_ONLY = {
    card: ["value", "items", "label", "platform", "display", "logo", "icon"],
    cta: ["title", "label", "handle", "platform", "display", "subtitle", "icon", "button", "button_done", "button_color", "avatar", "initials", "media", "logo", "width"],
  };
  const VARIANT_FIELDS = {
    card: {
      generic: ["value", "items", "icon", "logo"], price: ["value", "items", "icon", "logo"], location: ["value", "icon", "logo"],
      schedule: ["items", "label", "icon", "logo"], link: ["label", "platform", "display", "icon", "logo"],
      booking: ["label", "platform", "display", "items", "icon", "logo"], phone: ["value", "icon", "logo"],
      rating: ["value", "icon", "logo"], checklist: ["items"], stat: ["value"],
    },
    cta: {
      follow: ["title", "label", "handle", "platform", "subtitle", "button", "button_done", "button_color", "avatar", "initials", "media", "width"],
      link_below: ["label", "platform", "display", "icon", "media", "logo", "width"],
      comment: ["label"], save: ["label"], share: ["label"],
    },
  };
  /** ¿Se muestra el campo `k` en un grafico `kind` con su `variant`? (solo tarjeta e invitacion filtran) */
  function variantShows(kind, variant, k) {
    const only = VARIANT_ONLY[kind]; if (!only || !only.includes(k)) return true;
    const def = kind === "card" ? "generic" : "follow";
    const f = VARIANT_FIELDS[kind][variant] || VARIANT_FIELDS[kind][def];
    return f.includes(k);
  }
  /** Campos que el inspector no muestra para este elemento: los que su tipo de tarjeta o invitacion no
      usa, y la duracion y fuerza de un corte (un corte no dura: es instantaneo). */
  function paramHidden(track, kind, it, k) {
    if (track === "graphics" && it && !variantShows(it.kind || kind, it.variant, k)) return true;
    if (track === "transitions" && kind === "cut" && (k === "dur" || k === "intensity")) return true;
    // colores que el motor no lee en ese tipo (graphics.py): la tarjeta pinta con `accent` (card_theme),
    // el rotulo con `color` (siempre trae uno: su `accent` nunca se ve), el sticker con bg/fg y la foto
    // encima no tiene color: esos controles no harian nada
    // la invitacion «Seguir» solo usa `color` como respaldo del boton (graphics._cta_follow): su
    // control es «Color del botón» (con el color que de verdad se pinta), no dos filas para lo mismo
    if (track === "graphics" && it && ((it.kind === "card" && k === "color") || (it.kind === "lower_third" && k === "accent")
      || ((it.kind === "sticker_text" || it.kind === "media") && k === "color")
      || (it.kind === "cta" && (it.variant || "follow") === "follow" && k === "color"))) return true;
    return false;
  }
  /** Colores que, vacios, el motor pinta con el color de tu marca (graphics.py: pal["accent"]). */
  const BRAND_COLOR_KEYS = new Set(["accent", "badge_color", "bg", "button_color"]);
  function colorFromBrand(track, kind, k) {
    if (BRAND_COLOR_KEYS.has(k)) return true;
    return track === "graphics" && k === "color" && ["progress", "doodle"].includes(kind);
  }
  /** Opciones relativas del tamaño (×0.8 · ×1 · ×1.25 del tamaño del estilo) en valores del motor. */
  function relChoices(rel, base) {
    const b = +base;
    if (!isFinite(b) || b <= 0) return [];
    return (rel || []).map(([m, l]) => [Math.round(b * +m * 10000) / 10000, l]);
  }
  /** «Traer al frente» (+1) y «Enviar atrás» (-1): z por encima o por debajo de los demas
      elementos de la pista que se enciman con este. -> z nuevo. */
  function zStep(list, it, dir) {
    const a = +it.start, b = +it.end, z0 = +it.z || 0;
    const zs = (list || []).filter(x => x && x !== it && x.id !== it.id && +x.start < b && +x.end > a).map(x => +x.z || 0);
    if (!zs.length) return dir > 0 ? z0 + 1 : z0 - 1;
    return dir > 0 ? Math.max(z0, Math.max(...zs) + 1) : Math.min(z0, Math.min(...zs) - 1);
  }
  /** Secciones del inspector para los parametros de un tipo. La «Fuerza» (intensity) va en
      Animación, no en Tiempo. -> {cont, pos, style, anim} (listas de parametros). */
  const CONT_KEYS = new Set(["text", "lines", "title", "subtitle", "value", "items", "label", "handle", "text.text", "prefix", "suffix", "from", "to",
    "decimals", "accent_words", "name", "icon", "variant", "text.preset", "links", "media", "caption", "display", "platform", "button", "button_done",
    "initials", "avatar", "sep", "src_t"]);
  const POS_KEYS = new Set(["x", "y", "size", "rotation", "align", "path_to", "anchor", "width", "bar_y", "margin", "tilt", "aspect", "fit", "focus", "zoom", "bend"]);
  function inspectorGroups(params) {
    const g = { cont: [], pos: [], style: [], anim: [] };
    (params || []).forEach(p => {
      const k = p.k;
      if (CONT_KEYS.has(k)) g.cont.push(p);
      else if (POS_KEYS.has(k)) g.pos.push(p);
      else if (/^anim_|easing|_dur$|^stagger$|^draw_time$|^intensity$/.test(k)) g.anim.push(p);
      else g.style.push(p);
    });
    return g;
  }
  /** Unidades de Avanzado en español (nunca Hz ni st). */
  const UNIT_ES = { Hz: "veces por segundo", st: "semitonos", grados: "°", x: "×", EV: "pasos" };
  const unitEs = u => (u in UNIT_ES ? UNIT_ES[u] : (u || ""));
  /** Valor de Avanzado sin formato propio en la copy: con su unidad en español, en % si va de 0 a 1. */
  function plainValue(p, v) {
    if (v == null || v === "" || !isFinite(+v)) return "";
    const x = +v, u = unitEs(p && p.unit);
    const num = n => { const a = Math.abs(n); const s = a >= 10 || Math.abs(a - Math.round(a)) < 0.005 ? String(Math.round(a)) : (Math.round(a * 100) / 100).toString(); return (n < 0 && s !== "0" ? "−" : "") + s; };
    if (!u && p && +p.min === 0 && +p.max === 1) return `${num(x * 100)} %`;
    if (u === "×") return `×${num(x)}`;
    if (u === "°") return `${num(x)}°`;
    return u ? `${num(x)} ${u}` : num(x);
  }

  return {
    EXPORT_RES, EXPORT_RES_LABEL, EXPORT_RES_SHORT, EXPORT_QUALITY, EXPORT_QUALITY_LABEL, EXPORT_QUALITY_HINT, EXPORT_DEFAULTS, EXPORT_ERR,
    exportDims, resClass, parseDims, exportMaxRes, exportResOptions, pickExportRes, exportResNote, resLabel,
    sanitizeFileName, exportDate, projectTitle, defaultExportName, mergeExportPrefs,
    fmtSize, approxSize, approxTime, estimateText, fmtDuration, prettyPath, shortPath, pathBase, pathDir, revealLabel, exportError,
    normQuality, serverQuality, serverResRows, combineReasons, normSettings,
    CONF_LABEL, DISCARD_LABEL, JOIN_KINDS, createdMs, recordedMs, natCmp, recordingOrder, montageConf, findCand, slotNumbers, candVol, candVolKnown, montageClips,
    slotItems, shiftAfter, slotClipIdx, currentCand, swapTake, extraInVideo, addExtra, removeClip, removeSlot, isExtraSlot, itemConf, candConf, usedReason, montageRows, montageSummary, scriptText, composeMain, composeBody,
    montageStatus, wordsProgress, uploadPct, projectState, fmtSrc,
    assetNorm, lineRefs, scriptRefs, stripRefs, withInserts, matchAsset, fileKind, classifyFiles, INS, insertFit, lineSpans, placeInserts, insertForCompose,
    RAMP, rampOf, rampSpans, rampSide, rampPick, rampForce, mainJoins, syncRamps, toggleRamp,
    DEFAULT_ACCENT, normHex, projectAccent, applyAccent,
    MIN_KEEP, MIN_FRAG, SNAP_S, TRACKS, EVENT_TRACKS, RULER_STEPS, SAFE, XC, SYM_W, SNAP_X, PRO_LANGS, MIXERS,
    symDefaults, ltCentered, setLowerThirdAlign, snapCenterX, lowerThirdReveal, lowerThirdX,
    keepsFromCuts, Timeline, timelineFromPlan, shotFrame,
    getPath, setPath, clone, isEvent, kindKey, itemKind, kindParams, commonParams, kinds, kindLabel,
    fillDefaults, newId, allIds, newItem, duplicateItem, changeKind, itemStart, itemEnd, eventSpan,
    activeAt, lockedIds, findItem, itemLabel,
    packRows, snapTo, rulerStep, fmtClock, round3, zoomFromSlider, sliderFromZoom,
    captionGroups, ease, cameraAt, normWord, wordMatch, unitTimes,
    accentEs, hasLines, titleText, setTitleText, clearLineStyles, captionsHiddenAt, captionGroupSuppressed, captionY, snapTargets,
    captionRangeAlpha, capGroupAt, photoCaptionY, photoCaptionRows, captionYAt,
    sameVisual, regenCompanions, mergeRegenerate, normText, contentSig, dedupeFx, iconGeom, iconInkDx, kickerSplit, anchorXY,
    LINK_PHOTO, LINK_LOGO, LINK_BADGE, LINK_PAD_R, FOLLOW_BADGE, LOGO_PAD, LOGO_RING, LOGO_PLATE, LOGO_PLATE_DARK,
    linkSlot, logoLook, logoFit,
    pipCard, mediaAutoPlace, overlayBoxes, pipPlace, faceMedian,
    transitionOffJoin, setProLangs, planForLang, fxTexts, applyFxTexts, musicSig, mediaParam, mediaKinds, mediaKindFor,
    humanSkip,
    CAPTION_PRESETS, TEXT_PRESETS, TEXT_PRESET_Y, ITALIC, FUNC_END, captionCfg, captionUsable, balancedSplit, captionLayout,
    activeBump, italicOf, textUsable,
    textFields, setTextFields, textState, textStatus, translationRequest, applyTranslation, restoreOriginalTexts,
    dropReemitted,
    TAKE_KINDS, TAKE_LABEL, TAKE_PLURAL, TAKE_CHIP, TAKE_LEVELS, LEVEL_LABEL, LONG_VIDEO_S, FORMATS,
    isTakeCut, cutOn, cutKind, takeLevel, confBars, planWords, wordsInCuts, cutSpans, wordlessAt, takeGroups, cutTag,
    keptMarks, unionLen, takeStats, removedByCategory, kindCounts, levelOn, applyLevel, levelFinal,
    setCutEnabled, toggleCut, setCutsEnabled, kindOn, setKindEnabled, reviewItems, cleanSections, scriptParagraphs, listenRange, fmtLen,
    markEdges, adoptTakeCuts, takeReason, isLongLandscape, defaultFormat, projectFormat, shortsFor,
    spring, pillWordRect, pillAt, contrastOn, CAPTION_ROLES, captionColorRoles, captionStyleNote, captionCardOf, highlightLinked, paintsAccent, engineLum, ctaButtonColor, CTA_BTN_FALLBACK, CONTRAST, captionContrast,
    spokenWords, pickSampleShot, otherShot, sampleWindow, nearestChoice, fillExample, humanNum, humanValue, copyFor, visibleRows,
    CAPTION_COLOR_IDS, colorName, presetFromPlan, captionGroupsFor,
    LOOK_ORDER, srgbToLin, linToSrgb, vignetteAt, vignetteBwAt, lookVigCenter, shapeAt, shapeInv, percentileOf, bwGain, bwGainOf, bwTable,
    lookShotAt, lookTablesAt, lookGray, applyLook, lookTotal, LOOK_STEPS, lookVariants,
    DIRECTOR_CARDS, DIRECTOR_ORDER, directorCounts, directorExample, musicSourceOf, musicBpmOf, musicBpmSet, KEYSCALES, keyscaleEs,
    moodSampleUrl, PROFILE_USE, profileUseOf, profileUrlKey, profileDataKey, logoRect, FORMAT_ORDER, FORMAT_SHAPE,
    fmtTime, parseTime, paramCopyId, VARIANT_FIELDS, variantShows, paramHidden, colorFromBrand, relChoices, zStep, inspectorGroups, unitEs, plainValue,
    shortsWanted, LOOK_POLL, lookPollDelay, STYLE_FONTS, captionFonts, pillOwn, setPillOwn, colorWhereWords, captionOptionApplies, MONO_LOOKS, GRADE_COLOR_KEYS, gradeOptionApplies, musicReadyText, musicPromptShown, setMusicPrompt, airApplies, GRADE_NEUTRAL, GRADE_PREV, CAP_LOOK, gradeTuned, directorOutcome, directorQueCambia,
  };
});
