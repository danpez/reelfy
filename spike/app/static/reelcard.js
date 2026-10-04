/* Reelfy Studio: videos de apoyo en fragmentos (pista fx.media, estilo "reel") en la vista en vivo. Port
   PURO de spike/scripts/motion/reel.py (tiempo de cada toma, recortes y tarjeta identicos;
   tests/test_reel_card.py compara ambos) + el dibujo en canvas 2D (pantalla completa: cada toma recortada
   a la caja; ventana: FloatCard.drawCard con el look de la grabacion de pantalla). En el navegador queda
   como window.ReelCard; en node (pruebas) se usa con require().

   Uso desde fx.js (t = tiempo ORIGINAL del video compuesto):
     const R = window.ReelCard, L = finalEnd - finalStart;          // el tramo FINAL (sin silencios)
     const rows = R.fit(it.pieces, L, 30);                           // [[t0, t1, src_in]] locales
     const j = R.pieceAt(rows, lt), ts = R.srcTime(rows, lt);        // toma y segundo del archivo
     R.layoutOf(it) === "full" ? R.fullCrop(cx, w, h, B.w / B.h) : R.windowLayout(it, w, h, ...)
   Diferencia con el render: el fondo de la ventana es el filtro CSS del video (FloatCard.bgFilter). */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ReelCard = api;
})(typeof self !== "undefined" ? self : this, function (root) {
  "use strict";
  const FC = (root && root.FloatCard) || (typeof require === "function" ? require("./floatcard.js") : null);
  const SC = (root && root.ScreenCard) || (typeof require === "function" ? require("./screencard.js") : null);

  // ------------------------------------------------------------------ constantes (espejo de reel.py)
  const STYLE = "reel", LAYOUTS = ["window", "full"], ASPECT_DEFAULT = 16 / 9, SYM_W = 0.76;
  const DEFAULTS = { blur: 0.55, dim: 0.28, radius: 0.055, shadow: 0.6, border: true, motion: null, width: null, vignette: 0, layout: "window" };

  const num = (v, d) => { if (typeof v === "boolean" || v === null || v === undefined || v === "") return d; const f = +v; return isFinite(f) ? f : d; };
  const clamp = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x);
  const r5 = v => Math.floor(v * 1e5 + 0.5) / 1e5;
  const r6 = v => Math.floor(v * 1e6 + 0.5) / 1e6;

  function param(it, k) { const v = it ? it[k] : undefined; return v === undefined || v === null ? DEFAULTS[k] : v; }
  function layoutOf(it) { const v = it && it.layout; return LAYOUTS.includes(v) ? v : "window"; }
  /** Pantalla completa: "cut"; ventana: = ScreenCard.motionOf. */
  function motionOf(it, style) { return layoutOf(it) === "full" ? "cut" : SC.motionOf(it, style); }
  function look(it) {
    return { radius: param(it, "radius"), shadow: param(it, "shadow"), border: param(it, "border"), sheen: 0, kenburns: 0, kind: "video" };
  }

  // ------------------------------------------------------------------ tiempo (= reel.fit)
  function piecesOf(it) {
    return ((it && it.pieces) || []).filter(p => p && isFinite(+p.in) && isFinite(+p.out) && +p.out - +p.in > 1e-6 && +p.in >= 0);
  }
  function fit(pieces, L, fps) {
    fps = fps || 30;
    const ps = [];
    (pieces || []).forEach(p => {
      if (!p) return;
      const a = num(p.in, NaN), b = num(p.out, NaN);
      if (!isFinite(a) || !isFinite(b) || b - a <= 1e-6) return;
      ps.push([a, b, Math.max(b, num(p.max, b)), ps.length]);
    });
    L = Math.max(0, num(L, 0));
    if (!ps.length || L <= 0) return [];
    let d = ps.map(p => p[1] - p[0]);
    const S = d.reduce((x, y) => x + y, 0);
    if (S >= L) d = d.map(x => x * L / S);
    else {
      let extra = L - S;
      for (let j = ps.length - 1; j >= 0; j--) {
        const add = Math.min(ps[j][2] - ps[j][1], extra);
        d[j] += add; extra -= add;
        if (extra <= 1e-9) break;
      }
      if (extra > 1e-9) d[d.length - 1] += extra;
    }
    const out = []; let t = 0, F0 = 0;
    for (let j = 0; j < ps.length; j++) {
      t += d[j];
      const F1 = j < ps.length - 1 ? Math.floor(t * fps + 0.5) : Math.floor(L * fps + 0.5);
      if (F1 <= F0) continue;
      out.push([r6(F0 / fps), r6(F1 / fps), r6(ps[j][0]), ps[j][3], r6(ps[j][2])]);
      F0 = F1;
    }
    return out;
  }
  /** = reel.hold_frames / src_cap: nunca se lee la fuente en o despues del fin de la toma (src_max): un
      pedazo quitado/cambiado o un Fin alargado congela el ultimo cuadro de SU toma. */
  function holdFrames(row, fps) {
    fps = fps || 30;
    if (!row || row.length < 5) return 1 << 30;
    return Math.max(1, Math.floor((+row[4] - +row[2]) * fps + 1e-6));
  }
  function srcCap(row, t, fps) {
    fps = fps || 30;
    if (!row || row.length < 5) return t;
    return Math.min(t, +row[2] + (holdFrames(row, fps) - 1) / fps);
  }
  /** = reel.piece_at: fila que contiene lt (en una union gana la siguiente). */
  function pieceAt(rows, lt) {
    if (!rows || !rows.length) return -1;
    for (let j = 0; j < rows.length; j++) if (rows[j][0] - 1e-9 <= lt && lt < rows[j][1] - 1e-9) return j;
    return lt < rows[0][0] ? 0 : rows.length - 1;
  }
  function srcTime(rows, lt, maxT, fps) {
    const j = pieceAt(rows, lt); if (j < 0) return null;
    const r = rows[j], t = srcCap(r, r[2] + Math.min(Math.max(lt, r[0]), r[1]) - r[0], fps);
    return maxT == null ? t : Math.min(t, maxT);
  }

  // ------------------------------------------------------------------ geometria
  /** = reel.full_crop: [x0, y0, x1, y1] del recorte de pantalla completa o null (la fuente entera). */
  function fullCrop(cx, srcW, srcH, boxAspect) {
    const w = num(srcW, 0), h = num(srcH, 0);
    if (w <= 0 || h <= 0) return null;
    const ba = clamp(num(boxAspect, 9 / 16), 0.2, 5.0), sa = w / h;
    if (sa > ba + 1e-6) {
      const cw = ba / sa, c = clamp(num(cx, 0.5), cw / 2, 1 - cw / 2);
      return [r5(c - cw / 2), 0, r5(c + cw / 2), 1];
    }
    if (sa < ba - 1e-6) { const ch = sa / ba; return [0, r5(0.5 - ch / 2), 1, r5(0.5 + ch / 2)]; }
    return null;
  }
  function winCrop(p) {
    const c = p && p.win;
    if (!Array.isArray(c) || c.length !== 4) return null;
    const v = c.map(x => clamp(+x, 0, 1));
    if (!v.every(isFinite) || v[2] - v[0] < 0.1 || v[3] - v[1] < 0.1) return null;
    return v;
  }
  /** = reel.win_view: el recorte `win` agrandado a la proporcion de la tarjeta (el sujeto entero). */
  function winView(p, srcW, srcH, aspect) {
    const c = winCrop(p), w = num(srcW, 0), h = num(srcH, 0), a = num(aspect, 0);
    if (!c || w <= 0 || h <= 0 || a <= 0) return c;
    let cw = (c[2] - c[0]) * w, ch = (c[3] - c[1]) * h;
    if (cw / ch < a) cw = Math.min(w, ch * a); else ch = Math.min(h, cw / a);
    const fw = cw / w, fh = ch / h, cx = (c[0] + c[2]) / 2, cy = (c[1] + c[3]) / 2;
    const x0 = clamp(cx - fw / 2, 0, 1 - fw), y0 = clamp(cy - fh / 2, 0, 1 - fh);
    const out = [r5(x0), r5(y0), r5(x0 + fw), r5(y0 + fh)];
    return out[0] === 0 && out[1] === 0 && out[2] === 1 && out[3] === 1 ? null : out;
  }
  function windowAspect(it, srcW, srcH) {
    let w = num(srcW, 0), h = num(srcH, 0);
    const ps = piecesOf(it), c = ps.length ? winCrop(ps[0]) : null;
    if (w > 0 && h > 0 && c) { w *= c[2] - c[0]; h *= c[3] - c[1]; }
    const a = w > 0 && h > 0 ? w / h : ASPECT_DEFAULT;
    return clamp(a, FC.ASPECT_TALL, FC.ASPECT_MAX);
  }
  /** = reel.window_layout: FloatCard.cardLayout con la proporcion de la fuente. */
  function windowLayout(it, srcW, srcH, boxAspect, capTop, capBottom) {
    return FC.cardLayout(srcW, srcH, boxAspect, capTop, param(it, "width") || SYM_W, windowAspect(it, srcW, srcH), null, capBottom);
  }
  function anim(lt, dur, motion) { return SC.anim(lt, dur, motion); }
  function bgFilter(it, bg, boxW) { return FC.bgFilter({ blur: param(it, "blur"), dim: param(it, "dim") }, bg, boxW); }

  // ------------------------------------------------------------------ dibujo
  /** Pantalla completa: `frame` = {src, w, h} recortado a `crop` cubriendo la caja B; vineta opcional. */
  function drawFull(g, frame, B, crop, it) {
    if (frame && frame.src) {
      const c = crop || [0, 0, 1, 1];
      const sx = c[0] * frame.w, sy = c[1] * frame.h, sw = (c[2] - c[0]) * frame.w, sh = (c[3] - c[1]) * frame.h;
      const k = Math.max(B.w / sw, B.h / sh), dw = sw * k, dh = sh * k;
      g.drawImage(frame.src, sx, sy, sw, sh, B.x + (B.w - dw) / 2, B.y + (B.h - dh) / 2, dw, dh);
    } else {
      g.fillStyle = "#101014"; g.fillRect(B.x, B.y, B.w, B.h);
    }
    const v = clamp(num(param(it, "vignette"), 0), 0, 1);
    if (v > 1e-3 && typeof g.createRadialGradient === "function") {
      const gr = g.createRadialGradient(B.x + B.w / 2, B.y + B.h * 0.45, B.w * 0.25, B.x + B.w / 2, B.y + B.h * 0.45, B.h * 0.75);
      gr.addColorStop(0, "rgba(0,0,0,0)"); gr.addColorStop(1, `rgba(0,0,0,${(0.55 * v).toFixed(3)})`);
      g.fillStyle = gr; g.fillRect(B.x, B.y, B.w, B.h);
    }
    return { x: B.x, y: B.y, w: B.w, h: B.h };
  }
  /** Ventana: la toma (su recorte `win`) en la tarjeta. */
  function drawWindow(g, frame, B, card, st, it, lt, dur, crop) {
    const f = frame && frame.src && crop ? Object.assign({}, frame, { rect: [crop[0] * frame.w, crop[1] * frame.h, (crop[2] - crop[0]) * frame.w, (crop[3] - crop[1]) * frame.h] }) : frame;
    return FC.drawCard(g, f, B, card, st, look(it), lt, dur, false);
  }

  // ------------------------------------------------------------------ edicion (Studio)
  /* El catalogo de cada video de apoyo viaja en plan.montage.broll.sources[] ({sid, name, shots: [{i, a, b,
     cut_a, cut_b, subject, what, text, ok_window, ok_full, why, cx, win_crop}]}). Cambiar una toma o el
     formato de un fragmento usa SOLO tomas que sirven en ese formato (sin personas, sin cortar textos) y
     que no estan en otro fragmento del video. */
  const MARGIN = 0.08, PIECE_MIN = 0.9;
  /** = brollplan._avail: [inicio, fin] utilizable de una toma (sin el cuadro del corte). */
  function avail(sh) {
    let a = num(sh && sh.a, 0), b = num(sh && sh.b, 0);
    if (!sh || sh.cut_a !== false) a += MARGIN;
    if (!sh || sh.cut_b !== false) b -= MARGIN;
    return [a, Math.max(a, b)];
  }
  function okFor(sh, layout) { return !!(sh && (layout === "full" ? sh.ok_full : sh.ok_window)); }
  function sourceOf(plan, sid) {
    const b = plan && plan.montage && plan.montage.broll;
    return ((b && b.sources) || []).find(s => s && s.sid === sid) || null;
  }
  /** Tomas usadas por los fragmentos del video (salvo `exceptId`): Set "sid|i". */
  function usedShots(plan, exceptId) {
    const out = new Set();
    (((plan && plan.fx) || {}).media || []).forEach(m => {
      if (!m || m.style !== STYLE || m.id === exceptId) return;
      (m.pieces || []).forEach(p => out.add(`${m.sid}|${p.shot}`));
    });
    return out;
  }
  /** Tomas que pueden entrar en el fragmento `it` (en su formato o `layout`): sirven, no estan en otro
      fragmento ni en este, y duran lo minimo. Ordenadas por cercania (en el video de apoyo) a `near`. */
  function options(plan, it, layout, near) {
    const src = sourceOf(plan, it && it.sid); if (!src) return [];
    const lay = layout || layoutOf(it), used = usedShots(plan, it.id);
    (it.pieces || []).forEach(p => used.add(`${it.sid}|${p.shot}`));
    const ref = near == null ? null : +near;
    return (src.shots || []).filter(sh => okFor(sh, lay) && !used.has(`${it.sid}|${sh.i}`) && avail(sh)[1] - avail(sh)[0] >= PIECE_MIN * 0.6)
      .sort((x, y) => ref == null ? x.a - y.a : Math.abs(x.a - ref) - Math.abs(y.a - ref));
  }
  /** La toma `sh` en lugar de la pieza j (misma duracion, sin pasar del final de su toma). */
  function pieceFor(sh, dur, why) {
    const [a, mx] = avail(sh), d = Math.max(0.3, Math.min(num(dur, 1.5), mx - a));
    return { shot: sh.i, in: r6(a), out: r6(a + d), max: r6(mx), cx: sh.cx == null ? null : sh.cx, win: sh.win_crop || null, why: why || "" };
  }
  function swap(it, j, sh, why) {
    const ps = (it.pieces || []).map(p => Object.assign({}, p));
    if (!ps[j] || !sh) return ps;
    ps[j] = pieceFor(sh, num(ps[j].out, 0) - num(ps[j].in, 0), why);
    return ps;
  }
  /** Las piezas del fragmento en otro formato: las que no sirven ahi se cambian por la toma libre mas
      cercana que si sirve. null si no alcanzan las tomas. */
  function relayout(plan, it, layout, why) {
    const src = sourceOf(plan, it && it.sid); if (!src) return null;
    const byI = {}; (src.shots || []).forEach(sh => { byI[sh.i] = sh; });
    const out = [], taken = usedShots(plan, it.id);
    (it.pieces || []).forEach(p => { if (okFor(byI[p.shot], layout)) taken.add(`${it.sid}|${p.shot}`); });
    for (const p of it.pieces || []) {
      const sh = byI[p.shot];
      if (okFor(sh, layout)) { out.push(Object.assign({}, p, { cx: sh.cx == null ? p.cx : sh.cx, win: sh.win_crop || null })); continue; }
      const alt = (src.shots || []).filter(x => okFor(x, layout) && !taken.has(`${it.sid}|${x.i}`) && avail(x)[1] - avail(x)[0] >= PIECE_MIN * 0.6)
        .sort((x, y) => Math.abs(x.a - num(p.in, 0)) - Math.abs(y.a - num(p.in, 0)))[0];
      if (!alt) return null;
      taken.add(`${it.sid}|${alt.i}`);
      out.push(pieceFor(alt, num(p.out, 0) - num(p.in, 0), why));
    }
    return out;
  }

  return {
    STYLE, LAYOUTS, ASPECT_DEFAULT, DEFAULTS, MARGIN,
    param, layoutOf, motionOf, look, piecesOf, fit, holdFrames, srcCap, pieceAt, srcTime, fullCrop, winCrop, winView, windowAspect, windowLayout, anim, bgFilter,
    drawFull, drawWindow, avail, okFor, sourceOf, usedShots, options, pieceFor, swap, relayout,
  };
});
