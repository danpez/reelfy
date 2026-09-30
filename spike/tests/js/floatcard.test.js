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
