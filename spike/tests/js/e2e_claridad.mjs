// Piezas comunes de los E2E de claridad (Estilo y el inspector): ayudas que se instalan en la pagina
// (window.__e2e: pixeles, hashes, medidas) y el lint de claridad B de la spec (cada control con nombre y
// que hace, ejemplo donde aplica, sin decimales crudos ni unidades tecnicas en Basico, sin ingles ni
// jerga, sin emojis, texto >= 11 px y ayuda con contraste AA). Se pasan a page.evaluate tal cual.

export const HELPERS = () => {
  const hex = h => { const n = parseInt(h.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
  const region = (cv, r) => { const w = cv.width, h = cv.height; const [a, b, c, d] = r || [0, 0, 1, 1]; return [Math.floor(a * w), Math.floor(b * h), Math.max(1, Math.floor((c - a) * w)), Math.max(1, Math.floor((d - b) * h))]; };
  window.__e2e = {
    count(sel, h, tol, r) {
      const cv = typeof sel === "string" ? document.querySelector(sel) : sel; if (!cv || !cv.width) return -1;
      const [x, y, w, hh] = region(cv, r), d = cv.getContext("2d").getImageData(x, y, w, hh).data, [R, G, B] = hex(h), t = tol == null ? 30 : tol;
      let n = 0; for (let i = 0; i < d.length; i += 4) if (Math.abs(d[i] - R) <= t && Math.abs(d[i + 1] - G) <= t && Math.abs(d[i + 2] - B) <= t) n++;
      return n;
    },
    luma(sel, r, maxL) {
      const cv = typeof sel === "string" ? document.querySelector(sel) : sel;
      const [x, y, w, hh] = region(cv, r), d = cv.getContext("2d").getImageData(x, y, w, hh).data;
      let s = 0, n = 0; for (let i = 0; i < d.length; i += 4) { const L = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]; if (maxL == null || L <= maxL) { s += L; n++; } }
      return n ? s / n : 0;
    },
    nonBg(cv) {
      const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
      const r0 = d[0], g0 = d[1], b0 = d[2]; let n = 0, tot = d.length / 4;
      for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 10 && (Math.abs(d[i] - r0) + Math.abs(d[i + 1] - g0) + Math.abs(d[i + 2] - b0) > 30)) n++;
      return n / tot;
    },
    hash(sels) {
      let h = 2166136261 >>> 0;
      for (const sel of sels) {
        for (const cv of document.querySelectorAll(sel)) {
          if (!cv.width) continue;
          const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
          for (let i = 0; i < d.length; i += 28) for (let k = 0; k < 3; k++) { h ^= d[i + k]; h = Math.imul(h, 16777619) >>> 0; }
        }
      }
      return h;
    },
    /** Extremos horizontales de la tinta en la franja de los subtitulos (fondo liso). */
    inkX(sel, y0, y1) {
      const cv = document.querySelector(sel), w = cv.width, h = cv.height;
      const d = cv.getContext("2d").getImageData(0, Math.floor(y0 * h), w, Math.floor((y1 - y0) * h)).data;
      const rows = d.length / 4 / w; const bg = [d[0], d[1], d[2]];
      let l = w, r = -1;
      for (let y = 0; y < rows; y++) for (let x = 0; x < w; x++) { const i = (y * w + x) * 4; if (Math.abs(d[i] - bg[0]) + Math.abs(d[i + 1] - bg[1]) + Math.abs(d[i + 2] - bg[2]) > 120) { if (x < l) l = x; if (x > r) r = x; } }
      return { l, r, w };
    },
    hashCv(cv) {
      let h = 2166136261 >>> 0; const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
      for (let i = 0; i < d.length; i += 4) for (let k = 0; k < 3; k++) { h ^= d[i + k]; h = Math.imul(h, 16777619) >>> 0; }
      return h;
    },
    /** Error medio 0..255 entre dos lienzos del mismo tamano en la franja [x0, x1) (fraccion del ancho). */
    mae(a, b, x0, x1) {
      const w = a.width, h = a.height, xa = Math.floor((x0 || 0) * w), xb = Math.floor((x1 == null ? 1 : x1) * w);
      const da = a.getContext("2d").getImageData(xa, 0, xb - xa, h).data, db = b.getContext("2d").getImageData(xa, 0, xb - xa, h).data;
      let s = 0; for (let i = 0; i < da.length; i += 4) s += Math.abs(da[i] - db[i]) + Math.abs(da[i + 1] - db[i + 1]) + Math.abs(da[i + 2] - db[i + 2]);
      return s / (da.length / 4 * 3);
    },
    lumaCv(cv) { const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data; let s = 0; for (let i = 0; i < d.length; i += 16) s += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]; return s / (d.length / 16); },
    rect: sel => { const e = typeof sel === "string" ? document.querySelector(sel) : sel; if (!e) return null; const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height, r: r.right, b: r.bottom }; },
    labels: sel => [...document.querySelectorAll(sel)].map(r => r.querySelector(".hrow-lb label").textContent.trim()),
    /** Simetria de las galerias de opciones: anchos iguales, mismo hueco, el ultimo renglon centrado. */
    galSym(root) {
      const out = { width: 0, gap: 0, lastRow: 0, n: 0 };
      (root ? root.querySelectorAll(".kgal, .am-grid") : document.querySelectorAll(".kgal, .am-grid")).forEach(g => {
        if (!g.offsetParent) return;
        const cs = [...g.children].filter(c => c.offsetParent).map(c => c.getBoundingClientRect()); if (!cs.length) return;
        out.n++;
        const ws = cs.map(c => c.width); out.width = Math.max(out.width, Math.max(...ws) - Math.min(...ws));
        const rows = {}; cs.forEach(c => { const k = Math.round(c.top); (rows[k] = rows[k] || []).push(c); });
        const gr = g.getBoundingClientRect();
        Object.values(rows).forEach(r => {
          r.sort((a, b) => a.left - b.left);
          for (let i = 2; i < r.length; i++) out.gap = Math.max(out.gap, Math.abs((r[i].left - r[i - 1].right) - (r[1].left - r[0].right)));
          out.lastRow = Math.max(out.lastRow, Math.abs((r[0].left - gr.left) - (gr.right - r[r.length - 1].right)));
        });
      });
      return out;
    },
  };
};

/** Lint de claridad (B) sobre una superficie visible. -> lista de problemas. El contenido del usuario
    (sus textos, nombres, archivos: `.ucontent`) no se revisa contra la lista negra ni los decimales. */
export const LINT = ({ root, level, chrome }) => {
  const R = document.querySelector(root); if (!R) return ["sin superficie " + root];
  const ui = FX.uiCopy(), deny = (ui.deny || []).map(w => new RegExp("(?<![\\wÁÉÍÓÚáéíóúñÑ])" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?![\\wÁÉÍÓÚáéíóúñÑ])", "i"));
  const out = [];
  const visible = el => { const r = el.getBoundingClientRect(); if (!r.width || !r.height) return false; for (let e = el; e && e !== document.body; e = e.parentElement) { const s = getComputedStyle(e); if (s.display === "none" || s.visibility === "hidden") return false; } return true; };
  const user = el => !!(el && el.closest && el.closest(".ucontent"));
  // 1) cada control dentro de una fila con nombre y que hace (salvo el armazon: nivel, secciones, enlaces)
  R.querySelectorAll('input,select,textarea,[role="radio"],button').forEach(el => {
    if (!visible(el)) return;
    const row = el.closest(".hrow[data-setting]");
    const name = (el.getAttribute("aria-label") || el.getAttribute("title") || el.textContent || "").trim();
    if (chrome && el.closest(chrome)) { const grp = el.closest("[aria-label]"); const nm = name.length >= 8 ? name : grp ? grp.getAttribute("aria-label") : ""; if (!nm || nm.length < 8) out.push(`armazon sin nombre: ${el.outerHTML.slice(0, 90)}`); return; }
    if (!row) { out.push(`control fuera de fila: ${el.outerHTML.slice(0, 100)}`); return; }
    const help = row.querySelector(".hrow-help");
    if (!help || help.textContent.trim().length < 12) out.push(`fila sin «qué hace»: ${row.dataset.setting}`);
    const id = row.dataset.setting;
    if (!(ui.params[id] || ui.ui[id])) out.push(`fila sin copy: ${id}`);
    if (row.dataset.missing) out.push(`copy faltante: ${id}`);
  });
  // 2) ejemplo donde aplica
  R.querySelectorAll(".hrow[data-setting]").forEach(row => {
    if (!visible(row)) return;
    const pr = typeof PLAN !== "undefined" && PLAN && PLAN.fx ? FXCore.captionCardOf(PLAN.fx.captions || {}) : null;
    const cp = FXCore.copyFor(ui, row.dataset.setting, { preset: pr });
    if (!["vivo", "loop", "antes_despues", "audio", "texto"].includes(cp.ex)) return;
    const ex = [...row.children].find(c => c.classList.contains("hrow-ex"));
    // galerias: cada tarjeta con su ejemplo («Sin subtítulos» muestra el cuadro solo, a proposito)
    const cards = [...row.querySelectorAll(".gcard canvas, .kcard canvas")].filter(c => { const g = c.closest(".gcard"); return !g || g.dataset.k !== "none"; });
    // (el cuadro sintetico es liso: lo que no es fondo son los subtitulos; «Discreto» es chico)
    if (cards.length && !ex) { const bad = cards.filter(c => !c.width || __e2e.nonBg(c) < 0.005); if (bad.length) out.push(`galería sin ejemplo: ${row.dataset.setting} ${bad.map(c => { const b = c.closest("[data-k],[data-v]"); return b ? b.dataset.k || b.dataset.v : "?"; })}`); return; }
    // una lista de opciones que se escuchan (origen, ambiente, canciones, sonidos): su «Escuchar» es el ejemplo
    if (!ex && cp.ex === "audio" && [...row.querySelectorAll(".listen")].some(visible)) return;
    if (!ex) { out.push(`sin ejemplo: ${row.dataset.setting}`); return; }
    if (ex.dataset.ex === "texto") { if (!/^Ej/.test(ex.textContent.trim())) out.push(`ejemplo escrito sin «Ej»: ${row.dataset.setting}`); return; }
    const cvs = ex.querySelectorAll("canvas");
    if (cvs.length) { if ([...cvs].some(c => !c.width || __e2e.nonBg(c) < 0.02)) out.push(`ejemplo en blanco: ${row.dataset.setting}`); return; }
    if (!ex.querySelector("svg") && !ex.textContent.trim() && !ex.querySelector("img")) out.push(`ejemplo vacío: ${row.dataset.setting}`);
  });
  // 7) cada fila muestra SU nombre (el de la copy, o el de su estilo / tipo) en su propio .hrow-txt:
  //    una fila que envuelve otras (Limpieza › «Qué quitar») no le presta su nombre a la de adentro
  R.querySelectorAll(".hrow[data-setting]").forEach(row => {
    if (!visible(row)) return;
    const id = row.dataset.setting, e = ui.params[id] || ui.ui[id]; if (!e) return;
    const tx = [...row.children].find(c => c.classList.contains("hrow-txt"));
    const lb = tx && [...tx.children].find(c => c.classList.contains("hrow-lb"));
    const shown = lb && lb.querySelector("label") ? lb.querySelector("label").textContent.trim() : "";
    const names = new Set([e.label, ...Object.values(e.label_by || {})].map(s => String(s || "").trim()));
    if (!names.has(shown)) out.push(`fila con nombre ajeno: ${id} muestra «${shown}» (copy «${e.label}»)`);
    const help = tx && [...tx.children].find(c => c.classList.contains("hrow-help"));
    if (!help) out.push(`fila sin su propio «qué hace»: ${id}`);
  });
  // texto visible de la superficie (sin el contenido del usuario: las palabras van en canvas)
  const texts = [];
  const walk = n => { if (n.nodeType === 3) { const t = n.textContent.trim(); if (t && visible(n.parentElement)) texts.push([t, n.parentElement]); } else if (n.nodeType === 1 && !["SCRIPT", "STYLE", "svg"].includes(n.nodeName)) n.childNodes.forEach(walk); };
  walk(R);
  const attrs = [];
  // (data-tip: la ayuda (i) que se ve al pasar el mouse)
  R.querySelectorAll("[title],[aria-label],[placeholder],[data-tip],option").forEach(el => { if (user(el)) return; ["title", "aria-label", "placeholder", "data-tip"].forEach(a => { if (el.getAttribute(a)) attrs.push(el.getAttribute(a)); }); if (el.nodeName === "OPTION") attrs.push(el.textContent); });
  R.querySelectorAll("[aria-valuetext]").forEach(el => attrs.push(el.getAttribute("aria-valuetext")));
  // 3) Basico: sin decimales crudos (tambien en los valores de los campos), sin numeros a teclear, sin dB/Hz/px/EV/BPM
  if (level === "basic") {
    const raw = /(?<![\d:,.])\d*\.\d+(?!\d)(?!\s*(%|s\b|×|x\b|GB|MB|min))/;
    texts.forEach(([t, el]) => { if (el.closest('.hrow-ex[data-ex="texto"]') || user(el)) return; if (raw.test(t)) out.push(`decimal crudo en Básico: «${t}»`); });
    R.querySelectorAll('input[type="text"]').forEach(el => { if (visible(el) && !user(el) && raw.test(el.value)) out.push(`decimal crudo en Básico (campo): «${el.value}»`); });
    R.querySelectorAll('input').forEach(el => { if (visible(el) && el.type === "number") out.push("número a teclear en Básico"); if (visible(el) && el.type === "range") { const hv = el.parentElement.querySelector(".hv"); if (!hv || !/[a-zá-ú%]/i.test(hv.textContent)) out.push("barra sin valor en palabras"); } });
    const units = /\b(dB|Hz|px|EV|BPM)\b/;
    texts.filter(([t, el]) => !user(el)).concat(attrs.map(a => [a, null])).forEach(([t]) => { if (units.test(t)) out.push(`unidad técnica en Básico: «${t}»`); });
  }
  // 4) sin ingles ni jerga; 5) sin emojis
  // (las flechas tipograficas como «Izquierda ↔ Derecha» de la copy se ven como texto: no son emojis)
  const emoji = /(?![\u2190-\u21FF])\p{Extended_Pictographic}/u;
  texts.filter(([t, el]) => !user(el)).map(x => x[0]).concat(attrs).forEach(t => {
    deny.forEach(rx => { if (rx.test(t)) out.push(`jerga «${rx.source.slice(28, -27)}» en «${t}»`); });
    if (emoji.test(t)) out.push(`emoji en «${t}»`);
  });
  // 6) letra >= 11 px y ayuda con contraste AA
  const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const L = c => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const rgb = s => (s.match(/[\d.]+/g) || []).map(Number);
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor); if (c.length >= 3 && (c.length < 4 || c[3] > 0.5)) return c; } return [10, 12, 16]; };
  texts.forEach(([t, el]) => { const fs = parseFloat(getComputedStyle(el).fontSize); if (fs < 11) out.push(`letra de ${fs} px: «${t.slice(0, 30)}»`); });
  R.querySelectorAll(".hrow-help").forEach(el => {
    if (!visible(el)) return;
    const a = L(rgb(getComputedStyle(el).color)), b = L(bgOf(el)), cr = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (cr < 4.5) out.push(`contraste ${cr.toFixed(2)} en «${el.textContent.slice(0, 30)}»`);
  });
  return out;
};
