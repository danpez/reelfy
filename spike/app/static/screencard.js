/* Reelfy Studio: grabacion de pantalla sincronizada con una toma (pista fx.media, estilo "screen") en
   la vista en vivo. Port PURO de spike/scripts/motion/screen.py (geometria, animacion y sincronia
   identicas; tests/test_screen_card.py compara ambos) + el dibujo en canvas 2D (la ventana es
   FloatCard.drawCard con la fuente recortada a su marco; el fondo, FloatCard.bgFilter). En el navegador
   queda como window.ScreenCard; en node (pruebas) se usa con require().

   Uso desde fx.js (t = tiempo ORIGINAL del video compuesto, como todo el plan):
     const SC = window.ScreenCard;
     const cov = SC.coverage(it);                       // [a, b] o null: fuera no hay pantalla
     const tt = SC.syncTime(it, t);                     // segundo de la grabacion de pantalla en t
     const card = SC.layout(w, h, B.w / B.h, capTop, capBottom, it.width, SC.cropOf(it));
     const st = SC.anim(t - cov[0], cov[1] - cov[0], SC.motionOf(it, plan.director.style));
     SC.draw(g, frame, B, card, st, it, lt, dur)         // ventana (FloatCard.drawCard, sin brillo)
   Diferencia con el render: el fondo es el filtro CSS del video (FloatCard.bgFilter). */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ScreenCard = api;
})(typeof self !== "undefined" ? self : this, function (root) {
  "use strict";
  const FC = (root && root.FloatCard) || (typeof require === "function" ? require("./floatcard.js") : null);

  // ------------------------------------------------------------------ constantes (espejo de screen.py)
  const STYLE = "screen", MOTIONS = ["cut", "calm", "lively"], MOTION_BY_STYLE = { minimal: "cut", elegante: "calm" };
  const ASPECT_MIN = 0.40, ASPECT_MAX = 2.40, ASPECT_DEFAULT = 9 / 19.5, WIDTH_MIN = 0.30, TOP = 0.0625;
  const IN_DUR = 0.35, CALM_FROM = 0.965, CALM_RISE = 0.012, LIVELY_FLOAT = 0.5, UP_BIAS = 0.35, MARGIN = 0.015;
  const XC = 0.5, SYM_W = 0.76, RADIUS = 0.055;
  const DEFAULTS = { blur: 0.55, dim: 0.28, radius: RADIUS, shadow: 0.6, border: true, motion: null, width: null };

  const num = (v, d) => { if (typeof v === "boolean" || v === null || v === undefined || v === "") return d; const f = +v; return isFinite(f) ? f : d; };
  const clamp = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x);
  const c01 = x => clamp(x, 0, 1);
  const outCubic = p => { p = c01(p); return 1 - Math.pow(1 - p, 3); };
  const smoothstep = p => { p = c01(p); return p * p * (3 - 2 * p); };
  const r5 = v => Math.floor(v * 1e5 + 0.5) / 1e5;

  function param(it, k) { const v = it ? it[k] : undefined; return v === undefined || v === null ? DEFAULTS[k] : v; }
  /** "cut" | "calm" | "lively": el del item o, vacio, el del estilo (minimal: cut, elegante: calm, resto: lively). */
  function motionOf(it, style) {
    const m = it && it.motion;
    if (MOTIONS.includes(m)) return m;
    return MOTION_BY_STYLE[style || "minimal"] || "lively";
  }

  // ------------------------------------------------------------------ sincronia
  function syncPieces(it) {
    const s = it && it.sync, out = [];
    ((s && s.pieces) || []).forEach(p => {
      if (!Array.isArray(p) || p.length < 3) return;
      const a = +p[0], b = +p[1], c = +p[2];
      if (isFinite(a) && isFinite(b) && isFinite(c) && b > a) out.push([a, b, c]);
    });
    return out.sort((x, y) => x[0] - y[0]);
  }
  /** s del video compuesto -> s de la grabacion de pantalla (en una union gana el pedazo siguiente;
      fuera de todos, el mas cercano acotado) o null sin sincronia. */
  function syncTime(it, t) {
    const ps = syncPieces(it); if (!ps.length) return null;
    const s = it.sync || {}, off = num(s.offset, 0), rate = num(s.rate, 1) || 1;
    t = num(t, 0);
    let hit = ps.find(p => p[0] - 1e-6 <= t && t < p[1] - 1e-6) || null;
    if (!hit) {
      let best = Infinity;
      ps.forEach(p => { const d = p[0] <= t && t <= p[1] ? 0 : Math.min(Math.abs(t - p[0]), Math.abs(t - p[1])); if (d < best) { best = d; hit = p; } });
    }
    const tt = clamp(t, hit[0], hit[1]);
    return off + rate * (hit[2] + tt - hit[0]);
  }
  /** [a, b] (tiempo original) donde puede verse: [start, end] acotado a sus pedazos; null si nada. */
  function coverage(it) {
    const ps = syncPieces(it);
    let a = num(it && it.start, 0), b = num(it && it.end, 0);
    if (ps.length) { a = Math.max(a, ps[0][0]); b = Math.min(b, ps[ps.length - 1][1]); }
    return b - a > 1e-3 ? [a, b] : null;
  }

  // ------------------------------------------------------------------ geometria
  /** = screen.crop_of: [x0, y0, x1, y1] saneado (fracciones) o null (la grabacion completa). */
  function cropOf(it) {
    const c = it && it.crop;
    if (!Array.isArray(c) || c.length !== 4) return null;
    const v = c.map(Number);
    if (!v.every(isFinite)) return null;
    const [x0, y0, x1, y1] = v.map(c01);
    if (x1 - x0 < 0.1 || y1 - y0 < 0.1 || (x0 <= 0 && y0 <= 0 && x1 >= 1 && y1 >= 1)) return null;
    return [x0, y0, x1, y1];
  }
  /** = screen.src_aspect: proporcion de lo que se ve (dentro del marco), acotada. */
  function srcAspect(w, h, crop) {
    w = num(w, 0); h = num(h, 0);
    if (w > 0 && h > 0 && crop) { w *= crop[2] - crop[0]; h *= crop[3] - crop[1]; }
    return clamp(w > 0 && h > 0 ? w / h : ASPECT_DEFAULT, ASPECT_MIN, ASPECT_MAX);
  }
  /** = screen.band: la banda de la tarjeta flotante; si es la de arriba, desde TOP. */
  function band(boxAspect, capTop, capBottom) {
    let [top, bottom] = FC.cardBand(boxAspect, capTop, capBottom);
    if (Math.abs(top - (FC.SAFE.top + MARGIN)) < 1e-9) top = Math.min(top, TOP);
    return [top, bottom];
  }
  /** = screen.layout: ventana en fracciones de la caja, arriba de los subtitulos y centrada. */
  function layout(srcW, srcH, boxAspect, capTop, capBottom, width, crop) {
    const [top, bottom] = band(boxAspect, capTop, capBottom);
    const ba = clamp(num(boxAspect, 9 / 16), 0.2, 5.0);
    const asp = srcAspect(srcW, srcH, crop);
    const room = Math.max(0.08, bottom - top);
    const wmax = clamp(num(width, SYM_W), WIDTH_MIN, SYM_W);
    const w = Math.min(wmax, room * asp / ba), h = w * ba / asp;
    const cy = top + h / 2 + Math.max(0, room - h) * UP_BIAS;
    return { cx: XC, cy: r5(cy), w: r5(w), h: r5(h), aspect: r5(asp), radius: RADIUS };
  }
  /** Rect [x, y, w, h] (px de la fuente vw x vh) que se ve: el del marco recortado o el cuadro completo. */
  function srcRect(it, vw, vh) {
    const c = cropOf(it);
    if (!c) return [0, 0, vw, vh];
    return [c[0] * vw, c[1] * vh, (c[2] - c[0]) * vw, (c[3] - c[1]) * vh];
  }

  // ------------------------------------------------------------------ animacion
  /** = screen.anim: {alpha (siempre 1), scale, dy, tilt (0), sway, fdy, lift, bg}. */
  function anim(lt, dur, motion, inDur) {
    dur = Math.max(1e-3, num(dur, 1));
    lt = clamp(num(lt, 0), 0, dur);
    const tin = Math.max(1e-3, Math.min(num(inDur, IN_DUR), 0.4 * dur));
    let st;
    if (motion === "lively") {
      st = FC.cardAnim(lt, dur + 1e3, tin, FC.OUT_DUR, LIVELY_FLOAT);
      st.tilt = 0; st.sway = 0;
    } else if (motion === "calm") {
      const e = outCubic(c01(lt / tin));
      st = { scale: CALM_FROM + (1 - CALM_FROM) * e, dy: CALM_RISE * (1 - e), tilt: 0, sway: 0, fdy: 0, lift: 0 };
    } else {
      st = { scale: 1, dy: 0, tilt: 0, sway: 0, fdy: 0, lift: 0 };
    }
    st.alpha = 1;
    st.bg = motion === "calm" || motion === "lively" ? smoothstep(c01(lt / (0.6 * tin))) : 1;
    return st;
  }

  // ------------------------------------------------------------------ dibujo
  /** Filtro CSS del video base mientras se ve la pantalla (FloatCard.bgFilter con su desenfoque). */
  function bgFilter(it, bg, boxW) {
    return FC.bgFilter({ blur: param(it, "blur"), dim: param(it, "dim") }, bg, boxW);
  }
  /** = screen.look: lo que FloatCard.drawCard lee del item (sin brillo ni Ken Burns). */
  function look(it) {
    return { radius: param(it, "radius"), shadow: param(it, "shadow"), border: param(it, "border"), sheen: 0, kenburns: 0, kind: "video" };
  }
  /** La ventana: `frame` = {src, w, h} (el <video> de la grabacion) recortado a su marco. */
  function draw(g, frame, B, card, st, it, lt, dur) {
    const f = frame && frame.src ? Object.assign({}, frame, { rect: srcRect(it, frame.w, frame.h) }) : frame;
    return FC.drawCard(g, f, B, card, st, look(it), lt, dur, false);
  }

  return {
    STYLE, MOTIONS, MOTION_BY_STYLE, ASPECT_MIN, ASPECT_MAX, ASPECT_DEFAULT, WIDTH_MIN, TOP, IN_DUR, CALM_FROM, CALM_RISE,
    LIVELY_FLOAT, RADIUS, DEFAULTS,
    param, motionOf, syncPieces, syncTime, coverage, cropOf, srcAspect, band, layout, srcRect, anim, bgFilter, look, draw,
  };
});
