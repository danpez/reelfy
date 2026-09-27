# Reelfy Motion Engine (v0.5) — especificacion y contrato

Estado: en construccion (rama `feat/motion-engine`). Este documento es el CONTRATO entre modulos:
los nombres, firmas, claves JSON y catalogos de aqui son normativos. Si un modulo necesita algo
que no esta aqui, se agrega aqui primero.

## 0. Objetivo

Que Reelfy entregue, de forma automatica y 100% local, una edicion al nivel de un editor
profesional de short-form (referencia: un short editado profesionalmente, antes/despues, guardado fuera del repo):

- Tipografia animada con varios estilos (serif elegante, bold apilado, cascada, manuscrita,
  texto detras del sujeto, contador, subrayado/marcador) sincronizada a la voz.
- Graficos: garabatos dibujados a mano animados (destellos, estrellas, flechas, circulos,
  trazos punteados), iconos que se dibujan solos, tarjetas de informacion (precio, ubicacion,
  horario, link), llamadas a la accion (seguir, link abajo), lower thirds.
- Escenas de corte a pantalla completa (title cards con fondos animados, tarjetas grandes).
- Efectos sobre el video: zoom suave, punch-in, sacudida, blanco y negro dramatico,
  color pop (sujeto a color / fondo gris), desenfoque de fondo, reemplazo de fondo, contorno.
- Transiciones: flash, light leak, whip pan, zoom blur, glitch, film burn, papel rasgado,
  circulo, empuje.
- Color: igualacion entre tomas + look, vinieta, grano.
- Audio: SFX en cada evento visual (whoosh, pop, ding, cash, click, riser, impacto...),
  musica generada localmente y ajustada al video (ducking bajo la voz, cortes dramaticos),
  voz de estudio, loudness final -14 LUFS.
- Todo editable: cada decision de la IA es un elemento del plan con `enabled` y parametros;
  el Studio lo muestra en pistas con inspector y vista previa exacta.

Reglas: 100% local (sin APIs de pago ni nube; descargas de modelos permitidas en el setup),
licencias comerciales (MIT/Apache/BSD/OFL/CC0 o contenido propio sintetizado), cero emojis en
codigo/UI, determinista (mismo plan + seed = mismo video).

## 1. Arquitectura

```
analyze (existente)  ->  plan v1 (phrases, cuts, beats, camera, highlights)
compose (existente)  ->  + plan.shots (limites reales de cada clip)
director.direct(plan, style, intensity, seed)       -> plan.fx, plan.entities, plan.director (v2)
render_pro(plan, out_dir, opts):
  1. timeline   : keeps (cuts habilitados), remap orig->final, joins (uniones), duracion final
  2. base pass  : ffmpeg fuente -> reencuadre + cortes -> base_<hash>.mp4 (WxH, fps, GOP corto)
                  + voice_<hash>.wav (cadena de estudio + cortes, 48 kHz estereo)
  3. analysis   : rostro en timeline final (YuNet) + mattes (MODNet) solo en rangos que lo piden
  4. audio      : musica (IA/biblioteca) ajustada + SFX + voz -> mix.wav (-14 LUFS, TP -1.5)
  5. compositor : N procesos, cada uno un rango de cuadros: base -> capas -> encode chunk
                  -> concat -> mux con mix.wav
  6. shorts     : corte de highlights (tiempos remapeados) del video final
```

Paquete: `spike/scripts/motion/`

| Archivo | Responsable de |
|---|---|
| `ctx.py` | FrameCtx, superficies skia sobre numpy, conversion, helpers de dibujo comunes |
| `ease.py` | curvas de easing, resortes, helpers de animacion (in/out, stagger) |
| `assets.py` | registro de fuentes, iconos Tabler (paths skia), paletas, catalogo SFX/musica, rutas |
| `schema.py` | plan v2: defaults, normalize/validate, migracion v1->v2, catalogo de kinds con esquemas de parametros (lo consume el inspector de la UI via `/fx/schema`) |
| `timeline.py` | keeps, remap, joins, mapeo de items orig->final, rangos |
| `video_io.py` | probe (rotacion/HDR), base pass, decode/encode pipes, encoders por plataforma, concat, mux |
| `render.py` | compositor: orquestacion, render de cuadro, workers paralelos, preview de cuadro, draft |
| `camera.py` | zoom, punch, shake, kenburns, whip offsets, motion blur; centrado en rostro |
| `grade.py` | igualacion de tomas, looks, blanco y negro, vinieta, grano, nitidez |
| `matte.py` | MODNet, gating por rostro, suavizado temporal, upsample guiado, cache; efectos de sujeto |
| `text.py` | subtitulos (presets) y titulos cineticos |
| `graphics.py` | garabatos, iconos, tarjetas, lower third, CTA, fondos animados, barra de progreso |
| `cutaways.py` | escenas a pantalla completa (title_card, info_card, photo) |
| `transitions.py` | transiciones en uniones |
| `sfx.py` | sintesis de SFX (generador del catalogo) |
| `audio.py` | mezclador: voz, musica, SFX, ducking, stops, loudness |
| `music.py` | musica: ACE-Step (acestep.cpp) + biblioteca + generador procedural; ajuste a duracion |
| `director.py` | IA director: tomas (VLM), guion (LLM), coreografo (reglas), estilos, validacion |

Tests: `spike/tests/` (pytest). Demos visuales: `spike/tests/demos/` (scripts que producen hojas
de contacto PNG/MP4 en `spike/work/demos/`).

## 2. Unidades y coordenadas

- Salida W x H (9:16 = 1080 x 1920). Draft = escala 0.5 (540 x 960).
- Posiciones normalizadas: `x`, `y` en [0,1] relativas a W y H. `(0,0)` arriba-izquierda.
- Tamanos: `size` = fraccion de W (tipografia: tamano de fuente en px = `size * W`).
- `ctx.px(v)`: convierte px de referencia 1080 de ancho a px reales (`v * W/1080`). Todo grosor,
  sombra o radio fijo se expresa en px de referencia y pasa por `ctx.px`.
- Zonas seguras (TikTok/Reels): `SAFE = {top:0.09, bottom:0.80, left:0.06, right:0.88}`.
  Subtitulos por defecto en y=0.72. Ningun texto importante fuera de SAFE.
- Tiempo del plan: SIEMPRE tiempo ORIGINAL de la fuente (segundos). El render convierte a
  tiempo FINAL con `timeline.remap`. Los renderers reciben tiempo local del item en tiempo final.
- fps de salida: 30 (default) o 60 (`opts.fps`).

## 3. Plan v2 (claves nuevas; las de v1 se conservan)

```json
{
  "version": 2,
  "shots": [
    {"start": 0.0, "end": 7.99, "src": "IMG_0246.MOV", "person": true, "framing": "general",
     "place": "consultorio dental", "text_seen": [], "use": "a_camara", "free_space": "arriba"}
  ],
  "entities": {
    "name": "Dra. Ana Ruiz", "role": "Dentista", "price": "$1,000 MXN",
    "address": "Av. Central 123", "landmark": "a 2 cuadras de la Glorieta Norte",
    "cta": "Checa mis horarios en el link", "link_label": "Agenda tu cita", "handle": ""
  },
  "director": {"style": "dinamico", "intensity": 0.75, "seed": 7, "locked": [], "llm": "qwen3.5:4b"},
  "fx": {
    "captions": {"enabled": true, "preset": "clean_bold", "y": 0.72, "size": 0.052,
                 "font": "Poppins-ExtraBold", "color": "#FFFFFF", "accent": "#FFD23F",
                 "case": "none", "max_words": 4, "hide_during_titles": true},
    "text":        [],
    "graphics":    [],
    "cutaways":    [],
    "effects":     [],
    "transitions": [],
    "sfx":         [],
    "music":  {"enabled": true, "source": "ai", "track": null, "file": null, "prompt": "",
               "bpm": 110, "gain_db": -17, "duck_db": -9, "fade_in": 0.2, "fade_out": 1.5,
               "stops": [], "swells": []},
    "grade":  {"enabled": true, "look": "warm_clean", "match_shots": true,
               "vignette": 0.15, "grain": 0.025, "sharpen": 0.15}
  }
}
```

Campos comunes de todo item de lista (`text`, `graphics`, `cutaways`, `effects`):
`id` (str unico), `start`, `end` (tiempo original), `enabled` (bool), `src` ("ai"|"user"),
`locked` (bool, el director no lo toca al re-dirigir), `intensity` (0..1, default 1), `z` (int opcional, orden).
`transitions` y `sfx` usan `at` (tiempo original del evento) en vez de start/end
(`transitions`: `dur` en segundos, centrada en la union salvo `align`).

### 3.1 Catalogo de kinds (normativo)

**text** (`preset` + campos):
- comunes: `text` (str, `\n` separa lineas) o `lines` [{`text`,`scale`,`font`,`color`,`italic`}],
  `x`, `y`, `size`, `font`, `color`, `accent`, `accent_words` [str], `align` (left|center|right),
  `stroke` (px ref), `shadow` (0..1), `anim_in` (pop|rise|slide_left|slide_right|type|mask|fade|scale_down|word_stagger|letter_stagger),
  `anim_out` (fade|pop_out|slide_up|none), `behind` (bool: detras del sujeto), `box` (null|color de caja).
- presets: `title_pop`, `stack`, `cascade`, `serif_elegant`, `script_accent`, `behind_giant`,
  `highlight_marker`, `typewriter`, `counter` (`from`,`to`,`prefix`,`suffix`,`decimals`), `kicker`.

**captions.preset**: `clean_bold` (palabra por palabra, activa en acento), `karaoke`, `boxed`,
`minimal_small` (pequeno blanco al pecho), `bold_caps`, `none`.

**graphics** (`kind` + campos; todos con `x`,`y`,`size`,`color`,`anim_in`,`anim_out`,`rotation`):
- `doodle`: `name` en {sparkle, star4, star5, burst, rays, arrow_curve, arrow_straight,
  arrow_down, circle_scribble, underline, squiggle, dotted_path, heart, check, cross, exclaim,
  question, swirl, lightning, loop}; `stroke` (px ref), `draw` (bool: se dibuja con trazo), `wiggle` (0..1),
  `path_to` [x,y] (para flechas/punteados).
- `icon`: `name` (Tabler, p. ej. "dental", "map-pin", "clock", "cash"), `variant` (outline|filled),
  `badge` (none|circle|squircle), `badge_color`, `draw` (bool).
- `card`: `variant` en {price, location, schedule, link, phone, rating, checklist, stat, generic};
  `title`, `subtitle`, `value`, `items` [str], `icon`, `theme` (light|dark|brand).
- `lower_third`: `title`, `subtitle`.
- `cta`: `variant` en {follow, link_below, comment, save, share}; `label`, `handle`.
- `progress`: barra superior de progreso (`color`, `thickness`).
- `sticker_text`: palabra en pastilla/etiqueta (`bg`, `fg`).

**cutaways** (reemplazan el cuadro completo):
- `kind` en {title_card, info_card, photo, self_broll}.
- `background` en {grid_warp, gradient, paper, dark_stars, rays, blur_self, color, halftone};
  `bg_colors` [hex]; `text` (item de text embebido: preset + campos); `graphics` [items embebidos];
  `self_broll`: `src_t` (tiempo orig del material), `zoom_from`, `zoom_to`.

**effects** (sobre el video base, por rango):
- `zoom` (`from`,`to`, easing, centrado en rostro), `punch` (`amount`, `hold`),
  `shake` (`amp`,`freq`,`decay`), `kenburns` (`from`,`to`,`pan` [dx,dy]),
  `bw` (`contrast`,`subject_color` bool), `color_pop` (`bg_desat`,`bg_dark`),
  `bg_blur` (`radius`), `bg_replace` (`background` como cutaways, `bg_colors`),
  `subject_outline` (`color`,`width`,`glow`), `rgb_split` (`amount`), `flash_frame`.

**transitions** (`kind` en {cut, flash, light_leak, whip, zoom_blur, glitch, film_burn,
torn_paper, circle, slide}; `dur`, `dir` (left|right|up|down), `color`).

**sfx** (`name` del catalogo, `gain_db`, `pan` -1..1). Catalogo (sintetizado en `assets/motion/sfx/`):
whoosh_soft, whoosh_fast, swoosh_up, swoosh_down, whip, pop, pop_soft, bubble, click, tick,
typing, ding, bell, chime, sparkle, cash, coin, riser_short, riser_long, impact_soft,
impact_boom, sub_drop, glitch, shutter, notification, success, swipe, scratch, paper_rip,
pen_scribble, heartbeat, record_stop.

**music**: `source` en {ai, library, none}; `stops` [{`at`,`dur`}] (silencio dramatico),
`swells` [{`start`,`end`,`db`}] (subir en montajes/cutaways), `drop_at` (tiempo del golpe).

**grade.look**: natural, warm_clean, cinematic, bright_pop, moody, bw_contrast.

## 4. Contrato de render (normativo)

### 4.1 FrameCtx (`motion/ctx.py`)
Campos: `W, H, fps, n, t` (final), `t_orig`, `quality` ("final"|"draft"), `seed`, `style` (dict),
`shot` (dict|None), `face` ((cx,cy,w,h) normalizado en el cuadro de salida o None), `palette`.
Metodos: `px(v)`, `rng(key)`, `matte()` (np.float32 HxW en espacio de salida o None; perezoso),
`subject_image()` (skia.Image RGBA del sujeto recortado o None), `font(name)`, `icon(name)`.

### 4.2 Firmas de renderers
- Overlay (text, graphics, cutaways, captions): `draw(canvas, item, lt, dur, ctx) -> None`
  - `canvas`: skia.Canvas de W x H ya con el cuadro base dibujado.
  - `lt`: tiempo local desde el inicio del item (s, tiempo final); `dur`: duracion final.
- Base (camera, grade, effects de video): `apply(img, item, lt, dur, ctx) -> skia.Image`.
- Transicion: `apply(img, prev, item, p, ctx) -> skia.Image`, `p` en [0,1] a lo largo de la
  ventana; `prev` = ultimo cuadro COMPUESTO antes de la union (congelado); `img` = cuadro actual.
- Registro: cada modulo expone `RENDERERS: dict[str, callable]` por kind/preset.

### 4.2b Contratos especificos (normativos)
- **camera** (`camera.py`): los efectos de encuadre NO devuelven imagen; devuelven transformacion,
  para que render aplique la MISMA matriz al video y a la mascara del sujeto:
  `camera.MATRIX_EFFECTS[kind](item, lt, dur, ctx) -> (skia.Matrix, (mx, my))` para
  kind en {zoom, punch, shake, kenburns}; `(mx,my)` = vector de movimiento en px por cuadro
  (render agrega motion blur si |v| grande). Efectos de post: `camera.POST_EFFECTS[kind]`
  = `apply(img, item, lt, dur, ctx) -> skia.Image` para {rgb_split, flash_frame}.
  `camera.face_track(base_video, W, H, fps, n_frames) -> np.ndarray (n,4)` (cx,cy,w,h norm; NaN sin cara).
- **grade** (`grade.py`): `grade.Grader(base_video, shots_final, W, H, fps)` con `.prepare()` y
  `.apply(img, cfg, shot_idx, ctx) -> skia.Image` (igualacion + look + vinieta + grano + nitidez);
  `grade.RENDERERS = {"bw": apply}`.
- **matte** (`matte.py`): `matte.MatteProvider(base_video, W, H, fps, frame_ranges, cache_dir, quality)`
  con `.prepare(on_pct=None)` y `.get(n) -> np.float32 HxW` (espacio del video base, sin camara);
  render le aplica la matriz de camara y lo expone en `ctx.matte()` / `ctx.subject_image()`.
  `matte.RENDERERS = {color_pop, bg_blur, bg_replace, subject_outline}` (apply de base).
  Modelo: `DATA/models/modnet.onnx` (Apache-2.0, Xenova/modnet; `matte.ensure_model()` lo baja).
- **cutaways** (`cutaways.py`): ademas de `RENDERERS`, expone
  `draw_background(canvas, kind, colors, lt, ctx)` (lo reutilizan bg_replace y title cards).
  `blur_self` y `photo` usan `ctx.base_image()`.
- **transitions** (`transitions.py`): `WINDOWS[kind] = frac_antes` (0.5 = centrada en la union,
  0.0 = empieza en la union). Las que mezclan A y B (slide, circle, torn_paper, film_burn)
  usan 0.0: `prev` = A congelado, `img` = B en vivo. Las demas (flash, light_leak, whip,
  zoom_blur, glitch) son centradas y solo procesan el cuadro actual.
- **text** (`text.py`): `text.RENDERERS[preset]` y `text.draw_captions(canvas, phrases_final, cfg, t, ctx)`.
  render agrega a cada item de texto `_words` = palabras habladas (tiempo final, relativas al
  inicio del item) para revelar palabra por palabra en sincronia; `cfg["_hide"]` = rangos finales
  donde no se dibujan subtitulos.
- **audio** (`audio.py`): `audio.mix(voice_wav, duration, sfx_events, music_cfg, out_wav) -> dict`
  (eventos en tiempo final). `sfx.py` genera `assets/motion/sfx/*.flac` + `catalog.json`
  (`{name: {file, dur, hit}}`, `hit` = segundos hasta el golpe; se alinea `at` con `hit`).
- **music** (`music.py`): `music.get_music(cfg, duration, style, seed, on_pct) -> {wav, bpm, beats}`.

### 4.3 Orden por cuadro
1. base decodificado (RGBA) -> `camera` -> `grade` (incluye vinieta/grano) -> `effects` de video
2. dibujar base en canvas
3. items `behind:true` y luego el sujeto recortado encima (texto detras del sujeto)
4. `cutaways` activos (cubren todo)
5. `graphics` (por `z`, luego orden), `text` (no behind), `captions`
6. `transitions` activas (sobre el cuadro compuesto, con `prev` congelado)
7. logo / progress
- Un item cuya ventana no intersecta el cuadro no se llama.
- Presupuesto: final 1080x1920 <= 60 ms/cuadro promedio por proceso (M2 Max); draft <= 15 ms.

### 4.4 Determinismo
Aleatoriedad solo via `ctx.rng(item_id)` (semilla = plan.director.seed + hash(item_id)).

## 5. Director (`motion/director.py`)
- Entradas: plan v1 (+ shots), estilo, intensidad, seed.
- Paso 1, tomas: VLM (`qwen3.5:4b`, imagen del cuadro medio de cada toma) -> campos de `shots`.
- Paso 2, guion: LLM (json, think:false, num_ctx 8192) -> estructura (hook, problema, beneficio,
  oferta, ubicacion, cta), `entities`, y por frase: enfasis, tratamiento visual sugerido,
  palabras para iconos (tags en ingles para Tabler), tono.
- Paso 3, coreografo (reglas deterministas): convierte intenciones en items con tiempos,
  garantiza ritmo (evento visual cada 2-4 s segun intensidad), variedad (no repetir el mismo
  efecto seguido), transicion en cada cambio de toma, SFX por evento visual, hook visual en los
  primeros 2 s, CTA al final, no encimar titulos con subtitulos, respetar SAFE y la cara.
- Sin LLM/VLM: heuristicas equivalentes (nunca falla el analisis).
- `redirect(plan, only_ids=None)`: re-dirige respetando `locked` y `src:"user"`.
- Estilos: `dinamico` (default, referencia), `elegante`, `minimal`, `energetico`.

## 6. Studio (UI)
- Panel Director: estilo, intensidad, "Re-dirigir", musica (IA/biblioteca/ninguna + prompt).
- Pistas nuevas: Texto, Graficos, Escenas, Efectos, Transiciones, SFX, Musica.
- Inspector guiado por `/fx/schema`.
- Vista previa: canvas en vivo (aproximado) + cuadro exacto del servidor en pausa (`/frame`) +
  "Vista previa real" (draft 540p con audio, `/preview` modo pro).

## 7. Calidad y pruebas
- `pytest spike/tests` en verde. Cada modulo trae tests de su logica y una demo visual.
- E2E: `spike/tests/e2e_pro.sh` corre compose -> analyze -> direct -> render con los clips de
  prueba y genera hoja de contacto + metricas (duracion, LUFS, tiempo de render, % cuadros con
  evento visual, texto dentro de SAFE).
- Barra de calidad: comparacion visual contra `ejemplo_objetivo.mp4` (revision humana de Kevin).
