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
  const EPS = 1e-9, Q_EPS = 1e-6, SHOT_EPS = 0.1;
  const TRACKS = ["text", "graphics", "cutaways", "effects", "transitions", "sfx"];
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
        const fa = sa ? Math.ceil(a * f - SHOT_EPS) : Math.floor(a * f + Q_EPS);
        let fb = sb ? Math.ceil(b * f - SHOT_EPS) : Math.ceil(b * f - Q_EPS);
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
    const pre = { text: "tx", graphics: "gr", cutaways: "cw", effects: "fx", transitions: "tr", sfx: "sf" }[track] || "it";
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
    if (track === "text" && !it.text && !(it.lines && it.lines.length)) it.text = "Tu texto";
    if (track === "graphics" && it.kind === "sticker_text" && !it.text) it.text = "NUEVO";
    if (track === "graphics" && it.kind === "cta" && !it.label) it.label = "Sigueme";
    if (track === "graphics" && it.kind === "card" && !it.title) { it.title = "Titulo"; it.value = "Valor"; }
    if (track === "graphics" && it.kind === "lower_third" && !it.title) { it.title = "Nombre"; it.subtitle = "Descripcion"; }
    if (track === "graphics" && it.kind === "icon" && !it.name) it.name = "star";
    if (track === "cutaways" && !getPath(it, "text.text")) setPath(it, "text.text", "Titulo");
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
  function captionGroups(phrases, maxWords) {
    const mw = Math.max(1, +maxWords || 4);
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
      if (groups[i].length === 1 && groups[i - 1].length < mw + 1 && (+groups[i][0].start - +groups[i - 1][groups[i - 1].length - 1].end) < 0.45) {
        groups[i - 1] = groups[i - 1].concat(groups[i]); groups.splice(i, 1);
      }
    }
    return groups.map(g => ({ words: g, start: +g[0].start, end: +g[g.length - 1].end }));
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
  function captionCfg(cfg, titleFont) {
    const c = cfg || {}, preset = CAPTION_PRESETS[c.preset] ? c.preset : "clean_bold", P = CAPTION_PRESETS[preset];
    let font = c.font;
    if (preset === "minimal_small" && (!font || font === "Poppins-ExtraBold")) font = P.font;
    else if (preset === "bold_caps" && (!font || font === "Poppins-ExtraBold")) font = titleFont || "Poppins-Black";
    else font = font || "Poppins-ExtraBold";
    const cs = c.case && c.case !== "none" ? c.case : (P.case || "none");
    const mw = Math.min(+c.max_words || P.max_words, preset === "bold_caps" ? 3 : 99);
    return Object.assign({}, P, { preset, font, case: cs, max_words: mw,
      size: (+c.size || 0.052) * P.scale, x: c.x != null ? +c.x : 0.5, y: c.y != null ? +c.y : 0.72,
      max_lines: c.max_lines != null ? Math.max(1, Math.floor(+c.max_lines)) : P.max_lines,
      stroke: c.stroke != null ? +c.stroke : P.stroke, shadow: c.shadow != null ? +c.shadow : P.shadow,
      kw_accent: c.kw_accent != null ? !!c.kw_accent : preset === "clean_bold",
      recenter: c.recenter != null ? !!c.recenter : ["clean_bold", "minimal_small", "bold_caps"].includes(preset) });
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

  return {
    MIN_KEEP, MIN_FRAG, SNAP_S, TRACKS, EVENT_TRACKS, RULER_STEPS, SAFE, XC, SYM_W, SNAP_X, PRO_LANGS, MIXERS,
    symDefaults, ltCentered, setLowerThirdAlign, snapCenterX, lowerThirdReveal, lowerThirdX,
    keepsFromCuts, Timeline, timelineFromPlan,
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
  };
});
