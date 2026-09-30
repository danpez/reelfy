/* Reelfy Studio: recursos flotantes del guion (pista fx.media, estilo "float_card") en la vista
   en vivo. Port PURO de spike/scripts/motion/media.py (geometria y animacion identicas paso a
   paso; tests/test_media_float_preview.py compara ambos) + un dibujo aproximado en canvas 2D.
   En el navegador queda como window.FloatCard; en node (pruebas) se usa con require().

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
   Diferencias con el render: la inclinacion 3D de la entrada se aproxima con escala vertical
   (canvas 2D no tiene perspectiva) y el fondo usa el filtro CSS del video en vez del cuadro
   compuesto. Todas las fracciones son de la CAJA DE LAYOUT (x del ancho, y del alto). */
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
  const FLOAT_AMP = 0.004, FLOAT_T = 3.2, BREATH = 0.004, KENBURNS = 0.08;
  const DEFAULTS = { blur: 0.55, dim: 0.28, radius: RADIUS, shadow: 0.6, border: true,
    kenburns: KENBURNS, float: 1.0, in_dur: IN_DUR, out_dur: OUT_DUR };
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
  /** Parametro de estilo del item (o su default). */
  function param(it, k) { const v = it ? it[k] : undefined; return v === undefined || v === null ? DEFAULTS[k] : v; }

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
  /** media.card_anim -> {alpha, scale, dy, tilt, bg}. */
  function cardAnim(lt, dur, inDur, outDur, floatAmt) {
    dur = Math.max(1e-3, num(dur, 1.0));
    lt = clamp(num(lt, 0), 0, dur);
    const tin = Math.max(1e-3, Math.min(num(inDur, IN_DUR), 0.4 * dur));
    const tout = Math.max(1e-3, Math.min(num(outDur, OUT_DUR), 0.3 * dur));
    const sp = spring(lt, SPRING_F, SPRING_Z);
    let alpha = outCubic(c01(lt / (0.6 * tin)));
    let scale = POP_FROM + (1 - POP_FROM) * sp;
    let dy = RISE * (1 - sp);
    const tilt = TILT * (1 - sp);
    let bg = smoothstep(c01(lt / (1.2 * tin)));
    const fa = num(floatAmt, 1.0);
    const k = smoothstep(c01(lt / (2 * tin)));
    dy += FLOAT_AMP * fa * k * Math.sin(2 * Math.PI * lt / FLOAT_T);
    scale *= 1 + BREATH * fa * k * Math.sin(2 * Math.PI * lt / (1.5 * FLOAT_T));
    const q = c01((dur - lt) / tout);
    if (q < 1) {
      const pe = 1 - q;
      alpha *= smoothstep(q);
      scale *= 1 - EXIT_SCALE * inCubic(pe);
      dy -= EXIT_RISE * inCubic(pe);
      bg *= smoothstep(q);
    }
    return { alpha, scale, dy, tilt, bg };
  }
  /** media.kenburns_zoom: 1 -> 1 + amount (in_out_quad) en todo el item. */
  function kenburnsZoom(lt, dur, amount) {
    dur = Math.max(1e-3, num(dur, 1.0));
    return 1 + Math.max(0, num(amount, KENBURNS)) * inOutQuad(c01(num(lt, 0) / dur));
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
  /** media.tilt_quad: esquinas [tl, tr, br, bl] de la tarjeta inclinada (render; aqui solo pruebas). */
  function tiltQuad(w, h, tiltDeg, scale, persp) {
    const th = num(tiltDeg, 0) * Math.PI / 180, d = Math.max(1e-3, (persp == null ? 2.4 : persp) * h), s = scale == null ? 1 : +scale;
    return [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]].map(([x, y]) => {
      const z = -y * Math.sin(th), k = d / (d + z);
      return [x * k * s, y * Math.cos(th) * k * s];
    });
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
      Devuelve el rect en reposo {x, y, w, h} (px del canvas) o null si no se ve. */
  function drawCard(g, frame, B, card, st, it, lt, dur, baked) {
    if (!st || st.alpha <= 0.003) return null;
    const [x0, y0, w, h] = cardPx(card, B.w, B.h);
    const cx = B.x + x0 + w / 2, cy = B.y + y0 + h / 2 + num(st.dy, 0) * B.h;
    const r = clamp(num(param(it, "radius"), RADIUS), 0, 0.5) * w;
    const s = num(st.scale, 1), sy = s * Math.cos(num(st.tilt, 0) * Math.PI / 180);
    g.save();
    g.globalAlpha *= c01(num(st.alpha, 1));
    g.translate(cx, cy); g.scale(s, sy);
    const shadow = clamp(num(param(it, "shadow"), DEFAULTS.shadow), 0, 1);
    if (shadow > 0.001) {
      g.save(); g.shadowColor = `rgba(0,0,0,${(0.55 * shadow).toFixed(3)})`; g.shadowBlur = 0.09 * w; g.shadowOffsetY = 0.028 * w;
      g.fillStyle = "#1C1C22"; rrectPath(g, -w / 2, -h / 2, w, h, r); g.fill(); g.restore();
    }
    g.save(); rrectPath(g, -w / 2, -h / 2, w, h, r); g.clip();
    // fondo opaco de la tarjeta (= media.draw_card): una imagen con transparencia va sobre el
    g.fillStyle = CARD_BG; g.fillRect(-w / 2, -h / 2, w, h);
    if (frame && frame.src) {
      const z = baked ? 1 : kenburnsZoom(lt, dur, param(it, "kenburns"));
      const [sx, sy2, sw, sh] = baked ? [0, 0, frame.w, frame.h] : coverRect(frame.w, frame.h, w, h, z);
      try { g.drawImage(frame.src, sx, sy2, sw, sh, -w / 2, -h / 2, w, h); } catch (e) { /* cuadro aun no decodificado */ }
    }
    g.restore();
    if (param(it, "border")) {
      const bw = Math.max(1, 0.0028 * w);
      g.strokeStyle = "rgba(255,255,255,0.5)"; g.lineWidth = bw;
      rrectPath(g, -w / 2 + bw / 2, -h / 2 + bw / 2, w - bw, h - bw, Math.max(0, r - bw / 2)); g.stroke();
    }
    g.restore();
    return { x: B.x + x0, y: B.y + y0, w, h };
  }

  return {
    SAFE, XC, SYM_W, CARD_W, ASPECT, ASPECT_TALL, RADIUS, IN_DUR, OUT_DUR, KENBURNS, DEFAULTS, STYLES, FITS, ROOM_OK, CARD_BG,
    param, captionTop, captionBottom, cardBand, cardLayout, cardPx, cardAnim, kenburnsZoom, speedOf, srcTime, coverRect, tiltQuad,
    bgFilter, drawCard,
  };
});
