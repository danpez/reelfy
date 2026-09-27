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
    oldP.forEach(k => { if (!newP.has(k) && !["x", "y", "size", "color", "text"].includes(k)) delete item[k]; });
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
  function captionsHiddenAt(fx, t) {
    const cap = (fx && fx.captions) || {};
    const h = Object.prototype.hasOwnProperty.call(cap, "hide_during_titles") ? cap.hide_during_titles : true;
    if (!h) return false;
    const on = it => it && it.enabled !== false && t >= +it.start && t < +it.end;
    if (((fx && fx.text) || []).some(it => on(it) && !it.keep_captions)) return true;
    return ((fx && fx.cutaways) || []).some(it => on(it) && it.text && typeof it.text === "object" && String(it.text.text || "").trim() !== "");
  }

  /** text.draw_captions: si el grupo ya estaba oculto al empezar (inicio + 0.13 s y su ultima
      palabra caen tapados por un titulo) no vuelve a salir al terminar el titulo. */
  function captionGroupSuppressed(fx, g, t) {
    if (!g || !g.words || !g.words.length) return false;
    const s0 = +g.start + 0.13, last = Math.max(+g.start, +g.words[g.words.length - 1].start);
    return t > s0 && captionsHiddenAt(fx, s0) && captionsHiddenAt(fx, last);
  }
  /** Altura (0..1) de los subtitulos en t (original): `y_ranges` [{start, end, y}] la cambia por tramo. */
  function captionY(cfg, t) {
    const c = cfg || {};
    const r = (Array.isArray(c.y_ranges) ? c.y_ranges : []).find(x => x && +x.start <= t && t < +x.end && x.y != null);
    return r ? +r.y : (c.y != null ? +c.y : 0.72);
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
  function mergeRegenerate(localFx, serverFx, ids) {
    const fx = clone(localFx) || {};
    const known = allIds(localFx);
    const used = new Set();
    const results = [];
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
    return { fx, results };
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
    if (track === "sfx") return `s:${it.name}`;
    return `${track}:${it.kind}`;
  }
  function overlapFrac(track, a, b) {
    if (isEvent(track)) return Math.abs((+a.at || 0) - (+b.at || 0)) < 0.15 ? 1 : 0;
    const o = overlap(track, a, b), L = Math.min(+a.end - +a.start, +b.end - +b.start);
    return L > 1e-6 ? o / L : 0;
  }
  /** Quita (en sitio) los items NO bloqueados que repiten el contenido de uno bloqueado y se
      solapan con el mas de la mitad (el director a veces vuelve a generar un titulo que el usuario
      bloqueo), y los duplicados exactos entre items de la IA. -> [{track, id, of}] quitados. */
  function dedupeFx(fx) {
    const removed = [];
    if (!fx) return removed;
    TRACKS.forEach(tk => {
      const arr = fx[tk] || [];
      const keep = [];
      arr.forEach(it => {
        if (!it || it.locked || it.src === "user") { keep.push(it); return; }
        const sig = contentSig(tk, it);
        const dup = keep.find(k => k && contentSig(tk, k) === sig && (k.enabled !== false || it.enabled === false)
          && overlapFrac(tk, it, k) > (k.locked || k.src === "user" ? 0.5 : 0.9));
        if (dup) removed.push({ track: tk, id: it.id, of: dup.id });
        else keep.push(it);
      });
      // un item de la IA que quedo ANTES que el bloqueado que duplica tambien se quita
      const locked = keep.filter(k => k && (k.locked || k.src === "user"));
      const out = keep.filter(it => {
        if (!it || it.locked || it.src === "user") return true;
        const sig = contentSig(tk, it);
        const dup = locked.find(k => contentSig(tk, k) === sig && overlapFrac(tk, it, k) > 0.5);
        if (dup) { removed.push({ track: tk, id: it.id, of: dup.id }); return false; }
        return true;
      });
      if (out.length !== arr.length) fx[tk] = out;
    });
    return removed;
  }

  // ------------------------------------------------------------------ geometria (espejo del render)
  const SAFE = { top: 0.09, bottom: 0.80, left: 0.06, right: 0.88 };
  /** graphics.draw_icon: el badge mide `size` de diametro y el icono 0.56*size; sin badge, size. */
  function iconGeom(sizePx, badge) {
    const b = badge && badge !== "none";
    return { badge: b ? sizePx : 0, icon: b ? sizePx * 0.56 : sizePx, box: sizePx };
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
  const PRO_LANGS = ["es", "en"];
  /** Plan que se manda a /frame, /preview y /render con el idioma elegido: subtitulos traducidos
      (phrases_en). Los textos de titulos/graficos ya van en el plan (se traducen en sitio). */
  function planForLang(plan, lang) {
    if (!plan) return plan;
    if (lang === "en" && Array.isArray(plan.phrases_en) && plan.phrases_en.length) return Object.assign({}, plan, { phrases: plan.phrases_en });
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
  /** Parametro de medio propio (type "media": {sid, type, name, w, h, dur}) de un kind, o null. */
  function mediaParam(schema, track, kind) {
    return kindParams(schema, track, kind).find(p => p.type === "media") || null;
  }
  function mediaKinds(schema, track) { return kinds(schema, track).filter(k => mediaParam(schema, track, k)); }

  return {
    MIN_KEEP, MIN_FRAG, SNAP_S, TRACKS, EVENT_TRACKS, RULER_STEPS, SAFE, PRO_LANGS, MIXERS,
    keepsFromCuts, Timeline, timelineFromPlan,
    getPath, setPath, clone, isEvent, kindKey, itemKind, kindParams, commonParams, kinds, kindLabel,
    fillDefaults, newId, allIds, newItem, duplicateItem, changeKind, itemStart, itemEnd, eventSpan,
    activeAt, lockedIds, findItem, itemLabel,
    packRows, snapTo, rulerStep, fmtClock, round3, zoomFromSlider, sliderFromZoom,
    captionGroups, ease, cameraAt, normWord, wordMatch, unitTimes,
    accentEs, hasLines, titleText, setTitleText, clearLineStyles, captionsHiddenAt, captionGroupSuppressed, captionY, snapTargets,
    sameVisual, mergeRegenerate, normText, contentSig, dedupeFx, iconGeom, kickerSplit, anchorXY,
    transitionOffJoin, planForLang, fxTexts, applyFxTexts, musicSig, mediaParam, mediaKinds,
  };
});
