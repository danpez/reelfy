// Pruebas de la logica pura del Studio (app/static/fxcore.js). Corre con: node --test spike/tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const C = require(path.join(__dirname, "..", "..", "app", "static", "fxcore.js"));

// Esquema minimo con la misma forma que motion.schema.schema() (datos ficticios)
const SCHEMA = {
  common: {
    range: [{ k: "start", type: "num" }, { k: "end", type: "num" }, { k: "enabled", type: "bool" }],
    event: [{ k: "at", type: "num" }, { k: "enabled", type: "bool" }],
  },
  tracks: {
    text: { key: "preset", common: "range", items: {
      title_pop: { label: "Titulo pop", params: [{ k: "text", type: "text", default: "" }, { k: "y", type: "num", default: 0.2 }, { k: "size", type: "num", default: 0.09 }] },
      counter: { label: "Contador", params: [{ k: "text", type: "text", default: "" }, { k: "from", type: "num", default: 0 }, { k: "to", type: "num", default: 1000 }, { k: "prefix", type: "text", default: "$" }] },
    } },
    graphics: { key: "kind", common: "range", items: {
      doodle: { label: "Garabato", params: [{ k: "name", type: "select", default: "sparkle" }, { k: "size", type: "num", default: 0.18 }] },
      cta: { label: "Llamada a la accion", params: [{ k: "label", type: "text", default: "" }, { k: "variant", type: "select", default: "follow" }] },
    } },
    cutaways: { key: "kind", common: "range", items: {
      title_card: { label: "Tarjeta de titulo", params: [{ k: "background", type: "select", default: "grid_warp" }, { k: "text.text", type: "text", default: "" }, { k: "text.preset", type: "select", default: "title_pop" }] },
    } },
    transitions: { key: "kind", common: "event", items: { flash: { label: "Destello", params: [{ k: "dur", type: "num", default: 0 }] } } },
    sfx: { key: "name", common: "event", items: { pop: { label: "pop", params: [{ k: "gain_db", type: "num", default: -6 }] } } },
  },
};

test("keepsFromCuts: complemento de cortes activos, funde solapes y descarta tramos cortos", () => {
  const k = C.keepsFromCuts([{ start: 1, end: 2 }, { start: 1.5, end: 3 }, { start: 5, end: 5.03 }, { start: 8, end: 9, enabled: false }], 10);
  assert.deepEqual(k, [[0, 1], [3, 5], [5.03, 10]]);
  assert.deepEqual(C.keepsFromCuts([{ start: 0, end: 10 }], 10), []);
  assert.deepEqual(C.keepsFromCuts([], 4), [[0, 4]]);
  // tramo conservado <= 0.05 s se descarta
  assert.deepEqual(C.keepsFromCuts([{ start: 0, end: 2 }, { start: 2.04, end: 4 }], 6), [[4, 6]]);
});

test("Timeline: remap, remapClamped e inverse (sin fps)", () => {
  const tl = new C.Timeline([[0, 1], [3, 5]], 10, null, 0);
  assert.equal(tl.finalDuration, 3);
  assert.equal(tl.remap(0.5), 0.5);
  assert.equal(tl.remap(2), null);             // dentro del corte
  assert.equal(tl.remapClamped(2), 1);         // -> union siguiente
  assert.equal(tl.remap(4), 2);
  assert.equal(tl.inverse(2), 4);
  assert.equal(tl.inverse(99), 5);
});

test("Timeline con fps expande los keeps a la reticula y absorbe fragmentos", () => {
  const tl = new C.Timeline([[0.01, 1.01], [1.5, 1.6], [3.02, 4.0]], 5, 30, C.MIN_FRAG);
  // 1.5-1.6 dura 0.1 s < 0.15 -> absorbido
  assert.equal(tl.keeps.length, 2);
  assert.ok(Math.abs(tl.keeps[0][0] - 0) < 1e-9);                 // floor(0.3)=0
  assert.ok(Math.abs(tl.keeps[0][1] - 31 / 30) < 1e-9);          // ceil(30.3)=31
  assert.ok(Math.abs(tl.keeps[1][0] - 90 / 30) < 1e-9);          // floor(90.6)=90
});

test("Timeline pega uniones a cambios de toma cercanos", () => {
  const tl = C.timelineFromPlan({ duration: 10, cuts: [{ start: 4.05, end: 5 }], shots: [{ start: 0 }, { start: 4.0 }] }, null);
  assert.ok(Math.abs(tl.keeps[0][1] - 4.0) < 1e-9);
});

test("rutas con punto: getPath / setPath", () => {
  const o = {};
  C.setPath(o, "text.text", "Hola");
  assert.equal(C.getPath(o, "text.text"), "Hola");
  assert.equal(C.getPath(o, "no.existe"), undefined);
  C.setPath(o, "text.text", undefined);
  assert.equal(C.getPath(o, "text.text"), undefined);
});

test("newItem: id unico, tiempos dentro del video, defaults del kind y src user", () => {
  const fx = { text: [{ id: "tx_1", start: 0, end: 1 }] };
  const it = C.newItem(SCHEMA, "text", "title_pop", 9.5, 10, fx);
  assert.equal(it.preset, "title_pop");
  assert.equal(it.src, "user");
  assert.equal(it.locked, false);
  assert.ok(it.start <= 9.7 && it.end <= 10 && it.end > it.start);
  assert.equal(it.y, 0.2);
  assert.equal(it.text, "Tu texto");
  assert.notEqual(it.id, "tx_1");
  const ev = C.newItem(SCHEMA, "sfx", "pop", 3.3333, 10, fx);
  assert.equal(ev.at, 3.333);
  assert.equal(ev.name, "pop");
  assert.equal(ev.gain_db, -6);
  assert.equal(ev.start, undefined);
  const cw = C.newItem(SCHEMA, "cutaways", "title_card", 1, 10, fx);
  assert.equal(C.getPath(cw, "text.preset"), "title_pop");
  assert.equal(C.getPath(cw, "text.text"), "Titulo");
  const tr = C.newItem(SCHEMA, "transitions", "flash", 2, 10, fx);
  assert.equal(tr.dur, 0.4);
});

test("duplicateItem desplaza sin salirse y changeKind conserva lo comun", () => {
  const fx = { graphics: [] };
  const a = { id: "gr_1", kind: "doodle", name: "star4", start: 8, end: 9.5, size: 0.3, x: 0.2, src: "ai", locked: true };
  const b = C.duplicateItem("graphics", a, 1.5, fx, 10);
  assert.notEqual(b.id, a.id);
  assert.equal(b.src, "user"); assert.equal(b.locked, false);
  assert.equal(b.end, 10); assert.equal(b.start, 8.5);
  const c = C.changeKind(SCHEMA, "graphics", C.clone(a), "cta");
  assert.equal(c.kind, "cta");
  assert.equal(c.name, undefined);          // param del kind viejo
  assert.equal(c.x, 0.2);                   // posicion se conserva
  assert.equal(c.variant, "follow");        // default del nuevo kind
});

// ---- simetria (regla de la marca): todo lo que agrega el usuario nace centrado
const SCHEMA_SYM = {
  common: SCHEMA.common,
  tracks: {
    text: { key: "preset", common: "range", items: {
      cascade: { label: "Cascada", params: [{ k: "text", type: "text", default: "" }, { k: "x", type: "num", default: 0.1 }, { k: "align", type: "seg", default: "left" }] },
      counter: { label: "Contador", params: [{ k: "x", type: "num", default: 0.5 }, { k: "anchor", type: "seg", default: "right" }] },
    } },
    graphics: { key: "kind", common: "range", items: {
      // defaults ASIMETRICOS a proposito (catalogo viejo): la red de seguridad de newItem los centra
      doodle: { label: "Garabato", params: [{ k: "x", type: "num", default: 0.47 }, { k: "name", type: "select", default: "sparkle" }] },
      card: { label: "Tarjeta", params: [{ k: "x", type: "num", default: 0.47 }, { k: "size", type: "num", default: 0.74 }] },
      lower_third: { label: "Rotulo", params: [{ k: "x", type: "num", default: 0.07 }, { k: "title", type: "text", default: "" }, { k: "align", type: "seg", default: "center", nofill: true }] },
      media: { label: "Medio", params: [{ k: "x", type: "num", default: null }, { k: "y", type: "num", default: null }] },
      progress: { label: "Barra", params: [{ k: "bar_y", type: "num", default: 0 }] },
    } },
  },
};

test("simetria: constantes espejo de ctx (XC, SYM_W) y zona segura simetrica", () => {
  assert.equal(C.XC, 0.5);
  assert.ok(Math.abs(C.SYM_W - 0.76) < 1e-9);
  assert.ok(Math.abs(C.SAFE.left - (1 - C.SAFE.right)) < 1e-9);
  assert.equal(C.TEXT_PRESETS.cascade.align, "center");               // el bloque de la cascada se centra
});

test("simetria: newItem centra textos y graficos aunque el catalogo diga otra cosa", () => {
  const fx = {};
  const casc = C.newItem(SCHEMA_SYM, "text", "cascade", 1, 10, fx);
  assert.equal(casc.x, 0.5); assert.equal(casc.align, "center");
  const cnt = C.newItem(SCHEMA_SYM, "text", "counter", 1, 10, fx);
  assert.equal(cnt.anchor, "center"); assert.equal(cnt.x, 0.5);
  for (const k of ["doodle", "card", "lower_third"]) assert.equal(C.newItem(SCHEMA_SYM, "graphics", k, 1, 10, fx).x, 0.5, k);
  const lt = C.newItem(SCHEMA_SYM, "graphics", "lower_third", 1, 10, fx);
  assert.equal(lt.align, "center"); assert.ok(C.ltCentered(lt));
  // el PiP sin posicion sigue automatico y la barra no recibe x
  const pip = C.newItem(SCHEMA_SYM, "graphics", "media", 1, 10, fx);
  assert.equal(pip.x, undefined); assert.equal(pip.y, undefined);
  assert.equal(C.newItem(SCHEMA_SYM, "graphics", "progress", 1, 10, fx).x, undefined);
});

test("simetria: changeKind recentra cuando x cambia de significado (rotulo anclado a la izquierda)", () => {
  // rotulo viejo (sin align: x = borde izquierdo) -> tarjeta: x pasa a ser un centro -> 0.5
  const old = { id: "gr_1", kind: "lower_third", title: "Ana", x: 0.07, y: 0.7, start: 1, end: 2 };
  const card = C.changeKind(SCHEMA_SYM, "graphics", C.clone(old), "card");
  assert.equal(card.x, 0.5); assert.equal(card.align, undefined);
  // tarjeta en x 0.3 (elegida por el usuario) -> rotulo centrado: x sigue siendo el centro
  const lt = C.changeKind(SCHEMA_SYM, "graphics", { id: "gr_2", kind: "card", x: 0.3, start: 1, end: 2 }, "lower_third");
  assert.equal(lt.align, "center"); assert.equal(lt.x, 0.3);
  // rotulo centrado -> garabato: conserva x (ya era un centro)
  const dd = C.changeKind(SCHEMA_SYM, "graphics", { id: "gr_3", kind: "lower_third", align: "center", x: 0.42, start: 1, end: 2 }, "doodle");
  assert.equal(dd.x, 0.42); assert.equal(dd.align, undefined);
});

test("simetria: cambiar la alineacion del rotulo lo recoloca (centro <-> borde izquierdo)", () => {
  const lt = { kind: "lower_third", align: "center", x: 0.5 };
  C.setLowerThirdAlign(lt, "left");
  assert.equal(lt.align, "left"); assert.equal(lt.x, C.SAFE.left);
  C.setLowerThirdAlign(lt, "left");                                   // sin cambio: x se respeta
  assert.equal(lt.x, C.SAFE.left);
  lt.x = 0.2;
  C.setLowerThirdAlign(lt, "center");
  assert.equal(lt.align, "center"); assert.equal(lt.x, 0.5);
  const legacy = { kind: "lower_third", x: 0.07 };                   // sin align = izquierda
  assert.equal(C.ltCentered(legacy), false);
  C.setLowerThirdAlign(legacy, "center");
  assert.equal(legacy.x, 0.5);
});

test("simetria: rotulo centrado en la vista en vivo (graphics.draw_lower_third): caja y revelacion", () => {
  const W = 1080, pw = 420, barw = 10, padx = 28;
  // centrado en 0.5: mismo aire a izquierda y derecha; ancho util SYM_W*W - 2*padx
  const g = C.lowerThirdX({ kind: "lower_third", align: "center", x: 0.5 }, W, pw, barw, padx, 120);
  assert.equal(g.centered, true);
  assert.ok(Math.abs(g.left - (W - g.right)) < 1e-9);
  assert.ok(Math.abs(g.avail - (0.76 * W - 56)) < 1e-9);
  // el panel visible se despliega desde el centro: simetrico en CADA cuadro de la entrada
  for (const lt of [0.05, 0.2, 0.35, 0.5, 1.0]) {
    const rv = C.lowerThirdReveal({ kind: "lower_third", align: "center" }, lt, 3);
    const vl = g.cx - pw * rv.pan / 2, vr = g.cx + pw * rv.pan / 2, bl = g.cx - pw * rv.bar / 2, br = g.cx + pw * rv.bar / 2;
    assert.ok(Math.abs(vl - (W - vr)) < 1e-9 && Math.abs(bl - (W - br)) < 1e-9, lt);
  }
  // acotado SIMETRICO: en x = 0.3 el panel no sale de SAFE.left (0.12); si no cabe, al centro
  const g2 = C.lowerThirdX({ kind: "lower_third", align: "center", x: 0.3 }, W, pw, barw, padx);
  assert.ok(Math.abs(g2.left - 0.12 * W) < 1e-9);
  assert.equal(C.lowerThirdX({ kind: "lower_third", align: "center", x: 0.2 }, W, 0.9 * W, barw, padx).cx, 0.5 * W);
  // legado (sin align): x = borde izquierdo con la barra vertical; ancho util hasta SAFE.right
  const lg = C.lowerThirdX({ kind: "lower_third", x: 0.07 }, W, pw, barw, padx);
  assert.equal(lg.centered, false);
  assert.ok(Math.abs(lg.left - 0.07 * W) < 1e-9 && Math.abs(lg.right - (0.07 * W + barw + pw)) < 1e-9);
  assert.ok(Math.abs(lg.avail - (0.88 * W - 0.07 * W - barw - 56)) < 1e-9);
  assert.ok(Math.abs(C.lowerThirdX({ kind: "lower_third" }, W, pw, barw, padx).x0 - 0.07 * W) < 1e-9);
  // revelacion: barra -> panel -> textos; se pliega al final con undraw; sin revelacion con none
  const r0 = C.lowerThirdReveal({}, 0, 3), r1 = C.lowerThirdReveal({}, 1.5, 3), rEnd = C.lowerThirdReveal({}, 2.95, 3);
  assert.equal(r0.bar, 0); assert.ok(r1.bar > 0.99 && r1.pan > 0.99 && r1.te > 0.99 && r1.se > 0.99);
  assert.ok(rEnd.pan < 0.5 && rEnd.te < 0.5);
  const rNone = C.lowerThirdReveal({ anim_in: "none", anim_out: "none" }, 2.95, 3);
  assert.ok(rNone.pan > 0.99 && rNone.bar > 0.99);
  assert.ok(Math.abs(C.ease("out_expo", 0.5) - (1 - Math.pow(2, -5))) < 1e-12);
});

test("simetria: el iman del arrastre centra la caja VISIBLE, no el ancla", () => {
  // rotulo anclado a la izquierda: ancla en 0.25 y caja visible centrada en 0.495 -> el ancla se
  // corre 0.005 y la caja queda exactamente en el centro
  const [x, on] = C.snapCenterX(0.25, 0.495);
  assert.equal(on, true); assert.ok(Math.abs(x - 0.255) < 1e-9);
  // lejos del centro: no hay iman
  assert.deepEqual(C.snapCenterX(0.3, 0.3), [0.3, false]);
  assert.deepEqual(C.snapCenterX(0.3, 0.5 + C.SNAP_X + 0.001), [0.3, false]);
  // caja ya centrada: el ancla no se mueve
  assert.deepEqual(C.snapCenterX(0.5, 0.5), [0.5, true]);
  assert.equal(C.snapCenterX(0.1, 0.49, 0.02)[1], true);              // tolerancia propia
});

test("itemLabel y activeAt", () => {
  assert.equal(C.itemLabel(SCHEMA, "text", { preset: "title_pop", lines: [{ text: "HOLA" }, { text: "mundo" }] }), "HOLA mundo");
  assert.equal(C.itemLabel(SCHEMA, "text", { preset: "counter", prefix: "$", to: 500 }), "$500");
  assert.equal(C.itemLabel(SCHEMA, "graphics", { kind: "doodle", name: "star4" }), "Garabato: star4");
  assert.equal(C.itemLabel(SCHEMA, "cutaways", { kind: "title_card", text: { text: "Precio" } }), "Tarjeta de titulo: Precio");
  const items = [{ start: 1, end: 2 }, { start: 1.5, end: 3, enabled: false }];
  assert.equal(C.activeAt("text", items, 1.6).length, 1);
  assert.equal(C.activeAt("transitions", [{ at: 5, dur: 0.4 }], 5.15).length, 1);
  assert.equal(C.activeAt("transitions", [{ at: 5, dur: 0.4 }], 5.3).length, 0);
});

test("packRows: sin solapes dentro de una fila", () => {
  const iv = [[0, 10], [5, 15], [12, 20], [16, 18], [0, 3]];
  const { rows, n } = C.packRows(iv, 1);
  assert.equal(n, 2);
  for (let i = 0; i < iv.length; i++) for (let j = i + 1; j < iv.length; j++) {
    if (rows[i] !== rows[j]) continue;
    const [a, b] = iv[i], [c, d] = iv[j];
    assert.ok(b + 1 <= c || d + 1 <= a, `solape en fila ${rows[i]}`);
  }
});

test("snapTo usa umbral en pixeles (depende del zoom)", () => {
  assert.equal(C.snapTo(1.05, [1.0, 2.0], 100, 8), 1.0);     // 5 px
  assert.equal(C.snapTo(1.05, [1.0, 2.0], 10, 8), 1.0);      // 0.5 px
  assert.equal(C.snapTo(1.2, [1.0, 2.0], 100, 8), null);     // 20 px
});

test("zoom logaritmico ida y vuelta y paso de regla", () => {
  for (const z of [1, 2, 7.5, 40]) assert.ok(Math.abs(C.zoomFromSlider(C.sliderFromZoom(z, 40), 40) - z) / z < 0.01);
  assert.equal(C.rulerStep(30, 70), 5);
  assert.equal(C.rulerStep(800, 70), 0.1);
  assert.equal(C.fmtClock(65.4), "1:05");
  assert.equal(C.fmtClock(2.25, 0.25), "2.25s");
  assert.equal(C.fmtClock(2.5, 0.5), "2.5s");
  assert.equal(C.fmtClock(61.5, 0.5), "1:01.5");
});

test("captionGroups respeta max_words y corta en puntuacion o pausa", () => {
  const w = (t, s, e) => ({ text: t, start: s, end: e });
  const phrases = [[w("Hola", 0, .3), w("a", .35, .4), w("todos.", .45, .8)], [w("Hoy", .9, 1.1), w("vamos", 1.15, 1.4), w("a", 1.45, 1.5), w("aprender", 1.55, 2)], [w("algo", 3, 3.3)]];
  const g = C.captionGroups(phrases, 3);
  assert.ok(g.every(x => x.words.length <= 4));
  assert.equal(g[0].words.map(x => x.text).join(" "), "Hola a todos.");
  assert.equal(g[g.length - 1].words[0].text, "algo");   // pausa > 0.45 s
});

test("cameraAt combina zoom y punch con intensidad", () => {
  const eff = [{ kind: "zoom", start: 0, end: 2, from: 1, to: 1.2, easing: "linear" }, { kind: "punch", start: 1, end: 2, amount: 0.1, hold: 0.5, intensity: 0.5 }];
  const a = C.cameraAt(eff, 1.0), b = C.cameraAt(eff, 1.3);
  assert.ok(Math.abs(a.z - 1.1) < 1e-6);
  assert.ok(b.z > 1.1 * 1.049 && b.z < 1.2 * 1.06);
  assert.equal(C.cameraAt(eff, 5).z, 1);
});

// ------------------------------------------------------------------ ola 2 (arreglos del critico)
test("accentEs: etiquetas ASCII del esquema con ortografia correcta", () => {
  assert.equal(C.accentEs("Tamano"), "Tamaño");
  assert.equal(C.accentEs("Posicion Y"), "Posición Y");
  assert.equal(C.accentEs("Descripcion para la IA"), "Descripción para la IA");
  assert.equal(C.accentEs("Gigante detras"), "Gigante detrás");
  assert.equal(C.accentEs("MAYUSCULAS"), "MAYÚSCULAS");
  assert.equal(C.accentEs("Vinieta"), "Viñeta");
  assert.equal(C.accentEs("BPM (0 = automatico)"), "BPM (0 = automático)");
  assert.equal(C.accentEs("Color de acento"), "Color de acento");   // sin cambios
});

test("titulos con lines: el texto editable escribe en las lineas y conserva su estilo", () => {
  const it = { preset: "stack", text: "", lines: [{ text: "UN BUEN", scale: 1, font: "Anton", color: "#FFD23F" }, { text: "cafe", scale: 0.6, italic: true }] };
  assert.equal(C.titleText(it), "UN BUEN\ncafe");
  C.setTitleText(it, "HOLA MUNDO\ndespierta\nextra");
  assert.equal(it.lines.length, 3);
  assert.deepEqual(it.lines[0], { text: "HOLA MUNDO", scale: 1, font: "Anton", color: "#FFD23F" });
  assert.equal(it.lines[1].italic, true);
  assert.equal(it.lines[2].scale, 0.6);                  // copia el estilo de la ultima
  assert.equal(C.itemLabel(SCHEMA, "text", it), "HOLA MUNDO despierta extra");   // etiqueta del bloque
  C.setTitleText(it, "SOLO UNA");
  assert.equal(it.lines.length, 1);
  C.clearLineStyles(it);
  assert.equal(it.lines, null);
  assert.equal(it.text, "SOLO UNA");
  const plain = { preset: "title_pop", text: "Hola" };
  C.setTitleText(plain, "Hola\nmundo");
  assert.equal(plain.text, "Hola\nmundo");
  assert.equal(plain.lines, undefined);
});

test("captionsHiddenAt: misma regla que render.build_job", () => {
  const fx = { captions: {}, text: [
    { start: 1, end: 2, y: 0.1, behind: true },            // detras: tambien oculta
    { start: 3, end: 4, preset: "kicker", y: 0.2 },         // kicker: tambien
    { start: 5, end: 6, keep_captions: true },              // pide conservarlos
    { start: 7, end: 8, enabled: false },                   // desactivado: no cuenta
  ], cutaways: [{ start: 9, end: 10, text: { text: "Precio" } }, { start: 11, end: 12, text: { text: "  " } }] };
  const at = t => C.captionsHiddenAt(fx, t);
  assert.deepEqual([0.5, 1.5, 3.5, 5.5, 7.5, 9.5, 11.5].map(at), [false, true, true, false, false, true, false]);
  fx.captions.hide_during_titles = false;
  assert.equal(at(1.5), false);
  assert.equal(at(9.5), false);
});

test("iman al arrastrar un borde: con el playhead del INICIO del gesto los valores son monotonos", () => {
  // el Studio mueve el playhead al borde mientras se arrastra (para ver el cuadro): si el iman
  // usara el playhead vivo, el borde se pegaria a si mismo
  const plan = { duration: 40, cuts: [], highlights: [], shots: [{ start: 0 }], version: 2,
    fx: { text: [{ id: "a", start: 18, end: 21.2 }], graphics: [], cutaways: [], effects: [], transitions: [], sfx: [] } };
  const it = plan.fx.text[0], px = 40, v0 = it.end, ex = new Set([it]);
  const run = live => {
    let ph = 10; const ph0 = ph, got = [];
    for (let step = 1; step <= 60; step++) {
      const v = v0 + (step * 2) / px;
      const tg = C.snapTargets(plan, { playhead: live ? ph : ph0, exclude: ex });
      const s = C.snapTo(v, tg, px, 8);
      it.end = s != null ? s : v; got.push(it.end);
      ph = Math.max(0, it.end - 0.04);                 // lo que hace la UI
    }
    it.end = v0; return got;
  };
  const good = run(false);
  for (let i = 1; i < good.length; i++) assert.ok(good[i] >= good[i - 1] - 1e-9, `no monotono en ${i}: ${good[i - 1]} -> ${good[i]}`);
  assert.ok(good[good.length - 1] > v0 + 2.5);
  const bad = run(true);
  assert.ok(bad.some((x, i) => i && x < bad[i - 1] - 1e-6) || bad[bad.length - 1] < v0 + 1, "el caso con playhead vivo deberia fallar (control)");
});

test("snapTargets: cortes, shorts, frases/palabras, tomas y bordes fx con exclusion", () => {
  const cut = { start: 2, end: 3 };
  const plan = { duration: 20, cuts: [cut], highlights: [{ start: 5, end: 15 }], shots: [{ start: 0 }, { start: 7.5 }], version: 2,
    fx: { text: [{ start: 9, end: 10 }], sfx: [{ at: 11 }], music: { drop_at: 12, stops: [{ at: 13 }] } } };
  const ph = [[{ start: 4, end: 4.2 }, { start: 4.3, end: 4.6 }]];
  const t = C.snapTargets(plan, { phrases: ph, playhead: 6, exclude: new Set([cut]) });
  for (const v of [0, 20, 6, 5, 15, 4, 4.6, 7.5, 9, 10, 11, 12, 13]) assert.ok(t.includes(v), `falta ${v}`);
  assert.ok(!t.includes(2) && !t.includes(4.2));
  assert.ok(C.snapTargets(plan, { phrases: ph, words: true }).includes(4.2));
  assert.ok(!C.snapTargets(plan, { playhead: null }).includes(6));
});

test("mergeRegenerate: solo cambia el item pedido; ignora lo que el director agrega", () => {
  const local = { text: [{ id: "tx_hook_1", preset: "stack", start: 1, end: 3, lines: [{ text: "HOLA" }], text: "vieja", locked: true, src: "ai" }],
    effects: [{ id: "fx_p1", kind: "punch", start: 1.08, end: 2.08, src: "ai" }], graphics: [{ id: "gr_icon_1", kind: "icon", name: "tooth", start: 2, end: 3, src: "ai" }],
    sfx: [{ id: "sf_pop_1", name: "pop", at: 1 }] };
  // mismo titulo (solo cambia el campo muerto `text`) + extras en otras pistas
  const srv1 = C.clone(local);
  srv1.text[0].text = null; srv1.text[0].locked = false;
  srv1.effects.push({ id: "fx_p2", kind: "punch", start: 1.12, end: 2.12, src: "ai" });
  srv1.text.push({ id: "tx_hook_2", preset: "stack", start: 1.05, end: 2.46, text: "OTRO" });
  let r = C.mergeRegenerate(local, srv1, ["tx_hook_1"]);
  assert.equal(r.results[0].status, "same");
  assert.equal(r.fx.effects.length, 1); assert.equal(r.fx.text.length, 1);
  assert.equal(r.fx.text[0].locked, true);                       // conserva el bloqueo del usuario
  // cambio real del item con el mismo id
  const srv2 = C.clone(local); srv2.effects[0].start = 1.2; srv2.effects[0].end = 2.3; srv2.graphics.push({ id: "gr_x", kind: "doodle", start: 1, end: 2 });
  r = C.mergeRegenerate(local, srv2, ["fx_p1"]);
  assert.equal(r.results[0].status, "changed");
  assert.equal(r.fx.effects[0].start, 1.2); assert.equal(r.fx.graphics.length, 1);
  // el director borra el item y propone otro (id nuevo) en la misma pista
  const srv3 = C.clone(local); srv3.graphics = [{ id: "gr_doodle_9", kind: "doodle", name: "sparkle", start: 2.1, end: 3.2, src: "ai" }];
  srv3.effects.push({ id: "fx_flash_3", kind: "flash_frame", start: 30, end: 30.2 });
  r = C.mergeRegenerate(local, srv3, ["gr_icon_1"]);
  assert.deepEqual([r.results[0].status, r.results[0].newId], ["replaced", "gr_doodle_9"]);
  assert.equal(r.fx.graphics.length, 1); assert.equal(r.fx.graphics[0].kind, "doodle"); assert.equal(r.fx.effects.length, 1);
  // borrado sin alternativa: se conserva el original
  const srv4 = C.clone(local); srv4.sfx = [];
  r = C.mergeRegenerate(local, srv4, ["sf_pop_1"]);
  assert.equal(r.results[0].status, "none"); assert.equal(r.fx.sfx[0].id, "sf_pop_1");
  // un candidato identico a algo que ya existe no cuenta como alternativa
  const local2 = { graphics: [{ id: "g1", kind: "doodle", name: "star4", start: 1, end: 2 }, { id: "g2", kind: "cta", start: 1, end: 2 }] };
  const srv5 = { graphics: [{ id: "g2", kind: "cta", start: 1, end: 2 }, { id: "g9", kind: "cta", start: 1, end: 2 }] };
  r = C.mergeRegenerate(local2, srv5, ["g1"]);
  assert.equal(r.results[0].status, "none");
});

test("dedupeFx: quita la copia de la IA de un titulo bloqueado y duplicados exactos", () => {
  const fx = { text: [
    { id: "tx_title_1", preset: "kicker", start: 8.2, end: 10.4, lines: [{ text: "UN BUEN CAFE CALIENTE" }, { text: "despierta" }], locked: true, src: "ai" },
    { id: "tx_title_2", preset: "stack", start: 8.263, end: 10.424, text: "Un buen cafe caliente\ndespierta", src: "ai" },
    { id: "tx_otro", preset: "stack", start: 8.3, end: 10, text: "Otra cosa", src: "ai" },
  ], effects: [
    { id: "fx_punch_3", kind: "punch", start: 22.91, end: 23.861, src: "ai" },
    { id: "fx_punch_4", kind: "punch", start: 22.91, end: 23.861, src: "ai" },
    { id: "fx_punch_5", kind: "punch", start: 23.5, end: 24.5, src: "ai" },
  ], sfx: [{ id: "a", name: "pop", at: 1 }, { id: "b", name: "pop", at: 1.05 }, { id: "c", name: "pop", at: 2 }] };
  const rm = C.dedupeFx(fx);
  // solo la copia de lo bloqueado: lo que la IA propone entre si no se toca (su plan queda igual al
  // del servidor y la sincronizacion con la musica IA sigue funcionando)
  assert.deepEqual(rm.map(r => r.id).sort(), ["tx_title_2"]);
  assert.deepEqual(fx.text.map(x => x.id), ["tx_title_1", "tx_otro"]);
  assert.equal(fx.effects.length, 3);
  assert.equal(fx.sfx.length, 3);
  // dos destellos iguales en lugares distintos son decoracion: se quedan
  const fx3 = { graphics: [{ id: "s1", kind: "doodle", name: "sparkle", start: 15.5, end: 16.3, x: 0.3, y: 0.4, src: "ai" },
    { id: "s2", kind: "doodle", name: "sparkle", start: 15.6, end: 16.3, x: 0.7, y: 0.45, src: "ai" }] };
  assert.deepEqual(C.dedupeFx(fx3), []);
  // la copia ANTES del bloqueado tambien se quita
  const fx2 = { text: [{ id: "x", start: 1, end: 2, text: "Hola", src: "ai" }, { id: "y", start: 1.1, end: 2, text: "HOLA", locked: true, src: "ai" }] };
  assert.deepEqual(C.dedupeFx(fx2).map(r => r.id), ["x"]);
});

test("geometria viva como el render: icono con badge y kicker", () => {
  { const g = C.iconGeom(100, "circle"); assert.equal(g.badge, 100); assert.ok(Math.abs(g.icon - 56) < 1e-9); }
  assert.deepEqual(C.iconGeom(100, "none"), { badge: 0, icon: 100, box: 100 });
  const L = s => s.map(t => ({ text: t }));
  assert.deepEqual(C.kickerSplit({}, L(["UN BUEN CAFE CALIENTE", "despierta"])).label, "UN BUEN CAFE CALIENTE");
  assert.equal(C.kickerSplit({}, L(["corto"])).only, true);
  assert.equal(C.kickerSplit({}, L(["un texto largo de mas de cuatro palabras"])).label, null);
  assert.equal(C.kickerSplit({ kicker: "NUEVO" }, L(["a", "b"])).rest.length, 2);
  const xy = C.anchorXY({ anchor: "face_left" }, [0.5, 0.4, 0.2, 0.1]);
  assert.ok(Math.abs(xy[0] - 0.31) < 1e-9 && Math.abs(xy[1] - 0.375) < 1e-9);
  assert.equal(C.anchorXY({ anchor: "face" }, null), null);
  assert.deepEqual(C.anchorXY({ x: 0.2, y: 0.7 }, null), [0.2, 0.7]);
});

test("transitionOffJoin: avisa si la transicion no cae en una union o cambio de toma", () => {
  const plan = { duration: 20, cuts: [{ start: 5, end: 6 }], shots: [{ start: 0 }, { start: 12 }] };
  const tl = C.timelineFromPlan(plan, 30);
  assert.equal(C.transitionOffJoin(tl, plan, 6.0), false);     // union del corte
  assert.equal(C.transitionOffJoin(tl, plan, 12.1), false);    // cambio de toma
  assert.equal(C.transitionOffJoin(tl, plan, 9), true);
  assert.equal(C.transitionOffJoin(tl, plan, 9, "slide"), true);      // mezcla: avisa
  assert.equal(C.transitionOffJoin(tl, plan, 9, "flash"), false);     // destello: vale en cualquier punto
});

test("planForLang: el plan que viaja lleva el idioma elegido", () => {
  const plan = { phrases: [[{ text: "hola" }]], phrases_en: [[{ text: "hello" }]] };
  assert.equal(C.planForLang(plan, "en").phrases[0][0].text, "hello");
  assert.equal(C.planForLang(plan, "es").phrases[0][0].text, "hola");
  assert.equal(C.planForLang({ phrases: [[{ text: "hola" }]] }, "en").phrases[0][0].text, "hola");
  // v2 traduce a pt/fr/de/it: phrases_<lang>
  const p2 = Object.assign({}, plan, { phrases_pt: [[{ text: "ola" }]] });
  assert.equal(C.planForLang(p2, "pt").phrases[0][0].text, "ola");
  assert.equal(C.planForLang(p2, "de").phrases[0][0].text, "hola");     // sin traducir aun: el original
  assert.deepEqual(C.PRO_LANGS, ["es", "en", "pt", "fr", "de", "it"]);
  // /settings.pro_langs manda (en sitio: quien guardo la referencia la ve cambiar)
  const ref = C.PRO_LANGS;
  C.setProLangs(["en", "pt"]);
  assert.deepEqual(ref, ["es", "en", "pt"]);
  C.setProLangs([]);                                                    // vacio no borra
  assert.deepEqual(ref, ["es", "en", "pt"]);
  C.setProLangs(["es", "en", "pt", "fr", "de", "it"]);
});

test("fxTexts / applyFxTexts: traducir titulos, graficos y escenas en sitio", () => {
  const fx = { text: [{ id: "t1", lines: [{ text: "UN BUEN" }, { text: "cafe" }], text: "UN BUEN\ncafe" }, { id: "t2", text: "Hola" }, { id: "t3", preset: "counter", text: "litros" }],
    graphics: [{ id: "g1", kind: "cta", label: "Sigueme" }, { id: "g2", kind: "doodle", name: "star4" }], cutaways: [{ id: "c1", text: { text: "Precio" } }] };
  const L = C.fxTexts(fx);
  assert.deepEqual(L.map(x => x.text), ["UN BUEN", "cafe", "Hola", "Sigueme", "Precio"]);
  C.applyFxTexts(fx, L.map(x => Object.assign({}, x, { to: x.text.toUpperCase() + "!" })));
  assert.equal(fx.text[0].lines[1].text, "CAFE!");
  assert.equal(fx.text[0].text, "UN BUEN!\nCAFE!");
  assert.equal(fx.graphics[0].label, "SIGUEME!");
  assert.equal(fx.cutaways[0].text.text, "PRECIO!");
});

test("musicSig: el volumen de mezcla no desactualiza la musica; los cortes y el animo si", () => {
  const m = { source: "ai", mood: "upbeat", gain_db: -17, seed: 3 };
  const a = C.musicSig(m, [[0, 10]]);
  assert.equal(C.musicSig(Object.assign({}, m, { gain_db: -10, duck_db: -4 }), [[0, 10]]), a);
  assert.notEqual(C.musicSig(Object.assign({}, m, { mood: "chill" }), [[0, 10]]), a);
  assert.notEqual(C.musicSig(m, [[0, 9]]), a);
});

test("mediaParam: medios propios solo si el esquema los declara", () => {
  const s = C.clone(SCHEMA);
  assert.equal(C.mediaParam(s, "cutaways", "title_card"), null);
  s.tracks.cutaways.items.photo = { label: "Foto", params: [{ k: "media", type: "media" }] };
  assert.equal(C.mediaParam(s, "cutaways", "photo").k, "media");
  assert.deepEqual(C.mediaKinds(s, "cutaways"), ["photo"]);
});

test("subtitulos: un grupo tapado al empezar no reaparece; y_ranges mueve la altura", () => {
  const fx = { captions: {}, text: [{ start: 1, end: 3 }] };
  const g = { start: 1.2, end: 3.6, words: [{ start: 1.2 }, { start: 2.5 }] };
  assert.equal(C.captionGroupSuppressed(fx, g, 3.2), true);          // termino el titulo: sigue oculto
  assert.equal(C.captionGroupSuppressed(fx, { start: 2.8, end: 4, words: [{ start: 2.8 }, { start: 3.4 }] }, 3.5), false);
  assert.equal(C.captionY({ y: 0.7, y_ranges: [{ start: 5, end: 8, y: 0.6 }] }, 6), 0.6);
  assert.equal(C.captionY({ y: 0.7, y_ranges: [{ start: 5, end: 8, y: 0.6 }] }, 9), 0.7);
  assert.equal(C.captionY({}, 1), 0.72);
});

test("subtitulos del render (/captions): grupo activo como text.draw_captions", () => {
  const gs = [{ start: 1, end: 2.2, words: [] }, { start: 2.2, end: 3.0, words: [] }, { start: 5, end: 6, words: [] }];
  assert.equal(C.capGroupAt(gs, 0.5), null);
  assert.equal(C.capGroupAt(gs, 1.0), gs[0]);
  assert.equal(C.capGroupAt(gs, 2.2), gs[1]);           // relevo exacto: el siguiente
  assert.equal(C.capGroupAt(gs, 3.5), null);            // hueco: nada (el anterior ya termino)
  assert.equal(C.capGroupAt(gs, 5.5), gs[2]);
  assert.equal(C.capGroupAt([], 1), null);
});

test("y_ranges con hide: sin subtitulos en el tramo (fundido 0.12 s) y sin mover la altura", () => {
  const cfg = { y: 0.72, y_ranges: [{ start: 10, end: 12, hide: true }, { start: 20, end: 22, y: 0.5 }] };
  assert.equal(C.captionRangeAlpha(cfg, 11), 0);
  assert.equal(C.captionRangeAlpha(cfg, 5), 1);
  assert.ok(Math.abs(C.captionRangeAlpha(cfg, 12.06) - 0.5) < 1e-9);
  assert.equal(C.captionY(cfg, 11), 0.72);              // la fila hide no cambia la altura
  assert.equal(C.captionY(cfg, 21), 0.5);
});

test("foto con pie: los subtitulos se MUEVEN (render.photo_caption_y) y respetan keep_captions", () => {
  const photo = { id: "c1", kind: "photo", start: 4, end: 7, caption: "Mi primera consulta", size: 0.72 };
  const fx = { captions: { y: 0.72 }, cutaways: [photo], text: [] };
  const y = C.photoCaptionY(photo, 0.72);
  assert.equal(typeof y, "number");
  assert.equal(C.captionYAt(fx, 5), y);                 // durante la foto
  assert.equal(C.captionYAt(fx, 8), 0.72);              // despues vuelve
  assert.equal(C.captionsHiddenAt(fx, 5), false);       // se mueven, no se ocultan
  // sin pie o con keep_captions: no se mueven
  assert.equal(C.captionYAt({ captions: { y: 0.72 }, cutaways: [Object.assign({}, photo, { caption: "" })] }, 5), 0.72);
  assert.equal(C.captionYAt({ captions: { y: 0.72 }, cutaways: [Object.assign({}, photo, { keep_captions: true })] }, 5), 0.72);
  // subtitulos lejos del pie (arriba): no se mueven
  assert.equal(C.photoCaptionY(photo, 0.2), null);
});

test("PiP automatico (render.media_auto_place): CENTRADO en x, lejos de la cara, dentro de SAFE y sobre los subtitulos", () => {
  const S = C.SAFE;
  const face = [0.62, 0.3, 0.2, 0.12];
  const [x, y, s] = C.mediaAutoPlace(face, 0.3, 0.3, 0.66, []);
  const r = [x - 0.15 * s, y - 0.15 * s, x + 0.15 * s, y + 0.15 * s];
  const zone = [face[0] - face[2] / 2 - 0.03, face[1] - face[3] / 2 - 0.05, face[0] + face[2] / 2 + 0.03, face[1] + face[3] / 2 + 0.03];
  const inter = Math.max(0, Math.min(r[2], zone[2]) - Math.max(r[0], zone[0])) * Math.max(0, Math.min(r[3], zone[3]) - Math.max(r[1], zone[1]));
  assert.ok(inter < 1e-9);                              // (redondeo a 4 decimales: 0 +- 1e-17)
  assert.equal(x, 0.5);                                 // simetria: nunca de lado (aunque la cara este a la derecha)
  assert.ok(y > zone[3]);                               // al pecho, bajo la barbilla
  assert.ok(r[0] >= S.left - 1e-9 && r[2] <= S.right + 1e-9 && r[1] >= S.top - 1e-9 && r[3] <= 0.66 + 1e-9);
  // cara baja (sin lugar en el pecho): arriba de la cabeza, centrado
  const low = [0.45, 0.55, 0.2, 0.12];
  const [x3, y3] = C.mediaAutoPlace(low, 0.3, 0.2, 0.7, []);
  assert.equal(x3, 0.5); assert.ok(y3 < low[1] - low[3] / 2);
  // sin cara: arriba, centrado
  const [x2, y2] = C.mediaAutoPlace(null, 0.3, 0.3, 0.66, []);
  assert.equal(x2, 0.5); assert.ok(y2 < 0.4);
  // tarjeta enorme: centrada igual (nunca empujada a un lado)
  assert.equal(C.mediaAutoPlace(face, 0.95, 0.3, 0.66, [])[0], 0.5);
  // pipPlace: x/y fijos se respetan; vacios -> automatico con la tarjeta de pipCard
  const fx = { captions: { y: 0.72 }, text: [], graphics: [] };
  assert.deepEqual(C.pipPlace(fx, { x: 0.3, y: 0.4, size: 0.36, start: 0, end: 2 }, face, 1080, 1920, 1080, 1920), { x: 0.3, y: 0.4, size: 0.36, auto: false });
  const a = C.pipPlace(fx, { x: null, y: null, size: 0.36, start: 0, end: 2 }, face, 1080, 1920, 1080, 1920);
  assert.equal(a.auto, true);
  assert.equal(a.x, 0.5);
  const [pw, ph] = C.pipCard(0.36, null, 1080, 1920, 1080);
  assert.ok(Math.abs(pw - 0.36 * 1080) < 1e-6 && Math.abs(ph - pw * 1920 / 1080) < 1e-6);
  assert.deepEqual(C.pipCard(0.4, 1, 10, 20, 1000).map(Math.round), [400, 400]);
});

test("Regenerar: se adoptan las piezas LIGADAS (SFX de la transicion, acentos del titulo, composicion)", () => {
  const local = {
    transitions: [{ id: "tr_1", kind: "whip", at: 5 }],
    sfx: [{ id: "sf_1", name: "whoosh_fast", at: 5.05 }, { id: "sf_2", name: "pop", at: 9 }, { id: "sf_3", name: "ding", at: 5.1, locked: true }],
    text: [{ id: "tx_1", preset: "title_pop", text: "Hola", start: 1, end: 3 }, { id: "tx_bghero_s2", preset: "behind_giant", text: "DIENTES", start: 10, end: 12 }, { id: "tx_bghero_s2b", preset: "stack", text: "sanos", start: 10, end: 12 }],
    graphics: [{ id: "gr_1", kind: "doodle", name: "underline", start: 1.2, end: 2.8 }, { id: "gr_2", kind: "doodle", name: "sparkle", start: 6, end: 7 }],
    effects: [{ id: "fx_bgswap_s2", kind: "bg_replace", start: 10, end: 12, background: "paper" }],
  };
  const server = C.clone(local);
  server.transitions[0].kind = "zoom_blur";
  server.sfx[0].name = "swoosh_up";
  server.sfx[1].name = "bubble";                                        // ajeno: NO se adopta
  server.sfx[2].name = "bell";                                          // bloqueado: NO
  server.graphics = server.graphics.filter(g => g.id !== "gr_1");        // el acento se apago
  server.graphics[0].name = "star4";                                     // ajeno: NO
  let r = C.mergeRegenerate(local, server, ["tr_1"]);
  assert.equal(r.fx.transitions[0].kind, "zoom_blur");
  assert.equal(r.fx.sfx[0].name, "swoosh_up");
  assert.equal(r.fx.sfx[1].name, "pop");
  assert.equal(r.fx.sfx[2].name, "ding");
  assert.deepEqual(r.linked, [{ id: "sf_1", track: "sfx", status: "changed" }]);
  r = C.mergeRegenerate(local, server, ["tx_1"]);
  assert.ok(!r.fx.graphics.some(g => g.id === "gr_1"));                  // acento ligado quitado
  assert.equal(r.fx.graphics.find(g => g.id === "gr_2").name, "sparkle");
  // composicion: regenerar la frase trae el fondo y la palabra gigante
  const s2 = C.clone(local);
  s2.text[2].text = "fuertes"; s2.text[1].y = 0.4; s2.effects[0].background = "grid_warp";
  r = C.mergeRegenerate(local, s2, ["tx_bghero_s2b"]);
  assert.equal(r.fx.text[2].text, "fuertes");
  assert.equal(r.fx.text[1].y, 0.4);
  assert.equal(r.fx.effects[0].background, "grid_warp");
  assert.equal(r.results[0].status, "changed");
  assert.deepEqual([...C.regenCompanions(local, ["tx_bghero_s2b"]).keys()].sort(), ["fx_bgswap_s2", "tx_bghero_s2"]);
});

test("avisos del render en lenguaje humano (skipped / warnings)", () => {
  const plan = { fx: { sfx: [{ id: "sf_9", name: "boom" }], graphics: [{ id: "gr_m", kind: "media" }], transitions: [{ id: "tr_4", kind: "slide", at: 3 }] } };
  let h = C.humanSkip("omitido: sfx:sf_9:nombre_desconocido", plan);
  assert.equal(h.id, "sf_9"); assert.equal(h.level, "skip"); assert.match(h.msg, /no existe en el catálogo/);
  h = C.humanSkip("media:gr_m:ilegible", plan);
  assert.equal(h.track, "graphics"); assert.match(h.msg, /no se pudo leer/);
  h = C.humanSkip("transitions:tr_4:sin_union", plan);
  assert.equal(h.id, "tr_4"); assert.match(h.msg, /lejos de un corte/);
  h = C.humanSkip("matte:12.1-14.5:calidad_baja", plan);
  assert.equal(h.id, null); assert.match(h.msg, /12.1 a 14.5/);
  assert.match(C.humanSkip("music:error", plan).msg, /Música/);
  assert.match(C.humanSkip("effects:rgb_split", plan).msg, /no disponible/);
});

test("medios propios: graphics -> PiP, escenas -> foto (nunca la foto de perfil del CTA)", () => {
  const s = C.clone(SCHEMA);
  s.tracks.graphics.items.cta.params.push({ k: "media", type: "media", accept: ["image"] });
  s.tracks.graphics.items.media = { label: "PiP", params: [{ k: "media", type: "media", accept: ["image", "video"] }] };
  s.tracks.cutaways.items.photo = { label: "Foto", params: [{ k: "media", type: "media", accept: ["image", "video"] }] };
  assert.equal(C.mediaKindFor(s, "graphics", "video"), "media");
  assert.equal(C.mediaKindFor(s, "graphics"), "media");
  assert.equal(C.mediaKindFor(s, "cutaways", "image"), "photo");
  delete s.tracks.graphics.items.media;
  assert.equal(C.mediaKindFor(s, "graphics", "image"), null);
});

test("dedupeFx: un efecto de la IA solo se quita si copia uno BLOQUEADO (mismo tipo y parametros)", () => {
  const fx = { effects: [
    { id: "fx_a", kind: "zoom", start: 1, end: 3, from: 1.0, to: 1.2, src: "ai", locked: true },
    { id: "fx_b", kind: "zoom", start: 1.02, end: 3, from: 1.7, to: 1.8, src: "ai" },      // otra decision: se queda
    { id: "fx_c", kind: "zoom", start: 1.01, end: 3, from: 1.0, to: 1.2, src: "ai" },      // copia de lo bloqueado: se quita
  ], sfx: [{ id: "s1", name: "pop", at: 2, gain_db: -6, src: "user" }, { id: "s2", name: "pop", at: 2.05, gain_db: -12, src: "ai" }] };
  const rm = C.dedupeFx(fx);
  assert.deepEqual(fx.effects.map(e => e.id), ["fx_a", "fx_b"]);
  assert.deepEqual(rm.map(r => r.id), ["fx_c"]);
  assert.equal(fx.sfx.length, 2);                        // mismo sonido con otro volumen: no es copia
});

test("dedupeFx: la propuesta de la IA no se toca (re-emisiones con sufijo incluidas): solo copias de lo bloqueado", () => {
  // quitar algo de la propuesta haria que el plan local difiera del guardado y rompe la sincronia
  // automatica con la musica IA; esas copias son un defecto del director (se reporta alla)
  const bg = (id, b) => ({ id, kind: "bg_replace", start: 1.025, end: 3.28, background: b, src: "ai" });
  const fx = { effects: [bg("fx_hookbg_s0", "grid_warp"), bg("fx_hookbg_s0b", "grid_warp"),
    { id: "fx_cam_s0", kind: "zoom", start: 1, end: 3, src: "ai" }],
    text: [{ id: "tx_h", preset: "title_pop", text: "Tus dientes", start: 1, end: 3, locked: true, src: "ai" }] };
  assert.deepEqual(C.dedupeFx(fx), []);
  assert.deepEqual(fx.effects.map(e => e.id), ["fx_hookbg_s0", "fx_hookbg_s0b", "fx_cam_s0"]);
});

test("subtitulos: balancedSplit = text._balanced_split (min del maximo, sin cerrar linea en palabra funcional)", () => {
  assert.deepEqual(C.balancedSplit([100, 100, 100, 100], 10, 2, ["uno", "dos", "tres", "cuatro"]), [0, 2]);
  // 'de' al final de la primera linea penaliza: se prefiere otro corte
  assert.deepEqual(C.balancedSplit([120, 40, 120, 60], 10, 2, ["precio", "de", "lista", "hoy"]), [0, 1]);
  assert.deepEqual(C.balancedSplit([50, 50, 50], 10, 1, null), [0]);
  assert.deepEqual(C.balancedSplit([80, 80, 80, 80, 80, 80], 10, 3, null), [0, 2, 4]);
});

// medida de prueba: monoespaciada (0.6 em por letra), tinta 0.02 em adentro de cada lado
const mono = (txt, px) => { const adv = 0.6 * px * [...txt].length; return { adv, l: txt === " " ? 0 : 0.02 * px, r: txt === " " ? 0 : adv - 0.02 * px, asc: 0.72 * px, desc: 0.2 * px }; };

test("subtitulos en vivo con la geometria del render: escala y espacio del preset, ancho util y envoltura", () => {
  const W = 1080, H = 1920;
  // bold_caps: tamano 0.052 * 1.28, espacio x1.3, contorno y mayusculas del preset
  const D = C.captionCfg({ preset: "bold_caps" });
  assert.equal(D.case, "upper"); assert.equal(D.stroke, 5); assert.equal(D.font, "Poppins-Black");
  const L1 = C.captionLayout(["TUS", "DIENTES."], { preset: "bold_caps" }, W, H, mono);
  const fs = 0.052 * 1.28 * W;
  assert.ok(Math.abs(L1.fs - fs) < 1e-9);
  const sp = 0.6 * fs * 1.3;
  const want = 0.6 * fs * 3 + sp + 0.6 * fs * 8 - 0.04 * fs;       // tinta de TUS..DIENTES.
  assert.equal(L1.rows.length, 1);
  assert.ok(Math.abs(L1.rows[0].w - want) < 1e-6, `${L1.rows[0].w} vs ${want}`);
  // la palabra de la derecha empieza tras el espacio del preset (no pegada: 'TUSDIENTES')
  assert.ok(Math.abs(L1.xs[1] - (0.6 * fs * 3 + sp)) < 1e-6);
  // minimal_small: escala 0.74 (no 0.82)
  assert.ok(Math.abs(C.captionLayout(["hola"], { preset: "minimal_small" }, W, H, mono).fs - 0.052 * 0.74 * W) < 1e-9);
  // grupo que no cabe en el ancho util (0.7296 W): bold_caps lo parte en 2 lineas; clean_bold (1 linea) lo reduce
  const long = ["CONSULTORIO", "ESPECIALIZADO", "HOY"];
  const L2 = C.captionLayout(long, { preset: "bold_caps" }, W, H, mono);
  assert.equal(L2.rows.length, 2);
  const maxw = C.captionUsable(0.5) * W;
  assert.ok(Math.abs(maxw - 0.7296 * W) < 1e-6);
  const L3 = C.captionLayout(long.map(x => x.toLowerCase()), { preset: "clean_bold" }, W, H, mono);
  assert.equal(L3.rows.length, 1);
  assert.ok(L3.rows[0].w <= maxw + 1e-6 && L3.rows[0].sc < 1);
  // las lineas que dio /captions mandan
  assert.equal(C.captionLayout(long, { preset: "bold_caps" }, W, H, mono, { lines: 1 }).rows.length, 1);
});

test("subtitulos en vivo: con el rect del export, mismo ancho, centro y alto que el render", () => {
  const W = 540, H = 960, rect = [0.3, 0.68, 0.78, 0.76];
  const L = C.captionLayout(["TUS", "DIENTES."], { preset: "bold_caps" }, W, H, mono, { rect, lines: 1 });
  assert.ok(Math.abs(L.rows[0].w - (rect[2] - rect[0]) * W) < 1e-6);
  const c0 = L.rows[0].x0 + L.M[0].l * L.rows[0].sc, c1 = L.rows[0].x0 + L.xs[1] + L.M[1].r * L.rows[0].sc;
  assert.ok(Math.abs((c0 + c1) / 2 - (rect[0] + rect[2]) / 2 * W) < 1e-6);
  assert.ok(Math.abs(L.top - rect[1] * H) < 1e-9 && Math.abs(L.height - (rect[3] - rect[1]) * H) < 1e-9);
  // dos lineas: las dos quedan dentro del rect y la de abajo empieza donde termina la de arriba + gap
  const L2 = C.captionLayout(["CONSULTORIO", "ESPECIALIZADO", "HOY"], { preset: "bold_caps" }, W, H, mono, { rect: [0.12, 0.64, 0.86, 0.8], lines: 2 });
  assert.equal(L2.rows.length, 2);
  const r0 = L2.rows[0], r1 = L2.rows[1];
  assert.ok(r1.base - r1.asc >= r0.base + r0.desc - 1e-6);
  assert.ok(Math.abs(r1.base + r1.desc - (0.8 - 0.64) * H) < 1e-6);
  // palabra activa: text._active_bump
  assert.equal(C.activeBump(0.5, { start: 1, end: 1.4 }), 0);
  assert.ok(C.activeBump(1.1, { start: 1, end: 1.4 }) > 0.99);
  assert.ok(C.activeBump(1.8, { start: 1, end: 1.4 }) < 1e-9);
});

test("titulos: presets y fuentes como el render (espacio, separacion, ancho util, italica real)", () => {
  assert.equal(C.TEXT_PRESET_Y.serif_elegant, 0.15);
  assert.equal(C.TEXT_PRESETS.title_pop.space, 1.12);
  assert.deepEqual(C.italicOf("DMSerifDisplay"), { font: "DMSerifDisplay-Italic", synth: false });
  assert.deepEqual(C.italicOf("Anton"), { font: "Anton", synth: true });
  // ancho util: x es el CENTRO del bloque con cualquier alineacion (simetria) -> 2 * min(x - 0.12,
  // 0.88 - x); minimo 0.3; deriva
  assert.ok(Math.abs(C.textUsable(0.5, "center") - 0.76) < 1e-9);
  assert.ok(Math.abs(C.textUsable(0.5, "left") - 0.76) < 1e-9);
  assert.ok(Math.abs(C.textUsable(0.35, "right") - 0.46) < 1e-9);
  assert.ok(Math.abs(C.textUsable(0.1, "left") - 0.3) < 1e-9);
  assert.ok(Math.abs(C.textUsable(0.5, "center", 0.5, 0.04) - 0.76 * 0.5 / 1.04) < 1e-9);
});

test("traducir titulos por item: editar a mano con menos lineas, re-traducir y volver al original conserva TODO", () => {
  const mk = () => ({ text: [{ id: "t1", preset: "title_pop", start: 1, end: 3, text: "¿ya sientes\nSARRO?", lines: [{ text: "¿ya sientes", scale: 0.6 }, { text: "SARRO?", scale: 1 }] },
    { id: "t2", preset: "title_pop", start: 4, end: 6, text: "Hola" }],
    graphics: [{ id: "g1", kind: "cta", label: "Sigueme", start: 7, end: 9 }], cutaways: [{ id: "c1", kind: "title_card", text: { text: "Precio" }, start: 9, end: 10 }] });
  const fx = mk(), orig = JSON.parse(JSON.stringify(fx));
  const tr = (list, tag) => list.map(x => `${tag}:${x.text}`);
  // 1) traducir a pt
  let req = C.translationRequest(fx, null, "pt");
  assert.deepEqual(req.list.map(x => x.text), ["¿ya sientes", "SARRO?", "Hola", "Sigueme", "Precio"]);
  let st = C.applyTranslation(fx, null, req, tr(req.list, "pt"), "pt", []);
  assert.deepEqual(fx.text[0].lines.map(l => l.text), ["pt:¿ya sientes", "pt:SARRO?"]);
  assert.deepEqual(C.textStatus(fx, st), { translated: ["t1", "t2", "g1", "c1"], edited: [], fresh: [], total: 4 });
  // 2) el usuario corrige a mano el titulo y lo deja en UNA linea
  C.setTitleText(fx.text[0], "ja sente alívio?");
  assert.equal(fx.text[0].lines.length, 1);
  assert.deepEqual(C.textStatus(fx, st).edited, ["t1"]);
  // 3) re-traducir (p. ej. a de): lo editado a mano se respeta
  req = C.translationRequest(fx, st, "de");
  assert.deepEqual(req.kept, ["t1"]);
  assert.ok(!req.items.some(x => x.id === "t1"));
  st = C.applyTranslation(fx, st, req, tr(req.list, "de"), "de", []);
  assert.equal(C.titleText(fx.text[0]), "ja sente alívio?");
  assert.equal(fx.text[1].text, "de:Hola");                                  // desde el ORIGINAL, no desde pt
  // 4) pidiendo tambien los editados: sale del original COMPLETO (dos lineas)
  req = C.translationRequest(fx, st, "de", { edited: true });
  assert.deepEqual(req.items.map(x => x.id), ["t1"]);
  st = C.applyTranslation(fx, st, req, tr(req.list, "de"), "de", []);
  assert.deepEqual(fx.text[0].lines.map(l => l.text), ["de:¿ya sientes", "de:SARRO?"]);
  // 5) editar otra vez a una linea y volver a los originales: todas las lineas vuelven
  C.setTitleText(fx.text[0], "solo una");
  assert.equal(C.restoreOriginalTexts(fx, st), 4);
  assert.deepEqual(fx, orig);
  // un titulo nuevo tras traducir queda como 'fresh' y se traduce solo el
  const fx2 = mk(); let st2 = C.applyTranslation(fx2, null, C.translationRequest(fx2, null, "pt"), tr(C.translationRequest(fx2, null, "pt").list, "pt"), "pt", []);
  fx2.text.push({ id: "t3", preset: "title_pop", text: "Nuevo", start: 11, end: 12 });
  assert.deepEqual(C.textStatus(fx2, st2).fresh, ["t3"]);
  assert.deepEqual(C.translationRequest(fx2, st2, "pt").items.map(x => x.id), ["t3"]);
  // formato anterior (por ruta) sigue sirviendo para volver al original
  const fx3 = mk(); fx3.text[0].lines[1].text = "ALIVIO?"; fx3.text[1].text = "Ola";
  const legacy = { lang: "pt", src: [{ id: "t1", path: "lines.1.text", text: "SARRO?" }, { id: "t2", path: "text", text: "Hola" }], sig: "x" };
  C.restoreOriginalTexts(fx3, legacy);
  assert.equal(fx3.text[0].lines[1].text, "SARRO?"); assert.equal(fx3.text[1].text, "Hola");
});

test("dropReemitted: lo que el director re-emite con sufijo no viaja al re-dirigir (no se acumula); la propuesta no se toca", () => {
  const bg = (id, b, extra) => Object.assign({ id, kind: "bg_replace", start: 1.025, end: 3.28, background: b, src: "ai" }, extra || {});
  const fx = { effects: [bg("fx_hookbg_s0", "grid_warp"), bg("fx_hookbg_s0b", "rays"), bg("fx_hookbg_s0c", "grid_warp"),
    { id: "fx_hookbg_s1", kind: "bg_replace", start: 8, end: 9, src: "ai" },                       // otra toma: viaja
    { id: "fx_cam_s0", kind: "zoom", start: 1, end: 3, src: "ai" }, { id: "fx_cam_s0b", kind: "zoom", start: 5, end: 7, src: "ai" },   // otro tramo: viaja
    bg("fx_hookbg_s0d", "rays", { locked: true })],                                                  // bloqueado: viaja
    text: [{ id: "t1", text: "Hola" }] };
  const before = JSON.stringify(fx);
  const r = C.dropReemitted(fx);
  assert.deepEqual(r.removed, ["fx_hookbg_s0b", "fx_hookbg_s0c"]);
  assert.deepEqual(r.fx.effects.map(e => e.id), ["fx_hookbg_s0", "fx_hookbg_s1", "fx_cam_s0", "fx_cam_s0b", "fx_hookbg_s0d"]);
  assert.equal(r.fx.text, fx.text);
  assert.equal(JSON.stringify(fx), before);                      // el plan local (la propuesta) queda igual
});

test("iconInkDx: centro de la tinta del glifo respecto a su caja de 24 (graphics.icon_ink_dx) para centrar el icono en la vista", () => {
  const cols = n => new Array(n).fill(0);
  const a = cols(96); for (let i = 30; i <= 81; i++) a[i] = 255;           // tinta 30..81 -> centro 56 px
  assert.equal(C.iconInkDx(a), 2);                                          // 56*24/96 - 12
  const b = cols(96); for (let i = 12; i <= 83; i++) b[i] = 200;             // centrada: 0
  assert.equal(C.iconInkDx(b), 0);
  const c = cols(96); for (let i = 10; i <= 81; i++) c[i] = 255; c[5] = 10;  // bajo el umbral (24) no cuenta
  assert.equal(C.iconInkDx(c), -0.5);
  assert.equal(C.iconInkDx(cols(96)), 0);
  assert.equal(C.iconInkDx(null), 0);
});

// ---- perfil: foto y logo de los enlaces (espejo de graphics.link_slot_geom / logo_look / logo_fit)
function rgbaImg(w, h, fn) {
  const px = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const v = fn(x, y); px.set(v, (y * w + x) * 4); }
  return px;
}

test("linkSlot: foto y logo concentricos con la punta; el icono con el aire de siempre", () => {
  const p = C.linkSlot("photo", 100, true);
  assert.equal(p.d, 72); assert.equal(p.inset, 14); assert.equal(p.gap, 20); assert.equal(p.padR, 42);
  const l = C.linkSlot("logo", 100, false);
  assert.ok(Math.abs(l.d - 62) < 1e-9 && Math.abs(l.inset - 19) < 1e-9);
  // icono: exactamente la pastilla de antes (ib 0.36h/0.42h, 0.2h de separacion, 0.8h/1.0h de aire)
  const i2 = C.linkSlot("icon", 100, true), i1 = C.linkSlot("icon", 100, false);
  assert.ok(Math.abs(i2.d - 36) < 1e-9 && Math.abs(i2.inset + i2.padR - 80) < 1e-9);
  assert.ok(Math.abs(i1.d - 42) < 1e-9 && Math.abs(i1.inset + i1.padR - 100) < 1e-9);
});

test("logoLook: icono de app a sangre, logo transparente con placa, blanco con placa oscura, JPG ancho con su fondo", () => {
  const app = C.logoLook(rgbaImg(40, 40, () => [255, 120, 0, 255]), 40, 40);
  assert.equal(app.bleed, true);
  const tr = C.logoLook(rgbaImg(50, 40, (x, y) => (x >= 10 && x < 30 && y >= 5 && y < 25 ? [200, 30, 180, 255] : [0, 0, 0, 0])), 50, 40);
  assert.deepEqual(tr, { bleed: false, plate: C.LOGO_PLATE, box: [10, 5, 30, 25] });
  const wh = C.logoLook(rgbaImg(30, 30, (x, y) => (x > 5 && x < 25 && y > 5 && y < 25 ? [255, 255, 255, 255] : [0, 0, 0, 0])), 30, 30);
  assert.equal(wh.plate, C.LOGO_PLATE_DARK);
  const wide = C.logoLook(rgbaImg(60, 20, (x, y) => (x > 15 && x < 45 && y > 5 && y < 15 ? [20, 40, 160, 255] : [250, 244, 230, 255])), 60, 20);
  assert.deepEqual(wide, { bleed: false, plate: "#FAF4E6", box: [0, 0, 60, 20] });
  const empty = C.logoLook(rgbaImg(8, 8, () => [0, 0, 0, 0]), 8, 8);
  assert.deepEqual(empty, { bleed: false, plate: C.LOGO_PLATE, box: [0, 0, 8, 8] });
});

test("logoFit: contain con aire en la insignia redondeada; diagonal en el aro de la redonda", () => {
  const [w, h] = C.logoFit({ box: [0, 0, 200, 100] }, 100, "squircle");
  assert.ok(Math.abs(w - 100 * (1 - 2 * C.LOGO_PAD)) < 1e-9 && Math.abs(h - w / 2) < 1e-9);
  const [cw, ch] = C.logoFit({ box: [10, 10, 110, 110] }, 100, "circle");
  assert.ok(Math.abs(Math.hypot(cw, ch) - 100 * C.LOGO_RING) < 1e-9);
});

test("mediaParam: el logo (aux) no hace de una tarjeta un kind de medio propio", () => {
  const s = C.clone(SCHEMA);
  s.tracks.graphics.items.card = { label: "Tarjeta", params: [{ k: "logo", type: "media", accept: ["image"], aux: true }] };
  s.tracks.graphics.items.cta.params.push({ k: "media", type: "media", accept: ["image"] }, { k: "logo", type: "media", accept: ["image"], aux: true });
  assert.equal(C.mediaParam(s, "graphics", "card"), null);
  assert.equal(C.mediaParam(s, "graphics", "cta").k, "media");
  assert.equal(C.mediaKindFor(s, "graphics", "image"), null);    // sin PiP: nunca una tarjeta ni un CTA
  s.tracks.graphics.items.media = { label: "PiP", params: [{ k: "media", type: "media", accept: ["image", "video"] }] };
  assert.equal(C.mediaKindFor(s, "graphics", "image"), "media");
});

// profile.js en node (sin DOM): las reglas de foto/logo por enlace del panel Perfil
function loadProfileJs() {
  const vm = require("node:vm"), fs = require("node:fs");
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "app", "static", "profile.js"), "utf8");
  const ctx = { window: {}, console };
  vm.runInNewContext(src, ctx);
  return ctx.window.PF;
}

test("perfil: foto/logo automaticos se adoptan; lo que el usuario subio o quito manda", () => {
  const PF = loadProfileJs();
  const ph = sid => ({ sid, type: "image", name: sid + ".png", w: 100, h: 100 });
  const draft = { links: [
    { platform: "instagram", kind: "social", handle: "ana.demo", url: "https://www.instagram.com/ana.demo/" },
    { platform: "web", kind: "booking", url: "https://reserva.demo/ana", photo: ph("mine"), media_src: { photo: "user" } },
    { platform: "web", kind: "web", url: "https://otra.demo/", media_off: { photo: true } },
  ] };
  const server = { media_status: "done", links: [
    { platform: "instagram", handle: "ana.demo", url: "https://www.instagram.com/ana.demo/", photo: ph("ig100") },
    { platform: "web", url: "https://reserva.demo/ana", photo: ph("og1024"), logo: ph("icon512"), media_src: { photo: "auto", logo: "auto" } },
    { platform: "web", url: "https://otra.demo/", photo: ph("og2"), logo: ph("icon2") },
  ] };
  PF.adoptLinkMedia(draft, server);
  const [ig, bk, ot] = draft.links;
  assert.equal(ig.photo.sid, "ig100"); assert.equal(ig.media_src.photo, "auto");
  assert.equal(bk.photo.sid, "mine");                         // la subida por el usuario no se pisa
  assert.equal(bk.logo.sid, "icon512"); assert.equal(bk.media_src.logo, "auto");
  assert.equal(ot.photo, undefined);                          // quitada: no vuelve
  assert.equal(ot.logo.sid, "icon2");
  assert.ok(draft.links.every(l => l._mediaTried));
  // Quitar apaga (media_off) y Cambiar la marca como del usuario
  PF.setLinkMedia(bk, "logo", null);
  assert.equal(bk.logo, undefined); assert.equal(bk.media_off.logo, true);
  PF.setLinkMedia(ig, "photo", ph("up1"));
  assert.equal(ig.photo.sid, "up1"); assert.equal(ig.media_src.photo, "user"); assert.equal(ig.media_off.photo, false);
});

test("perfil: foto de la persona = la subida, si no la de su red principal, si no la de un enlace", () => {
  const PF = loadProfileJs();
  const p = { links: [{ platform: "web", kind: "booking", photo: { sid: "og" } },
    { platform: "instagram", kind: "social", primary: true, photo: { sid: "ig" } }] };
  assert.equal(PF.facePhoto(p).sid, "ig");
  assert.equal(PF.facePhoto(Object.assign({}, p, { avatar: { sid: "av" } })).sid, "av");
  p.links[1].media_off = { photo: true };
  assert.equal(PF.facePhoto(p).sid, "og");
  assert.equal(PF.facePhoto({ links: [] }), null);
});
