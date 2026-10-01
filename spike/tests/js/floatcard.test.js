// Pruebas de app/static/floatcard.js (recursos flotantes del guion en la vista en vivo).
// Corre con: node --test spike/tests/js   (la paridad con el render: tests/test_media_float_preview.py)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const FC = require(path.join(__dirname, "..", "..", "app", "static", "floatcard.js"));

const near = (a, b, tol) => assert.ok(Math.abs(a - b) <= (tol == null ? 1e-9 : tol), `${a} != ${b}`);

test("la tarjeta va centrada, dentro de la banda segura y arriba de los subtitulos", () => {
  const top = FC.captionTop({ y: 0.72, preset: "clean_bold", size: 0.052 }, true, 0, 1, 9 / 16);
  near(top, 0.72 - 0.05 - 0.01, 1e-6);
  for (const [w, h] of [[1080, 1350], [1920, 1080], [2160, 3840], [null, null]]) {
    const c = FC.cardLayout(w, h, 9 / 16, top);
    assert.equal(c.cx, 0.5);
    assert.ok(c.w <= FC.SYM_W + 1e-9);
    assert.ok(c.cy + c.h / 2 <= top - 0.015 + 1e-6, JSON.stringify(c));
    assert.ok(c.cy - c.h / 2 >= FC.SAFE.top + 0.015 - 1e-6);
  }
  assert.equal(FC.cardLayout(1920, 1080, 9 / 16, top).aspect, 0.8);
  assert.ok(FC.cardLayout(2160, 3840, 9 / 16, null).aspect < 0.8);
  assert.equal(FC.captionTop({ preset: "none" }, true), null);
  assert.equal(FC.captionTop({ y: 0.7 }, false), null);
});

test("pixeles simetricos: mismo margen a la izquierda y a la derecha", () => {
  for (const bw of [216, 217, 540, 607, 608, 1080, 1081]) {
    const bh = bw * 16 / 9, c = FC.cardLayout(1080, 1350, bw / bh, 0.66);
    const [x0, , w] = FC.cardPx(c, bw, bh);
    assert.equal(x0, Math.round(bw) - (x0 + w), `bw=${bw}`);
  }
});

test("animacion: entra con resorte, flota y sale", () => {
  const a = FC.cardAnim(0, 3);
  assert.equal(a.alpha, 0); near(a.scale, 0.9); near(a.tilt, 14); assert.equal(a.bg, 0);
  const m = FC.cardAnim(1.5, 3);
  near(m.alpha, 1); near(m.bg, 1); assert.ok(Math.abs(m.scale - 1) < 0.01);
  const e = FC.cardAnim(3, 3);
  assert.equal(e.alpha, 0); assert.equal(e.bg, 0);
  let over = 0;
  for (let t = 0.1; t < 0.6; t += 0.01) over = Math.max(over, FC.cardAnim(t, 3).scale);
  assert.ok(over > 1, "el resorte rebasa");
});

test("tiempo del clip: speed acelera, trim y hold a 1x, se congela al final", () => {
  near(FC.srcTime({ fit: "speed", speed: 1.4, src_in: 0.5 }, 1), 1.9);
  near(FC.srcTime({ fit: "trim", speed: 1.4, src_in: 2 }, 1.5), 3.5);
  near(FC.srcTime({ fit: "hold" }, 9, 3, 30), 3 - 1 / 30);
  assert.equal(FC.speedOf({ fit: "speed", speed: 99 }), 4);
  assert.equal(FC.speedOf(null), 1);
  near(FC.kenburnsZoom(4, 4), 1.08);
});

test("filtro del fondo y dibujo en un canvas de prueba", () => {
  assert.equal(FC.bgFilter({}, 0, 540), "");
  const f = FC.bgFilter({}, 1, 1080);
  assert.match(f, /^blur\([\d.]+px\) brightness\(0\.720\)$/);
  assert.equal(FC.bgFilter({ blur: 0, dim: 0 }, 1, 1080), "");
  // contexto 2D minimo que registra llamadas (sin DOM)
  const calls = [];
  const g = new Proxy({ globalAlpha: 1 }, {
    get(t, k) { if (k in t) return t[k]; return (...a) => { calls.push([k, a]); }; },
    set(t, k, v) { t[k] = v; return true; },
  });
  const card = FC.cardLayout(1080, 1350, 9 / 16, 0.66);
  const B = { x: 100, y: 0, w: 540, h: 960 };
  const r = FC.drawCard(g, { src: {}, w: 1080, h: 1350 }, B, card, FC.cardAnim(1.5, 3), {}, 1.5, 3);
  const [x0, y0, w, h] = FC.cardPx(card, B.w, B.h);
  assert.deepEqual(r, { x: B.x + x0, y: B.y + y0, w, h });
  assert.ok(calls.some(c => c[0] === "drawImage"));
  assert.ok(calls.some(c => c[0] === "clip"));
  assert.equal(FC.drawCard(g, null, B, card, FC.cardAnim(0, 3), {}, 0, 3), null);   // invisible
});

test("la tarjeta tiene fondo opaco bajo la imagen (una imagen con transparencia; = media.draw_card)", () => {
  for (const shadow of [0, 0.6]) {
    const calls = [];
    const g = new Proxy({ globalAlpha: 1 }, {
      get(t, k) { if (k in t) return t[k]; return (...a) => { calls.push([k, t.fillStyle]); }; },
      set(t, k, v) { t[k] = v; return true; },
    });
    const card = FC.cardLayout(1080, 1350, 9 / 16, 0.66);
    FC.drawCard(g, { src: {}, w: 1080, h: 1350 }, { x: 0, y: 0, w: 540, h: 960 }, card, FC.cardAnim(1.5, 3), { shadow }, 1.5, 3);
    const clip = calls.findIndex(c => c[0] === "clip"), img = calls.findIndex(c => c[0] === "drawImage");
    const bg = calls.findIndex((c, i) => i > clip && c[0] === "fillRect" && c[1] === FC.CARD_BG);
    assert.ok(clip >= 0 && bg > clip && img > bg, JSON.stringify(calls.map(c => c[0])));
  }
  assert.equal(FC.CARD_BG, "#1C1C22");
});

test("subtitulos subidos: la tarjeta va debajo y nunca es una miniatura", () => {
  const ba = 9 / 16;
  for (const y of [0.2, 0.35]) {
    const t = FC.captionTop({ y }, true, 0, 1, ba), b = FC.captionBottom({ y }, true, 0, 1, ba);
    const c = FC.cardLayout(1080, 1350, ba, t, null, null, null, b);
    assert.ok(c.w >= 0.4 - 1e-9 && c.cy - c.h / 2 >= b, `${y}: ${JSON.stringify(c)}`);
  }
  // sin la franja de abajo (llamada vieja): se supone la minima
  const t = FC.captionTop({ y: 0.2 }, true, 0, 1, ba);
  assert.ok(FC.cardLayout(1080, 1350, ba, t).w >= 0.4 - 1e-9);
});

test("vida en reposo: flota, se mece con perspectiva y sigue simetrica en cada cuadro", () => {
  const card = FC.cardLayout(1080, 1350, 9 / 16, FC.captionTop({ y: 0.72 }, true, 0, 1, 9 / 16));
  const ys = [], ratios = [];
  for (let lt = 0; lt <= 12; lt += 1 / 30) {
    const st = FC.cardAnim(lt, 12);
    const q = FC.cardQuad(card, 1080, 1920, st);
    near(q[0][0] + q[1][0], 1080, 1e-6); near(q[3][0] + q[2][0], 1080, 1e-6);   // centrada (espejo)
    if (lt > 0.8 && lt < 11.7) { ys.push((q[0][1] + q[3][1]) / 2); ratios.push((q[1][0] - q[0][0]) / (q[2][0] - q[3][0])); }
  }
  assert.ok(Math.max(...ys) - Math.min(...ys) > 25, "flota > 25 px a 1920");
  assert.ok(Math.min(...ratios) < 0.98 && Math.max(...ratios) > 1.02, "perspectiva por turnos");
  const still = FC.cardAnim(2.2, 6, null, null, 0);
  near(still.sway, 0); near(still.fdy, 0); near(still.scale, 1, 1e-6);
  // mas `float` no sube la amplitud (nunca entra a los subtitulos): solo la acelera
  for (let lt = 0.8; lt < 5; lt += 0.05) assert.ok(Math.abs(FC.cardAnim(lt, 6, null, null, 2).fdy) <= FC.FLOAT_AMP + 1e-12);
});

test("franjas de la perspectiva: contiguas, exactas en las esquinas, una sola sin inclinacion", () => {
  assert.equal(FC.warpStrips(800, 1000, 0, 1).length, 1);
  for (const tilt of [-14, -4.5, 3, 14]) {
    const st = FC.warpStrips(800, 1000, tilt, 1), q = FC.tiltQuad(800, 1000, tilt, 1);
    near(st[0][0], -500); near(st[st.length - 1][1], 500);
    near(st[0][2], q[0][1], 1e-9); near(st[st.length - 1][3], q[3][1], 1e-9);
    for (let i = 1; i < st.length; i++) { near(st[i][0], st[i - 1][1]); near(st[i][2], st[i - 1][3]); }
  }
});

test("luz: franja que cruza de arriba abajo, degradado ordenado y solo en imagenes", () => {
  const a0 = FC.IN_DUR + FC.SHEEN_DELAY;
  assert.equal(FC.sheenState(a0 - 0.01, 6), null);
  const u1 = FC.sheenState(a0 + 0.2, 6)[0], u2 = FC.sheenState(a0 + 0.6, 6)[0];
  assert.ok(u2 > u1, "baja");
  assert.equal(FC.sheenState(5.9, 6), null);                     // nunca en la salida
  const ls = FC.lightStops([0.5, 1], 0, 1, 0);
  assert.equal(ls[0][0], 0); assert.equal(ls[ls.length - 1][0], 1);
  for (let i = 1; i < ls.length; i++) assert.ok(ls[i][0] > ls[i - 1][0]);
  const top = ls.reduce((m, s) => (s[4] > m[4] ? s : m));
  near(top[0], 0.5, 1e-9);
  assert.deepEqual(FC.lightStops([0.5, 1], 0, 0), []);
  assert.equal(FC.sheenAmount({ kind: "video" }), 0); assert.equal(FC.sheenAmount({ kind: "image" }), 1);
  assert.equal(FC.sheenAmount({ kind: "video", sheen: 0.4 }), 0.4); assert.equal(FC.sheenAmount({}, true), 0);
  near(FC.kenburnsZoom(0, 4), 1 + FC.KB_LEAD * FC.KENBURNS); near(FC.kenburnsZoom(1, 4, 0), 1);
});

test("luz: pasada lenta (a 30 fps no salta) y con desenfoque de movimiento", () => {
  const a0 = FC.IN_DUR + FC.SHEEN_DELAY;
  // el centro avanza poco por cuadro (nucleo ~0.086 del alto): nunca mas de 0.043 a 30 fps
  let prev = null, step = 0;
  for (let k = 0; k <= FC.SHEEN_DUR * 30; k++) {
    const sh = FC.sheenState(a0 + k / 30, 20);
    if (sh && prev) step = Math.max(step, sh[0] - prev[0]);
    prev = sh;
  }
  assert.ok(step > 0 && step <= 0.043, `paso ${step}`);
  // fuera de la pasada: la misma luz que lightStops sin franja; en la pasada: promedio del obturador
  assert.deepEqual(FC.lightAt(a0 - 0.2, 20, 1.3, 1, 0.6), FC.lightStops(null, 1.3, 1, 0.6));
  const mid = FC.lightAt(a0 + FC.SHEEN_DUR / 2, 20, 0, 1, 0);
  const peak = Math.max(...mid.map(s => s[4]));
  assert.ok(peak >= 0.85 * FC.SHEEN_A && peak <= FC.SHEEN_A + 1e-9, `pico ${peak}`);
  assert.ok(mid.length > FC.lightStops(FC.sheenState(a0 + FC.SHEEN_DUR / 2, 20), 0, 1, 0).length);
  for (let i = 1; i < mid.length; i++) assert.ok(mid[i][0] > mid[i - 1][0]);
  assert.deepEqual(FC.lightAt(a0 + 0.5, 20, 0, 0), []);
});

test("dibujo: franjas recortadas al pixel del canvas, luz en imagenes y ninguna en clips", () => {
  const run = (it) => {
    const calls = [], grads = [];
    const T = { a: 2, b: 0, c: 0, d: 2, e: 0, f: 0.3 };       // canvas con devicePixelRatio 2
    const g = new Proxy({ globalAlpha: 1 }, {
      get(t, k) {
        if (k in t) return t[k];
        if (k === "getTransform") return () => T;
        if (k === "createLinearGradient") return (...a) => { const gr = { stops: [], addColorStop(o, c) { this.stops.push([o, c]); } }; grads.push(gr); return gr; };
        return (...a) => { calls.push([k, a]); };
      },
      set(t, k, v) { t[k] = v; return true; },
    });
    const card = FC.cardLayout(1080, 1350, 9 / 16, 0.66);
    const lt = FC.IN_DUR + FC.SHEEN_DELAY + FC.SHEEN_DUR / 2, st = FC.cardAnim(lt, 6);
    const r = FC.drawCard(g, { src: {}, w: 1080, h: 1350 }, { x: 0, y: 0, w: 540, h: 960 }, card, st, it, lt, 6);
    const [, , cw, ch] = FC.cardPx(card, 540, 960);
    return { calls, grads, r, st, n: FC.warpStrips(cw, ch, st.tilt, st.scale).length };
  };
  const img = run({ kind: "image" }), n = img.n;
  assert.ok(n > 8, "con vaiven hay franjas");
  const rows = img.calls.filter(c => c[0] === "rect").map(c => c[1]);
  assert.equal(rows.length, n);
  for (const [, y, , hgt] of rows) {
    for (const v of [y, y + hgt]) if (Math.abs(v) < 1e4) { const d = 2 * v + 0.3; near(d, Math.round(d), 1e-6); }   // fila de pixel exacta
  }
  assert.equal(img.calls.filter(c => c[0] === "drawImage").length, n);
  assert.ok(img.grads.length === 1 && img.grads[0].stops.length > 4, "degradado de luz");
  assert.ok(img.calls.some(c => c[0] === "fill" && c[1][0] === "evenodd"), "anillo del borde");
  const vid = run({ kind: "video" });
  assert.equal(vid.grads.length, 0, "los clips no llevan luz");
  assert.deepEqual(vid.r, img.r);
});
