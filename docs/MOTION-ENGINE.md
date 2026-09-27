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
render_pro(plan, out_path, opts) -> stats:
  0. prepare      : schema.normalize + probe + opts + Timeline cuantizado + job (items en tiempo final)
     musica       : music.prefetch(cfg remapeada) arranca YA en un hilo (ACE-Step en GPU), en
                    paralelo con todo lo que sigue; el paso de audio la toma de la cache
  1. timeline     : keeps (cuts habilitados) cuantizados a cuadro, remap orig->final, uniones
  2. base pass    : ffmpeg fuente -> reencuadre + cortes -> base_<clave>.mp4 (tamano del FORMATO,
                    fps, GOP corto) + voice_<clave>.wav (cadena de estudio + cortes, 48 kHz estereo)
  3. analisis     : EN PARALELO: matte (hilo principal) || rostro (camera.face_track) + grade
                    (Grader.prepare) en un hilo; luego anclas por item (_anchor, _face0). Sin
                    matte, la barra avanza con face_track (0-60 %) y Grader.prepare (60-90 %)
  4. audio        : EN PARALELO con el compositor: music.get_music (acierto de cache) +
                    audio.mix(voz, SFX, musica) -> mix.wav (-14 LUFS, TP -1.5). No emite pasos
                    (on_step) para no pisar la etiqueta del compositor. En DRAFT, si la musica IA
                    aun se genera (music.pending), mezcla con la procedural y marca
                    stats.music_pending (opts.draft_wait_music = True espera)
  5. compositor   : N procesos spawn, cada uno rangos contiguos de cuadros: base -> capas ->
                    chunk H.264; fronteras de chunk fuera de las ventanas de transicion
  6. concat + mux : espera el audio (si tarda: paso "Generando la musica..." y la espera avanza
                    en el tramo del mux), concat -c copy, mux AAC 192k +faststart. Cancelar
                    nunca espera a la musica: el candado de music.py sondea la cancelacion y el
                    hilo de audio (daemon) limpia su carpeta al terminar
  7. shorts       : con opts.shorts, highlights remapeados (render_shorts)
```

### 1.1 API de `motion/render.py` (firmas reales)

| Funcion | Uso |
|---|---|
| `render_pro(plan, out_path, opts=None) -> stats` | export completo a `out_path` (mp4). No deja `.part` en la carpeta de salida (temporal unico + `os.replace`). |
| `render_draft(plan, out_path, opts=None) -> stats` | vista previa real 540x960 con audio; REUTILIZA el base del formato (no repite el base pass). |
| `render_shorts(plan, final_mp4, opts=None, timeline=None) -> [{path, start, end, title, index}]` | corta los highlights (tiempo original) del video final: remapeados, a la reticula de cuadros, re-codificados con micro fundidos de 15 ms. Omite los de menos de 1 s. |
| `render_frame(plan, t_final, opts=None) -> np.ndarray` | un cuadro exacto RGBA (HxWx4) en tiempo FINAL: `n = floor(t*fps)` (el cuadro que muestra un reproductor en pausa). Con el base en cache usa rostro/matte/grade de la cache (o solo de los cuadros necesarios); sin base decodifica la fuente con el mismo reencuadre. |
| `ensure_base(plan, opts=None, prep=None, prog=None) -> dict` | base + voz en cache (lock por clave: seguro en segundo plano mientras corre un export). |
| `ensure_analysis(plan, opts=None, prep=None) -> dict` | base + rostro + mattes + grade en cache y precalentamiento de la musica (`opts.prefetch_music`, default True). Pensado para el Studio al abrir un proyecto. |
| `prepare(plan, opts=None) -> {plan, opts, timeline, mods, job, skipped, source}` | normaliza y arma el job sin tocar video (lo usan las demas). |
| `save_png(rgba, path)`, `output_size(fmt, src_info)`, `layout_box(W, H)`, `stage_plan(pesos)` | utilidades. |
| `music_cfg(plan, job, tl)`, `prefetch_music(...)`, `sfx_events(...)` | plomeria de audio (misma cfg para prefetch y mezcla). |
| `transition_frac(item, mods)`, `transition_dur(item, mods)` | ventana de transicion (ver 4.3). |

`opts`: `fmt` (`9:16`\|`1:1`\|`4:5`\|`16:9`\|`original`; otro = ValueError), `W`/`H` explicitos, `fps`
(30), `quality` (`final`\|`draft`), `workers` (default `min(4, cpu//3)`; 0/1 = en proceso),
`cache_dir` (default `WORK/motion_cache`), `music_cache_dir` (default el de music.py), `stems_dir`
(voz/musica/sfx por separado, debug), `enhance_audio` (True), `air`, `logo` ({path,size,opacity,pos}),
`progress` (barra automatica), `dither` (1.0; 0 = sin grano final), `min_fragment` (s),
`shorts`, `shorts_dir`, `modules` ({nombre: modulo}; falsy = desactivado), `sys_path`,
`on_pct(stage, pct_total)`, `on_step(msg)`, `cancel() -> bool`, `strict`, `keep_chunks`,
`cache_keep`, `threads` (hilos de cv2/OMP por worker; default cpu//workers), `draft_wait_music`
(False: en draft no se espera a la musica IA en generacion). `on_pct` es global 0..100, monotono y seguro entre hilos; una excepcion desde
`on_pct` o `cancel()` True mata ffmpeg y los workers (V.Canceled).

`stats` (claves principales): `out, W, H, fps, n_frames, fmt, layout, duration, source_duration,
removed, keeps, quality, items, modules, skipped, warnings, music_prefetch, t_base, t_voice,
cached_base, cached_voice, base, voice, reframe, t_face, t_matte, t_grade_prep, t_analysis,
matte{ranges[{start,end,quality}], warning, error, step}, music{wav, source, engine, bpm, drop,
keyscale, cached, gen_s, lufs, ending, warnings, pending_source}, music_pending (draft mezclado con
la procedural mientras la IA se genera: refrescar la vista previa al terminar el prefetch),
audio{...resultado de audio.mix}, sfx_events,
t_audio, t_audio_wait, t_compose, workers, chunks, threads_per_worker, ms_per_frame_{wall,proc,
compose,io}, profile_ms_per_frame{etapa: ms}, errors{renderer: n}, t_mux, shorts, t_total`.
`warnings` que la UI debe mostrar: `transitions:<id>:sin_union` (mezcla sin union real cerca),
`matte:<a>-<b>:calidad_baja` (quality < 0.85: la persona tocaba muebles; evitar contorno o
reemplazo de fondo ahi), `matte:sin_compuerta_de_persona`.

Paquete: `spike/scripts/motion/`

| Archivo | Responsable de |
|---|---|
| `ctx.py` | FrameCtx, superficies skia sobre numpy, conversion, helpers de dibujo comunes |
| `ease.py` | curvas de easing, resortes, helpers de animacion (in/out, stagger) |
| `assets.py` | registro de fuentes, iconos Tabler (paths skia), paletas, catalogo SFX/musica, rutas |
| `schema.py` | plan v2: defaults, normalize/validate, migracion v1->v2, catalogo de kinds con esquemas de parametros (lo consume el inspector via `/fx/schema`) |
| `timeline.py` | keeps, remap, joins, mapeo de items orig->final, rangos, cuantizacion |
| `video_io.py` | probe (rotacion/HDR/rango), base pass, reencuadre por formato, decode/encode, encoders por plataforma, concat, mux, locks y poda de cache |
| `render.py` | orquestacion, compositor por cuadro, workers, cuadro exacto, draft, shorts |
| `camera.py` | zoom, punch, shake, kenburns (matrices), motion blur, rgb_split/flash_frame, face_track |
| `grade.py` | igualacion de tomas, looks (LUT), detalle de luma, grano, vinieta, blanco y negro |
| `matte.py` | MODNet + compuerta de persona, cache incremental, efectos de sujeto |
| `text.py` | subtitulos (presets) y titulos cineticos |
| `graphics.py` | garabatos, iconos, tarjetas, lower third, CTA, barra de progreso, stickers |
| `cutaways.py` | escenas a pantalla completa y fondos animados |
| `transitions.py` | transiciones en uniones |
| `sfx.py` | sintesis del catalogo de SFX |
| `audio.py` | mezclador: voz, musica, SFX, ducking, stops, loudness |
| `music.py` | musica: ACE-Step (acestep.cpp) + procedural + biblioteca + archivo; ajuste a duracion |
| `director.py` | IA director: tomas (VLM), guion (LLM), coreografo (reglas), estilos, validacion |

Tests: `spike/tests/` (pytest). Demos visuales: `spike/tests/demos/` (escriben en `spike/work/demos/`,
ignorado por git).

## 2. Unidades, formatos y coordenadas

- Formatos: `9:16` 1080x1920 (referencia de diseno), `4:5` 1080x1350, `1:1` 1080x1080, `16:9`
  1920x1080 y `original` (dims de DISPLAY de la fuente con el lado corto a 1080 y el largo como
  maximo 3840). Draft = la mitad (540x960 en 9:16). Formato desconocido = ValueError.
- Reencuadre por formato (video_io.reframe_mode): `cover` (recorte que llena), `vfollow` (fuente
  vertical a 1:1/4:5: ancho completo y recorte vertical FIJO por keep que sigue la cara) y
  `pillar` (fuente vertical a 16:9: la toma completa en un inset sobre un fondo desenfocado de si
  misma; el matte se calcula solo en el frente, `job.matte_box`).
- CAJA DE LAYOUT (`render.layout_box(W, H)`): los overlays (texto, graficos, subtitulos,
  escenas) se disenan para 9:16. En 9:16 la caja es el cuadro; en 4:5, 1:1 y 16:9 es la franja
  central 9:16 (ancho H*9/16). Los renderers de overlays reciben un FrameCtx del tamano de la
  caja (face, matte, sujeto y base_image recortados a ella); las escenas fuera de 9:16 se
  componen en la caja y el resto se llena con la misma escena desenfocada.
- Posiciones normalizadas: `x`, `y` en [0,1] relativas a la caja. `(0,0)` arriba-izquierda.
- Tamanos: `size` = fraccion del ancho de la caja (tipografia: px = `size * W`).
- `ctx.px(v)`: px de referencia (ancho 1080) -> px reales (`v * W/1080`). Grosores, sombras y
  radios fijos se expresan en px de referencia.
- Zonas seguras (TikTok/Reels): `SAFE = {top:0.09, bottom:0.80, left:0.06, right:0.88}`.
  Subtitulos por defecto en y=0.72. Ningun texto importante fuera de SAFE.
- Tiempo del plan: SIEMPRE tiempo ORIGINAL de la fuente (s). El render convierte a tiempo FINAL
  con el Timeline; los renderers reciben tiempo local del item en tiempo final.
- CUANTIZACION del timeline (render usa `Timeline.from_plan(plan, fps=30)`): cada keep se
  EXPANDE a la reticula k/fps (floor al inicio, ceil al fin: nunca corta una cola de palabra);
  los keeps de menos de `MIN_FRAG` = 0.15 s se absorben en el corte; un limite a <= max(0.12 s,
  2 cuadros) de un cambio de toma se pega a ese cambio. Duracion final = n_frames/fps. Un item
  que empieza justo en el inicio de un keep puede arrancar 1 cuadro despues de la union.
  (El director usa el Timeline sin fps: la diferencia es menor a 1 cuadro.)
- fps de salida: 30 (default) o 60 (`opts.fps`). Salida H.264 High, bt709 completo (primaries,
  transfer, matriz, rango tv), ~0.22 bpp en final (13.7 Mbps a 1080p30), AAC 192k.

## 3. Plan v2 (claves nuevas; las de v1 se conservan)

```json
{
  "version": 2,
  "shots": [
    {"start": 0.0, "end": 5.4, "src": "toma_a.mov", "person": true, "framing": "medio",
     "place": "panaderia", "text_seen": [], "use": "a_camara", "free_space": "arriba"}
  ],
  "entities": {
    "name": "Panaderia La Espiga", "role": "Panadero", "price": "$45 la pieza",
    "address": "Calle Olmo 8", "landmark": "frente al mercado",
    "cta": "Pide tu pan de caja hoy", "link_label": "Haz tu pedido", "handle": "@la_espiga"
  },
  "director": {"style": "dinamico", "intensity": 0.75, "seed": 7, "locked": [], "llm": "qwen3.5:4b"},
  "fx": {
    "captions": {"enabled": true, "preset": "clean_bold", "y": 0.72, "size": 0.052,
                 "font": "Poppins-ExtraBold", "color": "#FFFFFF", "accent": "#FFD23F",
                 "case": "none", "max_words": 4, "hide_during_titles": true},
    "text": [], "graphics": [], "cutaways": [], "effects": [], "transitions": [], "sfx": [],
    "music":  {"enabled": true, "source": "ai", "track": null, "file": null, "prompt": "",
               "bpm": 0, "gain_db": -17, "duck_db": -9, "intro_fade": 0.0, "fade_out": 1.5,
               "stops": [], "swells": []},
    "grade":  {"enabled": true, "look": "warm_clean", "match_shots": true,
               "vignette": 0.15, "grain": 0.025, "sharpen": 0.15}
  }
}
```
(Datos de ejemplo ficticios.)

Campos comunes de todo item de lista (`text`, `graphics`, `cutaways`, `effects`): `id` (str
unico), `start`, `end` (tiempo original), `enabled`, `src` ("ai"|"user"), `locked` (el director
no lo toca al re-dirigir), `intensity` (0..1, default 1), `z` (int opcional, orden).
`transitions` y `sfx` usan `at` (tiempo original del evento) en vez de start/end.

### 3.1 Catalogo de kinds (normativo; fuente de verdad: `schema.KINDS` / `GET /fx/schema`)

El catalogo publica por pista y kind TODOS los parametros que implementa el modulo dueno, con
etiqueta en espanol, rango, paso, unidad y default (adoptado de los modulos: graphics.DEFAULTS,
cutaways.DEFAULTS/SCENE_IN/SCENE_OUT/BG_COLOR_ROLES, text.PRESETS, transitions.PARAMS,
music.CFG_SCHEMA). Tipos: `num` (con `len: 2` = punto [x, y]), `text` (`list: true` = lista),
`color` (hex o nombre: white, warm, cool, gold, rose, accent, accent2, primary, dark, light,
brand), `select`/`seg` (`opts`), `bool`, `font`, `icon`, `lines` (lineas de texto con estilo) y
`rows` (lista de objetos con `fields`, p. ej. music.stops). `nullable` con default null =
"automatico" (lo decide el renderer). normalize() solo completa comunes y `req`; con
`fill=True` completa todo.

**text** (`preset` en title_pop, stack, cascade, serif_elegant, script_accent, behind_giant,
highlight_marker, typewriter, counter, kicker): `text` o `lines` [{text, scale, font, color,
italic}], `x`, `y`, `size`, `font`, `color`, `accent`, `accent_words` [str], `align`, `stroke`,
`shadow`, `anim_in` (pop|rise|slide_left|slide_right|type|mask|fade|scale_down|word_stagger|
letter_stagger), `anim_out` (fade|pop_out|slide_up|none), `behind`, `box`. counter: `from`, `to`,
`prefix`, `suffix`, `decimals`, `sep` (","), `anchor` (right|center|left: de donde crece).
render agrega `_words` (palabras habladas del rango, tiempo final relativo al item).

**captions**: `preset` (clean_bold|karaoke|boxed|minimal_small|bold_caps|none), `y`, `x`, `size`,
`font`, `color`, `accent`, `case`, `max_words`, `hide_during_titles`, y finos (null = el estilo):
`max_lines` (1..4), `kw_accent`, `recenter`, `stroke`, `shadow`, `box`, `highlight`.

**graphics** (todos: `x`, `y` (nullable: vacios + `anchor` = junto a la cara), `size`, `color`,
`anim_in`, `anim_out`, `rotation`, `in_dur`, `out_dur`):
- `doodle`: `name` (20 figuras), `stroke`, `draw`, `wiggle`, `path_to` [x,y], `style`
  (brush|sketch), `anchor` (face|face_left|face_right|face_top), `bend` (-1..1, flechas),
  `glow`, `shadow`, `draw_time`.
- `icon`: `name` (Tabler), `variant` (outline|filled), `badge` (none|circle|squircle),
  `badge_color`, `draw`, `style` (clean|sketch), `anchor`, `shadow`.
- `card`: `variant` (price|location|schedule|link|phone|rating|checklist|stat|generic), `title`,
  `subtitle`, `value`, `items`, `icon`, `theme` (light|dark|brand), `accent`, `label` (boton).
- `lower_third`: `title`, `subtitle`, `theme` (dark|light), `accent`.
- `cta`: `variant` (follow|link_below|comment|save|share), `label`, `handle`, `subtitle`,
  `button`, `button_done`, `button_color`, `avatar` (initial|face).
- `progress`: `color`, `anim_in` (fade|none), `anim_out` (none|fade), `thickness`, `bar_y`
  (0..1, borde superior), `margin` (px). NO usa x/y/size.
- `sticker_text`: `text`, `bg`, `fg`, `font`, `border` (default true: sin el campo se dibuja).

**cutaways** (todas: `background` (grid_warp|gradient|paper|dark_stars|rays|blur_self|color|
halftone), `bg_colors` [hex] con `roles` por fondo (indice 0 = dominante, 1 = acento, 2 = tinta
opcional), `text.text`, `text.preset`, `anim_in`/`anim_out` (cut|fade|zoom|slide_*|circle|wipe),
`in_dur`, `out_dur`):
- `title_card`; `info_card` (tarjeta propia opcional: `variant`, `title`, `subtitle`, `value`,
  `items`; si no, la tarjeta va en `graphics` embebidos); `photo` (`caption`, `tilt`, `size`
  0.3..0.8);
  `self_broll` (`src_t` tiempo orig del material, `zoom_from`, `zoom_to` 1..4, `pan` [dx, dy]).

**effects**:
- camara (matrices): `zoom` (`from`, `to` 1..4, `easing`, `target` [x,y], `center` [x,y]),
  `punch` (`amount`, `hold` s o null = hasta el final, `mode` spring|cut, `release` smooth|cut,
  `attack`, `freq`, `damping`, `target`, `center`), `shake` (`amp` px, `freq`, `decay` 0..1,
  `rot` grados, `attack`), `kenburns` (`from`, `to`, `pan` [dx,dy], `easing`, `center`).
- color: `bw` (`contrast` 0.5..2, `subject_color`, `tone` neutral|warm|cool|sepia, `vignette`,
  `lift`, `fade` s).
- sujeto (piden matte): `color_pop` (`bg_desat`, `bg_dark`), `bg_blur` (`radius` px),
  `bg_replace` (`background`, `bg_colors`, `grain`, `vignette`), todas con `fade_in`/`fade_out`;
  `subject_outline` (`color`, `width` px, `glow`).
- post: `rgb_split` (`amount` px, `angle`, `radial`, `jitter`, `attack`, `hold` bool),
  `flash_frame` (`color`, `peak`, `bloom`, `attack`).

**transitions** (`kind` en cut, flash, light_leak, whip, zoom_blur, glitch, film_burn,
torn_paper, circle, slide; comunes `dur` (default y rango POR kind), `intensity` (0 = corte)):
- centradas (el efecto se centra en la union; `align` auto|center|start|end): `flash` (`color`
  white|warm|cool|hex), `light_leak` (`color` warm|gold|rose|cool|hex, `peak` whiteout|partial,
  `gate` bool), `whip` (`dir`), `zoom_blur` (`x`, `y`; null = la cara), `glitch` (`color`).
- mezclas A->B (la ventana SIEMPRE empieza en la union; sin `align`): `film_burn` (`color`, `x`,
  `y` = punto protegido), `torn_paper` (`dir`, `mode` split|sweep, `color` papel, `x`, `y`),
  `circle` (`x`, `y`, `ring`, `color`), `slide` (`dir`).

**sfx** (`name` del catalogo: 32 del contrato + generados). normalize resuelve alias en
espanol/ingles, prefijos, tags y sufijo `_N` (`schema.resolve_sfx_name`: 'riser_short_2' ->
riser_short con `variant` 1 si no venia; 'riser' -> riser_*); un nombre que no resuelve se
descarta en normalize y `render` lo reporta en `skipped` como `sfx:<id>:nombre_desconocido`:
`gain_db` (0 = pico al nivel de la voz), `pan`, `variant` (0-based; null = automatica
determinista), `pitch` (semitonos; null = afinado a la tonalidad de la musica), `dur` (s; risers:
anticipacion antes de `at`; continuos: el sonido va de `at` a `at+dur`, recortado o en loop).

**music**: `source` (ai|procedural|library|file|none), `track`, `file` (solo con source file),
`prompt`, `mood`, `bpm` (0 = automatico; con `bpm_lock` se fuerza cualquier valor, 110 sin lock
tambien es automatico), `keyscale`, `seed` (otra variacion), `ending` (auto|natural|hit|fade),
`drop_at` (s, golpe principal), `gain_db` (cama bajo la voz), `duck_db` (diferencia total
cama/huecos), `voice_carve_db` (parte del duck solo en 250 Hz-5 kHz), `intro_fade` ("Entrada
suave", s, default 0 = entra con su arranque horneado; > 0 se respeta siempre), `fade_out` ("Cola
final" 0.6..3.0 s: donde music.py pone el golpe/desvanecido antes del ultimo cuadro; el mezclador
solo agrega 30 ms de seguridad), `stops` [{`at`, `dur`, `mode` tape|cut, `fade`, `fade_back`}]
(silencio dramatico; tape = la propia cancion se frena en [at-fade, at]; render remapea `at` y
`dur` a tiempo final: si cruza un corte dura lo que queda en el video), `swells` [{`start`, `end`,
`db`, `ramp`}]. `fade_in` (legacy, planes viejos traen 0.2) ya no se expone: con el arranque
horneado por music.py no suaviza nada (el primer golpe no se apaga).

**grade**: `look` (natural, warm_clean, cinematic, bright_pop, moody, bw_contrast),
`match_shots`, `strength` (0..1.5), `exposure` (EV), `temperature`, `tint`, `saturation`,
`contrast`, `clarity` (null = la del look), `vignette` (se suma a la del look), `grain`,
`sharpen`.

## 4. Contrato de render (normativo)

### 4.1 FrameCtx (`motion/ctx.py`)
Campos: `W, H, fps, n, t` (final), `t_orig`, `quality` ("final"|"draft"), `seed`, `style`,
`shot` (dict|None), `face` ((cx,cy,w,h) normalizado o None), `palette`, `words`, `entities`.
Metodos: `px(v)`, `rng(key)`, `matte()` (np.float32 HxW en espacio de salida, perezoso),
`subject_image()` (skia.Image RGBA del sujeto recortado), `base_image()` (la ENTRADA del efecto
actual en efectos de base/post; el cuadro base final en overlays), `font(name)`, `icon(name)`.
Durante MATRIX_EFFECTS `ctx.face` esta en espacio del BASE; en el resto, en espacio de salida.

### 4.2 Firmas de renderers
- Overlay (text, graphics, cutaways, captions): `draw(canvas, item, lt, dur, ctx) -> None`
  (`canvas` en la caja de layout, ya con el cuadro base; `lt` = tiempo local final).
- Base (grade, efectos de video): `apply(img, item, lt, dur, ctx) -> skia.Image`.
- Transicion: `apply(img, prev, item, p, ctx) -> skia.Image`, `p` en [0,1].
- Registro: cada modulo expone `RENDERERS: dict[str, callable]`.

### 4.2b Contratos especificos
- **camera**: `MATRIX_EFFECTS[kind](item, lt, dur, ctx) -> (skia.Matrix, Motion)` para zoom,
  punch, shake, kenburns (render aplica la MISMA matriz al video y al matte; `camera.combine`,
  `apply_camera` = warp + motion blur del base). Ancla por prioridad: `item.center` ->
  `item._anchor` (fija por item: render la calcula con `camera.item_anchor` = mediana de la cara
  en el rango) -> `ctx.face`. `POST_EFFECTS` = {rgb_split, flash_frame}.
  `face_track(base_video, W, H, fps, n_frames, cache_dir=None, cuts=None, on_pct=None,
  cancel=None)` (NaN sin cara; on_pct cada 16 cuadros, cancel() -> InterruptedError).
- **grade**: `Grader(base_video, shots_final, W, H, fps, cache_dir=None)` con `.prepare()` (cache
  JSON) y `.apply(img, cfg, shot_idx, ctx)`. Pipeline por cuadro: detalle de LUMA en un canal
  (nitidez 3x3 + claridad a 1/4 + grano, `detail_luma`) -> LUT por plano (igualacion + look) ->
  vibrance (matriz 3x3 que protege la piel) -> vinieta. ~8 ms/cuadro 1080x1920 a 1 hilo.
  `RENDERERS = {"bw": ...}`.
- **matte**: `MatteProvider(base_video, W, H, fps, frame_ranges, cache_dir, quality, workers=None,
  step=None)`; `.prepare(on_pct)` INCREMENTAL: solo se infieren y escriben los huecos sin mascara
  (`missing_runs()`, con contexto a cada lado), pero las caras (cacheadas por cuadro en
  `faces.json`), el gating (>= 25 % de claves con cara) y el recorte/dims de inferencia se
  deciden sobre el RANGO PEDIDO completo: calcular por huecos da lo mismo que de una vez (un
  hueco sin caras dentro de un rango con caras se infiere con la cara mas cercana). Una
  excepcion de on_pct cancela sin escribir mascaras vacias. `.get(n)` (ceros fuera de rango);
  `.quality_of(a, b)`, `.warning`, `.error`, `.stats` (por hueco: quality, engine, dims, crop,
  range). `meta.json`/`faces.json` se escriben con temporal por proceso e hilo (dos prepare
  sobre la misma cache no se pisan). MODNet + compuerta de persona (PP-HumanSeg) en ONNX
  Runtime: en macOS por CoreML (Neural Engine, una sesion por forma de entrada, compilada en
  disco en `REELFY_MATTE_COREML_DIR` o DATA/models/matte/coreml; LRU de 8 formas que nunca poda
  una usada hace < 5 min; un fallo al crear la sesion se reintenta tras 60 s, hasta 3 veces), si
  no CPU; UNA sesion por proceso y modelo, executors persistentes.
  `RENDERERS = {color_pop, bg_blur, bg_replace, subject_outline}`.
- **cutaways**: `draw_background(canvas, kind, colors, lt, ctx)`, `BG_COLOR_ROLES`,
  `frame_request(item) -> (clave, ref, t_orig) | None`: render inyecta `item['_still']` (photo:
  cuadro base con grade al inicio) o `item['_src_image']` (self_broll: el cuadro de `src_t`).
- **transitions**: `WINDOWS`, `MIXERS`, `DEFAULTS`, `PARAMS` (formato del catalogo), `SFX`,
  `effective_frac(item)`, `window`, `progress`, `hit_time`.
- **text**: `RENDERERS[preset]`, `draw_captions(canvas, phrases_final, cfg, t, ctx)`,
  `is_behind(item)`; `cfg['_hide']` = rangos finales sin subtitulos.
- **audio**: `mix(voice_wav, duration, sfx_events, music_cfg, out_wav, *, seed, target_lufs,
  stems_dir, ...) -> dict` (todo en tiempo FINAL). Eventos `{id, name, at, gain_db, pan,
  variant, pitch, dur}`. Si `music_cfg.ending` es natural|hit|fade (lo copia render de
  get_music) solo aplica fundidos de seguridad (6/30 ms): el final horneado por music.py no se
  vuelve a fundir; `intro_fade` > 0 si se aplica. La mezcla completa termina con un fundido de
  seguridad de 12 ms (y 2 ms al inicio): la ultima muestra es ~0 aunque la voz termine a media
  onda. La voz del base (video_io.cut_audio) ya se apaga en 25 ms si el ultimo keep corta la
  fuente (VOICE_VERSION v4).
- **music**: `get_music(cfg, duration, style, seed, on_pct, cache_dir) -> {wav, bpm, beats,
  downbeats, sections, drop, hook_end, ending{mode,hit}, keyscale, lufs, src_offset, ...}` y
  `prefetch(cfg, duration, style, seed, cache_dir) -> Thread` y `pending(cfg, duration, style,
  seed, cache_dir) -> bool` (True si get_music tendria que generar con ACE: sin crudo en cache).
  La espera del candado de generacion sondea (LOCK_NB cada 0.1 s) y consulta on_pct: un
  get_music cancelado suelta al instante aunque otro este generando. render copia a la cfg del
  mezclador `wav, bpm, beats, downbeats, sections, drop, hook_end, ending, keyscale, lufs` y NO
  `src_offset` (en audio.py `offset` es buscar dentro del archivo; el wav ya viene ajustado).
  `stops` (`at` y `dur`), `swells` y `drop_at` llegan remapeados a tiempo FINAL.
- **sfx**: `assets/motion/sfx/*.flac` + `catalog.json` `{name: {file, dur, hit, tags, variants,
  category, desc, root?, loop?}}`; el golpe (`hit`) se alinea con `at`.

### 4.3 Orden por cuadro y campos internos
1. base decodificado (RGBA) -> `camera` (matrices + motion blur) -> `grade` -> efectos de base
   (bw, matte) -> post (rgb_split, flash_frame). `ctx.base_image()` = la entrada de cada efecto.
2. dibujar base en canvas
3. items `behind` (texto/graficos) y luego el sujeto recortado encima
4. `cutaways` activos (cubren todo; fuera de 9:16 en la caja con relleno desenfocado)
5. `graphics` (por `z`, luego orden), `text` (no behind), `captions`
6. `transitions` activas (sobre el cuadro compuesto, con `prev` congelado)
6b. grano/dither final +-2 niveles de luma sobre todo el cuadro (solo final; `opts.dither`)
7. barra de progreso y logo
- Un item cuya ventana no intersecta el cuadro no se llama. Un renderer que falla no tumba el
  cuadro (se cuenta en `stats.errors`; `opts.strict` lo propaga).
- Transiciones: `at` se remapea y se pega a la union o cambio de toma real mas cercano
  (+-0.25 s; si no hay, `warnings`). `L = round(dur*fps)` con dur robusto (invalido, 0 o
  negativo -> default del kind; tope 4 s; normalize ya lo acota al rango del kind y el corte
  seco es el kind `cut`), `F0 = J - round(L * transitions.effective_frac(item))` (mezclas: 0 ->
  empiezan en la union; centradas: 0.5 salvo `align`), `F1 = F0 + L`, `p = (n - F0 + 0.5)/L`,
  `prev` = cuadro `J-1` compuesto antes del paso 6, congelado. Los chunks nunca parten una ventana.
- Campos internos que agrega render (no se guardan en el plan): items `_fs`, `_fe` (tiempo final),
  `_ord`, `_words` (texto), `_behind`, `_stage` (matrix|base|post), `_anchor` (efectos de camara),
  `_face0` (graficos con `anchor` sin x/y: cara al inicio del item en coordenadas de la caja, para
  huecos del detector), `_frame_req`/`_still`/`_src_image`/`_frame_failed` (escenas), y en
  transiciones `_J`, `_F0`, `_F1`, `_P`, `_dur_final`, `_snapped`. En el job: `layout` (caja),
  `matte_ranges`, `matte_box`/`matte_box_base` (inset 16:9), `cut_frames`, `warnings`,
  `grade_cfg`, `grade_on`, `captions` (con `_hide`), `captions_on`, `words`, `phrases`, `shots`
  (tiempo final), `timeline`, `seed`, `style`, `entities`, `dither`.
- Workers: `workers` procesos spawn (`_SafeMain` evita re-ejecutar el script principal), cada uno
  con `cv2.setNumThreads(cpu//workers)` y los mismos limites OMP/BLAS; en proceso (workers <= 1)
  cv2 usa todos los nucleos.
- Presupuesto: final 1080x1920 <= 60 ms/cuadro promedio por proceso; draft <= 15 ms.

### 4.4 Determinismo
Aleatoriedad solo via `ctx.rng(item_id)` (semilla = plan.director.seed + hash(item_id)); la
mezcla usa `seed` del plan. El matte por CoreML (fp16) difiere del de CPU en ~2e-4 de alfa
(invisible); la cache de mascaras es la misma para ambos motores.

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

### 6.1 API del servidor para el motor pro (normativo; `spike/app/server.py`)
Todos los tiempos que manda o recibe la UI son del plan (tiempo ORIGINAL), salvo `t_final`.
El plan viaja completo en el cuerpo; si falta, el servidor usa el plan guardado del job. Los
endpoints que cambian el plan lo persisten en el job; `plan_rev` sube SOLO si el contenido del
plan cambio (exportar el mismo plan no la sube). Todo lo que toca disco, sqlite, red o el motor
es `def` (threadpool) o trabajo en segundo plano: nunca bloquea el event loop.
Licencia (solo app empaquetada) en todo lo que procesa; `engine_ready` (whisper) en
`/compose` y `/analyze`. Formatos: `9:16`, `1:1`, `4:5`, `16:9`, `original` (otro -> 400).

| Metodo y ruta | Cuerpo / params | Respuesta |
|---|---|---|
| POST `/compose` y `/analyze` | + `director: {style, intensity, seed, music, enabled?}` (opcional; default dinamico/0.75/7; en `/analyze` va como campo de formulario JSON). `music`: fuente (`ai`/`procedural`/`library`/`none`) o dict parcial de `fx.music`. `enabled:false` = sin director (plan v1). | `{job_id}`. Progreso monotono con `eta`: `stage` compose (2-32 %, avanza por clip normalizado) -> analyze (34-70 %; 2-70 % sin compose) -> director ("Dirigiendo la edicion (tomas / guion / coreografia / componiendo la musica)", 70-99 %) -> review. Si el director falla con IA cae a heuristicas y luego al plan v1 migrado; NUNCA tumba el analisis (aviso en `warnings`, tambien si el VLM cayo a heuristicas). La `music` elegida entra ANTES de sincronizar: si no es la pista IA por defecto (otra fuente, o prompt/mood/bpm/keyscale/seed/track/ending propios) el director corre con `music_sync=False` (no genera ACE ni alinea golpes a una pista que no va a sonar). compose pone en `plan.shots` los limites REALES de cada clip (`src` = nombre original). |
| POST `/direct/{job}` | `{plan, style?, intensity?, seed?, only_ids?, full?, use_llm?, music?, fmt?, background?}` | `{plan, plan_rev, warnings, ms}`. `only_ids`: `redirect` solo esos items (rapido, sin LLM). `full:true` (o plan v1): `direct` completo con LLM (`use_llm:false` = heuristicas). Si no: `redirect` del plan entero. Sin `music` se conserva la musica que el usuario eligio en el plan (fuente no IA, apagada o `locked`). `background:true` (con `full`): tarea lateral `direct` -> `{ok, state, background}`; progreso en `/status` (`direct_state/pct/stage/message`, `direct.plan_rev` al terminar; el plan llega con `?rev=N`); cancelable con `/cancel/{job}?only=direct` (el director termina el paso en curso y su resultado se DESCARTA). 409 si el job esta procesando o ya dirige. Relanza el analisis de fondo. |
| GET `/plan/{job}` | – | `{plan, plan_rev}` |
| PUT `/plan/{job}` | `{plan, base_rev?}` | `{ok, plan_rev}`: autoguardado del Studio (no relanza el analisis de fondo). Con `base_rev` (revision sobre la que edito) es concurrencia optimista: si `plan_rev` ya cambio (re-dirigir u otra ventana) -> **409** `{ok:false, plan_rev}` y NO se pisa. |
| GET `/fx/schema` | – | `schema.schema()` (catalogo de pistas/kinds/parametros para el inspector) |
| GET `/fx/fonts` | – | `{fonts:[{name, family, role, file, weight, italic, url}], default, roles, license}` |
| GET `/fx/font/{name}` | – | archivo TTF (nombre logico o de archivo; para `@font-face`) |
| GET `/fx/icons?q=&limit=&variant=` | – | `{icons:[{name, svg}]}` (Tabler MIT, SVG 24x24 `currentColor`; sin `q`: populares) |
| GET `/fx/sfx` | – | catalogo de SFX `{name: {dur, hit, tags, category, desc, variants, ...}}` |
| GET `/fx/sfx/{name}` | `?variant=` (0-based) | audio del SFX (flac) |
| POST `/frame/{job}` | `{plan, t_final, fmt, quality: "draft"\|"final", jpeg_quality?, drop_stale?}` | `image/jpeg` del cuadro exacto (`render.render_frame`, uno a la vez); cabecera `X-Frame-Ms`. 400 si el plan no es v2. Con `drop_stale:true` (arrastre) un pedido que espera turno y ya fue superado por otro del mismo job responde 204. |
| POST `/preview/{job}` | `{plan, pro: true, fmt}` | `{ok, state}`; tarea lateral `preview` (`render.render_draft` 540p con audio). No mueve la fase del job. Progreso en `/status`: `preview_state` (queued/running/done/error/canceled; `queued` ya esta escrito cuando responde), `preview_pct`, `preview_stage`, `preview_message`, `preview_done`, `preview_error`, `preview_t`, `preview_file` (servir con `/video/{file}`; `null` mientras no termina). Sin `pro`: preview legacy. |
| POST `/render/{job}` | `{plan, fmt, shorts (default true), legacy?}` | `{ok, pro, plan_rev}`; si `plan.version==2` usa `render.render_pro` con shorts (progreso por etapa en `stage` + `pct` global + ETA; cancelable); si no, pipeline legacy. 409 si ya procesa. `clips[0].stats` trae los tiempos del render, `skipped`, `music` (source/engine/bpm/cached, sin rutas) y `load`. Un export cancelado (o interrumpido por un cierre) vuelve a `phase/state: review` con `message` "Export cancelado…" (no es error). Del trabajo se conservan los 2 exports mas recientes. |
| POST `/music/{job}` | `{plan, seed?, fmt?}` | `{file, url, bpm, drop, source, engine, duration, sections, cached, warnings, ...}`: (re)genera la musica del plan con la MISMA cfg que el render (`render.music_cfg`: tras un export es acierto de cache) y deja un wav escuchable. Sincrono (10-40 s con ACE; threadpool). |
| GET `/music-file/{name}` | – | audio de la musica generada |
| POST `/analysis/{job}` | `{plan?, fmt?}` | `{ok, started}`: (re)lanza el analisis de fondo |
| POST `/cancel/{job}` | `?only=preview\|analysis\|direct\|job` | sin `only`: cancela render/analisis del job y sus tareas laterales. Con `only`: SOLO esa tarea lateral (cancelar la vista previa no mata un export en curso) o solo el trabajo principal (`job`). 400 con otro valor. |

`/status/{job}` es LIVIANO: `phase, state, kind, pct, message, eta, elapsed, stage, warnings,
plan_rev, has_plan, clips, error` + tareas laterales (`preview`, `analysis`, `direct` como dict
y aplanadas `<tarea>_state/pct/file/stage/error/done/t/message`). El plan completo va solo con
`?plan=1`, con `?rev=N` si `plan_rev > N`, o (UI legacy sin parametros) cuando cambia la fase;
`?plan=0` nunca. `stage`: compose, analyze, director, review, timeline, base, analysis,
audio, render, mux, done.

Analisis de fondo: tras `review` (y tras re-dirigir) el servidor lanza
`render.ensure_analysis(plan, fmt)` en segundo plano (tarea `analysis`, cancelable, uno a la
vez). Su clave (`key`/`bkey` en `/status`) solo cuenta lo que cambia el analisis: `bkey` = base
y voz (video, cortes, reencuadre, formato, audio); `key` = `bkey` + rostro, rangos de matte y
color. Editar textos, graficos o SFX NO lo cancela. Un pedido con la misma `key` no se relanza;
con la misma `bkey` se encadena al terminar el actual. Un export cancela los analisis de fondo
(no los pisa) salvo el del mismo job con la misma `bkey`, al que espera para reutilizar base,
rostro y matte; la vista previa real del mismo job hace lo mismo.

Setup (`GET /setup`): obligatorio = whisper + LLM `qwen3.5:4b` (app, instalacion nueva).
`llm_model` = LLM activo, `llm_target` = `qwen3.5:4b`. Al actualizar de v0.4 (hay `qwen2.5:3b`)
NO bloquea: usa el viejo (vision en heuristicas) y baja el nuevo en segundo plano en
`optional.llm` (`state` ready/missing/downloading/error/skipped/required/external, `pct`,
`msg`, `active`, `fallback`); al terminar lo activa y borra el viejo. Opcional en segundo plano:
afinador, sujeto (MODNet + PP-HumanSeg) y musica IA (ACE-Step ~3.6 GB) en `optional.{aligner,
matte, music}` con `state` (ready/missing/downloading/verifying/disabled/skipped/error), `pct`
y `msg`. `POST /setup/start {music_ai}` y `POST /setup/music {enabled}` activan o pausan la
musica IA (reactivarla enseguida relanza la descarga); sin ella el motor usa la procedural.

Disco: el servidor purga los archivos de los trabajos viejos (conserva 50), deja 2 exports por
trabajo, borra huerfanos y fuentes sin uso (> 24 h) y pone tope LRU de 5 GB a las caches
regenerables (`work/motion_cache`, `work/music`); con < 5 GB libres baja el tope y conserva
solo los 5 trabajos mas recientes.

## 7. Calidad y pruebas
- `pytest spike/tests` en verde. Cada modulo trae tests de su logica y una demo visual.
- E2E: `spike/tests/e2e_pro.sh` corre compose -> analyze -> direct -> render con los clips de
  prueba y genera hoja de contacto + metricas (duracion, LUFS, tiempo de render, % cuadros con
  evento visual, texto dentro de SAFE).
- Barra de calidad: comparacion visual contra `ejemplo_objetivo.mp4` (revision humana de Kevin).
