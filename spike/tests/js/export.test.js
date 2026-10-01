// Pruebas de la hoja "Exportar" del Studio (logica pura de app/static/fxcore.js): matriz de
// resoluciones por formato, nunca por encima de la fuente, nombre por defecto y saneado,
// preferencias recordadas, textos de la estimacion y del resultado, errores en espanol.
// Corre con: node --test spike/tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const C = require(path.join(__dirname, "..", "..", "app", "static", "fxcore.js"));

// ------------------------------------------------------------------ resoluciones
test("matriz de resoluciones por formato (lado corto 720/1080/1440/2160, pares)", () => {
  const want = {
    "9:16": { "720p": [720, 1280], "1080p": [1080, 1920], "1440p": [1440, 2560], "4k": [2160, 3840] },
    "16:9": { "720p": [1280, 720], "1080p": [1920, 1080], "1440p": [2560, 1440], "4k": [3840, 2160] },
    "1:1": { "720p": [720, 720], "1080p": [1080, 1080], "1440p": [1440, 1440], "4k": [2160, 2160] },
    "4:5": { "720p": [720, 900], "1080p": [1080, 1350], "1440p": [1440, 1800], "4k": [2160, 2700] },
  };
  for (const fmt of Object.keys(want))
    for (const res of C.EXPORT_RES) assert.deepEqual(C.exportDims(fmt, res), want[fmt][res], `${fmt} ${res}`);
  // el export de siempre: 9:16 -> 1080 x 1920 y 16:9 -> 1920 x 1080
  assert.deepEqual(C.exportDims("9:16", "1080p"), [1080, 1920]);
  assert.deepEqual(C.exportDims("16:9", "1080p"), [1920, 1080]);
});

test("formato original sigue el aspecto de la fuente", () => {
  assert.deepEqual(C.exportDims("original", "1080p", { w: 1920, h: 1080 }), [1920, 1080]);
  assert.deepEqual(C.exportDims("original", "4k", { w: 1920, h: 1080 }), [3840, 2160]);
  assert.deepEqual(C.exportDims("original", "4k", { w: 1080, h: 1920 }), [2160, 3840]);
  assert.deepEqual(C.exportDims("original", "720p", { w: 1440, h: 1080 }), [960, 720]);    // 4:3
  assert.deepEqual(C.exportDims("original", "1080p", { w: 1000, h: 1001 }), [1080, 1082]);   // siempre pares
  assert.deepEqual(C.exportDims("original", "1080p", null), [1080, 1920]);                 // sin fuente: vertical
  for (const r of C.EXPORT_RES) { const [w, h] = C.exportDims("original", r, { w: 1279, h: 719 }); assert.equal(w % 2 + h % 2, 0); }
});

test("clase de resolucion por el lado corto, con tolerancia", () => {
  assert.equal(C.resClass(1080, 1920), "1080p");
  assert.equal(C.resClass(1920, 1080), "1080p");
  assert.equal(C.resClass(1076, 1916), "1080p");
  assert.equal(C.resClass(2160, 3840), "4k");
  assert.equal(C.resClass(3840, 2160), "4k");
  assert.equal(C.resClass(1620, 2880), "1440p");         // el tope del video combinado (compose.MAX_VERTICAL)
  assert.equal(C.resClass(1440, 2560), "1440p");
  assert.equal(C.resClass(720, 1280), "720p");
  assert.equal(C.resClass(480, 854), "sd");
  assert.equal(C.resClass(0, 0), null);
  assert.equal(C.resClass(), null);
});

test("parseDims acepta 'WxH', arreglos y objetos", () => {
  assert.deepEqual(C.parseDims("1920x1080"), { w: 1920, h: 1080 });
  assert.deepEqual(C.parseDims("2160 × 3840"), { w: 2160, h: 3840 });
  assert.deepEqual(C.parseDims([1080, 1920]), { w: 1080, h: 1920 });
  assert.deepEqual(C.parseDims({ W: 720, H: 1280 }), { w: 720, h: 1280 });
  assert.equal(C.parseDims("abc"), null);
  assert.equal(C.parseDims([0, 10]), null);
  assert.equal(C.parseDims(null), null);
});

test("nunca por encima de la fuente; 720p y 1080p siempre", () => {
  const on = o => o.filter(x => x.enabled).map(x => x.id);
  assert.deepEqual(on(C.exportResOptions("9:16", "4k")), ["720p", "1080p", "1440p", "4k"]);
  assert.deepEqual(on(C.exportResOptions("9:16", "1440p")), ["720p", "1080p", "1440p"]);
  assert.deepEqual(on(C.exportResOptions("9:16", "1080p")), ["720p", "1080p"]);
  // fuente de 720p: 1080p sigue disponible (es el export de siempre), lo demas no
  assert.deepEqual(on(C.exportResOptions("9:16", null, "1280x720")), ["720p", "1080p"]);
  // sin max_res del servidor: la clase de la fuente
  assert.deepEqual(on(C.exportResOptions("16:9", null, { w: 3840, h: 2160 })), ["720p", "1080p", "1440p", "4k"]);
  assert.deepEqual(on(C.exportResOptions("16:9", null, null)), ["720p", "1080p"]);
  // max_res del servidor manda sobre las medidas del video combinado (montaje topado a 1620 x 2880)
  assert.deepEqual(on(C.exportResOptions("9:16", "4k", { w: 1620, h: 2880 })), ["720p", "1080p", "1440p", "4k"]);
  const o = C.exportResOptions("4:5", "4k");
  assert.deepEqual(o.find(x => x.id === "4k"), { id: "4k", label: "4K", W: 2160, H: 2700, enabled: true });
  assert.equal(C.exportMaxRes("720p"), "1080p");
  assert.equal(C.exportMaxRes(undefined, "480x854"), "1080p");
});

test("elegir la resolucion recordada o la mas alta disponible por debajo", () => {
  const o1080 = C.exportResOptions("9:16", "1080p"), o1440 = C.exportResOptions("9:16", "1440p");
  assert.equal(C.pickExportRes("4k", o1080), "1080p");
  assert.equal(C.pickExportRes("4k", o1440), "1440p");
  assert.equal(C.pickExportRes("720p", o1080), "720p");
  assert.equal(C.pickExportRes("1440p", C.exportResOptions("9:16", "4k")), "1440p");
  assert.equal(C.pickExportRes("8k", o1440), "1080p");
  assert.equal(C.pickExportRes(undefined, o1440), "1080p");
});

test("aviso de resoluciones que no agregan calidad", () => {
  assert.equal(C.exportResNote("1080p", C.exportResOptions("9:16", "1080p")), "Tu video original es 1080p; 1440p y 4K no agregarían calidad.");
  assert.equal(C.exportResNote("1440p", C.exportResOptions("9:16", "1440p")), "Tu video original es 1440p; 4K no agregaría calidad.");
  assert.equal(C.exportResNote("4k", C.exportResOptions("9:16", "4k")), "");
  assert.equal(C.exportResNote("1080p", C.exportResOptions("9:16", "1080p"), true), "Tus clips originales son 1080p; 1440p y 4K no agregarían calidad.");
  assert.equal(C.exportResNote("720p", C.exportResOptions("9:16", "720p")), "Tu video original es 720p; 1440p y 4K no agregarían calidad.");
  assert.match(C.exportResNote("sd", C.exportResOptions("9:16", null)), /menos de 720p/);
});

test("etiqueta de resolucion del resultado", () => {
  assert.equal(C.resLabel(2160, 3840), "2160 × 3840 · 4K");
  assert.equal(C.resLabel(1080, 1920), "1080 × 1920 · 1080p");
  assert.equal(C.resLabel(640, 360), "640 × 360");
  assert.equal(C.resLabel(null, 1), "");
});

// ------------------------------------------------------------------ nombre del archivo
test("saneado del nombre: prohibidos, extremos, extension, rutas y reservados", () => {
  assert.equal(C.sanitizeFileName("Mi video: parte 1/2"), "Mi video parte 1 2");
  assert.equal(C.sanitizeFileName('a*b?c"d<e>f|g\\h'), "a b c d e f g h");
  assert.equal(C.sanitizeFileName("  ..oculto. "), "oculto");
  assert.equal(C.sanitizeFileName("video.mp4"), "video");
  assert.equal(C.sanitizeFileName("video.MP4.mp4"), "video");
  assert.equal(C.sanitizeFileName("../../etc/passwd"), "etc passwd");
  assert.equal(C.sanitizeFileName("/Users/alguien/Desktop/x"), "Users alguien Desktop x");
  assert.equal(C.sanitizeFileName("C:\\Windows\\x"), "C Windows x");
  assert.equal(C.sanitizeFileName("línea\nnueva\ttab\u0000nulo"), "línea nueva tabnulo");
  assert.equal(C.sanitizeFileName("CON"), "CON_");
  assert.equal(C.sanitizeFileName("nul.txt"), "nul_.txt");
  assert.equal(C.sanitizeFileName("Consejos"), "Consejos");
  assert.equal(C.sanitizeFileName("   "), "Reelfy");
  assert.equal(C.sanitizeFileName("///", "Proyecto - 2026-09-30"), "Proyecto - 2026-09-30");
  assert.equal(C.sanitizeFileName("Reseña · Café ñandú"), "Reseña · Café ñandú");
  assert.equal(C.sanitizeFileName("a\u200bb"), "ab");
  // nada de separadores ni ".." en el resultado
  for (const s of ["..", "../a", "a/../../b", "\\\\server\\x", ".hidden", "...", "a/.."]) {
    const r = C.sanitizeFileName(s);
    assert.ok(!/[/\\]/.test(r) && !r.startsWith(".") && r !== "..", `${s} -> ${r}`);
  }
});

test("emoji compuestos (ZWJ), ZWNJ y selectores de variante se conservan; bidi e invisibles no", () => {
  // regresion: el saneado del cliente quitaba U+200B-U+200F, incluido el ZWJ (U+200D): los emoji
  // compuestos se partian ('👨‍💻 Demo' -> '👨💻 Demo') y el persa perdia su ZWNJ
  for (const s of ["👨\u200d💻 Demo", "🏳\ufe0f\u200d🌈 Orgullo", "👩🏽\u200d⚕\ufe0f Doctora", "می\u200cخواهم",
    "👨\u200d👩\u200d👧\u200d👦 Familia"]) {
    assert.equal(C.sanitizeFileName(s), s, JSON.stringify(s));
  }
  assert.equal(C.sanitizeFileName("a\u200bb\u200ec\u200fd"), "abcd");
  assert.equal(C.sanitizeFileName("video\u202ekcod"), "videokcod");             // bidi override
  assert.equal(C.sanitizeFileName("x\u2066y\u2069z\u202a\u202c"), "xyz");
  assert.equal(C.sanitizeFileName("\ufeffBOM"), "BOM");
  // el corte por bytes no deja un ZWJ colgando al final que el servidor quitaria
  const fam = "👨\u200d👩\u200d👧".repeat(20);
  const cut = C.sanitizeFileName(fam);
  assert.ok(Buffer.byteLength(cut, "utf8") <= 180 && !/[\u200c\u200d]$/.test(cut), JSON.stringify(cut.slice(-4)));
});

test("el nombre largo se corta en bytes UTF-8 sin romper caracteres", () => {
  const long = "ñ".repeat(300);
  const r = C.sanitizeFileName(long);
  assert.ok(Buffer.byteLength(r, "utf8") <= 180);
  assert.equal(r, "ñ".repeat(90));
  const emo = "\u{1F600}".repeat(80);       // 4 bytes cada uno
  assert.ok(Buffer.byteLength(C.sanitizeFileName(emo), "utf8") <= 180);
  assert.equal([...C.sanitizeFileName(emo)].length, 45);
  assert.ok(Buffer.byteLength(C.sanitizeFileName("a".repeat(400)), "utf8") <= 180);
});

test("nombre por defecto: <proyecto> - <fecha>, saneado y con patron", () => {
  const d = new Date(2026, 8, 30, 9, 5);
  assert.equal(C.exportDate(d), "2026-09-30");
  assert.equal(C.defaultExportName("Reseña de una bici", d), "Reseña de una bici - 2026-09-30");
  assert.equal(C.defaultExportName("A/B: pruebas", d), "A B pruebas - 2026-09-30");
  assert.equal(C.defaultExportName("Mi video", d, "{fecha} {proyecto} {hora}"), "2026-09-30 Mi video 09.05");
  assert.equal(C.defaultExportName("Mi video", d, "sin marcadores"), "Mi video - 2026-09-30");
  assert.equal(C.defaultExportName("", d), "Reelfy - 2026-09-30");
});

test("titulo del proyecto: tema, montaje o id", () => {
  assert.equal(C.projectTitle({ topic: "Reseña de una bicicleta urbana" }, "abc123xyz"), "Reseña de una bicicleta urbana");
  assert.equal(C.projectTitle({ takes: { topic: "  Tema   con  espacios " } }, "abc123"), "Tema con espacios");
  assert.equal(C.projectTitle({ montage: { timeline: [] } }, "abcdef99"), "Armado con IA abcdef");
  assert.equal(C.projectTitle({}, "abcdef99"), "Proyecto abcdef");
  assert.equal(C.projectTitle(null, ""), "Proyecto");
  const t = C.projectTitle({ topic: "Una frase muy larga que habla de muchas cosas distintas y que no cabe en el nombre de un archivo" }, "x");
  assert.ok(t.length <= 60 && !/\s$/.test(t), t);
  assert.ok("Una frase muy larga que habla de muchas cosas distintas y que no cabe".startsWith(t));
});

test("preferencias recordadas: navegador > servidor > por defecto; carpeta solo del servidor", () => {
  assert.deepEqual(C.mergeExportPrefs(null, null), { resolution: "1080p", quality: "high", shorts: true, folder: null, name_pattern: "{proyecto} - {fecha}" });
  const srv = { folder: "/Users/demo/Movies/Reelfy", resolution: "4k", quality: "max", name_pattern: "{proyecto} {fecha}" };
  assert.deepEqual(C.mergeExportPrefs(srv, null), { resolution: "4k", quality: "max", shorts: true, folder: "/Users/demo/Movies/Reelfy", name_pattern: "{proyecto} {fecha}" });
  const loc = { resolution: "1440p", quality: "standard", shorts: false, folder: "/tmp/inyectada" };
  const m = C.mergeExportPrefs(srv, loc);
  assert.equal(m.resolution, "1440p"); assert.equal(m.quality, "standard"); assert.equal(m.shorts, false);
  assert.equal(m.folder, "/Users/demo/Movies/Reelfy");                 // nunca la del navegador
  assert.equal(C.mergeExportPrefs({}, { resolution: "8k", quality: "ultra" }).resolution, "1080p");
  assert.equal(C.mergeExportPrefs({ folder: "  " }, {}).folder, null);
});

// ------------------------------------------------------------------ estimacion y resultado
test("tamanos y tiempos legibles", () => {
  assert.equal(C.fmtSize(498123456), "498 MB");
  assert.equal(C.fmtSize(1250000000), "1.25 GB");
  assert.equal(C.fmtSize(4500000), "4.5 MB");
  assert.equal(C.fmtSize(812000), "812 KB");
  assert.equal(C.fmtSize(0), "");
  assert.equal(C.approxSize(498123456), "~500 MB");
  assert.equal(C.approxSize(182000000), "~180 MB");
  assert.equal(C.approxSize(62400000), "~62 MB");
  assert.equal(C.approxSize(4520000), "~4.5 MB");
  assert.equal(C.approxSize(1234000000), "~1.2 GB");
  assert.equal(C.approxSize(300), "~1 MB");
  assert.equal(C.approxTime(20), "menos de 1 min");
  assert.equal(C.approxTime(145), "~2 min");
  assert.equal(C.approxTime(330), "~6 min");
  assert.equal(C.approxTime(3600), "~1 h");
  assert.equal(C.approxTime(4200), "~1 h 10 min");
  assert.equal(C.approxTime(-1), "");
  assert.equal(C.approxTime(NaN), "");
});

test("linea de la estimacion", () => {
  assert.equal(C.estimateText({ seconds: 145, bytes: 498e6 }), "Tardará ~2 min · ~500 MB");
  assert.equal(C.estimateText({ seconds: 30, bytes: 62e6 }), "Tardará menos de 1 min · ~62 MB");
  assert.equal(C.estimateText({ seconds: 330 }), "Tardará ~6 min");
  assert.equal(C.estimateText({ bytes: 2.4e9 }), "~2.4 GB");
  assert.equal(C.estimateText({}), "");
  assert.equal(C.estimateText(null), "");
});

test("duracion, rutas y etiqueta de Mostrar", () => {
  assert.equal(C.fmtDuration(74.4), "1:14");
  assert.equal(C.fmtDuration(725), "12:05");
  assert.equal(C.fmtDuration(3729), "1:02:09");
  assert.equal(C.fmtDuration(0), "0:00");
  assert.equal(C.prettyPath("/Users/demo/Movies/Reelfy"), "~/Movies/Reelfy");
  assert.equal(C.prettyPath("/home/demo/Videos"), "~/Videos");
  assert.equal(C.prettyPath("/Users/demo"), "~");
  assert.equal(C.prettyPath("C:\\Users\\demo\\Videos\\Reelfy"), "~\\Videos\\Reelfy");
  assert.equal(C.prettyPath("/Volumes/SSD/Exports"), "/Volumes/SSD/Exports");
  assert.equal(C.pathBase("/Users/demo/Movies/Reelfy/Mi video.mp4"), "Mi video.mp4");
  assert.equal(C.pathDir("/Users/demo/Movies/Reelfy/Mi video.mp4"), "/Users/demo/Movies/Reelfy");
  assert.equal(C.pathBase("C:\\x\\y.mp4"), "y.mp4");
  assert.equal(C.pathDir("C:\\x\\y.mp4"), "C:\\x");
  assert.equal(C.pathDir("/a.mp4"), "/");
  assert.equal(C.revealLabel("MacIntel"), "Mostrar en Finder");
  assert.equal(C.revealLabel("Win32"), "Mostrar en el Explorador");
  assert.equal(C.revealLabel("Linux x86_64"), "Mostrar en la carpeta");
});

test("errores del export en espanol", () => {
  assert.equal(C.exportError("OSError: [Errno 28] No space left on device").code, "space");
  assert.equal(C.exportError("PermissionError: [Errno 13] Permission denied: '/x'").code, "folder");
  assert.equal(C.exportError("Read-only file system").code, "folder");
  assert.equal(C.exportError("falló al recomponer los clips").code, "recompose");
  assert.equal(C.exportError("algo", "recompose").text, C.EXPORT_ERR.recompose);
  assert.equal(C.exportError("x", "nada").code, null);
  const other = C.exportError("El motor se detuvo.");
  assert.deepEqual(other, { code: null, text: "El motor se detuvo." });
  assert.equal(C.exportError("").text, "Ocurrió un error al exportar.");
  for (const k of ["space", "folder", "recompose"]) assert.ok(!/[a-z]+ing\b|error:/i.test(C.EXPORT_ERR[k]), k);
});

test("ruta corta para una linea", () => {
  assert.equal(C.shortPath("/Users/demo/Movies/Reelfy"), "~/Movies/Reelfy");
  const long = "/Users/demo/Documents/Clientes/Proyectos 2026/Campaña de otoño/Entregables/Reelfy";
  const s = C.shortPath(long, 40);
  assert.ok(s.length <= 40, s);
  assert.ok(s.startsWith("~/\u2026/") && s.endsWith("/Entregables/Reelfy"), s);
  assert.equal(C.shortPath("/Volumes/Disco externo/Videos/Exportados/2026/Reelfy", 24), "/\u2026/2026/Reelfy");
  assert.equal(C.shortPath("C:\\Users\\demo\\Videos\\Clientes\\Proyecto largo\\Reelfy", 26), "~\\\u2026\\Proyecto largo\\Reelfy");
  const one = C.shortPath("/Users/demo/" + "x".repeat(80), 30);
  assert.ok(one.length <= 30 && one.startsWith("\u2026"), one);
});

// ------------------------------------------------------------------ API del servidor (GET /export/options y compania)
test("calidad: ids del contrato y del servidor", () => {
  assert.equal(C.normQuality("alta"), "high");
  assert.equal(C.normQuality("ESTANDAR"), "standard");
  assert.equal(C.normQuality("maxima"), "max");
  assert.equal(C.normQuality("max"), "max");
  assert.equal(C.normQuality("ultra"), null);
  assert.equal(C.serverQuality("high"), "alta");
  assert.equal(C.serverQuality("standard", [{ id: "estandar" }, { id: "alta" }, { id: "maxima" }]), "estandar");
  assert.equal(C.serverQuality("max", ["standard", "high", "max"]), "max");
  assert.equal(C.serverQuality(undefined, []), "alta");
});

test("filas de resolucion del servidor", () => {
  const rows = C.serverResRows([
    { id: "4k", label: "4K", W: 2160, H: 3840, enabled: false, reason: "Tu video original es 1080p; 4K no agregaría calidad", recompose: false },
    { id: "720p", label: "720p", W: 720, H: 1280, enabled: true, reason: null },
    { id: "1080p", label: "1080p", W: 1080, H: 1920, enabled: true },
    { id: "1440p", label: "1440p", W: 1440, H: 2560, enabled: false, reason: "Tu video original es 1080p; 1440p no agregaría calidad" },
    { id: "8k", W: 1, H: 1 }, null,
  ]);
  assert.deepEqual(rows.map(r => r.id), ["720p", "1080p", "1440p", "4k"]);
  assert.deepEqual(rows[3], { id: "4k", label: "4K", W: 2160, H: 3840, enabled: false, reason: "Tu video original es 1080p; 4K no agregaría calidad", recompose: false, warn: "" });
  // montaje con clips de distinta resolucion: el aviso de la fila viaja tal cual
  const mix = C.serverResRows([{ id: "4k", label: "4K", W: 2160, H: 3840, enabled: true, recompose: true,
    warn: "2 de 9 clips son 1080p: se verán escalados en 4K" }]);
  assert.equal(mix[0].warn, "2 de 9 clips son 1080p: se verán escalados en 4K");
  assert.equal(C.pickExportRes("4k", rows), "1080p");
  assert.equal(C.combineReasons(rows), "Tu video original es 1080p; 1440p y 4K no agregarían calidad.");
  assert.equal(C.serverResRows([]), null);
  assert.equal(C.serverResRows("x"), null);
});

test("motivos distintos: el primero; uno solo: con punto final", () => {
  assert.equal(C.combineReasons([{ id: "4k", label: "4K", enabled: false, reason: "Tu video original es 1440p; 4K no agregaría calidad" }]),
    "Tu video original es 1440p; 4K no agregaría calidad.");
  const crop = [
    { id: "1440p", label: "1440p", enabled: true },
    { id: "4k", label: "4K", enabled: false, reason: "Al recortar tu video (4K horizontal) a Vertical 9:16 queda en 1215p; 4K no agregaría calidad" },
  ];
  assert.equal(C.combineReasons(crop), "Al recortar tu video (4K horizontal) a Vertical 9:16 queda en 1215p; 4K no agregaría calidad.");
  const mixed = [
    { id: "1440p", label: "1440p", enabled: false, reason: "Los clips originales ya no están en este equipo: sin ellos el máximo es 1080p" },
    { id: "4k", label: "4K", enabled: false, reason: "Tu video original es 1440p; 4K no agregaría calidad" },
  ];
  assert.equal(C.combineReasons(mixed), "Los clips originales ya no están en este equipo: sin ellos el máximo es 1080p.");
  assert.equal(C.combineReasons([]), "");
});

test("ultimas elecciones en cualquiera de las formas del servidor", () => {
  assert.deepEqual(C.normSettings({ last: { res: "4k", quality: "maxima", shorts: false, fmt: "9:16", folder: "/x" },
    folder: { path: "/Users/demo/Movies/Reelfy", label: "~/Movies/Reelfy" } }),
  { resolution: "4k", quality: "max", shorts: false, folder: "/Users/demo/Movies/Reelfy", folder_label: "~/Movies/Reelfy" });
  assert.deepEqual(C.normSettings({ folder: "/Users/demo/Movies/Reelfy", resolution: "1440p", quality: "standard", name_pattern: "{proyecto}" }),
    { resolution: "1440p", quality: "standard", folder: "/Users/demo/Movies/Reelfy", name_pattern: "{proyecto}" });
  assert.deepEqual(C.normSettings({ defaults: { res: "720p", quality: "alta", shorts: true }, folder: { path: "/v", label: "/v" } }),
    { resolution: "720p", quality: "high", shorts: true, folder: "/v", folder_label: "/v" });
  assert.deepEqual(C.normSettings(null), {});
  const m = C.mergeExportPrefs(C.normSettings({ last: { res: "4k", quality: "alta" }, folder: { path: "/f", label: "/f" } }), null);
  assert.deepEqual([m.resolution, m.quality, m.folder], ["4k", "high", "/f"]);
});

test("errores del servidor: codigos propios y mensajes en espanol se respetan", () => {
  assert.equal(C.exportError("x", "no_space").code, "space");
  assert.equal(C.exportError("x", "recompose_failed").code, "recompose");
  const msg = "No hay espacio suficiente para exportar en 4K: hacen falta ~3.1 GB y quedan 1.2 GB. Libera espacio o elige una resolución menor.";
  assert.deepEqual(C.exportError(msg), { code: "space", text: msg });
  const rec = "No se pudieron recomponer los clips en 4K: ffmpeg terminó con un error";
  assert.deepEqual(C.exportError(rec), { code: "recompose", text: C.EXPORT_ERR.recompose });
  const fol = "Elige la carpeta con «Cambiar…»: Reelfy solo guarda en carpetas que elegiste.";
  assert.equal(C.exportError(fol, "folder").text, fol);
  assert.equal(C.exportError("Render falló: KeyError: 'x'").text, "Render falló: KeyError: 'x'");
});
