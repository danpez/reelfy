/* Reelfy Studio: el color del export en la vista en vivo (WebGL1).
   Aplica lo que manda POST /fx/look_luts (grade.lut_payload) con la aritmetica de grade_array:
   tablas por canal (correccion de toma + estilo de color + ajustes finos) -> matriz de saturacion
   (o mezcla de canales + curva de B/N) -> vinieta. Asi Brillo, Calidez, Contraste... se ven tambien
   mientras el video corre, no solo al pausar. Un solo lienzo WebGL compartido (hasta 1080 x 1920);
   el resultado se copia al contexto 2D de quien dibuja (el filtro de efectos del 2D sigue aplicando).
   Sin WebGL: draw() devuelve false y quien llama usa su respaldo (filtro CSS o FXCore.applyLook).
     const L = LookGL.create(); L.setLut(payload);
     L.draw(ctx2d, fuente, sx, sy, sw, sh, dx, dy, dw, dh, t, {face, frame, gain}) -> true | false
   t = tiempo ORIGINAL (elige las tablas de la toma); frame = [x, y, w, h] del cuadro completo en el
   contexto (la vinieta va sobre el cuadro, no sobre el recorte); face = [cx, cy, w, h] del cuadro. */
(function () {
  "use strict";
  const C = window.FXCore;
  const MAXW = 1080, MAXH = 1920;
  const VS = "attribute vec2 aPos;varying vec2 vPos;varying vec2 vUv;uniform vec4 uSrc;"
    + "void main(){vec2 p=aPos*0.5+0.5;vPos=vec2(p.x,1.0-p.y);vUv=uSrc.xy+vPos*uSrc.zw;gl_Position=vec4(aPos,0.0,1.0);}";
  const FS = "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n"
    + "varying vec2 vPos;varying vec2 vUv;uniform sampler2D uImg;uniform sampler2D uLut;uniform mat3 uSat;uniform float uUseSat;"
    + "uniform float uMono;uniform vec3 uMix;uniform vec4 uVig;uniform vec4 uVr;uniform vec2 uSize;"
    + "vec2 at(float v){return vec2((floor(v*255.0+0.5)+0.5)/256.0,0.5);}"
    + "void main(){vec3 c=texture2D(uImg,vUv).rgb;"
    + "vec3 t=vec3(texture2D(uLut,at(c.r)).r,texture2D(uLut,at(c.g)).g,texture2D(uLut,at(c.b)).b);"
    + "vec3 o;if(uMono>0.5){o=vec3(texture2D(uLut,at(dot(uMix,t))).a);}else{o=uUseSat>0.5?clamp(uSat*t,0.0,1.0):t;}"
    + "o=floor(o*255.0+0.5)/255.0;float m=1.0;"
    + "if(uVig.x>0.002){vec2 d=floor(vPos*uSize)/uSize;vec2 f=uVr.xy+d*uVr.zw;float tt;"
    + "if(uVig.w<0.5){vec2 e=(f-uVig.yz)*2.0;e.y/=1.04;tt=clamp(sqrt(e.x*e.x*0.85+e.y*e.y)-0.45,0.0,1.0);tt=tt*tt*(3.0-2.0*tt);"
    + "m=clamp(1.0-uVig.x*1.6*pow(tt,1.15),0.0,1.0);}"
    + "else{vec2 e=vec2((f.x-uVig.y)/0.62,(f.y-uVig.z)/0.5);tt=clamp(length(e)-0.28,0.0,1.0);tt=tt*tt*(3.0-2.0*tt);m=clamp(1.0-uVig.x*tt,0.0,1.0);}"
    + "m=floor(m*255.0+0.5)/255.0;}"
    + "gl_FragColor=vec4(o*m,1.0);}";

  let G = null, failed = false, warned = false, SEQ = 0;
  const stats = { draws: 0, uploads: 0, tables: 0, gains: 0, fallbacks: 0 };
  function warn(msg, e) { if (!warned) { warned = true; console.warn("LookGL: " + msg + "; se usa el respaldo", e || ""); } }

  /** Contexto WebGL compartido (una vez). -> G o null (sin WebGL). */
  function init() {
    if (G) return G.lost ? null : G;
    if (failed) return null;
    try {
      const cv = document.createElement("canvas"); cv.width = 2; cv.height = 2;
      const opts = { alpha: false, antialias: false, depth: false, stencil: false, premultipliedAlpha: false, preserveDrawingBuffer: true };
      const gl = cv.getContext("webgl", opts) || cv.getContext("experimental-webgl", opts);
      if (!gl) throw new Error("sin WebGL");
      const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
      const prog = gl.createProgram();
      gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS));
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
      gl.useProgram(prog);
      const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
      const aPos = gl.getAttribLocation(prog, "aPos"); gl.enableVertexAttribArray(aPos); gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
      const tex = unit => { const t = gl.createTexture(); gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t);
        const f = unit ? gl.NEAREST : gl.LINEAR;
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, f); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, f);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE); return t; };
      const tImg = tex(0), tLut = tex(1);
      const U = {};
      ["uSrc", "uImg", "uLut", "uSat", "uUseSat", "uMono", "uMix", "uVig", "uVr", "uSize"].forEach(n => { U[n] = gl.getUniformLocation(prog, n); });
      gl.uniform1i(U.uImg, 0); gl.uniform1i(U.uLut, 1);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false); gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      G = { cv, gl, prog, U, tImg, tLut, src: null, srcVer: -1, lutKey: "", lost: false };
      cv.addEventListener("webglcontextlost", e => { e.preventDefault(); G.lost = true; warn("se perdio el contexto WebGL"); });
      return G;
    } catch (e) {
      failed = true; warn("WebGL no disponible", e);
      return null;
    }
  }

  // muestra chica para medir la ganancia del B/N (la luma de la cara cambia cuadro a cuadro): la
  // region de la cara del motor (_bw_face_gain) a su tamano (un pixel de cada dos, como el motor) o
  // la escena reducida a 64 px de ancho
  let MS = null;
  function measureGain(inst, src, sx, sy, sw, sh, T, face) {
    if (!MS) MS = document.createElement("canvas");
    const c = MS.getContext("2d", { willReadFrequently: true }), bw = inst.p.bw;
    let rx = sx, ry = sy, rw = sw, rh = sh, mw = 64, mh = Math.max(8, Math.min(160, Math.round(64 * sh / Math.max(1, sw)))), step = 1, isFace = false;
    if (face) {
      const [cx, cy, fw, fh] = face;
      const x0 = Math.max(0, Math.trunc((cx - 0.28 * fw) * sw)), x1 = Math.min(Math.round(sw), Math.trunc((cx + 0.28 * fw) * sw));
      const y0 = Math.max(0, Math.trunc((cy - 0.10 * fh) * sh)), y1 = Math.min(Math.round(sh), Math.trunc((cy + 0.32 * fh) * sh));
      if (x1 - x0 >= 4 && y1 - y0 >= 4) {
        isFace = true; rx = sx + x0; ry = sy + y0; rw = x1 - x0; rh = y1 - y0;
        mw = Math.min(160, rw); mh = Math.min(160, rh); step = mw === rw && mh === rh ? 2 : 1;
      }
    }
    if (MS.width !== mw || MS.height !== mh) { MS.width = mw; MS.height = mh; }
    c.drawImage(src, rx, ry, rw, rh, 0, 0, mw, mh);
    const gray = C.lookGray(inst.p, c.getImageData(0, 0, mw, mh).data, mw, mh, T), v = [];
    for (let y = 0; y < mh; y += step) for (let x = 0; x < mw; x += step) v.push(gray[y * mw + x]);
    stats.gains++;
    return C.bwGainOf(C.percentileOf(v, isFace ? 60 : 50) / 255, isFace, bw);
  }

  const isVideo = s => typeof HTMLVideoElement !== "undefined" && s instanceof HTMLVideoElement;

  function create() {
    const inst = { p: null, ver: 0, gain: null, gainKey: "", gainAt: 0 };
    inst.setLut = function (p) { inst.p = p || null; inst.ver = ++SEQ; inst.gain = null; inst.gainKey = ""; };
    inst.payload = () => inst.p;
    inst.ready = () => !!(inst.p && init());
    /** Dibuja `src` (video, lienzo o imagen) con el color del export. -> false sin WebGL o sin tablas. */
    inst.draw = function (ctx, src, sx, sy, sw, sh, dx, dy, dw, dh, t, o) {
      const p = inst.p, x = o || {};
      if (!p || !p.lut) return false;
      if (p.enabled === false) { ctx.drawImage(src, sx, sy, sw, sh, dx, dy, dw, dh); return true; }
      const g = init(); if (!g) { stats.fallbacks++; return false; }
      const gl = g.gl;
      try {
        // tamano del pase: el del destino (en pixeles del lienzo), hasta 1080 x 1920
        let vw = Math.max(1, Math.round(Math.abs(dw))), vh = Math.max(1, Math.round(Math.abs(dh)));
        const k = Math.min(1, MAXW / Math.max(vw, vh * MAXW / MAXH), MAXH / vh);
        if (k < 1) { vw = Math.max(1, Math.round(vw * k)); vh = Math.max(1, Math.round(vh * k)); }
        if (g.cv.width < vw || g.cv.height < vh) { g.cv.width = Math.max(g.cv.width, vw); g.cv.height = Math.max(g.cv.height, vh); }
        const fr = x.frame || [dx, dy, dw, dh];
        // tablas de la toma (igualar tomas) y, en B/N, la ganancia medida en el cuadro
        const si = C.lookShotAt(p, t), T = si >= 0 ? p.per_shot[si] : p.lut;
        let bwt = null, gain = null;
        if (p.mono && p.bw) {
          gain = x.gain;
          if (gain == null) {
            const now = performance.now(), vid = isVideo(src), key = `${inst.ver}|${si}|${vid ? "v" : src.__lookVer || 0}`;
            if (inst.gain == null || key !== inst.gainKey || src !== inst.gainSrc || (vid && now - inst.gainAt > 150)) {
              const fd = x.face ? [(x.face[0] * fr[2] + fr[0] - dx) / dw, (x.face[1] * fr[3] + fr[1] - dy) / dh, x.face[2] * fr[2] / dw, x.face[3] * fr[3] / dh] : null;
              try { inst.gain = measureGain(inst, src, sx, sy, sw, sh, T, fd); } catch (e) { inst.gain = 1; }
              inst.gainKey = key; inst.gainSrc = src; inst.gainAt = now;
            }
            gain = inst.gain;
          }
          bwt = C.bwTable(p.bw, gain);
        }
        const lutKey = `${inst.ver}|${si}|${gain}`;
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, g.tLut);
        if (lutKey !== g.lutKey) {
          const a = new Uint8Array(256 * 4);
          for (let i = 0; i < 256; i++) { a[i * 4] = T.r[i]; a[i * 4 + 1] = T.g[i]; a[i * 4 + 2] = T.b[i]; a[i * 4 + 3] = bwt ? bwt[i] : 0; }
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, a);
          g.lutKey = lutKey; stats.tables++;
        }
        // la fuente (un video se sube en cada cuadro; un lienzo o una imagen, solo si cambia)
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, g.tImg);
        const ver = src.__lookVer || 0;
        if (isVideo(src) || g.src !== src || g.srcVer !== ver) {
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
          g.src = isVideo(src) ? null : src; g.srcVer = ver; stats.uploads++;
        }
        const iw = src.videoWidth || src.naturalWidth || src.width, ih = src.videoHeight || src.naturalHeight || src.height;
        const U = g.U;
        gl.uniform4f(U.uSrc, sx / iw, sy / ih, sw / iw, sh / ih);
        const M = p.sat;
        gl.uniform1f(U.uUseSat, M ? 1 : 0);
        if (M) gl.uniformMatrix3fv(U.uSat, false, new Float32Array([M[0][0], M[1][0], M[2][0], M[0][1], M[1][1], M[2][1], M[0][2], M[1][2], M[2][2]]));
        gl.uniform1f(U.uMono, p.mono ? 1 : 0);
        const mx = p.mix || [0.4, 0.49, 0.11];
        gl.uniform3f(U.uMix, mx[0], mx[1], mx[2]);
        const v = p.vig || {}, [cx, cy] = C.lookVigCenter(p, x.face);
        gl.uniform4f(U.uVig, +v.amount || 0, cx, cy, v.shape === "bw" ? 1 : 0);
        gl.uniform4f(U.uVr, (dx - fr[0]) / fr[2], (dy - fr[1]) / fr[3], dw / fr[2], dh / fr[3]);
        gl.uniform2f(U.uSize, vw, vh);
        gl.viewport(0, 0, vw, vh);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        ctx.drawImage(g.cv, 0, g.cv.height - vh, vw, vh, dx, dy, dw, dh);
        stats.draws++;
        return true;
      } catch (e) {
        warn("no se pudo dibujar", e); stats.fallbacks++;
        return false;
      }
    };
    return inst;
  }

  window.LookGL = {
    create,
    supported: () => !!init(),
    /** Marca un lienzo como cambiado (se vuelve a subir aunque sea el mismo objeto). */
    touch(cv) { if (cv) cv.__lookVer = (cv.__lookVer || 0) + 1; },
    stats: () => Object.assign({}, stats),
  };
})();
