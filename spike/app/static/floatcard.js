/* Reelfy Studio: recursos flotantes del guion (pista fx.media, estilo "float_card") en la vista
   en vivo. Port PURO de spike/scripts/motion/media.py (geometria, animacion, luz y franjas de la
   perspectiva identicas paso a paso; tests/test_media_float_preview.py compara ambos) + el dibujo
   en canvas 2D. En el navegador queda como window.FloatCard; en node (pruebas) se usa con require().

   Uso desde fx.js (vista en vivo; el cuadro exacto al pausar lo da /frame con el render real):
     const FC = window.FloatCard;
     // 1) item activo en t (tiempo ORIGINAL, como todo el plan): lt = t - it.start, dur = end - start
     const card = FC.cardLayout(mediaW, mediaH, B.w / B.h,
                                FC.captionTop(PLAN.fx.captions, capOn, it.start, it.end, B.w / B.h),
                                it.width, it.aspect, it.y,
                                FC.captionBottom(PLAN.fx.captions, capOn, it.start, it.end, B.w / B.h));
     const st = FC.cardAnim(lt, dur, FC.param(it, "in_dur"), FC.param(it, "out_dur"), FC.param(it, "float"));
     // 2) fondo: filtro CSS extra para el video base mientras la tarjeta esta (desenfoque + oscurecido)
     const extra = FC.bgFilter(it, st.bg, B.w);                // "blur(4.1px) brightness(0.72)" o ""
     // 3) la tarjeta (B = caja de layout 9:16 en px del canvas: {x, y, w, h}); frame = {src, w, h}
     //    (imagen o <video> silenciado en FC.srcTime(it, lt, dur_del_clip): src_in + lt * speed)
     const r = FC.drawCard(g, frame, B, card, st, it, lt, dur);   // -> rect en reposo (seleccion)
   VIDA EN REPOSO (= media.py): flotacion, vaiven 3D con perspectiva sobre el eje horizontal
   (warpStrips: franjas afines, igual que el render), respiracion, Ken Burns con paralaje y, en
   imagenes, brillo de vidrio (lightAt = lightStops con desenfoque de movimiento: brillo fijo arriba +
   franja de luz que cruza lenta + borde + irisado `holo`). Diferencias con el render: el fondo usa el filtro CSS del video en vez del
   cuadro compuesto, la sombra es la de canvas y la entrada/salida aplica el alfa por capa
   dibujada (el render usa una capa). Todas las fracciones son de la CAJA DE LAYOUT (x del ancho,
   y del alto). */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FloatCard = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ------------------------------------------------------------------ constantes (espejo de media.py)
  const SAFE = { top: 0.09, bottom: 0.80, left: 0.12, right: 0.88 };
  const XC = 0.5, SYM_W = SAFE.right - SAFE.left;
  const CARD_W = SYM_W, CARD_W_MIN = 0.4;
  const ASPECT = 4 / 5, ASPECT_TALL = 9 / 16, ASPECT_WIDE = 1.0, ASPECT_MAX = 16 / 9;
  const MARGIN = 0.015, UP_BIAS = 0.35, RADIUS = 0.055, ROOM_OK = 0.35, CARD_BG = "#1C1C22";
  const CAPTION_HALF = 0.05, CAP_GAP = 0.01;
  const CAPTION_PRESET_LINES = { clean_bold: [1.0, 1], karaoke: [1.0, 1], boxed: [1.0, 1],
    minimal_small: [0.74, 2], bold_caps: [1.28, 2] };
  const IN_DUR = 0.35, OUT_DUR = 0.25, SPRING_F = 2.6, SPRING_Z = 0.55;
  const POP_FROM = 0.9, RISE = 0.035, TILT = 14.0, EXIT_SCALE = 0.06, EXIT_RISE = 0.02;
  // vida en reposo (media.py)
  const FLOAT_AMP = 0.008, FLOAT_T = 3.4, BREATH = 0.006, SWAY = 4.5, SWAY_T = 5.6, PERSP = 2.4;
  const WARP_STEP = 0.2, WARP_MIN = 8, WARP_MAX = 320;
  const KENBURNS = 0.08, KB_LEAD = 0.3, PARALLAX = 0.35, PARALLAX_MAX = 0.35;
  const SHEEN_A = 0.36, SHEEN_DELAY = 0.25, SHEEN_EVERY = 4.6, SHEEN_DUR = 1.8, SHEEN_MIN = 1.5, SHEEN_OUT = 0.3;
  const SHEEN_EASE = 0.35, SHUTTER = 1 / 30, SHUTTER_N = 4;
  const GLOSS_A = 0.07, HOLO_A = 0.16, RIM_A = 1.0;
  const BAND = [[-0.3, 0.0], [-0.24, 0.3], [-0.18, 0.0], [-0.14, 0.08], [-0.05, 0.42], [0.0, 1.0],
    [0.05, 0.42], [0.14, 0.08], [0.21, 0.0]];
  const GLOSS = [[0.0, 1.0], [0.16, 0.55], [0.42, 0.0]];
  const HOLO = [[-0.36, [150, 120, 255], 0.0], [-0.29, [150, 120, 255], 1.0], [-0.22, [90, 210, 255], 1.0],
    [-0.14, [90, 210, 255], 0.0], [0.12, [255, 214, 130], 0.0], [0.2, [255, 214, 130], 1.0],
    [0.28, [255, 120, 200], 1.0], [0.36, [255, 120, 200], 0.0]];
  const DEFAULTS = { blur: 0.55, dim: 0.28, radius: RADIUS, shadow: 0.6, border: true,
    kenburns: KENBURNS, float: 1.0, in_dur: IN_DUR, out_dur: OUT_DUR };
  const LIVE_DEFAULTS = { sheen: 1.0, holo: 0.6 };
  const BLUR_PX = 22.0;
  const STYLES = ["float_card"], FITS = ["hold", "speed", "trim"];

  // ------------------------------------------------------------------ utilidades (espejo de ease.py)
  const num = (v, d) => { if (typeof v === "boolean" || v === null || v === undefined || v === "") return d; const f = +v; return isFinite(f) ? f : d; };
  const clamp = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x);
  const c01 = x => clamp(x, 0, 1);
  const outCubic = p => { p = c01(p); return 1 - Math.pow(1 - p, 3); };
  const inCubic = p => { p = c01(p); return p * p * p; };
  const inOutQuad = p => { p = c01(p); return p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; };
  const smoothstep = p => { p = c01(p); return p * p * (3 - 2 * p); };
  function spring(t, freq, damping) {
    if (t <= 0) return 0;
    const w = 2 * Math.PI * freq, z = clamp(damping, 0.01, 0.999), wd = w * Math.sqrt(1 - z * z);
    return 1 - Math.exp(-z * w * t) * (Math.cos(wd * t) + (z * w / wd) * Math.sin(wd * t));
  }
  const r5 = v => Math.round(v * 1e5) / 1e5;
  /** Parametro de estilo del item (o su default: DEFAULTS o LIVE_DEFAULTS). */
  function param(it, k) { const v = it ? it[k] : undefined; return v === undefined || v === null ? (k in DEFAULTS ? DEFAULTS[k] : LIVE_DEFAULTS[k]) : v; }
  /** media.is_video: un clip (o un cuadro ya horneado a la tarjeta). */
  function isVideo(it, baked) { return !!baked || !!(it && it.kind === "video"); }
  /** media.sheen_amount: `sheen` efectivo (imagenes 1; clips 0 salvo que el item lo pida). */
  function sheenAmount(it, baked) {
    if (isVideo(it, baked) && (!it || it.sheen === undefined || it.sheen === null)) return 0;
    return clamp(num(param(it, "sheen"), LIVE_DEFAULTS.sheen), 0, 1);
  }

  // ------------------------------------------------------------------ geometria (media.py)
  /** media._caption_ys: {ys, half} de los subtitulos durante [t0, t1] o null. */
  function captionYs(cap, on, t0, t1, boxAspect) {
    if (!on || !cap || typeof cap !== "object" || cap.enabled === false || (cap.preset || "clean_bold") === "none") return null;
    const ba = boxAspect == null ? 9 / 16 : +boxAspect;
    const ys = [num(cap.y, 0.72)];
    (Array.isArray(cap.y_ranges) ? cap.y_ranges : []).forEach(r => {
      if (!r || typeof r !== "object" || r.hide) return;
      const a = num(r.start, NaN), b = num(r.end, NaN);
      if (!isFinite(a) || !isFinite(b)) return;
      if (t0 != null && t1 != null && !(a < t1 && t0 < b)) return;
      ys.push(num(r.y, ys[0]));
    });
    let [scale, lines] = CAPTION_PRESET_LINES[cap.preset || "clean_bold"] || [1.0, 1];
    if (cap.max_lines != null) lines = Math.max(1, Math.trunc(num(cap.max_lines, lines)));
    return { ys, half: Math.max(CAPTION_HALF, 0.625 * lines * num(cap.size, 0.052) * scale * ba) };
  }
  /** media.caption_top: borde superior de la franja de subtitulos durante [t0, t1] (ORIGINAL) o null. */
  function captionTop(cap, on, t0, t1, boxAspect) {
    const r = captionYs(cap, on, t0, t1, boxAspect);
    return r ? r5(Math.min(...r.ys) - r.half - CAP_GAP) : null;
  }
  /** media.caption_bottom: borde inferior de la franja de subtitulos durante [t0, t1] o null. */
  function captionBottom(cap, on, t0, t1, boxAspect) {
    const r = captionYs(cap, on, t0, t1, boxAspect);
    return r ? r5(Math.max(...r.ys) + r.half + CAP_GAP) : null;
  }
  /** media.card_band: [arriba, abajo] de la banda libre de la tarjeta: arriba de los subtitulos si
      mide >= ROOM_OK; si no, la mas grande entre esa y la de abajo; si ni esa cabe una tarjeta
      minima, toda la zona segura (los subtitulos van encima). */
  function cardBand(boxAspect, capTop, capBottom) {
    const top0 = SAFE.top + MARGIN, bot0 = SAFE.bottom - MARGIN;
    if (capTop == null) return [top0, bot0];
    const ct = num(capTop, SAFE.bottom);
    const cb = capBottom != null ? num(capBottom, ct + 2 * (CAPTION_HALF + CAP_GAP)) : ct + 2 * (CAPTION_HALF + CAP_GAP);
    const above = [top0, Math.min(SAFE.bottom, ct) - MARGIN], below = [Math.max(SAFE.top, cb) + MARGIN, bot0];
    const ra = above[1] - above[0], rb = below[1] - below[0];
    const ba = clamp(num(boxAspect, 9 / 16), 0.2, 5.0);
    if (Math.max(ra, rb) < CARD_W_MIN * ba / ASPECT_WIDE - 1e-9) return [top0, bot0];
    if (ra >= ROOM_OK - 1e-9 || ra >= rb) return above;
    return below;
  }
  /** media.card_layout -> {cx, cy, w, h, aspect, radius} (fracciones de la caja; cx siempre 0.5). */
  function cardLayout(srcW, srcH, boxAspect, capTop, width, aspect, y, capBottom) {
    const [top, bottom] = cardBand(boxAspect, capTop, capBottom);
    const room = Math.max(0.08, bottom - top);
    const ba = clamp(num(boxAspect, 9 / 16), 0.2, 5.0);
    let w = clamp(num(width, CARD_W), CARD_W_MIN, SYM_W);
    const sw = num(srcW, 0), sh = num(srcH, 0);
    const sa = sw > 0 && sh > 0 ? sw / sh : ASPECT;
    const manual = aspect != null && num(aspect, 0) > 0;
    let asp = manual ? clamp(num(aspect, ASPECT), ASPECT_TALL, ASPECT_MAX) : (sa >= ASPECT ? ASPECT : Math.max(ASPECT_TALL, sa));
    let h = w * ba / asp;
    if (h > room + 1e-9 && !manual) { asp = Math.min(ASPECT_WIDE, Math.max(asp, w * ba / room)); h = w * ba / asp; }
    if (h > room + 1e-9) {
      w = Math.max(CARD_W_MIN, room * asp / ba); h = w * ba / asp;
      if (h > room + 1e-9) { h = room; w = room * asp / ba; }
    }
    const free = Math.max(0, room - h);
    let cy;
    if (y != null && num(y, -1) >= 0) cy = free > 0 ? clamp(num(y, 0.5), top + h / 2, bottom - h / 2) : top + h / 2;
    else cy = top + h / 2 + free * UP_BIAS;
    return { cx: XC, cy: r5(cy), w: r5(w), h: r5(h), aspect: r5(asp), radius: RADIUS };
  }
  /** media.card_px: rect de la tarjeta en px de la caja [x0, y0, w, h], simetrico al pixel. */
  function cardPx(card, bw, bh) {
    const bwi = Math.round(bw);
    let x0 = Math.round((bwi - num(card.w, CARD_W) * bwi) / 2);
    x0 = Math.max(0, Math.min(x0, Math.floor(bwi / 2) - 1));
    const w = bwi - 2 * x0;
    const asp = num(card.aspect, ASPECT) || ASPECT;
    const h = Math.max(2, Math.round(w / asp));
    const y0 = Math.round(num(card.cy, 0.4) * bh - h / 2);
    return [x0, y0, w, h];
  }
  const tinTout = (dur, inDur, outDur) => [Math.max(1e-3, Math.min(num(inDur, IN_DUR), 0.4 * dur)),
    Math.max(1e-3, Math.min(num(outDur, OUT_DUR), 0.3 * dur))];
  /** media.card_anim -> {alpha, scale, dy, tilt, bg, sway, fdy, lift}. */
  function cardAnim(lt, dur, inDur, outDur, floatAmt) {
    dur = Math.max(1e-3, num(dur, 1.0));
    lt = clamp(num(lt, 0), 0, dur);
    const [tin, tout] = tinTout(dur, inDur, outDur);
    const sp = spring(lt, SPRING_F, SPRING_Z);
    let alpha = outCubic(c01(lt / (0.6 * tin)));
    let scale = POP_FROM + (1 - POP_FROM) * sp;
    let dy = RISE * (1 - sp);
    let tilt = TILT * (1 - sp);
    let bg = smoothstep(c01(lt / (1.2 * tin)));
    const fa = Math.max(0, num(floatAmt, 1.0)), fd = Math.min(fa, 1.0);
    const ph = lt * (1 + 0.5 * (Math.max(fa, 1) - 1));
    const k = smoothstep(c01(lt / (2 * tin)));
    const fdy = -FLOAT_AMP * fd * k * Math.sin(2 * Math.PI * ph / FLOAT_T);
    dy += fdy;
    const sway = SWAY * fd * k * Math.sin(2 * Math.PI * ph / SWAY_T);
    tilt += sway;
    scale *= 1 + BREATH * fd * k * Math.sin(2 * Math.PI * ph / (1.5 * FLOAT_T));
    const q = c01((dur - lt) / tout);
    if (q < 1) {
      const pe = 1 - q;
      alpha *= smoothstep(q);
      scale *= 1 - EXIT_SCALE * inCubic(pe);
      dy -= EXIT_RISE * inCubic(pe);
      bg *= smoothstep(q);
    }
    return { alpha, scale, dy, tilt, bg, sway, fdy, lift: clamp(-fdy / FLOAT_AMP, -1, 1) };
  }
  /** media.kenburns_zoom: 1 + amount (KB_LEAD + (1 - KB_LEAD) in_out_quad(lt / dur)); amount 0 = 1. */
  function kenburnsZoom(lt, dur, amount) {
    dur = Math.max(1e-3, num(dur, 1.0));
    const e = inOutQuad(c01(num(lt, 0) / dur));
    return 1 + Math.max(0, num(amount, KENBURNS)) * (KB_LEAD + (1 - KB_LEAD) * e);
  }
  /** media.parallax_px: corrimiento de la ventana de la imagen (px de la tarjeta; + = baja). */
  function parallaxPx(fdy, bh, h, zoom) {
    const slack = (1 - 1 / Math.max(1, num(zoom, 1))) * Math.max(0, num(h, 0)) / 2;
    const lim = PARALLAX_MAX * slack;
    return clamp(PARALLAX * num(fdy, 0) * num(bh, 0), -lim, lim);
  }
  /** media.sheen_state: [centro u, envolvente] de la franja de luz o null. */
  function sheenState(lt, dur, inDur, outDur) {
    dur = Math.max(1e-3, num(dur, 1.0));
    lt = num(lt, 0);
    const [tin, tout] = tinTout(dur, inDur, outDur);
    const end = dur - tout, a0 = tin + SHEEN_DELAY;
    if (lt < a0 || lt >= end) return null;
    const a = a0 + Math.floor((lt - a0) / SHEEN_EVERY) * SHEEN_EVERY;
    const d = Math.min(SHEEN_DUR, end - a);
    if (d < SHEEN_MIN - 1e-9) return null;
    const p = (lt - a) / d;
    if (p >= 1) return null;
    const e = (1 - SHEEN_EASE) * p + SHEEN_EASE * (1 - Math.cos(Math.PI * p)) / 2;
    const u = -SHEEN_OUT + (1 + 2 * SHEEN_OUT) * e;
    return [u, smoothstep(c01(5 * Math.min(p, 1 - p)))];
  }
  /** media._key_at: [r, g, b, a] de una capa lineal por tramos [[u, r, g, b, a]] en u. */
  function keyAt(keys, u) {
    if (u < keys[0][0] || u > keys[keys.length - 1][0]) {
      const k = u < keys[0][0] ? keys[0] : keys[keys.length - 1];
      return [k[1], k[2], k[3], 0];
    }
    for (let i = 1; i < keys.length; i++) {
      const b = keys[i];
      if (u <= b[0]) {
        const a = keys[i - 1], span = b[0] - a[0], f = span > 1e-12 ? (u - a[0]) / span : 1;
        return [1, 2, 3, 4].map(j => a[j] + (b[j] - a[j]) * f);
      }
    }
    const k = keys[keys.length - 1];
    return [k[1], k[2], k[3], k[4]];
  }
  /** media.light_stops: degradado vertical de la luz [[u, r, g, b, a]] (u 0 = arriba) o []. */
  function lightStops(sheen, sway, amount, holo) {
    const amt = clamp(num(amount, 1.0), 0, 1);
    if (amt <= 1e-4) return [];
    const layers = [];
    const gm = GLOSS_A * amt * (1 - 0.35 * clamp(num(sway, 0) / SWAY, -1, 1));
    layers.push(GLOSS.map(([u, f]) => [u, 255, 255, 255, f * gm]));
    if (sheen) {
      const c = num(sheen[0], 0), env = clamp(num(sheen[1], 0), 0, 1);
      const a = SHEEN_A * amt * env;
      layers.push(BAND.map(([u, f]) => [c + u, 255, 255, 255, f * a]));
      const ho = HOLO_A * amt * env * clamp(num(holo, 0), 0, 1);
      if (ho > 1e-4) layers.push(HOLO.map(([u, rgb, f]) => [c + u, rgb[0], rgb[1], rgb[2], f * ho]));
    }
    const us = [0, 1];
    layers.forEach(lay => lay.forEach(k => { if (k[0] > 0 && k[0] < 1) us.push(k[0]); }));
    us.sort((x, y) => x - y);
    const out = [];
    let peak = 0;
    for (const u of us) {
      if (out.length && u - out[out.length - 1][0] < 1e-6) continue;
      let pr = 1, pg = 1, pb = 1, pa = 1;
      for (const lay of layers) {
        const [r, g, b, a] = keyAt(lay, u);
        pr *= 1 - r / 255 * a; pg *= 1 - g / 255 * a; pb *= 1 - b / 255 * a; pa *= 1 - a;
      }
      const A = 1 - pa;
      const rgb = A > 1e-9 ? [pr, pg, pb].map(q => clamp(255 * (1 - q) / A, 0, 255)) : [255, 255, 255];
      peak = Math.max(peak, A);
      out.push([u, rgb[0], rgb[1], rgb[2], A]);
    }
    return peak >= 0.5 / 255 ? out : [];
  }
  /** media._interp (= numpy.interp): valor lineal por tramos de la columna j de `st` en u. */
  function interpCol(st, u, j) {
    if (u <= st[0][0]) return st[0][j];
    const n = st.length;
    if (u >= st[n - 1][0]) return st[n - 1][j];
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (st[m][0] <= u) lo = m; else hi = m; }
    const a = st[lo], b = st[hi], span = b[0] - a[0];
    return span > 0 ? a[j] + (b[j] - a[j]) * (u - a[0]) / span : b[j];
  }
  /** media.light_at: la luz del cuadro lt CON desenfoque de movimiento (la franja promediada en un
      obturador de SHUTTER s, SHUTTER_N muestras, premultiplicada) -> [[u, r, g, b, a]] o []. */
  function lightAt(lt, dur, sway, amount, holo, inDur, outDur) {
    const amt = clamp(num(amount, 1.0), 0, 1);
    if (amt <= 1e-4) return [];
    const t = num(lt, 0), shs = [];
    for (let i = 0; i < SHUTTER_N; i++) shs.push(sheenState(t + SHUTTER * ((i + 0.5) / SHUTTER_N - 0.5), dur, inDur, outDur));
    if (shs.every(x => x === null)) return lightStops(null, sway, amt, holo);
    const lists = shs.map(x => lightStops(x, sway, amt, holo));
    const us = [0, 1];
    lists.forEach(l => l.forEach(k => us.push(k[0])));
    us.sort((x, y) => x - y);
    const uu = [];
    for (const u of us) if (!uu.length || u - uu[uu.length - 1] >= 1e-6) uu.push(u);
    const pa = uu.map(() => 0), pr = uu.map(() => [0, 0, 0]);
    for (const l of lists) {
      if (!l.length) continue;
      uu.forEach((u, i) => {
        const a = interpCol(l, u, 4);
        pa[i] += a;
        for (let j = 0; j < 3; j++) pr[i][j] += interpCol(l, u, 1 + j) * a;
      });
    }
    let peak = 0;
    const out = uu.map((u, i) => {
      const A = pa[i] / SHUTTER_N;
      peak = Math.max(peak, A);
      const rgb = A > 1e-12 ? pr[i].map(q => clamp(q / SHUTTER_N / A, 0, 255)) : [255, 255, 255];
      return [u, rgb[0], rgb[1], rgb[2], A];
    });
    return peak >= 0.5 / 255 ? out : [];
  }
  /** media.speed_of: velocidad del clip (solo fit 'speed'). */
  function speedOf(it) { return it && it.fit === "speed" ? clamp(num(it.speed, 1.0), 0.25, 4.0) : 1.0; }
  /** media.src_time: segundo del archivo que se ve en lt (congelado en el ultimo cuadro). */
  function srcTime(it, lt, srcDur, fps) {
    let t = Math.max(0, num(it && it.src_in, 0)) + Math.max(0, num(lt, 0)) * speedOf(it);
    const d = num(srcDur, 0);
    if (d > 0) t = Math.min(t, Math.max(0, d - 1 / Math.max(1, num(fps, 30))));
    return t;
  }
  /** media.cover_rect: rect fuente [x, y, w, h] que llena dw x dh, centrado, con zoom. */
  function coverRect(iw, ih, dw, dh, zoom) {
    iw = Math.max(1e-6, +iw); ih = Math.max(1e-6, +ih);
    const ar = Math.max(1e-6, +dw) / Math.max(1e-6, +dh);
    let sw, sh;
    if (iw / ih > ar) { sw = ih * ar; sh = ih; } else { sw = iw; sh = iw / ar; }
    const z = Math.max(1, num(zoom, 1));
    sw /= z; sh /= z;
    return [(iw - sw) / 2, (ih - sh) / 2, sw, sh];
  }
  /** media.tilt_quad: esquinas [tl, tr, br, bl] de la tarjeta inclinada sobre el eje horizontal. */
  function tiltQuad(w, h, tiltDeg, scale, persp) {
    const th = num(tiltDeg, 0) * Math.PI / 180, d = Math.max(1e-3, (persp == null ? PERSP : persp) * h), s = scale == null ? 1 : +scale;
    return [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]].map(([x, y]) => {
      const z = -y * Math.sin(th), k = d / (d + z);
      return [x * k * s, y * Math.cos(th) * k * s];
    });
  }
  /** media.card_quad: esquinas de la tarjeta ANIMADA en px de la caja. */
  function cardQuad(card, bw, bh, st) {
    const [x0, y0, w, h] = cardPx(card, bw, bh);
    const cx = x0 + w / 2, cy = y0 + h / 2 + num(st && st.dy, 0) * bh;
    return tiltQuad(w, h, num(st && st.tilt, 0), num(st && st.scale, 1)).map(([x, y]) => [cx + x, cy + y]);
  }
  /** media.warp_strips: la perspectiva en franjas afines [[v0, v1, y0, y1, kx]] (v de -h/2 a h/2). */
  function warpStrips(w, h, tiltDeg, scale, persp) {
    const s = num(scale, 1), t = num(tiltDeg, 0);
    if (Math.abs(t) <= 0.02) return [[-h / 2, h / 2, -h / 2 * s, h / 2 * s, s]];
    const th = t * Math.PI / 180, sn = Math.sin(th), cs = Math.cos(th);
    const d = Math.max(1e-3, (persp == null ? PERSP : persp) * h);
    const dk = d * Math.abs(sn) / (d - h / 2 * Math.abs(sn)) ** 2 * h;   // max |dk/dv| * h
    const n = Math.trunc(clamp(Math.ceil(dk * Math.abs(s) * num(w, 0) / 2 / WARP_STEP), WARP_MIN, WARP_MAX));
    const proj = v => { const k = d / (d - v * sn); return [v * cs * k * s, k * s]; };
    const out = [];
    let ya = proj(-h / 2)[0];
    for (let i = 0; i < n; i++) {
      const v0 = -h / 2 + h * i / n, v1 = -h / 2 + h * (i + 1) / n, yb = proj(v1)[0];
      out.push([v0, v1, ya, yb, proj((v0 + v1) / 2)[1]]);
      ya = yb;
    }
    return out;
  }

  // ------------------------------------------------------------------ vista en vivo (canvas 2D)
  /** Filtro CSS extra para el video base mientras hay tarjeta (aproximacion de media.draw_background):
      blur(sigma) brightness(1 - dim * bg). `boxW` = ancho de la caja en px del canvas. */
  function bgFilter(it, bg, boxW) {
    const a = c01(num(bg, 0));
    if (a <= 0.003) return "";
    const b = clamp(num(param(it, "blur"), DEFAULTS.blur), 0, 1), dm = clamp(num(param(it, "dim"), DEFAULTS.dim), 0, 0.9);
    const sig = BLUR_PX * b * (num(boxW, 1080) / 1080) * (0.35 + 0.65 * a) * a;
    const parts = [];
    if (sig > 0.05) parts.push(`blur(${sig.toFixed(2)}px)`);
    if (dm > 0.001) parts.push(`brightness(${(1 - dm * a).toFixed(3)})`);
    return parts.join(" ");
  }
  function rrectPath(g, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }
  /** Tarjeta en un contexto 2D. B = caja de layout {x, y, w, h} (px del canvas); frame = {src, w, h}
      (imagen o <video>; null = marcador gris); baked = el cuadro ya viene recortado a la tarjeta.
      Devuelve el rect en reposo {x, y, w, h} (px del canvas) o null si no se ve.
      Como el render: perspectiva por franjas afines (warpStrips; cada franja recorta sus filas,
      alineadas al pixel del canvas: sin costuras), Ken Burns con paralaje, luz de lightStops y
      el borde que se ilumina; la sombra va en afin con el rectangulo medio del cuadrilatero. */
  function drawCard(g, frame, B, card, st, it, lt, dur, baked) {
    if (!st || st.alpha <= 0.003) return null;
    const [x0, y0, w, h] = cardPx(card, B.w, B.h);
    const cx = B.x + x0 + w / 2, cy = B.y + y0 + h / 2 + num(st.dy, 0) * B.h;
    const r = clamp(num(param(it, "radius"), RADIUS), 0, 0.5) * w;
    const s = num(st.scale, 1), tilt = num(st.tilt, 0);
    const quad = tiltQuad(w, h, tilt, s), strips = warpStrips(w, h, tilt, s);
    const qt = Math.min(quad[0][1], quad[1][1]), qb = Math.max(quad[2][1], quad[3][1]);
    const amt = sheenAmount(it, baked);
    const stops = amt > 1e-3 ? lightAt(lt, dur, num(st.sway, 0), amt, param(it, "holo"), param(it, "in_dur"), param(it, "out_dur")) : [];
    let light = null;
    if (stops.length && typeof g.createLinearGradient === "function") {
      const lg = g.createLinearGradient(0, -h / 2, 0, h / 2);
      if (lg && typeof lg.addColorStop === "function") {
        stops.forEach(([u, R, G, Bl, a]) => lg.addColorStop(clamp(u, 0, 1), `rgba(${R.toFixed(1)},${G.toFixed(1)},${Bl.toFixed(1)},${a.toFixed(4)})`));
        light = lg;
      }
    }
    // medio: ventana fuente con Ken Burns y paralaje (solo imagenes; un clip va tal cual, como el export)
    let src = null;
    if (frame && frame.src) {
      const z = baked ? 1 : kenburnsZoom(lt, dur, isVideo(it) ? 0 : param(it, "kenburns"));
      src = baked ? [0, 0, frame.w, frame.h] : coverRect(frame.w, frame.h, w, h, z);
      if (!baked) src[1] += parallaxPx(st.fdy, B.h, h, z) * src[3] / h;
    }
    const bwid = Math.max(1, 0.0028 * w), border = param(it, "border");
    g.save();
    g.globalAlpha *= c01(num(st.alpha, 1));
    g.translate(cx, cy);
    const shadow = clamp(num(param(it, "shadow"), DEFAULTS.shadow), 0, 1);
    if (shadow > 0.001) {
      const lift = clamp(num(st.lift, 0), -1, 1);
      g.save(); g.translate(0, (qt + qb) / 2); g.scale((quad[1][0] + quad[2][0]) / w, (qb - qt) / h);
      g.shadowColor = `rgba(0,0,0,${(0.55 * shadow * (1 - 0.12 * lift)).toFixed(3)})`; g.shadowBlur = 0.09 * w;
      g.shadowOffsetY = 0.028 * w * (1 + 0.3 * lift);
      g.fillStyle = CARD_BG; rrectPath(g, -w / 2, -h / 2, w, h, r); g.fill(); g.restore();
    }
    // filas de cada franja alineadas al pixel del canvas (recorte sin medio pixel: sin costuras)
    const T = typeof g.getTransform === "function" ? g.getTransform() : null;
    const snap = y => (T && Math.abs(T.d) > 1e-9 && Math.abs(T.b) < 1e-9 ? (Math.round(T.d * y + T.f) - T.f) / T.d : y);
    const last = strips.length - 1, BIG = 1e5;
    strips.forEach(([v0, v1, ya, yb, kx], i) => {
      g.save();
      if (last) {
        const top = i === 0 ? -BIG : snap(ya), bot = i === last ? BIG : snap(yb);
        g.beginPath(); g.rect(-BIG, top, 2 * BIG, bot - top); g.clip();
      }
      const sy = (yb - ya) / (v1 - v0);
      g.transform(kx, 0, 0, sy, 0, ya - v0 * sy);
      rrectPath(g, -w / 2, -h / 2, w, h, r); g.clip();
      // fondo opaco de la tarjeta (= media.draw_card): una imagen con transparencia va sobre el
      g.fillStyle = CARD_BG; g.fillRect(-w / 2, v0 - 2, w, v1 - v0 + 4);
      if (src) { try { g.drawImage(frame.src, src[0], src[1], src[2], src[3], -w / 2, -h / 2, w, h); } catch (e) { /* cuadro aun no decodificado */ } }
      if (light) { g.fillStyle = light; g.fillRect(-w / 2, v0 - 2, w, v1 - v0 + 4); }
      if (border) {
        // anillo claro fino (= drawDRRect del render) y la luz que lo cruza
        rrectPath(g, -w / 2, -h / 2, w, h, r);
        const ri = Math.max(0, r - bwid), xi = -w / 2 + bwid, yi = -h / 2 + bwid, wi = w - 2 * bwid, hi = h - 2 * bwid;
        g.moveTo(xi + ri, yi); g.arcTo(xi, yi, xi, yi + hi, ri); g.arcTo(xi, yi + hi, xi + wi, yi + hi, ri);
        g.arcTo(xi + wi, yi + hi, xi + wi, yi, ri); g.arcTo(xi + wi, yi, xi, yi, ri); g.closePath();
        g.fillStyle = "rgba(255,255,255,0.5)"; g.fill("evenodd");
        if (light) { const ga = g.globalAlpha; g.globalAlpha = ga * RIM_A; g.fillStyle = light; g.fill("evenodd"); g.globalAlpha = ga; }
      }
      g.restore();
    });
    g.restore();
    return { x: B.x + x0, y: B.y + y0, w, h };
  }

  return {
    SAFE, XC, SYM_W, CARD_W, ASPECT, ASPECT_TALL, RADIUS, IN_DUR, OUT_DUR, KENBURNS, DEFAULTS, LIVE_DEFAULTS, STYLES, FITS, ROOM_OK, CARD_BG,
    FLOAT_AMP, FLOAT_T, BREATH, SWAY, SWAY_T, PERSP, KB_LEAD, PARALLAX, PARALLAX_MAX, SHEEN_A, SHEEN_DELAY, SHEEN_EVERY,
    SHEEN_DUR, SHEEN_MIN, SHEEN_OUT, SHEEN_EASE, SHUTTER, SHUTTER_N, GLOSS_A, HOLO_A, RIM_A, WARP_STEP, WARP_MIN, WARP_MAX,
    param, isVideo, sheenAmount, captionTop, captionBottom, cardBand, cardLayout, cardPx, cardAnim, kenburnsZoom, parallaxPx,
    sheenState, lightStops, lightAt, cardQuad, warpStrips, speedOf, srcTime, coverRect, tiltQuad,
    bgFilter, drawCard,
  };
});
