/* Reelfy Studio · Estilo, tercera parte (claridad de ajustes, fase 4): el inspector y los catálogos.
   - ExampleLoop: un ejemplo en bucle (1-3 s) en un lienzo con TU cuadro (Estilo.Frames) y el mismo
     dibujo del Studio (FX.drawLiveTo); la camara de los efectos (acercamiento, golpe de zoom,
     sacudida, movimiento lento), los filtros (blanco y negro, solo tú a color, fondo desenfocado...) y
     las transiciones entre dos de tus tomas.
   - Galerías del inspector: «Estilo de título», «Tipo de gráfico», «Tipo de escena», «Tipo de efecto» y
     «Transición» (rejilla de 3 con su bucle; el último renglón centrado), las animaciones de entrada y
     salida, las figuras, los fondos, los tipos de tarjeta e invitación.
   - Campos: tiempo en m:ss del video final con «Aquí», muestras de color con el color que de verdad
     se pinta (el de tu marca cuando el elemento no trae uno), palabras destacadas como botones.
   - Sonidos: por grupo, cada uno con «Escuchar»; el menú «Agregar» en rejilla con su miniatura
     animada (los sonidos con «Escuchar» y al pasar el mouse).
   - Inicio, Montar clips y Limpieza usan la misma fila (HelpRow.hydrate) y sus ejemplos.
   Usa las piezas de estilo.js (Estilo._k) y estilo_proyecto.js (Estilo._kp, ListenButton, Audio). Todo
   texto viene de ui_copy.json; toda escritura al plan la hace quien llama (FX.edit / FX.gesture).
   Lee los globales de index.html (PLAN, V, FMT...) solo dentro de funciones. */
(function () {
  "use strict";
  const E = window.Estilo;
  if (!E || !E._k) return;
  const C = window.FXCore;
  const K = E._k, KP = E._kp || {};
  const { $, $$, esc, ICO, CHK, clamp, REDUCED, uid, T, copy, HelpRow, Segmented, Ticker, Frames, fitCanvas, radioKeys, roving, frameLayer, fmtAspect } = K;
  const UI = () => K.UI();
  const schema = () => (window.FX && FX.schema && FX.schema()) || null;
  const hasPlan = () => K.hasPlan();
  const brand = () => K.brand();
  /** Nombre de una opcion (ui_copy.json → options, con sus alias). */
  function optName(path, v) {
    const U = UI(), O = U.options || {}, al = (U.aliases || {})[path];
    const e = (O[path] && O[path][v]) || (al && O[al] && O[al][v]);
    return (e && e.label) || "";
  }
  const optEntry = (path, v) => { const U = UI(), O = U.options || {}, al = (U.aliases || {})[path]; return (O[path] && O[path][v]) || (al && O[al] && O[al][v]) || {}; };

  // ------------------------------------------------------------------ lienzos fuera de pantalla
  const OFFS = new Map();
  function off(W, H, slot) {
    const k = slot || "a";
    let cv = OFFS.get(k);
    if (!cv) { cv = document.createElement("canvas"); OFFS.set(k, cv); }
    if (cv.width !== W || cv.height !== H) { cv.width = Math.max(2, W); cv.height = Math.max(2, H); }
    return cv;
  }
  const CANVAS_FILTER = (() => { try { const c = document.createElement("canvas").getContext("2d"); c.filter = "blur(2px)"; return c.filter === "blur(2px)"; } catch (e) { return false; } })();

  // ------------------------------------------------------------------ ExampleLoop
  /* Un ejemplo animado registrado en el Ticker. o = {name, period (s), still (0..1: el cuadro quieto),
     hover (true: anima solo con el mouse o el foco en `host`; quieto el resto del tiempo), on() (anima
     tambien cuando devuelve true, p. ej. la opcion elegida), fps, draw(c, W, H, u (0..1))}. */
  function loopOn(cv, o) {
    const host = o.host || cv;
    let hov = false;
    const live = () => !REDUCED() && Ticker.frozen() == null && (!o.hover || hov || !!(o.on && o.on()));
    const it = {
      el: cv, name: o.name || "loop", fps: o.fps || 24,
      get static() { return !live(); },
      draw: (t, ts) => {
        fitCanvas(cv);
        const u = live() ? ((ts / 1000) % o.period) / o.period : (o.still == null ? 0.5 : o.still);
        o.draw(cv.getContext("2d"), cv.width, cv.height, u);
      },
    };
    if (o.hover) {
      const on = () => { hov = true; it.dirty = true; Ticker.kick(); }, offh = () => { hov = false; it.dirty = true; Ticker.kick(); };
      host.addEventListener("pointerenter", on); host.addEventListener("pointerleave", offh);
      host.addEventListener("focusin", on); host.addEventListener("focusout", offh);
    }
    Ticker.add(it);
    return it;
  }
  const ExampleLoop = E.ExampleLoop = {
    /** -> {el (canvas), update(), destroy()} */
    create(o) {
      const cv = document.createElement("canvas"); cv.className = "exl" + (o.cls ? " " + o.cls : ""); cv.setAttribute("aria-hidden", "true");
      const it = loopOn(cv, o);
      return { el: cv, update() { it.dirty = true; Ticker.kick(); }, destroy() { Ticker.remove(it); cv.remove(); } };
    },
  };

  // ------------------------------------------------------------------ el cuadro de muestra
  /** La cara de la toma de muestra en un lienzo W x H que cubre el cuadro -> {x, y, w, h} (px). */
  function faceBox(W, H) {
    const img = Frames.img, f = Frames.face;
    let n = null;
    if (img && f && window.FX && FX.coverRect) {
      const r = FX.coverRect(img, W, H, Frames.focusX);
      if (r) n = [(f[0] * r.iw - r.sx) / r.sw, (f[1] * r.ih - r.sy) / r.sh, f[2] * r.iw / r.sw, f[3] * r.ih / r.sh];
    }
    if (!n || !n.every(isFinite)) n = [0.5, 0.34, 0.24, 0.15];
    return { x: n[0] * W, y: n[1] * H, w: Math.max(8, n[2] * W), h: Math.max(8, n[3] * H) };
  }
  /** Silueta aproximada de la persona (cabeza y hombros desde la cara): la mascara de los ejemplos de
      «Solo tú a color», «Fondo desenfocado», «Cambiar el fondo» y «Silueta de color». */
  function silhouette(c, W, H) {
    const f = faceBox(W, H), ny = f.y + f.h * 0.5, sw = f.w * 1.5, top = ny + f.h * 0.35;
    c.beginPath();
    c.ellipse(f.x, f.y, f.w * 0.52, f.h * 0.64, 0, 0, Math.PI * 2);
    c.moveTo(f.x - f.w * 0.26, ny);
    c.lineTo(f.x + f.w * 0.26, ny);
    c.quadraticCurveTo(f.x + sw * 0.92, top, f.x + sw, H + 2);
    c.lineTo(f.x - sw, H + 2);
    c.quadraticCurveTo(f.x - sw * 0.92, top, f.x - f.w * 0.26, ny);
    c.closePath();
  }
  /** La otra toma (transiciones): tu segunda toma con el color del proyecto; con una sola toma, la
      misma en espejo (se distingue el cambio). */
  const B_CACHE = new Map();
  function frameB(W, H) {
    const p = window.FX && FX.lookPayload ? FX.lookPayload() : null;
    const k = `${W}x${H}|${Frames.key}|${Frames.img2 ? 1 : 0}|${Frames.img ? 1 : 0}|${p ? (p.look || "") + (p.ms || "") : "o"}`;
    let cv = B_CACHE.get(k);
    if (cv) return cv;
    cv = document.createElement("canvas"); cv.width = Math.max(2, W); cv.height = Math.max(2, H);
    const c = cv.getContext("2d");
    if (Frames.img2) FX.renderMini({ ctx: c, W, H, plan: EMPTY_PLAN(), t: 0, frame: Frames.img2, look: p && p.enabled !== false ? p : null, focusX: 0.5 });
    else { c.save(); c.translate(W, 0); c.scale(-1, 1); c.drawImage(frameLayer(W, H), 0, 0); c.restore(); }
    if (B_CACHE.size > 24) B_CACHE.clear();
    B_CACHE.set(k, cv);
    return cv;
  }
  const EMPTY_PLAN = () => ({ version: 2, duration: 3600, phrases: [], cuts: [], shots: [], director: {}, entities: {},
    fx: { text: [], graphics: [], cutaways: [], effects: [], transitions: [], sfx: [], media: [], captions: { enabled: false }, grade: { enabled: false }, music: { enabled: false } } });

  // ------------------------------------------------------------------ planes de un solo elemento
  function miniPlan(track, it) {
    const fx = EMPTY_PLAN().fx;
    fx[track] = [it];
    fx.accent = C.projectAccent(PLAN);
    return { version: 2, duration: 3600, phrases: [], cuts: [], shots: [{ start: 0, end: 3600, face: Frames.face || null }],
      director: PLAN.director || {}, entities: PLAN.entities || {}, fx };
  }
  const LEN = { text: 2.0, graphics: 2.0, cutaways: 2.2, effects: 1.6 };
  /** Copia de un elemento para un ejemplo: de 0 a su duracion de muestra (sin tocar el del plan). */
  function sampleOf(track, it, patch) {
    const x = C.clone(it);
    if (patch) Object.keys(patch).forEach(k => C.setPath(x, k, patch[k]));
    if (C.isEvent(track)) x.at = 0;
    else { x.start = 0; x.end = track === "effects" && x.kind === "flash_frame" ? 0.5 : LEN[track] || 2; }   // un fogonazo es corto
    x.enabled = true;
    return x;
  }

  // ------------------------------------------------------------------ filtros sin ctx.filter (WebKit viejo)
  function pixels(c, W, H, fn) {
    try { const d = c.getImageData(0, 0, W, H), a = d.data; for (let i = 0; i < a.length; i += 4) fn(a, i); c.putImageData(d, 0, 0); } catch (e) { /* lienzo sin lectura */ }
  }
  /** Dibuja `src` en c con gris (0..1), saturacion, contraste, brillo y desenfoque (px). */
  function drawFiltered(c, src, W, H, f) {
    if (CANVAS_FILTER) {
      const parts = [];
      if (f.blur) parts.push(`blur(${f.blur.toFixed(2)}px)`);
      if (f.gray) parts.push(`grayscale(${f.gray.toFixed(3)})`);
      if (f.sat != null) parts.push(`saturate(${f.sat.toFixed(3)})`);
      if (f.contrast) parts.push(`contrast(${f.contrast.toFixed(3)})`);
      if (f.bright != null) parts.push(`brightness(${f.bright.toFixed(3)})`);
      c.filter = parts.join(" ") || "none"; c.drawImage(src, 0, 0, W, H); c.filter = "none";
      return;
    }
    if (f.blur) {
      const s = Math.max(2, Math.round(f.blur * 1.6)), sm = off(Math.max(2, Math.round(W / s)), Math.max(2, Math.round(H / s)), "blur");
      sm.getContext("2d").drawImage(src, 0, 0, sm.width, sm.height);
      c.imageSmoothingEnabled = true; c.drawImage(sm, 0, 0, W, H);
    } else c.drawImage(src, 0, 0, W, H);
    if (f.gray || f.sat != null || f.contrast || f.bright != null) {
      const g = f.gray || 0, s = f.sat == null ? 1 : f.sat, ct = f.contrast || 1, b = f.bright == null ? 1 : f.bright;
      pixels(c, W, H, (a, i) => {
        let r = a[i], gg = a[i + 1], bb = a[i + 2];
        const L = 0.2126 * r + 0.7152 * gg + 0.0722 * bb;
        r += (L - r) * g; gg += (L - gg) * g; bb += (L - bb) * g;
        r = L + (r - L) * s; gg = L + (gg - L) * s; bb = L + (bb - L) * s;
        a[i] = clamp(((r - 128) * ct + 128) * b, 0, 255); a[i + 1] = clamp(((gg - 128) * ct + 128) * b, 0, 255); a[i + 2] = clamp(((bb - 128) * ct + 128) * b, 0, 255);
      });
    }
  }
  /** Los colores separados (desdoble): canal rojo a un lado, azul al otro. */
  function rgbSplit(c, src, W, H, d) {
    const ch = (col, slot) => {
      const o = off(W, H, slot), oc = o.getContext("2d");
      oc.globalCompositeOperation = "source-over"; oc.clearRect(0, 0, W, H); oc.drawImage(src, 0, 0);
      oc.globalCompositeOperation = "multiply"; oc.fillStyle = col; oc.fillRect(0, 0, W, H); oc.globalCompositeOperation = "source-over";
      return o;
    };
    c.fillStyle = "#000"; c.fillRect(0, 0, W, H);
    c.globalCompositeOperation = "lighter";
    c.drawImage(ch("#FF0000", "r"), -d, 0); c.drawImage(ch("#00FF00", "g"), 0, 0); c.drawImage(ch("#0000FF", "b"), d, 0);
    c.globalCompositeOperation = "source-over";
  }
  const NAMED = { white: "#FFFFFF", warm: "#FF9A3D", gold: "#FFC94A", rose: "#FF6F91", cool: "#8FD3FF", black: "#000000" };
  const colorOf = v => (v === "accent" ? brand() : NAMED[v] || C.normHex(v) || null);
  function hexA(h, a) { const n = parseInt((C.normHex(h) || "#FFFFFF").slice(1), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${clamp(a, 0, 1).toFixed(3)})`; }

  // ------------------------------------------------------------------ efectos sobre tu cuadro
  /** Un efecto en lt (s desde que empieza) sobre tu cuadro (W x H del formato del proyecto). */
  function drawEffect(c, W, H, it, lt) {
    const A = frameLayer(W, H), L = Math.max(0.05, (+it.end || 0) - (+it.start || 0)), on = lt >= 0 && lt < L;
    const k = it.intensity == null ? 1 : +it.intensity;
    const cam = on ? C.cameraAt([Object.assign({}, it, { start: 0, end: L, enabled: true })], lt) : { z: 1, dx: 0, dy: 0 };
    const f = faceBox(W, H), ax = clamp(f.x / W, 0.15, 0.85), ay = clamp(f.y / H, 0.15, 0.85), z = Math.max(1, cam.z);
    const sw = W / z, sh = H / z;
    const sx = clamp(ax * W - ax * sw - cam.dx * W, 0, W - sw), sy = clamp(ay * H - ay * sh - cam.dy * H, 0, H - sh);
    const base = off(W, H, "fx"), bc = base.getContext("2d");
    bc.setTransform(1, 0, 0, 1, 0, 0); bc.clearRect(0, 0, W, H); bc.drawImage(A, sx, sy, sw, sh, 0, 0, W, H);
    c.save(); c.setTransform(1, 0, 0, 1, 0, 0);
    const env = on ? clamp(lt / 0.22, 0, 1) * clamp((L - lt) / 0.22, 0, 1) : 0, kk = k * env;
    const person = () => { c.save(); silhouette(c, W, H); c.clip(); c.drawImage(base, 0, 0); c.restore(); };
    switch (on ? it.kind : "") {
      case "bw": drawFiltered(c, base, W, H, { gray: kk, contrast: 1 + ((it.contrast != null ? +it.contrast : 1.25) - 1) * kk }); break;
      case "color_pop": drawFiltered(c, base, W, H, { gray: 0.9 * kk, bright: 1 - 0.3 * kk }); person(); break;
      case "bg_blur": drawFiltered(c, base, W, H, { blur: Math.max(0.01, (it.radius != null ? +it.radius : 18) * W / 1080 * 1.4 * kk) }); person(); break;
      case "bg_replace": {
        c.drawImage(base, 0, 0);
        const cols = (Array.isArray(it.bg_colors) && it.bg_colors.length ? it.bg_colors : [brand(), "#14171D"]).map(x => C.normHex(x) || "#14171D");
        const g = c.createLinearGradient(0, 0, W * 0.4, H);
        g.addColorStop(0, cols[0]); g.addColorStop(1, cols[1] || "#14171D");
        c.globalAlpha = kk; c.fillStyle = g; c.fillRect(0, 0, W, H); c.globalAlpha = 1;
        person();
        break;
      }
      case "subject_outline": {
        c.drawImage(base, 0, 0);
        c.save(); silhouette(c, W, H);
        c.lineWidth = Math.max(1.5, (it.width != null ? +it.width : 10) * W / 1080 * 1.6); c.strokeStyle = hexA(it.color || brand(), kk); c.lineJoin = "round";
        c.shadowColor = hexA(it.color || brand(), 0.8 * kk); c.shadowBlur = (it.glow == null ? 0.6 : +it.glow) * 14 * W / 270;
        c.stroke(); c.restore();
        break;
      }
      case "rgb_split": rgbSplit(c, base, W, H, Math.round((it.amount != null ? +it.amount : 14) * W / 1080 * 2 * kk * (1 - Math.abs(2 * lt / L - 1)) + 0.5)); break;
      case "flash_frame": {
        c.drawImage(base, 0, 0);
        const p = lt / L; c.fillStyle = hexA(it.color || "#FFFFFF", (1 - p) * 0.9 * k); c.fillRect(0, 0, W, H);
        break;
      }
      default: c.drawImage(base, 0, 0);      // acercamiento, golpe, sacudida y movimiento lento: la camara
    }
    c.restore();
  }

  // ------------------------------------------------------------------ transiciones entre dos de tus tomas
  const easeIO = p => (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2);
  /** Una transicion con progreso p (0: tu toma, 1: la siguiente). */
  function drawTransition(c, W, H, it, p) {
    const A = frameLayer(W, H), B = frameB(W, H), kind = it.kind;
    const k = it.intensity == null ? 1 : +it.intensity, bell = 1 - Math.abs(2 * p - 1), e = easeIO(p);
    const dir = it.dir || "left", sx = dir === "left" ? -1 : dir === "right" ? 1 : 0, sy = dir === "up" ? -1 : dir === "down" ? 1 : 0;
    c.save(); c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, W, H);
    const fade = (a, b, x) => { c.globalAlpha = 1; c.drawImage(a, 0, 0); c.globalAlpha = clamp(x, 0, 1); c.drawImage(b, 0, 0); c.globalAlpha = 1; };
    switch (kind) {
      case "flash": c.drawImage(p < 0.5 ? A : B, 0, 0); c.fillStyle = hexA(colorOf(it.color) || "#FFFFFF", 0.95 * k * Math.pow(bell, 0.6)); c.fillRect(0, 0, W, H); break;
      case "light_leak": case "film_burn": {
        fade(A, B, (p - 0.3) / 0.4);
        const col = colorOf(it.color) || (kind === "film_burn" ? "#FF7A2E" : "#FF9A3D");
        const g = c.createRadialGradient(W * 0.78, H * 0.22, 0, W * 0.78, H * 0.22, H * 0.85);
        g.addColorStop(0, hexA("#FFF4E0", 0.9 * k * bell)); g.addColorStop(0.35, hexA(col, 0.85 * k * bell)); g.addColorStop(1, hexA(col, 0));
        c.globalCompositeOperation = "lighter"; c.fillStyle = g; c.fillRect(0, 0, W, H); c.globalCompositeOperation = "source-over";
        if (kind === "film_burn" && bell > 0.35) { c.fillStyle = hexA("#FFFFFF", (bell - 0.35) * 0.8 * k); c.fillRect(0, 0, W, H); }
        break;
      }
      case "whip": case "slide": {
        const dx = (sx || 0) * W, dy = (sy || 0) * H, mov = kind === "whip" ? e : easeIO(p);
        const blur = kind === "whip" ? Math.round(bell * 5) : 0;
        for (let i = 0; i <= blur; i++) {
          const j = blur ? (i / blur - 0.5) * 0.12 : 0;
          c.globalAlpha = blur ? 1 / (blur + 1) * 1.6 : 1;
          c.drawImage(A, (mov + j) * dx, (mov + j) * dy); c.drawImage(B, (mov + j - 1) * dx, (mov + j - 1) * dy);
        }
        c.globalAlpha = 1;
        break;
      }
      case "zoom_blur": {
        const img = p < 0.5 ? A : B, s = 1 + 0.9 * bell * k, n = Math.round(bell * 5);
        for (let i = 0; i <= n; i++) {
          const ss = s * (1 + i * 0.035);
          c.globalAlpha = n ? 1 / (n + 1) * 1.5 : 1;
          c.drawImage(img, W / 2 - W * ss / 2, H / 2 - H * ss / 2, W * ss, H * ss);
        }
        c.globalAlpha = 1;
        break;
      }
      case "glitch": {
        c.drawImage(p < 0.5 ? A : B, 0, 0);
        if (bell > 0.05) {
          const n = 7, seed = Math.floor(p * 18);
          for (let i = 0; i < n; i++) {
            const r = Math.sin((i + 1) * 12.9898 + seed * 78.233) * 43758.5453, fr = r - Math.floor(r);
            const y = fr * H, h = H * (0.03 + 0.08 * ((fr * 7) % 1)), dx = (fr - 0.5) * W * 0.25 * bell * k;
            c.drawImage(i % 2 ? A : B, 0, y, W, h, dx, y, W, h);
          }
          c.fillStyle = hexA(colorOf(it.color) || brand(), 0.18 * bell * k); c.fillRect(0, 0, W, H);
        }
        break;
      }
      case "torn_paper": {
        // la siguiente toma aparece detras de un borde rasgado que cruza la pantalla
        const horiz = sy === 0, sgn = (horiz ? sx : sy) || -1, pos = (sgn < 0 ? 1 - e : e) * (horiz ? W : H);
        c.drawImage(A, 0, 0);
        c.save(); c.beginPath();
        const steps = 18, amp = (horiz ? W : H) * 0.03;
        const jag = i => Math.sin(i * 2.7) * amp + Math.sin(i * 7.3) * amp * 0.5;
        if (horiz) {
          const x0 = sgn < 0 ? 0 : W;
          c.moveTo(x0, 0);
          for (let i = 0; i <= steps; i++) c.lineTo(pos + jag(i), i / steps * H);
          c.lineTo(x0, H);
        } else {
          const y0 = sgn < 0 ? 0 : H;
          c.moveTo(0, y0);
          for (let i = 0; i <= steps; i++) c.lineTo(i / steps * W, pos + jag(i));
          c.lineTo(W, y0);
        }
        c.closePath(); c.clip(); c.drawImage(B, 0, 0);
        c.lineWidth = Math.max(2, W * 0.02); c.strokeStyle = C.normHex(it.color) || "#F2EEE6"; c.stroke();
        c.restore();
        break;
      }
      case "circle": {
        c.drawImage(A, 0, 0);
        const r = e * Math.hypot(W, H) * 0.6;
        c.save(); c.beginPath(); c.arc(W / 2, H / 2, Math.max(0.5, r), 0, Math.PI * 2); c.clip(); c.drawImage(B, 0, 0); c.restore();
        if (it.ring && r > 1) { c.beginPath(); c.arc(W / 2, H / 2, r, 0, Math.PI * 2); c.lineWidth = Math.max(2, W * 0.02); c.strokeStyle = colorOf(it.color) || brand(); c.stroke(); }
        break;
      }
      default: c.drawImage(p < 0.5 ? A : B, 0, 0);       // corte
    }
    c.restore();
  }

  // ------------------------------------------------------------------ un elemento en su tarjeta
  /* La composicion entera (formato del proyecto) se dibuja a W*z de ancho y la tarjeta muestra el
     recorte alrededor de `view` = {cx, cy (fracciones), z}. Para titulos y graficos, la primera vez que
     el elemento esta completo la vista se acerca para que llene ~85 % de la tarjeta. */
  function drawCropped(c, W, H, view, full) {
    const z = view.z || 1;
    const Wf = Math.max(2, Math.round(W * z)), Hf = Math.max(2, Math.round(Wf / fmtAspect()));
    const o = off(Wf, Hf, "crop"), oc = o.getContext("2d");
    oc.setTransform(1, 0, 0, 1, 0, 0); oc.globalAlpha = 1; oc.clearRect(0, 0, Wf, Hf);
    const hits = full(oc, Wf, Hf) || [];
    const sx = clamp(view.cx * Wf - W / 2, 0, Math.max(0, Wf - W)), sy = clamp(view.cy * Hf - H / 2, 0, Math.max(0, Hf - H));
    c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, W, H);
    const w = Math.min(W, Wf), h = Math.min(H, Hf);
    if (h < H) { c.fillStyle = "#101318"; c.fillRect(0, 0, W, H); }
    c.drawImage(o, sx, sy, w, h, (W - w) / 2, (H - h) / 2, w, h);
    return { hits, Wf, Hf };
  }
  function itemView(track, it) {
    if (track === "cutaways" || track === "transitions") return { cx: 0.5, cy: 0.5, z: 1, fixed: true };
    if (track === "effects") { const f = faceBox(100, Math.round(100 / fmtAspect())); return { cx: 0.5, cy: clamp(f.y / Math.round(100 / fmtAspect()) + 0.08, 0, 1), z: 1, fixed: true }; }
    const y = it.y != null ? +it.y : track === "text" ? (C.TEXT_PRESET_Y && C.TEXT_PRESET_Y[it.preset]) || 0.45 : it.kind === "progress" ? 0.98 : 0.5;
    return { cx: 0.5, cy: clamp(y, 0, 1), z: 1, fit: false };
  }
  function fitView(v, r, W, H) {
    const hs = (r.hits || []).filter(h => !h.scene && h.bb && h.bb.w > 1 && h.bb.h > 1);
    if (!hs.length) return;
    const x0 = Math.min(...hs.map(h => h.bb.x)), y0 = Math.min(...hs.map(h => h.bb.y));
    const x1 = Math.max(...hs.map(h => h.bb.x + h.bb.w)), y1 = Math.max(...hs.map(h => h.bb.y + h.bb.h));
    const bw = (x1 - x0) / v.z, bh = (y1 - y0) / v.z;
    const z = clamp(Math.min(0.86 * W / Math.max(4, bw), 0.8 * H / Math.max(4, bh)), 1, 2.6);
    v.cx = clamp((x0 + x1) / 2 / r.Wf, 0, 1); v.cy = clamp((y0 + y1) / 2 / r.Hf, 0, 1); v.z = z; v.fit = true;
  }
  /** Una muestra (titulo, grafico, escena, efecto o transicion) en la fase u (0..1) de su bucle. Un
      titulo, grafico o escena se ve todo el bucle (entra al empezar y sale al terminar: nunca en blanco). */
  function drawSample(c, W, H, track, x, u, period, view) {
    const t = u * period;
    if (track === "transitions") {
      const d = clamp(+x.dur || 0.4, 0.1, 2.5);
      drawCropped(c, W, H, view, (oc, Wf, Hf) => { drawTransition(oc, Wf, Hf, x, clamp((t - (period / 2 - d / 2)) / d, 0, 1)); });
      return;
    }
    if (track === "effects") { drawCropped(c, W, H, view, (oc, Wf, Hf) => { drawEffect(oc, Wf, Hf, x, t - 0.2); }); return; }
    const L = (+x.end || 0) - (+x.start || 0);
    const full = tt => (oc, Wf, Hf) => { oc.drawImage(frameLayer(Wf, Hf), 0, 0); return FX.drawLiveTo({ ctx: oc, W: Wf, H: Hf, plan: miniPlan(track, x), t: tt }); };
    // la primera vez, la vista se acerca a lo dibujado con el elemento completo (a media vida): asi el
    // cuadro quieto de una animacion (a media entrada) usa el mismo acercamiento que el bucle
    if (!view.fixed && !view.fit) fitView(view, drawCropped(c, W, H, view, full(L * 0.6)), W, H);
    drawCropped(c, W, H, view, full(t));
  }

  // ------------------------------------------------------------------ Gallery: opciones con su ejemplo
  /* Rejilla de opciones (radiogroup) de ancho igual; el ultimo renglon, si no se llena, queda
     centrado (simetria). Debajo, la linea de lo que hace la opcion bajo el mouse (o la elegida) y
     su aviso, si tiene. o = {aria, items: [{v, label, help?, warn?}], value, cols, cls, period, still,
     hover (anima solo con el mouse; la elegida siempre), draw(c, W, H, v, u), onPick(v)} */
  const Gallery = E.Gallery = {
    create(o) {
      const wrap = document.createElement("div"); wrap.className = "kg-wrap" + (o.cls ? " " + o.cls : "");
      const g = document.createElement("div"); g.className = "kgal"; g.setAttribute("role", "radiogroup"); g.setAttribute("aria-label", o.aria || "");
      g.style.setProperty("--cols", o.cols || 3);
      const desc = document.createElement("p"); desc.className = "kdesc";
      const warn = document.createElement("p"); warn.className = "hrow-note kwarn";
      let cur = o.value, hov = null;
      o.items.forEach((x, i) => {
        const b = document.createElement("button"); b.type = "button"; b.className = "kcard"; b.setAttribute("role", "radio"); b.dataset.v = x.v == null ? "" : String(x.v); b.dataset.i = String(i);
        b.innerHTML = `<span class="kshot"><canvas aria-hidden="true"></canvas></span><span class="kname">${esc(x.label)}${CHK}</span>`;
        if (x.help) b.title = x.help;
        b.onclick = () => o.onPick(x.v);
        const show = () => { hov = x; setDesc(); }, hide = () => { if (hov === x) { hov = null; setDesc(); } };
        b.addEventListener("pointerenter", show); b.addEventListener("pointerleave", hide);
        b.addEventListener("focus", show); b.addEventListener("blur", hide);
        const cv = $("canvas", b);
        loopOn(cv, { host: b, name: o.name || "kcard", period: o.period || 2, still: o.still, hover: !!o.hover, fps: o.fps || 20,
          on: () => b.getAttribute("aria-checked") === "true", draw: (c, W, H, u) => o.draw(c, W, H, x.v, u) });
        g.appendChild(b);
      });
      radioKeys(g);
      function setDesc() {
        const x = hov || o.items.find(y => y.v === cur);
        desc.textContent = x && x.help ? `${x.label}: ${x.help}` : "";
        desc.hidden = !desc.textContent;
        warn.textContent = x && x.warn ? x.warn : ""; warn.hidden = !warn.textContent;
      }
      function sync(v) {
        cur = v;
        $$(".kcard", g).forEach(b => { const on = o.items[+b.dataset.i].v === v; b.setAttribute("aria-checked", String(on)); b.classList.toggle("on", on); });
        roving(g); setDesc();
      }
      wrap.append(g, desc, warn);
      sync(cur);
      return { el: wrap, gallery: g, sync };
    },
  };
  const ensureFrames = () => { if (hasPlan()) Frames.ensure(); };

  // ------------------------------------------------------------------ galerias del inspector
  const KIND_PATH = { text: "text.preset", graphics: "graphics.kind", cutaways: "cutaways.kind", effects: "effects.kind", transitions: "transitions.kind" };
  /** «Estilo de título», «Tipo de gráfico», «Tipo de escena», «Tipo de efecto», «Transición»: cada tipo
      en bucle con TU elemento (titulos y escenas con tu texto) o uno de muestra. o = {track, item, onPick} */
  function kindGallery(o) {
    ensureFrames();
    const S = schema(), track = o.track, it = o.item, kk = C.kindKey(S, track), path = KIND_PATH[track];
    const have = C.kinds(S, track), O = (UI().options || {})[path] || {};
    const ks = Object.keys(O).filter(k => have.includes(k)).concat(have.filter(k => !O[k]));
    const items = ks.map(k => ({ v: k, label: (O[k] && O[k].label) || C.kindLabel(S, track, k), help: O[k] && O[k].help, warn: O[k] && O[k].warn }));
    const samples = {}, views = {};
    const sample = k => {
      if (samples[k]) return samples[k];
      let x;
      if (it && it[kk] === k) x = sampleOf(track, it);
      else if (it && (track === "text" || track === "cutaways")) { x = sampleOf(track, it); C.changeKind(S, track, x, k); }
      else {
        x = C.newItem(S, track, k, 0, 3600, PLAN.fx); x = sampleOf(track, x);
        if (x.kind === "progress") x.thickness = Math.max(+x.thickness || 0, 14);   // la barra se distingue en la miniatura
      }
      if (track === "graphics") fillPerson(x);
      return (samples[k] = x);
    };
    const period = track === "transitions" ? 1.8 : track === "effects" ? 2.0 : (LEN[track] || 2) - 0.02;
    // cuadro quieto (reducir movimiento): las transiciones justo al empezar (a media transicion un fogonazo es solo blanco)
    return Gallery.create({ aria: copy(`item.kind.${track}`).label, items, value: it ? it[kk] : null, cols: 3, period, still: track === "transitions" ? 0.42 : 0.5, name: "kind",
      draw: (c, W, H, k, u) => { const x = sample(k); drawSample(c, W, H, track, x, u, period, views[k] || (views[k] = itemView(track, x))); }, onPick: o.onPick });
  }
  /** Rotulo e invitacion de muestra con los datos de la persona (perfil o lo que entendio la IA). */
  function fillPerson(x) {
    const ent = PLAN.entities || {}, pf = PLAN.profile || {};
    if (x.kind === "lower_third" && (x.title === "Nombre" || !x.title)) { x.title = pf.name || ent.name || T("brand.demo.name"); x.subtitle = pf.title || ent.role || T("brand.demo.role"); }
    // tus redes sin perfil todavia: dos de muestra para ver como se acomodan
    if (x.kind === "socials" && !(Array.isArray(x.links) && x.links.length)) x.links = [{ platform: "instagram", text: T("demo.handle") }, { platform: "tiktok", text: T("demo.handle") }];
  }
  /** Al cambiar el tipo de invitacion, el texto «Sígueme» del tipo anterior no se queda (cada tipo trae el suyo). */
  E.Catalog_ctaLabel = (x, v) => { if (x && x.kind === "cta" && v !== "follow" && /^s[ií]gueme$/i.test(String(x.label || "").trim())) delete x.label; return x; };
  /** Galeria de una opcion del elemento (animacion de entrada o salida, figura, fondo, estilo del texto,
      tipo de tarjeta o invitacion): cada opcion con TU elemento en bucle al pasar el mouse.
      o = {track, item, param (del esquema), path (de la copy), value, onPick} */
  function optionGallery(o) {
    ensureFrames();
    const track = o.track, it = o.item, p = o.param, k = p.k;
    const items = (p.opts || []).map(v => { const e = optEntry(o.path, v); return { v, label: e.label || String(v), help: e.help, warn: e.warn }; });
    if (p.nullable) items.unshift({ v: null, label: T("opt.auto"), help: T("opt.auto.help") });
    const views = {}, samples = {};
    const L = LEN[track] || 2;
    const anim = /^anim_in$/.test(k) ? "in" : /^anim_out$/.test(k) ? "out" : "";
    // cuadro quieto: a media entrada, a media salida o con el elemento completo
    const P = L - 0.02, still = anim === "in" ? 0.14 / P : anim === "out" ? (L - 0.1) / P : 0.5;
    const sample = v => samples[String(v)] || (samples[String(v)] = (() => { const x = sampleOf(track, it, { [k]: v }); if (k === "variant") E.Catalog_ctaLabel(x, v); if (track === "graphics") fillPerson(x); return x; })());
    return Gallery.create({ aria: o.aria || copy(o.copyId || `${track}.${k}`, { kind: it[C.kindKey(schema(), track)], track }).label, items, value: o.value, cols: o.cols || 3,
      period: P, still, hover: true, name: "opt", cls: "kg-opt" + (o.cls ? " " + o.cls : ""),
      draw: (c, W, H, v, u) => { const x = sample(v); drawSample(c, W, H, track, x, u, P, views[String(v)] || (views[String(v)] = itemView(track, x))); }, onPick: o.onPick });
  }
  /** Ejemplo vivo de la fila (96 x 56): TU elemento como esta ahora (se acerca a lo dibujado). */
  function itemTile(o) {
    ensureFrames();
    const track = o.track, L = LEN[track] || 2, period = C.isEvent(track) ? 1.8 : L - 0.02;
    let view = null, sig = "";
    const ex = ExampleLoop.create({ name: "tile", period, still: o.still == null ? 0.5 : o.still, hover: !o.live, fps: 20, cls: "tile",
      draw: (c, W, H, u) => {
        const x = sampleOf(track, o.get());
        const s = JSON.stringify(x);
        if (s !== sig || !view) { sig = s; view = itemView(track, x); }   // otro valor: la vista se vuelve a acercar
        drawSample(c, W, H, track, x, u, period, view);
      } });
    return ex;
  }
  /** «Detrás de la persona»: a la izquierda el texto encima de ti, a la derecha detrás (silueta). */
  function behindTile(o) {
    ensureFrames();
    return ExampleLoop.create({ name: "behind", period: 2.4, still: 0.5, hover: true, cls: "tile", draw: (c, W, H) => {
      const x = sampleOf("text", o.get(), { behind: false });
      const view = itemView("text", x);
      const Wf = W, Hf = Math.round(Wf / fmtAspect());
      const sy = clamp(view.cy * Hf - H / 2, 0, Math.max(0, Hf - H));
      const full = off(Wf, Hf, "bh"), fc = full.getContext("2d");
      fc.setTransform(1, 0, 0, 1, 0, 0); fc.clearRect(0, 0, Wf, Hf);
      fc.drawImage(frameLayer(Wf, Hf), 0, 0);
      FX.drawLiveTo({ ctx: fc, W: Wf, H: Hf, plan: miniPlan("text", x), t: 1.0 });
      // a la derecha, la persona encima del texto (como la deja el motor con el recorte)
      fc.save(); fc.beginPath(); fc.rect(Wf / 2, 0, Wf / 2, Hf); fc.clip(); silhouette(fc, Wf, Hf); fc.clip(); fc.drawImage(frameLayer(Wf, Hf), 0, 0); fc.restore();
      c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, W, H);
      c.drawImage(full, 0, sy, W, Math.min(H, Hf), 0, 0, W, Math.min(H, Hf));
      c.fillStyle = "rgba(255,255,255,.85)"; c.fillRect(Math.round(W / 2) - 1, 0, 2, H);
    } });
  }

  // ------------------------------------------------------------------ campos
  /** Tiempo en m:ss del video final con «Aquí» (el cursor). o = {get() -> s (original), set(s original), name} */
  function timeField(o) {
    const w = document.createElement("div"); w.className = "tfield";
    const inp = document.createElement("input"); inp.type = "text"; inp.className = "tf-in"; inp.inputMode = "decimal"; inp.spellcheck = false; inp.autocomplete = "off";
    inp.setAttribute("aria-label", T("time.aria", { campo: o.name || "" }));
    const here = document.createElement("button"); here.type = "button"; here.className = "btn ghost mini tf-here";
    here.innerHTML = `${ICO("target")}<span>${esc(T("time.here"))}</span>`; here.title = T("time.here.title"); here.setAttribute("aria-label", T("time.here.aria", { campo: o.name || "" }));
    w.append(inp, here);
    const fin = s => (window.FX && FX.finalTime ? FX.finalTime(s) : s);
    const show = () => { if (document.activeElement !== inp) inp.value = C.fmtTime(fin(+o.get() || 0)); };
    inp.addEventListener("change", () => {
      const v = C.parseTime(inp.value);
      if (v == null) { inp.setAttribute("aria-invalid", "true"); inp.title = T("time.bad"); inp.value = C.fmtTime(fin(+o.get() || 0)); return; }
      inp.removeAttribute("aria-invalid"); inp.title = "";
      const tl = window.FX && FX.timeline ? FX.timeline() : null;
      o.set(tl ? tl.inverse(v) : v);
      show();
    });
    inp.addEventListener("keydown", e => { if (e.key === "Enter") inp.blur(); e.stopPropagation(); });
    here.onclick = () => { o.set(typeof V !== "undefined" && V ? (V.currentTime || 0) : 0); show(); };
    show();
    return { el: w, input: inp, sync: show };
  }
  const SW_ITEM = ["#FFFFFF", "#111111", "#FFD23F", "#FF5A3C", "#2EC4B6", "#7C5CFF"];
  /** Muestras de color de un elemento: «Color de tu marca» con el color real, el color que se pinta
      cuando no hay uno propio, «Sin fondo» si se puede quitar, «Otro color» y (Avanzado) el codigo.
      o = {value, resolved, fromBrand (vacio = la marca), nullable, none (texto de la opcion vacia),
      onPick(v | null), input: (el) => cablea el «Otro color» (FX.gesture)} */
  function colorPick(o) {
    const w = document.createElement("div"); w.className = "cw-ctl ip-color";
    const cur = C.normHex(o.resolved), b = brand();
    // o.skip: colores que ese control no puede pintar (el boton «Seguir» no admite casi blancos)
    const list = [b].concat(SW_ITEM.filter(h => h !== b)).filter((h, i) => i === 0 || !(o.skip && o.skip(h)));
    const noneOn = o.none && o.value == null;
    const sw = (h, i) => {
      const on = !noneOn && C.normHex(h) === cur;
      const name = i === 0 ? T("color.brand") : C.colorName(h);
      return `<button type="button" class="cw-sw${on ? " on" : ""}" data-c="${h}" data-i="${i}" style="--c:${h}" aria-pressed="${on}" aria-label="${esc(T("color.swatch", { nombre: name }))}" title="${esc(name.charAt(0).toUpperCase() + name.slice(1))}"></button>`;
    };
    const iid = uid("ipc"), adv = E.level() === "advanced";
    w.innerHTML = `<div class="cw-sws">${o.none ? `<button type="button" class="cw-none${noneOn ? " on" : ""}" data-none="1" aria-pressed="${noneOn}">${esc(o.none)}</button>` : ""}${list.map(sw).join("")}`
      + `<label class="cw-other" title="${esc(T("color.other"))}"><input type="color" id="${iid}" value="${(cur || "#FFFFFF").toLowerCase()}" aria-label="${esc(T("color.other"))}"><span>${esc(T("color.other"))}</span></label></div>`
      + (adv ? `<input type="text" class="cw-hex" value="${esc(o.value == null ? "" : o.value)}" maxlength="7" spellcheck="false" placeholder="${esc(cur || "")}" aria-label="${esc(T("color.hex"))}">` : "");
    $$(".cw-sw", w).forEach(x => { x.onclick = () => o.onPick(x.dataset.i === "0" && o.fromBrand && o.nullable ? null : x.dataset.c); });
    const nb = $(".cw-none", w); if (nb) nb.onclick = () => o.onPick(null);
    if (o.input) o.input($('input[type="color"]', w));
    const hx = $(".cw-hex", w);
    if (hx) hx.addEventListener("change", () => {
      const s = hx.value.trim();
      if (!s && o.nullable) { hx.removeAttribute("aria-invalid"); o.onPick(null); return; }
      const h = C.normHex(s); if (!h) { hx.setAttribute("aria-invalid", "true"); return; }
      hx.removeAttribute("aria-invalid"); o.onPick(h);
    });
    return w;
  }
  /** Palabras destacadas: las palabras del titulo como botones (se prenden y se apagan). */
  const cleanWord = s => String(s || "").replace(/^[^\p{L}\p{N}$]+|[^\p{L}\p{N}%]+$/gu, "");
  function wordChips(o) {
    // o = {text, value: [palabras], onToggle(palabra)}
    const w = document.createElement("div"); w.className = "aw-ctl"; w.setAttribute("role", "group"); w.setAttribute("aria-label", T("aw.aria"));
    const seen = new Set(), words = [];
    String(o.text || "").split(/\s+/).map(cleanWord).filter(Boolean).forEach(x => { const k = x.toLowerCase(); if (!seen.has(k)) { seen.add(k); words.push(x); } });
    const on = x => (o.value || []).some(v => cleanWord(v).toLowerCase() === x.toLowerCase());
    w.innerHTML = words.length ? words.map(x => `<button type="button" class="aw-w${on(x) ? " on" : ""}" aria-pressed="${on(x)}" data-w="${esc(x)}">${esc(x)}</button>`).join("")
      : `<span class="aw-none">${esc(T("aw.none"))}</span>`;
    $$(".aw-w", w).forEach(b => { b.onclick = () => o.onToggle(b.dataset.w); });
    return w;
  }
  /** El ejemplo de las palabras destacadas: el titulo con esas palabras en su color. */
  function wordsExample(o) {
    const d = document.createElement("div"); d.className = "aw-ex";
    const col = o.color || brand(), acc = new Set((o.value || []).map(v => cleanWord(v).toLowerCase()));
    d.innerHTML = String(o.text || "").split(/(\s+)/).map(x => (/\s/.test(x) ? " " : acc.has(cleanWord(x).toLowerCase()) ? `<b style="color:${esc(col)}">${esc(x)}</b>` : esc(x))).join("");
    return d;
  }
  /** Sonidos por grupo, cada uno con «Escuchar». o = {value, onPick(nombre), gainDb, pan} */
  function sfxPicker(o) {
    const S = schema(), cat = (window.FX && FX.sfxCat && FX.sfxCat()) || {};
    const names = S ? C.kinds(S, "sfx") : Object.keys(cat);
    const G = Object.keys((UI().options || {})["sfx.group"] || {});
    const groupOf = n => (cat[n] && G.includes(cat[n].category) ? cat[n].category : G[G.length - 1]);
    const by = {}; names.forEach(n => { (by[groupOf(n)] = by[groupOf(n)] || []).push(n); });
    const groups = G.filter(g => (by[g] || []).length);
    let gcur = groupOf(o.value);
    const w = document.createElement("div"); w.className = "sfx-pick";
    const listBox = document.createElement("div"); listBox.className = "sfx-list";
    const seg = Segmented.create({ choices: groups.map(g => ({ v: g, label: optName("sfx.group", g) })), value: gcur, cls: "grid3", aria: T("sfx.group.aria"),
      onPick: g => { gcur = g; seg.update({ value: g }); drawList(); } });
    function drawList() {
      listBox.innerHTML = "";
      const items = (by[gcur] || []).map(n => {
        const label = optName("sfx.name", n) || n;
        return { v: n, label, listen: E.ListenButton ? E.ListenButton.create({ key: "sfx:" + n, name: label, cls: "sm", spec: () => ({ kind: "sfx", id: n, gainDb: o.gainDb, pan: o.pan }) }) : null };
      });
      const ol = KP.optionList({ aria: T("sfx.pick.aria"), items, value: o.value, onPick: v => o.onPick(v), cls: "sfx-opts" });
      listBox.appendChild(ol.el);
    }
    drawList();
    w.append(seg.el, listBox);
    return w;
  }

  // ------------------------------------------------------------------ menú «Agregar»
  /* El menu en rejilla: cada tipo con su miniatura animada (tu cuadro y un elemento de muestra); los
     sonidos por grupo con «Escuchar» (y suenan al pasar el mouse 300 ms o al recibir el foco). */
  function addMenuHtml(track, head, own) {
    const S = schema();
    if (track === "sfx") {
      const cat = (window.FX && FX.sfxCat && FX.sfxCat()) || {}, G = Object.keys((UI().options || {})["sfx.group"] || {});
      const groupOf = n => (cat[n] && G.includes(cat[n].category) ? cat[n].category : G[G.length - 1]);
      const by = {}; C.kinds(S, "sfx").forEach(n => { (by[groupOf(n)] = by[groupOf(n)] || []).push(n); });
      return `<div class="mh">${esc(head)}</div><p class="am-hint">${esc(T("add.sfx.hint"))}</p>`
        + G.filter(g => (by[g] || []).length).map(g => `<div class="mh">${esc(optName("sfx.group", g))}</div>`
          + by[g].map(n => `<div class="am-row"><button type="button" data-k="${esc(n)}" style="--c:250,204,21">${ICO("speaker")}<span>${esc(optName("sfx.name", n) || n)}</span></button><span class="am-ls" data-ls="${esc(n)}"></span></div>`).join("")).join("");
    }
    const O = (UI().options || {})[KIND_PATH[track]] || {}, have = C.kinds(S, track);
    const ks = Object.keys(O).filter(k => have.includes(k)).concat(have.filter(k => !O[k]));
    return `<div class="mh">${esc(head)}</div>` + (own || "")
      + `<div class="am-grid" role="group" aria-label="${esc(head)}">` + ks.map(k => `<button type="button" class="am-card" data-k="${esc(k)}" title="${esc((O[k] && O[k].help) || "")}">`
        + `<span class="am-shot"><canvas aria-hidden="true"></canvas></span><span class="am-name">${esc((O[k] && O[k].label) || C.kindLabel(S, track, k))}</span></button>`).join("") + `</div>`
      + `<p class="am-desc" aria-live="polite"></p>`;
  }
  function addMenuWire(m, track) {
    if (track === "sfx") {
      $$("[data-ls]", m).forEach(h => {
        const n = h.dataset.ls, label = optName("sfx.name", n) || n;
        if (E.ListenButton) h.appendChild(E.ListenButton.create({ key: "sfx:" + n, name: label, cls: "sm", spec: () => ({ kind: "sfx", id: n }) }).el);
      });
      // con el mouse encima o con el foco 300 ms se escucha antes de agregarlo (uno a la vez)
      $$("[data-k]", m).forEach(b => {
        let tm = 0;
        const play = () => { clearTimeout(tm); tm = setTimeout(() => { if (E.Audio) E.Audio.play({ key: "sfx:" + b.dataset.k, kind: "sfx", id: b.dataset.k }); }, 300); };
        const stop = () => clearTimeout(tm);
        b.addEventListener("mouseenter", play); b.addEventListener("mouseleave", stop);
        b.addEventListener("focus", play); b.addEventListener("blur", stop);
      });
      return;
    }
    ensureFrames();
    const S = schema(), views = {}, samples = {}, period = track === "transitions" ? 1.8 : track === "effects" ? 2.0 : (LEN[track] || 2) - 0.02;
    const desc = $(".am-desc", m), O = (UI().options || {})[KIND_PATH[track]] || {};
    $$(".am-card", m).forEach(b => {
      const k = b.dataset.k;
      const sample = () => samples[k] || (samples[k] = (() => { const x = sampleOf(track, C.newItem(S, track, k, 0, 3600, PLAN.fx)); if (track === "graphics") fillPerson(x); return x; })());
      loopOn($("canvas", b), { host: b, name: "add", period, still: 0.5, fps: 20, draw: (c, W, H, u) => { const x = sample(); drawSample(c, W, H, track, x, u, period, views[k] || (views[k] = itemView(track, x))); } });
      const show = () => { if (desc) desc.textContent = O[k] && O[k].help ? `${O[k].label}: ${O[k].help}` : ""; };
      b.addEventListener("pointerenter", show); b.addEventListener("focus", show);
    });
  }

  // ------------------------------------------------------------------ Inicio, Montar clips y Limpieza
  /** Ejemplo de «Quién sale en el video» (Inicio): el rótulo con su nombre sobre un fondo neutro. */
  function profileExample() {
    return ExampleLoop.create({ name: "pfex", period: 2.4, still: 0.5, hover: true, cls: "tile", draw: (c, W, H, u) => {
      const S = schema(); if (!S) return;
      const pf = (window.PF && PF.selected && PF.selected()) || null;
      const it = C.newItem(S, "graphics", "lower_third", 0, 3600, {});
      Object.assign(it, { start: 0, end: 2, title: (pf && (pf.name || pf.business)) || T("brand.demo.name"), subtitle: (pf && pf.title) || T("brand.demo.role"), y: 0.5 });
      const Wf = Math.round(W * 1.9), Hf = Math.round(Wf * 16 / 9), o = off(Wf, Hf, "pf"), oc = o.getContext("2d");
      oc.setTransform(1, 0, 0, 1, 0, 0);
      const g = oc.createLinearGradient(0, 0, 0, Hf); g.addColorStop(0, "#3a404c"); g.addColorStop(1, "#14171d"); oc.fillStyle = g; oc.fillRect(0, 0, Wf, Hf);
      const plan = Object.assign(EMPTY_PLAN(), { entities: {} });
      plan.fx.graphics = [it]; plan.fx.accent = C.DEFAULT_ACCENT;
      FX.drawLiveTo({ ctx: oc, W: Wf, H: Hf, plan, t: u * 2.4 });
      c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, W, H);
      c.drawImage(o, (Wf - W) / 2, Hf * 0.5 - H / 2, W, H, 0, 0, W, H);
    } });
  }
  /** Ejemplo de un fundido de entrada en el montaje (1 s): de negro a tu clip, en bucle. */
  function fadeExample(o) {
    return ExampleLoop.create({ name: "asmfade", period: 2.2, still: 0.4, hover: false, cls: "tile", draw: (c, W, H, u) => {
      const img = o.img && o.img(), t = u * 2.2, d = Math.max(0.05, o.dur ? o.dur() : 1);
      c.setTransform(1, 0, 0, 1, 0, 0); c.fillStyle = "#000"; c.fillRect(0, 0, W, H);
      if (img && (img.videoWidth || img.naturalWidth || img.width)) { c.globalAlpha = clamp(t / d, 0, 1); try { FX.drawCover(c, img, 0, 0, W, H, 0.5); } catch (e) { /* sin cuadro */ } c.globalAlpha = 1; }
    } });
  }
  /** Ejemplo de la transicion entre dos clips del montaje: A hasta la mitad y luego B (corte o fundido). */
  function asmTransExample(o) {
    return ExampleLoop.create({ name: "asmtr", period: 2.4, still: 0.5, hover: false, cls: "tile", draw: (c, W, H, u) => {
      const A = o.a && o.a(), B = o.b && o.b(), d = o.dur ? o.dur() : 0, t = u * 2.4;
      const p = d > 0 ? clamp((t - 1.2 + d / 2) / d, 0, 1) : (t < 1.2 ? 0 : 1);
      c.setTransform(1, 0, 0, 1, 0, 0); c.fillStyle = "#101318"; c.fillRect(0, 0, W, H);
      const dr = (img, a) => { if (!img) return; c.globalAlpha = a; try { FX.drawCover(c, img, 0, 0, W, H, 0.5); } catch (e) { /* sin cuadro */ } };
      dr(A, 1); dr(B, p); c.globalAlpha = 1;
    } });
  }
  /** Llena las filas fijas de una pantalla (data-setting) con su nombre y que hace (HelpRow.hydrate)
      y los textos de la copy (data-s). */
  function hydrateScreen(root) {
    if (!root || !(UI().strings)) return false;
    HelpRow.hydrate(root); E.hydrateStrings(root);
    return true;
  }

  E.Catalog = { optName, faceBox, silhouette, drawEffect, drawTransition, drawSample, itemView, sampleOf, miniPlan, kindGallery, optionGallery,
    itemTile, behindTile, timeField, colorPick, wordChips, wordsExample, sfxPicker, addMenuHtml, addMenuWire, profileExample, fadeExample,
    asmTransExample, hydrateScreen, KIND_PATH, canvasFilter: CANVAS_FILTER };
})();
