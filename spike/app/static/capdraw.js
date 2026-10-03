/* Reelfy Studio: subtitulos en un lienzo 2D, igual que el export (motion/text.py).
   Lo usan la vista previa del editor (FX.drawCaptions), la galeria de estilos y las vistas de
   Estilo. No toca globales del Studio: todo llega en `opt`.

   CapDraw.drawGroup(ctx, B, group, t, opt) -> caja dibujada {x, y, w, h} o null
     B     = caja de layout 9:16 en px del lienzo {x, y, w, h, s} (s = w / 1080)
     group = {words: [{text, start, end}], start, end, accent?, lines?, rect?} (tiempo original)
     opt   = {cfg (fx.captions), accent (color de la marca), srv (grupo del export: usa lines/rect y
              palabras clave), keywords? (palabras clave a la fuerza: ejemplos), yFrac (altura efectiva: y_ranges, foto con pie), rangeAlpha,
              titleFont (las letras del estilo: FXCore.captionFonts(plan) o la de titulo),
              fontCss(name, px), ensureFont(name) -> familia resuelta}
   Fiel al motor:
   - Pastilla de color (boxed): se DESLIZA con resorte de la palabra anterior a la actual y la
     primera crece de 0.6 a 1 (FXCore.pillAt = text._boxed_rrect), con sombra suave (0.3, desenfoque
     0.14 em, 0.06 em abajo) y la letra cambia de color EXACTO en la orilla de la pastilla (dentro,
     FXCore.contrastOn sin sombra; fuera, el texto base).
   - Karaoke: la orilla del relleno es el degradado del motor (marca en xe - 0.12 em, texto en xe).
   - Movimiento con las curvas del motor: el grupo entra con out_expo (Karaoke, Pastilla), cada
     palabra crece con el resorte del motor (pop 3.4 Hz / 0.6; pop_strong 3.0 Hz / 0.62), el renglon
     se recentra con su resorte (2.8 Hz / 0.82) y el grupo sale apagandose y encogiendo 4 % (in_quad).
     (Sin el giro al azar de «Impacto en mayúsculas»: depende de la semilla del render.)
   CapDraw.layout(group, cfg, B, opt) -> geometria cacheada (Map acotado; nada se guarda en el grupo).
   CapDraw.measurer(fontCss, font) -> (txt, px) => {adv, l, r, asc, desc} con su PROPIO lienzo. */
(function () {
  "use strict";
  const C = window.FXCore;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  // ------------------------------------------------------------------ medidas (lienzo propio)
  let MC = null;
  function mctx() {
    if (!MC) { const cv = typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(8, 8) : document.createElement("canvas"); MC = cv.getContext("2d"); }
    return MC;
  }
  const MCACHE = new Map();
  /** Medidas de un texto con la fuente real, a 100 px y escaladas (cacheadas por familia resuelta:
      la medida cambia cuando la fuente termina de cargar). */
  function measurer(fontCss, font) {
    return (txt, px) => {
      const css = fontCss(font, 100), key = css + "|" + txt;
      let m = MCACHE.get(key);
      if (!m) {
        const c = mctx(); c.font = css;
        const r = c.measureText(txt), adv = r.width;
        m = { adv, l: r.actualBoundingBoxLeft != null ? -r.actualBoundingBoxLeft : 0, r: r.actualBoundingBoxRight != null ? r.actualBoundingBoxRight : adv,
          asc: r.actualBoundingBoxAscent != null ? r.actualBoundingBoxAscent : 72, desc: r.actualBoundingBoxDescent != null ? r.actualBoundingBoxDescent : 0 };
        if (MCACHE.size > 4000) MCACHE.clear();
        MCACHE.set(key, m);
      }
      const k = px / 100;
      return { adv: m.adv * k, l: m.l * k, r: m.r * k, asc: m.asc * k, desc: m.desc * k };
    };
  }
  /** text._strip_marks: sin acentos (la pastilla mide la altura de la letra base). */
  const stripMarks = s => String(s).normalize("NFKD").replace(/[̀-ͯ]/g, "").normalize("NFC");
  const capCase = (w, cs) => (cs === "upper" ? w.toUpperCase() : cs === "lower" ? w.toLowerCase() : cs === "title" ? w.charAt(0).toUpperCase() + w.slice(1) : w);

  // ------------------------------------------------------------------ layout (cacheado)
  const LCACHE = new Map();
  function layout(gr, cfg, B, o) {
    if (!gr || !gr.words || !gr.words.length) return null;
    const D = C.captionCfg(cfg, o.titleFont);
    const words = gr.words.map(w => ({ w, txt: capCase(String(w.text || "").trim(), D.case) })).filter(x => x.txt);
    if (!words.length) return null;
    const fam = o.measure ? "medidas-externas" : o.ensureFont ? o.ensureFont(D.font) : "";
    const useRect = o.srv && !o.noRect;
    const key = [B.w.toFixed(1), B.h.toFixed(1), fam, D.font, JSON.stringify(cfg || {}), JSON.stringify(o.titleFont || null), o.srv ? 1 : 0, useRect ? 1 : 0,
      o.srv ? JSON.stringify([gr.lines, gr.rect]) : "", words.map(x => `${x.txt}@${x.w.start}`).join(" ")].join("|");
    let L = LCACHE.get(key);
    if (L) return L;
    // medidas de la fuente real (o.measure: las del motor, en la prueba de paridad en node)
    const meas = o.measure || measurer(o.fontCss, D.font);
    L = C.captionLayout(words.map(x => x.txt), cfg, B.w, B.h, meas,
      { lines: o.srv ? gr.lines : null, rect: useRect ? gr.rect : null, titleFont: o.titleFont });
    if (!L) return null;
    L.words = words; L.D = D;
    // altura de la letra base de cada palabra (para la pastilla), medida como M (al tamano del
    // preset); pillRects la escala con la linea
    const fs0 = D.size * B.w;
    L.caps = words.map(x => meas(stripMarks(x.txt), fs0).asc);
    if (LCACHE.size >= 400) LCACHE.delete(LCACHE.keys().next().value);
    LCACHE.set(key, L);
    return L;
  }

  // ------------------------------------------------------------------ dibujo
  function rrectPath(c, x, y, w, h, r, keep) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    if (!keep) c.beginPath();
    c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
  }
  function mixHex(a, b, q) {
    const p = h => { const s = C.normHex(h) || "#FFFFFF", n = parseInt(s.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
    const x = p(a), y = p(b), k = clamp(q, 0, 1);
    return `rgb(${x.map((v, i) => Math.round(v + (y[i] - v) * k)).join(",")})`;
  }
  /** Cajas de la pastilla de cada palabra (coordenadas del lienzo, sin el desplazamiento de entrada). */
  function pillRects(L, B, top) {
    return L.words.map((_, k) => {
      const r = L.rows.find(rr => k >= rr.a && k < rr.b); if (!r) return null;
      const sc = r.sc, ox = B.x + r.x0 + L.xs[k], oy = top + r.base;
      return C.pillWordRect({ l: L.M[k].l * sc, r: L.M[k].r * sc }, ox, oy, L.caps[k] * sc, L.fs);
    });
  }

  // ------------------------------------------------------------------ animacion (text._draw_group_body)
  /** Entrada de cada palabra (revelado palabra por palabra) u s despues de su inicio - 0.05:
      escala, alfa y desplazamiento vertical, con los resortes del motor. */
  function wordAnim(anim, u, fs) {
    if (anim === "pop") return { sc: 0.8 + 0.2 * C.spring(u, 3.4, 0.6), a: C.ease("out_cubic", u / 0.08), dy: (1 - C.ease("out_cubic", u / 0.22)) * 0.1 * fs };
    if (anim === "pop_strong") return { sc: 0.7 + 0.3 * C.spring(u, 3.0, 0.62), a: C.ease("out_cubic", u / 0.07), dy: 0 };
    return { sc: 1, a: C.ease("smoothstep", u / 0.2), dy: (1 - C.ease("out_cubic", u / 0.3)) * 0.12 * fs };
  }
  /** Entrada del grupo completo (Karaoke, Pastilla de color) u s despues de su inicio. */
  const groupIn = (u, fs) => ({ a: C.ease("out_cubic", u / 0.14), dy: (1 - C.ease("out_expo", u / 0.35)) * 0.22 * fs });
  /** Salida del grupo (po 0..1 en sus ultimos 0.11 s): se apaga y encoge 4 % (1 - in_quad). */
  const groupOut = po => { const q = C.ease("in_quad", po); return { a: 1 - q, sc: 1 - 0.04 * q }; };
  /** text._recenter_dx de un renglon: la parte ya dicha queda centrada y cada palabra nueva mueve el
      renglon con un resorte suave (2.8 Hz, 0.82) hasta 0 con el renglon completo. */
  function recenterDx(L, r, t) {
    const sc = r.sc, M = L.M, l = r.x0 + L.xs[r.a] + M[r.a].l * sc, rr = r.x0 + L.xs[r.b - 1] + M[r.b - 1].r * sc, cx = (l + rr) / 2;
    let dx = 0, prev = null;
    for (let i = r.a; i < r.b; i++) {
      const off = cx - (l + r.x0 + L.xs[i] + M[i].r * sc) / 2, tk = +L.words[i].w.start - 0.05;
      dx = prev == null ? off : dx + (off - prev) * C.spring(t - tk, 2.8, 0.82);
      prev = off;
    }
    return dx;
  }

  function drawGroup(c, B, gr, t, opt) {
    const o = opt || {}, cfg = o.cfg || {};
    if (cfg.enabled === false || cfg.preset === "none" || !gr || !gr.words || !gr.words.length) return null;
    const ra = o.rangeAlpha == null ? 1 : +o.rangeAlpha;
    if (ra <= 0.003) return null;
    const L = layout(gr, cfg, B, o);
    if (!L) return null;
    const D = L.D, fs = L.fs, s = B.s, M = L.M;
    const color = cfg.color || "#FFFFFF", accent = cfg.accent || o.accent || C.DEFAULT_ACCENT, hi = cfg.highlight || accent;
    // palabras clave: las del export (grupos del servidor) o las que se piden (o.keywords: el ejemplo de
    // «Resaltar una palabra clave»; solo si el ajuste esta prendido)
    const kws = o.keywords ? (D.kw_accent ? o.keywords : []) : o.srv && D.kw_accent !== false && cfg.kw_accent !== false ? gr.accent || [] : null;
    const accentSet = kws ? new Set(kws.map(C.normWord)) : null;
    // altura efectiva (y_ranges, foto con pie) respecto a la base del preset
    const dyY = ((o.yFrac == null ? D.y : +o.yFrac) - D.y) * B.h;
    const top = B.y + L.top + dyY;
    // salida del grupo: se apaga y encoge en sus ultimos 0.11 s (los del export ya traen su
    // permanencia; los aproximados, 0.12 s despues de su ultima palabra)
    const go = groupOut(o.srv ? clamp((t - (+gr.end - 0.11)) / 0.11, 0, 1) : clamp((t - +gr.end) / 0.12, 0, 1));
    let ga = go.a * ra, gdy = 0;
    if (D.reveal === "group") { const gi = groupIn(t - +gr.start, fs); ga *= gi.a; gdy = gi.dy; }
    if (ga <= 0.003) return null;
    const stroke = 2 * D.stroke * s;
    // 1) cada palabra visible: posicion, escala, alfa y color (mismo calculo que el motor)
    const units = [];
    L.rows.forEach(r => {
      const vis = i => D.reveal !== "word" || t >= +L.words[i].w.start - 0.05;
      let v1 = r.a; while (v1 < r.b && vis(v1)) v1++;
      if (v1 === r.a) return;
      const sc = r.sc;
      // recentrado con el resorte del motor (text._recenter_dx)
      const dx = D.recenter && D.reveal === "word" ? recenterDx(L, r, t) : 0;
      const base = top + r.base + gdy;
      for (let i = r.a; i < v1; i++) {
        const ww = L.words[i], w = ww.w, x = B.x + r.x0 + L.xs[i] + dx, wd = M[i].adv * sc;
        const u = t - (+w.start - 0.05);
        let a = 1, wsc = 1, wdy = 0;
        if (D.reveal === "word") { const wa = wordAnim(D.anim, u, fs); wsc = wa.sc; a = wa.a; wdy = wa.dy; }
        const kw = accentSet && accentSet.has(C.normWord(w.text));
        let fill = kw ? accent : color, wipe = null;
        const q = C.activeBump(t, w);
        if (D.active === "color" && !kw && q > 0.002) fill = mixHex(color, accent, q);
        if (D.active_scale > 0) { wsc *= 1 + D.active_scale * q; wdy -= 0.02 * fs * q; }
        if (D.active === "wipe") {
          fill = t >= +w.end ? accent : color;
          if (t >= +w.start && t < +w.end) {
            // barrido del karaoke: degradado del motor (marca hasta xe - 0.12 em, texto desde xe)
            const p = clamp((t - +w.start) / Math.max(0.08, +w.end - +w.start), 0, 1);
            const x0 = x + M[i].l * sc - 0.05 * fs, xe = x0 + ((M[i].r - M[i].l) * sc + 0.1 * fs) * p;
            wipe = [xe - 0.12 * fs, xe];
          }
        }
        units.push({ i, txt: ww.txt, x, cy: base + wdy, wd, a: ga * a, wsc, fill, wipe, sc });
      }
    });
    const drawUnits = (list, over) => list.forEach(U => {
      c.save(); c.globalAlpha = U.a; c.font = o.fontCss(D.font, fs * U.sc);
      if (U.wsc !== 1) { const px = U.x + U.wd / 2, py = U.cy - M[U.i].asc * U.sc / 2; c.translate(px, py); c.scale(U.wsc, U.wsc); c.translate(-px, -py); }
      if (!over && D.shadow > 0) { c.shadowColor = `rgba(0,0,0,${Math.min(0.85, 0.62 * D.shadow)})`; c.shadowBlur = 10 * s * D.shadow; c.shadowOffsetY = 3 * s; }
      if (!over && stroke > 0) { c.lineWidth = stroke; c.strokeStyle = cfg.stroke_color || "#000000"; c.lineJoin = "round"; c.strokeText(U.txt, U.x, U.cy); }
      let fill = over ? over : U.fill;
      if (!over && U.wipe) { const g = c.createLinearGradient(U.wipe[0], 0, U.wipe[1], 0); g.addColorStop(0, accent); g.addColorStop(1, color); fill = g; }
      c.fillStyle = fill; c.fillText(U.txt, U.x, U.cy);
      c.restore();
    });
    c.save(); c.textBaseline = "alphabetic"; c.lineJoin = "round";
    if (go.sc !== 1) {
      // el grupo encoge alrededor de su centro (como el motor: la pastilla tambien)
      const wm = Math.max(...L.rows.map(r => r.w)), xm = Math.min(...L.rows.map(r => r.x0)), cx = B.x + xm + wm / 2, cy = top + L.height / 2 + gdy;
      c.translate(cx, cy); c.scale(go.sc, go.sc); c.translate(-cx, -cy);
    }
    let pr = null;
    if (D.active === "pill") pr = C.pillAt(pillRects(L, B, top), L.words.map(x => +x.w.start), t, fs, gdy);
    if (pr) {
      // pastilla con sombra suave (text._boxed_pill)
      c.save(); c.globalAlpha = ga; c.shadowColor = "rgba(0,0,0,0.3)"; c.shadowBlur = 0.28 * fs; c.shadowOffsetY = 0.06 * fs;
      c.fillStyle = hi; rrectPath(c, pr.x, pr.y, pr.w, pr.h, pr.r); c.fill(); c.restore();
      // fuera de la pastilla: el texto base; dentro: letra en contraste sin sombra (recorte exacto)
      // (rectangulo enorme: el lienzo puede venir trasladado, p. ej. el recorte de una tarjeta)
      c.save(); c.beginPath(); c.rect(-1e5, -1e5, 2e5, 2e5); rrectPath(c, pr.x, pr.y, pr.w, pr.h, pr.r, true); c.clip("evenodd"); drawUnits(units); c.restore();
      c.save(); rrectPath(c, pr.x, pr.y, pr.w, pr.h, pr.r); c.clip(); drawUnits(units, C.contrastOn(hi)); c.restore();
    } else drawUnits(units);
    c.restore();
    const wmax = Math.max(...L.rows.map(r => r.w)), x0 = Math.min(...L.rows.map(r => r.x0));
    return { x: B.x + x0, y: top, w: wmax, h: L.height, pill: pr };
  }

  window.CapDraw = { drawGroup, layout, measurer, pillRects, stripMarks, wordAnim, groupIn, groupOut, recenterDx, _cache: LCACHE };
})();
